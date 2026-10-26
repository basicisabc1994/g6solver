# SPDX-License-Identifier: Apache-2.0
"""The language g6.graph_reasoning reads (owner, 2026-10-20): a graph of nodes and directed edges, facts that give a
label on a node or an edge a truth interval, and rules in the style of PyReason, read by G6's own parser. Every refusal
has its own stable code, and a parse error says where."""
from __future__ import annotations

from fractions import Fraction

import pytest

from g6.graph_reasoning.envelope import (
    ARITY_CLASH,
    BAD_INTERVAL,
    CONFLICT,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_STRATIFIABLE,
    PARSE_ERROR,
    UNKNOWN_FUNCTION,
    UNKNOWN_LABEL,
    UNSAFE_RULE,
    VACUOUS_CLAUSE,
    Refusal,
)
from g6.graph_reasoning.language import parse_atom, parse_model, render_atom

SOCIAL = """
# a small social graph
node alice, bob, carol, dave, eve.
edge alice -> bob, bob -> carol.
friend(alice, bob). friend(bob, carol).
friend(carol, dave) : [0.9, 1].        // an edge fact declares its edge
popular(dave).
popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].
"""


def _refused(text: str, **kw) -> Refusal:
    with pytest.raises(Refusal) as caught:
        parse_model(text, **kw)
    return caught.value


def test_a_program_reads_into_a_graph_facts_and_rules() -> None:
    model = parse_model(SOCIAL)
    assert model.nodes == ("alice", "bob", "carol", "dave", "eve")
    assert model.edges == (("alice", "bob"), ("bob", "carol"), ("carol", "dave"))
    assert model.arities == {"friend": 2, "popular": 1}
    assert model.facts[("friend", ("carol", "dave"))] == (Fraction(9, 10), Fraction(1))
    assert model.facts[("popular", ("dave",))] == (Fraction(1), Fraction(1))      # no bound means true, [1, 1]
    rule = model.rules[0]
    assert (rule.head, rule.variables, rule.bound, rule.function) == ("popular", ("x",), (Fraction(7, 10), 1), None)
    assert [(c.label, c.variables, c.bound) for c in rule.clauses] == [
        ("friend", ("x", "y"), (Fraction(9, 10), 1)), ("popular", ("y",), (Fraction(7, 10), 1))]
    assert rule.line == 8 and rule.kind == "node"          # SOCIAL starts with a blank line


def test_numbers_are_exact_decimals() -> None:
    model = parse_model("p(a) : [0.1, 0.30000000000000004].")
    assert model.facts[("p", ("a",))] == (Fraction(1, 10), Fraction("0.30000000000000004"))


def test_edge_rules_functions_thresholds_and_quoted_names() -> None:
    model = parse_model('edge "Bob Smith" -> carol.\nreview("Bob Smith", carol) : [0.6, 0.8].\n'
                        "score(x) : average <- review(x, y) : [0.1, 1].\n"
                        "trusts(x, y) <- review(x, y), score(x) : [0.5, 1].\n"
                        "hub(x) <- review(x, y) at least 2.\nwide(x) <- review(x, y) at least 50%.\n")
    assert model.nodes == ("Bob Smith", "carol")
    score, trusts, hub, wide = model.rules
    assert score.function == "average" and score.bound is None
    assert trusts.kind == "edge" and trusts.variables == ("x", "y") and trusts.bound == (1, 1)
    assert (hub.clauses[0].at_least, hub.clauses[0].percent) == (2, False)
    assert (wide.clauses[0].at_least, wide.clauses[0].percent) == (50, True)
    assert render_atom(("review", ("Bob Smith", "carol"))) == 'review("Bob Smith", carol)'


def test_facts_on_one_atom_are_intersected_and_an_empty_intersection_is_a_conflict() -> None:
    model = parse_model("p(a) : [0.2, 0.9]. p(a) : [0.5, 1].")
    assert model.facts[("p", ("a",))] == (Fraction(1, 2), Fraction(9, 10))
    refusal = _refused("p(a) : [0.2, 0.4].\np(a) : [0.5, 1].")
    assert refusal.code == CONFLICT and "p(a)" in refusal.reason and "line 1" in refusal.reason
    assert refusal.keep["conflict"]["atom"] == "p(a)"


def test_the_json_form_gives_the_same_model() -> None:
    graph = {"nodes": ["alice", "bob", "carol", "dave", "eve"], "edges": [["alice", "bob"], ["bob", "carol"]]}
    facts = [{"label": "friend", "edge": ["alice", "bob"]}, {"label": "friend", "edge": ["bob", "carol"]},
             {"label": "friend", "edge": ["carol", "dave"], "bound": [0.9, 1]}, {"label": "popular", "node": "dave"}]
    rules = "popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1]."
    model = parse_model(rules, graph=graph, facts=facts)
    text = parse_model(SOCIAL)
    assert (model.nodes, model.edges, model.facts, model.arities) == (text.nodes, text.edges, text.facts, text.arities)
    assert model.facts[("friend", ("carol", "dave"))] == (Fraction(9, 10), 1)       # the float 0.9 read as 9/10


