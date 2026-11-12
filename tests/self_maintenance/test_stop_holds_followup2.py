# SPDX-License-Identifier: Apache-2.0
"""The stop's second follow-up, on the merge of the stop's and the consent's follow-ups: two roots of "a stop fails".

1. **A start decides on every reading it takes once its row is written, whole** (review-stopfu4 finding 1,
   ``failure_read_as_success``). The reading of what landed was used for the records' bytes alone, so a stop it reported,
   one that could not be read among them, was dropped, and a start whose stop file was repaired a moment later spawned.
   Every reading after the row is now one value, decided whole: a stop in it, of any code, revokes the start durably
   and refuses.
2. **A stop's denial reaches every process that admits work** (review-cfufu2r4 finding 1,
   ``guard_covers_less_than_it_guards``). A stop that could write nothing denied only in the server's own memory, so a
   runner already alive went on admitting requests. A stop now keeps the stop lock for as long as it has written no
   durable record, and every path that admits work asks for that lock before it reads the stop (``envelope.admission``).

Each root has an inventory read from the code, and a table of every admission path against every way a stop arrives or
fails to be written.
"""
from __future__ import annotations

import ast
import contextvars
import inspect
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import stop as g6_stop
from g6.core.ledger import digest
from g6.mcp import improve
from g6.metaharness import loop as loop_module
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop, _Requests
from g6.self_maintenance import envelope

STOP_UNREADABLE = "G6_E_LOOP_STOP_UNREADABLE"
READERS = ("stop_in_force", "admission")


