"""Unit tests for the MyGene server's pure helpers. No network access."""

from __future__ import annotations

import json

import pytest
import requests

from conftest import FakeResponse


class TestCleanParams:
    def test_drops_empty_values(self, mygene):
        assert mygene._clean_params({"a": None, "b": "", "c": [], "d": "keep"}) == {"d": "keep"}

    def test_keeps_zero(self, mygene):
        # size=0 is how a facet-only query asks for counts without hits, so it
        # must survive the clean. 0 == "" is False in Python, which is what
        # makes this work; the test pins that down.
        assert mygene._clean_params({"size": 0}) == {"size": 0}

    def test_renders_booleans_lowercase(self, mygene):
        assert mygene._clean_params({"a": True, "b": False}) == {"a": "true", "b": "false"}

    def test_joins_lists(self, mygene):
        assert mygene._clean_params({"fields": ["symbol", "name"]}) == {"fields": "symbol,name"}


class TestApiError:
    def test_validation_error_names_the_parameter_and_bound(self, mygene):
        # The body the API returns for size=1001.
        error = mygene._api_error(400, {
            "code": 400, "success": False, "error": "Bad Request",
            "keyword": "size", "max": 1000, "num": 1001, "alias": "limit",
        })
        assert "size" in str(error) and "1000" in str(error)

    def test_lucene_parse_error_keeps_the_root_cause(self, mygene):
        error = mygene._api_error(400, {
            "code": 400, "success": False,
            "error": "search_phase_execution_exception",
            "root_cause_line_00": "parse_exception: Encountered '<EOF>'",
        })
        assert "search_phase_execution_exception" in str(error)
        assert "parse_exception" in str(error)

    def test_root_causes_are_ordered(self, mygene):
        error = mygene._api_error(400, {
            "error": "boom",
            "root_cause_line_01": "second",
            "root_cause_line_00": "first",
        })
        assert str(error).index("first") < str(error).index("second")

    def test_value_error_keeps_details(self, mygene):
        error = mygene._api_error(400, {
            "error": "ValueError", "details": "Result window is too large",
        })
        assert "Result window is too large" in str(error)

    def test_non_dict_body(self, mygene):
        assert "502" in str(mygene._api_error(502, "<html>gateway</html>"))

    def test_message_is_bounded(self, mygene):
        error = mygene._api_error(400, {"error": "x" * 5000})
        assert len(str(error)) < 700


