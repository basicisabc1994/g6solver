# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/wire-consistency-fu2: a synchronous tool body claims its start under its connection's lock, so
none starts once the connection ended; and G6 reads stdin itself, so a failed writer ends the connection while the
client still holds stdin open."""
import asyncio
import json
import os
import threading

import anyio
import pytest
from fastmcp import FastMCP
from mcp import types
from mcp.shared.message import SessionMessage

from g6.mcp import consent_transport as transport, repair
from g6.mcp.cloud_consent import install, public_tool
from g6.mcp.stdio import stdio_server


def test_a_body_claimed_after_its_connection_ended_never_starts(monkeypatch):
    """A worker thread held back just before its claim (as a preempted thread would be) while the writer fails and the
    connection closes: its claim then fails, so the body never starts, and nothing is written or counted."""
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    paused, release, refused = threading.Event(), threading.Event(), []
    calls = []
    claim = transport.Witness.claim

    def held_claim(self):
        paused.set()
        if not release.wait(5):
            raise RuntimeError("the test never released the worker")
        try:
            return claim(self)
        except BaseException as exc:
            refused.append(type(exc).__name__)
            raise

    monkeypatch.setattr(transport.Witness, "claim", held_claim)

    async def run():
        initialized = asyncio.Event()
        server = FastMCP("review10-claim")
        install(server)

        @server.tool
        @public_tool
        def probe() -> dict:
            calls.append("body started")
            return {"ok": True}

        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "codex", "version": "review10"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "probe", "arguments": {}}},
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
                    while not paused.is_set():
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
                    raise BrokenPipeError("client closed its reply pipe")
                self.written.append(ident)
                return len(data)

            async def flush(self):
                if self.written[-1] == 1:
                    initialized.set()

        output = Output()
        with anyio.fail_after(5):
            async with stdio_server(stdin=Input(), stdout=output) as (incoming, outgoing):
                await server._mcp_server.run(incoming, outgoing, server._mcp_server.create_initialization_options())
        assert paused.is_set() and not calls
        release.set()                                   # the connection is closed; only now may the worker claim
        with anyio.fail_after(5):
            while not refused:
                await anyio.sleep(0.01)
        return output.written

    try:
        written = asyncio.run(run())
    finally:
        release.set()
    assert calls == [], f"a tool body started after its connection ended: {calls}"
    assert refused, "the worker's claim did not fail on the ended connection"
    assert written == [1], written
    assert rows == [], rows


def test_a_claim_succeeds_only_while_the_connection_is_open():
    seen = transport.Witness()

    def claim():
        seen.claim()
        return True

    async def run():
        assert await anyio.to_thread.run_sync(claim) is True
        seen.close()
        with pytest.raises(RuntimeError):
            await anyio.to_thread.run_sync(claim)

    asyncio.run(run())


def _pipe_stdin():
    read_fd, write_fd = os.pipe()
    return os.fdopen(read_fd, "r", encoding="utf-8"), os.fdopen(write_fd, "wb", buffering=0)


def test_a_failed_writer_ends_the_connection_while_the_client_keeps_stdin_open():
    """G6 reads stdin in a thread it can leave: a write error ends the connection at once, the client's stdin still
    open, and that thread holds neither the connection nor the process (a daemon)."""
    async def run():
        source, client = _pipe_stdin()
        failed, closed = asyncio.Event(), asyncio.Event()

        class Broken:
            async def write(self, data):
                failed.set()
                raise BrokenPipeError("client closed its reply pipe")

            async def flush(self):
                raise AssertionError("the write failed")

        async def connection():
            try:
                async with stdio_server(stdin=anyio.wrap_file(source), stdout=Broken()) as (_, outgoing):
                    await outgoing.send(SessionMessage(types.JSONRPCMessage(
                        types.JSONRPCResponse(jsonrpc="2.0", id=1, result={}))))
                    await anyio.sleep_forever()
            finally:
                closed.set()

        pending = asyncio.create_task(connection())
        try:
            with anyio.fail_after(3):
                await failed.wait()
            with anyio.move_on_after(1):
                await closed.wait()
            ended = closed.is_set()
            readers = [t for t in threading.enumerate() if t.name == "g6-stdin-reader" and t.is_alive()]
        finally:
            client.close()
            await asyncio.wait_for(pending, 3)
            source.close()
        assert ended, "a failed writer did not end the connection until the client closed stdin"
        assert readers and all(t.daemon for t in readers), readers

    asyncio.run(run())


def test_g6s_stdin_reader_hands_each_line_to_the_server_until_end_of_input():
    """Lines from a real pipe reach the server in order, a malformed line as the SDK's parse error, and the client's
    end of input ends the incoming stream."""
    async def run():
        source, client = _pipe_stdin()

        class Output:
            async def write(self, data):
                return len(data)

            async def flush(self):
                pass

        ping = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        client.write((ping + "\nnot json\n" + ping.replace("7", "8") + "\n").encode())
        client.close()
        got = []
        with anyio.fail_after(5):
            async with stdio_server(stdin=anyio.wrap_file(source), stdout=Output()) as (incoming, outgoing):
                async for item in incoming:
                    got.append(item.message.root.id if isinstance(item, SessionMessage) else type(item).__name__)
                await outgoing.aclose()                # the server is done: G6's writer ends too
        source.close()
        return got

    assert asyncio.run(run()) == [7, "ValidationError", 8]
