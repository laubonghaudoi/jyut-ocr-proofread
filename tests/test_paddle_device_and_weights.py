"""PaddleOCR-VL runner: explicit device, local weights and the worker's log.

Offline, with the fake paddleocr and paddle packages of test_local_ocr_runners:
no GPU, no model and no download.  The device is gpu:N or cpu (unset: gpu:0
when the Paddle build sees a CUDA card); a GPU run never falls back to the CPU
silently; a CPU run takes its own queue resource and sees no card.  Weights come
from local folders only, and missing ones fail before the queue.  Device, dtype
and the Paddle package are recorded in the drafts and the report, never compared.
"""
from __future__ import annotations

import json
import sys
import unittest

from test_local_ocr_runners import LocalOCRTestBase, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import run_paddleocr_vl as paddle  # noqa: E402


class WorkerHelpTest(unittest.TestCase):
    def test_help_needs_no_paddleocr(self):
        # Isolated (-I): no paddleocr importable, and nothing loaded or read
        # from stdin; any other argument is refused.
        import subprocess
        worker = SCRIPTS / 'paddleocr_vl_worker.py'
        result = subprocess.run([sys.executable, '-I', str(worker), '--help'], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith('usage: paddleocr_vl_worker.py [--help]\n\n'), result.stdout)
        self.assertNotIn('pipeline load', result.stdout + result.stderr)
        refused = subprocess.run([sys.executable, '-I', str(worker), '--device', 'cpu'], capture_output=True,
                                 text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(refused.returncode, 2)
        self.assertIn('no arguments are taken', refused.stderr)


class PaddleDeviceTest(LocalOCRTestBase):
    def setUp(self):
        super().setUp()
        self.kwargs_file = self.base / 'kwargs.json'
        self.env['FAKE_KWARGS_FILE'] = str(self.kwargs_file)

    def loaded(self):
        return json.loads(self.kwargs_file.read_text())

    def test_unset_device_takes_the_gpu_paddle_sees_and_refuses_fallback(self):
        self.paddle_build('gpu', devices=1)
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PaddleOCR-VL on gpu:0 (bfloat16)', result.stdout)
        loaded = self.loaded()
        self.assertEqual(loaded['device'], 'gpu:0')
        self.assertEqual(loaded['env']['PADDLE_PDX_DISABLE_DEVICE_FALLBACK'], 'True')
        self.assertIsNone(loaded['env']['CUDA_VISIBLE_DEVICES'])
        execution = self.record()['ocrProfile']['execution']
        self.assertEqual({key: execution[key] for key in (
            'device', 'deviceSource', 'vlDevice', 'layoutDevice', 'dtype', 'paddlePackage')},
            {'device': 'gpu:0', 'deviceSource': 'detected', 'vlDevice': 'gpu:0',
             'layoutDevice': 'gpu:0', 'dtype': 'bfloat16',
             'paddlePackage': {'name': 'paddlepaddle-gpu', 'version': '3.3.1'}})
        self.assertEqual(execution['cudaProbe'], {'compiled': True, 'deviceCount': 1})
        self.assertEqual(report['execution'], execution)
        self.assertEqual(report['gpuQueue']['resource'], 'offline-test-gpu')
        self.assertEqual(execution['queueResource'], 'offline-test-gpu')
        # Recorded, not compared: the same pages on the CPU package stay current.
        self.paddle_build('cpu')
        result, report = self.run_cli('paddle', ['--pages', '1', '--device', 'cpu'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 1)
        self.assertEqual(report['gpuQueue']['mode'], 'not-needed')

    def test_cpu_build_runs_on_the_cpu_queue_with_no_card_visible(self):
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = self.loaded()
        self.assertEqual(loaded['device'], 'cpu')
        self.assertEqual(loaded['env']['CUDA_VISIBLE_DEVICES'], '')
        self.assertIsNone(loaded['env']['PADDLE_PDX_DISABLE_DEVICE_FALLBACK'])
        execution = report['execution']
        self.assertEqual((execution['device'], execution['dtype'], execution['paddlePackage']['name']),
                         ('cpu', 'float32', 'paddlepaddle'))
        self.assertEqual(execution['cudaProbe'], {'compiled': False, 'deviceCount': 0})
        self.assertEqual(report['gpuQueue']['resource'], paddle.DEFAULT_CPU_RESOURCE)
        result, report = self.run_cli('paddle', ['--pages', '2', '--cpu-resource', 'cpu-pool'],
                                      env=dict(self.env, OCR_PADDLE_DEVICE='cpu'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['gpuQueue']['resource'], 'cpu-pool')
        self.assertEqual(report['execution']['deviceSource'], 'config')
        self.assertIsNone(report['execution']['cudaProbe'])  # cpu asked for: Paddle not probed

    def test_configured_gpu_paddle_cannot_use_fails_before_the_queue(self):
        cases = (('cpu', 0, {'OCR_PADDLE_DEVICE': 'gpu:0'}, [], 'has no CUDA (paddlepaddle 3.3.1)'),
                 ('gpu', 0, {'OCR_PADDLE_DEVICE': 'gpu'}, [], 'sees 0 CUDA device(s)'),
                 ('gpu', 1, {}, ['--device', 'gpu:1'], 'sees 1 CUDA device(s)'))
        for kind, devices, env, extra, message in cases:
            with self.subTest(kind=kind, devices=devices, extra=extra):
                self.paddle_build(kind, devices=devices)
                result, _ = self.run_cli('paddle', ['--pages', '1', *extra], env=dict(self.env, **env))
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertIn('OCR_PADDLE_DEVICE=cpu', result.stderr)
                self.assertEqual(self.events(), [])
                self.assertFalse(self.queue_dir.exists())

    def test_fully_current_resume_needs_neither_the_gpu_nor_the_weights(self):
        self.run_cli('paddle', ['--pages', '1'])
        env = dict(self.env, OCR_PADDLE_DEVICE='gpu:0', PADDLE_VL_MODEL_DIR=str(self.base / 'gone'))
        result, report = self.run_cli('paddle', ['--pages', '1'], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 1)

    def test_gpu_gone_by_load_time_is_refused_by_the_worker(self):
        self.paddle_build('gpu', devices=1)
        result, report = self.run_cli('paddle', ['--pages', '1'],
                                      env=dict(self.env, FAKE_WORKER_CUDA_DEVICES='0'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('pipeline load refused: gpu:0 is configured, but Paddle sees 0',
                      report['pages'][0]['error'])
        self.assertNotIn('started:v1.6', self.events())
        self.assertFalse((self.output / 'page-0001.json').exists())

    def test_silent_cpu_fallback_after_load_fails_closed(self):
        self.paddle_build('gpu', devices=1)
        result, report = self.run_cli('paddle', ['--pages', '1-2'],
                                      env=dict(self.env, FAKE_PADDLE_FALLBACK='1'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['counts']['failed'], 2)
        self.assertIn('refusing a silent device fallback', report['pages'][0]['error'])
        self.assertEqual(report['execution']['vlDevice'], 'cpu')
        self.assertEqual(self.events(), ['started:v1.6', 'stopped'])
        self.assertFalse((self.output / 'page-0001.json').exists())

    def test_device_spelling(self):
        self.assertEqual([paddle.parse_device(value) for value in
                          (None, '', 'auto', 'cpu', 'gpu', 'GPU:1', ' gpu:02 ')],
                         [None, None, None, 'cpu', 'gpu:0', 'gpu:1', 'gpu:2'])
        result, _ = self.run_cli('paddle', ['--pages', '1', '--device', 'cuda'])
        self.assertEqual(result.returncode, 2)
        self.assertIn('device must be gpu:N or cpu', result.stderr)


class PaddleWeightsTest(LocalOCRTestBase):
    def setUp(self):
        super().setUp()
        self.kwargs_file = self.base / 'kwargs.json'
        self.env['FAKE_KWARGS_FILE'] = str(self.kwargs_file)

    def test_configured_folders_are_passed_and_the_host_check_is_skipped(self):
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads(self.kwargs_file.read_text())
        self.assertEqual((loaded['vl_rec_model_dir'], loaded['layout_detection_model_dir']),
                         (str(self.vl_dir), str(self.layout_dir)))
        self.assertEqual(loaded['env']['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'], 'True')
        weights = report['execution']['weights']
        self.assertEqual((weights['vl']['source'], weights['layout']['model']),
                         ('configured', 'PP-DocLayoutV3'))

    def test_missing_weights_fail_fast_and_point_to_the_installer(self):
        env = dict(self.env)
        env.pop('PADDLE_VL_MODEL_DIR')
        result, report = self.run_cli('paddle', ['--pages', '1'], env=env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('PaddleOCR-VL-1.6-0.9B weights are not installed', result.stderr)
        self.assertIn('./install.sh', result.stderr)
        self.assertIn('PADDLE_VL_MODEL_DIR', result.stderr)
        self.assertIsNone(report)
        self.assertEqual(self.events(), [])
        self.assertFalse(self.queue_dir.exists())

    def test_a_configured_folder_must_hold_its_model_and_weights(self):
        empty = self.base / 'empty-layout'
        empty.mkdir()
        (empty / 'inference.yml').write_text('Global:\n  model_name: PP-DocLayoutV3\n')
        cases = ((['--vl-model-dir', str(self.layout_dir)], 'holds PP-DocLayoutV3, not PaddleOCR-VL-1.6-0.9B'),
                 (['--layout-model-dir', str(empty)], 'has no weights file'),
                 (['--layout-model-dir', str(self.base / 'nowhere')], 'is not a folder'))
        for extra, message in cases:
            with self.subTest(message=message):
                result, _ = self.run_cli('paddle', ['--pages', '1', *extra])
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.events(), [])

    def test_paddlex_cache_is_used_when_no_folder_is_configured(self):
        cache = self.base / 'paddlex-cache' / 'official_models'
        vl = self.model_folder('PaddleOCR-VL-1.6', 'PaddleOCR-VL-1.6-0.9B', 'model.safetensors', root=cache)
        layout = self.model_folder('PP-DocLayoutV3', 'PP-DocLayoutV3', 'inference.pdiparams', root=cache)
        env = dict(self.env)
        env.pop('PADDLE_VL_MODEL_DIR')
        env.pop('PADDLE_LAYOUT_MODEL_DIR')
        result, report = self.run_cli('paddle', ['--pages', '1'], env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads(self.kwargs_file.read_text())
        self.assertEqual((loaded['vl_rec_model_dir'], loaded['layout_detection_model_dir']),
                         (str(vl), str(layout)))
        self.assertEqual(report['execution']['weights']['vl']['source'], 'paddlex-cache')


class PaddleWorkerLogTest(LocalOCRTestBase):
    def test_worker_stderr_is_kept_beside_the_report_and_its_tail_shown_on_failure(self):
        log = self.base / 'report.worker.log'
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['workerLog'], str(log))
        # Library prints on stdout reach the log too, never the JSON protocol.
        self.assertIn('fake paddlex: creating models on cpu', log.read_text())
        self.assertIn('fake paddlex: stderr line', log.read_text())
        self.assertNotIn('worker log', result.stderr)
        result, report = self.run_cli('paddle', ['--pages', '2'],
                                      env=dict(self.env, FAKE_LOAD_FAILURE='1'))
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'worker log {log}, last', result.stderr)
        self.assertIn('    | fake paddlex: stderr line', result.stderr)
        self.assertIn('fake load failure', report['pages'][0]['error'])
        result, report = self.run_cli('paddle', ['--pages', '2'], env=dict(self.env, FAKE_BAD_PAGE='2'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('fake paddlex: stderr line', result.stderr)

    def test_no_worker_no_log(self):
        self.run_cli('paddle', ['--pages', '1'])
        (self.base / 'report.worker.log').unlink()
        result, report = self.run_cli('paddle', ['--pages', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(report['workerLog'])
        self.assertFalse((self.base / 'report.worker.log').exists())


class PaddleProbeCacheTest(unittest.TestCase):
    def test_probe_cache_hits_invalidates_recovers_corrupt_and_sets_source_check_env(self):
        import run_paddleocr_vl as runner
        from pathlib import Path
        import os
        import stat
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir = root / 'probe-cache'
            venv_bin = root / 'venv' / 'bin'
            venv_bin.mkdir(parents=True)
            fake_py = venv_bin / 'python'
            calls_log = root / 'probe-calls.txt'
            env_log = root / 'probe-env.txt'
            fake_py.write_text(
                "#!/bin/sh\n"
                f"printf 'call\\n' >> '{calls_log}'\n"
                f"printf '%s\\n' \"${{PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:-}}\" >> '{env_log}'\n"
                "case \" $* \" in\n"
                "  *' cuda '*) printf '{\"versions\": {\"paddlepaddle\": \"3.3.1\", \"paddleocr\": \"3.0\"}, \"paddlePackages\": [], \"cuda\": {\"compiled\": true, \"deviceCount\": 1}}\\n' ;;\n"
                "  *) printf '{\"versions\": {\"paddlepaddle\": \"3.3.1\", \"paddleocr\": \"3.0\"}, \"paddlePackages\": []}\\n' ;;\n"
                "esac\n",
                encoding='utf-8',
            )
            fake_py.chmod(fake_py.stat().st_mode | stat.S_IEXEC)

            clean_env = {
                k: v for k, v in os.environ.items()
                if k not in ('PYTHONPATH', 'OCR_PADDLE_PROBE_CACHE')
            }
            clean_env['OCR_PADDLE_PROBE_CACHE_DIR'] = str(cache_dir)
            with mock.patch.dict(os.environ, clean_env, clear=True):
                first = runner.verify_paddleocr_available(str(fake_py), probe_cuda=False)
                second = runner.verify_paddleocr_available(str(fake_py), probe_cuda=False)
                self.assertEqual(first, second)
                self.assertEqual(len(calls_log.read_text().splitlines()), 1)
                self.assertEqual(env_log.read_text().splitlines(), ['True'])

                # Toggling probe_cuda misses non-cuda cache and caches separately
                cuda_res = runner.verify_paddleocr_available(str(fake_py), probe_cuda=True)
                self.assertEqual(cuda_res['cuda']['deviceCount'], 1)
                self.assertEqual(len(calls_log.read_text().splitlines()), 2)

                # Corrupt cache file falls back silently to live probe and refreshes it
                cache_files = list(cache_dir.glob('probe-*.json'))
                self.assertEqual(len(cache_files), 2)
                for cf_path in cache_files:
                    cf_path.write_text('{not-json', encoding='utf-8')
                refreshed = runner.verify_paddleocr_available(str(fake_py), probe_cuda=False)
                self.assertEqual(refreshed['versions']['paddleocr'], '3.0')
                self.assertEqual(len(calls_log.read_text().splitlines()), 3)

                # Changing interpreter file invalidates cache key
                fake_py.write_text(fake_py.read_text() + '# touched\n', encoding='utf-8')
                runner.verify_paddleocr_available(str(fake_py), probe_cuda=False)
                self.assertEqual(len(calls_log.read_text().splitlines()), 4)

                # PYTHONPATH without explicit OCR_PADDLE_PROBE_CACHE_DIR bypasses cache
                with mock.patch.dict(os.environ, {'PYTHONPATH': str(root)}, clear=False):
                    os.environ.pop('OCR_PADDLE_PROBE_CACHE_DIR', None)
                    self.assertIsNone(runner._probe_cache_path(str(fake_py), False))


if __name__ == '__main__':
    unittest.main()
