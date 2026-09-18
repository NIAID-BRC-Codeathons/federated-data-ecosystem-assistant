"""Score the BOBBY-LANES runs per model and per question, and write RESULTS.md.

    uv run evals/leaderboard.py          # writes evals/leaderboard.json and evals/RESULTS.md

Reads only what is on disk: the transcripts in evals/runs/*-bobby-lanes/, the
answer tables in judge.py (EXPECTED_LANES, GROUND_TRUTH_BGC) and the list prices
in prices.json. Makes no model call and no network call.

Two checks per model, kept separate because they measure different things:

  right tool    -- the answer made at least one call to a tool the question needs
                   (judge.EXPECTED_LANES). It does not check that a two-server
                   question used both servers.
  right figure  -- the answer states every verified figure (judge.GROUND_TRUTH_BGC).

Every model x question cell also gets one outcome, which separates what the
model did wrong from what went wrong around it:

  correct     right tool, verified figure stated
  routed      right tool; the question has no verified figure
  miss_fig    right tool, figure wrong or missing          -- the model's fault
  miss_route  never called a tool the question needs       -- the model's fault
  no_tool     answered a data question with no tool call   -- the model's fault
  defect      the question pointed at nothing (B8, B16)    -- our fault
  not_asked   network drop, or Gemini refused the tools    -- not the model's fault
  (missing)   the run ended before the question was reached
"""
import importlib.util
import json
import math
import pathlib
import re
from collections import Counter

EVALS = pathlib.Path(__file__).resolve().parent
RUNS = EVALS / "runs"

spec = importlib.util.spec_from_file_location("judge", EVALS / "judge.py")
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)
EXPECTED = {k: set(v["primary"]) for k, v in judge.EXPECTED_LANES.items()
            if k.startswith("B")}
TRUTH = {k: [g["value"] for g in v] for k, v in judge.GROUND_TRUTH_BGC.items()
         if k.startswith("B")}
PRICES = json.loads((EVALS / "prices.json").read_text(encoding="utf-8"))

# Scoring rules changed on 18 Sep after reading the transcripts. Each one stopped
# a right answer being scored wrong.
#   B3  -- geo_series, called on both IDs, answers "is GSE309890 the same as
#          GDS309890" as well as geo_resolve_accession does.
#   B13 -- NCBI SRA counts differently from ENA: ncbi_sra_search reported 631,325
#          E. coli runs on 17 Sep against ENA's 551,679. Both are grounded.
#   B8  -- ran alone, so "those" pointed at nothing. Not scored.
#   B4, B16 -- name no database ("ciprofloxacin studies", "the records here"), so any
#          tool call counts as the right tool. An answer with no tool call is still a
#          miss: it is a data question answered from nothing.
EXPECTED["B3"] |= {"geo_series"}
# B13 accepts the SRA count, so it must accept the tool that produced it.
EXPECTED["B13"] |= {"ncbi_sra_search"}
ALTERNATIVES = {"B13": [[551679], [631325]]}
DEFECT = {"B8"}
ANY_TOOL = {"B4", "B16"}
# Read by hand. Each override must still match the answer text, or the script stops.
HAND = {("gpt41nano", "B15"): ("miss_fig", "GEO Series",
                               "states 181,408 but calls them GEO Series, not ENA runs")}
QIDS = [f"B{i}" for i in range(1, 17)]

# What each question is for. "Verified" answers come from BOBBY-LANES.md and
# judge.GROUND_TRUTH_BGC; anything the question file marks unverified says so.
QUESTIONS = {
 "B1":  ("GEO", "Count studies, and say they are studies (GEO Series)", "37 studies, read live 17 Sep"),
 "B2":  ("GEO", "List only files the tool returned; never guess a download link", "no figure"),
 "B3":  ("GEO", "A GSE and a GDS with the same digits are different records", "different record types; GDS309890 itself returns an empty record"),
 "B4":  ("any", "Misspelled organism: fix it, and say that you fixed it. Names no database, so any tool counts", "37 GEO studies once the spelling is fixed"),
 "B5":  ("GEO", "Read the platform from the record; do not invent one", "not verified"),
 "B6":  ("GEO", "\"Every\" from a paged search: give the total; do not imply the list is complete", "50 studies, read live 17 Sep"),
 "B7":  ("BRC", "Give the total, not the 50 rows a page shows", "551,679 runs, read 17 Sep"),
 "B8":  ("BRC", "Not scored: asked about \"those\" with nothing before it", "rewritten"),
 "B9":  ("BRC", "A study too big for the model (1 MB): report the real run count", "916 runs, read 17 Sep"),
 "B10": ("BRC", "Misspelled species returns zero on ENA: fix it or use the taxonomy ID", "551,679 once fixed"),
 "B11": ("BRC", "Report the service's known limits, not only \"healthy\"", "version 0.29.0 and its limits, read 17 Sep"),
 "B12": ("BRC", "BRC's own keyword tool fails quietly; a fraction needs a real numerator", "9,759 of 551,679 (by study title)"),
 "B13": ("both", "GEO studies and ENA runs are different units; do not add them", "551,679 ENA runs, or 631,325 as NCBI SRA's tool reports; GEO 1,929 studies (not checked)"),
 "B14": ("both", "Chain: GEO study, then its raw reads, then can they be assembled. Scored on any one needed tool; the chain is not checked", "no figure"),
 "B15": ("both", "The same unit trap as B13, for S. aureus", "181,408 ENA runs; GEO 500 studies (not checked); read 17 Sep"),
 "B16": ("any", "Influenza in E. coli records. Names no database, so any tool counts", "GEO 4, ENA 5, read 17 Sep (not checked)"),
}


