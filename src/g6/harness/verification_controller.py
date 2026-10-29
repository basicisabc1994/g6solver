# SPDX-License-Identifier: Apache-2.0
"""Bounded, fail-closed adaptive experiment selection. Scheduling only: never executes code.

An observation is what a confined run's record (``g6.workforce.repo_tests``) says, read by :func:`from_run`: its
isolation backend, its network flag and its receipt come from the run record, never from a caller.
:func:`reduce_observation` keeps an observation only when it is bound to the state's exact artifact, checker and
environment digests, ran under an isolation backend with no network, and, when it names a receipt, G6's receipt store
matches that receipt to the artifact. A mutant's run is never receipted (it is not the artifact under review), so an
observation with no receipt is kept and says so. No observation decides a hypothesis: that is the gate's job, with
locked obligations.

``ExperimentState`` is pull request #2's ``VerificationState``, renamed (coordinator's ruling 4) so that the name stays
free for the mission's verification state, a different object.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from g6.complexity.ir import Hypothesis
from g6.workforce import receipt_store

Outcome = Literal["pass", "fail", "timeout", "error", "skipped"]
OUTCOMES: tuple[str, ...] = ("pass", "fail", "timeout", "error", "skipped")
#: How a run record's verdict reads as an outcome; every other verdict is an error.
_VERDICTS = {"passed": "pass", "failed": "fail", "timed_out": "timeout"}


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
class RunObservation:
    """One confined run, as its run record says: the artifact, checker and environment it was bound to, its outcome,
    the isolation backend that confined it (None when none did), whether it had the network, and its receipt ("" when
    the run was not receipted, as a mutant's never is)."""

    experiment_id: str
    artifact_digest: str
    checker_digest: str
    environment_digest: str
    outcome: Outcome
    backend: str | None
    network: bool
    receipt: str = ""

    def __post_init__(self) -> None:
        if not all((self.experiment_id, self.artifact_digest, self.checker_digest, self.environment_digest)):
            raise ValueError("an observation needs its experiment and three digest bindings")


def from_run(run: Mapping[str, Any], *, experiment_id: str, artifact_digest: str, checker_digest: str,
             environment_digest: str) -> RunObservation:
    """The observation a ``repo_tests`` run record makes: its outcome from ``verdict``, its backend and network from
    ``isolation`` and its receipt from ``receipt``, all read from the record, never from the caller."""
    isolation = run.get("isolation") if isinstance(run.get("isolation"), Mapping) else {}
    backend = isolation.get("backend")
    receipt = run.get("receipt")
    return RunObservation(experiment_id, artifact_digest, checker_digest, environment_digest,
                          _VERDICTS.get(run.get("verdict"), "error"),  # type: ignore[arg-type]
                          backend if isinstance(backend, str) and backend else None,
                          bool(isolation.get("network")), receipt if isinstance(receipt, str) else "")


def receipt_matches(receipt: str, *, artifact_digest: str) -> bool:
    """Whether G6's receipt store holds ``receipt`` for a run that began on ``artifact_digest``."""
    return receipt_store.check(receipt, artifact_digest=artifact_digest)[0] == receipt_store.MATCHED


@dataclass(frozen=True)
class ExperimentState:
    tree_digest: str
    environment_digest: str
    checker_digest: str
    hypotheses: tuple[Hypothesis, ...]
    budget_units: int
    attempted: tuple[str, ...] = ()
    observations: tuple[RunObservation, ...] = ()
    refusals: tuple[str, ...] = ()
    stop_reason: str = ""

    def __post_init__(self) -> None:
        if not all((self.tree_digest, self.environment_digest, self.checker_digest)):
            raise ValueError("experiment state requires pinned digests")
        if self.budget_units < 0:
            raise ValueError("negative budget")


def next_experiment(state: ExperimentState, candidates: tuple[Experiment, ...]) -> Experiment | None:
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


def reduce_observation(state: ExperimentState, experiment: Experiment, observed: RunObservation) -> ExperimentState:
    """``state`` after ``observed``: kept, or refused as ``"<experiment id>:<issue>"``, never a supported hypothesis.

    The issues: ``template_not_allowed``, ``budget_exhausted``, ``stale_observation`` (a digest differs from the
    state's), ``isolation_unestablished`` (no backend confined the run, or it had the network),
    ``receipt_not_matched`` (it names a receipt the store does not match to its artifact) and ``unknown_outcome``.
    Non-pass outcomes remain uncertainty.
    """
    if experiment.id in state.attempted:
        raise ValueError("experiment already attempted")
    if observed.experiment_id != experiment.id:
        raise ValueError("observation belongs to a different experiment")
    attempted = (*state.attempted, experiment.id)
    budget = max(0, state.budget_units - experiment.cost_units)
    issue = None
    if not experiment.allowed:
        issue = "template_not_allowed"
    elif experiment.cost_units > state.budget_units:
        issue = "budget_exhausted"
    elif (observed.artifact_digest != state.tree_digest or observed.environment_digest != state.environment_digest
          or observed.checker_digest != state.checker_digest):
        issue = "stale_observation"
    elif not observed.backend or observed.network:
        issue = "isolation_unestablished"
    elif observed.receipt and not receipt_matches(observed.receipt, artifact_digest=observed.artifact_digest):
        issue = "receipt_not_matched"
    elif observed.outcome not in OUTCOMES:
        issue = "unknown_outcome"
    if issue:
        return replace(state, attempted=attempted, budget_units=budget,
                       refusals=(*state.refusals, f"{experiment.id}:{issue}"))
    # Do not derive authoritative labels or hypothesis truth from a raw observation;
    # independent gate adjudication must perform that task with locked obligations.
    return replace(state, attempted=attempted, observations=(*state.observations, observed),
                   budget_units=budget)


def rebase(state: ExperimentState, *, tree_digest: str, environment_digest: str,
           checker_digest: str) -> ExperimentState:
    """New artifact means all old observations are retained as history, not eligible evidence."""
    if not all((tree_digest, environment_digest, checker_digest)):
        raise ValueError("new snapshot needs pinned digests")
    return replace(state, tree_digest=tree_digest, environment_digest=environment_digest,
                   checker_digest=checker_digest, attempted=(), observations=(),
                   hypotheses=tuple(replace(h, state="open") for h in state.hypotheses))


__all__ = ["OUTCOMES", "Experiment", "ExperimentState", "Outcome", "RunObservation", "from_run", "next_experiment",
           "rebase", "receipt_matches", "reduce_observation"]
