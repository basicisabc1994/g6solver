# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_autogen — run an AutoGen team (round robin, selector or swarm) on the model you pass in, stopped at a message limit, every tool call decided by G6's gate.

An extension in the agents family, an adapter on its one runtime (``g6.agents._runtime``). The owner, 2026-10-20: the
archive's ``agent_autogen`` rewritten; its Python path ran one fixed two-agent chat on litellm with a key from the
environment, its MCP patterns were prompts over ``claude -p`` with its shell and edit tools, it set no ceiling, and it
called any non-empty conversation ``verified``.

Capability label: ``implemented``.

    from g6.agents._runtime import run_agent
    from g6.agents.agent_autogen import AutoGenRuntime, TeamSpec
    spec = TeamSpec.model_validate({"pattern": "round_robin", "max_messages": 6,
                                    "agents": [{"name": "writer"}, {"name": "critic"}]})
    runtime = AutoGenRuntime(spec, task="Draft the release note", llm=my_g6_llm_caller)
    outcome = run_agent(runtime, runtime.task())    # "qualified-draft", or G6_E_AGENT_STEP_LIMIT at the limit

AutoGen's AgentChat (MIT) is an extra: ``pip install 'g6[agents-autogen]'``; without it a run says so. A team is a
typed spec (:mod:`~g6.agents.agent_autogen.spec`) or one of your own (``AutoGenRuntime.from_team``). Every agent talks
to the ``g6.core.llm`` caller passed in through ``G6ChatClient`` and calls tools only as ``GatedTool``, each call
decided by the runtime's gate (:mod:`~g6.agents.agent_autogen.client`); a team of your own that G6 cannot see into is
refused. Each message is a step, checked against G6's stop, the step limit the trusted policy caps and the time limit
(:mod:`~g6.agents.agent_autogen.adapter`). A team that ends at its limit has not finished; one that finished is
``qualified-draft``.

Ops (:mod:`~g6.agents.agent_autogen.agent_autogen_block`): ``check_team``, and ``run_team``, which asks the model you
configured for G6 and so is sensitive.

Not ported from the archive: the MCP surface over ``claude -p`` (its sequential, group-chat, debate, reflection,
handoff, mixture and code-execution "patterns"), the SQLite store of agents, teams, runs and sessions, the litellm
client and the key from the environment, the "free model" heuristic, the billing and paid gates, the planner and the
scaffold.
"""
from typing import Any

from g6.agents.agent_autogen.adapter import AutoGenRuntime
from g6.agents.agent_autogen.agent_autogen_block import SENSITIVE_OPS, AgentAutoGenBlock, AgentAutoGenInput
from g6.agents.agent_autogen.spec import AgentSpec, TeamSpec

__g6_component__ = True
__g6_block__ = "AgentAutoGenBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # run_team asks the model you configured
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # through the family's runtime (g6.agents._runtime)
__g6_extras__: dict[str, tuple[str, ...]] = {"agents-autogen": ("autogen_agentchat", "autogen_core")}
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (an agent reads
# the other agents' messages and the tools' results, and text there can carry instructions the model follows).
__g6_mitigates__: tuple[str, ...] = ("unbounded_agent_workflow",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()


def __getattr__(name: str) -> Any:
    """``G6ChatClient`` and ``GatedTool`` need AutoGen, so they are imported only when asked for."""
    if name in ("G6ChatClient", "GatedTool"):
        from g6.agents.agent_autogen import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AgentAutoGenBlock", "AgentAutoGenInput", "AgentSpec", "AutoGenRuntime", "TeamSpec"]