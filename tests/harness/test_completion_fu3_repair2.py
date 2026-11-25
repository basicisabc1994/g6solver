# SPDX-License-Identifier: Apache-2.0
"""Cleanup cannot withdraw a consumed task; spare ownership starts at creation."""
import errno
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.model_calls import note_model_call
from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests.harness.test_completion_writer_allowlist import unguarded_calls


class Session:
    def __init__(self, tmp_path, monkeypatch, key_file, session_id):
        key_file('G6_LOOP_MODE=scouts\n')
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
        return hook._task(self.folder)['task'] if store.task_present(self.folder / 'task.json') else None


def _consumed_during_cleanup(world, monkeypatch, variant):
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
            if os.name != 'nt':
                raise PermissionError(13, 'a staged file is still in use')
        return real_remove(folder)

    def publisher():
        try:
            hook.on_prompt({**world.event, 'prompt': prompt})
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(store, 'remove_tree', cleanup)
    worker = threading.Thread(target=publisher, name='prompt-publisher')
    worker.start()
    held = None
    try:
        assert at_cleanup.wait(30)
        expected = 'old task\n\nnew task' if variant == 'append' else 'old task'
        assert world.bound() == expected
        if os.name == 'nt':
            held = open(staged[0] / 'task.sha256.new', 'rb')
        said = hook.on_stop(world.event)
        assert ('Report for task: ' + expected) in said['systemMessage']
        assert set(world.reviewed) == {expected} and loop._load(world.folder)['done']
    finally:
        go_on.set()
        worker.join(30)
        if held is not None:
            held.close()
        monkeypatch.setattr(store, 'remove_tree', real_remove)
    assert not worker.is_alive()
    assert errors == []
    assert world.bound() == expected
    assert staged[0].is_dir(), 'the held staging copy must await a sweep'
    # A later retained write, even outside task_publication, sweeps under home.
    swept = []

    def checked_sweep(folder):
        if folder == staged[0]:
            home_lock = os.path.normcase(str(store.home_path()))
            assert any(key[0] == home_lock and active[0] for key, active in store._LOCKS.get())
            swept.append(folder)
        return real_remove(folder)

    with monkeypatch.context() as sweeping:
        sweeping.setattr(store, 'remove_tree', checked_sweep)
        store.write_bytes(store.home_path('sweep-check.json'), b'next write')
    assert swept == staged and not staged[0].exists()
    return expected


@pytest.mark.parametrize('fault', ['staging-cleanup-fails'])
@pytest.mark.parametrize('variant', ['append', 'first'])
def test_an_ended_review_keeps_the_binding_it_read(tmp_path, monkeypatch, key_file, variant, fault):
    world = Session(tmp_path, monkeypatch, key_file, 'repair2-consumed')
    ended_on = _consumed_during_cleanup(world, monkeypatch, variant)
    assert world.bound() == ended_on


@pytest.mark.parametrize('fault', ['staging-cleanup-fails'])
@pytest.mark.parametrize('variant', ['append', 'first'])
def test_the_next_prompt_builds_on_the_task_the_ended_review_read(tmp_path, monkeypatch, key_file, variant, fault):
    world = Session(tmp_path, monkeypatch, key_file, 'repair2-next')
    ended_on = _consumed_during_cleanup(world, monkeypatch, variant)
    hook.on_prompt({**world.event, 'prompt': 'next task'})
    hook.on_stop(world.event)
    assert world.reviewed[-1] == ended_on + '\n\nnext task'


@pytest.mark.parametrize('fault', ['staging-cleanup-fails'])
@pytest.mark.parametrize('variant', ['append', 'first'])
def test_mcp_refuses_a_task_the_ended_review_never_read(tmp_path, monkeypatch, key_file, variant, fault):
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:repair2-mcp')
    ended_on = _consumed_during_cleanup(world, monkeypatch, variant)
    other = 'old task' if variant == 'append' else 'a task this session never reviewed'
    assert other != ended_on
    reply = review_loop.step(str(world.repo), other, 'repair2-mcp', 'base')
    assert reply.get('code') == 'G6_E_INPUT_VALIDATION'


