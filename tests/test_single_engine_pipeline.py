from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import apply_packet_answers
import finish_book as finish
import proofread_pages as pp
import survey_pdf


def png() -> bytes:
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 120, 160, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xfa\xfa\xfa' * 120) * 160)) + chunk(b'IEND', b''))


def make_book(root: Path, texts=('合成正文甲乙。',)) -> tuple[Path, Path]:
    images, book = root / 'scans', root / 'book'
    images.mkdir()
    for scan in range(1, len(texts) + 1):
        (images / f'page-{scan:04d}.png').write_bytes(png())
    with contextlib.redirect_stdout(io.StringIO()):
        assert survey_pdf.main([str(book), '--images-dir', str(images), '--jobs', '1']) == 0
    drafts = book / 'ocr-actual-engine'
    drafts.mkdir()
    for scan, text in enumerate(texts, 1):
        (drafts / f'page-{scan:04d}.txt').write_text(text, encoding='utf-8')
    return book, drafts


def seal(book: Path, drafts: Path, scan: int, *, mode='transcribe', coverage=True):
    text = (drafts / f'page-{scan:04d}.txt').read_text()
    out = book / 'proofread'
    out.mkdir(exist_ok=True)
    record = {
        'schemaVersion': pp.SCHEMA_VERSION, 'scanPage': scan, 'status': 'complete', 'mode': mode,
        'auditorClean': True, 'doubts': [], 'needsResearch': [],
        'pageInventory': {'page_state': 'content',
                          'source_image_sha256': pp.sha256_file(book / 'renders' / f'page-{scan:04d}.png')},
        'coverageReview': {'complete': coverage, 'result_sha256': pp.sha256_bytes(text.encode()),
                           'source_image_sha256': pp.sha256_file(book / 'renders' / f'page-{scan:04d}.png')},
        'provenance': {'renderSha256': pp.sha256_file(book / 'renders' / f'page-{scan:04d}.png'),
                       'draftSha256': pp.sha256_file(drafts / f'page-{scan:04d}.txt'),
                       'markdownSha256': pp.sha256_bytes(text.encode())},
    }
    record['provenance']['jsonPayloadSha256'] = pp.sha256_bytes(pp.canonical_json_bytes(record))
    (out / f'page-{scan:04d}.md').write_text(text)
    (out / f'page-{scan:04d}.json').write_bytes(pp.canonical_json_bytes(record))


