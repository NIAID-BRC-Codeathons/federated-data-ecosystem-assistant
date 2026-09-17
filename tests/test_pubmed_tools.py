"""Tool-level tests against a recorded transport. No network access.

Each test asserts on both sides of a tool: the request it generated, and the
response it shaped. The request side matters as much as the response, because
a filter PubMed silently ignores returns plausible but wrong data.
"""

from __future__ import annotations

import pytest

from test_pubmed_helpers import ARTICLE_XML, EMPTY_ARTICLE_SET_XML, UNINDEXED_ARTICLE_XML

SEARCH = {
    "esearchresult": {
        "count": "148", "retmax": "2", "retstart": "0",
        "idlist": ["33668216", "42748471"],
        "querytranslation": '"BRAF"[All Fields]',
    },
}

SUMMARY = {
    "result": {
        "uids": ["33668216", "42748471"],
        "33668216": {
            "title": "A test article title.", "source": "Viruses", "pubdate": "2021 Feb 24",
            "authors": [{"name": "Zarski LM"}],
            "articleids": [{"idtype": "doi", "value": "10.3390/v13030356"}, {"idtype": "pmc", "value": "PMC7995974"}],
        },
        "42748471": {
            "title": "Spitz tumours.", "source": "Clin Exp Dermatol", "pubdate": "2026 Sep 16",
            "authors": [{"name": "Aydemir AT"}],
            "articleids": [{"idtype": "doi", "value": "10.1093/ced/llag404"}],
        },
    },
}

EINFO = {
    "einforesult": {
        "dbinfo": [{
            "fieldlist": [
                {"name": "ALL", "fullname": "All Fields", "description": "All terms."},
                {"name": "MESH", "fullname": "MeSH Terms", "description": "Medical Subject Headings."},
                {"name": "AUTH", "fullname": "Author", "description": "Author name."},
                {"name": "TITL", "fullname": "Title", "description": "Words in title."},
            ],
        }],
    },
}

ELINK_CITEDIN = {
    "linksets": [{
        "dbfrom": "pubmed", "ids": ["33668216"],
        "linksetdbs": [{"dbto": "pubmed", "linkname": "pubmed_pubmed_citedin", "links": ["41961066", "41745915"]}],
    }],
}

ELINK_NO_LINKS = {"linksets": [{"dbfrom": "pubmed", "ids": ["33668216"]}]}


class TestDescribeSearch:
    def test_filters_by_substring(self, pubmed, pm_record):
        pm_record(EINFO)
        result = pubmed.pubmed_describe_search("mesh")
        names = [f["tag"] for f in result["fields"]]
        assert names == ["MESH"]
        assert result["fields_total"] == 4

    def test_no_filter_lists_everything(self, pubmed, pm_record):
        pm_record(EINFO)
        result = pubmed.pubmed_describe_search()
        assert result["fields_matched"] == 4

    def test_matches_full_name_too(self, pubmed, pm_record):
        pm_record(EINFO)
        result = pubmed.pubmed_describe_search("author")
        assert any(f["tag"] == "AUTH" for f in result["fields"])

    def test_fields_are_cached_across_calls(self, pubmed, pm_record):
        recorder = pm_record(EINFO)
        pubmed.pubmed_describe_search("mesh")
        pubmed.pubmed_describe_search("author")
        assert len(recorder.calls) == 1

    def test_mentions_the_current_rate_limit(self, pubmed, pm_record, monkeypatch):
        pm_record(EINFO)
        monkeypatch.setattr(pubmed, "API_KEY", "")
        notes = " ".join(pubmed.pubmed_describe_search()["notes"])
        assert "3 requests/second" in notes

    def test_mentions_the_higher_rate_limit_with_a_key(self, pubmed, pm_record, monkeypatch):
        pm_record(EINFO)
        monkeypatch.setattr(pubmed, "API_KEY", "secret")
        notes = " ".join(pubmed.pubmed_describe_search()["notes"])
        assert "10 requests/second" in notes


