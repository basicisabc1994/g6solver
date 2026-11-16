# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: F811 - imported pytest fixtures are also test parameters
"""MCP accepts data only; provider callers and declared validators are selected by G6."""
from __future__ import annotations

import asyncio

import pytest

from g6._agentic import kernel
from g6.core import llm
from tests.agentic.test_followup2 import offline  # noqa: F401
from tests.agentic.test_mcp_agentic import FLOAT, call, data, rig  # noqa: F401

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")


@pytest.mark.parametrize("field", ["responder", "validator", "caller", "llm"])
def test_mcp_call_cannot_accept_client_chosen_code(rig, field):
    async def go():
        async with rig.client() as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            schema = tools["g6_agentic_run"].inputSchema
            assert set(schema["properties"]) == {"component", "op", "params_json", "part", "mode", "reason"}
            reply = await client.call_tool("g6_agentic_run", {**FLOAT, field: "client-chosen-code"},
                                           raise_on_error=False)
            assert reply.is_error, data(reply)

    asyncio.run(go())
    assert rig.model.prompts == [] and rig.served == []


def test_mcp_normal_call_uses_g6_provider_caller_and_declared_validator(rig, monkeypatch):
    prepared = []
    original = kernel.prepare

    def capture(*args, **kwargs):
        assert "llm" not in kwargs
        pending = original(*args, **kwargs)
        prepared.append(pending)
        return pending

    monkeypatch.setattr(kernel, "prepare", capture)
    reply = call(rig, FLOAT)
    assert reply["agentic"]["outcome"] == "substituted"
    assert len(prepared) == 1
    pending = prepared[0]
    assert pending.caller.cls is llm.OllamaCaller and pending.caller.responder is None
    assert pending.validator == "g6.cegis.hole:valid_float_grid"
    assert reply["agentic"]["person"]["answer"] == "local model"