@pytest.fixture
def active(tmp_path, monkeypatch):
    """A loop's folder a person enrolled and started through the dialog's own functions, with a runner that is a fake."""
    root = tmp_path / "loop"
    root.mkdir()
    domain = tmp_path / "domain.py"
    domain.write_text("# Inert; never executed.\n", encoding="utf-8")
    settings = {"domain_file": str(domain), "read_account": False, "windows": [
        {"provider": "claude", "allowance": 1000000, "week_start": "2026-10-26T08:00:00+00:00"}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    envelope.CycleLog(root / envelope.CYCLE_LOG).enrol("Ada", {"domain_file": envelope.file_digest(domain)}, domain="review")
    spawned = []
    monkeypatch.setattr(improve, "_spawn", lambda *args: spawned.append(args) or object())
    monkeypatch.setattr(improve, "_watch", lambda *args: improve._envelope("qualified-draft", improve.STARTED, "a fake runner"))
    monkeypatch.setattr(g6_stop, "engaged", lambda: None)
    held = dict(envelope._STOP_PENDING)
    envelope._STOP_PENDING.clear()
    yield SimpleNamespace(root=root, settings=settings, spawned=spawned)
    envelope._STOP_PENDING.clear()          # and with them any stop lock a failed stop still holds
    envelope._STOP_PENDING.update(held)


def _started(f) -> dict:
    shown = improve.asked(str(f.root))
    assert not isinstance(shown, dict), shown
    reply = improve.start(f.root, "Ada", shown[2])
    assert reply["code"] == improve.STARTED and len(f.spawned) == 1, reply
    assert runner._gated(f.root, f.settings)[:2] == ("", "")
    return shown[2]


# Root 1: every reading after the row, decided whole ------------------------------------------------------------------

def _unreadable_for_one_reading(f, monkeypatch, name: str, index: int) -> list:
    """Make ``name`` in the folder a directory for the ``index``-th reading the start takes once its row is written, and
    repair it as soon as that reading is taken: what a file locked for a moment, or mended by a person, looks like."""
    real_start, seen, after_row = envelope.CycleLog.start, [], []

    def row_then_count(self, *args, **kwargs):
        written = real_start(self, *args, **kwargs)
        after_row.append(True)
        return written

    def reading(real):
        def taken(root, *args, **kwargs):
            if not after_row:
                return real(root, *args, **kwargs)
            mine = len(seen)
            seen.append(None)
            if mine != index:
                return real(root, *args, **kwargs)
            (f.root / name).mkdir()
            try:
                found = real(root, *args, **kwargs)
            finally:
                (f.root / name).rmdir()
            seen[mine] = found
            return found
        return taken

    monkeypatch.setattr(envelope.CycleLog, "start", row_then_count)
    monkeypatch.setattr(improve, "stop_in_force", reading(improve.stop_in_force))
    return seen


@pytest.mark.parametrize("name", [envelope.STOP_FILE, envelope.STOPS_FILE])
@pytest.mark.parametrize("index", [0, 1], ids=["the reading of what landed", "the reading after it"])
def test_a_start_refuses_on_any_reading_after_its_row_that_reports_a_stop_and_stays_revoked(active, monkeypatch, name, index):
    shown = improve.asked(str(active.root))[2]
    seen = _unreadable_for_one_reading(active, monkeypatch, name, index)
    reply = improve.start(active.root, "Ada", shown)
    assert len(seen) > index and seen[index].code == STOP_UNREADABLE, "the reading was never taken unreadable"
    assert active.spawned == [], "the start spawned after a reading of its own reported a stop"
    assert reply["completion_state"] == "blocked-escalated" and reply["code"] == reply["stop_code"] == STOP_UNREADABLE, reply
    after = envelope.stop_in_force(active.root)         # the file is repaired by now: the revocation is what stands
    assert after and after.unread == (), after
    assert runner._gated(active.root, active.settings)[0]


def test_a_start_whose_reading_of_what_landed_reports_a_stop_asked_for_is_revoked_too(active, monkeypatch):
    """A stop that is readable is decided the same way: one landing between the row and that reading ends the start."""
    shown = improve.asked(str(active.root))[2]
    real_start = envelope.CycleLog.start

    def row_then_a_stop(self, *args, **kwargs):
        written = real_start(self, *args, **kwargs)
        (active.root / envelope.STOP_FILE).write_bytes(b"a person wrote this by hand\n")
        return written

    monkeypatch.setattr(envelope.CycleLog, "start", row_then_a_stop)
    reply = improve.start(active.root, "Ada", shown)
    assert active.spawned == [] and reply["code"] == improve.START_STOPPED and reply["stop_code"] == envelope.STOPPED, reply
    (active.root / envelope.STOP_FILE).unlink()          # the file removed by hand: the start stays ended on record
    assert envelope.stop_in_force(active.root).revoked


def _after_the_row(function) -> tuple[list[ast.Call], ast.FunctionDef]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function))).body[0]
    row = min(node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "writer.start")
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) in READERS
            and node.lineno > row], tree


def _decided_whole(tree: ast.AST, call: ast.Call) -> bool:
    """Whether the reading ``call`` takes is bound to a name whose stop decision an ``if`` tests: the name itself, as a
    truth value, or its ``code``. A reading used in place, or only for one of its fields, is not decided whole."""
    outer = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "admission"
             and call in node.args]
    if outer:                                           # a reading handed to ``admission`` is decided through it
        return _decided_whole(tree, outer[0])
    bound = [node.targets[0].id for node in ast.walk(tree) if isinstance(node, ast.Assign) and node.value is call
             and isinstance(node.targets[0], ast.Name)]
    if len(bound) != 1:
        return False
    name = bound[0]

    def decides(test: ast.AST) -> bool:
        if isinstance(test, ast.Name):
            return test.id == name
        if isinstance(test, ast.Attribute):
            return isinstance(test.value, ast.Name) and test.value.id == name and test.attr == "code"
        if isinstance(test, ast.BoolOp):
            return any(decides(value) for value in test.values)
        if isinstance(test, ast.UnaryOp):
            return decides(test.operand)
        if isinstance(test, ast.Compare):
            return decides(test.left)
        return False

    return any(decides(node.test) for node in ast.walk(tree) if isinstance(node, (ast.If, ast.IfExp)))


