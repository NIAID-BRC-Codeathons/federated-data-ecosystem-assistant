"""Pathogen Detection: the paths that would otherwise return plausible wrong numbers.

This service answers HTTP 200 to everything, stores every isolate twice, and
empties ``content`` if you ask it for a facet. None of those announce
themselves --- each one produces a number that looks like an answer. These tests
pin the three defenses in place.
"""

import pytest

from ncbi_mcp.envelope import NCBIError
from ncbi_mcp.pathogens import (
    _parse,
    build_fq,
    count_params,
    distinct_count,
    facet_buckets,
    quote_value,
    raw_row_count,
    record_params,
    records,
    solr_query,
)

from .conftest import load_json

# --- the 2x duplication ---------------------------------------------------


def test_fixture_really_contains_duplicate_rows():
    """Guard: if NCBI ever deduplicates, the tests below stop testing anything."""
    ngout = load_json("pathogens_duplicated_rows.json")["ngout"]
    content = ngout["data"]["content"]
    assert len(content) == 2
    assert len({row["target_acc"] for row in content}) == 1


def test_records_deduplicates_on_target_acc():
    ngout = load_json("pathogens_duplicated_rows.json")["ngout"]
    assert len(records(ngout)) == 1


def test_raw_row_count_is_the_undeduplicated_total():
    """totalCount counts index rows, not isolates. It is ~2x and stays that way."""
    ngout = load_json("pathogens_duplicated_rows.json")["ngout"]
    assert raw_row_count(ngout) == 2
    assert raw_row_count(ngout) == 2 * len(records(ngout))


def test_distinct_count_never_falls_back_to_total_count():
    """The whole point: no facet means no answer, not a wrong answer.

    Returning totalCount here would double every isolate count in the server,
    which is exactly the error distinct_count exists to prevent.
    """
    ngout = load_json("pathogens_duplicated_rows.json")["ngout"]
    assert "facets" not in ngout  # guard: fixture was captured without facets
    assert distinct_count(ngout) is None


def test_distinct_count_reads_num_buckets():
    ngout = {
        "facets": {"target_acc": {"numBuckets": 94336}},
        "data": {"totalCount": 188671},
    }
    assert distinct_count(ngout) == 94336


def test_records_and_counts_survive_a_body_with_no_data():
    assert records({}) == []
    assert raw_row_count({}) == 0
    assert distinct_count({}) is None
    assert facet_buckets({}, "AMR_genotypes") == []


# --- every failure arrives as HTTP 200 ------------------------------------


def test_empty_body_is_an_error_not_an_empty_result():
    """An unknown or missing `action` returns 200 and zero bytes."""
    with pytest.raises(NCBIError, match="empty response"):
        _parse("")


def test_non_json_body_is_reported_with_its_first_bytes():
    """`limit=abc` returns a plain-text CGI passthrough; a bad sort returns ERROR:."""
    with pytest.raises(NCBIError, match="non-JSON"):
        _parse("Status: 500 Internal Server Error\n")
    with pytest.raises(NCBIError, match="non-JSON"):
        _parse("ERROR: CJsonObject:NextChar: unexpected end")


def test_success_false_is_raised():
    payload = load_json("pathogens_error.json")
    assert payload["success"] is False  # guard
    with pytest.raises(NCBIError, match="rejected the query"):
        _parse('{"success": false, "error": "cannot retrieve from the database"}')


def test_body_without_ngout_is_an_error():
    with pytest.raises(NCBIError, match="no 'ngout'"):
        _parse('{"success": true}')


def test_json_that_is_not_an_object_is_an_error():
    with pytest.raises(NCBIError, match="unexpected shape"):
        _parse("[1, 2, 3]")


def test_zero_matches_is_a_valid_result_not_an_error():
    """`success: true` with totalCount 0 is a real negative, not a failure."""
    ngout = _parse(
        '{"success": true, "ngout": {"data": {"totalCount": 0, "content": []}}}'
    )
    assert raw_row_count(ngout) == 0
    assert records(ngout) == []


# --- query construction ---------------------------------------------------


def test_quote_value_refuses_a_double_quote():
    """Values are interpolated into field==["v"]; a quote would rewrite the query.

    Refusing beats escaping: a broken-out filter comes back as plausible data,
    because this service reports a malformed filter and an unknown field with
    the same opaque string.
    """
    with pytest.raises(NCBIError, match="cannot be used"):
        quote_value('Escherichia" or taxgroup_name==["Salmonella')


def test_quote_value_refuses_a_backslash():
    with pytest.raises(NCBIError, match="cannot be used"):
        quote_value("Staphylococcus\\aureus")


def test_quote_value_passes_ordinary_names_through():
    assert quote_value("E.coli and Shigella") == "E.coli and Shigella"


def test_build_fq_ors_values_and_ands_clauses():
    fq = build_fq(
        {"taxgroup_name": ["Staphylococcus aureus"], "AMR_genotypes": ["mecA", "mecC"]}
    )
    assert fq == (
        'taxgroup_name==["Staphylococcus aureus"] and AMR_genotypes==["mecA","mecC"]'
    )


def test_build_fq_skips_empty_clauses_and_returns_none_when_unfiltered():
    assert build_fq({"host": []}) is None
    assert build_fq({}) is None


# --- rows XOR the query echo ----------------------------------------------


def test_record_params_never_requests_a_facet():
    """A facet --- any facet --- empties `content` while totalCount stays right.

    Measured: limit=4 alone returns 2 rows; limit=4 plus an unrelated
    facets=taxgroup_name[||1|5] returns 0 rows and totalCount 2.
    """
    params = record_params('taxgroup_name==["Staphylococcus aureus"]', limit=20)
    assert "facets" not in params


def test_record_params_doubles_the_limit_for_the_duplication():
    assert record_params(None, limit=20)["limit"] == 40


def test_count_params_requests_the_distinct_facet_and_no_rows():
    params = count_params(None)
    assert params["limit"] == 0
    assert params["facets"].startswith("target_acc[")


def test_solr_query_joins_the_echoed_filters():
    ngout = {"facets": {"query": ["* -status:withdrawn", 'taxgroup_name:("X")']}}
    assert solr_query(ngout) == '* -status:withdrawn AND taxgroup_name:("X")'


def test_solr_query_is_absent_without_facets():
    """Record fetches cannot have it, so callers must tolerate None."""
    ngout = load_json("pathogens_duplicated_rows.json")["ngout"]
    assert solr_query(ngout) is None
