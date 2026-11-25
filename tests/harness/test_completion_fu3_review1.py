# SPDX-License-Identifier: Apache-2.0
"""A failed publication owns only its bytes; runner failures supply no test evidence."""
import threading
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import pytest

from g6.core import cloud_consent as cloud
from g6.harness import _completion_storage as store, completion_loop as loop
from tests.harness.test_completion_writer_allowlist import unguarded_calls


@pytest.mark.parametrize('filesystem', ['disk'])
def test_first_report_finish_with_a_fresh_home(tmp_path, monkeypatch, filesystem):
    home = tmp_path / 'fresh-home'
    monkeypatch.setenv('G6_HOME', str(home))
    session = home / 'completion_loop' / 'first-report'
    assert not home.exists()
    monkeypatch.setattr(loop, '_save', lambda *args, **kwargs: None)
    state = {}

    step = loop._finish(session, state, 'First report.', 'ending-code', 'untested', patch='patch')

    report = session / 'report.md'
    assert report.read_bytes() == b'First report.'
    assert step.text == 'First report.'
    assert step.code == 'ending-code' and step.ending == 'untested'
    assert step.call_ran and step.settled_here
    assert state == {'done': True, 'code': 'ending-code', 'ending': 'untested',
                     'ended_patch': 'patch', 'report_sha256': cloud.sha256(b'First report.')}


