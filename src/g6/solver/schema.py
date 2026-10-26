# SPDX-License-Identifier: Apache-2.0
"""Solver request and result types, and the label rule that keeps every run below ``verified``."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

# audit 028, SV-012: only the two states this component can reach. ``verified`` is not a member, so no code
# path, planner reply or caller can build a ``SolverOutput`` that claims it.
CompletionState = Literal["qualified-draft", "blocked-escalated"]
RunStatus = Literal["completed", "incomplete", "halted", "refused"]
StepStatus = Literal["completed", "skipped", "unavailable", "failed"]
Verdict = Literal["pass", "fail", "unchecked"]
Outcome = Literal["passed", "failed", "unchecked"]

E_EMPTY_GOAL = "G6_E_SOLVER_EMPTY_GOAL"
E_PRODUCTION_UNSUPPORTED = "G6_E_SOLVER_PRODUCTION_UNSUPPORTED"
E_SIGNATURE_UNVERIFIABLE = "G6_E_SOLVER_SIGNATURE_UNVERIFIABLE"
E_CONSTRAINT_UNEVALUABLE = "G6_E_SOLVER_CONSTRAINT_UNEVALUABLE"
E_REFUSED = "G6_E_SOLVER_REFUSED"
E_STEP_FAILED = "G6_E_SOLVER_STEP_FAILED"
E_NO_ANSWER = "G6_E_SOLVER_NO_ANSWER"
E_CHECK_FAILED = "G6_E_SOLVER_CHECK_FAILED"
# warning-card codes for a qualified draft
E_UNCHECKED = "G6_E_SOLVER_UNCHECKED"
E_CHECKED_DRAFT = "G6_E_SOLVER_CHECKED_DRAFT"
E_INCOMPLETE = "G6_E_SOLVER_INCOMPLETE"  # a planned step had no runner; leads the card whatever the checks say

_STEP_NUMBERS = range(1, 16)


class ConstraintSpec(BaseModel):
    """A requirement on the final answer, checked deterministically after the run.

    ``raw`` says what the caller means; ``kind`` says how it is checked: ``contains`` / ``excludes`` a ``value``,
    or ``max_chars`` / ``min_chars`` a ``threshold``. A constraint with no ``kind`` cannot be checked, so the run
    is refused before any step starts (audit 028, SV-002); it is never accepted and ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    raw: str
    kind: Literal["contains", "excludes", "max_chars", "min_chars"] | None = None
    value: str | None = None
    threshold: int | None = None


class SolverInput(BaseModel):
    """One goal for the solver.

    Every field is read. A field the archive accepted and never read (``subtasks``, ``guardrails``,
    ``checkpoints``, ``resource_bounds``, ...) is a validation error here, not a silent no-op (audit 028, SV-002).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    goal: str
    context: str | None = None
    constraints: list[ConstraintSpec] = Field(default_factory=list)
    complexity_override: Literal["simple", "moderate", "complex"] | None = None
    skip_steps: list[int] = Field(default_factory=list)
    # audit 028, SV-005: kept so a production request, or a signature nothing can verify, is refused with a code.
    run_mode: Literal["beta", "production"] = "beta"
    reviewer_signature: str | None = None

    @field_validator("skip_steps")
    @classmethod
    def _known_steps(cls, value: list[int]) -> list[int]:
        unknown = sorted(n for n in value if n not in _STEP_NUMBERS)
        if unknown:
            raise ValueError(f"skip_steps names steps outside 1-15: {unknown}")
        return value


class NamedCheck(BaseModel):
    """One check that ran (or could not run) on the final answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Literal["deterministic", "model_judgment"]
    verdict: Verdict
    detail: str = ""


class StepResult(BaseModel):
    """What one of the 15 steps did. ``completed`` means its runner returned a usable output, nothing more."""

    model_config = ConfigDict(extra="forbid")

    step_number: int
    step_name: str
    status: StepStatus
    output: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    attempts: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    fallback_component: str | None = None


