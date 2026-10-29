# SPDX-License-Identifier: Apache-2.0
"""Bounded, fail-closed adaptive experiment selection. Scheduling only: never executes code."""
from __future__ import annotations

from dataclasses import dataclass, replace

from g6.complexity.ir import Hypothesis


@dataclass(frozen=True)
class Experiment:
    id: str
    template: str
    hypothesis_ids: tuple[str, ...]
    cost_units: int
    allowed: bool = False
    requires_independent_executor: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.template or self.cost_units <= 0:
            raise ValueError("experiment needs identifier, template and positive cost")


@dataclass(frozen=True)
class Receipt:
    experiment_id: str
    tree_digest: str
    environment_digest: str
    checker_digest: str
    provenance: str
    outcome: str
    isolated: bool

    def __post_init__(self) -> None:
        if not all((self.experiment_id, self.tree_digest, self.environment_digest,
                    self.checker_digest, self.provenance)):
            raise ValueError("receipt needs experiment and four provenance bindings")


@dataclass(frozen=True)
class VerificationState:
    tree_digest: str
    environment_digest: str
    checker_digest: str
    hypotheses: tuple[Hypothesis, ...]
    budget_units: int
    attempted: tuple[str, ...] = ()
    observations: tuple[Receipt, ...] = ()
    refusals: tuple[str, ...] = ()
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if not all((self.tree_digest, self.environment_digest, self.checker_digest)):
            raise ValueError("verification state requires pinned digests")
        if self.budget_units < 0:
            raise ValueError("negative budget")


def next_experiment(state: VerificationState, candidates: tuple[Experiment, ...]) -> Experiment | None:
    """Greedy severity-weighted NEW open-hypothesis coverage / cost. Stable id tie-break."""
    weights = {"low": 1, "medium": 3, "high": 9}
    open_h = {h.id: weights[h.severity] for h in state.hypotheses if h.state in ("open", "ambiguous")}
    possible = [e for e in candidates if e.allowed and e.id not in state.attempted
                and e.cost_units <= state.budget_units and
                any(i in open_h for i in e.hypothesis_ids)]
    if not possible:
        return None
    return min(possible, key=lambda e: (
        -sum(open_h.get(i, 0) for i in set(e.hypothesis_ids)) / e.cost_units, e.id
    ))


def reduce_observation(state: VerificationState, experiment: Experiment, receipt: Receipt) -> VerificationState:
    """Receipt is caller-supplied until host custody validates it; never auto-supports a hypothesis.

    Only the existing confined executor should issue trusted host receipts. Missing isolation and
    stale bindings are refusals, not successful experiments. Non-pass outcomes remain uncertainty.
    """
    if experiment.id in state.attempted:
        raise ValueError("experiment already attempted")
    if receipt.experiment_id != experiment.id:
        raise ValueError("receipt belongs to a different experiment")
    attempted = (*state.attempted, experiment.id)
    budget = max(0, state.budget_units - experiment.cost_units)
    issue = None
    if not experiment.allowed:
        issue = "template_not_allowed"
    elif experiment.cost_units > state.budget_units:
        issue = "budget_exhausted"
    elif (receipt.tree_digest != state.tree_digest or receipt.environment_digest != state.environment_digest
          or receipt.checker_digest != state.checker_digest):
        issue = "stale_receipt"
    elif not receipt.isolated:
        issue = "isolation_unestablished"
    elif receipt.outcome not in ("pass", "fail", "timeout", "error", "skipped"):
        issue = "unknown_outcome"
    if issue:
        return replace(state, attempted=attempted, budget_units=budget,
                       refusals=(*state.refusals, f"{experiment.id}:{issue}"))
    # Do not derive authoritative labels or hypothesis truth from a raw receipt;
    # independent gate adjudication must perform that task with locked obligations.
    return replace(state, attempted=attempted, observations=(*state.observations, receipt),
                   budget_units=budget)


def rebase(state: VerificationState, *, tree_digest: str, environment_digest: str,
           checker_digest: str) -> VerificationState:
    """New artifact means all old observations are retained as history, not eligible evidence."""
    if not all((tree_digest, environment_digest, checker_digest)):
        raise ValueError("new snapshot needs pinned digests")
    return replace(state, tree_digest=tree_digest, environment_digest=environment_digest,
                   checker_digest=checker_digest, attempted=(), observations=(),
                   hypotheses=tuple(replace(h, state="open") for h in state.hypotheses))
