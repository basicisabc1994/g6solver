"""Finding 1 (must-close): a spare a dead or interrupted writer left beside a readable publication now refuses every
later publication to that destination.

Repair 2 creates each spare exclusively (``_reserve_spare`` -> ``append_text(..., exclusive=True)``), so a
``state.json.new`` / ``task.json.new`` this attempt did not create raises ``FileExistsError``. Nothing ever
removes such a spare (no attempt owns it), so the refusal is permanent. The parent (6ee0192c) overwrote it
and went on. A spare is left by a writer that is killed between creating it and replacing the destination
(the loop's own docstrings promise a killed hook leaves no session stuck), and by a rollback whose
``unlink`` of its own spare meets a sharing violation.

Every scenario here is on the real disk. The killed writers are real child processes (started and killed
by this test); the held files are real open handles. Nothing is monkeypatched in the storage layer except,
in the controls, the swap to the parent commit's storage module, read from git with ``git show``, and, in
the one node that says so, a single failed write.

This is not the bound producer's interruption window (a kill between its two replaces, which leaves a pair
no reader accepts). Here the destination pair is whole and readable throughout, the parent handled the same
interruption, and the refusal comes from ``_completion_storage._reserve_spare``.
"""
import errno
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_fu3_repair2 import Session

TREE = Path(__file__).resolve().parents[2]
PARENT = '6ee0192c'
windows_only = pytest.mark.skipif(os.name != 'nt', reason='the publication window is held open by Windows file sharing')

STATE_WRITER = '''
import sys
from pathlib import Path
from g6.harness import completion_loop as loop
if sys.argv[2] == 'parent':
    import importlib.util, subprocess
    shown = subprocess.run(['git', 'show', sys.argv[3] + ':src/g6/harness/_completion_storage.py'], cwd=sys.argv[4],
                           capture_output=True, check=True)
    spec = importlib.util.spec_from_loader('g6.harness._completion_storage_parent', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(shown.stdout.decode('utf-8'), 'parent-storage', 'exec'), module.__dict__)
    loop.store = module
folder = Path(sys.argv[1])
state = loop._load(folder)
assert state and state.readable
loop._save(folder, state)
'''

PROMPT_WRITER = '''
import json, sys
from pathlib import Path
from g6.harness import completion_hook as hook, completion_loop as loop
if sys.argv[2] == 'parent':
    import importlib.util, subprocess
    shown = subprocess.run(['git', 'show', sys.argv[3] + ':src/g6/harness/_completion_storage.py'], cwd=sys.argv[4],
                           capture_output=True, check=True)
    spec = importlib.util.spec_from_loader('g6.harness._completion_storage_parent', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(shown.stdout.decode('utf-8'), 'parent-storage', 'exec'), module.__dict__)
    hook.store = loop.store = module
event = json.loads(sys.argv[1])
hook._here = lambda cwd: (Path(event['cwd']), 'base')
hook._hint = lambda task: ''
hook.on_prompt(event)
'''


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


def _use(monkeypatch, code):
    """Run the loop, the hook and the tool on HEAD's storage, or on the parent's (the control)."""
    if code == 'parent':
        parent = _parent_store()
        for module in (hook, loop, review_loop):
            monkeypatch.setattr(module, 'store', parent)


def _killed_mid_publication(script, args, destination, spare):
    """Start a real writer, keep its destination open so its replace waits in the sharing retry, and kill it
    there, as a hook's timeout or a closed terminal does. Returns what the dead writer left."""
    for _ in range(8):
        with open(destination, 'rb'):
            child = subprocess.Popen([sys.executable, '-c', script, *args], env=os.environ.copy(),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and child.poll() is None and not spare.exists():
                    time.sleep(0.0005)
                child.kill()        # a process this test started
            finally:
                _, err = child.communicate(timeout=30)
        if spare.exists():
            return
        assert b'Error' not in err or b'PermissionError' in err, err.decode('utf-8', 'replace')
    pytest.fail('the writer was never caught inside its publication window')


# --- the run's state ---------------------------------------------------------------------------------------


@windows_only
@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_a_stop_after_a_killed_state_writer_still_reviews(tmp_path, monkeypatch, key_file, code):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-killed-state')
    _use(monkeypatch, code)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})       # the run's first save, as any step makes it
    _killed_mid_publication(STATE_WRITER, [str(world.folder), code, PARENT, str(TREE)],
                            world.folder / 'state.json', world.folder / 'state.json.new')
    assert loop._load(world.folder).readable, 'the retained state itself is intact and readable'
    said = [hook.on_stop(world.event) for _ in range(2)]
    assert world.reviewed, f'no stop after the killed writer ever reviewed the work: {said[-1]}'
    assert 'Report for task: old task' in said[0]['systemMessage']


@windows_only
@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_mcp_retry_after_a_killed_state_writer_is_not_refused_forever(tmp_path, monkeypatch, key_file, code):
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:fable-killed-state')
    _use(monkeypatch, code)
    first = review_loop.step(str(world.repo), 'old task', 'fable-killed-state', 'base')
    assert first['action'] == loop.STOP and first.get('code') != loop.RESERVATION_UNSAVED
    folder = hook.folder('mcp:fable-killed-state-2')
    hook.store.mkdir(folder, parents=True, exist_ok=True)
    loop._save(folder, {loop.ATTEMPTED: 0})
    _killed_mid_publication(STATE_WRITER, [str(folder), code, PARENT, str(TREE)],
                            folder / 'state.json', folder / 'state.json.new')
    replies = [review_loop.step(str(world.repo), 'old task', 'fable-killed-state-2', 'base') for _ in range(3)]
    codes = [reply.get('code') for reply in replies]
    assert loop.RESERVATION_UNSAVED not in codes, (
        f'every retry is told "Retry this call" and refused again: {codes}; {replies[-1].get("error", "")[-120:]}')


