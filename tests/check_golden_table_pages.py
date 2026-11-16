"""Checks for a golden_table over several pages ("pages": [first, last]): the output
passes only with the whole merged table on one page; a piece on each page, or
markup left open on one page, does not pass as merged.  And for text assertions
over several pages: the range is read as one text.

Usage: python3 tests/check_golden_table_pages.py   (exits non-zero on the first failure)
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_regression as rr  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


ROWS = [("地丁", "一〇五"), ("雜稅", "二三"), ("關稅", "八〇"), ("鹽課", "四五")]


def table(rows) -> str:
    return "| 項 | 數 |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in rows)


def html(rows, close=True) -> str:
    body = "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows)
    return f"<table><tr><td>項</td><td>數</td></tr>{body}" + ("</table>" if close else "")


ASSERTION = {"type": "golden_table", "page": 24, "pages": [24, 25], "value": "_check_table_pages.md"}
with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / ASSERTION["value"]
    golden.write_text(table(ROWS), encoding="utf-8")
    try:
        for name, p24, p25, want in (
                ("the merged table on the first page", table(ROWS), "（續前表）", "pass"),
                ("the merged table on the last page", "表見次頁", table(ROWS), "pass"),
                ("a piece on each page", table(ROWS[:2]), table(ROWS[2:]), "fail"),
                ("an HTML table left open on one page and closed on the next",
                 html(ROWS[:2], close=False), "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in ROWS[2:]) + "</table>",
                 "fail")):
            (Path(tmp) / "page-0024.md").write_text(p24, encoding="utf-8")
            (Path(tmp) / "page-0025.md").write_text(p25, encoding="utf-8")
            status, detail, _ = rr.check(ASSERTION, Path(tmp))
            check(f"{name}: {detail}", status, want)
        # An acceptable variant in a cell (the user's list: printed 𮅕, written 算) is a right cell.
        golden.write_text(table(ROWS + [("珠𮅕", "一")]), encoding="utf-8")
        (Path(tmp) / "page-0024.md").write_text(table(ROWS + [("珠算", "一")]), encoding="utf-8")
        (Path(tmp) / "page-0025.md").write_text("（續前表）", encoding="utf-8")
        status, detail, metrics = rr.check(ASSERTION, Path(tmp))
        check(f"an acceptable variant in a cell: {detail}", (status, metrics["cellsExact"]), ("pass", 12))
        golden.write_text(table(ROWS), encoding="utf-8")
        (Path(tmp) / "page-0025.md").unlink()
        status, detail, _ = rr.check(ASSERTION, Path(tmp))
        check(f"a page of the range missing: {detail}", status, "skip")
    finally:
        golden.unlink()

# A table printed in vertical columns is written a record per row, and scored in either
# orientation (the user's ruling, 2026-09-27): each table is compared as written and
# transposed, and the detail names the one scored.  A title row across the top stays a title
# when the grid under it is written the other way round.  No other turn is the same table.
RECORDS = """<table>
<tr><td colspan="3">價目</td></tr>
<tr><td>全年</td><td>一元</td><td rowspan="3">郵費在內</td></tr>
<tr><td>半年</td><td>六角</td></tr>
<tr><td>一册</td><td>五分</td></tr>
</table>"""
FIELDS = """<table>
<tr><td colspan="3">價目</td></tr>
<tr><td>全年</td><td>半年</td><td>一册</td></tr>
<tr><td>一元</td><td>六角</td><td>五分</td></tr>
<tr><td colspan="3">郵費在內</td></tr>
</table>"""
TURNED = """<table>
<tr><td>一册</td><td>半年</td><td>全年</td></tr>
<tr><td>五分</td><td>六角</td><td>一元</td></tr>
</table>"""
ONE = {"type": "golden_table", "page": 2, "value": "_check_table_turns.md"}
with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / ONE["value"]
    try:
        golden.write_text(RECORDS, encoding="utf-8")
        for name, written, want, how in (
                ("a record per row, as the golden", RECORDS, "pass", None),
                ("a column per row, title and note rows across it", FIELDS, "pass", "transposed under its title row"),
                ("a wrong cell in the other orientation still fails", FIELDS.replace("六角", "七角"), "fail",
                 "transposed under its title row")):
            (Path(tmp) / "page-0002.md").write_text(written, encoding="utf-8")
            status, detail, metrics = rr.check(ONE, Path(tmp))
            check(f"{name}: {detail}", (status, metrics["orientation"]), (want, how or "as written"))
        # The page score reads a table written in the other orientation in the golden's order.
        PAGE = {"type": "golden_page", "page": 2, "value": ONE["value"]}
        (Path(tmp) / "page-0002.md").write_text(FIELDS, encoding="utf-8")
        status, detail, metrics = rr.check(PAGE, Path(tmp))
        check(f"page score of the table in the other orientation: {detail}",
              (metrics["errors"], metrics.get("tablesTurned")), (0, ["p.2 transposed under its title row"]))
        (Path(tmp) / "page-0002.md").write_text(RECORDS, encoding="utf-8")
        status, detail, metrics = rr.check(PAGE, Path(tmp))
        check(f"... and as the golden writes it, nothing turned: {detail}",
              (metrics["errors"], metrics.get("tablesTurned")), (0, None))
        # Its columns written the other way round is not a turn the ruling accepts: the
        # table fails, as written (found in review: every turn and mirror of the grid was
        # tried, and a table the pipeline wrote the wrong way passed).
        golden.write_text("| 全年 | 半年 | 一册 |\n| --- | --- | --- |\n| 一元 | 六角 | 五分 |\n", encoding="utf-8")
        (Path(tmp) / "page-0002.md").write_text(TURNED, encoding="utf-8")
        status, detail, metrics = rr.check(ONE, Path(tmp))
        check(f"a grid written with its columns reversed fails: {detail}", (status, metrics["orientation"]),
              ("fail", "as written"))
        golden.write_text("| 年度 | 田賦 | 鹽稅 |\n| --- | --- | --- |\n| 十六年 | 一二〇 | 三四〇 |\n"
                          "| 十七年 | 五六〇 | 七八〇 |\n| 十八年 | 九一〇 | 二三〇 |\n", encoding="utf-8")
        rows = [["年度", "田賦", "鹽稅"], ["十六年", "一二〇", "三四〇"], ["十七年", "五六〇", "七八〇"],
                ["十八年", "九一〇", "二三〇"]]
        pipe = lambda grid: "".join("| " + " | ".join(row) + " |\n" + ("| --- | --- | --- |\n" if not n else "")
                                    for n, row in enumerate(grid))  # noqa: E731
        for name, grid in (("records in reverse order", rows[::-1]),
                           ("each record's fields reversed", [row[::-1] for row in rows]),
                           ("upside down", [row[::-1] for row in rows[::-1]]),
                           ("turned a quarter", [list(row) for row in zip(*rows)][::-1])):
            (Path(tmp) / "page-0002.md").write_text(pipe(grid), encoding="utf-8")
            status, detail, metrics = rr.check(ONE, Path(tmp))
            check(f"{name} fails: {detail}", status, "fail")
        (Path(tmp) / "page-0002.md").write_text(pipe([list(row) for row in zip(*rows)]), encoding="utf-8")
        status, detail, metrics = rr.check(ONE, Path(tmp))
        check(f"... and transposed passes: {detail}", (status, metrics["orientation"]), ("pass", "transposed"))
        # A turn is taken for what it does to the score, not for where its cells stand alone
        # (found in review): the output writes the golden's title row as a line above a table
        # in the golden's own order, so as written no cell stands at its place; transposed,
        # figures repeated down the table put six at theirs.  The page reads as written with
        # no error, and the table is scored as written, at its own CER.
        golden.write_text("前文。\n\n<table>\n<tr><td colspan=\"3\">價目</td></tr>\n"
                          "<tr><td>一〇</td><td>一〇</td><td>二〇</td></tr>\n"
                          "<tr><td>二〇</td><td>一〇</td><td>一〇</td></tr>\n"
                          "<tr><td>三〇</td><td>二〇</td><td>一〇</td></tr>\n</table>\n", encoding="utf-8")
        (Path(tmp) / "page-0002.md").write_text("前文。\n\n價目\n\n| 一〇 | 一〇 | 二〇 |\n| --- | --- | --- |\n"
                                               "| 二〇 | 一〇 | 一〇 |\n| 三〇 | 二〇 | 一〇 |\n", encoding="utf-8")
        status, detail, metrics = rr.check(PAGE, Path(tmp))
        check(f"a table in the golden's order under its title written as a line: {detail}",
              (metrics["errors"], metrics.get("tablesTurned")), (0, None))
        status, detail, metrics = rr.check(ONE, Path(tmp))
        check(f"... scored as written, not transposed for six cells at their places: {detail}",
              (metrics["orientation"], metrics["cellsExact"]), ("as written", 2))
        # Cells first: a turn that reads closer row by row but sets no cell where the golden has
        # it does not win over the table as written.
        golden.write_text("| 甲 | 乙 |\n| --- | --- |\n| 丙 | 丁 |\n", encoding="utf-8")
        (Path(tmp) / "page-0002.md").write_text("| 甲 | 乙 |\n| --- | --- |\n| 丙 | 戊 |\n", encoding="utf-8")
        status, detail, metrics = rr.check(ONE, Path(tmp))
        check(f"as written where no turn fits better: {detail}", (status, metrics["orientation"], metrics["cellsExact"]),
              ("fail", "as written", 3))
    finally:
        golden.unlink()

# Text in a table printed across pages is checked over the range: it may be written on any of them.
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / "page-0061.md").write_text("書名　敎授要言\n字（卽數碼）此種字", encoding="utf-8")
    (Path(tmp) / "page-0062.md").write_text("（續前表）", encoding="utf-8")
    for name, assertion, want in (
            ("contains_exact over pages finds text written on the first page",
             {"type": "contains_exact", "page": 62, "pages": [61, 62], "value": "字（卽數碼）此種字"}, "pass"),
            ("the same text checked on its printed page alone misses it",
             {"type": "contains_exact", "page": 62, "value": "字（卽數碼）此種字"}, "fail"),
            ("absent over pages sees every page of the range",
             {"type": "absent", "page": 62, "pages": [61, 62], "value": "卽數碼"}, "fail"),
            ("a page of the range missing is skipped",
             {"type": "contains", "page": 62, "pages": [61, 63], "value": "此種字"}, "skip")):
        status, detail, _ = rr.check(assertion, Path(tmp))
        check(f"{name}: {detail}", status, want)

# A ratchet (minCellsExact, top level or per --stage) lets a table that is not
# yet whole pass while it keeps the cells it had right.
SINGLE = {"type": "golden_table", "page": 2, "value": "_check_table_ratchet.md"}
with tempfile.TemporaryDirectory() as tmp:
    golden = rr.GOLDEN / SINGLE["value"]
    golden.write_text(table(ROWS), encoding="utf-8")
    (Path(tmp) / "page-0002.md").write_text(table(ROWS[:3] + [("鹽課", "四六")]), encoding="utf-8")
    try:
        for name, assertion, stage, want in (
                ("no ratchet: one wrong cell fails", SINGLE, None, "fail"),
                ("minCellsExact at the cells right passes", dict(SINGLE, minCellsExact=9), None, "pass"),
                ("a cell fewer than the ratchet fails", dict(SINGLE, minCellsExact=10), None, "fail"),
                ("the stage's block is the ratchet",
                 dict(SINGLE, ratchets={"repaired": {"minCellsExact": 9}}), "repaired", "pass"),
                ("no block for the stage: whole or fail",
                 dict(SINGLE, ratchets={"repaired": {"minCellsExact": 9}}), "phase3", "fail")):
            rr.STAGE = stage
            try:
                status, detail, _ = rr.check(assertion, Path(tmp))
            finally:
                rr.STAGE = None
            check(f"{name}: {detail}", status, want)
    finally:
        golden.unlink()

sys.exit(1 if failures else 0)
