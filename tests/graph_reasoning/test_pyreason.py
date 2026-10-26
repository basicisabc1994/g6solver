# SPDX-License-Identifier: Apache-2.0
"""PyReason as g6.graph_reasoning's second engine (owner, 2026-10-20). Without PyReason: which models it evaluates as
G6 does, the text G6 hands it, and an honest refusal when it is asked for and cannot import. With PyReason importable:
agreement, interval for interval, on the known-answer models and on seeded random small models. PyReason 3.7.0 does
not import on this library's Python with its pinned dependencies, so those tests skip and say why."""
from __future__ import annotations

import random
import sys

import pytest

from g6.graph_reasoning import ops, pyreason_engine
from g6.graph_reasoning.envelope import ENGINE_ANSWER, ENGINE_UNAVAILABLE, Refusal
from g6.graph_reasoning.language import parse_model

CHAIN = ("node alice, bob, carol, dave, eve.\nfriend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].\n"
         "popular(dave).\npopular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].\n")
EDGE_RULE = CHAIN + "trusts(x, y) <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].\n"
SCORES = ("review(x1, a) : [0.6, 0.8]. review(x1, b) : [0.8, 1]. review(x2, a) : [0.05, 0.5]. "
          "review(x2, b) : [0.4, 0.6].\nscore(x) : average <- review(x, y) : [0.1, 1].\n"
          "good(x) <- score(x) : [0.5, 1].\n")
FUNCTIONS = "a(k) : [0.5, 0.8]. b(k) : [0.4, 1]. a(m) : [0.2, 0.3]. b(m) : [0.9, 1].\n" + "".join(
    f"{f}(x) : {f} <- a(x) : [0.1, 1], b(x) : [0.1, 1].\n"
    for f in ("average", "average_lower", "maximum", "minimum", "product"))


@pytest.mark.parametrize("text", [CHAIN, EDGE_RULE, SCORES, FUNCTIONS,
                                  "e(a, b). e(b, c). p(c).\np(x) <- e(x, y), p(y).\n",
                                  "e(a, b). f(a, c). p(b). q(c).\nr(x) <- e(x, y), p(y), f(x, z), q(z).\n",
                                  "e(a, b). e(b, a).\nboth(x, y) <- e(x, y), e(y, x).\n",
                                  "e(a, b). p(a).\nr(x, y) <- e(x, y), p(x).\n"])
def test_models_pyreason_evaluates_as_g6_does(text: str) -> None:
    assert pyreason_engine.outside_fragment(parse_model(text)) == []


@pytest.mark.parametrize(("text", "says"), [
    ("popular(p1). friend(x1, p1).\nhub(x) <- friend(x, y), popular(y) at least 1.\n", "threshold"),
    ("popular(m). friend(a, m). owns(m, c). owns(a, c).\n"
     "popular(x) <- popular(y), friend(x, y), owns(y, z), owns(x, z).\n", "cycle"),
    ("e(a, b). e(b, a).\nr(x) <- e(x, y), e(y, x).\n", "cycle"),
    ("e(a, b). f(b, c). e(a, c).\nr(x, y) <- e(x, z), f(z, y).\n", "two head variables"),
    ("e(a, a).\nr(x) <- e(x, x).\n", "one variable twice"),
    ("e(a, a).\nr(x, x) <- e(x, x).\n", "one variable twice"),
    ("q(a, b). q(a, c).\nr(x, y) : [0.6, 0.8] <- q(x, y).\ns(x) : average <- r(x, y) : [0.1, 1].\n", "derives"),
    ("e(a, b). p(b) : [0.5, 1].\ns(x) : average <- p(y) : [0.1, 1], e(x, y).\n", "not in the head"),
    ("e(a, b). f(b, c).\ns(x) : maximum <- f(y, z), e(x, y).\n", "no head variable"),
    ("e(a, b). p(b) : [0.5, 1].\ns(x) : average <- e(x, y), p(y) : [0.1, 1].\n", "another clause uses too"),
    ("p(a).", "no rules"),
])
def test_models_pyreason_evaluates_differently_are_not_compared(text: str, says: str) -> None:
    reasons = pyreason_engine.outside_fragment(parse_model(text))
    assert reasons and says in reasons[0], reasons


