# SPDX-License-Identifier: Apache-2.0
"""G6's stdio MCP server, whose writer G6 sees: a reply is counted (its retry, its repair rows) only once its line was
written to stdout and flushed without error (``g6.mcp.consent_transport.Witness``, evidence "written").

The SDK's stdio transport (``mcp.server.stdio.stdio_server``) takes the stdout it writes to; G6 passes one that tells
the witness each line written and flushed. FastMCP's ``run_stdio_async`` opens that transport with no stdout, so
``serve`` is its body (fastmcp 2.14) less the banner and log level, over this ``stdio_server``.
``tests/core/test_mcp_delivery_review2.py`` fails if either changes shape.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from io import TextIOWrapper

import anyio

from g6.mcp.consent_transport import unwitness, witness


class WrittenStdout:
    """stdout as the SDK's stdio writer uses it (``write`` a line, then ``flush``). Each line G6 sent carries its own
    send's token (``consent_transport.Tokened``): once its write took the whole line and the flush returned, that
    token is ``written`` to the witness; a write that took less, or a write or flush that raises, discards that token
    without firing it. A write that returns no count (``None``, as some file objects do) raised nothing and is taken
    as having accepted the line. A line with no token (a reply G6 withheld in another's place) tells it nothing."""

    def __init__(self, inner, seen) -> None:
        self.inner, self.seen, self._pending = inner, seen, None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def write(self, data):
        key, self._pending = getattr(data, "token", None), None
        try:
            written = await self.inner.write(data)
        except BaseException:
            self.seen.discard(key)
            raise
        if written is None or isinstance(written, int) and written == len(data):
            self._pending = key
        else:
            self.seen.discard(key)                  # a short write: not the whole line, never counted
        return written

    async def flush(self):
        key, self._pending = self._pending, None
        try:
            await self.inner.flush()
        except BaseException:
            self.seen.discard(key)
            raise
        self.seen.written(key)


@asynccontextmanager
async def stdio_server(stdin=None, stdout=None):
    """The SDK's stdio transport over ``stdout`` (the process's own, as the SDK opens it, by default), its writes
    seen by the witness of the send stream it yields."""
    from mcp.server.stdio import stdio_server as sdk_stdio_server

    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
    out = WrittenStdout(stdout, None)
    async with sdk_stdio_server(stdin=stdin, stdout=out) as (read_stream, write_stream):
        out.seen = witness(write_stream)
        try:
            yield read_stream, write_stream
        finally:
            unwitness(write_stream)        # the connection ended: a reply never written is never counted


async def serve(server) -> None:
    """Run a FastMCP ``server`` over G6's ``stdio_server``, as FastMCP's ``run_stdio_async`` does."""
    from mcp.server.lowlevel.server import NotificationOptions

    async with server._lifespan_manager():
        async with stdio_server() as (read_stream, write_stream):
            await server._mcp_server.run(read_stream, write_stream, server._mcp_server.create_initialization_options(
                notification_options=NotificationOptions(tools_changed=True)))


__all__ = ["WrittenStdout", "serve", "stdio_server"]
