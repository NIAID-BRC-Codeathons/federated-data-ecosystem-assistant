# Five-minute demo script

Every number below was measured on **17 September 2026** and traces to
[`REPORT.md`](REPORT.md), [`PIPELINES.md`](PIPELINES.md) or a pasted live call in
[`QUESTIONS.md`](QUESTIONS.md). Re-check them the morning of the demo, off the venue
network, and update this file before anyone says them out loud.

The shape of the five minutes: answer the board's own question, show that the answer
carries the call that made it, then spend the last ninety seconds showing the system
refuse two things correctly. **The refusals are the strongest part.** A system that
cannot say no convincingly cannot be trusted when it says yes.

## The script

| Time | On screen | The call | Number, 17 Sep | Say |
|---|---|---|---|---|
| **0:00** | Slide: the whiteboard question, verbatim | — | — | "This is the question the team put on the board on day one. Watch what the system does with it." |
| **0:20** | Chat: *"How many methicillin-resistant strains of E. coli are there?"* | `ncbi_pathogen_organisms(contains="coli")` then `ncbi_pathogen_isolate_count(organism="E.coli and Shigella", amr_genes="mecA")`, same again for `Staphylococcus aureus` | **2** of **581,464** · *S. aureus* **93,260** of **171,412** | "Two in half a million is the noise floor. The number is not the answer — it is the proof the question is malformed." |
| **1:00** | The `api_call` block in the same result | — | `request_cost` per answer | "Every number comes back with the call that produced it. Nothing here is the model's recollection." |
| **1:20** | Chat: the reframing | `ncbi_pathogen_isolate_count(organism="E.coli and Shigella", amr_genes="gyrA_S83L")` | **170,726** of **581,464** — 29% | "It offers the question you probably meant: fluoroquinolone resistance, and that one has a real answer." |
| **2:00** | Chat: *"Which E. coli expression studies involve ciprofloxacin, and where are the numbers?"* | `geo_search(organism="Escherichia coli", term="ciprofloxacin", entry_type="gse")` then `geo_series("GSE309890")` | **37** Series, not **513** · 1 file, `GSE309890_FPKMs_allSamples.csv.gz` | "Without the entry-type filter this returns 513, mixing four kinds of record. And the expression values are in no API response at all — only in an FTP file." |
| **3:00** | Chat: *"Can I run AMR gene detection on the E. coli reference genome?"* | BRC's own federated server: `check_compatibility(iwc_id="amr_gene_detection-main", accession="GCF_000005845.2")` then `resolve_workflow_inputs` with the same arguments | `compatible: true` · **17** workflows for haploid taxid 562 · **2** E. coli assemblies | "It resolves the inputs and then stops. Nothing executes here — a human launches it at brc.usegalaxy.org." |
| **3:45** | **Failure 1.** Chat: *"Get the raw reads behind GSE309890 and variant-call them against K-12."* | `geo_series("GSE309890")` → `PRJNA1363958` → `ncbi_sra_runs_for_project(accession="PRJNA1363958")` → `ncbi_sra_run_metadata(accessions=…, detail="summary")`, against the workflow's `data_requirements` | 6 runs, all RNA-seq · workflow declares `library_strategy: ["WGS"]` | "The reads exist, and they do not satisfy this workflow's stated input. A router matching on organism alone would have promised a run that fails." |
| **4:15** | **Failure 2.** Chat: *"Who published GSE309890?"* | `geo_series("GSE309890")` — `pubmedids` is empty, so the result carries `pubmed_note` | "no link registered" | "It does not say the study is unpublished. It says no link is registered in GEO, which is a different fact." |
| **4:30** | Slide: the gap list (whiteboard TODO 4) | — | NDE not wired · 0 protein-folding workflows · `AST_phenotypes` readable but not filterable · funder missing on 3,401 of 3,644 NDE records | "These are the things we cannot answer, and we can tell you exactly why for each one." |
| **5:00** | Stop | — | — | — |

