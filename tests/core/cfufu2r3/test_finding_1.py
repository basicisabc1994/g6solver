"""A receipt-id failure must not prevent the independent stop gate standing."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope


def test_entropy_failure_before_legacy_identity_still_revokes_the_approved_start(monkeypatch):
    # Avoid pytest's mode-0700 temp directories, which this Windows sandbox cannot reopen.
    workspace = Path.cwd() / ".review" / "data" / uuid4().hex[:10]
    workspace.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location(
        "review_binding_fixture", Path.cwd() / "tests" / "core" / "test_consent_binding.py")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    rig = fixture.Rig(workspace, monkeypatch)
    assert rig.call("g6_improve_enrol")["code"] == "G6_E_LOOP_ENROLLED"
    monkeypatch.setattr(improve, "_spawn", lambda *a: object())
    monkeypatch.setattr(improve, "_watch", lambda *a: _envelope(
        "qualified-draft", improve.STARTED, "fake runner; no process started"))
    assert rig.call("g6_improve_start")["code"] == improve.STARTED
    root, settings, server = rig.root, rig.settings, rig.server
    assert runner._gate(root, settings) == ("", "")

    def no_entropy(size):
        raise OSError("OS entropy unavailable")

    raised = None
    with monkeypatch.context() as patch:
        patch.setattr(os, "urandom", no_entropy)
        try:
            server.tools["g6_improve_stop"](folder=str(root), reason="please stop")
        except OSError as exc:
            raised = str(exc)

    assert runner._gate(root, settings)[1], (
        f"stop raised {raised!r} before establishing any denial gate; the earlier approval remains executable"
    )
