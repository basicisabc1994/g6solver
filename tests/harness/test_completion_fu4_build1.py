# SPDX-License-Identifier: Apache-2.0
"""Build-1 deferred restoration and lexical inventory regressions (lead-run)."""
import errno
import json
import os
from pathlib import Path

import pytest

from g6.harness import _completion_storage as store, completion_loop as loop
from tests.harness.test_completion_writer_allowlist import unguarded_calls


def _task(text):
    return json.dumps({'repo': 'repo', 'task': text, 'base': 'base'}).encode()


def _refused_restore(target, prior, monkeypatch, *, task=False):
    store.write_retained(target, prior)
    real_replace = os.replace

    def held(source, destination):
        if Path(destination) == target.with_suffix('.sha256') or (
                Path(destination) == target and Path(source).read_bytes() == prior):
            raise PermissionError(errno.EACCES, 'reader holds the retained pair')
        return real_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(store, 'PUBLICATION_WAIT', 0)
        fault.setattr(os, 'replace', held)
        with pytest.raises(PermissionError):
            if task:
                with store.task_publication(target):
                    store.write_retained(target, _task('next'))
            else:
                store.write_retained(target, b'{"kept":"next"}')


def test_pending_restore_is_completed_before_the_next_write(tmp_path, monkeypatch):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior"}'
    _refused_restore(target, prior, monkeypatch)
    real_write, seen = store.cloud.write_retained, []

    def publish(item, body):
        seen.append((target.read_bytes(), target.with_suffix('.sha256').read_text()))
        return real_write(item, body)

    monkeypatch.setattr(store.cloud, 'write_retained', publish)
    store.write_retained(target, b'{"kept":"later"}')
    assert seen == [(prior, store.cloud.sha256(prior))]
    assert target.read_bytes() == b'{"kept":"later"}'
    assert not list(tmp_path.glob('*.cleanup'))


def test_pending_restore_is_completed_by_the_next_load(tmp_path, monkeypatch):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior","requests_sent":3}'
    _refused_restore(target, prior, monkeypatch)
    # Recovery must survive losing the process-local cleanup queue.
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    state = loop._load(tmp_path)
    assert state.readable
    assert state['kept'] == 'prior' and state['requests_sent'] == 3
    assert not list(tmp_path.glob('*.cleanup'))


def test_pending_restore_is_completed_by_the_next_read_task(tmp_path, monkeypatch):
    target = tmp_path / 'task.json'
    _refused_restore(target, _task('prior'), monkeypatch, task=True)
    monkeypatch.setattr(store, '_STAGING_ERRORS', {})
    assert store.read_task(target)['task'] == 'prior'
    assert not list(tmp_path.glob('*.cleanup'))


def test_task_publication_completes_pending_restore_before_a_replaced_writer(tmp_path, monkeypatch):
    target = tmp_path / 'task.json'
    _refused_restore(target, _task('prior'), monkeypatch, task=True)
    seen = []
    monkeypatch.setattr(store, 'write_retained', lambda item, body: seen.append(item.read_bytes()))
    with store.task_publication(target):
        store.write_retained(target, _task('later'))
    assert seen == [_task('prior')]


def test_restore_gets_its_own_bounded_sharing_retry(tmp_path, monkeypatch):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior"}'
    store.write_retained(target, prior)
    real_replace, restored = os.replace, []

    def sharing(source, destination):
        if Path(destination) == target.with_suffix('.sha256'):
            raise PermissionError(errno.EACCES, 'held receipt')
        if Path(destination) == target and Path(source).read_bytes() == prior:
            restored.append(source)
            if len(restored) == 1:
                raise PermissionError(errno.EACCES, 'briefly held content')
        return real_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', sharing)
        with pytest.raises(PermissionError):
            store.write_retained(target, b'{"kept":"next"}')
    assert len(restored) >= 2
    assert target.read_bytes() == prior
    assert not list(tmp_path.glob('*.cleanup'))


