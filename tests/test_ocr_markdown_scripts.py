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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FinalizePageMarkersTests(unittest.TestCase):
    def test_summary_only_suppresses_markers_but_keeps_write_summaries(self) -> None:
        annotated = """<!-- page_001 -->
正文未
<!-- page_002 -->
完。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            output = root / "final.md"
            manifest = root / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"join"}}',
                encoding="utf-8",
            )

            verbose = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
            )
            summarized = run_script(
                FINALIZE,
                target,
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--summary-only",
                "--write",
            )

            self.assertEqual(verbose.returncode, 0, verbose.stderr)
            self.assertIn("MARKER ", verbose.stdout)
            self.assertEqual(summarized.returncode, verbose.returncode)
            self.assertNotIn("MARKER ", summarized.stdout)
            self.assertIn("WROTE ", summarized.stdout)
            self.assertIn("removed 2 page marker(s)", summarized.stdout)
            self.assertEqual(output.read_text(encoding="utf-8"), "正文未完。\n")

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

    def test_table_continuation_preserves_one_table_and_rejects_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target, output, manifest = (
                root / "annotated.md", root / "final.md", root / "boundaries.json"
            )
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"table-cont"}}',
                encoding="utf-8",
            )
            target.write_text(
                "<!-- page_001 -->\n| 名 | 數 |\n| --- | --- |\n| 甲 | 1 |\n"
                "\n<!-- page_002 -->\n\n| 乙 | 2 |\n",
                encoding="utf-8",
            )
            result = run_script(
                FINALIZE, target, "--boundary-manifest", manifest,
                "--output", output, "--write",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "| 名 | 數 |\n| --- | --- |\n| 甲 | 1 |\n| 乙 | 2 |\n",
            )
            audited = run_script(
                AUDIT, output, "--mode", "final", "--annotated-source", target,
                "--boundary-manifest", manifest,
            )
            self.assertEqual(audited.returncode, 0, audited.stdout)
            for previous, following in (("正文", "| 乙 | 2 |"), ("| 甲 | 1 |", "正文")):
                with self.subTest(previous=previous, following=following):
                    target.write_text(
                        f"<!-- page_001 -->\n{previous}\n<!-- page_002 -->\n{following}\n",
                        encoding="utf-8",
                    )
                    rejected = run_script(FINALIZE, target, "--boundary-manifest", manifest)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("both sides are not table rows", rejected.stderr)

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
            structured_report = Path(temporary_directory) / "structured-audit.json"
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
            structured_final_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--annotated-source",
                target,
                "--boundary-manifest",
                manifest,
                "--json-report",
                structured_report,
            )
            auto_structured_audit = run_script(
                AUDIT,
                output,
                "--annotated-source",
                target,
                "--boundary-manifest",
                manifest,
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
                structured_final_audit.returncode,
                0,
                structured_final_audit.stdout + structured_final_audit.stderr,
            )
            structured_report_data = json.loads(
                structured_report.read_text(encoding="utf-8")
            )
            self.assertEqual(
                structured_report_data["input_sha256"]["annotated_source"],
                sha256_file(target),
            )
            self.assertEqual(
                structured_report_data["input_sha256"]["boundary_manifest"],
                sha256_file(manifest),
            )
            self.assertEqual(
                auto_structured_audit.returncode,
                0,
                auto_structured_audit.stdout + auto_structured_audit.stderr,
            )

            manifest.write_text(
                '{"boundaries":{"1":"start","2":"list-new"}}',
                encoding="utf-8",
            )
            stale_structured_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--annotated-source",
                target,
                "--boundary-manifest",
                manifest,
            )
            self.assertEqual(stale_structured_audit.returncode, 1)
            self.assertIn("[loose-list-blank]", stale_structured_audit.stdout)
            self.assertIn("[structured-final-stale]", stale_structured_audit.stdout)

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

    def test_write_refuses_case_only_output_alias(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            case_output = Path(temporary_directory) / "Annotated.md"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            case_result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                case_output,
                "--write",
            )

            self.assertEqual(case_result.returncode, 1)
            self.assertIn("--output must differ", case_result.stderr)
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

    def test_final_audit_report_hashes_interpretation_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "final.md"
            report = Path(temporary_directory) / "audit-report.json"
            notation = Path(temporary_directory) / "notation-policy.json"
            exceptions = Path(temporary_directory) / "source-exceptions.json"
            output.write_text("正文。\n", encoding="utf-8")
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

            audited = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--notation-policy",
                notation,
                "--source-exceptions",
                exceptions,
                "--json-report",
                report,
            )

            self.assertEqual(
                audited.returncode, 0, audited.stdout + audited.stderr
            )
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                report_data["input_sha256"],
                {
                    "notation_policy": hashlib.sha256(
                        notation.read_bytes()
                    ).hexdigest(),
                    "source_exceptions": hashlib.sha256(
                        exceptions.read_bytes()
                    ).hexdigest(),
                },
            )


class AuditOcrMarkdownTests(unittest.TestCase):
    def test_summary_only_reports_deterministic_issue_counts(self) -> None:
        annotated = """<!-- page_001 -->
