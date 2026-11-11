# SPDX-License-Identifier: Apache-2.0
"""Only current wire arguments are supplied; withheld labels are data too."""
import asyncio
import json

from fastmcp import Client
from mcp.types import Implementation
import pytest

from g6.core import cloud_consent as cloud
from g6.core.consent_declarations import contract, declares
from g6.mcp import build_server


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


def test_producer_defaults_are_not_client_supplied_bytes():
    with cloud.collect_sources() as capture:
        capture.client_arguments = {'text': 'CURRENT_LITERAL'}
        defaults = {'text': 'CURRENT_LITERAL', 'default': 'PRIVATE_HOST_DEFAULT'}
        refs = contract('client_request').sources(defaults)
        actual = json.dumps(capture.client_arguments, sort_keys=True)
        assert {ref.source_id for ref in refs} == {cloud.input_source(actual)}
        assert refs[0].content_sha256 == cloud.sha256(actual)
        assert cloud.input_source(json.dumps(defaults, sort_keys=True)) not in capture.supplied


def test_a_private_retained_scope_label_is_withheld_until_supplied():
    label = 'PRIVATE_RETAINED_SCOPE_LABEL'
    source = cloud.Source.text(label, 'file', 'PRIVATE_BODY')
    with cloud.collect_sources() as capture:
        hidden = cloud.withheld_message('openai:codex', (source,))
        assert label not in hidden
        assert cloud.sha256(label) in hidden
        capture.supplied.add(cloud.input_source(label))
        assert label in cloud.withheld_message('openai:codex', (source,))


def test_producer_mutation_cannot_expand_the_supplied_argument_snapshot():
    server = build_server()
    @server.tool()
    @declares('client_request', 'g6_operational')
    def echo(payload: dict) -> dict:
        payload['extra'] = 'fixed program text'
        return payload
    arguments = {'payload': {'literal': 'CURRENT_LITERAL'}}
    original = json.dumps(arguments, sort_keys=True)
    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='repair')) as client:
            return await client.call_tool('echo', arguments)
    assert asyncio.run(run()).data['literal'] == 'CURRENT_LITERAL'
    assert cloud.grants() == []
    assert {row['source'] for entry in cloud.disclosures() for row in entry['sources']} == {cloud.input_source(original)}
