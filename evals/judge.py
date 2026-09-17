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
    10: {"primary": {"nde_facet_counts", "nde_search_datasets", "nde_get_record"},
         "kind": "gap", "wired": False, "source": "NDE (not in MCP_SERVERS)"},
    11: {"primary": {"nde_list_repositories", "nde_search_datasets"},
         "kind": "gap", "wired": False, "source": "NDE (not in MCP_SERVERS)"},
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
GROUND_TRUTH: dict[int, list[dict]] = {
    2:  [{"value": 581464, "what": "distinct isolates in the `E.coli and Shigella` group",
          "decoys": {1162675: "index rows, not distinct isolates"}}],
    3:  [{"value": 37, "what": "GEO Series for E. coli + ciprofloxacin",
          "decoys": {513: "the unfiltered `db=gds` count over four record types"}}],
    4:  [{"value": 2, "what": "E. coli assemblies in BRC Analytics"},
         {"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
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
            "misroute": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
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
    "R10": {"primary": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
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
    "S3":  {"primary": {"ncbi_pubmed_search", "ncbi_pubmed_abstracts"},
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
    "R4":  [{"value": 2, "what": "E. coli assemblies in BRC Analytics"},
            {"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
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
    "S11": [{"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
    "S13": [{"value": 17, "what": "haploid-compatible workflows for taxid 562"}],
    "S14": [{"value": 48421, "what": "ENA studies -- the denominator that does exist"}],
    "S18": [{"value": 2, "what": "E. coli assemblies in BRC Analytics"}],
    "S19": [{"value": 875, "what": "amino acids in GyrA",
             "decoys": {101: "ccdB, which is `uniprot_search` hit 1 without a symbol check"}}],
    "S20": [{"value": 37, "what": "GEO Series",
             "decoys": {513: "the unfiltered `db=gds` count"}}],
}


def expected_for(qid: str | None) -> dict:
    """One lookup across the three maps. Demo questions are int-keyed."""
    blank = {"primary": set(), "kind": "answer", "source": "unknown"}
    if not qid:
        return blank
    if qid.startswith("R"):
        return EXPECTED_ROUTING.get(qid, blank)
    if qid.startswith("S"):
        return EXPECTED_STRESS.get(qid, blank)
    try:
        return EXPECTED.get(int(qid), blank)
    except ValueError:
        return blank


def ground_truth_for(qid: str | None) -> list[dict]:
    if not qid:
        return []
    if qid[0] in "RS":
        return GROUND_TRUTH_RS.get(qid) or []
    try:
        return GROUND_TRUTH.get(int(qid)) or []
    except ValueError:
        return []


# The `gds_513` trap is not a property of question 3; it is a property of any
# question whose true answer is the filtered Series count.
GDS_513_QIDS = {"3", "7", "R15", "S4", "S20"}


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
    m = re.search(r"[qrs](\d+)", path.stem.lower())
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
                     "decoy": decoy_hit,
                     "decoy_note": (spec.get("decoys") or {}).get(decoy_hit),
                     "substitute": substitute,
                     "weak": spec["value"] < FABRICATION_MIN})
    if any(r["state"] == "wrong" for r in rows):
        overall = "wrong"
    elif any(r["state"] == "miss" for r in rows):
        overall = "miss"
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
    if 94336 in answer_numbers(a) and not re.search(r"(?i)\bmec-?c\b", a):
        fired.append("meca_94336")

    return fired


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
        "set": ("routing" if (t.qid or "").startswith("R")
                else "stress" if (t.qid or "").startswith("S") else "demo"),
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
        "min_chain": check_min_chain(t, exp),
        "breadth": check_breadth(t, exp),
        "refusal_parts": check_refusal_parts(t, exp),
        "forbidden_units": check_forbidden_units(t, exp),
        "control_refusal": check_control_refusal(t, exp),
        "no_turn": check_no_turn(t),
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
        mark = {"hit": "✓", "miss": "**missing**", "wrong": "**wrong**"}[r["state"]]
        weak = "?" if (r["weak"] and r["state"] == "hit") else ""
        if r["state"] == "wrong":
            bits.append(f"{r['value']:,} {mark} (said {r['decoy']:,})")
        elif r["state"] == "miss" and r.get("substitute"):
            bits.append(f"{r['value']:,} {mark} (a competing small figure is in the answer)")
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
    L += [
        f"- routed {routed_yes}/{routed_scored} scored "
        f"({len(live) - routed_scored} not scorable: source not wired, or no tool applies)",
        f"- opened on the right source {first_yes}/{first_scored} — the strict read of "
        f"the same question",
        f"- ground truth, where PIPELINES.md pins one ({len(gts)} questions): "
        f"{gt_hit} correct · {gt_wrong} **wrong figure** · {gt_miss} **never stated**",
        f"- fabrication flags {fab} · unmatched-but-truncated {unm}",
        f"- other trap flags {sum(traps.values())} "
        f"({', '.join(f'{k}×{v}' for k, v in traps.most_common()) or 'none'})",
        f"- gap questions ({len(nulls)}): {n_dec} declined · {n_rsn} gave a reason · "
        f"{n_src} named a source · **{n_all} did all three**",
        "",
    ]
    return L


def write_report(by_model: dict[str, list[dict]], out: pathlib.Path,
                 skipped: list[str] | None = None) -> str:
    L = ["# Judge report", "",
         "Generated by `evals/judge.py` from the transcripts under `evals/runs/`. No model",
         "was asked anything: every flag below is decided from the transcript and the",
         "expectations encoded at the top of that file, which come from `QUESTIONS.md` and",
         "`PIPELINES.md`. Disagree with a flag by editing `EXPECTED` or `TRAP_NOTES` and",
         "re-running.", ""]
    if skipped:
        # In the report, not only on stderr. A record refused for missing
        # provenance is the one omission a reader cannot infer from the tables.
        L += [f"**{len(skipped)} record(s) refused for missing provenance and NOT scored.** "
              "A record with no `run_id` and no `code_sha` cannot say which run or which "
              "code produced it, and two spellings of the same question in one directory "
              "read as absent rather than wrong. Re-run them.", ""]
        L += [f"- `{x}`" for x in skipped] + [""]

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
    "honest_null_declines", "honest_null_reason", "honest_null_alternative",
    "denied", "error", "empty_answer", "no_turn",
    # ROUTING.md / STRESS.md, added 17 Sep at Runner's request. Each one is here
    # because the demo rubric is blind to it: a shotgun passes `routed`, an
    # early stop says only true things, and a bare decline and a four-part
    # refusal both score as "did not fabricate".
    "misroute_called", "misroute_used", "declared_missing",
    "min_chain_short", "breadth_over", "refusal_parts_short",
    "forbidden_units", "control_refused",
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
    mine = (t.qid or "")[:1]
    theirs = (t.qid_by_path or "")[:1]
    mine = mine if mine in "RS" else ""
    theirs = theirs if theirs in "RS" else ""
    if mine != theirs:
        return f"summary says {t.qid!r}, directory says {t.qid_by_path!r}"
    return None


def collect(runs: pathlib.Path) -> tuple[dict[str, list[dict]], list[str]]:
    by_model: dict[str, list[dict]] = {}
    skipped: list[str] = []
    for model_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        rows = []
        # q/r/s, not q. `_filename_id` at run_questions.py:278 pads whatever
        # prefix the question set uses -- `Q1 -> q01`, `R12 -> r12`, `S3 -> s03`
        # -- so a routing directory holds no `q*.jsonl` at all. Globbing for `q`
        # alone found nothing there and the model dropped out of the report with
        # no row and no skip line, which reads as "not run yet" rather than as
        # "I could not see it". Caught by the set-conflict self-test on 17 Sep.
        paths = sorted(q for pat in ("q*.jsonl", "r*.jsonl", "s*.jsonl")
                       for q in model_dir.glob(pat))
        for path in paths:
            try:
                t = Transcript(path)
            except Exception as exc:   # one unreadable transcript must not lose the run
                print(f"  could not read {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue
            ok, why = provenance_of(t.summary)
            if not ok:
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"{rel} ({why})")
                print(f"  SKIPPED, no provenance: {rel} -- {why}", file=sys.stderr)
                continue
            clash = qid_conflict(t)
            if clash:
                rel = f"{model_dir.name}/{path.name}"
                skipped.append(f"{rel} (question set disagrees: {clash})")
                print(f"  SKIPPED, set disagrees: {rel} -- {clash}", file=sys.stderr)
                continue
            try:
                rows.append(judge_one(t))
            except Exception as exc:
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
    text = write_report(by_model, args.out, skipped=skipped)
    if not args.quiet:
        print(text)
    print(f"\nwrote {args.out}", file=sys.stderr)
    if not by_model:
        if skipped:
            print(f"every record under {args.runs} was refused for missing "
                  f"provenance ({len(skipped)}). Re-run them; do not score the "
                  f"archive.", file=sys.stderr)
        else:
            print(f"no qNN.jsonl transcripts under {args.runs}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
