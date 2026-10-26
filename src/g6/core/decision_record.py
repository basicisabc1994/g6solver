# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Decision lifecycle records for G6 action logic."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DecisionState(str, Enum):
    DRAFT = "DRAFT"
    OBJECTIVE_DEFINED = "OBJECTIVE_DEFINED"
    OBSERVED = "OBSERVED"
    MODELLED = "MODELLED"
    FORECASTED = "FORECASTED"
    COORDINATED = "COORDINATED"
    AUTHORISED = "AUTHORISED"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    CLOSED = "CLOSED"
    REVISED = "REVISED"
    ROLLED_BACK = "ROLLED_BACK"


ALLOWED_TRANSITIONS: dict[DecisionState, tuple[DecisionState, ...]] = {
    DecisionState.DRAFT: (DecisionState.OBJECTIVE_DEFINED,),
    DecisionState.OBJECTIVE_DEFINED: (DecisionState.OBSERVED,),
    DecisionState.OBSERVED: (DecisionState.MODELLED,),
    DecisionState.MODELLED: (DecisionState.FORECASTED,),
    DecisionState.FORECASTED: (DecisionState.COORDINATED,),
    DecisionState.COORDINATED: (DecisionState.AUTHORISED,),
    DecisionState.AUTHORISED: (DecisionState.EXECUTED,),
    DecisionState.EXECUTED: (DecisionState.VERIFYING,),
    DecisionState.VERIFYING: (DecisionState.VERIFIED,),
    DecisionState.VERIFIED: (
        DecisionState.CLOSED,
        DecisionState.REVISED,
        DecisionState.ROLLED_BACK,
    ),
    DecisionState.CLOSED: (),
    DecisionState.REVISED: (),
    DecisionState.ROLLED_BACK: (),
}
PROHIBITED_TRANSITIONS: tuple[tuple[DecisionState, DecisionState], ...] = (
    (DecisionState.DRAFT, DecisionState.EXECUTED),
    (DecisionState.OBSERVED, DecisionState.EXECUTED),
    (DecisionState.AUTHORISED, DecisionState.CLOSED),
    (DecisionState.EXECUTED, DecisionState.CLOSED),
)


def _coerce_state(state: DecisionState | str) -> DecisionState:
    try:
        return state if isinstance(state, DecisionState) else DecisionState(state)
    except ValueError as exc:
        raise ValueError(f"unknown decision state: {state!r}") from exc


def can_transition(src: DecisionState | str, dst: DecisionState | str) -> bool:
    try:
        source = _coerce_state(src)
        target = _coerce_state(dst)
    except ValueError:
        return False
    if (source, target) in PROHIBITED_TRANSITIONS:
        return False
    return target in ALLOWED_TRANSITIONS.get(source, ())


def advance(state: DecisionState | str, dst: DecisionState | str) -> DecisionState:
    source = _coerce_state(state)
    target = _coerce_state(dst)
    if (source, target) in PROHIBITED_TRANSITIONS:
        raise ValueError(
            f"prohibited decision transition: {source.value} -> {target.value}"
        )
    if not can_transition(source, target):
        raise ValueError(
            f"unknown decision transition: {source.value} -> {target.value}"
        )
    return target


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    state: DecisionState
    objective_contract_ref: str = ""
    history: tuple[DecisionState, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "state": self.state.value,
            "objective_contract_ref": self.objective_contract_ref,
            "history": [state.value for state in self.history],
        }


__all__ = [
    "ALLOWED_TRANSITIONS",
    "PROHIBITED_TRANSITIONS",
    "DecisionRecord",
    "DecisionState",
    "advance",
    "can_transition",
]