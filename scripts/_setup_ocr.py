"""Portable, resumable OCR environment setup (stdlib bootstrap only).

This module never sources shell configuration, installs GPU drivers, or treats
file presence as evidence that an OCR engine is ready. Expensive work is opt-in
through setup(); inspect_setup() is read-only and never starts an OCR model.

Linux/WSL2 installs both draft engines by default: HunyuanOCR (GGUF through a pinned
llama.cpp llama-server) and PaddleOCR-VL (its own Python 3.12 environment and
pinned weights). macOS requires native Apple Silicon and first asks the user
to choose apple-vision or hunyuan-paddle. Intel Macs are unsupported.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import signal
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Iterable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _common import sha256_file  # noqa: E402


class SetupError(RuntimeError):
    """Setup can be resumed after the reported missing prerequisite is supplied."""

    returncode: int | None = None


class DownloadInterrupted(SetupError):
    """A download stopped part-way; rerunning resumes it."""


@dataclass(frozen=True)
class LocalModel:
    """A local proofreading model the agent may run on (env.sh PROOFREAD_* and
    the GPU handover's OCR_PHASE_* keys)."""
    endpoint: str
    name: str
    release_cmd: str | None = None
    restore_cmd: str | None = None
    health_url: str | None = None
    # Requests the server runs at once: PROOFREAD_MAX_INFLIGHT.
    concurrency: int | None = None


@dataclass(frozen=True)
class SetupOptions:
    repo_root: Path
    config_dir: Path
    data_dir: Path
    # "default" keeps the saved request (JYUT_OCR_ENGINE / JYUT_OCR_DEVICE),
    # else auto; "auto" asks for the platform default again.
    engine: str = "default"
    device: str = "default"
    offline: bool = False
    install_system_deps: bool = False
    interactive: bool = False
    local_model: LocalModel | None = None
    # Everything but the setup tests, e.g. the long downloads in the background
    # before a foreground run whose test may need a GPU hand-over.
    prepare_only: bool = False
    # time.time() when the agent's shell call that runs setup began (install.py
    # sets it); the hand-over smoke gets only what is left of the budget.
    started_at: float | None = None
    paddle_vl_backend: str = "default"


# Keys read strictly: paths the installer verifies and values it computes with.
PATH_KEYS = frozenset({
    "JYUT_OCR_PYTHON", "PADDLEOCR_PYTHON", "HUNYUAN_MODEL", "HUNYUAN_MMPROJ",
    "HUNYUAN_LLAMA_SERVER", "PADDLE_VL_MODEL_DIR", "PADDLE_LAYOUT_MODEL_DIR", "MLX_VLM_PYTHON",
})
CONFIG_KEYS = PATH_KEYS | frozenset({
    "JYUT_OCR_ENGINE", "JYUT_OCR_ENGINES", "JYUT_OCR_DEVICE", "OCR_HUNYUAN_DEVICE",
    "OCR_HUNYUAN_MODE", "OCR_HUNYUAN_SLOTS", "OCR_PADDLE_DEVICE",
    "JYUT_OCR_PADDLE_VL_BACKEND", "OCR_PADDLE_VL_BACKEND",
})
# Keys of the same skill read leniently and passed on unchanged: the GPU
# handover (OCR_PHASE_*), the proofreading model (PROOFREAD_*), queue and
# engine extras.  A value the installer cannot expand (a command naming $VAR)
# is kept as written; the installer never evaluates it.
SHARED_PREFIXES = ("OCR_PHASE_", "PROOFREAD_", "HUNYUAN_", "OCR_HUNYUAN_", "OCR_PADDLE_",
                   "PADDLE", "JYUT_OCR_", "OCR_GPU_", "OCR_CPU_")
EXPORT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*?)\s*$")
STATE_SCHEMA = 2
LOCK_SCHEMA = 2

PYTHON_VERSION = "3.12"
ENGINE_REQUESTS = ("hunyuan-paddle", "apple-vision", "hunyuan")
DEVICE_REQUESTS = ("auto", "cpu", "cuda", "metal")
PADDLE_VL_BACKENDS = ("auto", "native", "mlx-vlm-server")
# run_hunyuanocr.py's default --ctx-size; the context is per slot.
HUNYUAN_CONTEXT_PER_SLOT = 16384
# Measured shared-server peaks (one 5090, 16384 context per slot): 5,036 MiB at
# 1 slot, 15,788 MiB at 8 and 28,076 MiB at 16: about 3,500 MiB plus the KV
# cache of 96 KiB per token (24 layers x 8 KV heads x 128 dims, f16).
HUNYUAN_BASE_MIB = 3500
HUNYUAN_KV_KIB_PER_TOKEN = 96
# PaddleOCR-VL measured peak at 300 dpi is 11,495 MiB in bf16.
PADDLE_NEED_MIB = 12288
PADDLE_GPU_MIN_TOTAL_MIB = 11776
PADDLE_MIN_COMPUTE_CAPABILITY = (7, 0)
# Below compute capability 8.0 there is no bf16 and Paddle loads float32
# weights, roughly twice the memory (an estimate; not measured).
PADDLE_FLOAT32_BELOW = (8, 0)
SMOKE_QUEUE_TIMEOUT = 300
SMOKE_TIMEOUT = 1800
# The least of OCR_PHASE_MAX_SECONDS the hand-over smoke starts with (or half
# the budget, when that is smaller): the wrapper's default reserve (about 95 s),
# the release, and each engine's cold load and one page.
HANDOVER_SMOKE_FLOOR = 240.0
UV_HINT = ("Install uv, then rerun setup; it provisions Python 3.12 itself and never downloads uv: "
           "https://docs.astral.sh/uv/getting-started/installation/ "
           "(curl -LsSf https://astral.sh/uv/install.sh | sh, brew install uv, or pipx install uv).")
# Estimates for the free-disk check (bytes); downloads are added at their size.
EXTRACTED_RUNTIME_FACTOR = 2.5
SOURCE_BUILD_BYTES = 600_000_000
PYTHON_RUNTIME_BYTES = 150_000_000
CORE_VENV_BYTES = 100_000_000
PADDLE_GPU_DEPENDENCY_BYTES = 3_500_000_000
PADDLE_CPU_DEPENDENCY_BYTES = 900_000_000
MLX_DEPENDENCY_BYTES = 2_000_000_000
DISK_MARGIN_BYTES = 1 << 30


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def atomic_text(path: Path, text: str, *, mode: int | None = None) -> None:
    """Write PATH whole, through a temporary file in its folder: mode 0600, or MODE."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def version_tuple(text: str | None) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(part) for part in parts)


def shared_key(name: str) -> bool:
    return name in CONFIG_KEYS or name.startswith(SHARED_PREFIXES)


def read_environment(path: Path, *, home: Path | None = None) -> dict[str, str]:
    """Read literal single-line exports; never execute shell or substitute commands.

    Unknown lines survive write_environment() untouched. Paths and the values
    the installer computes with (CONFIG_KEYS) support only $HOME and ${HOME}
    expansion; dynamic settings there require an explicit literal path. The
    skill's other keys (OCR_PHASE_*, PROOFREAD_*, ...) are read as written."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    home_text = str(home or Path.home())
    for line in path.read_text(encoding="utf-8").splitlines():
        match = EXPORT.match(line)
        if not match or not shared_key(match[1]):
            continue
        name, raw = match.groups()
        strict = name in CONFIG_KEYS
        if strict and ("$(" in raw or "`" in raw):
            raise SetupError(f"Cannot safely read dynamic {name} in {path}; use a literal single-line export.")
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            if strict:
                raise SetupError(f"Cannot parse {name} in {path}: use a literal single-line export.") from exc
            values[name] = raw
            continue
        if len(tokens) > 1:
            if strict:
                raise SetupError(f"Cannot safely read {name} in {path}; quote its value.")
            values[name] = raw
            continue
        value = tokens[0] if tokens else ""
        value = value.replace("${HOME}", home_text)
        value = re.sub(r"\$HOME\b", lambda _: home_text, value)
        if strict and "$" in value:
            raise SetupError(f"Cannot expand {name} in {path}; only HOME expansion is supported.")
        values[name] = value
    return values


def environment_target(path: Path) -> Path:
    """The file write_environment() writes: PATH, or the file a symlinked PATH
    points to, so a dotfile manager's link keeps working and stays current."""
    if not path.is_symlink():
        return path
    target = Path(os.path.realpath(path))
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SetupError(f"{path} is a symlink that does not end at a regular file; fix the link and rerun setup.")
    if not target.parent.is_dir():
        raise SetupError(f"{path} is a symlink to {target}, whose folder does not exist; fix the link and rerun setup.")
    return target


# Shell words that open a block, and the word that closes each.
_SHELL_BLOCKS = {"if": "fi", "case": "esac", "for": "done", "while": "done", "until": "done",
                 "select": "done", "{": "}", "(": ")"}
# After these words the next word is a command again.
_SHELL_COMMAND_AFTER = {"then", "do", "else", "elif", "!", "time"}
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\2")
# The comment above an export the installer appends after a key's own lines.
OVERRIDE_NOTE = "# {name}: set by the jyut-ocr installer; the earlier {name} lines above are left as written."
# The comment above a key's line the installer replaced: the line follows it,
# commented out, and the installer's export follows that.
REPLACED_NOTE = "# {name}: replaced by the jyut-ocr installer on {date}; the earlier line is kept below as a comment."
_REPLACED_NOTE = re.compile(r"# ([A-Za-z_][A-Za-z_0-9]*): replaced by the jyut-ocr installer on \d{4}-\d{2}-\d{2}; ")


def top_level_lines(text: str) -> set[int]:
    """The indexes of TEXT's lines that start a command outside every block.

    A small, cautious reading of the shell: if/case/for/while/until/select,
    { } groups, ( ) subshells and function bodies are blocks; a line that
    starts inside a quote, a $( ) or ${ }, a here-document or after a
    backslash continuation does not start a top-level command.  Whatever it
    misreads only makes write_environment() append an override instead of
    replacing a line where it stands."""
    stack: list[str] = []
    top: set[int] = set()
    quote = ""
    substitutions: list[str] = []
    continued = False
    command = True
    word, word_command = "", True
    heredocs: list[tuple[str, bool]] = []

    def finish() -> None:
        nonlocal word, command
        if not word:
            return
        if word == ")" and stack and stack[-1] == "(":
            stack.pop()  # an operator: it ends the subshell wherever it stands
            command = False
        elif word_command and word in _SHELL_BLOCKS:
            stack.append(word)
            command = True
        elif word_command and stack and word == _SHELL_BLOCKS[stack[-1]]:
            stack.pop()
            command = False
        elif word.endswith("()") or (word_command and word in _SHELL_COMMAND_AFTER):
            command = True  # a function's name, or a word after which a command follows
        elif word.endswith(")") and stack and stack[-1] == "case":
            command = True  # a case pattern: its commands follow
        else:
            command = False
        word = ""

    for number, line in enumerate(text.splitlines()):
        if heredocs:
            name, strip = heredocs[0]
            if (line.lstrip("\t") if strip else line) == name:
                heredocs.pop(0)
            continue
        if not stack and not quote and not substitutions and not continued:
            top.add(number)
        if not quote and not substitutions and not continued:
            command = True
        continued = False
        index = 0
        while index < len(line):
            char = line[index]
            if not word:
                word_command = command
            if quote == "'":
                quote = "" if char == "'" else quote
                word += char
                index += 1
                continue
            if char == "\\":
                if index + 1 == len(line):
                    continued = True
                word += line[index:index + 2]
                index += 2
                continue
            if quote == '"':
                quote = "" if char == '"' else quote
                word += char
                index += 1
                continue
            if char in "'\"":
                quote = char
            elif substitutions:
                if line.startswith(("$(", "${"), index):
                    substitutions.append(")" if line[index + 1] == "(" else "}")
                    word += line[index:index + 2]
                    index += 2
                    continue
                if char == substitutions[-1]:
                    substitutions.pop()
                elif char == "(" and substitutions[-1] == ")":
                    substitutions.append(")")
                elif char == "`":
                    substitutions.append("`")
            elif line.startswith(("$(", "${"), index):
                substitutions.append(")" if line[index + 1] == "(" else "}")
                word += line[index:index + 2]
                index += 2
                continue
            elif char == "`":
                substitutions.append("`")
            elif char in " \t":
                finish()
                index += 1
                continue
            elif char == "#" and not word:
                break
            elif char in ";&|":
                finish()
                command = True
                index += 1
                continue
            elif char in "()" and not word:
                word = char
                finish()
                index += 1
                continue
            elif line.startswith("<<", index) and not line.startswith("<<<", index):
                found = _HEREDOC.match(line, index)
                if found:
                    heredocs.append((found[3], bool(found[1])))
                    finish()
                    index = found.end()
                    continue
            word += char
            index += 1
        if not quote and not substitutions and not continued:
            finish()
    return top


def _simple_export(raw: str) -> bool:
    """Whether an export's value is one shell word (and a comment at most), so
    the line can be rewritten without dropping another command on it."""
    try:
        return len(shlex.split(raw, comments=True, posix=True)) <= 1
    except ValueError:
        return False


