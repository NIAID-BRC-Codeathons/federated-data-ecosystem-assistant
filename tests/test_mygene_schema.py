"""Tests for field, species, and facet validation. No network access.

These guard the traps MyGene sets: an unknown field name in `fields` is
ignored rather than rejected, and an aggregation over a text field fails deep
inside Elasticsearch. Both would otherwise reach an agent as missing data or
an unreadable error.
"""

from __future__ import annotations

import time

import pytest

from conftest import FIELD_INDEX, TAXONOMY


class TestFieldIndexCache:
    def test_fetched_once_then_cached(self, mygene, record):
        recorder = record(FIELD_INDEX)
        assert mygene._field_index() == FIELD_INDEX
        mygene._field_index()
        assert recorder.paths == ["/metadata/fields"]

    def test_refetched_after_the_ttl(self, mygene, record):
        recorder = record(FIELD_INDEX)
        mygene._field_index()
        stale = time.monotonic() - mygene.SCHEMA_TTL_SECONDS - 1
        mygene._schema_cache["fields"] = (stale, FIELD_INDEX)
        mygene._field_index()
        assert len(recorder.calls) == 2

    def test_species_table_read_from_metadata(self, mygene, record):
        recorder = record({"taxonomy": TAXONOMY, "stats": {"total": 93788124}})
        assert mygene._species_aliases() == TAXONOMY
        assert recorder.last.path == "/metadata"


class TestCheckFields:
    def test_known_fields_pass(self, schema):
        schema._check_fields(["symbol", "name", "ensembl.gene", "go.MF.term"])

    def test_unknown_field_raises_with_close_matches(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._check_fields(["symbo"])
        assert "symbol" in str(exc.value)

    def test_unknown_field_suggests_substring_matches(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._check_fields(["kegg"])
        assert "pathway.kegg" in str(exc.value)

    def test_response_only_fields_are_allowed(self, schema):
        # The API adds these to a response rather than reading them from a
        # gene document, so they are not in the field index.
        schema._check_fields(["_id", "_score", "all", "query", "notfound"])

    def test_text_field_is_fine_for_output(self, schema):
        # A text field cannot be faceted, but selecting it for output is fine.
        schema._check_fields(["name"], must_be_indexed=False)

    def test_non_indexed_field_rejected_when_it_must_be_searchable(self, schema):
        with pytest.raises(schema.ToolError, match="not indexed"):
            schema._check_fields(["map_location"], must_be_indexed=True)

    def test_non_indexed_field_allowed_for_output(self, schema):
        schema._check_fields(["map_location"])


class TestCheckFacetable:
    @pytest.mark.parametrize("field", ["taxid", "type_of_gene", "symbol", "go.MF.id", "genomic_pos.chr"])
    def test_facetable_types_pass(self, schema, field):
        schema._check_facetable(field)

    def test_text_field_rejected_with_a_keyword_sibling(self, schema):
        # Faceting go.MF.term fails inside Elasticsearch with "Fielddata is
        # disabled", which does not say what to use instead. go.MF.id does.
        with pytest.raises(schema.ToolError) as exc:
            schema._check_facetable("go.MF.term")
        message = str(exc.value)
        assert "text" in message
        assert "go.MF.id" in message

    def test_object_field_rejected_with_leaf_suggestions(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._check_facetable("go.MF")
        assert "go.MF.id" in str(exc.value)

    def test_text_field_without_a_facetable_sibling(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._check_facetable("pathway.kegg.name")
        assert "Try one of" not in str(exc.value)

    def test_facetable_type_but_not_indexed_is_rejected(self, schema):
        with pytest.raises(schema.ToolError, match="not indexed"):
            schema._check_facetable("go.MF.pubmed")

    def test_unknown_field_rejected(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._check_facetable("taxidd")
        assert "taxid" in str(exc.value)


class TestResolveSpecies:
    def test_empty_means_all_species(self, schema):
        assert schema._resolve_species("") == ""
        assert schema._resolve_species("   ") == ""

    def test_common_name(self, schema):
        assert schema._resolve_species("human") == "human"

    def test_common_name_is_lowercased(self, schema):
        assert schema._resolve_species("Human") == "human"

    def test_taxid_passes_through(self, schema):
        # 246437 is not a named species, so only the numeric form works.
        assert schema._resolve_species("246437") == "246437"

    def test_mixed_list(self, schema):
        assert schema._resolve_species("human, mouse ,9823") == "human,mouse,9823"

    def test_explicit_all(self, schema):
        assert schema._resolve_species("all") == "all"

    def test_unknown_name_raises_with_close_match(self, schema):
        with pytest.raises(schema.ToolError) as exc:
            schema._resolve_species("hooman")
        message = str(exc.value)
        assert "human" in message
        assert "taxid" in message

    def test_error_names_the_offending_token_only(self, schema):
        # The API's own error says "cannot map some species to taxids" without
        # saying which one, so the tool has to.
        with pytest.raises(schema.ToolError) as exc:
            schema._resolve_species("human,notaspecies,mouse")
        assert "notaspecies" in str(exc.value)
