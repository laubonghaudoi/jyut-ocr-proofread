"""Portable, resumable OCR environment setup (stdlib bootstrap only).

This module never sources shell configuration, installs GPU drivers, or treats
file presence as evidence that an OCR engine is ready. Expensive work is opt-in
through setup(); inspect_setup() is read-only and never starts an OCR model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.request


class SetupError(RuntimeError):
    """Setup can be resumed after the reported missing prerequisite is supplied."""


@dataclass(frozen=True)
class SetupOptions:
    repo_root: Path
    config_dir: Path
    data_dir: Path
    engine: str = "auto"
    device: str = "auto"
    offline: bool = False
    install_system_deps: bool = False
    interactive: bool = False


CONFIG_KEYS = frozenset({
    "JYUT_OCR_PYTHON", "JYUT_OCR_ENGINE", "HUNYUAN_MODEL", "HUNYUAN_MMPROJ",
    "HUNYUAN_LLAMA_SERVER", "OCR_HUNYUAN_DEVICE", "OCR_HUNYUAN_MODE",
    "OCR_HUNYUAN_SLOTS",
})
EXPORT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*?)\s*$")
STATE_SCHEMA = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_environment(path: Path, *, home: Path | None = None) -> dict[str, str]:
    """Read literal single-line exports; never execute shell or substitute commands.

    Unknown lines survive write_environment() untouched. Only $HOME and ${HOME}
    expansion is supported; dynamic settings require an explicit literal path.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    home_text = str(home or Path.home())
    for line in path.read_text(encoding="utf-8").splitlines():
        match = EXPORT.match(line)
        if not match or match[1] not in CONFIG_KEYS:
            continue
        name, raw = match.groups()
        if "$(" in raw or "`" in raw:
            raise SetupError(f"Cannot safely read dynamic {name} in {path}; use a literal single-line export.")
        try:
            tokens = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            raise SetupError(f"Cannot parse {name} in {path}: use a literal single-line export.") from exc
        if len(tokens) > 1:
            raise SetupError(f"Cannot safely read {name} in {path}; quote its value.")
        value = tokens[0] if tokens else ""
        value = value.replace("${HOME}", home_text)
        value = re.sub(r"\$HOME\b", lambda _: home_text, value)
        if "$" in value:
            raise SetupError(f"Cannot expand {name} in {path}; only HOME expansion is supported.")
        values[name] = value
    return values


def write_environment(path: Path, updates: dict[str, str]) -> None:
    """Replace selected keys atomically while preserving other lines and a backup."""
    original = path.read_text(encoding="utf-8") if path.is_file() else ""
    pending = dict(updates)
    output: list[str] = []
    seen: set[str] = set()
    for line in original.splitlines(keepends=True):
        match = EXPORT.match(line.rstrip("\r\n"))
        name = match[1] if match else None
        if name in updates:
            if name not in seen:
                output.append(f"export {name}={shlex.quote(str(updates[name]))}\n")
                seen.add(name)
                pending.pop(name, None)
        else:
            output.append(line)
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    if not original:
        output.append("# Machine-local OCR environment. Generated without evaluating shell configuration.\n")
    output.extend(f"export {key}={shlex.quote(str(value))}\n" for key, value in pending.items())
    content = "".join(output)
    if content == original:
        return
    if original:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = path.parent / "env-backups" / f"env-{stamp}.sh"
        atomic_text(backup, original)
    atomic_text(path, content)


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
        raise SetupError(f"Command failed ({result.returncode}): {shlex.join(argv)}\n{detail}")
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
    return system


def default_directories() -> tuple[Path, Path]:
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "jyut-ocr"
    if platform.system() == "Darwin":
        data = Path(os.environ.get("XDG_DATA_HOME", str(home / "Library/Application Support"))) / "jyut-ocr"
    else:
        data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share"))) / "jyut-ocr"
    return config, data


def choose_engine(requested: str, existing: dict[str, str], *, system: str,
                  interactive: bool) -> str:
    if requested != "auto":
        if requested == "vision" and system != "Darwin":
            raise SetupError("Apple Vision requires macOS; choose --engine hunyuan on Linux/WSL2.")
        return requested
    saved = existing.get("JYUT_OCR_ENGINE")
    if saved in {"vision", "hunyuan"}:
        return choose_engine(saved, {}, system=system, interactive=False)
    if system == "Linux":
        return "hunyuan"
    if not interactive:
        raise SetupError("On macOS, noninteractive setup requires --engine vision or --engine hunyuan.")
    answer = input("OCR engine: [1] Apple Vision (built into macOS), [2] HunyuanOCR (~1.94 GiB download): ").strip()
    if answer not in {"1", "2"}:
        raise SetupError("Choose --engine vision or --engine hunyuan and rerun.")
    return "vision" if answer == "1" else "hunyuan"