def shell_syntax_error(text: str) -> str | None:
    """What `bash -n` says is wrong with TEXT, or None (also without bash)."""
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        result = subprocess.run([bash, "-n"], input=text, text=True, capture_output=True, timeout=30,
                                check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return None
    return " ".join((result.stderr or "").split()) or f"bash -n exit {result.returncode}"


def _replacement(lines: Sequence[str], index: int, name: str) -> bool:
    """Whether LINES[INDEX] is the export the installer wrote where it replaced
    a line of NAME: under REPLACED_NOTE and that line, commented out."""
    if index < 2:
        return False
    note = _REPLACED_NOTE.match(lines[index - 2])
    earlier = lines[index - 1]
    kept = EXPORT.match(earlier[2:].rstrip("\r\n")) if earlier.startswith("# ") else None
    return bool(note and note[1] == name and kept and kept[1] == name)


def environment_text(original: str, updates: Mapping[str, str], *, today: str | None = None) -> str:
    """ORIGINAL with UPDATES applied, never removing a line.

    A key with one line, at the top level and holding a single word, keeps
    that line commented out where it stands, under a dated note
    (REPLACED_NOTE), with the new export right after it: a person sees both,
    the shell only the new value.  A key set more than once, or inside a block
    (an if, a case, a function), or on a line with other commands keeps all
    its lines as written: the new value is appended at the end under a comment
    (OVERRIDE_NOTE).  A later update rewrites the installer's own export in
    either form, so a key never gains a second kept copy or note, and a key
    whose last line, at the top level, already sets the value is left alone.
    TODAY (YYYY-MM-DD) dates the note; it defaults to the local date."""
    lines = original.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    top = top_level_lines(original)
    places: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        match = EXPORT.match(line.rstrip("\r\n"))
        if match and match[1] in updates:
            places.setdefault(match[1], []).append(index)
    appended: list[str] = []
    date = today or datetime.now().strftime("%Y-%m-%d")
    for name, value in updates.items():
        export = f"export {name}={shlex.quote(str(value))}\n"
        found = places.get(name, [])
        if not found:
            appended.append(export)
            continue
        last = found[-1]
        raw = EXPORT.match(lines[last].rstrip("\r\n"))[2]
        simple = last in top and _simple_export(raw)
        if simple and (shlex.split(raw, comments=True, posix=True) or [""])[0] == str(value):
            continue  # the last line already sets this value: nothing to keep or replace
        own = ((last > 0 and lines[last - 1].rstrip("\r\n") == OVERRIDE_NOTE.format(name=name))
               or _replacement(lines, last, name))
        if simple and own:
            lines[last] = export
        elif simple and len(found) == 1:
            lines[last] = REPLACED_NOTE.format(name=name, date=date) + "\n# " + lines[last] + export
        else:
            appended += [OVERRIDE_NOTE.format(name=name) + "\n", export]
    if not original:
        lines.append("# Machine-local OCR environment. Generated without evaluating shell configuration.\n")
    return "".join(lines + appended)


def write_environment(path: Path, updates: dict[str, str]) -> None:
    """Set selected keys atomically, keeping every other line and a backup.

    environment_text() says which line changes; no line is ever removed.  A
    result that `bash -n` rejects while the original passed is not written.
    A symlinked PATH is written through to its target; the link stays.  The
    file keeps its mode; a new one (and every backup) is 0600."""
    target = environment_target(path)
    mode = stat.S_IMODE(target.stat().st_mode) if target.is_file() else None
    original = target.read_text(encoding="utf-8") if target.is_file() else ""
    content = environment_text(original, updates)
    if content == original:
        return
    if original and shell_syntax_error(original) is None:
        problem = shell_syntax_error(content)
        if problem is not None:
            raise SetupError(f"Setting {', '.join(updates)} in {target} would break its shell syntax "
                             f"(bash -n: {problem}); nothing was changed. Set them by hand and rerun setup.")
    if original:
        backup = path.parent / "env-backups" / f"env-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}.sh"
        atomic_text(backup, original)
    atomic_text(target, content, mode=mode)


def changed_keys(existing: Mapping[str, str], config: Mapping[str, str]) -> dict[str, str]:
    """The keys of CONFIG whose value is new; untouched keys keep their line as written."""
    return {key: value for key, value in config.items() if existing.get(key) != value}


def run(command: list[str | Path], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, capture: bool = False,
        timeout: float | None = None, own_group: bool = False) -> subprocess.CompletedProcess:
    argv = [str(item) for item in command]
    try:
        if own_group:
            process = subprocess.Popen(argv, cwd=cwd, env=env, text=True,
                                       stdout=subprocess.PIPE if capture else None,
                                       stderr=subprocess.STDOUT if capture else None,
                                       start_new_session=True)
            try:
                output, _ = process.communicate(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                # Terminate the owned process group on macOS as well as Linux;
                # killing just the Python wrapper can orphan its model server.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=10)
                raise
            result = subprocess.CompletedProcess(argv, process.returncode, output)
        else:
            result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                                    stdout=subprocess.PIPE if capture else None,
                                    stderr=subprocess.STDOUT if capture else None,
                                    timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"Command could not finish: {shlex.join(argv)} ({exc})") from exc
    if result.returncode:
        detail = (result.stdout or "")[-3000:] if capture else "See command output above."
        error = SetupError(f"Command failed ({result.returncode}): {shlex.join(argv)}\n{detail}")
        error.returncode = result.returncode
        raise error
    return result


def command_ok(command: list[str], timeout: float = 15) -> bool:
    try:
        run(command, capture=True, timeout=timeout)
        return True
    except SetupError:
        return False


def platform_name() -> str:
    system = platform.system()
    if system not in {"Linux", "Darwin"}:
        raise SetupError("Supported hosts are Linux, macOS and Windows WSL2. Run this installer inside WSL2 on Windows.")
    from _ocr_engines import require_supported_host
    try:
        require_supported_host(system, machine_name(system))
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    return system


def machine_name(system: str | None = None) -> str:
    """x86_64, aarch64 (Linux) or arm64 (macOS)."""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64"}:
        return "arm64" if (system or platform.system()) == "Darwin" else "aarch64"
    return machine


def default_directories() -> tuple[Path, Path]:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "jyut-ocr"
    if platform.system() == "Darwin":
        data = Path(os.environ.get("XDG_DATA_HOME", str(home / "Library/Application Support"))) / "jyut-ocr"
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "jyut-ocr"
    return config, data


# --- the host: GPUs, toolchain ---------------------------------------------------

@dataclass(frozen=True)
class GPU:
    index: int
    driver: str
    total_mib: int | None
    free_mib: int | None
    compute_capability: str | None
    name: str = ""


def _number(text: str) -> int | None:
    try:
        return int(float(text))
    except ValueError:
        return None


def nvidia_gpus() -> list[GPU]:
    """The NVIDIA GPUs the driver reports (nvidia-smi only; no CUDA toolkit needed).

    Queries metadata only: it starts nothing on the GPU."""
    if not shutil.which("nvidia-smi"):
        return []
    for fields in ("index,driver_version,memory.total,memory.free,compute_cap,name",
                   "index,driver_version,memory.total,memory.free,name"):
        try:
            output = run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                         capture=True, timeout=20).stdout or ""
        except SetupError:
            continue
        has_capability = "compute_cap" in fields
        gpus = []
        for line in output.splitlines():
            parts = [part.strip() for part in line.split(",", 5 if has_capability else 4)]
            if len(parts) < (6 if has_capability else 5) or _number(parts[0]) is None:
                continue
            capability = parts[4] if has_capability and re.fullmatch(r"\d+\.\d+", parts[4]) else None
            gpus.append(GPU(index=int(parts[0]), driver=parts[1], total_mib=_number(parts[2]),
                            free_mib=_number(parts[3]), compute_capability=capability, name=parts[-1]))
        if gpus:
            return gpus
    return []


def primary_gpu(gpus: Sequence[GPU]) -> GPU | None:
    """The GPU the runners use as CUDA0: one CUDA_VISIBLE_DEVICES index, else the first."""
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if re.fullmatch(r"\d+", visible):
        return next((gpu for gpu in gpus if gpu.index == int(visible)), None)
    return gpus[0] if gpus else None


def cuda_available() -> bool:
    """A working NVIDIA driver with a GPU.  The CUDA toolkit is not needed: the
    pinned llama.cpp release ships CUDA binaries, and Paddle's wheels carry CUDA."""
    return bool(nvidia_gpus())


def nvcc_path() -> str | None:
    for candidate in (shutil.which("nvcc"), "/usr/local/cuda/bin/nvcc"):
        if candidate and Path(candidate).is_file() and command_ok([candidate, "--version"]):
            return candidate
    return None


def metal_available() -> bool:
    """Apple Silicon always has Metal; the prebuilt llama.cpp embeds its shaders
    (GGML_METAL_EMBED_LIBRARY), so the offline `metal` compiler is not needed."""
    return platform.system() == "Darwin" and machine_name("Darwin") == "arm64"


def clang_available() -> bool:
    return command_ok(["xcrun", "--find", "clang"])


# --- engines and devices ---------------------------------------------------------------

def requested_value(requested: str | None, saved: str | None, choices: Sequence[str]) -> str:
    """The request in effect: an explicit one, else a saved one, else auto."""
    if requested and requested != "default":
        return requested
    return saved if saved in choices else "auto"


def engine_request(requested: str | None, existing: Mapping[str, str]) -> str:
    from _ocr_engines import DEFAULT_ROUTE, ROUTE_GUIDANCE, ROUTES
    value = requested if requested not in (None, "default") else existing.get("JYUT_OCR_ENGINE")
    if value is None:
        if existing.get("JYUT_OCR_ENGINES"):
            raise SetupError("Saved OCR configuration has no public route name. " + ROUTE_GUIDANCE)
        return DEFAULT_ROUTE
    if value not in ENGINE_REQUESTS:
        raise SetupError(f"Retired or unknown OCR route {value!r}. " + ROUTE_GUIDANCE)
    if requested in (None, "default") and existing.get("JYUT_OCR_ENGINES"):
        effective = tuple(existing["JYUT_OCR_ENGINES"].split(","))
        if len(effective) != len(ROUTES[value]) or set(effective) != set(ROUTES[value]):
            raise SetupError("Saved route and engines disagree or use a retired combination. " + ROUTE_GUIDANCE)
    return value


def device_request(requested: str | None, existing: Mapping[str, str]) -> str:
    # OCR_HUNYUAN_DEVICE is the effective device; only JYUT_OCR_DEVICE records
    # a person's choice, so an automatic CPU pick is never kept for good.
    return requested_value(requested, existing.get("JYUT_OCR_DEVICE"), DEVICE_REQUESTS)


def paddle_backend_request(requested: str | None, existing: Mapping[str, str]) -> str:
    """Remember the requested backend, separately from its effective selection."""
    return requested_value(requested, existing.get("JYUT_OCR_PADDLE_VL_BACKEND"), PADDLE_VL_BACKENDS)


def choose_paddle_backend(request: str, device: str, lock: Mapping[str, Any], *,
                          system: str, machine: str) -> str:
    if request not in PADDLE_VL_BACKENDS:
        raise SetupError("Invalid Paddle VL backend; choose auto, native or mlx-vlm-server.")
    apple_silicon = system == "Darwin" and machine == "arm64"
    minimum_macos = str(lock.get("mlx", {}).get("minimum_macos", "14.0"))
    supported_macos = version_tuple(platform.mac_ver()[0]) >= version_tuple(minimum_macos)
    if request == "mlx-vlm-server":
        if device == "cpu":
            raise SetupError("--device cpu conflicts with --paddle-vl-backend mlx-vlm-server; use native or device auto.")
        if not apple_silicon:
            raise SetupError("mlx-vlm-server requires an Apple Silicon Mac; use --paddle-vl-backend native here.")
        if not lock.get("mlx"):
            raise SetupError("The locked MLX environment is missing; update the skill before selecting mlx-vlm-server.")
        if not supported_macos:
            raise SetupError(f"The locked MLX environment requires macOS {minimum_macos} or newer; use native here.")
        return request
    if (request == "auto" and device != "cpu" and apple_silicon and supported_macos
            and lock.get("mlx", {}).get("enabled_by_default", False)):
        return "mlx-vlm-server"
    return "native"


def choose_engines(request: str, *, system: str, machine: str) -> tuple[tuple[str, ...], list[str]]:
    """Resolve the public route without downloading or changing configuration."""
    from _ocr_engines import route_engines
    try:
        engines = route_engines(request, system=system, machine=machine)
    except ValueError as exc:
        raise SetupError(str(exc)) from exc
    return engines, (["--engine hunyuan: HunyuanOCR only (Linux/WSL2 override)."]
                     if request == "hunyuan" else [])


def choose_device(requested: str, existing: dict[str, str], *, engine: str, system: str) -> str:
    """The Hunyuan device: cuda when an NVIDIA driver works, metal on Apple Silicon, else cpu."""
    selection = device_request(requested, existing)
    if engine == "vision":
        if selection not in {"auto", "cpu"}:
            raise SetupError("--device applies to HunyuanOCR and PaddleOCR-VL; Apple Vision manages its own compute device.")
        return "vision-managed"
    if selection == "auto":
        if system == "Linux":
            # Other GPUs (AMD, Intel) count as no GPU for now.
            return "cuda" if cuda_available() else "cpu"
        return "metal" if system == "Darwin" and metal_available() else "cpu"
    if selection == "cuda" and (system != "Linux" or not cuda_available()):
        raise SetupError("CUDA requested but no working NVIDIA driver/GPU answers nvidia-smi. "
                         "Use --device cpu; setup never installs drivers/toolkits.")
    if selection == "metal" and (system != "Darwin" or not (metal_available() or clang_available())):
        raise SetupError("Metal requested but this is not Apple Silicon and the Command Line Tools (clang) are missing. Install them or use --device cpu.")
    if selection not in {"cpu", "cuda", "metal"}:
        raise SetupError("Invalid device; choose --device auto, cpu, cuda or metal.")
    return selection


@dataclass(frozen=True)
class PaddlePlan:
    device: str                 # gpu:0 or cpu
    distribution: str           # paddlepaddle-gpu or paddlepaddle
    wheel: dict[str, Any]
    index: str | None = None    # Paddle's CUDA index (cu130 ...) for a GPU wheel
    need_mib: int = PADDLE_NEED_MIB
    notes: tuple[str, ...] = field(default_factory=tuple)
    backend: str = "native"

    @property
    def gpu(self) -> bool:
        return self.device.startswith("gpu")


def capability_tuple(text: str | None) -> tuple[int, int] | None:
    parts = version_tuple(text)
    return (parts[0], parts[1] if len(parts) > 1 else 0) if parts else None


def gpu_wheel_for(lock: Mapping[str, Any], machine: str, gpu: GPU) -> dict[str, Any] | None:
    """The first GPU wheel (newest CUDA first) the driver and compute capability allow."""
    capability = capability_tuple(gpu.compute_capability)
    for wheel in lock["paddle"]["gpu_wheels"]["wheels"]:
        if wheel["machine"] != machine or version_tuple(gpu.driver) < version_tuple(wheel["min_driver"]):
            continue
        if capability is None or capability < capability_tuple(wheel["min_compute_capability"]):
            continue
        if wheel.get("max_compute_capability") and capability > capability_tuple(wheel["max_compute_capability"]):
            continue
        return wheel
    return None


def paddle_plan(lock: Mapping[str, Any], *, system: str, machine: str, request: str,
                gpus: Sequence[GPU]) -> tuple[PaddlePlan | None, list[str]]:
    """Paddle's device and wheel here, or None when no Paddle can be installed.

    GPU when an NVIDIA card has compute capability >= 7.0, enough memory and a
    driver one of Paddle's CUDA builds accepts; otherwise the CPU wheel from PyPI
    (also on every Mac)."""
    notes: list[str] = []
    cpu = next((wheel for wheel in lock["paddle"]["cpu_wheels"]
                if wheel["system"] == system and wheel["machine"] == machine), None)
    gpu = primary_gpu(gpus) if system == "Linux" and request in {"auto", "cuda"} else None
    if gpu is not None:
        capability = capability_tuple(gpu.compute_capability)
        need = PADDLE_NEED_MIB
        minimum = PADDLE_GPU_MIN_TOTAL_MIB
        if capability is not None and capability < PADDLE_FLOAT32_BELOW:
            need, minimum = need * 2, minimum * 2
        if capability is None:
            notes.append(f"GPU {gpu.index} reports no compute capability, so PaddleOCR-VL runs on the CPU.")
        elif capability < PADDLE_MIN_COMPUTE_CAPABILITY:
            notes.append(f"GPU {gpu.index} has compute capability {gpu.compute_capability}; Paddle needs 7.0 or newer, "
                         "so PaddleOCR-VL runs on the CPU.")
        elif gpu.total_mib is not None and gpu.total_mib < minimum:
            notes.append(f"GPU {gpu.index} has {gpu.total_mib} MiB; PaddleOCR-VL needs about {minimum} MiB here "
                         "(11,495 MiB measured at 300 dpi in bf16), so it runs on the CPU.")
        else:
            wheel = gpu_wheel_for(lock, machine, gpu)
            if wheel is not None:
                # gpu:0 is the runners' CUDA0, as Hunyuan's CUDA0.
                return PaddlePlan("gpu:0", lock["paddle"]["gpu_wheels"]["distribution"], wheel,
                                  wheel["index"], need, tuple(notes)), notes
            notes.append(f"NVIDIA driver {gpu.driver} is older than every Paddle CUDA build allows "
                         "(cu118 needs 520.61.05 or newer), so PaddleOCR-VL runs on the CPU.")
    elif request == "cuda" and system == "Linux":
        notes.append("No NVIDIA GPU answers nvidia-smi, so PaddleOCR-VL runs on the CPU.")
    if cpu is None:
        notes.append(f"PaddlePaddle publishes no CPU wheel for {system} {machine}; PaddleOCR-VL is left out.")
        return None, notes
    return PaddlePlan("cpu", cpu["distribution"], cpu, None, 0, tuple(notes)), notes


# --- system dependencies ---------------------------------------------------------------

