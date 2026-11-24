"""Traditional/simplified and variant-equivalent character rulings and seals."""

from __future__ import annotations

from collections import Counter
import itertools
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence


from jyut_ocr.core.common import (  # noqa: E402
    canonical_json_bytes, sha256_bytes, sha256_file,
)
from jyut_ocr.paths import reference_path  # noqa: E402
from jyut_ocr.proofread.core import (  # noqa: E402
    CJK, facade_attr,
)


VARIANT_LIST = reference_path("variant-equivalents.json")
_VARIANT_RULINGS: dict[frozenset, bool] | None = None
# OpenCC's list of allographs: characters Unicode encodes at different code
# points that are one character (淸/清, 愼/慎, 旣/既).  Second to the user's own
# rulings: where both cover a pair they agreed on all 15 pairs, and it groups
# none of the pairs dictionaries and the model got wrong (刺/剌, 着/著, 已/巳)
# nor the two the user ruled apart (碍/礙, 赶/趕).
ALLOGRAPHS = VARIANT_LIST.with_name("opencc-allographs.txt")
_ALLOGRAPH_PAIRS: frozenset | None = None
# The hash of the bytes each list was parsed from, for the seal: the lists are
# read once per run, so a file edited mid-run must not be sealed as the one
# that decided the pages.
_LIST_SHA256: dict[str, str] = {}


def user_variant_rulings(path: Path = VARIANT_LIST) -> dict[frozenset, bool]:
    """Every pair the user ruled on: True = two forms of one character, False = not.

    A class of three forms (刧/劫/刼) rules each of its three pairs.  A missing
    or unreadable list rules nothing, so the model decides as before.
    """
    global _VARIANT_RULINGS
    if _VARIANT_RULINGS is None:
        ruled: dict[frozenset, bool] = {}
        try:
            raw = path.read_bytes()
            _LIST_SHA256["variantList"] = sha256_bytes(raw)
            classes = json.loads(raw.decode("utf-8")).get("classes") or []
        except (OSError, ValueError):
            classes = []
        for entry in classes:
            forms = [f for f in entry.get("forms") or [] if isinstance(f, str) and len(f) == 1]
            for x, y in itertools.combinations(forms, 2):
                if x != y:
                    ruled[frozenset((x, y))] = bool(entry.get("acceptable"))
        _VARIANT_RULINGS = ruled
    return _VARIANT_RULINGS


def opencc_allographs(path: Path | None = None) -> frozenset:
    """Every pair of characters one line of the allograph list puts together.

    A missing or unreadable list contributes nothing, so classification falls
    through to the profile and the model as before.
    """
    global _ALLOGRAPH_PAIRS
    if path is not None or _ALLOGRAPH_PAIRS is None:
        pairs: set[frozenset] = set()
        try:
            raw = (path or ALLOGRAPHS).read_bytes()
            lines = raw.decode("utf-8").splitlines()
            if path is None:
                _LIST_SHA256["allographs"] = sha256_bytes(raw)
        except (OSError, UnicodeError):
            lines = []
        for line in lines:
            forms = [c for c in line.strip() if not c.isspace()]
            if not forms or line.startswith("#"):
                continue
            pairs.update(frozenset(p) for p in itertools.combinations(dict.fromkeys(forms), 2))
        if path is not None:
            return frozenset(pairs)
        _ALLOGRAPH_PAIRS = frozenset(pairs)
    return _ALLOGRAPH_PAIRS


# OpenCC's table of traditional characters and their simplified forms
# (TSCharacters.txt), vendored byte for byte and pinned like the allograph
# list: a character it maps only to other characters exists only in
# traditional script (後 -> 后; not 乾, which simplified script writes too).
# In a book printed in simplified script (simplified_script) it tells the
# pairs whose forms differ in script alone, written as engine B read them where
# the book's rulings leave them open (resolve_variant), and the form an answer
# in traditional script is written in (simplified_as_read).  The lint reads it
# too (TEXT-TRADITIONAL).
TS_CHARACTERS = VARIANT_LIST.with_name("opencc-ts-characters.txt")
_TRADITIONAL_FORMS: dict[str, str] | None = None


