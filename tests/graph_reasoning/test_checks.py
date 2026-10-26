# SPDX-License-Identifier: Apache-2.0
"""G6's own check of a graph-reasoning interpretation, in pure Python (owner, 2026-10-20): closure, support (with its
rank check) and soundness. Each check is fed the engine's answer, which must pass, and planted wrong interpretations it
must catch: a missing tightening fails closure, an unsupported bound fails support, circular support fails the rank
check, and an interval outside [0, 1] fails soundness."""
from __future__ import annotations

import time
from fractions import Fraction as F

import pytest

from g6.graph_reasoning import checks, engine
from g6.graph_reasoning.engine import Step
from g6.graph_reasoning.envelope import TIME_LIMIT, Refusal
from g6.graph_reasoning.language import parse_model

CHAIN = ("node alice, bob, carol, dave, eve.\nfriend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].\n"
         "popular(dave).\npopular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].\n")
SCORES = ("review(x1, a) : [0.6, 0.8]. review(x1, b) : [0.8, 1]. review(x2, a) : [0.4, 0.6].\n"
          "score(x) : average <- review(x, y) : [0.1, 1].\ngood(x) <- score(x) : [0.5, 1].\n")
COUNTS = ("popular(p1). popular(p2). popular(n1) : [0, 0.2].\nfriend(x1, p1). friend(x1, p2). friend(x2, p1). "
          "friend(x2, n1).\nhub(x) : [0.6, 1] <- friend(x, y), popular(y) at least 2.\n"
          "wide(x) <- friend(x, y), popular(y) at least 50%.\ntrusts(x, y) <- friend(x, y), popular(y).\n")
MODELS = [CHAIN, SCORES, COUNTS, "s(a) : [0.2, 0.9]. g(a).\ns(x) : [0.5, 1] <- g(x).\ns(x) : [0, 0.7] <- g(x).\n",
          "e(a, b). e(b, a). e(b, c). p(c).\np(x) <- e(x, y), p(y).\nloop(x, x) <- e(x, y).\n"]


def _solve(text: str) -> tuple:
    model = parse_model(text)
    solution = engine.solve(model)
    return model, dict(solution.bounds), {a: list(s) for a, s in solution.history.items()}


def _failures(check: checks.Check, kind: str) -> list[str]:
    return [f for f in check.failures if f.startswith(kind + ":")]


@pytest.mark.parametrize("text", MODELS)
def test_the_engines_interpretation_passes_every_check(text: str) -> None:
    check = checks.check_solution(*_solve(text))
    assert check.passed and check.failures == (), check.failures
    assert check.checked == ("closure", "support", "soundness")


def test_a_missing_tightening_fails_closure() -> None:
    model, bounds, history = _solve(CHAIN)
    del bounds[("popular", ("alice",))]
    del history[("popular", ("alice",))]
    check = checks.check_solution(model, bounds, history)
    assert any("popular(alice)" in f for f in _failures(check, "closure")), check.failures
    assert _failures(check, "support") == []          # nothing wrong is claimed, something true is missing


def test_a_bound_looser_than_the_rules_give_fails_closure() -> None:
    model, bounds, history = _solve(SCORES)
    bounds[("score", ("x1",))] = (F(1, 2), 1)
    history[("score", ("x1",))] = [Step(5, (F(1, 2), 1), 0, (("x", "x1"),), (), ())]
    check = checks.check_solution(model, bounds, history)
    assert any("score(x1)" in f for f in _failures(check, "closure")), check.failures


def test_an_unsupported_bound_fails_support() -> None:
    model, bounds, history = _solve(CHAIN)
    bounds[("popular", ("eve",))] = (F(7, 10), 1)       # eve has no friends: nothing derives this
    check = checks.check_solution(model, bounds, history)
    assert any("popular(eve)" in f and "no step" in f for f in _failures(check, "support")), check.failures
    assert _failures(check, "closure") == []            # an extra bound breaks no rule: only support catches it


def test_a_step_whose_body_did_not_hold_fails_support() -> None:
    model, bounds, history = _solve(CHAIN)
    bounds[("popular", ("eve",))] = (F(7, 10), 1)
    history[("popular", ("eve",))] = [Step(1, (F(7, 10), 1), 0, (("x", "eve"),), ((("x", "eve"), ("y", "dave")),),
                                           ((("friend", ("eve", "dave")), (1, 1)), (("popular", ("dave",)), (1, 1))))]
    check = checks.check_solution(model, bounds, history)
    assert any("eve" in f and "not an edge" in f for f in _failures(check, "support")), check.failures


def test_a_source_bound_outside_its_clause_fails_support() -> None:
    model, bounds, history = _solve(CHAIN)
    step = history[("popular", ("carol",))][-1]
    sources = tuple((a, (F(1, 2), 1) if a[0] == "friend" else b) for a, b in step.sources)
    history[("popular", ("carol",))][-1] = Step(step.rank, step.value, step.rule, step.head, step.witnesses, sources)
    check = checks.check_solution(model, bounds, history)
    assert any("did not hold" in f for f in _failures(check, "support")), check.failures


