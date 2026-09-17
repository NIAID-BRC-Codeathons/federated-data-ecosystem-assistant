# Project state — ODI metagenomics reproduction

Working notes for whoever picks this up next, including a future me. The
[README](README.md) explains what the pipeline *is*; this file records where it
*stands*, what has actually been run, and the traps already paid for.

**Last updated:** 2026-09-17
**Status:** BV-BRC species arm **complete and scored** (18 specimens). Local
AMR arm **3 of 20 specimens called**; regression test passes via pileup
genotyping. Cohort download resuming after an ENA disconnect.

See [reports/interim-report.md](reports/interim-report.md) for the written
summary of results so far.

> This directory is intended to be split out into its own repository. It has no
> dependency on the `nde-mcp` code that currently surrounds it — the only
> couplings are the two MCP servers used interactively during development
> (`mcp__nde__*` for literature/dataset discovery, `mcp__bvbrc-mcp__*` for the
> BV-BRC arm), neither of which the scripts import. Moving the folder is
> sufficient; nothing needs rewriting. Deliberately not documented in the
> parent repo's CLAUDE.md for this reason.

---

## The target

> Street TL, Sanderson ND, Kolenda C, *et al.* "Clinical Metagenomic Sequencing
> for Species Identification and Antimicrobial Resistance Prediction in
> Orthopedic Device Infection." *J Clin Microbiol* 2022;60(4):e0215621.
> PMID 35354286 · PMCID PMC9020354 · DOI 10.1128/jcm.02156-21

Data: ENA **PRJEB42910** / ERP126837 — 288 runs, 258 Oxford Nanopore GridION
(R9.4.1) + 30 Illumina MiSeq. Authors' pipeline:
`gitlab.com/ModernisingMedicalMicrobiology/genericbugontworkflow`.

Two arms: **species ID on BV-BRC** (Kraken2), **variant calling locally**
(Clair3), because BV-BRC has no nanopore-aware variant caller.

---

## Where things stand

| Component | State |
|---|---|
| Reference handling (`00`) | **run** — both assemblies verified |
| Read fetching (`01`) | **running** — 5/20 on disk (17 GB); retries added |
| Mapping (`02`) | **run** — 3 specimens; ~76 s each, both refs |
| Clair3 calling (`03`) | **run** — native bioconda, 5 min/sample |
| Reference drift check (`04`) | written, needs VCFs |
| Depletion summary (`05`) | **runs now**, produces real output from ENA metadata |
| Catalogue build (`06`) | **run** — 122 substitutions extracted |
| Ground truth extract (`07`) | **run** — 115 specimens extracted |
| Resistance calling (`08`) | **run** — VCF-based; misses subpopulations |
| Species scoring (`09`) | **run** — 20 runs / 18 specimens scored |
| BV-BRC Kraken2 arm | **complete** — 20 jobs |
| Bracken fetch (`10`) | **run** — pulls results via the BV-BRC CLI |
| Pileup resistance (`11`) | **run** — 3 specimens; detects A477V |

Both arms have produced real results (below). Species: 18 specimens. AMR:
3 specimens (41, 119, 103) with the rest downloading.

### Result: species identification (2026-09-16)

**20 runs / 18 specimens**, Bracken abundance ≥5% of microbial reads and ≥700
reads. Two specimens (22, 72) contributed both a saponin and a filtration
aliquot; the tally counts each specimen once.

