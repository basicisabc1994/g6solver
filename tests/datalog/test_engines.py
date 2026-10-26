# SPDX-License-Identifier: Apache-2.0
"""g6.datalog's two engines (owner, 2026-10-20): the pure-Python semi-naive evaluator and Z3's Datalog engine. Each
known answer is computed a second way here, by hand or by a plain fixpoint written in the test, and the two engines
must derive the same relations, tuple for tuple, on seeded random programs."""
from __future__ import annotations

import importlib.util
import random
import sys
import time
from collections import deque

import pytest

from g6.datalog import seminaive, z3_engine
from g6.datalog.envelope import SOLVER_UNAVAILABLE, TIME_LIMIT, TOO_MANY_TUPLES, Refusal
from g6.datalog.language import parse_program

HAS_Z3 = importlib.util.find_spec("z3") is not None
needs_z3 = pytest.mark.skipif(not HAS_Z3, reason="z3-solver is not installed (pip install 'g6[formal]')")
ENGINES = [pytest.param("pure_python", id="pure_python"), pytest.param("z3", id="z3", marks=needs_z3)]

EDGES = [("a", "b"), ("b", "c"), ("c", "a"), ("c", "d"), ("d", "e"), ("f", "g"), ("g", "g"), ("h", "a")]
TC = "".join(f"edge({x}, {y}).\n" for x, y in EDGES) + (
    "path(X, Y) :- edge(X, Y).\n"
    "path(X, Z) :- edge(X, Y), path(Y, Z).\n")


def _relations(engine: str, text: str, facts: dict | None = None) -> dict[str, set[tuple]]:
    program = parse_program(text, facts)
    if engine == "z3":
        return z3_engine.solve(program)
    return seminaive.solve(program).relations


def _reach(edges: list[tuple[str, str]]) -> set[tuple[str, str]]:
    """The oracle: breadth-first search from every node."""
    out: dict[str, list[str]] = {}
    for x, y in edges:
        out.setdefault(x, []).append(y)
    found = set()
    for start in {x for e in edges for x in e}:
        seen, queue = set(), deque(out.get(start, []))
        while queue:
            node = queue.popleft()
            if node not in seen:
                seen.add(node)
                queue.extend(out.get(node, []))
        found |= {(start, node) for node in seen}
    return found


@pytest.mark.parametrize("engine", ENGINES)
def test_transitive_closure_matches_breadth_first_search(engine: str) -> None:
    relations = _relations(engine, TC)
    assert relations["path"] == _reach(EDGES) and len(relations["path"]) == 23  # 5 each from a, b, c, h; d, f, g 1
    assert ("a", "a") in relations["path"] and ("e", "a") not in relations["path"] and ("g", "g") in relations["path"]
    assert relations["edge"] == set(EDGES)


PARENTS = [("ann", "bea"), ("ann", "bo"), ("bea", "cy"), ("bo", "di"), ("cy", "ed"), ("di", "fay"), ("gus", "hal"),
           ("hal", "ivy")]
SG = "".join(f"par({c}, {p}).\n" for p, c in PARENTS) + (
    "person(X) :- par(X, _).\nperson(X) :- par(_, X).\n"
    "sg(X, X) :- person(X).\n"
    "sg(X, Y) :- par(X, XP), sg(XP, YP), par(Y, YP).\n")


def _same_generation() -> set[tuple[str, str]]:
    """The oracle: a plain fixpoint over the definition, written out here."""
    parent_of = {(c, p) for p, c in PARENTS}
    people = {x for pair in PARENTS for x in pair}
    sg = {(x, x) for x in people}
    while True:
        more = {(x, y) for (x, xp) in parent_of for (y, yp) in parent_of if (xp, yp) in sg} - sg
        if not more:
            return sg
        sg |= more


@pytest.mark.parametrize("engine", ENGINES)
def test_same_generation_matches_a_plain_fixpoint(engine: str) -> None:
    sg = _relations(engine, SG)["sg"]
    assert sg == _same_generation()
    assert {("cy", "di"), ("ed", "fay"), ("bea", "bo")} <= sg and ("hal", "bea") not in sg and ("cy", "fay") not in sg


