"""Tool-level tests against a recorded transport. No network access.

Each test asserts on both sides of a tool: the request it generated, and the
response it shaped. The request side matters as much as the response, because
a filter the API silently ignores returns plausible but wrong data.
"""

from __future__ import annotations

import json

import pytest

HIT = {
    "_id": "1017",
    "_score": 138.74754,
    "symbol": "CDK2",
    "name": "cyclin dependent kinase 2",
    "taxid": 9606,
    "entrezgene": "1017",
    "ensembl": {"gene": "ENSG00000123374"},
    "type_of_gene": "protein-coding",
}

MOUSE_HIT = {**HIT, "_id": "12566", "symbol": "Cdk2", "taxid": 10090, "entrezgene": "12566"}

SEARCH = {"took": 3, "total": 16, "max_score": 138.74754, "hits": [HIT]}

GENE = {
    "_id": "1017",
    "symbol": "CDK2",
    "name": "cyclin dependent kinase 2",
    "taxid": 9606,
    "entrezgene": "1017",
    "ensembl": {"gene": "ENSG00000123374"},
    "summary": "This gene encodes a member of a family of serine/threonine kinases.",
    "go": {"MF": [{"id": "GO:0004693", "term": "cyclin-dependent kinase activity"}]},
}

FACETS = {
    "took": 2,
    "total": 2428,
    "hits": [],
    "facets": {
        "taxid": {
            "_type": "terms",
            "terms": [{"count": 33, "term": 246437}, {"count": 16, "term": 9606}],
            "other": 2358,
            "missing": 0,
            "total": 49,
        }
    },
}