def engine_set(engine: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(engine, str):
        return tuple(part for part in engine.split(",") if part)
    return tuple(engine)


def missing_dependencies(engine: str | Iterable[str], *, building: bool = False) -> list[str]:
    engines = engine_set(engine)
    required = ["pdfinfo", "pdftoppm"]
    if "hunyuan" in engines and building:
        required += ["git", "cmake", "c++"]
    version_flag = {"pdfinfo": "-v", "pdftoppm": "-v"}
    missing = [item for item in required if not shutil.which(item) or
               not command_ok([item, version_flag.get(item, "--version")])]
    if "vision" in engines:
        if not shutil.which("swiftc"):
            missing.append("swiftc")
        if not command_ok(["xcode-select", "-p"]):
            missing.append("Apple Command Line Tools")
    return missing


def package_commands(missing: list[str], *, system: str) -> list[list[str]]:
    if not missing:
        return []
    if system == "Darwin":
        commands: list[list[str]] = []
        if any(name in missing for name in ("swiftc", "Apple Command Line Tools", "c++")):
            commands.append(["xcode-select", "--install"])
        if shutil.which("brew"):
            packages = []
            if any(name in missing for name in ("pdfinfo", "pdftoppm")):
                packages.append("poppler")
            packages.extend(name for name in ("git", "cmake") if name in missing)
            if packages:
                commands.append(["brew", "install", *packages])
        return commands
    if shutil.which("apt-get"):
        packages = []
        if any(name in missing for name in ("pdfinfo", "pdftoppm")):
            packages.append("poppler-utils")
        packages.extend(name for name in ("git", "cmake") if name in missing)
        if "c++" in missing:
            packages.append("build-essential")
        return [["sudo", "apt-get", "install", "-y", *packages]]
    if shutil.which("dnf"):
        names = {"pdfinfo": "poppler-utils", "pdftoppm": "poppler-utils", "c++": "gcc-c++"}
        return [["sudo", "dnf", "install", "-y", *sorted({names.get(n, n) for n in missing})]]
    if shutil.which("pacman"):
        names = {"pdfinfo": "poppler", "pdftoppm": "poppler", "c++": "base-devel"}
        return [["sudo", "pacman", "-S", "--needed", "--noconfirm", *sorted({names.get(n, n) for n in missing})]]
    return []


def ensure_dependencies(engine: str | Iterable[str], options: SetupOptions, system: str, *,
                        building: bool = False) -> None:
    missing = missing_dependencies(engine, building=building)
    if not missing:
        return
    commands = package_commands(missing, system=system)
    instructions = "\n".join("  " + shlex.join(cmd) for cmd in commands)
    if not options.install_system_deps:
        raise SetupError("Missing or unusable system dependencies: " + ", ".join(missing) + "\n" +
                         (instructions or "Install these using your OS package manager (Homebrew for Poppler on macOS).") +
                         "\nRun these commands, then rerun setup; remove broken wrappers from PATH if a package is already installed. --install-system-deps opts in to package-manager execution.")
    if options.offline:
        raise SetupError("System packages are missing; --offline prevents package-manager downloads.\n" + instructions)
    if not commands:
        raise SetupError("No supported package manager found; install: " + ", ".join(missing))
    for command in commands:
        if command[:2] == ["xcode-select", "--install"]:
            raise SetupError("Apple Command Line Tools need an interactive system dialog. Run xcode-select --install, finish it, then rerun setup.")
        if command[0] == "sudo":
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                command = command[1:]
            elif not options.interactive:
                command.insert(1, "-n")
        run(command)
    missing = missing_dependencies(engine, building=building)
    if missing:
        raise SetupError("Dependencies remain unavailable after installation: " + ", ".join(missing))


# --- Python environments ---------------------------------------------------------------

def find_uv() -> Path | None:
    """uv on PATH, else ~/.local/bin/uv (its installer's default). Never downloaded here."""
    for candidate in (shutil.which("uv"), str(Path.home() / ".local/bin/uv")):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate)
    return None


def uv_environment(options: SetupOptions) -> dict[str, str]:
    """uv keeps the Pythons it provisions and its wheel cache in the data folder.

    uv 0.8+ also links a ``pythonX.Y`` executable into its bin directory
    (``~/.local/bin`` by default) when it installs a Python.  The install passes
    ``--no-bin``; the bin directory is still pointed inside the data folder, so
    a uv that links anyway never writes outside it."""
    environment = dict(os.environ)
    environment.setdefault("UV_PYTHON_INSTALL_DIR", str(options.data_dir / "python-runtimes"))
    environment.setdefault("UV_CACHE_DIR", str(options.data_dir / "uv-cache"))
    environment["UV_PYTHON_BIN_DIR"] = str(options.data_dir / "python-runtimes" / "bin")
    return environment


def uv_install_has_no_bin(uv: Path, environment: Mapping[str, str]) -> bool:
    """Whether this uv's ``python install`` takes --no-bin (uv 0.8 and later).

    An older uv rejects the flag, and it never links an executable outside its
    install directory."""
    try:
        help_text = run([uv, "python", "install", "--help"], capture=True, env=dict(environment), timeout=60).stdout
    except SetupError:
        return False
    return "--no-bin" in (help_text or "")


def uv_find_has_system(uv: Path, environment: Mapping[str, str]) -> bool:
    """Whether this uv's ``python find`` takes --system (skip virtual environments)."""
    try:
        help_text = run([uv, "python", "find", "--help"], capture=True, env=dict(environment), timeout=60).stdout
    except SetupError:
        return False
    return "--system" in (help_text or "")


def uv_python(options: SetupOptions, uv: Path) -> Path:
    """A Python 3.12 that uv finds, provisioning one with uv when it finds none.

    Never a virtual environment: ``uv python find`` otherwise prefers the
    active one (VIRTUAL_ENV) and a .venv in the working directory or any parent
    (uv 0.10.2, run from inside a project, returned the project's
    .venv/bin/python3), so the venvs would be built from whatever project the
    installer ran in.  --system skips them and still finds uv-managed Pythons
    (UV_PYTHON_INSTALL_DIR) and those on PATH; a uv without --system looks from
    the data folder with VIRTUAL_ENV and CONDA_PREFIX unset."""
    environment = uv_environment(options)
    find: list[str | Path] = [uv, "python", "find", "--no-config"]
    find_environment = dict(environment)
    find_folder = None
    if uv_find_has_system(uv, environment):
        find.append("--system")
    else:
        for name in ("VIRTUAL_ENV", "CONDA_PREFIX"):
            find_environment.pop(name, None)
        options.data_dir.mkdir(parents=True, exist_ok=True)
        find_folder = options.data_dir
    find.append(PYTHON_VERSION)

    def found() -> Path:
        output = run(find, capture=True, env=find_environment, cwd=find_folder, timeout=60).stdout
        return Path((output or "").strip().splitlines()[-1])
    try:
        return found()
    except (SetupError, IndexError):
        pass
    if options.offline:
        raise SetupError(f"--offline: uv finds no Python {PYTHON_VERSION} and may not download one. Rerun without --offline.")
    print(f"Provisioning CPython {PYTHON_VERSION} with uv into {environment['UV_PYTHON_INSTALL_DIR']} (a download of about 30 MB).")
    install = [uv, "python", "install", "--no-config"]
    if uv_install_has_no_bin(uv, environment):
        install.append("--no-bin")
    run(install + [PYTHON_VERSION], env=environment, timeout=1800)
    try:
        return found()
    except IndexError as exc:
        raise SetupError(f"uv installed Python {PYTHON_VERSION} but cannot find it.") from exc


def set_aside(directory: Path) -> None:
    """Keep a broken environment beside its path instead of deleting it."""
    if directory.exists() or directory.is_symlink():
        directory.rename(directory.with_name(directory.name + "-incomplete-" + stamp()))


def version_pins(text: str) -> str:
    """A constraints file without its --hash options.  pip turns hash checking
    on for every package as soon as one requirement or constraint has a hash,
    and the verified wheel files and a platform's unlisted packages have none."""
    lines = []
    for line in re.sub(r"\\\n", " ", text).splitlines():
        if line.lstrip().startswith("#"):
            lines.append(line)
        else:
            lines.append(" ".join(token for token in line.split() if not token.startswith("--hash")))
    return "\n".join(lines) + "\n"


def install_requirements(options: SetupOptions, python: Path, requirements: Sequence[str], *,
                         uv: Path | None, requirement_file: Path | None = None,
                         constraints: Sequence[Path] = (), require_hashes: bool = False) -> None:
    """Install into PYTHON with uv, else pip.  CONSTRAINTS (files, in order)
    pin what the requirements pull in: uv checks their hashes too; pip gets
    their version pins only (version_pins)."""
    pins: list[Path] = []
    if uv is not None:
        command: list[str | Path] = [uv, "pip", "install", "--no-config", "--python", python]
        if options.offline:
            command.append("--offline")
        environment = uv_environment(options)
        for constraint in constraints:
            command += ["-c", constraint]
    else:
        command = [python, "-m", "pip", "install", "--disable-pip-version-check"]
        if options.offline:
            command.append("--no-index")
        environment = None
        for constraint in constraints:
            copy = options.data_dir / "downloads" / f"{constraint.stem}-versions.txt"
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(version_pins(constraint.read_text(encoding="utf-8")), encoding="utf-8")
            pins.append(copy)
            command += ["-c", copy]
    if requirement_file is not None:
        command += ["-r", requirement_file]
    if require_hashes:
        command += ["--require-hashes", "--only-binary", ":all:"]
    command += list(requirements)
    try:
        run(command, env=environment)
    finally:
        for copy in pins:
            copy.unlink(missing_ok=True)


def python_identity(executable: Path) -> dict[str, Any]:
    result = run([executable, "-c", "import sys,json,PIL; print(json.dumps({'version':sys.version,'version_info':list(sys.version_info[:3]),'prefix':sys.prefix,'base_prefix':sys.base_prefix,'pillow':PIL.__version__}))"], capture=True, timeout=20)
    try:
        identity = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise SetupError("Python/Pillow environment returned invalid identity data.") from exc
    if tuple(identity["version_info"]) < (3, 10):
        raise SetupError("Core Python must be version 3.10 or newer.")
    pillow_version = tuple(int(part) for part in re.match(r"^(\d+)\.(\d+)", identity["pillow"]).groups())
    if not (10, 4) <= pillow_version < (13, 0):
        raise SetupError("Core Pillow must satisfy requirements.txt (>=10.4,<13).")
    if identity["prefix"] == identity["base_prefix"]:
        raise SetupError("Core Python must be an isolated virtual environment.")
    return identity


def ensure_python(options: SetupOptions, existing: dict[str, str]) -> Path:
    """The core venv (JYUT_OCR_PYTHON): Python 3.12 from uv, else this interpreter.

    A venv whose interpreter no longer runs (an OS upgrade removed its base
    Python) is set aside and rebuilt at the same path."""
    candidate = Path(existing.get("JYUT_OCR_PYTHON", str(options.data_dir / "python/bin/python")))
    if candidate.is_file():
        try:
            python_identity(candidate)
            return candidate.absolute()
        except SetupError:
            pass
    venv = options.data_dir / "python"
    python = venv / "bin/python"
    uv = find_uv()
    usable_python = python.is_file() and command_ok([str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3,10))"])
    if uv is not None:
        if not usable_python:
            set_aside(venv)
            run([uv, "venv", "--no-config", "--python", uv_python(options, uv), venv], env=uv_environment(options))
    else:
        has_pip = usable_python and command_ok([str(python), "-m", "pip", "--version"])
        if not usable_python or not has_pip:
            # venv can fail after creating bin/python but before installing pip.
            # Preserve that partial environment and recreate at the final path so
            # switching bootstrap Python also repairs stale interpreter symlinks.
            set_aside(venv)
            try:
                run([sys.executable, "-m", "venv", venv])
            except SetupError as exc:
                raise SetupError("Could not create the isolated Python environment. " + UV_HINT +
                                 " Or, on Ubuntu/Debian, install python3-venv (matching the selected Python).\n" + str(exc)) from exc
    install_requirements(options, python, [], uv=uv, requirement_file=options.repo_root / "requirements.txt")
    python_identity(python)
    return python.absolute()


# Reads package metadata only: Paddle itself is never imported, so the probe
# starts nothing on a GPU and works in --check.
PADDLE_PROBE = r"""
import hashlib, importlib.metadata as m, json, re, sys
out = {"python": list(sys.version_info[:3]), "prefix": sys.prefix, "base_prefix": sys.base_prefix, "dists": {}}
for name in ("paddlepaddle", "paddlepaddle-gpu", "paddleocr", "paddlex"):
    try:
        dist = m.distribution(name)
    except m.PackageNotFoundError:
        continue
    entry = {"version": dist.version}
    if name.startswith("paddlepaddle"):
        entry["cuda"], entry["archs"] = None, []
        try:
            text = open(dist.locate_file("paddle/version/__init__.py"), encoding="utf-8").read()
        except OSError:
            text = ""
        cuda = re.search(r"^cuda_version\s*=\s*'([^']*)'", text, re.M)
        if cuda and re.fullmatch(r"\d+\.\d+", cuda.group(1)):
            entry["cuda"] = cuda.group(1)
        archs = re.search(r"^compiled_cuda_archs\s*=\s*\[([^\]]*)\]", text, re.M)
        if archs:
            entry["archs"] = [int(x) for x in archs.group(1).split(",") if x.strip().isdigit()]
        record = dist.read_text("RECORD")
        entry["recordSha256"] = hashlib.sha256(record.encode()).hexdigest() if record else None
    out["dists"][name] = entry
print(json.dumps(out))
"""


def paddle_probe(python: Path) -> dict[str, Any]:
    try:
        output = run([python, "-c", PADDLE_PROBE], capture=True, timeout=60).stdout or ""
        probe = json.loads(output.strip().splitlines()[-1])
    except (SetupError, ValueError, IndexError) as exc:
        raise SetupError(f"The Paddle Python {python} does not run or cannot report its packages.") from exc
    if not isinstance(probe, dict) or not isinstance(probe.get("dists"), dict):
        raise SetupError(f"The Paddle Python {python} returned invalid package data.")
    return probe


def cuda_of_index(index: str) -> str:
    """cu130 -> 13.0, cu118 -> 11.8."""
    digits = index[2:]
    return f"{digits[:-1]}.{digits[-1]}"


def paddle_mismatch(probe: Mapping[str, Any], plan: PaddlePlan, lock: Mapping[str, Any],
                    gpu: GPU | None) -> str | None:
    """Why PROBE's environment does not match PLAN, or None when it does."""
    dists = probe.get("dists") or {}
    python = tuple(probe.get("python") or ())[:2]
    if not (3, 10) <= python < (3, 14):
        return f"its Python is {'.'.join(map(str, python)) or 'unknown'}; Paddle needs 3.10 to 3.13"
    other = "paddlepaddle" if plan.distribution == "paddlepaddle-gpu" else "paddlepaddle-gpu"
    if other in dists:
        return f"it has {other}, not {plan.distribution} only"
    installed = dists.get(plan.distribution)
    wanted = lock["paddle"]["paddle_version"]
    if not installed or installed.get("version") != wanted:
        return f"it lacks {plan.distribution}=={wanted}"
    for package in lock["paddle"]["packages"]:
        if (dists.get(package["distribution"]) or {}).get("version") != package["version"]:
            return f"it lacks {package['distribution']}=={package['version']}"
    if plan.gpu:
        cuda = installed.get("cuda")
        allowed = {cuda_of_index(wheel["index"]): wheel["min_driver"]
                   for wheel in lock["paddle"]["gpu_wheels"]["wheels"]}
        if cuda not in allowed:
            return f"its Paddle build has CUDA {cuda or 'none'}, which this lock does not know"
        if gpu is not None and version_tuple(gpu.driver) < version_tuple(allowed[cuda]):
            return f"its CUDA {cuda} build needs NVIDIA driver {allowed[cuda]} or newer"
        capability = capability_tuple(gpu.compute_capability) if gpu else None
        archs = installed.get("archs") or []
        if capability and archs and not any(arch // 10 == capability[0] and arch % 10 <= capability[1] for arch in archs):
            return f"its Paddle build has no kernels for compute capability {gpu.compute_capability}"
    return None


def record_trust(state_path: Path, name: str, info: dict[str, Any]) -> None:
    """Add a trust-on-first-use SHA-256 to setup-state.json now, so a later
    download is checked against it even if this setup stops before the end."""
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    trusted = state.setdefault("trustOnFirstUse", {})
    trusted[name] = info
    atomic_json(state_path, state)


def trust_key(wheel: Mapping[str, Any]) -> str:
    return f"{wheel.get('index', 'pypi')}/{wheel['name']}"


def fetch_wheel(options: SetupOptions, wheel: Mapping[str, Any], trusted: Mapping[str, Any],
                state_path: Path) -> Path:
    """Download WHEEL into the data folder's downloads/ and verify it.

    PyPI wheels have a locked SHA-256.  Paddle's GPU index publishes none: the
    first download's SHA-256 is recorded (trust on first use) and any later
    download must match it."""
    folder = options.data_dir / "downloads" / wheel.get("index", "pypi")
    target = folder / wheel["name"]
    asset = {"size": wheel["size"], "sha256": wheel.get("sha256")}
    first_use = asset["sha256"] is None
    if first_use:
        known = trusted.get(trust_key(wheel))
        if known:
            asset["sha256"] = known["sha256"]
            first_use = False
    try:
        download_asset(wheel["url"], target, asset, offline=options.offline)
    except SetupError as exc:
        if wheel.get("sha256") is None and asset["sha256"] and str(exc).startswith("Download did not match"):
            raise SetupError(f"{wheel['name']} from Paddle's {wheel.get('index')} index differs from the SHA-256 recorded "
                             f"when it was first downloaded ({asset['sha256']}; trust on first use). It was not "
                             "installed. If Paddle republished the wheel on purpose, remove its entry from "
                             "trustOnFirstUse in setup-state.json and rerun.") from exc
        raise
    if first_use:
        digest = sha256_file(target)
        print(f"Trust on first use: {wheel['name']} ({wheel.get('index')}) has SHA-256 {digest}; "
              "later downloads must match it.")
        record_trust(state_path, trust_key(wheel), {"sha256": digest, "size": wheel["size"],
                                                     "url": wheel["url"], "recordedAt": now()})
    return target


def paddle_venv_python(options: SetupOptions, uv: Path | None) -> Path:
    """The Python 3.12 a new Paddle venv is made from."""
    if uv is not None:
        return uv_python(options, uv)
    system = shutil.which("python" + PYTHON_VERSION)
    if system and command_ok([system, "-c", "import venv, ensurepip"]):
        return Path(system)
    raise SetupError(f"PaddleOCR-VL needs its own Python {PYTHON_VERSION} environment, and neither uv nor a "
                     f"python{PYTHON_VERSION} with venv is available. " + UV_HINT)


def paddle_constraints(root: Path, wheel: Mapping[str, Any]) -> list[Path]:
    """The constraints files a Paddle install uses: PADDLE_CONSTRAINTS for
    every wheel, then, for a GPU wheel, the file of its own CUDA build
    (PADDLE_BUILD_CONSTRAINTS) when the skill has one.  Each build pins its own
    CUDA packages (cu129 x86_64 asks cuda-python==12.9.4, cu130 13.0.3), so
    they are never in the common file."""
    common = root / PADDLE_CONSTRAINTS
    if not common.is_file():
        raise SetupError(f"{PADDLE_CONSTRAINTS} is missing from the skill; reinstall the skill from its repository.")
    files = [common]
    if wheel.get("index"):
        build = root / PADDLE_BUILD_CONSTRAINTS.format(index=wheel["index"], machine=wheel.get("machine", ""))
        if build.is_file():
            files.append(build)
    return files


def reusable_paddle_python(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any],
                           plan: PaddlePlan, gpu: GPU | None) -> Path | None:
    """The Paddle Python ensure_paddle_python() keeps as it is, or None when it
    will install into DATA/paddle-python: the configured PADDLEOCR_PYTHON, else
    the managed venv, whose packages already match PLAN (paddle_probe reads
    their metadata only; Paddle is not imported)."""
    for candidate in (existing.get("PADDLEOCR_PYTHON"), str(options.data_dir / "paddle-python/bin/python")):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            probe = paddle_probe(Path(candidate))
        except SetupError:
            continue
        if paddle_mismatch(probe, plan, lock, gpu) is None:
            return Path(os.path.abspath(candidate))
    return None


