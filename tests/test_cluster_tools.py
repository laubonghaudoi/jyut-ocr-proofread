#!/usr/bin/env python3
"""Offline tests for generalized cluster orchestration utilities in cluster/."""

from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
CLUSTER_DIR = REPO_ROOT / "cluster"
if str(CLUSTER_DIR) not in sys.path:
    sys.path.insert(0, str(CLUSTER_DIR))

import fleet_router  # noqa: E402
import mass_health  # noqa: E402
import mass_ocr  # noqa: E402
import monitor  # noqa: E402
import tcp_pipe  # noqa: E402


class FleetRouterTests(unittest.TestCase):
    def test_least_inflight_routing_failover_and_status(self) -> None:
        counts = {"a": 0, "b": 0}
        counts_lock = threading.Lock()

        def make_handler(tag: str):
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:  # noqa: N802
                    body = b'{"data":[{"id":"mock"}]}\n'
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self) -> None:  # noqa: N802
                    length = int(self.headers.get("Content-Length", "0"))
                    _ = self.rfile.read(length)
                    with counts_lock:
                        counts[tag] += 1
                    resp = json.dumps({"backend": tag}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)

                def log_message(self, *_: object) -> None:
                    pass

            return Handler

        srv_a = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("a"))
        srv_b = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("b"))
        t_a = threading.Thread(target=srv_a.serve_forever, daemon=True)
        t_b = threading.Thread(target=srv_b.serve_forever, daemon=True)
        t_a.start()
        t_b.start()

        async def scenario() -> dict:
            backends_file: Path | None = None
            with tempfile.TemporaryDirectory() as tmp:
                backends_file = Path(tmp) / "backends.txt"
                backends_file.write_text(
                    f"http://127.0.0.1:{srv_a.server_address[1]}\n"
                    f"http://127.0.0.1:{srv_b.server_address[1]}\n",
                    encoding="utf-8",
                )
                router = fleet_router.FleetRouter(
                    backends_file=backends_file, health_interval=0.2
                )
                proxy_port, status_port = await router.start("127.0.0.1", 0, status_port=0)
                try:
                    def post_once() -> str:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                            data=b"{}",
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=5.0) as r:
                            return json.loads(r.read().decode("utf-8"))["backend"]

                    seen = [await asyncio.to_thread(post_once) for _ in range(4)]
                    self.assertEqual(set(seen), {"a", "b"})

                    # Shut down backend A mid-run: proxy must transparently fail over to B!
                    srv_a.shutdown()
                    srv_a.server_close()
                    failover_seen = await asyncio.to_thread(post_once)
                    self.assertEqual(failover_seen, "b")

                    def get_status() -> dict:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{status_port}/status", timeout=5.0
                        ) as r:
                            return json.loads(r.read().decode("utf-8"))

                    return await asyncio.to_thread(get_status)
                finally:
                    await router.stop()

        try:
            snap = asyncio.run(scenario())
            self.assertEqual(snap["totalRequests"], 5)
            self.assertEqual(snap["totalBackends"], 2)
        finally:
            try:
                srv_a.shutdown()
                srv_a.server_close()
            except OSError:
                pass
            srv_b.shutdown()
            srv_b.server_close()


