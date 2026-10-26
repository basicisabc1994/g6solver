# SPDX-License-Identifier: Apache-2.0
"""CogArchAixiBlock: the registry and MCP door (``g6_invoke``) to cog_arch_aixi.

A bounded MC-AIXI-CTW agent, never AIXI itself (which is incomputable). ``create`` keeps an agent (the interaction's
shape, its context tree, its seed and its planning bounds) under G6's state folder; ``act`` plans with rho-UCT on a
clone of the agent's model and returns the action; ``observe`` teaches the model the percept that followed (the
caller's report of it, kept as given); ``run_episode`` previews registered environments by default, without any
callback. With ``dry_run=False`` it requires the action gate to allow the episode, then compares fresh agents with
a random policy on the same seeds. The three built-in toy worlds have no outward effects and need no gate.
``run_episode`` remains sensitive over MCP. The best any op says is ``qualified-draft``.
"""
from __future__ import annotations

import json
import math
import random
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6 import action_gating
from g6.cogarch import _reply
from g6.cogarch._store import StoreBusy, atomic_write, store_lock
from g6.cogarch.cog_arch_aixi import agent as ag
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.core.storage import state_path

COMPONENT = "cog_arch_aixi"
CREATED, EXISTS, NO_AGENT, ACTED, OBSERVED = "G6_E_AIXI_CREATED", "G6_E_AIXI_EXISTS", "G6_E_AIXI_NO_AGENT", \
    "G6_E_AIXI_ACTED", "G6_E_AIXI_OBSERVED"
OBSERVE_FIRST, ACT_FIRST, BEATS, NO_BETTER = "G6_E_AIXI_OBSERVE_FIRST", "G6_E_AIXI_ACT_FIRST", \
    "G6_E_AIXI_BEATS_RANDOM", "G6_E_AIXI_NO_BETTER_THAN_RANDOM"
NO_ENV, ENV, CAP, INPUT, STORE = "G6_E_AIXI_NO_ENVIRONMENT", "G6_E_AIXI_ENVIRONMENT", "G6_E_AIXI_CAP", \
    "G6_E_AIXI_INPUT", "G6_E_AIXI_STORE"
DRY_RUN, GATE = "G6_E_AIXI_DRY_RUN", "G6_E_AIXI_GATE"
SENSITIVE_OPS: tuple[str, ...] = ("run_episode",)
MAX_NODES = 200_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_APPROXIMATION = ("a bounded Monte Carlo approximation of AIXI (MC-AIXI-CTW: a context tree of bounded depth and "
                  "rho-UCT with bounded playouts and horizon), never AIXI itself, which is incomputable")
_OPS: dict[str, str] = {
    "create": "create(agent, num_actions, observation_bits, reward_bits, reward_min, depth, horizon, playouts, "
              "exploration, seed): keep a new agent whose model is a context tree over its action-conditional history",
    "act": "act(agent, time_limit_s): plan with rho-UCT on a clone of the agent's model and return the action, with "
           "each action's sampled value and visits",
    "observe": "observe(agent, observation, reward): teach the model the percept that followed the last action (the "
               "caller's report of it, kept as given)",
    "run_episode": "run_episode(environment, seeds, cycles, depth, horizon, playouts, time_limit_s, dry_run=true): "
                   "registered environments get a model-only preview using num_actions, observation_bits, "
                   "reward_bits and reward_min; dry_run=false also requires the action gate to allow the episode. "
                   "The built-in coin-flip, tiger-door and maze run without a gate; results compare against random "
                   "on the same seed (the op remains sensitive over MCP)",
}
_ACTIONS: dict[str, str] = {
    CREATED: "Call act, then observe the percept that follows, cycle by cycle.",
    EXISTS: "Use the agent kept under that name, or create one under another name.",
    NO_AGENT: "Create the agent first.",
    ACTED: "Take the action in the environment, then call observe with the observation and reward that followed. The "
           "action is the planner's choice on the model as learned so far; only run_episode measures whether such "
           "choices beat a random policy.",
    OBSERVED: "Call act for the next action.",
    OBSERVE_FIRST: "Call observe with the percept that followed the last action first.",
    ACT_FIRST: "Call act first: a percept is learned as the answer to an action.",
    BEATS: "The agents beat a random policy on these seeds, on this environment; that says nothing of other "
           "environments or of AIXI. Try other held-out seeds before relying on it.",
    NO_BETTER: "The agents' model did not learn enough to beat a random policy here: raise depth, horizon or "
               "playouts, run more cycles, or do not use this agent design for this environment.",
    NO_ENV: "Register the environment from Python (register_environment(name, factory)); over MCP an environment "
            "can only be named, never sent.",
    ENV: "The environment broke its declared shape or raised: fix it in Python.",
    CAP: "Raise the cap named, within its ceiling, or give a smaller problem.",
    INPUT: "Correct the request as the reason says.",
    DRY_RUN: "Review the model-only plan and shape assumptions; dry_run=false also needs the action gate to allow it.",
    GATE: "Resolve the action gate's refusal before calling the registered environment.",
    STORE: "The agent's file could not be read or written; check the state folder.",
    "G6_E_AIXI_STORE_BUSY": "Retry after the other call releases this agent's lock.",
}


class CogArchAixiInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["create", "act", "observe", "run_episode", "ops", "help"] = "help"
    agent: str = Field("", max_length=64)
    num_actions: int = Field(2, ge=2, le=64)
    observation_bits: int = Field(1, ge=0, le=16)
    reward_bits: int = Field(1, ge=1, le=16)
    reward_min: int = Field(0, ge=-1_000_000, le=1_000_000)
    depth: int = Field(12, ge=1, le=48)
    horizon: int = Field(3, ge=1, le=12)
    playouts: int = Field(100, ge=1, le=2000)
    exploration: float = Field(math.sqrt(2), ge=0, le=10)
    seed: int = Field(0, ge=0, le=2**32)
    observation: int | None = Field(None, ge=0)
    reward: int | None = None
    environment: str = Field("", max_length=64)
    dry_run: bool = True
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3], min_length=1, max_length=10)
    cycles: int = Field(50, ge=1, le=1000)
    time_limit_s: float = Field(10.0, gt=0, le=120)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: CogArchAixiInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refuse(op: str, code: str, why: str, d: CogArchAixiInput, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, op, code, why, action=_ACTIONS[code], reason=why, **_ids(d), **fields)


def _answer(op: str, code: str, said: str, d: CogArchAixiInput, **fields: Any) -> dict[str, Any]:
    return _reply.answer(COMPONENT, op, code, said, action=_ACTIONS[code], approximation=_APPROXIMATION, **_ids(d),
                         **fields)


class _Refusal(Exception):  # noqa: N818 - carries a refusal out of a helper
    def __init__(self, code: str, why: str, **fields: Any) -> None:
        super().__init__(why)
        self.code, self.why, self.fields = code, why, fields


def _path(name: str):
    if not _NAME.fullmatch(name):
        raise _Refusal(INPUT, "give agent: a name of lower-case letters, digits, - and _ (at most 64)")
    return state_path("cogarch", COMPONENT, f"{name}.json")


def _load(name: str) -> ag.Agent:
    path = _path(name)
    if not path.is_file():
        raise _Refusal(NO_AGENT, f"no agent named {name!r} is kept")
    try:
        return ag.Agent.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _Refusal(STORE, f"the agent {name!r} could not be read: {str(exc)[:200]}") from exc


def _save(name: str, agent: ag.Agent) -> None:
    path = _path(name)
    atomic_write(path, json.dumps(agent.to_dict(), separators=(",", ":")))


def _shape(a: ag.Agent) -> dict[str, Any]:
    s = a.spec
    return {"num_actions": s.num_actions, "observation_bits": s.observation_bits, "reward_bits": s.reward_bits,
            "reward_min": s.reward_min, "reward_max": s.reward_max, "depth": a.model.depth, "horizon": a.horizon,
            "playouts": a.playouts, "exploration": a.exploration, "seed": a.seed}


def _depth_warning(depth: int, cycle_bits: int) -> str:
    return (f" Warning: depth {depth} holds less than one cycle ({cycle_bits} bits), so the model cannot tie a "
            "whole percept to the action before it." if depth < cycle_bits else "")


def create(d: CogArchAixiInput) -> dict[str, Any]:
    path = _path(d.agent)
    if path.is_file():
        return _refuse("create", EXISTS, f"an agent named {d.agent!r} is kept already", d)
    spec = ag.Spec(d.num_actions, d.observation_bits, d.reward_bits, d.reward_min)
    agent = ag.Agent.new(spec, depth=d.depth, seed=d.seed, horizon=d.horizon, playouts=d.playouts,
                         exploration=d.exploration)
    _save(d.agent, agent)
    said = (f"Created {d.agent!r}: {spec.num_actions} actions, percepts of {spec.observation_bits} observation and "
            f"{spec.reward_bits} reward bits (rewards {spec.reward_min} to {spec.reward_max}), seed {d.seed}.")
    said += _depth_warning(d.depth, spec.cycle_bits)
    return _answer("create", CREATED, said, d, agent=d.agent, shape=_shape(agent),
                   evidence=[{"kind": "agent", "path": str(path), "seed": d.seed}])


