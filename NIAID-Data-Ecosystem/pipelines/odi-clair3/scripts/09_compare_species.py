#!/usr/bin/env python3
"""Compare BV-BRC species calls against Street et al. Table S3.

This is the scoring step for the BV-BRC arm: take the taxonomic classification
output, apply abundance filtering, and report per-specimen agreement with the
culture result and with the authors' own sequencing call.

Bracken, not raw Kraken2
------------------------
Read Bracken's `*_bracken_output.txt`, not Kraken2's `*_k2_report.txt`. On
these long reads Kraken2 leaves most of the signal stranded at internal nodes:
in sample 119, 1,729,095 of 2,498,346 Staphylococcus reads (69%) stop at the
GENUS node and never reach a species, so a naive species/bacteria ratio reads
28% for an organism that is in fact ~100% of the sample. Bracken redistributes
those reads down to species using the database's k-mer distribution, turning
28% into 99.99%.

Using the raw Kraken2 report here would not merely be noisy, it would be
wrong in a way that looks plausible.

Filtering
---------
Street et al. filtered with thresholds chosen by maximising the Youden index:

    >50% reference genome coverage
    or, for low-biomass samples, >60% of total bacterial bases from one
    species PLUS >=700 reads with >80% of read bases mapping to the reference

Coverage-based criteria need a per-candidate-species alignment that the
BV-BRC service does not perform, so this applies the abundance half of the
same idea: a minimum fraction of classified reads plus a minimum absolute
read count. That is a SUBSTITUTE and is reported as one -- agreement rates
here are not directly comparable to the paper's 77% PPA / 90% NPA.

Defaults (--min-frac 0.05, --min-reads 700) are deliberately more permissive
than the paper's 60%, so co-infections and runner-up calls stay visible.

Usage:
    09_compare_species.py                       # uses results/bracken/
    09_compare_species.py --reports DIR --min-frac 0.10
    09_compare_species.py --out results/species_comparison.tsv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

HUMAN = "homo sapiens"


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ truth data

def load_truth(path: Path) -> dict[str, dict]:
    """Table S3 keyed by sample number."""
    truth: dict[str, dict] = {}
    with open(path) as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            n = row["sample_number"]
            if not n:
                continue
            rec = truth.setdefault(n, {
                "culture_sonication": row["sonication_species"],
                "culture_tissue": row["tissue_culture_species"],
                "device": row["device_type"],
                "their_seq": [],
            })
            sp = row["sequencing_species"].strip()
            if sp:
                rec["their_seq"].append(sp)
    return truth


# ---------------------------------------------------------------------- bracken

def parse_bracken(path: Path) -> list[dict]:
    """Parse a Bracken `_bracken_output.txt` into species rows."""
    rows: list[dict] = []
    with open(path) as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                rows.append({
                    "name": row["name"].strip(),
                    "taxid": row["taxonomy_id"].strip(),
                    "kraken_reads": int(row["kraken_assigned_reads"]),
                    "est_reads": int(row["new_est_reads"]),
                    "frac_total": float(row["fraction_total_reads"]),
                })
            except (KeyError, ValueError):
                continue
    return rows


def call_species(rows: list[dict], min_frac: float,
                 min_reads: int) -> tuple[list[dict], int, int]:
    """Species passing the abundance filter, most abundant first.

    Human reads are excluded from the denominator -- the deposit is already
    host-depleted and the residue is trace, but a microbial fraction should
    be a fraction of microbial reads regardless.
    """
    microbial = [r for r in rows if r["name"].lower() != HUMAN]
    human = sum(r["est_reads"] for r in rows if r["name"].lower() == HUMAN)
    denom = sum(r["est_reads"] for r in microbial)
    if denom <= 0:
        return [], 0, human

    hits = []
    for r in microbial:
        frac = r["est_reads"] / denom
        if frac >= min_frac and r["est_reads"] >= min_reads:
            hits.append({**r, "frac_microbial": frac})
    hits.sort(key=lambda x: -x["est_reads"])
    return hits, denom, human


# ------------------------------------------------------------------- comparison

def norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip().lower())
    # Drop subspecies/strain tails and unnamed-species markers.
    s = re.sub(r"\b(subsp|sp|spp)\.?\b.*$", "", s).strip()
    return s


def genus_of(s: str) -> str:
    n = norm(s)
    return n.split(" ")[0] if n else ""


def agreement(ours: str, theirs: str) -> str:
    """Compare two species names at species then genus level."""
    if not ours or not theirs:
        return "no_call"
    a, b = norm(ours), norm(theirs)
    if a == b:
        return "species_match"
    if genus_of(a) and genus_of(a) == genus_of(b):
        return "genus_match"
    return "mismatch"


def best_agreement(hits: list[dict], reference: str) -> tuple[str, str]:
    """Best verdict across all passing hits, and which one achieved it.

    Scored across every passing species rather than the top one alone: a
    genuine co-infection should not be marked a mismatch because the
    cultured organism came second.
    """
    order = {"species_match": 3, "genus_match": 2, "mismatch": 1,
             "no_call": 0}
    best, who = "no_call", ""
    for h in hits:
        v = agreement(h["name"], reference)
        if order[v] > order[best]:
            best, who = v, h["name"]
    return best, who


# --------------------------------------------------------------------- plumbing

def infer_sample(stem: str, explicit: dict[str, str]) -> tuple[str | None, str]:
    """Map a filename to a (Table S3 sample number, preparation).

    BV-BRC names its output from our sample_id (p119_sap), so both fall out.
    Prep matters: a specimen can appear twice, once per aliquot, and the two
    must not be conflated -- the sap/fil pair IS the depletion comparison.
    An explicit --sample-map always wins.
    """
    m = re.search(r"p(\d+)_(sap|fil)", stem)
    prep = m.group(2) if m else ""
    for key, val in explicit.items():
        if key in stem:
            return val, prep
    if m:
        return m.group(1), prep
    m2 = re.search(r"patient[_-]?(\d+)", stem, re.I)
    return (m2.group(1) if m2 else None), prep


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", default=str(
        pipe_root() / "results" / "bracken"))
    ap.add_argument("--glob", default="*bracken_output*")
    ap.add_argument("--truth", default=str(
        pipe_root() / "catalogue" / "street2022_table_s3.tsv"))
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--min-reads", type=int, default=700)
    ap.add_argument("--sample-map", default="")
    ap.add_argument("--out")
    args = ap.parse_args()

    truth_path = Path(args.truth)
    if not truth_path.exists():
        print(f"missing {truth_path}\nRun 07_extract_truth.py first.",
              file=sys.stderr)
        return 1
    truth = load_truth(truth_path)

    rdir = Path(args.reports)
    if not rdir.is_dir():
        print(f"not a directory: {rdir}", file=sys.stderr)
        return 1

    files = sorted(p for p in rdir.rglob(args.glob) if p.is_file())
    if not files:
        print(f"No files matching {args.glob!r} under {rdir}.",
              file=sys.stderr)
        print("Download each job's <sample>/bracken_output/ folder from the "
              "BV-BRC\nworkspace, or widen --glob.", file=sys.stderr)
        return 1

    explicit = dict(
        kv.split("=", 1) for kv in args.sample_map.split(",") if "=" in kv)

    print(f"{len(files)} sample(s) from {rdir}")
    print(f"filter: >={args.min_frac:.0%} of microbial reads "
          f"and >={args.min_reads} reads")
    print()

    detail: list[dict] = []
    tally: dict[str, int] = {}

    # Sort by specimen, then saponin before filtration so paired aliquots
    # print adjacently.
    for path in sorted(files, key=lambda p: (
            int(infer_sample(p.stem, explicit)[0] or 0),
            infer_sample(p.stem, explicit)[1] != "sap")):
        sample, prep = infer_sample(path.stem, explicit)
        rows = parse_bracken(path)
        if not rows:
            print(f"=== {path.name} ===\n  no parseable Bracken rows\n")
            continue

        hits, denom, human = call_species(rows, args.min_frac, args.min_reads)
        t = truth.get(sample or "", {})
        culture = t.get("culture_sonication", "")
        theirs = "; ".join(t.get("their_seq", []))

        label = f"sample {sample or '?'}"
        if prep:
            label += f" [{prep}]"
        print(f"=== {label} ({t.get('device', 'unknown device')}) ===")
        print(f"  microbial reads : {denom:,}   human: {human:,} "
              f"({human / (denom + human) * 100:.3f}%)"
              if denom + human else "")
        print(f"  culture         : {culture or '-'}")
        print(f"  their sequencing: {theirs or '-'}")
        for h in hits:
            print(f"  ours            : {h['name']:<32} "
                  f"{h['est_reads']:>9,}  {h['frac_microbial']:7.3%}")
        if not hits:
            print("  ours            : nothing passed the filter")

        v_cult, who_c = best_agreement(hits, culture)
        v_theirs, _ = best_agreement(
            hits, t["their_seq"][0] if t.get("their_seq") else "")
        if culture.lower() == "negative":
            v_cult = "culture_negative"
        print(f"  vs culture      : {v_cult}"
              + (f" ({who_c})" if who_c and who_c != culture else ""))
        print(f"  vs their seq    : {v_theirs}")
        print()

        # Count each SPECIMEN once. Filtration aliquots are the same
        # specimen re-prepared, so counting both would inflate agreement.
        if prep != "fil":
            tally[v_cult] = tally.get(v_cult, 0) + 1

        for h in hits:
            detail.append({
                "sample": sample or "",
                "prep": prep,
                "species": h["name"],
                "taxid": h["taxid"],
                "est_reads": h["est_reads"],
                "kraken_reads": h["kraken_reads"],
                "frac_microbial": f"{h['frac_microbial']:.5f}",
                "culture_sonication": culture,
                "their_sequencing": theirs,
                "verdict_vs_culture": v_cult,
                "verdict_vs_their_seq": v_theirs,
            })

    print("-" * 64)
    for k in sorted(tally):
        print(f"  {k:20s} {tally[k]}")
    print()
    print("The abundance filter here SUBSTITUTES for the paper's")
    print("Youden-optimized coverage criteria, which need per-species")
    print("alignment the BV-BRC service does not perform. These agreement")
    print("counts are not directly comparable to the paper's 77% PPA /")
    print("90% NPA over 115 specimens.")

    if detail and args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(detail[0]), delimiter="\t")
            w.writeheader()
            w.writerows(detail)
        print(f"\nper-species detail -> {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
