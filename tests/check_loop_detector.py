#!/usr/bin/env python3
"""Offline checks of switch --loop-detector (on by default since the round-4
benchmark, the user's decision of 2026-09-30; --no-loop-detector turns it
off).

No model, no GPU, no corpus.  The text is made up.  With the switch the
client streams every request and watches the reasoning as it comes
(Client._read_stream, scripts/_loop_watch.py); when the reasoning ends in one
passage written three times back to back, word for word, it gives the request
up and the effort ladder treats it exactly as a cut at max_tokens.  Checked:

- the rule (loop_at): three back-to-back copies of a passage are a loop, two
  and a bit are not, nor a passage written three times with other reasoning
  between, nor one longer than period_max; a short passage needs at least
  `repeated` characters of repetition; repeated_run against brute force;
  LoopWatch looks every `step` characters, whatever the pieces it is fed;
- the stream reader: a finished stream is read as the payload the endpoint
  answers unstreamed (answer, reasoning, finish reason, usage, timings); a
  looping one is closed where the loop is found, the lines after it unread,
  and read as a cut at max_tokens with the server's token count (or one per
  delta without it); the answer is never watched; an error event is a
  ProofreadError, a stream that ends before the model finished a dropped
  connection (retried);
- faults in a stream: comments (keep-alive), an event with no data, CRLF
  line ends are passed over; an event that cannot be read (not JSON, not
  UTF-8, not an object) is a dropped connection, the request asked again
  (over HTTP too, chunked as ninfer's library streams); a stream that ends
  after the finish but before its usage keeps its answer, with the server's
  live count for its tokens;
- the request: off, the body as before, unstreamed; on, streamed, and what
  request_body gives is what is sent;
- the ladder (a stand-in transport that streams, over the runner's own
  Client.ask, ask_answering and _read_stream): a loop cut goes on to the next
  rung with the full max_tokens, as a cut at max_tokens does without the
  switch, and the same answer comes back for fewer tokens; the bookkeeping (rungsAsked, rungsCutOff, degraded, loopCuts,
  no_answer's reason) and the ledger (loopWatched, loopCut,
  loopCutTokensSaved); a loop that would have ended in an empty reply is
  answered a rung lower;
- a whole page (proofread_pages.main with the stand-in): the page's text as
  without the switch, one decisionChanges entry at the spot, the calls,
  provenance, the run report and pages[]; off, none of their keys; a page
  sealed with the switch is not current without it, nor the other way round;
  the default is the switch on;
- where each entry says its call was (call_site): a 裁決 at its spot's crop,
  context, readings and place in the merged text - the main questions,
  D24's, D16's coverage read and a table character no engine read - even
  where the page's verdicts all answer alike; a punctuation pass at its
  block, range and tile;
- over HTTP (a stand-in server in this process on a free port): the client
  closes the connection where the loop is found, while the server still has
  lines to send, and the next rung answers.

Usage:
    python3 tests/check_loop_detector.py
"""

from __future__ import annotations

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import random
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import proofread_pages as pp  # noqa: E402
from jyut_ocr.proofread import loop_watch as _loop_watch  # noqa: E402
import stand_in  # noqa: E402

failures = 0
RULE = _loop_watch.RULE


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


# Made-up reasoning: a prefix that does not repeat, and passages of it.
random.seed(7)
WORDS = ["look", "again", "the", "mark", "beside", "column", "circle", "comma", "second", "print", "so",
         "wait", "maybe", "right", "left", "dot", "character", "image", "line", "count"]


def prose(n: int, seed: int) -> str:
    """N characters of made-up reasoning that repeats no passage of any length."""
    rng = random.Random(seed)
    out, i = [], 0
    while sum(map(len, out)) < n:
        out.append(f"{rng.choice(WORDS)} {i} ")
        i += 1
    return "".join(out)[:n]


# PREFIX ends in a line break, which no passage below holds, so a repetition
# begins where its first copy does.
PREFIX = prose(3000, 1) + "\n"
PASSAGE = prose(400, 2).replace(" ", "_") + "|"    # 401 characters, written with _ where PREFIX has spaces


def check_rule() -> None:
    check("the rule as sealed", RULE.text(), "copies=3,repeated=800,probe=96,periodMax=12000,step=64,tries=32")
    p = len(PASSAGE)
    three = PREFIX + PASSAGE * 3
    found = _loop_watch.loop_at(three)
    check("three copies back to back: a loop, the passage's length, where it repeats from",
          (found or {}).get("period"), p)
    check("... repeatsFrom is the second copy's start, atChar the end",
          ((found or {}).get("repeatsFrom"), (found or {}).get("atChar"), (found or {}).get("copies")),
          (len(PREFIX) + p, len(three), 3.0))
    check("two copies and most of a third: not yet", _loop_watch.loop_at(PREFIX + PASSAGE * 2 + PASSAGE[:p - 2]), None)
    spaced = PREFIX + "".join(PASSAGE + prose(300, 10 + i) for i in range(4))
    check("a passage written four times with other reasoning between: no loop",
          [_loop_watch.loop_at(spaced[:end]) for end in range(len(PREFIX) + 200, len(spaced) + 1, 97)
           if _loop_watch.loop_at(spaced[:end])], [])
    long = prose(13000, 3)
    check("a passage longer than period_max: not looked for", _loop_watch.loop_at(PREFIX + long * 3), None)
    short = "0123456789"
    check("a 10-character passage, 790 characters repeating: not yet",
          _loop_watch.loop_at(PREFIX + short * 80), None)
    found = _loop_watch.loop_at(PREFIX + short * 81)
    check("... 800 repeating: a loop of 10 characters, 81 copies",
          ((found or {}).get("period"), (found or {}).get("repeatedChars"), (found or {}).get("copies")),
          (10, 800, 81.0))
    one = _loop_watch.loop_at(PREFIX + "。" * 900)
    check("one mark written 900 times: a loop of one character", (one or {}).get("period"), 1)
    # repeated_run against brute force, on strings of a small alphabet.
    rng = random.Random(11)
    wrong = 0
    for _ in range(300):
        text = "".join(rng.choice("ab") for _ in range(rng.randint(1, 60)))
        end, period = len(text), rng.randint(1, 8)
        want = 0
        while want < end - period and text[end - want - 1] == text[end - want - 1 - period]:
            want += 1
        wrong += _loop_watch.repeated_run(text, end, period) != want
    check("repeated_run: as counted character by character, 300 strings", wrong, 0)
    # LoopWatch: the same finding whatever the pieces; looks every step characters.
    text = PREFIX + PASSAGE * 5
    ends = []
    for size in (1, 3, 17, 64, 200):
        watch = _loop_watch.LoopWatch()
        at = None
        for i in range(0, len(text), size):
            if watch.feed(text[i:i + size]) is not None:
                at = watch.found["atChar"]
                break
        ends.append(at)
    first = len(PREFIX) + 3 * len(PASSAGE)
    check("LoopWatch: found within a step of the third copy's end, whatever the pieces",
          [at is not None and first <= at < first + RULE.step + 200 for at in ends], [True] * 5)
    watch = _loop_watch.LoopWatch()
    looks = []
    real = _loop_watch.loop_at
    _loop_watch.loop_at = lambda t, rule=RULE: looks.append(len(t)) or None
    try:
        for i in range(0, 1000, 10):
            watch.feed(PREFIX[i:i + 10])
    finally:
        _loop_watch.loop_at = real
    check("LoopWatch: looks once per step characters", looks, list(range(70, 1001, 70)))


