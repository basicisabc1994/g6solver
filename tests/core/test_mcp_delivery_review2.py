# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/wire-consistency: repair evidence follows the writing of each reply, seen at the writer. The
server is opened through G6's stdio entry (``g6.mcp.stdio.stdio_server``), the one whose writer G6 sees."""
import asyncio
from types import SimpleNamespace

import anyio
from fastmcp.tools.tool import ToolResult
from mcp import types
from g6.mcp.stdio import stdio_server
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport
from g6.mcp import repair


class Session:
    client_params = SimpleNamespace(
        clientInfo=types.Implementation(name="codex", version="review")
    )


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


BAD = {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] refused"}


async def send(outbound, session, request_id, reply, record=None):
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


def test_failed_stdout_does_not_resolve_previous_repair(monkeypatch):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()
    # Establish a previously delivered refusal.
    repair.observe("g6_invoke", {"a": 1}, BAD, session)

    class BrokenOutput:
        async def write(self, data):
            await anyio.lowlevel.checkpoint()
            raise anyio.ClosedResourceError

        async def flush(self):
            raise AssertionError("write failed before flush")

    async def run():
        reply, record = repair.observed(
            "g6_invoke", {"a": 2}, {"ok": True}, session
        )
        async with stdio_server(
            stdin=EmptyInput(), stdout=BrokenOutput()
        ) as (_, outbound):
            await send(outbound, session, 1, reply, record)

    asyncio.run(run())
    assert not [r for r in rows if r.get("outcome") == "resolved"], rows


def test_flushed_refusal_counts_while_writer_is_busy_with_next_reply(monkeypatch):
    monkeypatch.setattr(repair, "_log", lambda row: None)
    session = Session()

    async def run():
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        release_second = asyncio.Event()

        class Output:
            writes = 0

            async def write(self, data):
                self.writes += 1
                if self.writes == 2:
                    second_started.set()
                    await release_second.wait()
                return len(data)                # G6's writer counts a line only when stdout says it took it

            async def flush(self):
                if self.writes == 1:
                    await release_first.wait()

        reply, record = repair.observed("g6_invoke", {"a": 1}, BAD, session)
        async with stdio_server(
            stdin=EmptyInput(), stdout=Output()
        ) as (_, outbound):
            await send(outbound, session, 1, reply, record)
            pending = asyncio.create_task(
                send(outbound, session, 2, {"ok": True})
            )
            try:
                with anyio.fail_after(5):
                    while outbound.statistics().tasks_waiting_send == 0:
                        await anyio.lowlevel.checkpoint()
                    release_first.set()
                    await second_started.wait()
                    await pending
                # Reply 1 has flushed; the writer is now occupied with reply 2.
                # (Its count came from its own callback at the flush; nothing settles later.)
                again, _ = repair.observed("g6_invoke", {"a": 1}, BAD, session)
            finally:
                release_first.set()
                release_second.set()
                await pending
                await outbound.aclose()
        return again["repair"]

    found = asyncio.run(run())
    assert found["retries_left"] == 0 and found["by"] == "person", found


def test_rows_say_written_through_g6s_writers_and_inferred_elsewhere(tmp_path, monkeypatch):
    """G6MemoryTransport sees the in-process client take each reply ("written"); the stock in-process client is a
    transport G6 cannot see, where the count is kept from the handoff and says so ("inferred")."""
    from fastmcp import Client

    from g6.mcp import build_server
    from g6.mcp.consent_transport import G6MemoryTransport

    monkeypatch.setenv("G6_HOME", str(tmp_path))
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)

    async def run(served):
        async with Client(served, client_info=types.Implementation(name="codex", version="review")) as client:
            for phase in ("one", "verify"):
                await client.call_tool("g6_select", {"task": "a", "phase": phase}, raise_on_error=False)

    for served, evidence in ((G6MemoryTransport(build_server()), "written"), (build_server(), "inferred")):
        rows.clear()
        asyncio.run(run(served))
        assert [(r["event"], r.get("outcome"), r.get("evidence")) for r in rows] == [
            ("refused", None, evidence), ("next", "resolved", evidence)], (evidence, rows)


def test_the_writer_seams_g6_counts_on_are_as_relied_on():
    """A FastMCP or SDK upgrade that changes the lines G6 copies or keys on fails here, not silently in the count."""
    import inspect

    from fastmcp import FastMCP
    from fastmcp.client.transports import FastMCPTransport
    from mcp.server import stdio as sdk

    import g6.mcp

    assert list(inspect.signature(sdk.stdio_server).parameters) == ["stdin", "stdout"]
    reader = inspect.getsource(sdk.stdio_server)
    lines = ["async for line in stdin", "types.JSONRPCMessage.model_validate_json(line)",
             "await read_stream_writer.send(session_message)"]
    at = [reader.find(line) for line in lines]
    assert -1 not in at and at == sorted(at), "the SDK's stdio reader changed: g6.mcp.stdio reads stdin through it"
    run = inspect.getsource(FastMCP.run_stdio_async)
    for part in ("self._lifespan_manager()", "stdio_server()", "self._mcp_server.run(", "NotificationOptions(",
                 "tools_changed=True"):
        assert part in run, f"FastMCP's run_stdio_async changed ({part}): g6.mcp.stdio.serve copies it"
    memory = inspect.getsource(FastMCPTransport.connect_session)
    for part in ("create_client_server_memory_streams()", "self.server._mcp_server.run(", "_enter_server_lifespan(",
                 "ClientSession("):
        assert part in memory, f"FastMCPTransport changed ({part}): G6MemoryTransport copies it"
    assert "stdio.serve" in inspect.getsource(g6.mcp.main), "G6's server runs stdio through its own writer"
