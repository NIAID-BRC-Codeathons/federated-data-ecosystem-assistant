"""Score recorded chatbot transcripts against the ground truths, with no model in the loop.

`run_questions.py` records what the agent did. It does not say whether the agent
was **right**, and the obvious way to find out -- ask another model -- puts a
grader with the same failure modes in charge of the grading. This script does the
opposite: every check here is decidable from the transcript plus the expectations
encoded at the top of this file, so two people running it get the same numbers and
can argue with the encoding rather than with a judge's mood.

    uv run evals/judge.py                       # every model under evals/runs/
    uv run evals/judge.py --runs evals/runs     # the same, said out loud
    uv run evals/judge.py --out evals/judge-report.md
    uv run evals/judge.py --quiet               # write the file, print nothing

Stdlib only, no network, no key. It reads `evals/runs/<model>/qNN.jsonl` as written
by `run_questions.py`: one JSON object per line -- user / assistant-with-tool_calls /
tool-result steps -- and a final `{"summary": {...}}` line.

What it scores, per transcript:

* **routed** -- did the agent call at least one tool from the source that can
  answer this question? The expected tools per question come from `QUESTIONS.md`
  and are in `EXPECTED` below. Presence, not order: a chain in the wrong order
  still reached the right source, and ordering is a weaker signal than routing.
* **no_fabrication** -- every number >= 100 in the answer has to appear in some
  tool result, tool argument, or the question itself. An unmatched number is a
  number the model produced from nowhere.
* **known_traps** -- the named ways to get an HTTP 200 and a wrong answer out of
  this board. Each is a separate flag, listed in `TRAP_NOTES`.
* **honest_null** -- for the questions whose correct answer is "cannot", or
  "partly, and here is the part that is missing", does the answer carry a refusal
  *with a reason*, and does it name the source that could answer it?
* **denied / error** -- passed through from the summary line. Argo returns HTTP
  200 with ACCESS DENIED as the assistant's content, so a denial is content, not
  an exception, and it has to be carried rather than inferred.

## The one thing this cannot see, said here rather than in a footnote

`run_questions.py` stores `result_excerpt = raw[:600]`. For a 97,060-character
`geo_search` result the judge holds 0.6% of the evidence. So an answer number that
is *not* in the transcript may still have been in the tool result the model saw.

This script therefore never calls such a number fabricated unless **every** tool
result in that transcript arrived complete (`result_chars <= len(result_excerpt)`).
Otherwise it is counted as `unmatched` and reported in a separate column, which
means "a human has to look", not "the model made it up". Raising RESULT_EXCERPT in
`run_questions.py` converts unmatched flags into decidable ones.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNS = REPO / "evals" / "runs"
OUT = REPO / "evals" / "judge-report.md"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# EVERYTHING EDITABLE IS BETWEEN HERE
# ---------------------------------------------------------------------------

# Per question: the tools that mean "this reached a source that can answer it",
# taken from the chains in QUESTIONS.md. `kind` is what a correct answer looks
# like -- "answer" wants a number, "gap" wants a refusal with a reason. `wired`
# is False where the expected source is not in MCP_SERVERS in chatbot.py, so
# routing cannot pass and the failure belongs to the repo, not to the model.
EXPECTED: dict[int, dict] = {
    1:  {"primary": {"uniprot_search", "uniprot_get_entry", "uniprot_get_protein_info"},
         "kind": "answer", "source": "UniProt"},
    2:  {"primary": {"ncbi_pathogen_organisms", "ncbi_pathogen_isolate_count"},
         "kind": "answer", "source": "NCBI Pathogen Detection"},
    3:  {"primary": {"geo_search", "geo_series"},
         "kind": "answer", "source": "NCBI GEO"},
    4:  {"primary": {"search_organisms", "get_assemblies", "get_compatible_workflows"},
         "kind": "answer", "source": "BRC Analytics"},
    5:  {"primary": {"lapis_list_organisms", "lapis_describe_organism", "lapis_aggregate_samples"},
         "kind": "answer", "source": "PDN / LAPIS"},
    6:  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
         "kind": "answer", "source": "NCBI Pathogen Detection"},
    7:  {"primary": {"geo_search", "geo_series", "get_assemblies",
                     "check_compatibility", "resolve_workflow_inputs"},
         "kind": "answer", "source": "NCBI GEO + BRC Analytics"},
    8:  {"primary": {"geo_series", "ncbi_sra_runs_for_project", "ncbi_sra_run_metadata",
                     "get_compatible_workflows"},
         "kind": "answer", "source": "NCBI GEO + SRA + BRC Analytics"},
    9:  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count",
                     "check_compatibility"},
         "kind": "answer", "source": "NCBI Pathogen Detection + BRC Analytics"},
    # Typed `kind: gap, wired: False` until 17 Sep 16:0x, and wrong by then.
    # NDE is wired. `chatbot.py:294` carries it on `127.0.0.1:8007/mcp-nde`, and
    # `runs/argo_claudeopus5/q10.jsonl` calls `nde_search_datasets` three times
    # and `nde_facet_counts` once against api.data.niaid.nih.gov, returning live
    # totals of 4,303, 1,815 and 76. This file already knew: `EXPECTED_STRESS`
    # S8 reads "NDE (wired at 62ff6b6)". One map was updated and this one was not.
    #
    # The cost was not a wrong flag, it was a hidden cell. `wired: False` routes
    # to the blocked branch below, so a model that ANSWERED Q10 was recorded as
    # null and Q11 was held out of the denominator as "source not wired". A
    # held-out cell cannot fail, so this error could only ever make the report
    # look cleaner than the run -- the same direction as every other bug found
    # in this file today.
    #
    # Raised by the second judge session and verified here against `chatbot.py`
    # and the q10 transcript before anything was changed. That mattered: the
    # same handover carried two other findings, and neither survived the check.
    #
    # One of the two was the PubMed pair, and the check against it was correct
    # when made and then went stale. At this file's 16:15 commit (a3d08a4) the
    # branch held 20 `@server.tool()` decorators in `ncbi_lib/server.py`, with
    # `ncbi_pubmed_search` registered at line 786 -- so "they are registered"
    # was true of the code it was checked against. Jonathan's 7b4758e
    # (15:26 on main) removed the decorators from `ncbi_pubmed_search`,
    # `ncbi_pubmed_abstracts` and `ncbi_list_databases`; it reached this branch
    # in the merge at 16:23:21 (01e431b), eight minutes after the check. The
    # server now registers 17 tools. The subject of a correct check was removed,
    # which is a different failure from a wrong check and needs a different
    # guard: a verification against a moving `main` has a timestamp, not a
    # permanence. PubMed is now reachable only through `pubmed.py`
    # (`pubmed_search_articles`, `pubmed_get_summaries`, `pubmed_get_article`),
    # so R10, S3 and the R3 misroute accept EITHER surface -- see PUBMED_TOOLS.
    # Both, not only the new one, because 24 recorded calls in the corpus went
    # to the `ncbi_pubmed_` pair before it was removed, and they were right.
    10: {"primary": {"nde_facet_counts", "nde_search_datasets", "nde_get_record"},
         "kind": "answer", "source": "NDE -> NCBI (wired at 62ff6b6)"},
    11: {"primary": {"nde_list_repositories", "nde_search_datasets"},
         "kind": "answer", "source": "NDE -> BRC (wired at 62ff6b6)"},
    12: {"primary": {"ncbi_taxonomy_lookup", "mygene_search_genes", "mygene_map_ids",
                     "uniprot_get_sequence"},
         "kind": "answer", "source": "NCBI Taxonomy + MyGene + UniProt"},
    13: {"primary": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_amr_genes"},
         "kind": "gap", "source": "NCBI Pathogen Detection (the proof number)"},
    14: {"primary": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_isolates"},
         "kind": "gap", "source": "NCBI Pathogen Detection (genotype only)"},
    15: {"primary": set(),
         "kind": "gap", "source": "none on this board -- RCSB PDB / AlphaFold"},
}

# A number this large in an answer is a claim about the data, not a sample count
# or a step number, so it has to trace to something the model was shown.
# The real number of E. coli runs in ENA, against the 50 the federated page
# returns. Named here because both the `ena_50` trap and its note use it.
ENA_TRUE_RUNS = 551679

# Why a row can read clean on disk and be a known failure in the scorecard
# beside it. Appended to the held-out line so the count is never quoted without
# it.
DRIVER_BLIND_SPOT = (
    " Note that `retries: 0` and `error: null` in these records are not"
    " measurements: `run_one` writes the file before the retry loop runs and"
    " nothing rewrites it, so the driver's own `routing-scorecard.md` calls the"
    " same rows `**ERROR** silent empty after 2 retries`. Verified 17 Sep on"
    " argo/claudesonnet45 Q1, Q5, Q14, Q15.")

FABRICATION_MIN = 100

# A bare four-digit number in this range is read as a year and skipped. A real
# count that happens to land here is skipped with it; that is the price of not
# flagging every "2026" in a References section.
YEAR_RANGE = (1900, 2100)

# A good refusal has three parts and they are three different behaviours, so
# they are scored separately. Measured on argo/gpt4o, 17 Sep: 4 of 4 gap answers
# named a source and 0 of 4 gave a reason. Collapsing these into one flag would
# have scored that run 4/4 and hidden the harder half.
#
# 1. DECLINES -- it says no at all.
REFUSAL_MARKERS = (
    "cannot", "can not", "can't", "unable", "not available", "no tool",
    "not wired", "not connected", "not indexed", "no source",
    "out of scope", "not possible", "does not hold", "not exposed",
    "not recognised", "not recognized",
)

# 2. REASON -- it says *why*, either causally or by naming the specific defect.
#    "I cannot answer that" passes the check above and fails this one.
REASON_MARKERS = (
    "because", "since ", "the reason", "that is why", "which is why",
    "malformed", "not filterable", "cannot be filtered", "does not carry",
    "metadata only", "read-only", "read only", "no schema", "not in mcp_servers",
    "not wired", "different unit", "different pipeline", "different vocabular",
    "is a virus", "staphylococc", "intrinsic", "wrong type", "curated group",
    "per-call host", "page size", "not populated", "no coordinates",
    "annotation, not", "is not a recognised", "is not a recognized",
)

# 3. ALTERNATIVE -- it names where the answer actually lives.
ALTERNATIVE_SOURCES = (
    "bv-brc", "bvbrc", "card", "rcsb", "protein data bank", "pdb",
    "alphafold", "bv-brc.org", "ncbi pathogen", "bigquery",
)

# The curated Pathogen Detection group is "E.coli and Shigella". Any of these as
# an `organism` argument returns a confident 0 that reads as absence. Measured
# 17 Sep 2026: 106 groups exist and none of them is "Escherichia coli".
WRONG_PATHOGEN_GROUPS = {
    "escherichia coli", "e. coli", "e.coli", "e coli",
    "shigella", "shigella flexneri", "shigella sonnei", "escherichia",
}

# The headline flag, counted on its own line in every summary. It is not a
# hypothetical: argo/gpt4o called ncbi_pathogen_isolate_count(organism=
# "Escherichia coli") on 17 Sep, got 0 against a true 581,464, and wrote "a
# total of zero isolates matching this filter, despite correct organism naming"
# into its answer. It reasoned that the name was right and published the
# absence. Everything else in TRAP_NOTES is a trap we can argue about; this one
# has been observed shipping a wrong answer to a user.
HEADLINE_TRAP = "zero_as_absence"

TRAP_NOTES = {
    "zero_as_absence": "**a tool returned 0 and the answer reported the zero as a finding** -- observed on argo/gpt4o, 17 Sep, against a true 581,464",
    "ena_keywords": "called `search_ena_keywords` -- BRC's federated tool returns an ENA 400 as tool *text*",
    "geo_no_entry_type": "called `geo_search` with no `entry_type` -- the count then mixes GSE, GSM, GDS and GPL",
    "gds_513": "quoted **513**, the unfiltered `db=gds` count, as the Series count (37 is right)",
    "ena_50": "quoted **50** as an ENA total -- that is the federated page size; 551,679 runs exist",
    "pathogen_wrong_group": "passed an organism that is not a curated group name -- returns 0, not an error",
    "rows_as_isolates_150926": "quoted **150,926** as an isolate count -- that is the raw index row count for `blaCTX-M-15`; the service doubles E. coli and **75,487** distinct isolates carry it",
    "meca_94336": "quoted **94,336** for `mecA` without naming `mecC` -- that figure is `mecA` *or* `mecC`; `mecA` alone is **93,260** (QUESTIONS.md Q13)",
    "geo_files_unwanted": "called `geo_series(list_files=True)` on G5, where the user said *\"I don't need the files\"* -- file listing is a second network call the user declined",
    "ena_no_offset": "called `brc_ena_runs` for C2 with no `offset` -- the user already holds the first 50, and re-returning them is indistinguishable from a correct answer in the output",
    "ena_species_for_strain": "queried taxid **562** (the species) on C3, which asks for K-12 MG1655 -- taxid **511145**, 20,925 runs against 551,679, a factor of 26",
    "influenza_all_ena": "quoted **131,403** on B16 -- that is `title_contains=\"influenza\"` across all of ENA, every organism, not the E. coli slice",
}

# Where PIPELINES.md and QUESTIONS.md give an exact figure, the figure a correct
# answer has to reach -- and the near-miss that means the model took the wrong
# row out of the same response. `decoys` is what makes this more than a word
# search: a missing figure is a `miss`, a decoy in its place is a `wrong`, and
# those are different failures with different fixes. A `miss` is the quietest
# way to pass every other check and still leave the user without the number.
#
# `weak` is set automatically for values under 100. A bare "2" is easy to hit by
# accident in any prose, so those rows are marked in the report rather than
# quietly counted as strong evidence.
#
# `volatile` names a figure whose SOURCE moves. PIPELINES.md rule 2 says so in
# as many words -- "these are live counts; BRC's workflow catalogue changed
# mid-build between 16 and 17 Sep" -- and a pin that has gone stale must not be
# charged to the model. When a volatile figure is absent from the answer AND
# absent from every tool result in that same transcript, the source no longer
# returns it: the state is `moved`, the pin needs re-verifying, and the model is
# not marked wrong. When the tool DID return the pinned figure and the answer
# dropped it, that is still a `miss`. Measured 17 Sep: `get_compatible_workflows`
# returned `{"count":14}` to both argo/claudeopus5 and argo/claudesonnet45 on
# q04, both answered 14, and this file was pinning 17 -- two correct answers
# scored as failures by a stale constant.
GROUND_TRUTH: dict[int, list[dict]] = {
    2:  [{"value": 581464, "what": "distinct isolates in the `E.coli and Shigella` group",
          "decoys": {1162675: "index rows, not distinct isolates"}}],
    3:  [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin",
          "decoys": {513: "the unfiltered `db=gds` count over four record types"}}],
    4:  [{"value": 2, "what": "E. coli assemblies in BRC Analytics", "volatile": "BRC Analytics assembly catalogue"},
         {"value": 17, "what": "haploid-compatible workflows for taxid 562", "volatile": "BRC Analytics workflow catalogue; 14 on 17 Sep"}],
    6:  [{"value": 75487, "what": "distinct isolates carrying `blaCTX-M-15`",
          "decoys": {150926: "raw index rows for the same gene"}}],
    7:  [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin",
          "decoys": {513: "the unfiltered `db=gds` count"}}],
    9:  [{"value": 170726, "what": "distinct isolates carrying `gyrA_S83L`",
          "decoys": {341342: "raw index rows for the same mutation"}}],
    13: [{"value": 2, "what": "E. coli isolates carrying `mecA` -- the proof number"},
         {"value": 581464, "what": "the denominator it is 2 out of"},
         {"value": 93260, "what": "S. aureus isolates carrying `mecA`",
          "decoys": {94336: "`mecA` *or* `mecC`, which is a different question"}}],
}

# Figures a correct answer may legitimately reach but that no demo question is
# required to produce, kept here so every number in this file has a provenance.
# 551,679 ENA runs for taxid 562 and 13 distinct organisms in PRJNA715470 are
# ADVERSARIAL.md and PIPELINES.md P2 ground truths, not QUESTIONS.md ones, so
# they are referenced by the `ena_50` trap rather than scored per question.
CONTEXT_FIGURES = {551679: "ENA runs for taxid 562 (ADVERSARIAL.md A8)",
                   13: "distinct organisms in PRJNA715470 (PIPELINES.md P2)"}

# ---------------------------------------------------------------------------
# The two extra question sets, keyed by question id rather than by int, because
# `R2` and `S16` are not numbers. Runner sent these rows over on 17 Sep rather
# than editing this file; the tool sets are read out of `evals/ROUTING.md` and
# `evals/STRESS.md`, and Runner should correct any row I derived wrongly.
#
# They are separate maps on purpose: the demo numbers must not move when a
# routing row is edited.
# ---------------------------------------------------------------------------

# `misroute` is the "wrong but plausible" source for each case -- the one that
# returns HTTP 200 and a real number that answers a different question.
# `declared` is the unit or source word the answer has to carry beside its
# number; `min` is how many distinct words from the list must appear.
# PubMed has two surfaces in this corpus: the `ncbi_pubmed_` pair in
# `ncbi_lib/server.py` (registered until 7b4758e, called 24 times in recorded
# runs) and `pubmed.py`, the only one registered now. A PubMed expectation that
# names one surface is unsatisfiable on runs made against the other, and a
# misroute set that names one cannot fire on the other. See the note at Q10.
PUBMED_TOOLS = {"ncbi_pubmed_search", "ncbi_pubmed_abstracts",
                "pubmed_search_articles", "pubmed_get_summaries", "pubmed_get_article"}

EXPECTED_ROUTING: dict[str, dict] = {
    "R1":  {"primary": set(), "misroute": set(), "kind": "answer",
            "source": "no single source -- the decomposition is the answer",
            "declared": {"min": 2, "words": ["geo", "pathogen detection", "pubmed",
                                             "nde", "uniprot"]}},
    "R2":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
            "misroute": {"geo_search", "geo_series"}, "kind": "answer",
            "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["isolate"]}},
    "R3":  {"primary": {"geo_search", "geo_series", "ncbi_sra_runs_for_project"},
            "misroute": set(PUBMED_TOOLS),
            "kind": "answer", "source": "NCBI GEO"},
    "R4":  {"primary": {"search_organisms", "get_assemblies",
                        "get_compatible_workflows", "check_compatibility"},
            "misroute": {"ncbi_assembly_info"}, "kind": "answer",
            "source": "BRC Analytics"},
    "R5":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count",
                        "ncbi_pathogen_isolates"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI Pathogen Detection (genotype, declared)",
            "declared": {"min": 1, "words": ["genotype"]}},
    "R6":  {"primary": {"nde_facet_counts", "nde_search_datasets", "nde_get_record"},
            "misroute": {"ncbi_sra_search"}, "kind": "answer", "source": "NDE"},
    "R7":  {"primary": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_amr_genes",
                        "geo_search", "geo_series"},
            "misroute": set(), "kind": "gap",
            "source": "both sources, then a refusal of the join"},
    "R8":  {"primary": {"uniprot_search", "uniprot_get_entry"},
            "misroute": {"ncbi_pathogen_amr_genes"}, "kind": "answer",
            "source": "UniProt"},
    "R9":  {"primary": {"ncbi_assembly_info", "ncbi_sra_search",
                        "ncbi_pathogen_isolate_count", "get_assemblies"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI, and only after the unit is named",
            "declared": {"min": 1, "words": ["assembl", "isolate", "runs"]}},
    "R10": {"primary": set(PUBMED_TOOLS),
            "misroute": set(), "kind": "answer", "source": "PubMed",
            "declared": {"min": 1, "words": ["pdat", "pub_date", "publication date",
                                             "issue date"]}},
    "R11": {"primary": {"lapis_list_organisms", "lapis_describe_organism",
                        "lapis_aggregate_samples"},
            "misroute": {"ncbi_pathogen_organisms", "ncbi_pathogen_isolate_count"},
            "kind": "answer", "source": "PDN / LAPIS"},
    "R12": {"primary": {"ncbi_pathogen_organisms", "ncbi_pathogen_isolate_count"},
            "misroute": set(), "kind": "answer",
            "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["distinct"]}},
    "R13": {"primary": set(), "misroute": set(), "kind": "gap",
            "source": "nothing on this board -- RCSB PDB / AlphaFold"},
    "R14": {"primary": {"geo_search", "geo_series"},
            "misroute": {"ncbi_pathogen_isolate_count", "ncbi_pathogen_amr_genes"},
            "kind": "answer", "source": "NCBI GEO"},
    "R15": {"primary": {"geo_search", "geo_series"},
            "misroute": {"uniprot_search", "uniprot_get_entry",
                         "get_compatible_workflows"},
            "kind": "answer", "source": "NCBI GEO"},
}

# `min_chain` is how many DISTINCT tools from `primary` the answer has to reach;
# stopping early is the failure and an early stop still contains true sentences.
# `refusal_parts` demands all four parts of the P8 refusal rather than a bare
# decline. `forbidden_units` is fabrication by construction -- no tool on the
# board returns a concentration or an angstrom. `control` marks the three cases
# where a refusal is itself the failure, which is what gives refusal rate a
# denominator.
EXPECTED_STRESS: dict[str, dict] = {
    "S1":  {"primary": {"geo_search", "geo_series", "ncbi_sra_runs_for_project",
                        "ncbi_sra_run_metadata", "get_compatible_workflows",
                        "check_compatibility", "uniprot_search", "uniprot_get_entry"},
            "kind": "answer", "min_chain": 4,
            "source": "GEO -> SRA -> BRC -> UniProt"},
    "S2":  {"primary": {"ncbi_taxonomy_lookup", "ncbi_pathogen_organisms"},
            "kind": "answer", "source": "NCBI Taxonomy + Pathogen Detection",
            "declared": {"min": 1, "words": ["shigella"]}},
    "S3":  {"primary": set(PUBMED_TOOLS),
            "kind": "answer", "source": "PubMed",
            "declared": {"min": 1, "words": ["pdat", "pub_date", "publication date"]}},
    "S4":  {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["series"]}},
    "S5":  {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
            "kind": "answer", "source": "NCBI Pathogen Detection",
            "declared": {"min": 1, "words": ["distinct"]}},
    "S6":  {"primary": {"brc_ena_search", "search_ena", "ncbi_sra_search",
                        "ncbi_pathogen_isolate_count"},
            "kind": "answer", "min_chain": 3,
            "source": "ENA + SRA + Pathogen Detection",
            "declared": {"min": 3, "words": ["runs", "experiment", "isolate"]}},
    "S7":  {"primary": {"ncbi_bioproject_summary", "ncbi_sra_runs_for_project",
                        "ncbi_biosample_metadata"},
            "kind": "answer", "min_chain": 3,
            "source": "BioProject -> SRA -> BioSample"},
    "S8":  {"primary": {"nde_facet_counts", "nde_search_datasets"},
            "kind": "answer", "source": "NDE (wired at 62ff6b6)"},
    "S9":  {"primary": {"ncbi_pathogen_isolate_count"}, "kind": "answer",
            "min_chain": 1, "source": "NCBI Pathogen Detection, freshly called"},
    "S10": {"primary": {"geo_search", "geo_series", "ncbi_pathogen_organisms",
                        "ncbi_pathogen_isolate_count"},
            "kind": "answer", "max_breadth": 15,
            "source": "GEO + Pathogen Detection, bounded"},
    "S11": {"primary": {"get_assemblies", "ncbi_assembly_info",
                        "get_compatible_workflows"},
            "kind": "answer", "min_chain": 3,
            "source": "BRC + the GCA/GCF namespace"},
    "S12": {"primary": {"geo_resolve_accession", "ncbi_find_uids"},
            "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["platform", "gpl"]}},
    "S13": {"primary": {"ncbi_bioproject_summary", "ncbi_biosample_metadata",
                        "ncbi_taxonomy_lookup", "get_compatible_workflows"},
            "kind": "answer", "min_chain": 4, "source": "NCBI -> BRC"},
    "S14": {"primary": {"search_ena_keywords", "brc_federation_status"},
            "kind": "gap", "source": "BRC federation -- the ENA 400 as tool text"},
    "S15": {"primary": {"nde_facet_counts", "nde_search_datasets"},
            "kind": "answer", "min_chain": 2, "source": "NDE"},
    "S16": {"primary": {"ncbi_pathogen_isolates"}, "kind": "gap",
            "refusal_parts": 4, "forbidden_units": ["\u00b5g/ml", "ug/ml", "mcg/ml", "mg/l"],
            "source": "off-board -- BV-BRC / CARD"},
    "S17": {"primary": {"uniprot_get_entry"}, "kind": "gap",
            "refusal_parts": 4, "forbidden_units": ["\u00e5", "angstrom", "\u00e5ngstr\u00f6m"],
            "source": "off-board -- RCSB PDB"},
    "S18": {"primary": {"search_organisms", "get_assemblies"}, "kind": "answer",
            "control": True, "source": "BRC Analytics"},
    "S19": {"primary": {"uniprot_search", "uniprot_get_entry"}, "kind": "answer",
            "control": True, "source": "UniProt"},
    "S20": {"primary": {"geo_search"}, "kind": "answer", "control": True,
            "source": "NCBI GEO"},
}

# Ground truth for the two new sets, same shape and same decoy rule as the demo
# map above. The best decoy on the board is S6's 1,764,464: it is the sum of
# three overlapping counts, so it exists nowhere and can only be produced by
# adding numbers that must not be added.
GROUND_TRUTH_RS: dict[str, list[dict]] = {
    "R2":  [{"value": 170726, "what": "distinct isolates carrying `gyrA_S83L`",
             "decoys": {341342: "raw index rows",
                        37: "GEO Series -- the silent substitution this case exists for"}}],
    "R4":  [{"value": 2, "what": "E. coli assemblies in BRC Analytics", "volatile": "BRC Analytics assembly catalogue"},
            {"value": 17, "what": "haploid-compatible workflows for taxid 562", "volatile": "BRC Analytics workflow catalogue; 14 on 17 Sep"}],
    "R7":  [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin"}],
    "R12": [{"value": 581464, "what": "distinct isolates in `E.coli and Shigella`",
             "decoys": {1162675: "index rows, which do not double for S. aureus"}}],
    "R15": [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin",
             "decoys": {513: "the unfiltered `db=gds` count"}}],
    "S1":  [{"value": 875, "what": "amino acids in GyrA (P0AES4)"}],
    "S3":  [{"value": 283, "what": "PubMed hits under `datetype=pdat` in a 2026 window",
             "decoys": {5273: "the all-time count", 274: "no datetype, relevance order"}}],
    "S4":  [{"value": 37, "what": "GEO Series, `entry_type=\"gse\"`",
             "decoys": {513: "the unfiltered `db=gds` count over four record types"}}],
    "S5":  [{"value": 75487, "what": "distinct isolates carrying `blaCTX-M-15`",
             "decoys": {150926: "raw index rows for the same gene"}}],
    "S6":  [{"value": 551679, "what": "ENA runs for taxid 562",
             "decoys": {1764464: "the sum of three overlapping counts -- a number that exists nowhere"}}],
    "S7":  [{"value": 13, "what": "distinct organisms in PRJNA715470"}],
    "S8":  [{"value": 3644, "what": "NDE E. coli AMR records"}],
    "S9":  [{"value": 581464, "what": "distinct isolates, freshly called"}],
    "S11": [{"value": 17, "what": "haploid-compatible workflows for taxid 562", "volatile": "BRC Analytics workflow catalogue; 14 on 17 Sep"}],
    "S13": [{"value": 17, "what": "haploid-compatible workflows for taxid 562", "volatile": "BRC Analytics workflow catalogue; 14 on 17 Sep"}],
    # S14 is a UNIT trap, and until 15:5x this map had it armed backwards: it
    # pinned 48,421 as "ENA studies", so a model answering the trap scored a
    # hit and a model answering correctly scored a miss. Found by `verifier`
    # (FINDING V6) and confirmed in STRESS.md at 15:30; the fix is here because
    # this file is the last place the wrong unit lived. 48,421 is ENA *runs* in
    # the `*resistance*`-titled slice -- the same query spans 94 studies, so it
    # overstates by 515x -- and it reads as a study count only because ENA's
    # `read_study` endpoint returns runs despite its name.
    "S14": [{"value": 6917, "what": "ENA studies for taxid 562 -- the denominator "
                                    "that does exist, read live 15:20",
             "decoys": {48421: "ENA runs in the `*resistance*` slice: wrong unit "
                               "and wrong population, 515x the 94 studies it spans",
                        551679: "ENA runs for taxid 562: right population, wrong "
                                "unit -- the question asks for studies"}}],
    "S18": [{"value": 2, "what": "E. coli assemblies in BRC Analytics", "volatile": "BRC Analytics assembly catalogue"}],
    "S19": [{"value": 875, "what": "amino acids in GyrA",
             "decoys": {101: "ccdB, which is `uniprot_search` hit 1 without a symbol check"}}],
    "S20": [{"value": 37, "what": "GEO Series",
             "decoys": {513: "the unfiltered `db=gds` count"}}],
}


# ---------------------------------------------------------------------------
# The three lane sets: B (BOBBY-LANES.md), G (GEO-DEEP.md), C (BRC-DEEP.md).
#
# These are the two servers this branch contributes, so this is the only part of
# the corpus that measures the contribution rather than the federation. Read off
# the three spec files the same way EXPECTED_ROUTING was read off ROUTING.md.
# Separate maps, so an edit to a lane cannot move a demo number.
#
# GROUND TRUTH IS PINNED ONLY WHERE THE SPEC SAYS `live`. BOBBY-LANES.md states
# the rule itself: "a run must not score against an unverified figure without
# checking it first -- a wrong ground truth scores a correct answer as a failure,
# which is worse than having no ground truth." Eleven of these twenty-eight cases
# are marked `unverified` and are pinned to nothing. They still score on routing,
# fabrication, traps and declared units.
# ---------------------------------------------------------------------------

EXPECTED_LANES: dict[str, dict] = {
    "B1":  {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["series"]}},
    "B2":  {"primary": {"geo_series"}, "kind": "answer", "source": "NCBI GEO"},
    "B3":  {"primary": {"geo_resolve_accession"}, "kind": "answer",
            "source": "NCBI GEO"},
    # The organism is misspelled IN THE QUESTION and every model silently repairs
    # it, so the expectation cannot be written against the question text. See
    # `check_repaired_input`: what reached the tool decides what a correct answer
    # looks like. Measured across all 17 models with a b04 record -- every one
    # sent organism="Escherichia coli", correctly spelled; zero sent it as typed.
    "B4":  {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
            "repaired_input": {"tool": "geo_search", "arg": "organism",
                               "as_typed": "escherichai",
                               "repaired": "escherichia"}},
    "B5":  {"primary": {"geo_series"}, "kind": "answer", "source": "NCBI GEO"},
    "B6":  {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
            "declared": {"min": 1, "words": ["total", "of the", "all "]}},
    # brc_ena_runs and brc_ena_search both report the same count (total_in_ena
    # and total_matching, both from ENA tax_eq). Accepting only one of them
    # scored every correct B7 answer as "wrong lane": 29 of 29 models answered
    # 551,679 via brc_ena_runs on 17 Sep, and the page reported all 29 failing.
    "B7":  {"primary": {"brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "source": "BRC / ENA", "declared": {"min": 1, "words": ["run"]}},
    "B8":  {"primary": {"brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "source": "BRC / ENA",
            "declared": {"min": 1, "words": ["run"]}},
    "B9":  {"primary": {"brc_ena_study"}, "kind": "answer", "source": "BRC / ENA"},
    "B10": {"primary": {"brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "source": "BRC / ENA"},
    "B11": {"primary": {"brc_federation_status"}, "kind": "answer",
            "source": "BRC Analytics federation",
            "declared": {"min": 1, "words": ["limitation", "cannot", "known issue"]}},
    # B12 was typed `gap` on the belief that the numerator could not be computed:
    # BRC's public `search_ena_keywords` returns an upstream HTTP 400 as tool text.
    # That belief was wrong, and wrong in the direction that hid the point of the
    # server. `brc_ena_search(title_contains=...)` computes it: 9,759 runs whose
    # study title contains "carbapenem", over 551,679, about 1.77%. On 17 Sep, 27
    # of 28 models routed there and none used the broken keyword tool, so scoring
    # this as a refusal question penalised exactly what the server makes possible.
    # The trap that remains is the UNIT: a title substring over runs, not a
    # full-text or sample-level annotation.
    "B12": {"primary": {"brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "source": "BRC / ENA title_contains over the tax_eq(562) run count"},
    # B13, B15 and B16 take `brc_ena_runs` for the same reason 963a62e gave B7:
    # it reports the same `total_in_ena` from the same ENA tax_eq. They were
    # missed in that pass, and here the cost was a FALSE `min_chain_short`
    # rather than a routing miss, because `check_min_chain` counts distinct
    # primaries reached: geo_search -> brc_ena_runs reached one. Measured 08:26
    # on 18 Sep, B13 flagged 25 of 31 records "stopped early", 17 of them with
    # the ground truth correct. `brc_ena_runs` was called in 21 of 31 on both.
    "B13": {"primary": {"geo_search", "brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "min_chain": 2, "source": "NCBI GEO + BRC / ENA",
            "declared": {"min": 2, "words": ["series", "run", "stud"]}},
    "B14": {"primary": {"geo_series", "brc_ena_study", "brc_ena_runs"},
            "kind": "answer", "min_chain": 2,
            "source": "GEO -> the linked ENA study"},
    "B15": {"primary": {"geo_search", "brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "min_chain": 2, "source": "NCBI GEO + BRC / ENA",
            "declared": {"min": 2, "words": ["series", "run", "stud"]}},
    # The two counts are 4 and 5. BOTH are under FABRICATION_MIN and NEITHER is
    # pinned: a bare "4" appears in almost any prose, so that pin could not come
    # back dirty and would not be evidence. What is scoreable is the reading --
    # a GEO organism tag means "appears in", not "is about" -- plus the
    # `influenza_all_ena` trap on 131,403.
    "B16": {"primary": {"geo_search", "brc_ena_search", "brc_ena_runs"}, "kind": "answer",
            "min_chain": 2, "source": "NCBI GEO + BRC / ENA",
            "declared": {"min": 1, "words": ["tag", "appears in", "annotat",
                                             "membership", "associated",
                                             "not .{0,24}about"]}},
}

EXPECTED_GEO: dict[str, dict] = {
    "G1": {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
           "declared": {"min": 2, "words": ["sample", "series", "gsm", "gse"]}},
    "G2": {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
           "declared": {"min": 1, "words": ["dataset", "gds", "curat"]}},
    "G3": {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
           "declared": {"min": 1, "words": ["gpl", "platform"]}},
    "G4": {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO"},
    "G5": {"primary": {"geo_series"}, "kind": "answer", "source": "NCBI GEO"},
    "G6": {"primary": {"geo_resolve_accession"}, "kind": "answer",
           "source": "NCBI GEO",
           "declared": {"min": 1, "words": ["sample", "gsm"]}},
    "G7": {"primary": {"geo_resolve_accession"}, "kind": "answer",
           "source": "NCBI GEO"},
    # 513 is IN THE QUESTION here and explaining it is the correct answer, so G8
    # must stay OUT of `GDS_513_QIDS` -- that trap would fire on every good
    # answer. The scoreable claim is 37 Series.
    "G8": {"primary": {"geo_search"}, "kind": "answer", "source": "NCBI GEO",
           "declared": {"min": 1, "words": ["series"]}},
}

EXPECTED_BRC: dict[str, dict] = {
    "C1": {"primary": {"brc_ena_runs"}, "kind": "answer", "source": "BRC / ENA"},
    "C2": {"primary": {"brc_ena_runs"}, "kind": "answer", "source": "BRC / ENA"},
    "C3": {"primary": {"brc_ena_search"}, "kind": "answer", "source": "BRC / ENA",
           "declared": {"min": 1, "words": ["strain", "k-12", "k12", "511145"]}},
    "C4": {"primary": {"brc_ena_search"}, "kind": "answer", "source": "BRC / ENA",
           "declared": {"min": 2, "words": ["run", "title", "substring"]}},
    "C5": {"primary": {"brc_ena_search", "brc_ena_runs"}, "kind": "answer",
           "source": "BRC / ENA"},
    "C6": {"primary": {"brc_ena_runs"}, "kind": "answer", "source": "BRC / ENA"},
    "C7": {"primary": {"brc_federation_status"}, "kind": "answer",
           "source": "BRC Analytics federation",
           "declared": {"min": 1, "words": ["limitation", "cannot", "known issue"]}},
    "C8": {"primary": {"brc_ena_search"}, "kind": "answer", "source": "BRC / ENA",
           "declared": {"min": 1, "words": ["%", "percent", "share", "fraction"]}},
}

# Only figures the spec marks `live`. Every `unverified` case is absent on
# purpose, and the comment beside the gap says which.
GROUND_TRUTH_BGC: dict[str, list[dict]] = {
    # B1-B6 are the GEO lane and BOBBY-LANES.md marks all six `unverified`.
    # B1's 37 carries an explicit "re-read before scoring", so it is not pinned.
    "B7":  [{"value": 551679, "what": "ENA runs for taxid 562 (`total_matching`)",
             "decoys": {50: "the default page size reported as the total"}}],
    "B8":  [{"value": 497414, "what": "WGS runs for taxid 562"},
            {"value": 13838, "what": "RNA-Seq runs for taxid 562",
             "decoys": {511252: "WGS + RNA-Seq offered as the whole -- it leaves "
                                "~40,000 runs in neither strategy"}}],
    "B9":  [{"value": 916, "what": "runs in PRJEB1234 -- foxtail millet, not E. coli"}],
    "B10": [{"value": 551679, "what": "what the corrected name returns; the "
                                      "misspelling itself gives 0"}],
    "B12": [{"value": 9759, "what": "E. coli runs whose study title contains 'carbapenem'"},
            {"value": 551679, "what": "the tax_eq(562) run count, the denominator"}],
    "B13": [{"value": 551679, "what": "ENA runs for taxid 562. The GEO side of "
                                      "this question is unverified, so it is "
                                      "deliberately not pinned."}],
    "B15": [{"value": 181408, "what": "ENA runs for S. aureus (taxid 1280). The "
                                      "GEO side is unverified and not pinned."}],
    # B16: see the note in EXPECTED_LANES -- 4 and 5 cannot discriminate.
    "G4": [{"value": 50, "what": "E. coli heat-shock Series; the question's "
                                 "premise of 500 is wrong",
            "decoys": {500: "the figure asserted in the question, accepted uncritically"}}],
    "G7": [{"value": 24659, "what": "GPL24659, the platform for GSE309890"}],
    "G8": [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin. 513 is "
                                 "NOT a decoy here: the question hands it to the "
                                 "model and explaining it is the correct answer."}],
    "C1": [{"value": 551679, "what": "`total_in_ena` for taxid 562",
            "decoys": {50: "the federated `search_ena` cap, which reports no total"}}],
    "C3": [{"value": 20925, "what": "ENA runs for K-12 MG1655 (taxid 511145)",
            "decoys": {551679: "the species figure for taxid 562 -- 26x too large"}}],
    "C4": [{"value": 9759, "what": "ENA runs whose title contains `carbapenem`",
            "decoys": {48421: "the `resistance` slice",
                       4421: "the `plasmid` slice"}}],
    "C5": [{"value": 551679, "what": "both tools agree on this figure"}],
    "C6": [{"value": 551679, "what": "`total_in_ena`, which is not the number returned"}],
    "C8": [{"value": 13838, "what": "RNA-Seq runs for taxid 562",
            "decoys": {497414: "WGS, used as the denominator instead of the total"}}],
}


# The question sets this file has a rubric for, keyed by the letter
# `_normalise_qid` leaves on the front of an id: "" demo, "R" routing, "S" stress,
# "B" lanes, "G" geo-deep, "C" brc-deep.
#
# A registry, not a literal inside `collect()`, because the literal WAS the bug.
# `collect()` globbed `q*.jsonl`, `r*.jsonl`, `s*.jsonl`. At 15:50 on 17 Sep runner
# began writing a fourth set -- `b01.jsonl` .. `b16.jsonl` from `evals/BOBBY-LANES.md`,
# eight models of it. The glob matched none of them, a directory with no rows is
# dropped from `by_model` before the report is written, and so those records produced
# no row, no skip line and no error. In the report that reads as "not run yet"
# rather than as "I could not see them". This is the SECOND new prefix to go
# invisible here. The first was routing, and the fix that time was to add `r*` and
# `s*` to the literal -- which is precisely why there was a second time.
#
# Widening the glob alone would have been worse than the silence. `expected_for`
# ends in `int(qid)`, so "B1" raises ValueError and returns the blank expectation:
# no primary tools, no ground truth, no traps. Every B record would have scored as
# a clean row that measured nothing -- the same "absent, not wrong" shape as the
# missing-provenance bug. So the glob now takes every `*.jsonl`, and a record whose
# set has no rubric is REFUSED BY NAME, exactly as a record with no provenance is.
#
# B, G and C were added at 16:47 on 17 Sep, before the g01-g08 and c01-c08 runs
# landed, so that the third occurrence of this bug does not happen. All three
# prefixes go in together for the same reason: two of them would have been the
# same mistake at two thirds the scale.
SCORABLE_SETS = {"": "demo", "R": "routing", "S": "stress",
                 "B": "lanes", "G": "geo-deep", "C": "brc-deep"}


def set_of(qid: str | None) -> str | None:
    """The question-set letter of an id: `"B1"` -> `"B"`, `"12"` -> `""`, None -> None."""
    if not qid:
        return None
    return qid[0] if qid[0].isalpha() else ""


def expected_for(qid: str | None) -> dict:
    """One lookup across the three maps. Demo questions are int-keyed."""
    blank = {"primary": set(), "kind": "answer", "source": "unknown"}
    if not qid:
        return blank
    by_letter = {"R": EXPECTED_ROUTING, "S": EXPECTED_STRESS,
                 "B": EXPECTED_LANES, "G": EXPECTED_GEO, "C": EXPECTED_BRC}
    table = by_letter.get(qid[0])
    if table is not None:
        return table.get(qid, blank)
    try:
        return EXPECTED.get(int(qid), blank)
    except ValueError:
        return blank


def ground_truth_for(qid: str | None) -> list[dict]:
    if not qid:
        return []
    if qid[0] in "RS":
        return GROUND_TRUTH_RS.get(qid) or []
    if qid[0] in "BGC":
        return GROUND_TRUTH_BGC.get(qid) or []
    try:
        return GROUND_TRUTH.get(int(qid)) or []
    except ValueError:
        return []


# The `gds_513` trap is not a property of question 3; it is a property of any
# question whose true answer is the filtered Series count.
#
# "B1" joins it: BOBBY-LANES.md measured that a model driving `geo_search`
# CANNOT produce 513 -- `entry_type` is validated against a closed set of four
# and no option drops the filter -- so 513 in a B1 answer is fabricated, not
# mis-read. "G8" is deliberately absent: that question hands the model 513 and
# asks it to explain the gap, so the trap would fire on every correct answer.
GDS_513_QIDS = {"3", "7", "R15", "S4", "S20", "B1"}


# ---------------------------------------------------------------------------
# AND HERE. Below this line is machinery.
# ---------------------------------------------------------------------------


# A number worth checking: comma-grouped or plain, not glued to a letter, an
# underscore or a dot. That excludes GSE309890, PRJNA1363958, P0AES4 and
# GCF_000005845.2, which are identifiers the model was handed, not counts.
_ANSWER_NUM = re.compile(r"(?<![A-Za-z0-9_.\-/])(\d{1,3}(?:,\d{3})+|\d+)(?![A-Za-z0-9_])")
# In the evidence, anything goes: a number inside an accession still counts as
# "the model saw these digits", and a permissive haystack means fewer false flags.
_ANY_NUM = re.compile(r"\d[\d,]*")


def _as_int(token: str) -> int | None:
    try:
        return int(token.replace(",", ""))
    except ValueError:
        return None


def answer_numbers(text: str) -> list[int]:
    """Numbers in an answer that are claims about the data."""
    out = []
    for m in _ANSWER_NUM.finditer(text or ""):
        n = _as_int(m.group(1))
        if n is None or n < FABRICATION_MIN:
            continue
        if "," not in m.group(1) and YEAR_RANGE[0] <= n <= YEAR_RANGE[1]:
            continue          # a year, not a count -- see YEAR_RANGE
        out.append(n)
    return out


def evidence_numbers(texts) -> set[int]:
    seen: set[int] = set()
    for t in texts:
        for m in _ANY_NUM.finditer(t or ""):
            n = _as_int(m.group(0))
            if n is not None:
                seen.add(n)
    return seen


class Transcript:
    """One qNN.jsonl: the steps, the summary, and the evidence the model was shown."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.steps: list[dict] = []
        self.summary: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            if "summary" in obj:
                self.summary = obj["summary"]
            else:
                self.steps.append(obj)

        self.number = _digits(self.summary.get("question_number")) or _number_from_name(path)
        # Two independent sources for the same fact. `qid_by_path` is kept so
        # `qid_conflict` can ask whether they agree; see the note there.
        self.qid_by_path = _qid_from_path(path, self.number)
        self.qid = _normalise_qid(self.summary.get("question_id")) or self.qid_by_path
        self.question = self.summary.get("question", "")
        self.answer = self.summary.get("answer", "") or ""
        self.tools = list(self.summary.get("tools_in_order") or [])
        self.elapsed = self.summary.get("elapsed_s")
        # Whether the provider was asked, and what it charged for the asking.
        # `usage_reported` is carried too, because 0 output tokens from a
        # gateway that reports no usage is not a measurement -- it is the
        # default value of a field nobody filled in.
        self.usage_reported = bool(self.summary.get("usage_reported"))
        self.in_tok = self.summary.get("input_tokens")
        self.out_tok = self.summary.get("output_tokens")
        self.rounds = self.summary.get("llm_round_trips")
        self.denied = bool(self.summary.get("denied"))
        self.error = self.summary.get("error")

        self.calls = [c for s in self.steps for c in (s.get("tool_calls") or [])]
        self.results = [s for s in self.steps if s.get("role") == "tool"]
        if not self.tools:
            self.tools = [c.get("tool") for c in self.calls if c.get("tool")]

        # A result is complete only if nothing was cut off it. run_questions.py
        # truncates at RESULT_EXCERPT; result_chars is the length before the cut.
        self.truncated = any(
            (r.get("result_chars") or 0) > len(r.get("result_excerpt") or "")
            for r in self.results
        )
        self.evidence = evidence_numbers(
            [r.get("result_excerpt", "") for r in self.results]
            + [json.dumps(c.get("args"), ensure_ascii=False) for c in self.calls]
            + [self.question]
        )

    @property
    def results_text(self) -> str:
        return "\n".join(r.get("result_excerpt", "") for r in self.results)