def test_g6_hands_pyreason_renamed_text_it_writes_itself() -> None:
    plan = pyreason_engine.translate(parse_model(CHAIN + 'node "Bob Smith".\n'))
    assert plan.nodes == {"Bob Smith": "n0", "alice": "n1", "bob": "n2", "carol": "n3", "dave": "n4", "eve": "n5"}
    assert plan.labels == {"friend": "l0", "popular": "l1"}
    assert plan.rules == ("l1(v0) : [0.7, 1.0] <- l0(v0, v1) : [0.9, 1.0], l1(v1) : [0.7, 1.0]",)
    assert "l0(n1, n2) : [1.0, 1.0]" in plan.facts and "l1(n4) : [1.0, 1.0]" in plan.facts
    assert plan.edges == (("n1", "n2"), ("n2", "n3"), ("n3", "n4")) and plan.functions == ()
    functions = pyreason_engine.translate(parse_model(FUNCTIONS)).functions
    assert functions == ("g6_average", "g6_average_lower", "g6_maximum", "g6_minimum", "g6_product")


def test_without_pyreason_asking_for_it_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyreason", None)
    assert pyreason_engine.unavailable_reason() and not pyreason_engine.available()
    with pytest.raises(Refusal) as caught:
        pyreason_engine.load()
    assert caught.value.code == ENGINE_UNAVAILABLE
    result = ops.reason(CHAIN, engine="pyreason")
    assert result.code == ENGINE_UNAVAILABLE and result.completion_state == "blocked-escalated"
    assert ops.reason(CHAIN).warning_card["code"] == "G6_E_GRAPH_REASONING_ONE_ENGINE"


def test_g6s_own_annotation_functions_for_pyreason_match_the_engines() -> None:
    from fractions import Fraction as F

    from g6.graph_reasoning import engine

    class Bound:
        def __init__(self, lower: float, upper: float) -> None:
            self.lower, self.upper = lower, upper

    bounds = [(F(1, 2), F(4, 5)), (F(2, 5), F(1))]
    plain = [[Bound(float(a), float(b)) for a, b in bounds[:1]], [Bound(float(a), float(b)) for a, b in bounds[1:]]]
    for name in ("average", "average_lower", "maximum", "minimum", "product"):
        expected = engine.annotate(name, bounds)
        got = getattr(pyreason_engine, f"g6_{name}")(plain, None)
        assert got == pytest.approx((float(expected[0]), float(expected[1]))), name


WHY = pyreason_engine.unavailable_reason()
needs_pyreason = pytest.mark.skipif(bool(WHY), reason=f"PyReason does not run here: {WHY}")


@needs_pyreason
@pytest.mark.parametrize("text", [CHAIN, EDGE_RULE, SCORES, FUNCTIONS])
def test_pyreason_agrees_on_the_known_answer_models(text: str) -> None:
    result = ops.reason(text, engine="pyreason", time_limit=60)
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.warning_card["code"] == ENGINE_ANSWER and result.evidence[0]["agreement"]["agreed"]


def _random_model(seed: int) -> str:
    rnd = random.Random(seed)
    nodes = [f"n{i}" for i in range(rnd.randint(2, 6))]
    grid = [0.25, 0.5, 0.75, 1]
    lines = [f"node {', '.join(nodes)}."]
    for s in nodes:
        for t in nodes:
            if s != t and rnd.random() < 0.4:
                lines.append(f"e({s}, {t}) : [{rnd.choice(grid)}, 1].")
    for n in nodes:
        if rnd.random() < 0.4:
            lines.append(f"p({n}) : [{rnd.choice(grid)}, 1].")
    if not any(line.startswith("e(") for line in lines):
        lines.append(f"e({nodes[0]}, {nodes[1]}).")
    lines += [f"p(x) : [{rnd.choice(grid[:2])}, 1] <- e(x, y) : [{rnd.choice(grid)}, 1], p(y) : [0.5, 1].",
              "q(x) <- p(x) : [0.75, 1].",
              f"t(x, y) <- e(x, y) : [{rnd.choice(grid)}, 1], q(y).",
              "s(x) : average <- e(x, y) : [0.25, 1]."]
    return "\n".join(lines) + "\n"


@needs_pyreason
@pytest.mark.parametrize("seed", range(20))
def test_pyreason_agrees_on_seeded_random_small_models(seed: int) -> None:
    text = _random_model(seed)
    assert pyreason_engine.outside_fragment(parse_model(text)) == []
    result = ops.reason(text, engine="pyreason", time_limit=60)
    assert result.completion_state == "qualified-draft", (seed, result.code, result.reason)
    assert result.evidence[0]["agreement"]["agreed"], result.evidence[0]["agreement"]
