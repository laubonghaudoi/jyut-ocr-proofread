"""Draft currency: what decides that a finished OCR page is still current.

Offline: the fake llama-server and fake paddleocr package from
test_local_ocr_runners, no GPU or model.  A page stays current while its
render, model, runtime and output-affecting request settings match; code hashes,
deployment, interpreter path, package versions and the absolute render path
are recorded but not compared.  Drafts written by an older runner (no
draftIdentity, no raw reply) are accepted and reported as legacy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import unittest

from test_local_ocr_runners import LocalOCRTestBase, SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import run_hunyuanocr as hy  # noqa: E402
import run_hunyuanocr_second_look as second  # noqa: E402
import run_paddleocr_vl as paddle  # noqa: E402

MOVED = '/a/folder/that/moved/renders/page-{:04d}.png'
TEXT_LINES = ['正文第一行讀出。', '正文第二行讀出。']

# The older runners' whole profiles, as they wrote them: no max tokens, no
# deployment, no package versions; repetition penalty recorded as 1.08 but
# never sent.
OLDER_HUNYUAN_PROFILE = {
    'engine': 'HunyuanOCR', 'prompt': hy.DOC_PARSE_PROMPT, 'recognitionLevel': 'vlm-whole-page',
    'usesLanguageCorrection': False, 'temperature': 0.0, 'topP': 1.0, 'repetitionPenalty': 1.08,
    'readingOrder': 'model-resolved-whole-page', 'boundingBoxConvention': 'none-whole-page-only',
}
OLDER_PADDLE_PROFILE = {
    'engine': 'PaddleOCR-VL', 'pipelineVersion': 'v1.6',
    'recognitionLanguages': ['zh-Hant', 'zh-Hans', 'en'], 'recognitionLevel': 'vlm-end-to-end',
    'usesLanguageCorrection': False, 'readingOrder': 'layout-model-block-order',
    'boundingBoxConvention': 'normalised-xywh-top-left-origin',
    'confidenceMeaning': 'layout-detector-block-score-not-glyph-confidence',
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal(record: dict) -> bytes:
    record['provenance'].pop('jsonPayloadSha256', None)
    record['provenance']['jsonPayloadSha256'] = sha(hy.canonical_json_bytes(record, pretty=True))
    return hy.canonical_json_bytes(record, pretty=True)


class DraftIdentityTestBase(LocalOCRTestBase):
    def setUp(self):
        super().setUp()
        self.ocr_a = self.base / 'ocr-a'
        self.ocr_b = self.output
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = str(probe.getsockname()[1])

    def hunyuan_args(self):
        return ['--model', str(self.model), '--mmproj', str(self.proj),
                '--llama-server', str(self.binary), '--port', self.port]

    def run_script(self, scripts: Path, name: str, args, *, env=None, report=None):
        report = report or self.base / (name + '.report.json')
        process = subprocess.run([sys.executable, str(scripts / name), *map(str, args),
                                  '--report', str(report)],
                                 env=env or self.env, capture_output=True, text=True, timeout=30)
        self.assertNotIn('Traceback', process.stderr, process.stderr)
        return process, json.loads(report.read_text())

    def run_hunyuan(self, extra=(), scripts=SCRIPTS, **kwargs):
        return self.run_script(scripts, 'run_hunyuanocr.py',
                               [self.renders, self.ocr_b, '--pages', '1-3', *self.hunyuan_args(), *extra],
                               **kwargs)

    def run_paddle(self, extra=(), scripts=SCRIPTS, **kwargs):
        return self.run_script(scripts, 'run_paddleocr_vl.py',
                               [self.renders, self.ocr_a, '--pages', '1-3', *extra], **kwargs)

    def run_second_look(self, extra=(), scripts=SCRIPTS, **kwargs):
        return self.run_script(scripts, 'run_hunyuanocr_second_look.py',
                               [self.renders, self.ocr_a, self.ocr_b, '--pages', '1-3',
                                *self.hunyuan_args(), '--server-mode', 'dedicated', *extra], **kwargs)

    def render_sha(self, page: int) -> str:
        return sha((self.renders / f'page-{page:04d}.png').read_bytes())

    def write_older_paddle(self, page: int, profile=OLDER_PADDLE_PROFILE) -> bytes:
        lines = [{'text': text, 'confidence': 0.9, 'boundingBox': [0.1, 0.1 + 0.2 * k, 0.8, 0.15],
                  'blockLabel': 'text', 'blockOrder': k} for k, text in enumerate(TEXT_LINES)]
        text_bytes = paddle.text_from_lines(lines)
        record = {
            'scanPage': page, 'renderFile': MOVED.format(page), 'generatedAt': '2026-01-01T00:00:00Z',
            'engine': 'PaddleOCR-VL', 'pipelineVersion': profile['pipelineVersion'],
            'recognitionLanguages': ['zh-Hant', 'zh-Hans', 'en'], 'recognitionLevel': 'vlm-end-to-end',
            'usesLanguageCorrection': False, 'renderWidth': 100, 'renderHeight': 200,
            'lines': lines, 'regions': [], 'schemaVersion': 2, 'status': 'complete',
            'textAuthority': paddle.TEXT_AUTHORITY,
            'provenance': {'renderSha256': self.render_sha(page), 'toolSha256': '0' * 64,
                           'ocrProfileSha256': paddle.profile_sha256(profile),
                           'textSha256': sha(text_bytes)},
        }
        self.ocr_a.mkdir(exist_ok=True)
        json_bytes = seal(record)
        (self.ocr_a / f'page-{page:04d}.json').write_bytes(json_bytes)
        (self.ocr_a / f'page-{page:04d}.txt').write_bytes(text_bytes)
        return json_bytes

    def write_older_hunyuan(self, page: int, profile=OLDER_HUNYUAN_PROFILE, look=None):
        lines = hy.lines_from_text('\n'.join(TEXT_LINES))
        text_bytes = hy.text_from_lines(lines)
        record = {
            'schemaVersion': 2, 'scanPage': page, 'status': 'complete',
            'renderFile': MOVED.format(page), 'generatedAt': '2026-01-01T00:00:00Z',
            'engine': 'HunyuanOCR', 'recognitionLevel': 'vlm-whole-page',
            'usesLanguageCorrection': False, 'sentenceMarks': 2,
            'textAuthority': hy.TEXT_AUTHORITY, 'lines': lines,
            'provenance': {'renderSha256': self.render_sha(page), 'toolSha256': '1' * 64,
                           'ocrProfileSha256': hy.profile_sha256(profile),
                           'textSha256': sha(text_bytes)},
        }
        if look is not None:
            record['secondLook'] = look
        self.ocr_b.mkdir(exist_ok=True)
        (self.ocr_b / f'page-{page:04d}.json').write_bytes(seal(record))
        (self.ocr_b / f'page-{page:04d}.txt').write_bytes(text_bytes)

    def older_book(self, look_share=second.UNREAD_SHARE):
        for page in range(1, 4):
            draft_a = self.write_older_paddle(page)
            look = {'schemaVersion': 1, 'toolSha256': '2' * 64, 'engineADraftSha256': sha(draft_a),
                    'groups': [], 'unreadShare': look_share}
            self.write_older_hunyuan(page, look=look)

    def snapshot(self):
        return {str(path.relative_to(self.base)): path.read_bytes()
                for path in sorted(self.base.glob('ocr-*/page-*')) + sorted(self.ocr_b.glob('page-*'))}


class LegacyDraftTest(DraftIdentityTestBase):
    def test_older_runner_drafts_resume_as_legacy_without_a_model(self):
        self.older_book()
        before = self.snapshot()
        for name, run in (('paddle', self.run_paddle), ('hunyuan', self.run_hunyuan),
                          ('second-look', self.run_second_look)):
            with self.subTest(runner=name):
                result, report = run()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(report['counts']['skippedCurrent'], 3)
                self.assertEqual(report['counts']['legacy'], 3)
                self.assertTrue(all(page['legacy'] for page in report['pages']))
                if name != 'second-look':
                    self.assertIn('legacy drafts', result.stdout)
        self.assertEqual(self.events(), [])  # no model process was started
        self.assertEqual(self.snapshot(), before)

    def test_older_drafts_with_other_settings_are_still_rejected(self):
        self.older_book(look_share=0.5)
        self.write_older_paddle(1, dict(OLDER_PADDLE_PROFILE, pipelineVersion='v1.5'))
        self.write_older_hunyuan(1, dict(OLDER_HUNYUAN_PROFILE, prompt='another prompt'))
        result, report = self.run_paddle()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['pages'][0]['status'], 'blocked')
        self.assertEqual(report['counts']['legacy'], 2)
        result, report = self.run_hunyuan()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report['pages'][0]['status'], 'blocked')
        self.assertIn('ocrProfileSha256', report['pages'][0]['error'])
        self.assertEqual(report['counts']['legacy'], 2)
        # Another unread threshold: the second look is read again.
        _, report = self.run_second_look(['--pages', '2-3'])
        self.assertEqual(report['counts']['skippedCurrent'], 0)
        self.assertEqual(report['counts']['completed'], 2)

    def test_legacy_profile_variants_follow_the_current_request(self):
        profile = dict(hy.OCR_PROFILE, maxTokens=8000, deployment={})
        hashes = hy.legacy_profile_hashes(profile)
        self.assertIn(hy.profile_sha256(OLDER_HUNYUAN_PROFILE), hashes)
        self.assertIn(hy.profile_sha256(dict(OLDER_HUNYUAN_PROFILE, repetitionPenalty=1.0)), hashes)
        self.assertIn(hy.profile_sha256(dict(OLDER_HUNYUAN_PROFILE, emitsSentencePunctuation=False)), hashes)
        # Unsent seed is equivalent only for greedy decoding.
        self.assertEqual(hy.legacy_profile_hashes(dict(profile, temperature=0.7)), frozenset())
        self.assertNotIn(hy.profile_sha256(OLDER_HUNYUAN_PROFILE),
                         hy.legacy_profile_hashes(dict(profile, prompt='changed')))
        self.assertEqual(paddle.legacy_profile_hash(dict(paddle.OCR_PROFILE, packageVersions={},
                                                         modelRevision='r')),
                         paddle.profile_sha256(OLDER_PADDLE_PROFILE))

    def test_new_draft_without_raw_reply_is_rejected(self):
        self.run_hunyuan(['--pages', '1'])
        path = self.ocr_b / 'page-0001.json'
        record = json.loads(path.read_text())
        del record['rawResponse']
        path.write_bytes(seal(record))
        result, report = self.run_hunyuan(['--pages', '1'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('raw response is missing', report['pages'][0]['error'])


class RecordedNotComparedTest(DraftIdentityTestBase):
    def frozen_scripts(self) -> Path:
        scripts = self.base / 'scripts'
        scripts.mkdir()
        for name in ('jyut_ocr/__init__.py', 'jyut_ocr/paths.py', 'jyut_ocr/core/__init__.py',
                     'jyut_ocr/core/common.py', '_ocr_runner_common.py', 'run_hunyuanocr.py',
                     'run_hunyuanocr_second_look.py', 'run_paddleocr_vl.py', 'paddleocr_vl_worker.py',
                     '_hunyuan_shared.py', '_gpu_queue.py', '_phase_budget.py', '_ocr_engines.py',
                     '_mlx_vlm_server.py', '_paddle_pool.py'):
            dst = scripts / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / name, dst)
        return scripts

    def test_helper_code_and_runner_edits_keep_drafts_current(self):
        scripts = self.frozen_scripts()
        for run in (self.run_paddle, self.run_hunyuan, self.run_second_look):
            result, report = run(scripts=scripts)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        started = len(self.events())
        before = self.snapshot()
        old_tool = json.loads((self.ocr_b / 'page-0001.json').read_text())['provenance']['toolSha256']
        for name in ('run_hunyuanocr.py', 'run_hunyuanocr_second_look.py', 'run_paddleocr_vl.py',
                     'paddleocr_vl_worker.py', '_hunyuan_shared.py', '_gpu_queue.py', '_phase_budget.py', '_ocr_engines.py',
                     '_mlx_vlm_server.py', '_paddle_pool.py'):
            with (scripts / name).open('a') as handle:
                handle.write('\n# an edit that changes these bytes\n')
        for name, run in (('paddle', self.run_paddle), ('hunyuan', self.run_hunyuan),
                          ('second-look', self.run_second_look)):
            with self.subTest(runner=name):
                result, report = run(scripts=scripts)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(report['counts']['skippedCurrent'], 3)
                self.assertEqual(report['counts']['legacy'], 0)
        self.assertNotEqual(report['toolSha256'], json.loads(
            (self.ocr_b / 'page-0001.json').read_text())['secondLook']['toolSha256'])
        _, report = self.run_hunyuan(scripts=scripts, report=self.base / 'again.json')
        self.assertNotEqual(report['tool']['toolSha256'], old_tool)  # recorded hash moved on
        self.assertEqual(len(self.events()), started)
        self.assertEqual(self.snapshot(), before)

    def test_paddle_interpreter_path_and_moved_book_keep_drafts_current(self):
        self.run_paddle(['--python', sys.executable])
        record = json.loads((self.ocr_a / 'page-0001.json').read_text())
        self.assertEqual(record['ocrProfile']['packageVersions']['paddleocr'], 'offline-v1')
        self.assertIn('draftIdentity', record)
        other = self.base / 'other-venv' / 'bin'
        other.mkdir(parents=True)
        (other / 'python').symlink_to(sys.executable)
        result, report = self.run_paddle(['--python', str(other / 'python')])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['tool']['interpreter'], str(other / 'python'))
        self.assertEqual(report['counts']['skippedCurrent'], 3)
        # The whole book folder moves: renderFile is recorded, not compared.
        moved = self.base / 'moved'
        moved.mkdir()
        for name in ('renders', 'ocr-a'):
            shutil.move(str(self.base / name), str(moved / name))
        self.renders, self.ocr_a = moved / 'renders', moved / 'ocr-a'
        result, report = self.run_paddle()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report['counts']['skippedCurrent'], 3)
        self.assertEqual(report['counts']['legacy'], 0)
        self.assertEqual(json.loads((self.ocr_a / 'page-0001.json').read_text())['renderFile'],
                         str((self.base / 'renders' / 'page-0001.png').resolve()))

    def test_runtime_binary_is_in_both_hunyuan_identities_but_not_in_legacy_matching(self):
        # The user's decision of 2026-11-14: the draft identity covers the
        # runtime.  A rebuilt or re-pinned llama-server changes it; the
        # deployment (context here) does not; an external endpoint's runtime
        # is its owner's and not named.
        deployment = {'mode': 'shared-local-server', 'modelSha256': 'm', 'mmprojSha256': 'p',
                      'binarySha256': 'b1', 'ctxSize': 8192}
        profile = dict(hy.OCR_PROFILE, maxTokens=4096, deployment=deployment)
        identity = lambda changes: hy.profile_sha256(hy.draft_identity(
            dict(profile, deployment=dict(deployment, **changes))))
        self.assertNotEqual(identity({}), identity({'binarySha256': 'b2'}))
        self.assertEqual(identity({}), identity({'ctxSize': 4096}))
        endpoint = dict(profile, deployment={'mode': 'external-endpoint', 'modelId': 'x', 'revision': 'r'})
        self.assertNotIn('runtime', hy.draft_identity(endpoint))
        self.assertEqual(hy.legacy_profile_hashes(profile),
                         hy.legacy_profile_hashes(dict(profile, deployment=dict(deployment, binarySha256='b2'))))
        local = argparse.Namespace(endpoint=None, endpoint_model=None, endpoint_revision=None, max_tokens=4096)
        look = lambda binary: hy.profile_sha256(second.look_identity(
            local, {'model': 'm', 'mmproj': 'p', 'binary': binary}))
        self.assertNotEqual(look('b1'), look('b2'))
        remote = argparse.Namespace(endpoint='http://127.0.0.1:1', endpoint_model='x', endpoint_revision='r',
                                    max_tokens=4096)
        self.assertNotIn('runtime', second.look_identity(remote, {'model': None, 'mmproj': None, 'binary': None}))

    def test_older_drafts_stay_legacy_current_after_a_runtime_change(self):
        # Decision 5: the old skill's drafts are accepted as they are; they
        # carry no draft identity, so a new runtime does not make them stale.
        self.older_book()
        with self.binary.open('a') as handle:
            handle.write('# rebuilt runtime\n')
        for name, run in (('hunyuan', self.run_hunyuan), ('second-look', self.run_second_look)):
            with self.subTest(runner=name):
                result, report = run()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((report['counts']['skippedCurrent'], report['counts']['legacy']), (3, 3))
        self.assertEqual(self.events(), [])

    def test_second_look_cut_off_crop_reading_is_not_kept(self):
        self.assertEqual(second.incomplete_finish(
            {'choices': [{'finish_reason': 'length', 'message': {'content': 'x'}}]}), 'length')
        self.assertIsNone(second.incomplete_finish(
            {'choices': [{'finish_reason': 'stop', 'message': {'content': 'x'}}]}))
        self.assertIsNone(second.incomplete_finish('plain text from an offline server'))
        self.assertIsNone(second.incomplete_finish({'choices': [{'message': {'content': 'x'}}]}))

    def test_resume_checks_keep_their_call_signatures(self):
        self.run_hunyuan(['--pages', '1'])
        page = hy.PageInput(1, self.renders / 'page-0001.png', self.render_sha(1),
                            self.ocr_b / 'page-0001.json', self.ocr_b / 'page-0001.txt')
        record = json.loads(page.json_output.read_text())
        profile = record['ocrProfile']
        other_tool = hy.ToolIdentity(Path(hy.__file__), 'm', 'p', None, 'another-tool')
        self.assertEqual(hy.completion_is_current(page, other_tool, profile), (True, 'current'))
        changed = dict(profile, maxTokens=profile['maxTokens'] + 1)
        current, reason = hy.completion_is_current(page, other_tool, changed)
        self.assertFalse(current)
        self.assertIn('draft identity', reason)
        self.run_paddle(['--pages', '1'])
        a_page = paddle.PageInput(1, page.render_file, self.render_sha(1),
                                  self.ocr_a / 'page-0001.json', self.ocr_a / 'page-0001.txt')
        a_profile = json.loads(a_page.json_output.read_text())['ocrProfile']
        self.assertTrue(paddle.validate_completion(a_page, None, a_profile).valid)


if __name__ == '__main__':
    unittest.main()