class TestSearchGenes:
    def test_shapes_the_response(self, schema, record):
        record(SEARCH)
        result = schema.mygene_search_genes("CDK2", species="human")
        assert result["total"] == 16
        assert result["hits_returned"] == 1
        assert result["species"] == "human"
        assert result["taxid_counts"] == {9606: 1}

    def test_adds_provenance_to_every_hit(self, schema, record):
        record(SEARCH)
        hit = schema.mygene_search_genes("CDK2", species="human")["hits"][0]
        assert hit["ncbi_gene_url"].endswith("/gene/1017")
        assert hit["ensembl_gene_url"].endswith("g=ENSG00000123374")
        assert hit["mygene_url"].endswith("/gene/1017")

    def test_requests_the_summary_fields_by_default(self, schema, record):
        recorder = record(SEARCH)
        schema.mygene_search_genes("CDK2", species="human")
        assert set(schema.SUMMARY_FIELDS) <= set(recorder.last.fields)

    def test_keeps_the_ids_provenance_needs(self, schema, record):
        # Asking only for the symbol must still return the ids, or the links
        # cannot be built.
        recorder = record(SEARCH)
        schema.mygene_search_genes("CDK2", species="human", fields="symbol")
        assert {"entrezgene", "ensembl.gene", "taxid"} <= set(recorder.last.fields)

    def test_empty_query_is_rejected(self, schema, record):
        record(SEARCH)
        with pytest.raises(schema.ToolError, match="__all__"):
            schema.mygene_search_genes("   ")

    def test_unknown_field_rejected_before_any_request(self, schema, record):
        recorder = record(SEARCH)
        with pytest.raises(schema.ToolError):
            schema.mygene_search_genes("CDK2", fields="symbol,nosuchfield")
        assert recorder.calls == []

    def test_gene_type_becomes_a_grouped_clause(self, schema, record):
        # Without the parentheses, `AND type_of_gene:x` would bind only to the
        # trailing token of a multi-word query.
        recorder = record(SEARCH)
        schema.mygene_search_genes("cyclin kinase", species="human", type_of_gene="ncrna")
        assert recorder.last.q == "(cyclin kinase) AND type_of_gene:ncrna"

    def test_unknown_gene_type_lists_the_real_ones(self, schema, record):
        recorder = record(SEARCH)
        with pytest.raises(schema.ToolError, match="protein-coding"):
            schema.mygene_search_genes("CDK2", type_of_gene="coding")
        assert recorder.calls == []

    def test_species_is_resolved_and_sent(self, schema, record):
        recorder = record(SEARCH)
        schema.mygene_search_genes("CDK2", species="Human, mouse")
        assert recorder.last.params["species"] == "human,mouse"

    def test_unscoped_search_warns_about_species(self, schema, record):
        record({**SEARCH, "total": 2428, "hits": [HIT, MOUSE_HIT]})
        result = schema.mygene_search_genes("CDK2")
        assert result["species"] == "all species"
        assert any("all species" in n for n in result["notes"])

    def test_more_results_than_shown_is_reported(self, schema, record):
        record(SEARCH)
        result = schema.mygene_search_genes("CDK2", species="human", size=1)
        assert any("16 genes match" in n for n in result["notes"])

    def test_note_reflects_the_post_budget_count_not_the_fetched_count(self, schema, record, monkeypatch):
        # Regression: the "N match and M are shown" note used to be built
        # before the response-size budget could trim hits further, so a
        # trimmed response could report a shown-count larger than what it
        # actually carried.
        monkeypatch.setattr(schema, "MAX_RESPONSE_CHARS", 600)
        big = {"total": 50, "hits": [dict(HIT, _id=f"gene{i}") for i in range(50)]}
        record(big)
        result = schema.mygene_search_genes("CDK2", species="human", size=50)
        shown = result["hits_returned"]
        assert shown < 50
        assert any(f"and {shown} are shown" in n for n in result["notes"])
        assert not any("and 50 are shown" in n for n in result["notes"])

    def test_offset_past_the_end_is_explained(self, schema, record):
        # Zero hits with a non-zero total otherwise looks like a failed query.
        record({"took": 1, "total": 16, "hits": []})
        result = schema.mygene_search_genes("CDK2", species="human", offset=500)
        assert result["hits_returned"] == 0
        assert any("past the end" in n for n in result["notes"])

    def test_size_is_clamped_and_reported(self, schema, record):
        recorder = record(SEARCH)
        result = schema.mygene_search_genes("CDK2", species="human", size=5000)
        assert recorder.last.params["size"] == schema.MAX_SIZE
        assert any("1000" in n for n in result["notes"])

    def test_sort_is_passed_through(self, schema, record):
        recorder = record(SEARCH)
        schema.mygene_search_genes("kinase", species="human", sort="symbol")
        assert recorder.last.params["sort"] == "symbol"

    def test_echoes_the_api_call(self, schema, record):
        record(SEARCH)
        call = schema.mygene_search_genes("CDK2", species="human")["api_call"]
        assert call["method"] == "GET"
        assert call["url"].endswith("/query")
        assert call["params"]["q"] == "CDK2"


class TestGetGene:
    def test_core_section_by_default(self, schema, record):
        recorder = record(GENE)
        result = schema.mygene_get_gene("1017")
        assert result["sections"] == ["core"]
        assert recorder.last.path == "/gene/1017"
        assert "summary" in recorder.last.fields

    def test_named_sections_select_their_fields(self, schema, record):
        recorder = record(GENE)
        schema.mygene_get_gene("1017", sections="go,pathway")
        assert "go" in recorder.last.fields
        assert "pathway" in recorder.last.fields

    def test_sections_never_request_the_whole_document(self, schema, record):
        # A full document is 100 KB, so "all" must still be an explicit field
        # list rather than fields=all.
        recorder = record(GENE)
        schema.mygene_get_gene("1017", sections="all")
        assert "all" not in recorder.last.fields
        assert len(recorder.last.fields) > 10

    def test_ids_travel_with_every_section(self, schema, record):
        recorder = record(GENE)
        schema.mygene_get_gene("1017", sections="go")
        assert {"symbol", "taxid", "entrezgene", "ensembl.gene"} <= set(recorder.last.fields)

    def test_unknown_section_lists_the_real_ones(self, schema, record):
        recorder = record(GENE)
        with pytest.raises(schema.ToolError) as exc:
            schema.mygene_get_gene("1017", sections="corre")
        assert "core" in str(exc.value)
        assert recorder.calls == []

    def test_long_generif_is_trimmed(self, schema, record):
        record({**GENE, "generif": [{"pubmed": i, "text": "t"} for i in range(370)]})
        result = schema.mygene_get_gene("1017", sections="literature")
        assert len(result["gene"]["generif"]) == schema.MAX_GENERIF
        assert any("370" in n for n in result["notes"])

    def test_absent_sections_are_reported(self, schema, record):
        # Coverage is uneven, so an empty section is worth saying out loud
        # rather than leaving the agent to infer the gene has no pathways.
        record(GENE)
        result = schema.mygene_get_gene("1017", sections="core,pathway,homologs")
        note = " ".join(result["notes"])
        assert "pathway" in note and "homologs" in note

    def test_missing_gene_propagates_the_api_error(self, schema, record):
        record({"code": 404, "success": False, "error": "Not Found."}, status=404)
        with pytest.raises(schema.ToolError, match="404"):
            schema.mygene_get_gene("999999999")

    def test_empty_id_rejected(self, schema, record):
        recorder = record(GENE)
        with pytest.raises(schema.ToolError):
            schema.mygene_get_gene("  ")
        assert recorder.calls == []


