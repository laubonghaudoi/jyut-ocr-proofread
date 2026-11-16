#!/usr/bin/env python3
"""The compatibility snapshot owns human decisions, not local pipeline tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_benchmark as sync


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="benchmark-snapshot-check-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.bench, self.skill = root / "bench", root / "skill"
        self.bench.mkdir()
        self.skill.mkdir()
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Snapshot test")
        self.run_git("config", "user.email", "snapshot@example.invalid")
        self.human = {"id": "human-1", "provenance": "human", "page": 1,
                      "type": "golden_page", "value": "sample/page-0001.md",
                      "note": "Confirmed reference", "ratchets": {"phase3": {"maxErrors": 2}}}
        self.case = {"bookId": "sample", "book": "Sample", "source": {"pdf": "/original.pdf"},
                     "assertions": [dict(self.human)]}
        self.write(self.bench / "data/cases/sample.json", self.case)
        self.write(self.bench / "data/policies/variant-equivalents.json", {"classes": []})
        self.write(self.bench / "data/golden/sample/page-0001.md", "甲乙。\n")
        self.write_manifest()
        self.commit = self.commit_source()
        local = dict(self.case)
        local["assertions"] = [
            {"id": "machine-1", "provenance": "machine", "page": 1, "type": "contains", "value": "甲"},
            dict(self.human),
            {"id": "regression-1", "provenance": "regression", "page": 1, "type": "absent", "value": "丙"},
        ]
        self.write(self.skill / "tests/cases/sample.json", local)
        self.local = local

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode() if isinstance(value, str) else sync.json_bytes(value))

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.bench), *args], stderr=subprocess.DEVNULL).decode().strip()

    def write_manifest(self):
        files = [{"path": str(p.relative_to(self.bench)), "sha256": sync.sha256(p.read_bytes()), "role": "test"}
                 for p in sorted((self.bench / "data").rglob("*"))
                 if p.is_file() and p.name != "manifest.json"]
        self.write(self.bench / "data/manifest.json", {
            "schema_version": 1, "dataset_version": "0.1.0",
            "books": [{"id": "sample", "case": "data/cases/sample.json", "pages": []}], "files": files})

    def commit_source(self):
        self.run_git("add", ".")
        self.run_git("commit", "-qm", "snapshot")
        return self.run_git("rev-parse", "HEAD")

    def run_sync(self, **kwargs):
        return sync.synchronize(self.bench, self.skill, self.commit, **kwargs)

    def test_sync_is_idempotent_and_preserves_local_metadata_order_and_ratchets(self):
        local = self.local
        local["assertions"][1]["ratchets"] = {"phase3": {"maxErrors": 9}}
        self.write(self.skill / "tests/cases/sample.json", local)
        original = (self.skill / "tests/cases/sample.json").read_bytes()
        lock = self.run_sync()
        self.assertEqual((self.skill / "tests/cases/sample.json").read_bytes(), original)
        self.assertEqual(self.run_sync(check=True), lock)
        before = {str(p): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()}
        self.run_sync()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()})

    def test_nonhuman_and_ratchet_edits_do_not_invalidate_lock(self):
        self.run_sync()
        local = sync.read_json(self.skill / "tests/cases/sample.json")
        local["assertions"][0]["value"] = "乙"
        local["assertions"][1]["ratchets"] = {"repaired": {"maxErrors": 0}}
        self.write(self.skill / "tests/cases/sample.json", local)
        self.run_sync(check=True)
        self.run_sync()
        self.assertEqual(sync.read_json(self.skill / "tests/cases/sample.json"), local)

    def test_local_human_edits_are_never_silently_overwritten(self):
        self.run_sync()
        local = sync.read_json(self.skill / "tests/cases/sample.json")
        local["assertions"][1]["note"] = "new unexported decision"
        self.write(self.skill / "tests/cases/sample.json", local)
        for check in [True, False]:
            with self.assertRaisesRegex(sync.SnapshotError, "human decisions changed"):
                self.run_sync(check=check)
        self.assertEqual(sync.read_json(self.skill / "tests/cases/sample.json"), local)

    def test_source_hash_or_commit_mismatch_never_writes(self):
        before = (self.skill / "tests/cases/sample.json").read_bytes()
        with self.assertRaisesRegex(sync.SnapshotError, "HEAD"):
            sync.synchronize(self.bench, self.skill, "0" * 40)
        self.write(self.bench / "data/golden/sample/page-0001.md", "changed")
        with self.assertRaisesRegex(sync.SnapshotError, "hash mismatch"):
            self.run_sync()
        self.assertEqual((self.skill / "tests/cases/sample.json").read_bytes(), before)
        self.assertFalse((self.skill / "tests/benchmark.lock.json").exists())

    def test_matching_hash_in_dirty_manifest_is_rejected(self):
        self.write(self.bench / "data/golden/sample/page-0001.md", "changed")
        self.write_manifest()
        with self.assertRaisesRegex(sync.SnapshotError, "manifest differs"):
            self.run_sync()
        self.assertFalse((self.skill / "tests/benchmark.lock.json").exists())

    def test_policy_and_golden_tampering_invalidates_the_snapshot(self):
        self.run_sync()
        for relative in ["references/variant-equivalents.json", "tests/golden/sample/page-0001.md"]:
            path = self.skill / relative
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(sync.SnapshotError, "managed file changed"):
                self.run_sync(check=True)
            path.write_bytes(original)

    def test_existing_temporary_file_fails_before_any_write(self):
        self.write(self.skill / "references/variant-equivalents.json.benchmark-tmp", "unrelated data")
        before = {str(p): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()}
        with self.assertRaisesRegex(sync.SnapshotError, "temporary file already exists"):
            self.run_sync()
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.skill.rglob("*") if p.is_file()})

    def test_committed_new_human_decision_syncs_without_touching_local_regressions(self):
        self.run_sync()
        self.case["assertions"][0]["note"] = "new accepted decision"
        self.case["assertions"][0]["ratchets"] = {"phase3": {"maxErrors": 888}}
        self.write(self.bench / "data/cases/sample.json", self.case)
        self.write_manifest()
        self.commit = self.commit_source()
        self.run_sync()
        local = sync.read_json(self.skill / "tests/cases/sample.json")
        self.assertEqual(local["assertions"][1]["note"], "new accepted decision")
        self.assertEqual(local["assertions"][1]["ratchets"], {"phase3": {"maxErrors": 2}})
        self.assertEqual(local["assertions"][0], self.local["assertions"][0])
        self.assertEqual(local["assertions"][2], self.local["assertions"][2])
        self.assertEqual(local["source"], self.local["source"])

    def test_unsafe_paths_and_nonhuman_collisions_are_rejected(self):
        with self.assertRaises(sync.SnapshotError):
            sync.safe_path(self.skill, "../escape")
        bad = dict(self.case)
        bad["assertions"] = [dict(self.human, id="machine-1")]
        with self.assertRaisesRegex(sync.SnapshotError, "collision"):
            sync.merge_case(self.local, bad)


if __name__ == "__main__":
    unittest.main()
