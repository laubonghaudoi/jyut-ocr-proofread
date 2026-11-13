#!/usr/bin/env python3
"""Launch the shared jyut-ocr-bench review tool; keep review data in its project."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys


REQUIRED_REVIEW_API = 1


def resolve_tool_root(tool_root=None, *, anchor=None):
    """An explicit root never falls back; symlink installs follow their source."""
    configured = tool_root if tool_root is not None else os.environ.get("JYUT_OCR_BENCH_ROOT")
    if configured is not None:
        if not str(configured).strip():
            raise ValueError("The configured jyut-ocr-bench root is empty")
        return Path(configured).expanduser().resolve()
    source = Path(anchor or __file__).resolve()
    for parent in source.parents:
        candidate = parent / "jyut-ocr-bench"
        if candidate.exists() or candidate.is_symlink():
            return candidate.resolve()
    raise ValueError("jyut-ocr-bench was not found; use --tool-root PATH or JYUT_OCR_BENCH_ROOT")


def load_review(tool_root=None, *, anchor=None):
    """Load only the selected checkout's versioned review entrypoint."""
    root = resolve_tool_root(tool_root, anchor=anchor)
    expected = {
        "jyut_ocr_review": root / "jyut_ocr_review/__init__.py",
        "jyut_ocr_review.server": root / "jyut_ocr_review/server.py",
    }
    for name, path in expected.items():
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Invalid shared review checkout: {path}")
        loaded = sys.modules.get(name)
        if loaded is not None and Path(getattr(loaded, "__file__", "")).resolve() != path.resolve():
            raise ValueError(f"Wrong module origin for {name}; use a fresh Python process")
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    package = importlib.import_module("jyut_ocr_review")
    if Path(package.__file__).resolve() != expected["jyut_ocr_review"].resolve():
        raise ValueError("Wrong module origin for jyut_ocr_review")
    api = getattr(package, "REVIEW_API_VERSION", None)
    if type(api) is not int or api != REQUIRED_REVIEW_API:
        raise ValueError(f"Unsupported review API {api!r}; expected {REQUIRED_REVIEW_API}. Update jyut-ocr-bench")
    module = importlib.import_module("jyut_ocr_review.server")
    if Path(module.__file__).resolve() != expected["jyut_ocr_review.server"].resolve():
        raise ValueError("Wrong module origin for jyut_ocr_review.server")
    if not callable(getattr(module, "main", None)):
        raise ValueError("The shared review server has no main entrypoint")
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--tool-root", help="Path to the shared jyut-ocr-bench checkout")
    options, forwarded = parser.parse_known_args(argv)
    try:
        module = load_review(options.tool_root)
    except (ImportError, OSError, ValueError) as exc:
        print(f"review launcher: {exc}", file=sys.stderr)
        return 2
    # Keep cwd unchanged: relative project/data paths belong to the caller.
    return module.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
