"""A printed chart - a brace tree, a classification or organisation chart -
read from text in any form a person or a model may draw it: a nested list, an
indented or box-drawn tree (a file tree), a sideways brace drawing, a Mermaid
graph.

The regression suite scores an output's chart against a golden with it
(golden_tree).

Standard library only: the regression runner imports this.
"""
from __future__ import annotations

import re
import unicodedata


def cjk(text: str) -> str:
    # The character classes the merger keeps: BMP unified ideographs,
    # Extension B and beyond, and 〇々 (as tests/run_regression.py counts them).
    return "".join(c for c in text
                   if "㐀" <= c <= "鿿" or "\U00020000" <= c <= "\U0003134F" or c in "〇々")


_TREE_NOTE = re.compile(r"[（(]([^（）()]*)[）)]")
_LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*\S)")
_BOX_PREFIX = re.compile(r"^([\s│├└┌┬┼┤┐┘─━┃┣┗┏╰╭|`+\\-]*)([^\s│├└┌┬┼┤┐┘─━┃┣┗┏╰╭|`+\\-].*)$")
_MERMAID_NODE = re.compile(r'([A-Za-z0-9_]+)\s*(?:\[\s*"?([^\]"]*)"?\s*\]|\(\s*"?([^)"]*)"?\s*\)|\{\s*"?([^}"]*)"?\s*\})')
# An arrow, with the |label| Mermaid allows after it kept out of the child's name.
_MERMAID_EDGE = re.compile(r"(?:-{2,}>|={2,}>|-{3,}|-\.->)\s*(?:\|[^|]*\|)?|--\s*\|[^|]*\|\s*>?")


def tree_label(text: str) -> tuple[str, list[str]]:
    """A tree node's name (its characters outside brackets) and its bracketed notes."""
    notes = [cjk(n) for n in _TREE_NOTE.findall(text) if cjk(n)]
    return cjk(_TREE_NOTE.sub("", text)), notes