def finish_book(book, drafts, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return finish.main([str(book), '--single-engine', '--ocr-a', str(drafts), '--no-deliver', *args])


class SingleEnginePipelineTests(unittest.TestCase):
    def test_actual_packet_replay_without_profile_then_finalizes_single_page(self):
        text = '合成正文甲乙。'
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp), (text,))
            argv = [str(book / 'renders'), str(drafts), str(book / 'proofread'), '--workers', '1',
                    '--backend', 'packet', '--no-loop-detector']
            observed = []
            for _ in range(8):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = pp.main(argv)
                if code == 0:
                    break
                self.assertEqual(code, 10)
                wave = sorted((book / 'packets').glob('wave-*'))[-1]
                answers = []
                for path in sorted(wave.glob('bundle-*.json')):
                    for item in json.loads(path.read_text())['items']:
                        kind = item['kind']
                        observed.append(kind)
                        if kind == 'page-inventory':
                            answer = {'schema_version': 1, 'page_state': 'content',
                                      'evidence': 'Synthetic fixture page; no external model.',
                                      'regions': [{'id': 'r1', 'kind': 'body', 'box': [0.1, 0.1, 0.9, 0.9],
                                                   'evidence': 'Synthetic body region.'}], 'features': []}
                        elif kind == 'writer':
                            answer = text
                        elif kind in ('content-recovery', 'coverage-review'):
                            candidate = json.loads(item['user_text'])['candidate']
                            answer = {'schema_version': 1, 'candidate_sha256': pp.sha256_bytes(candidate.encode()),
                                      'verdict': 'complete', 'regions': [{'id': 'r1', 'status': 'covered',
                                                                         'evidence': 'Synthetic fixture review.'}],
                                      'edits': []}
                        else:
                            self.fail(f'unexpected single-engine packet: {kind}')
                        answers.append({'request_id': item['request_id'],
                                        'answer': answer if isinstance(answer, str) else json.dumps(answer)})
                answer_path = wave / 'answers-single-engine.json'
                answer_path.write_text(json.dumps({'answers': answers}))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(apply_packet_answers.main([str(book / 'packets'), str(answer_path)]), 0)
            else:
                self.fail('single-engine replay did not finish')
            self.assertEqual(observed, ['page-inventory', 'writer', 'content-recovery', 'coverage-review'])
            self.assertFalse((book / 'book-profile.json').exists())
            record = json.loads((book / 'proofread' / 'page-0001.json').read_text())
            self.assertEqual(record['mode'], 'transcribe')
            self.assertEqual(record['engineCoverage']['engineB'], 0)
            self.assertEqual(record['engineTally'], {})
            self.assertTrue(record['coverageReview']['complete'])
            self.assertEqual(finish_book(book, drafts), 0)
            self.assertEqual((book / 'final.md').read_text().strip(), text)
            report = json.loads((book / 'finish' / 'finish-report.json').read_text())
            self.assertIsNone(report['inputs']['ocr_b'])
            self.assertTrue(report['options']['singleEngine'])
            self.assertEqual(report['steps']['repair']['status'], 'skipped')
            self.assertEqual(report['steps']['finalize']['auditErrors'], 0)
            self.assertFalse((book / 'repaired').exists())
            self.assertIn('一個 OCR 引擎', (book / 'doubts.md').read_text())
            self.assertNotIn('字由兩個 OCR 引擎', (book / 'doubts.md').read_text())

    def test_dual_engine_without_profile_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp))
            second = book / 'ocr-second'; second.mkdir()
            (second / 'page-0001.txt').write_text('合成正文甲乙。')
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(pp.main([str(book / 'renders'), str(drafts), str(book / 'proofread'),
                                          '--draft2-directory', str(second)]), 2)
            self.assertIn('no engine preference', err.getvalue())
            self.assertFalse((book / 'packets').exists())

    def test_stale_repair_ignored_and_unverified_joins_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp), ('甲頁未完', '乙頁接續。'))
            for scan in (1, 2): seal(book, drafts, scan)
            stale = book / 'repaired'; stale.mkdir()
            files = {'combined-repaired.md': 'STALE CONTENT', 'doubts.json': json.dumps([
                {'kind': 'stale', 'detail': 'STALE DOUBT', 'page': 1}]),
                'book-model.json': json.dumps({'stems': ['甲頁未完']}),
                'repair-report.json': '{bad json'}
            for name, text in files.items(): (stale / name).write_text(text)
            self.assertEqual(finish_book(book, drafts), 0)
            self.assertEqual((book / 'final.md').read_text().strip(), '甲頁未完\n\n乙頁接續。')
            doubts = json.loads((book / 'finish' / 'doubts.json').read_text())
            self.assertTrue(any(d['kind'] == 'single-engine-boundary' and d['page'] == 2 for d in doubts))
            self.assertNotIn('STALE DOUBT', (book / 'doubts.md').read_text())
            for name, text in files.items(): self.assertEqual((stale / name).read_text(), text)
            self.assertEqual(finish_book(book, drafts), 0)

    def test_conflicting_flags_and_missing_actual_draft_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp)); seal(book, drafts, 1)
            for flags in (('--ocr-b', str(drafts)), ('--extract-figures',)):
                self.assertEqual(finish_book(book, drafts, *flags), 2)
            (drafts / 'page-0001.txt').unlink()
            self.assertEqual(finish_book(book, drafts), 2)

    def test_final_audit_failure_prevents_success_or_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp)); seal(book, drafts, 1)
            def fail_audit(argv):
                path = Path(argv[argv.index('--json-report') + 1])
                path.write_text(json.dumps({'counts': {'errors': 1}, 'errors': [{'code': 'synthetic-error', 'line': 1}]}))
                return 1
            with mock.patch('audit_ocr_markdown.main', side_effect=fail_audit):
                self.assertEqual(finish_book(book, drafts), 1)
            report = json.loads((book / 'finish' / 'finish-report.json').read_text())
            self.assertNotIn('delivered', report)
            self.assertIn('delivery skipped', report['result'])
            self.assertIn('synthetic-error', (book / 'doubts.md').read_text())

    def test_incomplete_wrong_render_or_broken_seal_refused(self):
        for damage in ('missing', 'coverage', 'render', 'draft', 'markdown', 'merge'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                book, drafts = make_book(Path(tmp))
                seal(book, drafts, 1, mode='merge' if damage == 'merge' else 'transcribe',
                     coverage=damage != 'coverage')
                if damage == 'missing': (book / 'proofread' / 'page-0001.json').unlink()
                elif damage == 'render': (book / 'renders' / 'page-0001.png').write_bytes(b'wrong render')
                elif damage == 'draft': (drafts / 'page-0001.txt').write_text('wrong draft')
                elif damage == 'markdown': (book / 'proofread' / 'page-0001.md').write_text('wrong sealed text')
                self.assertIn(finish_book(book, drafts), (2, 3))
                self.assertFalse((book / 'final.md').exists())


if __name__ == '__main__':
    unittest.main()
