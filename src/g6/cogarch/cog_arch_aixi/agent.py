# SPDX-License-Identifier: Apache-2.0
"""A bounded MC-AIXI-CTW agent: an action-conditional context tree as the environment model, and rho-UCT planning over
a clone of it. AIXI itself is incomputable; this is the Monte Carlo approximation of Veness, Ng, Hutter, Uther and
Silver ("A Monte-Carlo AIXI Approximation", 2011), small and bounded.

Each interaction cycle is an action, then a percept: an observation in ``observation_bits`` bits and a reward in
``reward_bits`` bits over ``[reward_min, reward_min + 2**reward_bits - 1]`` (AIXI-03: multi-bit rewards). The model
conditions on the action bits and predicts the percept bits, so it learns the rewards each action brings (AIXI-02,
AIXI-04). :func:`plan` clones the live model and runs rho-UCT on the clone: at a decision node an untried action, else
the upper confidence bound on the normalised value; at a chance node a percept sampled from the model; past the
explored tree a random rollout; every sampled update reverted. Every random choice comes from a generator seeded per
agent and step.

An :class:`Environment` is registered by a person from Python (:func:`register_environment`); over MCP an episode can
only name one, never send one.
"""
from __future__ import annotations

import copy
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from g6.cogarch.cog_arch_aixi._environments import CoinFlip, SimpleMaze1D, TigerDoor
from g6.cogarch.cog_arch_aixi.ctw import CTWTree

#: The wall clock the time limits read (a test may replace it).
_wall: Callable[[], float] = time.monotonic


def to_bits(value: int, width: int) -> list[int]:
    """``value`` in ``width`` bits, most significant first."""
    return [(value >> i) & 1 for i in range(width - 1, -1, -1)]


