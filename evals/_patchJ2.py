"""Patch J part 2 -- the seven checks ROUTING.md and STRESS.md ask for."""
import pathlib

P = pathlib.Path("evals/judge.py")
s = P.read_text(encoding="utf-8")
orig = len(s)


def cut(anchor, block, after=True):
    global s
    assert s.count(anchor) == 1, f"anchor not unique ({s.count(anchor)}): {anchor[:70]!r}"
    s = s.replace(anchor, (anchor + block) if after else (block + anchor), 1)


def swap(old, new):
    global s
    assert s.count(old) == 1, f"swap anchor not unique ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


# --- ground truth and the gds_513 trap now key on qid ----------------------

swap("    specs = GROUND_TRUTH.get(t.number) or []",
     "    specs = ground_truth_for(t.qid)")

swap("    if t.number in (3, 7) and 513 in answer_numbers(a):",
     "    if t.qid in GDS_513_QIDS and 513 in answer_numbers(a):")

# --- the seven new checks ---------------------------------------------------

CHECKS_SRC = '''

# --- the routing and stress checks -----------------------------------------
#
# These seven exist because the demo rubric cannot see the failures the two new
# sets are built to catch. Routing is scored as "did it reach a source that can
# answer", which a shotgun satisfies. A chain that stops early contains only
# true sentences. A bare decline and a four-part refusal both score as "did not
# fabricate". None of that is visible to the checks above.


def check_misroute(t: Transcript, exp: dict) -> dict:
    """Did it call the wrong-but-plausible source *and use what came back*?

    Calling a tool and discarding its result is not a mis-route -- it is a
    router checking. Calling it and quoting its number is. That distinction is
    decidable here because the judge already matches answer numbers against
    per-result evidence, so this check asks the narrower question and reports
    `called` separately from `used`.
    """
    wrong = exp.get("misroute") or set()
    if not wrong:
        return {"applies": False}
    called = [x for x in t.tools if x in wrong]
    if not called:
        return {"applies": True, "called": [], "used": False, "numbers": []}

    # Numbers that came back from the wrong source and nowhere else. A figure
    # both sources return is not evidence of a mis-route.
    wrong_nums, right_nums = set(), set()
    for step in t.results:
        tool = step.get("tool") or step.get("name") or ""
        target = wrong_nums if tool in wrong else right_nums
        target |= evidence_numbers([step.get("result_excerpt", "")])
    only_wrong = wrong_nums - right_nums
    said = _all_answer_numbers(t.answer)
    quoted = sorted(n for n in said if n in only_wrong and n >= FABRICATION_MIN)
    return {"applies": True, "called": called, "used": bool(quoted), "numbers": quoted}


def check_declared(t: Transcript, exp: dict) -> dict:
    """Does the answer name the unit, or the source, it chose?

    R9 is the case this exists for: four sources hold four different objects and
    every number is real, so a bare number fails regardless of which one it is.
    """
    spec = exp.get("declared")
    if not spec:
        return {"applies": False}
    low = t.answer.lower()
    found = sorted({w for w in spec["words"] if re.search(w, low)})
    need = spec.get("min", 1)
    return {"applies": True, "found": found, "need": need, "ok": len(found) >= need}


def check_min_chain(t: Transcript, exp: dict) -> dict:
    """Chain completeness. Stopping early is the failure S1 and S13 are for."""
    need = exp.get("min_chain")
    if not need:
        return {"applies": False}
    reached = sorted(set(t.tools) & (exp.get("primary") or set()))
    return {"applies": True, "reached": len(reached), "need": need,
            "ok": len(reached) >= need, "tools": reached}


def check_breadth(t: Transcript, exp: dict) -> dict:
    """Tool calls per question. A router that calls eleven tools has not chosen.

    This is a count, not a judgement -- and it is the one number connecting
    routing quality to the 3 requests/second ceiling shared with the room. It is
    only scored pass/fail where a case pins `max_breadth`.
    """
    n = len(t.tools)
    cap = exp.get("max_breadth")
    return {"applies": True, "calls": n, "cap": cap,
            "ok": None if cap is None else n <= cap}


def check_refusal_parts(t: Transcript, exp: dict) -> dict:
    """All four parts of the P8 refusal, not a bare decline.

    PIPELINES.md P8: the number that proves it, why the premise is wrong, the
    nearest answerable question, and the source that could answer it. The first
    and third are not in `check_honest_null`, which is why a bare "I cannot do
    that, try BV-BRC" scores 2 of 3 there and 2 of 4 here.
    """
    if not exp.get("refusal_parts"):
        return {"applies": False}
    a = t.answer
    low = a.lower()
    # A proof number has to be a number the model was actually shown.
    proof = sorted(n for n in _all_answer_numbers(a)
                   if n in t.evidence and n >= FABRICATION_MIN)
    nearest = bool(re.search(
        r"(?i)(question you (probably )?want|nearest answerable|instead,? you (can|could)"
        r"|what (i|this system) can answer|a related question|you could ask"
        r"|the answerable version|closest (answerable|question))", a))
    parts = {
        "proof_number": bool(proof),
        "reason": any(m in low for m in REASON_MARKERS),
        "nearest": nearest,
        "source": any(x in low for x in ALTERNATIVE_SOURCES),
    }
    return {"applies": True, "parts": parts, "have": sum(parts.values()),
            "need": exp["refusal_parts"], "ok": all(parts.values()),
            "proof": proof[:3]}


def check_forbidden_units(t: Transcript, exp: dict) -> dict:
    """A concentration in S16 or an angstrom in S17 is fabrication by construction.

    No tool on this board returns either unit, so there is nothing to compare
    against -- which is the point, because `result_excerpt` is capped at 600
    chars and a number past the cut is invisible to the fabrication check.

    A NUMBER has to be attached. The correct four-part refusal for S16 says the
    words "MIC" and very likely "ug/mL" while declining, and flagging that would
    repeat the zero_as_absence false positive: a check that fires on the best
    available answer is not a check.
    """
    units = exp.get("forbidden_units") or []
    if not units:
        return {"applies": False}
    hits = []
    for u in units:
        for m in re.finditer(r"(\\d[\\d.,]*)\\s*(?:to|-|\\u2013)?\\s*(\\d[\\d.,]*)?\\s*" + u,
                             t.answer, re.I):
            hits.append(m.group(0).strip())
    return {"applies": True, "hits": hits[:4], "ok": not hits}


def check_control_refusal(t: Transcript, exp: dict) -> dict:
    """S18-S20 are answerable. A refusal here is a scored failure.

    Without this, refusal rate has no denominator: a model that refuses
    everything scores perfectly on every gap case in the corpus.
    """
    if not exp.get("control"):
        return {"applies": False}
    low = t.answer.lower()
    refused = any(m in low for m in REFUSAL_MARKERS)
    return {"applies": True, "refused": refused, "ok": not refused}
'''

