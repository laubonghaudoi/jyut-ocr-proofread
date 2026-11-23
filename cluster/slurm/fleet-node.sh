#!/usr/bin/env bash
# Launch SGLang proofreading servers on one Slurm node and register URLs in BACKENDS_FILE.
#
# Env vars:
#   PROOFREAD_MODEL_DIR   Local Qwen3-VL / proofreading model directory (required)
#   PROOFREAD_SERVED_NAME Served model ID (default: Qwen3-VL)
#   BACKENDS_FILE         Shared file read by cluster/fleet_router.py (required)
#   BASE_PORT             First HTTP port on this node (default: 30000)
#   GPUS_PER_INSTANCE     GPUs per TP server instance (default: 1)
#   TOTAL_GPUS            GPUs visible on this node (default: 8)
#   SGLANG_EXTRA_ARGS     Optional extra sglang flags

set -euo pipefail

: "${PROOFREAD_MODEL_DIR:?set PROOFREAD_MODEL_DIR}"
: "${BACKENDS_FILE:?set BACKENDS_FILE}"

SERVED_NAME="${PROOFREAD_SERVED_NAME:-Qwen3-VL}"
BASE_PORT="${BASE_PORT:-30000}"
TP="${GPUS_PER_INSTANCE:-1}"
TOTAL_GPUS="${TOTAL_GPUS:-8}"
NODE_HOST="$(hostname -s)"

pids=()
cleanup() {
    for p in "${pids[@]:-}"; do
        kill "${p}" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

for ((g=0; g + TP <= TOTAL_GPUS; g+=TP)); do
    devs=""
    for ((i=0; i<TP; i++)); do
        devs="${devs:+${devs},}$((g + i))"
    done
    port=$((BASE_PORT + g))
    cache_dir="/tmp/${USER:-user}-sglang-${port}"
    mkdir -p "${cache_dir}"
    CUDA_VISIBLE_DEVICES="${devs}" \
    HF_HUB_OFFLINE=1 \
    TRITON_CACHE_DIR="${cache_dir}/triton" \
    XDG_CACHE_HOME="${cache_dir}" \
    sglang serve \
        --model-path "${PROOFREAD_MODEL_DIR}" \
        --served-model-name "${SERVED_NAME}" \
        --tp "${TP}" \
        --host 0.0.0.0 \
        --port "${port}" \
        ${SGLANG_EXTRA_ARGS:-} &
    pids+=($!)
    printf 'http://%s:%s\n' "${NODE_HOST}" "${port}" >> "${BACKENDS_FILE}"
done

wait "${pids[@]}"
