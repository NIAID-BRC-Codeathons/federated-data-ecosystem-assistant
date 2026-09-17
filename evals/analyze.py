"""Turn the recorded transcripts into the tables behind `evals/FINDINGS.md`.

`run_questions.py` records what each model did. `judge.py` says whether each
transcript routed, fabricated or refused. Neither answers the question this script
exists for: **what differs between models, and is the difference real?**

    uv run evals/analyze.py                        # print every table
    uv run evals/analyze.py --write                # splice them into FINDINGS.md
    uv run evals/analyze.py --runs evals/runs
    uv run evals/analyze.py --only-model argo_gpt4o

Stdlib only. No network, no key, no model. Reads `evals/runs/<model>/qNN.jsonl`,
`evals/QUESTIONS.md` and `evals/judge-report.md`; writes between the two marker
comments in `evals/FINDINGS.md` and touches nothing else in that file.

## The division of labour, so two rubrics do not grow here

`judge.py` owns correctness: routing, fabrication, traps, refusal. This script owns
what the numbers *mean* across models: cost curves, chain depth against the
documented chain, the plateau, the transport comparison, variance.

**Fabrication is not re-implemented.** It is read out of `judge-report.md`. Two
fabrication rules that disagree would be worse than one. `reached` *is* computed
here as well, deliberately, as a cross-check on judge's `routed` -- and where the
two disagree the disagreement is printed rather than resolved, because a definition
two implementations read differently is a loose definition.

## Why this parses QUESTIONS.md instead of hardcoding the chains

The expected tools and the documented chain length come from the step tables in
`QUESTIONS.md`, parsed at run time. A hardcoded copy would drift silently the first
time someone edits a chain. The parse is checked against `CHAIN_LEN_ASSERT` below
and **raises** on a mismatch or on an empty parse, because an empty expected-tool
set would otherwise make every model score zero and look like a model finding.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parent.parent
RUNS = REPO / "evals" / "runs"
QUESTIONS_MD = REPO / "evals" / "QUESTIONS.md"
JUDGE_REPORT = REPO / "evals" / "judge-report.md"
FINDINGS = REPO / "evals" / "FINDINGS.md"

BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# EVERYTHING EDITABLE IS BETWEEN HERE
# ---------------------------------------------------------------------------

# The number of steps each question's table in QUESTIONS.md has, as read by a
# human on 17 Sep 2026. The parser below must agree; if it does not, one of the
# two is stale and the script stops rather than scoring against a guess.
# Q15 has no step table on purpose: no tool on this board can answer it.
CHAIN_LEN_ASSERT = {1: 2, 2: 2, 3: 1, 4: 3, 5: 3, 6: 2, 7: 5, 8: 4,
                    9: 3, 10: 4, 11: 3, 12: 4, 13: 0, 14: 0, 15: 0}

# Question classes. `gap` questions have no answer to route to; `unwired` ones
# have a right answer whose server is not in MCP_SERVERS, so a routing failure
# there belongs to the repo and not to the model (QUESTIONS.md Q10, Q11).
CLASS = {**{q: "answerable" for q in range(1, 10)},
         10: "unwired", 11: "unwired", 12: "answerable",
         13: "gap", 14: "gap", 15: "gap"}

# Tiers are MY grouping of the Argo catalogue in _reports/argo-model-matrix.md,
# put here so they can be argued with rather than inferred from the prose.
TIER_RULES = [
    ("small",     ("nano", "mini", "haiku", "flashlite", "35flash", "25flash")),
    ("reasoning", ("gpto1", "gpto3", "gpto4", "gpt5", "opus5", "opus48", "opus47",
                   "opus46", "sonnet5", "25pro")),
    ("frontier",  ("gpt4o", "gpt41", "opus45", "opus41", "sonnet45", "sonnet46",
                   "gpt51", "gpt52", "gpt54", "gpt55", "gpt56")),
]
DEFAULT_TIER = "unclassified"

# tool-name prefix -> the server it lives on. Used for "did it touch a source
# that cannot answer this" and for counting distinct servers in a chain. BRC's
# tools have no shared prefix, so they are named. Anything unmatched is reported
# as `unknown` in its own line -- never dropped, because a silently dropped tool
# would make an off-source call invisible.
SERVER_PREFIX = {"geo_": "geo", "ncbi_": "ncbi", "uniprot_": "uniprot",
                 "mygene_": "mygene", "myvariant_": "myvariant", "lapis_": "pdn",
                 "nde_": "nde", "string_": "string", "pubmed_": "pubmed",
                 "expasy_": "expasy", "sparql": "expasy", "brc_": "brc_analytics"}
BRC_TOOLS = {"search_organisms", "get_assemblies", "get_compatible_workflows",
             "check_compatibility", "resolve_workflow_inputs", "get_workflow_details",
             "search_ena", "search_ena_keywords", "list_workflows",
             "get_organism_details", "get_assembly_details", "brc_federation_status",
             # added 17 Sep after the unknown-tool check caught them in argo_gpt4o/q04
             "list_workflow_categories", "get_workflows_in_category"}

# D5. Each trap is one measurable thing, from README.md's table of what the
# obvious call returns. `arg` traps need the model to know an argument exists;
# `credulity` traps need it to disbelieve a number a tool just handed it. The
# split is a hypothesis in FINDINGS.md D5 and this is where it is measured.
TRAPS = {
    "ena_keywords":       ("credulity", "called `search_ena_keywords`; BRC returns an ENA 400 as tool *text*"),
    "geo_no_entry_type":  ("arg", "`geo_search` with no `entry_type`; the count then mixes GSE, GSM, GDS, GPL"),
    "gds_513":            ("credulity", "quoted **513**, the unfiltered `db=gds` count, as a Series count (37 is right)"),
    "ena_50":             ("credulity", "quoted **50** as an ENA total; that is the federated page size"),
    "pathogen_wrong_group": ("arg", "an `organism` outside the 106 curated groups; returns 0, not an error"),
}
# Any of these as a Pathogen Detection `organism` returns a confident 0.
WRONG_PATHOGEN_GROUPS = {"escherichia coli", "e. coli", "e.coli", "e coli",
                         "shigella", "shigella flexneri", "shigella sonnei", "escherichia"}

# D4, the four parts of a good refusal, from PIPELINES.md P8.
REFUSAL_MARKERS = ("cannot", "can not", "can't", "unable", "not available", "no tool",
                   "not wired", "not connected", "not indexed", "malformed", "does not hold",
                   "not recognised", "not recognized", "no source", "out of scope",
                   "not possible", "does not carry", "not exposed", "is not a recognised",
                   "is not a recognized")
REASON_MARKERS = ("because", "since", "the reason", "mediated by", "staphylococcal",
                  "different pipeline", "not in mcp_servers", "stdio", "read-only",
                  "is a row count", "is not", "which is why", "so the")
REFRAME_MARKERS = ("instead", "did you mean", "you probably want", "the question you",
                   "nearest", "reframe", "rephrase", "closest answerable", "if you meant",
                   "what you may want", "alternatively")
ALT_SOURCES = ("bv-brc", "bvbrc", "card", "rcsb", "protein data bank", "pdb",
               "alphafold", "ena ", "bigquery", "galaxy")
# The number that proves the refusal, per gap question, from QUESTIONS.md.
PROOF_NUMBERS = {13: {2, 581464, 93260, 171412, 94336},
                 14: {1548, 9036, 581464, 378},
                 15: set(),          # nothing on the board; the proof is naming RCSB
                 10: {3644, 3401, 60107},
                 11: {118625, 0}}

# ---------------------------------------------------------------------------
# AND HERE. Below this line is machinery.
# ---------------------------------------------------------------------------

_Q_HEADER = re.compile(r"^## Q(\d+)\.")
_STEP_ROW = re.compile(r"^\|\s*\d+\s*\|")
_BACKTICKED = re.compile(r"`([a-z][a-z0-9_]{3,})`")
_NUM_IN_TEXT = re.compile(r"\d[\d,]*")


def parse_chains(path: pathlib.Path) -> dict[int, list[str]]:
    """The tool chain for each question, read from its step table in QUESTIONS.md.

    A row is `| 1 | `tool` | args |` or `| 1 | server | `tool` | args |`. The tool
    is the first backticked snake_case identifier on the row, which works for both
    shapes because the server column is plain text.
    """
    chains: dict[int, list[str]] = {}
    current: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _q_header(line)
        if m is not None:
            current = m
            chains.setdefault(current, [])
            continue
        if current is None or not _STEP_ROW.match(line):
            continue
        # `arguments` cells are full of backticked things too; only the first
        # identifier on the row is the tool, and argument cells start with `name=`.
        found = _BACKTICKED.search(line)
        if found and "=" not in line[: found.end()]:
            chains[current].append(found.group(1))
    return chains


def _q_header(line: str) -> int | None:
    m = _Q_HEADER.match(line)
    return int(m.group(1)) if m else None


def check_chains(chains: dict[int, list[str]]) -> None:
    """Stop if the parse and the human reading disagree, or if it came back empty.

    An empty expected-tool set scores every model zero on routing and looks like a
    finding about models. It is not; it is a parser that stopped seeing.
    """
    problems = []
    for q, expected_len in CHAIN_LEN_ASSERT.items():
        got = chains.get(q)
        if got is None:
            problems.append(f"Q{q}: no `## Q{q}.` header found in {QUESTIONS_MD.name}")
        elif len(got) != expected_len:
            problems.append(f"Q{q}: parsed {len(got)} steps {got}, "
                            f"CHAIN_LEN_ASSERT says {expected_len}")
    if problems:
        raise SystemExit(
            "QUESTIONS.md and CHAIN_LEN_ASSERT disagree -- one of them is stale.\n  "
            + "\n  ".join(problems)
            + "\n\nFix the file or the constant; do not score against a guess.")


def server_of(tool: str) -> str:
    if tool in BRC_TOOLS:
        return "brc_analytics"
    for prefix, server in SERVER_PREFIX.items():
        if tool.startswith(prefix):
            return server
    return "unknown"


def tier_of(model: str) -> str:
    low = model.lower()
    for tier, needles in TIER_RULES:
        if any(n in low for n in needles):
            return tier
    return DEFAULT_TIER


class Run:
    """One qNN.jsonl, reduced to the fields the dimensions need."""

    def __init__(self, path: pathlib.Path, model: str, chains: dict[int, list[str]]):
        self.path = path
        self.model = model
        self.tier = tier_of(model)
        self.steps: list[dict] = []
        self.summary: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            (self.summary.update(obj["summary"]) if "summary" in obj
             else self.steps.append(obj))

        # `question_number` is an int in the base matrix and the string "Q2" in
        # runs from the patched driver (39bb6ad added letter-prefixed ids so
        # Q/R/S/E sets can share the driver). An unnormalised "Q2" silently
        # misses every int-keyed lookup -- CLASS, the parsed chains, the
        # duplicate check -- so the row lands in the row counts and in none of
        # the per-question tables. Normalise once, here.
        self.q = _as_qnum(self.summary.get("question_number")) or _q_from_name(path)
        self.klass = CLASS.get(self.q, "?")
        self.tools = list(self.summary.get("tools_in_order") or [])
        self.calls = [c for s in self.steps for c in (s.get("tool_calls") or [])]
        if not self.tools:
            self.tools = [c["tool"] for c in self.calls if c.get("tool")]
        self.results = [s for s in self.steps if s.get("role") == "tool"]
        # The first turn's input is the fixed prompt block: system prompt plus every
        # tool schema plus the question. The question is tens of tokens against tens of
        # thousands, so the smallest of these across a model's rows is a usable floor
        # for what the tool board costs that model before it does anything.
        self.first_turn_in = next(
            (s["usage"]["input_tokens"] for s in self.steps
             if (s.get("usage") or {}).get("input_tokens")), None)
        self.answer = self.summary.get("answer") or ""
        self.denied = bool(self.summary.get("denied"))
        self.error = self.summary.get("error")

        self.expected = set(chains.get(self.q, []))
        self.chain_len = len(chains.get(self.q, []))
        self.servers = {server_of(t) for t in self.tools}
        self.expected_servers = {server_of(t) for t in self.expected}

        # numbers the model was shown, for the refusal proof check only
        self.evidence: set[int] = set()
        for text in ([r.get("result_excerpt", "") for r in self.results]
                     + [json.dumps(c.get("args"), ensure_ascii=False) for c in self.calls]):
            for m in _NUM_IN_TEXT.finditer(text or ""):
                try:
                    self.evidence.add(int(m.group(0).replace(",", "")))
                except ValueError:
                    pass

    # --- D0 ---------------------------------------------------------------
    @property
    def complete(self) -> bool:
        return (not self.denied and not self.error
                and (self.summary.get("answer_chars") or 0) > 0)

    @property
    def completion_note(self) -> str:
        if self.error:
            return "error"
        if self.denied:
            return "denied"
        if not (self.summary.get("answer_chars") or 0):
            return ("**silent empty**" if not self.tools else "empty after tools")
        return "ok"

    # --- D1 ---------------------------------------------------------------
    @property
    def first_ok(self) -> bool | None:
        if not self.expected:
            return None                      # Q13-Q15: no chain to be first in
        return bool(self.tools) and self.tools[0] in self.expected

    @property
    def reached(self) -> bool | None:
        if not self.expected:
            return None
        return any(t in self.expected for t in self.tools)

    @property
    def off_source(self) -> set[str]:
        if not self.expected_servers:
            return set()
        return {s for s in self.servers if s not in self.expected_servers}

    # --- D2 ---------------------------------------------------------------
    @property
    def depth_ratio(self) -> float | None:
        n = self.summary.get("tool_call_count") or 0
        return round(n / self.chain_len, 2) if self.chain_len else None

    # --- D4 ---------------------------------------------------------------
    def refusal_parts(self) -> dict[str, bool] | None:
        if self.klass not in ("gap", "unwired") or not self.answer:
            return None
        low = self.answer.lower()
        answer_nums = {int(m.group(0).replace(",", ""))
                       for m in _NUM_IN_TEXT.finditer(self.answer)
                       if m.group(0).strip(",").isdigit() or "," in m.group(0)}
        wanted = PROOF_NUMBERS.get(self.q, set())
        return {
            "proof": bool(wanted & answer_nums) if wanted else None,
            "reason": any(m in low for m in REFUSAL_MARKERS) and any(m in low for m in REASON_MARKERS),
            "reframe": any(m in low for m in REFRAME_MARKERS),
            "alt_source": any(m in low for m in ALT_SOURCES),
        }

    # --- D5 ---------------------------------------------------------------
    def traps(self) -> list[str]:
        hit = []
        low = self.answer.lower()
        if "search_ena_keywords" in self.tools:
            hit.append("ena_keywords")
        for c in self.calls:
            args = c.get("args") or {}
            if c.get("tool") == "geo_search" and not args.get("entry_type"):
                hit.append("geo_no_entry_type")
            org = str(args.get("organism", "")).strip().lower()
            if c.get("tool", "").startswith("ncbi_pathogen") and org in WRONG_PATHOGEN_GROUPS:
                hit.append("pathogen_wrong_group")
        if self.q in (3, 7) and re.search(r"(?<![\d,])513(?![\d])", self.answer):
            hit.append("gds_513")
        if re.search(r"(?<![\d,])50(?![\d])[^.]{0,40}(ena|run)", low):
            hit.append("ena_50")
        return sorted(set(hit))


def _rel(path: pathlib.Path) -> str:
    """Repo-relative if we can, absolute if we cannot. Never raises: a path that
    cannot be prettified must not lose the reader the whole table."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.as_posix()


