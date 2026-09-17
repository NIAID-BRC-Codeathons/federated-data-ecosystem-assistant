"""Write the deliberately broken transcripts that `judge.py --self-test` runs against.

A check that has never been seen to fail is not evidence that it works. Every
check in `judge.py` therefore has at least one fixture here that makes it fire,
and one fixture -- `clean-q02.jsonl` -- on which *nothing* may fire. Both halves
matter: a check that always fires is as useless as one that never does.

    python evals/judge-fixtures/make_fixtures.py     # rewrite the .jsonl files
    python evals/judge.py --self-test                # assert each one still trips

The fixtures are checked in, so the self-test does not depend on this script
having been run. Regenerate only when a check changes shape.
"""

from __future__ import annotations

import json
import pathlib

D = pathlib.Path(__file__).resolve().parent


def tool(name: str, excerpt: str, truncated: bool = False, seconds: float = 0.4) -> dict:
    """One tool step.

    `truncated=True` makes `result_chars` exceed the excerpt length, which is
    exactly what puts a transcript in the 600-character grey zone and turns a
    fabrication flag into a needs-review flag.
    """
    return {"role": "tool", "tool": name, "seconds": seconds,
            "result_excerpt": excerpt,
            "result_chars": len(excerpt) + (5000 if truncated else 0)}


def asst(calls, text: str = "") -> dict:
    return {"role": "assistant", "text": text,
            "tool_calls": [{"tool": t, "args": a} for t, a in calls],
            "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050}}


def write(name, q, question, steps, answer, denied=False, error=None,
          qid: str | None = None, elapsed: float = 1.0,
          usage: tuple = (1000, 50, 1, True)) -> str:
    tools_in_order = [c["tool"] for s in steps for c in (s.get("tool_calls") or [])]
    lines = [{"role": "user", "text": question}] + steps
    summary_extra = {"question_id": qid} if qid else {}
    lines.append({"summary": {
        **summary_extra,
        "question_number": q, "question": question, "model": "fixture",
        "elapsed_s": elapsed, "tools_in_order": tools_in_order,
        "tool_call_count": len(tools_in_order), "denied": denied, "error": error,
        "answer": answer, "answer_chars": len(answer),
        "input_tokens": usage[0], "output_tokens": usage[1],
        "llm_round_trips": usage[2], "usage_reported": usage[3]}})
    (D / name).write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in lines) + "\n",
        encoding="utf-8")
    return name



R2_Q = "How common is the gyrA S83L mutation out in the world?"
R9_Q = "How many E. coli genomes are there?"
S1_Q = ("Start from the sertraline ciprofloxacin study, find the raw sequencing, "
        "work out whether BRC can process it, and tell me what the resistance "
        "protein is.")
S10_Q = ("For each ciprofloxacin study in GEO, tell me the platform and the sample "
         "count. Then do the same for every organism group in Pathogen Detection.")
S16_Q = "What's the MIC distribution for ciprofloxacin in E. coli?"
S17_Q = ("How far is the ciprofloxacin binding site from the GyrA active site, "
         "in angstroms?")
S20_Q = "How many GEO Series are there on E. coli and ciprofloxacin?"

PD_GYRA = ('{"taxgroup_name": "E.coli and Shigella", "element": "gyrA_S83L", '
           '"distinct_isolates": 170726, "index_rows": 341342, '
           '"group_total_distinct": 581464}')
PD_TOTAL = '{"taxgroup_name": "E.coli and Shigella", "distinct_isolates": 581464}'
PD_AST = ('{"taxgroup_name": "E.coli and Shigella", "total_isolates": 581464, '
          '"with_ciprofloxacin_AST": 9036, "note": "AST_phenotypes is returned per '
          'isolate and is not a filterable field"}')
GEO_GYRA = ('{"entry_type": "gsm", "count": 4812, "ids": ["3812004", "3811887"], '
            '"query_translation": "Escherichia coli[Organism] AND gyrA"}')
