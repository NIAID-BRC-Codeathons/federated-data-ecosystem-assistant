"""Mutation test: break each new guard on purpose, confirm the suite notices.

A check that has never failed is not evidence. This reverts each guard to the
version it replaced and asserts the self-test goes red on the named fixture.
The file is restored after every run, and the restore is verified by rerunning
the suite green at the end.
"""
import pathlib
import subprocess
import sys

EVALS = pathlib.Path(__file__).resolve().parent.parent
J = EVALS / "judge.py"
ORIG = J.read_text(encoding="utf-8")
B = chr(92)


MUTATIONS = [
    ("_states reverted to the substring test it replaced",
     '''    return any(re.search(rf"(?<![BSd.,]){s}(?![BSd.,])", a)
               for s in (f"{n:,}", str(n)))'''.replace("BS", B),
     '''    return str(n) in a or f"{n:,}" in a''',
     "rnr-total-substring.jsonl"),

    # One mutation per positive fixture, because the two use different keys:
    # rnr-total-substring carries `total_count`, rnr-page-size-as-finding
    # carries a bare `total`. Dropping either must be noticed by exactly one.
    ("total_count dropped from the measured key list",
     'r"(?:total_matching|total_count|total_found|total_runs"',
     'r"(?:total_matching|total_found|total_runs"',
     "rnr-total-substring.jsonl"),

    ("bare total dropped from the measured key list",
     'r"|total_in_ena|result_count|num_found|hit_count|total)"',
     'r"|total_in_ena|result_count|num_found|hit_count)"',
     "rnr-page-size-as-finding.jsonl"),

    ("meca_94336 back to 'mecC anywhere clears it'",
     '''        joint = re.search(
            r"(?i)mec-?aBSs*(?:or|and|/|BS+|,)BSs*mec-?c"
            r"|mec-?cBSs*(?:or|and|/|BS+|,)BSs*mec-?a"
            r"|mec-?aBSs*/BSs*mec-?c|either mec", a)'''.replace("BS", B),
     '''        joint = re.search(r"(?i)BSbmec-?cBSb", a)'''.replace("BS", B),
     "trap-meca-mecc-excluded.jsonl"),

    # The two halves of the 17 Sep 15:50 blindness, mutated separately because
    # they fail the same guard for different reasons and one mutation could not
    # tell them apart. Both leave the REFUSAL COUNT at 2 and change only which
    # reason is given, which is why the guard asserts tags and not a count.
    ("collect() back to the q/r/s prefix list",
     '        paths = sorted(model_dir.glob("*.jsonl"))',
     '        paths = sorted(q for pat in ("q*.jsonl", "r*.jsonl", "s*.jsonl")'
     ' for q in model_dir.glob(pat))',
     "[set-conflict] fixture_model-bobby-lanes/q03.jsonl"),

    ("qid_conflict back to the hardcoded RS alphabet",
     """
    mine = set_of(t.qid)
    theirs = set_of(t.qid_by_path)""",
     """
    mine = (t.qid or "")[:1]
    theirs = (t.qid_by_path or "")[:1]
    mine = mine if mine in "RS" else ""
    theirs = theirs if theirs in "RS" else \"\"""",
     "[no-rubric] fixture_model-bobby-lanes/q03.jsonl"),

    # The two wrong denominators, mutated separately because they are wrong for
    # different reasons and a reader has to be told which one was used. Both set
    # the same field, so one mutation could not distinguish them.
    ("denominator back to the file count (counts survivors)",
     '"expected": len(want) or None, "src": src_name,',
     '"expected": len(names) or None, "src": src_name,',
     "denominator guard: a B lane"),

    ("denominator back to the scorecard (absent on 7 of 13 live runs)",
     '"expected": len(want) or None, "src": src_name,',
     '"expected": (att[0] if att else None), "src": src_name,',
     "denominator guard: a B lane"),

    # Placeholder detection. Reverting it is the 16:07 state exactly: fifteen
    # stubs scored as a model that failed every question.
    ("placeholder transcripts scored instead of refused",
     'if re.fullmatch(r"(?i)BSs*questionBSs+BSd+BSs*", t.question or ""):'.replace("BS", chr(92)),
     'if False:',
     "placeholder guard: a stub question"),
]


def selftest() -> str:
    r = subprocess.run([sys.executable, str(J), "--self-test"],
                       capture_output=True, text=True, cwd=EVALS)
    return r.stdout + r.stderr


fails = []
for label, old, new, want in MUTATIONS:
    if ORIG.count(old) != 1:
        fails.append(f"ANCHOR MISSED ({ORIG.count(old)}x): {label}")
        continue
    J.write_text(ORIG.replace(old, new), encoding="utf-8")
    try:
        out = selftest()
    finally:
        J.write_text(ORIG, encoding="utf-8")
    red = "SELF-TEST FAILED" in out
    named = any(want in ln for ln in out.splitlines()
                if ln.strip().startswith(("FAIL", "!", "- ")) or "no fixture" in ln)
    print(f"{'ok  ' if red and named else 'BAD '} {label}")
    print(f"       suite red: {red}   {want} implicated: {named}")
    for ln in out.splitlines():
        if ln.strip().startswith(("FAIL", "!", "- ")):
            print("      ", ln.strip()[:110])
    if not (red and named):
        fails.append(label)

assert J.read_text(encoding="utf-8") == ORIG, "judge.py NOT restored"
after = selftest()
print("\nrestored; suite is",
      "green again" if "SELF-TEST PASSED" in after else "STILL RED -- investigate")
print([ln for ln in after.splitlines() if "SELF-TEST" in ln])
if fails:
    print("\nMUTATIONS THAT DID NOT REGISTER:", *fails, sep="\n  ")
    sys.exit(1)