@pytest.mark.parametrize('reader', ['load', 'task'])
def test_a_still_held_pending_restore_is_a_retryable_sharing_refusal(tmp_path, monkeypatch, reader):
    target = tmp_path / ('state.json' if reader == 'load' else 'task.json')
    prior = b'{"kept":"prior"}' if reader == 'load' else _task('prior')
    _refused_restore(target, prior, monkeypatch, task=reader == 'task')
    real_replace = os.replace

    def held(source, destination):
        if Path(destination) == target:
            raise PermissionError(errno.EACCES, 'still held')
        return real_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(store, 'PUBLICATION_WAIT', 0)
        fault.setattr(os, 'replace', held)
        with pytest.raises(PermissionError, match='This request was not sent. Retry this call.'):
            loop._load(tmp_path) if reader == 'load' else store.read_task(target)
    assert list(tmp_path.glob('*.cleanup'))
    result = loop._load(tmp_path) if reader == 'load' else store.read_task(target)
    assert result['kept' if reader == 'load' else 'task'] == 'prior'


@pytest.mark.parametrize('missing', [False, True], ids=['replaced', 'gone'])
def test_pending_restore_does_not_adopt_a_replaced_spare(tmp_path, monkeypatch, missing):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior"}'
    _refused_restore(target, prior, monkeypatch)
    markers = list(tmp_path.glob('*.cleanup'))
    assert markers, 'a refused restore needs a durable cleanup record'
    backup = next(item for item in tmp_path.glob('*.new') if item.read_bytes() == prior)
    # Keep the old inode alive so creation identity cannot be recycled.
    backup.rename(tmp_path / 'displaced-backup')
    if not missing:
        backup.write_bytes(b'{"kept":"foreign"}')
    state = loop._load(tmp_path)
    assert not state.readable
    assert target.read_bytes() == b'{"kept":"next"}'
    if not missing:
        assert backup.read_bytes() == b'{"kept":"foreign"}'
    assert not any(marker.exists() for marker in markers)


@pytest.mark.parametrize('source', [
    'import os as store\nclass Run:\n    if flag:\n        from g6.harness import _completion_storage as store\n'
    '    store.unlink(p)\n',
    'import os as store\nclass Outer:\n    from g6.harness import _completion_storage as store\n'
    '    class Inner:\n        store.unlink(p)\n',
    'if flag:\n    from g6.harness._completion_storage import path as vars\nvars(p)\n',
])
def test_inventory_refuses_conditional_and_enclosing_class_gate_bindings(source):
    assert unguarded_calls(source)


def test_inventory_refuses_a_non_gate_star_after_the_gate_import():
    assert unguarded_calls('from g6.harness._completion_storage import unlink\nfrom os import *\nunlink(p)\n')


@pytest.mark.parametrize('source', [
    "make = own.__globals__.get('os')\n",
    "make = values.get('unlink')\n",
    "make = values['__import__']\n",
])
def test_inventory_refuses_private_mapping_and_named_writer_lookups(source):
    assert unguarded_calls(source)


@pytest.mark.parametrize('source', [
    'import os as text\ntext.replace(p, q)\n',
    'import os as data\ndata.replace(p, q)\n',
    'import os\n[os][0].replace(p, q)\n',
])
def test_inventory_does_not_treat_an_import_as_text(source):
    assert unguarded_calls(source)


def test_inventory_admits_a_deferred_read_above_its_module_import():
    source = ('def read():\n    return store.task_present(p)\n'
              'from g6.harness import _completion_storage as store\nread()\n')
    assert not unguarded_calls(source)


@pytest.mark.parametrize('source', [
    'from os import *\nfrom g6.harness._completion_storage import unlink\nunlink(p)\n',
    'class Reader:\n    from g6.harness import _completion_storage as store\n    store.unlink(p)\n',
    "shown = text.replace('a', 'b')\n",
    "shown = parts[0].replace('a', 'b')\n",
])
def test_inventory_controls_keep_the_explicit_allowances(source):
    assert not unguarded_calls(source)


@pytest.mark.parametrize('scope', ['module', 'function', 'class'])
@pytest.mark.parametrize('arms', ['if-else', 'try-except'])
def test_inventory_admits_gate_imports_on_every_reaching_path(scope, arms):
    gate = 'from g6.harness import _completion_storage as store\n'
    if arms == 'if-else':
        source = 'if flag:\n    ' + gate + 'else:\n    ' + gate
    else:
        source = 'try:\n    ' + gate + 'except ImportError:\n    ' + gate
    source += 'store.task_present(p)\n'
    if scope != 'module':
        header = 'def read():\n' if scope == 'function' else 'class Reader:\n'
        source = header + ''.join('    ' + line for line in source.splitlines(keepends=True))
    assert not unguarded_calls(source)


