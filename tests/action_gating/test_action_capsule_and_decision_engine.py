# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Action capsule and gate-decision coverage for action_gating remediation."""
from __future__ import annotations

from pathlib import Path

from g6.action_gating import ActionGatingBlock, ActionGatingInput
from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.uncertainty import build_rumsfeld_matrix
from g6.action_gating.user_model import estimate_user_mental_model

#: A file the gate can read, standing for the backup a write is undone from (ACTION_GATING-01: without one a write's
#: reversibility is unknown).
BACKUP = str(Path(__file__).resolve())


def test_action_capsule_normalizes_unknowns():
    capsule = normalize_action_capsule({"action_type": "mystery_op"})

    assert capsule.action_type == "mystery_op"
    assert capsule.target_backend == "unknown"
    assert capsule.target_resource == "unknown"
    assert capsule.reversibility == "unknown"
    assert capsule.blast_radius == "unknown"
    assert capsule.missing_dependencies


def test_unknown_reversibility_is_conservative():
    capsule = normalize_action_capsule({"action_type": "mystery_op"})
    reversibility = classify_reversibility(capsule)
    impact = classify_impact(capsule, reversibility)

    assert reversibility == "unknown"
    assert impact.aggregate_risk == "unknown"
    assert any("not treated as low risk" in item for item in impact.rationale)


def test_scenario_modelling_blocks_high_risk_without_model():
    capsule = normalize_action_capsule(
        {
            "action_type": "account_deletion",
            "target_backend": "billing",
            "target_resource": "account-123",
            "irreversible_or_costly_to_reverse": True,
            "external_side_effect": True,
        }
    )
    risk = classify_impact(capsule, classify_reversibility(capsule))

    decision = decide_gate(
        capsule,
        risk=risk,
        scenario_model=None,
        mitigation_plan=build_mitigation_plan(capsule, risk, scenario_model=None),
        rumsfeld_matrix=build_rumsfeld_matrix(capsule, risk),
        user_mental_model=estimate_user_mental_model(capsule),
        safety_qa=None,
        audit_id="audit-test",
    )

    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert "G6_E_SCENARIO_REQUIRED" in decision.reason_codes
    assert decision.warning_card["degradation_level"] == "blocked"


def test_high_risk_requires_mitigation_or_blocks():
    capsule = normalize_action_capsule(
        {
            "action_type": "production_infrastructure_change",
            "target_backend": "infra",
            "target_resource": "prod-cluster",
            "real_world_effect": True,
        }
    )
    risk = classify_impact(capsule, classify_reversibility(capsule))
    scenarios = build_action_scenarios(capsule, risk)

    decision = decide_gate(
        capsule,
        risk=risk,
        scenario_model=scenarios,
        mitigation_plan=None,
        rumsfeld_matrix=build_rumsfeld_matrix(capsule, risk),
        user_mental_model=estimate_user_mental_model(capsule),
        safety_qa=None,
        audit_id="audit-test",
    )

    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert "G6_E_MITIGATION_REQUIRED" in decision.reason_codes


def test_known_unknowns_generate_pre_execution_checks():
    capsule = normalize_action_capsule(
        {
            "action_type": "send_email",
            "target_backend": "mail",
            "target_resource": "customer@example.test",
            "external_side_effect": True,
        }
    )
    risk = classify_impact(capsule, classify_reversibility(capsule))
    matrix = build_rumsfeld_matrix(capsule, risk)

    assert matrix.known_unknowns
    assert matrix.assumptions_to_validate
    assert matrix.monitoring_tripwires


def test_user_mental_model_does_not_infer_protected_traits():
    capsule = normalize_action_capsule(
        {
            "action_type": "payment",
            "description": "Charge the customer now",
            "target_resource": "invoice-1",
        }
    )
    estimate = estimate_user_mental_model(capsule)
    rendered = " ".join(estimate.model_dump(mode="json")["likely_user_assumptions"])

    assert estimate.confidence is None  # a template: nothing is measured, so no confidence (AG-011)
    assert estimate.caveats
    assert "protected" not in rendered.lower()


def test_confirmation_phrase_is_consequence_specific():
    block = ActionGatingBlock()
    out = block.infer(
        ActionGatingInput(
            op="evaluate",
            action={"action_type": "account_deletion", "action_params": {"target_resource": "account-123"}},
        )
    )

    assert out.is_ok()
    decision = out.value["gate_decision"]
    phrase = decision["required_confirmation_phrase"]
    assert "account_deletion" in phrase
    assert "account-123" in phrase
    assert phrase != "Are you sure?"


def test_gate_decision_transitions():
    low = normalize_action_capsule({"action_type": "write_file", "target_resource": "scratch.txt",
                                    "checkpoint": BACKUP})
    high = normalize_action_capsule(
        {
            "action_type": "send_email",
            "target_backend": "mail",
            "target_resource": "customer@example.test",
            "external_side_effect": True,
        }
    )

    low_decision = decide_gate(low, audit_id="audit-low")
    high_decision = decide_gate(high, audit_id="audit-high")

    assert low_decision.decision in {"allow", "allow_with_notice"}
    assert low_decision.completion_state in {"verified", "qualified-draft"}
    assert high_decision.decision in {"require_confirmation", "require_human_review", "block"}
    assert high_decision.required_confirmation_phrase