def traditional_forms() -> dict[str, str]:
    """Each character only traditional script has -> its simplified forms, in
    the table's order (崙 -> 仑𪨧).  A missing or unreadable table gives none."""
    global _TRADITIONAL_FORMS
    if _TRADITIONAL_FORMS is None:
        try:
            lines = facade_attr("TS_CHARACTERS", TS_CHARACTERS).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            lines = []
        table = {}
        for line in lines:
            key, _, values = line.partition("\t")
            forms = values.split()
            if not line.startswith("#") and len(key) == 1 and forms and key not in forms:
                table[key] = "".join(forms)
        _TRADITIONAL_FORMS = table
    return _TRADITIONAL_FORMS


def simplified_form(text: str) -> str:
    """TEXT with each character only traditional script has in its first
    simplified form (來 -> 来, 崙 -> 仑): for comparing an answer in
    traditional script with text printed in simplified script, never for
    writing."""
    table = traditional_forms()
    return "".join(table[c][0] if c in table else c for c in text)


def simplified_script(texts: Iterable[str]) -> bool:
    """Whether a book is printed in simplified script: the simplified forms of
    the characters only traditional script has outnumber those characters.
    Measured on both engines' drafts of five books (2026-10-10): 500 to 1,240
    times as many in three simplified archive volumes (a multi-volume archival compilation 2, 3, 5), about a
    seventh as many in two traditional books (后, 干, 里 are traditional
    characters too)."""
    table = traditional_forms()
    simplified = set("".join(table.values()))
    traditional = plain = 0
    for text in texts:
        for c in text:
            if c in table:
                traditional += 1
            elif c in simplified:
                plain += 1
    return plain > traditional


def simplified_as_read(answer: str, a: str, b: str) -> str:
    """ANSWER, 裁決's at a spot engine A read A and engine B read B, in a
    book printed in simplified script: each character only traditional
    script has that engine B did not read there is written in its simplified
    form where an engine read that form there (懸 where engine B read 悬 is
    悬); where neither engine read the character or any form of it, in its
    one simplified form when the table gives just one (蕆 where the engines
    read 藏 and 葳 is 蒇).  Otherwise as answered: 崙 where the engines read
    崙 and 嵛 stays 崙, and so does 崙 neither engine read, with two forms
    (仑 and 𪨧).  Engine B reads the script as printed (resolve_variant);
    裁決 often answers in traditional script.  The one form where neither
    engine read one, measured on the 2,051 phase 3 records of a multi-volume archival compilation 1-4
    whose printed reading is known (2026-10-25): 31 answers now as printed
    (30 of them 蒇, the engines reading 藏, 葳, 臧 or 赦), 5 no longer - 2
    on a handwritten plate printed in traditional script, 2 a name's
    character the measurement's reference held in traditional script, which
    the print gives in simplified script elsewhere (likely no break), 1
    title-page noise.  A name printed in traditional script is one an
    engine read there and stays: every such character converted instead
    fixes 38 and breaks 29."""
    table = traditional_forms()
    return "".join(c if c in b else next((form for form in table.get(c, "") if form in a + b),
                                         table[c] if c not in a and len(table.get(c, "")) == 1 else c)
                   for c in answer)


def converted_against_b(answer: str, a: str, b: str) -> str:
    """The characters simplified_as_read writes for ANSWER in a form engine
    B did not read, which a person checks (answer-script-converted): engine
    A's form where engine B read another character, none of the answer's
    forms in either script (塵 written 尘 as engine A read it, where engine B
    read 廑) - an answer in the wrong script that settled a disagreement
    against engine B's reading - and the table's one form, which neither
    engine read (蕆 written 蒇 where engine A read 藏 and engine B 葳 or
    nothing).  Measured on a multi-volume archival compilation 2, 3 and 5 (2026-10-10): 18 of the 91
    answers written in an engine's form, 11 of them misreadings either way
    (9 printed as engine B read them), 6 engine A's right reading, 1
    unclear; of the other 73, 3 were misreadings (engine B's 恒 where engine
    A read the printed 恇).  The one form is doubted where engine B read
    nothing too: TEXT-TRADITIONAL flagged the character while it stayed
    traditional, and a misreading there has no other flag (a multi-volume archival compilation 1 scan 66:
    相連變化 answered where engine A read the printed 水为先地下 and engine
    B nothing; replayed on the sealed records of a multi-volume archival compilation 1-5, 2026-10-26, the
    one spot this adds)."""
    table = traditional_forms()
    # What engine B read there, each character with its simplified forms.
    read_b = "".join(x + table.get(x, "") for x in b)
    return "".join(w for c, w in zip(answer, simplified_as_read(answer, a, b))
                   if c != w and (w not in a + b or (b and not any(form in read_b for form in table.get(c, "")))))