@pytest.mark.parametrize(("text", "where"), [
    ("p(a", "line 1, column 4"),
    ("p(a) : [0.5 1].", "line 1, column 13"),
    ("node alice\nedge alice -> bob.", "line 2, column 1"),
    ("p(x) <-1 q(x).", "timestep"),
    ("q(a).\np(x) <- q(x) q(x).", "line 2, column 14"),
    ('p(x) <- q("x").', "variables"),
    ("p(a, b, c).", "one or two"),
    ("p().", "one or two"),
    ("p(a) : average.", "fact"),
    ("p(x) <- .", "line 1, column 9"),
    ("p(x) <- q(x) at most 2.", "at least"),
    ("p(x) <- q(x) at least 0.", "at least"),
    ("p(x) <- q(x) at least 1.5.", "whole number"),
    ("p(x) <- q(x) at least 150%.", "100"),
    ("p(a) ; q(b).", "unexpected character ';'"),
    ('p("unterminated).', "unterminated"),
    ("node edge.", "keyword"),
])
def test_parse_errors_say_where(text: str, where: str) -> None:
    refusal = _refused(text)
    assert refusal.code == PARSE_ERROR, (refusal.code, refusal.reason)
    assert where in refusal.reason, refusal.reason
    assert refusal.keep["problems"][0]["line"] is not None


@pytest.mark.parametrize(("text", "code", "says"), [
    ("p(a). p(a, b).", ARITY_CLASH, "p is used with 2 arguments"),
    ("e(a, b). p(x) <- e(x).", ARITY_CLASH, "line 1, column 18"),
    ("p(a). q(x) <- p(x), missing(x).", UNKNOWN_LABEL, "missing"),
    ("p(a) : [0.8, 0.2].", BAD_INTERVAL, "lower bound 0.8 is above the upper bound 0.2"),
    ("p(a) : [0, 1.5].", BAD_INTERVAL, "outside [0, 1]"),
    ("p(a) : [-0.1, 1].", BAD_INTERVAL, "outside [0, 1]"),
    ("p(a). q(x) <- p(x) : [0, 1].", VACUOUS_CLAUSE, "every atom"),
    ("p(a). q(x) : median <- p(x).", UNKNOWN_FUNCTION, "average, average_lower, maximum, minimum, product"),
    ("p(a). q(x, y) <- p(x).", UNSAFE_RULE, "y"),
    ("p(a). q(x) : average <- p(x), q(x).", NOT_STRATIFIABLE, "q"),
    ("e(a, b). p(a). p(x) <- p(x), e(x, y) at least 50%.", NOT_STRATIFIABLE, "percent"),
])
def test_refusals_have_their_own_codes(text: str, code: str, says: str) -> None:
    refusal = _refused(text)
    assert refusal.code == code, (refusal.code, refusal.reason)
    assert says in refusal.reason, refusal.reason


def test_a_percent_threshold_may_recurse_through_its_own_clause() -> None:
    model = parse_model("e(a, b). p(b). p(x) <- e(x, y), p(y) at least 50%.")    # only e must be final first
    assert model.strata["e"] < model.strata["p"]


def test_strata_put_an_annotation_function_after_what_it_reads() -> None:
    model = parse_model("r(a, b) : [0.5, 1]. s(x) : average <- r(x, y) : [0.1, 1]. t(x) <- s(x) : [0.4, 1].")
    assert model.strata["r"] < model.strata["s"] <= model.strata["t"]


@pytest.mark.parametrize(("kw", "code"), [
    ({"graph": {"nodes": ["a"], "edges": [["a"]]}}, INPUT_INVALID),
    ({"graph": {"nodes": [""]}}, INPUT_INVALID),
    ({"graph": {"vertices": ["a"]}}, INPUT_INVALID),
    ({"facts": [{"label": "p", "node": "a", "edge": ["a", "b"]}]}, INPUT_INVALID),
    ({"facts": [{"label": "p", "node": "a", "bound": [0.9, 0.1]}]}, BAD_INTERVAL),
    ({"facts": [{"label": "2p", "node": "a"}]}, INPUT_INVALID),
    ({"facts": [{"label": "p", "node": "a", "bound": [True, 1]}]}, INPUT_INVALID),
])
def test_the_json_form_is_checked_too(kw: dict, code: str) -> None:
    assert _refused("", **kw).code == code


def test_size_limits_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from g6.graph_reasoning import language
    monkeypatch.setattr(language, "MAX_NODES", 3)
    assert _refused("node a, b, c, d.").code == INPUT_TOO_LARGE
    monkeypatch.setattr(language, "MAX_RULES", 1)
    assert _refused("p(a). q(x) <- p(x). r(x) <- p(x).").code == INPUT_TOO_LARGE
    monkeypatch.setattr(language, "MAX_EDGES", 1)
    assert _refused("edge a -> b, b -> a.").code == INPUT_TOO_LARGE
    monkeypatch.setattr(language, "MAX_FACTS", 1)
    assert _refused("p(a). p(b).").code == INPUT_TOO_LARGE
    monkeypatch.setattr(language, "MAX_TEXT", 5)
    assert _refused("p(a). p(b).").code == INPUT_TOO_LARGE


def test_an_atom_is_read_against_the_model() -> None:
    model = parse_model(SOCIAL)
    assert parse_atom("popular(alice)", model) == ("popular", ("alice",))
    assert parse_atom("friend(alice, bob)", model) == ("friend", ("alice", "bob"))
    for text, code in [("popularity(alice)", UNKNOWN_LABEL), ("popular(alice, bob)", ARITY_CLASH),
                       ("popular(alice", PARSE_ERROR), ("", INPUT_INVALID)]:
        with pytest.raises(Refusal) as caught:
            parse_atom(text, model)
        assert caught.value.code == code, (text, caught.value.reason)
