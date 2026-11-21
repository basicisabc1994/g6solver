# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/wire-consistency: the unnamed client's catalogue reply still takes the person's revocation and is
recorded as a disclosure; a repair is counted only once the reply was written to the client, and a late delivery
never gives back a spent retry."""
import asyncio
import json
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

os.environ["FASTMCP_ENV_FILE"] = os.devnull
os.environ["FASTMCP_CHECK_FOR_UPDATES"] = "off"

from fastmcp import Client
from mcp.types import Implementation
from g6.core import cloud_consent as policy
from g6.core.ledger import ChainLedger
from g6.mcp import build_server, repair

@pytest.fixture(autouse=True)
def isolated_io(monkeypatch):
    """Only persistence is in memory; source and permission decisions are real."""
    monkeypatch.setenv("G6_HOME", r"C:\nonexistent\g6-review-home")
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.delenv("G6_MCP_SENSITIVE", raising=False)
    ledger = ChainLedger(validate=policy._validate)
    monkeypatch.setattr(policy, "_folder", lambda: Path(r"C:\nonexistent\g6-review-home"))
    monkeypatch.setattr(policy, "_mutex", lambda *args: nullcontext())
    monkeypatch.setattr(policy, "_open", lambda *args: ledger)
    monkeypatch.setattr(policy, "_append",
        lambda log, kind, body: log.append(kind, str(len(log.entries())), body))
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    return ledger, rows


def test_unknown_catalogue_respects_revoked_input(isolated_io):
    ledger, _ = isolated_io
    secret = "WC1_PRIVATE_REQUEST"
    arguments = {
        "task": "review my patch", "phase": "verify",
        "considering": [{"hypothesis": secret, "predicts": "the check fails"}],
    }
    scope = policy.input_source(json.dumps(arguments, sort_keys=True, default=str))

    async def run():
        async with Client(build_server(), client_info=Implementation(name="unnamed-test", version="test")) as client:
            revoked = await client.call_tool("g6_cloud_revoke", {
                "source": scope, "provider": "cloud:unknown"})
            assert revoked.structured_content["result"]["status"] == "revoked"
            return (await client.call_tool("g6_select", arguments)).structured_content

    reply = asyncio.run(run())
    assert reply.get("cloud_consent_code") == policy.REQUIRED, {
        "ok": reply.get("ok"), "secret_returned": secret in json.dumps(reply),
        "ledger_kinds": [row["kind"] for row in ledger.entries()],
    }


def test_unknown_own_input_send_is_disclosed(isolated_io):
    ledger, _ = isolated_io
    arguments = {"task": "review my patch", "phase": "verify"}
    scope = policy.input_source(json.dumps(arguments, sort_keys=True, default=str))

    async def run():
        for name in ("codex", "unnamed-test"):
            async with Client(build_server(), client_info=Implementation(name=name, version="test")) as client:
                reply = (await client.call_tool("g6_select", arguments)).structured_content
                assert reply["ok"] is True and "cloud_consent_code" not in reply

    asyncio.run(run())
    recipients = {
        row["body"]["provider"] for row in ledger.entries()
        if row["kind"] == "disclosure"
        and any(source["source"] == scope for source in row["body"]["sources"])
    }
    assert recipients == {"openai:codex", "cloud:unknown"}, recipients

def test_late_delivery_does_not_restore_an_exhausted_retry_budget(isolated_io):
    from fastmcp.server.middleware import Middleware
    async def run():
        server = build_server()
        produced, release = asyncio.Event(), asyncio.Event()
        session_ref = []
        class DelayOldReply(Middleware):
            async def on_call_tool(self, context, call_next):
                result = await call_next(context)
                if context.message.arguments.get("phase") == "stale":
                    session_ref.append(context.fastmcp_context.session)
                    produced.set()
                    await release.wait()
                return result
        server.add_middleware(DelayOldReply())
        async with Client(server, client_info=Implementation(name="unnamed-test", version="test")) as client:
            async def refuse(phase):
                result = await client.call_tool("g6_select", {"task": "review patch", "phase": phase})
                return result.structured_content["repair"]["retries_left"]
            assert await refuse("initial") == 2
            stale = asyncio.create_task(refuse("stale"))
            await produced.wait()
            try:
                assert await refuse("newer") == 1
                assert await refuse("newest") == 0
            finally:
                release.set()
            await stale
            return repair._state(session_ref[0])[("g6_select", "G6_E_INPUT_VALIDATION")]["left"]
    left = asyncio.run(run())
    assert left == 0, f"delivery restored exhausted retries_left to {left}"

def test_stdio_failure_does_not_record_undelivered_repair(isolated_io):
    from fastmcp.tools.tool import ToolResult
    from mcp import types
    from mcp.server.stdio import stdio_server
    from mcp.shared.message import SessionMessage
    from g6.mcp import consent_transport as transport

    _, rows = isolated_io
    class Session:
        client_params = SimpleNamespace(clientInfo=Implementation(name="codex", version="test"))
    class EmptyInput:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration
    class BrokenOutput:
        async def write(self, data):
            await anyio.lowlevel.checkpoint()
            raise anyio.ClosedResourceError
        async def flush(self):
            raise AssertionError("no bytes were written")

    async def run():
        session = Session()
        reply, record = repair.observed("g6_select", {"phase": "invalid"}, {
            "ok": False, "error": "[G6_E_INPUT_VALIDATION] phase is invalid"}, session)
        result = ToolResult(content=reply)
        body = transport.call_body(result)
        ticket = transport.Ticket((), "openai:codex", frozenset(),
                                 transport.digest(body), delivered=record)
        message = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
            jsonrpc="2.0", id=1, result=body)))
        async with stdio_server(stdin=EmptyInput(), stdout=BrokenOutput()) as (_, outbound):
            token = transport._OUTBOUND.set(ticket)
            try:
                await transport._Stream(outbound, session).send(message)
            finally:
                transport._OUTBOUND.reset(token)
    asyncio.run(run())
    assert rows == [], [(row["event"], row.get("outcome")) for row in rows]
