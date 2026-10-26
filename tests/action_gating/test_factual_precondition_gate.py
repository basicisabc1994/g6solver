# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Factual-precondition gate (WS-A.2): the action gate must not let an action proceed on
an unproven factual premise (Epistemic Integrity EI-090..094; launch-blocker S1 #2).

This is the EMPIRICAL leg for WS-A.2 — the enforcement is real iff these pass. Each test
isolates the precondition gate (controlled LOW risk + satisfied scenario/mitigation) so a
different gate cannot confound the assertion, except where backward-compatibility/monotonicity
is being checked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import decide_gate
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ActionIntentModel,
    CoordinationContextModel,
    FactualEvidenceRef,
    FactualPrecondition,
    ImpactAssessment,
    VerificationContractModel,
    WorldStateModel,
)

_PC_CODE = "G6_E_FACTUAL_PRECONDITION_UNSUPPORTED"


def _irreversible_capsule(preconditions: list[FactualPrecondition]) -> ActionCapsule:
    # Satisfied verification, coordination and world contexts: an irreversible action that omits
    # them is blocked or sent to review (audit 003, AL-001/AL-002), which would mask this gate.
    return ActionCapsule(
        verification_contract=VerificationContractModel(
            expected_result="account is removed", observation_window="24h",
            leading_indicators=["deletion job started"], lagging_indicators=["no further logins"],
            monitor="oncall-identity", verification_time="T+24h", stop_threshold="any restore request",
            rollback_procedure="restore from the soft-delete tombstone", model_update_rule="revise on restore",
        ),
        coordination_context=CoordinationContextModel(proposed=ActionIntentModel(action_id="ag-irrev")),
        world_state=WorldStateModel(horizons_evaluated=["next week"]),
        action_id="ag-irrev",
        action_type="account_deletion",
        description="Delete the customer account",
        user_visible_summary="Delete the customer account",
        external_side_effect=True,
        irreversible_or_costly_to_reverse=True,
        reversibility="irreversible",
        blast_radius="account",
        created_at="2026-06-24T00:00:00+00:00",
        factual_preconditions=preconditions,
    )


def _safe_capsule(preconditions: list[FactualPrecondition]) -> ActionCapsule:
    return ActionCapsule(
        action_id="ag-safe",
        action_type="write_file",
        description="Write a scratch file",
        user_visible_summary="Write a scratch file",
        reversibility="reversible",
        blast_radius="local",
        # reversible only with a backup the gate can read (ACTION_GATING-01); this file stands for one
        proposed_inputs={"checkpoint": str(Path(__file__).resolve())},
        created_at="2026-06-24T00:00:00+00:00",
        factual_preconditions=preconditions,
    )


def _isolated_decide(capsule: ActionCapsule):
    """Decide with a satisfied scenario/mitigation so no scenario or mitigation blocker can confound
    the assertion. The supplied LOW risk can no longer lower the gate's own (review B1-2), so a
    high-risk action may still go to a person for authority; only a block or the precondition code
    is asserted. The action's own irreversible/external flags still push the precondition tier to R3."""
    risk = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, risk)
    mitigation = build_mitigation_plan(capsule, risk, scenario)  # residual low when risk low
    return decide_gate(
        capsule,
        risk=risk,
        scenario_model=scenario,
        mitigation_plan=mitigation,
        audit_id="audit-test",
    )


def _supported_pair(statement: str) -> FactualPrecondition:
    """Two independent primary, high-authority lineages -> E3 (meets R3)."""
    return FactualPrecondition(
        statement=statement,
        evidence=[
            FactualEvidenceRef(source_type="primary", independence_group="src_a", authority="high"),
            FactualEvidenceRef(source_type="primary", independence_group="src_b", authority="high"),
        ],
    )


# --------------------------------------------------------------------------- #
# Acceptance tests (Epistemic Integrity spec EI-090..094)
# --------------------------------------------------------------------------- #

def test_ei090_unsupported_precondition_blocks_irreversible_action() -> None:
    cap = _irreversible_capsule(
        [FactualPrecondition(statement="The customer requested account deletion.")]
    )
    decision = _isolated_decide(cap)

    assert decision.decision == "block"
    assert decision.completion_state == "blocked-escalated"
    assert _PC_CODE in decision.reason_codes

    # Control: the SAME action without the precondition is not blocked by this gate,
    # proving the unsupported precondition is what blocked it (the raw-LLM delta).
    base = _isolated_decide(_irreversible_capsule([]))
    assert _PC_CODE not in base.reason_codes
    assert base.decision != "block"


