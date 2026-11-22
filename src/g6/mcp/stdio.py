# SPDX-License-Identifier: Apache-2.0
"""G6's stdio MCP server, whose writer G6 owns: a reply is counted (its retry, its repair rows) only once its whole
line was written to stdout and flushed without error (``g6.mcp.consent_transport.Witness``, evidence "written").

The SDK's stdio transport (``mcp.server.stdio.stdio_server``) parses stdin's lines, which G6's own daemon thread
reads, so an ended connection never waits on a blocked read; its writer is never given a message.
G6's own writer takes each outgoing message from G6's own stream, serialises it once, writes the whole line (a short
write is continued, never dropped), flushes, and only then fires that message's own callback. A write or flush error
ends the connection, and with it the connection's witness: nothing for that message or after it counts. FastMCP's
``run_stdio_async`` opens the SDK transport with no stdout, so ``serve`` is its body (fastmcp 2.14) less the banner and
log level, over this ``stdio_server``. ``tests/core/test_mcp_delivery_review2.py`` fails if either changes shape.
"""
from __future__ import annotations

import os
import sys
import threading
from contextlib import asynccontextmanager, suppress
from io import TextIOWrapper, UnsupportedOperation

import anyio
import anyio.lowlevel
from mcp.shared.message import SessionMessage

from g6.mcp.consent_transport import Witness, calls_on, unwitness, witness


class _Unused:
    """The stdout handed to the SDK's transport, whose writer G6 never gives a message."""

    async def write(self, data):
        raise RuntimeError("G6's own stdio writer writes every message")

    async def flush(self):
        raise RuntimeError("G6's own stdio writer writes every message")


async def _write_line(stdout, line: str) -> None:
    """Write all of ``line``, continuing a short write. A write that takes nothing, or that does not say how much it
    took (no count: G6's writer cannot know the line was taken), ends the connection."""
    while line:
        taken = await stdout.write(line)
        if type(taken) is not int or taken <= 0:
            raise OSError("stdout did not say it accepted any of the line")
        line = line[taken:]


async def _writer(messages, stdout, stream, seen: Witness, connection: anyio.CancelScope) -> None:
    """G6's stdio writer: each message from ``messages``, its whole line written and flushed, then its callback."""
    try:
        async with messages:
            async for message in messages:
                if not isinstance(message, SessionMessage):
                    continue
                seen.take(message)
                await _write_line(stdout, message.message.model_dump_json(by_alias=True, exclude_none=True) + "\n")
                await stdout.flush()
                seen.written(message)
    except Exception:  # noqa: BLE001 - a write or flush error ends the connection; nothing after it counts
        unwitness(stream)              # ended before it is cancelled: no tool body starts after this
        connection.cancel()
    finally:
        unwitness(stream)
        connection.cancel()              # writer EOF also ends the parser, even while stdin stays open


def _raw_lines(fd, stopped):
    """Frame raw input with the text reader's LF, CRLF and CR line endings, including an unterminated final line.

    Keep partial UTF-8 bytes until the line is complete. Like the old readline(), there is no line-size cap. Only
    os.read waits on the client: no Python buffered stream lock can be held during that wait, on Windows or POSIX.
    """
    pending = bytearray()
    scan = 0
    while not stopped.is_set():
        chunk = os.read(fd, 65536)
        if not chunk:
            if pending:
                yield bytes(pending)
            return
        pending.extend(chunk)
        start = 0
        while scan < len(pending):
            byte = pending[scan]
            if byte == 13 and scan + 1 == len(pending):
                break                   # a CR at the chunk boundary may belong to a CRLF
            scan += 1
            if byte in (10, 13):
                if byte == 13 and pending[scan] == 10:
                    scan += 1
                yield bytes(pending[start:scan])
                start = scan
        if start:
            del pending[:start]
            scan -= start


