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

from jyut_ocr.core import tables as _tables
from jyut_ocr.core.note_markers import iter_note_markers, marker_key, math_context


CONTRACT_VERSION = "content-fidelity-v2"
SCHEMA_VERSION = 2
# The doubt a page carries when a content-fidelity stage could not be settled
# (a failed answer, an unresolved coverage review, an inventory that calls a
# page blank where the engines read text).  Such a page is sealed with this
# doubt and contentFidelityStatus "doubt" instead of failing.
DOUBT_KIND = "content-fidelity-unresolved"


class FidelityError(ValueError):
    """Stable validation location and bounded-correction eligibility."""
    def __init__(self, message: str, *, code: str = "contract-invalid",
                 path: str = "$", correctable: bool = True):
        super().__init__(message)
        self.code, self.path, self.correctable = code, path, correctable

    def diagnostic(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": str(self)}


REGION_KINDS = frozenset({"body", "heading", "cover", "table", "tree", "formula",
                          "latin", "note", "caption", "furniture", "illustration"})
ISSUE_KINDS = frozenset({"reading", "crop", "coverage", "ownership", "furniture"})
INVENTORY_SYSTEM = """逐區檢查當前完整掃描頁；OCR 只係尋位提示。必須睇完整原頁，
包括正文、稀疏題字、表格、樹圖、註文、外文、數字、算式及家具。
只輸出 JSON，schema_version=2，contract_version=content-fidelity-v2，
source_image_sha256 照抄請求。page_state=content|blank|uncertain，evidence 非空。
regions 列出全部可見區域（包括家具），每項 {id,kind,box:[x0,y0,x1,y1],evidence}，
box 係全頁 0 至 1 相對座標；kind 可用 body/heading/cover/table/tree/formula/latin/note/caption/furniture/illustration。
features 只列要保留嘅交付內容！家具數字、頁碼、印章文字只放 region evidence，唔放 features。
features 每項 {id,region_id,text,before,after}，精確記錄每次出現嘅數字、外文、算式、運算符、原印項號及註號。
before/after 係緊鄰原字，表格只用同格鄰字。唔可以推算、猜字或合併重複出現。
表格 feature 必須有 cell，兩種選一：
1. {kind:"labels",row_label:"完整可見行標籤格文字",column_label:"完整可見欄標籤格文字"}。
label 必須係成格嘅全部可見文字（包括附加年限、細字、換行），唔係簡稱或子字串。空 label 唔係 wildcard。
2. 無完整可辨標籤時，用 {kind:"grid",table_region_id:"r1",row:0,col:0,rows:2,cols:3,rowspan:1,colspan:1}。
格位由上至下行、左至右欄，全部從 0 開始；rows/cols 係原圖格網總尺寸，span 係原印合併格。
所有疑點列 issues:[{id,region_id,kind,evidence}]，region_id 係區域 ID；全頁範圍未知可為 null。
kind=reading|crop|coverage|ownership|furniture。有疑點 page_state 必須 uncertain；冇疑點 issues=[]。
唔清楚格位時保留 ownership issue，唔造假 labels 或將 feature 當 prose。
blank 只用於全頁冇可轉錄內容而 regions/features/issues 都空；只有家具仍係 content。
唔能據圖確定就記疑點，唔可為通過驗證聲稱完整。"""

REVIEW_SYSTEM = """逐區重新對照當前完整原頁、inventory 同候選 Markdown，補漏並保留來源字形、
標點、數字、算式、註號、表格格位及樹圖關係。兩份 OCR 一致唔證明正確。
只輸出 JSON：schema_version=2，contract_version、source_image_sha256、inventory_sha256、candidate_sha256 照抄請求。
verdict=complete|uncertain。regions:[{id,status:"covered|recovered|excluded|uncertain",evidence}] 須列晒 inventory 區域；
只有 furniture/illustration 可 excluded；唔可以因位置近頁邊就刪正文。
edits:[{region_id,before,old,after,new,evidence}] 全部對應修改前同一候選，before+old+after 必須精確唯一、唔重疊。
插入 old="" 但需要唯一前後錨點；整份候選空白先容許三者都空。
resolutions:[{issue_id,status:"resolved|unresolved|excluded",evidence}] 必須逐項處理 inventory issues。
單憑 verdict=complete 唔會解除疑點；家具 issue 或裁切家具可據圖 excluded，正文／未知範圍裁切不能 excluded。
resolved 要交代原圖具體位置同點樣解決讀法、範圍或格位；未能解決就 unresolved 同 verdict=uncertain。
table_mappings:[{region_id,table_index,orientation:"identity|transpose",source_rows,source_cols,evidence}]。
每個 table region（包括純漢字表格）都須明示映射到 edits 完成後候選第幾張表（0 起計）；
唔使自己計新 hash，程式驗證來源及候選 binding、修改同格網後會將映射封存到實際交付 bytes。
source_rows/source_cols 係原圖格網尺寸；grid feature 嘅尺寸及 spans 必須對應；as-printed 只接受 identity。
auto 嘅 transpose 必須有原圖證據。唔可以以算術、連號或語意推斷格位；映射唔清楚要 uncertain。
冇 table regions 時 table_mappings=[]。冇疑點時 resolutions=[]。唔可空泛宣稱 complete。"""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inventory_sha256(inventory: Mapping[str, Any]) -> str:
    """Canonical semantic inventory, including the source and scoped issues."""
    obj = {k: v for k, v in inventory.items() if k not in {"inventory_sha256", "correction"}}
    return text_sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _object(raw: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise FidelityError(f"duplicate JSON key: {key}", code="duplicate-key")
            out[key] = value
        return out
    try:
        obj = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError) as error:
        if isinstance(error, FidelityError):
            raise
        raise FidelityError(f"invalid fidelity JSON: {error}", code="invalid-json") from error
    if not isinstance(obj, dict) or type(obj.get("schema_version")) is not int or obj["schema_version"] not in {1, 2}:
        raise FidelityError("fidelity answer needs schema_version 1 or 2 object", path="$.schema_version")
    return obj


def _string(obj: Mapping[str, Any], key: str, *, empty: bool = False, path: str = "$") -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise FidelityError(f"{key} must be {'a' if empty else 'a nonempty'} string", path=f"{path}.{key}")
    return value


def _current(obj: Mapping[str, Any], *, correctable: bool = False) -> None:
    """CORRECTABLE: OBJ is a model's answer, whose wrong schema or contract
    version is one more mistake the one correction request may fix; stored
    evidence of another version is not."""
    if not isinstance(obj, Mapping) or obj.get("schema_version") != SCHEMA_VERSION or obj.get("contract_version") != CONTRACT_VERSION:
        raise FidelityError("historical fidelity contract cannot complete or resume as v2",
                            code="historical-contract", path="$.contract_version", correctable=correctable)


def _binding(obj: Mapping[str, Any], key: str, expected: str, *, path: str = "$",
             correctable: bool = False) -> None:
    """CORRECTABLE as in _current: a hash a model's answer copied wrong (or left
    out) can be corrected; stored evidence bound to other bytes cannot."""
    if obj.get(key) != expected:
        raise FidelityError(f"{key} is stale or belongs to different evidence", code="stale-evidence",
                            path=f"{path}.{key}", correctable=correctable)


def parse_inventory(raw: str, image: bytes) -> dict[str, Any]:
    """A model's inventory answer for IMAGE (its binding mistakes are correctable)."""
    return _parse_inventory(raw, hashlib.sha256(image).hexdigest(), answer=True)


def _parse_inventory(raw: str, source_hash: str, *, answer: bool = False) -> dict[str, Any]:
    obj = _object(raw)
    current = obj["schema_version"] == SCHEMA_VERSION
    if current:
        _current(obj, correctable=answer)
        _binding(obj, "source_image_sha256", source_hash, correctable=answer)
    elif "source_image_sha256" in obj:
        _binding(obj, "source_image_sha256", source_hash, correctable=answer)
    if obj.get("page_state") not in {"content", "blank", "uncertain"}:
        raise FidelityError("invalid inventory page_state", path="$.page_state")
    _string(obj, "evidence")
    regions, features = obj.get("regions"), obj.get("features")
    if not isinstance(regions, list) or not isinstance(features, list):
        raise FidelityError("inventory regions and features must be lists", path="$.regions")
    kinds: dict[str, str] = {}
    for i, region in enumerate(regions):
        path = f"$.regions[{i}]"
        if not isinstance(region, dict):
            raise FidelityError("invalid region", path=path)
        rid = _string(region, "id", path=path)
        if rid in kinds or region.get("kind") not in REGION_KINDS:
            raise FidelityError("duplicate region or invalid region kind", path=path)
        kinds[rid] = region["kind"]
        _string(region, "evidence", path=path)
        box = region.get("box")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(x) not in {int, float} or not math.isfinite(x) or not 0 <= x <= 1 for x in box)
                or box[0] >= box[2] or box[1] >= box[3]):
            raise FidelityError("region requires a finite full-page relative box", path=path + ".box")
    fids: set[str] = set()
    grids: dict[str, tuple[int, int]] = {}
    for i, feature in enumerate(features):
        path = f"$.features[{i}]"
        if not isinstance(feature, dict):
            raise FidelityError("invalid feature", path=path)
        fid = _string(feature, "id", path=path)
        if fid in fids or feature.get("region_id") not in kinds:
            raise FidelityError("duplicate feature or unknown feature region", path=path)
        rid = feature["region_id"]
        if kinds[rid] in {"furniture", "illustration"}:
            raise FidelityError("excluded-region text cannot be a retained feature", code="excluded-feature", path=path + ".region_id")
        fids.add(fid)
        for key in ("text", "before", "after"):
            _string(feature, key, empty=key != "text", path=path)
        cell = feature.get("cell")
        if kinds[rid] != "table":
            if cell is not None:
                raise FidelityError("non-table feature has table ownership", path=path + ".cell")
            continue
        if not isinstance(cell, dict):
            raise FidelityError("table feature needs logical cell ownership", code="cell-ownership-unresolved", path=path + ".cell")
        if not current or cell.get("kind") == "labels":
            _string(cell, "row_label", path=path + ".cell")
            _string(cell, "column_label", path=path + ".cell")
        elif cell.get("kind") == "grid":
            if cell.get("table_region_id") != rid:
                raise FidelityError("grid locator must name its table region", path=path + ".cell.table_region_id")
            for key in ("row", "col", "rows", "cols", "rowspan", "colspan"):
                if type(cell.get(key)) is not int or cell[key] < (0 if key in {"row", "col"} else 1):
                    raise FidelityError("grid coordinates/dimensions/spans must be integers", path=path + ".cell." + key)
            if cell["row"] + cell["rowspan"] > cell["rows"] or cell["col"] + cell["colspan"] > cell["cols"]:
                raise FidelityError("grid locator exceeds source dimensions", path=path + ".cell")
            dims = (cell["rows"], cell["cols"])
            if rid in grids and grids[rid] != dims:
                raise FidelityError("inconsistent source table dimensions", path=path + ".cell")
            grids[rid] = dims
        else:
            raise FidelityError("cell.kind must be labels or grid", path=path + ".cell.kind")
    issues = obj.get("issues", [])
    if current:
        if not isinstance(obj.get("issues"), list):
            raise FidelityError("inventory issues must be a list", path="$.issues")
        seen = set()
        for i, issue in enumerate(issues):
            path = f"$.issues[{i}]"
            if not isinstance(issue, dict):
                raise FidelityError("invalid issue", path=path)
            iid = _string(issue, "id", path=path)
            if iid in seen or issue.get("region_id") not in {None, *kinds} or issue.get("kind") not in ISSUE_KINDS:
                raise FidelityError("invalid, duplicate or unscoped issue", path=path)
            seen.add(iid)
            _string(issue, "evidence", path=path)
        if bool(issues) != (obj["page_state"] == "uncertain"):
            raise FidelityError("uncertain inventory requires scoped issues; content/blank must have none", path="$.issues")
    if obj["page_state"] == "blank" and (regions or features or issues):
        raise FidelityError("blank inventory contains visible regions", path="$.page_state")
    if obj["page_state"] == "content" and not regions:
        raise FidelityError("content inventory has no regions", path="$.regions")
    result = {**obj, "source_image_sha256": source_hash}
    if current:
        digest = inventory_sha256(result)
        if "inventory_sha256" in obj:
            _binding(obj, "inventory_sha256", digest, correctable=answer)
        result["inventory_sha256"] = digest
    return result


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