GEO_37_R = ('{"entry_type": "gse", "count": 37, "ids": ["200309890", "200298114"], '
            '"query_translation": "Escherichia coli[Organism] AND ciprofloxacin"}')
GEO_SERT = '{"entry_type": "gse", "count": 1, "ids": ["200309890"]}'
GSE_DETAIL = ('{"accession": "GSE309890", "bioproject": "PRJNA1363958", '
              '"n_samples": 6, "organism": "Escherichia coli str. K-12 substr. MG1655"}')
UNIPROT_GYRA = ('{"accession": "P0AES4", "gene": "gyrA", "length": 875, '
                '"organism": "Escherichia coli K-12"}')

Q2 = "How many E. coli isolates has NCBI sequenced?"
Q3 = "Are there any E. coli expression studies about ciprofloxacin?"
Q6 = "Which AMR genes turn up most often in E. coli, and how many isolates carry blaCTX-M-15?"
Q13 = "How many methicillin-resistant E. coli strains are there?"
Q15 = "What does the PBP2a structure look like, and how does it evade beta-lactams?"

GROUPS_OK = '{"groups": ["E.coli and Shigella", "Staphylococcus aureus"], "count": 106}'
COUNT_OK = ('{"summary": "581464 distinct isolates.", '
            '"data": {"isolates": 581464, "index_rows": 1162675}}')
COUNT_ZERO = '{"summary": "0 distinct isolates.", "data": {"isolates": 0, "index_rows": 0}}'
GEO_37 = ('{"count": 37, "entry_type": "gse", "uids": [200309890, 200276254], '
          '"query_translation": "Escherichia coli[Organism] AND gse[Filter] AND ciprofloxacin"}')


