# CLAUDE.md

## What this repo is

`nde-mcp` — an MCP server over the NIAID Data Ecosystem Discovery Portal API
(`api.data.niaid.nih.gov`), which federates ~14M metadata records for biomedical
datasets, biological samples, computational tools, and resource catalogs from
NCBI GEO/SRA/BioProject, Zenodo, Figshare, PDB, bio.tools, dbGaP, ImmPort and 50
other repositories.

The server is registered project-scoped in `.mcp.json` and its tools appear as
`mcp__nde__*`.

## When to use the NDE tools

**Any question about what biomedical data, datasets, samples, or bioinformatics
tools exist should go to the `mcp__nde__*` tools first — not to model knowledge,
and not to WebSearch.**

The model's training data cannot know what is currently in these repositories,
how many records match, or what a record's accession and download URL are. The
NDE index is rebuilt continuously; answering from memory produces plausible,
unverifiable, and frequently wrong answers. Reach for the tools when a question
involves any of:

- Whether data exists on a pathogen, disease, host organism, gene, or assay
  ("is there RNA-seq data on tuberculosis?", "what's available for dengue?")
- How much data exists, or how it breaks down — counts, trends, which
  repository holds it (`nde_facet_counts`)
- Finding a specific dataset, accession, DOI, or study
- What bioinformatics software or workflows exist for a task
  (`nde_search_tools`)
- Which repositories cover a topic, or how current they are
  (`nde_list_repositories`)

This applies to casual phrasing too. "What flu data is out there?" is a search
question, not a conversational one.

### Choosing among the tools

- `nde_search_datasets` — the default. Keyword plus structured filters.
- `nde_semantic_search` — when the phrasing is conversational or conceptual and
  keyword matching would likely miss ("immune profiling in infants after
  vaccination").
- `nde_facet_counts` — for "how many", "which", "what kinds of" questions, and
  to discover valid filter values *before* filtering on them.
- `nde_search_tools` — software and workflows, not data.
- `nde_get_record` / `nde_lookup_ids` — once you have ids.
- `nde_raw_query` — escape hatch for Lucene the structured tools can't express.

### When NOT to use them

Conceptual biology questions with no data-retrieval component ("how does
CRISPR work?", "what is a k-mer?") should be answered directly. The NDE index
holds dataset *metadata*, not biological facts, sequences, or literature.

## Reporting results

Report what the tools actually returned. Two specifics:

- **Always include provenance.** Every hit carries a `repository`, a
  `source_url`, and a `portal_url`. Cite them — the point of this project is
  traceable answers, not an opaque chatbot.
- **Show the query when it's non-obvious.** Responses echo the generated Lucene
  in `query` and the full request in `api_call`. Surface these when the routing
  or filtering was a judgment call, so the user can check your work.
- Give real counts from `total`. Don't round vaguely or imply completeness the
  result doesn't support.

## The vocabulary trap

Annotation vocabularies differ by record type. `Sample` records keep the
submitter's raw term ("RNA-seq"); `Dataset` records carry a curated OBI term
("rna-seq assay"). So `record_type=Dataset` + `measurement_technique=RNA-seq`
returns **zero** even though millions of datasets have an RNA-seq technique.

A zero-result response is therefore not evidence that no such data exists. The
server re-probes each filter and returns `conflicting_filters` when the filters
are mutually incompatible — follow that hint (usually via `nde_facet_counts` on
the field, restricted to the same `record_type`) rather than reporting "no data
found."

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                        # 120 offline tests, mocked transport
.venv/bin/python scripts/smoke_test.py  # every tool against the live API
```

Architecture: `client.py` (HTTP + error normalization), `query.py` (Lucene
construction, filter vocabulary, paging clamps), `format.py` (response shaping),
`server.py` (tool definitions). Tool docstrings are the model-facing contract —
when changing behavior, update the docstring too.
