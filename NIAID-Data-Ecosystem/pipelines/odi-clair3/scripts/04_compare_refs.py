#!/usr/bin/env python3
"""Compare Clair3 calls made against the two MRSA252 assemblies.

Why this exists
---------------
BX571856.1 (2,902,619 bp) and CP194230 (2,902,592 bp) are both "MRSA252" but
they are different assemblies, 27 bp apart in length. Gordon et al. 2014 --
the source of the resistance-SNP catalogue Street et al. used -- is indexed
against BX571856.1. If we were to call resistance off CP194230 coordinates
without checking, every catalogue lookup downstream of the first indel would
be silently off by some unknown offset.

This script does not attempt a full liftover. It answers the narrower and more
useful question: for this sample, do the two references agree on the variants
they find, and where they disagree, is the disagreement a constant offset
(benign, liftable) or scattered (a real assembly difference)?

Usage:
    04_compare_refs.py <run_accession> [--work DIR]
    04_compare_refs.py --all [--cohort FILE]
"""

from __future__ import annotations

import argparse
import gzip
import os
import sys
from collections import Counter
from pathlib import Path


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


def work_dir(override: str | None) -> Path:
    if override:
        return Path(override)
    return Path(os.environ.get("ODI_WORK", pipe_root() / "work"))


def read_vcf(path: Path) -> dict[int, tuple[str, str, float]]:
    """Return {pos: (ref, alt, qual)} for PASS SNVs in a Clair3 VCF."""
    if not path.exists():
        raise FileNotFoundError(path)

    calls: dict[int, tuple[str, str, float]] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 7:
                continue
            pos, ref, alt, qual, filt = int(f[1]), f[3], f[4], f[5], f[6]
            if filt not in ("PASS", "."):
                continue
            if alt in (".", "<NON_REF>"):
                continue
            # Restrict to SNVs; the resistance catalogue is SNV-based and
            # indel representation differs too much between assemblies to
            # compare naively.
            if len(ref) != 1 or len(alt) != 1:
                continue
            try:
                q = float(qual)
            except ValueError:
                q = 0.0
            calls[pos] = (ref, alt, q)
    return calls


def compare(run: str, work: Path) -> dict:
    vcf_dir = work / "vcf"
    prim = read_vcf(vcf_dir / f"{run}.primary" / "merge_output.vcf.gz")
    sec = read_vcf(vcf_dir / f"{run}.secondary" / "merge_output.vcf.gz")

    shared_pos = set(prim) & set(sec)
    only_prim = set(prim) - set(sec)
    only_sec = set(sec) - set(prim)

    concordant = sum(1 for p in shared_pos if prim[p][:2] == sec[p][:2])
    discordant = len(shared_pos) - concordant

    # For calls unique to one reference, look for a constant offset: if
    # CP194230 is simply shifted by k bp in some region, primary-only calls
    # will have secondary-only partners at a repeated delta.
    offsets: Counter[int] = Counter()
    sec_sorted = sorted(only_sec)
    for p in only_prim:
        for q in sec_sorted:
            d = q - p
            if -200 <= d <= 200 and d != 0 and prim[p][:2] == sec[q][:2]:
                offsets[d] += 1

    return {
        "run": run,
        "primary_calls": len(prim),
        "secondary_calls": len(sec),
        "shared_positions": len(shared_pos),
        "concordant": concordant,
        "discordant": discordant,
        "primary_only": len(only_prim),
        "secondary_only": len(only_sec),
        "top_offsets": offsets.most_common(5),
    }


def report(r: dict) -> str:
    lines = [
        f"=== {r['run']} ===",
        f"  calls on BX571856.1 (primary)  : {r['primary_calls']}",
        f"  calls on CP194230   (secondary): {r['secondary_calls']}",
        f"  positions called by both       : {r['shared_positions']}",
        f"    same allele                  : {r['concordant']}",
        f"    different allele             : {r['discordant']}",
        f"  primary only                   : {r['primary_only']}",
        f"  secondary only                 : {r['secondary_only']}",
    ]
    if r["top_offsets"]:
        lines.append("  recurring coordinate offsets (delta: count):")
        for d, n in r["top_offsets"]:
            lines.append(f"    {d:+d} bp : {n}")
        lines.append("  -> a dominant single offset means the two assemblies "
                     "are")
        lines.append("     liftable in this region; scattered offsets mean "
                     "they are not.")
    else:
        lines.append("  no recurring offsets found among reference-unique "
                     "calls")

    if r["discordant"]:
        lines.append(f"  WARNING: {r['discordant']} position(s) called with "
                     "DIFFERENT alleles by the two references.")
    return "\n".join(lines)


def cohort_runs(path: Path) -> list[str]:
    runs = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.split()
            if cols[0] == "run_accession" or cols[0] == "run":
                continue
            runs.append(cols[0])
    return runs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", help="run accession, e.g. ERR5260785")
    ap.add_argument("--all", action="store_true", help="every run in cohort")
    ap.add_argument("--cohort", help="cohort TSV (default samples.tsv)")
    ap.add_argument("--work", help="override ODI_WORK")
    args = ap.parse_args()

    work = work_dir(args.work)

    if args.all:
        cohort = Path(args.cohort or os.environ.get(
            "ODI_COHORT", pipe_root() / "samples.tsv"))
        if not cohort.is_absolute():
            cohort = pipe_root() / cohort
        runs = cohort_runs(cohort)
    elif args.run:
        runs = [args.run]
    else:
        ap.error("give a run accession or --all")

    failures = 0
    for run in runs:
        try:
            print(report(compare(run, work)))
            print()
        except FileNotFoundError as e:
            print(f"=== {run} ===\n  missing VCF: {e}\n", file=sys.stderr)
            failures += 1

    return 1 if failures == len(runs) else 0


if __name__ == "__main__":
    sys.exit(main())
