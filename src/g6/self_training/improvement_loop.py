# SPDX-License-Identifier: Apache-2.0
"""``run_improvement_cycle``: sense, propose, evaluate, gate, apply, remeasure, and roll back on a regression.

Every stage is a callable you pass in; nothing here calls a model, a shell or the network (the archive's Codex proposer
is not ported). The decision is :func:`~g6.self_training.patch_promotion_gate.govern_patch_promotion`'s, unchanged:
the cycle applies a patch only when that decision is ``auto_promotable`` (``promote_auto`` or ``canary``) and was made
on exactly this patch (``decision_matches``). It has no slice to canary on, so a canary is applied in full, like
``promote_auto`` (audit 008, ST-007); an ``apply_fn`` that wants a slice applies to one.

Off unless ``enabled=True``. The gate is asked with ``autonomous_low_risk=False`` unless the caller passes ``True``, so
by default every patch needs a person and nothing is applied. It applies only with ``apply_enabled=True`` and an
``apply_fn``, and then a ``rollback_fn``, a ``remeasure_fn`` and a ``regressed_fn`` are required too, before anything
runs, so no patch is applied that cannot be undone or will not be measured again (ST-007). ``apply_fn(patch)`` puts
the patch in place and ``rollback_fn(patch)`` undoes it. An ``apply_fn`` that raises may leave the patch partly in
place, so the cycle rolls it back (review B1-6). After applying, the cycle remeasures; a remeasure that raises, a
regression, or a ``regressed_fn`` that answers anything but ``True`` or ``False`` rolls the patch back.
The result keeps the states apart: ``applied`` (``apply_fn`` returned, and stays true after a rollback),
``rollback_attempted``, ``rolled_back`` and ``rollback_failed``. A rollback that raises is reported as applied, with
``rollback_failed`` and ``G6_E_SELF_TRAINING_ROLLBACK_FAILED`` (ST-006: the archive swallowed it and said not applied).

The process-wide stop (``g6.core.stop``) is read before sensing and again before proposing, evaluating and applying;
while it is engaged nothing more starts and the result is ``blocked-escalated`` with ``G6_E_STOPPED`` (ST-015). Once
a patch is applied the cycle finishes its remeasure and any rollback before it answers, so a stop never leaves an
unmeasured change in place.

The best result is ``qualified-draft``: the evaluation and the remeasure are the caller's, not independently
witnessed, so no cycle result is stronger (audit 008, ST-001 and ST-007; DECISIONS #22).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from g6.self_training.patch_promotion_gate import (
    BLOCK,
    REJECT,
    HarnessPatch,
    PatchEvalReport,
    PromotionDecision,
    decision_matches,
    govern_patch_promotion,
)

SenseFn = Callable[[], Any]
ProposeFn = Callable[[Any], HarnessPatch | None]
EvaluateFn = Callable[[HarnessPatch], PatchEvalReport]
ApplyFn = Callable[[HarnessPatch], Any]
RollbackFn = Callable[[HarnessPatch], Any]
RemeasureFn = Callable[[], Any]
RegressedFn = Callable[[Any, Any], bool]
SnapshotFn = Callable[[HarnessPatch], Any]

QUALIFIED = "qualified-draft"
BLOCKED = "blocked-escalated"

# Stable codes on the result's warning card (``G6_E_STOPPED`` comes from g6.core.stop).
LOOP_OFF = "G6_E_SELF_TRAINING_LOOP_OFF"
REMEASURE_REQUIRED = "G6_E_SELF_TRAINING_REMEASURE_REQUIRED"
NO_PATCH = "G6_E_SELF_TRAINING_NO_PATCH"
GATE_REJECTED = "G6_E_SELF_TRAINING_GATE_REJECTED"
GATE_BLOCKED = "G6_E_SELF_TRAINING_GATE_BLOCKED"
REVIEW_REQUIRED = "G6_E_SELF_TRAINING_REVIEW_REQUIRED"
PROPOSE_ONLY = "G6_E_SELF_TRAINING_PROPOSE_ONLY"
SNAPSHOT_FAILED = "G6_E_SELF_TRAINING_SNAPSHOT_FAILED"
DECISION_MISMATCH = "G6_E_SELF_TRAINING_DECISION_MISMATCH"
APPLY_FAILED = "G6_E_SELF_TRAINING_APPLY_FAILED"
NO_ROLLBACK = "G6_E_SELF_TRAINING_NO_ROLLBACK"
REMEASURE_FAILED = "G6_E_SELF_TRAINING_REMEASURE_FAILED"
ROLLED_BACK = "G6_E_SELF_TRAINING_ROLLED_BACK"
ROLLBACK_FAILED = "G6_E_SELF_TRAINING_ROLLBACK_FAILED"
APPLIED_UNWITNESSED = "G6_E_SELF_TRAINING_APPLIED_UNWITNESSED"
CYCLE_FAILED = "G6_E_SELF_TRAINING_CYCLE_FAILED"


@dataclass
class ImprovementCycleResult:
    """What one cycle did. ``stage`` is where it answered; ``code`` and ``message`` go on the warning card."""

    stage: str
    completion_state: str = QUALIFIED
    code: str = ""
    message: str = ""
    enabled: bool = False
    sensed: Any = None
    patch: HarnessPatch | None = None
    eval_report: PatchEvalReport | None = None
    decision: PromotionDecision | None = None
    applied: bool = False              # apply_fn returned: the patch was put in place
    remeasure_ran: bool = False
    remeasured: Any = None
    regressed: bool | None = None      # None when the remeasure gave no True/False answer
    rollback_attempted: bool = False
    rolled_back: bool = False          # the rollback returned
    rollback_failed: bool = False      # the rollback raised: the patch may still be in effect
    reasons: list[str] = field(default_factory=list)
    request_id: str = ""
    run_id: str = ""

    @property
    def reliability_label(self) -> str:
        return self.completion_state

    @property
    def patch_in_effect(self) -> bool | None:
        """Whether the patch is in place now; ``None`` when the cycle cannot tell (a rollback raised, or an apply raised
        and was not rolled back)."""
        if self.rollback_failed or (self.code == APPLY_FAILED and not self.rolled_back):
            return None
        return self.applied and not self.rolled_back

    def _evidence(self) -> list[dict[str, Any]]:
        found = []
        if self.decision is not None:
            found.append({"source": "govern_patch_promotion", "summary": f"{self.decision.decision} "
                          f"({self.decision.risk_tier})", "patch_digest": self.decision.patch_digest})
        if self.remeasure_ran:
            found.append({"source": "remeasure_fn", "summary": f"regressed={self.regressed}",
                          "remeasured": self.remeasured})
        return found

    def to_dict(self) -> dict[str, Any]:
        """The library envelope (``completion_state``, ``warning_card``, ``evidence``, ``request_id``, ``run_id``)
        with the cycle's fields."""
        from g6.core.reliability_envelope import envelope_fields, warning_card

        envelope = envelope_fields(reliability_label=self.completion_state, request_id=self.request_id,
                                   run_id=self.run_id, evidence=self._evidence())
        envelope["warning_card"] = warning_card(self.code, self.message)
        return {
            **envelope,
            "stage": self.stage, "enabled": self.enabled,
            "sensed": self.sensed,
            "patch": asdict(self.patch) if self.patch is not None else None,
            "eval_report": asdict(self.eval_report) if self.eval_report is not None else None,
            "decision": self.decision.to_dict() if self.decision is not None else None,
            "applied": self.applied, "remeasure_ran": self.remeasure_ran, "remeasured": self.remeasured,
            "regressed": self.regressed, "rollback_attempted": self.rollback_attempted,
            "rolled_back": self.rolled_back, "rollback_failed": self.rollback_failed,
            "patch_in_effect": self.patch_in_effect,
            "rollback_method": self.patch.rollback_method if self.patch is not None else None,
            "reasons": list(self.reasons),
        }