@pytest.mark.parametrize('moment', ['before-the-loop-started', 'after-a-loop-ended'])
@pytest.mark.parametrize('held', [False, True], ids=['control', 'staged-file-held-open'])
def test_prompt_prepares_before_waiting_for_the_home_mutex(tmp_path, monkeypatch, key_file, moment, held):
    """Run-state preparation cannot take the home lock before task staging."""
    from g6.workspace._coordination import Coordination

    if held and os.name != 'nt':
        pytest.skip('the sharing violation is Windows file semantics')
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    world = Session(tmp_path, monkeypatch, key_file, 'repair2-staging-order')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    if moment == 'after-a-loop-ended':
        assert 'Report for task: old task' in hook.on_stop(world.event)['systemMessage']
    errors, handle = [], None

    def publisher():
        try:
            hook.on_prompt({**world.event, 'prompt': 'new task'})
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=publisher, name='prompt-publisher')
    staging = store.home_path('completion_staging')
    try:
        with Coordination(store.home_path()).mutex():
            worker.start()
            deadline, staged = time.monotonic() + 4, []
            while time.monotonic() < deadline and not staged:
                staged = list(staging.glob('*/task.sha256.new'))
                time.sleep(0.01)
            assert staged, 'the publisher never prepared its staging copy'
            assert len(staged) == 1
            body = (staged[0].parent / 'task.json.new').read_bytes()
            assert json.loads(body)['task'] == 'old task\n\nnew task'
            assert staged[0].read_text(encoding='ascii') == hashlib.sha256(body).hexdigest()
            if held:
                handle = open(staged[0], 'rb')
            time.sleep(0.1)
            assert world.bound() == 'old task'
            assert worker.is_alive() and errors == []
    finally:
        if worker.ident is not None:
            worker.join(30)
        if handle is not None:
            handle.close()
    assert not worker.is_alive()
    assert errors == []
    assert world.bound() == 'old task\n\nnew task'
    said = hook.on_stop(world.event)
    assert world.reviewed[-1] == 'old task\n\nnew task'
    assert 'Report for task: old task\n\nnew task' in said['systemMessage']


def test_sweep_leaves_a_live_preparation_alone(tmp_path, monkeypatch):
    ready, resume, errors, staged = threading.Event(), threading.Event(), [], []
    real_remove = store.remove_tree

    def cleanup(folder):
        if threading.current_thread().name == 'active-preparation':
            staged.append(folder)
            ready.set()
            assert resume.wait(30)
            raise PermissionError(13, 'cleanup must wait')
        return real_remove(folder)

    def write():
        try:
            with store.task_publication(tmp_path / 'task.json'):
                store.write_retained(tmp_path / 'task.json', b'task')
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(store, 'remove_tree', cleanup)
    worker = threading.Thread(target=write, name='active-preparation')
    worker.start()
    try:
        assert ready.wait(30)
        store.write_retained(tmp_path / 'other.json', b'other')
        assert (staged[0] / 'task.json.new').read_bytes() == b'task'
    finally:
        resume.set()
        worker.join(30)
        monkeypatch.setattr(store, 'remove_tree', real_remove)
    assert not worker.is_alive() and not errors
    store.write_retained(tmp_path / 'other.json', b'later')
    assert not staged[0].exists()


