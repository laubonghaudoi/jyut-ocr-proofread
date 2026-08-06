#!/usr/bin/env python3
"""Validate a closed, typed OCR release evidence plan.

Strict v1 schema (unknown fields and duplicate JSON keys are rejected)::

    {
      "schema_version": 1,
      "discovery": {"globs": ["evidence/*.json", "*.md"]},
      "release_roots": ["final"],
      "artifacts": [
        {
          "id": "review",
          "type": "review-ledger",
          "path": "evidence/review.json",
          "classification": "include",
          "lifecycle": "active",
          "validation": "pass",
          "sha256": "<full file sha256>",
          "depends_on": []
        },
        {
          "id": "final",
          "type": "final-output",
          "path": "final.md",
          "classification": "include",
          "lifecycle": "active",
          "validation": "pass",
          "singleton_role": "final-output",
          "sha256": "<full file sha256>",
          "depends_on": [
            {
              "target": "review",
              "relation": "verified-by",
              "hash": {"mode": "full", "sha256": "<review file sha256>"}
            }
          ]
        },
        {
          "id": "scratch",
          "type": "review-ledger",
          "path": "evidence/scratch.json",
          "classification": "exclude",
          "lifecycle": "historical",
          "validation": "provisional",
          "sha256": "<full file sha256>",
          "depends_on": [],
          "exclusion_reason": "working copy; not release evidence"
        }
      ],
      "reconciliation_scope": {
        "id": "volume-toc",
        "unit": "toc-item",
        "source_count": 20,
        "included_count": 12,
        "evidence_ids": {
          "toc_item_ledger": "toc-items",
          "body_heading_inventory": "body-headings",
          "reconciliation_ledger": "toc-body-reconciliation",
          "variant_challenge_ledger": "variant-challenges",
          "invariants_qa": "invariants"
        },
        "partitions": [
          {"id": "current-volume", "start": 1, "end": 12,
           "disposition": "in-scope", "reconciled_count": 12},
          {"id": "other-volume", "start": 13, "end": 20,
           "disposition": "out-of-scope", "reason": "separate delivery"}
        ]
      }
    }

``reconciliation_scope`` is optional for a book without a TOC reconciliation.
When present, its ``evidence_ids`` map must name reachable active evidence of
the exact ledger/inventory types declared by the schema.  Scope validation is
deliberately structural: it verifies typed references, partition coverage, and
count arithmetic, but does not claim to semantically validate ledger rows.
Every regular file matched by ``discovery.globs`` must be classified exactly
once.  The plan itself is the sole bootstrap exemption.  All paths stay beneath
the plan directory, including after symlink resolution, and every artifact is
sealed by its full-file SHA-256.

Included artifacts must be ``active`` and validation ``pass``.  Excluded
artifacts must be ``historical`` or ``superseded`` and carry a non-empty
``exclusion_reason``.  A ``superseded_by`` edge must point to active included
evidence and is only valid on lifecycle ``superseded``.  Active dependencies
likewise target active included evidence.  Optional ``singleton_role`` values
must be unique across active included artifacts.

``release_roots`` must name active included artifacts.  Their transitive
``depends_on`` closure, including the roots, must equal the complete active
included set: an orphan active artifact fails the release.  Dependency and
supersession graphs are checked as separate DAGs.

Dependency hashes use ``full`` or ``canonical-subset`` mode.  A canonical
subset lists non-overlapping RFC 6901 JSON pointers.  Selected values become a
pointer-sorted array of ``{"pointer": POINTER, "value": VALUE}``, serialized
as UTF-8 JSON with ``ensure_ascii=false``, sorted object keys, separators
``,``/``:`` and no trailing newline, then SHA-256 hashed.  This seals stable
decision fields without introducing reciprocal full-file hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "discovery",
        "release_roots",
        "artifacts",
        "reconciliation_scope",
    }
)
ROOT_REQUIRED = frozenset(
    {"schema_version", "discovery", "release_roots", "artifacts"}
)
DISCOVERY_FIELDS = frozenset({"globs"})
ARTIFACT_FIELDS = frozenset(
    {
        "id",
        "type",
        "path",
        "classification",
        "lifecycle",
        "validation",
        "sha256",
        "depends_on",
        "exclusion_reason",
        "superseded_by",
        "singleton_role",
    }
)
ARTIFACT_REQUIRED = frozenset(
    {
        "id",
        "type",
        "path",
        "classification",
        "lifecycle",
        "validation",
        "sha256",
        "depends_on",
    }
)
DEPENDENCY_FIELDS = frozenset({"target", "relation", "hash"})
HASH_FIELDS = frozenset({"mode", "sha256", "pointers"})
SCOPE_FIELDS = frozenset(
    {
        "id",
        "unit",
        "source_count",
        "included_count",
        "evidence_ids",
        "partitions",
    }
)
EVIDENCE_ID_FIELDS = frozenset(
    {
        "toc_item_ledger",
        "body_heading_inventory",
        "reconciliation_ledger",
        "variant_challenge_ledger",
        "invariants_qa",
    }
)
EVIDENCE_ID_TYPES = {
    "toc_item_ledger": "toc-item-ledger",
    "body_heading_inventory": "body-heading-inventory",
    "reconciliation_ledger": "toc-body-reconciliation-ledger",
    "variant_challenge_ledger": "variant-challenge-ledger",
    "invariants_qa": "invariants-qa",
}
PARTITION_FIELDS = frozenset(
    {"id", "start", "end", "disposition", "reconciled_count", "reason"}
)
LIFECYCLES = frozenset({"active", "historical", "superseded"})
VALIDATIONS = frozenset({"pass", "provisional", "fail"})
DISPOSITIONS = frozenset({"in-scope", "out-of-scope"})


class PlanError(ValueError):
    """The release plan cannot be parsed safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as error:
        raise PlanError(
            f"invalid JSON at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(path: Path) -> str:
    """Use filesystem-native case semantics without collapsing POSIX A/a."""

    return os.path.normcase(str(path.resolve(strict=False)))


def _path_aliases(left: Path, right: Path) -> bool:
    if _path_key(left) == _path_key(right):
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _artifact_path(base: Path, raw_path: object, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise PlanError(f"{label} must be a non-empty relative path string")
    candidate = Path(raw_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise PlanError(f"{label} must be a safe relative path: {raw_path!r}")
    try:
        resolved = (base / candidate).resolve(strict=True)
    except FileNotFoundError as error:
        raise PlanError(f"{label} does not exist: {raw_path!r}") from error
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise PlanError(f"{label} escapes the plan directory: {raw_path!r}") from error
    if not resolved.is_file():
        raise PlanError(f"{label} is not a regular file: {raw_path!r}")
    return resolved


def _shape_errors(
    value: dict[str, Any], allowed: frozenset[str], required: Iterable[str], label: str
) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(value) - allowed)
    missing = sorted(set(required) - set(value))
    if unknown:
        errors.append(f"{label} has unknown field(s): {', '.join(unknown)}")
    if missing:
        errors.append(f"{label} is missing field(s): {', '.join(missing)}")
    return errors


def _discover(
    base: Path, plan_path: Path, discovery: object, errors: list[str]
) -> dict[str, Path]:
    if not isinstance(discovery, dict):
        errors.append("discovery must be a JSON object")
        return {}
    errors.extend(
        _shape_errors(discovery, DISCOVERY_FIELDS, DISCOVERY_FIELDS, "discovery")
    )
    globs = discovery.get("globs")
    if not isinstance(globs, list) or not globs:
        errors.append("discovery.globs must be a non-empty array")
        return {}
    discovered: dict[str, Path] = {}
    for index, pattern in enumerate(globs):
        label = f"discovery.globs[{index}]"
        if not isinstance(pattern, str) or not pattern:
            errors.append(f"{label} must be a non-empty string")
            continue
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or any(part == ".." for part in pattern_path.parts):
            errors.append(f"{label} must be a safe relative glob: {pattern!r}")
            continue
        try:
            matches = sorted(base.glob(pattern), key=lambda item: str(item))
        except (OSError, ValueError) as error:
            errors.append(f"{label} is invalid: {error}")
            continue
        for match in matches:
            try:
                resolved = match.resolve(strict=True)
                resolved.relative_to(base)
            except (FileNotFoundError, OSError, ValueError):
                errors.append(f"{label} matched outside the plan directory: {match}")
                continue
            if not resolved.is_file() or _path_aliases(resolved, plan_path):
                continue
            key = _path_key(resolved)
            if key in discovered:
                # Overlapping globs may legitimately select the exact same path.
                continue
            duplicate = next(
                (
                    previous
                    for previous in discovered.values()
                    if _path_aliases(resolved, previous)
                ),
                None,
            )
            if duplicate is not None:
                errors.append(
                    f"{label} matched duplicate/hard-link aliases: "
                    f"{duplicate} and {resolved}"
                )
                continue
            discovered[key] = resolved
    return discovered


def _decode_pointer_token(token: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            output.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in "01":
            raise PlanError(f"invalid RFC 6901 escape in pointer token {token!r}")
        output.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(output)


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if not pointer or not pointer.startswith("/"):
        raise PlanError("canonical-subset pointers must be non-empty and start with '/'")
    return tuple(_decode_pointer_token(token) for token in pointer[1:].split("/"))


def _pointer_value(document: Any, pointer: str) -> Any:
    current = document
    for token in _pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                raise PlanError(f"JSON pointer {pointer!r} does not exist")
            current = current[token]
        elif isinstance(current, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise PlanError(
                    f"JSON pointer {pointer!r} has invalid array index {token!r}"
                )
            position = int(token)
            if position >= len(current):
                raise PlanError(f"JSON pointer {pointer!r} is outside its array")
            current = current[position]
        else:
            raise PlanError(f"JSON pointer {pointer!r} traverses a scalar value")
    return current


def _canonical_subset_hash(path: Path, pointers: list[str]) -> str:
    document = _load_json(path)
    selected = [
        {"pointer": pointer, "value": _pointer_value(document, pointer)}
        for pointer in sorted(pointers)
    ]
    payload = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_dependency_hash(
    owner: str,
    index: int,
    contract: object,
    target_path: Path,
    target_full_hash: str,
    errors: list[str],
) -> None:
    label = f"artifact {owner!r} depends_on[{index}].hash"
    if not isinstance(contract, dict):
        errors.append(f"{label} must be a JSON object")
        return
    errors.extend(_shape_errors(contract, HASH_FIELDS, {"mode", "sha256"}, label))
    mode = contract.get("mode")
    expected = contract.get("sha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        errors.append(f"{label}.sha256 must be 64 lowercase hexadecimal characters")
        return
    if mode == "full":
        if "pointers" in contract:
            errors.append(f"{label}.pointers is forbidden in full mode")
        if expected != target_full_hash:
            errors.append(f"{label} mismatch: expected {expected}, got {target_full_hash}")
        return
    if mode != "canonical-subset":
        errors.append(f"{label}.mode must be 'full' or 'canonical-subset'")
        return
    pointers = contract.get("pointers")
    if not isinstance(pointers, list) or not pointers or not all(
        isinstance(pointer, str) for pointer in pointers
    ):
        errors.append(f"{label}.pointers must be a non-empty string array")
        return
    if len(set(pointers)) != len(pointers):
        errors.append(f"{label}.pointers contains duplicates")
        return
    try:
        tokenized = [_pointer_tokens(pointer) for pointer in pointers]
        for left_index, left in enumerate(tokenized):
            for right in tokenized[left_index + 1 :]:
                shorter, longer = sorted((left, right), key=len)
                if len(shorter) < len(longer) and longer[: len(shorter)] == shorter:
                    errors.append(
                        f"{label}.pointers must not contain "
                        "ancestor/descendant selections"
                    )
                    return
        actual = _canonical_subset_hash(target_path, pointers)
    except (OSError, UnicodeError, PlanError) as error:
        errors.append(f"{label} cannot be evaluated: {error}")
        return
    if actual != expected:
        errors.append(f"{label} mismatch: expected {expected}, got {actual}")


def _validate_scope(
    scope: object,
    *,
    entries: dict[str, dict[str, Any]],
    active_included: set[str],
    release_closure: set[str],
    errors: list[str],
) -> None:
    if not isinstance(scope, dict):
        errors.append("reconciliation_scope must be a JSON object")
        return
    errors.extend(
        _shape_errors(scope, SCOPE_FIELDS, SCOPE_FIELDS, "reconciliation_scope")
    )
    scope_id = scope.get("id")
    unit = scope.get("unit")
    source_count = scope.get("source_count")
    included_count = scope.get("included_count")
    evidence_ids = scope.get("evidence_ids")
    partitions = scope.get("partitions")
    if not isinstance(scope_id, str) or not KEBAB_RE.fullmatch(scope_id):
        errors.append("reconciliation_scope.id must be kebab-cased")
    if not isinstance(unit, str) or not KEBAB_RE.fullmatch(unit):
        errors.append("reconciliation_scope.unit must be kebab-cased")
    if (
        not isinstance(source_count, int)
        or isinstance(source_count, bool)
        or source_count < 1
    ):
        errors.append("reconciliation_scope.source_count must be a positive integer")
        return
    if (
        not isinstance(included_count, int)
        or isinstance(included_count, bool)
        or not 0 <= included_count <= source_count
    ):
        errors.append(
            "reconciliation_scope.included_count must be an integer from 0 to source_count"
        )
    if not isinstance(evidence_ids, dict):
        errors.append("reconciliation_scope.evidence_ids must be a JSON object")
    else:
        errors.extend(
            _shape_errors(
                evidence_ids,
                EVIDENCE_ID_FIELDS,
                EVIDENCE_ID_FIELDS,
                "reconciliation_scope.evidence_ids",
            )
        )
        for role in sorted(EVIDENCE_ID_FIELDS):
            artifact_id = evidence_ids.get(role)
            label = f"reconciliation_scope.evidence_ids.{role}"
            if not isinstance(artifact_id, str) or not artifact_id:
                errors.append(f"{label} must be a non-empty artifact id")
                continue
            artifact = entries.get(artifact_id)
            if artifact is None:
                errors.append(f"{label} targets missing evidence {artifact_id!r}")
                continue
            if artifact_id not in active_included:
                errors.append(
                    f"{label} must target active included evidence, got "
                    f"{artifact_id!r}"
                )
            if artifact_id not in release_closure:
                errors.append(
                    f"{label} must be reachable from release_roots, got "
                    f"{artifact_id!r}"
                )
            expected_type = EVIDENCE_ID_TYPES[role]
            if artifact.get("type") != expected_type:
                errors.append(
                    f"{label} must target artifact type {expected_type!r}, got "
                    f"{artifact.get('type')!r}"
                )
    if not isinstance(partitions, list) or not partitions:
        errors.append("reconciliation_scope.partitions must be a non-empty array")
        return
    next_start = 1
    counted_in_scope = 0
    partition_ids: set[str] = set()
    for index, partition in enumerate(partitions):
        label = f"reconciliation_scope.partitions[{index}]"
        if not isinstance(partition, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(
            _shape_errors(
                partition,
                PARTITION_FIELDS,
                {"id", "start", "end", "disposition"},
                label,
            )
        )
        partition_id = partition.get("id")
        start = partition.get("start")
        end = partition.get("end")
        disposition = partition.get("disposition")
        if not isinstance(partition_id, str) or not KEBAB_RE.fullmatch(partition_id):
            errors.append(f"{label}.id must be kebab-cased")
        elif partition_id in partition_ids:
            errors.append(f"{label}.id is duplicated: {partition_id!r}")
        else:
            partition_ids.add(partition_id)
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 1
            or end < start
        ):
            errors.append(f"{label} must have integer 1 <= start <= end")
            continue
        if start != next_start:
            errors.append(
                f"{label} starts at {start}; exact ordered coverage requires {next_start}"
            )
        next_start = end + 1
        span = end - start + 1
        if disposition not in DISPOSITIONS:
            errors.append(f"{label}.disposition must be in-scope or out-of-scope")
            continue
        if disposition == "in-scope":
            reconciled = partition.get("reconciled_count")
            if (
                not isinstance(reconciled, int)
                or isinstance(reconciled, bool)
                or reconciled != span
            ):
                errors.append(
                    f"{label}.reconciled_count must equal its {span}-item span"
                )
            counted_in_scope += span
        else:
            reason = partition.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{label}.reason is required when out-of-scope")
            if "reconciled_count" in partition and partition["reconciled_count"] != 0:
                errors.append(f"{label}.reconciled_count must be 0 when out-of-scope")
    if next_start != source_count + 1:
        errors.append(
            "reconciliation_scope.partitions do not end at source_count "
            f"{source_count} (next uncovered index is {next_start})"
        )
    if (
        isinstance(included_count, int)
        and not isinstance(included_count, bool)
        and counted_in_scope != included_count
    ):
        errors.append(
            f"reconciliation_scope.included_count is {included_count}, "
            f"but in-scope partitions cover {counted_in_scope}"
        )


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for target in sorted(graph.get(node, set())):
            if state.get(target, 0) == 0:
                cycle = visit(target)
                if cycle is not None:
                    return cycle
            elif state.get(target) == 1:
                start = stack.index(target)
                return [*stack[start:], target]
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle is not None:
                return cycle
    return None


def _closure(roots: list[str], graph: dict[str, set[str]]) -> set[str]:
    reached: set[str] = set()
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        pending.extend(sorted(graph.get(node, set()), reverse=True))
    return reached


def validate_plan(plan_path: Path) -> dict[str, object]:
    errors: list[str] = []
    plan_digest: str | None = None
    tool_identity = {
        "name": Path(__file__).name,
        "sha256": _sha256_file(Path(__file__).resolve(strict=True)),
    }
    try:
        sealed_plan = plan_path.resolve(strict=True)
        if not sealed_plan.is_file():
            raise PlanError("release plan is not a regular file")
        plan_digest = _sha256_file(sealed_plan)
        plan = _load_json(sealed_plan)
    except (FileNotFoundError, OSError, UnicodeError, PlanError) as error:
        return {
            "schema_version": 1,
            "ok": False,
            "plan": plan_path.name,
            "plan_sha256": plan_digest,
            "tool": tool_identity,
            "discovered_count": 0,
            "included_count": 0,
            "excluded_count": 0,
            "errors": [str(error)],
        }
    if not isinstance(plan, dict):
        errors.append("release plan root must be a JSON object")
        plan = {}
    errors.extend(_shape_errors(plan, ROOT_FIELDS, ROOT_REQUIRED, "release plan"))
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != 1:
        errors.append("schema_version must equal 1")

    base = sealed_plan.parent.resolve(strict=True)
    discovered = _discover(base, sealed_plan, plan.get("discovery"), errors)
    raw_artifacts = plan.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        errors.append("artifacts must be a non-empty array")
        raw_artifacts = []

    entries: dict[str, dict[str, Any]] = {}
    classified_paths: dict[str, str] = {}
    classified_files: list[tuple[Path, str]] = []
    included: set[str] = set()
    active_included: set[str] = set()
    excluded: set[str] = set()
    singleton_roles: dict[str, str] = {}
    for index, artifact in enumerate(raw_artifacts):
        label = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            errors.append(f"{label} must be a JSON object")
            continue
        errors.extend(
            _shape_errors(artifact, ARTIFACT_FIELDS, ARTIFACT_REQUIRED, label)
        )
        artifact_id = artifact.get("id")
        if not isinstance(artifact_id, str) or not KEBAB_RE.fullmatch(artifact_id):
            errors.append(f"{label}.id must be kebab-cased")
            continue
        if artifact_id in entries:
            errors.append(f"{label}.id is duplicated: {artifact_id!r}")
            continue
        entries[artifact_id] = artifact
        evidence_type = artifact.get("type")
        if not isinstance(evidence_type, str) or not KEBAB_RE.fullmatch(evidence_type):
            errors.append(f"{label}.type must be kebab-cased")
        try:
            path = _artifact_path(base, artifact.get("path"), f"{label}.path")
            artifact["_path"] = path
            key = _path_key(path)
            alias = next(
                (
                    previous_id
                    for previous_path, previous_id in classified_files
                    if _path_aliases(path, previous_path)
                ),
                None,
            )
            if alias is not None:
                errors.append(f"{label}.path aliases artifact {alias!r}")
            else:
                classified_paths[key] = artifact_id
                classified_files.append((path, artifact_id))
        except PlanError as error:
            errors.append(str(error))
            path = None
        expected_hash = artifact.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            errors.append(f"{label}.sha256 must be 64 lowercase hexadecimal characters")
        elif path is not None:
            try:
                actual_hash = _sha256_file(path)
                artifact["_sha256"] = actual_hash
                if actual_hash != expected_hash:
                    errors.append(
                        f"{label}.sha256 mismatch: expected {expected_hash}, got {actual_hash}"
                    )
            except OSError as error:
                errors.append(f"{label} cannot be hashed: {error}")

        lifecycle = artifact.get("lifecycle")
        validation = artifact.get("validation")
        classification = artifact.get("classification")
        dependencies = artifact.get("depends_on")
        singleton_role = artifact.get("singleton_role")
        if lifecycle not in LIFECYCLES:
            errors.append(f"{label}.lifecycle must be active, historical, or superseded")
        if validation not in VALIDATIONS:
            errors.append(f"{label}.validation must be pass, provisional, or fail")
        if not isinstance(dependencies, list):
            errors.append(f"{label}.depends_on must be an array")
        if singleton_role is not None and (
            not isinstance(singleton_role, str)
            or not KEBAB_RE.fullmatch(singleton_role)
        ):
            errors.append(f"{label}.singleton_role must be kebab-cased")
        if classification == "include":
            included.add(artifact_id)
            if lifecycle == "active" and validation == "pass":
                active_included.add(artifact_id)
            if lifecycle != "active" or validation != "pass":
                errors.append(
                    f"{label}: included evidence must be lifecycle active and validation pass"
                )
            if "exclusion_reason" in artifact:
                errors.append(f"{label}.exclusion_reason is forbidden when included")
            if "superseded_by" in artifact:
                errors.append(f"{label}.superseded_by is forbidden when included")
            if (
                lifecycle == "active"
                and validation == "pass"
                and isinstance(singleton_role, str)
                and KEBAB_RE.fullmatch(singleton_role)
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
            excluded.add(artifact_id)
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
    classified_keys = set(classified_paths)
    for key in sorted(discovered_keys - classified_keys):
        errors.append(
            f"discovered artifact is unclassified: {discovered[key].relative_to(base)}"
        )
    for key in sorted(classified_keys - discovered_keys):
        artifact_id = classified_paths[key]
        errors.append(
            f"classified artifact {artifact_id!r} is outside discovery.globs: "
            f"{entries[artifact_id].get('path')}"
        )

    dependency_graph: dict[str, set[str]] = {artifact_id: set() for artifact_id in entries}
    supersession_graph: dict[str, set[str]] = {artifact_id: set() for artifact_id in entries}
    for artifact_id, artifact in entries.items():
        superseded_by = artifact.get("superseded_by")
        if superseded_by is not None:
            if not isinstance(superseded_by, str) or not superseded_by:
                errors.append(
                    f"artifact {artifact_id!r}.superseded_by must be a non-empty id"
                )
            else:
                target = entries.get(superseded_by)
                if target is None:
                    errors.append(
                        f"artifact {artifact_id!r}.superseded_by targets missing "
                        f"evidence {superseded_by!r}"
                    )
                else:
                    supersession_graph[artifact_id].add(superseded_by)
                if target is not None and superseded_by not in active_included:
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
                _shape_errors(
                    dependency, DEPENDENCY_FIELDS, DEPENDENCY_FIELDS, label
                )
            )
            target_id = dependency.get("target")
            relation = dependency.get("relation")
            if not isinstance(target_id, str) or not target_id:
                errors.append(f"{label}.target must be a non-empty artifact id")
                continue
            if not isinstance(relation, str) or not KEBAB_RE.fullmatch(relation):
                errors.append(f"{label}.relation must be kebab-cased")
                continue
            edge = (target_id, relation)
            if edge in seen_edges:
                errors.append(f"{label} duplicates target/relation {edge!r}")
            seen_edges.add(edge)
            target = entries.get(target_id)
            if target is None:
                errors.append(f"{label} targets missing evidence {target_id!r}")
                continue
            dependency_graph[artifact_id].add(target_id)
            if artifact_id in active_included and target_id not in active_included:
                errors.append(
                    f"{label}: active evidence dependencies must target active included evidence"
                )
            target_path = target.get("_path")
            target_hash = target.get("_sha256")
            if isinstance(target_path, Path) and isinstance(target_hash, str):
                _validate_dependency_hash(
                    artifact_id,
                    index,
                    dependency.get("hash"),
                    target_path,
                    target_hash,
                    errors,
                )

    dependency_cycle = _find_cycle(dependency_graph)
    if dependency_cycle is not None:
        errors.append(
            f"dependency graph contains a cycle: {' -> '.join(dependency_cycle)}"
        )
    supersession_cycle = _find_cycle(supersession_graph)
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
            if root not in active_included:
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
    if roots:
        reached = _closure(roots, dependency_graph)
        missing = sorted(active_included - reached)
        extra = sorted(reached - active_included)
        if missing:
            errors.append(
                "active included artifact(s) are outside release-root closure: "
                + ", ".join(missing)
            )
        if extra:
            errors.append(
                "release-root closure contains non-active/non-included artifact(s): "
                + ", ".join(extra)
            )

    active_toc_evidence = sorted(
        artifact_id
        for artifact_id in active_included
        if entries.get(artifact_id, {}).get("type") in EVIDENCE_ID_TYPES.values()
    )
    if active_toc_evidence and "reconciliation_scope" not in plan:
        errors.append(
            "reconciliation_scope is required when active TOC reconciliation "
            "evidence is present: " + ", ".join(active_toc_evidence)
        )
    if "reconciliation_scope" in plan:
        _validate_scope(
            plan["reconciliation_scope"],
            entries=entries,
            active_included=active_included,
            release_closure=reached,
            errors=errors,
        )
    return {
        "schema_version": 1,
        "ok": not errors,
        "plan": plan_path.name,
        "plan_sha256": plan_digest,
        "tool": tool_identity,
        "discovered_count": len(discovered),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "release_root_count": len(roots),
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate exhaustive artifact classification, active release-root closure, "
            "full/subset hashes, separate dependency/supersession DAGs, and optional "
            "TOC reconciliation partitions in release-plan.json."
        ),
        epilog=(
            "The plan is read-only. Use --json for a stable machine-readable summary. "
            "See the module docstring for the strict v1 schema and canonical hash rules."
        ),
    )
    parser.add_argument("plan", type=Path, help="release-plan.json to validate")
    parser.add_argument("--json", action="store_true", help="emit one JSON report object")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_plan(args.plan)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        for error in report["errors"]:
            print(f"ERROR {args.plan}: {error}", file=sys.stderr)
        print(
            "SUMMARY "
            f"ok={'yes' if report['ok'] else 'no'} "
            f"errors={len(report['errors'])} "
            f"discovered={report['discovered_count']} "
            f"included={report['included_count']} "
            f"excluded={report['excluded_count']} "
            f"roots={report.get('release_root_count', 0)}"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
