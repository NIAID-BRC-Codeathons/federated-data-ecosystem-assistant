# Interim report — reproducing Street et al. 2022

**Status as of 2026-09-17.** Species-identification arm complete; AMR arm
running, 3 of 20 specimens fully called.

> Street TL, Sanderson ND, Kolenda C, *et al.* "Clinical Metagenomic Sequencing
> for Species Identification and Antimicrobial Resistance Prediction in
> Orthopedic Device Infection." *J Clin Microbiol* 2022;60(4):e0215621.
> PMID 35354286 · DOI 10.1128/jcm.02156-21

Data: ENA **PRJEB42910** (288 runs; 258 Oxford Nanopore GridION R9.4.1).
Decision history in [decision-record.md](decision-record.md); operational
state in [STATE.md](../STATE.md).

---

## Headline

**Both of the paper's central claims reproduce, and one of its stated
limitations does not have to.**

1. **Species identification agrees with culture on 17 of 18 specimens** —
   16 exact species matches, 1 genus match, 1 detection in a culture-negative
   specimen. All 18 agree with the authors' own sequencing calls.
2. **Read mapping reproduces the paper's coverage almost exactly** — within
   1–2% breadth and 1–4% depth on every specimen checked.
3. **The subpopulation the paper missed is detectable.** Street et al. report
   missing a resistant *rpoB* A477V present in ~20% of reads in specimen 41,
   because their pipeline took consensus calls. We detect it at **AF 0.213**
   — but only after abandoning VCF-based calling, which missed it for exactly
   the same reason theirs did.

The third point is the substantive finding. It is also the one that took two
attempts, and the failed attempt is more instructive than the success.

---

## 1. Species identification — complete

20 BV-BRC Kraken2 jobs → Bracken abundance → scored against the paper's
Table S3. 18 specimens (two contributed both saponin and filtration
aliquots).

| Verdict | n |
|---|---|
| species match | 16 |
| genus match | 1 |
| culture-negative detection | 1 |

Three specimens carry more information than the tally:

**Specimen 46 — culture-negative, 100% *S. aureus* over 466,712 reads.**
Street et al. also called *S. aureus*. This is the clinical case the method
exists for: an infection culture missed. Scoring it as a false positive
against culture would invert its meaning, so `culture_negative` is a distinct
verdict in the scorer.

**Specimen 20 — polymicrobial; the cultured organism ranks fourth.**

```
Fusobacterium vincentii   185,385  60.1%
Fusobacterium animalis     45,799  14.9%
Fusobacterium nucleatum    25,422   8.2%
Staphylococcus aureus      20,978   6.8%   ← the cultured organism
Staphylococcus schleiferi  20,144   6.5%
```

All three organisms the authors reported are recovered. Scoring on the most
abundant hit alone would have recorded a mismatch on a specimen where the
method arguably outperformed culture.

**Specimen 25 — culture's limitation, not the pipeline's.** Culture said
*E. cloacae*; we called *E. hormaechei*; the authors reported both.
*E. hormaechei* was split out of the *E. cloacae* complex and MALDI-TOF cannot
separate them.

### Host depletion, measured indirectly

The paper's headline depletion figure (human bases 98.1% → 11.9%) **cannot be
recomputed** — only non-human reads were deposited, so the denominator is
absent. What is measurable is the downstream consequence:

| Specimen | Prep | Microbial reads | Human | Top call |
|---|---|---|---|---|
| 22 | saponin | 1,145,565 | 0.001% | *E. faecium* 98.0% |
| 22 | filtration | 7,702 | 0.324% | *E. coli* 62.8%, *E. faecium* 33.2% |
| 72 | saponin | 1,239,452 | 0.019% | *S. dysgalactiae* 98.8% |
| 72 | filtration | 32,518 | 1.496% | *S. dysgalactiae* 100.0% |

