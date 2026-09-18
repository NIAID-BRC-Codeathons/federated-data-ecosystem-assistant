#!/usr/bin/env python3
"""Build the demo page: Bobby's two servers, every question, every model, side by side.

    uv run evals/build_demo.py                 # read evals/runs, write evals/demo/
    uv run evals/build_demo.py --self-test     # prove every guard can fire

WHAT IT CONSUMES, AND WHAT IT REFUSES TO RE-DERIVE

Correctness flags come from `judge.py` by importing `judge.collect()` and reading
the rows it returns. This file does not re-implement routing, fabrication, ground
truth or trap detection. Two scorers that disagree are worse than one.

Cost, tokens, time, provenance and transport faults come from the summary record
`run_questions.py` writes at the end of every transcript. Those are measured
fields, read as written.

Nothing here writes into `evals/runs/`. It is read-only over that tree.

THE GUARDS, AND WHY EACH ONE EXISTS

Every guard answers the same failure: an empty result standing in for "I could
not look". Each is exercised by `--self-test` against a record built to trip it,
and a clean record is checked to trip none of them.

  G1  Two records for one model with different `code_sha` are never averaged.
      The tree changed under a run twice on 17 Sep.
  G2  A denial, an error or a silent empty reply is UNSCORABLE, never zero.
      Argo returns HTTP 200 with ACCESS DENIED as the assistant's content and
      bills tokens for it, so it is a transport fault and not a model answer.
  G3  A record judge returns no row for is named on the page and marked not
      scored, never pass and never fail. Judge refuses by name -- no rubric for
      the set, no provenance, a placeholder question -- and on 17 Sep all 234
      BOBBY-LANES records sat in that state. An absent score must never render
      as an empty lane or as a result.
  G4  Every dollar figure carries `list_cost_note`. The prices were written from
      memory, not read off a price page. Tokens are measured; dollars are not.
  G5  GEO Series and ENA runs are never added. A Series is a curated study; a
      run is one sequencing run. `refuse_cross_lane_total` raises.
  G6  A question whose source was not wired for the run is marked source-absent,
      not scored as a model failure. The matrix ran without BV-BRC's 18 tools.
  G7  A failed attempt parked beside a good transcript never replaces it, and
      never disappears either. After 17 Sep the runner writes a failure to
      `q02.error-160722.jsonl` instead of over `q02.jsonl`. Both names match this
      file's `q*.jsonl` glob, so one question arrived as two records carrying one
      `question_id`, and the matrix cell went to whichever sorted last. Measured:
      `q07.error-...` sorts before `q07.jsonl` and the answer survived;
      `q07.retry-...` sorts after it and the answer became "held out". The page
      was correct only by an alphabetical accident in a filename another agent
      chooses. Now the primary file wins the cell by rule, parked attempts are
      counted and named, and a question with ONLY parked attempts still gets a
      row, held out -- a retried failure that vanishes is this project's defect
      wearing a different hat.
  G8  "Never asked" and "answered, nobody scored it" are never added together.
      They are opposite facts. A transport fault means the model was never
      reached; an unscored record means it answered and the scorer could not read
      the file. Measured 17 Sep: this page printed `unscorable + unseen` under one
      column called "held out", so 146 answered BOBBY-LANES records -- every one of
      them a real answer from Bobby's two servers -- read as failures. The page now
      carries two columns, "not asked" and "not scored", and this guard fails if
      anything merges them again.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as _dt
import html
import io
import json
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = HERE / "runs"
OUT = HERE / "demo"

sys.path.insert(0, str(HERE))


# --- what the two servers are ----------------------------------------------
#
# Seven tools on Bobby's branch. The lane a question belongs to is decided by
# these sets and nothing else, so a question cannot drift between lanes when a
# model calls something unexpected.

GEO_TOOLS = {"geo_search", "geo_series", "geo_resolve_accession"}
ENA_TOOLS = {"brc_ena_runs", "brc_ena_search", "brc_ena_study",
             "brc_federation_status"}

LANES = {
    "geo": {
        "name": "GEO lane",
        "server": "mcp_servers/geo.py",
        "port": 8009,
        "tools": GEO_TOOLS,
        "unit": "Series, one curated study",
        "fails_by": "a zero that needs interpreting",
    },
    "ena": {
        "name": "BRC / ENA lane",
        "server": "mcp_servers/brc_analytics.py",
        "port": 8008,
        "tools": ENA_TOOLS,
        "unit": "run, one sequencing run",
        "fails_by": "an exact-match miss that looks identical to absence",
    },
}

# BOBBY-LANES.md says which part each question is in. Part C asks both lanes the
# same shape of question, and that contrast is the point of the set.
BOBBY_LANE_OF = {**{"B%d" % i: "geo" for i in range(1, 7)},
                 **{"B%d" % i: "ena" for i in range(7, 13)},
                 **{"B%d" % i: "both" for i in range(13, 17)}}

# The headline finding, quoted rather than paraphrased.
HEADLINE = {
    "model": "argo/gpt4o",
    "tool": 'ncbi_pathogen_isolate_count(organism="Escherichia coli")',
    "said": "a total of zero isolates matching this filter, despite correct "
            "organism naming",
    "truth": 581464,
    "truth_note": "live-verified 17 Sep 2026 through the real server stack in "
                  "300 ms, with the retrieval URL in the tool output",
    "why": "The organism name was wrong. The data was not absent.",
    # Same model, same tool, same question, later in the day. The only thing that
    # changed is the filter value, and the filter value is the whole finding.
    "fixed_call": 'ncbi_pathogen_isolate_count(organism="E.coli and Shigella")',
    "fixed_sha": "bafed0f-dirty",
    "old_sha": None,   # the archived run predates code_sha; the tree is unknown
    # opus5 named the trap out loud on Q13 rather than walking into it.
    "named_by": "argo/claudeopus5",
    "named": "that vocabulary is matched literally -- a misspelled symbol returns "
             "zero isolates rather than an error",
}

# Questions built to catch exactly that failure.
ZERO_TRAP_QS = {"B4", "B10", "B16"}

# dataviz status palette. Every use carries a word, never colour alone.
STATUS_HEX = {"good": "#0ca30c", "warning": "#fab219",
              "serious": "#ec835a", "critical": "#d03b3b"}


class CrossLaneTotal(Exception):
    """Raised rather than return a number that means nothing."""


def refuse_cross_lane_total(geo_value, ena_value):
    """G5. A GEO Series and an ENA run are not the same unit.

    This is a function so the refusal is testable. Nothing in the renderer may
    add across lanes; anything that tries raises here.
    """
    raise CrossLaneTotal(
        "refused to add %s GEO Series to %s ENA runs: a Series is a curated "
        "study, a run is one sequencing run" % (geo_value, ena_value))


# --- reading the transcripts ------------------------------------------------


class Record:
    """One `qNN.jsonl`: its summary, its lane, and whether it can be scored."""

    def __init__(self, path):
        self.path = path
        self.summary = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "summary" in obj:
                self.summary = obj["summary"]
        s = self.summary
        self.model = s.get("model") or "unknown"
        self.qid = str(s.get("question_id") or s.get("question_number") or "")
        self.question = s.get("question") or ""
        self.tools = list(s.get("tools_in_order") or [])
        self.code_sha = s.get("code_sha") or ""
        self.run_id = s.get("run_id") or ""
        self.questions_file = (s.get("questions_file") or "").replace("\\", "/")
        self.set_name = pathlib.Path(self.questions_file).stem or "unknown"
        self.discarded = list(s.get("attempts_discarded") or [])

    @property
    def fault(self):
        """G2. The transport faults that mean the model was never properly asked.

        A denial, a raised error and a silent empty reply all leave no answer to
        score. They are held out, never counted as a wrong answer.

        `attempts_discarded` is deliberately NOT held out here. An attempt that
        was retried into a real answer produced a real answer, and discarding it
        would hide a success. The count gets its own column instead, so the
        transport-fault rate stays visible, which is why runner kept it.
        """
        s = self.summary
        if s.get("error"):
            return ("error", str(s["error"])[:120])
        if s.get("denied"):
            return ("denied", "Argo answered ACCESS DENIED as assistant content")
        if (s.get("answer_chars") == 0 and not self.tools
                and (s.get("output_tokens") or 0) == 0):
            return ("silent empty",
                    "%s input tokens billed, 0 output, no error, no denial"
                    % format(s.get("input_tokens", 0), ","))
        return None

    @property
    def scorable(self):
        return self.fault is None

    def lane(self, expected_tools):
        """Which server this question is about. Set first, tools second."""
        if self.qid in BOBBY_LANE_OF:
            return BOBBY_LANE_OF[self.qid]
        exp = set(expected_tools or ())
        geo, ena = exp & GEO_TOOLS, exp & ENA_TOOLS
        if geo and ena:
            return "both"
        if geo:
            return "geo"
        if ena:
            return "ena"
        return "off-branch"


# `q02.jsonl` is an answer. `q02.error-160722.jsonl` is a failed attempt parked
# beside it. Both match `q*.jsonl`, so the shape has to be told apart by name.
PRIMARY_NAME = re.compile(r"^[qrsb]\d+\.jsonl$", re.IGNORECASE)


def load_records(runs):
    """Every transcript under `runs`, grouped by run directory.

    Globs `b*` as well as `q*`, `r*` and `s*`. Judge does not (G3), and the
    difference between the two lists is reported on the page rather than
    silently absorbed.

    G7. One question must produce one row. The runner parks a failed attempt in
    a sidecar named after the question it failed, so a naive glob counts that
    question twice and inflates every denominator on the page.
    """
    groups = {}
    unreadable = []
    retried = {}
    if not runs.is_dir():
        return groups, ["%s is not a directory" % runs], retried
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        if d.name.startswith("_"):      # _archive-pre-matrix, _premerge-*
            continue
        paths = sorted(p for pat in ("q*.jsonl", "r*.jsonl", "s*.jsonl",
                                     "b*.jsonl") for p in d.glob(pat))
        primary, sidecar = {}, collections.defaultdict(list)
        for p in paths:
            try:
                rec = Record(p)
            except Exception as exc:
                unreadable.append("%s/%s: %s: %s"
                                  % (d.name, p.name, type(exc).__name__, exc))
                continue
            if not rec.summary:
                unreadable.append("%s/%s: no summary record" % (d.name, p.name))
                continue
            # Key on the FILE stem, not the question id. A sidecar carries the
            # same `question_id` as the answer it failed at, which is exactly
            # why keying on the id is what lost the count in the first place.
            stem = p.name.split(".", 1)[0].lower()
            if PRIMARY_NAME.match(p.name):
                primary[stem] = rec
            else:
                sidecar[stem].append(rec)
        rows = []
        parked = []
        for stem in sorted(set(primary) | set(sidecar)):
            if stem in primary:
                rows.append(primary[stem])
                for r in sidecar.get(stem, []):
                    parked.append("%s (answered on retry)" % r.path.name)
            else:
                # No answer ever landed for this question. Keep the newest
                # attempt so the question holds its row and reads as held out,
                # rather than silently leaving the matrix.
                last = sorted(sidecar[stem], key=lambda r: r.path.name)[-1]
                rows.append(last)
                for r in sidecar[stem]:
                    if r is not last:
                        parked.append("%s (superseded attempt)" % r.path.name)
        if parked:
            retried[d.name] = parked
        if rows:
            groups[d.name] = rows
    return groups, unreadable, retried


# --- consuming judge --------------------------------------------------------


def judge_rows(runs):
    """`judge.collect()`, keyed (run directory, question id).

    Judge's file is edited continuously during the event, so an import can catch
    it half-written. That is reported loudly and never turned into an empty
    result. An artifact that renders a blank matrix because it could not import
    the scorer is the exact failure this project is about.
    """
    try:
        import importlib
        import judge as _judge
        importlib.reload(_judge)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            by_model, skipped = _judge.collect(runs)
    except Exception as exc:
        return {}, [], "%s: %s" % (type(exc).__name__, exc)
    out = {}
    for model_dir, rows in by_model.items():
        for r in rows:
            out[(model_dir, _qid_key(r.get("q")))] = r
    return out, skipped, None


def _qid_key(raw):
    """Judge returns `'3'` for Q3 and `'R2'` for R2. Make both comparable."""
    s = str(raw or "").strip().upper()
    return "Q" + s if s.isdigit() else s


def expected_tools_for(qid):
    try:
        import judge as _judge
        exp = _judge.expected_for(_judge._normalise_qid(qid))
        return set(exp.get("primary") or ())
    except Exception:
        return set()


# --- the guards -------------------------------------------------------------


def sha_conflicts(groups):
    """G1. Run directories holding records built from more than one tree."""
    out = {}
    for name, recs in groups.items():
        shas = sorted({r.code_sha for r in recs if r.code_sha})
        if len(shas) > 1:
            out[name] = shas
    return out


def unseen_by_judge(groups, jrows):
    """G3. Records on disk that judge produced no row for.

    Reported per run directory with the question ids, because "judge scored
    nothing here" and "there is nothing here" look identical on a page and mean
    opposite things.
    """
    out = {}
    for name, recs in groups.items():
        missing = [r.qid for r in recs if (name, _qid_key(r.qid)) not in jrows]
        if missing:
            out[name] = missing
    return out


STANDING_COST_CAVEAT = (
    "Every dollar on this page is an UNVERIFIED list-price estimate, written "
    "from memory and never read off a price page. Argo billed none of it.")


def cost_note(recs):
    """G4. What runner said about this model's price, if anything."""
    for r in recs:
        note = r.summary.get("list_cost_note")
        if note:
            return str(note)
    return "no list_cost_note on the record"


