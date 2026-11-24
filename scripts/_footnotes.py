"""Convert printed page notes into Markdown footnotes in finish_book.py's
finalize step, operating on the repaired book while page markers are still in
place.

The rules follow the benchmark's footnote rules (jyut-ocr-bench
INSTRUCTIONS.md, 腳註; docs/scoring.md, #7): a call is written `[^L]` where
its number is printed, its note `[^L]: body`; labels are unique in the
document; only the calls and notes the pages print are written, and a call
or a note whose partner is not on the page stays as printed.

- Matching is per page, using page markers to locate page boundaries.  A
  note is a line whose text opens with a note number (_note_markers'
  NOTE_HEAD_RE); a call is a note number anywhere else on the page outside
  HTML blocks, $...$ inline math, or a note's own text.
- Pairing per key: every key on a page whose calls and notes match in count
  pairs the k-th call with the k-th note in reading order (two notes printed
  with the same number on one page are two footnotes), even if another note
  or call on the same page is unpaired or sits inside an HTML table.
  Unpaired or skipped items stay exactly as printed and are recorded in
  `left` for doubts.md.
- Across a page break only a note that clearly runs over: an unpaired call
  at the end of page p-1's calls and an unpaired note of the same number at
  the head of page p's notes, when the call before it has the number before
  its own (see _run_over).  Each such pair is listed in the report's
  `crossPage`; any other call or note without its partner on its page stays
  as printed.
- Labels keep the printed number by default (`renumber=False`): `②④⓪` ->
  `[^240]`, `⑤²` -> `[^52]`, `①` -> `[^1]`.  A label must be unique in the
  document, so when a printed number is used again (per-page `①` numbering,
  two notes numbered `①` on one page) the later footnote's label is the
  number, a hyphen and the use's ordinal: the second `①` is `[^1-2]`, the
  third `[^1-3]`, the same label on the call and on its definition.  A label
  that the text already holds as footnote syntax this step does not write
  (an engine's orphan `[^2]`) is skipped the same way.  `renumber=True`
  (`finish_book.py --renumber-footnotes`) numbers all paired footnotes
  sequentially `1, 2, 3, ...` across the whole document instead.
- Definitions stay where the notes are printed (the page foot); moving them
  after their calls' blocks is finish_book.py's opt-in --relocate-footnotes.
- Footnote separator rule (`---`): removed above a page's foot-note block only
  when all notes on that page were converted into `[^L]:` definitions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
from typing import Any

from jyut_ocr.core.note_markers import NOTE_CIRCLED, NOTE_HEAD_RE as NOTE, iter_note_markers, marker_key, math_context
from jyut_ocr.core.ocr_markdown import (
    FOOTNOTE_CALL_RE,
    FOOTNOTE_HEAD_RE,
    THEMATIC_BREAK_RE,
    find_page_markers,
    foot_notes,
    html_table_lines,
    is_blank,
    is_table_row,
    line_body,
    split_lines,
)

TABLE_SPAN_PAGES = 10

# A $...$ pair on a line: math, where no renderer reads a footnote.
MATH = re.compile(r"\$[^$\n]+\$")
CIRCLED = re.compile(rf"[{NOTE_CIRCLED}]")
# A line that opens an HTML block (CommonMark: a block-level tag, or a line
# that is one whole tag), which runs to the next blank line.  Not a line that
# opens with <sup>1</sup> and text (a paragraph), nor a comment.
HTML_BLOCK = re.compile(
    r" {0,3}(?:<(?:script|pre|style|textarea)(?:[\s>]|$)"
    r"|</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog"
    r"|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe"
    r"|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table"
    r"|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[\s/>]|$)"
    r"|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>[ \t]*$)",
    re.I,
)


def key(number: str) -> str:
    """The printed numeric identity of a supported marker; no inference."""
    return marker_key(number)


def _html(lines: list[str]) -> set[int]:
    """The lines Markdown is not read on: an HTML table's, and an HTML
    block's (HTML_BLOCK up to the next blank line)."""
    found = set(html_table_lines(lines))
    block = False
    for n, line in enumerate(lines):
        block = not is_blank(line) and (block or HTML_BLOCK.match(line_body(line)) is not None)
        if block:
            found.add(n)
    return found


