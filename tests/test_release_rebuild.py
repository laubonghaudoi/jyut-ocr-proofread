from __future__ import annotations

import hashlib
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
PLAN_REBUILD = SCRIPTS / "plan_release_rebuild.py"
VALIDATE = SCRIPTS / "validate_release_evidence.py"


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


class PlanReleaseRebuildTests(unittest.TestCase):
    def _fixture(
        self,
        root: Path,
        dependencies: dict[str, list[str]] | None = None,
        *,
        roots: list[str] | None = None,
        artifact_order: list[str] | None = None,
    ) -> tuple[Path, dict[str, object], dict[str, Path]]:
        dependencies = dependencies or {
            "source": [],
            "stage": ["source"],
            "final": ["stage"],
        }
        roots = roots or ["final"]
        evidence = root / "evidence"
        evidence.mkdir()
        files: dict[str, Path] = {}
        for artifact_id in dependencies:
            path = evidence / f"{artifact_id}.txt"
            path.write_text(f"sealed {artifact_id}\n", encoding="utf-8")
            files[artifact_id] = path
        old = evidence / "old.txt"
        old.write_text("historical evidence\n", encoding="utf-8")
        files["old"] = old

        artifacts_by_id: dict[str, dict[str, object]] = {}
        for artifact_id, dependency_ids in dependencies.items():
            artifacts_by_id[artifact_id] = {
                "id": artifact_id,
                "type": "release-input",
                "path": f"evidence/{artifact_id}.txt",
                "classification": "include",
                "lifecycle": "active",
                "validation": "pass",
                "sha256": sha256_file(files[artifact_id]),
                "depends_on": [
                    {
                        "target": dependency_id,
                        "relation": "derived-from",
                        "hash": {
                            "mode": "full",
                            "sha256": sha256_file(files[dependency_id]),
                        },
                    }
                    for dependency_id in dependency_ids
                ],
            }
        artifacts_by_id["old"] = {
            "id": "old",
            "type": "review-ledger",
            "path": "evidence/old.txt",
            "classification": "exclude",
            "lifecycle": "historical",
            "validation": "provisional",
            "sha256": sha256_file(old),
            "depends_on": [],
            "exclusion_reason": "superseded working evidence",
        }
        order = artifact_order or [*dependencies, "old"]
        plan: dict[str, object] = {
            "schema_version": 1,
            "discovery": {"globs": ["evidence/*.txt"]},
            "release_roots": roots,
            "artifacts": [artifacts_by_id[artifact_id] for artifact_id in order],
        }
        plan_path = root / "release-plan.json"
        self._write_plan(plan_path, plan)
        return plan_path, plan, files

    def _write_plan(self, path: Path, plan: dict[str, object]) -> None:
        path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_clean_plan_reports_target_and_plan_hashes_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, _, files = self._fixture(root)

            strict_result = run_script(VALIDATE, plan_path)
            self.assertEqual(strict_result.returncode, 0, strict_result.stderr)

            first = run_script(PLAN_REBUILD, plan_path, "--json-report")
            second = run_script(PLAN_REBUILD, plan_path, "--json-report")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(first.stdout, second.stdout)
            report = json.loads(first.stdout)
            self.assertEqual(report["status"], "clean")
            self.assertTrue(report["ok"])
            self.assertEqual(report["plan_sha256"], sha256_file(plan_path))
            self.assertEqual(report["direct_stale"], [])
            self.assertEqual(report["transitive_stale"], [])
            self.assertEqual(report["unaffected"], ["final", "source", "stage"])
            self.assertEqual(report["rebuild_order"], [])
            self.assertEqual([item["id"] for item in report["skipped"]], ["old"])
            targets = {item["id"]: item for item in report["targets"]}
            self.assertEqual(targets["source"]["actual_sha256"], sha256_file(files["source"]))
            self.assertEqual(
                targets["source"]["actual_sha256"],
                targets["source"]["expected_sha256"],
            )

            # Excluded/historical evidence is explicitly skipped, not hashed for
            # release freshness.
            files["old"].unlink()
            skipped_missing = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(skipped_missing.returncode, 0, skipped_missing.stderr)
            self.assertEqual(json.loads(skipped_missing.stdout)["status"], "clean")

            saved_report = root / "rebuild-report.json"
            saved = run_script(
                PLAN_REBUILD, plan_path, "--json-report", saved_report
            )
            self.assertEqual(saved.returncode, 0, saved.stderr)
            self.assertEqual(saved.stdout, "")
            self.assertEqual(
                json.loads(saved_report.read_text(encoding="utf-8"))["status"],
                "clean",
            )

            original_plan = plan_path.read_bytes()
            alias = run_script(
                PLAN_REBUILD, plan_path, "--json-report", plan_path
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("must not alias the release plan", alias.stderr)
            self.assertEqual(plan_path.read_bytes(), original_plan)

            hard_link = root / "plan-hard-link.json"
            hard_link.hardlink_to(plan_path)
            alias = run_script(
                PLAN_REBUILD, plan_path, "--json-report", hard_link
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("must not alias the release plan", alias.stderr)
            self.assertEqual(plan_path.read_bytes(), original_plan)

            case_alias = root / "RELEASE-PLAN.JSON"
            alias = run_script(
                PLAN_REBUILD, plan_path, "--json-report", case_alias
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("must not alias the release plan", alias.stderr)
            self.assertEqual(plan_path.read_bytes(), original_plan)

            artifact_bytes = files["source"].read_bytes()
            artifact_alias = root / "source-hard-link.txt"
            artifact_alias.hardlink_to(files["source"])
            alias = run_script(
                PLAN_REBUILD, plan_path, "--json-report", artifact_alias
            )
            self.assertEqual(alias.returncode, 2)
            self.assertIn("must not alias classified artifact 'source'", alias.stderr)
            self.assertEqual(files["source"].read_bytes(), artifact_bytes)

    def test_direct_mismatch_propagates_to_all_dependents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, _, files = self._fixture(root)
            files["source"].write_text("changed source\n", encoding="utf-8")

            result = run_script(PLAN_REBUILD, plan_path, "--json-report")

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "stale")
            self.assertEqual(report["direct_stale"], ["source"])
            self.assertEqual(report["transitive_stale"], ["final", "stage"])
            self.assertEqual(report["unaffected"], [])
            self.assertEqual(report["rebuild_order"], ["source", "stage", "final"])
            targets = {item["id"]: item for item in report["targets"]}
            self.assertEqual(targets["source"]["reason"], "hash-mismatch")
            self.assertEqual(targets["stage"]["caused_by"], ["source"])
            self.assertEqual(targets["final"]["stale_dependencies"], ["stage"])

    def test_missing_active_artifact_is_directly_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, plan, files = self._fixture(root)
            files["stage"].unlink()

            result = run_script(PLAN_REBUILD, plan_path, "--json-report")

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["direct_stale"], ["stage"])
            self.assertEqual(report["transitive_stale"], ["final"])
            self.assertEqual(report["unaffected"], ["source"])
            self.assertEqual(report["rebuild_order"], ["stage", "final"])
            stage = next(item for item in report["targets"] if item["id"] == "stage")
            self.assertEqual(stage["reason"], "missing")
            self.assertIsNone(stage["actual_sha256"])

            stage_artifact = next(
                item for item in plan["artifacts"] if item["id"] == "stage"
            )
            stage_artifact["path"] = "outside-glob/stage.txt"
            self._write_plan(plan_path, plan)
            outside = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(outside.returncode, 2)
            outside_report = json.loads(outside.stdout)
            self.assertEqual(outside_report["status"], "invalid")
            self.assertTrue(
                any(
                    "missing and outside discovery.globs" in error
                    for error in outside_report["errors"]
                )
            )

            plan["discovery"]["globs"] = ["**/*.txt"]
            self._write_plan(plan_path, plan)
            recursive = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(recursive.returncode, 1, recursive.stderr)
            recursive_report = json.loads(recursive.stdout)
            self.assertEqual(recursive_report["status"], "stale")
            self.assertEqual(recursive_report["direct_stale"], ["stage"])

    def test_branch_rebuild_order_is_dependency_first_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            dependencies = {
                "source": [],
                "branch-a": ["source"],
                "branch-b": ["source"],
                "root": ["branch-b", "branch-a"],
            }
            plan_path, _, files = self._fixture(
                root,
                dependencies,
                roots=["root"],
                artifact_order=["root", "branch-b", "source", "branch-a", "old"],
            )
            files["source"].write_text("changed source\n", encoding="utf-8")

            result = run_script(PLAN_REBUILD, plan_path, "--json-report")

            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(
                report["rebuild_order"],
                ["source", "branch-a", "branch-b", "root"],
            )
            repeated = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(repeated.stdout, result.stdout)

    def test_invalid_schema_and_cycle_fail_closed_with_exit_two(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original, _ = self._fixture(root)

            invalid = json.loads(json.dumps(original))
            invalid["schema_version"] = True
            self._write_plan(plan_path, invalid)
            invalid_result = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(invalid_result.returncode, 2)
            invalid_report = json.loads(invalid_result.stdout)
            self.assertEqual(invalid_report["status"], "invalid")
            self.assertIn("schema_version must equal 1", invalid_report["errors"])

            cyclic = json.loads(json.dumps(original))
            by_id = {item["id"]: item for item in cyclic["artifacts"]}
            by_id["source"]["depends_on"] = [
                {
                    "target": "final",
                    "relation": "derived-from",
                    "hash": {
                        "mode": "full",
                        "sha256": by_id["final"]["sha256"],
                    },
                }
            ]
            self._write_plan(plan_path, cyclic)
            cycle_result = run_script(PLAN_REBUILD, plan_path, "--json-report")
            self.assertEqual(cycle_result.returncode, 2)
            cycle_report = json.loads(cycle_result.stdout)
            self.assertTrue(
                any("dependency graph contains a cycle" in error for error in cycle_report["errors"])
            )

    def test_traversal_and_symlink_escape_are_invalid_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            plan_path, original, files = self._fixture(root)
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            try:
                traversal = json.loads(json.dumps(original))
                source = next(
                    item for item in traversal["artifacts"] if item["id"] == "source"
                )
                source["path"] = "../outside.txt"
                self._write_plan(plan_path, traversal)
                traversal_result = run_script(
                    PLAN_REBUILD, plan_path, "--json-report"
                )
                self.assertEqual(traversal_result.returncode, 2)
                self.assertTrue(
                    any(
                        "safe relative path" in error
                        for error in json.loads(traversal_result.stdout)["errors"]
                    )
                )

                escaped_link = root / "evidence" / "source.txt"
                escaped_link.unlink()
                escaped_link.symlink_to(outside)
                symlink_plan = json.loads(json.dumps(original))
                self._write_plan(plan_path, symlink_plan)
                symlink_result = run_script(
                    PLAN_REBUILD, plan_path, "--json-report"
                )
                self.assertEqual(symlink_result.returncode, 2)
                self.assertTrue(
                    any(
                        "escapes the plan directory" in error
                        for error in json.loads(symlink_result.stdout)["errors"]
                    )
                )
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
