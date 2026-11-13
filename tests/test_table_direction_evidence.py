from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import unittest
import zlib


SCRIPTS_DIR = (Path(__file__).resolve().parents[1] / 'scripts')
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import proofread_pages as pp


def image_fixture() -> bytes:
    """A synthetic page image: tests check request plumbing, not vision accuracy."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff\x00\x00\x00' * 2))
            + chunk(b'IEND', b''))


class Replies:
    def __init__(self, *answers: str):
        self.answers = iter(answers)
        self.calls: list[dict] = []

    def ask_answering(self, system, user, image, max_tokens, *, kind):
        self.calls.append({'system': system, 'user': user, 'image': image, 'kind': kind})
        return next(self.answers), {}


def choices(table: str, *, reversed_cells: set[tuple[int, int]], swapped: bool) -> str:
    records = []
    for cell in pp.direction_candidates(pp.first_table(table)):
        is_reversed = (cell.row, cell.col) in reversed_cells
        choice = '甲' if is_reversed == swapped else '乙'
        records.append({'row': cell.row, 'col': cell.col, 'choice': choice,
                        'evidence': '圖上指定格嘅字位置同局部排字方向清楚可見'})
    return json.dumps(records, ensure_ascii=False)


class TableDirectionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.image = image_fixture()
        self.table = '| 乙甲 | 一 |\n| --- | --- |\n| 二 | 丙丁 |'

    def test_reversal_requires_image_and_exact_cell_locators(self):
        client = Replies(choices(self.table, reversed_cells={(0, 0)}, swapped=False),
                         choices(self.table, reversed_cells={(0, 0)}, swapped=True))
        out, records = pp.fix_cell_direction(self.table, client, 128, block=7,
                                             render_bytes=self.image, context='box=[0,0,1,1]')
        self.assertEqual(pp.first_table(out).grid(), [['甲乙', '一'], ['二', '丙丁']])
        self.assertEqual([record['outcome'] for record in records], ['turned', 'kept'])
        self.assertTrue(all(call['image'] == self.image for call in client.calls))
        self.assertTrue(all('row=0, col=0, rowspan=1, colspan=1' in call['user']
                            and 'block=7' in call['user'] and self.table in call['user']
                            for call in client.calls))
        self.assertEqual(records[0]['sourceImageSha256'], pp.sha256_bytes(self.image))
        self.assertEqual(records[0]['tableSha256'], pp.sha256_bytes(self.table.encode()))

    def test_short_reversed_looking_original_is_kept(self):
        client = Replies(choices(self.table, reversed_cells=set(), swapped=False),
                         choices(self.table, reversed_cells=set(), swapped=True))
        out, records = pp.fix_cell_direction(self.table, client, 128, render_bytes=self.image)
        self.assertEqual(out, self.table)
        self.assertTrue(all(row['outcome'] == 'kept' for row in records))

    def test_missing_image_keeps_text_and_emits_undecided_without_asking(self):
        client = Replies()
        out, records = pp.fix_cell_direction(self.table, client, 128)
        self.assertEqual(out, self.table)
        self.assertEqual(client.calls, [])
        self.assertTrue(all(row['outcome'] == 'undecided' for row in records))
        self.assertTrue(all('no source image' in row['visualEvidence'] for row in records))

    def test_rightmost_label_and_merged_header_are_candidates_once(self):
        source = ('<table><tr><td>一</td><td>二</td><td>三</td></tr>'
                  '<tr><td>四</td><td colspan="2">乙甲</td></tr></table>')
        candidates = pp.direction_candidates(pp.first_table(source))
        self.assertEqual([(c.row, c.col, c.colspan) for c in candidates], [(1, 1, 2)])
        one_column = '<table><tr><th>乙甲</th></tr><tr><td>丙丁</td></tr></table>'
        candidates = pp.direction_candidates(pp.first_table(one_column))
        self.assertEqual([(c.row, c.col) for c in candidates], [(0, 0), (1, 0)])

    def test_unknown_or_legacy_response_fails_closed(self):
        for invalid in ('["乙", "乙"]', 'unknown', '[{"row": 0, "col": 0}]'):
            with self.subTest(reply=invalid):
                client = Replies(invalid, invalid)
                out, records = pp.fix_cell_direction(self.table, client, 128, render_bytes=self.image)
                self.assertEqual(out, self.table)
                self.assertTrue(all(row['outcome'] == 'undecided' for row in records))

    def test_wrong_locator_or_missing_visual_evidence_cannot_reverse(self):
        first = json.loads(choices(self.table, reversed_cells={(0, 0)}, swapped=False))
        second = json.loads(choices(self.table, reversed_cells={(0, 0)}, swapped=True))
        for key, value in [('row', 9), ('evidence', ''), ('col', False)]:
            with self.subTest(key=key):
                invalid = [dict(row) for row in first]
                invalid[0][key] = value
                client = Replies(json.dumps(invalid), json.dumps(second))
                out, records = pp.fix_cell_direction(self.table, client, 128, render_bytes=self.image)
                self.assertEqual(out, self.table)
                self.assertEqual(records[0]['outcome'], 'undecided')

    def test_duplicate_reply_locators_cannot_authorize_any_reversal(self):
        first = json.loads(choices(self.table, reversed_cells={(0, 0)}, swapped=False))
        second = json.loads(choices(self.table, reversed_cells={(0, 0)}, swapped=True))
        first[1] = dict(first[0])
        second[1] = dict(second[0])
        out, records = pp.fix_cell_direction(
            self.table, Replies(json.dumps(first), json.dumps(second)), 128, render_bytes=self.image)
        self.assertEqual(out, self.table)
        self.assertTrue(all(row['outcome'] == 'undecided' for row in records))

    def test_malformed_span_grid_is_preserved_pending_repair(self):
        source = '<table><tr><td rowspan="3">乙甲</td><td>丙丁</td></tr></table>'
        self.assertTrue(pp.first_table(source).problems)
        client = Replies()
        out, records = pp.fix_cell_direction(source, client, 128, render_bytes=self.image,
                                             table_orientation='as-printed')
        self.assertEqual(out, source)
        self.assertTrue(all(row['outcome'] == 'undecided' for row in records))
        out, how = pp.orient_table(source, self.image, client, 128)
        self.assertEqual(out, source)
        self.assertTrue(pp.orientation_undecided(how))
        self.assertEqual(client.calls, [])

    def test_visual_answer_fragments_and_duplicate_fields_are_rejected(self):
        one = choices(self.table, reversed_cells={(0, 0)}, swapped=False)
        two = choices(self.table, reversed_cells={(0, 0)}, swapped=True)
        for first, second in [
            ('uncertain\n' + one, 'uncertain\n' + two),
            (one.replace('"choice": "乙"', '"choice": "甲", "choice": "乙"'),
             two.replace('"choice": "甲"', '"choice": "乙", "choice": "甲"')),
        ]:
            out, records = pp.fix_cell_direction(self.table, Replies(first, second), 128,
                                                 render_bytes=self.image)
            self.assertEqual(out, self.table)
            self.assertTrue(all(record['outcome'] == 'undecided' for record in records))

    def test_disagreed_option_orders_do_not_reverse(self):
        answer = choices(self.table, reversed_cells={(0, 0)}, swapped=False)
        out, records = pp.fix_cell_direction(self.table, Replies(answer, answer), 128,
                                             render_bytes=self.image)
        self.assertEqual(out, self.table)
        self.assertTrue(all(record['outcome'] == 'undecided' for record in records))

    def test_as_printed_preserves_physical_dimensions_and_spans(self):
        source = ('<table><tr><td rowspan="2">乙甲</td><td>一</td></tr>'
                  '<tr><td>二</td></tr><tr><td colspan="2">丙丁</td></tr></table>')
        client = Replies()
        kept, how = pp.orient_table(source, self.image, client, 128, table_orientation='as-printed')
        self.assertEqual(kept, source)
        self.assertIn('policy=as-printed', how)
        self.assertEqual(client.calls, [])
        client = Replies(choices(source, reversed_cells={(0, 0)}, swapped=False),
                         choices(source, reversed_cells={(0, 0)}, swapped=True))
        out, records = pp.fix_cell_direction(source, client, 128, render_bytes=self.image,
                                             table_orientation='as-printed')
        before, after = pp.first_table(source), pp.first_table(out)
        self.assertEqual((before.rows, before.cols, before.layout()),
                         (after.rows, after.cols, after.layout()))
        self.assertEqual(after.grid()[0][0], '甲乙')
        self.assertTrue(all(record['tableOrientation'] == 'as-printed' for record in records))

    def test_auto_without_image_or_reviewer_is_undecided(self):
        for image, client in [(None, Replies()), (self.image, None)]:
            out, how = pp.orient_table(self.table, image, client, 128)
            self.assertEqual(out, self.table)
            self.assertTrue(pp.orientation_undecided(how))

    def test_totals_are_a_hint_not_authority_for_rotation(self):
        source = ('| 項名 | 甲年 | 乙年 |\n| --- | --- | --- |\n'
                  '| 甲項 | 一〇 | 二〇 |\n| 乙項 | 三〇 | 四〇 |\n| 合計 | 四〇 | 六〇 |')
        self.assertEqual(pp.totals_axis(source), 'column')
        client = Replies('RECORD: row\nEVIDENCE: 圖上每橫行列一筆紀錄，末行係合計')
        out, how = pp.orient_table(source, self.image, client, 128)
        self.assertEqual(out, source)
        self.assertFalse(pp.orientation_undecided(how))
        self.assertIn('唔證明紀錄方向', client.calls[0]['user'])

    def test_isolated_single_column_needs_visual_context(self):
        source = '<table><tr><td>乙甲</td></tr><tr><td>丙丁</td></tr></table>'
        no_image = Replies()
        out, how = pp.orient_table(source, None, no_image, 128)
        self.assertEqual(out, source)
        self.assertTrue(how.startswith(pp.SINGLE_LINE_KEPT))
        self.assertTrue(pp.orientation_undecided(how))
        self.assertEqual(no_image.calls, [])
        unknown = Replies('RECORD: unknown\nEVIDENCE: 只有孤立欄名，未見同表其餘格')
        out, how = pp.orient_table(source, self.image, unknown, 128)
        self.assertEqual(out, source)
        self.assertTrue(pp.orientation_undecided(how))
        clear = Replies('RECORD: column\nEVIDENCE: 圖上同表格線同欄名對應，呢列應為欄名一行')
        out, how = pp.orient_table(source, self.image, clear, 128,
                                   context='same-table continuation visible below the strip')
        self.assertEqual(pp.first_table(out).grid(), [['乙甲', '丙丁']])
        self.assertIn('same-table continuation', clear.calls[0]['user'])
        self.assertFalse(pp.orientation_undecided(how))

    def test_single_row_requires_visually_established_record_order(self):
        source = '<table><tr><td>甲乙</td><td>丙丁</td></tr></table>'
        client = Replies('RECORD: column\nEVIDENCE: 原圖每列係一筆紀錄',
                         'FIRST: right\nEVIDENCE: 原圖最右格係首筆紀錄')
        out, how = pp.orient_table(source, self.image, client, 128)
        self.assertEqual(pp.first_table(out).grid(), [['丙丁'], ['甲乙']])
        self.assertTrue(how.startswith('rotated'))
        self.assertTrue(all(call['image'] == self.image for call in client.calls))

    def test_unknown_order_does_not_default_to_rotation(self):
        client = Replies('RECORD: column\nEVIDENCE: 格線可見每列一筆紀錄',
                         'FIRST: unknown\nEVIDENCE: 圖像無法確認首筆')
        out, how = pp.orient_table(self.table, self.image, client, 128)
        self.assertEqual(out, self.table)
        self.assertTrue(pp.orientation_undecided(how))
        # A plausible OCR witness is still not source evidence.
        out, how = pp.turn_records(self.table, '乙甲二一丙丁')
        self.assertEqual(out, self.table)
        self.assertTrue(pp.orientation_undecided(how))

    def test_unknown_or_conflicting_axis_response_is_visible(self):
        for answer in ['RECORD: row', 'RECORD: column\nRECORD: row\nEVIDENCE: 不確定',
                       'RECORD: unknown\nEVIDENCE: 圖模糊', 'no idea']:
            with self.subTest(answer=answer):
                out, how = pp.orient_table(self.table, self.image, Replies(answer), 128)
                self.assertEqual(out, self.table)
                self.assertTrue(pp.orientation_undecided(how))

    def test_malformed_extra_directives_cannot_supply_a_partial_verdict(self):
        for answer in [
            'RECORD: column\nRECORD: row (uncertain)\nEVIDENCE: 格線可見',
            'RECORD: row\nEVIDENCE: 格線可見\nRECORD: maybe column',
            'RECORD: column (maybe)\nEVIDENCE: 格線可見',
            'RECORD: row\nEVIDENCE: 格線可見\n額外解釋',
        ]:
            with self.subTest(answer=answer):
                out, how = pp.orient_table(self.table, self.image, Replies(answer), 128)
                self.assertEqual(out, self.table)
                self.assertTrue(pp.orientation_undecided(how))
        for answer in [
            'FIRST: left\nFIRST: right (maybe)\nEVIDENCE: 圖上次序',
            'FIRST: right\nEVIDENCE: 圖上次序\nFIRST: maybe left',
            'FIRST: left (maybe)\nEVIDENCE: 圖上次序',
        ]:
            with self.subTest(answer=answer):
                first, _ = pp.ask_first_record(self.table, self.image, Replies(answer), 128)
                self.assertIsNone(first)

    def test_unknown_first_record_without_image_never_asks(self):
        client = Replies()
        first, reason = pp.ask_first_record(self.table, None, client, 128)
        self.assertIsNone(first)
        self.assertIn('no source image', reason)
        self.assertEqual(client.calls, [])

    def test_orientation_evidence_seals_raw_grid_and_selected_policy(self):
        source = '<table><tr><td>乙甲</td></tr><tr><td>丙丁</td></tr></table>'
        client = Replies('RECORD: column\nEVIDENCE: 原圖欄名對應確認')
        out, how = pp.orient_table(source, self.image, client, 128)
        record = pp.table_orientation_record(source, out, how, self.image, 'auto', block=3)
        self.assertEqual(record['inputText'], source)
        self.assertEqual(record['inputShape'], [2, 1])
        self.assertEqual(record['outputShape'], [1, 2])
        self.assertEqual(record['policy'], 'auto')
        self.assertEqual(record['sourceImageSha256'], pp.sha256_bytes(self.image))
        self.assertEqual(record['outcome'], 'resolved')

    def test_recover_without_image_keeps_unresolved_direction_in_report(self):
        source = '<table><tr><td>乙甲</td><td>一</td></tr><tr><td>二</td><td>丙丁</td></tr></table>'
        recovered, refused, orientations, _, sealed = pp.recover_tables(
            [{'label': 'table', 'text': source, 'box': [0, 0, 1, 1]}], '乙甲一二丙丁')
        self.assertEqual(refused, [])
        self.assertEqual(len(recovered), 1)
        self.assertTrue(pp.orientation_undecided(orientations[0]))
        self.assertEqual({row['kind'] for row in sealed['doubts']},
                         {'table-orientation-undecided', 'cell-direction-undecided'})
        self.assertEqual(sealed['tableOrientationEvidence'][0]['outcome'], 'undecided')
        self.assertTrue(all(record['outcome'] == 'undecided' for record in sealed['cellDirection']))


if __name__ == '__main__':
    unittest.main()
