from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    REPO_ROOT / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from jyut_ocr.finish import footnotes as _footnotes  # noqa: E402
from jyut_ocr.core import note_markers as nm  # noqa: E402
from jyut_ocr.core import ocr_markdown as om  # noqa: E402
import _repair_runs as runs  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import finalize_page_markers  # noqa: E402
import finish_book  # noqa: E402


class FootnotesFidelityTests(unittest.TestCase):
    def test_preserves_printed_numeric_labels_by_default_and_renumbers_on_opt_in(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "第一段正文引文②④⓪，以及第二處⑤²。\n\n"
            "第二段正文內容。\n\n"
            "---\n\n"
            "②④⓪ 檔案原註二百四十號。\n"
            "⑤² 檔案原註五十二號。\n"
        )
        out_default, report_default = _footnotes.convert(source)
        self.assertFalse(report_default["renumbered"])
        self.assertEqual(report_default["footnotes"], 2)
        self.assertEqual(report_default["rulesRemoved"], 1)
        self.assertEqual(report_default["left"], [])
        self.assertIn("第一段正文引文[^240]，以及第二處[^52]。", out_default)
        self.assertIn("[^240]: 檔案原註二百四十號。", out_default)
        self.assertIn("[^52]: 檔案原註五十二號。", out_default)
        self.assertNotIn("\n---\n", out_default)

        out_renum, report_renum = _footnotes.convert(source, renumber=True)
        self.assertTrue(report_renum["renumbered"])
        self.assertEqual(report_renum["footnotes"], 2)
        self.assertIn("第一段正文引文[^1]，以及第二處[^2]。", out_renum)
        self.assertIn("[^1]: 檔案原註二百四十號。", out_renum)
        self.assertIn("[^2]: 檔案原註五十二號。", out_renum)

    def test_greedy_partial_pairing_converts_valid_pairs_despite_unpaired_or_html_items(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "正文有配對註①，另有孤立註號②。\n\n"
            "<table><tr><td>表內格③</td></tr></table>\n\n"
            "---\n\n"
            "① 已配對注文。\n"
            "④ 未配對注文。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 1)
        # Rule stays because ④ remains an unconverted note at the foot of the page.
        self.assertEqual(report["rulesRemoved"], 0)
        self.assertIn("正文有配對註[^1]，另有孤立註號②。", out)
        self.assertIn("<td>表內格③</td>", out)
        self.assertIn("[^1]: 已配對注文。", out)
        self.assertIn("④ 未配對注文。", out)
        self.assertEqual(len(report["left"]), 1)
        kinds = {item["kind"] for item in report["left"][0]["why"]}
        self.assertEqual(kinds, {"call", "note", "html"})

    def test_note_running_over_a_page_break_pairs_when_the_numbering_runs_on(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "前段⑥完。\n\n"
            "頁末句帶跨頁註號⑦，尚未完\n\n"
            "---\n\n"
            "⑥ 本頁注文。\n\n"
            "<!-- page_002 -->\n\n"
            "接續上頁正文句⑧。\n\n"
            "---\n\n"
            "⑦ 印喺下一頁頁底嘅跨頁注文。\n"
            "⑧ 下一頁自己嘅注文。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 3)
        self.assertEqual(report["rulesRemoved"], 2)
        self.assertEqual(report["left"], [])
        self.assertEqual(report["crossPage"], [{"callPage": 1, "notePage": 2, "label": "7"}])
        self.assertIn("頁末句帶跨頁註號[^7]，尚未完", out)
        self.assertIn("[^7]: 印喺下一頁頁底嘅跨頁注文。", out)
        self.assertIn("[^8]: 下一頁自己嘅注文。", out)
        # The call before it may be on an earlier page: numbering through the book.
        source = (
            "<!-- page_001 -->\n\n甲⑥。\n\n⑥ 六。\n\n"
            "<!-- page_002 -->\n\n頁末⑦，未完\n\n"
            "<!-- page_003 -->\n\n接續。\n\n⑦ 跨頁注文。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["crossPage"], [{"callPage": 2, "notePage": 3, "label": "7"}])
        self.assertIn("頁末[^7]，未完", out)
        self.assertIn("[^7]: 跨頁注文。", out)

    def test_cross_page_orphans_stay_as_printed_unless_clearly_one_note(self) -> None:
        cases = {
            # The numbering does not run on: no call numbered 6 before the 7.
            "no previous number": (
                "<!-- page_001 -->\n\n頁末句⑦，尚未完\n\n<!-- page_002 -->\n\n接續。\n\n⑦ 跨頁注文。\n"
            ),
            # A note numbered 1 starts a page's (or a chapter's) numbering:
            # here page 2 lost the note of its call and page 3 the call of its note.
            "a restart": (
                "<!-- page_001 -->\n\n甲①。\n\n①一。\n\n<!-- page_002 -->\n\n乙①丙。\n\n"
                "<!-- page_003 -->\n\n丁戊。\n\n①丁嘅註。\n"
            ),
            # The note is not the first of the next page's notes.
            "not at the head": (
                "<!-- page_001 -->\n\n甲①。\n\n①一。\n\n<!-- page_002 -->\n\n乙②。\n\n"
                "<!-- page_003 -->\n\n丙③。\n\n③三。\n②二。\n"
            ),
            # The call comes before a call whose note its own page prints.
            "not the last call": (
                "<!-- page_001 -->\n\n甲①乙②丙③。\n\n①一。\n③三。\n\n"
                "<!-- page_002 -->\n\n丁。\n\n②二。\n"
            ),
            # Two pages apart.
            "not the next page": (
                "<!-- page_001 -->\n\n甲①乙②。\n\n①一。\n\n<!-- page_002 -->\n\n丙。\n\n"
                "<!-- page_003 -->\n\n丁。\n\n②二。\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name):
                out, report = _footnotes.convert(source)
                self.assertEqual(report["crossPage"], [])
                kinds = sorted(why["kind"] for page in report["left"] for why in page["why"])
                self.assertEqual(kinds, ["call", "note"])
        # Each half stays as printed; nothing is made up for the missing one.
        out, report = _footnotes.convert(cases["no previous number"])
        self.assertEqual(out, cases["no previous number"])
        self.assertEqual(
            [(page["page"], [why["kind"] for why in page["why"]]) for page in report["left"]],
            [(1, ["call"]), (2, ["note"])],
        )

    def test_printed_label_used_again_on_a_later_page_gets_a_distinct_id(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "甲頁正文①。\n\n"
            "① 甲頁注文。\n\n"
            "<!-- page_002 -->\n\n"
            "乙頁正文①。\n\n"
            "① 乙頁注文。\n\n"
            "<!-- page_003 -->\n\n"
            "丙頁正文①，又②。\n\n"
            "① 丙頁注文。\n"
            "② 丙頁第二條。\n"
        )
        out_default, report_default = _footnotes.convert(source, renumber=False)
        self.assertEqual(report_default["footnotes"], 4)
        self.assertEqual(report_default["relabelled"], 2)
        self.assertEqual(report_default["left"], [])
        self.assertIn("甲頁正文[^1]。", out_default)
        self.assertIn("[^1]: 甲頁注文。", out_default)
        self.assertIn("乙頁正文[^1-2]。", out_default)
        self.assertIn("[^1-2]: 乙頁注文。", out_default)
        self.assertIn("丙頁正文[^1-3]，又[^2]。", out_default)
        self.assertIn("[^1-3]: 丙頁注文。", out_default)
        self.assertIn("[^2]: 丙頁第二條。", out_default)

        out_renum, report_renum = _footnotes.convert(source, renumber=True)
        self.assertEqual(report_renum["footnotes"], 4)
        self.assertEqual(report_renum["left"], [])
        self.assertIn("甲頁正文[^1]。", out_renum)
        self.assertIn("乙頁正文[^2]。", out_renum)
        self.assertIn("[^2]: 乙頁注文。", out_renum)
        self.assertIn("丙頁正文[^3]，又[^4]。", out_renum)

    def test_two_notes_of_one_number_on_a_page_pair_in_order_with_distinct_ids(self) -> None:
        source = (
            "<!-- page_001 -->\n\n甲①，乙①。\n\n---\n\n① 甲註。\n① 乙註。\n\n"
            "<!-- page_002 -->\n\n丙①。\n\n① 丙註。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 3)
        self.assertEqual(report["left"], [])
        self.assertEqual(report["rulesRemoved"], 1)
        self.assertIn("甲[^1]，乙[^1-2]。", out)
        self.assertIn("[^1]: 甲註。\n[^1-2]: 乙註。\n", out)
        self.assertIn("丙[^1-3]。\n\n[^1-3]: 丙註。", out)
        wrapped = "<!-- page_001 -->\n\n甲<sup>１２</sup>，乙<sup>１２</sup>。\n\n<sup>１２</sup> 甲註。\n<sup>１２</sup> 乙註。\n"
        out, report = _footnotes.convert(wrapped)
        self.assertEqual(out, "<!-- page_001 -->\n\n甲[^12]，乙[^12-2]。\n\n[^12]: 甲註。\n[^12-2]: 乙註。\n")
        self.assertEqual(report["left"], [])

    def test_labels_never_collide_with_footnote_syntax_the_text_holds(self) -> None:
        # An engine's [^2] whose note is lost keeps its label: a later
        # note numbered 2 is [^2-2]; a [^1-2] already there is skipped too.
        source = (
            "<!-- page_001 -->\n\n甲[^1]乙[^2]丙[^1-2]。\n\n[^1]: 印咗嘅註。\n\n"
            "<!-- page_002 -->\n\n丁①戊②。\n\n①註一。\n②註二。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 3)
        self.assertIn("甲[^1]乙[^2]丙[^1-2]。\n\n[^1]: 印咗嘅註。", out)
        self.assertIn("丁[^1-3]戊[^2-2]。\n\n[^1-3]: 註一。\n[^2-2]: 註二。", out)
        self.assertEqual(
            [(page["page"], [why["kind"] for why in page["why"]]) for page in report["left"]], [(1, ["call"])]
        )
        # A note citing another note of its page keeps the shared label.
        inside = "<!-- page_001 -->\n\n乙[^1]丙[^2]。\n\n[^1]: 註甲。\n\n[^2]: 同[^1]。\n"
        out, report = _footnotes.convert(inside)
        self.assertEqual(out, inside)
        self.assertEqual([why["kind"] for why in report["left"][0]["why"]], ["inside"])
        # So does a printed note this step converts.
        paired = "<!-- page_001 -->\n\n甲①乙②。\n\n① 一。\n② 見[^1]。\n"
        out, report = _footnotes.convert(paired)
        self.assertEqual(out, "<!-- page_001 -->\n\n甲[^1]乙[^2]。\n\n[^1]: 一。\n[^2]: 見[^1]。\n")
        self.assertEqual(report["relabelled"], 0)

    def test_a_note_left_as_printed_that_cites_a_label_takes_it(self) -> None:
        # A note with no call stays a printed line, so the [^1] in its text
        # is a reference like any other: the page's own note 1 must not take
        # label 1 (it was written twice, and convert raised AssertionError).
        cases = {
            "same page": (
                "<!-- page_001 -->\n\n甲①。\n\n① 一。\n② 見[^1]。\n",
                "<!-- page_001 -->\n\n甲[^1-2]。\n\n[^1-2]: 一。\n② 見[^1]。\n",
            ),
            # The call page of a note that runs over holds the citing note.
            "across a page": (
                "<!-- page_001 -->\n\n甲①乙②。\n\n① 一。\n③ 見[^2]。\n\n<!-- page_002 -->\n\n丙。\n\n② 二。\n",
                "<!-- page_001 -->\n\n甲[^1]乙[^2-2]。\n\n[^1]: 一。\n③ 見[^2]。\n\n<!-- page_002 -->\n\n丙。\n\n[^2-2]: 二。\n",
            ),
        }
        for name, (source, want) in cases.items():
            with self.subTest(name):
                out, report = _footnotes.convert(source)
                self.assertEqual(out, want)
                self.assertEqual(report["relabelled"], 1)
                kinds = [why["kind"] for page in report["left"] for why in page["why"]]
                self.assertEqual(kinds, ["note", "inside"])

    def test_join_runs_past_relabelled_definitions_at_the_page_foot(self) -> None:
        source = (
            "<!-- page_001 -->\n\n甲①完。\n\n①一。\n\n"
            "<!-- page_002 -->\n\n後段未完，跨頁①\n\n---\n\n①二。\n\n"
            "<!-- page_003 -->\n\n續完。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 2)
        final = om.apply_boundary_contract(out, {1: "start", 2: "paragraph", 3: "join"})
        self.assertEqual(final, "甲[^1]完。\n\n[^1]: 一。\n\n後段未完，跨頁[^1-2]續完。\n\n[^1-2]: 二。\n")

    def test_note_items_survive_a_reason_without_text(self) -> None:
        # The KeyError 'text' of an earlier footnotes.json (two notes of one
        # number on a page): the doubt is still written.
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp)
            (book / finish_book.FINISH_DIR).mkdir()
            (book / finish_book.FINISH_DIR / finish_book.FOOTNOTES_JSON).write_text(
                json.dumps({"left": [{"page": 3, "why": [
                    {"kind": "duplicate_label", "number": "1", "calls": 2, "notes": 2},
                    {"kind": "call", "number": "②", "text": "甲②乙"},
                ]}]}),
                encoding="utf-8",
            )
            items = finish_book.note_items(book)
        self.assertEqual([(item.kind, item.page) for item in items], [("note-unmatched", 3)])
        self.assertIn("註號 1（「…?…」）", items[0].detail)
        self.assertIn("註號 ②（「…甲②乙…」）冇同號嘅註", items[0].detail)

    def test_numeric_wrappers_preserve_printed_identity(self) -> None:
        cases = [
            (r"$^{\textcircled{１２}}$", "12"),
            (r"${}^{\textcircled{１２}}$", "12"),
            (r"${{}^{\textcircled{１２}}}$", "12"),
            (r"$^{{{１２}}}$", "12"),
            (r"$^{\textcircled{2}\textcircled{4}\textcircled{0}}$", "240"),
            (r"$^{2}$$^{4}$⓪", "240"),
            ("<SUP>０１２</SUP>", "012"),
            ("[^１２]", "12"),
            ("❶⓪", "10"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(nm.marker_key(raw), expected)
                source = f"<!-- page_001 -->\n\n正文{raw}。\n\n{raw} 原註。\n"
                out, report = _footnotes.convert(source)
                self.assertEqual(report["footnotes"], 1)
                self.assertEqual(report["left"], [])
                self.assertIn(f"正文[^{expected}]。", out)
                self.assertIn(f"[^{expected}]: 原註。", out)

    def test_math_and_ordinary_enumeration_are_not_promoted_to_notes(self) -> None:
        for raw in [r"$x^{2}$", r"$x^{①}$", r"$12$", "12", "１２", "(12)", "x²", "x^{2}", "x<sup>2</sup>"]:
            with self.subTest(raw=raw):
                source = f"<!-- page_001 -->\n\n算式{raw}。\n\n② 第二項說明。\n"
                out, report = _footnotes.convert(source)
                self.assertEqual(out, source)
                self.assertEqual(report["footnotes"], 0)
        source = "<!-- page_001 -->\n\n１. 第一項\n２. 第二項\n\n12. 第十二項\n"
        self.assertEqual(_footnotes.convert(source)[0], source)

    def test_unicode_math_bases_and_signed_exponents_stay_unconverted(self) -> None:
        for raw in [
            "π²", "α^{2}", "β<sup>2</sup>", "я²", "א²", "é²", "𝑥²", "Ⅲ²", "٣²",
            "x\u0304²", "α\u0301^{2}", "x⁻²", "x⁺²", "x−²", "x-²", "x²+1",
            "①+②", "① − ②", "①×②", "①÷②",
        ]:
            with self.subTest(raw=raw):
                source = f"<!-- page_001 -->\n\n算式{raw}。\n\n② 第二項說明。\n"
                out, report = _footnotes.convert(source)
                self.assertEqual(out, source)
                self.assertEqual(report["footnotes"], 0)
                self.assertTrue(report["left"])
        # The same syntax after Han prose, including extended Han characters,
        # remains eligible for contextual pairing with a printed note.
        for body in ["正文²", "𠀀²", "正文^{2}", "正文<sup>2</sup>"]:
            with self.subTest(body=body):
                source = f"<!-- page_001 -->\n\n{body}。\n\n② 原註。\n"
                out, report = _footnotes.convert(source)
                self.assertEqual(report["footnotes"], 1)
                self.assertIn("[^2]。", out)

    def test_malformed_and_nonnumeric_wrappers_do_not_expose_inner_markers(self) -> None:
        for raw in [
            r"$^{\alpha2}$", r"$^{\textcircled{a②}}$", r"$^{\textcircled{2}}",
            r"^{\textcircled{2}", r"\textcircled{a②}", r"<sup>a②</sup>",
            r"$^{2+3}$", r"$^{\frac{1}{2}}$", r"$^{2}}$", r"$^{{2}$",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(nm.marker_key(raw), "")
                self.assertEqual(list(nm.iter_note_markers(raw)), [])
                source = f"<!-- page_001 -->\n\n正文{raw}。\n\n② 原註。\n"
                self.assertEqual(_footnotes.convert(source)[0], source)

    def test_unpaired_numeric_wrapper_stays_printed_and_is_reported(self) -> None:
        raw = r"${}^{\textcircled{２４０}}$"
        source = f"<!-- page_001 -->\n\n正文{raw}。\n"
        out, report = _footnotes.convert(source)
        self.assertEqual(out, source)
        self.assertEqual(report["footnotes"], 0)
        self.assertEqual(report["left"][0]["why"][0]["kind"], "call")
        self.assertEqual(report["left"][0]["why"][0]["number"], raw)

    def test_html_table_markers_remain_rendering_safe_and_unresolved(self) -> None:
        raw = r"$^{\textcircled{１２}}$"
        source = f"<!-- page_001 -->\n\n<table><tr><td>正文{raw}</td></tr></table>\n\n{raw} 原註。\n"
        out, report = _footnotes.convert(source)
        self.assertEqual(out, source)
        self.assertNotIn("[^", out)
        self.assertEqual(report["footnotes"], 0)
        self.assertEqual({x["kind"] for x in report["left"][0]["why"]}, {"html", "note"})

    def test_cross_page_wrapped_label_and_reused_wrapped_label(self) -> None:
        source = (
            "<!-- page_001 -->\n\n正文" + r"$^{\textcircled{１１}}$" + "，又" + r"$^{\textcircled{１２}}$" + "。\n\n"
            "<sup>１１</sup> 本頁原註。\n\n"
            "<!-- page_002 -->\n\n" + "<sup>１２</sup> 次頁原註。\n\n"
            "<!-- page_003 -->\n\n第二文" + r"$^{\textcircled{１２}}$" + "。\n\n"
            "<sup>１２</sup> 第三頁原註。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 3)
        self.assertEqual(report["left"], [])
        self.assertEqual(report["crossPage"], [{"callPage": 1, "notePage": 2, "label": "12"}])
        self.assertIn("正文[^11]，又[^12]。", out)
        self.assertIn("[^12]: 次頁原註。", out)
        self.assertIn("第二文[^12-2]。", out)
        self.assertIn("[^12-2]: 第三頁原註。", out)
        out, report = _footnotes.convert(source, renumber=True)
        self.assertEqual(report["footnotes"], 3)
        self.assertEqual(report["left"], [])
        self.assertIn("[^3]: 第三頁原註。", out)

    def test_run_tokenizer_accepts_only_complete_numeric_wrappers(self) -> None:
        valid = r"$^{\textcircled{２４０}}$"
        token = runs.tokens(valid, "a")[0]
        self.assertEqual((token.kind, token.cls), (runs.MARKER, "240"))
        self.assertIsNone(runs._call(token), "parsing a multi-digit wrapper must not broaden automatic restoration")
        # A figure alone in LaTeX is a note marker to the run stage, as the old
        # skill measured on engine A's drafts (check_repair_runs), but never a
        # call it writes by itself.
        token = runs.tokens(r"$12$", "a")[0]
        self.assertEqual((token.kind, token.cls), (runs.MARKER, "12"))
        self.assertIsNone(runs._call(token))
        for raw in [r"$x^{①}$", r"$^{\alpha2}$", "<sup>a2</sup>", r"x^{2}", "π²", "α^{2}", "x⁻²"]:
            with self.subTest(raw=raw):
                self.assertNotIn(runs.MARKER, [token.kind for token in runs.tokens(raw, "a")])

    def test_run_tokenizer_keeps_the_old_skills_note_marker_kinds(self) -> None:
        # The shapes the run stage's N1/N2 repairs rely on (check_repair_runs):
        # each wrapper is one note marker with its figures as its value.
        for raw, side, value in [
            ("$ 12 $", "a", "12"), ("$^{*}$", "a", "*"), ("$^{(1)}$", "a", "1"),
            ("<sub>2</sub>", "a", "2"), ("<sup>(1)</sup>", "a", "1"), ("$ ^{⑼} $", "b", "9"),
            ("\textcircled{9}", "b", "9"), ("[^1]", "a", "1"),
        ]:
            with self.subTest(raw=raw):
                kinds = [(t.kind, t.cls) for t in runs.tokens(raw, side) if t.kind != runs.SPACE]
                self.assertEqual(kinds, [(runs.MARKER, value)])
        # A call after a year, and a note's own number opening its line
        # before a dash, are calls, not formulas.
        self.assertIn((runs.MARKER, "1"), [(t.kind, t.cls) for t in runs.tokens("甲乙1990 $ ^{①} $丙", "a")])
        self.assertIn((runs.MARKER, "1"), [(t.kind, t.cls) for t in runs.tokens("甲乙1990①丙", "b")])
        self.assertEqual(runs.tokens("甲。\n⑦- Lin", "b")[3].kind, runs.MARKER)
        # LaTeX that holds no figure once its commands are out is markup.
        for raw, side in [(r"$ \Box\Box $", "page"), (r"$\quad$", "a")]:
            with self.subTest(raw=raw):
                self.assertEqual([t.kind for t in runs.tokens(raw, side)], [runs.MARKUP])
        # Formulas stay unknown markup: an operator beside, a command other
        # than \textcircled, a Latin base.
        for raw in ["①+②", r"$ 2 \times 3 $", "x$^{2}$", "H<sub>2</sub>O"]:
            with self.subTest(raw=raw):
                self.assertNotIn((runs.MARKER, "2"), [(t.kind, t.cls) for t in runs.tokens(raw, "a")])

    def test_oversized_malformed_wrappers_are_bounded_and_fail_closed(self) -> None:
        # A timeout isolates a potential parser hang instead of blocking the
        # whole test process.  This is a generous safety bound, not a benchmark.
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); from jyut_ocr.core import note_markers as nm; "
            "bad = ['${' + '{' * 20000 + '②' + '}' * 20000 + '$', '^{' + '1' * 20000 + '②']; "
            "assert all(nm.marker_key(x) == '' and list(nm.iter_note_markers(x)) == [] for x in bad)"
        )
        result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS_DIR)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_finalize_keeps_footnotes_at_page_foot_by_default_and_relocates_when_opted_in(self) -> None:
        annotated = (
            "<!-- page_001 -->\n\n"
            "首段含註號[^240]。\n\n"
            "次段正文必須留喺腳註之前。\n\n"
            "[^240]: 頁底原位腳註內容。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "annotated.md"
            manifest = root / "boundaries.json"
            out_keep = root / "final-keep.md"
            out_reloc = root / "final-reloc.md"
            src.write_text(annotated, encoding="utf-8")
            manifest.write_text('{"boundaries": {"1": "start"}}\n', encoding="utf-8")

            rc_keep = finalize_page_markers.main(
                [str(src), "--boundary-manifest", str(manifest), "--output", str(out_keep), "--write", "--summary-only"]
            )
            self.assertEqual(rc_keep, 0)
            text_keep = out_keep.read_text(encoding="utf-8")
            self.assertLess(
                text_keep.index("次段正文必須留喺腳註之前。"),
                text_keep.index("[^240]: 頁底原位腳註內容。"),
            )
            rc_audit_keep = audit_ocr_markdown.main(
                [
                    str(out_keep),
                    "--mode",
                    "final",
                    "--annotated-source",
                    str(src),
                    "--boundary-manifest",
                    str(manifest),
                    "--summary-only",
                ]
            )
            self.assertEqual(rc_audit_keep, 0)

            rc_reloc = finalize_page_markers.main(
                [
                    str(src),
                    "--boundary-manifest",
                    str(manifest),
                    "--output",
                    str(out_reloc),
                    "--write",
                    "--summary-only",
                    "--relocate-footnotes",
                ]
            )
            self.assertEqual(rc_reloc, 0)
            text_reloc = out_reloc.read_text(encoding="utf-8")
            self.assertLess(
                text_reloc.index("[^240]: 頁底原位腳註內容。"),
                text_reloc.index("次段正文必須留喺腳註之前。"),
            )
            rc_audit_reloc = audit_ocr_markdown.main(
                [
                    str(out_reloc),
                    "--mode",
                    "final",
                    "--annotated-source",
                    str(src),
                    "--boundary-manifest",
                    str(manifest),
                    "--summary-only",
                    "--relocate-footnotes",
                ]
            )
            self.assertEqual(rc_audit_reloc, 0)


if __name__ == "__main__":
    unittest.main()
