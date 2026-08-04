from __future__ import annotations

import hashlib
import json
import os
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

    def test_join_rejected_for_indented_list_continuation(self) -> None:
        annotated = """<!-- page_001 -->
- 一、頁尾未完

<!-- page_002 -->

  下一頁續文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "final.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"join"}}',
                encoding="utf-8",
            )

            finalized = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--write",
            )
            audited = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
            )

            self.assertEqual(finalized.returncode, 1)
            self.assertIn("use list-cont", finalized.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(audited.returncode, 1)
            self.assertIn("use list-cont", audited.stdout)

    def test_join_rejected_for_nested_list_item(self) -> None:
        annotated = """<!-- page_001 -->
- 外層
<!-- page_002 -->
  - 跨頁子項
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "final.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"join"}}',
                encoding="utf-8",
            )

            rejected = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--write",
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("use list-new", rejected.stderr)
            self.assertFalse(output.exists())

            manifest.write_text(
                '{"boundaries":{"1":"start","2":"list-new"}}',
                encoding="utf-8",
            )
            accepted = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--write",
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "- 外層\n  - 跨頁子項\n")

    def test_structural_boundary_may_separate_two_lists(self) -> None:
        annotated = """<!-- page_001 -->
- 第一個清單完結

<!-- page_002 -->

- 第二個獨立清單
"""
        expected = """- 第一個清單完結

- 第二個獨立清單
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "final.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            receipt = Path(temporary_directory) / "assembly-receipt.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"structural"}}',
                encoding="utf-8",
            )

            result = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--write",
            )
            annotated_audit = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
            )
            unproven_final_audit = run_script(AUDIT, output, "--mode", "final")
            receipt_final_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            auto_receipt_audit = run_script(
                AUDIT,
                output,
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), expected)
            self.assertEqual(
                annotated_audit.returncode,
                0,
                annotated_audit.stdout + annotated_audit.stderr,
            )
            self.assertEqual(unproven_final_audit.returncode, 1)
            self.assertIn("[loose-list-blank]", unproven_final_audit.stdout)
            self.assertEqual(
                receipt_final_audit.returncode,
                0,
                receipt_final_audit.stdout + receipt_final_audit.stderr,
            )
            self.assertEqual(
                auto_receipt_audit.returncode,
                0,
                auto_receipt_audit.stdout + auto_receipt_audit.stderr,
            )

            manifest.write_text(
                '{"boundaries":{"1":"start","2":"list-new"}}',
                encoding="utf-8",
            )
            stale_receipt_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(stale_receipt_audit.returncode, 1)
            self.assertIn("[loose-list-blank]", stale_receipt_audit.stdout)
            self.assertIn("[assembly-input-changed]", stale_receipt_audit.stdout)

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

    def test_write_refuses_in_place_and_boundary_alias_output(self) -> None:
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

            boundary_alias = Path(temporary_directory) / "boundary-output.md"
            original_manifest = manifest.read_text(encoding="utf-8")
            os.link(manifest, boundary_alias)
            boundary_result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                boundary_alias,
                "--write",
            )
            self.assertEqual(boundary_result.returncode, 1)
            self.assertIn("--boundary-manifest", boundary_result.stderr)
            self.assertEqual(
                manifest.read_text(encoding="utf-8"), original_manifest
            )

    def test_write_refuses_hard_link_output_alias(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            output = Path(temporary_directory) / "hard-linked-final.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            os.link(target, output)
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--write",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("--output must differ", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), annotated)

    def test_receipt_refuses_hard_link_and_case_only_output_aliases(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            output = Path(temporary_directory) / "existing-final.md"
            hard_linked_receipt = Path(temporary_directory) / "receipt-link.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            output.write_text("舊 final。\n", encoding="utf-8")
            os.link(output, hard_linked_receipt)

            hard_link_result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                hard_linked_receipt,
                "--write",
            )

            self.assertEqual(hard_link_result.returncode, 1)
            self.assertIn("receipt path must differ", hard_link_result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "舊 final。\n")

            case_output = Path(temporary_directory) / "CaseFinal.md"
            case_receipt = Path(temporary_directory) / "casefinal.md"
            case_result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                case_output,
                "--receipt",
                case_receipt,
                "--write",
            )

            self.assertEqual(case_result.returncode, 1)
            self.assertIn("receipt path must differ", case_result.stderr)
            self.assertFalse(case_output.exists())

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

    def test_receipt_detects_late_upstream_and_final_changes(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "combined-annotated.md"
            segment = Path(temporary_directory) / "segment-001.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            output = Path(temporary_directory) / "final.md"
            receipt = Path(temporary_directory) / "assembly-receipt.json"
            target.write_text(annotated, encoding="utf-8")
            segment.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--upstream",
                segment,
                "--write",
            )
            current = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertIn("RECEIPT", written.stdout)
            self.assertEqual(current.returncode, 0, current.stdout + current.stderr)

            segment.write_text("<!-- page_001 -->\n遲來修訂。\n", encoding="utf-8")
            stale_input = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(stale_input.returncode, 1)
            self.assertIn("[assembly-input-changed]", stale_input.stdout)

            segment.write_text(annotated, encoding="utf-8")
            target.write_text(
                "<!-- page_001 -->\n合併稿遲來修訂。\n", encoding="utf-8"
            )
            stale_annotated = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(stale_annotated.returncode, 1)
            self.assertIn("[assembly-input-changed]", stale_annotated.stdout)
            self.assertIn("[assembly-final-stale]", stale_annotated.stdout)

            target.write_text(annotated, encoding="utf-8")
            output.write_text("正文已另改。\n", encoding="utf-8")
            stale_output = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(stale_output.returncode, 1)
            self.assertIn("[assembly-output-changed]", stale_output.stdout)
            self.assertIn("[assembly-final-stale]", stale_output.stdout)

    def test_receipt_must_seal_final_audit_interpretation_files(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "combined-annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            output = Path(temporary_directory) / "final.md"
            receipt = Path(temporary_directory) / "assembly-receipt.json"
            notation = Path(temporary_directory) / "notation-policy.json"
            exceptions = Path(temporary_directory) / "source-exceptions.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            notation.write_text(
                json.dumps(
                    {
                        "ascii_square_brackets": {
                            "meaning": "source-editorial",
                            "scan_page": 1,
                            "note": "凡例明定來源校勘符號。",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            exceptions.write_text('{"exceptions":[]}', encoding="utf-8")

            unsealed_write = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--write",
            )
            unsealed_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--notation-policy",
                notation,
                "--source-exceptions",
                exceptions,
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(unsealed_write.returncode, 0, unsealed_write.stderr)
            self.assertEqual(unsealed_audit.returncode, 1)
            self.assertEqual(
                unsealed_audit.stdout.count("[assembly-audit-input-unsealed]"), 2
            )

            sealed_write = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--upstream",
                notation,
                "--upstream",
                exceptions,
                "--write",
            )
            sealed_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--notation-policy",
                notation,
                "--source-exceptions",
                exceptions,
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(sealed_write.returncode, 0, sealed_write.stderr)
            self.assertEqual(
                sealed_audit.returncode, 0, sealed_audit.stdout + sealed_audit.stderr
            )

    def test_receipt_schema_version_rejects_float(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            output = Path(temporary_directory) / "final.md"
            receipt = Path(temporary_directory) / "receipt.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--write",
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
            receipt_data["schema_version"] = 1.0
            receipt.write_text(json.dumps(receipt_data), encoding="utf-8")

            result = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("schema_version must be 1", result.stderr)


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

    def test_page_manifest_rehashes_selected_pdf_and_every_render(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "final.md"
            source = root / "current-source.pdf"
            render_one = root / "page-001.jpg"
            render_two = root / "page-002.jpg"
            manifest = root / "page-manifest.json"
            target.write_text("正文。\n", encoding="utf-8")
            source.write_bytes(b"source-pdf")
            render_one.write_bytes(b"render-one")
            render_two.write_bytes(b"render-two")
            manifest.write_text(
                json.dumps(
                    {
                        "source_pdf": "/old/location/source.pdf",
                        "source_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                        "expected_scan_pages": 2,
                        "pages": [
                            {
                                "scan_page": 1,
                                "render_file": render_one.name,
                                "render_sha256": hashlib.sha256(
                                    render_one.read_bytes()
                                ).hexdigest(),
                            },
                            {
                                "scan_page": 2,
                                "render_file": render_two.name,
                                "render_sha256": hashlib.sha256(
                                    render_two.read_bytes()
                                ).hexdigest(),
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            current = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--page-manifest",
                manifest,
                "--source-pdf",
                source,
            )
            self.assertEqual(current.returncode, 0, current.stdout + current.stderr)

            render_two.write_bytes(b"changed-render")
            changed_render = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--page-manifest",
                manifest,
                "--source-pdf",
                source,
            )
            self.assertEqual(changed_render.returncode, 1)
            self.assertIn("[render-hash-mismatch]", changed_render.stdout)

            render_two.write_bytes(b"render-two")
            source.write_bytes(b"different-source")
            changed_source = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--page-manifest",
                manifest,
                "--source-pdf",
                source,
            )
            self.assertEqual(changed_source.returncode, 1)
            self.assertIn("[source-pdf-hash-mismatch]", changed_source.stdout)

    def test_sealed_page_manifest_controls_annotated_marker_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            annotated = root / "combined-annotated.md"
            boundaries = root / "boundaries.json"
            final = root / "final.md"
            receipt = root / "assembly-receipt.json"
            source = root / "source.pdf"
            render_one = root / "page-001.jpg"
            render_two = root / "page-002.jpg"
            page_manifest = root / "page-manifest.json"
            annotated.write_text("<!-- page_001 -->\n正文。\n", encoding="utf-8")
            boundaries.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            source.write_bytes(b"source-pdf")
            render_one.write_bytes(b"render-one")
            render_two.write_bytes(b"render-two")

            def write_page_manifest(*, skip_second_marker: bool) -> None:
                second_record: dict[str, object] = {
                    "scan_page": 2,
                    "render_file": render_two.name,
                    "render_sha256": hashlib.sha256(
                        render_two.read_bytes()
                    ).hexdigest(),
                }
                if skip_second_marker:
                    second_record["marker_expected"] = False
                page_manifest.write_text(
                    json.dumps(
                        {
                            "source_sha256": hashlib.sha256(
                                source.read_bytes()
                            ).hexdigest(),
                            "expected_scan_pages": 2,
                            "pages": [
                                {
                                    "scan_page": 1,
                                    "render_file": render_one.name,
                                    "render_sha256": hashlib.sha256(
                                        render_one.read_bytes()
                                    ).hexdigest(),
                                },
                                second_record,
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            def finalize_with_manifest() -> subprocess.CompletedProcess[str]:
                return run_script(
                    FINALIZE,
                    annotated,
                    "--boundary-manifest",
                    boundaries,
                    "--output",
                    final,
                    "--receipt",
                    receipt,
                    "--upstream",
                    page_manifest,
                    "--write",
                )

            def audit_with_manifest() -> subprocess.CompletedProcess[str]:
                return run_script(
                    AUDIT,
                    final,
                    "--mode",
                    "final",
                    "--page-manifest",
                    page_manifest,
                    "--source-pdf",
                    source,
                    "--assembly-receipt",
                    receipt,
                )

            write_page_manifest(skip_second_marker=False)
            self.assertEqual(finalize_with_manifest().returncode, 0)
            missing = audit_with_manifest()
            self.assertEqual(missing.returncode, 1)
            self.assertIn("[page-manifest-marker-coverage]", missing.stdout)
            self.assertIn("missing page markers: 2", missing.stdout)

            write_page_manifest(skip_second_marker=True)
            self.assertEqual(finalize_with_manifest().returncode, 0)
            explicit_skip = audit_with_manifest()
            self.assertEqual(
                explicit_skip.returncode,
                0,
                explicit_skip.stdout + explicit_skip.stderr,
            )

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

    def test_book_notation_policy_distinguishes_source_brackets(self) -> None:
        final = "編者據[原件]訂正。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            policy = Path(temporary_directory) / "notation-policy.json"
            target.write_text(final, encoding="utf-8")
            policy.write_text(
                json.dumps(
                    {
                        "ascii_square_brackets": {
                            "meaning": "source-editorial",
                            "scan_page": 7,
                            "note": "凡例明定半形方括號係編者訂正。",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            unclassified = run_script(AUDIT, target, "--mode", "final")
            classified = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--notation-policy",
                policy,
            )

            self.assertEqual(unclassified.returncode, 1)
            self.assertIn("[low-confidence-bracket]", unclassified.stdout)
            self.assertEqual(classified.returncode, 0, classified.stdout)
            self.assertIn("[source-notation-policy]", classified.stdout)

    def test_exact_source_exception_accepts_printed_unclosed_quote(self) -> None:
        line = "原書只有「開引號。"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            exceptions = Path(temporary_directory) / "source-exceptions.json"
            target.write_text(line + "\n", encoding="utf-8")
            exceptions.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "code": "unmatched-opening-delimiter",
                                "line": 1,
                                "column": 5,
                                "delimiter": "「",
                                "text": line,
                                "scan_page": 42,
                                "note": "放大頁圖確認原書未閉合。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            unclassified = run_script(AUDIT, target, "--mode", "final")
            accepted = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--source-exceptions",
                exceptions,
            )

            self.assertEqual(unclassified.returncode, 1)
            self.assertIn("[unmatched-opening-delimiter]", unclassified.stdout)
            self.assertEqual(accepted.returncode, 0, accepted.stdout)
            self.assertIn("[source-exception]", accepted.stdout)

    def test_source_exception_consumes_only_one_exact_delimiter(self) -> None:
        line = "「甲“乙。"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            exceptions = Path(temporary_directory) / "source-exceptions.json"
            target.write_text(line + "\n", encoding="utf-8")
            exceptions.write_text(
                json.dumps(
                    {
                        "exceptions": [
                            {
                                "code": "unmatched-opening-delimiter",
                                "line": 1,
                                "column": 1,
                                "delimiter": "「",
                                "text": line,
                                "scan_page": 9,
                                "note": "只確認第一個角引號係原書如此。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--source-exceptions",
                exceptions,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("[source-exception]", result.stdout)
            self.assertIn("final.md:1:3", result.stdout)
            self.assertIn("[unmatched-opening-delimiter]", result.stdout)

    def test_cross_type_misnested_delimiter_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text("「甲『乙」丙』\n", encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[misnested-closing-delimiter]", result.stdout)

    def test_list_continuation_gap_and_table_width_are_errors(self) -> None:
        final = """- 第一項

  續文

