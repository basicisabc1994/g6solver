# SPDX-License-Identifier: Apache-2.0
"""Repair 3 of claude/wire-intent-fu (Codex review 3): a reply CommonMark cannot read whole is unreadable before any
cut; a JSON-looking object that does not decode is unreadable when it names verdict or its extent cannot be found,
found without recursion; every confirmation rejects every other outcome its question offered; no model is asked
through a caller that may hold a fallback chain (rewritten for review 4). FakeLLM and synthetic inputs only."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import markdown, semantic
from g6.core.llm import FakeLLM, FallbackCaller, LLMError
from g6.harness import behaviour_question as bq, candidate_standard as cs
from g6.harness import completion_loop as loop, verification_proposer as vp, verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard

TOKEN = "review-intfu3-fixture"


@pytest.fixture(autouse=True)
def online_reader(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


def _report(said):
    with semantic.request(llm=None):
        return loop.critic_report(said, marker=TOKEN)


# --- 1: a reply too deep to read whole -------------------------------------------------------------------------------

@pytest.mark.parametrize("cut", ["```python [codex]", "[codex] progress", "~~~"])
def test_truncated_document_cannot_become_clean_after_reply_cut(cut):
    said = (f"READ: {TOKEN}\nVERDICT: defect\nWHY: actual regression\n" + "> " * 120 + cut + "\n"
            + f"READ: {TOKEN}\nVERDICT: none\n")
    assert markdown.parse(said).truncated
    assert _report(said)["verdict"] not in loop.READ_VERDICTS


# --- 2: objects that do not decode ------------------------------------------------------------------------------------

DEEP = "[" * 6000 + "0" + "]" * 6000


@pytest.mark.parametrize("obj", [
    '{"x":1,"x":2,"bad":]}',
    '{"x":1,"x":2,"verdict":"defect","nested":' + DEEP + "}",
    '{"verdict":"defect","nested":' + DEEP + "}",
    '{"nested":' + DEEP + ',"\\u0076erdict":"defect"}',
    '{"rule": "r", "why": "unterminated',
    '{"why": "x"',
], ids=["malformed_repeat", "deep_repeat", "deep_verdict", "deep_escaped_verdict", "unterminated_string",
        "never_closed"])
def test_an_object_that_does_not_decode_cannot_supply_a_clean_review(obj):
    assert _report(f"READ: {TOKEN}\n{obj}\nVERDICT: none\n")["verdict"] not in loop.READ_VERDICTS


@pytest.mark.parametrize("prose", ["the set {1, 2}", "it returns {'a': 1}", '{"a": ' + DEEP + "}", "a dict {}"])
def test_control_braces_that_name_no_verdict_and_close_are_prose(prose):
    assert _report(f"READ: {TOKEN}\nVERDICT: defect\nWHY: {prose}\n")["verdict"] == "defect"


# --- 3: every other outcome a question offered is rejected ------------------------------------------------------------

TASK = "Fix add(a, b). For example, add(-2, 2) == 0."
TEXTS = {"calc.py": "def add(a, b):\n    return a + b\n"}


def _applied(choice, proposal):
    with semantic.request(llm=FakeLLM(default=json.dumps({"value": [proposal]}))):
        found = cs.from_sources(TEXTS, TASK, repo="fixture")
    raw = found["question"]["candidate"]
    candidate, = bq.caller_candidates(TASK, [raw])
    question, rule = candidate.questions("b" * 64)
    reading = bq.read("accept", {"choice": choice, "name": "Fixture"}, question, rule,
                      answered_at="2026-11-19T00:00:00Z")
    standard = ExpectationStandard().with_expectation(ConfirmedExpectation(question, reading.answer))
    return found, raw, cs.with_confirmed(found, standard)


def _scheduled(applied, function, args, outcome):
    _, instances, _, _ = cs.checks(applied, TEXTS, before=TEXTS)
    return [one.bindings for one in instances if one.bindings["function"] == function and any(
        case.get("args") == args and all(case.get(k) == v for k, v in outcome.items()) and outcome.keys() <= case.keys()
        for case in one.bindings["cases"])]


def test_confirming_a_drops_explicitly_rejected_b():
    proposal = {"text": "reject floats", "kind": "example", "function": "calc.add",
                "cases": [{"args": [-2.0, 2], "raises": "TypeError"}]}
    found, raw, applied = _applied("A", proposal)
    assert raw["outcomes"] == {"A": {"returns": 0}, "B": {"raises": "TypeError"}}
    assert not _scheduled(applied, raw["function"], raw["args"], {"raises": "TypeError"})
    assert _scheduled(applied, raw["function"], raw["args"], {"returns": 0})
    model = next(c for c in found["standard"] if c["origin"] == "model_proposal")
    saved, = model["cheapest_check"]["arguments"]["template_instances"]
    kept, _ = cs.gate(applied, [SimpleNamespace(id="saved", template="example.v1", requirement_id=model["id"],
                                                bindings=saved["bindings"])], "proposed check", "caller")
    assert not kept   # rewritten for Area B: the scheduling gate drops it (owns() no longer decides this)


def test_control_another_call_of_the_same_function_stays():
    proposal = {"text": "other call", "kind": "example", "function": "calc.add",
                "cases": [{"args": [3.0, 2], "raises": "TypeError"}]}
    _, raw, applied = _applied("A", proposal)
    assert _scheduled(applied, raw["function"], [3.0, 2], {"raises": "TypeError"})


# --- 4: a nested chain (rewritten for the rule that no model is asked through a caller that may hold a chain) ---------

def _broken(system, user):
    raise LLMError("G6_E_PROVIDER_FAILED: fixture")


def test_nested_chain_skips_the_proposer_and_asks_no_link(monkeypatch):
    monkeypatch.setattr(vp, "boundary", lambda root: "")
    monkeypatch.setattr(vp, "context", lambda *args, **kwargs: SimpleNamespace(text="fixture", sources=()))
    inner, fallback = FakeLLM(_broken), FakeLLM(default='{"instances":[]}')
    proposer = vp.Proposer(Path.cwd(), SimpleNamespace(), [], vs.search_config({"propose": True}),
                           llm=FallbackCaller([FallbackCaller([inner]), fallback]),
                           deadline=time.monotonic() + 100, room=20)
    proposer.templates()
    stages = [a for a in proposer.found.activities if a["activity"] == "proposer stage"]
    assert stages and all(a["reason"] == semantic.FALLBACK_UNSAFE for a in stages)
    assert not inner.calls and not fallback.calls


def test_nested_chain_keeps_a_semantic_reading_on_its_floor():
    inner, fallback = FakeLLM(_broken), FakeLLM(default='{"value":true}')
    with semantic.request(llm=FallbackCaller([FallbackCaller([inner]), fallback])):
        got = semantic.decide("refusal", "synthetic", False)
    assert (got.by, got.reason, got.value) == ("floor", semantic.FALLBACK_UNSAFE, False)
    assert not inner.calls and not fallback.calls