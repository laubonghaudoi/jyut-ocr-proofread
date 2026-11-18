"""How many PaddleOCR-VL workers read pages at once on one card.

Shared by run_paddleocr_vl.py, which starts them, and run_ocr_phase.py, which must know what
they need before it decides whether the local model has to make room.

Each worker is a whole PaddleOCR-VL process; the workers take pages from one line, and a
page's draft does not depend on which of them read it.  A worker's allocator grows for as
long as the card has room (one worker alone was seen at 27 GB after 270 pages of a book), so
when several run each is held to WORKER_SHARE_MIB (Paddle's FLAGS_gpu_memory_limit_mb).  One
worker runs as it always has, with no ceiling.

OCR_PADDLE_WORKERS / --workers: a number, or `auto` - as many shares as the free memory holds
beside CARD_RESERVE_MIB, at most MAX_AUTO_WORKERS.
"""
from __future__ import annotations

WORKERS_ENV = "OCR_PADDLE_WORKERS"
WORKER_SHARE_MIB = 14336
CARD_RESERVE_MIB = 2048
MAX_AUTO_WORKERS = 2
# Fewer pages than this to a worker are not worth its load.
MIN_PAGES_A_WORKER = 8


def asked(requested: str | None) -> int | None:
    """REQUESTED as a number of workers; None for `auto`.  ValueError for anything else."""
    text = (requested or "1").strip().lower()
    if text == "auto":
        return None
    try:
        number = int(text)
    except ValueError:
        raise ValueError(f"--workers takes a number or auto, not {requested!r}") from None
    if number < 1:
        raise ValueError("--workers must be at least 1")
    return number


def worker_count(requested: str | None, *, gpu: bool, pages: int, free_mib: int | None) -> tuple[int, str]:
    """How many workers read PAGES pages, and why.

    auto takes one worker when the free memory cannot be read.  A number is taken as given
    (the card may not hold it: a worker that cannot start is left out, and the rest go on).
    Only a GPU run of the native backend takes more than one, and a worker is never started
    for fewer than MIN_PAGES_A_WORKER pages."""
    number = asked(requested)
    if number is None:
        number = (min(MAX_AUTO_WORKERS, max(1, (free_mib - CARD_RESERVE_MIB) // WORKER_SHARE_MIB))
                  if free_mib is not None else 1)
        why = f"auto: {free_mib} MiB free" if free_mib is not None else "auto: free memory unknown"
    else:
        why = "as asked"
    if number > 1 and not gpu:
        return 1, "one worker: only a GPU run of the native backend takes more"
    by_pages = max(1, pages // MIN_PAGES_A_WORKER)
    if number > by_pages:
        return by_pages, f"{by_pages} worker(s): {pages} page(s), {MIN_PAGES_A_WORKER} a worker at least"
    return number, why


def need_mib(requested: str | None, one_worker_mib: int) -> int:
    """What the card must have free before the workers start.  A number above one asks for that
    many shares.  auto asks for one worker's memory only: it takes a second worker when the
    room is there, and never has the local model stopped to make that room."""
    try:
        number = asked(requested)
    except ValueError:
        return one_worker_mib        # the runner itself will say what is wrong with it
    return number * WORKER_SHARE_MIB if number and number > 1 else one_worker_mib
