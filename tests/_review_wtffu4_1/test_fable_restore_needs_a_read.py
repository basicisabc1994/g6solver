"""Finding 2: a reader that also refuses reads still tears the pair for good. No marker is written, so there is no
pending restore for the next access to finish.

The pending restore's record is written by ``_restore_retained`` (lines 506-536), and before it is written the
destination is read back twice, with nothing around either read:

* ``write_retained``'s handler reads ``current = _snapshot(target)`` (line 609) first thing in ``except OSError``;
* ``_restore_retained`` reads ``current = _snapshot(target)`` (line 513) before it makes a backup or a marker.

``_snapshot`` is ``read_bytes`` on both halves. A sharing refusal there (WinError 32, from a handle that shares no
reading; ``_sharing`` also lists 33, a byte-range lock's, which was not run here) leaves the handler as an
uncaught error: no retry, no restore, no backup, no marker. The destination keeps the new content
beside the old receipt, which no reader accepts, exactly as review 4's finding 1 left it: the run's state reads
"No report is available; the send count is unknown" (``G6_E_NO_CLEAN_REVIEW``) though nothing was sent, a task
binding reads ``G6_E_COMPLETION_TASK_UNREADABLE``, and where the refused save was a reservation the first reply
still says "This request was not sent. Retry this call."

Review 4's reader, with one difference. It opens the receipt as before (Python's ``open``), and once the save has
replaced the content it opens the content with no sharing at all (``CreateFile`` with share mode 0, as a backup or a
scanner may), and lets both go when the save has given up. The control is review 4's own reader, whose content
handle shares reading: the pending restore then works, and all four nodes pass.

Everything is on the real disk with real handles; nothing in G6 is replaced beyond the committed ``Session`` helper
(the scout and the repository reads).
"""
import _winapi
import json
import os
import threading
import time

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_fu3_repair2 import Session

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
HELD = pytest.mark.parametrize('exclusive', [False, True],
                               ids=['control-content-handle-shares-reading', 'content-handle-shares-nothing'])
GENERIC_READ, OPEN_EXISTING = 0x80000000, 3


class _Handle:
    def __init__(self, path, exclusive):
        self.stream = None if exclusive else open(path, 'rb')
        self.raw = _winapi.CreateFile(str(path), GENERIC_READ, 0, 0, OPEN_EXISTING, 0, 0) if exclusive else None

    def close(self):
        if self.stream is not None:
            self.stream.close()
        if self.raw is not None:
            _winapi.CloseHandle(self.raw)


def _while_read(work, content, *, exclusive):
    """Review 4's ``_while_read``: run ``work`` while another reader has the receipt open and opens the content as
    soon as the publication has replaced it; here the content handle shares reading or (``exclusive``) nothing."""
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
            while held is None and worker.is_alive():
                try:
                    held = _Handle(content, exclusive)
                except OSError:              # the publisher is replacing or reading it this instant
                    time.sleep(0.0005)
        finally:
            worker.join(60)                  # never leave the publisher running into another test
            if held is not None:
                held.close()
    assert not worker.is_alive() and done
    assert replaced, 'the publication never replaced the content, so there was nothing to restore'
    assert held is not None, 'the reader never got its content handle while the save was still trying'
    return done[0]


def _task(text):
    return json.dumps({'repo': 'r', 'task': text, 'base': 'b'}).encode('utf-8')


@HELD
def test_a_refused_state_publication_leaves_the_prior_state_readable(tmp_path, exclusive):
    run = store.home_path('completion_loop', 'a' * 32)
    store.mkdir(run, parents=True, exist_ok=True)
    loop._save(run, {loop.ATTEMPTED: 0, 'kept': 'prior'})

    def save():
        state = loop._load(run)
        state['kept'] = 'next'
        loop._save(run, state)

    outcome, error = _while_read(save, run / 'state.json', exclusive=exclusive)
    assert outcome == 'raised' and isinstance(error, PermissionError), 'that save is refused, and may say so'
    state = loop._load(run)        # every file is free again
    assert state.readable, (f'the refused save left a pair no reader accepts, and no record to restore it from: '
                            f'{sorted(p.name for p in run.iterdir())}')
    assert state['kept'] == 'prior'
    loop._save(run, state)


@HELD
def test_a_refused_task_publication_leaves_the_prior_binding_readable(tmp_path, exclusive):
    session = store.home_path('completion_loop', 'b' * 32)
    store.mkdir(session, parents=True, exist_ok=True)
    target = session / 'task.json'
    with store.task_publication(target):
        store.write_retained(target, _task('old task'))

    def publish():
        with store.task_publication(target):
            store.write_retained(target, _task('old task\n\nnew task'))

    outcome, error = _while_read(publish, target, exclusive=exclusive)
    assert outcome == 'raised' and isinstance(error, PermissionError)
    try:
        bound = store.read_task(target)['task']
    except store.TaskUnreadable:
        bound = None
    assert bound == 'old task', f'the refused publication left: {sorted(p.name for p in session.iterdir())}'


@HELD
def test_a_stop_refused_for_sharing_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file, exclusive):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-exclusive-reader')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    outcome, first = _while_read(lambda: hook.on_stop(world.event), world.folder / 'state.json', exclusive=exclusive)
    assert outcome == 'returned'
    assert loop.RESERVATION_UNSAVED in first['systemMessage'] and 'Retry this call' in first['systemMessage']
    assert world.reviewed == []
    # Every file is free again. The person stops once more, as the reply said to.
    later = hook.on_stop(world.event)
    assert world.reviewed, f'the retry the reply asked for can never review: {later["systemMessage"]}'
    assert 'Report for task: old task' in later['systemMessage']


@HELD
def test_an_mcp_call_refused_for_sharing_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file, exclusive):
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:fable-exclusive-reader')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})

    def call():
        return review_loop.step(str(world.repo), 'old task', 'fable-exclusive-reader', 'base')

    outcome, first = _while_read(call, world.folder / 'state.json', exclusive=exclusive)
    assert outcome == 'returned'
    assert first.get('code') == loop.RESERVATION_UNSAVED and 'The request reservation could not be saved' in first['error']
    assert world.reviewed == []
    later = call()
    assert world.reviewed and later.get('code') != loop.NO_CLEAN_REVIEW, (
        f'the retry the reply asked for is told the run cannot be read: {later.get("code")}; '
        f'requests_sent={later.get("requests_sent", "absent")!r}; {later.get("error", "")[-150:]}')