@windows_only
@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_a_spare_held_through_the_rollback_does_not_end_the_run(tmp_path, monkeypatch, key_file, code):
    """No process dies here. Another program has the run's two files open for half a second (a backup, an
    editor, a scanner without delete sharing): the publication's wait runs out, its rollback cannot unlink its
    own spare, and that call is honestly refused. Once the files are free again the run must go on."""
    world = Session(tmp_path, monkeypatch, key_file, 'fable-held-spare')
    _use(monkeypatch, code)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    spare, said = world.folder / 'state.json.new', []
    worker = threading.Thread(target=lambda: said.append(hook.on_stop(world.event)), name='stop-hook')
    held = open(world.folder / 'state.json', 'rb')
    try:
        worker.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not spare.exists():
            time.sleep(0.0005)
        with open(spare, 'rb'):
            held.close()
            worker.join(30)
    finally:
        held.close()
        worker.join(30)
    assert not worker.is_alive() and said
    assert loop.RESERVATION_UNSAVED in said[0]['systemMessage'], 'the interrupted call itself is refused, and says so'
    assert world.reviewed == []
    assert loop._load(world.folder).readable
    # Every file is free again. The person stops once more.
    later = hook.on_stop(world.event)
    assert world.reviewed, f'the run never recovers from one refused save: {later}'
    assert 'Report for task: old task' in later['systemMessage']


@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_one_failed_state_write_does_not_end_the_run(tmp_path, monkeypatch, key_file, code):
    """The one injected fault of this file, on real files: the disk is full for a single write of the state's
    spare (nine bytes arrive). A run-owned write that fails for anything but sharing is not rolled back, so
    the spare this attempt created stays. With room on the disk again, the run must go on."""
    world = Session(tmp_path, monkeypatch, key_file, 'fable-disk-full-once')
    _use(monkeypatch, code)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    real, failed = Path.write_bytes, []

    def full_once(item, body):
        if item.name == 'state.json.new' and item.parent.name == world.folder.name and not failed:
            failed.append(item)
            real(item, body[:9])
            raise OSError(errno.ENOSPC, 'No space left on device')
        return real(item, body)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'write_bytes', full_once)
        first = hook.on_stop(world.event)
    assert failed and loop.RESERVATION_UNSAVED in first['systemMessage'], 'the full disk refuses that call, honestly'
    assert world.reviewed == [] and loop._load(world.folder).readable
    later = hook.on_stop(world.event)
    assert world.reviewed, f'the run never recovers from one failed write: {later}'
    assert 'Report for task: old task' in later['systemMessage']


# --- the session's task ------------------------------------------------------------------------------------


@windows_only
@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_prompts_after_a_killed_prompt_hook_still_reach_the_task(tmp_path, monkeypatch, key_file, code):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-killed-prompt')
    _use(monkeypatch, code)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    event = json.dumps({**world.event, 'prompt': 'second ask'})
    _killed_mid_publication(PROMPT_WRITER, [event, code, PARENT, str(TREE)],
                            world.folder / 'task.json', world.folder / 'task.json.new')
    assert world.bound() == 'old task', 'the killed hook published nothing, and the binding is readable'
    errors = []
    try:
        hook.on_prompt({**world.event, 'prompt': 'new task'})
    except OSError as exc:
        errors.append(exc)
    said = hook.on_stop(world.event)
    assert errors == [], f'the next prompt hook failed, and the stop then reviewed {world.reviewed[-1:]!r}: {errors[0]!r}'
    assert world.bound() == 'old task\n\nnew task'
    assert 'Report for task: old task\n\nnew task' in said['systemMessage']


@windows_only
@pytest.mark.parametrize('code', ['parent', 'head'], ids=['control-parent-6ee0192c', 'head'])
def test_the_prompt_after_an_ended_loop_arms_the_loop_the_stop_promised(tmp_path, monkeypatch, key_file, code):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-killed-rearm')
    _use(monkeypatch, code)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    event = json.dumps({**world.event, 'prompt': 'second ask'})
    _killed_mid_publication(PROMPT_WRITER, [event, code, PARENT, str(TREE)],
                            world.folder / 'task.json', world.folder / 'task.json.new')
    assert 'Report for task: old task' in hook.on_stop(world.event)['systemMessage']
    promised = hook.on_stop(world.event)['systemMessage']
    assert 'Your next prompt starts a new loop' in promised
    armed = []
    for prompt in ('next task', 'next task again'):
        try:
            hook.on_prompt({**world.event, 'prompt': prompt})
            armed.append(prompt)
        except OSError as exc:
            armed.append(type(exc).__name__)
    world.reviewed.clear()
    said = hook.on_stop(world.event)
    assert world.reviewed, f'no prompt armed a new loop ({armed}); the stop still says: {said["systemMessage"]}'
    assert 'next task' in world.reviewed[-1]


def test_control_without_a_leftover_spare_head_extends_and_reviews(tmp_path, monkeypatch, key_file):
    """The same session on HEAD with no interrupted writer: the hunt above is the spare, not the harness."""
    world = Session(tmp_path, monkeypatch, key_file, 'fable-no-spare')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    hook.on_prompt({**world.event, 'prompt': 'new task'})
    assert world.bound() == 'old task\n\nnew task'
    assert 'Report for task: old task\n\nnew task' in hook.on_stop(world.event)['systemMessage']
    hook.on_prompt({**world.event, 'prompt': 'next task'})
    world.reviewed.clear()
    hook.on_stop(world.event)
    assert world.reviewed and 'next task' in world.reviewed[-1]
    assert not any(item.name.endswith('.new') for item in world.folder.iterdir())
