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
    / "plugins"
    / "jyut-ocr-proofread"
    / "skills"
    / "jyut-ocr-proofread"
    / "scripts"
)
ASSEMBLE = SCRIPTS / "assemble_ocr_segments.py"
VALIDATE = SCRIPTS / "validate_release_evidence.py"


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


def canonical_subset_hash(document: object, *pointers: str) -> str:
    def lookup(pointer: str) -> object:
        current = document
        for raw_token in pointer[1:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:  # pragma: no cover - fixture programming error
                raise AssertionError(pointer)
        return current

    selected = [
        {"pointer": pointer, "value": lookup(pointer)}
        for pointer in sorted(pointers)
    ]
    encoded = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(encoded)


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


class ValidateReleaseEvidenceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, dict[str, object]]:
        evidence = root / "evidence"
        evidence.mkdir()
        review_data = {
            "review_status": "passing",
            "decisions": ["page-1", "page-2"],
            "mutable_receipt": {"sha256": "not-part-of-canonical-subset"},
        }
        review = evidence / "review.json"
        review.write_text(
            json.dumps(review_data, ensure_ascii=False), encoding="utf-8"
        )
        scratch = evidence / "scratch.json"
        scratch.write_text('{"status":"provisional"}\n', encoding="utf-8")
        final = root / "final.md"
        final.write_text("# 題名\n\n正文。\n", encoding="utf-8")
        plan: dict[str, object] = {
            "schema_version": 1,
            "discovery": {"globs": ["evidence/*.json", "*.md"]},
            "release_roots": ["final"],
            "artifacts": [
                {
                    "id": "review",
                    "path": "evidence/review.json",
                    "classification": "include",
                    "sha256": sha256_file(review),
                    "type": "toc-item-ledger",
                    "lifecycle": "active",
                    "validation": "pass",
                    "depends_on": [],
                },
                {
                    "id": "scratch",
                    "path": "evidence/scratch.json",
                    "classification": "exclude",
                    "sha256": sha256_file(scratch),
                    "type": "review-ledger",
                    "lifecycle": "historical",
                    "validation": "provisional",
                    "depends_on": [],
                    "exclusion_reason": (
                        "provisional working copy; superseded before release"
                    ),
                },
                {
                    "id": "final",
                    "path": "final.md",
                    "classification": "include",
                    "sha256": sha256_file(final),
                    "type": "final-output",
                    "singleton_role": "final-output",
                    "lifecycle": "active",
                    "validation": "pass",
                    "depends_on": [
                        {
                            "target": "review",
                            "relation": "verified-by",
                            "hash": {
                                "mode": "canonical-subset",
                                "pointers": ["/review_status", "/decisions"],
                                "sha256": canonical_subset_hash(
                                    review_data, "/review_status", "/decisions"
                                ),
                            },
                        }
                    ],
                },
            ],
            "reconciliation_scope": {
                "id": "upper-volume-toc",
                "unit": "toc-item",
                "source_count": 20,
                "included_count": 12,
                "evidence_ids": {
                    "toc_item_ledger": "review",
                    "body_heading_inventory": "body-headings",
                    "reconciliation_ledger": "reconciliation",
                    "variant_challenge_ledger": "variant-challenges",
                    "invariants_qa": "invariants",
                },
                "partitions": [
                    {
                        "id": "upper-volume",
                        "start": 1,
                        "end": 12,
                        "disposition": "in-scope",
                        "reconciled_count": 12,
                    },
                    {
                        "id": "lower-volume",
                        "start": 13,
                        "end": 20,
                        "disposition": "out-of-scope",
                        "reason": "belongs to the separately released lower volume",
                    },
                ],
            },
        }
        extra_evidence = (
            ("body-headings", "body-heading-inventory"),
            ("reconciliation", "toc-body-reconciliation-ledger"),
            ("variant-challenges", "variant-challenge-ledger"),
            ("invariants", "invariants-qa"),
        )
        for artifact_id, artifact_type in extra_evidence:
            artifact_path = evidence / f"{artifact_id}.json"
            artifact_path.write_text(
                json.dumps({"status": "pass", "id": artifact_id}),
                encoding="utf-8",
            )
            artifact = {
                "id": artifact_id,
                "path": f"evidence/{artifact_path.name}",
                "classification": "include",
                "sha256": sha256_file(artifact_path),
                "type": artifact_type,
                "lifecycle": "active",
                "validation": "pass",
                "depends_on": [],
            }
            plan["artifacts"].append(artifact)
            plan["artifacts"][2]["depends_on"].append(
                {
                    "target": artifact_id,
                    "relation": "verified-by",
                    "hash": {
                        "mode": "full",
                        "sha256": sha256_file(artifact_path),
                    },
                }
            )
        plan_path = root / "release-plan.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return plan_path, plan

    def _write_plan(self, path: Path, plan: dict[str, object]) -> None:
        path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def test_valid_closed_plan_with_canonical_subset_and_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path, plan = self._fixture(Path(temporary_directory))

            result = run_script(VALIDATE, plan_path, "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schema_version"], 1)
            self.assertTrue(report["ok"])
            self.assertEqual(report["plan"], plan_path.name)
            self.assertEqual(report["plan_sha256"], sha256_file(plan_path))
            self.assertEqual(report["tool"]["name"], VALIDATE.name)
            self.assertEqual(report["tool"]["sha256"], sha256_file(VALIDATE))
            self.assertEqual(report["discovered_count"], 7)
            self.assertEqual(report["included_count"], 6)
            self.assertEqual(report["excluded_count"], 1)

            repeated = run_script(VALIDATE, plan_path, "--json")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout), report)

            del plan["reconciliation_scope"]
            self._write_plan(plan_path, plan)
            missing_scope = run_script(VALIDATE, plan_path)
            self.assertEqual(missing_scope.returncode, 1)
            self.assertIn(
                "reconciliation_scope is required", missing_scope.stderr
            )

            for artifact in plan["artifacts"]:
                if artifact["type"] in {
                    "toc-item-ledger",
                    "body-heading-inventory",
                    "toc-body-reconciliation-ledger",
                    "variant-challenge-ledger",
                    "invariants-qa",
                }:
                    artifact["type"] = "general-release-evidence"
            self._write_plan(plan_path, plan)
            no_toc = run_script(VALIDATE, plan_path)
            self.assertEqual(no_toc.returncode, 0, no_toc.stderr)

    def test_overlapping_discovery_globs_dedupe_the_same_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path, plan = self._fixture(Path(temporary_directory))
            plan["discovery"]["globs"].append("**/*.json")
            self._write_plan(plan_path, plan)

            result = run_script(VALIDATE, plan_path, "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["discovered_count"], 7)

    def test_pointer_ancestor_check_uses_decoded_token_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original = self._fixture(root)
            review_path = root / "evidence" / "review.json"
            review_data = {
                "": "empty-key",
                "foo": "ordinary-key",
                "a": {"": "nested-empty-key", "b": "nested-ordinary-key"},
            }
            review_path.write_text(
                json.dumps(review_data, ensure_ascii=False), encoding="utf-8"
            )

            for pointers in (("/", "/foo"), ("/a/", "/a/b")):
                with self.subTest(pointers=pointers):
                    plan = copy.deepcopy(original)
                    plan["artifacts"][0]["sha256"] = sha256_file(review_path)
                    contract = plan["artifacts"][2]["depends_on"][0]["hash"]
                    contract["pointers"] = list(pointers)
                    contract["sha256"] = canonical_subset_hash(
                        review_data, *pointers
                    )
                    self._write_plan(plan_path, plan)
                    result = run_script(VALIDATE, plan_path)
                    self.assertEqual(result.returncode, 0, result.stderr)

            ancestor = copy.deepcopy(original)
            ancestor["artifacts"][0]["sha256"] = sha256_file(review_path)
            contract = ancestor["artifacts"][2]["depends_on"][0]["hash"]
            contract["pointers"] = ["/a", "/a/b"]
            contract["sha256"] = canonical_subset_hash(
                review_data, "/a", "/a/b"
            )
            self._write_plan(plan_path, ancestor)
            rejected = run_script(VALIDATE, plan_path)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("ancestor/descendant selections", rejected.stderr)

    def test_unclassified_artifact_and_unreasoned_exclusion_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, plan = self._fixture(root)
            extra = root / "evidence" / "late-qa.json"
            extra.write_text('{"status":"passing"}\n', encoding="utf-8")

            unclassified = run_script(VALIDATE, plan_path)
            self.assertEqual(unclassified.returncode, 1)
            self.assertIn("discovered artifact is unclassified", unclassified.stderr)
            self.assertIn("late-qa.json", unclassified.stderr)

            extra.unlink()
            del plan["artifacts"][1]["exclusion_reason"]
            self._write_plan(plan_path, plan)
            unreasoned = run_script(VALIDATE, plan_path)
            self.assertEqual(unreasoned.returncode, 1)
            self.assertIn("exclusion_reason is required", unreasoned.stderr)

    def test_dependency_closure_validation_and_subset_hash_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original = self._fixture(root)
            cases: list[tuple[str, dict[str, object], str]] = []

            missing = copy.deepcopy(original)
            missing["artifacts"][2]["depends_on"][0]["target"] = "missing-review"
            cases.append(("missing", missing, "targets missing evidence"))

            provisional = copy.deepcopy(original)
            provisional["artifacts"][0]["validation"] = "provisional"
            cases.append(
                (
                    "provisional",
                    provisional,
                    "included evidence must be lifecycle active and validation pass",
                )
            )

            bad_subset = copy.deepcopy(original)
            bad_subset["artifacts"][2]["depends_on"][0]["hash"]["sha256"] = "0" * 64
            cases.append(("subset", bad_subset, "depends_on[0].hash mismatch"))

            orphan = copy.deepcopy(original)
            orphan["artifacts"][2]["depends_on"] = []
            cases.append(("orphan", orphan, "outside release-root closure:"))

            bad_root = copy.deepcopy(original)
            bad_root["release_roots"] = ["scratch"]
            cases.append(
                (
                    "excluded-root",
                    bad_root,
                    "release root 'scratch' must target active included evidence",
                )
            )

            for label, plan, expected in cases:
                with self.subTest(label=label):
                    self._write_plan(plan_path, plan)
                    result = run_script(VALIDATE, plan_path)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)

    def test_dependency_cycle_and_reconciliation_gap_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original = self._fixture(root)

            cyclic = copy.deepcopy(original)
            cyclic["artifacts"][0]["depends_on"] = [
                {
                    "target": "final",
                    "relation": "sealed-by",
                    "hash": {
                        "mode": "full",
                        "sha256": cyclic["artifacts"][2]["sha256"],
                    },
                }
            ]
            self._write_plan(plan_path, cyclic)
            cycle_result = run_script(VALIDATE, plan_path)
            self.assertEqual(cycle_result.returncode, 1)
            self.assertIn("dependency graph contains a cycle", cycle_result.stderr)

            gap = copy.deepcopy(original)
            gap["reconciliation_scope"]["partitions"][1]["start"] = 14
            self._write_plan(plan_path, gap)
            gap_result = run_script(VALIDATE, plan_path)
            self.assertEqual(gap_result.returncode, 1)
            self.assertIn("exact ordered coverage requires 13", gap_result.stderr)

    def test_full_hash_dependency_and_superseded_target_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, plan = self._fixture(root)
            review_hash = plan["artifacts"][0]["sha256"]
            plan["artifacts"][2]["depends_on"][0]["hash"] = {
                "mode": "full",
                "sha256": review_hash,
            }
            self._write_plan(plan_path, plan)
            full = run_script(VALIDATE, plan_path)
            self.assertEqual(full.returncode, 0, full.stderr)

            plan["artifacts"][1]["lifecycle"] = "superseded"
            plan["artifacts"][1]["superseded_by"] = "review"
            self._write_plan(plan_path, plan)
            superseded = run_script(VALIDATE, plan_path)
            self.assertEqual(superseded.returncode, 0, superseded.stderr)

            plan["artifacts"][1]["superseded_by"] = "scratch"
            self._write_plan(plan_path, plan)
            bad_target = run_script(VALIDATE, plan_path)
            self.assertEqual(bad_target.returncode, 1)
            self.assertIn("must target active included evidence", bad_target.stderr)
            self.assertIn("supersession graph contains a cycle", bad_target.stderr)

            plan["artifacts"][1]["lifecycle"] = "historical"
            plan["artifacts"][1]["superseded_by"] = "review"
            self._write_plan(plan_path, plan)
            historical_edge = run_script(VALIDATE, plan_path)
            self.assertEqual(historical_edge.returncode, 1)
            self.assertIn(
                "superseded_by is only allowed when lifecycle is superseded",
                historical_edge.stderr,
            )

    def test_reconciliation_scope_requires_typed_reachable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original = self._fixture(root)
            cases: list[tuple[str, dict[str, object], str]] = []

            missing_mapping = copy.deepcopy(original)
            del missing_mapping["reconciliation_scope"]["evidence_ids"]
            cases.append(
                (
                    "missing-mapping",
                    missing_mapping,
                    "reconciliation_scope is missing field(s): evidence_ids",
                )
            )

            wrong_type = copy.deepcopy(original)
            wrong_type["artifacts"][0]["type"] = "review-ledger"
            cases.append(
                (
                    "wrong-type",
                    wrong_type,
                    "must target artifact type 'toc-item-ledger'",
                )
            )

            inactive = copy.deepcopy(original)
            inactive["artifacts"][3]["validation"] = "provisional"
            cases.append(
                (
                    "inactive",
                    inactive,
                    "evidence_ids.body_heading_inventory must target active included",
                )
            )

            unreachable = copy.deepcopy(original)
            unreachable["artifacts"][2]["depends_on"] = [
                dependency
                for dependency in unreachable["artifacts"][2]["depends_on"]
                if dependency["target"] != "invariants"
            ]
            cases.append(
                (
                    "unreachable",
                    unreachable,
                    "evidence_ids.invariants_qa must be reachable from release_roots",
                )
            )

            for label, plan, expected in cases:
                with self.subTest(label=label):
                    self._write_plan(plan_path, plan)
                    result = run_script(VALIDATE, plan_path)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stderr)

    def test_singleton_roles_are_unique_but_optional_for_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, plan = self._fixture(root)

            no_role = copy.deepcopy(plan)
            del no_role["artifacts"][2]["singleton_role"]
            self._write_plan(plan_path, no_role)
            compatible = run_script(VALIDATE, plan_path)
            self.assertEqual(compatible.returncode, 0, compatible.stderr)

            second_output = root / "second.md"
            second_output.write_text("# 第二份\n", encoding="utf-8")
            duplicate = copy.deepcopy(plan)
            duplicate["artifacts"].append(
                {
                    "id": "second-final",
                    "type": "final-output",
                    "singleton_role": "final-output",
                    "path": second_output.name,
                    "classification": "include",
                    "lifecycle": "active",
                    "validation": "pass",
                    "sha256": sha256_file(second_output),
                    "depends_on": copy.deepcopy(
                        duplicate["artifacts"][2]["depends_on"]
                    ),
                }
            )
            duplicate["release_roots"].append("second-final")
            self._write_plan(plan_path, duplicate)
            rejected = run_script(VALIDATE, plan_path)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("singleton_role 'final-output' duplicates", rejected.stderr)
            self.assertIn(
                "release_roots may contain at most one artifact with singleton_role",
                rejected.stderr,
            )

    def test_validator_schema_version_requires_json_integer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            plan_path, original = self._fixture(Path(temporary_directory))
            for version in (True, 1.0):
                with self.subTest(version=version):
                    plan = copy.deepcopy(original)
                    plan["schema_version"] = version
                    self._write_plan(plan_path, plan)
                    result = run_script(VALIDATE, plan_path)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("schema_version must equal 1", result.stderr)

    def test_discovery_preserves_case_distinct_files_and_rejects_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            upper = root / "A.json"
            lower = root / "a.json"
            upper.write_text('{"case":"upper"}\n', encoding="utf-8")
            lower.write_text('{"case":"lower"}\n', encoding="utf-8")
            if not upper.samefile(lower):
                final = root / "final.md"
                final.write_text("# Final\n", encoding="utf-8")
                plan = {
                    "schema_version": 1,
                    "discovery": {"globs": ["*.json", "*.md"]},
                    "release_roots": ["final"],
                    "artifacts": [
                        {
                            "id": "upper",
                            "type": "review-ledger",
                            "path": upper.name,
                            "classification": "include",
                            "lifecycle": "active",
                            "validation": "pass",
                            "sha256": sha256_file(upper),
                            "depends_on": [],
                        },
                        {
                            "id": "lower",
                            "type": "review-ledger",
                            "path": lower.name,
                            "classification": "include",
                            "lifecycle": "active",
                            "validation": "pass",
                            "sha256": sha256_file(lower),
                            "depends_on": [],
                        },
                        {
                            "id": "final",
                            "type": "final-output",
                            "path": final.name,
                            "classification": "include",
                            "lifecycle": "active",
                            "validation": "pass",
                            "sha256": sha256_file(final),
                            "depends_on": [
                                {
                                    "target": artifact_id,
                                    "relation": "verified-by",
                                    "hash": {
                                        "mode": "full",
                                        "sha256": sha256_file(path),
                                    },
                                }
                                for artifact_id, path in (
                                    ("upper", upper),
                                    ("lower", lower),
                                )
                            ],
                        },
                    ],
                }
                plan_path = root / "release-plan.json"
                self._write_plan(plan_path, plan)
                result = run_script(VALIDATE, plan_path)
                self.assertEqual(result.returncode, 0, result.stderr)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "one.json"
            linked = root / "two.json"
            original.write_text('{"same":true}\n', encoding="utf-8")
            os.link(original, linked)
            final = root / "final.md"
            final.write_text("# Final\n", encoding="utf-8")
            plan = {
                "schema_version": 1,
                "discovery": {"globs": ["*.json", "*.md"]},
                "release_roots": ["final"],
                "artifacts": [
                    {
                        "id": "one",
                        "type": "review-ledger",
                        "path": original.name,
                        "classification": "include",
                        "lifecycle": "active",
                        "validation": "pass",
                        "sha256": sha256_file(original),
                        "depends_on": [],
                    },
                    {
                        "id": "two",
                        "type": "review-ledger",
                        "path": linked.name,
                        "classification": "include",
                        "lifecycle": "active",
                        "validation": "pass",
                        "sha256": sha256_file(linked),
                        "depends_on": [],
                    },
                    {
                        "id": "final",
                        "type": "final-output",
                        "path": final.name,
                        "classification": "include",
                        "lifecycle": "active",
                        "validation": "pass",
                        "sha256": sha256_file(final),
                        "depends_on": [],
                    },
                ],
            }
            plan_path = root / "release-plan.json"
            self._write_plan(plan_path, plan)
            result = run_script(VALIDATE, plan_path)
            self.assertEqual(result.returncode, 1)
            self.assertIn("duplicate/hard-link aliases", result.stderr)


if __name__ == "__main__":
    unittest.main()
