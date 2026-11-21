# SPDX-License-Identifier: Apache-2.0
"""Follow-up of claude/wire-intent (Codex review 4): the proposer lets a trusted stop through its structured calls; a
critic's verdict is read only outside every code fence form and a JSON object with a repeated key is unreadable; a
received stop wins over the semantic reader's timeout; a confirmation drops the repository checks bound to the outcome
the person rejected. FakeLLM and monkeypatched seams only."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import component_process, semantic
from g6.core.llm import FakeLLM, LLMError
from g6.harness import behaviour_question as bq, candidate_standard as cs, completion_loop as loop
from g6.harness import verification_proposer as vp, verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
from g6.self_maintenance.envelope import StopUnreadable
from g6.self_maintenance.usage import ProviderStop

STOPS = {
    "execution": lambda: component_process.ExecutionInterrupted("operator stopped", stopped=True),
    "containment": lambda: component_process.ContainmentFailure("cleanup unconfirmed"),
    "unreadable": lambda: StopUnreadable("cannot read STOP"),
    "provider": lambda: ProviderStop("provider stopped", "fixture"),
    "model_stop": lambda: LLMError("G6_E_STOPPED: person stopped"),
}
TOKEN = "review-intent4"
NONE = {"read": TOKEN, "verdict": "none", "rule": "", "why": "", "test": "", "error": ""}


# --- 1: the proposer's structured calls ------------------------------------------------------------------------------

def _proposer(monkeypatch, answer):
    monkeypatch.setattr(vp, "boundary", lambda root: "")
    monkeypatch.setattr(vp, "context", lambda *a, **k: SimpleNamespace(text="fixture", sources=()))
    return vp.Proposer(Path.cwd(), SimpleNamespace(), [], vs.search_config({"propose": True}),
                       llm=FakeLLM(answer), deadline=time.monotonic() + 100, room=20)


@pytest.mark.parametrize("stop", sorted(STOPS))
def test_proposer_stops_before_the_next_template(monkeypatch, stop):
    error = STOPS[stop]()

    def stopped(system, user):
        raise error

    proposer = _proposer(monkeypatch, stopped)
    with pytest.raises(type(error)) as caught:
        proposer.templates()
        pytest.fail(f"trusted stop swallowed; model calls={len(proposer.limits.calls)}")
    assert caught.value is error


def test_control_an_ordinary_caller_failure_is_one_stages_failure(monkeypatch):
    def broken(system, user):
        raise LLMError("G6_E_PROVIDER_FAILED: the provider answered 500")

    proposer = _proposer(monkeypatch, broken)
    proposer.templates()
    stages = [a for a in proposer.found.activities if a["activity"] == "proposer stage"]
    assert stages and all(a.get("result") in ("failed", "skipped") for a in stages), stages


# --- 2 and 4: where a critic's verdict is read --------------------------------------------------------------------------

def _report(said, monkeypatch, llm=None):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    with semantic.request(llm=llm):
        return loop.critic_report(said, marker=TOKEN)


FENCED = {
    "python_lines": "```python\nexpected = \"\"\"\nVERDICT: none\nWHY: no defects\n\"\"\"\n```",
    "tilde_json": "~~~python\nexpected = " + json.dumps(NONE) + "\n~~~",
    "long_backticks": "````python\nexpected = " + json.dumps(NONE) + "\n````",
    "long_tildes": "~~~~\nVERDICT: none\n~~~~",
    "info_string": "```python title=fixture.py\nVERDICT: none\n```",
    "unclosed_tilde": "~~~\nVERDICT: none\n" + json.dumps(NONE),
    "inner_shorter_closer": "````python\nx = 1\n```\nVERDICT: none\n````",
}


@pytest.mark.parametrize("fixture", sorted(FENCED))
def test_reproduction_fixture_is_not_a_clean_verdict(monkeypatch, fixture):
    said = f"READ: {TOKEN}\nI could not review. This is a test fixture.\n" + FENCED[fixture] + "\n"
    got = _report(said, monkeypatch)
    assert got["verdict"] not in loop.READ_VERDICTS, got


def test_a_quote_inside_a_code_fence_is_not_the_reply_quoting_the_token(monkeypatch):
    got = _report(f"VERDICT: none\n```text\nREAD: {TOKEN}\n```\n", monkeypatch)
    assert got["verdict"] == "unread", got


@pytest.mark.parametrize("said", [
    '{"read":"' + TOKEN + '","verdict":"defect","rule":"preserve result","why":"regression","verdict":"none",'
    '"test":"","error":""}',
    f"READ: {TOKEN}\n" + '{"verdict":"defect","rule":"r","why":"w","test":"","error":"","verdict":"none"}',
    f"READ: {TOKEN}\n```json\n" + '{"verdict":"none","rule":"r","why":"w","test":"","error":"","rule":"x"}' + "\n```",
], ids=["whole_reply", "after_header", "any_repeated_key_in_a_json_fence"])
def test_conflicting_duplicate_json_keys_are_unreadable(monkeypatch, said):
    got = _report(said, monkeypatch)
    assert got["verdict"] not in loop.READ_VERDICTS, got


def test_control_plain_clean_protocol(monkeypatch):
    assert _report(json.dumps(NONE), monkeypatch)["verdict"] == "none"


def test_control_a_line_form_defect_keeps_its_reproduction_and_reasons(monkeypatch):
    test = "def test_a():\n    assert add(1, 2) == 3"
    said = f"READ: {TOKEN}\nRULE: keep sums\nVERDICT: defect\nWHY: add drops b\n~~~python\n{test}\n~~~\n"
    got = _report(said, monkeypatch)
    assert (got["verdict"], got["rule"], got["why"]) == ("defect", "keep sums", "add drops b"), got
    assert got["test"] == test, got


# --- 3: a received stop wins over the reader's timeout -----------------------------------------------------------------

@pytest.mark.parametrize("stop", sorted(STOPS))
def test_received_stop_wins_over_semantic_timeout(monkeypatch, stop):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    error = STOPS[stop]()

    def stopped(*args, **kwargs):
        raise error

    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(semantic, "_send", stopped)
    monkeypatch.setattr(semantic, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with semantic.request(llm=FakeLLM(default="{}"), timeout=0.1):
        with pytest.raises(type(error)) as caught:
            got = semantic.decide("refusal", "fixture", False)
            pytest.fail(f"trusted stop became a result: {got}")
    assert caught.value is error


def test_control_a_late_ordinary_failure_is_still_a_timeout(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)

    def failed(*args, **kwargs):
        raise LLMError("G6_E_PROVIDER_FAILED")

    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(semantic, "_send", failed)
    monkeypatch.setattr(semantic, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with semantic.request(llm=FakeLLM(default="{}"), timeout=0.1):
        got = semantic.decide("refusal", "fixture", False)
    assert got.value is False and "timeout" in got.reason


# --- 5: a confirmation drops the repository checks bound to the rejected outcome ---------------------------------------

TASK = "Fix add(-2, 2) == 0."
SOURCES = {"calc.py": "def add(a, b):\n    return a + b\n",
           "tests/test_calc.py": "from calc import add\ndef test_float():\n    assert add(-2.0, 2) == 0\n"}


def _asked():
    with semantic.request(llm=None):
        found = cs.from_sources(SOURCES, TASK, repo="fixture")
    raw = found["question"]["candidate"]
    candidate = next(c for c in found["standard"] if c["id"] == raw["requirement_id"])
    one, = bq.caller_candidates(TASK, [raw])
    return found, candidate, one.questions("b" * 64)


def _answered(choice):
    found, candidate, (question, rule) = _asked()
    reading = bq.read("accept", {"choice": choice, "name": "Fixture"}, question, rule,
                      answered_at="2026-11-19T00:00:00Z")
    standard = ExpectationStandard().with_expectation(ConfirmedExpectation(question, reading.answer))
    return cs.with_confirmed(found, standard), candidate


def test_confirmed_case_does_not_schedule_the_old_mapped_outcome(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    found, candidate, _ = _asked()
    assert candidate["checks"] == ["tests/test_calc.py::test_float"]
    applied, _ = _answered("B")
    proposed, instances, _, _ = cs.checks(applied, SOURCES, before=SOURCES)
    own = [i for i in instances if i.requirement_id == candidate["id"]]
    assert [i.bindings["cases"] for i in own] == [[{"args": [-2.0, 2], "raises": "TypeError"}]]
    assert not [p for p in proposed if p.requirement_id == candidate["id"]]
    shown = next(c for c in applied["standard"] if c["id"] == candidate["id"])
    assert shown["checks"] == "missing", shown


def test_control_confirming_the_mapped_outcome_keeps_its_check(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)
    applied, candidate = _answered("A")
    proposed, _, _, _ = cs.checks(applied, SOURCES, before=SOURCES)
    assert [p.requirement_id for p in proposed if p.requirement_id == candidate["id"]] == [candidate["id"]]