class TestMapIds:
    def test_maps_unique_ids(self, schema, record):
        record([
            {"query": "CDK2", **HIT},
            {"query": "BRCA1", "_id": "672", "symbol": "BRCA1", "taxid": 9606, "entrezgene": "672"},
        ])
        result = schema.mygene_map_ids(["CDK2", "BRCA1"])
        assert set(result["matches"]) == {"CDK2", "BRCA1"}
        assert result["matches"]["CDK2"]["entrezgene"] == "1017"
        assert result["matched"] == 2

    def test_sends_q_as_a_json_array(self, schema, record):
        # Comma-joining would split any id that contains a comma, which a gene
        # name can.
        recorder = record([{"query": "CDK2", **HIT}])
        schema.mygene_map_ids(["CDK2"])
        assert recorder.last.body["q"] == ["CDK2"]

    def test_repeated_ids_are_deduped_not_called_ambiguous(self, schema, record):
        # Each copy of an id comes back as its own row, which otherwise looks
        # like one id matching several genes.
        recorder = record([{"query": "CDK2", **HIT}])
        result = schema.mygene_map_ids(["CDK2", "CDK2", "CDK2"])
        assert recorder.last.body["q"] == ["CDK2"]
        assert result["ids_submitted"] == 1
        assert result["ambiguous"] == {}
        assert any("repeat" in n for n in result["notes"])

    def test_genuine_ambiguity_is_reported(self, schema, record):
        # CDKN2 is an alias of both CDK2 and CDKN2A.
        record([
            {"query": "CDKN2", **HIT},
            {"query": "CDKN2", "_id": "1029", "symbol": "CDKN2A", "taxid": 9606, "entrezgene": "1029"},
        ])
        result = schema.mygene_map_ids(["CDKN2"], from_type="symbol,alias")
        assert len(result["ambiguous"]["CDKN2"]) == 2
        assert result["matches"] == {}
        assert any("more than one gene" in n for n in result["notes"])

    def test_unmatched_ids_are_listed(self, schema, record):
        record([{"query": "CDK2", **HIT}, {"query": "NOPE", "notfound": True}])
        result = schema.mygene_map_ids(["CDK2", "NOPE"])
        assert result["not_found"] == ["NOPE"]
        assert any("matched nothing" in n for n in result["notes"])

    def test_notfound_note_suggests_the_alias_fallback(self, schema, record):
        record([{"query": "CDKN2", "notfound": True}])
        result = schema.mygene_map_ids(["CDKN2"])
        assert any("symbol,alias" in n for n in result["notes"])

    def test_defaults_to_human(self, schema, record):
        # Unscoped mapping makes almost every symbol ambiguous across
        # orthologs, so this tool scopes by default where search does not.
        recorder = record([{"query": "CDK2", **HIT}])
        schema.mygene_map_ids(["CDK2"])
        assert recorder.last.body["species"] == "human"

    def test_all_species_is_available(self, schema, record):
        recorder = record([{"query": "CDK2", **HIT}])
        schema.mygene_map_ids(["CDK2"], species="all")
        assert recorder.last.body["species"] == "all"

    def test_scopes_are_validated_before_any_request(self, schema, record):
        recorder = record([])
        with pytest.raises(schema.ToolError, match="symbol"):
            schema.mygene_map_ids(["CDK2"], from_type="symbl")
        assert recorder.calls == []

    def test_scope_must_be_searchable(self, schema, record):
        recorder = record([])
        with pytest.raises(schema.ToolError, match="not indexed"):
            schema.mygene_map_ids(["CDK2"], from_type="map_location")
        assert recorder.calls == []

    def test_batch_is_capped_and_reported(self, schema, record):
        recorder = record([])
        result = schema.mygene_map_ids([f"GENE{i}" for i in range(1500)])
        assert len(recorder.last.body["q"]) == schema.MAX_BATCH_IDS
        assert any("1000" in n for n in result["notes"])

    def test_empty_ids_rejected(self, schema, record):
        recorder = record([])
        with pytest.raises(schema.ToolError):
            schema.mygene_map_ids(["", "   "])
        assert recorder.calls == []

    def test_a_single_object_response_is_accepted(self, schema, record):
        # The API answers a one-id POST with an object rather than a list.
        record({"query": "CDK2", **HIT})
        assert schema.mygene_map_ids(["CDK2"])["matched"] == 1

    def test_adds_provenance_to_matches(self, schema, record):
        record([{"query": "CDK2", **HIT}])
        match = schema.mygene_map_ids(["CDK2"])["matches"]["CDK2"]
        assert match["ncbi_gene_url"].endswith("/gene/1017")

    def test_diagnostics_survive_a_trim(self, schema, record, monkeypatch):
        # not_found and ambiguous are what the caller has to act on, so the
        # matches are dropped first.
        monkeypatch.setattr(schema, "MAX_RESPONSE_CHARS", 900)
        rows = [{"query": f"GENE{i}", "_id": str(i), "symbol": f"GENE{i}", "taxid": 9606}
                for i in range(60)]
        rows.append({"query": "NOPE", "notfound": True})
        record(rows)
        result = schema.mygene_map_ids([f"GENE{i}" for i in range(60)] + ["NOPE"])
        assert result["not_found"] == ["NOPE"]
        assert result["truncated"]
        assert result["matched"] == len(result["matches"]) + len(result["ambiguous"])