def variant_source(a: str, b: str) -> str | None:
    """Which list decides whether two characters are one: "user" (the user's
    own ruling, either way), "opencc" (an allograph pair), "script" (one is
    the other's form in simplified script, by OpenCC's table), or None."""
    pair = frozenset((a, b))
    if pair in user_variant_rulings():
        return "user"
    if pair in opencc_allographs():
        return "opencc"
    table = traditional_forms()
    if b in table.get(a, "") or a in table.get(b, ""):
        return "script"
    return None


def classify_pair(a: str, b: str) -> str:
    """'variant' when two single characters are forms of one character, else 'different'.

    Variant-form disputes (粵/粤, 既/旣) are not reading errors: which form a
    page prints is a question the vision judge was measured unable to answer
    (the same pair in one book's single type split 19:9), so they are kept
    out of the engine calibration and resolved per book instead.  Whether a
    pair IS a variant pair is knowledge, not vision, and only the lists are
    trusted with it: the user's own rulings (references/variant-equivalents.json)
    decide first, then OpenCC's allograph list (references/opencc-allographs.txt),
    then OpenCC's table of traditional characters and their simplified forms
    (references/opencc-ts-characters.txt).  A pair none of them covers is two
    characters, read from the image like any other disagreement.

    Until 2026-10-13 the model was asked about the pairs no list covers, and
    its answer did not hold: it called 刺/剌, 盡/藎, 祛/袪, 祇/祗 and 监/鉴
    forms of one character, and a multi-volume archival compilation 3 was delivered with 仰祈圣鉴 written
    圣监 four times, with no doubt.  On the six delivered books such pairs
    decided 442 sites: 71 were two different characters, 162 two characters
    some prints use for each other (于/於, 並/幷: only the print can tell),
    199 forms of one character (劫/刼, 出/岀), 10 misreadings or unsure.
    On a book with no engine preference (all six) those sites are now read
    from the image: 441 more 裁決 calls, about 1% of the model's time on five
    of the books and 5% on the sixth (measured 2026-10-13 on their seals); a
    form of one character no list holds can come out in either form.
    """
    a, b = CJK(a), CJK(b)
    if len(a) != 1 or len(b) != 1 or a == b:
        return "different"
    ruled = user_variant_rulings().get(frozenset((a, b)))
    if ruled is not None:
        return "variant" if ruled else "different"
    return "variant" if variant_source(a, b) else "different"


def variant_segment(seg: Mapping[str, Any]) -> list[tuple[str, str]] | None:
    """The variant pairs a disagreement consists of, or None if it is not one.

    A segment of equal length whose every differing position is a variant
    pair (況粵/况粤 is two) is a variant dispute.
    """
    a, b = CJK(seg["a"]), CJK(seg["b"])
    if not a or len(a) != len(b):
        return None
    pairs = [(x, y) for x, y in zip(a, b) if x != y]
    if not pairs or any(classify_pair(x, y) != "variant" for x, y in pairs):
        return None
    return pairs


_FOLD: dict[str, str] | None = None