def cuda_available() -> bool:
    return all(shutil.which(name) for name in ("nvcc", "nvidia-smi", "c++")) and command_ok(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]) and command_ok(["nvcc", "--version"])


def metal_available() -> bool:
    return (platform.system() == "Darwin" and command_ok(["xcrun", "--find", "metal"]) and
            command_ok(["xcrun", "--find", "clang"]))


def choose_device(requested: str, existing: dict[str, str], *, engine: str, system: str) -> str:
    if engine == "vision":
        if requested not in {"auto", "cpu"}:
            raise SetupError("--device applies to HunyuanOCR; Apple Vision manages its own compute device.")
        return "vision-managed"
    selection = requested
    if selection == "auto":
        selection = existing.get("OCR_HUNYUAN_DEVICE", "auto")
    if selection == "auto":
        return "cuda" if system == "Linux" and cuda_available() else "metal" if system == "Darwin" and metal_available() else "cpu"
    if selection == "cuda" and (system != "Linux" or not cuda_available()):
        raise SetupError("CUDA requested but a working NVIDIA driver, nvcc and C++ toolchain are not all available. Use --device cpu; setup never installs drivers/toolkits.")
    if selection == "metal" and (system != "Darwin" or not metal_available()):
        raise SetupError("Metal requested but Apple Metal/Command Line Tools are unavailable. Install them or use --device cpu.")
    if selection not in {"cpu", "cuda", "metal"}:
        raise SetupError("Invalid saved OCR_HUNYUAN_DEVICE; choose --device cpu, cuda or metal.")
    return selection


def missing_dependencies(engine: str, *, building: bool = False) -> list[str]:
    required = ["pdfinfo", "pdftoppm"]
    if engine == "hunyuan" and building:
        required += ["git", "cmake", "c++"]
    version_flag = {"pdfinfo": "-v", "pdftoppm": "-v"}
    missing = [item for item in required if not shutil.which(item) or
               not command_ok([item, version_flag.get(item, "--version")])]
    if engine == "vision":
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


def ensure_dependencies(engine: str, options: SetupOptions, system: str, *, building: bool = False) -> None:
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
    candidate = Path(existing.get("JYUT_OCR_PYTHON", str(options.data_dir / "python/bin/python")))
    if candidate.is_file():
        try:
            python_identity(candidate)
            return candidate.absolute()
        except SetupError:
            pass
    venv = options.data_dir / "python"
    python = venv / "bin/python"
    usable_python = python.is_file() and command_ok([str(python), "-c", "import sys; raise SystemExit(sys.version_info < (3,10))"])
    has_pip = usable_python and command_ok([str(python), "-m", "pip", "--version"])
    if not usable_python or not has_pip:
        # venv can fail after creating bin/python but before installing pip.
        # Preserve that partial environment and recreate at the final path so
        # switching bootstrap Python also repairs stale interpreter symlinks.
        if venv.exists():
            backup = venv.with_name("python-incomplete-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
            venv.rename(backup)
        try:
            run([sys.executable, "-m", "venv", venv])
        except SetupError as exc:
            raise SetupError("Could not create the isolated Python environment. On Ubuntu/Debian install python3-venv (matching the selected Python). Rerun setup afterwards.\n" + str(exc)) from exc
    command: list[str | Path] = [python, "-m", "pip", "install", "--disable-pip-version-check"]
    if options.offline:
        command.append("--no-index")
    command += ["-r", options.repo_root / "requirements.txt"]
    run(command)
    python_identity(python)
    return python.absolute()


def verify_asset(path: Path, asset: dict[str, Any]) -> bool:
    return path.is_file() and path.stat().st_size == asset["size"] and sha256_file(path) == asset["sha256"]


def download_asset(url: str, target: Path, asset: dict[str, Any], *, offline: bool = False) -> Path:
    """Resume only a correctly ranged response, then seal before atomic promotion."""
    if verify_asset(target, asset):
        return target
    if target.exists():
        raise SetupError(f"Existing model has the wrong size/hash: {target}. It was left untouched; move it aside before retrying.")
    if offline:
        raise SetupError(f"Offline cache lacks verified model: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists() and partial.stat().st_size > asset["size"]:
        raise SetupError(f"Oversized partial download left untouched: {partial}; move it aside and retry.")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == asset["size"]:
        if verify_asset(partial, asset):
            os.replace(partial, target)
            return target
        partial.rename(partial.with_name(partial.name + ".invalid-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")))
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
        raise SetupError(f"Download interrupted; rerun to resume {partial.name}: {exc}") from exc
    if not verify_asset(partial, asset):
        if partial.stat().st_size == asset["size"]:
            partial.rename(partial.with_name(partial.name + ".invalid-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")))
        raise SetupError(f"Model download did not match locked size/SHA-256: {target.name}; no model was installed. Rerun to resume or retry.")
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


