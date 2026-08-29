from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
    / "audit_title_notes.py"
)
SCRIPTS_DIR = SCRIPT.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import audit_title_notes as audit_title_notes_module  # noqa: E402
sys.path.pop(0)


def run_script(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(argument) for argument in arguments)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class AuditTitleNotesTests(unittest.TestCase):
    def _write_boundary(self, root: Path, label: str = "paragraph") -> Path:
        boundary = root / "boundaries.json"
        boundary.write_text(
            json.dumps({"boundaries": {"1": "start", "2": label}}),
            encoding="utf-8",
        )
        return boundary

    def _review_payload(
        self,
        target: Path,
        boundary: Path,
        *,
        note_kind: str = "title-note",
        previous_text: str = "* Editorial note.",
        following_text: str = "Source: Archive A, box 7.",
        boundary_label: str = "paragraph",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "artifact_type": "ocr-note-transition-reviews",
            "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "boundary_manifest_sha256": hashlib.sha256(
                boundary.read_bytes()
            ).hexdigest(),
            "reviews": [
                {
                    "scan_page": 2,
                    "note_kind": note_kind,
                    "previous_text": previous_text,
                    "following_text": following_text,
                    "boundary_label": boundary_label,
                    "disposition": "accepted-reviewed-note-transition",
                    "writer": "Writer A",
                    "independent_reviewer": "Reviewer B",
                    "visual_evidence": (
                        "Scan 2 shows an intentional transition after the note."
                    ),
                }
            ],
        }

    def _run(
        self,
        root: Path,
        markdown: str,
        *extra: object,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        target = root / "annotated.md"
        report = root / "report.json"
        target.write_text(markdown, encoding="utf-8")
        result = run_script(target, *extra, "--json-report", report)
        return result, target, report

    def test_balanced_title_notes_pass_and_seal_target_hash(self) -> None:
        markdown = """<!-- page_0001 -->
# 題名

  ## 第一件*（一九二一年）

正文。

* 第一件題注。

## 第二件**

正文。

** 第二件題注。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result, target, report = self._run(root, markdown)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["heading_marker_counts"], {"*": 1, "**": 1})
            self.assertEqual(
                payload["title_note_marker_counts"], {"*": 1, "**": 1}
            )
            self.assertEqual(payload["heading_star_count"], 3)
            self.assertEqual(payload["note_star_count"], 3)
            self.assertEqual(payload["mismatch_count"], 0)
            self.assertEqual(payload["orphan_count"], 0)
            self.assertEqual(payload["tool"]["name"], SCRIPT.name)
            self.assertEqual(
                payload["tool"]["sha256"],
                hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                [item["name"] for item in payload["tool_dependencies"]],
                ["_ocr_markdown.py", "validate_release_evidence.py"],
            )
            self.assertEqual(
                payload["invocation_profile"],
                {
                    "heading_level": 2,
                    "boundary_manifest_provided": False,
                    "transition_reviews_provided": False,
                },
            )
            self.assertEqual(
                payload["target_sha256"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )

    def test_marker_arity_mismatch_reports_missing_and_orphan(self) -> None:
        markdown = """<!-- page_001 -->
## 題名*

正文。

** 錯配題注。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result, _, report = self._run(root, markdown)

            self.assertEqual(result.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["mismatch_count"], 1)
            self.assertEqual(payload["missing_note_count"], 1)
            self.assertEqual(payload["orphan_count"], 1)
            self.assertEqual(payload["missing_notes"][0]["marker"], "*")
            self.assertEqual(payload["orphan_notes"][0]["marker"], "**")

    def test_full_markdown_emphasis_headings_are_not_title_note_markers(self) -> None:
        markdown = """<!-- page_001 -->
## *Italic title*

正文。

## **Bold title**

正文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result, _, report = self._run(root, markdown)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["heading_marker_counts"], {"*": 0, "**": 0})
            self.assertEqual(payload["title_note_marker_counts"], {"*": 0, "**": 0})
            self.assertEqual(payload["missing_note_count"], 0)
            self.assertEqual(payload["orphan_count"], 0)

    def test_nonrendered_markdown_cannot_satisfy_a_missing_title_note(self) -> None:
        cases = {
            "fence": """<!-- page_001 -->
## Actual item*

Body.

```text
* Not a rendered note.
```
""",
            "multiline-comment": """<!-- page_001 -->
## Actual item*

Body.

<!--
* Not a rendered note.
-->
""",
            "multiline-code-span": """<!-- page_001 -->
## Actual item*

Body before `code
* Not a rendered note.` after.
""",
        }
        for label, markdown in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                result, _, report = self._run(root, markdown)

                self.assertEqual(result.returncode, 1)
                payload = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "requires-visual-review")
                self.assertEqual(payload["missing_note_count"], 1)
                self.assertEqual(payload["title_note_marker_counts"]["*"], 0)

    def test_title_note_cross_page_hazard_is_always_actionable(self) -> None:
        markdown = """<!-- page_001 -->
## 題名*

正文。

* 題注。

<!-- page_002 -->
正文續文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary = root / "boundaries.json"
            boundary.write_text(
                json.dumps(
                    {"boundaries": {"1": "start", "2": "paragraph"}}
                ),
                encoding="utf-8",
            )
            result, _, report = self._run(
                root, markdown, "--boundary-manifest", boundary
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["mismatch_count"], 0)
            self.assertEqual(payload["cross_page_actionable_count"], 1)
            hazard = payload["cross_page_hazards"][0]
            self.assertEqual(hazard["note_kind"], "title-note")
            self.assertEqual(hazard["boundary_label"], "paragraph")
            self.assertTrue(hazard["status"].startswith("actionable-"))

    def test_numbered_note_boundary_without_review_is_actionable(self) -> None:
        markdown = """<!-- page_001 -->
## 題名

正文。

① 編者註。

<!-- page_002 -->
新段正文。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary = root / "boundaries.json"
            boundary.write_text(
                json.dumps(
                    {"boundaries": {"1": "start", "2": "structural"}}
                ),
                encoding="utf-8",
            )
            result, _, report = self._run(
                root, markdown, "--boundary-manifest", boundary
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["cross_page_actionable_count"], 1)
            self.assertEqual(payload["cross_page_accepted_count"], 0)
            self.assertEqual(
                payload["cross_page_hazards"][0]["status"],
                "actionable-numbered-note-cross-page-interruption",
            )

    def test_json_report_rejects_hard_link_alias(self) -> None:
        markdown = "<!-- page_001 -->\n## 題名\n\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            alias = root / "report.json"
            target.write_text(markdown, encoding="utf-8")
            alias.hardlink_to(target)

            result = run_script(target, "--json-report", alias)

            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias", result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), markdown)

    def test_transition_review_paths_reject_input_and_output_aliases(self) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
Source: Archive A, box 7.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            target.write_text(markdown, encoding="utf-8")
            boundary = self._write_boundary(root)

            target_alias = root / "reviews-target-alias.json"
            target_alias.hardlink_to(target)
            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                target_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias the annotated target", result.stderr)

            boundary_alias = root / "reviews-boundary-alias.json"
            boundary_alias.hardlink_to(boundary)
            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                boundary_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias --boundary-manifest", result.stderr)

            reviews = root / "transition-reviews.json"
            reviews.write_text(
                json.dumps(self._review_payload(target, boundary)),
                encoding="utf-8",
            )
            report_alias = root / "report-alias.json"
            report_alias.hardlink_to(reviews)
            original_reviews = reviews.read_bytes()
            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                reviews,
                "--json-report",
                report_alias,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("must not alias --transition-reviews", result.stderr)
            self.assertEqual(reviews.read_bytes(), original_reviews)

    def test_lexical_shape_never_auto_accepts_transition(self) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
According to Archive A, published in Catalogue B, volume 3.

## Next item

Body.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary = self._write_boundary(root)
            result, _, report = self._run(
                root, markdown, "--boundary-manifest", boundary
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["cross_page_hazard_count"], 1)
            self.assertEqual(payload["cross_page_actionable_count"], 1)
            self.assertEqual(
                payload["cross_page_hazards"][0]["status"],
                "actionable-title-note-cross-page-interruption",
            )

    def test_exact_review_accepts_non_chinese_title_note_transition(self) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
Source: Archive A, box 7.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "report.json"
            reviews = root / "transition-reviews.json"
            target.write_text(markdown, encoding="utf-8")
            boundary = self._write_boundary(root)
            reviews.write_text(
                json.dumps(self._review_payload(target, boundary)),
                encoding="utf-8",
            )

            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                reviews,
                "--json-report",
                report,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["cross_page_actionable_count"], 0)
            self.assertEqual(payload["cross_page_accepted_count"], 1)
            self.assertEqual(payload["transition_review_count"], 1)
            self.assertEqual(payload["unused_transition_reviews"], [])
            self.assertEqual(
                payload["transition_reviews_sha256"],
                hashlib.sha256(reviews.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                payload["invocation_profile"],
                {
                    "heading_level": 2,
                    "boundary_manifest_provided": True,
                    "transition_reviews_provided": True,
                },
            )
            hazard = payload["cross_page_hazards"][0]
            self.assertEqual(
                hazard["status"], "accepted-reviewed-note-transition"
            )
            self.assertEqual(hazard["transition_review"]["writer"], "Writer A")
            self.assertEqual(
                hazard["transition_review"]["independent_reviewer"],
                "Reviewer B",
            )

    def test_exact_review_accepts_numbered_note_transition(self) -> None:
        markdown = """<!-- page_001 -->
## Item

Body.

① Numbered editorial note.

<!-- page_002 -->
New paragraph after the note.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "report.json"
            reviews = root / "transition-reviews.json"
            target.write_text(markdown, encoding="utf-8")
            boundary = self._write_boundary(root, "structural")
            reviews.write_text(
                json.dumps(
                    self._review_payload(
                        target,
                        boundary,
                        note_kind="numbered-note",
                        previous_text="① Numbered editorial note.",
                        following_text="New paragraph after the note.",
                        boundary_label="structural",
                    )
                ),
                encoding="utf-8",
            )

            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                reviews,
                "--json-report",
                report,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["cross_page_hazards"][0]["status"],
                "accepted-reviewed-note-transition",
            )

    def test_transition_reviews_require_current_boundary_and_target_hashes(
        self,
    ) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
