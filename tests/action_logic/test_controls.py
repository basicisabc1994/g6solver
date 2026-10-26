# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""WS-C.1: the deterministic Dörner action-logic control floor (G6_Action_Logic.md).

EMPIRICAL + ABSTRACT legs. Each control is a pure function; the acceptance tests map to the
spec's mandatory AT-xx, and the property tests pin the closed-loop invariants (G6-INV-05/06/08,
§9). No LLM — the floor cannot be talked out of a block by fluency or pressure.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from g6.action_logic import (
    ActionIntent,
    ControlVariable,
    DecisionState,
    MonitoringCoverage,
    VerificationContract,
    WorldState,
    advance,
    assess_material_action,
    assess_weak_signals,
    check_crisis_monitoring,
    detect_conflicts,
    evaluate_coordination,
    evaluate_verification_contract,
    is_deteriorating,
    raise_severity,
    sequence_if_separable,
)
from g6.action_logic.controls import (
    E_BALLISTIC,
    E_COLLISION,
    E_CRISIS_MONITORING,
    E_NO_HORIZON,
    E_WEAK_SIGNAL,
)

_PURE = settings(max_examples=200, deadline=None)


def _complete_contract() -> VerificationContract:
    return VerificationContract(
        expected_result="error rate returns below 1%",
        observation_window="30 minutes post-deploy",
        leading_indicators=("p50 latency",),
        lagging_indicators=("daily error budget",),
        monitor="oncall-sre",
        verification_time="T+30m",
        stop_threshold="error rate > 5%",
        rollback_procedure="revert to previous release tag",
        model_update_rule="if error rate rises, retire the latency-cause hypothesis",
    )


# =========================================================================== #
# G6-INV-05 — ballistic-action prevention (AT-06)
# =========================================================================== #

class TestVerificationContract:
    def test_at06_material_action_without_contract_is_blocked(self) -> None:
        sev, reasons, item = evaluate_verification_contract(is_material=True, contract=None)
        assert sev == "block"
        assert E_BALLISTIC in reasons
        assert item["complete"] is False

    def test_at06_incomplete_contract_is_blocked(self) -> None:
        # Missing rollback + model-update rule -> still ballistic.
        partial = VerificationContract(
            expected_result="x", observation_window="1h", leading_indicators=("y",),
            monitor="m", verification_time="t", stop_threshold="s",
        )
        sev, reasons, item = evaluate_verification_contract(is_material=True, contract=partial)
        assert sev == "block"
        assert "rollback_procedure" in item["missing_elements"]
        assert "model_update_rule" in item["missing_elements"]

    def test_complete_contract_on_material_action_passes(self) -> None:
        sev, reasons, item = evaluate_verification_contract(
            is_material=True, contract=_complete_contract()
        )
        assert sev is None and reasons == [] and item["complete"] is True

    def test_non_material_action_is_out_of_scope(self) -> None:
        sev, reasons, item = evaluate_verification_contract(is_material=False, contract=None)
        assert sev is None and reasons == [] and item is None

    @_PURE
    @given(
        material=st.booleans(),
        has_contract=st.booleans(),
        complete=st.booleans(),
    )
    def test_property_material_passes_iff_complete_contract(
        self, material: bool, has_contract: bool, complete: bool
    ) -> None:
        contract = None
        if has_contract:
            contract = _complete_contract() if complete else VerificationContract(expected_result="x")
        sev, _r, _i = evaluate_verification_contract(is_material=material, contract=contract)
        if not material:
            assert sev is None  # out of scope
        elif has_contract and complete:
            assert sev is None  # the only material pass
        else:
            assert sev == "block"  # every other material case is ballistic


# =========================================================================== #
# G6-INV-08 — coordinated interventions (AT-05)
# =========================================================================== #

