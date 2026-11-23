# SPDX-License-Identifier: Apache-2.0
"""Run accounting survives every send/checkpoint boundary and competing writers."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud
from g6.core.model_calls import note_model_call
from g6.harness import completion_loop as loop
from g6.workspace._envelope import WorkspaceRefused


@pytest.fixture
def run_store(monkeypatch):
    root = Path.cwd()
    session = root / '.build' / 'virtual-count-run'
    files = {}
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: None)
    monkeypatch.setattr(Path, 'write_bytes', lambda p, b: files.__setitem__(p, b))
    monkeypatch.setattr(cloud, 'write_retained', lambda p, b: files.__setitem__(p, b))
    monkeypatch.setattr(loop, '_sync_state', lambda *a: None)
    def load(p):
        return json.loads(files.get(p / 'state.json', b'{}'))

    monkeypatch.setattr(loop, '_load', load)
    mutex = threading.Lock()

    @contextmanager
    def held(p):
        if not mutex.acquire(blocking=False):
            raise WorkspaceRefused('G6_E_WORKSPACE_BUSY', 'step already running')
        try:
            yield
        finally:
            mutex.release()

    monkeypatch.setattr(loop, '_held', held)
    monkeypatch.setattr(loop, 'settle', lambda: None)
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda *a: root)
    monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: SimpleNamespace(repo=root, sources=('code.py',)))
    monkeypatch.setattr(loop.repo_tests, '_git', lambda *a: b'code.py\0')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: True)
    monkeypatch.setattr(loop, '_before_fix', lambda: True)
    monkeypatch.setattr(loop, '_told', lambda *a: '')
    monkeypatch.setattr(loop, '_report', lambda *a: 'No sets ran.')
    monkeypatch.setattr(loop, '_patch_now', lambda *a: 'patch')
    monkeypatch.setattr(loop, 'harness', lambda: SimpleNamespace(scout='one', hidden_scouts=('two', 'three'), sources=()))
    return session, load


class Crash(RuntimeError):
    pass


@pytest.mark.parametrize('writer', ['prepare', 'advance'])
@pytest.mark.parametrize('nth', [1, 2, 3])
@pytest.mark.parametrize('point', ['send', 'before_checkpoint', 'after_checkpoint'])
def test_crash_at_each_send_and_checkpoint_keeps_upper_bound(run_store, monkeypatch, writer, nth, point):
    session, load = run_store
    sends = 0
    original = cloud.write_retained

    def write(path, body):
        if sends == nth and point == 'before_checkpoint':
            raise Crash('before checkpoint')
        original(path, body)
        saved = load(session).get('attempted')
        assert type(saved) is int and saved >= sends, (saved, sends)
        if sends == nth and point == 'after_checkpoint':
            raise Crash('after checkpoint')

    monkeypatch.setattr(cloud, 'write_retained', write)

    def scout(*a):
        nonlocal sends
        note_model_call()
        sends += 1
        assert load(session).get('attempted', -1) >= sends, 'send left before its reservation'
        if sends == nth and point == 'send':
            raise Crash('after send')
        return {'left_out': 'host finished'}

    monkeypatch.setattr(loop, '_scout', scout)
    with pytest.raises(Crash):
        getattr(loop, writer)(Path.cwd(), 'task', 'base', session, scout=object())
    assert load(session)['attempted'] >= sends
    assert sends == nth


@pytest.mark.parametrize('writer', ['prepare', 'advance'])
@pytest.mark.parametrize('save_point', ['before', 'after'])
@pytest.mark.parametrize('ordinal', [1, 2, 3, 4, 5, 6, 7])
def test_competing_writer_at_each_save_cannot_erase_count_or_review(
        run_store, monkeypatch, writer, save_point, ordinal):
    session, load = run_store
    loop._save(session, {'attempted': 4, 'requests_sent': 4, 'sets': {},
                         'reviews': [{'patch': 'prior', 'verdict': 'defect'}]}, sources=())
    entered, release = threading.Event(), threading.Event()
    writes = 0
    original = cloud.write_retained

    def write(path, body):
        nonlocal writes
        writes += 1
        selected = writes == ordinal
        if selected and save_point == 'before':
            entered.set()
            assert release.wait(5)
        original(path, body)
        if selected and save_point == 'after':
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(cloud, 'write_retained', write)

    def scout(*a):
        note_model_call()
        return {'left_out': 'host finished'}

    monkeypatch.setattr(loop, '_scout', scout)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(getattr(loop, writer), Path.cwd(), 'task', 'base', session, scout=object())
        assert entered.wait(5)
        try:
            blocked = loop.advance(Path.cwd(), 'task', 'base', session, scout=object())
            assert blocked.code == loop.BUSY
            with pytest.raises(WorkspaceRefused, match='step already running'):
                loop.prepare(Path.cwd(), 'task', 'base', session, scout=object())
        finally:
            release.set()
        future.result(timeout=5)
    state = load(session)
    assert state['attempted'] == 7
    assert state['reviews'] == [{'patch': 'prior', 'verdict': 'defect'}]


@pytest.mark.parametrize('proposed', [0, 2, None])
def test_stale_save_never_lowers_attempts_or_loses_review(run_store, proposed):
    session, load = run_store
    loop._save(session, {'attempted': 6, 'reviews': [{'patch': 'new'}],
                         'row_due': '{"session": "owed"}', 'row': 'log is held'}, sources=())
    stale = {'reviews': []}
    if proposed is not None:
        stale['attempted'] = proposed
    loop._save(session, stale, sources=())
    state = load(session)
    assert state['attempted'] == 6
    assert state['reviews'] == [{'patch': 'new'}]
    assert state['row_due'] == '{"session": "owed"}' and state['row'] == 'log is held'


@pytest.mark.parametrize('saved', [{'requests_sent': 0}, {'attempted': True}, {'attempted': -1}, {}])
def test_workflow_reader_requires_present_attempted_zero(monkeypatch, saved):
    from g6.harness.workflow import adapters
    events = []

    class Run:
        def __init__(self, *a, **k):
            pass

        def write(self, kind, *a, **kwargs):
            events.append((kind, kwargs.get('data')))

        def seconds(self):
            return 0

    monkeypatch.setattr(adapters, '_Run', Run)
    monkeypatch.setattr(adapters, '_report_ref', lambda *a: ())
    step = SimpleNamespace(action='stop', ending='untested', code=cloud.REQUIRED)
    adapters.observe_loop.__wrapped__(Path.cwd(), 'task', Path('run'), saved, step, at='now')
    ended = next(data for kind, data in events if kind == 'loop_ended')
    assert ended['requests_sent'] is None


def test_threaded_and_late_sends_reserve_from_current_state(run_store):
    session, load = run_store
    state = {}
    gate = threading.Event()
    with loop._writing(session):
        with loop._run_count(session, state, fresh=True):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: note_model_call(), range(16)))
            late = threading.Thread(target=lambda: (gate.wait(5), note_model_call()))
            late.start()
    loop._save(session, {**load(session), 'attempted': 20, 'reviews': [{'patch': 'later'}]}, sources=())
    gate.set()
    late.join(5)
    assert not late.is_alive()
    assert load(session)['attempted'] == 21
    assert load(session)['reviews'] == [{'patch': 'later'}]


def test_failed_reservation_flush_prevents_dispatch(run_store, monkeypatch):
    session, load = run_store
    sends = []

    def failed(*a):
        raise OSError('storage flush failed')

    monkeypatch.setattr(loop, '_sync_state', failed)
    with loop._writing(session), loop._run_count(session, {}, fresh=True):
        with pytest.raises(OSError, match='storage flush failed'):
            note_model_call()
            sends.append('sent')
    assert sends == []
    assert load(session)['attempted'] == 1


def test_legacy_run_never_acquires_a_known_total(run_store):
    session, load = run_store
    loop._save(session, {'sets': {}, 'requests_sent': 0}, sources=())
    with loop._writing(session), loop._run_count(session, load(session), fresh=False):
        note_model_call()
    loop._save(session, {'attempted': 0}, sources=())
    assert 'attempted' not in load(session)
    assert 'requests_sent' not in load(session)

@pytest.mark.parametrize('write_point', [1, 2, 3, 4])
@pytest.mark.parametrize('edge', ['before', 'after'])
def test_review_end_crash_at_every_write_keeps_send_bound(run_store, monkeypatch, write_point, edge):
    session, load = run_store
    sends = 0
    writes = 0
    original = cloud.write_retained
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)

    def write(path, body):
        nonlocal writes
        writes += 1
        if writes == write_point and edge == 'before':
            raise Crash('before review write')
        original(path, body)
        assert load(session)['attempted'] >= sends
        if writes == write_point and edge == 'after':
            raise Crash('after review write')

    def send():
        nonlocal sends
        note_model_call()
        sends += 1
        assert load(session)['attempted'] >= sends

    def review(change, task, critic, shipped, folder, reviews, deadline):
        send()
        reviews.append({'patch': 'patch', 'verdict': 'none', 'results': {}, 'file': 't.py',
                        'rule': 'rule', 'why': 'none', 'test': '', 'told': ''})
        return set(), ''

    monkeypatch.setattr(cloud, 'write_retained', write)
    monkeypatch.setattr(loop, 'review_round', review)
    monkeypatch.setattr(loop, '_final_review', lambda *a: (send(), 'review')[1])
    with pytest.raises(Crash):
        loop.advance(Path.cwd(), 'task', 'base', session, critic=object())
    saved = load(session)
    if saved:
        assert saved['attempted'] >= sends
    else:
        assert sends == 0
    assert writes == write_point


def test_scout_crash_resumed_mcp_never_says_unsent(run_store, monkeypatch):
    from g6.core.ledger import digest
    from g6.core.llm import LLMError
    from g6.harness import completion_hook
    from g6.harness.workflow.obstacle import obstacles
    from g6.mcp import review_loop
    from g6.mcp.cloud_consent import withheld_reply

    session, load = run_store
    monkeypatch.setattr(loop, '_owe_row', lambda *a, **k: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    monkeypatch.setattr(completion_hook, 'folder', lambda *a: session)
    monkeypatch.setattr(review_loop, '_asked', lambda *a:
                        {'repo': str(Path.cwd()), 'task': 'task', 'base': 'base'})

    def scout(change, task, text, *a):
        if text == 'one':
            return {'left_out': 'host did not launch'}
        note_model_call()
        raise Crash('after second scout sent')

    monkeypatch.setattr(loop, '_scout', scout)
    with pytest.raises(Crash):
        loop.advance(Path.cwd(), 'task', 'base', session, scout=object())
    assert load(session)['attempted'] == 1

    def refused(*a):
        raise LLMError(f'{cloud.REQUIRED}: sharing revoked')

    monkeypatch.setattr(loop, '_scout', refused)
    monkeypatch.setattr(loop, 'scout_host', lambda: object())
    with cloud.collect_sources() as capture:
        reply = review_loop.step(str(Path.cwd()), 'task', 'session', 'base')
        metadata = cloud.output_metadata(reply)
    job = {'digest': digest(reply), 'job_id': 'job', 'status': 'completed',
           'metadata': metadata, 'review_unsent': capture.review_unsent}
    rebuilt = withheld_reply(reply, job=job)['result']
    assert reply['requests_sent'] == 1
    assert obstacles(None, reply, ())[0].kind == 'refused_run'
    assert not capture.review_unsent
    assert 'not sent' not in reply['report'].lower()
    assert 'not sent' not in rebuilt['report'].lower()

@pytest.mark.parametrize('count', [None, 2])
def test_retained_consent_report_cannot_replay_an_unsent_claim(monkeypatch, count):
    session = Path('.build') / 'old-report'
    saved = {'done': True, 'code': cloud.REQUIRED, 'stop': 'The review was not sent.',
             'report_sha256': 'digest', 'requests_sent': 0}
    if count is not None:
        saved['attempted'] = count
    monkeypatch.setattr(Path, 'is_file', lambda *a: True)
    monkeypatch.setattr(Path, 'read_text', lambda *a, **k: 'digest')
    monkeypatch.setattr(cloud, 'read_retained', lambda p, d: json.dumps(saved).encode()
                        if p.name == 'state.json' else b'The review was not sent.')
    state = loop._load(session)
    assert 'not sent' not in state['stop'].lower()
    assert 'not sent' not in loop.read_report(session, state).lower()


def test_reservation_flush_uses_handles_that_allow_writing(monkeypatch):
    """Windows FlushFileBuffers rejects file handles opened with read access alone."""
    flushed = []

    class Stream:
        def __init__(self, mode):
            self.writable = '+' in mode

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def fileno(self):
            return int(self.writable)

    def flush(fd):
        if fd == 0:
            raise OSError('flush requires a writable handle')
        flushed.append(fd)

    monkeypatch.setattr(Path, 'open', lambda p, mode: Stream(mode))
    monkeypatch.setattr(loop.os, 'fsync', flush)
    monkeypatch.setattr(loop.os, 'open', lambda *a: 2)
    monkeypatch.setattr(loop.os, 'close', lambda *a: None)
    loop._sync_state(Path('.build') / 'flush-run')
    assert len(flushed) >= 2

@pytest.mark.parametrize('retained', [None, {'attempted': 9, 'reviews': [{'patch': 'retained'}]}])
def test_hook_new_run_uses_step_lock_and_never_resets_an_existing_run(run_store, monkeypatch, retained):
    from g6.harness import completion_hook as hook

    session, load = run_store
    asked = {'repo': str(Path.cwd()), 'base': 'base', 'task': 'prior task'}
    monkeypatch.setattr(hook, 'folder', lambda *a: session.parent)
    monkeypatch.setattr(hook, 'current', lambda *a: session)
    monkeypatch.setattr(hook, '_here', lambda *a: (Path.cwd(), 'base'))
    if retained is not None:
        loop._save(session, retained, sources=())
    event = {'session_id': 'session', 'cwd': str(Path.cwd())}
    with loop._held(session):
        with pytest.raises(WorkspaceRefused):
            hook._again(event, asked, {'done': True}, 'new task')
    assert load(session) == (retained or {})
    assert hook._again(event, asked, {'done': True}, 'new task')
    state = load(session)
    assert state['attempted'] == (9 if retained else 0)
    assert state.get('reviews', []) == ([{'patch': 'retained'}] if retained else [])


def test_fresh_refused_run_has_a_present_zero(run_store, monkeypatch):
    from g6.core.llm import LLMError

    session, load = run_store
    monkeypatch.setattr(loop, '_owe_row', lambda *a, **k: None)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)

    def refused(*a):
        raise LLMError(f'{cloud.REQUIRED}: sharing refused')

    monkeypatch.setattr(loop, '_scout', refused)
    step = loop.advance(Path.cwd(), 'task', 'base', session, scout=object())
    assert load(session)['attempted'] == 0
    assert step.text.startswith('The review was not sent.')

@pytest.mark.parametrize('ordinal', [1, 2, 3, 4, 5])
@pytest.mark.parametrize('edge', ['before', 'after'])
def test_review_writer_interleavings_preserve_new_and_prior_reviews(run_store, monkeypatch, ordinal, edge):
    session, load = run_store
    prior = {'patch': 'prior', 'verdict': 'none', 'results': {}, 'file': '',
             'rule': 'rule', 'why': 'none', 'test': '', 'told': ''}
    current = {**prior, 'patch': 'patch'}
    loop._save(session, {'attempted': 4, 'reviews': [prior]}, sources=())
    monkeypatch.setattr(loop, '_scouts_chosen', lambda: False)
    monkeypatch.setattr(loop, '_write_row', lambda *a: None)
    entered, release = threading.Event(), threading.Event()
    writes = 0
    original = cloud.write_retained

    def write(path, body):
        nonlocal writes
        writes += 1
        if writes == ordinal and edge == 'before':
            entered.set()
            assert release.wait(5)
        original(path, body)
        if writes == ordinal and edge == 'after':
            entered.set()
            assert release.wait(5)

    def review(change, task, critic, shipped, folder, reviews, deadline):
        note_model_call()
        note_model_call()
        reviews.append(current)
        return set(), ''

    monkeypatch.setattr(cloud, 'write_retained', write)
    monkeypatch.setattr(loop, 'review_round', review)
    monkeypatch.setattr(loop, '_final_review', lambda *a: (note_model_call(), 'review')[1])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(loop.advance, Path.cwd(), 'task', 'base', session, critic=object())
        assert entered.wait(5)
        try:
            blocked = loop.advance(Path.cwd(), 'task', 'base', session, critic=object())
            assert blocked.code == loop.BUSY
            with pytest.raises(WorkspaceRefused):
                loop.prepare(Path.cwd(), 'task', 'base', session, scout=object())
        finally:
            release.set()
        assert future.result(timeout=5).action == loop.STOP
    assert load(session)['attempted'] == 7
    assert load(session)['reviews'] == [prior, current]