def event(delta=None, finish=None, timings=None, usage=None, choices=True, **extra) -> bytes:
    """One server-sent event of a chat completion stream, as ninfer writes it."""
    payload = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 5, "model": "stand-in"}
    payload["choices"] = ([{"index": 0, "delta": delta or {}, "logprobs": None, "finish_reason": finish}]
                          if choices else [])
    if timings is not None:
        payload["timings"] = timings
    if usage is not None:
        payload["usage"] = usage
    payload.update(extra)
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode()


def sse(thought: str, answer: str, finish: str, used: int, piece: int = 4, live: bool = True,
        key: str = "reasoning_content") -> list[bytes]:
    """The stream of a response: the role, the reasoning in PIECE-character
    deltas (each with the server's live count, one token a character, when
    LIVE), the answer, the finish reason, usage, [DONE]."""
    lines = [event({"role": "assistant", "content": ""})]
    for i in range(0, len(thought), piece):
        lines.append(event({key: thought[i:i + piece]},
                           timings={"prompt_n": 10, "cache_n": 0, "prompt_ms": 1.5, "predicted_n": i + len(thought[i:i + piece]),
                                    "predicted_ms": 2.5} if live else None))
    for i in range(0, len(answer), piece):
        lines.append(event({"content": answer[i:i + piece]}))
    lines.append(event({}, finish=finish))
    lines.append(event(usage={"prompt_tokens": 10, "completion_tokens": used,
                              "completion_tokens_details": {"reasoning_tokens": len(thought)}},
                       timings={"prompt_n": 10, "cache_n": 0, "prompt_ms": 1.5, "predicted_n": used,
                                "predicted_ms": 9.5}, choices=False))
    lines.append(b"data: [DONE]\n\n")
    return lines


class Lines:
    """A stand-in for the response urlopen returns: its lines, and close()."""

    def __init__(self, lines):
        self.lines, self.read, self.closed = list(lines), 0, False

    def __iter__(self):
        for line in self.lines:
            if self.closed:
                raise ValueError("I/O operation on closed file")
            self.read += 1
            yield line

    def close(self):
        self.closed = True


def reader(**kwargs) -> pp.Client:
    return pp.Client(stand_in.CLOSED_ENDPOINT, "stand-in", "xhigh", 30, loop_detector=RULE, **kwargs)


