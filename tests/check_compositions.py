#!/usr/bin/env python3
"""Offline checks of where round 3's features meet, merged from three branches.

No model, no GPU, no corpus: made-up pages and books (tests/stand_in.py), the
real client asking a model server in this process that answers from the
request (stand_in.Served), and stores in temporary directories.

- The running-head band (furniture_band, which the repair's book model
  takes): its folios and anchors, what joins an anchor's column or row and
  what does not (a folio extends the band nowhere; a body column under a
  folio; blocks too wide, tall or long; a title at another type size; a
  label alone), and a table block is never in it; furniture_text.
- The table-edge text: the text written beside a table (engine B's title
  above it, with its marks) once, before the table, and sealed; no census or
  punctuation pass reads a table block.  The edge text writes each engine's
  marks in the order written: brackets with no character between them (a
  bracketed Arabic numeral, empty brackets at the table's edge) come out in
  order, not backwards or with one bracket.
- The padding of images too narrow for the server and whatever sends images:
  both rungs of a ladder whose first rung was cut send the image padded and
  the ledger says so on each; request_body is the body ask() sends.

Usage:
    python3 tests/check_compositions.py
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

failures = 0
SCRATCH = Path(tempfile.mkdtemp(prefix="compositions-check-"))


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def html(rows: list[list[str]]) -> str:
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"


def png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# --- the running-head band and tables ----------------------------------------------------

HEAD = "某省財政說明書歲入部"
# A small table printed in the running head's row, at the top of the page.
TABLE_BOX = [0.55, 0.015, 0.40, 0.05]
PRINTED = [["甲", "一", "二"], ["乙", "三", ""], ["丙", "四", "五"]]


def table_of(markdown: str):
    found = pp._tables.find_tables(markdown)
    return found[0].grid() if found else None


def check_band() -> None:
    """furniture_band and furniture_text on made-up pages."""
    head, body = "某書卷上", "天地玄黃宇宙洪荒"
    blocks = [{"text": "三", "box": [0.74, 0.68, 0.03, 0.02], "label": "number"},
              {"text": "第三", "box": [0.742, 0.40, 0.026, 0.05], "label": "vertical_text"},
              {"text": head, "box": [0.74, 0.18, 0.03, 0.20], "label": "doc_title"},
              {"text": body, "box": [0.60, 0.10, 0.10, 0.68], "label": "vertical_text"},
              {"text": body * 6, "box": [0.74, 0.10, 0.03, 0.68], "label": "vertical_text"}]
    none = SimpleNamespace(profile_data={})
    heads = SimpleNamespace(profile_data={"layout": {"runningHeads": [head]}})
    check("band: a folio extends the band nowhere: the blocks in its column are not the band's",
          pp.furniture_band(blocks, [], none), {0: "folio"})
    check("band: the running head's column: short thin blocks no longer than it; not the body, nor a long "
          "block there", pp.furniture_band(blocks, [head], heads),
          {0: "folio", 2: "running head", 1: "column of block 2"})
    # Text that is the book's furniture, with a second piece of evidence: a
    # folio's column (not the text alone).
    beside = [{"text": "三", "box": [0.74, 0.68, 0.03, 0.02], "label": "number"},
              {"text": head + "一集", "box": [0.74, 0.18, 0.03, 0.20], "label": "vertical_text"},
              {"text": head + "一集", "box": [0.30, 0.18, 0.03, 0.20], "label": "vertical_text"}]
    check("band: furniture text in a folio's column is an anchor; the same text elsewhere is not",
          pp.furniture_band(beside, [head], heads), {0: "folio", 1: "column of block 0, furniture text"})
    # The first body column printed under the folio in the top margin, the
    # running head a row across the top: the folio's column is not the band.
    corner = [{"text": "35", "box": [0.837, 0.159, 0.031, 0.02], "label": "number"},
              {"text": "定他的軍事總策略如下", "box": [0.844, 0.205, 0.032, 0.195], "label": "vertical_text"},
              {"text": "期四第刊月", "box": [0.259, 0.16, 0.525, 0.021], "label": "header"}]
    check("band: a body column under a folio in the top margin is not the band's",
          pp.furniture_band(corner, [], none), {0: "folio"})
    # A wide running head's row: a short block beside it joins; a body line
    # below it does not.
    row = [{"text": head, "box": [0.10, 0.03, 0.30, 0.03], "label": "header"},
           {"text": "第三冊", "box": [0.50, 0.03, 0.08, 0.03], "label": "text"},
           {"text": body, "box": [0.10, 0.10, 0.80, 0.05], "label": "text"}]
    check("band: the row of a wide running head", pp.furniture_band(row, [head], heads),
          {0: "running head", 1: "row of block 0"})
    # What joins a column is short: no longer than the anchor, and thin.
    long = [{"text": head, "box": [0.30, 0.10, 0.40, 0.50], "label": "header"},
            {"text": "第三", "box": [0.30, 0.10, 0.30, 0.70], "label": "text"},
            {"text": "第一二三四五", "box": [0.35, 0.65, 0.05, 0.10], "label": "text"},
            {"text": "第三", "box": [0.35, 0.65, 0.05, 0.10], "label": "text"}]
    check("band: not a block wider or taller than a margin's, nor one longer than the anchor",
          pp.furniture_band(long, [head], heads), {0: "running head", 3: "column of block 0"})
    # A profile that measured its head's type size: a block reading the head
    # far larger (a title) is no anchor; the head with its volume beside it at
    # the head's size is.
    sized = SimpleNamespace(profile_data={"layout": {"runningHeads": [head], "runningHeadGlyph": {head: 0.02}}})
    titles = [{"text": head, "box": [0.74, 0.18, 0.03, 0.08], "label": "vertical_text"},
              {"text": head, "box": [0.50, 0.10, 0.06, 0.30], "label": "paragraph_title"},
              {"text": head + "一集", "box": [0.20, 0.18, 0.03, 0.12], "label": "vertical_text"},
              {"text": head + "一集", "box": [0.10, 0.10, 0.06, 0.45], "label": "paragraph_title"}]
    check("band: the head's type size: a head at it, the head with a volume at it; not the same text as a title",
          pp.furniture_band(titles, [head], sized), {0: "running head", 2: "head's size, furniture text"})
    # A label is not evidence alone: a section heading labelled header, a
    # signature labelled number; a header block reading the running head with
    # a character or two beside it is.
    labelled = [{"text": "第五節某地之事", "box": [0.80, 0.17, 0.03, 0.19], "label": "header"},
                {"text": "某甲序", "box": [0.44, 0.41, 0.03, 0.06], "label": "number"},
                {"text": head + "一集", "box": [0.16, 0.18, 0.023, 0.14], "label": "header"},
                {"text": body, "box": [0.60, 0.10, 0.10, 0.68], "label": "vertical_text"}]
    check("band: a heading labelled header, a signature labelled number: not anchors",
          pp.furniture_band(labelled, [head], heads), {2: "label header, furniture text"})
    check("band: a block read as the profile's running head is an anchor",
          pp.furniture_band(blocks, [head], heads)[2], "running head")
    check("band: no anchor, no band", pp.furniture_band(blocks[1:], [], none), {})
    check("furniture_text: a running head, and one with at most two characters beside it; not a heading",
          [pp.furniture_text(t, [head], frozenset()) for t in (head, head + "一集", head + "第一集", "第五節某地之事", "")],
          [True, True, False, False, False])


def check_band_and_tables() -> None:
    # The band on its own: a small table in the running head's row is not the
    # band's; a text block with the same box and text is.
    heads = type("Arguments", (), {"profile_data": {"layout": {"runningHeads": [HEAD]}}})()
    row = [{"text": HEAD, "box": [0.05, 0.02, 0.40, 0.04], "label": "header"},
           {"text": html(PRINTED), "box": TABLE_BOX, "label": "table"},
           {"text": "甲一二", "box": TABLE_BOX, "label": "text"}]
    check("band: a table block in the running head's row is not the band's",
          pp.furniture_band(row, [HEAD], heads), {0: "running head", 2: "row of block 0"})


# --- The table-edge text -------------------------------------------------------------------

GRID = [["物名", "件數"], ["黑板", "二"], ["粉筆", "十"]]
PROSE_A = "右表所列，各件均係本年添置之物。(2)其價值另表詳之。"
PROSE_B = "右表所列各件均係本年添置之物二其價值另表詳之"
TITLE_B = "某處學堂器具表（單位件）"
# A title whose brackets hold no character: a bracketed Arabic numeral (the
# merged text holds CJK characters alone, so the numeral is not written) and
# empty brackets at the table's edge.
TITLE_NUMBERED_B = "第(2)某處學堂器具表（）"


def edge_page(extra, title=TITLE_B):
    """A table and, below it, engine A's prose block (its marks and a
    numbered head 。(2), engine B reading the numeral as a character); engine
    B read a title (TITLE) with its marks above the table, at the page's
    start, which the table block's span holds.  The census counts engine A's places on the
    prose; the punctuator answers engine A's marks."""
    blocks = [stand_in.block(html(GRID), [0.1, 0.1, 0.8, 0.5], "table"),
              stand_in.block(PROSE_A, [0.1, 0.7, 0.8, 0.2], "text")]
    b_text = title + "\n" + html(GRID) + "\n" + PROSE_B
    model = stand_in.StandIn(census="5\n雙圈：0", judge=lambda a, b: b or a or "無",
                             punctuate=lambda span: "右表所列，各件均係本年添置之物。二其價值另表詳之。")
    real = pp.leader_count
    # The dot runs measured on the crop: none (a blank made-up crop).
    pp.leader_count = lambda crop: 0
    try:
        with stand_in.book({1: (blocks, b_text)}) as root:
            record, markdown = stand_in.run(root, model, extra=extra)[1]
    finally:
        pp.leader_count = real
    return record, markdown, model.kinds()


