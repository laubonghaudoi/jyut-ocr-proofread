"""Other clients of the local model and the 503 stand-in (scripts/_tenant_clients.py).

No model and no GPU: a listening socket in this process plays the model
server, and the clients are real processes that this test starts.  A client
outside this process's family (ancestors and descendants are never reported)
is made by a double fork: its parent exits at once, so it is re-parented away
from this process.  Each such process stops by itself after 60 s at most.
"""
from __future__ import annotations

import errno
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _tenant_clients as tc  # noqa: E402

# argv: pid file, release file, host or '-', port or '-', marker words...
HOLDER = r'''
import os, socket, sys, time
pid_file, release, host, port = sys.argv[1:5]
if os.environ.get('HOLDER_ORPHAN') and os.fork():
    os._exit(0)
if os.environ.get('HOLDER_ORPHAN'):
    os.setsid()
held = []
if port != '-':
    held.append(socket.create_connection((host, int(port)), timeout=5))
with open(pid_file + '.tmp', 'w') as handle:
    handle.write(str(os.getpid()))
os.replace(pid_file + '.tmp', pid_file)
end = time.monotonic() + 60
while time.monotonic() < end and not os.path.exists(release):
    time.sleep(0.02)
'''

LINUX_PROC = os.path.exists("/proc/net/tcp") and os.path.isdir("/proc/self/fd")


def ipv6_loopback() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
        return True
    except OSError:
        return False


