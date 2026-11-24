"""Other clients of the local model server, and a stand-in that answers for it during a GPU handover.

run_ocr_phase.py stops the local model server (the GPU tenant) for the OCR
phase and starts it again afterwards.  Other programs may be using that
server: another agent session, an editor, a background proofreading run.  This
module finds them, waits until none of them has a connection open, and holds
the freed port while the server is down so that their next request is told to
wait instead of being refused.

find_other_clients(health_url, exclude_pids=..., match=..., ignore=...)
    Three kinds of client, one entry per process ({"pid", "cmd", "reason"}):
    - "socket": a process with an ESTABLISHED TCP connection to the health
      URL's host and port.  Linux reads /proc/net/tcp and /proc/net/tcp6 and
      each process's /proc/PID/fd links; macOS (or a Linux without them) asks
      lsof.  When the URL's host is a loopback address, a connection to any
      loopback address on that port counts.  A connection from a process this
      OS user cannot inspect (another user's, a proxy running as root) is
      reported once per user id with pid -1.
    - "process": a live process whose command line contains one of the
      `match` substrings (OCR_PHASE_CLIENT_MATCH, see parse_client_match), but
      that holds no such connection.  An idle agent session holds none, so the
      socket check alone cannot see it.  A process with a connection is
      reported as "socket" only.
    - "ignored": a client of either kind named by `ignore`
      (OCR_PHASE_CLIENT_IGNORE, see parse_client_ignore): a command-line
      substring, or "uid:N" for the processes of user id N (the pid -1
      entries too).  It never blocks.  For a client that keeps a pooled
      connection open whether or not a request is on it (a reverse proxy, an
      LLM gateway, a web UI): its connection is cut when the server stops,
      and its next one gets the stand-in.
    Never reported: the pids in exclude_pids, this process, its ancestors (the
    shell and the agent harness that started the wrapper) and its descendants
    (the OCR runners, the watchdog).  Zombies are not live and do not count.

wait_until_idle(health_url, exclude_pids=..., match=..., poll_s=..., timeout_s=..., ignore=...)
    The rule, exactly: every poll_s seconds, call find_other_clients; return
    True at the first call that reports no "socket" client, and False when
    timeout_s has passed since the start and the latest call still reported
    one.  timeout_s 0 checks once.  "process" and "ignored" clients never
    block: a "process" client holds no connection, so no request of its is
    in flight, and the stand-in makes its next request wait.  A connection
    counts whether a request is on it or it is only kept alive: Qwen Code
    0.27.3 keeps an idle connection open for 60 s after a request (undici
    keepAliveTimeout 6e4 in its chunk-E5XJBLSO.js), so a session whose turn
    has just ended is waited for up to about a minute, and a client that
    never closes its connection is waited for until the timeout unless
    `ignore` names it.  The check cannot see a request that starts after
    it returns; the stand-in covers a request that starts after the release,
    and a request that starts between the check and the release is cut off
    when the server stops.

listening(health_url), model_processes(health_url, candidates)
    For run_ocr_phase.py's question whether the model is up: whether
    anything accepts connections at the URL's host and port (a server still
    loading its model counts), and which of the candidate pids (the GPU's
    compute processes) are the model's: they listen on that port (Linux:
    /proc/net/tcp{,6} in state LISTEN), descend from one that does, or give
    it as --port on their own or a parent's command line.

StandIn(host, port, retry_after_s=30)
    A context manager.  Entering it binds host:port and answers every request
    (any method, any path) with 503, `Retry-After: N` and the JSON body
    {"error": {"message": "local model paused for OCR; retry after N seconds",
    "type": "server_busy"}}, one request per connection, until it is closed.
    It refuses to start (PortBusy) while anything still listens on the port:
    the real server has not let go of it yet.  It reads each request's body
    before answering (a client whose upload is still unread can get a reset
    instead of the answer) and waits CLIENT_CLOSE_GRACE for the client to
    close the connection first.  Closing the stand-in stops new connections
    at once and gives open ones CLOSE_GRACE to finish: a request on the wire
    is answered, and its client closes.  A connection still open after that
    (an idle keep-alive one, an upload that has not finished, a client that
    does not close after its answer) is reset, not closed with a FIN, and so
    is one whose client outlives CLIENT_CLOSE_GRACE.  Whoever sends the first
    FIN keeps the TIME_WAIT, so no connection leaves one on the server's
    port: the restored server can bind it at once, with or without
    SO_REUSEADDR.

How Qwen Code 0.27.3 treats the stand-in's 503 (read from its installed
bundle, not measured live; a live check needs the model server stopped):
- The OpenAI SDK 5.11.0 inside it (chunk-2UOMCYQG.js), built with
  generationConfig.maxRetries (default 3) and generationConfig.timeout,
  retries 408/409/429/5xx and waits Retry-After only when it is under 60 s;
  a longer one falls back to 0.5 s doubling, at most 8 s.  So N under 60
  also counts at this layer, and N of 60 or more gives a shorter total.
- Around each SDK call, retryWithBackoff (chunk-A6LYTPBP.js) makes 7
  attempts; for 429 or 503 with a positive Retry-After it waits exactly that
  long (capped only at 2^31-1 ms).  So one run tolerates 6 waits of N.
- Around that, the chat stream (chunk-ARONLIV5.js) retries a 429/503 failure
  generationConfig.maxRetries times (default 10) after
  max(min(60 s * 2^(k-1), 300 s), min(N, 300 s)).
- The "persistent" unattended mode (QWEN_CODE_UNATTENDED_RETRY) covers only
  429 and 529, not 503.
- Its error handler (chunk-CNWWXK3Q.js) re-wraps an error whose message says
  "timeout", "timed out", "etimedout" or "deadline exceeded", or whose type is
  "timeout", into a new error without the response headers, which would lose
  Retry-After: the stand-in's message and type say none of these.
Each of a client's requests is answered at once, so a client gives up, counted
from its first request, after about 6 * N seconds with maxRetries 0; after
about 26 * N + 60 seconds (2 x 7 x 2 = 28 requests; 840 s for N = 30) with
maxRetries 1 and N under 60, but about 13 * N with N from 60 to 300; with the
defaults (3 and 10) after hours.  Once the real server answers again, a
waiting client resumes at its next try: within N seconds, or, when it is in a
chat stream wait, within that wait (60 s at the first one, up to 300 s later).
"""
from __future__ import annotations

