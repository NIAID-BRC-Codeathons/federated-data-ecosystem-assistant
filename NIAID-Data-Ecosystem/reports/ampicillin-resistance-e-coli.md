# Ampicillin resistance in *Escherichia coli*

A federated-data walkthrough: from mechanism, to phenotype counts, to raw reads,
to the protein interaction network behind the genes.

Assembled 2026-09-16 from live queries against the NIAID Data Ecosystem
(`mcp__nde__*`), BV-BRC (`mcp__bvbrc-mcp__*`), STRING (`mcp__string-mcp__*`),
and the ENA portal API. Counts are as of that date and will drift as the
indexes rebuild.

---

## 1. The drug and its target

Ampicillin is an aminopenicillin: a β-lactam that acylates the active-site
serine of penicillin-binding proteins (PBPs), the transpeptidases that
cross-link peptidoglycan. In *E. coli* the lethal targets are mainly PBP1a/1b
and PBP3 (FtsI); inhibition produces the classic filamentation-then-lysis
phenotype.

Ampicillin reached clinics in 1961 and resistant *E. coli* were reported
within a few years. It is now among the most widespread resistances in the
species.

---

## 2. Phenotype data — BV-BRC

BV-BRC's `genome_amr` collection holds **241,447** ampicillin records for
*Escherichia* genomes.

| Evidence | Records | Resistant | Susceptible | Intermediate |
|---|---|---|---|---|
| Computational Method | 229,343 | — | — | — |
| Laboratory Method | 11,945 | 3,801 | 2,907 | 13 |

Among lab-tested records **with a called phenotype, 57% are resistant**
(3,801 / 6,721); the remainder of the 11,945 carry an MIC measurement but no
phenotype call. Across all evidence types: 53,622 resistant, 67,931
susceptible, 50,748 uncalled.

Testing methods: disk diffusion (6,775), broth dilution (3,385), MIC (969).
Measurements are overwhelmingly in mg/L (120,550) rather than mm (133).

Note that "Intermediate" is essentially absent (13 records) — ampicillin is
reported here as a near-binary call, not a three-tier one.

- Browse: https://www.bv-brc.org/view/GenomeList/?eq(genome_name,Escherichia*)#view_tab=amr&filter=eq(antibiotic,ampicillin)

### Caveat on the evidence split

95% of these records are computational predictions. Any claim about resistance
*rates* should be drawn from the Laboratory Method subset only. The
computational records are useful for gene–phenotype association work, not for
prevalence estimates.

---

## 3. Mechanisms, weighted by genome content

Faceting BV-BRC's `sp_gene` table for *Escherichia* β-lactamases annotated
against NDARO (234,173 hits):

| Gene family | Hits | Class | What it is |
|---|---|---|---|
| `ampC` | 16,679 | C | chromosomal cephalosporinase |
| `blaTEM` | 12,789 | A | plasmid/Tn*3*-borne — the classic ampicillin determinant |
| `blaEC` | 9,299 | C | intrinsic *E. coli* chromosomal AmpC lineage |
| `blaCTX-M` | 6,531 | A | ESBL |
| `blaOXA` | 6,477 | D | oxacillinase |
| `blaCMY` | 1,522 | C | plasmid-borne AmpC |
| `blaNDM` | 1,358 | B | metallo-carbapenemase |
| `blaSHV` | 165 | A | — |

Two dominant routes:

1. **Acquired TEM-type β-lactamase.** TEM-1 mobilized on Tn*3*/Tn*2*
   transposons and spread globally on plasmids.
2. **Chromosomal AmpC de-repression.** *E. coli* carries a chromosomal `ampC`
   expressed at a low, non-inducible level — it lacks the `ampR` regulator that
   makes *Enterobacter* inducible. Resistance arises from promoter/attenuator
   mutations that up-regulate it, not from acquisition.

Combined `ampC` + `blaEC` counts exceed `blaTEM`. Gene *presence* is not the
same as being the causal determinant in a given isolate, so this ranking
should not be read as "AmpC causes more ampicillin resistance than TEM" —
only that the chromosomal lineage is near-universal in the species while TEM
is acquired.

