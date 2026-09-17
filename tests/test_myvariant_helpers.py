"""Unit tests for the MyVariant server's pure helpers. No network access."""

from __future__ import annotations

import json

import pytest
import requests

from conftest import FakeResponse


class TestCleanParams:
    def test_drops_empty_values(self, myvariant):
        assert myvariant._clean_params({"a": None, "b": "", "c": [], "d": "keep"}) == {"d": "keep"}

    def test_keeps_zero(self, myvariant):
        # size=0 is how a facet-only query asks for counts without hits, so it
        # must survive the clean. 0 == "" is False in Python, which is what
        # makes this work; the test pins that down.
        assert myvariant._clean_params({"size": 0}) == {"size": 0}

    def test_renders_booleans_lowercase(self, myvariant):
        assert myvariant._clean_params({"a": True, "b": False}) == {"a": "true", "b": "false"}

    def test_joins_lists(self, myvariant):
        assert myvariant._clean_params({"fields": ["chrom", "vcf"]}) == {"fields": "chrom,vcf"}


class TestApiError:
    def test_validation_error_names_the_parameter_and_bound(self, myvariant):
        # The body the API returns for size=1001.
        error = myvariant._api_error(400, {
            "code": 400, "success": False, "error": "Bad Request",
            "keyword": "size", "max": 1000, "num": 1001, "alias": "limit",
        })
        assert "size" in str(error) and "1000" in str(error)

    def test_fielddata_error_keeps_the_root_cause(self, myvariant):
        # The body the API returns when faceting a text field.
        error = myvariant._api_error(400, {
            "code": 400, "success": False,
            "error": "search_phase_execution_exception",
            "root_cause_line_00": "Fielddata is disabled on [chrom]",
        })
        assert "search_phase_execution_exception" in str(error)
        assert "Fielddata is disabled" in str(error)

    def test_root_causes_are_ordered(self, myvariant):
        error = myvariant._api_error(400, {
            "error": "boom",
            "root_cause_line_01": "second",
            "root_cause_line_00": "first",
        })
        assert str(error).index("first") < str(error).index("second")

    def test_value_error_keeps_details(self, myvariant):
        # The body the API returns for offset + size over 10000.
        error = myvariant._api_error(400, {
            "error": "ValueError", "details": "Result window is too large",
        })
        assert "Result window is too large" in str(error)

    def test_non_dict_body(self, myvariant):
        assert "502" in str(myvariant._api_error(502, "<html>gateway</html>"))

    def test_message_is_bounded(self, myvariant):
        error = myvariant._api_error(400, {"error": "x" * 5000})
        assert len(str(error)) < 700


class TestRequest:
    def test_body_level_failure_raises_even_on_http_200(self, myvariant, mv_record):
        mv_record({"success": False, "error": "cannot map some ids."})
        with pytest.raises(myvariant.ToolError, match="cannot map some ids"):
            myvariant._get("/query", {"q": "BRAF"})

    def test_timeout_is_actionable(self, myvariant, monkeypatch):
        def timeout(*args, **kwargs):
            raise requests.Timeout("too slow")

        monkeypatch.setattr(myvariant.requests, "request", timeout)
        with pytest.raises(myvariant.ToolError, match="timed out"):
            myvariant._get("/query", {"q": "BRAF"})

    def test_connection_error_names_the_url(self, myvariant, monkeypatch):
        def boom(*args, **kwargs):
            raise requests.ConnectionError("no route")

        monkeypatch.setattr(myvariant.requests, "request", boom)
        with pytest.raises(myvariant.ToolError, match="Could not reach MyVariant.info"):
            myvariant._get("/query", {"q": "BRAF"})

    def test_non_json_body_is_reported(self, myvariant, mv_record):
        mv_record("<html>503</html>", status=503, json_fails=True)
        with pytest.raises(myvariant.ToolError, match="non-JSON"):
            myvariant._get("/query", {"q": "BRAF"})

    def test_uses_a_longer_timeout_than_a_typical_rest_call(self, myvariant, monkeypatch):
        # The index holds 1.5 billion documents, so an unscoped query or
        # facet can take tens of seconds; verified live at 27s for a
        # loosely-scoped facet, well past a typical REST timeout.
        seen = {}

        def capture(method, url, **kwargs):
            seen.update(kwargs)
            return FakeResponse({"total": 0, "hits": []})

        monkeypatch.setattr(myvariant.requests, "request", capture)
        myvariant._get("/query", {"q": "BRAF"})
        assert seen["timeout"] >= 90