@pytest.mark.parametrize('kind', ['retained', 'task', 'raw'])
def test_first_write_creates_the_destination_before_locking(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    target = store.home_path('completion_loop', 'new-run', 'task.json')
    held = []
    original_mutex = store.Coordination.mutex

    @contextmanager
    def checked_mutex(coordination, *args, **kwargs):
        assert target.parent.is_dir(), 'the destination folder must precede its lock'
        assert coordination.folder.is_dir(), 'the mutex folder must already exist'
        with original_mutex(coordination, *args, **kwargs):
            held.append(coordination.folder)
            yield

    monkeypatch.setattr(store.Coordination, 'mutex', checked_mutex)
    if kind == 'raw':
        store.write_bytes(target, b'first publication')
        assert target.read_bytes() == b'first publication'
    else:
        if kind == 'task':
            with store.task_publication(target):
                store.write_retained(target, b'first publication')
        else:
            store.write_retained(target, b'first publication')
        assert cloud.read_retained(target, target.with_suffix('.sha256').read_text()) == b'first publication'
    assert held


def test_absent_staging_cleanup_preserves_the_preparation_error(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    target = store.home_path('completion_loop', 'new-run', 'task.json')
    original_mkdir = store.mkdir
    staging = []

    def fail_preparation(folder, **kwargs):
        if folder.parent.name == 'completion_staging':
            staging.append(folder)
            raise OSError('preparation never created its folder')
        return original_mkdir(folder, **kwargs)

    monkeypatch.setattr(store, 'mkdir', fail_preparation)
    with pytest.raises(OSError, match='^preparation never created its folder$'):
        with store.task_publication(target):
            store.write_retained(target, b'not published')
    assert len(staging) == 1 and not staging[0].exists()
    assert not any(item.exists() for item in store.retained_paths(target))
    store.remove_tree(staging[0])
    store.unlink(staging[0] / 'task.json.new', missing_ok=True)
    assert not staging[0].exists(), 'absent cleanup must not create a folder'


def test_task_publication_keeps_the_two_argument_memory_writer_seam(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('G6_HOME', str(home))
    target = store.home_path('completion_loop', 'new-run', 'task.json')
    written = {}
    monkeypatch.setattr(Path, 'mkdir', lambda *args, **kwargs: None)
    monkeypatch.setattr(Path, 'write_bytes', lambda item, body: written.__setitem__(item, body))
    monkeypatch.setattr(cloud, 'write_retained', lambda item, body: written.__setitem__(item, body))
    with store.task_publication(target):
        store.write_retained(target, b'first publication')
    assert written[target] == b'first publication'


@pytest.mark.parametrize('replacement', ['neither', 'content', 'receipt', 'both'])
def test_task_rollback_preserves_another_producers_bytes(tmp_path, replacement):
    target = tmp_path / 'task.json'
    receipt = target.with_suffix('.sha256')
    store.write_retained(target, b'original')
    errors = []

    def publish():
        try:
            if replacement == 'both':
                store.write_retained(target, b'newer producer')
            elif replacement == 'content':
                store.write_bytes(target, b'newer producer')
            elif replacement == 'receipt':
                store.write_bytes(receipt, b'another receipt')
        except BaseException as exc:
            errors.append(exc)

    with pytest.raises(OSError, match='older attempt failed'):
        with store.task_publication(target):
            store.write_retained(target, b'older attempt')
            worker = threading.Thread(target=publish)
            worker.start()
            worker.join(5)
            assert not worker.is_alive()
            assert not errors
            before = target.read_bytes(), receipt.read_bytes()
            raise OSError('older attempt failed')
    if replacement == 'neither':
        assert cloud.read_retained(target, receipt.read_text()) == b'original'
    else:
        assert (target.read_bytes(), receipt.read_bytes()) == before


@pytest.mark.parametrize('runner', ['no-exit', 'bad-exit', 'refused', 'failed-test'])
def test_scouts_row_requires_a_recorded_failed_test(tmp_path, runner):
    run = (loop.ProbeRun(1, {'test_p.py::test_value': 'failed'}, {}) if runner == 'failed-test' else
           loop.ProbeRun(None if runner != 'bad-exit' else 2, {}, {}, 'runner unavailable'))
    failing, text = loop.failures_of([('scout', run, None)])
    state = {'stop': loop.LEFT, 'rounds': [{'failing': sorted(failing), 'text': text, 'tree': None}],
             'ran': {'scout': run.ran}, 'probe_runs': [{'scout': asdict(run)}]}
    row = loop._failure_row('task', 'base', tmp_path, state, '', scouts=True)
    assert row['independently_observed'] is (runner == 'failed-test')


def test_legacy_scouts_round_without_a_run_has_no_observed_test(tmp_path):
    state = {'stop': loop.LEFT, 'rounds': [{'failing': ['scout:test_p.py::test_value']}], 'ran': {'scout': 1}}
    assert not loop._failure_row('task', 'base', tmp_path, state, '', scouts=True)['independently_observed']


def test_scouts_report_keeps_the_parent_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, '_final_review', lambda *args: 'Final review stand-in.')
    monkeypatch.setattr(loop, '_before_fix', lambda: False)
    state = {'rounds': [{'failing': ['scout:no exit'], 'tree': None}], 'ran': {'scout': 0},
             'sets': {'scout': {'left_out': '', 'refused': []}},
             'probe_runs': [{'scout': asdict(loop.ProbeRun(None, {}, {}, 'runner unavailable'))}]}
    report = loop._report(tmp_path, 'task', 'base', state, loop.LEFT)
    expected = loop.patch_report.completion_report({
        'reason': loop.LEFT, 'repairs': 0, 'max_repairs': loop.MAX_REPAIRS,
        'failing': ['scout:no exit'], 'critic': None,
        'late_because': loop.NOT_ASKED, 'boundary': None,
        'sets': [{'label': 'scout', 'hidden': False, 'ran': 0, 'left_out': '', 'refused': [], 'late': True}],
    }, 'Final review stand-in.')
    assert report == expected
    assert 'scout:no exit' in report


@pytest.mark.parametrize('case', ['critic:test_critic.py::test_x', 'critic:no exit'])
def test_critic_message_keeps_the_parent_text(case):
    critic = {'rule': 'the rule', 'why': 'the why', 'test': 'def test_x():\n    assert False'}
    assert loop.with_critic(({case}, 'fails'), critic, loop.harness().critic_note) == ({case}, (
        'A reviewer from another team read your patch, without seeing your reasoning or how you got there, and says:\n\n'
        'the rule\n\nthe why\n\nTheir test, which fails on your code:\n\n'
        '```python\ndef test_x():\n    assert False\n```\n\n'
        'They can be wrong, as anyone can. Check their expectation against the issue and the repository before you act on it.'
        '\n\nfails'))


@pytest.mark.parametrize('source', [
    'host, = (p.write_text,)',
    'host, = [p.write_text]',
    'host, *rest = [p.write_text]',
    'host = (*[p.write_text],)',
    '(host := p.write_text)',
    'host += [p.write_text]',
    'for host in [p.write_text]: pass',
    'with manager(p.write_text) as host: pass',
    'def callback(host=p.write_text): pass',
    'host = lambda: p.write_text',
    'host = [p.write_text for p in paths]',
    'host = {"write": p.write_text}',
    'host(p.write_text)',
    'from os import truncate as host\ncallback(host)',
    'import os as fs\nhost = fs.replace',
    'from g6.harness import _completion_storage as store\nhost = store.write_retained',
    'host, = (p.future_filesystem_method,)',
    'from g6.harness import _completion_storage as store\nhost = (store.future_writer,)',
])
def test_inventory_refuses_writer_references_in_every_container(source):
    assert unguarded_calls(source)
