# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Hypothesis-discipline reflection gate (A6a / action_logic F.2): the action gate must pause an
action whose in-use hypothesis carries unresolved disconfirming evidence (AT-08 -> review) and
block an action relying on a FALSIFIED hypothesis without a valid, evidence-backed replacement
(AT-12 -> block). G6-INV-04/09, §11.

This wires a control that ships present-but-dormant. Because activating it introduces a new
``hypothesis_context`` capsule field, it is gated behind the default-OFF
``G6_ACTION_LOGIC_HYPOTHESIS_GATE`` flag: with the flag unset ``decide_gate`` is byte-identical
(doubly inert until a caller declares ``hypothesis_context``). Each test isolates this gate
(LOW risk + satisfied scenario/mitigation, no other declared context) so a different gate cannot
confound the result (the other contexts are supplied and satisfied).
"""
from __future__ import annotations

import pytest

from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ActionIntentModel,
    CoordinationContextModel,
    HypothesisContextModel,
    HypothesisModel,
    ImpactAssessment,
    VerificationContractModel,
    WorldStateModel,
)

_DISCONFIRMING = "G6_E_DISCONFIRMING_EVIDENCE_PENDING"
_FALSIFIED_REUSED = "G6_E_FALSIFIED_HYPOTHESIS_REUSED"
_FLAG = "G6_ACTION_LOGIC_HYPOTHESIS_GATE"

# Satisfied verification, coordination and world contexts: an irreversible action that omits
# them is blocked or sent to review (audit 003, AL-001/AL-002), which would mask this gate.
_SATISFIED_CONTEXT = {
    "verification_contract": VerificationContractModel(
        expected_result="account is removed", observation_window="24h",
        leading_indicators=["deletion job started"], lagging_indicators=["no further logins"],
        monitor="oncall-identity", verification_time="T+24h", stop_threshold="any restore request",
        rollback_procedure="restore from the soft-delete tombstone", model_update_rule="revise on restore",
    ),
    "coordination_context": CoordinationContextModel(proposed=ActionIntentModel(action_id="ag-hyp")),
    "world_state": WorldStateModel(horizons_evaluated=["next week"]),
}


@pytest.fixture(autouse=True)
def _flag_off_by_default(monkeypatch):
    """Guarantee a known baseline: the gate is OFF unless a test opts in."""
    monkeypatch.delenv(_FLAG, raising=False)


def _material_capsule(**extra) -> ActionCapsule:
    """A material action whose verification/coordination/world contexts are satisfied, so only the
    hypothesis gate can drive the decision."""
    extra = {**_SATISFIED_CONTEXT, **extra}
    return ActionCapsule(
        action_id="ag-hyp",
        action_type="account_deletion",
        description="Delete the customer account",
        user_visible_summary="Delete the customer account",
        external_side_effect=True,
        irreversible_or_costly_to_reverse=True,
        real_world_effect=True,
        reversibility="irreversible",
        blast_radius="account",
        created_at="2026-06-25T00:00:00+00:00",
        **extra,
    )


def _isolated_decide(capsule: ActionCapsule):
    """LOW risk + satisfied scenario/mitigation so the hypothesis gate is the SOLE driver."""
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    mitigation = build_mitigation_plan(capsule, risk, scenario)
    return decide_gate(
        capsule, risk=risk, scenario_model=scenario, mitigation_plan=mitigation, audit_id="audit-test"
    )


def _falsified_relied(**over) -> HypothesisContextModel:
    return HypothesisContextModel(
        relied_hypothesis=HypothesisModel(
            hypothesis_id="h-falsified",
            statement="Restart clears the queue backlog",
            confidence=0.1,
            falsification_condition="backlog persists after restart",
            status="falsified",
        ),
        **over,
    )


def _valid_replacement() -> HypothesisModel:
    return HypothesisModel(
        hypothesis_id="h-new",
        statement="Draining the queue first clears the backlog",
        confidence=0.6,
        supporting_evidence=["drain reduced backlog 90% in staging"],
        falsification_condition="backlog persists after drain",
        status="active",
    )


def _disconfirmed_active() -> HypothesisContextModel:
    return HypothesisContextModel(
        active_hypotheses=[
            HypothesisModel(
                hypothesis_id="h-active",
                statement="The nightly job is idempotent",
                confidence=0.5,
                disconfirming_evidence=["double-charge observed on retry"],
                falsification_condition="a retry produces a second charge",
                status="active",
            )
        ]
    )


# --------------------------------------------------------------------------- #
# Default-OFF: byte-identical / revert-fail
# --------------------------------------------------------------------------- #

def test_flag_off_falsified_reuse_is_not_blocked() -> None:
    """Revert-fail: with the flag unset, even a declared FALSIFIED relied hypothesis does NOT
    block — the wire is byte-identical to today. (If the gate were unconditionally wired, this
    would fail.)"""
    decision = _isolated_decide(_material_capsule(hypothesis_context=_falsified_relied()))
    assert _FALSIFIED_REUSED not in decision.reason_codes
    assert decision.decision != "block"


def test_flag_off_disconfirming_evidence_is_not_reviewed() -> None:
    decision = _isolated_decide(_material_capsule(hypothesis_context=_disconfirmed_active()))
    assert _DISCONFIRMING not in decision.reason_codes


def test_flag_falsey_value_stays_off(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "0")
    decision = _isolated_decide(_material_capsule(hypothesis_context=_falsified_relied()))
    assert _FALSIFIED_REUSED not in decision.reason_codes
    assert decision.decision != "block"


# --------------------------------------------------------------------------- #
# AT-12 — reauthorisation block on FALSIFIED-hypothesis reuse
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("truthy", ["1", "true", "yes", "TRUE"])
def test_at12_falsified_reuse_blocks_when_enabled(monkeypatch, truthy) -> None:
    monkeypatch.setenv(_FLAG, truthy)
    decision = _isolated_decide(_material_capsule(hypothesis_context=_falsified_relied()))
    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert _FALSIFIED_REUSED in decision.reason_codes


def test_at12_valid_replacement_is_accepted(monkeypatch) -> None:
    """Non-vacuity: the gate is not a blanket block — a NEW well-formed, evidence-backed
    replacement hypothesis clears the reauthorisation floor."""
    monkeypatch.setenv(_FLAG, "1")
    ctx = _falsified_relied(replacement_hypothesis=_valid_replacement())
    decision = _isolated_decide(_material_capsule(hypothesis_context=ctx))
    assert _FALSIFIED_REUSED not in decision.reason_codes
    assert decision.decision != "block"
    items = [e for e in decision.evidence if e.get("kind") == "reauthorisation"]
    assert items and items[0]["replacement_accepted"] is True


def test_at12_records_reauthorisation_evidence(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    decision = _isolated_decide(_material_capsule(hypothesis_context=_falsified_relied()))
    items = [e for e in decision.evidence if e.get("kind") == "reauthorisation"]
    assert items and items[0]["relied_hypothesis_id"] == "h-falsified"
    assert items[0]["replacement_accepted"] is False


# --------------------------------------------------------------------------- #
# AT-08 — disconfirming-evidence review
# --------------------------------------------------------------------------- #

def test_at08_disconfirming_evidence_triggers_review(monkeypatch) -> None:
    monkeypatch.setenv(_FLAG, "1")
    decision = _isolated_decide(_material_capsule(hypothesis_context=_disconfirmed_active()))
    assert _DISCONFIRMING in decision.reason_codes
    assert decision.decision == "require_human_review"
    assert decision.completion_state == "qualified-draft"
    items = [e for e in decision.evidence if e.get("kind") == "hypothesis"]
    assert items and items[0]["hypothesis_id"] == "h-active"


# --------------------------------------------------------------------------- #
# Opt-in + backward compatibility + monotonicity
# --------------------------------------------------------------------------- #

def test_enabled_but_no_declared_context_is_inert(monkeypatch) -> None:
    """Doubly inert: even with the flag ON, an action that declares no hypothesis_context is not
    touched by this gate."""
    monkeypatch.setenv(_FLAG, "1")
    decision = _isolated_decide(_material_capsule())
    assert _FALSIFIED_REUSED not in decision.reason_codes
    assert _DISCONFIRMING not in decision.reason_codes
    assert decision.decision != "block"


def test_review_never_downgrades_an_existing_block(monkeypatch) -> None:
    """Monotonic: a hypothesis review must not relax a pre-existing block. Withholding the
    scenario model on a material action BLOCKS (SCENARIO_REQUIRED); a disconfirming-evidence
    review stays a block."""
    monkeypatch.setenv(_FLAG, "1")
    cap = _material_capsule(hypothesis_context=_disconfirmed_active())
    decision = decide_gate(
        cap, risk=ImpactAssessment(aggregate_risk="high"),
        scenario_model=None, mitigation_plan=None, audit_id="audit-test",
    )
    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert "G6_E_SCENARIO_REQUIRED" in decision.reason_codes
