# SPDX-License-Identifier: Apache-2.0
"""Audit 030's falsification tests for the rule verdict (FEA-002/003/004/005/009/012, T03-T05, T08, T11, T14).

Words are tripwire signals: they can hold a goal as ``risky`` or downgrade it, never hard-block it. Structure (no
criteria, named context with nothing attached) is the only rule route to a block. Offline.
"""
from __future__ import annotations

import dataclasses

import pytest

from g6.intent_engine.schema import Assumption, IntentContract
from g6.reliability_pipeline.feasibility import (
    CLASSIFICATION_STRICTNESS,
    FeasibilityClassificationDecision,
    FeasibilityReport,
    at_least,
    deterministic_assess,
    deterministic_classify,
)

HARD = {"infeasible", "blocked_missing_access", "blocked_missing_context", "blocked_unclear_success"}


def _contract(goal: str, **fields) -> IntentContract:
    return IntentContract(goal=goal, success_criteria=fields.pop("success_criteria", ["done"]), **fields)


def _class(goal: str) -> str:
    return deterministic_classify(_contract(goal)).classification


def test_t03_a_password_word_is_a_risky_signal_not_an_access_block():
    found = deterministic_assess(_contract("Add a password strength meter to signup"))
    assert found.classification == "risky" and "access: password" in found.signals
    assert found.classification not in HARD


def test_t04_reproduction_is_not_production():
    assert _class("Write a reproduction script for the flaky test") == "clear"


def test_t05_a_negation_right_before_a_term_cancels_it_and_is_recorded():
    found = deterministic_classify(_contract("Do not delete anything; add a read-only report"))
    assert found.classification == "clear" and "negated: delete" in found.signals


def test_a_negation_further_back_does_not_cancel():
    """Control for T05: a negation that is not right before the term must not open a way around it."""
    assert _class("Do not hesitate to delete the production customer data") == "risky"


@pytest.mark.parametrize("goal", [
    "Add a test asserting it is impossible to submit an empty form",
    "Guarantee 100% uptime forever",
    "Rotate the private key and use production access",
    "Build a perpetual motion engine",
])
def test_no_word_chooses_a_hard_block(goal):
    assert _class(goal) not in HARD


@pytest.mark.parametrize(("goal", "signal"), [
    ("Fix check_password in auth.py", "access: password"),
    ("Rotate the private-key file", "access: private key"),
    ("Rotate the private_key file", "access: private key"),
    ("Mark the module safety-critical", "high stakes: safety critical"),
])
def test_separators_do_not_hide_a_term(goal, signal):
    found = deterministic_classify(_contract(goal))
    assert found.classification == "risky" and signal in found.signals


@pytest.mark.parametrize(("goal", "term"), [
    ("wipe the staging disk and drop table users", "wipe"),
    ("Drop table users on the staging database", "drop table"),
    ("Destroy the old backups", "destroy"),
    ("Purge the message queue", "purge"),
    ("Run rm -rf on the build folder", "rm rf"),
    ("Force-push the rewritten branch", "force push"),
])
def test_a_destructive_term_alone_makes_a_goal_risky(goal, term):
    found = deterministic_classify(_contract(goal))
    assert found.classification == "risky" and f"destructive: {term}" in found.signals


def test_a_destructive_term_right_after_a_negation_is_cancelled():
    """Control: the rule is the term, not any sentence near it."""
    found = deterministic_classify(_contract("Do not wipe anything; list the files in the staging folder"))
    assert found.classification == "clear" and "negated: wipe" in found.signals


def test_structure_still_blocks():
    assert deterministic_classify(_contract("Make it better", success_criteria=[])).classification == (
        "blocked_unclear_success")
    assert _class("Fix the bug in the attached document") == "blocked_missing_context"
    attached = deterministic_classify(_contract("Fix the bug in the attached document"), context_attached=True)
    assert attached.classification == "clear"


