# SPDX-License-Identifier: Apache-2.0
"""DecisionMakingBlock: the registry and MCP door (``g6_invoke``) to the decision library.

Every op calls one library function with the caller's inputs and returns its answer with the working; nothing here
re-implements a formula. The probabilities, costs and rewards the caller gives decide every answer; the block does not
estimate them. Same seed and inputs, same answer. The best any op says is ``qualified-draft``.

From Python only, and why: ``UCT`` (it needs a generative model, a callable, and over MCP that would mean running
the caller's code), ``record_decision`` (it appends to a ledger object the caller holds), and ``BoundedAllocator``
and ``BudgetedBandit`` as live objects (the door is stateless; the allocator's rules run inside
``plan_next_action`` and ``retry_budget`` on every call).
"""
from __future__ import annotations

import math
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.cogarch import _reply
from g6.cogarch.decision_making.bandits import UCB1, ThompsonBernoulli
from g6.cogarch.decision_making.budget import BoundedAllocator
from g6.cogarch.decision_making.mdp import NonFinite, Unfinished, value_iteration
from g6.cogarch.decision_making.policies import ExperimentCandidate, PlanNextActionPolicy
from g6.cogarch.decision_making.retry import MAX_RETRIES, RetryBudgetPolicy, RetryTask
from g6.cogarch.decision_making.stopping import VoIThresholdStop
from g6.cogarch.decision_making.utility import LinearUtility, expected_utility
from g6.cogarch.decision_making.voi import (
    expected_improvement,
    expected_information_gain,
    expected_value_of_perfect_information,
)
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

COMPONENT = "decision_making"
DECIDED, INPUT, CAP = "G6_E_DECISION_COMPUTED", "G6_E_DECISION_INPUT", "G6_E_DECISION_CAP"
NUMERIC = "G6_E_DECISION_NUMERIC"
SENSITIVE_OPS: tuple[str, ...] = ()
MAX_ITERATIONS = 10_000

_OPS: dict[str, tuple[tuple[str, ...], str]] = {
    "expected_value_of_perfect_information": (
        ("prior", "utilities"),
        "EVPI of knowing the state before acting; prior: state -> p, utilities: action -> state -> u."),
    "expected_information_gain": (
        ("prior", "true_positive_rate", "false_positive_rate"),
        "Bits a binary test can change a binary belief by (a test that cannot refute scores 0)."),
    "expected_improvement": (
        ("mean", "std", "best", "xi"),
        "Gaussian expected improvement of a candidate N(mean, std^2) over the incumbent best."),
    "expected_utility": (
        ("outcomes", "scale", "offset"),
        "E[u] over [[outcome, probability], ...] with the linear utility scale * x + offset."),
    "value_iteration": (
        ("states", "transitions", "terminal", "discount", "tolerance", "max_iterations", "time_limit_s"),
        "Exact values and greedy policy of a finite MDP; transitions: state -> action -> [[p, next, r]]; refused, "
        "never approximated, when it has not converged by max_iterations or time_limit_s."),
    "plan_next_action": (
        ("candidates", "budget", "min_net_gain", "cost_weight"),
        "The affordable experiment with the most expected information gain (picked by gain alone; cost enters "
        "through the budget and the stopping rule), or STOP."),
    "bandit_select": (
        ("algorithm", "arms", "history", "exploration", "seed", "prior_strength", "structural_scores", "mask",
         "iteration"),
        "Next arm from UCB1 or seeded Thompson sampling after replaying [[arm, reward], ...]."),
    "retry_budget": (
        ("tasks", "budget", "min_net_gain", "max_retries"),
        "How many retries each task gets: retries granted one at a time to the task whose next retry is worth most "
        "(its Beta-Bernoulli chance of success after the failures so far, times its value, less its cost), paid "
        "from the budget, until the best next retry is worth less than min_net_gain."),
}
_ACTION = ("Use the answer with its working; it is computed from the probabilities, costs and rewards you gave, "
           "which the block does not estimate or check against the world.")
_ACTIONS = {DECIDED: _ACTION, INPUT: "Correct the inputs as the reason says.",
            CAP: "Raise the cap named, within its ceiling, or give a smaller problem.",
            NUMERIC: "Rescale rewards or reduce the discount; the computation exceeded the finite numeric range."}


class Candidate(BaseModel):
    """An experiment for ``plan_next_action``: a binary test of a binary hypothesis."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=200)
    prior: float
    true_positive_rate: float
    false_positive_rate: float
    cost: float = 0.0


class Task(BaseModel):
    """A task for ``retry_budget``: what a success is worth, what an attempt costs, its record so far."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=200)
    value: float
    cost: float
    successes: int = Field(0, ge=0)
    failures: int = Field(0, ge=0)
    prior_alpha: float = 1.0
    prior_beta: float = 1.0


