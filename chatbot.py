"""Chatbot with an agentic tool-call loop over MCP servers tools."""

import os

import chainlit as cl
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

SYSTEM_PROMPT = """You are a bioinformatics assistant with access to several databases.
Always use tools to retrieve real data, never invent accessions or sequences.
For multi-step questions, chain tools: search -> get entry -> get interactions.
"""

# Existing MCP servers or local ones running on localhost. The local servers are started by the `mcp_servers` scripts.
MCP_SERVERS = {
        "string": {
            "url": "https://mcp.string-db.org/",
            "transport": "streamable_http",
        },
        "expasy": {
            "url": "https://chat.expasy.org/mcp/",
            "transport": "streamable_http",
        },
        "brc-analytics": {
            "url": "https://brc-analytics.org/api/v1/mcp/",
            "transport": "streamable_http",
        },
        "pdn": {
            "url": "http://127.0.0.1:8001/mcp-pdn",
            "transport": "streamable_http",
        },
        "mygene": {
            "url": "http://127.0.0.1:8002/mcp-mygene",
            "transport": "streamable_http",
        },
        "uniprot": {
            "url": "http://127.0.0.1:8003/mcp-uniprot",
            "transport": "streamable_http",
        },
        "myvariant": {
            "url": "http://127.0.0.1:8004/mcp-myvariant",
            "transport": "streamable_http",
        },
        "ncbi": {
            "url": "http://127.0.0.1:8005/mcp-ncbi",
            "transport": "streamable_http",
        },
        "pubmed": {
            "url": "http://127.0.0.1:8006/mcp-pubmed",
            "transport": "streamable_http",
        },
        "geo": {
            # GEO is the only source registered here with processed gene
            # expression: what genes changed, under what treatment.
            "url": "http://127.0.0.1:8007/mcp-geo",
            "transport": "streamable_http",
        },
        "brc_analytics_local": {
            # Complements "brc-analytics" above, which is BRC's own public
            # server. This covers only what that server cannot do: ENA paging
            # past its hard 50-row cap and the real total, a working keyword
            # search (theirs answers HTTP 400), and study lookup (theirs 500s).
            "url": "http://127.0.0.1:8008/mcp-brc-analytics",
            "transport": "streamable_http",
        },
    }

LLM_MODEL="openrouter/google/gemma-4-26b-a4b-it"
# LLM_MODEL="openrouter/mistralai/mistral-small-2603"
# LLM_MODEL="cesnet/qwen3-coder"
# LLM_MODEL="ollama/gemma4"
# LLM_MODEL="mistralai/mistral-small-latest"
# LLM_MODEL="anthropic/claude-opus-5"

def load_chat_model(model: str) -> BaseChatModel:
    provider, model_name = model.split("/", maxsplit=1)
    if provider == "openrouter":
        return ChatOpenAI(
            model=model_name,
            base_url="https://openrouter.ai/api/v1",
            api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
            max_completion_tokens=2048,
        )
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model_name, temperature=0)
    if provider == "mistralai":
        from langchain_mistralai import ChatMistralAI
        return ChatMistralAI(model_name=model_name, temperature=0, max_tokens=2048)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # Claude Opus 5 thinks by default and rejects temperature, so leave it unset.
        # Thinking tokens count against max_tokens, so allow more than the other providers.
        return ChatAnthropic(model=model_name, max_tokens=16000)
    raise ValueError(f"Unknown provider: {provider}")


def _root_cause(exc: BaseException) -> str:
    """The innermost real error.

    A failed MCP connection surfaces as an ExceptionGroup wrapping a TaskGroup,
    whose str() is "unhandled errors in a TaskGroup" and says nothing about what
    went wrong. Unwrap it so the startup line names the actual cause.
    """
    seen = 0
    while seen < 10:
        inner = getattr(exc, "exceptions", None)
        if not inner:
            break
        exc = inner[0]
        seen += 1
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