def from_table_edge(markdown: str, title: str = TITLE_B) -> str:
    """The page from the table's edge text (the title's line, TITLE as
    written) to the table's end."""
    at = markdown.find(title)
    found = pp._tables.find_tables(markdown)
    return markdown[at:found[0].end] if at >= 0 and found else ""


def check_table_edge_text() -> None:
    off, off_md, kinds = edge_page([])
    check("the prose is punctuated, the table block is not",
          ([p["block"] for p in off["punctuationPasses"]], "punctuate" in kinds), ([1], True))
    check("the edge text written once before the table, with engine B's marks",
          (off_md.count(TITLE_B), table_of(off_md)), (1, GRID))
    check("the edge text is there at all", bool(from_table_edge(off_md)), True)
    edge = lambda record: [c for c in record["decisionChanges"] if c["rule"] == "table-edge-text"]  # noqa: E731
    check("the table-edge-text entry and doubt are sealed",
          (len(edge(off)), len([d for d in off["doubts"] if d["kind"] == "table-edge-text"])), (1, 1))
    # Brackets with no character between them: beside the table they are
    # written in the order engine B wrote them (marks_as_written).  Before,
    # the title came out 第）（某處學堂器具表）.
    written = "第（）某處學堂器具表（）"
    off, off_md, _ = edge_page([], TITLE_NUMBERED_B)
    check("a title whose brackets hold no character: written in order beside the table",
          (from_table_edge(off_md, written).split("\n")[0], table_of(off_md)), (written, GRID))
    check("a title whose brackets hold no character: the table-edge-text entry as written",
          [b["written"] for c in edge(off) for b in c["evidence"]["beside"]], [written])


