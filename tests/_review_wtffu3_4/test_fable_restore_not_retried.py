"""Finding 1 (important): a publication that runs out its sharing wait tries its restore once, and a restore that
meets one more sharing refusal leaves the pair torn. No process dies, and the reply says "Retry this call".

``write_retained`` retries the whole publication for ``PUBLICATION_WAIT`` (0.3 s) on a sharing refusal and then calls
``_restore_retained``, which is not retried: its ``replace(spare, item)`` runs once, and its error is raised in place
of the publication's. The destination then holds the new content beside the old receipt, which no reader accepts:
the run's state reads as unreadable, so its loop is over (``G6_E_NO_CLEAN_REVIEW``, "the send count is unknown",
though nothing was sent), and a task binding reads as ``TASK_UNREADABLE``. The refused call's own reply still says
"This request was not sent. Retry this call."

Everything here is on the real disk with real open handles (Python's own ``open``, which shares no delete, as a
backup, an indexer or an editor may). Nothing in G6 is replaced except, in the hook and MCP nodes, the scout and the
repository reads the committed ``Session`` helper stands in, and, in the one node that says so, the swap to the
storage module of d56384c3, read from git. The reader opens the receipt before the save and the content once the
save has replaced it, and lets both go when the save has given up: about a third of a second. The control holds the
receipt alone for the same time: the restore then works and the run goes on.

Seen without choreography too (tests/_review_wtffu3_4/reader_walk.py, not a test: it is not deterministic): one
reader walking the folder, opening each file for at most 20 ms, tore a task binding at its 58th publication and a
run's state at its 219th; four other runs of 30 to 40 seconds (about 200 to 350 publications each) tore nothing.

The retry and the restore are e3ca7648's, the first commit of this branch; repair 3 only passes the selected spares
to the restore, and d56384c3's storage tears the same way (the last node). The branch's parent (9d0a683f) had no
retry and tore on the first refused receipt replace, so this is what is left of that defect, not a regression. It
is not the bound producer's interruption window: nothing is interrupted, and the writer itself goes on to report.
"""
import importlib.util
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_fu3_repair2 import Session

TREE = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
HELD = pytest.mark.parametrize('also_content', [False, True], ids=['control-receipt-held', 'content-held-at-the-restore'])


def _while_read(work, content, *, also_content):
    """Run ``work`` while another reader has the receipt open, and (``also_content``) opens the content as soon as
    the publication has replaced it. Returns what ``work`` returned or raised."""
    receipt, done = content.with_suffix('.sha256'), []

    def call():
        try:
            done.append(('returned', work()))
        except BaseException as exc:  # noqa: BLE001 - handed to the test
            done.append(('raised', exc))

    before, held, replaced = os.stat(content).st_ino, None, False
    worker = threading.Thread(target=call, name='publisher')
    with open(receipt, 'rb'):
        worker.start()
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and worker.is_alive() and os.stat(content).st_ino == before:
                time.sleep(0.0005)
            replaced = os.stat(content).st_ino != before
            while also_content and held is None and worker.is_alive():
                try:
                    held = open(content, 'rb')
                except PermissionError:      # the publisher is replacing it this instant
                    time.sleep(0.0005)
        finally:
            worker.join(60)                  # never leave the publisher running into another test
            if held is not None:
                held.close()
    assert not worker.is_alive() and done
    assert replaced, 'the publication never replaced the content, so there was nothing to restore'
    return done[0]


def _task(text):
    return json.dumps({'repo': 'r', 'task': text, 'base': 'b'}).encode('utf-8')


def _saved_run(name):
    run = store.home_path('completion_loop', name * 32)
    store.mkdir(run, parents=True, exist_ok=True)
    loop._save(run, {loop.ATTEMPTED: 0, 'kept': 'prior'})

    def save():
        state = loop._load(run)
        state['kept'] = 'next'
        loop._save(run, state)

    return run, save


