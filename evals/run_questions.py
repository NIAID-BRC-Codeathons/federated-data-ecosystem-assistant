"""Drive the real chatbot agent through the demo questions and record every step.

This is the missing piece of evidence. The offline suite proves each tool is
right; `run_eval.py` proves the tools beat the documented call. Neither says
anything about the *system*: whether the model picks the right tool, chains them
in the right order, or writes an answer a person would accept. This does.

It uses exactly what `chatbot.py` uses -- `init_agent()`, the same MCP servers,
the same SYSTEM_PROMPT, the same model -- so what it records is what a user gets.

    uv run evals/run_questions.py                # all 15 questions
    uv run evals/run_questions.py --only 3 7     # a subset, by Q number
    uv run evals/run_questions.py --dry-run      # list the questions, call nothing

Prerequisites:
  * the local servers are up:  uv run run_mcp_servers.py
  * `.env` holds the model and its credential, e.g.
        LLM_MODEL=argo/claudesonnet45
        ARGO_USER=<your Argonne username>
    Argo only resolves on the Argonne network; off it, every call hangs.

Output, per question:
  evals/runs/qNN.jsonl      every message the agent produced -- tool calls with
                            their arguments, tool results (truncated), the answer
  evals/routing-scorecard.md  one row per question: tools observed, in order

Argo returns HTTP 200 with "ACCESS DENIED" as the assistant's *content* when the
username is not authorised -- there is no auth error to catch. The run stops on
the first such reply rather than recording fifteen nonsense answers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # a Chainlit dependency, already installed

# chatbot.py never calls load_dotenv itself; Chainlit does it on import from the
# working directory. This driver is not Chainlit, so it has to do the same.
load_dotenv(REPO / ".env")

import chatbot  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

QUESTIONS_MD = REPO / "evals" / "QUESTIONS.md"
RUNS = REPO / "evals" / "runs"
SCORECARD = REPO / "evals" / "routing-scorecard.md"

# `## Q7. "Which E. coli ..." — NCBI ↔ BRC`  ->  (7, 'Which E. coli ...')
_Q = re.compile(r'^## Q(\d+)\.\s+"(.+?)"')

DENIAL = "ACCESS DENIED"
RESULT_EXCERPT = 600


def load_questions() -> list[tuple[int, str]]:
    out = []
    for line in QUESTIONS_MD.read_text(encoding="utf-8").splitlines():
        m = _Q.match(line)
        if m:
            text = m.group(2).replace("*", "")   # markdown italics on species names
            out.append((int(m.group(1)), text))
    return out


def _tool_calls(msg: AIMessage) -> list[dict]:
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


async def run_one(agent, number: int, question: str) -> dict:
    t0 = time.monotonic()
    result = await agent.ainvoke({"messages": [HumanMessage(content=question)]})
    elapsed = round(time.monotonic() - t0, 1)

    steps: list[dict] = []
    tools_in_order: list[str] = []
    answer = ""
    denied = False

    for msg in result["messages"]:
        if isinstance(msg, HumanMessage):
            steps.append({"role": "user", "text": _text(msg)})
        elif isinstance(msg, AIMessage):
            calls = _tool_calls(msg)
            text = _text(msg)
            if DENIAL in text.upper():
                denied = True
            steps.append({"role": "assistant", "text": text, "tool_calls": calls})
            tools_in_order.extend(c["tool"] for c in calls if c["tool"])
            if text and not calls:
                answer = text          # the last text-only AI message is the answer
        elif isinstance(msg, ToolMessage):
            raw = _text(msg)
            steps.append({
                "role": "tool",
                "tool": getattr(msg, "name", None),
                "result_excerpt": raw[:RESULT_EXCERPT],
                "result_chars": len(raw),
            })

    record = {
        "question_number": number,
        "question": question,
        "model": chatbot.LLM_MODEL,
        "elapsed_s": elapsed,
        "tools_in_order": tools_in_order,
        "tool_call_count": len(tools_in_order),
        "denied": denied,
        "answer": answer,
        "steps": steps,
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"q{number:02d}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for step in steps:
            f.write(json.dumps(step, ensure_ascii=False) + "\n")
        f.write(json.dumps({"summary": {k: v for k, v in record.items() if k != "steps"}},
                           ensure_ascii=False) + "\n")
    return record


def write_scorecard(records: list[dict]) -> None:
    L = ["# Routing scorecard", "",
         f"Model: `{chatbot.LLM_MODEL}`. Generated by `evals/run_questions.py`; one row per",
         "question, tools in the order the agent called them. Compare each row with the",
         "expected chain in `QUESTIONS.md`. Full transcripts are in `evals/runs/qNN.jsonl`.", "",
         "| Q | question | tools called, in order | calls | time | answer opens with |",
         "|---|---|---|---|---|---|"]
    for r in records:
        chain = " → ".join(f"`{t}`" for t in r["tools_in_order"]) or "*none*"
        opening = (r["answer"] or "").strip().replace("\n", " ")[:90]
        if r["denied"]:
            opening = "**ACCESS DENIED** (see docstring)"
        L.append(f"| {r['question_number']} | {r['question'][:70]} | {chain} | "
                 f"{r['tool_call_count']} | {r['elapsed_s']}s | {opening} |")
    L += ["", "## What to look for", "",
          "- A question that names a source and a row that never calls it.",
          "- A `search_ena_keywords` call: that federated tool returns an ENA 400 as text.",
          "- `geo_search` without `entry_type`: the count would mix four record types.",
          "- Any answer that quotes a number with no tool call behind it.",
          "- `denied`: an unauthorised Argo user gets HTTP 200 with ACCESS DENIED as content.", ""]
    SCORECARD.write_text("\n".join(L), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", type=int, nargs="*", help="question numbers to run")
    ap.add_argument("--dry-run", action="store_true", help="list questions and exit")
    args = ap.parse_args()

    questions = load_questions()
    if args.only:
        questions = [q for q in questions if q[0] in set(args.only)]
    if not questions:
        print("no questions matched", file=sys.stderr)
        return 2
    print(f"{len(questions)} question(s); model {chatbot.LLM_MODEL}")
    for n, q in questions:
        print(f"  Q{n:02d}  {q[:88]}")
    if args.dry_run:
        return 0

    print("\nconnecting to MCP servers ...")
    agent = await chatbot.init_agent()

    records = []
    for n, q in questions:
        print(f"\n--- Q{n:02d}: {q[:80]}")
        try:
            rec = await run_one(agent, n, q)
        except Exception as exc:  # one bad question must not lose the run
            print(f"    FAILED: {type(exc).__name__}: {str(exc)[:200]}")
            records.append({"question_number": n, "question": q, "model": chatbot.LLM_MODEL,
                            "elapsed_s": 0, "tools_in_order": [], "tool_call_count": 0,
                            "denied": False, "answer": f"ERROR {type(exc).__name__}: {exc}"})
            continue
        records.append(rec)
        print(f"    {rec['tool_call_count']} tool call(s) in {rec['elapsed_s']}s: "
              + (" → ".join(rec["tools_in_order"]) or "none"))
        if rec["denied"]:
            print("\nArgo answered ACCESS DENIED with no error. The username is not "
                  "authorised, or this machine is off the Argonne network. Stopping.")
            break

    write_scorecard(records)
    print(f"\nwrote {SCORECARD.relative_to(REPO)} and {len(records)} transcript(s) under evals/runs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
