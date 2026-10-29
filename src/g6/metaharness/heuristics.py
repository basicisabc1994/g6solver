# SPDX-License-Identifier: Apache-2.0
"""Pull request #4's heuristic discovery, as a meta-harness proposer.

Each candidate is the base profile with one more reasoning operator's instruction appended to its prompt. The
candidates go through the meta-harness's own propose, evaluate, decide and approve steps, like every other proposal:
this module keeps no assessment, promotion or approval of its own, so there is one gate. Proposing a prompt revision
(PR #4's ``prompt_revision`` operator) is ``ModelStrategy``'s job, so it has no text here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from g6.metaharness.propose import DevRunner, ProposerView

#: Pull request #4's operators, in its order.
OPERATORS: tuple[str, ...] = ("regression", "boundary", "counterexample", "metamorphic", "decompose", "prompt_revision")
OPERATOR_TEXT: dict[str, str] = {
    "regression": "Before you answer, check that your answer keeps every behaviour the task says must not change.",
    "boundary": "Before you answer, try the smallest, the largest and the empty inputs the task allows.",
    "counterexample": "Before you answer, look for one input that would make your answer wrong; if you find one, fix "
                      "the answer.",
    "metamorphic": "Before you answer, check that an input the task relates to this one gives a consistent answer.",
    "decompose": "Split the task into steps and check each step's result before the next.",
}
#: The longest prompt a profile holds (``Profile.prompt``).
MAX_PROMPT = 8000


@dataclass
class HeuristicStrategy:
    """Up to ``max_children`` candidates, each adding one operator text the base prompt does not hold yet, in
    ``OPERATORS`` order; none once the base already holds ``max_generation`` of them, and none whose prompt would pass
    ``MAX_PROMPT`` characters."""

    max_children: int = 4
    max_generation: int = 3
    name: str = "heuristics"

    def __post_init__(self) -> None:
        if not 1 <= self.max_children <= len(OPERATOR_TEXT) or not 1 <= self.max_generation <= len(OPERATOR_TEXT):
            raise ValueError(f"max_children and max_generation are each from 1 to {len(OPERATOR_TEXT)}")

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        base = dict(view.base)
        prompt = str(base.get("prompt") or "")
        if sum(text in prompt for text in OPERATOR_TEXT.values()) >= self.max_generation:
            return []
        out: list[dict[str, Any]] = []
        for operator in OPERATORS:
            text = OPERATOR_TEXT.get(operator)
            if text is None or text in prompt:
                continue
            grown = f"{prompt}\n\n{text}" if prompt else text
            if len(grown) > MAX_PROMPT:
                continue
            out.append({**base, "prompt": grown})
            if len(out) >= self.max_children:
                break
        return out


__all__ = ["MAX_PROMPT", "OPERATORS", "OPERATOR_TEXT", "HeuristicStrategy"]