class TestCoordination:
    def test_at05_shared_resource_overlap_blocks_material(self) -> None:
        a = ActionIntent("a", shared_resources=("db_pool",), window="w1")
        b = ActionIntent("b", shared_resources=("db_pool",), window="w1")
        sev, reasons, items = evaluate_coordination(a, [b], is_material=True)
        assert sev == "block" and E_COLLISION in reasons
        assert items[0]["kind"] == "resource_contention"

    def test_contradictory_objectives_detected(self) -> None:
        a = ActionIntent("scale_up", target_resource="replicas", direction="increase", window="w1")
        b = ActionIntent("scale_down", target_resource="replicas", direction="decrease", window="w1")
        conflicts = detect_conflicts(a, [b])
        assert any(c.kind == "contradictory_objectives" for c in conflicts)

    def test_duplicate_effects_detected(self) -> None:
        a = ActionIntent("a", target_resource="cache", effect_signature="flush", window="w1")
        b = ActionIntent("b", target_resource="cache", effect_signature="flush", window="w1")
        conflicts = detect_conflicts(a, [b])
        assert any(c.kind == "duplicate_effects" for c in conflicts)

    def test_combined_threshold_breach_detected(self) -> None:
        a = ActionIntent("a", costs=(("conns", 6.0),), window="w1")
        b = ActionIntent("b", costs=(("conns", 6.0),), window="w1")
        conflicts = detect_conflicts(a, [b], budgets={"conns": 10.0})
        assert any(c.kind == "combined_threshold_breach" for c in conflicts)

    def test_invalidated_assumption_detected(self) -> None:
        a = ActionIntent("a", relies_on_assumptions=("primary_db_healthy",))
        b = ActionIntent("b", invalidates_assumptions=("primary_db_healthy",))
        conflicts = detect_conflicts(a, [b])
        assert any(c.kind == "invalidated_assumption" for c in conflicts)

    def test_disjoint_windows_do_not_conflict(self) -> None:
        a = ActionIntent("a", shared_resources=("db",), window="w1")
        b = ActionIntent("b", shared_resources=("db",), window="w2")
        assert detect_conflicts(a, [b]) == ()

    def test_unknown_window_is_conservatively_overlapping(self) -> None:
        a = ActionIntent("a", shared_resources=("db",), window="")
        b = ActionIntent("b", shared_resources=("db",), window="w2")
        assert any(c.kind == "resource_contention" for c in detect_conflicts(a, [b]))

    def test_no_conflict_is_silent(self) -> None:
        a = ActionIntent("a", shared_resources=("cpu",), window="w1")
        b = ActionIntent("b", shared_resources=("disk",), window="w1")
        sev, reasons, items = evaluate_coordination(a, [b], is_material=True)
        assert sev is None and reasons == [] and items == []

    def test_at05_separable_conflicts_yield_a_sequence(self) -> None:
        # Only shared-resource contention -> serialisable into a coordinated sequence.
        a = ActionIntent("a", shared_resources=("db",), window="w1")
        b = ActionIntent("b", shared_resources=("db",), window="w1")
        order = sequence_if_separable([a, b])
        assert order == ["a", "b"]

    def test_at05_nonseparable_conflicts_cannot_be_sequenced(self) -> None:
        a = ActionIntent("a", target_resource="replicas", direction="increase", window="w1")
        b = ActionIntent("b", target_resource="replicas", direction="decrease", window="w1")
        assert sequence_if_separable([a, b]) is None


# =========================================================================== #
# §9 — weak-signal / future-risk review (AT-01) + AT-03 resource overshoot
# =========================================================================== #

