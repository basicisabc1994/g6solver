# SPDX-License-Identifier: Apache-2.0
"""The before-fix scout entry carries its task and retained state origins."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, consent_callers, host_models
from g6.core.llm import LLMError
from g6.harness import completion_loop as loop
from tests.host_stand_in import stand_in_hosts
from tests.s16r1.test_roots import grant


@pytest.mark.parametrize('missing', ['retained', 'task', 'unknown'])
def test_prepare_admits_every_origin_before_entering_the_scout(tmp_path, monkeypatch, missing):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    marker, sent = 'PRIVATE_BEFORE_FIX_CONTEXT', []
    saved = []
    observed = cloud.Source.text('document:scout-read', 'file', marker)
    carried = cloud.UNKNOWN if missing == 'unknown' else cloud.Source.text('document:retained', 'passage', marker)
    state = {'sets': {}, 'consent_sources': [asdict(carried)]}
    for source in ('model_inputs', cloud.repository_source(tmp_path)):
        grant(source)
    if missing != 'task':
        grant(cloud.input_source(marker))
    if missing != 'retained':
        grant(carried.source_id)
    else:
        cloud.revoke(carried.source_id, 'openai:codex')
    monkeypatch.setattr(loop, '_before_fix', lambda: True)
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, '_git', lambda *args: b'mod.py\0')
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *args: SimpleNamespace(repo=tmp_path))
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *args: '')
    monkeypatch.setattr(loop, 'harness', lambda: SimpleNamespace(sources=()))
    monkeypatch.setattr(loop, '_load', lambda *args: state)
    def save(*args):
        frame = loop._SOURCE_STATE.get()
        saved.append(tuple(frame.sources) if frame is not None else None)
    monkeypatch.setattr(loop, '_save', save)
    monkeypatch.setattr(loop, '_told', lambda *args: '')
    stand_in_hosts(monkeypatch, lambda *args: sent.append(marker) or 'fake')
    def scouts(*args):
        consent_callers.guard(host_models.CodexCaller()).complete('', marker)
        cloud.record_sources((observed,))
    monkeypatch.setattr(loop, '_scouts', scouts)
    with pytest.raises(LLMError, match=cloud.REQUIRED):
        loop.prepare(tmp_path, marker, 'HEAD', tmp_path / 'session', scout=lambda *args: '')
    assert sent == []
    if missing != 'unknown':
        grant(carried.source_id if missing == 'retained' else cloud.input_source(marker))
        loop.prepare(tmp_path, marker, 'HEAD', tmp_path / 'session', scout=lambda *args: '')
        assert sent == [marker]
        assert observed in saved[-1] and carried in saved[-1]
        assert cloud.input_source(marker) in {source.source_id for source in saved[-1]}
