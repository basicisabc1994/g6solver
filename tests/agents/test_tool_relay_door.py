# SPDX-License-Identifier: Apache-2.0
"""An agent's tool call to a G6 component's op hands the component's answer to the agent, so it is a door (plan A1
review): the agent gets the door's copy, which claims no more than the door decided. Offline."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from returns.result import Success

from g6.action_gating.audit import ActionGateAuditLogger
from g6.agents._runtime import AgentTask, GatedCalls, ToolGate, ToolSpec
from g6.agents._runtime import codes
from g6.core import registry

MEMORY = ActionGateAuditLogger()


class _Registry:
    """One read-only op whose component calls its own answer verified."""

    def __init__(self, value) -> None:
        self.value = value

    def get_module(self, name):  # noqa: ANN001, ANN201
        return SimpleNamespace(__g6_read_only_ops__=("op",))

    def invoke(self, component, op, params, *, block_sensitive=False, **kwargs):  # noqa: ANN001, ANN201
        assert block_sensitive is True
        return Success(self.value)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch):
    def make(value) -> GatedCalls:
        monkeypatch.setattr(registry, "get_registry", lambda: _Registry(value))
        gate = ToolGate(AgentTask(goal="read the notes", tools=(ToolSpec(name="demo.op", effect="none"),)),
                        audit_logger=MEMORY)
        return GatedCalls(gate)
    return make


def test_an_agent_never_receives_a_components_own_verified(calls) -> None:
    answered = calls({"completion_state": "verified", "evidence": ["all tests passed"],
                      "parts": [{"reliability_label": "verified"}], "answer": 42}).call("demo.op", {})
    assert answered["verdict"] == "run" and answered["code"] == codes.TOOL_ALLOWED
    result = answered["result"]
    assert result["completion_state"] == "qualified-draft" and result["claimed_completion_state"] == "verified"
    assert result["parts"] == [{"reliability_label": "qualified-draft"}] and result["answer"] == 42


def test_an_agent_gets_a_block_as_a_block(calls) -> None:
    result = calls({"reliability_label": "blocked-escalated", "answer": 1}).call("demo.op", {})["result"]
    assert result["reliability_label"] == "blocked-escalated" and result["answer"] == 1