def ensure_paddle_python(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any],
                         plan: PaddlePlan, gpu: GPU | None, state_path: Path,
                         trusted: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    """PADDLEOCR_PYTHON: a matching existing environment, else the managed one.

    An existing environment the person made is used only when it already holds
    the pinned packages for this device; it is never modified.  The managed
    venv (DATA/paddle-python) is repaired in place, or set aside and rebuilt
    when its interpreter no longer runs."""
    venv = options.data_dir / "paddle-python"
    managed = venv / "bin/python"
    configured = existing.get("PADDLEOCR_PYTHON")
    current: dict[str, Any] | None = None  # the managed venv's packages, when its Python runs
    for candidate, source in ((configured, "configured"), (str(managed), "managed")):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            probe = paddle_probe(Path(candidate))
        except SetupError as exc:
            if source == "configured":
                print(f"PADDLEOCR_PYTHON={candidate} is not used: {exc}")
            continue
        if Path(os.path.abspath(candidate)) == managed.absolute():
            current = probe
        problem = paddle_mismatch(probe, plan, lock, gpu)
        if problem is None:
            return Path(os.path.abspath(candidate)), probe
        if source == "configured" and Path(os.path.abspath(candidate)) != managed.absolute():
            print(f"PADDLEOCR_PYTHON={candidate} is not used: {problem}. Setting up {managed} instead.")
    uv = find_uv()
    if current is None or ".".join(map(str, current.get("python", [])[:2])) != PYTHON_VERSION:
        # Gone, broken by an OS upgrade, or another Python: keep it aside, rebuild.
        set_aside(venv)
        base = paddle_venv_python(options, uv)
        if uv is not None:
            run([uv, "venv", "--no-config", "--python", base, venv], env=uv_environment(options))
        else:
            run([base, "-m", "venv", venv])
        current = {"dists": {}}
    wheels = [(plan.distribution, plan.wheel, []), *((package["distribution"], package, package.get("extras", []))
                                                     for package in lock["paddle"]["packages"])]
    files = []
    for distribution, wheel, extras in wheels:
        path = fetch_wheel(options, wheel, trusted, state_path)
        extra = f"[{','.join(extras)}]" if extras else ""
        files.append((path, f"{distribution}{extra} @ {path.absolute().as_uri()}"))
    other = "paddlepaddle" if plan.distribution == "paddlepaddle-gpu" else "paddlepaddle-gpu"
    if other in current.get("dists", {}):
        # Both distributions install the same `paddle` package.
        if uv is not None:
            run([uv, "pip", "uninstall", "--no-config", "--python", managed, other], env=uv_environment(options))
        else:
            run([managed, "-m", "pip", "uninstall", "-y", other])
    constraints = paddle_constraints(options.repo_root, plan.wheel)
    if uv is None:
        print(f"NOTE Without uv the Paddle environment's other packages are pinned to the versions of "
              f"{', '.join(path.relative_to(options.repo_root).as_posix() for path in constraints)}, "
              "but their hashes are not checked (pip would ask hashes of every package).")
    install_requirements(options, managed, [reference for _, reference in files], uv=uv, constraints=constraints)
    probe = paddle_probe(managed)
    problem = paddle_mismatch(probe, plan, lock, gpu)
    if problem is not None:
        raise SetupError(f"The Paddle environment {managed} still does not match after installation: {problem}.")
    for path, _ in files:
        # Verified and installed; the hashes stay in the lock or setup-state.json.
        path.unlink(missing_ok=True)
    return managed.absolute(), probe


MLX_METADATA_PROBE = r"""
import importlib.metadata as m, json, sys
out = {"python": list(sys.version_info[:3]), "prefix": sys.prefix, "base_prefix": sys.base_prefix, "dists": {}}
for name in json.loads(sys.argv[1]):
    try:
        out["dists"][name] = m.version(name)
    except m.PackageNotFoundError:
        pass
print(json.dumps(out))
"""


def mlx_probe(python: Path, lock: Mapping[str, Any]) -> dict[str, Any]:
    """Metadata only: --check never imports MLX, initializes Metal or loads weights."""
    names = [package["name"] for package in lock["mlx"]["packages"]]
    try:
        output = run([python, "-c", MLX_METADATA_PROBE, json.dumps(names)], capture=True, timeout=60).stdout or ""
        probe = json.loads(output.strip().splitlines()[-1])
    except (SetupError, ValueError, IndexError) as exc:
        raise SetupError(f"The MLX Python {python} does not run or cannot report its packages.") from exc
    if not isinstance(probe, dict) or not isinstance(probe.get("dists"), dict):
        raise SetupError(f"The MLX Python {python} returned invalid package data.")
    return probe


def mlx_mismatch(probe: Mapping[str, Any], lock: Mapping[str, Any]) -> str | None:
    if tuple(probe.get("python") or ())[:2] != (3, 12):
        return "it requires Python 3.12"
    if not probe.get("prefix") or probe.get("prefix") == probe.get("base_prefix"):
        return "it is not an isolated Python environment"
    for package in lock["mlx"]["packages"]:
        if (probe.get("dists") or {}).get(package["name"]) != package["version"]:
            return f"it lacks {package['name']}=={package['version']}"
    return None


def reusable_mlx_python(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any]) -> Path | None:
    separate = {os.path.abspath(existing[key]) for key in ("PADDLEOCR_PYTHON", "JYUT_OCR_PYTHON") if existing.get(key)}
    other_roots = {Path(path).parent.parent.resolve() for path in separate if Path(path).parent.name == "bin"}
    for candidate in (existing.get("MLX_VLM_PYTHON"), str(options.data_dir / "mlx-python/bin/python")):
        if not candidate or os.path.abspath(candidate) in separate or not Path(candidate).is_file():
            continue
        try:
            probe = mlx_probe(Path(candidate), lock)
            if (mlx_mismatch(probe, lock) is None and Path(probe["prefix"]).resolve() not in other_roots):
                return Path(os.path.abspath(candidate))
        except SetupError:
            pass
    return None


def ensure_mlx_python(options: SetupOptions, existing: Mapping[str, str],
                      lock: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Reuse a verified environment or install the complete hash-locked MLX venv."""
    reusable = reusable_mlx_python(options, existing, lock)
    if reusable is not None:
        return reusable, mlx_probe(reusable, lock)
    venv = options.data_dir / "mlx-python"
    managed = venv / "bin/python"
    if venv.is_symlink():
        set_aside(venv)  # never repair another environment through a managed-path symlink
    current = None
    if managed.is_file():
        try:
            current = mlx_probe(managed, lock)
        except SetupError:
            pass
    uv = find_uv()
    if (current is None or tuple(current.get("python") or ())[:2] != (3, 12)
            or current.get("prefix") == current.get("base_prefix")):
        set_aside(venv)
        base = paddle_venv_python(options, uv)
        if uv is not None:
            run([uv, "venv", "--no-config", "--python", base, venv], env=uv_environment(options))
        else:
            run([base, "-m", "venv", venv])
    install_requirements(options, managed, [], uv=uv,
                         requirement_file=options.repo_root / lock["mlx"]["requirements"], require_hashes=True)
    probe = mlx_probe(managed, lock)
    problem = mlx_mismatch(probe, lock)
    if problem is not None:
        raise SetupError(f"The MLX environment {managed} still does not match after installation: {problem}.")
    return managed.absolute(), probe


# --- downloads ------------------------------------------------------------------------

def verify_asset(path: Path, asset: dict[str, Any]) -> bool:
    """Size and SHA-256 match (size only for a trust-on-first-use wheel not yet seen)."""
    return (path.is_file() and path.stat().st_size == asset["size"] and
            (asset.get("sha256") is None or sha256_file(path) == asset["sha256"]))


def download_asset(url: str, target: Path, asset: dict[str, Any], *, offline: bool = False) -> Path:
    """Resume only a correctly ranged response, then seal before atomic promotion."""
    if verify_asset(target, asset):
        return target
    if target.exists():
        raise SetupError(f"Existing model has the wrong size/hash: {target}. It was left untouched; move it aside before retrying.")
    if offline:
        raise SetupError(f"Offline: no verified local copy of {target}; rerun without --offline to download it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists() and partial.stat().st_size > asset["size"]:
        raise SetupError(f"Oversized partial download left untouched: {partial}; move it aside and retry.")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == asset["size"]:
        if verify_asset(partial, asset):
            os.replace(partial, target)
            return target
        partial.rename(partial.with_name(partial.name + ".invalid-" + stamp()))
        offset = 0
    headers = {"User-Agent": "jyut-ocr-proofread-setup/1", "Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            status = response.status
            if status == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                if not match or int(match[1]) != offset or int(match[3]) != asset["size"] or int(match[2]) >= asset["size"]:
                    raise SetupError("Download server returned an invalid Content-Range; partial data retained.")
                mode = "ab" if offset else "wb"
            elif status == 200:
                # Servers may ignore Range; truncate, never append a full object.
                mode = "wb"
            else:
                raise SetupError(f"Unexpected download HTTP status {status}")
            with partial.open(mode) as stream:
                for chunk in iter(lambda: response.read(1024 * 1024), b""):
                    stream.write(chunk)
                    if stream.tell() > asset["size"]:
                        raise SetupError("Download exceeded locked size; partial retained for inspection.")
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, urllib.error.URLError) as exc:
        raise DownloadInterrupted(f"Download interrupted; rerun to resume {partial.name}: {exc}") from exc
    if not verify_asset(partial, asset):
        if partial.stat().st_size == asset["size"]:
            partial.rename(partial.with_name(partial.name + ".invalid-" + stamp()))
        raise SetupError(f"Download did not match its locked size/SHA-256: {target.name}; nothing was installed. Rerun to resume or retry.")
    os.replace(partial, target)
    return target


def ensure_models(options: SetupOptions, existing: dict[str, str], lock: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for asset in lock["model"]["files"]:
        key = "HUNYUAN_MODEL" if asset["role"] == "model" else "HUNYUAN_MMPROJ"
        candidates = [Path(existing[key])] if existing.get(key) else []
        candidates += [Path.home() / "models/hunyuanocr" / asset["name"]]
        target = options.data_dir / "models" / lock["model"]["revision"] / asset["name"]
        candidates.append(target)
        chosen = next((p for p in candidates if verify_asset(p, asset)), None)
        if chosen is None:
            url = f"https://huggingface.co/{lock['model']['repo']}/resolve/{lock['model']['revision']}/{asset['name']}"
            chosen = download_asset(url, target, asset, offline=options.offline)
        found[key] = str(chosen.absolute())
    return found


PADDLE_MODEL_KEYS = {"vl": "PADDLE_VL_MODEL_DIR", "layout": "PADDLE_LAYOUT_MODEL_DIR"}


def paddlex_cache() -> Path:
    return Path(os.environ.get("PADDLE_PDX_CACHE_HOME") or Path.home() / ".paddlex") / "official_models"


def paddle_model_verified(directory: Path, model: Mapping[str, Any]) -> bool:
    return all(verify_asset(directory / item["name"], item) for item in model["files"])


def link_or_copy(source: Path, target: Path) -> None:
    """Hard-link SOURCE (read only) to TARGET, or copy it; never modifies SOURCE."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        os.link(source, partial)
    except OSError:
        shutil.copyfile(source, partial)
    os.replace(partial, target)


def ensure_paddle_weights(options: SetupOptions, existing: Mapping[str, str],
                          lock: Mapping[str, Any]) -> dict[str, str]:
    """PADDLE_VL_MODEL_DIR and PADDLE_LAYOUT_MODEL_DIR: the pinned revisions, file
    by file at their locked size and SHA-256, in DATA/paddle-models.

    Verified files already in PaddleX's own cache are linked or copied instead
    of downloaded; a configured folder that verifies is kept."""
    found: dict[str, str] = {}
    for model in lock["paddle"]["models"]:
        key = PADDLE_MODEL_KEYS[model["role"]]
        configured = existing.get(key)
        if configured and paddle_model_verified(Path(configured), model):
            found[key] = str(Path(configured).absolute())
            continue
        target = options.data_dir / "paddle-models" / model["name"] / model["revision"]
        cache = paddlex_cache() / model["name"]
        for item in model["files"]:
            destination = target / item["name"]
            if verify_asset(destination, item):
                continue
            cached = cache / item["name"]
            if not destination.exists() and verify_asset(cached, item):
                link_or_copy(cached, destination)
                if verify_asset(destination, item):
                    continue
            url = f"https://huggingface.co/{model['repo']}/resolve/{model['revision']}/{urllib.parse.quote(item['name'])}"
            download_asset(url, destination, item, offline=options.offline)
        found[key] = str(target.absolute())
    return found


# --- the llama.cpp runtime --------------------------------------------------------------

def runtime_libraries(binary: Path) -> dict[str, str]:
    """Bind the local shared libraries that carry most of a llama-server build."""
    libraries = {}
    for path in sorted(binary.parent.glob("lib*")):
        if path.is_file() and (path.name.endswith((".so", ".dylib")) or ".so." in path.name):
            libraries[path.name] = sha256_file(path)
    return libraries


def runtime_identity(binary: Path, lock: dict[str, Any], device: str) -> dict[str, Any]:
    """Attest a managed build or release binary, or verify an existing pinned checkout and --version."""
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SetupError(f"Missing executable runtime: {binary}")
    digest = sha256_file(binary)
    libraries = runtime_libraries(binary)
    # A sealed executable can still stop working when a shared library changes.
    # --version loads no model and also verifies the advertised pinned revision.
    version = run([binary, "--version"], capture=True, timeout=30).stdout or ""
    abbreviations = re.findall(r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])", version)
    # llama.cpp uses git rev-parse --short (often seven characters). Exact
    # forty-character identity is separately checked against the source/seal.
    if not any(lock["runtime"]["commit"].startswith(value) for value in abbreviations):
        raise SetupError("Existing runtime does not report the pinned llama.cpp commit.")
    for parent in [binary.parent, *binary.parents]:
        manifest_path = parent / "jyut-ocr-build.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            if manifest.get("commit") == lock["runtime"]["commit"] and manifest.get("binary_sha256") == digest and manifest.get("libraries", {}) == libraries and (device == "cpu" or manifest.get("device") == device):
                return {"path": str(binary.absolute()), "sha256": digest, "commit": manifest["commit"], "compiled_device": manifest["device"], "libraries": libraries,
                        "verification": "sealed-release-binary" if manifest.get("source") == "release" else "sealed-build",
                        **({"assets": manifest["assets"]} if manifest.get("assets") else {})}
    source = next((p for p in binary.parents if (p / ".git").exists()), None)
    if source is None:
        raise SetupError("Existing runtime has no verifiable pinned source checkout or sealed build manifest.")
    head = run(["git", "rev-parse", "HEAD"], cwd=source, capture=True).stdout.strip()
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, capture=True).stdout.strip()
    if head != lock["runtime"]["commit"] or dirty:
        raise SetupError("Existing runtime source is not the clean pinned commit.")
    cache = next((p / "CMakeCache.txt" for p in binary.parents if (p / "CMakeCache.txt").is_file()), None)
    settings = cache.read_text() if cache else ""
    compiled = "cuda" if re.search(r"^GGML_CUDA:BOOL=(ON|1|TRUE)$", settings, re.M) else "metal" if re.search(r"^GGML_METAL:BOOL=(ON|1|TRUE)$", settings, re.M) else "cpu"
    if device != "cpu" and compiled != device:
        raise SetupError(f"Existing runtime is not configured for {device}.")
    return {"path": str(binary.absolute()), "sha256": digest, "commit": head, "compiled_device": compiled, "libraries": libraries, "verification": "clean-pinned-source-and-version"}


