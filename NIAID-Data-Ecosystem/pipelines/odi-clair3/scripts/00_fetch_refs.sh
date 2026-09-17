#!/usr/bin/env bash
# Fetch both MRSA252 assemblies and verify they are what we think they are.
#
# This script is deliberately paranoid about lengths. The whole reason we carry
# two references is that they differ by 27 bp, and a silent swap would shift
# resistance-SNP coordinates without any error being raised anywhere.

. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

fetch() {
    local acc="$1" out="$2" want_len="$3"

    if [[ -s "$out" ]] && head -1 "$out" | grep -q "^>${acc}\$"; then
        echo "[refs] $acc already present, verifying"
    else
        echo "[refs] fetching $acc from ENA"
        # ENA serves headers like
        #   >ENA|BX571856|BX571856.1 Staphylococcus aureus ... complete genome
        # Rewrite to a bare accession. The pipe characters are the problem:
        # Clair3 interpolates the contig name into shell commands unquoted, so
        # `ENA|BX571856|...` gets parsed as a pipeline and the run dies with
        # "BX571856.1_1.vcf: command not found". The trailing description also
        # makes every downstream CHROM field unwieldy.
        curl -fsSL "https://www.ebi.ac.uk/ena/browser/api/fasta/${acc}?download=true" \
            | awk -v acc="$acc" 'NR==1 {print ">" acc; next} {print}' > "$out"
    fi

    # Length check against the expected value, not just "is it non-empty".
    local got_len
    got_len=$(grep -v '^>' "$out" | tr -d '\n' | wc -c | tr -d ' ')
    if [[ "$got_len" != "$want_len" ]]; then
        echo "[refs] FATAL: $acc is ${got_len} bp, expected ${want_len} bp." >&2
        echo "[refs] Refusing to continue -- resistance-SNP coordinates depend on this." >&2
        exit 1
    fi
    echo "[refs] $acc OK (${got_len} bp)"

    samtools faidx "$out"
}

fetch "$REF_PRIMARY_ID"   "$REF_PRIMARY_FA"   "$REF_PRIMARY_LEN"
fetch "$REF_SECONDARY_ID" "$REF_SECONDARY_FA" "$REF_SECONDARY_LEN"

echo
echo "[refs] Primary   (resistance calls): $REF_PRIMARY_FA"
echo "[refs] Secondary (drift check)     : $REF_SECONDARY_FA"
