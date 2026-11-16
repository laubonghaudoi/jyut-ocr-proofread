#!/usr/bin/env python3
"""Synchronize the pinned human benchmark snapshot; never change local ratchets.

Run with --check for a read-only integrity check. The benchmark checkout must
be at the exact committed revision and all manifest files must match it. Local
machine/regression assertions, case source metadata and stage ratchets belong
to this skill, while human decisions and referenced goldens belong to the bench.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPOSITORY = "https://github.com/laubonghaudoi/jyut-ocr-bench"
LOCAL_RATCHETS = frozenset({
    "ratchets", "ratchetFrom", "maxErrors", "minMarkRecall",
    "minMarkPrecision", "maxStructureErrors", "minCellsExact",
})
SKILL_ROOT = Path(__file__).resolve().parents[1]


class SnapshotError(ValueError):
    """A source or compatibility snapshot cannot safely be synchronized."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def semantic_hash(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or ".." in path.parts or str(path) != relative:
        raise SnapshotError(f"unsafe relative path: {relative!r}")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise SnapshotError(f"path escapes repository: {relative}")
    return result


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SnapshotError(f"expected JSON object: {path}")
    return value


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True)
    if result.returncode:
        raise SnapshotError(f"git {' '.join(arguments)}: {result.stderr.decode().strip()}")
    return result.stdout


def human_projection(case: dict) -> list[dict]:
    """Only benchmark-owned decisions; local stage thresholds are not owned."""
    return sorted(({key: value for key, value in assertion.items()
                    if key not in LOCAL_RATCHETS}
                   for assertion in case.get("assertions", [])
                   if assertion.get("provenance") == "human"), key=lambda x: x["id"])


def verified_source(root: Path, commit: str) -> tuple[dict, dict[str, str], bytes]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SnapshotError("--commit must be an exact 40-character lowercase Git SHA")
    if git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise SnapshotError("benchmark HEAD does not match the requested commit")
    manifest_path = root / "data/manifest.json"
    manifest = read_json(manifest_path)
    manifest_bytes = manifest_path.read_bytes()
    if git(root, "show", f"{commit}:data/manifest.json") != manifest_bytes:
        raise SnapshotError("benchmark manifest differs from the pinned commit")
    if manifest.get("schema_version") != 1 or not manifest.get("dataset_version"):
        raise SnapshotError("unsupported or missing benchmark manifest version")
    files: dict[str, str] = {}
    for entry in manifest.get("files", []):
        name, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise SnapshotError("manifest file requires path and sha256")
        if name in files or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SnapshotError(f"duplicate path or invalid hash: {name}")
        path = safe_path(root, name)
        if not path.is_file() or path.is_symlink() or sha256(path.read_bytes()) != digest:
            raise SnapshotError(f"benchmark content hash mismatch: {name}")
        files[name] = digest
    tracked = set(git(root, "ls-tree", "-rz", "--name-only", commit).decode().split("\0"))
    missing = set(files) - tracked
    if missing:
        raise SnapshotError(f"manifest file not committed: {sorted(missing)[0]}")
    dirty = git(root, "status", "--porcelain", "--untracked-files=all", "--",
                "data", *files).decode().strip()
    if dirty:
        raise SnapshotError(f"benchmark managed data is dirty: {dirty.splitlines()[0]}")
    return manifest, files, manifest_bytes


def merge_case(current: dict | None, canonical: dict) -> dict:
    assertions = canonical.get("assertions")
    if not isinstance(assertions, list) or any(a.get("provenance") != "human" for a in assertions):
        raise SnapshotError("canonical cases may contain human assertions only")
    by_id = {a["id"]: a for a in assertions}
    if len(by_id) != len(assertions):
        raise SnapshotError("duplicate canonical assertion ID")
    merged = copy.deepcopy(current if current is not None else canonical)
    result, used = [], set()
    for old in (current or {}).get("assertions", []):
        identifier = old["id"]
        if identifier in used:
            raise SnapshotError(f"duplicate local assertion ID: {identifier}")
        used.add(identifier)
        if old.get("provenance") != "human":
            if identifier in by_id:
                raise SnapshotError(f"human/nonhuman assertion ID collision: {identifier}")
            result.append(copy.deepcopy(old))
        elif identifier in by_id:
            updated = {k: copy.deepcopy(v) for k, v in by_id[identifier].items()
                       if k not in LOCAL_RATCHETS}
            updated.update({k: copy.deepcopy(v) for k, v in old.items() if k in LOCAL_RATCHETS})
            result.append(updated)
    result.extend(copy.deepcopy(a) for a in assertions if a["id"] not in used)
    merged["assertions"] = result
    return merged


def check_local_lock(skill_root: Path, lock: dict) -> list[str]:
    errors = []
    if lock.get("schema_version") != 1:
        return ["unsupported benchmark lock schema"]
    for name, digest in lock.get("managed_files", {}).items():
        path = safe_path(skill_root, name)
        if not path.is_file() or sha256(path.read_bytes()) != digest:
            errors.append(f"managed file changed: {name}")
    for name, digest in lock.get("human_projections", {}).items():
        path = safe_path(skill_root, name)
        if not path.is_file() or semantic_hash(human_projection(read_json(path))) != digest:
            errors.append(f"managed human decisions changed: {name}")
    return errors


