# SPDX-License-Identifier: Apache-2.0
"""Deterministic task-complexity heuristic: simple, moderate or complex, from the goal's wording. No model call.

It reads surface features (length, multi-part markers, domain words, question or imperative openings), so it
misjudges reworded tasks; it decides how many steps run, not whether an answer is right.
"""
from __future__ import annotations

import re
from enum import Enum


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


_MULTI_PART = re.compile(
    r"\b(?:and\s+then|after\s+that|next|finally|additionally|furthermore|step\s+\d|phase\s+\d|"
    r"first\s*,|second\s*,|third\s*,|lastly|subsequently|moreover)\b",
    re.IGNORECASE,
)
_DOMAIN_WORDS = frozenset({
    "optimize", "optimization", "algorithm", "architecture", "infrastructure", "distributed", "concurrent",
    "parallel", "scalable", "microservice", "pipeline", "workflow", "orchestrat", "deploy", "kubernetes", "docker",
    "database", "migration", "refactor", "integration", "authentication", "authorization", "encryption",
    "compliance", "regression", "performance",
})
_IMPERATIVE = re.compile(
    r"^(?:implement|build|create|design|develop|architect|construct|engineer|deploy|configure|set\s+up|"
    r"integrate|migrate|refactor|optimize|scale|write|code|program|automate)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"^(?:what\s+is|calculate|compute|find\s+the|how\s+many|how\s+much|convert|translate|define|explain|list|"
    r"describe|summarize|what\s+are|who\s+is|when\s+did|where\s+is)\b",
    re.IGNORECASE,
)
_LIST_ITEM = re.compile(r"(?:^|\n)\s*(?:\d+[.)]|-|\*)\s")


def classify_task(goal: str, context: str | None = None) -> TaskComplexity:
    """Classify a goal by surface heuristics. An empty or very short goal is ``moderate``."""
    goal = (goal or "").strip()
    if len(goal) < 3:
        return TaskComplexity.MODERATE
    lowered = goal.lower()
    words = len(goal.split())
    parts = len(_MULTI_PART.findall(goal))
    commas = goal.count(",")
    domain = sum(1 for w in _DOMAIN_WORDS if w in lowered)
    items = len(_LIST_ITEM.findall(goal))
    question = bool(_QUESTION.match(goal))
    imperative = bool(_IMPERATIVE.match(goal))
    rich_context = bool(context and len(context.strip()) > 50)

    # A short build/implement goal with domain words is not simple ("Implement distributed consensus").
    if imperative and domain >= 1:
        if domain >= 3 and (parts >= 2 or rich_context):
            return TaskComplexity.COMPLEX
        return TaskComplexity.MODERATE
    if words <= 50 and parts == 0 and items <= 1 and commas <= 2:
        if question and domain <= 2:
            return TaskComplexity.SIMPLE
        if words <= 25 and domain == 0 and not imperative:
            return TaskComplexity.SIMPLE
    if (words > 150 or parts >= 3 or items >= 4 or domain >= 3 or (words > 80 and parts >= 2)
            or (domain >= 2 and rich_context)):
        return TaskComplexity.COMPLEX
    return TaskComplexity.MODERATE


__all__ = ["TaskComplexity", "classify_task"]
