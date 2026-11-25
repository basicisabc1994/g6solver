"""Finding 1: a pending restore says "This request was not sent" about a call whose request had gone out.

``_RestorePending`` (9f79bb79) is one fixed sentence under the reservation's code: "The request reservation could not
be saved: the prior retained publication is waiting for a sharing refusal to clear. This request was not sent. Retry
this call." It is raised wherever a restore cannot finish, and the first such place is the save that left it pending.
That save is not always a reservation. A step reserves, sends, and then saves what came back (``_scouts`` line 1851,
the ``CONTINUE`` saves at 2162 and 2167, ``_owe_row``, ``_finish``). When a reader gets in the way of one of those
saves, the publication's own restore raises ``_RestorePending`` in place of the save's error, ``_advance_inner`` re-reads
and re-raises it (lines 2008-2014), and both surfaces print it as it is: the Stop hook by its declared code
(``completion_hook.on_stop``), the tool by ``review_loop._step``'s new ``except PermissionError``. The person is told
the request was not sent, and to retry, about a call that asked the reviewer; the retry asks the reviewer again. The
tool's reply has no ``requests_sent`` either, while the run's own retained count (once the restore finishes) is 1.

Before this change the same reader gave an uncoded ``PermissionError`` (the hook's "G6's completion loop stopped, so
this work is not tested: ..."), which claims nothing about what was sent.

Everything is on the real disk with real open handles (Python's own ``open``, which shares no delete). Nothing in G6
is replaced beyond what the committed ``Session`` helper stands in (the scout, which notes one model call and then
records the task as reviewed, and the repository reads). The reader is review 4's (``rev1_readers.Reader``): it opens
the receipt, and the content once a save has replaced it. Only when it arrives differs:

* ``reader-after-the-send`` (fails): it arrives while the scout is out, after the reservation was saved;
* ``control-reader-before-the-call``: it is there from the start, so the reservation itself is refused, nothing is
  sent, and the same sentence is true;
* ``control-receipt-only-after-the-send``: it never opens the content, so the restore works at once and the save's
  own uncoded error comes out, with no claim about the request.
"""
import os

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from g6.mcp import review_loop
from tests._review_wtffu4_1.rev1_readers import Reader, storage_at
from tests.harness.test_completion_fu3_repair2 import Session

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
UNSENT = 'This request was not sent'
WHEN = pytest.mark.parametrize('arrives, also_content', [
    ('before-the-call', True), ('after-the-send', False), ('after-the-send', True),
], ids=['control-reader-before-the-call', 'control-receipt-only-after-the-send', 'reader-after-the-send'])


def _one_call(world, monkeypatch, call, arrives, also_content):
    """Run ``call`` beside the reader. Returns what the reply said (``''`` for an uncoded error) and the reader."""
    reader = Reader(world.folder / 'state.json', also_content=also_content)
    sends = loop._scout                      # Session's stand-in: one noted model call, then the task is "reviewed"

    def scout(*args):
        made = sends(*args)
        if arrives == 'after-the-send' and not reader.handles:
            reader.arrive()
        return made

    monkeypatch.setattr(loop, '_scout', scout)
    if arrives == 'before-the-call':
        reader.arrive()
    try:
        said = call()
    except PermissionError as exc:
        assert getattr(exc, 'code', None) is None, 'an uncoded sharing error is expected here, nothing else'
        said = ''
    finally:
        reader.leave()
    assert reader.replaced, 'no save replaced the content beside the reader, so nothing here was exercised'
    return said, reader


def _check(world, said, arrives, also_content):
    sent = len(world.reviewed)
    if arrives == 'before-the-call':
        assert UNSENT in said and sent == 0, 'control: the reservation was refused, and nothing went out'
    if arrives == 'after-the-send':
        assert sent == 1, 'the reader arrived after the first request had gone out'
    if not also_content:
        assert UNSENT not in said, 'control: the restore worked, and the save\'s own error claims nothing'
    retained = loop.attempted(loop._load(world.folder))      # every file is free again: the restore finishes here
    assert not (UNSENT in said and sent), (
        f'the reply says the request was not sent after {sent} request(s) of this call went out '
        f'(the run\'s own retained count: {retained}): {said[-260:]}')


@WHEN
def test_a_stop_is_not_told_its_request_was_not_sent_after_it_went_out(tmp_path, monkeypatch, key_file, arrives,
                                                                       also_content):
    world = Session(tmp_path, monkeypatch, key_file, 'fable-after-send')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    said, _ = _one_call(world, monkeypatch, lambda: hook.on_stop(world.event).get('systemMessage', ''), arrives,
                        also_content)
    _check(world, said, arrives, also_content)


@WHEN
def test_an_mcp_call_is_not_told_its_request_was_not_sent_after_it_went_out(tmp_path, monkeypatch, key_file, arrives,
                                                                            also_content):
    world = Session(tmp_path, monkeypatch, key_file, 'mcp:fable-after-send')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    replies = []

    def call():
        replies.append(review_loop.step(str(world.repo), 'old task', 'fable-after-send', 'base'))
        return str(replies[-1].get('error', ''))

    said, _ = _one_call(world, monkeypatch, call, arrives, also_content)
    if replies and arrives == 'after-the-send':
        assert 'requests_sent' not in replies[0] or replies[0]['requests_sent'] >= 1, 'a count below what was sent'
    _check(world, said, arrives, also_content)


def test_the_retry_the_reply_asks_for_sends_the_request_again(tmp_path, monkeypatch, key_file):
    """What the false sentence costs: the person retries a call they were told sent nothing, and the reviewer is asked
    again. (The run's own count is right afterwards: only the reply's words were wrong.)"""
    world = Session(tmp_path, monkeypatch, key_file, 'fable-after-send-retry')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    said, _ = _one_call(world, monkeypatch, lambda: hook.on_stop(world.event).get('systemMessage', ''),
                        'after-the-send', True)
    first = len(world.reviewed)
    hook.on_stop(world.event)
    assert not (UNSENT in said and 'Retry this call' in said and first == 1 and len(world.reviewed) > 1), (
        f'told "not sent, retry" after one request; the retry sent {len(world.reviewed) - first} more; '
        f'retained count now {loop.attempted(loop._load(world.folder))}')


@pytest.mark.parametrize('surface', ['stop', 'mcp'])
def test_control_the_parents_storage_made_no_claim_about_the_request(tmp_path, monkeypatch, key_file, surface):
    """New with 9f79bb79: under the storage module of 1827b22e (this change's parent, read from git) the same reader
    after the same send gets the save's uncoded error on both surfaces, which says nothing of what was sent. (The
    pair is torn there, review 4's finding 1; that is what the pending restore repairs.)"""
    parent = storage_at('1827b22e')
    for module in (loop, hook, review_loop):
        monkeypatch.setattr(module, 'store', parent)
    world = Session(tmp_path, monkeypatch, key_file, ('mcp:' if surface == 'mcp' else '') + 'fable-after-send-parent')
    if surface == 'stop':
        hook.on_prompt({**world.event, 'prompt': 'old task'})
        call = lambda: hook.on_stop(world.event).get('systemMessage', '')  # noqa: E731
    else:
        parent.mkdir(world.folder, parents=True, exist_ok=True)
        call = lambda: str(review_loop.step(str(world.repo), 'old task', 'fable-after-send-parent', 'base').get('error', ''))  # noqa: E731
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    said, _ = _one_call(world, monkeypatch, call, 'after-the-send', True)
    assert len(world.reviewed) == 1 and UNSENT not in said