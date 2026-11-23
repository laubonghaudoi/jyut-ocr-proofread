#!/usr/bin/env bash
# vl-server.sh GPU PORT [extra server flags...]
# Serve PaddleOCR-VL-1.6 (pinned weights) via SGLang with the skill's required chat template.
#
# Required env vars:
#   JYUT_SKILL_DIR      Path to jyut-ocr-proofread repo (containing runtime/paddleocr-vl-server-chat-template.jinja)
#   PADDLE_VL_MODEL_DIR Path to local pinned PaddleOCR-VL-1.6 weights directory
# Optional env vars:
#   VL_SERVED_MODEL     OpenAI served model name (default: PaddleOCR-VL-1.6)
#   VL_CACHE_ROOT       Per-GPU Triton/XDG cache directory root (default: /tmp/$USER-vl-cache)

set -euo pipefail

gpu="${1:?usage: vl-server.sh GPU PORT [extra sglang flags]}"
port="${2:?usage: vl-server.sh GPU PORT [extra sglang flags]}"
shift 2

: "${JYUT_SKILL_DIR:?set JYUT_SKILL_DIR}"
: "${PADDLE_VL_MODEL_DIR:?set PADDLE_VL_MODEL_DIR}"

CHAT_TEMPLATE="${JYUT_SKILL_DIR}/runtime/paddleocr-vl-server-chat-template.jinja"
SERVED_MODEL="${VL_SERVED_MODEL:-PaddleOCR-VL-1.6}"
CACHE_ROOT="${VL_CACHE_ROOT:-/tmp/${USER:-user}-vl-cache}"

if [ -e /usr/local/cuda/compat/libcuda.so.1 ]; then
    export LD_LIBRARY_PATH="/usr/local/cuda/compat:${LD_LIBRARY_PATH:-}"
fi
export CUDA_VISIBLE_DEVICES="${gpu}"
export HF_HUB_OFFLINE=1
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"

gpu_cache="${CACHE_ROOT}/vl-${gpu}"
mkdir -p "${gpu_cache}"
export TRITON_CACHE_DIR="${gpu_cache}/triton"
export XDG_CACHE_HOME="${gpu_cache}"

exec sglang serve \
    --model-path "${PADDLE_VL_MODEL_DIR}" \
    --served-model-name "${SERVED_MODEL}" \
    --chat-template "${CHAT_TEMPLATE}" \
    --trust-remote-code \
    --host 127.0.0.1 \
    --port "${port}" \
    --enable-metrics \
    "$@"