def test_circular_support_fails_the_rank_check() -> None:
    model, bounds, history = _solve("e(a, b). e(b, a). p(c).\np(x) <- e(x, y), p(y).\n")
    for x, y in (("a", "b"), ("b", "a")):    # each derived only from the other: closed, and supported in a circle
        bounds[("p", (x,))] = (1, 1)
        history[("p", (x,))] = [Step(1, (1, 1), 0, (("x", x),), ((("x", x), ("y", y)),),
                                     ((("e", (x, y)), (1, 1)), (("p", (y,)), (1, 1))))]
    check = checks.check_solution(model, bounds, history)
    assert _failures(check, "closure") == []
    assert any("before rank 1" in f for f in _failures(check, "support")), check.failures


def test_a_history_that_does_not_give_the_bound_fails_support() -> None:
    model, bounds, history = _solve(CHAIN)
    bounds[("popular", ("bob",))] = (F(4, 5), 1)
    check = checks.check_solution(model, bounds, history)
    assert any("popular(bob)" in f and "history" in f for f in _failures(check, "support")), check.failures


def test_a_fact_that_was_never_written_fails_support() -> None:
    model, bounds, history = _solve(CHAIN)
    history[("popular", ("bob",))].insert(0, Step(0, (F(7, 10), 1), where=("line 9",)))
    check = checks.check_solution(model, bounds, history)
    assert any("popular(bob)" in f and "no such fact" in f for f in _failures(check, "support")), check.failures


def test_an_average_over_an_incomplete_set_fails_support() -> None:
    model, bounds, history = _solve(SCORES)
    full = history[("score", ("x1",))][-1]
    half = Step(full.rank, (F(3, 5), F(4, 5)), 0, full.head, full.witnesses[:1], full.sources[:1])
    bounds[("score", ("x1",))] = (F(3, 5), F(4, 5))
    history[("score", ("x1",))] = [half]
    check = checks.check_solution(model, bounds, history)
    assert any("score(x1)" in f and "qualif" in f for f in _failures(check, "support")), check.failures


def test_a_threshold_the_witnesses_do_not_meet_fails_support() -> None:
    model, bounds, history = _solve(COUNTS)
    bounds[("hub", ("x2",))] = (F(3, 5), 1)
    history[("hub", ("x2",))] = [Step(1, (F(3, 5), 1), 0, (("x", "x2"),), ((("x", "x2"), ("y", "p1")),),
                                      ((("friend", ("x2", "p1")), (1, 1)), (("popular", ("p1",)), (1, 1))))]
    check = checks.check_solution(model, bounds, history)
    assert any("at least 2" in f for f in _failures(check, "support")), check.failures


@pytest.mark.parametrize(("atom", "bound", "says"), [
    (("popular", ("alice",)), (F(9, 10), F(1, 2)), "empty"),
    (("popular", ("alice",)), (F(1, 2), F(3, 2)), "outside [0, 1]"),
    (("popular", ("zed",)), (F(1), F(1)), "not a node"),
    (("friend", ("alice", "dave")), (F(1), F(1)), "not an edge"),
    (("famous", ("alice",)), (F(1), F(1)), "not a label"),
])
def test_unsound_intervals_and_atoms_fail_soundness(atom: tuple, bound: tuple, says: str) -> None:
    model, bounds, history = _solve(CHAIN)
    bounds[atom] = bound
    check = checks.check_solution(model, bounds, history)
    assert any(says in f for f in _failures(check, "soundness")), check.failures


def test_a_reported_conflict_is_checked_and_a_planted_one_is_refuted() -> None:
    model = parse_model("trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x).")
    solution = engine.solve(model)
    good = checks.check_conflict(model, solution.bounds, solution.history, solution.conflict)
    assert good.passed, good.failures
    fake = engine.Conflict(solution.conflict.atom, (F(1, 5), 1), solution.conflict.step)    # overlaps [0, 0.3]
    bad = checks.check_conflict(model, solution.bounds, solution.history, fake)
    assert not bad.passed and any("not empty" in f or "history" in f for f in bad.failures), bad.failures


def test_the_check_stops_at_its_deadline() -> None:
    with pytest.raises(Refusal) as caught:
        checks.check_solution(*_solve(CHAIN), deadline=time.monotonic() - 1)
    assert caught.value.code == TIME_LIMIT


def test_the_check_shares_no_code_with_the_engine() -> None:
    import ast
    from pathlib import Path
    tree = ast.parse(Path(checks.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "g6.graph_reasoning.engine" not in imported and not any("engine" in (m or "") for m in imported)
