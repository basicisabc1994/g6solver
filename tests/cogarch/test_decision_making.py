# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.decision_making: standard, seeded decision rules over validated inputs, each answer with its working and
the same on every run; retries shared out within a budget by the value of one more attempt.

Known values are worked by hand in each test from the standard formulas: EVPI, binary mutual information, Gaussian
expected improvement, value iteration, UCB1, and the Beta-Bernoulli chance of success after failures."""
from __future__ import annotations

import json
import math
import random

import pytest
from pydantic import ValidationError

import g6.cogarch.decision_making as package
from g6.cogarch.decision_making import (
    UCT,
    BoundedAllocator,
    BudgetedBandit,
    DecisionMakingBlock,
    DecisionMakingInput,
    RetryBudgetPolicy,
    RetryTask,
    ThompsonBernoulli,
    Unfinished,
    VoIThresholdStop,
    expected_improvement,
    expected_information_gain,
    expected_value_of_perfect_information,
    record_decision,
)
from g6.core.failure_modes import FailureModeRegistry
from g6.mcp import describe_reply, invoke_reply


def run(**params) -> dict:
    return DecisionMakingBlock().infer(DecisionMakingInput(**params)).unwrap()


def mcp(op: str, **params) -> dict:
    reply = invoke_reply("decision_making", op, json.dumps(params))
    assert reply["ok"], reply
    return reply["result"]


# --- D1: a choice of what to try next repeats exactly, and shows why ---------------------------------------------------

def test_d1_a_seeded_choice_repeats_across_separate_calls_and_seeds_differ() -> None:
    # a case the seeding was not written around: a structural prior over five near-equal arms, after some history
    arms = ["a", "b", "c", "d", "e"]
    params = {"algorithm": "thompson", "arms": arms, "history": [["a", 1], ["b", 0], ["c", 1], ["d", 0]],
              "prior_strength": 2, "structural_scores": {"a": 0.5, "b": 0.6, "c": 0.4, "d": 0.55, "e": 0.5}}
    chosen = {seed: mcp("bandit_select", seed=seed, **params)["result"]["arm"] for seed in range(20)}
    again = {seed: mcp("bandit_select", seed=seed, **params)["result"]["arm"] for seed in range(20)}
    assert chosen == again and len(set(chosen.values())) > 1


def test_d1_the_next_experiment_comes_with_its_working() -> None:
    out = run(op="plan_next_action", budget=5, candidates=[
        {"candidate_id": "weak", "prior": 0.5, "true_positive_rate": 0.6, "false_positive_rate": 0.4, "cost": 1},
        {"candidate_id": "sharp", "prior": 0.5, "true_positive_rate": 1.0, "false_positive_rate": 0.0, "cost": 2},
        {"candidate_id": "dear", "prior": 0.5, "true_positive_rate": 1.0, "false_positive_rate": 0.0, "cost": 9}])
    decision = out["result"]
    assert decision["action"] == "RUN_EXPERIMENT" and decision["chosen"] == "sharp"
    rows = {r["candidate_id"]: r for r in decision["rationale"]["candidates"]}
    assert rows["sharp"]["expected_gain_bits"] == pytest.approx(1.0) and rows["dear"]["affordable"] is False
    assert out["completion_state"] == "qualified-draft"


# --- P2 DECISION_MAKING-01: the retries the page promised ---------------------------------------------------------------

def test_p2_decision_making01_retry_budget_known_values() -> None:
    # one failure so far, Beta(1, 1): retry 1 succeeds with 1/(1+1+1) = 1/3, retry 2 with 1/4, retry 3 with 1/5,
    # retry 4 with 1/6. Value 10, cost 2: net 10/3 - 2 = 1.333, 2.5 - 2 = 0.5, 2 - 2 = 0 (kept: not below 0),
    # 1.667 - 2 = -0.333 (stop). Three retries, 6 spent.
    out = run(op="retry_budget", budget=100, tasks=[{"task_id": "deploy", "value": 10, "cost": 2, "failures": 1}])
    result = out["result"]
    assert result["retries"] == {"deploy": 3} and result["budget_spent"] == 6
    assert [g["success_probability"] for g in result["grants"]] == pytest.approx([1 / 3, 1 / 4, 1 / 5])
    assert [g["net_value"] for g in result["grants"]] == pytest.approx([4 / 3, 0.5, 0.0])
    assert result["stop_reason"] == "best_next_retry_below_min_net_gain"
    tight = run(op="retry_budget", budget=5, tasks=[{"task_id": "deploy", "value": 10, "cost": 2, "failures": 1}])
    assert tight["result"]["retries"] == {"deploy": 2} and tight["result"]["stop_reason"] == \
        "budget_exhausted_for_every_task"


def test_p2_decision_making01_retries_go_to_the_task_whose_next_retry_is_worth_most() -> None:
    # A: value 10, cost 2, one failure: nets 1.333, 0.5, 0.0, -0.333. B: value 4, cost 1, no record: chances 1/2,
    # 1/3, 1/4, 1/5, nets 1.0, 0.333, 0.0, -0.2. Order: A, B, A, B, A (tie at 0.0: declared order), B, then stop.
    tasks = [{"task_id": "A", "value": 10, "cost": 2, "failures": 1}, {"task_id": "B", "value": 4, "cost": 1}]
    result = run(op="retry_budget", budget=100, tasks=tasks)["result"]
    assert [g["task_id"] for g in result["grants"]] == ["A", "B", "A", "B", "A", "B"]
    assert result["retries"] == {"A": 3, "B": 3} and result["budget_spent"] == 9


def test_p2_decision_making01_the_budget_and_max_retries_hold_and_bad_inputs_are_refused() -> None:
    capped = run(op="retry_budget", budget=100, max_retries=1, tasks=[{"task_id": "t", "value": 100, "cost": 1}])
    assert capped["result"]["retries"] == {"t": 1} and capped["result"]["stop_reason"] == "max_retries_for_every_task"
    for bad in ({"task_id": "t", "value": 10, "cost": -1}, {"task_id": "t", "value": float("nan"), "cost": 1},
                {"task_id": "t", "value": 1, "cost": 1, "prior_alpha": 0}):
        out = run(op="retry_budget", budget=10, tasks=[bad])
        assert out["code"] == "G6_E_DECISION_INPUT" and out["completion_state"] == "blocked-escalated"
    policy = RetryBudgetPolicy(BoundedAllocator(7), VoIThresholdStop(0, 1), max_retries=100)
    spent = policy.decide([RetryTask("x", value=1000, cost=3)])["budget_spent"]
    assert spent == 6 and policy.allocator.spent <= 7


def test_p3_decision_making03_fields_no_op_has_are_refused() -> None:
    for name in ("approved", "verified", "confidence", "trusted"):
        with pytest.raises(ValidationError):
            DecisionMakingInput(op="expected_improvement", **{name: True})
    with pytest.raises(ValidationError):
        DecisionMakingInput(op="plan_next_action", candidates=[{"candidate_id": "x", "prior": 0.5,
                                                                "true_positive_rate": 1, "false_positive_rate": 0,
                                                                "approved": True}])


# --- known values -------------------------------------------------------------------------------------------------------

def test_value_of_information_known_values() -> None:
    # EVPI: two states at 1/2, action a pays 4 or 0, action b pays 0 or 4: best without information 2, with 4: EVPI 2
    utilities = {"a": {"s1": 4.0, "s2": 0.0}, "b": {"s1": 0.0, "s2": 4.0}}
    assert expected_value_of_perfect_information({"s1": 0.5, "s2": 0.5}, utilities) == pytest.approx(2.0)
    # a perfect test of a fair belief resolves one bit; a test with tpr == fpr refutes nothing
    assert expected_information_gain(0.5, 1.0, 0.0) == pytest.approx(1.0)
    assert expected_information_gain(0.7, 0.5, 0.5) == pytest.approx(0.0)
    # EI: std 0 gives max(0, mean - best); mean == best, std 1 gives phi(0) = 1/sqrt(2 pi) = 0.398942
    assert expected_improvement(3.0, 0.0, 1.0) == pytest.approx(2.0)
    assert expected_improvement(1.0, 1.0, 1.0) == pytest.approx(1 / math.sqrt(2 * math.pi))
    out = mcp("expected_utility", outcomes=[[10, 0.25], [2, 0.75]], scale=2, offset=1)
    assert out["result"] == pytest.approx(2 * (10 * 0.25 + 2 * 0.75) + 1)     # 2 x 4 + 1 = 9


def test_value_iteration_known_values() -> None:
    # go from s to the goal for reward 1 (discount 0.9): V(s) = 1; or stay for 0: V stays 0. The policy is go.
    out = run(op="value_iteration", states=["s", "goal"], terminal=["goal"], discount=0.9,
              transitions={"s": {"stay": [[1.0, "s", 0.0]], "go": [[1.0, "goal", 1.0]]}})
    assert out["result"]["values"] == pytest.approx({"s": 1.0, "goal": 0.0}) and out["result"]["policy"] == {"s": "go"}


def test_ucb1_known_choice() -> None:
    # after a: 1, b: 0, a: 1 (t = 3): a scores 1 + sqrt(2 ln 3 / 2) = 2.048, b scores 0 + sqrt(2 ln 3 / 1) = 1.482
    out = run(op="bandit_select", algorithm="ucb1", arms=["a", "b"], history=[["a", 1], ["b", 0], ["a", 1]])
    assert out["result"]["arm"] == "a"
    assert run(op="bandit_select", arms=["a", "b"], mask=["a", "b"])["result"]["arm"] is None


# --- fail closed and bounded -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    {"op": "expected_value_of_perfect_information", "prior": {"s1": 0.6, "s2": 0.6}, "utilities": {"a": {"s1": 1,
                                                                                                         "s2": 1}}},
    {"op": "expected_information_gain", "prior": 1.5, "true_positive_rate": 1, "false_positive_rate": 0},
    {"op": "expected_utility", "outcomes": [[1, 0.5], [2, 0.4]]},
    {"op": "value_iteration", "states": ["s"], "transitions": {"s": {"a": [[1.0, "nowhere", 0.0]]}}},
    {"op": "bandit_select", "arms": ["a", "a"]},
    {"op": "plan_next_action", "candidates": []},
])
def test_g4_malformed_inputs_are_refused_with_a_code(params: dict) -> None:
    out = run(**params)
    assert out["code"] == "G6_E_DECISION_INPUT" and out["completion_state"] == "blocked-escalated"


def test_g4_value_iteration_that_has_not_converged_is_refused_naming_its_cap() -> None:
    # a loop with discount 0.99 needs hundreds of sweeps: 3 is not enough
    table = {"s": {"loop": [[1.0, "s", 1.0]]}}
    out = run(op="value_iteration", states=["s"], transitions=table, discount=0.99, max_iterations=3)
    assert out["code"] == "G6_E_DECISION_CAP" and out["cap"] == "max_iterations"
    assert "values" not in json.dumps(out.get("result", {}))


def test_uct_plans_from_python_seeded_and_bounded() -> None:
    class Chain:   # move right to the end for 1; left pays nothing
        def actions(self, state):
            return ["left", "right"]

        def step(self, state, action, rng: random.Random):
            nxt = min(state + 1, 3) if action == "right" else max(state - 1, 0)
            return nxt, 1.0 if nxt == 3 and state != 3 else 0.0

        def is_terminal(self, state):
            return state == 3

    assert UCT(Chain(), seed=1, playouts=200, horizon=5).plan(0) == "right"
    assert UCT(Chain(), seed=1, playouts=50).plan(0) == UCT(Chain(), seed=1, playouts=50).plan(0)
    with pytest.raises(ValueError, match="MAX_PLAYOUTS"):
        UCT(Chain(), playouts=10**9)
    with pytest.raises(Unfinished, match="time_limit_s"):
        UCT(Chain(), playouts=100_000, horizon=1000, time_limit_s=1e-9).plan(0)
    assert "uct" not in [op.lower() for op in mcp("ops")["ops"]]


def test_the_budget_only_tightens_and_a_budgeted_bandit_never_overspends() -> None:
    b = BoundedAllocator(10)
    assert b.allocate(6) and not b.allocate(5) and b.spent == 6
    assert b.tighten(20) == 10 and b.tighten(2) == 6
    bandit = BudgetedBandit(ThompsonBernoulli(("x", "y"), seed=3), {"x": 4, "y": 1}, BoundedAllocator(5))
    spent = []
    while (arm := bandit.select()) is not None and bandit.commit(arm):
        spent.append(arm)
    assert bandit.allocator.spent <= 5 and bandit.select() is None


def test_record_decision_appends_to_the_callers_ledger() -> None:
    class Ledger:
        def __init__(self):
            self.rows = []

        def append(self, kind, **fields):
            self.rows.append((kind, fields))
            return {"kind": kind, **fields}

    ledger = Ledger()
    record_decision(ledger, {"action": "STOP"}, recorded_at="2026-10-21T00:00:00Z", actor_id="planner-1")
    assert ledger.rows[0][0] == "DECISION_RECORDED" and ledger.rows[0][1]["payload"] == {"action": "STOP"}


# --- the gates and D2, D6 ------------------------------------------------------------------------------------------------

def test_g1_no_op_answers_verified() -> None:
    replies = [run(op="expected_improvement", mean=2, std=1, best=1), run(op="bandit_select", arms=["a"]),
               run(op="retry_budget", budget=1, tasks=[{"task_id": "t", "value": 1, "cost": 1}])]
    assert all(r["completion_state"] in ("qualified-draft", "blocked-escalated") for r in replies)


def test_g5_no_op_is_sensitive() -> None:
    assert package.__g6_sensitive_ops__ == ()


def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("ad_hoc_choice",)
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.cogarch.decision_making" not in FailureModeRegistry.planned()


def test_d6_over_mcp() -> None:
    assert describe_reply("decision_making")["skill"]
    assert "retry_budget" in mcp("ops")["ops"]
    out = mcp("expected_information_gain", prior=0.5, true_positive_rate=1, false_positive_rate=0)
    assert out["result"] == pytest.approx(1.0) and out["code"] == "G6_E_DECISION_COMPUTED"


@pytest.mark.parametrize("reward", [1e308, -1e308])
def test_mdp_overflow_refused_without_values_or_policy(reward: float) -> None:
    # Fix: decision_making/mdp.py:115 (non-finite Q guard); removing it admits the overflowing losing action.
    out = run(op="value_iteration", states=["s"], transitions={"s": {"stay": [(1, "s", reward)]}}, discount=0.9)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_DECISION_NUMERIC"
    assert "result" not in out and "values" not in out and "policy" not in out
    # Even a losing action's overflowing Q-value must be refused, before max() hides it.
    losing = run(op="value_iteration", states=["low", "s", "end"], terminal=["end"], discount=0.9,
                 transitions={"low": {"end": [(1, "end", -1e308)]},
                              "s": {"good": [(1, "end", 0)], "bad": [(1, "low", -1e308)]}})
    assert losing["code"] == "G6_E_DECISION_NUMERIC" and "result" not in losing
    ordinary = run(op="value_iteration", states=["s"], transitions={"s": {"stay": [(1, "s", 1)]}}, discount=0.9)
    assert ordinary["code"] == "G6_E_DECISION_COMPUTED"
    assert ordinary["result"]["values"]["s"] == pytest.approx(10)
    assert ordinary["result"]["policy"] == {"s": "stay"}


@pytest.mark.parametrize("reward", [math.nan, math.inf, -math.inf])
def test_thompson_nonfinite_reward_refused(reward: float) -> None:
    # Fix: decision_making/block.py:240 (isfinite reward guard); removing it accepts non-finite successes.
    out = run(op="bandit_select", algorithm="thompson", arms=["a", "b"], history=[("a", reward)])
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_DECISION_INPUT"
    assert "result" not in out
    for finite in (0, 1):
        ordinary = run(op="bandit_select", algorithm="thompson", arms=["a", "b"], history=[("a", finite)], seed=7)
        expected = ThompsonBernoulli(("a", "b"), seed=7)
        expected.update("a", bool(finite))
        assert ordinary["code"] == "G6_E_DECISION_COMPUTED"
        assert ordinary["result"]["arm"] == expected.select()
