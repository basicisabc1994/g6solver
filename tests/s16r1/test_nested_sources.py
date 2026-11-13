# SPDX-License-Identifier: Apache-2.0
"""Nested collectors and adapters retain origins, not just declarations."""
import asyncio

from fastmcp import Client
from mcp.types import Implementation
import pytest

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import declares
from g6.harness.completion_loop import ProbeRun
from g6.mcp import build_server
from g6.mcp.consent_controls import host_controls
from tests.s16r1.test_roots import grant


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


@pytest.mark.parametrize('unknown', [False, True])
def test_a_nested_collector_cannot_erase_carried_result_sources(unknown):
    marker = 'PRIVATE_NESTED_RESULT'
    source = cloud.UNKNOWN if unknown else cloud.Source.text('document:nested', 'file', marker)
    cloud.revoke(source.source_id, 'openai:codex')
    grant('declared')
    server = build_server()
    @server.tool()
    @declares('source:declared')
    def nested() -> dict:
        with cloud.collect_sources():
            return cloud.bind_output({'text': marker}, (source,))
    async def read():
        async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
            return await client.call_tool('nested', {})
    assert marker not in str(asyncio.run(read()))


def test_nested_rebinding_keeps_an_explicit_unknown():
    with cloud.collect_sources():
        value = cloud.bind_output({'text': 'PRIVATE'}, (cloud.UNKNOWN,))
        with cloud.collect_sources():
            cloud.bind_output(value, (cloud.Source.text('declared', 'file', 'PRIVATE'),))
            assert cloud.UNKNOWN in cloud.output_sources(value)
        assert cloud.UNKNOWN in cloud.output_sources(value)


def test_a_control_adapter_uses_the_producers_declared_frame():
    origin = cloud.Source.text('improvement', 'loop-state', 'synthetic state')
    with cloud.collect_sources(), cloud.request_sources((origin,)):
        value = host_controls({'text': 'synthetic state'}, {'code': 'G6_E_TEST'})
        refs = cloud.output_sources(value)
        assert origin in refs
        assert all(source.recorded for source in refs)
        explicit = cloud.bind_output({'text': 'PRIVATE'}, (cloud.UNKNOWN,))
        assert cloud.UNKNOWN in cloud.output_sources(host_controls(explicit, {}))


def test_typed_probe_data_can_carry_host_bound_sources():
    value = ProbeRun(1, {'test_probe': 'failed'}, {'test_probe': 'PRIVATE_DIAGNOSTIC'})
    with cloud.collect_sources():
        assert cloud.bind_output(value, (cloud.UNKNOWN,)) is value
        assert cloud.UNKNOWN in cloud.output_sources(value)
        # A result field by itself supplies no provenance or permission.
        unbound = ProbeRun(1, {}, {})
        unbound.sources = (cloud.Source.text('declared', 'file', ''),)
        assert cloud.output_sources(unbound) is None


def test_a_mutated_binding_cannot_become_unannotated_content():
    known = cloud.Source.text('declared', 'file', 'first')
    with cloud.collect_sources(), cloud.request_sources((known,)):
        value = cloud.bind_output({'text': 'first'}, (known,))
        value['text'] = 'PRIVATE_UNRECORDED_MUTATION'
        assert cloud.output_sources(value) is None
        assert cloud.UNKNOWN in cloud.current_capture().sources
        cloud.bind_output(value, (known,))
        assert cloud.UNKNOWN in cloud.output_sources(value)


def test_a_bare_process_prompt_needs_its_literal_grant_and_never_covers_unknown(tmp_path):
    from types import SimpleNamespace
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    marker, sent = 'PRIVATE_LITERAL_PROMPT', []
    for scope in (cloud.repository_source(tmp_path), 'agent_environment'):
        grant(scope)
    runner = SimpleNamespace(run=lambda *args, **kwargs: sent.append(kwargs['prompt']))
    with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    grant(cloud.input_source(marker))
    consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    assert sent == [marker]
    with cloud.request_sources((cloud.UNKNOWN,)), pytest.raises(AgentRefusal, match=cloud.REQUIRED):
        consent_run(runner, ['fake'], provider='openai:codex', prompt=marker, cwd=tmp_path)
    assert sent == [marker]
