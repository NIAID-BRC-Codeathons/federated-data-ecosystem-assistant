# Routing questions — can the system *choose* a source?

`QUESTIONS.md` names the source in the question. "Are there any *E. coli* **expression
studies** about ciprofloxacin?" tells the model to use GEO before it has decided anything. So
that set measures whether the tools work. It does not measure the thing the project is named
for: **a smart query system that turns a plain-language request into data fetched from the
right source.**

This file measures that. Fifteen questions where **the source is deliberately unstated and a
plausible wrong choice exists.** The failure being hunted is not a broken tool. It is a working
tool, called confidently, against a source that cannot answer the question — HTTP 200 all the
way down.

Each case carries five fields:

| field | what it is for |
|---|---|
| **could plausibly be chosen** | every source a competent router might reach for, not a strawman |
| **correct, and why** | the source that can actually answer, and the property that makes it the one |
| **wrong but plausible** | the specific mis-route, and what the answer looks like when it happens — this is what the judge scores |
| **the answer must contain** | the content test, independent of which tools were called |
| **ground truth** | a number, with where it came from |

## The three routing failures, named so they can be counted

| failure | what it looks like |
|---|---|
| **plausible mis-route** | one source, confidently, and it is the wrong one. No sign that another exists. |
| **silent substitution** | the question asks about X, the source answers about the nearest thing it holds, and the answer never says so — a literature count read as prevalence, a study count read as incidence, an index row count read as an isolate count |
| **shotgun** | eight sources called because none was chosen. Arrives somewhere, spends the whole NCBI budget, and cannot say why any single tool was called |

A shotgun is a routing failure even when the answer is right. Under a 3 requests/second
per-IP ceiling shared with the whole room, a router that cannot choose is a router that
cannot run at the venue.

## Provenance of every number below

**I measured nothing here.** I do not run models and I do not call NCBI or BRC — that budget
belongs to the matrix run. Two labels, and nothing in between:

- 📋 **quoted** — measured by a teammate on 2026-09-17, cited to the file and section holding
  the call. Not re-run by me.
- 🗂 **repo-verified** — read out of this checkout by me on 2026-09-17, with the file and line.
- ⚠️ **unverified** — nobody has measured it. The exact call is written out and queued in
  `_reports/run-queue.md`.

Every 📋 number is a live count against a live service. They move. Re-check before quoting one
on a slide.

**Weighting.** Ten of the fifteen turn on the four sources this branch can verify ground truth
for: GEO (`mcp_servers/geo.py`, port 8009 🗂), BRC Analytics local (`brc_analytics.py`, port
8008 🗂), the federated `brc-analytics` remote, and NCBI Pathogen Detection through Jonathan's
server. The rest are here because the *mis-route* lands on one of ours.

---

## R1. "What's known about ciprofloxacin resistance in E. coli?"

The archetype. Five sources answer it, each about something different, and none of them is
broken — which is what makes picking one and stopping the failure.

**Could plausibly be chosen:** GEO (what changes in expression under the drug) · NCBI Pathogen
Detection (how often the resistance alleles appear in sequenced isolates) · PubMed (what has
been published) · NDE (which datasets exist, and who funded them) · UniProt (what the drug's
target protein is).

**Correct, and why:** there is no single correct source, and **saying so is the answer.** The
question is under-specified at the level of *what kind of knowledge* — mechanism, prevalence,
literature, data availability. A router that decomposes it and names which source serves each
branch has routed correctly even if it then executes only one branch.

**Wrong but plausible:** PubMed alone, returning **5,273** hits 📋 as "what's known". That is
what has been *written*, not what the data shows, and the number is a MeSH-rewritten text match
📋. Wrong in the other direction: Pathogen Detection alone, returning 170,726, which is the
prevalence of one allele and says nothing about mechanism.

**The answer must contain:** the decomposition, at least two sources named with what each
contributes, and one number carrying its unit and source. If only one branch runs, it must say
which and why.