def release_binary(lock: Mapping[str, Any], *, system: str, machine: str, device: str,
                   gpu: GPU | None) -> dict[str, Any] | None:
    """The pinned release's prebuilt llama.cpp for this platform and device, if any.

    CUDA builds need the driver their CUDA release supports (13.3: 580 or
    newer, 12.8: 570.26 or newer); no CUDA toolkit is needed, because the
    release's CUDA runtime bundle is installed beside the binary."""
    assets = [asset for asset in lock["runtime"].get("prebuilt", [])
              if (asset["system"], asset["machine"]) == (system, machine)]
    for asset in assets:
        if asset["device"] != device:
            continue
        if device == "cuda" and (gpu is None or version_tuple(gpu.driver) < version_tuple(asset["min_driver"])):
            continue
        return asset
    if device == "cpu":
        # The macOS arm64 release has Metal built in; with --device cpu the
        # runners turn every offload off.
        return next((asset for asset in assets if asset["device"] != "cuda"), None)
    return None


def _member_name(name: str, strip: str | None) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise SetupError(f"Archive member escapes its folder: {name}")
    parts = path.parts
    if strip is not None:
        parts = parts[1:]
    return "/".join(parts) if parts else None


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract a release .tar.gz into DESTINATION, dropping its one top folder.

    Regular files, folders and symlinks that stay inside DESTINATION only."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        parts = [PurePosixPath(member.name).parts for member in members]
        tops = {part[0] for part in parts if part}
        top = next(iter(tops)) if len(tops) == 1 else None
        # Release archives hold one top folder (llama-b10969/ ...); drop it.
        strip = top if top is not None and all(
            len(part) > 1 or member.isdir() for part, member in zip(parts, members)) else None
        for member in members:
            name = _member_name(member.name, strip)
            if name is None:
                continue
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, target.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            elif member.issym():
                link = PurePosixPath(member.linkname)
                resolved = PurePosixPath(name).parent / link
                if link.is_absolute() or ".." in PurePosixPath(os.path.normpath(str(resolved))).parts:
                    raise SetupError(f"Archive link escapes its folder: {member.name} -> {member.linkname}")
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or target.exists():
                    target.unlink()
                os.symlink(member.linkname, target)
            else:
                raise SetupError(f"Archive member of an unsupported type: {member.name}")


def install_release_binary(options: SetupOptions, lock: Mapping[str, Any], asset: Mapping[str, Any]) -> Path:
    """Download the pinned release archive (and its CUDA runtime bundle), verify
    each by size and the release's SHA-256, extract them into one folder and seal it."""
    runtime = lock["runtime"]
    final = options.data_dir / "runtimes" / (runtime["commit"] + "-release-" + asset["name"].removesuffix(".tar.gz"))
    archives = [asset, *([asset["runtime_bundle"]] if asset.get("runtime_bundle") else [])]
    paths = []
    for item in archives:
        target = options.data_dir / "downloads" / "llama.cpp" / item["name"]
        paths.append(download_asset(runtime["release_url"] + item["name"], target, dict(item), offline=options.offline))
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=final.parent))
    try:
        for path in paths:
            extract_archive(path, staging / "bin")
        binary = staging / "bin" / runtime["target"]
        if not binary.is_file():
            raise SetupError(f"{asset['name']} holds no {runtime['target']}.")
        binary.chmod(0o755)
        atomic_json(staging / "jyut-ocr-build.json", {
            "schema_version": 1, "commit": runtime["commit"], "tag": runtime.get("tag"), "device": asset["device"],
            "source": "release", "assets": [{"name": item["name"], "sha256": item["sha256"]} for item in archives],
            "binary_sha256": sha256_file(binary), "libraries": runtime_libraries(binary), "created_at": now()})
        set_aside(final)
        os.replace(staging, final)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    for path in paths:
        path.unlink(missing_ok=True)
    return final / "bin" / runtime["target"]


def reusable_runtime(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any], device: str,
                     release: Mapping[str, Any] | None) -> tuple[Path, dict[str, Any]] | None:
    """The llama-server ensure_runtime() reuses, with its identity, or None
    when it must install the release binary RELEASE or build one.

    The candidates, in order: the configured HUNYUAN_LLAMA_SERVER,
    ~/llama.cpp's build, the release binary in the data folder and the managed
    source build; the first that runtime_identity() verifies for DEVICE."""
    managed = options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-" + device)
    candidates = [Path(existing["HUNYUAN_LLAMA_SERVER"])] if existing.get("HUNYUAN_LLAMA_SERVER") else []
    candidates += [Path.home() / "llama.cpp/build/bin/llama-server"]
    if release is not None:
        candidates.append(options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-release-" + release["name"].removesuffix(".tar.gz")) / "bin" / lock["runtime"]["target"])
    candidates.append(managed / "build/bin/llama-server")
    for candidate in candidates:
        try:
            return candidate.absolute(), runtime_identity(candidate, lock, device)
        except (SetupError, ValueError, KeyError, OSError):
            continue
    return None


_UNRUNNABLE = re.compile(r"version `((?:GLIBC|GLIBCXX|CXXABI)_[0-9.]+)' not found|"
                         r"([A-Za-z0-9_.+-]+\.so[0-9.]*): cannot open shared object file")


def unrunnable_reason(message: str) -> str | None:
    """What the system lacks, where a binary failed to start for want of a C
    library version or a shared library (the loader's messages); None for any
    other failure."""
    found = sorted({a or b for a, b in _UNRUNNABLE.findall(message)})
    return ("needs " + ", ".join(found)) if found else None


def ensure_runtime(options: SetupOptions, existing: dict[str, str], lock: dict[str, Any], device: str, *,
                   release: Mapping[str, Any] | None = None) -> tuple[Path, dict[str, Any]]:
    """A verified pinned llama-server: reuse one, else the release binary
    RELEASE, else a source build (needs git, cmake, a C++ compiler, and nvcc for CUDA)."""
    managed = options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-" + device)
    binary = managed / "build/bin/llama-server"
    reused = reusable_runtime(options, existing, lock, device, release)
    if reused is not None:
        return reused
    if release is not None:
        installed = install_release_binary(options, lock, release)
        try:
            return installed, runtime_identity(installed, lock, device)
        except SetupError as error:
            missing = unrunnable_reason(str(error))
            if missing is None:
                raise
            # The release binaries are built on a newer Linux than some hosts
            # run (b10969's Ubuntu builds need glibc 2.38; Ubuntu 22.04 has
            # 2.35): the documented fallback, the same commit from source.
            print(f"The pinned release binary {release['name']} does not run on this system ({missing}); "
                  "building the same pinned commit from source instead.", flush=True)
    # A verified runtime needs no compiler/CMake installation. Only a fresh or
    # resumed build requires the build toolchain; PDF tools were checked above.
    nvcc = nvcc_path() if device == "cuda" else None
    if device == "cuda" and nvcc is None:
        raise SetupError("No prebuilt llama.cpp CUDA build of the pinned release supports this NVIDIA driver, and "
                         "the CUDA toolkit (nvcc) needed to build one is not installed. Update the driver "
                         "(570.26 or newer) or install the CUDA toolkit; setup never installs drivers/toolkits.")
    if device == "metal" and not clang_available():
        raise SetupError("Building llama.cpp for Metal needs the Apple Command Line Tools: run xcode-select --install, then rerun setup.")
    ensure_dependencies("hunyuan", options, platform_name(), building=True)
    source = managed / "source"
    if not (source / ".git").exists():
        if options.offline:
            raise SetupError("Offline cache lacks a verified pinned runtime/source checkout.")
        source.mkdir(parents=True, exist_ok=True)
        run(["git", "init", source])
    # git init and adding its remote are separate resumable stages.
    try:
        origin = run(["git", "remote", "get-url", "origin"], cwd=source, capture=True).stdout.strip()
    except SetupError:
        run(["git", "remote", "add", "origin", lock["runtime"]["repo"]], cwd=source)
    else:
        if origin != lock["runtime"]["repo"]:
            raise SetupError(f"Managed runtime has an unexpected origin; leave it intact and use a clean data directory: {source}")
    try:
        head = run(["git", "rev-parse", "HEAD"], cwd=source, capture=True).stdout.strip()
    except SetupError:
        head = ""
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, capture=True).stdout.strip():
        raise SetupError(f"Managed runtime source has local edits; leave them intact and use a clean data directory: {source}")
    if head != lock["runtime"]["commit"]:
        try:
            run(["git", "cat-file", "-e", lock["runtime"]["commit"] + "^{commit}"], cwd=source, capture=True)
        except SetupError:
            if options.offline:
                raise SetupError("Offline source checkout does not contain the pinned commit.")
            run(["git", "fetch", "--depth", "1", "origin", lock["runtime"]["commit"]], cwd=source)
        run(["git", "checkout", "--detach", lock["runtime"]["commit"]], cwd=source)
    # Metal builds keep llama.cpp's default GGML_METAL_EMBED_LIBRARY, which
    # needs no offline `metal` compiler.
    configure = ["cmake", "-S", str(source), "-B", str(managed / "build"),
                 "-DCMAKE_BUILD_TYPE=Release", "-DGGML_CUDA=" + ("ON" if device == "cuda" else "OFF"),
                 "-DGGML_METAL=" + ("ON" if device == "metal" else "OFF"),
                 "-DLLAMA_OPENSSL=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF", "-DLLAMA_BUILD_UI=OFF",
                 "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_SERVER=ON"]
    if nvcc and not shutil.which("nvcc"):
        configure.append("-DCMAKE_CUDA_COMPILER=" + nvcc)
    run(configure)
    run(["cmake", "--build", managed / "build", "--config", "Release", "--target", "llama-server", "-j", str(min(os.cpu_count() or 1, 8))])
    # A failed build is resumable; do not seal it or overwrite an unrelated binary.
    if not binary.is_file():
        raise SetupError("Runtime build completed without producing llama-server.")
    atomic_json(managed / "jyut-ocr-build.json", {"schema_version": 1, "commit": lock["runtime"]["commit"], "device": device,
                "binary_sha256": sha256_file(binary), "libraries": runtime_libraries(binary), "configure": configure, "created_at": now()})
    return binary, runtime_identity(binary, lock, device)


# --- free disk --------------------------------------------------------------------------

def _sized(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def disk_needs(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any],
               engines: Sequence[str], release: Mapping[str, Any] | None,
               plan: PaddlePlan | None, *, device: str | None = None,
               gpu: GPU | None = None) -> list[tuple[str, int]]:
    """Estimated bytes the missing parts take (downloads at their size, unpacked
    parts estimated).  Model files are judged by size, so they are not hashed
    twice.  The llama.cpp runtime and the Paddle environment are judged as
    setup will judge them (reusable_runtime() for Hunyuan's DEVICE,
    reusable_paddle_python() for PLAN and GPU), so a configured one that is
    the wrong commit or holds other packages counts as the replacement setup
    installs, not as present."""
    needs: list[tuple[str, int]] = []
    if not Path(existing.get("JYUT_OCR_PYTHON", str(options.data_dir / "python/bin/python"))).is_file():
        needs.append(("core Python environment", CORE_VENV_BYTES + PYTHON_RUNTIME_BYTES))
    if "hunyuan" in engines:
        missing = 0
        for asset in lock["model"]["files"]:
            key = "HUNYUAN_MODEL" if asset["role"] == "model" else "HUNYUAN_MMPROJ"
            places = [Path(existing[key])] if existing.get(key) else []
            places += [Path.home() / "models/hunyuanocr" / asset["name"],
                       options.data_dir / "models" / lock["model"]["revision"] / asset["name"]]
            if not any(_sized(place, asset["size"]) for place in places):
                missing += asset["size"]
        if missing:
            needs.append(("HunyuanOCR weights", missing))
        runtime_device = device or (release["device"] if release is not None else "cpu")
        if reusable_runtime(options, existing, lock, runtime_device, release) is None:
            if release is not None:
                archives = [release, *([release["runtime_bundle"]] if release.get("runtime_bundle") else [])]
                size = sum(item["size"] for item in archives)
                needs.append((f"llama.cpp {release['name']}", int(size * (1 + EXTRACTED_RUNTIME_FACTOR))))
            else:
                needs.append(("llama.cpp source build", SOURCE_BUILD_BYTES))
    if "paddle" in engines and plan is not None:
        missing = 0
        for model in lock["paddle"]["models"]:
            configured = existing.get(PADDLE_MODEL_KEYS[model["role"]])
            target = options.data_dir / "paddle-models" / model["name"] / model["revision"]
            for item in model["files"]:
                places = [target / item["name"], paddlex_cache() / model["name"] / item["name"]]
                if configured:
                    places.insert(0, Path(configured) / item["name"])
                if not any(_sized(place, item["size"]) for place in places):
                    missing += item["size"]
        if missing:
            needs.append(("PaddleOCR-VL weights", missing))
        if reusable_paddle_python(options, existing, lock, plan, gpu) is None:
            extra = PADDLE_GPU_DEPENDENCY_BYTES if plan.gpu else PADDLE_CPU_DEPENDENCY_BYTES
            needs.append((f"Paddle environment ({plan.distribution} {plan.index or 'cpu'})",
                          int(plan.wheel["size"] * 2.5) + extra + PYTHON_RUNTIME_BYTES))
        if plan.backend == "mlx-vlm-server" and reusable_mlx_python(options, existing, lock) is None:
            needs.append(("MLX VL environment (isolated Python 3.12)", MLX_DEPENDENCY_BYTES + PYTHON_RUNTIME_BYTES))
    return needs


def check_free_disk(options: SetupOptions, needs: Sequence[tuple[str, int]]) -> None:
    if not needs:
        return
    total = sum(size for _, size in needs) + DISK_MARGIN_BYTES
    probe = options.data_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    detail = ", ".join(f"{label} about {size / 1e9:.1f} GB" for label, size in needs)
    if free < total:
        raise SetupError(f"Not enough free disk in {probe}: about {total / 1e9:.1f} GB is needed ({detail}, plus a 1 GiB margin) "
                         f"and {free / 1e9:.1f} GB is free. Free some space or pass another --data-dir; nothing was downloaded.")
    print(f"Disk: about {total / 1e9:.1f} GB needed in {probe} ({detail}); {free / 1e9:.1f} GB free.")


# --- identity and smoke tests -----------------------------------------------------------

ENVIRONMENT_FILES = ("requirements.txt", "runtime/ocr-lock.json")
# Every other package of the Paddle environment, pinned with PyPI's hashes
# (runtime/make_paddle_constraints.py): the common file for every Paddle
# wheel, and one per CUDA build for what that build's wheel pins exactly.
# Recorded, not part of the fingerprint: package versions do not ask for a
# new smoke (see environment_identity).
PADDLE_CONSTRAINTS = "runtime/paddle-constraints.txt"
PADDLE_BUILD_CONSTRAINTS = "runtime/paddle-constraints-{index}-{machine}.txt"


