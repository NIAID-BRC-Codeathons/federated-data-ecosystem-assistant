"""Tool-level tests against a recorded transport. No network access.

Each test asserts on both sides of a tool: the request it generated, and the
response it shaped. The request side matters as much as the response, because
a filter the API silently ignores returns plausible but wrong data.
"""

from __future__ import annotations

import json

import pytest

BRAF_HIT = {
    "_id": "chr7:g.140453136A>T",
    "_score": 35.6,
    "chrom": "7",
    "vcf": {"alt": "T", "position": "140453136", "ref": "A"},
    "dbsnp": {"rsid": "rs113488022"},
    "clinvar": {"variant_id": 13961, "gene": {"symbol": "BRAF"}},
    "snpeff": {"ann": {"genename": "BRAF"}},
    "cadd": {"phred": 32.0},
}

SEARCH = {"took": 3, "total": 148, "max_score": 35.6, "hits": [BRAF_HIT]}

# rs334 (sickle cell HBB) is genuinely multi-allelic: one rsid, three alleles
# at the same position.
RS334_HITS = [
    {"_id": "chr11:g.5248232T>A", "chrom": "11", "vcf": {"alt": "A", "position": "5248232", "ref": "T"}},
    {"_id": "chr11:g.5248232T>C", "chrom": "11", "vcf": {"alt": "C", "position": "5248232", "ref": "T"}},
    {"_id": "chr11:g.5248232T>G", "chrom": "11", "vcf": {"alt": "G", "position": "5248232", "ref": "T"}},
]

VARIANT_DOC = {
    "_id": "chr7:g.140453136A>T",
    "chrom": "7",
    "vcf": {"alt": "T", "position": "140453136", "ref": "A"},
    "dbsnp": {"rsid": "rs113488022"},
    "observed": True,
    "clinvar": {
        "variant_id": 13961,
        "gene": {"symbol": "BRAF"},
        "rcv": [{"accession": "RCV000014992", "clinical_significance": "Pathogenic"}],
    },
}

FACETS = {
    "took": 2,
    "total": 99632,
    "hits": [],
    "facets": {
        "dbnsfp.polyphen2.hdiv.pred": {
            "_type": "terms",
            "terms": [{"count": 2108, "term": "b"}, {"count": 1653, "term": "d"}],
            "other": 0,
            "missing": 0,
            "total": 3761,
        },
    },
}


