"""Drive the real chatbot agent through the demo questions and record every step.

This is the missing piece of evidence. The offline suite proves each tool is
right; `run_eval.py` proves the tools beat the documented call. Neither says
anything about the *system*: whether the model picks the right tool, chains them
in the right order, or writes an answer a person would accept. This does.

It uses exactly what `chatbot.py` uses -- `init_agent()`, the same MCP servers,
the same SYSTEM_PROMPT -- so what it records is what a user gets. Run it with
several models and it writes a side-by-side comparison as well.

    uv run evals/run_questions.py                          # LLM_MODEL from .env, all 15
    uv run evals/run_questions.py --only 3 7               # a subset, by Q number
    uv run evals/run_questions.py --model argo/gpt4o argo/claudesonnet45 argo/claudehaiku45
    uv run evals/run_questions.py --dry-run                # list questions, call nothing

Prerequisites:
  * the local servers are up:  uv run run_mcp_servers.py
  * `.env` holds the credential for each provider you name, e.g.
        LLM_MODEL=argo/claudesonnet45
        ARGO_USER=<your Argonne username>
    Argo only resolves on the Argonne network; off it, every call hangs.

Output:
  evals/runs/<model>/qNN.jsonl          every message the agent produced: tool calls
                                        with arguments, tool results (truncated), answer
  evals/runs/<model>/routing-scorecard.md  one row per question, tools in call order
  evals/model-comparison.md             one row per question, one column per model,
                                        plus per-model totals -- only with --model

Argo returns HTTP 200 with "ACCESS DENIED" as the assistant's *content* when the
username is not authorised -- there is no auth error to catch. A model whose
first reply says that is marked denied and skipped for the rest of the run,
rather than recording fifteen nonsense answers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
# The Windows console is cp1252; transcripts and tables are UTF-8 files, but the
# progress lines go to stdout and must not crash on a non-ASCII answer excerpt.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(REPO))

import os  # noqa: E402
from dotenv import load_dotenv  # a Chainlit dependency, already installed

# chatbot.py never calls load_dotenv itself; Chainlit does it on import from the
# working directory. This driver is not Chainlit, so it has to do the same.
load_dotenv(REPO / ".env")

# An eval run is unattended by definition, so it must never open a window. On
# 17 Sep the matrix launched, printed "Opening browser for BV-BRC login...",
# raised a tab on the operator's laptop and then stopped forever on model 1 of
# 36 -- the OAuth callback had no timeout, so it waited for a human who was not
# there. chatbot.py now honours MCP_NO_BROWSER by raising instead of opening,
# which load_tools() reports as an unavailable server while the other twelve
# load normally. Losing 18 BV-BRC tools is a degradation; a silent overnight
# hang is an outage, and it looks identical to a long-running job in the log.
#
# Set before `import chatbot`, because chatbot reads it at import time.
os.environ.setdefault("MCP_NO_BROWSER", "1")

import chatbot  # noqa: E402
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage  # noqa: E402

QUESTIONS_MD = REPO / "evals" / "QUESTIONS.md"   # overridden by --questions
RUNS = REPO / "evals" / "runs"
# Kept only as the default for callers that do not pass a tag. Every path that
# runs concurrently threads the tag through explicitly instead, because a module
# global cannot be shared by two models running at once without one of them
# writing its transcripts into the other's directory.
RUN_TAG = ""


def run_tag_for(prompt: str, rep: int) -> str:
    """The suffix that keeps one run's output out of another's directory.

    The questions-file stem is part of it: without that, a routing run and the
    base matrix share a directory and the second silently overwrites the first.
    """
    stem = QUESTIONS_MD.stem.lower()
    return ((f"-{stem}" if stem != "questions" else "")
            + (f"-{prompt}" if prompt != "paper" else "")
            + (f"-r{rep}" if rep else ""))
COMPARISON = REPO / "evals" / "model-comparison.md"
REGISTRY = REPO / "evals" / "RUNS.md"
# Filled once conditions are computed, then stamped onto every question record.
# A run id on the run only says what the run was; on each question it also says
# whether the tree changed underneath one -- which is a fault we have hit twice.
_RUN_ID = ["unregistered"]
_CODE_SHA = ["unknown"]

# `## Q7. "Which E. coli ..." — NCBI ↔ BRC`  ->  (7, 'Which E. coli ...')
# `## Q7. "Which E. coli ..." -- NCBI <-> BRC`  ->  ('Q7', 'Which E. coli ...')
# The letter is part of the id, not stripped: the base matrix numbers questions Q1..,
# adversary's routing set R1.., the stress set S1.., the expert-judge set E1... They
# share a driver and must never share an output filename.
_Q = re.compile(r'^## ([A-Z]{1,2}\d+)\.\s+"(.+?)"')

DENIAL = "ACCESS DENIED"

# Reference list prices, USD per 1M tokens (input, output), for the "what would this
# cost outside Argonne" column. Argo bills NONE of this -- it draws on Argonne's
# allocation -- so the column is a comparison aid, not a bill. Approximate, from
# public price pages as REMEMBERED on 17 Sep 2026, not read off them on the day.
#
# READ THIS BEFORE QUOTING ANY DOLLAR FIGURE. Tokens are MEASURED; dollars are a
# CONVERSION nobody has audited. PRICES_VERIFIED is what the comparison table
# prints its caveat from, so the caveat cannot be forgotten: check the prices,
# flip the flag, and it goes away on its own.
#
# 36 aliases are reachable on Argo and only these carry a price, so most models get
# no cost column. A missing price is recorded in words rather than left as a bare
# null, because a null meaning "we never looked" is otherwise indistinguishable
# from a null meaning "this was free".
LIST_PRICE_PER_M = {
    "gpt4o": (2.50, 10.00),
    "gpt41": (2.00, 8.00), "gpt41mini": (0.40, 1.60), "gpt41nano": (0.10, 0.40),
    "gpto3": (2.00, 8.00), "gpto3mini": (1.10, 4.40), "gpto4mini": (1.10, 4.40),
    "claudesonnet45": (3.00, 15.00), "claudehaiku45": (1.00, 5.00),
    "claudeopus45": (15.00, 75.00), "claudeopus41": (15.00, 75.00),
}
PRICES_VERIFIED = False
PRICE_AS_OF = "17 Sep 2026, from memory -- NOT read off a price page"

# Two faults that look like model behaviour in a transcript but are not.
#
# A SILENT EMPTY is zero output tokens with no error and no denial. Measured 17 Sep:
# argo/claudesonnet45 q01 billed 33,028 input tokens and returned nothing in 2.7s
# while q02-q04 of the same run were normal.
#
# A DENIAL is Argo answering HTTP 200 with "ACCESS DENIED" as the assistant's
# content. It reads like an entitlement problem and is not: Argo billed ac.ni 44,015
# input tokens on claudeopus5 turn 1 and denied turn 2 of the SAME question, and an
# unauthorised user cannot be billed. Nor is it context size -- claudesonnet45
# succeeded at 69,461 input tokens and was denied at 42,091.
#
# So both get retried, and every discarded attempt is KEPT. A retry that hides what
# it retried would erase the transport-fault rate, and that rate is a finding in its
# own right: as of the 14:05 run, Argo denied 2 of 3 Claude models and silently
# emptied 1 of 15 questions. Those numbers must survive the fix for them.
MAX_EMPTY_RETRIES = 2
MAX_DENIAL_RETRIES = 1
DENIAL_STREAK_TO_ABANDON = 3

# Stop retrying silent empties for a model that has never recovered from one.
#
# Measured 17 Sep, argo/claudesonnet45 over the 15-question set: 6 questions came
# back silent-empty and **0 of 6 recovered** across two retries each at a 2s/4s
# backoff. Each attempt still billed its full input, so every unrecovered cell
# cost about 113,000 input tokens for zero output.
#
# It is not question content -- argo/claudeopus5 answered the identical Q1 with 13
# tool calls -- and it is not context size: the same model earlier answered fine at
# 100,864, 170,257 and 224,806 input tokens while failing at 33,028. So retrying is
# the right default for a fault whose rate is unknown, and the wrong one once a
# model has shown it does not recover.
#
# The breaker keeps the first few retries, because that is the evidence that the
# fault does not recover, and then stops spending on the same answer. Every cell is
# still recorded as a fault, so the RATE is unaffected -- only the bill is.
EMPTY_FAILURES_BEFORE_GIVING_UP = 3

# Backoff between retries, seconds, indexed by attempt number. Longer than the
# original 2s/4s because 2s/4s recovered nothing, and a transport that needs more
# than eight seconds is worth distinguishing from one that is simply broken.
EMPTY_BACKOFF = (4.0, 10.0)


def is_silent_empty(rec: dict) -> bool:
    """The model returned nothing and gave no reason for it."""
    return (not rec.get("error") and not rec.get("denied")
            and rec.get("output_tokens", 0) == 0
            and rec.get("tool_call_count", 0) == 0
            and not (rec.get("answer") or "").strip())


def fault_kind(rec: dict) -> str | None:
    """Name the transport fault in a record, or None if it is a real result."""
    if is_silent_empty(rec):
        return "silent_empty"
    if rec.get("denied"):
        return "denied"
    return None


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return "unknown"


def run_conditions(args, models: list[str], questions: list[tuple[str, str]]) -> dict:
    """Everything needed to say what this run was and tell it from the next one.

    The git SHA plus a dirty flag is the part that matters. An uncommitted change is
    the commonest reason two runs that look identical disagree, and without the flag
    there is no way to see it afterwards. Credential NAMES are recorded so a run made
    without NCBI_API_KEY is explicable later; values never are.
    """
    sha = _git("rev-parse", "--short", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    return {
        "run_id": f"{time.strftime('%Y%m%d-%H%M%S')}-{sha}{'-dirty' if dirty else ''}",
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_sha": sha,
        "git_dirty": dirty,
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "driver_sha256_8": hashlib.sha256(
            pathlib.Path(__file__).read_bytes()).hexdigest()[:8],
        "models": models,
        "questions_file": QUESTIONS_MD.relative_to(REPO).as_posix(),
        "question_ids": [n for n, _ in questions],
        "prompt_variants": list(args.prompt),
        "repeats": args.repeat,
        # NAMES only, never values -- and a name is listed only when its value is
        # non-empty. `.env` carried NCBI_API_KEY= with nothing after it on 17 Sep,
        # so a presence test alone would have recorded the key as available for
        # every run made without one. Present-but-empty is the failure this whole
        # project keeps meeting; it must not be reintroduced by the record of it.
        "env_names_present": sorted(
            k for k in ("LLM_MODEL", "ARGO_USER", "ARGO_BASE_URL", "ANTHROPIC_API_KEY",
                        "ANTHROPIC_BASE_URL", "OPENROUTER_API_KEY", "NCBI_API_KEY")
            if os.environ.get(k, "").strip()),
        "env_names_empty": sorted(
            k for k in ("LLM_MODEL", "ARGO_USER", "ARGO_BASE_URL", "ANTHROPIC_API_KEY",
                        "ANTHROPIC_BASE_URL", "OPENROUTER_API_KEY", "NCBI_API_KEY")
            if k in os.environ and not os.environ.get(k, "").strip()),
        "servers": {n: c.get("url", "") for n, c in chatbot.MCP_SERVERS.items()},
        "prices_verified": PRICES_VERIFIED,
        "note": args.note or "",
    }


def append_registry(cond: dict, by_model: dict) -> None:
    """One row per run in evals/RUNS.md, appended, newest at the bottom.

    Status is deliberately NOT computed. Every run lands as `exploratory` and a human
    marks the one being quoted `final`, because which run is final is a decision, not
    a measurement.
    """
    if not REGISTRY.exists():
        REGISTRY.write_text(
            "# Run registry\n\n"
            "Every run of `evals/run_questions.py`, appended automatically.\n\n"
            "A run is identified by its start time and the git SHA of the code that\n"
            "produced it. `-dirty` in a run id means the tree had uncommitted changes,\n"
            "which is the usual reason two otherwise identical runs disagree.\n\n"
            "**Status is set by hand.** A run lands as `exploratory`. Mark one `final`\n"
            "only when its numbers are the ones being quoted, and mark a superseded run\n"
            "`void` with a reason rather than deleting it -- a void run is still the\n"
            "evidence for why it was voided.\n\n"
            "`empty` and `denied` count TRANSPORT FAULTS including attempts that were\n"
            "retried away. They are not model results, and a run with a high count in\n"
            "either column must not be compared against one without.\n\n"
            "| run id | started | branch | questions file | models | qs | prompts | reps "
            "| rows | denied | empty | errors | status | note |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8")
    every = [r for v in by_model.values() for r in v]
    # Count the faults that happened, not the ones that survived retry.
    denied = sum(1 for r in every if r.get("denied")) + sum(
        1 for r in every for a in r.get("attempts_discarded", [])
        if a.get("fault") == "denied")
    empty = sum(1 for r in every if "silent empty after" in str(r.get("error") or "")) + sum(
        1 for r in every for a in r.get("attempts_discarded", [])
        if a.get("fault") == "silent_empty")
    with REGISTRY.open("a", encoding="utf-8") as f:
        f.write(f"| `{cond['run_id']}` | {cond['started']} | {cond['git_branch']} | "
                f"`{cond['questions_file']}` | {len(cond['models'])} | "
                f"{len(cond['question_ids'])} | {','.join(cond['prompt_variants'])} | "
                f"{cond['repeats']} | {len(every)} | {denied} | {empty} | "
                f"{sum(1 for r in every if r.get('error'))} | exploratory | "
                f"{cond['note'] or '--'} |\n")

# Prompt ablation. "paper" is whatever chatbot.py ships (the 57-line research-paper
# prompt on main); "minimal" is the three-line prompt the repo started with; "none"
# is no system prompt at all. Comparing the three per model measures what the
# prompt actually buys -- tool selection, refusal quality, answer shape -- rather
# than assuming it.
MINIMAL_PROMPT = """You are a bioinformatics assistant with access to several databases.
Always use tools to retrieve real data, never invent accessions or sequences.
For multi-step questions, chain tools: search -> get entry -> get interactions.
"""
PROMPTS = {"paper": None, "minimal": MINIMAL_PROMPT, "none": ""}
# How much of each tool result to keep in the transcript.
#
# 600 was too small by a wide margin and it was blinding the scorer. Measured
# over 366 real tool results: the MEDIAN is 1,621 characters, so 600 cut more
# than half of a typical result, and only 15% of results survived whole. The
# judge's retrieved_not_reported check needs to see a total and a returned count
# in the same result to decide anything, and 146 of 344 results were cut before
# that pair became legible -- so the check could only ever report "0 among the 5%
# of evidence I could see", which is not a finding.
#
# 4000 keeps 68% of results whole (8000 would keep 81%, at roughly double the
# transcript size). Raise it with --excerpt when a question set needs more; the
# cost is disk, and disk is cheaper than a blind scorer.
#
# Found by the judge chat, which reported it rather than patching -- this file is
# not theirs.
RESULT_EXCERPT = 4000


def load_questions() -> list[tuple[str, str]]:
    out = []
    for line in QUESTIONS_MD.read_text(encoding="utf-8").splitlines():
        m = _Q.match(line)
        if m:
            text = m.group(2).replace("*", "")   # markdown italics on species names
            out.append((m.group(1), text))
    if not out:
        print(f"WARNING: no questions parsed from {QUESTIONS_MD}. The heading must read "
              f'\'## Q1. "the question"\' with straight double quotes.', file=sys.stderr)
    return out


def _filename_id(qid: str) -> str:
    """Q1 -> q01, R12 -> r12, so a directory sorts and globs cleanly.

    The padding is not cosmetic. judge.py and analyze.py both find records with
    `glob("q*.jsonl")`, so an unpadded q2.jsonl and a padded q02.jsonl from an
    earlier run coexist in one directory and a single glob returns both --
    sorted() puts q02 first, so the STALE record wins. Found on 17 Sep by the
    runner chat after a filename change here did exactly that.
    """
    m = re.match(r"^([A-Za-z]*)(\d+)$", str(qid))
    if not m:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", str(qid)).lower()
    letters, digits = m.group(1).lower() or "q", int(m.group(2))
    return f"{letters}{digits:02d}"


def _safe(model: str, tag: str = "") -> str:
    """argo/claudesonnet45 -> argo_claudesonnet45, usable as a directory name.

    RUN_TAG carries the questions-file stem as well as the prompt and repeat, so a
    routing run cannot land in the same directory as the base matrix. Before this,
    running ROUTING.md against a model already in the matrix overwrote q01-q15.jsonl
    in place, silently -- found by adversary, 17 Sep, before it destroyed anything.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model) + (tag or RUN_TAG)