async def load_tools(servers: dict) -> tuple[list, dict]:
    """Collect tools from every server, skipping the ones that are down.

    MultiServerMCPClient.get_tools() fans out across all servers and raises if
    any one of them is unreachable, which took the whole chat down at startup
    whenever a single local server was not running. Asking each server
    separately costs a missing one its tools and nothing else.
    """
    tools: list = []
    report: dict = {}
    for name, config in servers.items():
        try:
            server_tools = await MultiServerMCPClient({name: config}).get_tools()
        except Exception as exc:  # unreachable, refused, timed out, bad protocol
            report[name] = f"unavailable: {_root_cause(exc)}"
            continue
        tools.extend(server_tools)
        report[name] = f"{len(server_tools)} tools"
    return tools, report


async def init_agent():
    tools, report = await load_tools(MCP_SERVERS)
    for name, status in report.items():
        print(f"  {name:22s} {status}")
    if not tools:
        raise RuntimeError(
            "No MCP server answered. Start them with run_mcp_servers.py, or trim "
            "MCP_SERVERS to the ones you are running."
        )
    llm = load_chat_model(LLM_MODEL)
    return create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)


@cl.on_chat_start
async def on_chat_start():
    agent = await init_agent()
    cl.user_session.set("agent", agent)
    cl.user_session.set("chat_history", [])


@cl.on_message
async def on_message(message: cl.Message):
    """Handle each user message through the agentic tool loop."""
    agent = cl.user_session.get("agent")
    chat_history: list = cl.user_session.get("chat_history") or []
    if agent is None:
        await cl.Message(content="Agent not initialized.").send()
        return

    chat_history.append(HumanMessage(content=message.content))
    answer_msg = cl.Message(content="")
    pending_tool_calls: dict[str, dict] = {}
    current_tc_id: str | None = None

    async for chunk, __ in agent.astream(
        {"messages": chat_history}, stream_mode="messages"
    ):
        if isinstance(chunk, AIMessageChunk):
            # Anthropic streams content as a list of blocks; .text keeps only the text.
            if chunk.text:
                await answer_msg.stream_token(chunk.text)
            for tc_chunk in getattr(chunk, "tool_call_chunks", []) or []:
                tc_id = tc_chunk.get("id")
                if tc_id:
                    current_tc_id = tc_id
                    pending_tool_calls[tc_id] = {
                        "name": tc_chunk.get("name", "tool"),
                        "args": tc_chunk.get("args", "") or "",
                    }
                elif current_tc_id:
                    pending_tool_calls[current_tc_id]["args"] += tc_chunk.get("args", "") or ""
        elif isinstance(chunk, ToolMessage):
            tc_id = getattr(chunk, "tool_call_id", "")
            info = pending_tool_calls.get(tc_id, {})
            async with cl.Step(name=f"🛠 {info.get('name', 'tool')}") as s:
                s.input = info.get("args", "")
                s.output = str(chunk.content)[:800]
            answer_msg = cl.Message(content="")

    final_answer = answer_msg.content
    if final_answer:
        chat_history.append(AIMessage(content=final_answer))
    cl.user_session.set("chat_history", chat_history[-20:])
    await answer_msg.send()


@cl.set_starters
async def set_starters(user: cl.User | None = None, language: str | None = None):
    return [
        cl.Starter(
            label="UniProt disease variants BRCA1",
            message="What is the function of human BRCA1 and which diseases is it linked to?",
        ),
        cl.Starter(
            label="Rhea reactions ATP hydrolysis",
            message="Find reactions involving ATP hydrolysis in Rhea",
        ),
        cl.Starter(
            label="STRING interactions TP53",
            message="What are the interaction partners of TP53 with high confidence?",
        ),
        cl.Starter(
            label="GEO expression under ciprofloxacin",
            message=(
                "Which E. coli gene expression studies involve ciprofloxacin, and "
                "where are the actual expression values for the top one?"
            ),
        ),
        cl.Starter(
            label="BRC what can I run on E. coli",
            message=(
                "Which genome assemblies does BRC Analytics hold for Escherichia "
                "coli, and which analysis workflows can I run on them?"
            ),
        ),
        cl.Starter(
            label="Expression study to runnable workflow",
            message=(
                "Find an E. coli antibiotic resistance expression study in GEO, "
                "then tell me whether AMR Gene Detection can run on the E. coli "
                "reference genome."
            ),
        ),
    ]
