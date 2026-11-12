import asyncio
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from g6.core import cloud_consent as cloud, host_models, llm
from g6.harness.completion_loop import ToolSession
from g6.mcp.cloud_consent import request_grant


def test_scout_checks_the_add_dir_it_exposes(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    cwd, additional, files = (tmp_path / name for name in ('repository', 'earlier-scout', 'files'))
    for folder in (cwd, additional, files):
        folder.mkdir()
    (additional / 'context.txt').write_text('PRIVATE_REVOKED_ADD_DIR_CONTENT', encoding='utf-8')
    provider = 'anthropic:claude_code'
    sent = []

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    async def grants():
        context = SimpleNamespace(session=Dialog(), request_id='grant')
        for scope in (cloud.repository_source(cwd), 'model_inputs', 'agent_environment'):
            assert (await request_grant(context, scope, provider))['status'] == 'granted'

    asyncio.run(grants())
    cloud.revoke(cloud.repository_source(additional), provider)

    @contextmanager
    def folders():
        yield cwd, files

    def fake_process(argv, prompt, *args):
        exposed = Path(argv[argv.index('--add-dir') + 1])
        sent.append((exposed / 'context.txt').read_text(encoding='utf-8'))
        return 0, '{}', ''

    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(host_models, '_run', fake_process)
    monkeypatch.setattr(ToolSession, 'locate', lambda self: 'fake-scout')
    monkeypatch.setattr(ToolSession, '_reply', lambda *args: 'fake reply')
    try:
        ToolSession().run('Read context.txt in the additional directory', cwd, additional, 1)
    except llm.LLMError:
        pass
    assert sent == [], 'scout exposed a revoked additional directory to its file tools'