class TestClampPaging:
    def test_within_limits_is_untouched(self, myvariant):
        assert myvariant._clamp_paging(10, 0) == (10, 0, None)

    def test_size_clamped_to_api_maximum(self, myvariant):
        size, offset, note = myvariant._clamp_paging(5000, 0)
        assert (size, offset) == (myvariant.MAX_SIZE, 0)
        assert note and "1000" in note

    def test_result_window_clamps_size(self, myvariant):
        size, offset, note = myvariant._clamp_paging(50, 9990)
        assert (size, offset) == (10, 9990)
        assert note and "10000" in note

    def test_offset_alone_past_the_window_is_capped(self, myvariant):
        # The API rejects "from" above 10000 outright, even with size=0, so
        # offset has to be capped on its own, not only relative to size.
        size, offset, note = myvariant._clamp_paging(10, 99999)
        assert offset == myvariant.MAX_RESULT_WINDOW
        assert size == 0
        assert note and "10000" in note

    def test_negative_inputs_are_floored(self, myvariant):
        assert myvariant._clamp_paging(-5, -5) == (0, 0, None)


class TestQuoteIfHgvsLike:
    def test_bare_genomic_hgvs_is_quoted(self, myvariant):
        quoted, note = myvariant._quote_if_hgvs_like("chr7:g.140453136A>T")
        assert quoted == '"chr7:g.140453136A>T"'
        assert note and "HGVS" in note

    def test_bare_coding_hgvs_is_quoted(self, myvariant):
        quoted, note = myvariant._quote_if_hgvs_like("NM_007294.4:c.5074G>A")
        assert quoted == '"NM_007294.4:c.5074G>A"'
        assert note is not None

    def test_already_quoted_is_untouched(self, myvariant):
        quoted, note = myvariant._quote_if_hgvs_like('"chr7:g.140453136A>T"')
        assert quoted == '"chr7:g.140453136A>T"'
        assert note is None

    def test_genuine_field_query_is_untouched(self, myvariant):
        # A real field:value query also has a colon, but is not "nothing but"
        # an HGVS-shaped id, so it must not be mangled.
        quoted, note = myvariant._quote_if_hgvs_like("chrom:7")
        assert quoted == "chrom:7"
        assert note is None

    def test_boolean_expression_is_untouched(self, myvariant):
        q = 'chrom:7 AND clinvar.rcv.clinical_significance:"Pathogenic"'
        quoted, note = myvariant._quote_if_hgvs_like(q)
        assert quoted == q
        assert note is None

    def test_free_text_is_untouched(self, myvariant):
        quoted, note = myvariant._quote_if_hgvs_like("BRAF")
        assert quoted == "BRAF"
        assert note is None

    def test_note_points_to_the_deterministic_tools(self, myvariant):
        _, note = myvariant._quote_if_hgvs_like("chr7:g.140453136A>T")
        assert "myvariant_get_variant" in note
        _, note = myvariant._quote_if_hgvs_like("NM_007294.4:c.5074G>A")
        assert "myvariant_map_ids" in note


