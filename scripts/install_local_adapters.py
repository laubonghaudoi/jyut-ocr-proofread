#!/usr/bin/env python3
"""Install selected portable skill entrypoints and optional agent adapters.

Skills and Claude agents use exact symlinks.  Codex custom-agent TOMLs use
sealed regular-file projections because current Codex releases discover a
symlinked role name but fail when applying the symlinked configuration layer.

The installer deliberately does not consult CODEX_HOME or similar environment
overrides.  Tests and non-default installations can supply explicit roots.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 4
INSTALLER_NAME = "jyut-ocr-proofread-local-adapters"

AGENT_NAMES = (
    "jyut-ocr-segment-writer",
    "jyut-ocr-visual-auditor",
)
LEGACY_SPEC_IDS_V2 = (
    "codex-skill",
    "claude-skill",
    "codex-segment-writer",
    "codex-visual-auditor",
    "claude-segment-writer",
    "claude-visual-auditor",
)
LEGACY_SPEC_IDS_V3 = (
    *LEGACY_SPEC_IDS_V2,
    "gemini-skill",
)


SKILL_NAME = "jyut-ocr-proofread"
LEGACY_SKILL_PATH = Path("plugins/jyut-ocr-proofread/skills/jyut-ocr-proofread")
PORTABLE_AGENTS = ("generic", "codex", "claude", "gemini", "antigravity")
# Retained for callers inspecting the historical seven-target interface.
SPEC_IDS = LEGACY_SPEC_IDS_V3


class InstallError(RuntimeError):
    """A fail-closed installer or restore error."""


def absolute_root(path: Path) -> Path:
    """Resolve a root before target components are appended.

    Resolving a complete target would follow an existing target symlink and
    lose the path that the installer is meant to inspect.
    """

    return path.expanduser().resolve(strict=False)


def build_specs(
    codex_root: Path,
    claude_root: Path,
    gemini_root: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Historical seven-target interface (including custom role adapters).

    ``gemini_root`` retains its old Antigravity/config meaning for compatibility;
    use ``build_portable_specs`` for correctly separated Gemini CLI targets.
    """
    root = absolute_root(repo_root or REPO_ROOT)
    codex = absolute_root(codex_root)
    claude = absolute_root(claude_root)
    gemini = absolute_root(gemini_root or (Path.home() / ".gemini/config"))
    skill = root
    codex_adapters = root / "adapters/codex/agents"
    claude_adapters = root / "adapters/claude/agents"

    specs = [
        {
            "id": "codex-skill",
            "source": skill,
            "target": codex / "skills/jyut-ocr-proofread",
            "source_type": "directory",
            "install_mode": "symlink",
        },
        {
            "id": "claude-skill",
            "source": skill,
            "target": claude / "skills/jyut-ocr-proofread",
            "source_type": "directory",
            "install_mode": "symlink",
        },
        {
            "id": "gemini-skill",
            "source": skill,
            "target": gemini / "skills/jyut-ocr-proofread",
            "source_type": "directory",
            "install_mode": "symlink",
        },
        {
            "id": "codex-segment-writer",
            "source": codex_adapters / f"{AGENT_NAMES[0]}.toml",
            "target": codex / "agents" / f"{AGENT_NAMES[0]}.toml",
            "source_type": "regular-file",
            "install_mode": "sealed-copy",
        },
        {
            "id": "codex-visual-auditor",
            "source": codex_adapters / f"{AGENT_NAMES[1]}.toml",
            "target": codex / "agents" / f"{AGENT_NAMES[1]}.toml",
            "source_type": "regular-file",
            "install_mode": "sealed-copy",
        },
        {
            "id": "claude-segment-writer",
            "source": claude_adapters / f"{AGENT_NAMES[0]}.md",
            "target": claude / "agents" / f"{AGENT_NAMES[0]}.md",
            "source_type": "regular-file",
            "install_mode": "symlink",
        },
        {
            "id": "claude-visual-auditor",
            "source": claude_adapters / f"{AGENT_NAMES[1]}.md",
            "target": claude / "agents" / f"{AGENT_NAMES[1]}.md",
            "source_type": "regular-file",
            "install_mode": "symlink",
        },
    ]


    for spec in specs:
        spec["repo_root"] = root
        if spec["source_type"] == "directory":
            spec["legacy_source"] = root / LEGACY_SKILL_PATH
    return specs