Spot = tuple[int, int, int]  # (line_index, start_col, end_col)


@dataclass
class _PageScan:
    page: int | None
    marker: int
    end: int
    calls: dict[str, list[Spot]] = field(default_factory=lambda: defaultdict(list))
    notes: dict[str, list[Spot]] = field(default_factory=lambda: defaultdict(list))
    odd: dict[str, list[Spot]] = field(default_factory=dict)
    unread_notes: list[Spot] = field(default_factory=list)
    # Every call and note read on the page, in reading order, with its key
    # ("" for a number of no value); `calls` and `notes` lose what pairs.
    read_calls: list[tuple[Spot, str]] = field(default_factory=list)
    read_notes: list[tuple[Spot, str]] = field(default_factory=list)
    # The lines holding a note's own text: line -> where that text starts.
    note_text: dict[int, int] = field(default_factory=dict)


def _order(k: str) -> tuple[int, str]:
    return len(k), k


def _scan(lines: list[str], html: set[int], page: int | None, marker: int, end: int) -> _PageScan:
    """The calls, notes and odd numbers of one page (MARKER, END)."""
    scan = _PageScan(page=page, marker=marker, end=end)
    calls: dict[str, list[Spot]] = defaultdict(list)
    notes: dict[str, list[Spot]] = defaultdict(list)
    odd: dict[str, list[Spot]] = {"html": [], "math": [], "inside": []}
    for n in range(marker + 1, end):
        body = line_body(lines[n])
        note = None if n in html else NOTE.match(body)
        if note and math_context(body, note.start("number"), note.end("number")):
            note = None
        own = 0
        if note:
            k = key(note.group("number"))
            spot = (n, note.start("number"), note.end())
            notes[k].append(spot)
            scan.read_notes.append((spot, k))
            own = scan.note_text[n] = note.end()
        elif n not in html and (head := FOOTNOTE_HEAD_RE.match(body)):
            # A definition this step does not write ([^a]: ...): its text
            # is a note's text, where no number is a call.
            own = scan.note_text[n] = head.end()
        found: list[tuple[int, int]] = []
        # Footnote syntax with a label that is no number ([^1-2], [^a]) is
        # no call: nothing inside it is read as one.
        syntax = [ref.span() for ref in FOOTNOTE_CALL_RE.finditer(body)]
        for m in iter_note_markers(body, own):
            if any(s < m.start() < e for s, e in syntax):
                continue
            spot = (n, m.start(), m.end())
            found.append(m.span())
            if n in html:
                odd["html"].append(spot)
            elif n in scan.note_text:
                odd["inside"].append(spot)
            elif math_context(body, m.start(), m.end()) or any(
                s < m.end() and m.start() < e and not m.start() <= s < e <= m.end()
                for s, e in (pair.span() for pair in MATH.finditer(body))
            ):
                odd["math"].append(spot)
            else:
                k = key(m.group())
                calls[k].append(spot)
                scan.read_calls.append((spot, k))
        if n in html or n in scan.note_text:
            continue
        # A note-number glyph inside $...$ math that no marker covers is no
        # call; it is named in case the page's notes do not pair.
        for pair in MATH.finditer(body):
            if not any(s < pair.end() and pair.start() < e for s, e in found):
                odd["math"] += [(n, c.start(), c.end()) for c in CIRCLED.finditer(body, pair.start(), pair.end())]
    scan.unread_notes = notes.pop("", [])
    scan.odd = {"unread": calls.pop("", []) + scan.unread_notes, **odd}
    scan.calls, scan.notes = calls, notes
    return scan


