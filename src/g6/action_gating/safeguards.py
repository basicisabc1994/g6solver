# SPDX-License-Identifier: Apache-2.0
"""Injected mistake-proofing kernel; no catalogue, environment switch or default audit file."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from g6.action_gating.schema import GateDecision


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    INCONCLUSIVE = "INCONCLUSIVE"
    HITL_REQUIRED = "HITL_REQUIRED"
    BLOCK = "BLOCK"


# A block must never become a review: use the existing gate's severity order.
_RANK = {Outcome.ALLOW: 0, Outcome.WARN: 1, Outcome.INCONCLUSIVE: 2,
         Outcome.HITL_REQUIRED: 3, Outcome.BLOCK: 4}
_DECISION = {Outcome.ALLOW: "allow", Outcome.WARN: "allow_with_notice",
             Outcome.INCONCLUSIVE: "require_human_review", Outcome.HITL_REQUIRED: "require_human_review",
             Outcome.BLOCK: "block"}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("interaction context accepts data only")


@dataclass(frozen=True)
class InteractionContext:
    operation: str
    params: Mapping[str, Any] = field(default_factory=dict)
    surface: str = "python"
    user_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("params", "user_context"):
            object.__setattr__(self, name, _freeze(getattr(self, name)))


@dataclass(frozen=True)
class Safeguard:
    """Host-supplied trusted predicate, never caller code or an import string."""
    name: str
    predicate: Callable[[InteractionContext], Outcome]

    def check(self, context: InteractionContext) -> Outcome:
        try:
            answer = self.predicate(context)
        except Exception:  # a failed check blocks; do not leak exception values
            return Outcome.BLOCK
        return answer if isinstance(answer, Outcome) else Outcome.INCONCLUSIVE


def fold(outcomes: Iterable[Outcome]) -> Outcome:
    values = tuple(outcomes)
    if not values:
        raise ValueError("[G6_E_SAFEGUARDS_INACTIVE] no safeguard was evaluated")
    return max((v if isinstance(v, Outcome) else Outcome.INCONCLUSIVE for v in values), key=_RANK.__getitem__)


@dataclass(frozen=True)
class InterceptionResult:
    outcome: Outcome
    evaluated_count: int
    outcomes: tuple[tuple[str, Outcome], ...]
    shadow: bool = False
    code: str = "G6_E_SAFEGUARDS_EVALUATED"

    @property
    def completion_state(self) -> str:
        if self.code in {"G6_E_SAFEGUARDS_INACTIVE", "G6_E_SAFEGUARDS_AUDIT_FAILED"}:
            return "blocked-escalated"
        return "qualified-draft" if self.shadow or self.outcome in {Outcome.ALLOW, Outcome.WARN} else "blocked-escalated"

    def evidence(self) -> dict:
        return {"kind": "safeguards", "code": self.code, "evaluated_count": self.evaluated_count,
                "outcome": self.outcome.value, "shadow": self.shadow,
                "checks": [{"name": name, "outcome": value.value} for name, value in self.outcomes]}

    def apply(self, decision: GateDecision) -> GateDecision:
        from g6.action_gating.decision_engine import tighten_decision

        candidate = _DECISION[self.outcome]
        if self.shadow and self.code == "G6_E_SAFEGUARDS_EVALUATED":
            candidate = "allow_with_notice"
        return tighten_decision(decision, candidate, self.code, self.evidence())


class Interceptor:
    def __init__(self, safeguards: Iterable[Safeguard], *, shadow: bool = False,
                 sink: Callable[[dict], None] | None = None) -> None:
        self.safeguards = tuple(safeguards)
        if any(not isinstance(s, Safeguard) for s in self.safeguards):
            raise TypeError("safeguards must contain Safeguard objects")
        if type(shadow) is not bool:
            raise TypeError("shadow must be a boolean")
        self.shadow, self.sink = shadow, sink

    def intercept(self, context: InteractionContext) -> InterceptionResult:
        outcomes = tuple((s.name, s.check(context)) for s in self.safeguards)
        if not outcomes:
            return InterceptionResult(Outcome.BLOCK, 0, (), self.shadow, "G6_E_SAFEGUARDS_INACTIVE")
        result = InterceptionResult(fold(v for _, v in outcomes), len(outcomes), outcomes, self.shadow)
        if self.sink is not None:
            try:
                self.sink(result.evidence())
            except Exception:
                return InterceptionResult(Outcome.BLOCK, len(outcomes), outcomes, self.shadow,
                                          "G6_E_SAFEGUARDS_AUDIT_FAILED")
        return result
