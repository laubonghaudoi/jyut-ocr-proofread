#!/usr/bin/env python3
"""Run the OCR draft phase, freeing GPU memory first if a local LLM server holds it.

Why this exists
---------------
On a single GPU the OCR model and a local LLM server usually cannot be resident
at the same time.  Measured on one RTX 5090 (32,607 MiB): a 27B NVFP4 server has
a 22,481 MiB floor before any KV cache, and PaddleOCR-VL peaks at 11,495 MiB.
They do not both fit.

That is awkward when the whole workflow runs *inside* an agent harness backed by
that same server: the agent would have to stop the server it is thinking on.

It works anyway, because during a FOREGROUND shell tool call the harness has no
request in flight and its conversation state is client-side.  Stopping the
server, doing the OCR and restarting it inside ONE command is invisible to the
harness apart from a cold prefix cache on the next turn.

It does not work in the background.  Measured 2026-09-23 in Qwen Code: the agent
backgrounded the handover because a whole book outlasts the harness's shell time
limit (120 s by default, 600 s at most); its next turn found the server stopped
(ECONNREFUSED), retried the refused connection 7 times over about 76 s, gave up
and exited, and its exit killed the handover - SIGTERM to the process group,
SIGKILL 200 ms later - before the server came back.

One call is one hand-over WINDOW:

1. Nothing is stopped unless the window can be opened: the machine-wide window
   lock (one hand-over at a time) and the shared GPU queue lease, tried without
   waiting.  Another job on the GPU queue gives exit 76 with the server untouched.
2. The free GPU memory is compared with what the engines of this call need:
   the largest of them, not the sum (they run one after another): Paddle about
   11.5 GiB, Hunyuan from its slots x context (KV cache plus about 3.4 GiB),
   or OCR_PHASE_REQUIRED_MIB.  OCR_PHASE_SWAP=auto|always|never decides, on
   every platform: without nvidia-smi (macOS: vm_stat) an unknown amount
   counts as too little.  But only a model that is up before the call is
   ever stopped, so only it is started again: its health URL answers, or
   something listens on that URL's port (a model still loading), or one of
   the GPU's compute processes is the model's (it listens there, descends
   from what does, or gives the port as --port).  A model the person
   stopped stays stopped: on an idle GPU the OCR runs directly, with no
   release, stand-in, watchdog or restore, and status.json says
   "left-down" (outside the states recover_left_down restores); with too
   little free memory the call names what holds the GPU and how to free it
   and exits 1 (GPU FULL), stopping nothing.  Without OCR_PHASE_HEALTH_URL
   whether the model is up cannot be told, and the free memory decides as
   before.
3. Before the server is stopped, its other clients are looked for
   (scripts/_tenant_clients.py): a client with a request on the wire is waited
   for, but only while the window would still have the release and the
   first engine's load and longest page left (window_floor: measured in
   timings.json and BOOK/reports/ocr-times.json); still busy then: exit 76,
   nothing stopped, naming the clients.  OCR_PHASE_CLIENT_IGNORE names the
   ones never waited for (a proxy or web UI whose pooled connection never
   closes).  Idle sessions get a stand-in on the freed port that answers
   503 + Retry-After until the restore, instead of a refused connection.
4. A detached watchdog starts (it restores the server if this process dies),
   then the release.  Still too little free memory after it: the server is
   restored at once and the call exits 1 with the numbers, naming each
   compute process on the GPU (pid, user, name, MiB, its systemd unit) with
   the command that frees it (`systemctl --user stop UNIT`, `kill PID`).
   Nothing else on the GPU is ever stopped or killed: the person decides.
5. The runner runs in its own process group with a private GPU queue
   (OCR_GPU_QUEUE_DIR) and OCR_PHASE_WINDOW, so it never waits behind this
   process's own lease while every other session stays queued; runners outside
   a window defer instead of loading (scripts/_gpu_queue.py).
6. The restore waits until every OCR process of the window is gone: the
   runner's group, the detached Hunyuan broker it reports (OCR_PHASE_CHILDREN)
   and every lease in the private queue - GPU children inherit their lease, so
   this works where nvidia-smi cannot list processes (WSL2).  After a grace
   period the window's processes are killed.  The server is never restored
   while an OCR model of the window is still resident.  With
   OCR_PHASE_TENANT_MIB set, free memory is also waited for.
7. Every wait - the runner's wind-down, the kill grace, the OCR model leaving,
   the restore and its health check - is counted inside the time budget, so a
   call never outlives OCR_PHASE_MAX_SECONDS; the reserve kept for them grows
   or shrinks with the release-to-healthy time measured on this machine
   (under the OCR_PHASE_READY_PROBE setting in use; a restore the call's end
   cut short counts with the time it had, as a lower bound).

So this script:

- takes a time budget (--max-seconds, or OCR_PHASE_MAX_SECONDS from the machine
  config) set below the harness's shell time limit: the runner stops between
  pages when the budget is spent, the server is restored, and the exit code is
  75 - "not finished, run the SAME command again".  Runners skip sealed pages,
  so repeated calls finish the book;
- restores the server it stopped on success, on failure, and on
  SIGINT/SIGTERM/SIGHUP, and never one it did not stop;
- starts a detached watchdog, outside the process group, before releasing the
  server: if this process is killed before it has restored the server (a
  harness exit, a SIGKILL), the watchdog kills the window's OCR processes and
  restores it, retrying with backoff for half an hour, writing status.json and
  running OCR_PHASE_NOTIFY_CMD (with OCR_PHASE_NOTICE set) when it cannot;
- waits for the health endpoint and treats a failed restore as a hard error,
  because leaving the agent's own backend down is the worst possible outcome.
  A server whose health URL answers before its model can serve can also be
  asked for a 1-token chat completion (OCR_PHASE_READY_PROBE, off by
  default) before it counts as back.

Configure the commands for your machine (they are intentionally not hard-coded,
since this skill runs on many hosts):

    export OCR_PHASE_RELEASE_CMD="systemctl --user stop ninfer.service"
    export OCR_PHASE_RESTORE_CMD="systemctl --user start ninfer.service"
    export OCR_PHASE_HEALTH_URL="http://127.0.0.1:8090/v1/models"
    export OCR_PHASE_MAX_SECONDS=540   # below the harness's shell time limit

    python3 scripts/run_ocr_phase.py -- python3 scripts/ocr_book.py BOOK
    # exit 75: run the same command again; 76: wait, then the same; 0: done
    # (the RESULT line names it with --round N+1, which changes nothing but
    # keeps repeated calls from being identical)

A shell with NO time limit (the person removed the harness's cap, e.g. Qwen
Code `tools.shell.defaultTimeoutMs: 0`, and checked it) runs a whole book in
one call: OCR_PHASE_MAX_SECONDS=0 with OCR_PHASE_UNLIMITED_SHELL=1 (or
--unlimited-shell).  Without that flag a budget of 0 is refused when the
server has to be stopped, so a machine config that only says 0 falls back to
an error naming the fix rather than to a call the harness kills at its cap
(measured cost of the cap on one 609-page book: 8 chunk boundaries of about
40 s each for engine A alone, 7.5 min for the two engines, each boundary a
full re-prefill of the agent's 100K-token context).

An agent that does not run on the local model needs neither the budget nor the
foreground: the model it thinks with is elsewhere, so nothing of its own dies while
the server is down.  --background says so: the call has no time budget, the agent
starts it detached and waits for it with wait_for_run.py, and a whole book (or
several) is one window - the server stopped once and started once, where budgeted
calls stop and start it at every chunk boundary.  The watchdog is the same: a call
that is killed has its server restored.  It is refused (exit 2, nothing stopped)
when the agent does run on the local model, by any of three signs (agent_on_model):
JYUT_OCR_AGENT_IS_LOCAL_MODEL=1; Qwen Code on the proofreading model; or a process
that runs this call - the shell, the agent - holding a connection to the server.

The state folder (${XDG_STATE_HOME:-~/.local/state}/jyut-ocr/handover, or
OCR_PHASE_STATE_DIR; private, 0700) holds the window lock, status.json, the
watchdog log and timings.json.  A call that finds status.json saying an
earlier hand-over left the server down (its watchdog gave up, or a restore
failed) restores it before it decides anything (recover_left_down); still
down: exit 3, no OCR run.

Exit codes: 0 done; 75 time budget spent, server restored, call again;
76 GPU busy with another job (or the model's other clients), nothing was
stopped - or the runner sealed no page in its window (it waited for the GPU
or had no time to load), and when the server was stopped for it the RESULT
line says so and that it is back: wait and call again; 1 the runner failed
(or the GPU stayed too full after the release: GPU FULL, with what holds it
and how to free it); 2 configuration; 3 the server was NOT restored.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paddle_pool  # noqa: E402
from _phase_budget import (  # noqa: E402
    CHILDREN_ENV, DEADLINE_ENV, EXIT_INCOMPLETE, PROGRESS_ENV, process_start_time, read_children)
from _gpu_queue import (  # noqa: E402
    WINDOW_ENV, GPUQueue, GPUQueueError, GPUQueueTimeout, handover_directory, live_ticket_count)

EXIT_GPU_BUSY = 76
# PaddleOCR-VL measured peak is 11,495 MiB at 300 DPI; leave a little margin.
# Also the need of a runner this script does not know.
PADDLE_MIB = 12288
DEFAULT_REQUIRED_MIB = PADDLE_MIB
# HunyuanOCR on llama.cpp: KV cache per token (24 layers x 8 KV heads x 128
# dims x K and V in f16) for every slot's context, plus the weights (about
# 2 GB), compute buffers and CUDA context.  The base is fitted to peaks
# measured at 16,384 context per slot: 5,036 MiB (1 slot), 6,572 (2), 9,644
# (4), 15,788 (8) and 28,076 (16); the formula gives exactly these.
HUNYUAN_KV_BYTES_PER_TOKEN = 98304
HUNYUAN_BASE_MIB = 3500
HUNYUAN_DEFAULT_CTX = 16384
DEFAULT_HEALTH_TIMEOUT = 180.0
# Seconds kept for the restore and its health check while nothing was
# measured on this machine yet (timings.json).  The reference server measured
# 4-5 s from start to healthy.
DEFAULT_RESTORE_ESTIMATE = 60.0
# A measured estimate never keeps more than this, nor more than this share of
# the call's budget: slow restores cannot grow it until every budgeted call is
# refused (nothing is restored then, so nothing would ever lower it again).
RESTORE_ESTIMATE_CAP = 300.0
RESTORE_BUDGET_SHARE = 0.5
# A runner past its own deadline gets this long to stop by itself.
WIND_DOWN = 5.0
# How long a runner gets to exit after SIGTERM before its group is killed.
KILL_GRACE = 10.0
# How long the window's OCR processes (the shared broker drains its model in
# about a second) get to leave after the runner before they are killed.
WINDOW_GRACE = 15.0
# After the kill, how long they get to be gone.
KILL_SETTLE = 5.0
# With OCR_PHASE_TENANT_MIB: how long the freed memory gets to show up.
TENANT_WAIT = 10.0
# After the release, how long the server's memory gets to show up as free.
RELEASE_SETTLE = 15.0
# The GPU queue is asked this long before the call gives up with exit 76.
BUSY_WAIT = 2.0
# Less OCR time than this is not worth stopping the server for.
MIN_RUN_SECONDS = 60.0
# Seconds kept for the release command and the freed memory showing up while
# nothing was measured on this machine yet (timings.json).
DEFAULT_RELEASE_ESTIMATE = 30.0
# How long the 503 stand-in keeps trying to bind the freed port after the
# release (scripts/_tenant_clients.py raises PortBusy while it is still taken).
STAND_IN_BIND_WAIT = 5.0
# Without a budget, how long other clients of the model are waited for.
UNBUDGETED_CLIENT_WAIT = 1800.0
# How long the watchdog keeps trying to restore the server.
RESTORE_HORIZON = 1800.0
# Longest a release or restore command may run (a service manager's stop or
# start timeout); a budgeted call never lets one run past its end.
COMMAND_TIMEOUT = 300.0
# After a restore that followed a phase killed mid-release, how long to watch for
# the interrupted stop landing late.  systemd's default stop timeout is 90 s, but
# a stop that has not landed after this long was not coming.
RELEASE_SETTLE_SECONDS = 45.0
# How long the watchdog lets an interrupted restore finish on its own before it
# runs the (repeatable) restore command itself.
RESTORE_GRACE_SECONDS = 15.0
# How long a call asks the server's health URL whether a server status.json
# says was left down is up again.
HEALTH_PROBE_SECONDS = 2.0
# OCR_PHASE_READY_PROBE: the longest one 1-token chat completion may take.
READY_REQUEST_TIMEOUT = 60.0
# A failed budgeted restore with no more than this left was cut short by the
# call's end, not refused by the server: its time is kept as a lower bound.
CUT_SHORT_SECONDS = 1.0
# timings.json lists that hold restore times; restoreProbe says which
# OCR_PHASE_READY_PROBE URL they were measured with (absent: health only).
RESTORE_TIMINGS = ("restoreSeconds", "unbudgetedRestoreSeconds")
LOG_LIMIT = 1024 * 1024

HUNYUAN_SCRIPTS = ("run_hunyuanocr.py", "run_hunyuanocr_second_look.py")
PADDLE_SCRIPTS = ("run_paddleocr_vl.py",)
BOOK_SCRIPTS = ("ocr_book.py",)


class PhaseError(RuntimeError):
    """The phase cannot proceed safely."""


class Interrupted(BaseException):
    """A signal asked this phase to stop; cleanup happens in the finally blocks."""


# The signals that stop a phase (and, once the server is stopped, restore it).
HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def signals_held():
    """Hold HANDLED_SIGNALS until the block ends: one that arrives meanwhile is
    handled as the block ends, so a step that must not be cut in two (a
    stand-in bound but not yet known to the restore, handlers half installed)
    finishes first.  Without pthread_sigmask the block runs unprotected."""
    mask = getattr(signal, "pthread_sigmask", None)
    if mask is None:
        yield
        return
    before = mask(signal.SIG_BLOCK, ())  # the current mask, unchanged
    try:
        mask(signal.SIG_BLOCK, HANDLED_SIGNALS)
        yield
    finally:
        mask(signal.SIG_SETMASK, before)


def say(message: str, *, error: bool = False) -> None:
    """Print without ever letting a closed stdout stop a restore.

    When the harness that started this command exits, the pipe behind stdout
    goes away; a print that raised there would abort the restore it announces.
    """
    try:
        print(message, file=sys.stderr if error else sys.stdout, flush=True)
    except (OSError, ValueError):
        pass


class Clock:
    """The call's hard end (time.time()), or None for an unbudgeted call."""

    def __init__(self, end: float | None):
        self.end = end

    def left(self) -> float | None:
        return None if self.end is None else self.end - time.time()

    def cap(self, seconds: float) -> float:
        """SECONDS, or less when the end comes first (never below 0)."""
        left = self.left()
        return max(0.0, seconds if left is None else min(seconds, left))