Source: Archive A, box 7.
"""
        cases = ("without-boundary", "stale-target", "stale-boundary")
        for label in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                target = root / "annotated.md"
                report = root / "report.json"
                reviews = root / "transition-reviews.json"
                target.write_text(markdown, encoding="utf-8")
                boundary = self._write_boundary(root)
                review_payload = self._review_payload(target, boundary)
                if label == "stale-target":
                    review_payload["target_sha256"] = "0" * 64
                elif label == "stale-boundary":
                    review_payload["boundary_manifest_sha256"] = "0" * 64
                reviews.write_text(json.dumps(review_payload), encoding="utf-8")
                arguments: list[object] = [
                    target,
                    "--transition-reviews",
                    reviews,
                    "--json-report",
                    report,
                ]
                if label != "without-boundary":
                    arguments[1:1] = ["--boundary-manifest", boundary]

                result = run_script(*arguments)

                self.assertEqual(result.returncode, 1)
                payload = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "invalid-input")
                self.assertTrue(payload["errors"])
                if label == "without-boundary":
                    self.assertIn("requires --boundary-manifest", result.stderr)
                else:
                    self.assertIn("stale transition reviews", result.stderr)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            report = root / "report.json"
            reviews = root / "transition-reviews.json"
            target.write_text(markdown, encoding="utf-8")
            boundary = self._write_boundary(root)
            review_payload = self._review_payload(target, boundary)
            review_payload["schema_version"] = 1.0
            reviews.write_text(json.dumps(review_payload), encoding="utf-8")

            result = run_script(
                target,
                "--boundary-manifest",
                boundary,
                "--transition-reviews",
                reviews,
                "--json-report",
                report,
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "invalid-input")
            self.assertIn("schema_version must be integer 1", result.stderr)

    def test_boundary_hash_and_labels_use_one_byte_snapshot(self) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
Source: Archive A, box 7.
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            reviews = root / "transition-reviews.json"
            report = root / "report.json"
            target.write_text(markdown, encoding="utf-8")
            boundary.write_text(
                json.dumps({"boundaries": {"1": "start", "2": "paragraph"}}),
                encoding="utf-8",
            )
            reviews.write_text(
                json.dumps(
                    self._review_payload(
                        target,
                        boundary,
                        boundary_label="structural",
                    )
                ),
                encoding="utf-8",
            )

            original_read_bytes = Path.read_bytes
            boundary_path = boundary.resolve()
            swapped = False

            def read_then_replace(path: Path) -> bytes:
                nonlocal swapped
                payload = original_read_bytes(path)
                if path == boundary_path and not swapped:
                    swapped = True
                    boundary.write_text(
                        json.dumps(
                            {"boundaries": {"1": "start", "2": "structural"}}
                        ),
                        encoding="utf-8",
                    )
                return payload

            with (
                mock.patch.object(Path, "read_bytes", read_then_replace),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                returncode = audit_title_notes_module.main(
                    [
                        str(target),
                        "--boundary-manifest",
                        str(boundary),
                        "--transition-reviews",
                        str(reviews),
                        "--json-report",
                        str(report),
                    ]
                )

            self.assertTrue(swapped)
            self.assertEqual(returncode, 1)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "invalid-input")
            self.assertEqual(
                payload["cross_page_hazards"][0]["boundary_label"], "paragraph"
            )
            self.assertEqual(payload["cross_page_accepted_count"], 0)
            self.assertEqual(len(payload["unused_transition_reviews"]), 1)

    def test_transition_review_rows_are_strict_independent_and_used(self) -> None:
        markdown = """<!-- page_001 -->
