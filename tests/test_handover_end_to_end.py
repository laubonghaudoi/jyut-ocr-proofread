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
        import _setup_ocr as setup
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


if __name__ == '__main__':
    unittest.main()
