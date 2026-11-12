from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    REPO_ROOT / "plugins" / "jyut-ocr-proofread" / "skills" / "jyut-ocr-proofread" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _footnotes  # noqa: E402
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
