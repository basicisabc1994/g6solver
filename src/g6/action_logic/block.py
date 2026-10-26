# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""action_logic block: the generic-invoke surface over the Dörner control floor (W1).

Every op rebuilds the library's frozen records from the caller's JSON and calls the existing
deterministic control unchanged; nothing here re-implements a control. Severity stays in the
action gate's vocabulary (``notice < review < block``). Exposing a control here does not wire it
into the live gate (see the ``g6.action_logic`` docstring for what ``decide_gate`` calls).

``assess_material_action`` takes ``is_material`` from the caller, so its answer is labelled
``advisory_declared_materiality`` (audit 003, AL-009): it is not an authorisation. The action
gate derives materiality from the action itself.

Not exposed (and why). None of these breaks a safety rule; each is covered by an exposed op:
- ``evaluate_verification_contract``, ``evaluate_coordination``, ``assess_weak_signals``,
  ``is_deteriorating``: single legs of ``assess_material_action``, which runs them together.
- ``reauthorisation_severity_from_registry``: ``reauthorisation_severity`` with a dict lookup in
  front; a stateless surface has no hypothesis registry.
- ``advance``, ``can_transition``: re-exported core decision-lifecycle primitives, not this
  package's computation.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Literal

from g6.action_logic.controls import (
    assess_material_action,
    check_crisis_monitoring,
    detect_conflicts,
    sequence_if_separable,
)
from g6.action_logic.hypotheses import (
    apply_disconfirming_evidence,
    disconfirming_evidence_severity,
    reauthorisation_severity,
    update_on_outcome,
)
from g6.action_logic.schema import (
    ActionIntent,
    ControlVariable,
    Hypothesis,
    HypothesisStatus,
    MonitoringCoverage,
    Outcome,
    VerificationContract,
    WorldState,
)
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result
from pydantic import BaseModel, ConfigDict, Field

#: Refusal codes: a field an op or a record needs is missing; a field holds what its record cannot take.
E_FIELD_REQUIRED = "G6_E_ACTION_LOGIC_FIELD_REQUIRED"
E_INVALID_FIELD = "G6_E_ACTION_LOGIC_INVALID_FIELD"