def check_reader() -> None:
    client = reader()
    thought, answer = prose(2000, 4), "構件：略\n定案：甲\nRESEARCH: no"
    source = Lines(sse(thought, answer, "stop", len(thought) + len(answer)))
    payload = client._read_stream(source)
    check("a finished stream: the payload the endpoint answers unstreamed",
          (payload["choices"], payload["usage"], payload["timings"], payload["model"]),
          ([{"index": 0, "message": {"role": "assistant", "content": answer, "reasoning_content": thought},
             "finish_reason": "stop"}],
           {"prompt_tokens": 10, "completion_tokens": len(thought) + len(answer),
            "completion_tokens_details": {"reasoning_tokens": len(thought)}},
           {"prompt_n": 10, "cache_n": 0, "prompt_ms": 1.5, "predicted_n": len(thought) + len(answer),
            "predicted_ms": 9.5}, "stand-in"))
    check("... every line read, not closed early, no loopCut",
          (source.read, len(source.lines), source.closed, "loopCut" in payload),
          (len(source.lines), len(source.lines), False, False))
    looping = PREFIX + PASSAGE * 40
    source = Lines(sse(looping, "", "length", 16000))
    payload = client._read_stream(source)
    loop = payload.get("loopCut") or {}
    first = len(PREFIX) + 3 * len(PASSAGE)
    check("a looping stream: closed where the loop is found, the rest unread",
          (source.closed, source.read < len(source.lines) // 4), (True, True))
    check("... read as a cut at max_tokens: no answer, the server's count of the tokens",
          (payload["choices"][0]["finish_reason"], payload["choices"][0]["message"]["content"],
           payload["usage"]["completion_tokens"] == loop.get("cutTokens"), loop.get("tokensCountedBy")),
          ("length", "", True, "server"))
    check("... cut within a step of the third copy's end, the passage found",
          (first <= loop.get("cutTokens", 0) < first + RULE.step + 8, loop.get("period"), loop.get("atChar")),
          (True, len(PASSAGE), loop.get("cutTokens")))
    source = Lines(sse(looping, "", "length", 16000, live=False))
    loop = client._read_stream(source).get("loopCut") or {}
    check("... without the server's count: one token per delta, said so",
          (loop.get("cutTokens"), loop.get("tokensCountedBy")), (loop.get("atChar", 0) // 4, "deltas"))
    loop = client._read_stream(Lines(sse(looping, "", "length", 16000, key="reasoning"))).get("loopCut")
    check("... the reasoning under the key `reasoning` too", bool(loop), True)
    table = "| 甲 | 乙 |\n" * 400
    source = Lines(sse(prose(500, 5), table, "stop", 5000))
    payload = client._read_stream(source)
    check("an answer that repeats itself is never watched",
          ("loopCut" in payload, payload["choices"][0]["message"]["content"] == table, source.closed),
          (False, True, False))
    try:
        client._read_stream(Lines([event({"reasoning_content": "abc"}),
                                   b'data: {"error": {"message": "internal", "type": "internal_error"}}\n\n']))
        got = "read"
    except pp.ProofreadError as error:
        got = str(error)[:13]
    check("an error event: a ProofreadError", got, "stream error:")
    try:
        client._read_stream(Lines(sse(prose(300, 6), "答", "stop", 301)[:-3]))
        got = "read"
    except OSError as error:
        got = type(error).__name__
    check("a stream that ends before the model finished: a dropped connection (retried)", got, "ConnectionError")
    try:
        client._read_stream(Lines([json.dumps(stand_in.reply("答")).encode()]))
        got = "read"
    except pp.ProofreadError as error:
        got = str(error)[:60]
    check("a JSON body where a stream was asked for: a ProofreadError, not asked again",
          got, "--loop-detector: the endpoint answered a streamed request wi")


def check_request() -> None:
    off = pp.Client(stand_in.CLOSED_ENDPOINT, "stand-in", "xhigh", 30)
    on = reader()
    plain = pp.Client(stand_in.CLOSED_ENDPOINT, "stand-in", "xhigh", 30, loop_detector=None)
    body_off = off.request_body("s", "t", None, 16000)
    check("off: the body as before, unstreamed", body_off, plain.request_body("s", "t", None, 16000))
    check("... no stream keys", [k for k in body_off if "stream" in k or "timings" in k], [])
    body_on = on.request_body("s", "t", None, 16000)
    check("on: streamed, the usage at the end, the server's live token count",
          {k: body_on[k] for k in ("stream", "stream_options", "timings_per_token")},
          {"stream": True, "stream_options": {"include_usage": True}, "timings_per_token": True})
    check("... the rest as off", {k: v for k, v in body_on.items()
                                  if k not in ("stream", "stream_options", "timings_per_token")}, body_off)
    sent = []
    on._send = lambda body, kind: (sent.append(json.dumps(body).encode()) or
                                   ({"choices": [{"message": {"content": "判定：不同"}, "finish_reason": "stop"}],
                                     "usage": {}}, 0.0))
    on.ask("s", "t", None, 16000, kind="other")
    check("... what is sent is what request_body gives",
          json.loads(sent[0]) == on.request_body("s", "t", None, 16000), True)


class Streamer(pp.Client):
    """The runner's Client with a stand-in transport: each request is answered
    by THINK(kind, effort, question text), the reasoning (none by default),
    then the answer: ANSWERS[(kind, effort)] ("" an empty reply), else what
    the stand-in model for the effort answers (INNERS, xhigh's by default),
    else "答 <effort>".  One token a character; reasoning that reaches
    max_tokens is cut there, nothing answered.  Streamed (the switch on) it
    is sent as a stream to the runner's _read_stream; else as one payload.
    SEEN: each request's (kind, max_tokens, effort, streamed); STREAMS: each
    stream's (lines read, lines, closed)."""

    def __init__(self, *args, think=None, answers=None, inners=None, stop_at=None, **kwargs):
        super().__init__(*(args or ("stand-in", "stand-in", "xhigh", 30)), **kwargs)
        self.think = think or (lambda kind, effort, text: "")
        self.answers = dict(answers or {})
        self.inners = dict(inners or {})
        self.stop_at = dict(stop_at or {})
        self.seen: list[tuple] = []
        self.streams: list[tuple] = []
        self.lock = threading.Lock()

    def _send(self, body, kind):
        effort = body.get("reasoning_effort") or "none"
        system, user = body["messages"][0]["content"], body["messages"][1]["content"][0]["text"]
        with self.lock:
            self.seen.append((kind, body["max_tokens"], effort, bool(body.get("stream"))))
        thought = self.think(kind, effort, user) if effort != "none" else ""
        limit = body["max_tokens"]
        if (kind, effort) in self.stop_at and len(thought) > self.stop_at[(kind, effort)]:
            # The reasoning ends on its own there, with nothing answered.
            thought, text, finish = thought[:self.stop_at[(kind, effort)]], "", "stop"
        elif len(thought) >= limit:
            thought, text, finish = thought[:limit], "", "length"
        else:
            finish = "stop"
            if (kind, effort) in self.answers:
                text = self.answers[(kind, effort)]
            elif self.inners:
                inner = self.inners.get(effort) or self.inners["xhigh"]
                text = inner.ask_answering(system, user, None, limit, kind=kind)[0]
            else:
                text = f"答 {effort}"
        used = len(thought) + len(text)
        if not body.get("stream"):
            return ({"choices": [{"message": {"content": text, **({"reasoning_content": thought} if thought else {})},
                                  "finish_reason": finish}],
                     "usage": {"prompt_tokens": 10, "completion_tokens": used,
                               "completion_tokens_details": {"reasoning_tokens": len(thought)}}}, 0.0)
        source = Lines(sse(thought, text, finish, used))
        payload = self._read_stream(source)
        with self.lock:
            self.streams.append((source.read, len(source.lines), source.closed))
        return payload, 0.0


LOOP = PREFIX + PASSAGE * 60          # past 16,000 characters: cut at max_tokens without the switch
LOOP_AT = len(PREFIX) + 3 * len(PASSAGE)


def loops(*kinds_efforts):
    return lambda kind, effort, text: LOOP if (kind, effort) in kinds_efforts else ""


def ladder(on: bool, think, kind="adjudicate", max_tokens=16000, **kwargs):
    """One ask_answering on a Streamer, the ledger and the loop notes set:
    (answer, metrics, requests, ledger, loop notes)."""
    client = Streamer(think=think, loop_detector=RULE if on else None, **kwargs)
    ledger, notes = [], []
    pp._CALL_LEDGER.set(ledger)
    pp._LOOP_CUTS.set(notes)
    try:
        text, m = client.ask_answering("s", "t", None, max_tokens, kind=kind)
    finally:
        pp._CALL_LEDGER.set(None)
        pp._LOOP_CUTS.set(None)
    return text, m, client.seen, ledger, notes


def check_ladder() -> None:
    text, m, seen, ledger, notes = ladder(True, loops(("adjudicate", "xhigh")))
    off_text, off_m, off_seen, off_ledger, off_notes = ladder(False, loops(("adjudicate", "xhigh")))
    check("a loop at xhigh, off: cut at max_tokens, medium answers", (off_text, [s[1:3] for s in off_seen]),
          ("答 medium", [(16000, "xhigh"), (16000, "medium")]))
    check("... on: given up in the loop, medium with the full max_tokens, the same answer",
          (text, [s[1:] for s in seen]), (off_text, [(16000, "xhigh", True), (16000, "medium", True)]))
    cut = ledger[0]["completion_tokens"]
    check("... given up within a step of the third copy's end", LOOP_AT <= cut < LOOP_AT + RULE.step + 8, True)
    check("... the tokens: the loop's, not max_tokens'",
          (off_m["completion_tokens"] - m["completion_tokens"]), 16000 - cut)
    check("... not degraded, one rung given up, answered at medium",
          (m.get("degraded"), m.get("loopCuts"), m.get("effort"), "rungsAsked" in m), (None, 1, "medium", False))
    check("... the ledger: both watched, the first given up, with what it left of max_tokens",
          [(c["effort"], c.get("loopWatched"), bool(c.get("loopCut")), c.get("loopCutTokensSaved"), c["finish_reason"])
           for c in ledger],
          [("xhigh", True, True, 16000 - cut, "length"), ("medium", True, False, None, "stop")])
    check("... the calls: loopCut and loopCutTokensSaved, truncated as a cut",
          {k: v for k, v in pp.summarize_calls(ledger)["adjudicate"].items()
           if k in ("n", "truncated", "loopCut", "loopCutTokensSaved")},
          {"n": 2, "truncated": 1, "loopCut": 1, "loopCutTokensSaved": 16000 - cut})
    check("... off: no loop keys in the ledger or the calls, no notes",
          ([k for c in off_ledger for k in c if k.startswith("loop")],
           [k for k in pp.summarize_calls(off_ledger)["adjudicate"] if k.startswith("loop")], off_notes),
          ([], [], []))
    [note] = notes
    check("... one note: the rung given up and the loop found",
          ([(l["effort"], l["maxTokens"], l["cutTokens"], l["period"], l["copies"] >= 3, l["tokensCountedBy"])
            for l in note["loops"]], note["answer"], note["answeredAt"], note["rungsAsked"], note["degraded"]),
          ([("xhigh", 16000, cut, len(PASSAGE), True, "server")], "答 medium", "medium", 2, False))
    # No loop: the same requests and answer as off, but streamed.
    text, m, seen, ledger, notes = ladder(True, lambda kind, effort, t: prose(9000, 8))
    check("long reasoning that does not loop: answered at xhigh, nothing given up",
          (text, [s[1:] for s in seen], notes, m.get("loopCuts")), ("答 xhigh", [(16000, "xhigh", True)], [], None))
    # Every rung with reasoning loops: the rung without reasoning answers.
    text, m, seen, *_ , notes = ladder(True, loops(("adjudicate", "xhigh"), ("adjudicate", "medium"),
                                                     ("adjudicate", "low")))
    check("every rung with reasoning loops: none answers, three given up",
          (text, [s[2] for s in seen], m.get("loopCuts"), [l["effort"] for l in notes[0]["loops"]]),
          ("答 none", ["xhigh", "medium", "low", "none"], 3, ["xhigh", "medium", "low"]))
    # ... and then an empty reply: degraded, and no_answer says so.
    text, m, *_ = ladder(True, loops(("format", "xhigh"), ("format", "medium"), ("format", "low")), kind="format",
                         answers={("format", "none"): ""})
    check("... then an empty reply: degraded, four rungs, three cut, all three in a loop",
          (m.get("degraded"), m.get("rungsAsked"), m.get("rungsCutOff"), m.get("loopCuts")), (True, 4, 3, 3))
    reason = pp.no_answer(text, m)
    check("... no_answer's reason", reason, "no answer: cut off at the token budget at 3 effort(s) "
                                            "(3 of them given up in a word-for-word loop), then an empty reply, "
                                            "nothing written")
    check("... a cut, for the retry lower (no_answer_cut)", pp.no_answer_cut(reason), True)
    _, m, *_ = ladder(False, loops(("format", "xhigh"), ("format", "medium"), ("format", "low")), kind="format",
                      answers={("format", "none"): ""})
    check("... off: the reason as before", pp.no_answer("", m),
          "no answer: cut off at the token budget at 3 effort(s), then an empty reply, nothing written")
    # A loop that ends on its own with no answer (measured twice in the replays).
    stop = {("punctuate", "xhigh"): 7000}
    off_text, off_m, *_ = ladder(False, loops(("punctuate", "xhigh")), kind="punctuate", stop_at=stop)
    text, m, seen, *_ = ladder(True, loops(("punctuate", "xhigh")), kind="punctuate", stop_at=stop)
    check("a loop that would end on its own with an empty reply: off, no answer",
          (off_text, pp.no_answer(off_text, off_m)), ("", "no answer: the reply was empty"))
    check("... on: given up in the loop, medium answers", (text, [s[2] for s in seen], m.get("loopCuts")),
          ("答 medium", ["xhigh", "medium"], 1))


def check_changes() -> None:
    notes = []
    client = Streamer(think=loops(("adjudicate", "xhigh"), ("census", "xhigh")), loop_detector=RULE)
    pp._LOOP_CUTS.set(notes)
    try:
        for kind, where in (("census", {"block": 1, "mergedOffset": 0, "range": [0, 12]}),
                            ("adjudicate", {"mergedOffset": 30, "crop": "c.png"}), ("adjudicate", {})):
            with pp.call_site(where):
                client.ask_answering("s", f"{kind} {where}", None, 16000, kind=kind)
    finally:
        pp._LOOP_CUTS.set(None)
    changes = pp.loop_cut_changes(notes)
    check("entries by kind, then place", [(c["kind"], c.get("mergedOffset")) for c in changes],
          [("adjudicate", 30), ("adjudicate", None), ("census", 0)])
    check("... whatever order the calls ended in", pp.loop_cut_changes(notes[::-1]), changes)
    first = changes[0]
    check("... an entry", {k: first[k] for k in ("decision", "rule", "kind", "after", "afterBy", "mergedOffset")},
          {"decision": "loopDetector", "rule": "reasoning-loop", "kind": "adjudicate",
           "after": "答 medium", "afterBy": "medium", "mergedOffset": 30})
    check("... its evidence: the rung given up, the loop, the call, and what of its site is not a place",
          (sorted(first["evidence"]), sorted(first["evidence"]["loops"][0])),
          (sorted(["loops", "maxTokens", "answerTokens", "rungsAsked", "degraded", "questionSha256", "crop"]),
           sorted(["effort", "maxTokens", "period", "copies", "repeatedChars", "repeatsFrom", "atChar",
                   "cutTokens", "tokensCountedBy"])))
    check("... census's range goes with the evidence, its block and offset with the place",
          ({k: changes[2].get(k) for k in ("block", "mergedOffset")}, changes[2]["evidence"].get("range")),
          ({"block": 1, "mergedOffset": 0}, [0, 12]))


# A page with one numeral disagreement, which goes to 裁決 whatever the
# preference: engine A 二, engine B 三.  裁決 at xhigh answers engine B's
# reading, at medium engine A's.
PAGE = {1: ([stand_in.block("民國十二年春三月天地玄黃宇宙洪荒", [0.10, 0.10, 0.80, 0.05])],
            "民國十三年春三月天地玄黃宇宙洪荒")}
INNERS = {"xhigh": stand_in.StandIn(census=0, judge=lambda a, b: b),
          "medium": stand_in.StandIn(census=0, judge=lambda a, b: a)}


OFF = ["--no-loop-detector"]


def run_page(root: Path, extra=(), think=loops(("adjudicate", "xhigh")), overwrite=True):
    made = []

    def factory(*args, **kwargs):
        made.append(Streamer(*args, think=think, inners=INNERS, **kwargs))
        return made[-1]

    engine_a = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / "a").glob("page-*.txt")))
    for inner in INNERS.values():
        inner.engine_a_text = engine_a
    real = pp.Client
    pp.Client = factory
    (root / "report.json").unlink(missing_ok=True)
    said = io.StringIO()
    try:
        with contextlib.redirect_stderr(said):
            status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                              "--draft2-directory", str(root / "b"), *stand_in.preferring(root), "--pages", "1",
                              "--reasoning-effort", "xhigh", "--report", str(root / "report.json"),
                              "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, "--model", "stand-in",
                              *(["--overwrite"] if overwrite else []), *extra])
    finally:
        pp.Client = real
    if not overwrite:
        # A resume the page's seal says was decided with the switch set the
        # other way does not start (round4_split): no report, an ERROR line.
        if not (root / "report.json").exists():
            return status, [line for line in said.getvalue().splitlines() if line.startswith("ERROR")], bool(made)
        counts = json.loads((root / "report.json").read_text(encoding="utf-8"))["counts"]
        return status, (counts["skippedCurrent"], counts["blocked"]), bool(made)
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert status == 0, status
    record = json.loads((root / "out" / "page-0001.json").read_text(encoding="utf-8"))
    markdown = (root / "out" / "page-0001.md").read_text(encoding="utf-8")
    return record, markdown, report, made[0]


