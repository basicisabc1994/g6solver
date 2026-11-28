"""Review 4 reproductions against real content-addressed state on disk."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from g6.core.ledger import canonical
from g6.distill import acceptance_gate as gate, records as r
from g6.distill.held_out.case_gate import CaseRecord
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.harness import library_review_gate
from tests.distill.test_records import bodies
from tests.distill.test_review2_repairs import CASE


@pytest.fixture
def disk(monkeypatch):
    root = Path(__file__).resolve().parents[2] / '.bt_rev_dfu4' / uuid4().hex
    home = root / 'home'
    monkeypatch.setenv('G6_HOME', str(home))
    monkeypatch.setenv('G6_CLAUDE', r'C:\nonexistent\g6-no-host.exe')
    monkeypatch.setenv('G6_CODEX', r'C:\nonexistent\g6-no-host.exe')
    monkeypatch.delenv('G6_STOP', raising=False)
    for cls, body in bodies()[:3]:
        Stores().for_model(cls).put(cls(**body))
    case = CaseRecord.model_validate(CASE)
    archive = home / 'distill/held_out/regression_cases'
    archive.mkdir(parents=True)
    (archive / f'{case.case_id}.json').write_text(
        canonical(case.model_dump(mode='json')) + '\n', encoding='utf-8')
    return case


def admit(case, template='swallow.v1'):
    candidate = r.VerifierCandidate(**(bodies()[3][1] | {
        'form': 'template_instance', 'body': {'template': template, 'bindings': {}}}))
    Stores().for_model(r.VerifierCandidate).put(candidate)
    entry = r.LibraryEntry(**(bodies()[4][1] | {
        'candidate_id': candidate.candidate_id,
        'regression_suite': [{'case_ref': case.case_id, 'verdict': 'concern'}]}))
    Stores().for_model(r.LibraryEntry).put(entry)
    return candidate, entry


def test_unknown_anchor_does_not_replay_cases_through_public_decide(disk, monkeypatch):
    _, entry = admit(disk)
    assert gate.withdraw(entry.entry_id, Library(), reason='retired', person='Owner').verdict == 'withdraw'
    candidate, _ = admit(disk, 'skip_gate.v1')
    registration = 'f' * 64
    assert registration not in gate._policy()[1]['registrations']
    reads = []
    read = gate._recorded_case

    def observe_read(ident):
        reads.append(ident)
        return read(ident)

    monkeypatch.setattr(gate, '_recorded_case', observe_read)
    decision = gate.decide(candidate, Library(), registration)
    assert decision.verdict == 'block'
    assert 'missing owner registration anchor' in decision.reasons
    assert reads == [], f'unknown registration replayed protected cases: {reads}'


def test_owner_withdrawal_does_not_wait_for_running_library_check(disk, monkeypatch):
    _, entry = admit(disk)
    entered, release, withdrawing = Event(), Event(), Event()
    detect = gate.weakening.detect_test_weakening

    def paused_detector(before, after):
        entered.set()
        assert release.wait(10), 'test scheduler did not release the detector'
        return detect(before, after)

    def withdraw():
        withdrawing.set()
        return gate.withdraw(entry.entry_id, Library(), reason='stop this method', person='Owner')

    monkeypatch.setattr(gate.weakening, 'detect_test_weakening', paused_detector)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(library_review_gate.check,
                              {'tests/test_case.py': disk.before_tests},
                              {'tests/test_case.py': disk.after_tests})
        try:
            assert entered.wait(5)
            pending = pool.submit(withdraw)
            assert withdrawing.wait(5)
            try:
                decision = pending.result(timeout=1)
            except TimeoutError:
                completed_while_running = False
            else:
                completed_while_running = decision.verdict == 'withdraw'
        finally:
            release.set()
        findings = running.result(timeout=10)
        assert pending.result(timeout=10).verdict == 'withdraw'
    assert completed_while_running, (
        'owner withdrawal was blocked by execution; the running check first returned '
        f'{[(f["authority"], f["outcome"]) for f in findings]}')
