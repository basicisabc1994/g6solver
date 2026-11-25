# SPDX-License-Identifier: Apache-2.0
"""Repair-3 orphan recovery and flow-free writer inventory rulings."""
import errno
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.core.model_calls import note_model_call
from g6.workspace._envelope import WorkspaceRefused
from tests.harness.test_completion_fu3_repair2 import Session
from tests.harness.test_completion_writer_allowlist import unguarded_calls


@pytest.mark.parametrize('name', ['state.json', 'task.json'])
@pytest.mark.parametrize('leftover', ['none', 'content', 'receipt', 'both'])
def test_mutex_holder_recovers_leftover_spares(tmp_path, name, leftover):
    target = tmp_path / name
    store.write_retained(target, b'old')
    spares = store.retained_paths(target)[2:]
    for spare, kind in zip(spares, ('content', 'receipt')):
        if leftover in (kind, 'both'):
            spare.write_bytes(b'killed writer')
    store.write_retained(target, b'new')
    assert store.cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'new'
    assert not any(spare.exists() for spare in spares)


def test_first_publication_recovers_a_spare_found_under_its_mutex(tmp_path):
    # Amendment 1 supersedes repair 2's refusal of this orphan.
    target = tmp_path / 'task.json'
    spare = tmp_path / 'task.json.new'
    spare.write_bytes(b'killed writer')
    with store.task_publication(target):
        store.write_retained(target, b'ours')
    assert target.read_bytes() == b'ours'
    assert not spare.exists()


@pytest.mark.parametrize('name', ['state.json', 'task.json'])
@pytest.mark.parametrize('held', ['content', 'receipt'])
def test_unremovable_orphan_uses_an_attempt_private_spare(tmp_path, monkeypatch, name, held):
    target = tmp_path / name
    store.write_retained(target, b'old')
    orphan = store.retained_paths(target)[2 if held == 'content' else 3]
    orphan.write_bytes(b'held orphan')
    real_unlink = Path.unlink

    def sharing_violation(item, *args, **kwargs):
        if item == orphan:
            raise PermissionError(errno.EACCES, 'scanner holds orphan')
        return real_unlink(item, *args, **kwargs)

    with monkeypatch.context() as held_file:
        held_file.setattr(Path, 'unlink', sharing_violation)
        store.write_retained(target, b'new')
        assert orphan.read_bytes() == b'held orphan'
        assert store.cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'new'
    # Once released, the next holder can remove the orphan as usual.
    store.write_retained(target, b'next')
    assert not orphan.exists()
    assert store.cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'next'


@pytest.mark.parametrize('fail_once', [False, True], ids=['control', 'one-state-write-fails'])
def test_stop_can_review_after_a_single_state_write_failure(tmp_path, monkeypatch, key_file, fail_once):
    world = Session(tmp_path, monkeypatch, key_file, 'repair3-state-retry')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    real_write, failures = Path.write_bytes, []

    def full_once(item, body):
        if fail_once and item == world.folder / 'state.json.new' and not failures:
            failures.append(item)
            real_write(item, body[:9])
            raise OSError(errno.ENOSPC, 'one full-disk write')
        return real_write(item, body)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'write_bytes', full_once)
        first = hook.on_stop(world.event)
    if fail_once:
        assert failures and loop.RESERVATION_UNSAVED in first['systemMessage']
        assert world.reviewed == [] and loop._load(world.folder).readable
    later = hook.on_stop(world.event) if fail_once else first
    assert world.reviewed and set(world.reviewed) == {'old task'}
    assert 'Report for task: old task' in later['systemMessage']


GATE = 'from g6.harness import _completion_storage as store\n'
REBINDER = 'def change(*args):\n    global store\n    store = p\n    return 0\n'
REBINDS = {
    'class-body-global': 'class Change:\n    global store\n    store = p\n',
    'decorator': REBINDER.replace('return 0', 'return args[0]') + '@change\ndef noop():\n    pass\n',
    'builtin-callback': REBINDER + 'list(map(change, [0]))\n',
    'sort-key': REBINDER + 'sorted([0], key=change)\n',
    'called-in-class-body': REBINDER + 'class Run:\n    change()\n',
    'called-in-comprehension': REBINDER + '[change() for x in range(1)]\n',
    'called-through-a-function': REBINDER + 'def go():\n    change()\ngo()\n',
    'for-back-edge': "for index in range(2):\n    store.write_text(p, 'raw')\n    store = alternate\n",
    'while-back-edge': "count = 0\nwhile count < 2:\n    store.write_text(p, 'raw')\n    store = alternate\n    count += 1\n",
}


@pytest.mark.parametrize('form', list(REBINDS))
def test_inventory_refuses_writer_load_with_a_possible_rebinding(form):
    source = GATE + REBINDS[form]
    if 'back-edge' not in form:
        source += "store.write_text('raw')\n"
    assert any(call == 'store.write_text' for _, call in unguarded_calls(source))


