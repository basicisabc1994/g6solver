# SPDX-License-Identifier: Apache-2.0
"""A send made inside an admitted send, on a thread the send's own step started with a copy of its context: it is checked
as a send nested on the send's own thread is, under the same hold, and never waits for the send it is made inside of.
Only such a context counts as inside the send, and only while the send is out. No model is called: each send is the
sharing check (``cloud_consent.admission``) of literal inputs its caller supplied, with a body of the test's own."""
from __future__ import annotations

import contextvars
import json
import threading
from contextlib import nullcontext

import pytest

from g6.core import cloud_consent as c
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
    """Revocation waits for every admitted send that is out, a joined one too."""
    out, release, order = threading.Event(), threading.Event(), []

    def inner():
        send(P, A, body=lambda: (out.set(), release.wait(5), order.append('joined send ended')))

    def body():
        started(inner, context=contextvars.copy_context())
        assert out.wait(5), 'the send inside was not let go while its send was out'

    enclosing, _ = started(lambda: (send(P, A, body=body), order.append('enclosing send ended')))
    assert out.wait(5)
    revoking, _ = started(lambda: (c.revoke(A.source_id, P), order.append('revoked')))
    enclosing.join(SETTLE)
    revoking.join(SETTLE)
    assert enclosing.is_alive() and revoking.is_alive() and order == []
    release.set()
    enclosing.join(5)
    revoking.join(5)
    assert order[0] == 'joined send ended' and sorted(order[1:]) == ['enclosing send ended', 'revoked']
    assert kinds() == ['disclosure', 'revoke']


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
