"""Guards on the assumptions the rest of the code is built on.

These tests exist because each assertion below is load-bearing but invisible at
runtime: if one stops holding, nothing raises --- the server just quietly gets
slower, noisier, or wrong.
"""

import inspect

from ncbi_mcp.server import server


async def test_every_tool_is_async():
    """THE invariant. See the module docstring in server.py.

    FastMCP runs a sync tool on a worker thread, which would bypass the asyncio
    rate limiter entirely and blow NCBI's per-IP ceiling for everyone sharing
    the network.
    """
    tools = await server.list_tools()
    assert tools, "no tools registered"
    sync_tools = [
        t.name for t in tools if not inspect.iscoroutinefunction(t.fn)
    ]
    assert sync_tools == [], (
        f"these tools are synchronous and will bypass the rate limiter: "
        f"{sync_tools}"
    )


async def test_fastmcp_still_offloads_sync_tools_to_a_thread():
    """The measurement the invariant above depends on.

    If FastMCP ever starts running sync tools on the event loop, the invariant
    stops being necessary --- and this test failing is how we would find out,
    rather than assuming it forever.
    """
    import threading

    from fastmcp import FastMCP

    probe = FastMCP(name="probe")
    loop_thread = threading.get_ident()

    @probe.tool
    def sync_probe() -> str:
        """probe"""
        return "same" if threading.get_ident() == loop_thread else "worker"

    result = await probe.call_tool("sync_probe", {})
    assert result.content[0].text == "worker"


async def test_every_parameter_has_a_description():
    """Parameter descriptions are the agent's only basis for choosing between
    sixteen similar-sounding tools. A missing one is invisible at runtime and
    silently degrades routing."""
    undocumented = []
    for tool in await server.list_tools():
        for name, schema in (tool.parameters or {}).get("properties", {}).items():
            if not schema.get("description"):
                undocumented.append(f"{tool.name}.{name}")
    assert undocumented == [], f"parameters with no description: {undocumented}"


async def test_every_tool_has_a_description():
    missing = [t.name for t in await server.list_tools() if not t.description]
    assert missing == []


async def test_tool_names_are_namespaced():
    """The assistant federates NCBI with BV-BRC and PDN; unprefixed names like
    `search` would collide and make routing ambiguous."""
    bad = [t.name for t in await server.list_tools() if not t.name.startswith("ncbi_")]
    assert bad == []


def test_no_dependency_on_plain_httpx():
    """fastmcp ships httpx2, not httpx. `import httpx` fails in a clean install,
    so a stray `import httpx` anywhere in the package is a crash waiting for the
    first person who installs this from scratch."""
    import pathlib

    package = pathlib.Path(__file__).parent.parent / "ncbi_mcp"
    offenders = [
        path.name
        for path in package.glob("*.py")
        if "import httpx\n" in path.read_text()
    ]
    assert offenders == [], f"these modules import plain httpx: {offenders}"
