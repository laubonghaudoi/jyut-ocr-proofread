"""CPU-only broker lifecycle tests with independent fake HTTP model processes."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/jyut-ocr-proofread/skills/jyut-ocr-proofread/scripts"
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
log('start', slots=a.parallel, ctx=a.ctx_size)
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
        for name in ("_hunyuan_shared.py", "_gpu_queue.py", "_phase_budget.py", "run_hunyuanocr.py"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        name = "shared_test_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, self.scripts / "_hunyuan_shared.py")
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.module
        spec.loader.exec_module(self.module)
        self.binary = self.root / "fake-server"
        self.binary.write_text("#!" + sys.executable + "\n" + textwrap.dedent(FAKE).replace("__ROOT__", repr(str(self.root))))
        self.binary.chmod(0o700)
        for name in ("model", "mmproj"):
            (self.root / name).write_bytes(b"fake model bytes")
        self.config = {"binary": str(self.binary), "model": str(self.root / "model"),
                       "mmproj": str(self.root / "mmproj"), "model_id": "hunyuanocr",
                       "ctx": 16384, "slots": 2, "identity": "unit-test-source", "max_tokens": 100}
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
        for change in ({"slots": 0}, {"slots": 65}, {"identity": ""}, {"unrecognized": True}):
            with self.assertRaises(GPUQueueError):
                self.module.SharedHunyuanClient(dict(self.config, **change), self.queue_dir, self.resource)
        self.assertEqual(self.events(), [])

    def test_broker_import_failure_is_reported_without_unlimited_respawn(self):
        # An installed runtime can be broken before it even binds IPC. The
        # ordinary unlimited GPU wait must not turn this into infinite retries.
        (self.scripts / "run_hunyuanocr.py").write_text("invalid python !\n")
        started = time.monotonic()
        with self.assertRaisesRegex(GPUQueueError, "exited before admission.*log"):
            self.client()
        self.assertLess(time.monotonic() - started, 3)
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


class _lease:
    def __init__(self, directory, resource):
        self.queue = GPUQueue(directory, resource)
    def __enter__(self):
        return self.queue.acquire(timeout=5)
    def __exit__(self, *_):
        self.queue.close()


if __name__ == "__main__":
    unittest.main()
