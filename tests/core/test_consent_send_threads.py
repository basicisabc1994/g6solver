# SPDX-License-Identifier: Apache-2.0
"""A send made inside an admitted send, on a thread the send's own step started with a copy of its context: it is checked
as a send nested on the send's own thread is, under the same hold, and never waits for the send it is made inside of.
Only such a context counts as inside the send, and only while the send is out. No model is called: each send is the
sharing check (``cloud_consent.admission``) of literal inputs its caller supplied, with a body of the test's own."""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest

from g6.core import cloud_consent as c
from g6.core import model_calls
from g6.core.llm import LLMError

P, OTHER = 'cloud:fixture', 'cloud:fixture-other'
#: long enough for a thread that does not wait to finish; a thread still running after it is waiting
SETTLE = 0.3


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))


def literal(text):
    """A caller-supplied literal input: shared with any recipient until a revoke names it."""
    return c.Source.text(c.input_source(text), 'item', text)


A, B = literal('first input'), literal('second input')
UNSHARED = c.Source.text('fixture-repository', 'file', 'content')


def send(provider, *sources, body=None):
    """One admitted send of ``sources`` to ``provider``, the literal ones supplied by the caller; ``body`` runs while it is
    out."""
    supplied = [s.source_id for s in sources if s.source_id.startswith('input:')]
    with c.admission(provider, sources, c.sha256('payload'), supplied=supplied):
        return body() if body is not None else 'sent'


def outcome(fn):
    try:
        fn()
    except LLMError as error:
        assert c.REQUIRED in str(error), error
        return 'refused'
    return 'sent'


def started(fn, *, context=None, name=None):
    """``fn`` on a thread of its own, in ``context`` (none: a fresh thread with an empty context); its results in a list."""
    results = []

    def run():
        results.append(fn())

    thread = threading.Thread(target=context.run if context is not None else run,
                              args=(run,) if context is not None else (), name=name)
    thread.start()
    return thread, results


def kinds():
    return [json.loads(line)['kind'] for line in c.ledger_path().read_text(encoding='utf-8').splitlines()]


CASES = {
    'the same recipient and sources': (lambda: send(P, A), 'sent', 0),
    'another recipient': (lambda: send(OTHER, A), 'sent', 1),
    'a source the enclosing send did not disclose': (lambda: send(P, A, B), 'sent', 1),
    'a source not shared': (lambda: send(P, UNSHARED), 'refused', 0),
    'an unknown recipient': (lambda: send('cloud:unknown', A), 'refused', 0),
    'its source revoked first': (lambda: (c.revoke(A.source_id, P), send(P, A))[1], 'refused', 0),
}


@pytest.mark.parametrize('where', ['on the send\'s own thread', 'on a thread of its own'])
@pytest.mark.parametrize('case', list(CASES))
def test_a_send_inside_a_send_is_decided_as_on_one_thread_and_never_waits_for_it(case, where):
    inner, expected, disclosed = CASES[case]
    got = []

    def body():
        if where == 'on a thread of its own':
            thread, results = started(lambda: outcome(inner), context=contextvars.copy_context())
            thread.join(5)
            assert not thread.is_alive(), 'the send inside waited for the send it was made inside of'
            got.extend(results)
        else:
            got.append(outcome(inner))

    send(P, A, body=body)
    assert got == [expected]
    assert len(c.disclosures()) == 1 + disclosed


def test_a_fresh_thread_or_a_context_copied_before_the_send_takes_the_lock_and_waits():
    before = contextvars.copy_context()
    waiting = []

    def body():
        assert c.admitted(P)
        for context in (None, before):
            thread, results = started(lambda: (c.admitted(P), send(P, A)), context=context)
            thread.join(SETTLE)
            assert thread.is_alive(), 'a thread not started inside the send went ahead of it'
            waiting.append((thread, results))

    send(P, A, body=body)
    for thread, results in waiting:
        thread.join(5)
        assert results == [(False, 'sent')]
    assert len(c.disclosures()) == 3, 'each waiting send is a send of its own, disclosed'


