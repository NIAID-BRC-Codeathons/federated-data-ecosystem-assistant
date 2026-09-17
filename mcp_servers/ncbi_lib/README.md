# NCBI MCP server

One of the MVP's "at least three resources". Exposes two NCBI services as 20 MCP
tools, all prefixed `ncbi_` so the assistant can tell them apart from the mygene,
uniprot and PDN tools when routing:

- **E-utilities** — SRA, BioSample, BioProject, PubMed, Taxonomy, Gene,
  Assembly, and sequence databases.
- **Pathogen Detection** — the Isolates Browser, a curated index of bacterial
  isolates with computed AMR genotypes.

The entry point is **`mcp_servers/ncbi.py`**, alongside the other servers. This
directory holds the implementation it imports.

## Setup

Nothing beyond the repo's own install — this server has no separate environment
and no dependencies outside the root `pyproject.toml`:

```sh
uv sync
uv run mcp_servers/ncbi.py --port 8004      # HTTP
uv run mcp_servers/ncbi.py --stdio          # stdio
uv run pytest                               # 209 tests, NCBI's 80 among them
```

Each server owns a port and a namespaced path so they can all run at once:

| server | port | path |
|---|---|---|
| `mcp_servers/pdn.py` | 8001 | `/mcp-pdn` |
| `mcp_servers/mygene.py` | 8002 | `/mcp-mygene` |
| `mcp_servers/uniprot.py` | 8003 | `/mcp-uniprot` |
| `mcp_servers/ncbi.py` | 8004 | `/mcp-ncbi` |

`chatbot.py` already lists it. Binding is loopback: this server has no auth, and
anyone who can reach it spends the host's shared 3/sec NCBI budget.

