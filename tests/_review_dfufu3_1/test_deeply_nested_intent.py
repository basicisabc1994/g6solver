"""Review 1 of fu-3 (minor): one shape of unreadable intent still takes the patch review down.

``_read_intent`` turns ``OSError``, ``ValueError``, ``KeyError`` and ``TypeError`` into ``PartialAuthorityWrite``,
which the review reads as "library unavailable" and goes on. ``json.loads`` raises ``RecursionError`` for a deeply
nested document; that is none of the four, nor anything ``apply_review``, ``check_learned`` or ``review_patch``'s
``except (OSError, ValueError, TypeError)`` catch, so the whole review ends in a raw exception.

The control is the parent's review 4 case: an intent cut short.
"""
import pytest

from g6.core.storage import state_path
from g6.harness import library_review_gate as review
from tests.distill import test_runtime_identity_boundaries as runtime

TEXTS = {'deeply_nested': '[' * 200_000, 'control_cut_short': '{"body": {"committed": false, "version": 1, "wri'}


@pytest.mark.parametrize('shape', list(TEXTS))
def test_patch_review_goes_on_with_an_unreadable_intent(monkeypatch, tmp_path, shape):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    state_path('distill', 'authority_intent.json').write_text(TEXTS[shape], encoding='utf-8')
    reasons, runs = [], []
    try:
        review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    except RecursionError as error:
        pytest.fail(f'apply_review raised instead of listing the library as unavailable: {type(error).__name__}')
    assert [reason['code'] for reason in reasons] == ['library_unavailable'] and runs == []


@pytest.mark.parametrize('shape', list(TEXTS))
def test_learned_probe_replies_with_an_unreadable_intent(monkeypatch, tmp_path, shape):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    state_path('distill', 'authority_intent.json').write_text(TEXTS[shape], encoding='utf-8')
    params = {'repo': 'invalid/name', 'files': {}, 'regression': {'learned': {'entry_id': ''}}}
    try:
        result = review.check_learned(params)
    except RecursionError as error:
        pytest.fail(f'the learned probe raised instead of replying not_run: {type(error).__name__}')
    assert (result['status'], result.get('code')) == ('not_run', 'library_unavailable')