class TestSearchVariants:
    def test_shapes_the_response(self, mv_schema, mv_record):
        mv_record(SEARCH)
        result = mv_schema.myvariant_search_variants(gene="BRAF")
        assert result["total"] == 148
        assert result["hits_returned"] == 1
        assert result["assembly"] == "hg19"

    def test_adds_provenance_to_every_hit(self, mv_schema, mv_record):
        mv_record(SEARCH)
        hit = mv_schema.myvariant_search_variants(gene="BRAF")["hits"][0]
        assert hit["dbsnp_url"].endswith("/snp/rs113488022")
        assert hit["clinvar_url"].endswith("/variation/13961/")
        assert hit["myvariant_url"].endswith("/variant/chr7:g.140453136A>T")

    def test_requests_the_summary_fields_by_default(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF")
        assert set(mv_schema.SUMMARY_FIELDS) <= set(recorder.last.fields)

    def test_no_filters_is_rejected(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        with pytest.raises(mv_schema.ToolError, match="__all__"):
            mv_schema.myvariant_search_variants()
        assert recorder.calls == []

    def test_all_matches_everything(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(q="__all__")
        assert recorder.last.q == "__all__"

    def test_unknown_field_rejected_before_any_request(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_search_variants(gene="BRAF", fields="chrom,nosuchfield")
        assert recorder.calls == []

    def test_gene_becomes_an_or_clause_across_three_sources(self, mv_schema, mv_record):
        # SnpEff, dbNSFP, and ClinVar each record the gene name separately
        # and can disagree, so all three are searched.
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF")
        q = recorder.last.q
        assert 'snpeff.ann.genename:"BRAF"' in q
        assert 'dbnsfp.genename:"BRAF"' in q
        assert 'clinvar.gene.symbol:"BRAF"' in q

    def test_rsid_clause(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(rsid="rs334")
        assert recorder.last.q == 'dbsnp.rsid:"rs334"'

    def test_rsid_without_rs_prefix_is_rejected(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        with pytest.raises(mv_schema.ToolError, match="rs334"):
            mv_schema.myvariant_search_variants(rsid="334")
        assert recorder.calls == []

    def test_chrom_prefix_is_stripped(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(chrom="chr7", gene="BRAF")
        assert 'chrom:"7"' in recorder.last.q

    def test_clinical_significance_clause(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF", clinical_significance="Pathogenic")
        assert 'clinvar.rcv.clinical_significance:"Pathogenic"' in recorder.last.q

    def test_clauses_are_anded_together(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF", chrom="7", clinical_significance="Pathogenic")
        assert recorder.last.q.count(" AND ") == 2

    def test_bare_hgvs_q_is_auto_quoted(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        result = mv_schema.myvariant_search_variants(q="chr7:g.140453136A>T")
        assert recorder.last.q == '"chr7:g.140453136A>T"'
        assert any("HGVS" in n for n in result["notes"])

    def test_assembly_is_resolved_and_sent(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF", assembly="HG38")
        assert recorder.last.params["assembly"] == "hg38"

    def test_unknown_assembly_rejected_before_any_request(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_search_variants(gene="BRAF", assembly="hg18")
        assert recorder.calls == []

    def test_more_results_than_shown_is_reported(self, mv_schema, mv_record):
        mv_record(SEARCH)
        result = mv_schema.myvariant_search_variants(gene="BRAF", size=1)
        assert any("148 variants match and 1 are shown" in n for n in result["notes"])

    def test_note_reflects_the_post_budget_count_not_the_fetched_count(self, mv_schema, mv_record, monkeypatch):
        # Regression: the "N match and M are shown" note used to be built
        # before the response-size budget could trim hits further, so a
        # trimmed response could report a shown-count larger than what it
        # actually carried.
        monkeypatch.setattr(mv_schema, "MAX_RESPONSE_CHARS", 600)
        big = {"total": 50, "hits": [dict(BRAF_HIT, _id=f"chr7:g.{i}A>T") for i in range(50)]}
        mv_record(big)
        result = mv_schema.myvariant_search_variants(gene="BRAF", size=50)
        shown = result["hits_returned"]
        assert shown < 50
        assert any(f"and {shown} are shown" in n for n in result["notes"])
        assert not any("and 50 are shown" in n for n in result["notes"])

    def test_offset_past_the_end_is_explained(self, mv_schema, mv_record):
        mv_record({"took": 1, "total": 148, "hits": []})
        result = mv_schema.myvariant_search_variants(gene="BRAF", offset=500)
        assert result["hits_returned"] == 0
        assert any("past the end" in n for n in result["notes"])

    def test_size_is_clamped_and_reported(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        result = mv_schema.myvariant_search_variants(gene="BRAF", size=5000)
        assert recorder.last.params["size"] == mv_schema.MAX_SIZE
        assert any("1000" in n for n in result["notes"])

    def test_sort_is_passed_through(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_search_variants(gene="BRAF", sort="-cadd.phred")
        assert recorder.last.params["sort"] == "-cadd.phred"

    def test_multi_allelic_rsid_returns_every_allele(self, mv_schema, mv_record):
        mv_record({"took": 1, "total": 3, "hits": RS334_HITS})
        result = mv_schema.myvariant_search_variants(rsid="rs334")
        assert result["hits_returned"] == 3
        assert len({h["_id"] for h in result["hits"]}) == 3

    def test_echoes_the_api_call(self, mv_schema, mv_record):
        mv_record(SEARCH)
        call = mv_schema.myvariant_search_variants(gene="BRAF")["api_call"]
        assert call["method"] == "GET"
        assert call["url"].endswith("/query")


class TestGetVariant:
    def test_core_section_by_default(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T")
        assert result["sections"] == ["core"]
        assert recorder.last.path == "/variant/chr7:g.140453136A>T"
        assert "dbsnp.rsid" in recorder.last.fields

    def test_named_sections_select_their_fields(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="clinical,predictions")
        assert "clinvar" in recorder.last.fields
        assert "dbnsfp" in recorder.last.fields

    def test_civic_is_its_own_section_not_folded_into_clinical(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="clinical")
        assert "civic" not in recorder.last.fields
        assert "clinvar" in recorder.last.fields

    def test_sections_never_request_the_whole_document(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="all")
        assert "all" not in recorder.last.fields
        assert len(recorder.last.fields) > 10

    def test_ids_travel_with_every_section(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="predictions")
        assert {"chrom", "vcf", "dbsnp.rsid", "clinvar.variant_id"} <= set(recorder.last.fields)

    def test_unknown_section_lists_the_real_ones(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        with pytest.raises(mv_schema.ToolError) as exc:
            mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="clinicl")
        assert "clinical" in str(exc.value)
        assert recorder.calls == []

    def test_clinvar_rcv_is_trimmed(self, mv_schema, mv_record):
        doc = dict(VARIANT_DOC)
        doc["clinvar"] = {
            "variant_id": 1,
            "rcv": [{"accession": f"RCV{i}"} for i in range(40)],
        }
        mv_record(doc)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="clinical")
        assert len(result["variant"]["clinvar"]["rcv"]) == mv_schema.MAX_CLINVAR_RCV
        assert any("clinvar.rcv" in n for n in result["notes"])

    def test_civic_profiles_and_evidence_are_both_capped(self, mv_schema, mv_record):
        # Mirrors BRAF V600E: 15 profiles, the first alone holding 100
        # evidence items -- capping only the outer list is not enough.
        doc = dict(VARIANT_DOC)
        doc["civic"] = {
            "molecularProfiles": [
                {"name": f"p{i}", "evidenceItems": [{"id": j} for j in range(100)]}
                for i in range(15)
            ],
        }
        mv_record(doc)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="civic")
        profiles = result["variant"]["civic"]["molecularProfiles"]
        assert len(profiles) == mv_schema.MAX_CIVIC_PROFILES
        assert all(len(p["evidenceItems"]) == mv_schema.MAX_CIVIC_EVIDENCE_PER_PROFILE for p in profiles)
        assert any("civic.molecularProfiles" in n for n in result["notes"])

    def test_missing_observed_flag_is_flagged_as_cadd_only(self, mv_schema, mv_record):
        doc = {k: v for k, v in VARIANT_DOC.items() if k != "observed"}
        doc["cadd"] = {"phred": 9.68}
        mv_record(doc)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T", sections="predictions")
        assert any("CADD-precomputed" in n for n in result["notes"])

    def test_present_observed_flag_is_not_flagged(self, mv_schema, mv_record):
        mv_record(VARIANT_DOC)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T")
        assert not any("CADD-precomputed" in n for n in result["notes"])

    def test_absent_sections_are_reported(self, mv_schema, mv_record):
        mv_record(VARIANT_DOC)
        result = mv_schema.myvariant_get_variant(
            "chr7:g.140453136A>T", sections="core,population,other",
        )
        note = " ".join(result["notes"])
        assert "population" in note and "other" in note

    def test_still_oversized_after_trimming_says_so(self, mv_schema, mv_record, monkeypatch):
        # A document that is still too large after the list-trims run gets
        # returned whole with an honest note, rather than mangled further.
        monkeypatch.setattr(mv_schema, "MAX_RESPONSE_CHARS", 200)
        mv_record(VARIANT_DOC)
        result = mv_schema.myvariant_get_variant("chr7:g.140453136A>T")
        assert "truncated" in result
        assert "variant" in result  # returned whole, not stripped down to nothing

    def test_missing_variant_propagates_the_api_error(self, mv_schema, mv_record):
        mv_record({"code": 404, "success": False, "error": "Not Found."}, status=404)
        with pytest.raises(mv_schema.ToolError, match="404"):
            mv_schema.myvariant_get_variant("chr1:g.999999999A>T")

    def test_empty_id_rejected(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_get_variant("  ")
        assert recorder.calls == []

    def test_assembly_is_passed_through(self, mv_schema, mv_record):
        recorder = mv_record(VARIANT_DOC)
        mv_schema.myvariant_get_variant("chr7:g.140453136A>T", assembly="hg38")
        assert recorder.last.params["assembly"] == "hg38"


class TestMapIds:
    def test_maps_unique_ids(self, mv_schema, mv_record):
        mv_record([
            {"query": "rs113488022", **BRAF_HIT},
            {"query": "rs672", "_id": "chr17:g.41219625C>T", "chrom": "17"},
        ])
        result = mv_schema.myvariant_map_ids(["rs113488022", "rs672"])
        assert set(result["matches"]) == {"rs113488022", "rs672"}
        assert result["matched"] == 2

    def test_sends_q_as_a_json_array(self, mv_schema, mv_record):
        # Comma-joining would split any id that contains a comma.
        recorder = mv_record([{"query": "rs334", **BRAF_HIT}])
        mv_schema.myvariant_map_ids(["rs334"])
        assert recorder.last.body["q"] == ["rs334"]

    def test_repeated_ids_are_deduped_not_called_ambiguous(self, mv_schema, mv_record):
        recorder = mv_record([{"query": "rs334", **BRAF_HIT}])
        result = mv_schema.myvariant_map_ids(["rs334", "rs334", "rs334"])
        assert recorder.last.body["q"] == ["rs334"]
        assert result["ids_submitted"] == 1
        assert result["ambiguous"] == {}
        assert any("repeat" in n for n in result["notes"])

    def test_multi_allelic_rsid_is_reported_as_ambiguous(self, mv_schema, mv_record):
        # rs334 (sickle cell HBB) genuinely maps to three alleles.
        mv_record([{"query": "rs334", **hit} for hit in RS334_HITS])
        result = mv_schema.myvariant_map_ids(["rs334"])
        assert len(result["ambiguous"]["rs334"]) == 3
        assert result["matches"] == {}
        assert any("alternate alleles" in n for n in result["notes"])

    def test_unmatched_ids_are_listed(self, mv_schema, mv_record):
        mv_record([{"query": "rs334", **BRAF_HIT}, {"query": "rsBOGUS", "notfound": True}])
        result = mv_schema.myvariant_map_ids(["rs334", "rsBOGUS"])
        assert result["not_found"] == ["rsBOGUS"]
        assert any("matched nothing" in n for n in result["notes"])

    def test_notfound_note_mentions_merged_rsids(self, mv_schema, mv_record):
        mv_record([{"query": "rs1", "notfound": True}])
        result = mv_schema.myvariant_map_ids(["rs1"])
        assert any("dbsnp_merges" in n for n in result["notes"])

    def test_default_scope_is_dbsnp_rsid(self, mv_schema, mv_record):
        recorder = mv_record([{"query": "rs334", **BRAF_HIT}])
        mv_schema.myvariant_map_ids(["rs334"])
        assert recorder.last.body["scopes"] == "dbsnp.rsid"

    def test_transcript_hgvs_scope(self, mv_schema, mv_record):
        recorder = mv_record([{"query": "NM_007294.4:c.5074G>A", **BRAF_HIT}])
        mv_schema.myvariant_map_ids(["NM_007294.4:c.5074G>A"], from_type="clinvar.hgvs.coding")
        assert recorder.last.body["scopes"] == "clinvar.hgvs.coding"

    def test_scopes_are_validated_before_any_request(self, mv_schema, mv_record):
        recorder = mv_record([])
        with pytest.raises(mv_schema.ToolError, match="dbsnp.rsid"):
            mv_schema.myvariant_map_ids(["rs334"], from_type="dbsnp.rsidd")
        assert recorder.calls == []

    def test_scope_must_be_searchable(self, mv_schema, mv_record):
        recorder = mv_record([])
        with pytest.raises(mv_schema.ToolError, match="not indexed"):
            mv_schema.myvariant_map_ids(["rs334"], from_type="cadd.1000g.afr")
        assert recorder.calls == []

    def test_batch_is_capped_and_reported(self, mv_schema, mv_record):
        recorder = mv_record([])
        result = mv_schema.myvariant_map_ids([f"rs{i}" for i in range(1500)])
        assert len(recorder.last.body["q"]) == mv_schema.MAX_BATCH_IDS
        assert any("1000" in n for n in result["notes"])

    def test_empty_ids_rejected(self, mv_schema, mv_record):
        recorder = mv_record([])
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_map_ids(["", "   "])
        assert recorder.calls == []

    def test_a_single_object_response_is_accepted(self, mv_schema, mv_record):
        # The API answers a one-id POST with an object rather than a list.
        mv_record({"query": "rs334", **BRAF_HIT})
        assert mv_schema.myvariant_map_ids(["rs334"])["matched"] == 1

    def test_adds_provenance_to_matches(self, mv_schema, mv_record):
        mv_record([{"query": "rs113488022", **BRAF_HIT}])
        match = mv_schema.myvariant_map_ids(["rs113488022"])["matches"]["rs113488022"]
        assert match["dbsnp_url"].endswith("/snp/rs113488022")

    def test_assembly_is_passed_through_in_the_body(self, mv_schema, mv_record):
        recorder = mv_record([{"query": "rs334", **BRAF_HIT}])
        mv_schema.myvariant_map_ids(["rs334"], assembly="hg38")
        assert recorder.last.body["assembly"] == "hg38"

    def test_diagnostics_survive_a_trim(self, mv_schema, mv_record, monkeypatch):
        # not_found and ambiguous are what the caller has to act on, so the
        # matches are dropped first.
        monkeypatch.setattr(mv_schema, "MAX_RESPONSE_CHARS", 900)
        rows = [{"query": f"rs{i}", "_id": f"chr1:g.{i}A>T", "chrom": "1"} for i in range(60)]
        rows.append({"query": "rsNOPE", "notfound": True})
        mv_record(rows)
        result = mv_schema.myvariant_map_ids([f"rs{i}" for i in range(60)] + ["rsNOPE"])
        assert result["not_found"] == ["rsNOPE"]
        assert result["truncated"]
        assert result["matched"] == len(result["matches"]) + len(result["ambiguous"])


class TestFacetCounts:
    def test_shapes_the_terms(self, mv_schema, mv_record):
        mv_record(FACETS)
        result = mv_schema.myvariant_facet_counts("dbnsfp.polyphen2.hdiv.pred", q='snpeff.ann.genename:"BRAF"')
        assert result["terms"][0] == {"value": "b", "count": 2108}
        assert result["values_returned"] == 2
        assert result["total_matching_variants"] == 99632

    def test_requests_no_hits(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        mv_schema.myvariant_facet_counts("dbnsfp.genename")
        assert recorder.last.params["size"] == 0
        assert recorder.last.params["facets"] == "dbnsfp.genename"

    def test_defaults_to_the_whole_index(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        mv_schema.myvariant_facet_counts("dbnsfp.genename")
        assert recorder.last.q == "__all__"

    def test_values_outside_the_page_are_reported_using_the_final_count(self, mv_schema, mv_record):
        facets = json.loads(json.dumps(FACETS))
        facets["facets"]["dbnsfp.polyphen2.hdiv.pred"]["other"] = 500
        mv_record(facets)
        result = mv_schema.myvariant_facet_counts("dbnsfp.polyphen2.hdiv.pred")
        assert result["variants_outside_returned_values"] == 500
        assert any(f"outside the {result['values_returned']} values shown" in n for n in result["notes"])

    def test_outside_values_note_reflects_post_budget_count(self, mv_schema, mv_record, monkeypatch):
        # Regression: this note used to cite the term count fetched before
        # the response-size budget could trim it further.
        monkeypatch.setattr(mv_schema, "MAX_RESPONSE_CHARS", 700)
        facets = {
            "took": 1, "total": 100000, "hits": [],
            "facets": {
                "dbnsfp.genename": {
                    "terms": [{"count": 1, "term": f"gene{i}"} for i in range(60)],
                    "other": 999,
                    "missing": 0,
                },
            },
        }
        mv_record(facets)
        result = mv_schema.myvariant_facet_counts("dbnsfp.genename", facet_size=60)
        shown = result["values_returned"]
        assert shown < 60
        assert any(f"outside the {shown} values shown" in n for n in result["notes"])
        assert not any("outside the 60 values shown" in n for n in result["notes"])

    def test_genes_missing_the_field_are_reported(self, mv_schema, mv_record):
        facets = json.loads(json.dumps(FACETS))
        facets["facets"]["dbnsfp.polyphen2.hdiv.pred"]["missing"] = 7
        mv_record(facets)
        result = mv_schema.myvariant_facet_counts("dbnsfp.polyphen2.hdiv.pred")
        assert result["variants_missing_field"] == 7
        assert any("no value" in n for n in result["notes"])

    def test_empty_facet_over_a_nested_field_is_explained(self, mv_schema, mv_record):
        # A facetable field can still come back with no values when it sits
        # under a nested object the facet API cannot aggregate over.
        mv_record({"total": 148, "hits": [], "facets": {"dbnsfp.genename": {"terms": []}}})
        result = mv_schema.myvariant_facet_counts("dbnsfp.genename", q="chrom:7")
        assert result["terms"] == []
        assert any("nested" in n for n in result["notes"])

    def test_non_facetable_field_rejected_before_any_request(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_facet_counts("clinvar.rcv.clinical_significance")
        assert recorder.calls == []

    def test_facet_size_is_clamped(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        mv_schema.myvariant_facet_counts("dbnsfp.genename", facet_size=99999)
        assert recorder.last.params["facet_size"] == mv_schema.MAX_FACET_SIZE

    def test_empty_field_rejected(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        with pytest.raises(mv_schema.ToolError):
            mv_schema.myvariant_facet_counts("  ")
        assert recorder.calls == []

    def test_assembly_is_passed_through(self, mv_schema, mv_record):
        recorder = mv_record(FACETS)
        mv_schema.myvariant_facet_counts("dbnsfp.genename", assembly="hg38")
        assert recorder.last.params["assembly"] == "hg38"


class TestRawQuery:
    def test_returns_the_response_unshaped(self, mv_schema, mv_record):
        mv_record(SEARCH)
        result = mv_schema.myvariant_raw_query("chrom:7")
        assert result["response"]["max_score"] == 35.6
        assert result["response"]["hits"][0] == BRAF_HIT

    def test_does_not_validate_field_names(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_raw_query("__all__", fields="nosuchfield")
        assert recorder.last.params["fields"] == "nosuchfield"

    def test_does_not_auto_quote_hgvs(self, mv_schema, mv_record):
        # Unlike myvariant_search_variants, this is the raw escape hatch: a
        # bare HGVS id is sent exactly as given, colon and all.
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_raw_query("chr7:g.140453136A>T")
        assert recorder.last.q == "chr7:g.140453136A>T"

    def test_trims_nested_hits_and_says_so_at_the_top(self, mv_schema, mv_record, monkeypatch):
        monkeypatch.setattr(mv_schema, "MAX_RESPONSE_CHARS", 3000)
        big = {"total": 50, "hits": [{"_id": str(i), "summary": "s" * 80} for i in range(50)]}
        mv_record(big)
        result = mv_schema.myvariant_raw_query("__all__", fields="all", size=50)
        assert len(result["response"]["hits"]) < 50
        assert "truncated" in result
        assert len(json.dumps(result)) <= 3000

    def test_empty_query_rejected(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        with pytest.raises(mv_schema.ToolError, match="__all__"):
            mv_schema.myvariant_raw_query("")
        assert recorder.calls == []

    def test_assembly_is_passed_through(self, mv_schema, mv_record):
        recorder = mv_record(SEARCH)
        mv_schema.myvariant_raw_query("chrom:7", assembly="hg38")
        assert recorder.last.params["assembly"] == "hg38"


class TestDescribeFields:
    def test_filters_by_substring(self, mv_schema):
        result = mv_schema.myvariant_describe_fields("clinvar.hgvs")
        names = [f["name"] for f in result["fields"]]
        assert "clinvar.hgvs.coding" in names
        assert "chrom" not in names
        assert result["fields_total"] > result["fields_matched"]

    def test_reports_facetability_per_field(self, mv_schema):
        result = mv_schema.myvariant_describe_fields("")
        by_name = {f["name"]: f for f in result["fields"]}
        assert by_name["dbsnp.rsid"]["facetable"] is True
        assert by_name["clinvar.rcv.clinical_significance"]["facetable"] is False
        assert by_name["cadd.1000g.afr"]["facetable"] is False

    def test_lists_the_assemblies_and_id_formats(self, mv_schema):
        result = mv_schema.myvariant_describe_fields()
        assert set(result["assemblies"]) == {"hg19", "hg38"}
        assert "genomic HGVS" in result["id_format"]
        assert "transcript HGVS" in result["id_format"]

    def test_explains_the_observed_flag_caveat(self, mv_schema):
        notes = " ".join(mv_schema.myvariant_describe_fields()["notes"])
        assert "CADD" in notes

    def test_no_match_is_not_an_error(self, mv_schema):
        result = mv_schema.myvariant_describe_fields("zzzz")
        assert result["fields"] == []
        assert result["fields_matched"] == 0
