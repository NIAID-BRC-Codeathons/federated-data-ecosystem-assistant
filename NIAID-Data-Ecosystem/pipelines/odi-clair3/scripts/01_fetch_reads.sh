#!/usr/bin/env bash
# Fetch nanopore FASTQs for a cohort directly from ENA.
#
# Usage:  01_fetch_reads.sh [run_accession ...]
#         ODI_COHORT=amr_cohort.tsv 01_fetch_reads.sh
#
# With no arguments, reads every run from $ODI_COHORT (default samples.tsv).
# Resumable: curl -C - picks up a partial file, and anything already complete
# is skipped by the size check.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

COHORT="${ODI_COHORT:-$PIPE_ROOT/samples.tsv}"
[[ -f "$COHORT" ]] || COHORT="$PIPE_ROOT/$(basename "$COHORT")"

runs=("$@")
if [[ ${#runs[@]} -eq 0 ]]; then
    # Skip comments and the header row; take column 1.
    # `mapfile` is bash 4+; macOS ships bash 3.2, so read the list portably.
    while IFS= read -r line; do runs+=("$line"); done < <(
        grep -v '^#' "$COHORT" | awk 'NR>1 && NF {print $1}')
fi

MAX_ATTEMPTS="${ODI_MAX_ATTEMPTS:-6}"
failed=()

echo "[reads] ${#runs[@]} run(s) from $(basename "$COHORT") -> $FASTQ_DIR"

for run in "${runs[@]}"; do
    out="$FASTQ_DIR/${run}.fastq.gz"

    # Ask ENA where the file lives and how big it should be, rather than
    # guessing the FTP path from the accession.
    #
    # Select columns BY NAME from the header: ENA prepends run_accession to
    # every filereport whether or not you asked for it, so positional $1/$2
    # silently yields the accession and the URL instead of the URL and the
    # byte count. Also split on tabs only -- a run with paired files returns
    # a semicolon-separated list in one field.
    read -r url want_bytes < <(
        curl -fsSL "https://www.ebi.ac.uk/ena/portal/api/filereport?accession=${run}&result=read_run&fields=fastq_ftp,fastq_bytes&format=tsv" \
        | awk -F'\t' '
            NR==1 { for (i = 1; i <= NF; i++) col[$i] = i; next }
            NR==2 { print $col["fastq_ftp"], $col["fastq_bytes"] }'
    )
    # Single-end nanopore here; if ENA ever returns a pair, take the first
    # and say so rather than concatenating two URLs into one argument.
    if [[ "$url" == *";"* ]]; then
        echo "[reads] $run: multiple FASTQ files listed, using the first" >&2
        url="${url%%;*}"; want_bytes="${want_bytes%%;*}"
    fi

    if [[ -z "${url:-}" ]]; then
        echo "[reads] $run: no FASTQ path returned by ENA, skipping" >&2
        continue
    fi

    if [[ -s "$out" ]]; then
        have=$(wc -c < "$out" | tr -d ' ')
        if [[ "$have" == "$want_bytes" ]]; then
            echo "[reads] $run: complete (${have} bytes), skipping"
            continue
        fi
        echo "[reads] $run: partial (${have}/${want_bytes}), resuming"
    fi

    echo "[reads] $run: fetching ${want_bytes} bytes"

    # ENA drops long transfers -- a 7.8 GB pull died at 88% with
    # "curl: (18) transfer closed". Retry with -C - so each attempt resumes
    # from the bytes already on disk rather than restarting, and never let a
    # failure abort the loop: one bad run must not strand the other nineteen.
    attempt=0
    while :; do
        attempt=$((attempt + 1))
        if curl -fL --progress-bar -C - --retry 5 --retry-delay 10 \
                --retry-all-errors --speed-limit 10240 --speed-time 120 \
                "https://${url}" -o "$out"; then
            :
        else
            echo "[reads] $run: curl exited $? on attempt $attempt" >&2
        fi

        have=$(wc -c < "$out" 2>/dev/null | tr -d ' ' || echo 0)
        [[ "$have" == "$want_bytes" ]] && break

        if [[ $attempt -ge $MAX_ATTEMPTS ]]; then
            echo "[reads] $run: GIVING UP after $attempt attempts " \
                 "(${have}/${want_bytes}); partial file kept for resume" >&2
            failed+=("$run")
            break
        fi
        echo "[reads] $run: incomplete (${have}/${want_bytes}), " \
             "resuming (attempt $((attempt + 1)))" >&2
        sleep 5
    done

    [[ "$(wc -c < "$out" 2>/dev/null | tr -d ' ')" == "$want_bytes" ]] \
        && echo "[reads] $run: complete"
done

if [[ ${#failed[@]} -gt 0 ]]; then
    echo "[reads] ${#failed[@]} run(s) incomplete: ${failed[*]}" >&2
    echo "[reads] re-run the script to resume them" >&2
    exit 1
fi

echo "[reads] done -- all ${#runs[@]} run(s) complete"