def variant_fold(text: str) -> str:
    """Each character as one representative of its variant class: the user's
    pairs ruled one character, then OpenCC's allographs unless the user ruled
    the pair apart.  For finding where two readings of the same print meet
    (爲 in one engine and 為 in the other break every anchor across them),
    never for writing: the characters written are the engines'."""
    global _FOLD
    if _FOLD is None:
        ruled = user_variant_rulings()
        pairs = [tuple(pair) for pair, same in ruled.items() if same and len(pair) == 2]
        pairs += [tuple(pair) for pair in opencc_allographs() if len(pair) == 2 and pair not in ruled]
        parent: dict[str, str] = {}

        def root(char: str) -> str:
            while parent.get(char, char) != char:
                char = parent[char]
            return char

        for x, y in pairs:
            rx, ry = root(x), root(y)
            if rx != ry:
                parent[max(rx, ry)] = min(rx, ry)
        _FOLD = {char: root(char) for char in parent}
    return "".join(_FOLD.get(char, char) for char in text)


def variant_ruling_by(entry: Mapping[str, Any]) -> str:
    """Who made one ruling of a book's variant-rulings.json: "model" or
    "person".  The model's are those of the variant vote book_profile.py ran
    until 2026-10-12, in the files it wrote then; they are applied as a
    person's are.  A ruling the model wrote whose printed form a person has
    since changed is the person's: it no longer says what the model chose
    (modelPrinted)."""
    if entry.get("ruledBy") == "model" and entry.get("printed") == entry.get("modelPrinted"):
        return "model"
    return "person"