NEGATION = """
node(a). node(b). node(c). node(d).
edge(a, b). edge(b, c).
reach(X, Y) :- edge(X, Y).
reach(X, Z) :- edge(X, Y), reach(Y, Z).
unreachable(X, Y) :- node(X), node(Y), X != Y, not reach(X, Y).
isolated(X) :- node(X), not linked(X).
linked(X) :- edge(X, _).
linked(Y) :- edge(_, Y).
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_stratified_negation_matches_the_hand_computed_answer(engine: str) -> None:
    relations = _relations(engine, NEGATION)
    nodes = "abcd"
    reach = {("a", "b"), ("b", "c"), ("a", "c")}
    assert relations["reach"] == reach
    assert relations["unreachable"] == {(x, y) for x in nodes for y in nodes if x != y} - reach
    assert len(relations["unreachable"]) == 9 and relations["isolated"] == {("d",)}


@pytest.mark.parametrize("engine", ENGINES)
def test_comparisons_order_integers_below_texts_and_texts_by_code_point(engine: str) -> None:
    text = ('n(1). n(2). n(10). n(a). n("B"). n(-3).\n'
            "lt(X, Y) :- n(X), n(Y), X < Y.\n"
            "le_two(X) :- n(X), X <= 2.\n"
            "eq(X) :- n(X), X = a.\n"
            "tag(X, small) :- n(X), X < 5.\n"
            "self(X) :- pair(X, X).\npair(1, 1). pair(1, 2).\n")
    relations = _relations(engine, text)
    order = [-3, 1, 2, 10, "B", "a"]
    assert relations["lt"] == {(x, y) for i, x in enumerate(order) for y in order[i + 1:]}
    assert relations["le_two"] == {(-3,), (1,), (2,)} and relations["eq"] == {("a",)}
    assert relations["tag"] == {(-3, "small"), (1, "small"), (2, "small")} and relations["self"] == {(1,)}


@pytest.mark.parametrize("engine", ENGINES)
def test_relations_with_no_arguments_and_negation_across_three_strata(engine: str) -> None:
    relations = _relations(engine, "raining. wet :- raining. dry :- not raining. snowing :- not raining.\n"
                                   "cold :- not snowing. warm(X) :- t(X), not cold. t(1).")
    assert relations["raining"] == {()} and relations["wet"] == {()} and relations["dry"] == set()
    assert relations["snowing"] == set() and relations["cold"] == {()} and relations["warm"] == set()


@pytest.mark.parametrize("engine", ENGINES)
def test_facts_and_rules_for_one_relation_and_json_facts_with_any_string(engine: str) -> None:
    relations = _relations(engine, "p(a). p(X) :- q(X). q(b). r(X) :- named(X, _).",
                           {"named": [["Bob Smith", 1], ["not", 2]], "q": [["c"]]})
    assert relations["p"] == {("a",), ("b",), ("c",)} and relations["r"] == {("Bob Smith",), ("not",)}


@pytest.mark.parametrize("engine", ENGINES)
def test_a_relation_no_fact_or_rule_defines_is_empty(engine: str) -> None:
    relations = _relations(engine, "n(a). n(b). ok(X) :- n(X), not banned(X). bad(X) :- n(X), banned(X).")
    assert relations["ok"] == {("a",), ("b",)} and relations["bad"] == set() and relations["banned"] == set()


def test_the_pure_python_engine_records_one_derivation_per_tuple_each_from_earlier_tuples() -> None:
    program = parse_program(TC)
    model = seminaive.solve(program)
    assert set(model.support) == {(r, t) for r, rows in model.relations.items() for t in rows}
    fact = model.support[("edge", ("a", "b"))]
    assert fact.rule is None and fact.rank == 0
    step = model.support[("path", ("h", "e"))]
    assert step.rule == 1 and step.rank > 0
    bindings = dict(step.bindings)
    assert bindings["X"] == "h" and bindings["Z"] == "e"
    used = model.support[("path", (bindings["Y"], "e"))]
    assert used.rank < step.rank


def test_too_many_tuples_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = "".join(f"e({i}, {i + 1}).\n" for i in range(20)) + "p(X, Y) :- e(X, Y).\np(X, Z) :- e(X, Y), p(Y, Z).\n"
    monkeypatch.setattr(seminaive, "MAX_TUPLES", 100)
    with pytest.raises(Refusal) as caught:
        seminaive.solve(parse_program(chain))
    assert caught.value.code == TOO_MANY_TUPLES and "100" in caught.value.reason
    monkeypatch.setattr(seminaive, "MAX_TUPLES", 1000)
    assert len(seminaive.solve(parse_program(chain)).relations["p"]) == 210  # survival: under the limit


@pytest.mark.parametrize("engine", ENGINES)
def test_a_deadline_already_past_is_refused(engine: str) -> None:
    program = parse_program(TC)
    with pytest.raises(Refusal) as caught:
        if engine == "z3":
            z3_engine.solve(program, deadline=time.monotonic() - 1)
        else:
            seminaive.solve(program, deadline=time.monotonic() - 1)
    assert caught.value.code == TIME_LIMIT


def test_the_pure_python_engine_stops_at_its_deadline() -> None:
    program = parse_program("".join(f"e({i}, {i + 1}).\n" for i in range(300))
                            + "p(X, Y) :- e(X, Y).\np(X, Z) :- p(X, Y), p(Y, Z).\n")
    started = time.monotonic()
    with pytest.raises(Refusal) as caught:
        seminaive.solve(program, deadline=started + 0.05)
    assert caught.value.code == TIME_LIMIT and time.monotonic() - started < 2


def test_without_z3_the_z3_engine_is_unavailable_and_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)
    assert z3_engine.available() is False and z3_engine.version() == ""
    with pytest.raises(Refusal) as caught:
        z3_engine.solve(parse_program(TC))
    assert caught.value.code == SOLVER_UNAVAILABLE and "pip install 'g6[formal]'" in caught.value.reason


# --- the two engines agree on random programs -----------------------------------------------------------------------

_LEVELS = {"e1": 0, "e2": 0, "e3": 0, "i0": 1, "i1": 1, "i2": 2, "i3": 2}
_ARITY = {"e1": 1, "e2": 2, "e3": 2, "i0": 1, "i1": 2, "i2": 2, "i3": 1}
_VALUES = ["a", "b", "c", 1, 2]


def _text(value: object) -> str:
    return str(value)


def random_program(seed: int) -> str:
    """A safe, stratified program: positive atoms at or below the head's level, negated ones strictly below."""
    rng = random.Random(seed)
    lines = []
    for name in ("e1", "e2", "e3"):
        for _ in range(rng.randint(0, 7)):
            lines.append(f"{name}({', '.join(_text(rng.choice(_VALUES)) for _ in range(_ARITY[name]))}).")
    for head in ("i0", "i1", "i2", "i3"):
        for _ in range(rng.randint(1, 3)):
            level = _LEVELS[head]
            usable = [r for r, lv in _LEVELS.items() if lv <= level]
            body, bound = [], []
            for _ in range(rng.randint(1, 3)):
                rel = rng.choice(usable)
                args = []
                for _ in range(_ARITY[rel]):
                    term = rng.choice(["X", "Y", "Z", "W", "_", rng.choice(_VALUES)])
                    args.append(_text(term))
                    if term in ("X", "Y", "Z", "W"):
                        bound.append(term)
                body.append(f"{rel}({', '.join(args)})")
            lower = [r for r, lv in _LEVELS.items() if lv < level]
            if bound and lower and rng.random() < 0.5:
                rel = rng.choice(lower)
                body.append(f"not {rel}({', '.join(rng.choice(bound + ['a', '1']) for _ in range(_ARITY[rel]))})")
            if bound and rng.random() < 0.4:
                body.append(f"{rng.choice(bound)} {rng.choice(['=', '!=', '<', '<=', '>', '>='])} "
                            f"{rng.choice(bound + ['b', 2])}")
            head_args = [rng.choice(bound + ["c"]) if bound else "c" for _ in range(_ARITY[head])]
            lines.append(f"{head}({', '.join(head_args)}) :- {', '.join(body)}.")
    return "\n".join(lines)


@needs_z3
@pytest.mark.parametrize("seed", range(60))
def test_the_two_engines_agree_tuple_for_tuple_on_random_programs(seed: int) -> None:
    program = parse_program(random_program(seed))
    python = seminaive.solve(program).relations
    assert z3_engine.solve(program) == python


def test_the_random_programs_are_not_trivial() -> None:
    """Survival control: the generator makes programs that derive something, use negation and recurse."""
    derived = negated = recursive = 0
    for seed in range(60):
        program = parse_program(random_program(seed))
        relations = seminaive.solve(program).relations
        derived += any(relations[r] for r in ("i0", "i1", "i2", "i3") if r in relations)
        negated += any(rule.negations for rule in program.rules)
        recursive += bool(program.recursive)
    assert derived >= 40 and negated >= 30 and recursive >= 20, (derived, negated, recursive)
