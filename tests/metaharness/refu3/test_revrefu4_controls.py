"""Review refufu2r4: probes that pass. Each is an attack on a claim of the commit that found nothing, kept so that a
fixture which could not fail is not taken for a clean result. Fakes only: no model, no host program, no network."""
from __future__ import annotations

import json
import sys
import threading

import pytest

from g6.core import stop
from g6.core.llm import LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import ShadowLoop, _Refused, _Requests
from g6.self_maintenance import envelope, usage
from g6.self_maintenance.envelope import CYCLE_LOG, STOP_FILE, CycleLog, ask_to_stop, refused_requests


def _handle(root):
    handle = ShadowLoop.__new__(ShadowLoop)
    handle.root = root
    handle.log = CycleLog(root / CYCLE_LOG)
    handle.unreadable = ""
    handle.requests = _Requests(halted=handle._halted, keeping=handle._keep_refusal,
                                on_record=lambda: refused_requests(handle.root) | handle._here.refused)
    return handle


def _free(lock) -> bool:
    """Whether another thread can take ``lock`` now (an ``RLock`` its owner holds would let its owner in again)."""
    got = []
    probe = threading.Thread(target=lambda: got.append(lock.acquire(timeout=2) and (lock.release() or True)))
    probe.start()
    probe.join(5)
    return got == [True]


# Claim 2: a refusal another handle of the folder kept is never behind an admission -----------------------------------------

