"""Offset edits on page texts, the repair log, the doubts, and the log's
replay (scripts/repair_book.py).

A stage proposes edits against the page texts as they stand when it starts:
each replaces text[start:end] of one page with AFTER, and names the rule
that justifies it and the evidence.  Ledger.apply() takes a
stage's edits in the order proposed; on a page, an edit whose span overlaps
one already taken is not applied - the first proposed wins, and the other
becomes a doubt of kind `conflict` naming both.  Two edits overlap when
their spans share a character, when an insertion (an empty span) falls
strictly inside the other's span, or when two insertions are at one place;
an insertion at the edge of another span does not overlap it and goes first.

Every applied edit is one record of repair-log.jsonl.  A record's offset is
into the page text as it stands just before that record is applied, in log
order, so replay() - the sealed pages plus the log, record by record - gives
the repaired pages; each record's BEFORE must be what the page holds at its
offset, or the replay fails.  Offsets count Python characters (code points).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping, Sequence


# The doubt kinds the repair writes (doubts.json); each is described in
# references/long-book-workflow.md (tests/check_documented.py).
DOUBT_KINDS = (
    "conflict",              # two edits over the same text; the first proposed was applied
    "unit-not-in-scan",      # a contents entry with no unit in the body
    "leaves-missing",        # a jump in the folio sequence inside a run
    "heading-kind-unknown",  # a heading no rule can place
    "gate-failed",           # a rule family off for this book, with its measurement
    "detector-unmatched",    # a lint hit that no rule covers
    "run-one-engine",        # a text run (digits, Latin, kana, □) one engine alone read where the page has none
    "run-engines-differ",    # the engines read different text runs between the same two characters
    "run-no-place",          # a text run both engines read that the page lacks, with no clean place for it (or a table cell's marks, N5)
    "run-mark-dropped",      # marks of the page neither engine reads there, dropped (or engine A's written for them) by N6
    "retry-marks-unverified",  # a refused first answer's marks engine A confirms, not written back
    "note-markers-unverified",  # a page's note numbers the engines read that the page lacks or writes otherwise
    "heading-mark-unread",   # heading marks neither engine read, left: engine A read a column there (K11)
    "same-reading-written-differently",  # one engine's reading beside one character, written different ways (W1)
    # 裁決's answer in traditional script written in engine A's simplified
    # form where engine B read another character, or in the one form neither
    # engine read (V1): phase 3's doubt of that kind.
    "answer-script-converted",
    # A site a rule found and could not settle (_repair_rules.unsettled), by
    # what is at stake there: the kind of the defect counter or of phase 3's
    # doubt that names such a site.
    "TEXT-LOST",             # a character both engines read at the page's edge, which the page lacks (F8)
    "FURN-HEAD",             # running-head text, or marks beside it, that may be left in the body (F1)
    "FURN-ANSWER",           # an answer at the running head's band that may be a body character (F6)
    "MARK-STANDIN",          # stand-in glyphs that may stand for a mark (K1, K5)
    "MARK-FIGURE-LOST",      # a figure engine A read that is not written (K1, K-seq)
    "HEAD-NUMBER-LOST",      # a unit heading whose number is not the contents' (S4)
    "TOC-NUMBER-LOST",       # a contents entry whose number no source read (S11)
    "punctuation-partial",   # the mark after a number the numbers beside it carry (S4, S11)
    "research-front-matter",  # a title page printed right to left (S13)
)


class ReplayError(Exception):
    """A log record does not apply to the text it names."""


@dataclass
class Edit:
    """One proposed change: PAGE's text[start:end] becomes AFTER."""
    page: int
    start: int
    end: int
    after: str
    rule: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rule:
            raise ValueError("an edit names the rule that justifies it")
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"bad span {self.start}:{self.end}")


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    (s1, e1), (s2, e2) = a, b
    if s1 == e1 and s2 == e2:
        return s1 == s2
    if s1 == e1:
        return s2 < s1 < e2
    if s2 == e2:
        return s1 < s2 < e1
    return s1 < e2 and s2 < e1


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