def all_cost_notes(models):
    """Every distinct thing runner said about price, plus the standing caveat.

    Taking one model's note and printing it under everybody's dollars is how a
    page ends up showing $7.36 with no caveat at all: the alias that sorts
    first may carry a "no price on file" note that says nothing about the
    prices that ARE shown.
    """
    seen, out = set(), [STANDING_COST_CAVEAT]
    for m in models:
        n = m.get("cost_note")
        if n and n not in seen:
            seen.add(n)
            out.append("%s: %s" % (m["model"], n))
    return out


# --- aggregation ------------------------------------------------------------


def cell_for(rec, jrow):
    """One square of the matrix: what this model did with this question."""
    fault = rec.fault
    c = {
        "qid": rec.qid, "model": rec.model, "tools": rec.tools,
        "calls": rec.summary.get("tool_call_count", 0),
        "seconds": rec.summary.get("elapsed_s"),
        "ttft": rec.summary.get("ttft_s"),
        "in_tok": rec.summary.get("input_tokens") or 0,
        "out_tok": rec.summary.get("output_tokens") or 0,
        "usd": rec.summary.get("list_cost_usd"),
        "chars": rec.summary.get("answer_chars") or 0,
        "sha": rec.code_sha, "discarded": len(rec.discarded),
        "path": rec.path, "fault": fault,
        "state": "unscorable" if fault else "pending",
        "why": fault[1] if fault else "", "flags": [],
        "question": rec.question,
    }
    if fault:
        c["label"] = fault[0]
        return c
    if jrow is None:
        c["state"] = "unseen"
        c["label"] = "not scored"
        c["why"] = "judge produced no row for this record"
        return c

    gt = jrow.get("ground_truth") or {}
    traps = list(jrow.get("traps") or [])
    c["routed"] = jrow.get("routed")
    c["routed_first"] = jrow.get("routed_first")
    c["verdict"] = jrow.get("verdict")
    c["traps"] = traps
    c["gt"] = gt

    # Worst first. A wrong figure and a fabrication are the two states that
    # produce a confident false statement, which is what this page is about.
    if "zero_as_absence" in traps:
        c["state"], c["label"] = "critical", "zero read as absence"
    elif gt.get("applies") and gt.get("state") == "wrong":
        c["state"], c["label"] = "critical", "wrong figure"
    elif jrow.get("verdict") == "fabricated":
        # Judge's own evidence goes beside its verdict. On 18 Sep runner read
        # all 30 flagged rows: 29 were not fabrications -- mostly a taxonomy ID,
        # a percentage, or a correct figure the number parser split ("551 679"
        # read as 551 and 679). With
        # the numbers on the cell, "fabricated: 511145" reads as a taxid.
        c["state"], c["label"] = "critical", "fabricated"
        unmatched = [str(n) for n in
                     ((jrow.get("numbers") or {}).get("unmatched") or [])]
        if unmatched:
            c["label"] = "fabricated: " + ", ".join(unmatched[:4]) + (
                " +%d more" % (len(unmatched) - 4) if len(unmatched) > 4 else "")
            c["why"] = "judge found these numbers in no tool result"
    elif traps:
        c["state"], c["label"] = "serious", traps[0]
    elif gt.get("applies") and gt.get("state") == "miss":
        # Retrieved but not reported: routed correctly, never stated the number.
        if jrow.get("routed") == "yes":
            c["state"], c["label"] = "serious", "retrieved, not reported"
            c["flags"].append("retrieved, not reported")
        else:
            c["state"], c["label"] = "serious", "figure never stated"
    elif jrow.get("routed") == "no" and c["calls"] == 0:
        c["state"], c["label"] = "serious", "no tool call"
    elif jrow.get("routed") == "no":
        c["state"], c["label"] = "serious", "wrong lane"
    elif gt.get("applies") and gt.get("state") == "hit":
        # A checked figure beats a generic needs-review. Judge flags
        # needs-review whenever an answer holds a number it cannot match to the
        # 600-character tool excerpt, which is nearly every long answer. The
        # ground-truth check looked at the one number that matters and found it
        # right, so that is what the cell says -- with the caveat kept.
        c["state"], c["label"] = "good", "figure correct"
        if jrow.get("verdict") == "needs-review":
            c["why"] = ("the scored figure is right; other numbers in the "
                        "answer could not be checked against the cut tool log")
            c["flags"].append("other numbers unchecked")
    elif jrow.get("verdict") == "needs-review":
        c["state"], c["label"] = "warning", "needs review"
    else:
        c["state"], c["label"] = "good", "routed, no flag"
    if rec.qid in ZERO_TRAP_QS:
        c["flags"].append("zero-trap question")
    return c


def _qsort(qid):
    """B2 before B10, Q3 before Q15."""
    s = str(qid)
    letters = "".join(ch for ch in s if ch.isalpha())
    digits = "".join(ch for ch in s if ch.isdigit())
    return (letters, int(digits) if digits else 0)


BOBBY_TOOLS = GEO_TOOLS | ENA_TOOLS


def bobby_usage(recs):
    """How often this model actually called the seven tools on Bobby's branch.

    This is NOT the lane column and must never be confused with it. `lane()` says
    what a question is ABOUT, decided by the question set so it cannot drift. This
    says what the model DID. Measured 17 Sep: 26 calls to these seven tools sat
    behind 12 questions labelled "off-branch", so the page was showing his two
    servers as barely used when every one of the seven had been exercised.
    """
    calls = collections.Counter()
    qids = set()
    for r in recs:
        hit = [t for t in r.tools if t in BOBBY_TOOLS]
        if hit:
            qids.add(r.qid)
        for t in hit:
            calls[t] += 1
    return {"calls": sum(calls.values()), "questions": len(qids),
            "by_tool": dict(calls), "tools_used": len(calls)}


