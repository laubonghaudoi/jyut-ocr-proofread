#!/usr/bin/env python3
"""Offline checks of table recovery on one page: orientation, figure grouping,
the formatter's handling of recovered tables, label-cell direction, and tables
rebuilt from the image.

No model, no GPU, no corpus: synthetic grids and a fake client only.  Each case
is a shape measured on real pages (see the docstrings the checks name); the
text here is made up.

Usage:
    python3 tests/check_tables.py
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tables  # noqa: E402
import proofread_pages as pp  # noqa: E402


class FakeClient:
    """Answers each call with the next scripted answer for its kind; counts calls."""

    model, effort = "fake", None

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = {kind: list(texts) for kind, texts in (answers or {}).items()}
        self.calls: list[tuple[str, str]] = []

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        self.calls.append((kind, user_text))
        queue = self.answers.get(kind) or [""]
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return text, {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}


def html(rows: list[list[str]]) -> str:
    return "<table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows) + "</table>"


LABELS = ["年度", "費事甲", "費事乙", "費事丙", "費事丁", "計合"]


def check_single_line_orientation() -> None:
    column = _tables.render_markdown(_tables.Table("markdown", len(LABELS), 1, [
        _tables.Cell(r, 0, 1, 1, text) for r, text in enumerate(LABELS)]))
    client = FakeClient({"orient": ["RECORD: column\n照年份"]})
    text, how = pp.orient_table(column, b"png", client, 100)
    assert client.calls == [], "a single line of labels is not asked about"
    assert text == column and how.startswith(pp.SINGLE_LINE_KEPT), how
    row = _tables.render_markdown(_tables.Table("markdown", 1, 3, [
        _tables.Cell(0, c, 1, 1, text) for c, text in enumerate(["年度", "費事甲", "費事乙"])]))
    assert pp.orient_table(row, b"png", client, 100)[1].startswith(pp.SINGLE_LINE_KEPT)
    assert client.calls == []

    # Two axes and nothing else to go by (no print direction, no reading
    # order): still the page's question.  The single skill's answer contract
    # (a RECORD line with an EVIDENCE line, then the first record's side) is
    # the kept skill's (decision 1).
    grid = "| 物名 | 件數 |\n| --- | --- |\n| 黑板 | 二 |\n| 粉筆 | 十 |"
    client = FakeClient({"orient": ["RECORD: column\nEVIDENCE: 圖上每直列係一筆紀錄"],
                         "record-order": ["FIRST: right\nEVIDENCE: 圖上最右直列先讀"]})
    text, how = pp.orient_table(grid, b"png", client, 100)
    assert [kind for kind, _ in client.calls] == ["orient", "record-order"] and how.startswith("rotated"), how

    # Arithmetic still decides first on a vertical page (decision 8: the
    # print direction is what turns a table; a 合計 only shows which axis of
    # engine A's grid holds the printed columns): a single column of figures
    # that sums.
    figures = ["一、〇〇〇", "二、〇〇〇", "三、〇〇〇", "六、〇〇〇"]
    sums = _tables.render_markdown(_tables.Table("markdown", 4, 1, [
        _tables.Cell(r, 0, 1, 1, text) for r, text in enumerate(figures)]))
    client = FakeClient()
    assert pp.orient_table(sums, b"png", client, 100, print_direction="vertical")[1].startswith("rotated — 合計")
    assert client.calls == []

    # recover_tables seals the kept line as undecided, for a pass that sees
    # the table it labels.
    blocks = [{"text": html([[label] for label in LABELS]), "label": "table", "box": [0, 0, 1, 1]}]
    client = FakeClient({"orient": ["RECORD: column"]})
    tables, rejected, orientation, sources, sealed = pp.recover_tables(
        blocks, "".join(LABELS), b"png", client, 100, witness="".join(LABELS))
    assert len(tables) == 1 and not rejected, rejected
    assert "orient" not in [kind for kind, _ in client.calls], client.calls
    assert [d["kind"] for d in sealed["doubts"]][0] == "table-orientation-undecided", sealed
    assert "block 0" in sealed["doubts"][0]["detail"] and orientation[0].startswith("block 0: kept")
    print("orientation: a single line of cells is kept without a question and sealed undecided")


def cells_of(table_text: str) -> list[str]:
    return [cell.text for cell in pp.first_table(table_text).cells]


def check_figure_grouping() -> None:
    # Engine A's grid: a label column and one column of figures.  Two figures
    # both engines group alike show the page groups in threes.
    a_cells = ["年度", "一、〇〇〇、〇〇〇", "二、三四五、六七八", "三、〇二、〇、〇〇", "四、一、四七、二三八"]
    table = "| " + a_cells[0] + " |\n| --- |\n" + "".join(f"| {c} |\n" for c in a_cells[1:])
    b_text = "年度 一、〇〇〇、〇〇〇 二、三四五、六七八 三、〇二〇、〇〇〇 四、一四七、二三八"
    merged = "年度一〇〇〇〇〇〇二三四五六七八三〇二〇〇〇〇四一四七二三八"  # the merge added a 〇
    notes: list[str] = []
    out = cells_of(pp.restate_table(table, merged, b_text, notes))
    assert out == ["年度", "一、〇〇〇、〇〇〇", "二、三四五、六七八", "三、〇二〇、〇〇〇", "四、一四七、二三八"], out
    assert len(notes) == 2 and "四、一四七、二三八" in notes[1], notes
    # Characters: exactly the merged ones.
    assert "".join(pp.CJK(c) for c in out) == merged

    # Without engine B: as before - A's marks where the count held, bare digits
    # where the merge changed it.
    out = cells_of(pp.restate_table(table, merged))
    assert out[4] == "四、一、四七、二三八" and out[3] == "三〇二〇〇〇〇", out

    # The merge changed the digits and B read other digits: bare, nothing made up.
    other_b = b_text.replace("三、〇二〇、〇〇〇", "三、〇二一、〇〇〇")
    assert cells_of(pp.restate_table(table, merged, other_b))[3] == "三〇二〇〇〇〇"

    # B reads the figure two ways: no single reading to take.
    twice = b_text + " 四一、四七、二三八"
    assert cells_of(pp.restate_table(table, merged, twice))[4] == "四、一、四七、二三八"

    # A grouping that fits the page stays, whatever B wrote.
    fits = b_text.replace("二、三四五、六七八", "二三、四五、六七八")
    out = cells_of(pp.restate_table(table, merged, fits))
    assert out[2] == "二、三四五、六七八", out

    # No figure grouped alike by both engines: the page shows no grouping, so
    # A's marks are not judged wrong where the digits held.
    lone = "| 年度 |\n| --- |\n| 四、一、四七、二三八 |\n"
    assert cells_of(pp.restate_table(lone, "年度四一四七二三八", "年度 四、一四七、二三八"))[1] == \
        "四、一、四七、二三八"

    # The page's figures show two group sizes (threes and fours): not judged.
    mixed = ("| 年度 |\n| --- |\n| 一、〇〇〇、〇〇〇 |\n| 一二、三四五六 |\n| 四、一、四七、二三八 |\n")
    mixed_b = "一、〇〇〇、〇〇〇 一二、三四五六 四、一四七、二三八"
    assert cells_of(pp.restate_table(mixed, "年度一〇〇〇〇〇〇一二三四五六四一四七二三八", mixed_b))[3] == \
        "四、一、四七、二三八"

    # The notes reach the seal through recover_tables.
    blocks = [{"text": html([[c] for c in a_cells]), "label": "table", "box": [0, 0, 1, 1]}]
    # The single skill orients every recovered table and checks its label
    # cells with or without a reviewer (decision 1): this one-column grid is
    # a single line, kept and sealed undecided as the old skill sealed it
    # (orient_table's guard), and with no image its label cells are sealed
    # undecided too; those two doubts are left out here.
    def own(doubts: list[dict]) -> list[dict]:
        return [d for d in doubts if d["kind"] not in ("table-orientation-undecided", "cell-direction-undecided")]

    _, rejected, _, _, sealed = pp.recover_tables(blocks, merged, witness=b_text)
    assert not rejected and [n.split(":")[0] for n in sealed["figureGrouping"]] == ["block 0", "block 0"], sealed
    assert not own(sealed["doubts"]), sealed["doubts"]
    # A note marker in a cell is sealed with a doubt: nothing checks it.
    noted = [c + (" $ ^{{①⑨}} $" if c == a_cells[1] else "") for c in a_cells]
    blocks = [{"text": html([[c] for c in noted]), "label": "table", "box": [0, 0, 1, 1]}]
    _, rejected, _, _, sealed = pp.recover_tables(blocks, merged, witness=b_text + " ⑲")
    assert not rejected and [d["kind"] for d in own(sealed["doubts"])] == ["table-note-marker-unverified"] \
        and "①⑨" in own(sealed["doubts"])[0]["detail"], (rejected, sealed["doubts"])
    print("figure grouping: engine B's marks where A's do not fit the page or the merge changed the digits; "
          "a note marker in a cell is a doubt")


def check_vouched_grid() -> None:
    """A grid refused on engine A's word alone - a cell wrapped onto two lines,
    a merged cell - is accepted when engine B's own <table> agrees with it
    cell for cell (vouched_grid); the shapes are the modern book's (p.316,
    p.281, p.270, p.382, p.42), the text made up."""
    rows = [["1514", "正德九年", "戶部禁止行廣鹽。", "—", "《武宗實錄》116/1b"],
            ["1517", "正德十二年", "戶部稱廣鹽暫發賣。", "6395.65兩", "《王陽明全集》11/386"],
            ["1518", "正德十三年", "戶部題廣鹽止行南贛。", "—", "《世宗實錄》62/1b；\\n《王陽明全集》11/384"],
            ["1519", "正德十四年", "戶部允行廣鹽。", "—", "《武宗實錄》177/4b"]]
    a_table = html(rows)
    flat = [[c.replace("\\n", "") for c in row] for row in rows]
    b_text = "第四章\n" + html(flat) + "\n⑲ 註文。"
    # Refused on engine A's word alone, as before.
    table, why = pp.engine_table(a_table, witness="第四章 戶部禁止行廣鹽 註文")
    assert table is None and "hold line breaks" in why and "engine B wrote no grid" in why, why
    # Engine B's grid agrees: accepted, the wrapped cell written on one line with <br>.
    table, why = pp.engine_table(a_table, witness=b_text)
    assert table and "vouched by engine B's grid (17 cells, 0" in why, why
    assert "| 1514 | 正德九年 |" in table and "《世宗實錄》62/1b；<br>《王陽明全集》11/384" in table, table
    # A character or two apart in a cell or two (a variant sort, a dropped
    # character) is the same cell read apart: still vouched.
    near = [row[:] for row in flat]
    near[1][2] = "戶部称廣鹽暫發賣。"
    table, why = pp.engine_table(a_table, witness=html(near))
    assert table and "1 read a character or two apart" in why, why
    # Engine B wraps the same cell: the two may both have collapsed columns there; refused.
    same_break = [row[:] for row in flat]
    same_break[2][4] = "《世宗實錄》62/1b；<br>《王陽明全集》11/384"
    table, why = pp.engine_table(a_table, witness=html(same_break))
    assert table is None and "engine B's grid wraps the same cell" in why, why
    # Engine B's grid of another shape, or with cells shifted into their
    # neighbours (p.270: 明殷從儉 / 明), or more cells apart than a tenth: refused.
    table, why = pp.engine_table(a_table, witness=html([row + [""] for row in flat]))
    assert table is None and "engine B's grid is 4 rows x 6 columns" in why, why
    shifted = [[r[0] + r[1], r[2], r[3], r[4], ""] for r in flat]
    table, why = pp.engine_table(a_table, witness=html(shifted))
    assert table is None and "reads" in why and "cells otherwise, e.g. row 1 column 1" in why, why
    apart = [row[:] for row in flat]
    apart[0][2], apart[1][2], apart[2][2] = "戶部禁行廣鹽。", "戶部稱廣鹽發賣。", "戶部題廣鹽止南贛。"
    table, why = pp.engine_table(a_table, witness=html(apart))
    assert table is None and "reads 3 of 17 cells otherwise" in why, why
    # A figure in another slot is a difference too (the figures' column shifted a row).
    moved = [row[:] for row in flat]
    moved[0][0], moved[1][0] = moved[1][0], moved[0][0]
    table, why = pp.engine_table(a_table, witness=html(moved))
    assert table is None and "reads 2 of 17 cells otherwise" in why, why

    # A merged cell: refused alone, HTML with the rowspan when engine B lays it the same way.
    spanned = ("<table><tr><td>設置時間</td><td>內容</td><td>出處</td></tr>"
               "<tr><td rowspan=\"2\">弘治七年說</td><td>設巡撫都察院於贛。</td><td>《贛州府志》2/12a</td></tr>"
               "<tr><td>始添巡臣於贛。</td><td>《虔臺志》1/3b</td></tr>"
               "<tr><td>弘治八年說</td><td>以金澤巡撫南贛。</td><td>《明史》</td></tr></table>")
    table, why = pp.engine_table(spanned, witness="設置時間 內容 出處 弘治七年說")
    assert table is None and "merged layout unverified" in why and "engine B wrote no grid" in why, why
    table, why = pp.engine_table(spanned, witness="<table><caption></caption>" + spanned[7:])
    assert table and table.startswith("<table>") and 'rowspan="2">弘治七年說' in table and "1 merged cell(s)" in why, why
    # Engine B writing the merged cell's text in both rows agrees with the
    # merge; an empty slot where engine A spans does not, however few.
    repeated = spanned.replace(' rowspan="2"', "").replace("<tr><td>始添", "<tr><td>弘治七年說</td><td>始添")
    table, why = pp.engine_table(spanned, witness=repeated)
    assert table and 'rowspan="2">弘治七年說' in table, why
    blank = spanned.replace(' rowspan="2"', "").replace("<tr><td>始添", "<tr><td></td><td>始添")
    table, why = pp.engine_table(spanned, witness=blank)
    assert table is None and "merged layout unverified" in why and "row 3 column 1 (弘治七年說 / empty)" in why, why
    # The vouched grid still passes the other evidence: a cell of digits engine B's text lacks.
    stranger = [row[:] for row in flat]
    stranger[0][0] = "1541"
    table, why = pp.engine_table(a_table, witness=html(stranger))
    assert table is None and "letters or digits that neither" in why, why
    print("vouched grid: a wrapped or merged cell accepted on both engines' grids, refused on one")


def between(text: str) -> str:
    """The text between the first pair of --- lines of a prompt: what it hands over."""
    return text.split("---\n", 1)[1].split("\n---", 1)[0]


class PageClient(FakeClient):
    """FakeClient whose answers may be functions of the prompt; census counts 0."""

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        self.calls.append((kind, user_text))
        if kind == "census":
            answer = "符號：0\n形狀：無"
        else:
            queue = self.answers.get(kind) or [""]
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            if callable(answer):
                answer = answer(user_text)
        return answer, {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}


def page_client(**answers) -> PageClient:
    """A PageClient that keeps every table as the engine oriented it and
    judges label cells by WORDS; `answers` by kind, e.g. format=[...]."""
    return PageClient({"orient": ["RECORD: row"], "cell-direction": [lambda u: word_judge(u)],
                       **answers})


def run_page(blocks: list[tuple[str, str, list[float]]], b_text: str, client: FakeClient,
             unplaced: str = "", furniture: frozenset = frozenset(), prefer: str = "A",
             switches: tuple[str, ...] = ()) -> tuple[str, dict]:
    """proofread_page on a made-up page: engine A's blocks (label, text, box),
    engine B's text, a blank render; returns the page text and its seal.
    `unplaced` is engine A text in no block, which sends
    the page down the whole-page path (block_spans cannot split it);
    `switches` are proofread_pages.py's command-line switches (e.g.
    --no-unread-table-characters)."""
    import json
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        Image.new("L", (1000, 1400), 255).save(tmp / "page-0001.png")
        (tmp / "a.txt").write_text("\n".join([text for _, text, _ in blocks] + [unplaced]),
                                   encoding="utf-8")
        (tmp / "a.json").write_text(json.dumps({"lines": [
            {"text": text, "boundingBox": box, "blockLabel": label, "blockOrder": n}
            for n, (label, text, box) in enumerate(blocks)]}, ensure_ascii=False), encoding="utf-8")
        (tmp / "b.txt").write_text(b_text, encoding="utf-8")
        job = pp.PageJob(1, tmp / "page-0001.png", tmp / "a.txt", tmp / "a.json", tmp / "b.txt",
                         tmp / "page-0001.json", tmp / "page-0001.md")
        arguments = pp.build_parser().parse_args([str(tmp), str(tmp), str(tmp), *switches])
        arguments.prefer_engine, arguments.fallback_engine = prefer, "A"
        arguments.profile_data, arguments.profile_sha = None, ""
        arguments.variant_rulings, arguments.variant_rulings_sha = {}, ""
        arguments.variant_engine, arguments.stale_pair_classes = None, []
        arguments.furniture = furniture
        record = pp.proofread_page(job, client, arguments)
        return job.out_md.read_text(encoding="utf-8"), record


TITLE = "某處學堂器具表"
GRID = [["物名", "件數"], ["黑板", "二"], ["粉筆", "十"]]
PROSE = "右表所列各件均係本年添置之物其價值另表詳之"
BOX = {"title": [0.8, 0.1, 0.05, 0.5], "table": [0.3, 0.1, 0.4, 0.7], "prose": [0.1, 0.1, 0.1, 0.8]}


def check_tables_bypass_the_formatter() -> None:
    blocks = [("doc_title", TITLE, BOX["title"]), ("table", html(GRID), BOX["table"]),
              ("text", PROSE, BOX["prose"])]
    b_text = f"{TITLE}\n物名件數黑板二粉筆十\n{PROSE}"
    table = "| 物名 | 件數 |\n| --- | --- |\n| 黑板 | 二 |\n| 粉筆 | 十 |"

    # A formatter that keeps the token line: the recovered table stands where
    # its block is, between the title and the prose, never written by the model.
    echo = page_client(format=[lambda u: "## " + between(u)])
    text, record = run_page(blocks, b_text, echo)
    format_prompt = next(u for kind, u in echo.calls if kind == "format")
    assert pp.TABLE_TOKEN.format(1) in format_prompt and "黑板" not in between(format_prompt), format_prompt
    assert text.index(TITLE) < text.index(table) < text.index(PROSE), text
    assert record["tablesMissing"] == [] and record["auditorClean"], record["auditorReport"]
    assert [kind for kind, _ in echo.calls].count("format-retry") == 0

    # The token lost twice: the per-block assembly, tables in place.
    lost = page_client(format=[lambda u: between(u).replace(pp.TABLE_TOKEN.format(1), "")])
    lost.answers["format-retry"] = lost.answers["format"]
    text, record = run_page(blocks, b_text, lost)
    assert [kind for kind, _ in lost.calls].count("format-retry") == 1
    assert "structure-rejected" in [d["kind"] for d in record["doubts"]], record["doubts"]
    assert "placeholder" in record["auditorReport"] and table in text, (record["auditorReport"], text)

    # The token kept and the table written again (turned into layout order):
    # the formatter's copy is dropped, the recovered table stands once.
    turned = "| 件數 | 物名 |\n| --- | --- |\n| 二 | 黑板 |\n| 十 | 粉筆 |"
    copy = page_client(format=[lambda u: between(u) + "\n\n" + turned])
    text, record = run_page(blocks, b_text, copy)
    assert text.count("黑板") == 1 and table in text and turned not in text, text
    assert any("own copy" in note for note in record["tablesEnforced"]), record["tablesEnforced"]
    assert record["auditorClean"], record["auditorReport"]

    # A token written twice is refused like any other change; the retry holds.
    twice = page_client(format=[lambda u: between(u) + "\n" + pp.TABLE_TOKEN.format(1)])
    twice.answers["format-retry"] = [between]
    text, record = run_page(blocks, b_text, twice)
    assert text.count("黑板") == 1 and table in text, text
    assert [kind for kind, _ in twice.calls].count("format-retry") == 1 and record["auditorClean"]

    # Two tables with a heading printed between them keep that order: the
    # heading is text in the formatter's hands, the tables are tokens.
    second = [["項目", "數目"], ["房租", "五"], ["薪水", "八"]]
    heading = "乙表支出細數"
    blocks2 = [("table", html(GRID), BOX["table"]), ("paragraph_title", heading, BOX["title"]),
               ("table", html(second), BOX["prose"])]
    b2 = f"物名件數黑板二粉筆十\n{heading}\n項目數目房租五薪水八"
    text, record = run_page(blocks2, b2, page_client(format=[between]))
    assert text.index("黑板") < text.index(heading) < text.index("房租"), text
    assert record["tablesMissing"] == [] and len(pp._tables.find_tables(text)) == 2, text
    print("formatter: recovered tables stand in as token lines; lost tokens fall back per block; "
          "copies dropped")


def check_joined_tables_split() -> None:
    first = "| 年度 | 甲費 | 乙費 |\n| --- | --- | --- |\n| 元年度 | 一、〇〇〇 | 二、〇〇〇 |"
    second = "| 丙費 |\n| --- |\n| 丁費 |\n| 合計 |"
    joined = ("## 乙表\n\n| 年度 | 甲費 | 乙費 |\n| --- | --- | --- |\n| 元年度 | 一、〇〇〇 | 二、〇〇〇 |\n"
              "| 丙費 | 丁費 | 合計 |\n")
    out, notes, missing, disputed = pp.enforce_recovered_tables(joined, [first, second])
    assert first in out and second in out and not missing and not disputed, (out, notes, missing)
    assert "put back" in notes[0] and "## 乙表" in out, notes
    assert sorted(pp.CJK(out)) == sorted(pp.CJK(joined))
    # A table that is one recovered table is left to the usual enforcement.
    out, notes, missing, _ = pp.enforce_recovered_tables(f"{first}\n", [first, second])
    assert missing == [1] and not any("put back" in n for n in notes), notes
    print("joined tables: a written table made of several recovered tables is split back")


WORDS = {"年度", "儀器費", "書籍費", "合計", "元年度", "二年度", "揭示牌",
         "物名", "件數", "黑板", "粉筆", "項目", "數目", "房租", "薪水"}


def visual_answers(user_text: str, choose) -> str:
    """The label-cell answer in the single skill's contract (decision 1: the
    kept skill's located, evidence-bearing reply): one object per listed cell,
    its row and col as listed, CHOOSE(x, y)'s choice for its 甲「x」 乙「y」."""
    import json
    import re
    answers = []
    for row, col, x, y in re.findall(r"row=(\d+), col=(\d+)[^:\n]*: 甲「([^」]*)」\s*乙「([^」]*)」", user_text):
        answers.append({"row": int(row), "col": int(col), "choice": choose(x, y),
                        "evidence": "圖上該格字序同局部排字方向清楚"})
    return json.dumps(answers, ensure_ascii=False)


