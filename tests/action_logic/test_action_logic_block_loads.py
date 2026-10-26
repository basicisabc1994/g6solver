# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""W1: the registry loads action_logic's block, and every op answers what the library answers."""
from __future__ import annotations

import pytest
from g6.action_logic import (
    ActionIntent,
    ControlVariable,
    Hypothesis,
    HypothesisStatus,
    MonitoringCoverage,
    Outcome,
    WorldState,
    apply_disconfirming_evidence,
    assess_material_action,
    check_crisis_monitoring,
    detect_conflicts,
    disconfirming_evidence_severity,
    reauthorisation_severity,
    sequence_if_separable,
    update_on_outcome,
)
from g6.core.ai_block import AIBlock
from g6.core.registry import get_registry
from returns.result import Failure, Success

NAME = "action_logic"

EXPECTED_PARAMS = {
    "assess_material_action": ["is_material", "contract", "proposed", "active", "budgets", "world"],
    "detect_conflicts": ["proposed", "active", "budgets"],
    "sequence_if_separable": ["actions", "budgets"],
    "check_crisis_monitoring": ["baseline", "crisis"],
    "update_on_outcome": ["hypothesis", "outcome", "evidence_text"],
    "apply_disconfirming_evidence": ["hypothesis", "note"],
    "disconfirming_evidence_severity": ["hypothesis"],
    "reauthorisation_severity": ["relied_hypothesis", "new_hypothesis"],
}

NOT_EXPOSED = [
    "reauthorisation_severity_from_registry",
    "evaluate_verification_contract",
    "evaluate_coordination",
    "assess_weak_signals",
    "is_deteriorating",
    "advance",
    "can_transition",
]

PROPOSED_JSON = {
    "action_id": "a1", "target_resource": "db", "direction": "increase", "window": "w1",
    "shared_resources": ["cpu"], "costs": [["cpu", 6.0]],
}
ACTIVE_JSON = [{
    "action_id": "a2", "target_resource": "db", "direction": "decrease", "window": "w1",
    "shared_resources": ["cpu"], "costs": [["cpu", 5.0]],
}]
PROPOSED = ActionIntent(
    "a1", target_resource="db", direction="increase", window="w1",
    shared_resources=("cpu",), costs=(("cpu", 6.0),),
)
ACTIVE = [ActionIntent(
    "a2", target_resource="db", direction="decrease", window="w1",
    shared_resources=("cpu",), costs=(("cpu", 5.0),),
)]
HYP_JSON = {"hypothesis_id": "h1", "statement": "caching cuts latency", "confidence": 0.5}
HYP = Hypothesis("h1", "caching cuts latency", confidence=0.5)


def _invoke(op: str, params: dict | None = None):
    return get_registry().invoke(NAME, op, params or {}, block_sensitive=True)


def _answer(op: str, params: dict | None = None):
    res = _invoke(op, params)
    assert isinstance(res, Success), res
    value = res.unwrap()
    assert value["op"] == op
    return value["result"]


def _triple(found: tuple) -> dict:
    severity, reasons, item = found
    return {"severity": severity, "reason_codes": list(reasons), "item": item}


def test_the_registry_loads_a_block():
    registry = get_registry()
    cls = registry.get_class(NAME)
    assert cls is not None and issubclass(cls, AIBlock) and cls is not AIBlock
    described = registry.describe(NAME)
    assert described["available"] is True
    assert described["maturity"] == "beta"


def test_ops_lists_every_op_with_its_parameters():
    res = _invoke("ops")
    assert isinstance(res, Success), res
    out = res.unwrap()
    assert out["ops"] == list(EXPECTED_PARAMS)
    assert out["params"] == EXPECTED_PARAMS
    helped = _invoke("help").unwrap()
    assert len(helped["help"]) == len(EXPECTED_PARAMS)
    assert all(line.startswith(op + "(") for line, op in zip(helped["help"], EXPECTED_PARAMS))


def test_assess_material_action_answers_what_the_library_answers():
    world_json = {
        "variables": [{"name": "disk", "value": 0.8, "threshold": 0.9, "trend": "rising"}],
        "horizons_evaluated": ["24h"],
    }
    got = _answer("assess_material_action", {
        "is_material": True, "proposed": PROPOSED_JSON, "active": ACTIVE_JSON, "world": world_json,
    })
    expected = assess_material_action(
        is_material=True,
        contract=None,
        proposed=PROPOSED,
        active=ACTIVE,
        world=WorldState(
            variables=(ControlVariable("disk", 0.8, 0.9, "rising"),), horizons_evaluated=("24h",),
        ),
    ).to_dict()
    assert got == expected
    assert got["severity"] == "block"
    assert got["reason_codes"] == [
        "G6_E_BALLISTIC_ACTION_BLOCKED", "G6_E_ACTION_COLLISION", "G6_E_UNADDRESSED_WEAK_SIGNAL",
    ]


