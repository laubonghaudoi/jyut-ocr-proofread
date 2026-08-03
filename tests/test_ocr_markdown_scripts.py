from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
)
AUDIT = SCRIPTS / "audit_ocr_markdown.py"
FINALIZE = SCRIPTS / "finalize_page_markers.py"


def run_script(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(argument) for argument in arguments)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class FinalizePageMarkersTests(unittest.TestCase):
    def test_join_break_and_list_line_boundaries(self) -> None:
        annotated = """<!-- page_001 -->
# 題名

前
<!-- page_002 -->
後

段一。

<!-- page_003 -->

段二。

- 第一項
  <!-- page_004 -->
  續文
"""
        expected = """# 題名

前後

段一。

段二。

- 第一項
  續文
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "final.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                json.dumps(
                    {
                        "boundaries": {
                            "1": "start",
                            "2": "join",
                            "3": "paragraph",
                            "4": "list-cont",
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = run_script(
                FINALIZE,
                target,
                "--expected-count",
                4,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--write",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)
            self.assertEqual(output.read_text(encoding="utf-8"), expected)
            self.assertIn("page=2 contract=join", result.stdout)
            self.assertIn("page=3 contract=paragraph", result.stdout)
            self.assertIn("page=4 contract=list-cont", result.stdout)

    def test_dry_run_never_writes_source_or_output(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "final.md"
            target.write_text(annotated, encoding="utf-8")

            dry_run = run_script(FINALIZE, target, "--output", output)

            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)
            self.assertFalse(output.exists())
            self.assertIn("DRY-RUN", dry_run.stdout)
            self.assertIn("boundaries=UNRESOLVED", dry_run.stdout)
            self.assertIn("written=no", dry_run.stdout)

            refused = run_script(FINALIZE, target, "--output", output, "--write")
            self.assertEqual(refused.returncode, 1)
            self.assertIn("--write requires --boundary-manifest", refused.stderr)
            self.assertFalse(output.exists())

            manifest = Path(temporary_directory) / "boundaries.json"
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            written = run_script(
                FINALIZE,
                target,
                "--output",
                output,
                "--boundary-manifest",
                manifest,
                "--write",
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)
            self.assertEqual(output.read_text(encoding="utf-8"), "正文。\n")

    def test_contract_overrides_surrounding_blank_lines(self) -> None:
        compact = "前\n<!-- page_001 -->\n後\n"
        padded = "前\n\n\n<!-- page_001 -->\n\n後\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest = Path(temporary_directory) / "boundaries.json"
            manifest.write_text(
                '{"boundaries":{"1":"join"}}', encoding="utf-8"
            )
            outputs: list[str] = []
            for index, source in enumerate((compact, padded)):
                target = Path(temporary_directory) / f"annotated-{index}.md"
                output = Path(temporary_directory) / f"final-{index}.md"
                target.write_text(source, encoding="utf-8")
                result = run_script(
                    FINALIZE,
                    target,
                    "--boundary-manifest",
                    manifest,
                    "--output",
                    output,
                    "--write",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.read_text(encoding="utf-8"), source)
                outputs.append(output.read_text(encoding="utf-8"))
            self.assertEqual(outputs, ["前後\n", "前後\n"])

    def test_write_refuses_in_place_output(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                target,
                "--write",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("--output must differ", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)

    def test_duplicate_manifest_record_is_rejected(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","1":"paragraph"}}',
                encoding="utf-8",
            )

            result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--write",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate boundary record", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)

    def test_manifest_root_array_is_rejected(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '[["boundaries", {"1": "start"}]]', encoding="utf-8"
            )

            result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--write",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("root must be a JSON object", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)

    def test_missing_and_extra_manifest_records_are_rejected(self) -> None:
        annotated = "<!-- page_001 -->\n前\n<!-- page_002 -->\n後\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            target.write_text(annotated, encoding="utf-8")
            cases = (
                ('{"boundaries":{"1":"start"}}', "missing boundary records: 2"),
                (
                    '{"boundaries":{"1":"start","2":"join","3":"paragraph"}}',
                    "extra boundary records: 3",
                ),
            )
            for index, (manifest_text, expected_error) in enumerate(cases):
                with self.subTest(expected_error=expected_error):
                    manifest = Path(temporary_directory) / f"boundaries-{index}.json"
                    manifest.write_text(manifest_text, encoding="utf-8")
                    result = run_script(
                        FINALIZE,
                        target,
                        "--boundary-manifest",
                        manifest,
                        "--write",
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected_error, result.stderr)
                    self.assertEqual(target.read_text(encoding="utf-8"), annotated)


class AuditOcrMarkdownTests(unittest.TestCase):
    def test_page_zero_marker_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            target.write_text("<!-- page_000 -->\n正文。\n", encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "annotated")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[malformed-page-marker]", result.stdout)

    def test_frontmatter_brackets_are_not_low_confidence_ocr(self) -> None:
        final = """---
來源頁: [1, 2]
---

# 題名

正文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("[low-confidence-bracket]", result.stdout)

    def test_boundary_manifest_enables_exact_virtual_final_audit(self) -> None:
        annotated = """<!-- page_001 -->
# 題名

正文未
<!-- page_002 -->
完。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"join"}}', encoding="utf-8"
            )

            result = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("[unresolved-boundaries]", result.stdout)

    def test_expected_count_accepts_intentional_skipped_page(self) -> None:
        annotated = """<!-- page_001 -->
# 題名

第一段。

<!-- page_003 -->

第二段。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            target.write_text(annotated, encoding="utf-8")

            accepted = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                3,
                "--skip-pages",
                2,
            )
            rejected = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                3,
            )

            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertIn("PAGE MARKERS 2", accepted.stdout)
            self.assertIn("authority=heuristic-only", accepted.stdout)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("missing page markers: 2", rejected.stdout)

    def test_final_audit_checks_untracked_file_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            # The temporary file is outside the repository and cannot be Git-tracked.
            target = Path(temporary_directory) / "new-final.md"
            target.write_text(
                "---\n題名: 測試\n---\n\n# 題名\n\n正文。  \n",
                encoding="utf-8",
            )

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[trailing-whitespace]", result.stdout)

    def test_final_audit_reports_soft_break_heading_and_loose_list(self) -> None:
        final = """# 題名
正文第一行
正文第二行

- 第一項

- 第二項
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[heading-spacing-after]", result.stdout)
            self.assertIn("[ordinary-soft-break]", result.stdout)
            self.assertIn("[loose-list-blank]", result.stdout)

    def test_duplicate_long_paragraph_is_warning_not_failure(self) -> None:
        paragraph = "這是一段用來測試完全重複長段落偵測而且本身沒有任何結構問題的文字。"
        final = f"{paragraph}\n\n{paragraph}\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--duplicate-min-chars",
                20,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("[duplicate-long-paragraph]", result.stdout)
            self.assertIn("warnings=1", result.stdout)


if __name__ == "__main__":
    unittest.main()