def word_judge(user_text: str, bias: str | None = None, both: str = "") -> str:
    """Answers each 甲「x」 乙「y」 line: the reading in WORDS; `bias` answers
    that side always (a judge leaning to one position); `both` is a reading
    it calls fine either way."""
    def choose(x: str, y: str) -> str:
        if bias:
            return bias
        if both and both in (pp.CJK(x), pp.CJK(y)):
            return "兩個都得"
        a, b = pp.CJK(x) in WORDS, pp.CJK(y) in WORDS
        return "兩個都得" if a and b else "甲" if a else "乙" if b else "兩個都唔係"
    return visual_answers(user_text, choose)


LEDGER = [["年度", "費器儀", "費籍書", "計合"],
          ["元年度", "一、〇〇〇", "二〇〇", "一、二〇〇"],
          ["二年度", "九〇〇", "器儀", "九〇〇"]]


def ledger_table() -> str:
    return _tables.render_markdown(_tables.Table("markdown", 3, 4, [
        _tables.Cell(r, c, 1, 1, text) for r, row in enumerate(LEDGER) for c, text in enumerate(row)]))


def check_cell_direction() -> None:
    assert pp.turned_round("（名牌　揭示牌）") == "（牌示揭　牌名）"
    assert pp.turned_round("「計合」") == "「合計」"

    # Label axes only (row 0 and column 0), two or more characters, not a
    # figure, not a palindrome: the off-axis 器儀 is never asked.
    asked = [cell.text for cell in pp.direction_candidates(pp.first_table(ledger_table()))]
    assert asked == ["年度", "費器儀", "費籍書", "計合", "元年度", "二年度"], asked

    # The single skill asks only with the page image (decision 1).
    client = FakeClient({"cell-direction": [lambda u: word_judge(u)]})
    text, records = pp.fix_cell_direction(ledger_table(), PageClientAdapter(client), 100, block=3,
                                          render_bytes=b"png")
    cells = cells_of(text)
    assert cells[:4] == ["年度", "儀器費", "書籍費", "合計"] and cells[10] == "器儀", cells
    assert cells[4] == "元年度" and cells[8] == "二年度", cells
    assert sorted(pp.CJK(text)) == sorted(pp.CJK(ledger_table())), "a turn changes no character"
    prompts = [u for kind, u in client.calls]
    assert len(prompts) == 2, "one question per order, the whole table at once"
    assert "甲「費器儀」 乙「儀器費」" in prompts[0] and "甲「儀器費」 乙「費器儀」" in prompts[1], prompts
    outcome = {r["read"]: (r["outcome"], r["verdicts"]) for r in records}
    assert outcome["費器儀"] == ("turned", ["turned", "turned"]) and outcome["年度"][0] == "kept", outcome
    assert all(r["block"] == 3 for r in records)

    # A judge that always takes the first option, answers "both", or answers
    # something unreadable: the cell stays as read, undecided.
    for judge in (lambda u: word_judge(u, bias="甲"), lambda u: word_judge(u, both="儀器費"),
                  lambda u: "唔知", lambda u: '["乙"]'):
        client = PageClientAdapter(FakeClient({"cell-direction": [judge]}))
        text, records = pp.fix_cell_direction(ledger_table(), client, 100, render_bytes=b"png")
        undecided = [r["read"] for r in records if r["outcome"] == "undecided"]
        assert "費器儀" in undecided and "儀器費" not in text, (text, records)

    # recover_tables asks after the orientation and seals the undecided cells.
    blocks = [{"text": html(LEDGER), "label": "table", "box": [0, 0, 1, 1]}]
    witness = "".join("".join(row) for row in LEDGER)
    client = PageClientAdapter(FakeClient({"orient": ["RECORD: row"],
                                           "cell-direction": [lambda u: word_judge(u, both="儀器費")]}))
    tables, _, _, _, sealed = pp.recover_tables(blocks, pp.CJK(witness), b"png", client, 100,
                                                witness=witness)
    assert "書籍費" in tables[0] and "費器儀" in tables[0], tables
    assert [d["kind"] for d in sealed["doubts"]] == ["cell-direction-undecided"], sealed["doubts"]
    assert len(sealed["cellDirection"]) == 6

    # The formatter no longer turns cells: one it turned anyway is put back.
    recovered = "| 年度 | 儀器費 |\n| --- | --- |\n| 元年度 | 一、〇〇〇 |"
    written = "| 年度 | 費器儀 |\n| --- | --- |\n| 元年度 | 一、〇〇〇 |"
    out, notes, missing, _ = pp.enforce_recovered_tables(written, [recovered])
    assert out.strip() == recovered and not missing, (out, notes)
    print("cell direction: label cells turned only when the turned reading wins in both orders")


def hint_tables(user_text: str) -> str:
    """The tables a whole-page format prompt hands over to copy: the text
    between the second pair of --- lines."""
    import re
    first = re.search(r"---\n(.*?)\n---", user_text, re.S)
    found = re.search(r"---\n(.*?)\n---", user_text[first.end():], re.S) if first else None
    return found.group(1) if found else ""


def check_whole_page_turned_labels() -> None:
    """A whole-page pass (no block spans) copies the recovered tables.  Its
    deletion guard compares runs of the merged text, table included, where the
    labels stand as the engines read them: a copy with turned labels was
    refused twice and the page lost its table (measured with a stand-in that
    copies as told).  The formatter now gets the cells as read, and the
    turned labels are written when the table is enforced."""
    labels = ["費器儀", "費籍書", "費具器", "費項雜", "費修營", "費務公", "費膳伙", "計合"]
    figures = {0: "三〇〇", 6: "五〇〇", 7: "八〇〇"}
    rows = [["二年度", "元年度", "年度"]] + [[figures.get(n, ""), "", label]
                                           for n, label in enumerate(labels)]
    title, prose = "某處學堂經費表", "右表所列各費均係本年支出之數其細數另表詳之"
    blocks = [("doc_title", title, BOX["title"]), ("table", html(rows), BOX["table"]),
              ("text", prose, BOX["prose"])]
    flat = "".join("".join(row) for row in rows)
    words = {"儀器費", "書籍費", "器具費", "雜項費", "營修費", "公務費", "伙膳費", "合計",
             "年度", "元年度", "二年度"}

    def judge(user_text: str) -> str:
        return visual_answers(user_text, lambda x, y: (
            "甲" if pp.CJK(x) in words else "乙" if pp.CJK(y) in words else "兩個都唔係"))

    def copy_tables(user_text: str) -> str:
        return between(user_text).replace(flat, "\n\n" + hint_tables(user_text) + "\n\n", 1)

    client = page_client(format=[copy_tables], **{"cell-direction": [judge]})
    text, record = run_page(blocks, f"{title}\n{flat}\n{prose}", client, unplaced="某")
    prompt = next(u for kind, u in client.calls if kind == "format")
    assert "| 年度 | 費器儀 |" in prompt and "儀器費" not in prompt, prompt
    assert "| 年度 | 儀器費 | 書籍費 |" in text and "費器儀" not in text, text
    assert [kind for kind, _ in client.calls].count("format-retry") == 0, record["auditorReport"]
    assert record["tablesMissing"] == [] and record["droppedRuns"] == [], record
    assert [d["kind"] for d in record["doubts"]] == ["punctuation-whole-page"], record["doubts"]
    assert sorted(r["outcome"] for r in record["cellDirection"]).count("turned") == 8

    # A table the formatter wrote with merged cells stands as written
    # (tablesDisputed), but a cell it wrote the other way round from the
    # recovered table's is turned to match; a label that reads either way
    # (年度 here, matched as written first) is not.
    recovered = "| 年度 | 儀器費 | 度年 |\n| --- | --- | --- |\n| 元年度 | 一〇 | 二〇 |"
    written = ('<table><tr><td colspan="2">年度</td><td>費器儀</td></tr>'
               "<tr><td>元年度</td><td>一〇</td><td>二〇</td></tr><tr><td>度年</td></tr></table>")
    out, notes, missing, disputed = pp.enforce_recovered_tables(written, [recovered])
    assert disputed == [0] and "儀器費" in out and "費器儀" not in out, (out, notes)
    assert "年度" in out and "度年" in out and 'colspan="2"' in out, out
    assert sorted(pp.CJK(out)) == sorted(pp.CJK(written)) and "turned" in notes[0], notes
    print("whole page: the formatter copies label cells as read; the turned ones are written after")


class PageClientAdapter:
    """A FakeClient whose scripted answers may be functions of the prompt."""

    model, effort = "fake", None

    def __init__(self, inner: FakeClient):
        self.inner = inner

    @property
    def calls(self):
        return self.inner.calls

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        answer, metrics = self.inner.ask_answering(system, user_text, image_bytes, max_tokens, kind)
        return (answer(user_text) if callable(answer) else answer), metrics


def check_no_corpus_strings_in_prompts() -> None:
    """The prompts the table steps send carry no label of the user's goldens,
    either way round: the skill is generic, and a prompt that names a test
    page's labels would decide that page's answer for the model."""
    golden = Path(__file__).resolve().parent / "golden"
    labels = set()
    for path in golden.rglob("*"):
        if path.suffix in (".md", ".html"):
            for table in _tables.find_tables(path.read_text(encoding="utf-8")):
                labels |= {pp.CJK(cell.text) for cell in table.cells
                           if (cell.row == 0 or cell.col == 0) and len(pp.CJK(cell.text)) >= 2
                           and pp.chinese_figure(cell.text) is None}
    assert len(labels) > 20, "the goldens' tables were not found"
    # The one word every prompt about totals must name.
    labels -= {"合計", "總計"}
    fixed = [pp.DIRECTION_SYSTEM, pp.ORIENT_SYSTEM, pp.FIRST_RECORD_SYSTEM, pp.STRUCTURE_SYSTEM, pp.FORMAT_SYSTEM]
    # The table hints, from a made-up page whose own words are no golden's:
    # the structure pass, and the whole-page pass where the tables are copied.
    grid = [["類別", "儀器費"], ["甲項", "一、〇〇〇"], ["乙項", "九〇〇"]]
    blocks = [("table", html(grid), BOX["table"]), ("text", "本表所列均係本年之數", BOX["prose"]),
              ("table", "<table><tr><td>甲</td><td>乙</td></tr></table>", BOX["title"])]
    for unplaced in ("", "某"):
        client = page_client(format=[between])
        run_page(blocks, "類別儀器費甲項一〇〇〇乙項九〇〇本表所列均係本年之數甲乙", client, unplaced)
        fixed += [u for kind, u in client.calls if kind == "format"]
    found = sorted({label for label in labels for text in fixed
                    if len(label) >= 2 and (label in text or label[::-1] in text)})
    assert not found, found
    print(f"prompts: none of the goldens' {len(labels)} label cells in the table prompts")


def split_table(user_text: str) -> str:
    """A stand-in rebuild: the locked characters as a two-row table, in order."""
    chars = [c for c in between(user_text) if pp.CJK(c)]
    half = len(chars) // 2
    return f"| {''.join(chars[:half])} |\n| --- |\n| {''.join(chars[half:])} |"


