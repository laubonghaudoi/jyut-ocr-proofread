from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
    / "build_notation_inventory.py"
)


def run_script(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class NotationInventoryTests(unittest.TestCase):
    def test_inventory_seals_target_authority_and_scan_locations(self) -> None:
        text = """<!-- page_001 -->
   ## 題名**（一九二一年）

正文〔原〕〈補〉［衍］□□▭〔?〕……

** 題注。
<!-- page_002 -->
續文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            authority = root / "notation-policy.json"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")
            authority.write_text('{"authority":"凡例"}\n', encoding="utf-8")

            result = run_script(
                target,
                "--authority",
                authority,
                "--require-markers",
                "--include-starred-title-notes",
                "--json-report",
                report,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["page_marker_count"], 2)
            self.assertEqual(payload["scan_range"], {"start": 1, "end": 2})
            self.assertEqual(payload["counts"]["heading-star-reference"], 2)
            self.assertEqual(payload["counts"]["starred-note-definition"], 2)
            self.assertEqual(payload["counts"]["white-square"], 2)
            self.assertEqual(payload["counts"]["rectangle"], 1)
            self.assertEqual(payload["counts"]["explicit-uncertain-marker"], 1)
            self.assertEqual(payload["occurrence_record_count"], len(payload["occurrences"]))
            self.assertEqual(payload["count_units"]["heading-star-reference"], "asterisk-characters")
            self.assertEqual(payload["count_units"]["ellipsis-pair"], "marker-tokens")
            self.assertEqual(payload["tool"]["name"], SCRIPT.name)
            self.assertEqual(payload["tool"]["sha256"], hashlib.sha256(SCRIPT.read_bytes()).hexdigest())
            self.assertEqual(
                [item["name"] for item in payload["tool_dependencies"]],
                ["_ocr_markdown.py", "validate_release_evidence.py"],
            )
            self.assertEqual(
                payload["invocation_profile"],
                {
                    "require_markers": True,
                    "authority_provided": True,
                    "inventory_profile_provided": False,
                    "include_starred_title_notes": True,
                },
            )
            self.assertIsNone(payload["inventory_profile"])
            self.assertEqual(
                payload["target"]["sha256"], hashlib.sha256(text.encode()).hexdigest()
            )
            self.assertEqual(
                payload["authority"]["sha256"],
                hashlib.sha256(authority.read_bytes()).hexdigest(),
            )
            square = next(
                item for item in payload["occurrences"] if item["kind"] == "white-square"
            )
            self.assertEqual(square["scan_page"], 1)
            self.assertEqual(square["count"], 2)

    def test_profile_and_markdown_protection_preserve_true_columns(self) -> None:
        source_line = (
            "正文□……※○〓 [假□……※○〓](https://example.test/□……※○〓) "
            "![圖□……※○〓](image-□……※○〓.png) `假□……※○〓` "
            "<!-- 假□……※○〓 --> 尾□※。"
        )
        text = f"""---
title: "假□……※○〓"
---
<!-- page_001 -->
~~~text
假□……※○〓
~~~
[ref□]: https://example.test/……※○〓
## 真題*
* 真題注。
{source_line}
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            profile = root / "inventory-profile.json"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")
            profile_payload = {
                "schema_version": 1,
                "artifact_type": "ocr-notation-inventory-profile",
                "symbols": [
                    {"id": "reference-mark", "literal": "※"},
                    {"id": "circle-mark", "literal": "○"},
                    {"id": "geta-mark", "literal": "〓"},
                ],
            }
            profile.write_text(
                json.dumps(profile_payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            result = run_script(
                target,
                "--inventory-profile",
                profile,
                "--require-markers",
                "--json-report",
                report,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["counts"]["white-square"], 2)
            self.assertEqual(payload["counts"]["ellipsis-pair"], 1)
            self.assertEqual(payload["counts"]["reference-mark"], 2)
            self.assertEqual(payload["counts"]["circle-mark"], 1)
            self.assertEqual(payload["counts"]["geta-mark"], 1)
            self.assertEqual(payload["counts"]["ascii-square-bracket-open"], 0)
            self.assertEqual(payload["counts"]["ascii-square-bracket-close"], 0)
            self.assertNotIn("heading-star-reference", payload["counts"])
            self.assertNotIn("starred-note-definition", payload["counts"])
            self.assertEqual(payload["count_units"]["reference-mark"], "literal-tokens")
            self.assertEqual(
                payload["inventory_profile"],
                {
                    "path": str(profile),
                    "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
                },
            )
            self.assertEqual(
                payload["invocation_profile"],
                {
                    "require_markers": True,
                    "authority_provided": False,
                    "inventory_profile_provided": True,
                    "include_starred_title_notes": False,
                },
            )

            source_line_number = text.splitlines().index(source_line) + 1
            for kind, literal in (
                ("white-square", "□"),
                ("ellipsis-pair", "……"),
                ("reference-mark", "※"),
                ("circle-mark", "○"),
                ("geta-mark", "〓"),
            ):
                matched = next(
                    item
                    for item in payload["occurrences"]
                    if item["kind"] == kind and item["line"] == source_line_number
                )
                self.assertEqual(matched["column"], source_line.index(literal) + 1)
                self.assertEqual(matched["text"], source_line)
            trailing_reference = [
                item
                for item in payload["occurrences"]
                if item["kind"] == "reference-mark"
                and item["line"] == source_line_number
            ][-1]
            self.assertEqual(trailing_reference["column"], source_line.rindex("※") + 1)

    def test_empty_inventory_profile_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            profile = root / "profile.json"
            report = root / "report.json"
            target.write_text("<!-- page_001 -->\n正文。\n", encoding="utf-8")
            profile.write_text(
                '{"schema_version":1,"artifact_type":"ocr-notation-inventory-profile","symbols":[]}\n',
                encoding="utf-8",
            )
            result = run_script(
                target,
                "--inventory-profile",
                profile,
                "--json-report",
                report,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(payload["invocation_profile"]["inventory_profile_provided"])
            self.assertEqual(payload["inventory_profile"]["path"], str(profile))

    def test_shortcut_reference_links_are_protected_but_plain_brackets_remain(self) -> None:
        text = """<!-- page_001 -->
[archive]: https://example.test/catalogue
See [archive], ![archive], [catalogue][archive], and [archive][]. Keep [source brackets].
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")

            result = run_script(target, "--json-report", report)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["ascii-square-bracket-open"], 1)
            self.assertEqual(payload["counts"]["ascii-square-bracket-close"], 1)
            occurrences = [
                item
                for item in payload["occurrences"]
                if item["kind"].startswith("ascii-square-bracket-")
            ]
            self.assertTrue(all("source brackets" in item["text"] for item in occurrences))

    def test_false_reference_definitions_and_undefined_references_remain_visible(self) -> None:
        text = """<!-- page_001 -->
```text
[fenced]: https://example.test/not-a-definition
```
[a\\q]: https://example.test/backslash-is-not-escapable
Keep [fenced], [aq], [text][missing], and [collapsed][].
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")

            result = run_script(target, "--json-report", report)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["ascii-square-bracket-open"], 6)
            self.assertEqual(payload["counts"]["ascii-square-bracket-close"], 6)
            occurrences = [
                item
                for item in payload["occurrences"]
                if item["kind"].startswith("ascii-square-bracket-")
            ]
            self.assertTrue(all(item["line"] == 6 for item in occurrences))

    def test_invalid_link_syntax_remains_visible(self) -> None:
        text = """<!-- page_001 -->
[bogus]:
Keep [bogus] and [source](not valid destination with spaces).
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")

            result = run_script(target, "--json-report", report)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["ascii-square-bracket-open"], 3)
            self.assertEqual(payload["counts"]["ascii-square-bracket-close"], 3)

    def test_multiline_comments_and_code_spans_are_protected(self) -> None:
        text = """<!-- page_001 -->
<!--
[comment]
-->
Before `code
[code]` after.
Keep [source].
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "inventory.json"
            target.write_text(text, encoding="utf-8")

            result = run_script(target, "--json-report", report)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["ascii-square-bracket-open"], 1)
            self.assertEqual(payload["counts"]["ascii-square-bracket-close"], 1)
            occurrences = [
                item
                for item in payload["occurrences"]
                if item["kind"].startswith("ascii-square-bracket-")
            ]
            self.assertTrue(all(item["line"] == 7 for item in occurrences))

    def test_inventory_profile_schema_fails_closed(self) -> None:
        cases = {
            "unknown-root-field": (
                '{"schema_version":1,"artifact_type":"ocr-notation-inventory-profile",'
                '"symbols":[],"regex":"x"}',
                "invalid inventory profile fields",
            ),
            "duplicate-key": (
                '{"schema_version":1,"schema_version":1,'
                '"artifact_type":"ocr-notation-inventory-profile","symbols":[]}',
                "duplicate JSON key",
            ),
            "invalid-id": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "ocr-notation-inventory-profile",
                        "symbols": [{"id": "Not_Kebab", "literal": "※"}],
                    },
                    ensure_ascii=False,
                ),
                "id must be kebab-case",
            ),
            "built-in-id": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "ocr-notation-inventory-profile",
                        "symbols": [{"id": "ellipsis-pair", "literal": "※"}],
                    },
                    ensure_ascii=False,
                ),
                "collides with built-in category",
            ),
            "built-in-literal": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "ocr-notation-inventory-profile",
                        "symbols": [{"id": "custom-ellipsis", "literal": "……"}],
                    },
                    ensure_ascii=False,
                ),
                "duplicates built-in literal",
            ),
            "duplicate-literal": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "ocr-notation-inventory-profile",
                        "symbols": [
                            {"id": "first-mark", "literal": "※"},
                            {"id": "second-mark", "literal": "※"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                "literal is duplicated",
            ),
            "newline-literal": (
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "ocr-notation-inventory-profile",
                        "symbols": [{"id": "line-mark", "literal": "※\n○"}],
                    },
                    ensure_ascii=False,
                ),
                "must not contain a newline",
            ),
        }
        for label, (profile_text, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                target = root / "annotated.md"
                profile = root / "profile.json"
                target.write_text("<!-- page_001 -->\n正文。\n", encoding="utf-8")
                profile.write_text(profile_text + "\n", encoding="utf-8")
                result = run_script(target, "--inventory-profile", profile)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)

    def test_require_markers_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "final.md"
            target.write_text("正文□。\n", encoding="utf-8")
            result = run_script(target, "--require-markers")
            self.assertEqual(result.returncode, 1)
            self.assertIn("no canonical page markers found", result.stderr)

    def test_malformed_marker_fails_closed(self) -> None:
        cases = {
            "syntax": ("<!--page_001-->\n正文。\n", "malformed page marker"),
            "order": (
                "<!-- page_002 -->\n正文。\n<!-- page_001 -->\n續文。\n",
                "unique and strictly increasing",
            ),
        }
        for label, (text, expected) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                target = Path(temporary_directory) / "annotated.md"
                target.write_text(text, encoding="utf-8")
                result = run_script(target)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)

    def test_aliases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            authority = root / "authority.json"
            target.write_text("<!-- page_001 -->\n正文。\n", encoding="utf-8")
            authority.write_text("{}\n", encoding="utf-8")

            target_alias = root / "target-report.json"
            target_alias.hardlink_to(target)
            result = run_script(target, "--json-report", target_alias)
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias target", result.stderr)

            authority_alias = root / "authority-report.json"
            authority_alias.hardlink_to(authority)
            result = run_script(
                target,
                "--authority",
                authority,
                "--json-report",
                authority_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias --authority", result.stderr)

            authority_target_alias = root / "target-authority.json"
            authority_target_alias.hardlink_to(target)
            result = run_script(target, "--authority", authority_target_alias)
            self.assertEqual(result.returncode, 1)
            self.assertIn("--authority must not alias target", result.stderr)

            profile_target_alias = root / "target-profile.json"
            profile_target_alias.hardlink_to(target)
            result = run_script(
                target,
                "--inventory-profile",
                profile_target_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--inventory-profile must not alias target", result.stderr)

            profile_authority_alias = root / "authority-profile.json"
            profile_authority_alias.hardlink_to(authority)
            result = run_script(
                target,
                "--authority",
                authority,
                "--inventory-profile",
                profile_authority_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--inventory-profile must not alias --authority", result.stderr)

            profile = root / "profile.json"
            profile.write_text(
                '{"schema_version":1,"artifact_type":"ocr-notation-inventory-profile","symbols":[]}\n',
                encoding="utf-8",
            )
            profile_report_alias = root / "profile-report.json"
            profile_report_alias.hardlink_to(profile)
            result = run_script(
                target,
                "--inventory-profile",
                profile,
                "--json-report",
                profile_report_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--json-report must not alias --inventory-profile", result.stderr)


if __name__ == "__main__":
    unittest.main()
