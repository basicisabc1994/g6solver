# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/wire-consistency: writer evidence stays attached to its own send: a short, failed or cancelled write never lends its callback to a later identical line."""
import asyncio
from types import SimpleNamespace

import anyio
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


def test_short_middle_write_does_not_count_the_wrong_reply(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()
    arguments = [{"query": "claim", "extra": n} for n in (1, 2, 3)]

    async def run():
        flushed = [asyncio.Event() for _ in arguments]

        class Output:
            lines = []

            async def write(self, data):
                self.lines.append(data)
                return 0 if len(self.lines) == 2 else len(data)

            async def flush(self):
                flushed[len(self.lines) - 1].set()

        output = Output()
        async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
            for index, args in enumerate(arguments):
                reply, record = refused(session, args)
                await send(outbound, session, 7, reply, record)
                with anyio.fail_after(5):
                    await flushed[index].wait()
            await outbound.aclose()
        assert output.lines[1] == output.lines[2]

    asyncio.run(run())
    state = repair._state(session)[("g6_find", "G6_E_INPUT_VALIDATION")]
    assert state["digest"] == repair._digest(arguments[2]), (
        "the third line was written, but the short second write was counted",
        state,
    )


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


def test_a_failed_flush_discards_its_own_callback_and_no_other():
    """Two sends with identical text keep a callback each; the first's flush fails, so only the second is counted."""
    import pytest

    from g6.mcp.stdio import WrittenStdout

    fired, seen = [], transport.Witness()
    first, second = (SessionMessage(transport.Tokened.model_validate({"jsonrpc": "2.0", "id": 1, "result": {}}))
                     for _ in range(2))
    seen.expect(first, lambda: fired.append("first"))
    seen.expect(second, lambda: fired.append("second"))

    class Output:
        flushes = 0

        async def write(self, data):
            return len(data)

        async def flush(self):
            self.flushes += 1
            if self.flushes == 1:
                raise OSError("the pipe closed")

    out = WrittenStdout(Output(), seen)

    async def run():
        lines = [m.message.model_dump_json(by_alias=True, exclude_none=True) + "\n" for m in (first, second)]
        assert lines[0] == lines[1]
        await out.write(lines[0])
        with pytest.raises(OSError):
            await out.flush()
        await out.write(lines[1])
        await out.flush()

    asyncio.run(run())
    assert fired == ["second"], fired