# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest
from g6.intent_engine import Assumption, IntentContract, IntentElicitor, IntentSession
from pydantic import ValidationError

COMPLETE = {"why": "customers need reports", "success_criteria": ["CSV downloads with selected rows"],
            "failure_criteria": ["wrong rows exported"], "non_goals": ["PDF export"], "risk_tolerance": "low"}


def test_elicitor_asks_at_most_five_questions() -> None:
    session = IntentElicitor().elicit("make my app better")

    assert len(session.questions) <= 5
    assert {question.field for question in session.questions} >= {"why", "success_criteria"}
    assert session.contract.goal == "make my app better"


def test_elicitor_builds_contract_from_answers() -> None:
    session = IntentElicitor().elicit(
        "build export",
        answers={
            "why": "customers need reports",
            "success_criteria": ["CSV downloads with selected rows"],
            "failure_criteria": ["wrong rows exported"],
            "non_goals": ["PDF export"],
            "risk_tolerance": "low",
        },
    )

    assert session.contract.confidence >= 0.8
    assert session.questions == []


def test_declined_clarification_labels_assumption() -> None:
    session = IntentElicitor().elicit("build me an app", user_declined=True)

    assert session.ended_by == "declined"
    assert session.contract.assumptions[0].source == "fallback"


def test_placeholder_answers_leave_their_questions_open() -> None:
    """Audit 005 IE-002: field presence is not completeness; "x", "TBD", "n/a" and "..." answer nothing."""
    elicitor = IntentElicitor()
    for junk in ("x", "TBD", "n/a", "...", "xxx", "  ?  "):
        session = elicitor.elicit("build export", answers={**COMPLETE, "why": junk, "success_criteria": [junk],
                                                            "failure_criteria": [junk], "non_goals": [junk]})
        assert [q.field for q in session.questions] == ["why", "success_criteria", "failure_criteria", "non_goals"], junk
        assert session.contract.confidence < elicitor.confidence_threshold
        assert session.contract.success_criteria == [junk.strip()]  # the words are kept, only not counted
    assert elicitor.elicit("build export", answers=COMPLETE).questions == []  # control
    mixed = elicitor.elicit("build export", answers={**COMPLETE, "success_criteria": ["x", "CSV downloads"]})
    assert mixed.questions == []  # one real criterion beside a placeholder answers the question


def test_a_goal_answer_that_replaces_the_request_is_recorded() -> None:
    """Audit 005 IE-007 (T-10): the request is never replaced silently, and the session id covers the answers."""
    elicitor = IntentElicitor()
    session = elicitor.elicit("delete the prod db", answers={"goal": "write a haiku"})
    assert session.contract.goal == "write a haiku"
    assert any(a.source == "explicit" and "delete the prod db" in a.statement and "write a haiku" in a.statement
               for a in session.contract.assumptions)
    same = elicitor.elicit("delete the prod db", answers={"goal": "delete the prod db"})
    assert not any("replaced the request" in a.statement for a in same.contract.assumptions)  # control: no change
    assert session.session_id != elicitor.elicit("delete the prod db").session_id
    assert session.session_id == elicitor.elicit("delete the prod db", answers={"goal": "write a haiku"}).session_id


def test_ended_by_says_only_what_happened() -> None:
    """Audit 005 IE-008: below the threshold a session is "incomplete", never "confidence"; dead reasons are gone."""
    elicitor = IntentElicitor()
    partial = elicitor.elicit("build export", answers={"why": "customers need reports"})
    assert partial.contract.confidence < elicitor.confidence_threshold and partial.ended_by == "incomplete"
    assert elicitor.elicit("build export", answers=COMPLETE).ended_by == "confidence"
    assert elicitor.elicit("build export").ended_by == "question_budget"
    for dead in ("accepted", "idle_timeout"):
        with pytest.raises(ValidationError):
            IntentSession(session_id="s", initial_request="r", contract=IntentContract(goal="r"), ended_by=dead)


@pytest.mark.parametrize("bad", [7.0, -0.1, 1.0001, math.nan])
def test_confidence_is_bounded_on_every_path(bad) -> None:
    """Audit 005 IE-009 (T-02): a contract or assumption confidence outside [0, 1] fails validation."""
    with pytest.raises(ValidationError):
        IntentContract(goal="g", confidence=bad)
    with pytest.raises(ValidationError):
        Assumption(statement="s", confidence=bad)
    with pytest.raises(ValidationError):  # a chat-supplied contract (mission_accept) is validated the same way
        IntentContract.model_validate({"goal": "g", "assumptions": [
            {"statement": "s", "source": "explicit", "confidence": bad}]})
    assert IntentContract(goal="g", confidence=1.0).confidence == 1.0  # control: the bounds themselves are allowed
    assert Assumption(statement="s", confidence=0.0).confidence == 0.0
