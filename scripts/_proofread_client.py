"""HTTP/packet transport Client, media budget sizing, and effort policy for proofread_pages."""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import inspect
import io
import ipaddress
import json
import math
import os
import sys
import threading
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _common import sha256_bytes  # noqa: E402
import _loop_watch  # noqa: E402
from _proofread_core import (  # noqa: E402
    CJK, LOCAL_AGENT_ENV, MEDIA_PIXEL_LIMIT, ProofreadError, QWEN_CODE_MODEL_ENV, SITE_PLACE_KEYS,
    neutralize_multimodal_placeholders, _CALL_LEDGER, _CALL_SITE, _LOOP_CUTS, _large_image_guard,
)


_MEDIA_BUDGET_CACHE_MAX = 8
_MEDIA_BUDGET_CACHE_LOCK = threading.Lock()
_MEDIA_BUDGET_CACHE: OrderedDict[tuple[str, int], bytes] = OrderedDict()


def fit_media_budget(image_bytes: bytes, limit: int = MEDIA_PIXEL_LIMIT) -> bytes:
    """The image as sent: unchanged within the budget, else scaled down (aspect kept) to fit."""
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        if width * height <= limit:
            return image_bytes
        key = (sha256_bytes(image_bytes), limit)
        with _MEDIA_BUDGET_CACHE_LOCK:
            cached = _MEDIA_BUDGET_CACHE.get(key)
            if cached is not None:
                _MEDIA_BUDGET_CACHE.move_to_end(key)
                return cached
        scale = math.sqrt(limit / (width * height))
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        out = io.BytesIO()
        with _large_image_guard(width * height):
            small = image.convert("RGB" if image.mode not in ("1", "L", "RGB") else image.mode).resize(size, Image.LANCZOS)
            small.save(out, "PNG")
    scaled = out.getvalue()
    with _MEDIA_BUDGET_CACHE_LOCK:
        _MEDIA_BUDGET_CACHE[key] = scaled
        _MEDIA_BUDGET_CACHE.move_to_end(key)
        while len(_MEDIA_BUDGET_CACHE) > _MEDIA_BUDGET_CACHE_MAX:
            _MEDIA_BUDGET_CACHE.popitem(last=False)
    return scaled


# The vision server also refuses an image whose long side is more than 200 times
# its short side (ninfer's image processor: HTTP 400 invalid_prompt, "image
# aspect ratio must be at most 200"), and the page is lost with it.  Measured: on
# a 488-page book one page was lost to a 裁決 crop 4 px wide and 1,875 high (a
# strip beside a column, measure_columns), enlarged to 16 x 7,500 (upscale_crop).
# Such an image is padded on its short side with its own paper (its modal
# colour, as boundary_crop fills its gap), centred, to MEDIA_ASPECT_LIMIT - half
# the server's limit, for margin.  It is never scaled: shrinking the long side
# shrinks the text on it.  Every other image is sent as given, byte for byte.
MEDIA_ASPECT_LIMIT = 100


