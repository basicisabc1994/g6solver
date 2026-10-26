# SPDX-License-Identifier: Apache-2.0
"""The goal a caller declares (``GoalSpec`` and its control specs) and what an executor reports (``Observation``).

Field names and defaults follow the archive's ``GoalInput`` (kept unchanged as ``g6.intent_engine.GoalInput``), so a
well-formed stored goal loads. Unlike the archive, an unknown key is a validation error rather than silently ignored
(audit 013, GE-010: ``task=`` and ``max_depth=`` were accepted and never read), numbers are strict and finite (a NaN
limit compares false with every usage, so it could never trip), and lists are stored as tuples.

Scope, the same for every node: a guardrail, checkpoint, resource bound or breakpoint declared on a node applies to
that node and to every node below it (audit 013, GE-007: the archive read only the top level).
"""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

#: Usage names an executor may report, one per ``ResourceBoundsSpec`` limit (``max_<name>``).
USAGE_KEYS = ("execution_seconds", "disk_bytes", "tokens_per_minute", "tokens_per_hour", "tokens_per_day",
              "tokens_per_week", "tokens_per_month")
UsageKey = Literal["execution_seconds", "disk_bytes", "tokens_per_minute", "tokens_per_hour", "tokens_per_day",
                   "tokens_per_week", "tokens_per_month"]
#: The guardrail name a node's own ``resource_bounds`` is reported under; a declared guardrail may not take it.
RESOURCE_BOUNDS = "resource_bounds"

_Amount = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
_Count = Annotated[int, Field(strict=True, ge=0)]
_Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


def _control_name(value: str) -> str:
    # The name is part of a pause's approval scope, which ':' separates.
    if not value.strip() or value != value.strip() or ":" in value:
        raise ValueError("a control name must be non-blank, without surrounding spaces or ':'")
    return value


def _predicate_name(value: str) -> str:
    if not value.strip():
        raise ValueError("predicate must name a registered predicate")
    return value


ControlName = Annotated[str, AfterValidator(_control_name)]
PredicateName = Annotated[str, AfterValidator(_predicate_name)]


class ResourceBoundsSpec(BaseModel):
    """Declared limits, each checked against the matching ``Observation.usage`` figure (``max_tokens_per_hour``
    against ``usage["tokens_per_hour"]``). A limit with no reported figure is ``unknown``, which blocks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_execution_seconds: _Amount | None = None
    max_disk_bytes: _Count | None = None
    max_tokens_per_minute: _Count | None = None
    max_tokens_per_hour: _Count | None = None
    max_tokens_per_day: _Count | None = None
    max_tokens_per_week: _Count | None = None
    max_tokens_per_month: _Count | None = None

    def limits(self) -> dict[str, float]:
        """The declared limits keyed by usage name; undeclared ones are absent."""
        return {key.removeprefix("max_"): value for key, value in self.model_dump().items() if value is not None}


class GuardrailSpec(BaseModel):
    """A hard check: the registered ``predicate`` built from ``params``, read against an ``Observation``. A fail or an
    unknown at any node it applies to blocks that node."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ControlName
    predicate: PredicateName
    params: dict[str, JsonValue] = Field(default_factory=dict)
    message: str = ""

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value == RESOURCE_BOUNDS:
            raise ValueError(f"{RESOURCE_BOUNDS!r} is the name of a node's declared resource_bounds")
        return value


class CheckpointSpec(BaseModel):
    """A soft check, built like a guardrail. A fail or an unknown is reported and does not block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ControlName
    predicate: PredicateName
    params: dict[str, JsonValue] = Field(default_factory=dict)
    description: str = ""


class BreakpointSpec(BaseModel):
    """A human gate: while ``active``, every node it applies to gets a ``PauseRequest`` that needs a recorded approval
    before that node's work starts. Inactive breakpoints are ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ControlName
    description: str = ""
    active: bool = Field(default=True, strict=True)


class GoalSpec(BaseModel):
    """One goal and the subtasks the caller declares for it, recursively. Nothing in this package generates, orders or
    checks subtasks; ``constraints`` and ``success_criteria`` are carried as text for the executor and not checked."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    context: str | None = None
    constraints: tuple[str, ...] = ()
    resource_bounds: ResourceBoundsSpec | None = None
    guardrails: tuple[GuardrailSpec, ...] = ()
    checkpoints: tuple[CheckpointSpec, ...] = ()
    breakpoints: tuple[BreakpointSpec, ...] = ()
    subtasks: tuple[GoalSpec, ...] = ()
    success_criteria: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _unique_names(self) -> GoalSpec:
        # A report names each control by (kind, name, declaring node), so a name may appear once per kind and node.
        for kind, specs in (("guardrail", self.guardrails), ("checkpoint", self.checkpoints),
                            ("breakpoint", self.breakpoints)):
            names = [spec.name for spec in specs]
            repeated = sorted({name for name in names if names.count(name) > 1})
            if repeated:
                raise ValueError(f"{kind} name(s) declared twice on one node: {repeated}")
        return self

    def digest(self) -> str:
        """SHA-256 of the spec's canonical JSON: names this exact plan in an approval scope."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()


class Observation(BaseModel):
    """What the executor measured for one plan node (``node`` is a ``PlanTree`` id such as ``"root.0"``).

    Every built-in predicate reads only this object, never the spec's own declarations (audit 013, GE-005). Nothing
    here checks that the executor measured what it reports.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    node: str = "root"
    action: str | None = None
    usage: dict[UsageKey, _Amount] = Field(default_factory=dict)
    metrics: dict[str, _Number] = Field(default_factory=dict)


__all__ = ["RESOURCE_BOUNDS", "USAGE_KEYS", "BreakpointSpec", "CheckpointSpec", "GoalSpec", "GuardrailSpec",
           "Observation", "ResourceBoundsSpec", "UsageKey"]
