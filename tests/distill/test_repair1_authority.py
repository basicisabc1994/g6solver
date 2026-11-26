"""A completed status change must precede every durable authority writer."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from g6.core.ledger import ChainLedger
from g6.core.storage import state_path
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.harness import library_review_gate as review
from tests.distill import test_acceptance_gate as acceptance, test_runtime_identity_boundaries as boundaries
from tests.distill.test_runtime_identity_boundaries import AFTER, BEFORE, admit, inactive, method

disk = boundaries.disk
rig = acceptance.rig


def _pause(monkeypatch, module, name, selected=lambda *a, **k: True):
    ready, release = Event(), Event()
    original = getattr(module, name)
    reached = []

    def paused(*args, **kwargs):
        if not reached and selected(*args, **kwargs):
            reached.append(True)
            ready.set()
            assert release.wait(10), 'durable writer was not released'
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    return ready, release


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
def test_field_writer_checks_status_after_its_call_boundary(disk, monkeypatch, status):
    entry = admit(method(), disk)
    ready, release = _pause(monkeypatch, review, 'record_fire')
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(review.check, BEFORE, AFTER)
        try:
            assert ready.wait(10)
            pool.submit(inactive, entry, status).result(timeout=5)
        finally:
            release.set()
        findings = running.result(timeout=10)
    assert findings[0]['authority'] == 'A0'
    assert findings[0]['code'] == f'G6_E_DISTILL_METHOD_{status.upper()}_DURING_RUN'
    rows = ChainLedger(state_path('distill', 'field_fires_ledger.jsonl')).entries()
    assert not rows, 'stale application authority reached the field ledger'


@pytest.mark.parametrize('status', ['held', 'withdrawn'])
@pytest.mark.parametrize('writer', ['look', 'evaluated', 'admitted', 'report', 'certificate'])
def test_admission_writers_recheck_after_their_call_boundary(rig, monkeypatch, status, writer):
    _, candidate, _, frozen, _ = rig
    # An independent active method makes a completed status change visible to
    # the admission snapshot, without changing the proposed method itself.
    negative = frozen.acceptance[-1].case
    sentinel = admit(method(template='skip_gate.v1'), negative,
                     regression_suite=[{'case_ref': negative.case_id, 'verdict': 'no_concern'}])
    target = {'look': '_spend', 'evaluated': '_record', 'admitted': '_record',
              'report': '_save_report', 'certificate': '_save_report'}[writer]

    def selected(first, *args, **kwargs):
        if writer in {'evaluated', 'admitted'}:
            return first.verdict == ('evaluated' if writer == 'evaluated' else 'admit_probation')
        if writer in {'report', 'certificate'}:
            return ('authority' in first) == (writer == 'certificate')
        return True

    ready, release = _pause(monkeypatch, gate, target, selected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(gate.decide, candidate, Library(), frozen.registration_sha256)
        try:
            # One admission takes over a minute on a loaded machine; wait for the writer while the decision
            # runs, and fail at once if it ends without reaching it.
            while not ready.wait(1):
                assert not running.done(), f'admission ended without reaching {writer}: {running.result()}'
            changed_entry = next(entry for entry in Library().list()
                                 if entry.candidate_id == candidate.candidate_id) if writer == 'admitted' else sentinel
            pool.submit(inactive, changed_entry, status).result(timeout=5)
            reports = state_path('distill', 'acceptance_gate_reports')
            before_reports = set(reports.glob('*.json'))
            before_rows = len(gate._journal().entries())
        finally:
            release.set()
        answer = running.result(timeout=30)
    assert answer.verdict in {'block', 'refuse'}
    assert not answer.entry_id and not answer.report_sha256
    rows = gate._journal().entries()[before_rows:]
    assert not any(row['kind'] == 'reserve' or row['body'].get('verdict') in {
        'evaluated', 'admit_probation', 'admit', 'retain'} for row in rows)
    assert set(reports.glob('*.json')) == before_reports
    if writer != 'admitted':
        assert all(entry.candidate_id != candidate.candidate_id for entry in Library().list())
    else:
        # Admission before the pause is legitimate history. The later decision
        # must not append or return that authority after the completed change.
        assert Stores().for_model(r.LibraryEntry).list()
        assert next(entry for entry in Library().list()
                    if entry.candidate_id == candidate.candidate_id).authority == 'A0'
