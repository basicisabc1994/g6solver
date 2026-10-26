# SPDX-License-Identifier: Apache-2.0
"""Strategies propose theories; the runner measures them. ``T3Strategy`` is the archive's theory builder, rewritten.

A strategy has one job, ``propose(observations, store, *, cycle)``: given the current harness's results on every
training instance, return the theories to measure this cycle. Theories whose id is not in the store are added as
candidates; candidates already there are measured as stored. The runner runs each one with and without it on the
same instances and credits it only from the pairs that disagree (audit 006, LL-005). The archive's T3 credited every
theory in the prompt with the agent's pass or fail on every instance, so a hint added while the agent already passed
90% became "strong" and stopped the run as "theory saturation".

The theory texts are seven fixed templates keyed by heuristic failure codes. What a run learns is which of them help
on paired evidence, not new text.
"""
from __future__ import annotations

from typing import Protocol

from g6.learning.failure_classifier import DefaultClassifier, FailureClassifier
from g6.learning.ooda import ObservationResult
from g6.learning.schema import Theory
from g6.learning.theory_store import TheoryStore

THEORY_TEMPLATES: dict[str, str] = {
    "NO_OUTPUT": "Give an answer: an empty response always fails.",
    "WRONG_FORMAT": "Check the expected output format and structure before answering.",
    "TIMEOUT": "Take fewer steps; apply a simpler approach early.",
    "TOOL_ERROR": "Check that a tool exists and that its arguments are well formed before calling it.",
    "REASONING_DEPTH": "Commit to a best answer sooner instead of extending the analysis.",
    "PARTIAL_MATCH": "Cover every part the task asks for before answering.",
    "DOMAIN_MISMATCH": "The task may be outside the usual domain; state the assumptions you rely on.",
}


class LearningStrategy(Protocol):
    name: str

    def propose(self, observations: ObservationResult, store: TheoryStore, *, cycle: int) -> list[Theory]: ...


class T3Strategy:
    """Propose the template for each failure code seen in the current results that no theory in the store's scope
    covers yet (candidate, active or retired), plus the scope's existing candidates. The runner skips any theory it
    has already measured in this run."""

    name = "T3"

    def __init__(self, classifier: FailureClassifier | None = None) -> None:
        self._classifier = classifier or DefaultClassifier()

    def propose(self, observations: ObservationResult, store: TheoryStore, *, cycle: int) -> list[Theory]:
        failing: dict[str, list[str]] = {}
        for result in observations.results:
            if not result.passed:
                for code in self._classifier.classify(result).failure_codes:
                    failing.setdefault(code, []).append(result.instance_id)
        proposals = store.theories(status="candidate")
        for code, sources in failing.items():
            if store.find_by_failure_code(code):
                continue
            known = code in THEORY_TEMPLATES
            proposals.append(Theory.new(
                code, THEORY_TEMPLATES.get(code, f"Avoid {code} failures."),
                rationale=f"Proposed in cycle {cycle} from {len(sources)} failing training task(s)",
                prior_strength=0.5 if known else 0.3, source_instances=sources))
        return proposals


__all__ = ["THEORY_TEMPLATES", "LearningStrategy", "T3Strategy"]