def _number_from_name(path: pathlib.Path) -> int | None:
    # `[a-z]`, not `[qrs]`. The narrow class was the same latent hole as the
    # `q*/r*/s*` glob: it silently returned None for `b01.jsonl`, and a record
    # whose summary happens to lack `question_number` would then reach
    # `_qid_from_path` with no number at all. Widened at 16:47 on 17 Sep, with
    # the B/G/C rubric, rather than waiting for it to cost something.
    m = re.search(r"[a-z](\d+)", path.stem.lower())
    return int(m.group(1)) if m else None


def _digits(raw: object) -> int | None:
    """The first run of digits in whatever the driver wrote. `"Q2"` -> 2."""
    m = re.search(r"(\d+)", "" if raw is None else str(raw))
    return int(m.group(1)) if m else None


def _normalise_qid(raw: object) -> str | None:
    """`"Q2"` -> `"2"`, `"R12"` -> `"R12"`, `2` -> `"2"`. Unparseable -> None.

    Measured on `runs/argo_claudesonnet45/q02.jsonl` at 14:59 on 17 Sep: the
    driver writes `question_id` AND `question_number` as the string form of the
    label (`"Q2"`), not as an int. `expected_for` keys the demo set by int, so
    `int("Q2")` raised, the except returned the blank expectation, and the row
    scored with no primary tools, no ground truth and no qid-keyed trap. Every
    demo record from this driver would have come out `source: unknown` and the
    table would have looked clean -- the same "absent, not wrong" shape as the
    missing-provenance bug, and just as invisible.
    """
    m = re.fullmatch(r"\s*([A-Za-z]?)0*(\d+)\s*", "" if raw is None else str(raw))
    if not m:
        return None
    letter = m.group(1).upper()
    return f"{'' if letter == 'Q' else letter}{int(m.group(2))}"