def _tool_calls(msg) -> list[dict]:
    return [
        {"tool": tc.get("name"), "args": tc.get("args")}
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]


def _text(msg) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    # Anthropic-style content blocks
    return "".join(
        b.get("text", "") if isinstance(b, dict) else str(b) for b in content
    )


async def run_one(agent, model: str, number: str, question: str,
                  tag: str = "") -> dict:
    """Stream the agent exactly the way chatbot.py does, and rebuild the transcript.

    Argo's Claude models answer HTTP 500 "Streaming is required for operations
    that may take longer than 10 minutes" to a non-streaming call, so ainvoke()
    fails on argo/claudesonnet45 while the Chainlit UI -- which streams -- works.
    Measured by laptop_system_improvement on 17 Sep. Streaming here keeps the
    model, the transport and the code path identical to what the demo ships.

    stream_mode="messages" yields AIMessageChunks and ToolMessages. Chunks are
    summed with `+`, which is how LangChain reassembles tool_call_chunks into
    tool_calls, so the accumulated chunk carries the full tool calls and text.
    """
    t0 = time.monotonic()
    steps: list[dict] = [{"role": "user", "text": question}]
    tools_in_order: list[str] = []
    answer = ""
    denied = False
    ai_acc = None
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    llm_round_trips = 0
    tool_result_chars = 0
    ttft: float | None = None          # time to the first token from the model
    tool_timings: list[dict] = []      # per tool call: name, seconds
    last_activity: float = t0          # when the model last emitted anything
    tool_seconds = 0.0
    finish_reasons: list = []   # one per LLM round trip, in order

    def flush_ai():
        nonlocal ai_acc, answer, denied, llm_round_trips
        if ai_acc is None:
            return
        llm_round_trips += 1
        um = getattr(ai_acc, "usage_metadata", None) or {}
        for k in usage:
            usage[k] += int(um.get(k) or 0)
        calls = _tool_calls(ai_acc)
        text = _text(ai_acc)
        if DENIAL in text.upper():
            denied = True
        # response_metadata is the ONLY thing that separates a refusal from a
        # truncation from a crash, and it was being thrown away on exactly the
        # records that needed it. An empty answer with finish_reason "stop" is a
        # different fault from one with "content_filter" or "length", and for a
        # whole afternoon we could not tell which we had -- two hypotheses were
        # built and killed on evidence this field would have settled in one call.
        # (Measured 17 Sep: the silent empties report "stop" with 0 output tokens,
        # never content_filter and never length, which is what finally ruled out
        # both a size threshold and a safety filter.)
        meta = dict(getattr(ai_acc, "response_metadata", None) or {})
        step = {"role": "assistant", "text": text, "tool_calls": calls,
                "usage": {k: int(um.get(k) or 0) for k in usage}}
        if meta:
            step["finish_reason"] = meta.get("finish_reason") or meta.get("stop_reason")
            step["model_name"] = meta.get("model_name") or meta.get("model")
            step["response_id"] = meta.get("id")
            # Keep the whole envelope too, minus anything bulky, so a question we
            # have not thought to ask yet is still answerable from the transcript.
            step["response_metadata"] = {
                k: v for k, v in meta.items()
                if k not in ("logprobs",) and len(str(v)) < 2000
            }
        finish_reasons.append(step.get("finish_reason"))
        steps.append(step)
        tools_in_order.extend(c["tool"] for c in calls if c["tool"])
        if text and not calls:
            answer = text          # the last text-only AI message is the answer
        ai_acc = None

    async for item in agent.astream({"messages": [HumanMessage(content=question)]},
                                    stream_mode="messages"):
        chunk = item[0] if isinstance(item, tuple) else item
        if isinstance(chunk, ToolMessage):
            flush_ai()
            now = time.monotonic()
            # last_activity is when the model last emitted a chunk, i.e. when it
            # finished asking for this tool (or when the previous tool result was
            # consumed). The gap to this result is the tool's wall time.
            secs = round(now - last_activity, 2)
            last_activity = now
            tool_seconds += secs
            raw = _text(chunk)
            name = getattr(chunk, "name", None)
            tool_timings.append({"tool": name, "seconds": secs, "result_chars": len(raw)})
            steps.append({
                "role": "tool",
                "tool": name,
                "seconds": secs,
                "result_excerpt": raw[:RESULT_EXCERPT],
                "result_chars": len(raw),
            })
        elif isinstance(chunk, AIMessageChunk):
            last_activity = time.monotonic()
            if ttft is None and (chunk.content or getattr(chunk, "tool_call_chunks", None)):
                ttft = round(last_activity - t0, 2)
            ai_acc = chunk if ai_acc is None else ai_acc + chunk
    flush_ai()
    elapsed = round(time.monotonic() - t0, 1)
    tool_result_chars = sum(st.get("result_chars", 0) for st in steps if st.get("role") == "tool")
    alias = model.split("/", 1)[-1]
    price = LIST_PRICE_PER_M.get(alias)
    list_cost_usd = (round(usage["input_tokens"] / 1e6 * price[0]
                           + usage["output_tokens"] / 1e6 * price[1], 4)
                     if price and usage["total_tokens"] else None)
    if price is None:
        list_cost_note = f"no list price on file for alias {alias!r} -- not computed"
    elif not usage["total_tokens"]:
        list_cost_note = "provider reported no usage -- cost not computed"
    else:
        list_cost_note = f"list-price estimate, UNVERIFIED ({PRICE_AS_OF}); Argo billed $0"

    record = {
        "question_number": number,
        "question_id": number,
        "question": question,
        "model": model,
        "elapsed_s": elapsed,
        "tools_in_order": tools_in_order,
        "tool_call_count": len(tools_in_order),
        "denied": denied,
        "error": None,
        "answer": answer,
        "answer_chars": len(answer),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "usage_reported": bool(usage["total_tokens"]),
        "ttft_s": ttft,
        "llm_round_trips": llm_round_trips,
        "tool_result_chars": tool_result_chars,
        "tool_seconds": round(tool_seconds, 2),
        "model_seconds": round(max(elapsed - tool_seconds, 0.0), 2),
        "tool_timings": tool_timings,
        # The finish reason of every round trip, in order. An empty answer whose
        # last reason is "stop" is a provider defect; "length" is truncation;
        # "content_filter" is a refusal. Without this they are indistinguishable.
        "finish_reasons": finish_reasons,
        "last_finish_reason": finish_reasons[-1] if finish_reasons else None,
        "list_cost_usd": list_cost_usd,
        "list_cost_note": list_cost_note,
        # Stamped per QUESTION, not only per run: the fault we have hit twice is a
        # tree that changed mid-run, and only a per-question stamp shows q04
        # carrying a different SHA from q01.
        "run_id": _RUN_ID[0],
        "code_sha": _CODE_SHA[0],
        "questions_file": QUESTIONS_MD.relative_to(REPO).as_posix(),
        "retries": 0,
        "attempts_discarded": [],
        "steps": steps,
    }
    out_dir = RUNS / _safe(model, tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_filename_id(number)}.jsonl"
    # The path goes ON the record so the retry loop can come back and correct
    # this file. run_one() writes the transcript on every attempt, so a retried
    # question is overwritten by its own final attempt, and any annotation the
    # caller adds afterwards lives only in memory unless it is written back.
    record["_jsonl_path"] = str(path)
    _write_transcript(path, steps, record)
    return record


