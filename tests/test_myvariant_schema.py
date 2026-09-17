"""Tests for field and facet validation. No network access.

These guard the traps MyVariant sets: an unknown field name in `fields` is
ignored rather than rejected, and an aggregation over a text field fails deep
inside Elasticsearch. Both would otherwise reach an agent as missing data or
an unreadable error.
"""

from __future__ import annotations

import time

import pytest

from conftest import MV_FIELD_INDEX


class TestFieldIndexCache:
    def test_fetched_once_then_cached(self, myvariant, mv_record):
        recorder = mv_record(MV_FIELD_INDEX)
        assert myvariant._field_index() == MV_FIELD_INDEX
        myvariant._field_index()
        assert recorder.paths == ["/metadata/fields"]

    def test_refetched_after_the_ttl(self, myvariant, mv_record):
        recorder = mv_record(MV_FIELD_INDEX)
        myvariant._field_index()
        stale = time.monotonic() - myvariant.SCHEMA_TTL_SECONDS - 1
        myvariant._schema_cache["fields"] = (stale, MV_FIELD_INDEX)
        myvariant._field_index()
        assert len(recorder.calls) == 2


class TestCheckFields:
    def test_known_fields_pass(self, mv_schema):
        mv_schema._check_fields(["chrom", "vcf.alt", "dbsnp.rsid", "clinvar.rcv.clinical_significance"])

    def test_unknown_field_raises_with_close_matches(self, mv_schema):
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_fields(["chrome"])
        assert "chrom" in str(exc.value)

    def test_unknown_field_suggests_substring_matches(self, mv_schema):
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_fields(["polyphen2"])
        assert "dbnsfp.polyphen2" in str(exc.value)

    def test_response_only_fields_are_allowed(self, mv_schema):
        # The API adds these to a response rather than reading them from a
        # variant document, so they are not in the field index.
        mv_schema._check_fields(["_id", "_score", "all", "query", "notfound"])

    def test_text_field_is_fine_for_output(self, mv_schema):
        # A text field cannot be faceted, but selecting it for output is fine.
        mv_schema._check_fields(["clinvar.rcv.clinical_significance"], must_be_indexed=False)

    def test_non_indexed_field_rejected_when_it_must_be_searchable(self, mv_schema):
        with pytest.raises(mv_schema.ToolError, match="not indexed"):
            mv_schema._check_fields(["cadd.1000g.afr"], must_be_indexed=True)

    def test_non_indexed_field_allowed_for_output(self, mv_schema):
        mv_schema._check_fields(["cadd.1000g.afr"])


class TestCheckFacetable:
    @pytest.mark.parametrize(
        "field", ["dbsnp.rsid", "dbnsfp.genename", "dbnsfp.polyphen2.hdiv.pred", "cadd.phred", "vcf.position"],
    )
    def test_facetable_types_pass(self, mv_schema, field):
        mv_schema._check_facetable(field)

    def test_text_field_rejected_with_a_keyword_sibling(self, mv_schema):
        # Faceting clinvar.rcv.clinical_significance fails inside
        # Elasticsearch with "Fielddata is disabled", which does not say what
        # to use instead. This finds a real keyword sibling in the same
        # nested object.
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_facetable("clinvar.rcv.clinical_significance")
        message = str(exc.value)
        assert "text" in message
        assert "clinvar.rcv.number_submitters" in message or "clinvar.rcv.last_evaluated" in message

    def test_chrom_rejected_with_no_sibling_to_suggest(self, mv_schema):
        # chrom has no dotted parent, so there is nothing to search for a
        # sibling under; the error should say so cleanly rather than crash.
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_facetable("chrom")
        assert "Try one of" not in str(exc.value)

    def test_object_field_rejected_with_leaf_suggestions(self, mv_schema):
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_facetable("clinvar.rcv")
        assert "clinvar.rcv.number_submitters" in str(exc.value) or "clinvar.rcv.last_evaluated" in str(exc.value)

    def test_facetable_type_but_not_indexed_is_rejected(self, mv_schema):
        with pytest.raises(mv_schema.ToolError, match="not indexed"):
            mv_schema._check_facetable("cadd.1000g.afr")

    def test_unknown_field_rejected(self, mv_schema):
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema._check_facetable("chromm")
        assert "chrom" in str(exc.value)


class TestResolveAssembly:
    def test_empty_means_server_default(self, myvariant):
        assert myvariant._resolve_assembly("") == ""

    def test_known_values(self, myvariant):
        assert myvariant._resolve_assembly("hg19") == "hg19"
        assert myvariant._resolve_assembly("hg38") == "hg38"

    def test_unknown_value_lists_both_builds(self, myvariant):
        with pytest.raises(myvariant.ToolError) as exc:
            myvariant._resolve_assembly("grch38")
        message = str(exc.value)
        assert "hg19" in message and "hg38" in message
