"""The deliveries' copies beside the book's PDF (references/long-book-workflow.md
〈三次交付同本書目錄〉).

The person asks 「幫我識別校對 X.pdf」 and reads the book where X.pdf is.
Beside it go:
- X-draft.md, the draft (build_draft.py);
- X.md, the finished book (finish_book.py, its final.md);
- X-doubts.md, the doubt list (finish_book.py, its doubts.md, where each
  `final.md` it names reads X.md);
- X-圖/, the pictures of the book's figures (finish_book.py, its figures/;
  scripts/_figures.py), read from there by X.md's image lines and named by
  X-doubts.md.

X is the PDF's file name without `.pdf`.  The PDF is the one the page
manifest names (survey_pdf.py's source_pdf, with its source_sha256), so no
agent has to pass it on, and the book folder itself (renders, OCR drafts,
sealed pages: gigabytes) can live anywhere.

A copy never replaces a file that is not the pipeline's own.
BOOK/delivered.json keeps, for each copy, its path and the SHA-256 of every
version written there:
- no file at the path: the copy is written;
- a file holding one of those versions: it is replaced;
- a file at a path this book wrote before, holding none of them (the person
  corrected it): it is moved aside to <name>.edited-<time>.md beside it, the
  way finish_book.py keeps an edited final.md, and the copy is written;
- a file at a path this book never wrote (another tool's transcript, the
  person's own notes): it is left alone, and no copy is written; the reason
  says so and where the book's own file is.
The pictures in X-圖/ go by the same rules, each its own copy recorded in
delivered.json; one of ours the book no longer has is removed, one the
person changed stays.  A delivered.json written before figures were kept
holds no picture.

Nothing here stops a step.  If the manifest names no PDF, the PDF has moved or
changed (its SHA-256 is not the manifest's), or the folder cannot be written
to, the result is a reason and the book's own files stand.  Every copy is
written through a temporary file and a rename.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import datetime as _dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator, Sequence

RECORD_NAME = "delivered.json"
LOCK_NAME = ".delivered.lock"
MANIFEST_NAMES = ("page-manifest.json", "renders/page-manifest.json")
KEPT = 20                                   # versions remembered per copy
KINDS = ("draft", "final", "doubts")


@dataclass
class Delivery:
    kind: str
    path: Path | None                       # where the copy is (or would have gone)
    written: bool
    reason: str = ""                        # why not, when not written
    kept_as: str | None = None              # the person's edited copy, moved aside

    def line(self) -> str:
        if self.written:
            return f"{self.path}" + (f" (the edited copy kept as {self.kept_as})" if self.kept_as else "")
        return f"not copied beside the PDF: {self.reason}"


def copy_name(pdf: Path, kind: str) -> str:
    """X-draft.md, X.md, X-doubts.md for X.pdf."""
    stem = pdf.name[:-4] if pdf.name.lower().endswith(".pdf") else pdf.stem
    return {"draft": f"{stem}-draft.md", "final": f"{stem}.md", "doubts": f"{stem}-doubts.md"}[kind]


def figures_folder(pdf: Path) -> Path:
    """X-圖 beside X.pdf."""
    return pdf.parent / f"{copy_name(pdf, 'final')[:-3]}-圖"


# A link to a picture of figures/: final.md's image lines (`![](figures/NAME)`)
# and doubts.md's items (`[一幅圖片](figures/NAME)`).
FIGURE_LINK = re.compile(r"\]\(figures/(page-\d{4,}-\d+\.png)\)")


def figures_for_copy(text: str, pdf: Path) -> str:
    """final.md or doubts.md as its copy beside PDF reads: each link to a
    picture of figures/ reads X-圖/, between angle brackets (X may hold
    spaces), with the characters a link would read otherwise (%, #, ?, <, >:
    an escape, a fragment, a query, the brackets' end) percent-encoded."""
    folder = re.sub(r"[%#?<>]", lambda m: f"%{ord(m.group()):02X}", figures_folder(pdf).name)
    return FIGURE_LINK.sub(lambda m: f"](<{folder}/{m.group(1)}>)", text)


def find_manifest(book: Path, given: Path | None = None) -> Path | None:
    if given is not None:
        return given if given.is_file() else None
    return next((book / name for name in MANIFEST_NAMES if (book / name).is_file()), None)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_pdf(book: Path, manifest: Path | None = None) -> tuple[Path | None, str]:
    """The PDF the book was rendered from, as it still is where the manifest
    says, or None and why not."""
    found = find_manifest(book, manifest)
    if found is None:
        return None, f"no page manifest in {book}"
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        return None, f"the page manifest {found} cannot be read ({type(error).__name__})"
    named = data.get("source_pdf") if isinstance(data, dict) else None
    if not isinstance(named, str) or not named:
        return None, f"the page manifest {found} names no source PDF"
    pdf = Path(named).expanduser()
    if not pdf.is_absolute():
        pdf = found.parent / pdf
    if not pdf.is_file():
        return None, f"the PDF is no longer at {pdf} (moved or renamed)"
    sha = data.get("source_sha256")
    if isinstance(sha, str) and sha and _sha256_file(pdf) != sha:
        return None, f"{pdf} is not the PDF this book was rendered from (its SHA-256 differs)"
    return pdf, ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def _locked(book: Path, blocking: bool = True) -> Iterator[None]:
    """The delivery lock.  Not BLOCKING: BlockingIOError when a delivery is
    being written (clean_book.py refuses then, rather than wait)."""
    with open(book / LOCK_NAME, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _record(book: Path) -> dict[str, list[str]]:
    try:
        data = json.loads((book / RECORD_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {path: [s for s in shas if isinstance(s, str)] for path, shas in data.items() if isinstance(shas, list)}


def _atomic_write(path: Path, data: bytes) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _put(book: Path, target: Path, data: bytes, record: dict[str, list[str]]) -> tuple[str, str | None]:
    """Write DATA at TARGET under the rules above and record it in RECORD:
    '' and the name of the person's copy moved aside (or None), or why not."""
    versions = record.get(str(target), [])
    kept_as = None
    if target.is_symlink() or (target.exists() and not target.is_file()):
        return f"{target} is not a plain file; the book's own copy is in {book}", None
    if target.exists():
        current = _sha256(target.read_bytes())
        if current != _sha256(data) and current not in versions:
            if not versions:
                return (f"{target} already exists and was not written by this book's pipeline: left as it is; the "
                        f"book's own copy is in {book}"), None
            stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
            stem, suffix = os.path.splitext(target.name)
            aside = target.with_name(f"{stem}.edited-{stamp}{suffix}")
            number = 1
            while aside.exists():
                number += 1
                aside = target.with_name(f"{stem}.edited-{stamp}-{number}{suffix}")
            target.rename(aside)
            kept_as = aside.name
    _atomic_write(target, data)
    digest = _sha256(data)
    record[str(target)] = ([s for s in versions if s != digest] + [digest])[-KEPT:]
    return "", kept_as


def _save(book: Path, record: dict[str, list[str]]) -> None:
    _atomic_write(book / RECORD_NAME, (json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True)
                                       + "\n").encode("utf-8"))


def deliver(book: Path, kind: str, data: bytes, manifest: Path | None = None) -> Delivery:
    """Write DATA beside the book's PDF as the copy of KIND (draft, final,
    doubts), under the rules above.  Never raises for the book's sake: an
    error is the Delivery's reason."""
    if kind not in KINDS:
        raise ValueError(f"unknown delivery {kind!r}")
    pdf, why = source_pdf(book, manifest)
    if pdf is None:
        return Delivery(kind, None, False, why)
    target = pdf.parent / copy_name(pdf, kind)
    try:
        with _locked(book):
            record = _record(book)
            why, kept_as = _put(book, target, data, record)
            if why:
                return Delivery(kind, target, False, why)
            _save(book, record)
            return Delivery(kind, target, True, "", kept_as)
    except OSError as error:
        return Delivery(kind, target, False, f"{target} could not be written ({type(error).__name__}: {error})")


def deliver_figures(book: Path, pictures: Sequence[Path], manifest: Path | None = None) -> Delivery:
    """Copy PICTURES (BOOK/figures/'s) into X-圖/ beside the book's PDF,
    each under the rules above, and remove the pictures of ours there the
    book no longer has (one the person changed stays, still recorded); the
    folder goes when it is left empty.  Written only when every picture is
    there; KEPT_AS names the person's copies moved aside."""
    pdf, why = source_pdf(book, manifest)
    if pdf is None:
        return Delivery("figures", None, False, why)
    folder = figures_folder(pdf)
    try:
        with _locked(book):
            if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                return Delivery("figures", folder, False, f"{folder} is not a plain folder; the book's pictures are "
                                                          f"in {book}")
            record = _record(book)
            before = json.dumps(record, sort_keys=True)
            names = {picture.name for picture in pictures}
            for key in [k for k in record if Path(k).parent == folder and Path(k).name not in names]:
                stale = Path(key)
                if stale.is_file() and not stale.is_symlink() and _sha256_file(stale) in record[key]:
                    stale.unlink()
                if not (stale.exists() or stale.is_symlink()):
                    del record[key]
            reasons, aside = [], []
            for picture in pictures:
                folder.mkdir(exist_ok=True)
                reason, kept_as = _put(book, folder / picture.name, picture.read_bytes(), record)
                reasons += [reason] if reason else []
                aside += [kept_as] if kept_as else []
            if json.dumps(record, sort_keys=True) != before:
                _save(book, record)
            with contextlib.suppress(OSError):
                folder.rmdir()           # only when nothing is left in it
            return Delivery("figures", folder, not reasons, "; ".join(reasons[:3]), ", ".join(aside) or None)
    except OSError as error:
        return Delivery("figures", folder, False, f"{folder} could not be written ({type(error).__name__}: {error})")


def doubts_for_copy(text: str, pdf_final_name: str) -> str:
    """doubts.md as its copy beside the PDF reads: the book it lists doubts of
    is X.md there, not final.md."""
    return text.replace("final.md", pdf_final_name)
