# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_runtime — run any agent under one set of rules: declared tools and permissions, capped limits, G6's stop, and the action gate before each outward call.

An extension in the agents family. The owner, 2026-10-20: the archive's ``agent_runtime`` rewritten as the family's one
runtime interface, which every agent adapter goes through (B05 card, the shared design). The code lives in the
family's shared runtime, ``g6.agents._runtime``, so each adapter imports it without importing this extension; this
package puts its checks on the registry and MCP.

Capability label: ``implemented``.

    from g6.agents._runtime import AgentTask, Permissions, ToolSpec, run_agent
    task = AgentTask(goal="Tidy the notes", tools=(ToolSpec(name="edit", effect="write"),),
                     permissions=Permissions(workspace="/path/to/notes", read_files=True, write_files=True))
    outcome = run_agent(adapter, task)        # adapter: any AgentRuntime, such as agent_codex's CodexRuntime
    outcome.state, outcome.code               # "qualified-draft", "G6_E_AGENT_DRY_RUN": nothing outward ran
    outcome.claimed_status                    # what the agent said of its own work; it decides nothing

What every adapter gets from it:

* one interface, ``AgentRuntime``: a ``name``, ``available()`` and ``run(task, *, gate, usage, stop)``, which yields
  events; ``run_agent`` refuses an object that does not implement it (AGENT_RUNTIME-01);
* the same handling of every failure (``unhandled_agent_failure``): an adapter that raises any error, reports something
  that is not an event, stops without finishing, runs past its steps or its time, reports a tool result the gate never
  allowed, or keeps failing (a deterministic circuit breaker, AGENT_RUNTIME-02) ends as a ``blocked-escalated`` outcome
  with a stable ``G6_E_AGENT_*`` code, never a raised error;
* ``ToolGate`` before every tool call: the tool must be declared, its effect permitted, its paths inside the workspace
  (anything outside is refused, after ``g6.action_gating`` has evaluated it), the run within the trusted ceilings of
  ``g6.safety_engine``, and every effect allowed by ``g6.action_gating``; an outward call is a dry run unless you say
  otherwise;
* ``g6.core.stop`` checked before each step and each call, and a command-line agent started only through
  ``CliAgentRunner`` (``g6.core.component_process``, a clean environment, a deadline, its process tree ended);
* labels: a finished run is ``qualified-draft`` at best and the agent's own report is kept apart; ``as_executor`` hands
  the output to ``g6.harness.run``, whose checks judge it.

Ops over MCP (``g6_invoke``): ``check_task`` and ``decide`` (:mod:`~g6.agents.agent_runtime.agent_runtime_block`);
neither runs anything, so neither is sensitive. Running an agent is Python, through an adapter.

Not ported from the archive: the metrics server and the process-global collector, the model-judged health check and
circuit breaker, the uniform protocol and fallback chain no agent used, the retry policy (no adapter makes a call that
is safe to repeat yet), the lifecycle mixin, the JSON logger, the MCP surface over all of it and the scaffold.
"""
from g6.agents.agent_runtime.agent_runtime_block import SENSITIVE_OPS, AgentRuntimeBlock, AgentRuntimeInput

__g6_component__ = True
__g6_block__ = "AgentRuntimeBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS
__g6_read_only_ops__: tuple[str, ...] = ("check_task", "ops", "help")  # decide writes the action gate's audit trail
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # its block, and the family's runtime it puts on MCP
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unhandled_agent_failure",)
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["AgentRuntimeBlock", "AgentRuntimeInput"]
