"""The formatter's furniture deletion and its letter/figure guard, and engine
B drafts cut off before their reply ended.

Synthetic pages and fake clients only: no model, no OCR engine, no network.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402
from test_fidelity_switch_and_seals import Fake, between, inventory, page, review, run  # noqa: E402

HEAD = ("header", "卷一", [0.1, 0.02, 0.9, 0.05])
BODY = ("text", "見所著共人其書甚詳", [0.1, 0.1, 0.9, 0.8])
DRAFT = "卷一\n見所著共人其書甚詳"
LATIN = "見Skinner所著，共1513人。其書甚詳"
# A photo-reprint with two leaves a page, and a running head measured on
# other pages of the book.
LAYOUT = {"layout": {"leavesPerPage": 2, "runningHeads": ["某書"]}}


class Marked(Fake):
    """Fake whose census counts marks, so each block is punctuated; the
    punctuator writes in the Western name and the number, as a punctuator
    reading the crop does."""

    def __init__(self, **answers):
        super().__init__(**answers)
        self.sent: list[tuple[str, str, str]] = []

    def ask_answering(self, system, user_text, image, max_tokens, kind="other", **_):
        self.sent.append((kind, system, user_text))
        if kind == "census":
            self.calls.append((kind, user_text))
            return "符號：2\n形狀：，。", {}
        if kind == "punctuate":
            self.calls.append((kind, user_text))
            return between(user_text).replace("見所著共人", "見Skinner所著，共1513人。"), {}
        return super().ask_answering(system, user_text, image, max_tokens, kind)

    def prompts(self, kind):
        """(system, user text) of each call of KIND."""
        return [(system, user) for k, system, user in self.sent if k == kind]


def without_furniture(user, _):
    return "\n\n".join(line for line in between(user).split("\n") if "Skinner" in line)


def without_latin(user, _):
    return between(user).replace("Skinner", "").replace("1513", "")


class VerifyCharactersTests(unittest.TestCase):
    def test_off_rule_refuses_lost_letters_and_figures_but_lets_furniture_go(self):
        ok, reason = pp.verify_characters("見Skinner所著，共1513人。", "見所著，共人。", "")
        self.assertFalse(ok)
        self.assertIn("letter(s) or figure(s) outside the page's furniture", reason)
        self.assertIn("Skinner", reason)
        # A CJK running head may go (deleted_runs judges CJK deletions) ...
        self.assertTrue(pp.verify_characters("卷一\n" + LATIN, LATIN, "")[0])
        # ... so may a folio's figures where the removable text holds them ...
        self.assertTrue(pp.verify_characters("- 12 -\n" + LATIN, LATIN, "- 12 -")[0])
        self.assertFalse(pp.verify_characters("- 12 -\n" + LATIN, LATIN, "")[0])
        # ... but no CJK character may be invented.
        ok, reason = pp.verify_characters(LATIN, LATIN + "某", "")
        self.assertFalse(ok)
        self.assertIn("introduced", reason)
        # A note call written in another form keeps its figure; markup and
        # LaTeX command names are no text.
        self.assertTrue(pp.verify_characters("見①所著", "見<sup>1</sup>所著", "")[0])
        self.assertTrue(pp.verify_characters(r"開本$ 850 \times 1168 $毫米", "開本850×1168毫米", "")[0])
        self.assertTrue(pp.verify_characters("甲 12", "| 甲 | 12 |\n| --- | --- |", "")[0])

    def test_strict_rule_after_a_content_review_deletes_nothing(self):
        self.assertFalse(pp.verify_characters("卷一\n" + LATIN, LATIN)[0])
        self.assertFalse(pp.verify_characters("見Skinner所著，共1513人。", "見所著，共人。")[0])

    def test_removable_text_is_furniture_folios_heads_and_restored_figures(self):
        blocks = [{"label": "header", "text": "卷一 Vol. I", "box": [0.1, 0.02, 0.9, 0.05]},
                  {"label": "text", "text": "見Skinner所著", "box": [0.1, 0.1, 0.9, 0.8]},
                  {"label": "table", "text": "<table></table>", "box": [0.1, 0.8, 0.9, 0.9]},
                  {"label": "number", "text": "12", "box": [0.45, 0.95, 0.55, 0.98]}]
        punctuated = ["卷一 Vol. I", "見Skinner所著", "⟦T1⟧", "12"]
        text = pp.removable_text(blocks, punctuated, "\n".join(punctuated) + "\n- iv -", ["某書"], ["3."])
        for kept in ("卷一 Vol. I", "12", "- iv -", "某書", "3."):
            self.assertIn(kept, text)
        self.assertNotIn("Skinner", text)
        self.assertNotIn("⟦T1⟧", text)
        # The whole-page path has no punctuated spans: the furniture blocks'
        # own text counts there (a running head, a folio), the body's and the
        # table's do not.
        whole = pp.removable_text(blocks, [], "見Skinner所著", [], ["7"])
        self.assertEqual(whole.split("\n"), ["卷一 Vol. I", "12", "7"])
        # A figure's text stays: the whole-page fallback keeps it.
        figure = {"label": "figure", "text": "圖1 Map", "box": [0.1, 0.3, 0.9, 0.6]}
        self.assertNotIn("Map", pp.removable_text([*blocks, figure], [], "", [], []))


    def test_whole_page_head_and_folio_with_figures_may_go(self):
        # The whole-page path: a running head holding figures and a folio in
        # a number block, deleted by the formatter.  Their letters and
        # figures are the furniture blocks' own text, as on the block path,
        # whether the profile keeps the head with its figures or CJK only;
        # the body's figures still may not go.
        blocks = [{"label": "header", "text": "第三章 1911年的革命", "box": [0.1, 0.02, 0.5, 0.02]},
                  {"label": "number", "text": "125", "box": [0.85, 0.02, 0.05, 0.02]},
                  {"label": "text", "text": "正文甲乙丙丁，共30人。", "box": [0.1, 0.1, 0.8, 0.7]}]
        given = "第三章 1911年的革命 125\n正文甲乙丙丁，共30人。\n"
        for head in ("第三章1911年的革命", "第三章年的革命"):
            with self.subTest(head=head):
                block_path = pp.removable_text(blocks, ["第三章 1911年的革命", "125", "正文甲乙丙丁，共30人。"],
                                               given, [head], [])
                whole_page = pp.removable_text(blocks, [], given, [head], [])
                for removable in (block_path, whole_page):
                    self.assertEqual(pp.verify_characters(given, "正文甲乙丙丁，共30人。\n", removable),
                                     (True, "characters preserved"))
                    self.assertEqual(pp.furniture_unexplained(given, "正文甲乙丙丁，共30人。\n", removable), "")
                    ok, reason = pp.verify_characters(given, "正文甲乙丙丁，共人。\n", removable)
                    self.assertFalse(ok)
                    self.assertIn("outside the page's furniture: 30", reason)


class FurnitureUnexplainedTests(unittest.TestCase):
    """Without a content review, the CJK the formatter deletes must be
    furniture by evidence, however short its runs (deleted_runs reports runs
    of DELETED_RUN_LIMIT characters only)."""

    def test_short_body_runs_are_named_in_page_order(self):
        given = ("臣查此等字跡任承恩既經搜出即應隨折進呈。今移送臣處臣謹封呈覽但任承恩折稿稱有五紙。"
                 "今查原包內只有四紙除咨詢任承恩外合併聲明謹奏。")
        formatted = "臣查此等字跡，即應隨折進呈。今移送臣處，臣謹封呈覽，但任承恩折稿稱有五紙。除咨詢任承恩外，合併聲明。謹奏。"
        # Runs of 7 and 9 characters: no deleted run, and nothing else saw them.
        self.assertEqual(pp.deleted_runs(given, formatted), [])
        self.assertEqual(pp.furniture_unexplained(given, formatted, ""), "任承恩既經搜出今查原包內只有四紙")
        # A 凡例 whose repeated 一 was renumbered 1. 2. 3.
        given = "凡例\n一、本書所收檔案以時間為序。\n一、原件殘缺之字以□表示。\n一、編者所加按語以〔〕標出。\n"
        formatted = "## 凡例\n\n1. 本書所收檔案以時間為序。\n2. 原件殘缺之字以□表示。\n3. 編者所加按語以〔〕標出。\n"
        self.assertEqual(pp.furniture_unexplained(given, formatted, ""), "一一一")
        # Text moved, not lost.
        self.assertEqual(pp.furniture_unexplained("甲乙丙丁戊己庚辛", "戊己庚辛\n\n甲乙丙丁", ""), "")

    def test_furniture_by_evidence_may_go(self):
        body = "臣查此等字跡即應隨折進呈"
        # A block labelled furniture, or a known head (REMOVABLE) ...
        self.assertEqual(pp.furniture_unexplained("卷一\n" + body, body, "卷一"), "")
        self.assertEqual(pp.furniture_unexplained("卷一\n" + body, body, ""), "卷一")
        # ... a measured running head, misread or with its folio beside it ...
        for head in ("某書第二冊", "某書第二册", "某書第二冊一二五"):
            with self.subTest(head=head):
                self.assertEqual(pp.furniture_unexplained(head + "\n" + body, body, "", frozenset(), ["某書第二冊"]), "")
        # ... and a head the book's recurring n-grams explain, at the page's
        # edge, its volume and folio beside it, though the diff cut it in two
        # where the answer kept one of its figures.
        given = "正文說到治標方法雖爲某某先生言論集第五集六九"
        self.assertEqual(pp.furniture_unexplained(given, "正文說到治標方法雖爲五", "",
                                                  {"某某先生言論集第"}, ["某某先生言論集"]), "")
        # Evidence for a head explains no body text beside it.
        self.assertEqual(pp.furniture_unexplained("甲乙丙丁戊己庚辛", "甲乙丙丁戊己", "", {"某某先生言論集第"},
                                                  ["某某先生言論集"]), "庚辛")


class FormatterFurnitureTests(unittest.TestCase):
    def run_page(self, formatter, *switches, answers=None, profile=None):
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), DRAFT, DRAFT, *switches, blocks=[HEAD, BODY])
            if profile is not None:
                args.profile_data = profile
            client = Marked(**{"format": formatter, "format-retry": formatter, **(answers or {})})
            record, text = run(job, args, client)
        return record, text, client

    def test_off_formatter_deletes_the_running_head(self):
        record, text, client = self.run_page(without_furniture)
        self.assertEqual(text.strip(), LATIN)
        self.assertEqual(record["doubts"], [])
        self.assertNotIn("format-retry", client.kinds())

    def test_off_formatter_that_drops_latin_and_figures_is_refused(self):
        record, text, client = self.run_page(without_latin)
        self.assertEqual(client.kinds().count("format-retry"), 1)
        kinds = [d["kind"] for d in record["doubts"]]
        self.assertEqual(kinds, ["structure-rejected"])
        self.assertIn("Skinner1153", record["doubts"][0]["detail"])
        # The fallback: one paragraph per block, the header block left out,
        # the name and the number kept.
        self.assertEqual(text.strip(), LATIN)

    def test_off_formatter_that_drops_a_short_clause_is_refused(self):
        # Four characters of body text: no deleted run (DELETED_RUN_LIMIT),
        # and before, the page was sealed without them and no doubt.
        def without_clause(user, _):
            return between(user).replace("其書甚詳", "")

        record, text, client = self.run_page(without_clause)
        self.assertEqual(client.kinds().count("format-retry"), 1)
        self.assertEqual([d["kind"] for d in record["doubts"]], ["structure-rejected"])
        self.assertIn("deleted 4 character(s) outside the page's furniture (其書甚詳", record["doubts"][0]["detail"])
        self.assertIn("retry still deleted 4 character(s)", record["formatterRetryRefusal"])
        # The fallback keeps the clause, and leaves the header block out.
        self.assertEqual(text.strip(), LATIN)

    def test_off_whole_page_formatter_may_drop_the_margin_blocks_text_only(self):
        # The blocks do not account for the draft (one character more): the
        # page is formatted whole, and REMOVABLE holds no block.  The page's
        # margin blocks (margin_furniture) are the evidence there.
        draft = DRAFT + "也"
        for name, drop, kinds, kept in (("head", "卷一", ["punctuation-whole-page"], "見所著共人其書甚詳也"),
                                        ("clause", "其書", ["formatter-rejected"], "卷一見所著共人其書甚詳也")):
            def formatter(user, _, drop=drop):
                return between(user).replace(drop, "")

            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                job, args = page(Path(tmp), draft, draft, blocks=[HEAD, BODY])
                client = Fake(format=formatter, **{"format-retry": formatter})
                record, text = run(job, args, client)
                self.assertEqual([d["kind"] for d in record["doubts"]], kinds)
                self.assertEqual(pp.CJK(text), kept)

    def test_off_prompts_ask_the_formatter_to_delete_furniture(self):
        record, text, client = self.run_page(without_furniture, profile=LAYOUT)
        system, prompt = client.prompts("format")[0]
        self.assertEqual(system, pp.STRUCTURE_SYSTEM)
        self.assertIn("刪走欄外書眉／頁碼", prompt)
        self.assertIn("全部係家具，刪走", prompt)
        self.assertIn("某書；出現喺正文以外就刪走", prompt)
        self.assertIn("刪走欄外書眉、頁碼等頁面家具", pp.STRUCTURE_SYSTEM)
        self.assertIn("刪走欄外書眉、頁碼等頁面家具", pp.FORMAT_SYSTEM)

    def test_on_after_content_recovery_the_formatter_deletes_nothing(self):
        inv = inventory(regions=(("r1", "heading"), ("r2", "body")))
        answers = {"page-inventory": inv,
                   "content-recovery": lambda u, _: review(json.loads(u)),
                   "coverage-review": lambda u, _: review(json.loads(u))}
        record, text, client = self.run_page(without_furniture, "--content-fidelity", "on", answers=answers,
                                             profile=LAYOUT)
        system, prompt = client.prompts("format")[0]
        self.assertEqual(system, pp.STRUCTURE_SYSTEM_REVIEWED)
        self.assertNotIn("刪走", prompt)
        self.assertNotIn("某書", prompt)
        # A formatter that deletes the head anyway is refused (strict rule),
        # and the image-reviewed text is kept.
        self.assertIn("format-retry", client.kinds())
        self.assertIn("formatter deleted 2 retained character(s)",
                      " ".join(d["detail"] for d in record["doubts"]))
        self.assertIn("卷一", text)


class TableLossTests(unittest.TestCase):
    """The body-text guard (furniture_unexplained) and a table the structure
    pass rebuilds: bench doukeoi-zingkeoi s0327, whose retry left out one 兩
    of a merged cell and was refused, the page sealed with the table as a
    paragraph; hinzing-6 s0071, whose first answer, four characters short in
    the table, was refused and the retry split the table's rows."""

    # A table block whose engine grid is refused (cells hold line breaks,
    # engine B wrote no grid), as on s0071: the structure pass writes it.
    LEAD = "概由塾中發給自應預算經費方易籌辦"
    GRID = ("<table><tr><td>書物名</td><td>價值\n購置處</td></tr><tr><td>習字帖</td><td>每册一分七釐\n同前</td></tr>"
            "<tr><td>瓦墨盌</td><td>每個三分\n一個</td></tr></table>")
    TABLE = "| 書物名 | 價值購置處 |\n| --- | --- |\n| 習字帖 | 每册一分七釐同前 |\n| 瓦墨盌 | 每個三分一個 |"

    def run_page(self, answer):
        blocks = [("text", self.LEAD, [0.1, 0.05, 0.8, 0.05]), ("table", self.GRID, [0.1, 0.2, 0.8, 0.6])]
        draft2 = self.LEAD + "\n書物名價值購置處\n習字帖每册一分七釐同前\n瓦墨盌每個三分一個"
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), self.LEAD + "\n" + self.GRID, draft2, blocks=blocks)
            client = Marked(**{"format": answer, "format-retry": answer})
            record, text = run(job, args, client)
        self.assertEqual(len(record["tablesRejected"]), 1)
        return record, text, client

    def test_a_table_short_of_a_character_is_kept_with_a_doubt_not_flattened(self):
        answer = self.LEAD + "\n\n" + self.TABLE.replace("七釐", "七")
        record, text, client = self.run_page(answer)
        self.assertNotIn("format-retry", client.kinds())
        self.assertNotIn("structure-rejected", [d["kind"] for d in record["doubts"]])
        self.assertEqual(text.strip(), answer)
        lost = [d for d in record["doubts"] if d["kind"] == "table-characters-lost"]
        self.assertEqual(len(lost), 1)
        self.assertIn("(釐)", lost[0]["detail"])
        self.assertEqual(record["tableCharactersLost"], "釐")

    def test_text_moved_into_the_table_is_not_lost(self):
        # The lead-in written as the table's first row, as s0071's retry did.
        answer = (f"<table>\n<tr><td colspan=\"2\">{self.LEAD}</td></tr>\n<tr><td>書物名</td><td>價值購置處</td></tr>\n"
                  "<tr><td>習字帖</td><td>每册一分七釐同前</td></tr>\n<tr><td>瓦墨盌</td><td>每個三分一個</td></tr>\n</table>")
        record, text, client = self.run_page(answer)
        self.assertNotIn("format-retry", client.kinds())
        self.assertEqual(text.strip(), answer)
        self.assertNotIn("table-characters-lost", [d["kind"] for d in record["doubts"]])

    def test_a_table_written_as_printed_is_turned_to_reading_order(self):
        # s0071's accepted answer: the vertical table as printed, each field
        # a row and each item a column (right to left).  Engine B reads the
        # page in reading order, field names first, then each item; the
        # table read row by row must be that order, so the answer's table is
        # transposed (orient_written_tables) and the turn sealed.
        printed = ("| 書物名 | 習字帖 | 瓦墨盌 |\n| --- | --- | --- |\n"
                   "| 價值購置處 | 每册一分七釐同前 | 每個三分一個 |")
        answer = self.LEAD + "\n\n" + printed
        record, text, client = self.run_page(answer)
        self.assertNotIn("format-retry", client.kinds())
        self.assertEqual(text.strip(), self.LEAD + "\n\n" + self.TABLE)
        self.assertEqual(len(record["tableOrientation"]), 1)
        self.assertTrue(record["tableOrientation"][0].startswith("structure pass table 1: transposed — "),
                        record["tableOrientation"])
        evidence = record["tableOrientationEvidence"]
        self.assertEqual([(e["inputText"], e["outputText"], e["outcome"], e["status"]) for e in evidence],
                         [(printed, self.TABLE, "resolved", "active")])
        turned = [c for c in record["decisionChanges"] if c.get("rule") == "structure-table-oriented"]
        self.assertEqual([(c["decision"], c["after"], c["evidence"]["asWritten"]) for c in turned],
                         [("D23", self.TABLE, printed)])
        self.assertNotIn("table-orientation-undecided", [d["kind"] for d in record["doubts"]])
        # The answer in reading order already is kept as it stands.
        record, text, _ = self.run_page(self.LEAD + "\n\n" + self.TABLE)
        self.assertEqual(text.strip(), self.LEAD + "\n\n" + self.TABLE)
        self.assertTrue(record["tableOrientation"][0].startswith("structure pass table 1: kept — "),
                        record["tableOrientation"])
        self.assertEqual([c for c in record["decisionChanges"] if c.get("rule") == "structure-table-oriented"], [])

    def test_a_written_table_turned_on_engine_b_reading_is_a_doubt(self):
        # FX3 review F1: engine B's order and its lines are one reading, and
        # a table written right that engine B read band by band, several
        # cells to a line, counts the same as this one: every turn is a
        # doubt, beside its D23 change, so a person checks it.
        printed = ("| 書物名 | 習字帖 | 瓦墨盌 |\n| --- | --- | --- |\n"
                   "| 價值購置處 | 每册一分七釐同前 | 每個三分一個 |")
        record, text, _ = self.run_page(self.LEAD + "\n\n" + printed)
        self.assertEqual(text.strip(), self.LEAD + "\n\n" + self.TABLE)
        turned = [d for d in record["doubts"] if d["kind"] == "table-orientation-turned"]
        self.assertEqual(len(turned), 1, record["doubts"])
        self.assertTrue(turned[0]["detail"].startswith("STRUCTURE TABLE TURNED: structure pass table 1 (transposed)"),
                        turned[0]["detail"])
        self.assertIn("check the table against the image", turned[0]["detail"])
        self.assertNotIn("table-orientation-undecided", [d["kind"] for d in record["doubts"]])
        # A table kept as written is no doubt.
        record, _, _ = self.run_page(self.LEAD + "\n\n" + self.TABLE)
        self.assertNotIn("table-orientation-turned", [d["kind"] for d in record["doubts"]])

    def test_a_written_table_turn_is_a_doubt_whatever_its_total(self):
        # A vertical page (its lead-in printed down the page) whose table
        # the structure pass wrote right, each record a row, its total last;
        # engine B read the table one printed band to a line, so its order
        # and its lines both say transposed.  A 合計 does not say which axis
        # was printed as a line (FX4 review N1), so it decides nothing: the
        # table is turned on engine B's reading alone, sealed as D23, and
        # the page raises a table-orientation-turned doubt for a person to
        # check against the image - the safety net for exactly this page.
        rows = [["項目", "甲款", "乙款", "合計"], ["某甲", "一一", "二二", "三三"],
                ["某乙", "四四", "五五", "九九"], ["某丙", "一二", "二四", "三六"]]
        bands = ["".join(row[c] for row in rows) for c in range(4)]
        grid = "<table>" + "".join("<tr>" + "".join(f"<td>{row[c]}</td>" for row in rows[:2])
                                   + f"<td>{rows[2][c]}\n{rows[3][c]}</td></tr>" for c in range(4)) + "</table>"
        blocks = [("vertical_text", self.LEAD, [0.85, 0.05, 0.05, 0.8]), ("table", grid, [0.1, 0.1, 0.7, 0.8])]
        table = ("| 項目 | 甲款 | 乙款 | 合計 |\n| --- | --- | --- | --- |\n| 某甲 | 一一 | 二二 | 三三 |\n"
                 "| 某乙 | 四四 | 五五 | 九九 |\n| 某丙 | 一二 | 二四 | 三六 |")
        answer = self.LEAD + "\n\n" + table
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), self.LEAD + "\n" + grid, self.LEAD + "\n" + "\n".join(bands), blocks=blocks)
            client = Marked(**{"format": answer, "format-retry": answer})
            record, text = run(job, args, client)
        self.assertEqual(len(record["tablesRejected"]), 1)
        self.assertNotIn("format-retry", client.kinds())
        turned_table = ("| 項目 | 某甲 | 某乙 | 某丙 |\n| --- | --- | --- | --- |\n| 甲款 | 一一 | 四四 | 一二 |\n"
                        "| 乙款 | 二二 | 五五 | 二四 |\n| 合計 | 三三 | 九九 | 三六 |")
        self.assertEqual(text.strip(), self.LEAD + "\n\n" + turned_table)
        self.assertEqual(len(record["tableOrientation"]), 1)
        self.assertTrue(record["tableOrientation"][0].startswith("structure pass table 1: transposed — "),
                        record["tableOrientation"])
        self.assertTrue(record["tableOrientation"][0].endswith(pp.WRITTEN_TURN_UNCHECKED), record["tableOrientation"])
        self.assertEqual(len([c for c in record["decisionChanges"] if c.get("rule") == "structure-table-oriented"]), 1)
        kinds = [d["kind"] for d in record["doubts"]]
        self.assertIn("table-orientation-turned", kinds)
        self.assertNotIn("table-orientation-undecided", kinds)

    def test_body_text_lost_beside_a_table_is_still_refused(self):
        answer = self.LEAD.replace("預算經費方易", "") + "\n\n" + self.TABLE
        record, text, client = self.run_page(answer)
        self.assertEqual(client.kinds().count("format-retry"), 1)
        self.assertIn("structure-rejected", [d["kind"] for d in record["doubts"]])
        self.assertIn("deleted 6 character(s) outside the page's furniture (預算經費方易", record["formatterRetryReason"])
        self.assertIn("預算經費方易", text)

    def test_a_character_is_counted_where_it_went(self):
        given = "正文甲乙兩\n" + "名兩兩名兩"
        out_table = "| 名 | 兩 |\n| --- | --- |\n| 名 | 兩 |"
        # The table lacks a 兩 its block held; the prose holds its own: the table's.
        self.assertEqual(pp.split_table_losses("兩", given, "正文甲乙兩\n\n" + out_table, "名兩兩名兩"), ("", "兩"))
        # The prose lacks its 兩; the table holds all of its own: the prose's.
        self.assertEqual(pp.split_table_losses("兩", given, "正文甲乙\n\n" + out_table.replace("| 兩 |\n|", "| 兩兩 |\n|"),
                                                "名兩兩名兩"), ("兩", ""))
        # Lost from both: one each (the prose's 兩, and one of the table's).
        self.assertEqual(pp.split_table_losses("兩兩", given, "正文甲乙\n\n" + out_table, "名兩兩名兩"), ("兩", "兩"))
        # A table's text written as prose stands in for none the prose lost:
        # the table's 名 written beside it, and the prose's 乙 lost.
        self.assertEqual(pp.split_table_losses("乙", given, "正文甲兩名\n\n" + out_table.replace("| 名 | 兩 |\n| ---", "|  | 兩 |\n| ---"),
                                                "名兩兩名兩"), ("乙", ""))
        # No tables given: everything is the prose's, as before.
        self.assertEqual(pp.split_table_losses("兩乙", given, "正文甲", ""), ("兩乙", ""))


