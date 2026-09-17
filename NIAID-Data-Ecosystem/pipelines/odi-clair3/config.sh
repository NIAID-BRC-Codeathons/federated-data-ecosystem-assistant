#!/usr/bin/env bash
# Shared configuration for the ODI Clair3 arm.
# Source this from every script: . "$(dirname "$0")/../config.sh"

set -euo pipefail

# --- Layout -----------------------------------------------------------------
PIPE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# WORK holds everything large and disposable: FASTQs, BAMs, VCFs.
# Nothing under WORK is committed. Override to point at scratch/lustre.
WORK="${ODI_WORK:-$PIPE_ROOT/work}"

FASTQ_DIR="$WORK/fastq"
BAM_DIR="$WORK/bam"
VCF_DIR="$WORK/vcf"
LOG_DIR="$WORK/logs"

REF_DIR="$PIPE_ROOT/refs"
CATALOGUE_DIR="$PIPE_ROOT/catalogue"

# --- References -------------------------------------------------------------
# Two MRSA252 assemblies, deliberately. They are NOT the same sequence:
#
#   BX571856.1  2,902,619 bp  Sanger assembly, the one Street et al. used and
#                             the one Gordon et al. 2014 resistance-SNP
#                             coordinates are indexed against.
#   CP194230    2,902,592 bp  a later resequencing (BV-BRC 1280.63071), 27 bp
#                             shorter, 2 CDS different.
#
# Everything downstream runs against both so coordinate drift is measured
# rather than assumed. BX571856.1 is authoritative for resistance calls.
REF_PRIMARY_ID="BX571856.1"
REF_PRIMARY_FA="$REF_DIR/BX571856.1.fasta"
REF_PRIMARY_LEN=2902619

REF_SECONDARY_ID="CP194230"
REF_SECONDARY_FA="$REF_DIR/CP194230.fasta"
REF_SECONDARY_LEN=2902592

# BV-BRC genome IDs corresponding to each, for pulling annotations.
BVBRC_GENOME_PRIMARY="282458.100"   # matches BX571856.1 length exactly
BVBRC_GENOME_SECONDARY="1280.63071" # CP194230, has CARD/NDARO annotations

# --- Clair3 -----------------------------------------------------------------
# The paper used Clair (commit 54c7dd4). Clair3 is its maintained successor.
#
# Model choice matters more than any other parameter here. These are R9.4.1
# GridION reads from 2018-2020, so an r941 model is required -- an r10 model
# on r9 data produces systematically wrong quality calibration.
CLAIR3_MODEL="${CLAIR3_MODEL:-r941_prom_sup_g5014}"
CLAIR3_PLATFORM="ont"

# Haploid bacterium: no diploid priors, and we want everything genotyped.
CLAIR3_EXTRA_ARGS="${CLAIR3_EXTRA_ARGS:---include_all_ctgs --no_phasing_for_fa --haploid_precise}"

# --- Containers -------------------------------------------------------------
ODI_RUNTIME="${ODI_RUNTIME:-docker}"
CLAIR3_IMAGE="${CLAIR3_IMAGE:-hkubal/clair3:latest}"

# --- Coverage thresholds (from the paper, Methods) --------------------------
# Street et al. required >=20-fold depth at each resistance-conferring SNP,
# and >=20-fold plus 100% breadth for mobile-element AMR genes. Positions
# below this are reported as "insufficient coverage", NOT as susceptible --
# 60 of their 152 drug-organism combinations fell in that bucket, and
# collapsing it into "susceptible" is how you manufacture a perfect result.
MIN_SNP_DEPTH="${MIN_SNP_DEPTH:-20}"
MIN_GENE_DEPTH="${MIN_GENE_DEPTH:-20}"
MIN_GENE_BREADTH="${MIN_GENE_BREADTH:-1.0}"

# --- Threads ----------------------------------------------------------------
THREADS="${ODI_THREADS:-8}"

mkdir -p "$FASTQ_DIR" "$BAM_DIR" "$VCF_DIR" "$LOG_DIR" "$REF_DIR"
