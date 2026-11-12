# SPDX-License-Identifier: Apache-2.0
"""Synthetic check receipts survive reduction; opaque diagnostics stay unknown."""
import time
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core import host_models
from g6.core.llm import LLMError
from g6.harness import completion_loop as loop, verification_search as vs


@pytest.mark.parametrize('origin', ['known', 'unknown', 'revoked'])
def test_read_preserves_check_receipts(tmp_path, monkeypatch, accepted_cloud_dialog, origin):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(loop, '_loaded_elsewhere', lambda *args: '')
    monkeypatch.setattr(loop, '_in_repository', lambda *args: lambda value: value)
    source = cloud.UNKNOWN if origin == 'unknown' else cloud.Source.text(
        'fixture:' + origin, 'diagnostic', 'SYNTHETIC_CHECK_OUTPUT')
    if origin != 'unknown':
        accepted_cloud_dialog(source.source_id, 'openai:codex', text='synthetic diagnostic')
    if origin == 'revoked':
        cloud.revoke(source.source_id, 'openai:codex')
    run = {'verdict': 'failed', 'said': 'SYNTHETIC_CHECK_OUTPUT',
           'test_outcomes': {'test_x': {'call': 'failed'}}, 'failure_messages': {'test_x': 'assert False'}}
    with cloud.collect_sources():
        cloud.bind_output(run, (source,))
        result = loop._read(SimpleNamespace(repo=tmp_path), [run])
        refs = cloud.output_sources(result) or ()
        assert source in refs, 'check reduction dropped its origin receipt'
        if origin == 'known':
            with cloud.admission('openai:codex', refs, cloud.sha256('result')):
                pass
        else:
            with pytest.raises(LLMError, match=cloud.REQUIRED):
                with cloud.admission('openai:codex', refs, cloud.sha256('result')):
                    pytest.fail('reduction authorized unshared diagnostic bytes')


@pytest.mark.parametrize('recorded', [False, True])
def test_run_set_preserves_receipts_and_leaves_opaque_output_unknown(tmp_path, monkeypatch, recorded):
    source = cloud.Source.text('fixture:check', 'diagnostic', 'SYNTHETIC_CHECK_OUTPUT')
    run = {'verdict': 'failed', 'said': 'SYNTHETIC_CHECK_OUTPUT',
           'test_outcomes': {'test_x': {'call': 'failed'}}, 'failure_messages': {'test_x': 'assert False'}}
    monkeypatch.setattr(loop, '_checks', lambda *args: ([SimpleNamespace(id='x', source='test')], {'x': 'test_x'}, []))
    monkeypatch.setattr(loop, '_loaded_elsewhere', lambda *args: '')
    monkeypatch.setattr(loop, '_in_repository', lambda *args: lambda value: value)

    def check(*args, **kwargs):
        return cloud.bind_output(run, (source,)) if recorded else run

    monkeypatch.setattr(vs, '_run_check', check)
    with cloud.collect_sources():
        result = loop.run_set(SimpleNamespace(repo=tmp_path), tmp_path, at_base=False,
                              deadline=time.monotonic() + 10000)
        refs = cloud.output_sources(result)
        assert (source if recorded else cloud.UNKNOWN) in (refs or ()), 'run_set lost check provenance'


@pytest.mark.parametrize('origin', ['known', 'unknown', 'revoked'])
def test_retry_sends_only_diagnostics_with_complete_granted_receipts(
        tmp_path, monkeypatch, accepted_cloud_dialog, origin):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    marker = 'SYNTHETIC_RETRY_DIAGNOSTIC'
    source = cloud.Source.text('fixture:diagnostic', 'check', marker)
    for scope in (cloud.repository_source(tmp_path), cloud.input_source('task'), 'model_inputs', source.source_id):
        accepted_cloud_dialog(scope, 'openai:codex', text='synthetic critic input')
    if origin == 'revoked':
        cloud.revoke(source.source_id, 'openai:codex')
    monkeypatch.setattr(loop, '_pack', lambda *args: 'SYNTHETIC_PATCH')
    monkeypatch.setattr(loop, '_masked_pack', lambda *args: ('SYNTHETIC_PATCH', False))
    monkeypatch.setattr(host_models.CodexCaller, 'complete',
                        lambda self, system, user, **kwargs: sent.append(user) or 'VERDICT: none')
    reply = loop.run_critic(SimpleNamespace(repo=tmp_path, sources=('code.py',)), 'task',
                            host_models.CodexCaller(), 'system', tmp_path, told=marker,
                            patch='synthetic patch', system_sources=(),
                            told_sources=(cloud.UNKNOWN if origin == 'unknown' else source,))
    assert bool(sent) is (origin == 'known')
    if origin != 'known':
        assert cloud.REQUIRED in reply['error']