import contextlib
import errno
import ipaddress
import json
import os
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable
from urllib.parse import urlsplit

TCP_ESTABLISHED = "01"
# A command line longer than this is cut in the report: it is for messages.
CMD_LIMIT = 400
# How long a stand-in connection waits for the client to close it first.
CLIENT_CLOSE_GRACE = 5.0
# A request body larger than this is not read to the end before the answer.
MAX_DRAIN_BYTES = 1 << 30
# How long close() lets open stand-in connections finish before cutting them.
CLOSE_GRACE = 2.0
# SO_LINGER on, 0 s: close() then sends a reset and leaves no TIME_WAIT.
RESET_ON_CLOSE = struct.pack("ii", 1, 0)


class PortBusy(OSError):
    """The stand-in's port still has a listener: the real server has not gone."""


def parse_client_match(text: str | None) -> list[str]:
    """OCR_PHASE_CLIENT_MATCH as a list of substrings.

    Comma-separated; blank entries are dropped, since an empty substring
    would match every process on the machine.
    """
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def parse_client_ignore(text: str | None) -> list[str]:
    """OCR_PHASE_CLIENT_IGNORE as a list: command-line substrings, and "uid:N"
    entries for every process of user id N.  Comma-separated; blank entries
    are dropped, as in parse_client_match."""
    return parse_client_match(text)


def _ignore_rules(ignore: Iterable[str] | None) -> tuple[list[str], set[int]]:
    """(command-line substrings, user ids) of an ignore list."""
    texts: list[str] = []
    uids: set[int] = set()
    for item in (part.strip() for part in ignore or ()):
        if item.lower().startswith("uid:") and item[4:].strip().isdigit():
            uids.add(int(item[4:].strip()))
        elif item:
            texts.append(item)
    return texts, uids


