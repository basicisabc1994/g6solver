# SPDX-License-Identifier: Apache-2.0
"""Repair 2 of claude/wire-intent-fu (Codex review 2): until FallbackCaller itself stops on a stop (a bound hunk), the
proposer and the semantic reader ask no model through a caller that may hold a fallback chain, through any wrapper G6
reads, and G6 never changes a caller it is handed. A critic's reply is read through CommonMark, so code at any container depth and any line ending is code. A key is
compared decoded. A confirmation drops every check, mapped or generated, asserting the rejected outcome."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.consent_callers import destination
from g6.core.llm import FakeLLM, FallbackCaller, LLMError, TimingLLMCaller
from g6.harness import behaviour_question as bq, candidate_standard as cs
from g6.harness import completion_loop as loop, verification_proposer as vp, verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
from g6.self_maintenance.usage import ProviderStop

TOKEN = "review-intfu2-fixture"


@pytest.fixture(autouse=True)
def enable_synthetic_readings(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


def _proposer(monkeypatch, caller):
    monkeypatch.setattr(vp, "boundary", lambda root: "")
    monkeypatch.setattr(vp, "context", lambda *args, **kwargs: SimpleNamespace(text="fixture", sources=()))
    return vp.Proposer(Path.cwd(), SimpleNamespace(), [], vs.search_config({"propose": True}),
                       llm=caller, deadline=time.monotonic() + 100, room=20)


def _stop(kind):
    return ProviderStop("provider stopped", "fixture") if kind == "provider" else LLMError("G6_E_STOPPED: stopped")


def _raises(error):
    def answer(system, user):
        raise error
    return answer


# --- 1, 2 and 6: fallback chains (rewritten: no model is asked through a caller that may hold one) --------------------

def _unsafe(stages):
    return stages and all(a["result"] == "skipped" and a["reason"] == semantic.FALLBACK_UNSAFE for a in stages)


def _stages(proposer):
    return [a for a in proposer.found.activities if a["activity"] == "proposer stage"]


@pytest.mark.parametrize("kind", ["provider", "model"])
@pytest.mark.parametrize("position", ["outer", "inner"])
def test_proposer_asks_no_chain_behind_a_timing_wrapper(monkeypatch, kind, position):
    first, fallback = FakeLLM(_raises(_stop(kind))), FakeLLM(default='{"instances":[]}')
    timed = TimingLLMCaller(FallbackCaller([first, fallback]), lambda elapsed: None)
    caller = timed if position == "outer" else FallbackCaller([timed])
    proposer = _proposer(monkeypatch, caller)
    proposer.templates()
    assert _unsafe(_stages(proposer)) and not first.calls and not fallback.calls


@pytest.mark.parametrize("kind", ["provider", "model"])
def test_semantic_reader_asks_no_chain(kind):
    first, fallback = FakeLLM(_raises(_stop(kind))), FakeLLM(default='{"value":false}')
    with semantic.request(llm=FallbackCaller([first, fallback])):
        got = semantic.decide("refusal", "synthetic refusal classification", False)
    assert (got.by, got.reason) == ("floor", semantic.FALLBACK_UNSAFE) and not first.calls and not fallback.calls


def test_fallback_caller_reads_the_same_across_reviews(monkeypatch):
    backend = FakeLLM(default='{"instances":[]}')
    caller = FallbackCaller([backend])
    assert destination(caller).local
    for _ in range(2):
        review = _proposer(monkeypatch, caller)
        review.templates()
        assert _unsafe(_stages(review)) and caller._callers[0] is backend
    assert not backend.calls


def test_g6_never_changes_a_caller_it_is_handed(monkeypatch):
    links = (FakeLLM(_raises(LLMError("G6_E_PROVIDER_FAILED"))), FakeLLM(default='{"instances":[]}'))
    inner = FallbackCaller(list(links))
    caller = TimingLLMCaller(inner, lambda elapsed: None)
    _proposer(monkeypatch, caller).templates()
    with semantic.request(llm=caller):
        got = semantic.decide("refusal", "synthetic", False)
    assert got.reason == semantic.FALLBACK_UNSAFE
    assert caller._inner is inner and inner._callers == links and not any(one.calls for one in links)


@pytest.mark.parametrize("hidden", ["opaque", "guarded_unknown"])
def test_a_caller_g6_cannot_read_through_counts_as_holding_a_chain(monkeypatch, hidden):
    from g6.core import consent_callers

    class Opaque:
        def __init__(self):
            self.inner = FallbackCaller([FakeLLM(default='{"instances":[]}')])

        def complete(self, system, user, *, max_tokens=0):
            return self.inner.complete(system, user, max_tokens=max_tokens)

    caller = Opaque() if hidden == "opaque" else consent_callers.guard(Opaque())
    assert semantic.holds_fallback(caller)
    proposer = _proposer(monkeypatch, caller)
    proposer.templates()
    assert _unsafe(_stages(proposer)) and not caller.inner._callers[0].calls


def test_control_a_caller_without_a_chain_is_asked(monkeypatch):
    plain = FakeLLM(default='{"instances":[]}')
    assert not semantic.holds_fallback(TimingLLMCaller(plain, lambda elapsed: None))
    proposer = _proposer(monkeypatch, TimingLLMCaller(plain, lambda elapsed: None))
    proposer.templates()
    assert plain.calls and not any(a.get("reason") == semantic.FALLBACK_UNSAFE for a in _stages(proposer))

# --- 3: code at any depth, any line ending ----------------------------------------------------------------------------

FENCED_REPLIES = {
    "crlf": f"READ: {TOKEN}\r\nVERDICT: none\r\n```python\r\nx = 1\r\n```\r\nVERDICT: defect\r\nWHY: actual regression\r\n",
    "blockquote": (f"READ: {TOKEN}\nReview unavailable; fixture follows.\n"
                   '> ```python\n> expected = """\n> VERDICT: none\n> """\n> ```\n'),
    "list": (f"READ: {TOKEN}\nReview unavailable; fixture follows.\n"
             '- ```python\n  expected = """\n  VERDICT: none\n  """\n  ```\n'),
    "indented_code": f"READ: {TOKEN}\nReview unavailable; fixture follows.\n\n    VERDICT: none\n",
    "nested_list_in_quote": (f"READ: {TOKEN}\nfixture\n> - ~~~\n>   " + json.dumps({"read": TOKEN, "verdict": "none",
                             "rule": "", "why": "", "test": "", "error": ""}) + "\n>   ~~~\n"),
    "too_deep_to_read": f"READ: {TOKEN}\n" + "> " * 120 + "```\n" + "> " * 120 + "VERDICT: none\n",
}


@pytest.mark.parametrize("form", sorted(FENCED_REPLIES))
def test_fences_cannot_supply_or_hide_a_verdict(form):
    with semantic.request(llm=None):
        got = loop.critic_report(FENCED_REPLIES[form], marker=TOKEN)
    assert got["verdict"] not in loop.READ_VERDICTS, got


def test_control_a_json_fence_in_a_quote_is_still_the_protocols_position():
    stated = {"read": TOKEN, "verdict": "defect", "rule": "r", "why": "w", "test": "def test_a():\n    assert 0",
              "error": ""}
    with semantic.request(llm=None):
        got = loop.critic_report("> ```json\n> " + json.dumps(stated) + "\n> ```\n", marker=TOKEN)
    assert got["verdict"] == "defect" and got["test"] == stated["test"], got


def test_control_a_crlf_line_form_defect_keeps_its_reproduction():
    said = f"READ: {TOKEN}\r\nVERDICT: defect\r\nWHY: w\r\n```python\r\nx = 1\r\n```\r\n"
    with semantic.request(llm=None):
        got = loop.critic_report(said, marker=TOKEN)
    assert (got["verdict"], got["test"], got["why"]) == ("defect", "x = 1", "w"), got


# --- 4: a key is compared decoded --------------------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    f"READ: {TOKEN}\n" + r'{"verdict":"defect","why":' + "\nVERDICT: none\n",
    f"READ: {TOKEN}\n" + r'{"verdict":"none","rule":"","why":"","test":"","error":""}' + "\nVERDICT: defect\n",
], ids=["broken", "well_formed"])
def test_escaped_verdict_key_is_a_stated_verdict(said):
    with semantic.request(llm=None):
        got = loop.critic_report(said, marker=TOKEN)
    assert got["verdict"] not in loop.READ_VERDICTS, got


# --- 5: every check asserting the rejected outcome ----------------------------------------------------------------------

TASK = "Fix add(a, b). For example, add(-2, 2) == 0."
SOURCES = {"calc.py": "def add(a, b):\n    return a + b\n",
           "tests/test_calc.py": "from calc import add\ndef test_float():\n    assert add(-2.0, 2) == 0\n"}
REPLY = {"value": [{"text": "float zero", "kind": "example", "function": "calc.add",
                    "cases": [{"args": [-2.0, 2], "returns": 0}]}]}


def _confirmed_b():
    with semantic.request(llm=FakeLLM(default=json.dumps(REPLY))):
        found = cs.from_sources(SOURCES, TASK, repo="fixture")
    raw = found["question"]["candidate"]
    candidate, = bq.caller_candidates(TASK, [raw])
    question, rule = candidate.questions("b" * 64)
    reading = bq.read("accept", {"choice": "B", "name": "Fixture"}, question, rule, answered_at="2026-11-19T00:00:00Z")
    return found, raw, cs.with_confirmed(found, ExpectationStandard().with_expectation(
        ConfirmedExpectation(question, reading.answer)))


def _asserts_rejected(instance):
    return instance.bindings.get("function") == "calc.add" and any(
        json.dumps(case.get("args")) == "[-2.0, 2]" and case.get("returns") == 0 and "returns" in case
        for case in instance.bindings.get("cases", []))


def test_confirmation_removes_other_generated_checks_for_rejected_outcome():
    found, raw, applied = _confirmed_b()
    assert raw["args"] == [-2.0, 2] and raw["outcomes"] == {"A": {"returns": 0}, "B": {"raises": "TypeError"}}
    proposed, instances, _, _ = cs.checks(applied, SOURCES, before=SOURCES)
    assert any(i.requirement_id == raw["requirement_id"]
               and i.bindings["cases"] == [{"args": [-2.0, 2], "raises": "TypeError"}] for i in instances)
    assert not any(p.file == "tests/test_calc.py" and p.test == "test_float" for p in proposed)
    assert not [(i.requirement_id, i.bindings) for i in instances if _asserts_rejected(i)]


def test_a_saved_call_carrying_the_rejected_case_is_the_candidates_own():
    found, raw, applied = _confirmed_b()
    model = next(c for c in found["standard"] if c["origin"] == "model_proposal")
    saved, = model["cheapest_check"]["arguments"]["template_instances"]
    supplied = SimpleNamespace(template="example.v1", requirement_id=model["id"], bindings=saved["bindings"])
    assert cs.owns(applied, supplied)
    shown = next(c for c in applied["standard"] if c["id"] == model["id"])
    assert "template_instances" not in shown["cheapest_check"]["arguments"], shown["cheapest_check"]
