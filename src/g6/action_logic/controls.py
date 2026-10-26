# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Deterministic Dörner action-logic controls (G6-INV-05/06/08, §9).

This is a DETERMINISTIC floor — no LLM. It operationalises the closed-loop-control
invariants of ``G6_Action_Logic.md`` (Dörner, *The Logic of Failure*): a material action
must not be "ballistic" (taken without a plan to verify its effects), must be coordinated
against other live interventions, must account for deteriorating weak signals and at least
one future horizon, and monitoring must not collapse in a crisis.

It mirrors ``mvp.epistemic_integrity.adjudicator``: pure functions returning a severity in
the action gate's own vocabulary (``notice < review < block``) plus reason codes and
structured evidence items, so the controls compose monotonically into
``mvp.action_gating.decision_engine.decide_gate``. The companion deterministic decision
lifecycle (``mvp.core.decision_record``) and objective contract
(``mvp.core.objective_contract``) supply the state-machine and objective primitives this
floor enforces.

Raw-LLM delta (why this is *more than a raw LLM*): a raw model asked to take a side-effecting
action emits the action with no verification window, no rollback, no monitor, and no notion
of the other actions already in flight — it acts ballistically and treats the system as a
single snapshot. These controls block exactly those failures structurally.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from g6.action_logic.schema import (
    ActionConflict,
    ActionIntent,
    ActionLogicAssessment,
    ControlVariable,
    MonitoringCoverage,
    VerificationContract,
    WorldState,
    raise_severity,
)

# Reason codes (registered in core/error_codes.yaml).
E_BALLISTIC = "G6_E_BALLISTIC_ACTION_BLOCKED"
E_COLLISION = "G6_E_ACTION_COLLISION"
E_NO_HORIZON = "G6_E_NO_FUTURE_HORIZON"
E_WEAK_SIGNAL = "G6_E_UNADDRESSED_WEAK_SIGNAL"
E_CRISIS_MONITORING = "G6_E_CRISIS_MONITORING_DEGRADED"
E_CRISIS_MONITORING_UNKNOWN = "G6_E_CRISIS_MONITORING_UNKNOWN"
E_THRESHOLD_BREACHED = "G6_E_THRESHOLD_ALREADY_BREACHED"


# --------------------------------------------------------------------------- #
# G6-INV-05 — closed-loop action / ballistic-action prevention (AT-06)
# --------------------------------------------------------------------------- #

