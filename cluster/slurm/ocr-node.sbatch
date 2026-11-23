#!/usr/bin/env bash
#SBATCH --job-name=jyut-ocr-node
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=256G
#SBATCH --time=24:00:00

# Mixed OCR + Proofread worker node template (8 GPUs: local VL + HunyuanOCR;
# proofreading calls routed to PROOFREAD_ENDPOINT / fleet_router).
#
# Configure via environment variables before sbatch --export=ALL,...:
#   JYUT_SKILL_DIR, JYUT_WORK_DIR, JYUT_OUT_DIR, BOOKS_JSON,
#   PROOFREAD_ENDPOINT, PROOFREAD_MODEL, PADDLE_VL_MODEL_DIR, VL_REVISION

set -euo pipefail

: "${JYUT_SKILL_DIR:?set JYUT_SKILL_DIR}"
: "${JYUT_WORK_DIR:?set JYUT_WORK_DIR}"
: "${JYUT_OUT_DIR:?set JYUT_OUT_DIR}"
: "${BOOKS_JSON:?set BOOKS_JSON}"
: "${PROOFREAD_ENDPOINT:?set PROOFREAD_ENDPOINT}"
: "${PROOFREAD_MODEL:?set PROOFREAD_MODEL}"
: "${PADDLE_VL_MODEL_DIR:?set PADDLE_VL_MODEL_DIR}"

PYTHON_BIN="${JYUT_OCR_PYTHON:-python3}"
OCR_GPUS="${OCR_GPUS:-0,1,2,3,4,5,6,7}"
VL_BASE_PORT="${VL_BASE_PORT:-31500}"
VL_REVISION="${VL_REVISION:-sglang-paddleocr-vl-1.6-chat-template}"

IFS=',' read -r -a GPU_ARR <<< "${OCR_GPUS}"
vl_urls=()
vl_pids=()

cleanup() {
    for p in "${vl_pids[@]:-}"; do
        kill "${p}" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

for g in "${GPU_ARR[@]}"; do
    port=$((VL_BASE_PORT + g))
    "${JYUT_SKILL_DIR}/cluster/slurm/vl-server.sh" "${g}" "${port}" > "${JYUT_OUT_DIR}/vl-${g}.log" 2>&1 &
    vl_pids+=($!)
    vl_urls+=("http://127.0.0.1:${port}/v1")
done

VL_CSV="$(IFS=,; echo "${vl_urls[*]}")"
exec "${PYTHON_BIN}" "${JYUT_SKILL_DIR}/cluster/mass_ocr.py" \
    --books "${BOOKS_JSON}" \
    --out "${JYUT_OUT_DIR}" \
    --work "${JYUT_WORK_DIR}" \
    --skill-dir "${JYUT_SKILL_DIR}" \
    --ocr-gpus "${OCR_GPUS}" \
    --vl-urls "${VL_CSV}" \
    --vl-revision "${VL_REVISION}" \
    --proof-endpoint "${PROOFREAD_ENDPOINT}" \
    --proof-model "${PROOFREAD_MODEL}" \
    --order "${ORDER:-alternate}" \
    --dpi "${RENDER_DPI:-auto}" \
    --max-missing-engine-b "${MAX_MISSING_ENGINE_B:-1}" \
    --deliver
