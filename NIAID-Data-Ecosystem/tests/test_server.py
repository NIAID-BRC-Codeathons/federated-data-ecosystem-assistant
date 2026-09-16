"""Tests for the client and tool layer, against a mock HTTP transport.

No network access: httpx.MockTransport intercepts every request, which also
lets us assert on the exact query strings the tools generate.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nde_mcp import server as S
from nde_mcp.client import NDEClient, NDEError

HIT = {
    "_id": "test_1",
    "@type": "Dataset",
    "name": "Test dataset",
    "description": "A description.",
    "url": "https://example.org/1",
    "includedInDataCatalog": {"name": "NCBI SRA"},
}


class Recorder:
    """Mock transport that records requests and replays canned responses."""

    def __init__(self, response: dict | list | None = None, status: int = 200):
        self.requests: list[httpx.Request] = []
        self.response = response if response is not None else {"total": 1, "hits": [HIT]}
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.response(request) if callable(self.response) else self.response
        return httpx.Response(self.status, json=response)

    @property
    def last_query(self) -> str:
        return self.requests[-1].url.params.get("q", "")

    @property
    def queries(self) -> list[str]:
        return [r.url.params.get("q", "") for r in self.requests]


@pytest.fixture
def patched(monkeypatch):
    """Install a mock-backed NDEClient as the server's shared client."""

    def install(recorder: Recorder) -> Recorder:
        client = NDEClient(client=httpx.AsyncClient(transport=httpx.MockTransport(recorder)))
        monkeypatch.setattr(S, "_client", client)
        return recorder

    return install


async def call(tool, **kwargs) -> dict:
    return json.loads(await tool(**kwargs))


class TestClientErrors:
    async def test_http_error_body_is_surfaced(self):
        body = {
            "code": 400,
            "success": False,
            "error": "search_phase_execution_exception",
            "root_cause_line_00": "Failed to parse query [[unclosed]",
        }
        transport = httpx.MockTransport(lambda r: httpx.Response(400, json=body))
        async with NDEClient(client=httpx.AsyncClient(transport=transport)) as client:
            with pytest.raises(NDEError) as exc:
                await client.query({"q": "[unclosed"})
        assert "Failed to parse query" in str(exc.value)
        assert exc.value.status == 400

    async def test_body_level_failure_flag_is_an_error(self):
        # The API can return success:false with a 200 status.
        body = {"success": False, "error": "Bad Request", "keyword": "size", "max": 1000}
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        async with NDEClient(client=httpx.AsyncClient(transport=transport)) as client:
            with pytest.raises(NDEError) as exc:
                await client.query({"size": 5000})
        assert "size" in str(exc.value)

    async def test_connection_failure_is_wrapped(self):
        def boom(request):
            raise httpx.ConnectError("no route to host")

        transport = httpx.MockTransport(boom)
        async with NDEClient(client=httpx.AsyncClient(transport=transport)) as client:
            with pytest.raises(NDEError) as exc:
                await client.query({"q": "x"})
        assert "Could not reach" in str(exc.value)

    async def test_booleans_render_as_lowercase_strings(self):
        rec = Recorder()
        async with NDEClient(client=httpx.AsyncClient(transport=httpx.MockTransport(rec))) as client:
            await client.query({"q": "x", "use_ai_search": True})
        assert rec.requests[-1].url.params["use_ai_search"] == "true"


