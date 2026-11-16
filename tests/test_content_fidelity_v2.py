from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _content_fidelity as f
import _packet_client as packets
import proofread_pages as pp

IMAGE = b'synthetic source, no visual accuracy claim'
TABLE = '|  |  |\n| --- | --- |\n| 11 | 22 |'


def inventory(*, kind='table', cell=None, issues=None, text='22'):
    obj = {'schema_version': 2, 'contract_version': f.CONTRACT_VERSION,
           'source_image_sha256': hashlib.sha256(IMAGE).hexdigest(),
           'page_state': 'uncertain' if issues else 'content', 'evidence': 'synthetic full-page fixture',
           'regions': [{'id': 'r', 'kind': kind, 'box': [0, 0, 1, 1], 'evidence': 'central source region'}],
           'features': [] if kind == 'furniture' else [{'id': 'f', 'region_id': 'r', 'text': text,
                      'before': '', 'after': '', **({'cell': cell or {'kind': 'grid', 'table_region_id': 'r',
                      'row': 1, 'col': 1, 'rows': 2, 'cols': 2, 'rowspan': 1, 'colspan': 1}} if kind == 'table' else {})}],
           'issues': issues or []}
    return f.parse_inventory(json.dumps(obj), IMAGE)


def answer(inv, candidate, *, mappings=None, resolutions=None, status='covered', verdict='complete', edits=None):
    return {'schema_version': 2, 'contract_version': f.CONTRACT_VERSION,
            'source_image_sha256': inv['source_image_sha256'], 'inventory_sha256': inv['inventory_sha256'],
            'candidate_sha256': f.text_sha256(candidate), 'verdict': verdict,
            'regions': [{'id': 'r', 'status': status, 'evidence': 'synthetic ink and position checked'}],
            'edits': edits or [], 'resolutions': resolutions or [], 'table_mappings': mappings or []}


def mapping(result, *, orientation='identity', rows=2, cols=2, index=0):
    return {'region_id': 'r', 'table_index': index, 'orientation': orientation,
            'source_rows': rows, 'source_cols': cols, 'result_sha256': f.text_sha256(result),
            'evidence': 'synthetic source axes and cell correspondence checked'}