def loop_entries(record):
    return [change for change in record["decisionChanges"] if change["decision"] == "loopDetector"]


def place(change):
    """Where a loop entry says its call was: its place and, from its
    evidence, what of its call's site is not a place (crop, range, tile)."""
    return ({key: change[key] for key in pp.SITE_PLACE_KEYS if key in change},
            {key: change["evidence"][key] for key in ("crop", "range", "tile") if key in change["evidence"]})


def spot(adjudication, merged_offset, engine_b=None):
    """The place an adjudication record gives its spot, as an entry of a
    call on it should say it (place)."""
    return ({"block": adjudication.get("block", 0), "contextBefore": adjudication["context_before"],
             "contextAfter": adjudication["context_after"], "engineA": adjudication["draft_reading"],
             "engineB": adjudication["writer_reading"] if engine_b is None else engine_b,
             "mergedOffset": merged_offset}, {"crop": adjudication["crop"]})


def check_page() -> None:
    with stand_in.book(PAGE) as root:
        off, off_md, off_report, off_client = run_page(root, OFF)
        on, on_md, on_report, on_client = run_page(root)
        calm, calm_md, _, _ = run_page(root, think=lambda kind, effort, t: "")
        calm_off, calm_off_md, _, _ = run_page(root, OFF, think=lambda kind, effort, t: "")
        run_page(root)
        resumed = [run_page(root, extra, overwrite=False) for extra in ([], OFF)]
        run_page(root, OFF)
        resumed += [run_page(root, extra, overwrite=False) for extra in (OFF, [])]
    check("off: 裁決 at xhigh cut at max_tokens, medium answers engine A's 二",
          ("十二年" in off_md, [s[1:3] for s in off_client.seen if s[0] == "adjudicate"]),
          (True, [(16000, "xhigh"), (16000, "medium")]))
    check("on: the same page", (on_md, on["adjudications"]), (off_md, off["adjudications"]))
    check("... every request streamed", {s[3] for s in on_client.seen}, {True})
    check("... 裁決 at xhigh given up in the loop", [s[1:3] for s in on_client.seen if s[0] == "adjudicate"],
          [(16000, "xhigh"), (16000, "medium")])
    [entry] = loop_entries(on)
    check("... one decisionChanges entry, for 裁決, medium's verdict",
          (entry["kind"], entry["after"], entry["afterBy"], entry["evidence"]["crop"]),
          ("adjudicate", "構件：略\n定案：十二\nRESEARCH: no", "medium", on["adjudications"][0]["crop"]))
    check("... at the spot, as the adjudication record gives it, and where it stands in the text",
          place(entry), spot(on["adjudications"][0], pp.CJK(on_md).index("十二年")))
    cut = entry["evidence"]["loops"][0]["cutTokens"]
    check("... the calls: the loop cut and what it saved; every kind watched",
          ({kind: (row.get("loopCut"), row.get("loopCutTokensSaved")) for kind, row in on["calls"].items()},
           on["calls"]["adjudicate"]["completionTokens"], off["calls"]["adjudicate"]["completionTokens"] - (16000 - cut)),
          ({kind: ((1, 16000 - cut) if kind == "adjudicate" else (0, 0)) for kind in on["calls"]},
           on["calls"]["adjudicate"]["completionTokens"], on["calls"]["adjudicate"]["completionTokens"]))
    check("... sealed in provenance", on["provenance"].get("loopDetector"), RULE.text())
    check("... the report: the rule, the cuts by kind, the page's count",
          (on_report["loopDetector"]["rule"], on_report["loopDetector"]["loopCuts"]["adjudicate"],
           on_report["pages"][0]["loopCuts"], on_report["decisionChanges"].get("loopDetector")),
          (RULE.text(), {"loopCut": 1, "loopCutTokensSaved": 16000 - cut}, 1, 1))
    check("off: no entry, no provenance key, no loop keys in calls, report or pages[]",
          (loop_entries(off), "loopDetector" in off["provenance"],
           [k for row in off["calls"].values() for k in row if k.startswith("loop")],
           "loopDetector" in off_report, "loopCuts" in off_report["pages"][0]),
          ([], False, [], False, False))
    check("... nothing streamed", {s[3] for s in off_client.seen}, {False})
    check("no loop: the page as off, no entry", (calm_md, loop_entries(calm)), (calm_off_md, []))
    other_way = lambda how: [f"ERROR resume {how} --no-loop-detector: 1 sealed page(s) in {root / 'out'} (1) were "  # noqa: E731
                             f"decided with --loop-detector {'on' if how == 'without' else 'off'}, this run the other "
                             "way; a book is decided one way throughout, so nothing was proofread (to decide every "
                             "page again with this run's switches: --overwrite, every page)"]
    # The client is made only when some page is proofread: a same-way resume
    # whose page is current makes none either (it reports the page skipped).
    check("resume: sealed with the switch, current with it only; sealed without, current without only - "
          "a run the other way does not start (no client, no report) and says how to resume",
          resumed, [(0, (1, 0), False), (2, other_way("without"), False), (0, (1, 0), False), (2, other_way("with"), False)])
    check("provenance: the rule when on, nothing when off; a key a resume compares",
          (pp.decision_provenance(SimpleNamespace(loop_detector=True)).get("loopDetector"),
           "loopDetector" in pp.decision_provenance(SimpleNamespace()), "loopDetector" in pp.OPTIONAL_PROVENANCE),
          (RULE.text(), False, True))
    parse = lambda *extra: pp.build_parser().parse_args(["r", "a", "o", *extra]).loop_detector  # noqa: E731
    check("parsing: on by default, off with --no-loop-detector", (parse(), parse(*OFF)), (True, False))


