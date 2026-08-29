from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATE = (
    REPO_ROOT
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
    / "validate_review_evidence.py"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_validator(*arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE), *(str(argument) for argument in arguments)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class ReviewEvidenceValidatorTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, object]]:
        (root / "segments").mkdir()
        (root / "renders").mkdir()
        (root / "evidence").mkdir()
        content = root / "segments" / "p0010-p0020.md"
        render = root / "renders" / "page-014.png"
        evidence = root / "evidence" / "change-014.md"
        content.write_text("current segment\n", encoding="utf-8")
        render.write_bytes(b"not a real image; deterministic fixture")
        evidence.write_text("visually checked against scan 14\n", encoding="utf-8")
        ledger: dict[str, object] = {
            "schema_version": 1,
            "artifact_type": "ocr-review-amendment",
            "status": "independent-pass-after-amendment",
            "scan_range": {"start": 10, "end": 20},
            "writer": "/root/writer",
            "independent_verifier": "/root/verifier",
            "reviewer_differs_from_writer": True,
            "changed_scans": [14],
            "reviewed_scans": [13, 14, 15],
            "current_content_hashes": {
                "segments/p0010-p0020.md": sha256_file(content)
            },
            "changes": [
                {
                    "scan_page": 14,
                    "before": "误字",
                    "after": "正字",
                    "render": {
                        "path": "renders/page-014.png",
                        "sha256": sha256_file(render),
                    },
                    "evidence": {
                        "path": "evidence/change-014.md",
                        "sha256": sha256_file(evidence),
                    },
                }
            ],
            "unresolved_count": 0,
        }
        path = root / "amendment.json"
        path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path, ledger

    def _write(self, path: Path, ledger: dict[str, object]) -> None:
        path.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_valid_ledger_and_shared_root_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ledger, data = self._fixture(root)
            nested = root / "ledgers"
            nested.mkdir()
            relocated = nested / ledger.name
            self._write(relocated, data)

            result = run_validator(relocated, "--root", root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("range=10-20", result.stdout)
            self.assertIn("changed=1 reviewed=3 content_files=1 changes=1", result.stdout)
            self.assertIn("SUMMARY ledgers=1 valid=1 invalid=0", result.stdout)

    def test_rejects_bad_status_stale_hash_and_same_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ledger, original = self._fixture(root)
            cases: list[tuple[str, dict[str, object], str]] = []

            bad_status = copy.deepcopy(original)
            bad_status["status"] = "independent-pass-after-title-note-amendment"
            cases.append(("status", bad_status, "status must equal"))

            stale = copy.deepcopy(original)
            stale["current_content_hashes"]["segments/p0010-p0020.md"] = "0" * 64
            cases.append(("stale", stale, "hash mismatch"))

            same = copy.deepcopy(original)
            same["independent_verifier"] = "／ROOT／WRITER".replace("／", "/")
            cases.append(("same", same, "must identify different reviewers"))

            for label, data, expected in cases:
                with self.subTest(label=label):
                    self._write(ledger, data)
                    result = run_validator(ledger)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)

    def test_rejects_missing_changed_scan_and_neighbour_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ledger, original = self._fixture(root)

            missing_changed = copy.deepcopy(original)
            missing_changed["reviewed_scans"] = [13, 15]
            self._write(ledger, missing_changed)
            result = run_validator(ledger)
            self.assertEqual(result.returncode, 1)
            self.assertIn("does not cover changed scan(s): 14", result.stderr)

            missing_neighbour = copy.deepcopy(original)
            missing_neighbour["reviewed_scans"] = [14]
            self._write(ledger, missing_neighbour)
            result = run_validator(ledger)
            self.assertEqual(result.returncode, 1)
            self.assertIn("neighbour/context scan(s): 13, 15", result.stderr)

    def test_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ledger, _ = self._fixture(root)
            ledger.write_text(
                '{"schema_version":1,"schema_version":1}\n', encoding="utf-8"
            )

            result = run_validator(ledger)

            self.assertEqual(result.returncode, 1)
            self.assertIn("duplicate JSON key: 'schema_version'", result.stderr)

    def test_rejects_unsafe_and_symlink_escape_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ledger, original = self._fixture(root)
            outside = root.parent / f"{root.name}-outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            try:
                traversal = copy.deepcopy(original)
                traversal["current_content_hashes"] = {
                    "../outside.md": sha256_file(outside)
                }
                self._write(ledger, traversal)
                result = run_validator(ledger)
                self.assertEqual(result.returncode, 1)
                self.assertIn("safe canonical relative path", result.stderr)

                escape = root / "evidence" / "escape.md"
                escape.symlink_to(outside)
                symlink = copy.deepcopy(original)
                symlink["changes"][0]["evidence"] = {
                    "path": "evidence/escape.md",
                    "sha256": sha256_file(outside),
                }
                self._write(ledger, symlink)
                result = run_validator(ledger)
                self.assertEqual(result.returncode, 1)
                self.assertIn("escapes the validation root", result.stderr)
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
