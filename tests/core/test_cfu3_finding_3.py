"""A prior stop's timestamp must not make a failed new stop look durable."""
from datetime import datetime as RealDatetime
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.self_maintenance import envelope


def test_old_note_at_repeated_time_cannot_claim_a_failed_stop(monkeypatch):
    checkout = Path(__file__).resolve().parents[2]
    folder = checkout / ".review" / "tmp" / f"finding3-{uuid4().hex}"
    folder.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("finding3_helpers", checkout / "tests/core/test_consent_binding.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    rig = helpers.Rig(folder, monkeypatch)
    monkeypatch.setattr(improve, "_spawn", lambda *args: SimpleNamespace(poll=lambda: None))
    monkeypatch.setattr(improve, "_watch", lambda *args: _envelope("qualified-draft", improve.STARTED, "fake runner"))
    assert helpers.code(rig.call("g6_improve_enrol")) == runner.ENROLLED
    assert helpers.code(rig.call("g6_improve_start")) == improve.STARTED

    class RepeatedClock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return helpers.START

    # Wall-clock timestamps can repeat; both stops receive the same timestamp.
    monkeypatch.setattr(improve, "datetime", RepeatedClock)
    assert improve.stop(str(rig.root), "first stop")["code"] == improve.STOP_ASKED
    assert helpers.code(rig.call("g6_improve_start")) == improve.STARTED
    before = rig.log()._rows()
    assert not envelope.what_stops(rig.root, rig.log().started())[1]

    def refuse_write(*args, **kwargs):
        raise PermissionError("both stop files refuse this stop's writes")

    monkeypatch.setattr(envelope, "open", refuse_write, raising=False)
    reply = improve.stop(str(rig.root), "second stop")
    reopened = rig.log()
    assert reopened._rows() == before
    assert reopened.start_in_force() and not envelope.what_stops(rig.root, reopened.started())[1]
    assert reply["code"] == improve.STOP_FAILED, (
        f"failed stop reported {reply['code']} using an earlier stop's note; "
        f"the current start remains admitted; recorded={reply['recorded']}")
