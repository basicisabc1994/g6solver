# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A confirmed answer is compared by its bytes, never by Python equality, and a standard is immutable (G6 0.3, slice 2,
repair 3; review 3's findings 1 to 3; classes ``untrusted_input_given_authority``, ``one_fact_two_rules`` and
``check_then_act_race``, each met before in this module).

- An answer names only what the person chose; it repeats nothing the question holds, so it cannot name another
  artefact than the question showed (finding 1).
- What an answer means is compiled from the question's bytes every time, and "for this question", "the same answer" and
  "contradicts" compare canonical JSON, so a flag is never taken for a number, nor numeric text or a float for an int;
  a tuple is not JSON data and is refused before anything is shown (finding 2).
- A standard copies the records it is given into a tuple of its own when it is made: nothing a caller holds afterwards
  changes it, and adding an answer makes a new standard, a revision when it agrees and a superseding one when it
  contradicts (finding 3).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from g6.core.ledger import canonical
from g6.harness.confirmed_expectation import ContradictsStandard, ExpectationStandard, Question, confirm

BASE = "a" * 64


def question(**over) -> Question:
    base = {"requirement_id": "R1", "function": "shop.total", "args": [1],
            "outcomes": {"A": {"returns": 1}, "B": {"returns": 2}}, "scope": "the displayed input", "cited": "task",
            "baseline_sha256": BASE}
    return Question(**{**base, **over})


def by_person(shown: Question, option: str = "A"):
    return confirm(shown, option, answered_by="person:owner")


def case_of(one) -> dict:
    return one.instance.bindings["cases"][0]


# --- review 3's findings, brought in as this change's own -------------------------------------------------------------


def test_finding_1_an_answer_naming_another_artefact_than_the_question_showed_is_refused():
    shown = Question(
        requirement_id="R1", function="shop.total", args=[1],
        outcomes={"A": {"returns": 1}, "B": {"returns": 2}},
        scope="the displayed input", cited="task", baseline_sha256="a" * 64,
    )
    honest = confirm(shown, "A", answered_by="person:owner")
    assert honest.for_question(shown) is honest
    with pytest.raises(ValueError, match="artefact|artifact|question|binding"):
        # The original question hash and compiled scenario are retained, while
        # the answer now claims that the person answered about different bytes.
        forged = replace(honest, artefact_sha256="b" * 64)
        forged.for_question(shown)


def test_finding_2_an_answer_s_compiled_scenario_keeps_the_question_s_json_argument_types():
    shown = Question(
        requirement_id="R1", function="shop.is_integer", args=[1],
        outcomes={"A": {"returns": True}, "B": {"returns": False}},
        scope="the displayed input", cited="task", baseline_sha256="a" * 64,
    )
    honest = confirm(shown, "A", answered_by="person:owner")
    assert honest.for_question(shown) is honest
    bindings = honest.instance.bindings
    bindings["cases"][0]["args"] = [True]
    changed = honest.instance.model_copy(update={"bindings": bindings})

    def is_integer(value):   # a function can distinguish the inputs, and G6's own check digest does
        return type(value) is int

    assert is_integer(1) is True and is_integer(True) is False

    def broken_is_integer(value):   # fails the scenario the person answered, passes the substituted one
        return type(value) is bool

    assert broken_is_integer(shown.args[0]) is False
    assert broken_is_integer(bindings["cases"][0]["args"][0]) is True
    with pytest.raises(ValueError, match="scenario|question|binding"):
        forged = replace(honest, instance=changed)
        assert forged.check_sha256 != honest.check_sha256
        forged.for_question(shown)


def test_finding_3_a_standard_cannot_gain_a_contradiction_through_its_input_list():
    shown = question(scope="one input")
    first = by_person(shown, "A")
    later = by_person(shown, "B")
    supplied = [first]
    standard = ExpectationStandard(expectations=supplied)
    before = standard.sha256
    assert standard.contradicted_by(later) == (first.expectation_id,)
    supplied.append(later)
    assert standard.revision == 1 and standard.supersedes == ""
    assert standard.sha256 == before, "a frozen standard changed without superseding or a new revision"
    assert standard.predicates == (first.expectation_id,)


# --- a flag for a number, numeric text, a float for an int, a tuple for a list, in a scenario ----------------------

#: What the question showed, and what is planted in its place: Python equality takes each pair for one value
#: (``1 == True``, ``1 == 1.0``, ``0 == False``), or differs only in type ("1"); their canonical JSON never does.
PLANTED = [(1, True), (0, False), (1, 1.0), (2, 2.0), (1, "1")]


@pytest.mark.parametrize(("shown_value", "planted"), PLANTED)
def test_another_type_in_the_arguments_is_another_question(shown_value, planted):
    shown, other = question(args=[shown_value]), question(args=[planted])
    assert shown.sha256 != other.sha256
    got, elsewhere = by_person(shown), by_person(other)
    with pytest.raises(ValueError, match="another question"):
        got.for_question(other)
    with pytest.raises(ValueError, match="another question"):
        elsewhere.for_question(shown)
    assert type(case_of(got)["args"][0]) is type(shown_value) and type(case_of(elsewhere)["args"][0]) is type(planted)
    assert got.check_sha256 != elsewhere.check_sha256
    standard = ExpectationStandard().with_expectation(got)
    assert standard.contradicted_by(elsewhere) == ()   # another scenario: no contradiction, and not the same answer
    assert standard.with_expectation(elsewhere).predicates == (got.expectation_id, elsewhere.expectation_id)