def feature_losses(candidate: str, inventory: Mapping[str, Any],
                   table_mappings: list[Mapping[str, Any]] | None = None) -> list[str]:
    result_hash = text_sha256(candidate)
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
        if ownership and ownership.get("kind") == "grid":
            matches = [m for m in (table_mappings or []) if m.get("region_id") == feature["region_id"]
                       and m.get("result_sha256") == result_hash]
            if len(matches) == 1:
                mapping = matches[0]
                ti = mapping.get("table_index")
                if type(ti) is int and 0 <= ti < len(tables):
                    table = tables[ti]
                    row, col, rs, cs = (ownership[k] for k in ("row", "col", "rowspan", "colspan"))
                    rows, cols = ownership["rows"], ownership["cols"]
                    if mapping.get("orientation") == "transpose":
                        row, col, rs, cs, rows, cols = col, row, cs, rs, cols, rows
                    if (not table.problems and mapping.get("orientation") in {"identity", "transpose"}
                            and (table.rows, table.cols) == (rows, cols)):
                        for cell in table.cells:
                            if (cell.row, cell.col, cell.rowspan, cell.colspan) == (row, col, rs, cs) and needle:
                                places.extend(("cell", ti, cell.row, cell.col, m.start() + len(compact(feature["before"])))
                                              for m in re.finditer(re.escape(needle), compact(cell.text)))
        elif ownership:
            row_label, col_label = compact(ownership["row_label"]), compact(ownership["column_label"])
            region_maps = [m for m in (table_mappings or []) if m.get("region_id") == feature["region_id"]
                           and m.get("result_sha256") == result_hash]
            for ti, table in enumerate(tables):
                if table.problems or (region_maps and ti != region_maps[0]["table_index"]) or (not region_maps and len(tables) > 1):
                    continue
                grid = [[compact(value) for value in row] for row in table.grid(fill_spans=True)]
                for cell in table.cells:
                    rows = {value for row in grid[cell.row:cell.row + cell.rowspan] for value in row}
                    cols = {row[c] for row in grid for c in range(cell.col, cell.col + cell.colspan)}
                    # Labels are whole cell readings, so a one-digit heading
                    # does not also match every larger value containing it.
                    axes = region_maps[0]["orientation"] if region_maps else None
                    owns = bool(row_label and col_label) and (
                        (axes != "transpose" and row_label in rows and col_label in cols) or
                        (axes != "identity" and col_label in rows and row_label in cols))
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