def variant_person_unruled(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The entries of a rulings file's modelUnruled (pairs the variant vote
    left unruled, in a file it wrote; it never gave them a printed form) that
    a person gave a printed form of the pair: that person's rulings, where
    they found the pair."""
    out = []
    for entry in raw.get("modelUnruled") or []:
        if not isinstance(entry, Mapping):
            continue
        forms = [CJK(str(f)) for f in entry.get("forms") or []]
        if len(forms) == 2 and CJK(str(entry.get("printed") or "")) in forms:
            out.append(entry)
    return out


def variant_rulings_seal(raw: Mapping[str, Any], path: Path, rulings: Mapping[frozenset, str],
                         ruled_by: Mapping[frozenset, str]) -> str:
    """The hash of a book's variant rulings its pages seal
    (decision_provenance's variantRulingsSha256).

    A file a person wrote is sealed by its bytes, as it always was.  A file
    the variant vote wrote (book_profile.py, until 2026-10-12) was written
    again every time the book was profiled, with that run's own record - its
    time, seconds and usage, each crop's reply - so its bytes changed when no
    ruling did, and a resumed run would have refused every page it sealed.
    Such a file is sealed by what decides the pages: RULINGS, each pair's
    printed form as loaded, and RULED_BY, who ruled it (which the pages
    record); the pages sealed under it stay current."""
    entries = [e for e in raw.get("rulings") or [] if isinstance(e, Mapping)]
    if not ("modelVote" in raw or "modelUnruled" in raw or any(e.get("ruledBy") == "model" for e in entries)):
        return facade_attr("sha256_file", sha256_file)(path)
    return sha256_bytes(canonical_json_bytes(sorted(
        ["|".join(sorted(key)), printed, ruled_by.get(key, "person")] for key, printed in rulings.items())))


def resolve_variant(seg: Mapping[str, Any], arguments: Any) -> tuple[str, str, list[str]]:
    """Resolve a variant-form disagreement from the book's per-pair rulings.

    A pair without a ruling takes the engine the book's rulings favour (the
    engine whose forms were ruled printed more often); with no rulings at all,
    the reading preference.  Unruled pairs are reported, not hidden.  A
    ruling the model made (variant_ruling_by) is used as a person's is.
    In a book printed in simplified script (simplified_script), a pair
    without a ruling whose forms differ in script alone (one is the other's
    only in traditional script: 諭/谕, 並/并; traditional_forms) takes engine
    B's form, and is not reported unruled: engine B reads the script as
    printed, engine A does not.  Measured against the images of a multi-volume archival compilation 2, 3
    and 5 (2026-10-10): engine A read unit titles printed in simplified
    script in traditional script (a multi-volume archival compilation 3: 63 sites, which the engine A
    default wrote), and traditional forms printed in body text in simplified
    script (舖, 趲, 雋, 旂 on a multi-volume archival compilation 3; 並, 貲, 揀, 執 on a multi-volume archival compilation 5);
    engine B read the printed form at 29 of the 31 sites looked at (not 恥
    and 學, where engine A read 耻 and 学 as printed).

    The label is the segment's, which may hold several sites:
    variant-ruling when a person's rulings decided every site,
    variant-model-ruling when the model's did, variant-person-and-model-ruling
    when both did, variant-script when the print's script decided every site,
    and the engine the unruled sites took otherwise (a site the script
    decided counts as ruled there).  Which sites the model ruled is listed per
    site (variant_model_sites).
    """
    rulings = getattr(arguments, "variant_rulings", None) or {}
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    engine = getattr(arguments, "variant_engine", None) or (
        arguments.prefer_engine if arguments.prefer_engine in ("A", "B")
        else getattr(arguments, "fallback_engine", "A"))
    script = traditional_forms() if getattr(arguments, "simplified_script", False) else {}
    a, b = CJK(seg["a"]), CJK(seg["b"])
    out, unruled, ruled, by_model, by_script = [], [], 0, 0, 0
    for x, y in zip(a, b):
        if x == y:
            out.append(x)
            continue
        printed = rulings.get(frozenset((x, y)))
        if printed in (x, y):
            out.append(printed)
            ruled += 1
            by_model += ruled_by.get(frozenset((x, y))) == "model"
        elif y in script.get(x, "") or x in script.get(y, ""):
            out.append(y)
            by_script += 1
        else:
            out.append(y if engine == "B" else x)
            unruled.append(f"{x}|{y}")
    how = ("variant-script" if not unruled and not ruled
           else "variant-model-ruling" if not unruled and by_model == ruled
           else "variant-person-and-model-ruling" if not unruled and by_model
           else "variant-ruling" if not unruled
           else f"variant-unruled-engine-{engine}" if not ruled and not by_script
           else f"variant-partly-ruled-engine-{engine}")
    return "".join(out), how, unruled


def variant_model_sites(disagreements: Sequence[Mapping[str, Any]], arguments: Any) -> list[str] | None:
    """The variant pairs (engine A|engine B, one per site) a page wrote from
    the model's rulings, in every segment resolve_variant decided, whatever
    else the segment held; None when the book has no model ruling, so a page
    of a book ruled by a person alone is sealed as it always was."""
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    if "model" not in ruled_by.values():
        return None
    rulings = getattr(arguments, "variant_rulings", None) or {}
    return [f"{x}|{y}" for d in disagreements if str(d.get("resolved_by") or "").startswith("variant-")
            for x, y in zip(CJK(d.get("engineA") or ""), CJK(d.get("engineB") or ""))
            if x != y and ruled_by.get(frozenset((x, y))) == "model" and rulings.get(frozenset((x, y))) in (x, y)]


def variant_rulings_report(arguments: Any, model_sites: Mapping[str, int]) -> dict[str, Any]:
    """The run report's account of the book's variant rulings: the file, and
    which pairs a person ruled and which the model did, with the sites
    the model's rulings decided on the pages of this report (MODEL_SITES,
    engine A|engine B per site)."""
    ruled_by = getattr(arguments, "variant_ruled_by", None) or {}
    rulings = getattr(arguments, "variant_rulings", None) or {}
    labels = getattr(arguments, "variant_ruling_forms", None) or {}
    sites: Counter = Counter()
    for pair, n in model_sites.items():
        sites[frozenset(pair.split("|"))] += n
    # sha256 is what the pages sealed (variant_rulings_seal); fileSha256 the
    # file's bytes as read.
    out: dict[str, Any] = {"file": getattr(arguments, "variant_rulings_file", None),
                           "sha256": getattr(arguments, "variant_rulings_sha", "") or "",
                           "fileSha256": getattr(arguments, "variant_rulings_file_sha", "") or "",
                           "person": [], "model": []}
    for key, printed in rulings.items():
        row = {"pair": labels.get(key) or "|".join(sorted(key)), "printed": printed}
        if ruled_by.get(key) == "model":
            out["model"].append(dict(row, occurrences=sites.get(key, 0)))
        else:
            out["person"].append(row)
    out["model"].sort(key=lambda row: -row["occurrences"])
    return out
