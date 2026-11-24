# SPDX-License-Identifier: Apache-2.0
"""Task rollback must preserve the two-positional-argument retained-writer seam."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook
from g6.mcp import review_loop
from g6.workforce import repo_tests


@pytest.mark.parametrize('producer', ['record', 'replace-record', 'mcp', 'recover', 'rearm', 'append'])
@pytest.mark.parametrize('failure', ['none', 'before-content', 'spares', 'content'])
def test_task_producers_keep_two_argument_writer_and_rollback(tmp_path, monkeypatch, producer, failure):
    session = tmp_path / 'session'
    session.mkdir()
    monkeypatch.setattr(hook, '_here', lambda *a: (tmp_path, 'base'))
    monkeypatch.setattr(hook, 'folder', lambda *a: session)
    monkeypatch.setattr(hook, 'current', lambda *a: tmp_path / 'run')
    monkeypatch.setattr(hook, '_one_prompt', lambda *a: nullcontext())
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda *a: tmp_path)
    monkeypatch.setattr(repo_tests, 'change', lambda *a: SimpleNamespace(base_commit='base'))
    if producer in ('replace-record', 'recover', 'rearm', 'append'):
        hook._record(session, str(tmp_path), 'original task')
    before = {p.name: p.read_bytes() for p in session.iterdir()}
    event = {'session_id': 'same-task', 'cwd': str(tmp_path), 'prompt': 'fresh task'}
    real_write = store.write_retained

    def writer(target, body):
        if failure == 'none' or target.stem == 'state':
            return real_write(target, body)
        if failure == 'spares':
            store.write_bytes(target.with_name(target.name + '.new'), body)
            receipt = target.with_suffix('.sha256')
            store.write_bytes(receipt.with_name(receipt.name + '.new'), b'incomplete receipt')
        elif failure == 'content':
            store.write_bytes(target, body)
        raise OSError('task writer interrupted')

    def publish():
        if producer in ('record', 'replace-record'):
            return hook._record(session, str(tmp_path), 'fresh task')
        if producer == 'mcp':
            return review_loop._asked(str(tmp_path), 'fresh task', 'HEAD', session)
        if producer == 'recover':
            return hook._recover_task(event, 'fresh task')
        if producer == 'rearm':
            return hook._again(event, hook._task(session), {'done': True}, 'fresh task')
        return hook.on_prompt(event)

    monkeypatch.setattr(store, 'write_retained', writer)
    if failure == 'none':
        publish()
        asked = hook._task(session)
        assert asked['task'] == ('original task\n\nfresh task' if producer in ('rearm', 'append') else 'fresh task')
        assert asked['repo'] == str(tmp_path) and asked['base'] == 'base'
    else:
        with pytest.raises(OSError, match='task writer interrupted'):
            publish()
        assert {p.name: p.read_bytes() for p in session.iterdir()} == before
        if before:
            assert hook._task(session)['task'] == 'original task'
        else:
            assert not store.task_present(session / 'task.json')
