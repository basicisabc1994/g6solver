# SPDX-License-Identifier: Apache-2.0
"""Heuristic failure classification (no model calls), and the protocol for a custom classifier.

Ported from the archive's ``learning_layer/failure_classifier.py``. The codes are heuristic labels, not root causes:
a theory built on one is a hypothesis to measure, which is why the runner credits theories only from paired runs.
"""
from __future__ import annotations

from typing import Protocol

from g6.learning.schema import FailureAnalysis, TaskResult

FAILURE_CODES = ("NO_OUTPUT", "WRONG_FORMAT", "PARTIAL_MATCH", "TIMEOUT", "TOOL_ERROR", "REASONING_DEPTH",
                 "DOMAIN_MISMATCH")


class FailureClassifier(Protocol):
    def classify(self, result: TaskResult) -> FailureAnalysis: ...


class DefaultClassifier:
    """Every code whose heuristic fires, in this order: TIMEOUT (duration over the limit), TOOL_ERROR (the trace has
    an error), NO_OUTPUT (empty answer), REASONING_DEPTH (many turns, score under 0.3), WRONG_FORMAT (an answer that
    scored 0), PARTIAL_MATCH (score in (0, 0.5)), DOMAIN_MISMATCH (a ``domain_mismatch`` signal). The first is primary;
    a failure no heuristic explains is WRONG_FORMAT."""

    def __init__(self, timeout_ms: int = 120_000, high_turns: int = 20) -> None:
        self._timeout_ms = timeout_ms
        self._high_turns = high_turns

    def classify(self, result: TaskResult) -> FailureAnalysis:
        if result.passed:
            return FailureAnalysis(instance_id=result.instance_id)
        trace, answered = result.trace, bool(result.actual_output.strip())
        codes: list[str] = []
        if trace.duration_ms > self._timeout_ms:
            codes.append("TIMEOUT")
        if trace.error:
            codes.append("TOOL_ERROR")
        if not answered:
            codes.append("NO_OUTPUT")
        if answered and trace.num_turns >= self._high_turns and result.score < 0.3:
            codes.append("REASONING_DEPTH")
        if answered and result.score == 0.0:
            codes.append("WRONG_FORMAT")
        if answered and 0.0 < result.score < 0.5:
            codes.append("PARTIAL_MATCH")
        if result.signals.get("domain_mismatch"):
            codes.append("DOMAIN_MISMATCH")
        if not codes:
            codes.append("WRONG_FORMAT")
        context = [f"Score: {result.score:.2f}"]
        if trace.error:
            context.append(f"Error: {trace.error[:200]}")
        if trace.num_turns:
            context.append(f"Turns: {trace.num_turns}")
        signals = {**result.signals, "num_turns": trace.num_turns, "duration_ms": trace.duration_ms,
                   "cost_usd": trace.cost_usd, "output_len": len(result.actual_output), "score": result.score}
        return FailureAnalysis(instance_id=result.instance_id, failure_codes=tuple(codes), primary_code=codes[0],
                               signals=signals, context="; ".join(context))


__all__ = ["FAILURE_CODES", "DefaultClassifier", "FailureClassifier"]
