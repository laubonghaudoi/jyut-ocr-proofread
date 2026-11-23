#!/usr/bin/env python3
"""Offline checks of how phase 3 makes its model calls.

No model, no GPU, no corpus: a local stub server that answers like ninfer
(usage plus llama.cpp-style `timings`), and made-up disagreements.  Covers the
per-page call ledger, the global in-flight cap (and the book profile's), the
images as sent (pixel budget, aspect ratio) and the lone-circle rule.

Usage:
    python3 tests/check_calls.py
"""

from __future__ import annotations

import concurrent.futures as cf
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    """Answers every chat request; truncates when max_tokens is below `needs`;
    with `empty`, finishes on its own with nothing but a line break."""

    needs = 0
    empty = False
    delay = 0.0
    live = 0
    peak = 0
    lock = threading.Lock()
    seen: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with Stub.lock:
            Stub.live += 1
            Stub.peak = max(Stub.peak, Stub.live)
            parts = body["messages"][1]["content"]
            images = [p["image_url"]["url"] for p in parts
                      if isinstance(p, dict) and p.get("type") == "image_url"]
            Stub.seen.append({"max_tokens": body["max_tokens"],
                              "effort": body.get("reasoning_effort"),
                              "image": images[0] if images else None})
        time.sleep(Stub.delay)
        truncated = body["max_tokens"] < Stub.needs
        payload = {
            "choices": [{"message": {"content": "" if truncated else "\n" if Stub.empty else "符號：3"},
                         "finish_reason": "length" if truncated else "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": body["max_tokens"] if truncated else 40,
                      "completion_tokens_details": {"reasoning_tokens": 30}},
            "timings": {"cache_n": 100, "prompt_n": 20, "prompt_ms": 5.0, "predicted_ms": 50.0},
        }
        raw = json.dumps(payload).encode()
        with Stub.lock:
            Stub.live -= 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_: object) -> None:
        pass


def serve() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions"


def reset(needs: int = 0, delay: float = 0.0, empty: bool = False) -> None:
    Stub.needs, Stub.delay, Stub.live, Stub.peak, Stub.empty = needs, delay, 0, 0, empty
    Stub.seen = []


def check_ledger(endpoint: str) -> None:
    reset()
    client = pp.Client(endpoint, "stub", "xhigh", 30)
    ledger: list[dict] = []
    pp._CALL_LEDGER.set(ledger)
    with cf.ThreadPoolExecutor(3) as pool:
        pp.in_context(pool, lambda k: client.ask_answering("s", "t", None, 1000, kind=k),
                      ["census", "census", "punctuate"])
    assert [c["kind"] for c in ledger].count("census") == 2, ledger
    call = ledger[0]
    assert call["cached_tokens"] == 100 and call["serverPromptMs"] == 5.0, call
    summary = pp.summarize_calls(ledger)
    assert summary["census"]["n"] == 2 and summary["census"]["cachedTokens"] == 200, summary
    assert summary["punctuate"]["completionTokens"] == 40, summary
    pp._CALL_LEDGER.set(None)
    print("ledger: worker threads record into their page's ledger, with the server's timings")


def check_inflight_cap(endpoint: str) -> None:
    reset(delay=0.15)
    client = pp.Client(endpoint, "stub", None, 30, max_inflight=2)
    with cf.ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: client.ask("s", "t", None, 100), range(8)))
    assert Stub.peak == 2, f"{Stub.peak} requests on the wire with a cap of 2"
    print("in-flight cap: 8 callers, never more than 2 requests at the server")


