# NDE over streamable HTTP — a proposal for Bob

`nde_http.py` serves Bob Olson's `nde_mcp` package over streamable HTTP on port 8009,
so `chatbot.py` can reach it. It imports his package and does not modify, vendor or
patch a line of it. **Nothing here is committed to `NIAID-Data-Ecosystem/`.** It is
Bob's code; this directory only shows what a transport would look like, and the
decision is his.

## The gap

The whiteboard stars NDE as the entry point: the broad metadata catalogue that tells
you which datastore actually holds the payload. Bob's server exposes it as 9 tools and
it is merged into main.

`chatbot.py` cannot call any of them. Every entry in its `MCP_SERVERS` is
`transport: "streamable_http"` (`chatbot.py:72-123`), and `nde_mcp.server.main()` calls
`mcp.run()` with no argument, which is stdio (`NIAID-Data-Ecosystem/src/nde_mcp/server.py:715-717`).
There is no NDE entry in `MCP_SERVERS` at all. So the Discovery hop — ask NDE what
exists, then follow the record to NCBI or BRC for the data — is unreachable in the demo.

Nothing else blocks it. `load_tools` already asks each server separately
(`chatbot.py:205-223`), so an NDE process that is not running costs its own tools and
nothing else.

## Run it

```
uv run proposals/nde-http-transport/nde_http.py --port 8009      # HTTP, default
uv run proposals/nde-http-transport/nde_http.py --stdio          # stdio, unchanged
NDE_API_URL=https://api-staging.data.niaid.nih.gov/v1 \
    uv run proposals/nde-http-transport/nde_http.py --port 8010  # staging
```

No install, no second venv, no change to `pyproject.toml` or `uv.lock`. Bob's package
needs only `mcp` and `httpx`, both already pinned here, so putting his `src/` on
`sys.path` is enough. Verified against the repo's pins: mcp 1.30.0, httpx 0.28.1,
langchain-mcp-adapters 0.3.2, Python 3.13.15.

## Two registrations Bob would add

```python
# chatbot.py, in MCP_SERVERS
"nde": {
    # The board's starred entry point: broad metadata catalogue over ~14M
    # federated records, used to find which datastore holds the payload.
    "url": "http://127.0.0.1:8009/mcp-nde",
    "transport": "streamable_http",
},
```

```python
# run_mcp_servers.py, in SERVERS
"nde.py",            # 8009
```

If the file is landed as `mcp_servers/nde.py` it needs no edit: it walks up for
`NIAID-Data-Ecosystem/src` rather than counting parent directories, so it works from
either location. `SERVERS.md` would get the 8009 row.

## What was observed

All output below is real, taken 17 Sep 2026 on the laptop.

**Bob's own smoke test, unchanged, as a baseline** (in-process; it does not exercise a
transport):

```
$ python scripts/smoke_test.py
Testing 15 cases against https://api.data.niaid.nih.gov/v1
...
15/15 passed
```

**`initialize` + `tools/list` over HTTP**, raw JSON-RPC with
`Accept: application/json, text/event-stream`:

```
HTTP 200 | mcp-session-id: 462807bc43d24c779608c81ffaf5460a
serverInfo: {"name": "NDE MCP", "version": "1.30.0"}
tools/list -> 9 tools
  nde_facet_counts         Count records grouped by a field. Use for "how many", "which", and
  nde_get_record           Fetch the complete metadata for one record by its NDE id.
  nde_list_fields          List the indexed metadata fields available for querying and faceting.
  nde_list_repositories    List the 55 federated repositories with record counts and freshness.
  nde_lookup_ids           Resolve many identifiers to records in a single batch call.
  nde_raw_query            Run an arbitrary Lucene query against the NDE API.
  nde_search_datasets      Find biomedical datasets across ~14M federated records. Use this first
  nde_search_tools         Find bioinformatics software and workflows. Use when the user asks what
  nde_semantic_search      Find datasets by meaning rather than keyword. Use when the request is
```

**`nde_raw_query` for the E. coli baseline**, over HTTP (0.08 s):

```
=== tools/call nde_raw_query {"q": "infectiousAgent.name:\"escherichia coli\"", "size": 0} ===
{
  "total": 60107,
  "returned": 0,
  "offset": 0,
  "query": "infectiousAgent.name:\"escherichia coli\"",
  "api_call": "https://api.data.niaid.nih.gov/v1/query?q=infectiousAgent.name%3A%22escherichia+coli%22&size=0&_source=...",
  "results": [],
  "next_offset": 0,
  "nde_host": "https://api.data.niaid.nih.gov/v1"
}
```

**60,107**, matching the figure the home chat measured independently.

**All 9 tools called over the HTTP transport**, each response parsed and checked for the
host field:

```
ok   nde_search_datasets      nde_host=https://api.data.niaid.nih.gov/v1  keys=['total', 'returned']
ok   nde_semantic_search      nde_host=https://api.data.niaid.nih.gov/v1  keys=['total', 'returned']
ok   nde_search_tools         nde_host=https://api.data.niaid.nih.gov/v1  keys=['total', 'returned']
ok   nde_get_record           nde_host=https://api.data.niaid.nih.gov/v1  keys=['record']
ok   nde_lookup_ids           nde_host=https://api.data.niaid.nih.gov/v1  keys=['matched']
ok   nde_facet_counts         nde_host=https://api.data.niaid.nih.gov/v1  keys=['facets']
ok   nde_list_repositories    nde_host=https://api.data.niaid.nih.gov/v1  keys=['repository_count']
ok   nde_list_fields          nde_host=https://api.data.niaid.nih.gov/v1  keys=['fields', 'returned']
ok   nde_raw_query            nde_host=https://api.data.niaid.nih.gov/v1  keys=['total', 'returned']

9/9 tools carried nde_host over HTTP
```