Secondary contributors (present, not dominant for ampicillin specifically):
efflux via AcrAB-TolC (323,054 hits genus-wide in the resistance table),
reduced OmpF/OmpC porin permeability. PBP target alteration is rare in
*E. coli* compared to e.g. *S. pneumoniae*.

The broader hydrolase pool: **286,115** *Escherichia* features annotated as
β-lactamase-related under PATRIC's own annotation.

---

## 4. Dataset landscape — NDE

```
nde_search_datasets(query="ampicillin resistance",
                    pathogen="Escherichia coli")
→ q = (ampicillin resistance) AND @type:"Dataset"
        AND infectiousAgent.name:"Escherichia coli"
→ 327 matches
```

Repository breakdown: Figshare 259 (largely journal supplements), NCBI SRA 23,
NCBI BioProject 22, NCBI GEO 9, Zenodo 6, Mendeley 5, Dryad 2, PDB 2.

Assay types where recorded: sequencing 22, WGS 14, high-throughput sequencer 7,
high-throughput expression 7, RNA-seq 7. **270 of 327 records have no
`measurementTechnique` at all** — a metadata-completeness gap worth knowing
about before filtering on that field.

### False-positive warning

A visible fraction of the 327 match because "ampicillin resistance" appears as
a **plasmid selection marker** (AmpR) rather than as the study subject. Example:
`zenodo_14060701`, expression vectors for a green alga, matched on the vector
map. Filter accordingly.

---

## 5. Five studies with read data

Run-level metadata resolved via the ENA portal `filereport` endpoint.

### 5.1 Laboratory evolution of ampicillin resistance in UPEC

**SRP445789** / BioProject **PRJNA987582** — Kangwon National University, 2023

Uropathogenic *E. coli* CFT073 passaged under continuous low and high
ampicillin exposure, then sequenced against the parent. The cleanest
before/after design in this set: three genomes, one variable.

| Run | Sample | Reads | Bases |
|---|---|---|---|
| SRR25021235 | Wild type (CFT073) | 19,060,526 | 5.76 Gbp |
| SRR25021234 | Low resistance to Amp | 18,783,860 | 5.67 Gbp |
| SRR25021233 | High resistance to Amp | 17,967,175 | 5.43 Gbp |

WGS, Illumina NovaSeq 6000, paired-end. **3 runs, 16.86 Gbp, 6.5 GB compressed.**

- SRA: https://www.ncbi.nlm.nih.gov/sra/SRP445789
- NDE: https://data.niaid.nih.gov/resources?id=ncbi_sra_srp445789

### 5.2 Cyclic evolution from tolerance to resistance

**SRP411899** / **PRJNA909844** — Jinan University, 2024