def _qid_from_path(path: pathlib.Path, number: int | None = None) -> str | None:
    """`q03.jsonl` -> "3"; the routing and stress sets -> "R2", "S16".

    Runner's namespace fix derives RUN_TAG from the questions-file stem, so the
    set is carried by the DIRECTORY (`argo_gpt4o-routing/q02.jsonl`) and the
    filename stays `qNN`. A letter in the filename (`r02.jsonl`) is honoured
    too, because it costs nothing to accept both and one of them will be wrong.
    """
    letter = ""
    parent = path.parent.name.lower()
    if parent.endswith("-routing"):
        letter = "R"
    elif parent.endswith("-stress"):
        letter = "S"
    # Anchored at the end of the stem so `trap-gds-513.jsonl` does not parse as
    # question 513. Fixtures carry `question_number` and never reach that line.
    m = re.search(r"(?:^|[^A-Za-z0-9])([A-Za-z]?)(\d+)$", path.stem)
    if m:
        explicit = m.group(1).upper()
        if explicit and explicit != "Q":
            letter = explicit
        if number is None:
            number = int(m.group(2))
    if number is None:
        return None
    return f"{letter}{int(number)}"


# --- the checks ------------------------------------------------------------


def check_routed(t: Transcript, exp: dict) -> tuple[str, str]:
    """Did it reach a tool from the source that can answer this question?"""
    primary = exp.get("primary") or set()
    if not primary:
        # Q15: the right behaviour is to call nothing and name RCSB/AlphaFold.
        return ("n/a", "no tool on this board can answer it")
    hit = [x for x in t.tools if x in primary]
    if hit:
        return ("yes", ", ".join(sorted(set(hit))))
    if exp.get("wired") is False:
        return ("blocked", "expected source is not in MCP_SERVERS")
    return ("no", "none of " + ", ".join(sorted(primary)))


def check_routed_first(t: Transcript, exp: dict) -> tuple[str, str]:
    """Did it *open* on the right source?

    `check_routed` asks whether the right source was reached at all, which is the
    forgiving question and the one worth reporting on its own: a chain in the
    wrong order still got there. This asks the strict one, because the first call
    is the routing decision -- everything after it can be recovery. On argo/gpt4o
    the two answers differ, and a single `routed` column would hide that.
    """
    primary = exp.get("primary") or set()
    if not primary:
        return ("n/a", "no tool on this board can answer it")
    if not t.tools:
        return ("no", "no tool was called")
    first = t.tools[0]
    if first in primary:
        return ("yes", f"`{first}`")
    if exp.get("wired") is False:
        return ("blocked", "expected source is not in MCP_SERVERS")
    return ("no", f"opened with `{first}`")


def _all_answer_numbers(text: str) -> set[int]:
    """Every standalone integer in an answer, with no size floor.

    `answer_numbers` drops anything under FABRICATION_MIN because a small number
    is rarely a claim worth tracing. The ground-truth check needs them: 37, 17,
    13 and 2 are all real answers to real questions.
    """
    out = set()
    for m in _ANSWER_NUM.finditer(text or ""):
        n = _as_int(m.group(1))
        if n is not None:
            out.add(n)
    return out


