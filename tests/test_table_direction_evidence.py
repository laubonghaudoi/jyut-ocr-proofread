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

    def test_isolated_single_line_is_kept_undecided_without_a_question(self):
        # Decision 8 (benchmark table rule): a single line of cells does not
        # show which way it lies, so it is kept and sealed undecided and the
        # model is never asked, whatever the image or context (the old
        # skill's measured guard; the stitch turns a line of labels to the
        # table it heads).
        for source in ('<table><tr><td>乙甲</td></tr><tr><td>丙丁</td></tr></table>',
                       '<table><tr><td>甲乙</td><td>丙丁</td></tr></table>'):
            for image in (None, self.image):
                with self.subTest(source=source, image=bool(image)):
                    client = Replies('RECORD: column\nEVIDENCE: 圖上同表格線同欄名對應，呢列應為欄名一行',
                                     'FIRST: right\nEVIDENCE: 原圖最右格係首筆紀錄')
                    out, how = pp.orient_table(source, image, client, 128,
                                               context='same-table continuation visible below the strip')
                    self.assertEqual(out, source)
                    self.assertTrue(how.startswith(pp.SINGLE_LINE_KEPT))
                    self.assertTrue(pp.orientation_undecided(how))
                    self.assertEqual(client.calls, [])

    def test_two_axis_grid_without_evidence_requires_visually_established_record_order(self):
        source = '<table><tr><td>甲乙</td><td>丙丁</td></tr><tr><td>一</td><td>二</td></tr></table>'
        client = Replies('RECORD: column\nEVIDENCE: 原圖每列係一筆紀錄',
                         'FIRST: right\nEVIDENCE: 原圖最右格係首筆紀錄')
        out, how = pp.orient_table(source, self.image, client, 128)
        self.assertEqual(pp.first_table(out).grid(), [['丙丁', '二'], ['甲乙', '一']])
        self.assertTrue(how.startswith('rotated'))
        self.assertTrue(all(call['image'] == self.image for call in client.calls))

    def test_unknown_order_does_not_default_to_rotation(self):
        client = Replies('RECORD: column\nEVIDENCE: 格線可見每列一筆紀錄',
                         'FIRST: unknown\nEVIDENCE: 圖像無法確認首筆')
        out, how = pp.orient_table(self.table, self.image, client, 128)
        self.assertEqual(out, self.table)
        self.assertTrue(pp.orientation_undecided(how))
        # Decision 8: the benchmark's table is read in the page's reading
        # order, which engine B's text follows, so its order places the
        # columns (the old skill's measured vote); with no vote and no answer
        # nothing is turned.
        out, how = pp.turn_records(self.table, '乙甲二一丙丁')
        self.assertEqual(pp.first_table(out).grid(), [['乙甲', '二'], ['一', '丙丁']])
        self.assertTrue(how.startswith('transposed'))
        out, how = pp.turn_records(self.table, '', ask=lambda grid: (None, 'unknown'))
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
        source = ('<table><tr><td>乙甲</td><td>一</td><td>三</td></tr>'
                  '<tr><td>丙丁</td><td>二</td><td>四</td></tr></table>')
        client = Replies('RECORD: column\nEVIDENCE: 原圖欄名對應確認',
                         'FIRST: left\nEVIDENCE: 原圖最左格係首筆紀錄')
        out, how = pp.orient_table(source, self.image, client, 128)
        record = pp.table_orientation_record(source, out, how, self.image, 'auto', block=3)
        self.assertEqual(record['inputText'], source)
        self.assertEqual(record['inputShape'], [2, 3])
        self.assertEqual(record['outputShape'], [3, 2])
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


def grid_html(rows):
    return '<table>' + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows) + '</table>'


def grid_markdown(rows):
    lines = ['| ' + ' | '.join(row) + ' |' for row in rows]
    return '\n'.join([lines[0], '| ' + ' | '.join('---' for _ in rows[0]) + ' |', *lines[1:]])


