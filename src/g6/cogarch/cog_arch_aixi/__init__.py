# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_aixi — run a bounded MC-AIXI-CTW agent on a registered environment and judge it by its regret against a random policy on the same seed.

An extension in the cogarch family (standard library only). AIXI is incomputable; this is a bounded Monte Carlo
approximation of it (MC-AIXI-CTW): the environment model is a context tree over the agent's action-conditional
history, predicting each percept (an observation and a multi-bit reward), and the planner is rho-UCT over a clone of
that model, bounded in playouts, horizon and time, every choice seeded. The failure mode it guards against: a
planning agent acting on a model that has not learned the environment's rewards. ``run_episode`` measures that, as
reward and regret against a random policy on the same environment and seed, and refuses an agent no better than
random.

    from g6.cogarch.cog_arch_aixi import CogArchAixiBlock, CogArchAixiInput
    block = CogArchAixiBlock()
    block.infer(CogArchAixiInput(op="create", agent="guesser", num_actions=2, observation_bits=1, reward_bits=1))
    step = block.infer(CogArchAixiInput(op="act", agent="guesser")).unwrap()
    block.infer(CogArchAixiInput(op="observe", agent="guesser", observation=1, reward=int(step["action"] == 1)))

An environment is registered by a person from Python (``register_environment(name, factory)``, ``factory(seed)``
giving an :class:`~g6.cogarch.cog_arch_aixi.agent.Environment`); over MCP an episode can only name one, never send
code. ``run_episode`` is sensitive, since a registered environment may act outside G6.

Not ported from the archive's cog_arch_aixi, and why: the planner snapshot and the five self-descriptions it returned
as verified (AIXI-01); a planner whose reward model was created empty on every act, so it planned on its own imagined
rewards (AIXI-02); rewards cut to one bit (AIXI-03); actions counted as percepts (AIXI-04); the unseeded generator
(AIXI-05); the test that asserted verified (AIXI-06); the HAT gate and the analogy search (AIXI-07); the claude CLI
model door, the module-global environment table, the three hard-wired environments and the home-folder database
(AIXI-08); the thirteen describe and status ops, the stateless MCTS demos, the dead code and the scaffold (AIXI-09).
"""
from g6.cogarch.cog_arch_aixi import agent, ctw
from g6.cogarch.cog_arch_aixi.agent import Environment, register_environment, unregister_environment
from g6.cogarch.cog_arch_aixi.block import SENSITIVE_OPS, CogArchAixiBlock, CogArchAixiInput

__g6_component__ = True
__g6_block__ = "CogArchAixiBlock"
__g6_family__ = "cogarch"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # run_episode runs a registered environment, which may act
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unlearned_reward_model", "untested_agent_design")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "CogArchAixiBlock", "CogArchAixiInput", "Environment", "agent", "ctw",
           "register_environment", "unregister_environment"]
