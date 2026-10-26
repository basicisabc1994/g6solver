# SPDX-License-Identifier: Apache-2.0
"""A finite Markov decision process and exact value iteration.

The MDP is a small protocol; value iteration sweeps it synchronously to the caller's tolerance. Deterministic:
states in declared order, greedy ties by declared action order, no randomness. Fail closed: a malformed transition
distribution raises, and a sweep that does not converge within ``max_iterations`` (or before ``deadline``) raises
rather than returning an approximate answer as the answer.
"""
from __future__ import annotations

import math
import time
from collections.abc import Hashable, Sequence
from typing import Protocol

from g6.cogarch.decision_making.utility import validate_distribution

Transition = tuple[float, Hashable, float]  # (probability, next_state, reward)


class Unfinished(RuntimeError):  # noqa: N818 - the answer is "not finished", named by its cap
    """Value iteration stopped at a cap before converging."""

    def __init__(self, cap: str, limit: float, message: str) -> None:
        super().__init__(message)
        self.cap, self.limit = cap, limit


class NonFinite(ValueError):  # noqa: N818 - names the numeric refusal
    """Finite inputs exceeded the numeric range while solving the MDP."""


class MDP(Protocol):
    """A finite MDP: enumerable states, per-state actions, explicit transition distributions."""

    def states(self) -> Sequence[Hashable]:
        ...

    def actions(self, state: Hashable) -> Sequence[Hashable]:
        ...

    def transitions(self, state: Hashable, action: Hashable) -> Sequence[Transition]:
        ...

    def is_terminal(self, state: Hashable) -> bool:
        ...


def value_iteration(mdp: MDP, *, discount: float, tolerance: float = 1e-9, max_iterations: int = 100_000,
                    deadline: float | None = None) -> tuple[dict[Hashable, float], dict[Hashable, Hashable]]:
    """Solve a finite MDP; returns ``(values, greedy_policy)``. Terminal states have value 0 and no policy entry.

    Raises ``ValueError`` on malformed input, and :class:`Unfinished` (naming ``max_iterations`` or ``time_limit_s``)
    when the sweep has not contracted below ``tolerance`` in time: an unconverged value function is never returned.
    ``deadline`` is a ``time.monotonic()`` value."""
    if not 0.0 <= discount < 1.0:
        raise ValueError(f"discount must be in [0, 1), got {discount!r}")
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError(f"tolerance must be finite and > 0, got {tolerance!r}")
    states = list(mdp.states())
    if not states:
        raise ValueError("MDP has no states")
    known = set(states)
    values: dict[Hashable, float] = {s: 0.0 for s in states}
    for _ in range(max_iterations):
        if deadline is not None and time.monotonic() > deadline:
            raise Unfinished("time_limit_s", 0, "value iteration reached its time limit before converging below "
                                                 f"{tolerance}: refusing to return an unconverged answer")
        delta = 0.0
        for state in states:
            if mdp.is_terminal(state):
                continue
            best = None
            for action in mdp.actions(state):
                q = _q_value(mdp, state, action, values, discount, known)
                if best is None or q > best:
                    best = q
            if best is None:
                raise ValueError(f"non-terminal state {state!r} has no actions")
            residual = abs(best - values[state])
            if not math.isfinite(residual):
                raise NonFinite("value iteration produced a non-finite residual")
            delta = max(delta, residual)
            values[state] = best
        if not math.isfinite(delta) or not all(math.isfinite(v) for v in values.values()):
            raise NonFinite("value iteration produced non-finite output")
        if delta < tolerance:
            break
    else:
        raise Unfinished("max_iterations", max_iterations, f"value iteration did not converge below {tolerance} "
                                                           f"within max_iterations {max_iterations}: refusing to "
                                                           "return an unconverged answer")
    policy: dict[Hashable, Hashable] = {}
    for state in states:
        if mdp.is_terminal(state):
            continue
        best_action, best_q = None, None
        for action in mdp.actions(state):  # declared order: the first wins a tie
            q = _q_value(mdp, state, action, values, discount, known)
            if best_q is None or q > best_q:
                best_q, best_action = q, action
        policy[state] = best_action
    return values, policy


def _q_value(mdp: MDP, state: Hashable, action: Hashable, values: dict, discount: float, known: set) -> float:
    transitions = list(mdp.transitions(state, action))
    validate_distribution(p for p, _, _ in transitions)
    for _, next_state, reward in transitions:
        if next_state not in known:
            raise ValueError(f"{state!r}, {action!r}: the next state {next_state!r} is not one of the states")
        if not math.isfinite(float(reward)):
            raise ValueError(f"{state!r}, {action!r}: a reward is not finite")
    q = sum(p * (reward + discount * values[next_state]) for p, next_state, reward in transitions)
    if not math.isfinite(q):
        raise NonFinite("value iteration produced a non-finite Q-value")
    return q


__all__ = ["MDP", "Transition", "Unfinished", "value_iteration"]
