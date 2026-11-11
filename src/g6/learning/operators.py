# SPDX-License-Identifier: Apache-2.0
"""Content-changing operators over strategy artefacts, under lineage caps the caller cannot assert (0.3 design 14.2).

Pull request #4 named six operators. Five have a reviewed mapping here: the child's instruction is the parent's
instruction, a new line, and the operator's reviewed text; every other field is copied. ``prompt_revision`` has no
mapping (a model writes that one, which is the meta-harness's ``ModelStrategy``), and neither has any other name:
without a reviewed mapping a child is only a proposal, and ``derive`` makes none.

``derive`` is the trusted adapter. It resolves the parent's bytes from the store, reads lineage from the ledger and
nowhere else, and refuses a fourth generation, a fifth child of one parent and an instruction over the artefact's
limit. The child is a new strategy with its own digest: it has no receipts and no worth, whatever its parent has.

Nothing here assesses, promotes or approves a strategy, and nothing here calls a model. Promotion is the meta-harness
path (propose, evaluate, decide, approve) and the person's approval.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass

from g6.core.ledger import ChainLedger
from g6.learning.strategy import StrategyArtefact, StrategyError, StrategyStore

#: The reviewed operator texts (pull request #4's, as ``g6.metaharness.heuristics`` has carried them).
OPERATOR_TEXT: dict[str, str] = {
    "regression": "Before you answer, check that your answer keeps every behaviour the task says must not change.",
    "boundary": "Before you answer, try the smallest, the largest and the empty inputs the task allows.",
    "counterexample": "Before you answer, look for one input that would make your answer wrong; if you find one, fix "
                      "the answer.",
    "metamorphic": "Before you answer, check that an input the task relates to this one gives a consistent answer.",
    "decompose": "Split the task into steps and check each step's result before the next.",
}
MAX_CHILDREN = 4
MAX_GENERATION = 3
LINEAGE_KIND = "strategy_lineage"
LINEAGE = "G6_E_STRATEGY_LINEAGE"


def _append(operator: str) -> Callable[[StrategyArtefact], StrategyArtefact]:
    def mapping(parent: StrategyArtefact) -> StrategyArtefact:
        return dataclasses.replace(parent, instruction=parent.instruction + "\n" + OPERATOR_TEXT[operator])
    return mapping


#: One reviewed mapping per operator with a reviewed text. No other operator can make a child.
MAPPINGS: dict[str, Callable[[StrategyArtefact], StrategyArtefact]] = {name: _append(name) for name in OPERATOR_TEXT}


@dataclass(frozen=True)
class Lineage:
    child: str
    parent: str
    operator: str
    generation: int


class LineageError(StrategyError):
    """A derivation the adapter refuses (``G6_E_STRATEGY_LINEAGE``)."""

    def __init__(self, said: str) -> None:
        super().__init__(LINEAGE, said)


def proposal_only(operator: str) -> bool:
    """True when no reviewed mapping exists for ``operator``: a child under it is only a proposal."""
    return operator not in MAPPINGS


def lineage(ledger: ChainLedger) -> dict[str, Lineage]:
    """Child digest -> its lineage record, read from the ledger. A chain that does not check out raises."""
    problems = ledger.verify()
    if problems:
        raise LineageError(f"the lineage ledger does not check out: {problems[0]}")
    return {entry["id"]: Lineage(**entry["body"]) for entry in ledger.entries() if entry["kind"] == LINEAGE_KIND}


def derive(parent_digest: str, operator: str, *, store: StrategyStore, ledger: ChainLedger) -> StrategyArtefact:
    """The child of the stored strategy ``parent_digest`` under ``operator``: stored, with its lineage appended to the
    ledger. Refused, with nothing stored, for an operator with no reviewed mapping, a generation above
    ``MAX_GENERATION``, a parent that already has ``MAX_CHILDREN`` children, and an instruction past the artefact's
    limit. Deriving the same child again answers the child already kept."""
    parent = store.resolve(parent_digest)
    if proposal_only(operator):
        raise LineageError(f"the operator {operator!r} has no reviewed mapping, so its child is only a proposal")
    records = lineage(ledger)
    try:
        child = MAPPINGS[operator](parent)
    except StrategyError as exc:
        raise LineageError(f"the child is not a strategy artefact: {exc}") from None
    digest = child.content_digest
    known = records.get(digest)
    if known is not None:
        if (known.parent, known.operator) != (parent_digest, operator):
            raise LineageError("these bytes are already another parent's child")
        return store.resolve(digest)
    generation = records[parent_digest].generation + 1 if parent_digest in records else 1
    if generation > MAX_GENERATION:
        raise LineageError(f"generation {generation} is past the cap of {MAX_GENERATION}")
    if sum(record.parent == parent_digest for record in records.values()) >= MAX_CHILDREN:
        raise LineageError(f"the parent already has {MAX_CHILDREN} children")
    store.put(child)
    ledger.append(LINEAGE_KIND, digest, dataclasses.asdict(Lineage(digest, parent_digest, operator, generation)))
    return child


__all__ = ["LINEAGE", "LINEAGE_KIND", "MAPPINGS", "MAX_CHILDREN", "MAX_GENERATION", "OPERATOR_TEXT", "Lineage",
           "LineageError", "derive", "proposal_only", "lineage"]
