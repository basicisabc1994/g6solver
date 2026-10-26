# SPDX-License-Identifier: Apache-2.0
"""Run one step through the caller's runner, with retries and category fallbacks.

A step is ``completed`` only when a runner returned something usable: a non-empty string, or a mapping with at
least one non-empty value, that is not a refusal (audit 028, SV-008). ``None``, ``{}``, blank text or an exception
is a failed attempt. A step with no runner and no fallback is ``unavailable``: it did not run, and it never counts
as done.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.solver.category_registry import CategoryRegistry
from g6.solver.red_flag_detector import is_refusal
from g6.solver.schema import StepResult
from g6.solver.step_registry import StepMeta

# Keys read, in order, for the answer-bearing text of a step output.
ANSWER_KEYS = ("final_answer", "value", "result", "answer", "output", "code", "response", "text", "summary",
               "conclusion")


@dataclass(frozen=True)
class StepContext:
    """What a runner sees: the goal, the caller's constraints, the planned complexity, the step, and the outputs of
    the steps completed before it (by step name)."""

    goal: str
    context: str
    constraints: tuple[str, ...]
    complexity: str
    step: StepMeta
    outputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


StepRunner = Callable[[StepContext], Any]


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return any(_has_content(v) for v in (value.values() if isinstance(value, Mapping) else value))
    return True   # numbers and booleans are values


def answer_text(output: Mapping[str, Any]) -> str:
    """The first non-blank string under an answer key, or ``""``."""
    for key in ANSWER_KEYS:
        val = output.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def usable_output(value: Any) -> tuple[dict[str, Any] | None, str]:
    """``(output, "")`` when ``value`` is usable, else ``(None, why)``."""
    if isinstance(value, str):
        output: dict[str, Any] = {"result": value}
    elif isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            return None, "returned a mapping with non-string keys"
        output = dict(value)
    elif value is None:
        return None, "returned None"
    else:
        return None, f"returned {type(value).__name__}; a runner returns a mapping or a string"
    if not _has_content(output):
        return None, "returned no non-empty value"
    text = answer_text(output)
    if text and is_refusal(text):
        return None, "returned a refusal"
    return output, ""


def _attempt(runner: StepRunner, ctx: StepContext) -> tuple[dict[str, Any] | None, str]:
    try:
        return usable_output(runner(ctx))
    except Exception as exc:  # noqa: BLE001 - a runner's failure is recorded on the step, never raised past it
        return None, f"raised {type(exc).__name__}: {str(exc)[:200]}"


def execute_step(meta: StepMeta, ctx: StepContext, runner: StepRunner | None = None,
                 fallbacks: CategoryRegistry | None = None) -> StepResult:
    """Try ``runner`` up to ``meta.max_attempts`` times, then each fallback runner once."""
    start = time.monotonic()
    attempts = 0
    problems: list[str] = []

    def done(status: str, **fields: Any) -> StepResult:
        return StepResult(step_number=meta.number, step_name=meta.name, status=status, attempts=attempts,
                          latency_ms=int((time.monotonic() - start) * 1000), **fields)

    if runner is not None:
        for _ in range(max(1, meta.max_attempts)):
            attempts += 1
            output, problem = _attempt(runner, ctx)
            if output is not None:
                return done("completed", output=output)
            problems.append(problem)
    chain = fallbacks.fallback_chain(meta.fallback_categories) if fallbacks is not None else []
    for name, fallback in chain:
        attempts += 1
        output, problem = _attempt(fallback, ctx)
        if output is not None:
            return done("completed", output=output, fallback_used=True, fallback_component=name)
        problems.append(f"fallback {name} {problem}")
    if runner is None and not chain:
        return done("unavailable", reason="no runner supplied for this step")
    distinct = list(dict.fromkeys(problems))
    return done("failed", reason="; ".join(distinct)[:500])


__all__ = ["ANSWER_KEYS", "StepContext", "StepRunner", "answer_text", "execute_step", "usable_output"]
