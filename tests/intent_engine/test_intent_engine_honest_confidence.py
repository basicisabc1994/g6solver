# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.intent_engine.elicitor import IntentElicitor


def test_normal_elicitation_emits_bounded_confidence_with_method() -> None:
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

    assert session.contract.confidence_method == "field_completeness_heuristic"
    assert 0.0 <= session.contract.confidence <= 1.0


def test_junk_and_good_answers_never_emit_unmarked_confidence() -> None:
    elicitor = IntentElicitor()
    good = elicitor.elicit(
        "build export",
        answers={
            "why": "customers need monthly compliance reports",
            "success_criteria": ["CSV downloads with selected rows and a header"],
            "failure_criteria": ["wrong rows exported"],
            "non_goals": ["PDF export"],
            "risk_tolerance": "low",
        },
    )
    junk = elicitor.elicit(
        "build export",
        answers={
            "why": "x",
            "success_criteria": ["x"],
            "failure_criteria": ["x"],
            "non_goals": ["x"],
            "risk_tolerance": "low",
        },
    )

    assert good.contract.confidence_method == "field_completeness_heuristic"
    assert junk.contract.confidence_method == "field_completeness_heuristic"
    assert "confidence_method" in good.contract.model_dump()
    assert "confidence_method" in junk.contract.model_dump()
    # Audit 005 IE-002: a placeholder is not an answer, so junk no longer scores as a complete spec.
    assert junk.contract.confidence < elicitor.confidence_threshold <= good.contract.confidence
    assert [q.field for q in junk.questions] == ["why", "success_criteria", "failure_criteria", "non_goals"]
