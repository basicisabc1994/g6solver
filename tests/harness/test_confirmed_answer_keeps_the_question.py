# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A confirmed answer cannot change the question (G6 0.3, slice 2, repair 2; review 2's finding 2; class
``untrusted_input_given_authority``, met before).

The scenario (the function, its arguments and keyword arguments; example.v1 has no setup) comes from the question
alone. An answer contributes only its outcome, through the outcome fields example.v1 defines; any other key in an
outcome is refused, each scenario field planted in one is tested, and a confirmed expectation whose check runs another
scenario never answers the question.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from g6.harness import confirmed_expectation as ce
from g6.harness.confirmed_expectation import ConfirmedExpectation, Question, confirm
from g6.harness.verification_templates import Call, ExampleCase, TemplateInstance

BASE = hashlib.sha256(b"the base commit's tree").hexdigest()


def question(**over) -> Question:
    base = {"requirement_id": "R1", "function": "shop.total", "args": [[2, 3]],
            "outcomes": {"A": {"returns": 5}, "B": {"returns": 6}}, "scope": "sum the displayed input",
            "cited": "task", "baseline_sha256": BASE}
    return Question(**{**base, **over})


def test_finding_2_an_outcome_cannot_replace_the_question_s_scenario():
    try:
        planted = question(outcomes={"A": {"args": [[100]], "returns": 5}, "B": {"returns": 6}})
        made = confirm(planted, "A", answered_by="person:owner")
    except ValueError as exc:   # refused when the question is made: the scenario is the question's alone
        assert "args" in str(exc) and "scenario" in str(exc), exc
    else:
        case = made.instance.bindings["cases"][0]
        assert case["args"] == planted.args, (
            "the confirmed check calls another input than the question's scenario", case, planted.args)
        raise AssertionError("an outcome that restates the scenario was accepted")
    shown = question()
    confirmed = confirm(shown, "A", answered_by="person:owner")
    assert confirmed.for_question(shown) is confirmed
    case = confirmed.instance.bindings["cases"][0]
    assert case["args"] == shown.args == [[2, 3]] and case["returns"] == 5


@pytest.mark.parametrize(("field", "value"), [
    ("args", [[100]]), ("kwargs", {"rate": 2}), ("function", "shop.other"), ("setup", {"seed": 1}),
    ("cases", [{"args": [[1]], "returns": 1}]), ("requirement_id", "R9"), ("verified", True),
])
def test_each_scenario_field_planted_in_an_outcome_is_refused(field, value):
    with pytest.raises(ValueError, match=field):
        question(outcomes={"A": {field: value, "returns": 5}, "B": {"returns": 6}})
    with pytest.raises(ValueError, match=field):   # in the outcome that is not chosen too: the person is shown it
        question(outcomes={"A": {"returns": 5}, "B": {field: value, "returns": 6}})


def test_the_outcome_fields_are_the_template_s_own():
    # read from example.v1's case: every field of a case that is not its call's
    assert ce.OUTCOME_FIELDS == frozenset(ExampleCase.model_fields) - frozenset(Call.model_fields)
    assert ce.OUTCOME_FIELDS == {"returns", "raises", "effect"}
    for outcome in ({"returns": 5}, {"raises": "ValueError"}, {"effect": {"argument": 0, "equals": [2, 3]}}):
        shown = question(outcomes={"A": outcome, "B": {"returns": 6}})
        case = confirm(shown, "A", answered_by="person:owner").instance.bindings["cases"][0]
        assert case == {"args": [[2, 3]], **outcome}


def test_the_question_s_keyword_arguments_are_kept_and_never_an_outcome_s():
    shown = question(kwargs={"rate": 1})
    case = confirm(shown, "A", answered_by="person:owner").instance.bindings["cases"][0]
    assert case == {"args": [[2, 3]], "kwargs": {"rate": 1}, "returns": 5}


def test_a_confirmed_expectation_whose_check_runs_another_scenario_does_not_answer_the_question():
    # repair 3: an answer never carries a check, so none can run another scenario; what it means is compiled from the
    # question's bytes and the chosen option every time (this test held the earlier shape, which carried one)
    shown = question()
    honest = confirm(shown, "A", answered_by="person:owner")
    elsewhere = TemplateInstance(id=honest.instance.id, template="example.v1", requirement_id="R1", intent="change",
                                 provenance="person_confirmed",
                                 bindings={"function": "shop.total", "cases": [{"args": [[100]], "returns": 5}]})
    with pytest.raises(ValueError, match="instance"):
        ConfirmedExpectation(shown, honest.answer, instance=elsewhere)
    with pytest.raises(ValueError, match="scenario"):
        replace(honest, instance=elsewhere)
    # an answer that chose B means the question's scenario with outcome B: it can state no other outcome than it chose
    other_outcome = ConfirmedExpectation(shown, replace(honest.answer, option="B"))
    assert other_outcome.instance.bindings == {"function": "shop.total", "cases": [{"args": [[2, 3]], "returns": 6}]}
    assert other_outcome.for_question(shown) is other_outcome
    assert honest.for_question(shown) is honest   # the positive control