def _as_qnum(v) -> int | None:
    """`2`, `"2"` and `"Q2"` are the same question. `"R1"` is not a Q at all."""
    if isinstance(v, int):
        return v
    m = re.fullmatch(r"[Qq]?(\d+)", str(v or "").strip())
    return int(m.group(1)) if m else None


def _q_from_name(path: pathlib.Path) -> int | None:
    m = re.search(r"q(\d+)", path.stem)
    return int(m.group(1)) if m else None


def load_runs(root: pathlib.Path, chains: dict[int, list[str]],
              only: str | None = None) -> tuple[list[Run], list[str]]:
    """Every `evals/runs/<model>/qNN.jsonl`, one directory deep.

    `evals/runs/` also holds snapshot directories such as
    `_premerge-1405-0f0101e/`, which contain a *copy* of the model directories.
    Those are deliberately not loaded -- counting a pre-merge snapshot as extra
    model rows would double every model it holds -- but they are **named in the
    output**, because a directory silently skipped is indistinguishable from a
    directory that was not there.
    """
    out, notes = [], []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if only and d.name != only:
            continue
        files = sorted(d.glob("q*.jsonl"))
        if not files:
            deeper = len(list(d.glob("*/q*.jsonl")))
            notes.append(f"`{d.name}/` holds no `qNN.jsonl` of its own"
                         + (f" but {deeper} one level deeper — treated as a snapshot and "
                            "**not loaded**." if deeper else " and is empty."))
            continue
        for f in files:
            try:
                out.append(Run(f, d.name, chains))
            except (json.JSONDecodeError, KeyError) as exc:
                notes.append(f"**`{f.relative_to(root).as_posix()}` is unreadable** "
                             f"({type(exc).__name__}: {exc}) and is missing from every table.")

    # Two files can carry the same question for the same model -- `q02.jsonl`
    # from the base matrix and `q2.jsonl` from a later smoke run, because the
    # driver's filename padding changed at 39bb6ad. Both are real runs and both
    # are kept, but a row count that is larger than the question count must say
    # so: otherwise "9/9 answered" and "11/11 answered" look like the same claim
    # about the same nine questions.
    seen: dict[tuple[str, int], list[str]] = {}
    for r in out:
        seen.setdefault((r.model, r.q), []).append(r.path.name)
    for (model, q), names in sorted(seen.items()):
        if len(names) > 1:
            notes.append(f"**`{model}` Q{q} has {len(names)} transcripts** "
                         f"({', '.join(sorted(names))}) — both are counted as rows, so this "
                         "model's row count exceeds its question count.")
    return out, notes