def check_rebuild_checks() -> None:
    allowed, required = pp.Counter("甲乙丙丁戊己庚辛"), pp.Counter("甲乙丙丁")
    good = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 戊己 | 庚 |"
    table, why = pp.check_rebuilt_table(good, allowed, required, "甲乙丙丁戊己庚辛")
    assert table is not None and "2 rows" in why, why
    assert pp.check_rebuilt_table("唔係表格", allowed, required, "")[1].startswith("not a table")
    assert pp.check_rebuilt_table("甲乙丙丁", allowed, required, "")[1] == "no table in the answer"
    invented = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 戊己 | 庚辛壬 |"
    assert "neither engine" in pp.check_rebuilt_table(invented, allowed, required, "甲乙丙丁戊己庚辛")[1]
    lost = "| 甲乙 | 丙 |\n| --- | --- |\n| 戊己 | 庚 |"
    assert "left out: 丁" in pp.check_rebuilt_table(lost, allowed, required, "甲乙丙丁戊己庚辛")[1]
    two = good + "\n\n" + good
    assert "2 tables" in pp.check_rebuilt_table(two, allowed, required, "")[1]
    one_row = "| 甲乙丙丁 |\n| --- |"
    assert "row(s)" in pp.check_rebuilt_table(one_row, allowed, required, "")[1]
    # A variant swap is repaired against the region's text, as for the formatter.
    swapped = "| 甲乙 | 丙丁 |\n| --- | --- |\n| 戊己 | 庚爲 |"
    table, why = pp.check_rebuilt_table(swapped, pp.Counter("甲乙丙丁戊己庚為"), required, "甲乙丙丁戊己庚為")
    assert table is not None and "為" in table, why
    # An acceptable variant of what an engine read passes even where the region's
    # text cannot repair it (cells in another order), and keeps the answer's form:
    # 算 against engine B's 𮅕 (the user's list), 冊 against 册 (OpenCC's).
    # 筍 is not a form of 算 and stays refused.
    lock = pp.Counter("普通珠𮅕課本珠𮅕問題一年三二册")
    folded = "| 普通珠算課本 | 一年 |\n| --- | --- |\n| 珠算問題 | 三二冊 |"
    table, why = pp.check_rebuilt_table(folded, lock, pp.Counter("普通珠課本"), "")
    assert table is not None and "珠算" in table and "variants folded" in why, why
    wrong = folded.replace("珠算問題", "珠筍問題")
    assert "neither engine read here: 筍" in pp.check_rebuilt_table(wrong, lock, pp.Counter(), "")[1]
    # Nor is a required character left out when the answer writes an acceptable form of it.
    table, why = pp.check_rebuilt_table(folded, lock, pp.Counter("𮅕册"), "")
    assert table is not None, why
    # A refusal names the characters as the answer wrote them (and, left out,
    # as the engines read them), never the variant class's representative:
    # measured, a retry told 数 毎 冝 for the 數 每 宜 it had copied into two
    # rows wrote 数 毎 冝 into its answer.
    note = "每課宜照書數次"
    copied = f"| 甲 | {note} |\n| --- | --- |\n| 乙 | {note} |"
    why = pp.check_rebuilt_table(copied, pp.Counter("甲乙" + note), pp.Counter(), "")[1]
    assert why == "characters neither engine read here: " + "".join(sorted(note)), why
    assert not set(why) & set("毎冝数"), why
    short = "| 甲 | 課照書次 |\n| --- | --- |\n| 乙 | |"
    why = pp.check_rebuilt_table(short, pp.Counter("甲乙" + note), pp.Counter("甲乙" + note), "")[1]
    assert why == "characters both engines read here left out: " + "".join(sorted("每宜數")), why
    # An answer that wrote another form of a class than the lock holds is named in its own form.
    why = pp.check_rebuilt_table(copied.replace("數", "数"), pp.Counter("甲乙" + note), pp.Counter(), "")[1]
    assert "数" in why and "數" not in why, why

    # One retry names what was wrong; "not a table" is taken as it stands.
    client = PageClientAdapter(FakeClient({"table-rebuild": [invented],
                                           "table-rebuild-retry": [good]}))
    table, record = pp.rebuild_table(client, b"png", "甲乙丙丁戊己庚辛", required, 100)
    assert table is not None and record["outcome"] == "accepted", record
    assert [kind for kind, _ in client.calls] == ["table-rebuild", "table-rebuild-retry"]
    assert "neither engine" in client.calls[1][1], client.calls[1][1]
    client = PageClientAdapter(FakeClient({"table-rebuild": ["唔係表格"]}))
    table, record = pp.rebuild_table(client, b"png", "甲乙丙丁戊己庚辛", required, 100)
    assert table is None and record["reason"].startswith("not a table") and len(client.calls) == 1
    assert record["refusedAnswers"] == ["唔係表格"], record
    client = PageClientAdapter(FakeClient({"table-rebuild": [invented], "table-rebuild-retry": [invented]}))
    table, record = pp.rebuild_table(client, b"png", "甲乙丙丁戊己庚辛", required, 100)
    assert table is None and record["refusedAnswers"] == [invented, invented], record
    print("rebuild: one table, nothing beyond what either engine read, nothing both read left out")


def check_union_and_nomination() -> None:
    # A disagreement run deleted here and inserted there is one text: once.
    # An insertion found nowhere in engine A's side is not shown to be
    # moved: it is left to the usual way (not in the result).
    segments = [{"tag": "delete", "a": "第一種讀本", "b": ""}, {"tag": "equal", "a": "每課", "b": "每課"},
                {"tag": "insert", "a": "", "b": "第一種讀本"}, {"tag": "insert", "a": "", "b": "乙"}]
    assert pp.settle_by_union(segments, [0, 2, 3]) == {0: "第一種讀本", 2: ""}

    # Runs, not characters: an insertion that shares characters with an
    # unrelated run of engine A's is not taken apart (measured on a roster:
    # a post engine A missed lost the characters a name and post of engine
    # A's also held).
    roster = [{"tag": "insert", "a": "", "b": "前本省財政廳長"}, {"tag": "equal", "a": "李三才", "b": "李三才"},
              {"tag": "delete", "a": "何子明本省公路局長", "b": ""}]
    assert pp.settle_by_union(roster, [0, 2]) == {2: "何子明本省公路局長"}
    # A run found whole in engine A's side, variants folded, is moved text;
    # a longer one by its pieces of WITNESS_ANCHOR or more, the rest of it
    # added in place.
    moved = [{"tag": "delete", "a": "何子明本省公路局長", "b": ""},
             {"tag": "insert", "a": "", "b": "本省公路"}, {"tag": "insert", "a": "", "b": "爲學堂之用"},
             {"tag": "delete", "a": "爲學堂之用", "b": ""}]
    moved[2]["b"] = "為學堂之用"
    assert pp.settle_by_union(moved, [0, 1, 2, 3]) == {0: "何子明本省公路局長", 1: "", 2: "", 3: "爲學堂之用"}
    longer = [{"tag": "delete", "a": "何子明本省公路局長", "b": ""}, {"tag": "insert", "a": "", "b": "何子明本省公路局長兼"}]
    assert pp.settle_by_union(longer, [0, 1]) == {0: "何子明本省公路局長", 1: "兼"}
    # ... unless engine B read that run of engine A's elsewhere on the page:
    # then it is not the text B read in the block.
    assert pp.settle_by_union(moved, [0, 1], elsewhere="某處職員錄何子明本省公路局長") == \
        {0: "何子明本省公路局長"}
    # A replacement whose engine-B side matches nothing: the usual way when
    # nothing of engine A's there matched either, both kept when it did.
    swap = [{"tag": "replace", "a": "每課習字", "b": "三行"}, {"tag": "insert", "a": "", "b": "每課習字"}]
    assert pp.settle_by_union(swap, [0]) == {} and pp.settle_by_union(swap, [0, 1]) == {0: "每課習字三行", 1: ""}

    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    blocks = [{"text": first + second, "label": "vertical_text", "box": [0, 0, 1, 1]},
              {"text": "正文一段兩個引擎都照同一次序讀到嘅字", "label": "text", "box": [0, 0, 1, 1]},
              {"text": "書眉", "label": "header", "box": [0, 0, 1, 1]}]
    witness = second + first + "正文一段兩個引擎都照同一次序讀到嘅字書眉"
    nominated = pp.nominate_tables(blocks, witness)
    assert [n["block"] for n in nominated] == [0] and "another order" in nominated[0]["signal"], nominated
    assert pp.nominate_tables(blocks, first + second + "正文一段兩個引擎都照同一次序讀到嘅字") == []
    print("nomination: a block engine B reads in another order; its disagreements settled once each")


GRID_HEAD = "某處報吿書第一期"
NOTE_1 = "是書每課可供一小時之用二年教員宜詳言其理令學者旣通珠筍"
REPEAT = "每頁六字約可習寫一星期必極熟始習下頁"
NOTE_2 = "是書以切於日用爲主" + REPEAT


def check_order_conflict_table_from_b() -> None:
    """A grid engine B contradicts, on an order-conflict page: its text comes
    from B's lines (window_merge), the copy and the running-head row go, the
    labels B never read stay, a rare substitution reaches 裁決."""
    rows = [[GRID_HEAD, "", "", ""],
            ["甲種讀本", "三年", NOTE_1, REPEAT + REPEAT],
            ["乙種讀本", "二年", NOTE_2, ""]]
    block = {"text": html(rows), "label": "table", "box": [0.1, 0.1, 0.8, 0.8]}
    b_lines = "是書以切於日用為主每頁六字約可\n習寫一星期必極熟始習下頁\n是書每課可供一小時之用二年敎員宜\n詳言其理令學者旣通珠𮅕"
    furniture = frozenset({GRID_HEAD[:8]})
    segments = pp.window_merge(block, b_lines, (), furniture)
    assert "".join(x["a"] for x in segments).__len__() == len(pp.CJK(block["text"]))
    settled = {x["settled"][1] for x in segments if "settled" in x}
    assert settled == {"copy-engine-B-read-once", "table-furniture-row"}, settled
    kept = "".join(x["a"] for x in segments if x["tag"] == "equal")
    assert "甲種讀本三年" in kept and "乙種讀本二年" in kept, kept
    assert any(x["tag"] == "replace" and x["a"] == "筍" and x["b"] == "𮅕" for x in segments)
    assert any(x["tag"] == "replace" and x["a"] == "爲" and x["b"] == "為" for x in segments), \
        "a variant does not stop the line anchoring"

    # Through the page: the block's text is B's lines plus the labels, once.
    client = page_client(format=[between], adjudicate=["構件：略\n定案：𮅕\nRESEARCH: no"],
                         **{"table-rebuild": [split_table]})
    text, record = run_page([("table", block["text"], block["box"])], b_lines, client,
                            furniture=furniture)
    assert record["mode"] == "merge-order-conflict", record["mode"]
    assert pp.CJK(text).count(pp.CJK(REPEAT)) == 1 and GRID_HEAD not in text, text
    assert text.count("每頁六字約可") == 1, text
    assert "甲種讀本" in text and "乙種讀本二年" in text and "𮅕" in text and "筍" not in text, text
    assert [kind for kind, _ in client.calls].count("adjudicate") == 1
    assert record["tablesRebuilt"][0]["outcome"] == "accepted", record["tablesRebuilt"]
    assert "table-rebuilt-unvouched" in [d["kind"] for d in record["doubts"]]
    print("order conflict: a contradicted grid is read from engine B's lines and rebuilt")


def written_by(segments: list[dict]) -> str:
    """The block as the merge writes it where engine A is preferred."""
    return "".join(x["settled"][0] if "settled" in x else (x["b"] if x["tag"] == "insert" else x["a"])
                   for x in segments)


YEARS = ["民國元年", "計收銀一百二十萬元", "民國二年", "計收銀二百三十萬元",
         "民國三年", "計收銀三百四十萬元", "民國四年", "計收銀四百五十萬元"]


def check_window_line_ends_and_copies() -> None:
    """window_merge on shapes measured on a year table (engine B wrote the
    table as one line, the next block's sentence after it)."""
    # Engine B's line runs on into the sentence engine A read as the next
    # block: that is the other block's text, not written into this one; the
    # table's cells are each written once (on the previous code the sentence
    # was aligned into the block and one cell came out twice).
    first, second, third = "民國元年計收銀一百萬元", "民國二年計收銀二百三十萬元", "民國三年計收銀三百萬元"
    sentence = "茲將各屬所收之數另表列之如下"
    block = {"text": html([[first, second, third]])}
    segments = pp.window_merge(block, first + third + second + sentence, elsewhere="\0" + sentence)
    text = written_by(segments)
    assert [text.count(cell) for cell in (first, second, third)] == [1, 1, 1] and sentence not in text, text
    beyond = [x["b"] for x in segments if x.get("settled", ("", ""))[1] == "engine-B-beyond-the-block-windows"]
    assert beyond == [sentence], beyond
    assert "".join(x["a"] for x in segments) == pp.CJK(block["text"]), "each character of A's block once"
    # Nothing says engine A read the sentence elsewhere: the line is not cut,
    # and the sentence is set against engine A's text as before.
    segments = pp.window_merge(block, first + third + second + sentence)
    assert not any(x.get("settled", ("", ""))[1] == "engine-B-beyond-the-block-windows" for x in segments)
    assert "".join(x["b"] for x in segments).endswith(sentence), segments

    # A slice whose stretch of engine A another window has claimed: the
    # cells the windows already hold are not written again.
    grid = [["民國四年", YEARS[1], YEARS[5], YEARS[7]], ["民國元年", YEARS[3], "民國三年", "民國二年"]]
    segments = pp.window_merge({"text": html(grid)}, "".join(YEARS))
    text = written_by(segments)
    assert [text.count(cell) for cell in YEARS] == [1] * 8, text
    assert any(x.get("settled", ("", ""))[1] == "engine-B-copy-of-a-window" for x in segments)
    # A piece that joins the end of one figure, a year and the start of the
    # next is no copy of the cells it touches: the figures stay.
    grid = [["民國三年", "民國元年", YEARS[5], YEARS[3], "民國四年"], [YEARS[1], "民國二年", YEARS[1], YEARS[7], ""]]
    text = written_by(pp.window_merge({"text": html(grid)}, "".join(YEARS)))
    assert text.count(YEARS[5]) >= 1 and text.count(YEARS[7]) >= 1, text

    # A looped engine-B reading is no reading of the block: the page keeps
    # engine A's grid text, with no window read from B's lines.
    rows = [[GRID_HEAD, "", "", ""], ["甲種讀本", "三年", NOTE_1, REPEAT + REPEAT], ["乙種讀本", "二年", NOTE_2, ""]]
    b_lines = ("是書以切於日用為主每頁六字約可\n習寫一星期必極熟始習下頁\n是書每課可供一小時之用二年敎員宜\n"
               "詳言其理令學者旣通珠𮅕\n" + "某處報吿書" * 80)
    client = page_client(format=[between], **{"table-rebuild": [split_table]})
    text, record = run_page([("table", html(rows), [0.1, 0.1, 0.8, 0.8])], b_lines, client)
    assert record["mode"] == "merge-order-conflict", record["mode"]
    assert not any(d["resolved_by"].startswith(("copy-engine-B", "engine-B-")) for d in record["engineDisagreements"])
    assert record["tablesRebuilt"] == [] and pp.CJK(text).count(pp.CJK(REPEAT)) >= 2, (record["tablesRebuilt"], text)
    print("window merge: a line's run-on into another block cut, claimed copies once, not on a looped reading")


def check_nominated_block_rebuilt() -> None:
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    blocks = [("text", prose, BOX["title"]), ("vertical_text", first + second, BOX["table"]),
              ("text", prose[::-1], BOX["prose"])]
    b_text = prose + second + first + prose[::-1]

    client = page_client(format=[between], **{"table-rebuild": [split_table]})
    text, record = run_page(blocks, b_text, client)
    assert record["mode"] == "merge" and record["tableNominations"][0]["block"] == 1, record
    assert record["tableNominations"][0]["outcome"].startswith("accepted"), record["tableNominations"]
    tables = pp._tables.find_tables(text)
    assert len(tables) == 1 and text.index(prose) < tables[0].start, text
    assert pp.CJK(text).count(first) == 1 and pp.CJK(text).count(second) == 1, text
    assert any(d["resolved_by"] == "order-disputed-block-union" for d in record["engineDisagreements"])
    assert "table-rebuilt-unvouched" in [d["kind"] for d in record["doubts"]]

    # The page says it is not a table: the block stays text, each run once.
    client = page_client(format=[between], **{"table-rebuild": ["唔係表格"]})
    text, record = run_page(blocks, b_text, client)
    assert not pp._tables.find_tables(text) and pp.CJK(text).count(first) == 1, text
    assert record["tableNominations"][0]["outcome"].startswith("refused: not a table")
    assert "table-rebuild-refused" not in [d["kind"] for d in record["doubts"]], record["doubts"]

    # A roster engine B reads in another order: engine A missed a post that
    # engine B read, and engine B read one of A's rows at the running head.
    # The post shares characters with that row but is no part of it, so it
    # is not taken apart; it goes the usual way - kept whole under a
    # preference, asked of 裁決 otherwise.
    head, post = "某處職員錄", "前本省財政廳長"
    rows = "張大文李三才前本省教育廳長何子明本省公路局長"
    roster = [("header", head, [0.9, 0.05, 0.05, 0.3]), ("text", prose, BOX["title"]),
              ("vertical_text", rows, BOX["table"])]
    b_roster = head + "何子明本省公路局長" + prose + "張大文" + post + "李三才前本省教育廳長"
    text, record = run_page(roster, b_roster, page_client(format=[between], **{"table-rebuild": ["唔係表格"]}))
    assert record["tableNominations"][0]["block"] == 2, record["tableNominations"]
    assert "張大文" + post in pp.CJK(text), text
    kept = next(d for d in record["engineDisagreements"] if d["engineB"] == post)
    assert kept["resolved_by"] == "prefer-engine-A" and kept["resolved"] == post, kept
    # The row engine B read at the running head is text read in another order,
    # which D2 (place_moved_once) places once, at engine A's site, before the
    # union is taken: the union is left the post alone, and the row is on the
    # page once.
    assert record["tableNominations"][0]["union"] == {"settled": 0, "leftToUsualWay": 1}
    moved = next(d for d in record["engineDisagreements"] if d["resolved_by"] == "moved-kept-once")
    assert moved["engineB"] == "何子明本省公路局長", moved
    assert pp.CJK(text).count("何子明本省公路局長") == 1, text

    def judge(user_text: str) -> str:
        import re
        read = re.search(r"乙\) (.*)", user_text).group(1).strip()
        return f"構件：略\n定案：{'無' if read == '（冇字）' else read}\nRESEARCH: no"

    client = page_client(format=[between], adjudicate=[judge], **{"table-rebuild": ["唔係表格"]})
    text, record = run_page(roster, b_roster, client, prefer="adjudicate")
    asked = [u for kind, u in client.calls if kind == "adjudicate"]
    assert any(post in u for u in asked) and "張大文" + post in pp.CJK(text), (asked, text)
    print("nominated block: rebuilt in its place when the page shows a table, text otherwise")


