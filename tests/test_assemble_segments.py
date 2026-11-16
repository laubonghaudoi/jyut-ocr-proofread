from __future__ import annotations

import copy
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
ASSEMBLE = SCRIPTS / "assemble_ocr_segments.py"


def run_script(script: Path, *arguments: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *(str(argument) for argument in arguments)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


class AssembleOcrSegmentsTests(unittest.TestCase):
    def _page_manifest(
        self, root: Path, count: int, *, marker_expected: set[int] | None = None
    ) -> Path:
        expected = (
            marker_expected
            if marker_expected is not None
            else set(range(1, count + 1))
        )
        path = root / "page-manifest.json"
        path.write_text(
            json.dumps(
                {
                    "source_sha256": "0" * 64,
                    "expected_scan_pages": count,
                    "pages": [
                        {
                            "scan_page": scan,
                            "render_file": f"renders/p-{scan:03d}.png",
                            "render_sha256": "0" * 64,
                            "marker_expected": scan in expected,
                        }
                        for scan in range(1, count + 1)
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _manifest(self, root: Path, entries: list[tuple[str, bytes]]) -> Path:
        segments = root / "segments"
        segments.mkdir()
        page_manifest = self._page_manifest(root, len(entries))
        records = []
        for scan, (name, payload) in enumerate(entries, start=1):
            path = segments / name
            path.write_bytes(payload)
            records.append(
                {
                    "path": f"segments/{name}",
                    "sha256": sha256_bytes(payload),
                    "scan_start": scan,
                    "scan_end": scan,
                }
            )
        manifest = root / "ordered-segments.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "separator_utf8": "\n",
                    "scan_start": 1,
                    "scan_end": len(records),
                    "page_manifest": {
                        "path": page_manifest.name,
                        "sha256": sha256_file(page_manifest),
                    },
                    "segments": records,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_dry_run_then_exact_write_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = b"<!-- page_001 -->\r\n\xe6\xad\xa3\xe6\x96\x87"
            second = b"\n<!-- page_002 -->\nend\x00"
            manifest = self._manifest(root, [("one.md", first), ("two.md", second)])
            output = root / "combined.md"
            receipt = root / "assembly-receipt.json"

            dry_run = run_script(ASSEMBLE, manifest, "--output", output, "--json")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            dry_report = json.loads(dry_run.stdout)
            self.assertEqual(dry_report["mode"], "dry-run")
            self.assertFalse(dry_report["written"])
            self.assertFalse(output.exists())

            expected = first + b"\n" + second
            output.write_bytes(expected)
            replay = run_script(ASSEMBLE, manifest, "--output", output, "--json")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertTrue(json.loads(replay.stdout)["output_verified"])

            output.write_bytes(expected + b"drift")
            mismatch = run_script(ASSEMBLE, manifest, "--output", output)
            self.assertEqual(mismatch.returncode, 1)
            self.assertIn("does not byte-match replayed assembly", mismatch.stderr)

            written = run_script(
                ASSEMBLE,
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--write",
                "--json",
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            report = json.loads(written.stdout)
            self.assertTrue(report["ok"])
            self.assertTrue(report["written"])
            self.assertEqual(output.read_bytes(), expected)
            self.assertEqual(report["output_sha256"], sha256_bytes(expected))
            self.assertTrue(report["output_verified"])
            receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["output"]["sha256"], sha256_file(output))
            self.assertEqual(
                receipt_data["page_manifest"]["sha256"],
                sha256_file(root / "page-manifest.json"),
            )
            self.assertEqual(receipt_data["tool"]["name"], ASSEMBLE.name)
            self.assertEqual(receipt_data["tool"]["sha256"], sha256_file(ASSEMBLE))
            self.assertEqual(
                receipt_data["invocation_profile"],
                {
                    "operation": "assemble-ocr-segments",
                    "profile_version": 1,
                    "receipt": True,
                    "report_format": "json",
                    "write": True,
                },
            )
            self.assertNotIn(
                str(root), json.dumps(receipt_data["invocation_profile"])
            )
            self.assertEqual(
                [entry["path"] for entry in receipt_data["segments"]],
                ["segments/one.md", "segments/two.md"],
            )

    def test_rejects_duplicate_hash_drift_traversal_and_output_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = self._manifest(
                root,
                [
                    ("one.md", b"<!-- page_001 -->\none"),
                    ("two.md", b"<!-- page_002 -->\ntwo"),
                ],
            )
            original = json.loads(manifest.read_text(encoding="utf-8"))

            cases: list[tuple[str, dict[str, object], str, Path]] = []
            duplicate = copy.deepcopy(original)
            duplicate["segments"][1]["path"] = duplicate["segments"][0]["path"]
            duplicate["segments"][1]["sha256"] = duplicate["segments"][0]["sha256"]
            cases.append(("duplicate", duplicate, "duplicates an earlier file", root / "out-a"))
            drift = copy.deepcopy(original)
            drift["segments"][0]["sha256"] = "0" * 64
            cases.append(("hash", drift, "hash mismatch", root / "out-b"))
            traversal = copy.deepcopy(original)
            traversal["segments"][0]["path"] = "../outside.md"
            cases.append(("traversal", traversal, "contains '..'", root / "out-c"))
            gap = copy.deepcopy(original)
            gap["segments"][1]["scan_start"] = 3
            gap["segments"][1]["scan_end"] = 3
            cases.append(("range-gap", gap, "has a gap", root / "out-d"))
            overlap = copy.deepcopy(original)
            overlap["segments"][1]["scan_start"] = 1
            cases.append(
                (
                    "range-overlap",
                    overlap,
                    "overlaps or is out of order",
                    root / "out-e",
                )
            )

            for label, data, expected, output in cases:
                with self.subTest(label=label):
                    manifest.write_text(json.dumps(data), encoding="utf-8")
                    result = run_script(
                        ASSEMBLE, manifest, "--output", output, "--write"
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)
                    self.assertFalse(output.exists())

            manifest.write_text(json.dumps(original), encoding="utf-8")
            segment = root / "segments" / "one.md"
            before = segment.read_bytes()
            alias = run_script(
                ASSEMBLE, manifest, "--output", segment, "--write"
            )
            self.assertEqual(alias.returncode, 1)
            self.assertIn("aliases segment", alias.stderr)
            self.assertEqual(segment.read_bytes(), before)

    def test_rejects_symlink_escape_and_hard_link_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root.parent / f"{root.name}-outside.md"
            outside.write_bytes(b"outside")
            try:
                (root / "segments").mkdir()
                page_manifest = self._page_manifest(root, 2)
                (root / "segments" / "escape.md").symlink_to(outside)
                manifest = root / "ordered-segments.json"
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "separator_utf8": "\n",
                            "scan_start": 1,
                            "scan_end": 1,
                            "page_manifest": {
                                "path": page_manifest.name,
                                "sha256": sha256_file(page_manifest),
                            },
                            "segments": [
                                {
                                    "path": "segments/escape.md",
                                    "sha256": sha256_file(outside),
                                    "scan_start": 1,
                                    "scan_end": 1,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                escaped = run_script(ASSEMBLE, manifest)
                self.assertEqual(escaped.returncode, 1)
                self.assertIn("escapes the manifest directory", escaped.stderr)

                real = root / "segments" / "real.md"
                linked = root / "segments" / "linked.md"
                real.write_bytes(b"<!-- page_001 -->\nsame inode")
                os.link(real, linked)
                manifest.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "separator_utf8": "",
                            "scan_start": 1,
                            "scan_end": 2,
                            "page_manifest": {
                                "path": page_manifest.name,
                                "sha256": sha256_file(page_manifest),
                            },
                            "segments": [
                                {
                                    "path": "segments/real.md",
                                    "sha256": sha256_file(real),
                                    "scan_start": 1,
                                    "scan_end": 1,
                                },
                                {
                                    "path": "segments/linked.md",
                                    "sha256": sha256_file(linked),
                                    "scan_start": 2,
                                    "scan_end": 2,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                duplicate = run_script(ASSEMBLE, manifest)
                self.assertEqual(duplicate.returncode, 1)
                self.assertIn("duplicates an earlier file", duplicate.stderr)
            finally:
                outside.unlink(missing_ok=True)

    def test_page_manifest_marker_policy_is_exact_per_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = self._manifest(
                root,
                [
                    ("one.md", b"<!-- page_001 -->\none"),
                    ("two.md", b"<!-- page_002 -->\ntwo"),
                ],
            )
            original = json.loads(manifest.read_text(encoding="utf-8"))
            first = root / "segments" / "one.md"

            unsealed = copy.deepcopy(original)
            del unsealed["page_manifest"]
            manifest.write_text(json.dumps(unsealed), encoding="utf-8")
            missing_contract = run_script(ASSEMBLE, manifest)
            self.assertEqual(missing_contract.returncode, 1)
            self.assertIn(
                "missing manifest field(s): page_manifest", missing_contract.stderr
            )

            cases = [
                ("missing", b"one", "missing=1"),
                (
                    "extra",
                    b"<!-- page_001 -->\n<!-- page_002 -->\none",
                    "extra=2",
                ),
                ("malformed", b"<!-- page_01 -->\none", "malformed page marker"),
                (
                    "duplicate",
                    b"<!-- page_001 -->\n<!-- page_001 -->\none",
                    "markers are duplicated or out of order",
                ),
            ]
            for label, payload, expected in cases:
                with self.subTest(label=label):
                    first.write_bytes(payload)
                    changed = copy.deepcopy(original)
                    changed["segments"][0]["sha256"] = sha256_file(first)
                    manifest.write_text(json.dumps(changed), encoding="utf-8")
                    result = run_script(ASSEMBLE, manifest)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)

            page_manifest = root / "page-manifest.json"
            page_data = json.loads(page_manifest.read_text(encoding="utf-8"))
            page_data["pages"][1]["marker_expected"] = False
            page_manifest.write_text(json.dumps(page_data), encoding="utf-8")
            first.write_bytes(b"<!-- page_001 -->\none")
            second = root / "segments" / "two.md"
            second.write_bytes(b"two")
            accepted = copy.deepcopy(original)
            accepted["page_manifest"]["sha256"] = sha256_file(page_manifest)
            accepted["segments"][0]["sha256"] = sha256_file(first)
            accepted["segments"][1]["sha256"] = sha256_file(second)
            manifest.write_text(json.dumps(accepted), encoding="utf-8")
            result = run_script(ASSEMBLE, manifest)
            self.assertEqual(result.returncode, 0, result.stderr)

            accepted["page_manifest"]["sha256"] = "0" * 64
            manifest.write_text(json.dumps(accepted), encoding="utf-8")
            stale = run_script(ASSEMBLE, manifest)
            self.assertEqual(stale.returncode, 1)
            self.assertIn("page_manifest hash mismatch", stale.stderr)

    def test_separator_cannot_inject_text_or_break_marker_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            manifest = self._manifest(
                root,
                [
                    ("one.md", b"<!-- page_001 -->\none"),
                    ("two.md", b"<!-- page_002 -->\ntwo"),
                ],
            )
            data = json.loads(manifest.read_text(encoding="utf-8"))

            data["separator_utf8"] = "\nUNREVIEWED\n"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            injected = run_script(ASSEMBLE, manifest)
            self.assertEqual(injected.returncode, 1)
            self.assertIn("may contain only LF newlines", injected.stderr)

            data["separator_utf8"] = ""
            manifest.write_text(json.dumps(data), encoding="utf-8")
            fused = run_script(ASSEMBLE, manifest)
            self.assertEqual(fused.returncode, 1)
            self.assertIn("assembled output contains malformed", fused.stderr)

    def test_assembler_schema_version_requires_json_integer(self) -> None:
        for version in (True, 1.0):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory)
                manifest = self._manifest(
                    root, [("one.md", b"<!-- page_001 -->\none")]
                )
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["schema_version"] = version
                manifest.write_text(json.dumps(data), encoding="utf-8")
                result = run_script(ASSEMBLE, manifest)
                self.assertEqual(result.returncode, 1)
                self.assertIn("schema_version must equal 1", result.stderr)


if __name__ == "__main__":
    unittest.main()
