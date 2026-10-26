# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 003 (docs/transition/audits/003_action_logic.md).

Each test was written to fail on the code as ported from the archive, before the fix.
"""
from __future__ import annotations

import re
from pathlib import Path

import g6.action_logic
from g6.action_logic import (
    ActionIntent,
    ControlVariable,
    Hypothesis,
    HypothesisStatus,
    MonitoringCoverage,
    VerificationContract,
    WorldState,
    assess_material_action,
    assess_weak_signals,
    check_crisis_monitoring,
    detect_conflicts,
    evaluate_verification_contract,
    reauthorisation_severity,
    sequence_if_separable,
)
from g6.action_logic.controls import E_THRESHOLD_BREACHED
from g6.core.registry import get_registry

_CRISIS_UNKNOWN = "G6_E_CRISIS_MONITORING_UNKNOWN"


# AL-T05 / AL-003 — a single action that exceeds a budget on its own cannot be made safe by ordering.
def test_single_action_over_budget_is_not_separable() -> None:
    single = ActionIntent("a", costs=(("gpu", 12.0),))
    assert sequence_if_separable([single], budgets={"gpu": 10.0}) is None


def test_actions_each_within_budget_still_sequence() -> None:
    a = ActionIntent("a", shared_resources=("gpu",), costs=(("gpu", 6.0),), window="w1")
    b = ActionIntent("b", shared_resources=("gpu",), costs=(("gpu", 6.0),), window="w1")
    # Together they breach (12 > 10) but each fits, so running them one at a time is safe.
    assert sequence_if_separable([a, b], budgets={"gpu": 10.0}) == ["a", "b"]


# AL-T06 / AL-004 — two charges of the same invoice are a duplicate whichever order they run in.
def test_duplicate_non_idempotent_effects_are_not_separable() -> None:
    a = ActionIntent("charge-1", target_resource="invoice-1", effect_signature="charge-invoice-1", window="w")
    b = ActionIntent("charge-2", target_resource="invoice-1", effect_signature="charge-invoice-1", window="w")
    assert sequence_if_separable([a, b]) is None


def test_duplicate_effects_with_shared_idempotency_key_are_separable() -> None:
    a = ActionIntent("r1", target_resource="cache", effect_signature="warm", window="w", idempotency_key="warm-v1")
    b = ActionIntent("r2", target_resource="cache", effect_signature="warm", window="w", idempotency_key="warm-v1")
    assert sequence_if_separable([a, b]) == ["r1", "r2"]


# AL-T09 / AL-007 — a variable already past its threshold blocks, even though it is not a *weak* signal.
def test_already_breached_ceiling_blocks_material_action() -> None:
    world = WorldState(variables=(ControlVariable("load", 95.0, 90.0, "rising"),), horizons_evaluated=("24h",))
    severity, reasons, items = assess_weak_signals(world, is_material=True)
    assert severity == "block"
    assert E_THRESHOLD_BREACHED in reasons
    assert any(i["kind"] == "threshold_breach" for i in items)


def test_resource_below_floor_blocks() -> None:
    world = WorldState(
        variables=(ControlVariable("disk_free", 2.0, 5.0, "flat", is_resource=True),), horizons_evaluated=("1h",)
    )
    assessment = assess_material_action(is_material=True, contract=None, world=world)
    assert E_THRESHOLD_BREACHED in assessment.reason_codes


def test_value_at_threshold_is_not_a_breach() -> None:
    world = WorldState(variables=(ControlVariable("load", 90.0, 90.0, "flat"),), horizons_evaluated=("24h",))
    _, reasons, _ = assess_weak_signals(world, is_material=True)
    assert E_THRESHOLD_BREACHED not in reasons


# AL-005 — two empty coverage records do not show that monitoring was preserved.
def test_empty_coverage_records_are_unknown_not_preserved() -> None:
    severity, reasons, item = check_crisis_monitoring(MonitoringCoverage(), MonitoringCoverage())
    assert severity == "review" and reasons == [_CRISIS_UNKNOWN]
    assert item["unknown"] == ["baseline"]


def test_block_with_omitted_coverage_records_is_unknown_not_preserved() -> None:
    found = get_registry().invoke("action_logic", "check_crisis_monitoring", {}).unwrap()["result"]
    assert found["severity"] == "review" and found["reason_codes"] == [_CRISIS_UNKNOWN]


def test_a_crisis_record_missing_against_a_real_baseline_still_blocks() -> None:
    baseline = MonitoringCoverage(telemetry_signals=4, audit_logging=True)
    assert check_crisis_monitoring(baseline, MonitoringCoverage())[0] == "block"


# AL-006 / AL-T12 — a replacement must be a different hypothesis with evidence of its own.
_FALSIFIED = Hypothesis("h1", "Restarting clears the queue backlog", confidence=0.1,
                        supporting_evidence=("restart log 03:00",), falsification_condition="backlog persists",
                        status=HypothesisStatus.FALSIFIED)


def _replacement(statement: str, evidence: tuple[str, ...]) -> Hypothesis:
    return Hypothesis("h2", statement, confidence=0.5, supporting_evidence=evidence,
                      falsification_condition="backlog persists after the change")


def test_the_falsified_hypothesis_recast_under_a_new_id_is_not_a_replacement() -> None:
    recast = _replacement("  restarting clears the QUEUE backlog ", ("restart log 04:00",))
    assert reauthorisation_severity(relied_hypothesis=_FALSIFIED, new_hypothesis=recast)[0] == "block"


def test_blank_or_recycled_evidence_is_not_evidence() -> None:
    for evidence in (("   ",), ("restart log 03:00",)):
        new = _replacement("Draining the queue first clears the backlog", evidence)
        assert reauthorisation_severity(relied_hypothesis=_FALSIFIED, new_hypothesis=new)[0] == "block", evidence


def test_a_new_hypothesis_with_new_evidence_is_accepted() -> None:
    new = _replacement("Draining the queue first clears the backlog", ("drain cut the backlog 90% in staging",))
    severity, _reasons, item = reauthorisation_severity(relied_hypothesis=_FALSIFIED, new_hypothesis=new)
    assert severity is None and item["replacement_accepted"] is True


# AL-008 / AL-T07 — a different action reusing the proposed id is compared, not skipped.
def test_a_reused_action_id_is_an_identity_collision() -> None:
    proposed = ActionIntent("a", shared_resources=("db",), window="w1")
    other = ActionIntent("a", target_resource="replicas", direction="decrease", shared_resources=("db",), window="w1")
    kinds = [c.kind for c in detect_conflicts(proposed, [other])]
    assert "identity_collision" in kinds and "resource_contention" in kinds


def test_the_proposed_action_itself_in_the_active_set_is_not_a_conflict() -> None:
    proposed = ActionIntent("a", shared_resources=("db",), window="w1")
    assert detect_conflicts(proposed, [proposed]) == ()


def test_a_schedule_with_a_reused_id_is_not_separable() -> None:
    a = ActionIntent("a", shared_resources=("db",), window="w1")
    assert sequence_if_separable([a, a]) is None


# AL-009 — the generic block's verdict rests on declared materiality and says so.
def test_block_labels_declared_materiality_as_advisory() -> None:
    found = get_registry().invoke("action_logic", "assess_material_action", {"is_material": False}).unwrap()
    assert found["materiality"] == "advisory_declared_materiality"


# AL-010 / AL-T08 — real time intervals are compared; other time formats fail safe to overlapping.
def _contends(window_a: str, window_b: str) -> bool:
    a = ActionIntent("a", shared_resources=("db",), window=window_a)
    b = ActionIntent("b", shared_resources=("db",), window=window_b)
    return any(c.kind == "resource_contention" for c in detect_conflicts(a, [b]))


def test_overlapping_iso_intervals_overlap() -> None:
    assert _contends("2026-10-18T10:00/2026-10-18T12:00", "2026-10-18T11:00/2026-10-18T13:00")


def test_time_windows_in_another_format_fail_safe_to_overlapping() -> None:
    assert _contends("10:00-12:00", "11:00-13:00")
    assert _contends("2026-10-18T10:00/2026-10-18T12:00", "w1")


def test_adjacent_intervals_and_distinct_buckets_are_separate() -> None:
    assert not _contends("2026-10-18T10:00/2026-10-18T11:00", "2026-10-18T11:00/2026-10-18T12:00")
    assert not _contends("w1", "w2")


# AL-011 / AL-T15 — a plan needs leading AND lagging indicators, and complete is not verified.
def _plan(**over) -> VerificationContract:
    fields = dict(expected_result="error rate under 1%", observation_window="30m", leading_indicators=("p50",),
                  lagging_indicators=("daily errors",), monitor="oncall", verification_time="T+30m",
                  stop_threshold="error rate > 5%", rollback_procedure="revert", model_update_rule="revise")
    return VerificationContract(**{**fields, **over})


def test_a_plan_without_lagging_indicators_is_incomplete() -> None:
    assert _plan(lagging_indicators=()).missing_elements() == ("lagging_indicators",)
    assert _plan(leading_indicators=("  ",)).missing_elements() == ("leading_indicators",)


def test_a_complete_plan_is_not_operationally_verified() -> None:
    severity, _reasons, item = evaluate_verification_contract(is_material=True, contract=_plan())
    assert severity is None and item["complete"] is True
    assert item["operationally_verified"] is False


# AL-014 — the component metadata states the licence its file headers carry.
def test_component_metadata_licence_matches_the_file_headers() -> None:
    root = Path(g6.action_logic.__file__).parent
    spdx = {re.search(r"SPDX-License-Identifier:\s*(\S+)", f.read_text(encoding="utf-8")).group(1)
            for f in root.glob("*.py")}
    meta = (root / "component_metadata.yaml").read_text(encoding="utf-8")
    assert spdx == {"Apache-2.0"}
    assert re.search(r"^license_status:\s*(\S+)", meta, re.M).group(1) == "Apache-2.0"
