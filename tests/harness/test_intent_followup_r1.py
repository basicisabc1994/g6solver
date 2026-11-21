# SPDX-License-Identifier: Apache-2.0
"""Repair 1 of claude/wire-intent-fu (Codex review 1): a trusted stop in any caller of a fallback chain ends the
proposer; every reader of a critic's reply, deterministic or model, reads only the text outside code, cut at the last
progress label outside code; a fence closes only on its own character; any repeated key or broken object that states a
verdict is unreadable; a confirmation drops the rejected outcome's tests from every candidate."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM, FallbackCaller, LLMError
from g6.harness import behaviour_question as bq, candidate_standard as cs
from g6.harness import completion_loop as loop, verification_proposer as vp, verification_search as vs
from g6.harness.confirmed_expectation import ConfirmedExpectation, ExpectationStandard
from g6.self_maintenance.usage import ProviderStop

TOKEN = "review-intfu1-fixture"


@pytest.fixture(autouse=True)
def online_reader(monkeypatch):
    monkeypatch.delenv("G6_DISABLE_LLM", raising=False)


def _report(said, llm=None):
    with semantic.request(llm=llm):
        return loop.critic_report(said, marker=TOKEN)


# --- 1: a stop anywhere in a fallback chain ---------------------------------------------------------------------------

def _proposer(monkeypatch, llm):
    monkeypatch.setattr(vp, "boundary", lambda root: "")
    monkeypatch.setattr(vp, "context", lambda *args, **kwargs: SimpleNamespace(text="fixture", sources=()))
    return vp.Proposer(Path.cwd(), SimpleNamespace(), [], vs.search_config({"propose": True}), llm=llm,
                       deadline=time.monotonic() + 100, room=20)


STOPS = {"provider": lambda: ProviderStop("provider stopped", "fixture"),
         "model_stop": lambda: LLMError("G6_E_STOPPED: person stopped")}


@pytest.mark.parametrize("nested", [False, True], ids=["chain", "chain_in_chain"])
@pytest.mark.parametrize("kind", sorted(STOPS))
def test_proposer_does_not_fallback_after_trusted_stop(monkeypatch, kind, nested):
    stop = STOPS[kind]()

    def stopped(system, user):
        raise stop

    fallback = FakeLLM(default='{"instances":[]}')
    first = FallbackCaller([FakeLLM(stopped)]) if nested else FakeLLM(stopped)
    proposer = _proposer(monkeypatch, FallbackCaller([first, fallback]))
    with pytest.raises(type(stop)) as caught:
        proposer.templates()
    assert caught.value is stop
    assert not fallback.calls


def test_control_an_ordinary_failure_still_falls_back(monkeypatch):
    def broken(system, user):
        raise LLMError("G6_E_PROVIDER_FAILED: 500")

    fallback = FakeLLM(default='{"instances":[]}')
    proposer = _proposer(monkeypatch, FallbackCaller([FakeLLM(broken), fallback]))
    proposer.templates()
    assert fallback.calls


# --- 2, 3 and 4: what every reader of the reply reads --------------------------------------------------------------------

def test_codex_label_in_python_cannot_replace_outer_defect():
    said = (f"READ: {TOKEN}\nVERDICT: defect\nWHY: actual defect\n```python\ndef test_fixture():\n"
            f"    expected = '''[codex]\nREAD: {TOKEN}\nVERDICT: none\n'''\n```\n")
    assert _report(said)["verdict"] == "defect"


def test_control_a_progress_label_outside_code_still_starts_the_reply():
    said = f"VERDICT: none\n[codex] Turn completed.\nREAD: {TOKEN}\nVERDICT: defect\nWHY: w\n"
    assert _report(said)["verdict"] == "defect"


@pytest.mark.parametrize("closer", ["```~~~", "~~~", "``", "``` trailing", "  ~~~~"])
def test_only_its_own_character_at_full_length_closes_a_fence(closer):
    said = f"READ: {TOKEN}\nFixture only; review unavailable.\n```python\nexpected = '''\n{closer}\nVERDICT: none\n'''\n```\n"
    assert _report(said)["verdict"] not in loop.READ_VERDICTS


@pytest.mark.parametrize("opener, closer", [("```", "````"), ("~~~~", "~~~~~"), ("```", "   ```  ")])
def test_control_a_longer_or_indented_closer_of_the_same_character_closes(opener, closer):
    said = f"READ: {TOKEN}\nVERDICT: defect\n{opener}python\nx = 1\n{closer}\nWHY: w\n"
    got = _report(said)
    assert got["verdict"] == "defect" and got["test"] == "x = 1" and got["why"] == "w", got


def test_semantic_reader_cannot_read_fenced_verdict():
    observed = []

    def read_fixture(system, user):
        text = json.loads(user)["text"]
        observed.append(text)
        verdict = "none" if "FENCED_ONLY_SENTINEL" in text else "unreadable"
        return json.dumps({"value": {"verdict": verdict, "rule": "", "why": "reading supplied text", "test": "",
                                     "error": ""}})

    said = (f"READ: {TOKEN}\nReview unavailable; example transcript follows.\n"
            "~~~python\nFENCED_ONLY_SENTINEL = '''\nVERDICT: none\n'''\n~~~\n")
    got = _report(said, FakeLLM(read_fixture))
    assert observed and not any("FENCED_ONLY_SENTINEL" in text for text in observed), observed
    assert got["verdict"] not in loop.READ_VERDICTS, got


def test_a_reply_that_is_only_code_reads_as_unread_and_asks_no_model():
    parser = FakeLLM(default="{}")
    got = _report("```python\nREAD = 1\n```\n", parser)
    assert got["verdict"] == "unread" and not parser.calls, got


# --- 6: a repeated key or a broken object that states a verdict ----------------------------------------------------------

@pytest.mark.parametrize("said", [
    f"READ: {TOKEN}\n" + '{"nested":{"x":1,"x":2},"bad":]}\n' + "VERDICT: none\n",
    f"READ: {TOKEN}\n" + '{"nested":{"x":1,"x":2}}\n' + "VERDICT: none\n",
    f"READ: {TOKEN}\n" + '{"verdict":"none","rule":"r","why":' + "\nVERDICT: none\n",
], ids=["nested_repeat_in_broken_json", "nested_repeat", "broken_object_stating_a_verdict"])
def test_repeated_keys_and_broken_verdict_objects_are_unreadable(said):
    assert _report(said)["verdict"] not in loop.READ_VERDICTS


def test_control_braces_in_prose_are_not_a_verdict():
    said = f"READ: {TOKEN}\nVERDICT: defect\nWHY: it returns {{'a': 1}} for the set {{1, 2}}\n"
    assert _report(said)["verdict"] == "defect"


# --- 5: a confirmation drops the rejected outcome's tests from every candidate -------------------------------------------

def test_confirmation_removes_rejected_test_from_every_candidate():
    task = "Fix add(a, b). For example, add(-2, 2) == 0."
    sources = {"calc.py": "def add(a, b):\n    return a + b\n",
               "tests/test_calc.py": "from calc import add\ndef test_float():\n    assert add(-2.0, 2) == 0\n"}
    with semantic.request(llm=None):
        found = cs.from_sources(sources, task, repo="fixture")
    raw = found["question"]["candidate"]
    candidate, = bq.caller_candidates(task, [raw])
    question, rule = candidate.questions("b" * 64)
    reading = bq.read("accept", {"choice": "B", "name": "Fixture"}, question, rule, answered_at="2026-11-19T00:00:00Z")
    applied = cs.with_confirmed(found, ExpectationStandard().with_expectation(ConfirmedExpectation(question,
                                                                                                    reading.answer)))
    assert next(i for i in applied["standard"] if i["id"] == raw["requirement_id"])["checks"] == "missing"
    assert not any("tests/test_calc.py::test_float" in (i["checks"] if isinstance(i["checks"], list) else [])
                   for i in applied["standard"]), applied["standard"]
    proposed, instances, _, _ = cs.checks(applied, sources, before=sources)
    assert any(i.bindings["cases"] == [{"args": [-2.0, 2], "raises": "TypeError"}] for i in instances)
    assert not [p for p in proposed if p.file == "tests/test_calc.py" and p.test == "test_float"]