**Ground truth:** PubMed "Escherichia coli ciprofloxacin resistance" **5,273** with no date
window, **283** with a 2026 window under `datetype=pdat&sort=pub_date` 📋 (`PIPELINES.md` P6 and
P8 `board-latest`) · GEO **37** Series 📋 (`QUESTIONS.md` Q3) · Pathogen Detection `gyrA_S83L`
**170,726** distinct isolates of **581,464** 📋 (Q9) · NDE **3,644** E. coli AMR records 📋
(`PIPELINES.md` P1).

---

## R2. "How common is the gyrA S83L mutation out in the world?"

**Could plausibly be chosen:** NCBI Pathogen Detection · GEO · NDE · PubMed.

**Correct, and why:** Pathogen Detection. It is the only source on the board that counts
*occurrences across sequenced isolates*, which is what "how common" means.
`ncbi_pathogen_amr_genes` then `ncbi_pathogen_isolate_count`.

**Wrong but plausible:** GEO. The words "gyrA" and "resistance" both appear in GEO study
titles, `geo_search` returns a count, and **37** 📋 reads like an answer to "how common". It is a
count of *experiments about the topic*, not of organisms carrying the allele — two numbers four
orders of magnitude apart that share no unit. This is silent substitution, and it is the most
likely mis-route on the board involving a server we own.

**The answer must contain:** 170,726 of 581,464 (29%), the group name *E.coli and Shigella*
because the denominator is not pure *E. coli*, and the word "isolates" rather than "strains".

**Ground truth:** **170,726** distinct isolates, 341,342 index rows, of **581,464** 📋
(`QUESTIONS.md` Q9). GEO ciprofloxacin Series **37** 📋 (Q3) — the number the mis-route returns.

---

## R3. "Show me the data behind the finding that sertraline drives ciprofloxacin resistance."

**Could plausibly be chosen:** PubMed · GEO · NDE · NCBI SRA.

**Correct, and why:** GEO. "The data behind a finding" is the deposited experiment, and
GSE309890 is an adaptive-evolution RNA-seq study of exactly this 📋. `geo_search` →
`geo_series` → the supplementary file URLs. The SRA hop
(`ncbi_sra_runs_for_project` on PRJNA1363958 📋) reaches the raw reads.

**Wrong but plausible:** PubMed. The question contains a claim, PubMed indexes claims, and
`ncbi_pubmed_search` returns abstracts saying the same thing. An answer built from an abstract
is a citation. The user asked for data.

**The answer must contain:** the accession GSE309890, its organism at strain level
(*Escherichia coli* str. K-12 substr. MG1655 📋), where the numbers actually live — the
submitter-uploaded supplementary file, never an API response 📋 — and that GEO's `pubmedids` for
this Series is `[]` with `elink` returning no linkset 📋, so "no paper is linked" must not be
reported as "no paper exists".

**Ground truth:** GSE309890 rank 1 of 37 📋 · BioProject PRJNA1363958, 6 samples, `pubmedids`
`[]` 📋 (`QUESTIONS.md` Q8) · `GSE309890_FPKMs_allSamples.csv.gz`, 510 KB, served over `ftp://`
📋 (`ADVERSARIAL.md` A15).

---

## R4. "I have an E. coli genome. What can I actually do with it?"

**Could plausibly be chosen:** BRC Analytics (federated remote) · NCBI · BV-BRC · GEO.

**Correct, and why:** BRC Analytics. It is the only source on the board that answers "what can
be run" — `search_organisms` → `get_assemblies` → `get_compatible_workflows` →
`check_compatibility`. **17** haploid workflows for taxid 562 📋.

**Wrong but plausible:** NCBI. `ncbi_assembly_info` returns a rich record, and assembly
statistics are easy to narrate as an answer to "what can I do with it". Contig count is not a
capability.

**The answer must contain:** that BRC holds **2** assemblies and is a curated catalogue, not a
strain collection 📋; the 17 workflows; and the read-only limit — every input resolves and
**nothing launches**, a human starts the run at brc.usegalaxy.org with a Galaxy key 📋. An
answer implying a run happened is the A14 failure in `ADVERSARIAL.md`.

