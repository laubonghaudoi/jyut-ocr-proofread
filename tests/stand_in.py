"""A stand-in model and made-up books, for offline page runs of proofread_pages.main().

Not a check itself: the check_*.py files import it.  No GPU, no corpus: each
book is written to a temporary directory (a blank render, engine A's text and
layout blocks, engine B's text), and the model is replaced by StandIn, which
answers each kind of call from what the check gave it.  A check that needs
the real transport asks a model server in this process (Served).

Importing it imports tests/offline.py, which points the default endpoint at
a port nothing listens on: no check reaches the machine's model server.
run() gives each run that endpoint.
"""

from __future__ import annotations

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from offline import CLOSED_ENDPOINT  # noqa: E402 - first: the closed endpoint
import proofread_pages as pp  # noqa: E402


def between(text: str) -> str:
    """The text a prompt fences with --- lines: what the model is to work on."""
    match = re.search(r"---\n(.*?)\n---", text, re.S)
    return match.group(1) if match else ""


class StandIn:
    """Answers like the reading model, by kind of call.

    census     the count to report ("不詳" for none; "12\n雙圈：10" adds the
               doubled-circle line), or a function of the call's position
               among the census calls
    punctuate  a function from the characters handed over to the answer; the
               default writes no marks
    judge      the 裁決 answer for (engine A, engine B); default engine A's reading
    formatter  a function from (the text handed over, kind of call) to the
               answer; by default the formatter and the structure pass echo
               what they are given
    rebuild    a function from the characters a table rebuild is locked to
               (rebuild_table) to its answer; by default no table
    coverage   the blind transcription a crop gets when 裁決's 無 is checked
               against it (confirm_nothing); by default engine A's text of the
               book's pages, as run() finds it - a crop that shows the spot as
               engine A read it
    """

    model, effort = "stand-in", None

    def __init__(self, census: Any = 0, punctuate: Callable[[str], str] | None = None,
                 judge: Callable[[str, str], str] | None = None,
                 formatter: Callable[[str, str], str] | None = None,
                 rebuild: Callable[[str], str] | None = None,
                 coverage: str | None = None):
        self.census = census
        self.punctuate = punctuate or (lambda span: span)
        self.judge = judge or (lambda a, b: a or "無")
        self.formatter = formatter or (lambda text, kind: text)
        self.rebuild = rebuild
        self.coverage = coverage
        self.engine_a_text = ""
        self.calls: list[tuple[str, str]] = []
        self.systems: list[str] = []

    def ask_answering(self, system: str, user_text: str, image_bytes: bytes | None,
                      max_tokens: int, kind: str = "other") -> tuple[str, dict[str, Any]]:
        self.calls.append((kind, user_text))
        self.systems.append(system)
        metrics = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        # The census and the punctuator in any variant the switches make.
        if system.startswith(pp.CENSUS_SYSTEM.split("\n")[0]):
            return f"符號：{self.census}\n形狀：略", metrics
        if system.startswith(pp.PUNCT_SYSTEM.split("\n")[0]):
            return self.punctuate(between(user_text)), metrics
        if system == pp.ADJUDICATE_SYSTEM:
            first = re.search(r"甲\) (.*)", user_text)
            second = re.search(r"乙\) (.*)", user_text)
            a = first.group(1).strip() if first else ""
            b = second.group(1).strip() if second else ""
            a = "" if a == "（冇字）" else a
            b = "" if b == "（冇字）" else b
            return f"構件：略\n定案：{self.judge(a, b)}\nRESEARCH: no", metrics
        if system.startswith("你係 jyut-ocr-formatter"):
            # The structure pass and the whole-page formatter, in any variant.
            return self.formatter(between(user_text), kind), metrics
        if system == pp.BLIND_SYSTEM:
            return (self.coverage if self.coverage is not None else self.engine_a_text), metrics
        if system == pp.REBUILD_SYSTEM and self.rebuild is not None:
            return self.rebuild(between(user_text)), metrics
        return "RECORD: row" if "RECORD" in user_text else "[]", metrics

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def reply(content: str, finish: str = "stop", completion_tokens: int = 12, model: str = "stand-in",
          **usage: Any) -> dict:
    """A chat response's payload as the model server sends it."""
    return {"model": model, "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": completion_tokens, **usage}}


class Served:
    """A model server in this process, for checks that need the real
    transport (proofread_pages.Client._send over HTTP): an HTTP server on a
    free port of 127.0.0.1 that answers each endpoint (endpoint()) by a
    function of the request's body, which returns the response's payload
    (reply) or an HTTP status to fail with."""

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[dict], Any]] = {}
        self.lock = threading.Lock()
        routes = self.routes

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server's name
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                respond = routes.get(self.path)
                answer = respond(body) if respond else 404
                status, raw = ((answer, b"stand-in") if isinstance(answer, int)
                               else (200, json.dumps(answer, ensure_ascii=False).encode()))
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def endpoint(self, respond: Callable[[dict], Any]) -> str:
        """A new endpoint of this server, answered by RESPOND."""
        with self.lock:
            path = f"/{len(self.routes)}/v1/chat/completions"
            self.routes[path] = respond
        return f"http://127.0.0.1:{self.server.server_address[1]}{path}"


