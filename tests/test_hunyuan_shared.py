"""CPU-only broker lifecycle tests with independent fake HTTP model processes."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from _gpu_queue import GPUQueue, GPUQueueError, GPUQueueTimeout


FAKE = r'''
import argparse, base64, json, os, signal, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int)
parser.add_argument('--alias')
parser.add_argument('--parallel', type=int)
parser.add_argument('--ctx-size', type=int)
a, _ = parser.parse_known_args()
root = Path(__ROOT__)
lock = threading.Lock()
def log(event, **extra):
    with lock, (root / 'events.jsonl').open('a') as out:
        out.write(json.dumps(dict(event=event, pid=os.getpid(), time=time.time(), **extra)) + '\n')
def stop(*args):
    log('stop')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
log('start', slots=a.parallel, ctx=a.ctx_size, args=__import__('sys').argv[1:],
    graphs_off=os.environ.get('GGML_CUDA_DISABLE_GRAPHS'))
(root / 'server.pid').write_text(str(os.getpid()))
if (root / 'fail-start').exists():
    raise SystemExit(17)
if (root / 'slow-start').exists():
    time.sleep(3)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def send(self, value):
        body = json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try: self.wfile.write(body)
        except BrokenPipeError: pass
    def do_GET(self):
        self.send({'data': [{'id': a.alias}]})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        page = base64.b64decode(body['messages'][0]['content'][0]['image_url']['url'].split(',')[1]).decode()
        log('begin', page=page)
        crash = root / 'crash-times'
        if page == 'crash' and (root / 'crash-with-graphs').exists() and 'GGML_CUDA_DISABLE_GRAPHS' not in os.environ:
            # The M1 abort: llama.cpp's CUDA graphs on, and off it reads the page.
            log('crash', page=page)
            os._exit(134)
        if page == 'livelock' and (root / 'livelock-with-graphs').exists() and 'GGML_CUDA_DISABLE_GRAPHS' not in os.environ:
            # Simulated CUDA-graph livelock: /v1/models stays 200 OK while completion hangs.
            log('livelock', page=page)
            time.sleep(60)
            return
        if page == 'crash' and crash.exists() and int(crash.read_text()) > 0:
            # llama.cpp aborting mid-request (M1, 2026-11-20): the process is gone at once.
            crash.write_text(str(int(crash.read_text()) - 1))
            log('crash', page=page)
            os._exit(134)
        if page == 'bad':
            self.send_error(500, 'this page fails on its own')
            return
        time.sleep(2 if page == 'slow' else 0.15)
        log('end', page=page)
        self.send({'model': a.alias, 'choices': [{'finish_reason': 'stop', 'message': {'content': page}}]})
ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
'''


def wait_until(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("timed out waiting for isolated broker state")


class SharedBrokerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Freeze the executable sources: other tests/development edits must not
        # change a running daemon's source identity halfway through a case.
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for name in ("jyut_ocr/__init__.py", "jyut_ocr/paths.py", "jyut_ocr/core/__init__.py",
                     "jyut_ocr/core/common.py", "_ocr_runner_common.py", "_hunyuan_shared.py", "_gpu_queue.py",
                     "_phase_budget.py", "_ocr_engines.py", "run_hunyuanocr.py"):
            dst = self.scripts / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / name, dst)
        name = "shared_test_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, self.scripts / "_hunyuan_shared.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        spec.loader.exec_module(self.module)
        self.binary = self.root / "fake-server"
        self.binary.with_suffix(".py").write_text(textwrap.dedent(FAKE).replace("__ROOT__", repr(str(self.root))), encoding="utf-8")
        self.binary.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$0.py\" \"$@\"\n", encoding="utf-8")
        self.binary.chmod(0o700)
        for name in ("model", "mmproj"):
            (self.root / name).write_bytes(b"fake model bytes")
        self.config = {"binary": str(self.binary), "model": str(self.root / "model"),
                       "mmproj": str(self.root / "mmproj"), "model_id": "hunyuanocr",
                       "ctx": 16384, "slots": 2, "identity": "unit-test-source", "max_tokens": 100}
        self.config = self.module._config(self.config)
        self.queue_dir = self.root / "queue"
        self.resource = "isolated-fake-gpu"
        self.clients = []
        self.runtime_directories = set()

    def tearDown(self):
        for client in self.clients:
            client.close()
        for directory in self.runtime_directories:
            for owner in directory.glob("*.owner"):
                def free():
                    with owner.open("rb") as handle:
                        try:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            return True
                        except BlockingIOError:
                            return False
                try:
                    wait_until(free)
                except AssertionError:
                    # Only a PID returned by this test's private configuration
                    # is eligible for cleanup; never inspect production state.
                    for client in self.clients:
                        pid = client.report.get("brokerPid")
                        if pid:
                            try: os.kill(pid, signal.SIGTERM)
                            except ProcessLookupError: pass
                    raise
            shutil.rmtree(directory)
        self.temp.cleanup()

    def client(self, config=None, **kwargs):
        config = config or self.config
        body = self.module._identity(config, self.queue_dir.resolve(), self.resource)
        directory, _ = self.module._paths(body)
        self.runtime_directories.add(directory)
        client = self.module.SharedHunyuanClient(config, self.queue_dir, self.resource, **kwargs)
        self.clients.append(client)
        return client

    def page(self, text):
        path = self.root / (text + ".png")
        path.write_text(text)
        return path

    def events(self):
        path = self.root / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_two_members_share_server_cap_slots_and_survive_one_close(self):
        first = self.client(queue_timeout=5)
        second = self.client(queue_timeout=5)
        self.assertEqual(first.report["serverPid"], second.report["serverPid"])
        self.assertEqual(first.report["brokerPid"], second.report["brokerPid"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            requests = [pool.submit(client.ocr, self.page(f"p{i}"), 100, 5)
                        for i, client in enumerate((first, first, second, second))]
            self.assertEqual([future.result()["choices"][0]["message"]["content"] for future in requests],
                             ["p0", "p1", "p2", "p3"])
        active = peak = 0
        for event in self.events():
            if event["event"] == "begin": active += 1
            if event["event"] == "end": active -= 1
            peak = max(peak, active)
        self.assertEqual(peak, 2)
        self.assertEqual(active, 0)
        self.assertEqual(self.events()[0]["ctx"], 32768)
        first.close()
        second.ocr(self.page("still-alive"), 100, 5)
        self.assertEqual(sum(row["event"] == "start" for row in self.events()), 1)
        second.close()
        wait_until(lambda: any(row["event"] == "stop" for row in self.events()))
        with _lease(self.queue_dir, self.resource):
            pass

    def test_incompatible_configuration_waits_and_later_same_config_cannot_starve_it(self):
        first = self.client(queue_timeout=5)
        other_config = dict(self.config, slots=3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            incompatible = pool.submit(self.client, other_config, queue_timeout=8)
            queue = GPUQueue(self.queue_dir, self.resource)
            wait_until(lambda: len(list(queue.directory.glob("*.ticket"))) == 2)
            late_same = pool.submit(self.client, queue_timeout=8)
            time.sleep(0.2)
            self.assertFalse(incompatible.done())
            self.assertFalse(late_same.done())
            first.close()
            other = incompatible.result(timeout=5)
            self.assertFalse(late_same.done())
            other.close()
            late = late_same.result(timeout=5)
            self.assertNotEqual(late.report["serverPid"], first.report["serverPid"])
            late.ocr(self.page("after-fifo"), 100, 5)
        starts = [event["slots"] for event in self.events() if event["event"] == "start"]
        self.assertEqual(starts, [2, 3, 2])

    def test_queue_timeout_detaches_waiter_without_loading_model(self):
        lease = GPUQueue(self.queue_dir, self.resource).acquire(timeout=0)
        try:
            with self.assertRaises(GPUQueueTimeout):
                self.client(queue_timeout=0.2)
            def only_owner():
                with lease._guard():
                    return lease._live_tickets() == [lease.ticket]
            wait_until(only_owner)
            self.assertEqual(self.events(), [])
        finally:
            lease.close()

    def test_startup_timeout_stops_owned_child(self):
        (self.root / "slow-start").touch()
        with self.assertRaises(GPUQueueTimeout):
            self.client(queue_timeout=0.25)
        wait_until(lambda: any(row["event"] == "stop" for row in self.events()))
        with _lease(self.queue_dir, self.resource):
            pass

    def test_close_interrupts_active_client_call_and_keeps_other_membership(self):
        first = self.client(queue_timeout=5)
        second = self.client(queue_timeout=5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(first.ocr, self.page("slow"), 100, 10)
            wait_until(lambda: any(row.get("page") == "slow" and row["event"] == "begin" for row in self.events()))
            started = time.monotonic()
            first.close()
            with self.assertRaises(self.module.SharedHunyuanError):
                request.result(timeout=1)
            self.assertLess(time.monotonic() - started, 1)
        result = second.ocr(self.page("other"), 100, 3)
        self.assertEqual(result["choices"][0]["message"]["content"], "other")
        self.assertEqual(sum(row["event"] == "start" for row in self.events()), 1)

    def test_startup_error_includes_log_and_releases_queue(self):
        (self.root / "fail-start").touch()
        with self.assertRaisesRegex(GPUQueueError, "log"):
            self.client(queue_timeout=5)
        with _lease(self.queue_dir, self.resource):
            pass

    def test_reject_invalid_configuration_before_spawning(self):
        for change in ({"slots": 0}, {"slots": 65}, {"identity": ""}, {"unrecognized": True}, {"device": "bogus"}):
            with self.assertRaises(GPUQueueError):
                self.module.SharedHunyuanClient(dict(self.config, **change), self.queue_dir, self.resource)
        self.assertEqual(self.events(), [])

    def test_device_profile_changes_broker_identity_and_cpu_flags_reach_child(self):
        cpu = self.module._config(dict(self.config, device='cpu'))
        cuda = self.module._config(dict(self.config, device='cuda'))
        cpu_id = self.module._identity(cpu, self.queue_dir.resolve(), self.resource)
        cuda_id = self.module._identity(cuda, self.queue_dir.resolve(), self.resource)
        self.assertNotEqual(cpu_id['digest'], cuda_id['digest'])
        with patch.dict(os.environ, {'MTMD_BACKEND_DEVICE': 'none'}):
            alternate = self.module._identity(cuda, self.queue_dir.resolve(), self.resource)
        with patch.dict(os.environ, {'MTMD_BACKEND_DEVICE': 'CUDA0'}):
            current = self.module._identity(cuda, self.queue_dir.resolve(), self.resource)
        self.assertNotEqual(alternate['digest'], current['digest'])
        client = self.client(cpu, queue_timeout=5)
        self.assertEqual(client.report['device'], 'cpu')
        start = next(row for row in self.events() if row['event'] == 'start')
        args = start['args']
        self.assertEqual(args[args.index('--n-gpu-layers') + 1], '0')
        self.assertEqual(args[args.index('--device') + 1], 'none')
        self.assertEqual(args[args.index('--mmproj-device') + 1], 'none')
        self.assertIn('--no-mmproj-offload', args)
        self.assertIn('--no-kv-offload', args)

    def test_broker_import_failure_is_reported_without_unlimited_respawn(self):
        # An installed runtime can be broken before it even binds IPC. The
        # ordinary unlimited GPU wait must not turn this into infinite retries.
        (self.scripts / "run_hunyuanocr.py").write_text("invalid python !\n")
        started = time.monotonic()
        with self.assertRaisesRegex(GPUQueueError, "exited before admission.*log"):
            self.client()
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.events(), [])

    def test_owner_release_before_exit_does_not_replace_failing_child(self):
        # Model the interpreter shutdown window: IPC ownership is released,
        # but the first child still has no exit status for the join loop.
        real_popen = subprocess.Popen
        children = []

        def launch_releasing_child(command, **kwargs):
            owner = command[command.index("--owner-fd") + 1]
            child = real_popen(
                [sys.executable, "-c",
                 "import os,sys,time; os.close(int(sys.argv[1])); time.sleep(0.3); sys.exit(7)",
                 owner], **kwargs)
            children.append(child)
            return child

        try:
            with patch.object(self.module.subprocess, "Popen", side_effect=launch_releasing_child) as launch:
                with self.assertRaisesRegex(GPUQueueError, "exited before admission.*code 7.*log"):
                    self.client(queue_timeout=2)
                self.assertEqual(launch.call_count, 1)
        finally:
            for child in children:
                child.wait(timeout=3)
        self.assertEqual(self.events(), [])

    def test_unresponsive_discovery_owner_has_bounded_watchdog(self):
        self.module.DISCOVERY_TIMEOUT = 0.2
        body = self.module._identity(self.config, self.queue_dir.resolve(), self.resource)
        directory, _ = self.module._paths(body)
        self.runtime_directories.add(directory)
        owner = self.module._private_file(directory / (body["digest"][:32] + ".owner"), os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(GPUQueueError, "did not accept a membership"):
                self.client()
        finally:
            os.close(owner)
        self.assertEqual(self.events(), [])

    def test_sixteen_independent_processes_race_to_one_broker_and_share_four_slots(self):
        config = dict(self.config, slots=4)
        body = self.module._identity(config, self.queue_dir.resolve(), self.resource)
        directory, _ = self.module._paths(body)
        self.runtime_directories.add(directory)
        config_file = self.root / "client-config.json"
        config_file.write_text(json.dumps(config))
        ready = self.root / "ready"
        ready.mkdir()
        release = self.root / "release"
        code = """
