"""Offline MLX readiness, ownership and Paddle adapter integration tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import unittest
from unittest import mock

from test_local_ocr_runners import FAKE_PADDLE, LocalOCRTestBase, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
from jyut_ocr.ocr.mlx_vlm_server import MLXError, MLXServer


SERVER = r'''
import argparse, json, os, signal, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
a = argparse.ArgumentParser()
a.add_argument('--host'); a.add_argument('--port', type=int); a.add_argument('--model')
a = a.parse_args()
mode = os.environ.get('FAKE_MLX_MODE', '')
def event(text):
    with open(os.environ['FAKE_MLX_LOG'], 'a') as f: f.write(text + '\n')
def stop(*args):
    event('stopped'); raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        ready = mode != 'loading' and time.monotonic() - started > .15
        event('health-ready' if ready else 'health-loading')
        payload = {'model_ready': ready, 'loaded_model': '/wrong' if mode == 'wrong' else a.model,
                   'preload_failures': {'model': 'broken'} if mode == 'failure' else {}}
        data = json.dumps(payload).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(data)
try: server = HTTPServer((a.host, a.port), Handler)
except OSError as error:
    print(str(error), flush=True); raise SystemExit(1)
Path(os.environ['FAKE_MLX_PID']).write_text(str(os.getpid()))
event('started')
print(f'Uvicorn running on http://127.0.0.1:{a.port}', flush=True)
started = time.monotonic()
server.serve_forever()
'''


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class MLXRunnerTest(LocalOCRTestBase):
    def setUp(self):
        super().setUp()
        self.mlx_log = self.base / 'mlx-events'
        self.mlx_pid = self.base / 'mlx-pid'
        self.env.update(OCR_PADDLE_VL_BACKEND='mlx-vlm-server', MLX_VLM_PYTHON=sys.executable,
                        FAKE_MLX_LOG=str(self.mlx_log), FAKE_MLX_PID=str(self.mlx_pid))
        (self.vl_dir / 'config.json').write_text('{"torch_dtype":"bfloat16"}')
        (self.package / 'mlx').mkdir()
        (self.package / 'mlx' / '__init__.py').write_text('')
        (self.package / 'mlx' / 'core.py').write_text(
            'import os\nclass metal:\n @staticmethod\n def is_available(): '
            'return not os.environ.get("FAKE_NO_METAL")\n'
            'def default_device(): return "Device(gpu, 0)"\n')
        (self.package / 'mlx_vlm').mkdir()
        (self.package / 'mlx_vlm' / '__init__.py').write_text('')
        (self.package / 'mlx_vlm' / 'server.py').write_text(SERVER)
        dist = self.package / 'mlx_vlm-0.8.0.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: mlx-vlm\nVersion: 0.8.0\n')
        source = FAKE_PADDLE.replace('layout_detection_model_dir=None):',
                                     'layout_detection_model_dir=None, **kwargs):')
        source = source.replace('    def stopped(self):', '''        if kwargs:
            rec = self.paddlex_pipeline.vl_rec_model
            rec._genai_config = SimpleNamespace(backend=kwargs['vl_rec_backend'],
                                                server_url=kwargs['vl_rec_server_url'])
            rec.genai_client = SimpleNamespace(_model_name=kwargs['vl_rec_api_model_name'],
                                               _max_concurrency=kwargs['vl_rec_max_concurrency'])
            if os.environ.get('FAKE_MLX_BAD_CLIENT'): rec.genai_client._max_concurrency = 2
            if os.environ.get('FAKE_MLX_KWARGS'):
                Path(os.environ['FAKE_MLX_KWARGS']).write_text(json.dumps(dict(kwargs,
                    device=device, vl_rec_model_dir=vl_rec_model_dir)))
    def stopped(self):''')
        (self.package / 'paddleocr.py').write_text(source)

    def mlx_events(self):
        return self.mlx_log.read_text().splitlines() if self.mlx_log.exists() else []

    def assert_server_gone(self):
        if self.mlx_pid.exists():
            pid = int(self.mlx_pid.read_text())
            until = time.monotonic() + 5
            while alive(pid) and time.monotonic() < until:
                time.sleep(.05)
            self.assertFalse(alive(pid), f'owned server {pid} survived')

    def test_success_waits_for_model_and_reuses_completed_pages_without_server(self):
        kwargs_file = self.base / 'kwargs.json'
        result, report = self.run_cli('paddle', ['--pages', '1'],
                                    env=dict(self.env, FAKE_MLX_KWARGS=str(kwargs_file)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('health-loading', self.mlx_events())
        self.assertIn('health-ready', self.mlx_events())
        self.assert_server_gone()
        kwargs = json.loads(kwargs_file.read_text())
        self.assertEqual(kwargs['device'], 'cpu')
        self.assertEqual(kwargs['vl_rec_backend'], 'mlx-vlm-server')
        self.assertEqual(kwargs['vl_rec_max_concurrency'], 1)
        self.assertIsNone(kwargs['vl_rec_model_dir'])
        self.assertEqual(report['execution']['queueResource'], 'offline-test-gpu')
        self.assertEqual(report['execution']['vlDevice'], 'metal:gpu')
        record = self.record()
        self.assertEqual(record['layoutContract']['role'], 'layout')
        self.assertEqual(record['ocrProfile']['backendIdentity']['weightDtype'], 'bfloat16')
        before = self.mlx_events()
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 1)
        self.assertEqual(self.mlx_events(), before)

    def test_backend_switch_and_changed_weights_require_overwrite(self):
        result, _ = self.run_cli('paddle', ['--pages', '1', '--vl-backend', 'native'])
        self.assertEqual(result.returncode, 0, result.stderr)
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(report['counts']['blocked'], 1)
        self.assertFalse(self.mlx_pid.exists())
        result, _ = self.run_cli('paddle', ['--pages', '1', '--overwrite'])
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.vl_dir / 'model.safetensors').write_bytes(b'changed weights')
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(report['counts']['blocked'], 1)
        result, report = self.run_cli('paddle', ['--pages', '1', '--vl-backend', 'native'])
        self.assertEqual(report['counts']['blocked'], 1)

    def test_missing_metal_or_quantized_weights_fail_before_server(self):
        result, _ = self.run_cli('paddle', ['--pages', '1'],
                                env=dict(self.env, FAKE_NO_METAL='1'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('Metal', result.stderr)
        (self.vl_dir / 'config.json').write_text('{"torch_dtype":"bfloat16","quantization":{"bits":4}}')
        result, _ = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('unquantized', result.stderr)
        self.assertFalse(self.mlx_pid.exists())

    def test_wrong_model_preload_failure_and_client_fallback_close_owned_server(self):
        for environment in ({'FAKE_MLX_MODE': 'wrong'}, {'FAKE_MLX_MODE': 'failure'},
                            {'FAKE_MLX_BAD_CLIENT': '1'}, {'FAKE_LOAD_FAILURE': '1'}):
            with self.subTest(environment=environment):
                result, report = self.run_cli('paddle', ['--pages', '1'],
                                            env=dict(self.env, **environment))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(report['counts']['failed'], 1)
                self.assert_server_gone()

    def test_startup_phase_deadline_defers_and_cleans_up(self):
        result, report = self.run_cli('paddle', ['--pages', '1'], env=dict(
            self.env, FAKE_MLX_MODE='loading', OCR_PHASE_DEADLINE=str(time.time() + 1.3)))
        self.assertEqual(report['counts']['deferred'], 1, result.stderr)
        self.assertEqual(report['counts']['failed'], 0)
        self.assert_server_gone()

    def test_readiness_timeout_and_port_collision_do_not_adopt_existing_service(self):
        with mock.patch.dict(os.environ, self.env):
            with socket.socket() as occupied:
                occupied.bind(('127.0.0.1', 0))
                occupied.listen()
                port = occupied.getsockname()[1]
                with mock.patch('jyut_ocr.ocr.mlx_vlm_server.free_port', return_value=port):
                    with self.assertRaises(MLXError):
                        MLXServer(sys.executable, self.vl_dir, self.base / 'collision.log', ready_timeout=2)
                self.assertFalse(self.mlx_pid.exists())
            with mock.patch.dict(os.environ, {'FAKE_MLX_MODE': 'loading'}):
                with self.assertRaisesRegex(MLXError, 'not ready'):
                    MLXServer(sys.executable, self.vl_dir, self.base / 'timeout.log', ready_timeout=.6)
        self.assert_server_gone()

    def test_sigkill_of_runner_reaps_worker_and_server_on_macos_too(self):
        worker_pid = self.base / 'worker.pid'
        environment = dict(self.env, FAKE_PID_FILE=str(worker_pid),
                           FAKE_RELEASE_FILE=str(self.base / 'never-release'))
        command = [sys.executable, str(SCRIPTS / 'run_paddleocr_vl.py'), str(self.renders),
                   str(self.output), '--pages', '1', '--report', str(self.report)]
        with subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as runner:
            try:
                until = time.monotonic() + 8
                while not worker_pid.exists() and runner.poll() is None and time.monotonic() < until:
                    time.sleep(.05)
                self.assertTrue(worker_pid.exists())
                runner.kill()
                runner.wait(timeout=5)
                self.assert_server_gone()
                pid = int(worker_pid.read_text())
                until = time.monotonic() + 5
                while alive(pid) and time.monotonic() < until:
                    time.sleep(.05)
                self.assertFalse(alive(pid), f'owned Paddle worker {pid} survived')
            finally:
                if runner.poll() is None:
                    runner.kill()
                runner.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
