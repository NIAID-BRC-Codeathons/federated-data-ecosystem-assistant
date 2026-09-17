#!/usr/bin/env bash
# Pull Bracken outputs from a BV-BRC TaxonomicClassification job folder.
#
# Each job writes TWO entries into the output path: a `job_result` stub named
# after output_file, and a dot-prefixed folder holding the actual data:
#
#   <output_path>/.<output_file>/<sample_id>/bracken_output/<sample_id>_bracken_output.txt
#
# `p3-ls` lists both (it does not hide dot entries), so a completed job shows
# up twice. A job still running shows only the dot folder, or nothing.
#
# We want the small `_bracken_output.txt` (a few hundred bytes), NOT:
#   - `_k2_report.txt`  -- Kraken2's report, which strands ~69% of long reads
#                          at internal nodes; see 09_compare_species.py
#   - `_k2_output.txt`  -- per-read assignments, ~2 GB per sample
#
# Requires the BV-BRC CLI (`p3-ls`, `p3-cp`) and an active `p3-login`.
# If you do not have the CLI, the same files are reachable through the
# workspace browser:
#   https://www.bv-brc.org/workspace/<user>/home/odi-metagenomics
#
# Usage:
#   10_fetch_bracken.sh                       # all tax_* jobs
#   10_fetch_bracken.sh tax_ERR5260555_p41_sap

. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

WS="${ODI_WORKSPACE:-/${USER}@patricbrc.org/home/odi-metagenomics}"
DEST="$PIPE_ROOT/results/bracken"
mkdir -p "$DEST"

if ! command -v p3-ls >/dev/null 2>&1; then
    echo "[fetch] BV-BRC CLI not found (p3-ls)." >&2
    echo "[fetch] Install it, or download by hand from:" >&2
    echo "[fetch]   https://www.bv-brc.org/workspace${WS}" >&2
    echo "[fetch] Each job: .<output_file>/<sample_id>/bracken_output/" >&2
    exit 1
fi

jobs=("$@")
if [[ ${#jobs[@]} -eq 0 ]]; then
    # Hidden folders are the job results; strip the leading dot for matching.
    # `mapfile` is bash 4+; macOS ships bash 3.2, so read the list portably.
    while IFS= read -r line; do jobs+=("$line"); done < <(
        p3-ls "$WS" 2>/dev/null \
        | sed 's#^.*/##' | grep -E '^\.?tax_' | sed 's/^\.//' | sort -u)
fi

if [[ ${#jobs[@]} -eq 0 ]]; then
    echo "[fetch] no tax_* job folders under $WS" >&2
    echo "[fetch] (expected dot-prefixed folders like .tax_ERRxxxxxxx_pNN_sap)" >&2
    exit 1
fi

echo "[fetch] ${#jobs[@]} job(s) from $WS"

got=0 missed=0
for job in "${jobs[@]}"; do
    # sample_id is the job name minus the tax_<accession>_ prefix.
    sample="${job#tax_}"; sample="${sample#*_}"
    src="$WS/.${job}/${sample}/bracken_output/${sample}_bracken_output.txt"
    out="$DEST/${sample}_bracken_output.txt"

    if [[ -s "$out" ]]; then
        echo "[fetch] $sample: already have it"
        continue
    fi

    if p3-cp "ws:$src" "$out" 2>/dev/null && [[ -s "$out" ]]; then
        n=$(( $(wc -l < "$out") - 1 ))
        echo "[fetch] $sample: $n species"
        got=$((got + 1))
    else
        # Most likely the job is still running or failed; both are worth
        # distinguishing from "the path is wrong".
        echo "[fetch] $sample: not available (still running, or failed)" >&2
        rm -f "$out"
        missed=$((missed + 1))
    fi
done

echo "[fetch] $got fetched, $missed unavailable -> $DEST"
[[ $got -gt 0 ]] && echo "[fetch] now run: scripts/09_compare_species.py"