Two numbers on that gap slide are not from this repo's measured record: the NDE funder
coverage (3,401 of 3,644) comes from the 17 Sep NDE check in the home chat's plan, and
NDE cannot be demonstrated live at all. Say "measured against NDE production on 17
September, not through this chatbot" or leave it off.

## No-model fallback

The venue's Argo gateway only resolves on the Argonne network, and a reply that says
`ACCESS DENIED` with zero usage is a denial, not an answer. There are two different
failures and they need different fallbacks.

**No model, sources reachable.** Skip the chat window and call the tools directly. The
servers are plain functions; `uv run run_mcp_servers.py` and a Python prompt reproduces
every step above without an LLM anywhere in the path. Rehearse this once — it is the
likeliest Friday outcome.

**No network, or no time to debug.** Every step already has its real output saved in a
file in this repo. Show the file. Nothing is re-run, nothing can fail, and the output is
the same output that was captured live.

| Step | If there is no model | If there is no network |
|---|---|---|
| 0:20 methicillin | Call `ncbi_pathogen_organisms` and `ncbi_pathogen_isolate_count` directly | `REPORT.md`, last row — both counts with their calls |
| 1:20 gyrA_S83L reframe | `ncbi_pathogen_isolate_count` directly | `PIPELINES.md` P4 ground truth |
| 2:00 GEO | `geo_search` and `geo_series` directly | `REPORT.md`, the ciprofloxacin and expression-files rows |
| 3:00 BRC workflow | `curl` BRC's MCP endpoint, or `tests/test_bobby_servers_live.py::test_the_amr_workflow_is_still_compatible_with_k12` off-site | `QUESTIONS.md` Q7 — the pasted HTTP 200 for `check_compatibility` and `resolve_workflow_inputs` |
| 3:45 RNA-seq refusal | `geo_series` then `ncbi_sra_runs_for_project` directly; read `data_requirements` out of the workflow JSON | `QUESTIONS.md` Q8 — the pasted `data_requirements` block |
| 4:15 no PubMed link | `geo_series("GSE309890")` directly; `pubmed_note` is in the returned dict | `QUESTIONS.md` Q8, second failure |
| 4:30 gap slide | — | Static slide either way |

One preparation step makes the whole fallback work: **run `uv run evals/run_eval.py
--markdown evals/REPORT.md` on Friday morning from off the venue network** — a hotel
connection or a phone tether — and bring the regenerated `REPORT.md` with you. That gives
you fresh numbers without spending the venue's shared NCBI budget on stage.

## Do not do on stage

- **No `pytest -m live`.** It is deselected by default for a reason.
- **No eval harness from the venue IP.** NCBI's ceiling is 3 requests/second *per source
  IP across all its hosts*, and at a codeathon that budget is shared with the whole room.
  A 429 mid-demo reads as "your system is broken."
- **No number that was not re-checked that morning.** These are live counts. BRC's
  workflow catalogue changed between 16 and 17 Sep while this was being built.
- **Do not put "0/10 vs 10/10" on a slide.** [`README.md`](README.md) explains why it
  overstates; the frame is provenance, honest nulls and premise repair.
- **Do not quote 150,926 for `blaCTX-M-15` or 94,336 for MRSA.** Those are raw index rows
  from `QUESTIONS.md`. Take every denominator from `ncbi_pathogen_isolate_count`:
  **75,487** and **93,260**. `ncbi_pathogen_organisms` halves its row count, which is
  right for *E. coli* and wrong for *S. aureus*; its own note says to use
  `ncbi_pathogen_isolate_count` for an exact figure.
- **Do not say the chatbot has been run end to end.** It has not, on the machine these
  servers were built on. Say which layer you are showing.
- **Do not imply an analysis ran.** The BRC server is read-only by design and the answer
  stops at the resolved FASTA URL.
- **Do not edit `LLM_MODEL` and commit it.** Change it locally, leave it out of the diff.
