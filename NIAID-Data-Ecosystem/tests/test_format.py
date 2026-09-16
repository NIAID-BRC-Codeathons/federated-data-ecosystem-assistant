"""Unit tests for response shaping. No network access.

Fixtures below are trimmed from real NDE API responses.
"""

from __future__ import annotations

from nde_mcp.format import (
    clean_detail,
    describe_sources,
    download_urls,
    format_facets,
    measured_finding,
    format_search_response,
    names_of,
    summarize_hit,
    truncate,
)

SRA_HIT = {
    "_id": "ncbi_sra_srp425935",
    "_score": 15.558278,
    "@type": "Dataset",
    "name": "Whole genome sequencing of influenza A virus",
    "description": "The project is to reconstruct the transmission patterns.",
    "url": "https://www.ncbi.nlm.nih.gov/sra/SRP425935",
    "date": "2023-09-30",
    "datePublished": "2023-09-30",
    "includedInDataCatalog": {
        "@type": "DataCatalog",
        "name": "NCBI SRA",
        "url": "https://www.ncbi.nlm.nih.gov/sra/",
        "archivedAt": "https://www.ncbi.nlm.nih.gov/sra/SRP425935",
    },
    "author": [{"@type": "Person", "name": "Nagasaki University"}],
    "infectiousAgent": [
        {"@type": "DefinedTerm", "name": "Influenza A virus", "alternateName": ["FLUAV"]}
    ],
    "measurementTechnique": [{"@type": "DefinedTerm", "name": "whole genome sequencing assay"}],
    "ibmGraniteEmbeddingTextHash": "sha256:deadbeef",
    "ibmGraniteEmbeddingModel": "ibm-granite/granite-embedding-125m-english",
}


class TestNamesOf:
    def test_bare_string(self):
        assert names_of("RNA-seq") == ["RNA-seq"]

    def test_list_of_strings(self):
        assert names_of(["a", "b"]) == ["a", "b"]

    def test_defined_term_dict(self):
        assert names_of({"@type": "DefinedTerm", "name": "Influenza A virus"}) == ["Influenza A virus"]

    def test_list_of_defined_terms(self):
        value = [{"name": "a"}, {"name": "b"}]
        assert names_of(value) == ["a", "b"]

    def test_display_name_fallback(self):
        assert names_of({"displayName": "Sample"}) == ["Sample"]

    def test_deduplicates_case_insensitively(self):
        assert names_of(["RNA-seq", "rna-seq", "WGS"]) == ["RNA-seq", "WGS"]

    def test_respects_limit(self):
        assert len(names_of([{"name": str(i)} for i in range(50)], limit=5)) == 5

    def test_handles_none_and_empty(self):
        assert names_of(None) == []
        assert names_of([]) == []
        assert names_of({}) == []

    def test_skips_blank_strings(self):
        assert names_of(["", "  ", "real"]) == ["real"]


class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello", 100) == "hello"

    def test_long_text_gets_ellipsis(self):
        out = truncate("x" * 500, 100)
        assert len(out) == 100
        assert out.endswith("…")

    def test_whitespace_collapsed(self):
        assert truncate("a\n\n  b", 100) == "a b"

    def test_non_string_is_none(self):
        assert truncate(None, 10) is None
        assert truncate(42, 10) is None


class TestSummarizeHit:
    def test_core_fields(self):
        out = summarize_hit(SRA_HIT)
        assert out["id"] == "ncbi_sra_srp425935"
        assert out["type"] == "Dataset"
        assert out["name"].startswith("Whole genome sequencing")
        assert out["score"] == 15.5583

    def test_repository_flattened(self):
        out = summarize_hit(SRA_HIT)
        assert out["repository"]["name"] == "NCBI SRA"
        assert out["repository"]["record_url"].endswith("SRP425935")

    def test_provenance_links_present(self):
        links = summarize_hit(SRA_HIT)["links"]
        assert links["source_url"].startswith("https://www.ncbi.nlm.nih.gov")
        assert "ncbi_sra_srp425935" in links["portal_url"]

    def test_defined_terms_flattened_to_names(self):
        out = summarize_hit(SRA_HIT)
        assert out["pathogens"] == ["Influenza A virus"]
        assert out["measurement_techniques"] == ["whole genome sequencing assay"]
        assert out["authors"] == ["Nagasaki University"]

    def test_embeddings_are_dropped(self):
        out = summarize_hit(SRA_HIT)
        assert not any("Embedding" in k or "ibm" in k.lower() for k in out)

    def test_empty_annotation_keys_omitted(self):
        # Fields with no value should be absent rather than present-and-empty.
        out = summarize_hit(SRA_HIT)
        assert "health_conditions" not in out
        assert "programming_languages" not in out

    def test_repository_as_list(self):
        hit = dict(SRA_HIT, includedInDataCatalog=[{"name": "Zenodo", "url": "https://zenodo.org"}])
        assert summarize_hit(hit)["repository"]["name"] == "Zenodo"

    def test_minimal_hit_does_not_crash(self):
        out = summarize_hit({"_id": "x"})
        assert out["id"] == "x"


