# SPDX-License-Identifier: Apache-2.0
"""Deterministic evaluation of the caller's constraints against the final answer (audit 028, SV-002).

The archive accepted ``SolverInput.constraints``, never read them, told its planner there were none, and always
reported ``constraints_satisfied == {}``. Here each constraint is either checked or refused before the run.
"""
from __future__ import annotations

from collections.abc import Sequence

from g6.solver.schema import ConstraintSpec, NamedCheck

_NEEDS_VALUE = frozenset({"contains", "excludes"})
_NEEDS_THRESHOLD = frozenset({"max_chars", "min_chars"})


def constraint_problem(spec: ConstraintSpec) -> str | None:
    """Why ``spec`` cannot be checked, or ``None`` when it can."""
    if spec.kind is None:
        return (f"constraint {spec.raw!r} has no kind; only contains, excludes, max_chars and min_chars can be "
                "checked, and a free-text constraint is refused rather than ignored")
    if spec.kind in _NEEDS_VALUE:
        if not spec.value:
            return f"constraint {spec.raw!r} ({spec.kind}) needs a non-empty value"
        if spec.threshold is not None:
            return f"constraint {spec.raw!r} ({spec.kind}) does not use a threshold"
    if spec.kind in _NEEDS_THRESHOLD:
        if spec.threshold is None or spec.threshold < 0:
            return f"constraint {spec.raw!r} ({spec.kind}) needs a threshold >= 0"
        if spec.value is not None:
            return f"constraint {spec.raw!r} ({spec.kind}) does not use a value"
    return None


def evaluate_constraint(spec: ConstraintSpec, answer: str) -> NamedCheck:
    """Check one evaluable constraint; ``unchecked`` when there is no answer to check."""
    name = f"constraint:{spec.raw}"
    if not answer.strip():
        return NamedCheck(name=name, kind="deterministic", verdict="unchecked", detail="no final answer to check")
    if spec.kind == "contains":
        ok, detail = spec.value in answer, f"answer contains {spec.value!r}"
    elif spec.kind == "excludes":
        ok, detail = spec.value not in answer, f"answer does not contain {spec.value!r}"
    elif spec.kind == "max_chars":
        ok, detail = len(answer) <= spec.threshold, f"{len(answer)} chars <= {spec.threshold}"
    elif spec.kind == "min_chars":
        ok, detail = len(answer) >= spec.threshold, f"{len(answer)} chars >= {spec.threshold}"
    else:
        raise ValueError(constraint_problem(spec) or f"unknown constraint kind {spec.kind!r}")
    return NamedCheck(name=name, kind="deterministic", verdict="pass" if ok else "fail",
                      detail=detail if ok else f"not true: {detail}")


def evaluate_constraints(specs: Sequence[ConstraintSpec], answer: str) -> tuple[list[NamedCheck], dict[str, bool]]:
    """Checks for every constraint, and ``constraints_satisfied`` keyed by ``raw`` (unchecked ones are left out)."""
    checks = [evaluate_constraint(s, answer) for s in specs]
    satisfied: dict[str, bool] = {}
    for spec, check in zip(specs, checks, strict=True):
        if check.verdict != "unchecked":
            satisfied[spec.raw] = satisfied.get(spec.raw, True) and check.verdict == "pass"
    return checks, satisfied


__all__ = ["constraint_problem", "evaluate_constraint", "evaluate_constraints"]
