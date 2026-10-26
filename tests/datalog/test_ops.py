# SPDX-License-Identifier: Apache-2.0
"""g6.datalog's ops (owner, 2026-10-20): evaluate, query, explain and check, each labelled by one envelope. With Z3
both engines run and must agree; without it the pure-Python engine runs alone and the card says so and how to add the
second; either way G6's check runs, and an answer it contradicts, or one the engines disagree on, is blocked. Nothing
is ever ``verified``."""
from __future__ import annotations

import importlib.util
import sys

import pytest
from pydantic import ValidationError

from g6.datalog import ops, seminaive, z3_engine
from g6.datalog.envelope import (
    ANSWER_TOO_LARGE,
    CERTIFICATE_REFUTED,
    ENGINE_ANSWER,
    ENGINES_DISAGREE,
    INPUT_INVALID,
    NOT_CHECKED,
    NOT_EVALUATED,
    ONE_ENGINE,
    PARSE_ERROR,
    SOLVER_UNAVAILABLE,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_RELATION,
    UNSAFE_RULE,
    finish,
)
from g6.datalog.schema import DatalogResult
from g6.datalog.seminaive import Justification, Model
from g6.invariants import check_envelope

HAS_Z3 = importlib.util.find_spec("z3") is not None
needs_z3 = pytest.mark.skipif(not HAS_Z3, reason="z3-solver is not installed (pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver')")

FAMILY = """
parent(alice, bob).
parent(bob, carol).
parent(carol, dave).
parent(alice, eve).
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
ancestor(alice, X)?
"""
ANCESTORS = [["alice", "bob"], ["alice", "carol"], ["alice", "dave"], ["alice", "eve"], ["bob", "carol"],
             ["bob", "dave"], ["carol", "dave"]]


@pytest.fixture
def no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError


def _ok(result: DatalogResult) -> dict:
    data = result.model_dump(mode="json")
    assert check_envelope(data) == [], check_envelope(data)
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    return data


def _blocked(result: DatalogResult, code: str) -> dict:
    data = result.model_dump(mode="json")
    assert check_envelope(data) == [] and result.completion_state == "blocked-escalated"
    assert result.code == code and result.warning_card["code"] == code, (result.code, result.reason)
    assert result.relations == {} and result.queries == [] and result.explanation == {}
    return data


def test_evaluate_returns_the_derived_relations_checked() -> None:
    result = ops.evaluate(FAMILY, request_id="r-1", run_id="u-1")
    _ok(result)
    assert result.relations == {"ancestor": ANCESTORS}
    assert result.check["passed"] is True and result.check["checked"] == ["closure", "support", "negation"]
    assert (result.request_id, result.run_id) == ("r-1", "u-1")
    evidence = result.evidence[0]
    assert evidence["kind"] == "datalog_model" and evidence["program"]["rules"] == 2
    assert evidence["model"]["tuples"] == 11 and evidence["engines"][0]["name"] == "pure_python"


@needs_z3
def test_with_z3_both_engines_run_and_agree() -> None:
    result = ops.evaluate(FAMILY)
    _ok(result)
    assert result.engines == ["pure_python", "z3"] and result.warning_card["code"] == ENGINE_ANSWER
    evidence = result.evidence[0]
    assert evidence["z3_ran"] is True and evidence["agreement"]["agreed"] is True
    assert "tuple for tuple" in result.warning_card["message"] and "not verified" in result.warning_card["message"]


def test_without_z3_the_pure_python_engine_runs_alone_and_the_card_says_how_to_add_the_second(no_z3) -> None:
    result = ops.evaluate(FAMILY)
    _ok(result)
    assert result.engines == ["pure_python"] and result.relations == {"ancestor": ANCESTORS}
    card = result.warning_card
    assert card["code"] == ONE_ENGINE and "Only the pure-Python engine ran" in card["message"]
    assert "Z3 is not installed here" in card["message"] and "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'" in card["suggested_action"]
    assert result.check["passed"] is True and result.evidence[0]["z3_ran"] is False


def test_asking_for_z3_without_it_is_refused(no_z3) -> None:
    data = _blocked(ops.evaluate(FAMILY, engine="z3"), SOLVER_UNAVAILABLE)
    assert "pip install 'g6[formal] @ git+https://github.com/basicisabc1994/g6solver'" in data["reason"] and data["evidence"][0]["kind"] == "datalog_refusal"


def test_asking_for_the_pure_python_engine_alone_says_so() -> None:
    result = ops.evaluate(FAMILY, engine="python")
    _ok(result)
    assert result.engines == ["pure_python"] and "engine='python' was asked for" in result.warning_card["message"]


def test_evaluate_can_name_the_relations_it_returns() -> None:
    result = ops.evaluate(FAMILY, relations=["parent"])
    assert _ok(result)["relations"] == {"parent": [["alice", "bob"], ["alice", "eve"], ["bob", "carol"],
                                                   ["carol", "dave"]]}
    _blocked(ops.evaluate(FAMILY, relations=["ancestr"]), UNKNOWN_RELATION)


