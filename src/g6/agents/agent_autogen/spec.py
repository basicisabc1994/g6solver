# SPDX-License-Identifier: Apache-2.0
"""TeamSpec: an AutoGen team written as typed data, the form a team takes over MCP.

A spec names the team's pattern (``round_robin``: the agents speak in turn; ``selector``: a model picks who speaks
next; ``swarm``: an agent hands the task to another), its agents (each a name, its instructions, the tools it may call
and, in a swarm, whom it may hand over to), the tools (each a family ``ToolSpec``, so the runtime's gate knows what it
does), the permissions the tools need, the most messages the team may send and the text that says it has finished.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.agents._runtime import Permissions, ToolSpec

NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"


class AgentSpec(BaseModel):
    """One agent of the team."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=NAME)
    system_message: str = Field("", max_length=20_000)
    description: str = Field("", max_length=1000)
    tools: tuple[str, ...] = Field((), max_length=20)
    handoffs: tuple[str, ...] = Field((), max_length=10)


class TeamSpec(BaseModel):
    """A whole team; see the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pattern: Literal["round_robin", "selector", "swarm"] = "round_robin"
    agents: tuple[AgentSpec, ...] = Field(min_length=1, max_length=8)
    tools: tuple[ToolSpec, ...] = Field((), max_length=50)
    permissions: Permissions = Field(default_factory=Permissions)
    max_messages: int = Field(12, ge=1, le=100)
    max_tool_calls: int = Field(20, ge=0, le=200)
    stop_text: str = Field("TERMINATE", min_length=3, max_length=40, pattern=r"^[A-Za-z_]+$")

    @model_validator(mode="after")
    def _a_team_that_holds_together(self) -> TeamSpec:
        names = [a.name for a in self.agents]
        if len(set(names)) != len(names):
            raise ValueError("two agents share a name")
        if "user" in names:
            raise ValueError("'user' is the task's own speaker, not an agent's name")
        declared = {t.name for t in self.tools}
        for agent in self.agents:
            undeclared = [t for t in agent.tools if t not in declared]
            if undeclared:
                raise ValueError(f"agent {agent.name!r} calls {undeclared}, which the spec's tools do not declare")
            if agent.handoffs and self.pattern != "swarm":
                raise ValueError(f"agent {agent.name!r} hands over, which only a swarm does")
            unknown = [h for h in agent.handoffs if h not in names or h == agent.name]
            if unknown:
                raise ValueError(f"agent {agent.name!r} hands over to {unknown}, which are not other agents")
        if self.pattern == "swarm" and not any(a.handoffs for a in self.agents) and len(self.agents) > 1:
            raise ValueError("a swarm of several agents needs at least one that hands over")
        return self


__all__ = ["AgentSpec", "TeamSpec"]