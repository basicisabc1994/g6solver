# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_smolagents — run a smolagents tool-calling or code agent; its code runs in a separate, time-limited process with Python's network calls blocked.

An extension in the agents family, an adapter on its one runtime (``g6.agents._runtime``). The owner, 2026-10-20: the
archive's ``agent_smolagents`` rewritten; it ran tool code the caller stored in G6's own process, ran model-written
code in process with any import asked for, called the model through litellm with a key from the environment, took a
reviewer's signature as a review and called an admitted run ``verified``.

Capability label: ``implemented``.

    from g6.agents._runtime import run_agent
    from g6.agents.agent_smolagents import AgentSpec, SmolagentsRuntime
    spec = AgentSpec(agent_type="code", max_steps=6)
    runtime = SmolagentsRuntime(spec, task="What is the mean of 3, 5 and 10?", llm=my_g6_llm_caller)
    outcome = run_agent(runtime, runtime.task())     # "qualified-draft"; detail["steps"] holds each code step

smolagents (Apache-2.0) is an extra: ``pip install 'g6[agents-smolagents]'``; without it a run says so. G6 builds the
agent from a typed spec (:mod:`~g6.agents.agent_smolagents.spec`): a tool-calling agent whose every tool is decided
by the runtime's gate, or a code agent with no tools whose every step is held to a restricted syntax and run in
``g6.core.sandbox`` (:mod:`~g6.agents.agent_smolagents.sandbox`: another process with a clean environment, a time
limit, no network and an audit hook). That is not a security boundary: run agents you do not trust inside your own
sandbox (a container or VM).
Both talk to the ``g6.core.llm`` caller passed in (:mod:`~g6.agents.agent_smolagents.models`); each step is checked
against G6's stop, the step limit and the time limit (:mod:`~g6.agents.agent_smolagents.adapter`). An answer is
``qualified-draft``.

Ops (:mod:`~g6.agents.agent_smolagents.agent_smolagents_block`): ``check``, and ``run``, which asks the model you
configured for G6 and runs model-written code in G6's sandbox, and so is sensitive.

Not ported from the archive: the registry of tools stored as source code and run with ``exec`` in G6's process, the
in-process executor, the hosted executors (e2b and Modal are paid online services that would receive the code; G6's
sandbox runs it on this machine instead), litellm and the key from the environment, the default paid model, the
admission planner, the production gate that took a reviewer's signature, the scaffolds and the session store.
"""
from typing import Any

from g6.agents.agent_smolagents.adapter import SmolagentsRuntime
from g6.agents.agent_smolagents.agent_smolagents_block import SENSITIVE_OPS, AgentSmolagentsBlock, AgentSmolagentsInput
from g6.agents.agent_smolagents.spec import AgentSpec

__g6_component__ = True
__g6_block__ = "AgentSmolagentsBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # run asks the model you configured and runs its code
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # through the family's runtime (g6.agents._runtime)
__g6_extras__: dict[str, tuple[str, ...]] = {"agents-smolagents": ("smolagents",)}
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (the model reads
# tool results and code output, and text there can carry instructions it follows).
__g6_mitigates__: tuple[str, ...] = ("unbounded_code_execution",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__: tuple = ()


def __getattr__(name: str) -> Any:
    """``G6Model`` and ``G6Executor`` need smolagents, so they are imported only when asked for."""
    if name in ("G6Model", "G6Executor"):
        from g6.agents.agent_smolagents import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["AgentSmolagentsBlock", "AgentSmolagentsInput", "AgentSpec", "SmolagentsRuntime"]