class TestSearchArticles:
    def test_shapes_the_response(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        result = pubmed.pubmed_search_articles(q="BRAF")
        assert result["total"] == 148
        assert result["hits_returned"] == 2
        assert recorder.endpoints == ["esearch", "esummary"]

    def test_adds_provenance_to_every_hit(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        hit = pubmed.pubmed_search_articles(q="BRAF")["hits"][0]
        assert hit["links"]["pubmed_url"].endswith("/33668216/")
        assert hit["links"]["doi_url"].endswith("10.3390/v13030356")
        assert hit["links"]["pmc_url"].endswith("PMC7995974/")

    def test_no_filters_is_rejected(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        with pytest.raises(pubmed.ToolError, match="At least one"):
            pubmed.pubmed_search_articles()
        assert recorder.calls == []

    def test_author_becomes_a_field_qualified_clause(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        pubmed.pubmed_search_articles(author="Smith J")
        assert recorder.calls[0].term == "Smith J[Author]"

    def test_journal_mesh_and_publication_type_clauses(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        pubmed.pubmed_search_articles(journal="Nature", mesh_term="Melanoma", publication_type="Review")
        term = recorder.calls[0].term
        assert '"Nature"[Journal]' in term
        assert '"Melanoma"[MeSH Terms]' in term
        assert '"Review"[Publication Type]' in term

    def test_clauses_are_anded_together(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        pubmed.pubmed_search_articles(q="cancer", author="Smith J")
        assert recorder.calls[0].term == "cancer AND Smith J[Author]"

    def test_date_range_uses_esearchs_native_params_not_the_term(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        pubmed.pubmed_search_articles(q="cancer", date_from="2024/01/01", date_to="2024/12/31")
        params = recorder.calls[0].params
        assert params["datetype"] == "pdat"
        assert params["mindate"] == "2024/01/01"
        assert params["maxdate"] == "2024/12/31"
        # The term itself carries no date syntax; the date is a separate param.
        assert "2024" not in recorder.calls[0].term

    def test_bad_sort_value_rejected_before_any_request(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        with pytest.raises(pubmed.ToolError, match="date"):
            pubmed.pubmed_search_articles(q="cancer", sort="date")
        assert recorder.calls == []

    def test_summary_error_entries_are_dropped_from_hits(self, pubmed, pm_record):
        search = {"esearchresult": {"count": "1", "idlist": ["999999999999"]}}
        summary = {"result": {"uids": ["999999999999"], "999999999999": {"error": "cannot get document summary"}}}
        pm_record(lambda url, kw, n: search if n == 1 else summary)
        result = pubmed.pubmed_search_articles(q="cancer")
        assert result["hits"] == []

    def test_size_is_clamped_and_reported(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        result = pubmed.pubmed_search_articles(q="cancer", size=5000)
        assert recorder.calls[0].params["retmax"] == pubmed.MAX_SEARCH_SIZE
        assert any("200" in n for n in result["notes"])

    def test_offset_is_clamped_to_max_retstart(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        pubmed.pubmed_search_articles(q="cancer", offset=99999)
        assert recorder.calls[0].params["retstart"] == pubmed.MAX_RETSTART

    def test_more_results_than_shown_is_reported(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        result = pubmed.pubmed_search_articles(q="BRAF", size=2)
        assert any("148 articles match and 2 are shown" in n for n in result["notes"])

    def test_note_reflects_the_post_budget_count_not_the_fetched_count(self, pubmed, pm_record, monkeypatch):
        # Same class of bug fixed in mygene.py and myvariant.py: this note
        # has to be built from the count that survives the response-size
        # trim, not the count fetched before it.
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 700)
        big_search = {"esearchresult": {"count": "50", "idlist": [str(i) for i in range(50)]}}
        big_summary = {
            "result": {
                "uids": [str(i) for i in range(50)],
                **{str(i): {"title": f"Title number {i} is fairly long for padding purposes"} for i in range(50)},
            },
        }
        pm_record(lambda url, kw, n: big_search if n == 1 else big_summary)
        result = pubmed.pubmed_search_articles(q="cancer", size=50)
        shown = result["hits_returned"]
        assert shown < 50
        assert any(f"and {shown} are shown" in n for n in result["notes"])
        assert not any("and 50 are shown" in n for n in result["notes"])

    def test_offset_past_the_end_is_explained(self, pubmed, pm_record):
        empty_search = {"esearchresult": {"count": "148", "idlist": []}}
        pm_record(lambda url, kw, n: empty_search if n == 1 else {"result": {"uids": []}})
        result = pubmed.pubmed_search_articles(q="BRAF", offset=500)
        assert result["hits_returned"] == 0
        assert any("past the end" in n for n in result["notes"])

    def test_parser_warnings_are_surfaced(self, pubmed, pm_record):
        search = dict(SEARCH)
        search["esearchresult"] = {**SEARCH["esearchresult"], "warninglist": {"outputmessages": ["("]}}
        pm_record(lambda url, kw, n: search if n == 1 else SUMMARY)
        result = pubmed.pubmed_search_articles(q="cancer AND (unbalanced")
        assert any("adjusted or ignored" in n for n in result["notes"])

    def test_echoes_the_api_call(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: SEARCH if n == 1 else SUMMARY)
        call = pubmed.pubmed_search_articles(q="BRAF")["api_call"]
        assert call["url"].endswith("esearch.fcgi")
        assert call["params"]["term"] == "BRAF"


class TestGetArticle:
    def test_core_and_abstract_by_default(self, pubmed, pm_record):
        recorder = pm_record(ARTICLE_XML)
        result = pubmed.pubmed_get_article("33668216")
        assert result["sections"] == ["core", "abstract"]
        assert result["title"] == "A test article title."
        assert result["abstract"].startswith("BACKGROUND:")
        assert "mesh_terms" not in result
        assert recorder.last.params["id"] == "33668216"

    def test_mesh_section_is_opt_in(self, pubmed, pm_record):
        pm_record(ARTICLE_XML)
        result = pubmed.pubmed_get_article("33668216", sections="mesh")
        assert "mesh_terms" in result
        assert "abstract" not in result

    def test_unknown_section_lists_the_real_ones(self, pubmed, pm_record):
        recorder = pm_record(ARTICLE_XML)
        with pytest.raises(pubmed.ToolError) as exc:
            pubmed.pubmed_get_article("33668216", sections="abstrct")
        assert "core, abstract, mesh" in str(exc.value)
        assert recorder.calls == []

    def test_empty_pmid_rejected(self, pubmed, pm_record):
        recorder = pm_record(ARTICLE_XML)
        with pytest.raises(pubmed.ToolError):
            pubmed.pubmed_get_article("  ")
        assert recorder.calls == []

    def test_unresolvable_pmid_raises_a_clear_error(self, pubmed, pm_record):
        pm_record(EMPTY_ARTICLE_SET_XML)
        with pytest.raises(pubmed.ToolError, match="No PubMed record found"):
            pubmed.pubmed_get_article("999999999999")

    def test_missing_abstract_is_noted(self, pubmed, pm_record):
        pm_record(UNINDEXED_ARTICLE_XML)
        result = pubmed.pubmed_get_article("42750775", sections="core,abstract")
        assert any("no abstract" in n for n in result["notes"])

    def test_missing_mesh_terms_explains_the_indexing_lag(self, pubmed, pm_record):
        # Regression for the trap this tool exists to surface: a same-day
        # article has no MeSH terms yet, not because none apply.
        pm_record(UNINDEXED_ARTICLE_XML)
        result = pubmed.pubmed_get_article("42750775", sections="mesh")
        assert any("not been indexed yet" in n for n in result["notes"])

    def test_core_section_reports_indexing_status(self, pubmed, pm_record):
        pm_record(UNINDEXED_ARTICLE_XML)
        result = pubmed.pubmed_get_article("42750775", sections="core")
        assert result["indexing_status"] == "Publisher"


class TestGetSummaries:
    def test_maps_unique_ids(self, pubmed, pm_record):
        pm_record(SUMMARY)
        result = pubmed.pubmed_get_summaries(["33668216", "42748471"])
        assert set(result["summaries"]) == {"33668216", "42748471"}
        assert result["found"] == 2

    def test_repeated_ids_are_deduped(self, pubmed, pm_record):
        recorder = pm_record({"result": {"uids": ["33668216"], "33668216": SUMMARY["result"]["33668216"]}})
        result = pubmed.pubmed_get_summaries(["33668216", "33668216", "33668216"])
        assert recorder.last.params["id"] == "33668216"
        assert result["ids_submitted"] == 1
        assert any("repeat" in n for n in result["notes"])

    def test_error_entries_become_not_found(self, pubmed, pm_record):
        pm_record({"result": {"uids": ["1"], "1": {"error": "cannot get document summary"}}})
        result = pubmed.pubmed_get_summaries(["1"])
        assert result["not_found"] == ["1"]
        assert result["summaries"] == {}

    def test_missing_uid_also_becomes_not_found(self, pubmed, pm_record):
        # The uid is not even in "uids", not merely an error entry.
        pm_record({"result": {"uids": []}})
        result = pubmed.pubmed_get_summaries(["1"])
        assert result["not_found"] == ["1"]

    def test_batch_is_capped_and_reported(self, pubmed, pm_record):
        recorder = pm_record({"result": {"uids": []}})
        result = pubmed.pubmed_get_summaries([str(i) for i in range(500)])
        assert len(recorder.last.params["id"].split(",")) == pubmed.MAX_BATCH_IDS
        assert any("200" in n for n in result["notes"])

    def test_empty_ids_rejected(self, pubmed, pm_record):
        recorder = pm_record({})
        with pytest.raises(pubmed.ToolError):
            pubmed.pubmed_get_summaries(["", "   "])
        assert recorder.calls == []

    def test_adds_provenance_to_found_summaries(self, pubmed, pm_record):
        pm_record(SUMMARY)
        summary = pubmed.pubmed_get_summaries(["33668216"])["summaries"]["33668216"]
        assert summary["links"]["doi_url"].endswith("10.3390/v13030356")

    def test_diagnostics_survive_a_trim(self, pubmed, pm_record, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 800)
        uids = [str(i) for i in range(60)]
        summary = {
            "result": {
                "uids": uids + ["nope"],
                **{u: {"title": f"A reasonably long title for padding number {u}"} for u in uids},
                "nope": {"error": "cannot get document summary"},
            },
        }
        pm_record(summary)
        result = pubmed.pubmed_get_summaries(uids + ["nope"])
        assert result["not_found"] == ["nope"]
        assert result["truncated"]
        assert result["found"] == len(result["summaries"])


class TestRelatedArticles:
    def test_shapes_the_response(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: ELINK_CITEDIN if n == 1 else SUMMARY)
        result = pubmed.pubmed_related_articles("33668216", relationship="citing")
        assert result["related_count"] == 2
        assert result["related_articles"][0]["pmid"] == "41961066"

    def test_uses_the_right_linkname_per_relationship(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: ELINK_CITEDIN if n == 1 else {"result": {"uids": []}})
        pubmed.pubmed_related_articles("33668216", relationship="references")
        assert recorder.calls[0].params["linkname"] == "pubmed_pubmed_refs"

    def test_similar_uses_the_curated_top_five_linkname(self, pubmed, pm_record):
        recorder = pm_record(lambda url, kw, n: ELINK_CITEDIN if n == 1 else {"result": {"uids": []}})
        pubmed.pubmed_related_articles("33668216", relationship="similar")
        assert recorder.calls[0].params["linkname"] == "pubmed_pubmed_five"

    def test_citing_carries_the_pmc_only_coverage_caveat(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: ELINK_CITEDIN if n == 1 else SUMMARY)
        result = pubmed.pubmed_related_articles("33668216", relationship="citing")
        assert any("PMC's own full-text index" in n for n in result["notes"])

    def test_other_relationships_do_not_carry_the_pmc_caveat(self, pubmed, pm_record):
        pm_record(lambda url, kw, n: ELINK_CITEDIN if n == 1 else SUMMARY)
        result = pubmed.pubmed_related_articles("33668216", relationship="similar")
        assert not any("PMC" in n for n in result["notes"])

    def test_no_links_found_is_not_an_error(self, pubmed, pm_record):
        pm_record(ELINK_NO_LINKS)
        result = pubmed.pubmed_related_articles("33668216", relationship="references")
        assert result["related_pmids"] == []
        assert any("No 'references' links" in n for n in result["notes"])

    def test_empty_pmid_rejected(self, pubmed, pm_record):
        recorder = pm_record(ELINK_CITEDIN)
        with pytest.raises(pubmed.ToolError):
            pubmed.pubmed_related_articles("  ")
        assert recorder.calls == []

    def test_size_is_clamped_and_reported(self, pubmed, pm_record):
        many_links = {
            "linksets": [{
                "linksetdbs": [{"linkname": "pubmed_pubmed_citedin", "links": [str(i) for i in range(300)]}],
            }],
        }
        pm_record(lambda url, kw, n: many_links if n == 1 else {"result": {"uids": []}})
        result = pubmed.pubmed_related_articles("33668216", relationship="citing", size=1000)
        assert result["related_count"] == pubmed.MAX_SEARCH_SIZE
        assert any(str(pubmed.MAX_SEARCH_SIZE) in n for n in result["notes"])

    def test_more_links_than_shown_is_reported(self, pubmed, pm_record):
        many_links = {
            "linksets": [{
                "linksetdbs": [{"linkname": "pubmed_pubmed_citedin", "links": [str(i) for i in range(50)]}],
            }],
        }
        pm_record(lambda url, kw, n: many_links if n == 1 else {"result": {"uids": []}})
        result = pubmed.pubmed_related_articles("33668216", relationship="citing", size=10)
        assert result["related_count"] == 10
        assert any("More than 10" in n for n in result["notes"])


class TestRawSearch:
    def test_returns_the_response_unshaped(self, pubmed, pm_record):
        pm_record(SEARCH)
        result = pubmed.pubmed_raw_search("BRAF")
        assert result["response"]["count"] == "148"
        assert result["response"]["idlist"] == ["33668216", "42748471"]

    def test_bad_sort_rejected_before_any_request(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        with pytest.raises(pubmed.ToolError, match="date"):
            pubmed.pubmed_raw_search("cancer", sort="date")
        assert recorder.calls == []

    def test_empty_term_rejected(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        with pytest.raises(pubmed.ToolError, match="term is required"):
            pubmed.pubmed_raw_search("")
        assert recorder.calls == []

    def test_retmax_over_ceiling_is_clamped_and_reported(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        result = pubmed.pubmed_raw_search("cancer", retmax=50000)
        assert recorder.last.params["retmax"] == pubmed.MAX_RESULT_WINDOW
        assert any("9999" in n for n in result["notes"])

    def test_retstart_clamped_to_the_lower_ceiling(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        pubmed.pubmed_raw_search("cancer", retstart=99999)
        assert recorder.last.params["retstart"] == pubmed.MAX_RETSTART

    def test_date_params_are_passed_through(self, pubmed, pm_record):
        recorder = pm_record(SEARCH)
        pubmed.pubmed_raw_search("cancer", datetype="pdat", mindate="2024/01/01", maxdate="2024/12/31")
        params = recorder.last.params
        assert params["datetype"] == "pdat"
        assert params["mindate"] == "2024/01/01"
        assert params["maxdate"] == "2024/12/31"

    def test_does_not_enrich_hits(self, pubmed, pm_record):
        # Unlike pubmed_search_articles, this is the raw escape hatch: bare
        # ids, no title/author enrichment, no second esummary call.
        recorder = pm_record(SEARCH)
        pubmed.pubmed_raw_search("BRAF")
        assert recorder.endpoints == ["esearch"]

    def test_echoes_the_api_call(self, pubmed, pm_record):
        pm_record(SEARCH)
        call = pubmed.pubmed_raw_search("BRAF")["api_call"]
        assert call["url"].endswith("esearch.fcgi")
        assert call["params"]["term"] == "BRAF"