# --- judge-report.md, read not re-implemented -------------------------------

_JUDGE_MODEL = re.compile(r"^## `([^`]+)`$")
_RE_ROUTED = re.compile(r"routed (\d+)/(\d+) scored")
_RE_FAB = re.compile(r"fabrication flags (\d+) . unmatched-but-truncated (\d+)")
_RE_TRUTH = re.compile(r"ground truth,[^(]*\((\d+) questions?\): (\d+) correct . (\d+) "
                       r"\*\*wrong figure\*\* . (\d+) \*\*never stated\*\*")

# judge's trap names, split the way D5 splits them. A name judge emits that is
# not in here lands in `unclassified` and is printed -- an unknown trap must not
# vanish into a zero.
TRAP_CLASS = {"geo_no_entry_type": "arg", "pathogen_wrong_group": "arg",
              "ena_keywords": "credulity", "gds_513": "credulity",
              "ena_50": "credulity", "zero_as_absence": "credulity",
              "rows_as_isolates_150926": "credulity", "meca_94336": "credulity"}


def read_judge(path: pathlib.Path):
    """Judge's own numbers, per model. Never re-derived here.

    Returns (per_model, per_question, notes). The report is a series of
    ``## `model` `` sections, each a per-question table followed by summary
    bullets, and then sections that are NOT models (`## Cross-model`, the trap
    glossary). Those must reset the current model: attributing the cross-model
    table's rows to the last model read is exactly the bug this parser had on its
    first run, and it invented a routing disagreement that did not exist.

    A missing or unparseable report returns empty **with a note saying so**. It
    must never read as "zero fabrications".
    """
    if not path.exists():
        return {}, {}, [f"`{path.name}` does not exist yet; D3 and D8 have no judge input."]
    text = path.read_text(encoding="utf-8")
    per_model: dict[str, dict] = {}
    rows: dict[tuple[str, int], dict] = {}
    model = None
    for line in text.splitlines():
        if line.startswith("## "):
            h = _JUDGE_MODEL.match(line.rstrip())
            model = h.group(1) if h else None
            if model:
                per_model.setdefault(model, {})
            continue
        if not model:
            continue
        for rx, keys in ((_RE_ROUTED, ("routed", "scored")),
                         (_RE_FAB, ("fabricated", "unmatched")),
                         (_RE_TRUTH, ("truth_pinned", "correct", "wrong", "never_stated"))):
            m = rx.search(line)
            if m:
                per_model[model].update(dict(zip(keys, (int(g) for g in m.groups()))))
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 9 and cells[0].isdigit():
            rows[(model, int(cells[0]))] = {
                "routed": cells[2],
                "traps": [t.strip(" `") for t in cells[8].split(",")
                          if t.strip(" `—-")],
            }
    notes = []
    blank = [m for m, v in per_model.items() if "fabricated" not in v]
    if not per_model:
        notes.append(f"`{path.name}` exists but no ``## `model` `` section matched. "
                     "**D3 is unread, not clean** — do not read the blanks as zero.")
    elif blank:
        notes.append("No `fabrication flags` line found for " +
                     ", ".join(f"`{m}`" for m in blank) + "; those cells are unread, not clean.")
    return per_model, rows, notes