class _Halt(Exception):
    """Ends the cycle early; the result is already filled in."""


def _finish(r: ImprovementCycleResult, state: str, code: str, message: str) -> ImprovementCycleResult:
    r.completion_state, r.code, r.message = _cap(state), code, message
    return r


def _cap(state: str) -> str:
    # audit 008, ST-001 / ST-007: a cycle result is a qualified draft at best, whatever label it was handed.
    return BLOCKED if state == BLOCKED else QUALIFIED


def _stop_reason() -> str | None:
    from g6.core import stop   # imported here so the package's pure modules load no g6.core (audit 024 REL-012)

    return stop.engaged()


def _check_stop(r: ImprovementCycleResult, before: str) -> None:
    # audit 008, ST-015: the archive read the kill switch only in its block wrapper, never in the exported loop.
    reason = _stop_reason()
    if reason is not None:
        from g6.core.stop import STOPPED

        r.stage = before
        _finish(r, BLOCKED, STOPPED, f"G6 is stopped ({reason}); the cycle did not start {before}")
        raise _Halt


def _remeasure_missing(remeasure_fn: RemeasureFn | None, regressed_fn: RegressedFn | None) -> bool:
    return remeasure_fn is None or regressed_fn is None   # audit 008, ST-007: remeasure is mandatory to apply