def test_every_reading_a_start_takes_after_its_row_is_decided_whole():
    calls, tree = _after_the_row(improve.start)
    assert len(calls) >= 3, "the start reads what landed, and again, as an admission, once its locks are let go"
    undecided = [f"line {call.lineno}: {ast.unparse(call)}" for call in calls if not _decided_whole(tree, call)]
    assert undecided == [], undecided
    old = ast.parse("def start(root):\n    writer.start(who)\n    if not _appended_onto(stop_in_force(root), reading):\n"
                    "        return 1\n    halted = stop_in_force(root)\n    if halted:\n        return 2\n").body[0]
    found = [node for node in ast.walk(old) if isinstance(node, ast.Call) and ast.unparse(node.func) in READERS]
    assert [_decided_whole(old, call) for call in sorted(found, key=lambda c: c.lineno)] == [False, True]   # the old shape


# Root 2: a stop's denial reaches every process that admits work ------------------------------------------------------

PROBE = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from g6.core import stop
from g6.metaharness import runner
from g6.metaharness.loop import ShadowLoop, _Requests
stop.engaged = lambda: None                      # G6's own stop is another matter: this asks about the folder's
for line in sys.stdin:
    root = Path(line.strip())
    loop = SimpleNamespace(root=root, cannot_record="")
    loop._stop = lambda: ShadowLoop._stop(loop)
    requests = _Requests(halted=lambda: ShadowLoop._halted(loop))
    requests.open(10)
    admitted = False
    try:
        requests.take()                          # the real gate before a request; it sends none
        admitted = True
    except BaseException:
        pass
    try:
        gate = runner._gate(root, json.loads((root / "settings.json").read_text(encoding="utf-8")))
    except OSError as exc:
        gate = ["unread", type(exc).__name__]
    print(json.dumps({"gate": gate, "tick": ShadowLoop._stop(loop), "admitted": admitted}), flush=True)