**Ground truth:** 2 assemblies, GCF_000005845.2 (K-12 MG1655, taxid 511145) and GCF_000008865.2
(Sakai, taxid 386585); 17 compatible workflows for `ploidies=["HAPLOID"]`, taxid 562 📋
(`QUESTIONS.md` Q4). NCBI holds 452,563 *E. coli* assemblies 📋 (Q4) — the number that shows why
BRC is a catalogue and not a census.

---

## R5. "Which E. coli are resistant to ciprofloxacin?"

A routing question with a definition question underneath it. Genotype and phenotype are
different sources, different numbers, and only one of them is reachable from here.

**Could plausibly be chosen:** Pathogen Detection genotype (`AMR_genotypes`) · Pathogen
Detection phenotype (`AST_phenotypes`) · BV-BRC · GEO · CARD (off-board).

**Correct, and why:** Pathogen Detection genotype, **with the substitution declared**. The
phenotype field exists and carries measured S/I/R calls — 1,548 resistant, 6,563 susceptible 📋
— but `_pathogen_filter` builds its query from five fields (`taxgroup_name`, `AMR_genotypes`,
`host`, `isolation_source`, `epi_type`) and `AST_phenotypes` is not one of them 🗂, so no tool on
this board can filter on it. The reachable answer is the genotype proxy. The honest answer says
it is a proxy.

**Wrong but plausible:** reporting 170,726 `gyrA_S83L` carriers as "ciprofloxacin-resistant
*E. coli*". Carrying a first-step mutation is not a clinical resistance call, and an answer that
elides that is silent substitution with a real number behind it.

**The answer must contain:** the word "genotype" attached to 170,726; that measured
susceptibility exists in the index but is **not filterable through these tools**; and that only
9,036 of 581,464 isolates (1.6%) carry any ciprofloxacin AST result at all 📋 — so even the
phenotype answer, if it were reachable, would rest on a 1.6% denominator.

**Ground truth:** `gyrA_S83L` **170,726** 📋 · `AST_phenotypes==["ciprofloxacin=R"]` **1,548**
distinct, `=S` **6,563**, any cipro AST **9,036** of 581,464, 378 distinct phenotype strings 📋
(`QUESTIONS.md` Q14) · `_pathogen_filter` five fields 🗂 (`mcp_servers/ncbi_lib/server.py`, read
2026-09-17).

---

## R6. "Where can I download E. coli antimicrobial-resistance datasets?"

**Could plausibly be chosen:** NDE · NCBI SRA · BRC Analytics local (ENA) · BV-BRC · GEO.

**Correct, and why:** NDE. "Where is there data about X" is the discovery question, and NDE is
the catalogue that indexes across repositories — `nde_facet_counts` → `nde_search_datasets` →
`nde_get_record`, then the accessions out of `isBasedOn[]` into NCBI 📋. **NDE is now wired**:
`chatbot.py` carries an `nde` entry at `http://127.0.0.1:8007/mcp-nde` 🗂, `run_mcp_servers.py`
starts it without `--stdio` 🗂, and `nde_mcp/server.py` defaults to streamable-HTTP on
`DEFAULT_PORT = 8007` 🗂. Three documents in this repo still say it is stdio-only and
unreachable. They are stale — see `STRESS.md` S9.

**Wrong but plausible:** going straight to `ncbi_sra_search`. It returns real accessions and
looks like an answer, but it can only see NCBI — the question asked *where*, and answering from
inside one repository assumes the answer away. The subtler mis-route is NDE pointed at
**production**, which returns a clean **0** for BV-BRC while staging holds **118,625** 📋.

**The answer must contain:** a repository breakdown rather than one repository; the denominator
warning on funding — `funding.funder.name` is missing on **3,401 of 3,644** records, so "who
funded it" is answerable for under 7% 📋; and, if BV-BRC is mentioned, which NDE host was
queried.

