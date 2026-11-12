"""One successful stop must not clear a different in-flight stop's refusal latch."""
import asyncio
import builtins
import importlib.util
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from g6.mcp import improve
from g6.metaharness import runner
from g6.metaharness.api import _envelope
from g6.self_maintenance import envelope as e


def test_overlapping_stop_cannot_lose_its_process_latch(monkeypatch):
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

    entered, release = threading.Event(), threading.Event()
    original_mkdir, original_open, ordinary_open = Path.mkdir, os.open, builtins.open
    result = {}

    def is_first():
        return threading.current_thread() is first

    def mkdir(path, *args, **kwargs):
        if is_first() and path == root / e.STOP_RECORDS:
            entered.set()
            if not release.wait(15):
                raise TimeoutError("test did not release the first stop")
            raise PermissionError("first stop cannot reserve its canonical directory")
        return original_mkdir(path, *args, **kwargs)

    def low_open(path, *args, **kwargs):
        if is_first() and Path(path).name == e.STOP_DENIED:
            raise PermissionError("first stop cannot write its fallback marker")
        return original_open(path, *args, **kwargs)

    def opened(path, mode="r", *args, **kwargs):
        if is_first() and Path(path).name == e.STOPS_FILE and mode == "a":
            raise PermissionError("first stop cannot append its fallback count")
        return ordinary_open(path, mode, *args, **kwargs)

    def stopping():
        try:
            result["reply"] = server.tools["g6_improve_stop"](folder=str(root), reason="first stop")
        except BaseException as exc:
            result["exception"] = repr(exc)

    first = threading.Thread(target=stopping)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(os, "open", low_open)
    monkeypatch.setattr(e, "open", opened, raising=False)
    first.start()
    try:
        assert entered.wait(15), "first stop never reached its canonical reservation"
        second = server.tools["g6_improve_stop"](folder=str(root), reason="second stop")
        assert second["canonical_stop_id"]
        assert (root / e.STOP_RECORDS / second["canonical_stop_id"] / "stop.json").is_file()

        async def agree(**kwargs):
            return SimpleNamespace(action="accept", content={"value": "Ada"})

        context = SimpleNamespace(session=SimpleNamespace(elicit_form=agree), request_id="review")
        started = asyncio.run(server.tools["g6_improve_start"](ctx=context, folder=str(root)))
    finally:
        release.set()
        first.join(20)
    assert not first.is_alive(), "test's stop thread did not finish"
    assert "exception" not in result, result
    assert result["reply"]["code"] == improve.STOP_FAILED
    assert runner._gate(root, settings)[1], (
        f"first stop failed, but the second stop cleared its latch and admission remains open "
        f"after the new dialog returned {started['code']}"
    )
