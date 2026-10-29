"""Bounded model-neutral cognitive bootstrap: all stage outputs are untrusted drafts."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
import json
from .prompts import render_prompt

@dataclass(frozen=True)
class BootstrapResult:
    candidate: str
    constraints: tuple[str, ...]
    subgoals: tuple[str, ...]
    critiques: tuple[str, ...]
    calls: int
    status: str = "qualified-draft"

def _array(raw: str, cap: int) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(x[:512] for x in value[:cap] if isinstance(x,str) and x.strip())

def bootstrap(objective: str, *, thinker: Callable[[str,str],str],
              context: str = "", frozen_constraints: tuple[str,...] = (),
              max_subgoals: int = 6, max_revisions: int = 2) -> BootstrapResult:
    if not objective.strip() or not (1 <= max_subgoals <= 12 and 0 <= max_revisions <= 4):
        raise ValueError("objective or budget invalid")
    calls = 0
    def ask(stage: str, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        raw = thinker(stage, render_prompt(stage, objective=objective, context=context, **kwargs))
        if not isinstance(raw,str):
            raise TypeError("thinker must return text")
        return raw[:40000]
    inferred = _array(ask("constraints"), 16)
    constraints = tuple(dict.fromkeys(frozen_constraints + inferred))
    ask("first_principles", constraints=constraints)
    ask("heuristics", constraints=constraints)
    ask("self_assessment", constraints=constraints)
    subgoals = _array(ask("decompose", constraints=constraints),max_subgoals)
    observations = []
    for subgoal in subgoals:
        observations.append(ask("execute", subgoal=subgoal, constraints=constraints))
    candidate = ask("synthesize", observations=observations)
    critiques = []
    for _ in range(max_revisions):
        critique = ask("critique", candidate=candidate, constraints=constraints)
        critiques.append(critique)
        candidate = ask("revise", candidate=candidate, critique=critique)
    return BootstrapResult(candidate,constraints,subgoals,tuple(critiques),calls)