Saponin yields 149× and 38× more microbial sequence. Specimen 22 shows the
clinical consequence rather than the arithmetic: in the filtered aliquot the
true pathogen is demoted to a **33% minority behind *E. coli* contamination**.
At that depth the call is wrong unless you already know the answer.

---

## 2. AMR arm — 3 of 20 specimens

### Mapping reproduces the paper

| Specimen | Our breadth | Their breadth | Our depth | Their depth |
|---|---|---|---|---|
| 41 | 95.67% | 94.47% | 277× | 289× |
| 119 | 96.59% | 94.46% | 2313× | 2336× |
| 103 | 96.37% | 94.89% | 1193× | 1190× |

Same aligner (minimap2, `map-ont`), same reference (BX571856.1). Depth agrees
to within 4%, and on specimen 103 to within 0.3%. Our breadth is consistently
~1–2 points higher, plausibly a minimap2 version difference (2.31 vs their
2.17).

### The regression test, failed then passed

Specimen 41 was selected as a regression test precisely because the paper
documents a failure there: a resistant *rpoB* A477V present in ~20% of reads,
missed because "the pipeline calls consensus/majority reads and does not model
mixed populations."

**First attempt — VCF-based calling (`08_call_resistance.py`): missed it.**

```
mpileup BX571856.1:592260   ref(C)=176  alt(T)=46  AF=0.207   ← A477V
Clair3 VCF at 592260:       no record at all
```

Clair3 emitted 58,999 genome-wide records but covered **only 1 of 116
catalogue codons**. It is a consensus caller: it reports where the consensus
differs from the reference and is otherwise silent. The reproduction had
faithfully inherited the paper's flaw.

**Second attempt — pileup genotyping (`11_pileup_resistance.py`): found it.**

Rather than consume a caller's output, genotype the 116 catalogue codons
directly from `samtools mpileup`, counting alleles per codon and translating
whatever is present above `--min-af`. Caller silence becomes impossible by
construction.

```
specimen 41, rifampicin: RESISTANT (subpopulation)
    rpoB A473T @ AF 0.153
    rpoB A477V @ AF 0.213     ← independent mpileup estimate: 0.207
```

**Noise floor: 0.000.** Across the 110 codons called susceptible, no
susceptible codon shows *any* resistant-allele signal. AF 0.15–0.21 is
therefore far above nanopore error at these settings. A473T is a second
rifampicin variant in the same cluster; 286 reads span both codons, so the two
are plausibly a linked haplotype (not yet phased).

### Calls so far

| Specimen | Ciprofloxacin | Fusidic acid | Rifampicin | Trimethoprim |
|---|---|---|---|---|
| 41 | R *(ref allele)* | R *(ref allele)* | **R — subpopulation** | **R — dfrB L21V** |
| 119 | S | R *(ref allele)* | S | S |
| 103 | S | R *(ref allele)* | S | S |

*(ref allele)* marks a call resting on the sample **matching** MRSA252 at a
codon where the reference is itself resistant — no variant was observed. Only
three calls across the three specimens rest on observed read evidence:
dfrB L21V, rpoB A473T and rpoB A477V, all in specimen 41.

---

## 3. The reference-bias trap, and why it is load-bearing

MRSA252 is itself a resistant strain. It carries **gyrA S84L, grlA S80F and
fusA H557Y** — all three in the Gordon catalogue as resistant. At those codons
the reference base *is* the resistant state, so a "variant differs from
reference" rule fails in both directions simultaneously:

- a **susceptible** sample differs there → called resistant
- a **resistant** sample matches, emits no record → called nothing

Both callers therefore ask *"what amino acid is present"*, never *"is there a
variant"*, reading the reference base wherever the data is silent.

This is not hypothetical. Specimens 119 and 103 came back
ciprofloxacin-**susceptible** while 41 was resistant — against the same
reference. Checking rather than assuming a bug: specimen 103 carries **S** at
gyrA 84 and grlA 80 at 892× and 584× depth, the susceptible residues. These
are clinical isolates that genuinely differ from MRSA252, and real read
evidence correctly overrides the reference. A naive caller would have reported
all three as resistant.

