# SPDX-License-Identifier: Apache-2.0
"""The Python API ``g6.invoke`` takes the consent check ``g6_invoke`` and ``g6 invoke`` take (review-mvp-consent,
finding 1; the certificate issuance window before 0.3.0).

``g6.invoke`` lives in a certificate-bound file, so it kept calling the door with the raw registry after the other
surfaces handed it the guarded one (``g6.core.consent_registry.GuardedRegistry``): a configured remote model was sent
the caller's text with no grant. One outcome on the three surfaces now: with no live grant for the source and the
provider nothing is sent and the reply is the same refusal; with one, the call goes through and is disclosed. Offline:
the transport is a recorded stand-in.
"""
import json

import pytest

import g6
from g6.cli import main
from g6.core import cloud_consent as cloud, llm
from g6.mcp import invoke_reply

TEXT = 'SYNTHETIC_PRIVATE_PERSON'
PARAMS = {'text': TEXT, 'fields': {'person': 'person named in the text'}}
REMOTE = 'https://python-door-test.invalid'
PROVIDER = 'endpoint:' + cloud.sha256(REMOTE)
SURFACES = ['python', 'mcp', 'cli']


def invoke(surface, capsys):
    """(usable, the reply as text, the reply itself or None for the command) on one surface."""
    if surface == 'cli':
        code = main(['invoke', 'ctx_langextract', 'extract', json.dumps(PARAMS)])
        out, err = capsys.readouterr()
        return code == 0, out + err, None
    reply = (g6.invoke('ctx_langextract', 'extract', dict(PARAMS)) if surface == 'python' else
             invoke_reply('ctx_langextract', 'extract', json.dumps(PARAMS)))
    return reply['ok'], json.dumps(reply), reply


def decided(reply):
    """What a refusal decides: everything but the request's own id."""
    return {key: value for key, value in reply.items() if key != 'request_id'}


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


@pytest.mark.parametrize('surface', SURFACES)
@pytest.mark.parametrize('revoked', [False, True])
def test_no_surface_sends_to_a_remote_model_without_a_live_grant(surface, revoked, transport, accepted_cloud_dialog,
                                                                capsys):
    if revoked:
        accepted_cloud_dialog('model_inputs', PROVIDER, text=TEXT)
        cloud.revoke('model_inputs', PROVIDER)
    ok, said, _ = invoke(surface, capsys)
    assert transport == [], f'{surface}: private text reached the remote transport without a live grant'
    assert not ok and cloud.REQUIRED in said
    assert cloud.disclosures() == []


def test_the_python_refusal_is_the_mcp_refusal(transport, capsys):
    _, _, python = invoke('python', capsys)
    _, _, mcp = invoke('mcp', capsys)
    assert transport == []
    assert python['ok'] is False and python['completion_state'] == 'blocked-escalated'
    assert decided(python) == decided(mcp)
    code, text = python['warning_card']['code'], invoke('cli', capsys)[1]
    assert code in text and cloud.REQUIRED in text


@pytest.mark.parametrize('surface', SURFACES)
def test_a_grant_for_the_source_and_provider_lets_the_call_through_on_every_surface(surface, transport,
                                                                                  accepted_cloud_dialog, capsys):
    accepted_cloud_dialog('model_inputs', 'openai:api', text=TEXT)   # another provider's grant is not this one's
    assert not invoke(surface, capsys)[0] and transport == []
    accepted_cloud_dialog('model_inputs', PROVIDER, text=TEXT)
    ok, said, _ = invoke(surface, capsys)
    assert ok and TEXT in said
    assert len(transport) == 1 and transport[0][0] == REMOTE + '/api/chat'
    assert [row['provider'] for row in cloud.disclosures()] == [PROVIDER]
    cloud.revoke('model_inputs', PROVIDER)
    ok, said, _ = invoke(surface, capsys)
    assert not ok and cloud.REQUIRED in said and len(transport) == 1


def test_a_loopback_model_needs_no_grant_through_the_python_api(transport, monkeypatch, capsys):
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434')
    ok, said, _ = invoke('python', capsys)
    assert ok and TEXT in said and len(transport) == 1
    assert cloud.grants() == [] and cloud.disclosures() == []