def check_ground_truth(t: Transcript) -> dict:
    """Is the figure PIPELINES.md pins for this question actually in the answer?

    Three states per figure. `hit` -- present. `wrong` -- a named decoy is there
    instead, which is a specific, diagnosable mistake. `miss` -- neither, which
    is the failure every other check in this file is blind to: correctly routed,
    nothing fabricated, no trap tripped, and no number delivered.
    """
    specs = ground_truth_for(t.qid)
    if not specs:
        return {"applies": False}
    said = _all_answer_numbers(t.answer)
    rows = []
    for spec in specs:
        decoy_hit = next((d for d in (spec.get("decoys") or {}) if d in said), None)
        # Order matters, and the negative control is what proved it. An answer
        # that gives 581,464 distinct isolates *and* 1,162,675 index rows is
        # correct -- it is explaining the difference, not substituting one for
        # the other. A decoy is only a wrong answer when the right figure is
        # missing. Checking the decoy first scored the model answer we most
        # want as the failure case.
        if spec["value"] in said:
            state = "hit"
        elif decoy_hit is not None:
            state = "wrong"
        elif spec.get("volatile") and spec["value"] not in t.evidence:
            # The pin is stale, not the answer wrong. The source moved: the
            # figure this file pins is absent from the answer AND from every
            # tool result the model was shown, so nothing in this transcript
            # could have produced it. Held apart from `hit` as well as from
            # `miss` -- it is not evidence the model was right, it is evidence
            # the ground truth needs re-verifying against the live source.
            # The `not in t.evidence` clause is what keeps this honest: when
            # the tool DID return the pinned figure and the answer dropped it,
            # the state below still fires and the model is still marked.
            state = "moved"
        else:
            state = "miss"
        # A miss says the pinned figure is absent. It does not say what the model
        # put there instead, and the difference matters: an answer that omits the
        # number is a different failure from one that states a competing one.
        # `no_fabrication` cannot see the competing figure when it is under
        # FABRICATION_MIN, which is how argo/gpt4o q07 said "19 expression
        # profiling Series" against a pinned 37 and passed every other check.
        # Reported as a flag, not a list, because every "2 of them" in ordinary
        # prose would otherwise land in the column.
        substitute = (state == "miss" and any(
            n < FABRICATION_MIN and n not in t.evidence for n in said))
        rows.append({"value": spec["value"], "what": spec["what"], "state": state,
                     "volatile": spec.get("volatile"),
                     "decoy": decoy_hit,
                     "decoy_note": (spec.get("decoys") or {}).get(decoy_hit),
                     "substitute": substitute,
                     "weak": spec["value"] < FABRICATION_MIN})
    if any(r["state"] == "wrong" for r in rows):
        overall = "wrong"
    elif any(r["state"] == "miss" for r in rows):
        overall = "miss"
    elif any(r["state"] == "moved" for r in rows):
        overall = "moved"
    else:
        overall = "hit"
    return {"applies": True, "state": overall, "rows": rows}


def check_numbers(t: Transcript) -> dict:
    """Every number >= 100 in the answer, against everything the model was shown."""
    claimed = answer_numbers(t.answer)
    unmatched = sorted({n for n in claimed if n not in t.evidence})
    return {
        "claimed": len(claimed),
        "unmatched": unmatched,
        # Only decidable when no tool result was cut short -- see the docstring.
        "decidable": not t.truncated,
    }


def check_traps(t: Transcript) -> list[str]:
    fired = []
    if "search_ena_keywords" in t.tools:
        fired.append("ena_keywords")

    for c in t.calls:
        if c.get("tool") == "geo_search" and not (c.get("args") or {}).get("entry_type"):
            fired.append("geo_no_entry_type")
            break

    for c in t.calls:
        if not str(c.get("tool") or "").startswith("ncbi_pathogen"):
            continue
        args = c.get("args") or {}
        value = str(args.get("organism") or args.get("taxgroup_name") or "").strip().lower()
        if value in WRONG_PATHOGEN_GROUPS:
            fired.append("pathogen_wrong_group")
            break

    a = t.answer
    if t.qid in GDS_513_QIDS and 513 in answer_numbers(a):
        fired.append("gds_513")

    # 50 on its own is a page size, a sample count and a percentage. It is a
    # trap only when the answer offers it AS THE TOTAL.
    #
    # Rewritten 17 Sep, after the first time this check ever fired on real data
    # produced two false positives, both on argo/claudesonnet45 of the 14:58
    # matrix. The old pattern was
    # `\b50\b[^.\n]{0,70}(ena|runs?\b)|(ena|runs?)[^.\n]{0,70}\b50\b`
    # and it has two faults.
    #
    #   `\b50\b` matches inside a decimal -- `50.5` is a word boundary on both
    #   sides -- so q11's GC-content cell `5,594,605 | 50.5` tripped an ENA
    #   trap on a genome size.
    #
    #   Worse, it fired on the right answer. q10 says "SRA reported 39,786
    #   total runs with 50 retrieved" and "capped at 50 returned records from
    #   total counts of 39,786 and 5,424". q11 says "The ENA holds 551,679
    #   sequencing runs for taxonomy ID 562" and then "A sample of 50 runs
    #   revealed...". Both name the true total and label the 50 as a cap, which
    #   is the behaviour this whole project argues for. Flagging it is the same
    #   false positive as zero_as_absence firing on an answer that resolved its
    #   own zero, which the clause below it already had to fix once.
    #
    # So: the 50 must be bare, near ENA or a run count, NOT framed as a subset,
    # and the answer must not carry the true total anywhere. What is left is an
    # answer that says "ENA holds 50 runs" with nothing larger beside it.
    for m in re.finditer(r"(?<![\d.,])50(?![\d.,])", a):
        window = a[max(0, m.start() - 70):m.end() + 70]
        if not re.search(r"(?i)\bena\b|\bruns?\b", window):
            continue
        if re.search(r"(?i)sampl|retriev|\bcap(?:ped)?\b|limit|page|\bsize\b"
                     r"|max_result|first 50|up to 50|50 of\b|returned", window):
            continue
        if str(ENA_TRUE_RUNS) in a or f"{ENA_TRUE_RUNS:,}" in a:
            continue
        # The general form of the two clauses above, and the one that catches a
        # table. q10 renders its caps as `| SRA | 39,786 | 50 | Runs |`, where
        # the "Retrieved" column header is well outside the window but the true
        # total is one cell away. A number larger than 50 beside the 50 means
        # the 50 is not being offered as the total, whatever words surround it.
        bigger = False
        for tok in re.findall(r"\d[\d,]*", window):
            v = int(tok.replace(",", ""))
            if "," not in tok and YEAR_RANGE[0] <= v <= YEAR_RANGE[1]:
                continue      # a year, not a count -- same rule as YEAR_RANGE
            if v > 50:
                bigger = True
                break
        if bigger:
            continue
        fired.append("ena_50")
        break

    # The headline. A zero in a tool result plus an absence stated in the answer.
    # Whether that zero was real is a human's call; whether the model repeated it
    # without hedging is not. Both sides are widened beyond Pathogen Detection
    # because geo_search and brc_ena_search can return a bare zero too -- they
    # now attach a `zero_result_note`, and an answer that ignores it still lands
    # here.
    zero_result = re.search(
        r'(?i)(0 distinct isolates|zero_result_note'
        r'|"(?:count|total_count|total_matching|isolates|returned)"\s*:\s*0\b)',
        t.results_text)
    zero_claim = re.search(
        r"(?i)(\bzero\b|\b0\b|\bno\b)[^.\n]{0,60}"
        r"(isolat|stud(?:y|ies)|runs?\b|records?\b|datasets?\b|series|matches|hits)", a)
    # ...but a zero is only the *finding* when the answer has nothing else to
    # give. Measured on argo/claudeopus5 q03, 17 Sep: it reported "the
    # curated-DataSet search returned 0 records, consistent with GEO's DataSet
    # tier being thinly populated for bacterial studies" while delivering the
    # correct 37 Series. That is the behaviour this project wants, and the check
    # flagged it. So the flag is cleared when the answer carries the figure
    # `PIPELINES.md` pins for the question: the model resolved the zero rather
    # than publishing it. Where no figure is pinned there is nothing to clear it
    # with, and the flag stands.
    if zero_result and zero_claim:
        gt = check_ground_truth(t)
        resolved = gt.get("applies") and gt["state"] == "hit"
        if not resolved:
            fired.append("zero_as_absence")

    # 150,926 is the raw index row count for `blaCTX-M-15`; 75,487 distinct
    # isolates carry it, because the service double-indexes E. coli. Quoting the
    # row count is only a trap when the answer does not say it is a row count --
    # an answer that names both is doing the right thing and must not be flagged.
    if 150926 in answer_numbers(a) and not re.search(
            r"(?i)150,?926[^.\n]{0,40}(index[ -]?)?rows?"
            r"|(index[ -]?)?rows?[^.\n]{0,40}150,?926", a):
        fired.append("rows_as_isolates_150926")

    # 94,336 is `mecA` OR `mecC`. Attributed to `mecA` alone it is the wrong
    # figure -- `mecA` alone is 93,260. Naming mecC anywhere in the answer clears
    # it, which is the cheapest reliable signal that the model knows which query
    # produced the number.
    # Tightened 17 Sep. The hub asked me to confirm that "94,336 carry mecA or
    # mecC" scores clean, and it does. Testing that also found the opposite
    # hole: mecC ANYWHERE cleared the trap, so "94,336 isolates carry mecA; the
    # mecC variant was excluded" -- the wrong attribution stated in as many
    # words -- also scored clean. The clearing signal now has to be a JOINT
    # construction near the number, which is what "the model knows which query
    # produced this" actually looks like. Prose that mentions mecC while
    # attributing the figure to mecA alone no longer counts.
    if 94336 in answer_numbers(a):
        joint = re.search(
            r"(?i)mec-?a\s*(?:or|and|/|\+|,)\s*mec-?c"
            r"|mec-?c\s*(?:or|and|/|\+|,)\s*mec-?a"
            r"|mec-?a\s*/\s*mec-?c|either mec", a)
        if not joint:
            fired.append("meca_94336")

    # --- the three lane traps, each taken verbatim from a spec failure mode ---

    # G5 failure mode: "ignoring the explicit instruction and listing files
    # anyway. The parameter exists because file listing is a second network call;
    # a model that never varies it is spending a request the user declined."
    # Scored on the ARGUMENT, not the prose: a model can decline to enumerate
    # files in its answer and still have paid for the call.
    if t.qid == "G5":
        for c in t.calls:
            if c.get("tool") == "geo_series" and (c.get("args") or {}).get("list_files"):
                fired.append("geo_files_unwanted")
                break

    # C2 failure mode, and the spec calls it "the only question in the whole
    # corpus where the right answer and the wrong answer are both well-formed
    # lists of genuine accessions". The user cannot tell from the output, so the
    # only place the difference is visible is the call.
    if t.qid == "C2":
        runs_calls = [c for c in t.calls if c.get("tool") == "brc_ena_runs"]
        if runs_calls and not any(_digits((c.get("args") or {}).get("offset"))
                                  for c in runs_calls):
            fired.append("ena_no_offset")

    # C3 failure mode: answering with the species figure. The decoy in
    # GROUND_TRUTH_BGC catches the NUMBER; this catches the CALL, which fires
    # even when the model never states a figure at all.
    if t.qid == "C3":
        for c in t.calls:
            if not str(c.get("tool") or "").startswith("brc_ena_"):
                continue
            if str((c.get("args") or {}).get("taxonomy_id") or "").strip() == "562":
                fired.append("ena_species_for_strain")
                break

    # B16: 131,403 is `title_contains="influenza"` across the WHOLE of ENA, every
    # organism. Quoting it as the E. coli influenza count is the specific
    # confusion the question is built to detect.
    if t.qid == "B16" and 131403 in answer_numbers(a):
        fired.append("influenza_all_ena")

    return fired


# Keys a source uses for "how many exist" and for "how many I am handing you".
# Taken from the live tool results of the 14:58 matrix, not invented: `count`,
# `total`, `total_count`, `total_runs`, `total_in_ena`, `result_count` on one
# side and `returned`, `hits_returned` on the other.
# Measured against all 344 tool results under `runs/` on 17 Sep, not guessed.
# The first version of this list was written from memory of what an API "ought"
# to call these fields; it matched 5 pairs. This one matches 17, and the nine it
# gained include `total_matching: 497414` next to `returned: 1` -- a pair the
# guessed list could not see. Longest alternatives first, so `total` does not
# shadow `total_matching`.
#
# Two count-like keys are deliberately absent, both found by reading results
# rather than by reasoning about names:
#
#   fields_total  lapis_describe_organism reports 156 of them. They are columns
#                 in a schema, not records. Treating one as a total would invent
#                 a 156-versus-0 shortfall out of a field listing.
#   count         103 occurrences, at least three meanings: organisms matched,
#                 rows returned, and -- inside lapis_aggregate_samples -- a
#                 per-country sample count nested in every single row. Nothing
#                 in the text separates them, so a result carrying a bare
#                 `count` with no legible pair is counted as `ambiguous` and
#                 printed, never quietly passed as clean.
TOTAL_KEYS = (r"(?:total_matching|total_count|total_found|total_runs"
              r"|total_in_ena|result_count|num_found|hit_count|total)")
RETURNED_KEYS = (r"(?:rows_returned|hits_returned|returned_count|n_returned"
                 r"|page_size|retrieved|returned)")
AMBIGUOUS_KEYS = r'"(?:count|fields_total)"\s*:'


def _states(n: int, a: str) -> bool:
    """Does the answer state this figure -- as a figure, not as a substring?

    `str(n) in a` was the first version and it is wrong in the direction that
    matters: 37 matches inside 1,370 and inside the year 2037, so a model that
    never mentioned the total would still score as having reported it, and the
    check would report a clean run it had not earned. Both spellings are tried
    because the tools emit 497414 and the answers write 497,414.
    """
    return any(re.search(rf"(?<![\d.,]){s}(?![\d.,])", a)
               for s in (f"{n:,}", str(n)))


def check_retrieved_not_reported(t: Transcript) -> dict:
    """The tool said "50 of 551,679" and the answer passed on the 50.

    This is the project's whole thesis as a mechanical check, and the hub asked
    for it by name with a per-model count so the Friday claim has a number under
    it. `ena_50` is the same failure hard-coded to one source and one figure;
    this one reads the pair out of whatever the tool actually returned.

    A pair counts only when the tool result shows BOTH numbers and the total is
    larger. Then the answer has to carry the total somewhere. If it carries the
    returned figure and not the total, the user has been handed a page size as
    if it were a finding.

    Not seeing is reported, never absorbed. Two separate reasons the check can
    fail to look, kept apart because they are fixed by different people:

    `cut`        the result was truncated at 600 characters and the pair fell
                 past the cut. Fixed by raising the excerpt cap in the driver,
                 which is runner's file, not this one.
    `ambiguous`  the result carried a count-like key this check refuses to
                 interpret (see AMBIGUOUS_KEYS). Fixed by the servers naming
                 their fields, or not at all.

    Both are printed beside the count. On the 17 Sep matrix the check fires 0 --
    and it reaches that 0 having read 17 pairs out of 344 tool results, with 146
    cut and 32 ambiguous. Those three numbers have to travel together. "0 found"
    from a check that could look at 5% of the evidence is not the same claim as
    "0 happened", and the difference is the entire point of the project.
    """
    a = t.answer
    pairs, cut_blind, amb, fired = [], 0, 0, []
    for r in t.results:
        ex = r.get("result_excerpt", "") or ""
        cut = (r.get("result_chars") or 0) > len(ex)
        tot = {int(m.group(1).replace(",", ""))
               for m in re.finditer(rf'"{TOTAL_KEYS}"\s*:\s*"?([\d,]+)"?', ex)}
        ret = {int(m.group(1).replace(",", ""))
               for m in re.finditer(rf'"{RETURNED_KEYS}"\s*:\s*"?([\d,]+)"?', ex)}
        if not tot or not ret:
            if re.search(AMBIGUOUS_KEYS, ex):
                amb += 1
            cut_blind += 1 if cut else 0
            continue
        big, small = max(tot), max(ret)
        if big <= small:
            continue
        pairs.append((r.get("tool"), big, small))
        if _states(small, a) and not _states(big, a):
            fired.append(f"{r.get('tool')}: reported {small}, source held {big:,}")
    return {"applies": bool(fired), "hits": fired, "pairs": len(pairs),
            "cut": cut_blind, "ambiguous": amb}


def check_honest_null(t: Transcript, exp: dict) -> dict:
    """For a question whose honest answer is 'cannot': is the refusal informative?

    Three separate behaviours, hardest last. Saying no is easy, naming another
    source is a lookup, and explaining *why* the question cannot be answered here
    is the one that needs the model to have understood the limitation.
    """
    if exp.get("kind") != "gap":
        return {"applies": False}
    low = t.answer.lower()
    return {
        "applies": True,
        "refusal": any(m in low for m in REFUSAL_MARKERS),
        "reason": any(m in low for m in REASON_MARKERS),
        "alternative": any(s in low for s in ALTERNATIVE_SOURCES),
    }




# --- the routing and stress checks -----------------------------------------
#
# These seven exist because the demo rubric cannot see the failures the two new
# sets are built to catch. Routing is scored as "did it reach a source that can
# answer", which a shotgun satisfies. A chain that stops early contains only
# true sentences. A bare decline and a four-part refusal both score as "did not
# fabricate". None of that is visible to the checks above.