| Sample | Device | Culture | Ours (top) | Frac | vs culture |
|---|---|---|---|---|---|
| 6 | Knee | *S. aureus* | *S. aureus* | 100.0% | species |
| 10 | Knee (endobutton) | *S. aureus* | *S. aureus* | 100.0% | species |
| 13 | Metalwork (fibula) | *S. aureus* | *S. aureus* | 97.3% | species |
| 16 | Hip | *S. lugdunensis* | *S. lugdunensis* | 97.5% | species |
| 20 | Hip | *S. aureus* | *F. vincentii* +4 | 60.1% | species (polymicrobial) |
| 22 | Knee | *E. faecium* | *E. faecium* | 98.0% | species |
| 25 | Knee | *E. cloacae* | *E. hormaechei* | 89.0% | **genus** |
| 27 | Elbow | *S. aureus* | *S. aureus* | 100.0% | species |
| 38 | Hip | *S. aureus* | *S. aureus* | 100.0% | species |
| 39 | Knee | *S. aureus* | *S. aureus* | 99.8% | species |
| 41 | Hip | *S. aureus* | *S. aureus* | 98.4% | species |
| 46 | Knee | **negative** | *S. aureus* | 100.0% | **culture-negative** |
| 54 | Knee | *S. aureus* | *S. aureus* | 99.9% | species |
| 72 | Hip | *S. dysgalactiae* | *S. dysgalactiae* | 98.8% | species |
| 79 | Knee | *S. aureus* | *S. aureus* | 100.0% | species |
| 103 | Hip | *S. aureus* | *S. aureus* | 100.0% | species |
| 117 | Knee | *S. aureus* | *S. aureus* | 100.0% | species |
| 119 | Knee | *S. aureus* | *S. aureus* | 100.0% | species |

**16 species matches, 1 genus match, 1 culture-negative.** Every specimen also
agrees with the authors' own sequencing call — 18/18, including the two where
we differ from culture.

**Sample 46 — culture-negative, 100% *S. aureus* over 466,712 reads.** Street
et al. also called *S. aureus* here. This is the clinical case the method
exists for: an infection culture missed. It is the reason
`agreement()` returns `culture_negative` as its own verdict rather than
scoring it as a false positive.

**Sample 20 — genuine polymicrobial.** We recover all three organisms the
authors reported (*S. aureus*, *S. schleiferi*, *F. nucleatum*) plus two
additional *Fusobacterium* species that are almost certainly the same organism
split across closely-related database entries. The cultured organism is only
the *fourth* most abundant at 6.8%; scoring on the top hit alone would have
recorded a mismatch.

**Sample 25 — probably culture's limitation, not ours.** Culture said
*E. cloacae*, we called *E. hormaechei*. **Street et al. reported both.**
*E. hormaechei* was split out of the *E. cloacae* complex and MALDI-TOF cannot
separate them. Two independent pipelines disagreeing with culture in the same
direction is evidence about the reference standard.

#### Depletion effect, paired aliquots

| Specimen | Prep | Microbial reads | Human | Top call |
|---|---|---|---|---|
| 22 | sap | 1,145,565 | 0.001% | *E. faecium* 98.0% |
| 22 | fil | 7,702 | 0.324% | *E. coli* 62.8%, *E. faecium* 33.2% |
| 72 | sap | 1,239,452 | 0.019% | *S. dysgalactiae* 98.8% |
| 72 | fil | 32,518 | 1.496% | *S. dysgalactiae* 100.0% |

Saponin yields **149×** and **38×** more microbial reads. In specimen 22's
filtered aliquot the true pathogen is demoted to a 33% minority behind *E.
coli* contamination — at that depth the call would be wrong without the
culture result to check against. Residual human rises 300×/79× under
filtration, consistent with the paper's depletion claim while not being the
same measurement (see §5 of the traps).

Raw Bracken outputs are committed under `results/bracken/`; the scored table is
`results/species_comparison.tsv`.

**Caveat:** the abundance filter substitutes for the paper's Youden-optimized
coverage criteria, which need per-species alignment the BV-BRC service does not
perform. 18 specimens is not 115, and this cohort is deliberately enriched for
*S. aureus*. These counts are **not** comparable to the paper's 77% PPA /
90% NPA.

### Result: AMR arm — 3 of 20 specimens (2026-09-17)

The full local chain now runs end to end: fetch → minimap2 → Clair3 →
resistance call. Mapping reproduces the paper closely:

| Specimen | Our breadth | Their breadth | Our depth | Their depth |
|---|---|---|---|---|
| 41 | 95.67% | 94.47% | 277× | 289× |
| 119 | 96.59% | 94.46% | 2313× | 2336× |
| 103 | 96.37% | 94.89% | 1193× | 1190× |

Depth agrees to within 4%, and on specimen 103 to within 0.3%. Our breadth
runs ~1–2 points higher throughout — plausibly the minimap2 version gap
(2.31 vs their 2.17).

