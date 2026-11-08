"""Distinct enrolment rows must remain distinct even when their payloads match."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.self_maintenance.envelope import CycleLog, CYCLE_LOG, ENROLMENT_CHANGED


def test_repeated_timestamp_does_not_hide_a_new_mcp_enrolment(monkeypatch):
    checkout = Path(__file__).resolve().parents[2]
    folder = checkout / ".review" / "tmp" / f"finding2-{uuid4().hex}"
    folder.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("finding2_helpers", checkout / "tests/core/test_consent_binding.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    rig = helpers.Rig(folder, monkeypatch)
    real_open = runner.open_loop

    def fixed_clock(*args, **kwargs):
        kwargs["now"] = lambda: helpers.START
        return real_open(*args, **kwargs)

    monkeypatch.setattr(runner, "open_loop", fixed_clock)
    monkeypatch.setattr(improve, "_spawn", lambda *args: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(improve, "_watch", lambda *args: _envelope("qualified-draft", improve.STARTED, "fake runner"))
    assert helpers.code(rig.call("g6_improve_enrol")) == runner.ENROLLED
    assert helpers.code(rig.call("g6_improve_start")) == improve.STARTED
    reply = rig.call("g6_improve_enrol")
    reopened = CycleLog(rig.root / CYCLE_LOG)
    rows = reopened.enrolments()
    assert len(rows) == 2 and rows[0] == rows[1], "two separate enrolments reached the durable ledger"
    admission = runner._admission(rig.root, runner.read_settings(rig.root))
    assert admission[0] == ENROLMENT_CHANGED, (
        f"runner still admits the earlier start after a second MCP enrolment; "
        f"admission={admission[0]!r}, second enrolment reply={helpers.code(reply)}, recorded={reply.get('recorded')}")
    assert reply["recorded"] == {"the enrolment": True}
