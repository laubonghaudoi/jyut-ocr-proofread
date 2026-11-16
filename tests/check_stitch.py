#!/usr/bin/env python3
"""Offline checks of the cross-page table stitch (_stitch.py, stitch_tables.py).

No model, no GPU, no corpus: made-up pages, seals written the way phase 3
writes them, and blank renders.  Each case is a shape measured on the
calibration pages (see _stitch.py); the labels and figures here are invented.

Usage:
    python3 tests/check_stitch.py
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import offline  # noqa: E402,F401 - an endpoint nothing listens on
import _stitch  # noqa: E402
import _tables  # noqa: E402
import proofread_pages as pp  # noqa: E402
import stitch_tables  # noqa: E402

HEADER = ["年度", "甲捐", "乙捐", "丙捐", "合計"]
ROWS = [["元年度", "一、二〇〇", "三〇〇", "五〇", "一、五五〇"],
        ["二年度", "一、一〇〇", "四〇〇", "", "一、五〇〇"],
        ["三年度", "九〇〇", "五〇〇", "七〇", "一、四七〇"],
        ["四年度", "一、三〇〇", "", "八〇", "一、三八〇"],
        ["五年度", "八〇〇", "六〇〇", "九〇", "一、四九〇"]]
ROTATED = "block {}: rotated — 合計 reconciles down the column"


def markdown(rows: list[list[str]]) -> str:
    return _tables.render_markdown(_tables.find_tables(
        "\n".join(["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
                  + ["| " + " | ".join(r) + " |" for r in rows[1:]]))[0])


def seal(text: str, tables: list[tuple[int, str]] = (), orientation: list[str] | None = None,
         **extra) -> dict:
    """A page's seal as phase 3 writes it (only what the stitch reads)."""
    record = {"schemaVersion": pp.SCHEMA_VERSION, "status": "complete", "doubts": [],
              "auditorClean": True, "auditorReport": "", "mode": "merge",
              "tables": [{"block": block, "format": "markdown", "text": table} for block, table in tables],
              "tableOrientation": orientation if orientation is not None else [ROTATED.format(b) for b, _ in tables],
              "tablesMissing": [], "tablesRejected": [], **extra}
    return record


def page(number: int, text: str, record: dict, blocks=()) -> _stitch.PageInput:
    return _stitch.PageInput(number, text, record, list(blocks),
                             is_furniture=_stitch.furniture_test(list(blocks)))


def grid_of(text: str, index: int = 0) -> list[list[str]]:
    return _tables.find_tables(text)[index].grid()


def cjk_count(texts) -> Counter:
    return Counter(pp.CJK("".join(texts)))


def kinds(doubts) -> list[str]:
    return [d["kind"] for d in doubts]


def check_continuation_without_header() -> None:
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:])
    p1 = f"某某歲入表\n\n{head}"
    pages = [page(1, p1, seal(p1, [(1, head)])), page(2, part, seal(part, [(0, part)]))]
    result = _stitch.plan(pages)
    out1, out2 = result["pages"][1], result["pages"][2]
    merged = grid_of(out1["text"])
    assert merged == [HEADER] + ROWS, merged
    assert out1["text"].startswith("某某歲入表\n\n"), out1["text"]
    # The page whose only content was the moved table is left empty; the move
    # is in its seal, word for word.
    assert out2["text"] == "", out2["text"]
    assert out2["stitch"]["continuation"]["moved"] == part
    assert out2["stitch"]["continuation"]["head"] == {"page": 1, "table": 0}
    assert out1["stitch"]["head"]["pages"] == [1, 2] and out1["stitch"]["head"]["parts"][0]["header"] == "none"
    assert kinds(out1["doubts"]) == [_stitch.MERGED] and kinds(out2["doubts"]) == [_stitch.MERGED]
    # Nothing printed is lost or made up: every character of both pages is
    # still there, and every cell moved is in the head's table.
    assert cjk_count([p1, part]) == cjk_count([out1["text"], out2["text"]])
    moved = {cell for row in grid_of(part) for cell in row if cell}
    assert moved <= {cell for row in merged for cell in row}
    assert result["chains"][0]["records"] == 3 and result["chains"][0]["pages"] == [1, 2]
    print("stitch: a continuation with no header is appended to the head; its page is left empty")


def check_caption_kept() -> None:
    # The head written as HTML with its printed title as <caption>, no cell
    # merged: the merged table keeps the caption, so it stays HTML.
    head = _tables.render_html(replace(_tables.find_tables(markdown([HEADER] + ROWS[:2]))[0], caption="某某歲入表"))
    part = markdown(ROWS[2:])
    result = _stitch.plan([page(1, head, seal(head, [(0, head)])), page(2, part, seal(part, [(0, part)]))])
    merged = _tables.find_tables(result["pages"][1]["text"])[0]
    assert merged.caption == "某某歲入表" and merged.grid() == [HEADER] + ROWS, result["pages"][1]["text"]
    assert cjk_count([head, part]) == cjk_count([result["pages"][1]["text"], result["pages"][2]["text"]])
    print("stitch: a head's caption stays with the merged table")


def check_repeated_header() -> None:
    head = markdown([HEADER] + ROWS[:2])
    part = markdown([HEADER] + ROWS[2:])
    result = _stitch.plan([page(1, head, seal(head, [(0, head)])), page(2, part, seal(part, [(0, part)]))])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS
    stitched = result["pages"][1]["stitch"]["head"]["parts"][0]
    assert stitched["header"] == "repeated, dropped" and stitched["droppedHeader"] == HEADER, stitched
    # A label read the other way round on one page is the same label.
    turned = [HEADER[0], HEADER[1][::-1]] + HEADER[2:]
    part2 = markdown([turned] + ROWS[2:])
    result = _stitch.plan([page(1, head, seal(head, [(0, head)])), page(2, part2, seal(part2, [(0, part2)]))])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS
    print("stitch: a repeated label row is dropped (cells compared as sorted characters)")