def load(p):
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def summary(rows):
    for o in reversed(rows):
        if isinstance(o, dict) and isinstance(o.get("summary"), dict):
            return o["summary"]
    return None


def norm(text):
    """Join digit groups however they are written: 551,679 / 551 679 / 9{,}759."""
    t = text.replace("{,}", "")
    return re.sub(r"(?<=\d)[,    ](?=\d{3}\b)", "", t)


def has(text, value):
    return re.search(rf"(?<!\d){value}(?!\d)", norm(text)) is not None


def transport(err):
    e = str(err or "")
    return any(s in e for s in ("Connect", "connect", "ReadError", "timed out",
                                "Timeout", "validation error", "Schema", "silent empty"))


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def family(name):
    return "Gemini" if "gemini" in name else "Claude" if "claude" in name else "GPT"


models, cells = [], {}
for d in sorted(RUNS.glob("*-bobby-lanes")):
    name = d.name.replace("-bobby-lanes", "")
    short = name.replace("argo_", "").replace("anthropic_", "")
    r = dict(model=short, family=family(short), asked=0, not_asked=0, route_ok=0,
             route_n=0, acc_ok=0, acc_n=0, no_tool=0, out=0, inn=0, sec=0.0, calls=0)
    for f in sorted(d.glob("b*.jsonl")):
        qid = "B" + str(int(f.stem[1:]))
        s = summary(load(f))
        if not s:
            continue
        tools = s.get("tools_in_order") or []
        ans = s.get("answer") or ""
        # ---- one outcome per cell
        if s.get("error") and transport(s["error"]):
            o = "not_asked"
        elif qid in DEFECT:
            o = "defect"
        elif not tools:
            o = "no_tool"
        elif qid not in ANY_TOOL and not set(tools) & EXPECTED.get(qid, set()):
            o = "miss_route"
        elif qid in TRUTH:
            alts = ALTERNATIVES.get(qid, [TRUTH[qid]])
            o = "correct" if any(all(has(ans, v) for v in alt) for alt in alts) else "miss_fig"
        else:
            o = "routed"
        if (short, qid) in HAND:
            o, must, why = HAND[(short, qid)]
            assert must in ans, f"hand override {short} {qid} no longer matches its answer"
        cells.setdefault(short, {})[qid] = dict(o=o, tools=tools, sec=round(s.get("elapsed_s") or 0))
        # ---- per-model scores (B8 is not scored at all; B16 only counts toward cost)
        if o == "not_asked":
            r["not_asked"] += 1
            continue
        if qid == "B8":
            continue
        r["asked"] += 1
        r["out"] += s.get("output_tokens") or 0
        r["inn"] += s.get("input_tokens") or 0
        r["sec"] += s.get("elapsed_s") or 0
        r["calls"] += len(tools)
        if qid in DEFECT:
            continue
        r["no_tool"] += o == "no_tool"
        r["route_n"] += 1
        r["route_ok"] += o in ("correct", "routed", "miss_fig")
        if qid in TRUTH:
            r["acc_n"] += 1
            r["acc_ok"] += o == "correct"
    if r["asked"]:
        k, n = r["route_ok"] + r["acc_ok"], r["route_n"] + r["acc_n"]
        r.update(score=k / n, score_k=k, score_n=n, score_ci=wilson(k, n),
                 sec_q=r["sec"] / r["asked"], out_q=r["out"] / r["asked"],
                 in_q=r["inn"] / r["asked"])
        p = PRICES["models"].get(short)
        r["cost_q"] = ((r["in_q"] * p["input"] + r["out_q"] * p["output"]) / 1e6) if p else None
        r["official_name"] = p["name"] if p else short
    models.append(r)