def synchronize(benchmark_root: Path, skill_root: Path, commit: str,
                *, check: bool = False) -> dict:
    manifest, files, manifest_bytes = verified_source(benchmark_root, commit)
    lock_path = skill_root / "tests/benchmark.lock.json"
    previous = read_json(lock_path) if lock_path.exists() else None
    if previous:
        errors = check_local_lock(skill_root, previous)
        if errors:
            raise SnapshotError("; ".join(errors))
    writes: dict[str, bytes] = {}
    projections, managed_files = {}, {}
    seen_books = set()
    if not isinstance(manifest.get("books"), list) or not manifest["books"]:
        raise SnapshotError("benchmark manifest must name at least one book")
    for book in manifest.get("books", []):
        identifier, case_name = book.get("id"), book.get("case")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier):
            raise SnapshotError("invalid benchmark book ID")
        if identifier in seen_books:
            raise SnapshotError(f"duplicate benchmark book ID: {identifier}")
        seen_books.add(identifier)
        if case_name not in files:
            raise SnapshotError(f"case missing from manifest files: {case_name}")
        canonical = read_json(safe_path(benchmark_root, case_name))
        if canonical.get("bookId") != identifier:
            raise SnapshotError(f"case/book ID mismatch: {identifier}")
        relative = f"tests/cases/{identifier}.json"
        target = safe_path(skill_root, relative)
        current = read_json(target) if target.exists() else None
        merged = merge_case(current, canonical)
        projections[relative] = semantic_hash(human_projection(merged))
        if current != merged:
            writes[relative] = json_bytes(merged)
        for assertion in canonical["assertions"]:
            if assertion["type"] in {"golden_page", "golden_table", "golden_tree"}:
                name = assertion["value"]
                source = f"data/golden/{name}"
                if source not in files:
                    raise SnapshotError(f"human golden missing from manifest files: {source}")
                destination = f"tests/golden/{name}"
                safe_path(skill_root, destination)
                data = safe_path(benchmark_root, source).read_bytes()
                writes[destination] = data
                managed_files[destination] = sha256(data)
    # A book removed upstream must not leave formerly managed human decisions
    # active locally. Its independent regression/machine checks remain intact.
    for name in (previous or {}).get("human_projections", {}):
        if name not in projections:
            current = read_json(safe_path(skill_root, name))
            current["assertions"] = [a for a in current["assertions"]
                                     if a.get("provenance") != "human"]
            writes[name] = json_bytes(current)
    policy = "data/policies/variant-equivalents.json"
    if policy not in files:
        raise SnapshotError("human variant policy missing from manifest files")
    data = safe_path(benchmark_root, policy).read_bytes()
    writes["references/variant-equivalents.json"] = data
    managed_files["references/variant-equivalents.json"] = sha256(data)
    lock = {
        "schema_version": 1, "repository": REPOSITORY, "commit": commit,
        "dataset_version": manifest["dataset_version"],
        "manifest_sha256": sha256(manifest_bytes),
        "human_projection_excludes": sorted(LOCAL_RATCHETS),
        "human_projections": dict(sorted(projections.items())),
        "managed_files": dict(sorted(managed_files.items())),
    }
    if check:
        if previous != lock:
            raise SnapshotError("benchmark lock is absent or does not match the pinned source")
        errors = check_local_lock(skill_root, lock)
        if errors:
            raise SnapshotError("; ".join(errors))
        return lock
    # All source, local-ownership and path checks complete before any mutation.
    writes["tests/benchmark.lock.json"] = json_bytes(lock)
    for name in writes:
        path = safe_path(skill_root, name)
        temporary = path.with_name(path.name + ".benchmark-tmp")
        if temporary.exists():
            raise SnapshotError(f"temporary file already exists: {temporary}")
    for name, content in writes.items():
        path = safe_path(skill_root, name)
        if path.is_file() and path.read_bytes() == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".benchmark-tmp")
        with temporary.open("xb") as stream:
            stream.write(content)
        temporary.replace(path)
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path,
                        default=SKILL_ROOT.parent / "jyut-ocr-bench")
    parser.add_argument("--commit", help="exact benchmark commit (default: the current lock)")
    parser.add_argument("--check", action="store_true", help="check without modifying any files")
    arguments = parser.parse_args(argv)
    try:
        commit = arguments.commit
        if not commit:
            commit = read_json(SKILL_ROOT / "tests/benchmark.lock.json").get("commit")
        lock = synchronize(arguments.benchmark_root.resolve(), SKILL_ROOT, commit,
                           check=arguments.check)
    except (SnapshotError, OSError, KeyError, TypeError) as exc:
        print(f"benchmark snapshot: {exc}", file=sys.stderr)
        return 1
    print(f"benchmark snapshot {'verified' if arguments.check else 'synchronized'}: "
          f"{lock['dataset_version']} at {lock['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
