"""Finding 2: a task binding is withdrawn after another call has read it and ended a review on it.

``write_retained`` publishes a task under the home mutex, releases the mutex, and only then
removes its private staging folder. An ``OSError`` from that removal (on Windows: any other
process still holding a staged file open, as a scanner does) reaches ``task_publication``, whose
rollback retakes the mutex and compares bytes only: equal bytes are taken to mean "nobody used
this", so the published binding is replaced by the previous one, or deleted if it was the first.
``_record`` and the prompt hook's append hold no step mutex, so a Stop hook can read the
binding and end a run on it inside that window.

The fault here is a real sharing violation on the real disk where the platform gives one
(Windows); elsewhere the same ``OSError`` is raised in its place. ``store.remove_tree`` is
wrapped only to hold the publisher at the start of its cleanup, as a scheduler could.
"""
import os
import threading
from types import SimpleNamespace

import pytest

from g6.core.model_calls import note_model_call
from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop

VARIANTS = ['append', 'first']
FAULTS = ['control', 'staging-cleanup-fails']


class Session:
    def __init__(self, tmp_path, monkeypatch, session_id):
        (store.home_path() / '.env').write_text('G6_LOOP_MODE=scouts\n', encoding='utf-8')    # the person's key file
        self.repo = tmp_path / 'repo'
        self.repo.mkdir()
        self.event = {'session_id': session_id, 'cwd': str(self.repo), 'last_assistant_message': 'finished'}
        self.folder = hook.folder(session_id)
        self.reviewed = []
        change = SimpleNamespace(repo=self.repo, base_commit='base', sources=('mod.py',))
        monkeypatch.setattr(hook, '_here', lambda cwd: (self.repo, 'base'))
        monkeypatch.setattr(hook, '_hint', lambda task: '')
        monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda root: self.repo)
        monkeypatch.setattr(loop.repo_tests, 'change', lambda *args: change)
        monkeypatch.setattr(loop.repo_tests, 'diff_text', lambda change: 'patch')
        monkeypatch.setattr(loop.proposer, 'boundary', lambda root: '')
        monkeypatch.setattr(loop, 'scout_host', lambda: object())
        monkeypatch.setattr(loop, 'critic_host', lambda: None)

        def scout(change, task, text, where, host, deadline):
            note_model_call()
            self.reviewed.append(task)
            return {'left_out': 'synthetic host has no probes', 'where': str(where), 'refused': [], 'at_base': None}

        monkeypatch.setattr(loop, '_scout', scout)
        monkeypatch.setattr(loop, '_final_review', lambda root, task, base: 'Report for task: ' + task)

    def bound(self):
        """The task the session's binding names now; None with no binding."""
        return hook._task(self.folder)['task'] if store.task_present(self.folder / 'task.json') else None


def _publish_then_review_inside_the_cleanup_window(world, monkeypatch, variant, fault):
    """A prompt hook publishes; a Stop hook reads that binding and ends a run on it before the publisher's
    cleanup returns. Gives the task the ended review read."""
    if variant == 'append':
        hook.on_prompt({**world.event, 'prompt': 'old task'})
        prompt = 'new task'
    else:
        prompt = 'old task'
    at_cleanup, go_on, staged, errors = threading.Event(), threading.Event(), [], []
    real_remove = store.remove_tree

    def cleanup(folder):
        if threading.current_thread().name == 'prompt-publisher' and folder.parent.name == 'completion_staging':
            staged.append(folder)
            at_cleanup.set()
            assert go_on.wait(30)
            if fault != 'control' and os.name != 'nt':
                raise PermissionError(13, 'a staged file is still in use')
        return real_remove(folder)

    def publisher():
        try:
            hook.on_prompt({**world.event, 'prompt': prompt})
        except OSError as exc:
            errors.append(exc)

    monkeypatch.setattr(store, 'remove_tree', cleanup)
    worker = threading.Thread(target=publisher, name='prompt-publisher')
    worker.start()
    held = None
    try:
        assert at_cleanup.wait(30)
        expected = 'old task\n\nnew task' if variant == 'append' else 'old task'
        assert world.bound() == expected            # published, mutex released, visible to every reader
        if fault != 'control' and os.name == 'nt':
            held = open(staged[0] / 'task.sha256.new', 'rb')     # what a scanner does to a file just written
        said = hook.on_stop(world.event)
        assert ('Report for task: ' + expected) in said['systemMessage']
        assert set(world.reviewed) == {expected} and loop._load(world.folder)['done']
    finally:
        go_on.set()
        worker.join(30)
        if held is not None:
            held.close()
    assert not worker.is_alive()
    monkeypatch.setattr(store, 'remove_tree', real_remove)
    if fault == 'control':
        assert errors == []
    else:
        assert len(errors) == 1 and isinstance(errors[0], PermissionError), errors
    return expected