def _run_over(before: _PageScan, after: _PageScan, preceding: dict[Spot, str]) -> list[tuple[Spot, Spot, str]]:
    """Pair the notes that clearly run over from page BEFORE to page AFTER,
    and take them out of the two pages' unpaired calls and notes.

    A note on AFTER pairs with a call on BEFORE only when all of these hold:
    it opens AFTER's notes (every note before it on AFTER pairs this way
    too), and it is the only note of its number on AFTER, which has no call
    of it; the call is the only call of that number on BEFORE, which has no
    note of it, and it comes after every call BEFORE pairs on the page; and
    the numbering runs on: the call read before it in the book (PRECEDING,
    call spot -> the previous call's key) has the number before its own, so
    a note numbered 1, where a page's or a chapter's numbering starts, never
    pairs across a page.  Anything else stays as printed: a call whose note
    is not on its page, or a note whose call is not, is listed in `left`
    (the benchmark keeps the half a page prints and makes up no other)."""
    if before.page is None or after.page is None:
        return []
    last = max((spot for spot, k in before.read_calls if k and k not in before.calls), default=None)
    found: list[tuple[Spot, Spot, str]] = []
    for spot, k in sorted(after.read_notes):
        calls = before.calls.get(k, []) if k else []
        if (
            not k
            or after.notes.get(k) != [spot]
            or after.calls.get(k)
            or before.notes.get(k)
            or len(calls) != 1
            or (last is not None and calls[0] < last)
            or not preceding.get(calls[0])
            or int(preceding[calls[0]]) != int(k) - 1
        ):
            break
        found.append((calls[0], spot, k))
        last = calls[0]
    for _, _, k in found:
        before.calls.pop(k)
        after.notes.pop(k)
    return found


def _table_run_over(scans: list[_PageScan], lines: list[str]) -> list[tuple[Spot, Spot, str, int, int]]:
    """Pair unpaired continuation-page notes with unpaired single calls inside
    Markdown pipe tables on earlier pages (e.g. stitched multi-page tables),
    requiring the continuation tail from the first paired note onward to leave
    no later unpaired numbered notes behind."""
    by_src: dict[int, list[tuple[Spot, Spot, str, int, int]]] = defaultdict(list)
    for idx in range(1, len(scans)):
        after = scans[idx]
        if after.page is None:
            continue
        for spot, k in sorted(after.read_notes):
            if not k or not k.isdigit() or int(k) <= 1:
                continue
            if after.notes.get(k) != [spot] or after.calls.get(k):
                continue
            for src in range(idx - 1, max(-1, idx - TABLE_SPAN_PAGES - 1), -1):
                before = scans[src]
                if before.page is None:
                    continue
                if any(scans[mid].calls.get(k) or scans[mid].notes.get(k) for mid in range(src + 1, idx)):
                    break
                if src < idx - 1 and any(scans[mid].read_calls for mid in range(src + 1, idx)):
                    break
                if before.notes.get(k):
                    continue
                calls = before.calls.get(k, [])
                if len(calls) == 1 and is_table_row(line_body(lines[calls[0][0]])):
                    by_src[src].append((calls[0], spot, k, src, idx))
                    break
    found: list[tuple[Spot, Spot, str, int, int]] = []
    for src, group in sorted(by_src.items()):
        paired_spots = {(idx, spot) for _, spot, _, _, idx in group}
        first_paired = min(paired_spots)
        max_idx = max(idx for _, _, _, _, idx in group)
        leftover_in_tail = False
        for p in range(src + 1, max_idx + 1):
            sc = scans[p]
            unpaired = [sp for spots in sc.notes.values() for sp in spots] + list(sc.unread_notes)
            if any((p, sp) not in paired_spots and (p, sp) > first_paired for sp in unpaired):
                leftover_in_tail = True
                break
        if leftover_in_tail:
            continue
        if not scans[max_idx].read_calls:
            for p2 in range(max_idx + 1, min(len(scans), src + TABLE_SPAN_PAGES + 1)):
                sc2 = scans[p2]
                if sc2.page is None or sc2.read_calls:
                    break
                if sc2.notes or sc2.unread_notes:
                    leftover_in_tail = True
                    break
            if leftover_in_tail:
                continue
        for c_spot, n_spot, key_k, _, idx in group:
            scans[src].calls.pop(key_k)
            scans[idx].notes.pop(key_k)
            found.append((c_spot, n_spot, key_k, src, idx))
    return found


def _around(lines: list[str], n: int, s: int, e: int) -> str:
    body = line_body(lines[n])
    return body[max(0, s - 8) : e + 4]


