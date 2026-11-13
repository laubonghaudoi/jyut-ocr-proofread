from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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
        self.gemini = self.root / "config/gemini"
        self.backups = self.root / "backups"
        self._create_repository_sources()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_repository_sources(self) -> None:
        skill = self.repo
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
            "--gemini-root",
            str(self.gemini),
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
            self.codex, self.claude, self.gemini, repo_root=self.repo
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
        self.assertEqual(dry_stdout.count("DRY-RUN LINK"), 5)
        self.assertEqual(dry_stdout.count("DRY-RUN COPY"), 2)
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.gemini.exists())
        self.assertFalse(self.backups.exists())

        result, stdout, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        self.assertEqual(stdout.count("LINKED"), 5)
        self.assertEqual(stdout.count("COPIED"), 2)
        self.assertEqual(len(self.manifests()), 1)
        for spec in INSTALLER.build_specs(
            self.codex, self.claude, self.gemini, repo_root=self.repo
        ):
            self.assert_spec_current(spec)

        check_result, check_stdout, check_stderr = self.run_main(
            self.args("--check")
        )
        self.assertEqual(check_result, 0, check_stderr)
        self.assertEqual(check_stdout.count("CHECK OK"), 7)

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
            self.codex, self.claude, self.gemini, repo_root=self.repo
        )
        skill_source = specs[0]["source"]
        moved_source = skill_source.with_name("jyut-ocr-proofread-moved")
        skill_source.rename(moved_source)

        check_result, check_stdout, _ = self.run_main(self.args("--check"))
        self.assertEqual(check_result, 1)
        for spec in specs[:3]:
            expected = f"CHECK ERROR {spec['target']}"
            self.assertIn(expected, check_stdout)
        self.assertIn("canonical source is absent", check_stdout)

    def test_codex_projection_source_drift_is_detected_and_refreshed(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        specs = INSTALLER.build_specs(
            self.codex, self.claude, self.gemini, repo_root=self.repo
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
                self.codex, self.claude, self.gemini, repo_root=self.repo
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
            if calls == 4:
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
        self.assertFalse(targets["gemini-skill"].exists())

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
            self.codex, self.claude, self.gemini, repo_root=self.repo
        ):
            self.assert_spec_current(spec)

    def test_path_overlap_is_rejected_before_canonical_source_changes(self) -> None:
        overlapping_codex = self.repo / "nested-config"
        skill_source = self.repo / "SKILL.md"
        original = skill_source.read_bytes()
        arguments = [
            "--install",
            "--codex-root",
            str(overlapping_codex),
            "--claude-root",
            str(self.claude),
            "--gemini-root",
            str(self.gemini),
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
            "--gemini-root",
            str(self.gemini),
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
            self.codex, self.claude, self.gemini, repo_root=self.repo
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
            self.codex, self.claude, self.gemini, repo_root=self.repo
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

    def test_legacy_v2_manifest_can_still_be_restored(self) -> None:
        result, _, stderr = self.run_main(self.args("--install"))
        self.assertEqual(result, 0, stderr)
        manifest_path = self.manifests()[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        gemini_entry = next(
            item for item in manifest["entries"] if item["id"] == "gemini-skill"
        )
        Path(gemini_entry["target"]).unlink()
        manifest["schema_version"] = 2
        manifest["entries"] = [
            item for item in manifest["entries"] if item["id"] != "gemini-skill"
        ]
        manifest["created_parent_directories"] = [
            item
            for item in manifest["created_parent_directories"]
            if not item.startswith(str(self.gemini))
        ]
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        restore_result, restore_stdout, restore_stderr = self.run_main(
            self.args("--restore", manifest_path)
        )
        self.assertEqual(restore_result, 0, restore_stderr)
        self.assertIn("RESTORED", restore_stdout)
        for entry_id in INSTALLER.LEGACY_SPEC_IDS_V2:
            self.assertFalse(os.path.lexists(self.target_map()[entry_id]))

    def test_portable_targets_separate_gemini_and_antigravity(self) -> None:
        home = self.root / "user"
        specs = INSTALLER.build_portable_specs(
            repo_root=self.repo, agents=list(INSTALLER.PORTABLE_AGENTS), home=home,
        )
        targets = {spec["id"]: spec["target"] for spec in specs}
        self.assertEqual(targets, {
            "generic-skill": home / ".agents/skills/jyut-ocr-proofread",
            "claude-skill": home / ".claude/skills/jyut-ocr-proofread",
            "gemini-skill": home / ".gemini/skills/jyut-ocr-proofread",
            "antigravity-skill": home / ".gemini/config/skills/jyut-ocr-proofread",
        })
        self.assertTrue(all(spec["source"] == self.repo for spec in specs))
        self.assertEqual(
            INSTALLER.build_portable_specs(repo_root=self.repo, agents=["codex"], home=home)[0]["id"],
            "generic-skill",
        )

    def test_portable_default_installs_only_generic_skill(self) -> None:
        home = self.root / "user"
        with mock.patch.object(INSTALLER.Path, "home", return_value=home):
            result, stdout, stderr = self.run_main([
                "--install", "--backup-root", str(self.backups),
            ])
        self.assertEqual(result, 0, stderr)
        self.assertEqual(stdout.count("LINKED"), 1)
        self.assertEqual(os.readlink(home / ".agents/skills/jyut-ocr-proofread"), str(self.repo))
        self.assertFalse((home / ".codex").exists())
        self.assertFalse((home / ".claude").exists())
        manifest = json.loads(self.manifests()[0].read_text())
        self.assertEqual(manifest["schema_version"], 4)
        self.assertEqual(manifest["repo_root"], str(self.repo))

    def test_portable_roles_are_opt_in_for_selected_agents(self) -> None:
        home = self.root / "user"
        specs = INSTALLER.build_portable_specs(
            repo_root=self.repo, agents=["codex", "claude", "gemini"],
            with_agents=True, home=home,
        )
        self.assertEqual(len(specs), 7)
        codex_roles = [spec for spec in specs if spec["id"].startswith("codex-")]
        self.assertEqual(len(codex_roles), 2)
        self.assertTrue(all(spec["target"].parent == home / ".codex/agents" for spec in codex_roles))
        self.assertTrue(all(spec["install_mode"] == "sealed-copy" for spec in codex_roles))
        only_gemini = INSTALLER.build_portable_specs(
            repo_root=self.repo, agents=["gemini"], with_agents=True, home=home,
        )
        self.assertEqual(len(only_gemini), 1)

    def test_custom_collection_install_restore_and_identical_rerun(self) -> None:
        collection = self.root / "unusual skill folder"
        target = collection / "jyut-ocr-proofread"
        target.mkdir(parents=True)
        (target / "user.txt").write_bytes(b"preserve existing skill\x00")
        args = ["--skill-dir", str(collection), "--backup-root", str(self.backups)]
        result, _, stderr = self.run_main(["--install", *args])
        self.assertEqual(result, 0, stderr)
        self.assertEqual(os.readlink(target), str(self.repo))
        self.assertEqual((target / "references/rules.md").read_bytes(), b"\x00rules\xff\n")
        manifest = self.manifests()[0]
        result, stdout, stderr = self.run_main(["--install", *args])
        self.assertEqual(result, 0, stderr)
        self.assertIn("all 1 targets", stdout)
        self.assertEqual(self.manifests(), [manifest])
        result, _, stderr = self.run_main(["--check", *args])
        self.assertEqual(result, 0, stderr)
        result, _, stderr = self.run_main(["--restore", str(manifest), *args])
        self.assertEqual(result, 0, stderr)
        self.assertEqual((target / "user.txt").read_bytes(), b"preserve existing skill\x00")

    def test_portable_selection_restore_requires_exact_requested_targets(self) -> None:
        home = self.root / "user"
        specs = INSTALLER.build_portable_specs(repo_root=self.repo, agents=["claude"], home=home)
        with redirect_stdout(io.StringIO()):
            manifest = INSTALLER.perform_install(specs, self.backups)
        changed_specs = INSTALLER.build_portable_specs(repo_root=self.repo, agents=["gemini"], home=home)
        with self.assertRaises(INSTALLER.InstallError):
            INSTALLER.perform_restore(manifest, changed_specs)
        self.assertTrue(INSTALLER.spec_installed_current(specs[0]))
        with redirect_stdout(io.StringIO()):
            INSTALLER.perform_restore(manifest, specs)

    def test_legacy_v3_nested_source_restores_without_recreating_old_layout(self) -> None:
        old_source = self.repo / INSTALLER.LEGACY_SKILL_PATH
        old_source.mkdir(parents=True)
        (old_source / "SKILL.md").write_text("old layout\n")
        specs = INSTALLER.build_specs(self.codex, self.claude, self.gemini, repo_root=self.repo)
        old_specs = [dict(spec) for spec in specs]
        for spec in old_specs:
            if spec["source_type"] == "directory":
                spec["source"] = old_source
        target = old_specs[0]["target"]
        target.mkdir(parents=True)
        (target / "original.txt").write_text("restore me\n")
        with redirect_stdout(io.StringIO()):
            manifest_path = INSTALLER.perform_install(old_specs, self.backups)
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 3
        manifest_path.write_text(json.dumps(manifest))
        original_manifest_bytes = manifest_path.read_bytes()
        shutil.rmtree(old_source)
        with redirect_stdout(io.StringIO()):
            INSTALLER.perform_restore(manifest_path, specs)
        self.assertEqual((target / "original.txt").read_text(), "restore me\n")
        self.assertEqual(manifest_path.read_bytes(), original_manifest_bytes)
        self.assertFalse(old_source.exists())

    def test_v3_historical_gemini_identity_is_not_portable_gemini(self) -> None:
        home = self.root / "user"
        specs = INSTALLER.build_specs(home / ".codex", home / ".claude", home / ".gemini/config", repo_root=self.repo)
        with redirect_stdout(io.StringIO()):
            manifest_path = INSTALLER.perform_install(specs, self.backups)
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 3
        manifest_path.write_text(json.dumps(manifest))
        portable = INSTALLER.build_portable_specs(
            repo_root=self.repo, agents=["codex", "claude", "gemini"],
            with_agents=True, home=home,
        )
        # Even callers supplying all historical role identities cannot silently
        # reinterpret the old gemini-skill as the Gemini CLI directory.
        mixed = [dict(spec) for spec in specs]
        mixed[2] = next(spec for spec in portable if spec["id"] == "gemini-skill")
        with self.assertRaisesRegex(INSTALLER.InstallError, "paths do not match"):
            INSTALLER.load_manifest(manifest_path, mixed)

    def test_portable_unknown_agent_and_mixed_cli_modes_are_rejected(self) -> None:
        with self.assertRaisesRegex(INSTALLER.InstallError, "unsupported agents"):
            INSTALLER.build_portable_specs(repo_root=self.repo, agents=["unknown"])
        result, _, stderr = self.run_main(self.args("--install", "--agent", "generic"))
        self.assertEqual(result, 1)
        self.assertIn("cannot be combined", stderr)
        self.assertFalse(self.backups.exists())

    def test_legacy_link_migrates_to_root_with_recoverable_original_link(self) -> None:
        specs = INSTALLER.build_portable_specs(repo_root=self.repo, home=self.root / "user")
        target = specs[0]["target"]
        target.parent.mkdir(parents=True)
        old_text = str(self.repo / INSTALLER.LEGACY_SKILL_PATH)
        target.symlink_to(old_text)
        with redirect_stdout(io.StringIO()):
            manifest_path = INSTALLER.perform_install(specs, self.backups)
        self.assertEqual(os.readlink(target), str(self.repo))
        with redirect_stdout(io.StringIO()):
            INSTALLER.perform_restore(manifest_path, specs)
        self.assertEqual(os.readlink(target), old_text)

    def test_legacy_source_alias_is_bounded_to_known_path_and_schema(self) -> None:
        specs = INSTALLER.build_specs(self.codex, self.claude, self.gemini, repo_root=self.repo)
        with redirect_stdout(io.StringIO()):
            manifest_path = INSTALLER.perform_install(specs, self.backups)
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 3
        manifest["entries"][0]["source"] = str(self.repo / "different-skill")
        manifest["entries"][0]["installed_link_text"] = manifest["entries"][0]["source"]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(INSTALLER.InstallError, "paths do not match"):
            INSTALLER.load_manifest(manifest_path, specs)
        manifest["schema_version"] = 4
        manifest["entries"][0]["source"] = str(self.repo / INSTALLER.LEGACY_SKILL_PATH)
        manifest["entries"][0]["installed_link_text"] = manifest["entries"][0]["source"]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(INSTALLER.InstallError, "paths do not match"):
            INSTALLER.load_manifest(manifest_path, specs)

    def test_mislocated_source_without_root_skill_is_rejected(self) -> None:
        (self.repo / "SKILL.md").unlink()
        specs = INSTALLER.build_portable_specs(repo_root=self.repo, home=self.root / "user")
        with self.assertRaisesRegex(INSTALLER.InstallError, "no SKILL.md"):
            INSTALLER.perform_install(specs, self.backups)
        self.assertFalse(self.backups.exists())

    def _portable_alias_install(self):
        home = self.root / "user"
        first = self.root / "first-collection"
        second = self.root / "second-collection"
        first.mkdir()
        second.mkdir()
        alias = home / ".agents/skills"
        alias.parent.mkdir(parents=True)
        alias.symlink_to(first, target_is_directory=True)
        original = first / "jyut-ocr-proofread"
        original.mkdir()
        (original / "old.txt").write_text("preserve\n")
        specs = INSTALLER.build_portable_specs(repo_root=self.repo, home=home)
        with redirect_stdout(io.StringIO()):
            manifest = INSTALLER.perform_install(specs, self.backups)
        return specs, manifest, alias, first, second

    def test_v4_stable_parent_alias_can_restore_sealed_collection(self) -> None:
        specs, manifest, alias, first, second = self._portable_alias_install()
        payload = json.loads(manifest.read_text())
        self.assertEqual(payload["entries"][0]["target_mutation_path"], str(first / "jyut-ocr-proofread"))
        with redirect_stdout(io.StringIO()):
            INSTALLER.perform_restore(manifest, specs)
        self.assertEqual((first / "jyut-ocr-proofread/old.txt").read_text(), "preserve\n")
        self.assertEqual(alias.resolve(), first)
        self.assertEqual(list(second.iterdir()), [])

    def test_v4_restore_refuses_parent_alias_retargeted_to_foreign_collection(self) -> None:
        specs, manifest, alias, first, second = self._portable_alias_install()
        foreign = second / "jyut-ocr-proofread"
        foreign.symlink_to(self.repo)
        alias.unlink()
        alias.symlink_to(second, target_is_directory=True)
        before = manifest.read_bytes()
        with self.assertRaisesRegex(INSTALLER.InstallError, "target parent location changed"):
            INSTALLER.perform_restore(manifest, specs)
        self.assertEqual(os.readlink(foreign), str(self.repo))
        self.assertEqual(os.readlink(first / "jyut-ocr-proofread"), str(self.repo))
        self.assertEqual(manifest.read_bytes(), before)
        entry = json.loads(before)["entries"][0]
        self.assertEqual((Path(entry["backup_path"]) / "old.txt").read_text(), "preserve\n")

    def test_v4_restore_rechecks_alias_after_preflight_before_unlink(self) -> None:
        specs, manifest, alias, first, second = self._portable_alias_install()
        foreign = second / "jyut-ocr-proofread"
        foreign.symlink_to(self.repo)
        real_preflight = INSTALLER.preflight_restore

        def switch_after_preflight(entries):
            real_preflight(entries)
            alias.unlink()
            alias.symlink_to(second, target_is_directory=True)

        with mock.patch.object(INSTALLER, "preflight_restore", side_effect=switch_after_preflight):
            with self.assertRaisesRegex(INSTALLER.InstallError, "target parent location changed"):
                INSTALLER.perform_restore(manifest, specs)
        self.assertEqual(os.readlink(foreign), str(self.repo))
        self.assertEqual(os.readlink(first / "jyut-ocr-proofread"), str(self.repo))

    def test_legacy_restore_rejects_parent_alias_without_historical_seal(self) -> None:
        first = self.root / "legacy-collection"
        first.mkdir()
        self.codex.mkdir(parents=True)
        (self.codex / "skills").symlink_to(first, target_is_directory=True)
        specs = INSTALLER.build_specs(self.codex, self.claude, self.gemini, repo_root=self.repo)
        with redirect_stdout(io.StringIO()):
            manifest = INSTALLER.perform_install(specs, self.backups)
        payload = json.loads(manifest.read_text())
        payload["schema_version"] = 3
        for entry in payload["entries"]:
            entry.pop("target_mutation_path")
        manifest.write_text(json.dumps(payload))
        with self.assertRaisesRegex(INSTALLER.InstallError, "legacy target parent alias cannot be verified"):
            INSTALLER.perform_restore(manifest, specs)
        self.assertEqual(os.readlink(first / "jyut-ocr-proofread"), str(self.repo))
        self.assertTrue(all(INSTALLER.spec_installed_current(spec) for spec in specs))


if __name__ == "__main__":
    unittest.main()
