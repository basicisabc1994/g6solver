# SPDX-License-Identifier: Apache-2.0
"""g6.formal's pure-Python DPLL: right on random formulas, bounded on large ones, and labelled as pure Python."""
from __future__ import annotations

import itertools
import random

import pytest

from g6.formal import FormulaTooLarge, check_propositional, dpll_solve, recheck
from g6.formal.envelope import INPUT_TOO_LARGE, PARSE_ERROR, SOLVER_DECISION
from g6.formal.propositional import MAX_CLAUSES, parse, to_text
from g6.invariants import check_envelope

ATOMS = ["a", "b", "c", "d"]
_INFIX = {"and": "&", "or": "|", "implies": "->", "iff": "<->"}


def _tree(rng: random.Random, depth: int) -> tuple:
    if depth == 0 or rng.random() < 0.2:
        return ("atom", rng.choice(ATOMS)) if rng.random() < 0.9 else ("const", rng.random() < 0.5)
    kind = rng.choice(["not", "and", "or", "implies", "iff"])
    if kind == "not":
        return ("not", _tree(rng, depth - 1))
    return (kind, _tree(rng, depth - 1), _tree(rng, depth - 1))


def _holds(node: tuple, env: dict[str, bool]) -> bool:
    kind = node[0]
    if kind == "atom":
        return env[node[1]]
    if kind == "const":
        return node[1]
    if kind == "not":
        return not _holds(node[1], env)
    a, b = _holds(node[1], env), _holds(node[2], env)
    return {"and": a and b, "or": a or b, "implies": (not a) or b, "iff": a == b}[kind]


def _render(node: tuple) -> str:
    """Infix text written by the test, independent of the module's own printer."""
    kind = node[0]
    if kind == "atom":
        return node[1]
    if kind == "const":
        return "TRUE" if node[1] else "FALSE"
    if kind == "not":
        return f"~({_render(node[1])})"
    return f"({_render(node[1])} {_INFIX[kind]} {_render(node[2])})"


def test_dpll_agrees_with_enumeration_on_random_formulas() -> None:
    """Survival control: models are still found, each one satisfies the formula, and every UNSAT is confirmed by
    enumerating all assignments, so a solver that answered UNSAT (or SAT) to everything would fail."""
    rng = random.Random(17)
    outcomes = {"sat": 0, "unsat": 0}
    for _ in range(600):
        tree = _tree(rng, 5)
        model = dpll_solve(_render(tree), variables=ATOMS)
        some = any(_holds(tree, dict(zip(ATOMS, bits, strict=True)))
                   for bits in itertools.product([False, True], repeat=len(ATOMS)))
        if model is None:
            assert not some, _render(tree)
            outcomes["unsat"] += 1
        else:
            assert set(model) == set(ATOMS) and _holds(tree, model), _render(tree)
            outcomes["sat"] += 1
    assert outcomes["sat"] > 100 and outcomes["unsat"] > 10, outcomes


def test_canonical_text_reads_back_to_the_same_tree() -> None:
    rng = random.Random(3)
    for _ in range(200):
        tree = parse(_render(_tree(rng, 4)))
        assert parse(to_text(tree)) == tree


@pytest.mark.parametrize(("formula", "satisfiable"), [
    ("AND(A, NOT(A))", False),
    ("a implies b and a", True),
    ("IFF(A, B) & A & ~B", False),
    ("(A -> B) & A & ~B", False),
    ("A => B", True),
    ("TRUE", True),
    ("FALSE | false", False),
])
def test_both_syntaxes_are_decided(formula: str, satisfiable: bool) -> None:
    assert (dpll_solve(formula) is not None) is satisfiable


@pytest.mark.parametrize("formula", ["", "A &", "A B", "(A", "A)", "A $ B", "AND(A)", "é"])
def test_a_formula_it_cannot_read_is_a_value_error(formula: str) -> None:
    with pytest.raises(ValueError):
        dpll_solve(formula)


def test_cnf_blow_up_is_refused_before_it_is_built() -> None:
    """Distribution of 20 OR'ed pairs would need 2**20 clauses; the archive had no bound (audit 017 §2)."""
    formula = " | ".join(f"(a{i} & b{i})" for i in range(20))
    with pytest.raises(FormulaTooLarge, match=str(MAX_CLAUSES)):
        dpll_solve(formula)
    result = check_propositional(formula)
    assert result.completion_state == "blocked-escalated" and result.code == INPUT_TOO_LARGE


@pytest.mark.parametrize("formula", ["~" * 5000 + "A", "(" * 3000 + "A" + ")" * 3000])
def test_deep_nesting_is_refused_not_a_crash(formula: str) -> None:
    with pytest.raises(FormulaTooLarge):
        dpll_solve(formula)


def test_an_assertion_cannot_regroup_the_formula() -> None:
    """The archive joined text, AND({assertion}, {formula}), so this assertion turned formula FALSE into a
    satisfiable query. Each input is parsed on its own here."""
    result = check_propositional("FALSE", assertions=["FALSE, TRUE) | OR(TRUE"])
    assert result.satisfiable is None and result.code == PARSE_ERROR
    assert result.completion_state == "blocked-escalated"


def test_check_propositional_is_a_labelled_decision_with_a_recheckable_artifact() -> None:
    result = check_propositional("A & ~B", ["x"], ["B | C"], request_id="q1", run_id="r1")
    assert result.satisfiable is True and result.model == {"A": True, "B": False, "C": True, "x": True}
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == SOLVER_DECISION
    assert check_envelope(result.model_dump()) == [] and (result.request_id, result.run_id) == ("q1", "r1")
    assert result.artifact.statement == "AND(AND(A, NOT(B)), OR(B, C))"
    assert recheck(result.artifact)[0] is True
    unsat = check_propositional("A", assertions=["~A"])
    assert unsat.satisfiable is False and unsat.completion_state == "qualified-draft"
    tampered = unsat.artifact.model_copy(update={"checks": (unsat.artifact.checks[0].model_copy(
        update={"result": "sat"}),)})
    assert recheck(tampered)[0] is False