def check_profile_cap() -> None:
    """book_profile.py's client has at most --workers requests at the server
    at once, as phase 3's has --max-inflight; --no-inflight-cap makes it as
    before, with no cap.  A made-up one-page book and the stand-in model."""
    import book_profile as bp
    import stand_in
    made: list[dict] = []
    real = pp.Client

    def spy(*args, **kwargs):
        made.append(kwargs)
        return stand_in.StandIn(census=0)

    pages = {1: ([stand_in.block("第一章總論此事爲國家計", [0.3, 0.1, 0.4, 0.8])], "第一章總論此事為國家計")}
    pp.Client = spy
    try:
        with stand_in.book(pages) as root:
            for extra in ((), ("--no-inflight-cap",)):
                status = bp.main([str(root / "renders"), str(root / "a"), str(root / "b"),
                                  str(root / "book-profile.json"), "--workers", "3",
                                  "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT, *extra])
                assert status == 0, status
    finally:
        pp.Client = real
    assert [call.get("max_inflight") for call in made] == [3, None], made
    assert "max_inflight" not in made[1], "--no-inflight-cap builds the client as before"
    print("profile cap: book_profile.py's client sends at most --workers requests at once; "
          "--no-inflight-cap as before")


def check_lower_start(endpoint: str) -> None:
    """A retry of a call that answered nothing begins its effort ladder lower
    (ask_lower, NO_ANSWER_RETRY_EFFORT); a client with no ladder is asked as
    usual."""
    reset(needs=20000)
    client = pp.Client(endpoint, "stub", "xhigh", 30)
    text, m = pp.ask_lower(client, pp.NO_ANSWER_RETRY_EFFORT, "s", "t", None, 16000, kind="format-retry")
    assert [s["effort"] for s in Stub.seen] == ["low", None] and m.get("degraded") and not text, Stub.seen
    reset()
    text, m = pp.ask_lower(client, "medium", "s", "t", None, 16000, kind="format-retry")
    assert [s["effort"] for s in Stub.seen] == ["medium"] and text == "符號：3", Stub.seen
    reset()
    client.ask_answering("s", "t", None, 16000, kind="format")
    assert [s["effort"] for s in Stub.seen] == ["xhigh"], Stub.seen
    # Never raised: a client set below the rung begins at its own.
    reset(needs=20000)
    pp.ask_lower(pp.Client(endpoint, "stub", None, 30), "low", "s", "t", None, 16000, kind="format-retry")
    assert [s["effort"] for s in Stub.seen] == [None], Stub.seen

    class Plain:
        def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
            return "答", {}

    assert pp.ask_lower(Plain(), "low", "s", "t", None, 100, kind="format-retry") == ("答", {})
    print("effort: a call that answered nothing is asked again from a lower rung")


def check_no_answer_reason(endpoint: str) -> None:
    """no_answer says which way an answer was none, from the rungs
    ask_answering counted: cut off at the token budget at every effort, or
    a reply that finished on its own with nothing in it (degraded after one
    rung, cut at none - the shape of a real title page's structure pass,
    which the reason once called cut off)."""
    client = pp.Client(endpoint, "stub", "xhigh", 30)
    reset(needs=20000)
    text, m = client.ask_answering("s", "t", None, 16000, kind="format")
    assert (m["rungsAsked"], m["rungsCutOff"]) == (4, 4) and m["degraded"], m
    assert pp.no_answer(text, m) == "no answer: cut off at the token budget at every effort, nothing written"
    assert pp.no_answer_cut(pp.no_answer(text, m))
    reset(empty=True)
    text, m = client.ask_answering("s", "t", None, 16000, kind="format")
    assert [s["effort"] for s in Stub.seen] == ["xhigh"] and m["degraded"], Stub.seen
    assert (m["rungsAsked"], m["rungsCutOff"]) == (1, 0), m
    assert pp.no_answer(text, m) == "no answer: the reply was empty"
    assert not pp.no_answer_cut(pp.no_answer(text, m))
    # Cut off, then empty on its own a rung lower.
    assert pp.no_answer("", {"degraded": True, "rungsAsked": 2, "rungsCutOff": 1}) == (
        "no answer: cut off at the token budget at 1 effort(s), then an empty reply, nothing written")
    print("no answer: a cut at every effort and an empty reply are told apart")


def check_media_budget() -> None:
    """An image above the server's pixel budget is scaled down to fit; others go unchanged."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (400, 300), 255).save(buf, "PNG")
    png = buf.getvalue()
    assert pp.fit_media_budget(png, limit=120_000) == png, "an image within the budget was changed"
    small = pp.fit_media_budget(png, limit=30_000)
    with Image.open(io.BytesIO(small)) as image:
        w, h = image.size
    assert w * h <= 30_000 and abs(w / h - 4 / 3) < 0.02, (w, h)
    print("media budget: an image above the pixel budget is scaled down to fit, aspect kept; others unchanged")


def check_media_aspect(endpoint: str) -> None:
    """An image whose long side is more than MEDIA_ASPECT_LIMIT times its short
    side (the server refuses more than 200 with an HTTP 400, and the page is
    lost) is padded on its short side with its own paper, centred, never
    scaled; the call ledger counts it.  Every other image is sent as given."""
    import base64
    import io
    from PIL import Image, ImageDraw

    def png(image: "Image.Image") -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    def aspect(size: tuple[int, int]) -> float:
        return max(size) / min(size)

    limit = getattr(pp, "MEDIA_ASPECT_LIMIT", 100)
    assert limit <= 100, f"the limit {limit} leaves no margin under the server's 200"
    # A made-up crop 5 px wide and 2000 high: grey paper, a dark stroke down its middle.
    thin = Image.new("L", (5, 2000), 236)
    ImageDraw.Draw(thin).rectangle((2, 100, 2, 1900), fill=20)
    reset()
    client = pp.Client(endpoint, "stub", None, 30)
    ledger: list[dict] = []
    pp._CALL_LEDGER.set(ledger)
    ordinary = png(Image.new("RGB", (484, 688), (255, 255, 255)))
    client.ask("s", "t", png(thin), 100, kind="adjudicate")
    client.ask("s", "t", ordinary, 100, kind="adjudicate")
    client.ask("s", "t", None, 100, kind="census")
    pp._CALL_LEDGER.set(None)
    sent = [base64.b64decode(s["image"].split(",", 1)[1]) if s["image"] else None for s in Stub.seen]
    with Image.open(io.BytesIO(sent[0])) as image:
        padded = image.convert("L")
    w, h = padded.size
    assert aspect((w, h)) <= limit and h == 2000, f"sent {w}x{h}: aspect {aspect((w, h)):.0f}"
    left = (w - 5) // 2
    assert padded.crop((left, 0, left + 5, 2000)).tobytes() == thin.tobytes(), "the crop itself was changed"
    assert set(padded.crop((0, 0, left, 2000)).getdata()) | set(
        padded.crop((left + 5, 0, w, 2000)).getdata()) == {236}, "the padding is not the crop's own paper"
    assert sent[1] == ordinary, "an image within the limit was changed"
    assert sent[2] is None
    assert [c["padded"] for c in ledger] == [True, False, False], ledger
    summary = pp.summarize_calls(ledger)
    assert summary["adjudicate"]["padded"] == 1 and summary["census"]["padded"] == 0, summary
    # The same crop laid on its side, in colour: padded above and below.
    wide = Image.new("RGB", (2000, 5), (250, 245, 230))
    with Image.open(io.BytesIO(pp.fit_media_aspect(png(wide)))) as image:
        assert image.width == 2000 and aspect(image.size) <= limit, image.size
        assert image.getpixel((0, 0)) == (250, 245, 230), image.getpixel((0, 0))
    # At the limit and below it: the very bytes given.
    for size in ((400, 300), (10, 10 * limit), (10 * limit, 10), (1, 1)):
        given = png(Image.new("RGB", size, (255, 255, 255)))
        assert pp.fit_media_aspect(given) == given, size
    print(f"media aspect: a 5x2000 crop is sent padded with its paper to {w}x{h}, counted in the ledger; "
          "images within the limit are sent byte for byte")


def check_lone_circle() -> None:
    prose, table = {"label": "text"}, {"label": "table"}
    def seg(a: str, b: str, before: str = "維持", after: str = "庶保") -> dict:
        return {"tag": "replace" if a and b else ("delete" if a else "insert"),
                "a": a, "b": b, "before": before, "after": after}
    assert pp.is_lone_circle(seg("〇", ""), prose)
    assert pp.is_lone_circle(seg("", "〇"), prose)
    assert not pp.is_lone_circle(seg("〇〇", ""), prose), "two circles: a withheld name (張〇〇)"
    assert not pp.is_lone_circle(seg("〇", "", before="一九"), prose), "a zero inside a year"
    assert not pp.is_lone_circle(seg("〇", "", after="五年"), prose), "a zero before a numeral"
    assert not pp.is_lone_circle(seg("〇", ""), table), "a zero in a table cell"
    assert not pp.is_lone_circle(seg("〇", ""), None), "a block that cannot be found"
    assert not pp.is_lone_circle(seg("〇", "一"), prose), "two readings: a real numeral dispute"
    assert not pp.is_lone_circle(seg("〇〇〇", ""), prose)
    assert not pp.is_lone_circle(seg("二", ""), prose)
    print("circle rule: a lone circle in prose is a mark; zeros by numerals, in tables or unplaced are not")


def check_edge_circle_split() -> None:
    def seg(a: str, b: str, before: str = "候斧鉞", after: str = "滿人云") -> dict:
        return {"tag": "replace", "a": a, "b": b, "before": before, "after": after}
    parts, at = pp.split_edge_circle(seg("〇旂", "旒"))
    assert at == 0 and [(p["a"], p["b"]) for p in parts] == [("〇", ""), ("旂", "旒")], parts
    assert parts[0]["after"].startswith("旂") and parts[1]["before"].endswith("鉞〇"), parts
    parts, at = pp.split_edge_circle(seg("旂〇", "旒"))
    assert at == 1 and [(p["a"], p["b"]) for p in parts] == [("旂", "旒"), ("〇", "")], parts
    assert parts[1]["before"].endswith("旂") and parts[0]["after"].startswith("〇"), parts
    # The circle on engine B's side: A's stream has no circle, so contexts carry none.
    parts, at = pp.split_edge_circle(seg("旂", "〇旒"))
    assert at == 0 and [(p["a"], p["b"]) for p in parts] == [("", "〇"), ("旂", "旒")], parts
    assert parts[0]["after"].startswith("旂") and parts[1]["before"] == "候斧鉞", parts
    assert pp.split_edge_circle(seg("〇旂", "〇旒")) is None, "both engines read the circle"
    assert pp.split_edge_circle(seg("〇〇旂", "旒")) is None, "two circles stay together"
    assert pp.split_edge_circle(seg("旂", "旒")) is None
    # A numeral beside the circle in either engine's reading: no split, the
    # circle may be a zero (A 一九工, B 一九二〇 - A's context would not show it).
    assert pp.split_edge_circle(seg("〇五", "六")) is None
    assert pp.split_edge_circle(seg("工", "二〇", before="民國一九", after="年")) is None
    assert pp.split_edge_circle(seg("工〇", "二", before="民國一九", after="年")) is None
    assert pp.split_edge_circle(seg("甲乙丙丁", "計銀二百一〇甲乙丙丁"[:6])) is None
    print("edge circles: a circle fused with a substitution splits off; pairs and shared circles do not")


def check_multimodal_placeholders_and_img_tags(endpoint: str) -> None:
    client = pp.Client(endpoint, "stub", "xhigh", 30)
    body = client.request_body(
        "sys <image> <|vision_start|> ok",
        "draft <image> <|image_pad|> <|vision_end|> <video> <audio> <table><tr><td>正文</td></tr></table>",
        None,
        512,
    )
    sys_txt = body["messages"][0]["content"]
    usr_txt = body["messages"][1]["content"][0]["text"]
    assert "<image>" not in sys_txt and "<|vision_start|>" not in sys_txt, sys_txt
    assert "< image >" in sys_txt and "< |vision_start| >" in sys_txt, sys_txt
    for tok in ("<image>", "<|image_pad|>", "<|vision_end|>", "<video>", "<audio>"):
        assert tok not in usr_txt, (tok, usr_txt)
    assert "<table><tr><td>正文</td></tr></table>" in usr_txt, usr_txt
    cleaned = pp.strip_paddlex_img_tags(
        '前文\n<img src="imgs/img_in_seal_1.jpg" alt="Image" />\n<table><td><img src="imgs/img_in_2.jpg" alt="Image"" /></td><td>後文</td></table>'
    )
    assert cleaned == "前文\n<table><td></td><td>後文</td></table>", repr(cleaned)
    print("safety: Client.request_body neutralizes multimodal placeholders and strip_paddlex_img_tags removes img_in tags")


if __name__ == "__main__":
    server, endpoint = serve()
    try:
        check_ledger(endpoint)
        check_inflight_cap(endpoint)
        check_lower_start(endpoint)
        check_no_answer_reason(endpoint)
        check_media_aspect(endpoint)
        check_multimodal_placeholders_and_img_tags(endpoint)
    finally:
        server.shutdown()
    check_profile_cap()
    check_media_budget()
    check_lone_circle()
    check_edge_circle_split()
    print("all call checks passed")
