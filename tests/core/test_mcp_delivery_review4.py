# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/wire-consistency: writer evidence stays attached to its own send: a short, failed or cancelled write never lends its callback to a later identical line."""
import asyncio
from types import SimpleNamespace

import anyio
import pytest
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport
from g6.mcp import repair
from g6.mcp.stdio import stdio_server
from g6.mcp.cloud_consent import input_refusal

def refused(session, arguments):
    records = []
    reply = input_refusal(
        "g6_find", {"query": {}, "limit": {}},
        arguments=arguments, session=session, records=records
    )
    return reply, records[0]


class Session:
    client_params = SimpleNamespace(
        clientInfo=types.Implementation(name="codex", version="review4")
    )


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def send(outbound, session, request_id, reply, record):
    body = transport.call_body(ToolResult(content=reply))
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=request_id, result=body
    )))
    ticket = transport.Ticket(
        (), "openai:codex", frozenset(), transport.digest(body), delivered=record
    )
    token = transport._OUTBOUND.set(ticket)
    try:
        await transport._Stream(outbound, session).send(packet)
    finally:
        transport._OUTBOUND.reset(token)


@pytest.mark.parametrize("accepted", ["part of the line", "none of the line"])
def test_short_middle_write_does_not_count_the_wrong_reply(monkeypatch, accepted):
    """Three identical lines; the second's first write is short. A write that took part of the line is continued, so
    each reply counts on its own whole line and the last count is the third's. A write that took none of it ends the
    connection: the second and third count nothing, and the last count stays the first's."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()
    arguments = [{"query": "claim", "extra": n} for n in (1, 2, 3)]

    async def run():
        flushed = [asyncio.Event() for _ in arguments]

        class Output:
            def __init__(self):
                self.lines, self.writes = [""], 0        # the text of each line, split where it was flushed

            async def write(self, data):
                self.writes += 1
                if self.writes == 2:
                    data = data[:len(data) // 2] if accepted == "part of the line" else ""
                self.lines[-1] += data
                return len(data)

            async def flush(self):
                self.lines.append("")
                flushed[len(self.lines) - 2].set()

        output = Output()
        replies = [refused(session, args) for args in arguments]     # made before any is counted: identical lines
        with anyio.fail_after(5):
            async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
                for index, (reply, record) in enumerate(replies):
                    await send(outbound, session, 7, reply, record)
                    await flushed[index].wait()
                await outbound.aclose()
            if accepted == "none of the line":
                reply, record = replies[2]
                await send(outbound, session, 7, reply, record)    # the connection ended: never written
        return output

    output = asyncio.run(run())
    state = repair._state(session)[("g6_find", "G6_E_INPUT_VALIDATION")]
    if accepted == "part of the line":
        assert output.writes == 4 and output.lines[3] == "", output.lines
        assert output.lines[0] == output.lines[1] == output.lines[2] and output.lines[0].endswith("\n")
        assert [r["event"] for r in rows].count("refused") == 3 and {r["evidence"] for r in rows} == {"written"}, rows
        expected = arguments[2]
    else:
        assert output.writes == 2 and output.lines[1] == "", output.lines
        assert [(r["event"], r["evidence"]) for r in rows] == [("refused", "written")], rows
        expected = arguments[0]
    assert state["digest"] == repair._digest(expected), (
        "a count was given to a reply whose own whole line was not written", state)


def test_cancelled_send_does_not_steal_a_later_identical_line(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()
    cancelled_args = {"query": "claim", "extra": 1}
    delivered_args = {"query": "claim", "extra": 2}

    async def run():
        blocked, release = asyncio.Event(), asyncio.Event()
        flushed = asyncio.Event()

        class Output:
            writes = 0

            async def write(self, data):
                self.writes += 1
                if self.writes == 1:
                    blocked.set()
                    await release.wait()
                return len(data)

            async def flush(self):
                if self.writes == 2:
                    flushed.set()

        async with stdio_server(stdin=EmptyInput(), stdout=Output()) as (_, outbound):
            await send(outbound, session, 100, {"ok": True}, None)
            await blocked.wait()
            cancelled, cancelled_record = refused(session, cancelled_args)
            pending = asyncio.create_task(
                send(outbound, session, 7, cancelled, cancelled_record)
            )
            try:
                with anyio.fail_after(5):
                    while outbound.statistics().tasks_waiting_send == 0:
                        await anyio.lowlevel.checkpoint()
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
                delivered, delivered_record = refused(session, delivered_args)
                assert cancelled == delivered
                release.set()
                await send(outbound, session, 7, delivered, delivered_record)
                with anyio.fail_after(5):
                    await flushed.wait()
            finally:
                release.set()
                await outbound.aclose()

    asyncio.run(run())
    again, _ = refused(session, delivered_args)
    assert again["repair"]["retries_left"] == 0, (
        "an unchanged retry after a written refusal still has a retry left",
        again["repair"]["retries_left"],
        repair._state(session)[("g6_find", "G6_E_INPUT_VALIDATION")],
    )


def test_a_failed_flush_discards_its_own_callback_and_no_other(monkeypatch):
    """Two sends with identical text keep a callback each. The first's flush fails: that ends the connection, so the
    first is not counted, its failure fires no other send's callback, and the second, sent after, is never written
    and never counted."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    class Output:
        def __init__(self):
            self.writes, self.flushes = [], 0

        async def write(self, data):
            self.writes.append(data)
            return len(data)

        async def flush(self):
            self.flushes += 1
            raise OSError("the pipe closed")

    async def run():
        output = Output()
        first, first_record = refused(session, {"query": "claim", "extra": 1})
        second, second_record = refused(session, {"query": "claim", "extra": 2})
        assert first == second
        with anyio.fail_after(5):
            async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
                await send(outbound, session, 7, first, first_record)
                await anyio.sleep_forever()               # the failed flush ends the connection
            await send(outbound, session, 7, second, second_record)
        return output

    output = asyncio.run(run())
    assert len(output.writes) == 1 and output.flushes == 1, output.writes
    assert rows == [], rows
    assert ("g6_find", "G6_E_INPUT_VALIDATION") not in repair._state(session)