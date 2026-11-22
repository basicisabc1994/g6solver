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

import sys
import threading
from contextlib import asynccontextmanager, suppress
from io import TextIOWrapper

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


def _read_lines(readline, lines, token) -> None:
    """stdin's read loop, in G6's daemon thread: each line handed to ``lines`` on the event loop, then the end of
    input. Python cannot interrupt a blocked read, so the connection never waits on this thread: once the connection
    ended, the thread ends at its next line or end of input, and as a daemon it never holds the process open."""
    try:
        while line := readline():
            anyio.from_thread.run(lines.send, line, token=token)
    except BaseException:  # noqa: BLE001 - a read error, or the connection or its event loop ended: input ends here
        pass
    finally:
        with suppress(BaseException):
            anyio.from_thread.run_sync(lines.close, token=token)


@asynccontextmanager
async def _lines(stdin):
    """``stdin`` as the SDK's reader iterates it. A file (the process's stdin by default, or one wrapped by
    ``anyio.wrap_file``) is read by G6's own daemon thread (``_read_lines``), so a connection that ends stops reading
    at once, the client's stdin still open. Any other async iterable already reads on the event loop: it is kept."""
    if stdin is None:
        source = TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    elif isinstance(stdin, anyio.AsyncFile):
        source = stdin.wrapped
    else:
        yield stdin
        return
    sender, receiver = anyio.create_memory_object_stream(0)
    threading.Thread(target=_read_lines, args=(source.readline, sender, anyio.lowlevel.current_token()),
                     name="g6-stdin-reader", daemon=True).start()
    with receiver:
        yield receiver


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