DecisionMakingOp = Literal["ops", "help", "expected_value_of_perfect_information", "expected_information_gain",
                           "expected_improvement", "expected_utility", "value_iteration", "plan_next_action",
                           "bandit_select", "retry_budget"]


class DecisionMakingInput(BaseModel):
    """One op's parameters; fields no op has are refused (DECISION_MAKING-03)."""

    model_config = ConfigDict(extra="forbid")

    op: DecisionMakingOp = "ops"
    prior: float | dict[str, float] | None = None
    utilities: dict[str, dict[str, float]] = Field(default_factory=dict, max_length=200)
    true_positive_rate: float = 0.0
    false_positive_rate: float = 0.0
    mean: float = 0.0
    std: float = 0.0
    best: float = 0.0
    xi: float = 0.0
    outcomes: list[tuple[float, float]] = Field(default_factory=list, max_length=10_000)
    scale: float = 1.0
    offset: float = 0.0
    states: list[str] = Field(default_factory=list, max_length=1_000)
    transitions: dict[str, dict[str, list[tuple[float, str, float]]]] = Field(default_factory=dict, max_length=1_000)
    terminal: list[str] = Field(default_factory=list, max_length=1_000)
    discount: float = 0.9
    tolerance: float = 1e-9
    max_iterations: int = Field(default=MAX_ITERATIONS, ge=1, le=MAX_ITERATIONS)
    time_limit_s: float = Field(10.0, gt=0, le=60)
    candidates: list[Candidate] = Field(default_factory=list, max_length=1_000)
    budget: float = 0.0
    min_net_gain: float = 0.0
    cost_weight: float = 0.0
    tasks: list[Task] = Field(default_factory=list, max_length=1_000)
    max_retries: int = Field(10, ge=0, le=MAX_RETRIES)
    algorithm: Literal["ucb1", "thompson"] = "ucb1"
    arms: list[str] = Field(default_factory=list, max_length=1_000)
    history: list[tuple[str, float]] = Field(default_factory=list, max_length=100_000)
    exploration: float = 2.0
    seed: int = 0
    prior_strength: float = 0.0
    structural_scores: dict[str, float] | None = None
    mask: list[str] = Field(default_factory=list, max_length=1_000)
    iteration: int = Field(0, ge=0)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("transitions")
    @classmethod
    def _bounded_table(cls, value: dict) -> dict:
        size = sum(len(rows) for actions in value.values() for rows in actions.values())
        if size > 100_000:
            raise ValueError(f"transitions holds {size} rows; at most 100,000")
        return value


class _TabularMDP:
    """The library's MDP protocol over plain tables (actions in declared order)."""

    def __init__(self, states: list[str], transitions: dict, terminal: list[str]) -> None:
        self._states, self._transitions, self._terminal = list(states), transitions, set(terminal)

    def states(self) -> list[str]:
        return self._states

    def actions(self, state: str) -> list[str]:
        return list(self._transitions.get(state, {}))

    def transitions(self, state: str, action: str) -> list[tuple[float, str, float]]:
        return list(self._transitions[state][action])

    def is_terminal(self, state: str) -> bool:
        return state in self._terminal


def _distribution(prior: Any) -> dict[str, float]:
    if not isinstance(prior, dict):
        raise TypeError("prior must map each state to its probability")
    return prior


def _probability(prior: Any) -> float:
    if isinstance(prior, bool) or not isinstance(prior, (int, float)):
        raise TypeError("prior must be the probability that the hypothesis is true")
    return float(prior)


def _evpi(d: DecisionMakingInput) -> float:
    return expected_value_of_perfect_information(_distribution(d.prior), d.utilities)


def _eig(d: DecisionMakingInput) -> float:
    return expected_information_gain(_probability(d.prior), d.true_positive_rate, d.false_positive_rate)


def _ei(d: DecisionMakingInput) -> float:
    return expected_improvement(d.mean, d.std, d.best, xi=d.xi)


def _eu(d: DecisionMakingInput) -> float:
    if not all(math.isfinite(x) for x, _ in d.outcomes):
        raise ValueError("every outcome must be finite")
    return expected_utility(list(d.outcomes), LinearUtility(d.scale, d.offset))


def _value_iteration(d: DecisionMakingInput) -> dict[str, Any]:
    values, policy = value_iteration(_TabularMDP(d.states, d.transitions, d.terminal), discount=d.discount,
                                     tolerance=d.tolerance, max_iterations=d.max_iterations,
                                     deadline=time.monotonic() + d.time_limit_s)
    return {"values": values, "policy": policy}


