from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


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


def load_script_module(path: Path, module_name: str):
    """Import a script under a private name so race guards can be patched."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not import test module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_release_plan(
    plan_path: Path,
    annotated: Path,
    boundary_manifest: Path,
    *active_artifacts: Path,
) -> Path:
    """Create a minimal valid closure rooted at the annotated assembly."""

    base = plan_path.resolve().parent

    def relative(path: Path) -> str:
        return path.resolve().relative_to(base).as_posix()

    dependencies: list[dict[str, object]] = []
    artifacts: list[dict[str, object]] = []
    inputs = (("boundary", boundary_manifest),) + tuple(
        (f"active-{index}", path)
        for index, path in enumerate(active_artifacts, 1)
    )
    for artifact_id, path in inputs:
        digest = sha256_file(path)
        artifacts.append(
            {
                "id": artifact_id,
                "type": "release-input",
                "path": relative(path),
                "classification": "include",
                "lifecycle": "active",
                "validation": "pass",
                "sha256": digest,
                "depends_on": [],
            }
        )
        dependencies.append(
            {
                "target": artifact_id,
                "relation": "sealed-by",
                "hash": {"mode": "full", "sha256": digest},
            }
        )
    artifacts.append(
        {
            "id": "annotated",
            "type": "annotated-markdown",
            "path": relative(annotated),
            "classification": "include",
            "lifecycle": "active",
            "validation": "pass",
            "sha256": sha256_file(annotated),
            "depends_on": dependencies,
        }
    )
    plan = {
        "schema_version": 1,
        "discovery": {
            "globs": [artifact["path"] for artifact in artifacts]
        },
        "release_roots": ["annotated"],
        "artifacts": artifacts,
    }
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return plan_path


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
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"join"}}',
                encoding="utf-8",
            )
            write_release_plan(release_plan, target, manifest)

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
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--summary-only",
                "--write",
            )

            self.assertEqual(verbose.returncode, 0, verbose.stderr)
            self.assertIn("MARKER ", verbose.stdout)
            self.assertEqual(summarized.returncode, verbose.returncode)
            self.assertNotIn("MARKER ", summarized.stdout)
            self.assertIn("WROTE ", summarized.stdout)
            self.assertIn("removed 2 page marker(s)", summarized.stdout)
            self.assertIn("RECEIPT ", summarized.stdout)
            self.assertEqual(output.read_text(encoding="utf-8"), "正文未完。\n")

    def test_receipt_requires_a_valid_release_plan_without_bypass(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            dry_run = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
            )
            bypass = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--write",
            )

            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(bypass.returncode, 1)
            self.assertIn("--receipt requires --release-plan", bypass.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

            write_release_plan(release_plan, target, boundary)
            bad_plan = json.loads(release_plan.read_text(encoding="utf-8"))
            bad_plan["artifacts"][-1]["sha256"] = "0" * 64
            release_plan.write_text(json.dumps(bad_plan), encoding="utf-8")
            invalid = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(invalid.returncode, 1)
            self.assertIn("release plan validation failed", invalid.stderr)
            self.assertIn("sha256 mismatch", invalid.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

    def test_release_plan_enforces_dedicated_input_and_output_roles(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )

            write_release_plan(release_plan, target, boundary)
            normal = json.loads(release_plan.read_text(encoding="utf-8"))
            boundary_only = {
                **normal,
                "discovery": {"globs": ["boundaries.json"]},
                "release_roots": ["boundary"],
                "artifacts": [normal["artifacts"][0]],
            }
            release_plan.write_text(
                json.dumps(boundary_only), encoding="utf-8"
            )
            missing_target = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(missing_target.returncode, 1)
            self.assertIn(
                "annotated TARGET must be exactly one active included",
                missing_target.stderr,
            )

            annotated_record = normal["artifacts"][-1]
            annotated_record["depends_on"] = []
            annotated_only = {
                **normal,
                "discovery": {"globs": ["annotated.md"]},
                "release_roots": ["annotated"],
                "artifacts": [annotated_record],
            }
            release_plan.write_text(
                json.dumps(annotated_only), encoding="utf-8"
            )
            missing_boundary = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(missing_boundary.returncode, 1)
            self.assertIn(
                "--boundary-manifest must be exactly one active included",
                missing_boundary.stderr,
            )

            old_output = "舊 final。\n"
            output.write_text(old_output, encoding="utf-8")
            write_release_plan(release_plan, target, boundary, output)
            output_is_active = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(output_is_active.returncode, 1)
            self.assertIn(
                "--output must not alias any classified", output_is_active.stderr
            )
            self.assertEqual(output.read_text(encoding="utf-8"), old_output)

            receipt.write_text('{"old":"receipt"}\n', encoding="utf-8")
            generated_output = root / "generated-final.md"
            write_release_plan(release_plan, target, boundary, receipt)
            receipt_is_active = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                generated_output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(receipt_is_active.returncode, 1)
            self.assertIn(
                "--receipt must not alias any classified",
                receipt_is_active.stderr,
            )
            self.assertFalse(generated_output.exists())
            self.assertEqual(
                receipt.read_text(encoding="utf-8"), '{"old":"receipt"}\n'
            )

    def test_release_plan_auto_seals_closure_and_replay_detects_mutation(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            segment = root / "segment.md"
            decision = root / "decision.json"
            unclassified = root / "unclassified.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            segment.write_text(annotated, encoding="utf-8")
            decision.write_text('{"status":"pass"}\n', encoding="utf-8")
            unclassified.write_text('{"status":"unknown"}\n', encoding="utf-8")
            write_release_plan(
                release_plan, target, boundary, segment, decision
            )

            rejected = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                root / "rejected-final.md",
                "--receipt",
                root / "rejected-receipt.json",
                "--release-plan",
                release_plan,
                "--upstream",
                unclassified,
                "--write",
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn(
                "--upstream is not in the active included release closure",
                rejected.stderr,
            )

            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--upstream",
                segment,
                "--upstream",
                segment,
                "--write",
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["schema_version"], 2)
            release_plan_records = [
                record
                for record in receipt_data["inputs"]
                if record["role"] == "release-plan"
            ]
            self.assertEqual(len(release_plan_records), 1)
            self.assertEqual(
                Path(release_plan_records[0]["path"]).name,
                "release-plan.json",
            )
            upstream_names = [
                Path(record["path"]).name
                for record in receipt_data["inputs"]
                if record["role"] == "upstream"
            ]
            self.assertEqual(
                upstream_names,
                ["segment.md", "decision.json"],
            )
            current = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(current.returncode, 0, current.stdout + current.stderr)

            decision.write_text('{"status":"changed"}\n', encoding="utf-8")
            changed_artifact = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(changed_artifact.returncode, 1)
            self.assertIn("[assembly-input-changed]", changed_artifact.stdout)

            decision.write_text('{"status":"pass"}\n', encoding="utf-8")
            release_plan.write_text(
                release_plan.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            changed_plan = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(changed_plan.returncode, 1)
            self.assertIn("[assembly-input-changed]", changed_plan.stdout)

    def test_legacy_v1_receipt_is_readable_but_final_audit_hard_fails(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            write_release_plan(release_plan, target, boundary)
            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(written.returncode, 0, written.stderr)

            legacy = json.loads(receipt.read_text(encoding="utf-8"))
            legacy["schema_version"] = 1
            legacy["inputs"] = [
                record
                for record in legacy["inputs"]
                if record["role"] != "release-plan"
            ]
            receipt.write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )
            audited = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )

            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn(
                "[assembly-receipt-release-plan-missing]", audited.stdout
            )
            self.assertNotIn("error: assembly receipt", audited.stderr)

    def test_v2_receipt_requires_exact_ordered_release_plan_closure(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            first = root / "first.json"
            second = root / "second.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            first.write_text('{"first":true}\n', encoding="utf-8")
            second.write_text('{"second":true}\n', encoding="utf-8")
            write_release_plan(
                release_plan, target, boundary, first, second
            )
            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            original = json.loads(receipt.read_text(encoding="utf-8"))
            upstream_indexes = [
                index
                for index, record in enumerate(original["inputs"])
                if record["role"] == "upstream"
            ]
            self.assertEqual(len(upstream_indexes), 2)

            reordered = json.loads(json.dumps(original))
            left, right = upstream_indexes
            reordered["inputs"][left], reordered["inputs"][right] = (
                reordered["inputs"][right],
                reordered["inputs"][left],
            )
            receipt.write_text(json.dumps(reordered), encoding="utf-8")
            reordered_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(reordered_audit.returncode, 1)
            self.assertIn(
                "[assembly-receipt-release-plan-closure-mismatch]",
                reordered_audit.stdout,
            )

            incomplete = json.loads(json.dumps(original))
            del incomplete["inputs"][upstream_indexes[-1]]
            receipt.write_text(json.dumps(incomplete), encoding="utf-8")
            incomplete_audit = run_script(
                AUDIT,
                output,
                "--mode",
                "final",
                "--assembly-receipt",
                receipt,
            )
            self.assertEqual(incomplete_audit.returncode, 1)
            self.assertIn(
                "[assembly-receipt-release-plan-closure-mismatch]",
                incomplete_audit.stdout,
            )

    def test_excluded_classified_generated_paths_are_rejected(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            output = root / "final.md"
            generated_output = root / "generated-final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            old_output = "舊 final。\n"
            old_receipt = '{"old":"receipt"}\n'
            target.write_text(annotated, encoding="utf-8")
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            output.write_text(old_output, encoding="utf-8")
            receipt.write_text(old_receipt, encoding="utf-8")
            write_release_plan(release_plan, target, boundary)
            plan = json.loads(release_plan.read_text(encoding="utf-8"))
            for artifact_id, path in (
                ("old-output", output),
                ("old-receipt", receipt),
            ):
                plan["discovery"]["globs"].append(path.name)
                plan["artifacts"].append(
                    {
                        "id": artifact_id,
                        "type": "historical-output",
                        "path": path.name,
                        "classification": "exclude",
                        "lifecycle": "historical",
                        "validation": "provisional",
                        "sha256": sha256_file(path),
                        "depends_on": [],
                        "exclusion_reason": "prior generated artefact",
                    }
                )
            release_plan.write_text(json.dumps(plan), encoding="utf-8")

            output_alias = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                output,
                "--receipt",
                root / "new-receipt.json",
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(output_alias.returncode, 1)
            self.assertIn(
                "--output must not alias any classified", output_alias.stderr
            )
            self.assertEqual(output.read_text(encoding="utf-8"), old_output)

            receipt_alias = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                boundary,
                "--output",
                generated_output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
                "--write",
            )
            self.assertEqual(receipt_alias.returncode, 1)
            self.assertIn(
                "--receipt must not alias any classified", receipt_alias.stderr
            )
            self.assertFalse(generated_output.exists())
            self.assertEqual(receipt.read_text(encoding="utf-8"), old_receipt)

    def test_absent_generated_paths_matching_discovery_globs_fail_fast(self) -> None:
        cases = (
            ("exact output", "final.md", "receipt.txt", "final.md", "--output"),
            ("broad receipt", "final.txt", "receipt.json", "*.json", "--receipt"),
        )
        for label, output_name, receipt_name, pattern, role in cases:
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as temporary_directory,
            ):
                root = Path(temporary_directory)
                target = root / "annotated.md"
                boundary = root / "boundaries.json"
                output = root / output_name
                receipt = root / receipt_name
                release_plan = root / "release-plan.json"
                target.write_text(
                    "<!-- page_001 -->\n正文。\n", encoding="utf-8"
                )
                boundary.write_text(
                    '{"boundaries":{"1":"start"}}', encoding="utf-8"
                )
                write_release_plan(release_plan, target, boundary)
                plan = json.loads(release_plan.read_text(encoding="utf-8"))
                plan["discovery"]["globs"].append(pattern)
                release_plan.write_text(json.dumps(plan), encoding="utf-8")

                finalized = run_script(
                    FINALIZE,
                    target,
                    "--boundary-manifest",
                    boundary,
                    "--output",
                    output,
                    "--receipt",
                    receipt,
                    "--release-plan",
                    release_plan,
                    "--write",
                )

                self.assertEqual(finalized.returncode, 1)
                self.assertIn(
                    f"{role} would be discovered by release-plan glob(s)",
                    finalized.stderr,
                )
                self.assertFalse(output.exists())
                self.assertFalse(receipt.exists())

    def test_validate_to_receipt_mutation_fails_before_write(self) -> None:
        finalizer = load_script_module(FINALIZE, "_finalizer_race_test")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            evidence = root / "decision.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(
                "<!-- page_001 -->\n正文。\n", encoding="utf-8"
            )
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            evidence.write_text('{"status":"pass"}\n', encoding="utf-8")
            write_release_plan(release_plan, target, boundary, evidence)
            original_builder = finalizer.build_assembly_receipt

            def mutate_then_build(*args, **kwargs):
                evidence.write_text(
                    '{"status":"mutated"}\n', encoding="utf-8"
                )
                return original_builder(*args, **kwargs)

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(
                finalizer,
                "build_assembly_receipt",
                side_effect=mutate_then_build,
            ), redirect_stderr(stderr), redirect_stdout(stdout):
                result = finalizer.main(
                    [
                        str(target),
                        "--boundary-manifest",
                        str(boundary),
                        "--output",
                        str(output),
                        "--receipt",
                        str(receipt),
                        "--release-plan",
                        str(release_plan),
                        "--write",
                    ]
                )

            self.assertEqual(result, 1)
            self.assertIn("sha256 mismatch", stderr.getvalue())
            self.assertFalse(output.exists())
            self.assertFalse(receipt.exists())

    def test_post_write_mutation_fails_receipt_revalidation(self) -> None:
        finalizer = load_script_module(FINALIZE, "_finalizer_post_write_race_test")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            evidence = root / "decision.json"
            output = root / "final.md"
            receipt = root / "receipt.json"
            release_plan = root / "release-plan.json"
            target.write_text(
                "<!-- page_001 -->\n正文。\n", encoding="utf-8"
            )
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            evidence.write_text('{"status":"pass"}\n', encoding="utf-8")
            write_release_plan(release_plan, target, boundary, evidence)
            original_writer = finalizer.write_assembly_receipt

            def write_then_mutate(path, data):
                original_writer(path, data)
                evidence.write_text(
                    '{"status":"mutated-after-write"}\n', encoding="utf-8"
                )

            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.object(
                finalizer,
                "write_assembly_receipt",
                side_effect=write_then_mutate,
            ), redirect_stderr(stderr), redirect_stdout(stdout):
                result = finalizer.main(
                    [
                        str(target),
                        "--boundary-manifest",
                        str(boundary),
                        "--output",
                        str(output),
                        "--receipt",
                        str(receipt),
                        "--release-plan",
                        str(release_plan),
                        "--write",
                    ]
                )

            self.assertEqual(result, 1)
            self.assertIn(
                "[assembly-receipt-release-plan-invalid]", stderr.getvalue()
            )
            self.assertTrue(output.is_file())
            self.assertTrue(receipt.is_file())

    def test_plan_mutation_between_validator_and_parse_is_rejected(self) -> None:
        finalizer = load_script_module(FINALIZE, "_finalizer_plan_race_test")
        core = sys.modules[finalizer.load_validated_release_plan.__module__]
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "annotated.md"
            boundary = root / "boundaries.json"
            release_plan = root / "release-plan.json"
            target.write_text(
                "<!-- page_001 -->\n正文。\n", encoding="utf-8"
            )
            boundary.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            write_release_plan(release_plan, target, boundary)
            original_validator = core.validate_plan

            def validate_then_mutate(path):
                report = original_validator(path)
                path.write_text(
                    path.read_text(encoding="utf-8") + "\n",
                    encoding="utf-8",
                )
                return report

            with mock.patch.object(
                core, "validate_plan", side_effect=validate_then_mutate
            ):
                with self.assertRaisesRegex(
                    ValueError, "changed while it was being validated"
                ):
                    core.load_validated_release_plan(release_plan)

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
            release_plan = Path(temporary_directory) / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start","2":"structural"}}',
                encoding="utf-8",
            )
            write_release_plan(release_plan, target, manifest)

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
                "--release-plan",
                release_plan,
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
            release_plan = Path(temporary_directory) / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            output.write_text("舊 final。\n", encoding="utf-8")
            os.link(output, hard_linked_receipt)
            write_release_plan(release_plan, target, manifest)

            hard_link_result = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                hard_linked_receipt,
                "--release-plan",
                release_plan,
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
                "--release-plan",
                release_plan,
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
            release_plan = Path(temporary_directory) / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            segment.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            write_release_plan(release_plan, target, manifest, segment)

            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
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
            release_plan = Path(temporary_directory) / "release-plan.json"
            report = Path(temporary_directory) / "audit-report.json"
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
            write_release_plan(release_plan, target, manifest)

            unsealed_write = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
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
                "--release-plan",
                write_release_plan(
                    release_plan, target, manifest, notation, exceptions
                ),
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
                "--json-report",
                report,
            )

            self.assertEqual(sealed_write.returncode, 0, sealed_write.stderr)
            self.assertEqual(
                sealed_audit.returncode, 0, sealed_audit.stdout + sealed_audit.stderr
            )
            report_data = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(
                report_data["input_sha256"],
                {
                    "assembly_receipt": hashlib.sha256(
                        receipt.read_bytes()
                    ).hexdigest(),
                    "notation_policy": hashlib.sha256(
                        notation.read_bytes()
                    ).hexdigest(),
                    "source_exceptions": hashlib.sha256(
                        exceptions.read_bytes()
                    ).hexdigest(),
                },
            )

    def test_receipt_schema_version_rejects_float(self) -> None:
        annotated = "<!-- page_001 -->\n正文。\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "annotated.md"
            manifest = Path(temporary_directory) / "boundaries.json"
            output = Path(temporary_directory) / "final.md"
            receipt = Path(temporary_directory) / "receipt.json"
            release_plan = Path(temporary_directory) / "release-plan.json"
            target.write_text(annotated, encoding="utf-8")
            manifest.write_text(
                '{"boundaries":{"1":"start"}}', encoding="utf-8"
            )
            write_release_plan(release_plan, target, manifest)
            written = run_script(
                FINALIZE,
                target,
                "--boundary-manifest",
                manifest,
                "--output",
                output,
                "--receipt",
                receipt,
                "--release-plan",
                release_plan,
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
            self.assertIn(
                "schema_version must be integer 1 or 2", result.stderr
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
                        "assembly_receipt": False,
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
                            "assembly_receipt": False,
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

            tool_alias = Path(temporary_directory) / "audit-tool-alias.py"
            tool_before = hashlib.sha256(AUDIT.read_bytes()).hexdigest()
            os.link(AUDIT, tool_alias)
            tool_result = run_script(
                AUDIT,
                target,
                "--mode",
                "final",
                "--json-report",
                tool_alias,
            )
            self.assertEqual(tool_result.returncode, 2)
            self.assertIn("--json-report must differ", tool_result.stderr)
            self.assertEqual(
                hashlib.sha256(AUDIT.read_bytes()).hexdigest(), tool_before
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
            receipt = root / "assembly-receipt.json"
            report = root / "audit-report.json"
            release_plan = root / "release-plan.json"
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
                write_release_plan(
                    release_plan, annotated, boundaries, page_manifest
                )
                return run_script(
                    FINALIZE,
                    annotated,
                    "--boundary-manifest",
                    boundaries,
                    "--output",
                    final,
                    "--receipt",
                    receipt,
                    "--release-plan",
                    release_plan,
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
                    "assembly_receipt": hashlib.sha256(
                        receipt.read_bytes()
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


if __name__ == "__main__":
    unittest.main()