**Loaded the way `chatbot.py` actually loads it**, through
`MultiServerMCPClient({...}).get_tools()`:

```
[HTTP 8009] get_tools -> 9 tools in 0.08s
[HTTP 8009] nde_raw_query x3: ['0.13s', '0.07s', '0.07s'] (mean 0.09s)
[stdio]     get_tools -> 9 tools in 0.75s
[stdio]     nde_raw_query x3: ['0.86s', '0.92s', '0.89s'] (mean 0.89s)
```

## Production and staging are different corpora

`NDE_API_URL` picks the host, once, when the client is built — so one host per running
process. The two hosts disagree, and no response says which one answered. Measured
through this wrapper on 17 Sep:

| | production | staging |
|---|---|---|
| repositories | 55 | 72 |
| harvested records | 20,553,828 | 90,596,509 |
| `includedInDataCatalog.name:"Bacterial and Viral Bioinformatics Resource Center"` | **0** | **118,625** |
| `infectiousAgent.name:"escherichia coli"` | 60,107 | 267,278 |

BV-BRC records exist only on staging. The trap is that production still *lists* BV-BRC
in `nde_list_repositories`, with a `record_count` of 118,625 — so the catalogue entry
looks healthy while every query against it returns a clean, well-formed zero. That zero
is indistinguishable from "no such data", and an agent will report it as such.

This is why the wrapper stamps `nde_host` on every response. Six of Bob's nine tools
already carry the host inside the `api_call` URL; `nde_get_record`, `nde_lookup_ids`,
`nde_list_fields` and `nde_list_repositories` do not — and `nde_list_repositories` is
precisely the tool the trap runs through. To serve both corpora, run the file twice on
two ports with different `NDE_API_URL` values.

One correction to what was passed to me: IEDB matched no repository on either host,
under "IEDB", "Immune" or "Epitope". Whatever route reaches IEDB, it is not a federated
NDE repository name, and I could not verify the claim that IEDB records are staging-only.

## The alternative: a stdio entry, no new file

`langchain-mcp-adapters` 0.3.2 does support stdio. `StdioConnection` is a declared
transport in `langchain_mcp_adapters/sessions.py:83-127` with `command`, `args`, `env`
and `cwd`, and `_create_stdio_session` is dispatched at `sessions.py:457`. It is not
theoretical — it was run:

```python
# chatbot.py MCP_SERVERS, with `import sys, pathlib` added at the top
"nde": {
    "transport": "stdio",
    "command": sys.executable,
    "args": ["-m", "nde_mcp.server"],
    "cwd": str(pathlib.Path(__file__).parent / "NIAID-Data-Ecosystem" / "src"),
    # "env": {"NDE_API_URL": "https://api-staging.data.niaid.nih.gov/v1"},
},
```

It returns the same 9 tools and the same 60,107. **The HTTP wrapper is the cleaner
choice**, for four reasons:

1. **Cost per call.** The adapter opens a session per invocation, so stdio spawns a
   fresh Python process for every tool call: 0.89 s mean against 0.09 s over HTTP,
   measured above. Roughly ten times, on every hop of a multi-step query.
2. **It matches the other nine servers.** Every existing entry is `streamable_http` and
   every local server is started by `run_mcp_servers.py`. A stdio entry is the only one
   of its kind, started differently, debugged differently, and invisible to the port
   check people use to see what is up.
3. **The environment does not reach the child.** A stdio child does not inherit the
   shell's `.env`, so `NDE_API_URL` has to be duplicated into the entry's `env` block —
   a second place for the production/staging switch to be wrong. The HTTP server reads
   it once, at launch, where it is visible.
4. **A dead process is visible.** `load_tools` reports `unavailable: ...` for an HTTP
   server that is not running. A stdio entry that fails to spawn fails inside the same
   call path, per call.

The stdio entry's one real advantage is that it adds no file, which matters if the
objection to this proposal is file count rather than transport. It is a fair fallback;
it is not the better default.

## What was not tested

- **The chatbot end to end.** `chatbot.py` needs an LLM, and Argo is denied on this
  laptop. Tool loading was verified through the same `MultiServerMCPClient` call
  `load_tools` makes, which is the part this proposal affects; the agent loop above it
  was not run.
- **Two instances at once under load.** Production on 8009 and staging on 8010 were run
  together and both answered, but only with sequential probes.
- **Bob's `scripts/smoke_test.py` over the transport.** It imports the tool functions
  and calls them in-process (`from nde_mcp import server as S`), so it passes 15/15
  whatever transport is configured. The 9-tool HTTP check above is what actually
  exercises the wrapper.

## A note on the startup check

The wrapper compares its re-exported tool list against what Bob's own server advertises
— names, descriptions and input schemas — and refuses to start on any difference. That
check was itself checked, because a guard that has never failed is not evidence:

```
--- healthy ---   exported 9 tools
--- drop-one ---  RuntimeError (guard fired): Re-export mismatch. Bob's server advertises [...9...]; this wrapper advertises [...8...].
--- drop-all ---  RuntimeError (guard fired): No nde_* tool functions found in nde_mcp.server. The package layout changed; do not start, the export would be empty.
```

So if Bob adds a tenth tool, this file stops rather than serving nine of ten in silence.
