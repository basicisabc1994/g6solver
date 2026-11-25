"""Finding 3: a refused FIRST publication is never recovered, though the reply says "Retry this call".

The pending restore (9f79bb79) recovers a destination that had a prior pair: review 4's four content-held nodes pass.
A destination with no prior pair (a session's first task binding, a run's first saved state, which is the reservation
of its first request) stays unreadable for good after one sharing refusal, by two mechanisms:

(a) the restore of "no prior pair" is finished too late to matter. ``store.task_present`` (``review_loop._asked`` line
    47, ``completion_hook._on_stop`` line 319, ``on_prompt`` line 270) and ``task_path`` (``read_task`` line 172) are
    read before ``task_publication`` finishes the restore; the restore then removes the content, and ``_read_task``
    reads a file that is gone: ``TaskUnreadable``;
(b) the refused attempt's own spares stay. ``_restore_retained`` reaches ``_remove_spares(owned_spares)`` (line 536)
    only when ``_finish_restore`` returns, the marker does not name them, and ``_finish_restore_record`` removes only
    its backups. ``_remove_spares`` itself stops at a spare the reader still holds. A leftover ``<name>.new`` is, to
    every reader, an interrupted publication (``task_present`` through ``retained_paths``;
    ``_read_publication_bytes`` line 1148), and the one sweep of orphan spares (``_recover_spares``) runs only inside
    ``write_retained``, which a destination that reads as present-but-unreadable never reaches again.

What the person sees: the first reply is the reservation's refusal, "This request was not sent. Retry this call."
The retry, and every call after it, is ``G6_E_COMPLETION_TASK_UNREADABLE`` ("Start a new session") for a task
binding, and ``G6_E_NO_CLEAN_REVIEW`` ("No report is available; the send count is unknown") for a run whose only
request was never sent. A Stop hook ends that turn, and the next, untested.

(b) alone is older than this branch's change (the last control: the parent's storage leaves the same session
unreadable), so this is what is left, not a regression. What is new is the reply that asks for the retry, and the
claim that the next access finishes the recovery.

On the real disk with real open handles. The reader is a scanner of new files: it opens the publication's new receipt
spare as it appears, so the receipt cannot be moved into place, and (``content-held-at-the-restore``) the new content
once it is there, so the restore cannot remove it within its own wait. It lets go when the call has returned.
``spare-only`` never touches the content: the restore then works within the first call, and only (b) is left.
The reader has under a millisecond to open the spare, so a session where it came too late is tried again under
another name.
"""
import json
import os

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu4_1.rev1_readers import NewFileReader, Reader, storage_at
from tests.harness.test_completion_fu3_repair2 import Session

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
HELD = pytest.mark.parametrize('also_content', [False, True], ids=['spare-only', 'content-held-at-the-restore'])
TRIES = 40


def _refused_first(tmp_path, monkeypatch, key_file, name, stem, first_call, also_content, *, before=None):
    """Sessions until the reader lands on one's first ``stem`` publication: that session, and what its call did."""
    for attempt in range(TRIES):
        folder = tmp_path / f'session-{attempt}'
        folder.mkdir()
        world = Session(folder, monkeypatch, key_file, f'{name}-{attempt}')
        world.name = f'{name}-{attempt}'.removeprefix('mcp:')
        store.mkdir(world.folder, parents=True, exist_ok=True)
        if before is not None:
            before(world)
        reader = NewFileReader(world.folder, stem, also_content=also_content)
        try:
            outcome = ('returned', first_call(world))
        except PermissionError as exc:
            outcome = ('raised', exc)
        finally:
            reader.leave()
        if reader.landed and not world.reviewed:
            return world, outcome
    pytest.fail(f'the reader never opened a new receipt spare in time, in {TRIES} sessions')


def _mcp(world):
    return review_loop.step(str(world.repo), 'the task', world.name, 'base')


def _left(world):
    return sorted(p.name for p in world.folder.iterdir() if not p.name.startswith('.'))


@HELD
def test_an_mcp_call_refused_at_its_first_binding_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file,
                                                                            also_content):
    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'mcp:fable-first-binding', 'task', _mcp,
                                             also_content)
    if also_content:    # the pending restore's own reply
        assert outcome == 'returned' and first.get('code') == loop.RESERVATION_UNSAVED and 'Retry this call' in first['error']
    # Every file is free again. The client repeats its call, as the reply said to (twice, to show it is for good).
    later = _mcp(world)
    third = _mcp(world)
    assert world.reviewed and later.get('code') != store.TASK_UNREADABLE, (
        f'the retry is refused: {later.get("code")}; recovery={later.get("recovery")!r}; and again: '
        f'{third.get("code")}; left in the session: {_left(world)}')


@HELD
def test_an_mcp_call_refused_at_its_first_reservation_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file,
                                                                                also_content):
    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'mcp:fable-first-state', 'state', _mcp,
                                             also_content)
    assert outcome == 'returned' and first.get('code') == loop.RESERVATION_UNSAVED, first
    assert 'This request was not sent. Retry this call.' in first['error']
    later = _mcp(world)
    third = _mcp(world)
    assert world.reviewed and later.get('code') != loop.NO_CLEAN_REVIEW, (
        f'the retry is told the run is over: {later.get("code")}; requests_sent={later.get("requests_sent", "absent")!r}; '
        f'{later.get("error", "")[-110:]} and again: {third.get("code")}; left in the run: {_left(world)}')


