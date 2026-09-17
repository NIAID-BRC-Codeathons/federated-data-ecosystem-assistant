# Ground truth — every number this project asserts, with its unit and its read-date

Maintained by `verifier`. Built 17 Sep 2026, 15:25.

A figure without its unit is not a fact. *S. aureus* is 171,412 or 93,260 or 94,336
depending on whether you are counting isolates, isolates carrying `mecA`, or isolates
carrying `mecA` **or** `mecC` — three defensible answers to what sounds like one
question. Every row below therefore carries **what it counts**, not just what it is.

## How to read the Status column

| status | means |
|---|---|
| **live** | I ran the call myself today and the output is pasted in `_reports/verifier.md`. |
| **same-day** | A teammate ran it on 17 Sep and pasted the call. I have **not** re-measured it. |
| **stale-risk** | Last read at 10:15 today, on a source that moves. Not re-read since. |
| **derived** | Arithmetic over other rows in this table. No call of its own. |
| **WRONG** | Measured, and the label next to it in the repo is incorrect. See the finding. |

`same-day` is not a weaker claim about the *number*; it is an honest claim about **who
saw it**. Rows marked `stale-risk` are the ones that can embarrass us on Friday.

## The state of this table right now

- **The GEO gap is closed.** It was the largest hole in this file until 15:41. The
  matrix released the NCBI budget, and I re-read the GEO lane live at 0.3 req/s.
  **Nothing drifted** — 37, 513, 1,929, 449, 51, 29,029 and 510 KB all came back
  identical to the 10:15 reading. Four GEO rows are still `stale-risk` because I did not
  re-run them, and they are labelled as such.
- **One measured figure breaks a question.** `evals/BOBBY-LANES.md` B16 is built on GEO
  and ENA both returning zero for influenza. They return **4** and **5**. FINDING V7 in
  `_reports/verifier.md`; do not score B16 until it is fixed.
- **ENA and BRC Analytics are different hosts** and do not touch the NCBI budget, so
  those rows are live as of 15:05–15:22.
- **B16 was already scored before the warning existed.** A BOBBY-LANES run started at
  15:52 (five model directories under `evals/runs/*-bobby-lanes`, read-only listing);
  FINDING V7 did not exist until 15:55. Any scorecard from that run marks a *correct*
  answer wrong on B16, and on S14 for the separate reason in FINDING V6. Drop both cases
  before reading a number off it.
- **One figure is mislabelled, not stale** — see FINDING V6 at the bottom.
- **Two claims this project makes about its own plumbing are wrong**, both added at 16:20:
  the NCBI API key would make our rate over-subscription worse rather than better
  (FINDING V8), and the BV-BRC token does not expire in 20 minutes (FINDING V9). Each
  changes an item Bobby is being asked to act on in `_reports/STATUS.md`.

---

## ENA — `https://www.ebi.ac.uk/ena/portal/api/` (free of the NCBI budget)

The unit trap on this host is the **result grain**. `read_run` counts runs,
`study` counts studies, and `read_study` — despite the name — is run-grained.

| figure | what it counts | exact call | read | status | who |
|---:|---|---|---|---|---|
| 551,679 | ENA **runs** whose tax_id is exactly 562 | `count?result=read_run&query=tax_eq(562)` | 15:05, 15:20 | live | verifier |
| 606,048 | ENA runs for 562 **and its descendants** | `count?result=read_run&query=tax_tree(562)` | 15:05 | live | verifier |
| 54,369 | runs `tax_eq(562)` **excludes** that `tax_tree(562)` holds | 606,048 − 551,679 | 15:05 | derived | verifier |
| 20,925 | ENA runs for tax_id exactly 511145 (K-12 MG1655) | `count?result=read_run&query=tax_eq(511145)` | 15:05 | live | verifier |
| 6,917 | ENA **studies**, tax_id exactly 562 | `count?result=study&query=tax_eq(562)` | 15:20 | live | verifier |
| 10,616 | ENA studies, 562 and descendants | `count?result=study&query=tax_tree(562)` | 15:20 | live | verifier |
| 497,414 | ENA runs, tax_eq(562), `library_strategy=WGS` | `brc_ena_search(taxonomy_id="562", library_strategy="WGS")` | 15:05 | live | verifier (lead 17 Sep) |
| 13,838 | ENA runs, tax_eq(562), `library_strategy=RNA-Seq` | same, `library_strategy="RNA-Seq"` | 15:05 | live | verifier (lead 17 Sep) |
| 511,252 | WGS + RNA-Seq | 497,414 + 13,838 | 15:05 | derived | verifier |
| 40,427 | runs in **neither** WGS nor RNA-Seq | 551,679 − 511,252 | 15:05 | derived | verifier |
| 181,408 | ENA runs, tax_eq(1280) *S. aureus* | `count?result=read_run&query=tax_eq(1280)` | 15:05 | live | verifier (lead 17 Sep) |
| 195,638 | ENA runs, tax_tree(1280) | `count?result=read_run&query=tax_tree(1280)` | 15:05 | live | verifier |
| 48,421 | ENA **runs** — E. coli, study title matches `*resistance*` | `brc_ena_search(organism="Escherichia coli", title_contains="resistance")` | 15:22 | live | verifier |
| 94 | ENA **studies** for that same query | `count?result=study&query=scientific_name="Escherichia coli" AND study_title="*resistance*"` | 15:22 | live | verifier |
| 916 | runs in study PRJEB1234 (= ERP002070) | `brc_ena_study("PRJEB1234")` | 15:10 | live | verifier |
| 0 | runs for a misspelled `scientific_name`; `zero_result_note` fires | `brc_ena_search(organism="Escherichia colli")` | 17 Sep | same-day | lead |
| 369 / 13 | runs / distinct organisms in PRJNA715470 | `brc_ena_study("PRJNA715470")` | 17 Sep | same-day | README.md |
| 50 | **page size** of BRC's federated `search_ena`, with `has_more: true` | federated `search_ena` | 17 Sep | same-day | REPORT.md |
| 200 / 1000 | rows returned / ceiling of `/ena/taxonomy/562?limit=N`; HTTP 422 above 1000 | `GET brc-analytics.org/api/v1/ena/taxonomy/562?limit=N` | 15:12 | live | verifier |
| 1,764,464 | **nothing.** The sum of three overlapping counts; the S6 decoy | 551,679 + 631,321 + 581,464 | — | derived | adversary |
| 0.29.0 | BRC Analytics API version, production, healthy | `brc_federation_status()` | 17 Sep | same-day | lead |

