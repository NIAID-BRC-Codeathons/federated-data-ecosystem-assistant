"""Error detection, against real captured responses.

Every fixture here is a body NCBI actually returned with HTTP 200. The point of
these tests is that "HTTP 200" and "success" are different things, in at least
four distinct ways.
"""

import pytest
from ncbi_helpers import load_json
from ncbi_lib.envelope import NCBIError, check_for_error, envelope, provenance
from ncbi_lib.parsing import parse_esummary_records


def test_top_level_error_is_raised():
    """Shape 3: an accession where a UID belongs -> top-level "error"."""
    payload = load_json("esummary_bad_accession.json")
    assert "error" in payload  # guard: the fixture still has the shape under test
    with pytest.raises(NCBIError, match="Invalid uid"):
        check_for_error(payload)


def test_esearch_nested_error_is_raised():
    """Shape 1: esearchresult.ERROR, uppercase and nested."""
    with pytest.raises(NCBIError, match="Search Backend failed"):
        check_for_error({"esearchresult": {"ERROR": "Search Backend failed"}})


def test_esummaryresult_list_error_is_raised():
    """Shape 2: esummary reports a LIST of strings, not a string."""
    with pytest.raises(NCBIError, match="Invalid uid"):
        check_for_error({"esummaryresult": ["Invalid uid 0 at position 0"]})


def test_zero_hits_is_not_an_error():
    """A search matching nothing populates errorlist but is a valid result.

    Raising here would report "no data" as "the tool is broken", which is the
    single easiest way to make an agent give up on a correct query.
    """
    payload = load_json("esearch_zero_hits.json")
    result = payload["esearchresult"]
    assert result["errorlist"]["phrasesnotfound"]  # guard: errorlist IS populated
    check_for_error(payload)  # must not raise
    assert result["count"] == "0"


def test_per_uid_error_is_split_out_of_the_data():
    """Shape 4: the error hides inside an otherwise normal-looking record.

    None of the top-level checks can see this one, so it has to be caught when
    the records are unpacked --- otherwise it reaches the agent as data.
    """
    payload = load_json("esummary_invalid_uid.json")
    check_for_error(payload)  # correctly finds nothing at the top level

    records, errors = parse_esummary_records(payload)
    assert records == [], "a record that is only an error must not be returned as data"
    assert errors == ["999999999999: cannot get document summary"]


def test_good_records_survive_the_error_split():
    records, errors = parse_esummary_records(load_json("sra_esummary.json"))
    assert errors == []
    assert len(records) == 1
    assert records[0]["uid"] == "1063523"


def test_envelope_orders_summary_before_data():
    result = envelope("s", [1, 2], provenance(["u"], elapsed_ms=1))
    assert list(result) == ["summary", "data", "provenance"]


def test_truncation_is_always_reported():
    result = envelope(
        "s",
        [1],
        provenance(["u"], elapsed_ms=1),
        truncated=True,
        next_params={"retstart": 10},
    )
    assert result["truncated"] is True
    assert result["next"] == {"retstart": 10}


def test_provenance_omits_absent_fields_but_always_has_urls():
    block = provenance(["https://example"], elapsed_ms=5)
    assert block["urls"] == ["https://example"]
    assert "query_translation" not in block
    assert "notes" not in block
