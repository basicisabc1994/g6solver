# SPDX-License-Identifier: Apache-2.0
"""Bounded, model-free readiness and its opt-in flag (PMC-01/02/04/12/22)."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest
from g6.reliability_pipeline.intake import REPLAY_ANSWER, REPLAY_REQUIREMENT
from g6.workforce import service


@pytest.mark.parametrize("value,on", [  # founder, 2026-10-15: on by default for every plan
    (None, True), ("", True), ("1", True), ("true", True), ("yes", True), ("yes please", True),
    ("0", False), ("false", False), ("no", False), ("off", False), (" OFF ", False),
])
def test_readiness_flag_is_on_unless_a_plain_no(value, on):
    assert getattr(service, "ENV_READINESS", None) == "G6_WORKFORCE_READINESS"
    assert service.readiness_enabled({} if value is None else {service.ENV_READINESS: value}) is on


def modules():
    from g6.reliability_pipeline import failure_modes
    from g6.workforce import readiness
    return failure_modes, readiness


def test_skill_is_frozen_versioned_unmeasured_and_has_five_distinct_choices():
    fm, _ = modules()
    skill, = fm.SKILLS
    assert skill.failure_id == "event-replay-semantics.v1"
    assert skill.family == "intent_substitution" and skill.version and skill.owner
    assert skill.observed_performance == "unmeasured" and not skill.retired
    assert len(skill.interpretations) == 5
    options = {i.key: i for i in skill.interpretations}
    assert options[REPLAY_ANSWER].requirement == REPLAY_REQUIREMENT
    assert options[REPLAY_ANSWER].relation == "event-replay-v1"
    assert options["adjacent_duplicate"].relation == "adjacent-replay.v1"
    for key in ("concurrent_retry", "restart_persistence", "external_side_effect"):
        assert options[key].relation is None and options[key].missing_capability
    with pytest.raises(FrozenInstanceError):
        skill.retired = True
    for trigger in skill.triggers:
        assert fm.relevant(f"Handle {trigger.upper()} safely") == (skill,)
    assert fm.relevant("Fix a label, not a retrying label") == ()


def test_irrelevant_skill_takes_fast_path_with_disclosed_scope():
    _, rd = modules()
    result = rd.assess("Fix the label beside the acceptance rule and preserve its behavior.",
                       {"module": "policy.py", "function": "label"})
    assert result.decision == "fast_path" and result.question is None
    assert result.skills_consulted == () and result.version
    assert result.understanding["module"] == "policy.py"
    assert result.understanding["function"] == "label"
    assert result.understanding["forbidden_effects"] == "no test edits, no network, only the named module may change"


def test_ambiguous_webhook_asks_one_discriminating_question():
    fm, rd = modules()
    result = rd.assess("Make my payment webhook idempotent.", {})
    assert result.decision == "ask"
    question = result.question
    assert question["skill_id"] == fm.SKILLS[0].failure_id
    assert [o["key"] for o in question["options"]] == [i.key for i in fm.SKILLS[0].interpretations] + ["not_applicable"]
    for heading in ("My understanding", "What could go wrong", "What I will check", "One decision I need", "Scope I cannot verify"):
        assert heading in question["question"]
    for option in question["options"][:-1]:
        assert "adds the check" in option["effect"] or "cannot check:" in option["effect"]
    assert "qualified-draft" in question["options"][-2]["effect"]


def test_http_retry_question_ends_with_no_check_escape_option():
    _, rd = modules()
    result = rd.assess("Add retries to the HTTP fetch helper", {})
    assert result.decision == "ask"
    assert result.question["options"][-1] == {
        "key": "not_applicable", "label": "This is not about repeated events",
        "effect": "adds no check; the work proceeds on the existing tests",
    }


def test_question_preview_uses_the_consulted_skills_scope(monkeypatch):
    fm, rd = modules()
    skill = SimpleNamespace(**{**vars(fm.SKILLS[0]),
        "will_check": "What I will check: the selected boundary conditions.",
        "decision": "One decision I need: which boundary matters?",
        "cannot_verify": "Scope I cannot verify: unspecified boundaries."})
    monkeypatch.setattr(fm, "SKILLS", (skill,))
    question = rd.assess("Handle retries safely", {}).question["question"]
    assert "What I will check: the selected boundary conditions.\n" in question
    assert "One decision I need: which boundary matters?\n" in question
    assert "Scope I cannot verify: unspecified boundaries. " in question
    assert "selected available replay relation" not in question


def test_explicit_requirements_select_the_interpretation_without_a_question():
    fm, rd = modules()
    for option in fm.SKILLS[0].interpretations:
        result = rd.assess(option.requirement, {})
        assert result.decision == "proceed_explicit" and result.question is None
        assert result.interpretation == option
    assert rd.assess(REPLAY_REQUIREMENT.replace("Python event handler", "inventory handler"), {}).interpretation.key == REPLAY_ANSWER


def test_same_standard_choices_are_not_asked(monkeypatch):
    fm, rd = modules()
    skill = fm.SKILLS[0]
    first = skill.interpretations[0]
    synthetic = replace(skill, interpretations=(first, replace(first, key="same", label="Same guarantee")))
    monkeypatch.setattr(fm, "SKILLS", (synthetic,))
    result = rd.assess("Make the handler idempotent.", {})
    assert result.decision == "fast_path" and result.question is None
    assert result.not_asked == ((skill.failure_id, "cannot_change_the_outcome"),)


def test_a_retired_skill_is_never_consulted(monkeypatch):
    fm, rd = modules()
    monkeypatch.setattr(fm, "SKILLS", (replace(fm.SKILLS[0], retired=True),))
    assert fm.relevant("Make my payment webhook idempotent.") == ()
    result = rd.assess("Make my payment webhook idempotent.", {})
    assert result.decision == "fast_path" and result.skills_consulted == ()


def test_a_not_applicable_answer_from_the_chat_is_recorded_as_the_chats():
    _, rd = modules()
    events = [{"kind": "message", "evidence_id": "e1", "detail": {"from": "chat", "to": "intake", "refs": {
        "answer_actor": "chat", "answer": {"replay_semantics": "not_applicable"}}}}]
    found = rd.state(SimpleNamespace(events=lambda mission_id: events), "m1")
    assert found.interpretation == {"key": "not_applicable", "source": "chat_answer"}


def test_field_completeness_does_not_resolve_semantics():
    from g6.intent_engine import IntentElicitor
    _, rd = modules()
    task = "Make this Python event handler idempotent."
    elicitor = IntentElicitor()
    session = elicitor.elicit(task, answers={"why": task, "success_criteria": ["tests pass"],
        "failure_criteria": ["tests fail"], "non_goals": ["test edits"], "constraints": ["no network"],
        "risk_tolerance": "medium"})
    assert session.contract.confidence >= elicitor.confidence_threshold
    assert rd.assess(task, {}).decision == "ask"