def _snapshot_failed(snapshot_fn: SnapshotFn, patch: HarnessPatch) -> bool:
    """A snapshot fails if it raises, returns ``None`` / ``False``, or returns a result whose ``is_fail()`` is true."""
    try:
        snap = snapshot_fn(patch)
        is_fail = getattr(snap, "is_fail", None)
        return bool(is_fail()) if callable(is_fail) else snap is None or snap is False
    except Exception:  # noqa: BLE001 - a snapshot that cannot be taken or read is a failed snapshot
        return True


def _roll_back(r: ImprovementCycleResult, rollback: Callable[[], None]) -> None:
    r.rollback_attempted = True
    try:
        rollback()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed (audit 008, ST-006)
        r.rollback_failed = True
        r.reasons.append(f"the rollback raised {type(exc).__name__}: {exc}; the patch may still be in effect")
        return
    r.rolled_back = True


def _apply_phase(r: ImprovementCycleResult, patch: HarnessPatch, apply_fn: ApplyFn, rollback_fn: RollbackFn,
                 remeasure_fn: RemeasureFn, regressed_fn: RegressedFn) -> ImprovementCycleResult:
    """apply, remeasure, and roll back unless the remeasure says no regression. ``r`` is filled in as each step
    happens, so its apply states stay true even if a later step raises (audit 008, ST-006)."""
    def rollback() -> None:
        rollback_fn(patch)

    r.stage = "apply"
    try:
        apply_fn(patch)
    except Exception as exc:  # noqa: BLE001
        r.reasons.append(f"apply_fn raised {type(exc).__name__}: {exc}")
        # Review B1-6: the patch may be partly applied, and this cycle holds the rollback, so it uses it.
        r.stage = "rollback"
        _roll_back(r, rollback)
        if r.rollback_failed:
            return _finish(r, BLOCKED, ROLLBACK_FAILED, "apply_fn raised, so the patch may be partly applied, and the "
                           "rollback raised too: restore it by hand or from your snapshot")
        return _finish(r, BLOCKED, APPLY_FAILED, "apply_fn raised, so the patch may have been partly applied; the "
                       "cycle rolled it back with your rollback_fn")
    r.applied = True   # audit 008, ST-006: from here on the result never says the patch was not applied

    r.stage = "remeasure"
    try:
        r.remeasured = remeasure_fn()
        r.remeasure_ran = True
        verdict = regressed_fn(r.sensed, r.remeasured)
    except Exception as exc:  # noqa: BLE001 - an unmeasured change does not stay in place
        r.reasons.append(f"the remeasure raised {type(exc).__name__}: {exc}")
        r.stage = "rollback"
        _roll_back(r, rollback)
        if r.rollback_failed:
            return _finish(r, BLOCKED, ROLLBACK_FAILED, "the remeasure failed and the rollback raised: the patch was "
                           "applied and may still be in effect; restore it by hand or from your snapshot")
        return _finish(r, BLOCKED, REMEASURE_FAILED, "the remeasure failed, so the patch was rolled back")
    if verdict is True or verdict is False:
        r.regressed = verdict
    else:
        r.reasons.append(f"regressed_fn answered {verdict!r}, not True or False; treated as a regression")
    if r.regressed is False:
        r.stage = "done"
        return _finish(r, QUALIFIED, APPLIED_UNWITNESSED, "applied, remeasured and kept; the evaluation and the "
                       "remeasure are the caller's, not independently witnessed, so this is a qualified draft")
    r.stage = "rollback"
    _roll_back(r, rollback)
    if r.rollback_failed:
        return _finish(r, BLOCKED, ROLLBACK_FAILED, "the remeasure showed a regression and the rollback raised: the "
                       "patch was applied and may still be in effect; restore it by hand or from your snapshot")
    return _finish(r, BLOCKED, ROLLED_BACK, "the remeasure showed a regression, so the patch was rolled back")


def run_improvement_cycle(
    *,
    sense_fn: SenseFn,
    propose_fn: ProposeFn,
    evaluate_fn: EvaluateFn,
    apply_fn: ApplyFn | None = None,
    rollback_fn: RollbackFn | None = None,
    remeasure_fn: RemeasureFn | None = None,
    regressed_fn: RegressedFn | None = None,
    snapshot_fn: SnapshotFn | None = None,
    enabled: bool = False,
    apply_enabled: bool = False,
    autonomous_low_risk: bool = False,
    request_id: str = "",
    run_id: str = "",
) -> ImprovementCycleResult:
    """Run one cycle; it never raises. ``regressed_fn(sensed, remeasured)`` answers whether the patch made it worse."""
    r = ImprovementCycleResult(stage="start", enabled=bool(enabled), request_id=request_id or uuid.uuid4().hex[:12],
                               run_id=run_id or uuid.uuid4().hex)
    try:
        return _cycle(r, sense_fn, propose_fn, evaluate_fn, apply_fn, rollback_fn, remeasure_fn, regressed_fn,
                      snapshot_fn, bool(apply_enabled), autonomous_low_risk)
    except _Halt:
        return r
    except Exception as exc:  # noqa: BLE001 - the cycle fails closed; the states recorded so far stand
        r.reasons.append(f"{type(exc).__name__}: {exc}")
        return _finish(r, BLOCKED, CYCLE_FAILED, f"the cycle failed at {r.stage}: {type(exc).__name__}: {exc}")