class TestProvenance:
    def test_dbsnp_link(self, myvariant):
        links = myvariant._provenance({"dbsnp": {"rsid": "rs334"}})
        assert links["dbsnp_url"].endswith("/snp/rs334")

    def test_clinvar_link(self, myvariant):
        links = myvariant._provenance({"clinvar": {"variant_id": 13961}})
        assert links["clinvar_url"].endswith("/variation/13961/")

    def test_myvariant_link_from_id(self, myvariant):
        links = myvariant._provenance({"_id": "chr7:g.140453136A>T"})
        assert links["myvariant_url"].endswith("/variant/chr7:g.140453136A>T")

    def test_dbsnp_as_a_list_is_handled(self, myvariant):
        # Defensive: most docs carry a dict, but the code tolerates a list.
        links = myvariant._provenance({"dbsnp": [{"rsid": "rs334"}]})
        assert links["dbsnp_url"].endswith("/snp/rs334")

    def test_empty_dbsnp_list_is_not_an_error(self, myvariant):
        assert "dbsnp_url" not in myvariant._provenance({"dbsnp": []})

    def test_no_ids_means_no_links(self, myvariant):
        assert myvariant._provenance({"chrom": "7"}) == {}


class TestResolveAssembly:
    def test_empty_means_server_default(self, myvariant):
        assert myvariant._resolve_assembly("") == ""
        assert myvariant._resolve_assembly("   ") == ""

    def test_known_assemblies(self, myvariant):
        assert myvariant._resolve_assembly("hg19") == "hg19"
        assert myvariant._resolve_assembly("hg38") == "hg38"

    def test_is_lowercased(self, myvariant):
        assert myvariant._resolve_assembly("HG38") == "hg38"

    def test_unknown_assembly_lists_the_real_ones(self, myvariant):
        with pytest.raises(myvariant.ToolError) as exc:
            myvariant._resolve_assembly("hg18")
        message = str(exc.value)
        assert "hg19" in message and "hg38" in message


class TestTrimList:
    def test_short_list_untouched(self, myvariant):
        container = {"rcv": [1, 2, 3]}
        assert myvariant._trim_list(container, "rcv", 25, "clinvar.rcv") is None
        assert container["rcv"] == [1, 2, 3]

    def test_long_list_trimmed_and_reported(self, myvariant):
        container = {"rcv": list(range(40))}
        note = myvariant._trim_list(container, "rcv", 25, "clinvar.rcv")
        assert container["rcv"] == list(range(25))
        assert note and "40" in note and "25" in note and "15" in note

    def test_absent_or_scalar_key(self, myvariant):
        assert myvariant._trim_list({}, "rcv", 25, "clinvar.rcv") is None
        assert myvariant._trim_list({"rcv": "one"}, "rcv", 25, "clinvar.rcv") is None


class TestTrimClinvar:
    def test_caps_rcv_and_explains_what_it_is(self, myvariant):
        doc = {"clinvar": {"rcv": [{"accession": f"RCV{i}"} for i in range(40)]}}
        note = myvariant._trim_clinvar(doc)
        assert len(doc["clinvar"]["rcv"]) == myvariant.MAX_CLINVAR_RCV
        assert note and "ClinVar submission" in note

    def test_short_rcv_untouched(self, myvariant):
        doc = {"clinvar": {"rcv": [{"accession": "RCV1"}]}}
        assert myvariant._trim_clinvar(doc) is None

    def test_no_clinvar_section(self, myvariant):
        assert myvariant._trim_clinvar({}) is None


class TestTrimCivic:
    def test_caps_profiles_and_nested_evidence(self, myvariant):
        # Mirrors the shape found on BRAF V600E: many profiles, and the
        # first one alone holding far more evidence than the cap.
        doc = {
            "civic": {
                "molecularProfiles": [
                    {"name": f"profile{i}", "evidenceItems": [{"id": j} for j in range(100)]}
                    for i in range(15)
                ],
            },
        }
        note = myvariant._trim_civic(doc)
        profiles = doc["civic"]["molecularProfiles"]
        assert len(profiles) == myvariant.MAX_CIVIC_PROFILES
        assert all(len(p["evidenceItems"]) == myvariant.MAX_CIVIC_EVIDENCE_PER_PROFILE for p in profiles)
        assert "15" in note and "100" in note

    def test_small_civic_untouched(self, myvariant):
        doc = {"civic": {"molecularProfiles": [{"name": "p", "evidenceItems": [{"id": 1}]}]}}
        assert myvariant._trim_civic(doc) is None

    def test_no_civic_section(self, myvariant):
        assert myvariant._trim_civic({}) is None

    def test_profile_without_evidence_items_is_not_an_error(self, myvariant):
        doc = {"civic": {"molecularProfiles": [{"name": "p"}] * 10}}
        note = myvariant._trim_civic(doc)
        assert note and "10" in note


