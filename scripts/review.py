#!/usr/bin/env python3
"""Launch the shared jyut-ocr-bench review tool (jyut_ocr_review, review API 1 or 2).

API 2 keeps one answer store, the bench checkout's sample catalogue
(sample-catalogue/v1.json): submitting a review overwrites that case's answer,
and git keeps the history.  API 1's project-local queues, legacy audits and
Goldgate import are gone there, so their options are named as retired here
instead of failing inside the tool."""
from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import sys


# The jyut_ocr_review protocol versions this launcher can start.
SUPPORTED_REVIEW_APIS = (1, 2)
# Commands and options of API 1 that API 2 removed (project queues, legacy
# audits, Goldgate import).
API1_COMMANDS = ("validate-project", "import-goldgate")
API1_OPTIONS = ("--project-root", "--audit-root", "--root")


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
    review_api(package)
    module = importlib.import_module("jyut_ocr_review.server")
    if Path(module.__file__).resolve() != expected["jyut_ocr_review.server"].resolve():
        raise ValueError("Wrong module origin for jyut_ocr_review.server")
    if not callable(getattr(module, "main", None)):
        raise ValueError("The shared review server has no main entrypoint")
    return module


def review_api(package) -> int:
    """The checkout's REVIEW_API_VERSION, when this launcher supports it."""
    api = getattr(package, "REVIEW_API_VERSION", None)
    supported = " and ".join(str(version) for version in SUPPORTED_REVIEW_APIS)
    if type(api) is int and api in SUPPORTED_REVIEW_APIS:
        return api
    if type(api) is int and api > max(SUPPORTED_REVIEW_APIS):
        raise ValueError(f"Unsupported review API {api}; this launcher starts API {supported}. "
                         "Update this skill (scripts/review.py)")
    raise ValueError(f"Unsupported review API {api!r}; this launcher starts API {supported}. "
                     "Update jyut-ocr-bench")


def retired_arguments(forwarded: list[str]) -> list[str]:
    """The API 1 commands and options in FORWARDED (before a literal --)."""
    found = []
    if forwarded and forwarded[0] in API1_COMMANDS:
        found.append(forwarded[0])
    for item in forwarded:
        if item == "--":
            break
        name = item.split("=", 1)[0]
        if name in API1_OPTIONS and name not in found:
            found.append(name)
    return found


USAGE = """usage: review.py [--tool-root PATH] [ARGS ...]

Launch the shared jyut-ocr-bench review tool (review API 1 or 2), from
--tool-root PATH, else JYUT_OCR_BENCH_ROOT, else a jyut-ocr-bench folder beside
this skill's.  ARGS go to the tool unchanged, --help with them; its own help
lists them once its checkout is found.  API 2 writes answers to the bench
checkout's sample-catalogue/v1.json; API 1's --project-root, --audit-root,
validate-project and import-goldgate are refused there."""


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--tool-root", help="Path to the shared jyut-ocr-bench checkout")
    options, forwarded = parser.parse_known_args(argv)
    try:
        module = load_review(options.tool_root)
    except (ImportError, OSError, ValueError) as exc:
        if forwarded in (["--help"], ["-h"]):
            # The launcher's own help needs no checkout; the tool's needs one.
            print(USAGE)
            print(f"review launcher: the tool's own options are not shown: {exc}", file=sys.stderr)
            return 0
        print(f"review launcher: {exc}", file=sys.stderr)
        return 2
    api = sys.modules["jyut_ocr_review"].REVIEW_API_VERSION
    retired = retired_arguments(forwarded) if api >= 2 else []
    if retired:
        print(f"review launcher: {', '.join(retired)}: review API {api} has no project queues, legacy audits "
              "or Goldgate import; answers go only to the bench checkout's sample-catalogue/v1.json. "
              "Start it without them (e.g. review.py --port 8765), or use a review API 1 checkout",
              file=sys.stderr)
        return 2
    # Keep cwd unchanged: relative project/data paths belong to the caller.
    return module.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