**Ground truth:** 3,644 E. coli + AMR records, faceting to Figshare 1,640 · NCBI SRA 1,229 ·
NCBI BioProject 453 · NCBI GEO 114 📋 · `infectiousAgent.name` **60,107** vs `species.name`
**1** 📋 (`QUESTIONS.md` Q10, `PIPELINES.md` P1) · BV-BRC production 0 / staging 118,625 📋
(`QUESTIONS.md` Q11).

---

## R7. "Do the isolates carrying gyrA S83L show the expression changes you'd expect?"

Two sources, neither half optional, and a join that does not exist. A one-source answer here is
wrong in a way that looks complete.

**Could plausibly be chosen:** Pathogen Detection alone · GEO alone · both · PubMed.

**Correct, and why:** both, **and then a refusal of the join.** Pathogen Detection counts
isolates carrying the allele; GEO holds expression experiments. They index different objects.
No isolate-to-GEO-sample crosswalk exists anywhere on this board, and the 170,726 isolates have
no expression data at all.

**Wrong but plausible:** running `geo_search` for "gyrA", getting Series back, and presenting
them as the expression profile *of those isolates*. The studies are real, the count is real,
and the sentence joining them is fabricated.

**The answer must contain:** both numbers with their units, the explicit statement that no
identifier links a Pathogen Detection isolate to a GEO sample, and the nearest answerable
question — which expression studies exist for ciprofloxacin, 37 Series 📋.

**Ground truth:** 170,726 isolates 📋 · 37 Series 📋 · the crosswalk: **none**. `PIPELINES.md` P2
documents every accession bridge on this board (GEO Series → BioProject → SRA runs → ENA), and
not one of them reaches the Pathogen Detection isolate index.

---

## R8. "What does ciprofloxacin actually hit in E. coli?"

**Could plausibly be chosen:** UniProt · STRING · Pathogen Detection · MyGene · PubMed.

**Correct, and why:** UniProt. The question is about protein function —
`uniprot_search(query="gyrA", organism="Escherichia coli")` then `uniprot_get_entry`. The search
returns P0AES4 (gyrA) and P0AFI2 (parC), the two fluoroquinolone targets 📋.

**Wrong but plausible:** Pathogen Detection's mutation vocabulary. `ncbi_pathogen_amr_genes`
returns `gyrA_S83L`, `parC_S80I` and similar — real tokens that look like an answer to "what
does it hit", and are in fact resistance *alleles*, not targets. Also plausible and wrong:
taking `uniprot_search` hit 1 without checking the gene symbol, which on this query can be
**ccdB** 📋.

**The answer must contain:** DNA gyrase (GyrA, P0AES4) and topoisomerase IV (ParC, P0AFI2) as
the targets, and a gene-symbol check rather than rank-1 selection.

**Ground truth:** `uniprot_search("gyrA", organism="Escherichia coli", reviewed_only=True)`
returns P0AES4 gyrA 875 aa, P62554 ccdB 101 aa, P0AFI2 parC 752 aa 📋 (`QUESTIONS.md` Q1).

---

## R9. "How many E. coli genomes are there?"

**Could plausibly be chosen:** BRC Analytics · NCBI Assembly · Pathogen Detection · ENA through
BRC local.

**Correct, and why:** NCBI, and only after the router states or asks what "genome" means. Four
sources hold four different objects and every number is real: **2** curated assemblies in BRC 📋,
**452,563** assemblies in NCBI 📋, **581,464** isolates in Pathogen Detection 📋, **551,679** runs
in ENA 📋.

**Wrong but plausible:** BRC Analytics, returning **2**. The most damaging available mis-route,
because the answer is small, confident, and a five-order-of-magnitude under-statement of the
world. It happens because the question sounds like "genomes I could work with", and BRC is the
genome-shaped source on the board.

**The answer must contain:** the unit, named. A number without "assemblies" / "isolates" /
"runs" beside it fails this case regardless of which number was chosen.