def run_cut(pages, model, looping=lambda kind, text: True, profile=None):
    """PAGES (stand_in.book) proofread by the runner's Client over a
    Streamer: the xhigh rung of every call LOOPING picks (by kind and
    question text) reasons in a loop (LOOP), which the detector gives up;
    every other request is answered as the stand-in MODEL answers, at any
    effort.  Returns the page's record and Markdown."""
    def factory(*args, **kwargs):
        return Streamer(*args, think=lambda kind, effort, text: LOOP if effort == "xhigh" and looping(kind, text)
                        else "", inners={"xhigh": model}, **kwargs)

    with stand_in.book(pages, profile=profile) as root:
        model.engine_a_text = "\n".join(path.read_text(encoding="utf-8")
                                        for path in sorted((root / "a").glob("page-*.txt")))
        real = pp.Client
        pp.Client = factory
        try:
            status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                              "--draft2-directory", str(root / "b"), *stand_in.preferring(root), "--pages", "1",
                              "--reasoning-effort", "xhigh", "--overwrite", "--report", str(root / "report.json"),
                              "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, "--model", "stand-in"])
        finally:
            pp.Client = real
        assert status == 0, status
        record = json.loads((root / "out" / "page-0001.json").read_text(encoding="utf-8"))
        return record, (root / "out" / "page-0001.md").read_text(encoding="utf-8")


