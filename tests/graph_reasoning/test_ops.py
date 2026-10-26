# SPDX-License-Identifier: Apache-2.0
"""The ops of g6.graph_reasoning (owner, 2026-10-20): reason, query, explain and check, each one labelled result.
States are only qualified-draft or blocked-escalated; a conflict is blocked with its derivation; a check that fails or
engines that disagree block the answer; and the card says which engines ran."""
from __future__ import annotations

import pytest

from g6.graph_reasoning import checks, ops, pyreason_engine
from g6.graph_reasoning.envelope import (
    BAD_INTERVAL,
    CERTIFICATE_REFUTED,
    CONFLICT,
    ENGINE_ANSWER,
    ENGINE_UNAVAILABLE,
    ENGINES_DISAGREE,
    INPUT_INVALID,
    NOT_COMPARABLE,
    NOT_EVALUATED,
    ONE_ENGINE,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_EDGE,
    UNKNOWN_LABEL,
    UNKNOWN_NODE,
)

SOCIAL = """
node alice, bob, carol, dave, eve.
friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].
popular(dave).
popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].
"""
CONFLICTING = "trusted(a) : [0.8, 1]. flagged(a).\ntrusted(x) : [0, 0.3] <- flagged(x).\n"
THRESHOLD = "popular(p1). popular(p2). friend(x1, p1). friend(x1, p2).\nhub(x) <- friend(x, y), popular(y) at least 2.\n"


@pytest.fixture
def no_pyreason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyreason_engine, "unavailable_reason", lambda: "PyReason is not installed here")


def _walk(node: dict) -> list[dict]:
    found, stack = [], [node]
    while stack:
        current = stack.pop()
        found.append(current)
        for by in current.get("by", []):
            stack.extend(by.get("from", []))
    return found


def test_reason_returns_every_known_atom_on_one_engine(no_pyreason) -> None:
    result = ops.reason(SOCIAL, request_id="r1", run_id="u1")
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.warning_card["code"] == ONE_ENGINE and "not installed" in result.warning_card["message"]
    assert result.engines == ["pure_python"] and result.check["passed"] is True
    rows = {r["atom"]: r for r in result.interpretation}
    assert rows["popular(alice)"]["bounds"] == [0.7, 1.0] and rows["popular(alice)"]["exact"] == ["0.7", "1"]
    assert rows["popular(alice)"]["status"] == "between" and rows["popular(dave)"]["status"] == "true"
    assert rows["friend(carol, dave)"]["edge"] == ["carol", "dave"]
    assert "popular(eve)" not in rows                                   # unknown atoms are not listed
    assert (result.request_id, result.run_id) == ("r1", "u1")
    assert result.evidence[0]["kind"] == "graph_reasoning_interpretation"


def test_reason_can_name_the_labels_and_refuses_unknown_ones(no_pyreason) -> None:
    result = ops.reason(SOCIAL, labels=["popular"])
    assert {r["label"] for r in result.interpretation} == {"popular"}
    refused = ops.reason(SOCIAL, labels=["famous"])
    assert refused.code == UNKNOWN_LABEL and refused.completion_state == "blocked-escalated"


def test_query_gives_an_interval_and_the_steps_that_set_it(no_pyreason) -> None:
    answer = ops.query(SOCIAL, "popular(bob)").answer
    assert answer["bounds"] == [0.7, 1.0] and answer["status"] == "between"
    by = answer["derivation"]["by"][0]
    assert by["by"] == "rule" and by["sets"] == ["lower"] and by["bindings"] == {"x": "bob"}
    assert {child["atom"] for child in by["from"]} == {"friend(bob, carol)", "popular(carol)"}


def test_query_on_an_unknown_atom_says_unknown(no_pyreason) -> None:
    answer = ops.query(SOCIAL, "popular(eve)").answer
    assert answer["bounds"] == [0.0, 1.0] and answer["status"] == "unknown" and answer["derivation"]["unknown"]


@pytest.mark.parametrize(("atom", "code"), [("popular(zed)", UNKNOWN_NODE), ("friend(alice, dave)", UNKNOWN_EDGE),
                                            ("famous(alice)", UNKNOWN_LABEL), ("", INPUT_INVALID)])
