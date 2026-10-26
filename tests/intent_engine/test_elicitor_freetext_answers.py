# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""U2 live-demo regression (2026-06-12): user-typed answers must NEVER crash.

The clarify gate's unlock path feeds the user's FREE-TEXT answers back into
``IntentElicitor.elicit``. Two IntentContract fields are typed Literals
(``risk_tolerance``, ``user_expertise_level``); the elicitor passed the raw
answer through, so "Keep it to one page; my team is just me" crashed the
ENTIRE pipeline with a pydantic literal_error (observed live, run
draft-...-r2). Contract pinned here:

- exact/keyword answers coerce onto the literal ("Low risk is fine" → low);
- an unparseable answer falls back to the prior/unknown value AND is
  recorded as an explicit Assumption — the user's words are never silently
  dropped, and elicit() never raises;
- the answer still counts toward confidence (the gate must not re-fire
  forever on a free-text answer).
"""
from __future__ import annotations

from g6.intent_engine import IntentElicitor


def test_freetext_risk_tolerance_never_crashes() -> None:
    elicitor = IntentElicitor()

    session = elicitor.elicit(
        "Draft a one-page launch checklist for my note-taking app.",
        answers={
            "why": "Small business owners need it",
            "failure_criteria": "No subscribers after 90 days",
            "non_goals": "No paid ads",
            "risk_tolerance": "Keep it to one page; my team is just me and a part-time marketer",
            "success_criteria": ["100 paying subscribers in 90 days"],
        },
    )

    contract = session.contract
    assert contract.risk_tolerance == "unknown"  # honest fallback, no crash
    # The user's words survive as an explicit assumption.
    assert any(
        "part-time marketer" in assumption.statement and assumption.source == "explicit"
        for assumption in contract.assumptions
    )


def test_keyword_risk_answers_coerce() -> None:
    elicitor = IntentElicitor()

    low = elicitor.elicit("task", answers={"risk_tolerance": "Low risk is fine"})
    high = elicitor.elicit("task", answers={"risk_tolerance": "HIGH"})
    medium = elicitor.elicit("task", answers={"risk_tolerance": "medium, I guess"})

    assert low.contract.risk_tolerance == "low"
    assert high.contract.risk_tolerance == "high"
    assert medium.contract.risk_tolerance == "medium"


def test_negated_or_ranged_risk_answers_do_not_coerce() -> None:
    """Audit 005 IE-004: naming a level is not choosing it."""
    elicitor = IntentElicitor()
    for answer in ("anything but high risk", "not high", "low to medium", "I don't want high risk"):
        contract = elicitor.elicit("task", answers={"risk_tolerance": answer}).contract
        assert contract.risk_tolerance == "unknown", answer
        assert any(answer in a.statement and a.source == "explicit" for a in contract.assumptions)


def test_fields_carried_from_prior_count_toward_confidence() -> None:
    """Audit 005 IE-005: re-eliciting over a complete prior contract must not lose its completeness."""
    elicitor = IntentElicitor()
    full = elicitor.elicit("task", answers={"why": "the team asked", "success_criteria": ["it ships"],
                                            "failure_criteria": ["it breaks"], "non_goals": ["a rewrite"],
                                            "risk_tolerance": "low"}).contract
    again = elicitor.elicit("task", answers={"success_criteria": ["it ships today"]}, prior=full).contract
    assert again.confidence == full.confidence >= elicitor.confidence_threshold


def test_decline_does_not_claim_inferred_criteria() -> None:
    """Audit 005 IE-006: a decline leaves no criteria, and the note must say so."""
    contract = IntentElicitor().elicit("task", user_declined=True).contract
    assert contract.success_criteria == []
    assert not any("inferred from" in a.statement for a in contract.assumptions)


def test_freetext_expertise_never_crashes() -> None:
    elicitor = IntentElicitor()

    session = elicitor.elicit(
        "task",
        answers={"user_expertise_level": "I have run two startups before"},
    )

    assert session.contract.user_expertise_level == "unknown"


def test_freetext_answer_still_counts_toward_confidence() -> None:
    elicitor = IntentElicitor()

    answered = elicitor.elicit(
        "Draft a launch checklist.",
        answers={
            "why": "the launch is next week",
            "failure_criteria": "a missed step",
            "non_goals": "the press release",
            "risk_tolerance": "free text that cannot coerce",
            "success_criteria": ["one"],
        },
    )
    unanswered = elicitor.elicit("Draft a launch checklist.")

    # All five answer slots present → confidence clears the gate threshold
    # even though the literal stayed at its fallback.
    assert answered.contract.confidence > unanswered.contract.confidence
    assert answered.contract.confidence >= elicitor.confidence_threshold