@pytest.mark.parametrize('name,reached', [('task.json.new', 0), ('task.json.new', 9), ('task.sha256.new', 9)])
@pytest.mark.parametrize('replaced', [False, True], ids=['owned-partial', 'replacement'])
def test_failed_task_removes_only_the_spare_it_created(tmp_path, monkeypatch, name, reached, replaced):
    target = tmp_path / 'task.json'
    spare = tmp_path / name
    replacement = tmp_path / 'other-writer'
    replacement.write_bytes(b'another writer')
    real_write = Path.write_bytes
    identities = []

    def fail(item, body):
        if item == spare:
            # At this point creation ownership must already have been captured.
            attempt = store._ATTEMPT.get()
            identities.append(attempt.spares.get(item))
            real_write(item, body[:reached])
            if replaced:
                os.replace(replacement, item)
            raise OSError(errno.ENOSPC, 'disk filled after opening the spare')
        return real_write(item, body)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'write_bytes', fail)
        with pytest.raises(OSError, match='disk filled'):
            with store.task_publication(target):
                store.write_retained(target, b'new task bytes')
    assert identities and identities[0] is not None
    if replaced:
        assert spare.read_bytes() == b'another writer'
    else:
        assert not any(item.exists() for item in store.retained_paths(target))


def test_a_spare_the_attempt_did_not_create_is_never_removed(tmp_path):
    target = tmp_path / 'task.json'
    spare = tmp_path / 'task.json.new'
    spare.write_bytes(b'another writer')
    with pytest.raises(FileExistsError):
        with store.task_publication(target):
            store.write_retained(target, b'ours')
    assert spare.read_bytes() == b'another writer'
    assert not target.exists()


GATE = 'from g6.harness import _completion_storage as store\n'
REBINDINGS = {
    'assignment': "store = p\nstore.write_text(p, 'raw')",
    'unpacking': "store, other = paths\nstore.write_text(p, 'raw')",
    'augmented': "store += p\nstore.write_text(p, 'raw')",
    'parameter': "def publish(store):\n    store.write_text(p, 'raw')",
    'lambda-parameter': "publish = lambda store: store.write_text(p, 'raw')",
    'local-before-assignment': "def publish():\n    store.write_text(p, 'raw')\n    store = p",
    'loop': "for store in paths:\n    store.write_text(p, 'raw')",
    'with': "with context as store:\n    store.write_text(p, 'raw')",
    'walrus': "(store := p)\nstore.write_text(p, 'raw')",
    'annotation': "store: object = p\nstore.write_text(p, 'raw')",
    'delete': "del store\nstore.write_text(p, 'raw')",
    'import-after': "import os as store\nstore.replace(a, b)",
    'import-before': "import os as store\nstore.replace(a, b)\n" + GATE,
    'function-import': "def publish():\n    import os as store\n    store.replace(a, b)",
    'class': "class Example:\n    store = p\n    store.write_text(p, 'raw')",
    'method-skips-class': "store = p\nclass Example:\n    " + GATE.strip() + "\n    def publish(self):\n        store.write_text(p, 'raw')",
    'comprehension': "[store.write_text(p, 'raw') for store in paths]",
    'nested-comprehension': "[[store.write_text(p, 'raw') for x in xs] for store in paths]",
    'later-comprehension-target': "[x for x in xs for y in store.write_text(p, 'raw') for store in paths]",
    'generator-late': "values = (store.write_text(p, 'raw') for x in xs)\nstore = p",
    'class-comprehension': "store = p\nclass Example:\n    " + GATE.strip() + "\n    values = [store.write_text(p, 'raw') for x in xs]",
    'global': "def publish():\n    global store\n    store = p\n    store.write_text(p, 'raw')",
    'global-late': "def publish():\n    store.write_text(p, 'raw')\nstore = p",
    'global-call': "def replace_gate():\n    global store\n    store = p\nreplace_gate()\nstore.write_text(p, 'raw')",
    'nonlocal': "def outer():\n    " + GATE.strip() + "\n    def publish():\n        nonlocal store\n        store = p\n        store.write_text(p, 'raw')",
    'branch': "if flag:\n    store = p\nstore.write_text(p, 'raw')",
    'except': "try:\n    pass\nexcept Exception as store:\n    store.write_text(p, 'raw')",
    'interrupted-try': "try:\n    store = p\nexcept Exception:\n    store.write_text(p, 'raw')",
    'match': "match value:\n    case {'store': store}:\n        store.write_text(p, 'raw')",
}