class BenchmarkTableRuleTests(unittest.TestCase):
    """Decision 8: the benchmark's table rule.  A horizontal table keeps its
    rows and columns; a vertical one's printed columns, right to left, become
    rows top to bottom, merged cells turned with them; the table read row by
    row is the page's reading order.  Made-up text."""

    # Printed right to left: the label column, then two records.
    WANTED = [['物名', '件數', '價值'], ['黑板', '一', '二元'], ['講臺', '一', '三元']]
    PHYSICAL = [['講臺', '黑板', '物名'], ['一', '一', '件數'], ['三元', '二元', '價值']]
    FROM_LEFT = [['講臺', '一', '三元'], ['黑板', '一', '二元'], ['物名', '件數', '價值']]
    READING = '物名件數價值\n黑板一二元\n講臺一三元'

    def setUp(self):
        self.image = image_fixture()

    def test_page_print_direction_from_engine_layout(self):
        size = (1000, 1400)
        vertical = [{'label': 'vertical_text', 'text': '某某某某某某某某', 'box': [0.5, 0.1, 0.3, 0.3]}]
        self.assertEqual(pp.page_print_direction(vertical, size), 'vertical')
        heading = [{'label': 'text', 'text': '某處學堂器具表', 'box': [0.8, 0.1, 0.05, 0.5]}]
        self.assertEqual(pp.page_print_direction(heading, size), 'vertical')
        prose = [{'label': 'text', 'text': '某某某某某某某某某某', 'box': [0.1, 0.1, 0.8, 0.2]}]
        self.assertEqual(pp.page_print_direction(prose, size), 'horizontal')
        table_only = [{'label': 'table', 'text': grid_html(self.PHYSICAL), 'box': [0.1, 0.1, 0.8, 0.8]},
                      {'label': 'number', 'text': '二四', 'box': [0.9, 0.9, 0.02, 0.02]}]
        self.assertIsNone(pp.page_print_direction(table_only, size))
        self.assertIsNone(pp.page_print_direction(vertical + prose, size))

    def test_physical_grid_follows_the_print_direction_without_a_question(self):
        physical = ('<table><tr><td colspan="3">價目表</td></tr>'
                    '<tr><td rowspan="3">另加郵費</td><td>一元</td><td>全年</td></tr>'
                    '<tr><td>六角</td><td>半年</td></tr><tr><td>五分</td><td>一册</td></tr></table>')
        client = Replies()
        out, how = pp.orient_table(physical, self.image, client, 128, print_direction='vertical', physical=True)
        table = pp.first_table(out)
        self.assertEqual(table.grid(), [['價目表', '全年', '半年', '一册'], ['', '一元', '六角', '五分'],
                                        ['', '另加郵費', '', '']])
        self.assertEqual([(c.row, c.col, c.rowspan, c.colspan) for c in table.cells if c.text == '價目表'],
                         [(0, 0, 3, 1)])
        self.assertEqual([(c.row, c.col, c.rowspan, c.colspan) for c in table.cells if c.text == '另加郵費'],
                         [(2, 1, 1, 3)])
        self.assertTrue(how.startswith('rotated') and not pp.orientation_undecided(how))
        out, how = pp.orient_table(physical, self.image, client, 128, print_direction='horizontal', physical=True)
        self.assertEqual(out, physical)
        self.assertTrue(how.startswith('kept') and not pp.orientation_undecided(how))
        self.assertEqual(client.calls, [])

    def test_rebuilt_grid_already_in_reading_order_is_not_turned_again(self):
        # Bench case caangung-4/s0002: the rebuild answered with the printed
        # columns as rows (the label across the top kept on top), and the
        # mechanical turn put the three rows in reverse.  Engine B reads the
        # columns right to left, each from the top, the label's characters
        # between them.
        reading_grid = ('<table><tr><td colspan="3">價目表</td></tr>'
                        '<tr><td>全年念四期</td><td>半年十二册</td><td>一册</td></tr>'
                        '<tr><td>一元二角</td><td>六角</td><td>五分</td></tr>'
                        '<tr><td colspan="3">另加郵費每册一分</td></tr></table>')
        witness = '代售處各書坊\n全年念四期半年十二册一册價一元二角六角五分目另加郵費表每册一分'
        client = Replies()
        out, how = pp.orient_table(reading_grid, self.image, client, 128, witness=witness,
                                   print_direction='vertical', physical=True)
        self.assertEqual(out, reading_grid)
        self.assertTrue(how.startswith('kept') and not pp.orientation_undecided(how), how)
        self.assertEqual(client.calls, [])
        # The physical grid of the same table, with the same reading, is
        # turned as before, and so is one engine B does not settle.
        physical = ('<table><tr><td colspan="3">價目表</td></tr>'
                    '<tr><td rowspan="3">另加郵費每册一分</td><td>一元二角</td><td>全年念四期</td></tr>'
                    '<tr><td>六角</td><td>半年十二册</td></tr><tr><td>五分</td><td>一册</td></tr></table>')
        out, how = pp.orient_table(physical, self.image, client, 128, witness=witness,
                                   print_direction='vertical', physical=True)
        self.assertEqual(pp.first_table(out).grid()[0], ['價目表', '全年念四期', '半年十二册', '一册'])
        self.assertTrue(how.startswith('rotated'), how)
        out, how = pp.orient_table(reading_grid, self.image, client, 128, witness=None,
                                   print_direction='vertical', physical=True)
        self.assertTrue(how.startswith('rotated — vertical print'), how)
        self.assertEqual(client.calls, [])

    def test_engine_grid_is_turned_to_engine_b_reading_order(self):
        for rows, expected in ((self.PHYSICAL, 'rotated'), (self.WANTED, 'kept'), (self.FROM_LEFT, 'flipped')):
            with self.subTest(expected=expected):
                client = Replies()
                out, how = pp.orient_table(grid_html(rows), self.image, client, 128, witness=self.READING)
                self.assertEqual(pp.first_table(out).grid(), self.WANTED)
                self.assertTrue(how.startswith(expected), how)
                self.assertEqual(client.calls, [])

    def test_engine_b_table_is_a_grid_not_a_reading(self):
        votes = pp.reading_order_votes(pp.first_table(grid_html(self.PHYSICAL)), grid_html(self.WANTED))
        self.assertEqual(set(votes.values()), {(0, 0)})

    def test_vertical_page_totals_and_horizontal_page_without_a_question(self):
        figures = ('| 項目 | 甲年 | 乙年 |\n| --- | --- | --- |\n'
                   '| 甲項 | 一〇 | 二〇 |\n| 乙項 | 三〇 | 四〇 |\n| 合計 | 四〇 | 六〇 |')
        client = Replies()
        out, how = pp.orient_table(figures, self.image, client, 128, print_direction='vertical')
        self.assertTrue(how.startswith('rotated — 合計'), how)
        out, how = pp.orient_table(figures, self.image, client, 128, print_direction='horizontal')
        self.assertEqual(out, figures)
        self.assertTrue(how.startswith('kept') and not pp.orientation_undecided(how))
        self.assertEqual(client.calls, [])

    def test_a_table_the_structure_pass_wrote_is_turned_to_engine_b_reading_order(self):
        # Bench case hinzing-6/s0071: the engine grid was refused, and the
        # structure pass wrote the vertical table as printed, each printed
        # line across the page a row and the printed columns, right to left,
        # its columns (AS_PRINTED); nothing turned it.  The physical grid,
        # its columns left to right, and the reading-order grid read from
        # the left come out turned too, and the reading-order grid is kept.
        as_printed = [list(column) for column in zip(*self.WANTED)]
        for rows, expected in ((as_printed, 'transposed'), (self.PHYSICAL, 'rotated'),
                               (self.FROM_LEFT, 'flipped'), (self.WANTED, 'kept')):
            with self.subTest(expected=expected):
                written = grid_markdown(rows)
                before, after = '右表所列各件\n\n', '\n\n均係本年添置'
                out, notes = pp.orient_written_tables(before + written + after, [], self.READING, None)
                self.assertEqual(pp.first_table(out).grid(), self.WANTED)
                self.assertTrue(out.startswith(before) and out.endswith(after), out)
                self.assertEqual([(was, now, how.split(' ')[0]) for was, now, how in notes],
                                 [(written, out[len(before):-len(after)], expected)])
                self.assertFalse(pp.orientation_undecided(notes[0][2]), notes[0][2])
        written = grid_markdown(as_printed)
        text = f'右表所列各件\n\n{written}'
        # A horizontally printed page keeps its rows and columns; the
        # as-printed policy keeps the physical grid; a recovered table (an
        # engine grid or a rebuild, oriented before the pass) is not looked
        # at again; nor is a single line of cells.
        self.assertEqual(pp.orient_written_tables(text, [], self.READING, 'horizontal'), (text, []))
        self.assertEqual(pp.orient_written_tables(text, [], self.READING, 'vertical', 'as-printed'), (text, []))
        self.assertEqual(pp.orient_written_tables(text, [grid_html(as_printed)], self.READING, 'vertical'),
                         (text, []))
        line = '右表\n\n| 物名 | 黑板 | 講臺 |\n| --- | --- | --- |'
        self.assertEqual(pp.orient_written_tables(line, [], self.READING, 'vertical'), (line, []))
        # When engine B settles nothing (it wrote a grid, or did not read the
        # table) the answer's table stands, undecided, and nothing is asked.
        for witness in (grid_html(self.WANTED), '另頁別文', None):
            with self.subTest(witness=witness):
                out, notes = pp.orient_written_tables(text, [], witness, 'vertical')
                self.assertEqual(out, text)
                self.assertEqual(len(notes), 1)
                self.assertTrue(pp.orientation_undecided(notes[0][2]), notes[0][2])
        # Bench case hinzing-6/s0061: a ruled table engine B read band by
        # band, one cell to a line, so its order runs field by field.  The
        # order alone would turn a table written right; its lines hold no
        # two cells, so nothing settles and the table stands.
        bands = '物名\n黑板\n講臺\n件數\n一\n一\n價值\n二元\n三元'
        right = '右表所列各件\n\n' + grid_markdown(self.WANTED)
        self.assertEqual(pp.reading_order_turn(pp.first_table(right), bands, tuple(pp.WRITTEN_TABLE_TURNS))[0],
                         'transposed')
        self.assertEqual(pp.witness_line_axis(pp.first_table(right), bands)[0], None)
        out, notes = pp.orient_written_tables(right, [], bands, 'vertical')
        self.assertEqual(out, right)
        self.assertTrue(pp.orientation_undecided(notes[0][2]), notes[0][2])
        # Nor where the lines settle the axis and the order no turn: the
        # printed columns, one to a line, read in no one order.
        scrambled = '講臺一三元\n物名件數價值\n黑板一二元'
        printed = '右表所列各件\n\n' + grid_markdown(as_printed)
        self.assertEqual(pp.witness_line_axis(pp.first_table(printed), scrambled)[0], 'column')
        self.assertEqual(pp.reading_order_turn(pp.first_table(printed), scrambled,
                                               tuple(pp.WRITTEN_TABLE_TURNS))[0], None)
        out, notes = pp.orient_written_tables(printed, [], scrambled, 'vertical')
        self.assertEqual(out, printed)
        self.assertTrue(pp.orientation_undecided(notes[0][2]), notes[0][2])

    # Printed right to left: each record a printed column, its total at its
    # foot (合計 reconciles along the printed line); written the benchmark's
    # way, each record a row.
    TOTALLED = [['項目', '甲款', '乙款', '合計'], ['某甲', '一一', '二二', '三三'],
                ['某乙', '四四', '五五', '九九'], ['某丙', '一二', '二四', '三六']]

    def test_a_written_table_turn_is_not_decided_by_its_total(self):
        # FX4 review N1: a 合計 says which axis the records lie on, not which
        # axis was printed as a line - a total at each printed column's foot
        # and a total printed as a column of its own reconcile along
        # different axes - so it can neither refuse nor vouch for a turn.
        # Engine B's reading alone decides, the note says so, and the page
        # raises a table-orientation-turned doubt for every turn.
        right = '右表所列各件\n\n' + grid_markdown(self.TOTALLED)
        printed = '右表所列各件\n\n' + grid_markdown([list(column) for column in zip(*self.TOTALLED)])
        columns = '\n'.join(''.join(row) for row in self.TOTALLED)
        # Written as printed, read one printed column a line: turned to rows,
        # whichever axis its 合計 reconciles along.
        for direction in ('vertical', None):
            with self.subTest(direction=direction):
                out, notes = pp.orient_written_tables(printed, [], columns, direction)
                self.assertEqual(out, right)
                self.assertTrue(notes[0][2].startswith('transposed — '), notes[0][2])
                self.assertTrue(notes[0][2].endswith('; ' + pp.WRITTEN_TURN_UNCHECKED), notes[0][2])
                self.assertNotIn('合計', notes[0][2])
        # The same when the 合計 is printed as a column of its own, the last
        # printed column summing the ones before it: written as printed, its
        # 合計 reconciles along the written rows, the axis the transpose does
        # not take as printed lines, so a 合計 check would have refused this
        # right turn.  It is taken.
        own_column = [['項目', '一款', '二款'], ['甲項', '一二', '二一'], ['乙項', '一〇', '三〇'],
                      ['合計', '二二', '五一']]
        written = '\n\n' + grid_markdown([list(column) for column in zip(*own_column)])
        self.assertNotEqual(pp.totals_axis(written.strip()), pp.TURN_AXIS['transposed'])
        out, notes = pp.orient_written_tables(written, [], '\n'.join(''.join(row) for row in own_column), 'vertical')
        self.assertEqual(pp.first_table(out).grid(), own_column)
        self.assertTrue(notes[0][2].startswith('transposed — '), notes[0][2])
        self.assertTrue(notes[0][2].endswith('; ' + pp.WRITTEN_TURN_UNCHECKED), notes[0][2])
        # A table written right that engine B read one printed band to a
        # line, several cells a line, counts the same and is turned too:
        # nothing on the page tells the two apart, which is why every turn
        # is a doubt for a person to check against the image.
        bands = '\n'.join(' '.join(row[c] for row in self.TOTALLED) for c in range(4))
        table = pp.first_table(right)
        self.assertEqual(pp.reading_order_turn(table, bands, tuple(pp.WRITTEN_TABLE_TURNS))[0], 'transposed')
        self.assertEqual(pp.witness_line_axis(table, bands)[0], 'column')
        out, notes = pp.orient_written_tables(right, [], bands, 'vertical')
        self.assertEqual(pp.first_table(out).grid(), [list(column) for column in zip(*self.TOTALLED)])
        self.assertTrue(notes[0][2].endswith('; ' + pp.WRITTEN_TURN_UNCHECKED), notes[0][2])

    def test_recover_tables_turns_by_page_evidence(self):
        blocks = [{'label': 'vertical_text', 'text': '右表所列各件均係本年添置', 'box': [0.85, 0.1, 0.05, 0.8]},
                  {'label': 'table', 'text': grid_html(self.PHYSICAL), 'box': [0.1, 0.1, 0.7, 0.8]}]
        merged = '右表所列各件均係本年添置' + ''.join(''.join(r) for r in self.PHYSICAL)
        client = Replies('[]', '[]')   # the label-cell question only, answered unreadably
        tables, refused, orientations, _, sealed = pp.recover_tables(
            blocks, merged, self.image, client, 128, witness='右表所列各件均係本年添置\n' + self.READING)
        self.assertEqual(refused, [])
        self.assertEqual(pp.first_table(tables[0]).grid(), self.WANTED)
        self.assertTrue(orientations[0].startswith('block 1: rotated'), orientations)
        self.assertNotIn('table-orientation-undecided', [d['kind'] for d in sealed['doubts']])
        self.assertNotIn('orient', [call['kind'] for call in client.calls])


if __name__ == '__main__':
    unittest.main()
