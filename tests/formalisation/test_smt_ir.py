# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The restricted SMT grammar and its reader: nothing is read that Z3 would read differently."""
from __future__ import annotations

import inspect
import re

import pytest

from g6.harness.formalisation import smt_ir
from g6.harness.formalisation.smt_ir import OutsideFragment, read, write

SORTS = {"x": "Int", "y": "Int", "p": "Bool", "result": "Int"}
#: One text per constructor, each using it; the inventory test below fails when a constructor has none.
SAMPLES = {
    "IntLit": "(= result 7)", "BoolLit": "(= p true)", "Var": "(= result x)", "Neg": "(= result (- x))",
    "Arith": "(= result (+ x (* 2 y) (- y 1)))", "DivMod": "(= result (+ (div x 3) (mod y 2)))",
    "Compare": "(and (< x y) (distinct result x))", "Not": "(not (>= result x))", "And": "(and p (> x 0))",
    "Or": "(or p (<= x 0))", "Implies": "(=> (> x 5) (= result 0))", "Ite": "(= result (ite p x (- y)))",
}


def test_every_constructor_has_a_sample():
    assert set(SAMPLES) == {kind.__name__ for kind in smt_ir.CONSTRUCTORS}
    for name, text in SAMPLES.items():
        seen, todo = set(), [read(text, SORTS)]
        while todo:
            term = todo.pop()
            seen.add(type(term).__name__)
            todo.extend(smt_ir.children(term))
        assert name in seen


def test_constructors_lists_every_term_class():
    classes = {name for name, kind in inspect.getmembers(smt_ir, inspect.isclass)
               if kind.__module__ == smt_ir.__name__ and name != "OutsideFragment"}
    assert classes == {kind.__name__ for kind in smt_ir.CONSTRUCTORS}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_round_trip_equivalent(name):
    pytest.importorskip("z3")
    from g6.harness.formalisation.query import differ

    text = SAMPLES[name]
    again = write(read(text, SORTS))
    assert read(again, SORTS) == read(text, SORTS) and write(read(again, SORTS)) == again
    assert differ(SORTS, [], text, again, statement_sha256="0" * 64).reading == "equivalent_under_assumptions"


@pytest.mark.parametrize("text, names", [
    ("(forall ((z Int)) (> z 0))", "quantifier"), ("(exists ((z Int)) (> z 0))", "quantifier"),
    ('(= result "abc")', "string literal"), ("(= result (** x 2))", "'**'"),
    ("(= result (f x))", "uninterpreted function or unsupported operator 'f'"), ("(= result z)", "undeclared symbol 'z'"),
    ("(let ((a 1)) (= result a))", "let binding"), ("(= result 1.5)", "the character '.'"),
    ("(= result (div x y))", "positive whole-number literal"), ("(= result (div x 0))", "positive whole-number"),
    ("(= result (mod x (- 2)))", "positive whole-number"), ("(= result 007)", "the token '007'"),
    ("(= result x) (= result y)", "top-level items"), ("(= result x", "unclosed"), ("", "empty statement"),
    ("(set-option :a b)", "the character ':'"), ("(= result |x|)", "the character '|'"),
    ("(xor p p)", "'xor'"), ("(= result x y)", "3 argument"), ("(< x y result)", "3 argument"),
])
def test_outside_fragment_named(text, names):
    with pytest.raises(OutsideFragment, match=re.escape(names)):
        read(text, SORTS)


@pytest.mark.parametrize("text", ["(+ x p)", "(= x p)", "(< p x)", "(and x p)", "(not x)", "(ite x 1 2)",
                                  "(ite p x p)", "(=> p x)", "(- p)", "(* p 2)", "(distinct p 1)"])
def test_bool_is_never_coerced_to_int(text):
    """Z3 reads a Bool used as an Int by coercing it; the reader refuses it, so text it takes means the same to Z3."""
    with pytest.raises(OutsideFragment):
        read(text, SORTS)


@pytest.mark.parametrize("sorts", [{"x": "Real"}, {"x": "int"}, {"and": "Bool"}, {"true": "Bool"}, {"1x": "Int"},
                                   {"x": "(Array Int Int)"}, {"result__other": "Int"}])
def test_only_int_and_bool_symbols_with_plain_names(sorts):
    with pytest.raises(OutsideFragment):
        read("true", sorts)


def test_guards_are_every_antecedent_and_condition():
    term = read("(and (=> (> x 5) (= result 0)) (= result (ite p x y)) (=> p (=> (< y 0) (= result 1))))", SORTS)
    assert [write(g) for g in smt_ir.guards(term)] == ["(> x 5)", "p", "p", "(< y 0)"]


def test_evaluate_agrees_with_python_on_the_fragment():
    term = read("(= result (+ (div x 3) (mod y 2) (ite p 1 (- 1))))", SORTS)
    for x in range(-7, 8):
        for y in range(-3, 4):
            for p in (True, False):
                want = x // 3 + y % 2 + (1 if p else -1)
                assert smt_ir.evaluate(term, {"x": x, "y": y, "p": p, "result": want}) is True
                assert smt_ir.evaluate(term, {"x": x, "y": y, "p": p, "result": want + 1}) is False
    with pytest.raises(OutsideFragment):
        smt_ir.evaluate(term, {"x": True, "y": 0, "p": True, "result": 0})


def test_evaluate_matches_z3_on_div_and_mod_of_negatives():
    pytest.importorskip("z3")
    from g6.harness.formalisation.query import witness

    for x in (-7, -1, 0, 5):
        term = read("(= result (+ (* 10 (div x 3)) (mod x 3)))", SORTS)
        value = 10 * (x // 3) + x % 3
        assert smt_ir.evaluate(term, {"x": x, "result": value})
        found = witness(SORTS, [write(term), f"(= x {write(smt_ir.IntLit(x))})"], statement_sha256="0" * 64)
        assert found.witness["result"] == value


def test_substitute_keeps_the_sort():
    term = read("(> result x)", SORTS)
    assert write(smt_ir.substitute(term, "result", read("(+ x 1)", SORTS))) == "(> (+ x 1) x)"
    with pytest.raises(OutsideFragment):
        smt_ir.substitute(term, "result", read("p", SORTS))