import json, sys, time
from pathlib import Path
from _hunyuan_shared import SharedHunyuanClient
root, page = Path(sys.argv[1]), sys.argv[2]
client = SharedHunyuanClient(json.loads((root / 'client-config.json').read_text()),
                             root / 'queue', 'isolated-fake-gpu', queue_timeout=10)
try:
    (root / 'ready' / page).write_text(json.dumps(client.report))
    deadline = time.monotonic() + 10
    while not (root / 'release').exists():
        if time.monotonic() >= deadline: raise TimeoutError('test barrier')
        time.sleep(0.01)
    result = client.ocr(root / (page + '.png'), 100, 10)
    print(json.dumps(result['choices'][0]['message']['content']))
finally:
    client.close()
"""
        processes = []
        try:
            for index in range(16):
                self.page(f"job{index}")
                process = subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), str(self.root), f"job{index}"],
                                           env=dict(os.environ, PYTHONPATH=str(self.scripts)),
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(process)
            wait_until(lambda: len(list(ready.iterdir())) == 16, timeout=15)
            reports = [json.loads(path.read_text()) for path in ready.iterdir()]
            self.assertEqual(len({report["brokerPid"] for report in reports}), 1)
            self.assertEqual(len({report["serverPid"] for report in reports}), 1)
            release.touch()
            for index, process in enumerate(processes):
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual(json.loads(stdout.strip().splitlines()[-1]), f"job{index}")
        finally:
            release.touch()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
        active = peak = 0
        for event in self.events():
            if event["event"] == "begin": active += 1
            if event["event"] == "end": active -= 1
            peak = max(peak, active)
        self.assertEqual(peak, 4)
        self.assertEqual(active, 0)
        self.assertEqual(sum(event["event"] == "start" for event in self.events()), 1)


    # A llama-server that dies under the broker is started again and the pages it lost are read.
    # Found on M1 (2026-11-20): llama.cpp aborted in its image encoder under load ("CUDA error:
    # an illegal instruction was encountered") on two nodes; the broker ended with it, every page
    # left on that lane failed, and both books on the lane failed (MH1408, MH1442).  The same
    # pages read cleanly on their own.
    def starts(self):
        return sum(row["event"] == "start" for row in self.events())

    def test_a_server_that_dies_mid_book_is_started_again_and_its_pages_read(self):
        # Two books on one server, as on M1's lanes; each has as many pages out as the server has slots.
        (self.root / "crash-times").write_text("1")
        first, second = self.client(queue_timeout=5), self.client(queue_timeout=5)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages = [(first, "p0"), (first, "crash"), (second, "p2"), (second, "p3")]
            futures = [pool.submit(client.ocr, self.page(text), 100, 30) for client, text in pages]
            self.assertEqual([f.result()["choices"][0]["message"]["content"] for f in futures],
                             [text for _, text in pages])
        self.assertEqual(self.starts(), 2)
        self.assertEqual(second.ocr(self.page("after"), 100, 10)["choices"][0]["message"]["content"], "after")

    def test_a_server_that_dies_with_cuda_graphs_on_starts_again_with_them_off(self):
        # On by default; the restart after a crash turns them off, and the page reads.
        (self.root / "crash-with-graphs").touch()
        first, second = self.client(queue_timeout=5), self.client(queue_timeout=5)
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages = [(first, "p0"), (first, "crash"), (second, "p2"), (second, "p3")]
            futures = [pool.submit(client.ocr, self.page(text), 100, 30) for client, text in pages]
            self.assertEqual([f.result()["choices"][0]["message"]["content"] for f in futures],
                             [text for _, text in pages])
        self.assertEqual([row["graphs_off"] for row in self.events() if row["event"] == "start"], [None, "1"])

    def test_a_server_that_keeps_dying_is_given_up_and_its_page_fails(self):
        (self.root / "crash-times").write_text("99")
        client = self.client(queue_timeout=5)
        with self.assertRaises(self.module.SharedHunyuanError):
            client.ocr(self.page("crash"), 100, 60)
        # The first start and MAX_RESTARTS more, then no more.
        wait_until(lambda: self.starts() == 4)
        time.sleep(0.5)
        self.assertEqual(self.starts(), 4)

    def test_a_page_that_fails_on_a_server_still_answering_starts_nothing(self):
        client = self.client(queue_timeout=5)
        started = time.monotonic()
        with self.assertRaises(self.module.SharedHunyuanError):
            client.ocr(self.page("bad"), 100, 30)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(client.ocr(self.page("fine"), 100, 10)["choices"][0]["message"]["content"], "fine")
        self.assertEqual(self.starts(), 1)

    def test_a_dedicated_server_is_started_again_too(self):
        run = importlib.import_module("run_hunyuanocr")
        (self.root / "crash-times").write_text("1")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = run.HunyuanServer(None, self.binary, self.root / "model", self.root / "mmproj", port, 4096)
        try:
            first = server.process.pid
            self.assertEqual(server.ocr(self.page("crash"), 100, 30)["choices"][0]["message"]["content"], "crash")
            self.assertNotEqual(server.process.pid, first)
            self.assertEqual((server.generation, len(server.restarts)), (1, 1))
            self.assertEqual([row["graphs_off"] for row in self.events() if row["event"] == "start"], [None, "1"])
        finally:
            server.close()


class PageSlotTest(unittest.TestCase):
    """A member's page slot is free before its answer goes out.  Found on M1 (2026-11-20, a mass
    OCR run with two books on each lane's server, 16 slots, each client 16 workers): a client sent
    its next page the moment an answer arrived, the broker freed the slot only after sending it,
    and the page was refused ("too many outstanding pages for one shared OCR membership"), which
    failed the book's HunyuanOCR run (MH0925, page 183 of 870)."""

    def test_a_page_sent_the_moment_the_last_answer_arrives_finds_its_slot(self):
        import _hunyuan_shared as shared
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            render = root / "page.png"
            render.write_bytes(b"page")
            body = {"config": {"slots": 1}, "queue_root": str(root / "queue"), "resource": "unit-test",
                    "digest": "unit-test"}
            broker = shared._Broker(body, root, root / "broker.sock")
            broker.ready = True
            broker.members["s"] = None
            broker.request_counts["s"] = 0

            class Server:
                def ocr(self, render, max_tokens, left):
                    return {"text": "page"}
            broker.server = Server()
            worker = threading.Thread(target=broker._worker, daemon=True)
            worker.start()
            here, there = socket.socketpair()
            page = lambda: {"session": "s", "deadline": time.time() + 10, "max_tokens": 100, "render": str(render)}
            seen = []

            def send(connection, payload):
                seen.append(payload["status"])
                if len(seen) == 1:   # the client's next page goes out as soon as it reads this answer
                    try:
                        broker._page(here, page())
                    except shared.GPUQueueError as error:
                        seen.append(str(error))
            try:
                with patch.object(shared, "_send", send):
                    broker._page(here, page())
            finally:
                broker.cancel.set()
                worker.join(2)
                here.close()
                there.close()
            self.assertEqual(seen, ["complete", "complete"])
            self.assertEqual(broker.request_counts["s"], 0)


class LivelockWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "model").touch()
        (self.root / "mmproj").touch()
        self.binary = self.root / "fake-server"
        self.binary.with_suffix(".py").write_text(textwrap.dedent(FAKE).replace("__ROOT__", repr(str(self.root))), encoding="utf-8")
        self.binary.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$0.py\" \"$@\"\n", encoding="utf-8")
        self.binary.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def events(self):
        path = self.root / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def page(self, name):
        path = self.root / f"{name}.png"
        path.write_bytes(name.encode())
        return path

    def test_livelock_with_healthy_models_endpoint_restarts_without_graphs_and_idle_stays_up(self):
        import run_hunyuanocr as run
        (self.root / "livelock-with-graphs").touch()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = run.HunyuanServer(
            None,
            self.binary,
            self.root / "model",
            self.root / "mmproj",
            port,
            4096,
            stall_timeout=0.35,
        )
        try:
            first_pid = server.process.pid
            # Idle for > stall_timeout with 0 inflight requests: must NOT restart.
            time.sleep(0.55)
            self.assertEqual(server.generation, 0)
            self.assertEqual(server.process.pid, first_pid)
            self.assertFalse(server.is_stalled())

            # Request hangs on completion while /v1/models stays 200 OK; watchdog recovers with NO_GRAPHS=1.
            result = server.ocr(self.page("livelock"), 100, 10)
            self.assertEqual(result["choices"][0]["message"]["content"], "livelock")
            self.assertEqual(server.generation, 1)
            self.assertNotEqual(server.process.pid, first_pid)
            events = self.events()
            self.assertEqual([row["graphs_off"] for row in events if row["event"] == "start"], [None, "1"])
            self.assertTrue(any(row["event"] == "livelock" for row in events))
        finally:
            server.close()


class _lease:
    def __init__(self, directory, resource):
        self.queue = GPUQueue(directory, resource)
    def __enter__(self):
        return self.queue.acquire(timeout=5)
    def __exit__(self, *_):
        self.queue.close()


if __name__ == "__main__":
    unittest.main()