class V2TableOwnershipTests(unittest.TestCase):
    def test_unlabelled_grid_needs_explicit_mapping_and_stays_in_its_cell(self):
        inv = inventory()
        self.assertEqual(f.feature_losses(TABLE, inv), ['f'])
        good = answer(inv, TABLE, mappings=[mapping(TABLE)])
        self.assertTrue(f.apply_review(json.dumps(good), TABLE, inv)[1]['complete'])
        swapped = TABLE.replace('11 | 22', '22 | 11')
        bad = answer(inv, swapped, mappings=[mapping(swapped)])
        self.assertFalse(f.apply_review(json.dumps(bad), swapped, inv)[1]['complete'])
        self.assertEqual(f.feature_losses('11 22', inv, [mapping('11 22')]), ['f'])

    def test_full_multiline_labels_are_exact_not_abbreviations_or_digit_substrings(self):
        table = '| 類 | 三年<br>甲 | 10 |\n| --- | --- | --- |\n| 乙 | 22 | 33 |'
        inv = inventory(cell={'kind': 'labels', 'row_label': '乙', 'column_label': '三年甲'})
        self.assertEqual(f.feature_losses(table, inv), [])
        for label in ('甲', '1'):
            bad = inventory(cell={'kind': 'labels', 'row_label': '乙', 'column_label': label})
            self.assertEqual(f.feature_losses(table, bad), ['f'])
        for key in ('row_label', 'column_label'):
            with self.assertRaises(f.FidelityError):
                inventory(cell={'kind': 'labels', 'row_label': '乙', 'column_label': '三年甲', key: ''})

    def test_transpose_needs_evidence_mapping_and_as_printed_refuses_it(self):
        inv = inventory(cell={'kind': 'grid', 'table_region_id': 'r', 'row': 1, 'col': 2,
                             'rows': 2, 'cols': 3, 'rowspan': 1, 'colspan': 1})
        table = '| a | b |\n| --- | --- |\n| c | d |\n| e | 22 |'
        raw = json.dumps(answer(inv, table, mappings=[mapping(table, orientation='transpose', rows=2, cols=3)]))
        self.assertTrue(f.apply_review(raw, table, inv)[1]['complete'])
        with self.assertRaisesRegex(f.FidelityError, 'axis'):
            f.apply_review(raw, table, inv, table_orientation='as-printed')
        value = json.loads(raw); value['table_mappings'][0]['evidence'] = ''
        with self.assertRaises(f.FidelityError): f.apply_review(json.dumps(value), table, inv)

    def test_merged_cell_spans_and_result_bytes_are_bound(self):
        table = '<table><tr><td>甲</td><td rowspan="2">22</td></tr><tr><td>乙</td></tr></table>'
        inv = inventory(cell={'kind': 'grid', 'table_region_id': 'r', 'row': 0, 'col': 1,
                             'rows': 2, 'cols': 2, 'rowspan': 2, 'colspan': 1})
        raw = answer(inv, table, mappings=[mapping(table)])
        self.assertTrue(f.apply_review(json.dumps(raw), table, inv)[1]['complete'])
        changed = table + '\n'
        raw['candidate_sha256'] = f.text_sha256(changed)
        with self.assertRaises(f.FidelityError) as caught:
            f.apply_review(json.dumps(raw), changed, inv)
        # The answer's mapping copied the hash of other bytes: a slip in the
        # answer, which its one correction request may fix.
        self.assertTrue(caught.exception.correctable)
        split = '| 甲 | 22 |\n| --- | --- |\n| 乙 |  |'
        with self.assertRaisesRegex(f.FidelityError, 'span'):
            f.apply_review(json.dumps(answer(inv, split, mappings=[mapping(split)])), split, inv)

    def test_source_grid_bounds_region_dimensions_and_axis_names_rejected(self):
        for change in ({'row': -1}, {'col': 2}, {'rows': True}, {'rowspan': 0}, {'table_region_id': 'wrong'}):
            cell = {'kind': 'grid', 'table_region_id': 'r', 'row': 1, 'col': 1, 'rows': 2, 'cols': 2, 'rowspan': 1, 'colspan': 1, **change}
            with self.assertRaises(f.FidelityError): inventory(cell=cell)
        inv = inventory()
        for change in ({'orientation': 'rotate'}, {'source_cols': 3}, {'table_index': 2}):
            with self.assertRaises(f.FidelityError):
                f.apply_review(json.dumps(answer(inv, TABLE, mappings=[{**mapping(TABLE), **change}])), TABLE, inv)

    def test_mapping_rebind_only_preserves_same_ordered_cells(self):
        inv = inventory()
        html = '<table><tr><td></td><td></td></tr><tr><td>11</td><td>22</td></tr></table>'
        rebound = f.rebind_unchanged_tables(TABLE, html, [mapping(TABLE)])
        self.assertEqual(f.feature_losses(html, inv, rebound), [])
        self.assertEqual(f.rebind_unchanged_tables(TABLE, html.replace('11', '22'), [mapping(TABLE)]), [])
        self.assertEqual(f.rebind_unchanged_tables(TABLE, html, [mapping(TABLE + ' ')]), [])

    def test_all_table_regions_require_valid_structure_even_without_features(self):
        inv = inventory()
        inv["features"] = []
        inv["inventory_sha256"] = f.inventory_sha256(inv)
        prose = "甲乙丙丁"
        result, record = f.apply_review(json.dumps(answer(inv, prose)), prose, inv)
        self.assertFalse(record["complete"])
        self.assertEqual(record["unmapped_table_regions"], ["r"])
        candidate = "| 甲 | 乙 |\n| --- | --- |\n| 丙 | 丁 |"
        raw_map = mapping(candidate)
        del raw_map["result_sha256"]
        _, record = f.apply_review(json.dumps(answer(inv, candidate, mappings=[raw_map])), candidate, inv)
        self.assertTrue(record["complete"])
        self.assertEqual(record["table_mappings"][0]["result_sha256"], f.text_sha256(candidate))

    def test_mapping_is_consumer_sealed_after_explicit_edit(self):
        inv = inventory()
        raw_map = mapping(TABLE)
        del raw_map["result_sha256"]
        candidate = TABLE.replace('22', '2')
        edit = {'region_id': 'r', 'before': '11 | ', 'old': '2', 'after': ' |', 'new': '22', 'evidence': 'synthetic source correction'}
        result, record = f.apply_review(json.dumps(answer(inv, candidate, mappings=[raw_map], edits=[edit])), candidate, inv)
        self.assertEqual(result, TABLE)
        self.assertTrue(record['complete'])
        self.assertEqual(record['table_mappings'][0]['result_sha256'], f.text_sha256(TABLE))

    def test_identical_labels_across_tables_cannot_swap_region_ownership(self):
        inv = inventory(cell={'kind': 'labels', 'row_label': '甲', 'column_label': '值'}, text='12')
        inv['regions'].append({**inv['regions'][0], 'id': 'r2'})
        inv['features'].append({**inv['features'][0], 'id': 'f2', 'region_id': 'r2', 'text': '13'})
        inv['inventory_sha256'] = f.inventory_sha256(inv)
        one = '| 類 | 值 |\n| --- | --- |\n| 甲 | 13 |'
        two = one.replace('13', '12')
        candidate = one + '\n\n' + two
        maps = [mapping(candidate), {**mapping(candidate, index=1), 'region_id': 'r2'}]
        self.assertEqual(f.feature_losses(candidate, inv, maps), ['f', 'f2'])
        self.assertEqual(f.feature_losses(candidate, inv), ['f', 'f2'])
        good = candidate.replace('13', 'TEMP').replace('12', '13').replace('TEMP', '12')
        maps = [{**m, 'result_sha256': f.text_sha256(good)} for m in maps]
        self.assertEqual(f.feature_losses(good, inv, maps), [])


