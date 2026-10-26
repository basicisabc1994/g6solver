# SPDX-License-Identifier: Apache-2.0
"""g6.datalog's language (owner, 2026-10-20): a strict Datalog read by G6's own parser, never evaluated as code. Every
program is parsed, then checked for one arity per relation, safe rules and stratified negation, and each refusal has
its own stable code, with the line and column it found."""
from __future__ import annotations

import pytest

from g6.datalog import language
from g6.datalog.envelope import (
    ARITY_CLASH,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NOT_STRATIFIABLE,
    PARSE_ERROR,
    UNKNOWN_RELATION,
    UNSAFE_RULE,
    Refusal,
)
from g6.datalog.language import Comparison, Const, Negation, Var, parse_goal, parse_program, render_atom, render_rule

FAMILY = """
% a small family
parent(alice, bob).
parent(bob, carol).   // two comment styles
parent(carol, dave).
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
ancestor(alice, X)?
"""


def _refused(text: str = "", facts: dict | None = None) -> Refusal:
    with pytest.raises(Refusal) as caught:
        parse_program(text, facts)
    return caught.value


def test_a_program_is_read_into_facts_rules_and_queries() -> None:
    program = parse_program(FAMILY)
    assert program.facts == {"parent": frozenset({("alice", "bob"), ("bob", "carol"), ("carol", "dave")})}
    assert [render_rule(r) for r in program.rules] == ["ancestor(X, Y) :- parent(X, Y).",
                                                       "ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z)."]
    assert [render_atom(q) for q in program.queries] == ["ancestor(alice, X)"]
    assert program.arities == {"parent": 2, "ancestor": 2}
    assert (program.rules[1].line, program.rules[1].column) == (7, 1)
    assert program.strata == {"parent": 0, "ancestor": 0} and program.recursive == frozenset({"ancestor"})


def test_constants_are_names_quoted_strings_or_integers_and_a_name_is_its_string() -> None:
    program = parse_program('p(alice, "alice", "Bob Smith, Jr.", "say \\"hi\\"\\n", 42, -7, 0).')
    assert program.facts["p"] == frozenset({("alice", "alice", "Bob Smith, Jr.", 'say "hi"\n', 42, -7, 0)})
    assert render_atom(language.Atom("p", (Const("alice"), Const("Bob Smith"), Const(3), Const("not")))) == \
        'p(alice, "Bob Smith", 3, "not")'


def test_relations_with_no_arguments_negation_and_comparisons() -> None:
    program = parse_program("raining. wet :- raining. dry :- not raining.\n"
                            "n(1). n(2). big(X) :- n(X), X >= 2, X != 3, 1 < 2.")
    assert program.facts == {"raining": frozenset({()}), "n": frozenset({(1,), (2,)})}
    dry, big = program.rules[1], program.rules[2]
    assert isinstance(dry.body[0], Negation) and dry.body[0].atom.relation == "raining"
    assert [lit.op for lit in big.body if isinstance(lit, Comparison)] == [">=", "!=", "<"]
    assert program.strata["dry"] == program.strata["raining"] + 1
    assert render_rule(big) == "big(X) :- n(X), X >= 2, X != 3, 1 < 2."


def test_variables_start_upper_case_or_with_an_underscore_and_each_lone_underscore_is_its_own() -> None:
    program = parse_program("e(a, b). e(b, c). out(X) :- e(X, _). pair(_X, _X) :- e(_X, _), e(_, _X).")
    out, pair = program.rules
    assert out.body[0].args[0] == Var("X") and out.body[0].args[1].name != "X"
    first, second = pair.body[0].args[1], pair.body[1].args[0]
    assert first != second and first.anonymous and second.anonymous
    assert pair.head.args == (Var("_X"), Var("_X"))


def test_strata_put_each_negated_relation_below_the_rule_that_negates_it() -> None:
    program = parse_program("node(a). edge(a, a).\n"
                            "reach(X, Y) :- edge(X, Y).\nreach(X, Z) :- edge(X, Y), reach(Y, Z).\n"
                            "lonely(X) :- node(X), not reach(X, X).\nhappy(X) :- node(X), not lonely(X).")
    assert program.strata == {"node": 0, "edge": 0, "reach": 0, "lonely": 1, "happy": 2}
    assert [set(s) for s in program.order] == [{"node", "edge", "reach"}, {"lonely"}, {"happy"}]


def test_facts_may_also_come_as_declarative_json() -> None:
    program = parse_program("ancestor(X, Y) :- parent(X, Y).",
                            {"parent": [["alice", "bob"], ["Bob Smith", 3]], "flag": [[]]})
    assert program.facts == {"parent": frozenset({("alice", "bob"), ("Bob Smith", 3)}), "flag": frozenset({()})}
    assert program.arities == {"parent": 2, "flag": 0, "ancestor": 2}


def test_a_goal_is_read_against_the_program() -> None:
    program = parse_program(FAMILY)
    goal = parse_goal("ancestor(alice, X)?", program)
    assert goal.args == (Const("alice"), Var("X"))
    assert parse_goal("  ancestor(bob, carol)  ", program).args == (Const("bob"), Const("carol"))


