"""Canonical locations inside the jyut-ocr-proofread skill tree."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = PACKAGE_DIR.parent
SKILL_ROOT = SCRIPTS_DIR.parent
REFERENCES_DIR = SKILL_ROOT / "references"
RUNTIME_DIR = SKILL_ROOT / "runtime"


def script_path(name: str) -> Path:
    """Return the path to a CLI entrypoint or asset inside ``scripts/``."""
    return SCRIPTS_DIR / name


def reference_path(name: str) -> Path:
    """Return the path to a reference file or table inside ``references/``."""
    return REFERENCES_DIR / name