Set `NCBI_EMAIL` to your own address before running it — see
[Configuration](#configuration). No NCBI account is required; the server paces
itself at NCBI's keyless limit of 3 requests/second.

### Why this one is a package and not a single file

The other servers fit in one module. This one is ~3,300 lines, because NCBI
rate-limits **per source IP** at 3/sec: every call has to pass one
process-global limiter, which forces async tools, a shared client, retry and
backoff handling, and the provenance bookkeeping layered on top. Splitting it
puts each measured hazard behind its own seam — see [Why the code looks the way
it does](#why-the-code-looks-the-way-it-does).

### A note on `fastmcp`

This server was first built on **jlowin's standalone `fastmcp` package (v4)** and
was ported to `mcp.server.fastmcp` to live in the repo's environment. They are
different projects that share a class name, and they cannot coexist:

| | needs |
|---|---|
| `mcp.server.fastmcp` — the SDK's copy, what every server here uses | `mcp<2` |
| `fastmcp` — the standalone package | `mcp>=2.0` |

In `mcp` 2.x, `mcp.server.fastmcp` is a **tombstone** that raises
`ModuleNotFoundError` on import, pointing at a rename to `MCPServer`. So the root
`pyproject.toml` now pins `mcp >=1.15.0,<2`; before, it was unbounded and
resolved to 1.30.0 only because `uv.lock` happened to pin it, which meant
regenerating the lock would have broken all four servers at once.

One behavior differs between the two and is worth knowing if you port anything
else: **fastmcp 4 runs a sync tool on a worker thread; the SDK's copy runs it
directly on the event loop** (measured on mcp 1.30.0,
`tests/test_ncbi_invariants.py`). Either way a sync tool here would bypass the
rate limiter, which is why every tool in `server.py` is `async def`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NCBI_EMAIL` | `jonathangunti@gmail.com` | Who NCBI contacts about this software. Set it to your own. |
| `NCBI_API_KEY` | unset | Optional. Raises the rate limit to 10/sec. [Free here](https://account.ncbi.nlm.nih.gov/settings/). |
| `NCBI_MAX_RPS` | `3` (or `10` with a key) | Lowers the rate. Can only lower it. |
| `NCBI_COVERAGE` | `1` | Set to `0` to drop the `coverage` block and save one request per search. |

**The rate limit is per IP address, not per user.** If several people on the
codeathon network each run a copy of this server, they share one 3/sec budget.
Set `NCBI_MAX_RPS` to `3` divided by the number of running copies, or get an API
key.

## Every result carries its provenance

The project brief asks the agent to expose "its resource-selection rationale,
generated queries, API calls, and intermediate outputs rather than acting as an
opaque chatbot", and scores provenance directly. So every tool returns:

```json
{
  "summary": "49196 PubMed citations matched (showing 3).",
  "data": [ ... ],
  "provenance": {
    "source": "NCBI E-utilities",
    "tool": "ncbi_pubmed_search",
    "sources": [
      {"database": "pubmed", "utilities": ["esearch", "esummary"],
       "calls": 2, "percent_of_result": 100.0}
    ],
    "utilities_called": {"esearch": 2, "esummary": 1},
    "request_cost": {"total": 3, "primary": 2, "coverage": 1},
    "coverage": {
      "database": "pubmed", "matched": 49196, "denominator": 179297,
      "percent": 27.44, "basis": "Staphylococcus aureus[ORGN] in pubmed"
    },
    "urls": ["https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?..."],
    "query_translation": "(\"staphylococcus aureus\"[MeSH Terms] OR ...) AND ...",
    "result_count": 49196,
    "elapsed_ms": 863
  }
}
```

Four things worth reading in that block.

**`query_translation`** is the one to watch. **E-utilities silently rewrites
search terms** — it repairs unbalanced brackets and drops field names it does
not recognize, without warning either way. A search for `foo[NOSUCHFIELD]` runs
as a free-text search for `foo` and returns confident, wrong results. The
translation is the only place that shows up.

**`sources`** attributes the result to the databases that produced it, with the
utilities used and, when a tool draws on more than one database, each one's
share. If nobody measured how the records split, the share is **omitted rather
than guessed** — a fabricated 50/50 is worse than no number.

**`request_cost`** is the real number of NCBI requests this one result spent,
split by what they were for. Coverage lookups are counted here even though they
contribute no data, because execution cost is scored and hiding a request would
understate it. (This is why `utilities_called` can exceed the calls listed under
`sources`: the former counts every request, the latter only the ones that
produced data.)

### `coverage` — the denominator

A bare count is unreadable on its own. Measured 2026-09-16:
`Staphylococcus aureus[ORGN] AND MRSA` returns **16,360** BioSamples, which
sounds decisive until you know there are **230,466** S. aureus BioSamples. It is
7%, and the missing 93% are not absent — they are differently worded. An agent
shown only the numerator reports it as the population.

`basis` names exactly what the denominator counts, and it is not decoration:
"12.6% of S. aureus BioSamples" and "12.6% of all BioSamples" are different
claims, and only the basis distinguishes them. Two flavors:

| Flavor | Denominator | Cost |
|---|---|---|
| search | the same query with its organism filter left and everything else stripped | one extra request |
| fetch | how many UIDs you asked for | free |

Stripping can leave the query that was just run — an organism-only Pathogen
Detection count is its own denominator. That case reuses the number instead of
re-requesting it, so the coverage block is free and reads 100%.

The fetch flavor earns its keep: esummary drops UIDs it cannot resolve **without
comment**, so 48-of-50 and 50-of-50 look identical. Coverage reports
`shortfall: 2` and the notes name the UIDs.

Set `NCBI_COVERAGE=0` to switch it off if the shared IP gets tight. Database
totals come from einfo and are cached for the life of the process, so they cost
one request per database ever.

## Tools

**Discovery** — `ncbi_list_databases`, `ncbi_describe_database`

**SRA** — `ncbi_sra_search` (typed organism/strategy/platform/layout parameters,
so no Entrez syntax needed), `ncbi_sra_runs_for_project` (everything in a PRJNA
accession), `ncbi_sra_run_metadata`

**Other databases** — `ncbi_pubmed_search`, `ncbi_pubmed_abstracts`,
`ncbi_biosample_metadata`, `ncbi_bioproject_summary`, `ncbi_taxonomy_lookup`,
`ncbi_gene_info`, `ncbi_assembly_info`, `ncbi_sequence_fetch`

**Navigation** — `ncbi_find_uids` (accessions → the numeric UIDs most tools
need), `ncbi_linked_records` (cross-database links), `ncbi_entrez_raw` (escape
hatch for anything uncovered)

**Pathogen Detection** — `ncbi_pathogen_isolate_count`, `ncbi_pathogen_isolates`,
`ncbi_pathogen_amr_genes`, `ncbi_pathogen_organisms`

## Pathogen Detection: the right tool for resistance questions

Ask "how many methicillin-resistant *S. aureus* are there" through E-utilities
and you get a **floor, not a count**. BioSample can only match the free text a
submitter chose to write: `Staphylococcus aureus[ORGN] AND MRSA` returns 16,360
of 230,466 S. aureus BioSamples — 7% — and the missing 93% are not sensitive,
they are differently worded.

Pathogen Detection answers the same question from curated AMR genotype calls.
Measured 2026-09-16: **94,336 of 171,412** sequenced S. aureus isolates carry
`mecA` or `mecC` — **55.0%**, consistent with the 30–50% clinical prevalence
range the free-text number is nowhere near.

```
ncbi_pathogen_isolate_count(organism="Staphylococcus aureus", amr_genes="mecA,mecC")
→ 94,336 isolates · coverage 55.03% of all S. aureus isolates in Pathogen Detection
```

Three things to know before trusting a number from it.

**Filter values are matched literally, and a typo returns 0 rather than an
error** — indistinguishable from a real negative. `ncbi_pathogen_organisms` and
`ncbi_pathogen_amr_genes` return the exact vocabulary; the coverage
denominator is the other tell, since a denominator of 0 means the *organism*
was not recognized. Note the groupings are curated, not taxonomic: `"E.coli and
Shigella"` is one value.

**The index stores every isolate twice**, so `totalCount` is ~2x the isolate
count. Every count here comes from a `target_acc` facet's `numBuckets` instead,
which agrees exactly with the deduplicated FTP table; records are deduplicated
on the way out and the raw figure is only ever exposed under the name
`raw_row_count`.

**The endpoint is undocumented.** `pathogens-srv` is the backend the Isolates
Browser web UI calls, found in its JavaScript. NCBI's *published* programmatic
routes are BigQuery and FTP. It needs no auth and works today, but it is
unversioned with no deprecation policy and can change without notice. Every
behavior the code relies on was measured, because there is nothing to read.

## Why the code looks the way it does

Everything below was measured against the live API, not read out of the
documentation, and several items contradict what the documentation implies.
`databases.py` holds the per-database table.

- **Omitting `db` on esearch does not error.** It silently searches PubMed and
  returns plausible PubMed UIDs. `eutils.py` never sends a request without one.
- **HTTP 200 does not mean success.** Errors arrive in the body in four shapes:
  `esearchresult.ERROR`, `esummaryresult` (a *list*), a top-level `error`, and —
  invisible to all three — an `error` field inside an otherwise normal-looking
  per-UID record. The fourth reaches the agent as data unless it is split out.
- **Zero hits is not an error**, but it *does* populate `errorlist`. Treating a
  populated `errorlist` as failure reports "no data" as "the tool is broken".
- **efetch row order ≠ requested order** for SRA runinfo, so results are joined
  on the `Run` column. esummary's order *does* match, which makes this an easy
  mistake to make by analogy.
- **`efetch` is wrong for four databases.** It returns 34.6 MB for one gene, is
  unbounded for BioProject, and for Assembly and GEO it is not implemented but
  says so only by returning something that is not your data. Those tools use
  esummary and refuse efetch.
- **Taxonomy is the one database where efetch beats esummary.** The summary
  record has no lineage and no genetic code at all, so `ncbi_taxonomy_lookup`
  uses efetch. Two traps in its XML: `<LineageEx>` nests a full `<Taxon>` per
  ancestor (three requested taxa carry 49 `<TaxId>` elements), so walking every
  `Taxon` returns ancestors as results; and the common name arrives as
  `<GenbankCommonName>` for some taxa and `<CommonName>` for others.
- **elink omits `linksetdbs` entirely when there are no links** — not `[]` — so
  the obvious indexing raises `KeyError` on the most ordinary outcome there is.
  It also *merges* results from multiple source ids with no per-id attribution,
  so the tools never claim which input produced which link.
- **Every tool is `async def`**, enforced by a test. FastMCP runs sync tools on a
  worker thread, which would bypass the rate limiter entirely.
- **Pathogen Detection returns HTTP 200 for every failure**, including three
  that are not even JSON: an unknown `action` gives a zero-byte body,
  `limit=abc` gives a plain-text CGI `Status: 500`, and a malformed `sort` gives
  `ERROR: CJsonObject...`. Nothing branches on the status code.
- **Asking Pathogen Detection for a facet empties `content`** — any facet, even
  on an unrelated field, while `totalCount` stays correct. So rows and the
  echoed Solr query are mutually exclusive in one request, and record fetches
  report no `query_translation`. `count_params`/`record_params` make that a
  decision taken once rather than a landmine under every call site.
- **Both services share one rate limiter.** The 3/sec ceiling is per IP and
  NCBI-wide; two independently paced clients in one process would each believe
  they had the whole budget and together spend six.

## Testing

```sh
uv run pytest                     # whole repo; NCBI's 80 offline tests included
uv run pytest -k ncbi             # just this server
uv run pytest -m live             # 7 tests against the real API, opt-in
```

The tests live in the repo's `tests/` as `test_ncbi_*.py`, beside the mygene
suite. Offline tests run against real captured responses in
`tests/ncbi_fixtures/`, so they stay honest about what NCBI actually sends.
Live tests are deselected by default — at 3 requests/second a broad live suite
is slow and spends an allowance the whole room shares.

## Known gaps

- The 12 stripped SRA runinfo columns are all dbGaP/1000-Genomes
  controlled-access fields, measured empty on one open-access viral study. They
  should be expected to populate for controlled data, which this server cannot
  reach. If that ever changes, turn the stripping off.
- **The Pathogen Detection endpoint is unversioned and undocumented**, so the
  four `ncbi_pathogen_*` tools rest on behavior that could change without
  notice. If it breaks, NCBI's supported routes for the same data are BigQuery
  (`ncbi-pathogen-detect.pdbrowser.isolates`) and FTP. Whether the 2x row
  duplication is a bug or intentional is **unverified** — if it is ever fixed,
  the `numBuckets` counts stay correct but the 2x over-fetch in
  `record_params` becomes wasteful rather than wrong.
- **NCBI asks that `tool` and `email` be registered** by emailing
  `eutilities@ncbi.nlm.nih.gov` — sending them is not by itself compliance.
  Worth doing before demo day, since a block would hit the whole venue's IP.
- `NCBI_EMAIL` defaults to a personal address committed in this public repo. A
  project alias would be better.