**Ground truth:** BRC 2 📋 (Q4) · NCBI 452,563 assemblies 📋 (Q4) · Pathogen Detection 581,464
distinct isolates 📋 (Q2) · ENA 551,679 runs for taxid 562 📋 (`ADVERSARIAL.md` A8).

---

## R10. "What's new on carbapenem resistance this year?"

**Could plausibly be chosen:** PubMed · NDE · Pathogen Detection · BRC local (ENA keyword
search).

**Correct, and why:** PubMed — "what's new" is a literature question — through
`ncbi_pubmed_search` with `datetype=pdat` and `sort=pub_date`. Not our server; the ground truth
below is one teammate's observation on an analogue query, not a suite.

**Wrong but plausible, three ways.** Pathogen Detection returns carbapenemase carrier counts,
which is prevalence, not news. The federated `search_ena_keywords` is the tempting free-text
route and answers **HTTP 400 returned as tool text** 📋 — an error that reads as an absence. And
inside PubMed itself, `sort="Publication Date"` (PubMed's own web-UI label) is not rejected:
esearch returns HTTP 200 in **relevance** order, so "latest" silently becomes "best match" 📋.

**The answer must contain:** which date field it sorted on, and which "latest" it means — under
`sort=pub_date` the top hits are dated 2026 Dec while their e-publication dates are June,
because PubMed sorts on the citation's issue date 📋.

**Ground truth:** for the measured analogue ("Escherichia coli ciprofloxacin resistance"):
**283** with `datetype=pdat&sort=pub_date`, **274** with `datetype` omitted, **5,273** with no
window 📋 (`PIPELINES.md` P8 `board-latest`). The carbapenem query itself is ⚠️ **unverified** —
`ncbi_pubmed_search(term="carbapenem resistance", datetype="pdat", mindate="2026/01/01", maxdate="2026/12/31", sort="pub_date")`, queued.

---

## R11. "Is there any flu data in here?"

A legitimate question with a substring trap under it. `ADVERSARIAL.md` A1 asks a nonsense
question about influenza; this one is reasonable, and the mis-route is identical.

**Could plausibly be chosen:** PDN / LAPIS · NCBI Pathogen Detection · NDE · NCBI Virus
(off-board).

**Correct, and why:** PDN. It wraps GenSpectrum (`influenza-a`), Pathoplexus and CoV-Spectrum,
and organism keys there are **slugs**, not species names 📋.

**Wrong but plausible:** Pathogen Detection. Of its **106** organism groups, exactly one
contains the string "influenza" — ***Haemophilus influenzae***, a bacterium, 16,981 index rows 📋.
A router that greps the group list for "influenza" finds it, gets HTTP 200, and answers a
virology question with a bacterial count.

**The answer must contain:** that PDN is the viral source and Pathogen Detection is entirely
bacterial (106 groups, none viral 📋) — and if *H. influenzae* surfaces, that it is a bacterium
matched on a species name, not influenza.

**Ground truth:** 106 groups, no viral group, sole "influenza" match *Haemophilus influenzae* at
16,981 rows 📋 (`ADVERSARIAL.md` A1) · LAPIS rejects a species name with an error naming
`lapis_list_organisms` 📋 (A3).

---

## R12. "Which other bacteria have the same resistance problem as E. coli?"

**Could plausibly be chosen:** Pathogen Detection · BV-BRC · NDE · PubMed.

**Correct, and why:** Pathogen Detection. It is the one source holding the same measurement
across **106** organism groups 📋, so a cross-organism comparison is one vocabulary and 106
possible filters.

**Wrong but plausible:** comparing or summing `index_rows` across groups. The index doubles rows
for *E. coli* (1,162,675 rows / 581,464 distinct) and does **not** double for *S. aureus*
(171,412 / 171,412) 📋, so cross-organism arithmetic on rows is wrong by up to 2×, in one
direction only. The comparison has to be built from `ncbi_pathogen_isolate_count` per group.
This is the same non-uniform doubling that makes `ncbi_pathogen_organisms`'s derived
`approx_isolates = count // 2` right for E. coli and wrong by half for *S. aureus* 🗂 — a defect
for Jonathan, reported not patched.

**The answer must contain:** a per-group distinct-isolate count rather than a row count, the
group names as the service spells them, and — if the whole board is swept — what that sweep
costs in calls (see `STRESS.md` S12).

**Ground truth:** 106 groups 📋 · E. coli 581,464 distinct / 1,162,675 rows; *S. aureus* 171,412 /
171,412 📋 (`QUESTIONS.md` Q2) · largest groups *Salmonella enterica* 1,766,824 rows,
*E.coli and Shigella* 1,162,675 📋 (`ADVERSARIAL.md` A1) · *S. aureus* `mecA` 93,260, `mecA|mecC`
94,336 of 171,412 📋 (Q13).

---

## R13. "Can you predict the structure of GyrA for me?"

The routing set needs one question with **no** destination on the board, or refusal quality goes
untested here.

**Could plausibly be chosen:** UniProt · BRC Analytics (`PROTEIN_FOLDING`) · ExPASy · STRING ·
nothing.

**Correct, and why:** nothing on this board. The right routing decision is to leave.

**Wrong but plausible, two ways.** BRC lists a `PROTEIN_FOLDING` category with **0** workflows,
marked coming soon 📋 — a category name that reads as a capability. And `uniprot_get_entry`
returns function and GO annotation, which can be narrated as though it addressed structure.

**The answer must contain:** the four-part refusal `PIPELINES.md` P8 specifies — the number that
proves it (0 folding workflows 📋; 20 PDB cross-references exist upstream and `ENTRY_FIELDS`
returns 0 of them 📋), why the premise is off (prediction is not retrieval, and nothing here
computes structure), the nearest answerable question (function and GO annotation for P0AES4, or
the PDB identifiers once the UniProt field list is fixed), and who could answer it (RCSB PDB,
AlphaFold DB).

**Ground truth:** `PROTEIN_FOLDING` 0 workflows, coming soon 📋 (`PIPELINES.md` P7) · P0AES4
carries 20 PDB cross-references by default and 0 through `ENTRY_FIELDS` 📋 (`ADVERSARIAL.md` A7,
`uniprot.py:55`).

---

## R14. "I'm setting up a lab evolution experiment on antibiotic resistance. What existing data should I start from?"

Open-ended, no source words, and the two candidate sources differ by study design rather than
by topic.

**Could plausibly be chosen:** GEO · Pathogen Detection · NDE · PubMed.

**Correct, and why:** GEO. A lab-evolution experiment needs prior *designed, time-course*
data, and GEO is the only source on the board holding designed experiments — GSE309890 is itself
an adaptive-evolution series 📋. Pathogen Detection holds field isolates with no time axis and no
experimental design.

**Wrong but plausible:** Pathogen Detection, because it has by far the biggest numbers
(581,464 📋) and the question says "resistance". Half a million field isolates is not a starting
dataset for an evolution experiment; it is a snapshot of the world outside the lab.

**The answer must contain:** the distinction between designed experiments and field isolates, at
least one concrete accession with its design, and where the values live — the supplementary
file, not the API 📋.

**Ground truth:** 37 ciprofloxacin Series, GSE309890 at rank 1, an adaptive-evolution RNA-seq
study of sertraline-driven resistance in K-12 MG1655, 6 samples 📋 (`QUESTIONS.md` Q3, Q8).

---

## R15. "Which genes switch on when E. coli is stressed with an antibiotic?"

The cleanest GEO-owned routing case: the question never says expression, transcriptome, GEO or
Series.

**Could plausibly be chosen:** GEO · UniProt · Pathogen Detection · BRC Analytics · STRING.

**Correct, and why:** GEO, and it is the only possibility. "Which genes switch on" is
differential expression, and **GEO is the only source on this board with processed expression
data** 📋 — `geo_search(organism=..., term=..., entry_type="gse")` → `geo_series`.

**Wrong but plausible:** UniProt, which returns gene function and GO terms for named genes —
real annotation, and not a measurement of anything switching on. Or BRC, which offers an RNA-seq
*workflow*, answering "how could I measure this" instead of "what is already known".

**The answer must contain:** that the count is over Series and is a text match on study
metadata — `entry_type="gse"` gives **37** where the unfiltered `gds` count gives **513** across
four record types 📋 — the `query_translation` string, because "antimicrobial resistance" expands
through MeSH to `"drug resistance, microbial"[MeSH Terms]` and returns 234 📋 which are not 234
AMR experiments — and that **no per-gene numbers come back from the API at all**: they live in
the submitter's supplementary file 📋.

**Ground truth:** 37 filtered vs 513 unfiltered 📋 · MeSH expansion returning 234 📋
(`QUESTIONS.md` Q3) · `suppfile` returns `'CSV'`, a format string, never a filename or URL 📋
(`evals/README.md`).

---

## Scoring these

`judge.py` scores `EXPECTED[n]` by question number with a `primary` tool set, a `kind`
(`answer` or `gap`) and a `source` 🗂. This set needs one thing the demo set does not: **a
wrong-route flag.** Routing is currently scored as "did it call at least one tool from the
source that can answer" — presence, not exclusivity — so R2, R9 and R11 all pass that check
while getting the answer wrong, because a shotgun calling GEO *and* Pathogen Detection satisfies
`primary` either way.

What I am asking Judge for, as a separate map so the demo numbers are not disturbed:

```python
EXPECTED_ROUTING = {
    "R2": {"primary": {"ncbi_pathogen_amr_genes", "ncbi_pathogen_isolate_count"},
           "misroute": {"geo_search", "geo_series"},
           "kind": "answer", "source": "NCBI Pathogen Detection"},
    ...
}
```

with three checks the demo set does not have:

1. **`misroute`** — a tool from this set called *and its result used in the answer*. Calling it
   and discarding it is not a mis-route; calling it and quoting its number is. This is decidable
   from the transcript, because the judge already matches answer numbers against tool results.
2. **`declared`** — for the multi-source cases (R1, R5, R7, R9), does the answer name the unit
   or the source it chose? A regex over the unit words beside the number decides it. R9 fails on
   a bare number by construction.
3. **`breadth`** — tool calls per question. A router that calls eleven tools has not chosen.
   This is a count, not a judgement, and it is the one number connecting routing quality to the
   3 requests/second ceiling.

The `misroute` set for each case is the "wrong but plausible" paragraph above. I will hand Judge
the tool sets directly rather than make `judge.py` read prose.

## Running these

**`run_questions.py` cannot read this file.** `QUESTIONS_MD` is hard-coded to
`evals/QUESTIONS.md` and the loader regex is `^## Q(\d+)\.\s+"(.+?)"` 🗂
(`evals/run_questions.py:64,70,100`). Three changes are needed, and they belong to the hub, not
to me:

1. `--questions PATH`, defaulting to `evals/QUESTIONS.md`.
2. The regex generalised to allow a letter prefix, e.g. `^## ([A-Za-z]*)(\d+)\.\s+"(.+?)"`, so
   `R1` and `S1` parse alongside `Q1`.
3. **An output namespace, or the first run of this file destroys the base matrix.** Transcripts
   are written to `RUNS / _safe(model) / f"q{number:02d}.jsonl"` 🗂 (`run_questions.py:112,238,240`),
   and `_safe()` appends only `RUN_TAG`, which is derived from `--prompt` and `--repeat` and
   nothing else. Running this file against a model already in the matrix overwrites
   `q01.jsonl`–`q15.jsonl` in place, with no warning. Deriving `RUN_TAG` from the questions-file
   stem (`-routing`, `-stress`) fixes it.

Queued in `_reports/run-queue.md`. Until point 3 exists, **do not run this file against a model
whose base matrix has already landed.**