def tool_hashes(root: Path, engine: str | Iterable[str]) -> dict[str, str]:
    """The skill's own files used by setup and the smoke; recorded, not part of
    the fingerprint (see environment_identity)."""
    engines = engine_set(engine)
    names = ["scripts/_setup_ocr.py", "scripts/run_ocr_phase.py", "scripts/_ocr_engines.py", *ENVIRONMENT_FILES]
    if "vision" in engines:
        names += ["scripts/run_apple_vision_ocr.py", "scripts/apple_vision_ocr.swift", "scripts/_draft_contract.py",
                  "scripts/_vision_layout.py", "scripts/_mlx_vlm_server.py", "scripts/_phase_budget.py"]
    if "hunyuan" in engines:
        names += ["scripts/run_hunyuanocr.py", "scripts/_hunyuan_shared.py", "scripts/_gpu_queue.py", "scripts/_phase_budget.py"]
    if "paddle" in engines:
        names += ["scripts/run_paddleocr_vl.py", "scripts/paddleocr_vl_worker.py", "scripts/_paddle_pool.py",
                  "scripts/_mlx_vlm_server.py",
                  "runtime/mlx-requirements.txt", "scripts/_gpu_queue.py", "scripts/_phase_budget.py",
                  PADDLE_CONSTRAINTS, *sorted(path.relative_to(root).as_posix()
                                              for path in root.glob(PADDLE_BUILD_CONSTRAINTS.format(index="*", machine="*")))]
    return {name: sha256_file(root / name) for name in dict.fromkeys(names) if (root / name).is_file()}


def validate_hunyuan_config(config: dict[str, str]) -> None:
    try:
        slots = int(config.get("OCR_HUNYUAN_SLOTS", "1"))
    except ValueError as exc:
        raise SetupError("OCR_HUNYUAN_SLOTS must be an integer from 1 to 32.") from exc
    if not 1 <= slots <= 32 or config.get("OCR_HUNYUAN_MODE", "shared") not in {"shared", "dedicated"}:
        raise SetupError("Saved Hunyuan settings are invalid: slots must be 1..32 and mode shared or dedicated; correct env.sh and retry.")


def hunyuan_need_mib(slots: int, context: int = HUNYUAN_CONTEXT_PER_SLOT) -> int:
    """GPU memory a Hunyuan server with SLOTS slots of CONTEXT tokens needs."""
    return HUNYUAN_BASE_MIB + math.ceil(slots * context * HUNYUAN_KV_KIB_PER_TOKEN / 1024)


# The slots a first setup gives HunyuanOCR (hunyuan_slots_for).  One slot reads one page at a
# time; measured on a 32 GB card, 16 slots keep the card fully used (a book of 639 pages in 17
# minutes, 15.7 of 16 slots busy), and 32 were no faster.
HUNYUAN_AUTO_SLOTS_MAX = 16
HUNYUAN_CARD_RESERVE_MIB = 2048


def hunyuan_slots_for(gpu: GPU | None, device: str) -> int:
    """The slots a setup that finds none saved gives HunyuanOCR: as many as the card holds at
    the runner's context for each (hunyuan_need_mib) beside HUNYUAN_CARD_RESERVE_MIB, at most
    HUNYUAN_AUTO_SLOTS_MAX; one on the CPU, on Metal, or where the card's size is not known.
    By the card's whole memory, not what is free now: a local model that holds it makes room
    for the OCR phase."""
    if device != "cuda" or gpu is None or not gpu.total_mib:
        return 1
    one = hunyuan_need_mib(1)
    each = hunyuan_need_mib(2) - one
    room = gpu.total_mib - HUNYUAN_CARD_RESERVE_MIB - (one - each)
    return max(1, min(HUNYUAN_AUTO_SLOTS_MAX, room // each))


def engine_devices(engines: Sequence[str], devices: str | Mapping[str, str]) -> dict[str, str]:
    if isinstance(devices, str):
        return {engine: devices for engine in engines}
    return {engine: devices[engine] for engine in engines}


def environment_identity(options: SetupOptions, config: dict[str, str], engine: str | Iterable[str],
                         device: str | Mapping[str, str], lock: dict[str, Any]) -> dict[str, Any]:
    """The verified environment, and what the setup fingerprint covers.

    The fingerprint (setup_fingerprint) covers the environment only: the
    engines, each one's effective device, Python major.minor, the requirements
    and lock content, the model file hashes, the runtime binary and library
    hashes, Hunyuan's configured slots (the smoke proves they fit), and
    Paddle's package kind and CUDA build.  The skill's own script bytes, the
    full Python version, package versions, venv and model paths and the other
    saved settings are kept under "recorded", so a code edit or a Python patch
    release does not ask for a new smoke.
    """
    engines = engine_set(engine)
    devices = engine_devices(engines, device)
    python = python_identity(Path(config["JYUT_OCR_PYTHON"]))
    root = options.repo_root
    identity: dict[str, Any] = {"engines": list(engines), "devices": devices,
        "python": ".".join(str(part) for part in python["version_info"][:2]),
        "files": {name: sha256_file(root / name) for name in ENVIRONMENT_FILES if (root / name).is_file()},
        "recorded": {
            "config": {key: config.get(key) for key in sorted(CONFIG_KEYS)},
            "tools": tool_hashes(root, engines), "python": python}}
    if "hunyuan" in engines:
        validate_hunyuan_config(config)
        models = {}
        for asset in lock["model"]["files"]:
            key = "HUNYUAN_MODEL" if asset["role"] == "model" else "HUNYUAN_MMPROJ"
            if not config.get(key) or not verify_asset(Path(config[key]), asset):
                raise SetupError(f"Configured {key} does not match the locked model SHA-256.")
            models[asset["role"]] = asset["sha256"]
        runtime = runtime_identity(Path(config["HUNYUAN_LLAMA_SERVER"]), lock, devices["hunyuan"])
        identity["models"] = models
        identity["runtime"] = {key: runtime.get(key) for key in ("sha256", "libraries", "commit")}
        identity["deployment"] = {"hunyuanSlots": int(config.get("OCR_HUNYUAN_SLOTS", "1")),
                                  "hunyuanContextPerSlot": HUNYUAN_CONTEXT_PER_SLOT}
        identity["recorded"]["runtime"] = runtime
    if "paddle" in engines:
        if not config.get("PADDLEOCR_PYTHON"):
            raise SetupError("PADDLEOCR_PYTHON is not configured.")
        probe = paddle_probe(Path(config["PADDLEOCR_PYTHON"]))
        dists = probe["dists"]
        package = next((name for name in ("paddlepaddle-gpu", "paddlepaddle") if name in dists), None)
        if package is None:
            raise SetupError(f"PADDLEOCR_PYTHON {config['PADDLEOCR_PYTHON']} has no PaddlePaddle.")
        for name, wanted in [(package, lock["paddle"]["paddle_version"]),
                             *((item["distribution"], item["version"]) for item in lock["paddle"]["packages"])]:
            if (dists.get(name) or {}).get("version") != wanted:
                raise SetupError(f"PADDLEOCR_PYTHON lacks {name}=={wanted}; rerun setup.")
        weights = {}
        for model in lock["paddle"]["models"]:
            key = PADDLE_MODEL_KEYS[model["role"]]
            if not config.get(key) or not paddle_model_verified(Path(config[key]), model):
                raise SetupError(f"Configured {key} does not hold the locked {model['name']} files.")
            weights[model["role"]] = f"{model['repo']}@{model['revision']}"
        identity["paddle"] = {"package": package, "cuda": dists[package].get("cuda"),
                              "python": ".".join(str(part) for part in probe["python"][:2]),
                              "models": weights}
        identity["recorded"]["paddle"] = probe
        backend = config.get("OCR_PADDLE_VL_BACKEND", "native")
        if backend not in {"native", "mlx-vlm-server"}:
            raise SetupError(f"Invalid effective OCR_PADDLE_VL_BACKEND={backend!r}; rerun setup.")
        if backend == "mlx-vlm-server":
            if not config.get("MLX_VLM_PYTHON"):
                raise SetupError("MLX_VLM_PYTHON is not configured; rerun setup.")
            mlx = mlx_probe(Path(config["MLX_VLM_PYTHON"]), lock)
            problem = mlx_mismatch(mlx, lock)
            if problem is not None:
                raise SetupError(f"MLX_VLM_PYTHON {problem}; rerun setup.")
            if mlx.get("prefix") in {probe.get("prefix"), python.get("prefix")}:
                raise SetupError("MLX_VLM_PYTHON must be separate from the core and Paddle Python environments.")
            identity["paddle"]["vlBackend"] = backend
            identity["paddle"]["mlx"] = {"python": "3.12", "packages": mlx["dists"],
                                          "requirementsSha256": lock["mlx"]["requirements_sha256"]}
            identity["recorded"]["mlx"] = mlx
    return identity


def setup_fingerprint(identity: dict[str, Any]) -> str:
    """Hash of IDENTITY without its "recorded" part."""
    return canonical_hash({key: value for key, value in identity.items() if key != "recorded"})


def check_smoke(record: dict[str, Any]) -> bool:
    try:
        for artifact in record["artifacts"]:
            path = Path(artifact["path"])
            if not path.is_file() or sha256_file(path) != artifact["sha256"]:
                return False
        payload = json.loads(Path(record["json_output"]).read_text())
        text = Path(record["text_output"]).read_text()
        provenance = payload.get("provenance", {})
        return bool(text.strip() and payload.get("status") == "complete" and provenance.get("textSha256") == sha256_file(Path(record["text_output"])) and provenance.get("renderSha256") == sha256_file(Path(record["render"])))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def free_gpu_mib() -> int:
    """Free GPU memory, or estimated reclaimable unified memory on macOS."""
    if platform_name() == "Darwin":
        from run_ocr_phase import mac_free_mib
        free = mac_free_mib()
        if free is None:
            raise SetupError("Could not estimate reclaimable unified memory from vm_stat; inspect memory use and rerun setup.")
        return free
    free_text = run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture=True).stdout
    try:
        return min(int(line.strip()) for line in free_text.splitlines() if line.strip())
    except (ValueError, TypeError):
        raise SetupError("Could not read free GPU memory from nvidia-smi; inspect the GPU and rerun setup.")


def gpu_device(engine: str, device: str, config: Mapping[str, str] | None = None) -> bool:
    """Whether the setup test shares GPU/unified memory with a local model."""
    if engine == "hunyuan":
        return device in {"cuda", "metal"} or platform_name() == "Darwin"
    if engine == "paddle":
        return (device.startswith("gpu") or platform_name() == "Darwin"
                or (config or {}).get("OCR_PADDLE_VL_BACKEND") == "mlx-vlm-server")
    return False


def smoke_need_mib(engine: str, config: Mapping[str, str], paddle_need: int = PADDLE_NEED_MIB) -> int:
    if config.get("OCR_PHASE_REQUIRED_MIB"):
        try:
            need = int(config["OCR_PHASE_REQUIRED_MIB"])
        except ValueError as exc:
            raise SetupError("OCR_PHASE_REQUIRED_MIB must be a nonnegative integer.") from exc
        if need < 0:
            raise SetupError("OCR_PHASE_REQUIRED_MIB must be a nonnegative integer.")
        if need:
            return need  # zero retains run_ocr_phase.py's automatic estimate
    if engine == "hunyuan":
        return hunyuan_need_mib(int(config.get("OCR_HUNYUAN_SLOTS", "1")))
    return paddle_need if engine == "paddle" else 0


def smoke_directory(options: SetupOptions, fingerprint: str, engine: str) -> Path:
    # One folder per fingerprint and engine: a rerun resumes it (the runners
    # skip a sealed page) instead of starting another.
    return options.data_dir / "setup-smoke" / fingerprint[:16] / engine


def prepare_smoke(options: SetupOptions, config: Mapping[str, str], engine: str, device: str,
                  fingerprint: str) -> tuple[Path, list[str | Path]]:
    directory = smoke_directory(options, fingerprint, engine)
    renders, output = directory / "renders", directory / "ocr"
    renders.mkdir(parents=True, exist_ok=True)
    render = renders / "page-0001.png"
    if not render.is_file():
        # Pure synthetic text: this is an environment probe, never an accuracy benchmark.
        generator = "from PIL import Image,ImageDraw,ImageFont; import sys; im=Image.new('RGB',(1200,400),'white'); d=ImageDraw.Draw(im); f=ImageFont.load_default(size=60); d.text((70,80),'OCR SETUP TEST 12345',fill='black',font=f); d.text((70,190),'One page. Ready.',fill='black',font=f); im.save(sys.argv[1])"
        run([config["JYUT_OCR_PYTHON"], "-c", generator, render])
    runner = options.repo_root / "scripts" / {"hunyuan": "run_hunyuanocr.py", "paddle": "run_paddleocr_vl.py",
                                              "vision": "run_apple_vision_ocr.py"}[engine]
    command: list[str | Path] = [config["JYUT_OCR_PYTHON"], runner, renders, output, "--pages", "1",
                                 "--report", directory / "run-report.json"]
    if engine == "hunyuan":
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # The configured slots, at the runners' default context per slot: the
        # memory a book run will use.  A bounded queue wait, never a silent block.
        command += ["--workers", "1", "--model", config["HUNYUAN_MODEL"], "--mmproj", config["HUNYUAN_MMPROJ"],
                    "--llama-server", config["HUNYUAN_LLAMA_SERVER"], "--device", device,
                    "--server-mode", "dedicated", "--server-slots", str(int(config.get("OCR_HUNYUAN_SLOTS", "1"))),
                    "--port", str(port), "--max-tokens", "256", "--timeout", "1200",
                    "--gpu-queue-timeout", str(SMOKE_QUEUE_TIMEOUT)]
    elif engine == "paddle":
        backend = config.get("OCR_PADDLE_VL_BACKEND", "native")
        command += ["--python", config["PADDLEOCR_PYTHON"], "--vl-backend", backend,
                    "--gpu-queue-timeout", str(SMOKE_QUEUE_TIMEOUT)]
        if backend == "mlx-vlm-server":
            command += ["--mlx-python", config["MLX_VLM_PYTHON"]]
            locked = load_lock(options.repo_root)
            vl = next(model for model in locked["paddle"]["models"] if model["role"] == "vl")
            command += ["--model-revision", vl["revision"]]
    else:
        command += ["--workers", "1"]
    return directory, command


def smoke_record(directory: Path, command: Sequence[str | Path]) -> dict[str, Any]:
    render = directory / "renders/page-0001.png"
    text_path, json_path = directory / "ocr/page-0001.txt", directory / "ocr/page-0001.json"
    files = [render, text_path, json_path, directory / "run-report.json"]
    if not all(path.is_file() for path in files):
        raise SetupError("OCR smoke did not produce its complete text/JSON/report evidence; setup remains incomplete.")
    record = {"completed_at": now(), "command": [str(value) for value in command], "render": str(render),
              "json_output": str(json_path), "text_output": str(text_path), "artifacts": [{"path": str(path), "sha256": sha256_file(path)} for path in files]}
    if not check_smoke(record):
        raise SetupError("OCR smoke returned empty, incomplete or unsealed output; setup remains incomplete.")
    return record


def smoke_environment(config: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(config)
    # A direct smoke never stops a model service, and tests the local runtime.
    for key in list(environment):
        if key.startswith("OCR_PHASE_") or key == "HUNYUAN_ENDPOINT":
            environment.pop(key, None)
    return environment


def gpu_busy_error(engine: str, free: int, need: int) -> SetupError:
    memory = "MiB estimated reclaimable unified memory" if platform_name() == "Darwin" else "MiB free"
    return SetupError(f"GPU is busy ({free} {memory}; the {engine} setup test needs about {need} MiB). The test was not "
                      "started and no service was stopped; the GPU setting is kept. Let the program using the GPU "
                      "finish and rerun setup, or, if it is your local proofreading model, declare it with "
                      "--local-model-endpoint, --local-model-name, --local-model-release-cmd and "
                      "--local-model-restore-cmd so setup tests the GPU through a hand-over.")


def smoke_test(options: SetupOptions, config: dict[str, str], engine: str, device: str,
               fingerprint: str, *, need_mib: int | None = None) -> dict[str, Any]:
    """One engine's setup test, run directly on one synthetic page."""
    if gpu_device(engine, device, config):
        need = smoke_need_mib(engine, config) if need_mib is None else need_mib
        free = free_gpu_mib()
        if free < need:
            raise gpu_busy_error(engine, free, need)
    directory, command = prepare_smoke(options, config, engine, device, fingerprint)
    label = {"hunyuan": "HunyuanOCR", "paddle": "PaddleOCR-VL", "vision": "Apple Vision"}[engine]
    detail = ("system-managed compute; no model hand-over" if engine == "vision" else
              f"a GPU queue wait is capped at {SMOKE_QUEUE_TIMEOUT} s")
    print(f"Running the {label} setup test on {device} (one synthetic page; {detail}).")
    try:
        run(command, env=smoke_environment(config), timeout=SMOKE_TIMEOUT, own_group=True)
    except SetupError as exc:
        if exc.returncode == 75 and engine != "vision":
            raise SetupError(f"The {label} setup test waited {SMOKE_QUEUE_TIMEOUT} s for the GPU queue: another OCR job "
                             "holds it. Nothing was stopped; rerun setup when that job finishes.") from exc
        raise
    return smoke_record(directory, command)


def handover_failure(code: int, config: Mapping[str, str]) -> SetupError:
    meaning = {
        75: "did not finish within the hand-over's time budget (exit 75); the local model is back. Rerun the same "
            "setup command to continue; finished parts are kept",
        76: "did not get through (exit 76): the GPU is busy with another job, or the test sealed nothing in its "
            "window; the RESULT line above says whether the local model was stopped, and it is up again either way. "
            "Wait for that job to finish and rerun setup",
        3: "ran, but the local model was NOT restored (exit 3). Restore it now: " + str(config.get("OCR_PHASE_RESTORE_CMD")),
        1: "failed (exit 1): if its RESULT line says GPU FULL, too little GPU memory is free and the lines above "
           "name each process holding it with how to free it (nothing was stopped but the local model, which is "
           "back if it was up); free it and rerun setup. Otherwise see the output above",
        2: "was refused by run_ocr_phase.py (exit 2, a configuration error: see its message above; a hand-over needs "
           "OCR_PHASE_MAX_SECONDS in env.sh)",
    }.get(code, f"failed (exit {code}); see the output above")
    error = SetupError(f"The GPU setup test through the hand-over {meaning}.")
    error.returncode = code
    return error


def run_status(command: Sequence[str | Path], *, env: dict[str, str], timeout: float | None) -> int:
    """Run COMMAND in the foreground and return its exit code.  On a timeout it
    gets SIGTERM first, so a hand-over restores the model before it ends."""
    argv = [str(item) for item in command]
    process = subprocess.Popen(argv, env=env)
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as stop:
        process.terminate()
        try:
            process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        if isinstance(stop, KeyboardInterrupt):
            raise
        raise SetupError(f"The setup test did not finish in {timeout:.0f} s: {shlex.join(argv)}") from stop


def handover_smokes(options: SetupOptions, config: dict[str, str], engines: Sequence[str],
                    devices: Mapping[str, str], fingerprint: str, need: int) -> dict[str, dict[str, Any]]:
    """All engines' setup tests in one GPU hand-over (scripts/run_ocr_phase.py),
    for a GPU that the configured local model holds."""
    commands = {}
    for engine in engines:
        directory, command = prepare_smoke(options, config, engine, devices[engine], fingerprint)
        commands[engine] = (directory, command)
    plan = options.data_dir / "setup-smoke" / fingerprint[:16] / "plan.json"
    atomic_json(plan, {"commands": [[str(item) for item in command] for _, command in commands.values()]})
    python = config["JYUT_OCR_PYTHON"]
    wrapper = [python, options.repo_root / "scripts/run_ocr_phase.py", "--",
               python, options.repo_root / "scripts/_setup_ocr.py", "--run-smoke-plan", plan]
    environment = dict(os.environ)
    environment.update(config)
    environment.pop("HUNYUAN_ENDPOINT", None)
    if need and not config.get("OCR_PHASE_REQUIRED_MIB"):
        environment["OCR_PHASE_REQUIRED_MIB"] = str(need)
    try:
        budget = float(config.get("OCR_PHASE_MAX_SECONDS") or 0)
    except ValueError:
        budget = 0.0
    left = budget
    if budget > 0:
        # The budget is the agent's whole shell call, and this setup call has
        # already spent part of it (hashing, uv, nvidia-smi): the wrapper gets the rest.
        spent = max(0.0, time.time() - options.started_at) if options.started_at else 0.0
        left = float(math.floor(budget - spent))
        floor = min(HANDOVER_SMOKE_FLOOR, budget / 2)
        if left < floor:
            raise SetupError(
                f"The GPU setup test through the hand-over was not started: this setup call has already run "
                f"{spent:.0f} s of OCR_PHASE_MAX_SECONDS ({budget:.0f} s, kept below the agent's shell time "
                f"limit), leaving {max(left, 0):.0f} s, less than the {floor:.0f} s a test window needs. Nothing "
                "was stopped. Run the same setup command again in the foreground: what is downloaded and verified "
                "is kept. If this repeats, the checks before the test outlast that limit: run setup from a shell "
                "without it, or raise the limit and OCR_PHASE_MAX_SECONDS together.")
        wrapper[2:2] = ["--max-seconds", f"{left:.0f}"]
    print(f"The GPU is short of free memory and a local model is configured: running the setup tests through "
          f"one hand-over (run_ocr_phase.py stops the model if it is up, runs {', '.join(engines)}, and starts "
          f"it again only if it stopped it; nothing else on the GPU is touched; budget "
          f"{left:.0f} s{f' of {budget:.0f} s' if left != budget else ''}). Run this in the foreground.")
    code = run_status(wrapper, env=environment, timeout=(left + 300) if budget > 0 else None)
    if code:
        raise handover_failure(code, config)
    return {engine: smoke_record(directory, command) for engine, (directory, command) in commands.items()}


def run_smokes(options: SetupOptions, config: dict[str, str], engines: Sequence[str],
               devices: Mapping[str, str], fingerprint: str,
               paddle_need: int = PADDLE_NEED_MIB) -> dict[str, dict[str, Any]]:
    """Every engine's setup test.  Directly when the GPU has room; through one
    hand-over when a configured local model holds it (OCR_PHASE_RELEASE_CMD and
    OCR_PHASE_RESTORE_CMD, OCR_PHASE_SWAP auto or always); never by moving an
    engine to the CPU."""
    if tuple(engines) == ("vision",):
        return {"vision": smoke_test(options, config, "vision", devices["vision"], fingerprint)}
    gpu_engines = [engine for engine in engines if gpu_device(engine, devices[engine], config)]
    swap = (config.get("OCR_PHASE_SWAP") or "auto").strip().lower()
    tenant = bool(config.get("OCR_PHASE_RELEASE_CMD") and config.get("OCR_PHASE_RESTORE_CMD"))
    needs = {engine: smoke_need_mib(engine, config, paddle_need) for engine in gpu_engines}
    if tenant and swap != "never" and (gpu_engines or swap == "always"):
        try:
            room = swap != "always" and free_gpu_mib() >= max(needs.values(), default=0)
        except SetupError:
            room = False  # unknown memory: let the hand-over make the conservative decision
        if not room:
            return handover_smokes(options, config, engines, devices, fingerprint, max(needs.values(), default=0))
    if swap == "always" and not tenant:
        raise SetupError("OCR_PHASE_SWAP=always needs OCR_PHASE_RELEASE_CMD and OCR_PHASE_RESTORE_CMD in env.sh.")
    return {engine: smoke_test(options, config, engine, devices[engine], fingerprint,
                               need_mib=needs.get(engine)) for engine in engines}


def run_smoke_plan(path: Path) -> int:
    """Run the plan's runner commands in order, as one hand-over's runner; the
    first non-zero exit code ends it (75 lets the hand-over call again)."""
    plan = json.loads(Path(path).read_text())
    for command in plan["commands"]:
        child = subprocess.Popen(command)

        def forward(signum: int, _frame: Any, child: subprocess.Popen = child) -> None:
            child.send_signal(signum)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, forward)
        code = child.wait()
        if code:
            return code
    return 0


def load_lock(root: Path) -> dict[str, Any]:
    try:
        lock = json.loads((root / "runtime/ocr-lock.json").read_text())
        assert lock["schema_version"] == LOCK_SCHEMA and len(lock["model"]["files"]) == 2
        assert re.fullmatch(r"[0-9a-f]{40}", lock["runtime"]["commit"])
        for asset in lock["model"]["files"]:
            assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) and asset["size"] > 0
        for asset in lock["runtime"]["prebuilt"]:
            for item in [asset, *([asset["runtime_bundle"]] if asset.get("runtime_bundle") else [])]:
                assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and item["size"] > 0
        paddle = lock["paddle"]
        for item in [*paddle["packages"], *paddle["cpu_wheels"]]:
            assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and item["size"] > 0
        assert paddle["gpu_wheels"]["trust"] == "trust-on-first-use"
        for item in paddle["gpu_wheels"]["wheels"]:
            assert item["sha256"] is None and item["size"] > 0
        assert {model["role"] for model in paddle["models"]} == set(PADDLE_MODEL_KEYS)
        for model in paddle["models"]:
            assert re.fullmatch(r"[0-9a-f]{40}", model["revision"])
            for item in model["files"]:
                assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) and item["size"] > 0
        if "mlx" in lock:
            mlx = lock["mlx"]
            assert isinstance(mlx["enabled_by_default"], bool) and mlx["python"] == PYTHON_VERSION
            requirements = root / mlx["requirements"]
            assert requirements.resolve().is_relative_to(root.resolve())
            assert re.fullmatch(r"[0-9a-f]{64}", mlx["requirements_sha256"])
            assert sha256_file(requirements) == mlx["requirements_sha256"]
            packages = {package["name"]: package["version"] for package in mlx["packages"]}
            assert len(packages) == len(mlx["packages"]) and packages.get("mlx-vlm") == "0.8.0"
            assert all(isinstance(name, str) and name and isinstance(version, str) and version
                       for name, version in packages.items())
        return lock
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        raise SetupError("Invalid or missing runtime/ocr-lock.json.") from exc