_SERVED: list[Served] = []


def served() -> Served:
    """This process's Served, started on first use."""
    if not _SERVED:
        _SERVED.append(Served())
    return _SERVED[0]


def block(text: str, box: Sequence[float], label: str = "text", order: int | None = None) -> dict:
    return {"text": text, "boundingBox": list(box), "blockLabel": label, "blockOrder": order}


def region(box: Sequence[float], label: str = "chart") -> dict:
    """A layout region engine A found with no text (its runner's `regions`)."""
    return {"boundingBox": list(box), "blockLabel": label, "blockOrder": -1}


def write_book(root: Path, pages: Mapping[int, tuple[Sequence[dict], str] | tuple[Sequence[dict], str, Sequence[dict]]],
               profile: Mapping[str, Any] | None = None) -> Path:
    """A book under ROOT: page -> (engine A's blocks, engine B's text[, engine
    A's text-less regions]).  Engine A's text is its blocks' text joined by
    lines, as its runner writes it."""
    from PIL import Image

    for name in ("renders", "a", "b", "out"):
        (root / name).mkdir(parents=True, exist_ok=True)
    for page, (blocks, text_b, *regions) in pages.items():
        stem = f"page-{page:04d}"
        Image.new("L", (1000, 1400), 255).save(root / "renders" / f"{stem}.png")
        (root / "a" / f"{stem}.txt").write_text("\n".join(b["text"] for b in blocks) + "\n", encoding="utf-8")
        record = {"lines": list(blocks), **({"regions": list(regions[0])} if regions else {})}
        (root / "a" / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        (root / "b" / f"{stem}.txt").write_text(text_b + "\n", encoding="utf-8")
    if profile is not None:
        (root / "profile.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return root


@contextlib.contextmanager
def book(pages: Mapping[int, tuple[Sequence[dict], str]],
         profile: Mapping[str, Any] | None = None) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield write_book(Path(tmp), pages, profile)


def settled(pages: Mapping[int, tuple[dict, str]]) -> dict[int, tuple[dict, str]]:
    """PAGES (what run returns) without what two runs of the same decisions
    differ in: times (the time each page was sealed, how long it and its
    calls took) and the payload seal over them, and the folder of the render
    (each run's book is in a folder of its own)."""
    def still(value):
        if isinstance(value, dict):
            return {key: still(item) for key, item in value.items()
                    if key not in ("generatedAt", "seconds", "jsonPayloadSha256")
                    and not key.endswith(("Seconds", "Ms"))}
        if isinstance(value, list):
            return [still(item) for item in value]
        return value
    out = {}
    for page, (record, markdown) in pages.items():
        record = still(record)
        if record.get("renderFile"):
            record["renderFile"] = Path(record["renderFile"]).name
        out[page] = (record, markdown)
    return out


def preferring(root: Path, engine: str = "A") -> list[str]:
    """The arguments that give proofread_pages.main() the book profile at
    ROOT/profile.json, written first, preferring ENGINE, when there is none
    (the engine preference comes from the book profile)."""
    profile = root / "profile.json"
    if not profile.is_file():
        profile.write_text(json.dumps({"preferEngine": engine}), encoding="utf-8")
    return ["--profile", str(profile)]


def run(root: Path, model: StandIn, pages: str = "1", extra: Sequence[str] = ()) -> dict[int, tuple[dict, str]]:
    """Proofread the book at ROOT with MODEL in place of the client; returns
    page -> (sealed record, page Markdown).  The run's endpoint is
    CLOSED_ENDPOINT unless EXTRA gives one.  A book written with no profile gets one that prefers engine A
    (the engine preference comes from the book profile)."""
    real = pp.Client
    pp.Client = lambda *args, **kwargs: model
    if hasattr(model, "engine_a_text"):
        model.engine_a_text = "\n".join(path.read_text(encoding="utf-8")
                                        for path in sorted((root / "a").glob("page-*.txt")))
    try:
        profile = preferring(root)
        status = pp.main([str(root / "renders"), str(root / "a"), str(root / "out"),
                          "--draft2-directory", str(root / "b"), *profile, "--pages", pages,
                          "--overwrite", "--report", str(root / "report.json"),
                          "--backend", "http", "--endpoint", CLOSED_ENDPOINT, *extra])
    finally:
        pp.Client = real
    assert status == 0, status
    out = {}
    for js in sorted((root / "out").glob("page-*.json")):
        if "adjudication" in js.name:
            continue
        record = json.loads(js.read_text(encoding="utf-8"))
        out[record["scanPage"]] = (record, js.with_suffix(".md").read_text(encoding="utf-8"))
    return out
