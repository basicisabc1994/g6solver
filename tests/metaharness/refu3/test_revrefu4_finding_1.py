"""Review refufu2r4, finding 1: a refusal whose folder record cannot be found for one moment is kept nowhere.

``ShadowLoop._keep_refusal`` finds the folder's record (``self._here``) before it tries any file. When that one lookup
fails, nothing is written to ``refused.log``, the folder's set does not get the request, and the folder is not stopped: only
the meter that met the refusal holds it. Another handle of the same folder then sends the refused request, and so does a
restart. The commit states one cost for this method (an interrupt between a failed write and the ``finally``); this is a
second one, on an ordinary ``Exception`` path. Fakes only: no model, no host program, no network."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from g6.core import stop
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import ShadowLoop, _Counted, _Requests
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CYCLE_LOG, STOP_FILE, CycleLog, refused_requests


def _handle(root):
    """A handle wired as ``ShadowLoop.__init__`` wires one: the stop reader, the keeper, and the store with the folder."""
    handle = ShadowLoop.__new__(ShadowLoop)
    handle.root = root
    handle.log = CycleLog(root / CYCLE_LOG)
    handle.unreadable = ""
    handle.requests = _Requests(halted=handle._halted, keeping=handle._keep_refusal,
                                on_record=lambda: refused_requests(handle.root) | handle._here.refused)
    return handle


def _refuse_once(root, monkeypatch, *, fault: bool):
    """Handle one meets a refusal of ("system", "refused request"); with ``fault``, the folder cannot be opened for the one
    lookup the keeper makes. Returns the two handles of the folder."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    first, second = _handle(root), _handle(root)
    armed, original = [], loop_module._open_input

    @contextmanager
    def open_input(path, **kwargs):
        if armed:
            armed.clear()
            raise OSError(24, "Too many open files")     # one failed open, as a handle limit gives
        with original(path, **kwargs) as handle:
            yield handle

    monkeypatch.setattr(loop_module, "_open_input", open_input)

    def refuses(system, user):
        if fault:
            armed.append(True)                            # admission has passed: the next folder lookup is the keeper's
        raise LLMError(f"{usage.MODEL_REFUSED}: policy")

    with pytest.raises(LLMError):
        _Counted(FakeLLM(refuses), first.requests).complete("system", "refused request")
    assert not armed, "the fault was not reached: the fixture did not get to the keeper's lookup"
    return first, second


def test_control_without_the_fault_the_refusal_is_durable_and_the_other_handle_refuses_it(tmp_path, monkeypatch):
    first, second = _refuse_once(tmp_path, monkeypatch, fault=False)
    request = usage.request_digest("system", "refused request")
    assert request in refused_requests(tmp_path) and request in first._here.refused
    backend = FakeLLM(default="sent again")
    with pytest.raises(LLMError, match=loop_module.CLASSIFIER_STOP):
        _Counted(backend, second.requests).complete("system", "refused request")
    assert backend.calls == []


def test_a_refusal_is_kept_when_the_folders_record_cannot_be_found_for_one_lookup(tmp_path, monkeypatch):
    first, second = _refuse_once(tmp_path, monkeypatch, fault=True)
    request = usage.request_digest("system", "refused request")
    assert request in first.requests.refused               # the meter that met it holds it, as the commit says
    durable = request in refused_requests(tmp_path)
    stopped = bool(first.cannot_record) or (tmp_path / STOP_FILE).exists()
    backend = FakeLLM(default="sent again")
    try:
        _Counted(backend, second.requests).complete("system", "refused request")
    except LLMError:
        pass
    assert backend.calls == [], (
        "another handle of the folder sent the refused request again: the keeper's folder lookup failed once, so "
        f"refused.log was never tried (durable={durable}), the folder's set has no hold "
        f"({request in second._here.refused}) and the folder was not stopped (stopped={stopped})")
    assert durable or stopped, "a restart would send the refused request again: it is on no file and nothing stops the folder"


def test_a_refusal_no_file_would_keep_still_stops_the_folder_when_the_second_lookup_fails(tmp_path, monkeypatch):
    """The same lookup a second time: after a write that failed, ``self.cannot_record = why`` finds the folder's record
    again instead of using the one already found, and ``ask_to_stop`` comes after it. When that lookup fails, the folder is
    not stopped and no STOP file is asked for, though the refusal is on no file."""
    monkeypatch.setattr(stop, "engaged", lambda: None)
    first = _handle(tmp_path)
    armed, original = [], loop_module._open_input

    @contextmanager
    def open_input(path, **kwargs):
        if armed:
            armed.clear()
            raise OSError(24, "Too many open files")
        with original(path, **kwargs) as handle:
            yield handle

    def no_room(root, request, said):
        armed.append(True)                                # the write failed: the next folder lookup is the stop's
        return False

    monkeypatch.setattr(loop_module, "_open_input", open_input)
    monkeypatch.setattr(loop_module, "note_refusal", no_room)

    def refuses(system, user):
        raise LLMError(f"{usage.MODEL_REFUSED}: policy")

    with pytest.raises(LLMError):
        _Counted(FakeLLM(refuses), first.requests).complete("system", "refused request")
    assert not armed, "the fault was not reached"
    request = usage.request_digest("system", "refused request")
    assert request not in refused_requests(tmp_path) and request in first._here.refused
    assert first.cannot_record or (tmp_path / STOP_FILE).exists(), (
        "the refusal is on no file, and the folder is neither stopped in this process nor asked to stop: its other "
        "requests go on, and a restart sends the refused one again")