class SolverFailure(BaseModel):
    """Typed degradation record: which dependency failed, which fallback was taken, and what an operator must do."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_number: int
    step_name: str
    failed_dep: str
    chosen_fallback: str
    confidence_impact: Literal["reduced", "unknown"] = "reduced"
    required_operator_action: str


def derive_solver_failures(steps: Sequence[StepResult]) -> list[SolverFailure]:
    """One record per step that used a fallback runner, had no runner, or failed."""
    failures: list[SolverFailure] = []
    for s in steps:
        where = f"step {s.step_number} ({s.step_name})"
        if s.status == "failed":
            failures.append(SolverFailure(
                step_number=s.step_number, step_name=s.step_name, failed_dep="the step runner and every fallback",
                chosen_fallback="none", confidence_impact="unknown",
                required_operator_action=f"{where} failed ({s.reason or 'no reason given'}); a human must decide "
                                         "how to continue."))
        elif s.status == "unavailable":
            failures.append(SolverFailure(
                step_number=s.step_number, step_name=s.step_name, failed_dep="no runner supplied",
                chosen_fallback="none",
                required_operator_action=f"{where} did not run: supply a runner for '{s.step_name}' or skip it "
                                         "explicitly with skip_steps."))
        elif s.fallback_used:
            failures.append(SolverFailure(
                step_number=s.step_number, step_name=s.step_name, failed_dep="the step runner",
                chosen_fallback=s.fallback_component or "category fallback",
                required_operator_action=f"Review {where}: its output came from a fallback runner."))
    return failures


def derive_outcome(checks: Sequence[NamedCheck]) -> Outcome:
    """``passed`` only when at least one deterministic named check ran and every one passed (audit 028, SV-001).

    A model judgment can lower the outcome (a ``fail`` is ``failed``) but never raise it: a run whose only
    "pass" came from a model, or whose checks could not run, is ``unchecked``.
    """
    if any(c.verdict == "fail" for c in checks):
        return "failed"
    deterministic = [c for c in checks if c.kind == "deterministic"]
    if deterministic and all(c.verdict == "pass" for c in deterministic):
        return "passed"
    return "unchecked"


def blocking_code(*, status: str, steps: Sequence[StepResult], checks: Sequence[NamedCheck] = (),
                  final_answer: str = "", error: str = "") -> str:
    """The ``G6_E_*`` code that blocks this run, or ``""`` when it may stand as a qualified draft."""
    if status == "refused" or error:
        return E_REFUSED
    if any(s.status == "failed" for s in steps):
        return E_STEP_FAILED
    if not final_answer.strip():
        return E_NO_ANSWER
    if any(c.verdict == "fail" for c in checks):
        return E_CHECK_FAILED
    return ""


def derive_completion_state(*, status: str, steps: Sequence[StepResult], checks: Sequence[NamedCheck] = (),
                            final_answer: str = "", error: str = "") -> CompletionState:
    """The run's label, from visible fields only. Never ``verified``.

    Library rule (DECISIONS #22, audit 028 §11): nothing in this component checks that the answer is right. A
    caller's deterministic constraint checks one stated property of it, so a run whose checks all passed is still
    a qualified draft, with the checks named on the output. A refusal, a failed step, a missing answer or a
    failed check is ``blocked-escalated``.
    """
    code = blocking_code(status=status, steps=steps, checks=checks, final_answer=final_answer, error=error)
    return "blocked-escalated" if code else "qualified-draft"


class SolverOutput(BaseModel):
    """The result of one run. ``completion_state``, ``outcome``, ``success`` and ``failures`` are re-derived
    from the other fields on every construction, so none of them can be hand-set."""

    model_config = ConfigDict(extra="forbid")

    _history_receipt: Any = PrivateAttr(default=None)

    status: RunStatus
    completion_state: CompletionState = "qualified-draft"
    error_code: str = ""
    error: str = ""
    steps: list[StepResult] = Field(default_factory=list)
    failures: list[SolverFailure] = Field(default_factory=list)
    final_answer: str = ""
    final_answer_step: int | None = None
    checks: list[NamedCheck] = Field(default_factory=list)
    constraints_satisfied: dict[str, bool] = Field(default_factory=dict)
    # audit 028, SV-001: "every step returned" is ``status``; ``success`` means a named check passed, nothing else.
    outcome: Outcome = "unchecked"
    success: bool = False
    complexity_detected: str = ""
    total_latency_ms: int = 0
    agentic_evidence: dict[str, Any] | None = None
    degraded: bool = False
    degradation_reason: str | None = None
    request_id: str = ""
    run_id: str = ""

    @model_validator(mode="after")
    def _derive(self) -> SolverOutput:
        if not self.failures:
            self.failures = derive_solver_failures(self.steps)
        if self.failures or (self.agentic_evidence or {}).get("degraded") is True:
            self.degraded = True
        self.outcome = derive_outcome(self.checks)
        self.success = self.outcome == "passed"
        code = blocking_code(status=self.status, steps=self.steps, checks=self.checks,
                             final_answer=self.final_answer, error=self.error)
        self.completion_state = "blocked-escalated" if code else "qualified-draft"
        self.error_code = (self.error_code or code) if code else ""
        if self.degraded and not self.degradation_reason:
            missing = _not_run(self.steps)
            self.degradation_reason = ((f"{missing} did not run (no runner was supplied). " if missing else "")
                                       + "Solver output is degraded; see failures[] and agentic_evidence for the "
                                         "required operator actions.")
        return self

    def to_envelope(self) -> dict[str, Any]:
        """The result as a library envelope (``g6.core.reliability_envelope``) with a ``G6_E_*`` warning card."""
        from g6.core.reliability_envelope import envelope_fields, warning_card

        missing = _not_run(self.steps)
        if self.completion_state == "blocked-escalated":
            card = warning_card(self.error_code, self.error or _BLOCK_MESSAGES.get(self.error_code, "Run blocked."),
                                "Read failures[] and checks[], then decide how to proceed.")
        elif missing:
            names = ", ".join(c.name for c in self.checks if c.kind == "deterministic" and c.verdict == "pass")
            checked = (f"the named checks passed ({names}) on the answer the other steps gave, and they check those "
                       "properties only" if self.success else "no deterministic named check passed on the answer")
            card = warning_card(E_INCOMPLETE,
                                f"An incomplete run: {missing} did not run (no runner was supplied), and {checked}. "
                                "This is a qualified draft of part of the plan, not a verified answer.",
                                "Supply a runner for each step that did not run, or skip it explicitly with "
                                "skip_steps, then run again; or review the answer knowing those steps never ran.")
        elif self.success:
            names = ", ".join(c.name for c in self.checks if c.kind == "deterministic")
            card = warning_card(E_CHECKED_DRAFT,
                                f"The named checks passed ({names}); they check those properties only, so this "
                                "is a qualified draft, not a verified answer.",
                                "Review the answer against anything the checks do not cover.")
        else:
            card = warning_card(E_UNCHECKED,
                                "No deterministic named check passed on the answer; every step returning a value "
                                "is not a check.",
                                "Add a deterministic constraint, or review the answer before relying on it.")
        evidence = [
            {"kind": "solver_checks", "checks": [c.model_dump() for c in self.checks], "outcome": self.outcome},
            {"kind": "solver_steps", "steps": [{"step": s.step_number, "name": s.step_name, "status": s.status,
                                                 "reason": s.reason} for s in self.steps]},
            {"kind": "solver_failures", "failures": [f.model_dump() for f in self.failures]},
            {"kind": "agentic_evidence", **(self.agentic_evidence or {})},
        ]
        envelope = envelope_fields(reliability_label=self.completion_state, warning_card=card, evidence=evidence,
                                   request_id=self.request_id, run_id=self.run_id,
                                   degradation_reason=self.degradation_reason or "")
        return {**envelope, "status": self.status, "outcome": self.outcome, "success": self.success,
                "final_answer": self.final_answer, "error_code": self.error_code}


def _not_run(steps: Sequence[StepResult]) -> str:
    """The planned steps that had no runner, as ``step 1 (goal_setting), ...`` ('' when every planned step ran)."""
    return ", ".join(f"step {s.step_number} ({s.step_name})" for s in steps if s.status == "unavailable")


_BLOCK_MESSAGES = {
    E_STEP_FAILED: "A planned step failed after its retries and fallbacks.",
    E_NO_ANSWER: "The run produced no final answer.",
    E_CHECK_FAILED: "A named check on the final answer failed.",
    E_REFUSED: "The run was refused before any step ran.",
}

__all__ = [
    "CompletionState", "ConstraintSpec", "NamedCheck", "Outcome", "RunStatus", "SolverFailure", "SolverInput",
    "SolverOutput", "StepResult", "StepStatus", "Verdict", "blocking_code", "derive_completion_state",
    "derive_outcome", "derive_solver_failures",
]
