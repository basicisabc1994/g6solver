# SPDX-License-Identifier: Apache-2.0
"""g6.agents' shared runtime: the one interface every agent adapter goes through (the agent_runtime rewrite).

Shared family code, ``_``-prefixed so it never registers. The ``agent_runtime`` extension puts its checks on the
registry and MCP; each adapter (``agent_codex`` first) imports this, never a sibling.

* :mod:`~g6.agents._runtime.model`: the task a person sets (``Permissions``, ``Limits``, ``ToolSpec``, ``AgentTask``),
  a proposed ``ToolCall`` and the gate's ``ToolDecision``, an adapter's ``AgentEvent`` and the run's ``AgentOutcome``;
* :mod:`~g6.agents._runtime.gate`: ``ToolGate``, which decides every tool call before it runs: declared, permitted,
  inside the workspace, within the trusted ceilings of ``g6.safety_engine``, allowed by ``g6.action_gating``, and a
  dry run by default when it acts outward;
* :mod:`~g6.agents._runtime.run`: ``AgentRuntime`` (the interface), ``run_agent`` (the one way an adapter runs, with
  ``g6.core.stop`` checked before each step and ``g6.core.resources`` limits) and ``as_executor`` (for
  ``g6.harness.run``);
* :mod:`~g6.agents._runtime.cli`: ``CliAgentRunner``, the one way a command-line agent is started, through
  ``g6.core.component_process`` with a clean environment and a deadline;
* :mod:`~g6.agents._runtime.tools`: ``GatedCalls``, how a framework adapter calls a tool, each call decided first by
  the run's gate;
* :mod:`~g6.agents._runtime.breaker`: ``CircuitBreaker``, a deterministic state machine;
* :mod:`~g6.agents._runtime.codes`: the stable ``G6_E_AGENT_*`` codes.

Not carried over from the archive's ``agent_runtime`` (B05 card): the metrics server and process-global collector (an
HTTP listener in every block, AGENT_RUNTIME-04); the health check and breaker that asked a model, the breaker inside its
lock (AGENT_RUNTIME-02); the uniform protocol and fallback chain no agent used (AGENT_RUNTIME-01;
``g6.core.llm.FallbackCaller`` covers falling back between models); the retry policy, until an adapter makes a call
that is safe to repeat (an agent run changes things, so it runs at most once); the lifecycle mixin, the JSON logger and
the scaffold.
"""
from g6.agents._runtime import codes
from g6.agents._runtime.breaker import CircuitBreaker, CircuitState, is_transient_provider_error
from g6.agents._runtime.cli import BASE_ENV, CliAgentRunner, CliRun, clean_environment
from g6.agents._runtime.gate import ToolGate
from g6.agents._runtime.model import (
    AgentEvent,
    AgentOutcome,
    AgentRefusal,
    AgentTask,
    Availability,
    Limits,
    Permissions,
    ToolCall,
    ToolDecision,
    ToolSpec,
    effective_limits,
    model_named,
    versioned,
    workspace_problem,
)
from g6.agents._runtime.run import AgentExecutor, AgentRunBlocked, AgentRuntime, as_executor, run_agent
from g6.agents._runtime.tools import GatedCalls

__all__ = ["BASE_ENV", "AgentEvent", "AgentExecutor", "AgentOutcome", "AgentRefusal", "AgentRunBlocked",
           "AgentRuntime", "AgentTask", "Availability", "CircuitBreaker", "CircuitState", "CliAgentRunner", "CliRun",
           "GatedCalls",
           "Limits", "Permissions", "ToolCall", "ToolDecision", "ToolGate", "ToolSpec", "as_executor",
           "clean_environment", "codes", "effective_limits", "is_transient_provider_error", "model_named", "run_agent",
           "versioned", "workspace_problem"]
