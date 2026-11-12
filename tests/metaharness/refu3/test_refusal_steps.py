"""Nothing a refusal must do waits on a step that can fail before it (review refufu2r4, finding 1).

Every call the keeper's path makes, found by running it, is failed in turn: with an ``OSError``, with an error never
seen, and with an interrupt. After each the refusal is on the disk or the folder is stopped, and the meter that met it
holds it. Fakes only: no model, no host program, no network."""
from __future__ import annotations

import ast
import inspect
import sys
import threading

import pytest

from g6.core import stop
from g6.core.llm import LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import ShadowLoop, _Refused, _Requests
from g6.self_maintenance import envelope, usage
from g6.self_maintenance.envelope import CYCLE_LOG, STOP_FILE, CycleLog, refused_requests


class Unseen(Exception):
    """An error no code here names."""


def _handle(root):
    handle = ShadowLoop.__new__(ShadowLoop)
    handle.root = root
    handle.log = CycleLog(root / CYCLE_LOG)
    handle.unreadable = ""
    handle.requests = _Requests(halted=handle._halted, keeping=handle._keep_refusal, on_record=handle._on_record)
    return handle


def _free(lock) -> bool:
    got = []
    probe = threading.Thread(target=lambda: got.append(lock.acquire(timeout=2) and (lock.release() or True)))
    probe.start()
    probe.join(5)
    return got == [True]


#: The functions between "a refusal is known to a keeper" and "it is durable or the folder is stopped".
KEEPING = (ShadowLoop._keep_refusal, ShadowLoop._found, ShadowLoop._stop_folder, envelope.note_refusal)
#: The stop itself: a fault here comes after the write has already failed or no record was found, so it is a second one.
STOPPING = ("_stop_folder",)


@pytest.mark.parametrize("error", [OSError, Unseen, KeyboardInterrupt])
@pytest.mark.parametrize("write", ["kept", "fails"])
def test_each_call_of_the_keepers_path_failed_in_turn_leaves_the_refusal_durable_or_the_folder_stopped(
        tmp_path, monkeypatch, write, error):
    """Each call ``_keep_refusal``, ``_found``, ``_stop_folder`` and ``note_refusal`` make is failed once: a function of
    Python's as it begins and as it returns, a built-in one as it returns, which is where an interrupt is delivered and
    where a call that failed is seen. (Raising as a built-in call begins would skip a lock's own release, which nothing
    real does.) ``fails``: the disk also refuses the write, so the injected fault is a second one, and what must hold then
    is the stop unless the fault was in the stop itself. An interrupt that takes the write itself is no failed write: what
    became of the row is not known, so the request is held by the meter and the folder's record and no stop is asked for,
    as before this change."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    if write == "fails":
        def fsync(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(envelope.os, "fsync", fsync)
    targets = {function.__code__ for function in KEEPING}
    events = ("call", "return", "c_return")
    sites, number = [], 0
    while True:
        folder = tmp_path / f"{number}"
        folder.mkdir()
        handle = _handle(folder)
        handle.requests.open(10)
        identity = f"{number:064d}"
        seen, fired = [0], []

        def profile(frame, event, arg, number=number, seen=seen, fired=fired):
            if fired or event not in events:
                return
            inside = frame.f_code in targets
            below = frame.f_back is not None and frame.f_back.f_code in targets
            if (event.startswith("c_") and inside) or (not event.startswith("c_") and below):
                seen[0] += 1
                if seen[0] == number + 1:
                    where = frame if event.startswith("c_") else frame.f_back
                    called = getattr(arg, "__qualname__", "") if event.startswith("c_") else frame.f_code.co_name
                    fired.append((where.f_code.co_name in STOPPING or called in STOPPING,
                                  "note_refusal" in (where.f_code.co_name, called),
                                  f"{where.f_code.co_name}:{where.f_lineno} {event} {called}"))
                    raise error(fired[0][2])

        sys.setprofile(profile)
        try:
            handle.requests.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", identity)
        except (error, _Refused):
            pass
        finally:
            sys.setprofile(None)
        if not fired:
            break                                         # every call of the path has been failed once
        in_the_stop, in_the_write, site = fired[0]
        sites.append(site)
        assert not loop_module._RECORD.writing, f"a pending write is left after {site}"
        assert _free(loop_module._LOCK) and _free(loop_module._PUBLICATIONS), site
        assert identity in handle.requests.refused, f"the meter that met the refusal does not hold it after {site}"
        durable = identity in refused_requests(folder)
        stopped = bool(handle.cannot_record) or (folder / STOP_FILE).exists()
        if error is KeyboardInterrupt and in_the_write:
            assert identity in handle._here.refused, f"the folder's record does not hold it after {site}"
        elif write == "kept" or not in_the_stop:
            assert durable or stopped, f"neither on the disk nor stopped after {site}"
        number += 1
    names = {site.split(":")[0] for site in sites}
    assert {"_keep_refusal", "_found", "note_refusal"} <= names, f"the profile does not see the keeper's path: {sorted(names)}"
    assert write == "kept" or "_stop_folder" in names, sites
    assert number > 24, f"only {number} calls were failed ({sites})"


def test_the_keepers_path_is_the_functions_the_code_names():
    """Read from the code: the functions the fault run covers are every function of the loop the keeper reaches before the
    refusal is durable or the folder stopped, and the one writer it calls."""
    tree = ast.parse(inspect.getsource(loop_module))
    shadow = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ShadowLoop")
    members = {n.name: n for n in shadow.body if isinstance(n, ast.FunctionDef)
               and not any(ast.unparse(d).endswith(".setter") for d in n.decorator_list)}
    reached, pending, called = set(), ["_keep_refusal"], set()
    while pending:
        name = pending.pop()
        reached.add(name)
        for n in ast.walk(members[name]):
            if isinstance(n, ast.Attribute) and ast.unparse(n.value) == "self" and n.attr in members and n.attr not in reached:
                pending.append(n.attr)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                called.add(n.func.id)
    assert reached == {"_keep_refusal", "_found", "_stop_folder", "_here"}      # ``_here``: one call of the record's lookup
    assert {function.__name__ for function in KEEPING} == (reached - {"_here"}) | {"note_refusal"}
    # What they call that is no member: the writer, the stop file, and words made from plain strings.
    assert called == {"note_refusal", "ask_to_stop", "_writable", "_stopped_by"}
    # The keeper raises nothing of its own before the write: its first statement after the names is the write's ``try``.
    keep = members["_keep_refusal"]
    steps = [n for n in keep.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    assert isinstance(steps[-1], ast.Try) and len(steps) == 3
    assert not [n for step in steps[:-1] for n in ast.walk(step) if isinstance(n, ast.Call)]
    stop_folder = members["_stop_folder"]
    asked = [n for n in ast.walk(stop_folder) if isinstance(n, ast.Try) and "ask_to_stop(self.root, why)" in ast.unparse(n.body)]
    latch = [n for n in ast.walk(stop_folder) if isinstance(n, ast.Try) and "here.cannot_record = why" in ast.unparse(n.body)]
    assert asked and latch and "ask_to_stop(self.root, why)" in ast.unparse(latch[0].finalbody), (
        "the folder is asked to stop whatever the record did")


@pytest.mark.parametrize("lookups_that_fail", [1, 2])
@pytest.mark.parametrize("write", ["kept", "fails"])
def test_a_lookup_that_fails_is_a_write_that_failed(tmp_path, monkeypatch, write, lookups_that_fail):
    """The folder cannot be opened for the keeper's one lookup, or for that and the one after it. The write is tried
    whatever the lookup did. One failed lookup is tried once more and then holds as ever; with no record found the folder is
    asked to stop, which needs none, though the write took the refusal."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    handle, other = _handle(tmp_path), _handle(tmp_path)
    record = handle._here
    if write == "fails":
        monkeypatch.setattr(loop_module, "note_refusal", lambda root, request, said: False)
    left, original = [lookups_that_fail], loop_module._RECORD.folder

    def folder(root):
        if left[0]:
            left[0] -= 1
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: loop folder cannot be opened safely")
        return original(root)

    monkeypatch.setattr(loop_module._RECORD, "folder", folder)
    identity = "a" * 64
    handle.requests.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", identity)
    assert left == [0]
    durable, asked = identity in refused_requests(tmp_path), (tmp_path / STOP_FILE).exists()
    assert durable == (write == "kept")
    assert (identity in record.refused) == (lookups_that_fail == 1)
    assert asked == (write == "fails" or lookups_that_fail == 2), "a folder with no record found, or no row kept, is stopped"
    assert bool(record.cannot_record) == (write == "fails" and lookups_that_fail == 1)
    with pytest.raises(LLMError):                           # barred by the store, or halted by the stop
        other.requests.take(identity=identity)
    assert not other.requests.in_flight