def runtime_libraries(binary: Path) -> dict[str, str]:
    """Bind the local shared libraries that carry most of a llama-server build."""
    libraries = {}
    for path in sorted(binary.parent.glob("lib*")):
        if path.is_file() and (path.name.endswith((".so", ".dylib")) or ".so." in path.name):
            libraries[path.name] = sha256_file(path)
    return libraries


def runtime_identity(binary: Path, lock: dict[str, Any], device: str) -> dict[str, Any]:
    """Attest a managed build, or verify an existing pinned checkout and --version."""
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
                return {"path": str(binary.absolute()), "sha256": digest, "commit": manifest["commit"], "compiled_device": manifest["device"], "libraries": libraries, "verification": "sealed-build"}
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


def ensure_runtime(options: SetupOptions, existing: dict[str, str], lock: dict[str, Any], device: str) -> tuple[Path, dict[str, Any]]:
    managed = options.data_dir / "runtimes" / (lock["runtime"]["commit"] + "-" + device)
    binary = managed / "build/bin/llama-server"
    candidates = [Path(existing["HUNYUAN_LLAMA_SERVER"])] if existing.get("HUNYUAN_LLAMA_SERVER") else []
    candidates += [Path.home() / "llama.cpp/build/bin/llama-server", binary]
    for candidate in candidates:
        try:
            return candidate.absolute(), runtime_identity(candidate, lock, device)
        except (SetupError, ValueError, KeyError, OSError):
            continue
    # A verified runtime needs no compiler/CMake installation. Only a fresh or
    # resumed build requires the build toolchain; PDF tools were checked above.
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
    configure = ["cmake", "-S", str(source), "-B", str(managed / "build"),
                 "-DCMAKE_BUILD_TYPE=Release", "-DGGML_CUDA=" + ("ON" if device == "cuda" else "OFF"),
                 "-DGGML_METAL=" + ("ON" if device == "metal" else "OFF"),
                 "-DLLAMA_OPENSSL=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF", "-DLLAMA_BUILD_UI=OFF",
                 "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_SERVER=ON"]
    run(configure)
    run(["cmake", "--build", managed / "build", "--config", "Release", "--target", "llama-server", "-j", str(min(os.cpu_count() or 1, 8))])
    # A failed build is resumable; do not seal it or overwrite an unrelated binary.
    if not binary.is_file():
        raise SetupError("Runtime build completed without producing llama-server.")
    atomic_json(managed / "jyut-ocr-build.json", {"schema_version": 1, "commit": lock["runtime"]["commit"], "device": device,
                "binary_sha256": sha256_file(binary), "libraries": runtime_libraries(binary), "configure": configure, "created_at": now()})
    return binary, runtime_identity(binary, lock, device)


def tool_hashes(root: Path, engine: str) -> dict[str, str]:
    names = ["scripts/_setup_ocr.py", "requirements.txt", "runtime/ocr-lock.json"]
    names += ["scripts/run_apple_vision_ocr.py", "scripts/apple_vision_ocr.swift"] if engine == "vision" else [
        "scripts/run_hunyuanocr.py", "scripts/_hunyuan_shared.py", "scripts/_gpu_queue.py", "scripts/_phase_budget.py"]
    return {name: sha256_file(root / name) for name in names}


def validate_hunyuan_config(config: dict[str, str]) -> None:
    try:
        slots = int(config.get("OCR_HUNYUAN_SLOTS", "1"))
    except ValueError as exc:
        raise SetupError("OCR_HUNYUAN_SLOTS must be an integer from 1 to 32.") from exc
    if not 1 <= slots <= 32 or config.get("OCR_HUNYUAN_MODE", "shared") not in {"shared", "dedicated"}:
        raise SetupError("Saved Hunyuan settings are invalid: slots must be 1..32 and mode shared or dedicated; correct env.sh and retry.")


