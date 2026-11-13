# SPDX-License-Identifier: Apache-2.0
"""PR #4's bounded heuristic contracts, copied here unchanged in meaning: the PR is a draft with no tests, and G6's package stays
as reviewed. Its ``bootstrap`` engine is left out: it discards three stages' output and can return no subgoals."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

OPERATORS = ("regression", "boundary", "counterexample", "metamorphic", "decompose", "prompt_revision")


@dataclass(frozen=True)
class Heuristic:
    id: str
    operator: str
    parent_id: str = ""
    generation: int = 0


@dataclass(frozen=True)
class Trial:
    heuristic_id: str
    case_id: str
    baseline_success: bool
    candidate_success: bool
    baseline_cost: int
    candidate_cost: int
    evaluator_digest: str


def mutate(parent: Heuristic, max_children: int = 4, max_generation: int = 3) -> tuple[Heuristic, ...]:
    """Children of ``parent``, one for each other operator."""
    if parent.operator not in OPERATORS or not 0 <= max_children <= len(OPERATORS):
        raise ValueError("invalid mutation")
    if parent.generation >= max_generation:
        return ()
    return tuple(Heuristic(hashlib.sha256(f"{parent.id}:{op}".encode()).hexdigest()[:16], op, parent.id, parent.generation + 1)
                 for op in OPERATORS if op != parent.operator)[:max_children]


def assess(candidate: Heuristic, cases: Sequence[str], evaluator: Callable[[Heuristic, str], Trial],
           evaluator_digest: str) -> dict[str, Any]:
    """The candidate against the baseline on fixed cases: a case the baseline solved and the candidate did not is a veto."""
    if not cases or len(set(cases)) != len(cases) or not evaluator_digest:
        raise ValueError("fixed cases and evaluator identity required")
    trials = tuple(evaluator(candidate, case) for case in cases)
    if any(t.heuristic_id != candidate.id or t.case_id != case or t.evaluator_digest != evaluator_digest
           or min(t.baseline_cost, t.candidate_cost) < 0 for t, case in zip(trials, cases, strict=True)):
        raise ValueError("invalid receipt")
    regression = any(t.baseline_success and not t.candidate_success for t in trials)
    benefit = any((not t.baseline_success and t.candidate_success)
                  or (t.candidate_success and t.candidate_cost < t.baseline_cost) for t in trials)
    return {"candidate": candidate, "trials": trials, "recommended": benefit and not regression, "status": "qualified-draft"}