def check_not_merged() -> None:
    head = markdown([HEADER] + ROWS[:2])
    base = [page(1, head, seal(head, [(0, head)]))]

    def run(text: str, record: dict | None = None, first: list = None) -> dict:
        return _stitch.plan((first or base) + [page(2, text, record or seal(text, [(0, text)]))])

    # A label row of its own, over figures: another table.
    other = markdown([["年度", "丁捐", "戊捐", "己捐", "合計"]] + ROWS[2:])
    result = run(other)
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "separate", result["decisions"]
    assert result["pages"][2]["text"] == other and not result["pages"][2]["doubts"]
    # A title before it: position does not hold, nothing is even asked.
    titled = f"丙表\n\n{markdown(ROWS[2:])}"
    result = run(titled)
    assert result["chains"] == [] and result["decisions"] == [] and result["pages"][2]["text"] == titled
    # Its own caption.
    captioned = _tables.render_html(replace(_tables.find_tables(markdown(ROWS[2:]))[0], caption="丙表"))
    result = run(captioned)
    assert result["chains"] == [] and "title of its own" in result["decisions"][0]["evidence"][-1]
    # The head is not the last thing on its page.
    text1 = f"{head}\n\n右表係各年之數"
    result = _stitch.plan([page(1, text1, seal(text1, [(0, head)])),
                           page(2, markdown(ROWS[2:]), seal(markdown(ROWS[2:]), [(0, markdown(ROWS[2:]))]))])
    assert result["chains"] == [] and result["decisions"] == []
    # Another number of fields and nothing that says which is which: left as
    # two tables, both pages flagged.
    short = markdown([r[:4] for r in ROWS[2:]])
    result = run(short)
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided"
    assert kinds(result["pages"][1]["doubts"]) == [_stitch.UNDECIDED] == kinds(result["pages"][2]["doubts"])
    assert result["pages"][2]["text"] == short and result["pages"][1]["text"] == head
    # A first row that reads as labels while the head has none: the pages
    # disagree about what the rows are - flagged, not merged, not separated.
    bare = markdown(ROWS[:2])
    result = _stitch.plan([page(1, bare, seal(bare, [(0, bare)])), page(2, other, seal(other, [(0, other)]))])
    assert result["decisions"][0]["verdict"] == "undecided" and "head starts with none" in \
        result["decisions"][0]["evidence"][-1], result["decisions"]
    # A first record the markup puts in header cells (<th>): its figures say
    # it is a record, the markup says labels - flagged on both pages, not
    # separated.  The same records in Markdown are merged.  Header cells
    # holding labels and no figure still start a table of their own.
    part_t = _tables.find_tables(markdown(ROWS[2:]))[0]
    in_th = _tables.render_html(replace(part_t, kind="html", cells=[replace(c, header=(c.row == 0))
                                                                    for c in part_t.cells]))
    result = run(in_th)
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided", result["decisions"]
    assert "header cells but holds figures" in result["decisions"][0]["evidence"][-1]
    assert kinds(result["pages"][1]["doubts"]) == [_stitch.UNDECIDED] == kinds(result["pages"][2]["doubts"])
    assert run(markdown(ROWS[2:]))["decisions"][0]["verdict"] == "merge"
    text_t = _tables.find_tables(markdown([["類別", "摘要", "備考", "附記", "說明"]] + ROWS[2:]))[0]
    text_th = _tables.render_html(replace(text_t, kind="html"))
    assert run(text_th)["decisions"][0]["verdict"] == "separate"
    # A lone digit keeps a row from being a label row: a record of counts.
    counts = markdown([["某書", "四", "卷上", "兩册", "未頒"], ["某書", "一〇〇", "卷下", "一册", "已頒"]])
    counts_head = markdown([["書名", "册數", "卷", "裝", "頒否"], ["某書", "一二〇", "卷首", "一册", "已頒"]])
    result = _stitch.plan([page(1, counts_head, seal(counts_head, [(0, counts_head)])),
                           page(2, counts, seal(counts, [(0, counts)]))])
    assert result["decisions"][0]["verdict"] == "merge", result["decisions"]
    # Not the next scan page: nothing merged.  The single skill keeps the
    # scan-gap guard (a blocked decision and a doubt on both pages, where
    # position holds on both sides of the gap), which the benchmark does not
    # decide (decision 1: the kept skill's behaviour stands there).
    part = markdown(ROWS[2:])
    result = _stitch.plan(base + [page(3, part, seal(part, [(0, part)]))])
    assert result["chains"] == [] and [d["verdict"] for d in result["decisions"]] == ["blocked"]
    assert result["pages"][3]["text"] == part and kinds(result["pages"][3]["doubts"]) == [_stitch.BLOCKED]
    # A series of titled tables printed with one header on the head's page:
    # a table repeating that header is the next of the series.
    series = f"第一表\n\n{head}\n\n第二表\n\n{head}"
    again = markdown([HEADER] + ROWS[2:])
    result = _stitch.plan([page(1, series, seal(series, [(0, head), (1, head)])),
                           page(2, again, seal(again, [(0, again)]))])
    assert result["chains"] == [] and "series" in result["decisions"][0]["evidence"][-1]
    print("stitch: another label row, a title, a caption, text after the head, other field counts, "
          "a gap and a titled series are not merged")


def check_continuation_and_new_table() -> None:
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:4])
    new_header = ["項目", "子費", "丑費"]
    new = markdown([new_header, ["一月", "一〇", "二〇"]])
    new_more = markdown([["二月", "三〇", "四〇"], ["三月", "五〇", "六〇"]])
    text2 = f"{part}\n\n乙表支出\n\n{new}"
    result = _stitch.plan([page(1, head, seal(head, [(0, head)])),
                           page(2, text2, seal(text2, [(0, part), (2, new)])),
                           page(3, new_more, seal(new_more, [(0, new_more)]))])
    out2 = result["pages"][2]["text"]
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS[:4]
    # Only the first content of p.2 went; its title and new table stay, and
    # the new table continues on p.3 in its turn.
    assert out2.startswith("乙表支出\n\n"), out2
    assert grid_of(out2) == [new_header, ["一月", "一〇", "二〇"], ["二月", "三〇", "四〇"], ["三月", "五〇", "六〇"]]
    assert result["pages"][3]["text"] == ""
    stitch2 = result["pages"][2]["stitch"]
    assert stitch2["continuation"]["head"]["page"] == 1 and stitch2["head"]["pages"] == [2, 3], stitch2
    assert [c["head"] for c in result["chains"]] == [1, 2]
    print("stitch: a page with a continuation and a new table gives up only the continuation")


