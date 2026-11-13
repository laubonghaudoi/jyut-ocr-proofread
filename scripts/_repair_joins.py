"""The book repair tool's paragraph and page-boundary rules
(scripts/repair_book.py): where a paragraph starts inside a page, and what
each page marker stands for - the labels of boundaries.json that
finalize_page_markers.py applies.

Every rule here reads the text and engine A's blocks; none reads the page
image.

Paragraphs (stage `paragraphs`), within each page:
- P6 two items of one list (the same marker; numbered ones in sequence)
  with only blank lines between -> one newline (a tight list).
- P8 a list item whose text opens with a note number - a circled figure
  (①, ⑳, ㉑) or [^N] - is the note's line (or the printed number's): its
  bullet goes, which the print never has before such a number, and a blank
  line stands before it.  Measured on the six delivered books (2026-10-13,
  model-off): 107 such items on a historical monograph (its notes, mostly in a trailing
  list of notes, some of which lost their number), 7 on a multi-volume archival compilation 2 and 4 on
  a multi-volume archival compilation 4 (`- ① 录自…`, where the golden page holds the note line); P6 leaves
  them out.  Not a note whose number moved inside its line (`- 同上。②。`).
- P5 (_repair_rules) the blank line around a heading.

Page boundaries (stage `joins`), each marker page against the marker page
before it.  The previous page's body text is its text up to the last line
holding a character engine A read in a block of its reading order: the
lines after it - notes, a running head or a folio left in, a table - are set
aside (all of them where no line holds such a character: no body text).
- S  the first marker -> `start`.
- T  inside the contents (M-6) -> `list-new` when the next line is a list
  item, else `paragraph`.
- H  the next page opens with a heading -> `structural`.
- L  a list item after an item of the same list -> `list-new`.
- J  the previous page's body text does not end a sentence (a
  sentence-final mark of the book, M-10, a note number after it set aside;
  a heading or a date line ends one), what follows its last line is the
  page's foot notes or nothing, that line is not a table's, neither page
  is a contents page, and the next page opens with ordinary text -> `join`.
  The foot notes (_ocr_markdown.foot_notes: the lines that end the page
  opening with a note number - NOTE_LINE_RE, the one the finish writes as
  footnotes: circled figures, superscripts, [^N] - with a rule above them)
  are then all the lines set aside, and finalize puts them after the end
  of the paragraph the join runs on, in printed order.  Measured on a multi-volume archival compilation
  2-5 and a historical monograph (2026-10-24, model-off): 32, 26, 37, 22 and 338 page
  breaks after an unfinished body that lines set aside follow, all
  `paragraph` before; at 31, 26, 37, 20 and 52 of them the lines set aside
  are foot notes alone, and those are joined now (left: a note that lost
  its number or is marked ●, a title page's line; most of a historical monograph's foot
  notes lost their numbers or are list items; with the finish's note
  number, 2026-10-25, a historical monograph 53: a note opening with superscripts).  Two
  of those joins (a multi-volume archival compilation 2 and 4, one each) follow a body ending in ：,
  where the golden starts a new paragraph: J joins after ： anyway (16
  times on a multi-volume archival compilation 2-4 before, the golden breaking 15).  Not where the next
  page opens with a note number: a note or a numbered item is a line of
  its own, though its text may end without a full stop (a historical monograph's
  numbered list of theses runs ⑳, ㉑ across a page break with no 。; on
  the previous page such a line in engine A's reading order is not set
  aside, so it is no foot note either).
- P  anything else -> `paragraph`: both sides kept, nothing spliced.
A boundary is structure only: no rule here writes a doubt.  A join is never
written into a heading, a list item, a table or an HTML block (finalize's
contract), nor after lines set aside that are not foot notes, which it
would splice onto the next page's text.  After a join onto a list item
that the next page's first paragraph finishes, the blank line before the
list's next item goes (P6).
boundary-evidence.json records each label's rule and the text it read.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import _book_lint as lint
import _ocr_markdown as om
import _repair_edits as re_
import _repair_rules as rr
import _repair_structure as rs


HEADING = rs.HEADING
LIST_NUMBER = re.compile(r"^[ \t]*(?P<n>\d+)[.)][ \t]+\S")
LIST_BULLET = re.compile(r"^[ \t]*(?P<b>[-*+])[ \t]+\S")
# A note number written after a sentence's end: a circled or superscript
# figure, as an engine or phase 3 writes it.
NOTE_TAIL = re.compile(r"(?:[①-⓿❶-➓㉑-㉟㊱-㊿¹²³⁰-⁹]"
                       r"|<sup>[^<\n]*</sup>|\$?\^\{[^}\n]*\}\$?|\s)+$")


def kind_of(line: str) -> str:
    body = om.line_body(line)
    if HEADING.match(body):
        return "heading"
    if om.LIST_ITEM_RE.match(body):
        return "list"
    if om.is_table_row(body) or body.lstrip().startswith("<"):
        return "table"
    if body.startswith(("  ", "\t")):
        return "indented"
    return "prose"


# --- paragraphs -----------------------------------------------------------------

def list_marker(line: str) -> tuple[str, int | None] | None:
    match = LIST_NUMBER.match(line)
    if match:
        return "number", int(match.group("n"))
    match = LIST_BULLET.match(line)
    if match:
        return match.group("b"), None
    return None


def same_list(one: str, two: str) -> bool:
    a, b = list_marker(one), list_marker(two)
    if not a or not b or a[0] != b[0]:
        return False
    return a[1] is None or b[1] == a[1] + 1


# A list item's bullet before a note number at the head of its text.
NOTE_ITEM = re.compile(r"^[-*+][ \t]+(?=[①-⑳㉑-㉟㊱-㊿]|\[\^\d+\])")
# A line that opens with a note number: a note's, or a numbered item's (the
# finish's own: J never joins into a page whose first line it writes as a
# footnote's definition).
NOTE_HEAD = om.NOTE_LINE_RE


def p8(setting: rs.Setting, scan: int, plan: rr.Plan) -> None:
    """P8 on one page: a list item that opens with a note number loses its
    bullet, with a blank line before it (after a heading P5 writes that
    line)."""
    lines = rr.lines_of(setting.texts[scan])
    for before, line in zip([None, *lines], lines):
        match = NOTE_ITEM.match(line.text)
        if not match:
            continue
        if before is not None and before.text.strip() and not HEADING.match(before.text):
            plan.edits.append(re_.Edit(scan, before.end, line.start + match.end(), "\n\n", rule="P8",
                                       evidence={"note": line.text[:16]}))
        else:
            plan.edits.append(re_.Edit(scan, line.start, line.start + match.end(), "", rule="P8",
                                       evidence={"note": line.text[:16]}))


def p6(setting: rs.Setting, scan: int, plan: rr.Plan) -> None:
    """P6 on one page: two items of one list a blank line apart."""
    text = setting.texts[scan]
    lines = [line for line in rs.line_spans(text) if line.text.strip()]
    for one, two in zip(lines, lines[1:]):
        between = text[one.end:two.start]
        if between.count("\n") < 2 or between.strip():
            continue
        if same_list(one.text, two.text) and not (NOTE_ITEM.match(one.text) or NOTE_ITEM.match(two.text)):
            plan.edits.append(re_.Edit(scan, one.end, two.start, "\n", rule="P6", evidence={
                "items": [one.text[:8], two.text[:8]]}))


def paragraphs(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
               facts: rr.BookFacts | None = None, readings: rs.Readings | None = None) -> rr.Plan:
    """The paragraphs stage over the pages ORDER: P6, P8 and P5."""
    facts = facts or rr.book_facts(book, model)
    setting = rs.Setting(book, model, facts, readings or rs.Readings(book, model), list(order), texts)
    plan = rr.Plan()
    for scan in order:
        p6(setting, scan, plan)
        p8(setting, scan, plan)
    # P5 and P6 act on different lines (a heading's, and list items').
    plan.edits.extend(rr.spacing(book, model, texts, order).edits)
    return plan


# --- page boundaries -------------------------------------------------------------

def first_line(text: str) -> str:
    return next((line for line in text.split("\n") if line.strip()), "")


def last_line(text: str) -> str:
    return lint.last_line(text)


def finished(line: str, finals: str) -> bool:
    if HEADING.match(line) or rs.is_date_line(line):
        return True
    return lint.sentence_finished(NOTE_TAIL.sub("", line), finals)


def body_tail(setting: rs.Setting, scan: int) -> tuple[str, int]:
    """The last line of page SCAN's body text (the module's docstring), and
    how many lines of the page follow it (set aside)."""
    reading = setting.reading(scan)
    lines = [line for line in rs.line_spans(reading.text) if line.text.strip()]
    blocks = reading.pd.a_blocks
    body = {b for b, block in enumerate(blocks) if isinstance(block.get("order"), int) and block["order"] >= 0}
    for n in range(len(lines) - 1, -1, -1):
        line = lines[n]
        if any(reading.block(k) in body for k in reading.chars_in(line.start, line.end)):
            return line.text, len(lines) - 1 - n
    return "", len(lines)


def boundary(setting: rs.Setting, prev: int, scan: int, finals: str) -> tuple[str, dict[str, Any]]:
    """The label of the boundary between marker pages PREV and SCAN and its
    evidence (the module's docstring)."""
    before, after = setting.texts[prev], setting.texts[scan]
    if not before.strip() or not after.strip():
        return "paragraph", {"rule": "empty", "why": "a page with no text"}
    head = first_line(after)
    tail, aside = body_tail(setting, prev)
    lines = [line for line in before.split("\n") if line.strip()]
    notes = len(lines) - om.foot_notes(lines, len(lines))
    kinds = (kind_of(lines[-1 - notes] if notes < len(lines) else ""), kind_of(head))
    done = finished(tail, finals)
    evidence: dict[str, Any] = {"previousTail": tail[-12:], "nextHead": head[:12], "previousFinished": done,
                                "setAside": aside, "kinds": list(kinds)}
    if notes:
        evidence["footNotes"] = notes
    contents = set(setting.model.get("contentsPages") or [])
    if prev in contents and scan in contents:
        label, rule = ("list-new" if kinds[1] == "list" else "paragraph"), "T"
    elif kinds[1] == "heading":
        label, rule = "structural", "H"
    elif kinds[1] == "list" and kinds[0] == "list" and same_list(last_line(before), head):
        label, rule = "list-new", "L"
    elif not done and tail and aside == notes and not {prev, scan} & contents and kinds[0] != "table" \
            and kinds[1] == "prose" and not NOTE_HEAD.match(head):
        label, rule = "join", "J"
    else:
        label, rule = "paragraph", "P"
    evidence["rule"] = rule
    return label, evidence


def joins(book: Any, model: Mapping[str, Any], texts: Mapping[int, str], order: Sequence[int],
          facts: rr.BookFacts | None = None,
          readings: rs.Readings | None = None) -> tuple[rr.Plan, dict[int, str], dict[str, Any]]:
    """The joins stage over the assembly's marker pages ORDER: the plan (P6's
    edits after joins), the labels and the evidence."""
    facts = facts or rr.book_facts(book, model)
    setting = rs.Setting(book, model, facts, readings or rs.Readings(book, model), list(order), texts)
    finals = setting.finals()
    plan = rr.Plan()
    labels: dict[int, str] = {}
    evidence: dict[str, Any] = {}
    markers = [s for s in sorted(book.pages) if book.pages[s].marker]
    for n, scan in enumerate(order):
        if n == 0:
            labels[scan] = "start"
            evidence[str(scan)] = {"label": "start", "rule": "S"}
            continue
        prev = order[n - 1]
        k = markers.index(scan) if scan in markers else -1
        if k <= 0 or markers[k - 1] != prev:
            labels[scan] = "paragraph"
            evidence[str(scan)] = {"label": "paragraph", "rule": "gap",
                                   "why": f"the pages between {prev} and {scan} are not in this run"}
            continue
        label, found = boundary(setting, prev, scan, finals)
        labels[scan] = label
        evidence[str(scan)] = {"label": label, **found}
        if label == "join" and found["kinds"][0] == "list":
            close_list(setting, scan, plan)
    return plan, labels, evidence


def close_list(setting: rs.Setting, scan: int, plan: rr.Plan) -> None:
    """P6 across a page: the next page's first paragraph finishes the list
    item the previous page ends with (a join), and the list's next item
    follows it after a blank line: the blank line goes."""
    text = setting.texts[scan]
    lines = [line for line in rs.line_spans(text) if line.text.strip()]
    if len(lines) < 2:
        return
    one, two = lines[0], lines[1]
    between = text[one.end:two.start]
    if between.count("\n") >= 2 and not between.strip() and kind_of(one.text) == "prose" and list_marker(two.text):
        plan.edits.append(re_.Edit(scan, one.end, two.start, "\n", rule="P6", evidence={
            "across": "the previous page's list item goes on here", "item": two.text[:8]}))


def contract_problems(combined: str, labels: Mapping[int, str]) -> list[str]:
    """finalize_page_markers.py's contract for LABELS on the annotated book
    COMBINED."""
    lines = om.split_lines(combined)
    markers, malformed = om.find_page_markers(lines)
    problems = [f"line {n}: malformed page marker {body!r}" for n, body in malformed]
    return problems + om.boundary_contract_problems(lines, markers, dict(labels))