def _edges_from_lines(lines: list[tuple[int, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """Parent->child edges from (depth, text) lines: a line's parent is the nearest
    earlier line that is less deep."""
    edges, notes, stack = [], [], []
    for depth, text in lines:
        name, found = tree_label(text)
        notes += found              # an item that is only a note is still a note
        if not name:
            continue
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            edges.append((stack[-1][1], name))
        stack.append((depth, name))
    return edges, notes


_BOX = frozenset("┌├└┬┼┤│─┐┘┴━┃┣┗┏┳╋┫┻")


def brace_tree(text: str, box_width: int) -> tuple[list[tuple[str, str]], list[str]]:
    """Edges of a tree drawn sideways with box-drawing characters, as a printed
    brace chart is (the parent in the middle of a vertical bar, the children to
    its right, one per ┌ ├ ┼ └).  Columns are counted in display width, with
    box-drawing characters BOX_WIDTH wide (2 where a CJK font draws them wide).
    A group is a run of lines with a vertical connector in one column; its
    parent is the name just left of the junction (┼ or ┤), its children the
    first name right of the connector on each ┌ ├ ┼ └ line.  Bracketed notes
    that run over several lines are notes, not names."""
    lines = []
    for line in text.splitlines():
        cells, col = [], 0
        for ch in line:
            cells.append((col, ch))
            col += box_width if ch in _BOX else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        lines.append(cells)
    grid = [{c: ch for c, ch in cells} for cells in lines]
    tokens: list[list[list]] = []
    for cells in lines:
        row, cur = [], None
        for c, ch in cells:
            if ch.isspace() or ch in _BOX or ch in "|+`":
                if cur:
                    row.append(cur)
                cur = None
            elif cur is None:
                cur = [c, ch]
            else:
                cur[1] += ch
        if cur:
            row.append(cur)
        tokens.append(row)
    # A token is part of a note when it has no name outside brackets, or when it
    # continues a bracket opened a line or more above at about its column.
    note_tokens, notes, open_at = set(), [], {}     # open_at: column -> the note's text so far
    for i, row in enumerate(tokens):
        still_open = {}
        for start, word in row:
            opens = word.count("（") + word.count("(") - word.count("）") - word.count(")")
            inside = next((x for x in open_at if abs(start - x) <= 4), None)
            # A bracket this token opens and does not close starts a note: the
            # name is what comes before it, and the note is anchored at its column.
            cut = min((k for k in (word.find("（"), word.find("(")) if k >= 0), default=-1) if opens > 0 else -1
            head = word[:cut] if cut >= 0 else word
            name, found = tree_label(head)
            if inside is not None or not name:
                note_tokens.add((i, start))
                text = (open_at.get(inside, "") if inside is not None else "") + word
                if opens < 0 or (inside is None and opens == 0):
                    notes.append(cjk(text))            # the note closes here
                else:
                    still_open[inside if inside is not None else start] = text
            else:
                notes += found
                if cut >= 0:
                    width = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in head)
                    still_open[start + width] = word[cut:]
        # A note stays open while its column continues on the next line.
        for x, text in open_at.items():
            if x not in still_open and not any(abs(s - x) <= 4 for s, _ in row):
                notes.append(cjk(text))
        open_at = still_open
    edges = []
    for i, row in enumerate(grid):
        for c, ch in row.items():
            if ch != "┌":
                continue
            bottom = i
            while bottom + 1 < len(grid) and grid[bottom + 1].get(c) in set("│├┼┤└"):
                bottom += 1
                if grid[bottom].get(c) == "└":
                    break
            if grid[bottom].get(c) != "└":
                continue
            junction = next((j for j in range(i, bottom + 1) if grid[j].get(c) in ("┼", "┤")), None)
            if junction is None:
                continue
            left = [(s, w) for s, w in tokens[junction] if s < c and (junction, s) not in note_tokens]
            if not left:
                continue
            parent = tree_label(max(left)[1])[0]
            for j in range(i, bottom + 1):
                if grid[j].get(c) not in ("┌", "├", "┼", "└"):
                    continue
                right = [(s, w) for s, w in tokens[j] if s > c]
                if right and (j, min(right)[0]) not in note_tokens:
                    child = tree_label(min(right)[1])[0]
                    if parent and child:
                        edges.append((parent, child))
    return edges, [n for n in notes if n]


def tree_parses(text: str) -> dict[str, tuple[list[tuple[str, str]], list[str]]]:
    """Every tree the text can be read as: a nested list, an indented or box-drawn
    tree (a file tree), a Mermaid graph.  The checker keeps whichever matches the
    golden best, so the output may draw the chart in any of these forms."""
    lines = text.splitlines()
    out = {}
    items = [(len(m.group(1).expandtabs(4)), m.group(2)) for m in map(_LIST_ITEM.match, lines) if m]
    if items:
        out["list"] = _edges_from_lines(items)
    boxed = []
    for line in lines:
        m = _BOX_PREFIX.match(line.rstrip())
        if m and cjk(m.group(2)):
            boxed.append((len(m.group(1).expandtabs(4)), m.group(2)))
    if boxed:
        out["indent"] = _edges_from_lines(boxed)
    labels, edges, notes = {}, [], []
    for block in re.findall(r"```mermaid(.*?)```", text, re.S) or ([text] if "-->" in text else []):
        for line in block.splitlines():
            for m in _MERMAID_NODE.finditer(line):
                label = next(g for g in m.groups()[1:] if g is not None) if any(m.groups()[1:]) else m.group(1)
                labels[m.group(1)] = label
            parts = [p.strip() for p in _MERMAID_EDGE.split(line)]
            ids = [re.match(r"[A-Za-z0-9_]+", p).group(0) if re.match(r"[A-Za-z0-9_]+", p) else p for p in parts]
            if len(ids) > 1:
                edges += list(zip(ids, ids[1:]))
    for width in (2, 1):
        drawn = brace_tree(text, width)
        if drawn[0] and len(drawn[0]) > len(out.get("brace", ([], []))[0]):
            out["brace"] = drawn
    if edges:
        named = []
        for a, b in edges:
            na, fa = tree_label(labels.get(a, a))
            nb, fb = tree_label(labels.get(b, b))
            notes += fa + fb
            if na and nb:
                named.append((na, nb))
        out["mermaid"] = (named, notes)
    return out
