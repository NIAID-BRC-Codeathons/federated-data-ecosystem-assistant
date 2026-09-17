#!/usr/bin/env python3
"""Extract Street et al. Table S3 -- the per-sample ground truth.

Table S3 is the only supplementary table deposited with the paper as data
(the rest are figures). It carries, for each of the 115 specimens:

  - culture result from sonication fluid and from periprosthetic tissue
  - CFU/ml, number of positive tissue samples
  - histology evidence of acute infection
  - the authors' own sequencing species call after filtering
  - reference genome coverage at 1x, mean depth, bases/reads assigned

This is what our Kraken2 arm has to be judged against. Without it we would be
comparing our classifications to nothing.

One structural wrinkle: samples with multiple species detected span several
spreadsheet rows, with the sample-identifying columns blank on continuation
rows. Those are forward-filled here so each output row is self-contained.

Reading .xlsx without openpyxl: an xlsx is a zip of XML, and we only need
cell values, so the stdlib is enough.

Usage:
    07_extract_truth.py [--xlsx PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import csv
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Column letter -> output field name, from the header in row 5.
COLUMNS = {
    "A": "sample_number",
    "B": "device_type",
    "C": "sonication_species",
    "D": "sonication_cfu_per_ml",
    "E": "tissue_culture_species",
    "F": "positive_tissue_samples",
    "G": "histology_acute_infection",
    "H": "sequencing_batch",
    "I": "barcode",
    "J": "total_nonhuman_bases",
    "K": "total_nonhuman_reads",
    "L": "sequencing_species",
    "M": "ref_coverage_1x_pct",
    "N": "ref_coverage_mean_depth",
    "O": "bases_assigned",
    "P": "reads_assigned",
    "Q": "pct_bacterial_bases_assigned",
    "R": "pct_species_bases_mapping_ref",
}

# Columns that identify the specimen; blank on continuation rows.
CARRY = ["sample_number", "device_type", "sonication_species",
         "sonication_cfu_per_ml", "tissue_culture_species",
         "positive_tissue_samples", "histology_acute_infection",
         "sequencing_batch", "barcode", "total_nonhuman_bases",
         "total_nonhuman_reads"]

DATA_START_ROW = 6   # rows 1-5 are title and two header bands


def pipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_sheet(xlsx: Path, sheet: str = "xl/worksheets/sheet1.xml"):
    z = zipfile.ZipFile(xlsx)
    try:
        shared = ["".join(t.text or "" for t in si.iter(f"{NS}t"))
                  for si in ET.fromstring(
                      z.read("xl/sharedStrings.xml")).findall(f"{NS}si")]
    except KeyError:
        shared = []

    for row in ET.fromstring(z.read(sheet)).iter(f"{NS}row"):
        cells: dict[str, str] = {}
        for c in row.iter(f"{NS}c"):
            col = "".join(ch for ch in c.get("r", "") if ch.isalpha())
            v = c.find(f"{NS}v")
            inline = c.find(f"{NS}is")
            if c.get("t") == "s" and v is not None:
                val = shared[int(v.text)]
            elif inline is not None:
                val = "".join(t.text or "" for t in inline.iter(f"{NS}t"))
            elif v is not None:
                val = v.text or ""
            else:
                val = ""
            cells[col] = val.strip()
        yield int(row.get("r")), cells


def round_num(s: str, places: int = 4) -> str:
    """Trim spreadsheet float noise (94.356057236200002 -> 94.3561)."""
    try:
        return f"{float(s):.{places}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return s


NUMERIC = {"ref_coverage_1x_pct", "ref_coverage_mean_depth",
           "pct_bacterial_bases_assigned", "pct_species_bases_mapping_ref"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xlsx", default=str(
        pipe_root() / "catalogue" / "src" / "street"
        / "jcm.02156-21-s0001.xlsx"))
    ap.add_argument("--out", default=str(
        pipe_root() / "catalogue" / "street2022_table_s3.tsv"))
    args = ap.parse_args()

    xlsx = Path(args.xlsx)
    if not xlsx.exists():
        print(f"Street supplement not found: {xlsx}", file=sys.stderr)
        print("Fetch from Europe PMC PMC9020354 supplementaryFiles.",
              file=sys.stderr)
        return 1

    out_rows: list[dict] = []
    carried: dict[str, str] = {}

    for rnum, cells in read_sheet(xlsx):
        if rnum < DATA_START_ROW:
            continue
        rec = {field: cells.get(col, "") for col, field in COLUMNS.items()}
        if not any(rec.values()):
            continue

        # Forward-fill specimen identity onto continuation rows.
        if rec["sample_number"]:
            carried = {k: rec[k] for k in CARRY}
            rec["is_continuation"] = "0"
        else:
            for k in CARRY:
                rec[k] = carried.get(k, "")
            rec["is_continuation"] = "1"

        for k in NUMERIC:
            rec[k] = round_num(rec[k])

        out_rows.append(rec)

    fields = list(COLUMNS.values()) + ["is_continuation"]
    out = Path(args.out)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter="\t")
        w.writeheader()
        w.writerows(out_rows)

    samples = {r["sample_number"] for r in out_rows if r["sample_number"]}
    sa_culture = {r["sample_number"] for r in out_rows
                  if "Staphylococcus aureus" in r["sonication_species"]}
    sa_seq = {r["sample_number"] for r in out_rows
              if "Staphylococcus aureus" in r["sequencing_species"]}
    culture_neg = {r["sample_number"] for r in out_rows
                   if r["sonication_species"].lower() == "negative"}

    print(f"wrote {len(out_rows)} rows covering {len(samples)} specimens")
    print(f"  -> {out}")
    print()
    print(f"  culture-negative on sonication fluid : {len(culture_neg)}")
    print(f"  S. aureus by culture                 : {len(sa_culture)}")
    print(f"  S. aureus by their sequencing        : {len(sa_seq)}")
    print(f"  agreeing on S. aureus                : "
          f"{len(sa_culture & sa_seq)}")
    print()
    print("  S. aureus specimens (culture): "
          + ", ".join(sorted(sa_culture, key=lambda x: int(x))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