def check_rebuild_lock_both_readings() -> None:
    """A nominated block's rebuilt table may hold what either engine read in
    the block, each character at the higher engine's count (decision D20,
    rebuild_lock).  The shape measured on a ruled textbook table: engine B
    read a figure cell (here 四八) that the alignment paired with another of
    engine A's (六); 裁決, shown engine A's site, kept 六, and a table written
    as the page prints it was refused as inventing the figure."""
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    blocks = [("text", prose, BOX["title"]), ("vertical_text", first + second + "全書六册", BOX["table"]),
              ("text", prose[::-1], BOX["prose"])]
    b_text = prose + second + first + "全書四八册" + prose[::-1]
    printed = f"| {first} | 全書四八册 |\n| --- | --- |\n| {second} | |"

    def judge(user_text: str) -> str:
        import re
        read = re.search(r"甲\) (.*)", user_text).group(1).strip()
        return f"構件：略\n定案：{read}\nRESEARCH: no"

    assert pp.engine_b_inside([{"tag": "equal", "a": "甲乙", "b": "甲乙"}, {"tag": "replace", "a": "丙", "b": "丁戊"},
                               {"tag": "insert", "a": "", "b": "己"}, {"tag": "equal", "a": "庚", "b": "庚"}],
                              [0, 2, 3, 3], 1, 3) == "乙丁戊", "an insertion at the block's end is the next block's"
    text, runs = pp.rebuild_lock("甲乙丙", "甲乙丙", "甲乙丁戊", [("B", "丁戊")])
    assert text == "甲乙丙\n丁戊" and runs == [("B", "丁戊")], (text, runs)
    # Each character at the higher count, not the sum: engine A's 乙 twice and
    # engine B's once is two; the merge's own 丙 (裁決's) stays.
    text, runs = pp.rebuild_lock("甲乙丙", "甲乙乙", "甲乙", [("A", "乙")])
    assert pp.Counter(pp.CJK(text)) == pp.Counter("甲乙乙丙") and runs == [("A", "乙")], (text, runs)

    client = page_client(format=[between], adjudicate=[judge], **{"table-rebuild": [printed]})
    text, record = run_page(blocks, b_text, client)
    asked = [a for a in record["adjudications"] if a["writer_reading"] == "四八"]
    assert asked and asked[0]["resolved"] == "六", record["adjudications"]
    rebuilt = record["tablesRebuilt"][0]
    assert rebuilt["outcome"] == "accepted" and "四八" in "".join(rebuilt["lockAdded"]), rebuilt
    assert "四八" in text and pp._tables.find_tables(text), text
    # The structure pass writes the table as it stands: its character check
    # counts what the table holds beyond the merged text as sanctioned.
    assert "structure-rejected" not in [d["kind"] for d in record["doubts"]], record["doubts"]
    change = [c for c in record["decisionChanges"] if c["decision"] == "D20"]
    assert len(change) == 1 and change[0]["after"] == "四八" and change[0]["block"] == 1, change
    assert record["provenance"]["rebuildLock"] == "both"
    print("rebuild lock: a nominated block's table may hold either engine's reading of the block (D20)")


def check_witness_marks_left_out() -> None:
    """A block is also read against engine B with the glyphs this page's mark
    rule settles as engine A's marks left out (decision D21, marks_left_out).
    The shape measured on a magazine's price table: engine B wrote the note's
    full-cell comma as 丶, which broke the note's witness piece, so the block
    engine B reads in another order held one anchor and was not nominated."""
    import stand_in
    prose = "天地玄黃，宇宙洪荒，日月盈昃，辰宿列張寒來暑往秋收冬藏"
    note, charge = "郵費在內，代售處得另", "加手續費每册一分二"
    blocks = [stand_in.block(prose, [0.6, 0.1, 0.3, 0.8]),
              stand_in.block(charge + note, [0.1, 0.1, 0.3, 0.8], label="vertical_text")]
    b_text = prose.replace("，", "丶") + note.replace("，", "丶") + charge

    def table(locked: str) -> str:
        return f"| {charge} |\n| --- |\n| {pp.CJK(note)} |"

    with stand_in.book({1: (blocks, b_text)}) as root:
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0, rebuild=table))[1]
    nominated = record["tableNominations"]
    assert [n["block"] for n in nominated] == [1] and nominated[0]["witness"]["marksLeftOut"] == 4, nominated
    assert "丶" not in markdown and pp._tables.find_tables(markdown), markdown
    change = [c for c in record["decisionChanges"] if c["decision"] == "D21"]
    assert len(change) == 1 and pp._tables.find_tables(change[0]["after"]) and change[0]["block"] == 1, change
    assert record["provenance"]["markAwareWitness"] == "on"
    # The glyph the rule settles is no character the rebuild may write either.
    assert "丶" not in "".join(record["tablesRebuilt"][0].get("lockAdded") or []), record["tablesRebuilt"]
    # marks_left_out on its own: only the settled sites, a subsequence kept.
    segments = [{"tag": "equal", "a": "甲乙", "b": "甲乙"}, {"tag": "insert", "a": "", "b": "丶"},
                {"tag": "equal", "a": "丙丁", "b": "丙丁"}, {"tag": "insert", "a": "", "b": "盡"}]
    assert pp.marks_left_out(segments, [0, 2, 2, 4], [0, 2, 3, 5], "甲乙丶丙丁盡", ["甲乙", "", "丙丁", "盡"]) == \
        ("甲乙丙丁盡", 1)
    print("nomination: engine B's reading with the page's settled mark glyphs left out (D21)")


def check_rebuild_region() -> None:
    """A nominated block's rebuild takes in the blocks of its table the layout
    model split off (decision D22, rebuild_region): overlapping or within a
    glyph pitch, an edge shared, and read by engine B in another order.  The
    shape measured on a ruled textbook table: its header column was a block
    of its own, overlapping the body's box with the same top edge, read
    interleaved by engine A; rebuilt from the body alone the table had no
    header row, and the header's interleaved reading stayed on the page."""
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    head_a, head_b = "書名册全數書課每數册", "書名全書册數每册課數"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", head_a, [0.60, 0.20, 0.06, 0.60]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    b_text = prose + head_b + second + first
    # The rebuild writes the physical grid (its prompt), and on this vertical
    # page the benchmark's rule turns it: the right-hand printed column (the
    # labels, top down) becomes the first row (decision 8).
    printed = f"| {first} | 書名 |\n| --- | --- |\n| {second} | 全書册數 |\n|  | 每册課數 |"

    # The geometry and the order evidence on their own; the witness says
    # engine B read the header inside the region.
    agreed = ["x" * len(prose), "書名", "x" * 24]
    as_blocks = [{"text": text, "box": box, "label": label} for label, text, box in blocks]
    read = lambda j: head_a  # noqa: E731
    assert pp.rebuild_region(as_blocks, 2, (1000, 1400), agreed, read) == [1]
    assert pp.rebuild_region(as_blocks, 2, (1000, 1400), ["x" * len(prose), head_a, "x" * 24], read) == [], \
        "a neighbour engine B reads in engine A's order is not the table's"
    moved = [dict(b, box=[b["box"][0], 0.40, b["box"][2], 0.2]) if i == 1 else b for i, b in enumerate(as_blocks)]
    assert pp.rebuild_region(moved, 2, (1000, 1400), agreed, read) == [], "no edge shared"
    apart = [dict(b, box=[0.80, 0.20, 0.06, 0.60]) if i == 1 else b for i, b in enumerate(as_blocks)]
    assert pp.rebuild_region(apart, 2, (1000, 1400), agreed, read) == [], "further than a glyph pitch"
    # Not read in engine A's order is no evidence on its own: engine B must
    # have read most of it inside the region.
    assert pp.rebuild_region(as_blocks, 2, (1000, 1400), agreed, lambda j: "") == [], "engine B skipped it"
    assert pp.rebuild_region(as_blocks, 2, (1000, 1400), agreed, lambda j: head_a[:5]) == [], "half is not most"
    assert pp.rebuild_region(as_blocks, 2, (1000, 1400), agreed, lambda j: head_a[:6]) == [1]
    # region_witness: engine B's reading of the two blocks' stretches (one
    # stretch where they meet) beyond the nominated block's own text.
    pair = [{"text": "書名全書", "box": [0, 0, 1, 1]}, {"text": "第一第二", "box": [0, 0, 1, 1]}]
    interleaved = [{"tag": "replace", "a": "書名全書第一第二", "b": "書第名一全第書二"}]
    assert pp.region_witness(pair, interleaved, [0], 1, 0) == "書名全書"
    skipped = [{"tag": "delete", "a": "書名全書", "b": ""}, {"tag": "equal", "a": "第一第二", "b": "第一第二"}]
    assert pp.region_witness(pair, skipped, [0, 4], 1, 0) == ""

    client = page_client(format=[between], **{"table-rebuild": [printed]})
    text, record = run_page(blocks, b_text, client)
    nomination = record["tableNominations"][0]
    assert nomination["block"] == 2 and nomination["region"] == [1], nomination
    assert nomination["outcome"].startswith("accepted"), nomination
    asked = [u for kind, u in client.calls if kind == "table-rebuild"]
    assert all(c in between(asked[0]) for c in "書名全册數每課"), asked
    assert head_a not in pp.CJK(text) and pp._tables.find_tables(text), text
    assert pp.CJK(text).startswith(prose) and "書名" in text.split("|")[1], text
    change = [c for c in record["decisionChanges"] if c["decision"] == "D22"]
    # held: what both engines read of the header block, which the table holds.
    assert len(change) == 1 and change[0]["evidence"]["region"] == [1], change
    header = "".join(change[0]["evidence"]["held"])
    assert not pp.Counter(head_a) - pp.Counter(header) and header not in pp.CJK(text), change
    assert not record["tableCharactersLost"] and record["provenance"]["rebuildRegion"] == "on", record
    # The rebuild answers that it is no table: both blocks stay text.
    client = page_client(format=[between], **{"table-rebuild": ["唔係表格"]})
    text, record = run_page(blocks, b_text, client)
    assert not pp.Counter(header) - pp.Counter(pp.CJK(text)) and first in pp.CJK(text), text
    assert not pp._tables.find_tables(text), text
    print("rebuild region: a nominated block's table takes in its header column (D22)")


def check_rebuild_region_refused() -> None:
    """A region whose rebuild is refused falls back to the nominated block
    alone - the rebuild D22 off asks - before the block is given up as text;
    and a neighbour nominated itself is a table of its own, never taken in.
    The shape: two small tables side by side, each a block the layout called
    text and engine B read in another order.  Asked as one region, the
    answer held two tables and was refused; the page kept neither, and both
    fell back to the merge's interleaved text."""
    t1 = ("第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日")
    t2 = ("甲等學生每月繳費銀三毫正", "乙等學生每月繳費銀二毫正")
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", t1[0] + t1[1], [0.40, 0.20, 0.25, 0.60]),
              ("vertical_text", t2[0] + t2[1], [0.10, 0.20, 0.25, 0.60])]
    b_text = prose + t1[1] + t1[0] + t2[1] + t2[0]

    def table(t):
        return f"| {t[0][:6]} | {t[0][6:]} |\n| --- | --- |\n| {t[1][:6]} | {t[1][6:]} |"

    def rebuild(user):
        # A table for each of the two whose characters are all in the lock.
        locked = pp.CJK(between(user))
        return "\n\n".join(table(t) for t in (t1, t2) if not pp.Counter(t[0] + t[1]) - pp.Counter(locked))

    as_blocks = [{"text": text, "box": box, "label": label} for label, text, box in blocks]
    read = lambda j: as_blocks[j]["text"]  # noqa: E731
    assert pp.rebuild_region(as_blocks, 1, (1000, 1400), ["x" * len(prose), "", ""], read) == [2]
    assert pp.rebuild_region(as_blocks, 1, (1000, 1400), ["x" * len(prose), "", ""], read, nominated={1, 2}) == []
    client = page_client(format=[between], **{"table-rebuild": [rebuild]})
    text, record = run_page(blocks, b_text, client)
    outcomes = [(n["block"], n["outcome"][:8], n.get("region"), n.get("regionRefused"))
                for n in record["tableNominations"]]
    assert outcomes == [(1, "accepted", None, None), (2, "accepted", None, None)], outcomes
    assert len(pp._tables.find_tables(text)) == 2, text
    assert "table-rebuild-refused" not in [d["kind"] for d in record["doubts"]], record["doubts"]
    asked = [pp.CJK(between(u)) for kind, u in client.calls if kind == "table-rebuild"]
    assert len(asked) == 2 and not any(t1[0][:4] in a and t2[0][:4] in a for a in asked), asked

    # A region refused asks the nominated block alone: the table D22 off
    # keeps, and the block taken in stays text.  The header-column page of
    # check_rebuild_region, its region answered with two tables.
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    head_a, head_b = "書名册全數書課每數册", "書名全書册數每册課數"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", head_a, [0.60, 0.20, 0.06, 0.60]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    body = f"| {first} |\n| --- |\n| {second} |"

    def two_if_region(user):
        return (f"| 書名 | 全書册數 |\n| --- | --- |\n| 每册 | 課數 |\n\n{body}"
                if "書名" in between(user) else body)

    client = page_client(format=[between], **{"table-rebuild": [two_if_region]})
    text, record = run_page(blocks, prose + head_b + second + first, client)
    [nomination] = record["tableNominations"]
    assert nomination["outcome"].startswith("accepted") and "region" not in nomination, nomination
    assert nomination["regionRefused"]["region"] == [1], nomination
    assert nomination["regionRefused"]["reason"].startswith("2 tables in the answer"), nomination
    [rebuilt] = record["tablesRebuilt"]
    assert rebuilt["regionRefused"] == nomination["regionRefused"] and "region" not in rebuilt, rebuilt
    assert not pp.Counter(head_a) - pp.Counter(pp.CJK(text)) and pp._tables.find_tables(text), text
    assert not [c for c in record["decisionChanges"] if c["decision"] == "D22"], record["decisionChanges"]
    print("rebuild region: a refused region asks the nominated block alone; a nominated neighbour is not taken in")


def check_text_beside_rebuilt_table_kept() -> None:
    """The blocks a refused region named stay text beside the table rebuilt
    from the nominated block alone, and the structure pass may not leave them
    out; a page that lacks them says so in tableCharactersLost.  The shape
    measured on x2 p.61: a textbook table's region (its header column and
    its body) was refused for a character no engine read, the body alone was
    rebuilt and accepted, and the structure pass left the header column's
    text out - the deletion guard cut it into short pieces against the
    table's cells, tableCharactersLost counted table blocks only, and the
    page was sealed with the header gone and no doubt."""
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    head_a, head_b = "書名册全數書課每數册年備期用法", "書名全書册數每册課數備用年期用法"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", head_a, [0.60, 0.20, 0.06, 0.60]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    body = f"| {first} |\n| --- |\n| {second} |"

    def region_or_body(user):
        # The region's answer writes a header character no engine read (言).
        return (f"| 書名言 | 全書册數 |\n| --- | --- |\n| {first} | {second} |"
                if "書名" in between(user) else body)

    def drop_header(user):
        # The structure pass leaves out the header column's text.
        return "\n".join(line for line in between(user).split("\n") if "書名" not in line)

    for formatter, retry in ((drop_header, drop_header), (drop_header, between)):
        client = page_client(format=[formatter], **{"format-retry": [retry], "table-rebuild": [region_or_body],
                                                    "table-rebuild-retry": [region_or_body]})
        text, record = run_page(blocks, prose + head_b + second + first, client)
        [nomination] = record["tableNominations"]
        assert nomination["outcome"].startswith("accepted") and nomination["regionRefused"]["region"] == [1], nomination
        assert "deleted" in (record["formatterRetryReason"] or ""), record["formatterRetryReason"]
        assert not pp.Counter(head_a) - pp.Counter(pp.CJK(text)) and pp._tables.find_tables(text), text
        assert not record["tableCharactersLost"], record["tableCharactersLost"]
        kinds = [d["kind"] for d in record["doubts"]]
        assert ("structure-rejected" in kinds) == (retry is drop_header), kinds

    # A header of a few characters is no deleted run (DELETED_RUN_LIMIT), and
    # is kept all the same.
    short_a, short_b = "書名册數", "數册名書"
    blocks_short = [blocks[0], ("vertical_text", short_a, blocks[1][2]), blocks[2]]
    client = page_client(format=[drop_header], **{"format-retry": [between], "table-rebuild": [region_or_body],
                                                  "table-rebuild-retry": [region_or_body]})
    text, record = run_page(blocks_short, prose + short_b + second + first, client)
    assert record["tableNominations"][0].get("regionRefused"), record["tableNominations"]
    assert "beside a rebuilt table" in (record["formatterRetryReason"] or ""), record["formatterRetryReason"]
    assert short_a in pp.CJK(text), text

    # Counted as a table block's characters are: a page that lacks them says so.
    as_blocks = [{"text": prose, "label": "text"}, {"text": head_a, "label": "vertical_text"},
                 {"text": first + second, "label": "table"}]
    agreed = [prose, "", first + second]
    assert pp.table_characters_lost(as_blocks, agreed, prose + body) == ""
    lost = pp.table_characters_lost(as_blocks, agreed, prose + body, beside={1: "書名全書"})
    # One 書 of the two stands in the prose (本年新頒之書): prose only hides a loss.
    assert lost == "書名全", lost
    print("rebuild region refused: the text beside the table rebuilt without it is kept, or its loss sealed")


def check_text_beside_after_answer_nearby() -> None:
    """D16's reading-nearby rule (round3-merge) and the guard on the text
    beside a rebuilt table (round3-tables) decide together what of a header
    column stays: the second copy 裁決 answered away is not asked for, and
    the one copy left is.  The shape of the textbook page where both fire in
    every benchmark run: engine B read three header characters ahead of
    engine A's row-by-row reading of them, 裁決 answered 無 there, and D16
    let the answer go because the header holds them nearby; the region of
    the header and the body was refused, the body rebuilt alone.  Replayed
    on that page (four runs' verdicts, a structure pass leaving the header
    out): round3-merge lost the header with no doubt; round3-tables kept it
    with the three characters twice; merged, kept once.  Here the header is
    shorter than DELETED_RUN_LIMIT with its copy gone, so only the guard on
    the text beside the table can see it dropped."""
    first, second = "甲乙丙丁戊己庚辛壬癸天地", "子丑寅卯辰巳午未申酉玄黃"
    prose = "宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽"
    head_a, head_b = "書名册全數書課每數册", "書名全書每册册數課數"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", head_a, [0.60, 0.20, 0.06, 0.60]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    body = f"| {first} |\n| --- |\n| {second} |"

    def region_or_body(user):
        # The region's answer writes a header character no engine read (言), 裁決 does not confirm it.
        return (f"| 書名言 | 全書册數 |\n| --- | --- |\n| {first} | {second} |" if "書名" in between(user) else body)

    def judge(user):
        # 無 where engine B read the header's characters a second time; engine A's reading elsewhere.
        a = re.search(r"甲\) (.*)", user).group(1).strip()
        return f"構件：略\n定案：{'無' if '乙) 全書每' in user or a == '（冇字）' else a}\nRESEARCH: no"

    def drop_header(user):
        return "\n".join(line for line in between(user).split("\n") if "書名" not in line)

    for retry in (between, drop_header):
        client = page_client(format=[drop_header], adjudicate=[judge], **{
            "coverage-read": [head_a], "format-retry": [retry], "table-rebuild": [region_or_body],
            "table-rebuild-retry": [region_or_body]})
        text, record = run_page(blocks, prose + head_b + second + first, client, prefer="adjudicate")
        assert [(c["rule"], c["engineB"]) for c in record["decisionChanges"] if c["decision"] == "D16"] == [
            ("answer-accepted-reading-nearby", "全書每")], record["decisionChanges"]
        [nomination] = record["tableNominations"]
        assert nomination["outcome"].startswith("accepted") and nomination["regionRefused"]["region"] == [1], nomination
        assert "beside a rebuilt table" in (record["formatterRetryReason"] or ""), record["formatterRetryReason"]
        assert head_a in pp.CJK(text) and pp.CJK(text).count("全") == 1, text
        assert not record["tableCharactersLost"], record["tableCharactersLost"]
    print("rebuild region refused: the header D16 left one copy of is kept, once")


