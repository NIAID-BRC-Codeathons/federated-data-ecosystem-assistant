"""Unit tests for the PubMed server's pure helpers. No network access."""

from __future__ import annotations

import json

import pytest
import requests

# A minimal but real-shaped PubmedArticle, built from the fields the parsers
# actually read. Mirrors chr7:g.140453136A>T / PMID 33668216's structure,
# trimmed to what the tests need.
ARTICLE_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation Status="MEDLINE">
    <Article>
      <Journal>
        <JournalIssue>
          <PubDate><Year>2021</Year><Month>Feb</Month><Day>24</Day></PubDate>
        </JournalIssue>
        <Title>Viruses</Title>
        <ISOAbbreviation>Viruses</ISOAbbreviation>
      </Journal>
      <ArticleTitle>A test article title.</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Background text.</AbstractText>
        <AbstractText Label="METHODS">Methods text.</AbstractText>
      </Abstract>
      <AuthorList>
        <Author><LastName>Zarski</LastName><ForeName>Lila M</ForeName></Author>
        <Author><CollectiveName>Some Consortium</CollectiveName></Author>
      </AuthorList>
    </Article>
    <MeshHeadingList>
      <MeshHeading>
        <DescriptorName UI="D000818" MajorTopicYN="N">Animals</DescriptorName>
      </MeshHeading>
      <MeshHeading>
        <DescriptorName UI="D006566" MajorTopicYN="Y">Herpesviridae Infections</DescriptorName>
        <QualifierName MajorTopicYN="N">virology</QualifierName>
      </MeshHeading>
    </MeshHeadingList>
    <PublicationTypeList>
      <PublicationType>Journal Article</PublicationType>
    </PublicationTypeList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">33668216</ArticleId>
      <ArticleId IdType="doi">10.3390/v13030356</ArticleId>
      <ArticleId IdType="pmc">PMC7995974</ArticleId>
    </ArticleIdList>
    <ReferenceList>
      <Reference>
        <ArticleIdList>
          <ArticleId IdType="pubmed">11111111</ArticleId>
          <ArticleId IdType="doi">10.0000/not-this-articles-doi</ArticleId>
        </ArticleIdList>
      </Reference>
    </ReferenceList>
  </PubmedData>
</PubmedArticle>
</PubmedArticleSet>
"""

# No MeSH terms, no abstract, no author list, no PubmedData ids: the shape of
# a freshly published, not-yet-indexed record, per the live "Status=Publisher"
# check.
UNINDEXED_ARTICLE_XML = """<?xml version="1.0"?>
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation Status="Publisher">
    <Article>
      <Journal><JournalIssue><PubDate><Year>2026</Year><Month>Oct</Month></PubDate></JournalIssue></Journal>
      <ArticleTitle>A brand new article.</ArticleTitle>
    </Article>
  </MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="pubmed">42750775</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
