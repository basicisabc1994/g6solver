# SPDX-License-Identifier: Apache-2.0
"""Configured model admission through shipped invocation surfaces, with an offline transport."""
import json

import pytest

from g6.cli import main
from g6.core import cloud_consent as cloud, llm
from g6.mcp import invoke_reply


TEXT = 'SYNTHETIC_PRIVATE_PERSON'
PARAMS = {'text': TEXT, 'fields': {'person': 'person named in the text'}}
REMOTE = 'https://door-test.invalid'
PROVIDER = 'endpoint:' + cloud.sha256(REMOTE)


def invoke(surface, capsys):
    if surface == 'cli':
        code = main(['invoke', 'ctx_langextract', 'extract', json.dumps(PARAMS)])
        out, err = capsys.readouterr()
        return code == 0, out + err
    reply = invoke_reply('ctx_langextract', 'extract', json.dumps(PARAMS))
    return reply['ok'], json.dumps(reply)


@pytest.fixture
def transport(key_file, monkeypatch):
    key_file('G6_LLM_PROVIDER=ollama\n')
    monkeypatch.setenv('OLLAMA_BASE_URL', REMOTE)
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, body))
        return {'message': {'content': json.dumps({'person': TEXT})}}

    monkeypatch.setattr(llm, '_post_json', post)
    return sent


@pytest.mark.parametrize('surface', ['cli', 'mcp'])
@pytest.mark.parametrize('revoked', [False, True])
def test_configured_remote_model_needs_a_live_grant(surface, revoked, transport, accepted_cloud_dialog, capsys):
    if revoked:
        accepted_cloud_dialog('model_inputs', PROVIDER, text=TEXT)
        cloud.revoke('model_inputs', PROVIDER)
    ok, reply = invoke(surface, capsys)
    assert transport == [], 'private text reached the remote transport without a live grant'
    assert not ok and cloud.REQUIRED in reply
    assert cloud.disclosures() == []


@pytest.mark.parametrize('surface', ['cli', 'mcp'])
def test_dialog_grant_allows_only_its_source_and_provider(surface, transport, accepted_cloud_dialog, capsys):
    accepted_cloud_dialog('model_inputs', 'openai:api', text=TEXT)
    ok, _ = invoke(surface, capsys)
    assert not ok and transport == []
    accepted_cloud_dialog('source:other', PROVIDER, text='other synthetic input')
    ok, _ = invoke(surface, capsys)
    assert not ok and transport == []
    accepted_cloud_dialog('model_inputs', PROVIDER, text=TEXT)
    ok, reply = invoke(surface, capsys)
    assert ok and TEXT in reply
    assert len(transport) == 1
    assert TEXT in json.dumps(transport[0][1])
    assert transport[0][0] == REMOTE + '/api/chat'
    assert [row['provider'] for row in cloud.disclosures()] == [PROVIDER]
    cloud.revoke('model_inputs', PROVIDER)
    ok, reply = invoke(surface, capsys)
    assert not ok and cloud.REQUIRED in reply and len(transport) == 1


@pytest.mark.parametrize('surface', ['cli', 'mcp'])
@pytest.mark.parametrize('url', ['http://127.0.0.1:11434', 'http://localhost:11434', 'http://[::1]:11434'])
def test_loopback_model_keeps_working_without_a_grant(surface, url, transport, monkeypatch, capsys):
    monkeypatch.setenv('OLLAMA_BASE_URL', url)
    ok, reply = invoke(surface, capsys)
    assert ok and TEXT in reply
    assert len(transport) == 1 and transport[0][0] == url + '/api/chat'
    assert cloud.grants() == [] and cloud.disclosures() == []


@pytest.mark.parametrize('surface', ['cli', 'mcp'])
def test_a_retained_component_caller_checks_the_next_send(surface, transport, accepted_cloud_dialog,
                                                        monkeypatch, capsys):
    from g6.core import registry

    target = registry.get_registry()
    callers = []

    class RetainingRegistry:
        def __getattr__(self, name):
            return getattr(target, name)

        def invoke(self, *args, **kwargs):
            callers.append(kwargs['llm'])
            return target.invoke(*args, **kwargs)

    monkeypatch.setattr(registry, 'get_registry', RetainingRegistry)
    ok, _ = invoke(surface, capsys)
    assert not ok and transport == []
    caller = callers[0]
    accepted_cloud_dialog('model_inputs', PROVIDER, text=TEXT)
    assert json.loads(caller.complete('extract a person', TEXT)) == {'person': TEXT}
    assert len(transport) == 1
    cloud.revoke('model_inputs', PROVIDER)
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        caller.complete('extract a person', TEXT)
    assert len(transport) == 1
