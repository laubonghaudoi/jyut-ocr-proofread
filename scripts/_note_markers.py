"""Restricted numeric note syntax; recognizing syntax does not prove ownership.

Callers still need a matching definition and appropriate page context.  No
missing anchor is inferred from the numbering.  Balanced wrappers are parsed
with bounded depth/length, without a nested backtracking regular expression.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from collections.abc import Iterator

NOTE_CIRCLED = "①-⑳㉑-㉟㊱-㊿⓪-⓿❶-➓"
SUPERSCRIPTS = "⁰¹²³⁴-⁹"
_CIRCLED = re.compile(rf"[{NOTE_CIRCLED}]")
_FIGURE = re.compile(rf"[0-9０-９{NOTE_CIRCLED}{SUPERSCRIPTS}]")
_BARE = re.compile(rf"[{NOTE_CIRCLED}{SUPERSCRIPTS}]")
_MAX_LENGTH = 512
_MAX_DEPTH = 16


def _figures(raw: str) -> str:
    if not raw or any(_FIGURE.fullmatch(c) is None for c in raw):
        return ""
    values = [unicodedata.numeric(c, None) for c in raw]
    if any(value is None or value != int(value) for value in values):
        return ""
    return "".join(str(int(value)) for value in values)


def _group_end(text: str, start: int) -> int:
    """End of a balanced group, or -1; scan at most one marker's budget."""
    if start >= len(text) or text[start] != "{":
        return -1
    depth = 0
    for at in range(start, min(len(text), start + _MAX_LENGTH)):
        c = text[at]
        if c in "\r\n":
            return -1
        if c == "{":
            depth += 1
            if depth > _MAX_DEPTH:
                return -1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return at + 1
    return -1


def _numeric(raw: str, depth: int = 0) -> str:
    """Only digits, balanced grouping and the exact textcircled command."""
    raw = raw.strip(" \t")
    if not raw or depth >= _MAX_DEPTH or len(raw) > _MAX_LENGTH:
        return ""
    values: list[str] = []
    at = 0
    while at < len(raw):
        if raw[at] in " \t":
            at += 1
            continue
        if _FIGURE.fullmatch(raw[at]):
            values.append(_figures(raw[at]))
            at += 1
            continue
        if raw.startswith(r"\textcircled", at):
            at += len(r"\textcircled")
            while at < len(raw) and raw[at] in " \t":
                at += 1
        end = _group_end(raw, at)
        if end < 0:
            return ""
        value = _numeric(raw[at + 1:end - 1], depth + 1)
        if not value:
            return ""
        values.append(value)
        at = end
    return "".join(values)


def _latex(raw: str, depth: int = 0) -> str:
    raw = raw.strip(" \t")
    if not raw or len(raw) > _MAX_LENGTH or depth >= _MAX_DEPTH:
        return ""
    if raw.startswith("{"):
        end = _group_end(raw, 0)
        if end == len(raw):
            return _latex(raw[1:-1], depth + 1)
        if end > 0 and not raw[1:end - 1].strip(" \t"):
            raw = raw[end:].lstrip(" \t")
    if raw.startswith("^"):
        return _numeric(raw[1:], depth + 1)
    if raw.startswith(r"\textcircled"):
        return _numeric(raw, depth + 1)
    return ""


def _candidate_end(text: str, start: int) -> int:
    """Consume a whole wrapper, including invalid math, before scanning on."""
    limit = min(len(text), start + _MAX_LENGTH)
    newline = text.find("\n", start, limit)
    if newline >= 0:
        limit = newline
    if text[start] == "$":
        end = text.find("$", start + 1, limit)
        return end + 1 if end >= 0 else limit
    if text[start:start + 5].lower() == "<sup>":
        offset = text[start + 5:limit].lower().find("</sup>")
        end = start + 5 + offset if offset >= 0 else -1
        return end + 6 if end >= 0 else limit
    at = start
    if text.startswith("{}", at):
        at += 2
        while at < limit and text[at] in " \t":
            at += 1
    if at < limit and text[at] == "^":
        at += 1
    elif text.startswith(r"\textcircled", at):
        at += len(r"\textcircled")
    else:
        return start
    while at < limit and text[at] in " \t":
        at += 1
    if at < limit and text[at] == "{":
        end = _group_end(text, at)
        return end if end > 0 else limit
    while at < limit and _FIGURE.fullmatch(text[at]):
        at += 1
    return at if at > start else start


def _one(text: str, start: int) -> tuple[int, str]:
    if start >= len(text):
        return start, ""
    if text.startswith("[^", start):
        end = text.find("]", start + 2, start + _MAX_LENGTH)
        return (end + 1, _figures(text[start + 2:end])) if end >= 0 else (start, "")
    if _BARE.fullmatch(text[start]):
        return start + 1, _figures(text[start])
    end = _candidate_end(text, start)
    if end <= start:
        return start, ""
    raw = text[start:end]
    if raw.startswith("$"):
        return end, _latex(raw[1:-1]) if raw.endswith("$") and len(raw) > 1 else ""
    if raw[:5].lower() == "<sup>":
        return end, _figures(raw[5:-6].strip(" \t")) if raw[-6:].lower() == "</sup>" else ""
    return end, _latex(raw)


