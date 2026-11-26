"""Review 4, finding 2: a pending undo that cannot finish takes the patch review down with it.

Since repair 3 every reader runs recovery first, so ``PartialAuthorityWrite`` (an ``OSError``) can now come out of any
read. ``library_review_gate.check`` handles it at its last read and around ``record_fire`` only; its first read
(``status_snapshot``) and the reads inside its loop let it through. ``apply_review`` catches ``ValueError``,
``KeyError`` and ``LedgerError``, and ``check_learned`` the same plus ``UnicodeError``: neither catches it, so
``review_patch``'s outer ``except (OSError, ValueError, TypeError)`` turns the whole review into one blocked reply
(``[G6_E_PATCH_REVIEW] G6_E_DISTILL_PARTIAL_WRITE: ...``), for every repository, until the undo can finish.

The control is the library's other unreadable state (a broken chain): the review goes on and lists
``library_unavailable``.
"""
from pathlib import Path

import pytest

from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate
from g6.distill.ledger import PartialAuthorityWrite
from g6.distill.library import Library
from g6.harness import library_review_gate as review
from g6.mcp import distill
from tests.distill import test_runtime_identity_boundaries as runtime
from tests.distill.test_repair2_atomicity import interrupt


@pytest.fixture
def stuck(monkeypatch, tmp_path):
    """The builder's own scenario (test_pending_undo_blocks_later_reply_until_recovery_succeeds): a withdrawal fails
    after its library row, and the undo cannot rewrite library.jsonl. Returns a function that lets the undo work."""
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    hit = interrupt(monkeypatch, 'decision')
    opened = Path.open

    def cannot_restore(path, mode='r', *args, **kwargs):
        if hit and path.name == 'library.jsonl' and mode == 'wb':
            raise OSError('the file is in use')
        return opened(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', cannot_restore)
    refused = distill._call(gate.withdraw, entry.entry_id, Library(), reason='withdraw', person='Owner')
    assert refused['warning_card']['code'] == 'G6_E_DISTILL_PARTIAL_WRITE'
    assert state_path('distill', 'authority_intent.json').exists()
    return lambda: monkeypatch.setattr(Path, 'open', opened)


def test_patch_review_goes_on_while_an_undo_is_pending(stuck):
    reasons, runs = [], []
    try:
        review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    except PartialAuthorityWrite as error:
        pytest.fail('apply_review raised instead of listing the library as unavailable; review_patch turns this '
                    f'into a blocked reply for the whole review: {type(error).__name__}: {error}')
    assert not any(run['library']['authority'] == 'A2' for run in runs)
    assert [reason['code'] for reason in reasons] == ['library_unavailable'] or all(
        run['library'].get('code') == 'G6_E_DISTILL_PARTIAL_WRITE' for run in runs)


def test_patch_review_goes_on_with_an_unreadable_intent(monkeypatch, tmp_path):
    """The other state nothing in G6 can leave: an intent file that does not parse (cut short here). No call clears
    it, so this is every review from then on."""
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    state_path('distill', 'authority_intent.json').write_text('{"body": {"committed": false, "version": 1, "wri',
                                                              encoding='utf-8')
    reasons, runs = [], []
    try:
        review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    except PartialAuthorityWrite as error:
        pytest.fail(f'apply_review raised instead of listing the library as unavailable: {error}')
    assert [reason['code'] for reason in reasons] == ['library_unavailable'] and runs == []


def test_learned_probe_replies_while_an_undo_is_pending(stuck, monkeypatch):
    from g6.workforce import bench, patch_checks

    class Source:
        def __truediv__(self, path):
            return self

        def read_text(self, **kwargs):
            return runtime.BEFORE['tests/test_value.py']

    monkeypatch.setattr(bench, '_repo', lambda name, venture: Source())
    monkeypatch.setattr(bench, '_linked', lambda path: False)
    # the inputs of tests/distill/test_wire_review.py::test_learned_scope_resolves_real_bench_inputs, real check()
    params = {'repo': 'example', 'files': {'app.py': 'def f():\n    return 2\n'},
              'regression': {'learned': {'entry_id': 'a' * 64}}}
    reached = []
    check = review.check
    monkeypatch.setattr(review, 'check', lambda *a, **k: reached.append(True) or check(*a, **k))
    try:
        result = patch_checks.run(params, venture='v')
    except PartialAuthorityWrite as error:
        pytest.fail(f'the learned probe raised instead of replying not_run: {type(error).__name__}: {error}')
    assert reached and result['result']['probe']['status'] == 'not_run'


def test_control_learned_probe_replies_not_run_without_a_pending_undo(monkeypatch, tmp_path):
    from g6.workforce import bench, patch_checks

    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)

    class Source:
        def __truediv__(self, path):
            return self

        def read_text(self, **kwargs):
            return 'def f():\n    return 1\n'

    monkeypatch.setattr(bench, '_repo', lambda name, venture: Source())
    monkeypatch.setattr(bench, '_linked', lambda path: False)
    params = {'repo': 'example', 'files': {'app.py': 'def f():\n    return 2\n'},
              'regression': {'learned': {'entry_id': 'a' * 64}}}
    assert patch_checks.run(params, venture='v')['result']['probe']['status'] == 'not_run'


def test_control_review_reports_a2_once_the_undo_finishes(stuck):
    stuck()
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert [run['library']['authority'] for run in runs] == ['A2']
    assert [reason['code'] for reason in reasons] == ['library_defect_detected']
    assert not state_path('distill', 'authority_intent.json').exists()


def test_control_a_broken_library_chain_is_listed_and_the_review_goes_on(monkeypatch, tmp_path):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    chain = state_path('distill', 'library.jsonl')
    chain.write_bytes(chain.read_bytes() + b'not a row\n')
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert [reason['code'] for reason in reasons] == ['library_unavailable'] and runs == []
