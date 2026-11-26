# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/wire-consistency-fu: closing a connection releases its unread delivery callbacks, never firing them; no registry outlives its connection."""
import asyncio
from contextlib import suppress

import anyio
import pytest
from fastmcp import Context, FastMCP
from mcp import types

from g6.mcp import consent_transport as transport
from g6.mcp import repair
from g6.mcp.cloud_consent import install, public_tool


def test_disconnect_releases_buffered_reply_callback(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)

    async def run():
        server = FastMCP("delivery-lifecycle-review")
        install(server)
        logging_started = asyncio.Event()
        held = {}

        @server.tool
        @public_tool
        async def probe(ctx: Context) -> dict:
            held["stream"] = ctx.session._write_stream.inner
            held["seen"] = transport._WITNESSES[held["stream"]]
            # Program text; the real client logging callback pauses its reader.
            with transport.consent_form():
                await ctx.info("pause client reader")
            return {"ok": False, "error": "[G6_E_INPUT_VALIDATION] probe"}

        async def logging_callback(params):
            logging_started.set()
            await asyncio.Event().wait()

        link = transport.G6MemoryTransport(server)
        async with link.connect_session(
            logging_callback=logging_callback,
            client_info=types.Implementation(name="codex", version="review"),
        ) as client:
            await client.initialize()
            pending = asyncio.create_task(client.call_tool("probe", {}))
            try:
                with anyio.fail_after(5):
                    await logging_started.wait()
                    while (
                        not held["seen"]._expected
                        or held["stream"].statistics().current_buffer_used != 1
                    ):
                        await anyio.lowlevel.checkpoint()
                seen = held["seen"]
                keys = set(seen._expected)
                assert not seen._taken  # The reply is buffered, never read.
            finally:
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
        return seen, keys

    seen, keys = asyncio.run(run())
    try:
        assert rows == [], "The unread reply must not produce delivery evidence"
        # The registry is the connection's own witness (no module-level map): after the connection closed it holds
        # none of that connection's callbacks or taken marks.
        assert not hasattr(transport, "_OWNERS"), "a module-level registry of delivery callbacks"
        leaked = keys.intersection(seen._expected)
        assert not leaked and not seen._taken, (
            f"closed connection retained {len(leaked)} delivery callback(s)"
        )
    finally:
        for key in keys:
            seen.discard(key)


@pytest.mark.parametrize("ending", ["the write fails", "the connection is cancelled"])
def test_closing_g6s_stdio_server_discards_an_unwritten_replys_callback(monkeypatch, ending):
    """The stdio connection ends while a refusal's write has not finished. G6's writer is part of the connection, so
    the connection closes only once that write ended; when it ends without the whole line written and flushed (the
    write fails, or the connection is cancelled), its callback goes, unfired, with the connection's witness."""
    from types import SimpleNamespace

    from fastmcp.tools.tool import ToolResult
    from mcp.shared.message import SessionMessage

    from g6.mcp.stdio import stdio_server

    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = SimpleNamespace(client_params=SimpleNamespace(clientInfo=types.Implementation(name="codex", version="r")))

    class EmptyInput:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Output:
            async def write(self, data):
                started.set()
                await release.wait()                  # the line is still being written as the connection closes
                raise OSError("the pipe closed")

            async def flush(self):
                raise AssertionError("write failed before flush")

        reply, record = repair.observed("g6_invoke", {"a": 1}, {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] x"},
                                        session)
        body = transport.call_body(ToolResult(content=reply))
        ticket = transport.Ticket((), "openai:codex", frozenset(), transport.digest(body), delivered=record)
        packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=1, result=body)))
        with anyio.fail_after(5), anyio.CancelScope() as connection:
            async with stdio_server(stdin=EmptyInput(), stdout=Output()) as (_, outbound):
                seen = transport._WITNESSES[outbound]
                token = transport._OUTBOUND.set(ticket)
                try:
                    await transport._Stream(outbound, session).send(packet)
                finally:
                    transport._OUTBOUND.reset(token)
                await started.wait()
                assert seen._expected and seen._taken         # taken by the writer, not yet written
                await outbound.aclose()
                if ending == "the connection is cancelled":
                    connection.cancel()
                else:
                    # the connection waits for G6's writer as it closes; the write then fails
                    asyncio.get_running_loop().call_later(0.2, release.set)
        return seen, outbound

    seen, outbound = asyncio.run(run())
    assert not seen._expected and not seen._taken and rows == [], (seen._expected, rows)
    assert outbound not in transport._WITNESSES