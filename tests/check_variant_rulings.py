#!/usr/bin/env python3
"""Checks of a book's variant rulings.  The book profile lists the variant pairs, each with
crops of the printed glyph cut at the pair's own character, and rules none
(book_profile.py): a person may rule a pair in variant-rulings.json next to the profile, and
nothing waits for one.  Phase 3 writes each pair as the rulings say, and falls back for a
pair without one (proofread_pages.resolve_variant); in a book printed in simplified script,
a pair the rulings leave open whose forms differ in script alone takes engine B's form.  A
rulings file the model's variant vote wrote (book_profile.py ran it until 2026-10-12) is
read as before: its rulings are used like a person's and said to be the model's, a
person's ruling stands over them, a re-profile leaves the file alone, and the pages sealed
under it stay current.

No model, no GPU, no corpus: made-up books in temporary directories, and a stand-in model.

Usage: python3 tests/check_variant_rulings.py   (exits non-zero if any check fails)
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import book_profile as bp  # noqa: E402
import proofread_pages as pp  # noqa: E402
import stand_in  # noqa: E402

failures = 0


def check(name: str, got, want) -> None:
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


def rulings(root: Path) -> dict:
    return json.loads((root / "variant-rulings.json").read_text(encoding="utf-8"))


# --- book_profile.py ----------------------------------------------------------------
# Three pages of different text (text repeated on every page is furniture), each with
# the four pairs, engine A reading one form and engine B the other.
OPENINGS = {1: "第一章總論", 2: "第二章各論", 3: "第三章附則"}
# Two more pages, for a book with more pages per pair than the profile keeps crops.
MORE_OPENINGS = {**OPENINGS, 4: "第四章罰則", 5: "第五章施行"}
TEXT_A = "此事爲國家計淸理財政甚麼事都要做到眞實"
TEXT_B = "此事為國家計清理財政甚麽事都要做到真實"


class BookModel:
    """The profile's calls answered: every pair is two forms of one character, no crop
    shows anything, no block has marks."""

    model, effort = "stand-in", None

    def __init__(self):
        self.calls: list[str] = []

    def ask_answering(self, system, user_text, image_bytes, max_tokens, kind="other"):
        self.calls.append(kind)
        if system == pp.ADJUDICATE_SYSTEM:
            return "定案：無\nRESEARCH: no", {}
        return "符號：無\n雙圈：冇\n圈點：冇\n點數：0", {}


def crop_stand_in(render, block, index, run, context=4, pad=0.02):
    page = int(Path(render).stem.split("-")[1])
    return f"{page}:{index}:{(MORE_OPENINGS[page] + TEXT_B)[index]}".encode("utf-8")


def profile_book(root: Path):
    model = BookModel()
    real_client, real_crop = pp.Client, pp.crop_glyph
    pp.Client = lambda *args, **kwargs: model
    pp.crop_glyph = crop_stand_in
    try:
        status = bp.main([str(root / "renders"), str(root / "a"), str(root / "b"),
                          str(root / "book-profile.json"), "--workers", "2",
                          "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT])
    finally:
        pp.Client, pp.crop_glyph = real_client, real_crop
    assert status == 0, status
    return model, json.loads((root / "book-profile.json").read_text(encoding="utf-8"))


def proofread_book(root: Path, overwrite: bool):
    """Phase 3 on the three pages under ROOT's book profile and rulings; returns
    the exit status and each page's status in the run report."""
    real_client = pp.Client
    pp.Client = lambda *args, **kwargs: stand_in.StandIn(census=0)
    try:
        status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                          "--draft2-directory", str(root / "b"), "--profile", str(root / "book-profile.json"),
                          "--pages", "1-3", "--report", str(root / "report.json"),
                          "--backend", "http", "--endpoint", stand_in.CLOSED_ENDPOINT,
                          *(["--overwrite"] if overwrite else [])])
    finally:
        pp.Client = real_client
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return status, [page["status"] for page in report["pages"]]


# A rulings file as the vote wrote it (the books proofread before 2026-10-12 hold one):
# the pairs it ruled, those it left unruled, and its run's record.
VOTED = {"book": "renders", "instructions": "per-pair variant-form rulings for this book", "ruledBy": "model",
         "rulings": [{"forms": ["淸", "清"], "printed": "清", "modelPrinted": "清", "ruledBy": "model",
                      "occurrences": 3, "votes": {"淸": 0, "清": 2, "cannotTell": 1}}],
         "modelUnruled": [{"forms": ["爲", "為"], "occurrences": 3, "votes": {"爲": 1, "為": 1, "cannotTell": 1},
                           "reason": "tie: 爲 1, 為 1 (1 could not tell)"}],
         "modelVote": {"generatedAt": "2026-10-08T00:50:35Z", "pairsRuled": 1, "pairsUnruled": 1, "calls": 6}}

