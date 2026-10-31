# SPDX-License-Identifier: Apache-2.0
"""Each finding of the cross-family review of the loop, as the input that showed it. Each was seen failing first."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from g6.core import llm
from g6.core.llm import EmptyRetry, FakeLLM, FallbackCaller, LLMError
from g6.metaharness import Model
from g6.metaharness.loop import LoopSettings, ShadowLoop
from g6.metaharness.method import Method
from g6.self_maintenance.pacing import ProviderWindow

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)


def _loop(tmp_path, domain=None, proposer=None, settings=SETTINGS, now=lambda: NOW, **kw):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    return ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=now, clock=domains.Clock(), **kw), domain


def _enrolled(tmp_path, **kw):
    loop, domain = _loop(tmp_path, **kw)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _with_model(domain, caller) -> None:
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(caller)})


# Finding 1: a tick is charged what reached the backend, and stops at what it reserved -----------------------------------

class Retrying:
    """Answers through a backend it asks ``tries`` times, and notes none of them: the retries the reviewer counted."""

    def __init__(self, inner, tries: int = 8) -> None:
        self.inner, self.tries, self.backend = inner, tries, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.backend += self.tries
        return self.inner(system, user)


def test_1_a_retry_a_caller_notes_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)
    tutor, attempts = domain.tutor, []

    def blank_first(system: str, user: str) -> str:
        attempts.append(user)
        return "" if attempts.count(user) == 1 else tutor(system, user)

    backend = FakeLLM(blank_first)
    _with_model(domain, EmptyRetry(backend))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == len(backend.calls) + len(proposer.calls)


def test_1_each_attempt_of_a_fallback_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)

    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: the first backend is down")

    first, second = FakeLLM(down), FakeLLM(domain.tutor)
    _with_model(domain, FallbackCaller((first, second)))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert len(first.calls) > 0
    assert loop.meter.reading("claude", NOW).used == len(first.calls) + len(second.calls) + len(proposer.calls)


def test_1_a_resend_after_retry_after_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)

    def resent_once(system: str, user: str) -> str:
        for found in llm._WAITS.get():
            found.append({"url": "http://model", "status": 429, "retry_after_s": 1.0, "waited_s": 1.0, "cap_s": 30.0})
        return domain.tutor(system, user)

    backend = FakeLLM(resent_once)
    _with_model(domain, backend)
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == 2 * len(backend.calls) + len(proposer.calls)


def test_1_a_caller_that_notes_no_request_is_refused_and_charged_in_full(tmp_path):
    domain = domains.WordProblems(tmp_path)
    hidden = Retrying(domain.tutor)
    _with_model(domain, hidden)
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_UNMETERED_CALLER" and got["completion_state"] == "blocked-escalated"
    assert hidden.backend == hidden.tries                      # one call showed it: no second one went out
    assert loop.mh._head() == start and "cannot count" in loop.log.paused()
    assert loop.meter.reading("claude", NOW).used == loop.worst_case()   # unknown use is charged at its worst case


def test_1_a_tick_stops_at_what_it_reserved_and_what_it_ran_counts_for_nothing(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    start, reserved = loop.mh._head(), 40
    monkeypatch.setattr(loop, "worst_case", lambda: reserved)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_TICK_BUDGET_SPENT" and got["outcome"] == "blocked"
    assert loop.mh._head() == start
    assert len(domain.llm.calls) <= reserved                   # a FakeLLM call is one request: no overrun at all
    assert loop.meter.reading("claude", NOW).used == reserved


def test_1_a_proposer_counts_against_the_tick_as_the_model_does(tmp_path):
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, domain = _enrolled(tmp_path, proposer=FallbackCaller((proposer,)))
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == len(domain.llm.calls) + len(proposer.calls) > 0


def test_1_the_study_of_a_profile_outside_a_tick_is_not_stopped(tmp_path):
    loop, domain = _enrolled(tmp_path)
    case = loop.cut.tune[0]
    assert loop.workflow.models["local"].caller.complete("s", f"Problem: {case.input}")
    assert json.dumps(loop.status()["failed_streak"]) == "0"