@pytest.mark.parametrize('scope', ['module', 'function', 'class'])
def test_inventory_refuses_a_gate_import_with_a_missing_else(scope):
    source = ('if flag:\n    from g6.harness import _completion_storage as store\n'
              'store.task_present(p)\n')
    if scope != 'module':
        header = 'def read():\n' if scope == 'function' else 'class Reader:\n'
        source = header + ''.join('    ' + line for line in source.splitlines(keepends=True))
    assert unguarded_calls(source)


@pytest.mark.parametrize('operation', ['append_text', 'sync_state'])
def test_restore_backup_obeys_the_named_gate_refusal(tmp_path, monkeypatch, operation):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior"}'
    store.write_retained(target, prior)
    real_replace, real_gate = os.replace, getattr(store, operation)

    def held(source, destination):
        if Path(destination) == target.with_suffix('.sha256'):
            raise PermissionError(errno.EACCES, 'held receipt')
        return real_replace(source, destination)

    def refused(item, *args, **kwargs):
        names = args[0] if operation == 'sync_state' else (Path(item).name,)
        if any(name.startswith('state.json.') and name.endswith('.new')
               and len(name) == len('state.json.') + 32 + len('.new') for name in names):
            raise PermissionError(errno.EACCES, 'restore backup gate refused')
        return real_gate(item, *args, **kwargs)

    monkeypatch.setattr(store, 'PUBLICATION_WAIT', 0)
    monkeypatch.setattr(os, 'replace', held)
    monkeypatch.setattr(store, operation, refused)
    with pytest.raises(PermissionError, match='restore backup gate refused'):
        store.write_retained(target, b'{"kept":"next"}')
    assert not list(tmp_path.glob('state.json.*.new'))
    assert not list(tmp_path.glob('*.cleanup'))


def test_repair1_post_send_restore_keeps_the_save_error_and_count(tmp_path, monkeypatch, key_file):
    from g6.mcp import review_loop
    from tests.harness.test_completion_fu3_repair2 import Session

    world = Session(tmp_path, monkeypatch, key_file, 'mcp:repair1-after-send')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    real_scout, real_replace = loop._scout, os.replace
    sent = []

    def scout(*args):
        result = real_scout(*args)
        sent.append(True)
        return result

    def held(source, destination):
        if sent and Path(destination) in (world.folder / 'state.json', world.folder / 'state.sha256'):
            # Allow new content, but refuse both receipt publication and prior-content restoration.
            if Path(destination).suffix == '.sha256' or len(Path(source).name) > len('state.json.new'):
                raise PermissionError(errno.EACCES, 'post-send save refused')
        return real_replace(source, destination)

    monkeypatch.setattr(loop, '_scout', scout)
    monkeypatch.setattr(store, 'PUBLICATION_WAIT', 0)
    with monkeypatch.context() as fault:
        fault.setattr(os, 'replace', held)
        reply = review_loop.step(str(world.repo), 'task', 'repair1-after-send', 'base')
    assert 'post-send save refused' in reply['error']
    assert 'This request was not sent' not in reply['error']
    assert 'Retry this call' not in reply['error']
    assert reply.get('code') != loop.RESERVATION_UNSAVED
    assert reply['requests_sent'] == 1 and len(world.reviewed) == 1


def test_repair1_refused_destination_read_cannot_prevent_a_restore_record(tmp_path, monkeypatch):
    target, prior = tmp_path / 'state.json', b'{"kept":"prior"}'
    store.write_retained(target, prior)
    real_replace, real_read = os.replace, Path.read_bytes
    replaced = []

    def held(source, destination):
        if Path(destination) == target.with_suffix('.sha256') or replaced and Path(destination) == target:
            raise PermissionError(errno.EACCES, 'pair held')
        result = real_replace(source, destination)
        if Path(destination) == target:
            replaced.append(True)
        return result

    def no_read(item):
        if replaced and item == target:
            raise PermissionError(errno.EACCES, 'content shares no reads')
        return real_read(item)

    with monkeypatch.context() as fault:
        fault.setattr(store, 'PUBLICATION_WAIT', 0)
        fault.setattr(os, 'replace', held)
        fault.setattr(Path, 'read_bytes', no_read)
        with pytest.raises(PermissionError):
            store.write_retained(target, b'{"kept":"next"}')
        assert list(tmp_path.glob('*.cleanup')), 'recovery must be recorded without reading the refused destination'
    assert loop._load(tmp_path)['kept'] == 'prior'


