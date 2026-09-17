#!/usr/bin/env python3
"""Compare saponin vs filtration aliquots on microbial yield and coverage.

Reads the `samtools coverage` output written by 02_map.sh and pairs the sap
and fil runs from the same patient.

What this can and cannot show
-----------------------------
CANNOT: the human fraction. Street et al. deposited only reads "classified as
nonhuman", so the human reads are gone from the public data. The 98.1% -> 11.9%
figure in their Table is not recoverable from PRJEB42910 and any script
claiming to reproduce it is measuring something else.

CAN: the downstream consequence that actually matters -- how much microbial
sequence and how much pathogen genome breadth each preparation yields. That is
what determines whether the >=20x depth needed for AMR calling is reachable,
which is the paper's practical claim.

Usage:
    05_depletion_summary.py [--work DIR] [--cohort FILE]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_samples(cohort: Path) -> list[dict]:
    rows = []
    with open(cohort) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            c = line.split()
            if c[0] in ("run_accession", "run"):
                continue
            rows.append({
                "run": c[0],
                "patient": c[1],
                "prep": c[2],
                "reads": int(c[3]),
                "bases": int(c[4]),
            })
    return rows


def load_coverage(work: Path, run: str, reftag: str = "primary") -> dict | None:
    """Parse `samtools coverage` output for one run."""
    path = work / "bam" / f"{run}.{reftag}.coverage.txt"
    if not path.exists():
        return None
    with open(path) as fh:
        header = fh.readline().lstrip("#").split()
        line = fh.readline().split()
    if not line:
        return None
    rec = dict(zip(header, line))
    return {
        "numreads": int(rec.get("numreads", 0)),
        "covbases": int(rec.get("covbases", 0)),
        "coverage": float(rec.get("coverage", 0.0)),   # % breadth
        "meandepth": float(rec.get("meandepth", 0.0)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work")
    ap.add_argument("--cohort")
    args = ap.parse_args()

    work = Path(args.work or os.environ.get("ODI_WORK", pipe_root() / "work"))
    cohort = Path(args.cohort or os.environ.get(
        "ODI_COHORT", pipe_root() / "samples.tsv"))
    if not cohort.is_absolute():
        cohort = pipe_root() / cohort

    samples = load_samples(cohort)

    by_patient: dict[str, dict[str, dict]] = {}
    for s in samples:
        by_patient.setdefault(s["patient"], {})[s["prep"]] = s

    paired = {p: v for p, v in by_patient.items() if "sap" in v and "fil" in v}
    if not paired:
        print(f"No sap/fil pairs in {cohort.name}", file=sys.stderr)
        return 1

    print("Saponin vs filtration, paired aliquots from the same specimen")
    print("(deposited non-human reads only -- see docstring)")
    print()
    print(f"{'patient':>8}  {'prep':>4}  {'run':>12}  {'Gb':>7}  "
          f"{'mapped':>9}  {'breadth%':>9}  {'depth':>7}")
    print("-" * 68)

    any_cov = False
    for patient in sorted(paired, key=int):
        for prep in ("sap", "fil"):
            s = paired[patient][prep]
            cov = load_coverage(work, s["run"])
            gb = s["bases"] / 1e9
            if cov:
                any_cov = True
                print(f"{patient:>8}  {prep:>4}  {s['run']:>12}  {gb:7.3f}  "
                      f"{cov['numreads']:9d}  {cov['coverage']:9.2f}  "
                      f"{cov['meandepth']:7.2f}")
            else:
                print(f"{patient:>8}  {prep:>4}  {s['run']:>12}  {gb:7.3f}  "
                      f"{'-':>9}  {'-':>9}  {'-':>7}")
        sap, fil = paired[patient]["sap"], paired[patient]["fil"]
        ratio = sap["bases"] / fil["bases"] if fil["bases"] else float("inf")
        print(f"{'':>8}  {'':>4}  {'yield ratio':>12}  {ratio:6.1f}x")
        print()

    if not any_cov:
        print("No coverage files yet -- run 02_map.sh to populate them.",
              file=sys.stderr)
        print("Yield ratios above come from ENA metadata and are already "
              "valid.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
