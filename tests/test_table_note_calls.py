"""A note call an engine wrapped in LaTeX inside a table cell is written bare.

Engine A writes a superscript note call in a cell as LaTeX - `$ ^{⑲} $`,
`$ ^{{①⑨}} $`, `$ ^{203} $` - and recover_tables takes its grid's cells as
read, so the wrapper reached the page and the delivered book.  These tests use
made-up grids in that shape (no model, no image): the call is written as the
bare glyphs it holds, Arabic figures as superscript figures; a wrapper that is
a formula, or holds no note number, is left as it is.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _note_markers  # noqa: E402
import proofread_pages as pp  # noqa: E402


def html(rows: list[list[str]]) -> str:
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"


ROWS = [["西元", "時間", "事由", "歲收"],
        ["一五一一", "甲年", "設立鹽廠。", "一〇六七兩"],
        ["一五一七", "乙年", "暫行廣鹽。", "六三九五兩"],
        ["一五二六", "丙年", "復行廣鹽。", "二四〇〇兩"],
        ["一五八六", "丁年", "仍行廣鹽。", "二〇〇〇兩"]]
# Engine A's reading: each figure cell with its note call in LaTeX.
CALLS = {1: " $ ^{⑲} $", 2: " $ ^{{①⑨}} $", 3: " $ \\textcircled{9} $", 4: " $ ^{203} $"}


def page(rows: list[list[str]], calls: dict[int, str]) -> tuple[list[dict], str]:
    grid = [row[:3] + [row[3] + calls.get(r, "")] for r, row in enumerate(rows)]
    blocks = [{"text": html(grid), "label": "table", "box": [0, 0, 1, 1]}]
    merged = "".join(pp.CJK(cell) for row in rows for cell in row)
    return blocks, merged


class CellNoteCallTests(unittest.TestCase):
    def test_latex_note_calls_in_cells_are_written_bare(self) -> None:
        blocks, merged = page(ROWS, CALLS)
        tables, rejected, _, _, sealed = pp.recover_tables(blocks, merged, witness=None)
        self.assertEqual(rejected, [])
        grid = pp.first_table(tables[0]).grid()
        self.assertEqual([row[3] for row in grid[1:]],
                         ["一〇六七兩⑲", "六三九五兩①⑨", "二四〇〇兩⑨", "二〇〇〇兩²⁰³"])
        self.assertNotIn("$", tables[0])
        # Each bare call is the same note number to the footnote step as the
        # wrapper was.
        self.assertEqual([_note_markers.marker_key(row[3][5:]) for row in grid[1:]],
                         ["19", "19", "9", "203"])
        # The doubt still names engine A's reading and says what the page holds.
        doubt = next(d for d in sealed["doubts"] if d["kind"] == "table-note-marker-unverified")
        self.assertIn("$^{⑲}$", doubt["detail"])
        self.assertTrue(doubt["detail"].endswith("; written bare on the page: ⑲、①⑨、⑨、²⁰³"), doubt["detail"])

    def test_a_textcircled_call_alone_is_written_bare_with_a_doubt(self) -> None:
        # FX3 review F5 (probe p13_textcircled.py): a cell whose only call is
        # engine A's \\textcircled was written bare with no doubt, the one
        # that says a call in a cell is engine A's reading, unchecked.
        blocks, merged = page(ROWS, {3: " $ \\textcircled{9} $"})
        tables, _, _, _, sealed = pp.recover_tables(blocks, merged, witness=None)
        self.assertEqual(pp.first_table(tables[0]).grid()[3][3], "二四〇〇兩⑨")
        doubts = [d for d in sealed["doubts"] if d["kind"] == "table-note-marker-unverified"]
        self.assertEqual(len(doubts), 1, sealed["doubts"])
        self.assertIn("1 note marker(s)", doubts[0]["detail"])
        self.assertIn("textcircled{9}", doubts[0]["detail"])
        self.assertTrue(doubts[0]["detail"].endswith("; written bare on the page: ⑨"), doubts[0]["detail"])
        # A call written bare that no marker form matched still has the doubt.
        with mock.patch.object(pp, "CELL_NOTE_MARKER", re.compile("(?!)")):
            tables, _, _, _, sealed = pp.recover_tables(*page(ROWS, {1: " $ ^{⑲} $"}), witness=None)
        doubts = [d for d in sealed["doubts"] if d["kind"] == "table-note-marker-unverified"]
        self.assertEqual(len(doubts), 1, sealed["doubts"])
        self.assertTrue(doubts[0]["detail"].endswith("; written bare on the page: ⑲"), doubts[0]["detail"])

    def test_the_orientation_record_binds_the_table_written_bare(self) -> None:
        # FX3 review F4: the calls were written bare after the orientation
        # record was taken, so its output matched no table of the page and
        # the seal called it historical.  The record now holds the table as
        # written, and what was written bare.
        blocks, merged = page(ROWS, CALLS)
        tables, _, _, _, sealed = pp.recover_tables(blocks, merged, witness=None)
        record = sealed["tableOrientationEvidence"][-1]
        self.assertEqual(record["outputText"], tables[0])
        self.assertEqual(record["outputSha256"], pp.sha256_bytes(tables[0].encode("utf-8")))
        self.assertEqual([call for _, call in record["noteCallsBare"]], ["⑲", "①⑨", "⑨", "²⁰³"])
        self.assertIn("$", record["inputText"])
        # A table with no call to write bare keeps its record as it was.
        blocks, merged = page(ROWS, {})
        tables, _, _, _, sealed = pp.recover_tables(blocks, merged, witness=None)
        self.assertNotIn("noteCallsBare", sealed["tableOrientationEvidence"][-1])
        self.assertEqual(sealed["tableOrientationEvidence"][-1]["outputText"], tables[0])

    def test_a_formula_or_a_wrapper_holding_no_note_number_stays(self) -> None:
        cells = ["二〇〇〇兩", "m$^{2}$", "10$^{6}$兩", "一〇$^{2}$+一", "兩$^{(1)}$", "兩$ 12 $"]
        table = "| 甲 | 乙 |\n| --- | --- |\n" + "".join(f"| 丙丁 | {cell} |\n" for cell in cells)
        out, written = pp.bare_cell_note_calls(table)
        self.assertEqual(written, [])
        self.assertEqual(out, table)

    def test_a_circled_call_after_a_figure_is_written_bare_but_a_figure_is_not(self) -> None:
        table = ("| 甲 | 乙 |\n| --- | --- |\n| 一九九〇$^{①}$ | 1990$^{①}$ |\n"
                 "| 1990$^{2}$ | 兩$^{2}$ |")
        out, written = pp.bare_cell_note_calls(table)
        grid = pp.first_table(out).grid()
        self.assertEqual(grid[1], ["一九九〇①", "1990①"])
        # An Arabic figure after a figure may be an exponent: left as LaTeX.
        self.assertEqual(grid[2], ["1990$^{2}$", "兩²"])
        self.assertEqual(written, [("$^{①}$", "①"), ("$^{①}$", "①"), ("$^{2}$", "²")])

    def test_a_table_without_latex_is_returned_unchanged(self) -> None:
        table = html([["甲", "乙"], ["丙⑲", "丁"], ["戊", "己"]])
        self.assertEqual(pp.bare_cell_note_calls(table), (table, []))


if __name__ == "__main__":
    unittest.main()
