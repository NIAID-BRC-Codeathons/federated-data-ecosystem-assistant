"""NIAID Data Ecosystem (NDE) MCP server, served over streamable HTTP.

This file adds a transport. It does not add, remove or rewrite a single tool.

WHY IT EXISTS
-------------
NDE is the entry point the project whiteboard stars: a broad metadata catalogue
over ~14M records that points at the other datastores. Bob Olson's `nde_mcp`
package (NIAID-Data-Ecosystem/) exposes it as 9 MCP tools, and it is on main.

chatbot.py cannot reach any of them. Its `MultiServerMCPClient` entries in
MCP_SERVERS are all `streamable_http`, and `nde_mcp.server.main()` calls
`mcp.run()` with no transport, which is stdio. So the Discovery hop -- ask NDE
what exists, then follow the record to NCBI or BRC for the payload -- is not
reachable from the chatbot at all.

This wrapper imports Bob's package unchanged and re-exports its 9 tools on a
`FastMCP` instance configured for port 8009 at /mcp-nde, the same house pattern
as mcp_servers/geo.py and mcp_servers/brc_analytics.py. Bob's source is not
edited, imported-and-monkeypatched, or vendored. If his tools change, this file
picks the change up on the next restart; if he adds a tenth tool, the startup
check below fails loudly rather than exporting 9 of 10 in silence.

PRODUCTION vs STAGING -- READ THIS BEFORE TRUSTING A ZERO
---------------------------------------------------------
`NDE_API_URL` selects which NDE host answers, and it is read once, at client
construction, from the environment of THIS process:

    production (default)  https://api.data.niaid.nih.gov/v1
    staging               https://api-staging.data.niaid.nih.gov/v1

The two hosts are not the same corpus, and neither one says so in a response.
Measured 17 Sep 2026 through this wrapper:

    production  55 repositories,  20,553,828 harvested records
    staging     72 repositories,  90,596,509 harvested records

    q=includedInDataCatalog.name:"Bacterial and Viral
      Bioinformatics Resource Center"     production 0    staging 118,625
    q=infectiousAgent.name:"escherichia
      coli"                               production 60,107  staging 267,278

BV-BRC records therefore exist only on staging. Production still LISTS BV-BRC in
nde_list_repositories with a record_count of 118,625, so the catalogue entry is
no evidence that the records are searchable there -- the query returns a
well-formed response with zero hits and no warning, indistinguishable from "this
data does not exist". (IEDB is present on staging under its full catalog name, "Immune Epitope Database and Analysis Resource" -- 18,086 records on 17 Sep -- and absent on production. A name search for "IEDB" misses it because that string is not in the term, and "Immune"/"Epitope" missed too because nde_list_repositories matches on /v1/metadata source keys, not on the includedInDataCatalog.name facet terms. That is a limit of the tool worth knowing.) Two consequences:

  * One host per running process. To serve both, run this file twice on two
    ports with different `NDE_API_URL` values.
  * Every tool response from this wrapper carries an `nde_host` field naming
    the base URL that actually answered, so a zero -- or a count that differs
    4x between hosts -- can be read correctly. Six of Bob's nine tools already
    embed the host inside an `api_call` URL; the other four (nde_get_record,
    nde_lookup_ids, nde_list_repositories, nde_list_fields) do not, and
    nde_list_repositories is exactly where the production/staging difference
    shows up. `nde_host` is added to the JSON object a tool returns, only when
    the tool did not already set it. Nothing else about the response is touched.

RUN
---
    HTTP :   uv run proposals/nde-http-transport/nde_http.py --port 8009
    stdio:   uv run proposals/nde-http-transport/nde_http.py --stdio
    staging: NDE_API_URL=https://api-staging.data.niaid.nih.gov/v1 \
                 uv run proposals/nde-http-transport/nde_http.py --port 8009

Then in chatbot.py MCP_SERVERS:

    "nde": {"url": "http://127.0.0.1:8009/mcp-nde", "transport": "streamable_http"},

See README.md in this directory for the observed output and the alternative
(a `transport: "stdio"` entry, which also works and needs no new file).
"""

import asyncio
import functools
import inspect
import json
import pathlib
import sys
from typing import Any, Callable

# Bob's package is a separate distribution under NIAID-Data-Ecosystem/ and is not
# installed into the root venv. Its only runtime dependencies are mcp and httpx,
# both already pinned in this repo's pyproject.toml, so putting its src/ on the
# path is enough -- no install, no second venv, no change to uv.lock. Walking up
# for the directory rather than counting parents keeps this working if the file
# is landed as mcp_servers/nde.py.


