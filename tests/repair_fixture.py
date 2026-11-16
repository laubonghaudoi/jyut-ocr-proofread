"""A made-up book for the book repair tool's offline checks (not a check).

make_spec() lays out a small vertical book - a title page, contents pages,
two runs of body pages each opened by a divider, a running head in the outer
column (right on odd scans, left on even) with the run's suffix and the folio
under it - as page specs: the sealed Markdown, engine A's blocks, engine B's
text and the glyph cells to draw.  A check changes the specs it needs to (a
running head left in the text, a stand-in glyph, a lost figure) and
write_book() writes the book folder: renders, both drafts, sealed pages with
their seals, the page list, the page manifest, the profile and the annotated
assembly - the layout repair_book.py reads.

All text is made up.  The figures are the layout's own: 40 cells a column,
paragraphs indented two cells, headings three.  The book as made has nothing
to repair: its headings are at one level per contents depth (the title `#`,
the contents title and the dividers `##`, the contents groups and the units
`###`), its contents entries carry their numbers.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import proofread_pages as pp  # noqa: E402

WIDTH, HEIGHT = 800, 1100
PITCH = 20
CELLS = 40
FRAME_TOP = 100
COLUMN_STEP = 40
FIRST_COLUMN_X = 640
COLUMNS = 13
BAND_X = {"odd": 720, "even": 70}
HEAD = "青山文叢"
CONTENTS_WORD = "目次"
DIGITS = "〇一二三四五六七八九"
FILLER = ("春風吹過小橋流水人家門前柳樹新綠山中泉石清幽白雲深處秋月照林間鳥語花香晨光"
          "古寺鐘聲遠近漁舟唱晚歸來竹籬茅舍炊煙起落田野稻麥黃熟牧童牛背笛聲長")


def numeral(value: int) -> str:
    return "".join(DIGITS[int(c)] for c in str(value))


def filler(seed: int, size: int) -> str:
    """SIZE made-up characters, a pseudo-random walk over FILLER (no short
    period, so two copies of a page align one way only)."""
    out = []
    state = seed * 7919 + 17
    for _ in range(size):
        state = (state * 1103515245 + 12345) % 2147483648
        out.append(FILLER[(state >> 8) % len(FILLER)])
    return "".join(out)


def paragraph(seed: int, size: int) -> str:
    """SIZE characters of made-up prose with a comma every seven and a full
    stop at the end."""
    chars = filler(seed, size)
    out = []
    for i, c in enumerate(chars, 1):
        out.append(c)
        if i == size:
            out.append("。")
        elif i % 7 == 0:
            out.append("，")
    return "".join(out)


def make_spec() -> dict[int, dict[str, Any]]:
    """The made-up book, page -> spec.  Scans 2-3 are the contents and 4 a
    preface (suffix 目次, folios 1-3), 5-12 run 甲編 (folios 1-8, 5 its
    divider), 13-20 run 乙編 (13 its divider; folios 1-4, then 7-10: two
    leaves missing)."""
    units = {"甲編": [(5, "春日遊記"), (7, "秋夜讀書"), (9, "山居雜詠"), (11, "江上聽雨")],
             "乙編": [(13, "晨鐘暮鼓"), (15, "田園小記"), (18, "松下問答")]}
    spec: dict[int, dict[str, Any]] = {}
    spec[1] = {"items": [("heading", "青山文叢全編", 1)], "suffix": None, "folio": None, "divider": None}
    contents_lines = []
    for suffix, rows in units.items():
        # A group heading: the run's suffix, then its subtitle beside it.
        contents_lines.append(("heading", f"{suffix} 詩文", 3))
        for n, (_, title) in enumerate(rows, 1):
            contents_lines.append(("line", f"{n} {title}"))
    spec[2] = {"items": [("heading", "青山文叢目次", 2)] + contents_lines[:5], "suffix": CONTENTS_WORD, "folio": 1}
    spec[3] = {"items": contents_lines[5:], "suffix": CONTENTS_WORD, "folio": 2}
    spec[4] = {"items": [("para", paragraph(4, 60), False)], "suffix": CONTENTS_WORD, "folio": 3}
    folio_of = {}
    for scan in range(5, 13):
        folio_of[scan] = ("甲編", scan - 4)
    for scan in range(13, 21):
        folio_of[scan] = ("乙編", scan - 12 if scan < 17 else scan - 10)
    starts = {scan: (suffix, n, title) for suffix, rows in units.items() for n, (scan, title) in enumerate(rows, 1)}
    for scan, (suffix, folio) in folio_of.items():
        items: list[tuple] = []
        if scan in (5, 13):
            items.append(("divider", suffix))
        if scan in starts:
            _, n, title = starts[scan]
            items.append(("heading", f"{n} {title}", 3))
            items.append(("para", paragraph(scan, 50), False))
            items.append(("para", paragraph(scan + 40, 90), False))
        else:
            items.append(("para", paragraph(scan, 70), True))
            items.append(("para", paragraph(scan + 20, 120), False))
            items.append(("para", paragraph(scan + 60, 60), False))
        spec[scan] = {"items": items, "suffix": suffix, "folio": folio}
    for scan, page in spec.items():
        page["scan"] = scan
        page.setdefault("divider", None)
        page["headInB"] = "end" if scan % 3 else "start"
        page["standIns"] = {}
        page["extraSealed"] = []
    return spec


def layout(page: Mapping[str, Any]) -> dict[str, Any]:
    """Columns of glyph cells for PAGE's items, right to left: each column
    (x, first cell, cells, text, label).  A paragraph starts two cells down
    (flush when it continues the previous page), a heading three, a divider's
    suffix is drawn large; a line of a list or the contents starts flush (or
    at the cell its item gives: ("line", text, cell)); every column of a
    quotation ("quote", text, cell) starts at its cell."""
    columns: list[dict[str, Any]] = []
    x = page.get("firstX", FIRST_COLUMN_X)
    for item in page["items"]:
        kind = item[0]
        if kind == "divider":
            columns.append({"x": x, "top": 1, "cells": len(item[1]), "text": item[1], "label": "doc_title",
                            "scale": 3})
            x -= COLUMN_STEP * 2
            continue
        if kind == "heading":
            text, start, label = item[1], 3, "paragraph_title"
        elif kind == "line":
            text, start, label = item[1], (item[2] if len(item) > 2 else 0), "text"
        elif kind == "quote":
            text, start, label = item[1], item[2], "vertical_text"
        else:
            text, start, label = item[1], (0 if item[2] else 2), "vertical_text"
        # Each character with the marks after it: a mark takes no cell.
        units: list[str] = []
        for c in text:
            if pp.CJK(c) or not units:
                units.append(c)
            else:
                units[-1] += c
        at = 0
        first = True
        while at < len(units) or first:
            top = start if first or kind == "quote" else 0
            room = CELLS - top
            piece = units[at:at + room]
            columns.append({"x": x, "top": top, "cells": len(piece), "text": "".join(piece),
                            "label": label, "scale": 1})
            at += room
            first = False
            x -= COLUMN_STEP
    return {"columns": columns}


def draw(page: Mapping[str, Any], path: Path) -> None:
    from PIL import Image, ImageDraw
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    pen = ImageDraw.Draw(image)
    for column in layout(page)["columns"]:
        size = 16 * column["scale"]
        step = PITCH * column["scale"]
        for k in range(column["cells"]):
            top = FRAME_TOP + column["top"] * PITCH + k * step + 2
            pen.rectangle([column["x"] - size // 2, top, column["x"] + size // 2, top + size - 1], fill=0)
    parity = "odd" if page["scan"] % 2 else "even"
    if page.get("suffix") or page.get("folio"):
        bx = page.get("bandX", BAND_X[parity])
        head = HEAD + (page.get("suffix") or "")
        for k in range(len(head)):
            top = 130 + k * 16
            pen.rectangle([bx - 6, top, bx + 6, top + 11], fill=0)
        if page.get("folio"):
            for k in range(len(numeral(page["folio"]))):
                top = 690 + k * 16
                pen.rectangle([bx - 6, top, bx + 6, top + 11], fill=0)
    image.save(path)


def box_of(x: float, top_px: float, width_px: float, height_px: float) -> list[float]:
    return [round(x / WIDTH, 4), round(top_px / HEIGHT, 4), round(width_px / WIDTH, 4), round(height_px / HEIGHT, 4)]


def engine_a(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Engine A's blocks: the band (head, suffix, folio) and one block per
    column, with the column's text and the marks the sealed text holds, then
    the page's extraBlocks (blocks engine A read that the print layout does
    not draw)."""
    blocks = []
    parity = "odd" if page["scan"] % 2 else "even"
    bx = page.get("bandX", BAND_X[parity])
    if page.get("suffix") and not page.get("aHeadless"):
        blocks.append({"text": page.get("headRead") or HEAD, "boundingBox": box_of(bx - 8, 128, 16, len(HEAD) * 16),
                       "blockLabel": "header"})
        blocks.append({"text": page["suffix"], "boundingBox": box_of(bx - 8, 130 + len(HEAD) * 16, 16,
                                                                    len(page["suffix"]) * 16),
                       "blockLabel": "header"})
    if page.get("folio") and not page.get("aNoFolio"):
        text = page.get("aFolio") or numeral(page["folio"])
        box = box_of(*page["aFolioBox"]) if page.get("aFolioBox") else \
            box_of(bx - 8, 688, 16, len(numeral(page["folio"])) * 16)
        blocks.append({"text": text, "boundingBox": box, "blockLabel": "number"})
    marks = page.get("aMarks") or {}
    # aHorizontal: engine A reads every column as a line across the page (a
    # title page set horizontally).
    across = page.get("aHorizontal") is True
    for k, column in enumerate(layout(page)["columns"]):
        size = 16 * column["scale"]
        top = FRAME_TOP + column["top"] * PITCH
        height = column["cells"] * PITCH * column["scale"]
        text = marks.get(k, column["text"])
        box = box_of(max(0, column["x"] - height // 2), top, height, size + 4) if across \
            else box_of(column["x"] - size // 2 - 2, top, size + 4, height)
        blocks.append({"text": text, "boundingBox": box, "blockLabel": column["label"]})
    blocks.extend(copy.deepcopy(page.get("extraBlocks") or []))
    for old, new in (page.get("aEdits") or []):
        block = next((b for b in blocks if b["blockLabel"] != "number" and old in b["text"]), None)
        assert block is not None, (page["scan"], old)
        block["text"] = block["text"].replace(old, new, 1)
    for k, block in enumerate(blocks):
        # A block of the page's extraBlocks may carry its own order (-1:
        # outside engine A's reading order, a footnote's).
        block.setdefault("blockOrder", k + 1)
    return blocks


def printed_text(page: Mapping[str, Any]) -> str:
    """What the page prints, as Markdown: its items only."""
    return sealed_text(page, sealed=False)


def sealed_text(page: Mapping[str, Any], sealed: bool = True) -> str:
    """The sealed page: the printed items, then the check's additions
    (extraSealed lines, sealedEdits replacements)."""
    rows = []
    for item in page["items"]:
        if item[0] == "divider":
            rows.append(f"## {item[1]}")
        elif item[0] == "heading":
            rows.append("#" * item[2] + " " + item[1])
        else:
            rows.append(item[1])
    if not sealed:
        return "\n\n".join(rows) + "\n"
    rows.extend(page.get("extraSealed") or [])
    text = "\n\n".join(rows)
    for old, new in (page.get("sealedEdits") or []):
        assert old in text, (page["scan"], old)
        text = text.replace(old, new, 1)
    return text + "\n"


def engine_b(page: Mapping[str, Any]) -> str:
    """Engine B's reading of the print: the body's characters with its commas
    as 丶 and its full stops as 口, then bEdits, and the head, suffix and
    folio run into the stream at the start or end (not when bHeadless)."""
    body = printed_text(page)
    body = "".join(line.lstrip("# ") + "\n" for line in body.split("\n") if line.strip())
    body = body.replace("，", "丶").replace("。", "口")
    for old, new in (page.get("bEdits") or []):
        assert old in body, (page["scan"], old)
        body = body.replace(old, new, 1)
    if not page.get("suffix") or page.get("bHeadless"):
        return body
    head = HEAD + page["suffix"]
    folio = numeral(page["folio"]) if page.get("folio") else ""
    if page["headInB"] == "start":
        return folio + head + body
    return body.rstrip("\n") + folio + head + "\n"


def seal(markdown: str, record: dict[str, Any]) -> bytes:
    record = copy.deepcopy(record)
    record.setdefault("provenance", {})["markdownSha256"] = pp.sha256_bytes(markdown.encode("utf-8"))
    record["provenance"].pop("jsonPayloadSha256", None)
    record["provenance"]["jsonPayloadSha256"] = pp.sha256_bytes(pp.canonical_json_bytes(record))
    return pp.canonical_json_bytes(record)


def write_book(root: Path, spec: Mapping[int, Mapping[str, Any]], *, assemble: bool = True,
               profile: Mapping[str, Any] | None = None, excluded: tuple[int, ...] = ()) -> Path:
    for name in ("renders", "ocr-paddle", "ocr-hunyuan", "proofread", "assembled"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest_pages = []
    rows = []
    texts = {}
    for scan in sorted(spec):
        page = spec[scan]
        stem = f"page-{scan:04d}"
        draw(page, root / "renders" / f"{stem}.png")
        blocks = engine_a(page)
        (root / "ocr-paddle" / f"{stem}.txt").write_text("\n".join(b["text"] for b in blocks) + "\n", encoding="utf-8")
        (root / "ocr-paddle" / f"{stem}.json").write_text(json.dumps(
            {"lines": blocks, "renderWidth": WIDTH, "renderHeight": HEIGHT, "scanPage": scan}, ensure_ascii=False),
            encoding="utf-8")
        (root / "ocr-hunyuan" / f"{stem}.txt").write_text(engine_b(page), encoding="utf-8")
        markdown = sealed_text(page)
        record = {"scanPage": scan, "status": "complete", "schemaVersion": 1, "mode": "merge",
                  "adjudications": list(page.get("adjudications") or []),
                  "doubts": list(page.get("sealDoubts") or []),
                  **({"punctuationPasses": list(page["punctuationPasses"])} if page.get("punctuationPasses") else {}),
                  **({"tableStitch": dict(page["tableStitch"])} if page.get("tableStitch") else {})}
        data = seal(markdown, record)
        (root / "proofread" / f"{stem}.md").write_text(markdown, encoding="utf-8")
        (root / "proofread" / f"{stem}.json").write_bytes(data)
        render_sha = pp.sha256_file(root / "renders" / f"{stem}.png")
        manifest_pages.append({"scan_page": scan, "render_file": f"../renders/{stem}.png",
                               "render_sha256": render_sha, "marker_expected": scan not in excluded})
        rows.append({"scanPage": scan, "markdownPath": f"proofread/{stem}.md",
                     "markdownSha256": pp.sha256_bytes(markdown.encode("utf-8")),
                     "jsonSha256": pp.sha256_bytes(data), "markerExpected": scan not in excluded})
        texts[scan] = markdown
    manifest = {"expected_scan_pages": len(spec), "source_pdf": "made-up.pdf", "source_sha256": "0" * 64,
                "pages": manifest_pages}
    (root / "assembled" / "page-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (root / "assembled" / "page-sources.json").write_text(json.dumps({"pages": rows}, ensure_ascii=False),
                                                          encoding="utf-8")
    if assemble:
        order = [scan for scan in sorted(spec) if scan not in excluded]
        parts = [f"<!-- page_{scan:03d} -->\n\n{texts[scan].strip()}\n\n" for scan in order]
        combined = "".join(parts)
        combined = combined[:-1] if combined.endswith("\n\n") else combined
        (root / "assembled" / "combined-annotated.md").write_text(combined, encoding="utf-8")
    profile = profile if profile is not None else {
        "layout": {"runningHeads": [HEAD]},
        "notation": {"marks": ["。", "，"]},
    }
    (root / "book-profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return root


def tree_hashes(root: Path) -> dict[str, str]:
    """Every file under ROOT and its SHA-256."""
    return {str(path.relative_to(root)): pp.sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}
