# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_aixi: a bounded MC-AIXI-CTW agent (an action-conditional context tree, multi-bit rewards,
rho-UCT over a clone of the live model, seeded), judged by its regret against a random policy on the same environment
and seed. The three toy environments (CoinFlip, TigerDoor, SimpleMaze1D) have independent reference fixtures here.

The context-tree values are worked by hand from the Krichevsky-Trofimov estimate P(1) = (ones + 1/2) / (seen + 1) and
the mixture Pw = 1/2 Pkt + 1/2 Pw(child 0) Pw(child 1), a leaf at full depth being its KT estimate."""
from __future__ import annotations

import json
import math
import random

import pytest
from pydantic import ValidationError

import g6.cogarch.cog_arch_aixi as package
from g6.cogarch.cog_arch_aixi import CogArchAixiBlock, CogArchAixiInput, agent, register_environment
from g6.cogarch.cog_arch_aixi.ctw import CTWTree
from g6.core.failure_modes import FailureModeRegistry
from g6.mcp import describe_reply, invoke_reply


# --- the toy environments: fixtures, registered from Python -----------------------------------------------------------

class CoinFlip:
    """Guess the coin (heads 1 with probability bias); reward 1 for a right guess. Observation: the coin."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 1, 1, 0

    def __init__(self, seed: int, bias: float = 0.9) -> None:
        self.rng, self.bias = random.Random(f"coin:{seed}"), bias

    def step(self, action: int) -> tuple[int, int]:
        coin = 1 if self.rng.random() < self.bias else 0
        return coin, int(action == coin)


class TigerDoor:
    """Listen (-1, a noisy hint), or open a door: +10 for the gold, -100 for the tiger, then the tiger moves."""

    num_actions, observation_bits, reward_bits, reward_min = 3, 1, 7, -100

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(f"tiger:{seed}")
        self.tiger = self.rng.randrange(2)

    def step(self, action: int) -> tuple[int, int]:
        if action == 0:
            hint = self.tiger if self.rng.random() < 0.85 else 1 - self.tiger
            return hint, -1
        reward = -100 if action - 1 == self.tiger else 10
        self.tiger = self.rng.randrange(2)
        return 0, reward


class SimpleMaze1D:
    """Cells 0 to 3, start at 0, actions left and right; reaching cell 3 pays 1 and returns to 0. Observation: the
    cell."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 2, 1, 0

    def __init__(self, seed: int) -> None:
        self.cell = 0

    def step(self, action: int) -> tuple[int, int]:
        self.cell = max(0, self.cell - 1) if action == 0 else self.cell + 1
        if self.cell == 3:
            self.cell = 0
            return 0, 1
        return self.cell, 0


class TwoBadDoors:
    """Door 0 costs 1, door 1 costs 100: told apart only with more than one reward bit."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 0, 7, -100

    def __init__(self, seed: int) -> None:
        pass

    def step(self, action: int) -> tuple[int, int]:
        return 0, -1 if action == 0 else -100


class Indifferent:
    """Every action pays the same: no design can beat a random policy here."""

    num_actions, observation_bits, reward_bits, reward_min = 2, 1, 1, 0

    def __init__(self, seed: int) -> None:
        pass

    def step(self, action: int) -> tuple[int, int]:
        return 0, 0


class Broken:
    num_actions, observation_bits, reward_bits, reward_min = 2, 1, 1, 0

    def __init__(self, seed: int) -> None:
        pass

    def step(self, action: int) -> tuple[int, int]:
        return 0, 5         # outside the declared rewards 0 to 1


