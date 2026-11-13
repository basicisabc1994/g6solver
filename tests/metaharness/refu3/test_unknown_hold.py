"""A reply of unknown outcome is its own fact, in the latch, the cycle row and the words, and is barred as a refusal is
(review refufu2r4, finding 2). Fakes only: no model, no host program, no network."""
from __future__ import annotations

import ast
import inspect
import json
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.core import host_models, stop
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness import reflex
from g6.metaharness.loop import CLASSIFIER_STOP, ShadowLoop, _Counted, _Refused, _Requests
from g6.metaharness.shadow import Held
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, note_refusal, refusal_rows, refused_requests
from g6.self_maintenance.usage import HOST_UNKNOWN, MODEL_REFUSED, limit_ends, refused_by

NOW = datetime(2026, 11, 6, tzinfo=UTC)
UNREAD = f"{HOST_UNKNOWN}: host result has an unrecognised status; these inputs stay barred"
REFUSED = f"{MODEL_REFUSED}: refusal"
HELD = f"{HOST_UNKNOWN} held: the outcome of an earlier send of these inputs is unknown"


def _handle(root):
    handle = ShadowLoop.__new__(ShadowLoop)
    handle.root = root
    handle.log = CycleLog(root / CYCLE_LOG)
    handle.unreadable = ""
    handle.requests = _Requests(halted=handle._halted, keeping=handle._keep_refusal, on_record=handle._on_record)
    return handle


# The latch ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("said,latched,held_in", [(UNREAD, "hold", "unknown"), (REFUSED, "refusal", "refused")])
def test_a_hold_and_a_refusal_are_each_latched_as_what_they_are_and_both_end_the_tick(said, latched, held_in):
    meter = _Requests()
    meter.open(10)
    generation = meter.generation
    with pytest.raises(_Refused):
        meter.at_a_refusal(said, "request")
    other = "refusal" if latched == "hold" else "hold"
    assert getattr(meter, latched) == {"said": said, "request": "request"} and getattr(meter, other) is None
    assert getattr(meter, held_in) == {"request"} and not getattr(meter, "refused" if held_in == "unknown" else "unknown")
    with pytest.raises(_Refused):                         # nothing more of the tick goes out, whichever it was
        meter.take(identity="another request")
    with pytest.raises(_Refused):
        meter.recheck(generation)
    meter.close()
    assert getattr(meter.closed, latched) == {"said": said, "request": "request"} and getattr(meter.closed, other) is None
    assert meter.hold is None and meter.refusal is None   # the next tick starts with neither
    assert "hold" in _Requests.TICK_STATE


# The cycle row ------------------------------------------------------------------------------------------------------------

def _tick(root, monkeypatch, results):
    """One ``ShadowLoop._run`` around a cycle that sends one request through Claude Code for each of ``results``."""
    launches = []

    @contextmanager
    def folders():
        base = root / "host" / uuid4().hex
        work, files = base / "work", base / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        return 0, json.dumps(results[len(launches) - 1]), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-host")
    monkeypatch.setattr(stop, "engaged", lambda: None)
    folder = root / "loop"
    folder.mkdir()
    loop = ShadowLoop.__new__(ShadowLoop)
    loop.root, loop.log, loop.waited, loop.unreadable = folder, CycleLog(folder / CYCLE_LOG), 0, ""
    loop.requests = _Requests(now=lambda: NOW, halted=loop._halted, keeping=loop._keep_refusal, on_record=loop._on_record)
    loop._now, loop._beat = lambda: NOW, lambda *args, **kwargs: None
    loop.domain = SimpleNamespace(name="fake", sealed=lambda: SimpleNamespace(vault=None))
    loop._reserve = lambda: (SimpleNamespace(provider="claude", amount=10, ends=NOW + timedelta(days=1)), None)
    loop.meter = SimpleNamespace(
        settle=lambda admission, charged: charged, limit_reached=lambda provider, until: None,
        reading=lambda provider, now: SimpleNamespace(provider=provider, day=1, used=0, target=10, ceiling=10,
                                                      estimated=False))
    loop._row = lambda: {"at": NOW.isoformat(), "domain": "fake", "outcome": "idle", "code": loop_module.CYCLE,
                         "reason": ""}
    # what ``_run`` reads of a handle since the freeze follow-up joined it: no chain of adoptions and no row written yet,
    # and nothing judges a bare loop, so the cycle's last check holds the reading it was given
    loop.mh = SimpleNamespace(adoption_rows=lambda: [])
    loop._written, loop._met, loop._began = False, None, None
    loop._still_frozen = lambda: Held({})

    def cycle(recommended):
        caught = None
        for number in range(len(results)):
            try:
                _Counted(usage.WatchedClaude(), loop.requests).complete("system", f"request {number}")
            except BaseException as raised:               # a wrapper that swallows what a send raised: the latch decides
                caught = caught or raised
        if caught is not None:
            raise caught
        return loop._row()

    loop._cycle = cycle
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    return loop, loop._run(), launches


