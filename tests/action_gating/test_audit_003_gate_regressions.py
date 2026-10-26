# SPDX-License-Identifier: Apache-2.0
"""Audit 003 (action_logic) findings that live in the action gate: AL-001 and AL-002.

Each test isolates the missing-context policy: a supplied scenario and mitigation, so no other gate decides the
outcome. The supplied LOW risk cannot lower the gate's own (review B1-2), so an R3 action still meets the authority
rule, a person.
"""
from __future__ import annotations

from pathlib import Path

from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ActionIntentModel,
    CoordinationContextModel,
    ImpactAssessment,
    VerificationContractModel,
    WorldStateModel,
)

_BALLISTIC = "G6_E_BALLISTIC_ACTION_BLOCKED"
_NOT_EVALUATED = "G6_E_CONTROL_NOT_EVALUATED"

_COMPLETE = VerificationContractModel(
    expected_result="account is removed", observation_window="24h",
    leading_indicators=["deletion job started"], lagging_indicators=["no further logins"],
    monitor="oncall-identity", verification_time="T+24h", stop_threshold="any restore request",
    rollback_procedure="restore from the soft-delete tombstone", model_update_rule="revise on restore",
)
_CLEAN = CoordinationContextModel(
    proposed=ActionIntentModel(action_id="a", shared_resources=["db"], window="w1"),
    active=[ActionIntentModel(action_id="b", shared_resources=["cache"], window="w2")],
)
_CALM = WorldStateModel(horizons_evaluated=["next week"])


def _r3(**extra) -> ActionCapsule:
    """Irreversible, external, real-world: the tier that fails closed now."""
    return ActionCapsule(
        action_id="ag-r3", action_type="account_deletion", description="d", user_visible_summary="d",
        external_side_effect=True, irreversible_or_costly_to_reverse=True, real_world_effect=True,
        reversibility="irreversible", blast_radius="account", created_at="2026-10-18T00:00:00+00:00", **extra,
    )


def _decide(capsule: ActionCapsule):
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    return decide_gate(capsule, risk=risk, scenario_model=scenario,
                       mitigation_plan=build_mitigation_plan(capsule, risk, scenario), audit_id="audit-test")


def _not_evaluated(decision) -> dict[str, str]:
    return {e["control"]: e["severity"] for e in decision.evidence if e.get("kind") == "control_not_evaluated"}


# AL-T01 / AL-001 — an R3 action that declares no verification plan is ballistic and blocked.
def test_r3_action_without_a_verification_contract_is_blocked() -> None:
    decision = _decide(_r3(coordination_context=_CLEAN, world_state=_CALM))
    assert decision.decision == "block" and decision.completion_state == "blocked-escalated"
    assert _BALLISTIC in decision.reason_codes
    items = [e for e in decision.evidence if e.get("kind") == "verification_contract"]
    assert items and items[0]["missing_elements"] == ["contract_absent"]
    assert _not_evaluated(decision) == {"verification_contract": "block"}


# The audit's paired case: omitting the contract no longer does better than declaring a partial one.
def test_omitting_the_contract_is_no_better_than_an_incomplete_one() -> None:
    absent = _decide(_r3(coordination_context=_CLEAN, world_state=_CALM))
    partial = _decide(_r3(verification_contract=VerificationContractModel(expected_result="account removed"),
                          coordination_context=_CLEAN, world_state=_CALM))
    assert absent.decision == partial.decision == "block"
    assert _BALLISTIC in absent.reason_codes and _BALLISTIC in partial.reason_codes


# AL-T03 / AL-T04 / AL-002 — without coordination or world context an R3 action cannot be called
# conflict-free or trend-safe: it goes to a person, and the evidence names the checks that did not run.
def test_r3_action_without_coordination_or_world_context_goes_to_review() -> None:
    decision = _decide(_r3(verification_contract=_COMPLETE))
    assert decision.decision == "require_human_review"
    assert decision.completion_state != "verified"
    assert _not_evaluated(decision) == {"coordination": "review", "weak_signals": "review"}


def test_r3_action_with_every_context_supplied_is_not_held_for_missing_context() -> None:
    decision = _decide(_r3(verification_contract=_COMPLETE, coordination_context=_CLEAN, world_state=_CALM))
    assert _NOT_EVALUATED not in decision.reason_codes and _not_evaluated(decision) == {}
    # Held only by the authority rule (a high-risk action needs authority no request can show), not for context.
    assert decision.decision == "require_human_review" and "G6_E_AUTHORITY_UNVERIFIED" in decision.reason_codes


# Staged: a material but reversible, local-tier action keeps the notice, not a block or review.
def test_lower_tier_material_action_without_context_keeps_the_notice() -> None:
    # reversible only with a backup the gate can read (ACTION_GATING-01); this file stands for one
    cap = ActionCapsule(action_id="ag-acct", action_type="write_file", description="d",
                        user_visible_summary="d", reversibility="reversible", blast_radius="account",
                        proposed_inputs={"checkpoint": str(Path(__file__).resolve())},
                        created_at="2026-10-18T00:00:00+00:00")
    decision = _decide(cap)
    assert decision.decision == "allow_with_notice"
    assert _NOT_EVALUATED in decision.reason_codes and _BALLISTIC not in decision.reason_codes