def environment_identity(options: SetupOptions, config: dict[str, str], engine: str, device: str,
                         lock: dict[str, Any]) -> dict[str, Any]:
    identity: dict[str, Any] = {"engine": engine, "device": device,
        "config": {key: config.get(key) for key in sorted(CONFIG_KEYS) if not key.startswith("OCR_PHASE_") and key != "PADDLEOCR_PYTHON"},
        "tools": tool_hashes(options.repo_root, engine), "python": python_identity(Path(config["JYUT_OCR_PYTHON"]))}
    if engine == "hunyuan":
        validate_hunyuan_config(config)
        for asset in lock["model"]["files"]:
            key = "HUNYUAN_MODEL" if asset["role"] == "model" else "HUNYUAN_MMPROJ"
            if not config.get(key) or not verify_asset(Path(config[key]), asset):
                raise SetupError(f"Configured {key} does not match the locked model SHA-256.")
        identity["runtime"] = runtime_identity(Path(config["HUNYUAN_LLAMA_SERVER"]), lock, device)
    return identity


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


def smoke_test(options: SetupOptions, config: dict[str, str], engine: str, device: str,
               fingerprint: str) -> dict[str, Any]:
    if device == "cuda":
        free_text = run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture=True).stdout
        try:
            free = min(int(line.strip()) for line in free_text.splitlines() if line.strip())
        except (ValueError, TypeError):
            raise SetupError("Could not verify free CUDA memory; rerun with --device cpu or inspect the GPU.")
        if free < 4096:
            raise SetupError("GPU is busy (< 4 GiB free); OCR smoke was not started and no service was stopped. Free GPU memory or rerun --device cpu.")
    directory = options.data_dir / "setup-smoke" / (fingerprint[:16] + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    renders, output = directory / "renders", directory / "ocr"
    renders.mkdir(parents=True)
    render = renders / "page-0001.png"
    # Pure synthetic text: this is an environment probe, never an accuracy benchmark.
    generator = "from PIL import Image,ImageDraw,ImageFont; import sys; im=Image.new('RGB',(1200,400),'white'); d=ImageDraw.Draw(im); f=ImageFont.load_default(size=60); d.text((70,80),'OCR SETUP TEST 12345',fill='black',font=f); d.text((70,190),'One page. Ready.',fill='black',font=f); im.save(sys.argv[1])"
    run([config["JYUT_OCR_PYTHON"], "-c", generator, render])
    runner = options.repo_root / "scripts" / ("run_hunyuanocr.py" if engine == "hunyuan" else "run_apple_vision_ocr.py")
    command: list[str | Path] = [config["JYUT_OCR_PYTHON"], runner, renders, output, "--pages", "1", "--workers", "1", "--report", directory / "run-report.json"]
    environment = dict(os.environ)
    environment.update(config)
    # Installation cannot implicitly stop a user's unrelated model service.
    for key in list(environment):
        if key.startswith("OCR_PHASE_") or key == "HUNYUAN_ENDPOINT":
            environment.pop(key, None)
    if engine == "hunyuan":
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        command += ["--model", config["HUNYUAN_MODEL"], "--mmproj", config["HUNYUAN_MMPROJ"], "--llama-server", config["HUNYUAN_LLAMA_SERVER"],
                    "--device", device, "--server-mode", "dedicated", "--server-slots", "1", "--ctx-size", "4096", "--port", str(port),
                    "--max-tokens", "256", "--timeout", "1200"]
    run(command, env=environment, timeout=1800, own_group=True)
    text_path, json_path = output / "page-0001.txt", output / "page-0001.json"
    files = [render, text_path, json_path, directory / "run-report.json"]
    if not all(path.is_file() for path in files):
        raise SetupError("OCR smoke did not produce its complete text/JSON/report evidence; setup remains incomplete.")
    record = {"completed_at": now(), "command": [str(value) for value in command], "render": str(render),
              "json_output": str(json_path), "text_output": str(text_path), "artifacts": [{"path": str(path), "sha256": sha256_file(path)} for path in files]}
    if not check_smoke(record):
        raise SetupError("OCR smoke returned empty, incomplete or unsealed output; setup remains incomplete.")
    return record


def load_lock(root: Path) -> dict[str, Any]:
    try:
        lock = json.loads((root / "runtime/ocr-lock.json").read_text())
        assert lock["schema_version"] == 1 and len(lock["model"]["files"]) == 2
        assert re.fullmatch(r"[0-9a-f]{40}", lock["runtime"]["commit"])
        for asset in lock["model"]["files"]:
            assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) and asset["size"] > 0
        return lock
    except (OSError, ValueError, KeyError, AssertionError) as exc:
        raise SetupError("Invalid or missing runtime/ocr-lock.json.") from exc