def _read_lines(fd, lines, token, stopped) -> None:
    """Hand each raw line to the SDK parser without ever reading a Python stream. A blocked raw read may outlive
    the connection, but the daemon holds no buffered reader lock, so normal interpreter shutdown can finish."""
    try:
        for raw in _raw_lines(fd, stopped):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw              # the SDK returns its UTF-8/JSON ValidationError; never replace bad bytes
            anyio.from_thread.run(lines.send, line, token=token)
    except BaseException:  # noqa: BLE001 - a read error, or the connection or its event loop ended: input ends here
        pass
    finally:
        os.close(fd)                    # only this thread closes its raw descriptor, after any blocked read ends
        with suppress(BaseException):
            anyio.from_thread.run_sync(lines.close, token=token)


def _read_file_lines(readline, lines, token, stopped) -> None:
    """Keep descriptor-less files on their own readline interface without waiting for a blocked read at teardown."""
    try:
        while not stopped.is_set() and (line := readline()):
            anyio.from_thread.run(lines.send, line, token=token)
    except BaseException:  # noqa: BLE001 - a read error, or an ended connection or event loop: input ends here
        pass
    finally:
        with suppress(BaseException):
            anyio.from_thread.run_sync(lines.close, token=token)


@asynccontextmanager
async def _lines(stdin):
    """Use a raw duplicate for files with descriptors, their own readline otherwise. Keep other async iterables."""
    if stdin is None:
        source = sys.stdin
    elif isinstance(stdin, anyio.AsyncFile):
        source = stdin.wrapped
    else:
        yield stdin
        return
    fd = None
    fileno = getattr(source, "fileno", None)
    if callable(fileno):
        try:
            fd = fileno()
        except (UnsupportedOperation, OSError, ValueError):
            pass
    if fd is None:
        sender, receiver = anyio.create_memory_object_stream(0)
        stopped = threading.Event()
        threading.Thread(target=_read_file_lines,
                         args=(source.readline, sender, anyio.lowlevel.current_token(), stopped),
                         name="g6-stdin-reader", daemon=True).start()
        try:
            with receiver:
                yield receiver
        finally:
            stopped.set()
        return
    # Windows' CRT also locks a descriptor during os.read. Give the thread its own descriptor so finalization of
    # the caller's file cannot wait on that CRT lock. No Python file object ever owns this duplicate.
    fd = os.dup(fd)
    sender, receiver = anyio.create_memory_object_stream(0)
    stopped = threading.Event()
    threading.Thread(target=_read_lines, args=(fd, sender, anyio.lowlevel.current_token(), stopped),
                     name="g6-stdin-reader", daemon=True).start()
    try:
        with receiver:
            yield receiver
    finally:
        stopped.set()                    # never join a reader blocked on outside input


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None):
    """Stdio over ``stdin`` and ``stdout`` (the process's own by default): G6's stdin reader under the SDK's parser,
    and G6's writer. A write or flush error ends the whole connection: no tool body starts after it, and the reader
    and the server running over it are cancelled."""
    from mcp.server.stdio import stdio_server as sdk_stdio_server

    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    write_stream, messages = anyio.create_memory_object_stream(0)
    seen = witness(write_stream)
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_writer, messages, stdout, write_stream, seen, tg.cancel_scope)
            async with _lines(stdin) as lines, sdk_stdio_server(stdin=lines, stdout=_Unused()) as (read_stream,
                                                                                                unused_stream):
                await unused_stream.aclose()           # the SDK's writer ends at once: it never writes
                with calls_on(seen):
                    yield read_stream, write_stream
    finally:
        unwitness(write_stream)            # the connection ended: a reply never written is never counted


async def serve(server) -> None:
    """Run a FastMCP ``server`` over G6's ``stdio_server``, as FastMCP's ``run_stdio_async`` does."""
    from mcp.server.lowlevel.server import NotificationOptions

    async with server._lifespan_manager():
        async with stdio_server() as (read_stream, write_stream):
            await server._mcp_server.run(read_stream, write_stream, server._mcp_server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)))


__all__ = ["serve", "stdio_server"]