class TestDownloadUrls:
    """A DataDownload has no `name`, only `contentUrl` -- the generic name
    extractor misses it, so these guard the dedicated path."""

    def test_content_url_from_list(self):
        dist = [{"@type": "DataDownload", "contentUrl": "https://ex.org/f.csv", "encodingFormat": "CSV"}]
        assert download_urls(dist) == ["https://ex.org/f.csv"]

    def test_content_url_from_single_dict(self):
        assert download_urls({"@type": "DataDownload", "contentUrl": "https://ex.org/g"}) == ["https://ex.org/g"]

    def test_bare_string_accepted(self):
        assert download_urls(["https://ex.org/h"]) == ["https://ex.org/h"]

    def test_url_and_download_url_fallbacks(self):
        assert download_urls([{"url": "https://ex.org/u"}]) == ["https://ex.org/u"]
        assert download_urls([{"downloadUrl": "https://ex.org/d"}]) == ["https://ex.org/d"]

    def test_entry_without_any_url_is_skipped(self):
        assert download_urls([{"@type": "DataDownload"}]) == []

    def test_none_and_empty(self):
        assert download_urls(None) == []
        assert download_urls([]) == []

    def test_duplicates_removed(self):
        dist = [{"contentUrl": "a"}, {"contentUrl": "a"}, {"contentUrl": "b"}]
        assert download_urls(dist) == ["a", "b"]

    def test_limit_respected(self):
        dist = [{"contentUrl": f"u{i}"} for i in range(20)]
        assert len(download_urls(dist, limit=3)) == 3

    def test_surfaces_in_summary_links(self):
        hit = dict(SRA_HIT, distribution=[{"@type": "DataDownload", "contentUrl": "https://ex.org/f.csv"}])
        assert summarize_hit(hit)["links"]["downloads"] == ["https://ex.org/f.csv"]

    def test_absent_when_no_download(self):
        # The fixture's distribution is missing entirely.
        assert "downloads" not in summarize_hit(SRA_HIT)["links"]


class TestMeasuredFinding:
    """`Inference` records (Expression Atlas, staging deployment) carry their
    result in value/unitText/marginOfError -- dropping those would discard the
    entire point of the record."""

    INFERENCE = {
        "_id": "gxa_e_geod_41293_ensmusg00000061731",
        "@type": "Inference",
        "name": "Ext1 is upregulated in 'osteosarcoma' vs 'control'",
        "value": 2.7,
        "unitText": "Log2 fold change",
        "measurementQualifier": "'osteosarcoma' vs 'control'",
        "marginOfError": {"@type": "QuantitativeValue", "name": "adjusted p-value", "value": 1.85e-13},
        "observationAbout": {"@type": "DefinedTerm", "name": "Ext1", "identifier": "ENSMUSG00000061731"},
        "measuredProperty": {"@type": "Property", "name": "Gene Expression"},
        "observationType": {"@type": "DefinedTerm", "name": "differential gene expression ratio"},
        "subjectOf": {"@type": "Dataset", "identifier": ["E-GEOD-41293", "other"]},
    }

    def test_extracts_value_and_unit(self):
        out = measured_finding(self.INFERENCE)
        assert out["value"] == 2.7
        assert out["unit"] == "Log2 fold change"

    def test_extracts_subject_gene_and_id(self):
        out = measured_finding(self.INFERENCE)
        assert out["about"] == "Ext1"
        assert out["about_id"] == "ENSMUSG00000061731"

    def test_extracts_significance(self):
        out = measured_finding(self.INFERENCE)
        assert out["margin_of_error"] == {"name": "adjusted p-value", "value": 1.85e-13}

    def test_extracts_comparison_and_source_study(self):
        out = measured_finding(self.INFERENCE)
        assert out["comparison"] == "'osteosarcoma' vs 'control'"
        # subjectOf.identifier is a list; the first entry is the study accession.
        assert out["from_study"] == "E-GEOD-41293"

    def test_surfaces_in_summary(self):
        assert summarize_hit(self.INFERENCE)["finding"]["value"] == 2.7

    def test_absent_for_ordinary_records(self):
        # A Dataset has no value/observationAbout -- no empty `finding` key.
        assert measured_finding(SRA_HIT) is None
        assert "finding" not in summarize_hit(SRA_HIT)

    def test_zero_value_is_kept(self):
        # 0.0 is a real fold change, not a missing value.
        out = measured_finding({"value": 0.0, "observationAbout": {"name": "G"}})
        assert out["value"] == 0.0

    def test_value_without_subject_still_reported(self):
        assert measured_finding({"value": 1.5})["value"] == 1.5

    def test_identifier_equal_to_name_not_duplicated(self):
        out = measured_finding({"observationAbout": {"name": "X", "identifier": "X"}})
        assert "about_id" not in out


