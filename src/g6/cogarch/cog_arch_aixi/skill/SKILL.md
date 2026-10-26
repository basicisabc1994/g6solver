---
name: g6-cog-arch-aixi
description: Use when trying a model-based planning agent design on a small environment and it must be judged by a test rather than by argument, so an agent acting on a model that has not learned the environment's rewards is caught by its regret against a random policy on the same seed.
---

# G6 cog_arch_aixi

A bounded MC-AIXI-CTW agent, a teaching and research testbed. AIXI itself is incomputable; this is the Monte Carlo
approximation of Veness, Ng, Hutter, Uther and Silver (2011), small and bounded. The environment model is a context
tree (context tree weighting) over the agent's history, conditioned on its actions and predicting each percept: an
observation and a reward of several bits. The planner is rho-UCT run on a clone of that model. Every random choice is
seeded per agent and step. `run_episode` judges the design by the evaluator it cannot influence: a random policy on
the same environment and seed.

## When to use it

- Teaching or testing reinforcement-learning ideas on toy environments: a biased coin, a tiger behind a door, a short
  corridor.
- Checking whether a model-based agent beats a random policy on an environment you register, on held-out seeds.
- Driving an agent cycle by cycle from outside (`act`, then `observe`), where the caller is the environment.

## When not to use it

- For any real decision: the agent is a research approximation with a small model and short plans.
- For large observation spaces or long horizons: the model and the search are bounded and grow slowly.
- As AIXI: what it leaves out is most of AIXI (the universal mixture over all programs, unbounded planning).

## Ops

