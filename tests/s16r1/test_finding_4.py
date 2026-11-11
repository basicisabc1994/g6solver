import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

from g6.core import cloud_consent as cloud, host_models, llm
from g6.harness.completion_loop import ToolSession
from g6.mcp.cloud_consent import request_grant


def test_scout_checks_retained_prompt_origin(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    provider = 'anthropic:claude_code'
    prompt = 'PRIVATE_REVOKED_SCOUT_CONTEXT'
    source = cloud.Source.text('document:private', 'passage', prompt)

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    async def grants():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        for scope in (cloud.repository_source(tmp_path), 'model_inputs', 'agent_environment'):
            assert (await request_grant(context, scope, provider))['status'] == 'granted'

    asyncio.run(grants())
    cloud.revoke(source.source_id, provider)
    files = tmp_path / 'files'
    files.mkdir()

    @contextmanager
    def folders():
        yield tmp_path, files

    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(host_models, '_run', lambda argv, text, *args: sent.append(text) or (0, '{}', ''))
    monkeypatch.setattr(ToolSession, 'locate', lambda self: 'fake-scout')
    monkeypatch.setattr(ToolSession, '_reply', lambda *args: 'fake reply')
    with cloud.request_sources((source,)):
        try:
            ToolSession().run(prompt, tmp_path, tmp_path, 1)
        except llm.LLMError:
            pass
    assert sent == [], 'scout dropped the revoked prompt source'
