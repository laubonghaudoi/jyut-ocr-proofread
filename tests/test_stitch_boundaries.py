"""Synthetic boundary cases: no benchmark answers or real scan fixtures."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _stitch as stitch
import proofread_pages as pp
import stitch_tables


def table(rows: list[tuple[str, str]], header: tuple[str, str] = ("項目", "數額")) -> str:
    return "\n".join([f"| {header[0]} | {header[1]} |", "| --- | --- |"]
                     + [f"| {a} | {b} |" for a, b in rows]) + "\n"


def page(number: int, text: str, **record: object) -> stitch.PageInput:
    return stitch.PageInput(number, text, record)


class StitchBoundaryTests(unittest.TestCase):
    def run_pair(self, a: str, b: str, number: int = 2) -> dict:
        return stitch.plan([page(1, a), page(number, b)], geometry=False)

    def assert_blocked(self, result: dict, a: str, b: str, number: int = 2) -> None:
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["decisions"][0]["verdict"], "blocked")
        for n, text in ((1, a), (number, b)):
            self.assertEqual(result["pages"][n]["text"], text)
            self.assertTrue(any(d["kind"] == stitch.BLOCKED for d in result["pages"][n]["doubts"]))

    def test_note_apparatus_caption_and_exact_header_keep_all_content(self) -> None:
        a = "## 分項表\n\n" + table([("甲①", "100")]) + "\n---\n\n① 前頁注文。\n"
        b = "## 分項表（續）\n\n" + table([("乙②", "200")]) + "\n---\n\n② 次頁注文。\n    同一註續行。\n"
        result = self.run_pair(a, b)
        self.assertEqual(len(result["chains"]), 1)
        head = result["pages"][1]["text"]
        tail = result["pages"][2]["text"]
        self.assertIn("乙②", head)
        self.assertEqual(head.count("| 項目 | 數額 |"), 1)
        for text in ("① 前頁注文。", "② 次頁注文。", "    同一註續行。"):
            self.assertEqual(head.count(text), 1)
            self.assertNotIn(text, tail)
        self.assertEqual(tail.strip(), "## 分項表（續）")
        self.assertEqual(result["pages"][2]["stitch"]["continuation"]["retainedCaptions"], ["## 分項表（續）"])
        self.assertEqual(result["pages"][1]["stitch"]["head"]["movedNotes"][0]["text"], "---")

    def test_markdown_notes_move_with_their_unique_calls(self) -> None:
        a = table([("甲", "10")])
        b = table([("乙[^b]", "20")]) + "\n[^b]: 來源附註。\n"
        result = self.run_pair(a, b)
        self.assertEqual(len(result["chains"]), 1)
        self.assertIn("[^b]: 來源附註。", result["pages"][1]["text"])
        self.assertEqual(result["pages"][2]["text"], "")

    def test_unknown_body_after_table_is_actionable(self) -> None:
        a = table([("甲", "10")]) + "\n下列正文不可略過。\n"
        b = table([("乙", "20")])
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_unknown_body_before_table_is_actionable(self) -> None:
        a = table([("甲", "10")])
        b = "此段另起新節。\n\n" + table([("乙", "20")])
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_different_named_title_is_not_a_continuation(self) -> None:
        a = "## 甲表\n\n" + table([("甲", "10")])
        for title in ("## 乙表", "## 乙表（續）"):
            b = title + "\n\n" + table([("乙", "20")])
            with self.subTest(title=title):
                self.assert_blocked(self.run_pair(a, b), a, b)

    def test_missing_pages_stop_and_report_the_chain(self) -> None:
        a, b = table([("甲", "10")]), table([("乙", "20")])
        self.assert_blocked(self.run_pair(a, b, 3), a, b, 3)

    def test_furniture_is_traversed_but_not_deleted(self) -> None:
        a = table([("甲", "10")]) + "\n頁眉\n"
        b = "頁眉\n\n" + table([("乙", "20")])
        pages = [page(1, a), page(2, b)]
        for p in pages:
            p.is_furniture = lambda text, edge=None: text == "頁眉" or not stitch.printed(text)
        result = stitch.plan(pages, geometry=False)
        self.assertEqual(len(result["chains"]), 1)
        self.assertEqual(sum(p["text"].count("頁眉") for p in result["pages"].values()), 2)

    def test_same_column_count_alone_is_undecided(self) -> None:
        a = table([("甲", "10")])
        b = table([("丙", "30")], ("乙", "20"))
        result = self.run_pair(a, b)
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["decisions"][0]["verdict"], "undecided")
        self.assertEqual(result["pages"][1]["text"], a)
        self.assertEqual(result["pages"][2]["text"], b)

    def test_named_continuation_supplies_identity_without_repeated_header(self) -> None:
        a = "## 分項表\n\n" + table([("甲", "10")])
        b = "## 分項表（續）\n\n" + table([("丙", "30")], ("乙", "20"))
        result = self.run_pair(a, b)
        self.assertEqual(len(result["chains"]), 1)
        self.assertIn("| 乙 | 20 |", result["pages"][1]["text"])

    def test_bare_continuation_does_not_make_equal_width_sufficient(self) -> None:
        a = table([("甲", "10")])
        b = "（續）\n\n" + table([("丙", "30")], ("乙", "20"))
        result = self.run_pair(a, b)
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["decisions"][0]["verdict"], "undecided")

    def test_anagram_header_and_swapped_columns_are_never_deduplicated(self) -> None:
        a = table([("甲", "10")])
        for header in (("目項", "額數"), ("數額", "項目")):
            b = table([("乙", "20")], header)
            with self.subTest(header=header):
                result = self.run_pair(a, b)
                self.assertEqual(result["chains"], [])
                self.assertEqual(result["pages"][2]["text"], b)

    def test_repeated_text_record_is_not_assumed_to_be_a_header(self) -> None:
        a = table([("東", "西")], ("甲", "乙"))
        b = table([("南", "北")], ("甲", "乙"))
        result = self.run_pair(a, b)
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["decisions"][0]["verdict"], "undecided")

    def test_per_page_reused_note_marker_blocks_relocation(self) -> None:
        a = table([("甲①", "10")]) + "\n① 第一頁附註。\n"
        b = table([("乙①", "20")]) + "\n① 第二頁附註。\n"
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_shared_note_call_blocks_relocation(self) -> None:
        a = table([("甲", "10")])
        b = table([("乙①", "20")]) + "\n另一段亦引用①。\n\n① 共同附註。\n"
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_unlabelled_note_continuation_is_not_treated_as_furniture(self) -> None:
        a = table([("甲①", "10")]) + "\n① 注文。\n未縮排續行或正文。\n"
        b = table([("乙", "20")])
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_unlabelled_continuation_note_body_blocks_the_whole_move(self) -> None:
        a = table([("甲", "10")])
        b = table([("乙①", "20")]) + "\n① 注文第一行。\n這是同註第二行。\n"
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_indented_note_punctuation_moves_but_unscoped_punctuation_blocks(self) -> None:
        a = table([("甲", "10")])
        b = table([("乙①", "20")]) + "\n① 注文。\n    ……\n"
        result = self.run_pair(a, b)
        self.assertEqual(len(result["chains"]), 1)
        self.assertIn("    ……", result["pages"][1]["text"])
        self.assertEqual(result["pages"][2]["text"], "")
        b = table([("乙①", "20")]) + "\n① 注文。\n……\n"
        self.assert_blocked(self.run_pair(a, b), a, b)

    def test_as_printed_keeps_n_by_one_head_unrotated(self) -> None:
        a = "| 項目 |\n| --- |\n| 數額 |\n"
        b = table([("甲", "10")])
        inputs = [page(1, a, tables=[{"block": 0, "text": a}],
                       tableOrientation=["block 0: " + pp.SINGLE_LINE_KEPT],
                       provenance={"tableOrientation": "as-printed"}),
                  page(2, b, provenance={"tableOrientation": "as-printed"})]
        result = stitch.plan(inputs, geometry=False)
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["pages"][1]["text"], a)
        self.assertEqual(result["pages"][2]["text"], b)

    def test_auto_unknown_orientation_is_not_interpreted_as_kept(self) -> None:
        for how in ("kept, UNDECIDED — no source image", pp.SINGLE_LINE_KEPT,
                    pp.SINGLE_LINE_KEPT + ", UNDECIDED — no visual reviewer"):
            with self.subTest(how=how):
                self.assertIsNone(stitch.orientation_of({"tableOrientation": ["block 0: " + how]}, 0))

    def test_field_count_cannot_override_conflicting_visual_orientation_seals(self) -> None:
        a = "| 項目 | 數額 | 年度 |\n| --- | --- | --- |\n| 甲 | 10 | 2020 |\n"
        b = "| 年度 | 乙 |\n| --- | --- |\n| 數額 | 30 |\n| 項目 | 2021 |\n"
        pages = [page(1, a, tables=[{"block": 0, "text": a}],
                      tableOrientation=["block 0: kept — source image confirms row records"]),
                 page(2, b, tables=[{"block": 0, "text": b}],
                      tableOrientation=["block 0: rotated — source image confirms column records"])]
        result = stitch.plan(pages, geometry=False)
        self.assertEqual(result["chains"], [])
        self.assertEqual(result["decisions"][0]["verdict"], "undecided")
        self.assertEqual(result["pages"][1]["text"], a)
        self.assertEqual(result["pages"][2]["text"], b)

    def test_as_printed_never_uses_geometry_to_change_field_count(self) -> None:
        a = table([("甲", "10")])
        b = "| 乙 | 20 | 附記 |\n| --- | --- | --- |\n| 丙 | 30 | 附記二 |\n"
        head = page(1, a, provenance={"tableOrientation": "as-printed"})
        tail = page(2, b, provenance={"tableOrientation": "as-printed"})
        first = stitch.page_items(a, head.is_furniture)[0]
        part = stitch.page_items(b, tail.is_furniture)[0]
        chain = stitch.start_chain(head, first)

        def forbidden(*args):
            self.fail("as-printed policy must not realign the physical grid")

        decision = stitch.decide(chain, [first], tail, part, realign=forbidden)
        self.assertEqual(decision.verdict, "undecided")

    def test_three_page_note_chain_keeps_each_call_and_note_once(self) -> None:
        texts = [table([(label + marker, str(10 * n))]) + f"\n{marker} 注文{label}。\n"
                 for n, (label, marker) in enumerate(zip("甲乙丙", "①②③"), 1)]
        result = stitch.plan([page(n, text) for n, text in enumerate(texts, 1)], geometry=False)
        self.assertEqual(result["chains"][0]["pages"], [1, 2, 3])
        head = result["pages"][1]["text"]
        for label, marker in zip("甲乙丙", "①②③"):
            self.assertEqual(head.count(label + marker), 1)
            self.assertEqual(head.count(f"{marker} 注文{label}。"), 1)
        self.assertEqual(result["pages"][2]["text"], "")
        self.assertEqual(result["pages"][3]["text"], "")

    def test_mixed_recorded_orientation_policy_is_undecided(self) -> None:
        a, b = table([("甲", "10")]), table([("乙", "20")])
        result = stitch.plan([page(1, a), page(2, b, provenance={"tableOrientation": "as-printed"})],
                             geometry=False)
        self.assertEqual(result["chains"], [])
        self.assertIn("orientation policies differ", result["decisions"][0]["evidence"][-1])

    def test_stale_final_table_mapping_blocks_each_side(self) -> None:
        a, b = table([("甲", "10")]), table([("乙", "20")])
        for stale in (1, 2):
            with self.subTest(stale=stale):
                pages = [page(n, text, tableMappingCurrent=(n != stale),
                              tables=[{"block": 0, "text": text}],
                              tableOrientation=["block 0: kept — visual verdict"])
                         for n, text in ((1, a), (2, b))]
                result = stitch.plan(pages, geometry=False)
                self.assert_blocked(result, a, b)
                self.assertIsNone(stitch.seal_entry(pages[stale - 1].record, (a, b)[stale - 1]))
                self.assertIn("re-establish the mapping", result["decisions"][0]["evidence"][0])

    def test_directory_replay_is_idempotent_and_source_seals_are_unchanged(self) -> None:
        a = "## 分項表\n\n" + table([("甲①", "10")]) + "\n① 前註。\n"
        b = "## 分項表（續）\n\n" + table([("乙②", "20")]) + "\n② 後註。\n"
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "source", Path(tmp) / "stitched"
            source.mkdir()
            for n, text in ((1, a), (2, b)):
                record = stitch_tables.resealed({"schemaVersion": pp.SCHEMA_VERSION, "status": "complete",
                                                "scanPage": n, "doubts": [], "provenance": {}}, text, None, [])
                (source / f"page-{n:04d}.md").write_text(text, encoding="utf-8")
                (source / f"page-{n:04d}.json").write_bytes(pp.canonical_json_bytes(record))
            source_before = stitch_tables.page_files(source)
            first = stitch_tables.stitch_directory(source, output_dir=target, log=lambda message: None)
            self.assertEqual(len(first["tablesStitched"]), 1)
            before = stitch_tables.page_files(target)
            second = stitch_tables.stitch_directory(target, log=lambda message: None)
            self.assertEqual(second["pagesRewritten"], [])
            self.assertEqual(stitch_tables.page_files(target), before)
            self.assertEqual(stitch_tables.page_files(source), source_before)
            for n, text in ((1, a), (2, b)):
                record, _, why = stitch_tables.sealed_page(target / f"page-{n:04d}.json")
                self.assertEqual(why, "")
                self.assertEqual(record["tableStitch"]["before"], text)


if __name__ == "__main__":
    unittest.main()
