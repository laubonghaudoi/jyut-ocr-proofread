#!/usr/bin/env python3
"""Scan cluster bulk-OCR output directories for stale, failed, or orphaned books."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Sequence


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def scan_out_dirs(
    out_dirs: Sequence[Path],
    *,
    work_dir: Path | None = None,
    stale_minutes: float = 45.0,
    now: float | None = None,
) -> dict[str, Any]:
    current_wall = time.time() if now is None else now
    stale_seconds = max(60.0, float(stale_minutes) * 60.0)
    completed: list[str] = []
    failed: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    in_progress: list[dict[str, Any]] = []

    seen_books: set[str] = set()
    for out_dir in out_dirs:
        if not out_dir.is_dir():
            continue
        for book_dir in sorted(out_dir.iterdir()):
            if not book_dir.is_dir():
                continue
            status_path = book_dir / "status.json"
            if not status_path.is_file():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                failed.append({"id": book_dir.name, "outDir": str(out_dir), "reason": "corrupt status.json"})
                continue
            book_id = str(status.get("id") or book_dir.name)
            seen_books.add(book_id)
            stages = status.get("stages") or {}
            collect_exit = (stages.get("collect") or {}).get("exit")
            finish_exit = (stages.get("finish") or {}).get("exit")
            if finish_exit == 0 or collect_exit == 0:
                completed.append(book_id)
                continue

            bad = [
                (name, info.get("exit"))
                for name, info in stages.items()
                if isinstance(info, dict) and info.get("exit") not in (None, 0)
            ]
            if bad:
                failed.append(
                    {"id": book_id, "outDir": str(out_dir), "stages": bad, "error": status.get("error")}
                )
                continue

            updated_ts = _parse_iso(status.get("updatedAt"))
            if updated_ts is None:
                try:
                    updated_ts = status_path.stat().st_mtime
                except OSError:
                    updated_ts = current_wall
            age_s = max(0.0, current_wall - updated_ts)
            entry = {
                "id": book_id,
                "outDir": str(out_dir),
                "ageMinutes": round(age_s / 60.0, 1),
                "stage": status.get("currentStage"),
            }
            if age_s >= stale_seconds:
                stale.append(entry)
            else:
                in_progress.append(entry)

    untracked_work: list[str] = []
    if work_dir is not None and work_dir.is_dir():
        for d in sorted(work_dir.iterdir()):
            if d.is_dir() and d.name not in seen_books:
                untracked_work.append(d.name)

    return {
        "completed": len(completed),
        "failed": failed,
        "stale": stale,
        "inProgress": in_progress,
        "untrackedWorkDirs": untracked_work,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out_dirs", nargs="+", type=Path, help="one or more mass_ocr --out directories")
    p.add_argument("--work-dir", type=Path, help="optional shared work directory to check for orphans")
    p.add_argument("--stale-minutes", type=float, default=45.0)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = scan_out_dirs(args.out_dirs, work_dir=args.work_dir, stale_minutes=args.stale_minutes)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if not report["failed"] and not report["stale"] else 1
    print(
        f"completed={report['completed']}  in_progress={len(report['inProgress'])}  "
        f"stale={len(report['stale'])}  failed={len(report['failed'])}  "
        f"untracked_work={len(report['untrackedWorkDirs'])}"
    )
    for item in report["failed"]:
        print(f"  FAILED {item['id']} ({item['outDir']}): {item.get('stages') or item.get('reason')}")
    for item in report["stale"]:
        print(f"  STALE  {item['id']} ({item['outDir']}): stage={item.get('stage')} age={item['ageMinutes']}m")
    return 0 if not report["failed"] and not report["stale"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
