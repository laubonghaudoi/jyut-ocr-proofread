"""Offline CLI integration tests: fake HTTP server/Paddle package, no GPU or downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RAW_TEXT = "  輙與弑\n\n[原書符號]  \n"

FAKE_SERVER = r'''
import argparse, base64, json, os, signal, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int)
parser.add_argument('--alias')
a, _ = parser.parse_known_args()
if os.environ.get('FAKE_ARGV_FILE'): Path(os.environ['FAKE_ARGV_FILE']).write_text(json.dumps(sys.argv[1:]))
log = Path(os.environ['FAKE_LOG'])
def record(event):
    label = os.environ.get('FAKE_LABEL')
    with log.open('a') as f: f.write((label + ':' if label else '') + event + '\n')
def stop(signum, frame):
    record('stopped'); raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        self.reply({'data': [{'id': a.alias}]})
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        uri = payload['messages'][0]['content'][0]['image_url']['url']
        page = int(base64.b64decode(uri.split(',')[1]).decode().split()[-1])
        record('page:' + str(page))
        gate = os.environ.get('FAKE_RELEASE_FILE')
        while gate and not Path(gate).exists(): time.sleep(0.01)
        if os.environ.get('FAKE_MALFORMED'):
            self.reply({'invalid': True}); return
        reason = 'length' if str(page) == os.environ.get('FAKE_BAD_PAGE') else 'stop'
        self.reply({'model': os.environ.get('FAKE_RESPONSE_MODEL', a.alias),
                    'choices': [{'finish_reason': reason,
                                 'message': {'content': '  輙與弑\n\n[原書符號]  \n'}}],
                    'usage': {'completion_tokens': 15}})
record('started')
if os.environ.get('FAKE_PID_FILE'): Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()))
HTTPServer(('127.0.0.1', a.port), Handler).serve_forever()
'''

FAKE_PADDLE = r'''
import atexit, json, os, time
from pathlib import Path
__version__ = os.environ.get('FAKE_PADDLE_VERSION', 'offline-v1')
def record(event):
    label = os.environ.get('FAKE_LABEL')
    with open(os.environ['FAKE_LOG'], 'a') as f: f.write((label + ':' if label else '') + event + '\n')
class Result:
    def __init__(self, result): self.json = {'res': result}
class PaddleOCRVL:
    def __init__(self, pipeline_version):
        self.version = pipeline_version
        record('started:' + pipeline_version)
        if os.environ.get('FAKE_PID_FILE'): Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()))
        atexit.register(self.stopped)
        if os.environ.get('FAKE_LOAD_FAILURE'): raise RuntimeError('fake load failure')
    def stopped(self):
        record('stopped')
    def predict(self, render):
        page = int(Path(render).read_text().split()[-1])
        record('page:' + str(page))
        gate = os.environ.get('FAKE_RELEASE_FILE')
        while gate and not Path(gate).exists(): time.sleep(0.01)
        if str(page) == os.environ.get('FAKE_BAD_PAGE'): raise RuntimeError('fake page failure')
        return [Result({'width': 100, 'height': 200, 'pipeline': self.version,
                        'parsing_res_list': [
                            {'block_order': 0, 'block_content': '  輙與弑\n',
                             'block_bbox': [10, 10, 90, 40], 'block_label': 'text'},
                            {'block_order': None, 'block_content': '[原書符號]',
                             'block_bbox': [10, 50, 90, 80], 'block_label': 'table'},
                            {'block_order': None, 'block_content': '',
                             'block_bbox': [10, 90, 90, 150], 'block_label': 'image'}],
                        'layout_det_res': {'boxes': [{'order': 0, 'score': 0.8}]}})]
'''


class LocalOCRTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.renders = self.base / 'renders'
        self.renders.mkdir()
        for page in range(1, 4):
            (self.renders / f'page-{page:04d}.png').write_text(f'scan {page}')
        self.output = self.base / 'draft'
        self.report = self.base / 'report.json'
        self.log = self.base / 'process.log'
        self.env = dict(os.environ, FAKE_LOG=str(self.log))
        for key in ('HUNYUAN_MODEL', 'HUNYUAN_MMPROJ', 'HUNYUAN_LLAMA_SERVER',
                    'HUNYUAN_ENDPOINT', 'OCR_PHASE_DEADLINE', 'OCR_PHASE_PROGRESS',
                    'PADDLEOCR_PYTHON', 'PADDLEOCR_MODEL_REVISION', 'OCR_HUNYUAN_DEVICE'):
            self.env.pop(key, None)
        self.queue_dir = self.base / 'gpu-queue'
        self.env['OCR_GPU_QUEUE_DIR'] = str(self.queue_dir)
        self.env['OCR_GPU_RESOURCE'] = 'offline-test-gpu'
        # These tests exercise the explicit legacy owned-batch lifecycle.
        # Shared multi-client behavior has its own integration cases.
        self.env['OCR_HUNYUAN_MODE'] = 'dedicated'
        self.env['OCR_HUNYUAN_SLOTS'] = '1'
        self.binary = self.base / 'fake-llama-server'
        self.binary.write_text('#!' + sys.executable + '\n' + textwrap.dedent(FAKE_SERVER))
        self.binary.chmod(0o755)
        self.model = self.base / 'model.gguf'
        self.proj = self.base / 'mmproj.gguf'
        self.model.write_bytes(b'offline fake model')
        self.proj.write_bytes(b'offline fake projector')
        package = self.base / 'fake-package'
        package.mkdir()
        (package / 'paddleocr.py').write_text(textwrap.dedent(FAKE_PADDLE))
        self.env['PYTHONPATH'] = str(package)

    def run_cli(self, engine, extra=(), *, env=None, configured=True):
        script = 'run_hunyuanocr.py' if engine == 'hunyuan' else 'run_paddleocr_vl.py'
        args = [sys.executable, str(SCRIPTS / script), str(self.renders), str(self.output),
                '--report', str(self.report)]
        if configured and engine == 'hunyuan':
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            args += ['--model', str(self.model), '--mmproj', str(self.proj),
                     '--llama-server', str(self.binary), '--port', str(port)]
        args += list(extra)
        process = subprocess.run(args, env=env or self.env, capture_output=True,
                                 text=True, timeout=15)
        self.assertNotIn('Traceback', process.stderr, process.stderr)
        report = json.loads(self.report.read_text()) if self.report.exists() else None
        return process, report

    def events(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def record(self, page=1):
        return json.loads((self.output / f'page-{page:04d}.json').read_text())


class LocalOCRCLITest(LocalOCRTestBase):
    def test_device_policy_controls_decoder_and_projector_and_invalidates_resume(self):
        argv_file = self.base / 'argv.json'
        for device, selector, layers, projector in (
            ('cpu', 'none', '0', '--no-mmproj-offload'),
            ('cuda', 'CUDA0', '99', '--mmproj-offload'),
            ('metal', 'MTL0', '99', '--mmproj-offload'),
        ):
            with self.subTest(device=device):
                result, report = self.run_cli(
                    'hunyuan', ['--pages', '1', '--device', device, '--overwrite'],
                    env=dict(self.env, FAKE_ARGV_FILE=str(argv_file)))
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(argv_file.read_text())
                self.assertEqual(args[args.index('--device') + 1], selector)
                self.assertEqual(args[args.index('--mmproj-device') + 1], selector)
                self.assertEqual(args[args.index('--n-gpu-layers') + 1], layers)
                self.assertIn(projector, args)
                self.assertIn('--no-kv-offload' if device == 'cpu' else '--kv-offload', args)
                profile = report['ocrProfile']['deployment']['executionProfile']
                self.assertEqual(profile['requestedDevice'], device)
                self.assertEqual(profile['effectiveDevice'], device)
                self.assertEqual(profile['projectorOffload'], device != 'cpu')
                other = 'cpu' if device != 'cpu' else 'cuda'
                stale, stale_report = self.run_cli('hunyuan', ['--pages', '1', '--device', other])
                self.assertEqual(stale.returncode, 1)
                self.assertEqual(stale_report['counts']['blocked'], 1)

    def test_fresh_slot_default_and_existing_environment_override(self):
        sys.path.insert(0, str(SCRIPTS))
        import run_hunyuanocr as hy
        import run_hunyuanocr_second_look as second
        for module, positional in ((hy, ['renders', 'drafts']), (second, ['renders', 'a', 'b'])):
            with self.subTest(runner=module.__name__):
                with mock.patch.dict(os.environ, {}, clear=True):
                    args = module.build_parser().parse_args(positional)
                    self.assertEqual(args.server_slots, 1)
                    self.assertEqual(args.device, 'auto')
                with mock.patch.dict(os.environ, {'OCR_HUNYUAN_SLOTS': '16', 'OCR_HUNYUAN_DEVICE': 'cpu'}, clear=True):
                    args = module.build_parser().parse_args(positional)
                    self.assertEqual(args.server_slots, 16)
                    self.assertEqual(args.device, 'cpu')

    def test_projector_environment_change_invalidates_existing_draft(self):
        result, _ = self.run_cli('hunyuan', ['--pages', '1'], env=dict(self.env, MTMD_BACKEND_DEVICE='CUDA0'))
        self.assertEqual(result.returncode, 0, result.stderr)
        result, report = self.run_cli('hunyuan', ['--pages', '1'], env=dict(self.env, MTMD_BACKEND_DEVICE='none'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['blocked'], 1)

    def test_hunyuan_selected_pages_raw_preserved_owned_server_closed_and_resume(self):
        result, report = self.run_cli('hunyuan', ['--pages', '1,3'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p['scanPage'] for p in report['pages']], [1, 3])
        self.assertEqual(self.events(), ['started', 'page:1', 'page:3', 'stopped'])
        self.assertFalse((self.output / 'page-0002.json').exists())
        record = self.record()
        self.assertEqual(record['rawText'], RAW_TEXT)
        self.assertEqual(record['rawResponse']['choices'][0]['message']['content'], RAW_TEXT)
        self.assertEqual(record['textAuthority'], 'draft-only-not-text-authority')
        self.assertEqual(record['lines'][0]['blockLabel'], 'whole-page')
        before = (self.output / 'page-0001.json').read_bytes()
        result, report = self.run_cli('hunyuan', ['--pages', '1,3'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 2)
        self.assertEqual(len(self.events()), 4)  # no second server, no requests
        self.assertEqual((self.output / 'page-0001.json').read_bytes(), before)

    def test_hunyuan_stale_partial_and_modified_json_rejected(self):
        self.run_cli('hunyuan', ['--pages', '1-3'])
        (self.renders / 'page-0001.png').write_text('changed scan 1')
        (self.output / 'page-0002.txt').unlink()
        (self.output / 'page-0003.json').write_text('[]')
        result, report = self.run_cli('hunyuan')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['blocked'], 3)
        self.assertEqual(self.events().count('started'), 1)
        result, report = self.run_cli('hunyuan', ['--overwrite'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['completed'], 3)

    def test_hunyuan_settings_and_weight_changes_invalidate_resume(self):
        self.run_cli('hunyuan', ['--pages', '1'])
        for args in (['--max-tokens', '100'], ['--ctx-size', '4096']):
            with self.subTest(args=args):
                result, report = self.run_cli('hunyuan', ['--pages', '1', *args])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['counts']['blocked'], 1)
        self.model.write_bytes(b'changed model bytes')
        result, report = self.run_cli('hunyuan', ['--pages', '1'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['blocked'], 1)

    def test_hunyuan_truncated_response_not_sealed_and_other_pages_continue(self):
        env = dict(self.env, FAKE_BAD_PAGE='2')
        result, report = self.run_cli('hunyuan', env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['completed'], 2)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertIn('finish_reason=length', report['pages'][1]['error'])
        self.assertFalse((self.output / 'page-0002.json').exists())
        self.assertFalse((self.output / 'page-0002.txt').exists())
        self.assertEqual(self.events()[-1], 'stopped')

    def test_hunyuan_mismatched_or_malformed_response_is_page_failure(self):
        for setting in ({'FAKE_RESPONSE_MODEL': 'wrong'}, {'FAKE_MALFORMED': '1'}):
            with self.subTest(setting=setting):
                result, report = self.run_cli('hunyuan', ['--pages', '1'],
                                              env=dict(self.env, **setting))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['counts']['failed'], 1)
                self.assertFalse((self.output / 'page-0001.json').exists())

    def test_hunyuan_missing_config_and_occupied_port_do_not_take_over(self):
        result, _ = self.run_cli('hunyuan', configured=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('need --model', result.stderr)
        self.assertEqual(self.events(), [])
        with socket.socket() as owner:
            owner.bind(('127.0.0.1', 0)); owner.listen()
            port = owner.getsockname()[1]
            result, report = self.run_cli('hunyuan', ['--pages', '1', '--port', str(port)])
            self.assertEqual(result.returncode, 1)
            self.assertIn('unavailable', report['pages'][0]['error'])
            self.assertGreater(owner.fileno(), -1)
            self.assertEqual(self.events(), [])

    def test_hunyuan_endpoint_requires_identity_and_never_closes_external_server(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                payload = json.dumps({'data': [{'id': 'fake-external'}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(payload)
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                payload = json.dumps({'model': 'fake-external', 'choices': [
                    {'finish_reason': 'stop', 'message': {'content': RAW_TEXT}}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(payload)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            args = ['--pages', '1', '--endpoint', f'http://127.0.0.1:{server.server_port}']
            result, _ = self.run_cli('hunyuan', args, configured=False)
            self.assertEqual(result.returncode, 2)
            result, report = self.run_cli('hunyuan', [*args, '--endpoint-model', 'fake-external',
                                          '--endpoint-revision', 'deployment-a'], configured=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(thread.is_alive())
            self.assertIsNone(report['tool']['modelSha256'])
            self.assertEqual(report['ocrProfile']['deployment']['mode'], 'external-endpoint')
            result, report = self.run_cli('hunyuan', [*args, '--endpoint-model', 'fake-external',
                                          '--endpoint-revision', 'deployment-b'], configured=False)
            self.assertEqual(report['counts']['blocked'], 1)
            self.assertEqual(self.events(), [])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_paddle_selected_pages_raw_preserved_closed_and_resume(self):
        result, report = self.run_cli('paddle', ['--pages', '1,3'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['completed'], 2)
        self.assertEqual(self.events(), ['started:v1.6', 'page:1', 'page:3', 'stopped'])
        record = self.record()
        raw_blocks = record['rawResult']['parsing_res_list']
        self.assertEqual(raw_blocks[0]['block_content'], '  輙與弑\n')
        self.assertNotIn('_geometric_order', raw_blocks[1])
        self.assertEqual(record['regions'][0]['blockLabel'], 'image')
        self.assertEqual(record['lines'][0]['boundingBox'], [0.1, 0.05, 0.8, 0.15])
        result, report = self.run_cli('paddle', ['--pages', '1,3'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 2)
        self.assertEqual(len(self.events()), 4)

    def test_paddle_stale_partial_and_modified_json_rejected(self):
        self.run_cli('paddle')
        (self.renders / 'page-0001.png').write_text('changed scan 1')
        (self.output / 'page-0002.txt').unlink()
        (self.output / 'page-0003.json').write_text('[]')
        result, report = self.run_cli('paddle')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['blocked'], 3)
        self.assertEqual(self.events().count('started:v1.6'), 1)
        result, report = self.run_cli('paddle', ['--overwrite'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['completed'], 3)

    def test_paddle_runtime_pipeline_revision_changes_invalidate_resume(self):
        self.run_cli('paddle', ['--pages', '1'])
        for extra, env in ((['--pipeline-version', 'v1.5'], self.env),
                           (['--model-revision', 'cache-new'], self.env),
                           ([], dict(self.env, FAKE_PADDLE_VERSION='offline-v2'))):
            with self.subTest(extra=extra, version=env.get('FAKE_PADDLE_VERSION')):
                result, report = self.run_cli('paddle', ['--pages', '1', *extra], env=env)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['counts']['blocked'], 1)
        result, report = self.run_cli('paddle', ['--pages', '1', '--overwrite', '--pipeline-version', 'v1.5'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()['rawResult']['pipeline'], 'v1.5')

    def test_paddle_failed_page_report_and_load_failure_close_worker(self):
        result, report = self.run_cli('paddle', env=dict(self.env, FAKE_BAD_PAGE='2'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['completed'], 2)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertFalse((self.output / 'page-0002.json').exists())
        self.assertEqual(self.events()[-1], 'stopped')
        result, report = self.run_cli('paddle', ['--pages', '2'],
                                      env=dict(self.env, FAKE_LOAD_FAILURE='1'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertIn('fake load failure', report['pages'][0]['error'])
        self.assertEqual(self.events()[-1], 'stopped')

    def test_paddle_partial_response_times_out_and_owned_worker_dies(self):
        worker = self.base / 'partial_worker.py'
        worker.write_text("import json, os, sys, time\n"
                          "from pathlib import Path\n"
                          "Path(os.environ['FAKE_LOG']).write_text(str(os.getpid()))\n"
                          "print(json.dumps({'ok': True, 'ready': True}), flush=True)\n"
                          "sys.stdin.readline()\n"
                          "sys.stdout.write('{'); sys.stdout.flush()\n"
                          "time.sleep(30)\n")
        result, report = self.run_cli('paddle', ['--pages', '1', '--worker-source', str(worker),
                                                '--timeout', '0.1'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertIn('timed out', report['pages'][0]['error'])
        self.assertFalse((self.output / 'page-0001.json').exists())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(self.log.read_text()), 0)

    def test_missing_render_fails_and_invalid_page_ranges_are_configuration_errors(self):
        for engine in ('hunyuan', 'paddle'):
            with self.subTest(engine=engine):
                result, report = self.run_cli(engine, ['--pages', '4'])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['pages'][0]['status'], 'failed')
                self.assertEqual(report['counts']['failed'], 1)
                result, _ = self.run_cli(engine, ['--first-page', '0', '--last-page', '1'])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.events(), [])


class GPUQueueRunnerTest(LocalOCRTestBase):
    """Separate CLI processes compete for the same fake GPU, never real models."""

    def setUp(self):
        super().setUp()
        self.processes = []
        self.addCleanup(self.stop_processes)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]

    def stop_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)

    def launch(self, engine, label, *, hold=False, extra=(), env=None, output=None):
        script = 'run_hunyuanocr.py' if engine == 'hunyuan' else 'run_paddleocr_vl.py'
        command = [sys.executable, str(SCRIPTS / script), str(self.renders),
                   str(output or self.base / ('draft-' + label)), '--pages', '1',
                   '--report', str(self.base / ('report-' + label + '.json'))]
        if engine == 'hunyuan':
            command += ['--model', str(self.model), '--mmproj', str(self.proj),
                        '--llama-server', str(self.binary), '--port', str(self.port)]
        command += list(extra)
        environment = dict(self.env, FAKE_LABEL=label,
                           FAKE_PID_FILE=str(self.base / ('pid-' + label)))
        if hold:
            environment['FAKE_RELEASE_FILE'] = str(self.base / ('release-' + label))
        environment.update(env or {})
        process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def wait_event(self, event, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event in self.events():
                return
            time.sleep(0.01)
        self.fail(f'missing {event!r}; events={self.events()}')

    def wait_tickets(self, count, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(list(self.queue_dir.rglob('*.ticket'))) >= count:
                return
            time.sleep(0.01)
        self.fail(f'expected at least {count} enrolled queue tickets')

    def release(self, label):
        (self.base / ('release-' + label)).touch()

    def finish(self, process, expected=0):
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, expected, stdout + stderr)
        self.assertNotIn('Traceback', stderr)
        return stdout, stderr

    def test_owned_hunyuan_same_port_serializes_and_releases_before_session_finishes(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        second = self.launch('hunyuan', 'B')
        self.wait_tickets(2)
        self.assertIsNone(second.poll())
        self.assertNotIn('B:started', self.events())
        self.release('A')
        self.finish(first)
        self.finish(second)
        self.assertEqual(self.events(), ['A:started', 'A:page:1', 'A:stopped',
                                        'B:started', 'B:page:1', 'B:stopped'])
        # The test/session is still alive. The lease ends with the OCR process.
        third = self.launch('hunyuan', 'C', extra=['--gpu-queue-timeout', '0'])
        self.finish(third)

    def test_hunyuan_and_paddle_share_one_queue(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        second = self.launch('paddle', 'B')
        self.wait_tickets(2)
        self.assertNotIn('B:started:v1.6', self.events())
        self.release('A')
        self.finish(first)
        self.finish(second)
        self.assertLess(self.events().index('A:stopped'), self.events().index('B:started:v1.6'))

    def test_queue_wait_timeout_and_phase_deadline_defer_without_loading(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        for engine in ('hunyuan', 'paddle'):
            for mode in ('timeout', 'deadline'):
                with self.subTest(engine=engine, mode=mode):
                    label = engine + '-' + mode
                    extra = ['--gpu-queue-timeout', '0.1'] if mode == 'timeout' else []
                    env = {'OCR_PHASE_DEADLINE': str(time.time() + 0.2)} if mode == 'deadline' else {}
                    process = self.launch(engine, label, extra=extra, env=env)
                    self.finish(process, 75)
                    report = json.loads((self.base / ('report-' + label + '.json')).read_text())
                    self.assertEqual(report['counts']['deferred'], 1)
                    self.assertEqual(report['counts']['failed'], 0)
                    self.assertFalse((self.base / ('pid-' + label)).exists())
        self.release('A')
        self.finish(first)
        self.finish(self.launch('paddle', 'after', extra=['--gpu-queue-timeout', '0']))

    def test_cancelled_waiter_does_not_load_or_prevent_next_job(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        cancelled = self.launch('paddle', 'cancelled')
        self.wait_tickets(2)
        cancelled.terminate()
        cancelled.communicate(timeout=5)
        self.assertFalse((self.base / 'pid-cancelled').exists())
        following = self.launch('hunyuan', 'B')
        self.release('A')
        self.finish(first)
        self.finish(following)

    def test_owner_sigterm_closes_owned_child_before_successor_starts(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        second = self.launch('paddle', 'B')
        self.wait_tickets(2)
        first.terminate()
        first.communicate(timeout=5)
        self.assertNotEqual(first.returncode, 0)
        self.finish(second)
        self.assertLess(self.events().index('A:stopped'), self.events().index('B:started:v1.6'))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'parent-death signals are Linux-only')
    def test_owner_sigkill_does_not_leave_gpu_child_or_stale_queue_lock(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        child_pid = int((self.base / 'pid-A').read_text())
        first.kill()
        first.communicate(timeout=5)
        self.finish(self.launch('paddle', 'B'))
        # Reparented dead children can briefly remain zombies; neither holds GPU memory.
        stat = Path('/proc') / str(child_pid) / 'stat'
        self.assertTrue(not stat.exists() or stat.read_text().split(') ', 1)[1].startswith('Z '))

    def test_current_resume_skips_busy_queue(self):
        output = self.base / 'resumable'
        self.finish(self.launch('hunyuan', 'seed', output=output))
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        resumed = self.launch('hunyuan', 'resume', output=output,
                              extra=['--gpu-queue-timeout', '0'])
        self.finish(resumed)
        report = json.loads((self.base / 'report-resume.json').read_text())
        self.assertEqual(report['counts']['skippedCurrent'], 1)
        self.assertFalse((self.base / 'pid-resume').exists())
        self.release('A')
        self.finish(first)

    def test_backend_failure_closes_child_and_releases_queue(self):
        for engine, failure in (('hunyuan', {'FAKE_MALFORMED': '1'}),
                                ('paddle', {'FAKE_LOAD_FAILURE': '1'})):
            with self.subTest(engine=engine):
                failed = self.launch(engine, engine + '-failed', env=failure)
                self.finish(failed, 1)
                following = self.launch(engine, engine + '-following',
                                        extra=['--gpu-queue-timeout', '0'])
                self.finish(following)

    def test_invalid_queue_timeout_is_configuration_error_without_child(self):
        for engine in ('hunyuan', 'paddle'):
            for timeout in ('-1', 'nan', 'inf'):
                with self.subTest(engine=engine, timeout=timeout):
                    label = engine + '-' + timeout
                    process = self.launch(engine, label, extra=['--gpu-queue-timeout', timeout])
                    self.finish(process, 2)
                    self.assertFalse((self.base / ('pid-' + label)).exists())

    def test_external_endpoint_skips_busy_owned_queue_and_is_not_stopped(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                payload = json.dumps({'data': [{'id': 'external'}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(payload)
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                payload = json.dumps({'model': 'external', 'choices': [
                    {'finish_reason': 'stop', 'message': {'content': RAW_TEXT}}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(payload)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        first = self.launch('hunyuan', 'A', hold=True)
        try:
            self.wait_event('A:page:1')
            external = self.launch('hunyuan', 'external', extra=[
                '--endpoint', f'http://127.0.0.1:{server.server_port}',
                '--endpoint-model', 'external', '--endpoint-revision', 'test-deployment',
                '--gpu-queue-timeout', '0'])
            self.finish(external)
            self.assertTrue(thread.is_alive())
            self.assertFalse((self.base / 'pid-external').exists())
            self.assertIsNone(first.poll())
        finally:
            self.release('A')
            server.shutdown(); server.server_close(); thread.join(timeout=2)
        self.finish(first)


if __name__ == '__main__':
    unittest.main()
