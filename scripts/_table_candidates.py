"""Conservative table nominations from the other engine, never layout authority."""
from __future__ import annotations

from collections import Counter
import html
import re
from typing import Any, Mapping, Sequence

import _tables


def characters(text: str) -> str:
    """Visible cell text, including numeric signs, decimals and units.

    Strip table syntax, never punctuation from a figure: 12.5 and 125 are
    different readings. Plain OCR blocks have no table syntax to strip.
    """
    tables = _tables.find_tables(text)
    for table in reversed(tables):
        text = (text[:table.start] + " ".join([table.caption, *(c.text for c in table.cells)])
                + text[table.end:])
    visible = html.unescape(re.sub(r"<[^>]*>", "", text))
    return "".join(c for c in visible if not c.isspace())


def candidates(blocks: Sequence[Mapping[str, Any]], witness: str) -> list[dict[str, Any]]:
    """Map each valid B table to unambiguous, complete, contiguous A blocks.

    A repeated cell, partial block, missing cell or overlapping candidate gets
    the full-page image route. This deliberately makes no geometric guess and
    never grants a table/furniture label merely because B emitted markup.
    """
    texts = [characters(str(block.get("text") or "")) for block in blocks]
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for number, table in enumerate(_tables.find_tables(witness), 1):
        if table.problems or table.rows < 2 or table.cols < 2:
            continue
        cells = [characters(text) for text in [table.caption, *(cell.text for cell in table.cells)]
                 if characters(text)]
        if len(cells) < 2:
            continue
        record: dict[str, Any] = {"table": number, "format": table.kind,
                                  "signal": "engine B explicit table markup",
                                  "text": _tables.render(table), "route": "full-page",
                                  "reason": "table cells do not map uniquely to complete engine A blocks"}
        region: set[int] = set()
        reliable = True
        for cell in cells:
            hits = [(i, text.count(cell)) for i, text in enumerate(texts) if cell in text]
            if sum(count for _, count in hits) != 1:
                reliable = False
                break
            region.add(hits[0][0])
        ordered = sorted(region)
        if (reliable and ordered and not region & used
                and ordered == list(range(ordered[0], ordered[-1] + 1))
                and Counter("".join(texts[i] for i in ordered)) == Counter("".join(cells))):
            boxes = [blocks[i].get("box") for i in ordered]
            if all(isinstance(b, (list, tuple)) and len(b) == 4
                   and all(isinstance(v, (int, float)) for v in b) and b[2] > 0 and b[3] > 0 for b in boxes):
                x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
                record.update(route="union-crop", blocks=ordered,
                              box=[x, y, max(b[0] + b[2] for b in boxes) - x,
                                   max(b[1] + b[3] for b in boxes) - y],
                              reason="all cells map uniquely to complete contiguous blocks")
                used.update(region)
        out.append(record)
    return out


def fallback_hint(records: Sequence[Mapping[str, Any]]) -> str:
    pending = [r for r in records if r.get("route") == "full-page" or r.get("fallback")]
    if not pending:
        return ""
    return (f"\n另一個 OCR 引擎標咗 {len(pending)} 個疑似表格，但冇可靠嘅局部格網。"
            "請對住全頁圖確認有冇表格，包括純數字表；有就按頁圖重建行、欄同合併格。"
            "下面係未核實嘅 OCR 提示，唔係抄寫權威；漢字仍然只可搬動已定嘅字，唔准加字、刪字或改字。"
            "提示入面嘅數字、正負號、小數點同單位要逐格對全頁圖確認，唔好因為漢字串冇數字就漏咗佢。\n"
            + "\n".join(str(r.get("text") or "") for r in pending) + "\n")