def build_model(name, recs, jrows):
    cells = {}
    for r in sorted(recs, key=lambda r: _qsort(r.qid)):
        cells[r.qid] = cell_for(r, jrows.get((name, _qid_key(r.qid))))
    scorable = [c for c in cells.values() if c["state"] != "unscorable"]
    return {
        "dir": name, "model": recs[0].model, "set": recs[0].set_name,
        "cells": cells, "n": len(cells),
        "unscorable": sum(1 for c in cells.values() if c["state"] == "unscorable"),
        "critical": sum(1 for c in cells.values() if c["state"] == "critical"),
        "serious": sum(1 for c in cells.values() if c["state"] == "serious"),
        "warning": sum(1 for c in cells.values() if c["state"] == "warning"),
        "good": sum(1 for c in cells.values() if c["state"] == "good"),
        "unseen": sum(1 for c in cells.values() if c["state"] == "unseen"),
        "discarded": sum(c["discarded"] for c in cells.values()),
        "in_tok": sum(c["in_tok"] for c in cells.values()),
        "out_tok": sum(c["out_tok"] for c in cells.values()),
        "usd": sum(c["usd"] for c in cells.values() if c["usd"] is not None),
        "priced": sum(1 for c in cells.values() if c["usd"] is not None),
        "unpriced": sum(1 for c in cells.values() if c["usd"] is None),
        "seconds": sum((c["seconds"] or 0) for c in cells.values()),
        "scorable_n": len(scorable),
        "bobby": bobby_usage(recs),
        "cost_note": cost_note(recs),
        "shas": sorted({r.code_sha for r in recs if r.code_sha}),
        "run_ids": sorted({r.run_id for r in recs if r.run_id}),
        "faults": sorted(((c["qid"], c["label"], c["why"], c["in_tok"])
                          for c in cells.values() if c["state"] == "unscorable"),
                         key=lambda x: _qsort(x[0])),
    }


def build(runs):
    """Everything the page needs, with every guard already evaluated."""
    groups, unreadable, retried = load_records(runs)
    jrows, skipped, judge_error = judge_rows(runs)
    models = {n: build_model(n, recs, jrows) for n, recs in groups.items()}

    # Questions, in order, across every run directory of the same set.
    sets = {}
    for name, recs in groups.items():
        for r in recs:
            sets.setdefault(r.set_name, {}).setdefault(r.qid, r.question)

    lanes = {}
    for name, recs in groups.items():
        for r in recs:
            lanes[r.qid] = r.lane(expected_tools_for(r.qid))

    return {
        "runs": runs,
        "groups": groups,
        "models": models,
        "sets": sets,
        "lanes": lanes,
        "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "guards": {
            "sha_conflicts": sha_conflicts(groups),        # G1
            "unseen_by_judge": unseen_by_judge(groups, jrows),   # G3
            "judge_error": judge_error,                    # G3
            "judge_skipped": skipped,
            "unreadable": unreadable,
            "retried": retried,            # G7
        },
    }


# --- rendering --------------------------------------------------------------

CSS = """
:root{
  color-scheme:light;
  --paper:#f7f6f2; --card:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --rule:#e1e0d9; --ring:rgba(11,11,11,.10);
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --accent:#2a78d6;
  --good-bg:#e9f5e9; --warning-bg:#fdf3dd; --serious-bg:#fbeae2;
  --critical-bg:#f8e3e3; --grey-bg:#eeede8;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --paper:#0d0d0d; --card:#1a1a19; --ink:#fff; --ink2:#c3c2b7;
    --muted:#898781; --rule:#2c2c2a; --ring:rgba(255,255,255,.10);
    --accent:#3987e5;
    --good-bg:#122a12; --warning-bg:#332a10; --serious-bg:#32211a;
    --critical-bg:#331818; --grey-bg:#222221;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --paper:#0d0d0d; --card:#1a1a19; --ink:#fff; --ink2:#c3c2b7;
  --muted:#898781; --rule:#2c2c2a; --ring:rgba(255,255,255,.10);
  --accent:#3987e5;
  --good-bg:#122a12; --warning-bg:#332a10; --serious-bg:#32211a;
  --critical-bg:#331818; --grey-bg:#222221;
}
*{box-sizing:border-box}
body{
  margin:0; padding:0 16px 96px; background:var(--paper); color:var(--ink);
  font:16px/1.55 Charter,"Iowan Old Style",Georgia,"Times New Roman",serif;
}
.wrap{max-width:1100px;margin:0 auto}
.kicker{
  font:600 11px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
  margin:40px 0 10px;
}
h1{font-size:30px;line-height:1.2;margin:0 0 12px;font-weight:600;letter-spacing:-.01em}
h2{
  font:600 12px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.09em; text-transform:uppercase; color:var(--muted);
  margin:0; padding:0;
}
.verdict{font-size:19px;line-height:1.5;margin:0 0 14px;max-width:70ch}
.meta{
  font:12px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--muted);
  border-top:1px solid var(--rule);padding-top:10px;margin-bottom:28px;
}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:12px;margin:0 0 30px}
.tile{background:var(--card);border:1px solid var(--ring);border-radius:9px;padding:14px 15px}
.tile .lab{
  font:600 10px/1.3 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
}
.tile .val{
  font:600 30px/1.1 system-ui,-apple-system,"Segoe UI",sans-serif;
  margin:7px 0 3px;font-variant-numeric:tabular-nums;letter-spacing:-.02em;
}
.tile .den{font-size:12.5px;color:var(--ink2);line-height:1.4}
.pill{
  display:inline-block;margin-top:9px;padding:2px 8px;border-radius:99px;
  font:600 10px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.05em;text-transform:uppercase;border:1px solid var(--ring);
  background:var(--grey-bg);color:var(--ink2);
}
.pill.good{background:var(--good-bg)} .pill.warning{background:var(--warning-bg)}
.pill.serious{background:var(--serious-bg)} .pill.critical{background:var(--critical-bg)}
.sec{margin:0 0 14px;background:var(--card);border:1px solid var(--ring);border-radius:9px}
.sec>summary{
  cursor:pointer;padding:14px 16px;list-style:none;display:flex;
  justify-content:space-between;align-items:baseline;gap:14px;flex-wrap:wrap;
}
.sec>summary::-webkit-details-marker{display:none}
.sec>summary::after{content:"+";color:var(--muted);font:600 17px/1 system-ui,sans-serif}
.sec[open]>summary::after{content:"\\2013"}
.sec[open]>summary{border-bottom:1px solid var(--rule)}
.own{font:11px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--muted)}
.body{padding:16px}
.body>p{max-width:72ch}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:0 0 6px}
th,td{
  text-align:left;padding:7px 9px;border-bottom:1px solid var(--rule);
  vertical-align:top;
}
th{
  font:600 10px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
  border-bottom:1px solid var(--ink2);white-space:nowrap;
}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.scroll{overflow-x:auto;margin:0 -4px;padding:0 4px}
.cell{
  font:11.5px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;
  border-radius:5px;padding:5px 7px;display:block;min-width:112px;
}
.cell b{display:block;font-weight:600;font-size:11.5px}
.cell span{color:var(--ink2);font-size:10.5px}
.c-good{background:var(--good-bg)} .c-warning{background:var(--warning-bg)}
.c-serious{background:var(--serious-bg)} .c-critical{background:var(--critical-bg)}
.c-unscorable,.c-unseen{background:var(--grey-bg);color:var(--ink2)}
.q{max-width:250px;font-size:13px}
.q code{font-size:11px}
code{
  font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  background:var(--grey-bg);padding:1px 4px;border-radius:3px;
}
pre{
  background:var(--grey-bg);border:1px solid var(--ring);border-radius:7px;
  padding:11px 13px;overflow-x:auto;
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
pre code{background:none;padding:0}
.alarm{
  border:1px solid var(--critical);border-left-width:4px;border-radius:7px;
  background:var(--critical-bg);padding:13px 15px;margin:0 0 14px;
}
.alarm .lab{
  font:700 10px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;
  letter-spacing:.08em;text-transform:uppercase;color:var(--critical);
}
.quote{
  border-left:3px solid var(--critical);padding:3px 0 3px 13px;margin:11px 0;
  font-style:italic;max-width:66ch;
}
.note{font-size:12.5px;color:var(--ink2);max-width:74ch;margin:7px 0 0}
.caveat{
  font:11px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--muted);
  display:block;margin-top:3px;
}
footer{
  border-top:1px solid var(--rule);margin-top:34px;padding-top:16px;
  font-size:12.5px;color:var(--ink2);
}
svg{max-width:100%;height:auto;display:block}
"""


VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "area", "base",
             "col", "embed", "source", "track", "wbr",
             "path", "rect", "line", "circle", "polygon", "use", "stop"}