PAGES = {page: ([stand_in.block(opening + TEXT_A, [0.3, 0.1, 0.4, 0.8])], opening + TEXT_B)
         for page, opening in OPENINGS.items()}
with stand_in.book(PAGES) as root:
    model, profile = profile_book(root)
    check("book_profile: no rulings file written", (root / "variant-rulings.json").exists(), False)
    # The four pairs are on the lists and no substitution is left to calibrate on: the
    # notation survey is the only call, and nothing asks which form is printed.
    check("book_profile: no call but the notation survey", sorted(set(model.calls)), ["notation"])
    check("book_profile: every pair listed, with its crops, and the note says a person rules them",
          (sorted(p["engineA"] + p["engineB"] for p in profile["variants"]["pairs"]),
           [len(p["crops"]) for p in profile["variants"]["pairs"]], profile["variants"]["note"]),
          (["淸清", "爲為", "眞真", "麼麽"], [3] * 4, bp.VARIANT_NOTE))
    (root / "variant-rulings.json").write_text(json.dumps(VOTED, ensure_ascii=False), encoding="utf-8")
    check("resume: proofread under a file the vote wrote", proofread_book(root, overwrite=True)[0], 0)
    page = json.loads((root / "out" / "page-0001.json").read_text(encoding="utf-8"))
    check("resume: its ruling is written and said to be the model's, the pair it left unruled falls back",
          ([(d["engineA"], d["resolved"], d["resolved_by"]) for d in page["engineDisagreements"]
            if d["resolved_by"].startswith("variant")], page.get("variantRuledByModel")),
          ([("爲", "為", "variant-unruled-engine-B"), ("淸", "清", "variant-model-ruling"),
            ("麼", "麽", "variant-unruled-engine-B"), ("眞", "真", "variant-unruled-engine-B")], ["淸|清"]))
    before = (root / "variant-rulings.json").read_bytes()
    profile_book(root)
    check("resume: a re-profile leaves the file byte for byte", (root / "variant-rulings.json").read_bytes(),
          before)
    check("resume: ... and the pages sealed under it current", proofread_book(root, overwrite=False),
          (0, ["skipped-current"] * 3))
    # Such a file is sealed by its rulings, not its bytes (variant_rulings_seal): the
    # vote wrote its own record anew on every profile.
    VOTED["modelVote"]["generatedAt"] = "2026-10-09T00:00:00Z"
    (root / "variant-rulings.json").write_text(json.dumps(VOTED, ensure_ascii=False), encoding="utf-8")
    check("resume: its record changed, no ruling - the pages current",
          proofread_book(root, overwrite=False), (0, ["skipped-current"] * 3))
    VOTED["rulings"][0]["printed"] = "淸"
    (root / "variant-rulings.json").write_text(json.dumps(VOTED, ensure_ascii=False), encoding="utf-8")
    check("resume: a ruling changed - the pages sealed under the old one refused",
          proofread_book(root, overwrite=False), (1, ["blocked"] * 3))
    # A person's file: its bytes are the seal, and a re-profile leaves it alone too.
    person = {"rulings": [{"forms": ["爲", "為"], "printed": "爲"}]}
    (root / "variant-rulings.json").write_text(json.dumps(person, ensure_ascii=False), encoding="utf-8")
    before = (root / "variant-rulings.json").read_bytes()
    profile_book(root)
    check("a person's rulings file: a re-profile leaves it byte for byte",
          (root / "variant-rulings.json").read_bytes(), before)

# The profile keeps VARIANT_CROPS_PER_PAIR crops per pair, each from another page.
with stand_in.book({page: ([stand_in.block(opening + TEXT_A, [0.3, 0.1, 0.4, 0.8])], opening + TEXT_B)
                    for page, opening in MORE_OPENINGS.items()}) as root:
    _, profile = profile_book(root)
    check("book_profile: five pages a pair, the crops it keeps",
          [sorted(c["page"] for c in pair["crops"]) for pair in profile["variants"]["pairs"]],
          [[1, 2, 3]] * 4)