@pytest.mark.parametrize('form', list(REBINDINGS))
def test_inventory_refuses_each_rebinding(form):
    found = unguarded_calls(GATE + REBINDINGS[form])
    assert any(call in {'store.write_text', 'store.replace'} for _, call in found), found


@pytest.mark.parametrize('body', [
    "store.write_text(p, 'gated')",
    "def publish():\n    store.write_text(p, 'gated')",
    "def publish():\n    " + GATE.strip() + "\n    store.write_text(p, 'gated')",
    "class Example:\n    store.write_text(p, 'gated')",
    "[store.write_text(p, 'gated') for x in paths]",
    "[x for x in store.write_text(p, 'gated') for store in paths]",
    "(store.write_text(p, 'gated') for x in paths)",
    "def publish():\n    global store\n    store.write_text(p, 'gated')",
    "def outer():\n    " + GATE.strip() + "\n    def publish():\n        nonlocal store\n        store.write_text(p, 'gated')",
])
def test_inventory_keeps_plain_gate_controls(body):
    assert not unguarded_calls(GATE + body)


def test_inventory_resolves_module_calls_in_source_order():
    source = GATE + "store.write_text(p, 'gated')\nstore = p\nstore.write_text('raw')\n"
    found = unguarded_calls(source)
    assert (2, 'store.write_text') not in found
    assert (4, 'store.write_text') in found


def test_inventory_retains_prior_refusals_for_conflicting_imports():
    source = GATE + "store.write_text(p, 'gated')\nfrom os import path as store\n"
    # Tightening must keep even this older conservative refusal.
    assert (2, 'store.write_text') in unguarded_calls(source)


@pytest.mark.parametrize('record', ['disk', 'memory-fallback'])
def test_cleanup_record_survives_until_the_next_write(tmp_path, monkeypatch, key_file, record):
    # Each variant owns its home and queue. Replacing the disk variant's queue
    # below must not restore stale entries into the next variant's home.
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    target = tmp_path / 'task.json'
    staged = []
    real_remove, real_write = store.remove_tree, store.write_text

    def cleanup(folder):
        staged.append(folder)
        raise PermissionError(13, 'scanner holds staging')

    def write(item, text, **kwargs):
        if item.name.endswith('.cleanup'):
            raise OSError(errno.ENOSPC, 'cannot persist cleanup record')
        return real_write(item, text, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(store, 'remove_tree', cleanup)
        if record == 'memory-fallback':
            fault.setattr(store, 'write_text', write)
        with store.task_publication(target):
            store.write_retained(target, b'published')
    assert target.read_bytes() == b'published'
    assert len(staged) == 1
    assert staged[0].parent == store.home_path('completion_staging')
    assert staged[0].is_dir()
    if record == 'disk':
        assert staged[0].with_name(staged[0].name + '.cleanup').is_file()
        # A new process has no in-memory queue; the disk record is sufficient.
        monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    else:
        assert not staged[0].with_name(staged[0].name + '.cleanup').exists()
        assert staged[0] in store._STAGING_ERRORS
    monkeypatch.setattr(store, 'remove_tree', real_remove)
    store.write_bytes(store.home_path('next-write'), b'next')
    assert not staged[0].exists()
    assert not staged[0].with_name(staged[0].name + '.cleanup').exists()
    assert not store._STAGING_ERRORS


def test_spare_reservation_accepts_a_writer_without_fileno(tmp_path, monkeypatch, key_file):
    target = tmp_path / 'task.json'
    real_fdopen = os.fdopen

    def opened(fd, *args, **kwargs):
        sink = real_fdopen(fd, *args, **kwargs)

        class Writer:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                sink.close()

            def write(self, text):
                assert text, 'exclusive empty reservation needs no stream write'
                return sink.write(text)

        return Writer()

    monkeypatch.setattr(os, 'fdopen', opened)
    with store.task_publication(target):
        store.write_retained(target, b'{"repo":"repo","task":"task","base":"base"}')
    assert store.read_task(target) == {'repo': 'repo', 'task': 'task', 'base': 'base'}
    assert not any(item.exists() for item in store.retained_paths(target)[2:])