# --- little helpers ---------------------------------------------------------

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, nd=1):
    return "—" if x is None else (f"{x:,.{nd}f}" if isinstance(x, float) else f"{x:,}")


def linfit(xs, ys):
    """Least squares y = a + b x, with R². Needs >= 3 distinct x."""
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pts) < 3 or len({p[0] for p in pts}) < 2:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx == 0:
        return None
    b = sxy / sxx
    a = my - b * mx
    ss_res = sum((p[1] - (a + b * p[0])) ** 2 for p in pts)
    ss_tot = sum((p[1] - my) ** 2 for p in pts)
    r2 = 1 - ss_res / ss_tot if ss_tot else None
    return a, b, r2, n


# --- the tables -------------------------------------------------------------

def table(rows: list[list[str]], head: list[str]) -> str:
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def by_model(runs):
    d = defaultdict(list)
    for r in runs:
        d[r.model].append(r)
    return dict(sorted(d.items()))


def d0_completion(runs):
    rows = []
    for m, rs in by_model(runs).items():
        bad = [r for r in rs if not r.complete]
        rows.append([f"`{m}`", tier_of(m), f"{len(rs) - len(bad)}/{len(rs)}",
                     sum(1 for r in rs if r.denied), sum(1 for r in rs if r.error),
                     ", ".join(f"Q{r.q} {r.completion_note}" for r in bad) or "—"])
    body = table(rows, ["model", "tier", "answered", "denied", "errors", "incomplete rows"])
    return "### D0 · Completion\n\n" + body + "\n"


