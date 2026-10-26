# SPDX-License-Identifier: Apache-2.0
"""AgentSpec: a smolagents agent written as typed data, the form it takes over MCP.

``agent_type`` is ``toolcalling`` (the model calls declared tools, one per step, each decided by the runtime's gate)
or ``code`` (the model writes Python, each step run in G6's sandbox; it takes no tools, because code in the sandbox
cannot reach the gate). The spec also names the tools (family ``ToolSpec``), the permissions they need, the most
steps, a code step's time limit and any instructions. There is no executor to choose, no import to add and no tool
given as source code: those are fixed (B05 card, AGENT_SMOLAGENTS-01, -02, -06).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.agents._runtime import Permissions, ToolSpec


class AgentSpec(BaseModel):
    """See the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_type: Literal["toolcalling", "code"] = "toolcalling"
    tools: tuple[ToolSpec, ...] = Field((), max_length=30)
    permissions: Permissions = Field(default_factory=Permissions)
    max_steps: int = Field(8, ge=1, le=50)
    max_tool_calls: int = Field(20, ge=0, le=200)
    code_timeout_s: float = Field(30.0, gt=0, le=120, allow_inf_nan=False)
    instructions: str = Field("", max_length=20_000)

    @model_validator(mode="after")
    def _code_takes_no_tools(self) -> AgentSpec:
        if self.agent_type == "code" and self.tools:
            raise ValueError("a code agent takes no tools: its code runs in G6's sandbox, which cannot reach the gate; "
                             "use a toolcalling agent for tools")
        return self


__all__ = ["AgentSpec"]