# --- the client over HTTP ----------------------------------------------------------------

class Transport(pp.Client):
    """The real client (its ladder, its body, its ledger, its transport)
    asking a model server in this process (stand_in.Served) that answers from
    the request: a question needs NEEDS[user] or DEFAULT completion tokens at
    xhigh and below it, cut ("length") where max_tokens is less; xhigh
    answers XHIGH(user), a lower rung LOWER(user).  Every body sent is kept
    in SENT, as the server read it."""

    def __init__(self, needs=None, default=(2000, 500), xhigh=None, lower=None):
        super().__init__(stand_in.served().endpoint(self.respond), "stand-in", "xhigh", 30)
        self.needs = needs or {}
        self.default = default
        self.xhigh = xhigh or (lambda user: "判定：不同")
        self.lower = lower or (lambda user: "判定：異體")
        self.sent: list[dict] = []

    def respond(self, body):
        self.sent.append(body)
        user = body["messages"][1]["content"][0]["text"]
        top = body.get("reasoning_effort") == "xhigh"
        need = self.needs.get(user, self.default)[0 if top else 1]
        if body["max_tokens"] < need:
            return stand_in.reply("", "length", body["max_tokens"])
        return stand_in.reply((self.xhigh if top else self.lower)(user), "stop", need)


# --- the padding and what sends images ---------------------------------------------------

def image_of(body: dict):
    from PIL import Image
    parts = [c for c in body["messages"][1]["content"] if c.get("type") == "image_url"]
    if not parts:
        return None
    return Image.open(io.BytesIO(base64.b64decode(parts[0]["image_url"]["url"].split(",", 1)[1])))


def aspect(image) -> float:
    return max(image.size) / min(image.size)


def check_padding_and_senders() -> None:
    from PIL import Image, ImageDraw
    thin = Image.new("L", (4, 1000), 236)
    ImageDraw.Draw(thin).rectangle((1, 50, 2, 950), fill=20)
    crop = png(thin)
    limit = pp.MEDIA_ASPECT_LIMIT

    # A ladder: the first rung cut at max_tokens, the next answering; both
    # requests carry the crop padded, and the ledger marks both.
    client = Transport(xhigh=lambda u: "定案：甲", lower=lambda u: "定案：乙")
    ledger: list[dict] = []
    ledger_token = pp._CALL_LEDGER.set(ledger)
    try:
        text, metrics = client.ask_answering(pp.ADJUDICATE_SYSTEM, "甲) 某\n乙) 某", crop, 1000, kind="adjudicate")
    finally:
        pp._CALL_LEDGER.reset(ledger_token)
    sizes = [image_of(body).size for body in client.sent]
    check("a ladder: the first rung cut, then medium", ([b["reasoning_effort"] for b in client.sent], text),
          (["xhigh", "medium"], "定案：乙"))
    check("a ladder: both rungs send the crop padded to the limit",
          [aspect(image_of(body)) <= limit and size[1] == 1000 for body, size in zip(client.sent, sizes)],
          [True, True])
    check("a ladder: the ledger marks both padded, the first cut",
          [(row["padded"], row["finish_reason"]) for row in ledger], [(True, "length"), (True, "stop")])
    check("a ladder: the summary counts both", pp.summarize_calls(ledger)["adjudicate"]["padded"], 2)
    check("request_body: the body ask() sends, image padded",
          client.request_body(pp.ADJUDICATE_SYSTEM, "甲) 某\n乙) 某", crop, 1000, effort_override="medium"),
          client.sent[1])


def main() -> int:
    check_band()
    check_band_and_tables()
    check_table_edge_text()
    check_padding_and_senders()
    print(f"\n{'FAIL' if failures else 'ok'}: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