def _cycle(r: ImprovementCycleResult, sense_fn: SenseFn, propose_fn: ProposeFn, evaluate_fn: EvaluateFn,
           apply_fn: ApplyFn | None, rollback_fn: RollbackFn | None, remeasure_fn: RemeasureFn | None,
           regressed_fn: RegressedFn | None, snapshot_fn: SnapshotFn | None, apply_enabled: bool,
           autonomous_low_risk: bool) -> ImprovementCycleResult:
    _check_stop(r, "sense")
    if not r.enabled:
        r.stage = "off"
        return _finish(r, QUALIFIED, LOOP_OFF, "the improvement cycle is off (pass enabled=True); nothing ran")
    applying = apply_enabled and apply_fn is not None
    if applying and _remeasure_missing(remeasure_fn, regressed_fn):
        r.stage = "configure"
        return _finish(r, BLOCKED, REMEASURE_REQUIRED, "apply is enabled, so remeasure_fn and regressed_fn are "
                       "required: a patch is never applied without being measured again; nothing ran")
    if applying and not callable(rollback_fn):
        r.stage = "configure"
        return _finish(r, BLOCKED, NO_ROLLBACK, "apply is enabled, so rollback_fn is required: a patch is never "
                       "applied without a way to undo it; nothing ran")

    r.stage = "sense"
    r.sensed = sense_fn()

    _check_stop(r, "propose")
    r.stage = "propose"
    patch = propose_fn(r.sensed)
    if patch is None:
        return _finish(r, QUALIFIED, NO_PATCH, "the proposer proposed no patch")
    if not isinstance(patch, HarnessPatch):
        raise TypeError(f"propose_fn returned {type(patch).__name__}, not a HarnessPatch")
    r.patch = patch

    _check_stop(r, "evaluate")
    r.stage = "evaluate"
    report = evaluate_fn(patch)
    if not isinstance(report, PatchEvalReport):
        raise TypeError(f"evaluate_fn returned {type(report).__name__}, not a PatchEvalReport")
    r.eval_report = report

    r.stage = "gate"
    decision = govern_patch_promotion(patch, report, autonomous_low_risk=autonomous_low_risk)
    r.decision = decision
    r.reasons = list(decision.reasons)
    if decision.decision == BLOCK:
        return _finish(r, BLOCKED, GATE_BLOCKED, "the promotion gate blocked the patch; nothing was applied")
    if decision.decision == REJECT:
        return _finish(r, BLOCKED, GATE_REJECTED, "the patch failed the promotion criteria; nothing was applied")
    if not decision.auto_promotable:
        return _finish(r, decision.reliability_label, REVIEW_REQUIRED,
                       f"the promotion gate requires a person ({decision.decision}); nothing was applied")
    if not applying:
        return _finish(r, decision.reliability_label, PROPOSE_ONLY,
                       f"the promotion gate allows {decision.decision}, but applying is off (apply_enabled and "
                       "apply_fn); nothing was applied")

    _check_stop(r, "apply")
    r.stage = "snapshot"
    if snapshot_fn is not None and _snapshot_failed(snapshot_fn, patch):
        return _finish(r, BLOCKED, SNAPSHOT_FAILED, "the pre-apply snapshot failed; nothing was applied")
    if not decision_matches(decision, patch):
        return _finish(r, BLOCKED, DECISION_MISMATCH, "the patch changed after the promotion gate decided on it "
                       "(audit 008, ST-018); nothing was applied")
    return _apply_phase(r, patch, apply_fn, rollback_fn, remeasure_fn, regressed_fn)


def make_no_eval_report() -> PatchEvalReport:
    """An honest stand-in evaluator result for when nothing evaluated the patch: the gate rejects it."""
    return PatchEvalReport()


__all__ = ["APPLIED_UNWITNESSED", "APPLY_FAILED", "CYCLE_FAILED", "DECISION_MISMATCH", "GATE_BLOCKED",
           "GATE_REJECTED", "LOOP_OFF", "NO_PATCH", "NO_ROLLBACK", "PROPOSE_ONLY", "REMEASURE_FAILED",
           "REMEASURE_REQUIRED", "REVIEW_REQUIRED", "ROLLBACK_FAILED", "ROLLED_BACK", "SNAPSHOT_FAILED",
           "ImprovementCycleResult", "make_no_eval_report", "run_improvement_cycle"]
