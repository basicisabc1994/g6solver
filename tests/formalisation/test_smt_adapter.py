# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The adapter smt.return.int_bool.v1: what it binds, and what stays visible and unsupported."""
from __future__ import annotations

import dataclasses

import pytest

from g6.harness.formalisation import ADAPTERS, smt_adapter, smt_ir
from g6.harness.formalisation.statement import Observation
from tests.formalisation._cases import example, relation

ADAPTER = ADAPTERS["smt.return.int_bool.v1"]
PLUS_ONE = "(= result (+ x 1))"


def test_return_bound():
    one = relation(PLUS_ONE)
    case = example([3], {"returns": 4}, {"returns": 5})
    assert ADAPTER.supports(one) == ""
    assert ADAPTER.bind(one, case, case.accepted) == smt_adapter.Bound(("(= x 3)", "(= result 4)"))
    assert ADAPTER.bind(one, case, case.rejected[0]).conjuncts == ("(= x 3)", "(= result 5)")
    negative = example([-3], {"returns": -2}, {"returns": 0})
    assert ADAPTER.bind(relation(PLUS_ONE, domain=()), negative, negative.accepted).conjuncts == (
        "(= x (- 3))", "(= result (- 2))")


def test_keyword_arguments_are_bound_by_name():
    from tests.formalisation._cases import confirmed
    from g6.harness.formalisation.adapters import from_expectation

    two = relation("(= result (+ x y))", inputs=(("x", "Int"), ("y", "Int")), domain=())
    case = from_expectation(confirmed([1], {"A": {"returns": 3}, "B": {"returns": 4}}, kwargs={"y": 2}))
    assert ADAPTER.bind(two, case, case.accepted).conjuncts == ("(= x 1)", "(= y 2)", "(= result 3)")
    stray = from_expectation(confirmed([1], {"A": {"returns": 3}, "B": {"returns": 4}}, kwargs={"z": 2}))
    assert "keyword 'z'" in ADAPTER.bind(two, stray, stray.accepted).unsupported
    short = example([1], {"returns": 3}, {"returns": 4})
    assert "no value for y" in ADAPTER.bind(two, short, short.accepted).unsupported


def test_raise_needs_guard():
    case = example([-1], {"raises": "ValueError"}, {"returns": 0})
    bare = ADAPTER.bind(relation(PLUS_ONE), case, case.accepted)
    assert bare.conjuncts == () and "raises_when" in bare.unsupported
    guarded = relation(PLUS_ONE, raises_when="(< x 0)", error="ValueError", domain=())
    bound = ADAPTER.bind(guarded, case, case.accepted)
    assert bound == smt_adapter.Bound(("(= x (- 1))", "(< x 0)"), with_relation=False)
    # a confirmed return needs the guard false
    assert ADAPTER.bind(guarded, case, case.rejected[0]).conjuncts == ("(= x (- 1))", "(= result 0)", "(not (< x 0))")
    other = example([-1], {"raises": "KeyError"}, {"returns": 0})
    assert "the example raises KeyError" in ADAPTER.bind(guarded, other, other.accepted).unsupported
    assert "name the error" in ADAPTER.supports(relation(PLUS_ONE, raises_when="(< x 0)"))
    assert "no raises_when guard" in ADAPTER.supports(relation(PLUS_ONE, error="ValueError"))


def test_effect_unsupported():
    case = example([3], {"effect": {"argument": 0, "equals": [1]}}, {"returns": 4})
    bound = ADAPTER.bind(relation(PLUS_ONE), case, case.accepted)
    assert bound.conjuncts == () and "never stands for an effect" in bound.unsupported
    for kind in ("effect", "raises"):
        about = dataclasses.replace(relation(PLUS_ONE), observation=Observation(kind, "shop.f", (("x", "Int"),), "Int"))
        assert "no reviewed encoding" in ADAPTER.supports(about)
        assert ADAPTER.bind(about, case, {"returns": 4}).conjuncts == ()


@pytest.mark.parametrize("args, outcome, names", [
    ([1.5], {"returns": 2}, "a float"), (["3"], {"returns": 4}, "a str"), ([[3]], {"returns": 4}, "a list"),
    ([None], {"returns": 4}, "None"), ([3], {"returns": 4.0}, "a float"), ([3], {"returns": "4"}, "a str"),
    ([3], {"returns": None}, "None"), ([3], {"returns": [4]}, "a list"),
])
def test_float_and_string_unsupported(args, outcome, names):
    case = example(args, outcome, {"returns": 99})
    bound = ADAPTER.bind(relation(PLUS_ONE), case, case.accepted)
    assert bound.conjuncts == () and names in bound.unsupported


