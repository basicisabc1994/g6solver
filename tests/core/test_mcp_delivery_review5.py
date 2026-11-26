# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/wire-consistency-fu: a send cancelled after the writer took its message is still counted once that message is wholly written."""
import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import anyio
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport
from g6.mcp import repair
from g6.mcp.cloud_consent import input_refusal
from g6.mcp.stdio import stdio_server


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def send(outbound, session, request_id, reply, record=None):
    body = transport.call_body(ToolResult(content=reply))
    ticket = transport.Ticket(
        (), "openai:codex", frozenset(), transport.digest(body), delivered=record
    )
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=request_id, result=body
    )))
    context = transport._OUTBOUND.set(ticket)
    try:
        await transport._Stream(outbound, session).send(packet)
    finally:
        transport._OUTBOUND.reset(context)


def test_cancel_after_writer_takes_reply_still_counts_written_refusal(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = SimpleNamespace(client_params=SimpleNamespace(
        clientInfo=types.Implementation(name="codex", version="review")
    ))
    arguments = {"query": "claim", "extra": 1}

    def refuse():
        records = []
        reply = input_refusal(
            "g6_find", {"query": {}, "limit": {}},
            arguments=arguments, session=session, records=records
        )
        return reply, records[0]

    async def run():
        first_started, release_first = asyncio.Event(), asyncio.Event()
        release_second, second_flushed = asyncio.Event(), asyncio.Event()
        pending = None

        class Output:
            lines = []

            async def write(self, data):
                self.lines.append(data)
                if len(self.lines) == 1:
                    first_started.set()
                    await release_first.wait()
                else:
                    # The SDK owns the exact packet and has begun its write,
                    # but the rendezvous sender has not resumed yet.
                    pending.cancel()
                    await release_second.wait()
                return len(data)

            async def flush(self):
                if len(self.lines) == 2:
                    second_flushed.set()

        output = Output()
        async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
            await send(outbound, session, 1, {"ok": True})
            await first_started.wait()
            reply, record = refuse()
            pending = asyncio.create_task(send(outbound, session, 2, reply, record))
            try:
                with anyio.fail_after(5):
                    while outbound.statistics().tasks_waiting_send == 0:
                        await anyio.lowlevel.checkpoint()
                    release_first.set()
                    with suppress(asyncio.CancelledError):
                        await pending
                    assert pending.cancelled()
                    release_second.set()
                    await second_flushed.wait()
            finally:
                release_first.set()
                release_second.set()
                await outbound.aclose()
        assert len(output.lines) == 2
        assert json.loads(output.lines[1])["id"] == 2
        assert output.lines[1].endswith("\n")

    asyncio.run(run())
    again, _ = refuse()
    assert again["repair"]["retries_left"] == 0, (
        "the refusal was wholly written and flushed, but cancellation after "
        "the writer took it discarded its callback",
        again["repair"], rows,
    )