</PubmedArticleSet>
"""

# efetch's actual behavior on an id it cannot resolve, in XML mode: HTTP 200
# with a well-formed but empty PubmedArticleSet, not a parse error and not
# the near-blank text stub that rettype=abstract&retmode=text returns
# instead. Verified live against a made-up PMID.
EMPTY_ARTICLE_SET_XML = '<?xml version="1.0" ?>\n<PubmedArticleSet></PubmedArticleSet>'


def _article(xml: str):
    import xml.etree.ElementTree as ET

    return ET.fromstring(xml).find(".//PubmedArticle")


class TestThrottle:
    """_throttle uses a fake clock here; time.sleep is recorded, not real."""

    def test_sleeps_when_called_too_soon(self, pubmed, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(pubmed.time, "monotonic", lambda: clock[0])
        sleeps = []
        monkeypatch.setattr(pubmed.time, "sleep", lambda s: sleeps.append(s))
        pubmed._last_call = 100.0
        pubmed._throttle()
        assert sleeps == [pytest.approx(pubmed._MIN_INTERVAL)]

    def test_no_sleep_once_the_interval_has_passed(self, pubmed, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(pubmed.time, "monotonic", lambda: clock[0])
        sleeps = []
        monkeypatch.setattr(pubmed.time, "sleep", lambda s: sleeps.append(s))
        pubmed._last_call = 100.0 - pubmed._MIN_INTERVAL - 1
        pubmed._throttle()
        assert sleeps == []

    def test_updates_last_call_after_running(self, pubmed, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr(pubmed.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(pubmed.time, "sleep", lambda s: None)
        pubmed._throttle()
        assert pubmed._last_call == 100.0


class TestCleanParams:
    def test_drops_empty_values(self, pubmed):
        out = pubmed._clean_params({"a": None, "b": "", "c": [], "d": "keep"})
        assert out["d"] == "keep"
        assert "a" not in out and "b" not in out and "c" not in out

    def test_always_adds_tool_name(self, pubmed):
        assert pubmed._clean_params({})["tool"] == pubmed.TOOL_NAME

    def test_adds_email_only_when_set(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "CONTACT_EMAIL", "")
        assert "email" not in pubmed._clean_params({})
        monkeypatch.setattr(pubmed, "CONTACT_EMAIL", "a@b.com")
        assert pubmed._clean_params({})["email"] == "a@b.com"

    def test_adds_api_key_only_when_set(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "API_KEY", "")
        assert "api_key" not in pubmed._clean_params({})
        monkeypatch.setattr(pubmed, "API_KEY", "secret")
        assert pubmed._clean_params({})["api_key"] == "secret"


class TestRequest:
    def test_json_success(self, pubmed, pm_record):
        pm_record({"esearchresult": {"count": "1"}})
        assert pubmed._request("esearch", {"term": "x"})["esearchresult"]["count"] == "1"

    def test_xml_mode_returns_raw_text(self, pubmed, pm_record):
        pm_record(ARTICLE_XML)
        assert pubmed._request("efetch", {"id": "1"}, retmode="xml") == ARTICLE_XML

    def test_tolerates_a_literal_control_character_in_a_string_value(self, pubmed, pm_record):
        # Verified live: NCBI's own error body for a retstart past the
        # ceiling contains an unescaped "\n" inside a JSON string, which
        # resp.json() (strict by default) rejects outright.
        malformed = '{"esearchresult": {"ERROR": "line one\nline two"}}'
        pm_record(malformed)
        result = pubmed._request("esearch", {"term": "x"})
        assert "line one" in result["esearchresult"]["ERROR"]

    def test_genuinely_broken_body_still_raises(self, pubmed, pm_record):
        pm_record("not json at all {{{")
        with pytest.raises(pubmed.ToolError, match="non-JSON"):
            pubmed._request("esearch", {"term": "x"})

    def test_http_error_is_reported_with_status(self, pubmed, pm_record):
        pm_record({"error": "boom"}, status=500)
        with pytest.raises(pubmed.ToolError, match="500"):
            pubmed._request("esearch", {"term": "x"})

    def test_429_retries_then_succeeds(self, pubmed, pm_record):
        def flaky(url, kwargs, call_number):
            if call_number < 3:
                return {"error": "rate limited"}, 429
            return {"esearchresult": {"count": "1"}}

        recorder = pm_record(flaky)
        result = pubmed._request("esearch", {"term": "x"})
        assert result["esearchresult"]["count"] == "1"
        assert len(recorder.calls) == 3

    def test_429_exhausts_retries_and_raises(self, pubmed, pm_record):
        pm_record({"error": "rate limited"}, status=429)
        with pytest.raises(pubmed.ToolError, match="429"):
            pubmed._request("esearch", {"term": "x"})

    def test_timeout_is_actionable(self, pubmed, monkeypatch):
        def timeout(*args, **kwargs):
            raise requests.Timeout("too slow")

        monkeypatch.setattr(pubmed.requests, "get", timeout)
        with pytest.raises(pubmed.ToolError, match="timed out"):
            pubmed._request("esearch", {"term": "x"})

    def test_connection_error_names_the_url(self, pubmed, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(pubmed.requests, "get", boom)
        with pytest.raises(pubmed.ToolError, match="Could not reach PubMed"):
            pubmed._request("esearch", {"term": "x"})

    def test_sends_the_identifying_params(self, pubmed, pm_record):
        recorder = pm_record({"esearchresult": {"count": "0"}})
        pubmed._request("esearch", {"term": "x"})
        assert recorder.last.params["tool"] == pubmed.TOOL_NAME


class TestEsearch:
    def test_returns_esearchresult(self, pubmed, pm_record):
        pm_record({"esearchresult": {"count": "3", "idlist": ["1", "2", "3"]}})
        assert pubmed._esearch("cancer")["count"] == "3"

    def test_missing_esearchresult_key_raises(self, pubmed, pm_record):
        pm_record({"header": {}})
        with pytest.raises(pubmed.ToolError, match="rejected"):
            pubmed._esearch("cancer")

    def test_error_nested_inside_esearchresult_raises(self, pubmed, pm_record):
        # Regression: this was checked at the wrong nesting level
        # ("ERROR" in payload rather than "ERROR" in result), so a rejected
        # query read as a plain zero-result search instead of raising.
        pm_record({"esearchresult": {"ERROR": "retstart cannot be larger than 9998"}})
        with pytest.raises(pubmed.ToolError, match="retstart"):
            pubmed._esearch("cancer", retstart=99999)

    def test_error_message_is_the_error_text_not_the_whole_payload(self, pubmed, pm_record):
        pm_record({"esearchresult": {"ERROR": "a specific reason"}})
        with pytest.raises(pubmed.ToolError) as exc:
            pubmed._esearch("cancer")
        assert str(exc.value).strip().endswith("a specific reason")


class TestEsummary:
    def test_empty_list_makes_no_request(self, pubmed, pm_record):
        recorder = pm_record({})
        assert pubmed._esummary([]) == {}
        assert recorder.calls == []

    def test_keys_by_uid_in_order(self, pubmed, pm_record):
        pm_record({"result": {"uids": ["1", "2"], "1": {"title": "A"}, "2": {"title": "B"}}})
        result = pubmed._esummary(["1", "2"])
        assert result == {"1": {"title": "A"}, "2": {"title": "B"}}

    def test_error_entries_pass_through_unfiltered(self, pubmed, pm_record):
        # _esummary itself does not filter a per-uid error; callers do.
        pm_record({"result": {"uids": ["1"], "1": {"error": "cannot get document summary"}}})
        result = pubmed._esummary(["1"])
        assert "error" in result["1"]

    def test_joins_ids_with_commas(self, pubmed, pm_record):
        recorder = pm_record({"result": {"uids": []}})
        pubmed._esummary(["1", "2", "3"])
        assert recorder.last.params["id"] == "1,2,3"


class TestEfetchXml:
    def test_empty_list_makes_no_request(self, pubmed, pm_record):
        recorder = pm_record("")
        assert pubmed._efetch_xml([]) == []
        assert recorder.calls == []

    def test_returns_the_pubmedarticle_elements(self, pubmed, pm_record):
        pm_record(ARTICLE_XML)
        articles = pubmed._efetch_xml(["33668216"])
        assert len(articles) == 1

    def test_empty_article_set_for_an_unresolvable_id_returns_no_elements(self, pubmed, pm_record):
        # Verified live: efetch on a made-up PMID answers HTTP 200 with this
        # well-formed but empty body, not an error. No PubmedArticle means
        # the id did not resolve, and pubmed_get_article treats an empty
        # list as not found.
        pm_record(EMPTY_ARTICLE_SET_XML)
        assert pubmed._efetch_xml(["999999999999"]) == []

    def test_malformed_xml_raises(self, pubmed, pm_record):
        pm_record("<PubmedArticleSet><PubmedArticle>")
        with pytest.raises(pubmed.ToolError, match="unparseable"):
            pubmed._efetch_xml(["1"])


class TestText:
    def test_present_element(self, pubmed):
        article = _article(ARTICLE_XML)
        assert pubmed._text(article, ".//ArticleTitle") == "A test article title."

    def test_missing_path_returns_none(self, pubmed):
        article = _article(ARTICLE_XML)
        assert pubmed._text(article, ".//NoSuchTag") is None

    def test_none_element_returns_none(self, pubmed):
        assert pubmed._text(None, ".//ArticleTitle") is None


class TestArticleIds:
    def test_reads_the_articles_own_ids(self, pubmed):
        article = _article(ARTICLE_XML)
        ids = pubmed._article_ids(article)
        assert ids == {"pubmed": "33668216", "doi": "10.3390/v13030356", "pmc": "PMC7995974"}

    def test_does_not_pick_up_reference_list_ids(self, pubmed):
        # The fixture's ReferenceList carries a different doi and pubmed id
        # for a cited work, at the same relative XML depth a naive ".//"
        # search would also match.
        article = _article(ARTICLE_XML)
        ids = pubmed._article_ids(article)
        assert ids["doi"] != "10.0000/not-this-articles-doi"
        assert ids["pubmed"] == "33668216"

    def test_no_ids_present(self, pubmed):
        article = _article(UNINDEXED_ARTICLE_XML)
        # UNINDEXED_ARTICLE_XML does carry a pubmed id but no doi/pmc.
        assert pubmed._article_ids(article) == {"pubmed": "42750775"}


class TestLinks:
    def test_pubmed_url_always_present(self, pubmed):
        links = pubmed._links("33668216", {})
        assert links["pubmed_url"].endswith("/33668216/")

    def test_doi_and_pmc_urls_are_conditional(self, pubmed):
        links = pubmed._links("33668216", {"doi": "10.1/x", "pmc": "PMC1"})
        assert links["doi_url"] == "https://doi.org/10.1/x"
        assert links["pmc_url"].endswith("/PMC1/")
        assert pubmed._links("33668216", {}).keys() == {"pubmed_url"}


class TestParseCore:
    def test_reads_title_journal_authors_dates_ids(self, pubmed):
        core = pubmed._parse_core(_article(ARTICLE_XML), "33668216")
        assert core["title"] == "A test article title."
        assert core["journal"] == "Viruses"
        assert core["pub_date"] == "2021 Feb 24"
        assert core["doi"] == "10.3390/v13030356"
        assert core["pmcid"] == "PMC7995974"
        assert core["indexing_status"] == "MEDLINE"

    def test_prefers_iso_abbreviation_over_full_title(self, pubmed):
        # ARTICLE_XML's Journal carries both; ISOAbbreviation wins.
        assert pubmed._parse_core(_article(ARTICLE_XML), "1")["journal"] == "Viruses"

    def test_author_names_join_forename_and_lastname(self, pubmed):
        authors = pubmed._parse_core(_article(ARTICLE_XML), "1")["authors"]
        assert "Lila M Zarski" in authors

    def test_collective_name_used_when_there_is_no_individual_author(self, pubmed):
        authors = pubmed._parse_core(_article(ARTICLE_XML), "1")["authors"]
        assert "Some Consortium" in authors

    def test_unindexed_article_has_no_mesh_status_pending(self, pubmed):
        core = pubmed._parse_core(_article(UNINDEXED_ARTICLE_XML), "42750775")
        assert core["indexing_status"] == "Publisher"
        assert core["doi"] is None


class TestPubDate:
    def test_year_month_day(self, pubmed):
        citation = _article(ARTICLE_XML).find("MedlineCitation")
        assert pubmed._pub_date(citation) == "2021 Feb 24"

    def test_year_month_only(self, pubmed):
        citation = _article(UNINDEXED_ARTICLE_XML).find("MedlineCitation")
        assert pubmed._pub_date(citation) == "2026 Oct"

    def test_missing_pubdate_element(self, pubmed):
        import xml.etree.ElementTree as ET

        citation = ET.fromstring("<MedlineCitation><Article><Journal/></Article></MedlineCitation>")
        assert pubmed._pub_date(citation) is None


class TestParseAbstract:
    def test_labeled_sections_are_joined_with_their_label(self, pubmed):
        result = pubmed._parse_abstract(_article(ARTICLE_XML))
        assert result["abstract"] == "BACKGROUND: Background text. METHODS: Methods text."

    def test_labeled_sections_are_also_kept_structured(self, pubmed):
        result = pubmed._parse_abstract(_article(ARTICLE_XML))
        assert result["abstract_sections"] == [
            {"label": "BACKGROUND", "text": "Background text."},
            {"label": "METHODS", "text": "Methods text."},
        ]

    def test_unlabeled_single_paragraph_has_no_sections(self, pubmed):
        import xml.etree.ElementTree as ET

        article = ET.fromstring("<PubmedArticle><Abstract><AbstractText>Plain text.</AbstractText></Abstract></PubmedArticle>")
        result = pubmed._parse_abstract(article)
        assert result["abstract"] == "Plain text."
        assert result["abstract_sections"] is None

    def test_no_abstract_at_all(self, pubmed):
        result = pubmed._parse_abstract(_article(UNINDEXED_ARTICLE_XML))
        assert result["abstract"] is None
        assert result["abstract_sections"] is None


class TestParseMesh:
    def test_reads_terms_major_topic_and_qualifiers(self, pubmed):
        result = pubmed._parse_mesh(_article(ARTICLE_XML))
        by_term = {h["term"]: h for h in result["mesh_terms"]}
        assert by_term["Animals"]["major_topic"] is False
        assert by_term["Herpesviridae Infections"]["major_topic"] is True
        assert by_term["Herpesviridae Infections"]["qualifiers"] == ["virology"]

    def test_reads_publication_types(self, pubmed):
        result = pubmed._parse_mesh(_article(ARTICLE_XML))
        assert result["publication_types"] == ["Journal Article"]

    def test_no_mesh_at_all(self, pubmed):
        result = pubmed._parse_mesh(_article(UNINDEXED_ARTICLE_XML))
        assert result["mesh_terms"] == []
        assert result["publication_types"] == []


class TestFit:
    """_fit trims rows until the whole root payload fits the budget."""

    def test_under_budget_is_untouched(self, pubmed):
        payload = {"hits": [1, 2, 3]}
        assert pubmed._fit(payload, payload, "hits") == (3, 3)

    def test_trims_a_list(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 4000)
        payload = {"hits": [{"title": f"T{i}"} for i in range(400)]}
        kept, total = pubmed._fit(payload, payload, "hits")
        assert total == 400 and kept < 400
        assert len(payload["hits"]) == kept

    def test_trims_a_mapping(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 4000)
        rows = {f"pmid{i}": {"title": "x"} for i in range(400)}
        payload = {"summaries": rows}
        kept, total = pubmed._fit(payload, payload, "summaries")
        assert total == 400 and kept < 400
        assert len(payload["summaries"]) == kept

    def test_empty_mapping_does_not_crash(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 1)
        payload = {"summaries": {}}
        assert pubmed._fit(payload, payload, "summaries") == (0, 0)

    def test_missing_or_scalar_key(self, pubmed):
        assert pubmed._fit({}, {}, "hits") == (0, 0)
        assert pubmed._fit({"hits": "x"}, {"hits": "x"}, "hits") == (0, 0)

    def test_one_oversized_row_is_still_returned(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 10)
        payload = {"hits": [{"abstract": "x" * 500}]}
        assert pubmed._fit(payload, payload, "hits") == (1, 1)

    def test_reserves_room_for_the_truncation_note(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 4000)
        payload = {"hits": [{"title": f"T{i}"} for i in range(400)]}
        pubmed._fit(payload, payload, "hits")
        assert len(json.dumps(payload)) <= 4000 - pubmed.TRUNCATION_RESERVE


class TestBudget:
    def test_corrects_the_reported_count(self, pubmed, monkeypatch):
        monkeypatch.setattr(pubmed, "MAX_RESPONSE_CHARS", 80)
        payload = {"hits": [{"title": f"T{i}"} for i in range(40)], "hits_returned": 40}
        pubmed._budget(payload, "hits", "hits_returned")
        assert payload["hits_returned"] == len(payload["hits"])
        assert "truncated" in payload

    def test_silent_when_nothing_was_dropped(self, pubmed):
        payload = {"hits": [1, 2], "hits_returned": 2}
        pubmed._budget(payload, "hits", "hits_returned")
        assert "truncated" not in payload


class TestClampSize:
    def test_within_limit_is_untouched(self, pubmed):
        assert pubmed._clamp_size(10, 200) == (10, None)

    def test_over_limit_is_clamped_and_noted(self, pubmed):
        size, note = pubmed._clamp_size(5000, 200)
        assert size == 200
        assert note and "200" in note

    def test_negative_is_floored(self, pubmed):
        assert pubmed._clamp_size(-5, 200) == (0, None)