**Do not add the first, second and third rows of the 1,764,464 line together.** Runs,
experiments and isolates are three units over three services. That is the point of S6.

---

## NCBI Pathogen Detection — `ncbi.nlm.nih.gov/pathogens/pathogens-srv/`

Two units live here and they are not interchangeable: **distinct isolates** (the
`target_acc` facet's `numBuckets`) and **index rows** (`totalCount`). The service
double-indexes *E. coli* and does **not** double-index *S. aureus*, so no fixed ratio
converts one into the other.

**Seven rows here were re-read live at 16:14-16:17 on 17 Sep and are marked `live`.**
The matrix released the NCBI budget at 16:09:44. **Zero drift** — every figure came back
identical to the earlier reading, so Pathogen Detection did not move today. The remaining
`same-day` rows are a teammate's reading that I have not repeated.

| figure | what it counts | exact call | read | status | who |
|---:|---|---|---|---|---|
| 581,464 | **distinct isolates** in the `E.coli and Shigella` group | `?limit=0&facets=target_acc[\|\|1\|1]&fq=taxgroup_name==["E.coli and Shigella"]` | 17 Sep 16:14 | live | verifier (re-read) |
| 1,162,675 | **index rows** for the same group | same call, `totalCount` | 17 Sep 16:14 | live | verifier (re-read) |
| 106 | curated organism groups; none viral | `?limit=0&facets=taxgroup_name[\|\|1\|500]` | 17 Sep | same-day | adversary |
| 1,766,824 | index rows, *Salmonella enterica* — the largest group | same facet call | 17 Sep 16:14 | live | verifier (re-read) |
| 349,968 | index rows, *K. pneumoniae* | same facet call | 17 Sep 16:14 | live | verifier (re-read) |
| 16,981 | index rows, *Haemophilus influenzae* — the "influenza" substring trap | same facet call | 17 Sep | same-day | adversary |
| 171,412 | *S. aureus* distinct isolates **and** its facet count — ratio 1, not 2 | `target_acc` facet, `taxgroup_name=="Staphylococcus aureus"` | 17 Sep 16:14 | live | verifier (re-read) |
| 93,260 | *S. aureus* isolates carrying `mecA` **alone** | `amr_genes="mecA"` on the S. aureus group | 17 Sep 16:14 | live | verifier (re-read) |
| 94,336 | *S. aureus* isolates carrying `mecA` **or** `mecC` | the MRSA query | 17 Sep 16:14 | live | verifier (re-read) |
| 2 | *E. coli* isolates carrying `mecA` — the Q13 proof number | `amr_genes="mecA"` on `E.coli and Shigella` | 17 Sep 16:14 | live | verifier (re-read) |
| 75,487 | distinct isolates carrying `blaCTX-M-15` | `amr_genes="blaCTX-M-15"`, `target_acc` facet | 17 Sep 16:14 | live | verifier (re-read) |
| 150,926 | **index rows** for the same gene | same call, `totalCount` | 17 Sep 16:14 | live | verifier (re-read) |
| 170,726 | distinct isolates carrying `gyrA_S83L` | `amr_genes="gyrA_S83L"`, `target_acc` facet | 17 Sep 16:14 | live | verifier (re-read) |
| 341,342 | **index rows** for the same mutation | same call, `totalCount` | 17 Sep 16:14 | live | verifier (re-read) |
| 7,611 | distinct AMR gene **symbols** in the E. coli vocabulary | `ncbi_pathogen_amr_genes(organism="E.coli and Shigella")` | 17 Sep | same-day | lead |
| 1,119,505 | index rows, `blaEC` | AMR gene facet | 17 Sep | same-day | lead |
| 1,065,222 | index rows, `acrF` | AMR gene facet | 17 Sep | same-day | lead |
| 868,024 | index rows, `mdtM` | AMR gene facet | 17 Sep | same-day | lead |
| 396,464 | index rows, `sul2` | AMR gene facet | 17 Sep | same-day | lead |
| 20,608 | index rows, `mcr-1.1` | AMR gene facet | 17 Sep | same-day | lead |
| 378 | distinct **phenotype values** in the E. coli group | `?limit=0&facets=AST_phenotypes[\|\|1\|500]&fq=taxgroup_name==["E.coli and Shigella"]`, `numBuckets` | 17 Sep 16:23 | live | verifier |
| 1,548 | **distinct isolates**, ciprofloxacin-**resistant** E. coli | `fq=... and AST_phenotypes==["ciprofloxacin=R"]`, `target_acc` facet | 17 Sep 16:23 | live | verifier |
| 3,096 | **index rows** for the same filter — ratio 2.000 | same call, `totalCount` | 17 Sep 16:23 | live | verifier |
| 6,563 | **distinct isolates**, ciprofloxacin-**susceptible** E. coli | `fq=... and AST_phenotypes==["ciprofloxacin=S"]`, `target_acc` facet | 17 Sep 16:23 | live | verifier |
| 13,126 | **index rows** for the same filter. `QUESTIONS.md` lists this beside distinct-isolate figures | same call, `totalCount` | 17 Sep 16:23 | live | verifier |
| 9,036 | **distinct isolates** with **any** ciprofloxacin AST result — 1.55% of 581,464 | `fq=... and AST_phenotypes==[all six cipro values]`, `target_acc` facet | 17 Sep 16:23 | live | verifier |
| 18,072 | **index rows** for the same filter | same call, `totalCount` | 17 Sep 16:23 | live | verifier |
| 6 | ciprofloxacin phenotype **values** in the vocabulary: bare `ciprofloxacin`, `=S`, `=R`, `=I`, `=ND`, `=NS` | the `AST_phenotypes` facet buckets | 17 Sep 16:23 | live | verifier |
| 883,560 | **distinct isolates**, *Salmonella enterica* — the largest group | `ncbi_pathogen_isolate_count(organism="Salmonella enterica")` | 17 Sep 16:16 | live | verifier |
| 175,207 | **distinct isolates**, *K. pneumoniae* | `ncbi_pathogen_isolate_count(organism="Klebsiella pneumoniae")` | 17 Sep 16:16 | live | verifier |
| 174,162 | **distinct isolates**, *C. jejuni* | `ncbi_pathogen_isolate_count(organism="Campylobacter jejuni")` | 17 Sep 16:16 | live | verifier |
| 348,290 | **index rows**, *C. jejuni* — ratio 2.000 | same call, `totalCount` | 17 Sep 16:16 | live | verifier |
| 82,947 | **distinct isolates**, *L. monocytogenes* — **and** its index-row count. Ratio 1, like *S. aureus* | `ncbi_pathogen_isolate_count(organism="Listeria monocytogenes")` | 17 Sep 16:16 | live | verifier |
| 85,706 | **nothing real.** `ncbi_pathogen_organisms` `approx_isolates` for *S. aureus*. Exactly half the true 171,412 — see FINDING V10 | `ncbi_pathogen_organisms(limit=500)` | 17 Sep 16:17 | **WRONG** | verifier |
| 41,473 | **nothing real.** Same defect, *L. monocytogenes*. Half of 82,947 | `ncbi_pathogen_organisms(limit=500)` | 17 Sep 16:17 | **WRONG** | verifier |
| 0 | isolates matching `AMR_genotypes==["CFTR"]` — a true zero for a wrong premise | `fq=AMR_genotypes==["CFTR"]` on the E. coli group | 17 Sep | same-day | adversary |
| 0 | isolates for `organism="Escherichia coli"` — a **false** zero, wrong group name | `ncbi_pathogen_isolate_count(organism="Escherichia coli")` | 17 Sep | same-day | runner / matrix |

That last row is the headline finding, and it is the reason this file exists: a zero
that means "I could not look" is indistinguishable from a zero that means "there is
none" unless someone writes down which one it is.

**The two `WRONG` rows are the same disease in a new place.** `ncbi_pathogen_organisms`
derives its per-organism figure by halving the service's row count
(`mcp_servers/ncbi_lib/server.py:1691`), on a comment that says "the 2x duplication is
uniform". It is not: of six groups I measured, four are double-indexed and two are not.
For those two the tool reports exactly half the truth, with no signal that it has. This
table's own header has said "does **not** double-index *S. aureus*" since 15:43 — the
fact was already written down and the code still halves. Full evidence in FINDING V10;
`ncbi_lib` is not my file, so this is reported, not patched.

---

## NCBI GEO and E-utilities — **re-read live 15:41-15:52, 17 Sep**

Was the largest gap in this table. No longer. Twenty paced HTTP requests in one process
through `mcp_servers/geo.py`'s own `_wait_turn()`, about 0.3 req/s against a 3 req/s
ceiling. The four rows still marked `stale-risk` are the ones I did not re-run.

| figure | what it counts | exact call | read | status | who |
|---:|---|---|---|---|---|
| 37 | GEO **Series** for E. coli + ciprofloxacin | `geo_search(organism="Escherichia coli", term="ciprofloxacin", entry_type="gse")` | 15:41 | **live** | verifier |
| 513 | **all four `db=gds` record types**, same query, no `[Filter]` clause | `esearch db=gds term='"Escherichia coli"[Organism] AND (ciprofloxacin)'` | 15:41 | **live** | verifier |
| 1.92% | 37 / 1,929 coverage | derived from two live rows | 15:41 | derived | verifier |
| 50 | GEO **Series** for E. coli + heat shock (B6) | `geo_search(organism="Escherichia coli", term="heat shock", entry_type="gse")` | 15:44 | **live** | verifier |
| 1,929 | E. coli **Series** — the coverage denominator | `"Escherichia coli"[Organism] AND "gse"[Filter]` | 15:44 | **live** | verifier |
| 500 | S. aureus **Series** (B15 GEO side) | `"Staphylococcus aureus"[Organism] AND "gse"[Filter]` | 15:45 | **live** | verifier |
| 4 | E. coli **Series** for influenza (B16 GEO side) — **the file says 0** | `geo_search(organism="Escherichia coli", term="influenza", entry_type="gse")` | 15:47 | **live, and it breaks B16** | verifier |
| 449 | E. coli **Platforms** | `..."gpl"[Filter]` | 15:45 | **live** | verifier |
| 51 | E. coli **curated DataSets** | `..."gds"[Filter]` | 15:45 | **live** | verifier |
| 29,029 | E. coli **Samples** | `..."gsm"[Filter]` | 15:45 | **live** | verifier |
| 5,464 | S. aureus **Samples** — measured only to prove 500 is not a ceiling | `"Staphylococcus aureus"[Organism] AND "gsm"[Filter]` | 15:45 | **live** | verifier |
| 9,881 | **Series** for `antibiotic`, no organism, after MeSH rewrite | `(antibiotic) AND "gse"[Filter]` | 15:45 | **live** | verifier |
| 1 | supplementary **files** on GSE309890 (B2) | `geo_series("GSE309890", list_files=True)` | 15:52 | **live** | verifier |
| 510 KB | size of `GSE309890_FPKMs_allSamples.csv.gz`, modified 2025-11-14 11:10 | same call | 15:52 | **live** (GEO's own listing; I did not download it) | verifier |
| GPL24659 | platform of GSE309890 (B5); taxon `E. coli str. K-12 substr. MG1655`, 6 samples | same call | 15:52 | **live** | verifier |
| PRJNA1363958 | BioProject of GSE309890 — the B14 hand-off to ENA | same call | 15:52 | **live** | verifier |
| 139,839 | `db=gds` records for `antibiotic`, **all four types**, after MeSH rewrite | `esearch db=gds term=antibiotic` | 10:15 | stale-risk | lead |
| 8 | `db=gds` hits for the literal string `GSE309890` — Series + Platform + 6 Samples | `esearch db=gds term=GSE309890` | 10:15 | stale-risk | lead |
| 3 | `db=gds` hits for `GSM9284462`; **first hit is the Series, not the Sample** | `esearch db=gds term=GSM9284462` | 10:15 | stale-risk | lead |
| 491 | Series ever run on platform GPL24659 | `esearch db=gds term=GPL24659` | 10:15 | stale-risk | lead |
| 275 | sibling samples in the `GSM9284nnn` FTP directory | GEO FTP listing | 10:15 | stale-risk | lead |
| 631,321 | NCBI **SRA experiments** for txid562 — a third unit for "E. coli data" | `esearch db=sra term=txid562[Organism:exp]` | 17 Sep | same-day | adversary |
| 382 | SRA experiments in PRJNA715470 | `esearch db=sra` | 17 Sep | same-day | lead |

**Nothing drifted.** Every figure carried from 10:15 that I re-read came back identical:
37, 513, 1,929, 449, 51, 29,029, 510 KB. Five and a half hours on these indexes moved
nothing, which is itself worth knowing — the GEO rows are safer than the Pathogen
Detection rows, not less safe.

**Two apparent discrepancies that are unit differences, not drift.**

- 139,839 against 9,881 for `antibiotic`. The first has no `[Filter]` clause and sums
  Series, Platforms, Samples and DataSets; the second is Series only. Both correct, 14.2x
  apart. This is the B1 trap again at a different scale.
- `geo-lane-state.md`'s "8" is `esearch db=gds term=GSE309890`, the Series plus its own
  component records — not a file count. GSE309890 has **one** supplementary file.

**Two round numbers that are real.** B6 = 50 and B15 = 500 both look like caps. They are
not: E. coli Series returned 1,929 and Samples 29,029 in the same batch, and a raw
`esearch` for S. aureus Samples returned 5,464, so nothing is clamped at 500. The raw
`esearch` for S. aureus Series returned 500 too, so it is not a wrapper artefact.

**513 cannot be produced by the tool.** `geo_search` forces `key = (entry_type or "gse")`
at `mcp_servers/geo.py:564` and raises on an unknown value; no argument drops the
`[Filter]` clause. I had to call `geo._eutils` directly. Good server design, but it means
B1's stated failure mode — "quoting the unfiltered number" — is not reachable by a model
driving this tool.

**UID arithmetic** (costs zero requests, and is a convention rather than a measurement):
GSE = 200000000 + n, GPL = 100000000 + n, GSM = 300000000 + n, GDS = the bare number.
Re-confirmed live at 15:52: `geo_resolve_accession("GSE309890, GDS309890")` returns uid
`200309890` and uid `309890`. Holds to 8 digits; GSM is at 7 today.

---

## Other servers on the board — not Bobby's lane, recorded for completeness

| figure | what it counts | exact call | read | status | who |
|---:|---|---|---|---|---|
| 875 | amino acids in GyrA, UniProt P0AES4 | `GET rest.uniprot.org/uniprotkb/P0AES4` | 17 Sep | same-day | adversary |
| 20 | PDB cross-references on P0AES4, **default** entry | same call | 17 Sep | same-day | adversary |
| 0 | PDB cross-references on the same entry **through `ENTRY_FIELDS`** | `?fields=<ENTRY_FIELDS>` | 17 Sep | same-day | adversary |
| 101 | amino acids in ccdB — `uniprot_search` hit 1 with no symbol check (S19 decoy) | `uniprot_search(query="gyrA")` | 17 Sep | same-day | adversary |
| 283 | PubMed hits in a 2026 window **under `datetype=pdat`** | `esearch db=pubmed ... datetype=pdat` | 17 Sep | same-day | lead |
| 5,273 | the **all-time** count for the same term (S3 decoy) | same term, no date window | 17 Sep | same-day | lead |
| 274 | the same window with **no `datetype`**, relevance order (S3 decoy) | same term, no `datetype` | 17 Sep | same-day | lead |
| 3,644 | NDE **records** in the E. coli + AMR slice | NDE query | 17 Sep | same-day | lead |
| 3,401 | of those 3,644, records **missing** `funding.funder.name` | NDE facet | 17 Sep | same-day | lead |
| 60,107 | NDE **production** records, `infectiousAgent.name:"escherichia coli"` | NDE production | 17 Sep | same-day | lead |
| 118,625 | NDE **staging**, the same query — a different number from the same question | NDE staging | 17 Sep | same-day | adversary |
| 0 | NDE **production**, BV-BRC catalogue | `includedInDataCatalog.name:"Bacterial and Viral Bioinformatics Resource Center"` | 17 Sep | same-day | adversary |
| 2 | *E. coli* **assemblies** in BRC Analytics | `get_assemblies(taxonomy_id="562")` | 17 Sep | same-day | lead |
| 17 | **haploid-compatible workflows** for taxid 562 | `get_compatible_workflows(ploidies=["HAPLOID"], taxonomy_id="562")` | 17 Sep | same-day | lead |
| 511145 | the taxid BRC's assembly actually sits under — the **strain**, not 562 | `get_assemblies` response | 17 Sep | same-day | lead |

---

## Numbers about the project rather than about the data

These are asserted on slides and in `_reports/`, so they belong here too.

| figure | what it counts | source | read | status | who |
|---:|---|---|---|---|---|
| 1 / 14 | eval cases the **documented API call** gets right | `evals/run_eval.py` → `REPORT.md` | 17 Sep | same-day | lead |
| 13 / 14 | eval cases **our MCP tools** get right | same | 17 Sep | same-day | lead |
| 8 / 13 | of the 13 baseline failures, how many are **genuinely silent** (HTTP 200, no flag) | my recount of `REPORT.md` rows | 15:00 | live | verifier |
| 574 | tests in the full suite, all passing while the demo was dead | `_reports/STATUS.md` | 17 Sep | same-day | lead |
| 13 / 3 s | offline tests in `test_chatbot_wiring.py` / their runtime | `_reports/STATUS.md` | 17 Sep | same-day | lead |
| 66 | `@*.tool()` **definitions in source** across the nine ported servers on `main`; 66 unique, no collisions. **Excludes** the four remote `MCP_SERVERS` entries (`string`, `expasy`, `brc-analytics`, `bv-brc`) | `_reports/sentinel.md:155` | 17 Sep | same-day | sentinel |
| 99 | tools that **load at runtime** across 12 of 13 servers with `bv-brc` forced to fail. **Includes** the remote servers — `string` alone advertises 17 | runner, against a forced BV-BRC failure | 17 Sep | same-day | runner |
| 7.5 / 3 | req/s our three NCBI servers **target in sum** vs the per-IP ceiling | FINDING V5, from the source constants | 15:13 | live | verifier |
| ~~25.8 / 10~~ | **superseded at 16:03** — see the three rows below. Kept because I escalated it | FINDING V8 | 16:12 | **stale within 9 min** | verifier |
| 6.5 / 3 | req/s the three NCBI servers target in sum **at current HEAD**, vs the per-IP ceiling | `geo.py` 1.0 + `pubmed.py` 2.5 + `ncbi_lib` 3.0 | 16:31 | **live** | verifier |
| 22.4 / 10 | the same sum **once an API key is set** (3.33 + 9.09 + 10.0), vs the keyed ceiling | source constants at HEAD | 16:31 | **live** | verifier |
| 4.5 / 3 | the same sum with `NCBI_MAX_RPS=1`; `pubmed.py` ignores it, so 2.5 of that is unreachable | source constants at HEAD | 16:31 | **live** | verifier |
| 3.0 / 3 | req/s `ncbi_lib` claims **on its own** — the entire keyless ceiling, one server | `limiter.py:28` | 16:31 | **live** | verifier |
| 7200 s | BV-BRC token lifetime the token itself **declares** (`expires_in`) | `.bvbrc_oauth_tokens.json`, key present, value read | 16:20 | live | verifier |
| ~21 min | interval after which a re-auth was observed. **This is not a lifetime.** See FINDING V9 | `chatbot.py:112-120` comment, 14:20 to 14:41 | 16:20 | **WRONG as stated** | verifier |
| 10 | local commits not on `origin/bobby/ncbi-and-brc-analytics` (`d5429ca`) | `_reports/STATUS.md` | 14:52 | same-day | lead |

---

## Drift risks, named

### 1. ~~`analyze.py` hardcodes 581,464 with no read-date~~ — **CLOSED by analyst, 16:14**

**I was right when I reported it and I am wrong to keep saying it.** `analyze.py:133-150`
now carries the read date, the exact call, who read it, why the figure matters, and the
instruction "Re-read before Friday: change the value AND the date together", plus
`PROOF_NUMBERS_READ = "2026-09-17"`. It even cites this file. The fix is better than the
one I proposed. **I took the re-read it asks for at 16:14 and all five `PROOF_NUMBERS`
figures for Q13 came back identical**, so the constants are correct as they stand and
need no edit. Correction sent to analyst at 16:20.

The original report is kept below rather than deleted, because a drift risk that was
real and got fixed is worth being able to see.

#### Original report, 15:43 — superseded

```python
PROOF_NUMBERS = {13: {2, 581464, 93260, 171412, 94336},
                 14: {1548, 9036, 581464, 378},
                 ...
                 11: {118625, 0}}
```

NCBI Pathogen Detection grows daily. When 581,464 becomes 583,000, a model that calls
the tool and reports the **new correct figure** will fail `PROOF_NUMBERS` and be scored
as not having produced the proof number. The failure will look like a model defect and
will be a stale constant.

This is **analyst's file**, so it is reported here, not edited. The fix is not a bigger
number — it is carrying the read-date with the figure, or reading it at run time. The
same constant appears in `judge.py` (`GROUND_TRUTH[2]`, `[13]`, `R12`, `S9`) and in
`judge.py:118` as `ENA_TRUE_RUNS = 551679`, which is stable by comparison.

**Reported to:** the hub, for routing to analyst.

### 2. ~~Every GEO row above is five hours old~~ — CLOSED 15:52

Re-read live. Nothing drifted. Four rows remain `stale-risk` and are marked: the
unfiltered `antibiotic` count (139,839), the two `db=gds` literal-string counts (8 and
3), the GPL24659 Series count (491) and the FTP sibling count (275). None of them is
quoted in a question, which is why I spent the budget elsewhere.

### 3. `brc_federation_status()`'s `known_limitations` carries `"verified": "2026-09-17"` as a literal

The dates are Python string literals in a static list, not timestamps of a check. The
list will keep claiming it was verified today for as long as the strings sit there. I
re-measured four of the five entries live and they hold (FINDING V4 in
`_reports/verifier.md`); the fifth, the catalog/phenotype claim, I did not check.

---

## FINDING V6 — one figure in this table is mislabelled in the repo

**48,421 is runs, not studies.** Measured 15:22:

```
query: scientific_name="Escherichia coli" AND study_title="*resistance*"
read_run    {"count":"48421"}      <- what brc_ena_search reports as total_matching
study       {"count":"94"}
read_study  {"count":"48421"}      <- run-grained despite the name; this is how it got in
```

`evals/judge.py:403` calls it *"ENA studies -- the denominator that does exist"* and
`evals/STRESS.md:362` and `:375` repeat "studies". `evals/ADVERSARIAL.md:394` is
correct — it claims no unit at all.

S14 asks *"what fraction of E. coli ENA **studies** mention carbapenem?"* With 48,421
in the expected-value map, a model answering **94** is scored a miss and a model
answering **48,421** — wrong by 515× — is scored a hit. Full write-up and suggested
wording in `_reports/verifier.md`. Sent to the hub, judge and adversary at 15:24. I did
not edit either file.

---

## FINDING V7 — one question's ground truth is unreachable

**`BOBBY-LANES.md` B16 says "read the two zeros". Neither side returns zero.**
Measured 15:47 and 15:52 through the real servers:

| side | call | total | `zero_result_note` |
|---|---|---|---|
| GEO | `geo_search(organism="Escherichia coli", term="influenza", entry_type="gse")` | **4** | did not fire |
| ENA | `brc_ena_search(organism="Escherichia coli", title_contains="influenza")` | **5** | did not fire |
| ENA | `brc_ena_search(title_contains="influenza")` | **131,403** | did not fire |

Three of the four GEO hits are one human autoantibody study (GSE222765 / 764 / 760) in
which E. coli is one of seven taxa on the record — GEO tags a Series with **every**
organism in it, so `[Organism]` is not a filter on subject. The fourth, GSE122286, is
genuinely *Influenza A virus; Escherichia coli*.

A model that correctly reports 4 and 5 fails a rubric that requires it to report zeros.
Full write-up and a suggested rewrite in `_reports/verifier.md`. Escalated to the hub at
15:55. Not my file; not edited.

---

## FINDING V8 — the NCBI API key does not fix the rate problem, it scales it

> **Corrected 16:31, and the correction is against myself.** I escalated this at 16:08–16:12
> quoting `geo.py` at a 0.5 s gap. Commit `38110ac` had already fixed `geo.py` at **16:03:05**
> — five minutes before I sent it. My read was accurate when I took it and stale when I used
> it, which is the exact failure this file exists to catch. The conclusion survives; the
> numbers below are restated at HEAD. `geo.py` is no longer part of the problem.
>
> | limiter | keyless | keyed | honours `NCBI_MAX_RPS` |
> |---|---:|---:|---|
> | `geo.py` — fixed 16:03, divides by `NCBI_SERVER_SHARE` (default 3) | 1.00/s | 3.33/s | yes |
> | `pubmed.py` — untouched since 12:24 | 2.50/s | 9.09/s | **no** |
> | `ncbi_lib/limiter.py` — untouched since 09:12 | 3.00/s | 10.00/s | yes |
> | **sum vs ceiling** | **6.5 vs 3** (2.17×) | **22.4 vs 10** (2.24×) | |
>
> So: still over on both, the key still makes the absolute overshoot worse, and the two
> remaining offenders are `pubmed.py` — which reads neither `NCBI_MAX_RPS` nor
> `NCBI_SERVER_SHARE` — and `ncbi_lib`, which claims the **whole** 3/s keyless ceiling by
> itself. Applying `geo.py`'s own divide-by-share to both would land the sum at exactly 3.0/s.

**The count of three was checked, not assumed.** I looked for a fourth NCBI client and found
two false leads and no real one. `mcp_servers/pdn.py` is Pathoplexus and Loculus
(`lapis.pathoplexus.org`, `api.loculus.genspectrum.org`), not NCBI Pathogen Detection, despite
the name — it has no pacing at all and does not need any. `mygene.py` and `myvariant.py` both
contain `ncbi.nlm.nih.gov` strings, but they are output link templates (`NCBI_GENE_URL`,
`DBSNP_URL`, `CLINVAR_URL`); the hosts they request are `mygene.info` and `myvariant.info`.
So the NCBI budget is shared by exactly three processes: `geo.py`, `pubmed.py`, and `ncbi.py`
through `ncbi_lib`.

The original finding, as escalated, follows.


`_reports/STATUS.md` item 1 asks Bobby for an NCBI API key on the reasoning that it triples
our limit. That is true of the ceiling and not of what we emit, because all three limiters
raise themselves independently the moment the key is non-empty.

| process | keyless | with a key | source |
|---|---:|---:|---|
| `mcp_servers/geo.py` | `MIN_REQUEST_GAP = 0.5` → 2.0/s | `0.15` → 6.67/s | lines 91, 169 |
| `mcp_servers/pubmed.py` | `_MIN_INTERVAL = 0.4` → 2.5/s | `0.11` → 9.09/s | line 77 |
| `mcp_servers/ncbi_lib/limiter.py` | `DEFAULT_RPS_KEYLESS = 3.0` | `DEFAULT_RPS_WITH_KEY = 10.0` | lines 28-29 |
| **sum** | **7.5/s** vs a 3/s ceiling, 2.50× over | **25.76/s** vs a 10/s ceiling, 2.58× over | |

Each file is correct **per process**; the ceiling is per IP. `NCBI_MAX_RPS` is read in exactly
one place, `limiter.py:35`, and `grep` finds it in neither `geo.py` nor `pubmed.py`, so
runner's `NCBI_MAX_RPS=1` reaches one limiter of three and takes the sum to 5.5/s. No
environment setting reaches 3/s.

Runner measured the bound this does *not* cross: a BOBBY-LANES run at `--parallel 6` produced
zero 429s, because the six workers are async tasks in one driver process sharing one set of
MCP client connections, so a single limiter still sees all six. The figure above is a
cross-process sum and says nothing about concurrency inside one driver.

**Not demonstrated.** I have not observed a 429 from this stack. This is arithmetic off the
constants; provoking a real throttle on the shared venue IP is not something I will do.

---

## FINDING V9 — the BV-BRC token does not expire in 20 minutes

The one token file in the workspace, `federated-data-ecosystem-assistant/.bvbrc_oauth_tokens.json`,
has `mtime 14:20:15`, carries `expires_in: 7200`, and **has no `obtained_at` key**. The discard
path at `chatbot.py:119` is guarded by `if obtained_at and expires_in:`, so it is skipped and
the token is returned unconditionally. A token issued at 14:20 was alive until 16:20; the
re-auth happened at 14:41. Not expiry, and not the guard.

The fix written for this has therefore **never taken effect**: `obtained_at` is written only by
`set_tokens()` on a successful login, and the file has not been rewritten since 14:20:15. Same
shape as STATUS.md's own NCBI caveat — wiring in place, never exercised.

I killed my own first theory rather than recording it as the answer. `OAUTH_TOKEN_FILE` is a
relative path, so I expected a different working directory to explain it; the matrix ran
`uv run evals/run_questions.py` from the repo root, the same directory as `chainlit run
chatbot.py`. Same CWD, file visible. The relative path stays a latent hazard for anyone
launching from `evals/`, but it is not what happened here.

---

## What I have not done

- ~~I did not re-measure any Pathogen Detection figure~~ — **done at 16:14-16:17, after
  BOBBY-LANES released the budget at 16:09:44.** Seven figures re-read through the real
  tool stack, paced at one call per 3 s: 581,464 · 2 · 93,260 · 94,336 · 171,412 · 75,487 ·
  170,726. **Zero drift.** What I still have not done is measure the other **100 of 106**
  organism groups, so I cannot say how far the `approx_isolates` defect in FINDING V10
  spreads beyond the two groups where I caught it.
- ~~I did not verify the Q14 AST figures~~ — **done at 16:23. All four match.** 378
  distinct phenotype values, 1,548 resistant, 6,563 susceptible, 9,036 with any
  ciprofloxacin AST. `QUESTIONS.md` is right that these are unreachable through the board's
  *tools* — `_pathogen_filter` has no `AST_phenotypes` argument — so I called
  `pathogen_client.retrieve()` with a hand-built `fq`. That is the library's own client and
  its shared rate limiter, so the calls were paced; I changed no code. The `QUESTIONS.md`
  row counts confirm too: 18,072 and 13,126 index rows, both exactly 2x their isolate counts.
  **Every figure in `PROOF_NUMBERS[13]` and `PROOF_NUMBERS[14]` is now live-verified.**
- I did not download `GSE309890_FPKMs_allSamples.csv.gz`. Its size and date are GEO's own
  directory listing, not my observation of the file.
- I did not re-run the four GEO rows still marked `stale-risk`.
- **On the BV-BRC token (Job 3 claim D) I now have a partial answer, not none** — see
  FINDING V9. I still have not logged in, so I have not observed the token being accepted
  or rejected by `dev-9.bv-brc.org`, and I cannot say which mechanism forces the re-auth.
  What I can say from the artifact on disk is that it is not expiry and not the age guard.
- I did not verify the NDE, PubMed, UniProt or MyGene rows beyond reading where they
  came from. They are outside my lane and are recorded, not audited.