class V2IssueAndCorrectionTests(unittest.TestCase):
    def test_furniture_crop_issue_can_be_excluded_but_body_unknown_crop_stays_open(self):
        issue = {'id': 'cut', 'region_id': 'r', 'kind': 'crop', 'evidence': 'edge crop in source'}
        inv = inventory(kind='furniture', issues=[issue])
        raw = answer(inv, '', status='excluded')
        self.assertFalse(f.apply_review(json.dumps(raw), '', inv)[1]['complete'])
        raw['resolutions'] = [{'issue_id': 'cut', 'status': 'excluded', 'evidence': 'cropped mark belongs only to folio furniture'}]
        self.assertTrue(f.apply_review(json.dumps(raw), '', inv)[1]['complete'])
        for region in ('r', None):
            body = inventory(kind='body', text='甲', issues=[{**issue, 'region_id': region}])
            raw = answer(body, '甲', resolutions=[{'issue_id': 'cut', 'status': 'excluded', 'evidence': 'exclusion claim'}])
            with self.assertRaisesRegex(f.FidelityError, 'cannot be excluded'):
                f.apply_review(json.dumps(raw), '甲', body)
            raw['resolutions'] = []
            self.assertFalse(f.apply_review(json.dumps(raw), '甲', body)[1]['complete'])
            raw['resolutions'] = [{'issue_id': 'cut', 'status': 'resolved', 'evidence': 'whole uncropped original confirms body boundary'}]
            self.assertTrue(f.apply_review(json.dumps(raw), '甲', body)[1]['complete'])

    def test_v1_reads_historically_but_never_completes(self):
        old = {'schema_version': 1, 'page_state': 'content', 'evidence': 'historical',
               'regions': [{'id': 'r', 'kind': 'body', 'box': [0, 0, 1, 1], 'evidence': 'historical'}], 'features': []}
        inv = f.parse_inventory(json.dumps(old), IMAGE)
        self.assertEqual(inv['schema_version'], 1)
        with self.assertRaisesRegex(f.FidelityError, 'historical'):
            f.apply_review(json.dumps({'schema_version': 1}), '甲', inv)

    def test_inventory_correction_preserves_original_answer_and_bindings_once(self):
        good = inventory(kind='furniture'); good.pop('inventory_sha256')
        bad = {**good, 'features': [{'id': 'f', 'region_id': 'r', 'text': '22', 'before': '', 'after': ''}]}
        calls = []
        class Client:
            def ask_answering(self, system, prompt, image, max_tokens, kind):
                calls.append((kind, json.loads(prompt), image))
                return json.dumps(bad if len(calls) == 1 else good), {}
        result, _ = f.inventory_page(Client(), IMAGE, ['22'], 1024)
        self.assertEqual([c[0] for c in calls], ['page-inventory', 'page-inventory-correction'])
        self.assertEqual(calls[1][1]['original_answer_sha256'], f.text_sha256(json.dumps(bad)))
        self.assertEqual(calls[1][1]['validation_error']['code'], 'excluded-feature')
        self.assertEqual(calls[1][1]['validation_error']['path'], '$.features[0].region_id')
        self.assertEqual(calls[0][1]['source_image_sha256'], calls[1][1]['source_image_sha256'])
        self.assertEqual(result['inventory_sha256'], f.inventory_sha256(result))
        self.assertIn('correction', result)

    def test_review_correction_is_bounded_and_preserves_same_candidate(self):
        inv = inventory(kind='body', text='22'); candidate = '2'; calls = []
        class Client:
            def ask_answering(self, system, prompt, image, max_tokens, kind):
                calls.append(json.loads(prompt))
                return json.dumps(answer(inv, candidate)), {}
        with self.assertRaises(f.FidelityError) as caught:
            f.review_page(Client(), IMAGE, candidate, inv, 100, stage='coverage-review')
        self.assertEqual(len(calls), 2)
        for key in ('candidate_sha256', 'inventory_sha256', 'source_image_sha256', 'candidate'):
            self.assertEqual(calls[0][key], calls[1][key])
        self.assertEqual(caught.exception.correction['corrected_answer_sha256'], f.text_sha256(json.dumps(answer(inv, candidate))))

    def test_true_uncertainty_and_stale_evidence_never_trigger_corrective_call(self):
        # Changed 2026-11-14 (user decision 4): a binding the answer copied
        # wrong is one more answer mistake and gets the one correction request;
        # stored evidence bound elsewhere still gets none.
        inv = inventory(kind='body', text='22'); calls = []
        class Client:
            def ask_answering(self, system, prompt, image, max_tokens, kind):
                calls.append(kind)
                return json.dumps(self.raw), {}
        client = Client(); client.raw = answer(inv, '2', verdict='uncertain')
        self.assertFalse(f.review_page(client, IMAGE, '2', inv, 100, stage='coverage-review')[1]['complete'])
        self.assertEqual(len(calls), 1)
        for key in ('candidate_sha256', 'inventory_sha256', 'source_image_sha256', 'schema_version'):
            calls.clear(); client.raw = {**answer(inv, '22'), key: 1 if key == 'schema_version' else '0' * 64}
            with self.assertRaises(f.FidelityError) as caught:
                f.review_page(client, IMAGE, '22', inv, 100, stage='coverage-review')
            self.assertTrue(caught.exception.correctable)
            self.assertEqual(calls, ['coverage-review', 'coverage-review-correction'])
            self.assertIn('final_error', caught.exception.correction)
        changed = {**inv, 'evidence': 'different inventory'}
        calls.clear()
        with self.assertRaises(f.FidelityError): f.review_page(client, IMAGE, '22', changed, 100, stage='coverage-review')
        self.assertEqual(calls, [])

    def test_packet_corrective_request_replays_stably_without_third_attempt(self):
        inv = inventory(kind='body', text='22'); raw = json.dumps(answer(inv, '2'))
        with tempfile.TemporaryDirectory() as tmp:
            client = packets.PacketClient(Path(tmp))
            with self.assertRaises(packets.PacketPending): f.review_page(client, IMAGE, '2', inv, 100, stage='coverage-review')
            first = client.pending_items()[0]
            client.answers[first.request_id] = {'request_id': first.request_id, 'answer': raw}
            with self.assertRaises(packets.PacketPending): f.review_page(client, IMAGE, '2', inv, 100, stage='coverage-review')
            second = next(i for i in client.pending_items() if i.kind.endswith('-correction'))
            client.answers[second.request_id] = {'request_id': second.request_id, 'answer': raw}
            for _ in range(2):
                with self.assertRaises(f.FidelityError): f.review_page(client, IMAGE, '2', inv, 100, stage='coverage-review')
            self.assertEqual(len(client._pending), 2)