def png() -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("L", (40, 40), 255).save(buffer, "PNG")
    return buffer.getvalue()


# Three 裁決 on one page that all answer 算, one of them decision D24's: engine
# A read 筍 where engine B wrote a rare codepoint twice, and 筍 once more in a
# stretch engine B did not read.
P1, P2, P3 = "天地玄黃宇宙洪荒日月盈昃辰宿列張", "寒來暑往秋收冬藏閏餘成歲律呂調陽", "雲騰致雨露結為霜金生麗水玉出崑岡"
ONLY_A, P4 = "劍號巨闕珠筍夜光果珍李柰", "菜重芥薑海鹹河淡鱗潛羽翔龍師火帝"
SUAN = {1: ([stand_in.block(P1 + "珠筍" + P2 + "筍法" + P3 + ONLY_A + P4, [0.3, 0.1, 0.4, 0.8])],
            P1 + "珠\U0002E155" + P2 + "\U0002E155法" + P3 + P4)}
# Where each 筍 stands in the merged text.
SUAN_AT = [len(P1) + 1, len(P1 + "珠筍" + P2), len(P1 + "珠筍" + P2 + "筍法" + P3) + ONLY_A.index("筍")]


def check_places() -> None:
    """A call given up in a loop says where its answer went, as every
    decisionChanges entry does (call_site): the entry of a call on a spot ties
    to its adjudication record (its crop, context, readings) and to its place
    in the merged text - the main questions, D24's, D16's coverage read and a
    table character no engine read - even where the page's verdicts all
    answer alike; a punctuation pass's to its punctuationPasses record.  And
    the page is the page without the loops: the rung below answers alike."""
    judge = stand_in.StandIn(census=0, judge=lambda a, b: a.replace("筍", "算") if b in ("\U0002E155", "算") else a)
    calm, calm_md = run_cut(SUAN, judge, looping=lambda kind, text: False)
    on, on_md = run_cut(SUAN, judge, looping=lambda kind, text: kind == "adjudicate")
    adjudications = on["adjudications"]
    check("three 裁決, one of them D24's, all answering 算", ([a.get("askedFor") for a in adjudications],
          {a["verdict"] for a in adjudications}, [pp.CJK(on_md)[at] for at in SUAN_AT]),
          ([None, None, "D24"], {"構件：略\n定案：算"}, ["算"] * 3))
    check("... the page as without the loops", (on_md, adjudications), (calm_md, calm["adjudications"]))
    check("... each entry at its own spot: crop, context, readings and place in the merged text",
          [place(c) for c in loop_entries(on)],
          [spot(adjudications[0], SUAN_AT[0]), spot(adjudications[1], SUAN_AT[1]),
           spot(adjudications[2], SUAN_AT[2], engine_b="")])
    check("... D24's as its D24 entry gives it", place(loop_entries(on)[2])[0],
          {key: c[key] for c in on["decisionChanges"] if c["decision"] == "D24" for key in pp.SITE_PLACE_KEYS})
    # One of the three in a loop: its entry says which, though its answer is all three's.
    one, _ = run_cut(SUAN, judge, looping=lambda kind, text: kind == "adjudicate" and "下文：法" in text)
    check("one loop of three answering alike: its entry names its spot",
          ([c["after"] for c in loop_entries(one)], [place(c) for c in loop_entries(one)]),
          (["構件：略\n定案：算\nRESEARCH: no"], [spot(one["adjudications"][1], SUAN_AT[1])]))

    # D16's second look at a 無 whose reading stands nearby (confirm_nothing):
    # the coverage read is at the spot its 裁決 was.
    body, body2 = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏", "閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡"
    printed = body + "茲奉大總統令着大總統府秘書長即日到任" + body2
    skipped = body + "茲奉大總統令着府秘書長即日到任" + body2
    pages = {1: ([stand_in.block(skipped, [0.3, 0.1, 0.4, 0.8])], printed)}
    nothing = stand_in.StandIn(census=0, judge=lambda a, b: "無")
    adjudicate = {"preferEngine": "adjudicate"}
    calm, calm_md = run_cut(pages, nothing, looping=lambda kind, text: False, profile=adjudicate)
    on, on_md = run_cut(pages, nothing, looping=lambda kind, text: kind in ("adjudicate", "coverage-read"),
                        profile=adjudicate)
    [adjudication] = on["adjudications"]
    check("D16's coverage read: the page as without the loops",
          (on_md, adjudication["resolved_from"], "coverageRead" in adjudication),
          (calm_md, "adjudicator-empty", True))
    check("... 裁決 and the coverage read, each at the spot",
          [(c["kind"], place(c)) for c in loop_entries(on)],
          [(kind, spot(adjudication, len(body + "茲奉大總統令着"))) for kind in ("adjudicate", "coverage-read")])

    # A character no engine read, confirmed by 裁決 on its own crop in a
    # rebuilt table (headerCharacters): its block and context as its
    # adjudication record gives them, and where the table's region begins
    # (the region's first block is the body, before the header holding it).
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = ("右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
             "凡塾中教員務須先將各本通讀一過然後按表所定年期逐課教授勿稍躐等")
    pages = {1: ([stand_in.block(prose, [0.70, 0.10, 0.25, 0.80]),
                  stand_in.block(first + second, [0.10, 0.20, 0.52, 0.70], "vertical_text"),
                  stand_in.block("書名册全數書課每數册年備期用敎授要", [0.60, 0.20, 0.06, 0.60], "vertical_text")],
                 prose + "書名全書册數每册課數備用年期" + second + first)}
    region = (f"| 書名 | 全書册數 | 每册課數 | 備用年期 | 敎授要言 |\n| --- | --- | --- | --- | --- |\n"
              f"| {first[:5]} | {first[5:]} | {second[:5]} | {second[5:]} | |")
    table = stand_in.StandIn(census=0, judge=lambda a, b: "言" if (a, b) == ("", "言") else a or "無",
                             rebuild=lambda text: region if "書名" in text else f"| {first} |\n| --- |\n| {second} |")
    calm, calm_md = run_cut(pages, table, looping=lambda kind, text: False)
    on, on_md = run_cut(pages, table, looping=lambda kind, text: kind == "adjudicate")
    [header] = [c for c in on["decisionChanges"] if c["decision"] == "headerCharacters"]
    check("a table character no engine read: the page as without the loops",
          (on_md, "敎授要言" in on_md, [(a.get("askedFor"), a.get("block")) for a in on["adjudications"]],
           header["block"], header["mergedOffset"]), (calm_md, True, [("headerCharacters", 2)], 1, len(prose)))
    check("... its 裁決 at the spot's block and context, and where the table's entries put the region",
          [place(c) for c in loop_entries(on)], [spot(on["adjudications"][0], len(prose), engine_b="")])

    # Punctuation: each block's census and punctuate at its pass, as its
    # punctuationPasses record gives it.
    texts = ["天地玄黃宇宙洪荒日月盈昃", "辰宿列張寒來暑往秋收冬藏", "閏餘成歲律呂調陽雲騰致雨"]

    def punctuate(span: str) -> str:
        return span[:3] + "，" + span[3:6] + "。" + span[6:] + "。"

    pages = {1: ([stand_in.block(t, [0.85 - 0.12 * i, 0.1, 0.06, 0.6]) for i, t in enumerate(texts)], "".join(texts))}
    marks = stand_in.StandIn(census=3, punctuate=punctuate)
    # The last block is punctuated in two column groups.
    real_tiles = pp.block_tiles
    pp.block_tiles = lambda render, block, n, limit=pp.TILE_CHARACTERS: (
        [(png(), 0, 6), (png(), 6, 12)] if block["text"] == texts[2] else None)
    try:
        calm, calm_md = run_cut(pages, marks, looping=lambda kind, text: False)
        on, on_md = run_cut(pages, marks, looping=lambda kind, text: kind in ("census", "punctuate"))
    finally:
        pp.block_tiles = real_tiles
    passes = on["punctuationPasses"]
    check("punctuation: the page as without the loops, the last block in two",
          (on_md, [(p["block"], p["range"], p.get("tile")) for p in passes]),
          (calm_md, [(0, [0, 12], None), (1, [0, 12], None), (2, [0, 6], 0), (2, [6, 12], 1)]))
    starts = [len("".join(texts[:p["block"]])) for p in passes]
    check("... each pass's census and punctuate at the pass",
          [(c["kind"], place(c)) for c in loop_entries(on)],
          [(kind, ({"block": p["block"], "mergedOffset": start + p["range"][0]},
                   {"range": p["range"], **({"tile": p["tile"]} if "tile" in p else {})}))
           for kind in ("census", "punctuate") for p, start in zip(passes, starts)])