class TestFit:
    """_fit trims rows until the whole root payload fits the budget."""

    def test_under_budget_is_untouched(self, myvariant):
        payload = {"hits": [1, 2, 3]}
        assert myvariant._fit(payload, payload, "hits") == (3, 3)
        assert payload["hits"] == [1, 2, 3]

    def test_trims_a_list(self, myvariant, monkeypatch):
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 4000)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(400)]}
        kept, total = myvariant._fit(payload, payload, "hits")
        assert total == 400 and kept < 400
        assert len(payload["hits"]) == kept

    def test_trims_a_mapping(self, myvariant, monkeypatch):
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 4000)
        rows = {f"rs{i}": {"chrom": "7"} for i in range(400)}
        payload = {"matches": rows}
        kept, total = myvariant._fit(payload, payload, "matches")
        assert total == 400 and kept < 400
        assert len(payload["matches"]) == kept

    def test_empty_mapping_does_not_crash(self, myvariant, monkeypatch):
        # A mapping has no keys to test for truthiness, so taking it for a
        # list used to slice a dict and raise KeyError.
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 1)
        payload = {"matches": {}}
        assert myvariant._fit(payload, payload, "matches") == (0, 0)
        assert payload["matches"] == {}

    def test_empty_list_does_not_crash(self, myvariant, monkeypatch):
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 1)
        payload = {"hits": []}
        assert myvariant._fit(payload, payload, "hits") == (0, 0)

    def test_missing_or_scalar_key(self, myvariant):
        assert myvariant._fit({}, {}, "hits") == (0, 0)
        assert myvariant._fit({"hits": "x"}, {"hits": "x"}, "hits") == (0, 0)

    def test_one_oversized_row_is_still_returned(self, myvariant, monkeypatch):
        # Returning nothing would be worse than returning one row over budget.
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 10)
        payload = {"hits": [{"summary": "x" * 500}]}
        assert myvariant._fit(payload, payload, "hits") == (1, 1)

    def test_reserves_room_for_the_truncation_note(self, myvariant, monkeypatch):
        # Rows are trimmed to slightly under the budget, because the caller
        # appends a note explaining the trim afterwards and that note has to
        # fit too.
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 4000)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(400)]}
        myvariant._fit(payload, payload, "hits")
        assert len(json.dumps(payload)) <= 4000 - myvariant.TRUNCATION_RESERVE

    def test_measures_the_root_not_the_container(self, myvariant, monkeypatch):
        # raw_query trims a nested hits list, so the keys wrapping it have to
        # count against the budget too. Same rows, same budget: the payload
        # carrying padding must keep strictly fewer of them.
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 400)
        rows = [{"symbol": f"G{i}"} for i in range(50)]

        bare = {"hits": list(rows)}
        kept_bare, _ = myvariant._fit(bare, bare, "hits")

        nested = {"hits": list(rows)}
        padded = {"response": nested, "padding": "p" * 200}
        kept_padded, total = myvariant._fit(padded, nested, "hits")

        assert total == 50
        assert kept_padded < kept_bare < 50
        assert len(json.dumps(padded)) <= 400


class TestBudget:
    def test_corrects_the_reported_count(self, myvariant, monkeypatch):
        monkeypatch.setattr(myvariant, "MAX_RESPONSE_CHARS", 80)
        payload = {"hits": [{"symbol": f"GENE{i}"} for i in range(40)], "hits_returned": 40}
        myvariant._budget(payload, "hits", "hits_returned")
        assert payload["hits_returned"] == len(payload["hits"])
        assert "truncated" in payload

    def test_silent_when_nothing_was_dropped(self, myvariant):
        payload = {"hits": [1, 2], "hits_returned": 2}
        myvariant._budget(payload, "hits", "hits_returned")
        assert "truncated" not in payload
        assert payload["hits_returned"] == 2