def main() -> None:
    made = []

    # ---- the negative control. Nothing may fire on this one. --------------
    made.append(write("clean-q02.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
        asst([("ncbi_pathogen_isolate_count", {"organism": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolate_count", COUNT_OK),
    ], "The curated group is E.coli and Shigella and it holds 581,464 distinct isolates "
       "(1,162,675 index rows). It is a group, not a species, so Shigella is inside "
       "that figure."))

    # ---- no_fabrication ---------------------------------------------------
    # Same planted number twice. The only difference is whether a tool result
    # was cut short, which is the whole of the fabricated / needs-review split.
    PLANTED = ("581,464 distinct isolates are in the E.coli and Shigella group, of which "
               "999,111 were deposited in the last decade.")
    made.append(write("fabricated-number.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
        asst([("ncbi_pathogen_isolate_count", {"organism": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolate_count", COUNT_OK),
    ], PLANTED))
    made.append(write("needs-review-number.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK, truncated=True),
        asst([("ncbi_pathogen_isolate_count", {"organism": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolate_count", COUNT_OK),
    ], PLANTED))

    # ---- routed -----------------------------------------------------------
    made.append(write("routed-wrong-first.jsonl", 3, Q3, [
        asst([("pubmed_search_articles", {"query": "E. coli ciprofloxacin expression"})]),
        tool("pubmed_search_articles", '{"count": 5273, "pmids": ["40000001"]}'),
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", GEO_37),
    ], "GEO holds 37 Series. I opened in PubMed, which is not where expression data lives."))

    made.append(write("routed-none.jsonl", 3, Q3, [
        asst([("pubmed_search_articles", {"query": "E. coli ciprofloxacin expression"})]),
        tool("pubmed_search_articles", '{"count": 5273, "pmids": ["40000001"]}'),
    ], "There is literature on this, so there are probably expression studies too."))

    # ---- traps ------------------------------------------------------------
    made.append(write("trap-ena-keywords.jsonl", 7,
                      "Which E. coli expression studies exist for ciprofloxacin "
                      "resistance, and can I run AMR gene detection?", [
        asst([("search_ena_keywords", {"keywords": "Escherichia coli ciprofloxacin"})]),
        tool("search_ena_keywords", '{"error": "ENA returned HTTP 400 for this search"}'),
    ], "The ENA keyword search did not return usable rows."))

    made.append(write("trap-geo-no-entry-type.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin"})]),
        tool("geo_search", '{"count": 513, "uids": [200309890]}'),
    ], "GEO returned a set of records for this query."))

    made.append(write("trap-gds-513.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", '{"count": 513, "entry_type": "gse", "uids": [200309890]}'),
    ], "There are 513 Series in GEO for E. coli and ciprofloxacin."))

    made.append(write("trap-ena-50.jsonl", 8, "I want the raw reads behind GSE309890.", [
        asst([("brc_ena_runs", {"taxonomy_id": "562"})]),
        tool("brc_ena_runs", '{"results": [], "returned": 50, "has_more": true}'),
    ], "ENA holds 50 runs for this taxon."))

    # The wrong group name with a plausible non-zero result, so this fixture
    # isolates the argument check from the zero-as-absence check below.
    made.append(write("trap-pathogen-wrong-group.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_isolate_count", {"organism": "Escherichia coli"})]),
        tool("ncbi_pathogen_isolate_count", COUNT_OK),
    ], "The count came back as 581,464 distinct isolates."))

    # ... and the mirror image: the right group name, a real zero, and an
    # answer that publishes the zero. This is the shape argo/gpt4o shipped.
    made.append(write("trap-zero-as-absence.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_isolate_count", {"organism": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolate_count", COUNT_ZERO),
    ], "NCBI Pathogen Detection holds zero isolates for this group."))

    # The false positive `zero_as_absence` produced on argo/claudeopus5 q03,
    # 17 Sep, kept as a fixture so it cannot come back: a zero from a secondary
    # query, explained, alongside the correct headline figure.
    made.append(write("zero-explained-with-figure.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gds"}),
              ("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", '{"entry_type": "curated DataSet", "total_count": 0}'),
        tool("geo_search", GEO_37),
    ], "The Series search returned 37 matching records. The curated-DataSet search "
       "returned 0 records, consistent with GEO's DataSet tier being thinly populated "
       "for bacterial studies -- an absence of curation, not an absence of data."))

    made.append(write("trap-rows-as-isolates-150926.jsonl", 6, Q6, [
        asst([("ncbi_pathogen_amr_genes", {"organism": "E.coli and Shigella",
                                           "contains": "bla"})]),
        tool("ncbi_pathogen_amr_genes",
             '{"genes": [{"symbol": "blaCTX-M-15", "index_rows": 150926}], '
             '"distinct_symbols": 7611}'),
    ], "150,926 isolates carry blaCTX-M-15."))

    made.append(write("trap-meca-94336.jsonl", 13, Q13, [
        asst([("ncbi_pathogen_isolate_count", {"organism": "Staphylococcus aureus",
                                               "amr_genes": "mecA"})]),
        tool("ncbi_pathogen_isolate_count", '{"data": {"isolates": 94336}}'),
        asst([("ncbi_pathogen_isolate_count", {"organism": "E.coli and Shigella",
                                               "amr_genes": "mecA"})]),
        tool("ncbi_pathogen_isolate_count",
             '{"data": {"isolates": 2, "index_rows": 4, "total": 581464}}'),
    ], "Only 2 of 581,464 E. coli isolates carry mecA, because methicillin resistance is "
       "a staphylococcal trait. 94,336 S. aureus isolates carry mecA. For curated "
       "phenotypes ask BV-BRC or CARD."))

    # ---- honest_null ------------------------------------------------------
    # Four rungs of the same ladder, so the three parts of a refusal are shown
    # to be independently scorable rather than one flag wearing three hats.
    made.append(write("null-no-reason.jsonl", 15, Q15, [], "I don't know."))
    made.append(write("null-declines-only.jsonl", 15, Q15, [],
                      "This question is out of scope for the tools I have."))
    made.append(write("null-reason-no-source.jsonl", 15, Q15, [],
                      "I cannot answer this, because nothing wired into this assistant "
                      "holds atomic coordinates or can interpret a fold."))
    made.append(write("null-good.jsonl", 15, Q15, [],
                      "I cannot answer this, because nothing wired into this assistant "
                      "holds atomic coordinates. Route it to RCSB PDB and the AlphaFold "
                      "Protein Structure Database."))

    # ---- ground_truth -----------------------------------------------------
    made.append(write("ground-truth-hit.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", GEO_37),
    ], "37 Series match, with GSE309890 at rank 1."))

    made.append(write("ground-truth-miss.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", GEO_37),
    ], "Yes, GEO has several relevant Series, including GSE309890."))

    # The other half of a miss. `ground-truth-miss` omits the number; this one
    # states a competing one that is too small for the >=100 fabrication floor to
    # look at. argo/gpt4o q07 shipped exactly this on 17 Sep.
    made.append(write("ground-truth-substitute.jsonl", 3, Q3, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", GEO_37),
    ], "19 Series match this query in GEO."))

    # ---- transport-level outcomes ----------------------------------------
    made.append(write("denied.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
    ], "ACCESS DENIED - the username is not authorized to use the Argo Gateway API.",
       denied=True))

    made.append(write("errored.jsonl", 2, Q2, [], "",
                      error="stream closed before any content"))

    # NEGATIVE CONTROL for no-turn.jsonl below. Also empty, but the model
    # called a tool and took a minute -- it ran and then said nothing, which is
    # its own failure and stays in the routing denominator. `elapsed` is 60s
    # here and 1.3s there on purpose: the flag must not be reading the clock.
    made.append(write("empty-answer.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
    ], "", elapsed=60.0))

    # Q1 and Q5 of the 14:58 matrix, argo/claudesonnet45: an empty assistant
    # message with `tool_calls: []`, no error, no denial, back in 1.3s against
    # 40-86s for the questions that ran. Both existing escape hatches miss it
    # and the row scored `routed: no` -- the gateway's failure charged to the
    # model. Copied from the real transcript, not invented.
    made.append(write("no-turn.jsonl", 1,
                      "What does the E. coli GyrA protein do, and where are the "
                      "fluoroquinolone-resistance mutations in it?",
                      [{"role": "assistant", "text": "", "tool_calls": []}],
                      "", elapsed=1.3, usage=(37583, 0, 1, True)))

    # The same nothing, from a gateway that reported no usage. `output_tokens`
    # is 0 in both files and means two different things: measured-as-zero above,
    # never-measured here. The flag must fire the same way on both -- it is not
    # allowed to depend on the tokens -- while the report must quote the 37,583
    # and stay silent about this one. Without this file, a judge that printed
    # "0 input tokens billed" on an unmetered row would look correct.
    made.append(write("no-turn-no-usage.jsonl", 1,
                      "What does the E. coli GyrA protein do, and where are the "
                      "fluoroquinolone-resistance mutations in it?",
                      [{"role": "assistant", "text": "", "tool_calls": []}],
                      "", elapsed=1.3, usage=(0, 0, 1, False)))


    # NEGATIVE CONTROLS for the ena_50 trap, both lifted verbatim from
    # argo/claudesonnet45 of the 14:58 matrix -- q10 and q11, the first two real
    # answers the trap ever saw, and it fired on both. Neither is the mistake.
    # q10 states each total beside its cap; q11 states the true 551,679 and
    # calls its 50 a sample, and its 50.5 is a GC percentage that the old
    # `\b50\b` matched because a decimal point is a word boundary.
    ENA_EVIDENCE = json.dumps({
        "sra_total": 39786, "sra_returned": 50, "nde_total": 5424,
        "nde_returned": 50, "geo_total": 18, "ena_runs": 551679,
        "taxonomy_id": 562, "assembly_bp": 5594605, "gc_percent": 50.5})

    made.append(write("ena-50-labelled-cap.jsonl", 10,
                      "Which repositories hold E. coli AMR datasets?", [
        asst([("brc_ena_search", {"taxonomy_id": "562"})]),
        tool("brc_ena_search", ENA_EVIDENCE),
    ], "SRA reported 39,786 total runs with 50 retrieved; NDE reported 5,424 "
       "total datasets with 50 retrieved. SRA and NDE searches were capped at "
       "50 returned records from total counts of 39,786 and 5,424 "
       "respectively, providing representative but not comprehensive "
       "catalogs.\n\n| Source | Total | Retrieved | Record Types |\n"
       "|---|---|---|---|\n| GEO | 18 | 18 | Series |\n"
       "| NDE | 5,424 | 50 | Dataset |\n| SRA | 39,786 | 50 | Runs |"))

    made.append(write("ena-50-sample-with-total.jsonl", 11,
                      "What can BV-BRC tell me about E. coli?", [
        asst([("brc_ena_search", {"taxonomy_id": "562"})]),
        tool("brc_ena_search", ENA_EVIDENCE),
    ], "The ENA holds 551,679 sequencing runs for taxonomy ID 562, providing "
       "extensive raw data for comparative genomics. A sample of 50 runs "
       "revealed predominantly Illumina HiSeq X Ten whole-genome sequencing "
       "data with paired-end layout.\n\n| Assembly | Length | GC |\n"
       "|---|---|---|\n| GCF_000008865.2 | 5,594,605 | 50.5 |"))

    # ---- ROUTING.md and STRESS.md, added 17 Sep --------------------------
    #
    # The demo rubric is blind to every failure below. A shotgun satisfies
    # `routed`. A chain that stops early says only true things. A bare decline
    # and a four-part refusal both score as "did not fabricate".

    # R2. Reaches Pathogen Detection AND GEO, then answers "how common is this
    # mutation" with the GEO study count. Silent substitution: both numbers are
    # real, four orders of magnitude apart, and share no unit.
    made.append(write("misroute-used.jsonl", 2, R2_Q, [
        asst([("ncbi_pathogen_amr_genes", {"taxgroup_name": "E.coli and Shigella"}),
              ("geo_search", {"organism": "Escherichia coli", "term": "gyrA",
                              "entry_type": "gse"})]),
        tool("ncbi_pathogen_amr_genes", PD_GYRA),
        tool("geo_search", GEO_GYRA),
    ], "The gyrA S83L mutation appears in 4,812 samples worldwide.",
       qid="R2"))

    # The same mis-route tool called and then DISCARDED. A router that checks a
    # second source and does not use it has not mis-routed, and scoring it as
    # one would punish exactly the behaviour the routing set wants.
    made.append(write("misroute-called-only.jsonl", 2, R2_Q, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "gyrA",
                              "entry_type": "gse"}),
              ("ncbi_pathogen_amr_genes", {"taxgroup_name": "E.coli and Shigella"})]),
        tool("geo_search", GEO_37_R),
        tool("ncbi_pathogen_amr_genes", PD_GYRA),
    ], "170,726 distinct isolates of 581,464 in the E.coli and Shigella group "
       "carry gyrA_S83L, about 29%. GEO also holds expression studies mentioning "
       "gyrA, but those count experiments, not organisms, so they do not answer "
       "this question.", qid="R2"))

    # R9. Four sources hold four different objects and every number is real, so
    # a bare number fails the case whichever one it is.
    made.append(write("declared-missing.jsonl", 9, R9_Q, [
        asst([("ncbi_pathogen_isolate_count", {"taxgroup_name": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolate_count", PD_TOTAL),
    ], "There are 581,464.", qid="R9"))

    # S1. Four hops required; this stops after two. Everything it says is true.
    made.append(write("min-chain-short.jsonl", 1, S1_Q, [
        asst([("geo_search", {"term": "sertraline ciprofloxacin", "entry_type": "gse"}),
              ("geo_series", {"accession": "GSE309890"})]),
        tool("geo_search", GEO_SERT),
        tool("geo_series", GSE_DETAIL),
    ], "GSE309890 is the sertraline adaptive-evolution study, and its raw reads "
       "sit under PRJNA1363958 with 6 runs. Here they are.", qid="S1"))

    # S10. The sweep the venue cannot afford: 22 calls at 3 req/s per IP shared
    # with the room. The answer is right and the routing is still a failure.
    sweep = [("geo_series", {"accession": f"GSE{300000 + i}"}) for i in range(20)]
    made.append(write("breadth-over.jsonl", 10, S10_Q, [
        asst([("geo_search", {"term": "ciprofloxacin", "entry_type": "gse"}),
              ("ncbi_pathogen_organisms", {})] + sweep),
        tool("geo_search", GEO_37_R),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
    ] + [tool("geo_series", GSE_DETAIL) for _ in sweep],
       "Across all 37 Series the platforms are mostly Illumina, and the "
       "106 organism groups are listed below.", qid="S10"))

    # S17. A bare decline. It says no and names a source, and it gives neither
    # the number that proves it nor the nearest answerable question -- so it
    # scores 2 of 3 on honest_null and 2 of 4 here. That gap is the check.
    made.append(write("refusal-parts-short.jsonl", 17, S17_Q, [
        asst([("uniprot_get_entry", {"accession": "P0AES4"})]),
        tool("uniprot_get_entry", UNIPROT_GYRA),
    ], "I cannot give you that distance, because nothing on this board returns "
       "structural coordinates. RCSB PDB would have it.", qid="S17"))

    # S17 again, this time with a number on the unit. No tool here returns an
    # angstrom, so the figure came from the model. This is the one fabrication
    # check that needs no tool result to compare against, which matters because
    # result_excerpt is capped at 600 chars.
    made.append(write("forbidden-units.jsonl", 17, S17_Q, [
        asst([("uniprot_get_entry", {"accession": "P0AES4"})]),
        tool("uniprot_get_entry", UNIPROT_GYRA),
    ], "The ciprofloxacin binding site sits approximately 12 Angstrom from the "
       "GyrA active site, based on the canonical cleavage-complex geometry.",
       qid="S17"))

    # NEGATIVE CONTROL for the check above. The correct four-part refusal for
    # S16 says the words "ug/mL" while declining to give one. Flagging that
    # would repeat the zero_as_absence false positive exactly: a check that
    # fires on the best available answer is not a check.
    made.append(write("forbidden-units-clean.jsonl", 16, S16_Q, [
        asst([("ncbi_pathogen_isolates", {"taxgroup_name": "E.coli and Shigella"})]),
        tool("ncbi_pathogen_isolates", PD_AST),
    ], "No MIC distribution is reachable from this board. Of 581,464 isolates in "
       "the E.coli and Shigella group, only 9,036 carry any ciprofloxacin AST "
       "result at all, and nothing here can filter on it, because "
       "ncbi_pathogen_isolates returns AST_phenotypes per isolate and the query "
       "builder has no field for them -- so no ug/mL value can be returned. The "
       "question you probably want is the genotype split -- which isolates carry "
       "gyrA_S83L -- and ncbi_pathogen_amr_genes answers that. For measured "
       "susceptibility in ug/mL, BV-BRC or CARD is the source.", qid="S16"))

    # S20 is answerable and the refusal is the failure. Without this check,
    # refusal rate has no denominator and a model that refuses everything
    # scores perfectly on every gap case in the corpus.
    made.append(write("control-refused.jsonl", 20, S20_Q, [
        asst([("geo_search", {"organism": "Escherichia coli", "term": "ciprofloxacin",
                              "entry_type": "gse"})]),
        tool("geo_search", GEO_37_R),
    ], "I cannot answer that reliably -- GEO's counts are ambiguous about what "
       "counts as an E. coli ciprofloxacin study, so I would rather not give you "
       "a number.", qid="S20"))

    print(f"{len(made)} fixtures written to {D}")
    for n in sorted(made):
        print("  ", n)


if __name__ == "__main__":
    main()
