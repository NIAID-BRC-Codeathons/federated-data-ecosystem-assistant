"""Guards on the assumptions the rest of the code is built on.

These tests exist because each assertion below is load-bearing but invisible at
runtime: if one stops holding, nothing raises --- the server just quietly gets
slower, noisier, or wrong.
"""

import inspect

from ncbi_lib.server import server


# server.list_tools() returns the wire-protocol Tool, which carries no handle on
# the underlying function. The tool manager holds the registration objects, which
# do. It is private, so a future mcp release may move it --- that is a loud
# AttributeError here, which is the point of putting it in a test.
def _registered_tools():
    return server._tool_manager.list_tools()


async def test_every_tool_is_async():
    """THE invariant. See the module docstring in server.py.

    Measured on mcp 1.30.0: FastMCP awaits an async tool on the event loop and
    calls a sync one *directly on that same loop* (see the probe below). So a
    sync tool here could not await the shared rate limiter even if it wanted to,
    and its blocking I/O would stall every other in-flight call while it ran.
    Either way it bypasses the 3/sec per-IP ceiling that the whole room shares.
    """
    tools = _registered_tools()
    assert tools, "no tools registered"
    sync_tools = [t.name for t in tools if not inspect.iscoroutinefunction(t.fn)]
    assert sync_tools == [], (
        f"these tools are synchronous and will bypass the rate limiter: {sync_tools}"
    )


async def test_fastmcp_runs_sync_tools_on_the_event_loop():
    """The measurement the invariant above depends on.

    Note this is the opposite of jlowin's standalone fastmcp 4, which offloads
    sync tools to a worker thread. This server moved from that package to the
    SDK's vendored copy, so the reason for the invariant changed even though the
    invariant did not. If this ever starts returning "worker", revisit the
    rationale rather than assuming it still reads correctly.
    """
    import threading

    from mcp.server.fastmcp import FastMCP

    probe = FastMCP(name="probe")
    loop_thread = threading.get_ident()

    @probe.tool()
    def sync_probe() -> str:
        """probe"""
        return "same" if threading.get_ident() == loop_thread else "worker"

    content, _structured = await probe.call_tool("sync_probe", {})
    assert content[0].text == "same"


async def test_every_parameter_has_a_description():
    """Parameter descriptions are the agent's only basis for choosing between
    twenty similar-sounding tools. A missing one is invisible at runtime and
    silently degrades routing.

    Asserts on inputSchema --- the wire field the agent actually receives --- not
    on an internal attribute that may or may not exist.
    """
    undocumented = []
    for tool in await server.list_tools():
        properties = (tool.inputSchema or {}).get("properties", {})
        assert properties or not tool.inputSchema.get("required"), (
            f"{tool.name} declares required params but exposes no properties"
        )
        for name, schema in properties.items():
            if not schema.get("description"):
                undocumented.append(f"{tool.name}.{name}")
    assert undocumented == [], f"parameters with no description: {undocumented}"


async def test_every_tool_has_a_description():
    missing = [t.name for t in await server.list_tools() if not t.description]
    assert missing == []


async def test_tool_names_are_namespaced():
    """The assistant federates NCBI with the other servers in mcp_servers/;
    unprefixed names like `search` would collide and make routing ambiguous."""
    bad = [t.name for t in await server.list_tools() if not t.name.startswith("ncbi_")]
    assert bad == []


def test_no_dependency_on_httpx2():
    """This server used to be built on jlowin's fastmcp, which ships `httpx2`
    rather than `httpx`. It now installs from the repo's root pyproject.toml,
    where only `httpx` exists --- so a leftover `import httpx2` is a crash
    waiting for the first person who runs `uv sync` and starts this server.
    """
    import pathlib

    package = pathlib.Path(__file__).parent.parent / "mcp_servers" / "ncbi_lib"
    assert package.is_dir(), f"expected the package at {package}"
    offenders = [
        path.name for path in package.glob("*.py") if "import httpx2" in path.read_text()
    ]
    assert offenders == [], f"these modules import httpx2: {offenders}"
