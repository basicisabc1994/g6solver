# SPDX-License-Identifier: Apache-2.0
"""Types for the maturity scorecard: rubrics, audits, tiers and scores.

A rubric is a list of *shape* assertions about a component's output: a field exists, is non-empty, has a type, or is
above or below a bound. A high score means the output has the expected shape. It does not mean the output is right
(audit 008, ST-010: the archive called the top tiers "validated" and "production").

Tiers T0-T6:

* T0-T2: the component was not found, does not import, or failed (an invocation error, or every shape check failed);
* T3: it ran, and nothing reviewed checked its output shape (no rubric, an unevaluable rubric, or one nobody reviewed);
* T4-T5: a reviewed rubric's shape checks scored at least 0.5 / 0.8 (shape checks, not quality);
* T6: set only by an explicit ``ScorecardStore.promote`` on a passing, reviewed audit; never by an audit.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MaturityTier = Literal[0, 1, 2, 3, 4, 5, 6]

# audit 008, ST-010: the names say what was measured. No tier is called "validated" or "production".
TIER_NAMES: dict[int, str] = {
    0: "not-found",
    1: "does-not-import",
    2: "fails",
    3: "runs",
    4: "shape-partial",
    5: "shape-checked",
    6: "promoted",
}
#: The tiers that measure the output's shape (and only its shape).
SHAPE_CHECK_TIERS: frozenset[int] = frozenset({4, 5})
#: The tier an audit must reach to pass, and a promotion at or above which needs a passing reviewed audit.
PUBLICATION_THRESHOLD: int = 5
#: The highest tier an output can earn without a reviewed rubric that evaluates it (audit 008, ST-010).
UNREVIEWED_TIER_CAP: int = 3

CheckName = Literal["exists", "not_empty", "gt", "gte", "lt", "lte", "eq", "contains", "type_is", "length_gte",
                    "in_set"]


class RubricAssertion(BaseModel):
    """One shape assertion: ``check`` applied to the value at the dot-path ``field`` of the output."""

    model_config = ConfigDict(frozen=True)

    field: str
    check: CheckName = "exists"
    expected: Any = None
    weight: float = Field(default=1.0, ge=0.0, allow_inf_nan=False)
    description: str = ""


class RubricProvenance(BaseModel):
    """Who reviewed the rubric. An empty ``reviewed_by`` means nobody did."""

    model_config = ConfigDict(frozen=True)

    reviewed_by: str = ""
    review_notes: str = ""


class ComponentRubric(BaseModel):
    """The shape assertions for one component op, with the input to invoke it with."""

    model_config = ConfigDict(frozen=True)

    component: str
    op: str
    description: str = ""
    assertions: list[RubricAssertion] = Field(default_factory=list)
    test_input: dict[str, Any] = Field(default_factory=dict)
    provenance: RubricProvenance = Field(default_factory=RubricProvenance)

    @property
    def reviewed(self) -> bool:
        return bool(self.provenance.reviewed_by.strip())


class AssertionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    assertion: RubricAssertion
    passed: bool
    actual_value: Any = None
    error: str = ""


class RubricScore(BaseModel):
    """A rubric applied to one output. ``evaluable=False`` (no assertions, or no weight) has ``score=0.0`` and is never
    read as a pass (audit 008, ST-010: the archive scored it 1.0)."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["shape"] = "shape"
    evaluable: bool
    score: float = 0.0
    reviewed: bool = False
    results: list[AssertionResult] = Field(default_factory=list)
    reason: str = ""


class AuditResult(BaseModel):
    """One audit of one component op: the tier it earned and the evidence for it."""

    model_config = ConfigDict(frozen=True)

    component: str
    op: str = "infer"
    tier: MaturityTier
    tier_name: str = ""
    passed_threshold: bool = False
    score: float = 0.0
    evaluable: bool = False
    rubric_reviewed: bool = False
    assertion_results: list[AssertionResult] = Field(default_factory=list)
    error: str = ""
    timestamp: float = 0.0


class MaturityScore(BaseModel):
    """A component's standing on the scorecard.

    ``current_tier`` is the latest evidenced tier: the latest audit's, or a promotion granted after it, held down
    while a flag is unresolved. ``max_tier_seen`` is history only (audit 008, ST-009: the archive kept the maximum).
    """

    model_config = ConfigDict(frozen=True)

    component: str
    current_tier: MaturityTier = 0
    tier_name: str = TIER_NAMES[0]
    tier_source: Literal["audit", "promotion", "none"] = "none"
    max_tier_seen: MaturityTier = 0
    audit_count: int = 0
    pass_count: int = 0
    last_score: float = 0.0
    last_evaluable: bool = False
    last_audit_timestamp: float = 0.0
    flagged: bool = False
    flag_reason: str = ""
    flag_resolution: str = ""


class ComponentScorecard(BaseModel):
    """Every scored component. A flagged component is counted as flagged, never as above the threshold."""

    scores: dict[str, MaturityScore] = Field(default_factory=dict)
    total_components: int = 0
    above_threshold: int = 0
    below_threshold: int = 0
    flagged: int = 0
    timestamp: float = 0.0


__all__ = ["PUBLICATION_THRESHOLD", "SHAPE_CHECK_TIERS", "TIER_NAMES", "UNREVIEWED_TIER_CAP", "AssertionResult",
           "AuditResult", "CheckName", "ComponentRubric", "ComponentScorecard", "MaturityScore", "MaturityTier",
           "RubricAssertion", "RubricProvenance", "RubricScore"]