All 13,175 resistance-locus positions exceed 20×, so every catalogue codon is
callable on coverage grounds.

**The VCF-based caller failed the regression test**, and the reason mattered
more than the calls — it is what motivated `11_pileup_resistance.py` below.

*rpoB* A477V — the ~20% subpopulation Street et al. reported missing — **is
present in our data and we also missed it**:

```
samtools mpileup BX571856.1:592260   (rpoB codon 477, base 2)
  ref(C)=176  alt(T)=46  depth=222  AF=0.207     ← GCT->GTT = A477V
Clair3 VCF at 592260: no record at all
```

Clair3 emitted **no variant record** there, so `08_call_resistance.py` never
saw it. The subpopulation-detection logic (`--min-af 0.10`,
`resistant_subpopulation`) is untested by this run because it depends on
Clair3 reporting the site. **The reproduction has inherited the paper's flaw
rather than fixing it**, which is exactly what `amr_cohort.tsv` predicted
would need checking.

More broadly, **only 1 of 116 catalogue codons has any Clair3 record**
(58,999 VCF positions genome-wide). Clair3 is a consensus caller: it reports
sites where the consensus differs from the reference and is largely silent
elsewhere. That is fine for the reference-reading design, but it means
sub-consensus alleles are invisible.

Calls produced for sample 41:

| Drug | Verdict | Evidence |
|---|---|---|
| ciprofloxacin | RESISTANT | grlA S80F, gyrA S84L — **AF 0.000, `ref_is_res=1`** |
| fusidic acid | RESISTANT | fusA H557Y — **AF 0.000, `ref_is_res=1`** |
| trimethoprim | RESISTANT | dfrB L21V — AF 0.947, depth 221 (**real variant**) |
| rifampicin | susceptible | but see A477V above |

Three of four resistant calls rest on the sample *matching* MRSA252 at codons
where the reference is itself resistant — no variant was observed. For a
genuinely MRSA252-like strain that is the correct answer, and the
reference-bias design is what makes it come out right rather than inverted.
But it is a weak form of evidence and the `ref_is_resistant` / AF columns
exist so it cannot be mistaken for a positive detection.

**Only `dfrB L21V` is a true observed variant in this sample.**

#### The fix: `11_pileup_resistance.py` — **regression test now PASSES**

A consensus caller cannot answer the question the regression test poses, so
the fix was to stop asking it. `11_pileup_resistance.py` genotypes the 116
catalogue codons directly from `samtools mpileup`, counting alleles per codon
and translating whatever is present above `--min-af`. Sub-consensus alleles
are first-class, and caller silence is impossible by construction.

Sample 41, same BAM, same catalogue:

```
ciprofloxacin  RESISTANT  (grlA S80F [ref allele], gyrA S84L [ref allele])
fusidic acid   RESISTANT  (fusA H557Y)
rifampicin     RESISTANT (subpopulation)
                 rpoB A473T @ AF 0.153
                 rpoB A477V @ AF 0.213   ← the variant the paper missed
trimethoprim   RESISTANT  (dfrB L21V)
[4 observed variants among 116 catalogue codons]
```

**A477V detected at AF 0.213**, against an independent `samtools mpileup`
estimate of 0.207 (46 of 222 reads). This is the ~20% subpopulation Street
et al. reported their consensus pipeline could not see.

A473T at AF 0.153 is a second rifampicin-resistance variant in the same
cluster; 286 reads span both codons, so the two are plausibly a linked
resistant haplotype (not yet phased — see next steps).

**Noise-floor check.** Across the 110 codons called susceptible, the maximum
spurious resistant-allele fraction is **0.000** — no susceptible codon shows
any resistant signal at all. So AF 0.15–0.21 is far above the nanopore error
floor at these settings, and the two rpoB calls are not artifacts.

`[ref allele]` in the output marks calls that rest on the sample *matching* a
resistant reference rather than on an observed variant — still the correct
verdict for an MRSA252-like strain, but flagged so it cannot be mistaken for
a positive detection.

#### Two callers, kept deliberately