def build_portable_specs(
    *,
    repo_root: Path | None = None,
    agents: Sequence[str] = ("generic",),
    skill_dir: Path | None = None,
    with_agents: bool = False,
    home: Path | None = None,
) -> list[dict[str, Any]]:
    """Select skill entrypoints; optional role adapters never drive discovery.

    ``skill_dir`` is a collection directory, not the final skill directory.
    Generic and Codex share the standard ~/.agents/skills entrypoint.  The
    historical ~/.codex/skills path remains available via ``build_specs`` or
    an explicit collection directory.  Gemini CLI and Antigravity have
    distinct directories and distinct manifest identities.
    """
    root = absolute_root(repo_root or REPO_ROOT)
    user_home = absolute_root(home or Path.home())
    requested = list(dict.fromkeys(agents))
    if not requested:
        requested = ["generic"]
    unknown = set(requested) - set(PORTABLE_AGENTS)
    if unknown:
        raise InstallError(f"unsupported agents: {', '.join(sorted(unknown))}")
    collections = {
        "generic": user_home / ".agents/skills",
        "codex": user_home / ".agents/skills",
        "claude": user_home / ".claude/skills",
        "gemini": user_home / ".gemini/skills",
        "antigravity": user_home / ".gemini/config/skills",
    }
    specs: list[dict[str, Any]] = []
    if skill_dir is not None:
        specs.append({
            "id": "custom-skill", "source": root,
            "target": absolute_root(skill_dir) / SKILL_NAME,
            "source_type": "directory", "install_mode": "symlink",
            "repo_root": root,
        })
    else:
        seen: set[Path] = set()
        for agent in requested:
            target = collections[agent] / SKILL_NAME
            if target in seen:
                continue
            seen.add(target)
            # Stable identity independent of generic/codex selection order.
            identity = "generic" if agent == "codex" else agent
            specs.append({
                "id": f"{identity}-skill", "source": root, "target": target,
                "source_type": "directory", "install_mode": "symlink",
                "repo_root": root,
            })
    if with_agents:
        legacy = build_specs(user_home / ".codex", user_home / ".claude", repo_root=root)
        specs.extend(spec for spec in legacy if spec["source_type"] == "regular-file"
                     and spec["id"].split("-", 1)[0] in requested)
    return specs


def lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def lstat_type(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "regular-file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "other"


def path_type(path: Path) -> str:
    try:
        return lstat_type(path.lstat().st_mode)
    except FileNotFoundError:
        return "absent"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_inventory(path: Path, kind: str) -> list[dict[str, Any]]:
    """Seal every restorable object below *path* without following symlinks."""

    if kind == "regular-file":
        metadata = path.lstat()
        return [
            {
                "path": ".",
                "lstat_type": kind,
                "mode": stat.S_IMODE(metadata.st_mode),
                "size": metadata.st_size,
                "sha256": sha256_file(path),
            }
        ]
    if kind in {"absent", "symlink"}:
        return []
    if kind != "directory":
        raise InstallError(f"unsupported object type in backup inventory: {kind}: {path}")

    inventory: list[dict[str, Any]] = []

    def walk(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise InstallError(f"cannot inventory directory {directory}: {exc}") from exc
        for child in children:
            candidate = Path(child.path)
            try:
                metadata = candidate.lstat()
            except OSError as exc:
                raise InstallError(f"cannot inventory object {candidate}: {exc}") from exc
            child_kind = lstat_type(metadata.st_mode)
            record: dict[str, Any] = {
                "path": candidate.relative_to(path).as_posix(),
                "lstat_type": child_kind,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
            if child_kind == "directory":
                inventory.append(record)
                walk(candidate)
            elif child_kind == "regular-file":
                record.update(
                    {
                        "size": metadata.st_size,
                        "sha256": sha256_file(candidate),
                    }
                )
                inventory.append(record)
            elif child_kind == "symlink":
                record["link_text"] = os.readlink(candidate)
                inventory.append(record)
            else:
                raise InstallError(
                    f"unsupported nested object type {child_kind}: {candidate}"
                )

    walk(path)
    return inventory


def snapshot_path(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {
            "lstat_type": "absent",
            "link_text": None,
            "mode": None,
            "tree_entries": [],
        }

    kind = lstat_type(metadata.st_mode)
    return {
        "lstat_type": kind,
        "link_text": os.readlink(path) if kind == "symlink" else None,
        "mode": stat.S_IMODE(metadata.st_mode),
        "tree_entries": tree_inventory(path, kind),
    }


def mutation_location(path: Path) -> Path:
    """Resolve parent aliases while preserving the final lstat object."""

    return path.parent.resolve(strict=False) / path.name


def checked_entry_target(entry: dict[str, Any]) -> Path:
    """Bind mutations to the parent location sealed before installation.

    Keep the final component unresolved: it is the symlink being installed or
    removed.  Resolving the whole target would instead select its source.
    """
    target = Path(entry["target"])
    sealed = entry.get("target_mutation_path")
    if (
        not isinstance(sealed, str)
        or not Path(sealed).is_absolute()
        or mutation_location(target) != Path(sealed)
        or mutation_location(Path(sealed)) != Path(sealed)
    ):
        raise InstallError(f"{entry['id']}: target parent location changed or is unsealed: {target}")
    return Path(sealed)


def paths_overlap(first: Path, second: Path) -> bool:
    return (
        first == second
        or first in second.parents
        or second in first.parents
    )


def validate_layout(
    specs: Sequence[dict[str, Any]],
    *,
    backup_root: Path | None = None,
    backup_directory: Path | None = None,
) -> None:
    """Reject layouts where a move could consume a source or another target."""

    targets = [(spec["id"], mutation_location(spec["target"])) for spec in specs]
    for index, (entry_id, target) in enumerate(targets):
        for other_id, other_target in targets[index + 1 :]:
            if paths_overlap(target, other_target):
                raise InstallError(
                    f"target paths overlap: {entry_id}={target}; "
                    f"{other_id}={other_target}"
                )

    for spec in specs:
        source = spec["source"].resolve(strict=False)
        target = mutation_location(spec["target"])
        if paths_overlap(source, target):
            raise InstallError(
                f"source and target paths overlap for {spec['id']}: "
                f"source={source}; target={target}"
            )

    if backup_root is not None:
        root = absolute_root(backup_root)
        for entry_id, target in targets:
            if root == target or target in root.parents:
                raise InstallError(
                    f"backup root is inside install target {entry_id}: {root}"
                )
        for spec in specs:
            source = spec["source"].resolve(strict=False)
            if root == source or source in root.parents:
                raise InstallError(
                    f"backup root is inside canonical source {spec['id']}: {root}"
                )

    if backup_directory is not None:
        transaction = backup_directory.resolve(strict=False)
        for entry_id, target in targets:
            if paths_overlap(transaction, target):
                raise InstallError(
                    f"backup transaction overlaps install target {entry_id}: "
                    f"{transaction}"
                )
        for spec in specs:
            source = spec["source"].resolve(strict=False)
            if paths_overlap(transaction, source):
                raise InstallError(
                    f"backup transaction overlaps canonical source {spec['id']}: "
                    f"{transaction}"
                )


def validate_sources(specs: Iterable[dict[str, Any]]) -> None:
    errors: list[str] = []
    for spec in specs:
        source = spec["source"]
        expected = spec["source_type"]
        actual = path_type(source)
        if actual != expected:
            errors.append(
                f"{spec['id']}: source must be {expected}, got {actual}: {source}"
            )
        elif expected == "directory" and not (source / "SKILL.md").is_file():
            errors.append(f"{spec['id']}: source has no SKILL.md: {source}")
    if errors:
        raise InstallError("invalid canonical sources:\n  " + "\n  ".join(errors))


def exact_symlink(target: Path, source: Path) -> bool:
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) and os.readlink(target) == str(source)


def regular_file_matches(
    path: Path,
    sha256: str,
    mode: int,
    *,
    require_single_link: bool = True,
) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and (not require_single_link or metadata.st_nlink == 1)
        and stat.S_IMODE(metadata.st_mode) == mode
        and sha256_file(path) == sha256
    )


def spec_installed_current(spec: dict[str, Any]) -> bool:
    if spec["install_mode"] == "symlink":
        return exact_symlink(spec["target"], spec["source"])
    source = spec["source"]
    if path_type(source) != "regular-file":
        return False
    metadata = source.lstat()
    return regular_file_matches(
        spec["target"],
        sha256_file(source),
        stat.S_IMODE(metadata.st_mode),
    )


def entry_installed_current(entry: dict[str, Any]) -> bool:
    target = Path(entry["target"])
    if entry["install_mode"] == "symlink":
        return exact_symlink(target, Path(entry["installed_link_text"]))
    return regular_file_matches(
        target,
        entry["installed_source_sha256"],
        entry["installed_mode"],
    )


def ensure_parent(parent: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    cursor = parent
    while not lexists(cursor):
        missing.append(cursor)
        next_cursor = cursor.parent
        if next_cursor == cursor:
            raise InstallError(f"cannot find an existing ancestor for {parent}")
        cursor = next_cursor
    if not cursor.is_dir():
        raise InstallError(f"target parent ancestor is not a directory: {cursor}")
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except OSError as exc:
            raise InstallError(f"cannot create target parent {directory}: {exc}") from exc
        created.append(directory)


def create_exact_symlink(source: Path, target: Path) -> None:
    """Atomically create a single filesystem object, failing on any collision."""

    if lexists(target):
        raise InstallError(f"target reappeared before link creation: {target}")
    try:
        os.symlink(str(source), str(target), target_is_directory=source.is_dir())
    except OSError as exc:
        raise InstallError(f"cannot create symlink {target}: {exc}") from exc
    if not exact_symlink(target, source):
        raise InstallError(f"created link did not match canonical source: {target}")


def create_sealed_copy(
    source: Path,
    target: Path,
    *,
    expected_sha256: str,
    expected_mode: int,
    source_must_be_single_link: bool = True,
) -> None:
    """Atomically project one sealed regular file without overwriting a race."""

    if lexists(target):
        raise InstallError(f"target reappeared before copy creation: {target}")
    if not regular_file_matches(
        source,
        expected_sha256,
        expected_mode,
        require_single_link=source_must_be_single_link,
    ):
        raise InstallError(f"sealed copy source changed before projection: {source}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    linked = False
    try:
        with os.fdopen(descriptor, "wb") as output_handle, source.open(
            "rb"
        ) as input_handle:
            shutil.copyfileobj(input_handle, output_handle)
            os.fchmod(output_handle.fileno(), expected_mode)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if not regular_file_matches(temporary, expected_sha256, expected_mode):
            raise InstallError(f"sealed temporary copy verification failed: {temporary}")
        try:
            os.link(temporary, target, follow_symlinks=False)
        except OSError as exc:
            raise InstallError(f"cannot install sealed copy {target}: {exc}") from exc
        linked = True
        temporary.unlink()
        if not regular_file_matches(target, expected_sha256, expected_mode):
            raise InstallError(f"installed sealed copy verification failed: {target}")
    finally:
        if lexists(temporary):
            temporary.unlink()
        if linked and not regular_file_matches(target, expected_sha256, expected_mode):
            target.unlink(missing_ok=True)


def create_installed_object(entry: dict[str, Any]) -> None:
    target = checked_entry_target(entry)
    if entry["install_mode"] == "symlink":
        create_exact_symlink(Path(entry["installed_link_text"]), target)
        return
    projection = Path(entry["projection_path"])
    create_sealed_copy(
        projection,
        target,
        expected_sha256=entry["installed_source_sha256"],
        expected_mode=entry["installed_mode"],
    )


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if lexists(temporary):
            temporary.unlink()


def backup_existing(
    target: Path,
    backup: Path,
    expected_snapshot: dict[str, Any],
) -> None:
    if snapshot_path(target) != expected_snapshot:
        raise InstallError(f"target changed after inventory: {target}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if lexists(backup):
        raise InstallError(f"backup destination already exists: {backup}")
    try:
        shutil.move(str(target), str(backup))
    except OSError as exc:
        raise InstallError(f"cannot back up {target}: {exc}") from exc
    if lexists(target):
        raise InstallError(f"target remained after backup move: {target}")
    if snapshot_path(backup) != expected_snapshot:
        raise InstallError(f"backup verification failed: {backup}")


def remove_created_parents(created: Iterable[Path]) -> list[str]:
    errors: list[str] = []
    for directory in reversed(list(created)):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            # A directory that now contains user or restored data is retained.
            continue
    return errors


def rollback_install(
    entries: Sequence[dict[str, Any]],
    created_parents: Sequence[Path],
    backup_directory: Path,
) -> None:
    errors: list[str] = []
    for entry in reversed(entries):
        if entry["action"] != "installed":
            continue
        try:
            target = checked_entry_target(entry)
        except InstallError as exc:
            errors.append(str(exc))
            continue
        backup_text = entry["backup_path"]
        backup = Path(backup_text) if backup_text else None
        original = entry["original"]

        if entry_installed_current(entry):
            try:
                target.unlink()
            except OSError as exc:
                errors.append(f"cannot remove installed target {target}: {exc}")
                continue
        elif lexists(target):
            if snapshot_path(target) != original:
                errors.append(f"refusing to overwrite changed rollback target {target}")
                continue

        if backup is not None and lexists(backup):
            if lexists(target):
                errors.append(f"rollback target is occupied: {target}")
                continue
            try:
                ensure_parent(target.parent, [])
                shutil.move(str(backup), str(target))
            except (OSError, InstallError) as exc:
                errors.append(f"cannot restore backup for {target}: {exc}")
                continue

        if snapshot_path(target) != original:
            errors.append(f"rollback verification failed: {target}")

    if errors:
        raise InstallError("rollback failed:\n  " + "\n  ".join(errors))
    remove_created_parents(created_parents)
    try:
        shutil.rmtree(backup_directory)
    except OSError as exc:
        raise InstallError(
            f"rollback restored targets but could not remove {backup_directory}: {exc}"
        ) from exc


def manifest_entry(
    spec: dict[str, Any],
    *,
    backup_directory: Path,
) -> dict[str, Any]:
    target = spec["target"]
    source = spec["source"]
    target_mutation_path = mutation_location(target)
    original = snapshot_path(target)
    unchanged = spec_installed_current(spec)
    backup: Path | None = None
    if not unchanged and original["lstat_type"] != "absent":
        backup = backup_directory / "payload" / spec["id"]
    install_mode = spec["install_mode"]
    installed_sha256: str | None = None
    installed_mode: int | None = None
    projection: Path | None = None
    installed_link_text: str | None = None
    if install_mode == "symlink":
        installed_link_text = str(source)
    else:
        metadata = source.lstat()
        installed_sha256 = sha256_file(source)
        installed_mode = stat.S_IMODE(metadata.st_mode)
        if not unchanged:
            projection = backup_directory / "projections" / spec["id"]
    entry = {
        "id": spec["id"],
        "target": str(target),
        "target_mutation_path": str(target_mutation_path),
        "source": str(source),
        "install_mode": install_mode,
        "installed_link_text": installed_link_text,
        "installed_source_sha256": installed_sha256,
        "installed_mode": installed_mode,
        "projection_path": str(projection) if projection else None,
        "action": "unchanged" if unchanged else "installed",
        "original": original,
        "backup_path": str(backup) if backup else None,
    }
    checked_entry_target(entry)
    return entry


def prepare_projection(entry: dict[str, Any]) -> None:
    projection_text = entry["projection_path"]
    if projection_text is None:
        return
    projection = Path(projection_text)
    projection.parent.mkdir(parents=True, exist_ok=True)
    create_sealed_copy(
        Path(entry["source"]),
        projection,
        expected_sha256=entry["installed_source_sha256"],
        expected_mode=entry["installed_mode"],
        source_must_be_single_link=False,
    )


def perform_install(
    specs: Sequence[dict[str, Any]], backup_root: Path
) -> Path | None:
    if not specs:
        raise InstallError("no install targets selected")
    validate_sources(specs)
    validate_layout(specs, backup_root=backup_root)
    if all(spec_installed_current(spec) for spec in specs):
        print(f"ALREADY INSTALLED: all {len(specs)} targets match canonical sources")
        return None

    root = absolute_root(backup_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"cannot create backup root {root}: {exc}") from exc
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_directory = Path(
        tempfile.mkdtemp(prefix=f"{timestamp}-", dir=root)
    ).resolve(strict=False)
    try:
        validate_layout(
            specs,
            backup_root=backup_root,
            backup_directory=backup_directory,
        )
    except Exception:
        backup_directory.rmdir()
        raise
    manifest_path = backup_directory / "manifest.json"
    try:
        entries = [
            manifest_entry(spec, backup_directory=backup_directory) for spec in specs
        ]
        created_parents: list[Path] = []
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "installer": INSTALLER_NAME,
            "state": "installing",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(absolute_root(specs[0].get("repo_root", REPO_ROOT))),
            "backup_directory": str(backup_directory),
            "created_parent_directories": [],
            "entries": entries,
        }
        write_json_atomic(manifest_path, manifest)
    except Exception as exc:
        try:
            shutil.rmtree(backup_directory)
        except OSError as cleanup_error:
            raise InstallError(
                f"transaction setup failed ({exc}); could not remove "
                f"{backup_directory}: {cleanup_error}"
            ) from exc
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"transaction setup failed: {exc}") from exc

    try:
        for entry in entries:
            if entry["action"] == "installed":
                prepare_projection(entry)
        for entry in entries:
            if entry["action"] == "unchanged":
                continue
            target = checked_entry_target(entry)
            source = Path(entry["source"])
            backup_text = entry["backup_path"]
            if backup_text:
                backup_existing(target, Path(backup_text), entry["original"])
            elif snapshot_path(target)["lstat_type"] != "absent":
                raise InstallError(f"new target changed after inventory: {target}")
            ensure_parent(target.parent, created_parents)
            create_installed_object(entry)

        for entry in entries:
            checked_entry_target(entry)

        for entry in entries:
            if entry["install_mode"] != "sealed-copy":
                continue
            source = Path(entry["source"])
            if not regular_file_matches(
                source,
                entry["installed_source_sha256"],
                entry["installed_mode"],
                require_single_link=False,
            ):
                raise InstallError(f"canonical source changed during install: {source}")

        manifest["created_parent_directories"] = [
            str(path) for path in created_parents
        ]
        manifest["state"] = "installed"
        write_json_atomic(manifest_path, manifest)
    except Exception as exc:
        try:
            rollback_install(entries, created_parents, backup_directory)
        except InstallError as rollback_error:
            raise InstallError(
                f"installation failed ({exc}); {rollback_error}"
            ) from exc
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"installation failed and was rolled back: {exc}") from exc

    for entry in entries:
        if entry["action"] == "unchanged":
            print(
                f"KEPT {entry['target']} ({entry['install_mode']}) "
                f"<= {entry['source']}"
            )
        elif entry["install_mode"] == "symlink":
            print(f"LINKED {entry['target']} -> {entry['source']}")
        else:
            print(
                f"COPIED {entry['target']} <= {entry['source']} "
                f"sha256={entry['installed_source_sha256']}"
            )
    print(f"MANIFEST {manifest_path}")
    return manifest_path


def validate_snapshot_document(snapshot: Any, entry_id: str) -> None:
    if not isinstance(snapshot, dict):
        raise InstallError(f"{entry_id}: invalid original snapshot")
    required = {"lstat_type", "link_text", "mode", "tree_entries"}
    if not required.issubset(snapshot):
        raise InstallError(f"{entry_id}: incomplete original snapshot")
    if not isinstance(snapshot["tree_entries"], list):
        raise InstallError(f"{entry_id}: invalid tree inventory")


def load_manifest(
    manifest_path: Path,
    expected_specs: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    expected_ids = [spec["id"] for spec in expected_specs]
    if not expected_ids or len(expected_ids) != len(set(expected_ids)):
        raise InstallError("restore requires nonempty, unique target selection")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"cannot read restore manifest {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InstallError("restore manifest root must be an object")
    schema_version = payload.get("schema_version")
    if schema_version == 2:
        required_ids = LEGACY_SPEC_IDS_V2
    elif schema_version == 3:
        required_ids = LEGACY_SPEC_IDS_V3
    elif schema_version == SCHEMA_VERSION:
        required_ids = tuple(spec["id"] for spec in expected_specs)
    else:
        raise InstallError("unsupported restore manifest schema")
    if payload.get("installer") != INSTALLER_NAME:
        raise InstallError("manifest was not created by this installer")
    if payload.get("state") != "installed":
        raise InstallError("manifest does not describe a completed installation")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != len(required_ids):
        raise InstallError(
            f"restore manifest must contain exactly {len(required_ids)} entries"
        )

    expected = {
        spec["id"]: spec for spec in expected_specs if spec["id"] in required_ids
    }
    if set(expected) != set(required_ids):
        raise InstallError("restore target selection does not match manifest schema")
    requested_root = absolute_root(expected_specs[0].get("repo_root", REPO_ROOT))
    if payload.get("repo_root") != str(requested_root):
        raise InstallError("manifest repo root does not match requested source")
    seen_targets: set[str] = set()
    seen_ids: set[str] = set()
    backup_directory = manifest_path.resolve(strict=False).parent
    if payload.get("backup_directory") != str(backup_directory):
        raise InstallError("manifest backup directory does not match its location")

    for entry in entries:
        if not isinstance(entry, dict):
            raise InstallError("restore manifest entry must be an object")
        entry_id = entry.get("id")
        if entry_id not in expected or entry_id in seen_ids:
            raise InstallError(f"unexpected or duplicate manifest entry: {entry_id}")
        seen_ids.add(entry_id)
        spec = expected[entry_id]
        target_text = entry.get("target")
        source_text = entry.get("source")
        allowed_sources = {str(spec["source"])}
        if schema_version in {2, 3} and "legacy_source" in spec:
            allowed_sources.add(str(spec["legacy_source"]))
        if target_text != str(spec["target"]) or source_text not in allowed_sources:
            raise InstallError(f"{entry_id}: manifest paths do not match requested roots")
        if not Path(target_text).is_absolute() or not Path(source_text).is_absolute():
            raise InstallError(f"{entry_id}: target and source must be absolute")
        if schema_version in {2, 3}:
            # Historical manifests did not seal parent aliases.  Restore only
            # when the literal recorded target is its present mutation path;
            # do not guess which collection an old alias used to identify.
            if mutation_location(Path(target_text)) != Path(target_text):
                raise InstallError(f"{entry_id}: legacy target parent alias cannot be verified")
            entry["target_mutation_path"] = target_text
        checked_entry_target(entry)
        if target_text in seen_targets:
            raise InstallError(f"duplicate manifest target: {target_text}")
        seen_targets.add(target_text)
        install_mode = entry.get("install_mode")
        if install_mode != spec["install_mode"]:
            raise InstallError(f"{entry_id}: manifest install mode is invalid")
        projection_text = entry.get("projection_path")
        if install_mode == "symlink":
            if entry.get("installed_link_text") != source_text:
                raise InstallError(f"{entry_id}: invalid installed link text")
            if entry.get("installed_source_sha256") is not None:
                raise InstallError(f"{entry_id}: symlink has unexpected source hash")
            if entry.get("installed_mode") is not None:
                raise InstallError(f"{entry_id}: symlink has unexpected installed mode")
            if projection_text is not None:
                raise InstallError(f"{entry_id}: symlink has unexpected projection")
        else:
            digest = entry.get("installed_source_sha256")
            installed_mode = entry.get("installed_mode")
            if entry.get("installed_link_text") is not None:
                raise InstallError(f"{entry_id}: sealed copy has unexpected link text")
            if (
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise InstallError(f"{entry_id}: sealed copy hash is invalid")
            if not isinstance(installed_mode, int) or isinstance(installed_mode, bool):
                raise InstallError(f"{entry_id}: sealed copy mode is invalid")
        action = entry.get("action")
        if action not in {"installed", "unchanged"}:
            raise InstallError(f"{entry_id}: invalid action")
        validate_snapshot_document(entry.get("original"), entry_id)
        backup_text = entry.get("backup_path")
        original_absent = entry["original"]["lstat_type"] == "absent"
        if action == "unchanged" or original_absent:
            if backup_text is not None:
                raise InstallError(f"{entry_id}: unexpected backup path")
        else:
            expected_backup = backup_directory / "payload" / entry_id
            if backup_text != str(expected_backup):
                raise InstallError(f"{entry_id}: invalid backup path")
        if install_mode == "sealed-copy" and action == "installed":
            expected_projection = backup_directory / "projections" / entry_id
            if projection_text != str(expected_projection):
                raise InstallError(f"{entry_id}: invalid sealed-copy projection path")
        elif projection_text is not None:
            raise InstallError(f"{entry_id}: unexpected sealed-copy projection path")

    if seen_ids != set(required_ids):
        raise InstallError("restore manifest entry set is incomplete")
    created = payload.get("created_parent_directories")
    if not isinstance(created, list) or not all(
        isinstance(value, str) and Path(value).is_absolute() for value in created
    ):
        raise InstallError("invalid created-parent directory inventory")
    targets = [checked_entry_target(entry) for entry in entries]
    for directory_text in created:
        directory = Path(directory_text)
        if not any(directory in target.parents for target in targets):
            raise InstallError(f"created parent is not an ancestor of a target: {directory}")
    return payload


def preflight_restore(entries: Sequence[dict[str, Any]]) -> None:
    errors: list[str] = []
    for entry in entries:
        if entry["action"] != "installed":
            continue
        try:
            target = checked_entry_target(entry)
        except InstallError as exc:
            errors.append(str(exc))
            continue
        if not entry_installed_current(entry):
            errors.append(f"target no longer matches the installed object: {target}")
        projection_text = entry["projection_path"]
        if projection_text is not None and not regular_file_matches(
            Path(projection_text),
            entry["installed_source_sha256"],
            entry["installed_mode"],
        ):
            errors.append(f"sealed-copy projection is missing or changed: {projection_text}")
        backup_text = entry["backup_path"]
        if backup_text:
            backup = Path(backup_text)
            if snapshot_path(backup) != entry["original"]:
                errors.append(f"backup is missing or changed: {backup}")
    if errors:
        raise InstallError("restore preflight failed:\n  " + "\n  ".join(errors))


def rollback_restore(entries: Sequence[dict[str, Any]]) -> None:
    errors: list[str] = []
    for entry in reversed(entries):
        try:
            target = checked_entry_target(entry)
        except InstallError as exc:
            errors.append(str(exc))
            continue
        backup_text = entry["backup_path"]
        backup = Path(backup_text) if backup_text else None
        original = entry["original"]

        if backup is not None and snapshot_path(target) == original:
            if lexists(backup):
                errors.append(f"restore rollback backup is occupied: {backup}")
                continue
            try:
                ensure_parent(backup.parent, [])
                shutil.move(str(target), str(backup))
            except (OSError, InstallError) as exc:
                errors.append(f"cannot put restored data back into backup: {exc}")
                continue
        elif original["lstat_type"] == "absent" and lexists(target):
            errors.append(f"unexpected object during restore rollback: {target}")
            continue

        if not lexists(target):
            try:
                ensure_parent(target.parent, [])
                create_installed_object(entry)
            except InstallError as exc:
                errors.append(str(exc))
        elif not entry_installed_current(entry):
            errors.append(f"cannot recreate installed object over changed target: {target}")

    if errors:
        raise InstallError("restore rollback failed:\n  " + "\n  ".join(errors))


def perform_restore(
    manifest_path: Path,
    expected_specs: Sequence[dict[str, Any]],
) -> None:
    manifest = load_manifest(manifest_path.resolve(strict=False), expected_specs)
    manifest_ids = {entry["id"] for entry in manifest["entries"]}
    validate_layout(
        [spec for spec in expected_specs if spec["id"] in manifest_ids],
        backup_directory=manifest_path.resolve(strict=False).parent,
    )
    entries = [
        entry for entry in manifest["entries"] if entry["action"] == "installed"
    ]
    preflight_restore(entries)

    applied: list[dict[str, Any]] = []
    try:
        for entry in entries:
            target = checked_entry_target(entry)
            target.unlink()
            applied.append(entry)
            backup_text = entry["backup_path"]
            if backup_text:
                shutil.move(backup_text, str(target))
            if snapshot_path(target) != entry["original"]:
                raise InstallError(f"restored target verification failed: {target}")
    except Exception as exc:
        try:
            rollback_restore(applied)
        except InstallError as rollback_error:
            raise InstallError(f"restore failed ({exc}); {rollback_error}") from exc
        if isinstance(exc, InstallError):
            raise
        raise InstallError(f"restore failed and was rolled back: {exc}") from exc

    for entry in manifest["entries"]:
        checked_entry_target(entry)
    created = [Path(value) for value in manifest["created_parent_directories"]]
    remove_created_parents(created)
    print(f"RESTORED {manifest_path.resolve(strict=False)}")


def perform_check(specs: Sequence[dict[str, Any]]) -> bool:
    ok = True
    try:
        validate_sources(specs)
        validate_layout(specs)
    except InstallError as exc:
        print(f"CHECK ERROR {exc}")
        ok = False
    for spec in specs:
        target = spec["target"]
        source = spec["source"]
        source_kind = path_type(source)
        current = spec_installed_current(spec)
        if current and source_kind == spec["source_type"]:
            if spec["install_mode"] == "symlink":
                print(f"CHECK OK {target} -> {source}")
            else:
                print(
                    f"CHECK OK {target} <= {source} "
                    f"(sealed copy sha256={sha256_file(source)})"
                )
        else:
            snapshot = snapshot_path(target)
            detail = snapshot["lstat_type"]
            if detail == "symlink":
                detail += f" -> {snapshot['link_text']}"
            if current:
                detail += (
                    f"; canonical source is {source_kind}, "
                    f"expected {spec['source_type']}"
                )
            expected = (
                f"symlink -> {source}"
                if spec["install_mode"] == "symlink"
                else f"sealed copy <= {source}"
            )
            print(f"CHECK ERROR {target}: {detail}; expected {expected}")
            ok = False
    return ok


def perform_dry_run(specs: Sequence[dict[str, Any]], backup_root: Path) -> None:
    validate_sources(specs)
    validate_layout(specs, backup_root=backup_root)
    for spec in specs:
        target = spec["target"]
        source = spec["source"]
        install_mode = spec["install_mode"]
        if spec_installed_current(spec):
            print(f"DRY-RUN KEEP {target} ({install_mode}) <= {source}")
            continue
        original = snapshot_path(target)
        operation = "LINK" if install_mode == "symlink" else "COPY"
        arrow = "->" if install_mode == "symlink" else "<="
        if original["lstat_type"] == "absent":
            print(f"DRY-RUN {operation} {target} {arrow} {source}")
        else:
            print(
                f"DRY-RUN BACKUP {target} ({original['lstat_type']}) "
                f"under {absolute_root(backup_root)}; {operation} {arrow} {source}"
            )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--check", action="store_true", help="verify selected installed targets"
    )
    actions.add_argument(
        "--dry-run", action="store_true", help="show the install transaction"
    )
    actions.add_argument(
        "--install", action="store_true", help="install with a recoverable backup"
    )
    actions.add_argument(
        "--restore", type=Path, metavar="MANIFEST", help="restore a prior manifest"
    )
    parser.add_argument(
        "--codex-root",
        type=Path,
        help="legacy seven-target mode: Codex configuration root (default: ~/.codex)",
    )
    parser.add_argument(
        "--claude-root",
        type=Path,
        help="legacy seven-target mode: Claude configuration root (default: ~/.claude)",
    )
    parser.add_argument(
        "--gemini-root",
        type=Path,
        help="legacy seven-target mode: Antigravity root (default: ~/.gemini/config); historical flag name retained",
    )
    parser.add_argument("--agent", action="append", choices=PORTABLE_AGENTS,
                        help="select an agent (repeatable; default: generic)")
    parser.add_argument("--skill-dir", type=Path,
                        help="custom skill collection directory; appends jyut-ocr-proofread")
    parser.add_argument("--with-agents", action="store_true",
                        help="also install custom role adapters for selected Codex/Claude agents")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=Path.home() / ".jyut-ocr-proofread-backups",
        help="directory for timestamped backup transactions",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        legacy_roots = any(value is not None for value in (
            arguments.codex_root, arguments.claude_root, arguments.gemini_root
        ))
        legacy_restore = False
        if arguments.restore is not None and not (arguments.agent or arguments.skill_dir):
            try:
                header = json.loads(arguments.restore.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise InstallError(f"cannot read restore manifest: {exc}") from exc
            legacy_restore = isinstance(header, dict) and header.get("schema_version") in {2, 3}
        if legacy_roots or legacy_restore:
            if arguments.agent or arguments.skill_dir or arguments.with_agents:
                raise InstallError("legacy root options cannot be combined with portable selection options")
            specs = build_specs(
                arguments.codex_root or Path.home() / ".codex",
                arguments.claude_root or Path.home() / ".claude",
                arguments.gemini_root or Path.home() / ".gemini/config",
            )
        else:
            specs = build_portable_specs(
                agents=arguments.agent or ["generic"],
                skill_dir=arguments.skill_dir, with_agents=arguments.with_agents,
            )
        if arguments.check:
            return 0 if perform_check(specs) else 1
        if arguments.dry_run:
            perform_dry_run(specs, arguments.backup_root)
            return 0
        if arguments.install:
            perform_install(specs, arguments.backup_root)
            return 0
        if arguments.restore is not None:
            perform_restore(arguments.restore, specs)
            return 0
        raise InstallError("no action selected")
    except InstallError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