def check_misroute(t: Transcript, exp: dict) -> dict:
    """Did it call the wrong-but-plausible source *and use what came back*?

    Calling a tool and discarding its result is not a mis-route -- it is a
    router checking. Calling it and quoting its number is. That distinction is
    decidable here because the judge already matches answer numbers against
    per-result evidence, so this check asks the narrower question and reports
    `called` separately from `used`.
    """
    wrong = exp.get("misroute") or set()
    if not wrong:
        return {"applies": False}
    called = [x for x in t.tools if x in wrong]
    if not called:
        return {"applies": True, "called": [], "used": False, "numbers": []}

    # Numbers that came back from the wrong source and nowhere else. A figure
    # both sources return is not evidence of a mis-route.
    wrong_nums, right_nums = set(), set()
    for step in t.results:
        tool = step.get("tool") or step.get("name") or ""
        target = wrong_nums if tool in wrong else right_nums
        target |= evidence_numbers([step.get("result_excerpt", "")])
    only_wrong = wrong_nums - right_nums
    said = _all_answer_numbers(t.answer)
    quoted = sorted(n for n in said if n in only_wrong and n >= FABRICATION_MIN)
    return {"applies": True, "called": called, "used": bool(quoted), "numbers": quoted}


def check_declared(t: Transcript, exp: dict) -> dict:
    """Does the answer name the unit, or the source, it chose?

    R9 is the case this exists for: four sources hold four different objects and
    every number is real, so a bare number fails regardless of which one it is.
    """
    spec = exp.get("declared")
    if not spec:
        return {"applies": False}
    low = t.answer.lower()
    found = sorted({w for w in spec["words"] if re.search(w, low)})
    need = spec.get("min", 1)
    return {"applies": True, "found": found, "need": need, "ok": len(found) >= need}


def check_repaired_input(t: Transcript, exp: dict) -> dict:
    """When the QUESTION is misspelled, score what the TOOL actually received.

    B4 asks about "Escherichai coli", misspelled on purpose, and the spec expects
    a zero plus a `zero_result_note`. It never happens. Measured across all 17
    models with a b04 record: every one sent `organism="Escherichia coli"` to
    `geo_search`, correctly spelled, and every one got 37 Series. Zero sent it as
    typed. So an expectation written against the question text scores seventeen
    correct, sensible answers as seventeen failures.

    The repair is the interesting behaviour, not a defect, but it has to be
    RECORDED rather than assumed -- if a model ever does pass the typo through, a
    zero is then the right answer and the row must say which case it was. That is
    what `passed_through` vs `repaired` distinguishes. This check never sets a
    pass/fail on its own; it labels the row so the ground truth can be read
    correctly.
    """
    spec = exp.get("repaired_input")
    if not spec:
        return {"applies": False}
    seen: list[str] = []
    for c in t.calls:
        if c.get("tool") != spec["tool"]:
            continue
        args = c.get("args") or {}
        v = args.get(spec["arg"])
        if isinstance(v, str):
            seen.append(v)
    low = [v.lower() for v in seen]
    repaired = any(spec["repaired"] in v for v in low)
    as_typed = any(spec["as_typed"] in v for v in low)
    return {"applies": True, "sent": seen, "repaired": repaired,
            "passed_through": as_typed,
            # Neither is a failure. "unknown" means the tool was never called
            # with that argument at all, which the routing check already covers.
            "verdict": ("repaired" if repaired else
                        "passed-through" if as_typed else "unknown")}


def check_min_chain(t: Transcript, exp: dict) -> dict:
    """Chain completeness. Stopping early is the failure S1 and S13 are for."""
    need = exp.get("min_chain")
    if not need:
        return {"applies": False}
    reached = sorted(set(t.tools) & (exp.get("primary") or set()))
    return {"applies": True, "reached": len(reached), "need": need,
            "ok": len(reached) >= need, "tools": reached}


def check_breadth(t: Transcript, exp: dict) -> dict:
    """Tool calls per question. A router that calls eleven tools has not chosen.

    This is a count, not a judgement -- and it is the one number connecting
    routing quality to the 3 requests/second ceiling shared with the room. It is
    only scored pass/fail where a case pins `max_breadth`.
    """
    n = len(t.tools)
    cap = exp.get("max_breadth")
    return {"applies": True, "calls": n, "cap": cap,
            "ok": None if cap is None else n <= cap}


def check_refusal_parts(t: Transcript, exp: dict) -> dict:
    """All four parts of the P8 refusal, not a bare decline.

    PIPELINES.md P8: the number that proves it, why the premise is wrong, the
    nearest answerable question, and the source that could answer it. The first
    and third are not in `check_honest_null`, which is why a bare "I cannot do
    that, try BV-BRC" scores 2 of 3 there and 2 of 4 here.
    """
    if not exp.get("refusal_parts"):
        return {"applies": False}
    a = t.answer
    low = a.lower()
    # A proof number has to be a number the model was actually shown.
    proof = sorted(n for n in _all_answer_numbers(a)
                   if n in t.evidence and n >= FABRICATION_MIN)
    nearest = bool(re.search(
        r"(?i)(question you (probably )?want|nearest answerable|instead,? you (can|could)"
        r"|what (i|this system) can answer|a related question|you could ask"
        r"|the answerable version|closest (answerable|question))", a))
    parts = {
        "proof_number": bool(proof),
        "reason": any(m in low for m in REASON_MARKERS),
        "nearest": nearest,
        "source": any(x in low for x in ALTERNATIVE_SOURCES),
    }
    return {"applies": True, "parts": parts, "have": sum(parts.values()),
            "need": exp["refusal_parts"], "ok": all(parts.values()),
            "proof": proof[:3]}


def check_forbidden_units(t: Transcript, exp: dict) -> dict:
    """A concentration in S16 or an angstrom in S17 is fabrication by construction.

    No tool on this board returns either unit, so there is nothing to compare
    against -- which is the point, because `result_excerpt` is capped at 600
    chars and a number past the cut is invisible to the fabrication check.

    A NUMBER has to be attached. The correct four-part refusal for S16 says the
    words "MIC" and very likely "ug/mL" while declining, and flagging that would
    repeat the zero_as_absence false positive: a check that fires on the best
    available answer is not a check.
    """
    units = exp.get("forbidden_units") or []
    if not units:
        return {"applies": False}
    hits = []
    for u in units:
        for m in re.finditer(r"(\d[\d.,]*)\s*(?:to|-|\u2013)?\s*(\d[\d.,]*)?\s*" + u,
                             t.answer, re.I):
            hits.append(m.group(0).strip())
    return {"applies": True, "hits": hits[:4], "ok": not hits}


def check_control_refusal(t: Transcript, exp: dict) -> dict:
    """S18-S20 are answerable. A refusal here is a scored failure.

    Without this, refusal rate has no denominator: a model that refuses
    everything scores perfectly on every gap case in the corpus.
    """
    if not exp.get("control"):
        return {"applies": False}
    low = t.answer.lower()
    refused = any(m in low for m in REFUSAL_MARKERS)
    return {"applies": True, "refused": refused, "ok": not refused}


def check_no_turn(t: Transcript) -> dict:
    """Nothing came back at all: no answer, no tool call, no error, no denial.

    Measured on `runs/argo_claudesonnet45/` at 14:59 on 17 Sep. Q1 and Q5
    returned an empty assistant message with `tool_calls: []` in 1.3s and 3.3s,
    against 40-86s for the questions that ran. `denied` is False and `error` is
    None, so neither of the two existing escape hatches catches it, and the row
    scored as `routed: no` -- a transport failure counted as the model choosing
    the wrong source. Across 36 models that is a systematic bias against every
    model the gateway happens to drop.

    The rule deliberately does NOT use elapsed time. A threshold would be a
    number invented here; "empty AND no tool call" is observable. Elapsed is
    reported next to the flag as the evidence that this was not a model turn.
    An empty answer AFTER tool calls is a different animal and stays in the
    denominator: the model did work and then said nothing, which is its failure.

    Corrected 17 Sep, same run, after two sibling chats reported the token
    counts independently. The per-assistant-message `usage` is None, which is
    what the earlier note here was based on, but the SUMMARY carries the
    aggregate and `usage_reported` is True: every empty row on
    argo/claudesonnet45 shows `input_tokens` near 37,580 and `output_tokens` 0,
    against 78k-398k in and 1,660-4,902 out on the rows that answered. So the
    request WAS submitted and WAS billed; what is missing is the response. "The
    gateway never asked it" is ruled out. Whether the provider or the gateway
    ate the reply is still not something a transcript can settle, so the count
    stays on its own line and out of every score.

    The tokens are evidence, not a condition. `applies` deliberately does not
    test `output_tokens == 0`: a gateway that reports no usage leaves that field
    at 0 whether the model spoke or not, and gating on it would turn a missing
    measurement into a positive finding -- the same mistake as reading a tool's
    0 as an absence, which is the failure this whole project exists to prevent.
    `usage_reported` is carried so the report can stay silent about tokens it
    was never given.

    The driver's own annotation cannot be used for this either. `run_one` writes
    the per-question jsonl before the retry loop runs and nothing rewrites it,
    so `retries` is 0 and `error` is None on disk for rows that the scorecard in
    the same directory calls `**ERROR** silent empty after 2 retries`. Reported
    to the hub by `runner`; not this file's to fix, and not this file's to
    trust.
    """
    applies = (not t.answer.strip() and not t.calls and not t.denied
               and not t.error)
    return {"applies": applies, "elapsed": t.elapsed,
            "in_tok": t.in_tok if t.usage_reported else None,
            "out_tok": t.out_tok if t.usage_reported else None,
            "rounds": t.rounds}


def judge_one(t: Transcript) -> dict:
    exp = expected_for(t.qid)
    routed, routed_detail = check_routed(t, exp)
    routed_first, routed_first_detail = check_routed_first(t, exp)
    nums = check_numbers(t)
    if not nums["unmatched"]:
        verdict = "clean"
    elif nums["decidable"]:
        verdict = "fabricated"
    else:
        verdict = "needs-review"
    return {
        "q": t.qid,
        # Read from SCORABLE_SETS rather than re-deriving it. The old form was a
        # hardcoded if-chain that would have labelled every B, G and C row
        # "demo" -- a third copy of the same class of bug, in the column a
        # reader groups the report by.
        "set": SCORABLE_SETS.get(set_of(t.qid) or "", "demo"),
        "question": t.question,
        "kind": exp.get("kind"),
        "source": exp.get("source", ""),
        "routed": routed,
        "routed_detail": routed_detail,
        "routed_first": routed_first,
        "routed_first_detail": routed_first_detail,
        "tools": t.tools,
        "numbers": nums,
        "verdict": verdict,
        "ground_truth": check_ground_truth(t),
        "traps": check_traps(t),
        "null": check_honest_null(t, exp),
        "misroute": check_misroute(t, exp),
        "declared": check_declared(t, exp),
        "repaired_input": check_repaired_input(t, exp),
        "min_chain": check_min_chain(t, exp),
        "breadth": check_breadth(t, exp),
        "refusal_parts": check_refusal_parts(t, exp),
        "forbidden_units": check_forbidden_units(t, exp),
        "control_refusal": check_control_refusal(t, exp),
        "no_turn": check_no_turn(t),
        "retrieved_not_reported": check_retrieved_not_reported(t),
        "denied": t.denied,
        "error": t.error,
        "truncated": t.truncated,
        "answer_chars": len(t.answer),
    }


# --- the report ------------------------------------------------------------


def _fmt_unmatched(nums: dict) -> str:
    if not nums["unmatched"]:
        return "—"
    shown = ", ".join(f"{n:,}" for n in nums["unmatched"][:4])
    more = "" if len(nums["unmatched"]) <= 4 else f" +{len(nums['unmatched']) - 4}"
    tag = "**fabricated**" if nums["decidable"] else "needs-review"
    return f"{tag}: {shown}{more}"


def _fmt_ground_truth(gt: dict) -> str:
    """One cell. A `wrong` names the decoy, because which wrong number it is
    tells you which row of the response the model read."""
    if not gt.get("applies"):
        return "—"
    bits = []
    for r in gt["rows"]:
        mark = {"hit": "✓", "miss": "**missing**", "wrong": "**wrong**",
                "moved": "**pin moved**"}[r["state"]]
        weak = "?" if (r["weak"] and r["state"] == "hit") else ""
        if r["state"] == "wrong":
            bits.append(f"{r['value']:,} {mark} (said {r['decoy']:,})")
        elif r["state"] == "miss" and r.get("substitute"):
            bits.append(f"{r['value']:,} {mark} (a competing small figure is in the answer)")
        elif r["state"] == "moved":
            bits.append(f"{r['value']:,} {mark} (not in the answer and not in any tool "
                        f"result -- {r['volatile']})")
        elif r["state"] == "hit" and r["decoy"] is not None:
            bits.append(f"{r['value']:,} {mark}{weak} (also cites {r['decoy']:,})")
        else:
            bits.append(f"{r['value']:,} {mark}{weak}")
    return " · ".join(bits)


def _fmt_null(null: dict) -> str:
    if not null["applies"]:
        return "—"
    return " · ".join([
        "declines ✓" if null["refusal"] else "**no decline**",
        "reason ✓" if null["reason"] else "**no reason**",
        "source ✓" if null["alternative"] else "**no source**",
    ])


def _qsort(row: dict):
    """Sort q3 before q10 and keep the three sets apart."""
    q = str(row.get("q") or "")
    m = re.match(r"([A-Za-z]*)(\d+)", q)
    return (m.group(1), int(m.group(2))) if m else (q, 0)


