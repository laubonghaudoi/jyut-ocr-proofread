#!/usr/bin/env bash
# Parameterized two-pass live cutover between Slurm nodes (zero hardcoded paths/IDs).
#
# Required env vars:
#   SRC_JID       Source Slurm job ID
#   DST_JID       Destination Slurm job ID
#   DST_HOST      Destination node hostname/IP reachable from SRC node
#   PORT          Direct TCP port for cluster/tcp_pipe.py (e.g. 19300)
#   WORK_ROOT     Shared node-local scratch work directory (e.g. /tmp/$USER-books)
#   OUT_DIR       Shared cluster status directory (mass_ocr --out)
#   SKILL_DIR     Path to jyut-ocr-proofread repository
# Optional env vars:
#   STATE_DIR     Cutover scratch state directory (default: $OUT_DIR/cutover)
#   PYTHON_BIN    Python interpreter (default: ${JYUT_OCR_PYTHON:-python3})

set -euo pipefail

: "${SRC_JID:?set SRC_JID}"
: "${DST_JID:?set DST_JID}"
: "${DST_HOST:?set DST_HOST}"
: "${PORT:?set PORT}"
: "${WORK_ROOT:?set WORK_ROOT}"
: "${OUT_DIR:?set OUT_DIR}"
: "${SKILL_DIR:?set SKILL_DIR}"

STATE_DIR="${STATE_DIR:-${OUT_DIR}/cutover}"
PYTHON_BIN="${PYTHON_BIN:-${JYUT_OCR_PYTHON:-python3}}"
TCP_PIPE="${SKILL_DIR}/cluster/tcp_pipe.py"
mkdir -p "${STATE_DIR}"

echo "[cutover] Pass 1: snapshot incomplete/uncollected books from ${SRC_JID} -> ${DST_JID} (${DST_HOST}:${PORT})"

srun --overlap --jobid="${SRC_JID}" --ntasks=1 "${PYTHON_BIN}" - "${WORK_ROOT}" "${OUT_DIR}" > "${STATE_DIR}/pass1-books.txt" << 'PYEOF'
import json, os, sys
work_root, out_dir = sys.argv[1], sys.argv[2]
for name in sorted(os.listdir(work_root)):
    dp = os.path.join(work_root, name)
    if not os.path.isdir(dp):
        continue
    sf = os.path.join(out_dir, name, "status.json")
    if os.path.isfile(sf):
        st = json.load(open(sf)).get("stages", {})
        if (st.get("collect") or {}).get("exit") == 0:
            continue
    print(name)
PYEOF

if [ ! -s "${STATE_DIR}/pass1-books.txt" ]; then
    echo "[cutover] No incomplete books found in ${WORK_ROOT}; nothing to migrate."
    exit 0
fi

srun --overlap --jobid="${DST_JID}" --ntasks=1 bash -lc "
  mkdir -p '${WORK_ROOT}'
  '${PYTHON_BIN}' '${TCP_PIPE}' recv '${PORT}' | tar -C '${WORK_ROOT}' -xf -
" > "${STATE_DIR}/p1-recv.log" 2>&1 &
RECV_PID=$!
sleep 2
srun --overlap --jobid="${SRC_JID}" --ntasks=1 bash -lc "
  tar -C '${WORK_ROOT}' -cf - -T '${STATE_DIR}/pass1-books.txt' | '${PYTHON_BIN}' '${TCP_PIPE}' send '${DST_HOST}' '${PORT}'
" > "${STATE_DIR}/p1-send.log" 2>&1
wait "${RECV_PID}"

echo "[cutover] Stop source workers cleanly on ${SRC_JID} before Pass 2 delta sync if not already stopped."
echo "[cutover] Pass 2: delta sync mutable state (excluding immutable renders/ocr-* already matched on DST)."

srun --overlap --jobid="${SRC_JID}" --ntasks=1 "${PYTHON_BIN}" - "${WORK_ROOT}" > "${STATE_DIR}/pass2-paths.txt" << 'PYEOF' < "${STATE_DIR}/pass1-books.txt"
import os, sys
work_root = sys.argv[1]
books = [line.strip() for line in sys.stdin if line.strip()]
for name in books:
    dp = os.path.join(work_root, name)
    if not os.path.isdir(dp):
        continue
    for entry in sorted(os.listdir(dp)):
        if entry in ("renders", "ocr-hunyuan", "ocr-paddle", "book-profile-crops"):
            continue
        print(f"{name}/{entry}")
PYEOF

if [ -s "${STATE_DIR}/pass2-paths.txt" ]; then
    srun --overlap --jobid="${DST_JID}" --ntasks=1 bash -lc "
      '${PYTHON_BIN}' '${TCP_PIPE}' recv '$((PORT + 1))' | tar -C '${WORK_ROOT}' -xf -
    " > "${STATE_DIR}/p2-recv.log" 2>&1 &
    RECV_PID=$!
    sleep 2
    srun --overlap --jobid="${SRC_JID}" --ntasks=1 bash -lc "
      tar -C '${WORK_ROOT}' -cf - -T '${STATE_DIR}/pass2-paths.txt' | '${PYTHON_BIN}' '${TCP_PIPE}' send '${DST_HOST}' '$((PORT + 1))'
    " > "${STATE_DIR}/p2-send.log" 2>&1
    wait "${RECV_PID}"
fi

echo "[cutover] Pass 1 + Pass 2 complete. Ready to start proofread-only mass_ocr.py on ${DST_JID} with --ocr-gpus ''."