class MassOcrAndHealthTests(unittest.TestCase):
    def test_order_books_modes(self) -> None:
        books = [
            {"id": "b300", "pages": 300},
            {"id": "b10", "pages": 10},
            {"id": "b100", "pages": 100},
            {"id": "b50", "pages": 50},
        ]
        self.assertEqual([b["id"] for b in mass_ocr.order_books(books, "input")], ["b300", "b10", "b100", "b50"])
        self.assertEqual([b["id"] for b in mass_ocr.order_books(books, "smallest")], ["b10", "b50", "b100", "b300"])
        self.assertEqual([b["id"] for b in mass_ocr.order_books(books, "alternate")], ["b10", "b300", "b50", "b100"])

    def test_acquire_book_lock_prevents_concurrent_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_book = Path(tmp) / "book1"
            first = mass_ocr.acquire_book_lock(work_book)
            self.assertIsNotNone(first)
            try:
                # Try lock from another process so BSD/POSIX flock semantics are genuinely tested.
                cmd = [
                    sys.executable,
                    "-c",
                    (
                        "import sys; sys.path.insert(0, %r); import mass_ocr; from pathlib import Path; "
                        "h = mass_ocr.acquire_book_lock(Path(%r)); sys.exit(0 if h is None else 1)"
                    )
                    % (str(CLUSTER_DIR), str(work_book)),
                ]
                res = subprocess.run(cmd, check=False)
                self.assertEqual(res.returncode, 0)
            finally:
                mass_ocr.release_book_lock(first)

            reacquired = mass_ocr.acquire_book_lock(work_book)
            self.assertIsNotNone(reacquired)
            mass_ocr.release_book_lock(reacquired)

    def test_gpu_circuit_breaker_trips_and_resets_and_wipe_guard(self) -> None:
        breaker = mass_ocr.GpuCircuitBreaker(["0", "1"], max_consecutive_failures=2)
        self.assertFalse(breaker.record_failure("0", "oom"))
        breaker.record_success("0")
        self.assertEqual(breaker.available_gpus(), ["0", "1"])
        self.assertFalse(breaker.record_failure("0", "oom"))
        self.assertTrue(breaker.record_failure("0", "illegal instruction"))
        self.assertEqual(breaker.available_gpus(), ["1"])
        self.assertIn("0", breaker.tripped_summary())

        self.assertTrue(mass_ocr.should_skip_ocr_wipe([], Path("/tmp/book")))
        self.assertFalse(mass_ocr.should_skip_ocr_wipe(["0"], Path("/tmp/book")))

    def test_mass_health_and_monitor_and_tcp_pipe_and_shell_syntax(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dir = root / "out"
            work_dir = root / "work"
            (out_dir / "b_ok").mkdir(parents=True)
            (out_dir / "b_fail").mkdir(parents=True)
            (out_dir / "b_stale").mkdir(parents=True)
            (work_dir / "b_orphan").mkdir(parents=True)

            (out_dir / "b_ok" / "status.json").write_text(
                json.dumps({"id": "b_ok", "pages": 12, "stages": {"finish": {"exit": 0}, "collect": {"exit": 0}}}),
                encoding="utf-8",
            )
            (out_dir / "b_fail" / "status.json").write_text(
                json.dumps({"id": "b_fail", "pages": 20, "stages": {"proof": {"exit": 1}}}),
                encoding="utf-8",
            )
            (out_dir / "b_stale" / "status.json").write_text(
                json.dumps(
                    {
                        "id": "b_stale",
                        "pages": 8,
                        "currentStage": "proof",
                        "updatedAt": "2020-01-01T00:00:00Z",
                        "stages": {"ocr": {"exit": 0}},
                    }
                ),
                encoding="utf-8",
            )

            health = mass_health.scan_out_dirs([out_dir], work_dir=work_dir, stale_minutes=30.0)
            self.assertEqual(health["completed"], 1)
            self.assertEqual(len(health["failed"]), 1)
            self.assertEqual(len(health["stale"]), 1)
            self.assertEqual(health["untrackedWorkDirs"], ["b_orphan"])

            snap = monitor.collect_snapshot([out_dir])
            self.assertEqual(snap["booksTracked"], 3)
            self.assertEqual(snap["booksCompleted"], 1)
            self.assertEqual(snap["pagesCompleted"], 12)

        # tcp_pipe round-trip
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        payload = b"cluster-tar-payload-" * 512
        recv_buf = io.BytesIO()
        recv_result: list[tuple[int, str]] = []

        def run_recv() -> None:
            total, sha, _ = tcp_pipe.recv_stream(port, recv_buf, host="127.0.0.1")
            recv_result.append((total, sha))

        t = threading.Thread(target=run_recv, daemon=True)
        t.start()
        sent_total, sent_sha, _ = tcp_pipe.send_stream("127.0.0.1", port, io.BytesIO(payload))
        t.join(timeout=5.0)
        self.assertEqual(recv_buf.getvalue(), payload)
        self.assertEqual(recv_result, [(sent_total, sent_sha)])

        # bash -n syntax validation across all shell/sbatch scripts in cluster/
        if shutil.which("bash"):
            scripts = [CLUSTER_DIR / "cutover.sh", *sorted((CLUSTER_DIR / "slurm").glob("*.sh")), *sorted((CLUSTER_DIR / "slurm").glob("*.sbatch"))]
            self.assertGreaterEqual(len(scripts), 5)
            for script in scripts:
                res = subprocess.run(["bash", "-n", str(script)], text=True, capture_output=True, check=False)
                self.assertEqual(res.returncode, 0, f"{script}: {res.stderr}")


if __name__ == "__main__":
    unittest.main()
