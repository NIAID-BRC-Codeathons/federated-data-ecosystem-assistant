#!/usr/bin/env python3
"""Build a machine-readable resistance catalogue from the Gordon 2014 supplement.

Source
------
Gordon NC, Price JR, Cole K, et al. "Prediction of Staphylococcus aureus
antimicrobial resistance by whole-genome sequencing." J Clin Microbiol
2014;52(4):1182-91. PMID 24501024. Supplementary PDF, Tables S1-S4.

Street et al. 2022 adapted these tables into their own Table S1 (resistance
SNPs) and Table S2 (mobile-element AMR genes). Street's supplementary workbook
as deposited with the paper contains only Table S3 (per-sample results), so the
SNP catalogue has to come from Gordon directly.

The catalogue is AMINO-ACID substitutions in four genes:

    fusA   fusidic acid          (Table S1, ~60 entries)
    dfrB   trimethoprim          (Table S2, 9 entries)
    rpoB   rifampicin            (Table S3, ~30 entries)
    grlA / gyrA / grlB  quinolones (Table S4, combinatorial)

CRITICAL: these are protein coordinates, not genome coordinates. Turning a
Clair3 VCF position into a catalogue lookup requires translating through the
CDS of each gene in BX571856.1. That is what 07_call_resistance.py must do;
this script only produces the target list.

Usage:
    06_build_catalogue.py [--pdf PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zlib
from pathlib import Path

# Genes whose variants the catalogue covers, mapped to the drug they predict.
GENE_DRUG = {
    "fusA": "fusidic acid",
    "dfrB": "trimethoprim",
    "rpoB": "rifampicin",
    "grlA": "ciprofloxacin",
    "gyrA": "ciprofloxacin",
    "grlB": "ciprofloxacin",
}

# Table -> (gene(s), drug). Table S4 is multi-gene and handled specially.
TABLE_SPEC = {
    "S1": (["fusA"], "fusidic acid"),
    "S2": (["dfrB"], "trimethoprim"),
    "S3": (["rpoB"], "rifampicin"),
    "S4": (["grlA", "gyrA", "grlB"], "ciprofloxacin"),
}

# The 20 standard amino acids in one-letter code. B, J, O, U, X, Z are NOT
# amino acids; requiring membership here rejects OCR-style noise such as the
# 'B434N' that a naive [A-Z] pattern happily accepts.
AA = "ACDEFGHIKLMNPQRSTVWY"
SUB_RE = re.compile(rf"\b([{AA}])(\d{{1,4}})([{AA}])\b")


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


# Sentinel standing in for a column break while we collapse letter spacing.
COL = "\x01"


def extract_pdf_text(pdf: Path) -> str:
    """Pull text out of the PDF, preserving column structure.

    The JCM supplement is typeset with each glyph emitted separately, so raw
    extraction yields 'fu sA v ari an ts'. Crucially the spacing is not
    uniform: a SINGLE space separates characters within a word, while TWO OR
    MORE separate table columns. Collapsing all whitespace indiscriminately
    destroys that distinction and welds each MIC onto its reference marker
    ('>128' + '1' -> '>1281'), which silently corrupts every MIC in the table.

    So: protect multi-space runs as COL sentinels first, then glue up the
    single-space letter spacing, and let callers split on COL to recover the
    original columns.
    """
    data = pdf.read_bytes()
    chunks: list[str] = []
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        try:
            raw = zlib.decompress(data[start:end])
        except zlib.error:
            continue
        for tok in re.findall(rb"\((?:[^()\\]|\\.)*\)", raw):
            chunks.append(tok[1:-1].decode("latin-1", "replace"))

    text = " ".join(chunks).replace("\\(", "(").replace("\\)", ")")
    text = re.sub(r" {2,}", COL, text)
    # Repeatedly glue a single trailing char onto the previous word.
    for _ in range(8):
        text = re.sub(r"(?<=\w) (?=\w\b)", "", text)
    return re.sub(r" {2,}", COL, text)


def split_tables(text: str) -> dict[str, list[str]]:
    """Slice into Table S1..S5, each as a list of column tokens."""
    marks = [(m.group(1), m.start())
             for m in re.finditer(r"Table\x01?(S\d+)", text)]
    out: dict[str, list[str]] = {}
    for i, (name, pos) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(text)
        cols = [c.strip() for c in text[pos:end].split(COL) if c.strip()]
        out[name] = cols
    return out


MIC_RE = re.compile(r"^>?\s?\d+(?:\s?[.\-]\s?\d+)*$")


def parse_table(name: str, cols: list[str]) -> list[dict]:
    """Extract substitution entries from one table's column tokens.

    Each data row reads as: <substitution group> <MIC> <reference list>.
    Multi-substitution genotypes appear as 'L461K+H457Q' in one token; we
    record each constituent substitution, since the catalogue is applied
    per-variant when scanning a VCF.
    """
    genes, drug = TABLE_SPEC[name]
    rows: list[dict] = []

    for idx, tok in enumerate(cols):
        subs = SUB_RE.findall(tok)
        if not subs:
            continue
        # Skip header/prose tokens that happen to contain a code-like string.
        if len(tok) > 60:
            continue

        # The MIC is the next token that looks like a number or range.
        mic = ""
        for nxt in cols[idx + 1:idx + 3]:
            if MIC_RE.match(nxt):
                mic = re.sub(r"\s+", "", nxt)
                break

        for ref_aa, pos, alt_aa in subs:
            # Table S4 interleaves three genes across columns. The flattened
            # token stream does not preserve which column a substitution came
            # from, so attribution to grlA vs gyrA vs grlB is not recoverable
            # here. Flag it rather than guess -- a mis-assigned gene is worse
            # than one marked unresolved.
            gene = genes[0] if len(genes) == 1 else "grlA|gyrA|grlB"

            rows.append({
                "table": name,
                "gene": gene,
                "ref_aa": ref_aa,
                "codon": int(pos),
                "alt_aa": alt_aa,
                "substitution": f"{ref_aa}{pos}{alt_aa}",
                "drug": drug,
                "reported_mic": mic,
                "gene_ambiguous": len(genes) > 1,
            })

    # Deduplicate: substitutions recur across multi-substitution rows.
    # Keep the first (highest-MIC) occurrence, since tables are MIC-sorted.
    seen: set[tuple] = set()
    uniq = []
    for r in rows:
        key = (r["gene"], r["substitution"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", default=str(
        pipe_root() / "catalogue" / "src" / "gordon"
        / "JCM.03117-13_zjm999093277so1.pdf"))
    ap.add_argument("--out", default=str(pipe_root() / "catalogue"))
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"Gordon supplement not found: {pdf}", file=sys.stderr)
        print("Fetch it from Europe PMC PMC3993491 supplementaryFiles.",
              file=sys.stderr)
        return 1

    text = extract_pdf_text(pdf)
    tables = split_tables(text)

    missing = [t for t in TABLE_SPEC if t not in tables]
    if missing:
        print(f"WARNING: tables not found in PDF: {missing}", file=sys.stderr)

    entries: list[dict] = []
    for name in TABLE_SPEC:
        if name in tables:
            got = parse_table(name, tables[name])
            print(f"  {name}: {len(got):3d} unique substitutions "
                  f"({TABLE_SPEC[name][1]})")
            entries.extend(got)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    tsv = outdir / "gordon2014_resistance_snps.tsv"
    with open(tsv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "table", "gene", "substitution", "ref_aa", "codon", "alt_aa",
            "drug", "reported_mic", "gene_ambiguous"], delimiter="\t")
        w.writeheader()
        w.writerows(entries)

    meta = {
        "source": {
            "citation": ("Gordon NC, et al. J Clin Microbiol "
                         "2014;52(4):1182-91"),
            "pmid": "24501024",
            "pmcid": "PMC3993491",
            "doi": "10.1128/jcm.03117-13",
            "tables": "S1 (fusA), S2 (dfrB), S3 (rpoB), S4 (grlA/gyrA/grlB)",
        },
        "coordinate_system": "amino acid substitution in protein sequence",
        "reference_for_translation": "BX571856.1 (MRSA252)",
        "n_entries": len(entries),
        "by_drug": {},
        "caveats": [
            "Amino-acid coordinates, NOT genome coordinates. A VCF position "
            "must be translated through the relevant CDS before lookup.",
            "Table S4 entries cannot be attributed to grlA vs gyrA vs grlB "
            "from flattened PDF text; they are marked gene_ambiguous and must "
            "be resolved against the source PDF before use in a call.",
            "Covers 4 chromosomal loci only. Mobile-element genes (mecA, "
            "ermA/C, tetK/M, aacA-aphD) are NOT here -- Street's Table S2 "
            "covers those and is not in the deposited workbook.",
            "MIC values are as reported in the primary literature Gordon "
            "et al. cite, not measured by them.",
        ],
    }
    for e in entries:
        meta["by_drug"][e["drug"]] = meta["by_drug"].get(e["drug"], 0) + 1

    (outdir / "catalogue_meta.json").write_text(json.dumps(meta, indent=2))

    print(f"\nwrote {len(entries)} entries -> {tsv}")
    print(f"      metadata          -> {outdir / 'catalogue_meta.json'}")
    n_amb = sum(1 for e in entries if e["gene_ambiguous"])
    if n_amb:
        print(f"\n{n_amb} entries have ambiguous gene assignment (Table S4).")
        print("Resolve against the source PDF before using them to call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