def _uid_of(pid: int) -> int | None:
    """The user id PID runs as, or None when it cannot be read."""
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        pass
    try:
        result = subprocess.run(["ps", "-o", "uid=", "-p", str(pid)], text=True, capture_output=True,
                                check=False, timeout=10, stdin=subprocess.DEVNULL)
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def endpoint(health_url: str) -> tuple[str, int]:
    """The host and port a health URL points at (80 or 443 when it names none)."""
    parts = urlsplit(health_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL with a host: {health_url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.hostname, port


def _plain(address):
    """An IPv4-mapped IPv6 address as its IPv4 address; others unchanged."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _resolve(host: str, port: int) -> list[tuple[int, object]]:
    """(family, address) for each address host resolves to, in resolver order."""
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return [(family, literal)]
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    seen: list[tuple[int, object]] = []
    for family, _type, _proto, _name, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        entry = (family, ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0]))
        if entry not in seen:
            seen.append(entry)
    return seen


class _Targets:
    """The remote addresses that count as the model server."""

    def __init__(self, host: str, port: int) -> None:
        self.port = port
        self.addresses = {_plain(address) for _family, address in _resolve(host, port)}
        self.loopback = any(address.is_loopback for address in self.addresses)

    def __contains__(self, address) -> bool:
        address = _plain(address)
        return (self.loopback and address.is_loopback) or address in self.addresses


def _decode_proc_address(text: str):
    """A /proc/net/tcp{,6} address field (hex address in host order, hex port)."""
    hex_address, hex_port = text.split(":")
    raw = bytes.fromhex(hex_address)
    if sys.byteorder == "little":
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return ipaddress.ip_address(raw), int(hex_port, 16)


def parse_proc_net_tcp(text: str) -> list[tuple[object, int, str, int, int]]:
    """(remote address, remote port, state, uid, inode) for each socket line."""
    rows = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            remote, remote_port = _decode_proc_address(fields[2])
            rows.append((remote, remote_port, fields[3], int(fields[7]), int(fields[9])))
        except ValueError:
            continue
    return rows


def parse_lsof(text: str, targets: _Targets) -> set[int]:
    """The pids in `lsof -F pn` output whose connection's far end is a target."""
    pids: set[int] = set()
    pid = None
    for line in text.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None and "->" in value:
            remote = value.split("->", 1)[1].strip()
            host, _, port_text = remote.rpartition(":")
            try:
                address = ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
                port = int(port_text)
            except ValueError:
                continue
            if port == targets.port and address in targets:
                pids.add(pid)
    return pids


def _proc_socket_inodes(targets: _Targets) -> dict[int, int] | None:
    """{inode: uid} of the ESTABLISHED sockets to a target, or None without /proc/net/tcp."""
    found: dict[int, int] = {}
    readable = False
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name, encoding="ascii", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        readable = True
        for remote, remote_port, state, uid, inode in parse_proc_net_tcp(text):
            if state == TCP_ESTABLISHED and inode and remote_port == targets.port and remote in targets:
                found[inode] = uid
    return found if readable else None


def _proc_inode_owners(inodes: Iterable[int]) -> dict[int, set[int]]:
    """{inode: pids holding it} from /proc/PID/fd, for the processes this user may read."""
    wanted = set(inodes)
    owners: dict[int, set[int]] = {}
    if not wanted:
        return owners
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        fd_dir = f"/proc/{name}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                link = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if link.startswith("socket:["):
                try:
                    inode = int(link[8:-1])
                except ValueError:
                    continue
                if inode in wanted:
                    owners.setdefault(inode, set()).add(int(name))
    return owners


def _lsof_socket_pids(targets: _Targets) -> set[int] | None:
    """Pids with an ESTABLISHED connection to a target, from lsof; None without lsof."""
    lsof = shutil.which("lsof") or ("/usr/sbin/lsof" if os.path.exists("/usr/sbin/lsof") else None)
    if not lsof:
        return None
    try:
        result = subprocess.run(
            [lsof, "-nP", f"-iTCP:{targets.port}", "-sTCP:ESTABLISHED", "-Fpn"],
            text=True, capture_output=True, check=False, timeout=20, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # lsof exits 1 when nothing matched.
    return parse_lsof(result.stdout, targets)


def can_see_sockets() -> bool:
    """Whether this machine lets find_other_clients see connections (/proc/net/tcp or lsof)."""
    return os.path.exists("/proc/net/tcp") or bool(shutil.which("lsof")) or os.path.exists("/usr/sbin/lsof")


def _socket_clients(targets: _Targets) -> tuple[set[int], set[int]]:
    """(pids holding a connection to a target, uids of connections with no readable owner)."""
    inodes = _proc_socket_inodes(targets)
    if inodes is None:
        return _lsof_socket_pids(targets) or set(), set()
    owners = _proc_inode_owners(inodes)
    pids = {pid for holders in owners.values() for pid in holders}
    own_uid = os.getuid()
    # A socket of this user's with no owner was closed between the two reads.
    hidden = {uid for inode, uid in inodes.items() if inode not in owners and uid != own_uid}
    return pids, hidden


def _parse_ps(text: str) -> dict[int, tuple[int, str, str]]:
    """{pid: (ppid, state, command)} from `ps -A -ww -o pid=,ppid=,stat=,command=`."""
    table = {}
    for line in text.splitlines():
        fields = line.split(None, 3)
        if len(fields) < 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        table[pid] = (ppid, fields[2][:1], fields[3].strip() if len(fields) > 3 else "")
    return table


def _process_table() -> dict[int, tuple[int, str, str]]:
    """{pid: (ppid, state letter, command line)} for every process this user can see."""
    table: dict[int, tuple[int, str, str]] = {}
    if os.path.isdir("/proc/self/fd"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/stat", "rb") as handle:
                    stat = handle.read().decode("utf-8", "replace")
                with open(f"/proc/{name}/cmdline", "rb") as handle:
                    cmdline = handle.read()
            except OSError:
                continue
            # The command name in parentheses may itself hold spaces or parentheses.
            fields = stat[stat.rfind(")") + 2:].split()
            try:
                table[int(name)] = (int(fields[1]), fields[0], cmdline.replace(b"\0", b" ").decode("utf-8", "replace").strip())
            except (IndexError, ValueError):
                continue
        if table:
            return table
    try:
        result = subprocess.run(
            ["ps", "-A", "-ww", "-o", "pid=,ppid=,stat=,command="],
            text=True, capture_output=True, check=False, timeout=20, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return table
    return _parse_ps(result.stdout)


def _family(table: dict[int, tuple[int, str, str]], root: int) -> set[int]:
    """root, its ancestors and its descendants."""
    family = {root}
    pid = root
    while pid in table:
        parent = table[pid][0]
        if parent <= 0 or parent in family:
            break
        family.add(parent)
        pid = parent
    if root == os.getpid() and os.getppid() > 0:
        # Without a process table entry for this process, its parent at least.
        family.add(os.getppid())
    children: dict[int, list[int]] = {}
    for child, (parent, _state, _cmd) in table.items():
        children.setdefault(parent, []).append(child)
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in family:
                family.add(child)
                stack.append(child)
    return family


def _short(cmd: str) -> str:
    """A command line on one line, cut at CMD_LIMIT characters."""
    cmd = " ".join(cmd.split())
    return cmd if len(cmd) <= CMD_LIMIT else cmd[:CMD_LIMIT - 3] + "..."


def find_other_clients(health_url: str, *, exclude_pids: set[int], match: list[str],
                       ignore: Iterable[str] = ()) -> list[dict]:
    """The processes other than ours that use, or may use, the model server at health_url.

    Each entry is {"pid": int, "cmd": str, "reason": "socket" | "process" |
    "ignored"}, sorted by pid; see the module docstring for what each reason
    means and which processes are left out.
    """
    host, port = endpoint(health_url)
    targets = _Targets(host, port)
    table = _process_table()
    skip = _family(table, os.getpid()) | {int(pid) for pid in exclude_pids or ()}
    substrings = [text for text in (part.strip() for part in match or ()) if text]

    clients: dict[int, dict] = {}
    socket_pids, hidden_uids = _socket_clients(targets)
    for pid in sorted(socket_pids):
        entry = table.get(pid)
        if pid in skip or (entry is not None and entry[1] == "Z"):
            continue
        clients[pid] = {"pid": pid, "cmd": _short(entry[2] if entry else ""), "reason": "socket"}
    for pid, (_parent, state, cmd) in sorted(table.items()):
        if pid in skip or pid in clients or state == "Z" or not cmd:
            continue
        if any(text in cmd for text in substrings):
            clients[pid] = {"pid": pid, "cmd": _short(cmd), "reason": "process"}
    texts, uids = _ignore_rules(ignore)
    for pid, entry in clients.items():
        cmd = table[pid][2] if pid in table else entry["cmd"]
        if any(text in cmd for text in texts) or (uids and _uid_of(pid) in uids):
            entry["reason"] = "ignored"
    found = [clients[pid] for pid in sorted(clients)]
    for uid in sorted(hidden_uids):
        found.append({"pid": -1, "cmd": f"(a process of user id {uid}, which this user cannot inspect)",
                      "reason": "ignored" if uid in uids else "socket"})
    return found


def wait_until_idle(health_url: str, *, exclude_pids: set[int], match: list[str], poll_s: float,
                    timeout_s: float, on_wait: Callable[[list[dict]], None] | None = None,
                    ignore: Iterable[str] = ()) -> bool:
    """Wait until no other process has a connection open to the model server.

    True as soon as a check finds no "socket" client; False when timeout_s
    has passed and the latest check still found one.  "process" and
    "ignored" clients do not block.  on_wait, when given, is called with the
    blocking clients before each sleep.  The exact rule is in the module
    docstring.
    """
    if poll_s <= 0:
        raise ValueError("poll_s must be positive")
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        busy = [client for client in find_other_clients(health_url, exclude_pids=exclude_pids, match=match,
                                                        ignore=ignore)
                if client["reason"] == "socket"]
        if not busy:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if on_wait is not None:
            on_wait(busy)
        time.sleep(min(poll_s, remaining))


def listening(health_url: str) -> bool:
    """Whether something accepts TCP connections at the health URL's host and
    port: a model server that is up, or still loading its model."""
    host, port = endpoint(health_url)
    return _listening(host, port)


TCP_LISTEN = "0A"


def _listener_inodes(port: int) -> set[int]:
    """Inodes of the listening TCP sockets on PORT (any local address), from /proc/net/tcp{,6}."""
    found: set[int] = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(name, encoding="ascii", errors="replace") as handle:
                lines = handle.read().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != TCP_LISTEN:
                continue
            try:
                if int(fields[1].rsplit(":", 1)[1], 16) == port and int(fields[9]):
                    found.add(int(fields[9]))
            except (IndexError, ValueError):
                continue
    return found


def _names_port(command: str, port: int) -> bool:
    """Whether a command line gives PORT as its --port (--port N, --port=N)."""
    words = command.split()
    for index, word in enumerate(words):
        if word == "--port" and index + 1 < len(words) and words[index + 1] == str(port):
            return True
        if word == f"--port={port}":
            return True
    return False


def model_processes(health_url: str, candidates: Iterable[int]) -> set[int]:
    """Which of CANDIDATES (pids, the GPU's compute processes) are the model
    server at the health URL: a process that listens on its port, a
    descendant of one (an engine process its API server started), or one
    whose command line, or a parent's, gives that port as its --port (a
    server that loads its model before it listens).  Linux only (/proc);
    elsewhere none."""
    candidates = {int(pid) for pid in candidates}
    if not candidates or not os.path.isdir("/proc/self/fd"):
        return set()
    _host, port = endpoint(health_url)
    table = _process_table()
    owners = _proc_inode_owners(_listener_inodes(port))
    servers = {pid for pids in owners.values() for pid in pids}
    found: set[int] = set()
    for pid in candidates:
        seen: set[int] = set()
        current = pid
        while current in table and current not in seen and current > 1:
            seen.add(current)
            if current in servers or _names_port(table[current][2], port):
                found.add(pid)
                break
            current = table[current][0]
    return found


def _listening(host: str, port: int) -> bool:
    """Whether something accepts TCP connections at host:port (any of its addresses)."""
    for family, address in _resolve(host, port):
        probe = socket.socket(family, socket.SOCK_STREAM)
        probe.settimeout(2.0)
        try:
            probe.connect((str(address), port))
        except (ConnectionRefusedError, socket.gaierror):
            continue
        except socket.timeout:
            # Loopback does not time out unless a listener's backlog is full.
            return True
        except OSError:
            continue
        else:
            return True
        finally:
            probe.close()
    return False


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jyut-ocr-stand-in"
    sys_version = ""
    # Per-connection socket timeout while the request is read.
    timeout = 30

    def __getattr__(self, name: str):
        # Any method: BaseHTTPRequestHandler looks up do_<METHOD>.
        if name.startswith("do_"):
            return self._answer
        raise AttributeError(name)

    def _drain_body(self) -> None:
        """Read the request body to its end (up to MAX_DRAIN_BYTES) and drop it."""
        left = MAX_DRAIN_BYTES
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            while left > 0:
                line = self.rfile.readline(1024)
                if not line:
                    return
                try:
                    size = int(line.split(b";", 1)[0].strip() or b"0", 16)
                except ValueError:
                    return
                if size == 0:
                    while self.rfile.readline(1024) not in (b"\r\n", b"\n", b""):
                        pass
                    return
                left -= self._discard(size + 2)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        self._discard(min(length, left))

    def _discard(self, count: int) -> int:
        done = 0
        while done < count:
            chunk = self.rfile.read(min(1 << 16, count - done))
            if not chunk:
                break
            done += len(chunk)
        return done

    def _answer(self) -> None:
        stand_in = self.server.stand_in
        try:
            self._drain_body()
        except OSError:
            self.close_connection = True
            return
        self.send_response(503, "Service Unavailable")
        self.send_header("Retry-After", str(stand_in.retry_after_s))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(stand_in.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(stand_in.body)
        self.wfile.flush()
        self.close_connection = True
        stand_in._answered()

    def finish(self) -> None:
        try:
            super().finish()
            # Let the client close first: the side that closes first keeps the
            # TIME_WAIT, and on the server's port it could stop the restored
            # server from binding.  One that does not is reset (shutdown_request).
            self.connection.settimeout(CLIENT_CLOSE_GRACE)
            while self.connection.recv(1 << 16):
                pass
            self.server.stand_in._client_closed(self.connection)
        except OSError:
            pass

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name
        pass


class _Server(ThreadingHTTPServer):
    # SO_REUSEADDR lets the stand-in bind beside the real server's TIME_WAIT
    # connections; it never lets two listeners share an address (the
    # wildcard-beside-specific case of BSD is caught by the connect probe).
    allow_reuse_address = True
    allow_reuse_port = False
    daemon_threads = False
    block_on_close = True
    request_queue_size = 64

    def __init__(self, family: int, server_address, stand_in: "StandIn") -> None:
        self.address_family = family
        self.stand_in = stand_in
        super().__init__(server_address, _Handler, bind_and_activate=False)
        try:
            if family == socket.AF_INET6 and hasattr(socket, "IPV6_V6ONLY"):
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.socket.close()
            raise

    def server_bind(self) -> None:
        # HTTPServer.server_bind also looks the host's name up, which can stall.
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]

    def process_request(self, request, client_address) -> None:
        # Known before its thread starts, so close() can always cut it.
        self.stand_in._opened(request)
        super().process_request(request, client_address)

    def shutdown_request(self, request) -> None:
        try:
            if self.stand_in._closed_by_client(request):
                # Its FIN came first: ours goes second, and no TIME_WAIT stays here.
                super().shutdown_request(request)
            else:
                # Still open (cut by close(), or the client did not close in
                # time): a reset instead of a FIN, which would leave a TIME_WAIT.
                with contextlib.suppress(OSError):
                    request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET_ON_CLOSE)
                self.close_request(request)
        finally:
            self.stand_in._closed(request)

    def handle_error(self, request, client_address) -> None:
        pass


class StandIn:
    """Answer 503 + Retry-After on host:port until closed (see the module docstring)."""

    def __init__(self, host: str, port: int, retry_after_s: int = 30) -> None:
        if isinstance(retry_after_s, bool) or int(retry_after_s) != retry_after_s or not 1 <= retry_after_s <= 86400:
            raise ValueError("retry_after_s must be a whole number of seconds from 1 to 86400")
        self.host = host
        self.port = int(port)
        self.retry_after_s = int(retry_after_s)
        self.requests = 0
        self.body = json.dumps({"error": {
            "message": f"local model paused for OCR; retry after {self.retry_after_s} seconds",
            "type": "server_busy",
        }}).encode("utf-8")
        self._servers: list[_Server] = []
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        # Connections whose client closed first, and the ones close() cut.
        self._finished: set[socket.socket] = set()
        self._cut: set[socket.socket] = set()

    def start(self) -> "StandIn":
        if self._servers:
            return self
        if self.port and _listening(self.host, self.port):
            raise PortBusy(errno.EADDRINUSE, f"{self.host}:{self.port} still has a listener: "
                                             "the local model server has not let go of the port yet")
        addresses = _resolve(self.host, self.port)
        if not addresses:
            raise OSError(errno.EADDRNOTAVAIL, f"{self.host} does not resolve to an address to bind")
        if self.port == 0:
            addresses = addresses[:1]
        try:
            for family, address in addresses:
                try:
                    server = _Server(family, (str(address), self.port), self)
                except OSError as error:
                    if error.errno == errno.EADDRINUSE:
                        raise PortBusy(errno.EADDRINUSE, f"{address}:{self.port} is still taken: "
                                                         "the local model server has not let go of the port yet") from error
                    if len(addresses) > 1 and error.errno in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT):
                        continue
                    raise
                self._servers.append(server)
            if not self._servers:
                raise OSError(errno.EADDRNOTAVAIL, f"no address of {self.host} could be bound")
        except BaseException:
            self.close()
            raise
        self.port = self._servers[0].server_address[1]
        try:
            for server in self._servers:
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                                          name=f"stand-in {self.host}:{self.port}", daemon=True)
                thread.start()
                self._threads.append(thread)
        except BaseException:
            # A signal between two threads: nothing stays bound.
            self.close()
            raise
        return self

    def close(self) -> None:
        servers, self._servers = self._servers, []
        threads, self._threads = self._threads, []
        # shutdown() waits for serve_forever, so only for a server whose thread
        # started (a start() cut short leaves the others unstarted).
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join()
        for server in servers:
            # New connections are refused from here, as after the close; one
            # not yet accepted is reset with the listening socket.
            server.socket.close()
        deadline = time.monotonic() + CLOSE_GRACE
        while time.monotonic() < deadline:
            with self._lock:
                if not self._connections:
                    break
            time.sleep(0.02)
        with self._lock:
            lingering = list(self._connections)
            self._cut.update(lingering)
        for connection in lingering:
            # Wake the handler without sending anything (SHUT_WR would send a
            # FIN); shutdown_request then resets the connection.
            with contextlib.suppress(OSError):
                connection.shutdown(socket.SHUT_RD)
        for server in servers:
            server.server_close()

    def __enter__(self) -> "StandIn":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _answered(self) -> None:
        with self._lock:
            self.requests += 1

    def _opened(self, connection: socket.socket) -> None:
        with self._lock:
            self._connections.add(connection)

    def _client_closed(self, connection: socket.socket) -> None:
        with self._lock:
            if connection not in self._cut:
                self._finished.add(connection)

    def _closed_by_client(self, connection: socket.socket) -> bool:
        with self._lock:
            return connection in self._finished and connection not in self._cut

    def _closed(self, connection: socket.socket) -> None:
        with self._lock:
            self._connections.discard(connection)
            self._finished.discard(connection)
            self._cut.discard(connection)