# --- the local proofreading model -------------------------------------------------------

def models_url(endpoint: str) -> str:
    """{endpoint base}/v1/models for an OpenAI-compatible chat endpoint."""
    parts = urllib.parse.urlsplit(endpoint.strip())
    path = parts.path.rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + "/v1/models", "", ""))


def local_model_updates(local: LocalModel, existing: Mapping[str, str]) -> dict[str, str]:
    """env.sh keys for a local proofreading model: PROOFREAD_ENDPOINT and
    PROOFREAD_MODEL (proofread_pages.py's local-agent rule reads them),
    PROOFREAD_MAX_INFLIGHT when its concurrency is given (the default of
    book_profile.py --workers over HTTP and proofread_pages.py --max-inflight),
    and the GPU hand-over's OCR_PHASE_* keys when release/restore commands are
    given."""
    for label, url in (("--local-model-endpoint", local.endpoint), ("--local-model-health-url", local.health_url)):
        if url is None:
            continue
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise SetupError(f"{label} must be an http(s) URL with a host, not {url!r}.")
    if not local.name.strip():
        raise SetupError("--local-model-name must be the model ID the agent and the server use.")
    if bool(local.release_cmd) != bool(local.restore_cmd):
        raise SetupError("--local-model-release-cmd and --local-model-restore-cmd go together: the hand-over must be able to bring the model back.")
    if local.concurrency is not None and (isinstance(local.concurrency, bool) or not isinstance(local.concurrency, int)
                                          or local.concurrency < 1):
        raise SetupError("--local-model-concurrency must be a whole number of 1 or more: the requests the server runs at once.")
    updates = {"PROOFREAD_ENDPOINT": local.endpoint, "PROOFREAD_MODEL": local.name.strip()}
    if local.concurrency is not None:
        updates["PROOFREAD_MAX_INFLIGHT"] = str(local.concurrency)
    if local.release_cmd and local.restore_cmd:
        updates.update({"OCR_PHASE_RELEASE_CMD": local.release_cmd, "OCR_PHASE_RESTORE_CMD": local.restore_cmd,
                        "OCR_PHASE_HEALTH_URL": local.health_url or existing.get("OCR_PHASE_HEALTH_URL") or models_url(local.endpoint)})
        if not existing.get("OCR_PHASE_MAX_SECONDS"):
            # Below Qwen Code's 600 s shell maximum; see env.sh.example.
            updates["OCR_PHASE_MAX_SECONDS"] = "540"
    elif local.health_url:
        updates["OCR_PHASE_HEALTH_URL"] = local.health_url
    return updates


# --- inspect and setup ------------------------------------------------------------------

def plan_engines(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any], *,
                 system: str, machine: str) -> tuple[tuple[str, ...], dict[str, str], PaddlePlan | None, list[GPU], list[str], str, str]:
    """Engines, devices, Paddle plan, GPUs, notes and the engine and device requests."""
    engine_wanted = engine_request(options.engine, existing)
    device_wanted = device_request(options.device, existing)
    engines, notes = choose_engines(engine_wanted, system=system, machine=machine)
    devices: dict[str, str] = {}
    if "vision" in engines:
        # A saved HF device does not configure Vision's system-managed compute.
        vision_device = "auto" if options.device in (None, "default") else device_wanted
        devices["vision"] = choose_device(vision_device, {}, engine="vision", system=system)
    if "hunyuan" in engines:
        devices["hunyuan"] = choose_device(device_wanted, {}, engine="hunyuan", system=system)
    # nvidia-smi is asked only when Paddle's device depends on it.
    gpus = nvidia_gpus() if system == "Linux" and device_wanted in {"auto", "cuda"} and "paddle" in engines else []
    plan = None
    if "paddle" in engines:
        backend_request = paddle_backend_request(options.paddle_vl_backend, existing)
        backend = choose_paddle_backend(backend_request, device_wanted, lock, system=system, machine=machine)
        plan, paddle_notes = paddle_plan(lock, system=system, machine=machine, request=device_wanted, gpus=gpus)
        notes += paddle_notes
        if plan is None:
            raise SetupError("The hunyuan-paddle route requires both engines on this platform; Linux/WSL2 can explicitly select --engine hunyuan.")
        else:
            plan = replace(plan, backend=backend)
            devices["paddle"] = plan.device
            if backend == "mlx-vlm-server":
                notes.append("PaddleOCR-VL uses the isolated MLX server on Metal; document layout runs in Paddle on CPU.")
            elif (backend_request == "auto" and device_wanted != "cpu" and system == "Darwin" and machine == "arm64"
                  and version_tuple(platform.mac_ver()[0]) < version_tuple(str(lock.get("mlx", {}).get("minimum_macos", "14.0")))):
                notes.append("The locked MLX environment needs macOS 14 or newer; PaddleOCR-VL uses native inference here.")
    legacy_device = existing.get("OCR_HUNYUAN_DEVICE")
    if (options.device in (None, "default") and not existing.get("JYUT_OCR_DEVICE") and legacy_device
            and legacy_device != devices.get("hunyuan", legacy_device)):
        notes.append(f"The saved OCR_HUNYUAN_DEVICE={legacy_device} came from an older installer and is detected again "
                     f"({devices['hunyuan']} now); pass --device {legacy_device} to keep it.")
    return engines, devices, plan, gpus, notes, engine_wanted, device_wanted


# --- the engine choice on macOS ---------------------------------------------------------
#
# A Mac has an OCR engine built in (Apple Vision), so the engines downloaded
# from Hugging Face - several GB of weights and a Paddle environment - are a
# choice there, not a default: a first setup on macOS asks the person which
# they want before anything is downloaded.  Linux and WSL2 never ask (no
# built-in engine: both engines are the default).

ENGINE_LABELS = {"hunyuan": "HunyuanOCR", "paddle": "PaddleOCR-VL", "vision": "Apple Vision"}
# The two answers, as --engine takes them: the downloaded engines, or Apple
# Vision alone.  (--engine hunyuan stays an explicit override, not an answer.)
ENGINE_CHOICES = ("hunyuan-paddle", "apple-vision")
ENGINE_CHOICE_TRIES = 3


def engine_choice_pending(options: SetupOptions, existing: Mapping[str, str], system: str) -> bool:
    """Whether the person still has to choose this Mac's OCR engines: macOS,
    no --engine on this call, and no choice an earlier setup saved
    (engine_request's rule: JYUT_OCR_ENGINE counts only beside
    JYUT_OCR_ENGINES, which setup writes once the chosen engines are in
    place)."""
    if system != "Darwin" or options.engine not in (None, "default"):
        return False
    if existing.get("JYUT_OCR_ENGINE") or existing.get("JYUT_OCR_ENGINES"):
        engine_request(options.engine, existing)
        return False
    return True


