#!/usr/bin/env bash
# Clair3 variant calling on nanopore BAMs, against both references.
#
# Street et al. used Clair (commit 54c7dd4) plus a random-forest variant
# filter trained on their earlier N. gonorrhoeae work. Clair3 is Clair's
# maintained successor and supersedes that architecture; we do NOT have their
# random forest, so our filtering is Clair3's own quality model plus the
# paper's depth thresholds. That is a real methodological difference and is
# recorded as such in the README -- it is not a faithful reimplementation of
# their filter.
#
# Usage:  03_call_clair3.sh [run_accession ...]
#         ODI_COHORT=amr_cohort.tsv 03_call_clair3.sh

. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.sh"

COHORT="${ODI_COHORT:-$PIPE_ROOT/samples.tsv}"
[[ -f "$COHORT" ]] || COHORT="$PIPE_ROOT/$(basename "$COHORT")"

runs=("$@")
if [[ ${#runs[@]} -eq 0 ]]; then
    # `mapfile` is bash 4+; macOS ships bash 3.2, so read the list portably.
    while IFS= read -r line; do runs+=("$line"); done < <(
        grep -v '^#' "$COHORT" | awk 'NR>1 && NF {print $1}')
fi

# Clair3's entrypoint and model directory live in different places depending
# on how it was installed, so resolve both up front rather than hard-coding
# the container layout.
#
#   container (hkubal/clair3): /opt/bin/run_clair3.sh, /opt/models/<model>
#   bioconda:                  $CONDA_PREFIX/bin/run_clair3.sh,
#                              $CONDA_PREFIX/bin/models/<model>
#
# The bioconda build is native arm64, which matters on Apple Silicon: the
# published image is linux/amd64 and would run under emulation.
case "$ODI_RUNTIME" in
    native)
        CLAIR3_BIN="$(command -v run_clair3.sh || true)"
        if [[ -z "$CLAIR3_BIN" ]]; then
            echo "[clair3] run_clair3.sh not on PATH." >&2
            echo "[clair3] conda activate clair3   (see README)" >&2
            exit 1
        fi
        MODEL_ROOT="${CLAIR3_MODEL_ROOT:-$(dirname "$CLAIR3_BIN")/models}"
        ;;
    *)
        CLAIR3_BIN="/opt/bin/run_clair3.sh"
        MODEL_ROOT="${CLAIR3_MODEL_ROOT:-/opt/models}"
        ;;
esac

MODEL_PATH="$MODEL_ROOT/$CLAIR3_MODEL"
if [[ "$ODI_RUNTIME" == native && ! -d "$MODEL_PATH" ]]; then
    echo "[clair3] model not found: $MODEL_PATH" >&2
    echo "[clair3] available:" >&2
    ls "$MODEL_ROOT" 2>/dev/null | sed 's/^/[clair3]   /' >&2
    exit 1
fi

# Wrap the container runtime so the same script works under docker and
# singularity. Clair3 needs the refs, BAMs and output dir visible.
clair3_run() {
    case "$ODI_RUNTIME" in
        docker)
            docker run --rm \
                -v "$REF_DIR:$REF_DIR" \
                -v "$BAM_DIR:$BAM_DIR" \
                -v "$VCF_DIR:$VCF_DIR" \
                "$CLAIR3_IMAGE" "$@"
            ;;
        singularity)
            singularity exec \
                -B "$REF_DIR","$BAM_DIR","$VCF_DIR" \
                "$CLAIR3_IMAGE" "$@"
            ;;
        native)
            "$@"
            ;;
        *)
            echo "[clair3] unknown ODI_RUNTIME=$ODI_RUNTIME" >&2; exit 1 ;;
    esac
}

call_one() {
    local run="$1" reftag="$2" reffa="$3"
    local bam="$BAM_DIR/${run}.${reftag}.bam"
    local outdir="$VCF_DIR/${run}.${reftag}"

    if [[ ! -s "$bam" ]]; then
        echo "[clair3] $run/$reftag: no BAM, run 02_map.sh first" >&2
        return 1
    fi
    if [[ -s "$outdir/merge_output.vcf.gz" ]]; then
        echo "[clair3] $run vs $reftag: already called, skipping"
        return 0
    fi

    mkdir -p "$outdir"
    echo "[clair3] $run vs $reftag (model $CLAIR3_MODEL)"

    clair3_run "$CLAIR3_BIN" \
        --bam_fn="$bam" \
        --ref_fn="$reffa" \
        --threads="$THREADS" \
        --platform="$CLAIR3_PLATFORM" \
        --model_path="$MODEL_PATH" \
        --output="$outdir" \
        $CLAIR3_EXTRA_ARGS \
        2>&1 | tee "$LOG_DIR/${run}.${reftag}.clair3.log"
}

for run in "${runs[@]}"; do
    call_one "$run" primary   "$REF_PRIMARY_FA"
    call_one "$run" secondary "$REF_SECONDARY_FA"
done

echo "[clair3] done. VCFs under $VCF_DIR/<run>.<reftag>/merge_output.vcf.gz"
