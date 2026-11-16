"""The GPU hand-over (scripts/run_ocr_phase.py), the queue's local-model guard
and scripts/ocr_book.py, with fake llama-server, fake Paddle, fake nvidia-smi
and touch-file release/restore commands.  No GPU, no model, no systemctl."""
from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from test_local_ocr_runners import FAKE_PADDLE, FAKE_PADDLE_CORE, FAKE_SERVER, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import _gpu_queue  # noqa: E402
import _phase_budget  # noqa: E402
import run_ocr_phase as phase  # noqa: E402

PY = sys.executable

FAKE_SMI = r'''#!/bin/sh
# Free memory from $FAKE_SMI_FREE_FILE on a 32000 MiB card; like WSL2, no process list.
if [ -z "$FAKE_SMI_FREE_FILE" ] || [ ! -f "$FAKE_SMI_FREE_FILE" ]; then echo "No devices were found"; exit 6; fi
free=$(cat "$FAKE_SMI_FREE_FILE")
case "$*" in
  *--query-gpu*) echo "$((32000 - free)), 32000";;
  *) echo "Not Supported";;
esac
'''

# The fake llama-server, slow to stop (FAKE_STOP_DELAY) and with a pid file each.
SLOW_SERVER = FAKE_SERVER.replace(
    "def stop(signum, frame):\n    record('stopped'); raise SystemExit(0)",
    "def stop(signum, frame):\n    time.sleep(float(os.environ.get('FAKE_STOP_DELAY') or 0)); "
    "record('stopped'); raise SystemExit(0)").replace(
    "record('started')",
    "record('started')\nif os.environ.get('FAKE_PID_DIR'): "
    "(Path(os.environ['FAKE_PID_DIR']) / str(os.getpid())).write_text(str(os.getpid()))")
assert "FAKE_STOP_DELAY" in SLOW_SERVER and "FAKE_PID_DIR" in SLOW_SERVER

# The restore command: how many OCR processes (fake servers, and any pid in
# the pid folder) are alive at that moment, then the server's memory is taken.
RESTORE = r'''
import os, sys
from pathlib import Path
events, free, piddir = sys.argv[1:4]
alive = 0
for path in Path(piddir).glob('*'):
    try:
        pid = int(path.read_text())
        os.kill(pid, 0)
        with open(f'/proc/{pid}/stat') as stat:
            if stat.read().rsplit(')', 1)[1].split()[0] == 'Z':
                continue
    except FileNotFoundError:
        if sys.platform.startswith('linux'):
            continue
    except (OSError, ValueError):
        continue
    alive += 1
with open(events, 'a') as handle:
    handle.write(f'restore alive={alive}\n')
Path(free).write_text('1000')
'''

# A runner that takes the GPU lease of its queue the way the real runners do,
# then asks whether another session could take the shared one meanwhile.
LEASE_RUNNER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _gpu_queue import GPUQueue, GPUQueueTimeout, LocalModelOwnsGPU
out, shared = Path(sys.argv[2]), sys.argv[3]
resource = os.environ.get('OCR_GPU_RESOURCE', 'local-gpu')
lease = GPUQueue(None, resource)
lease.acquire(timeout=5)
result = {'root': str(lease.root), 'acquired': lease.acquired, 'window': os.environ.get('OCR_PHASE_WINDOW')}
other = GPUQueue(shared, resource)
try:
    other.acquire(timeout=0.5, local_model_guard=False)
    result['otherWrapper'] = 'acquired'
except GPUQueueTimeout:
    result['otherWrapper'] = 'blocked'
finally:
    other.close()
os.environ.pop('OCR_PHASE_WINDOW')
runner = GPUQueue(shared, resource)
try:
    runner.acquire(timeout=0.5)
    result['otherRunner'] = 'acquired'
except LocalModelOwnsGPU as error:
    result['otherRunner'] = str(error)
except GPUQueueTimeout:
    result['otherRunner'] = 'blocked'
finally:
    runner.close()
lease.close()
with open(os.environ['OCR_PHASE_PROGRESS'], 'a') as handle:
    handle.write('1\n')