class RefusedTableMarksTests(unittest.TestCase):
    """The punctuation lock and a table the structure pass builds from the
    image because its engine grid was refused: bench doukeoi-zingkeoi s0327,
    whose table block went to the structure pass as its characters alone (a
    table block has no punctuation pass), so both answers - the full table
    with the 、 and （） printed in its cells, which both engines read - were
    refused for "added 、×2" and the page was sealed with the table as one
    paragraph.  The marks both engines read in such a block may stand in the
    answer's tables; prose still may not gain a mark."""

    LEAD = "概由營中發給自應預算經費方易籌辦"
    # The grid is refused: its cells hold line breaks, engine B wrote none.
    GRID = ("<table><tr><td>營名</td><td>一年花費（人數\n天數）</td></tr>"
            "<tr><td>長沙、長寧\n共二營</td><td>每名一兩</td></tr></table>")
    B_LINES = "\n營名一年花費（人數天數）\n長沙、長寧共二營每名一兩"
    TABLE = "| 營名 | 一年花費（人數天數） |\n| --- | --- |\n| 長沙、長寧共二營 | 每名一兩 |"

    def run_page(self, answer, b_lines=B_LINES):
        blocks = [("text", self.LEAD, [0.1, 0.05, 0.8, 0.05]), ("table", self.GRID, [0.1, 0.2, 0.8, 0.6])]
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), self.LEAD + "\n" + self.GRID, self.LEAD + b_lines, blocks=blocks)
            client = Fake(**{"format": answer, "format-retry": answer})
            record, text = run(job, args, client)
        self.assertEqual(len(record["tablesRejected"]), 1)
        return record, text, client

    def test_the_marks_both_engines_read_in_a_refused_table_may_stand_in_its_table(self):
        answer = self.LEAD + "\n\n" + self.TABLE
        record, text, client = self.run_page(answer)
        self.assertNotIn("format-retry", client.kinds())
        self.assertNotIn("structure-rejected", [d["kind"] for d in record["doubts"]])
        self.assertEqual(text.strip(), answer)
        self.assertEqual(sorted(record["tableMarksAllowed"]), sorted("（）、"))

    def test_a_mark_the_answer_adds_to_prose_is_still_refused(self):
        # The table's 、 moved into the prose ...
        answer = self.LEAD.replace("發給", "發給、") + "\n\n" + self.TABLE.replace("、", "")
        record, text, client = self.run_page(answer)
        self.assertEqual(client.kinds().count("format-retry"), 1)
        self.assertEqual(record["formatterRetryReason"], "changed punctuation: added 、×1")
        self.assertIn("structure-rejected", [d["kind"] for d in record["doubts"]])
        self.assertEqual(record["tableMarksAllowed"], "")
        self.assertNotIn("、", text)
        # ... and added to the prose beside a table that carries its own.
        answer = self.LEAD.replace("發給", "發給、") + "\n\n" + self.TABLE
        record, text, client = self.run_page(answer)
        self.assertEqual(record["formatterRetryReason"], "changed punctuation: added 、×1")

    def test_a_mark_one_engine_read_or_read_fewer_times_is_refused(self):
        # Engine B read no 、 in the table: engine A's alone is not enough.
        record, text, client = self.run_page(self.LEAD + "\n\n" + self.TABLE,
                                             b_lines=self.B_LINES.replace("、", ""))
        self.assertEqual(record["formatterRetryReason"], "changed punctuation: added 、×1")
        # Both read it once; the answer writes it twice in the table.
        record, text, client = self.run_page(self.LEAD + "\n\n" + self.TABLE.replace("共二營", "、共二營"))
        self.assertEqual(record["formatterRetryReason"], "changed punctuation: added 、×1")

    def test_the_lock_input_gains_only_the_marks_written_inside_tables(self):
        given = "甲乙丙\n丁戊己庚"
        allowed = Counter({"、": 2, "（": 1, "）": 1})
        table = "| 丁、戊 | 己（庚） |\n| --- | --- |"
        held, extra = pp.table_marks_lock(given, "甲乙丙\n\n" + table, allowed)
        self.assertEqual(extra, Counter({"、": 1, "（": 1, "）": 1}))
        self.assertTrue(pp.punctuation_kept(held, "甲乙丙\n\n" + table))
        # A mark outside the tables is not covered, the same mark inside is.
        held, extra = pp.table_marks_lock(given, "甲、乙丙\n\n" + table.replace("、", ""), allowed)
        self.assertEqual(extra, Counter({"（": 1, "）": 1}))
        self.assertFalse(pp.punctuation_kept(held, "甲、乙丙\n\n" + table.replace("、", "")))
        # Nothing allowed: the lock holds the text given, as before.
        self.assertEqual(pp.table_marks_lock(given, "甲乙丙\n\n" + table, Counter()), (given, Counter()))
        # A table the text given already holds (a recovered one) brings no more.
        held, extra = pp.table_marks_lock("甲乙丙\n" + table, "甲乙丙\n\n" + table, allowed)
        self.assertEqual((held, extra), ("甲乙丙\n" + table, Counter()))
        # A leader each: two allowed leaders are two marks, not one run.
        held, extra = pp.table_marks_lock("甲乙", "| 甲……乙…… |\n| --- |", Counter({pp.LEADER: 2}))
        self.assertTrue(pp.punctuation_kept(held, "| 甲……乙…… |\n| --- |"))