def test_query_refuses_atoms_the_model_does_not_have(atom: str, code: str, no_pyreason) -> None:
    result = ops.query(SOCIAL, atom)
    assert result.code == code and result.completion_state == "blocked-escalated" and result.answer == {}


def test_explain_derives_the_interval_down_to_the_facts(no_pyreason) -> None:
    explanation = ops.explain(SOCIAL, "popular(alice)").explanation
    assert explanation["bounds"] == [0.7, 1.0] and not explanation["truncated"]
    nodes = _walk(explanation["derivation"])
    leaves = [n for n in nodes if not any(b.get("from") for b in n.get("by", []))]
    assert {n["atom"] for n in leaves} == {"friend(alice, bob)", "friend(bob, carol)", "friend(carol, dave)",
                                           "popular(dave)"}
    assert all(b["by"] == "fact" for n in leaves for b in n["by"])
    ranks = [b["rank"] for n in nodes for b in n.get("by", []) if b["by"] == "rule"]
    assert ranks == sorted(ranks, reverse=True)                         # each step rests on earlier ones


def test_explain_says_why_an_atom_stayed_unknown(no_pyreason) -> None:
    explanation = ops.explain(SOCIAL, "popular(eve)").explanation
    assert explanation["status"] == "unknown" and "stays [0, 1]" in explanation["because"]


def test_check_reports_without_evaluating() -> None:
    result = ops.check_model(SOCIAL + "hub(x) <- friend(x, y) at least 2.\n")
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == NOT_EVALUATED
    report = result.report
    assert report["labels"]["popular"] == {"arity": 1, "kind": "node", "stratum": 0, "given_by": "facts and rules",
                                           "recursive": True}
    assert report["rules"][1]["thresholds"] == ["friend"] and report["pyreason_comparable"] is False
    assert "threshold" in report["outside_pyreason_fragment"][0]
    assert result.interpretation == [] and result.engines == []


def test_a_conflict_is_blocked_with_its_derivation(no_pyreason) -> None:
    result = ops.reason(CONFLICTING)
    assert result.completion_state == "blocked-escalated" and result.code == CONFLICT
    assert "trusted(a) would be empty" in result.reason and result.interpretation == []
    conflict = result.conflict
    assert conflict["atom"] == "trusted(a)" and conflict["before"] == [0.8, 1.0] and conflict["met"] == [0.0, 0.3]
    assert conflict["between"][0]["derivation"]["by"][0]["by"] == "fact"
    assert conflict["between"][1]["by"] == "rule" and conflict["between"][1]["from"][0]["atom"] == "flagged(a)"
    assert "[0, 1]" in conflict["pyreason_would"]                       # PyReason's handling, documented
    assert result.check["passed"] is True and result.evidence[0]["conflict"]["atom"] == "trusted(a)"


def test_a_refused_model_is_blocked_with_where_it_failed(no_pyreason) -> None:
    result = ops.reason("p(a) : [0.9, 0.1].")
    assert result.code == BAD_INTERVAL and result.problems[0]["line"] == 1


@pytest.mark.parametrize(("kw", "code"), [({"time_limit": 61}, TIME_LIMIT_TOO_LONG), ({"time_limit": 0}, INPUT_INVALID),
                                          ({"engine": "z3"}, INPUT_INVALID)])
def test_bad_parameters_are_refused(kw: dict, code: str, no_pyreason) -> None:
    assert ops.reason(SOCIAL, **kw).code == code


def test_an_empty_model_is_refused() -> None:
    assert ops.reason("").code == INPUT_INVALID and ops.check_model("node a.").code == INPUT_INVALID


def test_asking_for_pyreason_without_it_is_refused(no_pyreason) -> None:
    result = ops.reason(SOCIAL, engine="pyreason")
    assert result.code == ENGINE_UNAVAILABLE and result.completion_state == "blocked-escalated"


def test_asking_for_pyreason_on_a_model_it_evaluates_differently_is_refused() -> None:
    result = ops.reason(THRESHOLD, engine="pyreason")
    assert result.code == NOT_COMPARABLE and "threshold" in result.reason


