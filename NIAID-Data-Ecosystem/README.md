# nde-mcp — MCP server for the NIAID Data Ecosystem

An [MCP](https://modelcontextprotocol.io) server over the **NIAID Data Ecosystem
Discovery Portal API** (`https://api.data.niaid.nih.gov`).

The portal federates metadata for roughly **14.2 million records** — datasets,
biological samples, computational tools, and resource catalogs — harvested from
NCBI GEO, NCBI SRA, NCBI BioProject, Zenodo, Figshare, the Protein Data Bank,
bio.tools, dbGaP, ImmPort, BEI Resources, HuBMAP, Vivli, and dozens of other
repositories. This server exposes that search surface to an LLM agent as a small
set of task-shaped tools.

Built for the **NIAID-BRCs AI Codeathon 2.0** federated data ecosystem assistant
project, as one of the MCP-enabled resources the assistant routes across.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Run

The server speaks MCP over stdio:

```bash
.venv/bin/nde-mcp
# or
.venv/bin/python -m nde_mcp.server
```

### Claude Code

```bash
claude mcp add nde -- /absolute/path/to/.venv/bin/nde-mcp
```

### Claude Desktop / any MCP client

Add to your client's MCP config:

```json
{
  "mcpServers": {
    "nde": {
      "command": "/absolute/path/to/.venv/bin/nde-mcp"
    }
  }
}
```

No API key is required — the NDE search API is public and unauthenticated.

### Pointing at staging

`NDE_API_URL` selects the deployment; it defaults to production. The same
server code works against either — the API surface is identical (verified:
34/34 endpoint signatures and all 1035 indexed fields match).

```bash
NDE_API_URL=https://api-staging.data.niaid.nih.gov/v1 .venv/bin/nde-mcp
```

Staging is a strict superset of production — 72 federated sources vs 55, with
nothing removed. As of 2026-09-16:

| | Production | Staging |
| --- | --- | --- |
| Searchable records | 14.2M | 84.4M |
| Sources | 55 | 72 |
| Record types | 5 | 6 (adds `Inference`) |

Staging adds NCBI BioSample (58.7M), Expression Atlas (10.4M), NCBI Virus,
BioStudies, UniProt, IEDB, BacDive, USIDNET, PathoPlexus and several culture
collections. `Inference` records are gene-level differential-expression
findings; their numeric result (fold change, p-value, gene, comparison) is
surfaced in each hit's `finding` field.

Staging is a pre-release deployment: expect its contents and availability to
change without notice. Use production for anything reproducible.

## Tools

| Tool | Purpose |
| --- | --- |
| `nde_search_datasets` | Keyword + structured filter search (pathogen, host species, health condition, assay, repository, date range, access tier, funder, author). |
| `nde_semantic_search` | Meaning-based search via the portal's `use_ai_search` mode, for conversational phrasing that keyword matching would miss. |
| `nde_search_tools` | Search computational tools and workflows by EDAM topic, language, OS, and category. |
| `nde_get_record` | Fetch complete metadata for one record by NDE id. |
| `nde_lookup_ids` | Batch-resolve many accessions/DOIs/ids in a single request. |
| `nde_facet_counts` | Group-by counts over any field — discover valid filter values, or summarize a topic's landscape. |
| `nde_list_repositories` | List federated repositories with record counts, descriptions, and freshness. |
| `nde_list_fields` | Browse the ~1000 indexed metadata fields. |
| `nde_raw_query` | Escape hatch for arbitrary Lucene queries (wildcards, negation, `_exists_`, nested booleans). |

### Design notes

**Responses are shaped, not proxied.** Raw NDE records are schema.org JSON-LD
running to hundreds of keys, including per-record embedding vectors and fully
expanded `DefinedTerm` wrappers. A single unfiltered hit can exceed 100 KB.
Search tools request a curated `_source` field set and flatten the results, so
a 10-hit response stays readable instead of swamping the context window.

**Every result carries provenance.** Each hit reports its source repository
plus both a `source_url` (the record in its home repository) and a
`portal_url` (the NDE detail page). Search responses also echo the generated
Lucene query and the exact `api_call` URL — so an agent can show its work, and
a human can reproduce it in a browser. That is a direct requirement of the
codeathon project: the assistant should expose its queries and API calls rather
than acting as an opaque chatbot.

**Filters are a vocabulary, not passthrough.** Tool arguments map to indexed
fields through an explicit table in `query.py`. Values are quoted and escaped
before being spliced into Lucene, so ontology terms containing spaces, hyphens,
or colons match as phrases rather than fragmenting into separate tokens.

**API limits are handled, not discovered.** The upstream API caps `size` at
1000 and `from + size` at 10,000. Paging arguments are clamped, and responses
carry a `next_offset` or an explanatory note at the window boundary.

**Empty results get diagnosed, not just reported.** Annotation vocabularies
differ by record type — `Sample` records keep the submitter's raw term
("RNA-seq") while `Dataset` records carry a curated OBI term ("rna-seq assay").
So `record_type=Dataset` + `measurement_technique=RNA-seq` matches zero records
even though 2.3M datasets do have an RNA-seq technique. When a search comes back
empty, the server re-tests each filter individually and reports which one
conflicts, with a hint about the vocabulary. An agent that would otherwise
report "no such data exists" gets told what to fix.

## Example questions this answers

- "What RNA-seq datasets on Mycobacterium tuberculosis were published since 2024?"
- "Which repositories hold the most influenza data, and how fresh are they?"
- "Find tools for phylogenetic analysis written in Python."
- "What assay types dominate SARS-CoV-2 datasets?" (faceting)
- "Pull the full metadata and download links for `ncbi_sra_srp425935`."

See [EXAMPLES.md](EXAMPLES.md) for worked calls with their generated queries
and real result counts.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                          # 120 offline unit tests (mocked transport)
.venv/bin/python scripts/smoke_test.py    # end-to-end: every tool against the live API
.venv/bin/python scripts/smoke_test.py -v # …and dump the responses
```

The unit tests use `httpx.MockTransport`, so they need no network and also
assert on the exact Lucene each tool generates. The smoke test is the one that
talks to the real API.

## API reference

- Portal: https://data.niaid.nih.gov
- API base: `https://api.data.niaid.nih.gov/v1`
- OpenAPI spec: `https://api.data.niaid.nih.gov/v1/spec`
- Source metadata: `https://api.data.niaid.nih.gov/v1/metadata`
- Field index: `https://api.data.niaid.nih.gov/v1/metadata/fields`

The API is a [BioThings](https://biothings.io) instance; `/query` accepts
Elasticsearch `query_string` syntax in `q`.