@HELD
def test_a_refused_state_publication_leaves_the_prior_state_readable(tmp_path, also_content):
    run, save = _saved_run('a')
    outcome, error = _while_read(save, run / 'state.json', also_content=also_content)
    assert outcome == 'raised' and isinstance(error, PermissionError), 'that save is refused, and may say so'
    state = loop._load(run)        # every file is free again
    assert state.readable, f'the refused save left a pair no reader accepts: {sorted(p.name for p in run.iterdir())}'
    assert state['kept'] == 'prior'
    loop._save(run, state)


@HELD
def test_a_refused_task_publication_leaves_the_prior_binding_readable(tmp_path, also_content):
    session = store.home_path('completion_loop', 'b' * 32)
    store.mkdir(session, parents=True, exist_ok=True)
    target = session / 'task.json'
    with store.task_publication(target):
        store.write_retained(target, _task('old task'))

    def publish():
        with store.task_publication(target):
            store.write_retained(target, _task('old task\n\nnew task'))

    outcome, error = _while_read(publish, target, also_content=also_content)
    assert outcome == 'raised' and isinstance(error, PermissionError)
    try:
        bound = store.read_task(target)['task']
    except store.TaskUnreadable:
        bound = None
    assert bound == 'old task', f'the refused publication left: {sorted(p.name for p in session.iterdir())}'


@HELD
def test_a_stop_refused_for_sharing_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file, also_content):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-restore-once')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    outcome, first = _while_read(lambda: hook.on_stop(world.event), world.folder / 'state.json',
                                 also_content=also_content)
    assert outcome == 'returned'
    assert loop.RESERVATION_UNSAVED in first['systemMessage'] and 'Retry this call' in first['systemMessage']
    assert world.reviewed == []
    # Every file is free again. The person stops once more, as the reply said to.
    later = hook.on_stop(world.event)
    assert world.reviewed, f'the retry the reply asked for can never review: {later["systemMessage"]}'
    assert 'Report for task: old task' in later['systemMessage']


@HELD
def test_an_mcp_call_refused_for_sharing_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file, also_content):
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:fable-restore-once')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})

    def call():
        return review_loop.step(str(world.repo), 'old task', 'fable-restore-once', 'base')

    outcome, first = _while_read(call, world.folder / 'state.json', also_content=also_content)
    assert outcome == 'returned'
    # Its words end "This request was not sent. Retry this call." (cut at 600 characters under a long test path).
    assert first.get('code') == loop.RESERVATION_UNSAVED and 'The request reservation could not be saved' in first['error']
    assert world.reviewed == []
    later = call()
    assert world.reviewed and later.get('code') != loop.NO_CLEAN_REVIEW, (
        f'the retry the reply asked for is told the run cannot be read: {later.get("code")}; '
        f'requests_sent={later.get("requests_sent", "absent")!r}; {later.get("error", "")[-150:]}')


def test_control_the_storage_before_repair_3_tore_the_same_way(tmp_path, monkeypatch):
    """Not a regression of d636f5ad: the same reader tears the same pair on d56384c3's storage module."""
    shown = subprocess.run(['git', 'show', 'd56384c3:src/g6/harness/_completion_storage.py'], cwd=TREE,
                           capture_output=True, check=False)
    if shown.returncode:
        pytest.skip('git cannot show d56384c3 here')
    spec = importlib.util.spec_from_loader('g6.harness._completion_storage_d56384c3', loader=None)
    parent = importlib.util.module_from_spec(spec)
    exec(compile(shown.stdout.decode('utf-8'), 'd56384c3:_completion_storage.py', 'exec'), parent.__dict__)
    monkeypatch.setattr(loop, 'store', parent)
    run, save = _saved_run('c')
    outcome, error = _while_read(save, run / 'state.json', also_content=True)
    assert outcome == 'raised' and isinstance(error, PermissionError)
    assert not loop._load(run).readable