@pytest.mark.parametrize("error", [OSError, Unseen, KeyboardInterrupt])
@pytest.mark.parametrize("raising", ["first", "second", "both"])
def test_every_target_meters_keeper_is_tried_when_one_raises(error, raising):
    """A send charged to two meters is refused. Each meter's keeper is called whatever the other's raised, both meters hold
    the request, and the first failure leaves once both have been tried."""
    kept = []

    def keeper(name):
        def keeping(request, said):
            kept.append(name)
            if raising in (name, "both"):
                raise error(f"{name} keeper failed")
        return keeping

    outer, inner = _Requests(keeping=keeper("first")), _Requests(keeping=keeper("second"))
    outer.open(10)
    inner.open(10)
    call = loop_module._Call(object(), outer, outer.generation, lambda: True)
    token = loop_module._ACTIVE.set((call,))
    try:
        record = inner.take(identity="request")
        assert [meter for meter, _ in record.charged] == [outer, inner]
        with pytest.raises(error) as raised:
            inner.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", record)
    finally:
        loop_module._ACTIVE.reset(token)
        inner.release(record)
    assert kept == ["first", "second"]
    assert str(raised.value) == ("second" if raising == "second" else "first") + " keeper failed"
    assert "request" in outer.refused and "request" in inner.refused
    assert outer.refusal is not None and inner.refusal is not None
    assert not loop_module._RECORD.writing and _free(loop_module._LOCK) and _free(loop_module._PUBLICATIONS)


def test_a_keeper_is_called_when_the_meters_own_sets_cannot_be_read(monkeypatch):
    """Whether a meter already holds the request is read under the lock to spare a second write. When that read fails, the
    keeper is called all the same: keeping twice is the safe direction."""
    kept = []
    meter = _Requests(keeping=lambda request, said: kept.append(request))
    meter.open(10)
    native = meter._lock
    entered = []

    class FailsOnce:
        def __enter__(self):
            entered.append(sys._getframe(1).f_code.co_name)
            if entered == ["at_a_refusal", "at_a_refusal"]:      # the first is the registration; the second reads the sets
                raise Unseen("the meter's sets could not be read")
            return native.__enter__()

        def __exit__(self, *args):
            return native.__exit__(*args)

    meter._lock = FailsOnce()
    with pytest.raises(Unseen):
        meter.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", "request")
    meter._lock = native
    assert kept == ["request"] and "request" in meter.refused and not loop_module._RECORD.writing