@pytest.mark.parametrize('ends', ['returns', 'raises'])
def test_a_context_copy_alive_after_its_send_ends_no_longer_counts_as_inside_it(ends):
    ready, go, out, release = (threading.Event() for _ in range(4))
    seen = []

    def child():
        ready.set()
        assert go.wait(5)
        seen.append(c.admitted(P))
        seen.append(outcome(lambda: send(P, A)))

    def body():
        nonlocal thread
        thread, _ = started(child, context=contextvars.copy_context())
        assert ready.wait(5)
        if ends == 'raises':
            raise LLMError('G6_E_MODEL_REFUSED: the provider refused the send')

    thread = None
    with pytest.raises(LLMError, match='MODEL_REFUSED') if ends == 'raises' else nullcontext():
        send(P, A, body=body)
    # another send now holds the lock: the child's later send takes the lock itself, so it waits for this one
    other, _ = started(lambda: send(P, B, body=lambda: (out.set(), release.wait(5))))
    assert out.wait(5)
    go.set()
    thread.join(SETTLE)
    assert thread.is_alive(), 'a context copy outliving its send still joined it'
    release.set()
    thread.join(5)
    other.join(5)
    assert seen == [False, 'sent']
    assert len(c.disclosures()) == 3, 'the later send is not that send\'s own step: it is disclosed'


def test_a_joined_send_still_out_when_its_send_ends_holds_the_lock_until_it_ends():
    """The send that ends does not wait for a send joined to it that is still out; revocation waits for both."""
    out, release, order = threading.Event(), threading.Event(), []

    def inner():
        send(P, A, body=lambda: (out.set(), release.wait(5), order.append('joined send ended')))

    def body():
        started(inner, context=contextvars.copy_context())
        assert out.wait(5), 'the send inside was not let go while its send was out'

    enclosing, _ = started(lambda: (send(P, A, body=body), order.append('enclosing send ended')))
    assert out.wait(5)
    enclosing.join(5)
    assert not enclosing.is_alive(), 'the send waited at its end for a send joined to it'
    revoking, _ = started(lambda: (c.revoke(A.source_id, P), order.append('revoked')))
    revoking.join(SETTLE)
    assert revoking.is_alive() and order == ['enclosing send ended'], 'a revoke returned while a send was out'
    release.set()
    revoking.join(5)
    assert order == ['enclosing send ended', 'joined send ended', 'revoked']
    assert kinds() == ['disclosure', 'revoke']


def test_a_send_ends_although_a_send_joined_to_it_waits_for_what_its_caller_does_next():
    went_on, out = threading.Event(), threading.Event()

    def body():
        started(lambda: send(P, A, body=lambda: (out.set(), went_on.wait(5))), context=contextvars.copy_context())
        assert out.wait(5)

    caller, _ = started(lambda: (send(P, A, body=body), went_on.set()))
    caller.join(5)
    stuck = caller.is_alive()
    went_on.set()                                # frees the cycle if there is one, so nothing is left held
    caller.join(5)
    assert not stuck, 'the send and the send joined to it wait for each other'
    reading, got = started(c.grants)
    reading.join(5)
    assert got == [[]], 'the lock was not let go when the last send under it ended'


def _on_a_loop(main):
    thread, results = started(lambda: asyncio.run(main()))
    thread.join(10)
    assert not thread.is_alive(), 'the event loop is stuck'
    return results


def test_an_asyncio_task_made_inside_a_send_and_out_when_it_ends_does_not_stall_the_loop():
    async def main():
        out, release = asyncio.Event(), asyncio.Event()

        async def inner():
            with c.admission(P, [A], c.sha256('inner'), supplied=[A.source_id]):
                out.set()
                await release.wait()

        with c.admission(P, [A], c.sha256('outer'), supplied=[A.source_id]):
            task = asyncio.create_task(inner())
            await out.wait()
        release.set()
        await task
        return len(c.disclosures())

    assert _on_a_loop(main) == [1], 'the task inside the send is its own step: disclosed once'


def test_an_asyncio_task_made_before_a_send_on_the_same_loop_is_a_send_of_its_own_decided_at_once():
    """It cannot wait for a send of its own thread (the loop would stop); it is decided under that send's lock, as a send
    of its own: checked, and disclosed."""
    seen = []

    async def main():
        go = asyncio.Event()

        async def earlier():
            await go.wait()
            began = time.monotonic()
            seen.extend((c.admitted(P), outcome(lambda: send(P, B)), outcome(lambda: send(P, UNSHARED)),
                         time.monotonic() - began))

        task = asyncio.create_task(earlier())
        await asyncio.sleep(0)
        with c.admission(P, [A], c.sha256('outer'), supplied=[A.source_id]):
            go.set()
            await asyncio.sleep(0.05)
        await task

    _on_a_loop(main)
    assert seen[:3] == [False, 'sent', 'refused'] and seen[3] < SETTLE
    assert len(c.disclosures()) == 2


