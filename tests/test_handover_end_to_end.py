"""The whole OCR phase of a tiny book through the GPU hand-over, end to end and
offline: scripts/run_ocr_phase.py -- scripts/ocr_book.py BOOK, with a fake local
model server on its own port, release/restore commands that stop and start it,
a fake nvidia-smi, a fake llama-server and a fake Paddle; and the installer's
setup tests through the same hand-over.  It checks that the pieces built
separately connect: the window opens, the 503 stand-in
(scripts/_tenant_clients.py) answers on the model's port while the model is
down, both engines write their drafts, the restore runs only once no OCR
process is left, the model serves again afterwards, and wait_for_run.py reads
the call's log as done.  No GPU, no model, no systemctl."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

from test_handover import PY, SLOW_SERVER, HandoverBase, alive
from test_local_ocr_runners import FAKE_PADDLE, SCRIPTS

# The local model: answers 200 on every GET, records when it is up and down,
# and lets go of its port a moment after SIGTERM, as a server that drains
# its requests does (MODEL_STOP_DELAY), so the release command has returned
# before the port is free.
MODEL_SERVER = r'''
import os, signal, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
port, pidfile, events = int(sys.argv[1]), sys.argv[2], sys.argv[3]
def record(event):
    with open(events, 'a') as handle:
        handle.write(event + '\n')
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        body = b'{"data": [{"id": "fake-local-model"}]}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('X-Fake-Model', '1')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
def stop(signum, frame):
    def later():
        time.sleep(float(os.environ.get('MODEL_STOP_DELAY') or 0))
        server.shutdown()
    threading.Thread(target=later, daemon=True).start()
signal.signal(signal.SIGTERM, stop)
Path(pidfile).write_text(str(os.getpid()))
record('model up')
server.serve_forever()
server.server_close()
record('model down')
'''

# The restore command: how many OCR processes are still alive (each fake
# llama-server and Paddle worker leaves its pid in the pid folder), then the
# model takes its memory back and starts again, detached, like a service.
RESTORE = r'''
import os, subprocess, sys
from pathlib import Path
events, free, piddir, server, port, pidfile = sys.argv[1:7]
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
subprocess.Popen([sys.executable, server, port, pidfile, events], start_new_session=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
'''

# Prepended to the fake llama-server: as it starts (inside the window), ask
# the model's port what answers there now.
PROBE = r'''
import os as _os, urllib.error as _error, urllib.request as _request
if _os.environ.get('E2E_PROBE_URL'):
    try:
        with _request.urlopen(_os.environ['E2E_PROBE_URL'], timeout=5) as _reply:
            _seen = f"{_reply.status} {_reply.headers.get('Retry-After')} {_reply.headers.get('X-Fake-Model')}"
    except _error.HTTPError as _reply:
        _seen = f"{_reply.code} {_reply.headers.get('Retry-After')} {_reply.headers.get('X-Fake-Model')}"
    except OSError as _failure:
        _seen = f"unreachable {_failure}"
    with open(_os.environ['E2E_PROBE_LOG'], 'a') as _handle:
        _handle.write(_seen + '\n')
'''

# The fake Paddle, also leaving the pid of the process that loads it.
PADDLE_WITH_PID = FAKE_PADDLE + r'''
import os as _os
from pathlib import Path as _Path
if _os.environ.get('FAKE_PID_DIR'):
    (_Path(_os.environ['FAKE_PID_DIR']) / f'paddle-{_os.getpid()}').write_text(str(_os.getpid()))
'''


# nvidia-smi as the wrapper (used, total) and the installer (free) ask it, on a
# 32000 MiB card whose free memory is in $FAKE_SMI_FREE_FILE.
FAKE_SMI = r'''#!/bin/sh
free=$(cat "$FAKE_SMI_FREE_FILE")
case "$*" in
  *memory.used,memory.total*) echo "$((32000 - free)), 32000";;
  *memory.free*) echo "$free";;
  *) echo "Not Supported";;
esac
'''


def get(url: str):
    """(status, headers) of a GET, or (None, None) when nothing answers."""
    try:
        with urllib.request.urlopen(url, timeout=5) as reply:
            return reply.status, reply.headers
    except urllib.error.HTTPError as reply:
        return reply.code, reply.headers
    except OSError:
        return None, None


@unittest.skipUnless(sys.platform.startswith('linux'), 'the fakes read /proc')
class OcrPhaseEndToEndTests(HandoverBase):
    def setUp(self):
        super().setUp()
        (self.bin / 'nvidia-smi').write_text(FAKE_SMI)
        self.model_script = self.base / 'model_server.py'
        self.model_script.write_text(MODEL_SERVER)
        self.model_pid = self.base / 'model.pid'
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.model_port = probe.getsockname()[1]
        self.model_url = f'http://127.0.0.1:{self.model_port}/v1/models'
        self.probe_log = self.base / 'probe.log'
        self.addCleanup(self.stop_model)
        self.start_model()

    def start_model(self):
        process = subprocess.Popen([PY, str(self.model_script), str(self.model_port), str(self.model_pid),
                                    str(self.events)], start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        self.wait_for(lambda: get(self.model_url)[0] == 200, 10, 'the fake model did not start')

    def stop_model(self):
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(self.model_pid.read_text()), signal.SIGKILL)

    def book_with_the_model_as_tenant(self, pages: int) -> Path:
        """A fake book whose engines probe the model's port, and the model
        declared as the GPU's tenant the way the installer writes it."""
        book = self.fake_book(pages=pages)
        # The fake llama-server probes the model's port as it starts; the fake
        # Paddle leaves its worker's pid for the restore's check.
        binary = self.base / 'fake-llama-server'
        binary.write_text('#!' + PY + '\n' + PROBE + textwrap.dedent(SLOW_SERVER))
        (self.base / 'fake-package' / 'paddleocr.py').write_text(textwrap.dedent(PADDLE_WITH_PID))
        # A release that only signals the model: it lets go of the port a moment later.
        release = (f"kill -TERM $(cat '{self.model_pid}'); echo release >> '{self.events}'; "
                   f"echo 30000 > '{self.free}'")
        restore = (f"'{PY}' '{self.base / 'restore.py'}' '{self.events}' '{self.free}' '{self.pids}' "
                   f"'{self.model_script}' {self.model_port} '{self.model_pid}'")
        (self.base / 'restore.py').write_text(RESTORE)
        self.env.update(OCR_PHASE_RELEASE_CMD=release, OCR_PHASE_RESTORE_CMD=restore,
                        OCR_PHASE_HEALTH_URL=self.model_url, OCR_PHASE_MAX_SECONDS='200',
                        MODEL_STOP_DELAY='1', E2E_PROBE_URL=self.model_url,
                        E2E_PROBE_LOG=str(self.probe_log))
        return book

    def assert_window(self):
        """One release, the stand-in answering inside it, one restore once no
        OCR process is left, and the model serving its port again."""
        probes = self.probe_log.read_text().splitlines()
        self.assertTrue(probes, 'no engine started a server')
        for seen in probes:
            status, retry, model = seen.split(' ', 2)
            self.assertEqual((status, model), ('503', 'None'), probes)
            self.assertTrue(10 <= int(retry) <= 300, probes)
        events = self.lines()
        self.assertEqual(events.count('release'), 1, events)
        restores = [line for line in events if line.startswith('restore')]
        self.assertEqual(restores, ['restore alive=0'], events)
        first_ocr = next(index for index, line in enumerate(events) if line == 'started')
        self.assertLess(events.index('release'), events.index('model down'))
        self.assertLess(events.index('model down'), first_ocr)
        self.assertLess(first_ocr, events.index('restore alive=0'))
        self.assertEqual(events[-1], 'model up')
        self.assertTrue(list(self.pids.glob('paddle-*')), 'the fake Paddle worker left no pid')
        for path in self.pids.glob('*'):
            self.assertFalse(alive(int(path.read_text())), f'{path.name} outlived the window')
        status, headers = get(self.model_url)
        self.assertEqual((status, headers.get('X-Fake-Model')), (200, '1'))
        self.assertEqual(self.status()['state'], 'restored')

    def test_one_call_runs_the_book_in_a_window_with_the_stand_in_and_restores_the_model(self):
        book = self.book_with_the_model_as_tenant(pages=2)
        log = self.base / 'ocr-phase.log'
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--kill-grace', '2', '--window-grace', '3',
                   '--release-settle', '1', '--', PY, str(SCRIPTS / 'ocr_book.py'), str(book)]
        with log.open('w') as handle:
            result = subprocess.run(command, env=self.env, stdout=handle, stderr=subprocess.STDOUT,
                                    timeout=180)
        output = log.read_text()
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('[ocr-phase] RESULT exit=0', output)
        self.assertIn('[ocr-book] RESULT exit=0', output)

        # The stand-in held the model's port while the model was down: each
        # llama-server start inside the window got 503 + Retry-After from it.
        self.assertIn('stand-in on port', output)
        self.assertNotIn('WARNING no stand-in', output)

        # Both engines wrote their drafts, and the early draft was built.
        self.assertEqual(len(list((book / 'ocr-hunyuan').glob('page-*.json'))), 2)
        self.assertEqual(len(list((book / 'ocr-paddle').glob('page-*.json'))), 2)
        self.assertTrue((book / 'draft.md').exists())
        self.assertTrue((book / 'reports' / 'second-look-run.json').exists())
        paddle = json.loads((book / 'reports' / 'paddleocr-run.json').read_text())
        self.assertEqual(paddle['execution']['queueResource'], 'offline-test-gpu')

        # One release and one restore around the OCR, the restore only once no
        # OCR process was left, and the model serves again, not the stand-in.
        self.assert_window()

        # wait_for_run.py, given the call's log, reads it as done.
        wait = subprocess.run([PY, str(SCRIPTS / 'wait_for_run.py'), str(book), str(log), '--timeout', '10'],
                              env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(wait.returncode, 0, wait.stdout + wait.stderr)
        self.assertIn('DONE ocr_book.py', wait.stdout)

    def test_a_background_call_runs_the_book_in_one_window_while_the_agent_waits(self):
        """--background, as an agent that does not run on the local model uses it: the call is
        started detached with the machine's budget far too small for the book, the agent waits
        with wait_for_run.py, and the model is stopped once and started once."""
        book = self.book_with_the_model_as_tenant(pages=2)
        log = self.base / 'ocr-phase.log'
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--kill-grace', '2', '--window-grace', '3',
                   '--release-settle', '1', '--background', '--', PY, str(SCRIPTS / 'ocr_book.py'), str(book)]
        env = dict(self.env, OCR_PHASE_MAX_SECONDS='1')
        with log.open('w') as handle:
            process = subprocess.Popen(command, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        try:
            deadline = time.monotonic() + 150
            while True:
                wait = subprocess.run([PY, str(SCRIPTS / 'wait_for_run.py'), str(book), str(log), '--timeout', '5'],
                                      env=env, capture_output=True, text=True, timeout=60)
                if wait.returncode != 75:
                    break
                self.assertIn('RUNNING', wait.stdout)
                self.assertLess(time.monotonic(), deadline, wait.stdout)
            self.assertEqual(process.wait(timeout=30), 0, log.read_text())
        finally:
            if process.poll() is None:
                process.kill()
        output = log.read_text()
        self.assertEqual(wait.returncode, 0, wait.stdout + wait.stderr + output)
        self.assertIn('DONE ocr_book.py', wait.stdout)
        self.assertIn('[ocr-phase] RESULT exit=0', output)
        self.assertNotIn('exit=75', output)
        self.assertEqual(len(list((book / 'ocr-paddle').glob('page-*.json'))), 2)
        self.assertEqual(len(list((book / 'ocr-hunyuan').glob('page-*.json'))), 2)
        self.assert_window()

    def test_a_pooled_connection_blocks_until_it_is_named_in_the_ignore_list(self):
        """CD-R1: a reverse proxy, gateway or web UI keeps a pooled keep-alive
        connection to the model's port open for good.  Each call waited for it
        and exited 76; the agent repeated that forever.  The 76 now names the
        pid and OCR_PHASE_CLIENT_IGNORE, which lets the window run."""
        self.book_with_the_model_as_tenant(pages=1)
        marker = 'fake-pooled-proxy-for-the-model'
        holder = subprocess.Popen(
            [PY, '-c', 'import socket, sys, time\n'
                       's = socket.create_connection(("127.0.0.1", int(sys.argv[1])))\n'
                       'open(sys.argv[2], "w").write("connected")\ntime.sleep(120)',
             str(self.model_port), str(self.base / 'held'), marker],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(holder)
        self.wait_for((self.base / 'held').exists, 10, 'the holder did not connect')
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--max-seconds', '80', '--restore-reserve', '10',
                   '--kill-grace', '2', '--window-grace', '3', '--release-settle', '1',
                   '--', PY, '-c', 'print("ocr ran")']
        blocked = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=120)
        output = blocked.stdout + blocked.stderr
        self.assertEqual(blocked.returncode, 76, output)
        self.assertEqual(self.lines().count('release'), 0, 'nothing may be stopped')
        result = next(line for line in blocked.stdout.splitlines() if line.startswith('[ocr-phase] RESULT'))
        self.assertIn(f'pid {holder.pid}', result)
        self.assertIn('OCR_PHASE_CLIENT_IGNORE', result)

        ignored = subprocess.run(command, env=dict(self.env, OCR_PHASE_CLIENT_IGNORE=f'nginx,{marker}'),
                                 capture_output=True, text=True, timeout=120)
        output = ignored.stdout + ignored.stderr
        self.assertEqual(ignored.returncode, 0, output)
        self.assertIn(f'pid {holder.pid} (ignored: OCR_PHASE_CLIENT_IGNORE)', output)
        self.assertIn('ocr ran', output)
        self.assertEqual(self.lines().count('release'), 1, self.lines())
        self.assertEqual(self.lines()[-1], 'model up')
        self.assertEqual(get(self.model_url)[0], 200)

    def test_the_installer_smokes_run_the_real_runners_in_one_window(self):
        """_setup_ocr.run_smokes with the local model holding the GPU: both
        engines' setup tests, as prepare_smoke builds them, in one hand-over
        (the queue guard lets the runners in only inside the window)."""
        from jyut_ocr.ocr import setup_ocr as setup
        self.book_with_the_model_as_tenant(pages=1)
        options = setup.SetupOptions(SCRIPTS.parent, self.base / 'config', self.base / 'data')
        fingerprint = 'e' * 64
        for engine in ('hunyuan', 'paddle'):
            # A render already there is kept, so the test needs no Pillow.
            render = setup.smoke_directory(options, fingerprint, engine) / 'renders' / 'page-0001.png'
            render.parent.mkdir(parents=True)
            render.write_text('scan 1')
        config = {key: self.env[key] for key in (
            'HUNYUAN_MODEL', 'HUNYUAN_MMPROJ', 'HUNYUAN_LLAMA_SERVER', 'OCR_HUNYUAN_SLOTS',
            'PADDLE_VL_MODEL_DIR', 'PADDLE_LAYOUT_MODEL_DIR', 'OCR_PHASE_RELEASE_CMD',
            'OCR_PHASE_RESTORE_CMD', 'OCR_PHASE_HEALTH_URL', 'OCR_PHASE_MAX_SECONDS')}
        config.update(JYUT_OCR_PYTHON=PY, PADDLEOCR_PYTHON=PY, OCR_HUNYUAN_DEVICE='cuda',
                      OCR_PADDLE_DEVICE='gpu:0')
        environment = {key: value for key, value in self.env.items() if key not in config}
        with mock.patch.dict(os.environ, environment, clear=True), \
                contextlib.redirect_stdout(io.StringIO()) as said:
            records = setup.run_smokes(options, config, ('hunyuan', 'paddle'),
                                       {'hunyuan': 'cuda', 'paddle': 'gpu:0'}, fingerprint)
        self.assertEqual(set(records), {'hunyuan', 'paddle'})
        self.assertIn('through one hand-over', said.getvalue())
        for engine, record in records.items():
            self.assertTrue(setup.check_smoke(record), engine)
        paddle = json.loads((setup.smoke_directory(options, fingerprint, 'paddle') / 'run-report.json').read_text())
        self.assertEqual(paddle['execution']['device'], 'gpu:0')
        self.assert_window()


