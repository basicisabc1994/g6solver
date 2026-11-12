"""Review cfufu2r4, finding 1, on the 0.3 integration line: a stop whose persistent writes all fail must still deny work in
a process that was already running before it, not only in the server that was asked.

The reviewer's probe built its handle as the loop's stop reader read one before the refusals follow-up joined it; the
joined reader (``ShadowLoop._halted``) also reads the handle's note of a store it could not read (``unreadable``), so the
probe here gives its handle that note, empty, as ``ShadowLoop.__init__`` does. The child is an import and read probe
only: it starts no runner, tick, model or network, and writes no state."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from g6.mcp import improve
from g6.metaharness import runner
from g6.self_maintenance import envelope as e
from tests.core.test_consent_binding import Rig

PROBE = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop, _Requests
from g6.self_maintenance import envelope as e
root = Path(sys.argv[2])
settings = runner.read_settings(root)
loop = SimpleNamespace(root=root, cannot_record="", unreadable="", log=e.CycleLog(root / e.CYCLE_LOG))
loop._stop = lambda: ShadowLoop._stop(loop)
requests = _Requests(halted=lambda: ShadowLoop._halted(loop))
requests.open(10)
for line in sys.stdin:
    if line.strip() == "quit":
        break
    admitted, why = False, ""
    try:
        requests.take()  # the real pre-request gate; sends no request
        admitted = True
    except BaseException as refused:
        why = f"{type(refused).__name__}: {refused}"
    print(json.dumps({"gate": runner._gate(root, settings), "tick_stop": ShadowLoop._stop(loop), "admitted": admitted,
                      "why": why}), flush=True)
'''


@pytest.fixture
def pending_stops():
    previous = dict(e._STOP_PENDING)
    e._STOP_PENDING.clear()
    yield
    e._STOP_PENDING.clear()
    e._STOP_PENDING.update(previous)


def test_a_stop_whose_writes_all_fail_denies_a_child_that_was_already_running(tmp_path, monkeypatch, pending_stops):
    rig = Rig(tmp_path, monkeypatch)
    assert rig.call("g6_improve_enrol")["code"] == runner.ENROLLED
    monkeypatch.setattr(improve, "_spawn", lambda *a: object())
    monkeypatch.setattr(improve, "_watch", lambda *a: improve._envelope("qualified-draft", improve.STARTED, "fake runner"))
    assert rig.call("g6_improve_start")["code"] == improve.STARTED
    root, settings = rig.root, rig.settings
    assert runner._gate(root, settings) == ("", "")
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c", PROBE, str(Path(e.__file__).resolve().parents[2]), str(root)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             encoding="utf-8", env=improve._environment())
    try:
        child.stdin.write("before\n")
        child.stdin.flush()
        before = json.loads(child.stdout.readline())
        assert before == {"gate": ["", ""], "tick_stop": ["", ""], "admitted": True, "why": ""}, before
        original_mkdir, original_low_open = Path.mkdir, os.open

        def refused_directory(path, *args, **kwargs):
            if path == root / e.STOP_RECORDS:
                raise PermissionError("cannot reserve a canonical stop")
            return original_mkdir(path, *args, **kwargs)

        def refused_marker(path, *args, **kwargs):
            if Path(path).name == e.STOP_DENIED:
                raise PermissionError("cannot persist the fallback denial")
            return original_low_open(path, *args, **kwargs)

        def refused_count(*args, **kwargs):
            raise PermissionError("cannot append the fallback denial count")

        with monkeypatch.context() as patch:
            patch.setattr(Path, "mkdir", refused_directory)
            patch.setattr(os, "open", refused_marker)
            patch.setattr(e, "open", refused_count, raising=False)
            reply = rig.server.tools["g6_improve_stop"](folder=str(root), reason="stop the existing run")
        assert reply["code"] == improve.STOP_FAILED
        assert runner._gate(root, settings)[1], "the server itself keeps its stop"
        child.stdin.write("after\nquit\n")
        child.stdin.flush()
        out, err = child.communicate(timeout=20)
        assert child.returncode == 0, err
        after = json.loads(out)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=20)
    assert after["admitted"] is False, f"the child that was already running still admits work after the stop: {after!r}"
    assert after["gate"][1] and after["tick_stop"][0], after