def from_bits(bits: list[int]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


@dataclass(frozen=True)
class Spec:
    """The shape of the interaction: actions, and how a percept is written in bits."""

    num_actions: int
    observation_bits: int
    reward_bits: int
    reward_min: int = 0

    @property
    def action_bits(self) -> int:
        return max(1, (self.num_actions - 1).bit_length())

    @property
    def cycle_bits(self) -> int:
        return self.action_bits + self.observation_bits + self.reward_bits

    @property
    def reward_max(self) -> int:
        return self.reward_min + 2 ** self.reward_bits - 1

    def percept_bits(self, observation: int, reward: int) -> list[int]:
        if not 0 <= observation < 2 ** self.observation_bits:
            raise ValueError(f"observation {observation} is outside 0 to {2 ** self.observation_bits - 1}")
        if not self.reward_min <= reward <= self.reward_max:
            raise ValueError(f"reward {reward} is outside {self.reward_min} to {self.reward_max}")
        return to_bits(observation, self.observation_bits) + to_bits(reward - self.reward_min, self.reward_bits)

    def decode(self, bits: list[int]) -> tuple[int, int]:
        return (from_bits(bits[:self.observation_bits]),
                from_bits(bits[self.observation_bits:]) + self.reward_min)


@runtime_checkable
class Environment(Protocol):
    """What a person registers: the interaction's shape and a step. Observations and rewards are integers."""

    num_actions: int
    observation_bits: int
    reward_bits: int
    reward_min: int

    def step(self, action: int) -> tuple[int, int]: ...


_ENVIRONMENTS: dict[str, Callable[[int], Environment]] = {}
_BUILTINS = {"coin-flip": CoinFlip, "tiger-door": TigerDoor, "maze": SimpleMaze1D}


def is_builtin(factory: Callable[[int], Environment]) -> bool:
    """Only these exact closed-world factories are exempt; a registered name is never authority."""
    return any(factory is built_in for built_in in (CoinFlip, TigerDoor, SimpleMaze1D))


def register_environment(name: str, factory: Callable[[int], Environment]) -> None:
    """Register ``factory(seed) -> Environment`` under ``name``, from Python only."""
    if not callable(factory):
        raise TypeError("an environment is registered as a factory: factory(seed) -> Environment")
    _ENVIRONMENTS[name] = factory


def unregister_environment(name: str) -> None:
    _ENVIRONMENTS.pop(name, None)


def environments() -> list[str]:
    return sorted(_BUILTINS.keys() | _ENVIRONMENTS.keys())


def environment(name: str) -> Callable[[int], Environment] | None:
    return _ENVIRONMENTS.get(name, _BUILTINS.get(name))


class Stopped(Exception):  # noqa: N818 - a cap reached is an answer, not an error
    def __init__(self, cap: str, limit: float, phase: str = "planning") -> None:
        super().__init__(f"{cap} {limit:g} reached")
        self.cap, self.limit, self.phase = cap, limit, phase


@dataclass
class _Node:
    visits: int = 0
    value: float = 0.0          # mean total reward (raw units) from here to the horizon
    children: dict[int, _Node] = field(default_factory=dict)


@dataclass
class Planner:
    spec: Spec
    model: CTWTree
    horizon: int
    rng: random.Random
    exploration: float
    deadline: float
    time_limit: float

    def _percept(self) -> tuple[list[int], int]:
        width = self.spec.observation_bits + self.spec.reward_bits
        bits = []
        for _ in range(width):
            bit = 1 if self.rng.random() < self.model.predict(1) else 0
            self.model.update([bit])
            bits.append(bit)
        return bits, self.spec.decode(bits)[1]

    def _rollout(self, steps: int) -> float:
        start, total, counted = len(self.model.history), 0.0, 0
        for _ in range(steps):
            self.model.extend(to_bits(self.rng.randrange(self.spec.num_actions), self.spec.action_bits))
            bits, reward = self._percept()
            counted += len(bits)
            total += reward
        self.model.revert(counted)
        self.model.retract(len(self.model.history) - start)
        return total

    def _normalised(self, value: float, steps: int) -> float:
        span = (self.spec.reward_max - self.spec.reward_min) * steps
        return (value - self.spec.reward_min * steps) / span if span else 0.0

    def _select(self, node: _Node, steps: int) -> int:
        untried = [a for a in range(self.spec.num_actions) if a not in node.children]
        if untried:
            return self.rng.choice(untried)
        log_n = math.log(node.visits)
        return max(range(self.spec.num_actions), key=lambda a: (
            self._normalised(node.children[a].value, steps)
            + self.exploration * math.sqrt(log_n / node.children[a].visits)))

    def _decision(self, node: _Node, steps: int) -> float:
        if steps == 0:
            return 0.0
        if node.visits == 0:
            total = self._rollout(steps)
        else:
            action = self._select(node, steps)
            bits = to_bits(action, self.spec.action_bits)
            self.model.extend(bits)
            total = self._chance(node.children.setdefault(action, _Node()), steps)
            self.model.retract(len(bits))
        node.value += (total - node.value) / (node.visits + 1)
        node.visits += 1
        return total

    def _chance(self, node: _Node, steps: int) -> float:
        bits, reward = self._percept()
        child = node.children.setdefault(from_bits(bits), _Node())
        total = reward + self._decision(child, steps - 1)
        self.model.revert(len(bits))
        node.value += (total - node.value) / (node.visits + 1)
        node.visits += 1
        return total

    def search(self, playouts: int) -> tuple[int, _Node, int]:
        root = _Node()
        done = 0
        for _ in range(playouts):
            if _wall() > self.deadline:
                if done == 0:
                    raise Stopped("time_limit_s", self.time_limit)
                break
            self._decision(root, self.horizon)
            done += 1
        best = max(root.children, key=lambda a: (root.children[a].value, -a)) if root.children \
            else self.rng.randrange(self.spec.num_actions)
        return best, root, done


@dataclass
class Agent:
    """An agent's lasting state: the interaction's shape, its model, its parameters and its record."""

    spec: Spec
    model: CTWTree
    seed: int
    horizon: int = 3
    playouts: int = 100
    exploration: float = math.sqrt(2)
    steps: int = 0
    pending_action: int | None = None
    total_reward: int = 0

    @classmethod
    def new(cls, spec: Spec, *, depth: int, seed: int, horizon: int, playouts: int,
            exploration: float = math.sqrt(2)) -> Agent:
        return cls(spec, CTWTree(depth=depth), seed, horizon, playouts, exploration)

    def act(self, time_limit_s: float) -> dict[str, Any]:
        """Plan on a clone of the model, append the chosen action to the live history, and say why it was chosen."""
        if self.pending_action is not None:
            raise ValueError("the last action's percept has not been observed yet")
        rng = random.Random(f"{self.seed}:act:{self.steps}")
        planner = Planner(self.spec, copy.deepcopy(self.model), self.horizon, rng, self.exploration,
                          _wall() + time_limit_s, time_limit_s)
        action, root, done = planner.search(self.playouts)
        self.model.extend(to_bits(action, self.spec.action_bits))
        self.pending_action = action
        return {"action": action, "playouts": done, "horizon": self.horizon,
                "values": {str(a): {"mean_reward_to_horizon": n.value, "visits": n.visits}
                           for a, n in sorted(root.children.items())}}

    def observe(self, observation: int, reward: int) -> None:
        """Learn the percept that followed the pending action (counted in the live model, never reverted)."""
        if self.pending_action is None:
            raise ValueError("there is no action to observe the percept of: act first")
        self.model.update(self.spec.percept_bits(observation, reward))
        self.model.forget_undo()
        self.pending_action = None
        self.steps += 1
        self.total_reward += reward

    def to_dict(self) -> dict[str, Any]:
        return {"spec": self.spec.__dict__, "model": self.model.to_dict(), "seed": self.seed, "horizon": self.horizon,
                "playouts": self.playouts, "exploration": self.exploration, "steps": self.steps,
                "pending_action": self.pending_action, "total_reward": self.total_reward}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Agent:
        return cls(Spec(**d["spec"]), CTWTree.from_dict(d["model"]), int(d["seed"]), int(d["horizon"]),
                   int(d["playouts"]), float(d["exploration"]), int(d["steps"]), d["pending_action"],
                   int(d["total_reward"]))


def spec_of(env: Environment) -> Spec:
    """The interaction's shape an environment declares, checked."""
    spec = Spec(int(env.num_actions), int(env.observation_bits), int(env.reward_bits), int(env.reward_min))
    if spec.num_actions < 2 or spec.observation_bits < 0 or spec.reward_bits < 1:
        raise ValueError("an environment has at least 2 actions, observation_bits >= 0 and reward_bits >= 1")
    return spec


def _step(env: Environment, spec: Spec, action: int) -> tuple[int, int]:
    observation, reward = env.step(action)
    if not (isinstance(observation, int) and isinstance(reward, int)) or isinstance(observation, bool) \
            or isinstance(reward, bool):
        raise ValueError("an environment's step returns (observation, reward) as integers")
    spec.percept_bits(observation, reward)    # raises when either is outside the declared range
    return observation, reward


def _check_deadline(deadline: float, time_limit: float, phase: str) -> None:
    if _wall() >= deadline:
        raise Stopped("time_limit_s", time_limit, phase)


def episode(factory: Callable[[int], Environment], seed: int, cycles: int, *, depth: int, horizon: int,
            playouts: int, exploration: float, deadline: float, time_limit: float) -> dict[str, Any]:
    """One seed: a fresh agent on ``factory(seed)`` and a random policy on another ``factory(seed)``, ``cycles``
    each. Raises :class:`Stopped` at the deadline."""
    _check_deadline(deadline, time_limit, "agent")
    env = factory(seed)
    _check_deadline(deadline, time_limit, "agent")
    spec = spec_of(env)
    agent = Agent.new(spec, depth=depth, seed=seed, horizon=horizon, playouts=playouts, exploration=exploration)
    rewards = []
    for _ in range(cycles):
        left = deadline - _wall()
        if left <= 0:
            raise Stopped("time_limit_s", time_limit)
        action = agent.act(left)["action"]
        _check_deadline(deadline, time_limit, "agent")
        observation, reward = _step(env, spec, action)
        _check_deadline(deadline, time_limit, "agent")
        agent.observe(observation, reward)
        rewards.append(reward)
    _check_deadline(deadline, time_limit, "baseline")
    baseline_env, policy = factory(seed), random.Random(f"{seed}:random-policy")
    _check_deadline(deadline, time_limit, "baseline")
    if spec_of(baseline_env) != spec:
        raise ValueError("the environment's factory gave two different shapes for one seed")
    baseline = []
    for _ in range(cycles):
        _check_deadline(deadline, time_limit, "baseline")
        baseline.append(_step(baseline_env, spec, policy.randrange(spec.num_actions))[1])
        _check_deadline(deadline, time_limit, "baseline")
    return {"seed": seed, "agent_reward": sum(rewards), "random_reward": sum(baseline),
            "agent_mean": sum(rewards) / cycles, "random_mean": sum(baseline) / cycles,
            "regret_vs_random": sum(baseline) - sum(rewards), "second_half_agent_mean":
            sum(rewards[cycles // 2:]) / max(1, cycles - cycles // 2), "model_nodes": agent.model.node_count(),
            "cycle_bits": spec.cycle_bits}


__all__ = ["Agent", "Environment", "Planner", "Spec", "Stopped", "environment", "environments", "episode",
           "from_bits", "register_environment", "spec_of", "to_bits", "unregister_environment"]