def act(d: CogArchAixiInput) -> dict[str, Any]:
    agent = _load(d.agent)
    if agent.pending_action is not None:
        return _refuse("act", OBSERVE_FIRST, f"the last action ({agent.pending_action}) has not been observed", d)
    if agent.model.node_count() > MAX_NODES:
        return _refuse("act", CAP, f"the model holds more than max_nodes {MAX_NODES} nodes", d, cap="max_nodes")
    try:
        chose = agent.act(d.time_limit_s)
    except ag.Stopped as stop:
        return _refuse("act", CAP, f"planning stopped at its cap: {stop}, before one playout finished", d,
                       cap=stop.cap)
    _save(d.agent, agent)
    said = (f"Chose action {chose['action']} after {chose['playouts']} rho-UCT playouts to horizon "
            f"{chose['horizon']} on a clone of the model learned from {agent.steps} cycles.")
    evidence = [{"kind": "plan", "by": "rho-UCT over a clone of the agent's context tree", "seed": agent.seed,
                 "step": agent.steps, **{k: v for k, v in chose.items() if k != "action"}}]
    out = _answer("act", ACTED, said, d, agent=d.agent, step=agent.steps, values=chose["values"], evidence=evidence)
    out["action"] = chose["action"]     # beside the envelope's own suggested action
    return out


def observe(d: CogArchAixiInput) -> dict[str, Any]:
    agent = _load(d.agent)
    if agent.pending_action is None:
        return _refuse("observe", ACT_FIRST, "there is no action whose percept this could be", d)
    if d.observation is None or d.reward is None:
        return _refuse("observe", INPUT, "give observation and reward: the percept that followed the action", d)
    try:
        agent.observe(d.observation, d.reward)
    except ValueError as exc:
        return _refuse("observe", INPUT, str(exc), d)
    _save(d.agent, agent)
    said = f"Learned the percept (observation {d.observation}, reward {d.reward}) after step {agent.steps}."
    return _answer("observe", OBSERVED, said, d, agent=d.agent, steps=agent.steps, total_reward=agent.total_reward,
                   evidence=[{"kind": "claim", "field": "percept", "value": [d.observation, d.reward],
                              "checked": "against the declared ranges only"}])


def _preview(d: CogArchAixiInput, deadline: float) -> dict[str, Any]:
    """Plan the first step without constructing or inspecting a registered environment."""
    spec = ag.Spec(d.num_actions, d.observation_bits, d.reward_bits, d.reward_min)
    first = []
    for seed in dict.fromkeys(d.seeds):
        ag._check_deadline(deadline, d.time_limit_s, "preview")
        agent = ag.Agent.new(spec, depth=d.depth, seed=seed, horizon=d.horizon, playouts=d.playouts,
                             exploration=d.exploration)
        chose = agent.act(deadline - ag._wall())
        ag._check_deadline(deadline, d.time_limit_s, "preview")
        first.append({"seed": seed, "action": chose["action"], "values": chose["values"],
                      "baseline_action": random.Random(f"{seed}:random-policy").randrange(spec.num_actions)})
    return _answer("run_episode", DRY_RUN, "Dry run: no environment factory or step was called. First actions "
                   "use fresh models and the caller's unobserved shape assumptions.", d, dry_run=True,
                   environment=d.environment, cycles=d.cycles, total_steps=2 * d.cycles * len(first),
                   first_step_actions=first, shape_assumptions=spec.__dict__,
                   evidence=[{"kind": "plan", "environment_called": False, "shape_observed": False}])