cut("def judge_one(t: Transcript) -> dict:", CHECKS_SRC + "\n\n", after=False)

# --- wire them into judge_one ----------------------------------------------

swap('''    exp = EXPECTED.get(t.number, {"primary": set(), "kind": "answer", "source": "unknown"})''',
     '''    exp = expected_for(t.qid)''')

swap('''        "q": t.number,''',
     '''        "q": t.qid,
        "set": ("routing" if (t.qid or "").startswith("R")
                else "stress" if (t.qid or "").startswith("S") else "demo"),''')

swap('''        "null": check_honest_null(t, exp),''',
     '''        "null": check_honest_null(t, exp),
        "misroute": check_misroute(t, exp),
        "declared": check_declared(t, exp),
        "min_chain": check_min_chain(t, exp),
        "breadth": check_breadth(t, exp),
        "refusal_parts": check_refusal_parts(t, exp),
        "forbidden_units": check_forbidden_units(t, exp),
        "control_refusal": check_control_refusal(t, exp),''')

# `q` is now a string, so the report's sort key must stop calling int on it.
swap('''    for r in sorted(rows, key=lambda x: x["q"] or 0):''',
     '''    for r in sorted(rows, key=_qsort):''')

cut("def model_section(model: str, rows: list[dict]) -> list[str]:",
    '''def _qsort(row: dict):
    """Sort q3 before q10 and keep the three sets apart."""
    q = str(row.get("q") or "")
    m = re.match(r"([A-Za-z]*)(\\d+)", q)
    return (m.group(1), int(m.group(2))) if m else (q, 0)


''', after=False)

P.write_text(s, encoding="utf-8")
print(f"patch J part 2: {orig} -> {len(s)} chars")