'''


class Child:
    """A process of its own that only reads: the runner's gate, a tick's stop and the gate before a request."""

    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", PROBE, str(Path(envelope.__file__).resolve().parents[2])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=improve._environment())

    def probe(self, root: Path) -> dict:
        self.process.stdin.write(f"{root}\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, self.process.stderr.read()
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=20)


@pytest.fixture(scope="module")
def running_child():
    child = Child()
    yield child
    child.close()


def _refused(monkeypatch, *, records=False, marker=False, lines=False, flush=False) -> None:
    """Refuse a stop's writes: its canonical record's folder, the marker that stands in for it, every row it adds to a
    file (the journal and the ``STOP`` file), or every flush to disk."""
    mkdir, low_open, flushed = Path.mkdir, os.open, os.fsync

    def no_folder(path, *args, **kwargs):
        if records and path.name == envelope.STOP_RECORDS:
            raise PermissionError("the canonical stop cannot be reserved")
        return mkdir(path, *args, **kwargs)

    def no_marker(path, *args, **kwargs):
        if marker and Path(path).name == envelope.STOP_DENIED:
            raise PermissionError("the marker cannot be made")
        return low_open(path, *args, **kwargs)

    def no_line(path, *args, **kwargs):
        if Path(path).name in (envelope.STOP_FILE, envelope.STOPS_FILE):
            raise PermissionError("the row cannot be written")
        return open(path, *args, **kwargs)

    def no_flush(handle):
        raise OSError("the disk refused the flush")

    monkeypatch.setattr(Path, "mkdir", no_folder)
    monkeypatch.setattr(os, "open", no_marker)
    if lines:
        monkeypatch.setattr(envelope, "open", no_line, raising=False)
    if flush:
        monkeypatch.setattr(os, "fsync", no_flush if flush else flushed)


def _folder_gone(f, monkeypatch) -> None:
    os.rename(f.root, f.root.with_name("gone"))


#: Every way a stop arrives or fails to be written, with what is refused while it is asked for.
ARRIVALS = {
    "its record, its line and its file written": lambda f, m: None,
    "only its count written": lambda f, m: _refused(m, records=True, marker=True),
    "every write refused": lambda f, m: _refused(m, records=True, marker=True, lines=True),
    "no write flushed to disk": lambda f, m: _refused(m, flush=True),
    "its folder gone": _folder_gone,
}


def _tick(root: Path):
    loop = SimpleNamespace(root=root, cannot_record="")
    loop._stop = lambda: ShadowLoop._stop(loop)
    return loop


def _in_a_copied_context(act):
    """``act`` on another thread, in a copy of this context: what a worker of the same server sees."""
    got = []
    worker = threading.Thread(target=lambda: got.append(contextvars.copy_context().run(act)))
    worker.start()
    worker.join(30)
    return got[0]


#: Every path by which work is admitted, in this process: each gives a true value when it refuses.
PATHS = {
    "the runner's gate": lambda f, shown: runner._gated(f.root, f.settings)[0],
    "a tick's guard": lambda f, shown: ShadowLoop._halted(_tick(f.root)) is not None,
    "the gate before a request": lambda f, shown: _taken(f.root),
    "a start on the agreement from before the stop": lambda f, shown: _not_started(f, shown),
    "the runner's gate in a copied context": lambda f, shown: _in_a_copied_context(
        lambda: runner._gated(f.root, f.settings)[0]),
}


def _taken(root: Path) -> bool:
    requests = _Requests(halted=lambda: ShadowLoop._halted(_tick(root)))
    requests.open(10)
    try:
        requests.take()
    except BaseException:  # noqa: BLE001 - a stop met at a request is on its way out of the tick as a BaseException
        return True
    return False


def _not_started(f, shown) -> bool:
    before = len(f.spawned)
    reply = improve.start(f.root, "Ada", shown)
    return reply["completion_state"] == "blocked-escalated" and len(f.spawned) == before


@pytest.mark.parametrize("arrival", list(ARRIVALS))
def test_every_admission_path_refuses_after_a_stop_however_it_arrived(active, monkeypatch, running_child, arrival):
    shown = _started(active)
    assert running_child.probe(active.root) == {"gate": ["", ""], "tick": ["", ""], "admitted": True}
    with monkeypatch.context() as fault:
        ARRIVALS[arrival](active, fault)
        reply = improve.stop(str(active.root), "stop the run")
    assert reply["completion_state"] in ("qualified-draft", "blocked-escalated")
    refused = {path: bool(asks(active, shown)) for path, asks in PATHS.items()}
    assert all(refused.values()), f"admitted in this process after a stop ({arrival}): {refused}"
    already = running_child.probe(active.root)          # alive before the stop: it holds no part of the server's memory
    assert already["admitted"] is False and already["gate"][1] and already["tick"][1], (arrival, already)
    restarted = Child()                                 # started after the stop, as a restart is
    try:
        after = restarted.probe(active.root)
    finally:
        restarted.close()
    assert after["admitted"] is False and after["gate"][1] and after["tick"][1], (arrival, after)


def test_a_stop_that_wrote_nothing_holds_the_stop_lock_until_its_process_lets_go(active, monkeypatch, running_child):
    _started(active)
    with monkeypatch.context() as fault:
        _refused(fault, records=True, marker=True, lines=True)
        reply = improve.stop(str(active.root), "stop the run")
    assert reply["code"] == improve.STOP_FAILED, reply
    held = running_child.probe(active.root)
    assert held["admitted"] is False and held["gate"][0] == STOP_UNREADABLE and envelope.STOP_LOCK in held["gate"][1], held
    assert not list((active.root).glob(envelope.STOP_RECORDS + "/*")) and not (active.root / envelope.STOP_FILE).exists()
    envelope._STOP_PENDING.clear()                      # the server's process ends: its lock goes with its request
    with envelope.stop_lock(active.root, wait=0):
        pass


def test_a_stop_that_wrote_its_record_lets_the_stop_lock_go(active, running_child):
    _started(active)
    assert improve.stop(str(active.root), "stop the run")["code"] == improve.STOP_ASKED
    with envelope.stop_lock(active.root, wait=0):       # nothing holds it: the record on disk is what refuses
        pass
    found = running_child.probe(active.root)
    assert found["admitted"] is False and found["gate"][0] == envelope.STOPPED, found


def test_a_folder_no_stop_or_start_ever_locked_is_read_with_nothing_written(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    before = sorted(p.name for p in root.iterdir())
    assert envelope.admission(root, envelope.stop_in_force(root)).code == ""
    assert sorted(p.name for p in root.iterdir()) == before


#: Every function that decides an admission (``envelope.admission``, on the reading it has just taken), by module, with
#: the path it is.
ADMITS = {
    "g6/metaharness/loop.py": {"_stop": "a tick's guard, the gate before each request, and a runner's pass"},
    "g6/metaharness/runner.py": {"run": "the runner's preflight, a restart's among them",
                                 "_gated": "the runner's gate, before any of the folder's code runs"},
    "g6/mcp/improve.py": {"asked": "the start's dialog", "start": "the start, before its locks and once they are let go"},
}
#: Every other function there that takes a reading (``stop_in_force``) and does not hand it to ``admission``, each with
#: why it admits nothing.
READS_ONLY = {
    "g6/metaharness/loop.py": {"_reloaded": "says what a reload that failed is; it refuses"},
    "g6/metaharness/runner.py": {},
    "g6/mcp/improve.py": {
        "_failed": "the refusal of a dialog or a start that failed", "_watch": "the refusal of a runner that ended",
        "_terms": "a binding made with no reading given: a comparison, which starts nothing",
        "start": "under the tick lock and the stop lock, which this process then holds itself",
        "_start_on_record": "a start's receipt, read back", "status": "status, which only reports",
        "_unread_status": "status, which only reports"},
}


def _callers(path: Path, name: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(call, ast.Call) and ast.unparse(call.func) == name for call in ast.walk(node)):
                found.add(node.name)
    return found


def _bare_readings(path: Path) -> set[str]:
    """The functions in ``path`` with a reading (``stop_in_force``) that is not handed straight to ``admission``."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            handed = {id(arg) for call in ast.walk(node) if isinstance(call, ast.Call)
                      and ast.unparse(call.func) == "admission" for arg in call.args}
            if any(isinstance(call, ast.Call) and ast.unparse(call.func) == "stop_in_force" and id(call) not in handed
                   for call in ast.walk(node)):
                found.add(node.name)
    return found


def test_every_path_that_admits_work_takes_the_admission_reading():
    package = Path(envelope.__file__).resolve().parents[2]
    for path, names in ADMITS.items():
        assert _callers(package / path, "admission") == set(names), (path, _callers(package / path, "admission"))
    for path, names in READS_ONLY.items():
        readers = _callers(package / path, "stop_in_force")
        assert readers == set(names) | set(ADMITS[path]), (path, sorted(readers))
        bare = _bare_readings(package / path)
        assert bare == set(names), (path, sorted(bare))
    halted = textwrap.dedent(inspect.getsource(ShadowLoop._halted))
    assert "self._stop()" in halted                                  # the gate before a request is the tick's stop
    assert "self._halted" in inspect.getsource(loop_module.ShadowLoop._refused)
    assert "self.loop._stop()" in inspect.getsource(runner.Runner.step)
    source = textwrap.dedent(inspect.getsource(envelope.admission))
    assert "_stop_held(" in source and "stop_lock(" in inspect.getsource(envelope._stop_held)   # the lock a stop keeps
    writer = textwrap.dedent(inspect.getsource(envelope._ask_to_stop))
    assert writer.index("stop_lock(") < writer.index("_establish_stop(")   # the durable write is made under that lock


def test_the_settings_a_start_binds_are_the_ones_the_gate_reads(active):
    _started(active)
    assert envelope.stop_in_force(active.root).start["settings"] == digest(active.settings)
