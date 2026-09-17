#!/usr/bin/env python3
"""Turn Clair3 VCFs into resistance calls against the Gordon 2014 catalogue.

This is the step that bridges the two coordinate systems. Clair3 reports
genome positions in BX571856.1; the catalogue is amino-acid substitutions in
six proteins. For each catalogue entry we locate the three genome bases of its
codon, read what the sample actually has there, translate, and compare.

The reference-bias trap
-----------------------
MRSA252 is itself quinolone-resistant. It carries gyrA S84L, grlA S80F and
grlB P451S -- three alleles that are in the catalogue as RESISTANT. So at
those codons the reference base is the resistant state, and:

  - a susceptible sample DIFFERS from the reference there and would be called
    resistant by any "variant != reference" rule;
  - a resistant sample MATCHES the reference and emits no VCF record at all,
    so the same rule reports nothing.

Both errors point the wrong way. This script therefore never asks "is there a
variant here". It asks "what amino acid does this sample have at this codon",
reading the reference base where the VCF is silent, and compares that to the
catalogue. Absence of a VCF record is evidence of the reference allele, which
at these three codons means resistant.

Coverage is a third state
-------------------------
Street et al. required >=20x depth at a resistance codon before calling it,
and 60 of their 152 drug-organism combinations failed that bar. Those are
reported here as `insufficient_coverage`, never folded into `susceptible`.
A codon with no reads is not a susceptible codon.

Mixed populations
-----------------
The paper missed a resistant rpoB A477V present in ~20% of reads in sample 41,
because their pipeline took consensus calls. Clair3 emits allele depths, so
this script reports the alternate-allele fraction and flags any resistant
allele seen above --min-af (default 0.10) even when it is not the consensus.
Those appear as `resistant_subpopulation`.

Usage:
    08_call_resistance.py <run_accession> [--reftag primary]
    08_call_resistance.py --all [--cohort amr_cohort.tsv]
    08_call_resistance.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------- genetic code

_B = "TCAG"
_AA = ("FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVV"
       "AAAADDEEGGGG")
CODON_TABLE = {
    a + b + c: _AA[i]
    for i, (a, b, c) in enumerate((x, y, z) for x in _B for y in _B for z in _B)
}

COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")


def revcomp(s: str) -> str:
    return s.translate(COMPLEMENT)[::-1]


def translate_codon(codon: str) -> str:
    return CODON_TABLE.get(codon.upper(), "X")


# ------------------------------------------------------------------- reference

class Reference:
    """The genome sequence, 1-based accessor."""

    def __init__(self, fasta: Path):
        seq = []
        with open(fasta) as fh:
            for line in fh:
                if not line.startswith(">"):
                    seq.append(line.strip())
        self.seq = "".join(seq).upper()
        self.name = fasta.stem

    def __len__(self) -> int:
        return len(self.seq)

    def base(self, pos: int) -> str:
        """1-based single base."""
        return self.seq[pos - 1]

    def slice(self, start: int, end: int) -> str:
        """1-based inclusive."""
        return self.seq[start - 1:end]


class Locus:
    """One gene, with codon <-> genome coordinate mapping."""

    def __init__(self, gene: str, start: int, end: int, strand: str,
                 drug: str, locus_tag: str = ""):
        self.gene = gene
        self.start = start
        self.end = end
        self.strand = strand
        self.drug = drug
        self.locus_tag = locus_tag

    def codon_positions(self, codon: int) -> list[int]:
        """Genome positions (1-based, forward strand) of a codon's 3 bases.

        Returned 5'->3' in the GENE's orientation, so for a minus-strand gene
        the positions descend. Callers must complement the bases they read.
        """
        offset = (codon - 1) * 3
        if self.strand == "+":
            p = self.start + offset
            return [p, p + 1, p + 2]
        p = self.end - offset
        return [p, p - 1, p - 2]

    def codon_seq(self, ref: Reference, codon: int,
                  overrides: dict[int, str] | None = None) -> str:
        """The codon's bases in gene orientation, with optional per-position
        substitutions keyed by forward-strand genome position."""
        overrides = overrides or {}
        out = []
        for p in self.codon_positions(codon):
            b = overrides.get(p, ref.base(p))
            out.append(b if self.strand == "+" else b.translate(COMPLEMENT))
        return "".join(out)

    def aa_at(self, ref: Reference, codon: int,
              overrides: dict[int, str] | None = None) -> str:
        return translate_codon(self.codon_seq(ref, codon, overrides))


def load_loci(path: Path) -> dict[str, Locus]:
    loci: dict[str, Locus] = {}
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            c = line.rstrip("\n").split("\t")
            if c[0] == "gene":
                continue
            loci[c[0]] = Locus(gene=c[0], locus_tag=c[1], start=int(c[2]),
                               end=int(c[3]), strand=c[4], drug=c[6])
    return loci


# ------------------------------------------------------------------- catalogue

class Entry:
    def __init__(self, gene: str, ref_aa: str, codon: int, alt_aa: str,
                 drug: str, mic: str, ambiguous: bool):
        self.gene = gene
        self.ref_aa = ref_aa
        self.codon = codon
        self.alt_aa = alt_aa
        self.drug = drug
        self.mic = mic
        self.ambiguous = ambiguous

    @property
    def substitution(self) -> str:
        return f"{self.ref_aa}{self.codon}{self.alt_aa}"


def load_catalogue(path: Path, loci: dict[str, Locus]) -> list[Entry]:
    """Load catalogue entries, expanding ambiguous multi-gene rows.

    Table S4 of Gordon 2014 interleaves grlA, gyrA and grlB across columns and
    the flattened PDF text does not say which column a substitution came from.
    Rather than drop those 29 entries or guess one gene, we resolve each
    against the reference: a substitution belongs to whichever of the three
    genes actually has the catalogue's stated reference amino acid at that
    codon. If exactly one matches, the assignment is certain. If several or
    none do, the entry is dropped with a warning -- never guessed.
    """
    entries: list[Entry] = []
    unresolved: list[str] = []

    with open(path) as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            genes = ([row["gene"]] if not row["gene"].startswith("grlA|")
                     else ["grlA", "gyrA", "grlB"])
            e = Entry(row["gene"], row["ref_aa"], int(row["codon"]),
                      row["alt_aa"], row["drug"], row["reported_mic"],
                      row["gene_ambiguous"] == "True")
            if len(genes) == 1:
                if genes[0] in loci:
                    e.gene = genes[0]
                    entries.append(e)
                continue
            entries.append(e)          # resolve later, needs the reference
            e._candidates = genes      # noqa: SLF001
    return entries


def resolve_ambiguous(entries: list[Entry], loci: dict[str, Locus],
                      ref: Reference) -> tuple[list[Entry], list[str]]:
    """Assign multi-gene entries to a single gene using the reference AA.

    A candidate gene matches if the reference carries EITHER the catalogue's
    wild-type amino acid at that codon or its resistant one. Both are evidence
    that the codon belongs to this gene -- and the resistant case is not an
    edge case here: MRSA252 is quinolone-resistant, so for gyrA S84L,
    grlA S80F and grlB P451S the reference has the resistant allele and a
    wild-type-only test would find no match and discard exactly the entries
    that matter most.

    Wild-type matches are preferred when both kinds are available, since a
    wild-type match is the stronger signal; a resistant-allele match is
    accepted only when it is the unique candidate.
    """
    resolved: list[Entry] = []
    dropped: list[str] = []

    for e in entries:
        cands = getattr(e, "_candidates", None)
        if not cands:
            resolved.append(e)
            continue

        wt_hits, res_hits = [], []
        for g in cands:
            loc = loci.get(g)
            if not loc:
                continue
            n_aa = (loc.end - loc.start + 1) // 3
            if e.codon > n_aa:
                continue
            aa = loc.aa_at(ref, e.codon)
            if aa == e.ref_aa:
                wt_hits.append(g)
            elif aa == e.alt_aa:
                res_hits.append(g)

        hits = wt_hits or res_hits
        if len(hits) == 1:
            e.gene = hits[0]
            e.ambiguous = False
            e.ref_is_resistant_allele = bool(not wt_hits and res_hits)
            resolved.append(e)
        else:
            why = (f"ambiguous -- wild-type matches {wt_hits}, "
                   f"resistant matches {res_hits}" if hits
                   else f"matches no candidate of {cands} at either allele")
            dropped.append(f"{e.substitution} ({e.drug}): {why}")
    return resolved, dropped


# ------------------------------------------------------------------------- VCF

class VcfCalls:
    """Per-position calls from a Clair3 VCF, with depth and allele fraction."""

    def __init__(self):
        self.by_pos: dict[int, dict] = {}

    @classmethod
    def load(cls, path: Path) -> "VcfCalls":
        v = cls()
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                f = line.rstrip("\n").split("\t")
                if len(f) < 10:
                    continue
                pos, ref, alts, filt = int(f[1]), f[3], f[4], f[6]
                fmt, sample = f[8].split(":"), f[9].split(":")
                d = dict(zip(fmt, sample))

                dp = int(d["DP"]) if d.get("DP", ".").isdigit() else 0
                ads = [int(x) for x in d.get("AD", "").split(",")
                       if x.strip().lstrip("-").isdigit()]

                v.by_pos[pos] = {
                    "ref": ref,
                    "alts": alts.split(",") if alts not in (".", "") else [],
                    "filter": filt,
                    "dp": dp,
                    "ad": ads,
                    "gt": d.get("GT", "./."),
                }
        return v

    def depth_at(self, pos: int) -> int | None:
        rec = self.by_pos.get(pos)
        return rec["dp"] if rec else None


def load_depths(path: Path) -> dict[int, int]:
    """Per-base depth from `samtools depth -a` output (pos -> depth)."""
    depths: dict[int, int] = {}
    if not path.exists():
        return depths
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 3:
                depths[int(parts[1])] = int(parts[2])
    return depths


# ---------------------------------------------------------------------- caller

def call_sample(run: str, reftag: str, ref: Reference,
                loci: dict[str, Locus], entries: list[Entry],
                work: Path, min_depth: int, min_af: float) -> list[dict]:
    vcf_path = work / "vcf" / f"{run}.{reftag}" / "merge_output.vcf.gz"
    if not vcf_path.exists():
        raise FileNotFoundError(vcf_path)
    vcf = VcfCalls.load(vcf_path)
    depths = load_depths(work / "bam" / f"{run}.{reftag}.depth.txt")

    results: list[dict] = []

    for e in entries:
        loc = loci.get(e.gene)
        if not loc:
            continue

        positions = loc.codon_positions(e.codon)

        # Depth: prefer the per-base depth file, fall back to VCF DP, and if
        # neither is available say so rather than assuming coverage.
        obs = [depths.get(p, vcf.depth_at(p)) for p in positions]
        known = [d for d in obs if d is not None]
        codon_depth = min(known) if len(known) == len(positions) else None

        # What the reference says at this codon.
        ref_aa = loc.aa_at(ref, e.codon)
        ref_is_resistant = (ref_aa == e.alt_aa)

        # What the sample says. Consensus first: apply any PASS alt whose
        # allele fraction is the majority.
        cons_overrides: dict[int, str] = {}
        sub_overrides: dict[int, str] = {}
        afs: list[float] = []

        for p in positions:
            rec = vcf.by_pos.get(p)
            if not rec or not rec["alts"]:
                continue
            alt = rec["alts"][0]
            if len(rec["ref"]) != 1 or len(alt) != 1:
                continue        # indel; codon-level handling is out of scope
            af = 0.0
            if len(rec["ad"]) >= 2 and sum(rec["ad"]) > 0:
                af = rec["ad"][1] / sum(rec["ad"])
            afs.append(af)
            if af >= 0.5 and rec["filter"] in ("PASS", "."):
                cons_overrides[p] = alt
            if af >= min_af:
                sub_overrides[p] = alt

        cons_aa = loc.aa_at(ref, e.codon, cons_overrides)
        sub_aa = loc.aa_at(ref, e.codon, sub_overrides)
        max_af = max(afs) if afs else 0.0

        # Verdict. Note this never asks "is there a variant" -- it asks what
        # amino acid the sample has, which is what makes the three
        # reference-resistant codons behave correctly.
        if codon_depth is None:
            verdict = "no_data"
        elif codon_depth < min_depth:
            verdict = "insufficient_coverage"
        elif cons_aa == e.alt_aa:
            verdict = "resistant"
        elif sub_aa == e.alt_aa and sub_overrides != cons_overrides:
            verdict = "resistant_subpopulation"
        else:
            verdict = "susceptible"

        results.append({
            "run": run,
            "reftag": reftag,
            "gene": e.gene,
            "substitution": e.substitution,
            "drug": e.drug,
            "codon": e.codon,
            "genome_pos": positions[0],
            "ref_aa": ref_aa,
            "catalogue_ref_aa": e.ref_aa,
            "sample_aa": cons_aa,
            "subpop_aa": sub_aa if sub_aa != cons_aa else "",
            "max_alt_fraction": f"{max_af:.3f}",
            "codon_depth": "" if codon_depth is None else codon_depth,
            "ref_is_resistant": "1" if ref_is_resistant else "0",
            "reported_mic": e.mic,
            "verdict": verdict,
        })

    return results


def summarize(rows: list[dict]) -> str:
    """Per-drug summary. Any resistant hit makes the drug resistant; a drug is
    only susceptible if every one of its codons was callable and none hit."""
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
            hits = ", ".join(f"{r['gene']} {r['substitution']}" for r in res)
            call = f"RESISTANT  ({hits})"
        elif sub:
            hits = ", ".join(
                f"{r['gene']} {r['substitution']} @ AF {r['max_alt_fraction']}"
                for r in sub)
            call = f"RESISTANT (subpopulation)  ({hits})"
        elif low and len(low) == len(rs):
            call = "insufficient coverage -- NOT a susceptible call"
        elif low:
            call = (f"susceptible at {len(rs) - len(low)} codon(s); "
                    f"{len(low)} uncallable")
        else:
            call = "susceptible"
        out.append(f"  {drug:16s} {call}")
    return "\n".join(out)


# ---------------------------------------------------------------------- selftest

def self_test(ref: Reference, loci: dict[str, Locus],
              entries: list[Entry]) -> int:
    """Validate the coordinate mapping without needing any sample data."""
    print("Self-test: codon mapping against BX571856.1")
    print()

    fails = 0

    # 1. Every CDS should translate cleanly.
    print("  CDS integrity:")
    for g, loc in sorted(loci.items()):
        n_aa = (loc.end - loc.start + 1) // 3
        first = loc.aa_at(ref, 1)
        last = loc.aa_at(ref, n_aa)
        internal = sum(1 for c in range(1, n_aa)
                       if loc.aa_at(ref, c) == "*")
        ok = last == "*" and internal == 0
        # GTG/TTG starts translate as V/L but are still methionine in vivo.
        start_ok = first in ("M", "V", "L")
        if not (ok and start_ok):
            fails += 1
        print(f"    {g:6s} {n_aa:5d} aa  start={first} stop={last} "
              f"internal_stops={internal}  {'OK' if ok and start_ok else 'FAIL'}")

    # 2. The catalogue's stated reference AA should match the genome, except
    #    at codons where MRSA252 itself carries the resistant allele.
    print()
    print("  Catalogue reference-AA agreement:")
    agree = ref_resistant = disagree = 0
    ref_res_list = []
    for e in entries:
        loc = loci.get(e.gene)
        if not loc:
            continue
        n_aa = (loc.end - loc.start + 1) // 3
        if e.codon > n_aa:
            disagree += 1
            continue
        got = loc.aa_at(ref, e.codon)
        if got == e.ref_aa:
            agree += 1
        elif got == e.alt_aa:
            ref_resistant += 1
            ref_res_list.append(f"{e.gene} {e.substitution}")
        else:
            disagree += 1

    print(f"    reference matches catalogue wild-type : {agree}")
    print(f"    reference IS the resistant allele     : {ref_resistant}")
    print(f"    neither (investigate)                 : {disagree}")

    if ref_res_list:
        print()
        print("    MRSA252 carries these resistant alleles natively:")
        for s in sorted(set(ref_res_list)):
            print(f"      {s}")
        print("    A 'variant differs from reference' rule would invert every")
        print("    one of these calls. This script reads the sample's actual")
        print("    amino acid instead.")

    if disagree > len(entries) * 0.25:
        print()
        print(f"    FAIL: {disagree} entries match neither allele -- the codon")
        print("    mapping is probably wrong.")
        fails += 1

    print()
    print("FAILED" if fails else "PASSED")
    return 1 if fails else 0


# -------------------------------------------------------------------- plumbing

def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
    ap.add_argument("--reftag", default="primary",
                    help="primary (BX571856.1) or secondary (CP194230)")
    ap.add_argument("--work")
    ap.add_argument("--min-depth", type=int,
                    default=int(os.environ.get("MIN_SNP_DEPTH", 20)))
    ap.add_argument("--min-af", type=float, default=0.10,
                    help="minimum alt fraction to flag a subpopulation")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--out", help="write per-codon TSV here")
    args = ap.parse_args()

    root = pipe_root()
    work = Path(args.work or os.environ.get("ODI_WORK", root / "work"))

    ref_fa = root / "refs" / "BX571856.1.fasta"
    if args.reftag == "secondary":
        ref_fa = root / "refs" / "CP194230.fasta"
        print("WARNING: calling against CP194230. The Gordon catalogue is "
              "indexed to BX571856.1;\n         coordinates past the first "
              "indel may be wrong.", file=sys.stderr)
    if not ref_fa.exists():
        print(f"reference not found: {ref_fa}\nRun 00_fetch_refs.sh first.",
              file=sys.stderr)
        return 1

    loci_path = root / "catalogue" / "loci_BX571856.1.tsv"
    cat_path = root / "catalogue" / "gordon2014_resistance_snps.tsv"
    for p in (loci_path, cat_path):
        if not p.exists():
            print(f"missing {p}\nRun 06_build_catalogue.py first.",
                  file=sys.stderr)
            return 1

    ref = Reference(ref_fa)
    loci = load_loci(loci_path)
    entries = load_catalogue(cat_path, loci)
    entries, dropped = resolve_ambiguous(entries, loci, ref)

    if dropped:
        print(f"note: {len(dropped)} Table S4 entries could not be assigned "
              "to a single gene and were dropped:", file=sys.stderr)
        for d in dropped:
            print(f"  {d}", file=sys.stderr)
        print(file=sys.stderr)

    if args.self_test:
        return self_test(ref, loci, entries)

    if args.all:
        cohort = Path(args.cohort)
        if not cohort.is_absolute():
            cohort = root / cohort
        runs = cohort_runs(cohort)
    elif args.run:
        runs = list(args.run)
    else:
        ap.error("give a run accession, --all, or --self-test")

    all_rows: list[dict] = []
    missing = 0
    for run in runs:
        try:
            rows = call_sample(run, args.reftag, ref, loci, entries, work,
                               args.min_depth, args.min_af)
        except FileNotFoundError as e:
            print(f"=== {run} ===\n  no VCF ({e}); run 03_call_clair3.sh\n",
                  file=sys.stderr)
            missing += 1
            continue
        all_rows.extend(rows)
        print(f"=== {run} ({args.reftag}) ===")
        print(summarize(rows))
        print()

    if all_rows:
        out = Path(args.out) if args.out else (
            work / f"resistance_calls.{args.reftag}.tsv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(all_rows[0]),
                               delimiter="\t")
            w.writeheader()
            w.writerows(all_rows)
        print(f"per-codon detail -> {out}")

    return 1 if missing == len(runs) else 0


if __name__ == "__main__":
    sys.exit(main())