def _plan_next_action(d: DecisionMakingInput) -> dict[str, Any]:
    policy = PlanNextActionPolicy(BoundedAllocator(d.budget), VoIThresholdStop(d.min_net_gain, d.cost_weight))
    return policy.decide([ExperimentCandidate(**c.model_dump()) for c in d.candidates])


def _retry_budget(d: DecisionMakingInput) -> dict[str, Any]:
    policy = RetryBudgetPolicy(BoundedAllocator(d.budget), VoIThresholdStop(d.min_net_gain, 1.0), d.max_retries)
    return policy.decide([RetryTask(**t.model_dump()) for t in d.tasks])


def _bandit_select(d: DecisionMakingInput) -> dict[str, Any]:
    if d.algorithm == "ucb1":
        ucb = UCB1(tuple(d.arms), exploration=d.exploration)
        for arm, reward in d.history:
            ucb.update(arm, reward)
        return {"algorithm": "ucb1", "arm": ucb.select(mask=d.mask)}
    thompson = ThompsonBernoulli(tuple(d.arms), seed=d.seed, structural_scores=d.structural_scores,
                                 prior_strength=d.prior_strength)
    for arm, reward in d.history:
        if not math.isfinite(reward):
            raise ValueError("every Thompson reward must be finite")
        thompson.update(arm, bool(reward))
    return {"algorithm": "thompson", "arm": thompson.select(mask=d.mask, iteration=d.iteration), "seed": d.seed}


_HANDLERS = {"expected_value_of_perfect_information": _evpi, "expected_information_gain": _eig,
             "expected_improvement": _ei, "expected_utility": _eu, "value_iteration": _value_iteration,
             "plan_next_action": _plan_next_action, "bandit_select": _bandit_select, "retry_budget": _retry_budget}


def _ids(d: DecisionMakingInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _catalogue(component: str, op: str) -> dict[str, Any]:
    if op == "ops":
        return {"component": component, "op": op, "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS),
                "params": {name: list(names) for name, (names, _line) in _OPS.items()}}
    return {"component": component, "op": op,
            "help": [f"{name}({', '.join(names)}): {line}" for name, (names, line) in _OPS.items()]}


@block_contract(
    purpose="give an agent standard, seeded decision rules (value of information, bandits, value iteration, the next "
            "experiment, retries within a budget) whose every answer shows its working and repeats on every run",
    mitigates_failure_modes=("ad_hoc_choice",),
    verification_method="standard formulas over validated inputs (probabilities must sum to 1, no NaN, no negative "
                        "cost); every random draw from a caller's seed; budgets that only tighten; an unconverged "
                        "value iteration refused, never returned",
)
@component_maturity(
    "beta",
    reason="a standard-library decision library behind a stateless door; deterministic and validated; the "
           "probabilities it is given decide every answer and are not estimated",
)
class DecisionMakingBlock(AIBlock[DecisionMakingInput, dict, dict]):
    """Deterministic decision-theoretic computations over the caller's inputs. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: DecisionMakingInput) -> Result[dict]:
        if data.op in ("ops", "help"):
            return Result.ok(_catalogue(self.name, data.op))
        try:
            result = _HANDLERS[data.op](data)
        except NonFinite as exc:
            return Result.ok(_reply.refuse(self.name, data.op, NUMERIC, str(exc), action=_ACTIONS[NUMERIC],
                                           reason=str(exc), **_ids(data)))
        except Unfinished as stop:
            limit = data.max_iterations if stop.cap == "max_iterations" else data.time_limit_s
            return Result.ok(_reply.refuse(self.name, data.op, CAP, str(stop), action=_ACTIONS[CAP], cap=stop.cap,
                                           limit=limit, reason=str(stop), **_ids(data)))
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            why = f"{data.op}: {type(exc).__name__}: {exc}"[:400]
            return Result.ok(_reply.refuse(self.name, data.op, INPUT, why, action=_ACTIONS[INPUT], reason=why,
                                           **_ids(data)))
        said = (f"{data.op} computed from the inputs given; the block does not estimate the probabilities, costs or "
                "rewards it was given.")
        return Result.ok(_reply.answer(self.name, data.op, DECIDED, said, action=_ACTIONS[DECIDED], result=result,
                                       evidence=[{"kind": "computation", "op": data.op, "deterministic": True,
                                                  "inputs_are": "the caller's, not estimated"}], **_ids(data)))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "Candidate", "DecisionMakingBlock", "DecisionMakingInput", "Task"]