def test_a_stricter_signal_wins_over_a_scope_word():
    """The archive checked vague words first, so "maybe delete production" came out underspecified."""
    assert _class("Maybe delete the production records") == "risky"


def test_t14_a_launch_figure_and_deadline_are_unchecked_assumptions():
    found = deterministic_assess(_contract(
        "Launch a meal-kit subscription in Brisbane with $5k and break even in 3 months"))
    assert (found.classification, found.risk_level) != ("clear", "low")
    unchecked = {a.statement for a in found.assumptions if a.source == "request_figure"}
    assert any("'$5k'" in s for s in unchecked) and any("'in 3 months'" in s for s in unchecked), unchecked


def test_t14_control_no_time_bound_no_figure_assumption():
    found = deterministic_assess(_contract("Upgrade the parser to Python 3.12"))
    assert (found.classification, found.risk_level, found.assumptions) == ("clear", "low", [])


def test_deadline_and_cost_fields_are_read():
    found = deterministic_assess(_contract("Ship the onboarding page", deadline="Friday", cost_constraint="$300"))
    assert {a.statement for a in found.assumptions} == {
        "The time bound 'Friday' is the request's own; nothing here checked it.",
        "The figure '$300' is the request's own; nothing here checked it."}
    assert found.risk_level == "medium"


def test_t11_assumption_provenance_is_kept():
    contract = _contract("Add a CSV export", assumptions=[Assumption(statement="users want CSV", source="inferred",
                                                                     confidence=0.3)])
    kept, = deterministic_assess(contract).assumptions
    assert (kept.statement, kept.source, kept.confidence) == ("users want CSV", "inferred", 0.3)


def test_fea004_the_rules_state_no_confidence():
    assert deterministic_assess(_contract("Add a CSV export")).confidence_level is None
    assert deterministic_classify(_contract("Delete the production records")).confidence is None


def test_fea004_a_floored_answer_does_not_carry_the_overruled_confidence():
    floor = deterministic_assess(_contract("Delete the production payment records"))
    overruled = FeasibilityReport(classification="clear", confidence_level=0.99, risk_level="low")
    found = at_least(overruled, floor)
    assert found.classification == "risky" and found.confidence_level != 0.99 and found.source == "rule"


def test_an_assessor_cannot_drop_the_unchecked_figures():
    floor = deterministic_assess(_contract("Reach 400 customers by December"))
    stricter = FeasibilityReport(classification="too_large", risk_level="medium")
    found = at_least(stricter, floor)
    assert found.classification == "too_large"
    assert {a.source for a in found.assumptions} == {"request_figure"} and found.assumptions == floor.assumptions


def test_fea001_on_a_tie_the_floor_wins():
    floor = FeasibilityReport(classification="infeasible", risk_level="high", source="rule")
    lateral = FeasibilityReport(classification="blocked_missing_context", risk_level="high")
    assert CLASSIFICATION_STRICTNESS["infeasible"] == CLASSIFICATION_STRICTNESS["blocked_missing_context"]
    assert at_least(lateral, floor).classification == "infeasible"


def test_t08_a_decision_cannot_be_constructed_verified():
    with pytest.raises(TypeError):
        FeasibilityClassificationDecision(classification="clear", risk_level="low", completion_state="verified")
    found = deterministic_classify(_contract("Add a CSV export"))
    assert found.completion_state == "qualified-draft"
    with pytest.raises(dataclasses.FrozenInstanceError):
        found.completion_state = "verified"  # type: ignore[misc]


def test_fea012_no_template_decomposition():
    goal = "Add a CSV export button to the reports page and make the header row bold"
    assert deterministic_assess(_contract(goal)).suggested_decomposition == []


def test_fea012_a_long_concrete_goal_is_not_too_large_for_its_length():
    goal = " ".join(["Rename the helper in the reports module and update its three call sites"] * 4)
    assert len(goal.split()) > 35 and _class(goal) == "clear"