# nvidia-smi from the JSON spec in $FAKE_GPU_SPEC: a card of `total` MiB
# whose compute processes are real processes of the test, each listed (and
# its MiB used) while it lives - a zombie is gone - by pid or by a pid file;
# `memory: "n/a"` answers [N/A] for the memory, as a unified-memory GPU does.
FAKE_GPU_SMI = r"""
import json, os, sys
spec = json.load(open(os.environ['FAKE_GPU_SPEC']))
def live(pid):
    try:
        with open(f'/proc/{pid}/stat') as handle:
            return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, ValueError, IndexError):
        return False
processes = []
for entry in spec.get('processes', []):
    pid = entry.get('pid')
    if entry.get('pidfile'):
        try:
            pid = int(open(entry['pidfile']).read())
        except (OSError, ValueError):
            continue
    if pid and live(pid):
        processes.append(dict(entry, pid=pid))
total = spec.get('total', 32000)
used = spec.get('base', 500) + sum(entry['mib'] for entry in processes)
query = ' '.join(sys.argv[1:])
with open(os.environ['FAKE_GPU_LOG'], 'a') as log:
    log.write(query + '\n')
if '--query-compute-apps' in query:
    for entry in processes:
        print(f"GPU-fake-0, {entry['pid']}, {entry['name']}, {entry['mib']}")
elif spec.get('memory') == 'n/a' and '--query-gpu' in query:
    print('0, GPU-fake-0, [N/A], [N/A]' if 'uuid' in query else '[N/A], [N/A]')
elif 'uuid' in query:
    print(f'0, GPU-fake-0, {used}, {total}')
elif 'memory.used,memory.total' in query:
    print(f'{used}, {total}')
elif 'memory.free' in query:
    print(total - used)
else:
    print('No devices were found')
    sys.exit(6)
"""

