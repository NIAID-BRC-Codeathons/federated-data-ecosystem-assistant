# Proposal: serve the NDE MCP server over HTTP

**Status: proposal, not landed. The NDE server is Bob Olson's; this touches none of it.**

## The gap

`NIAID-Data-Ecosystem/` is merged and works, but it speaks MCP over **stdio only**, and
every entry in `chatbot.py`'s `MCP_SERVERS` is `streamable_http`. So the whiteboard's
starred entry point — "broad metadata catalogue, queries other datastores" — is unreachable
from the chatbot, and the Discovery pipeline (P1 in `evals/PIPELINES.md`) is blocked.

## What this is

`nde_http.py` imports Bob's `nde_mcp` package **unchanged** and re-exports its 9 tools onto
an SDK `FastMCP` on port **8007** at `/mcp-nde`.

> **Port note.** This proposal originally specified **8009**, which was free when
> it was written and is now `mcp_servers/geo.py`. It has been corrected to
> **8007**, which is where the NDE server already runs — so this is a drop-in
> replacement for that process rather than a tenth port. Caught by the sentinel
> chat's port sweep, which widened from a `port =` pattern to every `80xx`
> literal in every `.py` after it found its narrower version blind to a server
> that declares its port through argparse. Two things it adds on the way through:

- every response carries `nde_host`, because `NDE_API_URL` selects production vs staging
  and only 6 of the 9 tools echo the host themselves — `nde_list_repositories`, where the
  BV-BRC trap lives, is one that does not. BV-BRC is **listed** on production with 118,625
  records and returns 0 for every query; it answers only on staging.
- a startup check compares tool names, descriptions and input schemas against what Bob's
  own server advertises and refuses to start on any difference. It was fed known-bad cases
  (one tool dropped, all dropped) and raised on both.

## Run it

```
uv run proposals/nde-http-transport/nde_http.py            # port 8007
NDE_API_URL=https://api-staging.data.niaid.nih.gov/v1 uv run proposals/nde-http-transport/nde_http.py
```

No new dependency: `nde_mcp` needs only `mcp` and `httpx`, both already pinned.

## Observed, 17 Sep 2026

```
initialize -> HTTP 200, serverInfo {"name": "NDE MCP", "version": "1.30.0"}
tools/list -> 9 tools: nde_facet_counts nde_get_record nde_list_fields
              nde_list_repositories nde_lookup_ids nde_raw_query
              nde_search_datasets nde_search_tools nde_semantic_search
nde_raw_query infectiousAgent.name:"escherichia coli" -> 60,107, nde_host = production
```

Loaded through `MultiServerMCPClient` exactly as `chatbot.py` does: 9 tools, ~0.09 s per
call. A `transport: "stdio"` entry in `MCP_SERVERS` also works with
`langchain-mcp-adapters` 0.3.2, but costs ~0.89 s per call because the adapter spawns a
process per invocation, and a stdio child does not see `.env`.

## What Bob would add, if he wants it

```python
# chatbot.py, MCP_SERVERS
"nde": {"url": "http://127.0.0.1:8007/mcp-nde", "transport": "streamable_http"},
```
```python
# run_mcp_servers.py, SERVERS
"../proposals/nde-http-transport/nde_http.py",   # 8007 -- or move it to mcp_servers/nde.py
```

The longer write-up — both transports measured, and a note on why a name search for
"IEDB" misses a catalog that is present on staging — was kept out of the repo on purpose.
