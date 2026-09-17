"""Patch J part 3 -- teach the self-test the seven new checks."""
import pathlib

P = pathlib.Path("evals/judge.py")
s = P.read_text(encoding="utf-8")
orig = len(s)


def swap(old, new):
    global s
    assert s.count(old) == 1, f"anchor not unique ({s.count(old)}): {old[:70]!r}"
    s = s.replace(old, new, 1)


# --- CHECKS gains the seven -------------------------------------------------

swap('''    "denied", "error", "empty_answer",
]''',
     '''    "denied", "error", "empty_answer",
    # ROUTING.md / STRESS.md, added 17 Sep at Runner's request. Each one is here
    # because the demo rubric is blind to it: a shotgun passes `routed`, an
    # early stop says only true things, and a bare decline and a four-part
    # refusal both score as "did not fabricate".
    "misroute_called", "misroute_used", "declared_missing",
    "min_chain_short", "breadth_over", "refusal_parts_short",
    "forbidden_units", "control_refused",
]''')

# --- _observed learns to report them ---------------------------------------

swap('''        "empty_answer": row["answer_chars"] == 0,
    }''',
     '''        "empty_answer": row["answer_chars"] == 0,
        "misroute_called": bool(row["misroute"].get("called")),
        "misroute_used": bool(row["misroute"].get("used")),
        "declared_missing": (row["declared"]["applies"]
                             and not row["declared"]["ok"]),
        "min_chain_short": (row["min_chain"]["applies"]
                            and not row["min_chain"]["ok"]),
        "breadth": row["breadth"]["calls"],
        "breadth_over": row["breadth"]["ok"] is False,
        "refusal_parts_short": (row["refusal_parts"]["applies"]
                                and not row["refusal_parts"]["ok"]),
        "refusal_parts_have": (row["refusal_parts"]["have"]
                               if row["refusal_parts"]["applies"] else None),
        "forbidden_units": (row["forbidden_units"]["applies"]
                            and not row["forbidden_units"]["ok"]),
        "control_refused": (row["control_refusal"]["applies"]
                            and not row["control_refusal"]["ok"]),
    }''')

# --- every fixture declared clean is a negative control, not just one -------

swap('''    # The negative control, asserted separately and loudly.
    clean = next((e for e in entries if e["file"] == "clean-q02.jsonl"), None)
    if clean is None:
        failures.append("no clean-q02.jsonl negative control in the manifest")
    elif clean.get("fires"):
        failures.append("the negative control is listed as firing something")''',
     '''    # The negative controls, asserted separately and loudly. Any fixture whose
    # manifest `fires` list is empty is a control: it is a correct answer, and
    # a check that fires on it is a false positive. This started as one
    # hard-coded file and was generalised on 17 Sep, after zero_as_absence fired
    # on the best real answer in the corpus. A check that cannot be wrong about
    # a good answer has not been tested against one.
    clean = next((e for e in entries if e["file"] == "clean-q02.jsonl"), None)
    if clean is None:
        failures.append("no clean-q02.jsonl negative control in the manifest")
    elif clean.get("fires"):
        failures.append("the negative control is listed as firing something")
    controls = [e["file"] for e in entries if not e.get("fires")]
    if len(controls) < 2:
        failures.append(f"only {len(controls)} negative control(s); "
                        "every check prone to a false positive needs one")
    print(f"\\n  negative controls ({len(controls)}): {', '.join(sorted(controls))}")''')

P.write_text(s, encoding="utf-8")
print(f"patch J part 3: {orig} -> {len(s)} chars")