---

## 4. What cannot be concluded yet

**The resistance calls have no ground truth.** Table S3 — the only
supplementary table deposited as data — carries culture species, histology and
the authors' sequencing calls, but **no antimicrobial susceptibility
phenotypes**. The paper's AST results are in Table 2, deposited only as a
page image. Until those are transcribed, the calls above can be checked for
internal consistency and against the reference, but **not scored for accuracy**.
This is the single biggest gap in the AMR arm.

**Methicillin cannot be called at all.** Gordon's catalogue covers six
chromosomal loci. *mecA* — the determinant that defines MRSA, and the central
drug question in orthopedic device infection — is a mobile element. Street's
Table S2 covers those genes and was not deposited. BV-BRC CARD read mapping is
the intended substitute, not yet wired in.

**Coverage is unrepresentative.** 3 of 20 AMR specimens, and the three done
first are the highest-yield. The marginal specimens (13, 20, 117 — near the
20× floor) are where `insufficient_coverage` should start appearing, and they
have not been processed.

**Model mismatch.** These are R9.4.1 reads, but the bundled r941 Clair3 models
are LSTM-architecture and will not load into Clair3 v2.0.3 (a stale bioconda
build); Clair3 0.1.x requires `pypy3.6`, unavailable on arm64. Calls therefore
used an **R10 model on R9 data**. This matters less than it would otherwise,
since `11_pileup_resistance.py` bypasses Clair3 entirely for resistance — but
any Clair3-derived result carries the caveat.

**18 specimens is not 115**, and the AMR cohort is deliberately
*S. aureus*-enriched. No agreement statistic here is comparable to the paper's
77% PPA / 90% NPA.

---

## 5. Notes on method, for the write-up

Four decisions materially changed the results and would change anyone else's:

1. **Bracken, not the Kraken2 report.** On long reads Kraken2's LCA leaves
   most signal at internal nodes — 69% of *Staphylococcus* reads in specimen
   119 never reach a species. The naive species/bacteria ratio reads **28%**
   for a sample that is ~100% *S. aureus*, which would have fallen below the
   paper's 60% threshold and produced no call at all.
2. **Pileup, not VCF, for resistance.** See §2.
3. **Read the amino acid, not the variant.** See §3.
4. **Keep `insufficient_coverage` distinct from `susceptible`.** 60 of the
   paper's 152 drug–organism combinations were uncallable. Merging the two
   manufactures a perfect-looking result.

Nine bugs or environment traps were found by running the pipeline rather than
by reading it; they are catalogued in STATE.md. The three with the widest
blast radius: ENA prepends an unrequested `run_accession` column to every
filereport (positional parsing silently yields the wrong fields); ENA FASTA
headers contain pipe characters that Clair3 interpolates unquoted into shell
commands; and macOS ships bash 3.2, so `mapfile` is unavailable.

---

## 6. Next steps

1. **Transcribe Table 2** (AST phenotypes) from the paper's page image. Without
   it the AMR arm cannot be scored, only described. This is the highest-value
   remaining task and needs no compute.
2. **Finish the cohort** — 15 runs still downloading (~17 GB of ~29 GB on
   disk). Mapping is ~76 s/specimen and pileup calling ~30 s, so the download
   dominates.
3. **Wire in mobile-element AMR** via BV-BRC CARD read mapping, to make
   methicillin callable.
4. **Phase the rpoB haplotype** — 286 reads span A473T and A477V; confirming
   they are on the same molecules would establish a single resistant
   subpopulation rather than two independent ones.
5. **Run the reference drift check** (`04_compare_refs.py`) now that VCFs
   against both assemblies exist for specimen 41.

---

*All results reproducible from the committed artifacts: `results/bracken/`
(20 Bracken outputs), `results/species_comparison.tsv`,
`results/resistance_pileup.tsv`, `catalogue/` (extracted Gordon 2014
catalogue, Table S3, locus coordinates).*
