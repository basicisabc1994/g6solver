"""Review 1 of fu-3: a committed field fire is disowned when the intent's cleanup fails again at the next read.

``record_fire`` now captures a committed fire whose intent could not be removed (``field_recorded: True``,
``publication_committed: True``). ``_check`` then makes one more read (``failure_writer(reading=True)``) before it
returns, and that read runs recovery, which tries to remove the same committed intent. When the removal fails again
(on Windows the file is still held by whatever held it microseconds earlier), ``PartialAuthorityWrite(committed=True,
recovering=True)`` leaves ``_check``; ``check`` replaces every finding with ``_unavailable``: ``field_recorded: False``
and no library application, while the fire's row is in ``field_fires_ledger.jsonl`` and is listed by
``g6_library history``. The builder's fixture fails the removal once only, so the second attempt always works.

The controls are the same review with a removal that fails once (the builder's case) and one that never fails.
"""
import json
from pathlib import Path

import pytest

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path
from g6.harness import library_review_gate as review
from tests.distill import test_runtime_identity_boundaries as runtime


def cleanup_fails(monkeypatch, times):
    """The committed intent cannot be removed ``times`` times (another process has it open)."""
    unlink, failed = Path.unlink, []

    def fail(path, *args, **kwargs):
        if path.name == 'authority_intent.json' and len(failed) < times and path.exists() \
                and json.loads(path.read_text(encoding='utf-8'))['body']['committed']:
            failed.append(True)
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process')
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    return failed


@pytest.mark.parametrize('times', [2, 1, 0], ids=['fails_twice', 'control_fails_once', 'control_works'])
def test_field_reply_matches_the_field_ledger(monkeypatch, tmp_path, times):
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    runtime.admit(runtime.method(), frozen)
    failed = cleanup_fails(monkeypatch, times)
    [answer] = review.check(runtime.BEFORE, runtime.AFTER)
    assert len(failed) == times
    rows = ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert len(rows) == 1, 'the fire committed in every case'
    assert answer['field_recorded'] is True, (
        f"the field ledger holds {len(rows)} row written by this review, but the reply says field_recorded="
        f"{answer['field_recorded']!r}, library_unavailable={answer.get('library_unavailable')!r}: {answer['reason']}")


@pytest.mark.parametrize('times', [2, 0], ids=['fails_twice', 'control_works'])
def test_a_review_that_reports_no_library_application_left_no_fire(monkeypatch, tmp_path, times):
    """The same through the patch review's door: a review that lists the library as unavailable and no library run
    must not have added a fire for that run to the method's history."""
    from g6.distill import surface
    frozen = runtime.disk.__wrapped__(monkeypatch, tmp_path)
    entry = runtime.admit(runtime.method(), frozen)
    failed = cleanup_fails(monkeypatch, times)
    reasons, runs = [], []
    review.apply_review(runtime.BEFORE, runtime.AFTER, reasons, runs)
    assert len(failed) == times
    fires = surface.library('history', entry.entry_id)['unlabelled_fires']
    applied = [run['command'] for run in runs]
    assert len(fires) == len(applied), (
        f"the review reported {len(applied)} library applications ({[r['code'] for r in reasons]}), "
        f"and the method's history now holds {len(fires)} fire from it")
