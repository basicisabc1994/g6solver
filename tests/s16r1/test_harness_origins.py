# SPDX-License-Identifier: Apache-2.0
"""Package text is operational only while its producer reads the package."""
from contextlib import contextmanager
import json

from g6.core import cloud_consent as cloud, host_models
from g6.core.llm import LLMError
from g6.harness import completion_loop as loop
from tests.s16r1.test_roots import grant


def test_custom_harness_without_training_is_a_source(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    data = json.loads(loop.HARNESS.read_text(encoding='utf-8'))
    data.update(scout='PRIVATE_CUSTOM_HARNESS_INSTRUCTION', trained={})
    path = tmp_path / 'custom-harness.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    shipped = loop.harness(path)
    cwd, where, files = (tmp_path / name for name in ('repository', 'work', 'files'))
    for folder in (cwd, where, files):
        folder.mkdir()
    for scope in (cloud.repository_source(cwd), cloud.repository_source(where), 'model_inputs', 'agent_environment'):
        grant(scope, 'anthropic:claude_code')
    @contextmanager
    def folders():
        yield tmp_path, files
    sent = []
    monkeypatch.setattr(host_models, '_folders', folders)
    monkeypatch.setattr(host_models, '_environment', lambda *args: {})
    monkeypatch.setattr(loop.ToolSession, 'locate', lambda self: 'fake')
    monkeypatch.setattr(loop.ToolSession, '_reply', lambda *args: 'fake reply')
    monkeypatch.setattr(host_models, '_run', lambda argv, text, *args: sent.append(text) or (0, '{}', ''))
    with cloud.request_sources(shipped.sources):
        try:
            loop.ToolSession().run(shipped.scout, cwd, where, 1)
        except LLMError:
            pass
    assert sent == [], 'a custom untrained harness was treated as package text'
    assert cloud.source_id(str(path)) in {source.source_id for source in shipped.sources}
    grant(cloud.source_id(str(path)), 'anthropic:claude_code')
    with cloud.request_sources(shipped.sources):
        loop.ToolSession().run(shipped.scout, cwd, where, 1)
    assert sent == ['PRIVATE_CUSTOM_HARNESS_INSTRUCTION']
