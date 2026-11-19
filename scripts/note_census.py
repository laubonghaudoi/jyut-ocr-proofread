#!/usr/bin/env python3
"""Count where a book's note numbers stand on its sealed pages, and how the
engines read those the pages lack (issue #8, item 1: which case each lost
note number is).  Reads only; no model, no GPU.

    note_census.py BOOK [BOOK ...] [--json OUT]

BOOK is a proofread book folder (proofread/, the engine drafts, renders,
page manifest: what repair_book.py reads).  On each sealed page the note
slots are found as the notes stage finds them (_repair_notes: a call in a
line, a note opening a line, a note at the page's foot in engine A's
footnote block with no number), and each is counted as one of:

- held             the page holds a note number there;
- both-same        the page lacks it; both engines read the same number;
- one-engine       the page lacks it; one engine alone reads a number;
- position-apart   one engine reads it and the other reads the same number
                   in a gap or two beside (a call each engine places
                   otherwise);
- engines-differ   the page lacks it; the engines read different numbers;
- layout-only      a note at the page's foot that neither engine reads a
                   number for (engine A: a footnote block).

A slot is also counted `multi-digit` where an engine's reading has two or
three figures or is a run of circled glyphs (a number in one circle that
Unicode has no glyph for).  With the book finished (finish/footnotes.json),
the footnotes written and the pages left as printed are given beside.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _repair_inputs as ri  # noqa: E402
import _repair_model as rm  # noqa: E402
import _repair_notes as rnotes  # noqa: E402
import _repair_rules as rr  # noqa: E402
import _repair_runs as rn  # noqa: E402

CATEGORIES = ("held", "both-same", "one-engine", "position-apart", "engines-differ", "layout-only")


def classify(slots: list[rnotes.Slot]) -> list[tuple[rnotes.Slot, str, bool]]:
    """Each slot with its category and whether an engine read it multi-digit."""
    out = []
    for slot in slots:
        values = {k: v for k, v in slot.readings.items() if k in ("a", "b") and v}
        multi = any(len(v) >= 2 for v in values.values()) or any(
            len([c for c in shown if rnotes.CIRCLED.match(c)]) >= 2 for k, shown in slot.shown.items() if k in ("a", "b"))
        if "page" in slot.readings:
            kind = "held"
        elif not values:
            kind = "layout-only"
        elif len(values) == 1:
            kind = "one-engine"
        elif len(set(values.values())) == 1:
            kind = "both-same"
        else:
            kind = "engines-differ"
        out.append((slot, kind, multi))
    # One engine's call beside the other engine's call of the same number.
    calls = [(s, k) for s, k, _ in out if s.kind == "call" and k == "one-engine"]
    apart = set()
    for (s1, _), (s2, _) in ((x, y) for i, x in enumerate(calls) for y in calls[i + 1:]):
        v1 = next(iter(v for k, v in s1.readings.items() if k in ("a", "b") and v))
        v2 = next(iter(v for k, v in s2.readings.items() if k in ("a", "b") and v))
        e1 = next(k for k in ("a", "b") if s1.readings.get(k))
        e2 = next(k for k in ("a", "b") if s2.readings.get(k))
        if v1 == v2 and e1 != e2 and abs(s1.gap.k - s2.gap.k) <= 2:
            apart |= {id(s1), id(s2)}
    return [(s, "position-apart" if id(s) in apart else k, m) for s, k, m in out]


def census(book_dir: Path) -> dict[str, Any]:
    paths = ri.InputPaths.resolve(book_dir, {})
    book = ri.load_book(paths, None)
    model = rm.learn(book)
    order = list(book.assembly.order)
    texts = {scan: book.pages[scan].sealed_text for scan in order}
    setting = rn.Setting(book, model, rr.book_facts(book, model), order, texts)
    counts = {"call": Counter(), "note": Counter()}
    multi = Counter()
    pages_lost: list[int] = []
    for scan in order:
        page = setting.page(scan)
        if not page.s.chars:
            continue
        slots = rnotes._slots(page)
        slots = slots + rnotes._layout_slots(page, slots)
        lost = False
        for slot, kind, many in classify(slots):
            counts[slot.kind][kind] += 1
            if many:
                multi[slot.kind] += 1
            lost = lost or kind != "held"
        if lost:
            pages_lost.append(scan)
    out: dict[str, Any] = {"book": str(book_dir), "pages": len(order), "calls": dict(counts["call"]),
                           "notes": dict(counts["note"]), "multiDigit": dict(multi), "pagesLacking": pages_lost}
    footnotes = book_dir / "finish" / "footnotes.json"
    if footnotes.is_file():
        data = json.loads(footnotes.read_text(encoding="utf-8"))
        out["finished"] = {"footnotes": data.get("footnotes"), "pagesLeft": len(data.get("left") or [])}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("books", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="write every book's counts as JSON")
    arguments = parser.parse_args(argv)
    results = []
    for book in arguments.books:
        try:
            results.append(census(book))
        except (ri.InputError, OSError, ValueError) as error:
            print(f"{book}: {error}", file=sys.stderr)
    total = {"call": Counter(), "note": Counter()}
    for result in results:
        total["call"].update(result["calls"])
        total["note"].update(result["notes"])
        print(f"{result['book']}: calls {result['calls']} notes {result['notes']} multi-digit {result['multiDigit']} "
              f"pages lacking {len(result['pagesLacking'])}/{result['pages']}"
              + (f" finished {result['finished']}" if "finished" in result else ""))
    for kind in ("call", "note"):
        print(f"all {kind}s: " + ", ".join(f"{c} {total[kind][c]}" for c in CATEGORIES if total[kind][c]))
    if arguments.json:
        arguments.json.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
