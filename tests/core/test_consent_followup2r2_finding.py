"""An MCP-accepted reason must not prevent the unconditional stop."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from fastmcp import Client
from fastmcp.exceptions import ToolError

from g6.mcp import build_server, improve
from g6.metaharness import runner
from g6.self_maintenance.envelope import (
    CYCLE_LOG, CycleLog, what_stops, stops_asked,
)


def test_a_schema_accepted_stop_reason_cannot_leave_the_start_admitted(tmp_path, monkeypatch):
    # The repository's deterministic FakeLLM domain and real consent handlers.
    # Only the runner launch/watch are fakes; no runner process is started.
    source = (Path.cwd() / "tests") / "core" / "test_consent_binding.py"
    spec = importlib.util.spec_from_file_location("review_stop_binding_helpers", source)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    rig = helpers.Rig(tmp_path, monkeypatch)
    assert rig.call("g6_improve_enrol")["code"] == runner.ENROLLED
    monkeypatch.setattr(improve, "_spawn", lambda *args: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(improve, "_watch", lambda *args: improve._envelope(
        "qualified-draft", improve.STARTED, "fake runner", running=True))
    assert rig.call("g6_improve_start")["code"] == improve.STARTED
    root, settings, log = rig.root, rig.settings, rig.log()
    assert runner._gate(root, settings) == ("", "")
    loop, _ = runner.open_loop(root)
    assert loop._refused() is None, "the enrolled domain must actually be runnable before the stop"
    assert what_stops(root, log.started())[1] == ""

    # This is a JSON string accepted by the actual tool schema, not a call
    # bypassing MCP validation. The wrapper only witnesses that it reached G6.
    reason = json.loads(r'"\ud800"')
    entered = []
    real_stop = improve.stop

    def observed(folder, reason=""):
        entered.append(reason)
        return real_stop(folder, reason)

    monkeypatch.setattr(improve, "stop", observed)

    async def call():
        async with Client(build_server()) as client:
            return await client.call_tool("g6_improve_stop", {"folder": str(root), "reason": reason})

    rejected = False
    try:
        asyncio.run(call())
    except ToolError:
        rejected = True
    if not entered:
        assert rejected, "a fix may reject this value before admitting it to the tool"
        return
    assert entered == [reason], "the request must reach G6 after MCP schema validation"
    reopened = CycleLog(root / CYCLE_LOG)
    assert what_stops(root, reopened.started())[1], (
        "MCP accepted the stop reason, but no stop stands and the existing start is still admitted: "
        f"gate={runner._gate(root, settings)!r}, stops={stops_asked(root)}, "
        f"STOP_exists={(root / 'STOP').exists()}, start_in_force={reopened.start_in_force()}"
    )