def check_orientation_from_head() -> None:
    head = markdown([HEADER] + ROWS[:2])
    # The continuation's own page kept the engine's grid: fields down, records
    # across, printed right to left (what orient_table would have turned).
    part_rows = ROWS[2:]
    kept = _tables.rotate_counterclockwise(_tables.find_tables(markdown(part_rows))[0])
    kept = _tables.rotate_counterclockwise(_tables.rotate_counterclockwise(kept))
    kept_md = _tables.render_markdown(kept)
    assert _tables.find_tables(kept_md)[0].rows == len(HEADER)
    result = _stitch.plan([page(1, head, seal(head, [(0, head)])),
                           page(2, kept_md, seal(kept_md, [(0, kept_md)], ["block 0: kept — RECORD: row"]))])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS, grid_of(result["pages"][1]["text"])
    assert "turned to the head's" in result["pages"][1]["stitch"]["head"]["parts"][0]["orientation"]
    print("stitch: a continuation its page oriented the other way is turned to the head's orientation")


def check_line_of_labels() -> None:
    labels = markdown([[label] for label in HEADER])
    part = markdown(ROWS)
    result = _stitch.plan([
        page(1, labels, seal(labels, [(4, labels)], [f"block 4: {pp.SINGLE_LINE_KEPT} (5 x 1); ..."])),
        page(2, part, seal(part, [(0, part)]))])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS
    print("stitch: a lone column of labels becomes the label row of the table it heads")


def check_furniture_and_blocked() -> None:
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:])
    folio, running = "二四", "某書第三編會計"
    blocks1 = [{"label": "table", "text": "<table></table>", "box": [0.1, 0.1, 0.6, 0.8]},
               {"label": "number", "text": folio, "box": [0.9, 0.9, 0.02, 0.02]},
               {"label": "header", "text": running, "box": [0.9, 0.1, 0.02, 0.4]}]
    text1 = f"{head}\n\n{running}\n{folio}"
    blocks2 = [{"label": "number", "text": "二五", "box": [0.9, 0.9, 0.02, 0.02]},
               {"label": "table", "text": "<table></table>", "box": [0.1, 0.1, 0.6, 0.8]}]
    text2 = f"二五\n\n{part}"
    result = _stitch.plan([page(1, text1, seal(text1, [(0, head)]), blocks1),
                           page(2, text2, seal(text2, [(1, part)]), blocks2)])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS
    # The furniture stays where it was.
    assert result["pages"][1]["text"].endswith(f"{running}\n{folio}") and result["pages"][2]["text"] == "二五"
    # A folio engine A read as something else, written as engine B read it:
    # engine A reads the folio after the table (the page's last block), and
    # the line of numerals is the page's last.
    garbled = [blocks1[0], blocks1[2], dict(blocks1[1], text="$\\frac{1}{li}$")]
    text_g = f"{head}\n\n二四"
    result = _stitch.plan([page(1, text_g, seal(text_g, [(0, head)]), garbled),
                           page(2, text2, seal(text2, [(1, part)]), blocks2)])
    assert grid_of(result["pages"][1]["text"]) == [HEADER] + ROWS, result["decisions"]
    # Without a folio block on the page a line of numerals is text; so it is
    # at the other edge from the folio, and where engine A read it as a block
    # of its own.
    for blocks in ([blocks1[0]], [garbled[2], blocks1[0], blocks1[2]],
                   garbled[:2] + [{"label": "text", "text": "二四", "box": [0.5, 0.5, 0.05, 0.02]}, garbled[2]]):
        result = _stitch.plan([page(1, text_g, seal(text_g, [(0, head)]), blocks),
                               page(2, text2, seal(text2, [(1, part)]), blocks2)])
        assert result["chains"] == [], (blocks, result["decisions"])
    # A title printed before the next table, engine A labelling it header (a
    # form's printed title is): furniture only by that label, so it is the
    # next table's title - nothing decided, the title stays with its table.
    other = markdown(ROWS[2:])
    titled = f"丙表\n\n{other}"
    for label in ("paragraph_title", "header"):
        title_blocks = [{"label": label, "text": "丙表", "box": [0.70, 0.08, 0.13, 0.03]},
                        {"label": "table", "text": "<table></table>", "box": [0.2, 0.13, 0.67, 0.75]}]
        result = _stitch.plan([page(1, text1, seal(text1, [(0, head)]), blocks1),
                               page(2, titled, seal(titled, [(1, other)]), title_blocks)])
        assert result["chains"] == [] and result["decisions"] == [] and result["pages"][2]["text"] == titled, \
            (label, result["decisions"])
    # A title in Latin letters, or of numerals alone, on a page with a folio:
    # the folio read before the page's other blocks, the title read as a block
    # of its own - or no other block to put the folio on either side of it.
    for title in ("Table 3", "（三）"):
        text3 = f"{title}\n\n{other}"
        title_block = {"label": "paragraph_title", "text": title, "box": [0.5, 0.05, 0.1, 0.03]}
        for blocks in ([blocks2[0]], [blocks2[0], title_block, blocks2[1]]):
            result = _stitch.plan([page(1, text1, seal(text1, [(0, head)]), blocks1),
                                   page(2, text3, seal(text3, [(len(blocks) - 1, other)]), blocks)])
            assert result["chains"] == [] and result["pages"][2]["text"] == text3, (title, result["decisions"])
    # The next page's table was refused by the formatter and written flat:
    # nothing to merge, and both pages say why.
    flat = "二五 三年度九〇〇五〇〇七〇一、四七〇"
    record2 = seal(flat, [(1, part)], tablesMissing=[1])
    result = _stitch.plan([page(1, text1, seal(text1, [(0, head)]), blocks1), page(2, flat, record2, blocks2)])
    assert result["chains"] == [] and kinds(result["pages"][2]["doubts"]) == [_stitch.BLOCKED]
    assert kinds(result["pages"][1]["doubts"]) == [_stitch.BLOCKED] and result["pages"][2]["text"] == flat
    print("stitch: running heads and folios do not break a chain, a title does whatever engine A labelled it; "
          "a continuation written flat is flagged")