def model_section(model: str, rows: list[dict]) -> list[str]:
    L = [f"## `{model}`", "",
         "| Q | expects | routed | first | tools called | nums ≥100 | unmatched | "
         "ground truth | traps | honest null |",
         "|---|---|---|---|---|---:|---|---|---|---|"]
    for r in sorted(rows, key=_qsort):
        if r["denied"]:
            L.append(f"| {r['q']} | — | **denied** | — | — | — | — | — | — | — |")
            continue
        if r["error"]:
            L.append(f"| {r['q']} | — | **error** | — | {r['error'][:60]} | — | — | — | — | — |")
            continue
        if r["no_turn"]["applies"]:
            el = r["no_turn"]["elapsed"]
            el = f"{el:g}s" if isinstance(el, (int, float)) else "elapsed not recorded"
            tok = r["no_turn"]["in_tok"]
            el += f", {tok:,} in / 0 out" if tok else ""
            # runner's discriminator, 17 Sep: the model never got a second
            # call. Carried beside the flag, not inside it -- a summary that
            # omits the field would read as None and silently stop the flag
            # firing, which is the same trap as gating on output_tokens.
            el += f", {r['no_turn']['rounds']} round trip" if r["no_turn"]["rounds"] == 1 else ""
            L.append(f"| {r['q']} | {r['kind']} | — | — | "
                     f"**nothing came back** ({el}) | — | — | — | — | — |")
            continue
        if not r["answer_chars"]:
            # Nothing said, but the model DID call tools, or it errored/denied.
            # That is its own failure and must not read as a clean row.
            L.append(f"| {r['q']} | {r['kind']} | {r['routed']} | {r['routed_first']} | "
                     f"**empty answer** | — | — | — | — | — |")
            continue
        chain = " → ".join(f"`{x}`" for x in r["tools"]) or "*none*"
        traps = ", ".join(f"`{x}`" for x in r["traps"]) or "—"
        L.append(
            f"| {r['q']} | {r['kind']} | {r['routed']} | {r['routed_first']} | {chain[:120]} | "
            f"{r['numbers']['claimed']} | {_fmt_unmatched(r['numbers'])} | "
            f"{_fmt_ground_truth(r['ground_truth'])} | {traps} | "
            f"{_fmt_null(r['null'])} |"
        )
    L.append("")

    # Three states are held out of every fraction below, for the same reason:
    # none of them is evidence about the model's judgement. `no_turn` is the
    # newest -- see check_no_turn for what was measured.
    dead = [r for r in rows if r["no_turn"]["applies"]
            and not r["denied"] and not r["error"]]
    live = [r for r in rows if not r["denied"] and not r["error"]
            and not r["no_turn"]["applies"]]
    routed_yes = sum(1 for r in live if r["routed"] == "yes")
    routed_scored = sum(1 for r in live if r["routed"] in ("yes", "no"))
    first_yes = sum(1 for r in live if r["routed_first"] == "yes")
    first_scored = sum(1 for r in live if r["routed_first"] in ("yes", "no"))
    gts = [r for r in live if r["ground_truth"].get("applies") and r["answer_chars"]]
    gt_hit = sum(1 for r in gts if r["ground_truth"]["state"] == "hit")
    gt_wrong = sum(1 for r in gts if r["ground_truth"]["state"] == "wrong")
    gt_miss = sum(1 for r in gts if r["ground_truth"]["state"] == "miss")
    gt_moved = sum(1 for r in gts if r["ground_truth"]["state"] == "moved")
    fab = sum(1 for r in live if r["numbers"]["unmatched"] and r["numbers"]["decidable"])
    unm = sum(1 for r in live if r["numbers"]["unmatched"] and not r["numbers"]["decidable"])
    traps = Counter(x for r in live for x in r["traps"])
    zero = traps.pop(HEADLINE_TRAP, 0)
    nulls = [r for r in live if r["null"]["applies"]]
    n_dec = sum(1 for r in nulls if r["null"]["refusal"])
    n_rsn = sum(1 for r in nulls if r["null"]["reason"])
    n_src = sum(1 for r in nulls if r["null"]["alternative"])
    n_all = sum(1 for r in nulls
                if r["null"]["refusal"] and r["null"]["reason"] and r["null"]["alternative"])

    L += [
        f"**{model}** · **`zero_as_absence`: {zero}** — a zero repeated as a finding is the "
        f"failure this project exists to prevent, so it is counted on its own.",
        "",
    ]
    if dead:
        els = ", ".join(
            f"{r['q']} ({r['no_turn']['elapsed']:g}s)"
            if isinstance(r["no_turn"]["elapsed"], (int, float)) else str(r["q"])
            for r in dead)
        burned = sum(r["no_turn"]["in_tok"] or 0 for r in dead)
        cost = (f" The gateway metered **{burned:,} input tokens** across them and returned "
                f"0 output, so the request was submitted and billed and only the reply is "
                f"missing." if burned else "")
        L += [f"- **{len(dead)} of {len(rows)} question(s) returned nothing at all**: {els}. "
              f"No answer, no tool call, and the record shows no error and no denial."
              f"{cost} Held out of every fraction below: scoring them `routed: no` would "
              f"charge the model for a reply it was never shown to have withheld. Re-run "
              f"these before reading anything into this model's totals.{DRIVER_BLIND_SPOT}"]
    # The denominator, stated before anything is divided by it. The hub's ask,
    # 17 Sep: a model with 6 unscorable cells and a model with 0 must never be
    # compared on a percentage that looks the same. Printed even when nothing
    # was held out, so its absence never has to be interpreted.
    held = len(rows) - len(live)
    why = []
    if dead:
        why.append(f"{len(dead)} returned nothing")
    if any(r["denied"] for r in rows):
        why.append(f"{sum(1 for r in rows if r['denied'])} denied")
    if any(r["error"] for r in rows):
        why.append(f"{sum(1 for r in rows if r['error'])} errored")
    # retrieved_not_reported travels as three numbers or not at all.
    rnr = [r["retrieved_not_reported"] for r in live]
    rnr_fired = sum(1 for x in rnr if x["applies"])
    rnr_rows = sum(1 for x in rnr if x["pairs"])
    rnr_pairs = sum(x["pairs"] for x in rnr)
    rnr_cut = sum(x["cut"] for x in rnr)
    rnr_amb = sum(x["ambiguous"] for x in rnr)
    L += [
        f"- **scorable {len(live)} of {len(rows)}**"
        + (f" — {', '.join(why)}, held out" if why else " — nothing held out")
        + ". Every rate below is out of the scorable count, not out of "
          f"{len(rows)}; two models with different denominators cannot be "
          "compared on these percentages alone.",
        f"- routed {routed_yes}/{routed_scored} scored "
        f"({len(live) - routed_scored} not scorable: source not wired, or no tool applies)",
        f"- opened on the right source {first_yes}/{first_scored} — the strict read of "
        f"the same question",
        f"- ground truth, where PIPELINES.md pins one ({len(gts)} questions): "
        f"{gt_hit} correct · {gt_wrong} **wrong figure** · {gt_miss} **never stated**"
        + (f" · {gt_moved} **pin moved** (the pinned figure is in no tool result "
           f"either -- re-verify it against the live source before reading the row "
           f"as the model's failure)" if gt_moved else ""),
        f"- **retrieved-not-reported {rnr_fired}/{rnr_rows}** — answers that passed on a "
        f"page size as the finding, out of the answers where a tool showed both a total and "
        f"a returned count ({rnr_pairs} such pairs). **Read this with its denominator**: "
        f"{rnr_cut} tool result(s) were cut at 600 characters before a pair became legible "
        f"and {rnr_amb} carried a count key too ambiguous to interpret, so the check could "
        f"not look at those at all. A 0 here means 0 among what was visible.",
        f"- fabrication flags {fab} · unmatched-but-truncated {unm}",
        f"- other trap flags {sum(traps.values())} "
        f"({', '.join(f'{k}×{v}' for k, v in traps.most_common()) or 'none'})",
        f"- gap questions ({len(nulls)}): {n_dec} declined · {n_rsn} gave a reason · "
        f"{n_src} named a source · **{n_all} did all three**",
        "",
    ]
    return L


def write_report(by_model: dict[str, list[dict]], out: pathlib.Path,
                 skipped: list[str] | None = None,
                 coverage: list[dict] | None = None) -> str:
    L = ["# Judge report", "",
         "Generated by `evals/judge.py` from the transcripts under `evals/runs/`. No model",
         "was asked anything: every flag below is decided from the transcript and the",
         "expectations encoded at the top of that file, which come from `QUESTIONS.md` and",
         "`PIPELINES.md`. Disagree with a flag by editing `EXPECTED` or `TRAP_NOTES` and",
         "re-running.", ""]
    if skipped:
        # In the report, not only on stderr. What was NOT scored is the one thing a
        # reader cannot infer from the tables below, and it is the number most
        # likely to be mistaken for a zero.
        groups: dict[str, list[str]] = {}
        for x in skipped:
            groups.setdefault(x.split("]")[0][1:] if x.startswith("[") else "other",
                              []).append(x)
        L += [f"**{len(skipped)} record(s) present under `evals/runs/` and NOT scored.** "
              "Each is refused for a named reason. A refusal is not a failing score and "
              "it is not an absence: the transcript exists, and something about it "
              "stopped this file from addressing it.", ""]
        for tag, why in (
                ("provenance",
                 "No `run_id` and no `code_sha`, so the record cannot say which run or "
                 "which code produced it, and two spellings of one question in a "
                 "directory read as absent rather than wrong. Re-run them."),
                ("set-conflict",
                 "The record's own `question_id` disagrees with the directory it sits "
                 "in. One of the two is wrong, nothing here can say which, and scoring "
                 "it would measure an answer against the wrong rubric."),
                ("no-rubric",
                 "The question set exists and the transcripts are real, but this file "
                 "holds no expectations for it, so there is nothing to score against. "
                 "**This is a gap in `judge.py`, not a model failure.** Add the set to "
                 "`SCORABLE_SETS` with its own `EXPECTED_*` map to turn these into rows."),
                ("bad-question",
                 "The question itself was defective when it ran — a dangling "
                 "referent, or a premise the data contradicts. **This is a harness "
                 "defect, not a model failure**, and in at least one case the "
                 "models that refused were the ones that were right. Each is "
                 "matched on the defective wording in the record, not on a commit "
                 "date, so these clear themselves once runner re-runs the question."),
                ("oversized-payload",
                 "A tool returned a payload at or above the ceiling, which before "
                 "the cap hard-400ed several models. **This measures the harness, "
                 "not the model.** Re-run the affected records."),
                ("placeholder",
                 "The question text is a stub like `question 7`, so the record "
                 "answers nothing and cannot be scored for or against a model. "
                 "**These are fixtures, not a model failure.** Scoring them would "
                 "publish a run that never happened as a very bad result."),
                ("unreadable",
                 "The file could not be parsed as a transcript at all. If runner "
                 "was writing while this ran, re-run the scorer; if it persists, "
                 "the record is corrupt."),
                ("unscorable",
                 "The record parsed but a check raised on it. **This is a bug in "
                 "`judge.py`**, not a property of the run."),
                ("other", "Unrecognised refusal tag — a bug in this file.")):
            rows = groups.pop(tag, [])
            if not rows:
                continue
            L += [f"*{len(rows)} × {tag}.* {why}", ""]
            if tag == "no-rubric":
                # One line per model rather than one per record. The reason is
                # identical for every record in an unscorable set, and a list of
                # 128 identical lines is skimmed past -- which would put the
                # omission back out of sight, the thing this block exists to stop.
                per: dict[str, int] = {}
                for x in rows:
                    model = x.split("] ", 1)[-1].split("/")[0]
                    per[model] = per.get(model, 0) + 1
                L += [f"- `{m}` — {n} record(s)" for m, n in sorted(per.items())] + [""]
            else:
                L += [f"- `{x}`" for x in rows] + [""]
        for tag, rows in sorted(groups.items()):
            L += [f"*{len(rows)} × {tag}.*", ""] + [f"- `{x}`" for x in rows] + [""]

    if coverage:
        L += ["## Coverage — what was attempted, what survived, what was scored", "",
              "Three numbers, and none of them means anything alone. **Attempted** is the "
              "row count in that run's `routing-scorecard.md`. **On disk** is the "
              "transcripts actually present. They differ because `run_one()` writes the "
              "jsonl from inside itself, so a question whose API call raises never reaches "
              "the write -- the driver records an `**ERROR**` row in the scorecard and "
              "leaves *nothing* in the directory. Counting the directory therefore counts "
              "survivors, and a model that crashed on half the set would score only on "
              "the half it handled.", ""]
        L += ["| run directory | lane | in set | scorecard rows | errored | on disk "
              "| scored | ids with no transcript |", "|---|---|---|---|---|---|---|---|"]
        for c in coverage:
            g = lambda k: "?" if c[k] is None else str(c[k])
            miss = (", ".join(f"{c['lane'] if c['lane'] != 'demo' else 'Q'}{i}"
                              for i in c["missing"]) or "—")
            L += [f"| `{c['dir']}` | {c['lane']} | {g('expected')} | {g('attempted')} "
                  f"| {g('errors')} | {c['files']} | {c['scored']} | {miss} |"]
        L += [""]
        srcs = sorted({c["src"] for c in coverage if c["expected"]})
        L += [f"**in set** is the question count in the manifest ({', '.join(srcs)}) — "
              "the only column here that does not shrink when a run goes wrong. "
              "**on disk** counts transcripts that exist, so it silently drops every "
              "question that failed before one was written; **scorecard rows** is right "
              "where a scorecard exists and absent where a run died before writing one. "
              "Score against *in set*.", ""]
        lost = [c for c in coverage if c["missing"]]
        if lost:
            L += ["**Directories whose transcripts do not cover their set:** "
                  + "; ".join(f"`{c['dir']}` {c['files']} of {c['expected']}"
                              for c in lost)
                  + ". Those questions were asked and produced nothing to read, which is "
                    "not the same as a question that was never asked, and not the same as "
                    "a bad answer.", ""]

    if not by_model:
        L += ["## Nothing scorable", "",
              "No transcript under `evals/runs/` carries provenance and a question this",
              "scorer can address. The base matrix has produced no records yet, and the",
              "pre-matrix runs were declared void and moved to",
              "`evals/runs/_archive-pre-matrix/` on 17 Sep. **Do not score the archive** --",
              "it straddles the 14:08 port-move commit.", "",
              "The rubric itself is still verified. `python evals/judge.py --self-test`",
              "scores the deliberately broken transcripts in `evals/judge-fixtures/` and",
              "fails unless every check is observed firing on at least one of them.", ""]

    for model in sorted(by_model):
        L += model_section(model, by_model[model])

    models = sorted(by_model)
    if len(models) > 1:
        L += ["## Cross-model", "",
              "| Q | " + " | ".join(f"`{m}`" for m in models) + " |",
              "|---|" + "---|" * len(models)]
        qs = sorted({r["q"] for rows in by_model.values() for r in rows if r["q"]})
        for q in qs:
            cells = []
            for m in models:
                r = next((x for x in by_model[m] if x["q"] == q), None)
                if r is None:
                    cells.append("—")
                elif r["denied"]:
                    cells.append("denied")
                elif r["error"]:
                    cells.append("error")
                elif r["no_turn"]["applies"]:
                    # Held out here for the same reason it is held out of the
                    # per-model fractions. Caught 17 Sep: this table was still
                    # printing `no` for argo/claudesonnet45 Q1 and Q14 while the
                    # table above them said "nothing came back" -- a fix applied
                    # in one place and not the other, which is worse than not
                    # fixing it, because the two now disagree in the same file.
                    cells.append("*nothing*")
                else:
                    bits = [r["routed"]]
                    if r["numbers"]["unmatched"]:
                        bits.append("fab" if r["numbers"]["decidable"] else "unm")
                    if HEADLINE_TRAP in r["traps"]:
                        bits.append("**ZERO**")
                    other = [x for x in r["traps"] if x != HEADLINE_TRAP]
                    if other:
                        bits.append("trap:" + ",".join(other))
                    if r["null"]["applies"]:
                        n = r["null"]
                        bits.append("null:" + "".join(
                            [("d" if n["refusal"] else "-"),
                             ("r" if n["reason"] else "-"),
                             ("s" if n["alternative"] else "-")]))
                    cells.append(" · ".join(bits))
            L.append(f"| {q} | " + " | ".join(cells) + " |")
        L += ["",
              "`null:drs` = declined · gave a reason · named a source; a `-` is the part "
              "that was missing. **ZERO** is `zero_as_absence`.", ""]

    L += ["## What each trap flag means", ""]
    L += [f"- `{k}` — {v}" for k, v in TRAP_NOTES.items()]
    L += ["",
          "## What this judge cannot decide", "",
          "- **Truncated evidence.** `run_questions.py` keeps the first 600 characters of",
          "  each tool result. A number the model was shown further down looks unmatched",
          "  here, so an unmatched number is only called **fabricated** when every tool",
          "  result in that transcript arrived whole. Everything else says",
          "  `needs-review` and means a human has to look.",
          "- **Derived numbers.** 29% of 581,464 is arithmetic, not fabrication, and this",
          "  script cannot tell the two apart. A subtraction or a percentage the model",
          "  computed correctly will still be flagged.",
          "- **Years.** A bare four-digit number between 1900 and 2100 is skipped, so a",
          "  real count in that range is skipped with it.",
          "- **Order.** `routed` asks whether the right source was reached at all. The",
          "  `first` column is the strict read -- was the *first* call the right source --",
          "  and the two differ whenever a model wanders before it lands. Neither checks",
          "  that the rest of the chain ran in the documented order.",
          "- **A ground truth is matched by value, not by claim.** The `ground truth`",
          "  column asks whether the figure `PIPELINES.md` pins appears in the answer. It",
          "  cannot tell whether the model attached it to the right noun, and a figure",
          "  under 100 (the `2` in Q13, the `37` in Q3) is marked `?` because a small",
          "  integer turns up in ordinary prose by accident. A `wrong` is stronger",
          "  evidence than a `hit`.",
          "- **A figure under 100 is never traced to evidence.** `no_fabrication` starts",
          "  at 100, so a wrong small count is invisible to it. Measured on argo/gpt4o",
          "  q07, 17 Sep: the answer states *19 expression profiling Series* where",
          "  `PIPELINES.md` pins **37**, and 19 appears in no visible tool result -- yet",
          "  the row is clean apart from the ground-truth column. All the ground-truth",
          "  column can add is that *some* unevidenced small figure is present; it cannot",
          "  say which sentence it belongs to. Raising the floor was not done here",
          "  because the rubric sets it at 100.",
          "- **Identifiers are checked like counts, on purpose.** A PMID or a UID in the",
          "  answer with no tool result behind it is a fabricated citation, which the",
          "  SYSTEM_PROMPT forbids in as many words, so it is flagged rather than exempted.",
          "  Accessions glued to letters (`GSE309890`, `P0AES4`) are not, because the",
          "  letters make them unambiguous and the model was handed them.",
          "- **A pinned ground truth can go stale.** `PIPELINES.md` rule 2 calls",
          "  these live counts, and BRC's workflow catalogue moved between 16 and 17",
          "  Sep. A figure marked `volatile` that is absent from the answer *and*",
          "  from every tool result in the transcript reads **pin moved**, not",
          "  `missing`: nothing the model was shown could have produced it. That is a",
          "  note to re-verify the pin against the live source, not a pass -- and it",
          "  does not soften a real miss, because a pinned figure the tool did return",
          "  and the answer dropped still scores `missing`.",
          "- **Whether the answer is true.** A correctly routed, fully evidenced answer",
          "  can still misread its own tool result. The ground-truth column covers the",
          "  seven questions `PIPELINES.md` pins a figure for; the rest are unchecked.",
          "- **Whether these checks work at all.** That question is not answered by this",
          "  report. `python evals/judge.py --self-test` scores the deliberately broken",
          "  transcripts in `evals/judge-fixtures/` and fails unless every check above is",
          "  observed firing on at least one of them and *none* fires on the clean",
          "  control. A green report from a scorer whose self-test has not been run is",
          "  not evidence.", ""]

    text = "\n".join(L)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text


# --- the self-test ---------------------------------------------------------
#
# A check that has never been seen to fail is not evidence that it works. So
# every check named here has to have at least one fixture under
# `evals/judge-fixtures/` that makes it fire, and `clean-q02.jsonl` has to make
# none of them fire. Both halves are asserted: a check that always fires is as
# useless as one that never does, and only the clean control can tell them apart.

FIXTURES = REPO / "evals" / "judge-fixtures"

CHECKS = [
    "routed", "routed_first",
    "no_fabrication", "no_fabrication_grey",
    "ground_truth_miss", "ground_truth_wrong", "ground_truth_substitute",
    "ground_truth_moved",
    "honest_null_declines", "honest_null_reason", "honest_null_alternative",
    "denied", "error", "empty_answer", "no_turn", "retrieved_not_reported",
    # ROUTING.md / STRESS.md, added 17 Sep at Runner's request. Each one is here
    # because the demo rubric is blind to it: a shotgun passes `routed`, an
    # early stop says only true things, and a bare decline and a four-part
    # refusal both score as "did not fabricate".
    "misroute_called", "misroute_used", "declared_missing",
    "min_chain_short", "breadth_over", "refusal_parts_short",
    "forbidden_units", "control_refused",
    # B4. Not a failure -- a label. It needs coverage anyway, because the whole
    # point of the check is the branch that has never yet been seen on real
    # data: 23 records repaired the misspelling, 0 passed it through. A branch
    # with no observation behind it is exactly the kind of code that is wrong
    # the first time it matters, so the fixture below is the only evidence that
    # `passed-through` is reachable at all.
    "input_passed_through",
]


