#!/usr/bin/env python3
"""Install/check the portable skill and its OCR environment.

Apple Silicon chooses --engine apple-vision (no model downloads) or
--engine hunyuan-paddle (both engines). Linux/WSL2 defaults to hunyuan-paddle
and also supports --engine hunyuan. Intel Macs are unsupported.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Sequence

# When the agent's shell call began, near enough (install.sh execs this file):
# the GPU hand-over smoke gets only what is left of OCR_PHASE_MAX_SECONDS.
STARTED = time.time()

from _setup_ocr import (LocalModel, SetupError, SetupOptions, default_directories, inspect_setup,
                        setup)
from install_local_adapters import (PORTABLE_AGENTS, InstallError, build_portable_specs, perform_check,
                                    perform_install)

REPO_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    config, data = default_directories()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", action="append", choices=PORTABLE_AGENTS,
                        help="repeat to install for multiple agents; default generic (~/.agents/skills); "
                             "qwen is Qwen Code (~/.qwen/skills)")
    parser.add_argument("--skill-dir", type=Path, help="custom skill collection parent; appends jyut-ocr-proofread")
    parser.add_argument("--with-agents", action="store_true", help="also install optional Codex/Claude custom-agent adapters")
    parser.add_argument("--engine", choices=("apple-vision", "hunyuan-paddle", "hunyuan"),
                        help="OCR route: apple-vision (Apple Silicon, no model downloads), "
                             "hunyuan-paddle (both engines), hunyuan (Linux/WSL2 only). "
                             "macOS first setup asks before downloading; retired auto/vision names are rejected")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "metal"),
                        help="override the device: auto (the default: the NVIDIA GPU, or Metal on Apple Silicon, "
                             "else CPU), cpu, cuda or metal. Without it the saved choice is kept")
    parser.add_argument("--paddle-vl-backend", choices=("auto", "native", "mlx-vlm-server"),
                        help="Paddle VL inference backend: auto uses the tested platform default; native keeps "
                             "Paddle inference; mlx-vlm-server uses an isolated MLX environment on Apple Silicon. "
                             "Without it the saved request is kept; --device cpu requires native")
    parser.add_argument("--check", action="store_true", help="read-only check; never initializes, downloads or starts OCR models")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--env-only", action="store_true", help="prepare/check OCR without changing installed skill entries")
    mode.add_argument("--skill-only", action="store_true", help="install/check skill entries without preparing OCR")
    parser.add_argument("--offline", action="store_true", help="use verified local assets only; no network downloads")
    parser.add_argument("--prepare-only", action="store_true",
                        help="download, verify and configure, but run no setup test (it may need a GPU hand-over, "
                             "which must run in the foreground); exits 2 until a run without it passes the test")
    parser.add_argument("--install-system-deps", action="store_true", help="explicitly allow recognized system package-manager commands; never installs GPU drivers/toolkits")
    parser.add_argument("--config-dir", type=Path, default=config, help="machine-local env.sh and setup-state.json directory")
    parser.add_argument("--data-dir", type=Path, default=data, help="external models, runtime, Python environment and smoke records directory")
    parser.add_argument("--backup-root", type=Path, default=Path.home() / ".jyut-ocr-proofread-backups")
    local = parser.add_argument_group(
        "local proofreading model",
        "declare an OpenAI-compatible model on this machine that proofreads (and that the agent may run on); "
        "written to env.sh as PROOFREAD_ENDPOINT, PROOFREAD_MODEL, PROOFREAD_MAX_INFLIGHT and, for the GPU "
        "hand-over, OCR_PHASE_*")
    local.add_argument("--local-model-endpoint", metavar="URL",
                       help="its chat completions URL, e.g. http://127.0.0.1:8080/v1/chat/completions")
    local.add_argument("--local-model-name", metavar="NAME", help="its model ID, exactly as the agent and the server name it")
    local.add_argument("--local-model-release-cmd", metavar="CMD",
                       help="shell command that stops it to free the GPU (with --local-model-restore-cmd)")
    local.add_argument("--local-model-restore-cmd", metavar="CMD",
                       help="shell command that starts it again; must be safe to repeat")
    local.add_argument("--local-model-health-url", metavar="URL",
                       help="URL that answers 2xx once it serves again (default: the endpoint's /v1/models)")
    local.add_argument("--local-model-concurrency", metavar="N", type=positive_int,
                       help="requests the server runs at once (its --max-concurrency, parallel slots or lanes); "
                            "written as PROOFREAD_MAX_INFLIGHT, the default of book_profile.py --workers (over "
                            "HTTP) and proofread_pages.py --max-inflight")
    return parser


def positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be a whole number of 1 or more, not {text!r}")
    return value


def local_model(args: argparse.Namespace, parser: argparse.ArgumentParser) -> LocalModel | None:
    given = {name: getattr(args, "local_model_" + name) for name in
             ("endpoint", "name", "release_cmd", "restore_cmd", "health_url", "concurrency")}
    if not any(value is not None for value in given.values()):
        return None
    if args.check or args.skill_only:
        parser.error("--local-model-* options change env.sh; they cannot be used with --check or --skill-only")
    if not given["endpoint"] or not given["name"]:
        parser.error("--local-model-endpoint and --local-model-name go together")
    return LocalModel(**given)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    local = local_model(args, parser)
    options = SetupOptions(repo_root=REPO_ROOT, config_dir=args.config_dir.expanduser().absolute(),
                           data_dir=args.data_dir.expanduser().absolute(), engine=args.engine or "default",
                           device=args.device or "default", offline=args.offline,
                           install_system_deps=args.install_system_deps,
                           interactive=sys.stdin.isatty(), local_model=local, prepare_only=args.prepare_only,
                           started_at=STARTED, paddle_vl_backend=args.paddle_vl_backend or "default")
    try:
        from _setup_ocr import platform_name, machine_name, choose_engines, engine_request, read_environment
        system = platform_name()
        existing = read_environment(options.config_dir / "env.sh")
        if args.engine or existing.get("JYUT_OCR_ENGINE") or existing.get("JYUT_OCR_ENGINES"):
            choose_engines(engine_request(options.engine, existing), system=system, machine=machine_name(system))
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