def test_query_answers_each_distinct_binding_with_its_derivation() -> None:
    result = ops.query(FAMILY, "ancestor(X, dave)")
    _ok(result)
    (goal,) = result.queries
    assert goal["goal"] == "ancestor(X, dave)" and goal["count"] == 3
    assert [a["bindings"] for a in goal["answers"]] == [{"X": "alice"}, {"X": "bob"}, {"X": "carol"}]
    first = goal["answers"][0]
    assert first["tuple"] == ["alice", "dave"] and first["derivation"]["atom"] == "ancestor(alice, dave)"
    assert first["derivation"]["by"] == "rule" and first["derivation"]["rule"] == 2


def test_query_reads_the_programs_own_queries_and_projects_anonymous_variables() -> None:
    result = ops.query(FAMILY)
    assert [a["bindings"]["X"] for a in _ok(result)["queries"][0]["answers"]] == ["bob", "carol", "dave", "eve"]
    projected = ops.query(FAMILY, "parent(X, _)").queries[0]
    assert [a["bindings"] for a in projected["answers"]] == [{"X": "alice"}, {"X": "bob"}, {"X": "carol"}]
    yes, no = ops.query(FAMILY, "ancestor(alice, dave)?").queries[0], ops.query(FAMILY, "ancestor(dave, alice)")
    assert yes["count"] == 1 and yes["answers"][0]["bindings"] == {}
    assert _ok(no)["queries"][0]["count"] == 0 and no.queries[0]["answers"] == []


@pytest.mark.parametrize("op", [ops.evaluate, ops.query, ops.explain, ops.check_program])
def test_an_empty_program_is_refused_rather_than_answered_with_nothing(op) -> None:
    data = _blocked(op("  % only a comment\n"), INPUT_INVALID)
    assert "empty" in data["reason"]
    assert ops.evaluate("", facts={"p": [["a"]]}).completion_state == "qualified-draft"  # survival: JSON facts count


def test_query_without_a_goal_or_with_a_misspelt_one_is_refused() -> None:
    _blocked(ops.query("p(a)."), INPUT_INVALID)
    _blocked(ops.query(FAMILY, "ancestr(alice, X)"), UNKNOWN_RELATION)


def _leaves(node: dict) -> list[dict]:
    return [node] if not node.get("from") else [leaf for child in node["from"] for leaf in _leaves(child)]


def test_explain_gives_the_derivation_tree_down_to_the_facts() -> None:
    result = ops.explain(FAMILY, "ancestor(alice, dave)")
    explanation = _ok(result)["explanation"]
    assert explanation["derivable"] is True and explanation["truncated"] is False
    tree = explanation["derivation"]
    assert tree["atom"] == "ancestor(alice, dave)" and tree["text"] == "ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z)."
    assert tree["bindings"] == {"X": "alice", "Y": "bob", "Z": "dave"} and tree["line"] == 7
    assert [leaf["atom"] for leaf in _leaves(tree)] == ["parent(alice, bob)", "parent(bob, carol)",
                                                        "parent(carol, dave)"]
    assert all(leaf["by"] == "fact" for leaf in _leaves(tree))


def test_explain_shows_a_negation_and_a_comparison_it_relied_on() -> None:
    text = "n(1). n(2). n(3). bad(2). good(X) :- n(X), X > 1, not bad(X)."
    tree = ops.explain(text, "good(3)").explanation["derivation"]
    assert tree["conditions"] == ["3 > 1", "not bad(3)"]


def test_explain_says_a_tuple_is_not_derivable_on_the_strength_of_the_closure_check() -> None:
    result = ops.explain(FAMILY, "ancestor(dave, alice)")
    explanation = _ok(result)["explanation"]
    assert explanation["derivable"] is False and "derivation" not in explanation
    assert "closed" in explanation["because"] and result.check["passed"] is True


def test_explain_takes_a_ground_atom() -> None:
    _blocked(ops.explain(FAMILY, "ancestor(alice, X)"), INPUT_INVALID)


def test_a_long_derivation_is_cut_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = "".join(f"e({i}, {i + 1}).\n" for i in range(30)) + "p(X, Y) :- e(X, Y).\np(X, Z) :- e(X, Y), p(Y, Z).\n"
    monkeypatch.setattr(ops, "MAX_EXPLAIN_NODES", 10)
    explanation = ops.explain(chain, "p(0, 30)").explanation
    assert explanation["derivable"] is True and explanation["truncated"] is True and explanation["nodes"] == 10