def test_a_tick_that_met_an_unknown_outcome_is_recorded_as_that_and_still_pauses_the_loop(tmp_path, monkeypatch):
    loop, reply, launches = _tick(tmp_path, monkeypatch, [{"type": "result", "result": "answer"}])
    row = loop.log.cycles()[-1]
    assert len(launches) == 1 and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == HOST_UNKNOWN == row["code"] and row["outcome"] == "blocked"
    assert "classifier_stop" not in row and "classifier or a permission" not in row["reason"]
    assert row["unknown_hold"] == {"said": UNREAD, "request": row["unknown_hold"]["request"]}
    assert row["reason"].startswith("the outcome of a send is unknown (") and "a person reads this" in row["reason"]
    assert loop._must_stop() == row["reason"] == loop.log.paused(), "the loop no longer pauses for a person on an unknown outcome"
    held = row["unknown_hold"]["request"]
    assert held in loop.requests.unknown and held not in loop.requests.refused
    assert held in refused_requests(loop.root) and (held, UNREAD) in refusal_rows(loop.root)   # barred after a restart too
    assert reflex._POINTS_AT[HOST_UNKNOWN] == reflex.STOP == reflex._POINTS_AT[CLASSIFIER_STOP]


def test_a_tick_that_met_both_is_a_classifiers_stop_and_keeps_the_hold_beside_it(tmp_path, monkeypatch):
    loop, reply, launches = _tick(tmp_path, monkeypatch, [
        {"type": "result", "result": "answer"}, {"type": "result", "is_error": True, "stop_reason": "refusal"}])
    row = loop.log.cycles()[-1]
    # the first send ended the tick: the second is turned away at admission by the latch and never launched
    assert len(launches) == 1 and row["code"] == HOST_UNKNOWN and "classifier_stop" not in row
    meter = _Requests()
    meter.open(10)
    for said, request in ((UNREAD, "one"), (REFUSED, "two")):
        with pytest.raises(_Refused):
            meter.at_a_refusal(said, request)
    meter.close()
    assert meter.closed.refusal == {"said": REFUSED, "request": "two"} and meter.closed.hold == {"said": UNREAD, "request": "one"}
    # what a tick met is put on its row by ``_carried`` since the freeze follow-up joined the refusals one
    carried = next(n for n in ast.walk(ast.parse(inspect.getsource(loop_module)))
                   if isinstance(n, ast.FunctionDef) and n.name == "_carried")
    stated = next(n for n in ast.walk(carried) if isinstance(n, ast.If) and ast.unparse(n.test) == "refusal is not None")
    assert [ast.unparse(n.test) for n in stated.orelse if isinstance(n, ast.If)] == ["hold is not None"], (
        "a stated refusal outranks a hold")
    assert any(ast.unparse(n) == "row['unknown_hold'] = hold" for n in ast.walk(ast.Module(stated.body, []))), (
        "a classifier's stop keeps the hold beside it on the row")


def test_a_cycle_the_log_would_not_take_stops_the_folder_for_a_hold_as_for_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    handle = _handle(tmp_path)
    handle.waited = 0

    def full(row):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(handle.log, "cycle", full)
    reply = handle._record({"outcome": "blocked", "code": HOST_UNKNOWN, "reason": "r",
                            "unknown_hold": {"said": UNREAD, "request": "a" * 64}})
    assert reply["warning_card"]["code"] == loop_module.CANNOT_RECORD
    assert handle.cannot_record.startswith("the outcome of a send is unknown (") and "refused" not in handle.cannot_record


# The words at admission ---------------------------------------------------------------------------------------------------

def _turned_away(meter, identity) -> str:
    with pytest.raises(LLMError) as raised:
        meter.take(identity=identity)
    assert not meter.in_flight
    return str(raised.value)


def test_held_inputs_are_turned_away_as_held_on_every_handle_and_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    first, second = _handle(tmp_path), _handle(tmp_path)
    held, refused = "a" * 64, "b" * 64
    first.requests.at_a_refusal(UNREAD, held)
    first.requests.at_a_refusal(REFUSED, refused)
    assert dict(refusal_rows(tmp_path)) == {held: UNREAD, refused: REFUSED}
    restarted = _handle(tmp_path)
    restarted._hold_what_is_on_record()
    assert restarted.requests.unknown == {held} and restarted.requests.refused == {refused}
    for handle in (first, second, restarted):             # the one that met it, another of the folder, and one made after
        said = _turned_away(handle.requests, held)
        assert said.startswith(HELD) and "refused" not in said and CLASSIFIER_STOP not in said
        assert _turned_away(handle.requests, refused) == f"{CLASSIFIER_STOP}: this request was refused once, and nothing sends it again"


def test_a_stated_refusal_outranks_a_hold_of_the_same_request_wherever_each_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    both = "c" * 64
    assert note_refusal(tmp_path, both, UNREAD) and note_refusal(tmp_path, both, REFUSED)
    handle = _handle(tmp_path)
    assert loop_module._kinds(refusal_rows(tmp_path)) == ({both}, set())
    assert handle._on_record() == {both}                  # no mark: the store holds a refusal of it
    assert _turned_away(handle.requests, both).startswith(CLASSIFIER_STOP)
    meter = _Requests(on_record=lambda: {both, loop_module._marked(both)})
    meter.refused.add(both)                               # this meter met the refusal; another's store holds only the hold
    assert _turned_away(meter, both).startswith(CLASSIFIER_STOP)
    plain = _Requests(on_record=lambda: {both})           # a store that says nothing of the kind: as before, a refusal
    assert _turned_away(plain, both).startswith(CLASSIFIER_STOP)