@pytest.mark.parametrize(("shown_value", "planted"), PLANTED)
def test_another_type_in_the_keyword_arguments_is_another_question(shown_value, planted):
    shown, other = question(kwargs={"rate": shown_value}), question(kwargs={"rate": planted})
    assert shown.sha256 != other.sha256
    got, elsewhere = by_person(shown), by_person(other)
    with pytest.raises(ValueError, match="another question"):
        got.for_question(other)
    assert type(case_of(got)["kwargs"]["rate"]) is type(shown_value)
    assert ExpectationStandard().with_expectation(got).contradicted_by(elsewhere) == ()


@pytest.mark.parametrize(("shown_value", "planted"), PLANTED)
def test_another_type_in_an_outcome_is_another_answer_and_contradicts(shown_value, planted):
    shown = question(outcomes={"A": {"returns": shown_value}, "B": {"returns": planted}})
    one, other = by_person(shown, "A"), by_person(shown, "B")
    assert canonical(one.instance.bindings) != canonical(other.instance.bindings)
    assert type(case_of(one)["returns"]) is type(shown_value) and type(case_of(other)["returns"]) is type(planted)
    standard = ExpectationStandard().with_expectation(one)
    assert standard.contradicted_by(other) == (one.expectation_id,)
    with pytest.raises(ContradictsStandard):
        standard.with_expectation(other)
    assert standard.superseding(other).predicates == (other.expectation_id,)
    with pytest.raises(ContradictsStandard):
        ExpectationStandard((one, other))


@pytest.mark.parametrize("where", ["args", "nested", "kwargs", "returns", "effect"])
def test_a_tuple_for_a_list_in_a_scenario_is_refused_before_it_is_shown(where):
    planted = {"args": {"args": (1,)}, "nested": {"args": [(1, 2)]}, "kwargs": {"kwargs": {"rate": (1,)}},
               "returns": {"outcomes": {"A": {"returns": (1,)}, "B": {"returns": 2}}},
               "effect": {"args": [[1]], "outcomes": {"A": {"effect": {"argument": 0, "equals": (1,)}},
                                                      "B": {"returns": 2}}}}[where]
    with pytest.raises(ValueError, match="tuple"):
        question(**planted)


# --- a caller-held list or dict changed after each record and each standard is made --------------------------------


def test_a_caller_held_list_or_dict_changed_after_each_record_and_standard_changes_nothing():
    args, kwargs = [[1, 2]], {"rate": {"mode": [1]}}
    outcomes = {"A": {"returns": [3]}, "B": {"effect": {"argument": 0, "equals": [1, 2, 3]}}}
    shown = question(args=args, kwargs=kwargs, outcomes=outcomes)
    asked = (shown.sha256, canonical(shown.to_dict()))
    args[0].append(9)
    kwargs["rate"]["mode"][0] = 7
    outcomes["A"]["returns"].append(9)
    outcomes["B"]["effect"]["equals"].clear()
    outcomes["C"] = {"returns": 0}
    assert (shown.sha256, canonical(shown.to_dict())) == asked
    got, other = by_person(shown, "A"), by_person(shown, "B")
    held = (got.check_sha256, canonical(got.to_dict()))
    for taken in (got.instance.bindings, shown.args, shown.kwargs, shown.outcomes, got.to_dict()):
        taken.clear()   # what a reader takes is a copy
    assert (got.check_sha256, canonical(got.to_dict())) == held and (shown.sha256, canonical(shown.to_dict())) == asked
    assert got.for_question(shown) is got
    supplied, replaced = [got], [got.expectation_id]
    standard = ExpectationStandard(supplied)
    superseding = ExpectationStandard([other], 2, standard.sha256, replaced)
    made = (standard.sha256, standard.predicates, superseding.sha256, superseding.replaced)
    supplied.append(other)
    supplied[0] = other
    replaced.append("f" * 64)
    replaced[0] = "e" * 64
    assert (standard.sha256, standard.predicates, superseding.sha256, superseding.replaced) == made
    assert type(standard.expectations) is tuple and type(superseding.replaced) is tuple


# --- an agreeing answer, a contradicting one, and the same answer under another citation -------------------------


def test_an_agreeing_answer_makes_a_revision_a_contradicting_one_a_superseding_standard_and_a_repeat_is_held_once():
    first = by_person(question())
    standard = ExpectationStandard().with_expectation(first)
    agreeing = by_person(question(args=[2], outcomes={"A": {"returns": 2}, "B": {"returns": 4}}))
    revised = standard.with_expectation(agreeing)
    assert (revised.revision, revised.previous, revised.supersedes) == (standard.revision + 1, standard.sha256, "")
    assert revised.predicates == (first.expectation_id, agreeing.expectation_id)
    repeated = confirm(question(cited="the task, paragraph 2"), "A", answered_by="person:reviewer")
    assert repeated.question_sha256 != first.question_sha256   # another question, the same answer
    assert revised.contradicted_by(repeated) == () and revised.with_expectation(repeated) is revised
    contradicting = by_person(question(cited="the task, paragraph 3"), "B")
    assert revised.contradicted_by(contradicting) == (first.expectation_id,)
    with pytest.raises(ContradictsStandard):
        revised.with_expectation(contradicting)   # never an overwrite
    superseding = revised.superseding(contradicting)
    assert (superseding.supersedes, superseding.replaced) == (revised.sha256, (first.expectation_id,))
    assert superseding.predicates == (agreeing.expectation_id, contradicting.expectation_id)
    assert superseding.revision == revised.revision + 1
    # the standards already made are as they were
    assert standard.predicates == (first.expectation_id,) and revised.predicates == (first.expectation_id,
                                                                                    agreeing.expectation_id)