class TestSearchDatasets:
    async def test_builds_expected_query(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_datasets, query="flu", repository="NCBI SRA", size=5)
        assert rec.last_query == 'flu AND @type:"Dataset" AND includedInDataCatalog.name:"NCBI SRA"'

    async def test_record_type_any_omits_type_filter(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_datasets, query="flu", record_type="any")
        assert "@type" not in rec.last_query

    async def test_comma_list_becomes_or_group(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_datasets, repository="NCBI SRA, Zenodo")
        assert (
            '(includedInDataCatalog.name:"NCBI SRA" OR includedInDataCatalog.name:"Zenodo")'
            in rec.last_query
        )

    async def test_sort_alias_translated(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_datasets, query="flu", sort="newest")
        assert rec.requests[-1].url.params["sort"] == "-date"

    async def test_response_is_shaped(self, patched):
        patched(Recorder())
        out = await call(S.nde_search_datasets, query="flu")
        assert out["total"] == 1
        assert out["results"][0]["id"] == "test_1"
        assert out["results"][0]["repository"]["name"] == "NCBI SRA"
        assert "api_call" in out

    async def test_api_error_returned_as_json_not_raised(self, patched):
        patched(Recorder({"success": False, "error": "boom"}, status=400))
        out = await call(S.nde_search_datasets, query="flu")
        assert "error" in out
        assert out["while"] == "nde_search_datasets"


class TestZeroResultDiagnostic:
    async def test_identifies_the_conflicting_filter(self, patched):
        # Full query empties out; dropping the technique filter restores hits.
        def respond(request):
            q = request.url.params.get("q", "")
            if "measurementTechnique" in q:
                return {"total": 0, "hits": []}
            return {"total": 8730, "hits": []}

        patched(Recorder(respond))
        out = await call(
            S.nde_search_datasets, query="tuberculosis", measurement_technique="RNA-seq"
        )
        assert out["total"] == 0
        culprits = {c["drop_filter"] for c in out["conflicting_filters"]}
        assert "measurement_technique" in culprits

    async def test_includes_vocabulary_hint(self, patched):
        def respond(request):
            q = request.url.params.get("q", "")
            return {"total": 0, "hits": []} if "measurementTechnique" in q else {"total": 5, "hits": []}

        patched(Recorder(respond))
        out = await call(
            S.nde_search_datasets, query="tb", measurement_technique="RNA-seq"
        )
        hint = out["conflicting_filters"][0]["hint"]
        assert "OBI" in hint or "rna-seq assay" in hint

    async def test_free_text_alone_is_not_blamed(self, patched):
        # A term that simply matches nothing should get the generic hint, not a
        # "drop your search term" diagnosis.
        patched(Recorder({"total": 0, "hits": []}))
        out = await call(S.nde_search_datasets, query="zzzznonexistent")
        assert "conflicting_filters" not in out
        assert "hint" in out

    async def test_no_extra_calls_when_results_exist(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_datasets, query="flu", repository="Zenodo")
        assert len(rec.requests) == 1


class TestSemanticSearch:
    async def test_sets_ai_flag(self, patched):
        rec = patched(Recorder())
        await call(S.nde_semantic_search, query="immune profiling in infants")
        assert rec.requests[-1].url.params["use_ai_search"] == "true"

    async def test_query_passed_verbatim(self, patched):
        rec = patched(Recorder())
        await call(S.nde_semantic_search, query="immune profiling in infants")
        # No Lucene grouping: the AI backend takes the raw phrase.
        assert rec.last_query == "immune profiling in infants"

    async def test_empty_query_rejected_without_a_call(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_semantic_search, query="  ")
        assert "error" in out
        assert rec.requests == []


class TestSearchTools:
    async def test_pins_computational_tool_type(self, patched):
        rec = patched(Recorder())
        await call(S.nde_search_tools, query="alignment", programming_language="Python")
        assert '@type:"ComputationalTool"' in rec.last_query
        assert 'programmingLanguage:"Python"' in rec.last_query


class TestGetRecord:
    async def test_looks_up_by_id(self, patched):
        rec = patched(Recorder())
        await call(S.nde_get_record, record_id="test_1")
        assert rec.last_query == '_id:"test_1"'

    async def test_missing_record_reports_cleanly(self, patched):
        patched(Recorder({"total": 0, "hits": []}))
        out = await call(S.nde_get_record, record_id="nope")
        assert "error" in out
        assert "nope" in out["error"]

    async def test_empty_id_rejected_without_a_call(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_get_record, record_id="   ")
        assert "error" in out
        assert rec.requests == []

    async def test_id_with_quote_is_escaped(self, patched):
        rec = patched(Recorder({"total": 0, "hits": []}))
        await call(S.nde_get_record, record_id='x" OR "y')
        assert rec.last_query == '_id:"x\\" OR \\"y"'

    async def test_full_flag_keeps_embeddings(self, patched):
        hit = dict(HIT, ibmGraniteEmbeddingModel="m")
        patched(Recorder({"total": 1, "hits": [hit]}))
        trimmed = await call(S.nde_get_record, record_id="test_1")
        full = await call(S.nde_get_record, record_id="test_1", full=True)
        assert "ibmGraniteEmbeddingModel" not in trimmed["record"]
        assert "ibmGraniteEmbeddingModel" in full["record"]