@pytest.mark.parametrize('fault', FAULTS)
@pytest.mark.parametrize('variant', VARIANTS)
def test_an_ended_review_keeps_the_binding_it_read(tmp_path, monkeypatch, variant, fault):
    world = Session(tmp_path, monkeypatch, 'consumed-binding')
    ended_on = _publish_then_review_inside_the_cleanup_window(world, monkeypatch, variant, fault)
    assert world.bound() == ended_on, (
        f'the run ended on a review of {ended_on!r}; the binding now names {world.bound()!r}')


@pytest.mark.parametrize('fault', FAULTS)
@pytest.mark.parametrize('variant', VARIANTS)
def test_the_next_prompt_builds_on_the_task_the_ended_review_read(tmp_path, monkeypatch, variant, fault):
    world = Session(tmp_path, monkeypatch, 'consumed-binding-next')
    ended_on = _publish_then_review_inside_the_cleanup_window(world, monkeypatch, variant, fault)
    hook.on_prompt({**world.event, 'prompt': 'next task'})
    said = hook.on_stop(world.event)
    assert world.reviewed[-1] == ended_on + '\n\nnext task', (
        f'last review read {world.reviewed[-1]!r}; the Stop hook said {said.get("systemMessage", "")[:160]!r}')


@pytest.mark.parametrize('fault', FAULTS)
@pytest.mark.parametrize('variant', VARIANTS)
def test_mcp_refuses_a_task_the_ended_review_never_read(tmp_path, monkeypatch, variant, fault):
    """The MCP view of the same folder, joined as review 1's committed session trace joins it (a hook session
    named ``mcp:<name>``): a premise of that trace, not something Claude Code's own session ids give."""
    world = Session(tmp_path, monkeypatch, 'mcp:consumed-binding-mcp')
    ended_on = _publish_then_review_inside_the_cleanup_window(world, monkeypatch, variant, fault)
    other = 'old task' if variant == 'append' else 'a task this session never reviewed'
    assert other != ended_on
    reply = review_loop.step(str(world.repo), other, 'consumed-binding-mcp', 'base')
    assert reply.get('code') == 'G6_E_INPUT_VALIDATION', (
        f'asked about {other!r}, answered ok={reply.get("ok")} with the report of {ended_on!r}: '
        f'{str(reply.get("report", ""))[-60:]!r}')


@pytest.mark.skipif(os.name != 'nt', reason='the sharing violation is Windows file semantics')
@pytest.mark.parametrize('held', [False, True], ids=['control', 'staged-file-held-open'])
@pytest.mark.parametrize('moment', ['before-the-loop-started', 'after-a-loop-ended'])
def test_the_turns_review_reads_the_prompt_whatever_reads_the_staging_copy(tmp_path, monkeypatch, moment, held):
    """No product function is replaced or paused here. The test holds the home mutex just long enough to open
    the staged receipt for reading, as a scanner would; the rest is the code's own timing on the real disk.
    The prompt hook then fails after its binding was published, the binding is withdrawn, and the review at
    the end of that turn either omits the prompt (append) or does not run (a prompt after an ended loop)."""
    import time

    from g6.workspace._coordination import Coordination

    world = Session(tmp_path, monkeypatch, 'staging-reader')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    if moment == 'after-a-loop-ended':
        assert 'Report for task: old task' in hook.on_stop(world.event)['systemMessage']
    errors, handle = [], None

    def publisher():
        try:
            hook.on_prompt({**world.event, 'prompt': 'new task'})
        except OSError as exc:
            errors.append(exc)

    worker = threading.Thread(target=publisher, name='prompt-publisher')
    staging = store.home_path('completion_staging')
    with Coordination(store.home_path()).mutex():         # the publisher prepares, then waits here to publish
        worker.start()
        deadline, staged = time.monotonic() + 4, []
        while time.monotonic() < deadline and not staged:
            staged = list(staging.glob('*/task.sha256.new'))
            time.sleep(0.01)
        assert staged, 'the publisher never prepared its staging copy'
        time.sleep(0.1)
        assert world.bound() == 'old task'                # nothing is published while the mutex is held
        if held:
            handle = open(staged[0], 'rb')
    worker.join(30)
    if handle is not None:
        handle.close()
    assert not worker.is_alive()
    said = hook.on_stop(world.event)
    assert world.reviewed[-1] == 'old task\n\nnew task', (
        f'the prompt hook raised {errors!r}; the turn\'s review read {world.reviewed[-1]!r}; '
        f'the Stop hook said {said.get("systemMessage", "")[:110]!r}')
    assert errors == []
