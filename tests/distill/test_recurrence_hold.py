"""A recurrence suspends automatic use without undoing the admission decision."""
import asyncio

import pytest

from g6.core.ledger import LedgerError, digest
from g6.distill import acceptance_gate as gate
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill.records import FailureRecord, LibraryEntry, RemovalCheck, VerifierCandidate
from g6.distill.rectify_gate import Rectifier
from tests.distill.test_rectify import library_check, rect
from tests.distill import test_review3_repairs

state_home = test_review3_repairs.state_home


def removal():
    entry = library_check()
    rows = Stores().for_model(FailureRecord).list()
    pinning = rect(rows).pinning.model_dump() | {'test_ref': f'library:{entry.entry_id}'}
    result = Rectifier().submit(rows[-1].record_id, rect(rows, pinning=pinning))
    return entry, rows, result


def recur(rows, *, independent=False, label='again'):
    row = FailureRecord(**(rows[0].model_dump(exclude={'record_id'}) | {
        'observed_by': 'independent' if independent else 'self', 'evidence': [],
        'source': 'experiment' if independent else 'person', 'case_ref': digest(label),
        'observed_at': '2026-11-22'}))
    Stores().for_model(FailureRecord).put(row)
    Rectifier().reconcile()
    return row


def test_self_observed_recurrence_holds_and_preserves_admission(state_home):
    entry, rows, removed = removal()
    again = recur(rows)
    [held] = Library().by_status('held')
    assert held.authority == 'A0' and held.held_recurrences == [again.record_id]
    assert held.certificate_sha256 == entry.certificate_sha256
    assert held.reason.startswith('G6_E_DISTILL_RECURRENCE_HELD:')
    assert Stores().for_model(LibraryEntry).get(entry.entry_id) == entry
    assert Rectifier().mode(rows[0].finding_class).removing_check is None
    assert Rectifier().mode(rows[0].finding_class).recurrences_after_removal == 1
    assert removed.pinning == Stores().for_model(type(removed)).list()[-1].pinning
    Rectifier().reconcile()
    assert Library().list() == [held]


def test_held_method_is_not_applied_in_review_or_as_a_dependency_or_removing_check(state_home):
    from g6.harness.library_review_gate import check
    entry, rows, _ = removal()
    recur(rows)
    [held] = Library().list()
    assert held.status == 'held'
    assert check({'tests/test_a.py': 'assert 1'}, {'tests/test_a.py': 'pass'}, entry_id=entry.entry_id) == []
    candidate = Stores().for_model(VerifierCandidate).get(entry.candidate_id)
    with pytest.raises(ValueError, match='held'):
        gate._run(candidate, object(), Library().list())
    with pytest.raises(LedgerError, match='current admitted'):
        Rectifier().submit(rows[0].record_id, rect(rows, pinning=rect(rows).pinning.model_dump() | {
            'test_ref': f'library:{entry.entry_id}'}))
    answer = gate.decide(candidate, Library(), '')
    assert answer.verdict in {'block', 'refuse'} and 'held' in ' '.join(answer.reasons)
    assert gate.promote(held.entry_id, Library(), '').verdict == 'refuse'


def test_independent_recurrence_demotes_a_held_method(state_home):
    _, rows, _ = removal()
    recur(rows)
    assert Library().list()[0].status == 'held'
    recur(rows, independent=True, label='independent')
    [entry] = Library().list()
    assert (entry.status, entry.authority) == ('demoted', 'A0')
    assert Rectifier().mode(rows[0].finding_class).recurrences_after_removal == 2


def test_independent_complete_replay_lifts_hold_without_erasing_recurrence(state_home):
    entry, rows, removed = removal()
    again = recur(rows)
    [held] = Library().list()
    assert held.status == 'held'
    check = RemovalCheck(**(removed.pinning.model_dump() | {
        'replayed': [r.record_id for r in [*rows, again]], 'receipt_sha256': digest('fresh independent replay')}))
    restored = Rectifier().release_hold(held.entry_id, [check])
    assert (restored.status, restored.authority) == ('admitted', 'A2')
    assert restored.certificate_sha256 == entry.certificate_sha256
    Rectifier().reconcile()
    assert Library().list() == [restored]
    assert Rectifier().mode(rows[0].finding_class).recurrences_after_removal == 1
    recur(rows, label='later recurrence')
    assert Library().list()[0].status == 'held'


@pytest.mark.parametrize('invalid', ['missing', 'failed', 'stale', 'wrong_method'])
def test_incomplete_or_stale_replay_cannot_lift_hold(state_home, invalid):
    _, rows, removed = removal()
    again = recur(rows)
    [held] = Library().list()
    assert held.status == 'held'
    data = removed.pinning.model_dump() | {'replayed': [r.record_id for r in [*rows, again]],
                                          'receipt_sha256': digest('fresh')}
    data.update({'missing': {'replayed': []}, 'failed': {'fixed_passed': False},
                 'stale': {'receipt_sha256': removed.pinning.receipt_sha256},
                 'wrong_method': {'test_ref': 'tests/test_other.py'}}[invalid])
    with pytest.raises(LedgerError):
        Rectifier().release_hold(held.entry_id, [RemovalCheck(**data)])
    assert Library().list() == [held]


