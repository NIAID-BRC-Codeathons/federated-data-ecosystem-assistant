# Worked examples

Tool calls and the queries they generate, verified against the live API.
Counts are from September 2026 and will drift as the index rebuilds.

## Discovery: what is even in here?

```
nde_list_repositories()
→ 55 repositories, 20.5M harvested records
  NCBI GEO 8,826,454 · Zenodo 6,861,956 · Figshare 2,177,036
  NCBI BioProject 1,112,164 · NCBI SRA 658,408 · …
```

```
nde_facet_counts(fields="@type")
→ Sample 8,728,311 · Dataset 5,448,004 · ComputationalTool 33,272
  DataCollection 11,567 · ResourceCatalog 43
```

## Keyword search with filters

```
nde_search_datasets(pathogen="Influenza A virus", repository="NCBI SRA", size=3)
→ q = @type:"Dataset" AND infectiousAgent.name:"Influenza A virus"
        AND includedInDataCatalog.name:"NCBI SRA"
→ 461 matches
```

Comma-separated values OR within a field:

```
nde_search_datasets(query="dengue", repository="NCBI SRA, Zenodo")
→ q = dengue AND @type:"Dataset"
        AND (includedInDataCatalog.name:"NCBI SRA"
             OR includedInDataCatalog.name:"Zenodo")
```

## Semantic search

When the phrasing is conversational, keyword matching under-performs:

```
nde_semantic_search(query="immune response profiling in infants after vaccination")
→ ranked by embedding similarity rather than term overlap
```

## Tools, not data

```
nde_search_tools(query="phylogenetic", programming_language="Python")
→ q = phylogenetic AND @type:"ComputationalTool" AND programmingLanguage:"Python"
→ 378 matches
```

## The vocabulary trap

This is the one gotcha worth knowing. Annotation vocabularies differ by record
type: `Sample` records keep the submitter's raw term, `Dataset` records carry a
curated OBI term. So this looks reasonable and returns nothing:

```
nde_search_datasets(query="tuberculosis", measurement_technique="RNA-seq")
→ q = tuberculosis AND @type:"Dataset" AND measurementTechnique.name:"RNA-seq"
→ 0 matches
```

Rather than reporting an empty result, the server re-tests each filter and
explains which one conflicts:

```json
{
  "total": 0,
  "why_no_results": "Each filter below matches records on its own, but the combination does not.",
  "conflicting_filters": [
    {
      "drop_filter": "measurement_technique",
      "clause": "measurementTechnique.name:\"RNA-seq\"",
      "matches_without_it": 8730,
      "hint": "Assay vocabularies differ by record type: Sample records use raw submitter terms (\"RNA-seq\"), Dataset records use curated OBI terms (\"rna-seq assay\", …)"
    }
  ]
}
```

Following the hint:

```
nde_facet_counts(fields="measurementTechnique.name", query="tuberculosis",
                 record_type="Dataset", top_n=5)
→ sequencing assay · rna-seq assay · whole genome sequencing assay
  x-ray diffraction · amplicon sequencing assay

nde_search_datasets(query="tuberculosis", measurement_technique="rna-seq assay")
→ matches
```

A genuinely absent term gets the ordinary "no matches" hint instead — the
diagnostic never blames the free-text term, since dropping it almost always
restores hits and says nothing useful.

## Landscape summaries

```
nde_facet_counts(fields="measurementTechnique.name, includedInDataCatalog.name",
                 query="tuberculosis", top_n=5)
→ 56,907 matching records, broken down by assay and by source repository
```

## Records and provenance

```
nde_get_record(record_id="ncbi_sra_srp425935")
→ full metadata, plus links:
    source_url  https://www.ncbi.nlm.nih.gov/sra/SRP425935
    portal_url  https://data.niaid.nih.gov/resources?id=ncbi_sra_srp425935
```

Batch resolution in one round trip:

```
nde_lookup_ids(ids="ncbi_sra_srp425935, biotools_align, bogus_id")
→ matched 2/3, not_found: ["bogus_id"]
```

## Escape hatch

Queries the structured tools cannot express:

```
nde_raw_query(q='_exists_:nctid AND @type:Dataset AND NOT includedInDataCatalog.name:"NCBI GEO"')
→ 15,644 datasets linked to a ClinicalTrials.gov record, excluding GEO
```

## Limits worth knowing

- `size` caps at 1000; `offset + size` caps at 10,000. Both are clamped rather
  than erroring, and responses carry `next_offset` or a note at the boundary.
- `nde_list_fields(search=…)` matches from the start of a name segment:
  `"infectious"` finds `infectiousAgent.*`, `"agent"` finds nothing.
- Faceting over the full 14M-record index can take several seconds.
