# SPDX-License-Identifier: Apache-2.0
"""Pull request #4's heuristic discovery, as a meta-harness proposer.

Each candidate is the base profile with one more reasoning operator's instruction in its prompt. The candidates go
through the meta-harness's own propose, evaluate, decide and approve steps, like every other proposal: this module
keeps no assessment, promotion or approval of its own, so there is one gate. Proposing a prompt revision (PR #4's
``prompt_revision`` operator) is ``ModelStrategy``'s job, so it has no text here.

With a strategy store and a lineage ledger (0.3 design, 14.2), candidates are built from stored strategy artefacts:
each is the base profile with one derived child strategy applied to its prompt, the child made by the trusted adapter
``g6.learning.operators.derive``, which reads generation and child caps from the ledger and not from the prompt's
text. The proposal's candidate profile holds the instruction's bytes, so the decision digest a person approves binds
the exact strategy.

What was harvested from PR #4 and what was retired (section 6's rule: one copy, and nothing that duplicates a gate):
its six operator names and five reviewed texts are kept (the texts now live in ``g6.learning.operators``, where the
mappings use them); its lineage and its child and generation caps are kept, read from a ledger; its
``heuristic_discovery`` ``assess`` and ``promote`` are replaced by the meta-harness gate and the person's approval,
because they checked a recommended assessment and truthy approval fields, not a gate decision; and its
``cognitive_bootstrap`` calls are not ported, because they discarded the heuristic text they returned and so are no
evidence that a strategy ran. Neither package is on this line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from g6.learning.operators import OPERATOR_TEXT, LineageError, derive, proposal_only
from g6.learning.paired_eval import apply
from g6.metaharness.propose import DevRunner, ProposerView

#: Pull request #4's operators, in its order.
OPERATORS: tuple[str, ...] = ("regression", "boundary", "counterexample", "metamorphic", "decompose", "prompt_revision")
#: The longest prompt a profile holds (``Profile.prompt``).
MAX_PROMPT = 8000


@dataclass
class HeuristicStrategy:
    """Up to ``max_children`` candidates, each adding one operator text the base prompt does not hold yet, in
    ``OPERATORS`` order; none once the base already holds ``max_generation`` of them, and none whose prompt would pass
    ``MAX_PROMPT`` characters.

    With ``store``, ``ledger`` and ``parents`` (content digests of stored strategy artefacts), each candidate is
    instead the base profile with one child strategy of a parent applied to its prompt. ``lineage`` then maps each
    candidate profile's digest to its parent strategy's content digest (the proposal's ``parent_digests``), and
    ``derived`` to the child's content digest and operator."""

    max_children: int = 4
    max_generation: int = 3
    name: str = "heuristics"
    store: Any = None                    # a g6.learning.strategy.StrategyStore
    ledger: Any = None                   # the g6.core.ledger.ChainLedger that holds strategy lineage
    parents: tuple[str, ...] = ()
    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict)
    derived: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 1 <= self.max_children <= len(OPERATOR_TEXT) or not 1 <= self.max_generation <= len(OPERATOR_TEXT):
            raise ValueError(f"max_children and max_generation are each from 1 to {len(OPERATOR_TEXT)}")
        if (self.store is None) != (self.ledger is None) or (self.parents and self.store is None):
            raise ValueError("stored strategies need a store and a lineage ledger together")

    def propose(self, view: ProposerView, runner: DevRunner) -> list[dict[str, Any]]:
        if self.store is not None:
            return self._from_artefacts(view)
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

    def _from_artefacts(self, view: ProposerView) -> list[dict[str, Any]]:
        from g6.metaharness.schema import Profile

        base = dict(view.base)
        prompt = str(base.get("prompt") or "")
        out: list[dict[str, Any]] = []
        for parent in self.parents:
            for operator in OPERATORS:
                if proposal_only(operator):
                    continue                           # no reviewed mapping: a child would be only a proposal
                try:
                    child = derive(parent, operator, store=self.store, ledger=self.ledger)
                except LineageError:
                    continue                           # a cap read from the ledger, or an instruction past its limit
                made = apply(child, prompt)
                if not made.applied or len(made.prompt) > MAX_PROMPT:
                    continue
                candidate = {**base, "prompt": made.prompt}
                digest = Profile.model_validate(candidate).digest()
                self.lineage[digest] = (parent,)
                self.derived[digest] = {"strategy": child.content_digest, "operator": operator}
                out.append(candidate)
                if len(out) >= self.max_children:
                    return out
        return out


__all__ = ["MAX_PROMPT", "OPERATORS", "OPERATOR_TEXT", "HeuristicStrategy"]
