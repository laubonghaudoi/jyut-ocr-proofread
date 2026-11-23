#!/usr/bin/env python3
"""Async HTTP reverse proxy / least-inflight router for proofread model backends.

Routes OpenAI-compatible chat completion requests across a pool of healthy
backend servers (for example multi-node SGLang instances behind Slurm),
periodically probes health, dynamically reloads a backends list file, retries
pre-response connection/upstream errors on another backend, and exposes a JSON
``/status`` endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence
import urllib.parse


@dataclass
class BackendState:
    url: str
    host: str
    port: int
    healthy: bool = False
    inflight: int = 0
    total_ok: int = 0
    total_err: int = 0
    consecutive_err: int = 0
    last_latency: float = 0.0
    last_check: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "host": self.host,
            "port": self.port,
            "healthy": self.healthy,
            "inflight": self.inflight,
            "totalOk": self.total_ok,
            "totalErr": self.total_err,
            "consecutiveErr": self.consecutive_err,
            "lastLatency": round(self.last_latency, 4),
        }


def parse_backend_url(raw: str) -> tuple[str, str, int]:
    text = raw.strip()
    if not text:
        raise ValueError("empty backend URL")
    if "://" not in text:
        text = "http://" + text
    parsed = urllib.parse.urlsplit(text)
    if not parsed.hostname:
        raise ValueError(f"invalid backend URL: {raw!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    canonical = f"{parsed.scheme}://{parsed.hostname}:{port}"
    return canonical, parsed.hostname, port


def load_backends_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            urls.append(line)
    return urls


async def probe_http(host: str, port: int, path: str, timeout: float = 3.0) -> tuple[bool, float]:
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False, time.monotonic() - started
    try:
        req = f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode("ascii")
        writer.write(req)
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        parts = status_line.decode("latin1", errors="replace").split()
        code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
        return 200 <= code < 300, time.monotonic() - started
    except (OSError, asyncio.TimeoutError, ValueError):
        return False, time.monotonic() - started
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def _read_http_request(reader: asyncio.StreamReader) -> tuple[bytes, dict[str, str], bytes] | None:
    head = bytearray()
    while True:
        line = await reader.readline()
        if not line:
            return None
        head.extend(line)
        if line in (b"\r\n", b"\n"):
            break
        if len(head) > 1024 * 1024:
            raise ValueError("HTTP request header too large")

    lines = bytes(head).split(b"\r\n")
    request_line = lines[0]
    headers: dict[str, str] = {}
    for raw_line in lines[1:]:
        if b":" not in raw_line:
            continue
        k, v = raw_line.split(b":", 1)
        headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()

    body = b""
    if "content-length" in headers:
        length = int(headers["content-length"])
        if length > 0:
            body = await reader.readexactly(length)
    elif headers.get("transfer-encoding", "").lower() == "chunked":
        chunks = bytearray()
        while True:
            size_line = await reader.readline()
            size_hex = size_line.split(b";", 1)[0].strip()
            size = int(size_hex, 16)
            if size == 0:
                await reader.readline()
                break
            chunks.extend(await reader.readexactly(size))
            await reader.readexactly(2)
        body = bytes(chunks)
    return request_line, headers, body


class FleetRouter:
    """Async least-inflight HTTP router across dynamic backend servers."""

    def __init__(
        self,
        backends: Sequence[str] = (),
        *,
        backends_file: Path | None = None,
        health_path: str = "/v1/models",
        health_interval: float = 5.0,
        request_timeout: float = 1200.0,
        max_retries: int = 3,
    ) -> None:
        self.backends_file = backends_file
        self.health_path = health_path if health_path.startswith("/") else "/" + health_path
        self.health_interval = max(0.2, float(health_interval))
        self.request_timeout = float(request_timeout)
        self.max_retries = max(1, int(max_retries))
        self.backends: dict[str, BackendState] = {}
        self.started_at = time.monotonic()
        self.total_requests = 0
        self._rr = 0
        self._stop_event: asyncio.Event | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._proxy_server: asyncio.AbstractServer | None = None
        self._status_server: asyncio.AbstractServer | None = None
        self.update_backends(backends)

    def update_backends(self, urls: Sequence[str]) -> None:
        parsed: dict[str, tuple[str, int]] = {}
        for raw in urls:
            canonical, host, port = parse_backend_url(raw)
            parsed[canonical] = (host, port)
        for existing in list(self.backends):
            if existing not in parsed:
                del self.backends[existing]
        for canonical, (host, port) in parsed.items():
            if canonical not in self.backends:
                self.backends[canonical] = BackendState(url=canonical, host=host, port=port)

    async def refresh_health(self) -> None:
        if self.backends_file is not None:
            file_urls = load_backends_file(self.backends_file)
            if file_urls:
                self.update_backends(file_urls)
        states = list(self.backends.values())
        if not states:
            return
        results = await asyncio.gather(
            *(probe_http(b.host, b.port, self.health_path) for b in states),
            return_exceptions=True,
        )
        now = time.monotonic()
        for state, res in zip(states, results):
            state.last_check = now
            if isinstance(res, tuple) and res[0]:
                state.healthy = True
                state.consecutive_err = 0
                state.last_latency = res[1]
            else:
                state.healthy = False

    def pick_backend(self, exclude: set[str] | None = None) -> BackendState | None:
        excluded = exclude or set()
        candidates = [b for b in self.backends.values() if b.healthy and b.url not in excluded]
        if not candidates:
            # Fallback to any unexcluded backend if none have passed health check yet
            candidates = [b for b in self.backends.values() if b.url not in excluded]
        if not candidates:
            return None
        candidates.sort(key=lambda b: (b.inflight, b.consecutive_err, b.last_latency))
        best_load = candidates[0].inflight
        tied = [b for b in candidates if b.inflight == best_load]
        chosen = tied[self._rr % len(tied)]
        self._rr += 1
        return chosen

    def status_snapshot(self) -> dict[str, Any]:
        backends = [b.to_dict() for b in sorted(self.backends.values(), key=lambda x: x.url)]
        return {
            "uptimeSeconds": round(time.monotonic() - self.started_at, 2),
            "totalRequests": self.total_requests,
            "healthyBackends": sum(1 for b in backends if b["healthy"]),
            "totalBackends": len(backends),
            "backends": backends,
        }

    async def _forward_once(
        self,
        backend: BackendState,
        request_line: bytes,
        headers: dict[str, str],
        body: bytes,
        client_writer: asyncio.StreamWriter,
    ) -> bool:
        """Forward request to BACKEND. Returns True if response started flowing to client."""
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(backend.host, backend.port), timeout=10.0
        )
        sent_to_client = False
        try:
            out = bytearray(request_line + b"\r\n")
            skip_headers = {"connection", "proxy-connection", "keep-alive", "transfer-encoding", "content-length"}
            for k, v in headers.items():
                if k in skip_headers:
                    continue
                if k == "host":
                    out.extend(f"Host: {backend.host}:{backend.port}\r\n".encode("latin1"))
                else:
                    out.extend(f"{k}: {v}\r\n".encode("latin1"))
            if "host" not in headers:
                out.extend(f"Host: {backend.host}:{backend.port}\r\n".encode("latin1"))
            if body or "content-length" in headers or headers.get("transfer-encoding", "").lower() == "chunked":
                out.extend(f"Content-Length: {len(body)}\r\n".encode("latin1"))
            out.extend(b"Connection: close\r\n\r\n")
            if body:
                out.extend(body)
            up_writer.write(bytes(out))
            await up_writer.drain()

            resp_head = bytearray()
            while True:
                line = await asyncio.wait_for(up_reader.readline(), timeout=self.request_timeout)
                if not line:
                    raise EOFError("upstream closed before sending response headers")
                resp_head.extend(line)
                if line in (b"\r\n", b"\n"):
                    break
            client_writer.write(bytes(resp_head))
            await client_writer.drain()
            sent_to_client = True

            while True:
                chunk = await asyncio.wait_for(up_reader.read(65536), timeout=self.request_timeout)
                if not chunk:
                    break
                client_writer.write(chunk)
                await client_writer.drain()
            return True
        finally:
            up_writer.close()
            try:
                await up_writer.wait_closed()
            except OSError:
                pass

    async def handle_proxy_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            parsed = await _read_http_request(reader)
            if parsed is None:
                return
            request_line, headers, body = parsed
            self.total_requests += 1
            tried: set[str] = set()
            for _ in range(max(self.max_retries, len(self.backends))):
                backend = self.pick_backend(exclude=tried)
                if backend is None:
                    break
                tried.add(backend.url)
                started = time.monotonic()
                backend.inflight += 1
                try:
                    await self._forward_once(backend, request_line, headers, body, writer)
                    backend.total_ok += 1
                    backend.consecutive_err = 0
                    backend.healthy = True
                    backend.last_latency = time.monotonic() - started
                    return
                except (OSError, asyncio.TimeoutError, EOFError):
                    backend.total_err += 1
                    backend.consecutive_err += 1
                    backend.healthy = False
                finally:
                    backend.inflight = max(0, backend.inflight - 1)
            msg = b'{"error": "no healthy upstream backend available"}\n'
            resp = (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(msg)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + msg
            )
            writer.write(resp)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def handle_status_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await _read_http_request(reader)
            payload = (json.dumps(self.status_snapshot(), indent=2) + "\n").encode("utf-8")
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode("ascii")
                + payload
            )
            writer.write(resp)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError, ValueError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def start(
        self, host: str = "127.0.0.1", port: int = 8080, status_port: int = 0
    ) -> tuple[int, int]:
        await self.refresh_health()
        self._proxy_server = await asyncio.start_server(self.handle_proxy_client, host, port)
        actual_port = int(self._proxy_server.sockets[0].getsockname()[1])
        actual_status_port = 0
        if status_port >= 0:
            self._status_server = await asyncio.start_server(
                self.handle_status_client, host, status_port
            )
            actual_status_port = int(self._status_server.sockets[0].getsockname()[1])

        self._stop_event = asyncio.Event()

        async def _health_loop() -> None:
            assert self._stop_event is not None
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.health_interval)
                except asyncio.TimeoutError:
                    await self.refresh_health()

        self._health_task = asyncio.create_task(_health_loop())
        return actual_port, actual_status_port

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._health_task is not None:
            await self._health_task
        for srv in (self._proxy_server, self._status_server):
            if srv is not None:
                srv.close()
                await srv.wait_closed()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", default="0.0.0.0", help="listen address (default 0.0.0.0)")
    p.add_argument("--port", type=int, default=8080, help="proxy listen port (default 8080)")
    p.add_argument("--status-port", type=int, default=8081, help="JSON /status port (0 to disable, default 8081)")
    p.add_argument("--backends", default="", help="comma-separated upstream base URLs")
    p.add_argument("--backends-file", type=Path, help="file of upstream URLs reloaded on each health check")
    p.add_argument("--health-path", default="/v1/models", help="GET path for backend health checks")
    p.add_argument("--health-interval", type=float, default=5.0, help="seconds between health probes")
    p.add_argument("--request-timeout", type=float, default=1200.0, help="upstream timeout per request")
    return p


async def _serve_forever(args: argparse.Namespace) -> None:
    urls = [u.strip() for u in (args.backends or "").split(",") if u.strip()]
    router = FleetRouter(
        urls,
        backends_file=args.backends_file,
        health_path=args.health_path,
        health_interval=args.health_interval,
        request_timeout=args.request_timeout,
    )
    status_port = -1 if args.status_port <= 0 else args.status_port
    actual_port, actual_status = await router.start(args.host, args.port, status_port=status_port)
    print(
        f"[fleet-router] listening on {args.host}:{actual_port} "
        f"(status={actual_status or 'disabled'}, backends={len(router.backends)})",
        flush=True,
    )
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await router.stop()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(_serve_forever(args))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