def apply_review(raw: str, candidate: str, inventory: Mapping[str, Any], *,
                 table_orientation: str = "auto") -> tuple[str, dict[str, Any]]:
    obj = _object(raw)
    # The inventory is this page's stored evidence; the bindings below are
    # what the answer was asked to copy, so a slip there is correctable.
    _current(inventory)
    _current(obj, correctable=True)
    _binding(inventory, "inventory_sha256", inventory_sha256(inventory))
    for key, expected in (("candidate_sha256", text_sha256(candidate)),
                          ("source_image_sha256", inventory["source_image_sha256"]),
                          ("inventory_sha256", inventory["inventory_sha256"])):
        _binding(obj, key, expected, correctable=True)
    if obj.get("verdict") not in {"complete", "uncertain"}:
        raise FidelityError("invalid coverage verdict", path="$.verdict")
    regions = {r["id"]: r for r in inventory["regions"]}
    rows, edits = obj.get("regions"), obj.get("edits")
    if not isinstance(rows, list) or not isinstance(edits, list):
        raise FidelityError("review regions and edits must be lists", path="$.regions" if not isinstance(rows, list) else "$.edits")
    seen: set[str] = set()
    for i, row in enumerate(rows):
        path = f"$.regions[{i}]"
        if not isinstance(row, dict):
            raise FidelityError("invalid coverage region", path=path)
        rid = _string(row, "id", path=path)
        if rid not in regions or rid in seen:
            raise FidelityError("unknown or repeated coverage region", path=path + ".id")
        seen.add(rid)
        status = row.get("status")
        if status not in {"covered", "recovered", "excluded", "uncertain"}:
            raise FidelityError("invalid coverage status", path=path + ".status")
        if status == "excluded" and regions[rid]["kind"] not in {"furniture", "illustration"}:
            raise FidelityError("body content cannot be excluded as furniture", path=path + ".status")
        _string(row, "evidence", path=path)
    if seen != set(regions):
        raise FidelityError("coverage answer omitted inventory regions", path="$.regions")
    placements: list[tuple[int, int, str]] = []
    for i, edit in enumerate(edits):
        path = f"$.edits[{i}]"
        if not isinstance(edit, dict) or edit.get("region_id") not in regions:
            raise FidelityError("edit has no inventory region", path=path + ".region_id")
        before, old, after, new = (_string(edit, key, empty=True, path=path) for key in ("before", "old", "after", "new"))
        _string(edit, "evidence", path=path)
        if old == new:
            raise FidelityError("no-op edit", path=path)
        locator = before + old + after
        if not locator and candidate:
            raise FidelityError("unanchored insertion into a nonempty candidate", path=path)
        if locator and candidate.count(locator) != 1:
            raise FidelityError("edit locator does not match exactly once", path=path)
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
            raise FidelityError("overlapping edits", path=path)
        placements.append((start, end, new))
    result = candidate
    for start, end, new in sorted(placements, reverse=True):
        result = result[:start] + new + result[end:]
    reviewed_result_sha = text_sha256(result)
    doubled_circle_count = result.count("\U00016fe6")
    result = result.replace("\U00016fe6", "。。")
    mappings = validate_table_mappings(obj.get("table_mappings"), result, inventory, table_orientation, seal_result=True)
    unresolved = resolve_issues(obj.get("resolutions"), inventory, rows)
    lost = feature_losses(result, inventory, mappings)
    missing_tables = sorted({r["id"] for r in inventory["regions"] if r["kind"] == "table"}
                            - {m["region_id"] for m in mappings})
    uncertain = bool(unresolved) or bool(missing_tables) or any(r["status"] == "uncertain" for r in rows)
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
                    "table_mappings": mappings, "unresolved_issue_ids": unresolved, "unmapped_table_regions": missing_tables,
                    "table_orientation": table_orientation,
                    "empty_retained_content": empty_retained_content, "complete": complete}


