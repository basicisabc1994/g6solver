# SPDX-License-Identifier: Apache-2.0
"""Unclassified state tears stay unreadable; task readers never cache bound fields."""
import json
from contextlib import nullcontext

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from g6.workforce import repo_tests


@pytest.mark.parametrize('error_type', [PermissionError, OSError])
def test_unclassified_receipt_failure_keeps_run_count_unknown(tmp_path, monkeypatch, error_type):
    session = tmp_path / 'run'
    loop._save(session, {'attempted': 0}, sources=())
    real_replace = store.os.replace

    def interrupted(source, target):
        if target == session / 'state.sha256':
            raise error_type('unclassified receipt failure')
        return real_replace(source, target)

    monkeypatch.setattr(store.os, 'replace', interrupted)
    with pytest.raises(error_type, match='unclassified receipt failure'):
        loop._save(session, {'attempted': 1}, sources=())
    state = loop._load(session)
    assert not state.readable and state['code'] == loop.NO_CLEAN_REVIEW
    assert loop.attempted(state) is None


@pytest.mark.parametrize('reader', ['hook', 'mcp', 'recovered-hook'])
@pytest.mark.parametrize('damage', ['repo', 'base', 'task', 'missing_receipt'])
def test_successful_task_read_grants_no_cached_fields(tmp_path, monkeypatch, reader, damage):
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: tmp_path)
    original = hook._record(tmp_path, str(tmp_path), 'original task')
    target = tmp_path / 'task.json'
    if reader == 'recovered-hook':
        target = tmp_path / 'task-1.json'
        original = {**original, 'run': 1}
        store.write_retained(target, json.dumps(original).encode('utf-8'))

    def read():
        if reader == 'mcp':
            return review_loop._asked(str(tmp_path), 'original task', 'HEAD', tmp_path)
        return hook._task(tmp_path)

    assert read() == original
    if damage == 'missing_receipt':
        target.with_suffix('.sha256').unlink()
    else:
        target.write_text(json.dumps({**original, damage: 'unbound replacement'}), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for _ in range(2):
        with cloud.collect_sources() as capture:
            with pytest.raises(repo_tests.RepoRefused if reader == 'mcp' else store.TaskUnreadable) as refused:
                read()
            if reader == 'mcp':
                assert refused.value.code == 'G6_E_COMPLETION_TASK_UNREADABLE'
            else:
                assert 'G6_E_COMPLETION_TASK_UNREADABLE' in str(refused.value)
            assert cloud.REQUIRED not in str(refused.value)
            assert 'g6_cloud_grant' not in str(refused.value)
            assert cloud.UNKNOWN in capture.sources
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_prompt_append_failure_preserves_the_whole_task_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    monkeypatch.setattr(hook, 'folder', lambda *a: tmp_path)
    monkeypatch.setattr(hook, '_one_prompt', lambda *a: nullcontext())
    original = hook._record(tmp_path, str(tmp_path), 'original task')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    real_replace = store.os.replace

    def interrupted(source, target):
        if target == tmp_path / 'task.sha256':
            raise OSError('task append interrupted')
        return real_replace(source, target)

    monkeypatch.setattr(store.os, 'replace', interrupted)
    with pytest.raises(OSError, match='task append interrupted'):
        hook.on_prompt({'session_id': 'same-task', 'cwd': str(tmp_path), 'prompt': 'additional task'})
    assert hook._task(tmp_path) == original
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
