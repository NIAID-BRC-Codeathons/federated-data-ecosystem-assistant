"""Shared fixtures for the MCP server tests. No network access.

Every test replaces requests.request with a Recorder, which both replays a
canned body and records what the tool asked for, so a test can assert on the
generated query as well as on the shaped response.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

# The servers in mcp_servers/ are standalone modules rather than a package, so
# the directory goes on the path rather than being imported as one. Putting it
# first also means `import mygene` finds this repo's server and not the
# similarly named client library on PyPI, if that is ever installed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_servers"))


# The real field types, read from https://mygene.info/v3/metadata/fields.
# Only the fields the tests exercise are here, and their types are verbatim,
# so a test that turns on a type is testing the real thing.
FIELD_INDEX: dict[str, dict] = {
    "symbol": {"index": True, "searched_by_default": True, "type": "keyword"},
    "name": {"index": True, "searched_by_default": True, "type": "text"},
    "taxid": {"index": True, "type": "integer"},
    "entrezgene": {"index": True, "searched_by_default": True, "type": "keyword"},
    "ensembl": {"index": True, "type": "object"},
    "ensembl.gene": {"index": True, "searched_by_default": True, "type": "keyword"},
    "type_of_gene": {"index": True, "type": "keyword"},
    "alias": {"index": True, "searched_by_default": True, "type": "keyword"},
    "summary": {"index": True, "searched_by_default": True, "type": "text"},
    "other_names": {"index": True, "searched_by_default": True, "type": "text"},
    "go": {"index": True, "type": "object"},
    "go.MF": {"index": True, "type": "object"},
    "go.MF.id": {"index": True, "searched_by_default": True, "type": "keyword"},
    "go.MF.term": {"index": True, "type": "text"},
    "go.MF.evidence": {"index": True, "type": "keyword"},
    # A facetable type that is not indexed, so it still cannot be faceted.
    "go.MF.pubmed": {"index": False, "type": "long"},
    "pathway": {"index": True, "type": "object"},
    "pathway.kegg": {"index": True, "type": "object"},
    "pathway.kegg.name": {"index": True, "type": "text"},
    "refseq": {"index": True, "type": "object"},
    "generif": {"index": True, "type": "object"},
    "uniprot": {"index": True, "type": "object"},
    "genomic_pos": {"index": True, "type": "object"},
    "genomic_pos.chr": {"index": True, "type": "keyword"},
    "HGNC": {"index": True, "type": "keyword"},
    "MIM": {"index": True, "type": "keyword"},
    "map_location": {"index": False, "type": "text"},
    "AnimalQTLdb": {"index": False, "type": "text"},
}

# The species MyGene names, from https://mygene.info/v3/metadata.
TAXONOMY: dict[str, int] = {
    "human": 9606,
    "mouse": 10090,
    "rat": 10116,
    "fruitfly": 7227,
    "nematode": 6239,
    "zebrafish": 7955,
    "thale-cress": 3702,
    "frog": 8364,
    "pig": 9823,
}

# The real field types, read from https://myvariant.info/v1/metadata/fields.
# Only the fields the tests exercise are here, and their types are verbatim,
# so a test that turns on a type is testing the real thing.
MV_FIELD_INDEX: dict[str, dict] = {
    "chrom": {"analyzer": "string_lowercase", "index": True, "type": "text"},
    "vcf": {"index": True, "type": "object"},
    "vcf.alt": {"analyzer": "string_lowercase", "index": True, "type": "text"},
    "vcf.ref": {"analyzer": "string_lowercase", "index": True, "type": "text"},
    "vcf.position": {"index": True, "type": "integer"},
    "dbsnp": {"index": True, "type": "object"},
    "dbsnp.rsid": {"index": True, "searched_by_default": True, "type": "keyword"},
    "dbsnp.gene": {"index": True, "type": "object"},
    "dbsnp.dbsnp_merges": {"index": True, "type": "object"},
    "dbsnp.dbsnp_merges.rsid": {"index": True, "searched_by_default": True, "type": "keyword"},
    "clinvar": {"index": True, "type": "object"},
    "clinvar.gene": {"index": True, "type": "object"},
    "clinvar.gene.symbol": {
        "analyzer": "string_lowercase", "index": True,
        "searched_by_default": True, "type": "text",
    },
    "clinvar.rcv": {"index": True, "type": "object"},
    "clinvar.rcv.clinical_significance": {"index": True, "type": "text"},
    "clinvar.rcv.accession": {
        "analyzer": "string_lowercase", "index": True,
        "searched_by_default": True, "type": "text",
    },
    "clinvar.rcv.last_evaluated": {"index": True, "type": "date"},
    "clinvar.rcv.number_submitters": {"index": True, "type": "integer"},
    "clinvar.hgvs": {"index": True, "type": "object"},
    "clinvar.hgvs.coding": {
        "analyzer": "string_lowercase", "index": True,
        "searched_by_default": True, "type": "text",
    },
    "clinvar.hgvs.protein": {
        "analyzer": "string_lowercase", "index": True,
        "searched_by_default": True, "type": "text",
    },
    "clinvar.hgvs.genomic": {
        "analyzer": "string_lowercase", "index": True,
        "searched_by_default": True, "type": "text",
    },
    "clinvar.variant_id": {"index": True, "type": "integer"},
    "clinvar.type": {"analyzer": "string_lowercase", "index": True, "type": "text"},
    "snpeff": {"index": True, "type": "object"},
    "snpeff.ann": {"index": True, "type": "object"},
    "snpeff.ann.genename": {"analyzer": "string_lowercase", "index": True, "type": "text"},
    "dbnsfp": {"index": True, "type": "object"},
    "dbnsfp.genename": {"index": True, "type": "keyword"},
    "dbnsfp.polyphen2": {"index": True, "type": "object"},
    "dbnsfp.polyphen2.hdiv": {"index": True, "type": "object"},
    "dbnsfp.polyphen2.hdiv.pred": {"index": True, "type": "keyword"},
    "cadd": {"index": True, "type": "object"},
    "cadd.phred": {"index": True, "type": "float"},
    # A facetable type that is not indexed, so it still cannot be faceted.
    "cadd.1000g.afr": {"index": False, "type": "float"},
    "civic": {"index": True, "type": "object"},
    "gnomad_exome": {"index": True, "type": "object"},
}


@dataclass
class Call:
    """One recorded request."""

    method: str
    url: str
    params: dict = field(default_factory=dict)
    body: Any = None

    @property
    def path(self) -> str:
        return self.url.replace("https://mygene.info/v3", "").replace("https://myvariant.info/v1", "")

    @property
    def q(self) -> str:
        return self.params.get("q", "")

    @property
    def fields(self) -> list[str]:
        return [f for f in str(self.params.get("fields", "")).split(",") if f]


class FakeResponse:
    """The parts of a requests.Response that the server reads."""

    def __init__(self, payload: Any, status: int = 200, *, json_fails: bool = False):
        self._payload = payload
        self.status_code = status
        self._json_fails = json_fails
        self.text = "" if json_fails else str(payload)

    def json(self) -> Any:
        if self._json_fails:
            raise ValueError("not json")
        return self._payload


class Recorder:
    """Stands in for requests.request: records the call, replays a body.

    `response` is either a body, or a callable taking (method, url, kwargs)
    that returns a body, a (body, status) pair, or raises to simulate a
    transport failure.
    """

    def __init__(self, response: Any = None, status: int = 200, *, json_fails: bool = False):
        self.calls: list[Call] = []
        self.response = response if response is not None else {"total": 0, "hits": []}
        self.status = status
        self.json_fails = json_fails

    def __call__(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(
            Call(method, url, dict(kwargs.get("params") or {}), kwargs.get("json"))
        )
        body = self.response
        if callable(body):
            body = body(method, url, kwargs)
        status = self.status
        if isinstance(body, tuple):
            body, status = body
        return FakeResponse(body, status, json_fails=self.json_fails)

    @property
    def last(self) -> Call:
        return self.calls[-1]

    @property
    def paths(self) -> list[str]:
        return [c.path for c in self.calls]


@pytest.fixture
def mygene():
    """The server module, with its schema cache emptied between tests."""
    import mygene as module

    module._schema_cache.clear()
    yield module
    module._schema_cache.clear()


@pytest.fixture
def schema(mygene):
    """Prime the field index and species table, so no test needs to serve them."""
    now = time.monotonic()
    mygene._schema_cache["fields"] = (now, FIELD_INDEX)
    mygene._schema_cache["taxonomy"] = (now, TAXONOMY)
    return mygene


@pytest.fixture
def myvariant():
    """The server module, with its schema cache emptied between tests."""
    import myvariant as module

    module._schema_cache.clear()
    yield module
    module._schema_cache.clear()


@pytest.fixture
def mv_schema(myvariant):
    """Prime the field index, so no test needs to serve it."""
    myvariant._schema_cache["fields"] = (time.monotonic(), MV_FIELD_INDEX)
    return myvariant


@pytest.fixture(autouse=True)
def block_network(mygene, myvariant, pubmed, monkeypatch):
    """Fail any request a test did not arrange, so the suite stays offline."""

    def refuse(method: str, url: str, **kwargs: Any):
        raise AssertionError(
            f"unmocked network call: {method} {url}. Use the `record` fixture."
        )

    def refuse_get(url: str, **kwargs: Any):
        raise AssertionError(
            f"unmocked network call: GET {url}. Use the `pm_record` fixture."
        )

    monkeypatch.setattr(mygene.requests, "request", refuse)
    monkeypatch.setattr(myvariant.requests, "request", refuse)
    # pubmed.py calls requests.get directly rather than requests.request.
    monkeypatch.setattr(pubmed.requests, "get", refuse_get)


@pytest.fixture
def record(mygene, block_network, monkeypatch) -> Callable[..., Recorder]:
    """Install a Recorder in place of requests.request. Returns the installer.

    Depends on block_network so the refusal is in place first and this
    replaces it, rather than the two racing on fixture order.
    """

    def install(response: Any = None, status: int = 200, *, json_fails: bool = False) -> Recorder:
        recorder = Recorder(response, status, json_fails=json_fails)
        monkeypatch.setattr(mygene.requests, "request", recorder)
        return recorder

    return install


@pytest.fixture
def mv_record(myvariant, block_network, monkeypatch) -> Callable[..., Recorder]:
    """Install a Recorder in place of requests.request, for myvariant.py."""

    def install(response: Any = None, status: int = 200, *, json_fails: bool = False) -> Recorder:
        recorder = Recorder(response, status, json_fails=json_fails)
        monkeypatch.setattr(myvariant.requests, "request", recorder)
        return recorder

    return install


# ---------------------------------------------------------------------------
# pubmed.py talks to NCBI E-utilities differently from the two BioThings
# servers above: it calls requests.get(url, **kwargs) directly rather than
# requests.request(method, url, **kwargs), and its _request reads resp.text
# through json.loads(..., strict=False) rather than resp.json(). The mock
# below matches that shape rather than reusing Recorder/FakeResponse, whose
# FakeResponse.text is a Python repr, not JSON, and would fail json.loads.
# ---------------------------------------------------------------------------


@dataclass
class PMCall:
    """One recorded requests.get call."""

    url: str
    params: dict = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        # ".../esearch.fcgi" -> "esearch"
        return self.url.rsplit("/", 1)[-1].removesuffix(".fcgi")

    @property
    def term(self) -> str:
        return self.params.get("term", "")


class PMFakeResponse:
    """The parts of a requests.Response that pubmed.py's _request reads.

    Unlike the BioThings servers, _request never calls resp.json(); it reads
    resp.text and parses it itself, tolerating stray control characters. So
    a dict payload is serialized to real JSON text here, and a str payload
    (XML, or a deliberately malformed body) is used exactly as given.
    """

    def __init__(self, payload: Any, status: int = 200):
        self.status_code = status
        self.text = payload if isinstance(payload, str) else json.dumps(payload)


class PMRecorder:
    """Stands in for requests.get: records the call, replays a body.

    `response` is either a body, or a callable taking (url, kwargs, call_number)
    -- call_number is 1-indexed, so a test can make the first N calls fail and
    the next one succeed, to exercise the 429-retry path without a real sleep.
    """

    def __init__(self, response: Any = None, status: int = 200):
        self.calls: list[PMCall] = []
        self.response = response if response is not None else {"esearchresult": {"count": "0", "idlist": []}}
        self.status = status

    def __call__(self, url: str, **kwargs: Any) -> PMFakeResponse:
        self.calls.append(PMCall(url, dict(kwargs.get("params") or {})))
        body = self.response
        if callable(body):
            body = body(url, kwargs, len(self.calls))
        status = self.status
        if isinstance(body, tuple):
            body, status = body
        return PMFakeResponse(body, status)

    @property
    def last(self) -> PMCall:
        return self.calls[-1]

    @property
    def endpoints(self) -> list[str]:
        return [c.endpoint for c in self.calls]


@pytest.fixture
def pubmed(monkeypatch):
    """The server module, with its module-level state reset between tests.

    Also silences time.sleep, real by default in both the rate-limit
    throttle and the 429 retry backoff: left alone, a two-call tool test
    would cost a real ~0.4s, and the suite runs hundreds of these. A test
    that means to exercise the throttle or the backoff delay itself
    re-patches time.sleep locally, after this fixture runs.
    """
    import pubmed as module

    module._field_cache.clear()
    module._last_call = 0.0
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    yield module
    module._field_cache.clear()
    module._last_call = 0.0


@pytest.fixture
def pm_record(pubmed, block_network, monkeypatch) -> Callable[..., PMRecorder]:
    """Install a PMRecorder in place of requests.get. Returns the installer."""

    def install(response: Any = None, status: int = 200) -> PMRecorder:
        recorder = PMRecorder(response, status)
        monkeypatch.setattr(pubmed.requests, "get", recorder)
        return recorder

    return install