# --- the files: seals, idempotence, resume -------------------------------------

def write_page(directory: Path, number: int, text: str, record: dict) -> None:
    from PIL import Image
    renders, drafts = directory / "renders", directory / "drafts"
    renders.mkdir(exist_ok=True)
    drafts.mkdir(exist_ok=True)
    render = renders / f"page-{number:04d}.png"
    if not render.is_file():
        Image.new("L", (300, 400), 255).save(render)
    draft = drafts / f"page-{number:04d}.txt"
    draft.write_text(pp.CJK(text), encoding="utf-8")
    record = dict(record, scanPage=number, provenance={
        "renderSha256": pp.sha256_file(render), "draftSha256": pp.sha256_file(draft),
        "markdownSha256": pp.sha256_bytes(text.encode("utf-8"))})
    record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
    out = directory / "out"
    out.mkdir(exist_ok=True)
    (out / f"page-{number:04d}.md").write_bytes(text.encode("utf-8"))
    (out / f"page-{number:04d}.json").write_bytes(pp.canonical_json_bytes(record))


def current(directory: Path, number: int) -> tuple[bool, str]:
    out = directory / "out"
    job = pp.PageJob(number, directory / "renders" / f"page-{number:04d}.png",
                     directory / "drafts" / f"page-{number:04d}.txt", None, None,
                     out / f"page-{number:04d}.json", out / f"page-{number:04d}.md")
    expect = {"renderSha256": pp.sha256_file(job.render), "draftSha256": pp.sha256_file(job.draft_text)}
    return pp.validate_completion(job, expect)


def snapshot(directory: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((directory / "out").iterdir())
            if not p.name.startswith(".")}


def check_files() -> None:
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:4])
    last = markdown(ROWS[4:])
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        text1 = f"某某歲入表\n\n{head}"
        write_page(tmp, 1, text1, seal(text1, [(1, head)], doubts=[{"kind": "punctuation-whole-page",
                                                                    "detail": "PUNCTUATED AS A WHOLE PAGE"}],
                                       auditorClean=False, auditorReport="PUNCTUATED AS A WHOLE PAGE"))
        write_page(tmp, 2, part, seal(part, [(0, part)]))
        write_page(tmp, 3, last, seal(last, [(0, last)]))
        out = tmp / "out"
        report = stitch_tables.stitch_directory(out, tmp / "renders", tmp / "drafts", log=lambda line: None)
        assert report["pagesRewritten"] == [1, 2, 3], report
        assert grid_of((out / "page-0001.md").read_text()) == [HEADER] + ROWS
        record1 = json.loads((out / "page-0001.json").read_text())
        # The page's own doubts stay; the stitch's are added; the seal holds.
        assert kinds(record1["doubts"]) == ["punctuation-whole-page", _stitch.MERGED], record1["doubts"]
        assert record1["tableStitch"]["before"] == text1 and not record1["auditorClean"]
        for number in (1, 2, 3):
            assert current(tmp, number) == (True, "current"), (number, current(tmp, number))
        assert (out / "page-0002.md").read_text() == "" and (out / "page-0003.md").read_text() == ""
        assert (out / "page-0002-stitch-01.png").is_file() and (out / "page-0003-stitch-01.png").is_file()

        # Twice gives the same bytes, and writes nothing.
        first = snapshot(tmp)
        report = stitch_tables.stitch_directory(out, tmp / "renders", tmp / "drafts", log=lambda line: None)
        assert report["pagesRewritten"] == [] and snapshot(tmp) == first

        # p.2 proofread again (a fresh seal, no tableStitch, one figure
        # changed): the chain is rebuilt from p.1's text as proofread.
        fixed = markdown([ROWS[2], ["四年度", "一、三〇〇", "", "八〇", "一、三八一"]])
        write_page(tmp, 2, fixed, seal(fixed, [(0, fixed)]))
        stitch_tables.stitch_directory(out, tmp / "renders", tmp / "drafts", log=lambda line: None)
        assert grid_of((out / "page-0001.md").read_text())[4][-1] == "一、三八一"
        assert all(current(tmp, n) == (True, "current") for n in (1, 2, 3))

        # p.2 proofread again with a title before its table: the chain is gone,
        # p.1 and p.3 are back as proofread, and their seals say nothing of it.
        titled = f"丙表\n\n{fixed}"
        write_page(tmp, 2, titled, seal(titled, [(0, fixed)]))
        stitch_tables.stitch_directory(out, tmp / "renders", tmp / "drafts", log=lambda line: None)
        assert (out / "page-0001.md").read_text() == text1
        record1 = json.loads((out / "page-0001.json").read_text())
        assert "tableStitch" not in record1 and kinds(record1["doubts"]) == ["punctuation-whole-page"]
        assert record1["auditorReport"] == "PUNCTUATED AS A WHOLE PAGE"
        # p.2's new table continues on p.3 (it ends its page, p.3 starts with
        # records in its fields).
        assert grid_of((out / "page-0002.md").read_text()) == [ROWS[2], grid_of(fixed)[1], ROWS[4]]
        assert not (out / "page-0002-stitch-01.png").exists() and (out / "page-0003-stitch-01.png").is_file()
        assert all(current(tmp, n) == (True, "current") for n in (1, 2, 3))

        # A dry run writes nothing.
        before = snapshot(tmp)
        write_page(tmp, 2, fixed, seal(fixed, [(0, fixed)]))
        after_write = snapshot(tmp)
        stitch_tables.stitch_directory(out, tmp / "renders", tmp / "drafts", dry_run=True, log=lambda line: None)
        assert snapshot(tmp) == after_write != before
    print("stitch files: resealed and current, the page's own doubts kept, twice gives the same bytes, "
          "a page proofread again rebuilds or undoes its chain, a dry run writes nothing")


