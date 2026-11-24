#!/usr/bin/env python3
"""Offline check of the book repair tool's book model (scripts/_repair_model.py):
each entry learned from a made-up book (tests/repair_fixture.py), and each
gate failing where the book does not show what it measures.

No model, no GPU, no corpus.  The made-up book prints a running head in the
outer column (right on odd scans, left on even), a run suffix under it and
the folio at its foot; contents on scans 2-3; two runs, each opened by a
divider that prints the suffix in display type; the second run skips two
leaves.

- M-1/M-2/M-2b: the band per parity from the folio blocks; the stem from the
  profile, or - with no profile head - the band's shared core; a misread
  head block still matches the stem, and its misread is learned.  On
  contents pages of short blocks, a line in the parity's folio strip on a
  scan whose own head lies further out, and lines under a number block
  spanning several columns, are not the band's.  Nor are a line of the body
  that reads like the head (phase 3's furniture_band takes it by its text),
  a numeral block at the far side of the page, or the stem set in display
  type (with what lies in its column).
- M-3/M-4: the suffix runs; the folio offset per run, a jump where leaves
  are missing (and the folios it skips); a folio engine A misread as
  Arabic figures on one page does not move the fit.
- M-5: the dividers are the pages printing the suffix in display type; a
  page of the run holding the suffix as an ordinary body block is not one.
- M-6: the contents pages, their lines mapped in order onto the titles, and
  the contents word; a list page that maps nothing is not contents.  Nor is
  a page of the body whose title and date line map (onto the contents, onto
  another date) between paragraphs that hold most of its text, nor a cover
  printed again at the end of the book (every mapped line on one page); the
  contents they stand beside stay the contents.  A contents whose lines the
  OCR ran together with the date lines printed under its titles (the date
  before the next title, a `<br>` between, a line holding two entries, the
  page on one line) is read as its printed lines: a contents, each title
  an entry of its own; a `<br>` at a title's wrap and an ellipsis inside a
  title stay inside it.  The map takes each body line once.
- M-7: engine B's mark aliases, and a glyph it writes for engine A's
  bracketed figure on four pages is a figure stand-in, while a character
  engine A skipped once is not, and neither is a CJK numeral engine B reads
  for engine A's figure on four pages.
- M-9: the body glyph; M-10: the sentence-final marks.
- Failing gates: no folio blocks (M-1), no notation (M-10), no running head
  anywhere (M-2).

Usage:
    python3 tests/check_repair_model.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import offline  # noqa: E402,F401
import repair_fixture as fx  # noqa: E402

sys.path.insert(0, str(HERE.parent / "scripts"))
from jyut_ocr.repair import inputs as ri  # noqa: E402
from jyut_ocr.repair import model as rm  # noqa: E402


FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"{'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def pp_cjk(text: str) -> str:
    return fx.pp.CJK(text)


def learn(root: Path, spec, **kwargs) -> dict:
    fx.write_book(root, spec, **kwargs)
    return rm.learn(ri.load_book(ri.InputPaths.resolve(root, {})))


def contents_lookalikes() -> None:
    """M-6 on page texts alone: a contents, units whose first page is a
    title, a date line and one long paragraph, and a cover printed again as
    the last page."""
    cover = "# 青山文叢全編\n\n春山居士著\n\n青山書局印行\n"
    titles = {5: "春日遊記", 7: "秋夜讀書", 9: "山居雜詠", 11: "江上聽雨"}
    texts = {1: cover, 13: cover.lstrip("# ")}
    texts[3] = "# 目次\n\n" + "\n\n".join(f"{title}……{scan}" for scan, title in titles.items()) + "\n"
    for scan in (2, 4, 6, 8, 10, 12):
        texts[scan] = fx.paragraph(scan, 200) + "\n"
    for scan, title in titles.items():
        # The first two units open with the same date line: each maps its
        # title onto the contents and its date onto the other's.
        date = "三年五月七日\n\n" if scan in (5, 7) else fx.paragraph(scan + 30, 80) + "\n\n"
        texts[scan] = f"# {title}\n\n{date}{fx.paragraph(scan, 300)}\n"
    found = rm.learn_contents(texts, [])
    pages = [run["pages"] for run in found["runs"]]
    check(pages == [[3]], f"M-6: a unit's first page (its title and date line map, its paragraph holds its text) "
                          f"and a cover printed again are no contents; the contents stay ({pages})")
    mapped = [(e["text"], e.get("matchedPage")) for run in found["runs"] for e in run["entries"]]
    check(mapped == [("目次", None)] + [(title, scan) for scan, title in titles.items()],
          f"M-6: the contents' entries still map onto the units those pages open ({mapped})")
    # The cover and its reprint each map every line onto the other: refused
    # as contents on the count of pages mapped onto, and returned as such
    # (the repair tool's doubt); the units' first pages, dropped by their
    # character share, are not.
    rejected = [(run["pages"], run["bodyPages"]) for run in found.get("rejected") or []]
    check(rejected == [([1], 1), ([13], 1)],
          f"M-6: a list-like run refused on the two-page guard is returned under `rejected` ({rejected})")
    # A title leaf before the contents - two short lines that map onto the
    # cover over a long paragraph - maps two lines in three and joins the
    # list-like run; by its own character share it is dropped, and the
    # contents stay (judged with it, the run would hold too little of its
    # text in mapped lines and the contents would be lost whole).
    texts[2] = "青山文叢全編\n\n春山居士著\n\n" + fx.paragraph(2, 200) + "\n"
    found = rm.learn_contents(texts, [])
    pages = [run["pages"] for run in found["runs"]]
    check(pages == [[3]], f"M-6: a title leaf whose two short lines map, over a long paragraph, is dropped from "
                          f"the contents run by its character share; the contents stay ({pages})")
    # A title page whose title and author the reprint prints again and whose
    # publisher a page after that repeats maps onto two pages, but two of its
    # three lines onto one: that page printed again, no contents.
    texts[2] = fx.paragraph(2, 200) + "\n"
    texts[13] = "青山文叢全編\n\n春山居士著\n"
    texts[14] = fx.paragraph(14, 200) + "\n\n青山書局印行\n"
    found = rm.learn_contents(texts, [])
    pages = [run["pages"] for run in found["runs"]]
    rejected = {run["pages"][0]: run["why"] for run in found.get("rejected") or []}
    check(pages == [[3]] and "one other page, over" in rejected.get(1, ""),
          f"M-6: a title page mapping more than half its lines onto one other page is refused as that page "
          f"printed again, though its lines map onto two pages; the contents stay ({pages}, {rejected.get(1)})")


def contents_run_together() -> None:
    """M-6 on a contents whose printed lines - each title with its leaders
    and page, its date line under it - the OCR ran together: a date before
    the next title, a `<br>` between title and date, a line holding two
    entries, a date after leaders that lost their page number, and the
    whole page on one line; a `<br>` where a title wraps and an ellipsis
    inside a title, with no date beside them, stay inside the title.  And
    the map takes no target twice."""
    titles = {5: "春日遊記", 7: "秋夜讀書", 9: "山居雜詠", 11: "江上聽雨", 13: "晨鐘暮鼓", 15: "田園小記",
              17: "雲外鐘聲古寺晚眺", 19: "從……到……的轉變"}
    dates = {5: "三年正月初五日", 7: "三年閏五月七日批", 9: "三年五月九日", 11: "三年五月十一日", 13: "三年五月十三日",
             15: "三年五月十五日", 17: "三年五月十七日", 19: "三年五月十九日"}
    texts = {scan: fx.paragraph(scan, 200) + "\n" for scan in (2, 4, 6, 8, 10, 12, 14, 16, 18, 20)}
    for scan, title in titles.items():
        texts[scan] = f"# {title}\n\n{dates[scan]}\n\n{fx.paragraph(scan, 300)}\n"
    lines = ["春日遊記……5", "三年正月初五日秋夜讀書……7", "- 三年閏五月七日批<br>山居雜詠……9",
             "- 江上聽雨……11<br>三年五月十一日", "晨鐘暮鼓……13三年五月十三日田園小記……15", "雲外鐘聲<br>古寺晚眺……17",
             "從……到……的轉變……三年五月十九日"]
    rows = [("目次", None), ("春日遊記", 5), ("三年正月初五日", 5), ("秋夜讀書", 7), ("三年閏五月七日批", 7),
            ("山居雜詠", 9), ("江上聽雨", 11), ("三年五月十一日", 11), ("晨鐘暮鼓", 13), ("三年五月十三日", 13),
            ("田園小記", 15), ("雲外鐘聲古寺晚眺", 17), ("從到的轉變", 19), ("三年五月十九日", 19)]
    # And the same lines sealed as one, their bullets gone: read whole, the
    # page would be one long line, no list.
    for name, page in (("lines run together", "\n\n".join(lines)),
                       ("the page on one line", "".join(line.removeprefix("- ") for line in lines))):
        texts[3] = f"# 目次\n\n{page}\n"
        found = rm.learn_contents(texts, [])
        pages = [run["pages"] for run in found["runs"]]
        read = [(e["text"], e.get("matchedPage")) for run in found["runs"] for e in run["entries"]]
        check(pages == [[3]], f"M-6: a contents whose titles and date lines the OCR ran together is a contents "
                              f"({name}: {pages})")
        check(read == rows,
              f"M-6: such a line is read as its printed lines - cut after leaders and the page, at a `<br>` beside "
              f"a date line, after a date that opens a title (the note after a date stays with it), not at a "
              f"`<br>` or an ellipsis inside a title - each title an entry mapped onto its unit ({name}: {read})")
    mapped = rm.map_in_order(["春日遊記", "春日遊記", "秋夜讀書"], ["春日遊記", "秋夜讀書"])
    check(mapped == [(0, 0, 1.0), None, (2, 1, 1.0)],
          f"M-6: the map takes each target once, the targets in order ({mapped})")


def figure_standins(spec) -> None:
    """Engine A reads （1） after a paragraph's third character on four
    pages, where engine B reads 丁 between two bracket strokes; on four
    other pages engine A reads （5） where engine B reads the numeral 五 (the
    figure in the other script); on one page engine B reads a character 竹
    that engine A skipped."""
    for scans, figure, glyph in (((6, 8, 10, 14), "1", "丁"), ((7, 12, 17, 19), "5", "五")):
        for scan in scans:
            columns = fx.layout(spec[scan])["columns"]
            k = next(i for i, c in enumerate(columns) if c["label"] == "vertical_text")
            text = columns[k]["text"]
            cut = sum(1 for _ in text[:3])
            spec[scan]["aMarks"] = {k: text[:cut] + f"（{figure}）" + text[cut:]}
            spec[scan]["bEdits"] = [(text[:cut].replace("，", "丶").replace("。", "口"),
                                     text[:cut].replace("，", "丶").replace("。", "口") + f"一{glyph}一")]
    columns = fx.layout(spec[11])["columns"]
    k = next(i for i, c in enumerate(columns) if c["label"] == "vertical_text")
    text = columns[k]["text"][:4].replace("，", "丶").replace("。", "口")
    spec[11]["bEdits"] = [(text, text + "竹")]


def main() -> int:
    contents_lookalikes()
    contents_run_together()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        spec = fx.make_spec()
        spec[9]["aFolio"] = "11"                       # engine A reads 五 as Arabic figures
        spec[16]["aHeadless"] = True                   # engine A misses the head here
        spec[8]["items"].append(("line", "甲編"))       # the suffix as an ordinary body line
        figure_standins(spec)
        model = learn(tmp / "book", spec)
        gates = model["gates"]
        check(all(gates[g]["passed"] for g in ("M-1", "M-2", "M-3", "M-4", "M-6", "M-10"))
              and not {"M-8", "M-8b"} & set(gates) and "geometry" not in model,
              f"every gate passes on the made-up book: {[g for g, v in gates.items() if not v['passed']]} failed")
        parities = model["band"]["parities"]
        check(parities["odd"]["side"] == "right" and parities["even"]["side"] == "left",
              "M-1: the band is the right column on odd scans, the left on even")
        band7 = [b["text"] for b in model["pages"]["7"]["band"]]
        check(band7 == [fx.HEAD, "甲編", "三"], f"M-1: a page's band holds its head, suffix and folio (got {band7})")
        check(model["stems"] == [fx.HEAD] and model["stemMethod"] == "profile", "M-2: the stem is the profile's head")
        runs = [(r["suffix"], r["first"], r["last"]) for r in model["suffixRuns"]]
        check(runs == [(fx.CONTENTS_WORD, 2, 4), ("甲編", 5, 12), ("乙編", 13, 20)], f"M-3: the suffix runs ({runs})")
        offsets = [[s["offset"] for s in r["stretches"]] for r in model["folio"]["runs"]]
        check(offsets == [[1], [4], [12, 10]], f"M-4: the folio offsets per run, with the jump ({offsets})")
        jumps = model["folio"]["jumps"]
        check(len(jumps) == 1 and jumps[0]["kind"] == "leaves-missing" and
              (jumps[0]["missingFrom"], jumps[0]["missingTo"]) == (5, 6) and jumps[0]["before"] == 17,
              f"M-4: the jump skips folios 5-6 before scan 17 ({jumps})")
        check(model["pages"]["9"]["folio"] == 5 and model["pages"]["18"]["folio"] == 8,
              "M-4: a page's expected folio, also where engine A misread it and after the jump")
        check(model["pages"]["16"]["suffix"] == "乙編" and model["pages"]["16"]["folio"] == 4,
              "a page whose head engine A missed keeps its run and folio (engine B read them)")
        check([d["page"] for d in model["dividers"]] == [5, 13],
              f"M-5: the dividers are the display-type pages; the body line on scan 8 is not one "
              f"({model['dividers']})")
        check(model["contentsPages"] == [2, 3], f"M-6: the contents pages ({model['contentsPages']})")
        entries = [e for r in model["contents"]["runs"] for e in r["entries"] if e.get("matchedPage")]
        check([(e["text"], e["matchedPage"]) for e in entries][:2] == [("春日遊記", 5), ("秋夜讀書", 7)]
              and len(entries) == 7 and model["contents"]["runs"][0]["contentsWord"] == fx.CONTENTS_WORD,
              "M-6: every contents entry maps, in order, onto its title; the contents word is the run's suffix")
        standins = model["standIns"]
        check({"丶", "口"} <= set(standins["markAliases"]["B"]),
              f"M-7a: engine B's mark aliases, as phase 3 measures them ({standins['markAliases']})")
        check("丁" in standins["figure"] and "竹" not in rm.standin_glyphs(model),
              f"M-7b: 丁 is a figure stand-in, 竹 (a character engine A skipped) is not ({standins['figure']})")
        check("五" not in standins["figure"] and "五" in standins["numeralsAtFigures"],
              f"M-7b: the numeral 五 engine B reads where engine A read （5） is the figure in the other script, "
              f"not a stand-in ({standins['figure']}, {standins.get('numeralsAtFigures')})")
        check(abs(model["glyph"] - fx.PITCH) <= 2, f"M-9: the body glyph is the cell ({model['glyph']})")
        check("。" in model["sentenceFinal"], "M-10: the sentence-final marks the book prints")

        model = learn(tmp / "no-head-profile", fx.make_spec(), profile={"notation": {"marks": ["。"]}})
        check(model["stems"] == [fx.HEAD] and model["stemMethod"] == "band core",
              f"M-2: with no head in the profile the stem is the band's shared core ({model['stems']})")

        spec = fx.make_spec()
        for page in spec.values():
            page["folio"] = None
        model = learn(tmp / "no-folio", spec, profile={"layout": {"runningHeads": [fx.HEAD]}})
        check(not model["gates"]["M-1"]["passed"] and not model["gates"]["M-10"]["passed"],
              "M-1 fails with no folio blocks; M-10 fails with no notation")
        spec = fx.make_spec()
        for page in spec.values():
            page["suffix"] = None
            page["folio"] = None
        model = learn(tmp / "no-head", spec, profile={"notation": {"marks": ["。"]}})
        check(not model["gates"]["M-2"]["passed"] and not model["gates"]["M-3"]["passed"],
              "M-2 and M-3 fail on a book that prints no running head")
        # Contents pages of short blocks only.  Scan 3 sits a column off the
        # other odd scans: its head lies further out and its first contents
        # line in the odd scans' folio strip, and engine A read no folio on
        # it.  On scan 2 engine A read the figures printed at the foot of
        # four contents columns as one number block.
        shifted = fx.make_spec()
        shifted[3].update(bandX=770, firstX=718, aNoFolio=True)
        shifted[2]["aFolioBox"] = (300, 1000, 160, 16)
        shifted[2]["aFolio"] = "1234"
        model = learn(tmp / "shifted-contents", shifted)
        band2 = [b["text"] for b in model["pages"]["2"]["band"]]
        band3 = [b["text"] for b in model["pages"]["3"]["band"]]
        check(band3 == [fx.HEAD, fx.CONTENTS_WORD],
              f"M-1: a contents line in the parity's folio strip, on a scan whose own head lies further out, "
              f"is not the band's ({band3})")
        check(all(pp_cjk(t) in (fx.HEAD, fx.CONTENTS_WORD, "") for t in band2),
              f"M-1: a number block spanning four columns marks no column: the contents lines under it are "
              f"not the band's ({band2})")
        runs = [(r["suffix"], r["first"], r["last"]) for r in model["suffixRuns"]]
        check(runs[:1] == [(fx.CONTENTS_WORD, 2, 4)],
              f"M-3: the contents word's run keeps its pages when contents lines stay out of the band ({runs})")

        short = rm.stem_matches("我們研究文學的方法又說文學", "文學史")
        long_ = rm.stem_matches("甲丙丁戊己庚說", "甲乙丙丁戊己庚")
        check(short == [] and long_ == [(0, 6)],
              f"M-2: a three-character stem's copy needs all three characters (a body word it begins with is "
              f"none), while a seven-character stem read with one character dropped still matches "
              f"({short}, {long_})")

        values = {text: rm.numeral_value(text) for text in ("一百零三", "一〇三", "二十五", "十", "一千零五十", "三三")}
        check(values == {"一百零三": 103, "一〇三": 103, "二十五": 25, "十": 10, "一千零五十": 1050, "三三": 33},
              f"M-4: a folio's value, a zero holding an empty place too ({values})")

        # A line of the body that reads like the head (phase 3's
        # furniture_band takes it by its text), and a numeral block labelled
        # number at the far side of the page: neither is where this book
        # prints its running head or its folio.
        placed = fx.make_spec()
        placed[10]["items"].insert(1, ("line", fx.HEAD))
        placed[9]["extraBlocks"] = [{"text": "五", "boundingBox": fx.box_of(60, 120, 16, 16), "blockLabel": "number"}]
        model = learn(tmp / "placed", placed)
        band10 = [b["text"] for b in model["pages"]["10"]["band"]]
        band9 = [b["text"] for b in model["pages"]["9"]["band"]]
        check(band10.count(fx.HEAD) == 1 and band9.count("五") == 1 and "五" == fx.numeral(5),
              f"M-1: a body line that reads like the head, and a numeral block at the far side of the page, are "
              f"not the band's; the page's own head and folio are ({band10}, {band9})")

        # Display type: a block holding the stem at twice the glyph of the
        # stem's other copies (a title page that prints the book's title,
        # which is its running head) leaves the band, with a block taken for
        # lying in its column.
        bands = {scan: [{"block": 0, "text": fx.HEAD, "label": "header", "box": [0.9, 0.1, 0.02, 0.1],
                         "why": "furniture_band: running head"}] for scan in (2, 3, 4, 5)}
        bands[6] = [{"block": 0, "text": fx.HEAD, "label": "paragraph_title", "box": [0.5, 0.2, 0.04, 0.2],
                     "why": "furniture_band: head's size, furniture text"},
                    {"block": 1, "text": "甲", "label": "text", "box": [0.5, 0.45, 0.04, 0.05],
                     "why": "furniture_band: column of block 0"}]
        removed = rm.drop_display_heads(bands, [fx.HEAD], {scan: (1000, 1000) for scan in bands})
        check([(r["page"], r["block"], r["alsoRemoved"]) for r in removed] == [(6, 0, [1])] and not bands[6]
              and all(len(bands[scan]) == 1 for scan in (2, 3, 4, 5)),
              f"M-1: a stem in display type leaves the band with the block in its column; the running heads "
              f"stay ({removed})")

        misread_book = fx.make_spec()
        misread_book[9]["headRead"] = fx.HEAD[0] + "丈" + fx.HEAD[2:]
        model = learn(tmp / "misread-head", misread_book)
        check(model["stemMisreads"].get(fx.HEAD[1]) == {"丈": 1} and
              any(b["text"] == misread_book[9]["headRead"] for b in model["pages"]["9"]["band"]),
              f"M-2b: a head block read with one character wrong is the band's, and the misread is learned "
              f"({model['stemMisreads']})")
    print(f"{'FAIL' if FAILURES else 'ok  '} check_repair_model: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
