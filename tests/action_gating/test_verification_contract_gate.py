# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Ballistic-action gate (WS-C.2): the action gate must not let a *material* action proceed
without a complete closed-loop verification plan (G6_Action_Logic.md G6-INV-05 / AT-06).

EMPIRICAL leg for WS-C.2 — the enforcement is real iff these pass. A declared contract is
checked for completeness; an absent one blocks the irreversible/external/real-world tier
(audit 003, AL-001; staged per Decision 2, lower material tiers get a notice). Each test isolates the ballistic gate (controlled LOW risk + satisfied
scenario/mitigation, no factual preconditions) so a different gate cannot confound the result.
"""
from __future__ import annotations

import pytest

from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ImpactAssessment,
    VerificationContractModel,
)

_BALLISTIC = "G6_E_BALLISTIC_ACTION_BLOCKED"


def _complete_contract() -> VerificationContractModel:
    return VerificationContractModel(
        expected_result="account is removed and the user is notified",
        observation_window="24h",
        leading_indicators=["deletion job started"],
        lagging_indicators=["no further logins"],
        monitor="oncall-identity",
        verification_time="T+24h",
        stop_threshold="any restore request",
        rollback_procedure="restore from soft-delete tombstone within 30 days",
        model_update_rule="if restore requested, revise the 'user wanted deletion' hypothesis",
    )


def _incomplete_contract() -> VerificationContractModel:
    # Has an expected result but no monitor, stop threshold, rollback, or model-update rule.
    return VerificationContractModel(expected_result="account removed", observation_window="24h")


def _material_capsule(contract: VerificationContractModel | None) -> ActionCapsule:
    return ActionCapsule(
        action_id="ag-material",
        action_type="account_deletion",
        description="Delete the customer account",
        user_visible_summary="Delete the customer account",
        external_side_effect=True,
        irreversible_or_costly_to_reverse=True,
        real_world_effect=True,
        reversibility="irreversible",
        blast_radius="account",
        created_at="2026-06-24T00:00:00+00:00",
        verification_contract=contract,
    )


def _local_reversible_capsule(contract: VerificationContractModel | None) -> ActionCapsule:
    return ActionCapsule(
        action_id="ag-local",
        action_type="write_file",
        description="Write a scratch file",
        user_visible_summary="Write a scratch file",
        reversibility="reversible",
        blast_radius="local",
        affected_systems=["local"],
        created_at="2026-06-24T00:00:00+00:00",
        verification_contract=contract,
    )


def _isolated_decide(capsule: ActionCapsule):
    """LOW risk + satisfied scenario/mitigation so the ballistic gate is the SOLE driver."""
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    mitigation = build_mitigation_plan(capsule, risk, scenario)
    return decide_gate(
        capsule, risk=risk, scenario_model=scenario, mitigation_plan=mitigation, audit_id="audit-test"
    )


# --------------------------------------------------------------------------- #
# AT-06 — ballistic-action prevention
# --------------------------------------------------------------------------- #

def test_at06_incomplete_contract_blocks_material_action() -> None:
    decision = _isolated_decide(_material_capsule(_incomplete_contract()))
    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert _BALLISTIC in decision.reason_codes

    # Control: the SAME material action with a COMPLETE contract is not blocked by this gate
    # (the raw-LLM delta — a raw model would emit the deletion with no plan to verify it).
    ok = _isolated_decide(_material_capsule(_complete_contract()))
    assert _BALLISTIC not in ok.reason_codes
    assert ok.decision != "block"


def test_at06_complete_contract_records_audit_evidence() -> None:
    decision = _isolated_decide(_material_capsule(_complete_contract()))
    items = [e for e in decision.evidence if e.get("kind") == "verification_contract"]
    assert items and items[0]["complete"] is True
    assert items[0]["missing_elements"] == []


def test_at06_block_evidence_lists_missing_elements() -> None:
    decision = _isolated_decide(_material_capsule(_incomplete_contract()))
    items = [e for e in decision.evidence if e.get("kind") == "verification_contract"]
    assert items and items[0]["complete"] is False
    missing = items[0]["missing_elements"]
    for element in ("monitor", "stop_threshold", "rollback_procedure", "model_update_rule"):
        assert element in missing


# --------------------------------------------------------------------------- #
# Opt-in + staging + backward compatibility
# --------------------------------------------------------------------------- #

def test_no_declared_contract_blocks_an_irreversible_action() -> None:
    # Audit 003, AL-001: this was opt-in, so declaring no contract let an irreversible action
    # through. "An action without a verification plan MUST be blocked" now holds for the R3 tier.
    decision = _isolated_decide(_material_capsule(None))
    assert decision.decision == "block"
    assert _BALLISTIC in decision.reason_codes
    items = [e for e in decision.evidence if e.get("kind") == "verification_contract"]
    assert items and items[0]["missing_elements"] == ["contract_absent"]


def test_non_material_action_is_out_of_scope() -> None:
    # A local, reversible action with an incomplete contract is NOT a material action, so the
    # closed-loop control does not apply (staged: the R3 tier fails closed, lower tiers do not).
    decision = _isolated_decide(_local_reversible_capsule(_incomplete_contract()))
    assert _BALLISTIC not in decision.reason_codes
    assert decision.decision != "block"


def test_ballistic_gate_never_downgrades_an_existing_block() -> None:
    # Withholding the scenario model on a material action still BLOCKS (SCENARIO_REQUIRED)
    # even with a complete contract — the ballistic gate is monotonic.
    cap = _material_capsule(_complete_contract())
    decision = decide_gate(
        cap, risk=ImpactAssessment(aggregate_risk="high"),
        scenario_model=None, mitigation_plan=None, audit_id="audit-test",
    )
    assert decision.decision == "block"
    assert "G6_E_SCENARIO_REQUIRED" in decision.reason_codes
    assert _BALLISTIC not in decision.reason_codes  # contract is complete, so it stays quiet


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #

def test_normalizer_accepts_dict_verification_contract() -> None:
    cap = normalize_action_capsule(
        {
            "action_type": "account_deletion",
            "verification_contract": {
                "expected_result": "removed",
                "observation_window": "24h",
                "leading_indicators": ["job started"],
                "monitor": "oncall",
                "verification_time": "T+24h",
                "stop_threshold": "restore request",
                "rollback_procedure": "tombstone restore",
                "model_update_rule": "revise on restore",
            },
        }
    )
    assert cap.verification_contract is not None
    assert cap.verification_contract.monitor == "oncall"


def test_normalizer_defaults_contract_to_none() -> None:
    cap = normalize_action_capsule({"action_type": "write_file", "target_resource": "scratch.txt"})
    assert cap.verification_contract is None


# --------------------------------------------------------------------------- #
# Property / invariant (formal-leg seed)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "flag", ["irreversible_or_costly_to_reverse", "external_side_effect", "real_world_effect"]
)
def test_property_incomplete_contract_always_blocks_material_actions(flag: str) -> None:
    """INVARIANT (fail-closed now): an incomplete verification plan ALWAYS blocks a material
    action, whichever materiality flag is set."""
    cap = ActionCapsule(
        action_id="ag-prop",
        action_type="op",
        description="d",
        user_visible_summary="d",
        reversibility="unknown",
        blast_radius="account",
        created_at="2026-06-24T00:00:00+00:00",
        verification_contract=_incomplete_contract(),
        **{flag: True},
    )
    decision = _isolated_decide(cap)
    assert decision.decision == "block"
    assert _BALLISTIC in decision.reason_codes