class TestRequest:
    def test_body_level_failure_raises_even_on_http_200(self, mygene, record):
        # The API reports some failures with a 200 status and success: false.
        record({"success": False, "error": "cannot map some species to taxids."})
        with pytest.raises(mygene.ToolError, match="cannot map some species"):
            mygene._get("/query", {"q": "CDK2"})

    def test_timeout_is_actionable(self, mygene, monkeypatch):
        def timeout(*args, **kwargs):
            raise requests.Timeout("too slow")

        monkeypatch.setattr(mygene.requests, "request", timeout)
        with pytest.raises(mygene.ToolError, match="timed out"):
            mygene._get("/query", {"q": "CDK2"})

    def test_connection_error_names_the_url(self, mygene, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(mygene.requests, "request", boom)
        with pytest.raises(mygene.ToolError, match="Could not reach MyGene.info"):
            mygene._get("/query", {"q": "CDK2"})

    def test_non_json_body_is_reported(self, mygene, record):
        record("<html>503</html>", status=503, json_fails=True)
        with pytest.raises(mygene.ToolError, match="non-JSON"):
            mygene._get("/query", {"q": "CDK2"})

    def test_sends_the_user_agent(self, mygene, record):
        recorder = record({"total": 0, "hits": []})
        mygene._get("/query", {"q": "CDK2"})
        # The recorder sees kwargs, so read the headers off the installed call.
        assert "federated-data-ecosystem-assistant" in mygene.HEADERS["User-Agent"]
        assert recorder.last.path == "/query"


class TestClampPaging:
    def test_within_limits_is_untouched(self, mygene):
        assert mygene._clamp_paging(10, 0) == (10, 0, None)

    def test_size_clamped_to_api_maximum(self, mygene):
        size, offset, note = mygene._clamp_paging(5000, 0)
        assert (size, offset) == (mygene.MAX_SIZE, 0)
        assert note and "1000" in note

    def test_result_window_clamps_size(self, mygene):
        size, offset, note = mygene._clamp_paging(50, 9990)
        assert (size, offset) == (10, 9990)
        assert note and "10000" in note

    def test_offset_alone_past_the_window_is_capped(self, mygene):
        # The API rejects "from" above 10000 outright, even with size=0, so
        # offset has to be capped on its own, not only relative to size.
        size, offset, note = mygene._clamp_paging(10, 99999)
        assert offset == mygene.MAX_RESULT_WINDOW
        assert size == 0
        assert note and "10000" in note

    def test_negative_inputs_are_floored(self, mygene):
        assert mygene._clamp_paging(-5, -5) == (0, 0, None)


class TestProvenance:
    def test_entrez_gene_link(self, mygene):
        links = mygene._provenance({"_id": "1017", "entrezgene": "1017"})
        assert links["ncbi_gene_url"].endswith("/gene/1017")
        assert links["mygene_url"].endswith("/gene/1017")

    def test_ensembl_link_from_dict(self, mygene):
        links = mygene._provenance({"ensembl": {"gene": "ENSG00000123374"}})
        assert links["ensembl_gene_url"].endswith("g=ENSG00000123374")

    def test_ensembl_link_from_list(self, mygene):
        # A gene mapped to several Ensembl models carries a list, not a dict.
        links = mygene._provenance({"ensembl": [{"gene": "ENSG1"}, {"gene": "ENSG2"}]})
        assert links["ensembl_gene_url"].endswith("g=ENSG1")

    def test_empty_ensembl_list_is_not_an_error(self, mygene):
        assert "ensembl_gene_url" not in mygene._provenance({"ensembl": []})

    def test_no_ids_means_no_links(self, mygene):
        assert mygene._provenance({"symbol": "CDK2"}) == {}


class TestSpeciesBreakdown:
    def test_single_species_has_no_warning(self, mygene):
        counts, note = mygene._species_breakdown([{"taxid": 9606}, {"taxid": 9606}], "human")
        assert counts == {9606: 2}
        assert note is None

    def test_unscoped_search_warns_it_spanned_all_species(self, mygene):
        counts, note = mygene._species_breakdown([{"taxid": 9606}, {"taxid": 10090}], "")
        assert counts == {9606: 1, 10090: 1}
        assert note and "all species" in note

    def test_scoped_search_still_warns_on_a_mix(self, mygene):
        _, note = mygene._species_breakdown([{"taxid": 9606}, {"taxid": 10090}], "human,mouse")
        assert note and "all species" not in note and "2 taxids" in note

    def test_hits_without_taxid(self, mygene):
        counts, note = mygene._species_breakdown([{"symbol": "CDK2"}], "")
        assert counts is None and note is None


class TestTrimGenerif:
    def test_short_list_untouched(self, mygene):
        doc = {"generif": [{"pubmed": i} for i in range(5)]}
        assert mygene._trim_generif(doc) is None
        assert len(doc["generif"]) == 5

    def test_long_list_trimmed_and_reported(self, mygene):
        doc = {"generif": [{"pubmed": i} for i in range(370)]}
        note = mygene._trim_generif(doc)
        assert len(doc["generif"]) == mygene.MAX_GENERIF
        assert note and "370" in note and "345" in note

    def test_absent_or_scalar_generif(self, mygene):
        assert mygene._trim_generif({}) is None
        assert mygene._trim_generif({"generif": "one"}) is None


class TestFit:
    """_fit trims rows until the whole root payload fits the budget.

    It trims to slightly under the budget, because the caller appends a note
    explaining the trim afterwards and that note has to fit too.
    """

    def test_reserves_room_for_the_truncation_note(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 4000)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(400)]}
        mygene._fit(payload, payload, "hits")
        # Room left over for a note of the length the callers actually write.
        assert len(json.dumps(payload)) <= 4000 - mygene.TRUNCATION_RESERVE

    def test_under_budget_is_untouched(self, mygene):
        payload = {"hits": [1, 2, 3]}
        assert mygene._fit(payload, payload, "hits") == (3, 3)
        assert payload["hits"] == [1, 2, 3]

    def test_trims_a_list(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 60)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(50)]}
        kept, total = mygene._fit(payload, payload, "hits")
        assert total == 50 and kept < 50
        assert len(payload["hits"]) == kept
        assert len(json.dumps(payload)) <= 60

    def test_trims_a_mapping(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 80)
        rows = {f"GENE{i}": {"entrezgene": str(i)} for i in range(50)}
        payload = {"matches": rows}
        kept, total = mygene._fit(payload, payload, "matches")
        assert total == 50 and kept < 50
        assert len(payload["matches"]) == kept

    def test_mapping_keeps_the_callers_order(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 40)
        payload = {"matches": {"A": 1, "B": 2, "C": 3, "D": 4}}
        mygene._fit(payload, payload, "matches")
        assert list(payload["matches"]) == list("ABCD")[: len(payload["matches"])]

    def test_empty_mapping_does_not_crash(self, mygene, monkeypatch):
        # A mapping has no keys to test for truthiness, so taking it for a list
        # used to slice a dict and raise KeyError.
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 1)
        payload = {"matches": {}}
        assert mygene._fit(payload, payload, "matches") == (0, 0)
        assert payload["matches"] == {}

    def test_empty_list_does_not_crash(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 1)
        payload = {"hits": []}
        assert mygene._fit(payload, payload, "hits") == (0, 0)

    def test_missing_or_scalar_key(self, mygene):
        assert mygene._fit({}, {}, "hits") == (0, 0)
        assert mygene._fit({"hits": "x"}, {"hits": "x"}, "hits") == (0, 0)

    def test_one_oversized_row_is_still_returned(self, mygene, monkeypatch):
        # Returning nothing would be worse than returning one row over budget.
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 10)
        payload = {"hits": [{"summary": "x" * 500}]}
        assert mygene._fit(payload, payload, "hits") == (1, 1)

    def test_measures_the_root_not_the_container(self, mygene, monkeypatch):
        # raw_query trims a nested hits list, so the keys wrapping it have to
        # count against the budget too. Same rows, same budget: the payload
        # carrying padding must keep strictly fewer of them.
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 400)
        rows = [{"symbol": f"G{i}"} for i in range(50)]

        bare = {"hits": list(rows)}
        kept_bare, _ = mygene._fit(bare, bare, "hits")

        nested = {"hits": list(rows)}
        padded = {"response": nested, "padding": "p" * 200}
        kept_padded, total = mygene._fit(padded, nested, "hits")

        assert total == 50
        assert kept_padded < kept_bare < 50
        assert len(json.dumps(padded)) <= 400


class TestBudget:
    def test_corrects_the_reported_count(self, mygene, monkeypatch):
        monkeypatch.setattr(mygene, "MAX_RESPONSE_CHARS", 80)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(40)], "hits_returned": 40}
        mygene._budget(payload, "hits", "hits_returned")
        assert payload["hits_returned"] == len(payload["hits"])
        assert "truncated" in payload

    def test_silent_when_nothing_was_dropped(self, mygene):
        payload = {"hits": [1, 2], "hits_returned": 2}
        mygene._budget(payload, "hits", "hits_returned")
        assert "truncated" not in payload
        assert payload["hits_returned"] == 2
