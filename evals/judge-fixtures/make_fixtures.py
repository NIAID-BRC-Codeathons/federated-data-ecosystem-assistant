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


def write(name, q, question, steps, answer, denied=False, error=None) -> str:
    tools_in_order = [c["tool"] for s in steps for c in (s.get("tool_calls") or [])]
    lines = [{"role": "user", "text": question}] + steps
    lines.append({"summary": {
        "question_number": q, "question": question, "model": "fixture",
        "elapsed_s": 1.0, "tools_in_order": tools_in_order,
        "tool_call_count": len(tools_in_order), "denied": denied, "error": error,
        "answer": answer, "answer_chars": len(answer),
        "input_tokens": 1000, "output_tokens": 50}})
    (D / name).write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in lines) + "\n",
        encoding="utf-8")
    return name


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

    made.append(write("empty-answer.jsonl", 2, Q2, [
        asst([("ncbi_pathogen_organisms", {"contains": "coli"})]),
        tool("ncbi_pathogen_organisms", GROUPS_OK),
    ], ""))

    print(f"{len(made)} fixtures written to {D}")
    for n in sorted(made):
        print("  ", n)


if __name__ == "__main__":
    main()