@pytest.mark.parametrize('access', ['task_present', 'task_path', 'retained_paths', 'load'])
def test_repair1_first_publication_recovers_before_presence_and_paths(tmp_path, monkeypatch, access):
    target = tmp_path / ('state.json' if access == 'load' else 'task.json')
    real_replace, real_unlink = os.replace, store.unlink

    def held(source, destination):
        if Path(destination) == target.with_suffix('.sha256'):
            raise PermissionError(errno.EACCES, 'first receipt held')
        return real_replace(source, destination)

    def held_unlink(item, **kwargs):
        if Path(item) in (target, target.with_suffix('.sha256').with_name(target.stem + '.sha256.new')):
            raise PermissionError(errno.EACCES, 'first publication held')
        return real_unlink(item, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(store, 'PUBLICATION_WAIT', 0)
        fault.setattr(os, 'replace', held)
        fault.setattr(store, 'unlink', held_unlink)
        with pytest.raises(PermissionError):
            store.write_retained(target, _task('first'))
    if access == 'load':
        assert loop._load(tmp_path) == {}
    else:
        if access == 'task_present':
            assert not store.task_present(target)
        elif access == 'task_path':
            assert store.task_path(target) == target
        else:
            assert not any(item.exists() for item in store.retained_paths(target))
        assert not target.exists(), 'path access must finish the absent-pair restore first'
    assert not list(tmp_path.glob('*.new'))
    assert not list(tmp_path.glob('*.cleanup'))


def test_repair1_class_loop_cannot_borrow_the_modules_gate_binding():
    assert unguarded_calls('from g6.harness import _completion_storage as store\n'
                           'class Run:\n    for turn in range(2):\n        store.unlink(p)\n        import os as store\n')


@pytest.mark.parametrize('binding', [
    'text = os\ntext.replace(p, q)\n',
    'mods = [os]\nmods[0].replace(p, q)\n',
    'mods = []\nmods += [os]\nmods[0].replace(p, q)\n',
    'def go(text=os):\n    text.replace(p, q)\n',
    'for text in [os]:\n    text.replace(p, q)\n',
    'text = [os][0]\ntext.replace(p, q)\n',
])
def test_repair1_module_values_cannot_use_the_text_replace_allowance(binding):
    assert unguarded_calls('import os\n' + binding)


@pytest.mark.parametrize('value', ['getattr', '__import__', 'exec', 'eval', 'compile', 'importlib.import_module'])
def test_repair1_dynamic_operations_are_refused_as_values(value):
    prefix = 'import importlib\n' if value.startswith('importlib.') else ''
    assert unguarded_calls(prefix + f'make = {value}\n')


@pytest.mark.parametrize('surface', ['mcp', 'stop'])
@pytest.mark.parametrize('read_at', ['body', 'reply'])
def test_repair1_amendment1_read_after_send_has_neutral_words_and_true_count(
        tmp_path, monkeypatch, key_file, surface, read_at):
    from g6.harness import completion_hook as hook
    from g6.mcp import review_loop
    from tests.harness.test_completion_fu3_repair2 import Session

    world = Session(tmp_path, monkeypatch, key_file, ('mcp:' if surface == 'mcp' else '') + 'amendment1-read')
    if surface == 'stop':
        hook.on_prompt({**world.event, 'prompt': 'task'})
    else:
        store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    real_scout = loop._scout

    def pending():
        return store._RestorePending()

    def scout(*args):
        result = real_scout(*args)
        if read_at == 'body':
            raise pending()
        return result

    def reply_state(session):
        assert world.reviewed
        raise pending()

    monkeypatch.setattr(loop, '_scout', scout)
    if read_at == 'reply':
        monkeypatch.setattr(loop, 'reply_state', reply_state)
    reply = (review_loop.step(str(world.repo), 'task', 'amendment1-read', 'base') if surface == 'mcp'
             else hook.on_stop(world.event))
    text = reply['error'] if surface == 'mcp' else reply['systemMessage']
    assert 'This request was not sent' not in text
    assert 'Retry this call' not in text
    assert loop.RESERVATION_UNSAVED not in text
    assert reply.get('code') != loop.RESERVATION_UNSAVED
    sent = len(world.reviewed)
    assert sent >= 1
    if surface == 'mcp':
        assert reply['requests_sent'] == sent
    else:
        assert f'Up to {sent} request(s)' in text


def test_repair1_amendment1_later_reservation_keeps_its_own_error(tmp_path, monkeypatch):
    from g6.core.model_calls import note_model_call

    loop._save(tmp_path, {loop.ATTEMPTED: 4})
    with loop._run_count(tmp_path, loop._load(tmp_path), fresh=False):
        note_model_call()

        def refused(*args, **kwargs):
            raise PermissionError(errno.EACCES, 'second reservation refused')

        monkeypatch.setattr(loop, '_save', refused)
        with pytest.raises(OSError, match='second reservation refused') as caught:
            note_model_call()
        assert getattr(caught.value, 'code', None) != loop.RESERVATION_UNSAVED
        assert 'This request was not sent' not in str(caught.value)
        assert 'Retry this call' not in str(caught.value)
        assert caught.value.state[loop.ATTEMPTED] == 5


@pytest.mark.parametrize('spare_name', ['state.json.new', 'state.sha256.new', 'task-1.json.new'])
def test_repair1_amendment2_reads_preserve_spares_no_restore_marker_owns(tmp_path, spare_name):
    spare = tmp_path / spare_name
    body = b'interrupted first publication'
    spare.write_bytes(body)
    identity = spare.stat().st_dev, spare.stat().st_ino
    target = tmp_path / ('task.json' if spare_name.startswith('task-') else 'state.json')
    for _ in range(2):
        if spare_name.startswith('state.'):
            state = loop._load(tmp_path)
            assert isinstance(state, loop._RetainedState) and not state.readable
            assert state['code'] == loop.NO_CLEAN_REVIEW and loop.attempted(state) is None
        else:
            assert store.task_path(target) == tmp_path / 'task-1.json'
            assert store.task_present(target)
            with pytest.raises(store.TaskUnreadable):
                store.read_task(target)
        store.retained_paths(tmp_path / spare_name.removesuffix('.new'))
        assert spare.read_bytes() == body
        assert (spare.stat().st_dev, spare.stat().st_ino) == identity


def test_repair2_prompt_prints_the_send_bound_before_a_long_storage_path(monkeypatch, capsys):
    import io
    from types import SimpleNamespace

    from g6.harness import completion_hook as hook

    error = loop._call_read_error(
        PermissionError(errno.EACCES, 'publication held', 'C:/' + 'deep/' * 80 + 'state.json'),
        {loop.ATTEMPTED: 1, 'call_requests': 1})

    def refused(event):
        raise error

    monkeypatch.delenv(hook.CHILD, raising=False)
    monkeypatch.setitem(hook.HOOKS, 'prompt', refused)
    monkeypatch.setattr(hook.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}')))
    assert hook.main(['prompt']) == 1
    shown = capsys.readouterr().err.strip()
    count = 'Up to 1 request(s) of this run may have gone out.'
    assert count in shown
    assert 'not sent' not in shown
    assert len(shown) <= 300
    assert 'C:/' not in shown or shown.index(count) < shown.index('C:/')


def test_repair2_text_replace_requires_a_proven_string_receiver():
    assert not unguarded_calls("shown = 'text'.replace('a', 'b')\n")
    assert not unguarded_calls("shown = text.replace('a', 'b')\n")
    assert not unguarded_calls("shown = parts[0].replace('a', 'b')\n")
    assert not unguarded_calls("text = str(value)\nshown = text.replace('a', 'b')\n")
    assert (2, 'text.replace') in unguarded_calls("text = pick()\nshown = text.replace('a', 'b')\n")
    assert (2, 'parts[0].replace') in unguarded_calls("parts = list(values)\nshown = parts[0].replace('a', 'b')\n")