class FakeServer:
    """A listener that accepts connections and holds them open."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self.listener = socket.socket(family, socket.SOCK_STREAM)
        self.listener.bind((host, 0))
        self.listener.listen(16)
        self.port = self.listener.getsockname()[1]
        self.host = host
        self.accepted: list[socket.socket] = []
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/v1/models"

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            self.accepted.append(connection)

    def close(self) -> None:
        self.listener.close()
        for connection in self.accepted:
            connection.close()


@unittest.skipUnless(os.name == "posix", "POSIX processes and sockets")
class ClientsTest(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        self.marker = f"fake-agent-cli-{uuid.uuid4().hex}.js"
        self.script = self.base / "holder.py"
        self.script.write_text(HOLDER, encoding="utf-8")

    def holder(self, *, server: FakeServer | None = None, orphan: bool = True, marker: str = "",
               connect: bool = True) -> tuple[int, Path]:
        """Start a process that holds a connection (or not); return its pid and release file."""
        name = uuid.uuid4().hex
        pid_file, release = self.base / f"{name}.pid", self.base / f"{name}.release"
        server = server or self.server
        args = [sys.executable, str(self.script), str(pid_file), str(release),
                server.host if connect else "-", str(server.port) if connect else "-"]
        if marker:
            args.append(marker)
        env = dict(os.environ, HOLDER_ORPHAN="1" if orphan else "")
        accepted = len(server.accepted)
        process = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        if orphan:
            process.wait(timeout=10)
        else:
            self.addCleanup(process.wait, 10)
        deadline = time.monotonic() + 10
        while not pid_file.exists():
            self.assertLess(time.monotonic(), deadline, "the holder did not start")
            time.sleep(0.02)
        pid = int(pid_file.read_text())
        self.addCleanup(self.stop, pid, release)
        if connect:
            # The connection is open once its pid file is written; wait for the accept too.
            deadline = time.monotonic() + 5
            while len(server.accepted) <= accepted:
                self.assertLess(time.monotonic(), deadline, "the holder's connection was not accepted")
                time.sleep(0.01)
        return pid, release

    @staticmethod
    def stop(pid: int, release: Path) -> None:
        release.touch()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.02)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def find(self, url: str | None = None, *, exclude=(), match=()) -> dict[int, dict]:
        found = tc.find_other_clients(url or self.server.url, exclude_pids=set(exclude), match=list(match))
        for entry in found:
            self.assertEqual(set(entry), {"pid", "cmd", "reason"})
        return {entry["pid"]: entry for entry in found}

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_a_process_outside_the_family_with_a_connection_is_a_socket_client(self) -> None:
        pid, _ = self.holder(marker=self.marker)
        found = self.find()
        self.assertIn(pid, found)
        self.assertEqual(found[pid]["reason"], "socket")
        self.assertIn(self.marker, found[pid]["cmd"])

    def test_a_child_of_this_process_is_never_reported(self) -> None:
        pid, _ = self.holder(orphan=False, marker=self.marker)
        found = self.find(match=[self.marker])
        self.assertNotIn(pid, found)

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_an_ancestor_of_the_caller_is_never_reported(self) -> None:
        held = socket.create_connection(("127.0.0.1", self.server.port))
        self.addCleanup(held.close)
        outside, _ = self.holder()
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); import _tenant_clients as tc; "
                "print(json.dumps(tc.find_other_clients(sys.argv[2], exclude_pids=set(), match=[])))")
        result = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts"), self.server.url],
                                text=True, capture_output=True, timeout=60, check=True)
        pids = {entry["pid"] for entry in json.loads(result.stdout)}
        self.assertIn(outside, pids, "the control: a connection outside the family is seen")
        self.assertNotIn(os.getpid(), pids, "the caller's parent holds a connection and is left out")

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_exclude_pids_are_left_out(self) -> None:
        pid, _ = self.holder(marker=self.marker)
        self.assertIn(pid, self.find())
        self.assertNotIn(pid, self.find(exclude={pid}, match=[self.marker]))

    def test_a_matched_process_without_a_connection_is_a_process_client(self) -> None:
        pid, _ = self.holder(marker=self.marker, connect=False)
        found = self.find(match=["no-such-program-anywhere", self.marker])
        self.assertIn(pid, found)
        self.assertEqual(found[pid]["reason"], "process")
        self.assertIn(self.marker, found[pid]["cmd"])
        self.assertNotIn(pid, self.find(match=[]))

    def test_blank_match_entries_match_nothing(self) -> None:
        pid, _ = self.holder(marker=self.marker, connect=False)
        self.assertEqual(self.find(match=["", "  "]), {})
        self.assertNotIn(pid, self.find(match=tc.parse_client_match(" , ,")))
        self.assertEqual(tc.parse_client_match(" a/cli.js, ,zed-editor ,"), ["a/cli.js", "zed-editor"])
        self.assertEqual(tc.parse_client_match(None), [])

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_a_matched_process_with_a_connection_is_reported_once_as_socket(self) -> None:
        pid, _ = self.holder(marker=self.marker)
        found = tc.find_other_clients(self.server.url, exclude_pids=set(), match=[self.marker])
        self.assertEqual([entry["reason"] for entry in found if entry["pid"] == pid], ["socket"])

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_a_connection_to_another_port_does_not_count(self) -> None:
        other = FakeServer()
        self.addCleanup(other.close)
        pid, _ = self.holder(server=other)
        self.assertNotIn(pid, self.find())
        self.assertIn(pid, self.find(other.url))

    @unittest.skipUnless(LINUX_PROC and ipv6_loopback(), "needs /proc and IPv6 loopback")
    def test_ipv6_loopback(self) -> None:
        server6 = FakeServer("::1")
        self.addCleanup(server6.close)
        pid, _ = self.holder(server=server6)
        self.assertEqual(self.find(server6.url)[pid]["reason"], "socket")
        # A loopback URL counts any loopback address on its port.
        self.assertIn(pid, self.find(f"http://127.0.0.1:{server6.port}/health"))

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_wait_until_idle_returns_when_the_connection_closes(self) -> None:
        pid, release = self.holder(marker=self.marker)
        threading.Timer(0.6, release.touch).start()
        seen: list[list[dict]] = []
        start = time.monotonic()
        idle = tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[self.marker], poll_s=0.1,
                                  timeout_s=20, on_wait=seen.append)
        self.assertTrue(idle)
        self.assertGreaterEqual(time.monotonic() - start, 0.5)
        self.assertTrue(seen and seen[0][0]["pid"] == pid and seen[0][0]["reason"] == "socket")

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_wait_until_idle_times_out_while_a_connection_stays(self) -> None:
        self.holder()
        start = time.monotonic()
        self.assertFalse(tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[], poll_s=0.1,
                                            timeout_s=0.5))
        self.assertGreaterEqual(time.monotonic() - start, 0.5)
        self.assertLess(time.monotonic() - start, 10)
        self.assertFalse(tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[], poll_s=0.1,
                                            timeout_s=0))

    def test_wait_until_idle_lets_matched_processes_without_a_connection_through(self) -> None:
        self.holder(marker=self.marker, connect=False)
        start = time.monotonic()
        self.assertTrue(tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[self.marker],
                                           poll_s=5, timeout_s=30))
        self.assertLess(time.monotonic() - start, 4)
        with self.assertRaises(ValueError):
            tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[], poll_s=0, timeout_s=1)

    @unittest.skipUnless(LINUX_PROC or tc.can_see_sockets(), "no way to see sockets here")
    def test_an_ignored_client_with_a_connection_never_blocks(self) -> None:
        # CD-R1: a proxy, gateway or web UI keeps a pooled connection open for
        # good; OCR_PHASE_CLIENT_IGNORE names it by its command line or uid.
        pid, _ = self.holder(marker=self.marker)
        self.assertEqual(self.find()[pid]["reason"], "socket")
        for ignore in ([self.marker], [f"uid:{os.getuid()}"], tc.parse_client_ignore(f" , {self.marker} ,")):
            with self.subTest(ignore=ignore):
                found = tc.find_other_clients(self.server.url, exclude_pids=set(), match=[self.marker],
                                              ignore=ignore)
                self.assertEqual([entry["reason"] for entry in found if entry["pid"] == pid], ["ignored"])
                self.assertEqual(set(found[0]), {"pid", "cmd", "reason"})
                start = time.monotonic()
                self.assertTrue(tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[], poll_s=5,
                                                   timeout_s=30, ignore=ignore))
                self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(self.find(match=[])[pid]["reason"], "socket")
        self.assertFalse(tc.wait_until_idle(self.server.url, exclude_pids=set(), match=[], poll_s=0.1,
                                            timeout_s=0, ignore=["no-such-program-anywhere", "uid:x", " "]))
        self.assertEqual(tc.parse_client_ignore("nginx, uid:33 ,"), ["nginx", "uid:33"])


class ParsingTest(unittest.TestCase):
    def test_endpoint(self) -> None:
        self.assertEqual(tc.endpoint("http://127.0.0.1:8090/v1/models"), ("127.0.0.1", 8090))
        self.assertEqual(tc.endpoint("http://localhost/health"), ("localhost", 80))
        self.assertEqual(tc.endpoint("https://[::1]/v1/models"), ("::1", 443))
        for bad in ("127.0.0.1:8090", "ftp://host/", "http:///path"):
            with self.assertRaises(ValueError):
                tc.endpoint(bad)

    @unittest.skipUnless(sys.byteorder == "little", "the sample is written in little-endian host order")
    def test_proc_net_tcp_lines(self) -> None:
        tcp = (
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 0100007F:1F9A 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 111 1\n"
            "   1: 0100007F:D2F0 0100007F:1F9A 01 00000000:00000000 00:00000000 00000000  1000        0 222 1\n"
            "   2: 0100007F:D2F2 0100007F:1F9A 06 00000000:00000000 03:00000000 00000000     0        0 0 3\n"
        )
        rows = tc.parse_proc_net_tcp(tcp)
        self.assertEqual([(str(r[0]), r[1], r[2], r[3], r[4]) for r in rows], [
            ("0.0.0.0", 0, "0A", 1000, 111),
            ("127.0.0.1", 8090, "01", 1000, 222),
            ("127.0.0.1", 8090, "06", 0, 0),
        ])
        tcp6 = (
            "  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
            "   0: 00000000000000000000000001000000:D2F0 00000000000000000000000001000000:1F9A 01 00000000:00000000 00:00000000 00000000  1000        0 333 1\n"
            "   1: 0000000000000000FFFF00000100007F:D2F1 0000000000000000FFFF00000100007F:1F9A 01 00000000:00000000 00:00000000 00000000  1001        0 444 1\n"
        )
        rows6 = tc.parse_proc_net_tcp(tcp6)
        self.assertEqual([(str(r[0]), r[1], r[4]) for r in rows6], [("::1", 8090, 333), ("::ffff:127.0.0.1", 8090, 444)])
        targets = tc._Targets("127.0.0.1", 8090)
        self.assertTrue(all(row[0] in targets for row in rows6), "v4-mapped and ::1 are loopback targets")

    def test_lsof_output(self) -> None:
        targets = tc._Targets("127.0.0.1", 8090)
        text = "p101\nn127.0.0.1:52341->127.0.0.1:8090\np102\nn127.0.0.1:8090->127.0.0.1:52341\n" \
               "p103\nn[::1]:50000->[::1]:8090\np104\nn127.0.0.1:50001->127.0.0.1:9000\np105\nn*:8090\n"
        self.assertEqual(tc.parse_lsof(text, targets), {101, 103})

    def test_ps_output_and_family(self) -> None:
        table = tc._parse_ps("  1     0 Ss   /sbin/init\n 10     1 S    bash -l\n 20    10 S+   python3 wrapper.py\n"
                             " 30    20 S    runner --x\n 40    30 Z    [dead] <defunct>\n 50     1 S    other agent\n")
        self.assertEqual(table[20], (10, "S", "python3 wrapper.py"))
        self.assertEqual(table[40][1], "Z")
        self.assertEqual(tc._family(table, 20), {1, 10, 20, 30, 40})


@unittest.skipUnless(os.name == "posix", "POSIX sockets")
class StandInTest(unittest.TestCase):
    def request(self, port: int, method: str = "GET", path: str = "/v1/models", body: bytes | None = None,
                headers: dict | None = None) -> tuple[int, dict, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    def test_every_request_gets_503_with_retry_after_and_a_json_error(self) -> None:
        with tc.StandIn("127.0.0.1", 0, retry_after_s=7) as stand_in:
            self.assertNotEqual(stand_in.port, 0)
            status, headers, body = self.request(stand_in.port)
            self.assertEqual(status, 503)
            self.assertEqual(headers["retry-after"], "7")
            self.assertEqual(headers["content-type"], "application/json")
            self.assertEqual(json.loads(body), {"error": {
                "message": "local model paused for OCR; retry after 7 seconds", "type": "server_busy"}})
            # A large upload is read to its end first, so the client gets the answer, not a reset.
            status, _, body = self.request(stand_in.port, "POST", "/v1/chat/completions", b"x" * 3_000_000,
                                           {"Content-Type": "application/json"})
            self.assertEqual((status, json.loads(body)["error"]["type"]), (503, "server_busy"))
            status, headers, body = self.request(stand_in.port, "HEAD", "/")
            self.assertEqual((status, body, headers["retry-after"]), (503, b"", "7"))
            status, _, _ = self.request(stand_in.port, "PROPFIND", "/any/path?x=1")
            self.assertEqual(status, 503)
            # An iterable body of unknown length goes out chunked.
            status, _, _ = self.request(stand_in.port, "POST", "/v1/chat/completions",
                                        iter([b"{", b"\"a\": 1", b"}"]))
            self.assertEqual(status, 503)
            # The count goes up after the reply is flushed, so the client can
            # read the last reply a moment before the handler thread counts it.
            deadline = time.monotonic() + 5
            while stand_in.requests < 5 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(stand_in.requests, 5)

    def test_the_message_never_says_timeout(self) -> None:
        # Qwen Code 0.27.3 re-wraps an error that reads like a timeout without
        # its response headers, which loses Retry-After.
        for seconds in (1, 30, 59, 600):
            error = json.loads(tc.StandIn("127.0.0.1", 0, seconds).body)["error"]
            for words in ("timeout", "timed out", "etimedout", "deadline exceeded"):
                self.assertNotIn(words, error["message"].lower())
            self.assertNotEqual(error["type"], "timeout")

    def test_concurrent_requests_are_all_answered(self) -> None:
        with tc.StandIn("127.0.0.1", 0, retry_after_s=3) as stand_in:
            results: list[int] = []
            threads = [threading.Thread(target=lambda: results.append(self.request(stand_in.port)[0]))
                       for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertEqual(results, [503] * 8)

    def test_it_refuses_a_port_that_still_has_a_listener(self) -> None:
        for listen_host in ("127.0.0.1", "0.0.0.0"):
            with self.subTest(listener=listen_host):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((listen_host, 0))
                listener.listen(4)
                port = listener.getsockname()[1]
                try:
                    with self.assertRaises(tc.PortBusy) as caught:
                        tc.StandIn("127.0.0.1", port).start()
                    self.assertEqual(caught.exception.errno, errno.EADDRINUSE)
                finally:
                    listener.close()
                with tc.StandIn("127.0.0.1", port) as stand_in:
                    self.assertEqual(self.request(stand_in.port)[0], 503)

    def test_a_second_stand_in_on_the_same_port_is_refused(self) -> None:
        with tc.StandIn("127.0.0.1", 0) as first:
            with self.assertRaises(tc.PortBusy):
                tc.StandIn("127.0.0.1", first.port).start()
            self.assertEqual(self.request(first.port)[0], 503)

    def test_close_frees_the_port_for_a_server_without_reuseaddr(self) -> None:
        stand_in = tc.StandIn("127.0.0.1", 0).start()
        port = stand_in.port
        for _ in range(3):
            self.assertEqual(self.request(port)[0], 503)
        stand_in.close()
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        # The clients closed first, so no TIME_WAIT is left on the port: a
        # server that does not set SO_REUSEADDR can bind it at once.
        deadline = time.monotonic() + 2
        while True:
            plain = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                plain.bind(("127.0.0.1", port))
                break
            except OSError as error:
                if error.errno != errno.EADDRINUSE or time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
            finally:
                plain.close()
        stand_in.close()  # a second close does nothing

    @staticmethod
    def time_waits(port: int) -> int:
        """TIME_WAIT sockets whose local port is PORT (Linux /proc/net/tcp)."""
        count = 0
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                with open(name, encoding="ascii") as handle:
                    rows = handle.read().splitlines()[1:]
            except OSError:
                continue
            for row in rows:
                fields = row.split()
                if len(fields) > 3 and int(fields[1].rsplit(":", 1)[1], 16) == port and fields[3] == "06":
                    count += 1
        return count

    def bind_plain_at_once(self, port: int) -> None:
        """A listener without SO_REUSEADDR binds PORT now (a moment for the last
        ACK on loopback, never the 60 s of a TIME_WAIT)."""
        deadline = time.monotonic() + 0.3
        while True:
            plain = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                plain.bind(("127.0.0.1", port))
                plain.listen(1)
                return
            except OSError as error:
                if error.errno != errno.EADDRINUSE or time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
            finally:
                plain.close()

    def test_close_cuts_idle_and_unfinished_connections_without_a_time_wait(self) -> None:
        # CD-R6: a client still uploading and an idle keep-alive connection when
        # the window ends.  They used to be shut down with a FIN from the
        # stand-in, which left TIME_WAIT on the port for about 60 s.
        stand_in = tc.StandIn("127.0.0.1", 0, retry_after_s=60).start()
        port = stand_in.port
        uploading = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(uploading.close)
        uploading.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: 5000000\r\n\r\n"
                          + b"x" * 10000)
        idle = socket.create_connection(("127.0.0.1", port))
        self.addCleanup(idle.close)
        time.sleep(0.3)
        started = time.monotonic()
        stand_in.close()
        self.assertLess(time.monotonic() - started, tc.CLOSE_GRACE + 3)
        # Both were reset, not closed: the clients see it.
        for client in (uploading, idle):
            client.settimeout(5)
            with self.assertRaises((ConnectionResetError, BrokenPipeError)):
                client.sendall(b"x" * 65536)
                client.recv(1)
        if sys.platform.startswith("linux"):
            self.assertEqual(self.time_waits(port), 0)
        self.bind_plain_at_once(port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reuse:
            reuse.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reuse.bind(("127.0.0.1", port))

    def test_a_request_finishing_within_the_grace_is_answered_and_the_port_is_free_after(self) -> None:
        stand_in = tc.StandIn("127.0.0.1", 0, retry_after_s=9).start()
        port = stand_in.port
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.addCleanup(client.close)
        client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: 20\r\n\r\n0123456789")
        time.sleep(0.2)
        closer = threading.Thread(target=stand_in.close)
        closer.start()
        time.sleep(0.3)
        # The window is over, but this upload ends within the grace: it gets its answer.
        client.sendall(b"0123456789")
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = client.recv(4096)
            if not chunk:
                break
            reply += chunk
        self.assertTrue(reply.startswith(b"HTTP/1.1 503"), reply)
        self.assertIn(b"Retry-After: 9", reply)
        client.close()
        closer.join(10)
        self.assertFalse(closer.is_alive())
        if sys.platform.startswith("linux"):
            self.assertEqual(self.time_waits(port), 0)
        self.bind_plain_at_once(port)

    def test_a_client_that_keeps_its_answered_connection_is_reset(self) -> None:
        with mock.patch.object(tc, "CLIENT_CLOSE_GRACE", 0.3):
            stand_in = tc.StandIn("127.0.0.1", 0).start()
            self.addCleanup(stand_in.close)
            client = socket.create_connection(("127.0.0.1", stand_in.port), timeout=10)
            self.addCleanup(client.close)
            client.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\n\r\n")
            time.sleep(1.0)  # answered, then kept open past the grace
            reply = b""
            with self.assertRaises(ConnectionResetError):
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    reply += chunk
            if sys.platform.startswith("linux"):
                self.assertEqual(self.time_waits(stand_in.port), 0)

    @unittest.skipUnless(sys.platform.startswith("linux"), "127.0.0.2 is a loopback address on Linux")
    def test_a_start_cut_short_by_a_signal_still_closes(self) -> None:
        # CD-R11: a signal between two of start()'s serving threads (a host
        # with two addresses) left a bound server whose thread never ran, and
        # close() then waited for that server forever.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        stand_in = tc.StandIn("127.0.0.1", port)
        real_start = threading.Thread.start
        started = []

        def start_once(thread):
            if started:
                raise KeyboardInterrupt  # what a signal handler raises there
            started.append(thread)
            real_start(thread)
        two = [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "127.0.0.2")]
        with mock.patch.object(tc, "_resolve", return_value=two), \
                mock.patch.object(threading.Thread, "start", start_once), self.assertRaises(KeyboardInterrupt):
            stand_in.start()
        self.assertEqual(len(started), 1)
        closer = threading.Thread(target=stand_in.close, daemon=True)
        closer.start()
        closer.join(10)
        self.assertFalse(closer.is_alive(), "close() hung on a server whose thread never started")
        self.assertEqual(stand_in._servers, [])
        for host in ("127.0.0.1", "127.0.0.2"):
            with socket.socket() as plain:
                plain.bind((host, port))

    def test_close_does_not_wait_for_an_idle_connection(self) -> None:
        stand_in = tc.StandIn("127.0.0.1", 0).start()
        idle = socket.create_connection(("127.0.0.1", stand_in.port))
        self.addCleanup(idle.close)
        time.sleep(0.1)
        start = time.monotonic()
        stand_in.close()
        self.assertLess(time.monotonic() - start, tc.CLOSE_GRACE + 3)

    def test_retry_after_must_be_whole_seconds_from_1(self) -> None:
        for bad in (0, -1, 1.5, True, 86401):
            with self.assertRaises(ValueError):
                tc.StandIn("127.0.0.1", 0, bad)
        self.assertEqual(tc.StandIn("127.0.0.1", 0).retry_after_s, 30)


if __name__ == "__main__":
    unittest.main()
