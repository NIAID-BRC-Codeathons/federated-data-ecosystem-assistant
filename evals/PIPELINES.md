# The pipelines, and how to prove each one works

A researcher does not ask for a tool. They ask a question, and the system has to decide
which sources can answer it, in what order, and — the part most systems skip — **which
parts cannot be answered, and why**.

This document names the eight pipelines that answer the board's questions, gives each one
a verification question with a ground truth, and states what a researcher would have done
instead. Every benchmark, test and demo in this repo should point back to a row here. If
a claim about the system cannot be traced to a pipeline and a verification question, it
is not yet a claim worth making.

Traceability: board question → pipeline → verification question → ground truth → the call
that produced it.

---

## Where this comes from

The team's day-1 whiteboard set two biological questions and four TODOs. TODO 4 is the
one that shapes this document:

> **"Why can't we answer certain questions with these resources?"**

That is not a caveat at the end of a demo. It is a deliverable, and it is the hardest one,
because a system that cannot say *no* convincingly cannot be trusted when it says *yes*.

The board's questions:

1. **"How many methicillin-resistant strains of *E. coli* are there?"** with four branches
   — read genome queries · what groups study these · tell me about strain X · structural
   implications
2. **"What is the latest in AMR for ←→"**

---

## The eight pipelines

| # | Pipeline | The researcher's question | Primary source |
|---|---|---|---|
| P1 | **Discovery** | "Where is there data about X?" | NDE |
| P2 | **ID crosswalk** | "Give me the actual accessions and files" | NCBI + ENA |
| P3 | **Expression** | "What genes changed under this treatment?" | GEO |
| P4 | **AMR genotype** | "How many isolates carry this resistance gene?" | NCBI Pathogen Detection |
| P5 | **Compute planning** | "What can I run, on which genome?" | BRC Analytics |
| P6 | **Literature** | "What is the latest on X?" | PubMed |
| P7 | **Protein & structure** | "What does this protein do?" | UniProt, MyGene, STRING |
| P8 | **Gap** | any question the board cannot answer | none — this is the point |

---

## P1 · Discovery — "where is there data about X?"

**Chain:** `nde_search_datasets` → `nde_facet_counts` → `nde_get_record` → accessions out.

**Verification question:** *"Where is there data on E. coli antimicrobial resistance, and
who funded it?"*

| | |
|---|---|
| Ground truth | 3,644 records; NIAID funds 44, Wellcome 30, NIGMS 26 |
| The trap | Pathogens live in `infectiousAgent.name`, hosts in `species.name`. The wrong field returns **1** where the right one returns **60,107**. Casing is irrelevant — the 16 Sep survey records this as a lowercase rule and that is wrong. |
| Without the system | You would query `species.name`, get 1 hit, and conclude NDE has no E. coli data |
| Honest limit | **NDE is not wired into `chatbot.py`.** It exists as a standalone stdio package. Until someone gives it an HTTP transport, P1 cannot run in the demo at all. |

**Status: blocked.** This is the single biggest hole in the system and it is nobody's lane.

---

## P2 · ID crosswalk — "give me the actual accessions and files"

**Chain:** `ncbi_taxonomy_lookup` → `ncbi_sra_search` → `ncbi_sra_runs_for_project` →
`brc_ena_study` → FASTQ URLs.

**Verification question:** *"What is actually in BioProject PRJNA715470?"*

| | |
|---|---|
| Ground truth | 382 SRA experiments · 369 ENA runs · **13 distinct organisms** |
| The trap | The project is labelled *E. coli*. It contains *Citrobacter*, *Enterobacter* and eleven other genera. **A project-level organism label is not a run-level organism.** |
| Without the system | You would take the project label at face value and build a cohort with three genera in it |
| Honest limit | ENA and NCBI disagree on run counts (369 vs 382 experiments — different units, different indexes). Say which you are quoting. |

---

## P3 · Expression — "what genes changed under this treatment?"

**Chain:** `geo_search` → `geo_series` → supplementary file URLs → (`ncbi_sra_runs_for_project`
for the raw reads behind it).

**Verification question:** *"Which E. coli expression studies involve ciprofloxacin, and
where are the numbers?"*

| | |
|---|---|
| Ground truth | 37 Series; GSE309890 ranks 1; `GSE309890_FPKMs_allSamples.csv.gz`, 510K |
| The traps | An unfiltered `gds` count returns **513**, mixing four record types. A GEO accession is not a UID, and guessing the GDS prefix returns a real record of the wrong type. The numbers are in **no** API response — only in FTP files. |
| Without the system | You would report 513, or fetch the wrong record silently, or conclude the expression values do not exist because `suppfile` says `'CSV'` |
| Honest limit | The supplementary file format is whatever the submitter uploaded. No schema, no units contract, no guarantee of a matrix. |

**This is the pipeline no other source on the board can serve.** GEO is the only
expression data here.

---

## P4 · AMR genotype — "how many isolates carry this gene?"

**Chain:** `ncbi_pathogen_organisms` → `ncbi_pathogen_isolate_count` →
`ncbi_pathogen_amr_genes` → `ncbi_pathogen_isolates`.

**Verification question:** *"How many E. coli isolates carry gyrA_S83L?"*

| | |
|---|---|
| Ground truth | **170,726** of 581,464 · `blaCTX-M-15` 75,487 · `mecA` **2** |
| The traps | The group name is `"E.coli and Shigella"`, not `"Escherichia coli"` — a wrong value returns a confident **0**. `totalCount` is 2× the distinct count for E. coli and **1×** for *S. aureus*, so halving unconditionally under-reports *S. aureus* by half. |
| Without the system | You would query the wrong group name and get zero, and read it as absence |
| Honest limit | `AST_phenotypes` — the measured susceptibility calls — is **not reachable** through the current tools. `_pathogen_filter` builds its query from five fields and that is not one of them. Genotype yes; phenotype not yet. |