def _gate_episode(d: CogArchAixiInput) -> dict[str, Any]:
    action = {
        "action_type": "run_code", "target_backend": "registered_environment", "target_resource": d.environment,
        "requested_by": "agent", "actor_type": "agent", "external_side_effect": True, "real_world_effect": True,
        "description": "Run registered environment factories and steps for an agent and a random baseline",
        "action_params": {"environment": d.environment, "seeds": list(dict.fromkeys(d.seeds)),
                          "cycles_per_policy": d.cycles, "total_steps": 2 * d.cycles * len(set(d.seeds))},
        "verification_contract": {
            "expected_result": "Integer percepts within the environment's declared ranges",
            "observation_window": f"Each callback return, within {d.time_limit_s:g} seconds overall",
            "leading_indicators": ["remaining deadline before each callback"],
            "lagging_indicators": ["percept range check", "reward compared with random baseline"],
            "monitor": "g6.cogarch.cog_arch_aixi.agent.episode and _step",
            "verification_time": "after each callback returns and at episode completion",
            "stop_threshold": "deadline reached, callback raises, or percept outside declared range",
            "rollback_procedure": "Stop further calls; external effects cannot be rolled back by G6",
            "model_update_rule": "Observe only range-checked percepts; refuse an incomplete episode",
        },
    }
    try:
        decided = action_gating.evaluate_action(action).gate_decision
        seen = {"decision": decided.decision, "reason_codes": list(decided.reason_codes),
                "message": decided.required_user_message, "audit_id": decided.audit_id}
    except Exception as exc:  # noqa: BLE001 - a missing or broken gate never grants permission
        raise _Refusal(GATE, f"the action gate failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
    if seen["decision"] not in ("allow", "allow_with_notice"):
        raise _Refusal(GATE, f"the action gate decided {seen['decision']}: {seen['message']}", gate=seen)
    return seen


def run_episode(d: CogArchAixiInput) -> dict[str, Any]:
    if not _NAME.fullmatch(d.environment):
        return _refuse("run_episode", INPUT, "give environment: the name it was registered under", d)
    factory = ag.environment(d.environment)
    if factory is None:
        registered = ", ".join(ag.environments()) or "none"
        return _refuse("run_episode", NO_ENV, f"no environment named {d.environment!r} is registered (registered: "
                                              f"{registered})", d)
    deadline = ag._wall() + d.time_limit_s
    results = []
    gate = None
    try:
        if not ag.is_builtin(factory):
            if d.dry_run:
                return _preview(d, deadline)
            gate = _gate_episode(d)
        for seed in dict.fromkeys(d.seeds):
            results.append(ag.episode(factory, seed, d.cycles, depth=d.depth, horizon=d.horizon,
                                      playouts=d.playouts, exploration=d.exploration, deadline=deadline,
                                      time_limit=d.time_limit_s))
    except _Refusal:
        raise
    except ag.Stopped as stop:
        return _refuse("run_episode", CAP, f"the episodes stopped at their cap: {stop}", d, cap=stop.cap,
                       episodes=results, phase=stop.phase)
    except Exception as exc:  # noqa: BLE001 - the person's environment failing is reported, fail closed
        return _refuse("run_episode", ENV, f"the environment failed: {type(exc).__name__}: {str(exc)[:200]}", d,
                       episodes=results)
    agent_mean = sum(r["agent_mean"] for r in results) / len(results)
    random_mean = sum(r["random_mean"] for r in results) / len(results)
    won = sum(1 for r in results if r["agent_reward"] > r["random_reward"])
    fields = {"environment": d.environment, "gate": gate, "episodes": results, "agent_mean_reward": agent_mean,
              "random_mean_reward": random_mean, "regret_vs_random_per_cycle": random_mean - agent_mean,
              "seeds_won": won,
              "evidence": [{"kind": "baseline", "by": "a random policy on the same environment and seed, which the "
                            "agent cannot influence", "seeds": [r["seed"] for r in results], "cycles": d.cycles}]}
    warning = _depth_warning(d.depth, results[0]["cycle_bits"])
    if agent_mean <= random_mean:
        return _refuse("run_episode", NO_BETTER, f"The agents averaged {agent_mean:.4f} reward a cycle against the "
                                                 f"random policy's {random_mean:.4f}: no better than random.{warning}",
                       d, **fields)
    said = (f"The agents averaged {agent_mean:.4f} reward a cycle against the random policy's {random_mean:.4f}, "
            f"winning on {won} of {len(results)} seeds.{warning}")
    return _answer("run_episode", BEATS, said, d, **fields)


@block_contract(
    purpose="run a bounded MC-AIXI-CTW agent (a context tree over its action-conditional history, rho-UCT planning on "
            "a clone of it) and judge it by its regret against a random policy on the same environment and seed",
    mitigates_failure_modes=("unlearned_reward_model", "untested_agent_design"),
    verification_method="run_episode compares each fresh agent's reward with a random policy's on the same registered "
                        "environment and seed; an agent no better than random is refused",
)
@component_maturity(
    "beta",
    reason="a context-tree model with multi-bit rewards and rho-UCT over a clone, seeded; tested on toy environments "
           "only; a bounded approximation, never AIXI",
)
class CogArchAixiBlock(AIBlock[CogArchAixiInput, dict, dict]):
    """A bounded MC-AIXI-CTW agent, judged against a random policy. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CogArchAixiInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values()),
                              "approximation": _APPROXIMATION})
        try:
            run = {"create": create, "act": act, "observe": observe, "run_episode": run_episode}[data.op]
            if data.op == "run_episode":
                return Result.ok(run(data))
            with store_lock(_path(data.agent)):
                return Result.ok(run(data))
        except StoreBusy as exc:
            return Result.ok(_refuse(data.op, "G6_E_AIXI_STORE_BUSY", str(exc), data))
        except OSError as exc:
            return Result.ok(_refuse(data.op, STORE, str(exc)[:200], data))
        except _Refusal as no:
            return Result.ok(_refuse(data.op, no.code, no.why, data, **no.fields))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "CogArchAixiBlock", "CogArchAixiInput"]
