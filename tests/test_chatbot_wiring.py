"""Guard the wiring in chatbot.py that a merge can quietly undo.

Written after a real outage on 17 Sep 2026. A conflict resolution in chatbot.py
deleted the last two lines of init_agent() -- the ones that build the model and
return the agent. The function then fell off the end and returned None, so every
question died with "'NoneType' object has no attribute 'astream'" and the Chainlit
demo answered "Agent not initialized." to everything. Nothing caught it: the whole
offline suite passed, because the suite tests the SERVERS and nobody tested the
assembly.

That is the shape of fault these tests exist for. They are deliberately cheap and
offline -- no network, no MCP servers, no model call -- so they can run in CI on
every push and fail in under a second when a merge eats a line.

What is checked here:
  * init_agent() actually returns something, on a stubbed load_tools
  * it refuses to start when no server answered, instead of returning a
    tool-less agent that silently invents answers
  * every MCP_SERVERS entry is well formed, with a unique port
  * the demo's own ports match run_mcp_servers.py

Run just these:
    uv run pytest tests/test_chatbot_wiring.py -v
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# chatbot.py sits at the repo root, not in a package, so importing it needs the
# root on sys.path. Locally that happened by accident -- an editable install and
# running pytest from the root both supply it -- so these tests passed here and
# failed on a clean checkout with ModuleNotFoundError: No module named 'chatbot'.
#
# Caught by the first CI run this branch has ever had, on the very commit that
# made CI possible. A test that only passes on the machine that wrote it is the
# same class of fault as a check that cannot come back dirty.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# --------------------------------------------------------------------------
# init_agent must return an agent
# --------------------------------------------------------------------------

def test_init_agent_has_a_return_statement():
    """The regression test for the 17 Sep outage, read off the source tree.

    Parsing the AST rather than calling the function means this runs with no
    network, no servers and no model credential, which is what lets it sit in CI
    and fail the moment a merge deletes the return again.
    """
    src = (REPO / "chatbot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
               and n.name == "init_agent"), None)
    assert fn is not None, "chatbot.py no longer defines init_agent"

    returns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Return) and n.value is not None]
    assert returns, (
        "init_agent() has no `return <value>`. It therefore returns None, and "
        "every caller dies with \"'NoneType' object has no attribute 'astream'\" "
        "while the Chainlit UI answers 'Agent not initialized.' to every message. "
        "This is exactly what a merge did on 17 Sep 2026."
    )


def test_init_agent_returns_the_created_agent():
    """The return must be the agent, not some truthy placeholder."""
    src = (REPO / "chatbot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "init_agent")
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "create_agent" in calls, (
        "init_agent() never calls create_agent(). Whatever it returns is not an "
        "agent built from the loaded tools."
    )


@pytest.mark.asyncio
async def test_init_agent_refuses_to_start_with_no_tools(monkeypatch):
    """No server answered is a hard stop, not a tool-less agent.

    A tool-less agent is the worst possible failure here: it still answers, and
    every answer is invented. Better to refuse loudly.
    """
    import chatbot

    async def no_tools(_servers):
        return [], {"everything": "did not answer"}

    monkeypatch.setattr(chatbot, "load_tools", no_tools)
    with pytest.raises(RuntimeError, match="No MCP server answered"):
        await chatbot.init_agent()


@pytest.mark.asyncio
async def test_init_agent_builds_an_agent_from_stub_tools(monkeypatch):
    """The assembly itself, with the network stubbed out.

    This is the test that would have caught the 17 Sep outage by *behaviour*
    rather than by reading the source, so it is worth having alongside the AST
    check even though the two overlap.
    """
    import chatbot

    sentinel = object()

    async def one_tool(_servers):
        return [object()], {"stub": "1 tool"}

    monkeypatch.setattr(chatbot, "load_tools", one_tool)
    monkeypatch.setattr(chatbot, "load_chat_model", lambda _m: object())
    monkeypatch.setattr(chatbot, "create_agent",
                        lambda **_kw: sentinel)

    agent = await chatbot.init_agent()
    assert agent is sentinel, (
        "init_agent() did not return what create_agent() produced -- it returned "
        f"{agent!r}. If this is None, the return statement has gone missing again."
    )


# --------------------------------------------------------------------------
# the server table
# --------------------------------------------------------------------------

def _ports(servers: dict) -> dict[str, int]:
    out = {}
    for name, cfg in servers.items():
        m = re.search(r"://127\.0\.0\.1:(\d+)/", cfg.get("url", ""))
        if m:
            out[name] = int(m.group(1))
    return out


def test_every_server_entry_is_well_formed():
    import chatbot

    assert chatbot.MCP_SERVERS, "MCP_SERVERS is empty; the demo would load no tools"
    for name, cfg in chatbot.MCP_SERVERS.items():
        assert cfg.get("url"), f"{name} has no url"
        assert cfg.get("transport"), f"{name} has no transport"
        assert cfg["url"].startswith(("http://", "https://")), \
            f"{name} url is not http(s): {cfg['url']}"


def test_no_two_local_servers_share_a_port():
    """A port collision shows up as a server that silently serves the wrong tools.

    Three collisions happened in two days on this project, each caught only after
    it had landed. The failure is quiet: the second server to bind loses, and its
    tools are simply absent from the agent with no error anywhere.
    """
    import chatbot

    ports = _ports(chatbot.MCP_SERVERS)
    clashes = {}
    for name, port in ports.items():
        clashes.setdefault(port, []).append(name)
    duplicated = {p: n for p, n in clashes.items() if len(n) > 1}
    assert not duplicated, f"these servers share a port: {duplicated}"


def test_geo_and_brc_are_on_the_ports_the_launcher_starts():
    """The two servers this branch owns, checked against the launcher and source.

    GEO moved 8007 -> 8009 when NDE took 8007 in PR #12. A stale copy on the old
    port meant GEO's tools were absent from an entire eval matrix without one
    error being raised -- found on 17 Sep, after the run.
    """
    import chatbot

    ports = _ports(chatbot.MCP_SERVERS)
    assert ports.get("geo") == 8009, f"geo is on {ports.get('geo')}, expected 8009"
    assert ports.get("brc_analytics_local") == 8008, \
        f"brc_analytics_local is on {ports.get('brc_analytics_local')}, expected 8008"

    geo_src = (REPO / "mcp_servers" / "geo.py").read_text(encoding="utf-8")
    assert "8009" in geo_src and "port=8007" not in geo_src, \
        "mcp_servers/geo.py still mentions its old port 8007"

    launcher = (REPO / "run_mcp_servers.py").read_text(encoding="utf-8")
    for name in ("geo.py", "brc_analytics.py"):
        assert name in launcher, f"run_mcp_servers.py does not start {name}"


def test_launcher_loads_dotenv_so_children_inherit_the_api_key():
    """Popen children inherit the parent environment; `uv run` does not read .env.

    Only pubmed.py called load_dotenv() itself, so geo.py and ncbi_lib/eutils.py
    both read an empty NCBI_API_KEY and silently kept the anonymous 3 req/sec pace
    -- a ceiling that is per IP and shared with every laptop at the venue.
    """
    launcher = (REPO / "run_mcp_servers.py").read_text(encoding="utf-8")
    assert "load_dotenv" in launcher, (
        "run_mcp_servers.py no longer calls load_dotenv(), so spawned servers get "
        "no NCBI_API_KEY and fall back to 3 req/sec shared across the venue IP."
    )
    assert launcher.index("load_dotenv()") < launcher.index("subprocess.Popen"), \
        "load_dotenv() must run before the first Popen, or children miss the key"


# --------------------------------------------------------------------------
# nothing unattended may open a window, or wait forever for a human
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_browser_env_makes_the_oauth_redirect_refuse(monkeypatch):
    """MCP_NO_BROWSER must raise instead of opening a tab.

    On 17 Sep the eval matrix launched, called webbrowser.open() against an
    already-running Chrome -- which raises a tab in the existing window -- and
    then hung. An unattended job opening a window on someone's screen is a real
    cost, and one that cannot be undone by noticing afterwards.
    """
    import chatbot

    opened = []
    monkeypatch.setattr(chatbot.webbrowser, "open", lambda u: opened.append(u))
    monkeypatch.setattr(chatbot, "NO_BROWSER", True)

    with pytest.raises(RuntimeError, match="MCP_NO_BROWSER"):
        await chatbot._oauth_redirect_handler("https://example.invalid/auth")
    assert not opened, f"a browser was opened anyway: {opened}"


def test_the_eval_driver_sets_no_browser_before_importing_chatbot():
    """The driver must set MCP_NO_BROWSER, and set it EARLY.

    chatbot reads the variable at import time, so setting it after the import
    would leave the flag on while the module had already decided it was off --
    a fix that reads correctly and does nothing.
    """
    src = (REPO / "evals" / "run_questions.py").read_text(encoding="utf-8")
    assert "MCP_NO_BROWSER" in src, \
        "evals/run_questions.py no longer suppresses the browser"
    assert src.index("MCP_NO_BROWSER") < src.index("import chatbot"), \
        "MCP_NO_BROWSER is set after `import chatbot`, so it has no effect"


def test_the_oauth_callback_cannot_wait_forever():
    """A bare `await ready.wait()` is an overnight hang with no error.

    load_tools() already survives a server that will not connect: it reports it
    and loads the rest. The bug was that this never returned to be reported.
    """
    src = (REPO / "chatbot.py").read_text(encoding="utf-8")
    assert "asyncio.wait_for" in src and "OAUTH_LOGIN_TIMEOUT" in src, \
        "the OAuth callback wait is unbounded again"
    assert "await ready.wait()\n    return result" not in src, \
        "the unbounded `await ready.wait()` has come back"


def test_tokens_are_stored_with_a_time_anchor():
    """expires_in is a duration; on its own it cannot say how much life is left."""
    src = (REPO / "chatbot.py").read_text(encoding="utf-8")
    assert "obtained_at" in src, (
        "token storage no longer records when a token was issued, so a cached "
        "token's remaining life cannot be computed and re-auth fires at random."
    )


def test_question_filenames_are_zero_padded():
    """q2.jsonl and q02.jsonl in one directory make a glob return both.

    judge.py and analyze.py both use glob("q*.jsonl"), and sorted() puts q02
    before q2 -- so an unpadded new record loses to a padded stale one.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rq_pad", REPO / "evals" / "run_questions.py")
    rq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rq)

    assert rq._filename_id("Q2") == "q02"
    assert rq._filename_id("Q13") == "q13"
    assert rq._filename_id("R1") == "r01"
    assert rq._filename_id("S20") == "s20"
    # different sets must not collide
    assert len({rq._filename_id(q) for q in ("Q1", "R1", "S1", "E1")}) == 4