class NoteMarkerTests(unittest.TestCase):
    """Bench tin-dei-wui-4 s0477: the punctuation pass wrote ① and ② at the
    head of the page's two notes, and the structure pass left out the ②; the
    notes sit in the folio's margin band, so the figure 2 was furniture to the
    character check, and the page was sealed with one note label (only [^1]
    paired, the two notes swapped by the scorer)."""

    BODY = "令與閩浙派駐防兵丁先行撤回查明現有船隻"
    NOTE1 = "錄自明清史料戊編第三本"
    NOTE2 = "欽定平定臺灣紀略刻本卷五十七為駐防"
    # What the punctuation pass writes for each block, its note markers too.
    PUNCTUATED = {BODY: "令與閩浙派〔駐〕②防兵丁，先行撤回。查明現有船隻",
                  NOTE1: "①錄自《明清史料戊編》第三本。", NOTE2: "②《欽定平定臺灣紀略》（刻本）卷五十七為「駐防」。"}

    def run_page(self, answer, punctuated=None, draft2=None):
        blocks = [("text", "令與閩浙派〔駐〕②防兵丁，先行撤回。查明現有船隻", [0.1, 0.1, 0.8, 0.6]),
                  ("footnote", "① 錄自《明清史料戊編》第三本。", [0.15, 0.81, 0.5, 0.018]),
                  ("footnote", "②《欽定平定臺灣紀略》（刻本）卷五十七為「駐防」。", [0.15, 0.83, 0.5, 0.018]),
                  ("number", "451", [0.75, 0.865, 0.05, 0.017])]
        draft = "\n".join(text for _, text, _ in blocks)
        answers = punctuated or self.PUNCTUATED

        class Punctuator(Marked):
            def ask_answering(inner, system, user_text, image, max_tokens, kind="other", **_):
                if kind in ("census", "punctuate"):
                    inner.calls.append((kind, user_text))
                    # A count the census could not take: no count refusal.
                    return ("符號：不詳\n雙圈：0\n形狀：無" if kind == "census"
                            else answers[pp.CJK(between(user_text))]), {}
                return super().ask_answering(system, user_text, image, max_tokens, kind)

        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), draft, draft if draft2 is None else draft2(draft), blocks=blocks)
            client = Punctuator(**{"format": answer, "format-retry": answer})
            record, text = run(job, args, client)
        return record, text, client

    def test_a_note_marker_the_structure_pass_left_out_is_written_back(self):
        answer = ("令與閩浙派〔駐〕②防兵丁，先行撤回。查明現有船隻\n\n①錄自《明清史料戊編》第三本。\n\n"
                  "《欽定平定臺灣紀略》（刻本）卷五十七為「駐防」。")
        record, text, client = self.run_page(answer)
        self.assertNotIn("format-retry", client.kinds())
        self.assertIn("\n\n②《欽定平定臺灣紀略》", text)
        self.assertEqual(text.count("②"), 2)
        changes = [c for c in record.get("decisionChanges", []) if c["rule"] == "note-marker-restored"]
        self.assertEqual([(c["decision"], c["after"], c["contextAfter"]) for c in changes],
                         [("textLoss", "②", "欽定平定臺灣紀略")])

    def test_an_answer_that_keeps_its_markers_is_left_alone(self):
        answer = ("令與閩浙派〔駐〕[^2]防兵丁，先行撤回。查明現有船隻\n\n[^1]: 錄自《明清史料戊編》第三本。\n\n"
                  "[^2]: 《欽定平定臺灣紀略》（刻本）卷五十七為「駐防」。")
        record, text, client = self.run_page(answer)
        self.assertEqual(text.strip(), answer)
        self.assertFalse([c for c in record.get("decisionChanges", []) if c["rule"] == "note-marker-restored"])

    def test_a_label_both_engines_read_that_the_pass_left_out_is_given_to_the_structure_pass(self):
        # Bench F2: the pass wrote ① at the head of the first note and no
        # label at the second (the same answer from two skills), which both
        # engines read, one glued to the bracket after it, one spaced.  The
        # structure pass, given no ②, wrote none; the page was sealed with one
        # note label.  Now the label goes into the text it is given.
        punctuated = dict(self.PUNCTUATED, **{self.NOTE2: "《欽定平定臺灣紀略》（刻本）卷五十七為「駐防」。"})
        record, text, client = self.run_page(lambda user, _: between(user), punctuated,
                                             draft2=lambda draft: draft.replace("②《", "② 《"))
        given = between(client.prompts("format")[0][1])
        self.assertIn("\n②《欽定平定臺灣紀略》", given)
        self.assertIn("\n②《欽定平定臺灣紀略》", text)
        self.assertEqual(text.count("②"), 2)
        changes = [(c["rule"], c["after"], c["engineA"], c["engineB"], c["contextAfter"], c["mergedOffset"])
                   for c in record["decisionChanges"] if c["decision"] == "textLoss"]
        self.assertEqual(changes, [("note-marker-both-engines", "②", "②", "②", "欽定平定臺灣紀略",
                                    len(self.BODY + self.NOTE1))])
        # Read by engine A alone: not written.
        record, text, client = self.run_page(lambda user, _: between(user), punctuated,
                                             draft2=lambda draft: draft.replace("②《", "《"))
        self.assertEqual(text.count("②"), 1)
        self.assertFalse([c for c in record["decisionChanges"] if c["decision"] == "textLoss"])

    def test_the_seal_holds_the_text_given_to_the_structure_pass(self):
        record, text, client = self.run_page(lambda user, _: between(user))
        given = between(client.prompts("format")[0][1])
        self.assertEqual(record["structureInput"], {
            "sha256": hashlib.sha256(given.encode("utf-8")).hexdigest(), "characters": len(given), "text": given})
        long = "甲" * (pp.STRUCTURE_INPUT_SEALED + 5)
        self.assertEqual(pp.structure_input_record(long), {
            "sha256": hashlib.sha256(long.encode("utf-8")).hexdigest(), "characters": len(long),
            "text": long[:pp.STRUCTURE_INPUT_SEALED], "truncated": True})
        # No structure pass (a page transcribed whole): none.
        with tempfile.TemporaryDirectory() as tmp:
            job, args = page(Path(tmp), "甲乙丙丁戊己", "甲乙丙丙丙丙")
            (Path(tmp) / "b.json").write_text(json.dumps({"truncated": True, "finishReason": "length"}),
                                             encoding="utf-8")
            record, _ = run(job, args, Fake(writer="甲乙丙丁戊己"))
        self.assertEqual(record["mode"], "transcribe")
        self.assertIsNone(record["structureInput"])

    def test_both_engines_markers_are_tied_to_the_merged_text(self):
        segments, pieces = [{"tag": "equal", "a": "甲乙丙丁", "b": "甲乙丙丁"}], ["甲乙丙丁"]
        agreed = pp.agreed_note_markers("甲乙。\n②《丙丁》", "甲乙。\n② 《丙丁》", segments, pieces)
        self.assertEqual(agreed, [{"gap": 2, "marker": "②", "raw": "②", "rawB": "②", "lineStart": True,
                                   "before": "。\n", "after": "《"}])
        # An engine's LaTeX around the marker; a call after the character before.
        agreed = pp.agreed_note_markers("甲 $ ^{①} $乙丙丁", "甲①乙丙丁", segments, pieces)
        self.assertEqual([(m["gap"], m["marker"], m["raw"], m["rawB"], m["lineStart"]) for m in agreed],
                         [(1, "①", "$ ^{①} $", "①", False)])
        # One engine alone, other figures, or another place: none.
        self.assertEqual(pp.agreed_note_markers("甲①乙丙丁", "甲乙丙丁", segments, pieces), [])
        self.assertEqual(pp.agreed_note_markers("甲①乙丙丁", "甲②乙丙丁", segments, pieces), [])
        self.assertEqual(pp.agreed_note_markers("甲①乙丙丁", "甲乙①丙丁", segments, pieces), [])
        self.assertEqual(pp.agreed_note_markers("甲①乙丙丁", None, segments, pieces), [])
        # A page whose engines read a circled number as a run of circled
        # figures, or one with a digit after it, prints numbers past one
        # glyph: a ⑲ both read may be a longer number cut short.
        for run in ("①⑨", "⑲⑳", "②0"):
            self.assertEqual(pp.agreed_note_markers("甲⑲乙丙丁" + run, "甲⑲乙丙丁", segments, pieces), [])
            self.assertEqual(pp.agreed_note_markers("甲⑲乙丙丁", "甲⑲乙丙丁$ ^{" + run + "} $", segments, pieces), [])
        self.assertEqual(len(pp.agreed_note_markers("甲⑲乙丙丁", "甲⑲乙丙丁", segments, pieces)), 1)

    def test_a_marker_the_pass_wrote_is_not_written_again(self):
        found = {"marker": "②", "lineStart": True, "before": "\n", "after": "《"}
        self.assertEqual(pp.place_note_markers("《甲乙》丙", "甲乙丙", [(0, found)]),
                         ("②《甲乙》丙", [(0, found)]))
        # Written a character off, or its figure another way: left as it is.
        self.assertEqual(pp.place_note_markers("《甲②乙》丙", "甲乙丙", [(0, found)]), ("《甲②乙》丙", []))
        self.assertEqual(pp.place_note_markers("(2)《甲乙》丙", "甲乙丙", [(0, found)]), ("(2)《甲乙》丙", []))
        # Not the block's characters: as it is.
        self.assertEqual(pp.place_note_markers("《甲》丙", "甲乙丙", [(0, found)]), ("《甲》丙", []))
        # Blocks: a line's first marker goes to the block it begins, a call to
        # the block it ends.
        call = {"gap": 2, "marker": "①", "lineStart": False}
        label = dict(call, lineStart=True)
        self.assertEqual(pp.note_markers_by_block([call, label], ["甲乙", "丙丁"]),
                         {0: [(2, call)], 1: [(0, label)]})

    def test_a_number_both_engines_read_twice_is_paired_by_place(self):
        # FX3 review F6 (probe p8_place.py): both engines read ① after 甲 and
        # after 丁; the pass wrote one ① a character off from the first and
        # left the second out.  Counted across the block, the ① left out was
        # written beside the one written (甲①乙①…) and the second call was
        # still missing.  Paired by place, the written ① stands for the first
        # and the second is written at its own gap.
        call = {"marker": "①", "lineStart": False, "before": "", "after": ""}
        self.assertEqual(pp.place_note_markers("甲乙①丙丁戊", "甲乙丙丁戊", [(1, call), (4, call)]),
                         ("甲乙①丙丁①戊", [(4, call)]))
        self.assertEqual(pp.place_note_markers("甲①乙丙丁戊", "甲乙丙丁戊", [(1, call), (4, call)]),
                         ("甲①乙丙丁①戊", [(4, call)]))
        # Both written, one of them two places off: nothing to write.
        self.assertEqual(pp.place_note_markers("甲乙丙①丁①戊", "甲乙丙丁戊", [(1, call), (4, call)]),
                         ("甲乙丙①丁①戊", []))
        # One written three places off pairs with neither, and the count is
        # one short: written at the first gap that lacks it, as before.
        self.assertEqual(pp.place_note_markers("甲乙丙丁戊己庚①", "甲乙丙丁戊己庚", [(1, call), (4, call)]),
                         ("甲①乙丙丁戊己庚①", [(1, call)]))

    def test_a_note_label_stays_at_the_head_of_its_line(self):
        # A label set before the line break that begins its note - at the end
        # of the line before, or on a line of its own - is moved back, when
        # it is a note's label by evidence: the line is a note block's text
        # (FX3 review F2: by its place alone it may be a call).  Changed from
        # bc265a4, which moved it with no evidence.
        out, restored = pp.restore_note_markers("某書第一頁。\n②《某書》卷五。", "某書第一頁。②\n\n《某書》卷五。",
                                                ["②《某書》卷五。"])
        self.assertEqual(out, "某書第一頁。\n\n②《某書》卷五。")
        self.assertEqual([(r["marker"], r.get("moved")) for r in restored], [("②", True)])
        # ... or the text given holds its call too (a cell's ⑲ and its note).
        self.assertEqual(pp.restore_note_markers("| 甲⑲ | 乙 |\n⑲〔某〕書", "| 甲⑲ | 乙 |\n\n⑲\n\n〔某〕書")[0],
                         "| 甲⑲ | 乙 |\n\n⑲〔某〕書")
        # The line before ends in a call of the same number: the label left
        # out is written, the call stays.
        self.assertEqual(pp.restore_note_markers("甲乙②\n②丙丁", "甲乙②\n\n丙丁")[0], "甲乙②\n\n②丙丁")
        # At the head of its line, in any form: left alone.
        for kept in ("某書第一頁。\n\n②《某書》卷五。", "某書第一頁。\n\n[^2]: 《某書》卷五。"):
            self.assertEqual(pp.restore_note_markers("某書第一頁。\n②《某書》卷五。", kept), (kept, []))

    def test_a_marker_at_a_line_head_with_no_evidence_of_a_label_is_not_moved(self):
        # FX3 review F2 (probe p11_moved.py): the text given holds a circled
        # figure at a block's head - the punctuation pass, or engine A's line
        # break, can put a call there - and the structure answer set it after
        # its character, at the end of the line before.  No call of the same
        # number stands elsewhere and no note block holds the line, so it is
        # no label by evidence: the answer stands (moved, finishing would read
        # the next paragraph as a note).
        given, answer = "其事見於某書甲乙丙\n①丁戊己庚辛。", "其事見於某書甲乙丙①\n\n丁戊己庚辛。"
        self.assertEqual(pp.restore_note_markers(given, answer), (answer, []))
        self.assertEqual(pp.note_labels(given), set())
        self.assertEqual(pp.restore_note_markers(given, answer, ["見於某書"]), (answer, []))
        # Left out, it is written back where the text given has it, as before.
        self.assertEqual(pp.restore_note_markers(given, "其事見於某書甲乙丙\n\n丁戊己庚辛。")[0],
                         "其事見於某書甲乙丙\n\n①丁戊己庚辛。")
        # The line a note block holds, or a call of the same number: a label.
        self.assertEqual(pp.note_labels(given, ["①丁戊己庚辛。"]), {given.index("①")})
        self.assertEqual(pp.restore_note_markers(given, answer, ["①丁戊己庚辛。"])[0],
                         "其事見於某書甲乙丙\n\n①丁戊己庚辛。")
        called = "其事見①於某書甲乙丙\n①丁戊己庚辛。"
        self.assertEqual(pp.note_labels(called), {called.rindex("①")})

    def test_markers_are_placed_beside_the_marks_they_stand_by(self):
        given = "兵部移會①\n令與閩浙派〔駐〕②防兵丁\n③錄自某書。\n④《某書》卷五。\n"
        out, restored = pp.restore_note_markers(
            given, "## 兵部移會\n\n令與閩浙派〔駐〕防兵丁\n\n錄自某書。\n\n《某書》卷五。")
        self.assertEqual(out, "## 兵部移會①\n\n令與閩浙派〔駐〕②防兵丁\n\n③錄自某書。\n\n④《某書》卷五。")
        self.assertEqual([r["marker"] for r in restored], ["①", "②", "③", "④"])
        # Kept in another form: nothing to write back.
        self.assertEqual(pp.restore_note_markers("甲①乙", "甲<sup>1</sup>乙"), ("甲<sup>1</sup>乙", []))
        self.assertEqual(pp.restore_note_markers("甲①乙", "甲[^1]乙"), ("甲[^1]乙", []))
        self.assertEqual(pp.restore_note_markers("甲①乙", "甲(1)乙"), ("甲(1)乙", []))
        # A line's first marker, its line joined to the one before (here as
        # a table's cells): it stays with the character after it.
        self.assertEqual(pp.restore_note_markers("甲\n①乙丙", "| 甲 | 乙丙 |")[0], "| 甲 | ①乙丙 |")
        self.assertEqual(pp.restore_note_markers("甲①\n乙丙", "| 甲 | 乙丙 |")[0], "| 甲① | 乙丙 |")
        # A line's first marker with no mark after it, the line before ending
        # in a mark: it begins its own line, not the end of the line before
        # (a note label there pairs with the wrong note).
        self.assertEqual(pp.restore_note_markers("某書第一頁。\n②某書卷五。", "某書第一頁。\n\n某書卷五。")[0],
                         "某書第一頁。\n\n②某書卷五。")
        self.assertEqual(pp.restore_note_markers("甲。\n①乙丙", "| 甲。 | 乙丙 |")[0], "| 甲。 | ①乙丙 |")
        # A line taken out whole (the characters beside the gap are gone):
        # its marker is not written back.
        self.assertEqual(pp.restore_note_markers("某書卷一①\n正文", "正文"), ("正文", []))


