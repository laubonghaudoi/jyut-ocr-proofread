#!/usr/bin/env python3
"""Cluster dashboard: summarize mass_ocr output directories and fleet router status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Sequence
import urllib.error
import urllib.request


STAGE_NAMES = ("render", "ocr", "profile", "proof", "finish", "collect")


def collect_snapshot(
    out_dirs: Sequence[Path], *, fleet_status_url: str | None = None
) -> dict[str, Any]:
    books: dict[str, dict[str, Any]] = {}
    stage_done = {name: 0 for name in STAGE_NAMES}
    stage_fail = {name: 0 for name in STAGE_NAMES}
    total_pages_done = 0

    for out_dir in out_dirs:
        if not out_dir.is_dir():
            continue
        for entry in sorted(out_dir.iterdir()):
            status_path = entry / "status.json"
            if not entry.is_dir() or not status_path.is_file():
                continue
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            book_id = str(data.get("id") or entry.name)
            books[book_id] = data
            stages = data.get("stages") or {}
            for name in STAGE_NAMES:
                info = stages.get(name)
                if isinstance(info, dict) and info.get("exit") is not None:
                    if info.get("exit") == 0:
                        stage_done[name] += 1
                    else:
                        stage_fail[name] += 1
            if (stages.get("finish") or {}).get("exit") == 0 or (stages.get("collect") or {}).get("exit") == 0:
                total_pages_done += int(data.get("pages") or 0)

    fleet: dict[str, Any] | None = None
    if fleet_status_url:
        try:
            with urllib.request.urlopen(fleet_status_url, timeout=3.0) as resp:
                fleet = json.loads(resp.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError):
            fleet = {"error": "unreachable"}

    return {
        "booksTracked": len(books),
        "booksCompleted": max(stage_done["collect"], stage_done["finish"]),
        "pagesCompleted": total_pages_done,
        "stagesDone": stage_done,
        "stagesFailed": stage_fail,
        "fleet": fleet,
    }


def format_snapshot(snap: dict[str, Any]) -> str:
    lines = [
        f"[monitor] tracked={snap['booksTracked']}  completed={snap['booksCompleted']}  "
        f"pages_completed={snap['pagesCompleted']}",
        "  stages done : " + "  ".join(f"{k}={snap['stagesDone'][k]}" for k in STAGE_NAMES),
        "  stages fail : " + "  ".join(f"{k}={snap['stagesFailed'][k]}" for k in STAGE_NAMES if snap['stagesFailed'][k]),
    ]
    fleet = snap.get("fleet")
    if isinstance(fleet, dict):
        if "error" in fleet:
            lines.append(f"  fleet router: {fleet['error']}")
        else:
            lines.append(
                f"  fleet router: {fleet.get('healthyBackends', 0)}/{fleet.get('totalBackends', 0)} healthy  "
                f"requests={fleet.get('totalRequests', 0)}"
            )
    return "\n".join(line for line in lines if line.strip())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out_dirs", nargs="+", type=Path)
    p.add_argument("--fleet-status-url", help="optional http://host:status_port/status URL")
    p.add_argument("--interval", type=float, default=0.0, help="refresh interval in seconds (0 = once)")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    while True:
        snap = collect_snapshot(args.out_dirs, fleet_status_url=args.fleet_status_url)
        if args.json:
            print(json.dumps(snap, indent=2, ensure_ascii=False), flush=True)
        else:
            print(format_snapshot(snap), flush=True)
        if args.interval <= 0:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