# --- memory -------------------------------------------------------------------------

def nvidia_free_mib() -> int | None:
    """Free memory of the GPU the runners use, or None when nvidia-smi cannot say.

    The first CUDA_VISIBLE_DEVICES entry when it is an index; otherwise the
    only GPU, or the fullest one (a multi-GPU host whose CUDA order is not
    known gets the conservative answer)."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        result = subprocess.run(
            [smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    free = []
    for line in result.stdout.splitlines():
        try:
            used, total = (int(value) for value in line.split(","))
        except ValueError:
            continue
        free.append(total - used)
    if not free:
        return None
    visible = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
    if visible.isdigit() and int(visible) < len(free):
        return free[int(visible)]
    return min(free)


def parse_vm_stat(text: str) -> int | None:
    """Estimated reclaimable unified memory in MiB from `vm_stat`: free,
    inactive and speculative pages. This is not a guaranteed allocation budget."""
    size = re.search(r"page size of (\d+) bytes", text)
    if not size or int(size.group(1)) <= 0:
        return None
    pages = 0
    for name in ("free", "inactive", "speculative"):
        match = re.search(rf"^Pages {name}:\s+(\d+)\.?\s*$", text, re.M)
        if not match:
            return None
        pages += int(match.group(1))
    # Purgeable pages overlap other categories: never add them again.
    return pages * int(size.group(1)) // (1024 * 1024)


def mac_free_mib() -> int | None:
    if sys.platform != "darwin" or not shutil.which("vm_stat"):
        return None
    try:
        result = subprocess.run(["vm_stat"], text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_vm_stat(result.stdout) if result.returncode == 0 else None


def free_memory_mib() -> tuple[int | None, str]:
    """(free MiB, where it was read), or (None, why it could not be)."""
    free = nvidia_free_mib()
    if free is not None:
        return free, "nvidia-smi"
    if sys.platform == "darwin":
        free = mac_free_mib()
        if free is not None:
            return free, "vm_stat (estimated reclaimable unified memory)"
        return None, "vm_stat gave no answer"
    return None, "no nvidia-smi answer"


def is_wsl() -> bool:
    """WSL2: its nvidia-smi lists no process, whatever runs on the GPU."""
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as handle:
            release = handle.read().lower()
    except OSError:
        return False
    return "microsoft" in release or "wsl" in release


def runners_gpu_uuid(smi: str) -> str | None:
    """The UUID of the GPU the runners use (picked as nvidia_free_mib picks
    it), or None when nvidia-smi does not say (then every listed process counts)."""
    try:
        result = subprocess.run(
            [smi, "--query-gpu=index,uuid,memory.used,memory.total", "--format=csv,noheader,nounits"],
            text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    gpus = []
    for line in result.stdout.splitlines() if result.returncode == 0 else ():
        fields = [field.strip() for field in line.split(",")]
        try:
            gpus.append((int(fields[0]), fields[1], int(fields[3]) - int(fields[2])))
        except (IndexError, ValueError):
            return None
    if not gpus or not all(gpu_id.startswith("GPU-") for _, gpu_id, _ in gpus):
        return None
    if len(gpus) == 1:
        return gpus[0][1]
    visible = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
    for index, gpu_id, _free in gpus:
        if (visible.isdigit() and int(visible) == index) or (visible.startswith("GPU-") and gpu_id.startswith(visible)):
            return gpu_id
    return min(gpus, key=lambda gpu: gpu[2])[1]


def compute_processes() -> tuple[list[dict] | None, str]:
    """([{"pid", "name", "mib"}] of the compute processes on the runners' GPU,
    where the list is from), or (None, why there is none).

    Only `nvidia-smi --query-compute-apps`: graphics-only processes (the
    desktop) are not in it.  None where processes cannot be listed: no
    nvidia-smi (macOS), WSL2 (its nvidia-smi lists none), or an answer that
    is not a list of processes."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None, "no nvidia-smi"
    if is_wsl():
        return None, "WSL2: nvidia-smi lists no processes there"
    try:
        result = subprocess.run(
            [smi, "--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            text=True, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return None, f"nvidia-smi failed: {error}"
    if result.returncode != 0:
        return None, f"nvidia-smi exit {result.returncode}"
    found = []
    for line in result.stdout.splitlines():
        if not line.strip() or "no running processes" in line.lower():
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 4 or not fields[1].isdigit():
            return None, f"nvidia-smi gave no process list ({line.strip()[:60]})"
        # A process name may hold a comma; "[N/A]" memory (no per-process figure) is None.
        found.append({"gpu": fields[0], "pid": int(fields[1]), "name": ",".join(fields[2:-1]).strip(),
                      "mib": int(fields[-1]) if fields[-1].isdigit() else None})
    gpu = runners_gpu_uuid(smi) if found else None
    listed = [dict(pid=entry["pid"], name=entry["name"], mib=entry["mib"]) for entry in found
              if gpu is None or entry["gpu"] == gpu]
    return listed, "nvidia-smi --query-compute-apps"


def process_label(entry: dict) -> str:
    """'pid 123 (python3, 2000 MiB)' for a compute process."""
    name = os.path.basename(str(entry.get("name") or "?")) or "?"
    mib = entry.get("mib")
    return f"pid {entry.get('pid')} ({name}{'' if mib is None else f', {mib} MiB'})"


def owner_of(pid: int) -> int | None:
    """The user id PID runs as, or None when it cannot be read here."""
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        pass
    try:
        result = subprocess.run(["ps", "-o", "uid=", "-p", str(pid)], capture_output=True, text=True,
                                timeout=10, check=False, stdin=subprocess.DEVNULL)
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def user_name(uid: int | None) -> str:
    if uid is None:
        return "unknown"
    try:
        import pwd  # noqa: PLC0415 - POSIX only
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return f"uid {uid}"


USER_MANAGER = re.compile(r"user@\d+\.service")


def systemd_unit(cgroup: str) -> tuple[str, bool] | None:
    """(the systemd service a process runs in, whether it is a user service)
    from its /proc/PID/cgroup text, or None: not in a service (a terminal's
    scope, a login session, a container, no systemd)."""
    lines = cgroup.splitlines()
    # cgroup v2 ("0::/path") first, then v1's systemd hierarchy.
    for line in sorted(lines, key=lambda line: not line.startswith("0::")):
        parts = [part for part in line.split(":", 2)[-1].split("/") if part]
        services = [part for part in parts if part.endswith(".service")]
        if not services:
            continue
        if USER_MANAGER.fullmatch(services[-1]):
            return None  # the user's own manager: a process of a scope under it
        return services[-1], any(USER_MANAGER.fullmatch(part) for part in parts)
    return None


def unit_of(pid: int) -> tuple[str, bool] | None:
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as handle:
            return systemd_unit(handle.read())
    except OSError:
        return None


def how_to_free(entry: dict) -> str:
    """The command that frees what a GPU process holds, for the person: stop
    its systemd service (a service manager would start a killed one again),
    or kill it."""
    pid, uid, unit = entry["pid"], entry.get("uid"), entry.get("unit")
    own = uid == os.getuid()
    if unit:
        command = f"systemctl --user stop {unit}" if entry.get("userUnit") else f"sudo systemctl stop {unit}"
        return command if own or not entry.get("userUnit") else f"as {entry.get('user')}: {command}"
    if own:
        return f"kill {pid}"
    return f"kill {pid} as its owner ({entry.get('user')}), or sudo kill {pid}"


def name_occupants() -> tuple[str, list[dict]]:
    """Say what holds the GPU's memory and how the person can free it, a line
    per process; nothing is stopped.  (The short form for the RESULT line,
    the processes with their user, unit and command.)"""
    processes, how = compute_processes()
    if processes is None:
        say(f"[ocr-phase] what holds the GPU cannot be listed here ({how}): free it with the GPU's own "
            "tools, then run the same command again", error=True)
        return f"what holds the GPU cannot be listed here ({how})", []
    if not processes:
        say("[ocr-phase] no compute process is on the GPU: graphics (the desktop) or processes this user "
            "cannot see hold its memory, or the engines need more than the card has (lower "
            "OCR_HUNYUAN_SLOTS, or set OCR_PHASE_REQUIRED_MIB if the estimate is too high)", error=True)
        return "no compute process is listed on the GPU", []
    shown, named = [], []
    for entry in processes:
        uid = owner_of(entry["pid"])
        unit = unit_of(entry["pid"])
        entry = dict(entry, uid=uid, user=user_name(uid), unit=unit[0] if unit else None,
                     userUnit=bool(unit and unit[1]))
        command = how_to_free(entry)
        mib = "?" if entry["mib"] is None else entry["mib"]
        in_unit = f", unit {entry['unit']}" if entry["unit"] else ""
        say(f"[ocr-phase] on the GPU: pid {entry['pid']}, user {entry['user']}, {entry['name'] or '?'}, "
            f"{mib} MiB{in_unit}; to free it: {command}", error=True)
        shown.append(f"{process_label(entry)}: {command}")
        named.append(dict({key: entry[key] for key in ("pid", "user", "name", "mib", "unit")}, free=command))
    more = f" and {len(shown) - 2} more" if len(shown) > 2 else ""
    return "held by " + "; ".join(shown[:2]) + more, named


def answers(url: str, timeout: float) -> bool:
    """One GET of URL: True when it answers 2xx within TIMEOUT seconds."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def model_state(health_url: str | None) -> tuple[bool | None, str]:
    """Whether the configured model is up before this call, and how that was
    seen: True when something listens on the health URL's host:port (a
    model that serves, or one still loading), or one of the GPU's compute
    processes is the model's (it listens there, descends from what does, or
    names the port as --port: a server that loads before it listens); False
    when none of these; None without OCR_PHASE_HEALTH_URL, which leaves
    nothing to tell it by.

    The port is only connected to and closed again, by this side first; no
    HTTP request is sent.  A GET that the server answers and then closes
    leaves the server's side of the connection in TIME_WAIT for a minute,
    and a server that binds without SO_REUSEADDR (measured: ninfer) thereby
    keeps the stand-in from binding the freed port after the release, so
    idle clients were refused instead of told to wait."""
    if not health_url:
        return None, "no OCR_PHASE_HEALTH_URL, so whether it is up cannot be told"
    parts = urllib.parse.urlsplit(health_url)
    where = f"{parts.hostname}:{parts.port or (443 if parts.scheme == 'https' else 80)}"
    module = tenant_clients()
    if module is not None and hasattr(module, "listening"):
        with contextlib.suppress(Exception):
            if module.listening(health_url):
                return True, f"something listens at {where} (up, or a model still loading)"
    processes, _ = compute_processes()
    if processes and module is not None and hasattr(module, "model_processes"):
        with contextlib.suppress(Exception):
            pids = module.model_processes(health_url, [entry["pid"] for entry in processes])
            if pids:
                named = ", ".join(process_label(entry) for entry in processes if entry["pid"] in pids)
                return True, f"its process is on the GPU: {named}"
    return False, f"nothing answers or listens at {where}"


def decide_swap(swap_mode: str, need: int, free: int | None, source: str,
                model_up: bool | None) -> tuple[bool, str]:
    """(stop the model for this call, why).  A model that is not up is never
    stopped, so never started again either; one that is up (or whose state
    cannot be told) is stopped as before: under always, or when free memory
    is short or cannot be read."""
    if swap_mode == "never":
        return False, "OCR_PHASE_SWAP=never"
    if model_up is False:
        room = "" if free is None else f"; free {free} MiB ({source})"
        return False, f"the model is not up, so it is neither stopped nor started{room}"
    if swap_mode == "always":
        return True, "a swap was asked for"
    if need <= 0:
        return False, "the engines use no GPU memory"
    if free is None:
        return True, f"free memory cannot be read ({source}), so it counts as too little"
    return free < need, f"free {free} MiB ({source})"


def hunyuan_mib(slots: int, ctx: int) -> int:
    return HUNYUAN_BASE_MIB + math.ceil(slots * ctx * HUNYUAN_KV_BYTES_PER_TOKEN / (1024 * 1024))


def _engine_options(arguments: list[str]) -> argparse.Namespace:
    try:
        slots = int(os.environ.get("OCR_HUNYUAN_SLOTS") or 1)
    except ValueError:
        slots = 1
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--server-slots", type=int, default=slots)
    parser.add_argument("--ctx-size", type=int, default=HUNYUAN_DEFAULT_CTX)
    parser.add_argument("--device", default=None)
    parser.add_argument("--endpoint", default=os.environ.get("HUNYUAN_ENDPOINT") or None)
    parser.add_argument("--single-engine", action="store_true")
    parser.add_argument("--engine", default=None)
    parser.add_argument("--vl-backend", default=os.environ.get("OCR_PADDLE_VL_BACKEND", "native"))
    parser.add_argument("--workers", default=os.environ.get(_paddle_pool.WORKERS_ENV, "auto"))
    try:
        known, _ = parser.parse_known_args(arguments)
    except SystemExit:  # a value it cannot read: the runner itself will say so
        known = parser.parse_known_args([])[0]
    return known


def _hunyuan_need(options: argparse.Namespace) -> tuple[int, str]:
    device = options.device or os.environ.get("OCR_HUNYUAN_DEVICE") or "auto"
    if options.endpoint:
        return 0, "Hunyuan on an external endpoint"
    if device == "cpu" and sys.platform != "darwin":
        return 0, "Hunyuan on the CPU"
    slots, ctx = max(1, options.server_slots), max(1, options.ctx_size)
    return hunyuan_mib(slots, ctx), f"Hunyuan {slots} slot(s) x {ctx} context"


def _paddle_need(options: argparse.Namespace) -> tuple[int, str]:
    device = (options.device or os.environ.get("OCR_PADDLE_DEVICE") or "").lower()
    if options.vl_backend == "mlx-vlm-server":
        return PADDLE_MIB, "Paddle layout and MLX VL server (provisional combined peak)"
    if device.startswith("cpu") and sys.platform != "darwin":
        return 0, "Paddle on the CPU"
    # Several workers asked for by number need a share each; auto needs one worker's memory
    # and takes a second only where the room is (_paddle_pool.need_mib).
    need = _paddle_pool.need_mib(options.workers, PADDLE_MIB)
    return need, "Paddle" if need == PADDLE_MIB else f"Paddle, {need // _paddle_pool.WORKER_SHARE_MIB} workers"


def engine_need(runner: list[str], *, _plan_depth: int = 0) -> tuple[int, str]:
    """The GPU memory (MiB) the engines of RUNNER need: the largest of them,
    since one call runs its engines one after another, and what it is."""
    if any(arg == "--engines" or arg.startswith("--engines=") for arg in runner):
        raise PhaseError("--engines was removed; use --engine apple-vision or --engine hunyuan-paddle")
    for index, argument in enumerate(runner):
        name = os.path.basename(argument)
        options = _engine_options(runner[index + 1:])
        if name == "_setup_ocr.py" and "--run-smoke-plan" in runner[index + 1:]:
            if _plan_depth:
                raise PhaseError("Nested setup smoke plans are not supported")
            try:
                marker = runner.index("--run-smoke-plan", index + 1)
                commands = json.loads(Path(runner[marker + 1]).read_text())["commands"]
                if not isinstance(commands, list) or not commands or any(
                        not isinstance(command, list) or not command or
                        any(not isinstance(word, str) for word in command) for command in commands):
                    raise ValueError("expected nonempty command lists")
                needs = [engine_need(command, _plan_depth=1) for command in commands]
            except (OSError, ValueError, IndexError, KeyError, TypeError) as exc:
                raise PhaseError(f"Cannot read setup smoke plan: {exc}") from exc
            return max(need for need, _ in needs), "setup tests: " + "; ".join(note for _, note in needs)
        if name in HUNYUAN_SCRIPTS:
            return _hunyuan_need(options)
        if name in PADDLE_SCRIPTS:
            return _paddle_need(options)
        if name == "run_apple_vision_ocr.py":
            return 0, "Apple Vision (system-managed memory)"
        if name in BOOK_SCRIPTS:
            import ocr_book
            from _ocr_engines import resolve_engines
            engine_options = argparse.Namespace(**dict(vars(options), device=None))
            try:
                book_options, _ = ocr_book.build_parser().parse_known_args(runner[index + 1:])
                engines = resolve_engines(options.engine, single_engine=options.single_engine,
                                           book=book_options.book.expanduser())
            except SystemExit as exc:
                raise PhaseError("Cannot read ocr_book.py arguments for its memory estimate") from exc
            except ValueError as exc:
                raise PhaseError(str(exc)) from exc
            needs = []
            if "hunyuan" in engines:
                needs.append(_hunyuan_need(engine_options))
            if "paddle" in engines:
                needs.append(_paddle_need(engine_options))
            if "vision" in engines:
                needs.append((0, "Apple Vision (system-managed memory)"))
            mib = max(need for need, _ in needs)
            return mib, " and ".join(note for _, note in needs) + f" (the larger counts)"
    return DEFAULT_REQUIRED_MIB, "an unknown runner (Paddle's need)"


# --- commands, health ---------------------------------------------------------------

def run_command(command: str, *, label: str, timeout: float | None = None) -> None:
    """Run a configured shell command in its own session; past TIMEOUT its
    whole process group is killed and that counts as a failure."""
    say(f"[ocr-phase] {label}: {command}")
    process = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            code = process.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            pass
        if deadline is not None and time.monotonic() >= deadline:
            signal_group(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            raise PhaseError(f"{label} did not finish within {timeout:.0f}s")
    if code != 0:
        raise PhaseError(f"{label} failed with exit code {code}")


def wait_for_health(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        left = deadline - time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=max(0.5, min(5.0, left))) as response:
                if 200 <= response.status < 300:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def ready_probe(value: str | None, endpoint: str | None) -> str | None:
    """OCR_PHASE_READY_PROBE as the chat completions URL to ask, or None (off).

    Off unless set: 1 (on, yes, true) asks PROOFREAD_ENDPOINT; an http(s) URL
    asks that URL.  Anything else is a configuration error."""
    text = (value or "").strip()
    if text.lower() in ("", "0", "off", "no", "false"):
        return None
    if text.lower() in ("1", "on", "yes", "true"):
        if not (endpoint or "").strip():
            raise PhaseError("OCR_PHASE_READY_PROBE=1 asks PROOFREAD_ENDPOINT, which is not set: set it, "
                             "or give the chat completions URL itself")
        text = endpoint.strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PhaseError(f"OCR_PHASE_READY_PROBE must be 0, 1 or an http(s) chat completions URL, not {value!r}")
    return text


def wait_until_ready(url: str, model: str | None, timeout_seconds: float) -> bool:
    """Ask URL for a 1-token chat completion until one answers 2xx or
    TIMEOUT_SECONDS pass: a server whose health URL answers while its model
    is still loading is not back yet."""
    body = {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1, "temperature": 0,
            "stream": False}
    if model:
        body["model"] = model
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("PROOFREAD_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode("utf-8")
    deadline = time.monotonic() + timeout_seconds
    while True:
        left = deadline - time.monotonic()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=max(0.5, min(READY_REQUEST_TIMEOUT, left))) as response:
                if 200 <= response.status < 300:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def bring_back(restore_cmd: str, health_url: str | None, health_timeout: float,
               *, label: str = "restore", clock: Clock | None = None,
               ready: tuple[str, str | None] | None = None) -> tuple[bool, float]:
    """Run the restore command and wait for health (and, with READY, a 1-token
    chat completion at READY's URL, within the same health timeout); (server
    back, seconds taken)."""
    clock = clock or Clock(None)
    began = time.monotonic()
    try:
        run_command(restore_cmd, label=label, timeout=clock.cap(COMMAND_TIMEOUT))
    except PhaseError as error:
        say(f"ERROR {error}", error=True)
        return False, time.monotonic() - began
    limit = clock.cap(health_timeout)
    deadline = time.monotonic() + limit
    if health_url:
        if not wait_for_health(health_url, limit):
            say(f"ERROR server did not become healthy at {health_url} within {limit:.0f}s",
                error=True)
            return False, time.monotonic() - began
        say(f"[ocr-phase] health OK: {health_url}")
    if ready is not None:
        url, model = ready
        if not wait_until_ready(url, model, max(0.0, deadline - time.monotonic())):
            say(f"ERROR the model did not answer a 1-token chat completion at {url} within {limit:.0f}s "
                "of the restore (OCR_PHASE_READY_PROBE)", error=True)
            return False, time.monotonic() - began
        say(f"[ocr-phase] ready OK: a 1-token chat completion at {url}")
    return True, time.monotonic() - began


def stays_up(url: str, seconds: float) -> bool:
    """True unless health fails twice in a row within the window."""
    deadline = time.monotonic() + seconds
    misses = 0
    while time.monotonic() < deadline:
        misses = 0 if wait_for_health(url, 0) else misses + 1
        if misses >= 2:
            return False
        time.sleep(1.0)
    return True


# --- processes ----------------------------------------------------------------------

def parent_of(pid: int) -> int | None:
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                return int(handle.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            return None
    try:
        result = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                                text=True, timeout=10, check=False)
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def ancestor_pids() -> set[int]:
    """This process and its ancestors: the shell and the agent that run this
    call are not 'other clients' of the model."""
    pids = {os.getpid()}
    pid = os.getppid()
    for _ in range(64):
        if pid is None or pid <= 1 or pid in pids:
            break
        pids.add(pid)
        pid = parent_of(pid)
    return pids


def group_members(pgid: int) -> set[int]:
    """The live processes of process group PGID."""
    members: set[int] = set()
    if sys.platform.startswith("linux"):
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                with open(f"/proc/{entry.name}/stat", encoding="utf-8") as handle:
                    fields = handle.read().rsplit(")", 1)[1].split()
            except (OSError, IndexError):
                continue
            if len(fields) > 2 and fields[2] == str(pgid) and fields[0] != "Z":
                members.add(int(entry.name))
        return members
    try:
        result = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat="], capture_output=True,
                                text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return members
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == str(pgid) and not parts[2].startswith("Z"):
            members.add(int(parts[0]))
    return members


def signal_group(pgid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


def stop_runner(process: subprocess.Popen | None, grace: float) -> None:
    """SIGTERM to the runner's group (the runners turn it into a clean exit that
    closes their GPU child), then SIGKILL to what is left of it.  Polls, so a
    signal handler never interrupts a blocking wait."""
    if process is None:
        return
    if process.poll() is None:
        signal_group(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            signal_group(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)


def sealed_pages(progress_file: str | os.PathLike | None) -> int:
    if not progress_file:
        return 0
    try:
        with open(progress_file, encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def window_residents(children: Path | None, private_root: Path | None,
                     runner_pgid: int | None) -> list[str]:
    """What of the window may still hold an OCR model: the runner's process
    group, the detached processes the runners named, and every lease in the
    window's private queue (GPU children inherit theirs)."""
    left = []
    if runner_pgid:
        members = group_members(runner_pgid)
        if members:
            left.append(f"runner group {runner_pgid} ({len(members)} process(es))")
    for kind, pid, started in read_children(children):
        if process_start_time(pid) == started:
            left.append(f"{kind} {pid}")
    if private_root is not None:
        tickets = live_ticket_count(private_root)
        if tickets:
            left.append(f"{tickets} GPU lease(s) in the window's queue")
    return left