def _nde_src() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "NIAID-Data-Ecosystem" / "src"
        if (candidate / "nde_mcp" / "server.py").is_file():
            return candidate
    raise RuntimeError(
        "Could not find NIAID-Data-Ecosystem/src above "
        f"{here}. This wrapper must live inside the repo checkout that "
        "contains Bob's nde_mcp package."
    )


sys.path.insert(0, str(_nde_src()))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from nde_mcp import server as nde  # noqa: E402  -- Bob's module, unmodified

HOST_FIELD = "nde_host"

mcp = FastMCP(
    name="NDE MCP",
    dependencies=["mcp", "httpx"],
    instructions=(
        (nde.mcp.instructions or "")
        + "\n\nEvery response carries an `nde_host` field naming the NDE API host "
        "that answered. BV-BRC and IEDB records exist only on the staging host "
        "(api-staging.data.niaid.nih.gov); on production a query scoped to either "
        "returns zero hits with no warning. Check `nde_host` before reporting that "
        "something does not exist."
    ),
    port=8009,
    streamable_http_path="/mcp-nde",
)


def _with_host(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Return an async passthrough that stamps the answering host onto the result.

    Bob's tools are `async def` and every one of them returns the string from
    `_as_json(<dict>)`, so the result parses back to a JSON object. If it ever
    does not, the original string is returned untouched: a transport wrapper
    must never be the reason a tool response is unreadable.

    `functools.wraps` carries the signature, docstring and annotations over, and
    FastMCP builds the input schema from `inspect.signature`, which follows
    `__wrapped__`. The re-exported schemas are therefore Bob's schemas; the
    startup check below asserts that rather than assuming it.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        result = await fn(*args, **kwargs)
        if not isinstance(result, str):
            return result
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            return result
        if not isinstance(payload, dict) or HOST_FIELD in payload:
            return result
        payload[HOST_FIELD] = nde.get_client().base_url
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)

    return wrapper


def _bob_tool_functions() -> dict[str, Callable[..., Any]]:
    """The public `nde_*` coroutines in Bob's server module.

    `@mcp.tool()` returns the function unchanged, so the module-level names are
    the tools themselves. Private helpers (`_run_search`, `_diagnose_empty`) are
    async too and are excluded by the leading underscore.
    """
    return {
        name: obj
        for name, obj in inspect.getmembers(nde, inspect.iscoroutinefunction)
        if name.startswith("nde_")
    }


def _register() -> list[str]:
    """Re-export Bob's tools onto this server, then prove nothing was lost.

    An exporter that silently ships zero tools looks exactly like a healthy one
    from the outside, so this compares the re-exported tool list against what
    Bob's own server advertises -- names, descriptions and input schemas -- and
    refuses to start on any difference.
    """
    functions = _bob_tool_functions()
    if not functions:
        raise RuntimeError(
            "No nde_* tool functions found in nde_mcp.server. The package layout "
            "changed; do not start, the export would be empty."
        )

    for name, fn in sorted(functions.items()):
        mcp.tool(name=name, annotations=nde.READ_ONLY)(_with_host(fn))

    theirs = {t.name: t for t in asyncio.run(nde.mcp.list_tools())}
    ours = {t.name: t for t in asyncio.run(mcp.list_tools())}
    if set(theirs) != set(ours):
        raise RuntimeError(
            "Re-export mismatch. Bob's server advertises "
            f"{sorted(theirs)}; this wrapper advertises {sorted(ours)}."
        )
    drifted = [
        name
        for name, tool in theirs.items()
        if tool.description != ours[name].description
        or tool.inputSchema != ours[name].inputSchema
    ]
    if drifted:
        raise RuntimeError(
            f"Re-exported tools differ from Bob's in description or input schema: {drifted}"
        )
    return sorted(ours)


TOOL_NAMES = _register()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NDE MCP server over streamable HTTP")
    parser.add_argument("--stdio", action="store_true", help="run over stdio")
    parser.add_argument("--port", type=int, default=8009, help="HTTP port")
    args = parser.parse_args()

    host = nde.get_client().base_url
    if args.stdio:
        print(f"NDE MCP Server (stdio), {len(TOOL_NAMES)} tools, host {host}", file=sys.stderr)
        mcp.run(transport="stdio")
    else:
        mcp.settings.port = args.port
        print(
            f"NDE MCP Server starting on http://localhost:{args.port}/mcp-nde ... "
            f"{len(TOOL_NAMES)} tools, host {host}"
        )
        mcp.run(transport="streamable-http")