def test_engine_python_says_so_on_the_card() -> None:
    result = ops.reason(SOCIAL, engine="python")
    assert result.warning_card["code"] == ONE_ENGINE and "engine='python'" in result.warning_card["message"]


def test_a_model_outside_pyreasons_fragment_says_why_on_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyreason_engine, "unavailable_reason", lambda: "")      # as if PyReason were here
    result = ops.reason(THRESHOLD)
    assert result.warning_card["code"] == ONE_ENGINE and "outside" in result.warning_card["message"]
    assert "threshold" in result.evidence[0]["outside_pyreason_fragment"][0]


def _fake_pyreason(monkeypatch: pytest.MonkeyPatch, shift: float) -> None:
    from g6.graph_reasoning import engine
    from g6.graph_reasoning.language import parse_model

    def solve(model):
        ours = engine.solve(model).bounds
        return ({a: (float(b[0]) + shift, float(b[1])) for a, b in ours.items()},
                [{"category": "stdout", "message": "planted", "count": 1}], 0.01)

    monkeypatch.setattr(pyreason_engine, "unavailable_reason", lambda: "")
    monkeypatch.setattr(pyreason_engine, "load", lambda: None)
    monkeypatch.setattr(pyreason_engine, "version", lambda: "planted")
    monkeypatch.setattr(pyreason_engine, "solve", solve)
    assert parse_model(SOCIAL)


def test_when_both_engines_agree_the_card_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_pyreason(monkeypatch, 0.0)
    result = ops.reason(SOCIAL, engine="pyreason")
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == ENGINE_ANSWER
    assert result.engines == ["pure_python", "pyreason"] and result.evidence[0]["agreement"]["agreed"] is True
    assert result.evidence[0]["pyreason_warnings"][0]["message"] == "planted"


def test_a_disagreement_beyond_the_tolerance_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_pyreason(monkeypatch, 1e-6)
    result = ops.reason(SOCIAL)
    assert result.completion_state == "blocked-escalated" and result.code == ENGINES_DISAGREE
    assert "friend(alice, bob): G6 [1, 1], PyReason [1.000001, 1]" in result.reason and result.interpretation == []
    assert len(result.evidence[0]["agreement"]["differences"]) == 7          # 3 friend edges and 4 popular nodes


def test_a_disagreement_within_the_tolerance_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_pyreason(monkeypatch, 1e-12)
    assert ops.reason(SOCIAL).warning_card["code"] == ENGINE_ANSWER


def test_an_interpretation_the_check_refutes_is_blocked(monkeypatch: pytest.MonkeyPatch, no_pyreason) -> None:
    monkeypatch.setattr(checks, "check_solution",
                        lambda *a, **k: checks.Check(checks.CHECKED, ("closure: planted",)))
    result = ops.reason(SOCIAL)
    assert result.code == CERTIFICATE_REFUTED and "planted" in result.reason and result.interpretation == []


def test_a_conflict_the_check_refutes_is_not_reported_as_a_conflict(monkeypatch: pytest.MonkeyPatch,
                                                                     no_pyreason) -> None:
    monkeypatch.setattr(checks, "check_conflict", lambda *a, **k: checks.Check(checks.CHECKED, ("support: planted",)))
    result = ops.reason(CONFLICTING)
    assert result.code == CERTIFICATE_REFUTED and "planted" in result.reason


def test_the_json_form_works_through_the_ops(no_pyreason) -> None:
    result = ops.query("popular(x) : [0.7, 1] <- friend(x, y), popular(y).", "popular(a)",
                       graph={"nodes": ["a", "b"], "edges": [["a", "b"]]},
                       facts=[{"label": "friend", "edge": ["a", "b"]}, {"label": "popular", "node": "b"}])
    assert result.answer["bounds"] == [0.7, 1.0]


def test_no_result_is_verified(no_pyreason) -> None:
    for result in (ops.reason(SOCIAL), ops.query(SOCIAL, "popular(bob)"), ops.explain(SOCIAL, "popular(bob)"),
                   ops.check_model(SOCIAL), ops.reason(CONFLICTING), ops.reason("p(")):
        assert result.completion_state in ("qualified-draft", "blocked-escalated")