def test_check_reports_the_program_without_evaluating_it() -> None:
    result = ops.check_program("node(a). edge(a, a). reach(X, Y) :- edge(X, Y). reach(X, Z) :- edge(X, Y), "
                               "reach(Y, Z). lonely(X) :- node(X), not reach(X, X), not banned(X). lonely(X)?")
    data = _ok(result)
    assert result.warning_card["code"] == NOT_EVALUATED and result.engines == [] and result.check == {}
    report = data["report"]
    assert report["relations"]["reach"] == {"arity": 2, "stratum": 0, "defined_by": "rules", "recursive": True}
    assert report["relations"]["banned"]["defined_by"] == "nothing" and report["undefined"] == ["banned"]
    assert report["strata"] == [["banned", "edge", "node", "reach"], ["lonely"]]
    assert (report["facts"], report["rules"], report["queries"], report["negation"]) == (2, 3, ["lonely(X)"], True)
    assert "banned" in result.warning_card["message"]


@pytest.mark.parametrize(("text", "code"), [("p(X) :- q(Y). q(a).", UNSAFE_RULE), ("p(a", PARSE_ERROR)])
def test_check_refuses_a_program_that_cannot_run_with_where_it_failed(text: str, code: str) -> None:
    data = _blocked(ops.check_program(text), code)
    assert data["problems"][0]["line"] == 1 and data["problems"][0]["column"] >= 1
    assert data["evidence"][0]["problems"] == data["problems"]


def test_a_relation_no_fact_or_rule_defines_is_named_on_the_card() -> None:
    result = ops.evaluate("n(a). ok(X) :- n(X), not banend(X).")
    assert _ok(result)["relations"] == {"ok": [["a"]]}
    assert "No fact or rule defines banend" in result.warning_card["message"]


def test_engines_that_disagree_block_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    real = seminaive.solve
    monkeypatch.setattr(z3_engine, "available", lambda: True)
    monkeypatch.setattr(z3_engine, "version", lambda: "planted")

    def short(program, *, deadline=None):
        relations = {name: set(rows) for name, rows in real(program).relations.items()}
        relations["ancestor"].discard(("alice", "dave"))
        return relations

    monkeypatch.setattr(z3_engine, "solve", short)
    data = _blocked(ops.evaluate(FAMILY), ENGINES_DISAGREE)
    assert data["evidence"][0]["agreement"]["agreed"] is False
    assert any("ancestor" in d and "ancestor(alice, dave)" in d for d in data["evidence"][0]["agreement"]["differences"])
    assert data["engines"] == ["pure_python", "z3"]


def test_a_model_the_check_contradicts_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    real = seminaive.solve

    def planted(program, *, deadline=None, max_tuples=None):
        model = real(program)
        model.relations["ancestor"].add(("eve", "alice"))
        model.support[("ancestor", ("eve", "alice"))] = Justification(0, (("X", "eve"), ("Y", "alice")), 1)
        return model

    monkeypatch.setattr(seminaive, "solve", planted)
    data = _blocked(ops.evaluate(FAMILY, engine="python"), CERTIFICATE_REFUTED)
    assert any("parent(eve, alice)" in f for f in data["evidence"][0]["check"]["failures"])
    assert data["evidence"][0]["kind"] == "datalog_check"


@pytest.mark.parametrize(("seconds", "code"), [(61, TIME_LIMIT_TOO_LONG), (0, INPUT_INVALID), (-1, INPUT_INVALID),
                                               (float("nan"), INPUT_INVALID)])
def test_time_limits_are_capped(seconds: float, code: str) -> None:
    _blocked(ops.evaluate(FAMILY, time_limit=seconds), code)


def test_a_program_that_runs_past_its_time_limit_is_blocked() -> None:
    chain = "".join(f"e({i}, {i + 1}).\n" for i in range(300)) + "p(X, Y) :- e(X, Y).\np(X, Z) :- p(X, Y), p(Y, Z).\n"
    _blocked(ops.evaluate(chain, time_limit=0.05), TIME_LIMIT)


def test_an_answer_too_large_for_one_reply_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "MAX_ANSWER_TUPLES", 5)
    data = _blocked(ops.evaluate(FAMILY), ANSWER_TOO_LARGE)
    assert "7 tuples" in data["reason"]
    _blocked(ops.query(FAMILY, "ancestor(X, Y)"), ANSWER_TOO_LARGE)
    assert ops.query(FAMILY, "ancestor(alice, X)").completion_state == "qualified-draft"  # survival: 4 answers fit


def test_no_result_can_be_verified_and_none_is_reported_unchecked() -> None:
    with pytest.raises(ValidationError):
        DatalogResult(operation="evaluate", completion_state="verified")
    unchecked = finish("evaluate", engines=[{"name": "pure_python", "version": "x"}], relations={"p": [["a"]]})
    assert unchecked.code == NOT_CHECKED and unchecked.completion_state == "blocked-escalated"
    assert unchecked.relations == {}


def test_a_model_object_is_what_the_engine_returns() -> None:
    assert isinstance(seminaive.solve(ops.parse_program(FAMILY)), Model)