# One disagreement holding three pairs (engine A 諭內閣, engine B 谕内阁, a heading on
# every page of 天地会 4): each pair's crop is cut at its own character, and the reading
# the profile records with it marks that character.  Measured: the reading marked the
# whole disagreement, so for 內/内 it put the spot where 諭 stands.
RUN_A, RUN_B = "奉上諭內閣著戶部撥銀", "奉上谕内阁著戶部撥銀"
with stand_in.book({page: ([stand_in.block(opening + RUN_A, [0.3, 0.1, 0.4, 0.8])], opening + RUN_B)
                    for page, opening in OPENINGS.items()}) as root:
    asked = []

    def crop_at(render, block, index, run, context=4, pad=0.02):
        asked.append((Path(render).stem, index, run, context))
        return f"{render}:{index}".encode("utf-8")

    real_crop, pp.crop_glyph = pp.crop_glyph, crop_at
    try:
        listed, _ = bp.collect_variants(bp.page_candidates(root / "renders", root / "a", root / "b"),
                                        root / "renders", root / "a", root / "b", [], root / "crops")
    finally:
        pp.crop_glyph = real_crop
    pairs = {pair["engineA"] + pair["engineB"]: pair for pair in listed["pairs"]}
    check("several pairs in one disagreement: each listed, cropped on each page",
          sorted((key, len(pair["crops"])) for key, pair in pairs.items()), [("內内", 3), ("諭谕", 3), ("閣阁", 3)])
    check("several pairs in one disagreement: each crop cut at its own character, context 2",
          sorted({(index, run, context) for page, index, run, context in asked if page == "page-0001"}),
          [(7, 1, 2), (8, 1, 2), (9, 1, 2)])
    check("several pairs in one disagreement: the reading marks the pair's own character",
          [pairs[key]["crops"][0]["context"] for key in ("諭谕", "內内", "閣阁")],
          ["總論奉上【】內閣著", "論奉上諭【】閣著戶", "奉上諭內【】著戶部"])

# --- phase 3: the rulings of a file the vote wrote are used like a person's, and said to
# be the model's ------------------------------------------------------------------------
PAGE_A = "此事爲國家計淸理財政"
PAGE_B = "此事為國家計清理財政"


def proofread(rulings_document=None, page_a=PAGE_A, page_b=PAGE_B):
    """One page of a book preferring engine A, proofread with RULINGS_DOCUMENT as
    its variant-rulings.json (none if None); returns the record, the page's
    Markdown and the run report."""
    blocks = [stand_in.block(page_a, [0.3, 0.1, 0.4, 0.8])]
    with stand_in.book({1: (blocks, page_b)}, profile={"preferEngine": "A"}) as root:
        if rulings_document is not None:
            (root / "variant-rulings.json").write_text(json.dumps(rulings_document, ensure_ascii=False),
                                                       encoding="utf-8")
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return record, markdown, report


def variant_rows(record):
    return [(d["engineA"], d["engineB"], d["resolved"], d["resolved_by"]) for d in record["engineDisagreements"]
            if d["resolved_by"].startswith("variant")]


PERSON = {"forms": ["爲", "為"], "printed": "為"}
MODEL = {"forms": ["淸", "清"], "printed": "清", "modelPrinted": "清", "ruledBy": "model",
         "votes": {"淸": 0, "清": 3, "cannotTell": 0}}
VOTED_UNRULED = {"ruledBy": "model",
                 "rulings": [{"forms": ["麼", "麽"], "printed": "麽", "modelPrinted": "麽", "ruledBy": "model"}],
                 "modelUnruled": [{"forms": ["爲", "為"], "printed": "為", "ruledBy": "person",
                                   "reason": "tie: 爲 1, 為 1 (1 could not tell)"}],
                 "modelVote": {"generatedAt": "2026-10-08T00:50:35Z", "pairsRuled": 1, "pairsUnruled": 1}}
record, markdown, report = proofread({"rulings": [PERSON, MODEL]})
check("phase 3: a model ruling is written like a person's", pp.CJK(markdown), "此事為國家計清理財政")
check("phase 3: each variant site says whose ruling decided it", variant_rows(record),
      [("爲", "為", "為", "variant-ruling"), ("淸", "清", "清", "variant-model-ruling")])
check("phase 3: the page lists the pairs the model ruled", record.get("variantRuledByModel"), ["淸|清"])
check("phase 3: the run report says which pairs the model ruled and which a person",
      {k: report["variantRulings"][k] for k in ("person", "model")},
      {"person": [{"pair": "爲|為", "printed": "為"}],
       "model": [{"pair": "淸|清", "printed": "清", "occurrences": 1}]})
