#!/usr/bin/env bash
# minimap2 -> sorted BAM, against BOTH MRSA252 assemblies.
#
# The paper used minimap2 v2.17-r941 with MRSA252 as reference. Preset map-ont
# is the correct one for R9 nanopore; do not substitute sr or asm5.
#
# Usage:  02_map.sh [run_accession ...]
#         ODI_COHORT=amr_cohort.tsv 02_map.sh

. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

COHORT="${ODI_COHORT:-$PIPE_ROOT/samples.tsv}"
[[ -f "$COHORT" ]] || COHORT="$PIPE_ROOT/$(basename "$COHORT")"

runs=("$@")
if [[ ${#runs[@]} -eq 0 ]]; then
    # `mapfile` is bash 4+; macOS ships bash 3.2, so read the list portably.
    while IFS= read -r line; do runs+=("$line"); done < <(
        grep -v '^#' "$COHORT" | awk 'NR>1 && NF {print $1}')
fi

map_one() {
    local run="$1" reftag="$2" reffa="$3"
    local fq="$FASTQ_DIR/${run}.fastq.gz"
    local bam="$BAM_DIR/${run}.${reftag}.bam"

    if [[ ! -s "$fq" ]]; then
        echo "[map] $run: no FASTQ, run 01_fetch_reads.sh first" >&2
        return 1
    fi
    if [[ -s "$bam" && -s "${bam}.bai" ]]; then
        echo "[map] $run vs $reftag: already mapped, skipping"
        return 0
    fi

    echo "[map] $run vs $reftag"
    minimap2 -ax map-ont -t "$THREADS" \
             -R "@RG\tID:${run}\tSM:${run}\tPL:ONT" \
             "$reffa" "$fq" 2> "$LOG_DIR/${run}.${reftag}.minimap2.log" \
      | samtools sort -@ "$THREADS" -o "$bam" -
    samtools index -@ "$THREADS" "$bam"

    # Depth/breadth summary now, while the BAM is hot in cache. These two
    # numbers decide whether AMR calling is even attemptable for this sample.
    samtools coverage "$bam" > "$BAM_DIR/${run}.${reftag}.coverage.txt"

    # Per-base depth over the resistance loci only. 08_call_resistance.py
    # needs depth at EVERY catalogue codon, including ones where the sample
    # matches the reference and Clair3 therefore emits no record -- without
    # it, a reference-allele codon is indistinguishable from an uncovered one
    # and gets reported as no_data instead of confirming the call. -a keeps
    # zero-depth positions, which is the whole point.
    #
    # Restricted to the six loci (~13 kb) rather than the full 2.9 Mb: the
    # caller only ever looks at those, and a genome-wide file is ~100x larger
    # for no gain.
    # One BED per reference -- the contig name differs between them, and the
    # coordinates are only strictly correct for the primary (the loci table
    # is indexed to BX571856.1). For the secondary this is a rough window,
    # which is fine: it is a drift check, not a calling reference.
    local bed="$BAM_DIR/.resistance_loci.${reftag}.bed"
    if [[ ! -s "$bed" ]]; then
        local ctg
        ctg=$(head -1 "$reffa" | sed 's/^>//; s/ .*//')
        awk -v ctg="$ctg" 'BEGIN{FS="\t"; OFS="\t"}
             !/^#/ && $1 != "gene" && NF >= 4 { print ctg, $3-1, $4, $1 }' \
            "$CATALOGUE_DIR/loci_BX571856.1.tsv" > "$bed"
    fi
    samtools depth -a -b "$bed" "$bam" \
        > "$BAM_DIR/${run}.${reftag}.depth.txt"
}

for run in "${runs[@]}"; do
    map_one "$run" primary   "$REF_PRIMARY_FA"
    map_one "$run" secondary "$REF_SECONDARY_FA"
done

echo "[map] done. Per-run coverage: $BAM_DIR/*.coverage.txt"