def d1_routing(runs, judge_rows):
    rows, disagreements = [], []
    for m, rs in by_model(runs).items():
        cell = {}
        for klass in ("answerable", "unwired", "gap"):
            sub = [r for r in rs if r.klass == klass and r.reached is not None]
            cell[klass] = (f"{sum(1 for r in sub if r.reached)}/{len(sub)}" if sub else "—")
        firsts = [r for r in rs if r.first_ok is not None]
        offs = sorted({s for r in rs for s in r.off_source} - {"unknown"})
        rows.append([f"`{m}`", tier_of(m), cell["answerable"], cell["unwired"], cell["gap"],
                     f"{sum(1 for r in firsts if r.first_ok)}/{len(firsts)}" if firsts else "—",
                     ", ".join(offs) or "—"])
        for r in rs:
            j = (judge_rows.get((m, r.q)) or {}).get("routed")
            if j is None or r.reached is None:
                continue
            mine = "yes" if r.reached else "no"
            if j in ("yes", "no") and j != mine:
                disagreements.append([f"`{m}`", f"Q{r.q}", f"judge `{j}`", f"analyst `{mine}`",
                                      f"`{_rel(r.path)}`"])
    out = ["### D1 · Routing\n",
           "`reached` = any expected tool called, by question class. `first ok` = the first "
           "tool called was an expected one. Expected sets are parsed from the step tables in "
           "`QUESTIONS.md`.\n",
           table(rows, ["model", "tier", "reached Q1–9,12", "unwired Q10–11", "gap Q13–15",
                        "first ok", "off-source servers touched"])]
    out.append("\n**Cross-check against `judge.py`.** " + (
        "Every row agrees.\n" if not disagreements else
        "The two implementations disagree on the rows below. A definition two scripts read "
        "differently is a loose definition; this is reported, not resolved by preference.\n\n"
        + table(disagreements, ["model", "Q", "judge", "analyst", "transcript"]) + "\n"))
    return "\n".join(out)


def d2_depth(runs):
    rows = []
    for m, rs in by_model(runs).items():
        ok = [r for r in rs if r.complete and r.depth_ratio is not None]
        ratios = [r.depth_ratio for r in ok]
        over = [f"Q{r.q} ({r.depth_ratio}×)" for r in ok if r.depth_ratio and r.depth_ratio >= 2]
        rows.append([f"`{m}`", tier_of(m),
                     fmt(mean([r.summary.get("tool_call_count") for r in rs])),
                     fmt(mean([r.summary.get("llm_round_trips") for r in rs])),
                     fmt(statistics.median(ratios), 2) if ratios else "—",
                     fmt(mean([len(r.servers - {"unknown"}) for r in rs])),
                     ", ".join(over[:4]) or "—"])
    return ("### D2 · Chain depth\n\n"
            "`depth ratio` = tool calls ÷ the documented chain length for that question in "
            "`QUESTIONS.md`. 1.0 is the documented chain; below 0.5 is skipping, above 2.0 is "
            "thrashing.\n\n"
            + table(rows, ["model", "tier", "mean calls", "mean LLM trips", "median depth ratio",
                           "mean servers", "ratio ≥ 2 (thrashing)"]) + "\n")