@HELD
def test_a_stop_refused_at_its_first_reservation_can_be_retried_as_it_says(tmp_path, monkeypatch, key_file,
                                                                           also_content):
    def prompt(world):
        hook.on_prompt({**world.event, 'prompt': 'the task'})

    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'fable-first-stop', 'state',
                                             lambda world: hook.on_stop(world.event), also_content, before=prompt)
    assert outcome == 'returned' and loop.RESERVATION_UNSAVED in first['systemMessage']
    assert 'This request was not sent. Retry this call.' in first['systemMessage']
    later = hook.on_stop(world.event)
    assert world.reviewed, (f'the retry the reply asked for can never review: {later.get("systemMessage", "")[:230]}; '
                            f'left in the run: {_left(world)}')


@HELD
def test_a_stop_after_a_prompt_refused_at_its_first_binding_reviews_the_task(tmp_path, monkeypatch, key_file,
                                                                             also_content):
    """The hook's first prompt is refused the same way (it fails, and the prompt goes ahead). The Stop hook then has no
    binding, and reads the task from the transcript, as it does for any session no prompt hook saw."""
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'user', 'message': {'content': 'the task'}}) + '\n', encoding='utf-8')

    def prompt(world):
        world.event['transcript_path'] = str(transcript)
        return hook.on_prompt({**world.event, 'prompt': 'the task'})

    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'fable-first-prompt', 'task', prompt,
                                             also_content)
    assert outcome == 'raised' and isinstance(first, PermissionError)
    said = hook.on_stop(world.event)
    assert set(world.reviewed) == {'the task'} and store.TASK_UNREADABLE not in said.get('systemMessage', ''), (
        f'the turn ended untested, on a binding that is not there: {said.get("systemMessage", "")[:200]}; '
        f'left in the session: {_left(world)}')


def test_the_retry_reads_the_binding_it_decided_on_before_the_restore_removed_it(tmp_path, monkeypatch, key_file):
    """Mechanism (a) by itself: with the refused attempt's spares taken away by hand, the retry still ends as
    ``TaskUnreadable``, because ``task_present`` and ``task_path`` were read before the restore removed the content.
    Only the call after that one binds and reviews."""
    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'mcp:fable-first-binding-order', 'task',
                                             _mcp, True)
    assert outcome == 'returned' and first.get('code') == loop.RESERVATION_UNSAVED
    for spare in ('task.json.new', 'task.sha256.new'):
        if (world.folder / spare).exists():
            os.remove(world.folder / spare)
    later = _mcp(world)
    after_retry = list(world.reviewed)
    third = _mcp(world)
    assert after_retry and later.get('code') != store.TASK_UNREADABLE, (
        f'the retry is refused: {later.get("code")}; the same call once more: '
        f'{"reviews" if world.reviewed else third.get("code")}')


def test_control_with_a_prior_pair_the_same_refusal_can_be_retried(tmp_path, monkeypatch, key_file):
    """Review 4's case, which the pending restore repairs: the run has a saved state, and a reader holds its receipt
    and then the content the save replaced. The retry reviews."""
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:fable-prior-pair')
    world.name = 'fable-prior-pair'
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = Reader(world.folder / 'state.json', also_content=True)
    reader.arrive()
    try:
        first = _mcp(world)
    finally:
        reader.leave()
    assert reader.replaced and first.get('code') == loop.RESERVATION_UNSAVED and world.reviewed == []
    later = _mcp(world)
    assert world.reviewed and later.get('code') != loop.NO_CLEAN_REVIEW


def test_control_without_the_refused_attempts_spare_the_same_call_binds_and_reviews(tmp_path, monkeypatch, key_file):
    """Mechanism (b) by itself: after the spare-only refusal nothing is left but the attempt's own receipt spare.
    With that one file taken away by hand, the same call binds the task and reviews."""
    world, _ = _refused_first(tmp_path, monkeypatch, key_file, 'mcp:fable-first-binding-swept', 'task', _mcp, False)
    assert _left(world) == ['task.sha256.new']
    os.remove(world.folder / 'task.sha256.new')
    later = _mcp(world)
    assert world.reviewed and later.get('code') != store.TASK_UNREADABLE


def test_control_the_parents_storage_left_the_first_binding_unreadable_too(tmp_path, monkeypatch, key_file):
    """Not a regression: with the storage module of 1827b22e (this change's parent, read from git) the same reader
    leaves the same session unreadable. There the first call's error was uncoded, and asked for no retry."""
    parent = storage_at('1827b22e')
    for module in (loop, hook, review_loop):
        monkeypatch.setattr(module, 'store', parent)
    world, (outcome, first) = _refused_first(tmp_path, monkeypatch, key_file, 'mcp:fable-first-binding-parent', 'task',
                                             _mcp, True)
    assert outcome == 'raised' and getattr(first, 'code', None) is None
    later = _mcp(world)
    assert later.get('code') == parent.TASK_UNREADABLE and world.reviewed == []