@pytest.mark.parametrize('body', [
    "store.write_text(p, 'gated')",
    'def read():\n    return [store.read_task(p) for p in paths]',
    'class Reader:\n    def read(self):\n        return store.read_task(p)',
    'with store.task_publication(p):\n    store.write_retained(p, body)',
])
def test_inventory_control_plain_gate_uses_still_pass(body):
    assert not unguarded_calls(GATE + body)


@pytest.mark.parametrize('rebind', [False, True], ids=['control', 'later-rebinding'])
def test_inventory_writer_allowance_does_not_depend_on_flow(rebind):
    source = GATE + "store.write_text(p, 'gated')\n"
    if rebind:
        source += "store = p\nstore.write_text('raw')\n"
    found = unguarded_calls(source)
    assert ((2, 'store.write_text') in found) == rebind


@pytest.mark.parametrize('body', [
    "p.write_text('raw')", 'callback = p.write_text', 'return_value = (p.write_text,)',
    'def later():\n    return p.write_text', 'list(map(p.write_text, values))',
])
def test_inventory_refuses_every_raw_writer_load(body):
    assert any(call == 'p.write_text' for _, call in unguarded_calls(body))


@pytest.mark.parametrize('recorded', [False, True], ids=['known-limit-unmarked', 'control-recorded-cleanup'])
def test_staging_sweep_requires_a_cleanup_record(tmp_path, monkeypatch, recorded):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    staged = store.home_path('completion_staging', 'repair3-staging')
    store.mkdir(staged, parents=True)
    (staged / 'task.json.new').write_bytes(b'private task')
    if recorded:
        store._defer_staging(staged, PermissionError(errno.EACCES, 'cleanup deferred'))
        assert staged.with_name(staged.name + '.cleanup').is_file()
        # The disk record must suffice after a process loses its memory queue.
        monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    store.write_bytes(store.home_path('later-write'), b'next')
    assert staged.exists() == (not recorded)
    if not recorded:
        assert (staged / 'task.json.new').read_bytes() == b'private task'
    assert not staged.with_name(staged.name + '.cleanup').exists()


@pytest.mark.parametrize('leftover', [False, True], ids=['control', 'orphan'])
def test_orphan_removal_enters_the_existing_cleanup_path(tmp_path, monkeypatch, leftover):
    target = tmp_path / 'state.json'
    spare = target.with_name(target.name + '.new')
    if leftover:
        spare.write_bytes(b'killed writer')
    recorded = []
    real_defer = store._defer_staging

    def defer(item, error):
        recorded.append(item)
        real_defer(item, error)

    monkeypatch.setattr(store, '_defer_staging', defer)
    store.write_retained(target, b'new')
    assert (spare in recorded) == leftover
    assert target.read_bytes() == b'new'
    assert not spare.exists()
    assert spare not in store._STAGING_ERRORS
    assert not spare.with_name(spare.name + '.cleanup').exists()


@pytest.mark.parametrize('held', [False, True], ids=['control', 'held-fixed-spare'])
def test_private_spare_is_owned_and_removed_after_a_partial_task_write(tmp_path, monkeypatch, held):
    target = tmp_path / 'task.json'
    spare = target.with_name(target.name + '.new')
    if held:
        spare.write_bytes(b'held orphan')
    real_unlink, real_write = Path.unlink, Path.write_bytes
    failed = []

    def unlink(item, *args, **kwargs):
        if held and item == spare:
            raise PermissionError(errno.EACCES, 'held orphan')
        return real_unlink(item, *args, **kwargs)

    def partial(item, body):
        if item.parent == target.parent and item.name.endswith('.new'):
            attempt = store._ATTEMPT.get()
            failed.append((item, attempt.spares.get(item)))
            real_write(item, body[:2])
            raise OSError(errno.ENOSPC, 'partial private spare')
        return real_write(item, body)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'unlink', unlink)
        fault.setattr(Path, 'write_bytes', partial)
        with pytest.raises(OSError, match='partial private spare'):
            with store.task_publication(target):
                store.write_retained(target, b'new')
    assert len(failed) == 1 and failed[0][1] is not None
    assert not failed[0][0].exists()
    assert not target.exists()
    if held:
        assert spare.read_bytes() == b'held orphan'
    store.write_retained(target, b'next')
    assert target.read_bytes() == b'next' and not spare.exists()


@pytest.mark.parametrize('body', [
    'callback = write_text',
    'from pathlib import Path\ncallback = Path.write_text',
    'from os import write as emit\ncallback = emit',
    'def capture(open):\n    return open',
])
def test_inventory_refuses_raw_writer_names_and_imported_captures(body):
    assert unguarded_calls(body)


def test_inventory_control_a_local_data_value_is_not_an_imported_writer():
    assert not unguarded_calls(GATE + 'with store.task_publication(p) as copy:\n    result = copy\n')


@pytest.mark.parametrize('late', [False, True], ids=['control', 'late-import'])
def test_inventory_does_not_lend_a_later_import_to_an_earlier_writer_load(late):
    call = "store.write_text(p, 'gated')\n"
    found = unguarded_calls(call + GATE if late else GATE + call)
    assert bool(found) == late