| 欄一 | 欄二 |
| --- | --- |
| 甲 | 乙 | 丙
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[loose-list-blank]", result.stdout)
            self.assertIn("[table-column-count]", result.stdout)

    def test_optional_outer_and_escaped_table_pipes_are_valid(self) -> None:
        final = r"""欄一 | 欄二
--- | ---
甲 \| 乙 | 丙
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("[ordinary-soft-break]", result.stdout)
            self.assertNotIn("[table-column-count]", result.stdout)

    def test_nested_list_gap_before_parent_item_is_error(self) -> None:
        final = """- 外層一
  - 內層

- 外層二
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text(final, encoding="utf-8")

            result = run_script(AUDIT, target, "--mode", "final")

            self.assertEqual(result.returncode, 1)
            self.assertIn("[loose-list-blank]", result.stdout)

    def test_direct_audit_checks_complete_end_of_file_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing_newline = Path(temporary_directory) / "missing-newline.md"
            extra_blank = Path(temporary_directory) / "extra-blank.md"
            space_tab = Path(temporary_directory) / "space-tab.md"
            missing_newline.write_text("正文。", encoding="utf-8")
            extra_blank.write_text("正文。\n\n", encoding="utf-8")
            space_tab.write_text(" \t正文。\n", encoding="utf-8")

            missing_result = run_script(AUDIT, missing_newline, "--mode", "final")
            blank_result = run_script(AUDIT, extra_blank, "--mode", "final")
            tab_result = run_script(AUDIT, space_tab, "--mode", "final")

            self.assertIn("[missing-final-newline]", missing_result.stdout)
            self.assertIn("[extra-blank-line-at-eof]", blank_result.stdout)
            self.assertIn("[space-before-tab]", tab_result.stdout)

    def test_empty_file_and_shorter_fence_close_are_handled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            empty = Path(temporary_directory) / "empty.md"
            fenced = Path(temporary_directory) / "fenced.md"
            unclosed_frontmatter = Path(temporary_directory) / "frontmatter.md"
            unclosed_fence = Path(temporary_directory) / "unclosed-fence.md"
            empty.write_text("", encoding="utf-8")
            fenced.write_text(
                "````\n```text\n「\n```\n````\n", encoding="utf-8"
            )
            unclosed_frontmatter.write_text(
                "---\n正文[低信心\n下一行「未閉合\n", encoding="utf-8"
            )
            unclosed_fence.write_text("```\n「未閉合\n", encoding="utf-8")

            empty_result = run_script(AUDIT, empty, "--mode", "final")
            fenced_result = run_script(AUDIT, fenced, "--mode", "final")
            frontmatter_result = run_script(
                AUDIT, unclosed_frontmatter, "--mode", "final"
            )
            unclosed_fence_result = run_script(
                AUDIT, unclosed_fence, "--mode", "final"
            )

            self.assertEqual(empty_result.returncode, 1)
            self.assertIn("[empty-file]", empty_result.stdout)
            self.assertEqual(
                fenced_result.returncode,
                0,
                fenced_result.stdout + fenced_result.stderr,
            )
            self.assertIn(
                "[unterminated-frontmatter]", frontmatter_result.stdout
            )
            self.assertIn(
                "[unterminated-fence]", unclosed_fence_result.stdout
            )


if __name__ == "__main__":
    unittest.main()