@pytest.fixture(autouse=True)
def environments(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6"))
    from types import SimpleNamespace

    from g6 import action_gating

    # A trusted gate double permits the extra closed test worlds; dedicated tests replace it with denial/failure.
    monkeypatch.setattr(action_gating, "evaluate_action", lambda action: SimpleNamespace(gate_decision=SimpleNamespace(
        decision="allow", reason_codes=[], required_user_message="test permits", audit_id="test")))
    for name, factory in (("two-bad-doors", TwoBadDoors), ("indifferent", Indifferent), ("broken", Broken)):
        register_environment(name, factory)
    yield
    for name in ("coin-flip", "tiger-door", "maze", "two-bad-doors", "indifferent", "broken"):
        package.unregister_environment(name)


def run(**params) -> dict:
    return CogArchAixiBlock().infer(CogArchAixiInput(**params)).unwrap()


def episode(environment: str, **params) -> dict:
    # depth 10 holds a whole cycle of the widest fixture (1 action bit and 7 reward bits)
    params = {"dry_run": False, "cycles": 40, "playouts": 40, "horizon": 2, "depth": 10, "seeds": [1, 2, 3],
              "time_limit_s": 120, **params}
    return run(op="run_episode", environment=environment, **params)


# --- D1: an agent no better than random is caught; one that has learned the rewards passes ----------------------------

def test_d1_an_agent_that_learned_nothing_useful_is_refused_and_one_that_learned_passes() -> None:
    caught = episode("indifferent")
    assert caught["completion_state"] == "blocked-escalated" and caught["code"] == "G6_E_AIXI_NO_BETTER_THAN_RANDOM"
    assert caught["regret_vs_random_per_cycle"] == 0
    passed = episode("coin-flip")
    assert passed["completion_state"] == "qualified-draft" and passed["code"] == "G6_E_AIXI_BEATS_RANDOM"
    assert any(e["kind"] == "baseline" for e in passed["evidence"])


# --- P0 and P1 ----------------------------------------------------------------------------------------------------------

def test_p1_aixi02_on_a_biased_coin_the_agent_beats_a_random_policy_on_every_held_out_seed() -> None:
    out = episode("coin-flip", seeds=[11, 12, 13])
    assert out["seeds_won"] == 3 and out["agent_mean_reward"] > out["random_mean_reward"] + 0.2
    assert all(e["second_half_agent_mean"] >= 0.75 for e in out["episodes"])


def test_p1_aixi03_rewards_of_many_bits_tell_a_small_loss_from_a_large_one() -> None:
    spec = agent.Spec(2, 0, 7, -100)
    assert spec.percept_bits(0, -1) != spec.percept_bits(0, -100)
    assert spec.decode(spec.percept_bits(0, -1)) == (0, -1) and spec.decode(spec.percept_bits(0, -100)) == (0, -100)
    out = episode("two-bad-doors")
    assert out["code"] == "G6_E_AIXI_BEATS_RANDOM" and out["agent_mean_reward"] > -25
    assert out["random_mean_reward"] < -40


def test_p0_aixi01_no_op_answers_verified() -> None:
    replies = [run(op="create", agent="a", num_actions=2, observation_bits=1, reward_bits=1, playouts=10)]
    replies.append(run(op="act", agent="a"))
    replies.append(run(op="observe", agent="a", observation=1, reward=1))
    replies.append(episode("coin-flip", cycles=5, seeds=[1]))
    assert all(r["completion_state"] in ("qualified-draft", "blocked-escalated") for r in replies)
    assert "never AIXI itself" in replies[1]["approximation"]


# --- the context tree's known values ----------------------------------------------------------------------------------

def test_kt_known_values() -> None:
    # depth 0 is one KT estimator. Bits 1, 1, 0: 1/2 x 3/4 x 1/6 = 0.0625; then P(1) = (2 + 1/2) / (3 + 1) = 0.625
    tree = CTWTree(depth=0)
    tree.update([1, 1, 0])
    assert math.exp(tree.log_probability()) == pytest.approx(0.0625)
    assert tree.predict(1) == pytest.approx(0.625) and tree.predict(0) == pytest.approx(0.375)


def test_ctw_known_values_at_depth_one() -> None:
    # bits 1, 0, 1 after a zero context: context 0 sees 1 and 1 (KT 1/2 x 3/4 = 0.375), context 1 sees 0 (KT 1/2);
    # the root's KT of 1, 0, 1 is 1/2 x 1/4 x 1/2 = 0.0625; Pw = 1/2 0.0625 + 1/2 (0.375 x 0.5) = 0.125.
    # Next bit, context 1: with a 1, context 1 sees 0, 1 (0.125), root KT 0.0625 x 5/8 = 0.0390625,
    # Pw = 0.01953125 + 1/2 (0.375 x 0.125) = 0.04296875, so P(1) = 0.04296875 / 0.125 = 0.34375 and P(0) = 0.65625
    tree = CTWTree(depth=1)
    tree.update([1, 0, 1])
    assert math.exp(tree.log_probability()) == pytest.approx(0.125)
    assert tree.predict(1) == pytest.approx(0.34375) and tree.predict(0) == pytest.approx(0.65625)


def test_revert_and_extend_leave_the_tree_as_it_was() -> None:
    tree = CTWTree(depth=4)
    tree.update([1, 0, 1, 1, 0])
    before, history, nodes = tree.to_dict(), list(tree.history), tree.node_count()
    tree.extend([1, 1])                  # an action: context only
    assert tree.to_dict()["root"] == before["root"]
    tree.update([0, 1])
    tree.revert(2)
    tree.retract(2)
    after = tree.to_dict()["root"]
    assert (after["c"], after["k"], after["w"]) == (before["root"]["c"], before["root"]["k"], before["root"]["w"])
    assert tree.history == history
    assert math.isclose(tree.log_probability(), CTWTree.from_dict(before).log_probability())
    assert tree.node_count() >= nodes


# --- act and observe, persisted across MCP calls -------------------------------------------------------------------

def mcp(op: str, **params) -> dict:
    reply = invoke_reply("cog_arch_aixi", op, json.dumps(params))
    assert "result" in reply and reply["ok"] is (reply["completion_state"] != "blocked-escalated"), reply
    return reply["result"]


def test_act_and_observe_over_mcp_keep_the_agent_between_calls() -> None:
    made = mcp("create", agent="mcp-agent", num_actions=2, observation_bits=1, reward_bits=1, playouts=20, seed=4)
    assert made["code"] == "G6_E_AIXI_CREATED" and made["shape"]["reward_max"] == 1
    env, total = CoinFlip(4), 0
    for _ in range(6):
        chose = mcp("act", agent="mcp-agent")
        assert chose["code"] == "G6_E_AIXI_ACTED" and chose["action"] in (0, 1)
        observation, reward = env.step(chose["action"])
        total += reward
        seen = mcp("observe", agent="mcp-agent", observation=observation, reward=reward)
    assert seen["steps"] == 6 and seen["total_reward"] == total
    assert mcp("observe", agent="mcp-agent", observation=0, reward=0)["code"] == "G6_E_AIXI_ACT_FIRST"
    mcp("act", agent="mcp-agent")
    assert mcp("act", agent="mcp-agent")["code"] == "G6_E_AIXI_OBSERVE_FIRST"
    assert mcp("observe", agent="mcp-agent", observation=2, reward=0)["code"] == "G6_E_AIXI_INPUT"


def test_the_same_seed_gives_the_same_episode_and_another_seed_another() -> None:
    first = episode("coin-flip", cycles=15, seeds=[5])
    again = episode("coin-flip", cycles=15, seeds=[5])
    other = episode("coin-flip", cycles=15, seeds=[6])
    assert first["episodes"] == again["episodes"] and first["episodes"] != other["episodes"]


def test_the_other_toy_environments_run_and_report_their_regret() -> None:
    for name in ("tiger-door", "maze"):
        out = episode(name, cycles=15, playouts=20, seeds=[1, 2])
        assert out["code"] in ("G6_E_AIXI_BEATS_RANDOM", "G6_E_AIXI_NO_BETTER_THAN_RANDOM")
        assert len(out["episodes"]) == 2 and all("regret_vs_random" in e for e in out["episodes"])


# --- the environment is registered from Python, never sent -------------------------------------------------------------

def test_an_unregistered_or_broken_environment_is_refused() -> None:
    missing = episode("nowhere")
    assert missing["code"] == "G6_E_AIXI_NO_ENVIRONMENT" and "coin-flip" in missing["warning_card"]["message"]
    broken = episode("broken", cycles=3, seeds=[1])
    assert broken["code"] == "G6_E_AIXI_ENVIRONMENT" and "outside" in broken["warning_card"]["message"]
    with pytest.raises(ValidationError):
        CogArchAixiInput(op="run_episode", environment="x", environment_code="import os")


def test_g5_run_episode_is_sensitive_and_refused_over_mcp() -> None:
    assert package.__g6_sensitive_ops__ == ("run_episode",)
    reply = invoke_reply("cog_arch_aixi", "run_episode", json.dumps({"environment": "coin-flip"}))
    assert not reply["ok"] and "G6_E_SENSITIVE_COMPONENT_DENIED" in reply["error"]
    allowed = invoke_reply("cog_arch_aixi", "run_episode", json.dumps({"environment": "coin-flip", "cycles": 5,
                                                                       "seeds": [1], "playouts": 10}),
                           allow_sensitive=frozenset({"cog_arch_aixi.run_episode"}))
    assert "result" in allowed and allowed["result"]["episodes"][0]["seed"] == 1   # it ran
    assert allowed["ok"] is (allowed["completion_state"] != "blocked-escalated")


# --- caps and the gates ---------------------------------------------------------------------------------------------------

def test_g4_the_time_limit_stops_planning_and_episodes(monkeypatch: pytest.MonkeyPatch) -> None:
    run(op="create", agent="slow", num_actions=2, observation_bits=1, reward_bits=1)
    ticks = iter(range(0, 100_000, 50))
    monkeypatch.setattr(agent, "_wall", lambda: next(ticks))
    out = run(op="act", agent="slow", time_limit_s=1)
    assert out["code"] == "G6_E_AIXI_CAP" and out["cap"] == "time_limit_s"
    late = episode("coin-flip", time_limit_s=1)
    assert late["code"] == "G6_E_AIXI_CAP" and late["cap"] == "time_limit_s"


@pytest.mark.parametrize("field", ["playouts", "horizon", "depth", "cycles", "time_limit_s"])
def test_g4_a_bound_cannot_be_raised_past_its_ceiling(field: str) -> None:
    with pytest.raises(ValidationError):
        CogArchAixiInput(op="run_episode", environment="coin-flip", **{field: 10**6})


def test_g3_a_callers_word_is_refused_as_a_field() -> None:
    for name in ("approved", "verified", "confidence", "optimal"):
        with pytest.raises(ValidationError):
            CogArchAixiInput(op="act", agent="a", **{name: True})


def test_an_agent_must_exist_and_cannot_be_created_twice() -> None:
    assert run(op="act", agent="ghost")["code"] == "G6_E_AIXI_NO_AGENT"
    run(op="create", agent="twice")
    assert run(op="create", agent="twice")["code"] == "G6_E_AIXI_EXISTS"
    assert run(op="create", agent="../out")["code"] == "G6_E_AIXI_INPUT"


# --- D2, D6 ---------------------------------------------------------------------------------------------------------------

def test_d2_the_failure_modes_are_registered_and_no_longer_planned() -> None:
    assert package.__g6_mitigates__ == ("unlearned_reward_model", "untested_agent_design")
    assert all(FailureModeRegistry.is_registered(c) for c in (*package.__g6_mitigates__, *package.__g6_introduces__))
    assert "g6.cogarch.cog_arch_aixi" not in FailureModeRegistry.planned()


def test_d6_over_mcp() -> None:
    assert describe_reply("cog_arch_aixi")["skill"]
    assert mcp("create", agent="d6", playouts=5)["code"] == "G6_E_AIXI_CREATED"
    assert mcp("act", agent="d6")["code"] == "G6_E_AIXI_ACTED"


@pytest.mark.parametrize("slow_at", ["factory", "step"])
def test_random_baseline_deadline_refuses_after_slow_callback(monkeypatch: pytest.MonkeyPatch, slow_at: str) -> None:
    # Fix: cog_arch_aixi/agent.py:320 (post-step deadline check); removing it accepts the final slow step.
    from types import SimpleNamespace

    from g6 import action_gating

    clock, instances, steps = [0.0], [], []

    class SlowBaseline(Indifferent):
        def __init__(self, seed):
            instances.append(self)
            self.baseline = len(instances) == 2
            if self.baseline and slow_at == "factory":
                clock[0] = 2.0

        def step(self, action):
            if self.baseline:
                steps.append(action)
                clock[0] = 2.0
            return 0, 0

    monkeypatch.setattr(agent, "_wall", lambda: clock[0])
    monkeypatch.setattr(action_gating, "evaluate_action", lambda action: SimpleNamespace(gate_decision=SimpleNamespace(
        decision="allow", reason_codes=[], required_user_message="test permits", audit_id="test")))
    register_environment("slow-baseline", SlowBaseline)
    try:
        out = run(op="run_episode", environment="slow-baseline", dry_run=False, cycles=1, seeds=[1],
                  time_limit_s=1, playouts=2, horizon=1)
    finally:
        package.unregister_environment("slow-baseline")
    assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_AIXI_CAP"
    assert out["cap"] == "time_limit_s" and out["phase"] == "baseline"
    assert len(steps) == (0 if slow_at == "factory" else 1)
    assert out["episodes"] == []


@pytest.mark.parametrize("decision", ["block", "allow", "allow_with_notice", "raises"])
def test_registered_environment_dry_run_and_gate(monkeypatch: pytest.MonkeyPatch, decision: str) -> None:
    # Fix: cog_arch_aixi/block.py:279 (preview return) and :261 (gate allow-list); removing either breaks refusal.
    from types import SimpleNamespace

    from g6 import action_gating

    calls, evaluated = [], []

    class Outward(Indifferent):
        def __init__(self, seed):
            calls.append(("factory", seed))

        def step(self, action):
            calls.append(("step", action))
            return 0, 0

    def gate(action):
        evaluated.append(action)
        if decision == "raises":
            raise RuntimeError("gate unavailable")
        return SimpleNamespace(gate_decision=SimpleNamespace(
            decision=decision, reason_codes=["G6_E_TEST_GATE"], required_user_message="test gate", audit_id="test"))

    monkeypatch.setattr(action_gating, "evaluate_action", gate)
    register_environment("outward", Outward)
    try:
        params = {"environment": "outward", "cycles": 2, "seeds": [3, 4], "playouts": 3, "horizon": 1}
        dry = run(op="run_episode", **params)
        assert dry["code"] == "G6_E_AIXI_DRY_RUN" and dry["completion_state"] == "qualified-draft"
        assert dry["environment"] == "outward" and dry["cycles"] == 2 and dry["total_steps"] == 8
        assert [p["seed"] for p in dry["first_step_actions"]] == [3, 4]
        assert all(p["action"] in (0, 1) and p["baseline_action"] in (0, 1) for p in dry["first_step_actions"])
        assert calls == [] and evaluated == [] and "episodes" not in dry
        out = run(op="run_episode", dry_run=False, **params)
    finally:
        package.unregister_environment("outward")
    assert len(evaluated) == 1
    assert evaluated[0]["external_side_effect"] is True
    assert evaluated[0]["target_resource"] == "outward"
    assert evaluated[0]["verification_contract"]["monitor"]
    if decision in ("allow", "allow_with_notice"):
        assert len(calls) == 12  # two factories and four steps per seed
        assert len(out["episodes"]) == 2 and out["gate"]["decision"] == decision
    else:
        assert calls == [] and out["completion_state"] == "blocked-escalated"
        assert out["code"] == "G6_E_AIXI_GATE"


def test_builtin_toys_need_no_gate_and_names_cannot_bypass_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix: cog_arch_aixi/block.py:277 (factory-identity exemption); removing it gates the built-in toys.
    from types import SimpleNamespace

    from g6 import action_gating

    calls = []
    monkeypatch.setattr(action_gating, "evaluate_action", lambda action: SimpleNamespace(gate_decision=SimpleNamespace(
        decision="block", reason_codes=[], required_user_message="denied", audit_id="test")))
    for name in ("coin-flip", "tiger-door", "maze"):
        package.unregister_environment(name)
        out = run(op="run_episode", environment=name, cycles=1, seeds=[1], playouts=2, horizon=1)
        assert len(out["episodes"]) == 1
    register_environment("coin-flip", lambda seed: calls.append(seed) or Indifferent(seed))
    try:
        out = run(op="run_episode", environment="coin-flip", dry_run=False, cycles=1, seeds=[1])
        assert out["code"] == "G6_E_AIXI_GATE" and calls == []
    finally:
        package.unregister_environment("coin-flip")
