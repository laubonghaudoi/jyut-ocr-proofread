# Cluster Bulk Orchestration Tools

Portable Slurm + multi-node orchestration utilities distilled from production
bulk digitization runs (1,400+ historical books / 519k+ pages) and configured
purely via CLI arguments and environment variables (zero site-specific paths).

## Architecture

1. **Proofread Fleet (`cluster/slurm/fleet.sbatch`, `fleet-node.sh`, `cluster/fleet_router.py`)**
   - Dedicated GPU nodes serve the proofreading VLM via OpenAI-compatible HTTP endpoints and append their `http://host:port` URLs to `BACKENDS_FILE`.
   - `fleet_router.py` exposes one proxy endpoint with least-inflight load balancing, background `/v1/models` health probes, dynamic `backends-file` reload, pre-response retry failover on upstream errors, and a JSON `/status` monitor endpoint.
2. **OCR + Proofread Workers (`cluster/mass_ocr.py`, `cluster/slurm/ocr-node.sbatch`, `vl-server.sh`)**
   - `vl-server.sh` launches local PaddleOCR-VL-1.6 SGLang workers with the required `runtime/paddleocr-vl-server-chat-template.jinja`.
   - `mass_ocr.py` coordinates `survey_pdf.py` (`--dpi auto`), `ocr_book.py` (`--max-missing-engine-b`), `book_profile.py`, `proofread_pages.py`, and `finish_book.py`.
3. **Production Safeguards Built Into `mass_ocr.py`**
   - **Per-book lock (`.book.lock`)**: Non-blocking `fcntl.flock` skips books currently held by another node or retry pass.
   - **GPU circuit breaker (`--gpu-max-consecutive-failures`)**: Automatically retires a poisoned GPU card after consecutive infrastructure failures so dead cards do not burn through the queue.
   - **Scheduling (`--order alternate|smallest|input`)**: `alternate` pairs smallest and largest books so multi-hundred-page books start early while small books deliver steadily.
   - **Proofread-only wipe guard (`--ocr-gpus ""`)**: Never wipes or re-renders existing `renders/` or `ocr-*` folders on CPU/cutover nodes.
4. **Monitoring, Health & Live Node Cutover**
   - `cluster/monitor.py`: Real-time throughput, per-stage counts, and fleet router status.
   - `cluster/mass_health.py`: Detects failed stages, stale `in-progress` status files, and untracked work folders.
   - `cluster/tcp_pipe.py` + `cluster/cutover.sh`: Parameterized two-pass streaming node migration when Slurm wall-time expiry approaches.

## Environment Variables

| Variable | Purpose |
|---|---|
| `JYUT_SKILL_DIR` | Path to this repository |
| `JYUT_OCR_PYTHON` | Python interpreter with skill dependencies (Pillow, etc.) |
| `JYUT_WORK_DIR` | Fast scratch root for per-book `renders/`, `ocr-*`, `proofread/` |
| `JYUT_OUT_DIR` | Shared run directory for `status.json`, `events.jsonl`, and stage logs |
| `PADDLE_VL_MODEL_DIR` | Local pinned PaddleOCR-VL weights directory |
| `PROOFREAD_MODEL_DIR` | Local proofreading VLM weights directory |
| `PROOFREAD_ENDPOINT` | Proxy URL exposed by `fleet_router.py` (`http://host:port/v1/chat/completions`) |
| `PROOFREAD_MODEL` | Served proofreading model ID |
