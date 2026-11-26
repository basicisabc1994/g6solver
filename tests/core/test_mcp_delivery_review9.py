# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/wire-consistency-fu2: ownership of G6's stdio writer follows every stream derived from it, a
write whose count is unknown is not a write, and a connection that ends neither waits on nor counts a tool body still
running in a worker thread."""
import asyncio
import json
import threading
from types import SimpleNamespace

import anyio
import pytest
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport, repair
from g6.mcp.cloud_consent import install, public_tool
from g6.mcp.stdio import stdio_server

BAD = {"ok": False, "error": "[G6_E_DESIGN_BAD_PATH] refused"}


class Session:
    client_params = SimpleNamespace(clientInfo=types.Implementation(name="codex", version="review9"))


class EmptyInput:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


async def send(stream, session, args):
    reply, record = repair.observed("g6_invoke", args, BAD, session)
    body = transport.call_body(ToolResult(content=reply))
    ticket = transport.Ticket((), "openai:codex", frozenset(), transport.digest(body), delivered=record)
    packet = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=1, result=body)))
    mark = transport._OUTBOUND.set(ticket)
    try:
        await stream.send(packet)
    finally:
        transport._OUTBOUND.reset(mark)


class Delegating:
    """A wrapper G6 did not write around the owned stream, delegating to it as ``_Stream`` does."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def send(self, message):
        await self.inner.send(message)


DERIVED = {
    "the wrapper's clone": lambda raw, session: transport._Stream(raw, session).clone(),
    "a clone of the raw stream": lambda raw, session: transport._Stream(raw.clone(), session),
    "a delegating wrapper": lambda raw, session: transport._Stream(Delegating(raw), session),
}


@pytest.mark.parametrize("derived", list(DERIVED))
def test_a_stream_derived_from_g6s_stdio_writer_counts_only_on_its_write(monkeypatch, derived):
    """A send through any stream derived from the owned one waits for its own write: nothing is counted while the
    write is blocked, nothing when it fails, and a send after the connection ended is dropped, never "inferred"."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class Output:
            async def write(self, data):
                started.set()
                await release.wait()
                raise anyio.ClosedResourceError

            async def flush(self):
                raise AssertionError("the write failed")

        with anyio.fail_after(5):
            async with stdio_server(stdin=EmptyInput(), stdout=Output()) as (_, raw):
                stream = DERIVED[derived](raw, session)
                await send(stream, session, {"a": 1})
                await started.wait()
                before_write = list(rows)
                release.set()
                await anyio.sleep_forever()                # the failed write ends the connection
            await send(stream, session, {"a": 2})
        return before_write

    before_write = asyncio.run(run())
    assert before_write == [], before_write
    assert rows == [], rows


def test_clones_of_a_memory_stream_share_the_state_g6_keys_ownership_by():
    """``g6.mcp.consent_transport`` names an owned connection by the anyio memory stream's shared state, so a clone
    is owned too; an anyio change to that fails here, not silently in the count."""
    sender, receiver = anyio.create_memory_object_stream(0)
    try:
        assert sender.clone()._state is sender._state
        assert transport._connection(transport._Stream(sender.clone(), None)) is sender._state
    finally:
        sender.close()
        receiver.close()


def test_a_write_that_returns_no_count_ends_the_connection_and_counts_nothing(monkeypatch):
    """G6's writer must know its line was taken: a write that returns no count is unknown, never a whole write."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    session = Session()

    class Output:
        flushes = 0

        async def write(self, data):
            return None

        async def flush(self):
            self.flushes += 1

    async def run():
        output = Output()
        with anyio.fail_after(5):
            async with stdio_server(stdin=EmptyInput(), stdout=output) as (_, raw):
                await send(transport._Stream(raw, session), session, {"a": 1})
                await anyio.sleep_forever()
        return output

    assert asyncio.run(run()).flushes == 0
    assert rows == [], rows


def test_an_ended_connection_neither_waits_on_nor_counts_a_running_synchronous_tool(monkeypatch):
    """Python cannot stop a thread, so a synchronous tool body already running when the connection ends (its writer
    failed) runs on. What holds: (a) no body starts once the connection is ending, (b) the connection closes without
    waiting for the running body, and (c) what that body returns is never sent nor counted."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []

    async def run():
        initialized, failed = asyncio.Event(), asyncio.Event()
        server = FastMCP("review9-stdio-thread")
        install(server)
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 1                            # a second body waits for the first's thread

        @server.tool
        @public_tool
        def probe(n: int) -> dict:
            calls.append(n)
            started.set()
            try:
                if not release.wait(10):
                    raise RuntimeError("the test never released the body")
                return BAD
            finally:
                finished.set()

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "codex", "version": "review9"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "probe", "arguments": {"n": 1}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "probe", "arguments": {"n": 2}}},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]

        class Input:
            index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index == 1:
                    await initialized.wait()
                if self.index == 3:
                    while not started.is_set():
                        await anyio.lowlevel.checkpoint()
                if self.index == 4:
                    while limiter.statistics().tasks_waiting == 0:
                        await anyio.lowlevel.checkpoint()
                if self.index == len(requests):
                    await anyio.sleep_forever()
                item = requests[self.index]
                self.index += 1
                return json.dumps(item) + "\n"

        class Output:
            written = []

            async def write(self, data):
                ident = json.loads(data).get("id")
                if ident == 3:
                    failed.set()
                    raise OSError("client pipe closed")
                self.written.append(ident)
                return len(data)

            async def flush(self):
                if self.written[-1] == 1:
                    initialized.set()

        output = Output()
        with anyio.fail_after(5):
            async with stdio_server(stdin=Input(), stdout=output) as (incoming, outgoing):
                await server._mcp_server.run(incoming, outgoing, server._mcp_server.create_initialization_options())
        assert failed.is_set() and not release.is_set() and not finished.is_set()   # (b) closed while it runs
        assert outgoing not in transport._WITNESSES
        release.set()
        assert await anyio.to_thread.run_sync(finished.wait, 5)
        for _ in range(20):
            await anyio.lowlevel.checkpoint()
        return output.written

    try:
        written = asyncio.run(run())
    finally:
        release.set()
    assert calls == [1], f"(a) a tool body started after the connection was ending: {calls}"
    assert written == [1], f"(c) a reply after the connection ended was written: {written}"
    assert rows == [], f"(c) a body that finished after the connection ended was counted: {rows}"
