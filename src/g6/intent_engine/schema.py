# SPDX-License-Identifier: Apache-2.0
"""The intent contract a mission is held to, and the goal schema it extends (from the archive's
``intent_engine.schema`` and ``goal_engine.schema``; field names and defaults unchanged, so stored contracts load)."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.intent_engine.checks import Check

# IE-RB: a limit is a non-negative, finite number. The archive compared ``v < 0`` before coercion, so NaN (which never
# trips a comparison) and infinity were accepted and a string raised a raw TypeError instead of a validation error.
_Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_Count = Annotated[int, Field(ge=0)]


class ResourceBoundsSchema(BaseModel):
    """
    Declared resource limits, validated at the system boundary (the archive's runtime
    ``ResourceBounds`` enforcement type is not ported; these are data only).
    """

    model_config = ConfigDict(frozen=True)

    max_execution_seconds: _Seconds | None = None
    max_disk_bytes: _Count | None = None
    max_tokens_per_minute: _Count | None = None
    max_tokens_per_hour: _Count | None = None
    max_tokens_per_day: _Count | None = None
    max_tokens_per_week: _Count | None = None
    max_tokens_per_month: _Count | None = None


class GuardrailSpec(BaseModel):
    """Declarative guardrail — resolved to a Guardrail at runtime."""

    model_config = ConfigDict(frozen=True)

    name: str
    predicate: str  # reference key: "resource_limit", "f1_threshold", "csf_hazard", etc.
    params: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class CheckpointSpec(BaseModel):
    """Declarative checkpoint — soft assertion logged per node."""

    model_config = ConfigDict(frozen=True)

    name: str
    predicate: str  # reference key: "metric_above", "latency_below", etc.
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class BreakpointSpec(BaseModel):
    """Declarative breakpoint — HITL pause point."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    active: bool = True


class GoalInput(BaseModel):
    """
    Canonical JSON input schema for the G6 problem-solving engine.

    Validated at the system boundary; passed into ``GoalDecomposer``.
    Nested ``subtasks`` allow callers to pre-specify a decomposition that
    ``GoalDecomposer`` will honour rather than generating its own.
    """

    model_config = ConfigDict(frozen=True)

    goal: str
    context: str | None = None
    constraints: list[str] = Field(default_factory=list)
    resource_bounds: ResourceBoundsSchema | None = None
    guardrails: list[GuardrailSpec] = Field(default_factory=list)
    checkpoints: list[CheckpointSpec] = Field(default_factory=list)
    breakpoints: list[BreakpointSpec] = Field(default_factory=list)
    subtasks: list["GoalInput"] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    model_config = ConfigDict(frozen=True)

    statement: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)   # bounded on every path, chat-supplied too (IE-009)
    source: Literal["explicit", "inferred", "fallback"] = "fallback"


class IntentQuestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    question: str
    reason: str


class IntentContract(GoalInput):
    success_checks: dict[str, Check] = Field(default_factory=dict)
    failure_checks: dict[str, Check] = Field(default_factory=dict)
    declared_risks: list[str] = Field(default_factory=list, max_length=100)

    why: str = ""
    failure_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    user_expertise_level: Literal["unknown", "beginner", "intermediate", "expert"] = "unknown"
    risk_tolerance: Literal["unknown", "low", "medium", "high"] = "unknown"
    cost_constraint: str = ""
    deadline: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)   # audit 005 IE-009
    confidence_method: Literal["field_completeness_heuristic"] = "field_completeness_heuristic"
    declined_clarification: bool = False


class IntentSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    initial_request: str
    questions: list[IntentQuestion] = Field(default_factory=list)
    contract: IntentContract
    # Only what the elicitor produces (audit 005 IE-008): "confidence" means the threshold was reached.
    ended_by: Literal["declined", "question_budget", "confidence", "incomplete"] = "confidence"