# The reasoning the stand-in server streams at xhigh: a loop from its 201st character.
SERVED_PREFIX = prose(200, 14) + "\n"
SERVED_AT = len(SERVED_PREFIX) + 3 * len(PASSAGE)


class Looping(BaseHTTPRequestHandler):
    """A stand-in model server that streams: at xhigh, reasoning that loops,
    3,000 events of it, one a millisecond (so the server is never far ahead
    of what its client has read); below it an answer.  Each request's body
    and, at xhigh, how many lines it wrote before its client was gone."""
    record: list = []
    done = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        entry = {"body": body, "written": 0, "gone": False}
        self.record.append(entry)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        xhigh = body.get("reasoning_effort") == "xhigh"
        lines = (sse((SERVED_PREFIX + PASSAGE * 40)[:12000], "", "length", 16000) if xhigh
                 else sse(prose(300, 13), "答 medium", "stop", 309))
        entry["total"] = len(lines)
        try:
            for number, line in enumerate(lines):
                self.wfile.write(line)
                self.wfile.flush()
                entry["written"] = number + 1
                if xhigh:
                    time.sleep(0.001)
        except (BrokenPipeError, ConnectionResetError):
            entry["gone"] = True
        if xhigh:
            self.done.set()

    def log_message(self, *_: object) -> None:
        pass


