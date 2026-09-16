"""Parsing the formats E-utilities returns, against real captured responses."""

from ncbi_mcp.databases import RUNINFO_OPEN_ACCESS_EMPTY
from ncbi_mcp.parsing import (
    expand_sra_summary,
    expand_xml_field,
    missing_accessions,
    order_runinfo,
    parse_esummary_records,
    parse_linksets,
    parse_runinfo_csv,
    parse_xml_fragment,
    xml_to_dict,
)

from .conftest import load_json, load_text

# --- runinfo CSV ----------------------------------------------------------


def test_runinfo_parses_and_keeps_the_useful_columns():
    rows = parse_runinfo_csv(load_text("runinfo_small.csv"))
    assert len(rows) == 3
    assert all(r["Run"].startswith("SRR") for r in rows)
    assert rows[0]["BioProject"]
    assert rows[0]["download_path"].startswith("https://")


def test_runinfo_drops_only_the_requested_columns():
    rows = parse_runinfo_csv(
        load_text("runinfo_small.csv"), drop_columns=RUNINFO_OPEN_ACCESS_EMPTY
    )
    for column in RUNINFO_OPEN_ACCESS_EMPTY:
        assert column not in rows[0]
    # These four look like they belong to the same controlled-access family but
    # are populated for open data; dropping them would lose real metadata.
    for column in ("ReleaseDate", "LoadDate", "InsertDev", "Tumor"):
        assert column in rows[0]


def test_runinfo_skips_blank_lines_and_repeated_headers():
    csv_text = load_text("runinfo_small.csv").rstrip("\n")
    header = csv_text.splitlines()[0]
    polluted = csv_text + "\n\n" + header + "\n\n"
    assert len(parse_runinfo_csv(polluted)) == 3


def test_runinfo_is_reordered_to_match_the_request():
    """efetch row order does not follow the requested order.

    Zipping positionally would attach one run's metadata to another run's
    accession --- a wrong answer that looks completely well-formed.
    """
    rows = parse_runinfo_csv(load_text("runinfo_small.csv"))
    returned = [r["Run"] for r in rows]
    requested = list(reversed(returned))

    ordered = order_runinfo(rows, requested)
    assert [r["Run"] for r in ordered] == requested


def test_missing_accessions_are_detected():
    rows = parse_runinfo_csv(load_text("runinfo_small.csv"))
    requested = [rows[0]["Run"], "SRR0000000"]
    assert missing_accessions(rows, requested) == ["SRR0000000"]


# --- XML buried in JSON ---------------------------------------------------


def test_multi_root_fragment_parses():
    """expxml is a run of sibling elements with no wrapper --- not well-formed
    XML on its own, so ElementTree rejects it unless it is wrapped first."""
    parsed = parse_xml_fragment("<A x='1'/><B>text</B>")
    assert parsed is not None
    assert xml_to_dict(parsed) == {"A": {"x": "1"}, "B": "text"}


def test_single_root_fragment_also_parses():
    assert xml_to_dict(parse_xml_fragment("<A>v</A>")) == {"A": "v"}


def test_malformed_fragment_returns_none_rather_than_raising():
    assert parse_xml_fragment("<A><unclosed>") is None
    assert parse_xml_fragment("") is None


def test_repeated_tags_become_a_list():
    assert xml_to_dict(parse_xml_fragment("<I>a</I><I>b</I>")) == {"I": ["a", "b"]}


def test_sra_summary_xml_fields_are_expanded():
    records, _ = parse_esummary_records(load_json("sra_esummary.json"))
    expanded = expand_sra_summary(records[0])
    assert isinstance(expanded["expxml"], dict), "expxml must not stay a string"
    assert isinstance(expanded["runs"], dict)


def test_biosample_sampledata_is_expanded():
    records, _ = parse_esummary_records(load_json("biosample_esummary.json"))
    expanded = expand_xml_field(records[0], "sampledata")
    assert isinstance(expanded["sampledata"], dict)


# --- elink ----------------------------------------------------------------


def test_no_links_returns_empty_without_keyerror():
    """The ordinary no-links case OMITS linksetdbs rather than sending [].

    payload["linksets"][0]["linksetdbs"] raises KeyError on the most common
    outcome there is.
    """
    payload = load_json("elink_no_links.json")
    assert "linksetdbs" not in payload["linksets"][0]  # guard on the fixture
    assert parse_linksets(payload) == {}


def test_linksets_group_by_target_database():
    payload = {
        "linksets": [
            {
                "dbfrom": "bioproject",
                "linksetdbs": [
                    {"dbto": "sra", "links": ["1", "2"]},
                    {"dbto": "pubmed", "links": ["9"]},
                ],
            }
        ]
    }
    assert parse_linksets(payload) == {"sra": ["1", "2"], "pubmed": ["9"]}