- `create`: keep a new agent: `num_actions`, `observation_bits`, `reward_bits`, `reward_min`, `depth`, `horizon`, `playouts`, `exploration`, `seed`.
- `act`: plan with rho-UCT on a clone of the agent's model and return the `action`, with each action's sampled value and visits.
- `observe`: teach the model the `observation` and `reward` that followed the last action (the caller's report, kept as given).
- `run_episode`: preview a registered environment by default (`dry_run: true`); `dry_run: false` requires an allowing action-gate decision, then compares a fresh agent per seed with random. The built-in `coin-flip`, `tiger-door` and `maze` run directly without a gate.

## Reading the result

A reward is an integer in `reward_min` to `reward_min + 2^reward_bits - 1`, an observation an integer below
`2^observation_bits`; anything else is refused. `act` gives `action`, `values` (per action: the mean sampled reward
to the horizon and its visits) and `G6_E_AIXI_ACTED`. `run_episode` gives `episodes` (per seed: agent and random
rewards, their means, `regret_vs_random`, the agent's mean over the second half, `model_nodes`), `agent_mean_reward`,
`random_mean_reward`, `regret_vs_random_per_cycle` and `seeds_won`: `G6_E_AIXI_BEATS_RANDOM`, `qualified-draft`, or
`G6_E_AIXI_NO_BETTER_THAN_RANDOM`, `blocked-escalated`. A reply warns when `depth` holds less than one cycle of bits.

A registered environment's default reply is `G6_E_AIXI_DRY_RUN`, `qualified-draft`: `environment`, `cycles`,
`total_steps` (both policies across distinct seeds), `first_step_actions` (agent and baseline actions per seed) and
`shape_assumptions`. The preview uses fresh models and the request's `num_actions`, `observation_bits`, `reward_bits`
and `reward_min`; these are unobserved assumptions, since no factory or step is called. It reports no measured
reward. Actual runs carry the gate's decision and audit reference in `gate`; toy runs have `gate: null`.

Refusals: `G6_E_AIXI_GATE` (denial, missing decision or a gate error), `G6_E_AIXI_STORE_BUSY` (lock timeout),
`G6_E_AIXI_OBSERVE_FIRST`, `G6_E_AIXI_ACT_FIRST`, `G6_E_AIXI_NO_AGENT`, `G6_E_AIXI_EXISTS`,
`G6_E_AIXI_NO_ENVIRONMENT` (it lists the registered names), `G6_E_AIXI_ENVIRONMENT` (an environment broke its declared
shape or raised), `G6_E_AIXI_CAP` (`cap`: `time_limit_s`, or `max_nodes` 200,000 in a kept model), `G6_E_AIXI_STORE`
and `G6_E_AIXI_INPUT`. Bounds and ceilings: `depth` 12 (48), `horizon` 3 (12), `playouts` 100 (2,000), `cycles`
50 (1,000), up to 10 `seeds`, `time_limit_s` 10 (120). Episode deadline checks run before and after factories and
steps in both policies, including the final baseline step. A deadline refusal identifies `phase` (`agent`,
`baseline`, `planning` or `preview`); an incomplete seed is omitted from `episodes`.

## Alone and composed

Register an environment from Python: `register_environment("my-env", factory)`, where `factory(seed)` returns an
object with `num_actions`, `observation_bits`, `reward_bits`, `reward_min` and `step(action) -> (observation,
reward)`. Over MCP an environment can only be named, never sent, and `run_episode` is sensitive: it runs only when
the person who runs the server lists `cog_arch_aixi.run_episode` in `G6_MCP_SENSITIVE`. This surface permission does
not replace the default preview or action gate for registered environments. Only `allow` or `allow_with_notice`
from `g6.action_gating.evaluate_action` lets a real registered episode start; caller approval fields do not.
The gate describes both policies' factories and steps and the range, deadline and baseline checks G6 performs.
Only the exact built-in toy factories are exempt; registering a replacement under a toy's name does not exempt it.
`create`, `act` and `observe` remain non-sensitive: they write only G6's own state. Each named agent is locked across
load, change and save, with a 5-second wait and stale-lock removal after 300 seconds. Replacements use unique
same-folder temporary files. An expired or replaced lease refuses the write with `G6_E_AIXI_STORE_BUSY`; retry
from a fresh load. Give `depth` at least one cycle of bits (action, observation and reward bits).

## Failure modes it catches

- `unlearned_reward_model`: a planning agent acting on a model that has not learned the environment's rewards. The
  planner runs on a clone of the live model, which learns every reward in all its bits, and `run_episode` refuses an
  agent that does no better than a random policy on the same seeds.
- `untested_agent_design`: an agent design judged by argument instead of by a test: `run_episode` is that test.

## Blind spots

- Beating a random policy is a low bar, and on few seeds it can be luck: read `seeds_won` and the second-half means,
  and try other held-out seeds.
- The model only sees `depth` bits back; an environment whose rewards depend on older history is not learned.
- Rewards and observations reported to `observe` are the caller's, checked only against the declared ranges.
- One environment's result says nothing of another's.
- A registered Python callback runs in its caller's process. A callback that hangs cannot be interrupted by these
  cooperative deadline checks; G6 checks again only when it returns. External effects cannot be rolled back, and
  range checks on reported percepts do not verify those effects. Register only code you control.

## What it needs

The built-in toy worlds need no key, outside program, extra or network. Registered environments are the person's
Python code and may have their own outside effects or requirements; real episodes pass through the action gate.
Agents are files under G6's state folder.

## Examples

```g6-example op=create
{"agent": "skill-guesser", "num_actions": 2, "observation_bits": 1, "reward_bits": 1, "playouts": 30, "seed": 3}
```

```g6-example op=act
{"agent": "skill-guesser"}
```

```g6-example op=observe
{"agent": "skill-guesser", "observation": 1, "reward": 0}
```

The caller is the environment here: it took the action, and reports the coin and the reward that followed.

```g6-example op=run_episode
{"environment": "coin-flip", "seeds": [1, 2, 3], "cycles": 40}
```

Sensitive: refused over MCP unless the server allows it. `coin-flip` is a built-in toy, so once allowed on that
surface it runs without an action gate. A registered environment instead returns a preview by default.

```python
import random

from g6.cogarch.cog_arch_aixi import CogArchAixiBlock, CogArchAixiInput, register_environment

class Coin:  # heads with probability 0.9; reward 1 for a right guess
    num_actions, observation_bits, reward_bits, reward_min = 2, 1, 1, 0
    def __init__(self, seed):
        self.rng = random.Random(seed)
    def step(self, action):
        coin = 1 if self.rng.random() < 0.9 else 0
        return coin, int(action == coin)

register_environment("skill-coin", Coin)
out = CogArchAixiBlock().infer(CogArchAixiInput(op="run_episode", environment="skill-coin", seeds=[1, 2],
                                                cycles=30, playouts=30, horizon=2, depth=6)).unwrap()
assert out["code"] == "G6_E_AIXI_DRY_RUN" and out["total_steps"] == 120
assert out["completion_state"] != "verified"
```

## The four open problems

- **Learning signals**: the rewards in each percept, learned in all their bits by the context tree; per-seed regret
  against random is the signal for the design.
- **Evaluators the model cannot influence**: the random policy on the same environment and seed; the agent's own
  value estimates never decide the label.
- **Transfer by failure mode**: bound to `unlearned_reward_model`, the same regret check for any registered
  environment.
- **Enough held-out cases**: the caller chooses the seeds; Not yet: the tests use three toy environments and a few
  seeds, not a benchmark suite.