class TestWeakSignals:
    def test_rising_load_toward_ceiling_is_deteriorating(self) -> None:
        assert is_deteriorating(ControlVariable("cpu", value=70, threshold=90, trend="rising"))

    def test_depleting_resource_is_deteriorating(self) -> None:
        v = ControlVariable("disk_free", value=30, threshold=10, trend="falling", is_resource=True)
        assert is_deteriorating(v)

    def test_flat_or_safe_trend_is_not_a_weak_signal(self) -> None:
        assert not is_deteriorating(ControlVariable("cpu", value=70, threshold=90, trend="flat"))
        # already past the threshold is an active fault, not a *weak* signal:
        assert not is_deteriorating(ControlVariable("cpu", value=95, threshold=90, trend="rising"))

    def test_at01_unacknowledged_deteriorating_signal_blocks_material(self) -> None:
        world = WorldState(
            variables=(ControlVariable("cpu", value=70, threshold=90, trend="rising"),),
            horizons_evaluated=("next 24h",),
        )
        sev, reasons, items = assess_weak_signals(world, is_material=True)
        assert sev == "block" and E_WEAK_SIGNAL in reasons

    def test_at01_acknowledged_signal_downgrades_to_notice(self) -> None:
        world = WorldState(
            variables=(ControlVariable("cpu", value=70, threshold=90, trend="rising", acknowledged=True),),
            horizons_evaluated=("next 24h",),
        )
        sev, reasons, _items = assess_weak_signals(world, is_material=True)
        assert sev == "notice" and E_WEAK_SIGNAL not in reasons

    def test_no_future_horizon_blocks_material(self) -> None:
        world = WorldState(variables=(), horizons_evaluated=())
        sev, reasons, _items = assess_weak_signals(world, is_material=True)
        assert sev == "block" and E_NO_HORIZON in reasons

    def test_at03_resource_overshoot_blocks(self) -> None:
        # An intervention improving output while depleting a finite supporting resource.
        world = WorldState(
            variables=(
                ControlVariable("token_budget", value=200, threshold=50, trend="falling", is_resource=True),
            ),
            horizons_evaluated=("end of run",),
        )
        sev, reasons, _i = assess_weak_signals(world, is_material=True)
        assert sev == "block" and E_WEAK_SIGNAL in reasons

    def test_non_material_action_skips_weak_signal_gate(self) -> None:
        world = WorldState(variables=(), horizons_evaluated=())
        assert assess_weak_signals(world, is_material=False) == (None, [], [])


# =========================================================================== #
# G6-INV-06 — monitoring survives crises (AT-07)
# =========================================================================== #

class TestCrisisMonitoring:
    def _baseline(self) -> MonitoringCoverage:
        return MonitoringCoverage(
            telemetry_signals=10, verification_per_hour=4.0,
            audit_logging=True, anomaly_detection=True, escalation_ready=True,
        )

    def test_at07_dropped_telemetry_is_critical(self) -> None:
        crisis = MonitoringCoverage(
            telemetry_signals=3, verification_per_hour=4.0,
            audit_logging=True, anomaly_detection=True, escalation_ready=True,
        )
        sev, reasons, item = check_crisis_monitoring(self._baseline(), crisis)
        assert sev == "block" and E_CRISIS_MONITORING in reasons
        assert "telemetry" in item["degraded"]

    def test_at07_shorter_review_interval_is_allowed(self) -> None:
        # Higher verification frequency (shorter interval) + everything else preserved -> OK.
        crisis = MonitoringCoverage(
            telemetry_signals=10, verification_per_hour=12.0,
            audit_logging=True, anomaly_detection=True, escalation_ready=True,
        )
        sev, reasons, item = check_crisis_monitoring(self._baseline(), crisis)
        assert sev is None and reasons == [] and item["degraded"] == []

    def test_at07_dropping_audit_or_escalation_is_critical(self) -> None:
        crisis = MonitoringCoverage(
            telemetry_signals=10, verification_per_hour=4.0,
            audit_logging=False, anomaly_detection=True, escalation_ready=False,
        )
        sev, _r, item = check_crisis_monitoring(self._baseline(), crisis)
        assert sev == "block"
        assert "audit_logging" in item["degraded"] and "escalation_readiness" in item["degraded"]

    @_PURE
    @given(
        b_tel=st.integers(0, 20), c_tel=st.integers(0, 20),
        b_vf=st.floats(0, 20), c_vf=st.floats(0, 20),
    )
    def test_property_any_drop_blocks_and_dominance_passes(
        self, b_tel: int, c_tel: int, b_vf: float, c_vf: float
    ) -> None:
        base = MonitoringCoverage(telemetry_signals=b_tel, verification_per_hour=b_vf,
                                  audit_logging=True, anomaly_detection=True, escalation_ready=True)
        crisis = MonitoringCoverage(telemetry_signals=c_tel, verification_per_hour=c_vf,
                                    audit_logging=True, anomaly_detection=True, escalation_ready=True)
        sev, _r, _i = check_crisis_monitoring(base, crisis)
        dropped = c_tel < b_tel or c_vf < b_vf
        assert (sev == "block") == dropped


