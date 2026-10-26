# SPDX-License-Identifier: Apache-2.0
"""Seeded UCT planning over a generative model (from Python only: the model is a callable, never sent over MCP).

Standard UCT (upper-confidence trees): selection by the UCB score down the built tree, one node expanded per
playout, a uniform-random rollout to the horizon, a discounted backup; the recommended action is the root child with
the most visits (ties by declared action order). Every random draw comes from ``random.Random(seed)`` made at
``plan`` time, so the same seed, state and model plan the same action. Bounded: ``playouts`` at most
:data:`MAX_PLAYOUTS`, ``horizon`` at most :data:`MAX_HORIZON`, and an optional ``time_limit_s``.
"""
from __future__ import annotations

import math
import random
import time
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from g6.cogarch.decision_making.mdp import Unfinished

MAX_PLAYOUTS, MAX_HORIZON = 100_000, 1_000


class GenerativeModel(Protocol):
    """A model that samples one step; no distributions needed."""

    def actions(self, state: Hashable) -> Sequence[Hashable]:
        ...

    def step(self, state: Hashable, action: Hashable, rng: random.Random) -> tuple[Hashable, float]:
        ...

    def is_terminal(self, state: Hashable) -> bool:
        ...


@dataclass
class _Node:
    state: Hashable
    visits: int = 0
    value_sum: float = 0.0
    children: dict[Hashable, _Node] = field(default_factory=dict)


@dataclass
class UCT:
    model: GenerativeModel
    seed: int = 0
    playouts: int = 200
    horizon: int = 20
    discount: float = 1.0
    exploration: float = 1.4142135623730951  # sqrt(2)
    time_limit_s: float | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.playouts <= MAX_PLAYOUTS:
            raise ValueError(f"playouts must be 1 to MAX_PLAYOUTS {MAX_PLAYOUTS}, got {self.playouts!r}")
        if not 1 <= self.horizon <= MAX_HORIZON:
            raise ValueError(f"horizon must be 1 to MAX_HORIZON {MAX_HORIZON}, got {self.horizon!r}")
        if not 0.0 <= self.discount <= 1.0:
            raise ValueError(f"discount must be in [0, 1], got {self.discount!r}")
        if self.time_limit_s is not None and not self.time_limit_s > 0:
            raise ValueError(f"time_limit_s must be > 0, got {self.time_limit_s!r}")

    def plan(self, state: Hashable) -> Hashable:
        """An action for ``state``; ``None`` only when the state is terminal or offers no actions. Raises
        :class:`~g6.cogarch.decision_making.mdp.Unfinished` naming ``time_limit_s`` when the limit cuts the
        playouts short."""
        if self.model.is_terminal(state):
            return None
        actions = list(self.model.actions(state))
        if not actions:
            return None
        rng = random.Random(self.seed)
        root = _Node(state)
        deadline = None if self.time_limit_s is None else time.monotonic() + self.time_limit_s
        for done in range(self.playouts):
            if deadline is not None and time.monotonic() > deadline:
                raise Unfinished("time_limit_s", self.time_limit_s or 0,
                                 f"UCT reached time_limit_s {self.time_limit_s:g} after {done} of {self.playouts} "
                                 "playouts")
            self._sample(root, rng, depth=0)
        best_action, best_visits = None, -1
        for action in actions:  # declared order: the first wins a tie
            child = root.children.get(action)
            visits = child.visits if child is not None else 0
            if visits > best_visits:
                best_visits, best_action = visits, action
        return best_action

    def _sample(self, node: _Node, rng: random.Random, depth: int) -> float:
        if depth >= self.horizon or self.model.is_terminal(node.state):
            node.visits += 1
            return 0.0
        actions = list(self.model.actions(node.state))
        if not actions:
            node.visits += 1
            return 0.0
        untried = [a for a in actions if a not in node.children]
        if untried:
            action = untried[0] if node.visits == 0 else rng.choice(untried)
            next_state, reward = self.model.step(node.state, action, rng)
            child = _Node(next_state)
            node.children[action] = child
            value = reward + self.discount * self._rollout(next_state, rng, depth + 1)
            child.visits += 1
        else:
            action = self._ucb_action(node, actions)
            next_state, reward = self.model.step(node.state, action, rng)
            child = node.children[action]
            child.state = next_state   # the model may sample another state; statistics stay per action
            value = reward + self.discount * self._sample(child, rng, depth + 1)
        # Edges carry Q(s, a), the immediate reward plus the discounted future, credited by the parent only; a
        # node's own frame adds visits, never value, so a recursive visit is not counted twice.
        child.value_sum += value
        node.visits += 1
        return value

    def _ucb_action(self, node: _Node, actions: Sequence[Hashable]) -> Hashable:
        log_visits = math.log(max(1, node.visits))
        best_action, best_score = actions[0], -math.inf
        for action in actions:
            child = node.children[action]
            mean = child.value_sum / max(1, child.visits)
            score = mean + self.exploration * math.sqrt(log_visits / max(1, child.visits))
            if score > best_score:
                best_score, best_action = score, action
        return best_action

    def _rollout(self, state: Hashable, rng: random.Random, depth: int) -> float:
        total, weight, current = 0.0, 1.0, state
        for _ in range(depth, self.horizon):
            if self.model.is_terminal(current):
                break
            actions = list(self.model.actions(current))
            if not actions:
                break
            current, reward = self.model.step(current, rng.choice(actions), rng)
            total += weight * reward
            weight *= self.discount
        return total


__all__ = ["MAX_HORIZON", "MAX_PLAYOUTS", "UCT", "GenerativeModel"]
