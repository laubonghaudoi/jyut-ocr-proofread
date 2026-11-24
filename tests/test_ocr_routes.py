"""Offline route boundaries: no downloaded model or hand-over on pure Vision."""
from __future__ import annotations

import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from jyut_ocr.ocr import engines as engines
from jyut_ocr.ocr import setup_ocr as setup
import install
import ocr_book
import run_ocr_phase as phase


class Routes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.options = setup.SetupOptions(ROOT, self.base / 'config', self.base / 'data')
        self.addCleanup(mock.patch.stopall)
        mock.patch('platform.system', return_value='Darwin').start()
        mock.patch('platform.machine', return_value='arm64').start()
        mock.patch.dict(os.environ, {}, clear=True).start()

    def test_names_match_between_installer_and_book(self):
        for route, expected in (('apple-vision', ('vision',)), ('hunyuan-paddle', ('hunyuan', 'paddle'))):
            installed = install.build_parser().parse_args(['--engine', route])
            book = ocr_book.build_parser().parse_args(['BOOK', '--engine', route])
            self.assertEqual(setup.choose_engines(installed.engine, system='Darwin', machine='arm64')[0], expected)
            self.assertEqual(engines.resolve_engines(book.engine, environ={}), expected)
        for retired in ('auto', 'vision', 'vision,hunyuan', 'hunyuan,vision', 'hunyuan,paddle'):
            with self.subTest(route=retired):
                with self.assertRaisesRegex(ValueError, '--engine apple-vision.*--engine hunyuan-paddle'):
                    engines.resolve_engines(retired, environ={})
                for parser, args in ((install.build_parser(), []), (ocr_book.build_parser(), ['BOOK'])):
                    with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
                        parser.parse_args([*args, '--engine', retired])
                    self.assertIn('apple-vision', error.getvalue())
                    self.assertIn('hunyuan-paddle', error.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ocr_book.build_parser().parse_args(['BOOK', '--engines', 'hunyuan,paddle'])
        with self.assertRaisesRegex(phase.PhaseError, '--engine apple-vision'):
            phase.engine_need(['ocr_book.py', 'BOOK', '--engines', 'hunyuan,paddle'])
        with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit):
            ocr_book.main(['BOOK', '--engines', 'vision'])
        self.assertIn('--engine apple-vision or --engine hunyuan-paddle', error.getvalue())

    def test_profile_and_proofread_reject_mixed_books_before_writing_packets(self):
        import book_profile
        from test_dual_default_plumbing import write_book, run
        from test_vision_pipeline_contract import layout
        book = write_book(self.base, pages=1, a='ocr-vision', a_json=layout)
        argv = [str(book / 'renders'), str(book / 'ocr-vision'), str(book / 'ocr-hunyuan'),
                str(book / 'book-profile.json')]
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(book_profile.main(argv), 2)
        self.assertIn('engine A must be PaddleOCR-VL', error.getvalue())
        (book / 'reports').mkdir()
        record = book / 'reports/ocr-engines.json'
        for content in (json.dumps({'schemaVersion': 1, 'engines': ['vision', 'hunyuan']}), '{broken'):
            record.write_text(content)
            before = {p: p.read_bytes() for p in book.rglob('*') if p.is_file()}
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(book_profile.main(argv), 2)
            code, _, error, _ = run(book, '--single-engine', drafts='ocr-vision')
            self.assertEqual(code, 2, error)
            self.assertEqual({p: p.read_bytes() for p in book.rglob('*') if p.is_file()}, before)

    def test_retired_settings_stop_before_setup_writes_anything(self):
        self.options.config_dir.mkdir()
        path = self.options.config_dir / 'env.sh'
        for public, internal in (('auto', 'hunyuan,paddle'), ('vision', 'vision'),
                                 ('vision,hunyuan', 'vision,hunyuan'), ('hunyuan', 'hunyuan'),
                                 ('hunyuan-paddle', 'vision,hunyuan')):
            original = f'export JYUT_OCR_ENGINE={public}\nexport JYUT_OCR_ENGINES={internal}\n'
            path.write_text(original)
            with mock.patch.object(setup, '_setup_locked') as locked:
                with self.assertRaises(setup.SetupError):
                    setup.setup(self.options)
                locked.assert_not_called()
            self.assertEqual(setup.inspect_setup(self.options)['status'], 'action-required')
            self.assertEqual(path.read_text(), original)
            self.assertEqual(sorted(p.relative_to(self.base).as_posix() for p in self.base.rglob('*')),
                             ['config', 'config/env.sh'])

    def test_vision_setup_ignores_residual_models_and_never_hands_over(self):
        self.options.config_dir.mkdir()
        stale = self.options.data_dir / 'models' / 'old.gguf'
        stale.parent.mkdir(parents=True)
        stale.write_text('existing model, must remain untouched')
        (self.options.config_dir / 'env.sh').write_text(
            f'export JYUT_OCR_ENGINE=vision,hunyuan\nexport JYUT_OCR_ENGINES=vision,hunyuan\n'
            'export JYUT_OCR_DEVICE=metal\n'
            f'export HUNYUAN_MODEL="{stale}"\nexport HUNYUAN_LLAMA_SERVER=/old/server\n'
            'export PADDLE_VL_MODEL_DIR=/old/paddle\nexport MLX_VLM_PYTHON=/old/mlx/python\n'
            'export OCR_PHASE_SWAP=always\nexport OCR_PHASE_RELEASE_CMD=stop-model\n'
            'export OCR_PHASE_RESTORE_CMD=start-model\n')
        forbidden = ('ensure_models', 'ensure_runtime', 'ensure_paddle_weights', 'ensure_paddle_python',
                     'ensure_mlx_python', 'handover_smokes', 'free_gpu_mib', 'download_asset')
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(mock.patch.object(setup, name)) for name in forbidden}
            stack.enter_context(mock.patch.object(setup, 'ensure_dependencies'))
            stack.enter_context(mock.patch.object(setup, 'ensure_python', return_value=Path('/offline/python')))
            stack.enter_context(mock.patch.object(setup, 'check_free_disk'))
            identity = stack.enter_context(mock.patch.object(setup, 'environment_identity', return_value={'id': 1}))
            smoke = stack.enter_context(mock.patch.object(setup, 'smoke_test', return_value={'json_output': 'vision.json'}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = setup.setup(replace(self.options, engine='apple-vision'))
        self.assertEqual(result['engines'], ['vision'])
        for call in mocks.values():
            call.assert_not_called()
        self.assertEqual(smoke.call_count, 1)
        self.assertEqual(smoke.call_args.args[2], 'vision')
        self.assertEqual(identity.call_args.args[2], ('vision',))
        config = setup.read_environment(self.options.config_dir / 'env.sh')
        self.assertEqual((config['JYUT_OCR_ENGINE'], config['JYUT_OCR_ENGINES']), ('apple-vision', 'vision'))
        self.assertEqual(stale.read_text(), 'existing model, must remain untouched')
        self.assertEqual(engines.resolve_engines(environ=config), ('vision',))

    def test_intel_and_rosetta_stop_all_ocr_entrypoints_before_side_effects(self):
        import run_apple_vision_ocr as vision
        import run_hunyuanocr as hunyuan
        import run_hunyuanocr_second_look as second
        import run_paddleocr_vl as paddle
        with mock.patch('platform.machine', return_value='x86_64'):
            for module in (vision, hunyuan, second, paddle, phase):
                with self.subTest(module=module.__name__), contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(module.main([]), 2)
                    self.assertIn('native arm64', error.getvalue())
            with mock.patch.object(install, 'perform_install') as skill, mock.patch.object(install, 'setup') as prepare:
                for flags in (['--skill-only'], ['--env-only'], ['--check']):
                    with contextlib.redirect_stderr(io.StringIO()) as error:
                        self.assertEqual(install.main([*flags, '--config-dir', str(self.options.config_dir),
                                                       '--data-dir', str(self.options.data_dir)]), 2)
                    self.assertIn('Rosetta', error.getvalue())
                skill.assert_not_called()
                prepare.assert_not_called()
            self.assertEqual(setup.inspect_setup(self.options)['status'], 'action-required')
            self.assertEqual(list(self.base.iterdir()), [])
            (self.base / 'renders').mkdir()
            with contextlib.redirect_stdout(io.StringIO()) as error, mock.patch.object(ocr_book, 'run_step') as step:
                self.assertEqual(ocr_book.main([str(self.base), '--engine', 'hunyuan-paddle']), 2)
                self.assertIn('native arm64', error.getvalue())
                step.assert_not_called()
            self.assertEqual(list(self.base.iterdir()), [self.base / 'renders'])

    def test_macos_hunyuan_only_rejected_but_linux_and_wsl_kept(self):
        for args in ({'request': 'hunyuan'}, {'single_engine': True}):
            with self.assertRaisesRegex(ValueError, 'Hunyuan-only'):
                engines.resolve_engines(environ={}, **args)
            self.assertEqual(engines.resolve_engines(environ={}, system='Linux', machine='x86_64', **args), ('hunyuan',))
        self.assertEqual(engines.resolve_engines(environ={}, system='Linux'), ('hunyuan', 'paddle'))

    def test_phase_rejects_retired_books_before_any_model_handover(self):
        (self.base / 'reports').mkdir()
        record = self.base / 'reports/ocr-engines.json'
        record.write_text(json.dumps({'schemaVersion': 1, 'engines': ['vision', 'hunyuan']}))
        before = record.read_bytes()
        args = phase.build_parser().parse_args(['--force-swap', '--required-mib', '1', '--release-cmd', 'stop-model',
                                               '--restore-cmd', 'start-model', '--', 'ocr_book.py', str(self.base),
                                               '--engine', 'hunyuan-paddle'])
        with mock.patch.object(phase, 'run_plain') as plain, mock.patch.object(phase, 'ready_probe') as probe, \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(phase.run_phase(args), 2)
        plain.assert_not_called()
        probe.assert_not_called()
        self.assertEqual(record.read_bytes(), before)

    def test_vision_phase_bypasses_forced_swap_and_memory_override(self):
        for runner in (['ocr_book.py', str(self.base), '--engine', 'apple-vision'],
                       ['run_apple_vision_ocr.py', 'R', 'O']):
            args = phase.build_parser().parse_args(['--force-swap', '--required-mib', '999999',
                                                   '--release-cmd', 'stop-model', '--restore-cmd', 'start-model',
                                                   '--', *runner])
            with mock.patch.object(phase, 'run_plain', return_value=0) as plain, \
                    mock.patch.object(phase, 'ready_probe') as probe, mock.patch.object(phase, 'run_command') as command, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(phase.run_phase(args), 0)
            plain.assert_called_once()
            probe.assert_not_called()
            command.assert_not_called()
            self.assertEqual(list(self.base.iterdir()), [])


class OcrBookMissingEngineBTests(unittest.TestCase):
    def test_max_missing_engine_b_tolerates_up_to_limit_and_forwards_allow_flag(self):
        import ocr_book
        import time

        def run_case(max_missing: int, failed_pages: list[int]) -> tuple[int, list[list[str]]]:
            with tempfile.TemporaryDirectory() as tmp:
                book = Path(tmp) / 'book'
                (book / 'renders').mkdir(parents=True)
                for p in (1, 2, 3):
                    (book / 'renders' / f'page-{p:04d}.png').write_bytes(b'png')
                calls: list[list[str]] = []

                def fake_run_step(command, step_env, progress):
                    calls.append(list(command))
                    report_idx = command.index('--report') + 1
                    report_path = Path(command[report_idx])
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    script_name = Path(command[1]).name
                    if script_name == 'run_hunyuanocr.py' and failed_pages:
                        report = {
                            'counts': {
                                'requested': 3,
                                'completed': 3 - len(failed_pages),
                                'skippedCurrent': 0,
                                'failed': len(failed_pages),
                                'deferred': 0,
                            },
                            'pages': [{'scanPage': p, 'status': 'failed'} for p in failed_pages],
                        }
                        report_path.write_text(json.dumps(report), encoding='utf-8')
                        return 1, time.time(), time.monotonic(), None
                    report_path.write_text(json.dumps({'counts': {'requested': 3, 'completed': 3, 'failed': 0, 'deferred': 0}}), encoding='utf-8')
                    return 0, time.time(), time.monotonic(), None

                argv = [str(book), '--engine', 'hunyuan-paddle', '--max-missing-engine-b', str(max_missing)]
                with mock.patch.object(ocr_book, 'resolve_engines', return_value=('hunyuan', 'paddle')), \
                        mock.patch('jyut_ocr.ocr.engines.require_supported_host', return_value=None), \
                        mock.patch.object(ocr_book, 'run_step', side_effect=fake_run_step), \
                        mock.patch('subprocess.run', return_value=mock.Mock(returncode=0, stdout='')), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc = ocr_book.main(argv)
                return rc, calls

        rc_strict, calls_strict = run_case(0, [2])
        self.assertEqual(rc_strict, 1)
        self.assertEqual(len(calls_strict), 1)

        rc_ok, calls_ok = run_case(1, [2])
        self.assertEqual(rc_ok, 0)
        self.assertEqual(len(calls_ok), 3)
        self.assertIn('--allow-missing-engine-b', calls_ok[2])

        rc_over, _ = run_case(1, [2, 3])
        self.assertEqual(rc_over, 1)

        rc_neg, _ = run_case(-1, [])
        self.assertEqual(rc_neg, 2)


if __name__ == '__main__':
    unittest.main()
