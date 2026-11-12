# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Check 1: the confirmed examples through the same observation, with the rejected outcomes as witnesses."""
from __future__ import annotations

import dataclasses

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.harness.formalisation.checks import check_examples  # noqa: E402
from tests.formalisation._cases import example, relation  # noqa: E402

CASE = [example([3], {"returns": 4}, {"returns": 5})]


def test_accepts_right_rejects_wrong():
    right = check_examples(relation("(= result (+ x 1))"), CASE)
    assert right.status == "passed" and right.reasons == ()
    assert [q.reading for q in right.queries] == ["witness", "no_witness_for_this_encoding"]
    assert dict(right.detail) == {"accepted_permitted": 1, "rejected_excluded": 1, "cases": 1}

    plus_two = check_examples(relation("(= result (+ x 2))"), CASE)
    assert plus_two.status == "failed" and "does not permit the confirmed outcome" in plus_two.reasons[0]

    loose = check_examples(relation("(>= result x)"), CASE)   # it permits the 5 the person rejected
    assert loose.status == "failed" and "permits an outcome the person rejected" in loose.reasons[0]
    assert [q.reading for q in loose.queries] == ["witness", "witness"]


def test_example_outside_the_domain_fails():
    found = check_examples(relation("(= result (+ x 1))", domain=("(> x 5)",)), CASE)
    assert found.status == "failed" and "does not permit the confirmed outcome" in found.reasons[0]


def test_no_examples_is_incomplete():
    found = check_examples(relation("(= result (+ x 1))"), [])
    assert found.status == "incomplete" and "no confirmed example" in found.reasons[0] and found.queries == ()


def test_unsupported_stays_visible():
    effect = example([3], {"effect": {"argument": 0, "equals": [1]}}, {"returns": 5})
    found = check_examples(relation("(= result (+ x 1))"), [*CASE, effect])
    assert found.status == "unsupported" and any("never stands for an effect" in why for why in found.reasons)
    floating = example([1.5], {"returns": 2}, {"returns": 3})
    assert check_examples(relation("(= result (+ x 1))"), [floating]).status == "unsupported"
    # a rejected outcome the adapter cannot bind is a missing control, and stays visible too
    odd = example([3], {"returns": 4}, {"raises": "ValueError"})
    found = check_examples(relation("(= result (+ x 1))"), [odd])
    assert found.status == "unsupported" and "a rejected outcome" in found.reasons[0]
    # a decided violation is still reported as failed beside an unsupported case
    assert check_examples(relation("(= result (+ x 2))"), [*CASE, effect]).status == "failed"


def test_statement_without_an_adapter_or_outside_it_is_unsupported():
    one = relation("(= result (+ x 1))")
    assert check_examples(dataclasses.replace(one, adapter="smt.nothing.v1"), CASE).status == "unsupported"
    assert check_examples(dataclasses.replace(one, text="(forall ((z Int)) true)"), CASE).status == "unsupported"


def test_raise_and_return_examples_with_a_guard():
    guarded = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError", domain=())
    cases = [example([-1], {"raises": "ValueError"}, {"returns": 0}), example([3], {"returns": 4}, {"raises": "ValueError"})]
    assert check_examples(guarded, cases).status == "passed"
    wrong_guard = relation("(= result (+ x 1))", raises_when="(< x (- 5))", error="ValueError", domain=())
    found = check_examples(wrong_guard, cases)
    assert found.status == "failed" and len(found.reasons) == 2   # the raise is not permitted, the return 0 is


@pytest.mark.parametrize("why", ["incomplete", "timeout"])
def test_undecided_is_incomplete_never_passed_or_failed(monkeypatch, why):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, why))
    for text in ("(= result (+ x 1))", "(= result (+ x 2))"):
        found = check_examples(relation(text), CASE)
        assert found.status == "incomplete" and all("did not decide" in r for r in found.reasons)


def test_unavailable_engine_is_unavailable(monkeypatch):
    from g6.formal.envelope import SOLVER_UNAVAILABLE, Refusal

    def gone(query, timeout):
        raise Refusal(SOLVER_UNAVAILABLE, "no z3 here")

    monkeypatch.setattr(z3_bridge, "run_query", gone)
    assert check_examples(relation("(= result (+ x 1))"), CASE).status == "unavailable"