class TruncatedEngineBTests(unittest.TestCase):
    def run_page(self, draft, draft2, b_record, writer="甲乙丙丁戊己"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job, args = page(root, draft, draft2)
            if b_record is not None:
                (root / "b.json").write_text(json.dumps(b_record), encoding="utf-8")
            client = Fake(writer=writer, format=lambda u, n: between(u))
            record, text = run(job, args, client)
        return record, text, client

    def test_cut_off_draft_is_not_merged_and_the_page_is_transcribed(self):
        record, text, client = self.run_page("甲乙丙丁戊己", "甲乙丙丙丙丙",
                                             {"truncated": True, "finishReason": "length"})
        self.assertEqual(record["mode"], "transcribe")
        self.assertEqual(client.kinds(), ["writer"])
        hint = client.calls[0][1]
        self.assertIn("甲乙丙丁戊己", hint)
        self.assertNotIn("丙丙丙丙", hint)
        self.assertNotIn("底稿 B", hint)
        doubts = [d for d in record["doubts"] if d["kind"] == "engine-b-truncated"]
        self.assertEqual(len(doubts), 1)
        self.assertIn("finish reason length", doubts[0]["detail"])
        self.assertFalse(record["auditorClean"])
        self.assertEqual(text.strip(), "甲乙丙丁戊己")

    def test_cut_off_draft_is_cut_at_its_loop_and_merged(self):
        # Bench nlc-finance pp.4-6: engine B's reply read the page's text,
        # then looped until its tokens ran out.  What it read before the loop
        # is merged as the second reading (the page used to be transcribed
        # from engine A's draft alone, with no table path and no stitch);
        # the text past the cut is engine A's alone, and a doubt names the cut.
        body = "臣查此等字跡任承恩既經搜出即應隨折進呈今移送臣處"
        record, text, client = self.run_page(body + "謹奏", body + "口" * 1200,
                                             {"truncated": True, "finishReason": "length"})
        self.assertEqual(record["mode"], "merge")
        self.assertNotIn("writer", client.kinds())
        self.assertEqual(record["engineCoverage"]["engineB"], len(body) + 1)
        doubts = [d for d in record["doubts"] if d["kind"] == "engine-b-truncated"]
        self.assertEqual(len(doubts), 1)
        detail = doubts[0]["detail"]
        self.assertIn("finish reason length", detail)
        self.assertIn(f"from character {len(body)} on it wrote 「口」 1200 times over", detail)
        self.assertIn(f"merged up to there ({len(body) + 1} of its {len(body) + 1200} characters", detail)
        self.assertFalse(record["auditorClean"])
        self.assertEqual(pp.CJK(text), body + "謹奏")
        self.assertNotIn("口口", text)

    def test_cut_off_draft_with_no_loop_is_merged_as_far_as_it_goes(self):
        body = "臣查此等字跡任承恩既經搜出即應隨折進呈"
        record, text, client = self.run_page(body + "今移送臣處", body, {"truncated": True, "finishReason": "length"})
        self.assertEqual(record["mode"], "merge")
        detail = [d for d in record["doubts"] if d["kind"] == "engine-b-truncated"][0]["detail"]
        self.assertIn("(finish reason length); its reading is merged as far as it goes (19 characters), so what "
                      "the page holds past that is engine A's reading alone", detail)
        self.assertEqual(pp.CJK(text), body + "今移送臣處")

    def test_cut_off_draft_that_loops_from_its_start_is_transcribed(self):
        # Nothing usable left before the loop: engine B is treated as missing.
        body = "臣查此等字跡任承恩既經搜出即應隨折進呈"
        record, text, client = self.run_page(body, "口" * 1200, {"truncated": True, "finishReason": "length"},
                                             writer=body)
        self.assertEqual(record["mode"], "transcribe")
        self.assertEqual(client.kinds(), ["writer"])
        detail = [d for d in record["doubts"] if d["kind"] == "engine-b-truncated"][0]["detail"]
        self.assertIn("leaving 1 of its 1200 characters, so it is not merged", detail)
        self.assertEqual(text.strip(), body)

    def test_loops_are_cut_as_the_draft_cuts_them_save_a_table_rows_run_up(self):
        # Prose: the unit once, as build_draft.cut_loops keeps it.
        self.assertEqual(pp.cut_engine_b_loops("正文甲乙" + "口" * 1200)[0], "正文甲乙口")
        # A table opened on the loop's line keeps its closed rows, closed;
        # the row the loop is in is its run-up (nlc-finance p.4: 「十 九」
        # 「○二 ○ ○ …」 after the page's whole table).
        whole = "<table><tr><td>九年度</td></tr></table>"
        kept, loops = pp.cut_engine_b_loops(
            whole + "\n<table><tr><td>甲</td></tr><tr><td>十 九</td><td>○二" + " ○" * 1200)
        self.assertEqual(kept, whole + "\n<table><tr><td>甲</td></tr></table>")
        self.assertEqual(len(loops), 1)
        kept, _ = pp.cut_engine_b_loops(whole + "\n<table><caption></caption><tr><td>十 九</td><td>○二" + " ○" * 1200)
        self.assertEqual(kept, whole)
        # Text after a loop is the page's, as in the draft.
        self.assertEqual(pp.cut_engine_b_loops("甲" + "乙丙" * 600 + "\n丁戊")[0], "甲乙丙\n丁戊")
        # No loop, nothing cut; and build_draft finds the same loops.
        self.assertEqual(pp.cut_engine_b_loops("甲乙丙"), ("甲乙丙", []))
        import build_draft
        self.assertIs(build_draft.find_loops, pp.find_loops)

    def test_whole_or_unrecorded_drafts_are_merged_as_before(self):
        for b_record in ({"truncated": False, "finishReason": "stop"}, None, {"finishReason": "length"}):
            with self.subTest(b_record=b_record):
                record, text, client = self.run_page("甲乙丙丁戊己", "甲乙丙丁戊己", b_record)
                self.assertEqual(record["mode"], "merge")
                self.assertNotIn("writer", client.kinds())
                self.assertNotIn("engine-b-truncated", [d["kind"] for d in record["doubts"]])

    def test_cut_off_reading_keeps_a_page_engine_a_found_empty_from_being_sealed_blank(self):
        record, text, client = self.run_page("", "甲乙丙丁", {"truncated": True, "finishReason": "length"},
                                             writer="甲乙丙丁")
        self.assertEqual(record["mode"], "transcribe")
        self.assertEqual(client.kinds(), ["writer"])
        self.assertEqual(text.strip(), "甲乙丙丁")
        self.assertIn("engine-b-truncated", [d["kind"] for d in record["doubts"]])
        # Both engines silent is still a blank page with no call.
        record, text, client = self.run_page("", "", {"truncated": True, "finishReason": "length"})
        self.assertEqual((record["mode"], client.calls), ("blank-page", []))


class Verdict:
    """裁決 answering every question with ANSWER."""

    def __init__(self, answer):
        self.answer = answer

    def ask_answering(self, system, user_text, image, max_tokens, kind="other", **_):
        return f"構件：略\n定案：{self.answer}\nRESEARCH: no", {}


def judge(a, b, answer, cut_off=()):
    piece, record, _ = pp.adjudicate_segment(Verdict(answer), None,
                                             {"a": a, "b": b, "before": "見所著共人", "after": "其書甚詳",
                                              "cutOffSides": list(cut_off)}, 16000)
    return piece, record["resolved_from"], record.get("cutOffReadingAnswered")


# Made-up readings: a run engine B alone read (a label column read as a grid of
# doubled cells), and a line both engines read, engine B with three more.
GRID = "項目甲費乙乙費丙丙費丁"
BOTH, LONGER = "於今日之中國", "於今日之中國姑從畧"


class CutOffDeletionGuardTests(unittest.TestCase):
    """D16 (the answer-deletion guard) keeps no reading only an engine whose
    reply for the page was cut off made; text both engines read stays kept."""

    def test_nothing_printed_answers_away_a_reading_only_the_cut_off_engine_made(self):
        self.assertEqual(judge("", GRID, "無", cut_off=["b"]), ("", "adjudicator-empty", "B"))
        # A third reading shorter than it is written too.
        self.assertEqual(judge("", GRID, "項目", cut_off=["b"]), ("項目", "adjudicator", "B"))
        # With engine B's reply whole, the guard keeps its reading, as before.
        self.assertEqual(judge("", GRID, "無"), (GRID, "adjudicator-deletion-refused", None))

    def test_text_both_engines_read_is_still_kept_on_a_cut_off_page(self):
        # The answer leaves out more than two characters engine A read too:
        # engine A's reading is kept, without what engine B alone read.
        self.assertEqual(judge(BOTH, LONGER, "國", cut_off=["b"]), (BOTH, "adjudicator-deletion-refused", None))
        # Engine B whole: its longer reading, as before.
        self.assertEqual(judge(BOTH, LONGER, "國"), (LONGER, "adjudicator-deletion-refused", None))
        # A reading only engine A made is kept over a part of it, cut-off page or not.
        for cut_off in ((), ["b"]):
            with self.subTest(cut_off=cut_off):
                self.assertEqual(judge("用敎授要", "", "敎", cut_off), ("用敎授要", "adjudicator-deletion-refused", None))
        # Two characters left out stay within the guard's limit either way.
        self.assertEqual(judge("", "甲乙", "無", cut_off=["b"]), ("", "adjudicator-empty", None))

    def test_a_whole_reading_that_shares_nothing_with_the_answer_is_not_kept_over_it(self):
        # FX3 review F3 (probe p12_guard.py): engine A read the spot as other
        # text, and 裁決 answers part of engine B's reading, which was cut off.
        # The answer is a view of engine B's reading; engine A's shares no
        # character with it, so it is no reading of what 裁決 answered: the
        # answer is written, and its record names the cut-off engine and the
        # whole reading it passed over.
        piece, record, _ = pp.adjudicate_segment(Verdict("子丑"), None, {
            "a": "甲乙丙丁戊", "b": "子丑寅卯辰巳", "before": "見所著共人", "after": "其書甚詳",
            "cutOffSides": ["b"]}, 16000)
        self.assertEqual((piece, record["resolved_from"], record.get("cutOffReadingAnswered")),
                         ("子丑", "adjudicator", "B"))
        self.assertEqual(record["unsharedWholeReadings"], ["A"])
        # With engine B's reply whole, the guard keeps its reading, as before.
        self.assertEqual(judge("甲乙丙丁戊", "子丑寅卯辰巳", "子丑"),
                         ("子丑寅卯辰巳", "adjudicator-deletion-refused", None))
        # Engine A's reading sharing a character with the answer is kept.
        self.assertEqual(judge("甲乙丙丁子", "子丑寅卯辰巳", "子丑", cut_off=["b"]),
                         ("甲乙丙丁子", "adjudicator-deletion-refused", None))

    def page(self, cut_off):
        """A page where engine B read GRID inside a line engine A read whole,
        and 裁決 answers that nothing is printed there; engine B's reply ran
        into a loop of 〇 when CUT_OFF, and ended when not."""
        # Long enough that the run engine B alone read leaves its order with
        # engine A's (merge, not merge-order-conflict).
        body = "萬國博覽會開會末有日本人設人類館列亞洲各地人民之風俗器物以供衆覽焉其意蓋欲示人以文明之等差也"
        loop = "〇" * 1200 if cut_off else ""
        model = stand_in.StandIn(census=0, judge=lambda a, b: "無")
        with stand_in.book({1: ([stand_in.block(body, [0.3, 0.1, 0.4, 0.8])], body[:8] + GRID + body[8:] + loop)},
                           profile={"preferEngine": "adjudicate"}) as root:
            if cut_off:
                (root / "b" / "page-0001.json").write_text(
                    json.dumps({"truncated": True, "finishReason": "length"}), encoding="utf-8")
            record, markdown = stand_in.run(root, model)[1]
        return body, record, markdown

    def test_a_cut_off_page_answers_engine_b_alone_away_with_a_sealed_d16_change(self):
        body, record, markdown = self.page(cut_off=True)
        self.assertEqual(pp.CJK(markdown), body)
        self.assertIn("engine-b-truncated", [d["kind"] for d in record["doubts"]])
        self.assertNotIn("deletion-refused", [d["kind"] for d in record["doubts"]])
        self.assertIn(("", GRID, "", "adjudicator-empty"),
                      [(a["draft_reading"], a["writer_reading"], a["resolved"], a["resolved_from"])
                       for a in record["adjudications"]])
        self.assertEqual([(c["rule"], c["after"], c["mergedOffset"], c["evidence"])
                          for c in record["decisionChanges"] if c["decision"] == "D16"],
                         [("answer-accepted-cut-off-reading", "", 8,
                           {"cutOffEngine": "B", "readingCharacters": len(GRID), "answeredCharacters": 0,
                            "limit": pp.ANSWER_DELETION_LIMIT})])
        # The page holds the reading nowhere else: a person looks.
        self.assertIn("reading-dropped", [d["kind"] for d in record["doubts"]])

    def test_the_same_page_with_engine_b_whole_keeps_its_reading(self):
        body, record, markdown = self.page(cut_off=False)
        self.assertEqual(pp.CJK(markdown), body[:8] + GRID + body[8:])
        self.assertEqual([d["kind"] for d in record["doubts"]], ["deletion-refused"])
        self.assertEqual([c["rule"] for c in record["decisionChanges"] if c["decision"] == "D16"],
                         ["answer-deletion-guard"])


class LearnedHeadsTests(unittest.TestCase):
    def test_a_label_is_learned_from_three_pages_even_in_a_two_page_book(self):
        # The made-up book of check_learned_heads: 第二冊 stands beside the
        # stem in the head's band on pages 12, 13 and 14.
        import check_learned_heads as book
        blocks = {page: [dict(b, box=b["boundingBox"], label=b["blockLabel"]) for b in book.PAGES[page][0]]
                  for page in book.PAGES}
        three = pp.learn_heads({page: blocks[page] for page in (12, 13, 14)}, [book.HEAD])
        self.assertEqual(three["labels"], {"第二冊": 3})
        two = pp.learn_heads({page: blocks[page] for page in (12, 13)}, [book.HEAD])
        self.assertEqual((two["pagesWithBand"], two["labels"], two["regions"]), (2, {}, []))


if __name__ == "__main__":
    unittest.main()