def engine_choice_options(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any], *,
                          system: str, machine: str) -> list[tuple[str, str]]:
    """The answers a Mac picks from, each (its --engine value, what it sets
    up here and the disk that still takes).  The disk is disk_needs() for
    that answer: what is here already (weights, a runtime) is not counted.
    Read-only, as disk_needs is."""
    described = []
    for request in ENGINE_CHOICES:
        asked = replace(options, engine=request)
        try:
            engines, devices, plan, _, _, _, _ = plan_engines(asked, existing, lock, system=system, machine=machine)
            release = (release_binary(lock, system=system, machine=machine, device=devices["hunyuan"], gpu=None)
                       if "hunyuan" in engines else None)
            needs = disk_needs(asked, existing, lock, engines, release, plan, device=devices.get("hunyuan"), gpu=None)
            disk = f"about {sum(size for _, size in needs) / 1e9:.1f} GB more disk"
        except SetupError:
            # The size is a courtesy: a --device this answer cannot take is
            # reported when the answer is given, not here.
            engines, disk = choose_engines(request, system=system, machine=machine)[0], "disk not estimated"
        names = " and ".join(ENGINE_LABELS[engine] for engine in engines)
        if request == "apple-vision":
            text = (f"{names} only, built into macOS: no model download, {disk} for the Python environment; "
                    "one draft a page, the single-engine workflow")
        else:
            text = (f"{names}, downloaded from Hugging Face: {disk}; "
                    + ("two drafts a page, the full workflow" if len(engines) > 1 else
                       "one draft a page, the single-engine workflow"))
        described.append((request, text))
    return described


def engine_choice_message(choices: Sequence[tuple[str, str]]) -> str:
    """What a setup that cannot ask says (no terminal: an agent's shell, a
    pipe), and what --check says, while the choice is pending."""
    return ("The OCR engines of this Mac are not chosen yet, and nothing has been downloaded. Rerun the same "
            "command with "
            + " or ".join(f"--engine {request} ({text})" for request, text in choices)
            + ". An agent running setup asks the person which one and never chooses for them.")


def ask_engine_choice(options: SetupOptions, existing: Mapping[str, str], lock: Mapping[str, Any], *,
                      system: str, machine: str) -> SetupOptions:
    """OPTIONS with the engines chosen: unchanged unless the choice is pending
    (engine_choice_pending).  At a terminal the person is asked, 1 or 2; a
    setup with no terminal stops with engine_choice_message.  Either way
    before any download or dependency install, and before env.sh says which
    engines there are."""
    if not engine_choice_pending(options, existing, system):
        return options
    choices = engine_choice_options(options, existing, lock, system=system, machine=machine)
    if not options.interactive:
        raise SetupError(engine_choice_message(choices))
    print("Which OCR engines should this Mac use?", file=sys.stderr)
    for number, (request, text) in enumerate(choices, 1):
        print(f"  {number}) {text}  [--engine {request}]", file=sys.stderr)
    for _ in range(ENGINE_CHOICE_TRIES):
        print(f"Enter 1 or {len(choices)}: ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if not answer:
            break  # end of input: nobody is there to answer
        answer = answer.strip().lower()
        for number, (request, _) in enumerate(choices, 1):
            if answer in (str(number), request):
                # Saved in env.sh only once these engines are in place: a
                # setup that stops before then asks again unless told.
                print(f"Chosen: --engine {request} (pass it to setup to skip this question).", file=sys.stderr)
                return replace(options, engine=request)
    raise SetupError(engine_choice_message(choices))


def summary(engines: Sequence[str], devices: Mapping[str, str], config_path: Path, **extra: Any) -> dict[str, Any]:
    return {"status": "ready", "engines": list(engines), "devices": dict(devices), "engine": ",".join(engines),
            "device": devices.get("hunyuan") or devices.get("vision") or next(iter(devices.values()), None),
            "config": str(config_path), **extra}


def saved_plan(options: SetupOptions, config: Mapping[str, str]) -> tuple[tuple[str, ...], dict[str, str]] | None:
    """The engines and effective devices setup saved, which --check verifies,
    unless this call asks for other ones (an explicit --engine or --device)."""
    if (options.engine not in (None, "default") or options.device not in (None, "default")
            or options.paddle_vl_backend not in (None, "default")):
        return None
    wanted = engine_request(options.engine, config)
    engines, _ = choose_engines(wanted, system=platform_name(), machine=machine_name())
    if not config.get("JYUT_OCR_ENGINES"):
        return None
    if engines != engine_set(config.get("JYUT_OCR_ENGINES", "")):
        raise SetupError("Saved route and effective engines disagree; rerun setup with --engine.")
    if not engines:
        return None
    keys = {"hunyuan": "OCR_HUNYUAN_DEVICE", "paddle": "OCR_PADDLE_DEVICE"}
    devices = {engine: config.get(keys[engine], "") if engine in keys else "vision-managed" for engine in engines}
    if not all(devices.values()):
        return None
    return engines, devices


def setup_test_command(options: SetupOptions) -> str:
    """The command that runs the owed setup test for these folders."""
    config, data = default_directories()
    words = ["./install.sh", "--env-only"]
    if options.config_dir.absolute() != config.absolute():
        words += ["--config-dir", str(options.config_dir)]
    if options.data_dir.absolute() != data.absolute():
        words += ["--data-dir", str(options.data_dir)]
    if options.paddle_vl_backend not in (None, "default"):
        words += ["--paddle-vl-backend", options.paddle_vl_backend]
    return shlex.join(words)


def inspect_setup(options: SetupOptions) -> dict[str, Any]:
    """Read-only: no directories, downloads, config writes or model starts."""
    try:
        system = platform_name()
        config = read_environment(options.config_dir / "env.sh")
        lock = load_lock(options.repo_root)
        saved = saved_plan(options, config)
        if saved is not None:
            (engines, devices), notes = saved, []
        else:
            machine = machine_name(system)
            if engine_choice_pending(options, config, system):
                # Not set up, and on macOS the engines are the person's choice.
                raise SetupError(engine_choice_message(
                    engine_choice_options(options, config, lock, system=system, machine=machine)))
            engines, devices, _, _, notes, _, _ = plan_engines(options, config, lock, system=system, machine=machine)
        if "paddle" in engines:
            wanted = choose_paddle_backend(paddle_backend_request(options.paddle_vl_backend, config),
                                           device_request(options.device, config), lock,
                                           system=system, machine=machine_name(system))
            if wanted != config.get("OCR_PADDLE_VL_BACKEND", "native"):
                raise SetupError(f"Saved Paddle VL backend is stale; rerun setup to prepare {wanted}.")
        missing = missing_dependencies(engines)
        if missing:
            raise SetupError("Missing dependencies: " + ", ".join(missing))
        state = json.loads((options.config_dir / "setup-state.json").read_text())
        identity = environment_identity(options, config, engines, devices, lock)
        current = state.get("schema_version") == STATE_SCHEMA and state.get("fingerprint") == setup_fingerprint(identity)
        if current and state.get("status") == "pending-smoke":
            # Prepared (--prepare-only) for exactly this configuration: only the test is owed.
            return dict(summary(engines, devices, options.config_dir / "env.sh", **({"notes": notes} if notes else {})),
                        status="pending-smoke",
                        reason=f"Prepared for this configuration; only the setup test is owed. Run "
                               f"{setup_test_command(options)} in the foreground (never in the background: it may "
                               "stop and restore the local model for a GPU hand-over); --check passes once it does.")
        if not current or state.get("status") != "ready":
            raise SetupError("Saved setup/smoke is absent or stale for this configuration; rerun setup.")
        smokes = state.get("smoke") or {}
        if any(engine not in smokes or not check_smoke(smokes[engine]) for engine in engines):
            raise SetupError("Saved OCR smoke artifacts are missing or changed; rerun setup.")
        return summary(engines, devices, options.config_dir / "env.sh",
                       verified_at={engine: smokes[engine].get("completed_at") for engine in engines},
                       **({"notes": notes} if notes else {}))
    except (SetupError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "action-required", "reason": str(exc)}


def _setup_locked(options: SetupOptions) -> dict[str, Any]:
    system = platform_name()
    machine = machine_name(system)
    environment_path = options.config_dir / "env.sh"
    existing = read_environment(environment_path)
    if options.local_model is not None:
        # Written first, so a hand-over can test the GPU even if a later step
        # stops this run.
        write_environment(environment_path, changed_keys(existing, local_model_updates(options.local_model, existing)))
        existing = read_environment(environment_path)
    lock = load_lock(options.repo_root)
    # macOS, first setup: the person chooses the engines before anything else.
    options = ask_engine_choice(options, existing, lock, system=system, machine=machine)
    engines, devices, plan, gpus, notes, engine_wanted, device_wanted = plan_engines(
        options, existing, lock, system=system, machine=machine)
    agent = os.environ.get("QWEN_CODE_MODEL", "").strip()
    if options.local_model is not None and agent and agent != options.local_model.name.strip():
        notes.append(f"This Qwen Code session runs {agent}, not --local-model-name {options.local_model.name}: "
                     "proofread_pages.py treats the agent as the local model only when the two are the same.")
    for note in notes:
        print("NOTE " + note)
    ensure_dependencies(engines, options, system)
    options.data_dir.mkdir(parents=True, exist_ok=True)
    state_path = options.config_dir / "setup-state.json"
    try:
        prior = json.loads(state_path.read_text())
    except (OSError, ValueError):
        prior = {}
    trusted = dict(prior.get("trustOnFirstUse") or {})
    if not gpus and devices.get("hunyuan") == "cuda":
        gpus = nvidia_gpus()
    gpu = primary_gpu(gpus)
    slots_chosen_here = False
    release = (release_binary(lock, system=system, machine=machine, device=devices["hunyuan"], gpu=gpu)
               if "hunyuan" in engines else None)
    check_free_disk(options, disk_needs(options, existing, lock, engines, release, plan,
                                        device=devices.get("hunyuan"), gpu=gpu))
    config = dict(existing)
    config.update({"JYUT_OCR_PYTHON": str(ensure_python(options, existing)), "JYUT_OCR_ENGINE": engine_wanted,
                   "JYUT_OCR_ENGINES": ",".join(engines), "JYUT_OCR_DEVICE": device_wanted})
    if "hunyuan" in engines:
        config.update(ensure_models(options, existing, lock))
        device = devices["hunyuan"]
        try:
            binary, _ = ensure_runtime(options, existing, lock, device, release=release)
        except DownloadInterrupted:
            raise
        except SetupError as exc:
            if device_wanted != "auto" or device == "cpu":
                raise
            # For now only: the request stays auto, so the next run tries again.
            print(f"The automatic {device} runtime could not be prepared ({str(exc).splitlines()[0]}); "
                  "using the pinned CPU runtime for now. Fix the cause and rerun setup to use the GPU.")
            device = devices["hunyuan"] = "cpu"
            binary, _ = ensure_runtime(options, existing, lock, device,
                                       release=release_binary(lock, system=system, machine=machine, device="cpu", gpu=None))
        config.update({"HUNYUAN_LLAMA_SERVER": str(binary), "OCR_HUNYUAN_DEVICE": device})
        config.setdefault("OCR_HUNYUAN_MODE", "shared")
        # A saved count is kept as it is.  None saved: what the card holds (one where that is
        # not known), and one again should the setup test not pass with it.
        slots_chosen_here = not config.get("OCR_HUNYUAN_SLOTS")
        if slots_chosen_here:
            config["OCR_HUNYUAN_SLOTS"] = str(hunyuan_slots_for(gpu, device))
            if config["OCR_HUNYUAN_SLOTS"] != "1":
                print(f"HunyuanOCR reads {config['OCR_HUNYUAN_SLOTS']} pages at once on this card "
                      "(OCR_HUNYUAN_SLOTS in env.sh; change it there).")
        validate_hunyuan_config(config)
    if plan is not None and "paddle" in engines:
        config.update(ensure_paddle_weights(options, existing, lock))
        python, _ = ensure_paddle_python(options, existing, lock, plan, gpu, state_path, trusted)
        config.update({"PADDLEOCR_PYTHON": str(python), "OCR_PADDLE_DEVICE": plan.device})
        config.update({"JYUT_OCR_PADDLE_VL_BACKEND": paddle_backend_request(options.paddle_vl_backend, existing),
                       "OCR_PADDLE_VL_BACKEND": plan.backend})
        if plan.backend == "mlx-vlm-server":
            mlx_python, _ = ensure_mlx_python(options, config, lock)
            config["MLX_VLM_PYTHON"] = str(mlx_python)
        try:
            trusted = dict(json.loads(state_path.read_text()).get("trustOnFirstUse") or trusted)
        except (OSError, ValueError):
            pass
    write_environment(environment_path, changed_keys(existing, config))
    identity = environment_identity(options, config, engines, devices, lock)
    fingerprint = setup_fingerprint(identity)
    smokes = prior.get("smoke") if isinstance(prior.get("smoke"), dict) else {}
    if prior.get("status") == "ready" and prior.get("fingerprint") == fingerprint and all(
            engine in smokes and check_smoke(smokes[engine]) for engine in engines):
        return summary(engines, devices, environment_path, smoke="already-verified",
                       **({"notes": notes} if notes else {}))
    state = {"schema_version": STATE_SCHEMA, "status": "pending-smoke", "identity": identity,
             "fingerprint": fingerprint, "updated_at": now()}
    if trusted:
        state["trustOnFirstUse"] = trusted
    atomic_json(state_path, state)
    if options.prepare_only:
        return dict(summary(engines, devices, environment_path, **({"notes": notes} if notes else {})),
                    status="pending-smoke",
                    reason="Prepared; the setup test has not run. Rerun without --prepare-only, in the foreground, to run it.")
    paddle_need = plan.need_mib if plan is not None and plan.gpu else PADDLE_NEED_MIB
    try:
        try:
            state["smoke"] = run_smokes(options, config, engines, devices, fingerprint, paddle_need)
        except (SetupError, OSError, ValueError) as first:
            if not (slots_chosen_here and config.get("OCR_HUNYUAN_SLOTS") != "1"):
                raise
            # The count worked out for the card did not pass: one slot, which every setup
            # before 2026-11-17 used, and the test again.
            print(f"The setup test did not pass with {config['OCR_HUNYUAN_SLOTS']} HunyuanOCR slots "
                  f"({str(first).splitlines()[0]}); trying one slot.")
            config["OCR_HUNYUAN_SLOTS"] = "1"
            write_environment(environment_path, {"OCR_HUNYUAN_SLOTS": "1"})
            identity = environment_identity(options, config, engines, devices, lock)
            fingerprint = setup_fingerprint(identity)
            state.update({"identity": identity, "fingerprint": fingerprint, "updated_at": now()})
            atomic_json(state_path, state)
            state["smoke"] = run_smokes(options, config, engines, devices, fingerprint, paddle_need)
    except (SetupError, OSError, ValueError) as exc:
        state.update({"status": "action-required", "error": str(exc), "updated_at": now()})
        atomic_json(state_path, state)
        raise
    state.update({"status": "ready", "updated_at": now()})
    atomic_json(state_path, state)
    return summary(engines, devices, environment_path,
                   smoke={engine: record["json_output"] for engine, record in state["smoke"].items()},
                   **({"notes": notes} if notes else {}))


def setup(options: SetupOptions) -> dict[str, Any]:
    """Serialize changes to both shared asset storage and machine configuration."""
    system = platform_name()
    existing = read_environment(options.config_dir / "env.sh")
    if options.engine not in (None, "default") or existing.get("JYUT_OCR_ENGINE") or existing.get("JYUT_OCR_ENGINES"):
        choose_engines(engine_request(options.engine, existing), system=system, machine=machine_name(system))
    for directory in (options.data_dir, options.config_dir):
        if directory.resolve().is_relative_to(options.repo_root.resolve()):
            raise SetupError("Environment data/config must be outside the skill repository.")
    locks = []
    try:
        for directory in sorted({options.data_dir.resolve(), options.config_dir.resolve()}, key=str):
            directory.mkdir(parents=True, exist_ok=True)
            stream = (directory / ".setup.lock").open("a+")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                stream.close()
                raise SetupError("Another setup process is using this data/config directory; retry when it finishes.") from exc
            locks.append(stream)
        return _setup_locked(options)
    finally:
        for stream in reversed(locks):
            stream.close()


if __name__ == "__main__":
    # The hand-over's runner for the setup tests (handover_smokes).
    if len(sys.argv) == 3 and sys.argv[1] == "--run-smoke-plan":
        raise SystemExit(run_smoke_plan(Path(sys.argv[2])))
    print("usage: _setup_ocr.py --run-smoke-plan PLAN.json (internal; run ./install.sh)", file=sys.stderr)
    raise SystemExit(2)
