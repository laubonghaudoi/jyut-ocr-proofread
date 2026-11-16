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
import _content_fidelity as fidelity
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
    image = (book / 'renders' / f'page-{scan:04d}.png').read_bytes()
    image_sha = pp.sha256_bytes(image)
    inventory = fidelity.parse_inventory(json.dumps({
        'schema_version': 2, 'contract_version': 'content-fidelity-v2',
        'source_image_sha256': image_sha,
        'page_state': 'blank' if mode == 'blank-page' else 'content',
        'evidence': 'Synthetic fixture; no model or external OCR.',
        'regions': [] if mode == 'blank-page' else [
            {'id': 'r1', 'kind': 'body', 'box': [0.1, 0.1, 0.9, 0.9],
             'evidence': 'Synthetic body region.'}], 'features': [], 'issues': [],
    }), image)
    review = None
    if mode != 'blank-page':
        _, review = fidelity.apply_review(json.dumps({
            'schema_version': 2, 'contract_version': 'content-fidelity-v2',
            'source_image_sha256': image_sha, 'inventory_sha256': inventory['inventory_sha256'],
            'candidate_sha256': fidelity.text_sha256(text),
            'verdict': 'complete', 'regions': [
                {'id': 'r1', 'status': 'covered', 'evidence': 'Synthetic fixture review.'}],
            'edits': [], 'resolutions': [], 'table_mappings': [],
        }), text, inventory)
        review['complete'] = coverage
    out = book / 'proofread'
    out.mkdir(exist_ok=True)
    record = {
        'schemaVersion': pp.SCHEMA_VERSION, 'scanPage': scan, 'status': 'complete', 'mode': mode,
        'contentFidelityContract': 'content-fidelity-v2',
        'auditorClean': True, 'doubts': [], 'needsResearch': [],
        'pageInventory': inventory, 'coverageReview': review,
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
        # Changed 2026-11-14 (user decision 4): content fidelity is a switch,
        # off by default; the page then asks only the writer question and is
        # sealed without the v2 record.  With it on, the three questions are
        # asked as before.
        for switch, kinds in (('off', ['writer']),
                              ('on', ['page-inventory', 'writer', 'content-recovery', 'coverage-review'])):
            with self.subTest(content_fidelity=switch):
                self.packet_replay_finalizes(switch, kinds)

    def packet_replay_finalizes(self, switch, kinds):
        text = '合成正文甲乙。'
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp), (text,))
            argv = [str(book / 'renders'), str(drafts), str(book / 'proofread'), '--workers', '1',
                    '--backend', 'packet', '--no-loop-detector', '--content-fidelity', switch,
                    '--single-engine']
            observed = []
            for _ in range(8):
                with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
                    code = pp.main(argv)
                if code == 0:
                    break
                self.assertEqual(code, 10, output.getvalue() + errors.getvalue())
                wave = sorted((book / 'packets').glob('wave-*'))[-1]
                answers = []
                for path in sorted(wave.glob('bundle-*.json')):
                    for item in json.loads(path.read_text())['items']:
                        kind = item['kind']
                        observed.append(kind)
                        if kind == 'page-inventory':
                            request = json.loads(item['user_text'])
                            answer = {'schema_version': 2, 'contract_version': 'content-fidelity-v2',
                                      'source_image_sha256': request['source_image_sha256'],
                                      'page_state': 'content',
                                      'evidence': 'Synthetic fixture page; no external model.',
                                      'regions': [{'id': 'r1', 'kind': 'body', 'box': [0.1, 0.1, 0.9, 0.9],
                                                   'evidence': 'Synthetic body region.'}], 'features': [], 'issues': []}
                        elif kind == 'writer':
                            answer = text
                        elif kind in ('content-recovery', 'coverage-review'):
                            request = json.loads(item['user_text'])
                            answer = {'schema_version': 2, 'contract_version': 'content-fidelity-v2',
                                      'source_image_sha256': request['source_image_sha256'],
                                      'inventory_sha256': request['inventory_sha256'],
                                      'candidate_sha256': request['candidate_sha256'],
                                      'verdict': 'complete', 'regions': [{'id': 'r1', 'status': 'covered',
                                                                         'evidence': 'Synthetic fixture review.'}],
                                      'edits': [], 'resolutions': [], 'table_mappings': []}
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
            self.assertEqual(observed, kinds)
            self.assertFalse((book / 'book-profile.json').exists())
            record = json.loads((book / 'proofread' / 'page-0001.json').read_text())
            self.assertEqual(record['mode'], 'transcribe')
            self.assertEqual(record['engineCoverage']['engineB'], 0)
            self.assertEqual(record['engineTally'], {})
            if switch == 'on':
                self.assertTrue(record['coverageReview']['complete'])
                self.assertEqual(record['contentFidelityStatus'], 'complete')
            else:
                self.assertFalse(fidelity.carries_record(record))
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

    def test_dual_engine_without_profile_rejected_beyond_a_short_document(self):
        # Changed 2026-11-14 (user decision 2, two engines the default): a
        # document of at most 4 pages takes book_profile.py's short-document
        # fast path with no profile; a longer one still needs the profile.
        for pages, code in ((1, 10), (pp.SHORT_DOC_MAX_PAGES, 10), (pp.SHORT_DOC_MAX_PAGES + 1, 2)):
            with self.subTest(pages=pages), tempfile.TemporaryDirectory() as tmp:
                book, drafts = make_book(Path(tmp), ('合成正文甲乙。',) * pages)
                second = book / 'ocr-second'; second.mkdir()
                for scan in range(1, pages + 1):
                    (second / f'page-{scan:04d}.txt').write_text('合成正文甲乙。')
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(pp.main([str(book / 'renders'), str(drafts), str(book / 'proofread'),
                                              '--draft2-directory', str(second), '--pages', '1']), code)
                if code == 2:
                    self.assertIn('no engine preference', err.getvalue())
                    self.assertFalse((book / 'packets').exists())
                else:
                    report = json.loads((book / 'proofread-run.json').read_text())
                    self.assertIn('short document', report['enginePreference']['source'])
                    self.assertEqual(report['enginePreference']['engine'], 'adjudicate')

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

    def test_final_audit_errors_are_doubts_and_the_book_is_delivered(self):
        # Changed 2026-11-14 (user decision 9: final-audit errors become doubts
        # and delivery still happens, the same rule for both routes).  Before,
        # the single-engine route exited 1 and skipped the delivery.
        def fail_audit(argv):
            path = Path(argv[argv.index('--json-report') + 1])
            path.write_text(json.dumps({'counts': {'errors': 1}, 'errors': [{'code': 'synthetic-error', 'line': 1}]}))
            return 1
        for source in ('pdf', 'images-dir'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                book, drafts = make_book(Path(tmp)); seal(book, drafts, 1)
                pdf = Path(tmp) / 'source.pdf'
                if source == 'pdf':
                    pdf.write_bytes(b'%PDF-1.4 synthetic source\n')
                    manifest = json.loads((book / 'page-manifest.json').read_text())
                    manifest.pop('source_kind', None)
                    manifest.update(source_pdf=str(pdf), source_sha256=pp.sha256_file(pdf))
                    (book / 'page-manifest.json').write_text(json.dumps(manifest))
                with mock.patch('audit_ocr_markdown.main', side_effect=fail_audit), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(finish.main([str(book), '--single-engine', '--ocr-a', str(drafts)]), 0)
                report = json.loads((book / 'finish' / 'finish-report.json').read_text())
                self.assertIn('1 final-audit error(s) listed in doubts.md', report['result'])
                self.assertIn('synthetic-error', (book / 'doubts.md').read_text())
                if source == 'pdf':
                    self.assertEqual(sorted(d['kind'] for d in report['delivered'] if d['written']),
                                     ['doubts', 'final'])
                    self.assertEqual((Path(tmp) / 'source.md').read_text(), (book / 'final.md').read_text())
                else:
                    # survey_pdf.py writes source_pdf "" for a folder of images: no copy is tried.
                    self.assertEqual(report['delivered'], [])
                    self.assertIn('skipping beside-PDF copy', (book / 'finish' / 'finish-book.log').read_text())

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

    def test_finishing_requires_v2_evidence_in_every_nonblank_mode(self):
        damage_cases = ('contract', 'inventory-version', 'review-version', 'inventory-hash',
                        'review-inventory-hash', 'review-image', 'candidate-hash', 'result-hash', 'complete',
                        'review-regions', 'review-object', 'inventory-object')
        for mode in ('transcribe', 'merge', 'image-recovery'):
            for damage in (None, *damage_cases):
                with self.subTest(mode=mode, damage=damage), tempfile.TemporaryDirectory() as tmp:
                    book, drafts = make_book(Path(tmp))
                    seal(book, drafts, 1, mode=mode)
                    path = book / 'proofread' / 'page-0001.json'
                    record = json.loads(path.read_text())
                    if damage == 'contract': record.pop('contentFidelityContract')
                    elif damage == 'inventory-version': record['pageInventory']['schema_version'] = 1
                    elif damage == 'review-version': record['coverageReview']['schema_version'] = 1
                    elif damage == 'inventory-hash': record['pageInventory']['inventory_sha256'] = '0' * 64
                    elif damage == 'review-inventory-hash': record['coverageReview']['inventory_sha256'] = '0' * 64
                    elif damage == 'review-image': record['coverageReview']['source_image_sha256'] = '0' * 64
                    elif damage == 'candidate-hash': record['coverageReview'].pop('candidate_sha256')
                    elif damage == 'result-hash': record['coverageReview']['result_sha256'] = '0' * 64
                    elif damage == 'complete': record['coverageReview']['complete'] = False
                    elif damage == 'review-regions': record['coverageReview']['regions'] = []
                    elif damage == 'review-object': record['coverageReview'] = ['invalid object']
                    elif damage == 'inventory-object': record['pageInventory'] = ['invalid object']
                    record['provenance'].pop('jsonPayloadSha256')
                    record['provenance']['jsonPayloadSha256'] = pp.sha256_bytes(pp.canonical_json_bytes(record))
                    path.write_bytes(pp.canonical_json_bytes(record))
                    inputs = finish.Inputs(book, book / 'proofread', book / 'renders', drafts,
                                           None, None, book / 'page-manifest.json',
                                           single_engine=mode == 'transcribe')
                    if damage is None:
                        sealed = finish.read_sealed(inputs, finish.Log(io.StringIO()))
                        self.assertTrue(sealed.rows[0]['sealChecks']['contentFidelityV2'])
                    else:
                        with self.assertRaises(finish.FinishError) as error:
                            finish.read_sealed(inputs, finish.Log(io.StringIO()))
                        self.assertEqual(error.exception.code, finish.EXIT_USAGE)
                        self.assertIn('content-fidelity-v2', str(error.exception))

    def test_blank_finishing_requires_v2_inventory_without_a_review(self):
        for single_engine in (True, False):
            for version in (1, 2):
                with self.subTest(single_engine=single_engine, version=version), tempfile.TemporaryDirectory() as tmp:
                    book, drafts = make_book(Path(tmp), ('',))
                    seal(book, drafts, 1, mode='blank-page')
                    path = book / 'proofread' / 'page-0001.json'
                    record = json.loads(path.read_text())
                    record['pageInventory']['schema_version'] = version
                    record['provenance'].pop('jsonPayloadSha256')
                    record['provenance']['jsonPayloadSha256'] = pp.sha256_bytes(pp.canonical_json_bytes(record))
                    path.write_bytes(pp.canonical_json_bytes(record))
                    inputs = finish.Inputs(book, book / 'proofread', book / 'renders', drafts,
                                           None, None, book / 'page-manifest.json', single_engine=single_engine)
                    if version == 2:
                        sealed = finish.read_sealed(inputs, finish.Log(io.StringIO()))
                        self.assertEqual(sealed.records[1]['mode'], 'blank-page')
                    else:
                        with self.assertRaises(finish.FinishError) as error:
                            finish.read_sealed(inputs, finish.Log(io.StringIO()))
                        self.assertEqual(error.exception.code, finish.EXIT_USAGE)

    def test_existing_finish_does_not_resume_from_a_resealed_v1_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            book, drafts = make_book(Path(tmp))
            seal(book, drafts, 1)
            self.assertEqual(finish_book(book, drafts), finish.EXIT_DONE)
            final_bytes = (book / 'final.md').read_bytes()
            path = book / 'proofread' / 'page-0001.json'
            record = json.loads(path.read_text())
            record.pop('contentFidelityContract')
            record['pageInventory']['schema_version'] = 1
            record['coverageReview']['schema_version'] = 1
            record['provenance'].pop('jsonPayloadSha256')
            record['provenance']['jsonPayloadSha256'] = pp.sha256_bytes(pp.canonical_json_bytes(record))
            path.write_bytes(pp.canonical_json_bytes(record))
            self.assertEqual(finish_book(book, drafts), finish.EXIT_USAGE)
            self.assertEqual((book / 'final.md').read_bytes(), final_bytes)


if __name__ == '__main__':
    unittest.main()