out.write_text(json.dumps(result))
'''

# A runner that leaves behind a detached process holding its GPU lease, which
# it tells nobody about: an OCR model the hand-over cannot see or stop.
LEAK_RUNNER = r'''
import os, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _gpu_queue import GPUQueue
gate, pidfile = sys.argv[2], sys.argv[3]
lease = GPUQueue(None, os.environ.get('OCR_GPU_RESOURCE', 'local-gpu'))
lease.acquire(timeout=5)
child = subprocess.Popen([sys.executable, '-c',
    'import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nwhile not p.exists(): time.sleep(0.05)', gate],
    pass_fds=lease.pass_fds, start_new_session=True,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
Path(pidfile).write_text(str(child.pid))
lease.close()
'''

# A runner that sleeps with a child of its own, reporting both pids.
SLEEP_RUNNER = r'''
import os, signal, subprocess, sys, time
from pathlib import Path
if os.environ.get('IGNORE_TERM'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}')
time.sleep(600)
'''


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f'/proc/{pid}/stat', encoding='utf-8') as handle:
            return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return True


class HandoverBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        smi = self.bin / 'nvidia-smi'
        smi.write_text(FAKE_SMI)
        smi.chmod(0o755)
        self.free = self.base / 'free'
        self.free.write_text('1000')
        self.events = self.base / 'events'
        self.pids = self.base / 'pids'
        self.pids.mkdir()
        self.state = self.base / 'state'
        self.shared = self.base / 'shared-queue'
        runtime = self.base / 'run'
        runtime.mkdir(mode=0o700)
        self.restore_script = self.base / 'restore.py'
        self.restore_script.write_text(RESTORE)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('OCR_PHASE_', 'OCR_GPU_', 'OCR_HUNYUAN_', 'HUNYUAN_', 'PADDLEOCR_',
                                       'OCR_PADDLE_', 'PADDLE_', 'OCR_CPU_'))}
        env.update(PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                   FAKE_SMI_FREE_FILE=str(self.free), FAKE_LOG=str(self.events),
                   FAKE_PID_DIR=str(self.pids), OCR_PHASE_STATE_DIR=str(self.state),
                   OCR_GPU_QUEUE_DIR=str(self.shared), OCR_GPU_RESOURCE='offline-test-gpu',
                   XDG_RUNTIME_DIR=str(runtime), TMPDIR=str(self.base))
        env.update(OCR_PHASE_RELEASE_CMD=self.release_cmd(), OCR_PHASE_RESTORE_CMD=self.restore_cmd())
        self.env = env
        self.processes: list[subprocess.Popen] = []
        self.addCleanup(self.reap)

    def reap(self):
        for process in self.processes:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.kill()
            with contextlib.suppress(Exception):
                process.communicate(timeout=5)
        for path in self.pids.glob('*'):
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(path.read_text()), signal.SIGKILL)

    def release_cmd(self, free: int = 30000) -> str:
        return f"echo release >> '{self.events}'; echo {free} > '{self.free}'"

    def restore_cmd(self) -> str:
        return f"'{PY}' '{self.restore_script}' '{self.events}' '{self.free}' '{self.pids}'"

    def lines(self) -> list[str]:
        return self.events.read_text().splitlines() if self.events.exists() else []

    def phase(self, runner, *options, env=None, timeout=60):
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--kill-grace', '2', '--window-grace', '3',
                   '--release-settle', '1', *options, '--', *runner]
        return subprocess.run(command, env=env or self.env, capture_output=True, text=True,
                              timeout=timeout)

    def status(self) -> dict:
        return json.loads((self.state / 'status.json').read_text())

    def wait_for(self, predicate, seconds=20.0, message=''):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(message or 'condition not reached')

    def fake_book(self, pages=3) -> Path:
        book = self.base / 'book'
        renders = book / 'renders'
        renders.mkdir(parents=True)
        for page in range(1, pages + 1):
            (renders / f'page-{page:04d}.png').write_text(f'scan {page}')
        binary = self.base / 'fake-llama-server'
        binary.write_text('#!' + PY + '\n' + textwrap.dedent(SLOW_SERVER))
        binary.chmod(0o755)
        (self.base / 'model.gguf').write_bytes(b'offline fake model')
        (self.base / 'mmproj.gguf').write_bytes(b'offline fake projector')
        package = self.base / 'fake-package'
        package.mkdir()
        (package / 'paddleocr.py').write_text(textwrap.dedent(FAKE_PADDLE))
        (package / 'paddle').mkdir()
        (package / 'paddle' / '__init__.py').write_text(textwrap.dedent(FAKE_PADDLE_CORE))
        # Paddle as the installer sets it up on a GPU host: the CUDA build seeing
        # one card, and the pinned weight folders (the runner never downloads).
        dist = self.base / 'dist' / 'paddlepaddle_gpu-3.3.1.dist-info'
        dist.mkdir(parents=True)
        (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: paddlepaddle-gpu\nVersion: 3.3.1\n')
        weights = {}
        for key, name, model_name, file in (
                ('PADDLE_VL_MODEL_DIR', 'PaddleOCR-VL-1.6', 'PaddleOCR-VL-1.6-0.9B', 'model.safetensors'),
                ('PADDLE_LAYOUT_MODEL_DIR', 'PP-DocLayoutV3', 'PP-DocLayoutV3', 'inference.pdiparams')):
            folder = self.base / 'paddle-models' / name
            folder.mkdir(parents=True)
            (folder / 'inference.yml').write_text(f'Global:\n  model_name: {model_name}\n')
            (folder / file).write_bytes(b'offline fake weights')
            weights[key] = str(folder)
        self.env.update(HUNYUAN_MODEL=str(self.base / 'model.gguf'),
                        HUNYUAN_MMPROJ=str(self.base / 'mmproj.gguf'),
                        HUNYUAN_LLAMA_SERVER=str(binary),
                        PYTHONPATH=os.pathsep.join([str(package), str(dist.parent)]),
                        OCR_HUNYUAN_SLOTS='1', FAKE_CUDA_BUILD='1', FAKE_CUDA_DEVICES='1',
                        PADDLE_PDX_CACHE_HOME=str(self.base / 'paddlex-cache'), **weights)
        return book

    def hunyuan(self, book: Path, *extra) -> list[str]:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        return [PY, str(SCRIPTS / 'run_hunyuanocr.py'), str(book / 'renders'), str(book / 'ocr-hunyuan'),
                '--report', str(self.base / 'hunyuan-report.json'), '--port', str(port), *extra]


class NeedAndPolicyTests(unittest.TestCase):
    def test_hunyuan_need_reproduces_the_measured_peaks(self):
        for slots, measured in ((1, 5036), (2, 6572), (4, 9644), (8, 15788), (16, 28076)):
            self.assertEqual(phase.hunyuan_mib(slots, 16384), measured)

    def test_the_need_is_the_largest_engine_of_the_call(self):
        with mock.patch.dict(os.environ, {'OCR_HUNYUAN_SLOTS': '16'}, clear=True):
            self.assertEqual(phase.engine_need(['python3', 'scripts/ocr_book.py', 'BOOK'])[0], 28076)
            self.assertEqual(phase.engine_need(['python3', 'scripts/run_paddleocr_vl.py', 'R', 'O'])[0], 12288)
            self.assertEqual(phase.engine_need(['python3', 'run_hunyuanocr_second_look.py', 'R', 'A', 'B',
                                                '--server-slots', '2'])[0], 6572)
        with mock.patch.dict(os.environ, {'OCR_HUNYUAN_SLOTS': '1'}, clear=True):
            self.assertEqual(phase.engine_need(['python3', 'ocr_book.py', 'BOOK'])[0], 12288)
            self.assertEqual(phase.engine_need(['python3', 'ocr_book.py', 'BOOK', '--single-engine'])[0], 5036)
            self.assertEqual(phase.engine_need(['true'])[0], phase.DEFAULT_REQUIRED_MIB)
        if sys.platform != 'darwin':
            with mock.patch.dict(os.environ, {'OCR_HUNYUAN_DEVICE': 'cpu'}, clear=True):
                self.assertEqual(phase.engine_need(['run_hunyuanocr.py', 'R', 'O'])[0], 0)
                self.assertEqual(phase.engine_need(['ocr_book.py', 'B', '--single-engine'])[0], 0)
            with mock.patch.dict(os.environ, {'HUNYUAN_ENDPOINT': 'http://127.0.0.1:1'}, clear=True):
                self.assertEqual(phase.engine_need(['run_hunyuanocr.py', 'R', 'O'])[0], 0)
            # Paddle's device as the installer writes it (OCR_PADDLE_DEVICE), or the runner's --device.
            with mock.patch.dict(os.environ, {'OCR_PADDLE_DEVICE': 'cpu', 'OCR_HUNYUAN_SLOTS': '1'}, clear=True):
                self.assertEqual(phase.engine_need(['run_paddleocr_vl.py', 'R', 'O'])[0], 0)
                self.assertEqual(phase.engine_need(['ocr_book.py', 'BOOK'])[0], 5036)
            with mock.patch.dict(os.environ, {'OCR_PADDLE_DEVICE': 'gpu:0'}, clear=True):
                self.assertEqual(phase.engine_need(['run_paddleocr_vl.py', 'R', 'O', '--device', 'cpu'])[0], 0)
                self.assertEqual(phase.engine_need(['run_paddleocr_vl.py', 'R', 'O'])[0], 12288)

    def test_macos_free_memory_from_vm_stat(self):
        text = textwrap.dedent('''\
            Mach Virtual Memory Statistics: (page size of 16384 bytes)
            Pages free:                               65536.
            Pages active:                            900000.
            Pages inactive:                          131072.
            Pages speculative:                         1024.
            Pages wired down:                        200000.
            ''')
        self.assertEqual(phase.parse_vm_stat(text), (65536 + 131072 + 1024) * 16384 // (1024 * 1024))
        self.assertIsNone(phase.parse_vm_stat('nothing here'))

    def test_the_first_budget_question_needs_the_startup_time(self):
        with mock.patch.dict(os.environ, {'OCR_PHASE_DEADLINE': str(time.time() + 30),
                                          'OCR_PHASE_STARTUP_SECONDS': '60'}):
            budget = _phase_budget.Budget()
            self.assertFalse(budget.allows_next_page())  # before loading: 30 s < 60 s
        with mock.patch.dict(os.environ, {'OCR_PHASE_DEADLINE': str(time.time() + 30),
                                          'OCR_PHASE_STARTUP_SECONDS': '20'}):
            budget = _phase_budget.Budget()
            self.assertTrue(budget.allows_next_page())
            budget.record(5.0)
            self.assertTrue(budget.allows_next_page())  # afterwards only the longest page counts
        with mock.patch.dict(os.environ, {'OCR_PHASE_STARTUP_SECONDS': '999'}):
            os.environ.pop('OCR_PHASE_DEADLINE', None)
            self.assertTrue(_phase_budget.Budget().allows_next_page())

    def test_child_records_name_live_processes_with_their_start(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'children'
            with mock.patch.dict(os.environ, {'OCR_PHASE_CHILDREN': str(path)}):
                _phase_budget.record_child('broker', os.getpid())
            ((kind, pid, started),) = _phase_budget.read_children(path)
            self.assertEqual((kind, pid), ('broker', os.getpid()))
            self.assertEqual(started, _phase_budget.process_start_time(os.getpid()))


class LocalModelGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'
        self.state.mkdir()
        self.queue = Path(self.temp.name) / 'queue'
        patcher = mock.patch.dict(os.environ, {'OCR_PHASE_STATE_DIR': str(self.state),
                                               'OCR_PHASE_RELEASE_CMD': 'do-not-run'})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ('OCR_PHASE_WINDOW', 'OCR_PHASE_SWAP'):
            os.environ.pop(key, None)

    def open_window(self, window_id: str) -> int:
        fd = os.open(self.state / 'window.lock', os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        (self.state / 'window.json').write_text(json.dumps({'id': window_id}))
        return fd

    def test_a_lease_outside_an_open_window_defers_before_taking_a_ticket(self):
        lease = _gpu_queue.GPUQueue(self.queue, 'gpu')
        with self.assertRaises(_gpu_queue.LocalModelOwnsGPU) as raised:
            lease.acquire(timeout=1)
        self.assertIsInstance(raised.exception, _gpu_queue.GPUQueueTimeout)
        self.assertIn('run through run_ocr_phase.py', str(raised.exception))
        self.assertEqual(lease.status, 'deferred-local-model')
        self.assertFalse(self.queue.exists())

    def test_inside_the_open_window_never_and_no_tenant_the_lease_is_granted(self):
        fd = self.open_window('w1')
        os.environ['OCR_PHASE_WINDOW'] = 'w1'
        _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1).close()
        os.environ['OCR_PHASE_WINDOW'] = 'w0'  # a window that is over
        with self.assertRaises(_gpu_queue.LocalModelOwnsGPU):
            _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1)
        os.environ['OCR_PHASE_SWAP'] = 'never'
        _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1).close()
        os.environ.pop('OCR_PHASE_SWAP')
        os.environ['OCR_PHASE_WINDOW'] = 'w1'
        os.close(fd)  # the owner is gone: the window is closed whatever window.json says
        with self.assertRaises(_gpu_queue.LocalModelOwnsGPU):
            _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1)
        os.environ.pop('OCR_PHASE_RELEASE_CMD')
        _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1).close()
        # The wrapper's own lease is taken before its window opens.
        os.environ['OCR_PHASE_RELEASE_CMD'] = 'do-not-run'
        _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1, local_model_guard=False).close()

    def test_live_ticket_count_sees_an_inherited_lease(self):
        lease = _gpu_queue.GPUQueue(self.queue, 'gpu').acquire(timeout=1, local_model_guard=False)
        child = subprocess.Popen([PY, '-c', 'import time; time.sleep(30)'], pass_fds=lease.pass_fds)
        try:
            lease.close()
            self.assertEqual(_gpu_queue.live_ticket_count(self.queue), 1)
        finally:
            child.kill()
            child.wait()
        self.assertEqual(_gpu_queue.live_ticket_count(self.queue), 0)
        self.assertEqual(_gpu_queue.live_ticket_count(self.queue / 'missing'), 0)


class RunnerGuardTests(HandoverBase):
    def test_runners_outside_a_window_defer_with_75_and_load_nothing(self):
        book = self.fake_book()
        for mode in ('dedicated', 'shared'):
            with self.subTest(mode=mode):
                result = subprocess.run(self.hunyuan(book, '--server-mode', mode), env=self.env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
                self.assertIn('GPU belongs to the local model; run through run_ocr_phase.py', result.stdout)
                self.assertNotIn('started', self.lines())
                # The summary says what the 75 means here (CD-R2).
                self.assertIn(_gpu_queue.LOCAL_MODEL_SUMMARY, result.stdout.splitlines())
        paddle = subprocess.run([PY, str(SCRIPTS / 'run_paddleocr_vl.py'), str(book / 'renders'),
                                 str(book / 'ocr-paddle'), '--report', str(self.base / 'p.json')],
                                env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(paddle.returncode, 75, paddle.stdout + paddle.stderr)
        self.assertIn('GPU belongs to the local model', paddle.stdout)
        self.assertIn(_gpu_queue.LOCAL_MODEL_SUMMARY, paddle.stdout.splitlines())
        self.assertFalse(any(line.startswith('started') for line in self.lines()))
        if sys.platform != 'darwin':
            # Paddle on the CPU (its own queue resource) needs none of the
            # model's GPU memory, so it is not deferred.
            cpu = subprocess.run([PY, str(SCRIPTS / 'run_paddleocr_vl.py'), str(book / 'renders'),
                                  str(book / 'ocr-paddle'), '--report', str(self.base / 'p.json')],
                                 env=dict(self.env, OCR_PADDLE_DEVICE='cpu'), capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(cpu.returncode, 0, cpu.stdout + cpu.stderr)
            self.assertNotIn('GPU belongs to the local model', cpu.stdout)
            self.assertEqual(len(list((book / 'ocr-paddle').glob('page-*.json'))), 3)
            self.assertEqual(json.loads((self.base / 'p.json').read_text())['execution']['queueResource'],
                             'local-cpu')

    def test_a_runner_with_less_time_than_its_startup_defers_before_loading(self):
        book = self.fake_book()
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        env.update(OCR_PHASE_DEADLINE=str(time.time() + 60), OCR_PHASE_STARTUP_SECONDS='999')
        for mode in ('dedicated', 'shared'):
            with self.subTest(mode=mode):
                result = subprocess.run(self.hunyuan(book, '--server-mode', mode), env=env,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
                self.assertIn('phase deadline reached before model loading', result.stdout)
                self.assertNotIn('started', self.lines())
        env['OCR_PHASE_STARTUP_SECONDS'] = '5'
        result = subprocess.run(self.hunyuan(book, '--server-mode', 'dedicated'), env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class WindowTests(HandoverBase):
    def test_the_runner_gets_its_own_queue_while_other_sessions_stay_out(self):
        out = self.base / 'lease.json'
        result = self.phase([PY, '-c', LEASE_RUNNER, str(SCRIPTS), str(out), str(self.shared)],
                            '--max-seconds', '120', '--restore-reserve', '20')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        seen = json.loads(out.read_text())
        self.assertTrue(seen['acquired'])
        self.assertEqual(Path(seen['root']), (self.state / 'private-queue').absolute())
        self.assertTrue(seen['window'])
        self.assertEqual(seen['otherWrapper'], 'blocked')
        self.assertIn('run through run_ocr_phase.py', seen['otherRunner'])
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        self.assertEqual(self.status()['state'], 'restored')
        self.assertEqual(_gpu_queue.live_ticket_count(self.state / 'private-queue'), 0)
        # The window is closed: the shared queue is free again.
        self.wait_for(lambda: _gpu_queue.live_ticket_count(self.shared) == 0, 10)

    def test_a_real_runner_seals_the_book_inside_the_window(self):
        book = self.fake_book()
        for mode in ('dedicated', 'shared'):
            with self.subTest(mode=mode):
                self.events.unlink(missing_ok=True)
                out = book / 'ocr-hunyuan'
                for path in out.glob('*') if out.exists() else ():
                    path.unlink()
                result = self.phase(self.hunyuan(book, '--server-mode', mode),
                                    '--max-seconds', '120', '--restore-reserve', '30')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads((self.base / 'hunyuan-report.json').read_text())
                self.assertEqual(report['counts']['completed'], 3)
                self.assertIn(str(self.state / 'private-queue'), json.dumps(report['gpuQueue']))
                lines = self.lines()
                self.assertEqual(lines[0], 'release')
                self.assertEqual(lines[-1], 'restore alive=0')
                self.assertLess(lines.index('stopped'), lines.index('restore alive=0'))

    def test_restore_waits_for_the_detached_broker_to_drain(self):
        book = self.fake_book()
        env = dict(self.env, FAKE_STOP_DELAY='2')
        result = self.phase(self.hunyuan(book, '--server-mode', 'shared'), '--max-seconds', '120',
                            '--restore-reserve', '30', '--window-grace', '10', env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.lines()
        # nvidia-smi lists no process (as on WSL2): the lease is the barrier.
        self.assertEqual(lines[-1], 'restore alive=0', lines)
        self.assertLess(lines.index('stopped'), lines.index('restore alive=0'))
        report = json.loads((self.base / 'hunyuan-report.json').read_text())
        self.assertEqual(report['gpuQueue']['mode'], 'shared-local-fifo')

    def test_a_wedged_broker_is_killed_before_the_restore(self):
        book = self.fake_book()
        server = self.base / 'fake-llama-server'
        server.write_text(server.read_text().replace(
            "signal.signal(signal.SIGTERM, stop)", "signal.signal(signal.SIGTERM, signal.SIG_IGN)"))
        result = self.phase(self.hunyuan(book, '--server-mode', 'shared'), '--max-seconds', '120',
                            '--restore-reserve', '40')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('stopping them', result.stdout)
        self.assertEqual(self.lines()[-1], 'restore alive=0', self.lines())

    def test_never_restores_while_an_ocr_model_is_resident(self):
        gate, pidfile = self.base / 'gate', self.base / 'leak.pid'
        result = self.phase([PY, '-c', LEAK_RUNNER, str(SCRIPTS), str(gate), str(pidfile)],
                            '--max-seconds', '60', '--restore-reserve', '15', '--restore-horizon', '60')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn('may still be resident', result.stderr)
        self.assertEqual(self.lines(), ['release'])
        leak = int(pidfile.read_text())
        (self.pids / 'leak').write_text(str(leak))
        self.assertTrue(alive(leak))
        time.sleep(1.0)
        self.assertEqual(self.lines(), ['release'])  # the watchdog waits too
        gate.touch()
        self.wait_for(lambda: 'restore alive=0' in self.lines(), 30, f'no restore: {self.lines()}')
        self.wait_for(lambda: self.status()['state'] == 'restored', 10)

    def test_more_memory_needed_than_the_release_frees_restores_at_once(self):
        marker = self.base / 'ran'
        env = dict(self.env, OCR_PHASE_RELEASE_CMD=self.release_cmd(free=4000))
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15', env=env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('only 4000 MiB free after the release, 12288 MiB needed', result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])


class BusyTests(HandoverBase):
    def test_a_busy_gpu_queue_gives_76_and_touches_nothing(self):
        holder = _gpu_queue.GPUQueue(self.shared, 'offline-test-gpu')
        holder.acquire(timeout=1, local_model_guard=False)
        try:
            result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15')
        finally:
            holder.close()
        self.assertEqual(result.returncode, 76, result.stdout + result.stderr)
        self.assertIn('RESULT exit=76 GPU BUSY', result.stdout)
        self.assertIn('nothing was stopped', result.stderr)
        self.assertEqual(self.lines(), [])

    def test_a_second_hand_over_gives_76(self):
        self.state.mkdir()
        fd = os.open(self.state / 'window.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15')
        finally:
            os.close(fd)
        self.assertEqual(result.returncode, 76, result.stdout + result.stderr)
        self.assertIn('another OCR hand-over is running', result.stderr)
        self.assertEqual(self.lines(), [])

    def test_a_deferral_with_no_progress_is_76_not_75(self):
        result = self.phase([PY, '-c', 'raise SystemExit(75)'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        self.assertEqual(result.returncode, 76, result.stdout + result.stderr)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        # The server was stopped and restored for it: the RESULT line says so (CD-R3).
        result_line = next(line for line in result.stdout.splitlines() if 'RESULT' in line)
        self.assertIn('the server was stopped for it and is back up', result_line)
        self.assertNotIn('nothing was stopped', result_line)
        # wait_for_run.py and the installer say the same (CD-R8).
        log = self.base / 'phase.log'
        log.write_text(result.stdout + result.stderr)
        wait = subprocess.run([PY, str(SCRIPTS / 'wait_for_run.py'), str(self.base), str(log), '--timeout', '5'],
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn('no page was sealed in the OCR window', wait.stdout, wait.stdout + wait.stderr)
        self.assertIn('the local model was stopped for it and is back up', wait.stdout)
        self.assertNotIn('nothing was stopped', wait.stdout)
        import _setup_ocr
        message = str(_setup_ocr.handover_failure(76, {}))
        self.assertIn('GPU is busy', message)
        self.assertIn('says whether the local model was stopped', message)
        self.assertNotIn('nothing was stopped', message)
        progress = f'import os; open(os.environ["OCR_PHASE_PROGRESS"], "a").write("4\\n"); raise SystemExit(75)'
        result = self.phase([PY, '-c', progress], '--max-seconds', '60', '--restore-reserve', '15')
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)


class RoundTests(HandoverBase):
    """GH-13: an agent harness counts identical tool calls in a row as a
    loop, and a book takes many 75s in a row.  The RESULT line of a 75 or a
    76 names the next call with --round N+1, which changes nothing else."""

    def next_call(self, result) -> list[str]:
        line = next(line for line in result.stdout.splitlines() if 'RESULT' in line)
        self.assertIn('; next call: ', line)
        return shlex.split(line.split('; next call: ', 1)[1])

    def test_the_wrapper_names_its_next_call_with_the_next_round(self):
        progress = 'import os; open(os.environ["OCR_PHASE_PROGRESS"], "a").write("1\\n"); raise SystemExit(75)'
        runner = [PY, '-c', progress]
        result = self.phase(runner, '--max-seconds', '60', '--restore-reserve', '15')
        self.assertEqual(result.returncode, 75, result.stdout + result.stderr)
        command = self.next_call(result)
        self.assertEqual(command[:2], [PY, str(SCRIPTS / 'run_ocr_phase.py')])
        self.assertEqual(command[command.index('--round') + 1], '2')
        self.assertEqual(command[command.index('--round') + 2:], ['--', *runner])
        self.assertIn('--max-seconds', command[:command.index('--round')])
        # The named call runs as this one did, and names round 3, once.
        again = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(again.returncode, 75, again.stdout + again.stderr)
        command = self.next_call(again)
        self.assertEqual(command.count('--round'), 1)
        self.assertEqual(command[command.index('--round') + 1], '3')
        # A 76 names it too; a call that is done names none.
        holder = _gpu_queue.GPUQueue(self.shared, 'offline-test-gpu')
        holder.acquire(timeout=1, local_model_guard=False)
        try:
            busy = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15', '--round', '7')
        finally:
            holder.close()
        self.assertEqual(busy.returncode, 76, busy.stdout + busy.stderr)
        command = self.next_call(busy)
        self.assertEqual(command[command.index('--round'):], ['--round', '8', '--', 'true'])
        done = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15')
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn('next call', done.stdout)

    def test_ocr_book_run_directly_names_its_next_call(self):
        import ocr_book

        def call(*argv, env=None, meaning=None):
            def run(arguments):
                if meaning:
                    arguments.result_meaning = meaning
                return 75
            out = io.StringIO()
            clean = {key: value for key, value in os.environ.items()
                     if key not in (_gpu_queue.WINDOW_ENV, _phase_budget.PROGRESS_ENV)}
            with mock.patch.object(ocr_book, 'run', side_effect=run), \
                    mock.patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self.assertEqual(ocr_book.main(list(argv)), 75)
            return out.getvalue().splitlines()[-1]

        line = call('BOOK', '--max-seconds', '50', '--round', '4')
        self.assertIn('RESULT exit=75 INCOMPLETE', line)
        command = shlex.split(line.split('; next call: ', 1)[1])
        self.assertEqual(command, [sys.executable, str(SCRIPTS / 'ocr_book.py'), 'BOOK', '--max-seconds', '50',
                                   '--round', '5'])
        # Under run_ocr_phase.py the wrapper names the next call; a 75 that
        # says to use the hand-over names that command instead.
        self.assertNotIn('next call', call('BOOK', env={_gpu_queue.WINDOW_ENV: 'w'}))
        self.assertNotIn('next call', call('BOOK', env={_phase_budget.PROGRESS_ENV: '/p'}))
        line = call('BOOK', '--round', '2', meaning='INCOMPLETE - use run_ocr_phase.py')
        self.assertNotIn('next call', line)
        arguments = ocr_book.build_parser().parse_args(['BOOK', '--round', '2'])
        arguments.argv = ['BOOK', '--round', '2']
        self.assertEqual(shlex.split(ocr_book.hand_over_command(arguments))[-2:], [str(SCRIPTS / 'ocr_book.py'), 'BOOK'])


class SwapPolicyTests(HandoverBase):
    def test_swap_is_decided_on_every_platform(self):
        unknown = dict(self.env, FAKE_SMI_FREE_FILE=str(self.base / 'no-such-file'))
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15', env=unknown)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('cannot be read', result.stdout)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        self.events.unlink()
        never = dict(unknown, OCR_PHASE_SWAP='never')
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15', env=never)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.lines(), [])
        if sys.platform != 'darwin':
            cpu = dict(unknown, OCR_HUNYUAN_DEVICE='cpu')
            result = self.phase(['python3', 'scripts/ocr_book.py', 'BOOK', '--single-engine'],
                                '--max-seconds', '60', env=cpu)
            self.assertIn('the engines use no GPU memory -> no swap needed', result.stdout)
            self.assertEqual(self.lines(), [])
        self.free.write_text('30000')
        roomy = dict(self.env)
        result = self.phase(['true'], '--max-seconds', '60', env=roomy)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('no swap needed', result.stdout)
        self.assertEqual(self.lines(), [])
        always = dict(roomy, OCR_PHASE_SWAP='always')
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15', env=always)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])

    def test_without_a_local_model_the_runner_runs_as_it_is(self):
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        result = self.phase([PY, '-c', 'import os; print("window", os.environ.get("OCR_PHASE_WINDOW"))'],
                            env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('window None', result.stdout)
        refused = self.phase(['true'], '--force-swap', env=env)
        self.assertEqual(refused.returncode, 2)

    def test_the_budget_refusals_come_before_anything_stops(self):
        result = self.phase(['true'], '--force-swap', '--max-seconds', '0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('OCR_PHASE_UNLIMITED_SHELL', result.stderr)
        result = self.phase(['true'], '--force-swap')
        self.assertEqual(result.returncode, 2)
        self.assertIn('needs a time budget', result.stderr)
        result = self.phase(['true'], '--force-swap', '--max-seconds', '20')
        self.assertEqual(result.returncode, 2)
        self.assertIn('must exceed', result.stderr)
        result = self.phase(['python3', 'x.py', '--gpu-queue-dir', '/q'], '--force-swap', '--max-seconds', '60')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.lines(), [])


class BudgetTests(HandoverBase):
    def test_the_call_never_outlives_its_budget(self):
        marker = self.base / 'runner'
        slow_once = (f"if [ -f '{self.base}/slow-done' ]; then {self.restore_cmd()}; "
                     f"else touch '{self.base}/slow-done'; sleep 60; fi")
        env = dict(self.env, IGNORE_TERM='1', OCR_PHASE_RESTORE_CMD=slow_once)
        started = time.monotonic()
        result = self.phase([PY, '-c', SLEEP_RUNNER, str(marker)], '--max-seconds', '14',
                            '--restore-reserve', '8', '--restore-horizon', '60', env=env)
        took = time.monotonic() - started
        self.assertLessEqual(took, 14 + 1.5, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        runner, child = (int(pid) for pid in marker.read_text().split())
        self.assertFalse(alive(runner))
        self.assertFalse(alive(child))
        # The watchdog carries on and brings the server back.
        self.wait_for(lambda: 'restore alive=0' in self.lines(), 30, f'no restore: {self.lines()}')
        self.wait_for(lambda: self.status()['state'] == 'restored', 10)

    def test_the_default_reserve_counts_every_wait_and_shrinks_with_measured_restores(self):
        arguments = phase.build_parser().parse_args(['--', 'true'])
        arguments.restore_reserve = None
        self.state.mkdir()
        self.assertEqual(phase.reserve_seconds(arguments, self.state, True),
                         phase.WIND_DOWN + phase.KILL_GRACE + phase.WINDOW_GRACE + phase.KILL_SETTLE
                         + phase.DEFAULT_RESTORE_ESTIMATE)
        for seconds in (4.0, 5.0):
            phase.record_restore_time(self.state, seconds)
        self.assertEqual(phase.restore_estimate(self.state), 15.0)
        self.assertLess(phase.reserve_seconds(arguments, self.state, True), 60)
        self.assertEqual(phase.reserve_seconds(arguments, self.state, False),
                         phase.WIND_DOWN + phase.KILL_GRACE + phase.WINDOW_GRACE + phase.KILL_SETTLE)


    def test_slow_restores_never_grow_the_reserve_past_the_budget(self):
        # CD-R13: one unbudgeted 400 s restore made every 540 s call exit 2
        # ("must exceed the 640s kept"), and nothing ever lowered it again.
        phase.private_directory(self.state)
        phase.record_restore_time(self.state, 4.0)
        phase.record_restore_time(self.state, 400.0, budgeted=False)
        timings = json.loads((self.state / 'timings.json').read_text())
        self.assertEqual((timings['restoreSeconds'], timings['unbudgetedRestoreSeconds']), ([4.0], [400.0]))
        self.assertEqual(phase.restore_estimate(self.state), 15.0)
        phase.record_restore_time(self.state, 3.0, budgeted=False)  # no higher than the estimate: counted
        self.assertEqual(json.loads((self.state / 'timings.json').read_text())['restoreSeconds'], [4.0, 3.0])
        # Slow budgeted restores (or timings from before the cap) are capped.
        (self.state / 'timings.json').write_text(json.dumps({'restoreSeconds': [400.0]}))
        self.assertEqual(phase.restore_estimate(self.state), phase.RESTORE_ESTIMATE_CAP)
        self.assertEqual(phase.restore_estimate(self.state, 540), 540 * phase.RESTORE_BUDGET_SHARE)
        self.assertEqual(phase.restore_estimate(self.state, 1200), phase.RESTORE_ESTIMATE_CAP)
        (self.state / 'timings.json').write_text('{}')
        self.assertEqual(phase.restore_estimate(self.state, 100), phase.DEFAULT_RESTORE_ESTIMATE)
        (self.state / 'timings.json').write_text(json.dumps({'restoreSeconds': [400.0]}))
        result = self.phase([PY, '-c', 'print("ocr")'], '--max-seconds', '540')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])

    def test_restore_times_count_only_under_the_ready_probe_setting_they_were_measured_with(self):
        # E9-R2: health-only restores of 4 s kept a 15 s reserve after
        # OCR_PHASE_READY_PROBE was turned on for a model that needs 40 s
        # before it answers: every probed restore ran out of time.
        phase.private_directory(self.state)
        probe = 'http://127.0.0.1:9/v1/chat/completions'
        phase.record_restore_time(self.state, 4.0)
        self.assertEqual(phase.restore_estimate(self.state, 540), 15.0)
        self.assertEqual(phase.restore_estimate(self.state, 540, probe), phase.DEFAULT_RESTORE_ESTIMATE)
        arguments = phase.build_parser().parse_args(['--max-seconds', '540', '--', 'true'])
        arguments.restore_reserve = None
        waits = phase.WIND_DOWN + phase.KILL_GRACE + phase.WINDOW_GRACE + phase.KILL_SETTLE
        self.assertEqual(phase.reserve_seconds(arguments, self.state, True), waits + 15.0)
        arguments.ready = (probe, None)
        self.assertEqual(phase.reserve_seconds(arguments, self.state, True), waits + phase.DEFAULT_RESTORE_ESTIMATE)
        # The first probed restore starts the probe's own list.
        phase.record_restore_time(self.state, 40.0, probe=probe)
        timings = json.loads((self.state / 'timings.json').read_text())
        self.assertEqual((timings['restoreSeconds'], timings['restoreProbe']), ([40.0], probe))
        self.assertEqual(phase.restore_estimate(self.state, 540, probe), 65.0)
        self.assertEqual(phase.restore_estimate(self.state, 540), phase.DEFAULT_RESTORE_ESTIMATE)
        # Turned off again: the health-only list starts over.
        phase.record_restore_time(self.state, 5.0, budgeted=False)
        timings = json.loads((self.state / 'timings.json').read_text())
        self.assertEqual(timings['restoreSeconds'], [5.0])
        self.assertNotIn('restoreProbe', timings)
        self.assertNotIn('unbudgetedRestoreSeconds', timings)
        # local-ocr.md names the key, so nobody deletes timings.json by hand.
        self.assertIn('`restoreProbe`', (SCRIPTS.parent / 'references' / 'local-ocr.md').read_text())


class WatchdogTests(HandoverBase):
    def test_the_watchdog_restores_after_the_wrapper_is_killed(self):
        marker = self.base / 'runner'
        notice = self.base / 'notice'
        env = dict(self.env, OCR_PHASE_NOTIFY_CMD=f'echo "$OCR_PHASE_NOTICE" >> {notice}')
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--max-seconds', '120', '--restore-reserve', '20',
                   '--', PY, '-c', SLEEP_RUNNER, str(marker)]
        wrapper = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.processes.append(wrapper)
        self.wait_for(marker.exists, 20, 'the runner did not start')
        runner, child = (int(pid) for pid in marker.read_text().split())
        self.assertEqual(self.lines(), ['release'])
        os.killpg(wrapper.pid, signal.SIGKILL)  # the harness's group kill
        wrapper.wait(timeout=5)
        self.wait_for(lambda: 'restore alive=0' in self.lines(), 30, f'no restore: {self.lines()}')
        self.assertFalse(alive(runner))
        self.assertFalse(alive(child))
        self.wait_for(lambda: self.status()['state'] == 'restored', 10)
        log = (self.state / 'watchdog.log').read_text()
        self.assertIn('ended without restoring', log)
        self.assertEqual(oct((self.state / 'watchdog.log').stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), '0o700')
        self.assertFalse(notice.exists())  # it came back at once: nobody to tell

    def test_a_failing_restore_is_retried_and_a_person_is_told(self):
        marker = self.base / 'runner'
        notice = self.base / 'notice'
        tries = self.base / 'tries'
        flaky = (f"echo x >> '{tries}'; [ $(wc -l < '{tries}') -ge 3 ] && {self.restore_cmd()}")
        env = dict(self.env, OCR_PHASE_RESTORE_CMD=flaky,
                   OCR_PHASE_NOTIFY_CMD=f'echo "$OCR_PHASE_NOTICE" >> {notice}')
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--max-seconds', '120', '--restore-reserve', '20',
                   '--restore-horizon', '60', '--', PY, '-c', SLEEP_RUNNER, str(marker)]
        wrapper = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.processes.append(wrapper)
        self.wait_for(marker.exists, 20, 'the runner did not start')
        os.killpg(wrapper.pid, signal.SIGKILL)
        wrapper.wait(timeout=5)
        self.wait_for(lambda: 'restore alive=0' in self.lines(), 40, f'no restore: {self.lines()}')
        self.wait_for(lambda: self.status()['state'] == 'restored', 10)
        # The person is told after the status is written.
        self.wait_for(lambda: 'is back' in notice.read_text(), 10, 'no all-clear notice')
        self.assertIn('restore failed', notice.read_text())


class LeftDownTests(HandoverBase):
    """CD-R9: a server an earlier hand-over left down (the watchdog gave up,
    or a restore failed) is restored before a later call decides anything;
    its free memory would otherwise read as room and the call would run with
    no swap and report DONE."""

    def gave_up(self) -> None:
        # The restore fails for good: exit 3, and the watchdog gives up.
        env = dict(self.env, OCR_PHASE_RESTORE_CMD='false')
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '10',
                            '--restore-horizon', '2', env=env)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.wait_for(lambda: self.status().get('state') == 'gave-up', 60, 'the watchdog did not give up')
        self.assertEqual(self.lines(), ['release'])
        self.free.write_text('30000')  # the server is down: its memory is free

    def test_the_next_call_restores_first_and_then_decides_the_swap(self):
        self.gave_up()
        marker = self.base / 'ran'
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w").write("ocr")'],
                            '--max-seconds', '60', '--restore-reserve', '10')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('left down by an earlier hand-over (status.json: gave-up', output)
        # Restored first; with the server resident the swap is decided again.
        self.assertEqual(self.lines(), ['release', 'restore alive=0', 'release', 'restore alive=0'])
        self.assertTrue(marker.exists())
        decisions = [line.rsplit('-> ', 1)[1] for line in result.stdout.splitlines() if '] need ' in line]
        self.assertEqual(decisions, ['no swap needed', 'swapping'])
        self.assertEqual(self.status()['state'], 'restored')
        self.assertIn('RESULT exit=0 DONE', result.stdout)

    def test_a_restore_that_still_fails_runs_no_ocr_and_exits_3(self):
        self.gave_up()
        marker = self.base / 'ran'
        notice = self.base / 'notice'
        env = dict(self.env, OCR_PHASE_RESTORE_CMD='false',
                   OCR_PHASE_NOTIFY_CMD=f'echo "$OCR_PHASE_NOTICE" >> {notice}')
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w").write("ocr")'],
                            '--max-seconds', '60', '--restore-reserve', '10', env=env)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertFalse(marker.exists())
        self.assertNotIn('DONE', output)
        self.assertIn('RESULT exit=3 SERVER NOT RESTORED', result.stdout)
        self.assertIn('by hand: false', result.stdout)
        self.assertEqual(self.lines(), ['release'])
        self.assertEqual(self.status()['state'], 'restore-failed')
        self.assertIn('left down by an earlier hand-over', notice.read_text())

    def test_a_server_found_up_again_is_taken_as_restored(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Healthy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

        self.gave_up()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Healthy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = dict(self.env, OCR_PHASE_HEALTH_URL=f'http://127.0.0.1:{server.server_address[1]}/v1/models')
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '10', env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('it was restored since', result.stdout)
        self.assertEqual(self.lines(), ['release'])
        self.assertEqual(self.status()['state'], 'restored')

    def test_a_watchdog_still_restoring_gives_76_and_says_so(self):
        self.state.mkdir(mode=0o700)
        (self.state / 'status.json').write_text(json.dumps({'state': 'restore-failed', 'message': 'x'}))
        holder = _gpu_queue.GPUQueue(self.shared, 'offline-test-gpu')
        holder.acquire(timeout=1, local_model_guard=False)
        try:
            result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15')
        finally:
            holder.close()
        self.assertEqual(result.returncode, 76, result.stdout + result.stderr)
        self.assertIn('the watchdog of an earlier hand-over is still restoring', result.stderr)
        self.assertEqual(self.lines(), [])


class OtherClientTests(HandoverBase):
    """In-process, with a stand-in for scripts/_tenant_clients.py."""

    def stub(self, clients, idle_after=True):
        calls = []

        class StandIn:
            def __init__(self, host, port, retry_after_s=30):
                calls.append(('stand-in', host, port, retry_after_s))

            def __enter__(self):
                calls.append(('open',))
                return self

            def __exit__(self, *exc):
                calls.append(('close', self._events()))
                return False

            @staticmethod
            def _events():
                return list(open(self_events).read().splitlines()) if os.path.exists(self_events) else []

        self_events = str(self.events)

        def find_other_clients(health_url, *, exclude_pids, match, ignore=()):
            calls.append(('find', health_url, os.getpid() in exclude_pids, os.getppid() in exclude_pids, match))
            if ignore:
                calls.append(('ignore', list(ignore)))
            return clients

        def wait_until_idle(health_url, *, exclude_pids, match, poll_s, timeout_s, on_wait=None, ignore=()):
            calls.append(('wait', timeout_s))
            return idle_after

        module = mock.Mock(find_other_clients=find_other_clients, wait_until_idle=wait_until_idle,
                           StandIn=StandIn)
        return module, calls

    def setUp(self):
        super().setUp()
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Healthy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Healthy)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}/v1/models'

    def run_main(self, module, *options):
        env = dict(self.env, OCR_PHASE_HEALTH_URL=self.url, OCR_PHASE_CLIENT_MATCH='qwen, cli.js')
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(phase, 'tenant_clients', return_value=module), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = phase.main(['--max-seconds', '120', '--restore-reserve', '20', '--health-timeout', '1',
                               '--kill-grace', '1', '--window-grace', '1', '--release-settle', '1',
                               *options, '--', 'true'])
        return code, output.getvalue()

    def test_busy_clients_are_waited_for_and_still_busy_gives_76(self):
        module, calls = self.stub([{'pid': 4242, 'cmd': 'node cli.js', 'reason': 'socket'}], idle_after=False)
        code, output = self.run_main(module)
        self.assertEqual(code, 76, output)
        self.assertEqual(calls[0], ('find', self.url, True, True, ['qwen', 'cli.js']))
        self.assertEqual(calls[1][0], 'wait')
        self.assertLessEqual(calls[1][1], 120 - 20 - phase.MIN_RUN_SECONDS)
        self.assertEqual(self.lines(), [])
        self.assertIn('nothing was stopped', output)
        # The RESULT line names the client and the setting that lets a pooled
        # connection through (CD-R1).
        result = next(line for line in output.splitlines() if line.startswith('[ocr-phase] RESULT'))
        self.assertIn('blocked by pid 4242 (node cli.js)', result)
        self.assertIn('OCR_PHASE_CLIENT_IGNORE', result)

    def timed_book(self):
        """A book whose engines' startup is measured, and a measured release:
        a window needs 6.5 s release + 60 s Paddle (the slowest engine)."""
        book = self.fake_book(pages=1)
        (book / 'reports').mkdir()
        (book / 'reports' / 'ocr-times.json').write_text(json.dumps({'engines': {
            'hunyuan': {'loadSeconds': 1, 'longestPageSeconds': 1},
            'paddle': {'loadSeconds': 40, 'longestPageSeconds': 10},
            'second-look': {'loadSeconds': 1, 'longestPageSeconds': 1}}}))
        phase.private_directory(self.state)
        phase.record_release_time(self.state, 1.0)
        return book

    def test_the_client_wait_keeps_the_time_the_first_engine_needs(self):
        # CD-R3: the wait could use the budget down to 60 s, so a late window
        # stopped the model for an engine that could not load in time.
        book = self.timed_book()
        floor = phase.window_floor(phase.build_parser().parse_args(
            ['--max-seconds', '90', '--', PY, str(SCRIPTS / 'ocr_book.py'), str(book)]),
            [PY, str(SCRIPTS / 'ocr_book.py'), str(book)], self.state, 10)
        self.assertEqual(floor[0], 6.5 + 60)
        self.assertEqual(phase.startup_need([PY, str(SCRIPTS / 'ocr_book.py'), str(book), '--single-engine'])[0],
                         2 * 1.2)
        slept = []

        def slow(*args, timeout_s, **kwargs):
            slept.append(timeout_s)
            time.sleep(timeout_s + 1)  # idle only after the time it was given
            return True

        module, calls = self.stub([{'pid': 4246, 'cmd': 'curl', 'reason': 'socket'}])
        module.wait_until_idle = slow
        env = dict(self.env, OCR_PHASE_HEALTH_URL=self.url)
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(phase, 'tenant_clients', return_value=module), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = phase.main(['--max-seconds', '80', '--restore-reserve', '10', '--health-timeout', '1',
                               '--kill-grace', '1', '--window-grace', '1', '--release-settle', '1',
                               '--', PY, str(SCRIPTS / 'ocr_book.py'), str(book)])
        said = output.getvalue()
        self.assertEqual(code, 76, said)
        self.assertLessEqual(slept[0], 80 - 10 - 66.5)
        self.assertIn('less than the 66s a window needs', said)
        self.assertEqual(self.lines(), [], 'nothing may be stopped')
        self.assertFalse((book / 'ocr-hunyuan').exists())

    def test_a_budget_smaller_than_the_estimate_keeps_the_old_floor(self):
        book = self.timed_book()
        runner = [PY, str(SCRIPTS / 'ocr_book.py'), str(book)]
        arguments = phase.build_parser().parse_args(['--max-seconds', '60', '--', *runner])
        self.assertEqual(phase.window_floor(arguments, runner, self.state, 10)[0], phase.MIN_RUN_SECONDS)
        self.assertEqual(phase.window_floor(arguments, ['true'], self.state, 10)[0], phase.MIN_RUN_SECONDS)

    def test_the_ignore_list_goes_to_the_client_lookup(self):
        module, calls = self.stub([{'pid': 4245, 'cmd': 'nginx: worker', 'reason': 'ignored'}])
        code, output = self.run_main(module, '--client-ignore', 'nginx, uid:33,')
        self.assertEqual(code, 0, output)
        self.assertIn(('ignore', ['nginx', 'uid:33']), calls)
        self.assertNotIn('wait', [call[0] for call in calls])
        self.assertIn('pid 4245 (ignored: OCR_PHASE_CLIENT_IGNORE)', output)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])

    def test_idle_clients_get_the_stand_in_closed_before_the_restore(self):
        module, calls = self.stub([{'pid': 4243, 'cmd': 'node cli.js', 'reason': 'process'}])
        code, output = self.run_main(module)
        self.assertEqual(code, 0, output)
        self.assertNotIn('wait', [call[0] for call in calls])
        stand_in = [call for call in calls if call[0] == 'stand-in']
        self.assertEqual(stand_in[0][1:3], ('127.0.0.1', self.port))
        self.assertGreaterEqual(stand_in[0][3], 10)
        closed = [call for call in calls if call[0] == 'close']
        self.assertEqual(closed[0][1], ['release'])  # closed after the release, before the restore
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        self.assertEqual(self.status()['state'], 'restored')
        self.assertIn('restoreSeconds', json.loads((self.state / 'timings.json').read_text()))

    def test_a_signal_while_the_stand_in_starts_still_closes_it_before_the_restore(self):
        # CD-R11 (a): SIGTERM after the stand-in has bound but before
        # open_stand_in returned left it bound through the restore.
        module, calls = self.stub([{'pid': 4247, 'cmd': 'node cli.js', 'reason': 'process'}])
        entered = module.StandIn.__enter__

        def bind_then_signal(stand_in):
            entered(stand_in)
            os.kill(os.getpid(), signal.SIGTERM)
            return stand_in
        module.StandIn.__enter__ = bind_then_signal
        code, output = self.run_main(module)
        self.assertEqual(code, 128 + signal.SIGTERM, output)
        self.assertIn(('open',), calls)
        closed = [call for call in calls if call[0] == 'close']
        self.assertEqual(closed, [('close', ['release'])], 'closed once, after the release and before the restore')
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        self.assertEqual(self.status()['state'], 'restored')
        self.assertEqual(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_a_signal_in_the_error_path_before_the_restore_is_caught(self):
        # CD-R11 (b): a signal between the end of the run and the restore (here
        # while the failed release is reported) escaped swap_and_run, so the
        # call crashed and only the watchdog brought the server back.
        module, _ = self.stub([])
        self.env['OCR_PHASE_RELEASE_CMD'] = f"echo release >> '{self.events}'; false"
        say = phase.say
        sent = []

        def say_then_signal(message, *, error=False):
            say(message, error=error)
            if 'release failed' in message and not sent:
                sent.append(message)
                os.kill(os.getpid(), signal.SIGTERM)
        with mock.patch.object(phase, 'say', side_effect=say_then_signal):
            code, output = self.run_main(module)
        self.assertTrue(sent, output)
        self.assertEqual(code, 128 + signal.SIGTERM, output)
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])
        self.assertEqual(self.status()['state'], 'restored')
        self.assertIn('RESULT exit=143', output)

    def model_server(self, not_ready_for: int, ready_at=None):
        """A fake model: health always answers; chat completions answer 503
        for the first NOT_READY_FOR requests (still loading), then 200 - or,
        with READY_AT (a list holding a time.monotonic() value), 503 until then."""
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        posts = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def do_POST(self):
                posts.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                ready = len(posts) > not_ready_for if ready_at is None else time.monotonic() >= ready_at[0]
                body = b'{"choices": []}' if ready else b'{"error": "loading"}'
                self.send_response(200 if ready else 503)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}', posts

    def test_the_ready_probe_setting(self):
        self.assertIsNone(phase.ready_probe(None, 'http://h/v1/chat/completions'))
        for off in ('', '0', 'off', 'no', 'false'):
            self.assertIsNone(phase.ready_probe(off, None))
        self.assertEqual(phase.ready_probe('1', 'http://h/v1/chat/completions'), 'http://h/v1/chat/completions')
        self.assertEqual(phase.ready_probe('https://m/v1/chat/completions', None), 'https://m/v1/chat/completions')
        for bad, endpoint in (('1', None), ('1', ' '), ('maybe', 'http://h/v1'), ('ftp://h/x', None)):
            with self.subTest(bad=bad, endpoint=endpoint), self.assertRaises(phase.PhaseError):
                phase.ready_probe(bad, endpoint)

    def test_a_ready_probe_waits_for_the_model_to_answer_after_its_health_url(self):
        # CD-R14 / GH-21: a server whose health URL answers while the model
        # still loads; off by default, the probe holds the restore until a
        # 1-token chat completion answers.
        base, posts = self.model_server(not_ready_for=2)
        module, _ = self.stub([])
        self.url = base + '/v1/models'
        code, output = self.run_main(module, '--health-timeout', '20')
        self.assertEqual(code, 0, output)
        self.assertEqual(posts, [], 'off by default')
        self.env.update(OCR_PHASE_READY_PROBE='1', PROOFREAD_ENDPOINT=base + '/v1/chat/completions',
                        PROOFREAD_MODEL='local-27b')
        code, output = self.run_main(module, '--health-timeout', '20')
        self.assertEqual(code, 0, output)
        self.assertEqual(len(posts), 3, output)
        self.assertEqual(posts[-1], ('/v1/chat/completions', {
            'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 1, 'temperature': 0,
            'stream': False, 'model': 'local-27b'}))
        self.assertIn('ready OK: a 1-token chat completion at ' + base, output)
        self.assertEqual(self.status()['state'], 'restored')
        # A bad setting is a configuration error before anything is stopped.
        events = self.lines()
        self.env.update(OCR_PHASE_READY_PROBE='maybe')
        code, output = self.run_main(module)
        self.assertEqual(code, 2, output)
        self.assertEqual(self.lines(), events)

    def test_a_model_that_never_answers_the_probe_is_not_restored_and_the_watchdog_probes_too(self):
        base, posts = self.model_server(not_ready_for=10 ** 6)
        module, _ = self.stub([])
        self.url = base + '/v1/models'
        self.env.pop('PROOFREAD_MODEL', None)
        self.env.update(OCR_PHASE_READY_PROBE=base + '/v1/chat/completions')
        code, output = self.run_main(module, '--health-timeout', '2', '--restore-horizon', '3')
        self.assertEqual(code, 3, output)
        self.assertIn('did not answer a 1-token chat completion', output)
        self.assertTrue(posts)
        self.assertNotIn('model', posts[0][1])  # no PROOFREAD_MODEL: the server's own
        self.wait_for(lambda: self.status()['state'] == 'gave-up', 60, 'the watchdog did not give up')
        self.assertIn('OCR_PHASE_READY_PROBE', (self.state / 'watchdog.log').read_text())
        # The health timeout ended it with time left: no restore time is kept.
        self.assertNotIn('restoreSeconds', phase.read_timings(self.state))
        self.assertNotIn('time ran out', output)

    def test_a_restore_the_call_s_end_cuts_short_is_kept_as_the_least_it_needs(self):
        # E9-R2: a restore that ran out of the call's time was never recorded,
        # so the reserve never grew and every later call ended the same way.
        ready_at = [float('inf')]
        base, posts = self.model_server(0, ready_at=ready_at)
        module, _ = self.stub([])
        self.url = base + '/v1/models'
        probe = base + '/v1/chat/completions'
        self.env.pop('PROOFREAD_MODEL', None)
        self.env.update(OCR_PHASE_READY_PROBE=probe)
        phase.private_directory(self.state)
        (self.state / 'timings.json').write_text(json.dumps({'restoreSeconds': [1.0], 'restoreProbe': probe}))
        self.assertEqual(phase.restore_estimate(self.state, 10, probe), 5.0)
        ready_at[0] = time.monotonic() + 13  # after this call's end; the watchdog finds it ready
        code, output = self.run_main(module, '--max-seconds', '10', '--restore-reserve', '4', '--health-timeout', '30')
        self.assertEqual(code, 3, output)
        self.assertIn('time ran out', output)
        timings = phase.read_timings(self.state)
        self.assertEqual(timings['restoreSeconds'][0], 1.0)
        self.assertGreaterEqual(timings['restoreSeconds'][1], 6.0, output)
        self.assertEqual(timings['restoreProbe'], probe)
        self.assertGreater(phase.restore_estimate(self.state, 540, probe), 15.0)
        self.wait_for(lambda: self.status()['state'] == 'restored', 40, 'the watchdog did not restore')

    def test_an_unbudgeted_call_records_its_restore_apart_from_the_budgeted_ones(self):
        # CD-R13: only a call with a budget may raise the restore estimate.
        module, _ = self.stub([])
        seen = []
        real = phase.record_restore_time

        def record(state, seconds, *, budgeted=True, **rest):
            seen.append(budgeted)
            real(state, seconds, budgeted=budgeted, **rest)
        with mock.patch.object(phase, 'record_restore_time', side_effect=record):
            code, output = self.run_main(module)
            self.assertEqual(code, 0, output)
            code, output = self.run_main(module, '--max-seconds', '0', '--unlimited-shell')
        self.assertEqual(code, 0, output)
        self.assertEqual(seen, [True, False])

    def test_busy_clients_that_finish_let_the_window_run(self):
        module, calls = self.stub([{'pid': 4244, 'cmd': 'curl', 'reason': 'socket'}], idle_after=True)
        code, output = self.run_main(module)
        self.assertEqual(code, 0, output)
        self.assertEqual([call[0] for call in calls][:2], ['find', 'wait'])
        self.assertEqual(self.lines(), ['release', 'restore alive=0'])


class LoadMeasureTests(unittest.TestCase):
    """ocr_book.measure(): an engine's load from its own model-ready signal
    (the run report's modelReadyAt), else from its first seal less that
    page's own time.  The page times are the bench OCR run's (2026-11-15),
    where the old rule (the fastest page taken off the first seal) gave
    24.81 s for Hunyuan's 16 slots and 27.46 s for Paddle, about three times
    the loads read from the engines' logs (about 2 s and 8 s)."""

    def setUp(self):
        import ocr_book
        self.ocr_book = ocr_book
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.times = Path(temp.name) / 'ocr-times.json'

    def report(self, pages, **extra):
        return {'pages': [{'scanPage': n, 'status': 'completed', 'seconds': s} for n, s in pages], **extra}

    def measured(self, engine):
        return json.loads(self.times.read_text())['engines'][engine]

    def test_parallel_slots_take_the_model_ready_signal(self):
        # 16 slots: the first seal is page 9, 24.5 s after the model was ready.
        pages = [(1, 34.13), (9, 24.53), (32, 1.71), (50, 70.58)]
        started = 1000.0
        self.ocr_book.measure(self.times, 'hunyuan', started, started + 26.52,
                              self.report(pages, modelReadyAt=started + 1.99), 9)
        hunyuan = self.measured('hunyuan')
        self.assertEqual(hunyuan['loadSeconds'], 1.99)
        self.assertEqual(hunyuan['loadFrom'], 'model-ready')
        self.assertEqual(hunyuan['longestPageSeconds'], 70.58)
        self.assertEqual(hunyuan['pagesTimed'], 4)
        # Without the signal: the first sealed page's own time, not the fastest.
        self.ocr_book.measure(self.times, 'hunyuan', started, started + 26.52, self.report(pages), 9)
        self.assertEqual(self.measured('hunyuan')['loadSeconds'], 1.99)
        self.assertEqual(self.measured('hunyuan')['loadFrom'], 'first-page')

    def test_a_sequential_engine_takes_its_first_page_off(self):
        # Paddle reads page 1 first (20.2 s); its fastest page was 1.06 s.
        pages = [(1, 20.2), (2, 5.2), (3, 1.06), (4, 35.73)]
        started = 2000.0
        self.ocr_book.measure(self.times, 'paddle', started, started + 28.52, self.report(pages), 1)
        paddle = self.measured('paddle')
        self.assertEqual(paddle['loadSeconds'], 8.32)
        self.assertEqual(paddle['loadFrom'], 'first-page')
        # A model-ready time from an earlier run (before this start) is not this load's.
        self.ocr_book.measure(self.times, 'paddle', started, started + 28.52,
                              self.report(pages, modelReadyAt=started - 600), 1)
        self.assertEqual(self.measured('paddle')['loadSeconds'], 8.32)
        # Nothing known of the first page: the fastest page, as before.
        self.ocr_book.measure(self.times, 'paddle', started, started + 28.52, self.report(pages), None)
        self.assertEqual(self.measured('paddle')['loadSeconds'], 27.46)
        self.assertEqual(self.measured('paddle')['loadFrom'], 'fastest-page')
        # The estimate ocr_book.py starts an engine by reads the same keys.
        self.ocr_book.measure(self.times, 'paddle', started, started + 28.52,
                              self.report(pages, modelReadyAt=started + 8.0), 1)
        self.assertAlmostEqual(self.ocr_book.startup_seconds(json.loads(self.times.read_text()), 'paddle'),
                               (8.0 + 35.73) * self.ocr_book.STARTUP_MARGIN)

    def test_run_step_names_the_first_sealed_page(self):
        temp = Path(tempfile.mkdtemp(dir=self.times.parent))
        progress = temp / 'progress'
        progress.write_text('7\n')
        script = temp / 'runner.py'
        script.write_text(f"import time\ntime.sleep(0.3)\nopen({str(progress)!r}, 'a').write('3\\n4\\n')\n")
        code, started, first_seal, first_page = self.ocr_book.run_step([PY, str(script)], dict(os.environ),
                                                                       str(progress))
        self.assertEqual((code, first_page), (0, 3))
        self.assertGreater(first_seal, started)


class OcrBookTests(HandoverBase):
    def book_command(self, book, *extra):
        return [PY, str(SCRIPTS / 'ocr_book.py'), str(book), *extra]

    def test_ocr_book_runs_the_engines_in_order_and_resumes(self):
        book = self.fake_book()
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        env['OCR_HUNYUAN_MODE'] = 'shared'
        # A first call whose engine A would start with less time than it needs.
        reports = book / 'reports'
        reports.mkdir()
        (reports / 'ocr-times.json').write_text(json.dumps(
            {'engines': {'paddle': {'loadSeconds': 500, 'longestPageSeconds': 10}}}))
        first = subprocess.run(self.book_command(book, '--max-seconds', '300'), env=env,
                               capture_output=True, text=True, timeout=120)
        self.assertEqual(first.returncode, 75, first.stdout + first.stderr)
        self.assertEqual(len(list((book / 'ocr-hunyuan').glob('page-*.json'))), 3)
        self.assertTrue((book / 'draft.md').exists())
        self.assertFalse((book / 'ocr-paddle').exists() and list((book / 'ocr-paddle').glob('page-*.json')))
        self.assertIn('loads nothing', first.stdout)
        self.assertFalse(any(line.startswith('started:') for line in self.lines()))  # no Paddle model
        times = json.loads((reports / 'ocr-times.json').read_text())['engines']
        self.assertIn('hunyuan', times)
        # The next call starts afresh: engine A, then the second look.
        second = subprocess.run(self.book_command(book, '--max-seconds', '300'), env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(list((book / 'ocr-paddle').glob('page-*.json'))), 3)
        self.assertTrue((reports / 'second-look-run.json').exists())
        times = json.loads((reports / 'ocr-times.json').read_text())['engines']
        self.assertLess(times['paddle']['loadSeconds'], 500)
        draft = (book / 'draft.md').stat().st_mtime_ns
        before = len(self.lines())
        third = subprocess.run(self.book_command(book), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertEqual(len(self.lines()), before)  # nothing loaded, nothing read
        self.assertEqual((book / 'draft.md').stat().st_mtime_ns, draft)
        self.assertIn('RESULT exit=0', third.stdout)

    def test_ocr_times_take_the_engines_model_ready_signals(self):
        # Each runner's report says when its model was ready (the server
        # answering with the model, the Paddle worker's ready line), and the
        # load ocr-times.json keeps is measured to it.
        book = self.fake_book(pages=2)
        self.slow_paddle(1.5)
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        result = subprocess.run(self.book_command(book), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reports = book / 'reports'
        times = json.loads((reports / 'ocr-times.json').read_text())['engines']
        for engine, name in (('hunyuan', 'hunyuanocr-run.json'), ('paddle', 'paddleocr-run.json')):
            report = json.loads((reports / name).read_text())
            self.assertIsInstance(report.get('modelReadyAt'), float, name)
            self.assertEqual(times[engine]['loadFrom'], 'model-ready', engine)
            self.assertIn('longestPageSeconds', times[engine])
        self.assertGreaterEqual(times['paddle']['loadSeconds'], 1.5)
        self.assertLess(times['paddle']['loadSeconds'], 30)

    def test_a_direct_call_under_a_local_model_says_to_use_the_hand_over(self):
        # CD-R2: with OCR_PHASE_RELEASE_CMD set and the GPU free, a direct call
        # defers every GPU engine (75), and its RESULT line said "time ran
        # out ... run the SAME command again", which only defers again.
        book = self.fake_book(pages=2)
        log = self.base / 'ocr-book.log'
        with log.open('w') as handle:
            result = subprocess.run(self.book_command(book), env=self.env, stdout=handle,
                                    stderr=subprocess.STDOUT, timeout=120)
        output = log.read_text()
        self.assertEqual(result.returncode, 75, output)
        self.assertNotIn('started', self.lines())
        self.assertIn(_gpu_queue.LOCAL_MODEL_SUMMARY, output.splitlines())
        result_line = output.splitlines()[-1]
        self.assertTrue(result_line.startswith('[ocr-book] RESULT exit=75 INCOMPLETE - the GPU belongs to the '
                                               'local model'), result_line)
        self.assertIn(f"run_ocr_phase.py -- {PY} {SCRIPTS / 'ocr_book.py'} {book}", result_line)
        self.assertIn('not the same command again', result_line)
        self.assertNotIn('time ran out', result_line)
        self.assertNotIn('run the same command again', output.replace('not the same command again', ''))
        wait = subprocess.run([PY, str(SCRIPTS / 'wait_for_run.py'), str(book), str(log), '--timeout', '5'],
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn('run it through run_ocr_phase.py, not the SAME command again', wait.stdout)
        # A runner run directly reads the same through wait_for_run.py.
        runner_log = self.base / 'hunyuan.log'
        with runner_log.open('w') as handle:
            subprocess.run(self.hunyuan(book, '--server-mode', 'dedicated'), env=self.env, stdout=handle,
                           stderr=subprocess.STDOUT, timeout=60)
        wait = subprocess.run([PY, str(SCRIPTS / 'wait_for_run.py'), str(book / 'ocr-hunyuan'), str(runner_log),
                               '--timeout', '5'], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn('run it through run_ocr_phase.py, not the SAME command again', wait.stdout,
                      runner_log.read_text())
        # Out of time is still "run the same command again".
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        env.update(OCR_PHASE_DEADLINE=str(time.time() + 60), OCR_PHASE_STARTUP_SECONDS='999')
        late = subprocess.run(self.hunyuan(book, '--server-mode', 'dedicated'), env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(late.returncode, 75, late.stdout + late.stderr)
        self.assertNotIn('NOT RUN', late.stdout)

    def slow_paddle(self, seconds: float):
        package = self.base / 'fake-package' / 'paddleocr.py'
        package.write_text(package.read_text().replace(
            "        self.version = pipeline_version\n",
            "        self.version = pipeline_version\n"
            "        time.sleep(float(os.environ.get('FAKE_LOAD_DELAY') or 0))\n"))
        self.env['FAKE_LOAD_DELAY'] = str(seconds)

    def test_the_paddle_load_ends_with_the_budget(self):
        # CD-R3: the worker waited up to 600 s for the model, past the deadline.
        book = self.fake_book(pages=2)
        self.slow_paddle(30)
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        env['OCR_PHASE_DEADLINE'] = str(time.time() + 3)
        began = time.monotonic()
        result = subprocess.run([PY, str(SCRIPTS / 'run_paddleocr_vl.py'), str(book / 'renders'),
                                 str(book / 'ocr-paddle'), '--report', str(self.base / 'p.json')],
                                env=env, capture_output=True, text=True, timeout=60)
        self.assertLess(time.monotonic() - began, 15, result.stdout + result.stderr)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('time budget reached while PaddleOCR-VL was loading', result.stdout)
        self.assertIn('the time budget ran out before a single page was sealed', result.stderr)
        report = json.loads((self.base / 'p.json').read_text())
        self.assertEqual(report['counts']['deferred'], 2)
        self.assertEqual(report['counts']['failed'], 0)

    def slow_server(self, seconds: float):
        """The fake llama-server takes SECONDS to listen, as a model load does."""
        binary = self.base / 'fake-llama-server'
        head, rest = binary.read_text().split('\n', 1)
        binary.write_text(head + "\nimport os as _o, time as _t\n_t.sleep(float(_o.environ.get('FAKE_START_DELAY') or 0))\n"
                          + rest)
        self.env['FAKE_START_DELAY'] = str(seconds)

    def test_the_hunyuan_load_ends_with_the_budget_in_both_modes(self):
        # Dedicated: READY_TIMEOUT (600 s) was not capped by the budget.
        # Shared: the broker's startup deadline was a queue timeout (75), so a
        # budget too small for the load looped 75 -> 76 for ever under the
        # hand-over.  Both now end with 1 and the times, the pages deferred.
        book = self.fake_book(pages=2)
        self.slow_server(30)
        for mode in ('dedicated', 'shared'):
            with self.subTest(mode=mode):
                env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
                env['OCR_PHASE_DEADLINE'] = str(time.time() + 4)
                began = time.monotonic()
                result = subprocess.run(self.hunyuan(book, '--server-mode', mode), env=env,
                                        capture_output=True, text=True, timeout=90)
                output = result.stdout + result.stderr
                self.assertLess(time.monotonic() - began, 25, output)
                self.assertEqual(result.returncode, 1, output)
                self.assertIn('time budget reached while HunyuanOCR was loading', result.stdout)
                self.assertIn("the time budget is too small for HunyuanOCR's model load", result.stderr)
                self.assertRegex(result.stderr, r'\d+s of it were left when the load began, and the server was '
                                                r'still loading after \d+s')
                report = json.loads((self.base / 'hunyuan-report.json').read_text())
                self.assertEqual((report['counts']['deferred'], report['counts']['failed']), (2, 0))
                # No fake server is left loading after the runner.
                self.wait_for(lambda: not any(alive(int(path.read_text())) for path in self.pids.glob('*')
                                              if path.read_text().isdigit()), 20, 'a fake server outlived its run')

    def test_a_shared_hunyuan_budget_too_small_for_its_load_ends_in_raise_the_budget(self):
        # Through ocr_book.py: 1 with both times and "raise the budget", not
        # 75 (which the hand-over turned into a 76 to repeat for ever).
        book = self.fake_book(pages=2)
        self.slow_server(30)
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        env['OCR_HUNYUAN_MODE'] = 'shared'
        result = subprocess.run(self.book_command(book, '--max-seconds', '4', '--single-engine'), env=env,
                                capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        result_line = result.stdout.splitlines()[-1]
        self.assertRegex(result_line, r'RESULT exit=1 FAILED - the time budget is too small for hunyuan: \d+s '
                                      r'were left when it started, and its model load and longest page take at '
                                      r'least \d+s')
        self.assertIn('raise the budget', result_line)
        self.assertNotIn('next call', result_line)

    def test_a_first_engine_short_of_its_estimate_is_75_once_then_a_failure(self):
        # CD-R3: the first engine of a call got the time its estimate asked for
        # and still sealed nothing: 75 (the estimate was short, and is raised),
        # where it was 1 ("FAILED - do not just repeat").  With less than the
        # raised estimate it stays a failure, so a budget too small never loops.
        book = self.fake_book(pages=2)
        self.slow_paddle(30)
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        first = subprocess.run(self.book_command(book, '--single-engine'), env=env, capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        times_path = book / 'reports' / 'ocr-times.json'
        times = json.loads(times_path.read_text())
        times['engines']['paddle'] = {'loadSeconds': 2, 'longestPageSeconds': 1}
        times_path.write_text(json.dumps(times))
        short = subprocess.run(self.book_command(book, '--max-seconds', '8'), env=env, capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(short.returncode, 75, short.stdout + short.stderr)
        self.assertIn('paddle: the time ran out before its first page although', short.stdout)
        paddle = json.loads(times_path.read_text())['engines']['paddle']
        self.assertTrue(paddle['loadAtLeast'])
        self.assertGreaterEqual(paddle['loadSeconds'], 6)
        again = subprocess.run(self.book_command(book, '--max-seconds', '8'), env=env, capture_output=True,
                               text=True, timeout=120)
        self.assertEqual(again.returncode, 1, again.stdout + again.stderr)
        self.assertIn('RESULT exit=1 FAILED - the time budget is too small for paddle', again.stdout)

    def test_one_window_covers_every_engine(self):
        book = self.fake_book()
        self.env['OCR_HUNYUAN_MODE'] = 'shared'
        result = self.phase([PY, str(SCRIPTS / 'ocr_book.py'), str(book)], '--max-seconds', '200',
                            '--restore-reserve', '30', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.lines()
        self.assertEqual(lines.count('release'), 1)
        self.assertEqual([line for line in lines if line.startswith('restore')], ['restore alive=0'])
        self.assertEqual(lines[-1], 'restore alive=0')
        self.assertEqual(len(list((book / 'ocr-paddle').glob('page-*.json'))), 3)
        self.assertTrue((book / 'draft.md').exists())


if __name__ == '__main__':
    unittest.main()
