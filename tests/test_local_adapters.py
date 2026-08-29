from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = REPO_ROOT / "scripts/install_local_adapters.py"


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "test_install_local_adapters_module", INSTALLER_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not import {INSTALLER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


INSTALLER = load_installer()


class LocalAdapterInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "repo"
        self.codex = self.root / "config/codex"
        self.claude = self.root / "config/claude"
        self.backups = self.root / "backups"
        self._create_repository_sources()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_repository_sources(self) -> None:
        skill = (
            self.repo
            / "plugins/jyut-ocr-proofread/skills/jyut-ocr-proofread"
        )
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_bytes(b"canonical skill\n")
        (skill / "references/rules.md").write_bytes(b"\x00rules\xff\n")
        codex_agents = self.repo / "adapters/codex/agents"
        claude_agents = self.repo / "adapters/claude/agents"
        codex_agents.mkdir(parents=True)
        claude_agents.mkdir(parents=True)
        for name in INSTALLER.AGENT_NAMES:
            (codex_agents / f"{name}.toml").write_text(
                f'name = "{name}"\n', encoding="utf-8"
            )
            (claude_agents / f"{name}.md").write_text(
                f"---\nname: {name}\nmodel: inherit\n---\n", encoding="utf-8"
            )

    def args(self, action: str, *extra: object) -> list[str]:
        return [
            action,
            *(str(value) for value in extra),
            "--codex-root",
            str(self.codex),
            "--claude-root",
            str(self.claude),
            "--backup-root",
            str(self.backups),
        ]

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(INSTALLER, "REPO_ROOT", self.repo):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = INSTALLER.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def manifests(self) -> list[Path]:
        return sorted(self.backups.glob("*/manifest.json"))

    def target_map(self) -> dict[str, Path]:
        specs = INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        )
        return {spec["id"]: spec["target"] for spec in specs}

    def assert_spec_current(self, spec: dict[str, object]) -> None:
        self.assertTrue(INSTALLER.spec_installed_current(spec), spec["id"])
        target = spec["target"]
        source = spec["source"]
        if spec["install_mode"] == "symlink":
            self.assertTrue(target.is_symlink())
            self.assertEqual(os.readlink(target), str(source))
        else:
            self.assertTrue(target.is_file())
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), source.read_bytes())

    def test_fresh_install_check_and_idempotent_reinstall(self) -> None:
        dry_result, dry_stdout, dry_stderr = self.run_main(
            self.args("--dry-run")
        )
        self.assertEqual(dry_result, 0, dry_stderr)
        self.assertEqual(dry_stdout.count("DRY-RUN LINK"), 4)
        self.assertEqual(dry_stdout.count("DRY-RUN COPY"), 2)
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.backups.exists())

        result, stdout, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        self.assertEqual(stdout.count("LINKED"), 4)
        self.assertEqual(stdout.count("COPIED"), 2)
        self.assertEqual(len(self.manifests()), 1)
        for spec in INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        ):
            self.assert_spec_current(spec)

        check_result, check_stdout, check_stderr = self.run_main(
            self.args("--check")
        )
        self.assertEqual(check_result, 0, check_stderr)
        self.assertEqual(check_stdout.count("CHECK OK"), 6)

        again_result, again_stdout, again_stderr = self.run_main(
            self.args("--install")
        )
        self.assertEqual(again_result, 0, again_stderr)
        self.assertIn("ALREADY INSTALLED", again_stdout)
        self.assertEqual(len(self.manifests()), 1)

    def test_check_reports_exact_links_as_broken_when_source_disappears(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        specs = INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        )
        skill_source = specs[0]["source"]
        moved_source = skill_source.with_name("jyut-ocr-proofread-moved")
        skill_source.rename(moved_source)

        check_result, check_stdout, _ = self.run_main(self.args("--check"))
        self.assertEqual(check_result, 1)
        for spec in specs[:2]:
            expected = f"CHECK ERROR {spec['target']}"
            self.assertIn(expected, check_stdout)
        self.assertIn("canonical source is absent", check_stdout)

    def test_codex_projection_source_drift_is_detected_and_refreshed(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        specs = INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        )
        writer = next(spec for spec in specs if spec["id"] == "codex-segment-writer")
        old_bytes = writer["target"].read_bytes()
        writer["source"].write_bytes(old_bytes + b"# canonical update\n")

        check_result, check_stdout, _ = self.run_main(self.args("--check"))
        self.assertEqual(check_result, 1)
        self.assertIn(f"CHECK ERROR {writer['target']}", check_stdout)
        self.assertEqual(writer["target"].read_bytes(), old_bytes)

        refresh_result, refresh_stdout, refresh_stderr = self.run_main(
            self.args("--install")
        )
        self.assertEqual(refresh_result, 0, refresh_stderr)
        self.assertIn(f"COPIED {writer['target']}", refresh_stdout)
        self.assertEqual(writer["target"].read_bytes(), writer["source"].read_bytes())
        self.assertFalse(writer["target"].is_symlink())

    def test_check_rejects_multilink_file_as_codex_projection(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        writer = next(
            spec
            for spec in INSTALLER.build_specs(
                self.codex, self.claude, repo_root=self.repo
            )
            if spec["id"] == "codex-segment-writer"
        )
        foreign = self.root / "foreign-copy.toml"
        foreign.write_bytes(writer["target"].read_bytes())
        os.chmod(foreign, stat.S_IMODE(writer["source"].lstat().st_mode))
        writer["target"].unlink()
        os.link(foreign, writer["target"])

        check_result, check_stdout, _ = self.run_main(self.args("--check"))
        self.assertEqual(check_result, 1)
        self.assertIn(f"CHECK ERROR {writer['target']}", check_stdout)

    def test_setup_failure_removes_empty_transaction_directory(self) -> None:
        real_manifest_entry = INSTALLER.manifest_entry
        calls = 0

        def disappear_during_setup(spec, *, backup_directory):
            nonlocal calls
            calls += 1
            if calls == 3:
                spec["source"].unlink()
            return real_manifest_entry(spec, backup_directory=backup_directory)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(INSTALLER, "REPO_ROOT", self.repo):
            with mock.patch.object(
                INSTALLER, "manifest_entry", side_effect=disappear_during_setup
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = INSTALLER.main(self.args("--install"))

        self.assertEqual(result, 1)
        self.assertIn("transaction setup failed", stderr.getvalue())
        self.assertTrue(
            not self.backups.exists() or not any(self.backups.iterdir())
        )
        for target in self.target_map().values():
            self.assertFalse(os.path.lexists(target), target)

    def test_existing_directory_files_and_links_are_inventoried_and_restored(self) -> None:
        targets = self.target_map()
        old_skill = targets["codex-skill"]
        (old_skill / "nested").mkdir(parents=True)
        (old_skill / "empty").mkdir()
        original_skill_bytes = b"old\x00skill\xff"
        (old_skill / "nested/data.bin").write_bytes(original_skill_bytes)
        nested_link_text = "../nested/data.bin"
        os.symlink(nested_link_text, old_skill / "data-link")
        link_mode = stat.S_IMODE((old_skill / "data-link").lstat().st_mode)
        empty_mode = stat.S_IMODE((old_skill / "empty").lstat().st_mode)
        nested_mode = stat.S_IMODE((old_skill / "nested").lstat().st_mode)
        data_mode = stat.S_IMODE((old_skill / "nested/data.bin").lstat().st_mode)

        old_agent = targets["codex-segment-writer"]
        old_agent.parent.mkdir(parents=True, exist_ok=True)
        original_agent_bytes = b"old agent\x00\xff\n"
        old_agent.write_bytes(original_agent_bytes)

        foreign_source = self.root / "foreign-skill"
        foreign_source.mkdir()
        foreign_link = targets["claude-skill"]
        foreign_link.parent.mkdir(parents=True)
        os.symlink(str(foreign_source), foreign_link)

        broken_link = targets["claude-visual-auditor"]
        broken_link.parent.mkdir(parents=True, exist_ok=True)
        broken_text = str(self.root / "missing-adapter")
        os.symlink(broken_text, broken_link)

        before_result, before_stdout, _ = self.run_main(self.args("--check"))
        self.assertEqual(before_result, 1)
        self.assertIn("CHECK ERROR", before_stdout)

        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        manifest_path = self.manifests()[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in manifest["entries"]}
        self.assertEqual(
            entries["codex-skill"]["original"]["lstat_type"], "directory"
        )
        self.assertEqual(
            entries["codex-skill"]["original"]["tree_entries"],
            [
                {
                    "lstat_type": "symlink",
                    "link_text": nested_link_text,
                    "mode": link_mode,
                    "path": "data-link",
                },
                {
                    "lstat_type": "directory",
                    "mode": empty_mode,
                    "path": "empty",
                },
                {
                    "lstat_type": "directory",
                    "mode": nested_mode,
                    "path": "nested",
                },
                {
                    "lstat_type": "regular-file",
                    "mode": data_mode,
                    "path": "nested/data.bin",
                    "sha256": INSTALLER.sha256_file(
                        Path(entries["codex-skill"]["backup_path"])
                        / "nested/data.bin"
                    ),
                    "size": len(original_skill_bytes),
                }
            ],
        )
        self.assertEqual(
            entries["claude-skill"]["original"]["link_text"],
            str(foreign_source),
        )
        self.assertEqual(
            entries["claude-visual-auditor"]["original"]["link_text"],
            broken_text,
        )

        restore_result, restore_stdout, restore_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(restore_result, 0, restore_stderr)
        self.assertIn("RESTORED", restore_stdout)
        self.assertEqual(
            (old_skill / "nested/data.bin").read_bytes(), original_skill_bytes
        )
        self.assertTrue((old_skill / "empty").is_dir())
        self.assertEqual(os.readlink(old_skill / "data-link"), nested_link_text)
        self.assertEqual(old_agent.read_bytes(), original_agent_bytes)
        self.assertTrue(foreign_link.is_symlink())
        self.assertEqual(os.readlink(foreign_link), str(foreign_source))
        self.assertTrue(broken_link.is_symlink())
        self.assertEqual(os.readlink(broken_link), broken_text)
        self.assertFalse(targets["codex-visual-auditor"].exists())
        self.assertFalse(targets["claude-segment-writer"].exists())

    def test_restore_refuses_tampered_target_before_mutating_any_link(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        manifest_path = self.manifests()[0]
        targets = self.target_map()
        tampered = targets["codex-segment-writer"]
        tampered.unlink()
        tampered.write_bytes(b"user replacement")
        untouched = targets["claude-segment-writer"]
        untouched_link_text = os.readlink(untouched)

        restore_result, _, restore_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(restore_result, 1)
        self.assertIn("restore preflight failed", restore_stderr)
        self.assertEqual(tampered.read_bytes(), b"user replacement")
        self.assertTrue(untouched.is_symlink())
        self.assertEqual(os.readlink(untouched), untouched_link_text)

    def test_restore_refuses_tampered_nested_backup_symlink(self) -> None:
        targets = self.target_map()
        old_skill = targets["codex-skill"]
        old_skill.mkdir(parents=True)
        os.symlink("first-target", old_skill / "nested-link")

        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        manifest_path = self.manifests()[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in manifest["entries"] if item["id"] == "codex-skill"
        )
        backup_link = Path(entry["backup_path"]) / "nested-link"
        backup_link.unlink()
        os.symlink("changed-target", backup_link)

        restore_result, _, restore_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(restore_result, 1)
        self.assertIn("backup is missing or changed", restore_stderr)
        for spec in INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        ):
            self.assert_spec_current(spec)

    def test_path_overlap_is_rejected_before_canonical_source_changes(self) -> None:
        overlapping_codex = self.repo / "plugins/jyut-ocr-proofread"
        skill_source = (
            self.repo
            / "plugins/jyut-ocr-proofread/skills/jyut-ocr-proofread/SKILL.md"
        )
        original = skill_source.read_bytes()
        arguments = [
            "--install",
            "--codex-root",
            str(overlapping_codex),
            "--claude-root",
            str(self.claude),
            "--backup-root",
            str(self.backups),
        ]
        result, _, stderr = self.run_main(arguments)
        self.assertEqual(result, 1)
        self.assertIn("source and target paths overlap", stderr)
        self.assertEqual(skill_source.read_bytes(), original)
        self.assertFalse(self.backups.exists())

    def test_backup_root_inside_existing_target_is_rejected(self) -> None:
        target = self.target_map()["codex-skill"]
        target.mkdir(parents=True)
        (target / "old.txt").write_text("keep\n", encoding="utf-8")
        nested_backup = target / "installer-backups"
        arguments = [
            "--install",
            "--codex-root",
            str(self.codex),
            "--claude-root",
            str(self.claude),
            "--backup-root",
            str(nested_backup),
        ]
        result, _, stderr = self.run_main(arguments)
        self.assertEqual(result, 1)
        self.assertIn("backup root is inside install target", stderr)
        self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "keep\n")
        self.assertFalse(nested_backup.exists())

    def test_install_failure_rolls_back_every_changed_target(self) -> None:
        targets = self.target_map()
        old_skill = targets["codex-skill"]
        old_skill.mkdir(parents=True)
        (old_skill / "preserve.bin").write_bytes(b"preserve\x00\xff")
        broken = targets["claude-skill"]
        broken.parent.mkdir(parents=True)
        old_broken_text = str(self.root / "does-not-exist")
        os.symlink(old_broken_text, broken)

        real_create = INSTALLER.create_exact_symlink
        call_count = 0

        def fail_on_third(source: Path, target: Path) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise INSTALLER.InstallError("injected link failure")
            real_create(source, target)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(INSTALLER, "REPO_ROOT", self.repo):
            with mock.patch.object(
                INSTALLER, "create_exact_symlink", side_effect=fail_on_third
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = INSTALLER.main(self.args("--install"))

        self.assertEqual(result, 1)
        self.assertIn("injected link failure", stderr.getvalue())
        self.assertEqual(
            (old_skill / "preserve.bin").read_bytes(), b"preserve\x00\xff"
        )
        self.assertTrue(broken.is_symlink())
        self.assertEqual(os.readlink(broken), old_broken_text)
        for entry_id, target in targets.items():
            if entry_id not in {"codex-skill", "claude-skill"}:
                self.assertFalse(os.path.lexists(target), target)
        self.assertEqual(self.manifests(), [])

    def test_restore_failure_rolls_back_to_all_installer_links(self) -> None:
        targets = self.target_map()
        codex_skill = targets["codex-skill"]
        claude_skill = targets["claude-skill"]
        codex_skill.mkdir(parents=True)
        claude_skill.mkdir(parents=True)
        (codex_skill / "old.txt").write_text("codex old\n", encoding="utf-8")
        (claude_skill / "old.txt").write_text("claude old\n", encoding="utf-8")

        install_result, _, install_stderr = self.run_main(self.args("--install"))
        self.assertEqual(install_result, 0, install_stderr)
        manifest_path = self.manifests()[0]
        real_move = INSTALLER.shutil.move
        call_count = 0

        def fail_on_second(source, destination):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("injected restore move failure")
            return real_move(source, destination)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(INSTALLER, "REPO_ROOT", self.repo):
            with mock.patch.object(
                INSTALLER.shutil, "move", side_effect=fail_on_second
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    restore_result = INSTALLER.main(
                        self.args("--restore", manifest_path)
                    )

        self.assertEqual(restore_result, 1)
        self.assertIn("injected restore move failure", stderr.getvalue())
        for spec in INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        ):
            self.assert_spec_current(spec)

        retry_result, _, retry_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(retry_result, 0, retry_stderr)
        self.assertEqual(
            (codex_skill / "old.txt").read_text(encoding="utf-8"), "codex old\n"
        )
        self.assertEqual(
            (claude_skill / "old.txt").read_text(encoding="utf-8"),
            "claude old\n",
        )

    def test_restore_leaves_preexisting_exact_link_untouched(self) -> None:
        specs = INSTALLER.build_specs(
            self.codex, self.claude, repo_root=self.repo
        )
        preexisting = specs[0]
        preexisting["target"].parent.mkdir(parents=True)
        os.symlink(str(preexisting["source"]), preexisting["target"])

        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        manifest_path = self.manifests()[0]
        restore_result, _, restore_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(restore_result, 0, restore_stderr)
        self.assertTrue(preexisting["target"].is_symlink())
        self.assertEqual(
            os.readlink(preexisting["target"]), str(preexisting["source"])
        )


if __name__ == "__main__":
    unittest.main()