def kill_window(children: Path | None, runner_pgid: int | None, sig: int) -> None:
    if runner_pgid and group_members(runner_pgid):
        signal_group(runner_pgid, sig)
    for kind, pid, started in read_children(children):
        if process_start_time(pid) != started:
            continue
        try:
            # Brokers and owned supervisors lead dedicated process groups.
            # Their model children inherit the queue lease and may ignore TERM;
            # the final KILL must include the whole owned group before restore.
            if kind in {"broker", "mlx-owned"} and os.getpgid(pid) == pid:
                signal_group(pid, sig)
            else:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def clear_window(children: Path | None, private_root: Path | None, runner_pgid: int | None,
                 clock: Clock, grace: float, settle: float = KILL_SETTLE) -> list[str]:
    """Wait for the window's OCR processes to leave, killing them after GRACE;
    what is still there at the end (empty: the OCR model is gone)."""
    def wait(seconds: float) -> list[str]:
        deadline = time.monotonic() + clock.cap(seconds)
        while True:
            left = window_residents(children, private_root, runner_pgid)
            if not left or time.monotonic() >= deadline:
                return left
            time.sleep(0.2)

    left = wait(grace)
    if not left:
        return left
    say(f"[ocr-phase] still there after {grace:.0f}s: {'; '.join(left)}; stopping them")
    kill_window(children, runner_pgid, signal.SIGTERM)
    left = wait(settle / 2)
    if left:
        kill_window(children, runner_pgid, signal.SIGKILL)
        left = wait(settle / 2)
    return left


