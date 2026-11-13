"""Image-bound full-page coverage and conservative, exact-locator repairs.

OCR text is only a locator hint.  Only the current full-image response can
authorize a repair; the JSON consumer checks its candidate hash, regions,
locators, non-overlap and protected text.  This is not an OCR correctness
oracle: uncertain visual readings remain explicit failures of coverage.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import html
import json
import math
import re
import unicodedata
from typing import Any, Mapping

import _tables
from _note_markers import iter_note_markers, marker_key, math_context


class FidelityError(ValueError):
    """An answer cannot provide current, complete image-backed evidence."""


REGION_KINDS = frozenset({"body", "heading", "cover", "table", "tree", "formula",
                          "latin", "note", "caption", "furniture", "illustration"})
INVENTORY_SYSTEM = """逐區檢查當前完整掃描頁；OCR 只係尋位提示，唔係權威。
先睇晒全頁，唔可以因為兩份 OCR 都空白／冇漢字就判空白。包括稀疏封面、樹圖、表格、
小字、正文旁註、頁底註、拉丁字、數字、算式、運算符同印刷項號。正文同家具按本頁
圖像及位置區分，短文件中重複字或字數少本身唔證明係家具。
只輸出 JSON：{"schema_version":1,"page_state":"content|blank|uncertain",
"evidence":"全頁觀察及限制","regions":[{"id":"r1","kind":"body",
"box":[0.1,0.1,0.9,0.9],"evidence":"該區嘅位置及實際所見"}],
"features":[{"id":"f1","region_id":"r1","text":"原圖確認嘅精確文字",
"before":"緊接之前嘅文字","after":"緊接之後嘅文字"}]}。
box 係全頁相對座標 x0,y0,x1,y1，0 至 1；有文字嘅區域全部列出，家具亦列出。
kind 可用 body/heading/cover/table/tree/formula/latin/note/caption/furniture/illustration。
features 必須列齊可辨認嘅數字、算式、運算符、外文、原印項號、正文註號及註定義；
每次出現分開列，連同足以唯一定位嘅緊鄰正文。表格 feature 額外填
"cell":{"row_label":"同一行嘅識別文字","column_label":"同一欄嘅識別文字"}；
before/after 只用同一格內鄰字，唔跨格。兩個 label 必須據圖可辨，唔可以猜；佢哋係
邏輯格位依據，容許整表轉置。冇辦法辨認所屬格時 page_state=uncertain。
整區只有一個 feature 先可兩邊空白。
text/before/after 用將要保留嘅字面 Markdown 表示；唔可以憑上下文補數字或推算註號。
blank 只適用於全頁冇可轉錄內容且 regions/features 都空；有家具也列為 content。
睇唔清、範圍被截或未覆蓋就填 uncertain，唔可冒認完整。"""

REVIEW_SYSTEM = """逐區對照當前完整原頁、inventory 同候選 Markdown，補回漏讀區域同來源符號。
必須重新睇整張原圖，包含兩個 OCR 都漏嘅內容；唔好以字數／兩引擎同意代替核圖。
原文係唯一依據；唔好照抄 OCR 猜測、補連號、推算數字或刪來源異體／校勘符號。
正文註號與頁底註須逐一核對；有定義無正文錨點時按圖補，唔可按次序猜位置。
表格數字／算式必須喺原來所屬格，樹圖必須保留父子關係。家具只憑本頁圖像和位置裁定。
只輸出 JSON：{"schema_version":1,"candidate_sha256":"照抄本次 hash",
"verdict":"complete|uncertain","regions":[{"id":"r1","status":"covered|recovered|excluded|uncertain",
"evidence":"本次對圖位置、讀法及候選中所屬位置"}],
"edits":[{"region_id":"r1","before":"候選中緊接之前嘅原字串",
"old":"要替換嘅候選原字串","after":"候選中緊接之後嘅原字串",
"new":"對圖確認嘅文字","evidence":"原圖筆畫、位置及修正依據"}]}。
edits 全部對應修改前同一份候選；before+old+after 必須精確、唯一匹配，唔可重疊。
插入用 old="" 但前後錨點必須唯一；整頁空候選時先可三者都空。
可以一次替換整個有問題區域，唔需要推測字元 offset。唔好改候選中表格占位符。
所有 inventory region 都要逐一交代；只有 furniture/illustration 可 excluded。
凡未能據圖閉合嘅漏字／格位／讀法，必須 uncertain；唔可空泛宣稱 complete。"""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _object(raw: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise FidelityError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    try:
        obj = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError) as error:
        raise FidelityError(f"invalid fidelity JSON: {error}") from error
    if not isinstance(obj, dict) or type(obj.get("schema_version")) is not int or obj["schema_version"] != 1:
        raise FidelityError("fidelity answer needs schema_version 1 object")
    return obj


def _string(obj: Mapping[str, Any], key: str, *, empty: bool = False) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise FidelityError(f"{key} must be {'a' if empty else 'a nonempty'} string")
    return value


def parse_inventory(raw: str, image: bytes) -> dict[str, Any]:
    obj = _object(raw)
    if obj.get("page_state") not in {"content", "blank", "uncertain"}:
        raise FidelityError("invalid inventory page_state")
    _string(obj, "evidence")
    regions, features = obj.get("regions"), obj.get("features")
    if not isinstance(regions, list) or not isinstance(features, list):
        raise FidelityError("inventory regions and features must be lists")
    ids: set[str] = set()
    kinds: dict[str, str] = {}
    for region in regions:
        if not isinstance(region, dict):
            raise FidelityError("invalid region")
        rid = _string(region, "id")
        if rid in ids or region.get("kind") not in REGION_KINDS:
            raise FidelityError("duplicate region or invalid region kind")
        ids.add(rid)
        kinds[rid] = region["kind"]
        _string(region, "evidence")
        box = region.get("box")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(x) not in {int, float} or not math.isfinite(x) or not 0 <= x <= 1 for x in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise FidelityError("region requires a finite full-page relative box")
    fids: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            raise FidelityError("invalid feature")
        fid = _string(feature, "id")
        if fid in fids or feature.get("region_id") not in ids:
            raise FidelityError("duplicate feature or unknown feature region")
        if kinds[feature["region_id"]] in {"furniture", "illustration"}:
            raise FidelityError("excluded-region text cannot be a retained feature")
        fids.add(fid)
        _string(feature, "text")
        _string(feature, "before", empty=True)
        _string(feature, "after", empty=True)
        if kinds[feature["region_id"]] == "table":
            cell = feature.get("cell")
            if not isinstance(cell, dict):
                raise FidelityError("table feature needs logical cell ownership")
            _string(cell, "row_label")
            _string(cell, "column_label")
    if obj["page_state"] == "blank" and (regions or features):
        raise FidelityError("blank inventory contains visible regions")
    if obj["page_state"] == "content" and not regions:
        raise FidelityError("content inventory has no regions")
    return {**obj, "source_image_sha256": hashlib.sha256(image).hexdigest()}


def visible_text(text: str) -> str:
    """Remove known layout markup, preserving digits, formulas and cell bounds."""
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"</(?:td|th)\s*>\s*<(?:td|th)\b[^>]*>", "|", text, flags=re.I)
    text = re.sub(r"</?(?:table|thead|tbody|tfoot|tr|td|th|sup|sub|br|p|div|span|strong|em|b|i|u|s|del|ins|ul|ol|li|blockquote|code|pre|a|h[1-6])\b[^>]*>", "", text, flags=re.I)
    text = html.unescape(text)
    text = re.sub(r"(?m)^\s*\|[\s:|\-]+\|\s*$", "", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-+*]\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:-\s*){3,}$", "", text)
    text = re.sub(r"(?m)^\s*(?:_\s*){3,}$", "", text)
    text = re.sub(r"(?<!\\)\*\*(.+?)(?<!\\)\*\*", r"\1", text)
    # A literal printed star is escaped by the output contract.  An unescaped
    # emphasis opener needs a left boundary and no following whitespace;
    # x*y and x*y*z therefore retain their arithmetic multiplication signs.
    text = re.sub(r"(?<![A-Za-z0-9_\\*])\*(?=\S)([^*\n]*?\S)(?<!\\)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<![\w\\])__(?=\S)(.+?\S)__(?!\w)", r"\1", text)
    # Explicitly escape literal stars; mathematical multiplication is retained.
    text = text.replace(r"\*", "*")
    return text


def canonical_note_markers(text: str) -> str:
    for match in reversed(list(iter_note_markers(text))):
        key = marker_key(match.group())
        if key and not math_context(text, match.start(), match.end()):
            text = text[:match.start()] + "⟦note:" + key + "⟧" + text[match.end():]
    return text


def compact(text: str) -> str:
    return re.sub(r"\s+", "", visible_text(canonical_note_markers(text))).replace("\U00016fe6", "。。")


def character_counts(text: str) -> Counter[str]:
    """Retained textual glyphs, regardless of script, plus arithmetic signs.

    Punctuation has its own image census.  Counts allow layout reordering;
    anchored feature checks below retain the positions of vulnerable content.
    """
    text = canonical_note_markers(text)
    for table in reversed(_tables.find_tables(text)):
        text = text[:table.start] + table.caption + "\n".join(cell.text for cell in table.cells) + text[table.end:]
    return Counter(c for c in visible_text(text)
                   if unicodedata.category(c)[0] in {"L", "N"}
                   or unicodedata.category(c) == "Sm" or c in "*%/-")


def refresh_table_mapping(source_tables: list[str], final_tables: list[str]) -> tuple[bool, list[dict[str, Any]]]:
    """Bind source-approved numeric repairs to unchanged logical table cells.

    No guessing by bag-of-words or proximity.  New/reordered tables, changed
    cell geometry or changed CJK labels require renewed source mapping.
    """
    if len(source_tables) != len(final_tables):
        return False, []
    records = []
    def letters(text: str) -> str:
        return "".join(c for c in visible_text(text) if unicodedata.category(c).startswith("L"))
    for before, after in zip(source_tables, final_tables):
        left, right = _tables.find_tables(before), _tables.find_tables(after)
        if len(left) != 1 or len(right) != 1:
            return False, []
        a, b = left[0], right[0]
        if (a.problems or b.problems or a.rows != b.rows or a.cols != b.cols
                or a.layout() != b.layout() or letters(a.caption) != letters(b.caption)
                or any(letters(x.text) != letters(y.text) for x, y in zip(a.cells, b.cells))):
            return False, []
        # Identical numeric-only tables are fine; changed tables without any
        # CJK/Latin labels cannot identify correspondence among several grids.
        if before != after and len(source_tables) > 1 and not any(letters(c.text) for c in a.cells):
            return False, []
        records.append({"source_sha256": text_sha256(before), "final_sha256": text_sha256(after),
                        "rule": "same-ordered-grid-same-cell-spans-and-letters", "changed": before != after})
    return True, records


def feature_losses(candidate: str, inventory: Mapping[str, Any]) -> list[str]:
    candidate = canonical_note_markers(candidate)
    tables = _tables.find_tables(candidate)
    prose = candidate
    for table in reversed(tables):
        prose = prose[:table.start] + "\n" + prose[table.end:]
    target = compact(prose)
    choices: list[list[tuple[Any, ...]]] = []
    for feature in inventory["features"]:
        needle = compact(feature["before"] + feature["text"] + feature["after"])
        places: list[tuple[Any, ...]] = []
        ownership = feature.get("cell")
        if ownership:
            row_label, col_label = compact(ownership["row_label"]), compact(ownership["column_label"])
            for ti, table in enumerate(tables):
                if table.problems:
                    continue
                grid = [[compact(value) for value in row] for row in table.grid(fill_spans=True)]
                for cell in table.cells:
                    rows = {value for row in grid[cell.row:cell.row + cell.rowspan] for value in row}
                    cols = {row[c] for row in grid for c in range(cell.col, cell.col + cell.colspan)}
                    # Labels are whole cell readings, so a one-digit heading
                    # does not also match every larger value containing it.
                    owns = (row_label in rows and col_label in cols or
                            col_label in rows and row_label in cols)
                    if owns and needle:
                        places.extend(("cell", ti, cell.row, cell.col, m.start() + len(compact(feature["before"])))
                                      for m in re.finditer(re.escape(needle), compact(cell.text)))
        elif needle:
            places.extend(("prose", m.start() + len(compact(feature["before"])))
                          for m in re.finditer(re.escape(needle), target))
        choices.append(places)
    # Each source occurrence needs a distinct target occurrence; repeated
    # numerals must not be satisfied by one surviving copy.
    assigned: dict[tuple[Any, ...], int] = {}
    def match(index: int, seen: set[tuple[Any, ...]]) -> bool:
        for place in choices[index]:
            if place in seen:
                continue
            seen.add(place)
            if place not in assigned or match(assigned[place], seen):
                assigned[place] = index
                return True
        return False
    return [inventory["features"][i]["id"] for i in range(len(choices)) if not match(i, set())]


def apply_review(raw: str, candidate: str, inventory: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    obj = _object(raw)
    if obj.get("candidate_sha256") != text_sha256(candidate):
        raise FidelityError("review candidate hash is stale")
    if obj.get("verdict") not in {"complete", "uncertain"}:
        raise FidelityError("invalid coverage verdict")
    regions = {r["id"]: r for r in inventory["regions"]}
    rows, edits = obj.get("regions"), obj.get("edits")
    if not isinstance(rows, list) or not isinstance(edits, list):
        raise FidelityError("review regions and edits must be lists")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FidelityError("invalid coverage region")
        rid = _string(row, "id")
        if rid not in regions or rid in seen:
            raise FidelityError("unknown or repeated coverage region")
        seen.add(rid)
        status = row.get("status")
        if status not in {"covered", "recovered", "excluded", "uncertain"}:
            raise FidelityError("invalid coverage status")
        if status == "excluded" and regions[rid]["kind"] not in {"furniture", "illustration"}:
            raise FidelityError("body content cannot be excluded as furniture")
        _string(row, "evidence")
    if seen != set(regions):
        raise FidelityError("coverage answer omitted inventory regions")
    placements: list[tuple[int, int, str]] = []
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("region_id") not in regions:
            raise FidelityError("edit has no inventory region")
        before, old, after, new = (_string(edit, key, empty=True) for key in ("before", "old", "after", "new"))
        _string(edit, "evidence")
        if old == new:
            raise FidelityError("no-op edit")
        locator = before + old + after
        if not locator and candidate:
            raise FidelityError("unanchored insertion into a nonempty candidate")
        if locator and candidate.count(locator) != 1:
            raise FidelityError("edit locator does not match exactly once")
        start = candidate.find(locator) + len(before) if locator else 0
        end = start + len(old)
        def overlaps(begin: int, stop: int) -> bool:
            if start == end and begin == stop:
                return start == begin
            if start == end:
                return begin < start < stop
            if begin == stop:
                return start < begin < end
            return max(start, begin) < min(end, stop)
        if any(overlaps(begin, stop) for begin, stop, _ in placements):
            raise FidelityError("overlapping edits")
        placements.append((start, end, new))
    result = candidate
    for start, end, new in sorted(placements, reverse=True):
        result = result[:start] + new + result[end:]
    reviewed_result_sha = text_sha256(result)
    doubled_circle_count = result.count("\U00016fe6")
    result = result.replace("\U00016fe6", "。。")
    lost = feature_losses(result, inventory)
    uncertain = inventory["page_state"] == "uncertain" or any(r["status"] == "uncertain" for r in rows)
    retained_regions = any(r["kind"] not in {"furniture", "illustration"} for r in regions.values())
    empty_retained_content = retained_regions and not character_counts(result)
    complete = obj["verdict"] == "complete" and not uncertain and not lost and not empty_retained_content
    return result, {**obj, "source_image_sha256": inventory["source_image_sha256"],
                    "reviewed_result_sha256": reviewed_result_sha,
                    "result_sha256": text_sha256(result), "feature_losses": lost,
                    "double_circle_normalizations": doubled_circle_count,
                    "source_equivalent_transforms": ([{"kind": "double-circle-canonical-spelling",
                        "before_sha256": reviewed_result_sha, "after_sha256": text_sha256(result),
                        "count": doubled_circle_count}] if doubled_circle_count else []),
                    "empty_retained_content": empty_retained_content, "complete": complete}


def inventory_page(client: Any, image: bytes, drafts: list[str], max_tokens: int) -> tuple[dict[str, Any], Mapping[str, Any]]:
    raw, metrics = client.ask_answering(
        INVENTORY_SYSTEM, "以下 OCR 只作候選位置，必須核對當前整頁：\n" +
        json.dumps({"drafts": drafts}, ensure_ascii=False), image, max_tokens, kind="page-inventory")
    return parse_inventory(raw, image), metrics


def review_page(client: Any, image: bytes, candidate: str, inventory: Mapping[str, Any],
                max_tokens: int, *, stage: str, table_orientation: str = "auto") -> tuple[str, dict[str, Any], Mapping[str, Any]]:
    if stage not in {"content-recovery", "coverage-review"}:
        raise ValueError("unknown content fidelity stage")
    if inventory["source_image_sha256"] != hashlib.sha256(image).hexdigest():
        raise FidelityError("inventory belongs to a different source image")
    prompt = json.dumps({"candidate_sha256": text_sha256(candidate), "candidate": candidate,
                         "inventory": inventory, "stage": stage,
                         "table_orientation": table_orientation,
                         "table_rule": "as-printed: preserve physical rows/columns; auto: change axes only with explicit image evidence; never infer right-to-left or orientation from arithmetic"}, ensure_ascii=False)
    raw, metrics = client.ask_answering(REVIEW_SYSTEM, prompt, image, max_tokens, kind=stage)
    result, record = apply_review(raw, candidate, inventory)
    return result, record, metrics