@pytest.mark.parametrize(("text", "where", "says"), [
    ("parent(alice, bob)", (1, 19), "expected '.'"),
    ("parent(alice, bob).\nparent(bob carol).", (2, 12), "expected ',' or ')'"),
    ('p("open).', (1, 3), "unterminated string"),
    ('p("a\\q").', (1, 5), "escape"),
    ("p(a) :- .", (1, 9), "expected a relation name"),
    ("p(a) :- q(a),.", (1, 14), "expected a relation name"),
    ("Parent(a, b).", (1, 1), "relation name"),
    ("p(a). ?- p(X).", (1, 7), "write a query as"),
    ("p(1.5).", (1, 3), "decimal"),
    ("p(a) ; q(a).", (1, 6), "unexpected character"),
    ("not p(a).", (1, 1), "head"),
    ("p(a) :- not not q(a).", (1, 13), "expected a relation name"),
    ("p(X) :- q(X), X == 1.", (1, 17), "unexpected character"),
    ("p(" + "9" * 40 + ").", (1, 3), "integer"),
    ("p(a) :- q(a)", (1, 13), "expected '.'"),
])
def test_a_parse_error_says_where_and_what_was_expected(text: str, where: tuple[int, int], says: str) -> None:
    refusal = _refused(text)
    assert refusal.code == PARSE_ERROR and says in refusal.reason, refusal.reason
    problem = refusal.keep["problems"][0]
    assert (problem["line"], problem["column"]) == where, problem
    assert refusal.reason.startswith(f"line {where[0]}, column {where[1]}: ")


@pytest.mark.parametrize(("text", "says"), [
    ("p(X) :- q(Y). q(a).", "X appears in the head"),
    ("p(X).", "a fact must be ground"),
    ("p(_) :- q(a). q(a).", "anonymous variable in the head"),
    ("p(X) :- q(X), not r(X, Y). q(a). r(a, a).", "Y appears in a negated atom"),
    ("p(X) :- q(X), not r(X, _). q(a). r(a, a).", "anonymous variable in a negated atom"),
    ("p(X) :- q(X), X < Y. q(a).", "Y appears in a comparison"),
    ("p(X) :- not q(X). q(a).", "X appears in the head"),
    ("p(X) :- X = a.", "X appears in the head"),
])
def test_an_unsafe_rule_is_refused(text: str, says: str) -> None:
    refusal = _refused(text)
    assert refusal.code == UNSAFE_RULE and says in refusal.reason, refusal.reason
    assert refusal.keep["problems"][0]["line"] == 1


def test_every_unsafe_rule_is_listed() -> None:
    refusal = _refused("q(a).\np(X) :- q(Y).\nr(Z) :- q(a).\n")
    assert refusal.code == UNSAFE_RULE and [p["line"] for p in refusal.keep["problems"]] == [2, 3]


@pytest.mark.parametrize("text", [
    "r(a). p(X) :- r(X), not p(X).",
    "r(a). p(X) :- r(X), not q(X). q(X) :- r(X), not p(X).",
    "r(a). p(X) :- r(X), not q(X). q(X) :- s(X). s(X) :- p(X).",
])
def test_negation_through_recursion_is_refused_with_the_cycle(text: str) -> None:
    refusal = _refused(text)
    assert refusal.code == NOT_STRATIFIABLE and "p" in refusal.keep["cycle"], refusal.reason
    assert "negation" in refusal.reason and refusal.keep["problems"][0]["line"] == 1


@pytest.mark.parametrize(("text", "facts", "says"), [
    ("parent(a, b). parent(a, b, c).", None, "parent is used with 3 arguments"),
    ("p(a). q(X) :- p(X, X).", None, "p is used with 2 arguments"),
    ("q(X) :- p(X).", {"p": [["a", "b"]]}, "p is used with 1 argument"),
    ("", {"p": [["a"], ["a", "b"]]}, "p is used with 2 arguments"),
])
def test_one_relation_name_has_one_arity(text: str, facts: dict | None, says: str) -> None:
    refusal = _refused(text, facts)
    assert refusal.code == ARITY_CLASH and says in refusal.reason, refusal.reason


@pytest.mark.parametrize(("facts", "says"), [
    ({"Parent": [["a"]]}, "relation name"),
    ({"p": [[True]]}, "string or an integer"),
    ({"p": [[1.5]]}, "string or an integer"),
    ({"p": [[10 ** 30]]}, "integer"),
    ({"not": [["a"]]}, "keyword"),
])
def test_json_facts_that_are_not_constants_are_refused(facts: dict, says: str) -> None:
    refusal = _refused("", facts)
    assert refusal.code == INPUT_INVALID and says in refusal.reason, refusal.reason


def test_size_limits_are_refused_before_anything_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(language, "MAX_FACTS", 3)
    monkeypatch.setattr(language, "MAX_RULES", 1)
    monkeypatch.setattr(language, "MAX_TEXT", 200)
    assert _refused("p(1). p(2). p(3). p(4).").code == INPUT_TOO_LARGE
    assert "4 facts" in _refused("p(1). p(2).", {"q": [[1], [2]]}).reason
    assert _refused("q(1). p(X) :- q(X). r(X) :- q(X).").code == INPUT_TOO_LARGE
    assert _refused("% " + "x" * 300).code == INPUT_TOO_LARGE
    assert parse_program("p(1). p(2). p(3). r(X) :- p(X).").arities == {"p": 1, "r": 1}  # survival: at the limits


@pytest.mark.parametrize(("goal", "code"), [
    ("ancestr(alice, X)", UNKNOWN_RELATION),
    ("ancestor(alice)", ARITY_CLASH),
    ("ancestor(alice, X) :- parent(X, Y)", PARSE_ERROR),
    ("ancestor(alice, X). parent(a, b)", PARSE_ERROR),
    ("", INPUT_INVALID),
])
def test_a_goal_that_names_nothing_in_the_program_is_refused(goal: str, code: str) -> None:
    with pytest.raises(Refusal) as caught:
        parse_goal(goal, parse_program(FAMILY))
    assert caught.value.code == code, caught.value.reason