def test_no_send_is_registered_once_another_handle_holds_its_refusal(tmp_path, monkeypatch):
    """300 races of one handle's refusal against another handle's send of the same request. Under the admission lock, as
    the send's place is taken, the folder either does not hold the request yet (the send left first, which is allowed) or
    the send is refused. Never both held and let out."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    refusing, sending = _handle(tmp_path), _handle(tmp_path)
    folder = sending._here
    held_at_charge: dict[str, bool] = {}
    original = loop_module._RECORD.charge

    def charge(meter, generation, places):
        if meter is sending.requests and places:
            held_at_charge[threading.current_thread().name] = threading.current_thread().name in folder.refused
        return original(meter, generation, places)

    monkeypatch.setattr(loop_module._RECORD, "charge", charge)
    let_out, refused, wrong = 0, 0, []
    for index in range(300):
        identity = f"{index:064d}"
        start = threading.Barrier(2)
        outcome = []

        def refuse():
            start.wait(5)
            refusing.requests.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", identity)

        def send():
            start.wait(5)
            try:
                outcome.append(sending.requests.take(identity=identity))
            except LLMError as failed:
                outcome.append(failed)

        threads = [threading.Thread(target=refuse), threading.Thread(target=send, name=identity)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        assert not any(thread.is_alive() for thread in threads), "a refusal and a send of the same request wait on each other"
        assert len(outcome) == 1
        if isinstance(outcome[0], usage.Send):
            let_out += 1
            if held_at_charge.get(identity):
                wrong.append(identity)
            sending.requests.release(outcome[0])
        else:
            refused += 1
            assert str(outcome[0]).startswith(loop_module.CLASSIFIER_STOP)
        assert identity in refused_requests(tmp_path) and identity in folder.refused
    assert not wrong, f"{len(wrong)} sends left while the folder already held their refusal"
    assert let_out + refused == 300 and refused, (let_out, refused)
    assert not loop_module._RECORD.writing and _free(loop_module._LOCK)


# Claim 4: nothing waits on itself --------------------------------------------------------------------------------------------

def test_refusals_stops_and_every_tick_step_on_two_handles_end(tmp_path, monkeypatch):
    """Eight threads on two handles of one folder: sends, refusals kept on the disk, a refusal whose write fails (so the
    folder stops and a STOP file is written), tick openings, closings, rechecks and settlements. All of them end."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    handles = [_handle(tmp_path), _handle(tmp_path)]
    real_note = loop_module.note_refusal
    monkeypatch.setattr(loop_module, "note_refusal",
                        lambda root, request, said: False if request.endswith("f") else real_note(root, request, said))
    errors: list[BaseException] = []

    def work(seed: int) -> None:
        handle = handles[seed % 2]
        meter = handle.requests
        try:
            for step in range(60):
                identity = f"{seed:02d}{step:061d}{'f' if (seed, step) == (7, 56) else '0'}"   # 7 + 56: a refusal
                kind = (seed + step) % 6
                try:
                    if kind == 0:
                        meter.open(1000)
                    elif kind == 1:
                        meter.close()
                    elif kind == 2:
                        meter.recheck(meter.generation)
                    elif kind == 3:
                        meter.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", identity)
                    elif kind == 4:
                        call = loop_module._Call(object(), meter, None, lambda: True)
                        meter.begin(call)
                        meter.settle((call,), lambda: 0)
                    else:
                        record = meter.take(identity=identity)
                        if record is not None:
                            meter.release(record)
                except (LLMError, _Refused, loop_module._Halted, loop_module._AtLimit):
                    pass
        except BaseException as failed:  # noqa: BLE001 - reported below
            errors.append(failed)

    threads = [threading.Thread(target=work, args=(seed,)) for seed in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(90)
    assert not [thread for thread in threads if thread.is_alive()], "threads still waiting: a lock or a pending write is held"
    assert not errors, errors
    assert not loop_module._RECORD.writing and _free(loop_module._LOCK) and _free(loop_module._PUBLICATIONS)
    assert handles[0].cannot_record and handles[0].cannot_record == handles[1].cannot_record
    assert (tmp_path / STOP_FILE).exists()
    with pytest.raises((LLMError, loop_module._Halted)) as after:     # in a tick the stop leaves as _Halted, outside as LLMError
        handles[1].requests.take(identity="a request after the folder stopped")
    # this process's own stop for the folder says so by its own code (``ShadowLoop._stop``), as the freeze and stop follow-ups rule
    assert loop_module.CANNOT_RECORD in f"{getattr(after.value, 'code', '')} {after.value}"


# Claim 1: one interrupt, at any line of the refusal's path, leaves nothing pending and the request held ------------------

def _nested(function, name):
    return next(const for const in function.__code__.co_consts if getattr(const, "co_name", "") == name)


@pytest.mark.parametrize("write", ["kept", "fails"])
def test_one_interrupt_at_any_call_of_a_refusals_path_leaves_no_pending_write_and_the_request_held(tmp_path, monkeypatch, write):
    """A ``KeyboardInterrupt`` raised, in turn, as each call made by ``at_a_refusal``, its ``finish``, ``_keep_refusal`` and
    ``note_refusal`` begins or returns, which is where the interpreter delivers one. (An interrupt raised by a line trace at
    a ``try:`` lands on an instruction no handler covers and that nothing real can interrupt: the first form of this probe
    did that, and reported a pending write that no run could leave.) After each: no pending write, both locks free, and
    the request held by the meter, or on the disk, or the folder stopped."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    if write == "fails":
        def fsync(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(envelope.os, "fsync", fsync)
    targets = {_Requests.at_a_refusal.__code__, _nested(_Requests.at_a_refusal, "finish"),
               ShadowLoop._keep_refusal.__code__, envelope.note_refusal.__code__}
    lost, sites, before, number = [], [], [], 0
    while True:
        folder = tmp_path / f"{number}"
        folder.mkdir()
        handle = _handle(folder)
        handle.requests.open(10)
        identity = f"{number:064d}"
        seen, fired, begun = [0], [], []

        def profile(frame, event, arg, number=number, seen=seen, fired=fired, begun=begun):
            if fired:
                return
            if loop_module._RECORD.writing and not begun:
                begun.append(True)                        # the pending write is registered: the refusal's keeping has begun
            inside = frame.f_code in targets
            below = frame.f_back is not None and frame.f_back.f_code in targets
            if (event == "c_return" and inside) or (event in ("call", "return") and below):
                seen[0] += 1
                if seen[0] == number + 1:
                    where = frame if inside and event == "c_return" else frame.f_back
                    called = getattr(arg, "__qualname__", "") if event == "c_return" else frame.f_code.co_name
                    fired.append(f"{where.f_code.co_name}:{where.f_lineno} {event} {called}")
                    raise KeyboardInterrupt(fired[0])

        sys.setprofile(profile)
        try:
            handle.requests.at_a_refusal(f"{usage.MODEL_REFUSED}: policy", identity)
        except (KeyboardInterrupt, _Refused):
            pass
        finally:
            sys.setprofile(None)
        if not fired:
            break                                         # every call of the path has been interrupted once
        sites.append(fired[0])
        assert not loop_module._RECORD.writing, f"a pending write is left after an interrupt at {fired[0]}"
        assert _free(loop_module._LOCK) and _free(loop_module._PUBLICATIONS), fired[0]
        held = identity in handle.requests.refused
        durable = identity in refused_requests(folder)
        stopped = bool(handle.cannot_record) or (folder / STOP_FILE).exists()
        if begun and not (held or durable or stopped):
            lost.append(fired[0])
        if not begun:                                     # an interrupt before the keeping began is a death before the refusal
            before.append(fired[0])                       # was read: nothing of it is held, on the base commit either
        number += 1
    assert number > 30, f"only {number} calls were interrupted: the profile does not see the refusal's path ({sites})"
    assert before and all(site.startswith("at_a_refusal:") for site in before) and len(before) < 16, before
    for name in ("at_a_refusal", "finish", "_keep_refusal", "note_refusal"):
        assert any(site.startswith(name + ":") for site in sites), f"no call of {name} was interrupted"
    assert not lost, f"the refused request is held nowhere after one interrupt at: {lost}"


# Claim 3: an unknown outcome is never an answer, and never clears a refusal ------------------------------------------------

@pytest.mark.parametrize("status", ["missing", None, "false", 0, [], {}])
def test_an_unrecognised_status_never_answers_and_never_outranks_a_refusal(status):
    unknown = {"type": "result", "result": "answer"} | ({} if status == "missing" else {"is_error": status})
    answer = {"type": "result", "is_error": False, "result": "answer"}
    refusal = {"type": "result", "is_error": True, "stop_reason": "refusal"}
    limit = {"type": "result", "is_error": True, "api_error_status": 429}

    def kind(*events, code=0):
        return usage._classify_reply("claude", code, "\n".join(map(json.dumps, events)))[0]

    for code in (0, 1):
        assert kind(unknown, code=code) == "unknown"
        assert kind(answer, unknown, code=code) == kind(unknown, answer, code=code) == "unknown"
        assert kind(refusal, unknown, code=code) == kind(unknown, refusal, code=code) == "refusal"
        # A limit beside an unread result is held as unknown: the tick still ends, on the hold (see the next test).
        assert kind(limit, unknown, code=code) == kind(unknown, limit, code=code) == "unknown"


def test_a_limit_beside_an_unread_result_still_ends_the_tick(tmp_path, monkeypatch, consented_provider_fakes):
    from contextlib import contextmanager
    from uuid import uuid4

    from g6.core import host_models
    from g6.metaharness.loop import _Counted

    printed = "\n".join(map(json.dumps, [{"type": "result", "is_error": True, "api_error_status": 429},
                                         {"type": "result", "result": "answer"}]))
    launches = []

    @contextmanager
    def folders():
        base = tmp_path / uuid4().hex
        (base / "work").mkdir(parents=True)
        (base / "files").mkdir()
        yield base / "work", base / "files"

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        return 1, printed, ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-host")
    meter = _Requests()
    meter.open(10)
    caller = _Counted(usage.WatchedClaude(), meter)
    with pytest.raises(_Refused):
        caller.complete("system", "request")
    with pytest.raises(_Refused):
        caller.complete("system", "another request")       # nothing more of the tick goes out
    # Changed on purpose from the review's copy, which read the hold from ``meter.refusal``: finding 2 of the same review
    # gives a reply of unknown outcome a latch of its own. The tick ends as it did.
    assert len(launches) == 1 and meter.limited is None and meter.hold is not None and meter.refusal is None


# Claim 2, the stated limit: a STOP file is seen at an admission's next read, and at the read after any wait ----------------

def test_a_stop_file_written_while_a_send_waits_for_an_identical_one_is_read_before_it_leaves(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    handle = _handle(tmp_path)
    meter = handle.requests
    first = meter.take(identity="identical request", watched=True, owner=object())
    waiting, outcome = threading.Event(), []
    real_wait = meter._settled.wait

    def wait(*args, **kwargs):
        waiting.set()
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(meter._settled, "wait", wait)

    def send():
        try:
            outcome.append(meter.take(identity="identical request", watched=True, owner=object()))
        except LLMError as failed:
            outcome.append(failed)

    worker = threading.Thread(target=send)
    worker.start()
    assert waiting.wait(10)
    ask_to_stop(tmp_path, "a person stops the folder")
    meter.release(first)
    worker.join(20)
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], LLMError) and str(outcome[0]).startswith(loop_module.STOPPED)
