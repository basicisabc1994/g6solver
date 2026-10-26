# SPDX-License-Identifier: Apache-2.0
"""The orchestrator: plan the run, execute each planned step through the caller's runners, pick the final answer,
check it, and label the result.

Steps call no archive component: each runs through ``runners[step.name]`` (audit 028 §11). Nothing is written to
disk and nothing is learned from a run: the archive's learning stack, which stored every run whose steps all
returned as a "success" under ``~/.g6``, is not ported (audit 028, SV-001, SV-011).

The final answer is read from completed steps in a priority order that depends on complexity (simple: 8, 10, 11;
moderate: 10, 8, 11, 12, 14; complex: 10, 11, 14, 12, 8, 15), from the first non-blank string under an answer key
(``final_answer``, ``value``, ``result``, ``answer``, ...). A runner whose text is not an answer should not put it
under one of those keys.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from g6.solver.agentic_evidence import summarize_agentic_evidence
from g6.solver.agentic_runtime import Decision, SolverPlanner
from g6.solver.category_registry import CategoryRegistry
from g6.solver.complexity_classifier import TaskComplexity
from g6.solver.constraints import constraint_problem, evaluate_constraints
from g6.solver.llm_quality_checker import LLMQualityChecker
from g6.solver.history import _observe
from g6.solver.schema import (
    E_CONSTRAINT_UNEVALUABLE,
    E_EMPTY_GOAL,
    E_PRODUCTION_UNSUPPORTED,
    E_SIGNATURE_UNVERIFIABLE,
    SolverInput,
    SolverOutput,
    StepResult,
)
from g6.solver.step_executor import StepContext, StepRunner, answer_text, execute_step
from g6.solver.step_registry import STEP_REGISTRY

_ANSWER_PRIORITY = {
    TaskComplexity.SIMPLE: (8, 10, 11),
    TaskComplexity.MODERATE: (10, 8, 11, 12, 14),
    TaskComplexity.COMPLEX: (10, 11, 14, 12, 8, 15),
}


@dataclass(frozen=True)
class RunPlan:
    """What a run would do: its complexity, the steps that run, the steps the caller skipped, and who decided."""

    complexity: TaskComplexity
    steps_to_run: frozenset[int]
    skipped_by_caller: frozenset[int]
    decisions: tuple[Decision, ...]


def plan_run(solver_input: SolverInput, *, planner: SolverPlanner | None = None) -> RunPlan:
    """Decide the complexity and the steps to run, without running any step."""
    planner = planner if planner is not None else SolverPlanner()
    # audit 028, SV-002: the planner is told the caller's real constraints (the archive always passed []).
    constraints = [c.raw for c in solver_input.constraints]
    decisions: list[Decision] = []
    if solver_input.complexity_override:
        complexity = TaskComplexity(solver_input.complexity_override)
        decisions.append(Decision("complexity", False, False, f"caller_override:{complexity.value}",
                                  "complexity_override"))
    else:
        complexity, decision = planner.assess_complexity(goal=solver_input.goal, context=solver_input.context or "",
                                                         constraints=constraints)
        decisions.append(decision)
    steps, decision = planner.plan_steps(goal=solver_input.goal, complexity=complexity, constraints=constraints)
    decisions.append(decision)
    skipped = frozenset(solver_input.skip_steps)
    return RunPlan(complexity, steps - skipped, skipped, tuple(decisions))


def _refusal(solver_input: SolverInput) -> tuple[str, str] | None:
    if len(solver_input.goal.strip()) < 3:
        return E_EMPTY_GOAL, "the goal is empty or shorter than 3 characters"
    # audit 028, SV-005: the archive let any non-empty reviewer_signature lift its production gate. Nothing in the
    # library can verify a signature, so production is refused outright and a signature is not accepted at all.
    if solver_input.run_mode == "production":
        return E_PRODUCTION_UNSUPPORTED, ("run_mode='production' is refused: no review record exists for this "
                                          "solver and nothing in the library can verify a reviewer_signature; "
                                          "use run_mode='beta'")
    if (solver_input.reviewer_signature or "").strip():
        return E_SIGNATURE_UNVERIFIABLE, ("reviewer_signature is refused: nothing in the library verifies a "
                                          "signature, so accepting one would let any string stand for a review")
    for spec in solver_input.constraints:
        problem = constraint_problem(spec)
        if problem:
            return E_CONSTRAINT_UNEVALUABLE, problem
    return None


def _final_answer(steps: Sequence[StepResult], complexity: TaskComplexity, planner: SolverPlanner, goal: str,
                  constraints: Sequence[str]) -> tuple[str, int | None, Decision | None]:
    outputs = {s.step_number: s.output for s in steps if s.status == "completed"}
    priority = [n for n in _ANSWER_PRIORITY[complexity] if n in outputs]
    candidates: list[tuple[int, str]] = []
    for n in priority:
        text = answer_text(outputs[n])
        if text and text not in {c for _, c in candidates}:
            candidates.append((n, text))
    if len(candidates) >= 2:
        index, decision = planner.select_answer(goal=goal, candidates=[c for _, c in candidates],
                                                constraints=constraints)
        return candidates[index][1], candidates[index][0], decision
    if candidates:
        return candidates[0][1], candidates[0][0], None
    best_step, best = None, ""
    for n in priority:   # no answer key: the longest string a priority step returned
        for value in outputs[n].values():
            if isinstance(value, str) and len(value.strip()) > len(best):
                best_step, best = n, value.strip()
    return best, best_step, None


def solve(solver_input: SolverInput, runners: Mapping[str, StepRunner], *,
          fallbacks: CategoryRegistry | None = None, planner: SolverPlanner | None = None,
          quality_checker: LLMQualityChecker | None = None, request_id: str = "", run_id: str = "") -> SolverOutput:
    """Run the planned steps through ``runners`` (step name -> callable taking a ``StepContext``).

    A planned step with no runner (and no fallback) is ``unavailable`` and the run is ``incomplete``; a step whose
    runner never returned anything usable is ``failed`` and the run is ``halted`` and ``blocked-escalated``. The
    caller's constraints are checked against the final answer; ``success`` is true only when such a deterministic
    check passed. The best label is ``qualified-draft``.
    """
    unknown = sorted(set(runners) - set(STEP_REGISTRY.names()))
    if unknown:
        raise ValueError(f"G6_E_SOLVER_UNKNOWN_STEP: no step is named {unknown}; the steps are "
                         f"{list(STEP_REGISTRY.names())}")
    run_id = run_id or uuid.uuid4().hex
    refusal = _refusal(solver_input)
    if refusal is not None:
        return SolverOutput(status="refused", error_code=refusal[0], error=refusal[1], request_id=request_id,
                            run_id=run_id)

    planner = planner if planner is not None else SolverPlanner()
    plan = plan_run(solver_input, planner=planner)
    constraints = tuple(c.raw for c in solver_input.constraints)
    outputs: dict[str, dict] = {}
    steps: list[StepResult] = []
    for meta in STEP_REGISTRY:
        if meta.number in plan.skipped_by_caller or meta.number not in plan.steps_to_run:
            reason = ("skipped by the caller (skip_steps)" if meta.number in plan.skipped_by_caller
                      else f"not in the {plan.complexity.value} plan")
            steps.append(StepResult(step_number=meta.number, step_name=meta.name, status="skipped", reason=reason))
            continue
        ctx = StepContext(goal=solver_input.goal, context=solver_input.context or "", constraints=constraints,
                          complexity=plan.complexity.value, step=meta,
                          outputs=MappingProxyType({k: MappingProxyType(v) for k, v in outputs.items()}))
        result = execute_step(meta, ctx, runners.get(meta.name), fallbacks)
        if result.status == "completed":
            outputs[meta.name] = result.output
        steps.append(result)

    if any(s.status == "failed" for s in steps):
        status = "halted"
    elif any(s.status == "unavailable" for s in steps):
        status = "incomplete"
    else:
        status = "completed"
    answer, answer_step, answer_decision = _final_answer(steps, plan.complexity, planner, solver_input.goal,
                                                         constraints)
    decisions = [*plan.decisions, *([answer_decision] if answer_decision else [])]
    checks, satisfied = evaluate_constraints(solver_input.constraints, answer)
    if quality_checker is not None and answer:
        checks.append(quality_checker.check(answer, solver_input.goal, step_number=answer_step or 0).as_check())
    return _observe(SolverOutput(
        status=status, steps=steps, final_answer=answer, final_answer_step=answer_step, checks=checks,
        constraints_satisfied=satisfied, complexity_detected=plan.complexity.value,
        total_latency_ms=sum(s.latency_ms for s in steps),
        agentic_evidence=summarize_agentic_evidence((d.as_dict() for d in decisions),
                                                    model_supplied=planner.model_supplied),
        request_id=request_id, run_id=run_id))


__all__ = ["RunPlan", "plan_run", "solve"]
