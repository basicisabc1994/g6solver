# SPDX-License-Identifier: Apache-2.0
"""V7's agent-phase network rule (PROTOCOL.md §4): an HTTPS proxy that lets through only the hosts it is given.

The agent's container sits on an internal Docker network with no route out; this proxy runs in a sidecar on that
network and the default one, so the only way out is a CONNECT to an allowed ``host:port`` (the model endpoint). Every
request, allowed or refused, is one JSON line in the log, which the gaming check reads. Standard library only: it runs
under the standalone Python copied into the sidecar.

    python -I egress_proxy.py --port 3128 --allow api.anthropic.com:443 --log /opt/v7/egress.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

MAX_HEAD = 16384


def decide(head: bytes, allow: frozenset[tuple[str, int]]) -> tuple[bool, str, int, str]:
    """(allowed, host, port, method) for a request head. Only CONNECT to an allowed host and port passes."""
    line = head.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    parts = line.split()
    if len(parts) != 3:
        return False, "", 0, "malformed"
    method, target = parts[0].upper(), parts[1]
    if method != "CONNECT":
        host = target.split("://", 1)[-1].split("/", 1)[0]
        return False, host.rsplit(":", 1)[0].lower(), 0, method
    host, _, port = target.rpartition(":")
    if not host or not port.isdigit():
        return False, target.lower(), 0, method
    host = host.strip("[]").lower()
    return (host, int(port)) in allow, host, int(port), method


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


def make_handler(allow: frozenset[tuple[str, int]], log_path: str):
    def log(entry: dict) -> None:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 3), **entry}) + "\n")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        allowed, host, port, method = decide(head[:MAX_HEAD], allow)
        log({"method": method, "host": host, "port": port, "allowed": allowed})
        if not allowed:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=30)
        except (OSError, asyncio.TimeoutError) as exc:
            log({"method": method, "host": host, "port": port, "error": type(exc).__name__})
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    return handle


def parse_allow(items: list[str]) -> frozenset[tuple[str, int]]:
    out = set()
    for item in items:
        host, _, port = item.rpartition(":")
        if not host or not port.isdigit():
            raise SystemExit(f"--allow takes host:port, got {item!r}")
        out.add((host.lower(), int(port)))
    return frozenset(out)


async def serve(port: int, allow: frozenset[tuple[str, int]], log_path: str, host: str = "0.0.0.0") -> None:
    server = await asyncio.start_server(make_handler(allow, log_path), host, port, limit=MAX_HEAD)
    async with server:
        await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=3128)
    ap.add_argument("--allow", action="append", default=[], help="host:port, repeatable")
    ap.add_argument("--log", required=True)
    args = ap.parse_args()
    asyncio.run(serve(args.port, parse_allow(args.allow), args.log))


if __name__ == "__main__":
    main()
