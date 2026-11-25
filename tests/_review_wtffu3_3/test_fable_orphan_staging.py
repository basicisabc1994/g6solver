"""Finding 3 (limit): a staging copy whose owner died before its cleanup is never swept.

Repair 2's sweep takes only what a ``.cleanup`` record (or this process's memory) names. A prompt hook that
is killed while it prepares or publishes (a hook timeout, a closed terminal) leaves
``completion_staging/<uuid>/task.json.new`` with the person's whole prompt and no record, so no later write
of any session removes it. The control is the path the repair covers: a live writer whose cleanup met a real
sharing violation leaves a record, and the next home-mutex write removes both.

Real disk, real child process (started and killed here), real open handles; nothing is monkeypatched in the
storage layer.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook
from g6.workspace._coordination import Coordination
from tests.harness.test_completion_fu3_repair2 import Session

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='the windows are held open by Windows file sharing')

PROMPT_WRITER = '''
import json, sys
from pathlib import Path
from g6.harness import completion_hook as hook
event = json.loads(sys.argv[1])
hook._here = lambda cwd: (Path(event['cwd']), 'base')
hook._hint = lambda task: ''
hook.on_prompt(event)
'''
SECRET = 'second ask: the customer list is in notes/private.txt'


def _staged():
    root = store.home_path('completion_staging')
    return [item for item in root.iterdir() if item.is_dir()] if root.is_dir() else []


def _later_writes(world):
    """Every kind of later home-mutex write: a bare one, and another session's whole prompt and stop."""
    store.write_bytes(store.home_path('next-write'), b'next')
    other = {**world.event, 'session_id': world.event['session_id'] + '-another-session'}
    hook.on_prompt({**other, 'prompt': 'another task'})
    hook.on_stop(other)
    store.write_bytes(store.home_path('next-write'), b'again')


def test_a_killed_prompt_hooks_staging_copy_is_swept_by_later_writes(tmp_path, monkeypatch, key_file):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-orphan-staging')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    assert _staged() == []
    event, spare = json.dumps({**world.event, 'prompt': SECRET}), world.folder / 'task.json.new'
    for _ in range(8):
        with open(world.folder / 'task.json', 'rb'):        # its replace waits here, in the sharing retry
            child = subprocess.Popen([sys.executable, '-c', PROMPT_WRITER, event], env=os.environ.copy(),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and child.poll() is None and not spare.exists():
                time.sleep(0.0005)
            child.kill()        # a process this test started
            child.communicate(timeout=30)
        if _staged():
            break
    left = _staged()
    assert len(left) == 1, 'the killed hook was never caught with its staging copy'
    assert SECRET in json.loads((left[0] / 'task.json.new').read_bytes())['task']
    assert not list(left[0].parent.glob('*.cleanup'))
    _later_writes(world)
    assert not left[0].exists(), (
        f'the dead hook\'s copy of the prompt is still at completion_staging/{left[0].name}/task.json.new '
        'after another session\'s prompt and stop; no record names it, so no sweep ever will')


def test_control_a_live_writers_failed_cleanup_is_swept_by_the_next_write(tmp_path, monkeypatch, key_file):
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    world = Session(tmp_path, monkeypatch, key_file, 'fable-recorded-staging')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    errors, handle = [], None

    def publisher():
        try:
            hook.on_prompt({**world.event, 'prompt': SECRET})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=publisher, name='prompt-publisher')
    root = store.home_path('completion_staging')
    try:
        with Coordination(store.home_path()).mutex():
            worker.start()
            deadline, staged = time.monotonic() + 10, []
            while time.monotonic() < deadline and not staged:
                staged = list(root.glob('*/task.sha256.new'))
                time.sleep(0.005)
            assert len(staged) == 1
            handle = open(staged[0], 'rb')      # the publisher's own cleanup will meet a sharing violation
        worker.join(30)
    finally:
        worker.join(30)
        if handle is not None:
            handle.close()
    assert errors == [] and world.bound() == 'old task\n\n' + SECRET
    left = staged[0].parent
    assert left.is_dir() and left.with_name(left.name + '.cleanup').is_file()
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})       # as in a later process: only the disk record remains
    store.write_bytes(store.home_path('next-write'), b'next')
    assert not left.exists() and not left.with_name(left.name + '.cleanup').exists()