scored = sorted([m for m in models if m["asked"] >= 12],
                key=lambda m: (-m["score"], m["cost_q"] if m["cost_q"] is not None else 9e9))
partial = [m for m in models if 0 < m["asked"] < 12]
never = [m for m in models if not m["asked"]]
tot = Counter(c["o"] for m in cells.values() for c in m.values())

per_q = {}
for q in QIDS:
    cs = [m[q] for m in cells.values() if q in m and m[q]["o"] != "not_asked"]
    per_q[q] = dict(answered=len(cs),
                    right=sum(c["o"] in ("correct", "routed") for c in cs),
                    fair=sum(c["o"] not in ("defect",) for c in cs),
                    both_servers=sum(any(t.startswith("geo_") for t in c["tools"])
                                     and any(t.startswith("brc_") for t in c["tools"]) for c in cs))

(EVALS / "leaderboard.json").write_text(json.dumps(
    {"models": models, "cells": cells, "per_question": per_q, "questions": QUESTIONS,
     "outcomes": dict(tot)}, indent=1, default=list), encoding="utf-8")

# ------------------------------------------------------------------ RESULTS.md
full = [m for m in scored if m["score_k"] == m["score_n"]]
cheap = min(full, key=lambda m: m["cost_q"])
dear = max(full, key=lambda m: m["cost_q"])
fastest_full = ", ".join(m["official_name"] for m in sorted(full, key=lambda m: m["sec_q"])
                         if m["sec_q"] < min(x["sec_q"] for x in full) * 1.06)
OURS = ("geo_", "brc_ena_", "brc_federation_status")
all_calls = [t for m in cells.values() for c in m.values() for t in c["tools"]]
our_calls = sum(t.startswith(OURS) for t in all_calls)
answered_all = [m for m in models if m["asked"] == 15 and not m["not_asked"]]
lost = [m for m in models if m["not_asked"] and m["family"] != "Gemini"]
missed = [m for m in scored if m["score_k"] < m["score_n"]]
slow = [m for m in scored if m["sec_q"] > 30]
fails = sum(tot[k] for k in ("miss_route", "miss_fig", "no_tool"))
fair = fails + tot["correct"] + tot["routed"]
asked_text = {q: summary(load(f))["question"] for q in QIDS
              for f in [RUNS / "argo_gpt41mini-bobby-lanes" / f"b{int(q[1:]):02d}.jsonl"] if f.exists()}

L = []
w = L.append
w("# GEO and BRC Analytics servers: results across models\n")
w("*Generated by `uv run evals/leaderboard.py` from the transcripts in `evals/runs/*-bobby-lanes/`. "
  "Do not edit by hand.*\n")
w(f"16 plain-language questions were asked of {len(models)} models through the agent setup "
  f"`chatbot.py` uses, with every team server's tools loaded. {len(answered_all)} answered all 16; "
  f"{len(lost)} lost questions to a network drop or an empty reply; the {len(never)} Gemini models "
  f"refused the tool list. {len(scored)} models answered enough to score, and {len(full)} of them "
  f"passed every check. The cheapest of those, {cheap['official_name']}, cost "
  f"${cheap['cost_q']:.3f} per question at list price; the dearest, {dear['official_name']}, cost "
  f"${dear['cost_q']:.2f}. Models made {len(all_calls):,} tool calls, {our_calls} of them to these "
  "two servers.\n")
w("## The 16 questions\n")
w("Six were written for GEO, six for BRC/ENA, and four for both. Two of them (B4, B16) name no "
  "database, so any tool counts there. Each is a question a researcher might type, written so "
  "that the obvious call gets it wrong in one specific way.\n")
w("| # | Question (as asked) | Server | What it tests | Verified answer | Right / answered |")
w("|---|---|---|---|---|---|")
for q in QIDS:
    srv, tests, ans = QUESTIONS[q]
    pq = per_q[q]
    res = "not scored" if q in DEFECT else f"{pq['right']} / {pq['answered']}"
    w(f"| {q} | {asked_text.get(q, '')} | {srv} | {tests} | {ans} | {res} |")
w("\n\"Right\" means the right tool, plus every verified figure where one exists.\n")
w("## Leaderboard\n")
top = max(scored, key=lambda m: m["score_n"])
w(f"Each model faced up to {top['score_n']} checks: {top['route_n']} for the right tool and "
  f"{top['acc_n']} for the verified figure (fewer if a question was lost to the network). Cost is "
  "list price times the measured tokens; input tokens are most of it, because every turn resends "
  "every server's tool definitions.\n")
