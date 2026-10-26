# SPDX-License-Identifier: Apache-2.0
"""Optional model-backed planning decisions, each with a deterministic fallback.

``SolverPlanner(llm)`` makes three decisions: the task's complexity, which steps run, and which candidate answer
is kept. With no model (the default) the keyword classifier, the step profile and the priority order decide. With
a model whose call fails, or whose reply is malformed or off-menu, the fallback decides and the decision is
recorded ``degraded``. Every decision records ``llm_used``, so ``agentic_evidence`` says who decided.

The model is never asked for a completion state and its reply is never read for one (audit 028, SV-012); the
label comes from ``schema.derive_completion_state`` alone.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from g6.core.llm import LLMCaller, extract_json
from g6.solver.complexity_classifier import TaskComplexity, classify_task
from g6.solver.step_profiles import ALL_STEPS, profile_for
from g6.solver.step_registry import STEP_REGISTRY

T = TypeVar("T")

# The answer spine (goal, construction, evaluation) runs even when a model plans a degenerate set.
ANSWER_SPINE = frozenset({1, 8, 11})

_COMPLEXITY_SYSTEM = (
    "You classify a task for a 15-step problem solver as simple, moderate or complex by what solving it demands "
    "(tools, steps, stakes, ambiguity), not by its length or keywords. Return only JSON: "
    '{"level": "simple|moderate|complex", "rationale": "..."}.')
_PLAN_SYSTEM = (
    "You choose which steps of a 15-step problem solver to run for a task. Skip steps that add nothing; keep the "
    'steps that produce and evaluate the answer. Return only JSON: {"steps_to_run": [step numbers], '
    '"rationale": "..."}.')
_ANSWER_SYSTEM = (
    "You pick the best final answer for a goal from candidate answers. Never write a new answer. Return only JSON: "
    '{"selected_index": <0-based index>, "rationale": "..."}.')


class SolverDecisionError(ValueError):
    """The model did not produce a usable, validated decision."""


@dataclass(frozen=True)
class Decision:
    """One planning decision and who made it."""

    decision: str
    llm_used: bool
    degraded: bool
    detail: str = ""
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rationale(reply: dict[str, Any]) -> str:
    text = str(reply.get("rationale") or "").strip()
    if not text:
        raise SolverDecisionError("the reply has no rationale")
    return text


class SolverPlanner:
    """Planning decisions for one run. ``llm`` is any ``g6.core.llm.LLMCaller``; ``None`` means deterministic."""

    def __init__(self, llm: LLMCaller | None = None) -> None:
        self.llm = llm

    @property
    def model_supplied(self) -> bool:
        return self.llm is not None

    def _ask(self, system: str, payload: dict[str, Any], max_tokens: int) -> dict[str, Any]:
        assert self.llm is not None
        reply = extract_json(self.llm.complete(system, json.dumps(payload), max_tokens=max_tokens))
        if not isinstance(reply, dict):
            raise SolverDecisionError("the reply is not a JSON object")
        return reply

    def _decide(self, name: str, by_model: Callable[[], tuple[T, str, str]],
                fallback: Callable[[], tuple[T, str]]) -> tuple[T, Decision]:
        reason = "no model supplied"
        if self.llm is not None:
            try:
                value, detail, rationale = by_model()
                return value, Decision(name, True, False, detail, rationale[:500])
            except Exception as exc:  # noqa: BLE001 - any model failure falls back, and is recorded as degraded
                reason = f"{type(exc).__name__}: {exc}"[:500]
        value, detail = fallback()
        return value, Decision(name, False, self.llm is not None, detail, reason)

    def assess_complexity(self, *, goal: str, context: str = "",
                          constraints: Sequence[str] = ()) -> tuple[TaskComplexity, Decision]:
        def by_model() -> tuple[TaskComplexity, str, str]:
            reply = self._ask(_COMPLEXITY_SYSTEM, {"goal": goal, "context": context[:4000],
                                                   "constraints": list(constraints)}, 600)
            level = str(reply.get("level") or "").strip().lower()
            if level not in {c.value for c in TaskComplexity}:
                raise SolverDecisionError(f"level must be simple, moderate or complex, got {level!r}")
            return TaskComplexity(level), level, _rationale(reply)

        def fallback() -> tuple[TaskComplexity, str]:
            level = classify_task(goal, context)
            return level, f"keyword_classifier:{level.value}"

        return self._decide("complexity", by_model, fallback)

    def plan_steps(self, *, goal: str, complexity: TaskComplexity,
                   constraints: Sequence[str] = ()) -> tuple[frozenset[int], Decision]:
        def by_model() -> tuple[frozenset[int], str, str]:
            available = [{"number": m.number, "name": m.title} for m in STEP_REGISTRY]
            reply = self._ask(_PLAN_SYSTEM, {"goal": goal, "complexity": complexity.value,
                                             "available_steps": available, "constraints": list(constraints)}, 800)
            raw = reply.get("steps_to_run")
            if not isinstance(raw, list):
                raise SolverDecisionError("steps_to_run is not a list")
            chosen = frozenset(n for n in raw if isinstance(n, int) and not isinstance(n, bool) and n in ALL_STEPS)
            if not chosen:
                raise SolverDecisionError("the plan runs no known step")
            run = chosen | ANSWER_SPINE
            return run, f"run={sorted(run)}", _rationale(reply)

        def fallback() -> tuple[frozenset[int], str]:
            return profile_for(complexity).active_steps, f"profile:{complexity.value}"

        return self._decide("step_plan", by_model, fallback)

    def select_answer(self, *, goal: str, candidates: Sequence[str],
                      constraints: Sequence[str] = ()) -> tuple[int, Decision]:
        """The index of the kept candidate. The model may only choose one; it cannot write a new answer."""
        def by_model() -> tuple[int, str, str]:
            reply = self._ask(_ANSWER_SYSTEM, {"goal": goal, "candidates": list(candidates),
                                               "constraints": list(constraints)}, 400)
            index = reply.get("selected_index")
            if not (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candidates)):
                raise SolverDecisionError(f"selected_index {index!r} is not a candidate")
            return index, f"selected_index={index}", _rationale(reply)

        def fallback() -> tuple[int, str]:
            return 0, "priority_order:0"

        return self._decide("answer", by_model, fallback)


__all__ = ["ANSWER_SPINE", "Decision", "SolverDecisionError", "SolverPlanner"]
