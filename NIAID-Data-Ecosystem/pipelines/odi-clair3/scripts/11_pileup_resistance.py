#!/usr/bin/env python3
"""Genotype resistance codons directly from the pileup, not from a VCF.

Why this exists
---------------
`08_call_resistance.py` reads a Clair3 VCF. Clair3 is a CONSENSUS caller: it
emits records where the consensus differs from the reference and is largely
silent elsewhere. On sample 41 it produced 58,999 genome-wide records but
covered only **1 of 116** catalogue codons -- and crucially it emitted nothing
at rpoB codon 477, where the raw pileup shows a clean 20.7% A477V
subpopulation (46 of 222 reads).

That is the exact variant Street et al. reported missing, for the exact same
reason: consensus calling cannot see a minority allele. A reproduction that
inherits the flaw is not a reproduction of interest.

This script sidesteps the caller. For each of the 116 catalogue codons it
counts observed bases at the three genome positions, builds the codon from
whichever bases are present above --min-af, translates, and compares to the
catalogue. Sub-consensus alleles are therefore first-class.

It shares the reference-bias logic of `08`: MRSA252 itself carries gyrA S84L,
grlA S80F and fusA H557Y, so the question is never "is there a variant" but
"what amino acid is present, and at what fraction".

Requires: samtools on PATH.

Usage:
    11_pileup_resistance.py ERR5260555
    11_pileup_resistance.py --all --cohort amr_cohort.tsv
    11_pileup_resistance.py ERR5260555 --min-af 0.05
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the catalogue/locus/translation machinery so the two callers cannot
# drift apart in their coordinate handling.
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "_rc", Path(__file__).resolve().parent / "08_call_resistance.py")
_rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rc)

Reference = _rc.Reference
load_loci = _rc.load_loci
load_catalogue = _rc.load_catalogue
resolve_ambiguous = _rc.resolve_ambiguous
translate_codon = _rc.translate_codon
COMPLEMENT = _rc.COMPLEMENT


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------- pileup

# mpileup read-base column: strip read-start markers (^X), read ends ($), and
# indel run-length encodings (+3ACG / -2TT) before counting.
_CARET = re.compile(r"\^.")
_INDEL = re.compile(r"[+-](\d+)")


def clean_bases(s: str) -> str:
    s = _CARET.sub("", s).replace("$", "")
    out, i = [], 0
    while i < len(s):
        m = _INDEL.match(s, i)
        if m:
            i = m.end() + int(m.group(1))     # skip the indel sequence
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def pileup_counts(bam: Path, ref: Path, positions: list[int],
                  contig: str, min_bq: int) -> dict[int, Counter]:
    """Base counts per position via `samtools mpileup`."""
    if not positions:
        return {}
    lo, hi = min(positions), max(positions)
    cmd = ["samtools", "mpileup", "-f", str(ref), "-r",
           f"{contig}:{lo}-{hi}", "-Q", str(min_bq), "-d", "0",
           "--no-output-ins", "--no-output-del", str(bam)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"samtools mpileup failed: {e.stderr[:200]}", file=sys.stderr)
        return {}
    except FileNotFoundError:
        print("samtools not found on PATH", file=sys.stderr)
        sys.exit(1)

    wanted = set(positions)
    counts: dict[int, Counter] = {}
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) < 5:
            continue
        pos = int(f[1])
        if pos not in wanted:
            continue
        refbase = f[2].upper()
        c: Counter = Counter()
        for ch in clean_bases(f[4]):
            if ch in ".,":
                c[refbase] += 1
            elif ch.upper() in "ACGT":
                c[ch.upper()] += 1
        counts[pos] = c
    return counts


def codon_alleles(counts: dict[int, Counter], positions: list[int],
                  strand: str, ref: Reference,
                  min_af: float) -> list[tuple[str, float, int]]:
    """Candidate codons with their limiting allele fraction and depth.

    Each position contributes every base seen at >= min_af. The cartesian
    product over three positions is capped -- in practice at most one position
    in a codon is polymorphic, and combinatorial explosion here would mean
    the data is noise anyway.
    """
    per_pos: list[list[tuple[str, float]]] = []
    depths: list[int] = []
    for p in positions:
        c = counts.get(p, Counter())
        tot = sum(c.values())
        depths.append(tot)
        if tot == 0:
            return []
        alleles = [(b, n / tot) for b, n in c.items() if n / tot >= min_af]
        if not alleles:
            alleles = [(c.most_common(1)[0][0], 1.0)]
        per_pos.append(sorted(alleles, key=lambda x: -x[1]))

    if len(per_pos[0]) * len(per_pos[1]) * len(per_pos[2]) > 12:
        per_pos = [a[:1] for a in per_pos]

    out: list[tuple[str, float, int]] = []
    for b0, f0 in per_pos[0]:
        for b1, f1 in per_pos[1]:
            for b2, f2 in per_pos[2]:
                bases = [b0, b1, b2]
                if strand == "-":
                    bases = [b.translate(COMPLEMENT) for b in bases]
                out.append(("".join(bases), min(f0, f1, f2), min(depths)))
    out.sort(key=lambda x: -x[1])
    return out


# ---------------------------------------------------------------------- caller

def call_sample(run: str, reftag: str, ref: Reference, loci, entries,
                work: Path, min_depth: int, min_af: float,
                min_bq: int) -> list[dict]:
    bam = work / "bam" / f"{run}.{reftag}.bam"
    if not bam.exists():
        raise FileNotFoundError(bam)

    contig = ref.name
    ref_fa = (pipe_root() / "refs" / f"{ref.name}.fasta")

    # One mpileup per gene rather than per codon: three orders of magnitude
    # fewer subprocess launches.
    by_gene: dict[str, list] = {}
    for e in entries:
        by_gene.setdefault(e.gene, []).append(e)

    results: list[dict] = []
    for gene, ents in by_gene.items():
        loc = loci[gene]
        allpos = sorted({p for e in ents
                         for p in loc.codon_positions(e.codon)})
        counts = pileup_counts(bam, ref_fa, allpos, contig, min_bq)

        for e in ents:
            positions = loc.codon_positions(e.codon)
            cands = codon_alleles(counts, positions, loc.strand, ref, min_af)
            depth = min((sum(counts.get(p, Counter()).values())
                         for p in positions), default=0)

            ref_aa = loc.aa_at(ref, e.codon)
            ref_is_res = (ref_aa == e.alt_aa)

            aas = [(translate_codon(c), f) for c, f, _ in cands]
            cons_aa = aas[0][0] if aas else ""
            hit = next(((a, f) for a, f in aas if a == e.alt_aa), None)

            if depth == 0:
                verdict = "no_data"
            elif depth < min_depth:
                verdict = "insufficient_coverage"
            elif hit and hit[1] >= 0.5:
                verdict = "resistant"
            elif hit:
                verdict = "resistant_subpopulation"
            else:
                verdict = "susceptible"

            results.append({
                "run": run,
                "reftag": reftag,
                "gene": gene,
                "substitution": e.substitution,
                "drug": e.drug,
                "codon": e.codon,
                "genome_pos": positions[0],
                "ref_aa": ref_aa,
                "sample_aa": cons_aa,
                "resistant_af": f"{hit[1]:.3f}" if hit else "0.000",
                "codon_depth": depth,
                "ref_is_resistant": "1" if ref_is_res else "0",
                # "Observed" means the resistant amino acid DIFFERS from the
                # reference, i.e. read evidence put it there. Do not infer
                # this from the allele fraction: at a codon where the
                # reference is already resistant the fraction is ~1.0 either
                # way, and thresholding it (an earlier bug) labelled AF 0.988
                # as observed while AF 0.996 was not, for the same position
                # in different samples.
                "observed_variant": "1" if (hit and not ref_is_res) else "0",
                "reported_mic": e.mic,
                "verdict": verdict,
            })
    return results


def summarize(rows: list[dict]) -> str:
    drugs: dict[str, list[dict]] = {}
    for r in rows:
        drugs.setdefault(r["drug"], []).append(r)

    out = []
    for drug in sorted(drugs):
        rs = drugs[drug]
        res = [r for r in rs if r["verdict"] == "resistant"]
        sub = [r for r in rs if r["verdict"] == "resistant_subpopulation"]
        low = [r for r in rs if r["verdict"] in
               ("insufficient_coverage", "no_data")]

        if res:
            def tag(r):
                base = f"{r['gene']} {r['substitution']}"
                # Flag calls that rest on matching a resistant reference
                # rather than on an observed variant.
                return base + (" [ref allele]"
                               if r["observed_variant"] == "0" else "")
            out.append(f"  {drug:16s} RESISTANT  "
                       f"({', '.join(tag(r) for r in res)})")
        elif sub:
            hits = ", ".join(
                f"{r['gene']} {r['substitution']} @ AF {r['resistant_af']}"
                for r in sub)
            out.append(f"  {drug:16s} RESISTANT (subpopulation)  ({hits})")
        elif low and len(low) == len(rs):
            out.append(f"  {drug:16s} insufficient coverage "
                       "-- NOT a susceptible call")
        else:
            out.append(f"  {drug:16s} susceptible")
    return "\n".join(out)


def cohort_runs(path: Path) -> list[str]:
    runs = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            c = line.split()
            if c[0] in ("run_accession", "run"):
                continue
            runs.append(c[0])
    return runs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="*",
                    help="one or more run accessions")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--cohort", default="amr_cohort.tsv")
    ap.add_argument("--reftag", default="primary")
    ap.add_argument("--work")
    ap.add_argument("--min-depth", type=int,
                    default=int(os.environ.get("MIN_SNP_DEPTH", 20)))
    ap.add_argument("--min-af", type=float, default=0.10,
                    help="minimum allele fraction to consider (default 0.10)")
    ap.add_argument("--min-bq", type=int, default=7,
                    help="minimum base quality; nanopore Q is low, so the "
                         "samtools default of 13 discards real signal")
    ap.add_argument("--out")
    args = ap.parse_args()

    root = pipe_root()
    work = Path(args.work or os.environ.get("ODI_WORK", root / "work"))

    ref_fa = root / "refs" / ("BX571856.1.fasta" if args.reftag == "primary"
                              else "CP194230.fasta")
    if not ref_fa.exists():
        print(f"reference not found: {ref_fa}", file=sys.stderr)
        return 1

    ref = Reference(ref_fa)
    loci = load_loci(root / "catalogue" / "loci_BX571856.1.tsv")
    entries = load_catalogue(
        root / "catalogue" / "gordon2014_resistance_snps.tsv", loci)
    entries, dropped = resolve_ambiguous(entries, loci, ref)
    if dropped:
        print(f"note: {len(dropped)} Table S4 entries unassigned, dropped",
              file=sys.stderr)

    if args.all:
        cohort = Path(args.cohort)
        if not cohort.is_absolute():
            cohort = root / cohort
        runs = cohort_runs(cohort)
    elif args.run:
        runs = list(args.run)
    else:
        ap.error("give a run accession or --all")

    all_rows: list[dict] = []
    for run in runs:
        try:
            rows = call_sample(run, args.reftag, ref, loci, entries, work,
                               args.min_depth, args.min_af, args.min_bq)
        except FileNotFoundError as e:
            print(f"=== {run} ===\n  no BAM ({e}); run 02_map.sh\n",
                  file=sys.stderr)
            continue
        all_rows.extend(rows)
        print(f"=== {run} ({args.reftag}, pileup, min_af={args.min_af}) ===")
        print(summarize(rows))
        n_obs = sum(1 for r in rows if r["observed_variant"] == "1")
        print(f"  [{n_obs} observed variant(s) among "
              f"{len(rows)} catalogue codons]")
        print()

    if all_rows:
        out = Path(args.out) if args.out else (
            work / f"resistance_pileup.{args.reftag}.tsv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(all_rows[0]),
                               delimiter="\t")
            w.writeheader()
            w.writerows(all_rows)
        print(f"per-codon detail -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