# A person's ruling stands over the model's for the same pair, wherever it is in the file.
record, markdown, report = proofread({"rulings": [dict(PERSON, forms=["淸", "清"], printed="淸"), MODEL]})
check("phase 3: a person's ruling stands over the model's for the same pair",
      (variant_rows(record)[1], record.get("variantRuledByModel"), report["variantRulings"]["model"]),
      (("淸", "清", "淸", "variant-ruling"), None, []))
# A person who changes the printed form of a ruling the model wrote makes it theirs: it no
# longer says what the model chose (modelPrinted).
record, markdown, report = proofread({"rulings": [dict(MODEL, printed="淸")]})
check("phase 3: a model ruling whose printed form a person changed is theirs",
      (variant_rows(record)[1], record.get("variantRuledByModel"), report["variantRulings"]["person"],
       report["variantRulings"]["model"]),
      (("淸", "清", "淸", "variant-ruling"), None, [{"pair": "淸|清", "printed": "淸"}], []))
# Two pairs side by side are one segment with two sites: each site the model ruled is
# listed and counted whatever else the segment holds, and the segment's label says
# whose rulings decided it.
record, markdown, report = proofread({"rulings": [MODEL]}, "此事爲淸國家計理財政", "此事為清國家計理財政")
check("phase 3: a segment the model ruled in part - its model site listed and counted",
      (pp.CJK(markdown), variant_rows(record), record.get("variantRuledByModel"), report["variantRulings"]["model"]),
      ("此事爲清國家計理財政", [("爲淸", "為清", "爲清", "variant-partly-ruled-engine-A")], ["淸|清"],
       [{"pair": "淸|清", "printed": "清", "occurrences": 1}]))
record, markdown, report = proofread({"rulings": [PERSON, MODEL]}, "此事爲淸國家計理財政", "此事為清國家計理財政")
check("phase 3: a segment a person and the model ruled - labelled as both, the model's site listed",
      (pp.CJK(markdown), variant_rows(record), record.get("variantRuledByModel")),
      ("此事為清國家計理財政", [("爲淸", "為清", "為清", "variant-person-and-model-ruling")], ["淸|清"]))
record, markdown, report = proofread({"rulings": [MODEL, dict(PERSON, printed="爲", modelPrinted="爲", ruledBy="model")]},
                                     "此事爲淸國家計理財政", "此事為清國家計理財政")
check("phase 3: a segment the model ruled at every site",
      (variant_rows(record), record.get("variantRuledByModel")),
      ([("爲淸", "為清", "爲清", "variant-model-ruling")], ["爲|為", "淸|清"]))
# A person's printed form on a pair the vote left unruled (its modelUnruled entry) is
# theirs.
record, markdown, report = proofread(VOTED_UNRULED)
check("phase 3: a person's ruling on a modelUnruled entry is used as theirs",
      (variant_rows(record)[0], report["variantRulings"]["person"]),
      (("爲", "為", "為", "variant-ruling"), [{"pair": "爲|為", "printed": "為"}]))
# Without model rulings nothing new is sealed: the page is the one a person's rulings
# (or none) always made.
person_record, person_markdown, _ = proofread({"rulings": [PERSON, dict(MODEL, ruledBy="person")]})
check("phase 3: a person's rulings only - resolved as before, no model key on the page",
      (variant_rows(person_record), "variantRuledByModel" in person_record),
      ([("爲", "為", "為", "variant-ruling"), ("淸", "清", "清", "variant-ruling")], False))
bare_record, bare_markdown, bare_report = proofread()
check("phase 3: no rulings - the reading preference, the pairs queued, no model key",
      (variant_rows(bare_record), "variantRuledByModel" in bare_record, bare_report["variantQueue"],
       bare_report["variantRulings"]),
      ([("爲", "為", "爲", "variant-unruled-engine-A"), ("淸", "清", "淸", "variant-unruled-engine-A")], False,
       [{"pair": "爲|為", "occurrences": 1}, {"pair": "淸|清", "occurrences": 1}],
       {"file": None, "sha256": "", "fileSha256": "", "person": [], "model": []}))