class TestLookupIds:
    async def test_posts_batch_and_reports_misses(self, patched):
        response = [dict(HIT, query="a"), {"query": "b", "notfound": True}]
        rec = patched(Recorder(response))
        out = await call(S.nde_lookup_ids, ids="a, b")
        assert rec.requests[-1].method == "POST"
        assert out["requested"] == 2
        assert out["matched"] == 1
        assert out["not_found"] == ["b"]

    async def test_empty_ids_rejected(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_lookup_ids, ids="")
        assert "error" in out
        assert rec.requests == []

    async def test_oversized_batch_rejected_locally(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_lookup_ids, ids=",".join(str(i) for i in range(1001)))
        assert "error" in out
        assert rec.requests == []


class TestFacetCounts:
    async def test_requests_facets_with_zero_size(self, patched):
        rec = patched(Recorder({"total": 10, "hits": [], "facets": {}}))
        await call(S.nde_facet_counts, fields="@type, species.name", top_n=5)
        params = rec.requests[-1].url.params
        assert params["size"] == "0"
        assert params["facets"] == "@type,species.name"
        assert params["facet_size"] == "5"

    async def test_empty_fields_rejected(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_facet_counts, fields="")
        assert "error" in out
        assert rec.requests == []

    async def test_top_n_clamped_to_api_max(self, patched):
        rec = patched(Recorder({"total": 0, "hits": [], "facets": {}}))
        await call(S.nde_facet_counts, fields="@type", top_n=99_999)
        assert rec.requests[-1].url.params["facet_size"] == "1000"


class TestRawQuery:
    async def test_passes_lucene_through_untouched(self, patched):
        rec = patched(Recorder())
        q = '_exists_:nctid AND NOT includedInDataCatalog.name:"NCBI GEO"'
        await call(S.nde_raw_query, q=q)
        assert rec.last_query == q

    async def test_summarize_false_returns_raw_payload(self, patched):
        patched(Recorder())
        out = await call(S.nde_raw_query, q="flu", summarize=False)
        assert "hits" in out
        assert out["hits"][0]["_id"] == "test_1"

    async def test_empty_q_rejected(self, patched):
        rec = patched(Recorder())
        out = await call(S.nde_raw_query, q="")
        assert "error" in out
        assert rec.requests == []

    async def test_oversized_size_is_clamped_before_sending(self, patched):
        rec = patched(Recorder())
        await call(S.nde_raw_query, q="flu", size=99_999)
        assert rec.requests[-1].url.params["size"] == "1000"


class TestListTools:
    async def test_repositories_summarized(self, patched):
        metadata = {
            "build_date": "2026-09-08",
            "src": {"zenodo": {"stats": {"zenodo": 100}, "sourceInfo": {"name": "Zenodo"}}},
        }
        patched(Recorder(metadata))
        out = await call(S.nde_list_repositories)
        assert out["repository_count"] == 1
        assert out["repositories"][0]["record_count"] == 100

    async def test_fields_include_filter_shortcuts(self, patched):
        patched(Recorder({"name": {"type": "text"}, "@type": {"type": "keyword"}}))
        out = await call(S.nde_list_fields)
        assert out["total_fields"] == 2
        # The shortcut table tells the model which friendly name maps to which field.
        assert out["filter_shortcuts"]["pathogen"] == "infectiousAgent.name"
        assert "Dataset" in out["record_types"]