def evaluate_verification_contract(
    *, is_material: bool, contract: VerificationContract | None
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """A material action without a COMPLETE verification plan is ballistic -> BLOCK (G6-INV-05).

    Returns ``(severity, reason_codes, item)``. A non-material action is out of scope for
    this control (``None``). The spec is categorical ("An action without a verification plan
    MUST be blocked"); the standalone floor therefore returns ``block`` for any material
    action whose contract is missing or incomplete. Deployment staging (which risk tiers
    fail closed *now* vs. observe/soft) is applied by the gate wiring, not here.

    ``complete`` means every element is present (syntactically complete). Nothing here checks
    that the monitor, stop threshold or rollback exist and work, so ``operationally_verified``
    is always false (audit 003, AL-011).
    """
    if not is_material:
        return None, [], None
    missing = (
        ("contract_absent",)
        if contract is None
        else contract.missing_elements()
    )
    if missing:
        item = {
            "kind": "verification_contract",
            "complete": False,
            "missing_elements": list(missing),
            "operationally_verified": False,
        }
        return "block", [E_BALLISTIC], item
    return None, [], {
        "kind": "verification_contract", "complete": True, "missing_elements": [], "operationally_verified": False,
    }


# --------------------------------------------------------------------------- #
# G6-INV-08 — coordinated interventions (AT-05)
# --------------------------------------------------------------------------- #

# A bucket token: a letter, then letters, digits, "_" or "-" (e.g. "w1", "nightly-batch").
_BUCKET = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _interval(window: str) -> tuple[datetime, datetime] | None:
    """``start/end`` as two ISO-8601 datetimes with start before end, else ``None``."""
    start, sep, end = window.partition("/")
    if not sep:
        return None
    try:
        lo, hi = datetime.fromisoformat(start.strip()), datetime.fromisoformat(end.strip())
        return (lo, hi) if lo < hi else None
    except (TypeError, ValueError):  # unparseable, or one timezone-aware and one naive
        return None


def _windows_overlap(a: str, b: str) -> bool:
    """Timing overlap (audit 003, AL-010). Two window forms are supported:

    - an ISO-8601 interval ``start/end`` (``2026-10-18T10:00/2026-10-18T12:00``): the
      half-open intervals are compared;
    - a bucket token (``w1``, ``nightly``): equal tokens (ignoring case) overlap; different
      tokens are the caller's statement that the windows are separate.

    Anything else — empty, a time written another way (``10:00-12:00``), or one interval and
    one token — cannot be proven separate, so it overlaps (fail-safe). The archive treated
    any two different strings as separate.
    """
    a, b = a.strip(), b.strip()
    ia, ib = _interval(a), _interval(b)
    if ia and ib:
        try:
            return ia[0] < ib[1] and ib[0] < ia[1]
        except TypeError:  # one timezone-aware, one naive: not comparable
            return True
    if _BUCKET.fullmatch(a) and _BUCKET.fullmatch(b):
        return a.casefold() == b.casefold()
    return True


def _cost_map(intent: ActionIntent) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, cost in intent.costs:
        out[name] = out.get(name, 0.0) + cost
    return out


def detect_conflicts(
    proposed: ActionIntent,
    active: Iterable[ActionIntent],
    *,
    budgets: dict[str, float] | None = None,
) -> tuple[ActionConflict, ...]:
    """Detect collisions between a proposed action and active/scheduled ones (G6-INV-08).

    Checks the spec's collision classes that are decidable from *declared* intent fields:
    contradictory objectives, duplicate effects, shared-resource contention, combined
    threshold breaches against a resource budget, and assumptions invalidated by a live
    action. (Feedback amplification and fine-grained timing are folded into shared-resource
    + window overlap; full modelling is a maturity follow-up, recorded honestly.)

    An active entry identical to the proposed action is the action itself and is skipped. An
    active entry that shares the proposed id but declares something different is an
    ``identity_collision`` and is still compared (audit 003, AL-008: the archive skipped every
    same-id entry, which hid a conflicting action behind a reused id).
    """
    budgets = budgets or {}
    conflicts: list[ActionConflict] = []
    prop_cost = _cost_map(proposed)
    prop_shared = set(proposed.shared_resources)
    prop_relies = set(proposed.relies_on_assumptions)

    active_list: list[ActionIntent] = []
    for other in active:
        if other.action_id == proposed.action_id:
            if other == proposed:
                continue
            conflicts.append(ActionConflict(
                "identity_collision", other.action_id, "same action_id, different declared action",
            ))
        active_list.append(other)
    for other in active_list:
        overlap = _windows_overlap(proposed.window, other.window)
        same_target = bool(proposed.target_resource) and proposed.target_resource == other.target_resource
        if same_target and overlap:
            if proposed.direction and other.direction and proposed.direction != other.direction:
                conflicts.append(ActionConflict(
                    "contradictory_objectives", other.action_id,
                    f"{proposed.direction} vs {other.direction} on {proposed.target_resource}",
                ))
            if proposed.effect_signature and proposed.effect_signature == other.effect_signature:
                conflicts.append(ActionConflict(
                    "duplicate_effects", other.action_id,
                    f"duplicate '{proposed.effect_signature}' on {proposed.target_resource}",
                ))
        shared = prop_shared & set(other.shared_resources)
        if shared and overlap:
            conflicts.append(ActionConflict(
                "resource_contention", other.action_id, ",".join(sorted(shared)),
            ))
        invalidated = prop_relies & set(other.invalidates_assumptions)
        if invalidated:
            conflicts.append(ActionConflict(
                "invalidated_assumption", other.action_id, ",".join(sorted(invalidated)),
            ))

    # Combined-threshold: proposed + all overlapping active draws on a budgeted resource.
    for resource, budget in budgets.items():
        total = prop_cost.get(resource, 0.0)
        for other in active_list:
            if _windows_overlap(proposed.window, other.window):
                total += _cost_map(other).get(resource, 0.0)
        if total > budget:
            conflicts.append(ActionConflict(
                "combined_threshold_breach", "*",
                f"{resource}: combined {total:g} > budget {budget:g}",
            ))
    return tuple(conflicts)


def evaluate_coordination(
    proposed: ActionIntent,
    active: Iterable[ActionIntent],
    *,
    is_material: bool,
    budgets: dict[str, float] | None = None,
) -> tuple[str | None, list[str], list[dict[str, Any]]]:
    """An unresolved collision on a material action is uncoordinated action -> BLOCK (AT-05)."""
    conflicts = detect_conflicts(proposed, active, budgets=budgets)
    if not conflicts:
        return None, [], []
    items = [c.to_dict() for c in conflicts]
    if is_material:
        return "block", [E_COLLISION], items
    return "notice", [E_COLLISION], items


def sequence_if_separable(
    actions: list[ActionIntent], *, budgets: dict[str, float] | None = None
) -> list[str] | None:
    """AT-05's "coordinated sequence" branch.

    If the only collisions among ``actions`` are *separable* (shared-resource / combined-budget /
    timing — resolvable by running them one at a time), return a safe serial ordering of
    action ids. Return ``None`` (must block, not sequence) when ordering cannot help:

    - contradictory objectives or an invalidated assumption;
    - one action alone exceeds a budget (running it by itself still breaches);
    - a duplicate effect that is not declared idempotent with a shared ``idempotency_key``
      (a second charge or send is a duplicate whichever order it runs in);
    - two entries share an action id (a schedule of ids cannot say which one runs).
    """
    budgets = budgets or {}
    if len({a.action_id for a in actions}) != len(actions):
        return None
    for a in actions:
        for resource, draw in _cost_map(a).items():
            if resource in budgets and draw > budgets[resource]:
                return None
    non_separable = {"contradictory_objectives", "invalidated_assumption"}
    by_id = {a.action_id: a for a in actions}
    for i, proposed in enumerate(actions):
        others = actions[:i] + actions[i + 1:]
        for c in detect_conflicts(proposed, others, budgets=budgets):
            if c.kind in non_separable:
                return None
            if c.kind == "duplicate_effects":
                other = by_id.get(c.with_action)
                if not (proposed.idempotency_key and other and other.idempotency_key == proposed.idempotency_key):
                    return None
    # Serial schedule: each action fits on its own, so one-at-a-time removes the contention.
    return [a.action_id for a in actions]


# --------------------------------------------------------------------------- #
# §9 — weak-signal / future-risk review (AT-01)
# --------------------------------------------------------------------------- #

def is_deteriorating(v: ControlVariable) -> bool:
    """A weak signal: a variable whose *current value is still acceptable* but whose trend
    moves it toward a known threshold (§9 q1 / AT-01). Requires value, threshold and trend.
    """
    if v.value is None or v.threshold is None:
        return False
    if v.is_resource:
        # A finite supporting resource depletes downward toward a floor it is still above.
        return v.trend == "falling" and v.value > v.threshold
    # A load/pressure variable rises toward a ceiling it is still below.
    return v.trend == "rising" and v.value < v.threshold


def is_breached(v: ControlVariable) -> bool:
    """The variable is already past its threshold: a resource below its floor, or a load
    above its ceiling. Requires value and threshold; equality is not a breach."""
    if v.value is None or v.threshold is None:
        return False
    return v.value < v.threshold if v.is_resource else v.value > v.threshold


def assess_weak_signals(
    world: WorldState, *, is_material: bool
) -> tuple[str | None, list[str], list[dict[str, Any]]]:
    """Block a material action that ignores deteriorating signals or evaluates no future horizon.

    Defeats Dörner's "reactive repair trap" (fix the visible fault, ignore the trend). A
    material action must (a) evaluate at least one future horizon beyond the immediate
    objective, and (b) not leave a deteriorating weak signal unacknowledged. An acknowledged
    deteriorating signal (explicitly represented/accepted) downgrades to a notice.
    """
    if not is_material:
        return None, [], []
    severity: str | None = None
    reasons: list[str] = []
    items: list[dict[str, Any]] = []

    if not world.horizons_evaluated:
        severity = raise_severity(severity, "block")
        reasons.append(E_NO_HORIZON)
        items.append({"kind": "future_horizon", "evaluated": []})

    for v in world.variables:
        if is_breached(v):
            # Not a weak signal — a present fault. Reported under its own code so the two
            # meanings stay distinct; acknowledgement does not soften a live breach.
            severity = raise_severity(severity, "block")
            reasons.append(E_THRESHOLD_BREACHED)
            items.append({
                "kind": "threshold_breach", "variable": v.name, "value": v.value,
                "threshold": v.threshold, "is_resource": v.is_resource,
            })
            continue
        if not is_deteriorating(v):
            continue
        item = {
            "kind": "weak_signal",
            "variable": v.name,
            "value": v.value,
            "threshold": v.threshold,
            "trend": v.trend,
            "acknowledged": v.acknowledged,
        }
        items.append(item)
        if v.acknowledged:
            severity = raise_severity(severity, "notice")
        else:
            severity = raise_severity(severity, "block")
            reasons.append(E_WEAK_SIGNAL)
    return severity, reasons, items


# --------------------------------------------------------------------------- #
# G6-INV-06 — monitoring survives crises (AT-07)
# --------------------------------------------------------------------------- #

def check_crisis_monitoring(
    baseline: MonitoringCoverage, crisis: MonitoringCoverage
) -> tuple[str | None, list[str], dict[str, Any]]:
    """Monitoring coverage in a crisis MUST be >= the pre-crisis baseline (G6-INV-06).

    A drop in any dimension is a critical control failure -> block. A shorter review
    interval (a HIGHER ``verification_per_hour``) is explicitly allowed.

    A baseline that records no coverage at all cannot show that coverage was preserved, so
    it is ``review`` with ``G6_E_CRISIS_MONITORING_UNKNOWN`` (audit 003, AL-005: the archive
    reported two empty records as preserved). Not called by any gate: no trusted crisis-state
    source exists in the library, so callers supply both records.
    """
    if baseline == MonitoringCoverage():
        return "review", [E_CRISIS_MONITORING_UNKNOWN], {"degraded": [], "unknown": ["baseline"]}
    degraded: list[str] = []
    if crisis.telemetry_signals < baseline.telemetry_signals:
        degraded.append("telemetry")
    if crisis.verification_per_hour < baseline.verification_per_hour:
        degraded.append("verification_frequency")
    if baseline.audit_logging and not crisis.audit_logging:
        degraded.append("audit_logging")
    if baseline.anomaly_detection and not crisis.anomaly_detection:
        degraded.append("anomaly_detection")
    if baseline.escalation_ready and not crisis.escalation_ready:
        degraded.append("escalation_readiness")
    if degraded:
        return "block", [E_CRISIS_MONITORING], {"degraded": degraded}
    return None, [], {"degraded": []}


# --------------------------------------------------------------------------- #
# Combined per-action assessment
# --------------------------------------------------------------------------- #

def assess_material_action(
    *,
    is_material: bool,
    contract: VerificationContract | None = None,
    proposed: ActionIntent | None = None,
    active: Iterable[ActionIntent] = (),
    budgets: dict[str, float] | None = None,
    world: WorldState | None = None,
) -> ActionLogicAssessment:
    """Run the per-action Dörner controls and combine them monotonically.

    Verification contract (G6-INV-05) + coordination (G6-INV-08) + weak-signal (§9). The
    combined severity is the MOST restrictive across controls (a single block blocks the
    action). Crisis monitoring (G6-INV-06) is a system-level invariant assessed separately
    via :func:`check_crisis_monitoring`.
    """
    severity: str | None = None
    reasons: list[str] = []
    items: list[dict[str, Any]] = []

    v_sev, v_reasons, v_item = evaluate_verification_contract(
        is_material=is_material, contract=contract
    )
    severity = raise_severity(severity, v_sev)
    reasons.extend(v_reasons)
    if v_item is not None:
        items.append(v_item)

    if proposed is not None:
        c_sev, c_reasons, c_items = evaluate_coordination(
            proposed, active, is_material=is_material, budgets=budgets
        )
        severity = raise_severity(severity, c_sev)
        reasons.extend(c_reasons)
        items.extend(c_items)

    if world is not None:
        w_sev, w_reasons, w_items = assess_weak_signals(world, is_material=is_material)
        severity = raise_severity(severity, w_sev)
        reasons.extend(w_reasons)
        items.extend(w_items)

    return ActionLogicAssessment(
        severity=severity,
        reason_codes=tuple(dict.fromkeys(reasons)),  # de-dup, order-preserving
        items=tuple(items),
    )


__all__ = [
    "E_BALLISTIC",
    "E_COLLISION",
    "E_NO_HORIZON",
    "E_WEAK_SIGNAL",
    "E_CRISIS_MONITORING",
    "E_CRISIS_MONITORING_UNKNOWN",
    "E_THRESHOLD_BREACHED",
    "is_breached",
    "evaluate_verification_contract",
    "detect_conflicts",
    "evaluate_coordination",
    "sequence_if_separable",
    "is_deteriorating",
    "assess_weak_signals",
    "check_crisis_monitoring",
    "assess_material_action",
]
