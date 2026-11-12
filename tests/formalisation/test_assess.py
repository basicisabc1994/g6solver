# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The assessment: five outcomes, always; ready for the person only when all five passed; it never accepts."""
from __future__ import annotations

import typing

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.harness.formalisation.adapters import from_expectation  # noqa: E402
from g6.harness.formalisation.checks import CHECKS, Assessment, CheckOutcome, assess  # noqa: E402
from g6.harness.confirmed_expectation import ExpectationStandard  # noqa: E402
from tests.formalisation._cases import confirmed, relation  # noqa: E402

FIRST = confirmed([3], {"A": {"returns": 4}, "B": {"returns": 5}})
STANDARD = ExpectationStandard((FIRST,))
CASES = (from_expectation(FIRST),)
AGREED = CheckOutcome("comparison", "passed", ())
RIGHT = relation("(= result (+ x 1))")


def test_all_five_passed_is_ready_for_the_person_and_nothing_more():
    found = assess(RIGHT, STANDARD, CASES, comparison=AGREED)
    assert found.status == "ready_for_person" and found.reasons == ()
    assert tuple(one.check for one in found.outcomes) == CHECKS == ("examples", "domain", "comparison", "rendering",
                                                                   "scope")
    assert all(one.status == "passed" for one in found.outcomes)
    assert found.rendering.statement_sha256 == RIGHT.sha256 and found.scope.rendering_sha256 == found.rendering.sha256
    hints = typing.get_type_hints(Assessment)
    assert set(typing.get_args(hints["status"])) == {"unconfirmed", "ready_for_person"}   # no accepting state
    assert not any("accept" in name for name in vars(found))
    assert len(found.sha256) == 64 and found.sha256 == assess(RIGHT, STANDARD, CASES, comparison=AGREED).sha256


def test_no_comparison_is_unconfirmed():
    found = assess(RIGHT, STANDARD, CASES)
    assert found.status == "unconfirmed" and found.outcomes[2].status == "incomplete"
    assert any(why.startswith("comparison:") for why in found.reasons)
    assert [one.status for one in found.outcomes] == ["passed", "passed", "incomplete", "passed", "passed"]


@pytest.mark.parametrize("status", ["failed", "incomplete", "unsupported", "unavailable"])
def test_one_incomplete_is_unconfirmed(status):
    found = assess(RIGHT, STANDARD, CASES, comparison=CheckOutcome("comparison", status, ("why",)))
    assert found.status == "unconfirmed" and "comparison: why" in found.reasons
    with pytest.raises(ValueError):
        assess(RIGHT, STANDARD, CASES, comparison=CheckOutcome("examples", "passed", ()))


def test_no_examples_is_unconfirmed():
    found = assess(RIGHT, ExpectationStandard(()), (), comparison=AGREED)
    assert found.status == "unconfirmed" and found.outcomes[0].status == "incomplete"


def test_failed_keeps_diagnostics_and_examples():
    wrong = relation("(>= result x)")
    found = assess(wrong, STANDARD, CASES, comparison=AGREED)
    assert found.status == "unconfirmed" and found.outcomes[0].status == "failed"
    assert any("permits an outcome the person rejected" in why for why in found.reasons)
    assert found.outcomes[0].queries and found.outcomes[0].queries[-1].witness == {"x": 3, "result": 5}
    assert found.statement is wrong and found.rendering is not None and found.scope is not None
    assert STANDARD.expectations == (FIRST,) and found.cases == CASES   # the confirmed examples are kept as they were


def test_unrenderable_statement_has_no_scope_and_is_unconfirmed():
    import dataclasses

    odd = dataclasses.replace(RIGHT, text="(forall ((z Int)) (> z 0))")
    found = assess(odd, STANDARD, CASES, comparison=AGREED)
    assert found.status == "unconfirmed" and found.rendering is None and found.scope is None
    assert [one.status for one in found.outcomes] == ["unsupported", "unsupported", "passed", "unsupported",
                                                      "incomplete"]


def test_undecided_engine_is_unconfirmed(monkeypatch):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, "timeout"))
    found = assess(RIGHT, STANDARD, CASES, comparison=AGREED)
    assert found.status == "unconfirmed" and found.outcomes[0].status == found.outcomes[1].status == "incomplete"