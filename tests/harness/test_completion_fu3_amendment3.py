# SPDX-License-Identifier: Apache-2.0
"""Caught task publication failures leave the prior binding usable or the first call free."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import _completion_storage as store, completion_hook as hook
from g6.mcp import review_loop
from g6.workforce import repo_tests


@pytest.mark.parametrize('stage', ['content_spare', 'receipt_spare', 'content_replace', 'receipt_replace'])
@pytest.mark.parametrize('existing', [False, True], ids=['first-binding', 'prior-binding'])
def test_caught_task_publication_failure_leaves_a_usable_binding(tmp_path, monkeypatch, stage, existing):
    session = tmp_path / 'session'
    session.mkdir()
    target = session / 'task.json'
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: tmp_path)
    monkeypatch.setattr(repo_tests, 'change', lambda *a: SimpleNamespace(base_commit='base'))
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    if existing:
        hook._record(session, str(tmp_path), 'original task')
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    real_write, real_replace = Path.write_bytes, store.os.replace

    def write(path, body):
        if (stage == 'content_spare' and path.name == 'task.json.new'
                or stage == 'receipt_spare' and path.name == 'task.sha256.new'):
            raise OSError('task publication interrupted')
        return real_write(path, body)

    def replace(source, destination):
        if (stage == 'content_replace' and Path(destination) == target
                or stage == 'receipt_replace' and Path(destination) == target.with_suffix('.sha256')):
            raise OSError('task publication interrupted')
        return real_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'write_bytes', write)
        fault.setattr(store.os, 'replace', replace)
        with pytest.raises(OSError, match='task publication interrupted'):
            if existing:
                hook._record(session, str(tmp_path), 'replacement task')
            else:
                review_loop._asked(str(tmp_path), 'original task', 'HEAD', session)
    assert {p.name: p.read_bytes() for p in session.iterdir()} == before
    asked = review_loop._asked(str(tmp_path), 'original task', 'HEAD', session)
    assert asked == {'repo': str(tmp_path), 'task': 'original task', 'base': 'base'}
    assert hook._task(session) == asked


@pytest.mark.parametrize('reader', ['hook', 'mcp'])
@pytest.mark.parametrize('damage', ['repo', 'base', 'task', 'missing_receipt'])
def test_repeated_task_reads_refuse_damage_without_a_sharing_instruction(tmp_path, monkeypatch, reader, damage):
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: tmp_path)
    hook._record(tmp_path, str(tmp_path), 'original task')
    target = tmp_path / 'task.json'
    if damage == 'missing_receipt':
        target.with_suffix('.sha256').unlink()
    else:
        changed = json.loads(target.read_bytes())
        changed[damage] = 'untrusted replacement'
        target.write_text(json.dumps(changed), encoding='utf-8')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    for _ in range(2):
        with cloud.collect_sources() as capture:
            with pytest.raises(ValueError if reader == 'hook' else repo_tests.RepoRefused) as refused:
                if reader == 'hook':
                    hook._task(tmp_path)
                else:
                    review_loop._asked(str(tmp_path), 'original task', 'HEAD', tmp_path)
            if reader == 'hook':
                assert 'G6_E_COMPLETION_TASK_UNREADABLE' in str(refused.value)
            else:
                assert refused.value.code == 'G6_E_COMPLETION_TASK_UNREADABLE'
            assert cloud.REQUIRED not in str(refused.value)
            assert 'g6_cloud_grant' not in str(refused.value)
            assert cloud.UNKNOWN in capture.sources
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