def test_where_an_observer_carries_the_context_only_the_sends_own_copies_are_inside_it():
    """``model_calls`` carries the context into every thread started where an observer is open. A thread started so,
    for work of its own, is not inside the send (it waits, and is disclosed); a copy the send's step makes itself, and a
    pool task it submits, are. Inside such a thread the same holds for a send it makes there."""
    seen, ended = {}, threading.Event()

    def nested():                                   # on a thread carried into, not inside the send around it
        seen['carried thread'] = c.admitted(P)
        assert ended.wait(5)                        # a send of its own waits for the send around it: made after it

        def own_step():
            explicit = contextvars.copy_context()  # the nested send's own copy, started as a thread
            thread, results = started(lambda: c.admitted(OTHER), context=explicit)
            thread.join(5)
            plain = threading.Thread(target=lambda: results.append(c.admitted(OTHER)))
            plain.start()                           # carried again: work of its own
            plain.join(5)
            return results

        seen['inside the nested send'] = send(OTHER, B, body=own_step)

    def body():
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            seen['pool task'] = pool.submit(c.admitted, P).result(5)
        finally:
            pool.shutdown(wait=True)
        explicit, plain = contextvars.copy_context(), threading.Thread(target=nested)
        thread, results = started(lambda: c.admitted(P), context=explicit)
        thread.join(5)
        seen['explicit copy'] = results
        seen['plain'] = plain
        plain.start()

    with model_calls.counting(before_call=lambda: None):
        send(P, A, body=body)
    ended.set()
    seen.pop('plain').join(10)
    assert seen == {'pool task': True, 'explicit copy': [True], 'carried thread': False,
                    'inside the nested send': [True, False]}
    assert len(c.disclosures()) == 2, 'the send made on the carried thread is a send of its own'


def test_a_worker_started_inside_a_watched_send_waits_for_it_and_its_revoke_too():
    out, seen = threading.Event(), {}

    def worker():
        seen['admitted'] = c.admitted(P)
        began = time.monotonic()
        seen['send'] = outcome(lambda: send(P, A))
        seen['revoke'] = c.revoke(A.source_id, P)['status']
        seen['waited'] = time.monotonic() - began >= SETTLE

    def body():
        thread = threading.Thread(target=worker)
        seen['thread'] = thread
        thread.start()
        thread.join(SETTLE)
        out.set()
        assert thread.is_alive(), 'a thread started for work of its own went ahead of the send'

    with model_calls.counting(before_call=lambda: None):
        send(P, A, body=body)
    seen.pop('thread').join(5)
    assert seen == {'admitted': False, 'send': 'sent', 'revoke': 'revoked', 'waited': True}
    assert kinds() == ['disclosure', 'disclosure', 'revoke']


def test_threads_inside_one_send_read_decide_and_append_one_at_a_time(monkeypatch):
    """A revoke on one inner thread does not land between another inner thread's decision and its disclosure."""
    deciding, go = threading.Event(), threading.Event()
    decisions = c._decisions

    def paused(ledger):
        found = decisions(ledger)
        if threading.current_thread().name == 'deciding':
            deciding.set()
            assert go.wait(5)
        return found

    monkeypatch.setattr(c, '_decisions', paused)
    got = []

    def body():
        first, sent = started(lambda: send(P, B), context=contextvars.copy_context(), name='deciding')
        assert deciding.wait(5)
        second, revoked = started(lambda: c.revoke(B.source_id, P), context=contextvars.copy_context())
        second.join(SETTLE)
        assert second.is_alive(), 'the revoke went ahead inside another thread\'s decision'
        go.set()
        for thread in (first, second):
            thread.join(5)
            assert not thread.is_alive(), 'a thread inside the send waited for it'
        got.extend((*sent, *revoked))

    send(P, A, body=body)
    assert got[0] == 'sent' and got[1]['status'] == 'revoked'
    assert kinds() == ['disclosure', 'disclosure', 'revoke']


def test_many_threads_inside_one_send_each_disclose_once_on_one_unbroken_chain():
    sources = [literal(f'input {n}') for n in range(8)]
    barrier, got = threading.Barrier(len(sources)), []

    def one(source):
        barrier.wait(5)
        return send(P, source, body=lambda: send(P, source))      # its own step nested inside it: disclosed once

    def body():
        threads = [started(lambda s=s: one(s), context=contextvars.copy_context()) for s in sources]
        for thread, results in threads:
            thread.join(5)
            assert not thread.is_alive(), 'a thread inside the send waited for it'
            got.extend(results)

    send(P, A, body=body)
    assert got == ['sent'] * len(sources)
    assert len(c.disclosures()) == 1 + len(sources)          # the chain opens whole: no entry lost or interleaved