class TestFacetCounts:
    def test_shapes_the_terms(self, schema, record):
        record(FACETS)
        result = schema.mygene_facet_counts("taxid", q="CDK2")
        assert result["terms"][0] == {"value": 246437, "count": 33}
        assert result["values_returned"] == 2
        assert result["total_matching_genes"] == 2428

    def test_requests_no_hits(self, schema, record):
        recorder = record(FACETS)
        schema.mygene_facet_counts("taxid")
        assert recorder.last.params["size"] == 0
        assert recorder.last.params["facets"] == "taxid"

    def test_defaults_to_the_whole_index(self, schema, record):
        recorder = record(FACETS)
        schema.mygene_facet_counts("taxid")
        assert recorder.last.q == "__all__"

    def test_values_outside_the_page_are_reported(self, schema, record):
        record(FACETS)
        result = schema.mygene_facet_counts("taxid", q="CDK2")
        assert result["genes_outside_returned_values"] == 2358
        assert any("2358" in n for n in result["notes"])

    def test_outside_values_note_reflects_post_budget_count(self, schema, record, monkeypatch):
        # Regression: this note used to cite the term count fetched before
        # the response-size budget could trim it further.
        monkeypatch.setattr(schema, "MAX_RESPONSE_CHARS", 700)
        facets = {
            "took": 1, "total": 100000, "hits": [],
            "facets": {
                "taxid": {
                    "terms": [{"count": 1, "term": 100000 + i} for i in range(60)],
                    "other": 999,
                    "missing": 0,
                },
            },
        }
        record(facets)
        result = schema.mygene_facet_counts("taxid", facet_size=60)
        shown = result["values_returned"]
        assert shown < 60
        assert any(f"outside the {shown} values shown" in n for n in result["notes"])
        assert not any("outside the 60 values shown" in n for n in result["notes"])

    def test_genes_missing_the_field_are_reported(self, schema, record):
        facets = json.loads(json.dumps(FACETS))
        facets["facets"]["taxid"]["missing"] = 7
        record(facets)
        result = schema.mygene_facet_counts("taxid")
        assert result["genes_missing_field"] == 7
        assert any("no value" in n for n in result["notes"])

    def test_taxid_gets_an_explanation(self, schema, record):
        record(FACETS)
        result = schema.mygene_facet_counts("taxid")
        assert any("9606 is human" in n for n in result["notes"])

    def test_empty_facet_over_a_nested_field_is_explained(self, schema, record):
        # genomic_pos is a nested object, so aggregating genomic_pos.chr
        # returns no values rather than an error.
        record({"total": 1721, "hits": [], "facets": {"genomic_pos.chr": {"terms": []}}})
        result = schema.mygene_facet_counts("genomic_pos.chr", q="kinase")
        assert result["terms"] == []
        assert any("nested" in n for n in result["notes"])

    def test_non_facetable_field_rejected_before_any_request(self, schema, record):
        recorder = record(FACETS)
        with pytest.raises(schema.ToolError):
            schema.mygene_facet_counts("go.MF.term")
        assert recorder.calls == []

    def test_facet_size_is_clamped(self, schema, record):
        recorder = record(FACETS)
        schema.mygene_facet_counts("taxid", facet_size=99999)
        assert recorder.last.params["facet_size"] == schema.MAX_FACET_SIZE

    def test_empty_field_rejected(self, schema, record):
        recorder = record(FACETS)
        with pytest.raises(schema.ToolError):
            schema.mygene_facet_counts("  ")
        assert recorder.calls == []