def check_unread_table_characters() -> None:
    """A rebuilt table may write up to two characters no engine read in its
    region, each confirmed by 裁決 on a crop of its own spot, each sealed as
    a doubt (the user's headerCharacters ruling, 2026-09-27; confirm_unread).
    The shape measured on hinzing-6 p.61: the header column's fifth cell
    prints 敎授要言, engine A read 敎授要, engine B (v1.0) none of it, and
    the region rebuild (header and body, D22) was refused for 言 in all six
    runs; the rebuild itself wrote 旨 for 言 once, 發 for 敎 once."""
    # Where the character stands: after the longest stretch of its cell before
    # it that engine A read once in the region, or before the one after it.
    texts = ["書名册全數書課每數册年備期用敎授要", "第一種讀本每課可供一小時"]
    assert pp.unread_spot(texts, "敎授要言", 3) == (0, 17, "敎授要", ""), pp.unread_spot(texts, "敎授要言", 3)
    assert pp.unread_spot(texts, "言敎授", 0) == (0, 14, "數册年備期用"[-4:], "敎授")   # before the stretch after it
    assert pp.unread_spot(texts, "言某某", 0) is None          # nothing around it in the cell is read
    assert pp.unread_spot(texts, "每課言可供", 2)[:2] == (1, 7)   # 每課 read once: after it
    assert pp.unread_spot(["甲乙", "甲乙"], "甲乙言", 2) is None  # read twice: no place
    # Every place of a class no engine read, in reading order.
    markup = "| 書名 | 敎授要言 |\n| --- | --- |\n| 讀本 | 言文 |"
    assert pp.unread_sites(markup, "言") == [("言", "敎授要言", 3), ("言", "言文", 0)]

    lock = pp.Counter("書名敎授要讀本一種")
    answer = "| 書名 | 敎授要言 |\n| --- | --- |\n| 一種 | 讀本 |"
    asked = []
    table, why = pp.confirm_unread(answer, lock, pp.Counter("書名讀本"), "",
                                   lambda c, cell, at: {"char": c, "confirmed": True}, asked)
    assert table is not None and "confirmed by 裁決 (言)" in why and len(asked) == 1, why
    table, why = pp.confirm_unread(answer, lock, pp.Counter("書名讀本"), "",
                                   lambda c, cell, at: {"char": c, "confirmed": False, "answered": "旨"}, [])
    assert table is None and why == "裁決 did not confirm 言 (read 旨)", why
    three = answer.replace("讀本", "言本").replace("一種", "言種")
    asked = []
    table, why = pp.confirm_unread(three, lock, pp.Counter("書名"), "",
                                   lambda c, cell, at: {"char": c, "confirmed": True}, asked)
    assert table is None and "more than the 2" in why and not asked, why
    # Something else wrong besides (a character both engines read left out): not asked.
    assert pp.confirm_unread(answer.replace("書名", "書"), lock, pp.Counter("書名讀本"), "",
                             lambda c, cell, at: {"confirmed": True}, []) == (None, "")

    # On a page: the header column and the body, the header's last character
    # read by no engine.  裁決 reads it on its spot: the region stands.
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    # Prose enough that the page merges (engine B read none of the fifth header cell).
    prose = ("右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
             "凡塾中教員務須先將各本通讀一過然後按表所定年期逐課教授勿稍躐等")
    head_a, head_b = "書名册全數書課每數册年備期用敎授要", "書名全書册數每册課數備用年期"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("vertical_text", head_a, [0.60, 0.20, 0.06, 0.60]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    b_text = prose + head_b + second + first
    region_answer = (f"| 書名 | 全書册數 | 每册課數 | 備用年期 | 敎授要言 |\n| --- | --- | --- | --- | --- |\n"
                     f"| {first[:5]} | {first[5:]} | {second[:5]} | {second[5:]} | |")
    body = f"| {first} |\n| --- |\n| {second} |"

    def rebuild(user):
        return region_answer if "書名" in between(user) else body

    def judge(reading):
        # 裁決 on the character no engine read; engine A's reading anywhere else.
        def answer(user):
            if "乙) 言" in user:
                return f"構件：略\n定案：{reading}"
            first = re.search(r"甲\) (.*)", user)
            return "構件：略\n定案：" + ((first.group(1).strip() if first else "") or "無").replace("（冇字）", "無")
        return answer

    client = page_client(format=[between], adjudicate=[judge("言")],
                         **{"table-rebuild": [rebuild], "table-rebuild-retry": [rebuild]})
    text, record = run_page(blocks, b_text, client)
    [nomination] = record["tableNominations"]
    assert nomination["outcome"].startswith("accepted") and nomination["region"] == [1], nomination
    assert "confirmed by 裁決 (言)" in nomination["outcome"], nomination
    assert "敎授要言" in pp.CJK(text) and not record["tableCharactersLost"], text
    [rebuilt] = record["tablesRebuilt"]
    [asked] = rebuilt["unreadAsked"]
    assert asked["confirmed"] and asked["answered"] == "言" and asked["block"] == 1 and asked["crop"], asked
    assert [a for a in record["adjudications"] if a.get("askedFor") == "headerCharacters"], record["adjudications"]
    [doubt] = [d for d in record["doubts"] if d["kind"] == "table-unread-confirmed"]
    assert "「言」 in 「敎授要言」" in doubt["detail"], doubt
    [change] = [c for c in record["decisionChanges"] if c["decision"] == "headerCharacters"]
    assert change["evidence"]["confirmed"][0]["char"] == "言"
    assert "言" not in "".join(c["after"] or "" for c in record["decisionChanges"] if c["decision"] == "D20")
    assert record["provenance"]["unreadTableCharacters"] == "on"

    # 裁決 reads another character there (the rebuild wrote 旨 for the printed 言): refused, as
    # today - the region falls back to the body alone, and the header stays text.
    client = page_client(format=[between], adjudicate=[judge("旨")],
                         **{"table-rebuild": [rebuild], "table-rebuild-retry": [rebuild]})
    text, record = run_page(blocks, b_text, client)
    [nomination] = record["tableNominations"]
    assert nomination["regionRefused"]["region"] == [1], nomination
    assert "裁決 did not confirm 言 (read 旨)" in nomination["regionRefused"]["reason"], nomination
    assert len(nomination["regionRefused"]["unreadAsked"]) == 2, nomination   # the answer and its retry
    # ... on one spot: asked once.
    assert len([a for a in record["adjudications"] if a.get("askedFor") == "headerCharacters"]) == 1
    retry = [u for kind, u in client.calls if kind == "table-rebuild-retry"][0]
    assert "旨" not in retry.split("原因：")[1], retry   # the retry is told the lock's reason, not 裁決's reading
    assert not pp.Counter(head_a) - pp.Counter(pp.CJK(text)), text

    # The switch off: nothing asked, refused as before.
    client = page_client(format=[between], adjudicate=[judge("言")],
                         **{"table-rebuild": [rebuild], "table-rebuild-retry": [rebuild]})
    text, record = run_page(blocks, b_text, client, switches=("--no-unread-table-characters",))
    assert "regionRefused" in record["tableNominations"][0], record["tableNominations"]
    assert not [a for a in record["adjudications"] if a.get("askedFor") == "headerCharacters"]
    assert record["provenance"]["unreadTableCharacters"] == "off"
    print("rebuild: up to two characters no engine read stand when 裁決 reads them on their spot (headerCharacters)")


EDGE_PROSE = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏"
EDGE_TAIL = "閏餘成歲律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡"
EDGE_TITLE = "廣東某年收支表"
EDGE_NOTE = "右表數目據本年冊報"
EDGE_GRID = [["項目", "甲年"], ["田賦", "一二〇〇"], ["鹽稅", "二三〇〇"], ["關稅", "四五〇〇"]]
EDGE_BOX = {"top": [0.1, 0.05, 0.8, 0.2], "table": [0.1, 0.3, 0.8, 0.6], "foot": [0.1, 0.92, 0.8, 0.05]}
EDGE_BLOCKS = [("text", EDGE_PROSE, EDGE_BOX["top"]), ("table", html(EDGE_GRID), EDGE_BOX["table"])]


def edge_page(blocks, b_text, prefer="A", switches=(), unplaced="", **answers):
    """run_page with a formatter that keeps what it is given and 裁決 answering
    engine B's reading (乙) wherever it is asked; returns the page, its seal
    and the client."""
    def judge(user_text: str) -> str:
        read = re.search(r"乙\) (.*)", user_text).group(1).strip()
        return f"構件：略\n定案：{'無' if read == '（冇字）' else read}\nRESEARCH: no"

    client = page_client(**{"format": [between], "adjudicate": [judge], **answers})
    text, record = run_page(blocks, b_text, client, prefer=prefer, switches=switches, unplaced=unplaced)
    return text, record, client


def edge_changes(record: dict) -> list[dict]:
    return [c for c in record["decisionChanges"] if c["rule"] == "table-edge-text"]


def edge_doubts(record: dict) -> list[str]:
    return [d["detail"] for d in record["doubts"] if d["kind"] == "table-edge-text"]


def check_table_edge_text() -> None:
    """The text a table block's merged text holds before the table's first
    cell is written before the table (textLoss, table-edge-text;
    restate_table, table_edges).  Found in the round-3
    integration review, on a made-up page (this one): a title only engine B
    read above a table the two engines read alike - block_spans hands a
    reading at a block boundary to the block after it, the table - was in no
    cell, and the table's token stood for the block's whole span, so the
    page lacked it under either preference and under 裁決, which had answered
    it printed, with no doubt."""
    prose, title, grid = EDGE_PROSE, EDGE_TITLE, EDGE_GRID
    blocks, b_text = EDGE_BLOCKS, EDGE_PROSE + "\n" + EDGE_TITLE + "\n" + html(EDGE_GRID)
    for prefer in ("A", "B", "adjudicate"):
        text, record, client = edge_page(blocks, b_text, prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == grid and pp.CJK(text).count(title) == 1, (prefer, text)
        assert pp.CJK(text[:table.start]) == prose + title, (prefer, text)
        [change] = edge_changes(record)
        assert change["decision"] == "textLoss", change
        [beside] = change["evidence"]["beside"]
        assert beside["side"] == "before" and beside["written"] == title and beside["onPage"], beside
        assert (beside["engineA"], beside["engineB"]) == ("", title), beside
        [doubt] = edge_doubts(record)
        assert title in doubt and "engine A read 0 of its 7 character(s) there, engine B 7): written" in doubt, doubt
        assert record["provenance"]["tableEdgeText"] == "on"
        if prefer == "adjudicate":
            assert [a["resolved"] for a in record["adjudications"]] == [title], record["adjudications"]

    # Its marks: a table block has no punctuation pass; the marks engine B read
    # there go with it.
    text, record, _ = edge_page(blocks, prose + "\n" + title + "（單位元）\n" + html(grid))
    [table] = pp._tables.find_tables(text)
    assert text[:table.start].strip().endswith(title + "（單位元）"), text

    # The structure pass refused (it answers nothing, twice): the page is put
    # together block by block, and the title stands before the table there too.
    text, record, _ = edge_page(blocks, b_text, format=[""])
    [table] = pp._tables.find_tables(text)
    assert "structure-rejected" in [d["kind"] for d in record["doubts"]], record["doubts"]
    assert pp.CJK(text[:table.start]) == prose + title and table.grid() == grid, text
    assert "table-text-left-out" not in [d["kind"] for d in record["doubts"]], record["doubts"]
    print("tables: the text a table block holds before the table's first cell is written before it, "
          "with its marks, and sealed (textLoss table-edge-text)")


def check_table_edge_text_trailing() -> None:
    """The text a table block's merged text holds after the table's last cell
    - where the table is the page's last block, block_spans hands it a
    reading after the table - is written after the table (table_edges).  On
    a made-up page (this one): a note only engine B read after such a table
    was lost, with no doubt."""
    prose, grid, note = EDGE_PROSE, EDGE_GRID, EDGE_NOTE
    blocks = EDGE_BLOCKS
    for prefer in ("A", "B"):
        text, record, _ = edge_page(blocks, prose + "\n" + html(grid) + "\n" + note + "。", prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == grid and text[table.end:].strip() == note + "。", (prefer, text)
        [change] = edge_changes(record)
        assert [b["side"] for b in change["evidence"]["beside"]] == ["after"], change
        assert len(edge_doubts(record)) == 1
    # Both edges of one table.
    text, record, _ = edge_page(blocks, prose + "\n" + EDGE_TITLE + "\n" + html(grid) + "\n" + note)
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[:table.start]) == prose + EDGE_TITLE and pp.CJK(text[table.end:]) == note, text
    [change] = edge_changes(record)
    assert [b["side"] for b in change["evidence"]["beside"]] == ["before", "after"], change
    # A block after the table: the reading at the boundary is that block's
    # (block_spans), as it was - after the table, once, nothing to seal.
    after = blocks + [("text", EDGE_TAIL, EDGE_BOX["foot"])]
    text, record, _ = edge_page(after, prose + "\n" + html(grid) + "\n" + note + "\n" + EDGE_TAIL)
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[table.end:]) == note + EDGE_TAIL and not edge_changes(record), text
    # The last character misread by engine A and engine B's taken: the note
    # stays after the table, not in its last cell (table_places).
    misread = grid[:-1] + [["關稅", "四五〇八"]]
    text, record, _ = edge_page([blocks[0], ("table", html(misread), EDGE_BOX["table"])],
                                prose + "\n" + html(grid) + "\n" + note, "B")
    [table] = pp._tables.find_tables(text)
    assert table.grid() == grid and pp.CJK(text[table.end:]) == note, text
    # But a last figure one engine read a digit longer is a reading of the
    # cell, not text after the table: it stays whole there.
    longer = grid[:-1] + [["關稅", "四五〇〇〇"]]
    text, record, _ = edge_page([blocks[0], ("table", html(misread), EDGE_BOX["table"])],
                                prose + "\n" + html(longer), "B")
    [table] = pp._tables.find_tables(text)
    assert table.grid() == longer and not pp.CJK(text[table.end:]) and not edge_changes(record), text
    # So is a last cell of text one engine read a character longer, with no
    # more than INSERTION_LIMIT beyond it (split_lopsided's measure).
    noted = [["項目", "甲年", "附註"], ["田賦", "一二〇〇", "照舊"], ["關稅", "四五〇〇", "另加五成"]]
    a_noted = noted[:-1] + [["關稅", "四五〇〇", "另如"]]
    text, record, _ = edge_page([blocks[0], ("table", html(a_noted), EDGE_BOX["table"])],
                                prose + "\n" + html(noted), "B")
    [table] = pp._tables.find_tables(text)
    assert table.grid() == noted and not pp.CJK(text[table.end:]) and not edge_changes(record), text
    print("tables: the text a table block holds after the table's last cell is written after it")


def check_table_edge_text_marks_in_order() -> None:
    """The edge text's marks are written in the order the engine wrote them
    (marks_as_written), each gap parted as seam_sides parts it.  Found in the
    round-3 integration review, on a made-up page (this one): a bracket pair
    with no character between its brackets - a bracketed Arabic numeral, or
    empty brackets - came out backwards, 第(2) as 第）（, and at the table's
    edge with one bracket, 表（） as 表）: the old order wrote a gap's closing
    marks first and tied an opening one to the character after, the table's
    first cell, outside the edge text.  The numeral
    itself is not written: the merged text holds characters (CJK) alone.  A
    mark stays with the line it is written on (seam_sides): a bullet heading
    the line after the table is that line's, where the old order gave it to
    the table's last cell."""
    prose, grid, title, note = EDGE_PROSE, EDGE_GRID, EDGE_TITLE, EDGE_NOTE
    cases = [("第(2)" + title, "", "第（）" + title, ""),
             (title + "（）", "", title + "（）", ""),
             (title + "(2)", "", title + "（）", ""),
             ("「" + title + "」", "『" + note + "』", "「" + title + "」", "『" + note + "』"),
             ("", "(2)" + note, "", "（）" + note),
             ("", note + "（）", "", note + "（）"),
             ("", "（注）" + note + "。", "", "（注）" + note + "。"),
             # A bullet heading the note's line goes with that line; a comma
             # an engine wrapped to the head of the line closes the line
             # before (the table's), as before.
             ("", "▲" + note, "", "▲" + note),
             ("", "，" + note, "", note)]
    for above, below, want_above, want_below in cases:
        b_text = prose + "\n" + (above + "\n" if above else "") + html(grid) + ("\n" + below if below else "")
        for prefer in ("A", "B"):
            text, record, _ = edge_page(EDGE_BLOCKS, b_text, prefer)
            [table] = pp._tables.find_tables(text)
            got = (text[:table.start].strip().split("\n")[-1] if above else "", text[table.end:].strip())
            assert table.grid() == grid and got == (want_above, want_below), (above, below, prefer, text)
            written = [(b["side"], b["written"]) for c in edge_changes(record) for b in c["evidence"]["beside"]]
            want = [(side, w) for side, w in (("before", want_above), ("after", want_below)) if w]
            assert written == want, (above, below, prefer, written)
    # The marks one engine read, tied to the merged text: a pair with no
    # character between its brackets stays together, and at a stretch's edge
    # with no seam between it and the next character goes with that
    # character, both brackets or neither.
    for a_text, whole, head in (("器具表(2)甲乙", "器具表（）甲乙", "器具表"),
                                ("器具表「」甲乙", "器具表「」甲乙", "器具表"),
                                ("器具表。（二）甲乙", "器具表。（二）甲乙", "器具表。"),
                                ("器具表）。（甲乙", "器具表）。（甲乙", "器具表）。")):
        chars = pp.CJK(a_text)
        placed: dict[int, str] = {}
        tied = pp.engine_marks_merged(a_text, [{"a": chars, "b": chars}], [chars], placed=placed, written=True)
        got = (pp.marked_stretch(chars, 0, len(chars), [(tied, placed)]), pp.marked_stretch(chars, 0, 3, [(tied, placed)]))
        assert got == (whole, head), (a_text, got)
    # Where the merged text holds another engine's reading of the print (engine
    # B read the numeral as a character), the print parts the gap, as for a
    # tile that keeps engine A's marks: engine A's 第(2)表 is 第（二）表.
    segments, pieces = [{"a": "第", "b": "第"}, {"a": "", "b": "二"}, {"a": "表", "b": "表"}], ["第", "二", "表"]
    placed = {}
    tied = pp.engine_marks_merged("第(2)表", segments, pieces, placed=placed, written=True)
    assert pp.marked_stretch("第二表", 0, 3, [(tied, placed)]) == "第（二）表", tied
    print("tables: the edge text's marks are written in the order the engine wrote them")


def check_table_edge_text_read_by_a() -> None:
    """A title engine A read too is not written twice.  Read by both engines
    above the table, it is in its own block's span, not the table's.  Read
    by engine A below the table and by engine B above it, it is text read in
    another order, which D2 places once, at engine A's site, before the
    table is recovered - but not a run shorter than MOVE_MINIMUM: engine B's
    copy then stands at the table's edge, and written there it made two
    (edge_copies).  It is not written, and the doubt names it (before, it
    was dropped with nothing said)."""
    prose, title, grid = EDGE_PROSE, EDGE_TITLE, EDGE_GRID
    blocks, b_text = EDGE_BLOCKS, EDGE_PROSE + "\n" + EDGE_TITLE + "\n" + html(EDGE_GRID)
    titled = [blocks[0], ("doc_title", title, [0.1, 0.26, 0.8, 0.03]), blocks[1]]
    for prefer in ("A", "B", "adjudicate"):
        text, record, _ = edge_page(titled, b_text, prefer)
        assert pp.CJK(text).count(title) == 1 and not edge_changes(record) and not edge_doubts(record), text
        below = blocks + [("doc_title", title, EDGE_BOX["foot"])]
        text, record, _ = edge_page(below, b_text, prefer)
        [table] = pp._tables.find_tables(text)
        assert pp.CJK(text).count(title) == 1 and pp.CJK(text[table.end:]) == title, (prefer, text)
        assert not edge_changes(record) and not edge_doubts(record), (record["decisionChanges"], record["doubts"])
        short = "附表"
        below = blocks + [("doc_title", short, EDGE_BOX["foot"])]
        text, record, _ = edge_page(below, prose + "\n" + short + "\n" + html(grid), prefer)
        [table] = pp._tables.find_tables(text)
        assert pp.CJK(text).count(short) == 1 and pp.CJK(text[table.end:]) == short, (prefer, text)
        assert "extra-copy" not in [d["kind"] for d in record["doubts"]], record["doubts"]
        [doubt] = edge_doubts(record)
        assert short in doubt and "not written" in doubt and not edge_changes(record), doubt
    print("tables: a title engine A read too is not written twice beside the table")


def check_table_edge_text_misread_elsewhere() -> None:
    """A title only engine B read is no extra copy of text the page holds
    where engine B read it too, one character misread (edge_copies: a copy
    elsewhere counts only as engine A's reading alone).  Found in the round-3
    review of the table-edge rule, on a made-up page (this one): the title
    restates two of the table's column labels, and engine B read one label
    with one character wrong in its cells; engine B's reading then held the
    labels' stretches once, the page twice, and the title was held back as
    an extra copy (57%) - under 裁決 too, which had answered it printed -
    with no decisionChanges entry.  So too a title the prose above names,
    which engine B misread there."""
    title = "田賦鹽稅比較表"
    grid = [["年分", "田賦", "鹽稅"], ["甲年", "一二〇〇", "二三〇〇"], ["乙年", "一三〇〇", "二四〇〇"]]
    b_grid = [["年分", "田賊", "鹽稅"]] + grid[1:]
    blocks = [("text", EDGE_PROSE, EDGE_BOX["top"]), ("table", html(grid), EDGE_BOX["table"])]
    b_text = EDGE_PROSE + "\n" + title + "\n" + html(b_grid)
    for prefer in ("A", "B", "adjudicate"):
        text, record, _ = edge_page(blocks, b_text, prefer)
        [table] = pp._tables.find_tables(text)
        assert pp.CJK(text[:table.start]) == EDGE_PROSE + title, (prefer, text)
        [change] = edge_changes(record)
        assert change["evidence"]["beside"][0]["written"] == title, change
        [doubt] = edge_doubts(record)
        assert "engine B 7): written" in doubt, doubt

    # 裁決 answering what is printed: the label as printed, and the title.
    def printed(user_text: str) -> str:
        read = re.search(r"乙\) (.*)", user_text).group(1).strip()
        read = {"賊": "賦", "（冇字）": "無"}.get(read, read)
        return f"構件：略\n定案：{read}\nRESEARCH: no"

    text, record, _ = edge_page(blocks, b_text, "adjudicate", adjudicate=[printed])
    [table] = pp._tables.find_tables(text)
    assert table.grid() == grid and pp.CJK(text[:table.start]) == EDGE_PROSE + title, text
    assert sorted(a["resolved"] for a in record["adjudications"]) == sorted([title, "賦"]), record["adjudications"]

    # The prose above names the table, and engine B misread one character there.
    named, misread = "歲入總表", "歲人總表"
    prose = EDGE_PROSE[:16] + "詳見" + named + EDGE_PROSE[16:]
    blocks = [("text", prose, EDGE_BOX["top"]), ("table", html(EDGE_GRID), EDGE_BOX["table"])]
    for prefer in ("A", "B", "adjudicate"):
        text, record, _ = edge_page(blocks, prose.replace(named, misread) + "\n" + named + "\n" + html(EDGE_GRID),
                                    prefer)
        [table] = pp._tables.find_tables(text)
        assert pp.CJK(text[:table.start]).endswith(named) and table.grid() == EDGE_GRID, (prefer, text)
    print("tables: a title engine B read beside the table is written where engine B misread its words elsewhere")


def check_table_edge_text_pieces() -> None:
    """The edge text is judged in the pieces engine B read it in, its lines
    and cells (edge_verdicts): a line only engine B read is written though
    engine B's reading of text engine A read elsewhere stands beside it.
    Found in the round-3 review of the table-edge rule, on a made-up page
    (this one): after a table that ends the page engine B read a unit line
    only it read, then the running foot, which engine A read as its footer
    block first in its order; judged as one stretch the foot was 60% of it,
    and the unit line was not written either, the doubt blaming it on a
    reading elsewhere.  Figures on a line of their own (the
    folio) say nothing by themselves and go with the foot beside them."""
    foot, unit, folio = "省稅第十一章", "單位銀元", "六九三"
    footer = ("footer", foot, [0.1, 0.96, 0.6, 0.03])
    blocks = [footer, ("text", EDGE_PROSE, EDGE_BOX["top"]), ("table", html(EDGE_GRID), EDGE_BOX["table"])]
    b_text = EDGE_PROSE + "\n" + html(EDGE_GRID) + "\n" + unit + "\n" + foot
    for prefer in ("A", "B", "adjudicate"):
        text, record, _ = edge_page(blocks, b_text, prefer)
        [table] = pp._tables.find_tables(text)
        assert pp.CJK(text[table.end:]) == unit and pp.CJK(text).count(foot) == 1, (prefer, text)
        [change] = edge_changes(record)
        [beside] = change["evidence"]["beside"]
        assert beside["written"] == unit and [h["text"] for h in beside["heldElsewhere"]] == [foot], beside
        [doubt] = edge_doubts(record)
        assert f"「{foot}」 not written" in doubt and f"the rest written (「{unit}」)" in doubt, doubt
    # The folio on a line of its own after the foot: figures, which go with
    # the foot beside them.
    blocks = [footer, ("number", folio, [0.8, 0.96, 0.1, 0.03])] + blocks[1:]
    text, record, _ = edge_page(blocks, b_text + "\n" + folio)
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[table.end:]) == unit and pp.CJK(text).count(folio) == 1, text
    [change] = edge_changes(record)
    assert [h["text"] for h in change["evidence"]["beside"][0]["heldElsewhere"]] == [foot, folio], change
    print("tables: the text beside a table is judged in the lines engine B read it in")


