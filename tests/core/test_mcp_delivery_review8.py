# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/wire-consistency-fu: a send during stdio teardown is never counted as inferred, and framing survives a short write."""
import asyncio
from types import SimpleNamespace

import anyio
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport, repair
from g6.mcp.stdio import stdio_server


class Session:
    client_params = SimpleNamespace(
        clientInfo=types.Implementation(name="codex", version="review")
    )


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def send(outbound, session, ident, reply, record=None):
    body = transport.call_body(ToolResult(content=reply))
    ticket = transport.Ticket(
        (), "openai:codex", frozenset(), transport.digest(body),
        delivered=record,
    )
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=ident, result=body,
    )))
    mark = transport._OUTBOUND.set(ticket)
    try:
        await transport._Stream(outbound, session).send(packet)
    finally:
        transport._OUTBOUND.reset(mark)


def refusal(session):
    return repair.observed(
        "g6_invoke", {"a": 1},
        {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] refused"}, session,
    )


def test_send_during_stdio_teardown_is_not_counted_as_inferred(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    async def run():
        started, release, send_late = (
            asyncio.Event(), asyncio.Event(), asyncio.Event()
        )

        class Output:
            writes = 0
            accepted = []

            async def write(self, data):
                self.writes += 1
                if self.writes == 1:
                    started.set()
                    await release.wait()
                    self.accepted.append(data)
                    return len(data)
                raise anyio.ClosedResourceError

            async def flush(self):
                pass

        output = Output()
        reply, record = refusal(session)
        with anyio.fail_after(5):
            async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
                await send(outbound, session, 1, {"ok": True})
                await started.wait()

                async def late_sender():
                    await send_late.wait()
                    try:
                        await send(outbound, session, 2, reply, record)
                    finally:
                        await outbound.aclose()

                pending = asyncio.create_task(late_sender())

                def finish_during_sdk_exit():
                    release.set()
                    send_late.set()

                asyncio.get_running_loop().call_later(
                    0.05, finish_during_sdk_exit
                )
            await pending

        assert output.writes == 2
        assert [types.JSONRPCMessage.model_validate_json(line).root.id
                for line in output.accepted] == [1]

    asyncio.run(run())
    assert rows == [], f"failed stdio send counted after unwitness: {rows}"


def test_short_write_does_not_count_next_unparseable_response(monkeypatch):
    """A short write is continued, so the next reply is never joined to a broken line: both lines parse, and the
    refusal is counted once, on "written" evidence, only after its own whole line was written and flushed."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    async def run():
        flushed = asyncio.Event()

        class Output:
            text = ""
            writes = 0
            flushes = 0

            async def write(self, data):
                self.writes += 1
                accepted = data[:-1] if self.writes == 1 else data
                self.text += accepted
                return len(accepted)

            async def flush(self):
                self.flushes += 1
                if self.flushes == 2:
                    flushed.set()

        output = Output()
        reply, record = refusal(session)
        async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, outbound):
            try:
                await send(outbound, session, 1, {"ok": True})
                await send(outbound, session, 2, reply, record)
                with anyio.fail_after(5):
                    await flushed.wait()
            finally:
                await outbound.aclose()

        assert output.writes == 3, "the short first write was not continued"
        lines = output.text.split("\n")
        assert lines[-1] == "" and len(lines) == 3, output.text
        assert [types.JSONRPCMessage.model_validate_json(line).root.id for line in lines[:2]] == [1, 2]

    asyncio.run(run())
    assert [(r.get("event"), r.get("evidence")) for r in rows] == [("refused", "written")], rows


class OpenInput:
    """A client that keeps stdin open: the connection does not end from its side."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await anyio.sleep_forever()


def test_a_failed_write_ends_the_connection_and_counts_nothing_after_it(monkeypatch):
    """A write error ends the whole connection (G6 stops reading too), not only its writer; the failed reply and a send
    after it on that stream count nothing, on neither "written" nor "inferred" evidence."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    class Broken:
        writes = 0

        async def write(self, data):
            self.writes += 1
            raise OSError("the pipe closed")

        async def flush(self):
            raise AssertionError("write failed before flush")

    async def run():
        output = Broken()
        reply, record = refusal(session)
        with anyio.fail_after(5):
            async with stdio_server(stdin=OpenInput(), stdout=output) as (_, outbound):
                await send(outbound, session, 1, reply, record)
                await anyio.sleep_forever()          # the connection ends from the failed write, not from here
        assert output.writes == 1
        assert outbound not in transport._WITNESSES
        late, late_record = repair.observed(
            "g6_invoke", {"a": 2}, {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] refused"}, session)
        await send(outbound, session, 2, late, late_record)

    asyncio.run(run())
    assert rows == [], f"a reply on a failed stdio connection was counted: {rows}"
