# SPDX-License-Identifier: Apache-2.0
"""The pure-Python engine of g6.graph_reasoning (owner, 2026-10-20): one static fixpoint that tightens intervals
monotonically, recording for each atom every step that tightened it. Known answers, each against an oracle written
here: popularity spreading over hops, a threshold, a percentage, the annotation functions, an edge rule, a conflict
that must be reported, and an unknown that stays [0, 1]."""
from __future__ import annotations

import random
import time
from fractions import Fraction as F

import pytest

from g6.graph_reasoning import engine
from g6.graph_reasoning.envelope import TIME_LIMIT, TOO_MANY_GROUNDINGS, Refusal
from g6.graph_reasoning.language import UNKNOWN, parse_model

CHAIN = """
node alice, bob, carol, dave, eve.
friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].
popular(dave).
popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].
"""


def _bound(solution: engine.Solution, label: str, *component: str) -> tuple:
    return solution.bounds.get((label, component), UNKNOWN)


def _oracle_spread(edges: dict[tuple[str, str], tuple], popular: set[str], low: F) -> set[str]:
    """Who becomes popular: walk the friend edges backwards from the popular nodes, through edges at least ``low``."""
    reached, frontier = set(popular), list(popular)
    while frontier:
        target = frontier.pop()
        for (source, sink), bound in edges.items():
            if sink == target and bound[0] >= low and source not in reached:
                reached.add(source)
                frontier.append(source)
    return reached


def test_popularity_spreads_over_hops_one_round_per_hop() -> None:
    solution = engine.solve(parse_model(CHAIN))
    edges = {("alice", "bob"): (1, 1), ("bob", "carol"): (1, 1), ("carol", "dave"): (F(9, 10), 1)}
    expected = _oracle_spread(edges, {"dave"}, F(9, 10)) - {"dave"}
    assert expected == {"alice", "bob", "carol"}
    for name in expected:
        assert _bound(solution, "popular", name) == (F(7, 10), 1), name
    assert _bound(solution, "popular", "dave") == (1, 1)
    assert [solution.history[("popular", (n,))][-1].rank for n in ("carol", "bob", "alice")] == [1, 2, 3]
    step = solution.history[("popular", ("bob",))][-1]
    assert step.rule == 0 and dict(step.head) == {"x": "bob"}
    assert dict(step.witnesses[0]) == {"x": "bob", "y": "carol"}
    assert dict(step.sources) == {("friend", ("bob", "carol")): (1, 1), ("popular", ("carol",)): (F(7, 10), 1)}
    assert solution.conflict is None


def test_an_unknown_stays_zero_to_one() -> None:
    solution = engine.solve(parse_model(CHAIN))
    assert _bound(solution, "popular", "eve") == UNKNOWN and ("popular", ("eve",)) not in solution.history


def test_a_weak_edge_stops_the_spread() -> None:
    solution = engine.solve(parse_model(CHAIN.replace("friend(carol, dave) : [0.9, 1]", "friend(carol, dave) : "
                                                                                        "[0.5, 1]")))
    assert all(_bound(solution, "popular", n) == UNKNOWN for n in ("alice", "bob", "carol"))


THRESHOLD = """
popular(p1). popular(p2). popular(n1) : [0, 0.2].
friend(x1, p1). friend(x1, p2). friend(x2, p1). friend(x2, n1). friend(x3, p1). friend(x3, n1). friend(x3, n2).
node n2.
hub(x) : [0.6, 1] <- friend(x, y), popular(y) at least 2.
wide(x) <- friend(x, y), popular(y) at least 50%.
"""


def test_a_count_threshold_and_a_percentage() -> None:
    model = parse_model(THRESHOLD)
    solution = engine.solve(model)
    popular = {n for (label, (n, *_)), b in model.facts.items() if label == "popular" and b == (1, 1)}
    friends: dict[str, list[str]] = {}
    for (label, comp), _ in model.facts.items():
        if label == "friend":
            friends.setdefault(comp[0], []).append(comp[1])
    for x, ys in friends.items():
        hits = sum(1 for y in ys if y in popular)
        assert (_bound(solution, "hub", x) == (F(3, 5), 1)) == (hits >= 2), x
        assert (_bound(solution, "wide", x) == (1, 1)) == (2 * hits >= len(ys)), x
    assert _bound(solution, "hub", "x1") == (F(3, 5), 1) and _bound(solution, "wide", "x3") == UNKNOWN


@pytest.mark.parametrize(("function", "expected"), [
    ("average", (F(9, 20), F(9, 10))),
    ("average_lower", (F(9, 20), F(1))),
    ("minimum", (F(2, 5), F(4, 5))),
    ("maximum", (F(1, 2), F(1))),
    ("product", (F(1, 5), F(4, 5))),
])
def test_each_annotation_function_over_the_body_bounds(function: str, expected: tuple) -> None:
    text = f"a(k) : [0.5, 0.8]. b(k) : [0.4, 1].\nc(x) : {function} <- a(x) : [0.1, 1], b(x) : [0.1, 1]."
    lowers, uppers = [F(1, 2), F(2, 5)], [F(4, 5), F(1)]
    oracle = {"average": (sum(lowers) / 2, sum(uppers) / 2), "average_lower": (sum(lowers) / 2, max(uppers)),
              "minimum": (min(lowers), min(uppers)), "maximum": (max(lowers), max(uppers)),
              "product": (lowers[0] * lowers[1], uppers[0] * uppers[1])}[function]
    assert oracle == expected
    assert _bound(engine.solve(parse_model(text)), "c", "k") == expected