def d3_fabrication(runs, judge, notes):
    if not any("fabricated" in v for v in judge.values()):
        return ("### D3 · Fabrication\n\n**Not scored.** "
                + " ".join(notes or ["`judge-report.md` gave no per-model counts."])
                + "\nAn absent judge report is not a clean one; no fabrication rate is "
                  "reported here until it parses.\n")
    rows = [[f"`{m}`", tier_of(m),
             fmt(v["fabricated"]) if "fabricated" in v else "**unread**",
             fmt(v["unmatched"]) if "unmatched" in v else "**unread**",
             f"{v.get('routed', '?')}/{v.get('scored', '?')}"]
            for m, v in sorted(judge.items())]
    total_unmatched = sum(v.get("unmatched", 0) for v in judge.values())
    tail = ("\n**The unmatched column is large, so D3 is not yet decidable.** "
            "`run_questions.py` keeps the first 600 characters of each tool result; an "
            "unmatched number may have been in the part the transcript does not hold. "
            "Raising `RESULT_EXCERPT` converts these into decidable flags.\n"
            if total_unmatched >= max(3, len(judge)) else "\n")
    return ("### D3 · Fabrication (read from `judge-report.md`, not re-implemented)\n\n"
            + table(rows, ["model", "tier", "fabricated", "unmatched", "judge routed"]) + tail)


def d4_refusal(runs):
    rows = []
    for m, rs in by_model(runs).items():
        parts = [(r, r.refusal_parts()) for r in rs]
        parts = [(r, p) for r, p in parts if p]
        if not parts:
            rows.append([f"`{m}`", tier_of(m), "—", "—", "—", "—", "—", "—"])
            continue
        def share(key):
            vals = [p[key] for _, p in parts if p[key] is not None]
            return f"{sum(vals)}/{len(vals)}" if vals else "n/a"
        scores = [sum(1 for v in p.values() if v) for _, p in parts]
        rows.append([f"`{m}`", tier_of(m), len(parts), share("proof"), share("reason"),
                     share("reframe"), share("alt_source"), fmt(mean(scores), 2)])
    return ("### D4 · Refusal quality\n\n"
            "The four parts of a good refusal, from `PIPELINES.md` P8, on Q10, Q11 and "
            "Q13–Q15. Reported as components: a 2/4 with the proof number is a different "
            "failure from a 2/4 with the reframing.\n\n"
            + table(rows, ["model", "tier", "rows scored", "proof number", "reason",
                           "reframing", "named source", "mean /4"]) + "\n")


def d5_traps(runs, judge_rows):
    """Judge decides which traps fired; this script only splits them arg vs credulity.

    My own four detectors (`Run.traps`) run alongside as a cross-check, because the
    prompt assigns those four to me. Where the two differ the difference is printed
    rather than reconciled.
    """
    rows, unknown, disagree = [], set(), []
    for m, rs in by_model(runs).items():
        hits = defaultdict(list)
        for r in rs:
            jt = (judge_rows.get((m, r.q)) or {}).get("traps") or []
            for t in jt:
                hits[t].append(f"Q{r.q}")
            mine, theirs = set(r.traps()), set(jt)
            where = f"`{_rel(r.path)}`"
            disagree += [[f"`{m}`", f"Q{r.q}", f"`{t}`", "analyst only", where]
                         for t in sorted(mine - theirs)]
            disagree += [[f"`{m}`", f"Q{r.q}", f"`{t}`", "judge only", where]
                         for t in sorted((theirs - mine) & set(TRAPS))]
        unknown |= {k for k in hits if k not in TRAP_CLASS}
        arg = sum(len(v) for k, v in hits.items() if TRAP_CLASS.get(k) == "arg")
        cred = sum(len(v) for k, v in hits.items() if TRAP_CLASS.get(k) == "credulity")
        rows.append([f"`{m}`", tier_of(m), arg, cred,
                     sum(len(v) for k, v in hits.items() if k not in TRAP_CLASS),
                     "; ".join(f"`{k}` {','.join(v)}" for k, v in sorted(hits.items())) or "none"])
    out = ["### D5 · Trap avoidance\n",
           "Which traps fired is `judge.py`'s call, read from `judge-report.md`. The split is "
           "mine: an **arg** trap needs the model to know an argument exists, a **credulity** "
           "trap needs it to disbelieve a number a tool just handed it. D5 predicted the "
           "prompt fixes the first and model quality fixes the second.\n",
           table(rows, ["model", "tier", "arg traps", "credulity traps", "unclassified",
                        "which (judge)"]),
           "\n" + "\n".join(f"- `{k}` — **{v}**" for k, v in sorted(TRAP_CLASS.items()))]
    if unknown:
        out.append("\n**Judge emitted trap names this script cannot classify.** They are "
                   "counted under `unclassified` and never dropped: "
                   + ", ".join(f"`{u}`" for u in sorted(unknown))
                   + ". Add them to `TRAP_CLASS`.\n")
    out.append("\n**My four detectors vs judge.** " + (
        "Every transcript agrees.\n" if not disagree else
        "The rows below fired for one and not the other.\n\n"
        + table(disagree, ["model", "Q", "trap", "seen by", "transcript"]) + "\n"))
    return "\n".join(out)


