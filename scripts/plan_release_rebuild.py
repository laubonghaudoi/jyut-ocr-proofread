#!/usr/bin/env python3
"""Plan a dependency-safe rebuild for stale OCR release evidence.

The input is the strict v1 ``release-plan.json`` accepted by
``validate_release_evidence.py``.  This read-only diagnostic hashes every
active included artifact, marks missing or hash-mismatched files directly
stale, then propagates staleness from each dependency to its dependents.

Exit status is deliberately distinct from the release validator:

* 0: every active included artifact still matches its sealed SHA-256;
* 1: the plan is structurally valid, but one or more artifacts are stale;
* 2: the plan cannot be analysed safely (invalid schema/path/graph).

Excluded, historical, and superseded evidence is never used to decide the
rebuild closure.  It is reported under ``skipped`` instead.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import heapq
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

import validate_release_evidence as strict
from _ocr_markdown import paths_refer_to_same_file


EXIT_CLEAN = 0
EXIT_STALE = 1
EXIT_INVALID = 2


class RebuildPlanError(ValueError):
    """The release plan cannot be analysed safely."""


TOOL_PATH = Path(__file__).resolve()
STRICT_VALIDATOR_PATH = Path(strict.__file__).resolve()


def _stable_file_bytes(path: Path) -> bytes:
    """Read one regular file and fail closed if it mutates during the read."""

    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        payload = handle.read()
        after = os.fstat(handle.fileno())
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(payload) != after.st_size:
        raise RebuildPlanError(f"file changed while being read: {path.name!r}")
    return payload


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(_stable_file_bytes(path))


def _glob_parts_match(
    path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]
) -> bool:
    """Match a relative path using pathlib-style ``**`` segment semantics."""

    memo: dict[tuple[int, int], bool] = {}

    def visit(path_index: int, pattern_index: int) -> bool:
        key = (path_index, pattern_index)
        if key in memo:
            return memo[key]
        if pattern_index == len(pattern_parts):
            result = path_index == len(path_parts)
        elif pattern_parts[pattern_index] == "**":
            result = visit(path_index, pattern_index + 1) or (
                path_index < len(path_parts) and visit(path_index + 1, pattern_index)
            )
        else:
            result = (
                path_index < len(path_parts)
                and fnmatch.fnmatchcase(
                    path_parts[path_index], pattern_parts[pattern_index]
                )
                and visit(path_index + 1, pattern_index + 1)
            )
        memo[key] = result
        return result

    return visit(0, 0)


def _declared_path_is_discoverable(
    raw_path: object, discovery: object
) -> bool:
    """Return whether a missing relative path belongs to a declared glob."""

    if not isinstance(raw_path, str) or not raw_path or not isinstance(discovery, dict):
        return False
    globs = discovery.get("globs")
    if not isinstance(globs, list):
        return False
    path_parts = Path(raw_path).parts
    for pattern in globs:
        if not isinstance(pattern, str) or not pattern:
            continue
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or any(
            part == ".." for part in pattern_path.parts
        ):
            continue
        if _glob_parts_match(path_parts, pattern_path.parts):
            return True
    return False


def _load_plan(plan_path: Path) -> tuple[Path, str, dict[str, Any]]:
    try:
        sealed_plan = plan_path.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError) as error:
        raise RebuildPlanError(f"release plan does not exist: {plan_path}") from error
    if not sealed_plan.is_file():
        raise RebuildPlanError("release plan is not a regular file")
    try:
        payload = _stable_file_bytes(sealed_plan)
        decoded = payload.decode("utf-8")
        plan = json.loads(decoded, object_pairs_hook=strict._unique_object)
    except UnicodeError as error:
        raise RebuildPlanError(f"release plan is not valid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise RebuildPlanError(
            "invalid JSON at line "
            f"{error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    except strict.PlanError as error:
        raise RebuildPlanError(str(error)) from error
    if not isinstance(plan, dict):
        raise RebuildPlanError("release plan root must be a JSON object")
    return sealed_plan, _sha256_bytes(payload), plan


def _safe_artifact_path(
    base: Path,
    plan_path: Path,
    raw_path: object,
    label: str,
) -> tuple[Path, bool]:
    """Resolve an artifact without requiring it to exist.

    Missing active artifacts are stale, not malformed.  Traversal and symlink
    escapes remain invalid even when their target does not exist.
    """

    if not isinstance(raw_path, str) or not raw_path:
        raise RebuildPlanError(f"{label} must be a non-empty relative path string")
    candidate = Path(raw_path)
    if (
        candidate.is_absolute()
        or candidate == Path(".")
        or any(part == ".." for part in candidate.parts)
    ):
        raise RebuildPlanError(f"{label} must be a safe relative path: {raw_path!r}")
    try:
        resolved = (base / candidate).resolve(strict=False)
        resolved.relative_to(base)
    except (OSError, RuntimeError, ValueError) as error:
        raise RebuildPlanError(
            f"{label} escapes the plan directory: {raw_path!r}"
        ) from error
    if strict._path_aliases(resolved, plan_path):
        raise RebuildPlanError(f"{label} aliases the release plan")
    exists = resolved.exists()
    if exists and not resolved.is_file():
        raise RebuildPlanError(f"{label} is not a regular file: {raw_path!r}")
    return resolved, exists


def _validate_pointer_contract(contract: object, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(contract, dict):
        return [f"{label} must be a JSON object"]
    errors.extend(
        strict._shape_errors(
            contract, strict.HASH_FIELDS, {"mode", "sha256"}, label
        )
    )
    mode = contract.get("mode")
    expected = contract.get("sha256")
    if not isinstance(expected, str) or not strict.SHA256_RE.fullmatch(expected):
        errors.append(f"{label}.sha256 must be 64 lowercase hexadecimal characters")
    if mode == "full":
        if "pointers" in contract:
            errors.append(f"{label}.pointers is forbidden in full mode")
        return errors
    if mode != "canonical-subset":
        errors.append(f"{label}.mode must be 'full' or 'canonical-subset'")
        return errors
    pointers = contract.get("pointers")
    if not isinstance(pointers, list) or not pointers or not all(
        isinstance(pointer, str) for pointer in pointers
    ):
        errors.append(f"{label}.pointers must be a non-empty string array")
        return errors
    if len(set(pointers)) != len(pointers):
        errors.append(f"{label}.pointers contains duplicates")
        return errors
    try:
        tokenized = [strict._pointer_tokens(pointer) for pointer in pointers]
    except strict.PlanError as error:
        errors.append(f"{label}: {error}")
        return errors
    for left_index, left in enumerate(tokenized):
        for right in tokenized[left_index + 1 :]:
            shorter, longer = sorted((left, right), key=len)
            if len(shorter) < len(longer) and longer[: len(shorter)] == shorter:
                errors.append(
                    f"{label}.pointers must not contain ancestor/descendant selections"
                )
                return errors
    return errors


def _closure(roots: Iterable[str], graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    pending = list(sorted(roots, reverse=True))
    while pending:
        artifact_id = pending.pop()
        if artifact_id in reached:
            continue
        reached.add(artifact_id)
        pending.extend(sorted(graph.get(artifact_id, set()), reverse=True))
    return reached


def _stale_causes(
    artifact_id: str,
    direct: set[str],
    graph: dict[str, set[str]],
    memo: dict[str, set[str]],
) -> set[str]:
    if artifact_id in memo:
        return memo[artifact_id]
    if artifact_id in direct:
        memo[artifact_id] = {artifact_id}
        return memo[artifact_id]
    causes: set[str] = set()
    for dependency in sorted(graph.get(artifact_id, set())):
        causes.update(_stale_causes(dependency, direct, graph, memo))
    memo[artifact_id] = causes
    return causes


def _dependency_first_order(
    stale: set[str], graph: dict[str, set[str]]
) -> list[str]:
    """Topologically order a stale closure with dependencies before owners."""

    reverse: dict[str, set[str]] = {artifact_id: set() for artifact_id in stale}
    remaining_dependencies: dict[str, int] = {}
    for owner in stale:
        dependencies = graph.get(owner, set()) & stale
        remaining_dependencies[owner] = len(dependencies)
        for dependency in dependencies:
            reverse[dependency].add(owner)
    ready = [
        artifact_id
        for artifact_id, count in remaining_dependencies.items()
        if count == 0
    ]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        artifact_id = heapq.heappop(ready)
        order.append(artifact_id)
        for owner in sorted(reverse[artifact_id]):
            remaining_dependencies[owner] -= 1
            if remaining_dependencies[owner] == 0:
                heapq.heappush(ready, owner)
    if len(order) != len(stale):
        raise RebuildPlanError("stale dependency closure contains a cycle")
    return order


def _invalid_report(
    plan_name: str,
    plan_sha256: str | None,
    errors: Iterable[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "invalid",
        "ok": False,
        "plan": plan_name,
        "plan_sha256": plan_sha256,
        "active_count": 0,
        "skipped_count": 0,
        "targets": [],
        "direct_stale": [],
        "transitive_stale": [],
        "unaffected": [],
        "skipped": [],
        "rebuild_order": [],
        "errors": sorted(set(errors)),
    }


def analyse_plan(plan_path: Path) -> dict[str, Any]:
    """Return a deterministic rebuild report without modifying any artifact."""

    sealed_plan, plan_digest, plan = _load_plan(plan_path)
    base = sealed_plan.parent.resolve(strict=True)
    errors: list[str] = []
    errors.extend(
        strict._shape_errors(plan, strict.ROOT_FIELDS, strict.ROOT_REQUIRED, "release plan")
    )
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1:
        errors.append("schema_version must equal 1")

    discovered = strict._discover(
        base, sealed_plan, plan.get("discovery"), errors
    )
    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        errors.append("artifacts must be a non-empty array")
        raw_artifacts = []

    entries: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    path_exists: dict[str, bool] = {}
    classified_existing: dict[str, str] = {}
    classified_files: list[tuple[Path, str]] = []
    active: set[str] = set()
    skipped: set[str] = set()
    singleton_roles: dict[str, str] = {}

    for index, artifact in enumerate(raw_artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(
            strict._shape_errors(
                artifact, strict.ARTIFACT_FIELDS, strict.ARTIFACT_REQUIRED, label
            )
        )
        artifact_id = artifact.get("id")
        if not isinstance(artifact_id, str) or not strict.KEBAB_RE.fullmatch(artifact_id):
            errors.append(f"{label}.id must be kebab-cased")
            continue
        if artifact_id in entries:
            errors.append(f"{label}.id is duplicated: {artifact_id!r}")
            continue
        entries[artifact_id] = artifact

        artifact_type = artifact.get("type")
        if not isinstance(artifact_type, str) or not strict.KEBAB_RE.fullmatch(
            artifact_type
        ):
            errors.append(f"{label}.type must be kebab-cased")
        expected_hash = artifact.get("sha256")
        if not isinstance(expected_hash, str) or not strict.SHA256_RE.fullmatch(
            expected_hash
        ):
            errors.append(f"{label}.sha256 must be 64 lowercase hexadecimal characters")

        try:
            resolved, exists = _safe_artifact_path(
                base, sealed_plan, artifact.get("path"), f"{label}.path"
            )
            paths[artifact_id] = resolved
            path_exists[artifact_id] = exists
            key = strict._path_key(resolved)
            duplicate = next(
                (
                    previous_id
                    for previous_path, previous_id in classified_files
                    if strict._path_aliases(resolved, previous_path)
                ),
                None,
            )
            if duplicate is not None:
                errors.append(f"{label}.path aliases artifact {duplicate!r}")
            elif key in classified_existing:
                errors.append(
                    f"{label}.path aliases artifact {classified_existing[key]!r}"
                )
            else:
                classified_files.append((resolved, artifact_id))
                if exists:
                    classified_existing[key] = artifact_id
        except RebuildPlanError as error:
            errors.append(str(error))

        lifecycle = artifact.get("lifecycle")
        validation = artifact.get("validation")
        classification = artifact.get("classification")
        dependencies = artifact.get("depends_on")
        singleton_role = artifact.get("singleton_role")
        if lifecycle not in strict.LIFECYCLES:
            errors.append(f"{label}.lifecycle must be active, historical, or superseded")
        if validation not in strict.VALIDATIONS:
            errors.append(f"{label}.validation must be pass, provisional, or fail")
        if not isinstance(dependencies, list):
            errors.append(f"{label}.depends_on must be an array")
        if singleton_role is not None and (
            not isinstance(singleton_role, str)
            or not strict.KEBAB_RE.fullmatch(singleton_role)
        ):
            errors.append(f"{label}.singleton_role must be kebab-cased")

        if classification == "include":
            if lifecycle != "active" or validation != "pass":
                errors.append(
                    f"{label}: included evidence must be lifecycle active and validation pass"
                )
            else:
                active.add(artifact_id)
                if (
                    artifact_id in paths
                    and not path_exists.get(artifact_id, False)
                    and not _declared_path_is_discoverable(
                        artifact.get("path"), plan.get("discovery")
                    )
                ):
                    errors.append(
                        f"{label}.path is missing and outside discovery.globs: "
                        f"{artifact.get('path')!r}"
                    )
            if "exclusion_reason" in artifact:
                errors.append(f"{label}.exclusion_reason is forbidden when included")
            if "superseded_by" in artifact:
                errors.append(f"{label}.superseded_by is forbidden when included")
            if (
                lifecycle == "active"
                and validation == "pass"
                and isinstance(singleton_role, str)
                and strict.KEBAB_RE.fullmatch(singleton_role)
            ):
                previous = singleton_roles.get(singleton_role)
                if previous is not None:
                    errors.append(
                        f"{label}.singleton_role {singleton_role!r} duplicates "
                        f"active included artifact {previous!r}"
                    )
                else:
                    singleton_roles[singleton_role] = artifact_id
        elif classification == "exclude":
            skipped.add(artifact_id)
            if lifecycle not in {"historical", "superseded"}:
                errors.append(
                    f"{label}: excluded evidence must be historical or superseded"
                )
            reason = artifact.get("exclusion_reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{label}.exclusion_reason is required when excluded")
            if lifecycle == "superseded" and not isinstance(
                artifact.get("superseded_by"), str
            ):
                errors.append(f"{label}.superseded_by is required when superseded")
        else:
            errors.append(f"{label}.classification must be include or exclude")
        if "superseded_by" in artifact and lifecycle != "superseded":
            errors.append(
                f"{label}.superseded_by is only allowed when lifecycle is superseded"
            )

    discovered_keys = set(discovered)
    classified_keys = set(classified_existing)
    for key in sorted(discovered_keys - classified_keys):
        errors.append(
            "discovered artifact is unclassified: "
            f"{discovered[key].relative_to(base)}"
        )
    for key in sorted(classified_keys - discovered_keys):
        artifact_id = classified_existing[key]
        errors.append(
            f"classified artifact {artifact_id!r} is outside discovery.globs: "
            f"{entries[artifact_id].get('path')}"
        )

    target_hashes: dict[str, str | None] = {}
    direct_reasons: dict[str, str] = {}
    for artifact_id in sorted(active):
        path = paths.get(artifact_id)
        if path is None:
            continue
        if not path_exists.get(artifact_id, False):
            target_hashes[artifact_id] = None
            direct_reasons[artifact_id] = "missing"
            continue
        try:
            actual_hash = _sha256_file(path)
        except (OSError, RebuildPlanError):
            target_hashes[artifact_id] = None
            direct_reasons[artifact_id] = "hash-error"
            continue
        target_hashes[artifact_id] = actual_hash
        if actual_hash != entries[artifact_id].get("sha256"):
            direct_reasons[artifact_id] = "hash-mismatch"

    dependency_graph: dict[str, set[str]] = {
        artifact_id: set() for artifact_id in entries
    }
    supersession_graph: dict[str, set[str]] = {
        artifact_id: set() for artifact_id in entries
    }
    for artifact_id, artifact in sorted(entries.items()):
        superseded_by = artifact.get("superseded_by")
        if superseded_by is not None:
            if not isinstance(superseded_by, str) or not superseded_by:
                errors.append(
                    f"artifact {artifact_id!r}.superseded_by must be a non-empty id"
                )
            elif superseded_by not in entries:
                errors.append(
                    f"artifact {artifact_id!r}.superseded_by targets missing "
                    f"evidence {superseded_by!r}"
                )
            else:
                supersession_graph[artifact_id].add(superseded_by)
                if superseded_by not in active:
                    errors.append(
                        f"artifact {artifact_id!r}.superseded_by must target active "
                        f"included evidence, got {superseded_by!r}"
                    )

        dependencies = artifact.get("depends_on")
        if not isinstance(dependencies, list):
            continue
        seen_edges: set[tuple[str, str]] = set()
        for index, dependency in enumerate(dependencies):
            label = f"artifact {artifact_id!r} depends_on[{index}]"
            if not isinstance(dependency, dict):
                errors.append(f"{label} must be a JSON object")
                continue
            errors.extend(
                strict._shape_errors(
                    dependency,
                    strict.DEPENDENCY_FIELDS,
                    strict.DEPENDENCY_FIELDS,
                    label,
                )
            )
            target_id = dependency.get("target")
            relation = dependency.get("relation")
            if not isinstance(target_id, str) or not target_id:
                errors.append(f"{label}.target must be a non-empty artifact id")
                continue
            if not isinstance(relation, str) or not strict.KEBAB_RE.fullmatch(relation):
                errors.append(f"{label}.relation must be kebab-cased")
                continue
            edge = (target_id, relation)
            if edge in seen_edges:
                errors.append(f"{label} duplicates target/relation {edge!r}")
            seen_edges.add(edge)
            if target_id not in entries:
                errors.append(f"{label} targets missing evidence {target_id!r}")
                continue
            dependency_graph[artifact_id].add(target_id)
            if artifact_id in active and target_id not in active:
                errors.append(
                    f"{label}: active evidence dependencies must target active included evidence"
                )

            hash_label = f"{label}.hash"
            contract = dependency.get("hash")
            errors.extend(_validate_pointer_contract(contract, hash_label))
            if not isinstance(contract, dict):
                continue
            if contract.get("mode") == "full":
                declared_target_hash = entries[target_id].get("sha256")
                if contract.get("sha256") != declared_target_hash:
                    errors.append(
                        f"{hash_label} does not match target {target_id!r} "
                        "declared sha256"
                    )
            elif (
                contract.get("mode") == "canonical-subset"
                and artifact_id in active
                and target_id in active
                and target_id not in direct_reasons
                and isinstance(paths.get(target_id), Path)
                and isinstance(contract.get("pointers"), list)
            ):
                try:
                    actual_subset = strict._canonical_subset_hash(
                        paths[target_id], contract["pointers"]
                    )
                except (OSError, UnicodeError, strict.PlanError) as error:
                    errors.append(f"{hash_label} cannot be evaluated: {error}")
                else:
                    if actual_subset != contract.get("sha256"):
                        errors.append(
                            f"{hash_label} mismatch: expected "
                            f"{contract.get('sha256')}, got {actual_subset}"
                        )

    dependency_cycle = strict._find_cycle(dependency_graph)
    if dependency_cycle is not None:
        errors.append(
            f"dependency graph contains a cycle: {' -> '.join(dependency_cycle)}"
        )
    supersession_cycle = strict._find_cycle(supersession_graph)
    if supersession_cycle is not None:
        errors.append(
            f"supersession graph contains a cycle: {' -> '.join(supersession_cycle)}"
        )

    raw_roots = plan.get("release_roots")
    roots: list[str] = []
    reached: set[str] = set()
    if not isinstance(raw_roots, list) or not raw_roots or not all(
        isinstance(root, str) and root for root in raw_roots
    ):
        errors.append("release_roots must be a non-empty array of artifact ids")
    else:
        roots = raw_roots
        if len(set(roots)) != len(roots):
            errors.append("release_roots contains duplicates")
        for root in roots:
            if root not in active:
                errors.append(
                    f"release root {root!r} must target active included evidence"
                )
        final_output_roots = [
            root
            for root in roots
            if entries.get(root, {}).get("singleton_role") == "final-output"
        ]
        if len(final_output_roots) > 1:
            errors.append(
                "release_roots may contain at most one artifact with "
                "singleton_role 'final-output': " + ", ".join(final_output_roots)
            )
        reached = _closure(roots, dependency_graph)
        missing_from_closure = sorted(active - reached)
        extra_in_closure = sorted(reached - active)
        if missing_from_closure:
            errors.append(
                "active included artifact(s) are outside release-root closure: "
                + ", ".join(missing_from_closure)
            )
        if extra_in_closure:
            errors.append(
                "release-root closure contains non-active/non-included artifact(s): "
                + ", ".join(extra_in_closure)
            )

    active_toc_evidence = sorted(
        artifact_id
        for artifact_id in active
        if entries.get(artifact_id, {}).get("type") in strict.EVIDENCE_ID_TYPES.values()
    )
    if active_toc_evidence and "reconciliation_scope" not in plan:
        errors.append(
            "reconciliation_scope is required when active TOC reconciliation "
            "evidence is present: " + ", ".join(active_toc_evidence)
        )
    if "reconciliation_scope" in plan:
        strict._validate_scope(
            plan["reconciliation_scope"],
            entries=entries,
            active_included=active,
            release_closure=reached,
            errors=errors,
        )

    if errors:
        return _invalid_report(sealed_plan.name, plan_digest, errors)

    direct = set(direct_reasons)
    reverse: dict[str, set[str]] = {artifact_id: set() for artifact_id in active}
    for owner in active:
        for dependency in dependency_graph[owner]:
            reverse[dependency].add(owner)
    stale = set(direct)
    pending = list(sorted(direct, reverse=True))
    while pending:
        artifact_id = pending.pop()
        for dependent in sorted(reverse[artifact_id], reverse=True):
            if dependent in stale:
                continue
            stale.add(dependent)
            pending.append(dependent)
    transitive = stale - direct
    unaffected = active - stale
    rebuild_order = _dependency_first_order(stale, dependency_graph)

    cause_memo: dict[str, set[str]] = {}
    targets: list[dict[str, Any]] = []
    for artifact_id in sorted(active):
        expected = entries[artifact_id]["sha256"]
        actual = target_hashes[artifact_id]
        target: dict[str, Any] = {
            "id": artifact_id,
            "path": entries[artifact_id]["path"],
            "expected_sha256": expected,
            "actual_sha256": actual,
        }
        if artifact_id in direct:
            target["state"] = "direct-stale"
            target["reason"] = direct_reasons[artifact_id]
            target["caused_by"] = [artifact_id]
        elif artifact_id in transitive:
            target["state"] = "transitive-stale"
            target["stale_dependencies"] = sorted(
                dependency_graph[artifact_id] & stale
            )
            target["caused_by"] = sorted(
                _stale_causes(
                    artifact_id, direct, dependency_graph, cause_memo
                )
            )
        else:
            target["state"] = "unaffected"
        targets.append(target)

    skipped_records = [
        {
            "id": artifact_id,
            "path": entries[artifact_id]["path"],
            "classification": entries[artifact_id]["classification"],
            "lifecycle": entries[artifact_id]["lifecycle"],
            "validation": entries[artifact_id]["validation"],
            "reason": "not-active-included",
        }
        for artifact_id in sorted(skipped)
    ]
    status = "stale" if stale else "clean"
    return {
        "schema_version": 1,
        "status": status,
        "ok": status == "clean",
        "plan": sealed_plan.name,
        "plan_sha256": plan_digest,
        "active_count": len(active),
        "skipped_count": len(skipped),
        "targets": targets,
        "direct_stale": sorted(direct),
        "transitive_stale": sorted(transitive),
        "unaffected": sorted(unaffected),
        "skipped": skipped_records,
        "rebuild_order": rebuild_order,
        "errors": [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Hash active release evidence, propagate stale dependencies, and emit "
            "a dependency-first rebuild order without modifying any file."
        ),
        epilog=(
            "Exit 0 means clean, exit 1 means stale, and exit 2 means the plan is "
            "invalid or unsafe. Historical/excluded artifacts are listed as skipped."
        ),
    )
    parser.add_argument("plan", type=Path, help="strict v1 release-plan.json")
    parser.add_argument(
        "--json-report",
        "--json",
        dest="json_report",
        nargs="?",
        const="-",
        metavar="PATH",
        help=(
            "emit one deterministic JSON report; omit PATH to write stdout, "
            "or provide PATH to save it"
        ),
    )
    return parser


def _print_text_report(report: dict[str, Any]) -> None:
    plan_sha = report.get("plan_sha256") or "unavailable"
    print(f"PLAN {report['plan']} sha256={plan_sha}")
    for target in report.get("targets", []):
        actual = target.get("actual_sha256") or "missing"
        print(
            f"TARGET {target['id']} state={target['state']} "
            f"expected={target['expected_sha256']} actual={actual} "
            f"path={target['path']}"
        )
    for skipped in report.get("skipped", []):
        print(
            f"SKIPPED {skipped['id']} lifecycle={skipped['lifecycle']} "
            f"path={skipped['path']}"
        )
    if report.get("rebuild_order"):
        print("REBUILD_ORDER " + " ".join(report["rebuild_order"]))
    for error in report.get("errors", []):
        print(f"ERROR {report['plan']}: {error}", file=sys.stderr)
    print(
        "SUMMARY "
        f"status={report['status']} "
        f"direct={len(report.get('direct_stale', []))} "
        f"transitive={len(report.get('transitive_stale', []))} "
        f"unaffected={len(report.get('unaffected', []))} "
        f"skipped={report.get('skipped_count', 0)}"
    )


def _protected_report_paths(
    plan_path: Path, report: dict[str, Any]
) -> list[tuple[str, Path]]:
    """Return input/tool paths that a JSON report must never overwrite."""

    protected = [
        ("the release plan", plan_path),
        ("the rebuild planner", TOOL_PATH),
        ("the strict release validator", STRICT_VALIDATOR_PATH),
    ]
    try:
        sealed_plan, current_digest, plan = _load_plan(plan_path)
    except (OSError, RebuildPlanError):
        return protected
    report_digest = report.get("plan_sha256")
    if isinstance(report_digest, str) and current_digest != report_digest:
        raise RebuildPlanError("release plan changed before JSON report write")
    base = sealed_plan.parent
    artifacts = plan.get("artifacts")
    if not isinstance(artifacts, list):
        return protected
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw_path = artifact.get("path")
        artifact_id = artifact.get("id")
        candidate = Path(raw_path) if isinstance(raw_path, str) else None
        if (
            candidate is not None
            and raw_path
            and not candidate.is_absolute()
            and candidate != Path(".")
            and not any(part == ".." for part in candidate.parts)
        ):
            protected.append(
                (f"classified artifact {artifact_id!r}", base / candidate)
            )
    return protected


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyse_plan(args.plan)
    except (OSError, RebuildPlanError) as error:
        plan_digest: str | None = None
        try:
            candidate = args.plan.resolve(strict=True)
            if candidate.is_file():
                plan_digest = _sha256_file(candidate)
        except (OSError, RebuildPlanError, RuntimeError):
            pass
        report = _invalid_report(args.plan.name, plan_digest, [str(error)])
    if args.json_report:
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n"
        if args.json_report == "-":
            print(rendered, end="")
        else:
            output = Path(args.json_report)
            try:
                for label, protected in _protected_report_paths(args.plan, report):
                    if paths_refer_to_same_file(output, protected):
                        raise RebuildPlanError(
                            f"--json-report must not alias {label}"
                        )
                output.write_text(rendered, encoding="utf-8", newline="")
            except (OSError, RebuildPlanError) as error:
                print(f"ERROR could not write JSON report: {error}", file=sys.stderr)
                return EXIT_INVALID
    else:
        _print_text_report(report)
    if report["status"] == "clean":
        return EXIT_CLEAN
    if report["status"] == "stale":
        return EXIT_STALE
    return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
