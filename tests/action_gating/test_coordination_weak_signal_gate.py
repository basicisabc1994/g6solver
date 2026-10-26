# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Coordination + weak-signal gates (P3.1): the action gate must not let a *material* action
proceed when it collides with a declared live/scheduled action (G6-INV-08 / AT-05) or when it
ignores a deteriorating signal / evaluates no future horizon (§9 / AT-01).

EMPIRICAL leg for the P3.1 live-wiring of the Dörner coordination and weak-signal controls into
``mvp.action_gating.decide_gate``. Both gates read a DECLARED context (``coordination_context``
/ ``world_state``); without it an irreversible action goes to review (audit 003, AL-002).
Each test isolates one gate (LOW risk + satisfied scenario/mitigation, a complete verification
contract) so a different gate cannot confound the result.
"""
from __future__ import annotations

import pytest

from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ActionIntentModel,
    ControlVariableModel,
    CoordinationContextModel,
    ImpactAssessment,
    VerificationContractModel,
    WorldStateModel,
)

_COLLISION = "G6_E_ACTION_COLLISION"
_NO_HORIZON = "G6_E_NO_FUTURE_HORIZON"
_WEAK_SIGNAL = "G6_E_UNADDRESSED_WEAK_SIGNAL"

_COMPLETE_CONTRACT = VerificationContractModel(
    expected_result="account is removed", observation_window="24h",
    leading_indicators=["deletion job started"], lagging_indicators=["no further logins"],
    monitor="oncall-identity", verification_time="T+24h", stop_threshold="any restore request",
    rollback_procedure="restore from the soft-delete tombstone", model_update_rule="revise on restore",
)


def _material_capsule(**extra) -> ActionCapsule:
    """A material (irreversible/external/real-world) action with a COMPLETE verification contract,
    so the ballistic gate stays silent (audit 003, AL-001: an absent contract now blocks) and the
    coordination/weak-signal gate is the sole driver."""
    extra.setdefault("verification_contract", _COMPLETE_CONTRACT)
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
        created_at="2026-06-25T00:00:00+00:00",
        **extra,
    )


def _local_reversible_capsule(**extra) -> ActionCapsule:
    return ActionCapsule(
        action_id="ag-local",
        action_type="write_file",
        description="Write a scratch file",
        user_visible_summary="Write a scratch file",
        reversibility="reversible",
        blast_radius="local",
        affected_systems=["local"],
        created_at="2026-06-25T00:00:00+00:00",
        **extra,
    )


def _isolated_decide(capsule: ActionCapsule):
    """LOW risk + satisfied scenario/mitigation so the new gate is the SOLE driver."""
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    mitigation = build_mitigation_plan(capsule, risk, scenario)
    return decide_gate(
        capsule, risk=risk, scenario_model=scenario, mitigation_plan=mitigation, audit_id="audit-test"
    )


def _colliding_context() -> CoordinationContextModel:
    # Shared resource "db" in the same time window -> resource_contention.
    return CoordinationContextModel(
        proposed=ActionIntentModel(action_id="a", target_resource="x",
                                   shared_resources=["db"], window="w1"),
        active=[ActionIntentModel(action_id="b", shared_resources=["db"], window="w1")],
    )


def _clean_context() -> CoordinationContextModel:
    return CoordinationContextModel(
        proposed=ActionIntentModel(action_id="a", target_resource="x",
                                   shared_resources=["db"], window="w1"),
        active=[ActionIntentModel(action_id="b", shared_resources=["cache"], window="w2")],
    )


def _deteriorating_world(*, acknowledged: bool) -> WorldStateModel:
    return WorldStateModel(
        variables=[ControlVariableModel(name="load", value=8, threshold=10, trend="rising",
                                        acknowledged=acknowledged)],
        horizons_evaluated=["next hour"],
    )


# --------------------------------------------------------------------------- #
# AT-05 — action-collision prevention
# --------------------------------------------------------------------------- #

def test_at05_collision_blocks_material_action() -> None:
    decision = _isolated_decide(_material_capsule(coordination_context=_colliding_context()))
    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert _COLLISION in decision.reason_codes

    # Control: the SAME action with non-overlapping resources/windows is not blocked by this gate
    # (the raw-LLM delta — a raw model ignores the other actions already in flight).
    ok = _isolated_decide(_material_capsule(coordination_context=_clean_context()))
    assert _COLLISION not in ok.reason_codes
    assert ok.decision != "block"


def test_at05_collision_records_conflict_evidence() -> None:
    decision = _isolated_decide(_material_capsule(coordination_context=_colliding_context()))
    items = [e for e in decision.evidence if e.get("kind") == "resource_contention"]
    assert items and items[0]["with_action"] == "b"


def test_at05_collision_on_non_material_action_is_a_soft_notice() -> None:
    # A local reversible action that collides is NOT material, so the collision is a soft notice,
    # not a block (staged: the material tier fails closed, lower tiers do not).
    decision = _isolated_decide(_local_reversible_capsule(coordination_context=_colliding_context()))
    assert decision.decision != "block"
    assert _COLLISION in decision.reason_codes


# --------------------------------------------------------------------------- #
# AT-01 — weak-signal / future-horizon review
# --------------------------------------------------------------------------- #

def test_at01_unacknowledged_deterioration_blocks_material_action() -> None:
    decision = _isolated_decide(_material_capsule(world_state=_deteriorating_world(acknowledged=False)))
    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert _WEAK_SIGNAL in decision.reason_codes

    # Control: an ACKNOWLEDGED deteriorating signal (with a horizon evaluated) is a notice, not a block.
    ok = _isolated_decide(_material_capsule(world_state=_deteriorating_world(acknowledged=True)))
    assert _WEAK_SIGNAL not in ok.reason_codes
    assert _NO_HORIZON not in ok.reason_codes
    assert ok.decision != "block"


def test_at01_no_future_horizon_blocks_material_action() -> None:
    # A declared world state that evaluates no future horizon blocks a material action.
    decision = _isolated_decide(_material_capsule(world_state=WorldStateModel()))
    assert decision.decision == "block"
    assert _NO_HORIZON in decision.reason_codes


def test_at01_weak_signal_records_evidence() -> None:
    decision = _isolated_decide(_material_capsule(world_state=_deteriorating_world(acknowledged=False)))
    items = [e for e in decision.evidence if e.get("kind") == "weak_signal"]
    assert items and items[0]["variable"] == "load" and items[0]["acknowledged"] is False


# --------------------------------------------------------------------------- #
# Opt-in + staging + backward compatibility + monotonicity
# --------------------------------------------------------------------------- #

def test_no_declared_context_sends_an_irreversible_action_to_review() -> None:
    # Audit 003, AL-002: both gates were opt-in, so declaring neither context let an irreversible
    # action through with a notice. Neither check can run, so a person must review it.
    decision = _isolated_decide(_material_capsule())
    assert _COLLISION not in decision.reason_codes
    assert _WEAK_SIGNAL not in decision.reason_codes
    assert _NO_HORIZON not in decision.reason_codes
    assert decision.decision == "require_human_review"
    assert "G6_E_CONTROL_NOT_EVALUATED" in decision.reason_codes


def test_notice_never_downgrades_an_existing_block() -> None:
    # Withholding the scenario model on a material action BLOCKS (SCENARIO_REQUIRED). An acknowledged
    # weak signal (a notice) must not relax that block — the gate is monotonic.
    cap = _material_capsule(world_state=_deteriorating_world(acknowledged=True))
    decision = decide_gate(
        cap, risk=ImpactAssessment(aggregate_risk="high"),
        scenario_model=None, mitigation_plan=None, audit_id="audit-test",
    )
    assert decision.decision == "block"
    assert "G6_E_SCENARIO_REQUIRED" in decision.reason_codes


# --------------------------------------------------------------------------- #
# Normalizer
# --------------------------------------------------------------------------- #

def test_normalizer_accepts_dict_contexts() -> None:
    cap = normalize_action_capsule(
        {
            "action_type": "account_deletion",
            "coordination_context": {
                "proposed": {"action_id": "a", "shared_resources": ["db"], "window": "w1"},
                "active": [{"action_id": "b", "shared_resources": ["db"], "window": "w1"}],
            },
            "world_state": {
                "variables": [{"name": "load", "value": 8, "threshold": 10, "trend": "rising"}],
                "horizons_evaluated": ["next hour"],
            },
        }
    )
    assert cap.coordination_context is not None
    assert cap.coordination_context.active[0].action_id == "b"
    assert cap.world_state is not None and cap.world_state.variables[0].name == "load"


def test_normalizer_defaults_contexts_to_none() -> None:
    cap = normalize_action_capsule({"action_type": "write_file", "target_resource": "scratch.txt"})
    assert cap.coordination_context is None
    assert cap.world_state is None


# --------------------------------------------------------------------------- #
# Property / invariant (formal-leg seed)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "flag", ["irreversible_or_costly_to_reverse", "external_side_effect", "real_world_effect"]
)
def test_property_unacknowledged_deterioration_always_blocks_material_actions(flag: str) -> None:
    """INVARIANT: an unacknowledged deteriorating signal ALWAYS blocks a material action,
    whichever materiality flag is set."""
    cap = ActionCapsule(
        action_id="ag-prop",
        action_type="op",
        description="d",
        user_visible_summary="d",
        reversibility="unknown",
        blast_radius="account",
        created_at="2026-06-25T00:00:00+00:00",
        world_state=_deteriorating_world(acknowledged=False),
        **{flag: True},
    )
    decision = _isolated_decide(cap)
    assert decision.decision == "block"
    assert _WEAK_SIGNAL in decision.reason_codes
