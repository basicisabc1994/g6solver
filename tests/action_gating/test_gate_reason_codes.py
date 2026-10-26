# SPDX-License-Identifier: Apache-2.0
"""Reason codes say why. ``G6_E_GATE_OK`` comes only with ``allow``: a decision short of it names its cause even when
no named check fired, and an action type without a profile is never allowed on its caller's declaration alone."""
from __future__ import annotations

from pathlib import Path

import pytest

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import decide_gate, evaluate_action
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

_OK = "G6_E_GATE_OK"
_RUNS = {"allow", "allow_with_notice"}
BACKUP = str(Path(__file__).resolve())


def _evaluate(action: dict):
    return evaluate_action(action, audit_logger=ActionGateAuditLogger()).gate_decision


def test_a_plain_allow_still_carries_gate_ok() -> None:
    # a write is reversible only with a backup the gate can read (ACTION_GATING-01); this file stands for one
    gate = _evaluate({"action_type": "write_file", "target_resource": "notes.txt", "checkpoint": BACKUP})
    assert gate.decision == "allow" and gate.reason_codes == [_OK]


def test_unknown_risk_names_why_instead_of_gate_ok() -> None:
    gate = _evaluate({"action_type": "write_file", "target_resource": "notes.txt", "reversibility": "unknown"})
    assert gate.decision == "require_additional_information"
    assert _OK not in gate.reason_codes and "G6_E_RISK_UNKNOWN" in gate.reason_codes


def test_high_residual_risk_names_why_instead_of_gate_ok() -> None:
    gate = _evaluate({"action_type": "write_file", "target_resource": "notes.txt", "safety_domains": ["privacy"]})
    assert gate.decision == "require_mitigation"
    assert _OK not in gate.reason_codes and "G6_E_RESIDUAL_RISK_HIGH" in gate.reason_codes


def test_a_confirmation_names_why_instead_of_gate_ok() -> None:
    capsule = normalize_action_capsule({"action_type": "write_file", "target_resource": "notes.txt",
                                        "safety_domains": ["privacy"]})
    risk = classify_impact(capsule, classify_reversibility(capsule))
    scenario = build_action_scenarios(capsule, risk)
    plan = build_mitigation_plan(capsule, risk, scenario).model_copy(update={"residual_risk": "medium"})
    decision = decide_gate(capsule, risk=risk, scenario_model=scenario, mitigation_plan=plan, audit_id="audit-test")
    assert decision.decision == "require_confirmation"
    assert _OK not in decision.reason_codes and "G6_E_CONFIRMATION_REQUIRED" in decision.reason_codes


def test_an_acknowledged_weak_signal_notice_names_why_instead_of_gate_ok() -> None:
    capsule = ActionCapsule(
        action_id="ag-ack", action_type="write_file", description="d", user_visible_summary="d",
        reversibility="reversible", blast_radius="account", created_at="2026-10-20T00:00:00+00:00",
        proposed_inputs={"checkpoint": BACKUP},
        verification_contract=VerificationContractModel(
            expected_result="file written", observation_window="1h", leading_indicators=["write ok"],
            lagging_indicators=["no restore"], monitor="ops", verification_time="T+1h", stop_threshold="any error",
            rollback_procedure="restore the copy", model_update_rule="revise on error"),
        coordination_context=CoordinationContextModel(proposed=ActionIntentModel(action_id="ag-ack")),
        world_state=WorldStateModel(variables=[ControlVariableModel(name="load", value=8, threshold=10,
                                                                    trend="rising", acknowledged=True)],
                                    horizons_evaluated=["next hour"]),
    )
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    decision = decide_gate(capsule, risk=risk, scenario_model=scenario,
                           mitigation_plan=build_mitigation_plan(capsule, risk, scenario), audit_id="audit-test")
    assert decision.decision == "allow_with_notice"
    assert _OK not in decision.reason_codes and "G6_E_WEAK_SIGNAL_ACKNOWLEDGED" in decision.reason_codes


@pytest.mark.parametrize("action", [
    {"action_type": "write_file", "target_resource": "notes.txt", "reversibility": "unknown"},
    {"action_type": "write_file", "target_resource": "notes.txt", "safety_domains": ["privacy"]},
    {"action_type": "notify_customer", "target_resource": "c@example.test", "target_backend": "local",
     "reversibility": "reversible", "blast_radius": "local"},
    {"action_type": "send_email", "target_resource": "c@example.test"},
    {"action_type": "mystery_op"},
])
def test_gate_ok_never_rides_on_a_decision_short_of_allow(action: dict) -> None:
    gate = _evaluate(action)
    assert gate.reason_codes and (_OK not in gate.reason_codes or gate.decision == "allow")


# Audit 002, AG-008: an action type with no profile has no descriptor here, so its declared facts are only claims.
def test_an_unprofiled_action_declared_reversible_and_local_is_not_allowed() -> None:
    gate = _evaluate({"action_type": "notify_customer", "target_resource": "c@example.test",
                      "target_backend": "local", "reversibility": "reversible", "blast_radius": "local"})
    assert gate.decision not in _RUNS
    assert gate.decision == "require_additional_information" and gate.risk.aggregate_risk == "unknown"
    assert "G6_E_ACTION_UNPROFILED" in gate.reason_codes


def test_an_unprofiled_action_built_as_a_capsule_is_not_allowed_either() -> None:
    capsule = ActionCapsule(action_id="ag-typed", action_type="notify_customer", description="d",
                            user_visible_summary="d", reversibility="reversible", blast_radius="local",
                            created_at="2026-10-20T00:00:00+00:00")
    assert evaluate_action(capsule, audit_logger=ActionGateAuditLogger()).gate_decision.decision not in _RUNS


def test_a_declaration_still_raises_an_unprofiled_action() -> None:
    gate = _evaluate({"action_type": "notify_customer", "target_resource": "c@example.test",
                      "external_side_effect": True})
    assert gate.risk.aggregate_risk == "high" and gate.decision not in _RUNS