def test_bool_is_not_int():
    """``True`` is never bound as ``1``, nor ``1`` as ``True``, in an input or in the result."""
    flag = relation("(= result (ite p 1 0))", inputs=(("p", "Bool"),), domain=())
    case = example([True], {"returns": 1}, {"returns": 0})
    assert ADAPTER.bind(flag, case, case.accepted).conjuncts == ("(= p true)", "(= result 1)")
    as_int = example([1], {"returns": 1}, {"returns": 0})
    assert "an int" in ADAPTER.bind(flag, as_int, as_int.accepted).unsupported
    as_bool = example([True], {"returns": True}, {"returns": 0})
    assert "a bool" in ADAPTER.bind(flag, as_bool, as_bool.accepted).unsupported
    number = example([True], {"returns": 2}, {"returns": 3})
    assert "a bool" in ADAPTER.bind(relation(PLUS_ONE), number, number.accepted).unsupported
    truth = relation("(= result (> x 0))", result_sort="Bool")
    one = example([3], {"returns": 1}, {"returns": False})
    assert "an int" in ADAPTER.bind(truth, one, one.accepted).unsupported
    assert ADAPTER.bind(truth, one, one.rejected[0]).conjuncts == ("(= x 3)", "(= result false)")


def test_negative_and_zero_divisor_refused():
    """Python's // and % differ from SMT-LIB's div and mod on a negative divisor, and a zero divisor has no agreed
    value: both are outside the fragment, never approximated."""
    for text in ("(= result (div x (- 2)))", "(= result (mod x 0))", "(= result (div x y))"):
        with pytest.raises(smt_ir.OutsideFragment, match="positive whole-number literal"):
            relation(text, inputs=(("x", "Int"), ("y", "Int")))
    held = dataclasses.replace(relation(PLUS_ONE), text="(= result (div x (- 2)))")
    assert "positive whole-number literal" in ADAPTER.supports(held)
    case = example([3], {"returns": 4}, {"returns": 5})
    assert ADAPTER.bind(held, case, case.accepted).conjuncts == ()
    assert ADAPTER.supports(relation("(= result (div x 2))")) == ""


def test_text_outside_the_fragment_or_not_canonical_is_unsupported():
    base = relation(PLUS_ONE)
    for change, names in [({"text": "(=  result (+ x 1))"}, "canonical"), ({"text": "(+ x 1)"}, "Int term"),
                          ({"text": "(forall ((z Int)) (> z 0))"}, "quantifier"),
                          ({"domain": ("(> result 0)",)}, "undeclared symbol 'result'"),
                          ({"definitions": (("helper", "(> x 0)"),)}, "definition 'helper'"),
                          ({"bounds": (("depth", 3),)}, "no bounds"), ({"language": "lean4"}, "not smt.relation"),
                          ({"adapter": "smt.return.int_bool.v0"}, "names the adapter")]:
        assert names in ADAPTER.supports(dataclasses.replace(base, **change))
    named = dataclasses.replace(base, observation=Observation("returns", "shop.f", (("result", "Int"),), "Int"))
    assert "confused with the returned value" in ADAPTER.supports(named)
    wrong = example([3], {"returns": 4}, {"returns": 5}, function="shop.g")
    assert "shop.g" in ADAPTER.bind(base, wrong, wrong.accepted).unsupported


def test_concretise_gives_an_executable_call_or_nothing():
    one = relation("(>= result x)")
    assert ADAPTER.concretise(one, {"x": 3, "result": 3}) == {"args": [3], "kwargs": {}, "outcome": {"returns": 3}}
    assert ADAPTER.concretise(one, {"result": 3}) is None and ADAPTER.concretise(one, {"x": 3}) is None
    assert ADAPTER.concretise(one, {"x": True, "result": 3}) is None
    guarded = relation(PLUS_ONE, raises_when="(< x 0)", error="ValueError", domain=())
    assert ADAPTER.concretise(guarded, {"x": -2, "result": 0})["outcome"] == {"raises": "ValueError"}
    assert ADAPTER.concretise(guarded, {"x": 2, "result": 3})["outcome"] == {"returns": 3}


def test_two_results_query():
    pytest.importorskip("z3")
    loose = ADAPTER.two_results(relation("(>= result x)"))
    assert loose.reading == "witness" and loose.witness["result"] != loose.witness["result__other"]
    assert ADAPTER.two_results(relation(PLUS_ONE)).reading == "no_witness_for_this_encoding"