def test_ei091_well_supported_precondition_satisfies_gate() -> None:
    cap = _irreversible_capsule([_supported_pair("The customer requested account deletion.")])
    decision = _isolated_decide(cap)

    # The precondition is adequately supported (E3 >= R3 minimum), so the precondition
    # gate does not fire; the action may still be gated by other controls.
    assert _PC_CODE not in decision.reason_codes
    # Evidence trail records the precondition grade for auditability.
    pc_items = [e for e in decision.evidence if e.get("kind") == "factual_precondition"]
    assert pc_items and pc_items[0]["supported"] is True
    assert pc_items[0]["evidence_grade"] == "E3"


def test_ei092_low_risk_unsupported_precondition_is_soft_not_blocking() -> None:
    # Staged rollout: a low-risk, local, reversible action with an unsupported precondition
    # is annotated (soft) but NOT hard-blocked or escalated.
    cap = _safe_capsule(
        [FactualPrecondition(statement="The scratch file may be overwritten.")]
    )
    decision = decide_gate(cap, risk=ImpactAssessment(aggregate_risk="low"), audit_id="audit-test")

    assert decision.decision == "allow_with_notice"
    assert decision.completion_state == "qualified-draft"
    assert _PC_CODE in decision.reason_codes
    assert decision.decision not in {"block", "require_human_review"}


def test_ei093_contested_precondition_blocks_irreversible_action() -> None:
    # Credible independent support AND opposition -> CONTESTED -> not "supported" even at E3.
    pc = FactualPrecondition(
        statement="The data was fully backed up before deletion.",
        evidence=[
            FactualEvidenceRef(
                source_type="primary", independence_group="backup_log",
                supports_or_opposes="supports", authority="high",
            ),
            FactualEvidenceRef(
                source_type="primary", independence_group="restore_test",
                supports_or_opposes="opposes", authority="high",
            ),
        ],
    )
    decision = _isolated_decide(_irreversible_capsule([pc]))

    assert decision.decision == "block"
    assert _PC_CODE in decision.reason_codes
    pc_items = [e for e in decision.evidence if e.get("kind") == "factual_precondition"]
    assert pc_items and pc_items[0]["contested"] is True


def test_ei094_no_preconditions_is_backward_compatible() -> None:
    cap = _irreversible_capsule([])
    decision = _isolated_decide(cap)
    assert _PC_CODE not in decision.reason_codes

    # A normalized capsule with no preconditions defaults to an empty list.
    norm = normalize_action_capsule({"action_type": "write_file", "target_resource": "scratch.txt"})
    assert norm.factual_preconditions == []


# --------------------------------------------------------------------------- #
# Normalizer + monotonicity
# --------------------------------------------------------------------------- #

def test_normalizer_accepts_string_and_structured_preconditions() -> None:
    cap = normalize_action_capsule(
        {
            "action_type": "send_email",
            "factual_preconditions": [
                "The recipient consented to email.",
                {
                    "statement": "Order #5 is fully refunded.",
                    "evidence": [
                        {"source_type": "primary", "independence_group": "ledger", "authority": "high"}
                    ],
                },
                "   ",  # blank entry is ignored
            ],
        }
    )

    assert len(cap.factual_preconditions) == 2
    assert cap.factual_preconditions[0].statement == "The recipient consented to email."
    assert cap.factual_preconditions[0].evidence == []
    assert cap.factual_preconditions[1].evidence[0].source_type == "primary"


def test_precondition_gate_never_downgrades_an_existing_block() -> None:
    # Even with a well-supported precondition, withholding the scenario model on an
    # irreversible action must still BLOCK (SCENARIO_REQUIRED); the precondition gate is
    # monotonic and must not relax another gate's block.
    cap = _irreversible_capsule([_supported_pair("The customer requested account deletion.")])
    risk = classify_impact(cap, classify_reversibility(cap))

    decision = decide_gate(
        cap, risk=risk, scenario_model=None, mitigation_plan=None, audit_id="audit-test"
    )

    assert decision.decision == "block"
    assert "G6_E_SCENARIO_REQUIRED" in decision.reason_codes
    assert _PC_CODE not in decision.reason_codes  # precondition is supported, so it stays quiet


