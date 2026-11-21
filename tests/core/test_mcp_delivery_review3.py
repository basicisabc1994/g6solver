# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/wire-consistency: a line counts only when all of it was written and flushed, each write keeps its own callback, and a transport G6 cannot see counts at the handoff."""
import asyncio
from types import SimpleNamespace

import anyio
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport
from g6.mcp import repair
from g6.mcp.stdio import stdio_server


BAD = {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] refused"}


class Session:
    client_params = SimpleNamespace(
        clientInfo=types.Implementation(name="codex", version="review3")
    )


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def send(outbound, session, request_id, reply, record):
    body = transport.call_body(ToolResult(content=reply))
    ticket = transport.Ticket(
        (), "openai:codex", frozenset(), transport.digest(body), delivered=record
    )
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=request_id, result=body
    )))
    token = transport._OUTBOUND.set(ticket)
    try:
        await transport._Stream(outbound, session).send(packet)
    finally:
        transport._OUTBOUND.reset(token)


def test_short_stdout_write_is_not_written_evidence(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    async def run():
        flushed = asyncio.Event()

        class ShortOutput:
            accepted = ""

            async def write(self, data):
                self.accepted += data[:-1]
                return len(data) - 1

            async def flush(self):
                flushed.set()

        output = ShortOutput()
        reply, record = repair.observed("g6_invoke", {"a": 1}, BAD, session)
        async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
            await send(outbound, session, 1, reply, record)
            with anyio.fail_after(5):
                await flushed.wait()
            await outbound.aclose()
        assert output.accepted and not output.accepted.endswith("\n")

    asyncio.run(run())
    assert rows == [], f"incomplete line was recorded: {rows}"


def test_identical_lines_do_not_replace_another_deliveries_callback(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()
    first_args, second_args = {"a": 1}, {"a": 2}

    async def run():
        first_written = asyncio.Event()
        release_flush = asyncio.Event()
        second_failed = asyncio.Event()

        class Output:
            writes = 0

            async def write(self, data):
                self.writes += 1
                if self.writes == 2:
                    second_failed.set()
                    raise anyio.ClosedResourceError
                first_written.set()
                return len(data)

            async def flush(self):
                await release_flush.wait()

        first, first_record = repair.observed("g6_invoke", first_args, BAD, session)
        second, second_record = repair.observed("g6_invoke", second_args, BAD, session)
        assert first == second  # Legal reused request id produces identical serialized lines.
        async with stdio_server(stdin=EmptyInput(), stdout=Output()) as (_, outbound):
            await send(outbound, session, 7, first, first_record)
            with anyio.fail_after(5):
                await first_written.wait()
            # The client has the first complete line and can reuse its request id
            # while the writer is still waiting for that line's flush to return.
            pending = asyncio.create_task(
                send(outbound, session, 7, second, second_record)
            )
            try:
                with anyio.fail_after(5):
                    while outbound.statistics().tasks_waiting_send == 0:
                        await anyio.lowlevel.checkpoint()
                    release_flush.set()
                    await second_failed.wait()
                    await pending
            finally:
                release_flush.set()
                await pending
                await outbound.aclose()

    asyncio.run(run())
    assert len(rows) == 1 and rows[0]["evidence"] == "written", rows
    counted = repair._state(session)[("g6_invoke", "G6_E_DESIGN_BAD_PATH")]
    assert counted["digest"] == repair._digest(first_args), (
        "only the first line succeeded, but its callback counted the failed "
        f"second reply: {counted}"
    )


def test_other_transport_counts_at_handoff(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    class OtherTransport:
        async def send(self, packet):
            self.packet = packet

    async def run():
        reply, record = repair.observed("g6_invoke", {"a": 1}, BAD, session)
        await send(OtherTransport(), session, 1, reply, record)
        again, _ = repair.observed("g6_invoke", {"a": 1}, BAD, session)
        return again["repair"]

    again = asyncio.run(run())
    assert again["retries_left"] == 0, (
        f"successful handoff left the unchanged retry at {again['retries_left']}; "
        f"rows={rows}"
    )