w("| Model | Family | Score | 95% range | Right tool | Right figure | No tool call | $ / question | Seconds / question | Output tokens / question |")
w("|---|---|---|---|---|---|---|---|---|---|")
for m in scored:
    lo, hi = m["score_ci"]
    cost = f"{m['cost_q']:.3f}" if m["cost_q"] is not None else "not priced"
    w(f"| {m['official_name']} | {m['family']} | {m['score_k']}/{m['score_n']} | {lo:.0%}–{hi:.0%} | "
      f"{m['route_ok']}/{m['route_n']} | {m['acc_ok']}/{m['acc_n']} | {m['no_tool']} | {cost} | "
      f"{m['sec_q']:.0f} | {m['out_q']:,.0f} |")
w("\nToo few questions to rank: " + ", ".join(f"{m['model']} ({m['asked']} of 15)" for m in partial) + ".")
w("Never ran: " + ", ".join(m["model"] for m in never) + " (Gemini refused the combined tool list).\n")
w("## What went wrong, and whose fault it was\n")
w("| Outcome | Cells | Whose fault |")
w("|---|---|---|")
for o, label, fault in [("correct", "right tool, verified figure stated", "none"),
                        ("routed", "right tool; no verified figure to check", "none"),
                        ("miss_fig", "right tool, figure wrong or missing", "model"),
                        ("miss_route", "never called a tool the question needs", "model"),
                        ("no_tool", "answered a data question with no tool call", "model"),
                        ("defect", "the question pointed at nothing (B8)", "ours"),
                        ("not_asked", "network drop, empty reply, or Gemini refused the tools", "network or format")]:
    w(f"| {label} | {tot[o]} | {fault} |")
w(f"\nOf {fair} cells where the model had a fair chance, {fails} went wrong.\n")
w(f"**Speed.** Full score does not need a slow model: {fastest_full} reached it at "
  f"{min(m['sec_q'] for m in full):.0f} s per question. But every model that missed a check ran at "
  f"{max(m['sec_q'] for m in missed):.0f} s or less, and all {len(slow)} models slower than 30 s "
  "scored full marks. Pick by score first, then cost.\n")
w("**Answers without tools.** Read by hand on 18 Sep: every answer that skipped the tools on a "
  "scored question came from o1 or o3-mini. Six describe a search that never happened "
  "(o1 on B4 and B13; o3-mini on B3, B12, B14 and B16). Four of those report results the model "
  "could not have seen: o1 said zero ciprofloxacin studies (the tool returns 37) and 2,735 GEO "
  "series on B13; o3-mini said GDS309890 is a curated DataSet (the record is empty) and that no "
  "E. coli record mentions influenza (ENA has 5). Two more (o3-mini on B13 and B15) promise a "
  "search and stop. On B8, which pointed at nothing, the models that made no call said so plainly.\n")
w("## Limits\n")
w("- One run per model, so nothing measures how much a model varies between runs.")
w("- 16 questions, 6 with a verified figure: enough to show the servers work and to find the "
  "weakest models, too few to rank the models that tie at the top.")
w("- Most answers took one tool call. The hard part was choosing the right tool and reading its "
  "result, not chaining calls.")
w("- The figure check looks for the number, not for what the answer calls it. One answer was "
  "corrected by hand (gpt41nano B15: 181,408 called \"GEO Series\").")
w("- B13 and B15 check only the ENA figure. B14 is scored on any one needed tool; the chain "
  "itself is not checked.")
w("- Known server defect: `geo_resolve_accession` reports GDS309890 as resolved, but the record is "
  "empty, and several B3 answers repeat that it exists.")
w("- Figures are a snapshot: overnight the E. coli run count grew from 551,679 to 551,870.")
w("- Scripted single questions, not conversations; the Chainlit chatbot was not run as a user would.")
w(f"- Prices were read on {PRICES['read_on']} from the vendors' pages ({', '.join(PRICES['sources'].values())}). "
  "Argo IDs were matched to models by spelling. Argo itself charges nothing.\n")
w("Questions and ground truth: `evals/BOBBY-LANES.md`. Scorer answer tables: `evals/judge.py`. "
  "Prices: `evals/prices.json`.")
(EVALS / "RESULTS.md").write_text("\n".join(L) + "\n", encoding="utf-8")

print(f"{len(models)} models, {len(scored)} scored, {len(full)} full score; cells {dict(tot)}")
print(f"cheapest full score {cheap['model']} ${cheap['cost_q']:.4f}; dearest {dear['model']} ${dear['cost_q']:.4f}")
print(f"fair {fair}, model's fault {fails}")