def test_a_hold_only_a_cycle_row_kept_is_held_after_a_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    held, refused, asked = "d" * 64, "e" * 64, "f" * 64
    log = CycleLog(tmp_path / CYCLE_LOG)
    log.cycle({"outcome": "blocked", "code": HOST_UNKNOWN, "reason": "r", "unknown_hold": {"said": UNREAD, "request": held}})
    log.cycle({"outcome": "blocked", "code": CLASSIFIER_STOP, "reason": "r",
               "classifier_stop": {"said": REFUSED, "request": refused, "asked": asked}})
    handle = _handle(tmp_path)
    assert refused_requests(tmp_path) == set()            # refused.log holds neither: only the cycles do
    handle._hold_what_is_on_record()
    assert handle.requests.unknown == {held} and handle.requests.refused == {refused, asked}
    assert _turned_away(handle.requests, held).startswith(HELD)
    assert _turned_away(handle.requests, refused).startswith(CLASSIFIER_STOP)


def test_the_loops_own_bar_is_never_read_as_a_providers_statement():
    """Read from the code: ``_not_sent`` has two outcomes, and neither begins as a statement ``refused_by`` or
    ``limit_ends`` reads. So a held input sent again in a tick is a failed request, as a refused one is: the tick goes on."""
    function = next(n for n in ast.parse(inspect.getsource(loop_module)).body
                    if isinstance(n, ast.FunctionDef) and n.name == "_not_sent")
    assert len([n for n in ast.walk(function) if isinstance(n, ast.Return)]) == 2
    meter = _Requests()
    meter.unknown.add("held")
    said = [str(loop_module._not_sent("held", {"held"}, [meter])), str(loop_module._not_sent("refused", {"refused"}, [meter])),
            str(loop_module._not_sent("marked", {"marked", loop_module._marked("marked")}, [meter]))]
    assert said[0] == said[2] and said[0].startswith(HELD) and said[1].startswith(CLASSIFIER_STOP)
    assert all(refused_by(text) == "" and limit_ends(text, NOW) is None for text in said)
    request = usage.request_digest("system", "held request")
    meter.unknown.add(request)
    meter.open(10)
    backend = FakeLLM(default="an answer")
    with pytest.raises(LLMError, match=HELD):
        _Counted(backend, meter).complete("system", "held request")
    assert backend.calls == [] and meter.hold is None and meter.refusal is None
    assert _Counted(backend, meter).complete("system", "another request") == "an answer"   # the tick goes on


def test_the_store_reader_gives_each_row_with_what_was_said_and_marks_only_a_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(stop, "engaged", lambda: None)
    held, refused = "a" * 64, "b" * 64
    assert refusal_rows(tmp_path) == [] and refused_requests(tmp_path) == set()
    assert note_refusal(tmp_path, held, UNREAD) and note_refusal(tmp_path, refused, REFUSED)
    with (tmp_path / "refused.log").open("ab") as torn:
        torn.write(b"c" * 30)                             # a row a crash cut short is no digest
    assert refusal_rows(tmp_path) == [(held, UNREAD), (refused, REFUSED)]
    assert refused_requests(tmp_path) == {held, refused}
    handle = _handle(tmp_path)
    handle._here.refused.add("held by this process alone")
    assert handle._on_record() == {held, refused, "held by this process alone", loop_module._marked(held)}
    assert loop_module._marked(held) != held and len(loop_module._marked(held)) != len(held)


# Claude Code out of turns ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", [0, 1])
def test_a_claude_code_result_out_of_turns_with_its_error_stated_is_a_failed_case_and_no_hold(tmp_path, monkeypatch, code,
                                                                                              consented_provider_fakes):
    """The shape this repository's own stand-ins give Claude Code's result when ``--max-turns`` runs out: the subtype
    ``error_max_turns`` with ``is_error`` true and no text. It is a failed case, which may be sent again, and holds nothing.
    Whether a real Claude Code states ``is_error`` there is not settled by any code here."""
    result = {"type": "result", "subtype": "error_max_turns", "is_error": True, "num_turns": 6}
    assert usage._event_outcome("claude", result) == ("", "failed")
    assert usage._classify_reply("claude", code, json.dumps(result))[0] == "failed"
    launches = []

    @contextmanager
    def folders():
        base = tmp_path / "host" / uuid4().hex
        work, files = base / "work", base / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *args):
        launches.append(stdin)
        return code, json.dumps(result), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-host")
    meter = _Requests()
    meter.open(10)
    caller = _Counted(usage.WatchedClaude(), meter)
    for _ in range(2):
        with pytest.raises(LLMError, match=host_models.HOST_FAILED):
            caller.complete("system", "request")
    assert len(launches) == 2 and not meter.unknown and not meter.refused and meter.hold is None and meter.refusal is None
