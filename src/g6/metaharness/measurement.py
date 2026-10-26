# SPDX-License-Identifier: Apache-2.0
"""Measurements collected while the existing executor runs, never from proposal fields."""
from __future__ import annotations

from typing import Any

from g6.core.llm import DEFAULT_MAX_TOKENS
from g6.core.model_calls import counting
from g6.metaharness.trace import MeasuredStep, MeasuredTrace
from g6.reasoning import MeteredCaller


class TraceCaller(MeteredCaller):
    def __init__(self, *args: Any, step: str, model: str, usage: Any = None, order: list[str] | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.step, self.model, self.usage = step, model, usage
        self.order = order if order is not None else []
        self.observations: list[MeasuredStep] = []

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        if self.step not in self.order:
            self.order.append(self.step)
        start, error, before = self._clock(), "", self.calls
        receipts: list[dict[str, Any]] = []
        boundaries = 0

        def read_receipt() -> dict[str, Any]:
            nonlocal error
            if self.usage is None:
                return {}
            try:
                raw = self.usage()
                receipt = {k: raw[k] for k in ("cost_usd", "input_tokens", "output_tokens") if k in raw}
                MeasuredStep(name=self.step, latency_s=0.0, **receipt)
                return receipt
            except Exception:
                error = error or "G6_E_META_USAGE_UNAVAILABLE"
                return {}

        def before_attempt() -> None:
            nonlocal boundaries
            if boundaries:
                receipts.append(read_receipt())
            boundaries += 1

        try:
            with counting(before_call=before_attempt):
                return super().complete(system, user, max_tokens=max_tokens)
        except Exception:
            error = "G6_E_META_CALL_FAILED"
            raise
        finally:
            elapsed = max(0.0, self._clock() - start)
            attempts = self.calls - before
            if attempts:
                receipts.append(read_receipt())
            receipt = {}
            # Uninstrumented child/retry calls cannot be reconstructed from a last-call receipt.
            if receipts and len(receipts) == attempts:
                for key in ("cost_usd", "input_tokens", "output_tokens"):
                    values = [r.get(key) for r in receipts]
                    receipt[key] = sum(values) if all(value is not None for value in values) else None
            self.observations.append(MeasuredStep(name=self.step, model=self.model, latency_s=elapsed,
                                                  error=error, **receipt))


def trace_for(callers: dict[str, MeteredCaller], case_id: str, profile_digest: str,
              result: dict[str, Any], oracle: str) -> MeasuredTrace:
    steps = []
    # The built-in caller protocol is synchronous: aggregate repeated calls in first-use serial order.
    order = next((c.order for c in callers.values() if getattr(c, "order", None)), list(callers))
    for name in order:
        caller = callers[name]
        observations = getattr(caller, "observations", ())
        if not observations:
            continue
        def total(key: str) -> Any:
            values = [getattr(o, key) for o in observations]
            return sum(values) if all(v is not None for v in values) else None
        # Only a one-step result is directly judged by the workflow checks. Intermediate outputs stay unchecked.
        checked = tuple((result.get("results") or {}).keys()) if len(callers) == 1 else ()
        if len(callers) == 1 and oracle != "unknown":
            checked = (*checked, "workflow.oracle")
        passed = (oracle == "pass" and all(v == "pass" for v in (result.get("results") or {}).values())) \
            if checked else None
        steps.append(MeasuredStep(name=name, model=observations[0].model, latency_s=total("latency_s"),
                                  depends_on=(steps[-1].name,) if steps else (),
                                  input_tokens=total("input_tokens"), output_tokens=total("output_tokens"),
                                  cost_usd=total("cost_usd"), checked_by=checked, passed=passed,
                                  error=next((o.error for o in observations if o.error), "")))
    return MeasuredTrace(case_id=case_id, profile_digest=profile_digest, steps=tuple(steps))
