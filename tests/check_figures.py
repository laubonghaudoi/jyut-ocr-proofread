#!/usr/bin/env python3
"""Offline check of the figures kept as pictures: scripts/_figures.py,
finish_book.py's finalize and doubt list, scripts/_deliver.py's X-圖/ and
clean_book.py.

No GPU, no model, no corpus.

- Which figures (page_figures, on made-up engine-A pages): regions labelled
  chart or image, not header_image, footer_image, seal or text; under 2 % of
  the page left out, just over it kept; a page without `regions` gives none;
  regions whose crops would meet (overlapping, or 1.5 % apart) are one
  figure (a third region bridging two joins all three; chart wins the
  label, the earliest afterLine its place), 3 % apart two; numbered in
  reading order; each anchored on the line engine A reads just before it
  when that is a title, else on the nearest figure_title (the line before
  is body text, or there is none).
- plates, on a made-up page of a book printed in simplified script: a
  block engine A reads in traditional script that engine B leaves unread,
  reads in traditional script too or under half of: a plate, its block and
  the garble inside it engine B did not read go, the print inside and the
  caption stay; engine B reading it in simplified script, a traditional
  book, no engine B draft, a block as much in one script as the other, a
  folio: none; grown by the engine A region and the engine B box (any size)
  that meet it, in the region's place; two blocks apart with no print
  between one plate, a caption engine B reads between them two, blocks
  whose crops meet one; a title starts none, but goes inside a plate
  another block starts; of a chain of engine B's specks only those
  meeting the block join it, and the body line beyond them stays; find
  tells the book's script from both engines' drafts.
- engine B's boxes, in any book: one of 2 % of the page or more is a
  picture, the circles inside it engine B did not read go; its boxes only
  on a line of their own, not corner to corner nor upside down; none where
  a line engine A reads in Han characters or with a Latin word lies partly
  in it (a formula block does not count, and goes), under 2 %, or meeting
  an engine A region; two whose crops meet are one; a box's corners are
  no reading of the figures inside it.
- take_out, on a made-up annotated book: a plate's lines go, its image line
  in place of the first, the caption stays, a line 裁決 rewrote goes on the
  letters left of engine A's; a caption engine A read into the plate's
  block stays when engine B's draft holds it in simplified script, a line
  engine B read in traditional script goes; the joins into and out of
  them become paragraph breaks (the boundaries given untouched) and the
  markers then removed glue nothing to the image line; a line from
  inside a paragraph: the image line between blank lines; nothing of a
  plate on the page: nothing taken out, its line at the page's end; place
  leaves a picture put in place of its text; engine A's regions take
  nothing out.
- the crop: 1 % around the box, each side stopped at a text line beyond it
  (one just above, one overlapping the box's edge, one at its side); none
  past the page's edge.
- cut: each picture is the render's pixels inside the crop, lossless; an
  RGB render whose bands are one is kept grey with the same values, a
  coloured one in colour; no render, no picture; an earlier run's picture
  goes, a picture of another name stays; cut again with the render gone,
  the earlier picture goes too.
- place, on a made-up annotated book: the image line after the paragraph
  holding the anchor (with its source in one paragraph),
  two figures sharing it in order, at the page's end without an anchor or
  with one the page does not hold, past a page marker that joins to the end
  of the paragraph that runs on, at the end of the book; the markers then
  removed give the text without figures plus the image lines alone between
  blank lines, and the final audit with --annotated-source passes; body
  text quoting the caption before it (in full, or a caption of only a
  number) does not take the image line.
- doubts.md: one line per figure in its own section (all of them, past the
  ten lines a kind gets), a link to its picture (other image names still
  said as 裁圖), the lines taken out for it, or engine A's lines a plate
  took none of out, with a word that a transcription may remain; the
  order line names the section; a doubt (phase 3's, the repair's, the
  lint's) on a line a plate took out of final.md stays, tagged so (its
  context in either script, a spot ［…］ read with it, past the clipped
  detail), one on text final.md holds or on another page untagged; the
  research reason names the plate.
- the links beside a PDF whose name holds %, #, ?, < or >: percent-encoded,
  so the link reads the folder.
- finish_book.py on tests/repair_fixture.py's book, delivered beside a PDF
  whose name holds spaces and CJK, first without regions (a delivered.json
  with no picture), then with them but without --extract-figures (no
  figure extraction by default, decision 8 of 2026-11-14: no picture, no
  X-圖/, final.md as before), then with --extract-figures, as every run
  below: the RESULT line and the output line,
  final.md the one before plus the image lines, the run-on figure after the
  paragraph the next page continues, the caption's figure after it, the
  same final audit, X.md's links in angle brackets resolving to X-圖/'s
  pictures (the same bytes as figures/), delivered.json recording each
  picture's SHA-256, X-doubts.md linking them in X-圖/, the fifth step
  leaving figures/; again with every figure gone: X-圖/, its records and
  the image lines go; with them back, then: a picture the person changed is
  moved aside and written anew; a figure gone takes its picture, link and
  record with it, one the person changed stays; X-圖/ a link: nothing
  written through it, a WARNING.
- finish_book.py --extract-figures on the made-up book, an engine B box
  round circles phase 3 wrote as page 17's first line, which the repair
  joins to page 16: the
  picture, the line taken out (figures.json), the join a paragraph break in
  finish/boundaries.json, the image line between the two pages'
  paragraphs, the final audit on that copy clean, doubts.md listing the
  line.
- clean_book.py: the default cleanup leaves figures/ and X-圖/, and is
  refused while a picture of figures/ is not the one delivered; --all is
  refused while a picture in X-圖/ is the person's or gone (saying the
  book folder holds the only copy), or X-圖/ lies inside the book folder (a
  link), and otherwise removes the folder and keeps X.md, X-doubts.md and
  X-圖/.
- SKILL.md and the reference say that figures are extracted only when the
  person asks (--extract-figures; by default the text printed in figures
  is transcribed: user decision 8, BENCH's rule), where the pictures are,
  how many, that --all keeps them, what is kept, the plates and engine B's
  boxes, their text taken out and listed, the delivery's links and records,
  and the limits.

Usage:
    python3 tests/check_figures.py
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))
import offline  # noqa: E402,F401  (private stores, closed endpoint)
import check_finish_book as cfb  # noqa: E402
import _deliver  # noqa: E402
import _figures  # noqa: E402
import audit_ocr_markdown  # noqa: E402
import clean_book  # noqa: E402
import finish_book  # noqa: E402
from _ocr_markdown import apply_boundary_contract  # noqa: E402

FAILURES: list[str] = []
PDF_NAME = "海港 年報 第二冊.pdf"
STEM = PDF_NAME[:-4]
FOLDER = f"{STEM}-圖"


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def region(label: str, box: list[float], after: int = 0) -> dict:
    return {"blockLabel": label, "boundingBox": box, "afterLine": after, "blockOrder": -1, "confidence": 0.0}


def figure(page: int, number: int, label: str, box: list[float], anchor: str = "") -> _figures.Figure:
    return _figures.Figure(page, number, label, box, _figures._crop(box, []), anchor)


def line(text: str, label: str, box: list[float]) -> dict:
    return {"text": text, "blockLabel": label, "boundingBox": box}


# --- which figures ------------------------------------------------------------------------------

def selection_checks() -> None:
    page = {"lines": [{"text": "正文", "blockLabel": "text", "boundingBox": [0.1, 0.05, 0.8, 0.05]}],
            "regions": [region("header_image", [0.0, 0.0, 1.0, 0.2]), region("footer_image", [0.0, 0.8, 1.0, 0.2]),
                        region("seal", [0.3, 0.3, 0.4, 0.4]), region("text", [0.1, 0.5, 0.5, 0.5]),
                        region("image", [0.1, 0.1, 0.1, 0.19]),       # 1.9 % of the page
                        region("chart", [0.5, 0.1, 0.15, 0.14]),      # 2.1 %
                        region("image", [0.1, 0.6, 0.5, 0.3])]}
    figures = _figures.page_figures(7, page)
    check([(f.label, f.box, f.number, f.name) for f in figures] == [("chart", [0.5, 0.1, 0.15, 0.14], 1, "page-0007-1.png"),
                                                                     ("image", [0.1, 0.6, 0.5, 0.3], 2, "page-0007-2.png")],
          f"chart and image regions of 2 % of the page or more, numbered in reading order; header_image, footer_image, "
          f"seal, text and a 1.9 % image left out ({[(f.label, f.box) for f in figures]})")
    check(_figures.page_figures(7, {"lines": page["lines"]}) == [], "a page without `regions` (an older engine-A run): none")

    order = _figures.page_figures(1, {"regions": [region("image", [0.1, 0.6, 0.3, 0.3]), region("chart", [0.1, 0.1, 0.3, 0.3])]})
    check([f.label for f in order] == ["image", "chart"], "numbered in engine A's reading order, not top to bottom")
    near = _figures.page_figures(1, {"regions": [region("image", [0.1, 0.1, 0.3, 0.3]), region("chart", [0.3, 0.2, 0.3, 0.3])]})
    check([(f.label, f.box) for f in near] == [("chart", [0.1, 0.1, 0.5, 0.4])],
          f"two overlapping regions: one figure, their union, labelled chart ({[(f.label, f.box) for f in near]})")
    gap = _figures.page_figures(1, {"regions": [region("image", [0.1, 0.1, 0.8, 0.2]), region("image", [0.1, 0.315, 0.8, 0.2])]})
    apart = _figures.page_figures(1, {"regions": [region("image", [0.1, 0.1, 0.8, 0.2]), region("image", [0.1, 0.33, 0.8, 0.2])]})
    check(len(gap) == 1 and gap[0].box == [0.1, 0.1, 0.8, 0.415] and len(apart) == 2,
          f"1.5 % apart (their padded crops meet): one figure; 3 % apart: two ({[f.box for f in gap]}, {len(apart)})")
    bridge = _figures.page_figures(1, {"regions": [region("image", [0.1, 0.1, 0.2, 0.2]), region("image", [0.6, 0.1, 0.2, 0.2]),
                                                   region("image", [0.25, 0.15, 0.4, 0.1])]})
    check([f.box for f in bridge] == [[0.1, 0.1, 0.7, 0.2]], f"a region bridging two others joins all three ({[f.box for f in bridge]})")

    titles = {"lines": [{"text": "圖一 上圖", "blockLabel": "figure_title", "boundingBox": [0.2, 0.05, 0.4, 0.03]},
                        {"text": "圖二 下圖", "blockLabel": "figure_title", "boundingBox": [0.2, 0.92, 0.4, 0.03]}],
              "regions": [region("image", [0.1, 0.1, 0.8, 0.3]), region("image", [0.1, 0.55, 0.8, 0.35])]}
    check([f.anchor for f in _figures.page_figures(1, titles)] == ["圖一 上圖", "圖二 下圖"],
          "no line before a figure: each takes the nearest figure_title on its page")
    heads = {"lines": [line("圖一 總圖", "figure_title", [0.2, 0.05, 0.4, 0.03]),
                       line("一、甲族", "paragraph_title", [0.1, 0.1, 0.2, 0.03]),
                       line("旁邊嘅正文", "text", [0.6, 0.5, 0.3, 0.3]),
                       line("二、乙族", "paragraph_title", [0.1, 0.85, 0.2, 0.03])],
             "regions": [region("image", [0.1, 0.14, 0.45, 0.3], 2), region("image", [0.1, 0.5, 0.45, 0.3], 3)]}
    check([f.anchor for f in _figures.page_figures(1, heads)] == ["一、甲族", "圖一 總圖"],
          f"the line before a figure when it is a title (a heading above it), else the nearest figure_title (body "
          f"text beside it) ({[f.anchor for f in _figures.page_figures(1, heads)]})")
    merged = _figures.page_figures(1, {"lines": heads["lines"], "regions": [region("image", [0.1, 0.1, 0.3, 0.3], 2),
                                                                           region("chart", [0.3, 0.2, 0.3, 0.3], 4)]})
    check([f.anchor for f in merged] == ["一、甲族"], f"merged regions: the earliest place in the reading order "
                                                       f"({[f.anchor for f in merged]})")

    box = [0.2, 0.3, 0.6, 0.4]
    check(_figures._crop(box, []) == [0.19, 0.29, 0.81, 0.71] and _figures._crop([0.0, 0.995, 1.0, 0.005], [])
          == [0.0, 0.985, 1.0, 1.0], "the crop: 1 % around the box, never past the page's edge")
    beside = [line("圖題", "figure_title", [0.25, 0.27, 0.3, 0.025]), line("旁註", "text", [0.05, 0.4, 0.14, 0.05]),
              line("遠處", "text", [0.2, 0.8, 0.5, 0.03]), line("右上", "text", [0.85, 0.25, 0.1, 0.03])]
    over = [line("小標題", "paragraph_title", [0.25, 0.28, 0.3, 0.025])]
    check(_figures._crop(box, beside) == [0.19, 0.295, 0.81, 0.71] and _figures._crop(box, over) == [0.19, 0.3, 0.81, 0.71],
          f"the crop stops at a text line just beyond the box (above, at its side), at the box's edge for one "
          f"overlapping it; a line further off or diagonal to it does not count ({_figures._crop(box, beside)}, "
          f"{_figures._crop(box, over)})")


def cut_checks(tmp: Path) -> None:
    from PIL import Image

    renders, folder = tmp / "cut" / "renders", tmp / "cut" / _figures.FOLDER
    renders.mkdir(parents=True)
    grey = Image.linear_gradient("L").resize((400, 500))
    Image.merge("RGB", (grey, grey, grey)).save(renders / "page-0001.png")
    Image.merge("RGB", (grey, grey.rotate(90), grey)).save(renders / "page-0002.png")
    grey.save(renders / "page-0003.png")
    folder.mkdir()
    (folder / "page-0009-1.png").write_bytes(b"an earlier run's picture")
    (folder / "notes.png").write_bytes(b"the person's")
    figures = [figure(1, 1, "image", [0.1, 0.2, 0.3, 0.4]), figure(2, 1, "chart", [0.0, 0.0, 0.5, 0.5]),
               figure(3, 1, "image", [0.5, 0.5, 0.5, 0.5]), figure(4, 1, "image", [0.1, 0.1, 0.5, 0.5])]
    _figures.cut(figures, renders, folder)
    one, two, three = (Image.open(folder / f"page-000{n}-1.png") for n in (1, 2, 3))
    check(one.mode == "L" and one.size == (128, 210) and one.tobytes() == grey.crop((36, 95, 164, 305)).tobytes(),
          f"a grey RGB render: the box plus 1 % of the page, kept grey with the render's values ({one.mode} {one.size})")
    source = Image.open(renders / "page-0002.png")
    check(two.mode == "RGB" and two.size == (204, 255) and two.tobytes() == source.crop((0, 0, 204, 255)).tobytes(),
          f"a coloured render: kept in colour, the padding clamped at the page's edge ({two.mode} {two.size})")
    check(three.mode == "L" and three.size == (204, 255) and three.tobytes() == grey.crop((196, 245, 400, 500)).tobytes(),
          "a grey render: lossless")
    check([f.picture for f in figures] == [True, True, True, False] and not (folder / "page-0004-1.png").exists(),
          "no render: no picture, and the figure says so")
    (tmp / "cut" / "finish").mkdir()
    figures[2].removed = ["# 告示", "為禁搶奪事"]
    (tmp / "cut" / "finish" / finish_book.FIGURES_JSON).write_text(json.dumps([f.as_dict() for f in figures]), encoding="utf-8")
    said = [item.detail for item in finish_book.figure_items(tmp / "cut")]
    check(said[:2] == ["[一幅圖片](figures/page-0001-1.png)", "[一幅圖表](figures/page-0002-1.png)"]
          and "冇留到圖片" in said[3] and "figures/" not in said[3],
          f"doubts.md links each picture, and says which have none ({said[0]}; {said[3]})")
    check(said[2] == "[一幅圖片](figures/page-0003-1.png)；圖入面嘅字之前轉寫咗做正文，而家攞走咗：「# 告示」「為禁搶奪事」",
          f"doubts.md lists every line taken out for a figure, as it was ({said[2]})")
    check(not (folder / "page-0009-1.png").exists() and (folder / "notes.png").is_file(),
          "an earlier run's picture goes; a picture of another name stays")
    _figures.cut([], renders, tmp / "cut" / "empty")
    check(not (tmp / "cut" / "empty").exists(), "no figure: no folder")
    (renders / "page-0001.png").rename(renders / "page-0001.png.away")
    again = [figure(1, 1, "image", [0.1, 0.2, 0.3, 0.4]), figure(2, 1, "chart", [0.0, 0.0, 0.5, 0.5])]
    _figures.cut(again, renders, folder)
    check([f.picture for f in again] == [False, True] and not (folder / "page-0001-1.png").exists(),
          "cut again with a render gone: the earlier run's picture goes too (no picture is delivered for it)")


# --- where the image lines go -------------------------------------------------------------------

ANNOTATED = """<!-- page_001 -->