def check_http() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Looping)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    try:
        client = pp.Client(endpoint, "stand-in", "xhigh", 30, loop_detector=RULE)
        ledger = []
        pp._CALL_LEDGER.set(ledger)
        try:
            text, m = client.ask_answering("s", "t", None, 16000, kind="adjudicate")
        finally:
            pp._CALL_LEDGER.set(None)
        Looping.done.wait(10)
    finally:
        server.shutdown()
        server.server_close()
    first = Looping.record[0]
    check("over HTTP: the loop given up, medium answers", (text, m.get("loopCuts")), ("答 medium", 1))
    check("... both requests streamed", [r["body"].get("stream") for r in Looping.record], [True, True])
    check("... the client closed the connection where the loop was found; the server had lines left to send",
          (first["gone"], first["written"] < first["total"] // 2), (True, True))
    check("... the ledger: the xhigh rung given up at the server's count",
          (ledger[0].get("loopWatched"), bool(ledger[0].get("loopCut")),
           SERVED_AT <= ledger[0]["completion_tokens"] < SERVED_AT + RULE.step + 8), (True, True, True))


def raised(read) -> str:
    """What READ() did: "read", or the name of what it raised (a ProofreadError's first words)."""
    try:
        read()
    except pp.ProofreadError as error:
        return "ProofreadError: " + str(error)[:20]
    except Exception as error:  # noqa: BLE001 - which one is what is checked
        return type(error).__name__
    return "read"


def check_stream_faults() -> None:
    """What a stream can carry besides events, and what may go wrong in it,
    never costs an answer silently: passed over when it holds nothing, else
    the request asked again (a dropped connection, retried)."""
    client = reader()
    thought, answer = prose(700, 21), "答：甲乙丙"
    lines = sse(thought, answer, "stop", 720)
    def read(stream: list) -> dict:
        """The payload read from STREAM, or {"raised": the name of what reading it raised}."""
        try:
            return client._read_stream(Lines(stream))
        except Exception as error:  # noqa: BLE001 - which one is what is checked
            return {"raised": type(error).__name__}

    def said(payload: dict) -> tuple:
        if "raised" in payload:
            return (payload["raised"],)
        return (payload["choices"][0]["message"]["content"], payload["choices"][0]["finish_reason"],
                payload.get("usage"))

    noisy = [b": keep-alive\n\n", *lines[:5], b"data:\n\n", b": keep-alive\n\n", *lines[5:]]
    whole = (answer, "stop", {"prompt_tokens": 10, "completion_tokens": 720,
                              "completion_tokens_details": {"reasoning_tokens": len(thought)}})
    check("stream noise: comments (keep-alive) and an event with no data are passed over, the answer read",
          said(read(noisy)), whole)
    crlf = [line.replace(b"data: ", b"data:").replace(b"\n", b"\r\n") for line in lines]
    check("... CRLF line ends and data: without a space", said(read(crlf)), whole)
    broken = [*lines[:6], b'data: {"choices": [{"delta": {"content": "\xe7\x94\xb2"\n\n', *lines[6:]]
    check("an event that is not JSON: the request asked again (a dropped connection), no answer read without it",
          raised(lambda: client._read_stream(Lines(broken))), "ConnectionError")
    check("... nor one that is not UTF-8", raised(lambda: client._read_stream(Lines(
        [*lines[:6], b"data: {\"choices\": [\xff\xfe]}\n\n", *lines[6:]]))), "ConnectionError")
    check("... nor one that is JSON but not an object", raised(lambda: client._read_stream(Lines(
        [*lines[:6], b"data: [1, 2]\n\n", *lines[6:]]))), "ConnectionError")
    # The finish event, then the stream ends: no usage event, no [DONE].
    check("a stream that ends after the finish, before usage and [DONE]: the answer kept, "
          "the server's live count its tokens",
          said(read(lines[:-2])), (answer, "stop", {"prompt_tokens": 10, "completion_tokens": len(thought)}))
    check("... and one that ends before the finish: asked again",
          raised(lambda: client._read_stream(Lines(lines[:-4]))), "ConnectionError")


class Chunked(BaseHTTPRequestHandler):
    """A stand-in model server that streams as ninfer's HTTP library does
    (HTTP/1.1, chunked, one chunk an event, keep-alive comments): its first
    answer has an event that cannot be read, its second is whole."""
    protocol_version = "HTTP/1.1"
    record: list = []
    first_done = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        self.rfile.read(int(self.headers["Content-Length"]))
        first = not self.record
        entry = {"gone": False}
        self.record.append(entry)
        lines = sse(prose(600, 31), "答 chunked", "stop", 610)
        lines = [b": keep-alive\n\n", *lines]
        if first:
            # After the unreadable event, reasoning for as long as the client
            # stays (at most 10 s): whether it closed the connection is what
            # is checked, whatever the machine's load.
            more = (event({"reasoning_content": "."}) for _ in range(10_000))
            lines = [*lines[:40], b'data: {"choices": [{"delta": {"reasoning_content": "x"\n\n', *more]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for line in lines:
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n")
                self.wfile.flush()
                if first:
                    time.sleep(0.001)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            entry["gone"] = True
        self.close_connection = True
        if first:
            self.first_done.set()

    def log_message(self, *_: object) -> None:
        pass


def check_http_faults() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Chunked)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"
    sleep = pp.time.sleep
    pp.time.sleep = lambda seconds: None     # the retry's back-off, not what is checked
    try:
        client = pp.Client(endpoint, "stand-in", "xhigh", 30, loop_detector=RULE)
        answers: list = []
        got = raised(lambda: answers.append(client.ask("s", "t", None, 16000, kind="adjudicate")))
        Chunked.first_done.wait(15)
    finally:
        pp.time.sleep = sleep
        server.shutdown()
        server.server_close()
    check("over HTTP, chunked: an event that cannot be read is asked again (the first connection closed), "
          "the answer read the next time",
          (got, [r["gone"] for r in Chunked.record], answers[0][0] if answers else None),
          ("read", [True, False], "答 chunked"))


def main() -> int:
    check_rule()
    check_reader()
    check_stream_faults()
    check_request()
    check_ladder()
    check_changes()
    check_page()
    check_places()
    check_http()
    check_http_faults()
    print(f"{'FAIL' if failures else 'ok  '} {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
