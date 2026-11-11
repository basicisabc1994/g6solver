# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A confirmed answer is a frozen record of what the person answered (G6 0.3, slice 2, repair 1; review 1's findings
3 and 6).

- The question's scenario and the compiled check are kept as they were when the person was shown and answered them:
  nested lists, dictionaries and arguments a caller changes afterwards, in the objects it passed or in those it read
  back, rewrite nothing; bytes, which no example.v1 literal holds, are refused before anything is shown (finding 3).
- "Contradicts" is decided by the answer itself, the compiled scenario and its outcome: the same answer to a question
  with another citation is the same answer, held once; another outcome for the same scenario still contradicts and
  starts a superseding standard (finding 6).
"""
from __future__ import annotations

import copy
import pickle
from dataclasses import replace

import pytest

from g6.harness.confirmed_expectation import ContradictsStandard, ExpectationStandard, Question, confirm

BASE = "a" * 64


def question(**over) -> Question:
    base = {"requirement_id": "R1", "function": "shop.total", "args": [[2, 3]],
            "outcomes": {"A": {"returns": 5}, "B": {"raises": "ValueError"}}, "scope": "one input",
            "cited": "task paragraph 1", "baseline_sha256": BASE}
    return Question(**{**base, **over})


# --- review 1's findings, brought in as this change's own -------------------------------------------------------------


def test_finding_3_mutating_question_arguments_does_not_rewrite_a_confirmed_standard():
    arguments = [[2, 3]]
    shown = Question(requirement_id="R1", function="shop.total", args=arguments,
                     outcomes={"A": {"returns": 5}, "B": {"returns": 6}}, scope="this input", cited="task",
                     baseline_sha256=BASE)
    original_question = copy.deepcopy(shown)
    confirmed = confirm(shown, "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(confirmed)
    frozen_hash = standard.sha256
    arguments[0][:] = [100, 200]
    assert confirmed.for_question(original_question) is confirmed
    assert confirmed.instance.bindings["cases"][0]["args"] == [[2, 3]], (
        "confirmed check was rewritten through the question's caller-owned argument list", confirmed.instance.bindings)
    assert standard.sha256 == frozen_hash


def test_finding_6_a_consistent_answer_to_a_new_question_does_not_require_superseding():
    first = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first)
    repeated = confirm(replace(question(), cited="task paragraph 2"), "A", answered_by="person:owner")
    assert first.instance.bindings == repeated.instance.bindings
    assert standard.contradicted_by(repeated) == (), (
        "the exact same input and outcome was classified as a contradicting answer", standard.contradicted_by(repeated))


# --- frozen from the moment of confirmation -----------------------------------------------------------------------


def test_nested_dicts_and_lists_the_caller_changes_after_confirmation_rewrite_nothing():
    args, kwargs = [{"items": [1, 2]}], {"rounding": {"mode": "half_up", "digits": [2]}}
    outcomes = {"A": {"returns": {"total": 3}}, "B": {"effect": {"argument": 0, "equals": {"items": [1, 2, 3]}}}}
    shown = question(args=args, kwargs=kwargs, outcomes=outcomes)
    asked = shown.sha256
    for answer in ("A", "B"):
        got = confirm(shown, answer, answered_by="person:owner")
        before = (got.check_sha256, got.to_dict())
        args[0]["items"].append(99)
        kwargs["rounding"]["digits"][0] = 7
        outcomes["A"]["returns"]["total"] = 4
        outcomes["B"]["effect"]["equals"]["items"].clear()
        assert (got.check_sha256, got.to_dict()) == before
        assert shown.sha256 == asked and got.for_question(shown) is got
        args[0]["items"].pop()
        kwargs["rounding"]["digits"][0] = 2
        outcomes["A"]["returns"]["total"] = 3
        outcomes["B"]["effect"]["equals"]["items"].extend([1, 2, 3])


def test_what_a_reader_takes_from_a_frozen_record_is_a_copy():
    shown = question()
    got = confirm(shown, "A", answered_by="person:owner")
    taken = got.instance
    taken.bindings["cases"][0]["args"][0].append(4)
    taken.bindings["function"] = "shop.other"
    assert got.instance.bindings == {"function": "shop.total", "cases": [{"args": [[2, 3]], "returns": 5}]}
    shown.args[0].append(9)
    shown.outcomes["A"]["returns"] = 6
    assert shown.args == [[2, 3]] and shown.outcomes["A"] == {"returns": 5}
    assert got.for_question(shown) is got


@pytest.mark.parametrize("value", [b"\x00\x01", bytearray(b"ab"), (1, 2), {1, 2}, float("nan")])
def test_a_value_no_example_v1_literal_holds_is_refused_before_it_is_shown(value):
    # bytes, a bytearray that could change after the person answers, a tuple, a set, a number that is not finite
    with pytest.raises(ValueError):
        question(args=[value])
    with pytest.raises(ValueError):
        question(kwargs={"data": value})
    with pytest.raises(ValueError):
        question(outcomes={"A": {"returns": value}, "B": {"returns": 1}})


@pytest.mark.parametrize("over", [{"outcomes": [["A", {"returns": 5}]]}, {"outcomes": {"A": 5, "B": 6}},
                                  {"kwargs": [1]}])
def test_a_malformed_scenario_is_refused(over):
    with pytest.raises(ValueError):
        question(**over)


def test_a_frozen_record_survives_a_copy_and_a_restart_unchanged():
    got = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(got)
    for again in (copy.deepcopy(got), pickle.loads(pickle.dumps(got))):
        assert again == got and again.check_sha256 == got.check_sha256 and again.to_dict() == got.to_dict()
    restored = pickle.loads(pickle.dumps(standard))
    assert restored.sha256 == standard.sha256 and restored.predicates == standard.predicates


# --- "contradicts" is decided by the answer itself ------------------------------------------------------------------


def test_the_same_answer_under_another_citation_is_held_once():
    first = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first)
    for cited in ("task paragraph 2", "the issue's title"):
        repeated = confirm(question(cited=cited), "A", answered_by="person:owner")
        assert repeated.question_sha256 != first.question_sha256   # another question, the same answer
        assert standard.contradicted_by(repeated) == ()
        assert standard.with_expectation(repeated) is standard


def test_another_outcome_for_the_same_scenario_still_contradicts_under_any_citation():
    first = confirm(question(), "A", answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first)
    later = confirm(question(cited="task paragraph 2"), "B", answered_by="person:owner")
    assert standard.contradicted_by(later) == (first.expectation_id,)
    with pytest.raises(ContradictsStandard):
        standard.with_expectation(later)
    superseding = standard.superseding(later)
    assert superseding.replaced == (first.expectation_id,) and superseding.predicates == (later.expectation_id,)


def test_another_scenario_is_another_answer():
    first = confirm(question(), "A", answered_by="person:owner")
    other = confirm(question(args=[[1, 1]], outcomes={"A": {"returns": 2}, "B": {"returns": 3}}), "A",
                    answered_by="person:owner")
    standard = ExpectationStandard().with_expectation(first)
    assert standard.contradicted_by(other) == ()
    assert standard.with_expectation(other).predicates == (first.expectation_id, other.expectation_id)
