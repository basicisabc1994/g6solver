"""Review refufu2r4, finding 2: a reply of unknown outcome is written to the loop's records as a classifier's refusal.

The commit makes a Claude Code result that names no ``is_error`` an unknown outcome. The classifier keeps it apart from a
refusal (``_classify_reply`` gives "unknown"), but the tick does not: the hold goes through ``at_a_refusal``, is latched as
the tick's ``refusal``, and ``ShadowLoop._run`` writes the cycle as ``G6_E_LOOP_CLASSIFIER_STOP`` with ``classifier_stop``
and "a classifier or a permission stopped a request". A later send of the same inputs is told "this request was refused
once". No classifier and no permission said anything. Fakes only: no model, no host program, no network."""
from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.core import host_models
from g6.core.llm import LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import CLASSIFIER_STOP, ShadowLoop, _Counted, _Requests
from g6.metaharness.shadow import Held
from g6.self_maintenance import usage
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _fake_host(root, monkeypatch, result):
    launches = []

    @contextmanager
    def folders():
        base = root / "host" / uuid4().hex
        work, files = base / "work", base / "files"
        work.mkdir(parents=True)
        files.mkdir()
        yield work, files

    def run(argv, stdin, cwd, files, *args):
        launches.append((list(argv), stdin))
        return 0, json.dumps(result), ""

    monkeypatch.setattr(host_models, "_folders", folders)
    monkeypatch.setattr(host_models, "_environment", lambda *args: {})
    monkeypatch.setattr(host_models.key_file, "read", lambda: {})
    monkeypatch.setattr(host_models, "_run", run)
    monkeypatch.setattr(host_models.ClaudeCodeCaller, "locate", lambda self: "fake-host")
    return launches


def _tick(root, monkeypatch, result):
    """One ``ShadowLoop._run`` around a cycle that sends one request through Claude Code, which prints ``result``."""
    launches = _fake_host(root, monkeypatch, result)
    folder = root / "loop"
    folder.mkdir()
    loop = ShadowLoop.__new__(ShadowLoop)
    loop.root, loop.log, loop.waited, loop.cannot_record = folder, CycleLog(folder / CYCLE_LOG), 0, ""
    loop.requests = _Requests(now=lambda: NOW)
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
    answers = []

    def cycle(recommended):
        answers.append(_Counted(usage.WatchedClaude(), loop.requests).complete("system", "request"))
        return loop._row()

    loop._cycle = cycle
    monkeypatch.setattr(loop_module, "sealed_closed", lambda vault: nullcontext([]))
    reply = loop._run()
    return loop, reply, launches, answers


def test_control_a_stated_answer_ends_the_tick_as_no_refusal(tmp_path, monkeypatch):
    loop, reply, launches, answers = _tick(tmp_path, monkeypatch, {"type": "result", "is_error": False, "result": "answer"})
    assert answers == ["answer"] and len(launches) == 1
    assert reply["warning_card"]["code"] != CLASSIFIER_STOP and loop.requests.closed.refusal is None


def test_control_a_refusal_claude_code_states_is_recorded_as_one(tmp_path, monkeypatch):
    loop, reply, launches, answers = _tick(tmp_path, monkeypatch,
                                           {"type": "result", "is_error": True, "stop_reason": "refusal"})
    assert answers == [] and len(launches) == 1
    assert reply["warning_card"]["code"] == CLASSIFIER_STOP
    assert loop.log.cycles()[-1]["classifier_stop"]["said"].startswith(usage.MODEL_REFUSED)


def test_control_a_result_with_no_status_is_never_an_answer_and_its_inputs_are_not_sent_again(tmp_path, monkeypatch,
                                                                                            consented_provider_fakes):
    launches = _fake_host(tmp_path, monkeypatch, {"type": "result", "result": "answer"})
    assert usage._classify_reply("claude", 0, json.dumps({"type": "result", "result": "answer"}))[0] == "unknown"
    caller, answers = _Counted(usage.WatchedClaude(), _Requests()), []
    for _ in range(2):
        try:
            answers.append(caller.complete("system", "request"))
        except LLMError:
            pass
    assert answers == [] and len(launches) == 1


def test_a_reply_of_unknown_outcome_is_not_recorded_as_a_classifiers_refusal(tmp_path, monkeypatch):
    loop, reply, launches, answers = _tick(tmp_path, monkeypatch, {"type": "result", "result": "answer"})
    assert answers == [] and len(launches) == 1               # held, as the commit rules: never an answer
    row = loop.log.cycles()[-1]
    assert reply["warning_card"]["code"] != CLASSIFIER_STOP and "classifier_stop" not in row, (
        "the cycle says a classifier or a permission stopped a request, and no provider said so: "
        f"code={reply['warning_card']['code']} reason={row.get('reason')!r}")


def test_inputs_held_for_an_unknown_outcome_are_not_said_to_have_been_refused(tmp_path, monkeypatch, consented_provider_fakes):
    _fake_host(tmp_path, monkeypatch, {"type": "result", "result": "answer"})
    caller = _Counted(usage.WatchedClaude(), _Requests())
    with pytest.raises(LLMError, match=usage.HOST_UNKNOWN):
        caller.complete("system", "request")
    with pytest.raises(LLMError) as again:
        caller.complete("system", "request")
    assert "was refused" not in str(again.value), f"an unread reply is reported as a refusal: {again.value}"