正文[甲]∵。

<!-- page_002 -->

正文[乙]。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            manifest = root / "boundaries.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"paragraph"}}',
                encoding="utf-8",
            )

            verbose = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
            )
            summarized = run_script(
                AUDIT,
                target,
                "--mode",
                "annotated",
                "--expected-count",
                2,
                "--boundary-manifest",
                manifest,
                "--summary-only",
            )

            self.assertEqual(verbose.returncode, 1)
            self.assertIn("BOUNDARY-SUGGESTION ", verbose.stdout)
            self.assertIn("[low-confidence-bracket]", verbose.stdout)
            self.assertEqual(summarized.returncode, verbose.returncode)
            self.assertIn("MODE annotated", summarized.stdout)
            self.assertIn("PAGE MARKERS 2", summarized.stdout)
            self.assertNotIn("BOUNDARY-SUGGESTION ", summarized.stdout)
            self.assertNotIn("[low-confidence-bracket]", summarized.stdout)
            error_count = (
                "ISSUE-COUNT severity=error "
                "code=low-confidence-bracket count=2"
            )
            warning_count = (
                "ISSUE-COUNT severity=warning "
                "code=suspicious-ocr-symbol count=1"
            )
            self.assertIn(error_count, summarized.stdout)
            self.assertIn(warning_count, summarized.stdout)
            self.assertLess(
                summarized.stdout.index(error_count),
                summarized.stdout.index(warning_count),
            )
            self.assertIn(
                "SUMMARY errors=2 warnings=1 accepted=0 markers=2",
                summarized.stdout,
            )

    def test_json_report_is_deterministic_and_preserves_cli_behavior(self) -> None:
        line = "原書只有「開引號，並有[低]∵。"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "final.md"
            exceptions = root / "source-exceptions.json"
            first_report = root / "first-report.json"
            second_report = root / "second-report.json"
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
                                "scan_page": 9,
                                "note": "頁圖確認原書未閉合。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            baseline = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--source-exceptions",
                exceptions,
            )
            first = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--source-exceptions",
                exceptions,
                "--json-report",
                first_report,
            )
            second = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--source-exceptions",
                exceptions,
                "--json-report",
                second_report,
            )

            self.assertEqual(baseline.returncode, 1)
            self.assertEqual(first.returncode, baseline.returncode)
            self.assertEqual(second.returncode, baseline.returncode)
            self.assertEqual(first.stdout, baseline.stdout)
            self.assertEqual(second.stdout, baseline.stdout)
            self.assertEqual(first.stderr, baseline.stderr)
            self.assertEqual(second.stderr, baseline.stderr)
            self.assertEqual(first_report.read_bytes(), second_report.read_bytes())

            report = json.loads(first_report.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["mode"], "final")
            self.assertEqual(report["exit_status"], 1)
            self.assertEqual(
                report["target"],
                {
                    "bytes": len((line + "\n").encode("utf-8")),
                    "sha256": hashlib.sha256(
                        (line + "\n").encode("utf-8")
                    ).hexdigest(),
                },
            )
            self.assertEqual(
                report["tool"],
                {
                    "name": "audit_ocr_markdown.py",
                    "sha256": hashlib.sha256(AUDIT.read_bytes()).hexdigest(),
                },
            )
            self.assertEqual(
                report["audit_profile"],
                {
                    "duplicate_min_chars": 120,
                    "expected_count": None,
                    "optional_gates": {
                        "annotated_source": False,
                        "boundary_manifest": False,
                        "notation_policy": False,
                        "page_manifest": False,
                        "source_exceptions": True,
                        "source_pdf": False,
                    },
                    "requested_mode": "final",
                    "skip_pages": [],
                },
            )
            self.assertEqual(
                report["input_sha256"],
                {
                    "source_exceptions": hashlib.sha256(
                        exceptions.read_bytes()
                    ).hexdigest()
                },
            )
            self.assertEqual(
                report["counts"],
                {
                    "accepted_exceptions": 1,
                    "errors": 1,
                    "markers": 0,
                    "warnings": 1,
                },
            )
            self.assertEqual(
                report["issue_counts"],
                [
                    {
                        "code": "low-confidence-bracket",
                        "count": 1,
                        "severity": "error",
                    },
                    {
                        "code": "suspicious-ocr-symbol",
                        "count": 1,
                        "severity": "warning",
                    },
                    {
                        "code": "source-exception",
                        "count": 1,
                        "severity": "accepted",
                    },
                ],
            )
            self.assertEqual(
                report["errors"][0]["code"], "low-confidence-bracket"
            )
            self.assertEqual(
                report["warnings"][0]["code"], "suspicious-ocr-symbol"
            )
            self.assertEqual(
                report["accepted_exceptions"][0]["code"],
                "source-exception",
            )
            self.assertNotIn(str(root), first_report.read_text(encoding="utf-8"))

    def test_json_report_hash_profile_is_path_independent(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        manifest_text = '{"boundaries":{"1":"start"}}'
        with tempfile.TemporaryDirectory() as temporary_directory:
            reports: list[bytes] = []
            for directory_name in ("first-copy", "second-copy"):
                root = Path(temporary_directory) / directory_name
                root.mkdir()
                target = root / "annotated.md"
                manifest = root / "boundaries.json"
                report = root / "audit-report.json"
                target.write_text(annotated, encoding="utf-8")
                manifest.write_text(manifest_text, encoding="utf-8")

                result = run_script(
                    AUDIT,
                    target,
                    "--mode",
                    "auto",
                    "--expected-count",
                    3,
                    "--skip-pages",
                    "3,2",
                    "--duplicate-min-chars",
                    77,
                    "--boundary-manifest",
                    manifest,
                    "--json-report",
                    report,
                )

                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                report_data = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(report_data["mode"], "annotated")
                self.assertEqual(
                    report_data["audit_profile"],
                    {
                        "duplicate_min_chars": 77,
                        "expected_count": 3,
                        "optional_gates": {
                            "annotated_source": False,
                            "boundary_manifest": True,
                            "notation_policy": False,
                            "page_manifest": False,
                            "source_exceptions": False,
                            "source_pdf": False,
                        },
                        "requested_mode": "auto",
                        "skip_pages": [2, 3],
                    },
                )
                self.assertEqual(
                    report_data["target"],
                    {
                        "bytes": len(annotated.encode("utf-8")),
                        "sha256": hashlib.sha256(
                            annotated.encode("utf-8")
                        ).hexdigest(),
                    },
                )
                self.assertEqual(
                    report_data["input_sha256"],
                    {
                        "boundary_manifest": hashlib.sha256(
                            manifest_text.encode("utf-8")
                        ).hexdigest()
                    },
                )
                self.assertNotIn(
                    str(Path(temporary_directory)),
                    report.read_text(encoding="utf-8"),
                )
                reports.append(report.read_bytes())

            self.assertEqual(reports[0], reports[1])

    def test_json_report_target_identity_changes_with_audited_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "final.md"
            first_report = root / "first.json"
            second_report = root / "second.json"
            first_text = "正文。\n"
            second_text = "改寫正文。\n"

            target.write_text(first_text, encoding="utf-8")
            first = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--json-report",
                first_report,
            )
            target.write_text(second_text, encoding="utf-8")
            second = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--json-report",
                second_report,
            )

            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(first.stdout, second.stdout)
            first_data = json.loads(first_report.read_text(encoding="utf-8"))
            second_data = json.loads(second_report.read_text(encoding="utf-8"))
            self.assertNotEqual(first_data["target"], second_data["target"])
            self.assertEqual(
                first_data["target"]["sha256"],
                hashlib.sha256(first_text.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                second_data["target"]["sha256"],
                hashlib.sha256(second_text.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                first_data["audit_profile"], second_data["audit_profile"]
            )
            self.assertEqual(first_data["tool"], second_data["tool"])

    def test_json_report_cannot_overwrite_an_audit_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            original = "正文。\n"
            target.write_text(original, encoding="utf-8")

            result = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--json-report",
                target,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("--json-report must differ", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)

            local_scripts = Path(temporary_directory) / "scripts"
            local_scripts.mkdir()
            local_audit = local_scripts / AUDIT.name
            local_audit.write_bytes(AUDIT.read_bytes())
            for sibling in AUDIT.parent.glob("*.py"):
                if sibling.name != AUDIT.name:
                    os.symlink(sibling, local_scripts / sibling.name)
            tool_alias = Path(temporary_directory) / "audit-tool-alias.py"
            tool_before = hashlib.sha256(local_audit.read_bytes()).hexdigest()
            os.link(local_audit, tool_alias)
            tool_result = run_script(
                local_audit,
                target,
                "--mode",
                "final",
                "--json-report",
                tool_alias,
            )
            self.assertEqual(tool_result.returncode, 2)
            self.assertIn("--json-report must differ", tool_result.stderr)
            self.assertEqual(
                hashlib.sha256(local_audit.read_bytes()).hexdigest(), tool_before
            )

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

    def test_annotated_page_manifest_immediately_checks_marker_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            source = root / "source.pdf"
            render_one = root / "page-001.jpg"
            render_two = root / "page-002.jpg"
            manifest = root / "page-manifest.json"
            source.write_bytes(b"source-pdf")
            render_one.write_bytes(b"render-one")
            render_two.write_bytes(b"render-two")

            def write_manifest(*, second_marker_expected: bool) -> None:
                manifest.write_text(
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
                                {
                                    "scan_page": 2,
                                    "render_file": render_two.name,
                                    "render_sha256": hashlib.sha256(
                                        render_two.read_bytes()
                                    ).hexdigest(),
                                    "marker_expected": second_marker_expected,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            def audit() -> subprocess.CompletedProcess[str]:
                return run_script(
                    AUDIT,
                    target,
                    "--mode",
                    "annotated",
                    "--page-manifest",
                    manifest,
                    "--source-pdf",
                    source,
                )

            target.write_text("<!-- page_001 -->\n正文一。\n", encoding="utf-8")
            write_manifest(second_marker_expected=True)
            missing = audit()
            self.assertEqual(missing.returncode, 1)
            self.assertIn("[page-manifest-marker-coverage]", missing.stdout)
            self.assertIn("missing page markers: 2", missing.stdout)

            write_manifest(second_marker_expected=False)
            intentional_skip = audit()
            self.assertEqual(
                intentional_skip.returncode,
                0,
                intentional_skip.stdout + intentional_skip.stderr,
            )

            target.write_text(
                "<!-- page_001 -->\n正文一。\n<!-- page_002 -->\n正文二。\n",
                encoding="utf-8",
            )
            unexpected = audit()
            self.assertEqual(unexpected.returncode, 1)
            self.assertIn("[page-manifest-marker-coverage]", unexpected.stdout)
            self.assertIn("unexpected page markers: 2", unexpected.stdout)

    def test_sealed_page_manifest_controls_annotated_marker_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            annotated = root / "combined-annotated.md"
            boundaries = root / "boundaries.json"
            final = root / "final.md"
            report = root / "audit-report.json"
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
                    "--write",
                )

            def audit_with_manifest() -> subprocess.CompletedProcess[str]:
                return run_script(
                    AUDIT,
                    final,
                    "--mode",
                    "final",
                    "--annotated-source",
                    annotated,
                    "--boundary-manifest",
                    boundaries,
                    "--page-manifest",
                    page_manifest,
                    "--source-pdf",
                    source,
                    "--json-report",
                    report,
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
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                report_data["input_sha256"],
                {
                    "annotated_source": hashlib.sha256(
                        annotated.read_bytes()
                    ).hexdigest(),
                    "boundary_manifest": hashlib.sha256(
                        boundaries.read_bytes()
                    ).hexdigest(),
                    "page_manifest": hashlib.sha256(
                        page_manifest.read_bytes()
                    ).hexdigest(),
                    "source_pdf": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
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

    def test_notation_policy_inventories_each_supported_source_symbol(self) -> None:
        final = "據[原件]及［增］、〔疑〕、〈篇〉，缺□、▭並存疑〔?〕。\n"
        policies = {
            "ascii_square_brackets": "source-editorial",
            "fullwidth_square_brackets": "source-editorial",
            "lenticular_brackets": "source-apparatus",
            "angle_brackets": "source-apparatus",
            "white_square": "source-illegible",
            "rectangle": "source-damage",
            "explicit_uncertain_marker": "source-uncertain",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "final.md"
            policy = root / "notation-policy.json"
            target.write_text(final, encoding="utf-8")
            policy.write_text(
                json.dumps(
                    {
                        notation: {
                            "meaning": meaning,
                            "scan_page": index,
                            "note": f"凡例證明 {notation}。",
                        }
                        for index, (notation, meaning) in enumerate(
                            reversed(tuple(policies.items())), 1
                        )
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
            self.assertIn(
                "[unclassified-source-placeholder]", unclassified.stdout
            )
            self.assertIn(
                "[unclassified-uncertain-marker]", unclassified.stdout
            )
            self.assertEqual(
                classified.returncode,
                0,
                classified.stdout + classified.stderr,
            )
            self.assertEqual(
                classified.stdout.count("[source-notation-policy]"), 7
            )
            for notation in policies:
                self.assertIn(notation, classified.stdout)
            self.assertIn("inventory=1", classified.stdout)

    def test_notation_authorization_is_specific_and_final_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            final = root / "final.md"
            annotated = root / "annotated.md"
            white_policy = root / "white-policy.json"
            lenticular_policy = root / "lenticular-policy.json"
            ascii_policy = root / "ascii-policy.json"
            final.write_text("正文[低]□及〔?〕。\n", encoding="utf-8")
            annotated.write_text(
                "<!-- page_001 -->\n正文[低]。\n", encoding="utf-8"
            )
            white_policy.write_text(
                json.dumps(
                    {
                        "white_square": {
                            "meaning": "source-illegible",
                            "scan_page": 2,
                            "note": "凡例明定缺字符號。",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            lenticular_policy.write_text(
                json.dumps(
                    {
                        "lenticular_brackets": {
                            "meaning": "source-apparatus",
                            "scan_page": 2,
                            "note": "凡例明定校勘括號。",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            ascii_policy.write_text(
                json.dumps(
                    {
                        "ascii_square_brackets": {
                            "meaning": "source-editorial",
                            "scan_page": 2,
                            "note": "凡例明定半形方括號。",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            white_only = run_script(
                AUDIT,
                final,
                "--mode",
                "final",
                "--notation-policy",
                white_policy,
            )
            lenticular_only = run_script(
                AUDIT,
                final,
                "--mode",
                "final",
                "--notation-policy",
                lenticular_policy,
            )
            annotated_ascii = run_script(
                AUDIT,
                annotated,
                "--mode",
                "annotated",
                "--notation-policy",
                ascii_policy,
            )

            self.assertEqual(white_only.returncode, 1)
            self.assertIn("[low-confidence-bracket]", white_only.stdout)
            self.assertIn(
                "[unclassified-uncertain-marker]", white_only.stdout
            )
            self.assertEqual(lenticular_only.returncode, 1)
            self.assertIn(
                "[unclassified-uncertain-marker]", lenticular_only.stdout
            )
            self.assertEqual(annotated_ascii.returncode, 1)
            self.assertIn(
                "[notation-policy-requires-final]", annotated_ascii.stdout
            )
            self.assertIn("[low-confidence-bracket]", annotated_ascii.stdout)

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

    def test_paddlex_img_in_tag_flagged_outside_fences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            bad_prose = root / "bad-prose.md"
            bad_table = root / "bad-table.md"
            fenced_ok = root / "fenced-ok.md"
            bad_prose.write_text(
                '前文\n\n<img src="imgs/img_in_image_box_1_2_3_4.jpg" alt="Image" />\n',
                encoding="utf-8",
            )
            bad_table.write_text(
                '<table><tr><td><img src="imgs/img_in_seal_box_475_2209_885_2616.jpg" alt="Image"" /></td><td>正文</td></tr></table>\n',
                encoding="utf-8",
            )
            fenced_ok.write_text(
                '```html\n<img src="imgs/img_in_example.jpg" alt="Image" />\n```\n',
                encoding="utf-8",
            )

            prose_res = run_script(AUDIT, bad_prose, "--mode", "final")
            table_res = run_script(AUDIT, bad_table, "--mode", "final")
            fence_res = run_script(AUDIT, fenced_ok, "--mode", "final")

            self.assertEqual(prose_res.returncode, 1)
            self.assertIn("[ocr-engine-image-tag]", prose_res.stdout)
            self.assertEqual(table_res.returncode, 1)
            self.assertIn("[ocr-engine-image-tag]", table_res.stdout)
            self.assertEqual(fence_res.returncode, 0, fence_res.stdout + fence_res.stderr)


if __name__ == "__main__":
    unittest.main()
