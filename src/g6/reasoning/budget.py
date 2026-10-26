# SPDX-License-Identifier: Apache-2.0
"""Hard ceilings on a reasoning run, enforced by the caller wrapper itself (so nothing can forget)."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, LLMError
from g6.core.model_calls import counting


class BudgetExhausted(LLMError):
    """A ceiling was reached; the caller must stop and report best-so-far or abstain."""


@dataclass(frozen=True)
class Budget:
    max_model_calls: int = 32
    max_output_chars: int = 400_000
    max_wall_clock_s: float = 120.0

    def __post_init__(self) -> None:
        if self.max_model_calls < 1 or self.max_output_chars < 1 or not self.max_wall_clock_s > 0:
            raise ValueError("budget ceilings must be positive")


class MeteredCaller:
    """Wraps an ``LLMCaller``. Every attempt is counted, including ones that raise (audit 023 RH-007).

    ``check()`` names the first exhausted ceiling or returns None; ``complete`` refuses once one is hit.
    An inner caller that retries (``EmptyRetry``) is charged every model call it notes
    (``g6.core.model_calls``), counted after it returns; wrap the backend, not the retry, to bound each one.
    A call cannot be interrupted, so one that returns after the time limit raises ``BudgetExhausted``
    instead of returning its late answer; it is still charged (audit 023 RH-008).
    """

    def __init__(self, inner: LLMCaller, budget: Budget | None = None, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.inner, self.budget, self._clock = inner, budget or Budget(), clock
        self._start = clock()
        self.calls = 0
        self.failed_calls = 0
        self.output_chars = 0

    def elapsed_s(self) -> float:
        return self._clock() - self._start

    def check(self) -> str | None:
        if self.calls >= self.budget.max_model_calls:
            return "max_model_calls"
        if self.output_chars >= self.budget.max_output_chars:
            return "max_output_chars"
        if self.elapsed_s() >= self.budget.max_wall_clock_s:
            return "max_wall_clock_s"
        return None

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        limit = self.check()
        if limit:
            raise BudgetExhausted(f"G6_E_BUDGET_EXHAUSTED: {limit}")
        self.calls += 1
        with counting() as noted:
            try:
                text = self.inner.complete(system, user, max_tokens=max_tokens)
            except Exception:
                self.failed_calls += 1
                raise
            finally:
                self.calls += max(0, noted() - 1)   # the inner caller's retries are model calls too
        self.output_chars += len(text or "")
        if self.elapsed_s() >= self.budget.max_wall_clock_s:
            raise BudgetExhausted("G6_E_BUDGET_EXHAUSTED: max_wall_clock_s (the call returned after the time limit)")
        return text