class Ledger:
    """The page texts under repair, the log of applied edits and the doubts."""

    def __init__(self, texts: Mapping[int, str]):
        self.original = dict(texts)
        self.texts = dict(texts)
        self.records: list[dict[str, Any]] = []
        self.doubts: list[dict[str, Any]] = []

    def doubt(self, kind: str, page: int | None, detail: str, **fields: Any) -> dict[str, Any]:
        if kind not in DOUBT_KINDS:
            raise ValueError(f"unknown doubt kind {kind!r}")
        record = {"id": f"U{len(self.doubts) + 1:04d}", "kind": kind, "page": page, "detail": detail, **fields}
        self.doubts.append(record)
        return record

    def apply(self, stage: str, edits: Sequence[Edit]) -> list[dict[str, Any]]:
        """Apply one stage's EDITS (see the module's docstring); returns the
        records written, in log order."""
        taken: dict[int, list[Edit]] = {}
        for edit in edits:
            if edit.page not in self.texts:
                raise ValueError(f"no page {edit.page} under repair")
            text = self.texts[edit.page]
            if edit.end > len(text):
                raise ValueError(f"page {edit.page}: span {edit.start}:{edit.end} past the end ({len(text)})")
            if text[edit.start:edit.end] == edit.after:
                continue
            clash = next((other for other in taken.get(edit.page, [])
                          if overlaps((edit.start, edit.end), (other.start, other.end))), None)
            if clash is not None:
                self.doubt("conflict", edit.page,
                           f"{describe(edit)} overlaps {describe(clash)}; the first proposed was applied",
                           stage=stage, applied=describe_record(clash, text),
                           refused=describe_record(edit, text))
                continue
            taken.setdefault(edit.page, []).append(edit)
        written = []
        for page in sorted(taken):
            text = self.texts[page]
            shift = 0
            pieces = []
            at = 0
            # Insertions at an edge go before the span they touch.
            for edit in sorted(taken[page], key=lambda e: (e.start, e.end)):
                before = text[edit.start:edit.end]
                offset = edit.start + shift
                pieces.append(text[at:edit.start])
                pieces.append(edit.after)
                at = edit.end
                current = "".join(pieces) + text[at:]
                record = {
                    "id": f"E{len(self.records) + 1:04d}",
                    "stage": stage,
                    "page": page,
                    "line": line_of(current, offset),
                    "offset": offset,
                    "before": before,
                    "after": edit.after,
                    "rule": edit.rule,
                    "evidence": dict(edit.evidence),
                }
                self.records.append(record)
                written.append(record)
                shift += len(edit.after) - len(before)
            pieces.append(text[at:])
            self.texts[page] = "".join(pieces)
        return written


def describe(edit: Edit) -> str:
    return f"{edit.rule} at {edit.start}:{edit.end}"


def describe_record(edit: Edit, text: str) -> dict[str, Any]:
    return {"rule": edit.rule, "start": edit.start, "end": edit.end,
            "before": text[edit.start:edit.end], "after": edit.after}


REQUIRED_FIELDS = ("id", "stage", "page", "line", "offset", "before", "after", "rule", "evidence")


def record_problems(record: Mapping[str, Any]) -> list[str]:
    """What a log record lacks: every field, and a rule."""
    name = record.get("id", "?")
    problems = [f"{name}: no field {key}" for key in REQUIRED_FIELDS if key not in record]
    if not record.get("rule"):
        problems.append(f"{name}: names no rule")
    if not isinstance(record.get("page"), int):
        problems.append(f"{name}: no page")
    return problems


def replay(original: Mapping[int, str], records: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """The pages ORIGINAL becomes under RECORDS, applied one by one; a record
    whose BEFORE is not at its offset raises ReplayError."""
    texts = dict(original)
    for record in records:
        page = record["page"]
        if page not in texts:
            raise ReplayError(f"{record.get('id')}: no page {page}")
        text = texts[page]
        offset = record["offset"]
        before = record["before"]
        if not (0 <= offset <= len(text)) or text[offset:offset + len(before)] != before:
            raise ReplayError(f"{record.get('id')}: page {page} does not hold {before!r} at {offset}")
        texts[page] = text[:offset] + record["after"] + text[offset + len(before):]
    return texts


def read_log(lines: Iterable[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in lines if line.strip()]


def log_bytes(records: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                   for record in records).encode("utf-8")