def _summary_of(record: dict) -> dict:
    """The summary line as written: everything except the bulky internal fields."""
    return {k: v for k, v in record.items() if k not in ("steps", "_jsonl_path")}


def _write_transcript(path: pathlib.Path, steps: list[dict], record: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        for step in steps:
            f.write(json.dumps(step, ensure_ascii=False) + "\n")
        f.write(json.dumps({"summary": _summary_of(record)}, ensure_ascii=False) + "\n")


def rewrite_summary(record: dict) -> bool:
    """Replace the summary line of a record's transcript with the current one.

    Without this the per-question .jsonl disagrees with the scorecard about the
    same cell. run_one() writes the file; the retry loop then annotates the
    in-memory record with `retries`, `attempts_discarded` and the
    "silent empty after N retries" error. write_scorecard() and
    model-comparison.md are built from those in-memory records and were right,
    but the .jsonl kept the untouched final attempt: error null, retries 0,
    attempts_discarded empty.

    The .jsonl is what the scoring chats read, so judge was reading every
    silent-empty cell as a model that had chosen to answer nothing. A transport
    fault scored as a capability is the exact mistake the retry exists to
    prevent, so the fix for it must not reintroduce it one file over.

    Found by the runner chat, 17 Sep, from a live matrix cell.
    """
    raw = record.get("_jsonl_path")
    if not raw:
        return False
    path = pathlib.Path(raw)
    if not path.exists():
        return False
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if lines and '"summary"' in lines[-1]:
        lines = lines[:-1]
    lines.append(json.dumps({"summary": _summary_of(record)}, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _rel(path: pathlib.Path) -> str:
    """Repo-relative when it can be, absolute when it cannot.

    relative_to() RAISES rather than falling back, so a RUNS directory pointed
    outside the repo -- a test harness, a scratch run -- crashed the model at the
    very last line, after all its work was done and written. A progress message
    must never be able to lose a completed run.
    """
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _prefixed(model: str):
    """A print that names its model, because concurrent runs interleave.

    Without this, a parallel matrix produces a log in which no line can be
    attributed to the model that wrote it, which is worse than no log.
    """
    alias = model.split("/", 1)[-1][:18]

    def say(msg: str) -> None:
        print(f"[{alias:<18}] {msg}", flush=True)

    return say


def _error_record(model: str, number: str, question: str, exc: Exception) -> dict:
    return {"question_number": number, "question_id": number, "question": question, "model": model,
            "elapsed_s": 0, "tools_in_order": [], "tool_call_count": 0, "denied": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}", "answer": "", "answer_chars": 0,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "usage_reported": False,
            "ttft_s": None, "llm_round_trips": 0, "tool_result_chars": 0, "list_cost_usd": None,
            "tool_seconds": 0.0, "model_seconds": 0.0, "tool_timings": [],
            "finish_reasons": [], "last_finish_reason": None,
            "list_cost_note": "run failed before any usage was reported",
            "run_id": _RUN_ID[0], "code_sha": _CODE_SHA[0],
            "questions_file": QUESTIONS_MD.relative_to(REPO).as_posix(),
            "retries": 0, "attempts_discarded": []}


def write_scorecard(model: str, records: list[dict], tag: str = "") -> pathlib.Path:
    path = RUNS / _safe(model, tag) / "routing-scorecard.md"
    L = ["# Routing scorecard", "",
         f"Model: `{model}`. Generated by `evals/run_questions.py`; one row per question,",
         "tools in the order the agent called them. Compare each row with the expected chain",
         "in `QUESTIONS.md`. Full transcripts are the `qNN.jsonl` files beside this one.", "",
         "| Q | question | tools called, in order | calls | time | answer opens with |",
         "|---|---|---|---|---|---|"]
    for r in records:
        chain = " → ".join(f"`{t}`" for t in r["tools_in_order"]) or "*none*"
        opening = (r["answer"] or "").strip().replace("\n", " ")[:90]
        if r["denied"]:
            opening = "**ACCESS DENIED** (see docstring)"
        elif r["error"]:
            opening = f"**ERROR** {r['error'][:80]}"
        L.append(f"| {r['question_number']} | {r['question'][:70]} | {chain} | "
                 f"{r['tool_call_count']} | {r['elapsed_s']}s | {opening} |")
    L += ["", "## What to look for", "",
          "- A question that names a source and a row that never calls it.",
          "- A `search_ena_keywords` call: that federated tool returns an ENA 400 as text.",
          "- `geo_search` without `entry_type`: the count would mix four record types.",
          "- Any answer that quotes a number with no tool call behind it.",
          "- `denied`: an unauthorised Argo user gets HTTP 200 with ACCESS DENIED as content.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def write_comparison(by_model: dict[str, list[dict]], questions: list[tuple[int, str]]) -> None:
    models = list(by_model)
    L = ["# Model comparison", "",
         "Same 15 questions, same servers, same SYSTEM_PROMPT, one column per model.",
         "Generated by `evals/run_questions.py --model ...`. Each cell is",
         "`calls · seconds · first tool`; **denied** means Argo answered ACCESS DENIED,",
         "**error** means the run raised. Transcripts under `evals/runs/<model>/`.", "",
         "This table measures behaviour, not correctness: it says which tools each model",
         "reached for and how fast, not whether the answer was right. Judge correctness",
         "against the ground truths in `PIPELINES.md` by reading the transcripts.", "",
         "| Q | question | " + " | ".join(f"`{m}`" for m in models) + " |",
         "|---|---|" + "---|" * len(models)]
    index = {m: {r["question_number"]: r for r in recs} for m, recs in by_model.items()}
    for n, q in questions:
        cells = []
        for m in models:
            r = index[m].get(n)
            if r is None:
                cells.append("—")
            elif r["denied"]:
                cells.append("**denied**")
            elif r["error"]:
                cells.append("**error**")
            else:
                first = r["tools_in_order"][0] if r["tools_in_order"] else "no tool"
                tok = f" · {r['total_tokens']:,}t" if r.get("total_tokens") else ""
                cells.append(f"{r['tool_call_count']} · {r['elapsed_s']}s{tok} · `{first}`")
        L.append(f"| {n} | {q[:60]} | " + " | ".join(cells) + " |")

    L += ["", "## Per-model totals", "",
          "Tokens are what the provider reported on the stream (`usage_reported` says whether it did).",
          "`list $` is what the run would cost at public list price outside Argonne -- Argo bills none of it.", "",
          "| model | answered | denied | errors | mean calls | LLM trips | mean s | model s | tool s | mean TTFT s | in tok | out tok | usage | no-tool | mean chars | list $ |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        recs = by_model[m]
        ok = [r for r in recs if not r["denied"] and not r["error"]]
        n_ok = len(ok) or 1
        ttfts = [r["ttft_s"] for r in ok if r.get("ttft_s") is not None]
        costs = [r["list_cost_usd"] for r in ok if r.get("list_cost_usd") is not None]
        L.append(
            f"| `{m}` | {len(ok)}/{len(recs)} | {sum(r['denied'] for r in recs)} | "
            f"{sum(bool(r['error']) for r in recs)} | "
            f"{sum(r['tool_call_count'] for r in ok) / n_ok:.1f} | "
            f"{sum(r.get('llm_round_trips', 0) for r in ok) / n_ok:.1f} | "
            f"{sum(r['elapsed_s'] for r in ok) / n_ok:.1f} | "
            f"{sum(r.get('model_seconds', 0) for r in ok) / n_ok:.1f} | "
            f"{sum(r.get('tool_seconds', 0) for r in ok) / n_ok:.1f} | "
            f"{(sum(ttfts) / len(ttfts)) if ttfts else 0:.1f} | "
            f"{sum(r.get('input_tokens', 0) for r in ok):,} | "
            f"{sum(r.get('output_tokens', 0) for r in ok):,} | "
            f"{sum(1 for r in ok if r.get('usage_reported'))}/{len(ok)} | "
            f"{sum(1 for r in ok if not r['tools_in_order'])} | "
            f"{sum(r['answer_chars'] for r in ok) / n_ok:.0f} | "
            f"{('$' + format(sum(costs), '.2f')) if costs else '--'} |")
    L += ["", "**A no-tool answer to a data question is a fabrication until proven otherwise.**",
          "That column is the first one to read.", ""]
    COMPARISON.write_text("\n".join(L), encoding="utf-8")


async def run_model(model: str, questions: list[tuple[str, str]],
                    prompt: str = "paper", rep: int = 0,
                    tools: list | None = None) -> list[dict]:
    """Run one model over the question set. Safe to run concurrently with others.

    Nothing here mutates a module global. The model, the system prompt and the
    output tag are all parameters, and the agent is built from `tools` that were
    loaded once and are shared by every model in the run -- a tool is a handle on
    an HTTP endpoint, so sharing them costs nothing and saves 36 connection
    storms against 13 servers.
    """
    tag = run_tag_for(prompt, rep)
    system_prompt = (chatbot.SYSTEM_PROMPT if PROMPTS.get(prompt) is None
                     else PROMPTS[prompt])
    say = _prefixed(model)
    say("connecting ...")
    try:
        if tools is None:
            tools, report = await chatbot.load_tools(chatbot.MCP_SERVERS)
            for name, status in report.items():
                say(f"  {name:22s} {status}")
        if not tools:
            raise RuntimeError("no MCP server answered")
        agent = chatbot.create_agent(
            model=chatbot.load_chat_model(model),
            tools=tools,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        say(f"could not build the agent: {type(exc).__name__}: {str(exc)[:200]}")
        return [_error_record(model, n, q, exc) for n, q in questions]

    records: list[dict] = []
    denial_streak = 0
    empty_unrecovered = 0      # silent empties this model never came back from
    empty_recovered = 0        # ... and ones it did
    for n, q in questions:
        say(f"{n}: {q[:70]}")
        try:
            rec = await run_one(agent, model, n, q, tag)
            discarded: list[dict] = []
            attempts = 0
            # Once a model has failed to recover from several silent empties in a
            # row, retrying it again buys nothing and costs a full input charge
            # per attempt. Recording the fault is what matters; paying three times
            # to record it is not. A single recovery re-earns the retries, because
            # it proves the fault is transient for this model after all.
            retry_empties = (empty_recovered > 0
                             or empty_unrecovered < EMPTY_FAILURES_BEFORE_GIVING_UP)
            # Retry ONLY the two transport faults. A real error, or a genuine
            # no-tool answer, stands as measured -- those are results.
            while True:
                fault = fault_kind(rec)
                if fault == "silent_empty":
                    cap = MAX_EMPTY_RETRIES if retry_empties else 0
                else:
                    cap = MAX_DENIAL_RETRIES
                if fault is None or attempts >= cap:
                    break
                attempts += 1
                # Keep what we are throwing away, or the fault rate disappears
                # along with the fault.
                discarded.append({
                    "attempt": attempts, "fault": fault,
                    "input_tokens": rec.get("input_tokens", 0),
                    "output_tokens": rec.get("output_tokens", 0),
                    "elapsed_s": rec.get("elapsed_s", 0),
                    "answer_chars": rec.get("answer_chars", 0),
                })
                detail = (f"0 output tokens, no error, {rec.get('input_tokens', 0):,} "
                          f"input tokens billed" if fault == "silent_empty"
                          else "Argo returned ACCESS DENIED as content (intermittent)")
                say(f"  {fault.upper()} ({detail}) -- retry {attempts}/{cap}")
                await asyncio.sleep(EMPTY_BACKOFF[min(attempts - 1, len(EMPTY_BACKOFF) - 1)]
                                    if fault == "silent_empty" else 2.0 * attempts)
                rec = await run_one(agent, model, n, q, tag)
            rec["retries"] = attempts
            rec["attempts_discarded"] = discarded
            rec["retries_suppressed"] = not retry_empties
            if is_silent_empty(rec):
                empty_unrecovered += 1
                if not retry_empties:
                    # Say it every time. A silent empty that was never retried
                    # must not be mistaken later for one that was.
                    rec["error"] = (
                        f"silent empty, not retried: this model failed to recover "
                        f"from {EMPTY_FAILURES_BEFORE_GIVING_UP} silent empties in a "
                        f"row, so retries were suppressed to stop billing input "
                        f"tokens for an answer that does not arrive")
                    say(f"  SILENT EMPTY, retries suppressed "
                        f"({empty_unrecovered} unrecovered so far for this model)")
            elif attempts:
                empty_recovered += 1
            if attempts and is_silent_empty(rec):
                # Still empty after retries, so now it IS a finding. Say so in the
                # record; a blank answer must never be left to speak for itself.
                rec["error"] = (f"silent empty after {attempts} retries: provider "
                                f"returned 0 output tokens, no error, no denial")
                say(f"  still empty after {attempts} retries -- recorded as an error")
            elif attempts and not fault_kind(rec):
                say(f"  recovered on retry {attempts}")
            if attempts:
                # Push the annotation back into the transcript, or the file the
                # scoring chats read disagrees with the scorecard about the same
                # cell -- and the .jsonl is the one they read.
                if not rewrite_summary(rec):
                    say("  WARNING: could not rewrite the transcript summary; "
                        "this question's .jsonl understates its retries")
        except Exception as exc:  # one bad question must not lose the run
            say(f"  FAILED: {type(exc).__name__}: {str(exc)[:160]}")
            err = _error_record(model, n, q, exc)
            # Write a stub transcript for the failure too, or the run directory
            # and the scorecard disagree about what was even ATTEMPTED.
            #
            # run_one() writes the .jsonl from inside itself, so an exception
            # escapes before that write and the question leaves no file. Every
            # scorer globs the directory, so the denominator silently shrinks: a
            # model that crashed on four questions and answered twelve well
            # outscores one that answered all sixteen adequately, and nothing
            # anywhere says so. Measured 17 Sep -- argo/gemini25pro produced 16
            # ERROR rows in its scorecard and ONE transcript.
            #
            # Control, because a clean check that cannot fail is worth nothing:
            # 191 transcript summaries across the whole tree carried ZERO non-null
            # errors while the scorecards for those same runs carried 19. The
            # transcript error field had never once come back dirty.
            try:
                d = RUNS / _safe(model, tag)
                d.mkdir(parents=True, exist_ok=True)
                _write_transcript(
                    d / f"{_filename_id(n)}.jsonl",
                    [{"role": "user", "text": q},
                     {"role": "error", "exception": type(exc).__name__,
                      "message": str(exc)[:4000]}],
                    err,
                )
            except Exception as write_exc:   # never let bookkeeping lose the run
                say(f"  (could not write the failure stub: {write_exc})")
            records.append(err)
            continue
        records.append(rec)
        say(f"  {rec['tool_call_count']} tool call(s) in {rec['elapsed_s']}s: "
            + (" -> ".join(rec["tools_in_order"]) or "none"))

        # One denial costs one question. Only a streak means the model is really
        # shut, and only then is it right to stop spending on it.
        denial_streak = denial_streak + 1 if rec.get("denied") else 0
        if denial_streak >= DENIAL_STREAK_TO_ABANDON:
            say(f"  {denial_streak} denials in a row after retry -- treating this "
                f"model as unavailable and skipping its remaining questions.")
            for n2, q2 in questions[len(records):]:
                records.append({**_error_record(model, n2, q2,
                                                RuntimeError("skipped after denial streak")),
                                "denied": True, "error": None})
            break
    path = write_scorecard(model, records, tag)
    say(f"done -- wrote {_rel(path)}")
    return records


async def main() -> int:
    global RESULT_EXCERPT, QUESTIONS_MD
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*",
                    help="question ids to run, e.g. --only 3 7 or --only R1 R2")
    ap.add_argument("--model", nargs="*",
                    help="one or more LLM_MODEL values, e.g. argo/gpt4o argo/claudesonnet45; "
                         "default is LLM_MODEL from .env")
    ap.add_argument("--prompt", nargs="*", choices=list(PROMPTS), default=["paper"],
                    help="system-prompt variants to run: paper (as shipped), minimal, none")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each model/prompt N times to measure tool-selection variance")
    ap.add_argument("--questions", default=None,
                    help="question file to run, relative to evals/ or an absolute path "
                         "(default QUESTIONS.md). Its stem is added to the output "
                         "directory name, so sets cannot overwrite each other.")
    ap.add_argument("--excerpt", type=int, default=RESULT_EXCERPT, metavar="CHARS",
                    help=f"characters of each tool result to keep in the transcript "
                         f"(default {RESULT_EXCERPT}). The scorer reads this field, so "
                         f"too small a value makes checks unable to see their own "
                         f"evidence rather than able to report nothing found.")
    ap.add_argument("--parallel", type=int, default=1, metavar="N",
                    help="run N models concurrently (default 1). Wall time is ~94%% "
                         "model latency, and NCBI's 3 req/sec ceiling is enforced "
                         "inside the MCP servers, which are shared processes -- so "
                         "concurrent streams QUEUE at the limiter rather than "
                         "exceeding it. 6-8 is a sensible ceiling; beyond that the "
                         "tool calls queue long enough to distort the timing "
                         "columns, which are part of what we are measuring.")
    ap.add_argument("--note", default="",
                    help="what this run is testing; recorded in evals/RUNS.md")
    ap.add_argument("--dry-run", action="store_true", help="list questions and exit")
    args = ap.parse_args()

    if args.questions:
        cand = pathlib.Path(args.questions)
        # Resolve against several roots rather than one. `--questions ROUTING.md`
        # and `--questions evals/ROUTING.md` are both natural to type, and joining
        # every relative path to evals/ turned the second into evals/evals/ROUTING.md
        # and failed at launch. Found by runner after every queued job in
        # run-queue.md had been written with the repo-root spelling.
        if cand.is_absolute():
            QUESTIONS_MD = cand
        else:
            for root in (REPO / "evals", REPO, pathlib.Path.cwd()):
                if (root / cand).exists():
                    QUESTIONS_MD = root / cand
                    break
            else:
                QUESTIONS_MD = REPO / "evals" / cand
        if not QUESTIONS_MD.exists():
            print(f"no such questions file: {QUESTIONS_MD}", file=sys.stderr)
            return 2
    RESULT_EXCERPT = args.excerpt

    questions = load_questions()
    if args.only:
        want = {str(o).upper() for o in args.only}
        questions = [q for q in questions
                     if q[0].upper() in want or q[0].lstrip("A-Z").lstrip("QRSE") in want
                     or re.sub(r"^[A-Z]+", "", q[0]) in want]
    if not questions:
        print("no questions matched", file=sys.stderr)
        return 2
    models = args.model or [chatbot.LLM_MODEL]
    print(f"{len(questions)} question(s) from {QUESTIONS_MD.name} x "
          f"{len(models)} model(s): {', '.join(models)}")
    for n, q in questions:
        print(f"  {n:<4} {q[:88]}")
    if args.dry_run:
        return 0

    cond = run_conditions(args, models, questions)
    _RUN_ID[0] = cond["run_id"]
    _CODE_SHA[0] = cond["git_sha"] + ("-dirty" if cond["git_dirty"] else "")
    print(f"run id {cond['run_id']}"
          + ("   [TREE DIRTY -- uncommitted changes]" if cond["git_dirty"] else ""))
    if not PRICES_VERIFIED:
        print("   list prices are UNVERIFIED; tokens are measured, dollars are not")

    # Load the tools ONCE and share them. A tool is a handle on an HTTP endpoint,
    # so 36 models can hold the same list; loading per model would mean 36
    # connection storms against 13 servers for no benefit.
    shared_tools, report = await chatbot.load_tools(chatbot.MCP_SERVERS)
    for name, status in sorted(report.items()):
        print(f"  {name:22s} {status}")
    if not shared_tools:
        print("No MCP server answered. Start them with run_mcp_servers.py.",
              file=sys.stderr)
        return 2
    print(f"  {len(shared_tools)} tools from {sum(1 for v in report.values() if 'tools' in str(v))}"
          f"/{len(report)} servers")

    jobs = [(model, prompt, rep,
             model + ("" if prompt == "paper" else f" [{prompt}]")
                   + ("" if args.repeat == 1 else f" #{rep + 1}"))
            for model in models
            for prompt in args.prompt
            for rep in range(args.repeat)]

    by_model: dict[str, list[dict]] = {}
    lanes = max(1, args.parallel)
    if lanes > 1:
        print(f"\nrunning {len(jobs)} job(s), {lanes} at a time")
        print("  NCBI's 3 req/sec ceiling is enforced inside the MCP servers, which")
        print("  are shared processes, so these streams queue at the limiter rather")
        print("  than racing past it. Tool latency rises; the rate does not.\n")
        gate = asyncio.Semaphore(lanes)

        async def one(model, prompt, rep, label):
            async with gate:
                return label, await run_model(model, questions, prompt=prompt,
                                              rep=rep, tools=shared_tools)

        for label, recs in await asyncio.gather(*(one(*j) for j in jobs)):
            by_model[label] = recs
    else:
        for model, prompt, rep, label in jobs:
            by_model[label] = await run_model(model, questions, prompt=prompt,
                                              rep=rep, tools=shared_tools)

    for label in by_model:
        # The first prompt/rep is the right tag here: a label only ever carries a
        # non-default prompt or rep when there is exactly one of each in the run.
        d = RUNS / _safe(label.split(" ")[0],
                         run_tag_for(args.prompt[0], 0))
        d.mkdir(parents=True, exist_ok=True)
        (d / "run-manifest.json").write_text(
            json.dumps({**cond, "label": label}, indent=2), encoding="utf-8")
    append_registry(cond, by_model)
    if args.model:
        write_comparison(by_model, questions)
        print(f"\nwrote {COMPARISON.relative_to(REPO)}")
    print(f"registered in {REGISTRY.relative_to(REPO)} as {cond['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