class TestRawQuery:
    def test_returns_the_response_unshaped(self, schema, record):
        record(SEARCH)
        result = schema.mygene_raw_query("symbol:CDK2")
        assert result["response"]["max_score"] == 138.74754
        assert result["response"]["hits"][0] == HIT

    def test_does_not_validate_field_names(self, schema, record):
        # The escape hatch passes Lucene through, typos included.
        recorder = record(SEARCH)
        schema.mygene_raw_query("__all__", fields="nosuchfield")
        assert recorder.last.params["fields"] == "nosuchfield"

    def test_trims_nested_hits_and_says_so_at_the_top(self, schema, record, monkeypatch):
        monkeypatch.setattr(schema, "MAX_RESPONSE_CHARS", 3000)
        big = {"total": 50, "hits": [{"_id": str(i), "summary": "s" * 80} for i in range(50)]}
        record(big)
        result = schema.mygene_raw_query("__all__", fields="all", size=50)
        assert len(result["response"]["hits"]) < 50
        assert "truncated" in result
        # The note explaining the trim fits inside the budget too.
        assert len(json.dumps(result)) <= 3000

    def test_empty_query_rejected(self, schema, record):
        recorder = record(SEARCH)
        with pytest.raises(schema.ToolError, match="__all__"):
            schema.mygene_raw_query("")
        assert recorder.calls == []


class TestDescribeFields:
    def test_filters_by_substring(self, schema):
        result = schema.mygene_describe_fields("go.MF")
        names = [f["name"] for f in result["fields"]]
        assert "go.MF.id" in names
        assert "symbol" not in names
        assert result["fields_total"] > result["fields_matched"]

    def test_reports_facetability_per_field(self, schema):
        result = schema.mygene_describe_fields("go.MF")
        by_name = {f["name"]: f for f in result["fields"]}
        assert by_name["go.MF.id"]["facetable"] is True
        assert by_name["go.MF.term"]["facetable"] is False
        assert by_name["go.MF.pubmed"]["facetable"] is False

    def test_lists_the_species_and_gene_types(self, schema):
        result = schema.mygene_describe_fields()
        assert result["species_names"]["human"] == 9606
        assert "protein-coding" in result["type_of_gene_values"]

    def test_explains_the_ensembl_only_id(self, schema):
        notes = " ".join(schema.mygene_describe_fields()["notes"])
        assert "Ensembl" in notes

    def test_no_match_is_not_an_error(self, schema):
        result = schema.mygene_describe_fields("zzzz")
        assert result["fields"] == []
        assert result["fields_matched"] == 0