class TestCleanDetail:
    def test_strips_embedding_and_scoring_fields(self):
        out = clean_detail(SRA_HIT)
        assert "ibmGraniteEmbeddingTextHash" not in out
        assert "ibmGraniteEmbeddingModel" not in out
        assert "_score" not in out

    def test_keeps_substantive_fields(self):
        out = clean_detail(SRA_HIT)
        assert out["name"] == SRA_HIT["name"]
        assert out["includedInDataCatalog"]["name"] == "NCBI SRA"

    def test_adds_portal_url(self):
        assert "ncbi_sra_srp425935" in clean_detail(SRA_HIT)["_portal_url"]


class TestFormatSearchResponse:
    def test_counts_and_echo(self):
        payload = {"total": 100, "hits": [SRA_HIT]}
        out = format_search_response(payload, query="flu", size=1, offset=0)
        assert out["total"] == 100
        assert out["returned"] == 1
        assert out["query"] == "flu"

    def test_next_offset_when_more_remain(self):
        payload = {"total": 100, "hits": [SRA_HIT]}
        out = format_search_response(payload, query="flu", size=1, offset=0)
        assert out["next_offset"] == 1

    def test_no_next_offset_on_last_page(self):
        payload = {"total": 1, "hits": [SRA_HIT]}
        out = format_search_response(payload, query="flu", size=1, offset=0)
        assert "next_offset" not in out

    def test_paging_note_at_window_edge(self):
        payload = {"total": 50_000, "hits": [SRA_HIT] * 10}
        out = format_search_response(payload, query="flu", size=10, offset=9_995)
        assert "next_offset" not in out
        assert "10,000" in out["paging_note"]

    def test_hint_on_zero_results(self):
        out = format_search_response({"total": 0, "hits": []}, query="zzz", size=10, offset=0)
        assert "hint" in out
        assert out["results"] == []


class TestFormatFacets:
    def test_flattens_terms(self):
        facets = {
            "@type": {
                "_type": "terms",
                "terms": [{"term": "Dataset", "count": 5}, {"term": "Sample", "count": 3}],
                "other": 1,
                "missing": 2,
                "total": 8,
            }
        }
        out = format_facets(facets)
        assert out["@type"]["values"] == [
            {"value": "Dataset", "count": 5},
            {"value": "Sample", "count": 3},
        ]
        assert out["@type"]["other_count"] == 1
        assert out["@type"]["missing_count"] == 2

    def test_empty_facets(self):
        assert format_facets({}) == {}


class TestDescribeSources:
    METADATA = {
        "src": {
            "zenodo": {
                "stats": {"zenodo": 649929},
                "version": "2026-08-21",
                "sourceInfo": {"name": "Zenodo", "description": "General repository", "url": "https://zenodo.org"},
            },
            "ncbi_sra": {
                "stats": {"ncbi_sra": 658408},
                "sourceInfo": {"name": "NCBI SRA", "description": "Sequence Read Archive"},
            },
        }
    }

    def test_sorted_by_record_count_desc(self):
        rows = describe_sources(self.METADATA)
        assert [r["name"] for r in rows] == ["NCBI SRA", "Zenodo"]

    def test_counts_summed_from_stats(self):
        rows = describe_sources(self.METADATA)
        assert rows[0]["record_count"] == 658408

    def test_name_filter_is_case_insensitive(self):
        rows = describe_sources(self.METADATA, name_filter="zeno")
        assert len(rows) == 1
        assert rows[0]["name"] == "Zenodo"

    def test_filter_matches_description_too(self):
        rows = describe_sources(self.METADATA, name_filter="Sequence Read")
        assert [r["name"] for r in rows] == ["NCBI SRA"]

    def test_empty_metadata(self):
        assert describe_sources({}) == []
