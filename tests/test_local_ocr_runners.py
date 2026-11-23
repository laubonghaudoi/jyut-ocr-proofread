"""Offline CLI integration tests: fake HTTP server/Paddle package, no GPU or downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
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
import atexit, json, os, sys, time
from pathlib import Path
from types import SimpleNamespace
__version__ = os.environ.get('FAKE_PADDLE_VERSION', 'offline-v1')
def record(event):
    label = os.environ.get('FAKE_LABEL')
    with open(os.environ['FAKE_LOG'], 'a') as f: f.write((label + ':' if label else '') + event + '\n')
class Result:
    def __init__(self, result): self.json = {'res': result}
class PaddleOCRVL:
    def __init__(self, pipeline_version, device=None, vl_rec_model_dir=None,
                 layout_detection_model_dir=None):
        self.version = pipeline_version
        record('started:' + pipeline_version)
        print('fake paddlex: creating models on ' + str(device))
        sys.stderr.write('fake paddlex: stderr line\n')
        if os.environ.get('FAKE_KWARGS_FILE'):
            Path(os.environ['FAKE_KWARGS_FILE']).write_text(json.dumps({
                'device': device, 'vl_rec_model_dir': vl_rec_model_dir,
                'layout_detection_model_dir': layout_detection_model_dir,
                'env': {key: os.environ.get(key) for key in (
                    'CUDA_VISIBLE_DEVICES', 'PADDLE_PDX_DISABLE_DEVICE_FALLBACK',
                    'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'FLAGS_gpu_memory_limit_mb')}}))
        if os.environ.get('FAKE_PID_FILE'): Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()))
        atexit.register(self.stopped)
        if os.environ.get('FAKE_LOAD_FAILURE'): raise RuntimeError('fake load failure')
        # PaddleX's predictors: where the models really went (FAKE_PADDLE_FALLBACK
        # plays a silent CPU fallback).
        loaded = 'cpu' if os.environ.get('FAKE_PADDLE_FALLBACK') else (device or 'cpu')
        kind, _, index = loaded.partition(':')
        self.paddlex_pipeline = SimpleNamespace(
            vl_rec_model=SimpleNamespace(device=loaded, dtype='bfloat16' if kind == 'gpu' else 'float32'),
            layout_det_model=SimpleNamespace(runner=SimpleNamespace(_config={
                'device_type': kind, 'device_id': int(index) if index else None})))
    def stopped(self):
        record('stopped')
    def predict(self, render):
        page = int(Path(render).read_text().split()[-1])
        record('page:' + str(page))
        gate = os.environ.get('FAKE_RELEASE_FILE')
        while gate and not Path(gate).exists(): time.sleep(0.01)
        if str(page) == os.environ.get('FAKE_BAD_PAGE'): raise RuntimeError('fake page failure')
        # Several workers: who read the page, a page that takes its worker with it (once, or
        # every time), and pages slow enough for two workers to share them.
        if os.environ.get('FAKE_READER_DIR'):
            Path(os.environ['FAKE_READER_DIR'], 'page-%d-%d' % (page, os.getpid())).write_text('')
        if str(page) == os.environ.get('FAKE_DEADLY_PAGE'):
            once = os.environ.get('FAKE_DEADLY_ONCE')
            if not once or not Path(once).exists():
                if once: Path(once).write_text('')
                os._exit(3)
        # A page too large for a worker's share of the card: it takes every worker that has a ceiling.
        if str(page) == os.environ.get('FAKE_TOO_LARGE_PAGE') and os.environ.get('FLAGS_gpu_memory_limit_mb'):
            os._exit(3)
        time.sleep(float(os.environ.get('FAKE_PAGE_SECONDS', '0')))
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

# The two calls PaddleX's device test makes (utils/device.get_default_device).
# FAKE_WORKER_CUDA_DEVICES is what the worker alone sees (a card gone between
# the runner's probe and the model load).
FAKE_PADDLE_CORE = r'''
import os
class _Cuda:
    @staticmethod
    def device_count():
        if os.environ.get('JYUT_PADDLE_DEVICE') and 'FAKE_WORKER_CUDA_DEVICES' in os.environ:
            return int(os.environ['FAKE_WORKER_CUDA_DEVICES'])
        return int(os.environ.get('FAKE_CUDA_DEVICES', '0'))
class device:
    cuda = _Cuda
    @staticmethod
    def is_compiled_with_cuda():
        return os.environ.get('FAKE_CUDA_BUILD') == '1'
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
                    'PADDLEOCR_PYTHON', 'PADDLEOCR_MODEL_REVISION', 'OCR_HUNYUAN_DEVICE',
                    'OCR_PHASE_RELEASE_CMD', 'OCR_PHASE_WINDOW', 'OCR_PHASE_SWAP',
                    'OCR_PHASE_STARTUP_SECONDS', 'OCR_PHASE_CHILDREN',
                    'OCR_PADDLE_DEVICE', 'OCR_CPU_RESOURCE', 'OCR_PADDLE_GPU_RESOURCE',
                    'CUDA_VISIBLE_DEVICES'):
            self.env.pop(key, None)
        self.queue_dir = self.base / 'gpu-queue'
        self.env['OCR_GPU_QUEUE_DIR'] = str(self.queue_dir)
        self.env['OCR_GPU_RESOURCE'] = 'offline-test-gpu'
        # These tests exercise the explicit legacy owned-batch lifecycle.
        # Shared multi-client behavior has its own integration cases.
        self.env['OCR_HUNYUAN_MODE'] = 'dedicated'
        self.env['OCR_HUNYUAN_SLOTS'] = '1'
        self.binary = self.base / 'fake-llama-server'
        self.binary.with_suffix('.py').write_text(textwrap.dedent(FAKE_SERVER), encoding='utf-8')
        self.binary.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$0.py\" \"$@\"\n", encoding='utf-8')
        self.binary.chmod(0o755)
        self.model = self.base / 'model.gguf'
        self.proj = self.base / 'mmproj.gguf'
        self.model.write_bytes(b'offline fake model')
        self.proj.write_bytes(b'offline fake projector')
        package = self.base / 'fake-package'
        package.mkdir()
        (package / 'paddleocr.py').write_text(textwrap.dedent(FAKE_PADDLE))
        (package / 'paddle').mkdir()
        (package / 'paddle' / '__init__.py').write_text(textwrap.dedent(FAKE_PADDLE_CORE))
        self.package = package
        # The installer's pinned weight folders (PADDLE_*_MODEL_DIR); PaddleX's
        # own cache is an empty folder unless a test fills it.
        self.vl_dir = self.model_folder('PaddleOCR-VL-1.6', 'PaddleOCR-VL-1.6-0.9B', 'model.safetensors')
        self.layout_dir = self.model_folder('PP-DocLayoutV3', 'PP-DocLayoutV3', 'inference.pdiparams')
        self.env['PADDLE_VL_MODEL_DIR'] = str(self.vl_dir)
        self.env['PADDLE_LAYOUT_MODEL_DIR'] = str(self.layout_dir)
        self.env['PADDLE_PDX_CACHE_HOME'] = str(self.base / 'paddlex-cache')
        self.paddle_build('cpu')

    def model_folder(self, name, model_name, weights, root=None):
        folder = (root or self.base / 'paddle-models') / name
        folder.mkdir(parents=True)
        (folder / 'inference.yml').write_text(f'Global:\n  model_name: {model_name}\n')
        (folder / weights).write_bytes(b'offline fake weights')
        return folder

    def paddle_build(self, kind, devices=0):
        """Which fake Paddle package is installed: 'cpu' (paddlepaddle) or 'gpu'
        (paddlepaddle-gpu seeing DEVICES cards)."""
        name = 'paddlepaddle_gpu' if kind == 'gpu' else 'paddlepaddle'
        dist = self.base / f'dist-{kind}' / f'{name}-3.3.1.dist-info'
        if not dist.exists():
            dist.mkdir(parents=True)
            (dist / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name.replace("_", "-")}\n'
                                           'Version: 3.3.1\n')
        self.env['PYTHONPATH'] = os.pathsep.join([str(self.package), str(dist.parent)])
        self.env['FAKE_CUDA_BUILD'] = '1' if kind == 'gpu' else '0'
        self.env['FAKE_CUDA_DEVICES'] = str(devices)

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
    def test_device_policy_controls_decoder_and_projector_and_is_recorded_not_compared(self):
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
                self.assertEqual(self.record()['ocrProfile']['deployment']['executionProfile']
                                 ['requestedDevice'], device)
                # The device is deployment: recorded in the draft, not part of
                # its identity, so another device resumes the finished page.
                other = 'cpu' if device != 'cpu' else 'cuda'
                again, again_report = self.run_cli('hunyuan', ['--pages', '1', '--device', other])
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(again_report['counts']['skippedCurrent'], 1)
                self.assertEqual(self.record()['ocrProfile']['deployment']['executionProfile']
                                 ['requestedDevice'], device)

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

    def test_projector_environment_change_is_recorded_not_compared(self):
        result, _ = self.run_cli('hunyuan', ['--pages', '1'], env=dict(self.env, MTMD_BACKEND_DEVICE='CUDA0'))
        self.assertEqual(result.returncode, 0, result.stderr)
        selectors = self.record()['ocrProfile']['deployment']['executionProfile']['runtimeSelectors']
        self.assertEqual(selectors['MTMD_BACKEND_DEVICE'], 'CUDA0')
        result, report = self.run_cli('hunyuan', ['--pages', '1'], env=dict(self.env, MTMD_BACKEND_DEVICE='none'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 1)

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

    def test_hunyuan_request_settings_and_weight_changes_invalidate_resume(self):
        self.run_cli('hunyuan', ['--pages', '1'])
        result, report = self.run_cli('hunyuan', ['--pages', '1', '--max-tokens', '100'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['blocked'], 1)
        self.assertIn('draft identity', report['pages'][0]['error'])
        for path, content in ((self.model, b'changed model bytes'), (self.proj, b'changed projector')):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                path.write_bytes(content)
                result, report = self.run_cli('hunyuan', ['--pages', '1'])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['counts']['blocked'], 1)
                path.write_bytes(original)

    def test_hunyuan_deployment_changes_keep_drafts_current_but_a_new_runtime_does_not(self):
        # Deployment (context, slots, workers) is recorded in the draft but is
        # not its identity.  The runtime is (the user's decision of
        # 2026-11-14, changed from A3's reading): a rebuilt or re-pinned
        # llama-server makes the draft stale, so two builds' drafts are not
        # mixed in one book.
        self.run_cli('hunyuan', ['--pages', '1'])
        for args in (['--ctx-size', '4096'], ['--server-slots', '2'],
                     ['--server-slots', '2', '--workers', '1']):
            with self.subTest(args=args):
                result, report = self.run_cli('hunyuan', ['--pages', '1', *args])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report['counts']['skippedCurrent'], 1)
        self.assertEqual(self.events().count('started'), 1)
        with self.binary.open('a') as handle:
            handle.write('# rebuilt runtime\n')
        result, report = self.run_cli('hunyuan', ['--pages', '1'])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual((report['counts']['skippedCurrent'], report['counts']['blocked']), (0, 1))
        self.assertIn('runtime', report['pages'][0]['error'])
        self.assertEqual(self.events().count('started'), 1)

    def test_hunyuan_truncated_response_kept_as_draft_and_reported(self):
        env = dict(self.env, FAKE_BAD_PAGE='2')
        result, report = self.run_cli('hunyuan', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['completed'], 3)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(report['counts']['truncated'], 1)
        self.assertTrue(report['pages'][1]['truncated'])
        self.assertEqual(report['pages'][1]['finishReason'], 'length')
        self.assertNotIn('truncated', report['pages'][0])
        self.assertIn('TRUNCATED', result.stdout)
        record = self.record(2)
        self.assertTrue(record['truncated'])
        self.assertEqual(record['finishReason'], 'length')
        self.assertEqual(record['rawText'], RAW_TEXT)
        self.assertEqual((self.output / 'page-0002.txt').read_text(), '  輙與弑\n[原書符號]\n')
        self.assertFalse(self.record(1)['truncated'])
        self.assertEqual(self.record(1)['finishReason'], 'stop')
        self.assertEqual(self.events()[-1], 'stopped')
        result, report = self.run_cli('hunyuan')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 3)
        self.assertEqual(report['counts']['truncated'], 1)
        self.assertTrue(report['pages'][1]['truncated'])

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

    def test_the_server_keeps_no_cache_in_system_memory_where_it_has_the_options(self):
        # The stand-in server's --help names neither option (an older llama-server): none is
        # passed, or it would refuse to start.  One that names them is started with both off.
        argv_file = self.base / 'argv.json'
        env = dict(self.env, FAKE_ARGV_FILE=str(argv_file))
        result, _ = self.run_cli('hunyuan', ['--pages', '1'], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        plain = json.loads(argv_file.read_text())
        self.assertNotIn('--cache-ram', plain)
        self.assertNotIn('--ctx-checkpoints', plain)
        source = self.binary.with_suffix('.py').read_text()
        self.binary.with_suffix('.py').write_text(source.replace("parser.add_argument('--alias')",
                               "parser.add_argument('--alias')\nparser.add_argument('--cache-ram', help='prompt cache')"
                               "\nparser.add_argument('--ctx-checkpoints', help='checkpoints a slot')"))
        result, _ = self.run_cli('hunyuan', ['--pages', '1', '--overwrite'], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(argv_file.read_text())
        self.assertEqual(args[args.index('--cache-ram') + 1], '0')
        self.assertEqual(args[args.index('--ctx-checkpoints') + 1], '0')
        self.assertIn('--no-cache-prompt', args)

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

    def test_paddle_pipeline_and_revision_changes_invalidate_resume_packages_do_not(self):
        self.run_cli('paddle', ['--pages', '1'])
        for extra in (['--pipeline-version', 'v1.5'], ['--model-revision', 'cache-new']):
            with self.subTest(extra=extra):
                result, report = self.run_cli('paddle', ['--pages', '1', *extra])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(report['counts']['blocked'], 1)
        # Package versions are recorded in the draft, not compared.
        result, report = self.run_cli('paddle', ['--pages', '1'],
                                      env=dict(self.env, FAKE_PADDLE_VERSION='offline-v2'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 1)
        self.assertEqual(self.record()['ocrProfile']['packageVersions']['paddleocr'], 'offline-v1')
        # v1.5 loads its own VL model: the 1.6 folder is refused before loading.
        result, _ = self.run_cli('paddle', ['--pages', '1', '--overwrite', '--pipeline-version', 'v1.5'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('not PaddleOCR-VL-1.5-0.9B', result.stderr)
        vl15 = self.model_folder('PaddleOCR-VL-1.5', 'PaddleOCR-VL-1.5-0.9B', 'model.safetensors')
        result, report = self.run_cli('paddle', ['--pages', '1', '--overwrite', '--pipeline-version', 'v1.5',
                                                '--vl-model-dir', str(vl15)])
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
        # Paddle on a card it can see: it takes the same GPU queue as Hunyuan.
        self.paddle_build('gpu', devices=1)
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

    def test_paddle_with_a_resource_of_its_own_runs_beside_hunyuan(self):
        # OCR_PADDLE_GPU_RESOURCE (a card with room for both, an H200 on M1): Paddle queues on
        # its own resource, so it neither waits for HunyuanOCR nor holds it up; two Paddle runs
        # still take turns on theirs.
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        own = {'OCR_PADDLE_GPU_RESOURCE': 'offline-test-paddle-gpu'}
        held = self.launch('paddle', 'B', hold=True, env=own, extra=['--gpu-queue-timeout', '0'])
        self.wait_event('B:page:1')
        report_path = self.base / 'report-C.json'
        waiting = self.launch('paddle', 'C', env=own, extra=['--gpu-queue-timeout', '0.1'])
        self.finish(waiting, 75)
        self.assertEqual(json.loads(report_path.read_text())['gpuQueue']['resource'], 'offline-test-paddle-gpu')
        self.release('B')
        self.finish(held)
        self.assertIsNone(first.poll())
        self.release('A')
        self.finish(first)

    def test_paddle_on_cpu_does_not_hold_or_wait_for_the_gpu_queue(self):
        first = self.launch('hunyuan', 'A', hold=True)
        self.wait_event('A:page:1')
        on_cpu = self.launch('paddle', 'cpu', extra=['--device', 'cpu', '--gpu-queue-timeout', '0'])
        self.finish(on_cpu)
        report = json.loads((self.base / 'report-cpu.json').read_text())
        self.assertEqual((report['gpuQueue']['resource'], report['counts']['completed']), ('local-cpu', 1))
        self.assertIsNone(first.poll())
        # A second CPU run waits for the first one, not for the GPU.
        held = self.launch('paddle', 'cpu-held', hold=True, extra=['--device', 'cpu'])
        self.wait_event('cpu-held:page:1')
        waiting = self.launch('paddle', 'cpu-waiting', extra=['--device', 'cpu', '--gpu-queue-timeout', '0.1'])
        self.finish(waiting, 75)
        on_gpu = self.launch('paddle', 'gpu', extra=['--gpu-queue-timeout', '0.1'])
        self.finish(on_gpu, 75)
        self.release('cpu-held')
        self.finish(held)
        self.release('A')
        self.finish(first)

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


class PaddleWorkersTest(LocalOCRTestBase):
    """Several PaddleOCR-VL workers on one card (--workers): the stand-in package, no GPU."""

    def setUp(self):
        super().setUp()
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import run_paddleocr_vl as runner
        self.runner = runner
        self.pages = 2 * runner.MIN_PAGES_A_WORKER
        for page in range(4, self.pages + 1):
            (self.renders / f'page-{page:04d}.png').write_text(f'scan {page}')
        self.paddle_build('gpu', devices=1)
        self.readers = self.base / 'readers'
        self.readers.mkdir()
        self.env.update(FAKE_READER_DIR=str(self.readers), FAKE_PAGE_SECONDS='0.05')

    def read(self, workers, name, env=None):
        output, report = self.base / f'draft-{name}', self.base / f'report-{name}.json'
        process = subprocess.run(
            [sys.executable, str(SCRIPTS / 'run_paddleocr_vl.py'), str(self.renders), str(output),
             '--report', str(report), '--device', 'gpu:0', '--workers', str(workers)],
            env=env or self.env, capture_output=True, text=True, timeout=60)
        self.assertNotIn('Traceback', process.stderr, process.stderr)
        return process, json.loads(report.read_text()), output

    def who_read(self):
        """{page: the processes that were given it}"""
        found = {}
        for path in self.readers.iterdir():
            _, page, pid = path.name.split('-')
            found.setdefault(int(page), set()).add(pid)
        return found

    def test_two_workers_share_the_pages_and_the_drafts_are_the_ones_one_worker_writes(self):
        one, one_report, one_dir = self.read(1, 'one')
        self.assertEqual(one.returncode, 0, one.stderr)
        self.assertEqual(one_report['workers'], {'asked': '1', 'started': 1, 'lost': 0, 'aloneAfter': False})
        self.assertEqual(len({pid for pids in self.who_read().values() for pid in pids}), 1)
        for path in self.readers.iterdir():
            path.unlink()
        two, two_report, two_dir = self.read(2, 'two')
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(two_report['workers'], {'asked': '2', 'started': 2, 'lost': 0, 'aloneAfter': False})
        self.assertEqual(two_report['counts']['completed'], self.pages)
        self.assertIn('2 workers', two.stdout)
        readers = self.who_read()
        self.assertEqual(sorted(readers), list(range(1, self.pages + 1)))
        self.assertTrue(all(len(pids) == 1 for pids in readers.values()), readers)
        self.assertEqual(len({pid for pids in readers.values() for pid in pids}), 2)
        for page in range(1, self.pages + 1):
            name = f'page-{page:04d}'
            self.assertEqual((one_dir / f'{name}.txt').read_bytes(), (two_dir / f'{name}.txt').read_bytes())
            first = json.loads((one_dir / f'{name}.json').read_text())
            second = json.loads((two_dir / f'{name}.json').read_text())
            # Two runs differ in when a page was written and in the hash that covers it,
            # with one worker as with two; in nothing else.
            for record in (first, second):
                record.pop('generatedAt')
                record['provenance'].pop('jsonPayloadSha256')
            self.assertEqual(first, second, name)
        # A page's place in the report is its number, whoever read it first.
        self.assertEqual([entry['scanPage'] for entry in two_report['pages']], list(range(1, self.pages + 1)))

    def test_one_worker_has_no_ceiling_and_each_of_two_has_its_share(self):
        kwargs = self.base / 'kwargs.json'
        env = dict(self.env, FAKE_KWARGS_FILE=str(kwargs))
        self.read(1, 'one', env)
        self.assertIsNone(json.loads(kwargs.read_text())['env']['FLAGS_gpu_memory_limit_mb'])
        self.read(2, 'two', env)
        self.assertEqual(json.loads(kwargs.read_text())['env']['FLAGS_gpu_memory_limit_mb'],
                         str(self.runner.WORKER_SHARE_MIB))

    def test_a_worker_that_dies_leaves_its_page_to_the_other_and_every_page_is_read(self):
        env = dict(self.env, FAKE_DEADLY_PAGE='5', FAKE_DEADLY_ONCE=str(self.base / 'died'))
        result, report, _ = self.read(2, 'lost', env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts'], dict(report['counts'], completed=self.pages, failed=0, deferred=0))
        self.assertEqual(report['workers'], {'asked': '2', 'started': 2, 'lost': 1, 'aloneAfter': False})
        self.assertEqual(len(self.who_read()[5]), 2)
        self.assertIn('page 0005: its worker is gone', result.stderr)
        self.assertIn('1 worker(s) go on and one reads it again', result.stderr)

    def test_a_page_that_takes_every_worker_fails_with_the_pages_left(self):
        result, report, output = self.read(2, 'deadly', dict(self.env, FAKE_DEADLY_PAGE='5'))
        self.assertEqual(result.returncode, 1)
        # Both workers, then the one with no ceiling that took over: the page takes it too.
        self.assertEqual(report['workers'], {'asked': '2', 'started': 2, 'lost': 3, 'aloneAfter': True})
        by_page = {entry['scanPage']: entry for entry in report['pages']}
        self.assertEqual(by_page[5]['status'], 'failed')
        self.assertIn('worker unavailable', by_page[5]['error'])
        done = report['counts']['completed']
        self.assertEqual(done + report['counts']['failed'], self.pages)
        self.assertGreater(done, 0)
        self.assertEqual(len(list(output.glob('page-*.json'))), done)
        self.assertIn('ERROR worker unavailable', result.stderr)

    def test_a_page_too_large_for_a_share_is_read_by_one_worker_with_no_ceiling(self):
        # Several workers must never fail a page one worker would have read: when each of them
        # is gone under its ceiling, a single worker with none (the way one always runs) goes on.
        result, report, _ = self.read(2, 'large', dict(self.env, FAKE_TOO_LARGE_PAGE='5'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['workers'], {'asked': '2', 'started': 2, 'lost': 2, 'aloneAfter': True})
        self.assertEqual(report['counts']['completed'], self.pages)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(len(self.who_read()[5]), 3)
        self.assertIn('one worker with no memory ceiling reads the pages left', result.stderr)
        # One worker alone has no ceiling, so the page is nothing special to it.
        alone, alone_report, _ = self.read(1, 'large-one', dict(self.env, FAKE_TOO_LARGE_PAGE='5'))
        self.assertEqual(alone.returncode, 0, alone.stderr)
        self.assertEqual(alone_report['workers']['lost'], 0)

    def test_three_workers_lose_two_to_one_page_and_the_third_reads_the_rest(self):
        for page in range(self.pages + 1, 3 * self.runner.MIN_PAGES_A_WORKER + 1):
            (self.renders / f'page-{page:04d}.png').write_text(f'scan {page}')
        result, report, _ = self.read(3, 'three', dict(self.env, FAKE_DEADLY_PAGE='5'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['workers'], {'asked': '3', 'started': 3, 'lost': 2, 'aloneAfter': False})
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual(report['counts']['completed'], 3 * self.runner.MIN_PAGES_A_WORKER - 1)
        self.assertIn('page 0005: FAILED its second worker is gone too', result.stderr)

    def test_the_budget_ends_every_worker_and_the_pages_left_wait_for_the_next_call(self):
        env = dict(self.env, FAKE_PAGE_SECONDS='0.4', OCR_PHASE_DEADLINE=str(time.time() + 2.5))
        result, report, _ = self.read(2, 'budget', env)
        self.assertEqual(result.returncode, self.runner.EXIT_INCOMPLETE, result.stderr)
        counts = report['counts']
        self.assertEqual(counts['failed'], 0)
        self.assertGreater(counts['completed'], 0)
        self.assertGreater(counts['deferred'], 0)
        self.assertEqual(counts['completed'] + counts['deferred'], self.pages)
        self.assertIn('time budget reached', result.stdout)
        again, again_report, _ = self.read(2, 'budget', dict(self.env))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again_report['counts']['skippedCurrent'], counts['completed'])
        self.assertEqual(again_report['counts']['completed'], counts['deferred'])

    def test_how_many_workers_a_card_and_a_job_get(self):
        count, share, reserve = self.runner.worker_count, self.runner.WORKER_SHARE_MIB, self.runner.CARD_RESERVE_MIB
        many = 1000
        for free_gib, expected in ((8, 1), (12, 1), (16, 1), (24, 1), (32, 2), (48, 2), (80, 2)):
            with self.subTest(free_gib=free_gib):
                self.assertEqual(count('auto', gpu=True, pages=many, free_mib=free_gib * 1024)[0], expected)
        self.assertEqual(count('auto', gpu=True, pages=many, free_mib=reserve + 2 * share)[0], 2)
        self.assertEqual(count('auto', gpu=True, pages=many, free_mib=reserve + 2 * share - 1)[0], 1)
        self.assertEqual(count('auto', gpu=True, pages=many, free_mib=None)[0], 1)
        self.assertEqual(count('3', gpu=True, pages=many, free_mib=None)[0], 3)
        self.assertEqual(count(None, gpu=True, pages=many, free_mib=None)[0], 1)
        self.assertEqual(count('4', gpu=False, pages=many, free_mib=None)[0], 1)
        low = self.runner.MIN_PAGES_A_WORKER
        self.assertEqual(count('2', gpu=True, pages=2 * low - 1, free_mib=None)[0], 1)
        self.assertEqual(count('2', gpu=True, pages=2 * low, free_mib=None)[0], 2)
        for bad in ('0', '-1', 'many'):
            with self.subTest(bad=bad), self.assertRaises(self.runner.ConfigurationError):
                count(bad, gpu=True, pages=many, free_mib=None)

    def test_what_the_hand_over_needs_free_before_the_workers_start(self):
        import _paddle_pool as pool
        import run_ocr_phase as phase
        one = phase.PADDLE_MIB
        self.assertEqual(pool.need_mib('1', one), one)
        self.assertEqual(pool.need_mib(None, one), one)
        # auto asks for one worker's memory: the local model is never stopped for a second.
        self.assertEqual(pool.need_mib('auto', one), one)
        self.assertEqual(pool.need_mib('2', one), 2 * pool.WORKER_SHARE_MIB)
        self.assertEqual(pool.need_mib('nonsense', one), one)
        runner = ['python', 'run_paddleocr_vl.py', 'renders', 'ocr-paddle', '--device', 'gpu:0']
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(pool.WORKERS_ENV, None)
            self.assertEqual(phase.engine_need(runner), (one, 'Paddle'))
            self.assertEqual(phase.engine_need([*runner, '--workers', '2']),
                             (2 * pool.WORKER_SHARE_MIB, 'Paddle, 2 workers'))
            self.assertEqual(phase.engine_need([*runner, '--workers', 'auto']), (one, 'Paddle'))
            os.environ[pool.WORKERS_ENV] = '2'
            self.assertEqual(phase.engine_need(runner)[0], 2 * pool.WORKER_SHARE_MIB)


if __name__ == '__main__':
    unittest.main()
