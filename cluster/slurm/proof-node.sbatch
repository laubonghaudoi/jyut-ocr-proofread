#!/usr/bin/env bash
#SBATCH --job-name=jyut-proof-node
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=256G
#SBATCH --time=24:00:00

# CPU-only proofread + finish worker template (--ocr-gpus "" refuses to wipe
# pre-rendered/OCR'd work directories and resumes straight from disk).

set -euo pipefail

: "${JYUT_SKILL_DIR:?set JYUT_SKILL_DIR}"
: "${JYUT_WORK_DIR:?set JYUT_WORK_DIR}"
: "${JYUT_OUT_DIR:?set JYUT_OUT_DIR}"
: "${BOOKS_JSON:?set BOOKS_JSON}"
: "${PROOFREAD_ENDPOINT:?set PROOFREAD_ENDPOINT}"
: "${PROOFREAD_MODEL:?set PROOFREAD_MODEL}"

PYTHON_BIN="${JYUT_OCR_PYTHON:-python3}"

exec "${PYTHON_BIN}" "${JYUT_SKILL_DIR}/cluster/mass_ocr.py" \
    --books "${BOOKS_JSON}" \
    --out "${JYUT_OUT_DIR}" \
    --work "${JYUT_WORK_DIR}" \
    --skill-dir "${JYUT_SKILL_DIR}" \
    --ocr-gpus "" \
    --ocr-retries 0 \
    --retries "${PROOF_RETRIES:-5}" \
    --proofread-books "${PROOFREAD_BOOKS:-16}" \
    --max-inflight "${MAX_INFLIGHT:-128}" \
    --proof-workers "${PROOF_WORKERS:-32}" \
    --proof-endpoint "${PROOFREAD_ENDPOINT}" \
    --proof-model "${PROOFREAD_MODEL}" \
    --order "${ORDER:-smallest}" \
    --engine-a-after-b-fails \
    --deliver
