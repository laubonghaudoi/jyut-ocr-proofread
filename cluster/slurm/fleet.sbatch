#!/usr/bin/env bash
#SBATCH --job-name=jyut-proof-fleet
#SBATCH --nodes=4
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=256G
#SBATCH --time=24:00:00

# Multi-node proofreading model fleet + central async least-inflight router.
#
# Export PROOFREAD_MODEL_DIR, JYUT_SKILL_DIR, RUN_SHARED_DIR before submitting.

set -euo pipefail

: "${JYUT_SKILL_DIR:?set JYUT_SKILL_DIR}"
: "${PROOFREAD_MODEL_DIR:?set PROOFREAD_MODEL_DIR}"
: "${RUN_SHARED_DIR:?set RUN_SHARED_DIR}"

PYTHON_BIN="${JYUT_OCR_PYTHON:-python3}"
BACKENDS_FILE="${RUN_SHARED_DIR}/fleet-backends.txt"
ROUTER_PORT="${ROUTER_PORT:-8080}"
STATUS_PORT="${STATUS_PORT:-8081}"

mkdir -p "${RUN_SHARED_DIR}"
: > "${BACKENDS_FILE}"

srun --ntasks="${SLURM_NNODES}" --ntasks-per-node=1 \
    env BACKENDS_FILE="${BACKENDS_FILE}" PROOFREAD_MODEL_DIR="${PROOFREAD_MODEL_DIR}" \
    "${JYUT_SKILL_DIR}/cluster/slurm/fleet-node.sh" &
FLEET_PID=$!

cleanup() {
    kill "${FLEET_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

exec "${PYTHON_BIN}" "${JYUT_SKILL_DIR}/cluster/fleet_router.py" \
    --host 0.0.0.0 \
    --port "${ROUTER_PORT}" \
    --status-port "${STATUS_PORT}" \
    --backends-file "${BACKENDS_FILE}"
