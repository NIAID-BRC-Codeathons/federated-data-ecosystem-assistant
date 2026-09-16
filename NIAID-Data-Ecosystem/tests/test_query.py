"""Unit tests for Lucene query construction. No network access."""

from __future__ import annotations

import pytest

from nde_mcp.query import (
    build_clauses,
    build_query,
    clamp_paging,
    date_range_clause,
    escape_value,
    join_clauses,
    normalize_sort,
    vocabulary_hint,
)


class TestBuildQuery:
    def test_empty_is_match_all(self):
        assert build_query() == "__all__"
        assert build_query("") == "__all__"
        assert build_query("   ") == "__all__"

    def test_single_term_not_grouped(self):
        assert build_query("malaria") == "malaria"

    def test_multi_term_is_grouped(self):
        # Without grouping, appending `AND field:x` would bind only to the
        # trailing token.
        assert build_query("antimicrobial resistance") == "(antimicrobial resistance)"

    def test_already_grouped_text_untouched(self):
        assert build_query("(dengue OR zika)") == "(dengue OR zika)"

    def test_filters_are_anded(self):
        q = build_query("flu", filters={"record_type": "Dataset", "repository": "NCBI SRA"})
        assert q == 'flu AND @type:"Dataset" AND includedInDataCatalog.name:"NCBI SRA"'

    def test_multiple_values_become_or_group(self):
        q = build_query(filters={"repository": ["NCBI SRA", "Zenodo"]})
        assert q == '(includedInDataCatalog.name:"NCBI SRA" OR includedInDataCatalog.name:"Zenodo")'

    def test_values_with_spaces_are_quoted(self):
        # Unquoted, "Influenza A virus" would tokenize into three loose terms.
        q = build_query(filters={"pathogen": "Influenza A virus"})
        assert q == 'infectiousAgent.name:"Influenza A virus"'

    def test_unknown_filter_key_passes_through_as_field(self):
        q = build_query(filters={"customField.name": "x"})
        assert q == 'customField.name:"x"'

    def test_empty_filter_values_are_dropped(self):
        assert build_query("flu", filters={"pathogen": None, "repository": []}) == "flu"
        assert build_query(filters={"pathogen": "  "}) == "__all__"

    def test_filters_only_without_text(self):
        assert build_query(filters={"record_type": "Dataset"}) == '@type:"Dataset"'

    def test_all_sentinel_is_not_emitted_as_text(self):
        assert build_query("__all__", filters={"record_type": "Dataset"}) == '@type:"Dataset"'


class TestDateRanges:
    def test_both_bounds(self):
        assert date_range_clause("date", "2024-01-01", "2025-12-31") == "date:[2024-01-01 TO 2025-12-31]"

    def test_open_upper_bound(self):
        assert date_range_clause("date", "2024-01-01", None) == "date:[2024-01-01 TO *]"

    def test_open_lower_bound(self):
        assert date_range_clause("date", None, "2024-01-01") == "date:[* TO 2024-01-01]"

    def test_no_bounds_is_none(self):
        assert date_range_clause("date", None, None) is None

    def test_range_appended_to_query(self):
        q = build_query("flu", date_from="2024-01-01")
        assert q == "flu AND date:[2024-01-01 TO *]"

    def test_range_value_not_quoted(self):
        # A quoted range would be parsed as a literal string, not a range.
        q = build_query(filters={"date": "[2024-01-01 TO *]"})
        assert q == "date:[2024-01-01 TO *]"


class TestEscaping:
    def test_embedded_quote_escaped(self):
        assert escape_value('say "hi"') == 'say \\"hi\\"'

    def test_backslash_escaped(self):
        assert escape_value("a\\b") == "a\\\\b"

    def test_quote_in_filter_value_cannot_break_out(self):
        # A value containing a quote must not terminate the phrase early and
        # inject its own clause.
        q = build_query(filters={"pathogen": 'evil" OR @type:"Dataset'})
        assert q.count('infectiousAgent.name:"') == 1
        assert q == 'infectiousAgent.name:"evil\\" OR @type:\\"Dataset"'

    def test_wildcard_passes_through(self):
        assert build_query(filters={"doi": "*"}) == "doi:*"


class TestClamping:
    def test_size_capped_at_1000(self):
        assert clamp_paging(5000, 0) == (1000, 0)

    def test_negative_values_floored(self):
        assert clamp_paging(-5, -10) == (0, 0)

    def test_normal_values_unchanged(self):
        assert clamp_paging(10, 20) == (10, 20)

    def test_window_boundary_shrinks_size(self):
        # from + size must stay <= 10000.
        assert clamp_paging(100, 9950) == (50, 9950)

    def test_offset_beyond_window_is_pulled_back(self):
        size, offset = clamp_paging(10, 50_000)
        assert offset < 10_000
        assert offset + size <= 10_000


class TestSort:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("relevance", None),
            ("newest", "-date"),
            ("oldest", "date"),
            ("recently_updated", "-dateModified"),
            ("name", "name.keyword"),
            (None, None),
            ("", None),
        ],
    )
    def test_aliases(self, alias, expected):
        assert normalize_sort(alias) == expected

    def test_raw_expression_passes_through(self):
        assert normalize_sort("-datePublished") == "-datePublished"


class TestClauses:
    def test_clauses_are_labelled(self):
        clauses = build_clauses(
            "flu", filters={"record_type": "Dataset"}, date_from="2024-01-01"
        )
        assert [label for label, _ in clauses] == ["query", "record_type", "date range"]

    def test_join_round_trips_with_build_query(self):
        kwargs = {"filters": {"record_type": "Dataset", "repository": "Zenodo"}, "date_from": "2024"}
        assert join_clauses(build_clauses("flu", **kwargs)) == build_query("flu", **kwargs)

    def test_join_empty_is_match_all(self):
        assert join_clauses([]) == "__all__"


class TestVocabularyHints:
    def test_known_field_has_hint(self):
        hint = vocabulary_hint("measurementTechnique.name")
        assert hint and "Sample" in hint and "Dataset" in hint

    def test_unknown_field_has_none(self):
        assert vocabulary_hint("name") is None
