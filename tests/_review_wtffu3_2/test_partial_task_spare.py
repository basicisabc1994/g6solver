"""Finding 1: a caught first-binding write error leaves a spare the rollback does not own.

The spare a failed write leaves part written (a full disk, a quota, an I/O error after the
file was opened) is not byte-equal to the intended body, so repair 1's "clean up only owned
spares" keeps it. ``task_present`` then reports a binding, ``read_task`` cannot read one, and
the first call is no longer free. The parent (bab70a2d) removed both spares on this path.
"""
import errno
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop

TREE = Path(__file__).resolve().parents[2]
PARENT = 'bab70a2d'
STAGES = {
    # name: (spare the write fails on, bytes that reach the disk before the error; None: none, the file is never opened)
    'control-nothing-written': ('task.json.new', None),
    'content-spare-part-written': ('task.json.new', 9),
    'content-spare-opened-empty': ('task.json.new', 0),
    'receipt-spare-part-written': ('task.sha256.new', 9),
}


def _world(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(hook, '_here', lambda cwd: (repo, 'base'))
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda root: repo)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *args: SimpleNamespace(base_commit='base'))
    return repo


def _disk_fault(monkeypatch, session, stage):
    """The disk fills while one spare of this session's task is written."""
    name, reached = STAGES[stage]
    original = Path.write_bytes

    def write(path, body):
        if path.parent == session and path.name == name:
            if reached is not None:
                original(path, body[:reached])
            raise OSError(errno.ENOSPC, 'No space left on device')
        return original(path, body)

    monkeypatch.setattr(Path, 'write_bytes', write)


@pytest.mark.parametrize('stage', list(STAGES))
def test_caught_first_binding_error_leaves_the_mcp_call_free(tmp_path, monkeypatch, stage):
    repo = _world(tmp_path, monkeypatch)
    session = hook.folder('mcp:partial-spare')
    store.mkdir(session, parents=True, exist_ok=True, mode=0o700)
    with monkeypatch.context() as fault:
        _disk_fault(fault, session, stage)
        with pytest.raises(OSError, match='No space left'), loop._held(session):
            review_loop._asked(str(repo), 'first task', 'base', session)
    left = sorted(item.name for item in store.retained_paths(session / 'task.json') if item.exists())
    assert left == [], f'the caught write error left {left}, which reads as a binding nobody can read'
    with loop._held(session):
        asked = review_loop._asked(str(repo), 'first task', 'base', session)
    assert asked == {'repo': str(repo), 'task': 'first task', 'base': 'base'}
    assert hook._task(session) == asked


@pytest.mark.parametrize('stage', list(STAGES))
def test_stop_hook_still_binds_from_the_transcript_after_the_prompt_hook_failed(tmp_path, monkeypatch, stage):
    repo = _world(tmp_path, monkeypatch)
    monkeypatch.setattr(hook, '_hint', lambda task: '')
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'user', 'message': {'content': 'first task'}}) + '\n', encoding='utf-8')
    event = {'session_id': 'partial-spare-hook', 'cwd': str(repo), 'transcript_path': str(transcript)}
    session = hook.folder(event['session_id'])
    advanced = []

    def advance(repo, task, base, run, **kwargs):
        advanced.append(task)
        return loop.Step(loop.STOP, code=loop.NO_PYTHON, note=loop.NOTHING_CHANGED, ending='untested', call_ran=False)

    monkeypatch.setattr(loop, 'advance', advance)
    with monkeypatch.context() as fault:
        _disk_fault(fault, session, stage)
        with pytest.raises(OSError, match='No space left'):
            hook.on_prompt({**event, 'prompt': 'first task'})
    said = hook.on_stop(event)
    assert advanced == ['first task'], f'the Stop hook never reached the loop: {said}'
    assert store.TASK_UNREADABLE not in json.dumps(said)


def _parent_store():
    """The parent commit's storage module, read from git (a read only) and loaded beside HEAD's."""
    shown = subprocess.run(['git', 'show', f'{PARENT}:src/g6/harness/_completion_storage.py'], cwd=TREE,
                           capture_output=True, check=False)
    if shown.returncode:
        pytest.skip(f'git cannot show {PARENT} here')
    spec = importlib.util.spec_from_loader('g6.harness._completion_storage_parent', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(shown.stdout.decode('utf-8'), f'{PARENT}:_completion_storage.py', 'exec'), module.__dict__)
    return module


@pytest.mark.parametrize('stage', list(STAGES))
def test_control_the_parent_left_the_first_call_free(tmp_path, monkeypatch, stage):
    """The same four faults against the parent's storage: this is the behaviour repair 1 lost."""
    repo = _world(tmp_path, monkeypatch)
    parent = _parent_store()
    for module in (hook, loop, review_loop):
        monkeypatch.setattr(module, 'store', parent)
    session = hook.folder('mcp:partial-spare')
    parent.mkdir(session, parents=True, exist_ok=True, mode=0o700)
    with monkeypatch.context() as fault:
        _disk_fault(fault, session, stage)
        with pytest.raises(OSError, match='No space left'), loop._held(session):
            review_loop._asked(str(repo), 'first task', 'base', session)
    assert [item.name for item in parent.retained_paths(session / 'task.json') if item.exists()] == []
    with loop._held(session):
        assert review_loop._asked(str(repo), 'first task', 'base', session)['task'] == 'first task'