class _Refused(ValueError):
    """A refusal that names its code and the field at fault, so no internal error text reaches the caller."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

_OPS: dict[str, tuple[tuple[str, ...], str]] = {
    "assess_material_action": (
        ("is_material", "contract", "proposed", "active", "budgets", "world"),
        "Combined verification-contract + coordination + weak-signal verdict for one action.",
    ),
    "detect_conflicts": (
        ("proposed", "active", "budgets"),
        "Collisions between a proposed action and active ones (G6-INV-08).",
    ),
    "sequence_if_separable": (
        ("actions", "budgets"),
        "A safe serial order of action ids, or null when a collision cannot be sequenced away.",
    ),
    "check_crisis_monitoring": (
        ("baseline", "crisis"),
        "Block when crisis monitoring coverage drops below the baseline (G6-INV-06).",
    ),
    "update_on_outcome": (
        ("hypothesis", "outcome", "evidence_text"),
        "Apply the §11 reflection rules to a hypothesis after an observed outcome.",
    ),
    "apply_disconfirming_evidence": (
        ("hypothesis", "note"),
        "Record disconfirming evidence and reduce confidence without falsifying (AT-08).",
    ),
    "disconfirming_evidence_severity": (
        ("hypothesis",),
        "Review when an in-use hypothesis carries disconfirming evidence (AT-08).",
    ),
    "reauthorisation_severity": (
        ("relied_hypothesis", "new_hypothesis"),
        "Block reliance on a falsified hypothesis unless a new evidenced one replaces it (AT-12).",
    ),
}

ActionLogicOp = Literal[
    "ops",
    "help",
    "assess_material_action",
    "detect_conflicts",
    "sequence_if_separable",
    "check_crisis_monitoring",
    "update_on_outcome",
    "apply_disconfirming_evidence",
    "disconfirming_evidence_severity",
    "reauthorisation_severity",
]


class ActionLogicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a misspelt parameter is refused, never dropped

    op: ActionLogicOp = "ops"
    is_material: bool = True
    contract: dict[str, Any] | None = None
    proposed: dict[str, Any] | None = None
    active: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    budgets: dict[str, float] | None = None
    world: dict[str, Any] | None = None
    baseline: dict[str, Any] = Field(default_factory=dict)
    crisis: dict[str, Any] = Field(default_factory=dict)
    hypothesis: dict[str, Any] = Field(default_factory=dict)
    outcome: str = ""
    evidence_text: str = ""
    note: str = ""
    relied_hypothesis: dict[str, Any] = Field(default_factory=dict)
    new_hypothesis: dict[str, Any] | None = None


def _tuples(value: Any) -> Any:
    """JSON lists (nested, e.g. ``costs``) become the tuples the frozen records declare."""
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    return value


def _fields_ok(cls: type, record: Any, where: str) -> None:
    """Refuse, naming the fields, a record that is not an object, lacks a field ``cls`` needs or has one it lacks."""
    if not isinstance(record, dict):
        raise _Refused(E_INVALID_FIELD, f"{where} must be an object")
    known = {f.name: f for f in dataclasses.fields(cls)}
    missing = [f"{where}.{name}" for name, f in known.items() if name not in record
               and f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING]
    if missing:
        raise _Refused(E_FIELD_REQUIRED, " and ".join(missing) + (" is" if len(missing) == 1 else " are") + " required")
    unknown = [f"{where}.{name}" for name in record if name not in known]
    if unknown:
        raise _Refused(E_INVALID_FIELD, f"{cls.__name__} has no field {', '.join(unknown)}")


def _record(cls: type, record: dict[str, Any], where: str) -> Any:
    _fields_ok(cls, record, where)
    return cls(**{key: _tuples(value) for key, value in record.items()})


def _world(record: dict[str, Any]) -> WorldState:
    _fields_ok(WorldState, record, "world")
    fields = {key: _tuples(value) for key, value in record.items()}
    fields["variables"] = tuple(_record(ControlVariable, v, f"world.variables[{i}]")
                                for i, v in enumerate(record.get("variables", ())))
    return WorldState(**fields)


def _enum(kind: type, value: Any, where: str) -> Any:
    try:
        return kind(value)
    except ValueError:
        raise _Refused(E_INVALID_FIELD, f"{where} must be one of {', '.join(m.value for m in kind)}") from None


def _hypothesis(record: dict[str, Any], where: str) -> Hypothesis:
    _fields_ok(Hypothesis, record, where)
    fields = {key: _tuples(value) for key, value in record.items()}
    if "status" in fields:
        fields["status"] = _enum(HypothesisStatus, fields["status"], f"{where}.status")
    return Hypothesis(**fields)


def _triple(found: tuple) -> dict[str, Any]:
    severity, reasons, item = found
    return {"severity": severity, "reason_codes": list(reasons), "item": item}


def _actions(records: list[dict[str, Any]], where: str) -> list[ActionIntent]:
    return [_record(ActionIntent, a, f"{where}[{i}]") for i, a in enumerate(records)]


def _assess(data: ActionLogicInput) -> dict[str, Any]:
    return assess_material_action(
        is_material=data.is_material,
        contract=None if data.contract is None else _record(VerificationContract, data.contract, "contract"),
        proposed=None if data.proposed is None else _record(ActionIntent, data.proposed, "proposed"),
        active=_actions(data.active, "active"),
        budgets=data.budgets,
        world=None if data.world is None else _world(data.world),
    ).to_dict()


def _detect_conflicts(data: ActionLogicInput) -> list[dict[str, Any]]:
    if data.proposed is None:
        raise _Refused(E_FIELD_REQUIRED, "proposed is required")
    found = detect_conflicts(
        _record(ActionIntent, data.proposed, "proposed"),
        _actions(data.active, "active"),
        budgets=data.budgets,
    )
    return [conflict.to_dict() for conflict in found]


def _sequence(data: ActionLogicInput) -> list[str] | None:
    return sequence_if_separable(_actions(data.actions, "actions"), budgets=data.budgets)


def _crisis(data: ActionLogicInput) -> dict[str, Any]:
    return _triple(check_crisis_monitoring(
        _record(MonitoringCoverage, data.baseline, "baseline"), _record(MonitoringCoverage, data.crisis, "crisis")
    ))


def _update(data: ActionLogicInput) -> dict[str, Any]:
    hypothesis = _hypothesis(data.hypothesis, "hypothesis")
    if not data.outcome:
        raise _Refused(E_FIELD_REQUIRED, "outcome is required")
    return update_on_outcome(
        hypothesis, _enum(Outcome, data.outcome, "outcome"), evidence=data.evidence_text
    ).to_dict()


def _disconfirm(data: ActionLogicInput) -> dict[str, Any]:
    return apply_disconfirming_evidence(_hypothesis(data.hypothesis, "hypothesis"), data.note).to_dict()


def _disconfirm_severity(data: ActionLogicInput) -> dict[str, Any]:
    return _triple(disconfirming_evidence_severity(_hypothesis(data.hypothesis, "hypothesis")))


def _reauthorisation(data: ActionLogicInput) -> dict[str, Any]:
    new = None if data.new_hypothesis is None else _hypothesis(data.new_hypothesis, "new_hypothesis")
    return _triple(reauthorisation_severity(
        relied_hypothesis=_hypothesis(data.relied_hypothesis, "relied_hypothesis"), new_hypothesis=new
    ))


_HANDLERS = {
    "assess_material_action": _assess,
    "detect_conflicts": _detect_conflicts,
    "sequence_if_separable": _sequence,
    "check_crisis_monitoring": _crisis,
    "update_on_outcome": _update,
    "apply_disconfirming_evidence": _disconfirm,
    "disconfirming_evidence_severity": _disconfirm_severity,
    "reauthorisation_severity": _reauthorisation,
}


def _catalogue(component: str, op: str) -> dict[str, Any]:
    if op == "ops":
        params = {name: list(names) for name, (names, _line) in _OPS.items()}
        return {"component": component, "op": op, "ops": list(_OPS), "params": params}
    lines = [f"{name}({', '.join(names)}): {line}" for name, (names, line) in _OPS.items()]
    return {"component": component, "op": op, "help": lines}


@component_maturity(
    "beta",
    reason="W1 generic-invoke block over the deterministic Dörner controls; no live-gate wiring",
)
class ActionLogicBlock(AIBlock[ActionLogicInput, dict, dict]):
    """Deterministic Dörner action-logic controls over caller-declared actions and hypotheses."""

    def __init__(self, name: str = "action_logic", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: ActionLogicInput) -> Result[dict]:
        if data.op in ("ops", "help"):
            return Result.ok(_catalogue(self.name, data.op))
        handler = _HANDLERS[data.op]  # the input's op is one of _HANDLERS, ops or help
        try:
            out = {"component": self.name, "op": data.op, "result": handler(data)}
        except _Refused as exc:
            return Result.fail(f"[{exc.code}] {data.op}: {exc}")
        except (ValueError, TypeError, KeyError) as exc:  # a value a control cannot read, named by the control
            return Result.fail(f"[{E_INVALID_FIELD}] {data.op}: {exc}")
        if data.op == "assess_material_action":
            out["materiality"] = "advisory_declared_materiality"
        return Result.ok(out)


__all__ = ["ActionLogicBlock", "ActionLogicInput"]
