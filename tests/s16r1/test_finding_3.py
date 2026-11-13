import asyncio
from types import SimpleNamespace

from g6.agents._runtime.consent import consent_run
from g6.agents._runtime.model import AgentRefusal
from g6.core import cloud_consent as cloud
from g6.mcp.cloud_consent import request_grant


def test_coding_process_checks_prompt_origin(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    provider = 'openai:codex'
    prompt = 'PRIVATE_REVOKED_PROMPT_CONTENT'
    source = cloud.Source.text('document:private', 'passage', prompt)

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    async def grants():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        for scope in (cloud.repository_source(tmp_path), 'agent_environment'):
            assert (await request_grant(context, scope, provider))['status'] == 'granted'

    asyncio.run(grants())
    cloud.revoke(source.source_id, provider)
    runner = SimpleNamespace(run=lambda *args, **kwargs: sent.append(kwargs['prompt']))
    with cloud.request_sources((source,)):
        try:
            consent_run(runner, ['fake'], provider=provider, prompt=prompt, cwd=tmp_path)
        except AgentRefusal:
            pass
    assert sent == [], 'coding process dropped the revoked prompt source'