# The release: SIGTERM to the model, then wait for it to be gone, as
# `systemctl stop` does.
RELEASE_MODEL = r"""
import os, signal, sys, time
pidfile, events = sys.argv[1:3]
def live(pid):
    try:
        with open(f'/proc/{pid}/stat') as handle:
            return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, ValueError, IndexError):
        return False
try:
    pid = int(open(pidfile).read())
    os.kill(pid, signal.SIGTERM)
except (OSError, ValueError):
    pid = None
end = time.monotonic() + 10
while pid and live(pid) and time.monotonic() < end:
    time.sleep(0.05)
with open(events, 'a') as handle:
    handle.write('release\n')
"""


@unittest.skipUnless(sys.platform.startswith('linux'), 'the fakes read /proc')
class GpuOccupancyTests(HandoverBase):
    """What holds the GPU: the configured model (up or not) and other compute
    processes, listed by a fake nvidia-smi from real processes of the test.
    Nothing on the GPU is ever killed: what holds it is named with how to
    free it."""

    def setUp(self):
        super().setUp()
        (self.bin / 'nvidia-smi').write_text('#!' + PY + '\n' + FAKE_GPU_SMI)
        self.spec = self.base / 'gpu.json'
        self.smi_log = self.base / 'smi.log'
        self.gpu([])
        self.model_script = self.base / 'model_server.py'
        self.model_script.write_text(MODEL_SERVER)
        self.model_pid = self.base / 'model.pid'
        import socket
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.model_port = probe.getsockname()[1]
        self.model_url = f'http://127.0.0.1:{self.model_port}/v1/models'
        (self.base / 'release.py').write_text(RELEASE_MODEL)
        (self.base / 'restore.py').write_text(RESTORE)
        self.env.update(
            FAKE_GPU_SPEC=str(self.spec), FAKE_GPU_LOG=str(self.smi_log), OCR_PHASE_HEALTH_URL=self.model_url,
            OCR_PHASE_RELEASE_CMD=f"'{PY}' '{self.base / 'release.py'}' '{self.model_pid}' '{self.events}'",
            OCR_PHASE_RESTORE_CMD=(f"'{PY}' '{self.base / 'restore.py'}' '{self.events}' '{self.free}' "
                                   f"'{self.pids}' '{self.model_script}' {self.model_port} '{self.model_pid}'"))
        self.addCleanup(self.stop_model)

    def gpu(self, processes, **extra):
        self.spec.write_text(json.dumps(dict(processes=processes, **extra)))

    def model_on_the_gpu(self, mib=8000):
        return {'pidfile': str(self.model_pid), 'name': 'fake-model-server', 'mib': mib}

    def start_model(self):
        process = subprocess.Popen([PY, str(self.model_script), str(self.model_port), str(self.model_pid),
                                    str(self.events)], start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        self.wait_for(lambda: get(self.model_url)[0] == 200, 10, 'the fake model did not start')

    def stop_model(self):
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(self.model_pid.read_text()), signal.SIGKILL)

    def other_process(self) -> subprocess.Popen:
        """A process of this user on the GPU (a real one, so a kill would show)."""
        process = subprocess.Popen(['sleep', '120'], stdin=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def result_line(self, result) -> str:
        return next(line for line in result.stdout.splitlines() if line.startswith('[ocr-phase] RESULT'))

    def how_to_free(self, pid: int) -> str:
        """What the wrapper tells the person for a process of this user."""
        import run_ocr_phase as phase
        unit = phase.unit_of(pid)
        return phase.how_to_free({'pid': pid, 'uid': os.getuid(), 'user': phase.user_name(os.getuid()),
                                  'unit': unit[0] if unit else None, 'userUnit': bool(unit and unit[1])})

    def test_a_model_up_is_stopped_and_started_again_and_the_rest_is_named_not_killed(self):
        # (d) The model is up and another process of this user holds 20000
        # MiB: the model is stopped, still too little is free, so the model
        # is started again and the call ends GPU FULL naming the process and
        # how to free it; the process is untouched.
        self.start_model()
        other = self.other_process()
        self.gpu([self.model_on_the_gpu(), {'pid': other.pid, 'name': '/usr/bin/sleep', 'mib': 20000}])
        marker = self.base / 'ran'
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertFalse(marker.exists())
        self.assertIsNone(other.poll(), 'the other process was killed')
        self.assertEqual([line for line in self.lines() if line == 'release' or line.startswith('restore')],
                         ['release', 'restore alive=0'])
        self.assertEqual(get(self.model_url)[0], 200)
        self.assertIn(f'on the GPU: pid {other.pid}, user ', output)
        import run_ocr_phase as phase
        how = self.how_to_free(other.pid)
        self.assertIn(f'on the GPU: pid {other.pid}, user {phase.user_name(os.getuid())}, /usr/bin/sleep, '
                      f'20000 MiB', output)
        self.assertIn(f'to free it: {how}', output)
        line = self.result_line(result)
        self.assertIn('RESULT exit=1 GPU FULL', line)
        self.assertIn(f'held by pid {other.pid} (sleep, 20000 MiB): {how}', line)
        self.assertIn('the local model was stopped for this call and is back up', line)


    def status(self) -> dict:
        path = self.state / 'status.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def swaps(self) -> list[str]:
        return [line for line in self.lines() if line == 'release' or line.startswith('restore')]

    def test_a_model_that_is_not_up_on_an_idle_gpu_runs_the_ocr_directly(self):
        # (a) The person stopped the model; nothing else is on the GPU, whose
        # memory nvidia-smi cannot report (a unified-memory GPU): the OCR
        # runs at once, with no release, no stand-in and no restore.
        self.gpu([], memory='n/a')
        marker = self.base / 'ran'
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(marker.exists())
        self.assertEqual(self.swaps(), [])
        self.assertNotIn('stand-in on port', output)
        self.assertIn('[ocr-phase] model: not up (nothing answers or listens at 127.0.0.1:', output)
        self.assertIn('[gpu: model not up, left as it was]', self.result_line(result))
        self.assertIsNone(get(self.model_url)[0], 'the model was started')
        self.assertEqual(self.status()['state'], 'left-down')
        self.assertEqual((self.status()['modelUp'], self.status()['released']), (False, False))
        self.assertEqual(json.loads((self.state / 'timings.json').read_text())['lastWindow']['modelUp'], False)
        # The same with the memory read and plenty of it.
        self.gpu([])
        marker.unlink()
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(self.swaps(), [])

    def test_always_never_starts_a_model_that_is_not_up(self):
        # (b) OCR_PHASE_SWAP=always stopped and then STARTED a model the
        # person had stopped (the release did nothing, the restore started it).
        self.env['OCR_PHASE_SWAP'] = 'always'
        marker = self.base / 'ran'
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(marker.exists())
        self.assertEqual(self.swaps(), [])
        self.assertIsNone(get(self.model_url)[0], 'the model was started')
        self.assertIn('the model is not up, so it is neither stopped nor started', output)
        # A model that is up is still stopped and started again under always.
        self.start_model()
        self.gpu([self.model_on_the_gpu()])
        result = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '15')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.swaps(), ['release', 'restore alive=0'])
        self.assertIn('[gpu: model up, stopped and started again]', self.result_line(result))
        self.assertEqual(get(self.model_url)[0], 200)
        self.assertEqual((self.status()['state'], self.status()['released'], self.status()['restored']),
                         ('restored', True, True))

    def test_another_process_on_the_gpu_is_named_not_killed_and_the_stopped_model_stays_down(self):
        # (c) The model is stopped and another process of this user holds
        # 20000 MiB: nothing to stop of ours, so the call names the process
        # with how to free it and ends GPU FULL; the process is untouched and
        # the model is not started.
        other = self.other_process()
        self.gpu([{'pid': other.pid, 'name': '/usr/bin/sleep', 'mib': 20000}])
        marker = self.base / 'ran'
        result = self.phase([PY, '-c', f'open({str(marker)!r}, "w")'], '--max-seconds', '60',
                            '--restore-reserve', '15')
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertFalse(marker.exists())
        self.assertIsNone(other.poll(), 'the other process was killed')
        self.assertEqual(self.swaps(), [])
        self.assertIsNone(get(self.model_url)[0], 'the model was started')
        self.assertIn('only 11500 MiB free, 12288 MiB needed, and the local model is not up', output)
        how = self.how_to_free(other.pid)
        self.assertIn(f'on the GPU: pid {other.pid}', output)
        self.assertIn(f'to free it: {how}', output)
        line = self.result_line(result)
        self.assertIn('RESULT exit=1 GPU FULL', line)
        self.assertIn('the local model was not up and was not started', line)
        self.assertIn(f'held by pid {other.pid} (sleep, 20000 MiB): {how}', line)
        status = self.status()
        self.assertEqual((status['state'], status['exitCode']), ('left-down', 1))
        self.assertEqual([(entry['pid'], entry['mib'], entry['free']) for entry in status['heldBy']],
                         [(other.pid, 20000, how)])

    def test_without_a_configured_model_nothing_changes(self):
        # (g) No release/restore commands: the runner runs as it is, the GPU
        # is not even looked at, and nothing on it is touched.
        other = self.other_process()
        self.gpu([{'pid': other.pid, 'name': 'sleep', 'mib': 20000}])
        env = {key: value for key, value in self.env.items() if not key.startswith('OCR_PHASE_')}
        result = self.phase([PY, '-c', 'import os; print("window", os.environ.get("OCR_PHASE_WINDOW"))'],
                            env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('window None', result.stdout)
        self.assertIsNone(other.poll())
        self.assertFalse(self.smi_log.exists(), 'nvidia-smi was asked')
        self.assertIn('[gpu: no local model configured; nothing stopped]', self.result_line(result))

    def test_the_watchdog_is_not_armed_for_a_model_that_was_not_up(self):
        # (h) The wrapper killed mid-window (a harness's group kill): with the
        # model up its watchdog restores it; with the model not up there is
        # nothing to restore, and nothing starts it.
        from test_handover import SLEEP_RUNNER
        self.env['OCR_PHASE_SWAP'] = 'always'
        marker = self.base / 'runner'
        command = [PY, str(SCRIPTS / 'run_ocr_phase.py'), '--max-seconds', '120', '--restore-reserve', '20',
                   '--', PY, '-c', SLEEP_RUNNER, str(marker)]
        wrapper = subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        self.processes.append(wrapper)
        self.wait_for(marker.exists, 20, 'the runner did not start')
        runner, child = (int(pid) for pid in marker.read_text().split())
        self.addCleanup(lambda: [os.kill(pid, signal.SIGKILL) for pid in (runner, child) if alive(pid)])
        os.killpg(wrapper.pid, signal.SIGKILL)
        wrapper.wait(timeout=5)
        time.sleep(4)
        self.assertEqual(self.swaps(), [])
        self.assertIsNone(get(self.model_url)[0], 'the model was started')
        log = self.state / 'watchdog.log'
        self.assertFalse(log.exists() and 'ended without restoring' in log.read_text())
        import run_ocr_phase as phase
        self.assertNotIn(self.status().get('state'), phase.DOWN_STATES)

    def test_a_later_call_never_starts_a_model_the_person_stopped(self):
        # (i) recover_left_down restores a model an earlier hand-over left
        # down; a call that found the model down writes no such state, so no
        # later call "recovers" it.
        self.env.update(OCR_PHASE_SWAP='always',
                        OCR_PHASE_RESTORE_CMD=f"echo restore-attempt >> '{self.events}'; false")
        first = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '10', '--restore-horizon', '2')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.lines(), [])
        self.assertEqual(self.status()['state'], 'left-down')
        import run_ocr_phase as phase
        arguments = phase.build_parser().parse_args(['--restore-cmd', f"echo restore >> '{self.events}'",
                                                     '--health-url', self.model_url, '--', 'true'])
        with mock.patch.dict(os.environ, self.env):
            self.assertIsNone(phase.recover_left_down(arguments, self.state, phase.Clock(None)))
        self.assertEqual(self.lines(), [])
        self.env.pop('OCR_PHASE_SWAP')
        second = self.phase(['true'], '--max-seconds', '60', '--restore-reserve', '10')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(self.lines(), [])
        self.assertIsNone(get(self.model_url)[0], 'the model was started')
        self.assertEqual(self.status()['state'], 'left-down')

    def test_a_model_still_loading_counts_as_up(self):
        # Something listens on the model's port but answers nothing yet (a
        # server loading its model): it is up, so stopped and started again.
        import socket
        import run_ocr_phase as phase
        self.assertEqual(phase.model_state(None)[0], None)
        self.assertEqual(phase.model_state(self.model_url)[0], False)
        with socket.socket() as loading:
            loading.bind(('127.0.0.1', self.model_port))
            loading.listen(8)
            with mock.patch.object(phase, 'HEALTH_PROBE_SECONDS', 0.5):
                up, seen = phase.model_state(self.model_url)
        self.assertTrue(up, seen)
        self.assertIn('a model still loading', seen)