class V2FormatterRegressionTests(unittest.TestCase):
    def test_two_valid_tables_rejected_for_abbreviated_ownership_seal_a_doubt_not_clean_prose(self):
        # Changed 2026-11-14 (user decision 4): the page is sealed with the
        # image-reviewed text and a content-fidelity doubt instead of failing.
        table = '| 年限甲 | 值 |\n| --- | --- |\n| 乙 | 22 |'
        inv = inventory(cell={'kind': 'labels', 'row_label': '乙', 'column_label': '值'})
        # Put 22 under a different complete heading, reproducing a shorthand
        # locator mismatch without incorporating any real benchmark text.
        table = '| 值 | 年限甲 |\n| --- | --- |\n| 乙 | 22 |'
        inv = inventory(cell={'kind': 'labels', 'row_label': '乙', 'column_label': '甲'})
        draft = '值年限甲乙22'; calls = []
        class Client:
            model, effort = 'synthetic', 'test'
            def ask_answering(self, system, prompt, image, max_tokens, kind='other', **kwargs):
                calls.append(kind)
                if kind == 'page-inventory': return json.dumps(inv), {}
                if kind == 'content-recovery':
                    candidate = json.loads(prompt)['candidate']
                    edits = [] if candidate == draft else [{'region_id': 'r', 'before': '', 'old': candidate, 'after': '', 'new': draft, 'evidence': 'synthetic recovery'}]
                    return json.dumps(answer(inv, candidate, edits=edits, verdict='uncertain')), {}
                if kind in {'format', 'format-retry'}: return table, {}
                if kind == 'census': return '符號：0\n雙圈：0\n形狀：無', {}
                raise AssertionError(kind)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); render = root / 'page.png'; render.write_bytes(IMAGE)
            a = root / 'a.txt'; b = root / 'b.txt'; a.write_text(draft); b.write_text(draft)
            job = pp.PageJob(1, render, a, None, b, root / 'out.json', root / 'out.md')
            args = pp.build_parser().parse_args([str(root), str(root), str(root), '--content-fidelity', 'on'])
            args.prefer_engine, args.furniture, args.profile_data = '', frozenset(), {}
            with mock.patch.object(pp, 'block_tiles', return_value=[]), mock.patch.object(pp, 'column_reach', return_value=1):
                record = pp.proofread_page(job, Client(), args)
            self.assertIn('format-retry', calls)
            self.assertNotIn('coverage-review', calls)
            self.assertEqual(job.out_md.read_text(), draft)
            self.assertEqual(record['contentFidelityStatus'], 'doubt')
            self.assertFalse(record['auditorClean'])
            self.assertTrue(any(d['kind'] == f.DOUBT_KIND and 'no table' in d['detail'] for d in record['doubts']))
            f.validate_completed_record(record, draft, hashlib.sha256(IMAGE).hexdigest())
            saved = [json.loads(p.read_text()) for p in root.glob('out.fidelity-failure-*.json')]
            saved = next(s for s in saved if s['detail'].get('code') == 'coverage-structure-mismatch')
            self.assertEqual(saved['formatter_rejected_output'], table)
            self.assertEqual(saved['formatter_rejected_retry'], table)
            self.assertEqual(saved['candidate'], draft)

    def test_new_unlabelled_grid_reaches_final_mapping_instead_of_false_fallback(self):
        table = '| 甲 | 乙 |\n| --- | --- |\n| 丙 | 22 |'
        inv = inventory(); draft = '甲乙丙22'; calls = []
        class Client:
            model, effort = 'synthetic', 'test'
            def ask_answering(self, system, prompt, image, max_tokens, kind='other', **kwargs):
                calls.append(kind)
                if kind == 'page-inventory': return json.dumps(inv), {}
                if kind in {'content-recovery', 'coverage-review'}:
                    candidate = json.loads(prompt)['candidate']
                    if kind == 'coverage-review':
                        m = mapping(candidate); del m['result_sha256']
                        return json.dumps(answer(inv, candidate, mappings=[m])), {}
                    edits = [] if candidate == draft else [{'region_id': 'r', 'before': '', 'old': candidate, 'after': '', 'new': draft, 'evidence': 'synthetic source'}]
                    return json.dumps(answer(inv, candidate, edits=edits, verdict='uncertain')), {}
                if kind == 'format': return table, {}
                if kind == 'census': return '符號：0\n雙圈：0\n形狀：無', {}
                raise AssertionError(kind)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); render = root / 'page.png'; render.write_bytes(IMAGE)
            a = root / 'a.txt'; b = root / 'b.txt'; a.write_text(draft); b.write_text(draft)
            job = pp.PageJob(1, render, a, None, b, root / 'out.json', root / 'out.md')
            args = pp.build_parser().parse_args([str(root), str(root), str(root), '--content-fidelity', 'on'])
            args.prefer_engine, args.furniture, args.profile_data = '', frozenset(), {}
            with mock.patch.object(pp, 'block_tiles', return_value=[]), mock.patch.object(pp, 'column_reach', return_value=1):
                record = pp.proofread_page(job, Client(), args)
            self.assertNotIn('format-retry', calls)
            self.assertEqual(job.out_md.read_text(), table)
            self.assertTrue(record['coverageReview']['complete'])
            f.validate_completed_record(record, table, hashlib.sha256(IMAGE).hexdigest())


if __name__ == '__main__': unittest.main()
