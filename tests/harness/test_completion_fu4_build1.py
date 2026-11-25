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