class ModelProbeTests(unittest.TestCase):
    """Asking whether the model is up must not keep the stand-in off its port."""

    def test_the_up_check_leaves_the_port_free_for_the_stand_in(self):
        # A model server that binds without SO_REUSEADDR (measured: ninfer)
        # and closes each connection after its answer.  An HTTP health GET
        # right before the release left the server's side in TIME_WAIT, and
        # the stand-in could not bind the freed port for a minute (measured
        # live: no stand-in in the window).  The up check only connects and
        # closes first, so the port is free as soon as the server is gone.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import run_ocr_phase as phase
        from jyut_ocr.ocr import tenant_clients as clients

        class Answer(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        class NoReuse(ThreadingHTTPServer):
            allow_reuse_address = False
            daemon_threads = True

        server = NoReuse(('127.0.0.1', 0), Answer)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        try:
            up, seen = phase.model_state(f'http://127.0.0.1:{port}/v1/models')
            self.assertTrue(up, seen)
            time.sleep(0.3)  # let the server finish its side of the probe
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        deadline = time.monotonic() + 2
        while True:
            try:
                stand_in = clients.StandIn('127.0.0.1', port, retry_after_s=30)
                stand_in.__enter__()
                break
            except clients.PortBusy as error:
                if time.monotonic() > deadline:
                    self.fail(f'the stand-in could not bind after the up check: {error}')
                time.sleep(0.1)
        stand_in.__exit__(None, None, None)


class OccupantTests(unittest.TestCase):
    """How a GPU process is named and how the person is told to free it."""

    def setUp(self):
        import run_ocr_phase
        self.phase = run_ocr_phase

    def test_the_systemd_unit_of_a_process(self):
        unit = self.phase.systemd_unit
        self.assertEqual(unit('0::/user.slice/user-1000.slice/user@1000.service/app.slice/ninfer.service\n'),
                         ('ninfer.service', True))
        self.assertEqual(unit('0::/system.slice/ollama.service\n'), ('ollama.service', False))
        # A terminal's scope or a login session is no service: kill it.
        self.assertIsNone(unit('0::/user.slice/user-1000.slice/user@1000.service/app.slice/'
                               'app-org.gnome.Terminal.slice/vte-spawn-1.scope\n'))
        self.assertIsNone(unit('0::/user.slice/user-1000.slice/session-2.scope\n'))
        self.assertIsNone(unit('0::/system.slice/docker-0123abcd.scope\n'))
        # cgroup v1: the systemd hierarchy.
        self.assertEqual(unit('12:cpu:/\n1:name=systemd:/system.slice/vllm.service\n'), ('vllm.service', False))

    def test_how_the_person_frees_a_process(self):
        me = os.getuid()
        how = self.phase.how_to_free
        self.assertEqual(how({'pid': 7, 'uid': me, 'unit': 'ninfer.service', 'userUnit': True}),
                         'systemctl --user stop ninfer.service')
        self.assertEqual(how({'pid': 7, 'uid': 0, 'unit': 'ollama.service', 'userUnit': False}),
                         'sudo systemctl stop ollama.service')
        self.assertEqual(how({'pid': 7, 'uid': me + 1, 'user': 'kim', 'unit': 'x.service', 'userUnit': True}),
                         'as kim: systemctl --user stop x.service')
        self.assertEqual(how({'pid': 7, 'uid': me}), 'kill 7')
        self.assertEqual(how({'pid': 7, 'uid': me + 1, 'user': 'kim'}), 'kill 7 as its owner (kim), or sudo kill 7')

    def test_the_swap_decision_never_stops_a_model_that_is_not_up(self):
        decide = self.phase.decide_swap
        for mode in ('auto', 'always'):
            for free in (None, 1000, 30000):
                self.assertFalse(decide(mode, 12288, free, 'nvidia-smi', False)[0], (mode, free))
        # Up, or unknown (no health URL; WSL2 and macOS tell it by the URL alone): as before.
        for up in (True, None):
            self.assertTrue(decide('auto', 12288, 1000, 'nvidia-smi', up)[0])
            self.assertTrue(decide('auto', 12288, None, 'no nvidia-smi answer', up)[0])
            self.assertFalse(decide('auto', 12288, 30000, 'nvidia-smi', up)[0])
            self.assertTrue(decide('always', 12288, 30000, 'nvidia-smi', up)[0])
            self.assertFalse(decide('auto', 0, 1000, 'nvidia-smi', up)[0])
            self.assertFalse(decide('never', 12288, 1000, 'nvidia-smi', up)[0])

    def test_the_compute_list_is_the_runners_gpu_only(self):
        apps = 'GPU-a, 11, python3, 2000\nGPU-b, 12, /opt/train, [N/A]\n'
        gpus = '0, GPU-a, 1000, 24000\n1, GPU-b, 20000, 24000\n'

        def run(command, **kwargs):
            query = ' '.join(command)
            stdout = apps if 'compute-apps' in query else gpus
            return subprocess.CompletedProcess(command, 0, stdout, '')
        with mock.patch.object(self.phase.shutil, 'which', return_value='/fake/nvidia-smi'), \
                mock.patch.object(self.phase, 'is_wsl', return_value=False), \
                mock.patch.object(self.phase.subprocess, 'run', side_effect=run):
            with mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': '0'}):
                self.assertEqual(self.phase.compute_processes()[0], [{'pid': 11, 'name': 'python3', 'mib': 2000}])
            with mock.patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': ''}):
                # Order unknown: the fullest card, as the free memory is read.
                self.assertEqual(self.phase.compute_processes()[0], [{'pid': 12, 'name': '/opt/train', 'mib': None}])
            apps = 'No running processes found\n'
            self.assertEqual(self.phase.compute_processes()[0], [])
            apps = 'Not Supported\n'
            self.assertIsNone(self.phase.compute_processes()[0])
        with mock.patch.object(self.phase.shutil, 'which', return_value='/fake/nvidia-smi'), \
                mock.patch.object(self.phase, 'is_wsl', return_value=True):
            self.assertEqual(self.phase.compute_processes(), (None, 'WSL2: nvidia-smi lists no processes there'))
        with mock.patch.object(self.phase.shutil, 'which', return_value=None):
            self.assertEqual(self.phase.compute_processes(), (None, 'no nvidia-smi'))


if __name__ == '__main__':
    unittest.main()