def d6_cost(runs):
    rows, fits, blocks = [], [], []
    for m, rs in by_model(runs).items():
        ok = [r for r in rs if r.complete]
        if not ok:
            ok = rs
        tin = [r.summary.get("input_tokens") for r in ok]
        trips = [r.summary.get("llm_round_trips") for r in ok]
        fit = linfit(trips, tin)
        per_trip = [(i / t) for i, t in zip(tin, trips) if i and t]
        costs = [r.summary.get("list_cost_usd") for r in ok]
        priced = [c for c in costs if c is not None]
        floors = [r.first_turn_in for r in rs if r.first_turn_in]
        floor = min(floors) if floors else None
        blocks.append([f"`{m}`", tier_of(m), fmt(floor, 0) if floor else "—", len(floors),
                       fmt(mean([100 * floor * (r.summary.get("llm_round_trips") or 0)
                                 / r.summary["input_tokens"]
                                 for r in ok if r.summary.get("input_tokens")]), 1) + "%"
                       if floor else "—"])
        rows.append([
            f"`{m}`", tier_of(m), len(ok),
            fmt(mean(tin), 0), fmt(mean([r.summary.get("output_tokens") for r in ok]), 0),
            fmt(mean(per_trip), 0),
            fmt(mean([r.summary.get("ttft_s") for r in ok]), 2),
            fmt(mean([r.summary.get("model_seconds") for r in ok])),
            fmt(mean([r.summary.get("tool_seconds") for r in ok])),
            (f"${sum(priced):.2f}" if priced else "**no price row**"),
        ])
        if fit:
            a, b, r2, n = fit
            share = None
            tot = sum(x for x in tin if x)
            if tot:
                share = 100 * sum(a + 0 * 1 for _ in ok) / tot if False else None
            fits.append([f"`{m}`", fmt(a, 0), fmt(b, 0), fmt(r2, 3), n])
    out = ["### D6 · Cost\n",
           "Tokens are measured. **Dollars are not** — `LIST_PRICE_PER_M` in "
           "`run_questions.py` is documented there as prices *as remembered* on 17 Sep 2026, "
           "and has no row at all for `claudeopus5` or `gpt5`. Treat the dollar column as "
           "unverified and never as the basis of a cost-per-quality claim.\n",
           table(rows, ["model", "tier", "rows", "mean in tok", "mean out tok",
                        "in tok / round trip", "mean TTFT s", "model s", "tool s",
                        "list $ (unverified)"])]
    out += ["\n**What the tool board costs before anyone asks anything.** The smallest "
            "first-turn input across a model's rows — the system prompt, all ~90 tool "
            "schemas and a question of a few dozen tokens. It is an upper bound on the "
            "true floor, tighter the more rows a model has.\n",
            table(blocks, ["model", "tier", "prompt block (tok)", "rows seen",
                           "share of all input tokens"])]
    if fits:
        out += ["\n**Is input cost schema overhead × round trips?** Least squares of "
                "`input_tokens` on `llm_round_trips`, per model. The intercept is the "
                "fixed cost of one turn — system prompt plus every tool schema. D6 predicted "
                "≈33k ± 15% with R² > 0.8.\n",
                table(fits, ["model", "intercept (tok)", "slope (tok/trip)", "R²", "n"])]
    else:
        out.append("\n*Too few rows per model to fit the overhead regression (needs ≥ 3).*\n")
    return "\n".join(out) + "\n"


def d7_transport(runs):
    pairs = defaultdict(dict)
    for m, rs in by_model(runs).items():
        if m.startswith("argo_"):
            pairs[m[len("argo_"):]]["argo"] = rs
        elif m.startswith("anthropic_"):
            pairs[m[len("anthropic_"):]]["anthropic"] = rs
    both = {k: v for k, v in pairs.items() if len(v) == 2}
    if not both:
        return ("### D7 · Transport\n\n*Not yet runnable: no model has been recorded through "
                "both `argo/` and `anthropic/`. Queue item 6.*\n")
    rows = []
    for name, v in sorted(both.items()):
        a = {r.q: r for r in v["argo"]}
        n = {r.q: r for r in v["anthropic"]}
        shared = sorted(set(a) & set(n))
        same_first = sum(1 for q in shared
                         if (a[q].tools[:1] or [None]) == (n[q].tools[:1] or [None]))
        rows.append([
            f"`{name}`", len(shared), f"{same_first}/{len(shared)}",
            fmt(mean([a[q].summary.get("tool_call_count") for q in shared])),
            fmt(mean([n[q].summary.get("tool_call_count") for q in shared])),
            fmt(mean([a[q].summary.get("ttft_s") for q in shared]), 2),
            fmt(mean([n[q].summary.get("ttft_s") for q in shared]), 2),
            fmt(mean([a[q].summary.get("answer_chars") for q in shared]), 0),
            fmt(mean([n[q].summary.get("answer_chars") for q in shared]), 0)])
    return ("### D7 · Transport — `argo/` vs `anthropic/`, same weights\n\n"
            "D7 predicted identical first tool on ≥13/15, call counts within 1, answer "
            "lengths within 30%. Anything else means the two routes are not sending the "
            "same request, and every Argo number in this file inherits that.\n\n"
            + table(rows, ["model", "shared Q", "same first tool", "argo calls", "anth calls",
                           "argo TTFT", "anth TTFT", "argo chars", "anth chars"]) + "\n")


def d8_plateau(runs, judge):
    rows = []
    for m, rs in by_model(runs).items():
        ok = [r for r in rs if r.complete]
        routed = [r for r in rs if r.reached is not None]
        traps = sum(len(r.traps()) for r in rs)
        tok = sum((r.summary.get("input_tokens") or 0) + (r.summary.get("output_tokens") or 0)
                  for r in rs)
        jt = judge.get(m, {})
        fab = jt.get("fabricated")
        truth = (f"{jt['correct']}✓ / {jt['wrong']}✗ / {jt['never_stated']} unstated"
                 if "correct" in jt else "—")
        rows.append([f"`{m}`", tier_of(m), f"{len(ok)}/{len(rs)}",
                     f"{sum(1 for r in routed if r.reached)}/{len(routed)}" if routed else "—",
                     truth, traps, "**unread**" if fab is None else fab, fmt(tok, 0),
                     fmt(mean([r.summary.get("elapsed_s") for r in rs]))])
    return ("### D8 · Plateau — does more model buy anything here\n\n"
            "Quality proxies against what they cost. A plateau here is a plateau **on the "
            "dimensions that can be measured mechanically**, which is not the same as a "
            "plateau in answer quality; the stronger claim needs a human reading answers "
            "against `PIPELINES.md`.\n\n"
            + table(rows, ["model", "tier", "answered", "routed", "ground truth (judge)",
                           "trap hits", "fabricated", "total tokens", "mean s"]) + "\n")