def inspect_setup(options: SetupOptions) -> dict[str, Any]:
    """Read-only: no directories, downloads, config writes or model starts."""
    try:
        system = platform_name()
        config = read_environment(options.config_dir / "env.sh")
        engine = choose_engine(options.engine, config, system=system, interactive=False)
        device = choose_device(options.device, config, engine=engine, system=system)
        missing = missing_dependencies(engine)
        if missing:
            raise SetupError("Missing dependencies: " + ", ".join(missing))
        state = json.loads((options.config_dir / "setup-state.json").read_text())
        identity = environment_identity(options, config, engine, device, load_lock(options.repo_root))
        if state.get("schema_version") != STATE_SCHEMA or state.get("status") != "ready" or state.get("fingerprint") != canonical_hash(identity):
            raise SetupError("Saved setup/smoke is absent or stale for this configuration; rerun setup.")
        if not check_smoke(state["smoke"]):
            raise SetupError("Saved OCR smoke artifacts are missing or changed; rerun setup.")
        return {"status": "ready", "engine": engine, "device": device, "config": str(options.config_dir / "env.sh"), "verified_at": state["smoke"]["completed_at"]}
    except (SetupError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "action-required", "reason": str(exc)}


def _setup_locked(options: SetupOptions) -> dict[str, Any]:
    system = platform_name()
    environment_path = options.config_dir / "env.sh"
    existing = read_environment(environment_path)
    engine = choose_engine(options.engine, existing, system=system, interactive=options.interactive)
    ensure_dependencies(engine, options, system)
    device = choose_device(options.device, existing, engine=engine, system=system)
    options.data_dir.mkdir(parents=True, exist_ok=True)
    config = dict(existing)
    config.update({"JYUT_OCR_PYTHON": str(ensure_python(options, existing)), "JYUT_OCR_ENGINE": engine})
    lock = load_lock(options.repo_root)
    if engine == "hunyuan":
        config.update(ensure_models(options, existing, lock))
        try:
            binary, _ = ensure_runtime(options, existing, lock, device)
        except SetupError:
            if options.device != "auto" or existing.get("OCR_HUNYUAN_DEVICE", "auto") != "auto" or device == "cpu":
                raise
            print(f"The automatic {device} runtime could not be prepared; retrying the pinned CPU build.")
            device = "cpu"
            binary, _ = ensure_runtime(options, existing, lock, device)
        config.update({"HUNYUAN_LLAMA_SERVER": str(binary), "OCR_HUNYUAN_DEVICE": device})
        config.setdefault("OCR_HUNYUAN_MODE", "shared")
        config.setdefault("OCR_HUNYUAN_SLOTS", "1")
        validate_hunyuan_config(config)
    write_environment(environment_path, config)
    identity = environment_identity(options, config, engine, device, lock)
    fingerprint = canonical_hash(identity)
    state_path = options.config_dir / "setup-state.json"
    try:
        prior = json.loads(state_path.read_text())
    except (OSError, ValueError):
        prior = {}
    if prior.get("status") == "ready" and prior.get("fingerprint") == fingerprint and check_smoke(prior.get("smoke", {})):
        return {"status": "ready", "engine": engine, "device": device, "config": str(environment_path), "smoke": "already-verified"}
    state = {"schema_version": STATE_SCHEMA, "status": "pending-smoke", "identity": identity,
             "fingerprint": fingerprint, "updated_at": now()}
    atomic_json(state_path, state)
    try:
        state["smoke"] = smoke_test(options, config, engine, device, fingerprint)
    except (SetupError, OSError, ValueError) as exc:
        state.update({"status": "action-required", "error": str(exc), "updated_at": now()})
        atomic_json(state_path, state)
        raise
    state.update({"status": "ready", "updated_at": now()})
    atomic_json(state_path, state)
    return {"status": "ready", "engine": engine, "device": device, "config": str(environment_path), "smoke": state["smoke"]["json_output"]}


def setup(options: SetupOptions) -> dict[str, Any]:
    """Serialize changes to both shared asset storage and machine configuration."""
    platform_name()
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