@dataclass(frozen=True)
class MarkerMatch:
    """Small match-compatible result for the existing internal parser callers."""
    string: str
    begin: int
    finish: int
    number_end: int | None = None

    def group(self, group: int | str = 0) -> str:
        return self.string[self.start(group):self.end(group)]

    def start(self, group: int | str = 0) -> int:
        if group not in (0, "number"):
            raise IndexError(group)
        return self.begin

    def end(self, group: int | str = 0) -> int:
        if group not in (0, "number"):
            raise IndexError(group)
        return self.number_end if group == "number" and self.number_end is not None else self.finish

    def span(self, group: int | str = 0) -> tuple[int, int]:
        return self.start(group), self.end(group)


class _MarkerPattern:
    def __init__(self, *, line_start: bool = False, note_head: bool = False) -> None:
        self.line_start = line_start
        self.note_head = note_head

    def match(self, text: str, pos: int = 0) -> MarkerMatch | None:
        if self.line_start and pos != 0:
            return None
        end = pos
        while end < len(text):
            following, value = _one(text, end)
            if not value or following - pos > _MAX_LENGTH:
                break
            end = following
        if end == pos:
            return None
        number_end = end
        if self.note_head:
            if text[pos:end].startswith("[^") and text[end:end + 1] == ":":
                end += 1
            while end < len(text) and text[end] in " \t":
                end += 1
        return MarkerMatch(text, pos, end, number_end)

    def fullmatch(self, text: str) -> MarkerMatch | None:
        match = self.match(text)
        return match if match is not None and match.end() == len(text) else None

    def finditer(self, text: str, pos: int = 0) -> Iterator[MarkerMatch]:
        return iter_note_markers(text, pos)


NUMBER_RE = _MarkerPattern()
NOTE_LINE_RE = _MarkerPattern(line_start=True)
NOTE_HEAD_RE = _MarkerPattern(line_start=True, note_head=True)


def marker_key(raw: str) -> str:
    """Numeric identity of an entire marker, preserving sequence/leading zeroes."""
    if not raw or len(raw) > _MAX_LENGTH:
        return ""
    at = 0
    values: list[str] = []
    while at < len(raw):
        end, value = _one(raw, at)
        if not value:
            return ""
        values.append(value)
        at = end
    return "".join(values)


def iter_note_markers(text: str, start: int = 0) -> Iterator[MarkerMatch]:
    """Yield whole markers; never mine an invalid wrapper/formula for digits."""
    at = start
    while at < len(text):
        match = NUMBER_RE.match(text, at)
        if match is not None:
            yield match
            at = match.end()
            continue
        # Suppress inner candidate glyphs in formulas or malformed wrappers.
        end = _candidate_end(text, at)
        if end - at >= _MAX_LENGTH:
            # An oversized corrupt wrapper is unresolved as a whole.  Never
            # resume inside it and mistake a later circled glyph for a call.
            newline = text.find("\n", end)
            end = newline if newline >= 0 else len(text)
        at = max(at + 1, end)


def _math_operator(char: str) -> bool:
    # A pipe can be Markdown cell syntax.  Other mathematical symbols include
    # Unicode plus/minus signs, comparisons, and superscript signed exponents.
    return char in "=+*/-" or (char != "|" and unicodedata.category(char) == "Sm")


def _math_base(char: str) -> bool:
    # Unicode variables and numerals are not limited to ASCII: π, 𝑥, Ⅲ, ٣.
    # Han prose legitimately carries superscript note calls, so do not classify
    # an ordinary Chinese sentence ending as an algebraic base.
    name = unicodedata.name(char, "")
    han = name.startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))
    return not han and unicodedata.category(char)[0] in {"L", "N"}


def math_context(text: str, start: int, end: int) -> bool:
    """Withhold a marker beside a plausible mathematical base/operator.

    Non-Han letters, numbers, combining marks on those bases and signed
    exponents are treated conservatively.  A citation following a non-Han word
    may remain unresolved; a Chinese sentence anchor is not thereby math.
    """
    raw = text[start:end]
    if raw.startswith("[^"):
        return False
    left = text[:start].rsplit("\n", 1)[-1].rstrip()
    right = text[end:].split("\n", 1)[0].lstrip()
    # Check operators before the circled-label exemption: ① + ② is arithmetic,
    # even when a separate numbered line happens to share an operand's value.
    if (left and _math_operator(left[-1])) or (right and _math_operator(right[0])):
        return True
    if all(_CIRCLED.fullmatch(c) for c in raw):
        return False
    while left and unicodedata.category(left[-1]).startswith("M"):
        left = left[:-1]
    return bool(left and (left[-1] in ")]" or _math_base(left[-1])))