def _why(
    calls: dict[str, list[Spot]],
    notes: dict[str, list[Spot]],
    odd: dict[str, list[Spot]],
    lines: list[str],
) -> list[dict[str, Any]]:
    """Build diagnostic reasons for any remaining unpaired calls, notes, or odd
    markers on a page."""
    why: list[dict[str, Any]] = []
    for k in sorted(set(calls) | set(notes), key=_order):
        c_spots = calls.get(k, [])
        n_spots = notes.get(k, [])
        if not c_spots and not n_spots:
            continue
        if not n_spots:
            why += [
                {"kind": "call", "number": line_body(lines[n])[s:e], "text": _around(lines, n, s, e)}
                for n, s, e in c_spots
            ]
        elif not c_spots:
            why += [{"kind": "note", "number": k, "text": line_body(lines[n])[:30]} for n, _, _ in n_spots]
        elif len(c_spots) != len(n_spots):
            why.append({"kind": "count", "number": k, "calls": len(c_spots), "notes": len(n_spots)})
    why += [
        {"kind": kind, "number": line_body(lines[n])[s:e], "text": _around(lines, n, s, e)}
        for kind, spots in odd.items()
        for n, s, e in spots
        if spots
    ]
    return why


def convert(text: str, *, renumber: bool = False) -> tuple[str, dict[str, Any]]:
    """Convert paired printed notes in annotated Markdown `text` into Markdown
    footnotes (`[^L]` and `[^L]: `), returning `(converted_text, report)`.

    When `renumber=False` (default), each paired footnote keeps its printed
    numeric label `k` (e.g. `[^240]`), and a printed label used again gets
    `k-2`, `k-3`, ... so that labels are unique.  When `renumber=True`,
    paired footnotes are numbered sequentially `1, 2, 3, ...` across the
    document.  The report counts the footnotes, the pages written, the rules
    removed and the labels changed for uniqueness (`relabelled`), lists the
    pairs made across a page break (`crossPage`) and, for each page with
    something left as printed, why (`left`).
    """
    lines = split_lines(text)
    markers, _ = find_page_markers(lines)
    html = _html(lines)
    starts = [m.line_index for m in markers]
    scans = [
        _scan(lines, html, page, marker, end)
        for page, marker, end in zip([None, *(m.page for m in markers)], [-1, *starts], [*starts, len(lines)])
    ]

    # Candidate pairs: (call_spot, note_spot, key, call_page_idx, note_page_idx)
    candidates: list[tuple[Spot, Spot, str, int, int]] = []

    # Step 1: on each page, every key whose calls and notes match in count,
    # the k-th call with the k-th note.
    for idx, scan in enumerate(scans):
        for k in sorted(set(scan.calls) & set(scan.notes), key=_order):
            if len(scan.calls[k]) == len(scan.notes[k]):
                candidates += [(c, n, k, idx, idx) for c, n in zip(scan.calls.pop(k), scan.notes.pop(k))]

    # Step 2: a note that clearly runs over from the page before (_run_over).
    read = [item for scan in scans for item in scan.read_calls]
    preceding = {spot: k for (spot, _), (_, k) in zip(read[1:], read)}
    for idx in range(1, len(scans)):
        candidates += [(c, n, k, idx - 1, idx) for c, n, k in _run_over(scans[idx - 1], scans[idx], preceding)]

    # Step 2b: a stitched multi-page table whose calls sit on an earlier page.
    candidates += _table_run_over(scans, lines)

    candidates.sort(key=lambda item: (item[0], item[1]))

    # Footnote syntax this step does not write keeps its label: `taken`, or
    # `cited` for a reference inside a definition's own text (a note citing
    # another), which may share its label with a footnote of its own page.
    # Only a definition's text is no call: a definition the text holds, or a
    # note this step converts.  A note left as printed stays a plain line, so
    # a reference in its text is a call like any other and its label taken.
    spans: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for c_spot, n_spot, _, _, _ in candidates:
        spans[c_spot[0]].append((c_spot[1], c_spot[2]))
        spans[n_spot[0]].append((n_spot[1], n_spot[2]))
    converted = {n_spot[0] for _, n_spot, _, _, _ in candidates}
    taken: set[str] = set()
    cited: dict[str, set[int]] = defaultdict(set)
    for idx, scan in enumerate(scans):
        printed = {spot[0] for spot, _ in scan.read_notes} - converted
        for n in range(scan.marker + 1, scan.end):
            for m in FOOTNOTE_CALL_RE.finditer(line_body(lines[n])):
                if any(s < m.end() and m.start() < e for s, e in spans.get(n, ())):
                    continue
                if n in scan.note_text and m.start() >= scan.note_text[n] and n not in printed:
                    cited[m.group("label")].add(idx)
                else:
                    taken.add(m.group("label"))

    accepted: list[tuple[Spot, Spot, str, int, int]] = []
    used: set[str] = set()

    def free(label: str, pages: set[int] = frozenset()) -> bool:
        return label not in used and label not in taken and cited.get(label, set()) <= pages

    relabelled = 0
    seq_label = 0
    for c_spot, n_spot, k, c_idx, n_idx in candidates:
        if renumber:
            seq_label += 1
            while not free(str(seq_label)):
                seq_label += 1
            lbl = str(seq_label)
        elif free(k, {c_idx, n_idx}):
            lbl = k
        else:
            # The printed number is used already: the number and the use's
            # ordinal, the same on the call and on its definition.
            use = 2
            while not free(f"{k}-{use}"):
                use += 1
            lbl = f"{k}-{use}"
            relabelled += 1
        accepted.append((c_spot, n_spot, lbl, c_idx, n_idx))
        used.add(lbl)

    # Remove the foot-note separator rule (---) on pages that had at least one
    # note converted AND have zero unconverted notes remaining on the page.
    notes_converted = {n_idx for _, _, _, _, n_idx in accepted}
    rules = 0
    for idx, scan in enumerate(scans):
        remaining_notes = sum(len(v) for v in scan.notes.values()) + len(scan.unread_notes)
        if idx in notes_converted and remaining_notes == 0:
            rule = foot_notes(lines, scan.end)
            if scan.marker < rule < scan.end and THEMATIC_BREAK_RE.fullmatch(line_body(lines[rule])):
                gone = rule + 1
                while gone < scan.end and is_blank(lines[gone]):
                    gone += 1
                lines[rule:gone] = [""] * (gone - rule)
                rules += 1

    # Build `left` diagnostics for pages with any remaining unpaired/odd items.
    left: list[dict[str, Any]] = []
    for scan in scans:
        why = _why(scan.calls, scan.notes, {kind: spots for kind, spots in scan.odd.items() if spots}, lines)
        if why:
            left.append({"page": scan.page, "why": why})

    edits: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for (n, s, e), (note_line, number, head), lbl, _, _ in accepted:
        edits[n].append((s, e, f"[^{lbl}]"))
        edits[note_line].append((number, head, f"[^{lbl}]: "))

    for n, spans_on_line in edits.items():
        body = line_body(lines[n])
        ending = lines[n][len(body) :]
        for s, e, new in sorted(spans_on_line, reverse=True):
            body = body[:s] + new + body[e:]
        lines[n] = body + ending

    out = "".join(lines)
    # Never a definition without its call, nor a label defined or called
    # twice (a reference inside a definition's text is no call).
    heads: Counter[str] = Counter()
    refs: Counter[str] = Counter()
    for line in split_lines(out):
        body = line_body(line)
        if head := FOOTNOTE_HEAD_RE.match(body):
            heads[head.group("label")] += 1
        else:
            refs.update(m.group("label") for m in FOOTNOTE_CALL_RE.finditer(body))
    assert all(heads[lbl] == refs[lbl] == 1 for _, _, lbl, _, _ in accepted), (
        "a footnote written without its call, or twice"
    )
    written_pages = {idx for _, _, _, c_idx, n_idx in accepted for idx in (c_idx, n_idx)}
    return out, {
        "footnotes": len(accepted),
        "pagesWritten": len(written_pages),
        "rulesRemoved": rules,
        "renumbered": renumber,
        "relabelled": relabelled,
        "crossPage": [
            {"callPage": scans[c_idx].page, "notePage": scans[n_idx].page, "label": lbl}
            for _, _, lbl, c_idx, n_idx in accepted
            if c_idx != n_idx
        ],
        "left": left,
    }
