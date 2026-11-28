# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/wire-consistency-fu: a connection that ends on any path (a lifespan or server error, an outer cancellation) discards its unread delivery callbacks, and every place that creates a witness ends it in a finally."""
import asyncio
from contextlib import asynccontextmanager, nullcontext, suppress

import anyio
import pytest
from fastmcp import Context, FastMCP
from mcp import types

from g6.mcp import consent_transport as transport
from g6.mcp import repair
from g6.mcp.cloud_consent import install, public_tool


@pytest.mark.parametrize(
    "exit_path", ["lifespan_error", "server_error", "outer_cancel"]
)
def test_exceptional_disconnect_discards_unread_reply(monkeypatch, exit_path):
    rows = []
    monkeypatch.setattr(repair, "_log", rows.append)
    held = {}

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            if exit_path == "lifespan_error":
                raise RuntimeError("review lifespan teardown failed")

    async def run():
        paused, crash = asyncio.Event(), asyncio.Event()
        server = FastMCP("review-disconnect", lifespan=lifespan)
        install(server)
        if exit_path == "server_error":
            original = server._mcp_server.run

            async def failing_server(*args, **kwargs):
                async with anyio.create_task_group() as group:
                    group.start_soon(lambda: original(*args, **kwargs))
                    await crash.wait()
                    raise RuntimeError("review server task failed")

            monkeypatch.setattr(server._mcp_server, "run", failing_server)

        @server.tool
        @public_tool
        async def probe(ctx: Context) -> dict:
            held["stream"] = ctx.session._write_stream.inner
            held["seen"] = transport._WITNESSES[held["stream"]]
            with transport.consent_form():
                await ctx.info("pause the client reader")
            return {"ok": False, "error": "[G6_E_INPUT_VALIDATION] probe"}

        async def logging_callback(params):
            paused.set()
            await asyncio.Event().wait()

        link = transport.G6MemoryTransport(server)
        expected = (
            nullcontext() if exit_path == "outer_cancel"
            else pytest.raises(ExceptionGroup, match="unhandled errors")
        )
        with anyio.CancelScope() as cancel_scope, expected:
            async with link.connect_session(
                logging_callback=logging_callback,
                client_info=types.Implementation(name="codex", version="review"),
            ) as client:
                await client.initialize()
                pending = asyncio.create_task(client.call_tool("probe", {}))
                try:
                    with anyio.fail_after(5):
                        await paused.wait()
                        while (
                            not held["seen"]._expected
                            or held["stream"].statistics().current_buffer_used != 1
                        ):
                            await anyio.lowlevel.checkpoint()
                    assert not held["seen"]._taken
                    if exit_path == "server_error":
                        crash.set()
                        await asyncio.Event().wait()
                    elif exit_path == "outer_cancel":
                        cancel_scope.cancel()
                        await anyio.lowlevel.checkpoint()
                finally:
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending

    try:
        asyncio.run(run())
        seen, stream = held["seen"], held["stream"]
        assert rows == [], "an unread reply was counted"
        assert stream.statistics().open_send_streams == 0
        assert stream.statistics().open_receive_streams == 0
        assert (
            stream not in transport._WITNESSES
            and not seen._expected
            and not seen._taken
        ), (
            "closed connection retained delivery state",
            stream in transport._WITNESSES,
            len(seen._expected),
            len(seen._taken),
        )
    finally:
        if "stream" in held:
            transport.unwitness(held["stream"])


def test_every_place_that_creates_a_witness_ends_it_in_a_finally():
    """Each ``witness(stream)`` under ``g6/mcp`` is followed at once by a ``try`` whose ``finally`` calls
    ``unwitness`` on the same stream, so no exit path (an error, a cancellation) leaves a connection's callbacks."""
    import ast
    from pathlib import Path

    import g6.mcp

    found, unguarded = 0, []
    for path in sorted(Path(g6.mcp.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                body = getattr(node, field, None)
                if not isinstance(body, list):
                    continue
                for index, statement in enumerate(body):
                    calls = [c for c in ast.walk(statement) if isinstance(c, ast.Call)
                             and getattr(c.func, "id", getattr(c.func, "attr", "")) == "witness"]
                    if not calls or not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
                        continue                    # the simple statement that makes the call, not one around it
                    found += 1
                    stream = ast.dump(calls[0].args[0])
                    after = body[index + 1] if index + 1 < len(body) else None
                    ends = isinstance(after, ast.Try) and any(
                        isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", "")) == "unwitness"
                        and c.args and ast.dump(c.args[0]) == stream
                        for s in after.finalbody for c in ast.walk(s))
                    if not ends:
                        unguarded.append(f"{path.name}:{statement.lineno}")
    assert found >= 2 and not unguarded, (found, unguarded)