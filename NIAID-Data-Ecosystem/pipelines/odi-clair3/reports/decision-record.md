# Reproducing a clinical metagenomics paper: a decision record

How a federated-data assistant went from a paper title to a validated analysis
pipeline, and every judgement call made along the way — including the ones that
were wrong.

Assembled 2026-09-16 from live queries against Europe PMC, the ENA portal API,
the NIAID Data Ecosystem (`mcp__nde__*`), and BV-BRC (`mcp__bvbrc-mcp__*`).
Counts are as of that date.

**The target**

> Street TL, Sanderson ND, Kolenda C, *et al.* "Clinical Metagenomic Sequencing
> for Species Identification and Antimicrobial Resistance Prediction in
> Orthopedic Device Infection." *J Clin Microbiol* 2022;60(4):e0215621.
> PMID [35354286](https://pubmed.ncbi.nlm.nih.gov/35354286/) ·
> DOI [10.1128/jcm.02156-21](https://doi.org/10.1128/jcm.02156-21)

**What this document is for.** The pipeline itself is described in
[README.md](../README.md); its current status is in [STATE.md](../STATE.md).
This is the reasoning: why each tool was chosen, what the alternatives were,
where the evidence overturned an assumption, and which conclusions are
provisional. A reproduction whose decisions are invisible cannot be audited,
and the decisions turned out to matter more than the code.

---

## 1. Finding the paper, and the first dead end

The request named a paper by title only. Two obvious routes:

| Route | Outcome |
|---|---|
| `WebSearch` | **Blocked** — org policy forbids `web_search` for this model on Vertex |
| `pubmed.ncbi.nlm.nih.gov` via `WebFetch` | **Failed** — the search page requires cookies and returned none |
| `ebi.ac.uk/europepmc/.../search?query=TITLE:"..."` | **Worked** — full record, structured JSON |

**Decision: use Europe PMC's REST API as the default bibliographic resolver.**
It needs no key, returns structured JSON including PMCID and a
`supplementaryFiles` endpoint, and is not cookie-gated. The PubMed HTML
interface is not machine-readable; its REST sibling would have worked but
Europe PMC also carries full text and supplements, which mattered later.

In parallel — not sequentially, since neither depended on the other — a
semantic NDE search was issued for the study's *data*:

```
nde_semantic_search("clinical metagenomic nanopore sequencing of sonication
                     fluid from orthopedic device infection for species
                     identification and antimicrobial resistance prediction")
→ 1000 matches, top hit prjeb23460 @ 0.9673
```

Semantic rather than keyword search, because the query is a description of a
study design rather than a set of index terms. A keyword search for the same
concepts returned **zero**:

```
nde_search_datasets(query="metagenomic sequencing orthopedic device infection
                           prosthetic joint infection antimicrobial resistance")
→ 0 matches
```

That contrast is itself a finding: stacking specific terms in a keyword query
narrows to nothing, while the semantic index handles the same intent. The
top hits were the authors' *earlier* papers (PRJEB23460, PRJEB24383), not the
target — useful context, wrong study.

---

## 2. The deposit, and a claim that had to be checked

The paper's data availability statement says non-human reads are in **ENA
PRJEB42910**. Resolving that in NDE produced an immediate discrepancy:

| | |
|---|---|
| Paper says | PRJEB42910 |
| NDE record title | "Optimization of prosthetic joint infection sample DNA extraction for Nanopore sequencing" |
| Expected | something about the clinical evaluation |

The NDE record is titled for a *methods-optimization* study, not the 115-sample
clinical cohort. Two possibilities: the accession is wrong in the paper, or the
submission bundles both arms under one title.

**Decision: check ENA directly rather than trust either the paper or the
index.**

```bash
curl "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=PRJEB42910\
&result=read_run&fields=run_accession,sample_title,instrument_model,\
library_strategy,read_count,base_count&format=tsv&limit=0"
→ 288 runs: 258 OXFORD_NANOPORE (GridION), 30 ILLUMINA (MiSeq), all WGS
```

I initially reported that the metadata "doesn't partition them" and that
mapping runs to the clinical cohort would require the supplementary sample
table. **That was wrong, and inspecting the `sample_title` field disproved it
within one call:**

```
patient_119_sonication fluid_sap
patient_92_sonication fluid_fil
```

Every run is labelled with patient number, specimen type, and preparation —
`sap` for saponin lysis, `fil` for 5-µm filtration. The two arms are separable
from metadata alone. The correction mattered: it turned a "you'll have to do
this by hand" into a one-line `awk`.

**Lesson recorded:** an unexpected title is a reason to read the per-record
metadata, not to conclude the records are unusable.

---

## 3. Choosing services: what BV-BRC can and cannot do

With 30 workflows available, the question is which map onto the paper's five
steps.

| Paper step | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Saponin host depletion | *(none)* | `Fastq Utils` `scrub_human` | Wet-lab step; deposited reads are already non-human |
| Species ID | **Taxonomic Classification** (Kraken2) | Metagenomic Binning | Substitute for their Youden-optimized filter; binning is heavier and answers a different question |
| Alignment | **minimap2** | BWA-mem, Bowtie2, LAST, Snippy | The paper used minimap2 v2.17; `map-ont` is the only correct preset for R9 |
| Variant calling | **Clair3, locally** | FreeBayes, BCFtools, Snippy (in `Variation`) | ⚠️ see below |
| Mobile-element AMR | **Metagenomic Read Mapping** vs CARD | — | Closest analogue to their Table S2 |

### The one that forced a decision

BV-BRC's `Variation` service offers exactly three callers — FreeBayes,
BCFtools, Snippy — and **all three are short-read variant callers**. The paper
used Clair specifically *because* R9 nanopore error rates make ordinary callers
unusable; running FreeBayes on these reads reproduces the problem the paper was
written to solve.

This was surfaced as an explicit choice rather than silently resolved:

- **Use Snippy, document the divergence** — stays inside BV-BRC, least-bad of
  the three, but still short-read-tuned.
- **Run Clair3 locally** — Clair3 is Clair's maintained successor with R9
  models; faithful, more setup.
- **Skip variant calling** — mobile elements only.

**Decision: Clair3, locally** (user's call). Consequence: the project splits
into a BV-BRC arm and a local arm, and the local arm needs a scaffold that runs
anywhere — hence `ODI_WORK` / `ODI_RUNTIME` / `ODI_THREADS` rather than
hard-coded paths.

**Model choice is not a detail.** These are R9.4.1 reads from 2018–2020;
`CLAIR3_MODEL` defaults to `r941_prom_sup_g5014`. An r10 model on r9 data
miscalibrates quality throughout, silently.

---

## 4. The reference trap

Both of these are called "MRSA252". They are not the same sequence.

| | Length | BV-BRC id | GenBank | Status |
|---|---|---|---|---|
| **BX571856.1** | 2,902,619 bp | `282458.100` | — | WGS |
| **CP194230** | 2,902,592 bp | `1280.63071` | CP194230 | **Complete** |

Searching BV-BRC for MRSA252 returns 23 genomes. The one that *looks*
authoritative — `1280.63071`, status Complete, single contig, 30 CARD and 20
NDARO annotations attached — is a **later resequencing**, 27 bp shorter with 2
CDS different. Gordon *et al.* 2014, the source of the resistance-SNP
catalogue, numbered against BX571856.1.

A resistance SNP looked up at CP194230 coordinates against a
BX571856.1-indexed catalogue is wrong by an unknown offset past the first
indel, and **nothing raises an error**.

**Decision: carry both, measure the drift.** `00_fetch_refs.sh` hard-fails on a
length mismatch; `04_compare_refs.py` reports whether disagreements are a
constant liftable offset or scattered. BX571856.1 is authoritative for
resistance calls; CP194230 is a control.

This is the clearest case in the project of a convenient default being the
wrong one. Annotation richness attracted the eye; coordinate provenance was
what mattered.

---

## 5. The cohort was selected twice

### First attempt: by yield

Six highest-yield saponin runs, 3.8–7.3 Gb. Reasoning: deeper data gives the
best chance of reaching the ≥20× needed for AMR calling.

### Then the ground truth appeared

Street's supplementary workbook turned out to contain **Table S3** — culture
result, histology, and the authors' own sequencing call for all 115 specimens.
Extracting it (`07_extract_truth.py`) gave the check that the parse was right:

```
culture-negative on sonication fluid : 48      ← paper states 48
S. aureus by culture                 : 19
S. aureus by their sequencing        : 20
agreeing on S. aureus                : 17
```

But using it required establishing that Table S3 sample numbers correspond to
ENA `patient_N` — plausible, unproven, and load-bearing. **Verified by matching
non-human base counts:**

| Sample | Table S3 bases | ENA bases | Δ |
|---|---|---|---|
| 119 | 7,350,065,534 | 7,349,284,900 | 0.011% |
| 25 | 5,570,859,830 | 5,564,146,077 | 0.121% |
| 16 | 5,533,753,660 | 5,532,460,287 | 0.023% |
| 72 | 5,366,369,114 | 5,365,957,886 | 0.008% |
| 103 | 3,822,239,040 | 3,821,628,038 | 0.016% |

Five of six agree to within 0.12%; the gap is reads dropped at deposit. Sample
22 differs because ENA splits it across two saponin runs that sum past the S3
figure — an explicable exception, not a counterexample.

### The first cohort was badly chosen

With the join established, the yield-selected cohort could be checked:

| Sample | Culture | *S. aureus*? |
|---|---|---|
| 119 | *S. aureus* | ✅ |
| 103 | *S. aureus* | ✅ |
| 25 | *Enterobacter cloacae* | ❌ |
| 16 | *S. lugdunensis* | ❌ |
| 72 | *S. dysgalactiae* | ❌ |
| 22 | *Enterococcus faecium* | ❌ |

**Only 2 of 6.** Yield measures how much DNA was in the tube, not which
organism. The AMR cohort was rebuilt on the paper's own criteria —
`sequencing_species == S. aureus` **and** their mean depth ≥ 20× — giving 14
specimens across 20 runs.

**Decision: keep both cohorts.** `samples.tsv` (yield-selected, with sap/fil
pairs) answers the depletion question; `amr_cohort.tsv` answers the resistance
question. The failed selection is kept and documented rather than deleted,
because "don't pick cohorts by file size" is a reusable lesson.

### The regression test chose itself

Sample 41 is *S. aureus* at 289× depth, and the paper reports missing a
resistant *rpoB* A477V present in ~20% of reads there — their pipeline took
consensus calls and did not model mixed populations. A477V is in the extracted
catalogue (rifampicin, MIC 1). Clair3 reports allele fractions.

So the reproduction has a built-in test of whether it merely re-runs the paper
or actually improves on it. If this arm also misses A477V, it has inherited the
flaw.

---

## 6. What could not be reproduced, and saying so

The paper's headline depletion result — human bases falling from a median
**98.1%** to **11.9%** — is **not recoverable from PRJEB42910.** Street et al.
deposited only reads *"classified as nonhuman"*. The denominator is gone.

The temptation is to compute *something* host-related and call it a
reproduction. Instead the measurable consequence is reported — microbial yield
and pathogen breadth per preparation, which is what determines whether ≥20× is
reachable:

| Patient | Saponin | Filtration | Ratio |
|---|---|---|---|
| 103 | 3.82 Gb | 0.016 Gb | **240×** |
| 22 | 4.16 Gb | 0.034 Gb | **121×** |
| 72 | 5.37 Gb | 0.144 Gb | **37×** |

With the caveat stated in `samples.tsv` and in the tool docstring: raw yield is
not the same claim as human fraction, since two aliquots can differ in yield
for ordinary library reasons. Corroborating, not confirming.

**Decision: encode the limitation where it will be read.** Not only in prose —
`05_depletion_summary.py`'s docstring has a CANNOT/CAN section, so anyone
running it sees why no "% human" column exists.

---

## 7. Extracting the catalogue: two silent corruptions

Gordon 2014's resistance catalogue exists only as a supplementary PDF. No
`pdftotext` on this machine, so extraction is `zlib.decompress` over the
content streams plus a regex for text-showing operators.

### Spacing is semantic

The typesetting emits each glyph separately:

```
'fu sA  v ari an ts  asso c i at ed  wi t h  fu si d i c  ac i d'
```

The obvious fix — collapse all whitespace, glue up the letters — produces
readable gene names and **corrupts every MIC in the table**. A *single* space
separates characters within a word; *two or more* separate table columns.
Collapsing both welds each MIC onto its reference marker:

```
B434N  >128  1     →  parsed as MIC ">1281"
```

Caught because `>1281 mg/L` is not a plausible fusidic-acid MIC. The fix
protects multi-space runs as sentinels before gluing, then splits on them,
recovering true columns:

```
'B434N' | '>128' | '1'
```

### `[A-Z]` is not the amino-acid alphabet

The same first row exposed a second bug: `B434N` parsed as a substitution. B is
not an amino acid — nor are J, O, U, X, Z. The regex now requires membership in
the 20-letter set.

Result: **122 unique substitutions** — *fusA* 60, *rpoB* 25, *grlA/gyrA/grlB*
29, *dfrB* 8.

**Decision: hand-rolled stdlib parsers, no dependencies.** Both the PDF and the
xlsx (also parsed by hand — an xlsx is a zip of XML) are one-off extractions of
static published tables. Adding `openpyxl` and `pdfminer` to buy two
conversions was not worth the install surface, and the cached source archives
are committed so the extractors rebuild offline.

---

## 8. The reference-bias trap

This is the most consequential finding, and it emerged from validation rather
than from reading.

Before writing the caller, the six CDS were translated from BX571856.1 and
spot-checked against the catalogue's stated wild-type residues:

```
fusA  H457  expect H  got H   OK
rpoB  H481  expect H  got H   OK
dfrB  F99   expect F  got F   OK
gyrA  S84   expect S  got L   MISMATCH
grlA  S80   expect S  got F   MISMATCH
grlB  P451  expect P  got I   MISMATCH
```

The three mismatches are not a coordinate bug. **MRSA252 is itself a
quinolone-resistant strain** — it carries gyrA S84L and grlA S80F natively.
The reference *is* the resistant allele at those codons.

Consequence for any "variant differs from reference" caller — it fails in both
directions **simultaneously**:

- a **susceptible** sample differs from the reference there → called resistant
- a **resistant** sample matches the reference, emits no VCF record at all →
  called nothing

Both errors point the wrong way, and neither produces an error message.

**Decision: the caller never asks whether a variant exists.** It asks *what
amino acid this sample has at this codon* — reading the reference base wherever
the VCF is silent, translating, comparing to the catalogue. Absence of a VCF
record is *evidence of the reference allele*, which at these codons means
resistant.

### The same trap, one level up

The first implementation of the Table S4 gene-assignment resolver matched
candidate genes on the **wild-type** amino acid only. It silently discarded
S84L, S80F and P451S — exactly the entries that matter most. Fixed to match
either allele, preferring wild-type when both are available:

```
before:  8 entries dropped (incl. S84L, S80F, P451S)
after:   6 entries dropped (S80Y, S84A, P451S, P585S, E422D, D443E)
```

The remaining six match no candidate gene at either allele. Before dropping
them, the near-misses were checked for a systematic frameshift — they are
scattered, not a constant offset — so they are genuinely unresolvable from the
flattened PDF and are dropped **with a warning rather than guessed**. A
mis-assigned gene is worse than one marked unresolved.

**116 of 122 entries usable**: *fusA* 60, *rpoB* 25, *grlA* 15, *dfrB* 8,
*gyrA* 7, *grlB* 1.

### Validation that does not need data

`08_call_resistance.py --self-test` runs against the reference genome alone and
**independently rediscovers** the three reference-resistant alleles:

```
CDS integrity:
  dfrB   160 aa  start=M stop=* internal_stops=0  OK
  fusA   694 aa  start=M stop=* internal_stops=0  OK
  grlA   801 aa  start=V stop=* internal_stops=0  OK
  grlB   666 aa  start=L stop=* internal_stops=0  OK
  gyrA   887 aa  start=M stop=* internal_stops=0  OK
  rpoB  1184 aa  start=L stop=* internal_stops=0  OK

Catalogue reference-AA agreement:
  reference matches catalogue wild-type : 107
  reference IS the resistant allele     : 3      ← gyrA S84L, grlA S80F, fusA H557Y
  neither (investigate)                 : 6
```

(*grlA* and *rpoB* beginning V and L are GTG/TTG starts, normal in
*S. aureus* — checked, not assumed.)

**Decision: make the self-test a first-class entry point.** The whole
coordinate mapping is verifiable with no reads downloaded, so a broken mapping
is caught in one second rather than after 29 GB of transfer and hours of
compute.

### Where the coordinates came from

Not from BV-BRC. From the EMBL feature table of BX571856.1 itself. Five of six
loci agree with BV-BRC `282458.100` exactly; ***grlB* does not** — ENA 1417763
vs BV-BRC 1417769, a 6 bp start-codon difference. ENA wins, because Gordon
numbered against that record. Also: *fusA* carries no `/gene` qualifier there —
it is `fus`, SAR0552, findable only by product string.

---

## 9. The BV-BRC failure, and a wrong hypothesis

Six Kraken2 jobs were submitted and all six **failed**: `success: 0`, no output
files, no error message, dying 2–4 minutes in.

**First hypothesis: ERR vs SRR.** The app documents `srr_accession` as "SRA
Sample accession with **SRR** prefix", and these are ENA **ERR** accessions.
Plausible, and wrong.

Two observations killed it. `agent_get_sra_metadata` resolves ERR accessions
fine. And diffing the failed submissions against the account's historical
*successful* ones showed the real difference: every success included
`sample_id` inside its `srr_libs` entry; every failure omitted it.

**Decision: test with a 16 MB probe before resubmitting six multi-GB jobs.**
`ERR5260976` (patient_103 filtration, the smallest run in the deposit) with
`sample_id` added:

```
completed in 88 seconds — Krona, Sankey, MultiQC, alpha diversity,
and an output folder named "p103_fil" from the sample_id
```

Root cause: `sample_id` is required inside each `srr_libs` entry. Omitting it
passes the submission layer *and* preflight, then the app dies downstream with
no filename to write under — and reports nothing. All six resubmitted
correctly (jobs 23586156–23586161).

**Lesson recorded:** when a batch fails identically, diff against a known-good
invocation before theorizing. And an ERR accession is not the suspicious thing
just because the docs say SRR.

---

## 10. Bracken, not Kraken2 — a ratio that lies

The six jobs completed (21–42 min each) and the first report read cleanly.
Sample 119, culture-confirmed *S. aureus*:

```
92.12  2520086  D  2      Bacteria
91.32  2498346  G  1279     Staphylococcus        ← 1,729,095 reads stop HERE
28.11   769156  S  1280       Staphylococcus aureus
```

Taking the obvious ratio — species reads over bacterial reads — gives
**28%** for an organism that is essentially the entire sample. 69% of the
*Staphylococcus* reads never reach a species node at all.

This is characteristic of long reads against Kraken2's LCA assignment: a 3 kb
read spanning conserved and variable regions often has its lowest common
ancestor at genus or family. The reads are not unclassified, they are
classified *shallowly*.

A pipeline that scored on the raw report would have reported sample 119 as a
28% *S. aureus* sample — below the paper's 60%-of-bacterial-bases threshold,
and therefore **no call at all** for a specimen that is unambiguously
*S. aureus*. The number is plausible enough to survive review.

**Decision: score from Bracken's `_bracken_output.txt`, not Kraken2's
`_k2_report.txt`.** Bracken redistributes internal-node reads down to species
using the database's k-mer distribution. Same sample:

```
Staphylococcus aureus   kraken 769,156  + added 1,831,004  = 2,600,160  (99.988%)
```

28% → 99.99%. The docstring of `09_compare_species.py` records why, with the
sample-119 numbers, so nobody re-points it at the Kraken report.

**Lesson recorded:** when a service emits several outputs, the largest or
most obvious file is not necessarily the right input. The 1.99 GB
`k2_output.txt` is per-read assignments; the 6.5 KB `k2_report.txt` is the
tempting one; the 290-byte `bracken_output.txt` is the correct one.

### The result — 20 runs, 18 specimens

**16 species matches, 1 genus match, 1 culture-negative detection.** All 18
specimens agree with the authors' own sequencing calls, including both where
we differ from culture.

Three specimens are worth more than a row in a table, and each drove a design
decision.

**Sample 46 — culture-negative, 100% *S. aureus* over 466,712 reads.** Street
et al. called *S. aureus* here too. This is the clinical case the whole method
exists for: an infection that culture missed. Scoring it as a false positive
against culture would be exactly backwards.

**Decision: `culture_negative` is its own verdict**, not a mismatch. The
reference standard is imperfect — the paper says so itself, which is why it
reports positive/negative percent *agreement* rather than sensitivity and
specificity.

**Sample 20 — genuine polymicrobial infection.** Five organisms above
threshold, and the cultured one (*S. aureus*) ranks **fourth at 6.8%**:

```
Fusobacterium vincentii   185,385  60.1%
Fusobacterium animalis     45,799  14.9%
Fusobacterium nucleatum    25,422   8.2%
Staphylococcus aureus      20,978   6.8%   ← the cultured organism
Staphylococcus schleiferi  20,144   6.5%
```

We recover all three organisms the authors reported (*S. aureus*,
*S. schleiferi*, *F. nucleatum*); the two extra *Fusobacterium* species are
almost certainly the same organism split across closely-related database
entries.

**Decision: score across every passing hit, not the top one.** Had the scorer
compared only the most abundant species, this specimen — where the pipeline
arguably outperformed culture — would have been recorded as a mismatch.

**Sample 25 — probably culture's limitation, not ours.** Culture said
*E. cloacae*; we called *E. hormaechei*. **Street et al. reported both.**
*E. hormaechei* was split out of the *E. cloacae* complex and MALDI-TOF cannot
separate them. Two independent sequencing pipelines disagreeing with culture
*in the same direction* is evidence about the reference standard.

### The depletion comparison, at last measurable

Two specimens contributed both aliquots:

| Specimen | Prep | Microbial reads | Human | Top call |
|---|---|---|---|---|
| 22 | sap | 1,145,565 | 0.001% | *E. faecium* 98.0% |
| 22 | fil | 7,702 | 0.324% | *E. coli* 62.8%, *E. faecium* 33.2% |
| 72 | sap | 1,239,452 | 0.019% | *S. dysgalactiae* 98.8% |
| 72 | fil | 32,518 | 1.496% | *S. dysgalactiae* 100.0% |

Saponin yields **149×** and **38×** more microbial reads. Specimen 22 shows
the clinical consequence rather than just the arithmetic: in the filtered
aliquot the true pathogen is demoted to a **33% minority behind *E. coli*
contamination**. At that depth the call is wrong unless you already know the
answer.

This is the §6 claim in its measurable form. Not the human fraction — that
denominator is gone — but the downstream consequence, which is what actually
determines whether the method works.

**One scoring bug found here.** The first run printed the two filtration
aliquots as duplicate "sample 22" / "sample 72" rows with no prep label, and
counted them separately, inflating agreement from 18 specimens to 20. Fixed:
the prep is parsed out of the filename, shown in the heading, and filtration
aliquots are excluded from the tally since they are the same specimens
re-prepared.

Residual human across all 20 runs: **0.000–1.496%**. Independent confirmation
that the deposit is host-depleted, and therefore that §6's conclusion holds.

---

## 11. Interruption: a shared working tree

Mid-session, most of the pipeline directory was deleted by another process, and
a git stash appeared:

```
stash@{0}: On add-nde-mcp-server: not-mine: odi-clair3 pipeline + gitignore
```

The branch had also moved to new commits. Inspecting the stash showed it
contained **only** the `.gitignore` change — plain `git stash` skips untracked
files, so the scripts were deleted, not saved.

**Decision: rebuild from cached sources; do not touch the other person's
stash.** Both supplement archives were still in the tool-results cache, so
every artifact regenerated deterministically. Restoring a stash created by
someone else is their call.

**Lesson recorded** in STATE.md as a standing hazard: commit early in this
tree.

---

## 12. Known gaps, stated as gaps

The most important one is clinical:

> **Methicillin cannot currently be called at all.**

Gordon's catalogue covers six chromosomal loci. *mecA* — the determinant that
defines MRSA and the central drug question in orthopedic device infection —
is a mobile element. Street's Table S2 covers those genes and **was not
deposited** with the paper. BV-BRC's Metagenomic Read Mapping against CARD is
the intended substitute, not yet wired in.

Others, in `08`'s docstring and STATE.md:

- **No random-forest variant filter.** Street's model was trained on their
  earlier *N. gonorrhoeae* work and never published as an artifact. Clair3's
  own quality model plus the ≥20× threshold stands in. Expect more false
  positives than the paper reports.
- **Indels skipped.** Gordon's `ins 475H` / `ins 475G` *rpoB* entries are not
  actionable by a substitution-only caller.
- **Six Table S4 entries unassigned.**
- **Kraken2 is a substitute**, not a reimplementation, of their
  Youden-optimized filter. Confidence 0.1 is BV-BRC's short-read default and is
  permissive on 2.7–3.3 kb reads; post-hoc filtering comparable to the paper's
  thresholds is needed before comparing to Table S3.

### The category that must not collapse

60 of the paper's 152 drug–organism combinations had **insufficient coverage**
to call. The caller keeps `insufficient_coverage` and `no_data` as distinct
verdicts, never merged into `susceptible`.

A codon with no reads is not a susceptible codon. Merging the two is precisely
how a perfect-looking result gets manufactured, and it would have made this
reproduction *look better* than the paper while being worse. The per-drug
rollup is deliberately asymmetric for the same reason: any resistant codon
makes the drug resistant, but a drug is only called susceptible when every one
of its codons was callable.

---

## 13. What the federated tooling was actually good for

An honest accounting, since that is the point of the codeathon project.

**Worked well**

- `nde_semantic_search` found the study family from a prose description where
  keyword search returned zero.
- `mcp__bvbrc-mcp__agent_search_data` over `genome_feature` gave CDS
  coordinates in one call, and its error responses list valid fields on
  failure — `public` is not a field on `genome`, and the tool said so and
  suggested alternatives.
- `agent_get_sra_metadata` confirmed platform, layout and library selection
  per run without leaving the session.
- `agent_list_jobs` / `agent_workspace_browse` made the silent job failures
  diagnosable at all.

**Did not**

- `WebSearch` blocked by org policy; PubMed's HTML interface cookie-gated.
  Europe PMC's REST API carried the whole bibliographic layer.
- No NDE record for PRJEB42910 annotates a health condition or pathogen list,
  so this study **will not surface under a `healthCondition` filter** — found
  only via free text or accession.
- The NDE title for PRJEB42910 describes the methods-optimization arm, not the
  clinical cohort. Index metadata inherits whatever the submitter wrote.
- Of the paper's pipeline tools, only minimap2 is in bio.tools. Clair and the
  Nextflow workflow are not indexed; the GitLab URL in the paper is the only
  handle.

**The general shape.** The federated indexes were excellent at *discovery* —
what exists, where it lives, what it contains — and consistently insufficient
for *verification*. Every load-bearing fact in this project had to be confirmed
against a primary source: assembly lengths from ENA rather than BV-BRC, CDS
coordinates from the EMBL record rather than the annotation, the sample-number
join from base counts rather than assumption, and the job failure from a diff
against known-good rather than from the documented field description.

---

## Decisions at a glance

| # | Decision | Alternative rejected | Basis |
|---|---|---|---|
| 1 | Europe PMC REST for bibliography | PubMed HTML, WebSearch | Cookie-gated / policy-blocked |
| 2 | Semantic over keyword NDE search | `nde_search_datasets` | Keyword returned 0 |
| 3 | Verify accession at ENA | Trust paper or index | Title mismatch |
| 4 | Clair3 locally | Snippy/FreeBayes in BV-BRC | All three are short-read callers |
| 5 | `r941` Clair3 model | r10 default | R9.4.1 chemistry, 2018–2020 |
| 6 | Both MRSA252 assemblies | `1280.63071` alone | 27 bp apart; catalogue indexed to the other |
| 7 | EMBL feature table for CDS | BV-BRC annotation | *grlB* differs by 6 bp |
| 8 | Cohort from ground truth | Yield-ranked | Only 2 of 6 were *S. aureus* |
| 9 | Keep both cohorts | Replace the failed one | They answer different questions |
| 10 | Report yield, not "% human" | Compute a host fraction | Denominator absent from deposit |
| 11 | Stdlib-only parsers | openpyxl + pdfminer | Two one-off static extractions |
| 12 | Column-preserving PDF parse | Collapse whitespace | Corrupts every MIC |
| 13 | Amino-acid alphabet check | `[A-Z]` | Accepts `B434N` |
| 14 | Read sample AA, not "is variant" | Variant-vs-reference | Reference is resistant at 3 codons |
| 15 | Resolve S4 on either allele | Wild-type only | Discards S84L, S80F, P451S |
| 16 | Drop 6 unresolvable entries | Guess a gene | Mis-assignment worse than unresolved |
| 17 | Self-test without data | Validate on first real run | Catches mapping errors pre-download |
| 18 | 16 MB probe before batch | Resubmit six multi-GB jobs | Isolated cause in 88 s |
| 19 | `insufficient_coverage` distinct | Merge into susceptible | Would fake a perfect result |
| 20 | Bracken over Kraken2 report | `_k2_report.txt` | 69% of reads strand at genus; 28% vs 99.99% |
| 21 | Score across all passing hits | Top hit only | Sample 20: cultured organism ranked 4th |
| 22 | `culture_negative` its own verdict | Score as false positive | Sample 46: culture missed a real infection |
| 23 | Count specimens, not runs | Count every aliquot | fil partners would double-count agreement |

## Caveats carried through this report

- The species arm has run on real data (20 runs / 18 specimens). The Clair3
  arm has not: its validation is against reference sequence and published
  tables only.
- 18 specimens is not 115, and the cohort is deliberately enriched for
  *S. aureus*. The agreement counts are not comparable to the paper's
  77% PPA / 90% NPA.
- The sample-number join rests on base-count agreement across six specimens,
  not on an explicit statement by the authors.
- `samtools` and `minimap2` are not installed on the development machine; the
  local arm is unexecuted. This is an arm64 Mac — verify Clair3 image
  architecture before assuming `docker run` works.
- Table S3's depth column is the *authors'* depth, useful for cohort selection
  but not a substitute for depth re-derived from our own BAMs.
- Methicillin, the clinically central drug, is currently uncallable.
