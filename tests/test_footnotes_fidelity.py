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

import _footnotes  # noqa: E402
import _note_markers as nm  # noqa: E402
import _repair_runs as runs  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import finalize_page_markers  # noqa: E402


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

    def test_cross_page_orphan_lookback_pairs_call_with_next_page_note(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "頁末句帶跨頁註號⑦，尚未完\n\n"
            "<!-- page_002 -->\n\n"
            "接續上頁正文句。\n\n"
            "---\n\n"
            "⑦ 印喺下一頁頁底嘅跨頁注文。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 1)
        self.assertEqual(report["rulesRemoved"], 1)
        self.assertEqual(report["left"], [])
        self.assertIn("頁末句帶跨頁註號[^7]，尚未完", out)
        self.assertIn("[^7]: 印喺下一頁頁底嘅跨頁注文。", out)

    def test_duplicate_per_page_label_kept_as_printed_unless_renumbered(self) -> None:
        source = (
            "<!-- page_001 -->\n\n"
            "甲頁正文①。\n\n"
            "① 甲頁注文。\n\n"
            "<!-- page_002 -->\n\n"
            "乙頁正文①。\n\n"
            "① 乙頁注文。\n"
        )
        out_default, report_default = _footnotes.convert(source, renumber=False)
        self.assertEqual(report_default["footnotes"], 1)
        self.assertIn("甲頁正文[^1]。", out_default)
        self.assertIn("[^1]: 甲頁注文。", out_default)
        self.assertIn("乙頁正文①。", out_default)
        self.assertIn("① 乙頁注文。", out_default)
        self.assertEqual(len(report_default["left"]), 1)
        self.assertEqual(report_default["left"][0]["page"], 2)
        self.assertEqual(report_default["left"][0]["why"][0]["kind"], "duplicate_label")

        out_renum, report_renum = _footnotes.convert(source, renumber=True)
        self.assertEqual(report_renum["footnotes"], 2)
        self.assertEqual(report_renum["left"], [])
        self.assertIn("甲頁正文[^1]。", out_renum)
        self.assertIn("乙頁正文[^2]。", out_renum)
        self.assertIn("[^2]: 乙頁注文。", out_renum)

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

    def test_cross_page_wrapped_label_and_duplicates_keep_existing_policy(self) -> None:
        source = (
            "<!-- page_001 -->\n\n正文" + r"$^{\textcircled{１２}}$" + "。\n\n"
            "<!-- page_002 -->\n\n" + "<sup>１２</sup> 次頁原註。\n\n"
            "<!-- page_003 -->\n\n第二文" + r"$^{\textcircled{１２}}$" + "。\n\n"
            "<sup>１２</sup> 第三頁原註。\n"
        )
        out, report = _footnotes.convert(source)
        self.assertEqual(report["footnotes"], 1)
        self.assertIn("[^12]: 次頁原註。", out)
        self.assertIn("<sup>１２</sup> 第三頁原註。", out)
        self.assertTrue(any(x["kind"] == "duplicate_label" for page in report["left"] for x in page["why"]))
        out, report = _footnotes.convert(source, renumber=True)
        self.assertEqual(report["footnotes"], 2)
        self.assertEqual(report["left"], [])
        self.assertIn("[^2]: 第三頁原註。", out)

    def test_duplicate_wrapped_labels_on_same_page_do_not_merge(self) -> None:
        source = "<!-- page_001 -->\n\n甲<sup>１２</sup>，乙<sup>１２</sup>。\n\n<sup>１２</sup> 甲註。\n<sup>１２</sup> 乙註。\n"
        out, report = _footnotes.convert(source)
        self.assertEqual(out, source)
        self.assertEqual(report["footnotes"], 0)
        self.assertTrue(report["left"])

    def test_run_tokenizer_accepts_only_complete_numeric_wrappers(self) -> None:
        valid = r"$^{\textcircled{２４０}}$"
        token = runs.tokens(valid, "a")[0]
        self.assertEqual((token.kind, token.cls), (runs.MARKER, "240"))
        self.assertIsNone(runs._call(token), "parsing a multi-digit wrapper must not broaden automatic restoration")
        for raw in [r"$x^{①}$", r"$12$", r"$^{\alpha2}$", "<sup>a2</sup>", r"x^{2}", "π²", "α^{2}", "x⁻²"]:
            with self.subTest(raw=raw):
                self.assertNotIn(runs.MARKER, [token.kind for token in runs.tokens(raw, "a")])

    def test_oversized_malformed_wrappers_are_bounded_and_fail_closed(self) -> None:
        # A timeout isolates a potential parser hang instead of blocking the
        # whole test process.  This is a generous safety bound, not a benchmark.
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import _note_markers as nm; "
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