`08` (VCF-based) and `11` (pileup-based) share their catalogue, locus and
translation code via import, so they cannot drift on coordinates. `11` is the
one to trust for resistance; `08` remains useful as the Clair3-consuming path
and as a cross-check. On sample 41 they agree on all four drugs except
rifampicin, where `08` says susceptible and `11` finds the subpopulation —
which is precisely the difference this work set out to measure.

`--min-bq 7` (not samtools' default 13): nanopore base qualities are low
enough that the default discards real signal.

#### Specimens 119 and 103: the reference-bias logic proves itself

Both came back ciprofloxacin-**susceptible** while 41 was resistant on the
same reference. That looked like a bug and is not: specimen 103 carries **S**
at gyrA 84 and grlA 80 at 892× and 584× depth — the *susceptible* residues.
These are clinical isolates that genuinely differ from MRSA252, and real read
evidence correctly overrides the resistant reference. A naive
variant-vs-reference caller would have reported all three as resistant.

| Specimen | Cipro | Fusidic acid | Rifampicin | Trimethoprim |
|---|---|---|---|---|
| 41 | R *(ref allele)* | R *(ref allele)* | **R — subpopulation** | **R — dfrB L21V** |
| 119 | S | R *(ref allele)* | S | S |
| 103 | S | R *(ref allele)* | S | S |

Only **three** calls across the three specimens rest on observed read
evidence: dfrB L21V, rpoB A473T, rpoB A477V — all in specimen 41. Everything
else marked *(ref allele)* is the sample matching a resistant reference.

#### No ground truth for the AMR calls

**Table S3 carries no antimicrobial susceptibility phenotypes** — only culture
species, histology and the authors' sequencing calls. The paper's AST results
are in Table 2, deposited as a page image (`catalogue/src/street/
jcm.02156-21_t002.jpg`). Until transcribed, resistance calls can be checked
for internal consistency but **not scored for accuracy**. This is the biggest
gap in the AMR arm and needs no compute to close.

### Local environment

Installed and working: `samtools` 1.24, `minimap2` 2.31 (both Homebrew),
Clair3 **v2.0.3 via bioconda**, native arm64 — no container needed:

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate clair3
export ODI_RUNTIME=native ODI_THREADS=8 ODI_COHORT=amr_cohort.tsv
export CLAIR3_MODEL=r1041_e82_400bps_sup_v430_bacteria_finetuned
```

**No container runtime is installed** (no docker/podman/singularity/colima),
and none is needed for the native path. The Python
scripts use only the stdlib, on purpose: the xlsx and PDF parsers are
hand-rolled to avoid a dependency for two one-off extractions. (No
`pdftotext` either -- 06_build_catalogue.py decompresses the PDF streams
itself.)

The BV-BRC CLI (`p3-ls`, `p3-cp`) IS available and is what `10_fetch_bracken.sh`
uses. Note macOS ships **bash 3.2**, so the shell scripts avoid `mapfile`
(bash 4+); if you add array-reading code, use `while IFS= read -r`.

### BV-BRC jobs

All 20 complete. Output under `/olson@patricbrc.org/home/odi-metagenomics`,
one dot-prefixed folder per job (`.tax_<accession>_<sample_id>/`). Results are
mirrored locally in `results/bracken/`, so the analysis is reproducible without
BV-BRC access.

Re-fetch with `scripts/10_fetch_bracken.sh` (needs `p3-login`). Runtime was
1.5–42 min per job, scaling with read count.

---

## Traps already paid for

Each of these cost real time. They are recorded so they are not rediscovered.

### 1. The `sample_id` trap (BV-BRC)

`TaxonomicClassification` requires `sample_id` **inside each `srr_libs`
entry**. Omitting it is accepted by the GoWe submission layer *and* by
preflight, then the job dies 2–4 minutes in with `success: 0`, **no output
files, and no error message** — the app has no filename to write under.

Six jobs were lost to this. The wrong first hypothesis was the ERR-vs-SRR
prefix (the field is documented as "SRA Sample accession with SRR prefix");
ENA `ERR` accessions work fine. Isolated with a 16 MB probe run, which
completed in 88 seconds.

**Always include `sample_id`. Always probe with the smallest run first.**

### 2. MRSA252 is itself quinolone-resistant

The reference genome carries three catalogue *resistance* alleles natively:

```
gyrA S84L      grlA S80F      fusA H557Y
```

A "variant differs from reference" rule fails in **both directions at once**
here: a susceptible sample differs at those codons and is called resistant,
while a resistant sample matches the reference, emits no VCF record, and is
called nothing.

`08_call_resistance.py` therefore asks "what amino acid does this sample have
at this codon", reading the reference base wherever the VCF is silent. Absence
of a VCF record is *evidence of the reference allele*, which at these three
codons means resistant.

This also bit the first implementation of the Table S4 gene-assignment
resolver, which matched only on wild-type AA and so silently discarded S84L,
S80F and P451S — precisely the entries that matter most. It now matches either
allele, preferring wild-type when both are available.

### 3. Two different "MRSA252" assemblies

| | Length | BV-BRC | Note |
|---|---|---|---|
| **BX571856.1** | 2,902,619 bp | `282458.100` | Sanger. What the paper used, what Gordon 2014 coordinates index against. |
| **CP194230** | 2,902,592 bp | `1280.63071` | Later resequencing. 27 bp shorter, 2 CDS different. |

The trap: `1280.63071` is the one that *looks* right in BV-BRC — status
Complete, single contig, 30 CARD and 20 NDARO annotations attached. It is the
**wrong** reference for catalogue coordinates. `00_fetch_refs.sh` hard-fails on
a length mismatch rather than proceeding.

Locus coordinates were taken from the EMBL feature table of BX571856.1 itself.
Five of six agree exactly with BV-BRC; ***grlB* does not** — ENA 1417763 vs
BV-BRC 1417769, a 6 bp start-codon difference. ENA wins. Also: *fusA* has no
`/gene` qualifier in that record; it is `fus`, SAR0552, findable only by
product.

### 4. PDF spacing is semantic

In the Gordon supplement a **single** space separates characters within a word
but **two or more** separate table columns. Collapsing all whitespace welds
each MIC onto its reference marker (`>128` + `1` → `>1281`), silently
corrupting every MIC in the table. `06_build_catalogue.py` protects
multi-space runs as sentinels before gluing up letter spacing.

Related: `[A-Z]` is not the amino-acid alphabet. A naive substitution regex
accepts `B434N`; B, J, O, U, X, Z are not amino acids.

### 5. The human-fraction claim is not reproducible

Street et al. deposited only reads *"classified as nonhuman"*. The 98.1% →
11.9% host-depletion figure has no recoverable denominator in PRJEB42910. Any
script reporting a "% human" from these files is measuring something else.

What *is* measurable is microbial yield and pathogen breadth per preparation —
the downstream consequence that determines whether ≥20× is reachable. Three
paired aliquots exist (patients 22, 72, 103); yield ratios 121×, 37×, 240×.

### 6. Yield is a bad proxy for organism

The first cohort was picked on sequencing yield. Only 2 of 6 turned out to be
*S. aureus*. `amr_cohort.tsv` is instead selected from the paper's own ground
truth. Don't re-derive cohorts from file sizes.

### 7. Shared working tree

Mid-session, most of this directory was deleted by another process and a stash
appeared: `stash@{0}: "not-mine: odi-clair3 pipeline + gitignore"`. Plain
`git stash` skips untracked files, so the scripts were **deleted, not saved**;
they were rebuilt from cached sources. The branch also moved under us.

**Commit early here.** Someone else works in this repo.

---

### 8. ENA prepends an unrequested column

`filereport?...&fields=fastq_ftp,fastq_bytes` returns **three** columns —
`run_accession` is always first, requested or not. Positional `awk '{print
$1,$2}'` therefore yields the accession and the URL, not the URL and the byte
count, and curl tries to resolve the accession as a hostname. Select columns
by name from the header row.