def wait_for_room(mib: int | None, seconds: float) -> int | None:
    """Poll free memory until it reaches MIB or SECONDS pass; the last reading."""
    deadline = time.monotonic() + seconds
    while True:
        free, _ = free_memory_mib()
        if free is None or mib is None or free >= mib or time.monotonic() >= deadline:
            return free
        time.sleep(0.5)


# --- the state folder ---------------------------------------------------------------

def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not os.path.isdir(path) or info.st_uid != os.getuid():
        raise PhaseError(f"hand-over state folder must be a folder of this user: {path}")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)


def write_json(path: Path, value: dict) -> None:
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", delete=False,
                                         encoding="utf-8")
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(handle.name)


def write_status(state: Path, status: str, message: str, **fields) -> None:
    """status.json: where the agent's server stands, for whoever looks."""
    with contextlib.suppress(OSError, PhaseError):
        private_directory(state)
        write_json(state / "status.json", dict(fields, state=status, message=message, pid=os.getpid(),
                                               updatedAt=time.strftime("%Y-%m-%dT%H:%M:%S%z")))


def read_status(state: Path) -> dict:
    try:
        with open(state / "status.json", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


# status.json states that leave the server down, or may: a hand-over under
# way, a restore that failed, a watchdog retrying or one that gave up.  Only a
# call that stopped the server writes one: a call that found it down writes
# "left-down", and no later call starts a server the person stopped.
DOWN_STATES = frozenset({"releasing", "ocr-running", "restoring", "restore-failed",
                         "watchdog-restoring", "gave-up"})


# --- what a call did with the GPU (arguments.gpu) -----------------------------------

def record_gpu(arguments: argparse.Namespace, **fields) -> None:
    """Add FIELDS to the call's GPU record: modelUp (True, False, None: cannot
    tell) and how it was seen, released (the call stopped the model),
    restored (it started it again), heldBy (what held the GPU when too
    little was free, with how to free it)."""
    record = getattr(arguments, "gpu", None)
    if record is None:
        record = arguments.gpu = {}
    record.update(fields)


def gpu_fields(arguments: argparse.Namespace) -> dict:
    """What status.json and timings.json keep of the call's GPU record."""
    record = getattr(arguments, "gpu", None) or {}
    return {key: record[key] for key in ("modelUp", "released", "restored", "heldBy") if key in record}


def gpu_note(record: dict | None) -> str:
    """The RESULT line's short account: was the model up, was it stopped and
    started again."""
    if not record:
        return ""
    if record.get("mode") == "no-tenant":
        return "gpu: no local model configured; nothing stopped"
    if record.get("mode") == "never":
        return "gpu: OCR_PHASE_SWAP=never; nothing stopped"
    if record.get("leftDownByEarlier"):
        return "gpu: model left down by an earlier hand-over, NOT started again"
    up = record.get("modelUp")
    text = ("model up" if up else "model not up" if up is False
            else "model state unknown (no OCR_PHASE_HEALTH_URL)")
    if record.get("released"):
        text += ", stopped and started again" if record.get("restored") else ", stopped and NOT started again"
    elif up is False:
        text += ", left as it was"
    else:
        text += ", not stopped"
    return "gpu: " + text


def record_window(state: Path, arguments: argparse.Namespace, code: int) -> None:
    """After a window: timings.json keeps its GPU record (lastWindow), and a
    window that found the model not up says so in status.json - "left-down",
    outside DOWN_STATES."""
    record = getattr(arguments, "gpu", None) or {}
    if record.get("mode") not in ("auto", "always"):
        return
    fields = gpu_fields(arguments)
    if record.get("modelUp") is False and not record.get("released"):
        write_status(state, "left-down", "the model was not up before this hand-over; it was neither "
                     "stopped nor started", exitCode=code, **fields)
    timings = read_timings(state)
    timings["lastWindow"] = dict(fields, exitCode=code, at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    with contextlib.suppress(OSError, PhaseError):
        private_directory(state)
        write_json(state / "timings.json", timings)


def recover_left_down(arguments: argparse.Namespace, state: Path, clock: Clock) -> int | str | None:
    """Before a call decides whether to stop the server: a server an earlier
    hand-over left down (status.json in DOWN_STATES) is restored first.

    Called with the window open: this call holds the window lock and the
    shared GPU lease, which a hand-over under way and a watchdog still
    retrying both hold, so a state in DOWN_STATES is one whose hand-over and
    watchdog are gone without a confirmed restore.  Free memory then looks
    plentiful, and the call would run its OCR with no swap and report DONE
    while the person's model stays down.

    None: nothing was left down (or the server answers its health check
    again).  "restored": restored here, so the swap must be decided again
    with the server resident.  3: still down; the restore command is named."""
    status = read_status(state)
    left = str(status.get("state") or "")
    if left not in DOWN_STATES:
        return None
    when = status.get("updatedAt") or "?"
    if arguments.health_url and wait_for_health(arguments.health_url, HEALTH_PROBE_SECONDS):
        say(f"[ocr-phase] status.json said {left!r} ({when}), but the server answers at "
            f"{arguments.health_url}: it was restored since")
        write_status(state, "restored", "the server was found up at the start of a later hand-over",
                     previous=left)
        return None
    say(f"[ocr-phase] WARNING the local model server was left down by an earlier hand-over "
        f"(status.json: {left} at {when}: {status.get('message') or '-'}); restoring it before "
        "anything else", error=True)
    if not arguments.restore_cmd:
        message = ("the local model server was left down by an earlier hand-over, and "
                   "OCR_PHASE_RESTORE_CMD is not set: restart it by hand")
        say(f"ERROR {message}", error=True)
        arguments.result_note = "status.json " + left
        return 3
    ok, seconds = bring_back(arguments.restore_cmd, arguments.health_url, arguments.health_timeout,
                             label="restore left over from an earlier hand-over", clock=clock,
                             ready=getattr(arguments, "ready", None))
    if not ok:
        message = (f"the local model server was left down by an earlier hand-over ({left}) and "
                   f"restoring it failed; restart it by hand: {arguments.restore_cmd}")
        say(f"ERROR {message}; no OCR was run", error=True)
        write_status(state, "restore-failed", message, previous=left, restoreCmd=arguments.restore_cmd)
        notify(arguments.notify_cmd, message)
        arguments.result_note = f"left down by an earlier hand-over; by hand: {arguments.restore_cmd}"
        return 3
    write_status(state, "restored", "the server was restored at the start of a later hand-over",
                 previous=left, restoreSeconds=round(seconds, 2))
    return "restored"


def read_timings(state: Path) -> dict:
    try:
        with open(state / "timings.json", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def probe_of(arguments: argparse.Namespace) -> str | None:
    """The OCR_PHASE_READY_PROBE URL a call restores with, or None (health only)."""
    ready = getattr(arguments, "ready", None)
    return ready[0] if ready else None


def restore_timings(state: Path, probe: str | None) -> dict:
    """timings.json, without its restore times when they were measured under
    another OCR_PHASE_READY_PROBE setting: a model that answers its health URL
    in seconds may need far longer before it answers a chat completion, so
    times from before the probe was turned on would keep the reserve too small
    for every probed restore."""
    timings = read_timings(state)
    if timings.get("restoreProbe") != probe:
        for key in RESTORE_TIMINGS:
            timings.pop(key, None)
    if probe is None:
        timings.pop("restoreProbe", None)
    else:
        timings["restoreProbe"] = probe
    return timings


def record_restore_time(state: Path, seconds: float, *, budgeted: bool = True, probe: str | None = None) -> None:
    """Keep how long a restore took (restoreSeconds, the last five), under the
    ready probe PROBE (restore_timings).  An unbudgeted call's restore runs
    with no clock over it, so a slow one (a cold start after a reboot) is an
    outlier for the budgeted calls: it is counted only when it does not raise
    the estimate, and is kept apart otherwise.  A budgeted restore the call's
    end cut short is kept the same way, with the time it had: the restore
    needs at least that, and the next estimate keeps more."""
    timings = restore_timings(state, probe)
    measured = [value for value in timings.get("restoreSeconds", []) if isinstance(value, (int, float))]
    if budgeted or seconds <= _restore_estimate(measured):
        timings["restoreSeconds"] = (measured + [round(seconds, 2)])[-5:]
    else:
        aside = [value for value in timings.get("unbudgetedRestoreSeconds", []) if isinstance(value, (int, float))]
        timings["unbudgetedRestoreSeconds"] = (aside + [round(seconds, 2)])[-5:]
    with contextlib.suppress(OSError, PhaseError):
        private_directory(state)
        write_json(state / "timings.json", timings)


def record_release_time(state: Path, seconds: float) -> None:
    """Keep how long the release took, up to the memory showing up as free."""
    timings = read_timings(state)
    measured = [value for value in timings.get("releaseSeconds", []) if isinstance(value, (int, float))]
    timings["releaseSeconds"] = (measured + [round(seconds, 2)])[-5:]
    with contextlib.suppress(OSError, PhaseError):
        private_directory(state)
        write_json(state / "timings.json", timings)


def release_estimate(state: Path) -> float:
    """Seconds to keep for the release and its settle: half again the slowest
    of the last five measured, plus 5 s."""
    measured = [value for value in read_timings(state).get("releaseSeconds", [])
                if isinstance(value, (int, float)) and value >= 0]
    if not measured:
        return DEFAULT_RELEASE_ESTIMATE
    return max(measured) * 1.5 + 5.0


def startup_need(runner: list[str]) -> tuple[float, str]:
    """Seconds the first engine of RUNNER needs to load and seal its first page:
    the book's measurement (ocr_book.py's BOOK/reports/ocr-times.json, or the
    one beside a runner's --report), else ocr_book.py's default; for
    ocr_book.py the slowest of its engines, since any of them may be the first
    that loads in a call.  0 for a runner this script does not know."""
    try:
        import ocr_book  # noqa: PLC0415
    except ImportError:
        return 0.0, "no startup estimate"
    engines = {"run_hunyuanocr.py": "hunyuan", "run_paddleocr_vl.py": "paddle", "run_apple_vision_ocr.py": "vision",
               "run_hunyuanocr_second_look.py": "second-look"}
    for index, argument in enumerate(runner):
        name = os.path.basename(argument)
        rest = runner[index + 1:]
        if name in BOOK_SCRIPTS:
            try:
                options, _ = ocr_book.build_parser().parse_known_args(rest)
            except SystemExit:
                return 0.0, "no startup estimate"
            times = ocr_book.read_json(Path(options.book).expanduser() / options.reports / ocr_book.TIMES_NAME)
            from _ocr_engines import resolve_engines
            try:
                names = list(resolve_engines(options.engine, single_engine=options.single_engine,
                                             book=Path(options.book).expanduser()))
            except ValueError as exc:
                raise PhaseError(str(exc)) from exc
            if len(names) > 1:
                names.append("second-look")
        elif name in engines:
            parser = argparse.ArgumentParser(add_help=False)
            parser.add_argument("--report", type=Path)
            try:
                known, _ = parser.parse_known_args(rest)
            except SystemExit:
                known = argparse.Namespace(report=None)
            times = ocr_book.read_json(known.report.parent / ocr_book.TIMES_NAME) if known.report else {}
            names = [engines[name]]
        else:
            continue
        seconds, engine = max((ocr_book.startup_seconds(times, engine), engine) for engine in names)
        return seconds, f"{engine}'s load and longest page"
    return 0.0, "no startup estimate"


def window_floor(arguments: argparse.Namespace, runner: list[str], state: Path,
                 reserve: float) -> tuple[float, str]:
    """The OCR time a window must still have when the server is stopped: the
    release and its settle, then the first engine's load and longest page
    (at least MIN_RUN_SECONDS).  A budget too small for that even without any
    wait keeps MIN_RUN_SECONDS: its runner then says the budget is too small."""
    startup, what = startup_need(runner)
    release = release_estimate(state)
    floor = max(MIN_RUN_SECONDS, release + startup)
    note = f"{release:.0f}s release + {startup:.0f}s {what}"
    if arguments.max_seconds and arguments.max_seconds - reserve < floor:
        return MIN_RUN_SECONDS, note + ", more than the whole budget leaves"
    return floor, note


def _restore_estimate(measured: list[float]) -> float:
    measured = [value for value in measured if value >= 0]
    if not measured:
        return DEFAULT_RESTORE_ESTIMATE
    return min(RESTORE_ESTIMATE_CAP, max(15.0, max(measured) * 1.5 + 5.0))


def restore_estimate(state: Path, budget: float | None = None, probe: str | None = None) -> float:
    """Seconds to keep for the restore and its health check: half again the
    slowest of the last five measured (under the ready probe PROBE), plus 5 s,
    at least 15 s, at most RESTORE_ESTIMATE_CAP and, for a call with BUDGET,
    RESTORE_BUDGET_SHARE of it (DEFAULT_RESTORE_ESTIMATE before anything was
    measured)."""
    measured = [value for value in restore_timings(state, probe).get("restoreSeconds", [])
                if isinstance(value, (int, float))]
    estimate = _restore_estimate(measured)
    if budget and any(value >= 0 for value in measured):
        estimate = min(estimate, budget * RESTORE_BUDGET_SHARE)
    return estimate


def notify(command: str | None, message: str) -> None:
    """Tell a person (OCR_PHASE_NOTIFY_CMD, with the text in OCR_PHASE_NOTICE)."""
    if not command:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(command, shell=True, env=dict(os.environ, OCR_PHASE_NOTICE=message),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, start_new_session=True)


# --- other clients and the stand-in (scripts/_tenant_clients.py) ---------------------

def tenant_clients():
    """scripts/_tenant_clients.py, or None where it is not installed."""
    try:
        import _tenant_clients  # noqa: PLC0415
    except ImportError:
        return None
    return _tenant_clients


# What says the calling agent is the local model (scripts/proofread_pages.py's agent_is_local_model
# reads the same two; that module is too large to load here).
LOCAL_AGENT_ENV = "JYUT_OCR_AGENT_IS_LOCAL_MODEL"
QWEN_MODEL_ENV = "QWEN_CODE_MODEL"
SERVED_MODEL_ENV = "PROOFREAD_MODEL"


def agent_on_model(health_url: str | None, environ: dict[str, str] | None = None) -> str | None:
    """Why the agent that runs this call cannot be without the local model, or None when
    nothing says so (--background).

    Three signs, any one enough: the agent said it is the local model (LOCAL_AGENT_ENV=1,
    which SKILL.md has such an agent export in every call); it is Qwen Code running the
    model proofreading is served by; or one of this call's ancestors - its shell, the agent
    - holds a connection to the server at HEALTH_URL.  LOCAL_AGENT_ENV=0 answers the second
    sign only: a connection is evidence, whatever was said."""
    env = os.environ if environ is None else environ
    said = (env.get(LOCAL_AGENT_ENV) or "").strip()
    if said == "1":
        return f"{LOCAL_AGENT_ENV}=1 says the agent is the local model"
    qwen, served = (env.get(QWEN_MODEL_ENV) or "").strip(), (env.get(SERVED_MODEL_ENV) or "").strip()
    if said != "0" and qwen and qwen == served:
        return f"the agent is Qwen Code on the model that proofreads ({QWEN_MODEL_ENV} is {SERVED_MODEL_ENV})"
    module = tenant_clients() if health_url else None
    if module is not None:
        try:
            ours = ancestor_pids() - {os.getpid()}
            holding = [client for client in module.find_other_clients(health_url, exclude_pids=set(), match=[])
                       if client["reason"] == "socket" and client["pid"] in ours]
        except Exception:  # noqa: BLE001 - the sockets cannot be read here: the other two signs stand
            holding = []
        if holding:
            return (f"a process that runs this call (pid {holding[0]['pid']}: {holding[0]['cmd']}) "
                    f"holds a connection to the model server")
    return None


def client_match(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def wait_for_other_clients(arguments: argparse.Namespace, clock: Clock, reserve: float,
                           floor: tuple[float, str] = (MIN_RUN_SECONDS, "")) -> int | None:
    """None when the server may be stopped now; EXIT_GPU_BUSY when its other
    clients still have requests on the wire and the budget allows no more
    waiting, or the wait left the window less than FLOOR (the OCR time it
    needs after the stop); nothing has been stopped then."""
    if not arguments.health_url:
        say("[ocr-phase] WARNING no OCR_PHASE_HEALTH_URL: the model's other clients are not "
            "looked for, and no stand-in answers them", error=True)
        return None
    module = tenant_clients()
    if module is None:
        say("[ocr-phase] WARNING scripts/_tenant_clients.py is missing: the model's other "
            "clients are not looked for", error=True)
        return None
    exclude = ancestor_pids()
    match = client_match(arguments.client_match)
    ignore = client_match(arguments.client_ignore)
    try:
        clients = module.find_other_clients(arguments.health_url, exclude_pids=exclude, match=match,
                                            ignore=ignore)
    except Exception as error:  # noqa: BLE001 - a broken probe must not stop the OCR
        say(f"[ocr-phase] WARNING the model's other clients could not be looked for: {error}",
            error=True)
        return None
    busy = [client for client in clients if client.get("reason") == "socket"]
    for client in clients:
        reason = ("ignored: OCR_PHASE_CLIENT_IGNORE" if client.get("reason") == "ignored"
                  else client.get("reason"))
        say(f"[ocr-phase] another client of the model: pid {client.get('pid')} "
            f"({reason}) {str(client.get('cmd', ''))[:120]}")
    if len(clients) > len(busy):
        say(f"[ocr-phase] {len(clients) - len(busy)} idle or ignored client(s): the stand-in answers "
            "them 503 + Retry-After while the server is down")
    if not busy:
        return None
    left = clock.left()
    needed, needed_note = floor
    waiting = UNBUDGETED_CLIENT_WAIT if left is None else left - reserve - needed
    if waiting <= 0:
        say(f"ERROR {len(busy)} other client(s) are using the model and the budget leaves no "
            "time to wait for them; nothing was stopped", error=True)
        blocked_by(arguments, busy)
        return EXIT_GPU_BUSY
    say(f"[ocr-phase] waiting up to {waiting:.0f}s for {len(busy)} other client(s) to finish")
    latest = [busy]
    try:
        idle = module.wait_until_idle(arguments.health_url, exclude_pids=exclude, match=match,
                                      poll_s=2.0, timeout_s=waiting, on_wait=latest.append, ignore=ignore)
    except Exception as error:  # noqa: BLE001
        say(f"[ocr-phase] WARNING waiting for the other clients failed: {error}", error=True)
        idle = False
    if not idle:
        say("ERROR the model's other clients are still using it; nothing was stopped", error=True)
        blocked_by(arguments, latest[-1])
        return EXIT_GPU_BUSY
    left = clock.left()
    if left is not None and left - reserve < needed:
        # The OCR engine would load with less time than it needs and seal
        # nothing; a later call gets a whole window.
        say(f"ERROR the other clients finished, but the wait left {max(0.0, left - reserve):.0f}s of OCR "
            f"time, less than the {needed:.0f}s a window needs ({needed_note}); nothing was stopped",
            error=True)
        arguments.result_note = "the wait for the model's other clients used the time a window needs"
        return EXIT_GPU_BUSY
    return None


def blocked_by(arguments: argparse.Namespace, busy: list[dict]) -> None:
    """Name the clients that kept the server busy, and the setting that lets a
    client which never closes its connection through (for the RESULT line)."""
    named = ", ".join(f"pid {client.get('pid')} ({' '.join(str(client.get('cmd', '')).split()[:3])[:60]})"
                      for client in busy[:3])
    more = f" and {len(busy) - 3} more" if len(busy) > 3 else ""
    say(f"[ocr-phase] still connected: {named}{more}. A client that keeps a pooled connection open "
        "whether or not it has a request (a reverse proxy, an LLM gateway, a web UI) never lets go: "
        "add a piece of its command line, or uid:N for pid -1, to OCR_PHASE_CLIENT_IGNORE", error=True)
    arguments.result_note = (f"blocked by {named}{more}; if that client only keeps a pooled connection "
                             "open (a proxy, a gateway, a web UI), name it in OCR_PHASE_CLIENT_IGNORE")


def retry_after(clock: Clock) -> int:
    """Retry-After for the stand-in: about a sixth of the window, so a client
    that retries six times (Qwen Code) outlasts it."""
    left = clock.left()
    return 60 if left is None else int(max(10, min(300, math.ceil(left / 6))))


def open_stand_in(arguments: argparse.Namespace, clock: Clock, keep=None):
    """The 503 stand-in on the freed port, or None.  KEEP is given each stand-in
    before it binds, with signals held until it is bound, so a signal at any
    point leaves the caller holding whatever it has to close."""
    if not arguments.health_url:
        return None
    module = tenant_clients()
    if module is None or not hasattr(module, "StandIn"):
        return None
    parts = urllib.parse.urlsplit(arguments.health_url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    seconds = retry_after(clock)
    # A release command may return before the server has let go of its port
    # (a stop that only signals it): the bind is retried while the port is busy.
    busy = getattr(module, "PortBusy", None)
    until = time.monotonic() + clock.cap(STAND_IN_BIND_WAIT)
    while True:
        try:
            with signals_held():
                stand_in = module.StandIn(parts.hostname or "127.0.0.1", port, retry_after_s=seconds)
                if keep is not None:
                    keep(stand_in)
                stand_in.__enter__()
            break
        except Exception as error:  # noqa: BLE001 - the window works without it
            if isinstance(busy, type) and isinstance(error, busy) and time.monotonic() < until:
                time.sleep(0.2)
                continue
            say(f"[ocr-phase] WARNING no stand-in on port {port}: {error}", error=True)
            if keep is not None:
                keep(None)  # it closed itself when it could not start
            return None
    say(f"[ocr-phase] stand-in on port {port}: 503 + Retry-After {seconds}s until the restore")
    return stand_in


def close_stand_in(stand_in) -> None:
    if stand_in is not None:
        with contextlib.suppress(Exception):
            stand_in.__exit__(None, None, None)


# --- the window ---------------------------------------------------------------------

class Window:
    """One hand-over: the window lock, the shared GPU lease, a private queue."""

    def __init__(self, state: Path, resource: str):
        self.state = state
        self.id = uuid.uuid4().hex
        self.directory = state / "windows" / self.id
        self.private_root = state / "private-queue"
        self.progress = self.directory / "progress"
        self.children = self.directory / "children"
        self.lease = GPUQueue(None, resource)
        self.lock_fd: int | None = None

    def open(self, busy_wait: float) -> str | None:
        """Open the window; None, or why it cannot be opened now."""
        private_directory(self.state)
        fd = os.open(self.state / "window.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + max(busy_wait, 0.5)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return "another OCR hand-over is running on this machine"
                time.sleep(0.05)
        self.lock_fd = fd
        try:
            self.lease.acquire(timeout=busy_wait, local_model_guard=False)
        except GPUQueueTimeout:
            self._unlock()
            return "another job holds the GPU queue"
        except GPUQueueError as error:
            self._unlock()
            raise PhaseError(str(error)) from error
        try:
            earlier = live_ticket_count(self.private_root)
            if earlier:
                self.lease.close()
                self._unlock()
                return (f"an OCR process of an earlier hand-over still holds the GPU "
                        f"({earlier} lease(s) in {self.private_root})")
            private_directory(self.state / "windows")
            self._sweep()
            private_directory(self.directory)
            self.progress.touch(mode=0o600)
            self.children.touch(mode=0o600)
            write_json(self.state / "window.json", {"id": self.id, "pid": os.getpid(),
                                                   "privateQueue": str(self.private_root),
                                                   "startedAt": time.time()})
        except BaseException:
            self.lease.close()
            self._unlock()
            raise
        return None

    def _sweep(self) -> None:
        """Remove the folders of windows that ended a day ago or more."""
        for path in (self.state / "windows").iterdir():
            with contextlib.suppress(OSError):
                if path.is_dir() and time.time() - path.stat().st_mtime > 24 * 3600:
                    shutil.rmtree(path, ignore_errors=True)

    def environment(self, env: dict[str, str]) -> None:
        env.update({WINDOW_ENV: self.id, "OCR_GPU_QUEUE_DIR": str(self.private_root),
                    PROGRESS_ENV: str(self.progress), CHILDREN_ENV: str(self.children)})

    def _unlock(self) -> None:
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def close(self, *, keep_files: bool = False) -> None:
        """Close the window.  KEEP_FILES leaves its folder for the watchdog."""
        with contextlib.suppress(OSError):
            with open(self.state / "window.json", encoding="utf-8") as handle:
                if json.load(handle).get("id") == self.id:
                    os.unlink(self.state / "window.json")
        if not keep_files:
            shutil.rmtree(self.directory, ignore_errors=True)
        self.lease.close()
        self._unlock()


# --- the watchdog -------------------------------------------------------------------

def open_log(path: Path):
    with contextlib.suppress(OSError):
        if path.stat().st_size > LOG_LIMIT:
            os.replace(path, path.with_name(path.name + ".1"))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")
    except OSError:
        return None


def watchdog_main(arguments: argparse.Namespace) -> int:
    """Restore the server if the phase process ends without having done so.

    The phase process holds the write end of a pipe and reports each step into
    it: "releasing" before it stops the server, "released", "runner PID START"
    once the OCR runner is going, "restoring" and finally "restored" (or
    "ocr-resident" / "restore-failed").  The pipe reaches end-of-file however
    that process ends - even SIGKILL - so a missing "restored" after
    "releasing" means nobody finished bringing the server back.  The watchdog
    runs in its own session, so a process-group kill aimed at the phase does
    not reach it.  It holds a copy of the window's shared GPU lease, so no
    other job takes the GPU before the server is back.
    """
    state = Path(arguments.watchdog_state)
    log = open_log(state / "watchdog.log")
    # The phase passes the probe's URL itself; a bad one means no probe here.
    try:
        url = ready_probe(arguments.ready_probe, None)
    except PhaseError:
        url = None
    arguments.ready = (url, arguments.ready_model) if url else None

    def note(message: str) -> None:
        if log is not None:
            with contextlib.suppress(OSError, ValueError):
                log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [pid {os.getpid()}] {message}\n")
                log.flush()

    received = b""
    while True:
        try:
            chunk = os.read(arguments.watchdog_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        received += chunk
    steps = received.decode("utf-8", "replace").split("\n")
    if "restored" in steps:
        return 0
    if "releasing" not in steps:
        return 0  # the server was never stopped
    note("phase process ended without restoring the server")
    sys.stdout = sys.stderr = log if log is not None else open(os.devnull, "w")
    window = Path(arguments.watchdog_window) if arguments.watchdog_window else None
    children = window / "children" if window else None
    private_root = Path(arguments.watchdog_queue) if arguments.watchdog_queue else None
    runner_pgid = None
    for step in steps:
        parts = step.split()
        if parts[:1] == ["runner"] and len(parts) == 3:
            pid, started = int(parts[1]), parts[2]
            # Only the runner we were told about: a reused pid has another start
            # time.  Its group stays ours while any member is left, since a
            # group's number is not handed out again before it is empty.
            if process_start_time(pid) == started.replace("_", " "):
                signal_group(pid, signal.SIGTERM)
                note(f"stopping the orphaned OCR runner group {pid}")
                runner_pgid = pid
            elif group_members(pid):
                runner_pgid = pid
    write_status(state, "watchdog-restoring", "the hand-over ended without restoring the server; "
                 "the watchdog restores it", restoreCmd=arguments.restore_cmd)
    clock = Clock(None)
    horizon = time.monotonic() + arguments.restore_horizon
    delay, attempt, notified, late_stops = 5.0, 0, False, 0
    ok = False
    while True:
        attempt += 1
        left = clear_window(children, private_root, runner_pgid, clock, WINDOW_GRACE)
        if runner_pgid and not group_members(runner_pgid):
            runner_pgid = None  # gone: its number may go to someone else now
        if left:
            note(f"an OCR model of the window may still be resident ({'; '.join(left)}); "
                 "not restoring yet")
        else:
            if arguments.tenant_mib:
                free = wait_for_room(arguments.tenant_mib, TENANT_WAIT)
                if free is not None and free < arguments.tenant_mib:
                    note(f"only {free} MiB free of the {arguments.tenant_mib} MiB the server needs; "
                         "no OCR process of the window is left, restoring")
            if (attempt == 1 and "restoring" in steps and "restore-failed" not in steps
                    and arguments.health_url and wait_for_health(arguments.health_url, RESTORE_GRACE_SECONDS)):
                # A restore was under way; a service manager finishes a start it
                # was already given, even when the client that asked was killed.
                note("the interrupted restore came up by itself")
                ok = True
            else:
                # Restore even if the server still answers: a stop that was under
                # way when the phase died completes later.  The restore command
                # must be safe to repeat.
                ok, _ = bring_back(arguments.restore_cmd, arguments.health_url,
                                   arguments.health_timeout, label="watchdog restore",
                                   ready=getattr(arguments, "ready", None))
                note("server restored" if ok else f"server was NOT restored (attempt {attempt})")
            if (ok and "released" not in steps and arguments.health_url and late_stops < 3
                    and not stays_up(arguments.health_url, RELEASE_SETTLE_SECONDS)):
                # Killed mid-release: the stop may still be finishing (a service
                # manager completes it without the client) and land after this start.
                late_stops += 1
                note("the server went down again after the restore (the interrupted stop "
                     "finished later); restoring again")
                ok = False
                continue
        if ok:
            break
        message = (f"the local model server is down after an OCR hand-over and the restore "
                   f"failed (attempt {attempt}); the watchdog keeps trying. By hand: "
                   f"{arguments.restore_cmd}")
        write_status(state, "restore-failed", message, attempt=attempt, restoreCmd=arguments.restore_cmd)
        if not notified:
            notify(arguments.notify_cmd, message)
            notified = True
        if time.monotonic() + delay > horizon:
            break
        time.sleep(delay)
        delay = min(delay * 2, 120.0)
    if ok:
        write_status(state, "restored", "the watchdog restored the server", attempt=attempt)
        if window is not None:
            shutil.rmtree(window, ignore_errors=True)
        if notified:
            notify(arguments.notify_cmd, f"the local model server is back (attempt {attempt})")
        return 0
    message = (f"the watchdog gave up restoring the local model server after {attempt} attempt(s); "
               f"restart it by hand: {arguments.restore_cmd}")
    note(message)
    write_status(state, "gave-up", message, attempt=attempt, restoreCmd=arguments.restore_cmd)
    notify(arguments.notify_cmd, message)
    return 3


def start_watchdog(arguments: argparse.Namespace, window: Window) -> int:
    """Start the detached watchdog; return the write end of its pipe."""
    read_fd, write_fd = os.pipe()
    command = [
        sys.executable, os.path.abspath(__file__),
        "--watchdog-fd", str(read_fd),
        "--watchdog-state", str(window.state),
        "--watchdog-window", str(window.directory),
        "--watchdog-queue", str(window.private_root),
        "--restore-cmd", arguments.restore_cmd,
        "--health-timeout", str(arguments.health_timeout),
        "--restore-horizon", str(arguments.restore_horizon),
    ]
    if arguments.health_url:
        command += ["--health-url", arguments.health_url]
    if arguments.tenant_mib:
        command += ["--tenant-mib", str(arguments.tenant_mib)]
    if arguments.notify_cmd:
        command += ["--notify-cmd", arguments.notify_cmd]
    # Always given, so the watchdog never takes another probe from the environment.
    url, model = getattr(arguments, "ready", None) or ("0", None)
    command += ["--ready-probe", url] + (["--ready-model", model] if model else [])
    try:
        subprocess.Popen(
            command,
            pass_fds=(read_fd, *window.lease.pass_fds),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        os.close(read_fd)
    return write_fd


# --- the runner ---------------------------------------------------------------------

def run_runner(runner: list[str], env: dict[str, str], hard_stop: float | None,
               holder: dict[str, subprocess.Popen], on_start=None, *, own_group: bool = False,
               pass_fds: tuple[int, ...] = (), kill_grace: float = KILL_GRACE) -> int:
    """Run the runner; past the hard stop it is stopped.

    The runner checks the deadline itself between pages and caps each page at
    it, so the hard stop only catches a runner that ignores the budget (or a
    model that takes longer to load than the whole budget).  The wait polls
    instead of blocking, so a signal handler never interrupts a blocking
    ``wait`` that holds Popen's internal lock (measured: that deadlocked the
    phase with the server down).  OWN_GROUP puts the runner in a process group
    of its own, so its whole tree can be stopped.
    """
    process = subprocess.Popen(runner, env=env, start_new_session=own_group, pass_fds=pass_fds)
    holder["runner"] = process
    if on_start is not None:
        on_start(process)
    while True:
        try:
            return process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        if hard_stop is not None and time.time() >= hard_stop:
            say("[ocr-phase] the runner is past the time budget; stopping it", error=True)
            if own_group:
                stop_runner(process, kill_grace)
            else:
                process.terminate()
                try:
                    process.wait(timeout=kill_grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            sealed = sealed_pages(env.get(PROGRESS_ENV, ""))
            if sealed:
                return EXIT_INCOMPLETE
            say("ERROR the time budget ran out before a single page was sealed; "
                "calling again would do the same. Read the runner's output above.",
                error=True)
            return 1


# --- the command line ---------------------------------------------------------------

def _float_env(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int_env(name: str) -> int | None:
    value = _float_env(name)
    return int(value) if value is not None else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--release-cmd",
        default=os.environ.get("OCR_PHASE_RELEASE_CMD"),
        help="shell command that frees GPU memory (env: OCR_PHASE_RELEASE_CMD)",
    )
    parser.add_argument(
        "--restore-cmd",
        default=os.environ.get("OCR_PHASE_RESTORE_CMD"),
        help="shell command that brings the server back; must be safe to repeat "
             "(env: OCR_PHASE_RESTORE_CMD)",
    )
    parser.add_argument(
        "--health-url",
        default=os.environ.get("OCR_PHASE_HEALTH_URL"),
        help="URL polled after restore; its host and port are the server's, where other "
             "clients are looked for and the stand-in answers (env: OCR_PHASE_HEALTH_URL)",
    )
    parser.add_argument(
        "--required-mib",
        type=int,
        default=_int_env("OCR_PHASE_REQUIRED_MIB"),
        help="free GPU memory the OCR engines need; default: from the engines of the "
             "runner and their settings (env: OCR_PHASE_REQUIRED_MIB)",
    )
    parser.add_argument(
        "--swap",
        choices=("auto", "always", "never"),
        default=(os.environ.get("OCR_PHASE_SWAP") or "auto").strip().lower(),
        help="auto: stop the server when free memory is short or cannot be read; always; "
             "never. A server that is not up before the call is never stopped or started "
             "(env: OCR_PHASE_SWAP)",
    )
    parser.add_argument(
        "--force-swap",
        action="store_true",
        help="the same as --swap always",
    )
    parser.add_argument(
        "--tenant-mib",
        type=int,
        default=_int_env("OCR_PHASE_TENANT_MIB"),
        help="free GPU memory the server needs to start again; waited for before the "
             "restore (env: OCR_PHASE_TENANT_MIB)",
    )
    parser.add_argument(
        "--client-match",
        default=os.environ.get("OCR_PHASE_CLIENT_MATCH"),
        help="comma list of command-line fragments that name the model's clients "
             "(env: OCR_PHASE_CLIENT_MATCH)",
    )
    parser.add_argument(
        "--client-ignore",
        default=os.environ.get("OCR_PHASE_CLIENT_IGNORE"),
        help="comma list of the model's clients never waited for: command-line fragments, "
             "or uid:N for a user's processes; for a proxy, gateway or web UI that keeps a "
             "pooled connection open (env: OCR_PHASE_CLIENT_IGNORE)",
    )
    parser.add_argument(
        "--notify-cmd",
        default=os.environ.get("OCR_PHASE_NOTIFY_CMD"),
        help="shell command run when the server cannot be restored, with the text in "
             "OCR_PHASE_NOTICE (env: OCR_PHASE_NOTIFY_CMD)",
    )
    parser.add_argument(
        "--ready-probe",
        default=os.environ.get("OCR_PHASE_READY_PROBE"),
        help="after the health check, also wait for a 1-token chat completion before the server "
             "counts as restored, for a server whose health URL answers before its model can serve: "
             "1 asks PROOFREAD_ENDPOINT (with PROOFREAD_MODEL), or give the chat completions URL; "
             "off by default (env: OCR_PHASE_READY_PROBE)",
    )
    parser.add_argument("--ready-model", default=os.environ.get("PROOFREAD_MODEL") or None,
                        help=argparse.SUPPRESS)
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=DEFAULT_HEALTH_TIMEOUT,
        help=f"seconds to wait for health after restore (default {DEFAULT_HEALTH_TIMEOUT:.0f}); "
             "a budgeted call never waits past its end",
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=_float_env("OCR_PHASE_MAX_SECONDS"),
        help="time budget for this call, including the restore; set it below the "
             "harness's shell time limit (env: OCR_PHASE_MAX_SECONDS). Required when "
             "the server has to be stopped; 0 means no budget (only outside a harness)",
    )
    parser.add_argument(
        "--restore-reserve",
        type=float,
        default=_float_env("OCR_PHASE_RESTORE_RESERVE"),
        help="seconds of the budget kept for stopping the runner, waiting for the OCR "
             "model to leave and restoring the server; default: from the waits and the "
             "restore time measured on this machine",
    )
    parser.add_argument(
        "--unlimited-shell",
        action="store_true",
        default=os.environ.get("OCR_PHASE_UNLIMITED_SHELL", "").strip().lower() in ("1", "true", "yes"),
        help="the shell running this call has no time limit, so --max-seconds 0 may stop the "
             "server for a whole book in one call (env: OCR_PHASE_UNLIMITED_SHELL=1)",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="for an agent that does NOT run on the local model: this call has no time budget "
             "(whatever --max-seconds or OCR_PHASE_MAX_SECONDS say) and is started detached, the "
             "agent waiting with wait_for_run.py, so the server is stopped and started once for the "
             "whole run. Refused, with nothing stopped, when the agent does run on the local model",
    )
    parser.add_argument(
        "--round",
        type=int,
        default=1,
        help="which call of a run of repeated calls this is; it changes nothing. On exit 75 or 76 "
             "the RESULT line names the next command with --round N+1, so repeated calls are not "
             "identical (an agent harness's loop detection counts identical tool calls)",
    )
    # Waits, for tests and slow hosts.
    parser.add_argument("--kill-grace", type=float, default=KILL_GRACE, help=argparse.SUPPRESS)
    parser.add_argument("--window-grace", type=float, default=WINDOW_GRACE, help=argparse.SUPPRESS)
    parser.add_argument("--release-settle", type=float, default=RELEASE_SETTLE, help=argparse.SUPPRESS)
    parser.add_argument("--busy-wait", type=float, default=BUSY_WAIT, help=argparse.SUPPRESS)
    parser.add_argument("--restore-horizon", type=float,
                        default=_float_env("OCR_PHASE_RESTORE_HORIZON") or RESTORE_HORIZON,
                        help=argparse.SUPPRESS)
    parser.add_argument("--watchdog-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--watchdog-state", help=argparse.SUPPRESS)
    parser.add_argument("--watchdog-window", help=argparse.SUPPRESS)
    parser.add_argument("--watchdog-queue", help=argparse.SUPPRESS)
    parser.add_argument(
        "runner",
        nargs=argparse.REMAINDER,
        help="the OCR runner command, after a literal --",
    )
    return parser


RESULT_MEANING = {
    0: "DONE",
    EXIT_INCOMPLETE: "INCOMPLETE - time budget spent, finished pages are sealed, the "
                     "server is up: run the SAME command again",
    EXIT_GPU_BUSY: "GPU BUSY - another job holds the GPU (or the model's other clients are "
                   "using it); nothing was stopped: wait, then run the SAME command again",
    1: "FAILED - read the runner's summary and ERROR lines above; do not just repeat",
    2: "CONFIGURATION ERROR - nothing was run",
    3: "SERVER NOT RESTORED - the watchdog retries; if it stays down, restart it by hand",
}


# Exit 1 when the GPU has too little free memory for the call's engines:
# what holds it is listed with how to free it, and nothing of it is stopped.
GPU_FULL = ("GPU FULL - too little free GPU memory for the OCR engines; no OCR was run. Nothing on the GPU "
            "was killed: tell the user what holds it and how to free it (the lines above), then run the "
            "SAME command again")


def next_round(argv: list[str], runner: list[str], current: int) -> str:
    """This call's command line for the next call of the run: the same, with
    --round CURRENT+1 among its own options (before the runner)."""
    options = list(argv[:len(argv) - len(runner)]) if runner else list(argv)
    kept: list[str] = []
    skip = False
    for item in options:
        if skip:
            skip = False
            continue
        if item == "--round":
            skip = True
            continue
        if item.startswith("--round="):
            continue
        kept.append(item)
    return shlex.join([sys.executable, os.path.abspath(__file__), *kept, "--round", str(current + 1), *runner])


def main(argv: list[str] | None = None) -> int:
    from _ocr_engines import require_supported_host
    try:
        require_supported_host()
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    raw = list(sys.argv[1:] if argv is None else argv)
    arguments = build_parser().parse_args(raw)
    if arguments.watchdog_fd is not None:
        return watchdog_main(arguments)
    code = run_phase(arguments)
    # One line that stands on its own, whatever the caller does with the rest of
    # the output (an agent piping through `tail` loses the exit code itself).
    note = getattr(arguments, "result_note", None)
    meaning = (getattr(arguments, "result_meaning", None)
               or RESULT_MEANING.get(code, "runner exit code, see its output above"))
    # The next call of a run that must be repeated differs from this one by
    # its round only (GH-13: identical calls in a row read as a loop).
    after = (f"; next call: {next_round(raw, arguments.runner, arguments.round)}"
             if code in (EXIT_INCOMPLETE, EXIT_GPU_BUSY) else "")
    # Whether the model was up, and whether this call stopped and restarted it.
    gpu = gpu_note(getattr(arguments, "gpu", None))
    say(f"[ocr-phase] RESULT exit={code} {meaning}{f' ({note})' if note else ''}{f' [{gpu}]' if gpu else ''}"
        f"{after}")
    return code


def reserve_seconds(arguments: argparse.Namespace, state: Path, swap: bool) -> float:
    """The part of the budget kept after the runner's deadline: every wait the
    end of the window can take, each capped there."""
    if arguments.restore_reserve is not None:
        return arguments.restore_reserve
    waits = WIND_DOWN + arguments.kill_grace + arguments.window_grace + KILL_SETTLE
    if swap:
        waits += (restore_estimate(state, arguments.max_seconds or None, probe_of(arguments))
                  + (TENANT_WAIT if arguments.tenant_mib else 0.0))
    return waits


def run_phase(arguments: argparse.Namespace, started: float | None = None) -> int:
    started = time.time() if started is None else started
    runner = arguments.runner
    if runner and runner[0] == "--":
        runner = runner[1:]
    if not runner:
        say("ERROR no runner command given (put it after --)", error=True)
        return 2
    swap_mode = "always" if arguments.force_swap else arguments.swap
    tenant = bool(arguments.release_cmd or arguments.restore_cmd)
    if getattr(arguments, "background", False):
        why = agent_on_model(arguments.health_url)
        if why:
            # The measured failure of 2026-09-23: the agent's next turn finds its own server
            # down, gives up, and its exit kills the hand-over.
            say(f"ERROR --background is for an agent that does not run on the local model, and {why}. "
                "Run this call in the FOREGROUND with its time budget instead (leave --background out).",
                error=True)
            return 2
        # No budget, and no cap to run into: the call is not a tool call.
        arguments.max_seconds = 0.0
        arguments.unlimited_shell = True
    budgeted = bool(arguments.max_seconds)
    clock = Clock(started + arguments.max_seconds if budgeted else None)
    env = dict(os.environ)
    env.pop(DEADLINE_ENV, None)

    # Validate known OCR routes before any health probe or hand-over state write,
    # even with a memory override. Vision never stops a configured model.
    try:
        detected_need, detected_note = engine_need(runner)
    except PhaseError as error:
        say(f"ERROR {error}", error=True)
        return 2
    if "Apple Vision" in detected_note and "Hunyuan" not in detected_note and "Paddle" not in detected_note:
        arguments.gpu = {"mode": "no-tenant"}
        say("[ocr-phase] Apple Vision: running directly without model hand-over")
        return run_plain(arguments, runner, env, clock)

    if not tenant:
        if swap_mode == "always":
            say("ERROR a swap was asked for but no release/restore commands are configured: "
                "set OCR_PHASE_RELEASE_CMD and OCR_PHASE_RESTORE_CMD", error=True)
            return 2
        # Nothing owns the GPU from outside the queue: run the runner as it is.
        arguments.gpu = {"mode": "no-tenant"}
        say("[ocr-phase] no local model to hand over (OCR_PHASE_RELEASE_CMD unset); "
            "running the OCR runner directly")
        return run_plain(arguments, runner, env, clock)

    try:
        url = ready_probe(arguments.ready_probe, os.environ.get("PROOFREAD_ENDPOINT"))
    except PhaseError as error:
        say(f"ERROR {error}", error=True)
        return 2
    arguments.ready = (url, arguments.ready_model) if url else None
    if any(part == "--gpu-queue-dir" or part.startswith("--gpu-queue-dir=") for part in runner):
        say("ERROR drop --gpu-queue-dir from the runner: the hand-over gives it the window's "
            "own queue (with the shared one it would wait behind this call forever)", error=True)
        return 2
    try:
        state = handover_directory()
    except (OSError, RuntimeError) as error:
        say(f"ERROR no hand-over state folder: {error}", error=True)
        return 2
    if arguments.required_mib:
        need, need_note = arguments.required_mib, "OCR_PHASE_REQUIRED_MIB"
    else:
        try:
            need, need_note = engine_need(runner)
        except PhaseError as error:
            say(f"ERROR {error}", error=True)
            return 2
    free, source = free_memory_mib()
    # A model that is not up before this call is never stopped, so never
    # started again (never: not even looked at).
    if swap_mode == "never":
        arguments.gpu = {"mode": "never"}
        model_up = None
    else:
        model_up, seen = model_state(arguments.health_url)
        arguments.gpu = {"mode": swap_mode, "modelUp": model_up, "model": seen, "released": False,
                         "restored": None}
        say(f"[ocr-phase] model: {({True: 'up', False: 'not up', None: 'cannot tell'})[model_up]} ({seen})")
    swap, reason = decide_swap(swap_mode, need, free, source, model_up)
    say(f"[ocr-phase] need {need} MiB ({need_note}); {reason} "
        f"-> {'swapping' if swap else 'no swap needed'}")
    if not swap and swap_mode == "never" and free is not None and free < need:
        say(f"[ocr-phase] WARNING only {free} MiB free; the OCR model may fail to load", error=True)

    reserve = reserve_seconds(arguments, state, swap)
    if budgeted and arguments.max_seconds <= reserve:
        say(f"ERROR --max-seconds ({arguments.max_seconds:.0f}) must exceed the "
            f"{reserve:.0f}s kept for the end of the window (--restore-reserve)", error=True)
        return 2
    if swap:
        if not arguments.release_cmd or not arguments.restore_cmd:
            say(
                f"ERROR the GPU has to be freed ({reason}) but the release/restore commands "
                "are not both configured.\n"
                "       Set OCR_PHASE_RELEASE_CMD and OCR_PHASE_RESTORE_CMD (and ideally\n"
                "       OCR_PHASE_HEALTH_URL), or stop the GPU process yourself and re-run.",
                error=True,
            )
            return 2
        if arguments.max_seconds is None:
            # Without a budget a long call runs until the harness kills it; the
            # watchdog then saves the server but not the agent's next turn.
            say(
                "ERROR stopping the server needs a time budget: set OCR_PHASE_MAX_SECONDS "
                "(machine config) or --max-seconds below the harness's shell time limit, "
                "and run this in the FOREGROUND. In a shell with no time limit, "
                "--max-seconds 0 with --unlimited-shell (OCR_PHASE_UNLIMITED_SHELL=1) "
                "runs without one.",
                error=True,
            )
            return 2
        if not budgeted and not arguments.unlimited_shell:
            # A budget of 0 is only safe where nothing will kill the call: say so
            # rather than run into the harness's cap with the server down.
            say(
                "ERROR --max-seconds 0 (no budget) stops the server for as long as the "
                "whole book takes, which a shell with a time limit kills mid-way. Set "
                "OCR_PHASE_UNLIMITED_SHELL=1 (or --unlimited-shell) only when this shell "
                "has no time limit and that was checked; otherwise set OCR_PHASE_MAX_SECONDS "
                "below the limit.",
                error=True,
            )
            return 2

    runner_deadline = hard_stop = None
    if budgeted:
        runner_deadline = clock.end - reserve
        hard_stop = runner_deadline + WIND_DOWN
        env[DEADLINE_ENV] = f"{runner_deadline:.3f}"

    window = Window(state, os.environ.get("OCR_GPU_RESOURCE", "local-gpu"))
    try:
        busy = window.open(arguments.busy_wait)
    except PhaseError as error:
        say(f"ERROR {error}", error=True)
        return 2
    if busy:
        left = str(read_status(state).get("state") or "")
        if left in ("watchdog-restoring", "restore-failed"):
            busy += (f" (status.json: {left}: the watchdog of an earlier hand-over is still restoring "
                     "the local model server)")
        say(f"ERROR {busy}; nothing was stopped", error=True)
        return EXIT_GPU_BUSY
    try:
        recovered = recover_left_down(arguments, state, clock)
    except BaseException:
        window.close()
        raise
    if isinstance(recovered, int):
        window.close()
        record_gpu(arguments, leftDownByEarlier=True, restored=False)
        return recovered
    if recovered == "restored" and not getattr(arguments, "recovered_once", False):
        # The swap was decided with the server down: decide it again.
        window.close()
        arguments.recovered_once = True
        return run_phase(arguments, started)
    window.environment(env)
    # A swap that ends without saying the server is back keeps the window's
    # folder (its children file) for the watchdog.
    restored = not swap
    try:
        if swap:
            code, restored = swap_and_run(arguments, runner, env, window, clock, hard_stop, reserve,
                                          need, state)
        elif model_up is False and need > 0 and gpu_full_with_the_model_down(arguments, need):
            code = 1
        else:
            code = run_in_window(arguments, runner, env, window, clock, hard_stop)
    finally:
        window.close(keep_files=not restored)
    record_window(state, arguments, code)
    return code


def gpu_full_with_the_model_down(arguments: argparse.Namespace, need: int) -> bool:
    """The model is not up, so there is nothing to stop: with too little free
    memory for the engines (read now, in the window), say what holds it and
    how to free it, and end the call (GPU FULL) with nothing stopped or
    started.  An idle GPU, or one whose free memory cannot be read, runs."""
    free, _ = free_memory_mib()
    if free is None or free >= need:
        return False
    say(f"ERROR only {free} MiB free, {need} MiB needed, and the local model is not up: something "
        "else holds the GPU. Nothing was stopped; no OCR was run", error=True)
    held, named = name_occupants()
    record_gpu(arguments, heldBy=named)
    arguments.result_note = f"{free} MiB free, {need} MiB needed; {held}"
    arguments.result_meaning = GPU_FULL + "; the local model was not up and was not started"
    return True


def run_plain(arguments: argparse.Namespace, runner: list[str], env: dict[str, str],
              clock: Clock) -> int:
    progress = tempfile.NamedTemporaryFile(prefix="ocr-phase-progress-", delete=False)
    progress.close()
    env[PROGRESS_ENV] = progress.name
    hard_stop = None
    if clock.end is not None:
        reserve = arguments.restore_reserve if arguments.restore_reserve is not None else (
            WIND_DOWN + arguments.kill_grace)
        if arguments.max_seconds <= reserve:
            say(f"ERROR --max-seconds ({arguments.max_seconds:.0f}) must exceed "
                f"--restore-reserve ({reserve:.0f})", error=True)
            return 2
        env[DEADLINE_ENV] = f"{clock.end - reserve:.3f}"
        hard_stop = clock.end - reserve + WIND_DOWN
    try:
        return run_runner(runner, env, hard_stop, {}, kill_grace=arguments.kill_grace)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(progress.name)


def no_progress_is_busy(code: int, window: Window, arguments: argparse.Namespace | None = None,
                        *, swapped: bool = False) -> int:
    """Under a window, exit 75 with no page sealed means the runner waited for
    the GPU or for time to load, not that work remains to be resumed: calling
    again at once would stop and start the server for nothing."""
    if code == EXIT_INCOMPLETE and not sealed_pages(window.progress):
        say("[ocr-phase] the runner sealed no page (it deferred: GPU queue, or no time left to "
            "load its model); reported as GPU busy", error=True)
        if swapped and arguments is not None:
            arguments.result_meaning = (
                "GPU BUSY - the runner sealed no page in this window (it waited for the GPU, or had "
                "no time to load its model); the server was stopped for it and is back up: wait, "
                "then run the SAME command again")
        return EXIT_GPU_BUSY
    return code


def run_in_window(arguments: argparse.Namespace, runner: list[str], env: dict[str, str],
                  window: Window, clock: Clock, hard_stop: float | None) -> int:
    """No swap: the runner runs in the window (the GPU is still exclusive to it),
    and the window closes only once its OCR processes are gone."""
    holder: dict[str, subprocess.Popen] = {}
    interrupted: Interrupted | None = None

    def on_signal(signum: int, _frame: object) -> None:
        raise Interrupted(signum)

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    code = 1
    try:
        say(f"[ocr-phase] running: {shlex.join(runner)}")
        code = run_runner(runner, env, hard_stop, holder, own_group=True,
                          pass_fds=window.lease.pass_fds, kill_grace=arguments.kill_grace)
    except Interrupted as signal_exit:
        interrupted = signal_exit
    finally:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        process = holder.get("runner")
        stop_runner(process, arguments.kill_grace)
        left = clear_window(window.children, window.private_root, process.pid if process else None,
                            clock, arguments.window_grace)
        if left:
            say(f"[ocr-phase] WARNING still there at the end of the window: {'; '.join(left)}",
                error=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if interrupted is not None:
        return 128 + int(interrupted.args[0])
    say(f"[ocr-phase] OCR runner exit code {code}")
    return no_progress_is_busy(code, window)


def swap_and_run(arguments: argparse.Namespace, runner: list[str], env: dict[str, str],
                 window: Window, clock: Clock, hard_stop: float | None, reserve: float,
                 need: int, state: Path) -> tuple[int, bool]:
    """Stop the server, run the runner, restore the server; (exit code, restored)."""
    waited = wait_for_other_clients(arguments, clock, reserve, window_floor(arguments, runner, state, reserve))
    if waited is not None:
        return waited, True

    status = {"restored": False, "restoring": False}
    holder: dict[str, subprocess.Popen] = {}
    watchdog_fd: int | None = None
    stand_in = None

    def tell_watchdog(step: str) -> None:
        if watchdog_fd is not None:
            try:
                os.write(watchdog_fd, f"{step}\n".encode())
            except OSError:
                pass

    def restore() -> bool:
        nonlocal stand_in
        if status["restored"]:
            return True
        status["restoring"] = True
        # The OCR model must let go of the GPU before the server loads again.
        process = holder.get("runner")
        stop_runner(process, min(arguments.kill_grace, clock.cap(arguments.kill_grace)))
        left = clear_window(window.children, window.private_root, process.pid if process else None,
                            clock, arguments.window_grace)
        if left:
            record_gpu(arguments, restored=False)
            close_stand_in(stand_in)
            stand_in = None
            tell_watchdog("ocr-resident")
            say("ERROR an OCR model of this window may still be resident "
                f"({'; '.join(left)}); the server is not restored over it", error=True)
            return False
        if arguments.tenant_mib:
            free = wait_for_room(arguments.tenant_mib, clock.cap(TENANT_WAIT))
            if free is not None and free < arguments.tenant_mib:
                say(f"[ocr-phase] WARNING only {free} MiB free of the {arguments.tenant_mib} MiB "
                    "the server needs (OCR_PHASE_TENANT_MIB); no OCR process of this window is "
                    "left, so something else holds it; restoring", error=True)
        close_stand_in(stand_in)
        stand_in = None
        tell_watchdog("restoring")
        write_status(state, "restoring", "the OCR window is over; restoring the server",
                     windowId=window.id)
        ok, seconds = bring_back(arguments.restore_cmd, arguments.health_url,
                                 arguments.health_timeout, clock=clock,
                                 ready=getattr(arguments, "ready", None))
        record_gpu(arguments, restored=ok)
        if not ok:
            tell_watchdog("restore-failed")  # no point waiting for it; retry at once
            left = clock.left()
            if arguments.health_url and left is not None and left <= CUT_SHORT_SECONDS:
                # The call's end stopped the wait, not the server: without this
                # the reserve would never grow, and every call would end so.
                record_restore_time(state, seconds, probe=probe_of(arguments))
                estimate = restore_estimate(state, arguments.max_seconds, probe_of(arguments))
                say(f"[ocr-phase] the call's time ran out {seconds:.0f}s into the restore: kept as the "
                    f"least it needs, so the next call keeps {estimate:.0f}s for it (timings.json)", error=True)
            return False
        if arguments.health_url:
            record_restore_time(state, seconds, budgeted=clock.end is not None, probe=probe_of(arguments))
        status["restored"] = True
        tell_watchdog("restored")
        write_status(state, "restored", "the server is back after the OCR window",
                     windowId=window.id, restoreSeconds=round(seconds, 2), **gpu_fields(arguments))
        return True

    # A killed phase must not leave the agent's own backend down.  The handler
    # only raises: stopping the runner and restoring happen in the finally below,
    # outside any Popen call the signal may have interrupted.
    def on_signal(signum: int, _frame: object) -> None:
        if status["restoring"]:
            return  # already on the way back; let it finish
        # First, so a second signal however soon finds the restore under way.
        status["restoring"] = True
        say(f"\n[ocr-phase] caught signal {signum}; restoring before exit")
        raise Interrupted(signum)

    def runner_started(process: subprocess.Popen) -> None:
        started = process_start_time(process.pid)
        if started is not None:
            tell_watchdog(f"runner {process.pid} {started.replace(' ', '_')}")

    def keep_stand_in(value) -> None:
        nonlocal stand_in
        stand_in = value

    previous: dict[int, object] = {}
    runner_code = 1
    too_full = False
    interrupted: Interrupted | None = None
    # Anything else that escapes the run, raised again once the server is
    # back - or dropped for exit 3 when it is not, as a `return` in a
    # `finally` did before Python 3.14 warned about it (PEP 765).
    unexpected: BaseException | None = None
    # The inner finally marks the restore as under way, after which
    # on_signal only returns: a signal anywhere before it (the run, the
    # PhaseError clause, the finally itself) is an Interrupted the outer
    # clauses catch, and none after it can escape before the restore.
    try:
        try:
            with signals_held():
                for sig in HANDLED_SIGNALS:
                    previous[sig] = signal.signal(sig, on_signal)
            # Only a model that was up comes here: the watchdog is armed for it,
            # and the restore starts only what this call stopped.
            watchdog_fd = start_watchdog(arguments, window)
            tell_watchdog("releasing")
            record_gpu(arguments, released=True)
            write_status(state, "releasing", "stopping the server for an OCR window", windowId=window.id,
                         **gpu_fields(arguments))
            release_began = time.monotonic()
            run_command(arguments.release_cmd, label="release", timeout=clock.cap(COMMAND_TIMEOUT))
            tell_watchdog("released")
            released_free = wait_for_room(need, clock.cap(arguments.release_settle))
            if released_free is None:
                # Nothing tells when the server's memory is free: give it a moment.
                time.sleep(clock.cap(min(2.0, arguments.release_settle)))
            record_release_time(state, time.monotonic() - release_began)
            say(f"[ocr-phase] free memory after release: "
                f"{'unknown' if released_free is None else f'{released_free} MiB'}")
            if released_free is not None and released_free < need:
                say(f"ERROR only {released_free} MiB free after the release, {need} MiB needed: "
                    "something else holds the GPU. Restoring the server; no OCR was run; nothing "
                    "else was stopped",
                    error=True)
                # Named with how to free it; the person decides, nothing is killed.
                held, named = name_occupants()
                record_gpu(arguments, heldBy=named)
                arguments.result_note = f"{released_free} MiB free after the release, {need} MiB needed; {held}"
                too_full = True
                runner_code = 1
            else:
                write_status(state, "ocr-running", "the server is stopped for an OCR window",
                             windowId=window.id, restoreCmd=arguments.restore_cmd, **gpu_fields(arguments))
                open_stand_in(arguments, clock, keep_stand_in)
                say(f"[ocr-phase] running: {shlex.join(runner)}")
                runner_code = run_runner(runner, env, hard_stop, holder, runner_started, own_group=True,
                                         pass_fds=window.lease.pass_fds, kill_grace=arguments.kill_grace)
        except PhaseError as error:
            runner_code = 2
            say(f"ERROR {error}", error=True)
        finally:
            # Handlers stay in place (ignoring signals) until the server is back.
            status["restoring"] = True
    except Interrupted as signal_exit:
        interrupted = signal_exit
    except BaseException as error:  # noqa: BLE001 - re-raised below once the server is back
        unexpected = error
    ok = restore()
    for sig, handler in previous.items():
        signal.signal(sig, handler)
    if not ok:
        # Before the watchdog takes over, so its own status is the last word.
        write_status(state, "restore-failed", "the hand-over ended with the server down; the "
                     "watchdog keeps trying", windowId=window.id, restoreCmd=arguments.restore_cmd)
    if watchdog_fd is not None:
        # Closing without "restored" hands the restore to the watchdog.
        os.close(watchdog_fd)
    if not ok:
        # Losing the backend is worse than a failed OCR pass; say so loudly.
        say(
            "ERROR the GPU server was NOT restored (the watchdog will retry). "
            "If it stays down, restart it manually before continuing:\n"
            f"       {arguments.restore_cmd}",
            error=True,
        )
        return 3, False
    if unexpected is not None:
        raise unexpected

    if interrupted is not None:
        return 128 + int(interrupted.args[0]), True
    if too_full:
        arguments.result_meaning = GPU_FULL + "; the local model was stopped for this call and is back up"
        return 1, True
    say(f"[ocr-phase] OCR runner exit code {runner_code}")
    return no_progress_is_busy(runner_code, window, arguments, swapped=True), True


if __name__ == "__main__":
    raise SystemExit(main())
