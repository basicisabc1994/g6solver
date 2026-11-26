"""Review 4: real persisted library and production entry points."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from g6.core.ledger import digest
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.execution import Observation, run_candidate
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.harness import library_review_gate
from tests.distill.test_records import bodies
from tests.distill.test_runtime_identity_boundaries import (
    AFTER, BEFORE, admit, disk, inactive, method,
)


@pytest.fixture
def tmp_path():
    path = Path(__file__).resolve().parents[2] / '.bt_rev_dfu4' / uuid4().hex
    path.mkdir(parents=True)
    return path


def test_unknown_registration_is_refused_before_recorded_case_access(disk, monkeypatch):
    first = admit(method(), disk)
    inactive(first, 'withdrawn')
    other = method(template='skip_gate.v1')
    Stores().for_model(r.VerifierCandidate).put(other)
    assert digest('unregistered') not in gate._policy()[1]['registrations']
    reads = []
    reader = gate._recorded_case

    def observed_read(ident):
        reads.append(ident)
        return reader(ident)

    monkeypatch.setattr(gate, '_recorded_case', observed_read)
    answer = gate.decide(other, Library(), digest('unregistered'))
    assert answer.verdict == 'block'
    assert 'owner registration anchor' in ' '.join(answer.reasons)
    assert reads == [], f'unknown registration read recorded bodies {len(reads)} times'


def test_owner_withdrawal_does_not_wait_for_running_application(disk, monkeypatch):
    entry = admit(method(), disk)
    entered, release, withdrawing = Event(), Event(), Event()
    detect = gate.weakening.detect_test_weakening

    def slow_detector(before, after):
        entered.set()
        if not release.wait(10):
            raise RuntimeError('review cleanup deadline')
        return detect(before, after)

    def withdraw():
        withdrawing.set()
        return gate.withdraw(entry.entry_id, Library(), reason='stop faulty check', person='Owner')

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', slow_detector)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(library_review_gate.check, BEFORE, AFTER)
        try:
            assert entered.wait(5)
            pending = pool.submit(withdraw)
            assert withdrawing.wait(5)
            from concurrent.futures import wait
            completed = bool(wait([pending], timeout=1).done)
        finally:
            release.set()
        found = running.result(timeout=10)
        decision = pending.result(timeout=10)
    assert decision.verdict == 'withdraw'
    assert completed, (
        'owner withdrawal blocked behind method execution; running check reported '
        f'{[(f["authority"], f["outcome"]) for f in found]} before withdrawal completed'
    )


def test_recorded_comparison_does_not_expand_reuse_chain_exponentially(disk, monkeypatch):
    retired = admit(method(template='skip_gate.v1'), disk,
                    regression_suite=[{'case_ref': disk.case_id, 'verdict': 'no_concern'}])
    inactive(retired, 'withdrawn')
    contract = r.MethodContract(**(bodies()[2][1] | {
        'cost_bound': {'wall_s': 120.0, 'probe_runs': 0, 'model_calls': 0}}))
    Stores().for_model(r.MethodContract).put(contract)
    leaf = r.VerifierCandidate(**(method().model_dump(mode='json', exclude={'candidate_id'}) | {
        'contract': contract}))
    entry = admit(leaf, disk, contract_sha256=contract.content_id)
    for _ in range(2):
        parent = r.VerifierCandidate(**(bodies()[3][1] | {
            'form': 'reuse', 'body': entry.entry_id, 'lineage': [entry.entry_id], 'contract': contract}))
        entry = admit(parent, disk, contract_sha256=contract.content_id)
    root = Stores().for_model(r.VerifierCandidate).get(entry.candidate_id)
    calls = []
    detect = gate.weakening.detect_test_weakening

    def counted(before, after):
        calls.append(1)
        return detect(before, after)

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', counted)
    result = run_candidate(root, [Observation(BEFORE, AFTER, {})],
                           lambda ref: Stores().for_model(r.VerifierCandidate).get(
                               Stores().for_model(r.LibraryEntry).get(ref).candidate_id))
    assert result == ['concern']
    assert len(calls) <= 16, f'one observation through two reuse edges ran detector {len(calls)} times'