### 9. ENA FASTA headers break Clair3

Headers arrive as `>ENA|BX571856|BX571856.1 Staphylococcus aureus ...`. Clair3
interpolates the contig name into shell commands **unquoted**, so the pipes
are parsed as a pipeline and the run dies with `BX571856.1_1.vcf: command not
found`. `00_fetch_refs.sh` now rewrites the header to a bare accession.

### 10. The bioconda r941 models will not load

Clair3 v2.0.3 expects Conv/ResNet checkpoints; the bundled `r941_prom_*`
models are LSTM-architecture and fail with a `state_dict` mismatch. Clair3
0.1.x (the LSTM-era code) requires `pypy3.6`, unavailable on arm64. So R9.4.1
data is being called with an **R10 model**
(`r1041_e82_400bps_sup_v430_bacteria_finetuned`). This matters less than it
sounds because `11_pileup_resistance.py` bypasses Clair3 for resistance, but
any Clair3-derived result carries the caveat.

### 11. ENA drops long transfers

A 7.8 GB pull died at 88% with `curl: (18) transfer closed`, and `set -e`
killed the whole loop — 15 runs stranded. `01_fetch_reads.sh` now retries with
`-C -` resume, up to `ODI_MAX_ATTEMPTS` (default 6), and never lets one run
abort the rest.

