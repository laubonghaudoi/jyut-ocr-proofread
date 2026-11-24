"""Opt-in timing evidence, separate from packet replay state and page seals."""

from __future__ import annotations

from contextlib import suppress
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4


EVENTS_ENV = "JYUT_OCR_PACKET_EVENTS"
CASE_ENV = "JYUT_OCR_PACKET_CASE"
HELP = (
    f"Set {EVENTS_ENV} to a JSONL path outside the packets directory to record packet timing events. "
    f"{CASE_ENV} optionally labels the case. The log is independent of the answer checkpoint and seals."
)


class PacketEvents:
    """Events in one script invocation. An unavailable log never blocks OCR."""

    def __init__(self, packets_dir: Path, stage: str | None = None, *, enabled: bool = True):
        self.stage = stage
        self._path: Path | None = None
        path = os.environ.get(EVENTS_ENV) if enabled else None
        if not path:
            return
        try:
            target = Path(path).expanduser()
            parent = target.parent.resolve()
            if target.suffix != ".jsonl" or (parent / target.name).is_relative_to(packets_dir.resolve()):
                raise ValueError("use a .jsonl file outside the packets directory")
            self._path = parent / target.name
        except (OSError, RuntimeError, ValueError) as error:
            self._warn(error)
            return
        self._invocation = uuid4().hex
        self._case = os.environ.get(CASE_ENV)

    @staticmethod
    def _warn(error: Exception) -> None:
        with suppress(OSError):
            print(f"WARNING packet event log unavailable: {error}", file=sys.stderr, flush=True)

    def record(self, event: str, **fields: Any) -> None:
        path = self._path
        if path is None:
            return
        row = {
            "schema_version": 1, "event": event, "stage": self.stage, "case": self._case,
            "invocation_id": self._invocation, "pid": os.getpid(),
            "timestamp_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(), **fields,
        }
        try:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                state = os.fstat(stream.fileno())
                if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                    raise ValueError("event log must be a regular file with one link")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
                stream.flush()
        except (OSError, ValueError) as error:
            self._path = None
            self._warn(error)

    def emit(self, wave: Mapping[str, Any]) -> None:
        if self._path is not None:
            self.record("emit", wave=wave["wave"], request_count=wave["pending"], bundles=[
                {"name": Path(bundle["bundle"]).name, "sha256": bundle["sha256"],
                 "request_count": bundle["itemCount"]} for bundle in wave["bundles"]
            ])

    def applied(self, wave_dirs: Iterable[Path], **counts: int) -> None:
        if self._path is None:
            return
        waves = []
        for directory in dict.fromkeys(wave_dirs):
            try:
                manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                stage = manifest.get("stage") if isinstance(manifest, dict) else None
            except (OSError, ValueError):
                stage = None
            waves.append({"wave": directory.name, "stage": stage if stage in ("book-profile", "proofread") else None})
        stages = {wave["stage"] for wave in waves}
        self.record("apply", stage=next(iter(stages)) if len(stages) == 1 else None,
                    waves=waves, **counts)