"Ampicillin-controlled glucose metabolism manipulates the transition from
tolerance to resistance in bacteria." Isolates sequenced across serial
generations of cyclic ampicillin exposure, ~20 biological replicates per
generation (samples titled e.g. *"treatment of generation 9 biological
replicate 18"*). Use this to watch resistance mutations arise and fix over time.

**205 runs, 337.92 Gbp, 118.8 GB compressed.** WGS, NovaSeq 6000, paired-end.
Examples: SRR22571224 (gen 4 rep 8, 3.99M reads), SRR22571241 (gen 9 rep 18,
7.65M reads).

Full manifest:
```
https://www.ebi.ac.uk/ena/portal/api/filereport?accession=PRJNA909844&result=read_run&fields=run_accession,sample_title,fastq_ftp&format=tsv
```

- SRA: https://www.ncbi.nlm.nih.gov/sra/SRP411899
- NDE: https://data.niaid.nih.gov/resources?id=ncbi_sra_srp411899

### 5.3 TraDIS screen for genes tolerating sub-MIC β-lactams

**SRP395140** / **PRJNA875563** — University of Newcastle, 2022

Transposon mutant library of *E. coli* BW25113 challenged with 1 µg/ml
ampicillin, 1 µg/ml benzylpenicillin, or 6.25 µg/ml benzylpenicillin.
Functional-genomics complement to the evolution studies: which genes are
*required* to survive sub-inhibitory drug, rather than which mutations confer
resistance.

**8 runs, 1.09 Gbp, 1.0 GB.** Tn-Seq, Illumina MiSeq (1×42 bp),
SRR21383877–SRR21383884, 2.0M–5.0M reads each. Smallest download here by a
wide margin.

- SRA: https://www.ncbi.nlm.nih.gov/sra/SRP395140
- NDE: https://data.niaid.nih.gov/resources?id=ncbi_sra_srp395140

### 5.4 Transcriptome + methylome of adaptive resistance

**SRP429717** / **PRJNA949588** — University of Calabria, 2023

RNA-seq of *E. coli* JM109 selected at sub-MIC for ampicillin, gentamicin, or
ciprofloxacin versus untreated control — epigenetic (5mC/6mA) regulation of the
adaptive-resistance transcriptome. The only expression dataset in this set, and
it supplies two non-β-lactam arms as specificity controls.

| Run | Sample | Reads | Bases |
|---|---|---|---|
| SRR23989494 | JM109 control (untreated) | 34,962,940 | 7.01 Gbp |
| SRR23989495 | **ampicillin-resistant** | 34,827,400 | 6.99 Gbp |
| SRR23989492 | gentamicin-resistant | 19,423,482 | 3.89 Gbp |
| SRR23989493 | ciprofloxacin-resistant | 29,469,409 | 5.91 Gbp |

**4 runs, 23.80 Gbp, 9.2 GB.** RNA-Seq, NovaSeq 6000, paired-end.
**No replicates** — one library per condition, so treat differential-expression
calls as exploratory.

- SRA: https://www.ncbi.nlm.nih.gov/sra/SRP429717
- NDE: https://data.niaid.nih.gov/resources?id=ncbi_sra_srp429717

### 5.5 Clinical isolates where blaTEM-1 is the sole acquired β-lactamase

**PRJNA1134622** — Oxford "OXEC" collection, 2024

Deliberately filtered to isolates whose only acquired β-lactamase is
`blaTEM-1`, making it the population-scale counterpart to the lab-evolution
work. Submitters flag that some deposited candidates were later dropped for
carrying non-TEM-1 variants — filter before use.

**548 runs, 254.61 Gbp, 245.5 GB.** WGS; substantial Oxford Nanopore (MinION)
content alongside Illumina. Examples: SRR30896101 (OXEC-247, 743,839 long reads,
2.55 Gbp), SRR30896095 (OXEC-252, 1.04 Gbp). Long reads matter here — TEM-1 copy
number and IS*26*-mediated amplification are hard to resolve with short reads.

- BioProject: https://www.ncbi.nlm.nih.gov/bioproject/PRJNA1134622
- NDE: https://data.niaid.nih.gov/resources?id=prjna1134622

### Also relevant

**PRJNA550338** (10 runs, MiSeq + MinION) — piperacillin/tazobactam resistance
from **IS*26*-associated amplification of `blaTEM-1`**: resistance by gene
dosage rather than point mutation. Different drug, but mechanistically the
closest thing here to an explanation of how TEM-1 escalates beyond plain
ampicillin resistance.

### Data volume summary

| Study | Runs | Bases | Compressed |
|---|---|---|---|
| PRJNA987582 (UPEC lab evolution) | 3 | 16.86 Gbp | 6.5 GB |
| PRJNA949588 (RNA-seq adaptive) | 4 | 23.80 Gbp | 9.2 GB |
| PRJNA875563 (TraDIS) | 8 | 1.09 Gbp | 1.0 GB |
| PRJNA909844 (cyclic evolution) | 205 | 337.92 Gbp | 118.8 GB |
| PRJNA1134622 (OXEC blaTEM-1) | 548 | 254.61 Gbp | 245.5 GB |

---

## 6. Protein interaction network — STRING

Seventeen genes implicated by the mechanisms and studies above, queried against
*E. coli* K-12 MG1655 (taxon 511145). All 17 resolved.

Gene set: PBP targets (`ftsI`, `mrcA`, `mrcB`, `mrdA`); chromosomal
β-lactamase and its induction/recycling pathway (`ampC`, `ampG`, `ampD`,
`nagZ`); efflux (`acrA`, `acrB`, `tolC`); *mar* regulon (`marR`, `marA`);
porins and their two-component regulator (`ompF`, `ompC`, `envZ`, `ompR`).

### The set is a genuine module

| Metric | Value |
|---|---|
| Nodes / edges | 17 / 59 |
| Expected edges by chance | 5 |
| Average node degree | 6.94 |
| Local clustering coefficient | 0.792 |
| PPI enrichment p-value | < 1e-16 (reported as 0) |

Twelve-fold more connections than a random set of this size.

### Functional enrichment

| Category | Term | Genes | FDR |
|---|---|---|---|
| KEGG | eco01501 beta-Lactam resistance | 11 | 1.9e-19 |
| Process | GO:0046677 Response to antibiotic | 8 | 1.7e-5 |
| Process | GO:0071555 Cell wall organization | 7 | 3.6e-5 |
| Keyword | KW-0046 Antibiotic resistance | 7 | 1.6e-6 |
| Keyword | KW-0961 Cell wall biogenesis/degradation | 7 | 1.6e-6 |
| Process | GO:0000270 Peptidoglycan metabolic process | 6 | 4.6e-4 |
| KEGG | eco02020 Two-component system | 6 | 1.2e-3 |
| Process | GO:0009252 Peptidoglycan biosynthesis | 5 | 5.9e-4 |
| KEGG | eco00550 Peptidoglycan biosynthesis | 4 | 2.1e-4 |
| Process | GO:0140330 Xenobiotic detox by outer-membrane export | 3 | 4.4e-3 |

### Two clusters, two mechanisms

MCL clustering (inflation 3.0) splits the network in half along a
mechanistically meaningful line:

**Cluster 1 (9 proteins) — keeping the drug out.**
`ompC`, `ompF`, `acrA`, `acrB`, `tolC`, `marA`, `marR`, `ompR`, `envZ`.
Influx control and efflux plus their transcriptional regulators. The
permeability arm.

**Cluster 2 (8 proteins) — the target and the enzyme.**
`ftsI`, `mrcA`, `mrcB`, `mrdA`, `ampC`, `ampG`, `ampD`, `nagZ`.
The PBPs ampicillin inhibits, plus the peptidoglycan-recycling pathway that
signals AmpC induction. The target/hydrolysis arm.

STRING labels both clusters "beta-Lactam resistance" — the same KEGG pathway
reached by two independent routes.

Network image (STRING-hosted, may expire):
https://string-db.org/images/userimages/network.by6lOOH2roFy.png

### Physical complexes vs. functional association

**This distinction matters more than the dense picture suggests.** Restricting
to the physical subnetwork collapses 59 edges to **10**. What survives:

| Interaction | Score | Experimental | Database |
|---|---|---|---|
| acrA–acrB | 0.999 | 0.957 | 0.900 |
| acrA–tolC | 0.999 | 0.952 | 0.900 |
| acrB–tolC | 0.998 | 0.901 | 0.900 |
| ompF–ompC | 0.998 | — | 0.900 |
| envZ–ompR | 0.992 | 0.603 | — |
| mrdA–mrcA | 0.983 | 0.526 | — |
| ftsI–mrcB | 0.973 | 0.457 | — |
| mrcB–mrcA | 0.965 | 0.457 | 0.900 |
| ftsI–mrdA | 0.950 | — | 0.900 |
| ftsI–mrcA | 0.424 | — | — |

The AcrA–AcrB–TolC tripartite efflux pump is the best-evidenced structure in
the network. EnvZ–OmpR is the two-component pair; OmpF–OmpC form a mixed
heterotrimer; the four PBPs interconnect.

The 49 edges that drop out are functional associations, most text-mining-driven
co-mention. Specifically:

- **Every `ampC` edge is text-mining only** — `ampD`–`ampC` (0.911),
  `ampG`–`ampC` (0.906), `ftsI`–`ampC` (0.904), `nagZ`–`ampC` (0.842). AmpC
  hydrolyzes a substrate and is regulated transcriptionally; it does not bind
  these partners. The co-mention reflects a real *pathway* relationship, not a
  complex.
- `marA`→`tolC` (0.902) and `marA`→`acrA` (0.899) are regulator→target
  relationships, not physical contacts.

### Cross-cluster edges

Where the two mechanisms interact:

- `marA`→`tolC` (0.902), `marA`→`acrA` (0.899), `marA`→`ompF` (0.892).
  **MarA is the hub coordinating both arms**: it activates efflux *and*
  represses OmpF (via micF), raising resistance from both directions at once.
  MarR represses `marAB`, so loss-of-function `marR` mutations de-repress the
  whole program. This is the mechanistic basis for the `marR` (129,773) and
  `tolC` (130,245) hits in the BV-BRC resistance table in §3.
- `ompF`/`ompC`–`tolC` (0.970 / 0.942, largely phylogenetic profile) — influx
  and efflux co-evolve.
- `nagZ`–`mrcA` (0.626), `ampD`–`mrcA` (0.574), `ampG`–`mrcA` (0.568) —
  peptidoglycan recycling feeding back onto synthesis.

---

## 7. Where the network meets the data

The interaction structure predicts what each dataset should show:

- **TraDIS (SRP395140)** screened for genes required to survive sub-MIC
  ampicillin. Hits should concentrate in **cluster 1** — at sub-inhibitory
  drug the cell survives by exclusion, not hydrolysis.
- **RNA-seq of adaptive resistance (SRP429717)** should show the *mar* regulon
  and its downstream efflux/porin targets moving as a coordinated unit.
- **Lab-evolution genomes (SRP445789, PRJNA909844)** are where `marR` or `ampC`
  promoter mutations should appear — the two single-locus changes that shift
  each cluster wholesale.
- **OXEC clinical isolates (PRJNA1134622)** test whether the lab-evolution
  routes match what selection actually produces in patients.

None of these predictions were tested here; they are the analyses this
assembled evidence sets up.

---

## 8. Provenance and tooling notes

### Queries run

| Source | Query | Result |
|---|---|---|
| NDE | `(ampicillin resistance) AND @type:"Dataset" AND infectiousAgent.name:"Escherichia coli"` | 327 |
| NDE | same + `includedInDataCatalog.name:"NCBI SRA"`, broadened to `ampicillin OR beta-lactam` | 2,181 |
| BV-BRC | `genome_amr`: `genome_name:Escherichia* AND antibiotic:ampicillin` | 241,447 |
| BV-BRC | `sp_gene`: `genome_name:Escherichia* AND source:NDARO AND product:*beta-lactamase*` | 234,173 |
| BV-BRC | `genome_feature`: `genome_name:Escherichia* AND product:*beta-lactamase* AND annotation:PATRIC` | 286,115 |
| STRING | 17-protein functional network, taxon 511145, score ≥ 0.400 | 59 edges |
| STRING | same, physical network | 10 edges |
| ENA | `filereport` per study accession | see §5 |

### Two tooling gaps found

1. **`mcp__bvbrc-mcp__agent_search_data` field validation.** Querying
   `genome_feature` with `genus:Escherichia` fails — `genus` exists on
   `genome`, `misc_niaid_sgc`, `sp_gene_ref`, and `strain`, but not
   `genome_feature`. The error response helpfully lists valid fields and
   suggests alternate collections. Use `genome_name:Escherichia*` instead.

2. **`mcp__brc-analytics-mcp__search_ena_keywords` rejects bare accessions.**
   Passing `SRP395140` or `PRJNA949588` returns HTTP 400 — the tool builds a
   free-text ENA query that the portal rejects for accession-shaped input. All
   run-level metadata in §5 came from direct curl against
   `https://www.ebi.ac.uk/ena/portal/api/filereport`. Worth fixing if
   accession lookup is meant to be in scope.

### Reproducing the ENA calls

```bash
for acc in PRJNA987582 PRJNA949588 PRJNA875563 PRJNA909844 PRJNA1134622; do
  curl -s "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${acc}&result=read_run&fields=run_accession,sample_title,library_strategy,instrument_model,read_count,base_count,fastq_ftp,fastq_bytes&format=tsv"
done
```

### Caveats carried through this report

- BV-BRC ampicillin records are 95% computational predictions; the 57%
  resistance figure is from the 6,721 lab-tested records with a phenotype call.
- Gene-family counts measure presence, not causation.
- 270 of 327 NDE dataset hits lack a `measurementTechnique` value.
- Some NDE hits match on AmpR as a plasmid selection marker, not as subject.
- 49 of 59 STRING edges are functional association, not physical interaction;
  all `ampC` edges are text-mining-derived.
- SRP429717 has no biological replicates.