def check_table_edge_text_cell_read_short() -> None:
    """What engine B read on one line or in one cell with the table's first
    (last) cell is that cell's, not text beside the table (table_places).
    Found in the round-3 review of the table-edge rule, on made-up pages
    (these): engine A read the first cell 目 and engine B 項目, and 項 stood
    on a line of its own above the table; engine A read the last figure a
    digit short, and the digit stood below it; where the table is the first
    (last) block, a first cell engine A read 頂 for 項目名稱, or a last cell
    it read 另如 for 另加五成照舊, was split, 稱 (另加) kept in the cell and
    the rest set beside the table - the table restated against the whole
    page had kept those cells whole."""
    top, foot = EDGE_BOX["top"], [0.1, 0.92, 0.8, 0.05]
    for prefer in ("A", "B"):
        a_grid = [["目", "甲年"]] + EDGE_GRID[1:]
        text, record, _ = edge_page([("text", EDGE_PROSE, top), ("table", html(a_grid), EDGE_BOX["table"])],
                                    EDGE_PROSE + "\n" + html(EDGE_GRID), prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == EDGE_GRID and pp.CJK(text[:table.start]) == EDGE_PROSE, (prefer, text)
        assert not edge_doubts(record) and not edge_changes(record), (record["doubts"], record["decisionChanges"])
        a_grid = EDGE_GRID[:-1] + [["關稅", "四五〇"]]
        text, record, _ = edge_page([("text", EDGE_PROSE, top), ("table", html(a_grid), EDGE_BOX["table"])],
                                    EDGE_PROSE + "\n" + html(EDGE_GRID), prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == EDGE_GRID and not pp.CJK(text[table.end:]), (prefer, text)
    printed = [["項目", "甲年", "附註"], ["田賦", "一二〇〇", "照舊"], ["關稅", "四五〇〇", "另加五成照舊"]]
    a_grid = printed[:-1] + [["關稅", "四五〇〇", "另如"]]
    first = [["項目名稱", "甲年"]] + EDGE_GRID[1:]
    for prefer in ("B", "adjudicate"):
        text, record, _ = edge_page([("text", EDGE_PROSE, top), ("table", html(a_grid), EDGE_BOX["table"])],
                                    EDGE_PROSE + "\n" + html(printed), prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == printed and not pp.CJK(text[table.end:]), (prefer, text)
        assert not edge_changes(record) and not edge_doubts(record), record["decisionChanges"]
        text, record, _ = edge_page([("table", html([["頂", "甲年"]] + EDGE_GRID[1:]), EDGE_BOX["table"]),
                                     ("text", EDGE_TAIL, foot)], html(first) + "\n" + EDGE_TAIL, prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == first and not pp.CJK(text[:table.start]), (prefer, text)
    # Engine B's reading on one line from the prose on: its lines say nothing
    # of where the table begins, and 項 stays beside the table.
    a_grid = [["目", "甲年"]] + EDGE_GRID[1:]
    text, record, _ = edge_page([("text", EDGE_PROSE, top), ("table", html(a_grid), EDGE_BOX["table"])],
                                EDGE_PROSE + "".join(pp.CJK(html(EDGE_GRID))))
    [table] = pp._tables.find_tables(text)
    assert table.grid() == a_grid and pp.CJK(text[:table.start]) == EDGE_PROSE + "項", text
    # Nor on a page formatted whole (no block spans): the table is restated
    # against the whole page, and that line would take the prose into its
    # first cell.
    text, record, client = edge_page([("text", EDGE_PROSE, top), ("table", html(a_grid), EDGE_BOX["table"])],
                                     EDGE_PROSE + "".join(pp.CJK(html(EDGE_GRID))), unplaced="某某")
    prompt = next(u for kind, u in client.calls if kind == "format")
    assert "| 目 | 甲年 |" in prompt and "冬藏項目 |" not in prompt, prompt
    print("tables: what engine B read on one line with the table's first or last cell stays in that cell")


def check_table_edge_text_read_in_cells() -> None:
    """The doubt names what of the text beside a table engine B read in its
    own table's cells (engine_b_table_lines).  Found in the round-3 review
    of the table-edge rule, on a made-up page (this one): engine A misread
    the table's first three characters (頂自 | 申年 for 項目 | 甲年) and
    engine B read a title above the table; the merge kept both readings of
    that replacement (more than INSERTION_LIMIT on each side, too uneven to
    pair), so engine B's reading of the first cells was written above the
    table with the title, and the doubt said only "written" - as a prose
    block keeps both readings, but nothing said the text beside the table
    was engine B's reading of the table's own cells."""
    blocks = [("text", EDGE_PROSE, EDGE_BOX["top"]), ("table", html([["頂自", "申年"]] + EDGE_GRID[1:]),
                                                       EDGE_BOX["table"])]
    text, record, _ = edge_page(blocks, EDGE_PROSE + "\n" + EDGE_TITLE + "\n" + html(EDGE_GRID))
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[:table.start]) == EDGE_PROSE + EDGE_TITLE + "項目甲", text
    [doubt] = edge_doubts(record)
    assert "engine B read 「項目甲」 of it in its own table's cells" in doubt, doubt
    [change] = edge_changes(record)
    assert change["evidence"]["beside"][0]["inEngineBTable"] == "項目甲", change
    # A title engine B read outside its table: nothing to say.
    text, record, _ = edge_page(EDGE_BLOCKS, EDGE_PROSE + "\n" + EDGE_TITLE + "\n" + html(EDGE_GRID))
    [doubt] = edge_doubts(record)
    assert "own table's cells" not in doubt and "inEngineBTable" not in edge_changes(record)[0]["evidence"]["beside"][0]
    print("tables: the doubt names what of the text beside a table engine B read in its own table's cells")


def check_table_edge_text_whole_block() -> None:
    """A piece of edge text that is the whole of a block engine A alone read
    elsewhere is not written, figures and all (block_copy).  Found in the
    round-3 review of the table-edge rule, on made-up pages (these): engine A
    read the folio 十二 as its number block, engine B read it beside a table
    at the page's end or start; edge_copies never weighs a stretch of
    figures, so the folio was written twice (once before the rule).  A prose
    block there keeps it twice too: D2 never places a run of figures."""
    folio = "十二"

    def printed(user_text: str) -> str:
        a = re.search(r"甲\) (.*)", user_text).group(1).strip()
        b = re.search(r"乙\) (.*)", user_text).group(1).strip()
        read = b if b != "（冇字）" else a
        return f"構件：略\n定案：{'無' if read == '（冇字）' else read}\nRESEARCH: no"

    number_foot, number_top = ("number", folio, [0.45, 0.95, 0.1, 0.03]), ("number", folio, [0.45, 0.02, 0.1, 0.03])
    pages = [([number_foot, ("text", EDGE_PROSE, EDGE_BOX["top"]), ("table", html(EDGE_GRID), EDGE_BOX["table"])],
              EDGE_PROSE + "\n" + html(EDGE_GRID) + "\n" + folio),
             ([("table", html(EDGE_GRID), EDGE_BOX["table"]), ("text", EDGE_PROSE, EDGE_BOX["top"]), number_top],
              folio + "\n" + html(EDGE_GRID) + "\n" + EDGE_PROSE)]
    for blocks, b_text in pages:
        for prefer in ("A", "B", "adjudicate"):
            text, record, _ = edge_page(blocks, b_text, prefer, adjudicate=[printed])
            assert pp.CJK(text).count(folio) == 1, (prefer, text)
            [doubt] = edge_doubts(record)
            assert f"「{folio}」 not written - it is the whole of block" in doubt, doubt
            assert not edge_changes(record), record["decisionChanges"]
    # Engine B read the folio where engine A did too: the block is no
    # reading of engine A's alone, and what engine B read beside the table
    # is written, as before.
    blocks, b_text = pages[0]
    text, record, _ = edge_page(blocks, folio + "\n" + b_text, adjudicate=[printed])
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[table.end:]) == folio and pp.CJK(text).count(folio) == 2, text
    print("tables: edge text that is the whole of a block engine A alone read elsewhere is not written")


def check_table_edge_text_cells_again() -> None:
    """Engine B's second reading of the table's own cells beside the table
    is not written (cells_copy).  Found in the round-3 review of the
    table-edge rule, on a made-up page (this one): after a table that ends
    the page engine B read its last two rows again; the merge keeps a run
    only one engine read under every preference, edge_copies does not count
    it (engine B read it twice), and the rows were written again after the
    table - before the rule, dropped with the table's token."""
    again = "鹽稅二三〇〇關稅四五〇〇"
    for prefer in ("A", "B", "adjudicate"):
        text, record, _ = edge_page(EDGE_BLOCKS, EDGE_PROSE + "\n" + html(EDGE_GRID) + "\n" + again, prefer)
        [table] = pp._tables.find_tables(text)
        assert table.grid() == EDGE_GRID and not pp.CJK(text[table.end:]), (prefer, text)
        [doubt] = edge_doubts(record)
        assert f"「{again}」 not written - it stands whole in the table's own cells" in doubt, doubt
        assert not edge_changes(record), record["decisionChanges"]
    # A note that is more than the cells is written.
    note = again + "另計"
    text, record, _ = edge_page(EDGE_BLOCKS, EDGE_PROSE + "\n" + html(EDGE_GRID) + "\n" + note)
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[table.end:]) == note, text
    print("tables: engine B's second reading of a table's own cells beside it is not written")


def check_table_edge_text_substitution() -> None:
    """A table's first character engine A misread and the merge took engine
    B's: the alignment joins the text before the table to that character in
    one replacement, and the first cell took everything from the
    replacement's start - on a made-up page (this one) the title above the
    table and every character of the prose before it, which was then written
    twice.  The table is looked for in its own block's span, and a
    replacement at its edge longer than its characters there is split at the
    edge (table_places)."""
    prose, title, grid = EDGE_PROSE, EDGE_TITLE, EDGE_GRID
    misread = [["頂目", "甲年"]] + grid[1:]
    blocks = [EDGE_BLOCKS[0], ("table", html(misread), EDGE_BOX["table"])]
    for shown in (title, ""):
        b_text = prose + "\n" + shown + "\n" + html(grid)
        text, record, _ = edge_page(blocks, b_text, "B")
        [table] = pp._tables.find_tables(text)
        assert table.grid() == grid and pp.CJK(text).count(prose) == 1, (shown, text)
        assert pp.CJK(text[:table.start]) == prose + shown, (shown, text)
        assert bool(edge_changes(record)) == bool(shown), record["decisionChanges"]
        assert bool(edge_doubts(record)) == bool(shown), record["doubts"]
    # A page formatted whole (engine A text in no block: no spans) has no
    # token: the title is in the merged text the formatter is given, as
    # before, and the table it is shown no longer holds the prose.
    text, record, client = edge_page(blocks, prose + "\n" + title + "\n" + html(grid), "B", unplaced="某某")
    prompt = next(u for kind, u in client.calls if kind == "format")
    assert title in pp.CJK(between(prompt)) and "| 項目 | 甲年 |" in prompt, prompt
    print("tables: a replacement at a table's edge is split there, and the table stays in its block's span")


def check_table_edge_text_unchanged() -> None:
    """A page whose table blocks hold nothing beside their tables has no
    table-edge-text change or doubt."""
    prose, grid, box = EDGE_PROSE, EDGE_GRID, EDGE_BOX
    pages = [
        ([("text", prose, box["top"]), ("table", html(grid), box["table"]), ("text", EDGE_TAIL, box["foot"])],
         prose + "\n" + html(grid) + "\n" + EDGE_TAIL),
        ([("table", html(grid), box["table"]), ("text", EDGE_TAIL, box["foot"])], html(grid) + "\n" + EDGE_TAIL),
        # A cell the engines read differently, inside the table.
        ([("text", prose, box["top"]), ("table", html([grid[0], ["田賊", "一二〇〇"]] + grid[2:]), box["table"])],
         prose + "\n" + html(grid)),
    ]
    for blocks, b_text in pages:
        for prefer in ("A", "B", "adjudicate"):
            new, record, _ = edge_page(blocks, b_text, prefer)
            assert pp._tables.find_tables(new), new
            assert not edge_changes(record) and not edge_doubts(record)
    print("tables: a page whose table blocks hold nothing beside their tables has nothing beside them")


def check_table_edge_text_rebuilt() -> None:
    """A rebuilt table's token stands for its region's whole span too: what
    of the span the rebuilt table does not hold, from the span's first
    character on or back from its last, is written beside it (table_edges).
    On a made-up page (this one): a title only engine B read above a block
    nominated as a missed table went into the rebuild's lock; the rebuild
    left it out of the table, and the page lost it, with only the
    rebuilt-unvouched doubt naming its characters."""
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    title = "各種讀本用法一覽表"

    def without_title(user_text: str) -> str:
        chars = "".join(c for c in between(user_text) if pp.CJK(c)).replace(title, "")
        return f"| {chars[:len(chars) // 2]} |\n| --- |\n| {chars[len(chars) // 2:]} |"

    blocks = [("text", prose, BOX["title"]), ("vertical_text", first + second, BOX["table"]),
              ("text", prose[::-1], BOX["prose"])]
    b_text = prose + title + second + first + prose[::-1]
    client = page_client(format=[between], **{"table-rebuild": [without_title]})
    text, record = run_page(blocks, b_text, client)
    assert record["tableNominations"][0]["outcome"].startswith("accepted"), record["tableNominations"]
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text).count(first) == 1 and pp.CJK(text).count(second) == 1, text
    assert pp.CJK(text[:table.start]).endswith(prose + title), text
    [change] = edge_changes(record)
    assert change["evidence"]["source"] == "rebuilt" and change["evidence"]["beside"][0]["written"] == title
    print("tables: what a rebuilt table leaves out of its region's span at its edges is written beside it")