def check_pages_sealed_while_planning() -> None:
    """Another run sharing the directory proofreads a page again while the
    stitch plans: the stitch never writes a plan built from the page before
    it over the new one, and a page's seal waits for the stitch's lock."""
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:])
    fresh = markdown([ROWS[2], ["四年度", "一、三〇〇", "", "八〇", "一、三八一"], ROWS[4]])
    real_plan = _stitch.plan
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_page(tmp, 1, head, seal(head, [(0, head)]))
        write_page(tmp, 2, part, seal(part, [(0, part)]))
        # p.2 sealed again, by a writer outside the lock, after the stitch read it.
        calls = []

        def plan_while_sealed(pages, *a, **k):
            out = real_plan(pages, *a, **k)
            if not calls:
                write_page(tmp, 2, fresh, seal(fresh, [(0, fresh)]))
            calls.append(1)
            return out
        _stitch.plan = plan_while_sealed
        try:
            logged: list[str] = []
            report = stitch_tables.stitch_directory(tmp / "out", tmp / "renders", tmp / "drafts", log=logged.append)
        finally:
            _stitch.plan = real_plan
        record2 = json.loads((tmp / "out/page-0002.json").read_text())
        assert len(calls) == 2 and record2["tableStitch"]["before"] == fresh, (calls, record2.get("tableStitch"))
        assert grid_of((tmp / "out/page-0001.md").read_text())[4][-1] == "一、三八一"
        assert report["pagesRewritten"] == [1, 2] and "changed while the stitch planned" in logged[0], logged
        assert all(current(tmp, n) == (True, "current") for n in (1, 2))

        # A directory that keeps changing: nothing written, and the report says which pages.
        write_page(tmp, 2, part, seal(part, [(0, part)]))
        before = snapshot(tmp)

        def plan_always_changing(pages, *a, **k):
            out = real_plan(pages, *a, **k)
            (tmp / "out/page-0003.md").write_text(str(len(calls)))
            calls.append(1)
            return out
        _stitch.plan = plan_always_changing
        try:
            report = stitch_tables.stitch_directory(tmp / "out", tmp / "renders", tmp / "drafts", log=lambda line: None)
        finally:
            _stitch.plan = real_plan
        (tmp / "out/page-0003.md").unlink()
        assert report["pagesRewritten"] == [] and report["pagesChanging"] == ["page-0003.md"], report
        assert snapshot(tmp) == before

    # proofread_page seals under the lock: while the stitch holds it, a page
    # (a blank one - no model call) is not sealed; it is once the lock goes.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_page(tmp, 1, head, seal(head, [(0, head)]))
        (tmp / "drafts/page-0002.txt").write_text("")
        from PIL import Image
        Image.new("L", (300, 400), 255).save(tmp / "renders/page-0002.png")
        job = pp.PageJob(2, tmp / "renders/page-0002.png", tmp / "drafts/page-0002.txt", None, None,
                         tmp / "out/page-0002.json", tmp / "out/page-0002.md")
        arguments = pp.build_parser().parse_args([str(tmp / "renders"), str(tmp / "drafts"), str(tmp / "out")])
        client = type("StandIn", (), {"model": "stand-in", "effort": "xhigh"})()
        worker = threading.Thread(target=pp.proofread_page, args=(job, client, arguments))
        with stitch_tables.directory_lock(tmp / "out"):
            worker.start()
            worker.join(2.0)
            assert worker.is_alive() and not job.out_json.exists() and not job.out_md.exists()
        worker.join(30)
        assert not worker.is_alive() and json.loads(job.out_json.read_text())["mode"] == "blank-page"
    print("stitch files: a page sealed while the stitch planned is read again, a directory that keeps changing "
          "is left as it is, a page's seal waits for the stitch's lock")


def check_main_runs_the_stitch() -> None:
    """proofread_pages.main() stitches the directory when the run ends - also a
    run whose pages were all sealed before (a resumed run)."""
    head = markdown([HEADER] + ROWS[:2])
    part = markdown(ROWS[2:])
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        write_page(tmp, 1, head, seal(head, [(0, head)]))
        write_page(tmp, 2, part, seal(part, [(0, part)]))
        # The round-4 fixes off: what they seal is read off the drafts when
        # the run starts, so the provenance a page would be sealed with here
        # is the one without them (ROUND4_DEFAULTS).
        old_defaults = ["--no-" + switch[2:] for switch in pp.ROUND4_DEFAULTS]
        # The book profile, which gives the engine preference.
        (tmp / "profile.json").write_text(json.dumps({"preferEngine": "A"}))
        profile = ["--profile", str(tmp / "profile.json")]
        arguments = pp.build_parser().parse_args(
            [str(tmp / "renders"), str(tmp / "drafts"), str(tmp / "out"), *profile, *old_defaults])
        arguments.prefer_engine, arguments.fallback_engine = "A", "A"
        arguments.profile_sha = pp.profile_fingerprint({"preferEngine": "A"})
        provenance = pp.decision_provenance(arguments)
        for number in (1, 2):
            path = tmp / "out" / f"page-{number:04d}.json"
            record = json.loads(path.read_text())
            record["provenance"].update(provenance)
            record["provenance"].pop("jsonPayloadSha256")
            record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
            path.write_bytes(pp.canonical_json_bytes(record))
        # One engine's drafts here: two engines are the default, so the run
        # names the one-engine route (--single-engine).
        status = pp.main([str(tmp / "renders"), str(tmp / "drafts"), str(tmp / "out"), *profile,
                          "--pages", "1-2", "--backend", "http", "--endpoint", "http://127.0.0.1:9/v1/chat/completions",
                          "--report", str(tmp / "report.json"), "--single-engine", *old_defaults])
        report = json.loads((tmp / "report.json").read_text())
        assert status == 0 and report["counts"]["skippedCurrent"] == 2, report["counts"]
        assert grid_of((tmp / "out" / "page-0001.md").read_text()) == [HEADER] + ROWS
        assert report["tablesStitched"][0]["pages"] == [1, 2], report["tablesStitched"]
        assert report["doubtPages"].get(_stitch.MERGED) == 2, report["doubtPages"]
        assert all(entry["doubts"] == [_stitch.MERGED] for entry in report["pages"]), report["pages"]
    print("main: the stitch runs when the run ends, on resumed pages too, and reports what it merged")