---

## P5 · Compute planning — "what can I run, on which genome?"

**Chain:** `search_organisms` → `get_assemblies` → `get_compatible_workflows` →
`check_compatibility` → `resolve_workflow_inputs`.

**Verification question:** *"Can I run AMR gene detection on the E. coli reference genome,
and what inputs does it need?"*

| | |
|---|---|
| Ground truth | `compatible: true`; 17 workflows for haploid taxid 562; `ASSEMBLY_FASTA_URL` resolves, nothing unresolved |
| The traps | Only **2** E. coli assemblies exist — this is a reference catalogue, not a strain collection. You ask about taxid 562 and the assembly comes back as 511145. |
| Without the system | You would assume a genome-analysis platform has many E. coli genomes, and design around strains that are not there |
| Honest limit | Read-only. Launching a workflow needs a Galaxy key and a separate human yes, and read and execute deliberately live in different servers. |

---

## P6 · Literature — "what is the latest in AMR for X?"

**Chain:** `pubmed_search_articles` → `pubmed_fetch_abstracts` → link out to the data.

**Verification question:** *"What is the latest on ciprofloxacin resistance in E. coli?"*

| | |
|---|---|
| Ground truth | to be set — this is Everaldo's server and landed today |
| The trap | NCBI rewrites terms through MeSH silently. A count is meaningless without reading `querytranslation`. |
| Without the system | Search results ranked by relevance with no date control, and no path from a paper to its data |
| Honest limit | The paper-to-data hop is sparse: only 137 of 3,644 E. coli AMR records in NDE carry a PMID. |

**Status: unverified by us.** Needs a ground truth before it goes in a demo.

---

## P7 · Protein & structure — "what does this protein do?"

**Chain:** `mygene_query` → `uniprot_get_entry` → STRING interactions.

**Verification question:** *"What does E. coli GyrA do, and where are the
fluoroquinolone-resistance mutations?"*

| | |
|---|---|
| Ground truth | to be set |
| The trap | `uniprot_get_entry`'s docstring promises "interactions" and an "AlphaFold structure URL"; the returned dict has **neither**. A router reading the docstring promises the user a field that never arrives. |
| Without the system | Manual UniProt browsing |
| Honest limit | "Structural implications" — the board's fourth branch — is the weakest area on the whole board. BRC lists `PROTEIN_FOLDING` with **0** workflows, marked coming soon. |

**Open defect**, in someone else's file, reported not patched.

---

## P8 · Gap — the board's TODO 4

Not a fallback. The pipeline that runs when the honest answer is *no*, and the one that
decides whether this system can be trusted.

A good refusal has four parts: **the number that proves it**, **why the question is
malformed**, **the nearest answerable question**, and **the source that could answer it**.

**Verification question:** *"How many methicillin-resistant strains of E. coli are there?"*
— the board's own headline question.

A bad system says "I don't know." A worse one invents a number. This system should say:

> **2** — out of 581,464 *E. coli* and Shigella isolates in NCBI Pathogen Detection carry
> `mecA`. For comparison, 93,260 of 171,412 *S. aureus* isolates carry it.
>
> Two in half a million is the noise floor, and it is telling you the question is
> malformed rather than the data being missing: methicillin resistance is mediated by
> `mecA`/`mecC`, which are staphylococcal. Gram-negatives like *E. coli* are
> intrinsically resistant to methicillin, so no one types for it.
>
> The question you probably want is one of:
> - **MRSA** — 93,260 *S. aureus* isolates carry `mecA`
> - **ESBL *E. coli*** — 75,487 carry `blaCTX-M-15`
> - **Fluoroquinolone-resistant *E. coli*** — 170,726 carry `gyrA_S83L`
>
> Measured susceptibility (MIC values) is a different question again, and this system
> cannot reach it: the `AST_phenotypes` field exists in the source but is not exposed by
> any tool here. BV-BRC or CARD would answer it.

Every number in that answer carries the call that produced it. **That paragraph is the
product.** It is what a researcher cannot get from an LLM alone, from the API docs, or
from a search engine.

### The other gaps, stated

| Board question | Verdict | Why |
|---|---|---|
| "What groups study these?" | **partial** | NDE has funders and authors but is not wired in; NCBI gives `submitter_organization` and `CenterName` |
| "Structural implications" | **weak** | no folding workflows; UniProt structure links promised in a docstring but not returned |
| "Tell me about strain X" | **partial** | only if X is one of 5,506 curated assemblies; for E. coli that is 2 |
| "Latest in AMR for X" | **partial** | PubMed covers literature; the paper→data hop is sparse |
| MIC / susceptibility values | **cannot** | field exists upstream, no tool reaches it |

---

## How to use this document

1. **Every benchmark points at a pipeline.** A test that cannot name its pipeline and its
   verification question is measuring something nobody asked for.
2. **Every ground truth carries the call that produced it**, and a date. These are live
   counts; BRC's workflow catalogue changed mid-build between 16 and 17 Sep.
3. **A pipeline with no ground truth is not verified**, and should be labelled that way in
   any demo. P6 and P7 are in that state right now.
4. **P8 is scored like the others.** "Cannot answer" is a correct answer and should pass,
   not be excluded from the denominator.

Coverage today: P2–P5 verified with ground truth · P1 blocked, not wired · P6, P7
unverified · P8 specified, one case verified.