def check_table_edge_text_rebuilt_places() -> None:
    """What of a rebuilt table's region its edge text is, is found by the
    places of the region's characters, not by counting them
    (rebuilt_edges).  Found in the round-3 review of the table-edge rule,
    on made-up pages (these): the walk from the region's first character
    went on while the region held that character more often than the
    table - so a title engine B alone read (各種讀本年用法一覽表) was cut at
    年, where the rebuild wrote engine A's 年 for the merge's 季 (the D20
    lock allows it), and 年用法一覽表 was lost while the doubt said
    各種讀本 was written; the region's first character, 弟 as the merge wrote
    it, which the rebuild wrote as engine B's 第, stood again on a line of
    its own; and a character engine B read once more in the middle of the
    first row (第, or 次), which the rebuild left out, was taken for the
    region's first (last) character, which the table's first (last) cell
    holds, and written twice.  And what engine A alone read at the region's
    edge and the rebuild left out is no text beside the table: it is engine
    A's reading of the block the table stands for (found on the benchmark,
    replayed offline with a stand-in rebuild)."""
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    rows = ["第一種讀本每季可供一小時", "第二種讀本每頁可習寫三日", "第三種讀本每冊可用半學年"]
    title = "各種讀本年用法一覽表"

    def table_of(chars: str) -> str:
        return f"| {chars[:len(chars) // 2]} |\n| --- |\n| {chars[len(chars) // 2:]} |"

    # The title, and the rebuild writes engine A's 年 where the merge wrote 季.
    a_rows = [rows[0].replace("季", "年")] + rows[1:]
    blocks = [("text", prose, BOX["title"]), ("vertical_text", "".join(a_rows), BOX["table"]),
              ("text", prose[::-1], BOX["prose"])]
    client = page_client(format=[between], **{"table-rebuild": [table_of("".join(a_rows))]})
    text, record = run_page(blocks, prose + title + rows[0] + rows[2] + rows[1] + prose[::-1], client, prefer="B")
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[:table.start]) == prose + title, text
    [change] = edge_changes(record)
    assert change["evidence"]["beside"][0]["written"] == title, change

    # The merge wrote engine A's 弟 for the region's first character; the
    # rebuild writes engine B's 第.
    blocks = [blocks[0], ("vertical_text", "弟" + "".join(rows)[1:], BOX["table"]), blocks[2]]
    client = page_client(format=[between], **{"table-rebuild": [table_of("".join(rows))]})
    text, record = run_page(blocks, prose + rows[0] + rows[2] + rows[1] + prose[::-1], client)
    [table] = pp._tables.find_tables(text)
    assert pp.CJK(text[:table.start]) == prose and not edge_changes(record) and not edge_doubts(record), text

    # Engine B read the first row with one character more in its middle -
    # the region's first character, or its last - and the rebuild left it out.
    first, second = "第一種讀本每課可供一小時並須溫習半點鐘", "第二種讀本每頁可習寫三日並須默書一次"
    blocks = [blocks[0], ("vertical_text", first + second, BOX["table"]), blocks[2]]
    for extra in ("第", "次"):
        for prefer in ("A", "B"):
            client = page_client(format=[between], **{"table-rebuild": [table_of(first + second)]})
            text, record = run_page(blocks, prose + second + first[:10] + extra + first[10:] + prose[::-1], client,
                                    prefer=prefer)
            [table] = pp._tables.find_tables(text)
            assert extra not in [line.strip() for line in text.splitlines()], (extra, prefer, text)
            assert not edge_changes(record) and not edge_doubts(record), (extra, prefer, record["doubts"])

    # Engine A alone read one character more at the block's end, which the
    # rebuild left out: engine A's reading of the table, not text after it.
    blocks = [blocks[0], ("vertical_text", first + second + "次", BOX["table"]), blocks[2]]
    client = page_client(format=[between], **{"table-rebuild": [table_of(first + second)]})
    text, record = run_page(blocks, prose + second + first + prose[::-1], client)
    assert "次" not in [line.strip() for line in text.splitlines()] and not edge_doubts(record), text
    assert any("(次)" in d["detail"] for d in record["doubts"] if d["kind"] == "table-rebuilt-unvouched"), \
        record["doubts"]
    print("tables: a rebuilt table's edge text is found by where the region's characters stand")


def check_rebuild_region_witness() -> None:
    """A neighbour is taken into a nominated block's region only on evidence
    that engine B read its text inside the region (region_witness), and a
    table that takes it in must hold that text.  The shape: a title column
    beside a table the layout called text, sharing its top edge.  Engine B
    skipped the title, or read it after the table, where the alignment set
    it against one of the table's rows - agreed on nothing either way, so
    the old rule took it in; the table was accepted without it, and the
    title left the page with nothing counting it lost."""
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    title = "學堂用書一覽"
    blocks = [("text", prose, [0.70, 0.10, 0.25, 0.80]),
              ("text", title, [0.63, 0.20, 0.05, 0.40]),
              ("vertical_text", first + second, [0.10, 0.20, 0.52, 0.70])]
    # The model writes the table only (REBUILD_SYSTEM: 淨係輸出個表).
    printed = f"| {first[:6]} | {first[6:]} |\n| --- | --- |\n| {second[:6]} | {second[6:]} |"

    # Skipped: no witness, not taken in; the table is the body's, the title text.
    client = page_client(format=[between], **{"table-rebuild": [printed]})
    text, record = run_page(blocks, prose + second + first, client)
    [nomination] = record["tableNominations"]
    assert nomination["outcome"].startswith("accepted") and "region" not in nomination, nomination
    assert "regionRefused" not in nomination and title in pp.CJK(text), (nomination, text)
    assert len([k for k, _ in client.calls if k.startswith("table-rebuild")]) == 1, client.calls
    assert not [c for c in record["decisionChanges"] if c["decision"] == "D22"]

    # Read after the table: taken in, and the table must hold it; this one
    # does not, so the region is refused and the body rebuilt alone.
    client = page_client(format=[between], **{"table-rebuild": [printed]})
    text, record = run_page(blocks, prose + second + first + title, client)
    [nomination] = record["tableNominations"]
    assert nomination["outcome"].startswith("accepted") and nomination["regionRefused"]["region"] == [1], nomination
    assert "characters both engines read here left out" in nomination["regionRefused"]["reason"], nomination
    assert title in pp.CJK(text) and pp._tables.find_tables(text), text

    # A table that does hold it stands for it: the title is written once, in
    # the table, and sealed as held.
    holding = f"| {title} | |\n| --- | --- |\n{printed.split(chr(10), 2)[0]}\n{printed.split(chr(10), 2)[2]}"
    client = page_client(format=[between], **{"table-rebuild": [holding]})
    text, record = run_page(blocks, prose + second + first + title, client)
    [nomination] = record["tableNominations"]
    assert nomination["outcome"].startswith("accepted") and nomination["region"] == [1], nomination
    assert pp.CJK(text).count(title) == 1 and not record["tableCharactersLost"], text
    [change] = [c for c in record["decisionChanges"] if c["decision"] == "D22"]
    assert change["evidence"]["held"] == [title], change
    print("rebuild region: a neighbour is taken in only on engine B's witness, and its table must hold it")


def check_rebuilt_orientation_and_brackets() -> None:
    """A table the formatter rebuilt is asked which way its records run, as a
    recovered one is (proposed decision D23, orient_table), turned by engine B's
    reading order or, with none, by asking which side holds the first record
    (turn_records); and the brackets engine A printed in the region are
    written back cell by cell (table_brackets).  The shapes measured on a
    textbook table's continuation page: the three accepted rebuilds wrote a
    field per row, two with the first record in the first column and one in
    the last, and none kept a bracket engine A had read."""
    # turn_records: engine B's order decides; a tie asks; no answer rotates.
    grid = ("| 甲種讀本 | 乙種讀本 |\n| --- | --- |\n| 二 | 一 |\n"
            "| 是書每課可供一小時之用 | 是書以切於日用爲主而設 |")
    text, how = pp.turn_records(grid, "某處是書每課可供一小時之用某某是書以切於日用爲主而設")
    assert pp.first_table(text).grid()[0][0] == "甲種讀本" and how.startswith("transposed"), (text, how)
    text, how = pp.turn_records(grid, "是書以切於日用爲主而設某某是書每課可供一小時之用")
    assert pp.first_table(text).grid()[0][0] == "乙種讀本" and how.startswith("rotated"), (text, how)
    text, how = pp.turn_records(grid, "", ask=lambda table: ("left", "FIRST: left"))
    assert pp.first_table(text).grid()[0][0] == "甲種讀本" and "asked" in how, how
    # No vote and no answer: kept, undecided - no default turn (decision 1;
    # the benchmark turns a table by its print, never by a guess).
    text, how = pp.turn_records(grid, "", ask=lambda table: (None, "(empty reply)"))
    assert text == grid and pp.orientation_undecided(how), how
    assert pp.FIRST_LINE.search("理由\nFIRST: Right\n").group(1) == "Right"

    # table_brackets: engine A's brackets back in the cells, by their context.
    table = "| 甲乙丙丁戊己 | 庚辛壬癸子丑寅 |\n| --- | --- |\n| 天地 | 玄黃 |"
    fixed, done = pp.table_brackets(table, ["<table><tr><td>甲乙（丙丁）戊己</td><td>庚辛壬癸（子丑寅）</td>"
                                            "</tr></table>", "甲乙（丙丁）戊己"])
    assert pp.first_table(fixed).grid()[0] == ["甲乙（丙丁）戊己", "庚辛壬癸（子丑寅）"], fixed
    assert done == {"inserted": 4, "moved": 0, "unplaced": []}, done
    fixed, done = pp.table_brackets(table, ["青赤（黃白）"])
    assert fixed == table and done["unplaced"] == ["青赤（黃白", "青赤黃白）"], done

    # Through a page: a window-read table the formatter wrote a field per row.
    x, y, z = "甲乙丙丁戊己庚辛", "壬癸子丑寅卯辰巳", "青赤黃白黑紫綠藍"
    p1 = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲"
    p2 = "律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
    # A vertical page: the text blocks are columns, read right to left.
    blocks = [("vertical_text", p1, [0.8, 0.05, 0.1, 0.8]),
              ("table", html([[x + "（" + y[:4] + "）" + y[4:]], [z]]), [0.3, 0.05, 0.4, 0.8]),
              ("vertical_text", p2, [0.1, 0.05, 0.1, 0.8])]
    b_text = "\n".join([p2, z, y, x, p1])
    fields = f"| {x} | {z} |\n| --- | --- |\n| {y} | |"

    def page(first: str, *switches: str) -> tuple[str, dict, list]:
        client = page_client(format=[between], orient=["RECORD: column"], **{
            "table-rebuild": [fields], "record-order": [f"FIRST: {first}"]})
        text, record = run_page(blocks, b_text, client, switches=switches)
        return text, record, [kind for kind, _ in client.calls]

    # The rebuild's physical grid on a vertical page: rotated by the
    # benchmark's rule, the page asked nothing (decision 8).
    text, record, kinds = page("left")
    table = pp._tables.find_tables(text)[0]
    assert "orient" not in kinds and "record-order" not in kinds, kinds
    assert [row[:2] for row in table.grid()] == [[z, ""], [x, "（壬癸子丑）寅卯辰巳"]], text
    assert record["tablesRebuilt"][0]["brackets"]["inserted"] == 2, record["tablesRebuilt"]
    change = [c for c in record["decisionChanges"] if c["decision"] == "D23"]
    assert len(change) == 1 and pp.first_table(change[0]["after"]).grid()[0][:2] == [z, ""], change
    assert record["tableOrientation"][0].startswith("block 1: rebuilt from the crop, rotated"), record
    assert "table-marks-unread" not in [d["kind"] for d in record["doubts"]], record["doubts"]
    print("rebuilt tables: asked which way records run, turned by reading order (D23); brackets written back")