@pytest.mark.parametrize('name', ['state.json', 'task.json'])
def test_a_later_holder_removes_private_orphans_only_for_its_destination(tmp_path, name):
    target = tmp_path / name
    private = [tmp_path / f'{name}.{suffix * 32}.new' for suffix in ('a', 'b')]
    unrelated = tmp_path / f'{name}.not-an-attempt.new'
    for item in [*private, unrelated]:
        item.write_bytes(b'leftover')
    store.write_retained(target, b'new')
    assert target.read_bytes() == b'new'
    assert not any(item.exists() for item in private)
    assert unrelated.read_bytes() == b'leftover'


@pytest.mark.parametrize('held', [False, True], ids=['control', 'held-fixed-spare'])
def test_caller_failure_restores_through_the_selected_private_spare(tmp_path, monkeypatch, held):
    target = tmp_path / 'task.json'
    store.write_retained(target, b'old')
    spare = target.with_name(target.name + '.new')
    if held:
        spare.write_bytes(b'held orphan')
    real_unlink = Path.unlink

    def unlink(item, *args, **kwargs):
        if held and item == spare:
            raise PermissionError(errno.EACCES, 'held orphan')
        return real_unlink(item, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, 'unlink', unlink)
        with pytest.raises(OSError, match='caller step failed'):
            with store.task_publication(target):
                store.write_retained(target, b'new')
                raise OSError('caller step failed')
    assert store.cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'old'
    if held:
        assert spare.read_bytes() == b'held orphan'
    store.write_retained(target, b'next')
    assert not any(item.name.endswith('.new') for item in target.parent.iterdir())


@pytest.mark.parametrize('present', [False, True], ids=['missing-folder', 'control-existing-folder'])
def test_task_memory_publication_does_not_require_a_spare_directory(tmp_path, monkeypatch, present):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    target = home / 'new-task' / 'task.json'
    if present:
        target.parent.mkdir()
    written = {}
    monkeypatch.setattr(Path, 'mkdir', lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, 'write_bytes', lambda item, body: written.__setitem__(item, body))
    monkeypatch.setattr(store.cloud, 'write_retained', lambda item, body: written.__setitem__(item, body))
    with store.task_publication(target):
        store.write_retained(target, b'first task')
    assert written[target] == b'first task'
    assert target.parent.is_dir() == present


def test_state_memory_save_keeps_counts_and_reaches_the_writer_fault(tmp_path, monkeypatch):
    session = tmp_path / 'absent-run'
    files = {}
    mutex = threading.Lock()

    @contextmanager
    def held(folder):
        if not mutex.acquire(blocking=False):
            raise WorkspaceRefused('G6_E_WORKSPACE_BUSY', 'step already running')
        try:
            yield
        finally:
            mutex.release()

    def load(folder):
        return json.loads(files.get(folder / 'state.json', b'{}'))

    def publish(target, body):
        assert mutex.locked(), 'publication must still hold the run mutex'
        files[target] = body

    monkeypatch.setattr(Path, 'mkdir', lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, 'write_bytes', lambda item, body: files.__setitem__(item, body))
    monkeypatch.setattr(store.cloud, 'write_retained', publish)
    monkeypatch.setattr(loop, '_held', held)
    monkeypatch.setattr(loop, '_load', load)
    monkeypatch.setattr(loop, '_sync_state', lambda *args: None)
    loop._save(session, {'attempted': 6, 'reviews': [{'patch': 'prior'}]}, sources=())
    loop._save(session, {'attempted': 0, 'reviews': []}, sources=())
    assert load(session)['attempted'] == 6
    assert load(session)['reviews'] == [{'patch': 'prior'}]
    with loop._writing(session), loop._run_count(session, load(session), fresh=False):
        note_model_call()
    assert load(session)['attempted'] == 7
    assert load(session)['reviews'] == [{'patch': 'prior'}]

    def interrupted(target, body):
        assert mutex.locked()
        raise RuntimeError('at retained writer')

    monkeypatch.setattr(store.cloud, 'write_retained', interrupted)
    with pytest.raises(RuntimeError, match='^at retained writer$'):
        loop._save(session, {'attempted': 0}, sources=())
    assert load(session)['attempted'] == 7
    assert not session.exists()


@pytest.mark.parametrize('error', [PermissionError, NotADirectoryError])
def test_spare_scan_errors_other_than_missing_directory_still_refuse(tmp_path, monkeypatch, error):
    target = tmp_path / 'task.json'
    real_iterdir = Path.iterdir

    def refused(folder):
        if folder == target.parent:
            raise error('cannot inspect spare directory')
        return real_iterdir(folder)

    monkeypatch.setattr(Path, 'iterdir', refused)
    with pytest.raises(error, match='cannot inspect spare directory'):
        store.write_retained(target, b'not published')
    assert not target.exists() and not target.with_suffix('.sha256').exists()
