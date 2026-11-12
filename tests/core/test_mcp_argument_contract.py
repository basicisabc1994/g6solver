# SPDX-License-Identifier: Apache-2.0
"""Producer wrappers must preserve the MCP input contract before any body runs."""
import asyncio
import importlib.util

import pytest
from fastmcp import Client, Context
from mcp.types import Implementation

from g6.mcp import build_server


def test_every_registered_tool_refuses_undeclared_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    entered = []

    def body_reached(self, *args, **kwargs):
        entered.append(self)
        raise AssertionError('input validation allowed the producer to run')

    if importlib.util.find_spec('g6.core.consent_declarations'):
        from g6.core.consent_declarations import Declaration
        monkeypatch.setattr(Declaration, 'sources', body_reached)
    server = build_server()

    async def run():
        tools = await server.get_tools()
        results = {}
        async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
            for name, tool in tools.items():
                assert 'agent_supplied_answer' not in tool.parameters.get('properties', {})
                # Supply every required argument so missing-argument errors
                # cannot make this inventory pass. Unknown schema types require
                # an explicit fixture when the tool inventory grows.
                properties = tool.parameters.get('properties', {})
                arguments = {}
                for key in tool.parameters.get('required', []):
                    values = {'string': 'synthetic-input', 'integer': 1, 'number': 1.0,
                              'boolean': False, 'object': {}, 'array': []}
                    kind = properties[key].get('type')
                    assert kind in values, (name, key, properties[key])
                    arguments[key] = values[kind]
                arguments['agent_supplied_answer'] = 'Person'
                results[name] = await client.call_tool(name, arguments, raise_on_error=False)
        return results

    results = asyncio.run(run())
    assert results
    assert all(reply.is_error for reply in results.values()), {
        name: str(reply) for name, reply in results.items() if not reply.is_error}
    assert not entered, 'undeclared arguments must be refused before any producer runs'


@pytest.mark.parametrize('arguments', [{'intent': 'not an object'}, {'intent': {}, 'answer': 'Person'}])
def test_schema_error_stays_a_tool_error_without_a_cloud_grant(tmp_path, monkeypatch, arguments):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    entered = []
    server = build_server()

    @server.tool()
    async def intent_contract(intent: dict, ctx: Context) -> dict:
        entered.append(intent)
        return {'result': intent}

    async def run():
        async with Client(server) as client:
            rejected = await client.call_tool('intent_contract', arguments, raise_on_error=False)
            healthy = await client.call_tool('g6_help', {})
            return rejected, healthy

    rejected, healthy = asyncio.run(run())
    assert rejected.is_error
    assert 'Traceback' not in str(rejected.content)
    assert not entered
    assert not healthy.is_error