def check_verdict_propagation() -> None:
    """A character only engine A read, which the page's verdicts judged a
    misread of engine A's at every site they were asked about, goes to 裁決
    too (decision D24, judged_misreads).  The shape measured on a textbook
    table's continuation page: 裁決 read engine A's 筍 as 算 wherever engine
    B had a rare codepoint, and the label cell 珠筍 in a band engine B never
    read kept engine A's misread."""
    # judged_misreads: every disputed site replaced, by one character.
    asked = [({"a": "筍", "b": "𮅕"}, {"resolved": "算", "resolved_from": "adjudicator"}),
             ({"a": "珠筍", "b": "珠𮅕"}, {"resolved": "珠算", "resolved_from": "adjudicator"}),
             ({"a": "一", "b": "二"}, {"resolved": "二", "resolved_from": "adjudicator"}),
             ({"a": "甲", "b": "乙"}, {"resolved": "丙", "resolved_from": "adjudicator"}),
             ({"a": "甲", "b": "丁"}, {"resolved": "甲", "resolved_from": "adjudicator"}),
             ({"a": "戊", "b": "己"}, {"resolved": "庚", "resolved_from": "adjudicator-invalid"})]
    assert pp.judged_misreads(asked) == {"筍": ("算", 2)}, pp.judged_misreads(asked)
    # A reading already put to 裁決 (an insertion question, or an answer the
    # deletion guard or D3's partial rule refused, keeping the engine's reading)
    # is not asked about again: the page would show a mix no verdict wrote.
    segs = [{"tag": "delete", "a": "珠筍", "b": "", "before": "增補教", "after": "時亦宜"},
            {"tag": "delete", "a": "筍時", "b": "", "before": "習題", "after": "教員"}]
    spots = pp.propagation_spots(segs, ["珠筍", "筍時"], set(), {1}, {"筍": ("算", 2)})
    assert [(i, p, sub["a"], sub["b"]) for i, p, sub in spots] == [(0, 1, "筍", "算")], spots
    assert len(pp.propagation_spots(segs, ["珠筍", "筍時"], set(), set(), {"筍": ("算", 2)})) == 2

    note = "是書每課可供一小時之用教員宜詳言其理令學者旣通珠筍之法又能明珠筍之理"
    rows = [[GRID_HEAD, "", "", ""], ["珠筍讀本", "三年", note, REPEAT + REPEAT], ["乙種讀本", "二年", NOTE_2, ""]]
    block = {"text": html(rows), "label": "table", "box": [0.1, 0.1, 0.8, 0.8]}
    b_lines = ("是書以切於日用為主每頁六字約可\n習寫一星期必極熟始習下頁\n" + note.replace("筍", "𮅕")[:16]
               + "\n" + note.replace("筍", "𮅕")[16:])
    furniture = frozenset({GRID_HEAD[:8]})

    def judge(user_text: str) -> str:
        import re
        first = re.search(r"甲\) (.*)", user_text).group(1).strip()
        second = re.search(r"乙\) (.*)", user_text).group(1).strip()
        read = ("算" if second == "𮅕" else second if "乙唔係第二個引擎嘅讀法" in user_text else first)
        return f"構件：略\n定案：{read}\nRESEARCH: no"

    client = page_client(format=[between], adjudicate=[judge], **{"table-rebuild": [split_table]})
    text, record = run_page([("table", block["text"], block["box"])], b_lines, client, furniture=furniture)
    asked = [a for a in record["adjudications"] if a.get("askedFor") == "D24"]
    assert len(asked) == 1 and asked[0]["draft_reading"] == "筍" and asked[0]["writer_reading"] == "算", \
        record["adjudications"]
    # No engine's disagreement: out of the engine tally the drift audit reads.
    assert asked[0]["trigger"] == "verdict-propagation" and "B" not in record["engineTally"], record["engineTally"]
    assert "珠算讀本" in pp.CJK(text) and "筍" not in text, text
    change = [c for c in record["decisionChanges"] if c["decision"] == "D24"]
    assert len(change) == 1 and (change[0]["engineA"], change[0]["after"]) == ("筍", "算"), change
    assert change[0]["evidence"]["judgedSites"] == 2 and not record["tableCharactersLost"], change
    assert record["tablesRebuilt"][0]["outcome"] == "accepted", record["tablesRebuilt"]
    print("propagation: a character only engine A read goes to 裁決 where the page judged it a misread (D24)")


def check_structure_pass_no_answer() -> None:
    """An empty structure answer, or one cut off at every effort, is a failed
    pass: asked again from a lower effort, and never sealed as "characters
    preserved" (no_answer).  The shape measured on a page whose engine grid
    was refused for line breaks in its cells: every rung of the effort ladder
    reasoned to the token budget, the answer was empty, it passed every
    check, and the page went out as flat text sealed "STRUCTURE PASS
    REJECTED: characters preserved"."""
    cut = {"degraded": True, "finish_reason": "length"}
    assert pp.no_answer("", cut) == "no answer: cut off at the token budget at every effort, nothing written"
    assert pp.no_answer("甲乙", cut).endswith("(2 character(s) written before the cut)")
    assert pp.no_answer(" \n", {}) == "no answer: the reply was empty" and pp.no_answer("甲", {}) is None
    # Degraded, but finished on its own with nothing written: no cut.
    assert pp.no_answer("\n", {"degraded": True, "finish_reason": "stop"}) == "no answer: the reply was empty"

    class Silent(PageClient):
        """The structure pass answers nothing, cut off (degraded), or with
        `finish` "stop" an empty reply that finished on its own; a retry
        answers the page; records the rung each call began on."""

        def __init__(self, retry: str, *args, finish: str = "length", **kwargs):
            super().__init__(*args, **kwargs)
            self.retry, self.starts, self.finish = retry, [], finish

        def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other", start=""):
            self.starts.append((kind, start))
            if kind == "format":
                self.calls.append((kind, user_text))
                return "", {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                            "degraded": True, "finish_reason": self.finish}
            if kind == "format-retry":
                self.calls.append((kind, user_text))
                return self.retry.replace("{text}", between(user_text)), {
                    "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                    **({"degraded": True, "finish_reason": self.finish} if not self.retry else {})}
            return super().ask_answering(system, user_text, image_bytes, max_tokens, kind)

    # A table the engine grid could not give (a cell holding a line break):
    # the structure pass must write it.
    grid = "<table><tr><td>物名</td><td>件數</td></tr><tr><td>黑板</td><td>二\\n張</td></tr></table>"
    blocks = [("text", PROSE, BOX["prose"]), ("table", grid, BOX["table"])]
    b_text = PROSE + "\n物名件數黑板二張"
    client = Silent("{text}", {"orient": ["RECORD: row"]})
    text, record = run_page(blocks, b_text, client)
    assert ("format-retry", pp.NO_ANSWER_RETRY_EFFORT) in client.starts, client.starts
    assert record["formatterRetryReason"] == "no answer: cut off at the token budget at every effort, nothing written"
    assert "structure-rejected" not in [d["kind"] for d in record["doubts"]] and PROSE in pp.CJK(text), record
    retry_prompt = [u for kind, u in client.calls if kind == "format-retry"][0]
    assert "冇寫出答案" in retry_prompt and "被拒絕" not in retry_prompt, retry_prompt

    # An empty reply that finished on its own is no cut: asked again at the
    # client's own effort, told the reply was empty - not that it thought
    # too long - and sealed so.
    client = Silent("{text}", {"orient": ["RECORD: row"]}, finish="stop")
    text, record = run_page(blocks, b_text, client)
    assert ("format-retry", "") in client.starts, client.starts
    assert record["formatterRetryReason"] == "no answer: the reply was empty", record["formatterRetryReason"]
    retry_prompt = [u for kind, u in client.calls if kind == "format-retry"][0]
    assert "空白答案" in retry_prompt and "諗得太耐" not in retry_prompt, retry_prompt
    assert "structure-rejected" not in [d["kind"] for d in record["doubts"]] and PROSE in pp.CJK(text), record

    # The retry answers nothing either: the page's blocks, sealed for what
    # happened - not "characters preserved".
    client = Silent("", {"orient": ["RECORD: row"]})
    text, record = run_page(blocks, b_text, client)
    rejected = [d["detail"] for d in record["doubts"] if d["kind"] == "structure-rejected"]
    assert rejected and "no answer" in rejected[0] and "characters preserved" not in record["auditorReport"], rejected
    assert PROSE in pp.CJK(text), text

    # A cut-off answer that also swapped one glyph is still no answer: the
    # variant-swap repair must not turn it into an accepted page and lose
    # what came after the cut (five characters: no deleted run).
    class CutOff(PageClient):
        def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other", start=""):
            text, m = super().ask_answering(system, user_text, image_bytes, max_tokens, kind)
            if kind == "format":
                m = dict(m, degraded=True, finish_reason="length")
            return text, m

    prose = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲律呂調陽內外雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
    client = CutOff({"orient": ["RECORD: row"], "format": [prose.replace("內", "内")[:-5]], "format-retry": [prose]})
    text, record = run_page([("text", prose, [0.1, 0.1, 0.8, 0.8])], prose, client)
    assert "format-retry" in [kind for kind, _ in client.calls], client.calls
    assert record["formatterRetryReason"].startswith("no answer: cut off"), record["formatterRetryReason"]
    assert pp.CJK(text) == prose and "structure-rejected" not in [d["kind"] for d in record["doubts"]], record
    print("structure pass: an empty or cut-off answer is a failure, asked again from a lower effort")


def check_union_outside_in_engine_b_order() -> None:
    """settle_by_union's `elsewhere` is engine B's reading outside the
    nominated block, in engine B's order.  When D2 (place_moved_once) has put
    a run engine B read first at engine A's site after the block, the
    segments' engine-B text is no longer in engine B's order; cut at engine B
    positions it took the head of the block's own reading for text read
    elsewhere (the merge commit), which keeps engine A's copy of it out of the
    union's pool."""
    before, block, moved = "甲乙丙丁", "戊己庚辛壬癸子丑", "寅卯辰巳"
    segments = [{"tag": "equal", "a": before, "b": before, "bStart": 4},
                {"tag": "equal", "a": block, "b": block, "bStart": 8},
                {"tag": "equal", "a": moved, "b": moved, "bStart": 0, "movedFrom": {}}]
    starts, b_starts = [0, 4, 12], [4, 8, 0]
    outside = pp.engine_b_outside(segments, starts, b_starts, 4, 12)
    assert outside == moved + before + "\0", outside
    # Without a move it is the segments' text with the block's stretch cut
    # out, as the union always took it; a disagreement of engine A's alone
    # inside the block marks its place.
    plain = [{"tag": "equal", "a": before, "b": before}, {"tag": "delete", "a": "戊己", "b": ""},
             {"tag": "equal", "a": "庚辛", "b": "庚辛"}, {"tag": "equal", "a": moved, "b": moved}]
    assert pp.engine_b_outside(plain, [0, 4, 6, 8], [0, 4, 4, 6], 4, 8) == before + "\0" + moved
    assert pp.engine_b_outside(plain[:2] + plain[3:], [0, 4, 6], [0, 4, 4], 4, 6) == before + "\0" + moved
    # Engine B's reading of a set-aside looped block at the block's edge is
    # that block's, so it stays outside this one.
    looped = [{"tag": "equal", "a": before, "b": before},
              {"tag": "insert", "a": "", "b": "青赤黃白", pp.LOOPED_READING: True},
              {"tag": "equal", "a": block, "b": block}]
    assert pp.engine_b_outside(looped, [0, 4, 4], [0, 4, 8], 4, 12) == before + "青赤黃白" + "\0"
    print("union: engine B's reading outside a nominated block is taken in engine B's order")


def check_window_block_spans() -> None:
    """An order-conflict page that keeps engine A's reading is split into its
    blocks (block_spans), and a table block of it read from engine B's lines
    (window_merge) has its segments in engine B's order.  Text only engine B
    read at the end of the last window stands after the block's last
    character in engine A's stream; it is the table block's, not the next
    block's (on the merged code before this, the next paragraph began with it
    and the rebuild was locked to the block without it)."""
    x, y, z = "甲乙丙丁戊己庚辛", "壬癸子丑寅卯辰巳", "青赤黃白黑紫綠藍"
    tail = "午未申酉"
    p1 = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲"
    p2 = "律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
    blocks = [("table", html([[x + y], [z]]), [0.1, 0.1, 0.8, 0.3]),
              ("text", p1, [0.1, 0.45, 0.8, 0.2]), ("text", p2, [0.1, 0.7, 0.8, 0.2])]
    b_text = "\n".join([p2, z, y, x + tail, p1])
    client = page_client(format=[between], **{"table-rebuild": [split_table]})
    text, record = run_page(blocks, b_text, client)
    assert record["mode"] == "merge-order-conflict" and record["punctuationPasses"], record["mode"]
    rebuilt = record["tablesRebuilt"][0]
    assert rebuilt["outcome"] == "accepted" and rebuilt["locked"] == 28, rebuilt
    table = pp._tables.find_tables(text)[0]
    assert tail in pp.CJK(text[table.start:table.end]), text
    assert pp.CJK(text[table.end:]).startswith(p1) and pp.CJK(text).count(tail) == 1, text

    # Asked of 裁決 (no preference), the same text is judged on the table
    # block's crop: its place in engine A's stream is the block's end, which
    # named the next block, and the boundary crop for text between two blocks
    # (D3) showed the table's foot beside the paragraph's head.
    cropped: list[str] = []
    real = pp.segment_crop_kind

    def spy(render, render_bytes, block, offset, seg):
        cropped.append(f"{seg['b']}@{(block or {}).get('label')}")
        return real(render, render_bytes, block, offset, seg)

    pp.segment_crop_kind = spy
    try:
        client = page_client(format=[between], **{"table-rebuild": [split_table]})
        text, record = run_page(blocks, b_text, client, prefer="adjudicate")
    finally:
        pp.segment_crop_kind = real
    asked = [a for a in record["adjudications"] if a.get("writer_reading") == tail]
    assert asked and not asked[0].get("boundaryCrop"), record["adjudications"]
    assert f"{tail}@table" in cropped, cropped
    print("order conflict, per block: a window's text only engine B read stays in its table block")


def check_window_table_marks_unread() -> None:
    """A table block gets no punctuation pass, and the structure pass may add
    no mark: when a window-read table's rebuild is refused, nothing reads the
    marks printed in it.  Measured on a textbook table whose printed marks are
    all brackets: the page used to go whole (the formatter punctuated it, and
    the page was sealed punctuation-whole-page); per block it was sealed with
    only table-rebuild-refused.  It is now sealed table-marks-unread, with the
    marks engine A read there.  A rebuilt table, and a nominated block (which
    keeps its own label and its pass), are not."""
    x, y, z = "甲乙丙丁戊己庚辛", "壬癸子丑寅卯辰巳", "青赤黃白黑紫綠藍"
    p1 = "天地玄黃宇宙洪荒日月盈昃辰宿列張寒來暑往秋收冬藏閏餘成歲"
    p2 = "律呂調陽雲騰致雨露結爲霜金生麗水玉出崑岡劍號巨闕珠稱夜光"
    blocks = [("text", p1, [0.1, 0.05, 0.8, 0.2]),
              ("table", html([[x + "（" + y[:4] + "）" + y[4:]], [z]]), [0.1, 0.3, 0.8, 0.3]),
              ("text", p2, [0.1, 0.7, 0.8, 0.2])]
    b_text = "\n".join([p2, z, y, x, p1])
    invented = "| 甲乙丙丁某 |\n| --- |\n| 青赤黃白 |"

    def unread(record: dict) -> list[str]:
        return [d["detail"] for d in record["doubts"] if d["kind"] == "table-marks-unread"]

    client = page_client(format=[between], **{"table-rebuild": [invented], "table-rebuild-retry": [invented]})
    text, record = run_page(blocks, b_text, client)
    assert record["mode"] == "merge-order-conflict" and record["punctuationPasses"], record["mode"]
    assert record["tablesRebuilt"][0]["outcome"] == "refused", record["tablesRebuilt"]
    assert "table-rebuild-refused" in [d["kind"] for d in record["doubts"]], record["doubts"]
    details = unread(record)
    assert len(details) == 1 and "block 1 " in details[0], record["doubts"]
    assert "engine A read 2 mark(s) in it (（×1）×1)" in details[0], details
    assert 1 not in [p["block"] for p in record["punctuationPasses"]], record["punctuationPasses"]

    # The model on the crop says it is no table: still no step reads its marks.
    client = page_client(format=[between], **{"table-rebuild": ["唔係表格"]})
    text, record = run_page(blocks, b_text, client)
    assert "table-rebuild-refused" not in [d["kind"] for d in record["doubts"]], record["doubts"]
    assert len(unread(record)) == 1 and "not a table" in unread(record)[0], record["doubts"]

    # Rebuilt: the table is the rebuild's (sealed table-rebuilt-unvouched).
    client = page_client(format=[between], **{"table-rebuild": [split_table]})
    text, record = run_page(blocks, b_text, client)
    assert record["tablesRebuilt"][0]["outcome"] == "accepted" and not unread(record), record["doubts"]

    # A nominated block whose rebuild is refused is punctuated as the text it is.
    first, second = "第一種讀本每課可供一小時", "第二種讀本每頁可習寫三日"
    prose = "右表所列各本均係本年新頒之書其用法詳見各本卷首凡例之中此處不贅"
    nominated = [("text", prose, BOX["title"]), ("vertical_text", first + second, BOX["table"]),
                 ("text", prose[::-1], BOX["prose"])]
    client = page_client(format=[between], **{"table-rebuild": [invented], "table-rebuild-retry": [invented]})
    text, record = run_page(nominated, prose + second + first + prose[::-1], client)
    assert record["tableNominations"][0]["outcome"].startswith("refused"), record["tableNominations"]
    assert 1 in [p["block"] for p in record["punctuationPasses"]] and not unread(record), record["doubts"]
    print("order conflict, per block: a window-read table whose rebuild is refused is sealed "
          "table-marks-unread")


if __name__ == "__main__":
    check_single_line_orientation()
    check_figure_grouping()
    check_vouched_grid()
    check_tables_bypass_the_formatter()
    check_joined_tables_split()
    check_cell_direction()
    check_whole_page_turned_labels()
    check_no_corpus_strings_in_prompts()
    check_rebuild_checks()
    check_union_and_nomination()
    check_order_conflict_table_from_b()
    check_window_line_ends_and_copies()
    check_nominated_block_rebuilt()
    check_rebuild_lock_both_readings()
    check_witness_marks_left_out()
    check_rebuild_region()
    check_rebuild_region_refused()
    check_text_beside_rebuilt_table_kept()
    check_text_beside_after_answer_nearby()
    check_unread_table_characters()
    check_table_edge_text()
    check_table_edge_text_trailing()
    check_table_edge_text_marks_in_order()
    check_table_edge_text_read_by_a()
    check_table_edge_text_misread_elsewhere()
    check_table_edge_text_pieces()
    check_table_edge_text_cell_read_short()
    check_table_edge_text_read_in_cells()
    check_table_edge_text_whole_block()
    check_table_edge_text_cells_again()
    check_table_edge_text_substitution()
    check_table_edge_text_unchanged()
    check_table_edge_text_rebuilt()
    check_table_edge_text_rebuilt_places()
    check_rebuild_region_witness()
    check_rebuilt_orientation_and_brackets()
    check_verdict_propagation()
    check_structure_pass_no_answer()
    check_union_outside_in_engine_b_order()
    check_window_block_spans()
    check_window_table_marks_unread()
    print("all table checks passed")