## Item*

Body.

* Editorial note.

<!-- page_002 -->
Source: Archive A, box 7.
"""
        mutations = {
            "unknown-field": lambda payload: payload["reviews"][0].update(
                {"extra": "forbidden"}
            ),
            "duplicate-row": lambda payload: payload["reviews"].append(
                dict(payload["reviews"][0])
            ),
            "empty-writer": lambda payload: payload["reviews"][0].update(
                {"writer": " "}
            ),
            "same-reviewer": lambda payload: payload["reviews"][0].update(
                {"independent_reviewer": "writer a"}
            ),
            "same-reviewer-nfkc": lambda payload: payload["reviews"][0].update(
                {"independent_reviewer": "Ｗｒｉｔｅｒ Ａ"}
            ),
            "empty-visual-evidence": lambda payload: payload["reviews"][0].update(
                {"visual_evidence": ""}
            ),
            "unused-row": lambda payload: payload["reviews"][0].update(
                {"following_text": "Different text."}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                target = root / "annotated.md"
                report = root / "report.json"
                reviews = root / "transition-reviews.json"
                target.write_text(markdown, encoding="utf-8")
                boundary = self._write_boundary(root)
                review_payload = self._review_payload(target, boundary)
                mutate(review_payload)
                reviews.write_text(json.dumps(review_payload), encoding="utf-8")

                result = run_script(
                    target,
                    "--boundary-manifest",
                    boundary,
                    "--transition-reviews",
                    reviews,
                    "--json-report",
                    report,
                )

                self.assertEqual(result.returncode, 1)
                payload = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "invalid-input")
                self.assertTrue(payload["errors"])
                if label == "unused-row":
                    self.assertEqual(len(payload["unused_transition_reviews"]), 1)
                    self.assertIn("unused transition review", result.stderr)

    def test_stale_or_invalid_boundary_shape_fails_closed(self) -> None:
        markdown = """<!-- page_001 -->
## 題名

正文。

<!-- page_002 -->
續文。
"""
        invalid_manifests = {
            "unknown-root-field": {
                "boundaries": {"1": "start", "2": "join"},
                "extra": {},
            },
            "stale-missing-page": {"boundaries": {"1": "start"}},
        }
        for label, manifest in invalid_manifests.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                boundary = root / "boundaries.json"
                boundary.write_text(json.dumps(manifest), encoding="utf-8")
                result, _, report = self._run(
                    root, markdown, "--boundary-manifest", boundary
                )

                self.assertEqual(result.returncode, 1)
                payload = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(payload["status"], "invalid-input")
                self.assertTrue(payload["errors"])

    def test_configurable_heading_level(self) -> None:
        markdown = """<!-- page_001 -->
## 部

### 件名*

正文。

* 題注。
"""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result, _, report = self._run(root, markdown, "--heading-level", 3)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["heading_level"], 3)
            self.assertEqual(payload["main_item_count"], 1)


if __name__ == "__main__":
    unittest.main()
