# SPDX-License-Identifier: Apache-2.0
"""Each finding of the cross-family review of the loop, as the input that showed it. Each was seen failing first."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import mmap
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core import llm
from g6.core.llm import EmptyRetry, FakeLLM, FallbackCaller, LLMError
from g6.core.vault import AccessDenied
from g6.metaharness import Model
from g6.metaharness import domain as sealed_module
from g6.metaharness.domain import sealed_closed
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


# Finding 2: what the sealed guard does, and what no guard in this process can do -----------------------------------------

def test_2_a_handle_opened_before_the_tick_is_seen_and_nothing_is_adopted(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()
    with open(domain.sealed_file, "rb") as held:
        got = loop.tick()
        assert held.readable()
    assert got["outcome"] == "sealed_breach" and got["completion_state"] == "blocked-escalated"
    assert loop.mh._head() == start and "sealed" in loop.log.paused()
    assert len(domain.llm.calls) == 0                          # seen on the way in: the cycle never ran


def test_2_a_descriptor_opened_before_the_tick_is_seen_too(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    descriptor = os.open(domain.sealed_file, os.O_RDONLY)
    try:
        assert loop.tick()["outcome"] == "sealed_breach"
    finally:
        os.close(descriptor)


def test_2_a_descriptor_cannot_be_wrapped_or_mapped_while_developing(tmp_path):
    domain = domains.WordProblems(tmp_path)
    descriptor = os.open(domain.sealed_file, os.O_RDONLY)
    try:
        with sealed_closed(domain.sealed().vault) as breaches:
            with pytest.raises(AccessDenied):
                open(descriptor, "rb", closefd=False)
            with pytest.raises(AccessDenied):
                mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
        assert len(breaches) >= 3                              # the sweep on the way in, and each attempt
    finally:
        os.close(descriptor)


def test_2_a_handle_taken_during_a_tick_by_a_route_the_guard_cannot_see_is_seen_on_the_way_out(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    held = []
    real = domain.failures

    def smuggled(view):
        guarded = list(sealed_module._GUARDED)
        sealed_module._GUARDED.clear()                         # as native code would: past the audit hook
        held.append(open(domain.sealed_file, "rb"))
        sealed_module._GUARDED.extend(guarded)
        return real(view)

    domain.failures = smuggled
    try:
        got = loop.tick()
    finally:
        held[0].close()
    assert got["outcome"] == "sealed_breach"


def test_2_what_the_guard_cannot_do_a_read_through_a_handle_raises_nothing(tmp_path):
    """Pinned so no one takes the sweep for more than it is. Nothing in this process stops this read: a read on an open
    handle emits no audit event. The sweep only notices that the handle is there, and the docstring says so."""
    domain = domains.WordProblems(tmp_path)
    held = open(domain.sealed_file, "rb")
    try:
        with sealed_closed(domain.sealed().vault) as breaches:
            assert held.read(10)                               # the read succeeds
            assert breaches                                    # and the handle is on the record
    finally:
        held.close()


def test_2_the_docstring_and_the_design_say_what_the_guard_does_not_do():
    said = (Path(g6.__file__).parent / "metaharness" / "domain.py").read_text(encoding="utf-8")
    design = (Path(__file__).parents[2] / "docs" / "superpowers" / "specs" /
              "2026-10-29-self-improvement-loop-design.md").read_text(encoding="utf-8")
    for text in (said, design):
        assert "cannot be stopped" in text.replace("\n", " ")


def test_2_an_unrelated_open_descriptor_is_no_breach(tmp_path):
    domain = domains.WordProblems(tmp_path)
    other = tmp_path / "ordinary.txt"
    other.write_text("x", encoding="utf-8")
    with open(other, "rb"):
        with sealed_closed(domain.sealed().vault) as breaches:
            pass
    assert breaches == []