### 12. macOS bash is 3.2

`mapfile` is bash 4+. Four scripts used it and would have failed on first run.
Use `while IFS= read -r`.

## Key facts worth not re-deriving

**ENA `patient_N` == Street Table S3 `sample N`.** Verified on six specimens by
matching non-human base counts to within 0.01% (sample 119: S3 7,350,065,534
vs ENA 7,349,284,900; the gap is reads dropped at deposit). This join is what
makes ground-truth-driven cohort selection possible.

**Table S3 extraction is correct**: 48 culture-negative specimens, matching the
paper's stated 48 exactly. 19 *S. aureus* by culture, 20 by their sequencing,
17 agreeing.

**Catalogue**: 122 substitutions extracted, **116 usable** after gene
resolution — *fusA* 60, *rpoB* 25, *grlA* 15, *dfrB* 8, *gyrA* 7, *grlB* 1.
Six (S80Y, S84A, P451S, P585S, E422D, D443E) match no candidate gene at either
allele; checked for a systematic frameshift, near-misses are scattered, so they
are genuinely unresolvable from the flattened PDF and are dropped with a
warning rather than guessed.

**Sample 41 is the regression test.** Street et al. missed a resistant *rpoB*
A477V at ~20% of reads there because their pipeline took consensus calls.
A477V is in the catalogue (rifampicin, MIC 1). Clair3 reports allele
fractions, so a working arm should catch it as `resistant_subpopulation`. If
this arm also misses it, the reproduction has inherited the flaw rather than
fixed it.

**Deposited sample titles encode the arm**: `patient_119_sonication fluid_sap`
vs `..._fil`. The saponin/filtration split is selectable from metadata alone.

---

## Next steps, in order

1. ~~Collect and score the Kraken2 results.~~ **Done** — 18 specimens.
2. ~~Install samtools/minimap2, decide the Clair3 host.~~ **Done** — native
   bioconda on arm64, no container.
3. **Transcribe Table 2 (AST phenotypes)** from the page image. Highest-value
   remaining task; needs no compute. Without it the AMR arm cannot be scored.
4. **Finish the cohort** — 15 runs still to download. Mapping ~76 s/specimen,
   pileup calling ~30 s; the download dominates. The marginal specimens
   (13, 20, 117, near the 20× floor) are where `insufficient_coverage` should
   start appearing and are untested so far.
5. **Wire in mobile-element AMR** via BV-BRC CARD read mapping, to make
   methicillin callable.
6. **Phase the rpoB haplotype** — 286 reads span A473T and A477V; confirming
   they sit on the same molecules would establish one resistant subpopulation
   rather than two independent ones.
7. **Run `04_compare_refs.py`** now that specimen 41 has VCFs against both
   assemblies — the reference-drift check has never actually been executed.

## Deliberate non-goals

- **Not reproducing the random-forest variant filter.** Street's model was
  trained on their earlier *N. gonorrhoeae* work and never published as an
  artifact. Clair3's own quality model plus the ≥20× depth threshold stands in.
  Expect more false positives than the paper reports.
- **Not reproducing saponin depletion.** Wet-lab step; already applied to the
  deposited reads.
- **Not collapsing `insufficient_coverage` into `susceptible`.** 60 of the
  paper's 152 drug–organism combinations were uncallable. A codon with no reads
  is not a susceptible codon, and merging the two is how a perfect-looking
  result gets manufactured.