# --------------------------------------------------------------------------- #
# Property / invariant tests (formal-leg seed: invariants over an input matrix)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "flag",
    ["irreversible_or_costly_to_reverse", "external_side_effect", "real_world_effect"],
)
def test_property_unsupported_precondition_always_blocks_r3_actions(flag: str) -> None:
    """INVARIANT (fail-closed now): an unsupported (E0) factual precondition ALWAYS blocks
    an external / irreversible / real-world action, whichever R3 flag is set."""
    cap = ActionCapsule(
        action_id="ag-prop",
        action_type="op",
        description="d",
        user_visible_summary="d",
        reversibility="unknown",
        blast_radius="account",
        created_at="2026-06-24T00:00:00+00:00",
        factual_preconditions=[FactualPrecondition(statement="An unproven premise holds.")],
        **{flag: True},
    )
    decision = _isolated_decide(cap)
    assert decision.decision == "block"
    assert _PC_CODE in decision.reason_codes


@pytest.mark.parametrize(
    "statement",
    ["Premise A.", "Premise B about the data.", "A claim with no supporting evidence."],
)
def test_property_low_risk_unsupported_precondition_never_hard_blocks(statement: str) -> None:
    """INVARIANT (staged rollout): a low-risk, local, reversible action with an unsupported
    precondition is NEVER hard-blocked or escalated to human review — soft only."""
    cap = _safe_capsule([FactualPrecondition(statement=statement)])
    decision = decide_gate(cap, risk=ImpactAssessment(aggregate_risk="low"), audit_id="audit-prop")
    assert decision.decision not in {"block", "require_human_review"}


# --------------------------------------------------------------------------- #
# Stance: evidence that only opposes a premise refutes it, never supports it
# --------------------------------------------------------------------------- #

def _opposed_only(statement: str) -> FactualPrecondition:
    """Two independent primary, high-authority lineages that both OPPOSE the premise."""
    return FactualPrecondition(
        statement=statement,
        evidence=[
            FactualEvidenceRef(source_type="primary", independence_group="src_a", supports_or_opposes="opposes",
                               authority="high"),
            FactualEvidenceRef(source_type="primary", independence_group="src_b", supports_or_opposes="opposes",
                               authority="high"),
        ],
    )


def _pc_item(decision) -> dict:
    return next(e for e in decision.evidence if e.get("kind") == "factual_precondition")


def test_ei090_a_premise_only_opposed_by_its_evidence_blocks_an_irreversible_action() -> None:
    decision = _isolated_decide(_irreversible_capsule([_opposed_only("The customer requested account deletion.")]))
    assert decision.decision == "block" and _PC_CODE in decision.reason_codes
    item = _pc_item(decision)
    assert (item["evidence_grade"], item["supported"], item["refuted"]) == ("E0", False, True)


def test_ei090_send_email_on_a_premise_its_evidence_opposes_is_not_supported() -> None:
    from g6.action_gating.audit import ActionGateAuditLogger
    from g6.action_gating.decision_engine import evaluate_action

    premise = _opposed_only("The customer consented to marketing email.").model_dump()
    ev = evaluate_action({"action_type": "send_email", "target_resource": "c@example.test",
                          "factual_preconditions": [premise]}, audit_logger=ActionGateAuditLogger())
    item = _pc_item(ev.gate_decision)
    assert (item["risk_level"], item["evidence_grade"], item["supported"]) == ("R3", "E0", False)
    assert _PC_CODE in ev.gate_decision.reason_codes and ev.gate_decision.decision == "block"


def test_a_refuted_premise_goes_to_a_person_even_on_a_low_risk_action() -> None:
    cap = _safe_capsule([_opposed_only("The scratch file may be overwritten.")])
    decision = decide_gate(cap, risk=ImpactAssessment(aggregate_risk="low"), audit_id="audit-test")
    assert decision.decision == "require_human_review" and _PC_CODE in decision.reason_codes
    assert _pc_item(decision)["refuted"] is True