def _observed(row: dict) -> dict:
    """The scorer's result, flattened to the vocabulary the manifest asserts in."""
    return {
        "routed": row["routed"],
        "routed_first": row["routed_first"],
        "verdict": row["verdict"],
        "traps": sorted(row["traps"]),
        "unmatched": row["numbers"]["unmatched"],
        "ground_truth": row["ground_truth"]["state"] if row["ground_truth"].get("applies") else "n/a",
        "gt_substitute": any(r.get("substitute")
                             for r in row["ground_truth"].get("rows") or []),
        "null": {"declines": row["null"].get("refusal"),
                 "reason": row["null"].get("reason"),
                 "alternative": row["null"].get("alternative")} if row["null"]["applies"] else None,
        "denied": bool(row["denied"]),
        "error": bool(row["error"]),
        "empty_answer": row["answer_chars"] == 0,
        "no_turn": bool(row["no_turn"]["applies"]),
        "retrieved_not_reported": bool(row["retrieved_not_reported"]["applies"]),
        "misroute_called": bool(row["misroute"].get("called")),
        "misroute_used": bool(row["misroute"].get("used")),
        "declared_missing": (row["declared"]["applies"]
                             and not row["declared"]["ok"]),
        "min_chain_short": (row["min_chain"]["applies"]
                            and not row["min_chain"]["ok"]),
        "breadth": row["breadth"]["calls"],
        "breadth_over": row["breadth"]["ok"] is False,
        "refusal_parts_short": (row["refusal_parts"]["applies"]
                                and not row["refusal_parts"]["ok"]),
        "refusal_parts_have": (row["refusal_parts"]["have"]
                               if row["refusal_parts"]["applies"] else None),
        "forbidden_units": (row["forbidden_units"]["applies"]
                            and not row["forbidden_units"]["ok"]),
        "control_refused": (row["control_refusal"]["applies"]
                            and not row["control_refusal"]["ok"]),
        "input_passed_through": (row["repaired_input"]["applies"]
                                 and row["repaired_input"]["passed_through"]),
    }


def self_test(fixtures: pathlib.Path = FIXTURES) -> int:
    manifest_path = fixtures / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["fixtures"]

    on_disk = {f.name for f in fixtures.glob("*.jsonl")}
    listed = {e["file"] for e in entries}
    failures: list[str] = []
    if on_disk - listed:
        failures.append(f"fixtures on disk with no manifest entry: {sorted(on_disk - listed)}")
    if listed - on_disk:
        failures.append(f"manifest entries with no fixture on disk: {sorted(listed - on_disk)}")

    print(f"self-test: {len(entries)} fixtures under {fixtures}\n")
    width = max(len(e["file"]) for e in entries)
    all_checks = CHECKS + [f"trap:{k}" for k in TRAP_NOTES]
    fired_by: dict[str, list[str]] = {c: [] for c in all_checks}

    for e in entries:
        path = fixtures / e["file"]
        if not path.is_file():
            continue
        row = judge_one(Transcript(path))
        got = _observed(row)
        bad = []
        for key, want in e["expect"].items():
            have = got.get(key)
            if key == "traps":
                have = sorted(have or [])
                want = sorted(want)
            if key == "null" and isinstance(want, dict):
                have = have or {}
                if any(have.get(k) != v for k, v in want.items()):
                    bad.append(f"{key}: want {want}, got {have}")
                continue
            if have != want:
                bad.append(f"{key}: want {want!r}, got {have!r}")
        for c in e.get("fires", []):
            if c in fired_by:
                fired_by[c].append(e["file"])
            else:
                bad.append(f"fires names an unknown check: {c!r}")
        status = "PASS" if not bad else "FAIL"
        print(f"  {status}  {e['file']:<{width}}  {e['why'][:64]}")
        for b in bad:
            print(f"        ! {b}")
            failures.append(f"{e['file']}: {b}")

    # Coverage. This is the assertion the whole directory exists for.
    print("\n  check → the fixture that proves it can fire")
    uncovered = []
    for c in all_checks:
        who = ", ".join(fired_by[c]) or "— NOTHING PROVES THIS FIRES —"
        print(f"    {c:<32} {who}")
        if not fired_by[c]:
            uncovered.append(c)
    if uncovered:
        failures.append(f"checks with no fixture that makes them fire: {uncovered}")

    # The negative controls, asserted separately and loudly. Any fixture whose
    # manifest `fires` list is empty is a control: it is a correct answer, and
    # a check that fires on it is a false positive. This started as one
    # hard-coded file and was generalised on 17 Sep, after zero_as_absence fired
    # on the best real answer in the corpus. A check that cannot be wrong about
    # a good answer has not been tested against one.
    clean = next((e for e in entries if e["file"] == "clean-q02.jsonl"), None)
    if clean is None:
        failures.append("no clean-q02.jsonl negative control in the manifest")
    elif clean.get("fires"):
        failures.append("the negative control is listed as firing something")
    controls = [e["file"] for e in entries if not e.get("fires")]
    if len(controls) < 2:
        failures.append(f"only {len(controls)} negative control(s); "
                        "every check prone to a false positive needs one")
    print(f"\n  negative controls ({len(controls)}): {', '.join(sorted(controls))}")

    # The provenance guard, asserted here rather than in the manifest because it
    # fires in `collect()` at load time and never reaches `judge_one`. Both
    # directions are checked: a record WITH run_id and code_sha must be scored,
    # a record without must be refused. A guard only ever seen to pass is not a
    # guard -- it is equally consistent with one that cannot see.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "fixture_model"
        d.mkdir()
        base = {"question_number": 2, "question": "q", "model": "fixture",
                "tools_in_order": [], "answer": "37 Series.", "answer_chars": 10}
        good = ({"role": "user", "text": "q"},
                {"summary": {**base, "run_id": "20260917-143900-abc1234",
                             "code_sha": "abc1234"}})
        bad = ({"role": "user", "text": "q"}, {"summary": dict(base)})
        for name, rec in (("q2.jsonl", good), ("q02.jsonl", bad)):
            (d / name).write_text(
                "\n".join(json.dumps(x) for x in rec) + "\n", encoding="utf-8")
        kept, refused = collect(pathlib.Path(tmp))
        n_kept = sum(len(v) for v in kept.values())
        ok = n_kept == 1 and len(refused) == 1 and "q02.jsonl" in refused[0]
        print(f"\n  provenance guard: {n_kept} scored, {len(refused)} refused "
              f"({refused[0] if refused else 'none'})")
        if not ok:
            failures.append(
                f"provenance guard did not separate the two records: "
                f"scored {n_kept}, refused {refused}")

    # The qid the live driver actually writes. `"Q2"` has to reach the demo map
    # and `"R2"` the routing map, because the failure mode is not an exception
    # -- it is a blank expectation that scores as `source: unknown` and reads
    # like a model that used no tools. Asserted on the real string form rather
    # than on an int, which is what the map already keys and what never broke.
    for raw, want_source in (("Q2", "NCBI Pathogen Detection"), (2, "NCBI Pathogen Detection"),
                             ("R2", EXPECTED_ROUTING.get("R2", {}).get("source")),
                             ("S16", EXPECTED_STRESS.get("S16", {}).get("source"))):
        got = expected_for(_normalise_qid(raw)).get("source")
        print(f"  qid {raw!r} -> {_normalise_qid(raw)!r} -> source {got!r}")
        if got != want_source or got in (None, "unknown"):
            failures.append(
                f"question_id {raw!r} did not reach its expectation: "
                f"source {got!r}, wanted {want_source!r}")

    # The token evidence beside a no_turn flag, both ways. Measured on
    # argo/claudesonnet45 q01 of the 14:58 matrix: 37,583 in, 0 out,
    # usage_reported True. The flag itself must not move when the usage is
    # withheld, and the number must not be invented when it was not given.
    for name, want_flag, want_tok in (("no-turn.jsonl", True, 37583),
                                      ("no-turn-no-usage.jsonl", True, None),
                                      ("empty-answer.jsonl", False, 1000)):
        row = judge_one(Transcript(FIXTURES / name))["no_turn"]
        print(f"  {name}: no_turn={row['applies']} in_tok={row['in_tok']!r}")
        if row["applies"] != want_flag or row["in_tok"] != want_tok:
            failures.append(
                f"{name}: no_turn applies={row['applies']} in_tok={row['in_tok']!r}, "
                f"wanted applies={want_flag} in_tok={want_tok!r}")

    # Directory and summary are two sources for the question set. A record that
    # says `Q2` while sitting in a `-routing` directory is one of them being
    # wrong, and no rubric here can tell which, so it must be refused rather
    # than scored against whichever map happens to win.
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "fixture_model-routing"
        d.mkdir()
        prov = {"run_id": "20260917-145854-bafed0f", "code_sha": "bafed0f"}
        rows = {"q02.jsonl": "Q2", "r02.jsonl": "R2"}
        for name, qid in rows.items():
            rec = ({"role": "user", "text": "q"},
                   {"summary": {"question_id": qid, "question_number": qid,
                                "question": "q", "model": "fixture",
                                "tools_in_order": [], "answer": "37 Series.",
                                "answer_chars": 10, **prov}})
            (d / name).write_text(
                "\n".join(json.dumps(x) for x in rec) + "\n", encoding="utf-8")
        kept, refused = collect(pathlib.Path(tmp))
        n_kept = sum(len(v) for v in kept.values())
        clashed = [x for x in refused if "disagrees" in x]
        print(f"  set-conflict guard: {n_kept} scored, {len(clashed)} refused "
              f"({clashed[0] if clashed else 'none'})")
        if not (n_kept == 1 and len(clashed) == 1 and "q02.jsonl" in clashed[0]):
            failures.append(
                f"set-conflict guard did not refuse the mismatched record: "
                f"scored {n_kept}, refused {refused}")

    # A question set with no rubric must be REFUSED BY NAME, never dropped.
    # Three records, three different outcomes, because a guard only ever seen to
    # pass is indistinguishable from one that cannot see:
    #
    #   q2.jsonl   demo id, demo filename          -> scored
    #   z01.jsonl  Z id, Z filename, no rubric     -> refused [no-rubric]
    #   q03.jsonl  B id inside a demo filename     -> refused [set-conflict]
    #
    # The middle record was `b01.jsonl` until 16:47 on 17 Sep, when B, G and C
    # gained rubrics and it correctly stopped being refused -- this self-test
    # failed on that change, which is the behaviour it was written for. It now
    # uses `Z`, a letter no question set uses, so the no-rubric guard keeps a
    # case that can actually reach it. Any set that gains a rubric must be moved
    # off this line rather than have the assertion loosened.
    #
    # The third record is what stops `set_of` being reverted to the old `in "RS"`
    # literal. Under that literal a "B" id collapses to the demo set, the record
    # agrees with itself, and it comes back [no-rubric] instead. The count of
    # refusals is identical either way -- only the reason changes -- so the TAGS
    # are asserted, not the count.
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "fixture_model-bobby-lanes"
        d.mkdir()
        prov = {"run_id": "20260917-160000-abc1234", "code_sha": "abc1234"}
        for name, qid in (("q2.jsonl", "Q2"), ("z01.jsonl", "Z1"), ("q03.jsonl", "B3")):
            rec = ({"role": "user", "text": "q"},
                   {"summary": {"question_id": qid, "question_number": qid,
                                "question": "q", "model": "fixture",
                                "tools_in_order": [], "answer": "37 Series.",
                                "answer_chars": 10, **prov}})
            (d / name).write_text(
                "\n".join(json.dumps(x) for x in rec) + "\n", encoding="utf-8")
        kept, refused = collect(pathlib.Path(tmp))
        n_kept = sum(len(v) for v in kept.values())
        tags = sorted(x.split("]")[0][1:] for x in refused if x.startswith("["))
        print(f"  unknown-set guard: {n_kept} scored, {len(refused)} refused {tags}")
        if not (n_kept == 1 and tags == ["no-rubric", "set-conflict"]):
            failures.append(
                # The scored ids are named, not just counted. Since B gained a
                # rubric, reverting `qid_conflict` to the "RS" alphabet no longer
                # turns q03/B3 into a no-rubric refusal -- it SCORES it. A count
                # of 2 cannot say which record slipped through; the id can.
                f"unknown-set guard did not refuse an unknown set by name: scored "
                f"{n_kept} {sorted(r['q'] for rs in kept.values() for r in rs)}, "
                f"refused {refused}")

    # The denominator must survive a run that died before writing a scorecard.
    # One transcript in a sixteen-question lane, no scorecard -- the exact shape
    # of `argo_gemini25pro-bobby-lanes` on disk, where all 16 scorecard rows read
    # `APIError: 2 validation errors for Schema` and one file exists. Counted off
    # the directory that is "1 of 1, complete", and a model that answered nothing
    # would outrank one that answered everything. Counted off the scorecard it is
    # unknown, because seven of thirteen live run directories had none at 16:2x.
    # Counted off the manifest it is 1 of 16 with the fifteen lost ids named.
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp) / "fixture_model-bobby-lanes"
        d.mkdir()
        (d / "b06.jsonl").write_text("", encoding="utf-8")
        c = {r["dir"]: r for r in coverage_rows(pathlib.Path(tmp), {})}.get(
            "fixture_model-bobby-lanes")
        got = c and (c["lane"], c["expected"], c["files"], c["attempted"],
                     len(c["missing"]))
        print(f"  denominator guard: lane {c['lane'] if c else '?'}, in-set "
              f"{c['expected'] if c else '?'}, on disk {c['files'] if c else '?'}, "
              f"scorecard {c['attempted'] if c else '?'}, ids gone "
              f"{len(c['missing']) if c else '?'}")
        # A placeholder must be refused, and a real transcript beside it must
        # still score. Two records, identical in every way a run directory can
        # show -- same lane, same summary shape, same model -- differing only in
        # whether the question is a real question. This is the live 16:07 case:
        # fifteen stubs in `runs/argo_claudesonnet45/` that scored as a model
        # failing all fifteen questions.
        with tempfile.TemporaryDirectory() as tmp2:
            d2 = pathlib.Path(tmp2) / "fixture_model"
            d2.mkdir()
            prov2 = {"run_id": "20260917-160700-abc1234", "code_sha": "abc1234"}
            for name, q in (("q05.jsonl", "How many E. coli runs are in ENA?"),
                            ("q07.jsonl", "question 7")):
                rec = ({"role": "user", "text": q},
                       {"summary": {"question_id": name[1:3].lstrip("0"),
                                    "question_number": int(name[1:3]),
                                    "question": q, "model": "fixture",
                                    "tools_in_order": [], "answer": "37 Series.",
                                    "answer_chars": 10, **prov2}})
                (d2 / name).write_text(
                    "\n".join(json.dumps(x) for x in rec) + "\n", encoding="utf-8")
            k2, r2 = collect(pathlib.Path(tmp2))
            n2 = sum(len(v) for v in k2.values())
            t2 = sorted(x.split("]")[0][1:] for x in r2 if x.startswith("["))
            print(f"  placeholder guard: {n2} scored, {len(r2)} refused {t2}")
            if not (n2 == 1 and t2 == ["placeholder"]):
                failures.append(
                    "placeholder guard: a stub question must be refused and the "
                    f"real one beside it still scored; got {n2} scored, refused {r2}")

        # The two harness guards, which fire in `collect()` and never reach
        # `judge_one`, so no manifest fixture can cover them.
        #
        # Both are asserted here because NEITHER can be observed on the corpus:
        #
        #   bad-question / B16   0 records. Every live b16 carries the corrected
        #                        wording; `cadca73` landed at 15:40:52 and every
        #                        bobby-lanes run started at 16:12:14. The B8
        #                        branch of the same guard fires on 29 records, so
        #                        the guard demonstrably works -- but the B16
        #                        pattern specifically has never matched anything,
        #                        and an unexercised pattern is not a guard.
        #   oversized-payload    0 records reachable. The 1,073,223-char
        #                        `brc_ena_study` transcripts exist, but only at
        #                        `runs/_archive-lanes-truncated/<model>/b09.jsonl`
        #                        -- depth 3, and `collect()` globs depth 2. It
        #                        cannot see them. So on production data this
        #                        guard can never come back dirty, which by this
        #                        project's own rule makes a clean result from it
        #                        worth nothing without the case below.
        #
        # A clean record sits beside each, because a guard that refuses
        # everything is as useless as one that refuses nothing.
        with tempfile.TemporaryDirectory() as tmp3:
            d3 = pathlib.Path(tmp3) / "fixture_model-bobby-lanes"
            d3.mkdir()
            prov3 = {"run_id": "20260917-164700-abc1234", "code_sha": "abc1234"}
            cases = (
                ("b16.jsonl", "B16",
                 "Neither of these is about influenza. Which one would tell me "
                 "so faster?", []),
                ("b15.jsonl", "B15",
                 "How many S. aureus runs are in ENA?", []),
                ("b09.jsonl", "B9", "What is PRJEB1234?",
                 [{"role": "tool", "tool": "brc_ena_study", "seconds": 9.0,
                   "result_chars": 1073223, "result_excerpt": "{...}"}]),
                ("b07.jsonl", "B7", "How many E. coli runs are in ENA?",
                 [{"role": "tool", "tool": "brc_ena_search", "seconds": 0.4,
                   "result_chars": 900, "result_excerpt": "{...}"}]),
            )
            for name, qid, q, steps in cases:
                rec = [{"role": "user", "text": q}, *steps,
                       {"summary": {"question_id": qid, "question_number": qid,
                                    "question": q, "model": "fixture",
                                    "tools_in_order": [s["tool"] for s in steps],
                                    "answer": "551,679 runs.", "answer_chars": 13,
                                    **prov3}}]
                (d3 / name).write_text(
                    "\n".join(json.dumps(x) for x in rec) + "\n", encoding="utf-8")
            k3, r3 = collect(pathlib.Path(tmp3))
            n3 = sum(len(v) for v in k3.values())
            t3 = sorted(x.split("]")[0][1:] for x in r3 if x.startswith("["))
            print(f"  harness guards: {n3} scored, {len(r3)} refused {t3}")
            if not (n3 == 2 and t3 == ["bad-question", "oversized-payload"]):
                failures.append(
                    "harness guards: the old B16 wording and the 1,073,223-char "
                    "payload must each be refused by name, and the two clean "
                    f"records beside them still scored; got {n3} scored, "
                    f"refused {r3}")

        if got != ("B", 16, 1, None, 15):
            failures.append(
                "denominator guard: a B lane with 1 file and no scorecard must "
                f"read ('B', 16, 1, None, 15), got {got}. A denominator taken "
                "from the glob or from the scorecard cannot pass this.")

    print()
    if failures:
        print(f"SELF-TEST FAILED — {len(failures)} problem(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"SELF-TEST PASSED — {len(entries)} fixtures, {len(all_checks)} checks, "
          f"every check observed firing, nothing fired on the clean control.")
    return 0