def test_detect_conflicts_answers_what_the_library_answers():
    got = _answer("detect_conflicts", {
        "proposed": PROPOSED_JSON, "active": ACTIVE_JSON, "budgets": {"cpu": 10.0},
    })
    expected = [c.to_dict() for c in detect_conflicts(PROPOSED, ACTIVE, budgets={"cpu": 10.0})]
    assert got == expected
    assert [c["kind"] for c in got] == [
        "contradictory_objectives", "resource_contention", "combined_threshold_breach",
    ]


def test_sequence_if_separable_answers_what_the_library_answers():
    actions_json = [
        {"action_id": "b1", "shared_resources": ["cpu"], "window": "w1"},
        {"action_id": "b2", "shared_resources": ["cpu"], "window": "w1"},
    ]
    got = _answer("sequence_if_separable", {"actions": actions_json})
    expected = sequence_if_separable([
        ActionIntent("b1", shared_resources=("cpu",), window="w1"),
        ActionIntent("b2", shared_resources=("cpu",), window="w1"),
    ])
    assert got == expected == ["b1", "b2"]


def test_check_crisis_monitoring_answers_what_the_library_answers():
    got = _answer("check_crisis_monitoring", {
        "baseline": {"telemetry_signals": 5, "verification_per_hour": 2.0, "audit_logging": True},
        "crisis": {"telemetry_signals": 3, "verification_per_hour": 4.0, "audit_logging": True},
    })
    expected = _triple(check_crisis_monitoring(
        MonitoringCoverage(telemetry_signals=5, verification_per_hour=2.0, audit_logging=True),
        MonitoringCoverage(telemetry_signals=3, verification_per_hour=4.0, audit_logging=True),
    ))
    assert got == expected
    assert got == {
        "severity": "block",
        "reason_codes": ["G6_E_CRISIS_MONITORING_DEGRADED"],
        "item": {"degraded": ["telemetry"]},
    }


def test_update_on_outcome_answers_what_the_library_answers():
    got = _answer("update_on_outcome", {
        "hypothesis": HYP_JSON, "outcome": "confirmed", "evidence_text": "p95 down",
    })
    expected = update_on_outcome(HYP, Outcome.CONFIRMED, evidence="p95 down").to_dict()
    assert got == expected
    assert got["status"] == "supported"
    assert got["confidence"] == pytest.approx(0.7)


def test_apply_disconfirming_evidence_answers_what_the_library_answers():
    got = _answer("apply_disconfirming_evidence", {"hypothesis": HYP_JSON, "note": "p95 up"})
    expected = apply_disconfirming_evidence(HYP, "p95 up").to_dict()
    assert got == expected
    assert got["status"] == "weakened"
    assert got["confidence"] == pytest.approx(0.25)


def test_disconfirming_evidence_severity_answers_what_the_library_answers():
    hyp_json = {**HYP_JSON, "disconfirming_evidence": ["p95 up"]}
    got = _answer("disconfirming_evidence_severity", {"hypothesis": hyp_json})
    expected = _triple(disconfirming_evidence_severity(
        Hypothesis("h1", "caching cuts latency", confidence=0.5, disconfirming_evidence=("p95 up",)),
    ))
    assert got == expected
    assert got["severity"] == "review"
    assert got["reason_codes"] == ["G6_E_DISCONFIRMING_EVIDENCE_PENDING"]


def test_reauthorisation_severity_answers_what_the_library_answers():
    relied_json = {**HYP_JSON, "status": "falsified"}
    got = _answer("reauthorisation_severity", {"relied_hypothesis": relied_json})
    expected = _triple(reauthorisation_severity(
        relied_hypothesis=Hypothesis(
            "h1", "caching cuts latency", confidence=0.5, status=HypothesisStatus.FALSIFIED,
        ),
    ))
    assert got == expected
    assert got["severity"] == "block"
    assert got["item"]["replacement_accepted"] is False


def test_a_malformed_argument_is_refused_with_the_component_code():
    res = _invoke("update_on_outcome", {"hypothesis": HYP_JSON, "outcome": "not-an-outcome"})
    assert isinstance(res, Failure)
    assert res.failure().startswith("[G6_E_ACTION_LOGIC_INVALID_FIELD] update_on_outcome: outcome"), res.failure()


def test_an_unknown_op_is_refused():
    res = _invoke("no_such_op")
    assert isinstance(res, Failure)
    assert res.failure().startswith("[G6_E_INPUT_VALIDATION] [ACTION_LOGIC_ERROR] no_such_op: op:"), res.failure()


@pytest.mark.parametrize("op", NOT_EXPOSED)
def test_a_function_left_unexposed_is_refused(op):
    res = _invoke(op)
    assert isinstance(res, Failure)
    assert res.failure().startswith(f"[G6_E_INPUT_VALIDATION] [ACTION_LOGIC_ERROR] {op}: op:"), res.failure()
