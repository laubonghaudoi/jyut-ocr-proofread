#!/usr/bin/env python3
"""Cluster bulk orchestrator for two-engine OCR + remote-endpoint proofreading.

Generalizes the multi-node Slurm bulk workflow into a portable orchestrator
with five production safeguards learned from 1,400+ book runs:

1. Per-book non-blocking ``fcntl.flock`` on ``<work>/<book_id>/.book.lock`` so
   overlapping worker pools never corrupt the same book directory.
2. GPU fast-fail circuit breaker (``--gpu-max-consecutive-failures``): takes a
   GPU lane offline after N consecutive infrastructure failures instead of
   letting a poisoned card fail every book in the queue.
3. ``--order {alternate,smallest,input}`` scheduling (``alternate`` pairs the
   smallest and largest books so big books start early while small books finish
   steadily without tail pileups).
4. ``--ocr-gpus ""`` proofread-only wipe guard: when running without local OCR
   GPUs (e.g. after node cutover), refuses to remove or re-render existing
   ``renders/`` or ``ocr-*`` directories.
5. Pass-through ``--dpi auto|<N>`` and ``--max-missing-engine-b N`` flags.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, IO, Sequence


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def order_books(books: Sequence[dict[str, Any]], mode: str = "alternate") -> list[dict[str, Any]]:
    """Order BOOK specs for scheduling: 'input', 'smallest', or 'alternate'."""
    items = list(books)
    if mode == "input":
        return items
    by_size = sorted(items, key=lambda b: (int(b.get("pages") or 0), str(b.get("id") or "")))
    if mode == "smallest":
        return by_size
    if mode == "alternate":
        out: list[dict[str, Any]] = []
        lo, hi = 0, len(by_size) - 1
        while lo <= hi:
            out.append(by_size[lo])
            if lo != hi:
                out.append(by_size[hi])
            lo += 1
            hi -= 1
        return out
    raise ValueError(f"unknown order mode: {mode!r}")


def should_skip_ocr_wipe(ocr_gpus: Sequence[str], book_work_dir: Path) -> bool:
    """Return True on proofread-only workers (--ocr-gpus "") so existing
    renders/ and ocr-* drafts are never wiped or re-rendered."""
    return len(ocr_gpus) == 0


def acquire_book_lock(book_work_dir: Path) -> IO[str] | None:
    """Try a non-blocking exclusive lock on <book_work_dir>/.book.lock.

    Returns the open file handle on success, or None if another worker holds it.
    """
    book_work_dir.mkdir(parents=True, exist_ok=True)
    lock_path = book_work_dir / ".book.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} started={_utc_now()}\n")
    handle.flush()
    return handle


def release_book_lock(handle: IO[str] | None) -> None:
    if handle is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        handle.close()


class GpuCircuitBreaker:
    """Track consecutive infrastructure failures per GPU lane and fence off dead cards."""

    def __init__(self, gpus: Sequence[str], max_consecutive_failures: int = 3) -> None:
        self.gpus = [g.strip() for g in gpus if g.strip()]
        self.limit = max(0, int(max_consecutive_failures))
        self._lock = threading.Lock()
        self._failures: dict[str, int] = {g: 0 for g in self.gpus}
        self._tripped: dict[str, str] = {}

    def record_success(self, gpu: str) -> None:
        with self._lock:
            if gpu in self._failures:
                self._failures[gpu] = 0

    def record_failure(self, gpu: str, reason: str = "") -> bool:
        """Increment failure count for GPU; returns True if GPU just tripped offline."""
        if self.limit <= 0:
            return False
        with self._lock:
            if gpu not in self._failures or gpu in self._tripped:
                return False
            self._failures[gpu] += 1
            if self._failures[gpu] >= self.limit:
                self._tripped[gpu] = reason or f"{self._failures[gpu]} consecutive failures"
                return True
            return False

    def available_gpus(self) -> list[str]:
        with self._lock:
            return [g for g in self.gpus if g not in self._tripped]

    def tripped_summary(self) -> dict[str, str]:
        with self._lock:
            return dict(self._tripped)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_event(events_path: Path, lock: threading.Lock, event: dict[str, Any]) -> None:
    row = {"at": _utc_now(), **event}
    with lock:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _run_cmd(
    cmd: Sequence[str], log_path: Path, *, env: dict[str, str], cwd: Path
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return int(proc.returncode)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--books", required=True, type=Path, help="JSON list of {id, pdf, ...} book specs")
    p.add_argument("--out", required=True, type=Path, help="cluster output/status directory")
    p.add_argument("--work", required=True, type=Path, help="per-book scratch workspace root")
    p.add_argument(
        "--skill-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="path to jyut-ocr-proofread repository",
    )
    p.add_argument("--python", default=os.environ.get("JYUT_OCR_PYTHON") or sys.executable)
    p.add_argument("--ocr-gpus", default="", help="comma-separated GPU IDs for OCR, or '' for proofread-only")
    p.add_argument("--vl-urls", default="", help="comma-separated PaddleOCR-VL SGLang/vLLM OpenAI base URLs")
    p.add_argument("--vl-revision", default=os.environ.get("OCR_PADDLE_VL_SERVER_REVISION", ""))
    p.add_argument("--vl-model", default=os.environ.get("OCR_PADDLE_VL_SERVER_MODEL", "PaddleOCR-VL-1.6"))
    p.add_argument("--vl-concurrency", type=int, default=16)
    p.add_argument("--proof-endpoint", default=os.environ.get("PROOFREAD_ENDPOINT", ""))
    p.add_argument("--proof-model", default=os.environ.get("PROOFREAD_MODEL", ""))
    p.add_argument("--proof-effort", default=os.environ.get("PROOFREAD_EFFORT", "xhigh"))
    p.add_argument("--proofread-books", type=int, default=8, help="concurrent books in proofreading/finishing")
    p.add_argument("--proof-workers", type=int, default=32)
    p.add_argument("--proof-page-concurrency", type=int, default=4)
    p.add_argument("--max-inflight", type=int, default=128)
    p.add_argument("--order", choices=("alternate", "smallest", "input"), default="alternate")
    p.add_argument("--dpi", default="auto", help="survey_pdf --dpi ('auto' or integer >= 72)")
    p.add_argument("--max-pixels", type=int, default=40_000_000)
    p.add_argument("--render-jobs", type=int, default=4)
    p.add_argument("--max-missing-engine-b", type=int, default=1)
    p.add_argument("--engine-a-after-b-fails", action="store_true")
    p.add_argument("--gpu-max-consecutive-failures", type=int, default=3)
    p.add_argument("--ocr-retries", type=int, default=2)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--deliver", action="store_true", help="allow finish_book to deliver beside PDF")
    p.add_argument("--no-finish", action="store_true", help="stop after proofreading without finish_book.py")
    p.add_argument("--dry-run", action="store_true", help="print scheduling plan without running subprocesses")
    return p


def _process_book(
    spec: dict[str, Any],
    args: argparse.Namespace,
    *,
    ocr_gpu: str | None,
    vl_url: str | None,
    events_path: Path,
    event_lock: threading.Lock,
) -> tuple[bool, bool]:
    """Run pipeline stages for one book. Returns (succeeded, gpu_infra_ok)."""
    book_id = str(spec["id"])
    pdf = Path(spec["pdf"]) if spec.get("pdf") else None
    work_dir = args.work / book_id
    out_dir = args.out / book_id
    lock_handle = acquire_book_lock(work_dir)
    if lock_handle is None:
        _append_event(events_path, event_lock, {"id": book_id, "stage": "lock", "status": "skipped-locked"})
        return True, True

    gpu_infra_ok = True
    try:
        scripts = args.skill_dir / "scripts"
        py = args.python
        status_path = out_dir / "status.json"
        status: dict[str, Any] = {
            "id": book_id,
            "pdf": str(pdf) if pdf else None,
            "pages": spec.get("pages"),
            "workDir": str(work_dir),
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
            "currentStage": "init",
            "stages": {},
        }
        _write_json_atomic(status_path, status)

        def record_stage(name: str, code: int) -> None:
            status["stages"][name] = {"exit": code, "at": _utc_now()}
            status["currentStage"] = name
            status["updatedAt"] = _utc_now()
            _write_json_atomic(status_path, status)
            _append_event(events_path, event_lock, {"id": book_id, "stage": name, "exit": code})

        base_env = dict(os.environ)
        proof_only = should_skip_ocr_wipe([ocr_gpu] if ocr_gpu else [], work_dir)
        manifest = work_dir / "page-manifest.json"

        # 1. Render (never overwrite/wipe existing renders on proofread-only nodes)
        if not manifest.is_file():
            if proof_only:
                record_stage("render", 2)
                return False, True
            if pdf is None or not pdf.is_file():
                record_stage("render", 2)
                return False, True
            code = _run_cmd(
                [
                    py,
                    str(scripts / "survey_pdf.py"),
                    str(pdf),
                    str(work_dir),
                    "--dpi",
                    str(args.dpi),
                    "--max-pixels",
                    str(args.max_pixels),
                    "--jobs",
                    str(args.render_jobs),
                ],
                out_dir / "render.log",
                env=base_env,
                cwd=args.skill_dir,
            )
            record_stage("render", code)
            if code != 0:
                return False, True
        else:
            record_stage("render", 0)

        # 2. Dual OCR
        engine_record = work_dir / "reports" / "ocr-engines.json"
        if not proof_only and not engine_record.is_file():
            assert ocr_gpu is not None
            ocr_env = dict(base_env)
            ocr_env["CUDA_VISIBLE_DEVICES"] = ocr_gpu
            ocr_env["OCR_PADDLE_DEVICE"] = f"gpu:{ocr_gpu}" if "," not in ocr_gpu else "gpu:0"
            if vl_url:
                ocr_env["OCR_PADDLE_VL_BACKEND"] = "sglang-server"
                ocr_env["OCR_PADDLE_VL_SERVER_URL"] = vl_url
                ocr_env["OCR_PADDLE_VL_SERVER_MODEL"] = args.vl_model
                if args.vl_revision:
                    ocr_env["OCR_PADDLE_VL_SERVER_REVISION"] = args.vl_revision
                ocr_env["OCR_PADDLE_VL_CONCURRENCY"] = str(args.vl_concurrency)
            ocr_cmd = [
                py,
                str(scripts / "ocr_book.py"),
                str(work_dir),
                "--engine",
                "hunyuan-paddle",
                "--max-missing-engine-b",
                str(args.max_missing_engine_b),
            ]
            code = -1
            for _ in range(max(1, args.ocr_retries + 1)):
                code = _run_cmd(ocr_cmd, out_dir / "ocr.log", env=ocr_env, cwd=args.skill_dir)
                if code == 0:
                    break
            record_stage("ocr", code)
            if code != 0:
                gpu_infra_ok = False
                return False, gpu_infra_ok
        else:
            record_stage("ocr", 0)

        # 3. Profile (for >= 5 pages)
        profile_json = work_dir / "book-profile.json"
        if not profile_json.is_file():
            prof_cmd = [
                py,
                str(scripts / "book_profile.py"),
                str(work_dir),
                "--endpoint",
                args.proof_endpoint,
                "--model",
                args.proof_model,
            ]
            code = _run_cmd(prof_cmd, out_dir / "profile.log", env=base_env, cwd=args.skill_dir)
            if code not in (0,):
                record_stage("profile", code)
                return False, True
        record_stage("profile", 0)

        # 4. Proofread
        proof_cmd = [
            py,
            str(scripts / "proofread_pages.py"),
            str(work_dir),
            "--endpoint",
            args.proof_endpoint,
            "--model",
            args.proof_model,
            "--reasoning-effort",
            args.proof_effort,
            "--workers",
            str(args.proof_workers),
            "--page-concurrency",
            str(args.proof_page_concurrency),
            "--max-inflight",
            str(args.max_inflight),
        ]
        if args.engine_a_after_b_fails:
            proof_cmd.append("--engine-a-after-b-fails")
        code = -1
        for _ in range(max(1, args.retries + 1)):
            code = _run_cmd(proof_cmd, out_dir / "proofread.log", env=base_env, cwd=args.skill_dir)
            if code == 0:
                break
        record_stage("proof", code)
        if code != 0:
            return False, True

        # 5. Finish (optional)
        if not args.no_finish:
            fin_cmd = [py, str(scripts / "finish_book.py"), str(work_dir)]
            if not args.deliver:
                fin_cmd.append("--no-deliver")
            code = _run_cmd(fin_cmd, out_dir / "finish.log", env=base_env, cwd=args.skill_dir)
            record_stage("finish", code)
            if code != 0:
                return False, True
            record_stage("collect", 0)
        return True, True
    finally:
        release_book_lock(lock_handle)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    books_raw = json.loads(args.books.read_text(encoding="utf-8"))
    books = order_books(books_raw, args.order)
    ocr_gpus = [g.strip() for g in (args.ocr_gpus or "").split(",") if g.strip()]
    vl_urls = [u.strip() for u in (args.vl_urls or "").split(",") if u.strip()]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "books": [b.get("id") for b in books],
                    "order": args.order,
                    "ocrGpus": ocr_gpus,
                    "proofreadOnly": should_skip_ocr_wipe(ocr_gpus, args.work),
                    "dpi": args.dpi,
                    "maxMissingEngineB": args.max_missing_engine_b,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    events_path = args.out / "events.jsonl"
    event_lock = threading.Lock()
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())

    breaker = GpuCircuitBreaker(ocr_gpus, args.gpu_max_consecutive_failures)
    gpu_q: queue.Queue[str | None] = queue.Queue()
    if ocr_gpus:
        for g in ocr_gpus:
            gpu_q.put(g)

    def _vl_for_gpu(gpu: str | None) -> str | None:
        if not vl_urls or gpu is None:
            return vl_urls[0] if vl_urls else None
        idx = ocr_gpus.index(gpu) if gpu in ocr_gpus else 0
        return vl_urls[idx % len(vl_urls)]

    work_q: queue.Queue[dict[str, Any]] = queue.Queue()
    for spec in books:
        work_q.put(spec)

    failures = 0
    fail_lock = threading.Lock()
    worker_count = max(1, len(ocr_gpus) if ocr_gpus else args.proofread_books)

    def _worker() -> None:
        nonlocal failures
        while not stop_event.is_set():
            try:
                spec = work_q.get_nowait()
            except queue.Empty:
                return
            gpu = gpu_q.get() if ocr_gpus else None
            try:
                if gpu is not None and gpu not in breaker.available_gpus():
                    # Put book back if another healthy GPU exists, else fail.
                    if breaker.available_gpus():
                        work_q.put(spec)
                        continue
                ok, gpu_ok = _process_book(
                    spec,
                    args,
                    ocr_gpu=gpu,
                    vl_url=_vl_for_gpu(gpu),
                    events_path=events_path,
                    event_lock=event_lock,
                )
                if not ok:
                    with fail_lock:
                        failures += 1
                if gpu is not None:
                    if gpu_ok:
                        breaker.record_success(gpu)
                    else:
                        if breaker.record_failure(gpu, f"failed on {spec.get('id')}"):
                            _append_event(
                                events_path,
                                event_lock,
                                {"stage": "gpu-circuit-breaker", "gpu": gpu, "status": "tripped"},
                            )
                            continue  # Retire this GPU from gpu_q!
                if gpu is not None and gpu in breaker.available_gpus():
                    gpu_q.put(gpu)
            finally:
                work_q.task_done()

    with cf.ThreadPoolExecutor(worker_count) as pool:
        futs = [pool.submit(_worker) for _ in range(worker_count)]
        for fut in futs:
            fut.result()

    tripped = breaker.tripped_summary()
    if tripped:
        print(f"[mass-ocr] tripped GPUs: {tripped}", file=sys.stderr, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
