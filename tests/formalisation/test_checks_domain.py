# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Check 2: domain, reachability and consistency, asked apart; the design's frozen interpretation fixtures."""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.harness.formalisation.checks import check_domain  # noqa: E402
from tests.formalisation._cases import example, relation  # noqa: E402

ANSWERS = {"domain_has_witness", "guards_reachable", "jointly_satisfiable", "constrains_behaviour", "observations"}


def test_interpretation_fixtures():
    useful = check_domain(relation("(= result (+ x 1))", domain=()))   # free result = x + 1: a useful relation
    assert useful.status == "passed" and set(useful.detail) == ANSWERS

    nothing = check_domain(relation("true", domain=()))
    assert nothing.status == "failed" and "constrains nothing" in nothing.reasons[0]
    assert nothing.detail["constrains_behaviour"] == "no_witness_for_this_encoding"

    unreachable = check_domain(relation("(=> (> x 100) (= result (+ x 1)))"))
    assert unreachable.status == "failed" and "unreachable in the domain" in unreachable.reasons[0]
    assert unreachable.detail["guards_reachable"][0] == {
        "guard": "(> x 100)", "reachable": "no_witness_for_this_encoding", "holds_with_statement": "not asked"}

    text = "(and (=> (> x 5) (= result 0)) (=> (> x 7) (= result 1)))"
    conflict = check_domain(relation(text))
    assert conflict.status == "failed" and conflict.detail["jointly_satisfiable"] == "witness"
    assert any("a conflict inside the domain: where (> x 7) holds" in why for why in conflict.reasons)
    # the same statement on a domain that never reaches the conflict has none: it arises only within 0..10
    assert not any("conflict" in why for why in check_domain(relation(text, domain=("(>= x 0)", "(<= x 5)"))).reasons)

    empty = check_domain(relation("(= result (+ x 1))", domain=("(> x 10)", "(< x 0)")))
    assert empty.status == "failed" and "the domain is empty" in empty.reasons[0] and len(empty.queries) == 1
    assert empty.detail["guards_reachable"] == "not asked: the domain is empty"


def test_four_answers_are_reported_apart():
    found = check_domain(relation("(=> (> x 5) (= result 0))"), [example([7], {"returns": 0}, {"returns": 1})])
    assert found.status == "passed" and set(found.detail) == ANSWERS
    assert found.detail["domain_has_witness"] == "witness" and found.detail["jointly_satisfiable"] == "witness"
    assert found.detail["guards_reachable"] == [
        {"guard": "(> x 5)", "reachable": "witness", "holds_with_statement": "witness"}]
    assert found.detail["observations"] == {"accepted_permitted": 1, "rejected_excluded": 1, "cases": 1}
    asked = [q.query.question for q in found.queries]
    assert asked == ["witness"] * 7 and all(q.query.quantifier == "exists" for q in found.queries)


def test_assumptions_apart_from_claim():
    """A specification relation need not hold for every unconstrained output: result = x + 1 is not valid, and passes."""
    from g6.formal import check_validity

    one = relation("(= result (+ x 1))")
    assert check_validity(list(one.domain), one.text, sorts={"x": "Int", "result": "Int"}).valid is False
    assert check_domain(one).status == "passed"
    assumed = relation("(= result (+ x 1))", assumptions=("(> x 20)",))   # an assumption the domain contradicts
    assert check_domain(assumed).status == "failed" and "domain is empty" in check_domain(assumed).reasons[0]


def test_rejected_observation_must_violate_the_relation():
    found = check_domain(relation("(>= result x)"), [example([3], {"returns": 4}, {"returns": 5})])
    assert found.status == "failed" and "permits an outcome the person rejected" in found.reasons[0]


def test_named_entailment_obligations_go_through_check_contract(monkeypatch):
    from g6 import formal

    calls = []
    real = formal.check_contract

    def spy(properties, **kwargs):
        calls.append((tuple(properties), tuple(kwargs["premises"])))
        return real(properties, **kwargs)

    monkeypatch.setattr(formal, "check_contract", spy)
    assert check_domain(relation("(= result (+ x 1))")).status == "passed" and calls == []
    holds = check_domain(relation("(= result (+ x 1))", entails={"positive": "(> result 0)"}))
    assert holds.status == "passed" and holds.detail["named_obligations"][0]["value"] == "T"
    assert calls == [(("(> result 0)",), ("(>= x 0)", "(<= x 10)", "(= result (+ x 1))"))]
    broken = check_domain(relation("(= result (+ x 1))", entails={"small": "(< result 5)"}))
    assert broken.status == "failed" and "named obligation entails_small does not follow" in broken.reasons[0]


@pytest.mark.parametrize("why", ["incomplete", "timeout"])
def test_undecided_is_incomplete(monkeypatch, why):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, why))
    found = check_domain(relation("(=> (> x 100) (= result (+ x 1)))"))
    assert found.status == "incomplete" and found.detail["domain_has_witness"] == "undecided"
    assert all(not q.decided for q in found.queries)


def test_unsupported_statement_is_unsupported():
    import dataclasses

    assert check_domain(dataclasses.replace(relation("true"), adapter="")).status == "unsupported"