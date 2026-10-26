# SPDX-License-Identifier: Apache-2.0
"""The baselines a learned strategy is compared against, and a skill renderer. Off by default: not exported from
``g6.learning``; import them from ``g6.learning.baselines``.

- ``T0Strategy`` proposes nothing, so a run is a plain paired evaluation of the raw agent (both arms are the same).
- ``TemplateStrategy`` merges the archive's T1 (one audit cycle) and T2 (fixed-rule evolution): each cycle it proposes
  one theory for the most frequent primary failure code. T2's ``ConfigOverride`` (a temperature) is not ported: it was
  carried, exported and counted, but never reached the agent (audit 006, LL-009).
- ``render_skill`` is the archive's ``SkillGenerator`` as a pure function: a markdown guide rendered from a theory,
  for a bundle. It writes nothing to a store, and nothing measures a skill document.

None of these has been shown to improve an agent; the experiment in audit 006 §6 has not been run.
"""
from __future__ import annotations

from collections.abc import Sequence

from g6.learning.failure_classifier import DefaultClassifier, FailureClassifier
from g6.learning.ooda import ObservationResult
from g6.learning.schema import FailureAnalysis, SkillDocument, Theory
from g6.learning.strategies import THEORY_TEMPLATES
from g6.learning.theory_store import TheoryStore

MAX_SKILL_CHARS = 2000


class T0Strategy:
    """No learning: proposes nothing."""

    name = "T0"

    def propose(self, observations: ObservationResult, store: TheoryStore, *, cycle: int) -> list[Theory]:
        return []


class TemplateStrategy:
    """One theory per cycle for the most frequent primary failure code not yet covered in the store's scope."""

    name = "template"

    def __init__(self, classifier: FailureClassifier | None = None) -> None:
        self._classifier = classifier or DefaultClassifier()

    def propose(self, observations: ObservationResult, store: TheoryStore, *, cycle: int) -> list[Theory]:
        by_code: dict[str, list[str]] = {}
        for result in observations.results:
            if not result.passed:
                by_code.setdefault(self._classifier.classify(result).primary_code, []).append(result.instance_id)
        uncovered = {code: ids for code, ids in by_code.items() if not store.find_by_failure_code(code)}
        if not uncovered:
            return []
        code, sources = max(uncovered.items(), key=lambda item: (len(item[1]), item[0]))
        hint = THEORY_TEMPLATES.get(code, "Re-read the task's requirements before answering.")
        return [Theory.new(code, f"Recurring failure ({len(sources)} of {len(observations.results)} tasks): {hint}",
                           rationale=f"Most frequent failure in cycle {cycle}", source_instances=sources)]


def render_skill(theory: Theory, analyses: Sequence[FailureAnalysis] = ()) -> SkillDocument | None:
    """A markdown guide for ``theory`` (at most ``MAX_SKILL_CHARS``), or ``None`` when the theory has no text."""
    if not theory.instruction.strip():
        return None
    lines = [f"# Handling {theory.failure_code}", "", "## When to use", "",
             f"Tasks that fail with {theory.failure_code}.", "", "## Steps", "", f"1. {theory.instruction}"]
    if theory.rationale:
        lines += ["", "## Rationale", "", theory.rationale]
    contexts = [a.context for a in analyses[:3] if a.context]
    if contexts:
        lines += ["", "## Observed failures", ""] + [f"- {c}" for c in contexts]
    lines += ["", "## Pitfalls", "", "- Check that the failure code matches before applying this.",
              "- Unmeasured: this guide has no paired evidence of its own."]
    content = "\n".join(lines)
    if len(content) > MAX_SKILL_CHARS:
        content = content[:MAX_SKILL_CHARS].rsplit("\n", 1)[0]
    return SkillDocument(content=content, theory_id=theory.theory_id)


__all__ = ["MAX_SKILL_CHARS", "T0Strategy", "TemplateStrategy", "render_skill"]