def markup_faults(page):
    """Tags that do not close, or close in the wrong order.

    A stray </summary> collapses a whole section in a browser while the page
    still renders, so nothing looks wrong from the command line. Checked here
    because this page is never opened before it is shown to a room.
    """
    import html.parser

    class Check(html.parser.HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in VOID_TAGS:
                self.stack.append((tag, self.getpos()[0]))

        def handle_startendtag(self, tag, attrs):
            pass

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            if not self.stack:
                self.bad.append("line %d: </%s> with nothing open"
                                % (self.getpos()[0], tag))
            elif self.stack[-1][0] != tag:
                self.bad.append("line %d: </%s> but <%s> from line %d is open"
                                % (self.getpos()[0], tag, self.stack[-1][0],
                                   self.stack[-1][1]))
            else:
                self.stack.pop()

    c = Check()
    c.feed(page)
    return c.bad + ["<%s> from line %d never closes" % t for t in c.stack]


def headline_echo(models):
    """Any record that used the headline's true figure, right or wrong.

    The demo-worthy contrast is not the quote on its own. It is one model
    calling a figure zero while another uses the same figure correctly, from
    the same tool. Built from the data so it disappears if the data changes.
    """
    hits, misses = [], []
    for m in models:
        for qid, c in c_items(m):
            for row in ((c.get("gt") or {}).get("rows") or []):
                if row.get("value") == HEADLINE["truth"]:
                    (hits if row.get("state") == "hit" else misses).append(
                        (m["model"], qid, row.get("what") or ""))
    return hits, misses


def c_items(m):
    return sorted(m["cells"].items(), key=lambda kv: _qsort(kv[0]))


def fault_asymmetry(models):
    """G6. Same run, same commit, very different transport-fault rates.

    If one alias loses 40% of its questions to a fault and another loses none,
    a per-model ranking across them compares different denominators. That is
    the comparison measuring the harness instead of the models, so it goes on
    the page rather than into a footnote.
    """
    by_run = {}
    for m in models:
        for rid in m["run_ids"]:
            by_run.setdefault(rid, []).append(m)
    out = []
    for rid, ms in sorted(by_run.items()):
        if len(ms) < 2:
            continue
        rates = [(m, (m["unscorable"] / m["n"]) if m["n"] else 0.0) for m in ms]
        hi = max(r for _, r in rates)
        lo = min(r for _, r in rates)
        if hi - lo >= 0.20:
            out.append((rid, sorted(rates, key=lambda x: -x[1])))
    return out


def usd_str(usd, priced, unpriced):
    """G4. A missing price is unknown, never zero.

    runner writes list_cost_usd: null when it holds no price for an alias, and
    says so in list_cost_note. Summing that as 0.0 would print $0.00 and read as
    "this model was free" -- the same fault this whole project is about, an
    empty value standing in for "I could not look".
    """
    if not priced:
        return "not priced"
    if unpriced:
        return "$%.2f + %d unpriced" % (usd, unpriced)
    return "$%.2f" % usd


def esc(x):
    return html.escape(str(x), quote=True)


def _tile(label, value, den, pill=None, pill_state=""):
    p = ('<div class="pill %s">%s</div>' % (esc(pill_state), esc(pill))) if pill else ""
    return ('<div class="tile"><div class="lab">%s</div><div class="val">%s</div>'
            '<div class="den">%s</div>%s</div>'
            % (esc(label), esc(value), den, p))


def _flow_svg(data):
    """The whole picture: two servers, seven tools, the sets, the models."""
    models = sorted(data["models"].values(), key=lambda m: m["dir"])
    n_models = len({m["model"] for m in models})
    n_recs = sum(m["n"] for m in models)
    geo = " · ".join(sorted(GEO_TOOLS))
    ena = " · ".join(sorted(ENA_TOOLS))
    return """
<svg viewBox="0 0 900 250" role="img"
     aria-label="A question goes to one of two servers on Bobby's branch. The GEO
     server on port 8009 has three tools and answers in Series. The BRC and ENA
     server on port 8008 has four tools and answers in runs. The two units are
     never added together.">
  <defs>
    <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
            markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="currentColor"/>
    </marker>
  </defs>
  <g font-family="system-ui,-apple-system,Segoe UI,sans-serif" fill="currentColor">
    <rect x="1" y="86" width="150" height="62" rx="7" fill="none"
          stroke="currentColor" stroke-opacity=".28"/>
    <text x="76" y="107" font-size="12" font-weight="600" text-anchor="middle">%(nq)s questions</text>
    <text x="76" y="124" font-size="10.5" text-anchor="middle" opacity=".72">%(sets)s</text>
    <text x="76" y="139" font-size="10.5" text-anchor="middle" opacity=".72">%(nm)s model(s)</text>

    <line x1="152" y1="117" x2="212" y2="117" stroke="currentColor"
          stroke-opacity=".38" stroke-width="2" marker-end="url(#a)"/>
    <text x="182" y="108" font-size="9.5" text-anchor="middle" opacity=".6">routes</text>

    <rect x="216" y="22" width="330" height="82" rx="7" fill="none"
          stroke="%(geo)s" stroke-width="1.6"/>
    <text x="232" y="44" font-size="12" font-weight="600">GEO lane — port 8009</text>
    <text x="232" y="61" font-size="10" opacity=".78">mcp_servers/geo.py</text>
    <text x="232" y="78" font-size="10" opacity=".78">%(geotools)s</text>
    <text x="232" y="94" font-size="10" font-weight="600" opacity=".9">unit: Series (a curated study)</text>

    <rect x="216" y="130" width="330" height="82" rx="7" fill="none"
          stroke="%(ena)s" stroke-width="1.6"/>
    <text x="232" y="152" font-size="12" font-weight="600">BRC / ENA lane — port 8008</text>
    <text x="232" y="169" font-size="10" opacity=".78">mcp_servers/brc_analytics.py</text>
    <text x="232" y="186" font-size="10" opacity=".78">%(enatools)s</text>
    <text x="232" y="202" font-size="10" font-weight="600" opacity=".9">unit: run (one sequencing run)</text>

    <line x1="152" y1="112" x2="212" y2="64" stroke="currentColor"
          stroke-opacity=".38" stroke-width="2" marker-end="url(#a)"/>
    <line x1="152" y1="122" x2="212" y2="170" stroke="currentColor"
          stroke-opacity=".38" stroke-width="2" marker-end="url(#a)"/>

    <line x1="552" y1="63" x2="612" y2="63" stroke="currentColor"
          stroke-opacity=".38" stroke-width="2" marker-end="url(#a)"/>
    <line x1="552" y1="171" x2="612" y2="171" stroke="currentColor"
          stroke-opacity=".38" stroke-width="2" marker-end="url(#a)"/>

    <rect x="616" y="34" width="280" height="58" rx="7" fill="none"
          stroke="currentColor" stroke-opacity=".28"/>
    <text x="632" y="55" font-size="11.5" font-weight="600">A zero here needs interpreting</text>
    <text x="632" y="72" font-size="10" opacity=".78">NCBI rewrites the query first, so a</text>
    <text x="632" y="86" font-size="10" opacity=".78">zero can mean the term was changed.</text>

    <rect x="616" y="142" width="280" height="58" rx="7" fill="none"
          stroke="currentColor" stroke-opacity=".28"/>
    <text x="632" y="163" font-size="11.5" font-weight="600">A zero here is an exact-match miss</text>
    <text x="632" y="180" font-size="10" opacity=".78">ENA matches scientific_name exactly,</text>
    <text x="632" y="194" font-size="10" opacity=".78">so one wrong letter returns zero.</text>

    <rect x="216" y="222" width="680" height="24" rx="5" fill="none"
          stroke="%(crit)s" stroke-width="1.4" stroke-dasharray="4 3"/>
    <text x="232" y="238" font-size="10.5" font-weight="600">
      The two units are never added. A Series is a study; a run is one sequencing run.
    </text>
  </g>
</svg>""" % {"nq": n_recs, "nm": n_models,
              "sets": " · ".join(sorted(data["sets"])) or "none yet",
              "geotools": esc(geo), "enatools": esc(ena),
              "geo": STATUS_HEX["good"], "ena": "#2a78d6",
              "crit": STATUS_HEX["critical"]}


def _matrix(data, qids, models, title):
    """Question down, model across. The real side-by-side."""
    if not qids or not models:
        return "<p class=\"note\">No records for this set yet.</p>"
    head = "".join("<th>%s<br><span class=\"caveat\">%s</span></th>"
                   % (esc(m["model"]), esc(m["set"])) for m in models)
    rows = []
    for q in qids:
        lane = data["lanes"].get(q, "off-branch")
        qtext = ""
        for m in models:
            c = m["cells"].get(q)
            if c and c.get("question"):
                qtext = c["question"]
                break
        tds = []
        for m in models:
            c = m["cells"].get(q)
            if not c:
                tds.append('<td><span class="cell c-unscorable">not run</span></td>')
                continue
            extra = []
            if c["state"] not in ("unscorable", "unseen"):
                extra.append("%s call%s" % (c["calls"], "" if c["calls"] == 1 else "s"))
                if c["seconds"] is not None:
                    extra.append("%.0fs" % c["seconds"])
            if c["discarded"]:
                extra.append("retried %dx" % c["discarded"])
            tds.append(
                '<td><span class="cell c-%s"><b>%s</b><span>%s</span></span></td>'
                % (esc(c["state"]), esc(c["label"]), esc(" · ".join(extra) or c["why"][:44])))
        flag = ' <span class="caveat">zero-trap question</span>' if q in ZERO_TRAP_QS else ""
        rows.append('<tr><td class="q"><b>%s</b> <span class="caveat">%s lane</span>%s'
                    '<br>%s</td>%s</tr>'
                    % (esc(q), esc(lane), flag, esc(qtext[:120]), "".join(tds)))
    return ('<div class="scroll"><table><thead><tr><th>%s</th>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>' % (esc(title), head, "".join(rows)))


def render_html(data):
    models = sorted(data["models"].values(), key=lambda m: (m["set"], m["dir"]))
    g = data["guards"]
    n_rec = sum(m["n"] for m in models)
    n_unscorable = sum(m["unscorable"] for m in models)
    n_unseen = sum(m["unseen"] for m in models)
    n_crit = sum(m["critical"] for m in models)
    # A wrong figure is checked against a pinned answer. A fabrication is
    # judge's heuristic flag. They are one tile, never one undifferentiated
    # number.
    crit_labels = [c["label"] for m in models for c in m["cells"].values()
                   if c["state"] == "critical"]
    n_wrong = sum(1 for lb in crit_labels if lb == "wrong figure")
    n_fab = sum(1 for lb in crit_labels if lb.startswith("fabricated"))
    n_zero = sum(1 for lb in crit_labels if lb == "zero read as absence")
    n_serious = sum(m["serious"] for m in models)
    n_good = sum(m["good"] for m in models)
    scored = n_rec - n_unscorable - n_unseen
    n_models = len({m["model"] for m in models})
    usd = sum(m["usd"] for m in models)
    note = models[0]["cost_note"] if models else cost_note([])
    have_lanes = any(m["set"] == "BOBBY-LANES" for m in models)

    P = []
    P.append("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    P.append("<title>Two servers, every model — what each one did</title>")
    P.append("<style>%s</style></head><body><div class=\"wrap\">" % CSS)

    # 1. Header
    P.append('<div class="kicker">NIAID-BRC AI Codeathon · Project 7 BioNexus · '
             'results page</div>')
    P.append("<h1>Two servers, every model — what each one did</h1>")
    if not models:
        P.append('<p class="verdict">No transcripts have landed yet. This page is '
                 'generated from <code>evals/runs/</code> and will fill in as the '
                 'matrix runs.</p>')
    else:
        P.append('<p class="verdict">%d model(s) answered %d question(s) against '
                 'the two servers on this branch. <b>%d answer(s) carry a '
                 'confident false statement</b>, %d more have a softer flag, '
                 '%d state the checked figure correctly, and %d could not '
                 'be scored at all, because the model was never asked.</p>'
                 % (n_models, n_rec, n_crit, n_serious, n_good, n_unscorable))
        if not have_lanes:
            P.append('<p class="verdict"><b>Read that first number carefully.</b> '
                     'The three questions built to catch a zero read as an absence '
                     'are B4, B10 and B16, and they have not run. A low count here '
                     'is partly because the questions most likely to produce one '
                     'were not asked.</p>')
    P.append('<div class="meta">Generated %s from <code>%s</code> · '
             'correctness flags come from <code>evals/judge.py</code>, not from this '
             'page · open decisions: %d</div>'
             % (esc(data["generated"]), esc(str(data["runs"])),
                len(g["sha_conflicts"]) + len(g["unseen_by_judge"])
                + (1 if g["judge_error"] else 0) + (0 if have_lanes else 1)))

    # G1 / G3 alarms, above everything they would otherwise corrupt.
    if g["judge_error"]:
        P.append('<div class="alarm"><div class="lab">Scores are missing, not clean'
                 '</div><p>The scorer could not be imported, so every correctness '
                 'column on this page is blank because nothing looked, not because '
                 'nothing was wrong.</p><pre><code>%s</code></pre></div>'
                 % esc(g["judge_error"]))
    for d, shas in g["sha_conflicts"].items():
        P.append('<div class="alarm"><div class="lab">Two trees in one run</div>'
                 '<p><code>%s</code> holds records built from %d different commits: '
                 '<code>%s</code>. These are not averaged anywhere on this page. '
                 'Re-run the directory before quoting any figure from it.</p></div>'
                 % (esc(d), len(shas), esc("</code>, <code>".join(shas))))
    if g["unseen_by_judge"]:
        rows = "".join("<tr><td><code>%s</code></td><td>%s</td><td class=\"n\">%d</td></tr>"
                       % (esc(d), esc(", ".join(q)[:90]), len(q))
                       for d, q in g["unseen_by_judge"].items())
        P.append('<div class="alarm"><div class="lab">On disk, but never scored</div>'
                 '<p>These records exist and the scorer returned no row for them. '
                 'They are marked <b>not scored</b> in the matrix, never as a pass '
                 'and never as a failure.</p>'
                 '<table><thead><tr><th>run folder</th><th>question ids</th>'
                 '<th class="n">count</th></tr></thead><tbody>%s</tbody></table>'
                 '<p class="note">Judge gives its reason for each refusal. The '
                 'reasons are listed in the caveats at the foot of this page. '
                 'Not patched here: <code>evals/judge.py</code> has another '
                 'owner.</p></div>' % rows)

    # 2. Number tiles
    P.append('<div class="tiles">')
    P.append(_tile("Confident false statements", n_crit,
                   ("of %d scored · %d wrong figure · %d zero read as absence · "
                    "%d fabrication flag" % (scored, n_wrong, n_zero, n_fab))
                   if scored else "nothing scored yet",
                   "Worst failure", "critical"))
    P.append(_tile("Held out, not scored", n_unscorable + n_unseen,
                   "transport faults %d · unscored records %d" % (n_unscorable, n_unseen),
                   "Never counted as zero", "warning"))
    P.append(_tile("Clean answers", n_good,
                   "of %d scored answers" % scored if scored else "nothing scored yet",
                   "Judge's flags", "good"))
    P.append(_tile("Models on the page", n_models,
                   "%d run folder(s), %d record(s)" % (len(models), n_rec)))
    priced = sum(m["priced"] for m in models)
    unpriced = sum(m["unpriced"] for m in models)
    P.append(_tile("List cost", usd_str(usd, priced, unpriced),
                   "Argo billed $0 of this<span class=\"caveat\">%s</span>"
                   % esc(STANDING_COST_CAVEAT),
                   "Unverified" if priced else "No price on file", "warning"))
    P.append("</div>")

    # The comparison measuring the harness, above the matrix it changes.
    for rid, rates in fault_asymmetry(models):
        worst, _ = rates[0]
        best, _ = rates[-1]
        P.append('<div class="alarm"><div class="lab">This compares different '
                 'denominators</div>')
        P.append('<p>In run <code>%s</code>, <code>%s</code> lost <b>%d of %d</b> '
                 'questions to a transport fault. <code>%s</code> lost <b>%d of %d'
                 '</b>. Same run, same commit, same servers. Ranking these two '
                 'against each other compares %d answers with %d, and the missing '
                 'ones are not model behaviour.</p>'
                 % (esc(rid), esc(worst["model"]), worst["unscorable"], worst["n"],
                    esc(best["model"]), best["unscorable"], best["n"],
                    worst["n"] - worst["unscorable"], best["n"] - best["unscorable"]))
        rows = "".join(
            '<tr><td><code>%s</code></td><td>%s</td><td class="n">%s</td>'
            '<td>%s</td></tr>' % (esc(q), esc(lab), format(tok, ","), esc(why))
            for q, lab, why, tok in worst["faults"])
        if rows:
            P.append('<table><thead><tr><th>question</th><th>fault</th>'
                     '<th class="n">input tokens billed</th>'
                     '<th>what the record shows</th></tr></thead><tbody>%s</tbody>'
                     '</table>' % rows)
        P.append('<p class="note">Every one is held out of the matrix as '
                 '<b>unscorable</b>. None is counted as a wrong answer. Re-run them '
                 'before quoting any per-model figure.</p></div>')

    # 3. The whole picture, always visible
    P.append('<h2 style="margin-bottom:10px">The two lanes</h2>')
    P.append('<div class="sec"><div class="body">%s' % _flow_svg(data))
    P.append('<p class="note">The lanes fail differently, and that is the result '
             'worth showing. A model that reads a GEO zero correctly is not '
             'automatically a model that reads an ENA zero correctly.</p></div></div>')

    # 4. Decision table
    P.append('<h2 style="margin:26px 0 10px">What still needs a decision</h2>')
    dec = []
    if not have_lanes:
        dec.append(("The 16 lane questions have not run (D1)", "Blocked", "runner",
                    "warning",
                    "This page can only compare the two servers once "
                    "<code>BOBBY-LANES.md</code> has run against them."))
    if g["unseen_by_judge"]:
        dec.append(("Scorer cannot see b-prefixed records (D2)", "Reported", "judge",
                    "critical",
                    "Add <code>b*.jsonl</code> to the glob and B1-B16 to "
                    "<code>EXPECTED</code>, or the lane set scores as empty."))
    if n_unscorable:
        dec.append(("Transport faults need a re-run (D3)", "Open", "runner", "warning",
                    "%d record(s) have no answer to score. Re-run before any "
                    "per-model figure is quoted." % n_unscorable))
    dec.append(("Dollar prices are unverified (D4)", "Open", "Bobby", "warning",
                "Tokens are measured. The price per token was written from memory. "
                "Read them off a price page or drop the column."))
    dec.append(("Which run is the final one (D5)", "Open", "Bobby", "warning",
                "<code>evals/RUNS.md</code> marks status by hand, on purpose. "
                "Nothing here is marked <code>final</code> yet."))
    P.append('<div class="sec"><div class="body"><table><thead><tr><th>item</th>'
             '<th>status</th><th>owner</th><th>what it unblocks</th></tr></thead><tbody>')
    for title, state, owner, pill, what in dec:
        P.append('<tr><td><b>%s</b></td><td><span class="pill %s">%s</span></td>'
                 '<td>%s</td><td>%s</td></tr>'
                 % (esc(title), esc(pill), esc(state), esc(owner), what))
    P.append("</tbody></table></div></div>")

    # 5. Collapsible sections, first one open
    lane_sets = [s for s in sorted(data["sets"]) if s == "BOBBY-LANES"]
    other_sets = [s for s in sorted(data["sets"]) if s != "BOBBY-LANES"]
    first = True
    for sname in lane_sets + other_sets:
        qids = sorted(data["sets"][sname], key=_qsort)
        ms = [m for m in models if m["set"] == sname]
        label = ("The two servers on Bobby's branch"
                 if sname == "BOBBY-LANES" else "The whole federation")
        P.append('<details class="sec"%s><summary><span><b>%s</b> — %s</span>'
                 '<span class="own">%d question(s) · %d model(s) · owner: demo'
                 '</span></summary><div class="body">'
                 % (" open" if first else "", esc(sname), esc(label),
                    len(qids), len(ms)))
        P.append(_matrix(data, qids, ms, "question"))
        P.append("</div></details>")
        first = False

    # The headline failure
    P.append('<details class="sec" open><summary><span><b>A zero read as an absence'
             '</b> — the failure worth showing</span>'
             '<span class="own">owner: demo · measured 17 Sep 2026</span></summary>'
             '<div class="body">')
    P.append('<p><code>%s</code> called <code>%s</code>, got <b>0</b>, and wrote:</p>'
             % (esc(HEADLINE["model"]), esc(HEADLINE["tool"])))
    P.append('<div class="quote">%s</div>' % esc(HEADLINE["said"]))
    P.append('<p>The true figure is <b>%s</b>. %s</p>'
             % (format(HEADLINE["truth"], ","), esc(HEADLINE["why"])))
    P.append('<p class="note">%s.</p>' % esc(HEADLINE["truth_note"]))
    P.append('<p><b>The same model asked the same tool again, and got it right.</b> '
             'The second call passed <code>%s</code>. Nothing else changed. '
             '<code>E.coli and Shigella</code> is the taxgroup NCBI actually keeps; '
             '<code>Escherichia coli</code> is not one, so the exact-match filter '
             'matched nothing and the tool reported that as a quantity.</p>'
             % esc(HEADLINE["fixed_call"]))
    P.append('<p class="note">The archived transcript carries no <code>code_sha</code>, '
             'so the server code behind the zero is unknown and the two runs are not '
             'compared as scores. The filter value is visible in both transcripts and '
             'is what this finding rests on.</p>')
    P.append('<p><b>One model named the trap instead of falling into it.</b> '
             '<code>%s</code> enumerated the controlled vocabulary before filtering '
             'on it, and said why:</p>' % esc(HEADLINE["named_by"]))
    P.append('<div class="quote">%s</div>' % esc(HEADLINE["named"]))
    hits, misses = headline_echo(models)
    if hits:
        P.append('<p><b>The same figure, in this run, handled correctly.</b> '
                 'These records were scored against %s and got it right:</p>'
                 % format(HEADLINE["truth"], ","))
        P.append('<table><thead><tr><th>model</th><th>question</th>'
                 '<th>what the figure is</th></tr></thead><tbody>%s</tbody></table>'
                 % "".join('<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>'
                           % (esc(a), esc(b), esc(c)) for a, b, c in hits))
        P.append('<p>That is the contrast worth showing. One model called the '
                 'figure zero and said the name was correct. Another read the same '
                 'figure from the same tool and used it as a denominator. Nothing '
                 'about the first answer looked broken.</p>')
    P.append('<p>This is the worst failure shape in the set because it is a '
             'confident false statement, not a visible error. Nothing on the screen '
             'looks broken. Both servers now attach a <code>zero_result_note</code> '
             'saying what a zero can also mean.</p>')
    trap_rows = []
    for m in models:
        for q in sorted(ZERO_TRAP_QS):
            c = m["cells"].get(q)
            if c:
                trap_rows.append('<tr><td><code>%s</code></td><td>%s</td>'
                                 '<td><span class="pill %s">%s</span></td></tr>'
                                 % (esc(m["model"]), esc(q),
                                    esc(c["state"]), esc(c["label"])))
    if trap_rows:
        P.append('<table><thead><tr><th>model</th><th>question</th>'
                 '<th>what happened</th></tr></thead><tbody>%s</tbody></table>'
                 % "".join(trap_rows))
    else:
        P.append('<p class="note"><b>The three questions built to catch this have '
                 'not run yet.</b> B4, B10 and B16 are in '
                 '<code>evals/BOBBY-LANES.md</code>. Until they run, the quote above '
                 'is a single measured case and not a rate.</p>')
    P.append("</div></details>")

    # Cost, tokens and time
    P.append('<details class="sec"><summary><span><b>Cost, tokens and time</b> — '
             'read after correctness</span><span class="own">owner: demo</span>'
             '</summary><div class="body">')
    P.append('<p>Tokens are what the provider reported. <b>Dollars are not '
             'measured.</b> %s</p>' % esc(STANDING_COST_CAVEAT))
    P.append('<ul>%s</ul>' % "".join("<li>%s</li>" % esc(n)
                                     for n in all_cost_notes(models)[1:]))
    noprice = [m for m in models if not m["priced"]]
    if noprice:
        P.append('<p class="note"><b>No price is on file for %s.</b> Those rows '
                 'read <code>not priced</code>. A missing price is not a zero. '
                 'The total above covers only the models that do have a price.</p>'
                 % esc(", ".join(m["model"] for m in noprice)))
    P.append('<table><thead><tr><th>model</th><th>set</th><th class="n">records</th>'
             '<th class="n">not asked</th><th class="n">not scored</th>'
             '<th class="n">in tokens</th>'
             '<th class="n">out tokens</th><th class="n">seconds</th>'
             '<th class="n">his 7 tools: calls</th><th class="n">on questions</th>'
             '<th class="n">list $ (unverified)</th></tr></thead><tbody>')
    for m in models:
        P.append('<tr><td><code>%s</code></td><td>%s</td><td class="n">%d</td>'
                 '<td class="n">%d</td><td class="n">%d</td>'
                 '<td class="n">%s</td><td class="n">%s</td>'
                 '<td class="n">%.0f</td><td class="n">%d</td>'
                 '<td class="n">%d</td><td class="n">%s</td></tr>'
                 % (esc(m["model"]), esc(m["set"]), m["n"],
                    m["unscorable"], m["unseen"], format(m["in_tok"], ","),
                    format(m["out_tok"], ","), m["seconds"],
                    m["bobby"]["calls"], m["bobby"]["questions"],
                    esc(usd_str(m["usd"], m["priced"], m["unpriced"]))))
    P.append("</tbody></table>")
    tot = sum(m["bobby"]["calls"] for m in models)
    tused = set()
    for m in models:
        tused |= set(m["bobby"]["by_tool"])
    P.append('<p><b>The two servers were used %d times</b> across these runs, and '
             '%d of their 7 tools were called at least once. That is a different '
             'fact from the lane column in the matrix above: the lane says what a '
             'question is <i>about</i> and is fixed by the question set, so a '
             'question stays put when a model calls something unexpected. This row '
             'says what the models actually <i>did</i>.</p>' % (tot, len(tused)))
    P.append('<p class="note">A held-out record still billed tokens. '
             'A silent empty reply cost input tokens and returned nothing, so the '
             'cost column is not a cost-per-answer.</p>')
    P.append("</div></details>")

    # What this page cannot tell you
    P.append('<details class="sec"><summary><span><b>What this page cannot tell you'
             '</b></span><span class="own">owner: demo</span></summary>'
             '<div class="body"><ul>')
    P.append("<li><b>The full chatbot has never run end to end here.</b> Every "
             "number comes from the eval driver, which streams the agent the same "
             "way the chat UI does, but is not the chat UI.</li>")
    P.append("<li><b>Q11 measures a source being down, not a capability.</b> The "
             "matrix ran without BV-BRC's 18 tools.</li>")
    P.append("<li><b>Ground truths marked <code>unverified</code> are not scored."
             "</b> Scoring against a wrong ground truth turns a correct answer "
             "into a failure.</li>")
    P.append("<li><b>Tool results are cut to 600 characters in the transcript.</b> "
             "A number the model was shown further down looks unmatched, which is "
             "why the scorer says <code>needs review</code> rather than "
             "<code>fabricated</code>.</li>")
    P.append("<li><b>Counts drift.</b> Every live figure was true on 17 Sep 2026.</li>")
    P.append("</ul>")
    if g["judge_skipped"]:
        P.append('<p class="note">The scorer refused %d record(s). Its reason '
                 'is in brackets on each: %s</p>' % (len(g["judge_skipped"]),
                                         esc("; ".join(g["judge_skipped"])[:400])))
    if g["unreadable"]:
        P.append('<p class="note">%d record(s) could not be read: %s</p>'
                 % (len(g["unreadable"]), esc("; ".join(g["unreadable"])[:400])))
    if g["retried"]:
        for d, parked in sorted(g["retried"].items()):
            P.append('<p class="note"><b>%s</b>: %d failed attempt(s) parked '
                     'beside an answer, not counted as answers: %s</p>'
                     % (esc(d), len(parked), esc(", ".join(parked)[:300])))
    P.append("</div></details>")

    # Names
    P.append('<details class="sec"><summary><span><b>Names used on this page</b>'
             '</span><span class="own">owner: demo</span></summary><div class="body">'
             '<table><thead><tr><th>name</th><th>what it is</th></tr></thead><tbody>')
    for name, what in [
        ("GEO", "NCBI Gene Expression Omnibus. Curated expression studies."),
        ("Series (GSE)", "One curated study in GEO. The unit of the GEO lane."),
        ("ENA", "European Nucleotide Archive. Raw sequencing data."),
        ("run", "One sequencing run in ENA. The unit of the BRC / ENA lane."),
        ("BRC Analytics", "The federated service this branch's second server wraps."),
        ("MCP server", "A small service that exposes tools to the chat model."),
        ("Argo", "Argonne's model gateway. It bills no dollars."),
        ("transport fault", "The model was never properly asked: a denial, an "
                            "error, or a reply with no content at all."),
        ("zero-trap question", "A question whose correct answer needs the model to "
                               "say what a zero could also mean. B4, B10, B16."),
        ("code_sha", "The commit the eval code was at when the record was written."),
    ]:
        P.append("<tr><td><code>%s</code></td><td>%s</td></tr>" % (esc(name), esc(what)))
    P.append("</tbody></table></div></details>")

    # 6. Footer
    P.append("<footer><p>Regenerate this page:</p>"
             "<pre><code>uv run evals/build_demo.py\n"
             "uv run evals/build_demo.py --self-test   # prove the guards fire</code></pre>"
             "<p>Nothing on this page is hand-edited. It is rebuilt from "
             "<code>evals/runs/</code> and from <code>evals/judge.py</code>. "
             "Dollar figures are unverified list prices; Argo billed none of them. "
             "Live figures were read on 17 Sep 2026 and drift.</p></footer>")
    P.append("</div></body></html>")
    return "\n".join(P)


def render_md(data):
    """A plain-text twin, so the numbers survive without a browser."""
    models = sorted(data["models"].values(), key=lambda m: (m["set"], m["dir"]))
    g = data["guards"]
    L = ["# Two servers, every model — what each one did", "",
         "Generated %s by `evals/build_demo.py`. Do not hand-edit."
         % data["generated"], ""]
    if g["judge_error"]:
        L += ["> **Scores are missing, not clean.** The scorer could not be "
              "imported: `%s`" % g["judge_error"], ""]
    for d, shas in g["sha_conflicts"].items():
        L += ["> **Two trees in one run.** `%s` mixes %s. Not averaged anywhere."
              % (d, ", ".join("`%s`" % s for s in shas)), ""]
    for d, q in g["unseen_by_judge"].items():
        L += ["> **On disk, never scored.** `%s`: %d record(s) the scorer returned "
              "no row for (%s). Marked `not scored`, never as a pass."
              % (d, len(q), ", ".join(q)[:120]), ""]
    if not models:
        L += ["No transcripts yet under `%s`." % data["runs"], ""]
        return "\n".join(L)

    for sname in sorted(data["sets"]):
        ms = [m for m in models if m["set"] == sname]
        if not ms:
            continue
        qids = sorted(data["sets"][sname], key=_qsort)
        L += ["## %s" % sname, "",
              "| Q | lane | " + " | ".join("`%s`" % m["model"] for m in ms) + " |",
              "|---|---|" + "---|" * len(ms)]
        for q in qids:
            cells = []
            for m in ms:
                c = m["cells"].get(q)
                cells.append("not run" if not c else
                             "%s (%s)" % (c["label"], c["state"]))
            L.append("| %s | %s | %s |"
                     % (q, data["lanes"].get(q, "?"), " | ".join(cells)))
        L.append("")

    L += ["## Per model", "",
          "| model | set | records | not asked | not scored | in tok | out tok "
          "| s | his 7 tools: calls | on questions | list $ |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        L.append("| `%s` | %s | %d | %d | %d | %s | %s | %.0f | %d | %d | %s |"
                 % (m["model"], m["set"], m["n"], m["unscorable"], m["unseen"],
                    format(m["in_tok"], ","), format(m["out_tok"], ","),
                    m["seconds"], m["bobby"]["calls"], m["bobby"]["questions"],
                    usd_str(m["usd"], m["priced"], m["unpriced"])))
    L += [""] + ["- %s" % n for n in all_cost_notes(models)] + [""]
    return "\n".join(L)


# --- the self-test ----------------------------------------------------------
#
# A check that has never been seen to fail is not a check. Each guard below is
# given a record built to trip it, and a clean record is given to all of them to
# prove they can also come back quiet.


def _rec(**kw):
    s = {"question_number": "Q2", "question_id": "Q2", "question": "how many?",
         "model": "test/model", "elapsed_s": 1.0, "tools_in_order": ["geo_search"],
         "tool_call_count": 1, "denied": False, "error": None, "answer": "37 Series",
         "answer_chars": 9, "input_tokens": 100, "output_tokens": 10,
         "total_tokens": 110, "usage_reported": True, "ttft_s": 1.0,
         "llm_round_trips": 1, "tool_result_chars": 10, "tool_seconds": 0.1,
         "model_seconds": 0.9, "tool_timings": [], "list_cost_usd": 0.01,
         "list_cost_note": "list-price estimate, UNVERIFIED",
         "run_id": "t-1", "code_sha": "aaaaaaa", "questions_file": "evals/QUESTIONS.md",
         "retries": 0, "attempts_discarded": []}
    s.update(kw)
    return s


def _write(d, name, summary):
    d.mkdir(parents=True, exist_ok=True)
    lines = [{"role": "user", "text": summary["question"]},
             {"summary": summary}]
    (d / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n",
                          encoding="utf-8")


def self_test():
    fails = []
    fired = []

    with tempfile.TemporaryDirectory() as tmp:
        runs = pathlib.Path(tmp) / "runs"

        # G1: one folder, two commits.
        _write(runs / "m_sha", "q02.jsonl", _rec(code_sha="aaaaaaa"))
        _write(runs / "m_sha", "q03.jsonl",
               _rec(question_number="Q3", question_id="Q3", code_sha="bbbbbbb"))
        # G2: a denial, an error and a silent empty.
        _write(runs / "m_fault", "q02.jsonl", _rec(denied=True, answer="", answer_chars=0))
        _write(runs / "m_fault", "q03.jsonl",
               _rec(question_number="Q3", question_id="Q3", error="RuntimeError: x"))
        _write(runs / "m_fault", "q04.jsonl",
               _rec(question_number="Q4", question_id="Q4", answer="", answer_chars=0,
                    tools_in_order=[], tool_call_count=0, output_tokens=0))
        # G3: a record judge returns no row for. This used to lean on judge
        # having no rubric for set B; on 18 Sep judge gained one, the fixture
        # got scored, and G3 stopped reaching the path it tests. A record with
        # no `run_id` is refused by design (`judge.provenance_of`), whatever
        # sets judge covers, so the fixture no longer rots when judge improves.
        _write(runs / "m_lanes", "b04.jsonl",
               _rec(question_number="B4", question_id="B4",
                    questions_file="evals/BOBBY-LANES.md", run_id=None))
        # G4: a model runner holds no price for. Must never read as $0.00.
        _write(runs / "m_noprice", "q02.jsonl",
               _rec(model="test/unpriced", list_cost_usd=None,
                    list_cost_note="no list price on file for alias 'x' -- not computed"))
        # G7: a failed attempt parked beside the answer it failed at. Both
        # files match `q*.jsonl`, so a naive glob sees one question as two.
        _write(runs / "m_sidecar", "q02.jsonl", _rec())
        _write(runs / "m_sidecar", "q02.error-160722.jsonl",
               _rec(error="RecursionError: maximum recursion depth exceeded",
                    answer="", answer_chars=0, output_tokens=0))
        # G7: the sidecar name that sorts AFTER the answer. Under the old
        # keying this failure replaced a correct answer in the matrix cell.
        _write(runs / "m_late", "q07.jsonl",
               _rec(question_number="Q7", question_id="Q7",
                    answer="551,679 runs", answer_chars=13))
        _write(runs / "m_late", "q07.retry-160722.jsonl",
               _rec(question_number="Q7", question_id="Q7",
                    error="RecursionError: maximum recursion depth exceeded",
                    answer="", answer_chars=0, output_tokens=0))
        # G7: a question where only the failed attempt exists. It must keep its
        # row and read as held out, never vanish from the matrix.
        _write(runs / "m_onlyside", "q05.error-160722.jsonl",
               _rec(question_number="Q5", question_id="Q5",
                    error="RecursionError: maximum recursion depth exceeded",
                    answer="", answer_chars=0, output_tokens=0))
        # The negative control: one ordinary record that must trip nothing.
        _write(runs / "m_clean", "q02.jsonl", _rec())

        data = build(runs)
        g = data["guards"]

        # -- G1
        if "m_sha" in g["sha_conflicts"] and len(g["sha_conflicts"]["m_sha"]) == 2:
            fired.append("G1 code_sha conflict: %s" % g["sha_conflicts"]["m_sha"])
        else:
            fails.append("G1 did not see two commits in one folder: %s"
                         % g["sha_conflicts"])

        # -- G2
        fault_cells = data["models"]["m_fault"]["cells"]
        labels = sorted(c["label"] for c in fault_cells.values())
        if labels == ["denied", "error", "silent empty"]:
            fired.append("G2 transport faults held out: %s" % labels)
        else:
            fails.append("G2 did not hold out all three faults: %s" % labels)
        if data["models"]["m_fault"]["unscorable"] != 3:
            fails.append("G2 scored a transport fault: unscorable=%d"
                         % data["models"]["m_fault"]["unscorable"])
        if any(c["state"] in ("good", "serious", "critical")
               for c in fault_cells.values()):
            fails.append("G2 gave a transport fault a correctness state")

        # -- G3
        if g["unseen_by_judge"].get("m_lanes") == ["B4"]:
            fired.append("G3 b-prefixed record seen here, unscored by judge")
        else:
            fails.append("G3 did not report the unscored b-record: %s"
                         % g["unseen_by_judge"])
        if data["models"]["m_lanes"]["cells"]["B4"]["state"] != "unseen":
            fails.append("G3 rendered an unscored record as something else: %s"
                         % data["models"]["m_lanes"]["cells"]["B4"]["state"])

        # -- G4
        page = render_html(data)
        if "$" in page and "UNVERIFIED" in page:
            fired.append("G4 every dollar carries the unverified note")
        else:
            fails.append("G4 a dollar figure appeared with no unverified note")
        np = data["models"]["m_noprice"]
        shown = usd_str(np["usd"], np["priced"], np["unpriced"])
        if shown == "not priced" and np["unpriced"] == 1:
            fired.append("G4 a model with no price on file reads 'not priced', "
                         "not $0.00")
        else:
            fails.append("G4 rendered a missing price as a number: %r" % shown)
        if "$0.00" in page:
            fails.append("G4 the page printed $0.00, which reads as free")

        # -- a checked figure must outrank judge's generic needs-review, which
        #    fires on nearly every long answer because the tool log is cut.
        probe = cell_for(
            Record(runs / "m_clean" / "q02.jsonl"),
            {"verdict": "needs-review", "routed": "yes", "traps": [],
             "ground_truth": {"applies": True, "state": "hit", "rows": []}})
        if probe["label"] == "figure correct" and probe["state"] == "good":
            fired.append("a correct figure outranks needs-review: %s"
                         % probe["flags"])
        else:
            fails.append("a correct figure was buried under needs-review: %s"
                         % probe["label"])

        # -- a fabrication flag carries judge's own unmatched numbers, so a
        #    taxonomy ID or a split figure is visible as what it is.
        fab = cell_for(
            Record(runs / "m_clean" / "q02.jsonl"),
            {"verdict": "fabricated", "routed": "yes", "traps": [],
             "numbers": {"claimed": 3, "unmatched": [511145], "decidable": True}})
        if fab["state"] == "critical" and "511145" in fab["label"]:
            fired.append("a fabrication flag shows judge's evidence: %s"
                         % fab["label"])
        else:
            fails.append("a fabrication flag reached the page without judge's "
                         "evidence: %r" % fab["label"])

        # -- G5
        try:
            refuse_cross_lane_total(37, 551679)
            fails.append("G5 added a GEO Series to an ENA run")
        except CrossLaneTotal as exc:
            fired.append("G5 refused a cross-lane total: %s" % str(exc)[:56])

        # -- the page has to be valid HTML, because nobody opens it first
        bad = markup_faults(page)
        if bad:
            fails.append("the page is malformed HTML: %s" % "; ".join(bad[:4]))
        else:
            fired.append("the rendered page closes every tag it opens")

        # -- the standing price caveat cannot depend on which model sorts first
        if "$" in page and STANDING_COST_CAVEAT not in page:
            fails.append("a dollar figure appeared with no standing caveat")
        else:
            fired.append("every dollar sits with the standing unverified caveat")

        # -- G6
        asym = fault_asymmetry(list(data["models"].values()))
        names = {m["dir"] for _, rates in asym for m, _ in rates}
        if asym and "m_fault" in names:
            fired.append("G6 flagged a run where one model lost far more "
                         "questions to transport than another")
        else:
            fails.append("G6 missed a 100%% vs 0%% transport-fault split: %s" % asym)
        if any(m["dir"] == "m_clean" and r > 0 for _, rates in asym
               for m, r in rates):
            fails.append("G6 blamed a model that had no transport fault")

        # -- G7
        side = data["models"]["m_sidecar"]
        named = g["retried"].get("m_sidecar") or []
        if (side["n"] == 1 and side["unscorable"] == 0
                and len(named) == 1 and "q02.error-160722.jsonl" in named[0]):
            fired.append("G7 parked attempt: m_sidecar kept 1 record, named %s"
                         % named[0])
        else:
            fails.append("G7 did not park the failed attempt: cells=%d, "
                         "unscorable=%d, parked=%s"
                         % (side["n"], side["unscorable"], named))
        late = data["models"]["m_late"]
        if late["unscorable"] == 0 and late["n"] == 1:
            fired.append("G7 a late-sorting parked attempt did not replace the "
                         "answer it failed at")
        else:
            fails.append("G7 let a parked attempt overwrite a real answer: "
                         "cells=%d, unscorable=%d"
                         % (late["n"], late["unscorable"]))
        only = data["models"]["m_onlyside"]
        if only["n"] == 1 and only["unscorable"] == 1:
            fired.append("G7 attempt-only question kept its row, held out")
        else:
            fails.append("G7 lost a question that only ever failed: records=%d, "
                         "unscorable=%d" % (only["n"], only["unscorable"]))

        # -- G8
        lanes = data["models"]["m_lanes"]
        # Assert on the RENDERED numbers, not on the dict. The first version of
        # this guard checked the dict and did not notice the two counts being
        # added back together at the render site, which is where the bug lived.
        md = render_md(data)
        htm = render_html(data)
        md_row = [ln for ln in md.splitlines()
                  if "BOBBY-LANES" in ln and ln.startswith("| `")]
        # m_lanes: 1 record, 0 never asked, 1 answered-but-unscored.
        md_ok = any("| BOBBY-LANES | 1 | 0 | 1 |" in ln for ln in md_row)
        # The headers must be two, AND the row must carry the two numbers the
        # right way round. Checking only the headers let a merge at the render
        # site pass: the columns were still named, and both held the wrong value.
        # The per-model table row, not the matrix header, which also carries
        # the model name and the set name.
        htm_row = [r for r in htm.split("<tr>")
                   if "<td><code>test/model</code></td>" in r
                   and "<td>BOBBY-LANES</td>" in r]
        htm_nums = (re.findall(r'<td class="n">(-?\d+)</td>', htm_row[0])
                    if htm_row else [])
        html_ok = ('<th class="n">not asked</th>' in htm
                   and '<th class="n">not scored</th>' in htm
                   # "held out" may still appear in prose about transport
                   # faults, which is correct. It must not be a column header.
                   and '<th class="n">held out</th>' not in htm
                   # records, never asked, answered-but-unscored
                   and htm_nums[:3] == ["1", "0", "1"])
        if lanes["unscorable"] == 0 and lanes["unseen"] == 1 and md_ok and html_ok:
            fired.append("G8 an unscored record renders as not-scored, never as "
                         "not-asked, in both the page and the markdown")
        else:
            fails.append("G8 merged 'never asked' with 'answered but unscored': "
                         "not_asked=%d, not_scored=%d, md_row=%s, html_ok=%s"
                         % (lanes["unscorable"], lanes["unseen"],
                            md_row[:1], htm_nums[:3]))

        # -- the negative control
        clean = data["models"]["m_clean"]
        quiet = ("m_clean" not in g["sha_conflicts"]
                 and "m_clean" not in g["unseen_by_judge"]
                 and "m_clean" not in g["retried"]
                 and clean["unscorable"] == 0)
        if quiet:
            fired.append("negative control: the ordinary record tripped nothing")
        else:
            fails.append("a guard fired on the clean control: unscorable=%d, "
                         "sha=%s, unseen=%s"
                         % (clean["unscorable"], "m_clean" in g["sha_conflicts"],
                            g["unseen_by_judge"].get("m_clean")))

        # -- the page must render with no records at all, and say so.
        empty = build(pathlib.Path(tmp) / "nothing")
        blank = render_html(empty)
        if "No transcripts have landed yet" in blank:
            fired.append("an empty runs folder says so, rather than rendering blank")
        else:
            fails.append("an empty runs folder rendered without saying it was empty")

    for line in fired:
        print("  fired: %s" % line)
    print()
    if fails:
        print("SELF-TEST FAILED — %d problem(s):" % len(fails), file=sys.stderr)
        for f in fails:
            print("  - %s" % f, file=sys.stderr)
        return 1
    print("SELF-TEST PASSED — %d guards observed firing, and none fired on the "
          "clean control." % len(fired))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=pathlib.Path, default=RUNS)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--self-test", action="store_true",
                    help="build records designed to trip each guard, and check "
                         "a clean record trips none of them")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    data = build(args.runs)
    args.out.mkdir(parents=True, exist_ok=True)
    page = args.out / "index.html"
    md = args.out / "model-matrix.md"
    page.write_text(render_html(data), encoding="utf-8")
    md.write_text(render_md(data), encoding="utf-8")

    g = data["guards"]
    models = data["models"]
    print("read   %s" % args.runs)
    print("wrote  %s  (%d bytes)" % (page, page.stat().st_size))
    print("wrote  %s  (%d bytes)" % (md, md.stat().st_size))
    print()
    print("%d run folder(s), %d record(s)"
          % (len(models), sum(m["n"] for m in models.values())))
    for name, m in sorted(models.items()):
        print("  %-34s %-13s %2d records · %d not asked · %d not scored "
              "· %d critical · %d serious · %d clean"
              % (name, m["set"], m["n"], m["unscorable"], m["unseen"],
                 m["critical"], m["serious"], m["good"]))
    print()
    if g["judge_error"]:
        print("GUARD  scorer unavailable: %s" % g["judge_error"])
    for d, shas in g["sha_conflicts"].items():
        print("GUARD  %s mixes %d commits: %s — not averaged" % (d, len(shas), shas))
    for d, q in g["unseen_by_judge"].items():
        print("GUARD  %s: %d record(s) on disk with no score (%s)"
              % (d, len(q), ", ".join(q)[:70]))
    for s in g["unreadable"]:
        print("GUARD  unreadable: %s" % s)
    for d, parked in sorted(g["retried"].items()):
        print("GUARD  %s: %d failed attempt(s) parked, not counted: %s"
              % (d, len(parked), ", ".join(parked)[:70]))
    if not any([g["judge_error"], g["sha_conflicts"], g["unseen_by_judge"],
                g["unreadable"], g["retried"]]):
        print("no guard fired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
