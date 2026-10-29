# SPDX-License-Identifier: Apache-2.0
"""Pull request #4's cognitive bootstrap, as a bounded meta-harness helper.

A model (``thinker``, any ``(stage, prompt) -> text`` callable) is walked through PR #4's stages: constraints, first
principles, heuristics, a self-assessment, a decomposition into subgoals, one step per subgoal, a synthesis, then up to
``max_revisions`` rounds of critique and revision. Every prompt is the one catalogue's strict render of
``cb-<stage>`` (``g6.workspace.align_prompt_library.catalogue``), filled with that template's own placeholders and no
others. Every output is an untrusted draft: the result is ``qualified-draft`` at best, and nothing here decides a gate.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from g6.workspace.align_prompt_library import catalogue
from g6.workspace.align_prompt_library.library import placeholders

#: The most characters of one reply kept, and of one item of a JSON list.
MAX_REPLY = 40_000
MAX_ITEM = 512


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
    return tuple(x[:MAX_ITEM] for x in value[:cap] if isinstance(x, str) and x.strip())


def bootstrap(objective: str, *, thinker: Callable[[str, str], str], context: str = "",
              frozen_constraints: tuple[str, ...] = (), max_subgoals: int = 6, max_revisions: int = 2
              ) -> BootstrapResult:
    """Walk ``thinker`` through the stages; at most ``5 + max_subgoals + 1 + 2 * max_revisions`` calls."""
    if not isinstance(objective, str) or not objective.strip() or not (1 <= max_subgoals <= 12
                                                                        and 0 <= max_revisions <= 4):
        raise ValueError("objective or budget invalid")
    calls = 0

    def ask(stage: str, **values: object) -> str:
        nonlocal calls
        name = "cb-" + stage.replace("_", "-")
        offered = {"objective": objective, "context": context, **values}
        wanted = placeholders(catalogue.entry(name).template)
        prompt = catalogue.render(name, {key: str(offered[key]) for key in wanted})["text"]
        calls += 1
        raw = thinker(stage, prompt)
        if not isinstance(raw, str):
            raise TypeError("thinker must return text")
        return raw[:MAX_REPLY]

    inferred = _array(ask("constraints"), 16)
    constraints = tuple(dict.fromkeys(frozen_constraints + inferred))
    ask("first_principles", constraints=constraints)
    ask("heuristics", constraints=constraints)
    ask("self_assessment", constraints=constraints)
    subgoals = _array(ask("decompose", constraints=constraints), max_subgoals)
    observations = [ask("execute", subgoal=subgoal, constraints=constraints) for subgoal in subgoals]
    candidate = ask("synthesize", observations=observations)
    critiques = []
    for _ in range(max_revisions):
        critique = ask("critique", candidate=candidate, constraints=constraints)
        critiques.append(critique)
        candidate = ask("revise", candidate=candidate, critique=critique)
    return BootstrapResult(candidate, constraints, subgoals, tuple(critiques), calls)


__all__ = ["MAX_REPLY", "BootstrapResult", "bootstrap"]
