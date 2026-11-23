#!/usr/bin/env python3
"""Raw TCP stream pipe between cluster nodes (e.g., tar over direct node Ethernet).

Usage:
    # Receiver node:
    python3 cluster/tcp_pipe.py recv PORT > out.tar
    # Sender node:
    python3 cluster/tcp_pipe.py send DST_HOST PORT < in.tar
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import sys
import time
from typing import BinaryIO, Sequence


DEFAULT_CHUNK_BYTES = 1024 * 1024


def recv_stream(
    port: int,
    out_stream: BinaryIO,
    *,
    host: str = "0.0.0.0",
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> tuple[int, str, float]:
    """Listen on HOST:PORT, accept one connection, write bytes to OUT_STREAM."""
    started = time.monotonic()
    total = 0
    digest = hashlib.sha256()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        conn, addr = srv.accept()
        with conn:
            print(f"[tcp-pipe] accepted {addr[0]}:{addr[1]}", file=sys.stderr, flush=True)
            while True:
                chunk = conn.recv(chunk_bytes)
                if not chunk:
                    break
                out_stream.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            out_stream.flush()
    elapsed = max(1e-6, time.monotonic() - started)
    return total, digest.hexdigest(), elapsed


def send_stream(
    host: str,
    port: int,
    in_stream: BinaryIO,
    *,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    retries: int = 30,
    retry_delay: float = 0.5,
) -> tuple[int, str, float]:
    """Connect to HOST:PORT (with retries) and stream IN_STREAM until EOF."""
    last_err: Exception | None = None
    sock: socket.socket | None = None
    for _ in range(max(1, retries)):
        try:
            sock = socket.create_connection((host, port), timeout=15.0)
            sock.settimeout(None)
            break
        except OSError as error:
            last_err = error
            time.sleep(retry_delay)
    if sock is None:
        raise OSError(f"could not connect to {host}:{port}: {last_err}")

    started = time.monotonic()
    total = 0
    digest = hashlib.sha256()
    with sock:
        while True:
            chunk = in_stream.read(chunk_bytes)
            if not chunk:
                break
            sock.sendall(chunk)
            digest.update(chunk)
            total += len(chunk)
        sock.shutdown(socket.SHUT_WR)
    elapsed = max(1e-6, time.monotonic() - started)
    return total, digest.hexdigest(), elapsed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)
    p_recv = sub.add_parser("recv", help="listen on PORT and write incoming stream to stdout")
    p_recv.add_argument("port", type=int)
    p_recv.add_argument("--host", default="0.0.0.0")
    p_recv.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)

    p_send = sub.add_parser("send", help="connect to HOST PORT and stream stdin")
    p_send.add_argument("host")
    p_send.add_argument("port", type=int)
    p_send.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "recv":
        total, sha, elapsed = recv_stream(
            args.port, sys.stdout.buffer, host=args.host, chunk_bytes=args.chunk_bytes
        )
    else:
        total, sha, elapsed = send_stream(
            args.host, args.port, sys.stdin.buffer, chunk_bytes=args.chunk_bytes
        )
    mb = total / (1024 * 1024)
    print(
        f"[tcp-pipe] {args.mode} done: {mb:.2f} MiB in {elapsed:.2f}s "
        f"({mb / elapsed:.1f} MiB/s) sha256={sha[:16]}",
        file=sys.stderr,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