def resolve_issues(raw: Any, inventory: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> list[str]:
    if not isinstance(raw, list):
        raise FidelityError("review resolutions must be a list", path="$.resolutions")
    issues = {x["id"]: x for x in inventory["issues"]}
    regions = {x["id"]: x for x in inventory["regions"]}
    statuses = {x["id"]: x["status"] for x in rows}
    seen, unresolved = set(), []
    for i, item in enumerate(raw):
        path = f"$.resolutions[{i}]"
        if not isinstance(item, dict) or item.get("issue_id") not in issues or item["issue_id"] in seen:
            raise FidelityError("unknown or duplicate issue resolution", path=path)
        iid = item["issue_id"]
        seen.add(iid)
        _string(item, "evidence", path=path)
        issue = issues[iid]
        rid, status = issue["region_id"], item.get("status")
        if status == "unresolved":
            unresolved.append(iid)
        elif status == "excluded":
            if rid is None or regions[rid]["kind"] not in {"furniture", "illustration"} or statuses[rid] != "excluded":
                raise FidelityError("body/crop/ownership issue cannot be excluded as furniture", path=path)
        elif status == "resolved":
            if rid is not None and statuses[rid] not in {"covered", "recovered"}:
                raise FidelityError("resolved issue needs covered/recovered region", path=path)
        else:
            raise FidelityError("invalid issue resolution status", path=path + ".status")
    # Missing resolutions remain open rather than disappearing under complete.
    return sorted(set(unresolved) | (set(issues) - seen))


def validate_table_mappings(raw: Any, result: str, inventory: Mapping[str, Any], orientation: str, *,
                            seal_result: bool = False) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise FidelityError("review table_mappings must be a list", path="$.table_mappings")
    tables = _tables.find_tables(result)
    grids = {f["region_id"]: f["cell"] for f in inventory["features"] if f.get("cell", {}).get("kind") == "grid"}
    table_regions = {r["id"] for r in inventory["regions"] if r["kind"] == "table"}
    seen, targets, sealed = set(), set(), []
    for i, item in enumerate(raw):
        path = f"$.table_mappings[{i}]"
        if not isinstance(item, dict) or item.get("region_id") not in table_regions or item["region_id"] in seen:
            raise FidelityError("unknown or duplicate source table mapping", path=path)
        rid, ti = item["region_id"], item.get("table_index")
        seen.add(rid)
        if type(ti) is not int or ti < 0 or ti >= len(tables) or ti in targets:
            raise FidelityError("table mapping needs a distinct candidate table", code="cell-ownership-unresolved", path=path + ".table_index")
        targets.add(ti)
        if not seal_result or "result_sha256" in item:
            # seal_result: the answer's own mapping (correctable); otherwise
            # the stored, sealed mapping.
            _binding(item, "result_sha256", text_sha256(result), path=path, correctable=seal_result)
        _string(item, "evidence", path=path)
        table = tables[ti]
        for key in ("source_rows", "source_cols"):
            if type(item.get(key)) is not int or item[key] < 1:
                raise FidelityError("mapping source dimensions must be positive integers", path=path + "." + key)
        cell = grids.get(rid)
        if cell and (item["source_rows"], item["source_cols"]) != (cell["rows"], cell["cols"]):
            raise FidelityError("mapping source dimensions differ", path=path)
        axes = item.get("orientation")
        if axes not in {"identity", "transpose"} or orientation == "as-printed" and axes != "identity":
            raise FidelityError("mapping axis change is not permitted", path=path + ".orientation")
        dims = (item["source_rows"], item["source_cols"])
        if axes == "transpose":
            dims = dims[::-1]
        if table.problems or (table.rows, table.cols) != dims:
            raise FidelityError("mapping candidate table dimensions/markup differ", code="coverage-structure-mismatch", path=path)
        for feature in inventory["features"]:
            source = feature.get("cell", {})
            if feature["region_id"] != rid or source.get("kind") != "grid":
                continue
            geom = tuple(source[k] for k in ("row", "col", "rowspan", "colspan"))
            if axes == "transpose":
                geom = (geom[1], geom[0], geom[3], geom[2])
            if geom not in table.layout():
                raise FidelityError("mapping cell span/ownership differs", code="cell-ownership-unresolved", path=path)
        sealed.append({**item, "result_sha256": text_sha256(result)})
    return sealed


def rebind_unchanged_tables(before: str, after: str, mappings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Only byte-bound, same ordered logical cells can survive layout markup."""
    left, right = _tables.find_tables(before), _tables.find_tables(after)
    if len(left) != len(right):
        return []
    if any(a.problems or b.problems or a.layout() != b.layout()
           or compact(a.caption) != compact(b.caption)
           or [compact(c.text) for c in a.cells] != [compact(c.text) for c in b.cells]
           for a, b in zip(left, right)):
        return []
    return [{**m, "result_sha256": text_sha256(after)} for m in mappings
            if m.get("result_sha256") == text_sha256(before)]


def _answer(client: Any, system: str, prompt: dict[str, Any], image: bytes, max_tokens: int,
            kind: str, consume: Any) -> tuple[Any, Mapping[str, Any]]:
    raw, metrics = client.ask_answering(system, json.dumps(prompt, ensure_ascii=False), image, max_tokens, kind=kind)
    try:
        return consume(raw), metrics
    except FidelityError as error:
        error.rejected_answer = raw
        if not error.correctable:
            raise
        correction = {"original_answer_sha256": text_sha256(raw), "original_answer": raw,
                      "validation_error": error.diagnostic(), "correction_attempt": 1}
        retry_prompt = {**prompt, **correction}
        corrected, second_metrics = client.ask_answering(
            system + "\n上一回答未符合契約；只允許呢一次修正。保留原圖同所有 bindings；真係未清楚就報 uncertain。",
            json.dumps(retry_prompt, ensure_ascii=False), image, max_tokens, kind=kind + "-correction")
        try:
            result = consume(corrected)
        except FidelityError as terminal:
            terminal.correction = {**correction, "corrected_answer": corrected,
                                   "corrected_answer_sha256": text_sha256(corrected), "final_error": terminal.diagnostic()}
            raise
        evidence = result[1] if isinstance(result, tuple) else result
        evidence["correction"] = {**correction, "corrected_answer_sha256": text_sha256(corrected)}
        # Preserve every underlying call and unknown values for the caller.
        return result, {"fidelity_attempt_metrics": [metrics, second_metrics]}


def inventory_page(client: Any, image: bytes, drafts: list[str], max_tokens: int) -> tuple[dict[str, Any], Mapping[str, Any]]:
    prompt = {"contract_version": CONTRACT_VERSION, "source_image_sha256": hashlib.sha256(image).hexdigest(), "drafts": drafts}
    def consume(raw: str) -> dict[str, Any]:
        result = parse_inventory(raw, image)
        _current(result, correctable=True)
        return result
    return _answer(client, INVENTORY_SYSTEM, prompt, image, max_tokens, "page-inventory", consume)


def review_page(client: Any, image: bytes, candidate: str, inventory: Mapping[str, Any],
                max_tokens: int, *, stage: str, table_orientation: str = "auto") -> tuple[str, dict[str, Any], Mapping[str, Any]]:
    if stage not in {"content-recovery", "coverage-review"}:
        raise ValueError("unknown content fidelity stage")
    _current(inventory)
    if inventory["source_image_sha256"] != hashlib.sha256(image).hexdigest():
        raise FidelityError("inventory belongs to a different source image", code="stale-evidence", correctable=False)
    _binding(inventory, "inventory_sha256", inventory_sha256(inventory))
    prompt = {"contract_version": CONTRACT_VERSION, "source_image_sha256": inventory["source_image_sha256"],
              "inventory_sha256": inventory["inventory_sha256"], "candidate_sha256": text_sha256(candidate),
              "candidate": candidate, "inventory": inventory, "stage": stage, "table_orientation": table_orientation}
    def consume(raw: str) -> tuple[str, dict[str, Any]]:
        result, record = apply_review(raw, candidate, inventory, table_orientation=table_orientation)
        # An honest uncertain answer is terminal evidence, not an invitation to
        # keep asking. A claimed complete answer contradicting exact checks gets
        # one same-bound repair, with the original answer preserved.
        if record["verdict"] == "complete" and (record["feature_losses"] or record["empty_retained_content"] or record["unmapped_table_regions"]):
            raise FidelityError("complete review contradicts retained content/ownership: " + ",".join(record["feature_losses"]),
                                code="coverage-structure-mismatch", path="$.verdict")
        return result, record
    (result, record), metrics = _answer(client, REVIEW_SYSTEM, prompt, image, max_tokens, stage, consume)
    return result, record, metrics


def carries_record(record: Mapping[str, Any]) -> bool:
    """Whether a sealed page holds content-fidelity evidence at all.  Pages
    proofread with the switch off (the default) and pages sealed before it
    existed hold none, and nothing requires it of them."""
    return any(key in record for key in ("contentFidelityContract", "pageInventory", "coverageReview",
                                         "contentFidelityStatus"))


def _reviewed_text(record: Mapping[str, Any], markdown: str, accepted: set[str]) -> str:
    """The text the coverage review is bound to: the sealed Markdown, or, on a
    page the table stitch rewrote, the text it was proofread as
    (tableStitch.before), which the stitch binds to the review
    (tableStitch.coverageBinding) and to the Markdown it wrote in its place."""
    if text_sha256(markdown) in accepted:
        return markdown
    stitch = record.get("tableStitch")
    if isinstance(stitch, Mapping) and isinstance(stitch.get("before"), str):
        before = stitch["before"]
        binding = stitch.get("coverageBinding") or {}
        if (text_sha256(before) in accepted and isinstance(binding, Mapping)
                and binding.get("reviewedSha256") == text_sha256(before)
                and binding.get("stitchedSha256") == text_sha256(markdown)):
            return before
    raise FidelityError("result_sha256 is stale or belongs to different evidence", code="stale-evidence",
                        path="$.result_sha256", correctable=False)


def validate_completed_record(record: Mapping[str, Any], markdown: str, image_sha256: str) -> None:
    """Completion consumer: historical seals never count as current v2 review.

    A page whose fidelity stages could not be settled is sealed with
    contentFidelityStatus "doubt" and a DOUBT_KIND doubt: what evidence it
    holds must still be bound to this image and text, but it need not be
    complete."""
    if record.get("contentFidelityContract") != CONTRACT_VERSION:
        raise FidelityError("page lacks current content fidelity contract", code="historical-contract", correctable=False)
    doubt = record.get("contentFidelityStatus") == "doubt"
    if doubt and not any(isinstance(d, Mapping) and d.get("kind") == DOUBT_KIND for d in record.get("doubts") or []):
        raise FidelityError("content fidelity doubt is not among the page's doubts", code="coverage-unresolved",
                            correctable=False)
    if record.get("contentFidelityStatus") not in (None, "complete", "doubt"):
        raise FidelityError("invalid contentFidelityStatus", correctable=False)
    if doubt and record.get("pageInventory") is None:
        return
    inventory = record.get("pageInventory") or {}
    _current(inventory)
    inventory = _parse_inventory(json.dumps(inventory, ensure_ascii=False), image_sha256)
    _binding(inventory, "source_image_sha256", image_sha256)
    _binding(inventory, "inventory_sha256", inventory_sha256(inventory))
    if record.get("mode") == "blank-page" and not doubt:
        if inventory.get("page_state") != "blank" or inventory.get("regions") or inventory.get("features") or inventory.get("issues") or markdown:
            raise FidelityError("blank page contract is not empty")
        return
    if doubt and record.get("coverageReview") is None:
        return
    review = record.get("coverageReview") or {}
    _current(review)
    if not re.fullmatch(r"[0-9a-f]{64}", str(review.get("candidate_sha256", ""))):
        raise FidelityError("review candidate hash binding is missing", code="stale-evidence", correctable=False)
    rows = review.get("regions")
    if (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)
            or len(rows) != len(inventory["regions"])
            or {r.get("id") for r in rows} != {r["id"] for r in inventory["regions"]}
            or any(r.get("status") not in {"covered", "recovered", "excluded", "uncertain"} for r in rows)):
        raise FidelityError("coverage answer omitted inventory regions")
    for i, row in enumerate(rows):
        _string(row, "evidence", path=f"$.regions[{i}]")
    kinds = {r["id"]: r["kind"] for r in inventory["regions"]}
    if any(r["status"] == "excluded" and kinds[r["id"]] not in {"furniture", "illustration"} for r in rows):
        raise FidelityError("body content cannot be excluded as furniture")
    for key, expected in (("source_image_sha256", image_sha256), ("inventory_sha256", inventory["inventory_sha256"])):
        _binding(review, key, expected)
    if doubt:
        # An unresolved review's edits may have been set aside: the page then
        # holds the candidate it was asked about.
        _reviewed_text(record, markdown, {str(review.get("result_sha256")), str(review.get("candidate_sha256"))})
        return
    markdown = _reviewed_text(record, markdown, {str(review.get("result_sha256"))})
    mappings = validate_table_mappings(review.get("table_mappings"), markdown, inventory, review.get("table_orientation", "auto"))
    if (review.get("complete") is not True or review.get("verdict") != "complete"
            or resolve_issues(review.get("resolutions"), inventory, review.get("regions", []))
            or any(r.get("status") == "uncertain" for r in review.get("regions", []))
            or {m["region_id"] for m in mappings} != {r["id"] for r in inventory["regions"] if r["kind"] == "table"}
            or feature_losses(markdown, inventory, mappings)
            or any(r["kind"] not in {"furniture", "illustration"} for r in inventory["regions"]) and not character_counts(markdown)
            or review.get("empty_retained_content") or review.get("feature_losses")):
        raise FidelityError("full-image coverage is incomplete or belongs to other content", code="coverage-unresolved", correctable=False)