# --- the ruled grid ---------------------------------------------------------------

FIELDS = 6
BANDS = [(100, 300), (300, 500), (500, 700)]   # left to right; records read right to left
TOP, PITCH = 200, 80


def draw_page(path: Path, inked: list[list[int]], faint: tuple[int, int] | None = None,
              split: tuple[int, int] | None = None, tilt: int = 0) -> list[float]:
    """A ruled table page: three bands of FIELDS ruled rows, text (squares) in
    the listed fields of each band, right band first.  `faint` / `split` (band,
    rule) draw one rule too light to count or broken in the middle; `tilt`
    shifts the vertical rules by that many pixels from top to bottom (skew).
    Returns the block's box."""
    from PIL import Image, ImageDraw
    image = Image.new("L", (1000, 1400), 255)
    draw = ImageDraw.Draw(image)
    bottom = TOP + FIELDS * PITCH
    for x in (100, 300, 500, 700):
        draw.line([(x, TOP), (x + tilt, bottom)], fill=0, width=3)
    for k, (a, b) in enumerate(BANDS):
        for r in range(FIELDS + 1):
            y = TOP + r * PITCH
            if (k, r) == faint:
                draw.line([(a, y), (b, y)], fill=235, width=2)
            elif (k, r) == split:
                draw.line([(a, y), (a + 35, y)], fill=0, width=2)
                draw.line([(b - 35, y), (b, y)], fill=0, width=2)
            else:
                draw.line([(a, y), (b, y)], fill=0, width=2)
        record = len(BANDS) - 1 - k
        for f in inked[record]:
            y = TOP + f * PITCH + 30
            for g in range(5):
                x = a + 40 + g * 22
                draw.rectangle([x, y, x + 14, y + 16], fill=0)
    image.save(path)
    return [0.1, TOP / 1400, 0.6, FIELDS * PITCH / 1400]


def engine_html(rows: list[list[str]]) -> str:
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows) + "</table>"


def turned_markdown(engine_rows: list[list[str]]) -> str:
    """The engine grid as phase 3 seals it: turned counter-clockwise."""
    table = _tables.find_tables(engine_html(engine_rows))[0]
    return _tables.render_markdown(_tables.rotate_counterclockwise(table))


def geometry_pages(tmp: Path, cont_inked: list[list[int]], cont_engine: list[list[str]], **draw) -> list:
    """Head page: labels + two records over FIELDS rows.  Continuation: three
    records whose engine grid is `cont_engine` (engine rows x records, left to
    right)."""
    labels = ["年度", "甲捐", "乙捐", "丙捐", "丁捐", "合計"]
    head_records = [["元年度", "一〇", "", "三〇", "", "四〇"], ["二年度", "一一", "二二", "", "", "三三"]]
    head_engine = [[head_records[1][f], head_records[0][f], labels[f]] for f in range(FIELDS)]
    head_inked = [[f for f in range(FIELDS) if labels[f]]] + [[f for f in range(FIELDS) if r[f]] for r in head_records]
    box1 = draw_page(tmp / "page-0001.png", head_inked)
    box2 = draw_page(tmp / "page-0002.png", cont_inked, **draw)
    head_md = turned_markdown(head_engine)
    cont_md = turned_markdown(cont_engine)
    pages = []
    for number, text, engine, box in ((1, head_md, head_engine, box1), (2, cont_md, cont_engine, box2)):
        blocks = [{"label": "table", "text": engine_html(engine), "box": box}]
        pages.append(_stitch.PageInput(number, text, seal(text, [(0, text)]), blocks,
                                       render=tmp / f"page-{number:04d}.png",
                                       is_furniture=_stitch.furniture_test(blocks)))
    return pages