def unknown_tools(runs):
    unknown = defaultdict(set)
    for r in runs:
        for t in r.tools:
            if server_of(t) == "unknown":
                unknown[t].add(r.model)
    if not unknown:
        return ""
    return ("\n**Tools this script could not map to a server** (so their off-source status is "
            "unknown, not clean) — add them to `SERVER_PREFIX` or `BRC_TOOLS`: "
            + ", ".join(f"`{t}`" for t in sorted(unknown)) + "\n")


def build(runs, judge, judge_rows, notes, root: pathlib.Path = RUNS) -> str:
    models = sorted({r.model for r in runs})
    head = [
        f"*Generated by `evals/analyze.py` from {len(runs)} transcripts across "
        f"{len(models)} model rows under `{_rel(root)}/`. Expected chains parsed from "
        f"`QUESTIONS.md`; fabrication read from `judge-report.md`.*\n",
        "**Every gap below is unreplicated** — one run per cell. Nothing here is a real "
        "difference between models until `--repeat 3` shows it exceeds a model's spread "
        "against itself.\n",
    ]
    # `judge-report.md` is written by another chat against whatever had landed when it
    # ran. If it has scored fewer transcripts than exist, every judge-derived cell is a
    # verdict on a subset -- and "0 fabrications" from a judge that saw two transcripts
    # is not a clean result. Say so with the numbers.
    have = {(r.model, r.q) for r in runs}
    scored = {k for k in judge_rows if k in have}
    if len(scored) < len(have):
        missing = defaultdict(list)
        for m, q in sorted(have - scored):
            missing[m].append(f"Q{q}")
        notes.append(
            f"**`judge-report.md` has scored {len(scored)} of {len(have)} transcripts.** "
            "Every judge-derived cell below (D3, D5, the ground-truth column of D8) is a "
            "verdict on that subset only; the rest are unscored, not clean — "
            + "; ".join(f"`{m}` {', '.join(qs)}" for m, qs in missing.items()) + ".")
    if notes:
        head.append("**Input notes:** " + " ".join(notes) + "\n")
    parts = [d0_completion(runs), d1_routing(runs, judge_rows), d2_depth(runs),
             d3_fabrication(runs, judge, notes), d4_refusal(runs), d5_traps(runs, judge_rows),
             d6_cost(runs), d7_transport(runs), d8_plateau(runs, judge)]
    return "\n".join(head + parts) + unknown_tools(runs)


def splice(findings: pathlib.Path, block: str) -> None:
    text = findings.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{findings.name} has no {BEGIN} / {END} markers; refusing to guess "
                         "where the generated section goes.")
    before = text.split(BEGIN)[0]
    after = text.split(END, 1)[1]
    findings.write_text(f"{before}{BEGIN}\n\n{block}\n{END}{after}", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=pathlib.Path, default=RUNS)
    ap.add_argument("--judge", type=pathlib.Path, default=JUDGE_REPORT)
    ap.add_argument("--findings", type=pathlib.Path, default=FINDINGS)
    ap.add_argument("--only-model", default=None)
    ap.add_argument("--write", action="store_true",
                    help="splice the tables into FINDINGS.md between the markers")
    args = ap.parse_args()

    chains = parse_chains(QUESTIONS_MD)
    check_chains(chains)
    args.runs = args.runs.resolve()
    if not args.runs.exists():
        raise SystemExit(f"{args.runs} does not exist -- nothing to analyse.")
    runs, load_notes = load_runs(args.runs, chains, args.only_model)
    if not runs:
        # An empty result must say where the data went. `runner` archives a
        # superseded matrix into `runs/_archive-.../<model>/`, which leaves
        # `runs/` holding only directories-of-directories; exiting with a bare
        # "empty" here would read as "the matrix is clean" when it means
        # "the matrix moved". Name the nested sets and give the command.
        nested = sorted({p.parent.parent for p in args.runs.glob("*/*/q*.jsonl")})
        hint = "".join(
            f"\n  python evals/analyze.py --runs {d.as_posix()}"
            f"   ({len(list(d.glob('*/q*.jsonl')))} transcripts)" for d in nested)
        raise SystemExit(
            f"No qNN.jsonl directly under {args.runs}. That is not a clean result."
            + (f" {len(nested)} nested transcript set(s) were found instead:{hint}"
               if nested else " Nothing was found one level deeper either."))
    judge, judge_rows, judge_notes = read_judge(args.judge)
    notes = load_notes + judge_notes
    block = build(runs, judge, judge_rows, notes, args.runs)
    if args.write:
        splice(args.findings, block)
        print(f"wrote {len(block):,} chars into {_rel(args.findings)} "
              f"({len(runs)} transcripts, {len({r.model for r in runs})} models)")
    else:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