# =========================================================================== #
# Combined assessment + monotonic-severity invariant
# =========================================================================== #

class TestCombinedAssessment:
    def test_block_dominates_notice(self) -> None:
        world = WorldState(
            variables=(ControlVariable("cpu", value=70, threshold=90, trend="rising", acknowledged=True),),
            horizons_evaluated=("24h",),
        )  # acknowledged -> notice on its own
        res = assess_material_action(
            is_material=True, contract=None,  # ballistic -> block
            world=world,
        )
        assert res.severity == "block" and res.blocks()
        assert E_BALLISTIC in res.reason_codes

    def test_clean_material_action_passes_all_controls(self) -> None:
        world = WorldState(variables=(), horizons_evaluated=("24h",))
        proposed = ActionIntent("a", shared_resources=("db",), window="w1")
        res = assess_material_action(
            is_material=True, contract=_complete_contract(),
            proposed=proposed, active=[ActionIntent("b", shared_resources=("cpu",), window="w1")],
            world=world,
        )
        assert res.severity is None and not res.blocks()

    def test_reason_codes_are_deduped(self) -> None:
        world = WorldState(
            variables=(
                ControlVariable("a", value=1, threshold=2, trend="rising"),
                ControlVariable("b", value=1, threshold=2, trend="rising"),
            ),
            horizons_evaluated=("24h",),
        )
        res = assess_material_action(is_material=True, contract=_complete_contract(), world=world)
        assert list(res.reason_codes).count(E_WEAK_SIGNAL) == 1

    @_PURE
    @given(
        a=st.sampled_from([None, "notice", "review", "block"]),
        b=st.sampled_from([None, "notice", "review", "block"]),
    )
    def test_property_raise_severity_never_lowers(self, a, b) -> None:
        from g6.action_logic.schema import SEVERITY_RANK
        out = raise_severity(a, b)
        assert SEVERITY_RANK[out] >= SEVERITY_RANK[a]
        assert SEVERITY_RANK[out] >= SEVERITY_RANK[b]


# =========================================================================== #
# §5 — the decision lifecycle forbids the ballistic shortcut (re-exported)
# =========================================================================== #

class TestDecisionLifecycle:
    def test_draft_cannot_jump_straight_to_executed(self) -> None:
        # The §5 prohibited transition: a decision must be observed/modelled/.../authorised
        # before it can execute. The ballistic shortcut is structurally rejected.
        with pytest.raises(ValueError):
            advance(DecisionState.DRAFT, DecisionState.EXECUTED)

    def test_authorised_cannot_close_without_verification(self) -> None:
        with pytest.raises(ValueError):
            advance(DecisionState.AUTHORISED, DecisionState.CLOSED)

    def test_full_lifecycle_path_is_allowed(self) -> None:
        state = DecisionState.DRAFT
        path = [
            DecisionState.OBJECTIVE_DEFINED, DecisionState.OBSERVED, DecisionState.MODELLED,
            DecisionState.FORECASTED, DecisionState.COORDINATED, DecisionState.AUTHORISED,
            DecisionState.EXECUTED, DecisionState.VERIFYING, DecisionState.VERIFIED,
            DecisionState.CLOSED,
        ]
        for nxt in path:
            state = advance(state, nxt)
        assert state is DecisionState.CLOSED
