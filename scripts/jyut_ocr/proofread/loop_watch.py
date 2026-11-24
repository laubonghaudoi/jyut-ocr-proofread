"""Word-for-word loops in the model's reasoning, watched while it streams.

Switch --loop-detector (proofread_pages.py; on by default, --no-loop-detector
turns it off).  At the top effort the model sometimes writes one passage of
its reasoning again and again, word for word, until the request's max_tokens
cuts it off; the effort ladder then asks the next rung (Client.ask_answering),
and the tokens of the loop were spent for nothing.  Measured on the 34
top-effort calls (裁決, punctuate, punctuate-retry, format) that one benchmark
run over six books cut at 16,000 tokens, each asked again twice with 32,000
(68 replays):

- 17 replays were cut again at 32,000.  Every one of them ended in one
  passage written back to back until the cut, the passage 20 to 2,784
  characters long; in 15 the repetition began before 16,000 tokens (at 200
  to 10,000).
- 2 more wrote a passage back to back from about 1,600 tokens and ended on
  their own at about 8,000 with an empty answer (finish_reason "stop").
- The 49 that ended with an answer never wrote a passage of 200 characters
  or more even twice in a row: at most 1.77 copies of one (loop_at's count).
- Measured again on every other reasoning text captured (802 more
  responses of the effort experiments: every effort, sampled and
  thinking-budget controls; 870 in all), with loop_at asked at every
  character of each text, so whatever events a stream is cut into: none of
  the 746 that ended with an answer (576 at the top effort, the rest at
  medium, low and none; 105 sampled) holds a loop, nor by 2 copies and 200
  characters looked for every 16 characters; it fires on 41, every one of
  them ended with no answer (36 cut at max_tokens, 5 empty).  From where
  the repetition begins to where it is found: 501 to 4,630 tokens, median
  about 1,100.

So a stream whose reasoning ends in COPIES back-to-back copies of one passage,
with at least REPEATED_MIN characters repeating the copy before them
(loop_at), is taken for such a loop: the client gives the request up there
and treats it exactly as a cut at max_tokens (Client._read_stream).

What the rule assumes: reasoning that has written the same passage three
times running, word for word, does not go on to end in an answer before
max_tokens.  The evidence is the measurements above: the loops were seen
at the top effort (40 of the 41; one at low, which ended empty), and no
answer at any effort came after one; the watch applies on every rung, as a
guard against degenerate output, which a passage written three times over
is.  Only the reasoning is watched: the answer after it transcribes a page,
and a page can print the same words many times.

It also assumes the reasoning does not copy such a run out of the question.
Reasoning does quote the question word for word (in the captures, up to 843
characters of it), so a question whose text holds one passage three times
running, at least `repeated` characters of it repeating, could be taken for
a loop.  Print was not found to: of 7,379 page texts of the corpus (the
OCR engines' drafts and every sealed page) the rule fires on 52: 48 an
engine's own loop (a few characters or a line read over and over; two
carried into a sealed page by an older run), 4 an engine's table markup of
empty cells written out row after row (no printed character); and on no
request of the replays (a 488-page book, 7,541 requests; 30 benchmark
pages, 545).  Where it happens the rung is given up and the next one
answers, recorded in the page's decisionChanges; a call whose every rung is
given up ends as one cut at every rung does (no_answer, degraded).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Rule:
    """The loop rule's settings, all in characters of the reasoning text but
    COPIES and TRIES.

    - copies: back-to-back copies of one passage at the end of the text;
    - repeated: characters, at least, that repeat the copy before them (so a
      short passage needs more copies: one of 20 characters, 41);
    - probe: the end of the text looked for earlier in it, where a copy of
      the passage may begin;
    - period_max: the longest passage looked for;
    - step: how many new characters between two looks;
    - tries: earlier places of the probe tried, nearest first.

    Measured on the 68 replays (the module's docstring): copies 2 to 4,
    repeated 200 to 1,200 and probe 48 to 160 all found the 19 loops and
    fired on no reasoning that ended in an answer; 3 copies of at least 800
    characters leave the widest margin over the answers' 1.77."""
    copies: int = 3
    repeated: int = 800
    probe: int = 96
    period_max: int = 12000
    step: int = 64
    tries: int = 32

    def text(self) -> str:
        """The rule as sealed (provenance, the run report)."""
        return (f"copies={self.copies},repeated={self.repeated},probe={self.probe},"
                f"periodMax={self.period_max},step={self.step},tries={self.tries}")


RULE = Rule()


def repeated_run(text: str, end: int, period: int) -> int:
    """How many characters before END each equal the one PERIOD before it:
    the largest n with text[end - n:end] == text[end - n - period:end - period]."""
    most = end - period
    if most <= 0:
        return 0
    good, size = 0, 64
    while True:
        size = min(size, most)
        if text[end - size:end] != text[end - size - period:end - period]:
            bad = size
            break
        good = size
        if size == most:
            return good
        size *= 2
    while bad - good > 1:
        middle = (good + bad) // 2
        if text[end - middle:end] == text[end - middle - period:end - period]:
            good = middle
        else:
            bad = middle
    return good


def loop_at(text: str, rule: Rule = RULE) -> dict[str, Any] | None:
    """The loop TEXT ends in, or None: the shortest passage, at most
    rule.period_max characters, of which TEXT ends in rule.copies copies back
    to back (the last one may be partial) with at least rule.repeated
    characters repeating the copy before them.  Where a copy may begin is
    where the last rule.probe characters were written before (nearest first,
    rule.tries places).

    Found: period (the passage's length), copies (how many, 1 + repeated /
    period), repeatedChars (the characters that repeat the copy before them),
    repeatsFrom (where the repetition begins: the second copy's first
    character) and atChar (the text's length)."""
    end = len(text)
    probe = rule.probe
    if end < rule.repeated + probe:
        return None
    tail = text[end - probe:]
    low = max(0, end - probe - rule.period_max)
    # An earlier place of the tail lies wholly in text[low:stop].
    stop = end - 1
    for _ in range(rule.tries):
        at = text.rfind(tail, low, stop)
        if at < 0:
            return None
        period = end - probe - at
        need = max((rule.copies - 1) * period, rule.repeated)
        if need + period <= end and text[end - need:] == text[end - need - period:end - period]:
            repeated = repeated_run(text, end, period)
            return {"period": period, "copies": round(1 + repeated / period, 2), "repeatedChars": repeated,
                    "repeatsFrom": end - repeated, "atChar": end}
        stop = at + probe - 1
    return None


class LoopWatch:
    """The reasoning of one stream, fed as it comes (feed): every rule.step
    new characters it looks at the end of all of it (loop_at).  Once a loop
    is found it is kept, and nothing more is looked at."""

    def __init__(self, rule: Rule = RULE):
        self.rule = rule
        self.text = ""
        self.pending: list[str] = []
        self.unchecked = 0
        self.found: dict[str, Any] | None = None

    def feed(self, piece: str) -> dict[str, Any] | None:
        """PIECE is the next part of the reasoning; the loop found so far, or None."""
        if self.found is not None or not piece:
            return self.found
        self.pending.append(piece)
        self.unchecked += len(piece)
        if self.unchecked >= self.rule.step:
            self.text += "".join(self.pending)
            self.pending.clear()
            self.unchecked = 0
            self.found = loop_at(self.text, self.rule)
        return self.found
