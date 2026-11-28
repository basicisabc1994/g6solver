from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate
from g6.distill.library import Library
from g6.harness import library_review_gate as review
from tests.distill.test_runtime_identity_boundaries import (
    AFTER, BEFORE, admit, disk as original_disk, method,
)


@pytest.fixture
def disk(monkeypatch):
    # Windows denies this sandbox access to pytest's mode-0700 basetemp.
    # Keep the real store in the other directory authorised for this review.
    folder = Path(__file__).parent / '_state' / uuid4().hex
    folder.mkdir(parents=True)
    return original_disk.__wrapped__(monkeypatch, folder)


def test_withdrawal_before_field_append_cannot_record_a2(disk, monkeypatch):
    entry = admit(method(), disk)
    ready, release = Event(), Event()
    append = review.record_fire

    def paused_append(*args):
        ready.set()
        assert release.wait(10), 'field append was not released'
        return append(*args)

    monkeypatch.setattr(review, 'record_fire', paused_append)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(review.check, BEFORE, AFTER)
        try:
            assert ready.wait(10), 'application never reached its field append'
            withdrawal = pool.submit(
                gate.withdraw, entry.entry_id, Library(),
                reason='owner stopped method', person='Owner',
            ).result(timeout=5)
            assert withdrawal.verdict == 'withdraw'
            assert Library().list()[0].status == 'withdrawn'
        finally:
            release.set()
        findings = running.result(timeout=10)
    assert findings[0]['authority'] == 'A0'
    assert findings[0]['code'] == 'G6_E_DISTILL_METHOD_WITHDRAWN_DURING_RUN'
    rows = ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert not any(row['body']['authority'] == 'A2' for row in rows), (
        'field ledger appended A2 authority after withdrawal completed: '
        f'{[row["body"] for row in rows]}'
    )
