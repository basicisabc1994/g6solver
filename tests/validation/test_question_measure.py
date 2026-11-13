# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The question slice's measurement, offline, with fakes (G6 0.3 design, section 5; slice 3, items 6 and 7).

- The simulated maintainer runs the scenario on the gold fix, as each outcome's example.v1 check, and picks the
  outcome that matches; it is recorded as ``simulated:maintainer``, never ``person:``, and the product refuses it.
- The harness counts unnecessary, missed and harmful questions and the person's time, each with its denominator, and
  calls no model.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from g6.harness import behaviour_question as bq
from g6.harness import question_store as qs
from g6.harness.confirmed_expectation import Answer, Unconfirmed
from g6.harness.verification_templates import template_instances

MEASURE = Path(__file__).resolve().parents[2] / "validation" / "question_slice" / "measure.py"
QUESTION = {"requirement_id": "R1", "function": "shop.prices.total", "args": [[2, -3]], "kwargs": {},
            "outcomes": {"A": {"returns": -1}, "B": {"raises": "ValueError"}}}


def _measure():
    spec = importlib.util.spec_from_file_location("question_slice_measure", MEASURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _measure()


def gold(outcome: dict):
    """A fake runner for the gold fix: an outcome's example.v1 check passes there when its one case states
    ``outcome``."""
    def passes(instance: dict) -> bool:
        [case] = instance["bindings"]["cases"]
        return {k: v for k, v in case.items() if k not in ("args", "kwargs")} == outcome
    return passes


def test_each_outcome_is_its_example_v1_check_a_review_would_take():
    found = m.outcome_instances(QUESTION)
    assert set(found) == {"A", "B"}
    assert [one.template for one in template_instances(list(found.values()), {"R1"})] == ["example.v1"] * 2


def test_the_simulated_maintainer_picks_the_outcome_the_gold_fix_gives():
    got = m.simulated_answer(QUESTION, gold({"raises": "ValueError"}))
    assert got["option"] == "B" and got["answered_by"] == m.SIMULATED and got["note"] == m.UPPER_BOUND
    assert m.simulated_answer(QUESTION, gold({"returns": 0}))["option"] == "neither"
    assert m.simulated_answer(QUESTION, lambda one: True)["option"] == "neither"   # both pass: it cannot tell


def test_the_product_refuses_the_simulated_maintainer_as_a_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    [one] = bq.caller_candidates("A negative price is refused.", [{**QUESTION, "cited": "A negative price is refused.",
                                                                   "evidence": ["shop/prices.py"]}])
    case, _ = one.questions("b" * 64)
    with pytest.raises(Unconfirmed):
        Answer(question_sha256=case.sha256, option="B", answered_by=m.SIMULATED, answered_at="2026-11-07T10:00:00Z")
    # the dialog's reader records every answer as person: and the name given, so none through it is the simulator's
    typed = bq.read("accept", {"choice": "B", "name": m.SIMULATED}, case, None, answered_at="2026-11-07T10:00:00Z")
    assert typed.answer.answered_by == f"person:{m.SIMULATED}"
    store = qs.TaskQuestions(qs.task_identity(tmp_path, "t"))
    with pytest.raises(qs.AnswerRefused):
        store.answer(m.simulated_answer(QUESTION, gold({"raises": "ValueError"})))


def test_the_counts_each_carry_their_denominator_and_no_model_is_called():
    person = {"answered_by": "person:Ada", "seconds": 30.0}
    cases = [
        {"case": "decided", "asked": True, "task_decides": True, "answer": {"option": "B", **person},
         "gold_option": "B", "final_correct": True},
        {"case": "said-so", "asked": True, "answer": {"option": "task_already_says", **person}, "final_correct": True},
        {"case": "harm", "asked": True, "answer": {"option": "A", "answered_by": "person:Ada", "seconds": 90.0},
         "gold_option": "B", "final_correct": False},
        {"case": "missed", "asked": False, "answer": None, "catchable": True, "final_correct": False},
        {"case": "fine", "asked": False, "answer": None, "final_correct": True},
        {"case": "simulated", "asked": True, "answer": {"option": "B", "answered_by": m.SIMULATED},
         "gold_option": "B", "final_correct": True}]
    got = m.measure(cases)
    assert got["cases"] == 6 and got["asked"] == {"count": 4, "of": 6} and got["model_calls"] == 0
    assert got["unnecessary"] == {"count": 2, "of": 4, "cases": ["decided", "said-so"]}
    assert got["missed"] == {"count": 1, "of": 2, "cases": ["missed"]}
    assert got["harmful"] == {"count": 1, "of": 3, "cases": ["harm"]}
    assert got["person_time"] == {"answers": 3, "seconds_total": 150.0, "seconds_median": 30.0}
    assert got["answered_by_simulated_maintainer"] == 1 and got["note"] == m.UPPER_BOUND


def test_the_simulated_maintainer_is_an_upper_bound_it_causes_no_harm_by_construction():
    question = {**QUESTION}
    answer = m.simulated_answer(question, gold({"returns": -1}))
    got = m.measure([{"case": "c", "asked": True, "answer": answer, "gold_option": "A", "final_correct": False}])
    assert got["harmful"]["count"] == 0 and "upper bound" in got["note"]
