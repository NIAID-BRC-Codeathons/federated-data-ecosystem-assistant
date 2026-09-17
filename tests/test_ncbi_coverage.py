"""Coverage percentages and per-database attribution.

The point of these numbers is to stop an agent reporting a numerator as if it
were a population, so the tests care most about the cases where a number would
be *wrong but plausible*: a denominator that silently means something else, a
percentage invented for a split nobody measured, and a coverage lookup being
credited as a source of data.
"""

from ncbi_lib import coverage
from ncbi_lib.envelope import provenance
from ncbi_lib.eutils import Call

# --- picking a denominator ------------------------------------------------


def test_organism_clause_becomes_the_denominator():
    """The organism is the population; everything else is the selection.

    "S. aureus AND MRSA" should be measured against all S. aureus, not against
    all of BioSample and not against itself.
    """
    assert (
        coverage.organism_basis("Staphylococcus aureus[ORGN] AND (MRSA OR mecA)")
        == "Staphylococcus aureus[ORGN]"
    )


def test_quoted_and_lowercase_organism_spellings_both_match():
    assert coverage.organism_basis('"Escherichia coli"[Organism] AND x') is not None
    assert coverage.organism_basis("Escherichia coli[orgn]") is not None


def test_repeated_organisms_are_not_double_counted():
    basis = coverage.organism_basis("Homo sapiens[ORGN] OR Homo sapiens[ORGN]")
    assert basis == "Homo sapiens[ORGN]"


def test_no_organism_clause_means_no_organism_basis():
    """A project accession has no organism, so the caller must fall back to the
    database total rather than inventing a subset."""
    assert coverage.organism_basis("PRJNA257197") is None
    assert coverage.organism_basis("") is None


# --- fetch coverage -------------------------------------------------------


def test_fetch_coverage_reports_silently_dropped_uids():
    """esummary omits UIDs it cannot resolve without comment, so 48-of-50 and
    50-of-50 are indistinguishable unless something counts them."""
    block = coverage.for_fetch("gene", requested=50, returned=48)
    assert block["matched"] == 48
    assert block["denominator"] == 50
    assert block["percent"] == 96.0
    assert block["shortfall"] == 2


def test_fetch_coverage_has_no_shortfall_key_when_nothing_was_dropped():
    assert "shortfall" not in coverage.for_fetch("gene", requested=3, returned=3)


def test_zero_denominator_does_not_divide_by_zero():
    assert coverage.for_fetch("gene", requested=0, returned=0) is None


def test_coverage_can_be_switched_off(monkeypatch):
    """The search flavor costs an extra request against a shared per-IP budget."""
    monkeypatch.setenv("NCBI_COVERAGE", "0")
    assert coverage.enabled() is False
    assert coverage.for_fetch("gene", requested=5, returned=5) is None


# --- per-database attribution ---------------------------------------------


def test_single_database_is_credited_with_the_whole_result():
    block = provenance(
        [Call("esearch", "sra", "u1"), Call("esummary", "sra", "u2")],
        elapsed_ms=1,
    )
    (source,) = block["sources"]
    assert source["database"] == "sra"
    assert source["calls"] == 2
    assert source["utilities"] == ["esearch", "esummary"]
    assert source["percent_of_result"] == 100.0


def test_multiple_databases_split_by_measured_record_counts():
    block = provenance(
        [Call("esearch", "biosample", "u1"), Call("esearch", "pubmed", "u2")],
        elapsed_ms=1,
        records_by_database={"biosample": 80, "pubmed": 20},
    )
    percents = {s["database"]: s["percent_of_result"] for s in block["sources"]}
    assert percents == {"biosample": 80.0, "pubmed": 20.0}


def test_no_percentage_is_invented_for_an_unmeasured_split():
    """Two databases and no record counts: the honest answer is to say which
    databases were touched and omit the share, not to guess 50/50."""
    block = provenance(
        [Call("esearch", "sra", "u1"), Call("esearch", "pubmed", "u2")],
        elapsed_ms=1,
    )
    assert [s["database"] for s in block["sources"]] == ["sra", "pubmed"]
    assert all("percent_of_result" not in s for s in block["sources"])


def test_coverage_calls_cost_a_request_but_never_credit_a_database():
    """A denominator lookup describes the result; it does not contribute to it.

    It still has to appear in request_cost --- execution cost is scored, and
    hiding the extra request would understate what the answer cost.
    """
    block = provenance(
        [
            Call("esearch", "sra", "u1"),
            Call("esearch", "sra", "u2", purpose="coverage"),
        ],
        elapsed_ms=1,
    )
    (source,) = block["sources"]
    assert source["calls"] == 1, "coverage lookup must not inflate the source"
    assert block["request_cost"] == {"total": 2, "primary": 1, "coverage": 1}
    assert len(block["urls"]) == 2, "both calls were really made; report both"


def test_plain_url_strings_still_produce_a_valid_block():
    """Back-compat: a caller with only URLs loses the breakdown, not the block."""
    block = provenance(["https://example"], elapsed_ms=5)
    assert block["urls"] == ["https://example"]
    assert "sources" not in block


def test_tool_name_is_recorded():
    block = provenance([], elapsed_ms=1, tool="ncbi_sra_search")
    assert block["tool"] == "ncbi_sra_search"