# 山居

春風吹過小橋。

圖一 江上漁舟圖，資料來源：某書。

秋月照林間。

<!-- page_002 -->

晨光古寺。

鐘聲遠近。

<!-- page_003 -->

田野稻麥黃熟，牧童

<!-- page_004 -->

牛背笛聲長。

竹籬茅舍。

<!-- page_005 -->

炊煙起落。
"""
BOUNDARIES = {1: "start", 2: "paragraph", 3: "paragraph", 4: "join", 5: "paragraph"}
PLACED = """# 山居

春風吹過小橋。

圖一 江上漁舟圖，資料來源：某書。

![](figures/page-0001-1.png)

![](figures/page-0001-2.png)

秋月照林間。

晨光古寺。

鐘聲遠近。

![](figures/page-0002-1.png)

![](figures/page-0002-2.png)

田野稻麥黃熟，牧童牛背笛聲長。

![](figures/page-0003-1.png)

竹籬茅舍。

炊煙起落。

![](figures/page-0005-1.png)
"""


def place_checks(tmp: Path) -> None:
    box = [0.1, 0.1, 0.5, 0.5]
    figures = [figure(1, 1, "image", box, "圖1 江上漁舟圖"), figure(1, 2, "chart", box, "圖一江上漁舟"),
               figure(2, 1, "image", box), figure(2, 2, "image", box, "圖九 無關嘅題"),
               figure(3, 1, "image", box), figure(5, 1, "image", box), figure(6, 1, "image", box)]
    placed = _figures.place(ANNOTATED, figures, BOUNDARIES)
    final = apply_boundary_contract(placed, BOUNDARIES)
    check(final == PLACED, "the image lines after the caption's paragraph (two in order), at the page's end, past a "
                           "joining marker to the end of the paragraph that runs on, at the book's end; a page not "
                           "in the text is left out:\n" + final)
    check([f.after for f in figures] == ["anchor", "anchor", "page", "page", "page", "page", ""],
          f"each figure says where its line went ({[f.after for f in figures]})")
    without = re.sub(r"\n\n!\[\]\(figures/page-\d+-\d+\.png\)(?=\n)", "", final)
    check(without == apply_boundary_contract(ANNOTATED, BOUNDARIES), "the image lines removed, the text is the one without figures")
    work = tmp / "place"
    work.mkdir()
    (work / "annotated.md").write_text(placed, encoding="utf-8")
    (work / "final.md").write_text(final, encoding="utf-8")
    (work / "boundaries.json").write_text(json.dumps({"boundaries": {str(k): v for k, v in BOUNDARIES.items()}}),
                                          encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        code = audit_ocr_markdown.main([str(work / "final.md"), "--mode", "final", "--boundary-manifest",
                                        str(work / "boundaries.json"), "--annotated-source", str(work / "annotated.md"),
                                        "--summary-only", "--json-report", str(work / "audit.json")])
    report = json.loads((work / "audit.json").read_text(encoding="utf-8"))
    check(code == 0 and report["counts"]["errors"] == 0, f"the final audit of it, against the annotated text with the "
                                                         f"image lines, passes ({report['counts']})")
    quoted = ("<!-- page_001 -->\n\n城牆嘅位置，可以參看下面圖一「清代廣州城圖」，城門共有八座。\n\n圖一 清代廣州城圖\n\n城外係河。\n\n"
              "<!-- page_002 -->\n\n詳見圖3所示嘅分佈。\n\n圖3\n\n城內有街。\n\n"
              "<!-- page_003 -->\n\n圖4 江口\n\n水退。\n\n圖4 江口\n")
    marks = {1: "start", 2: "paragraph", 3: "paragraph"}
    out = apply_boundary_contract(_figures.place(quoted, [figure(1, 1, "image", box, "圖一 清代廣州城圖"),
                                                          figure(2, 1, "image", box, "圖3"),
                                                          figure(3, 1, "image", box, "圖4 江口")], marks), marks)
    check(out == ("城牆嘅位置，可以參看下面圖一「清代廣州城圖」，城門共有八座。\n\n圖一 清代廣州城圖\n\n![](figures/page-0001-1.png)\n\n"
                  "城外係河。\n\n詳見圖3所示嘅分佈。\n\n圖3\n\n![](figures/page-0002-1.png)\n\n城內有街。\n\n"
                  "圖4 江口\n\n![](figures/page-0003-1.png)\n\n水退。\n\n圖4 江口\n"),
          "body text quoting the caption before it (in full; a caption of only a number) does not take the image line; of "
          "two equal paragraphs the first does:\n" + out)
    noted = "<!-- page_001 -->\n\n圖五 城外係河，\n\n①註文。\n\n<!-- page_002 -->\n\n河上有船。\n\n下段。\n"
    marks = {1: "start", 2: "join"}
    out = apply_boundary_contract(_figures.place(noted, [figure(1, 1, "image", box, "圖五 城外係河")], marks), marks)
    check(out == "圖五 城外係河，河上有船。\n\n①註文。\n\n![](figures/page-0001-1.png)\n\n下段。\n",
          "a paragraph that runs on past its page's foot note (a join) takes the image line after its end, not before "
          "the note, where the join would glue it to the next page's text:\n" + out)


def doubt_checks() -> None:
    items = [finish_book.Item("figures", "figure-kept", page, f"[一幅圖片](figures/page-{page:04d}-1.png)", "finish")
             for page in range(1, 13)]
    items.append(finish_book.Item("text", "reading-dropped", 3, "裁圖 proofread/page-0003-adjudication-01.png 見", "phase3"))
    sealed = finish_book.Sealed(b"", 12, {n: True for n in range(1, 13)}, {}, {}, [], None)
    text = finish_book.doubts_markdown(Path("/nowhere"), sealed, items)
    section = text.split("\n## 圖：淨係留咗圖片（12 項）", 1)[-1].split("\n## ", 1)[0]
    shown = [line for line in section.splitlines() if line.startswith("- 第")]
    check(len(shown) == 12 and all(shown[n - 1] == f"- 第 {n} 頁：[一幅圖片](figures/page-{n:04d}-1.png)" for n in range(1, 13))
          and "仲有" not in section and "page-0003-adjudication-01.png" not in text and "裁圖 裁圖 見" in text
          and "表格、圖、格式" in text,
          f"doubts.md: every figure in its own section, a link to its picture; a crop's name still said as 裁圖; the "
          f"order line names the section ({shown[:1]})")
    for stem in ("期刊 #3", "書?版", "報告 100%2F", "書 <甲>"):
        copy = _deliver.figures_for_copy("![](figures/page-0001-1.png)\n[一幅圖片](figures/page-0001-1.png)\n",
                                         Path("/shelf") / f"{stem}.pdf")
        targets = re.findall(r"\]\(<([^<>]*)>\)", copy)
        parts = [urllib.parse.urlsplit(target) for target in targets]
        check(len(targets) == 2 and all(not (p.query or p.fragment) and urllib.parse.unquote(p.path)
                                        == f"{stem}-圖/page-0001-1.png" for p in parts),
              f"a PDF named {stem!r}: the links read its X-圖/ ({targets[:1]})")


def taken_doubt_checks(tmp: Path) -> None:
    """The doubts on lines a plate took out of final.md, and a plate that
    took none out."""
    book = tmp / "taken"
    (book / "finish").mkdir(parents=True)
    (book / "repaired").mkdir()
    (book / finish_book.FINAL).write_text("春風吹過小橋，義軍到，示為信。\n\n![](figures/page-0002-1.png)\n\n义军告示之一\n",
                                          encoding="utf-8")
    plate, seal = figure(2, 1, "image", [0.1, 0.1, 0.5, 0.5]), figure(2, 2, "image", [0.1, 0.7, 0.2, 0.1])
    plate.source, plate.drop, plate.lines, plate.picture = "plate", [0], ["臺灣義軍告示為禁搶奪事照得本帥"], True
    plate.removed = ["臺灣義軍告示為禁搶奪事照得本帥"]
    seal.source, seal.drop, seal.lines, seal.picture = "plate", [0], ["精書髭精鬚粉\n精"], True
    (book / "finish" / finish_book.FIGURES_JSON).write_text(json.dumps([plate.as_dict(), seal.as_dict()]),
                                                           encoding="utf-8")
    (book / "repaired" / "doubts.json").write_text(json.dumps([
        {"kind": "answer-script-converted", "page": 2, "detail": "V1: written 为 (「…臺湾義軍告示［为］禁搶奪事…」)"},
        {"kind": "answer-script-converted", "page": 2, "detail": "V1: written 桥 (「…春風吹過小［桥］…」)"},
        {"kind": "same-reading-written-differently", "page": 2, "detail": "W1: 義軍［告］示為 p.2"},
        {"kind": "answer-script-converted", "page": 2, "detail": "V1: written 义 (「義軍」)"},
        {"kind": "answer-script-converted", "page": 3, "detail": "V1: written 为 (「…臺湾義軍告示［为］禁搶奪事…」)"}]),
        encoding="utf-8")
    (book / "repaired" / "lint.json").write_text(json.dumps({"after": {
        "hits": {"TEXT-TRADITIONAL": [{"page": 2, "text": "臺（台）灣（湾）" * 40 + "：臺灣義軍告示為禁搶奪事照得本帥"}]},
        "counters": {"TEXT-TRADITIONAL": {"count": 1}}}}), encoding="utf-8")
    flag = {"needs_research": True, "context_before": "臺灣義軍告示為", "resolved": "禁", "context_after": "搶奪事照得本帥",
            "draft_reading": "禁", "writer_reading": "林"}
    sealed = finish_book.Sealed(b"", 3, {1: True, 2: True, 3: True}, {}, {2: {"adjudications": [flag]}}, [], None)
    items = finish_book.collect_items(book, sealed)
    tagged = [(i.kind, i.page) for i in items if i.detail.endswith(finish_book.TAKEN_WITH_FIGURE)]
    check(tagged == [("research-glyph", 2), ("answer-script-converted", 2), ("same-reading-written-differently", 2),
                     ("TEXT-TRADITIONAL", 2)] and "或者連圖攞走咗" in items[0].detail and len(items) == 9,
          f"a doubt on a line a plate took out of final.md stays and says so (a context in either script, a spot read "
          f"with its context, past the clipped detail); one on text final.md holds, or on another page, does not "
          f"({tagged})")
    said = [i.detail for i in items if i.kind == "figure-kept"]
    check(said[1] == "[一幅圖片](figures/page-0002-2.png)；引擎 A 喺圖入面讀到「精書髭精鬚粉 精」，正文冇攞走任何一行："
                     "佢嘅轉寫可能仲留喺正文，要對圖" and "攞走咗：「臺灣義軍告示為禁搶奪事照得本帥」" in said[0],
          f"a plate whose lines the text did not give up: its doubt quotes engine A's and says a transcription may "
          f"remain ({said[1]})")


# --- plates and engine B's boxes ----------------------------------------------------------------

def plate_page(*extra: dict) -> dict:
    """A made-up page of a book printed in simplified script: a plate engine
    A reads as a block in traditional script, a line of garble inside it,
    print engine B reads inside it, its caption below, a folio."""
    return {"lines": [line("义军告示之一", "vision_footnote", [0.3, 0.75, 0.4, 0.04]),
                      line("臺灣義軍告示為禁搶奪事照得本帥", "vertical_text", [0.2, 0.1, 0.6, 0.5]),
                      line("衡若大坑", "vertical_text", [0.25, 0.45, 0.05, 0.1]),
                      line("天运丙午年", "vertical_text", [0.7, 0.2, 0.05, 0.2]),
                      line("五", "number", [0.85, 0.9, 0.02, 0.02]), *extra], "regions": []}


PLATE_B = "义军告示之一\n天运丙午年\n"     # engine B: the caption and the print; the plate unread


def plate_checks() -> None:
    figures = _figures.page_figures(3, plate_page(), PLATE_B, simplified=True)
    check([(f.source, f.label, f.box, f.drop, f.name) for f in figures]
          == [("plate", "image", [0.2, 0.1, 0.6, 0.5], [1, 2], "page-0003-1.png")],
          f"a block in traditional script engine B leaves unread, in a simplified book: a plate, its box; its block "
          f"and the garble inside it engine B did not read go, the print inside and the caption below stay "
          f"({[(f.source, f.box, f.drop) for f in figures]})")
    check(figures[0].as_dict()["drop"] == ["臺灣義軍告示為禁搶奪事照得本帥", "衡若大坑"] and "lines" not in figures[0].as_dict(),
          "figures.json names engine A's lines that go, not the page's")
    read = _figures.page_figures(3, plate_page(), "义军告示之一\n臺灣義軍告示為禁搶奪事照得本帥\n天运丙午年\n", simplified=True)
    printed = _figures.page_figures(3, plate_page(), "义军告示之一\n台湾义军告示为禁抢夺事照得本帅\n天运丙午年\n",
                                    simplified=True)
    half = _figures.page_figures(3, plate_page(), "义军告示之一\n台湾义军\n天运丙午年\n", simplified=True)
    check([f.drop for f in read] == [[1, 2]] and printed == [] and [f.source for f in half] == ["plate"],
          f"engine B reads it in traditional script too: a plate; in simplified script: print engine A misread, none; "
          f"under half of it: unread ({[f.drop for f in read]}, {len(printed)}, {[f.source for f in half]})")
    tie = plate_page()
    tie["lines"][1] = line("臺湾義军告示", "vertical_text", [0.2, 0.1, 0.6, 0.5])
    folio = plate_page()
    folio["lines"][1]["blockLabel"] = "number"
    check(_figures.page_figures(3, plate_page(), PLATE_B) == [] and _figures.page_figures(3, plate_page()) == []
          and _figures.page_figures(3, tie, PLATE_B, simplified=True) == []
          and _figures.page_figures(3, folio, PLATE_B, simplified=True) == [],
          "no plate in a book printed in traditional script, without engine B's draft, of a block as much in one script "
          "as the other, or of a folio")
    grown = plate_page()
    grown["regions"] = [region("chart", [0.02, 0.85, 0.05, 0.05]), region("image", [0.15, 0.55, 0.3, 0.1], 2)]
    figures = _figures.page_figures(3, grown, PLATE_B + "(790,400),(850,450)\n(850,850),(900,900)\n", simplified=True)
    check([(f.number, f.source, f.label, f.box) for f in figures]
          == [(1, "plate", "image", [0.15, 0.1, 0.7, 0.55])],
          f"a plate grown by the engine A region and the engine B box (under 2 % of the page) that meet it, in the "
          f"region's place; a region and a box apart from it, under 2 %, none ({[(f.number, f.source, f.box) for f in figures]})")
    two = plate_page(line("大坑", "vertical_text", [0.6, 0.605, 0.1, 0.02]),
                     line("鄉民結會", "vertical_text", [0.2, 0.63, 0.3, 0.1]),
                     line("乡里", "vertical_text", [0.25, 0.65, 0.05, 0.05]))
    figures = _figures.page_figures(3, two, PLATE_B + "乡里\n", simplified=True)
    check([(f.box, f.drop) for f in figures] == [([0.2, 0.1, 0.6, 0.63], [1, 2, 5, 6])],
          f"two plate blocks apart, garble engine B did not read between them and print inside each: one plate "
          f"({[(f.box, f.drop) for f in figures]})")
    parted = plate_page(line("鄉民結會", "vertical_text", [0.2, 0.85, 0.3, 0.1]))
    figures = _figures.page_figures(3, parted, PLATE_B, simplified=True)
    check([(f.box, f.drop) for f in figures] == [([0.2, 0.1, 0.6, 0.5], [1, 2]), ([0.2, 0.85, 0.3, 0.1], [5])],
          f"a caption engine B reads between two plates: two, numbered top to bottom "
          f"({[(f.box, f.drop) for f in figures]})")
    touching = plate_page(line("鄉民結會", "vertical_text", [0.2, 0.615, 0.3, 0.1]))
    touching["lines"][0]["boundingBox"] = [0.3, 0.602, 0.4, 0.01]
    check([f.box for f in _figures.page_figures(3, touching, PLATE_B, simplified=True)] == [[0.2, 0.1, 0.6, 0.615]],
          "blocks whose crops meet are one plate, a caption between or not")
    title = plate_page()
    title["lines"][1]["blockLabel"] = "paragraph_title"
    inner = plate_page(line("臺灣告示", "doc_title", [0.3, 0.2, 0.2, 0.05]))
    check(_figures.page_figures(3, title, PLATE_B, simplified=True) == []
          and [f.drop for f in _figures.page_figures(3, inner, PLATE_B, simplified=True)] == [[1, 2, 5]],
          "a title engine A reads in traditional script and engine B leaves unread starts no plate; inside a plate "
          "another block starts, it goes")
    lone = {"lines": [line("臺灣義軍告示為禁搶奪事", "text", [0.7, 0.1, 0.1, 0.2]),
                      line("乡民结会于此聚众，官府屡禁不止", "text", [0.1, 0.6, 0.5, 0.03])]}
    specks = "".join(f"({700 - 20 * n},{300 + 15 * n}),({708 - 20 * n},{308 + 15 * n})\n" for n in range(23))
    figures = _figures.page_figures(3, lone, specks, simplified=True)
    check([(f.box, f.drop) for f in figures] == [([0.68, 0.1, 0.12, 0.223], [0])],
          f"a chain of engine B's specks: only those meeting the block join the plate, and the body line engine B "
          f"skipped beyond them stays ({[(f.box, f.drop) for f in figures]})")


def grounding_checks() -> None:
    page = {"lines": [line("○○○", "paragraph_title", [0.2, 0.3, 0.1, 0.1]),
                      line("80", "paragraph_title", [0.15, 0.15, 0.05, 0.05]),
                      line("此三杯茶可飲", "text", [0.5, 0.3, 0.4, 0.05]),
                      line("七", "number", [0.3, 0.4, 0.02, 0.02]),
                      line("○", "paragraph_title", [0.8, 0.8, 0.05, 0.05])]}
    text_b = "80\n此三杯茶可飲\n(100,100),(400,500)\n"
    figures = _figures.page_figures(2, page, text_b)
    check([(f.source, f.box, f.drop, f.anchor) for f in figures] == [("grounding", [0.1, 0.1, 0.3, 0.4], [0], "")],
          f"an engine B box of 2 % of the page or more, in any book: a picture; engine A's circles inside it go, the "
          f"figures engine B read, the folio and the circle outside it stay ({[(f.source, f.box, f.drop) for f in figures]})")
    below = {"lines": page["lines"] + [line("圖中地名", "text", [0.2, 0.75, 0.1, 0.05])],
             "regions": [region("image", [0.1, 0.7, 0.5, 0.25])]}
    figures = _figures.page_figures(2, below, text_b)
    check([(f.number, f.source, f.drop) for f in figures] == [(1, "regions", []), (2, "grounding", [0])],
          f"engine A's region first, though lower on the page, and nothing of it goes; engine B's box after it "
          f"({[(f.number, f.source, f.drop) for f in figures]})")
    check(_figures.grounding_boxes("文字 (1,2),(3,4)\n(1,2),(3,4) 文字\n(0,0),(999,999)\n(400,400),(100,100)\n"
                                   " (5, 6) , (900 , 950) \n")
          == [[0.005, 0.006, 0.895, 0.944]],
          "engine B's boxes: a line of its own, not one from corner to corner of its grid nor one upside down")
    over = {"lines": page["lines"] + [line("此為忠心", "text", [0.38, 0.45, 0.3, 0.05])]}
    small = "(100,100),(200,280)\n"
    near = {"lines": page["lines"], "regions": [region("image", [0.41, 0.1, 0.01, 0.01])]}
    check(_figures.page_figures(2, over, text_b) == [] and _figures.page_figures(2, page, small) == []
          and [f.source for f in _figures.page_figures(2, near, text_b)] == [],
          "no picture where a line engine A reads in Han characters lies partly in the box, of a box under 2 % of the "
          "page, or of one that meets an engine A region (under 2 % itself: no figure)")
    latin = {"lines": [line("Plate III. The Canton Factories, 1840", "text", [0.2, 0.3, 0.5, 0.05])]}
    formula = {"lines": [line(r"\begin{matrix}0&0\end{matrix}", "display_formula", [0.2, 0.3, 0.1, 0.05])]}
    check(_figures.page_figures(2, latin, "(100,100),(800,500)\n") == []
          and [f.drop for f in _figures.page_figures(2, formula, "(100,100),(800,500)\n")] == [[0]],
          "a line engine A reads with a Latin word keeps engine B's box from a picture; a formula block does not, "
          "and goes")
    year = {"lines": [line("1925", "text", [0.2, 0.2, 0.05, 0.02]), line("圖中之城", "text", [0.5, 0.7, 0.2, 0.05])]}
    check(all([f.drop for f in _figures.page_figures(2, year, f"(100,100),(400,{y})\n圖中之城\n")] == [[0]]
              for y in (500, 600)),
          "engine B's box corners are no reading: figures inside the box engine B did not read go, wherever it ends")
    pair = _figures.page_figures(2, {"lines": []}, "(100,100),(300,300)\n(310,100),(500,300)\n(100,700),(300,900)\n")
    check([f.box for f in pair] == [[0.1, 0.1, 0.4, 0.2], [0.1, 0.7, 0.2, 0.2]],
          f"engine B boxes whose crops meet are one picture ({[f.box for f in pair]})")


def find_checks(tmp: Path) -> None:
    for script, body in (("simplified", "乡民结会于义军之营"), ("traditional", "鄉民結會於義軍之營")):
        a, b = tmp / "find" / script / "ocr-paddle", tmp / "find" / script / "ocr-hunyuan"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        page = plate_page(line(body * 3, "text", [0.1, 0.62, 0.8, 0.1]))
        (a / "page-0003.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        (a / "page-0003.txt").write_text("\n".join(l["text"] for l in page["lines"]), encoding="utf-8")
        (b / "page-0003.txt").write_text(PLATE_B + body * 3 + "\n", encoding="utf-8")
        figures = _figures.find(a, [3, 4], b)
        check([(f.source, f.drop) for f in figures] == ([("plate", [1, 2])] if script == "simplified" else []),
              f"find: a book whose drafts are in {script} script ({[(f.source, f.drop) for f in figures]})")
    a, b = tmp / "find" / "simplified" / "ocr-paddle", tmp / "find" / "simplified" / "ocr-hunyuan"
    (b / "page-0003.txt").unlink()
    check(_figures.find(a, [3], b) == [], "find: no engine B draft for the page, no plate")


TAKEN = """<!-- page_001 -->