def check_realignment() -> None:
    # Three records over six printed rows; field 3 is blank in all three and
    # engine A dropped that row, so its grid has five rows.
    records = [["三年度", "五〇", "六〇", "", "七〇", "一八〇"],
               ["四年度", "", "六一", "", "七一", "一三二"],
               ["五年度", "五二", "", "", "", "五二"]]
    inked = [[f for f in range(FIELDS) if r[f]] for r in records]
    engine = [[records[2][f], records[1][f], records[0][f]] for f in (0, 1, 2, 4, 5)]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pages = geometry_pages(tmp, inked, engine)
        result = _stitch.plan(pages)
        merged = grid_of(result["pages"][1]["text"])
        assert merged[3:] == records, merged
        part = result["pages"][1]["stitch"]["head"]["parts"][0]
        assert part["fields"]["by"] == "ruled grid" and part["fields"]["inserted"] == [3], part["fields"]
        assert result["pages"][2]["text"] == ""
        # Without the geometry the pair is only undecided.
        assert _stitch.plan(pages, geometry=False)["decisions"][0]["verdict"] == "undecided"

        # A rule too faint to see and one broken in the middle: those bands
        # lose their own count; the head's rows still place them.
        pages = geometry_pages(tmp, inked, engine, faint=(0, 2), split=(1, 4))
        result = _stitch.plan(pages)
        assert grid_of(result["pages"][1]["text"])[3:] == records, result["decisions"]
        placed = result["pages"][1]["stitch"]["head"]["parts"][0]["fields"]["placed"]
        assert placed["五年度"] == "the head's rows" and placed["四年度"] == "the head's rows", placed

        # A skewed scan: the vertical rules drift across the page.
        pages = geometry_pages(tmp, inked, engine, tilt=9)
        assert grid_of(_stitch.plan(pages)["pages"][1]["text"])[3:] == records

        # The page says one thing, engine A's grid another: 四年度 is printed
        # in fields 0, 2, 3 and 4, which puts engine row 3 in field 3, where
        # 三年度's own count puts it in field 4.  Unaligned: nothing merged,
        # no row written shifted.
        conflict_inked = [inked[0], [0, 2, 3, 4], inked[2]]
        pages = geometry_pages(tmp, conflict_inked, engine)
        result = _stitch.plan(pages)
        verdict = result["decisions"][0]["verdict"]
        assert verdict == "unaligned" and result["chains"] == [], result["decisions"]
        assert result["pages"][2]["text"] == pages[1].text and grid_of(result["pages"][1]["text"])[1:] == \
            grid_of(pages[0].text)[1:]
        assert kinds(result["pages"][1]["doubts"]) == [_stitch.DOUBT_PREFIX + verdict]

        # Bands that cannot be paired with the records (a missing vertical
        # rule): the geometry says nothing, the pair is undecided.
        two = [[records[1][f], records[0][f]] for f in (0, 1, 2, 4, 5)]
        pages = geometry_pages(tmp, inked, two)
        result = _stitch.plan(pages)
        assert result["decisions"][0]["verdict"] == "undecided" and "cannot be paired" in \
            result["decisions"][0]["evidence"][-1], result["decisions"]

        # Two records each placed on its own count whose placements cross:
        # 四年度's ink puts engine row 3 in field 2, 三年度's puts engine row 2
        # in field 3 (a smear read by both measures alike).  The map is one
        # to one but not in order; 五年度, placed through it, would have its
        # two figures swapped.  Unaligned, nothing merged.
        crossing = [["三年度", "五〇", "六〇", "", "一八〇"], ["四年度", "", "", "六一", ""],
                    ["五年度", "", "五二", "七二", ""]]
        pages = geometry_pages(tmp, [[0, 1, 3, 5], [0, 2], [0, 1, 2, 3]],
                               [[crossing[2][j], crossing[1][j], crossing[0][j]] for j in range(5)])
        result = _stitch.plan(pages)
        assert result["decisions"][0]["verdict"] == "unaligned" and result["chains"] == [], result["decisions"]
        assert "cross" in result["decisions"][0]["evidence"][-1] and result["pages"][2]["text"] == pages[1].text

        # A leader printed for nil is ink in its ruled cell: the record is
        # placed with it, and the mark stays in its field.
        leader = [records[0], ["四年度", "……", "六一", "", "七一", "一三二"], records[2]]
        pages = geometry_pages(tmp, [[f for f in range(FIELDS) if r[f]] for r in leader],
                               [[leader[2][f], leader[1][f], leader[0][f]] for f in (0, 1, 2, 4, 5)])
        result = _stitch.plan(pages)
        assert grid_of(result["pages"][1]["text"])[3:] == leader, result["decisions"]
        placed = result["pages"][1]["stitch"]["head"]["parts"][0]["fields"]["placed"]
        assert placed["四年度"] == "its ruled rows and the head's rows", placed

        # A mark in an engine row no other record fills (engine A added a row
        # for it): placed on its own count, it takes a field another record's
        # figure holds.  Unaligned, and the mark is still on its page - it is
        # never written as a blank cell.
        a = ["三年度", "五〇", "六〇", "", "", "七〇", "一八〇"]
        b = ["四年度", "", "六一", "", "七一", "", "一三二"]
        c = ["五年度", "五二", "", "—", "", "", "五二"]
        pages = geometry_pages(tmp, [[0, 1, 2, 4, 5], [0, 2, 3, 5], [0, 1, 3, 5]],
                               [[c[j], b[j], a[j]] for j in range(7)])
        result = _stitch.plan(pages)
        assert result["decisions"][0]["verdict"] == "unaligned" and result["chains"] == [], result["decisions"]
        assert "—" in result["pages"][2]["text"] and result["pages"][2]["text"] == pages[1].text
    print("ruled grid: a dropped blank row put back, faint and broken rules and skew survived, "
          "a contradiction or crossed placements left unaligned, unpaired bands undecided, a mark kept in its "
          "field or left unaligned")


# --- a cell the page break cut ------------------------------------------------------

BOOK_HEADER = ["書名", "册數", "要言", "備考"]
CUT = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"      # a notes cell: two full lines of ten
ENDED = "青赤黃白黑東南西北中"


def witness_lines(*cells: str, line: int = 10) -> str:
    """Engine B's reading of a page: each cell's text in printed lines of `line`."""
    return "\n".join(text[i:i + line] for text in cells for i in range(0, len(text), line))


def cell_pages(part_rows: list[list[str]] | None, witness1: str, text2: str | None = None,
               blocks2: list | None = None, cut: str = CUT) -> list:
    head = markdown([BOOK_HEADER, ["某甲課本", "二", ENDED, "已頒"], ["某乙課本", "一", cut, ""]])
    text1 = head
    page1 = _stitch.PageInput(1, text1, seal(text1, [(0, head)], ["block 0: kept — RECORD: row"]),
                              [], witness=witness1)
    if part_rows is not None:
        text2 = markdown(part_rows)
        record2 = seal(text2, [(0, text2)], ["block 0: kept — RECORD: row"])
    else:
        record2 = seal(text2)
    page2 = _stitch.PageInput(2, text2, record2, blocks2 or [], is_furniture=_stitch.furniture_test(blocks2 or []))
    return [page1, page2]


