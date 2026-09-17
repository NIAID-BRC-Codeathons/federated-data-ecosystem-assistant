# Reproducing Street et al. 2022 — orthopedic device infection metagenomics

An attempt to re-run the analysis in:

> Street TL, Sanderson ND, Kolenda C, *et al.* **Clinical Metagenomic Sequencing
> for Species Identification and Antimicrobial Resistance Prediction in
> Orthopedic Device Infection.** *J Clin Microbiol* 2022;60(4):e0215621.
> PMID [35354286](https://pubmed.ncbi.nlm.nih.gov/35354286/) ·
> DOI [10.1128/jcm.02156-21](https://doi.org/10.1128/jcm.02156-21)

Data: ENA **PRJEB42910** / ERP126837 (288 runs — 258 Oxford Nanopore GridION,
30 Illumina MiSeq). Authors' pipeline:
[genericbugontworkflow](https://gitlab.com/ModernisingMedicalMicrobiology/genericbugontworkflow).

> **[STATE.md](STATE.md)** tracks what has actually been run, jobs in flight,
> next steps, and the traps already paid for. Read it before picking this up.

The work is split in two. Species identification runs on **BV-BRC**; the
variant-calling arm runs **locally**, because BV-BRC has no nanopore-aware
variant caller.

---

## What is and isn't a reproduction

Being blunt about this up front, because three of the paper's steps cannot be
reproduced from public data and a fourth is only approximated.

| Paper step | Here | Faithful? |
|---|---|---|
| 5% saponin host depletion | — | **No — wet lab.** Already applied to deposited reads. |
| Species ID + Youden-optimized filter | Kraken2 (BV-BRC, `standard` db) | **Substitute.** Different classifier, different thresholds. |
| minimap2 → MRSA252 | minimap2 `-ax map-ont` | **Yes.** Same tool, same preset, same reference. |
| Clair (commit 54c7dd4) | Clair3 | **Successor, not the same.** Clair3 supersedes Clair's architecture. |
| Random-forest variant filter | — | **No.** Their model (trained on *N. gonorrhoeae*) was never published as an artifact. |
| Gordon 2014 SNP catalogue | `catalogue/gordon2014_resistance_snps.tsv` | **Extracted and applied** — 122 substitutions, 116 usable. |
| Resistance calling | `08_call_resistance.py` | Codon-aware, reference-bias-safe. See below. |
| Mobile-element AMR genes | BV-BRC CARD read mapping | **Analogue.** Different database from their curated Table S2. |

### The human-fraction claim is not recoverable

The paper's headline depletion result — human bases falling from a median
**98.1%** to **11.9%** — cannot be reproduced from PRJEB42910. Street et al.
deposited only reads *"classified as nonhuman"*. The host reads were removed
before upload, so the denominator is gone.

What *is* measurable is the downstream consequence: microbial yield and
pathogen breadth per preparation, which is what decides whether the ≥20× depth
needed for resistance calling is reachable. The paired aliquots make that
contrast visible from ENA metadata before a single base is mapped:

| Patient | Saponin | Filtration | Ratio |
|---|---|---|---|
| 103 | 3.82 Gb | 0.016 Gb | **240×** |
| 22 | 4.16 Gb | 0.034 Gb | **121×** |
| 72 | 5.37 Gb | 0.144 Gb | **37×** |

Raw yield is not the same claim as human fraction — two aliquots can differ in
yield for ordinary library reasons. Treat this as corroborating, not
confirming.

---

## The two-reference problem

Both are called "MRSA252". They are **not the same sequence**:

| | Length | BV-BRC | Note |
|---|---|---|---|
| **BX571856.1** | 2,902,619 bp | `282458.100` | Sanger assembly. What the paper used. What Gordon 2014 coordinates index against. |
| **CP194230** | 2,902,592 bp | `1280.63071` | Later resequencing. 27 bp shorter, 2 CDS different. |

The trap: `1280.63071` is the one that looks right in BV-BRC — status
*Complete*, single contig, 30 CARD and 20 NDARO annotations attached. It is
the **wrong** reference for catalogue coordinates. A resistance SNP looked up
at CP194230 coordinates against a BX571856.1-indexed catalogue is wrong by an
unknown offset past the first indel, and nothing will raise an error.

Everything runs against both. `04_compare_refs.py` reports whether the
disagreements are a constant liftable offset or scattered. `00_fetch_refs.sh`
hard-fails on a length mismatch rather than proceeding.

---

## Layout

```
odi-clair3/
  config.sh                     paths, model, thresholds — source this
  samples.tsv                   depletion cohort (9 runs, sap/fil pairs)
  amr_cohort.tsv                S. aureus cohort (20 runs, from ground truth)
  scripts/
    00_fetch_refs.sh            both assemblies + length verification
    01_fetch_reads.sh           FASTQs from ENA (resumable)
    02_map.sh                   minimap2 -ax map-ont -> sorted BAM + coverage
    03_call_clair3.sh           Clair3, containerized
    04_compare_refs.py          primary-vs-secondary concordance + offsets
    05_depletion_summary.py     sap vs fil yield and breadth
    06_build_catalogue.py       Gordon 2014 PDF -> resistance SNP TSV
    07_extract_truth.py         Street Table S3 xlsx -> per-specimen truth
  catalogue/
    gordon2014_resistance_snps.tsv   122 substitutions, 4 genes
    street2022_table_s3.tsv          115 specimens, culture + their calls
    catalogue_meta.json              provenance and caveats
    src/                             the downloaded supplements
  work/                         gitignored: FASTQ, BAM, VCF, logs
```

## Running

```bash
export ODI_WORK=/scratch/odi          # anywhere with ~60 GB
export ODI_RUNTIME=docker             # or singularity, or native
export ODI_THREADS=16

scripts/06_build_catalogue.py         # no downloads needed
scripts/07_extract_truth.py           # no downloads needed

scripts/00_fetch_refs.sh
export ODI_COHORT=amr_cohort.tsv      # or leave unset for samples.tsv
scripts/01_fetch_reads.sh
scripts/02_map.sh
scripts/03_call_clair3.sh
scripts/04_compare_refs.py --all --cohort amr_cohort.tsv
scripts/05_depletion_summary.py
```

Each step skips completed work, so re-running is cheap. `05`, `06` and `07`
work without downloading any reads.

Requires: `curl`, `samtools`, `minimap2`, `python3`, and Docker or Singularity
for Clair3. No Python packages beyond the stdlib — the xlsx and PDF parsers
are hand-rolled precisely to avoid a dependency for two one-off extractions.

### Model choice

`CLAIR3_MODEL` defaults to `r941_prom_sup_g5014`. These are **R9.4.1** GridION
reads collected 2018–2020 — an r10 model on r9 data miscalibrates quality
throughout. If the basecaller is confirmed as HAC rather than SUP, switch to
`r941_prom_hac_g360+g422`.

---

## Cohort

Six high-yield saponin runs, plus the three filtration partners:

| Run | Patient | Prep | Reads | Bases |
|---|---|---|---|---|
| ERR5260785 | 119 | sap | 2,735,794 | 7.35 Gb |
| ERR5260607 | 25 | sap | 1,667,686 | 5.56 Gb |
| ERR5260520 | 16 | sap | 1,739,475 | 5.53 Gb |
| ERR5260530 | 72 | sap | 1,291,114 | 5.37 Gb |
| ERR5260738 | 22 | sap | 1,169,326 | 4.16 Gb |
| ERR5260958 | 103 | sap | 1,310,249 | 3.82 Gb |
| ERR5260516 | 72 | fil | 35,068 | 0.14 Gb |
| ERR5260755 | 22 | fil | 7,977 | 0.034 Gb |
| ERR5260976 | 103 | fil | 6,198 | 0.016 Gb |

Patients 22 and 79 each have a second saponin run (ERR5260478, ERR5260611) —
technical replicates, useful for checking variant-call stability.

The deposit's `sample_title` field encodes patient, specimen and prep
(`patient_119_sonication fluid_sap`), so the two arms are separable from
metadata alone. Neither NDE record annotates a health condition or pathogen
list, so this study will not surface under a `healthCondition` filter.

---

## Ground truth and cohort selection

`07_extract_truth.py` pulls Street's Table S3 — the only supplementary table
deposited as data — giving culture result, histology, and the authors' own
sequencing call for all 115 specimens. Its 48 culture-negative specimens match
the paper's stated 48 exactly, which is the check that the row parsing and
forward-fill of multi-species rows are correct.

**ENA `patient_N` = Table S3 `sample N`.** Verified on six specimens by
matching non-human base counts to within 0.01% (sample 119: S3 7,350,065,534
vs ENA 7,349,284,900; the gap is reads dropped at deposit).

That join is what makes `amr_cohort.tsv` possible. Selecting on sequencing
yield — the obvious first move, and what `samples.tsv` does — is a bad proxy:
of the six highest-yield saponin runs, only two are *S. aureus*. The AMR
cohort is instead selected on `sequencing_species == S. aureus` **and** the
authors' own mean depth ≥ 20×, their threshold for attempting a resistance
call. That yields 14 specimens / 20 runs, including three with technical
replicates.

**Sample 41 is the regression test.** Street et al. missed a resistant *rpoB*
A477V present in ~20% of reads there, because their pipeline took consensus
calls and did not model mixed populations. A477V *is* in the extracted
catalogue (rifampicin, MIC 1). Clair3 reports allele fractions, so a working
arm should see what theirs could not. If we also miss it, the reproduction has
inherited the flaw rather than fixed it.

## The catalogue

`06_build_catalogue.py` parses Gordon 2014's supplementary PDF into 122 unique
amino-acid substitutions across four loci: *fusA* (60, fusidic acid), *rpoB*
(25, rifampicin), *dfrB* (8, trimethoprim), and *grlA/gyrA/grlB* (29,
quinolones).

Two extraction hazards, both handled, both worth knowing if you re-run it:

- **Spacing is semantic.** The PDF emits each glyph separately; a *single*
  space separates characters within a word but *two or more* separate table
  columns. Collapsing all whitespace welds each MIC onto its reference marker
  (`>128` + `1` → `>1281`), silently corrupting every MIC in the table.
- **`[A-Z]` is not the amino-acid alphabet.** A naive substitution regex
  accepts `B434N`; B, J, O, U, X and Z are not amino acids. The parser
  requires membership in the 20-letter set.

The 29 Table S4 entries are marked `gene_ambiguous` — that table interleaves
three genes across columns and the flattened text does not preserve which
column a substitution came from. They are flagged rather than guessed, since a
mis-assigned gene is worse than one marked unresolved.

## Resistance calling, and the reference-bias trap

`08_call_resistance.py` bridges the two coordinate systems: the catalogue is
in *protein* coordinates, a Clair3 VCF is in *genome* coordinates. CDS
boundaries come from the EMBL feature table of BX571856.1 itself
(`catalogue/loci_BX571856.1.tsv`), not a secondary annotation. Five of six
agree exactly with BV-BRC `282458.100`; *grlB* does not (ENA 1417763 vs BV-BRC
1417769), and ENA wins because Gordon numbered against that record. *fusA*
carries no `/gene` tag there — it is `fus`, SAR0552.

**MRSA252 is itself quinolone-resistant.** The reference already carries three
catalogue resistance alleles:

```
gyrA S84L      grlA S80F      fusA H557Y
```

At these codons the reference base *is* the resistant state, so a
"variant differs from reference" rule fails in both directions at once: a
susceptible sample differs there and gets called resistant, while a resistant
sample matches the reference, emits no VCF record, and gets called nothing.

So the caller never asks whether a variant exists. It asks **what amino acid
this sample has at this codon** — reading the reference base wherever the VCF
is silent, translating the codon, and comparing to the catalogue. Absence of a
VCF record is evidence of the reference allele, which at these three codons
means resistant.

`--self-test` validates the whole mapping with no sample data: it confirms all
six CDS translate cleanly (correct start, terminal stop, no internal stops)
and then *rediscovers the reference-resistant alleles independently* by
checking each catalogue entry against the genome. 107 of 114 entries match the
catalogue wild-type, 3 match the resistant allele, 6 match neither.

Those 6 are unresolvable Table S4 entries. Gordon's S4 interleaves three genes
across columns and the flattened PDF does not record which column a
substitution came from, so the script assigns each by testing which candidate
gene carries either the wild-type *or* the resistant amino acid at that codon
— matching on wild-type alone would discard S84L and S80F, precisely the
entries that matter most. Six (S80Y, S84A, P451S, P585S, E422D, D443E) match
no candidate at either allele and are dropped with a warning rather than
guessed. **116 of 122 entries are usable**: *fusA* 60, *rpoB* 25, *grlA* 15,
*dfrB* 8, *gyrA* 7, *grlB* 1.

Three verdict states, deliberately:

- `resistant` / `resistant_subpopulation` — the latter when a resistant allele
  is present above `--min-af` (default 0.10) but is not the consensus. This is
  the sample-41 case: Street et al. missed an *rpoB* A477V at ~20% of reads
  because their pipeline took consensus calls. Clair3 reports allele depths,
  so this arm can see it.
- `susceptible`
- `insufficient_coverage` / `no_data` — **never** merged into `susceptible`.
  60 of the paper's 152 drug–organism combinations landed here. A codon with
  no reads is not a susceptible codon.

Per-drug rollup is deliberately asymmetric: any resistant codon makes the drug
resistant, but a drug is only called susceptible when *every* one of its
codons was callable.

## Unfinished

1. **Mobile-element genes are absent.** Gordon covers six chromosomal loci
   only. *mecA*, *ermA/C*, *tetK/M*, *aacA-aphD* live in Street's Table S2,
   which was not deposited. BV-BRC CARD read mapping is the intended
   substitute, and without it methicillin — the clinically central drug
   here — cannot be called at all.
2. **Six Table S4 entries unassigned** — see above.
3. **No random-forest filter.** Clair3's own quality model plus the paper's
   ≥20× depth threshold is what stands in. Expect more false positives than
   the paper reports.
4. **Indels are skipped.** The caller handles single-base substitutions only;
   Gordon's `ins 475H` / `ins 475G` *rpoB* entries are not actionable.
5. **Nothing has actually been run.** Every result above is from the
   self-test, which validates coordinates and catalogue logic against the
   reference genome alone. No reads have been downloaded, mapped, or called.
   The pipeline is verified as far as it can be without data.

## BV-BRC arm: a submission gotcha

The Kraken2 jobs are submitted through the `TaxonomicClassification` app with
`srr_libs`. **`sample_id` is required inside each `srr_libs` entry.** Omitting
it is accepted by the GoWe submission layer and by preflight, then the job
dies 2–4 minutes in with `success: 0`, no output files, and no error message —
the app has no filename to write under. Six jobs were lost to this before the
cause was isolated with a 16 MB probe run.

ENA `ERR` accessions work fine despite the field being documented as "SRA
Sample accession with SRR prefix"; that was the wrong first hypothesis.

---

*Scaffolded 2026-09-16. Accessions and assembly lengths verified live against
the ENA portal API and BV-BRC.*