@pytest.mark.parametrize('accepted', [False, True])
def test_existing_dialog_can_lift_hold_only_after_person_accepts(state_home, monkeypatch, accepted):
    from g6.mcp import consent, distill
    from g6.mcp.distill_backend import Service
    entry, rows, _ = removal()
    recur(rows)
    [held] = Library().list()
    assert held.status == 'held'
    dialogs, tools = [], {}

    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)

    async def dialog(ctx, message, schema):
        dialogs.append(message)
        return ('accept' if accepted else 'decline'), {'confirm': 'change', 'name': 'Person'}, ''

    monkeypatch.setattr(consent, 'elicit', dialog)
    service = Service()
    distill.register(Server(), object, service.jobs, backend=service)
    answer = asyncio.run(tools['g6_library_decide'](held.entry_id, 'restore', 'reviewed recurrence', object()))
    assert dialogs
    [current] = Library().list()
    if accepted:
        assert answer['verdict'] == 'lift_hold'
        assert (current.status, current.authority) == ('admitted', 'A2')
        assert current.decided_by.person == 'Person'
        assert current.certificate_sha256 == entry.certificate_sha256
        Rectifier().reconcile()
        assert Library().list() == [current]
    else:
        assert answer['ok'] is False and current == held


def test_held_method_is_excluded_from_synthesis_reuse():
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import MemoryLibrary, fixture
    concept, case = fixture()
    library = MemoryLibrary(['assert_strength.v1'])
    entry = library.values[0]
    library.values[0] = LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'status': 'held', 'authority': 'A0', 'held_recurrences': [digest('recurrence')]}))
    result = Synthesizer().synthesise(concept, [case], library=library)
    assert result.reuses == []
    assert all(entry.entry_id not in candidate.lineage for candidate in result.candidates)


def test_client_reporting_route_holds_without_demoting(state_home):
    from g6.distill import surface
    from g6.distill.intake import class_key
    entry = library_check()
    first = surface.failure_record('g6_refusal', 'g6_self', '{}', 'first client report')
    row = Stores().for_model(FailureRecord).get(first['record_id'])
    key = class_key(row)
    pinning = rect([row]).pinning.model_dump() | {'test_ref': f'library:{entry.entry_id}'}
    Rectifier().submit(row.record_id, rect([row], failure_mode=key, pinning=pinning))
    again = surface.failure_record('g6_refusal', 'g6_self', '{}', 'repeated client report')
    assert again['observed_by'] == 'self'
    [held] = Library().list()
    assert (held.status, held.authority) == ('held', 'A0')
    assert held.held_recurrences == [again['record_id']]
    history = Stores().for_model(LibraryEntry).list()
    assert all(value.status != 'demoted' for value in history)
    assert Rectifier().mode(key).removing_check is None
    assert Rectifier().mode(key).recurrences_after_removal == 1


def test_held_test_method_cannot_run_in_patch_review_even_by_historical_id(state_home):
    from g6.distill.records import TemplateInstance
    from g6.harness.library_review_gate import check
    from tests.distill.test_review3_repairs import seed
    *_, candidate, entry = seed()
    candidate = VerifierCandidate(**(candidate.model_dump(exclude={'candidate_id'}) | {
        'form': 'template_instance', 'body': TemplateInstance(template='assert_strength.v1', bindings={})}))
    Stores().for_model(VerifierCandidate).put(candidate)
    entry = LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {'candidate_id': candidate.candidate_id}))
    Stores().for_model(LibraryEntry).put(entry)
    before = {'tests/test_a.py': 'def test_a():\n    assert calc() == 4\n'}
    after = {'tests/test_a.py': 'def test_a():\n    pass\n'}
    assert check(before, after, entry_id=entry.entry_id)[0]['outcome'] == 'defect_detected'
    held = LibraryEntry(**(entry.model_dump(exclude={'entry_id'}) | {
        'version': 2, 'status': 'held', 'authority': 'A0', 'held_recurrences': [digest('recurrence')]}))
    Stores().for_model(LibraryEntry).put(held)
    assert check(before, after, entry_id=entry.entry_id) == []
    with pytest.raises(ValueError, match='held'):
        gate._run(candidate, object(), Library().list())


def test_held_state_cannot_carry_application_authority():
    from pydantic import ValidationError
    from tests.distill.test_records import bodies
    for authority in ('A1', 'A2'):
        with pytest.raises(ValidationError, match='authority does not match'):
            LibraryEntry(**(bodies()[4][1] | {'status': 'held', 'authority': authority}))


def test_hook_recurrence_holds_before_the_reporting_route_returns(state_home):
    from g6.distill import surface
    from g6.distill.intake import class_key
    entry = library_check()

    def report(label):
        surface.record_reproduction({'tool_name': 'g6_review_patch', 'tool_response': {
            'assessment': {'outcome': 'defect_detected', 'reasons': [{'reproduction': label}]}}})

    report('first reproduction')
    row = Stores().for_model(FailureRecord).list()[-1]
    pinning = rect([row]).pinning.model_dump() | {'test_ref': f'library:{entry.entry_id}'}
    Rectifier().submit(row.record_id, rect([row], failure_mode=class_key(row), pinning=pinning))
    report('recurring reproduction')
    [held] = Library().list()
    assert (held.status, held.authority) == ('held', 'A0')


def test_independent_recurrence_still_lowers_a_person_demoted_a1_method(state_home):
    entry, rows, _ = removal()
    assert gate.demote(entry.entry_id, Library(), person='Person', reason='dispute').verdict == 'demote'
    assert Library().list()[0].authority == 'A1'
    recur(rows, independent=True)
    assert (Library().list()[0].status, Library().list()[0].authority) == ('demoted', 'A0')