春風吹過小橋，

<!-- page_002 -->

臺灣義軍告示為禁搶奪事照得本帥
衡若大坑

义军告示之一

清史髡精丛粉

<!-- page_003 -->

田野稻麥黃熟。
"""


def take_out_checks() -> None:
    def plate(page: int, drop: list[int], lines: list[str]) -> _figures.Figure:
        figure = figure_(page, 1)
        figure.source, figure.drop, figure.lines, figure.picture = "plate", drop, lines, True
        return figure

    def figure_(page: int, number: int) -> _figures.Figure:
        return figure(page, number, "image", [0.1, 0.1, 0.5, 0.5])
    engine = ["臺灣義軍告示為禁搶奪事照得本帥", "衡若大坑", "义军告示之一", "精書髭精鬚粉"]
    one = plate(2, [0, 1, 3], engine)
    boundaries = {1: "start", 2: "join", 3: "join"}
    text, joins = _figures.take_out(TAKEN, [one], boundaries)
    check(text == TAKEN.replace("臺灣義軍告示為禁搶奪事照得本帥\n衡若大坑\n", "![](figures/page-0002-1.png)\n")
          .replace("\n清史髡精丛粉\n", "")
          and one.removed == ["臺灣義軍告示為禁搶奪事照得本帥", "衡若大坑", "清史髡精丛粉"] and one.after == "text",
          f"take_out: the plate's lines go, its image line in place of the first, the caption stays; a line 裁決 "
          f"rewrote goes on the letters left of engine A's ({one.removed}):\n" + text)
    check(joins == {1: "start", 2: "paragraph", 3: "paragraph"} and boundaries[2] == "join",
          f"the joins into and out of the lines taken out are paragraph breaks; the boundaries given are left alone "
          f"({joins})")
    final = apply_boundary_contract(text, joins)
    check(final == "春風吹過小橋，\n\n![](figures/page-0002-1.png)\n\n义军告示之一\n\n田野稻麥黃熟。\n",
          "the markers removed: the image line on its own, nothing glued to it:\n" + final)
    kept = plate(2, [1], engine)
    text, joins = _figures.take_out(TAKEN, [kept], boundaries)
    check(text == TAKEN.replace("衡若大坑\n", "![](figures/page-0002-1.png)\n").replace(
          "臺灣義軍告示為禁搶奪事照得本帥\n", "臺灣義軍告示為禁搶奪事照得本帥\n\n") and joins == boundaries,
          "a line taken out from inside a paragraph: the image line between blank lines, the joins stand:\n" + text)
    none = plate(2, [0], ["鄉民結會於營"])
    text, joins = _figures.take_out(TAKEN, [none], boundaries)
    placed = _figures.place(text, [none], joins)
    check(text == TAKEN and none.removed == [] and none.after == "page" and placed.count("![](figures/page-0002-1.png)") == 1,
          "a plate whose text the page does not hold: nothing taken out, its image line at the page's end")
    check(_figures.place(_figures.take_out(TAKEN, [one], boundaries)[0], [one], boundaries).count("![](") == 1,
          "place leaves a picture take_out put in place of its text where it is")
    regions = figure_(2, 1)
    check(_figures.take_out(TAKEN, [regions], boundaries) == (TAKEN, boundaries),
          "a figure of engine A's regions takes nothing out")
    check(_figures.take_out(TAKEN, [plate(2, [0, 1, 3], engine)], {1: "start", 2: "structural", 3: "join"})[1]
          == {1: "start", 2: "structural", 3: "paragraph"}, "a break before the lines taken out stays as it is")
    merged = plate(2, [0, 1], ["臺灣義軍告示為禁搶奪事照得本帥衡若大坑义军告示之一", "精書髭精鬚粉"])
    unread = plate(2, [0, 1], merged.lines)
    merged.text_b = "义军告示之一\n臺灣義軍告示為禁搶奪事照得本帥\n"
    text, _ = _figures.take_out(TAKEN, [merged], boundaries)
    _figures.take_out(TAKEN, [unread], boundaries)
    check(text == TAKEN.replace("臺灣義軍告示為禁搶奪事照得本帥\n衡若大坑\n", "![](figures/page-0002-1.png)\n")
          .replace("\n清史髡精丛粉\n", "") and "义军告示之一" not in merged.removed and "义军告示之一" in unread.removed,
          f"a line in simplified script engine B's draft holds stays, though engine A read it into the plate's block; "
          f"the plate's line engine B read in traditional script goes ({merged.removed}; unread by engine B: "
          f"{unread.removed})")
    end = plate(3, [0, 1], ["田野稻麥黃熟", "牛背笛聲長"])
    text, _ = _figures.take_out(TAKEN + "\n牛背笛聲長。", [end], boundaries)
    check(text == TAKEN.replace("田野稻麥黃熟。\n", "![](figures/page-0003-1.png)\n"),
          "the book's last lines taken out: the image line, then nothing:\n" + text[-80:])


# --- the whole chain on the made-up book --------------------------------------------------------

def finished(book: Path, keep_work: bool = False, extract: bool = True) -> tuple[int, list[str]]:
    """finish_book.py on BOOK, delivered; with EXTRACT, --extract-figures:
    figures are kept as pictures only when asked (decision 8 of 2026-11-14,
    no figure extraction by default)."""
    return cfb.finish(book, *(("--extract-figures",) if extract else ()), deliver=True, keep_work=keep_work)


def set_regions(book: Path, scan: int, regions: list[dict] | None, caption: str | None = None) -> None:
    path = book / "ocr-paddle" / f"page-{scan:04d}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if regions is None:
        data.pop("regions", None)
    else:
        data["regions"] = regions
    if caption is not None:
        data["lines"].append({"text": caption, "blockLabel": "figure_title", "boundingBox": [0.3, 0.2, 0.4, 0.03]})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def chain_checks(tmp: Path) -> None:
    from PIL import Image

    book = cfb.phase3_book(tmp / "chain" / "work")
    shelf = tmp / "chain" / "shelf"
    shelf.mkdir()
    pdf = shelf / PDF_NAME
    pdf.write_bytes(b"%PDF-1.4 made up\n")
    manifest = json.loads((book / "page-manifest.json").read_text(encoding="utf-8"))
    manifest.update(source_pdf=str(pdf), source_sha256=sha(pdf))
    (book / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    page9 = (book / "proofread" / "page-0009.md").read_text(encoding="utf-8").split("\n\n")
    set_regions(book, 9, None, caption="圖一 " + page9[1][:12])      # the caption of the figure to come
    page16 = (book / "proofread" / "page-0016.md").read_text(encoding="utf-8")
    cfb.reseal(book, 16, text=page16.rstrip("\n").rstrip("。") + "\n")   # its last paragraph runs on to page 17
    code, lines = finished(book)
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    before = (book / "final.md").read_text(encoding="utf-8")
    audit_before = json.loads((book / "finish" / "final-audit.json").read_text(encoding="utf-8"))["counts"]
    check(code == 0 and len(lines) == 5 and "figures" not in lines[0] and not (shelf / FOLDER).exists()
          and len(record) == 2 and not (book / "figures").exists(),
          f"no regions: no figure, no X-圖/, delivered.json records X.md and X-doubts.md only ({lines[0][-120:]})")

    set_regions(book, 6, [region("image", [0.05, 0.05, 0.3, 0.2]), region("image", [0.5, 0.8, 0.4, 0.15])])
    set_regions(book, 9, [region("chart", [0.2, 0.3, 0.5, 0.3]), region("header_image", [0.0, 0.0, 1.0, 0.1])])
    set_regions(book, 16, [region("image", [0.1, 0.1, 0.4, 0.3]), region("image", [0.45, 0.1, 0.3, 0.3])])
    # Decision 8 of 2026-11-14: no figure extraction by default.  Engine A's
    # regions give no picture without --extract-figures, and the text stays.
    code, lines = finished(book, extract=False)
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    check(code == 0 and "figures" not in lines[0] and not (shelf / FOLDER).exists() and len(record) == 2
          and not (book / "figures").exists() and (book / "final.md").read_text(encoding="utf-8") == before,
          f"regions without --extract-figures (the default): no figure, no X-圖/, final.md as before ({lines[0][-120:]})")
    code, lines = finished(book)
    names = ["page-0006-1.png", "page-0006-2.png", "page-0009-1.png", "page-0016-1.png"]
    check(code == 0 and "; 4 of 4 figures kept as pictures in figures/;" in lines[0]
          and lines[3] == f"figures beside the PDF: {shelf / FOLDER} (4 pictures)",
          f"the RESULT line counts the figures, a line names X-圖/ ({lines[0][-140:]}; {lines[3:4]})")
    final = (book / "final.md").read_text(encoding="utf-8")
    links = re.findall(r"^!\[\]\(figures/(page-\d{4}-\d+\.png)\)$", final, re.M)
    without = re.sub(r"\n\n!\[\]\(figures/page-\d+-\d+\.png\)(?=\n)", "", final)
    check(links == names and without == before, f"final.md: the text before plus one image line per figure ({links})")
    figures = {f["name"]: f for f in json.loads((book / "finish" / "figures.json").read_text(encoding="utf-8"))}
    joined = json.loads((book / "repaired" / "boundaries.json").read_text(encoding="utf-8"))["boundaries"].get("17")
    run_on = final.split("![](figures/page-0016-1.png)", 1)[0].rstrip("\n").rsplit("\n", 1)[-1]
    page17 = (book / "proofread" / "page-0017.md").read_text(encoding="utf-8").split("\n\n")[0].strip()
    check(joined == "join" and run_on.endswith(page17) and figures["page-0016-1.png"]["after"] == "page",
          f"a figure at the end of a page whose paragraph runs on: after that paragraph's end on the next page "
          f"({joined}, …{run_on[-20:]})")
    after_caption = final.split("![](figures/page-0009-1.png)", 1)[0].rstrip("\n").rsplit("\n", 1)[-1]
    check(figures["page-0009-1.png"]["after"] == "anchor" and after_caption == page9[1].strip(),
          f"a figure with a caption: after the caption's paragraph (…{after_caption[-20:]})")
    audit = json.loads((book / "finish" / "final-audit.json").read_text(encoding="utf-8"))
    check(audit["counts"] == audit_before
          and audit["input_sha256"]["annotated_source"] == sha(book / "finish" / finish_book.FIGURES_SOURCE),
          f"the final audit read the text with the image lines and found what it found before ({audit['counts']})")
    render = Image.open(book / "renders" / "page-0016.png")
    picture = Image.open(book / "figures" / "page-0016-1.png")
    check(picture.size == (530, 341) and picture.tobytes() == render.crop((78, 99, 608, 440)).tobytes(),
          f"two overlapping regions are one picture, cut lossless with 1 % around but not over the running head's "
          f"column at its left nor past its edge where text columns run on below it ({picture.size})")
    folder = shelf / FOLDER
    copy = (shelf / f"{STEM}.md").read_text(encoding="utf-8")
    targets = re.findall(r"!\[\]\(<([^>]+)>\)", copy)
    check(targets == [f"{FOLDER}/{name}" for name in names] and "](figures/" not in copy
          and all((shelf / target).read_bytes() == (book / "figures" / Path(target).name).read_bytes() for target in targets),
          f"X.md's image lines read X-圖/ in angle brackets (a name with spaces), each the picture of figures/ ({targets[:1]})")
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    pictures = {Path(k).name: v for k, v in record.items() if Path(k).parent == folder}
    check(sorted(pictures) == names and all(v[-1] == sha(folder / name) for name, v in pictures.items()),
          "delivered.json records each picture in X-圖/ with its SHA-256")
    doubts = (shelf / f"{STEM}-doubts.md").read_text(encoding="utf-8")
    section = doubts.split("\n## 圖：淨係留咗圖片", 1)[-1].split("\n## ", 1)[0]
    items = [line for line in section.splitlines() if line.startswith("- 第")]
    check(len(items) == 4 and all(f"](<{FOLDER}/{name}>)" in item for item, name in zip(items, names))
          and "](figures/page-0006-1.png)" in (book / "doubts.md").read_text(encoding="utf-8"),
          f"X-doubts.md: one item per figure linking its picture in X-圖/ (doubts.md: in figures/) ({items[:1]})")
    check((book / "figures").is_dir() and len(list((book / "figures").iterdir())) == 4
          and cfb.report(book)["steps"]["clean"]["status"] == "cleaned",
          "the fifth step cleaned and left figures/")

    # Every figure gone: X-圖/, its records and the image lines go; then they come back.
    for scan in (6, 9, 16):
        set_regions(book, scan, [])
    code, lines = finished(book)
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    check(code == 0 and not folder.exists() and not (book / "figures").exists() and len(record) == 2
          and "![](" not in (shelf / f"{STEM}.md").read_text(encoding="utf-8"),
          f"every figure gone: X-圖/ removed, delivered.json without pictures, no image line ({sorted(record)})")
    set_regions(book, 6, [region("image", [0.05, 0.05, 0.3, 0.2]), region("image", [0.5, 0.8, 0.4, 0.15])])
    set_regions(book, 9, [region("chart", [0.2, 0.3, 0.5, 0.3]), region("header_image", [0.0, 0.0, 1.0, 0.1])])
    set_regions(book, 16, [region("image", [0.1, 0.1, 0.4, 0.3]), region("image", [0.45, 0.1, 0.3, 0.3])])
    code, lines = finished(book)
    check(code == 0 and sorted(p.name for p in folder.iterdir()) == names, "the figures back: their pictures back")

    # Again: a picture the person changed is kept aside; a figure gone takes its picture with it.
    (folder / "page-0006-2.png").write_bytes(b"the person's own picture")
    set_regions(book, 9, [])
    code, lines = finished(book)
    aside = sorted(p.name for p in folder.glob("page-0006-2.edited-*.png"))
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    pictures = {Path(k).name for k in record if Path(k).parent == folder}
    check(code == 0 and len(aside) == 1 and (folder / aside[0]).read_bytes() == b"the person's own picture"
          and (folder / "page-0006-2.png").read_bytes() == (book / "figures" / "page-0006-2.png").read_bytes()
          and "WARNING" in lines[0], f"a picture the person changed: moved aside ({aside}), the picture written anew")
    check(not (folder / "page-0009-1.png").exists() and not (book / "figures" / "page-0009-1.png").exists()
          and "page-0009-1.png" not in pictures and "page-0009-1.png" not in (shelf / f"{STEM}.md").read_text(encoding="utf-8")
          and len(pictures) == 3, "a figure gone: its picture, its record and its line go")
    (folder / "page-0016-1.png").write_bytes(b"the person's map")
    set_regions(book, 16, [])
    code, lines = finished(book)
    record = json.loads((book / "delivered.json").read_text(encoding="utf-8"))
    check(code == 0 and (folder / "page-0016-1.png").read_bytes() == b"the person's map"
          and str(folder / "page-0016-1.png") in record and not (book / "figures" / "page-0016-1.png").exists(),
          "a figure gone whose picture the person changed: the picture stays, still recorded")
    elsewhere = shelf / "elsewhere"
    elsewhere.mkdir()
    folder.rename(shelf / "kept")
    os.symlink(elsewhere, folder)
    set_regions(book, 9, [region("chart", [0.2, 0.3, 0.5, 0.3])])
    code, lines = finished(book)
    delivered = {d["kind"]: d for d in cfb.report(book)["delivered"]}
    check(code == 0 and "WARNING" in lines[0] and not delivered["figures"]["written"] and not any(elsewhere.iterdir()),
          f"X-圖/ a link: nothing written through it, a WARNING ({delivered['figures'].get('reason', '')[:80]})")
    folder.unlink()
    (shelf / "kept").rename(folder)
    set_regions(book, 9, [])
    code, lines = finished(book)
    check(code == 0 and "WARNING" not in lines[0], f"X-圖/ back: delivered again ({lines[0][-80:]})")
    clean_checks(book, shelf)


def grounding_chain_checks(tmp: Path) -> None:
    """finish_book.py on the made-up book, an engine B box on page 17 round
    circles engine A read and phase 3 wrote as the page's first line, which
    the repair runs on from page 16's last paragraph (a join)."""
    book = cfb.phase3_book(tmp / "boxes" / "work")
    path = book / "ocr-paddle" / "page-0017.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lines"].insert(3, line("○○○", "paragraph_title", [0.2, 0.3, 0.1, 0.1]))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    draft = book / "ocr-hunyuan" / "page-0017.txt"
    draft.write_text("(150,200),(400,600)\n" + draft.read_text(encoding="utf-8"), encoding="utf-8")
    page16 = (book / "proofread" / "page-0016.md").read_text(encoding="utf-8")
    cfb.reseal(book, 16, text=page16.rstrip("\n").rstrip("。") + "\n")
    page17 = (book / "proofread" / "page-0017.md").read_text(encoding="utf-8")
    cfb.reseal(book, 17, text="〇〇〇\n\n" + page17)
    code, lines = cfb.finish(book, "--extract-figures", deliver=False, keep_work=True)
    figures = json.loads((book / "finish" / "figures.json").read_text(encoding="utf-8"))
    repaired = json.loads((book / "repaired" / "boundaries.json").read_text(encoding="utf-8"))["boundaries"]
    joins = json.loads((book / "finish" / finish_book.BOUNDARIES_NAME).read_text(encoding="utf-8"))["boundaries"]
    check(code == 0 and "; 1 of 1 figures kept as pictures in figures/;" in lines[0]
          and [(f["source"], f["drop"], f["removed"], f["after"]) for f in figures] == [("grounding", ["○○○"], ["〇〇〇"], "text")],
          f"an engine B box of the made-up book: a picture, the circles' line taken out for it ({figures})")
    final = (book / "final.md").read_text(encoding="utf-8")
    last16 = page16.rstrip("\n").rstrip("。").rsplit("\n", 1)[-1]
    first17 = page17.split("\n\n")[0].strip()
    check(repaired["17"] == "join" and joins["17"] == "paragraph" and "〇〇〇" not in final
          and f"{last16}\n\n![](figures/page-0017-1.png)\n\n{first17}" in final,
          f"the repair's join onto it a paragraph break in finish/boundaries.json; the image line in its place, between "
          f"page 16's last paragraph and page 17's first ({repaired['17']}, {joins['17']})")
    audit = json.loads((book / "finish" / "final-audit.json").read_text(encoding="utf-8"))
    doubts = (book / "doubts.md").read_text(encoding="utf-8")
    check(audit["counts"]["errors"] == 0 and audit["input_sha256"]["annotated_source"]
          == sha(book / "finish" / finish_book.FIGURES_SOURCE)
          and "- 第 17 頁：[一幅圖片](figures/page-0017-1.png)；圖入面嘅字之前轉寫咗做正文，而家攞走咗：「〇〇〇」" in doubts,
          f"the final audit, on finish/boundaries.json, finds nothing; doubts.md lists the line taken out "
          f"({audit['counts']})")
    draft.write_text(draft.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
    code, lines = cfb.finish(book, "--extract-figures", deliver=False, keep_work=True)
    check(code == 0 and not (book / "finish" / finish_book.BOUNDARIES_NAME).exists()
          and f"{last16}〇〇〇" in (book / "final.md").read_text(encoding="utf-8"),
          "the box gone: no figure, the circles back in the text as the repair joined them, finish/boundaries.json gone")


def clean_checks(book: Path, shelf: Path) -> None:
    folder = shelf / FOLDER
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = clean_book.main([str(book)])
    check(code == 0 and (book / "figures").is_dir() and folder.is_dir(), "clean_book.py: the default cleanup leaves "
                                                                        "figures/ and X-圖/")
    ours = (book / "figures" / "page-0006-1.png").read_bytes()
    (book / "figures" / "page-0006-1.png").write_bytes(ours + b"\0")
    with contextlib.redirect_stdout(out := io.StringIO()):
        code = clean_book.main([str(book)])
    last = out.getvalue().splitlines()[-1]
    check(code == 3 and "figures/page-0006-1.png" in last and "not delivered yet" in last,
          f"the default cleanup refused while a picture of figures/ is not the one delivered ({last[:160]})")
    (book / "figures" / "page-0006-1.png").write_bytes(ours)
    (folder / "page-0006-1.png").unlink()
    with contextlib.redirect_stdout(out := io.StringIO()):
        code = clean_book.main([str(book), "--all"])
    last = out.getvalue().splitlines()[-1]
    check(code == 3 and book.is_dir() and f"{FOLDER}/page-0006-1.png" in last and "changed or removed by the person" in last
          and "holds the only copy" in last, f"--all refused while a picture is gone from X-圖/ ({last[:220]})")
    (folder / "page-0006-1.png").write_bytes(ours)
    keep = (folder / "page-0006-1.png").read_bytes()
    (folder / "page-0006-1.png").write_bytes(b"the person's")
    with contextlib.redirect_stdout(out := io.StringIO()):
        code = clean_book.main([str(book), "--all"])
    last = out.getvalue().splitlines()[-1]
    check(code == 3 and book.is_dir() and f"{FOLDER}/page-0006-1.png" in last,
          f"--all refused while a picture in X-圖/ is the person's ({last[:160]})")
    (folder / "page-0006-1.png").write_bytes(keep)
    inside = book / "kept-pictures"
    shutil.move(str(folder), str(inside))
    os.symlink(inside, folder)
    with contextlib.redirect_stdout(out := io.StringIO()):
        code = clean_book.main([str(book), "--all"])
    last = out.getvalue().splitlines()[-1]
    check(code == 3 and book.is_dir() and "lie inside the book folder" in last and FOLDER in last,
          f"--all refused when X-圖/ lies inside the book folder ({last[:160]})")
    folder.unlink()
    shutil.move(str(inside), str(folder))
    with contextlib.redirect_stdout(out := io.StringIO()):
        code = clean_book.main([str(book), "--all"])
    last = out.getvalue().splitlines()[-1]
    check(code == 0 and not book.exists() and (shelf / f"{STEM}.md").is_file() and (shelf / f"{STEM}-doubts.md").is_file()
          and (folder / "page-0006-1.png").is_file() and f"{FOLDER}/ beside the PDF" in last,
          f"--all: the book folder gone; X.md, X-doubts.md and X-圖/ stay ({last[-160:]})")


def skill_checks() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
    section = workflow.split("\n## 圖：淨係留圖片", 1)[-1].split("\n## ", 1)[0]
    folder = skill.split("\n## 三、", 1)[-1].split("\n## ", 1)[0]
    run = skill.split("\n## 四、", 1)[-1].split("\n## ", 1)[0]
    talk = skill.split("\n## 九、", 1)[-1].split("\n## ", 1)[0]
    wanted = {
        # Decision 8: no figure extraction by default; X-圖/ only with the switch.
        "SKILL.md: X-圖/ among the deliveries, only with --extract-figures":
            "`X-doubts.md`（疑問清單），加咗 `--extract-figures` 先有 `X-圖/`" in folder,
        "SKILL.md: the switch among those added only when the person asks":
            "`--extract-figures`、`--renumber-footnotes`、`--relocate-footnotes`（用戶要求先加）" in skill,
        "SKILL.md: a plate's text taken out, listed": "連圖攞走咗嘅字（例如影印原件嘅轉寫）`X-doubts.md` 逐行列住" in talk,
        "SKILL.md: --all keeps the deliveries, X-圖/ among them":
            "淨係留 PDF 隔籬嘅交付檔" in run and "`X-圖/` 留低" in workflow,
        "SKILL.md: tell where the pictures are and how many": "有圖（`--extract-figures`）就講 `X-圖/` 喺邊、幾多幅" in talk,
        "SKILL.md: the reference section named": "〈圖：淨係留圖片（--extract-figures）〉" in skill,
        "the reference: by default no extraction, figure text transcribed":
            "預設唔抽圖" in section and "圖表文字都要轉" in section and "用戶要求先加" in section,
        "the reference: what is kept": all(w in section for w in ("`regions`", "`chart`", "`image`", "2%", "1%",
                                                                  "`engineALoopsDropped`", "`finish/combined-figures.md`")),
        "the reference: the delivery": "`![](<X-圖/page-NNNN-K.png>)`" in section and "`delivered.json`" in section,
        "the reference: plates": all(w in section for w in ("**影印嘅原件**", "**簡體字書**", "`simplified_script`",
                                                             "冇讀，或者都讀成繁體", "一半或以上", "印刷字")),
        "the reference: engine B's boxes": all(w in section for w in ("**引擎 B 框住嘅圖**", "`(0,0),(999,999)`",
                                                                     "漢字或者假名")),
        "the reference: their text taken out, listed": all(w in section for w in (
            "`_figures.take_out`", "`removed`", "`finish/boundaries.json`", "冇一個字靜靜咁冇咗")),
        "the reference: the limits": all(w in section for w in ("淨係引擎 A 搵到嘅區域", "引擎 A 當字讀咗嘅圖",
                                                                "圖入面嘅字")),
    }
    for name, ok in wanted.items():
        check(ok, f"the skill's text: {name}")


def main() -> int:
    selection_checks()
    with tempfile.TemporaryDirectory(prefix="figures-") as name:
        tmp = Path(name)
        cut_checks(tmp)
        place_checks(tmp)
        doubt_checks()
        taken_doubt_checks(tmp)
        find_checks(tmp)
        chain_checks(tmp)
        grounding_chain_checks(tmp)
    plate_checks()
    grounding_checks()
    take_out_checks()
    skill_checks()
    print(f"{'FAIL' if FAILURES else 'ok  '} figures kept as pictures: {len(FAILURES)} problem(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
