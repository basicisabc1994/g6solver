# SPDX-License-Identifier: Apache-2.0
"""G6's stdio MCP server, whose writer G6 owns: a reply is counted (its retry, its repair rows) only once its whole
line was written to stdout and flushed without error (``g6.mcp.consent_transport.Witness``, evidence "written").

The SDK's stdio transport (``mcp.server.stdio.stdio_server``) reads stdin; its writer is never given a message.
G6's own writer takes each outgoing message from G6's own stream, serialises it once, writes the whole line (a short
write is continued, never dropped), flushes, and only then fires that message's own callback. A write or flush error
ends the connection, and with it the connection's witness: nothing for that message or after it counts. FastMCP's
``run_stdio_async`` opens the SDK transport with no stdout, so ``serve`` is its body (fastmcp 2.14) less the banner and
log level, over this ``stdio_server``. ``tests/core/test_mcp_delivery_review2.py`` fails if either changes shape.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from io import TextIOWrapper

import anyio
from mcp.shared.message import SessionMessage

from g6.mcp.consent_transport import Witness, unwitness, witness


class _Unused:
    """The stdout handed to the SDK's transport, whose writer G6 never gives a message."""

    async def write(self, data):
        raise RuntimeError("G6's own stdio writer writes every message")

    async def flush(self):
        raise RuntimeError("G6's own stdio writer writes every message")


async def _write_line(stdout, line: str) -> None:
    """Write all of ``line``, continuing a short write; a write that takes nothing ends the connection."""
    while line:
        taken = await stdout.write(line)
        if taken is None:
            return                                     # a file object that returns no count took it all or raised
        if not isinstance(taken, int) or taken <= 0:
            raise OSError("stdout accepted none of the line")
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
        connection.cancel()
    finally:
        unwitness(stream)


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None):
    """Stdio over ``stdin`` and ``stdout`` (the process's own by default): the SDK's reader, G6's writer. A write or
    flush error cancels the whole connection, the SDK's reader and the server running over it included."""
    from mcp.server.stdio import stdio_server as sdk_stdio_server

    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    write_stream, messages = anyio.create_memory_object_stream(0)
    seen = witness(write_stream)
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_writer, messages, stdout, write_stream, seen, tg.cancel_scope)
            async with sdk_stdio_server(stdin=stdin, stdout=_Unused()) as (read_stream, unused_stream):
                await unused_stream.aclose()           # the SDK's writer ends at once: it never writes
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