def check_cell_joins() -> None:
    open_witness = witness_lines(ENDED, CUT)          # the cut cell ends in a full line
    # A tail record: no key field filled, text only where the head's last
    # record has text - joined to that cell, then the records follow.
    tail = ["", "", "戌亥", ""]
    more = ["某丙課本", "三", "日月", ""]
    result = _stitch.plan(cell_pages([tail, more], open_witness))
    merged = grid_of(result["pages"][1]["text"])
    assert merged[-2][2] == CUT + "戌亥" and merged[-1] == more and len(merged) == 4, merged
    joins = result["pages"][1]["stitch"]["head"]["parts"][0]["cellJoins"]
    assert joins[0]["join"] == "…辰巳午未申酉 | 戌亥…" and not joins[0]["repeated"], joins
    assert _stitch.CELL_JOINED in kinds(result["pages"][1]["doubts"]) and \
        _stitch.CELL_JOINED in kinds(result["pages"][2]["doubts"])
    assert cjk_count([result["pages"][1]["text"], result["pages"][2]["text"]]) == \
        cjk_count([cell_pages([tail, more], open_witness)[0].text, markdown([tail, more])])

    # The same record where engine B shows the cell ending on its page (its
    # last line shorter than its full lines): not joined, not appended.
    ended_witness = witness_lines(ENDED, CUT[:14])
    pages = cell_pages([tail, more], ended_witness, cut=CUT[:14])
    result = _stitch.plan(pages)
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided", result["decisions"]
    assert "ends on its page" in result["decisions"][0]["evidence"][-1]
    # No engine B reading: nothing shows the cell cut.
    result = _stitch.plan(cell_pages([tail, more], ""))
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided"

    # A tail whose field is blank in the head's last record is no tail: the
    # record is appended as a record, nothing joined.
    stray = ["", "", "", "未頒"]
    result = _stitch.plan(cell_pages([stray, more], open_witness))
    merged = grid_of(result["pages"][1]["text"])
    assert merged[-2] == stray and merged[-3][2] == CUT, merged
    assert "cellJoins" not in result["pages"][1]["stitch"]["head"]["parts"][0]

    # Marks are printed text.  A mark beside the tail, in a field the head's
    # last record leaves blank, makes the record no tail: it is appended
    # whole (a join would have dropped the mark with the row).
    marked = ["", "", "戌亥", "……"]
    result = _stitch.plan(cell_pages([marked, more], open_witness))
    merged = grid_of(result["pages"][1]["text"])
    assert merged[-2] == marked and merged[-3][2] == CUT, merged
    # A new record named by a ditto mark: the mark fills the name field, and
    # engine B does not show the name cell above it cut - undecided, nothing
    # joined, the mark still on its page.
    ditto = ["〃", "", "戌亥", ""]
    result = _stitch.plan(cell_pages([ditto, more], open_witness))
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided", result["decisions"]
    assert result["pages"][2]["text"] == markdown([ditto, more])

    # A repeated character at the join is kept, and said.
    result = _stitch.plan(cell_pages([["", "", "酉戌", ""], more], open_witness))
    joins = result["pages"][1]["stitch"]["head"]["parts"][0]["cellJoins"]
    assert grid_of(result["pages"][1]["text"])[-2][2] == CUT + "酉戌" and joins[0]["repeated"]
    assert "kept twice" in " ".join(d["detail"] for d in result["pages"][1]["doubts"])

    # A text block at the top of the next page, printed in the cut cell's
    # field: it starts well below where the page's text starts and its first
    # line is as long as the cell's full lines.
    tail_text = "天地玄黃宇宙洪荒日月盈昃辰宿"
    prose = "右表所列各書均係本年頒行之本其未頒者另行補列"
    mid = [{"label": "vertical_text", "text": "天地玄黃宇宙洪荒日月\n盈昃辰宿", "box": [0.80, 0.50, 0.05, 0.40]},
           {"label": "vertical_text", "text": prose, "box": [0.20, 0.20, 0.50, 0.70]}]
    text2 = f"{tail_text}\n\n{prose}"
    result = _stitch.plan(cell_pages(None, open_witness, text2, mid))
    merged = grid_of(result["pages"][1]["text"])
    assert merged[-1][2] == CUT + tail_text and result["pages"][2]["text"] == prose, result["decisions"]
    part = result["pages"][1]["stitch"]["head"]["parts"][0]
    assert part["kind"] == "cell" and part["cellJoins"][0]["join"] == "…辰巳午未申酉 | 天地玄黃宇宙…"
    assert result["pages"][2]["stitch"]["continuation"]["moved"] == tail_text
    # The same block at the top of the frame is a paragraph: nothing decided.
    top = [dict(mid[0], box=[0.80, 0.20, 0.05, 0.40]), mid[1]]
    result = _stitch.plan(cell_pages(None, open_witness, text2, top))
    assert result["chains"] == [] and result["decisions"] == [] and result["pages"][2]["text"] == text2
    # A first line of another length (another field): nothing decided.
    other = [dict(mid[0], text="天地玄黃宇宙洪\n荒日月盈昃辰宿"), mid[1]]
    result = _stitch.plan(cell_pages(None, open_witness, text2, other))
    assert result["chains"] == [] and result["decisions"] == []
    # The cell ended on its page: undecided, the text stays.
    result = _stitch.plan(cell_pages(None, witness_lines(ENDED, CUT + "戌"), text2, mid, cut=CUT + "戌"))
    assert result["chains"] == [] and result["decisions"][0]["verdict"] == "undecided"
    assert result["pages"][2]["text"] == text2
    print("cell joins: a tail record and a tail text block joined to a cell the page cut; not where the "
          "cell ends on its page, the field is blank, the block starts at the frame top or has another length; "
          "a record holding a mark kept whole")


if __name__ == "__main__":
    check_cell_joins()
    check_realignment()
    check_continuation_without_header()
    check_caption_kept()
    check_repeated_header()
    check_not_merged()
    check_continuation_and_new_table()
    check_orientation_from_head()
    check_line_of_labels()
    check_furniture_and_blocked()
    check_files()
    check_pages_sealed_while_planning()
    check_main_runs_the_stitch()
    print("all stitch checks passed")