def fit_media_aspect(image_bytes: bytes, limit: float = MEDIA_ASPECT_LIMIT) -> bytes:
    """The image as sent: unchanged within LIMIT, else its short side padded
    with its own paper, the image centred, to LIMIT."""
    from PIL import Image
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
        if max(width, height) <= limit * min(width, height):
            return image_bytes
        out = io.BytesIO()
        with _large_image_guard(width * height):
            source = image.convert("L" if image.mode in ("1", "L") else "RGB")
            histogram = source.histogram()
            paper = tuple(max(range(256), key=lambda level: histogram[256 * band + level])
                          for band in range(len(source.getbands())))
            side = math.ceil(max(width, height) / limit)
            size = (side, height) if width < height else (width, side)
            padded = Image.new(source.mode, size, paper if len(paper) > 1 else paper[0])
            padded.paste(source, ((size[0] - width) // 2, (size[1] - height) // 2))
            padded.save(out, "PNG")
    return out.getvalue()


# Switch --effort-policy (HTTP only; off unless given, and then nothing here
# changes): the rung of the effort ladder a KIND of question is first asked
# on, in place of the client's own effort (--reasoning-effort).  The client's
# effort stays the highest rung any call is asked on - a kind's rung only
# lowers it - and the ladder still only steps down from there
# (Client.ask_answering): a kind begun on low that is cut off at max_tokens is
# asked once more with no reasoning.  A retry is a kind of its own
# (punctuate-retry, format-retry, table-rebuild-retry, the -correction of a
# fidelity question), so a policy that names only the first question leaves
# its retry on the client's effort: the retry of a refused answer is asked
# higher, with no rule of its own.  Which kinds may begin lower without
# costing characters or marks is not known: the policy exists so that it can
# be measured, kind by kind, against the golden pages (tests/run_regression.py).
# It is recorded with the page (effortPolicy, beside reasoningEffort) and, as
# the effort is, never compared on a resume nor bound into a packet request:
# it changes how hard the model thinks, not what it is asked.
EFFORT_POLICY_ENV = "PROOFREAD_EFFORT_POLICY"
# The rungs of the effort ladder, highest first (Client.EFFORT_LADDER; None:
# no reasoning asked for).  Here, not read off Client: the checks put a
# stand-in in Client's place.
EFFORT_RUNGS: tuple[str | None, ...] = ("xhigh", "medium", "low", None)
# Every kind an HTTP call is asked as (the kind= of each ask_answering call of
# proofread_pages.py and book_profile.py, and _content_fidelity's three with
# their one correction); tests/check_effort_policy.py holds the list to them.
EFFORT_KINDS = (
    "adjudicate", "coverage-read", "cell-direction", "orient", "record-order",
    "table-rebuild", "table-rebuild-retry", "census", "census+punctuate", "punctuate", "punctuate-retry", "punctuate-shape",
    "writer", "format", "format-retry", "notation",
    "page-inventory", "page-inventory-correction", "content-recovery", "content-recovery-correction",
    "coverage-review", "coverage-review-correction", "note-numbers",
)


def parse_effort_policy(text: str | None) -> dict[str, str | None]:
    """--effort-policy's value as {kind: rung}: KIND=RUNG pairs with commas
    between, KIND one of EFFORT_KINDS and RUNG a rung of the effort ladder
    (xhigh, medium, low, or none for no reasoning).  Empty: no policy.  A
    kind or rung it does not know is refused before any page is asked, since
    a misspelt kind would otherwise run a whole book on the client's effort
    unnoticed."""
    rungs = {str(rung or "none"): rung for rung in EFFORT_RUNGS}
    policy: dict[str, str | None] = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        kind, separator, rung = (piece.strip() for piece in part.partition("="))
        if not separator or kind not in EFFORT_KINDS or rung.lower() not in rungs:
            raise ProofreadError(
                f"--effort-policy: {part.strip()!r} is not KIND=RUNG; kinds: {', '.join(EFFORT_KINDS)}; "
                f"rungs: {', '.join(rungs)}")
        if kind in policy and policy[kind] != rungs[rung.lower()]:
            raise ProofreadError(f"--effort-policy names {kind} twice, with two rungs")
        policy[kind] = rungs[rung.lower()]
    return policy


def effort_policy_text(policy: Mapping[str, str | None] | None) -> str:
    """A policy as it is recorded (effortPolicy) and as --effort-policy takes
    it again: its pairs in the order of their kinds; "" for none."""
    return ",".join(f"{kind}={(policy or {})[kind] or 'none'}" for kind in sorted(policy or {}))


def effort_policy_record(client: Any) -> dict[str, str]:
    """The effortPolicy of a page's record or a run report: the client's
    policy, only when it has one (an HTTP client given --effort-policy)."""
    text = effort_policy_text(getattr(client, "effort_policy", None))
    return {"effortPolicy": text} if text else {}


class Client:
    """Minimal chat client. Retries only on transport errors, never on HTTP 4xx."""

    def __init__(self, endpoint: str, model: str | None, effort: str | None, timeout: float,
                 temperature: float = 0.0, max_inflight: int | None = None,
                 loop_detector: _loop_watch.Rule | None = None,
                 effort_policy: Mapping[str, str | None] | None = None):
        self.endpoint = endpoint
        # No model named: the server's first listed one (resolve_http_model).
        self.model = resolve_http_model(endpoint, model, timeout)
        self.effort = effort
        # Switch --effort-policy: the rung each kind named begins on (above).
        self.effort_policy: dict[str, str | None] = dict(effort_policy or {})
        self.timeout = timeout
        self.temperature = temperature
        # Requests on the wire at once, across every page and every page's
        # worker threads.  The server queues what it cannot admit and answers
        # 503 after its pending timeout, which this client treats as fatal, so
        # the queue is kept here instead, where waiting costs nothing.
        self.inflight = threading.BoundedSemaphore(max_inflight) if max_inflight else None
        # Switch --loop-detector: every request is streamed and its reasoning
        # watched for a word-for-word loop by this rule (_loop_watch); None:
        # off, and requests are sent unstreamed.
        self.loop_detector: _loop_watch.Rule | None = loop_detector

    def request_body(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
    ) -> dict[str, Any]:
        """The JSON body of one request, as ask() sends it."""
        return self._request(system, user_text, image_bytes, max_tokens, effort_override)[0]

    def _request(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
    ) -> tuple[dict[str, Any], bool]:
        """The JSON body of one request, as ask() sends it (request_body), and
        whether its image was padded to the server's aspect ratio
        (fit_media_aspect), which the call ledger records."""
        system = neutralize_multimodal_placeholders(system)
        user_text = neutralize_multimodal_placeholders(user_text)
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        padded = False
        if image_bytes is not None:
            # Padded first, then scaled to the budget, which keeps the aspect
            # within a pixel: padding after scaling could take it over the budget.
            shaped = fit_media_aspect(image_bytes)
            padded = shaped is not image_bytes
            b64 = base64.b64encode(fit_media_budget(shaped)).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}
            )
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            # Greedy by default.  Two reasons, and neither is about quality.
            # Transcription wants the most likely reading, not a sample from the
            # distribution.  And without it nothing here is reproducible: an A/B
            # over six pages returned exactly opposite results on two runs of
            # the SAME code, and the sealed SHA-256 provenance could never be
            # checked by re-running.  Measured: greedy gives byte-identical
            # output three times out of three, default sampling three different
            # answers out of three.
            "temperature": self.temperature,
            "top_p": 1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        effort = self.effort if effort_override == "" else effort_override
        if effort:
            body["reasoning_effort"] = effort
        if self.loop_detector is not None:
            # Streamed, so the reasoning can be watched as it comes and the
            # request given up in a loop (_read_stream): the usage at the end
            # (stream_options), and the server's own count of the tokens
            # generated on every event (timings_per_token, as llama.cpp's
            # server and ninfer take it), so a request given up says what
            # it spent.
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
            body["timings_per_token"] = True
        return body, padded

    def ask(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        effort_override: str | None = "",
        kind: str = "other",
    ) -> tuple[str, dict[str, Any]]:
        """One request.

        With the loop detector on (--loop-detector) the request is streamed
        and given up in a word-for-word loop (_read_stream): it comes back as
        a cut at max_tokens (finish_reason "length"), its metrics say loopCut
        (what was found), and the ledger marks every call watched
        (loopWatched) and a call given up (loopCut) with what it left of its
        max_tokens (loopCutTokensSaved)."""
        body, padded = self._request(system, user_text, image_bytes, max_tokens, effort_override)
        effort = self.effort if effort_override == "" else effort_override
        started = time.monotonic()
        payload, waited = self._send(body, kind)
        del body
        usage = payload.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        timings = payload.get("timings") or {}
        choice = payload["choices"][0]
        loop = payload.get("loopCut")
        metrics = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": details.get("reasoning_tokens", 0),
            "finish_reason": choice.get("finish_reason"),
            "effort": effort,
            **({"loopCut": loop} if loop else {}),
        }
        ledger = _CALL_LEDGER.get()
        if ledger is not None:
            # Wall time from the caller's side; the server's own split (prompt,
            # of which cache_n tokens were reused, then decode) comes from the
            # llama.cpp-style `timings` ninfer adds to every response.  Wall
            # minus both is time spent waiting to be admitted.
            ledger.append({
                "kind": kind, "maxTokens": max_tokens,
                "seconds": round(time.monotonic() - started - waited, 2),
                "waitSeconds": round(waited, 2),
                "serverPromptMs": timings.get("prompt_ms", 0.0) or 0.0,
                "serverDecodeMs": timings.get("predicted_ms", 0.0) or 0.0,
                "cached_tokens": timings.get("cache_n", 0) or 0,
                "padded": padded,
                **metrics,
                # What a loop cut left of the rung's max_tokens: the saving
                # where the loop would have gone on to max_tokens, as 17 of
                # the 19 loops in the measured replays did; the other two
                # ended on their own with no answer (_loop_watch).
                **({"loopWatched": True,
                    **({"loopCutTokensSaved": max(0, max_tokens - (metrics["completion_tokens"] or 0))}
                       if loop else {})}
                   if self.loop_detector is not None else {}),
            })
        return choice["message"].get("content") or "", metrics

    def _send(self, body: dict[str, Any], kind: str) -> tuple[dict[str, Any], float]:
        """POST BODY to the endpoint: the response payload, and the seconds
        spent waiting for a free lane (the in-flight cap).  KIND is the call's
        kind, which the endpoint is not sent; a stand-in model in the checks
        answers by it."""
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("PROOFREAD_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload_body = dict(body)
        stream = bool(payload_body.get("stream"))
        last: Exception | None = None
        waited = 0.0
        for attempt in range(3):
            try:
                queued = time.monotonic()
                if self.inflight:
                    self.inflight.acquire()
                waited += time.monotonic() - queued
                try:
                    # Serialize only after acquiring an in-flight slot so queued
                    # threads do not hold multi-MB base64 JSON payloads in RAM.
                    request = urllib.request.Request(
                        self.endpoint, json.dumps(payload_body).encode(), headers
                    )
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        del request
                        # A streamed request (--loop-detector) is read as one payload.
                        payload = (self._read_stream(response) if stream
                                   else json.loads(response.read().decode()))
                        return payload, waited
                finally:
                    if self.inflight:
                        self.inflight.release()
            except urllib.error.HTTPError as error:
                detail = error.read().decode(errors="replace")[:300]
                if error.code == 400 and "reasoning_effort" in detail and "reasoning_effort" in payload_body:
                    payload_body.pop("reasoning_effort", None)
                    continue
                # A 4xx is a contract problem; retrying sends the same bad request.
                raise ProofreadError(f"HTTP {error.code}: {detail}") from error
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                last = error
                time.sleep(2 * (attempt + 1))
        raise ProofreadError(f"endpoint unreachable after 3 attempts: {last}")

    def _read_stream(self, response: Any) -> dict[str, Any]:
        """A streamed response (switch --loop-detector) as the one payload the
        endpoint answers unstreamed: the reasoning and the answer joined from
        their deltas, the finish reason, and the usage and timings of the last
        events that carried them.  RESPONSE gives the stream's lines (the SSE
        of an OpenAI-compatible endpoint) and has close().

        The reasoning is watched as it comes, the answer never
        (_loop_watch.LoopWatch).  When the reasoning ends in a word-for-word
        loop the connection is closed there - the server stops generating
        when it finds its client gone - and the payload is that of a request
        cut off at its max_tokens: finish_reason "length", no answer, the
        tokens generated so far (the server's own count on the last event,
        timings_per_token; else one per delta, fewer than it generated where
        a delta held several) as completion and reasoning tokens, and
        loopCut: what was found (_loop_watch.loop_at), cutTokens and
        tokensCountedBy ("server" or "deltas").

        A stream that ends before the model finished (no finish reason) is a
        transport failure, retried as a dropped connection is; so is an event
        that cannot be read (not UTF-8, not JSON, not an object): it may hold a
        piece of the answer, and an answer read without it would have lost
        that piece, so the whole request is asked again.  What that assumes:
        one event per data line, as ninfer and llama.cpp's server write them;
        a data line with nothing after "data:" holds no delta and is passed
        over, as are comments (": keep-alive") and blank lines.  A stream
        that finished but ended before its usage event keeps its answer, and
        its tokens are the server's live count on the last event that had one
        (timings_per_token; none without it).  An error event from the server
        is a ProofreadError, as an HTTP error is, and so is a JSON body where
        a stream was asked for (a server that does not stream: asking again
        would generate the whole answer again)."""
        watch = _loop_watch.LoopWatch(self.loop_detector or _loop_watch.RULE)
        reasoning: list[str] = []
        content: list[str] = []
        head: dict[str, Any] = {}
        finish = usage = timings = found = None
        deltas = 0
        for raw in response:
            try:
                line = raw.decode("utf-8").strip()
            except UnicodeDecodeError as error:
                raise ConnectionError(f"an event of the stream is not UTF-8 ({error})") from error
            if line.startswith("{"):
                raise ProofreadError("--loop-detector: the endpoint answered a streamed request with a JSON "
                                     "body, not a stream; it needs a server that streams")
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            if not data:
                continue
            try:
                event = json.loads(data)
            except ValueError as error:
                raise ConnectionError(f"an event of the stream is not JSON ({error}): {data[:80]!r}") from error
            if not isinstance(event, dict):
                raise ConnectionError(f"an event of the stream is not a JSON object: {data[:80]!r}")
            if event.get("error") is not None:
                raise ProofreadError(f"stream error: {json.dumps(event['error'], ensure_ascii=False)[:300]}")
            for key in ("id", "created", "model"):
                if key in event:
                    head.setdefault(key, event[key])
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if thought:
                    reasoning.append(thought)
                    deltas += 1
                    if not content:
                        found = watch.feed(thought)
                if delta.get("content"):
                    content.append(delta["content"])
                    deltas += 1
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
            if found is not None:
                response.close()
                break
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        payload: dict[str, Any] = {**head, "object": "chat.completion"}
        if found is not None:
            counted = (timings or {}).get("predicted_n")
            tokens = counted if isinstance(counted, int) and counted > 0 else deltas
            prompt = ((timings.get("prompt_n") or 0) + (timings.get("cache_n") or 0)) if timings else 0
            payload.update({
                "choices": [{"index": 0, "message": message, "finish_reason": "length"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": tokens,
                          "completion_tokens_details": {"reasoning_tokens": tokens}},
                **({"timings": timings} if timings else {}),
                "loopCut": {**found, "cutTokens": tokens,
                            "tokensCountedBy": "server" if tokens == counted else "deltas"}})
            return payload
        if finish is None:
            raise ConnectionError("the stream ended before the model finished")
        if usage is None and timings and isinstance(timings.get("predicted_n"), int):
            # Finished, but the stream ended before its usage event: the
            # server's live count on the last event that had one.
            usage = {"prompt_tokens": (timings.get("prompt_n") or 0) + (timings.get("cache_n") or 0),
                     "completion_tokens": timings["predicted_n"]}
        payload.update({"choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": usage or {}, **({"timings": timings} if timings else {})})
        return payload

    # Effort ladder used to recover a page that thought past its token budget.
    # Only reasoning_effort works per-request on an OpenAI-compatible endpoint;
    # an Anthropic-style thinking budget is ignored there (verified).
    EFFORT_LADDER = list(EFFORT_RUNGS)

    def ask_answering(
        self,
        system: str,
        user_text: str,
        image_bytes: bytes | None,
        max_tokens: int,
        kind: str = "other",
        start: str | None = "",
    ) -> tuple[str, dict[str, Any]]:
        """ask(), but guarantee the model actually answers.

        With a high reasoning effort a request can spend its whole max_tokens on
        reasoning and return empty content with finish_reason "length" - observed
        losing a page outright in a production run. Retrying the same request is
        pointless; it fails the same way. So each retry steps DOWN the effort
        ladder, which shifts budget from reasoning to the answer. The last rung
        disables reasoning entirely, where an empty answer is a real failure
        rather than a budget artifact.

        START names the rung to begin on instead of the client's effort ("",
        the default): a retry of a call that answered nothing (ask_lower).  It
        only ever lowers the effort: a client set below START begins at its
        own rung.  With no START, a kind the client's policy names
        (--effort-policy) begins on the policy's rung, under the same rule.

        Switch --loop-detector (loop_detector): a rung given up in a
        word-for-word loop (_read_stream) is a cut at max_tokens - the ladder
        goes on to the next rung, the rung is asked and cut off (rungsAsked,
        rungsCutOff).  The answer's metrics count the rungs given up
        (loopCuts) and the page's decisionChanges hold the call
        (loop_cut_changes).
        """
        try:
            own = self.EFFORT_LADDER.index(self.effort)
        except ValueError:
            own = 0
        if start == "" and kind in self.effort_policy:
            start = self.effort_policy[kind]
        try:
            start = own if start == "" else max(own, self.EFFORT_LADDER.index(start))
        except ValueError:
            start = own
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        best: tuple[str, dict[str, Any]] | None = None
        asked = cut = 0
        # The rungs given up in a loop (--loop-detector), in the order asked.
        loops: list[dict[str, Any]] = []

        def noted(text: str, m: dict[str, Any], rung_tokens: int | None) -> tuple[str, dict[str, Any]]:
            if not loops:
                return text, m
            degraded = bool(m.get("degraded"))
            call = {
                "kind": kind, "effort": self.EFFORT_LADDER[own] or "none", "maxTokens": max_tokens,
                "answeredAt": None if degraded else (m.get("effort") or "none"),
                "answerTokens": None if degraded else rung_tokens,
                "rungsAsked": m["rungsAsked"] if degraded else asked + 1,
                "degraded": degraded,
                "questionSha256": hashlib.sha256(
                    system.encode("utf-8") + b"\0" + user_text.encode("utf-8") + b"\0"
                    + (image_bytes or b"")).hexdigest(),
                "answer": text,
                # Where on the page the question is, when its caller says (call_site).
                "site": _CALL_SITE.get(),
            }
            m["loopCuts"] = len(loops)
            notes = _LOOP_CUTS.get()
            if notes is not None:
                notes.append({**call, "loops": loops})
            return text, m

        for rung in self.EFFORT_LADDER[start:]:
            text, m = self.ask(system, user_text, image_bytes, max_tokens, effort_override=rung, kind=kind)
            for k in totals:
                totals[k] += m.get(k, 0)
            truncated = m.get("finish_reason") == "length"
            if m.get("loopCut"):
                # Given up in a loop (--loop-detector): the next rung, as after
                # a cut at max_tokens.
                loops.append({"effort": rung or "none", "maxTokens": max_tokens, **m["loopCut"]})
            if text.strip() and not truncated:
                rung_tokens = m.get("completion_tokens", 0)
                m.update(totals)
                return noted(text, m, rung_tokens)   # complete answer - done
            asked, cut = asked + 1, cut + truncated
            # Keep the longest partial seen, in case every rung truncates.
            if best is None or len(text.strip()) > len(best[0].strip()):
                best = (text, dict(m))
            if not truncated:
                break  # empty for some other reason; stepping down will not help
        text, m = best if best else ("", {})
        m.update(totals)
        m["degraded"] = True           # never silently pass off a truncated page
        # How the ladder ended, for no_answer: the rungs asked and how many of
        # them were cut off at the token budget.  An empty reply that finished
        # on its own is degraded too, after one rung, cut off at none.
        m["rungsAsked"], m["rungsCutOff"] = asked, cut
        return noted(text, m, None)


def loop_cut_changes(notes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The decisionChanges entries of switch --loop-detector: one per call of
    the page with a rung the loop detector gave up in a word-for-word loop of
    its reasoning (Client.ask_answering, _loop_watch).

    Where: SITE_PLACE_KEYS from the call's call_site (the rest of the site
    - crop, range, tile - in the evidence), and questionSha256 tells a call
    with no place from the page's others.  "after" is the answer the ladder
    gave (the longest partial when no rung answered), "afterBy" the rung
    that gave it.  The evidence holds each rung given up: its effort,
    max_tokens, the tokens spent when given up (cutTokens; tokensCountedBy
    says whose count), and the loop found (period, copies, repeatedChars,
    repeatsFrom, atChar: characters of the reasoning).  In order of kind,
    then place, so the order does not follow the threads' timing.
    questionSha256 is the SHA-256 of the question (system prompt, text and
    image): the seal holds no prompt, so it can only be matched against the
    same question asked again (a replay)."""
    def site(note: Mapping[str, Any]) -> Mapping[str, Any]:
        return note.get("site") or {}

    return [{"decision": "loopDetector", "rule": "reasoning-loop", "kind": note["kind"],
             **{key: site(note)[key] for key in SITE_PLACE_KEYS if key in site(note)},
             "after": note["answer"], "afterBy": note["answeredAt"],
             "evidence": {"loops": note["loops"],
                          **{key: note[key] for key in ("maxTokens", "answerTokens", "rungsAsked", "degraded",
                                                        "questionSha256")},
                          **{key: value for key, value in site(note).items() if key not in SITE_PLACE_KEYS}}}
            for note in sorted(notes, key=lambda n: (n["kind"], site(n).get("mergedOffset") is None,
                                                     site(n).get("mergedOffset") or 0, n["questionSha256"],
                                                     n["answer"]))]


def ask_lower(client: "Client", effort: str | None, *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    """client.ask_answering with its effort ladder begun at EFFORT ("" is
    the client's own); a client with no ladder (a stand-in model) is asked
    as it always is."""
    try:
        takes = "start" in inspect.signature(client.ask_answering).parameters
    except (TypeError, ValueError):
        takes = False
    return client.ask_answering(*args, **kwargs, **({"start": effort} if takes else {}))


def no_answer(text: str, metrics: Mapping[str, Any]) -> str | None:
    """Why a structure pass's answer is no answer, or None when it is one:
    nothing written, or cut off at the token budget on every rung of the
    effort ladder (ask_answering's "degraded": the longest partial comes
    back).  Measured on a page whose engine grid was refused, so the
    structure pass had to write the table: all four rungs used their whole
    budget reasoning, the answer was empty, and an empty answer invents no
    character, deletes no run of prose (the page has none) and changes no
    mark - so it passed every check, and the page was sealed as flat text
    with "STRUCTURE PASS REJECTED: characters preserved".

    The reason says which of the two happened, from the rungs ask_answering
    counted: a reply that finished on its own with nothing in it is
    degraded as well, after its first rung, and is no cut.  Measured: a
    title page's structure pass reasoned 2,237 tokens at the top effort and
    answered a bare line break, finish reason "stop", in two runs of four
    (the other two answered at the same effort).  A client that counts no
    rungs (a stand-in) is judged by the answer's own finish reason, and a
    partial answer, which only a cut leaves.  Rungs the loop detector gave
    up (--loop-detector, loopCuts) are cuts, and the reason says how many."""
    if not metrics.get("degraded"):
        return None if text.strip() else "no answer: the reply was empty"
    written = len(CJK(text))
    asked, cut = metrics.get("rungsAsked"), metrics.get("rungsCutOff")
    if cut is None:
        asked = cut = int(metrics.get("finish_reason") == "length" or bool(text.strip()))
    if not cut:
        return "no answer: the reply was empty"
    tail = f" ({written} character(s) written before the cut)" if written else ", nothing written"
    loops = (f" ({metrics['loopCuts']} of them given up in a word-for-word loop)"
             if metrics.get("loopCuts") else "")
    if cut < asked:
        return (f"no answer: cut off at the token budget at {cut} effort(s){loops}, "
                f"then an empty reply{tail}")
    return "no answer: cut off at the token budget at every effort" + loops + tail


def no_answer_cut(reason: str | None) -> bool:
    """Whether no_answer's REASON is a cut at the token budget (the retry
    then begins lower and says it thought too long), not an empty reply."""
    return bool(reason) and "cut off at the token budget" in reason


def loopback_endpoint(endpoint: str | None) -> bool:
    """Whether ENDPOINT's host is this machine (localhost, 127.0.0.0/8, ::1)."""
    try:
        host = (urllib.parse.urlsplit((endpoint or "").strip()).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def agent_is_local_model(model: str | None = None, endpoint: str | None = None,
                         environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Whether the calling agent is the local proofreading model, and why.

    Such an agent must not answer packets: a packet puts the page image into
    the agent's own context, and its questions are the ones the scripts
    would send that same model over HTTP.  LOCAL_AGENT_ENV=1/0 decides;
    otherwise the agent is local when it is Qwen Code (QWEN_CODE_MODEL in its
    shell) running the model proofreading resolves to (MODEL, else
    PROOFREAD_MODEL) served on this machine (ENDPOINT, else
    PROOFREAD_ENDPOINT, on a loopback host)."""
    env = os.environ if environ is None else environ
    forced = (env.get(LOCAL_AGENT_ENV) or "").strip()
    if forced in ("1", "0"):
        return forced == "1", f"{LOCAL_AGENT_ENV}={forced}"
    if forced:
        raise ProofreadError(f"{LOCAL_AGENT_ENV} must be 1 or 0, not {forced!r}")
    agent = (env.get(QWEN_CODE_MODEL_ENV) or "").strip()
    model = (model or env.get("PROOFREAD_MODEL") or "").strip()
    endpoint = (endpoint or env.get("PROOFREAD_ENDPOINT") or "").strip()
    if agent and agent == model and loopback_endpoint(endpoint):
        return True, (f"{QWEN_CODE_MODEL_ENV}={agent} is the proofreading model, served at {endpoint}")
    return False, ""


def endpoint_models_url(endpoint: str) -> str:
    """{endpoint base}/v1/models for an OpenAI-compatible chat endpoint
    (http://host:port/v1/chat/completions -> http://host:port/v1/models)."""
    parts = urllib.parse.urlsplit(endpoint.strip())
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + "/v1/models", "", ""))


def resolve_http_model(endpoint: str, model: str | None, timeout: float = 30.0) -> str:
    """The model an HTTP run asks for: MODEL (--model, else PROOFREAD_MODEL,
    as the parser left it), else the first id the server lists at
    endpoint_models_url.  Never a packet label."""
    if model and model.strip():
        return model.strip()
    url = endpoint_models_url(endpoint)
    headers = {}
    if os.environ.get("PROOFREAD_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['PROOFREAD_API_KEY']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=min(timeout, 30.0)) as response:
            listed = json.loads(response.read().decode("utf-8", errors="replace"))
        ids = [entry.get("id") for entry in (listed.get("data") or []) if isinstance(entry, Mapping)]
        first = next((str(i).strip() for i in ids if isinstance(i, str) and i.strip()), None)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError, AttributeError) as error:
        raise ProofreadError(f"no model named for --backend http (no --model, no PROOFREAD_MODEL) and the "
                             f"server's model list at {url} could not be read ({error}); pass --model") from error
    if not first:
        raise ProofreadError(f"no model named for --backend http (no --model, no PROOFREAD_MODEL) and the "
                             f"server's model list at {url} names none; pass --model")
    return first
