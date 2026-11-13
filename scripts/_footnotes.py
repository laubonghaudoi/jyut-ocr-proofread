"""Convert printed page notes into Markdown footnotes in finish_book.py's
finalize step, operating on the repaired book while page markers are still in
place.

Key design rules:
- Matching is scoped per page (with a 1-page cross-page lookback for an orphan
  call at the end of page p-1 whose note is printed on page p), using page
  markers to locate page boundaries.
- A note is a line whose text opens with a note number
  (_ocr_markdown.NOTE_LINE_RE); a call is a note number anywhere else on the
  page outside HTML blocks, $...$ inline math, or another note's own text.
- Greedy partial pairing: every key on a page whose calls and notes match in
  count (len(calls[k]) == len(notes[k]) >= 1) is paired, even if another note
  or call on the same page is unpaired or sits inside an HTML table.  Unpaired
  or skipped items stay exactly as printed and are recorded in `left` for
  doubts.md.
- Preserving printed labels by default (`renumber=False`): each paired footnote
  uses its printed numeric value `k` (e.g. `②④⓪` -> `[^240]`, `⑤²` -> `[^52]`,
  `①` -> `[^1]`) so numeric footnote identity matches the source page.  If a
  later page repeats an already-used numeric label (such as per-page `①`
  numbering across a multi-page book) and `renumber=False`, the repeated label
  is left as printed (`①`) rather than minting a non-numeric suffix or
  colliding with the earlier `[^1]:` definition.  Passing `renumber=True`
  (`finish_book.py --renumber-footnotes`) numbers all paired footnotes
  sequentially `1, 2, 3, ...` across the whole document.
- Footnote separator rule (`---`): removed above a page's foot-note block only
  when all notes on that page were converted into `[^L]:` definitions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import re
from typing import Any

from _note_markers import NOTE_HEAD_RE as NOTE, iter_note_markers, marker_key, math_context
from _ocr_markdown import (
    FOOTNOTE_CALL_RE,
    FOOTNOTE_HEAD_RE,
    THEMATIC_BREAK_RE,
    find_page_markers,
    foot_notes,
    html_table_lines,
    is_blank,
    line_body,
    split_lines,
)

# A $...$ pair on a line: math, where no renderer reads a footnote.
MATH = re.compile(r"\$[^$\n]+\$")
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
    extra_why: list[dict[str, Any]] = field(default_factory=list)


def _around(lines: list[str], n: int, s: int, e: int) -> str:
    body = line_body(lines[n])
    return body[max(0, s - 8) : e + 4]


def _why(
    calls: dict[str, list[Spot]],
    notes: dict[str, list[Spot]],
    odd: dict[str, list[Spot]],
    extra_why: list[dict[str, Any]],
    lines: list[str],
) -> list[dict[str, Any]]:
    """Build diagnostic reasons for any remaining unpaired calls, notes, or odd
    markers on a page."""
    why: list[dict[str, Any]] = []
    for k in sorted(set(calls) | set(notes), key=lambda k: (len(k), k)):
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
    why += extra_why
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

    When `renumber=False` (default), each paired footnote preserves its printed
    numeric label `k` (e.g. `[^240]`).  When `renumber=True`, paired footnotes
    are numbered sequentially `1, 2, 3, ...` across the document.
    """
    lines = split_lines(text)
    markers, _ = find_page_markers(lines)
    html = _html(lines)
    starts = [m.line_index for m in markers]

    page_scans: list[_PageScan] = []
    for page, marker, end in zip([None, *(m.page for m in markers)], [-1, *starts], [*starts, len(lines)]):
        calls: dict[str, list[Spot]] = defaultdict(list)
        notes: dict[str, list[Spot]] = defaultdict(list)
        odd: dict[str, list[Spot]] = {"html": [], "math": [], "inside": []}
        for n in range(marker + 1, end):
            body = line_body(lines[n])
            note = None if n in html else NOTE.match(body)
            if note and math_context(body, note.start("number"), note.end("number")):
                note = None
            if note:
                notes[key(note.group("number"))].append((n, note.start("number"), note.end()))
            for m in iter_note_markers(body, note.end() if note else 0):
                spot = (n, m.start(), m.end())
                if n in html:
                    odd["html"].append(spot)
                elif note:
                    odd["inside"].append(spot)
                elif math_context(body, m.start(), m.end()) or any(
                    s < m.end() and m.start() < e and not m.start() <= s < e <= m.end()
                    for s, e in (pair.span() for pair in MATH.finditer(body))
                ):
                    odd["math"].append(spot)
                else:
                    calls[key(m.group())].append(spot)
        unread_calls = calls.pop("", [])
        unread_notes = notes.pop("", [])
        odd = {"unread": unread_calls + unread_notes, **odd}
        page_scans.append(
            _PageScan(
                page=page,
                marker=marker,
                end=end,
                calls=calls,
                notes=notes,
                odd=odd,
                unread_notes=unread_notes,
            )
        )

    # Candidate pairs: (call_spot, note_spot, key_str, call_page_idx, note_page_idx)
    candidates: list[tuple[Spot, Spot, str, int, int]] = []

    # Step 1: Greedy same-page pairing for every key where len(calls[k]) == len(notes[k]) >= 1.
    for idx, ps in enumerate(page_scans):
        for k in sorted(set(ps.calls) & set(ps.notes), key=lambda k: (len(k), k)):
            if len(ps.calls[k]) == len(ps.notes[k]) >= 1:
                if not renumber and len(ps.calls[k]) > 1:
                    # Two footnotes on the same page cannot share the same numeric [^k]: label
                    # without renumbering; keep them as printed unless renumber=True.
                    ps.extra_why.append({
                        "kind": "duplicate_label", "number": k,
                        "calls": len(ps.calls[k]), "notes": len(ps.notes[k]),
                    })
                    continue
                for c_spot, n_spot in zip(ps.calls.pop(k), ps.notes.pop(k)):
                    candidates.append((c_spot, n_spot, k, idx, idx))

    # Step 2: 1-page cross-page orphan lookback (unpaired single call on page p-1,
    # unpaired single note of the same key on page p with no call on page p).
    for idx in range(1, len(page_scans)):
        prev_ps = page_scans[idx - 1]
        curr_ps = page_scans[idx]
        for k in sorted(list(curr_ps.notes.keys()), key=lambda k: (len(k), k)):
            if (
                len(curr_ps.notes[k]) == 1
                and not curr_ps.calls.get(k)
                and len(prev_ps.calls.get(k, [])) == 1
                and not prev_ps.notes.get(k)
            ):
                c_spot = prev_ps.calls.pop(k)[0]
                n_spot = curr_ps.notes.pop(k)[0]
                candidates.append((c_spot, n_spot, k, idx - 1, idx))

    candidates.sort(key=lambda item: (item[0], item[1]))

    # Collect pre-existing [^label] / [^label]: markers outside candidate spans so
    # assigned labels never collide with untouched footnote syntax.
    candidate_CallNote_spans: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for c_spot, n_spot, _, _, _ in candidates:
        candidate_CallNote_spans[c_spot[0]].append((c_spot[1], c_spot[2]))
        candidate_CallNote_spans[n_spot[0]].append((n_spot[1], n_spot[2]))

    def _overlaps_candidate(line_no: int, s: int, e: int) -> bool:
        return any(cs < e and s < ce for cs, ce in candidate_CallNote_spans.get(line_no, ()))

    taken: set[str] = set()
    for n, line in enumerate(lines):
        body = line_body(line)
        if m_head := FOOTNOTE_HEAD_RE.match(body):
            if not _overlaps_candidate(n, m_head.start(), m_head.end()):
                taken.add(m_head.group("label"))
        for m_call in FOOTNOTE_CALL_RE.finditer(body):
            if not _overlaps_candidate(n, m_call.start(), m_call.end()):
                taken.add(m_call.group("label"))

    # Assign labels and record any skipped duplicate-label candidates back onto their pages.
    accepted: list[tuple[Spot, Spot, str]] = []
    used_labels: list[str] = []
    used_set: set[str] = set()
    pages_with_writes: set[int] = set()
    seq_label = 0

    for c_spot, n_spot, k, c_idx, n_idx in candidates:
        if renumber:
            seq_label += 1
            while str(seq_label) in taken or str(seq_label) in used_set:
                seq_label += 1
            lbl = str(seq_label)
        else:
            if k in taken or k in used_set:
                # Preserve printed numbers on both call and note rather than
                # colliding with an earlier [^k]: definition.
                cn, cs, ce = c_spot
                page_scans[n_idx].notes[k].append(n_spot)
                if c_idx == n_idx:
                    page_scans[c_idx].calls[k].append(c_spot)
                page_scans[n_idx].extra_why.append(
                    {
                        "kind": "duplicate_label",
                        "number": line_body(lines[cn])[cs:ce],
                        "text": _around(lines, cn, cs, ce),
                    }
                )
                continue
            lbl = k

        accepted.append((c_spot, n_spot, lbl))
        used_labels.append(lbl)
        used_set.add(lbl)
        pages_with_writes.add(c_idx)
        pages_with_writes.add(n_idx)

    # Remove the foot-note separator rule (---) on pages that had at least one
    # note converted AND have zero unconverted notes remaining on the page.
    notes_converted_per_page: Counter[int] = Counter()
    for _, n_spot, _ in accepted:
        for idx, ps in enumerate(page_scans):
            if ps.marker < n_spot[0] < ps.end:
                notes_converted_per_page[idx] += 1
                break

    rules = 0
    for idx, ps in enumerate(page_scans):
        remaining_notes = sum(len(v) for v in ps.notes.values()) + len(ps.unread_notes)
        if notes_converted_per_page[idx] > 0 and remaining_notes == 0:
            rule = foot_notes(lines, ps.end)
            if ps.marker < rule < ps.end and THEMATIC_BREAK_RE.fullmatch(line_body(lines[rule])):
                gone = rule + 1
                while gone < ps.end and is_blank(lines[gone]):
                    gone += 1
                lines[rule:gone] = [""] * (gone - rule)
                rules += 1

    # Build `left` diagnostics for pages with any remaining unpaired/odd items.
    left: list[dict[str, Any]] = []
    for idx, ps in enumerate(page_scans):
        # Suppress redundant call/note entry when already explained by duplicate_label on same page.
        dup_spots = set()
        if ps.extra_why:
            for k in list(ps.calls.keys()):
                if ps.calls[k] and ps.notes.get(k) and len(ps.calls[k]) == len(ps.notes[k]):
                    ps.calls.pop(k, None)
                    ps.notes.pop(k, None)
                    dup_spots.add(k)
        why = _why(
            ps.calls,
            ps.notes,
            {kind: spots for kind, spots in ps.odd.items() if spots},
            ps.extra_why,
            lines,
        )
        if why:
            left.append({"page": ps.page, "why": why})

    edits: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for (n, s, e), (note_line, number, head), lbl in accepted:
        edits[n].append((s, e, f"[^{lbl}]"))
        edits[note_line].append((number, head, f"[^{lbl}]: "))

    for n, spans in edits.items():
        body = line_body(lines[n])
        ending = lines[n][len(body) :]
        for s, e, new in sorted(spans, reverse=True):
            body = body[:s] + new + body[e:]
        lines[n] = body + ending

    out = "".join(lines)
    heads = Counter(m.group("label") for line in split_lines(out) if (m := FOOTNOTE_HEAD_RE.match(line_body(line))))
    refs = Counter(m.group("label") for m in FOOTNOTE_CALL_RE.finditer(out)) - heads
    assert all(heads[lbl] == refs[lbl] == 1 for lbl in used_labels), (
        "a footnote written without its call, or twice"
    )
    written_pages = [page_scans[idx].page for idx in sorted(pages_with_writes)]
    return out, {
        "footnotes": len(accepted),
        "pagesWritten": len(written_pages),
        "rulesRemoved": rules,
        "renumbered": renumber,
        "left": left,
    }