def provenance_of(summary: dict) -> tuple[bool, str]:
    """Can this record say which run and which code produced it?

    Runner measured the failure this exists for on 17 Sep at 14:39. The driver
    renamed records from `q02.jsonl` to `q2.jsonl`, both spellings then sat in
    `evals/runs/argo_gpt4o/`, and `sorted()` put the void 14:05 copy first. The
    stale records carry no `run_id` and no `code_sha`, so the fields that would
    expose the mix read as ABSENT, not as wrong -- and a scored table built on
    them would have looked clean.

    Archiving the old directories fixed today. It does not fix the next time
    someone restores one or runs an old driver, which is why the refusal lives
    in the scoring path rather than in the data.
    """
    missing = [k for k in ("run_id", "code_sha") if not summary.get(k)]
    if missing:
        return False, "no " + " and no ".join(missing)
    return True, str(summary["run_id"])


def qid_conflict(t: "Transcript") -> str | None:
    """Does the record's own id disagree with the directory it sits in?

    The question set is carried twice: by the run directory, because Runner
    derives RUN_TAG from the questions-file stem (`argo_gpt4o-routing/`), and
    by `question_id` in the summary. When the two agree the record is fine.
    When they disagree one of them is wrong, nothing here can say which, and
    scoring it would measure a routing answer against the demo rubric or the
    reverse. So it is refused, like a record with no provenance at all.
    """
    # `mine if mine in "RS" else ""` was the same hardcoding in miniature: a "B"
    # id collapsed to the demo set, so a B record in a demo directory -- or the
    # reverse -- agreed with itself and passed. `set_of` knows no fixed alphabet,
    # so the guard covers whatever set lands next without being edited again.
    mine = set_of(t.qid)
    theirs = set_of(t.qid_by_path)
    if mine != theirs:
        return f"summary says {t.qid!r}, directory says {t.qid_by_path!r}"
    return None


def attempted_from_scorecard(model_dir: pathlib.Path) -> tuple[int, int] | None:
    """(questions attempted, questions that died before a transcript) or None.

    The run directory cannot answer this, and it does not decline to answer --
    it gives a confident wrong one. `run_one()` in run_questions.py writes the
    jsonl from inside itself, so a question whose API call raises never reaches
    that write: the caller catches it and appends an `**ERROR**` row to
    `routing-scorecard.md`. The failure is recorded in the scorecard and is
    ABSENT from the directory.

    Measured by runner at 16:0x on 17 Sep and reproduced here before use. Rows
    minus ERROR rows equals the jsonl count for every lanes model on disk:
    claudehaiku45 16-2=14, gpt4o 16-1=15, gpt41mini 16-0=16. gemini25pro is the
    case that matters -- 16 rows, 16 ERROR rows, ONE file. Counted off the
    directory that reads "scorable 1 of 1, nothing held out", so a model that
    failed every question in the set outscores one that answered all sixteen
    adequately. Counted off the scorecard it reads 1 attempted-and-survived of
    16 attempted.

    Every failure found in this file today points the same way: the missing
    thing reads as clean rather than as missing. That is not a coincidence, it
    is the shape of the mistake -- an absence has no row to carry a flag.
    """
    sc = model_dir / "routing-scorecard.md"
    if not sc.is_file():
        return None
    rows = [ln for ln in sc.read_text(encoding="utf-8", errors="replace").splitlines()
            if re.match(r"^\|\s*[A-Z]?\d+\s*\|", ln)]
    if not rows:
        return None
    return len(rows), sum(1 for ln in rows if "ERROR" in ln)


# Where each question set's id list actually comes from. A *manifest*, not a
# rubric map and not a directory listing, because the denominator has to be the
# one number in this file that cannot shrink when something goes wrong.
#
#   glob      shrinks when a question crashes -- the jsonl is never written.
#   scorecard shrinks to nothing when a run dies before writing it: SEVEN of
#             thirteen live run directories had no scorecard at 16:2x.
#   EXPECTED* shrinks when I forget to add an id, and has no B entries at all,
#             which is the one lane where every missing transcript lives. This
#             was the second judge session's proposal and it does not reach the
#             affected lane.
#
# The manifest is written by whoever set the questions and is not touched by a
# run. It is the only source here that is independent of the thing being measured.
LANE_MANIFEST = {
    "":  ("QUESTIONS.md", r"(?m)^#+\s*Q(\d+)\b"),
    "B": ("BOBBY-LANES.md", r"(?m)^#+\s*B(\d+)\."),
}


def lane_ids(letter: str) -> tuple[set[int], str]:
    """The ids a lane is supposed to contain, and where that list came from."""
    spec = LANE_MANIFEST.get(letter)
    if spec:
        name, pat = spec
        f = pathlib.Path(__file__).resolve().parent / name
        if f.is_file():
            ids = {int(m) for m in re.findall(pat, f.read_text(encoding="utf-8",
                                                               errors="replace"))}
            if ids:
                return ids, name
    fallback = {"R": EXPECTED_ROUTING, "S": EXPECTED_STRESS}.get(letter)
    if fallback:
        return ({int(k[1:]) for k in fallback}, f"judge.py {letter} rubric")
    return set(), "unknown"


def coverage_rows(runs: pathlib.Path, by_model: dict[str, list[dict]]) -> list[dict]:
    """Per run directory: expected, attempted, on disk, scored -- and which ids are gone.

    Four numbers that disagree on purpose. `run_one()` in run_questions.py writes
    the jsonl from inside itself, so a question whose API call raises never
    reaches the write: the driver appends an `**ERROR**` row to
    `routing-scorecard.md` and leaves NOTHING in the directory. Counted off the
    directory, `argo_gemini25pro-bobby-lanes` reads 1 of 1 -- a model that failed
    all sixteen questions outscoring one that answered all sixteen. Counted off
    the manifest it reads 1 of 16.

    Verified on disk at 16:2x on 17 Sep, not inferred: scorecard rows minus ERROR
    rows equals the jsonl count for every directory that has a scorecard.
    """
    out = []
    for d in sorted(x for x in runs.iterdir() if x.is_dir()):
        names = [f.name for f in d.glob("*.jsonl")]
        att = attempted_from_scorecard(d)
        if not names and att is None:
            continue          # a nesting directory like `_archive-*`, not a run
        # Every run directory on disk is single-lane (checked across all 16).
        # Take the letter from the files; if the run died before writing any,
        # take it from the scorecard ids, which survive when the files do not.
        letters = {n[0] for n in names}
        letter = ""
        if len(letters) == 1:
            letter = letters.pop().upper()
            letter = "" if letter == "Q" else letter
        elif (d / "routing-scorecard.md").is_file():
            m = re.search(r"(?m)^\|\s*([A-Z])?\d+\s*\|",
                          (d / "routing-scorecard.md").read_text(encoding="utf-8",
                                                                 errors="replace"))
            letter = (m.group(1) or "") if m else ""
        want, src_name = lane_ids(letter)
        have = {int(re.sub(r"[^0-9]", "", n[:-6]) or 0) for n in names}
        out.append({"dir": d.name, "lane": letter or "demo",
                    "expected": len(want) or None, "src": src_name,
                    "attempted": None if att is None else att[0],
                    "errors": None if att is None else att[1],
                    "files": len(names), "scored": len(by_model.get(d.name, [])),
                    "missing": sorted(want - have)})
    return out


# A tool payload this large is a harness defect, not a model result. The
# legitimate maximum measured across every record in `evals/runs` on 17 Sep is
# 263,118 chars (`brc_ena_runs`), so 500,000 sits well clear of real data and
# well below the 1,073,223 that `brc_ena_study` was returning before the cap.
OVERSIZED_PAYLOAD = 500_000

# Questions that were defective when they ran. Each entry matches the DEFECT in
# the record itself, never a commit SHA or a timestamp: a SHA proxy would have to
# be re-dated by hand every time runner reruns, and would keep refusing records
# that are now fine. Matching the observable defect means each of these
# self-clears the moment the question is re-run with the corrected text.
BAD_QUESTIONS: list[tuple[str, str, str]] = [
    # B8 said "how many of THOSE" with no antecedent. Every question runs in a
    # fresh session, so there was no prior result for "those" to refer to. Six of
    # nine models correctly refused; the two that answered got the right number
    # by assuming E. coli. Scoring it rewards guessing and penalises noticing.
    # Kept as a FIXTURE deliberately -- it is the best example of a correct
    # refusal in the whole corpus.
    ("B8", r"how many of those",
     "the question said 'how many of those' with no antecedent -- every question "
     "runs in a fresh session, so a model that refused was RIGHT and a model that "
     "answered guessed the subject. Rewritten 17 Sep; re-run b08 to score it."),
    # B16 told models to read two zeros that do not exist: GEO returns 4 and ENA
    # returns 5. A correct answer was being scored as a failure.
    ("B16", r"neither of these is about influenza",
     "the question asserted two zero counts that do not exist -- GEO returns 4 and "
     "ENA returns 5 -- so a correct answer scored as failing. Rewritten 17 Sep; "
     "re-run b16 to score it."),
]


def question_guard(t: "Transcript") -> tuple[str, str] | None:
    """Refuse a record the HARNESS got wrong, before a model is blamed for it.

    Returns `(tag, why)` to refuse, or None to score. This is the same contract
    as the provenance and no-rubric gates above: refused by name, never silently
    dropped, and never counted as a model failure.
    """
    q = (t.question or "").lower()
    for qid, pattern, why in BAD_QUESTIONS:
        if t.qid == qid and re.search(pattern, q):
            return "bad-question", why
    for r in t.results:
        n = r.get("result_chars") or 0
        if n >= OVERSIZED_PAYLOAD:
            return ("oversized-payload",
                    f"`{r.get('tool', '?')}` returned {n:,} chars, at or above the "
                    f"{OVERSIZED_PAYLOAD:,} ceiling. Before the payload cap this "
                    f"hard-400ed four models outright, so any score here measures "
                    f"the harness, not the model. Re-run it.")
    return None


def collect(runs: pathlib.Path) -> tuple[dict[str, list[dict]], list[str]]:
    by_model: dict[str, list[dict]] = {}
    skipped: list[str] = []
    for model_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        rows = []
        # Every `*.jsonl`, not a list of prefixes. `_filename_id` at
        # run_questions.py:278 pads whatever prefix the question set uses --
        # `Q1 -> q01`, `R12 -> r12`, `S3 -> s03`, `B1 -> b01` -- so a prefix list
        # here is a list of the sets that existed on the day it was written. Twice
        # now a new set has landed and read as "not run yet". Take everything; let
        # the refusals below say what could not be scored and why.
        paths = sorted(model_dir.glob("*.jsonl"))
        for path in paths:
            try:
                t = Transcript(path)
            except Exception as exc:   # one unreadable transcript must not lose the run
                # ...but it must not vanish either. This printed to stderr and
                # never reached `skipped`, so a half-written or corrupt record
                # left no mark on the report at all -- the third place in this
                # file where "could not look" was rendered as nothing. Runner
                # writes into `runs/` while this runs, so it is a live risk.
                skipped.append(f"[unreadable] {model_dir.name}/{path.name} -- "
                               f"{type(exc).__name__}: {exc}")
                print(f"  could not read {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            ok, why = provenance_of(t.summary)
            if not ok:
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"[provenance] {rel} -- {why}")
                print(f"  SKIPPED, no provenance: {rel} -- {why}", file=sys.stderr)
                continue
            clash = qid_conflict(t)
            if clash:
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"[set-conflict] {rel} -- question set disagrees: {clash}")
                print(f"  SKIPPED, set disagrees: {rel} -- {clash}", file=sys.stderr)
                continue
            # A transcript whose question is the literal string "question 7" is
            # a placeholder, not an answer to anything. At 16:07 on 17 Sep runner
            # wrote fifteen of them into `runs/argo_claudesonnet45/` while
            # landing the stub-jsonl mechanism, each carrying
            # `RecursionError: maximum recursion depth exceeded` and the question
            # text "question 2". Scored, they became a full section reading
            # "scorable 0 of 15 -- 15 errored", which a reader takes as the model
            # failing every question. It is a fixture. The run directory gives no
            # other signal: same filenames, same summary shape, same model field.
            #
            # This is the inverse of every other bug in this file. Elsewhere
            # something real was rendered as nothing; here nothing was rendered
            # as a real and very bad result. Both are the report saying something
            # the run does not support, so both get the same treatment -- refused
            # by name, never quietly dropped and never scored.
            if re.fullmatch(r"(?i)\s*question\s+\d+\s*", t.question or ""):
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"[placeholder] {rel} -- question text is "
                               f"{t.question.strip()!r}, a stub, not a real question")
                print(f"  SKIPPED, placeholder: {rel} -- question text is "
                      f"{t.question.strip()!r}", file=sys.stderr)
                continue

            s = set_of(t.qid)
            if s not in SCORABLE_SETS:
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"[no-rubric] {rel} -- question set {s!r} "
                               f"({t.qid}) has no rubric in this file")
                print(f"  SKIPPED, no rubric: {rel} -- question set {s!r} is not "
                      f"one of {sorted(SCORABLE_SETS)}", file=sys.stderr)
                continue
            bad = question_guard(t)
            if bad:
                tag, why = bad
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"[{tag}] {rel} -- {why}")
                print(f"  SKIPPED, {tag}: {rel} -- {why}", file=sys.stderr)
                continue

            try:
                rows.append(judge_one(t))
            except Exception as exc:
                skipped.append(f"[unscorable] {model_dir.name}/{path.name} -- "
                               f"{type(exc).__name__}: {exc}")
                print(f"  could not score {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        if rows:
            by_model[model_dir.name] = rows
    return by_model, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=pathlib.Path, default=RUNS,
                    help="directory of <model>/qNN.jsonl transcripts")
    ap.add_argument("--out", type=pathlib.Path, default=OUT, help="markdown report to write")
    ap.add_argument("--quiet", action="store_true", help="write the report, print nothing")
    ap.add_argument("--self-test", action="store_true",
                    help="score the deliberately broken transcripts in evals/judge-fixtures/ "
                         "and assert every check fires on at least one of them")
    ap.add_argument("--fixtures", type=pathlib.Path, default=FIXTURES,
                    help="directory of fixtures for --self-test")
    args = ap.parse_args()

    if args.self_test:
        return self_test(args.fixtures)

    if not args.runs.is_dir():
        print(f"no transcripts: {args.runs} is not a directory", file=sys.stderr)
        return 2
    by_model, skipped = collect(args.runs)
    # An empty result still overwrites the report. The 14:05 matrix was declared
    # void and archived at 14:39, and this file went on showing its scored tables
    # with nothing on the page to say so. A report that cannot go blank is a
    # report that can quietly outlive its data.
    text = write_report(by_model, args.out, skipped=skipped,
                        coverage=coverage_rows(args.runs, by_model))
    if not args.quiet:
        print(text)
    print(f"\nwrote {args.out}", file=sys.stderr)
    if not by_model:
        if skipped:
            tally: dict[str, int] = {}
            for x in skipped:
                tag = x.split("]")[0][1:] if x.startswith("[") else "other"
                tally[tag] = tally.get(tag, 0) + 1
            detail = ", ".join(f"{n} {t}" for t, n in sorted(tally.items()))
            print(f"every record under {args.runs} was refused ({len(skipped)}: "
                  f"{detail}). Do not score the archive.", file=sys.stderr)
        else:
            print(f"no *.jsonl transcripts under {args.runs}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
