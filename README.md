# Federated NIAID–BRC Data Ecosystem Assistant

**NIAID-BRCs AI Codeathon 2.0** · September 16–18, 2026 · Argonne National Laboratory

One research question, one coordinated plan spanning BV-BRC, BRC Analytics, PDN, the NIAID Data Ecosystem, NCBI resources, and other participating repositories.

Project page: https://niaid-brc-codeathons.github.io/projects/federated-data-ecosystem-assistant/

---

> **This is a draft pitch, not a plan.**
>
> What follows is a one-slide proposal from the organizing team. It exists
> to seed a team, not to constrain one. Scope, methods, target organism,
> and success criteria are all still open — expect them to change
> substantially. Turning this into a real plan is the team's first job, and
> it lands in the project charter due August 28, 2026.

---

## Goal (proposed)

Allow a user to ask one research question and receive a coordinated plan spanning BV-BRC, BRC Analytics, PDN, the NIAID Data Ecosystem, NCBI resources, and other participating repositories.

## Three-Day MVP (proposed)

Register a limited set of MCP-enabled tools from at least three resources. Demonstrate five end-to-end questions, such as finding relevant datasets, retrieving pathogen genomes, identifying available workflows, launching an analysis, and returning a provenance-linked result.

The agent should expose its resource-selection rationale, generated queries, API calls, and intermediate outputs rather than acting as an opaque chatbot.

## Evaluation (proposed)

Ten canonical questions scored for correct resource routing, tool-call success, result relevance, provenance, execution cost, and recovery from failed calls.

## Leads

- Bob Olson
- Panayiotis Smeros

Team assignments are still being finalized. Participants can review their project, and request a reassignment, in the participant spreadsheet circulated by the organizing team.

## Working here

This repository is the team's working space for the codeathon — code, notebooks, data pointers, and notes. Replace this README with the real thing once the charter is written. Team members get access through the [NIAID-BRC-Codeathons](https://github.com/NIAID-BRC-Codeathons) organization; accept the invitation if you have not already.

---

# NCBI MCP server

The first of the "at least three resources" in the MVP. Exposes NCBI
E-utilities — SRA, BioSample, BioProject, PubMed, Taxonomy, Gene, Assembly, and
sequence databases — as 16 MCP tools, all prefixed `ncbi_` so the assistant can
tell them apart from BV-BRC and PDN tools when routing.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest          # 40 offline tests
```

Then copy the `mcpServers` block from `mcp.json.example` into your client's MCP
config, filling in the absolute path and your email address.

No NCBI account is required. The server paces itself at NCBI's keyless limit of
3 requests/second.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `NCBI_EMAIL` | `jonathangunti@gmail.com` | Who NCBI contacts about this software. Set it to your own. |
| `NCBI_API_KEY` | unset | Optional. Raises the rate limit to 10/sec. [Free here](https://account.ncbi.nlm.nih.gov/settings/). |
| `NCBI_MAX_RPS` | `3` (or `10` with a key) | Lowers the rate. Can only lower it. |

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
  "summary": "891 SRA runs in PRJNA257197 (showing 100).",
  "data": [ ... ],
  "truncated": true,
  "next": {"max_results": 891},
  "provenance": {
    "source": "NCBI E-utilities",
    "urls": ["https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?..."],
    "query_translation": "PRJNA257197[All Fields]",
    "result_count": 891,
    "elapsed_ms": 412
  }
}
```

`query_translation` is the one to watch. **E-utilities silently rewrites search
terms** — it repairs unbalanced brackets and drops field names it does not
recognize, without warning either way. A search for `foo[NOSUCHFIELD]` runs as a
free-text search for `foo` and returns confident, wrong results. The translation
is the only place that shows up.

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

## Why the code looks the way it does

Everything below was measured against the live API, not read out of the
documentation, and several items contradict what the documentation implies.
`ncbi_mcp/databases.py` holds the per-database table.

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
- **elink omits `linksetdbs` entirely when there are no links** — not `[]` — so
  the obvious indexing raises `KeyError` on the most ordinary outcome there is.
  It also *merges* results from multiple source ids with no per-id attribution,
  so the tools never claim which input produced which link.
- **Every tool is `async def`**, enforced by a test. FastMCP runs sync tools on a
  worker thread, which would bypass the rate limiter entirely.

## Testing

```sh
.venv/bin/python -m pytest          # 40 offline tests, no network
.venv/bin/python -m pytest -m live  # 7 tests against the real API
```

Offline tests run against real captured responses in `tests/fixtures/`, so they
stay honest about what NCBI actually sends. Live tests are opt-in and
deliberately few — at 3 requests/second a broad live suite is slow and spends an
allowance the whole room shares.

## Known gaps

- Built on `fastmcp` 4 (the standalone package). Note this is **not**
  `mcp.server.fastmcp`, the module vendored inside the official `mcp` SDK — that
  one is a tombstone in `mcp` 2.x and raises on import. Same name, different
  project. Most tutorials online mean the old one.
- The 12 stripped SRA runinfo columns are all dbGaP/1000-Genomes
  controlled-access fields, measured empty on one open-access viral study. They
  should be expected to populate for controlled data, which this server cannot
  reach. If that ever changes, turn the stripping off.
- **NCBI asks that `tool` and `email` be registered** by emailing
  `eutilities@ncbi.nlm.nih.gov` — sending them is not by itself compliance.
  Worth doing before demo day, since a block would hit the whole venue's IP.
- `NCBI_EMAIL` defaults to a personal address committed in this public repo. A
  project alias would be better.