def test_average_reads_every_qualified_edge_of_the_head() -> None:
    text = ("review(x1, a) : [0.6, 0.8]. review(x1, b) : [0.8, 1]. review(x2, a) : [0.05, 0.5]. "
            "review(x2, b) : [0.4, 0.6].\nscore(x) : average <- review(x, y) : [0.1, 1].")
    solution = engine.solve(parse_model(text))
    assert _bound(solution, "score", "x1") == (F(7, 10), F(9, 10))
    assert _bound(solution, "score", "x2") == (F(2, 5), F(3, 5))      # the review below 0.1 does not qualify
    step = solution.history[("score", ("x1",))][-1]
    assert len(step.witnesses) == 2 and len(step.sources) == 2           # every qualified atom is a source


def test_an_edge_rule_labels_edges_that_exist_and_adds_none() -> None:
    text = CHAIN + "trusts(x, y) <- friend(x, y), popular(y) : [0.7, 1].\n"
    solution = engine.solve(parse_model(text))
    # friend(x, y) with no interval asks for [1, 1], which friend(carol, dave) : [0.9, 1] is not within
    assert {comp for (label, comp) in solution.bounds if label == "trusts"} == {("alice", "bob"), ("bob", "carol")}
    assert all(comp in set(parse_model(text).edges) for (label, comp) in solution.bounds if label == "trusts")


def test_a_conflict_is_reported_with_both_sides_and_never_resolved() -> None:
    text = "trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x)."
    solution = engine.solve(parse_model(text))
    conflict = solution.conflict
    assert conflict is not None and conflict.atom == ("trusted", ("a",))
    assert conflict.before == (F(4, 5), 1) and conflict.step.value == (0, F(3, 10)) and conflict.step.rule == 0
    assert dict(conflict.step.sources) == {("flagged", ("a",)): (1, 1)}
    assert solution.bounds[("trusted", ("a",))] == (F(4, 5), 1)            # left as it was, not reset to [0, 1]


def test_tightening_is_an_intersection_and_lower_and_upper_can_come_from_different_steps() -> None:
    text = "s(a) : [0.2, 0.9]. g(a).\ns(x) : [0.5, 1] <- g(x).\ns(x) : [0, 0.7] <- g(x)."
    solution = engine.solve(parse_model(text))
    assert _bound(solution, "s", "a") == (F(1, 2), F(7, 10))
    assert [s.rule for s in solution.history[("s", ("a",))]] == [None, 0, 1]


def test_strata_run_an_annotation_function_only_on_final_inputs() -> None:
    # r is derived at round 1 from q; the average over r must wait for r to be complete.
    text = ("q(a, b). q(a, c). r(a, b) : [0.2, 0.4].\nr(x, y) : [0.6, 1] <- q(x, y).\n"
            "s(x) : average <- r(x, y) : [0.1, 1].")
    solution = engine.solve(parse_model(text))
    # r(a, b): [0.2, 0.4] meets [0.6, 1]: that is a conflict, so use a model without it
    assert solution.conflict is not None
    text = "q(a, b). q(a, c).\nr(x, y) : [0.6, 0.8] <- q(x, y).\ns(x) : average <- r(x, y) : [0.1, 1]."
    solution = engine.solve(parse_model(text))
    assert _bound(solution, "s", "a") == (F(3, 5), F(4, 5))
    assert solution.history[("s", ("a",))][-1].rank > solution.history[("r", ("a", "c"))][-1].rank


def _random_model(seed: int) -> str:
    rnd = random.Random(seed)
    nodes = [f"n{i}" for i in range(rnd.randint(2, 7))]
    lines = [f"node {', '.join(nodes)}."]
    grid = [F(k, 4) for k in range(5)]
    for s in nodes:
        for t in nodes:
            if s != t and rnd.random() < 0.35:
                low = rnd.choice(grid[2:])
                lines.append(f"e({s}, {t}) : [{float(low)}, 1].")
    for n in nodes:
        if rnd.random() < 0.4:
            lines.append(f"p({n}) : [{float(rnd.choice(grid[2:]))}, 1].")
    lines.append("p(x) : [0.5, 1] <- e(x, y) : [0.5, 1], p(y) : [0.5, 1].")
    lines.append("q(x) <- p(x) : [0.75, 1].")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("seed", range(12))
def test_the_answer_does_not_depend_on_the_order_rules_and_facts_are_written(seed: int) -> None:
    text = _random_model(seed)
    lines = text.splitlines()
    shuffled = [lines[0], *random.Random(seed).sample(lines[1:], len(lines) - 1)]
    a, b = engine.solve(parse_model(text)), engine.solve(parse_model("\n".join(shuffled)))
    assert a.bounds == b.bounds and (a.conflict is None) == (b.conflict is None)


def test_too_many_groundings_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "MAX_GROUNDINGS", 3)
    with pytest.raises(Refusal) as caught:
        engine.solve(parse_model("node a, b, c. p(a). q(x) <- p(x), p(y)."))
    assert caught.value.code == TOO_MANY_GROUNDINGS


def test_the_engine_stops_at_its_deadline() -> None:
    with pytest.raises(Refusal) as caught:
        engine.solve(parse_model(CHAIN), deadline=time.monotonic() - 1)
    assert caught.value.code == TIME_LIMIT
