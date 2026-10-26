# SPDX-License-Identifier: Apache-2.0
"""``reachability_bound``: an upper bound on P(the agent reaches an unsafe state within N steps) on the caller's model.

The archive's ``SafetyVerifier._union_bound``, kept: the state distribution is propagated forward step by step, and by
Boole's inequality ``vub = min(1, sum over t = 0..N of P(the agent is in an unsafe state at step t))`` is at least the
probability of reaching an unsafe state within N steps. It can exceed it: an agent that stays in an unsafe state is
counted at every step it is there.

Fixed here:

* CSF-007: step 0 counts. The archive scored only successor states, so an agent that started in an unsafe state had
  ``vub = 0``, and N=0 was vacuously safe (``ResourceTriple`` now needs N >= 1).
* CSF-006: there is no ``worst_case`` and no method switch. ``|unsafe|/|states| * N`` is not a bound (it ignores the
  transitions), and a strategy could select it.
* CSF-008: ``p_unsafe`` is ``vub``. The archive left it 0.0, and its main consumer reported and acted on that.
* CSF-013: no confidence interval or PAC ``delta``. The number is exact arithmetic on the model given, not an estimate
  from samples, so an interval around it would mean nothing.

A bound is a computed number on the caller's model, never ``verified``: ``qualified-draft`` when it is within epsilon,
``blocked-escalated`` when it is not, with the model and its assumptions in the evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from g6.csf.schema import MODEL_INVALID, BoundedAgent, CSFInputError, check_epsilon, check_id, labelled

MODEL_BOUND = "G6_E_CSF_MODEL_BOUND"
BOUND_EXCEEDED = "G6_E_CSF_BOUND_EXCEEDED"

#: What every bound depends on; carried in the evidence of every report.
ASSUMPTIONS = (
    "The states, transition probabilities and unsafe labels are the caller's model; nothing in g6.csf measured them.",
    "The model is a time-homogeneous Markov chain: each step depends only on the current state.",
    "The bound sums P(in an unsafe state at step t) for t = 0..N (Boole's inequality): it is at least the probability "
    "of reaching an unsafe state within N steps and can exceed it.",
    "The outgoing probabilities of each state sum to 1 within 1e-9.",
)


@dataclass(frozen=True)
class StepHazard:
    """Step ``step``: the probability mass in unsafe states, and the running bound up to and including it."""

    step: int
    unsafe_mass: float
    cumulative: float


@dataclass(frozen=True)
class SafetyReport:
    """The union bound ``vub`` on the model, whether it is within ``epsilon``, and the per-step terms it sums."""

    epsilon: float
    vub: float
    meets_bound: bool
    steps: tuple[StepHazard, ...]
    model: dict[str, Any]
    request_id: str = ""
    run_id: str = ""
    method: str = field(default="union_bound", init=False)

    @property
    def p_unsafe(self) -> float:
        """The same number as ``vub`` (audit 012, CSF-008: the archive never set it, so it read 0.0)."""
        return self.vub

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.meets_bound else "blocked-escalated"

    def to_envelope(self) -> dict[str, Any]:
        """The report as a library envelope (``g6.core.reliability_envelope``) with a ``G6_E_*`` warning card."""
        horizon = self.model.get("horizon_N")
        if self.meets_bound:
            code = MODEL_BOUND
            action = "Check the transition probabilities against observed behaviour before relying on the bound."
            message = (f"Upper bound {self.vub:.6g} <= epsilon {self.epsilon:g} on the model supplied: the probability "
                       f"of reaching an unsafe state within {horizon} steps is at most {self.vub:.6g} if the model's "
                       "states, probabilities and unsafe labels are right. Nothing here measured them, so this is not "
                       "verification.")
        else:
            code, action = BOUND_EXCEEDED, "Do not proceed on this model: change the plan or the model, or escalate."
            message = (f"Upper bound {self.vub:.6g} > epsilon {self.epsilon:g}: the model does not show that the "
                       f"probability of reaching an unsafe state within {horizon} steps is within epsilon (a union "
                       "bound can exceed the true probability).")
        evidence = {
            "kind": "csf_reachability_bound", "method": self.method, "epsilon": self.epsilon, "vub": self.vub,
            "p_unsafe": self.p_unsafe, "meets_bound": self.meets_bound,
            "unsafe_mass_by_step": [s.unsafe_mass for s in self.steps], "measured": False, "model": self.model,
            "assumptions": list(ASSUMPTIONS),
        }
        envelope = labelled(self, state=self.completion_state, code=code, message=message, action=action,
                            evidence=[evidence])
        return {**envelope, "p_unsafe": self.p_unsafe}


def _unsafe_mass_by_step(agent: BoundedAgent) -> list[float]:
    """P(the agent is in an unsafe state at step t) on the model, for t = 0..N."""
    dist = {agent.initial_state: 1.0}
    masses: list[float] = []
    for t in range(agent.resources.N + 1):
        # audit 012, CSF-007: t starts at 0, so an unsafe initial state counts.
        masses.append(math.fsum(mass for state, mass in dist.items() if state in agent.unsafe_states))
        if t == agent.resources.N:
            break
        nxt: dict[str, float] = {}
        for state, mass in dist.items():
            for outcome in agent.kernel[state]:
                nxt[outcome.next_state] = nxt.get(outcome.next_state, 0.0) + mass * outcome.probability
        dist = nxt
    return masses


def reachability_bound(agent: BoundedAgent, epsilon: float, *, request_id: str = "",
                       run_id: str = "") -> SafetyReport:
    """The union bound on P(reach an unsafe state within ``agent.resources.N`` steps), counting the initial state,
    compared with ``epsilon`` in [0, 1]. ``meets_bound`` is ``vub <= epsilon``; ``p_unsafe`` is ``vub``. A model with
    no unsafe state is refused (``G6_E_CSF_MODEL_INVALID``), never bounded at 0."""
    if not isinstance(agent, BoundedAgent):
        raise CSFInputError(MODEL_INVALID, f"agent must be a BoundedAgent, got {type(agent).__name__}")
    if not agent.unsafe_states:
        # With no unsafe state every bound is 0 whatever the transitions say: an unnamed hazard is not safety.
        raise CSFInputError(MODEL_INVALID, "the model names no unsafe state, so the bound would be 0 whatever its "
                                           "transitions say; name the states that count as unsafe")
    eps = check_epsilon(epsilon)
    steps: list[StepHazard] = []
    total = 0.0
    for t, mass in enumerate(_unsafe_mass_by_step(agent)):
        total += mass
        steps.append(StepHazard(t, mass, min(total, 1.0)))
    vub = min(total, 1.0)
    return SafetyReport(epsilon=eps, vub=vub, meets_bound=vub <= eps, steps=tuple(steps), model=agent.describe(),
                        request_id=check_id(request_id, "request_id"), run_id=check_id(run_id, "run_id"))


__all__ = ["ASSUMPTIONS", "BOUND_EXCEEDED", "MODEL_BOUND", "SafetyReport", "StepHazard", "reachability_bound"]
