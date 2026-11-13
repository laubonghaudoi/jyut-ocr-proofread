#!/usr/bin/env python3
"""Install one portable Agent Skill and prepare its local OCR environment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from _setup_ocr import SetupError, SetupOptions, default_directories, inspect_setup, setup
from install_local_adapters import (InstallError, build_portable_specs, perform_check,
                                    perform_install)

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    config, data = default_directories()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", action="append", choices=("generic", "codex", "claude", "gemini", "antigravity"),
                        help="repeat to install for multiple agents; default generic (~/.agents/skills)")
    parser.add_argument("--skill-dir", type=Path, help="custom skill collection parent; appends jyut-ocr-proofread")
    parser.add_argument("--with-agents", action="store_true", help="also install optional Codex/Claude custom-agent adapters")
    parser.add_argument("--engine", choices=("auto", "vision", "hunyuan"), default="auto")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "metal"), default="auto")
    parser.add_argument("--check", action="store_true", help="read-only check; never initializes, downloads or starts OCR models")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--env-only", action="store_true", help="prepare/check OCR without changing installed skill entries")
    mode.add_argument("--skill-only", action="store_true", help="install/check skill entries without preparing OCR")
    parser.add_argument("--offline", action="store_true", help="use verified local assets only; no network downloads")
    parser.add_argument("--install-system-deps", action="store_true", help="explicitly allow recognized system package-manager commands; never installs GPU drivers/toolkits")
    parser.add_argument("--config-dir", type=Path, default=config, help="machine-local env.sh and setup-state.json directory")
    parser.add_argument("--data-dir", type=Path, default=data, help="external models, runtime, Python environment and smoke records directory")
    parser.add_argument("--backup-root", type=Path, default=Path.home() / ".jyut-ocr-proofread-backups")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = SetupOptions(repo_root=REPO_ROOT, config_dir=args.config_dir.expanduser().absolute(),
                           data_dir=args.data_dir.expanduser().absolute(), engine=args.engine,
                           device=args.device, offline=args.offline,
                           install_system_deps=args.install_system_deps,
                           interactive=sys.stdin.isatty())
    try:
        current = True
        if not args.env_only:
            specs = build_portable_specs(repo_root=REPO_ROOT, agents=args.agent or ["generic"],
                                         skill_dir=args.skill_dir, with_agents=args.with_agents)
            if args.check:
                current = perform_check(specs)
            else:
                backup = perform_install(specs, args.backup_root)
                if backup:
                    print(f"Skill installation backup: {backup}")
        if not args.skill_only:
            result = inspect_setup(options) if args.check else setup(options)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            current = current and result.get("status") == "ready"
        return 0 if current else 2
    except (SetupError, InstallError, OSError, ValueError) as exc:
        print(f"ACTION REQUIRED: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Setup interrupted. Completed files/configuration are preserved; rerun the same command to resume.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