# --- phase 3, a book printed in simplified script: a pair the rulings leave open whose
# forms differ in script alone takes engine B's form (resolve_variant) ----------------
# 天地会 3 scan 75 (kept folder, 2026-10-10): engine A read the unit title in traditional
# script, engine B as printed; the book's rulings ruled no pair, and engine A's forms were
# written.  天地会 5 scan 177: engine B read the printed 並, engine A 并.
TITLE_A, TITLE_B = "諭內閣著戶部酌拨銀一百萬兩解閩", "谕内阁著户部酌拨银一百万两解闽"
DATE = "乾隆五十二年七月十三日内阁奉上谕台湾现在剿捕贼匪"
BODY_A, BODY_B = "认真踩缉稽察并出示晓谕村庄百姓", "认真踩缉稽察並出示晓谕村庄百姓"
SCRIPT_PAIRS = {f"{x}|{y}": "variant" for x, y in zip(TITLE_A + BODY_A, TITLE_B + BODY_B) if x != y}


def proofread_simplified(rulings_document=None):
    """One page of 天地会 3's title and 天地会 5's sentence, engine A preferred
    (as 天地会 3's unruled pairs fell back to it); returns the record, the
    page's Markdown and the run report."""
    blocks = [stand_in.block(TITLE_A, [0.3, 0.1, 0.4, 0.05]), stand_in.block(DATE, [0.3, 0.2, 0.4, 0.05]),
              stand_in.block(BODY_A, [0.3, 0.3, 0.4, 0.05])]
    with stand_in.book({1: (blocks, "\n".join((TITLE_B, DATE, BODY_B)))},
                       profile={"preferEngine": "A", "pairClasses": SCRIPT_PAIRS}) as root:
        if rulings_document is not None:
            (root / "variant-rulings.json").write_text(json.dumps(rulings_document, ensure_ascii=False),
                                                       encoding="utf-8")
        record, markdown = stand_in.run(root, stand_in.StandIn(census=0))[1]
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    return record, markdown, report


record, markdown, report = proofread_simplified()
check("phase 3, simplified script: the open pairs are written as engine B read them",
      (pp.CJK(markdown), {d["resolved_by"] for d in record["engineDisagreements"] if d["resolved_by"].startswith("variant")},
       record["variantUnruled"], report["variantQueue"]),
      (pp.CJK(TITLE_B + DATE + BODY_B), {"variant-script"}, [], []))
record, markdown, report = proofread_simplified({"rulings": [{"forms": ["諭", "谕"], "printed": "諭"}]})
check("phase 3, simplified script: a ruling stands over the script",
      (pp.CJK(markdown)[:3], variant_rows(record)[:2]),
      ("諭内阁", [("諭", "谕", "諭", "variant-ruling"), ("內", "内", "内", "variant-script")]))
# A segment holding a site the script decides and a pair that differs in no script (爲
# and 為 are both 为 in simplified script) is partly ruled, the open site engine A's.
check("phase 3, simplified script: a script site beside an unruled pair is partly ruled",
      pp.resolve_variant({"a": "諭爲", "b": "谕為"}, SimpleNamespace(prefer_engine="A", simplified_script=True)),
      ("谕爲", "variant-partly-ruled-engine-A", ["爲|為"]))


# --- the skill's text: the profile rules no variant, and nothing waits for a person ---
ROOT = Path(__file__).resolve().parents[1]
skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")
references = "".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "references").glob("*.md")))
workflow = (ROOT / "references" / "long-book-workflow.md").read_text(encoding="utf-8")
check("SKILL.md, README.md and references/: the vote's script and switches are not named",
      [name for name in ("variant_vote.py", "--no-variant-vote", "--variant-vote-crops", "--variant-vote-calls")
       if name in skill or name in readme or name in references], [])
check("SKILL.md: no person rules the variants, and nothing waits for one",
      "異體亦唔使人裁" in skill and "唔停低問人" in skill, True)
check("SKILL.md and the reference: no step hands the variants to a person or waits for a ruling",
      [phrase for phrase in ("代人裁", "交畀佢裁", "逐對由人裁", "填好「printed」", "留畀人裁")
       if phrase in skill or phrase in workflow], [])
check("long-book-workflow.md: what phase 3 seals for the rulings of a file the vote wrote",
      [name for name in ("variant-model-ruling", "variant-person-and-model-ruling", "variantRuledByModel",
                         "variantRulings", "modelUnruled", "ruledBy: model")
       if name not in workflow], [])
check("long-book-workflow.md: what phase 3 writes and seals in a book printed in simplified script",
      [name for name in ("variant-script", "answeredTraditional", "simplified_script") if name not in workflow], [])

print("FAILED" if failures else "all variant-ruling checks passed")
sys.exit(1 if failures else 0)
