"""One offline path through the installed slices; external custody/ports are fakes."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.core.llm import FakeLLM
from g6.distill.ledger import Stores
from g6.distill.library import Library
from g6.distill import records as r


@pytest.fixture
def home(monkeypatch):
    home = Path('.build/home') / ('e2e-' + uuid4().hex)
    home.mkdir(parents=True)
    monkeypatch.setenv('G6_HOME', str(home.absolute()))
    return home


def test_failure_to_review_prediction_and_draft_rehearsal(home, monkeypatch):
    from g6.mcp.distill_backend import Service
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_records import bodies
    from tests.distill.test_acceptance_measurements import sample
    from tests.distill.steady_clock import steady_clock
    gate, _, frozen, _ = sample.__wrapped__()
    steady_clock(monkeypatch)       # the cost bound is judged on a deterministic clock, not a shared runner's
    # A fake host custody port supplies sealed, independently labelled fixture
    # cases. Neither the proposer nor synthesis receives these cases/labels.
    registration = digest([o.case.case_id for o in frozen.acceptance])
    policy, settings = gate._policy()
    monkeypatch.setattr(gate, '_policy', lambda: (policy, settings | {
        'registrations': {registration: {'path': 'synthetic-custody'}}}))
    def custody(ident, candidate, library):
        assert ident == registration
        assert not set(candidate.training_case_ids) & {o.case.case_id for o in frozen.acceptance}
        return frozen
    monkeypatch.setattr(gate, '_load', custody)
    before = {'tests/test_train.py': 'def test_training():\n    assert answer(2) == 3\n'}
    after = {'tests/test_train.py': 'def test_training():\n    pass\n'}
    failure = r.FailureRecord(**(bodies()[0][1] | {'case_ref': digest((before, after)),
        'provenance': {'scope': 'g6_public_archive', 'case': {'before': before, 'after': after}}}))
    Stores().for_model(r.FailureRecord).put(failure)
    fake = FakeLLM(default=json.dumps({key: bodies()[1][1][key] for key in
        ('statement', 'observable_features', 'falsifier', 'prediction', 'worth')}))
    class Jobs:
        def start(self, operation):
            return {'status': 'completed', 'result': operation()}
    service = Service(jobs=Jobs(), synthesizer=Synthesizer(caller=fake), registration=registration)
    result = service.distill_start('test_weakening', failure.record_id)['result']
    assert len(fake.calls) == 1  # concept proposal; seed synthesis needs no call
    assert result['decisions'][0]['verdict'] == 'admit_probation'
    repeated = service.distill_start('test_weakening', failure.record_id)['result']
    assert repeated['decisions'] == [] and repeated['candidates'] == []
    assert repeated['reuses'][0]['entry_id'] == result['decisions'][0]['entry_id']
    looks = [row for row in ChainLedger(home / 'distill/acceptance_gate.jsonl').entries()
             if row['kind'] == 'reserve']
    assert len(looks) == 1
    assert len(ChainLedger(home / 'distill/reuse_ledger.jsonl').entries()) == 1
    assert len(fake.calls) == 1
    [concept] = Stores().for_model(r.Concept).list()
    candidate = Stores().for_model(r.VerifierCandidate).get(result['candidates'][0]['candidate_id'])
    assert concept.from_failures == [failure.record_id] and candidate.concept_id == concept.concept_id
    [entry] = Library().list()
    # Host-measured field labels are separate from both predictions and fires.
    labels = home / 'distill/held_out/field' / (candidate.candidate_id + '.jsonl')
    labels.parent.mkdir(parents=True, exist_ok=True)
    chain = ChainLedger(labels)
    for index in range(30):
        body = gate.FieldOutcome(candidate_id=candidate.candidate_id, fire_id=f'field-{index}', unit_id=f'bug-{index}',
            source='oracle', label='confirmed', receipt_sha256=digest(('measured', index))).model_dump(mode='json')
        chain.append('field', digest(body), body)
    assert gate.promote(entry.entry_id, Library(), registration).verdict == 'admit'
    [admitted] = Library().list()
    assert admitted.status == 'admitted' and admitted.authority == 'A2'

    # Real g6_review_patch backend; only repository reads are faked, no git or child tests.
    from g6.harness import patch_review
    from g6.workforce import repo_tests
    from g6.workforce.repo_tests import Change, Ignored
    change = Change(home, 'HEAD', 'a' * 40, 'a' * 40, tuple(before), before, after, (), ())
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda path: home)
    monkeypatch.setattr(repo_tests, 'snapshot', lambda *a, **kw: {'fixture': 'fixed'})
    monkeypatch.setattr(repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(repo_tests, 'test_related', lambda *a: tuple(before))
    monkeypatch.setattr(repo_tests, 'related_texts', lambda *a: (before, after))
    monkeypatch.setattr(repo_tests, 'loadable_changes', lambda *a: {})
    monkeypatch.setattr(repo_tests, 'ignored_inputs', lambda *a: Ignored((), {}, True))
    monkeypatch.setattr(repo_tests, 'diff_text', lambda *a: '')
    review = patch_review.review_patch(home, task='Preserve assertions', requirements=[], tests=[])
    runs = review['assessment']['scope']['checks_run']
    assert any(row.get('provenance') == 'library' and row.get('library', {}).get('authority') == 'A2' for row in runs)
    assert review['completion_state'] == 'blocked-escalated'
    assert Library().export(admitted.entry_id)['entries'][0]['entry_id'] == admitted.entry_id

    # Real runner, meta controller, recorded simulation and measured scoring;
    # fake command port receives a draft plan, never a merge.
    from tests.distill.test_research_runner import fixture
    from tests.self_training.test_research_merge_gate import fixture as merge_fixture
    from g6.distill.repository_adapter import GhRepository
    from g6.self_training.autonomy_gate import Autonomy
    _, runner, _, _, _, _ = fixture(push=True, draft_pr=True)
    _, _, _, _, change, _ = merge_fixture()
    commands = []
    def command(argv, **kwargs):
        commands.append(argv)
        return 'https://example.test/draft/1'
    runner.repository = GhRepository(root='/fixture', command=command, stop=lambda: False,
        session_trailers=('Co-Authored-By: Test <test@example.test>', 'Claude-Session: integration-fixture'),
        config=runner.config)
    execute = runner.execute
    runner.execute = lambda *a, **kw: replace(execute(*a, **kw), change=change,
                                             library_changes=(admitted.entry_id,))
    tick = runner.tick(now=100.)
    assert tick.status == 'completed' and tick.pr == 'https://example.test/draft/1'
    assert runner.meta.hit_rates() == {'experiment_decide': 1.}
    cycle = runner.journal.entries()[-1]['body']
    assert cycle['prediction'] and cycle['scores'] and cycle['merge'] is None
    assert any(argv[:3] == ('gh', 'pr', 'create') and '--draft' in argv for argv in commands)
    assert not any(argv[:3] == ('gh', 'pr', 'merge') for argv in commands)
    # Full off is stronger than merge off: it forbids even a new rehearsal tick.
    runner.config = Autonomy()
    assert runner.tick(now=104.).status == 'off'
    assert Service(runners={'fixture': runner}, now=lambda: 103.).research_digest('fixture')['ticks'] == 1


def test_library_export_refuses_user_or_unknown_training_provenance(home):
    from tests.distill.test_records import bodies
    # This is a read-boundary test: a forged caller flag cannot waive provenance.
    records = {cls: cls(**body) for cls, body in bodies()[:5]}
    class Store:
        def __init__(self, cls):
            self.cls = cls
        def get(self, ident):
            return records[self.cls]
        def list(self):
            return [records[self.cls]]
    from unittest.mock import patch
    with patch.object(Stores, 'for_model', lambda self, cls: Store(cls)):
        entry = records[r.LibraryEntry]
        with pytest.raises(ValueError, match='local|provenance'):
            Library().export(entry.entry_id)


def test_public_wrapper_cannot_export_a_user_learned_parent(home):
    from tests.distill.test_records import bodies
    stores = Stores()
    def learned(scope, label, parent=None):
        failure = r.FailureRecord(**(bodies()[0][1] | {'case_ref': digest(label), 'provenance': {'scope': scope}}))
        stores.for_model(r.FailureRecord).put(failure)
        concept = r.Concept(**(bodies()[1][1] | {'from_failures': [failure.record_id]}))
        stores.for_model(r.Concept).put(concept)
        contract = r.MethodContract(**bodies()[2][1])
        if not stores.for_model(r.MethodContract).list():
            stores.for_model(r.MethodContract).put(contract)
        candidate = r.VerifierCandidate(**(bodies()[3][1] | {'concept_id': concept.concept_id,
            'training_case_ids': [failure.case_ref], 'form': 'reuse' if parent else 'template_instance',
            'body': parent.entry_id if parent else {'template': 'assert_strength.v1', 'bindings': {}},
            'lineage': [parent.entry_id] if parent else []}))
        stores.for_model(r.VerifierCandidate).put(candidate)
        entry = r.LibraryEntry(**(bodies()[4][1] | {'concept_id': concept.concept_id,
            'candidate_id': candidate.candidate_id, 'parents': [parent.entry_id] if parent else []}))
        stores.for_model(r.LibraryEntry).put(entry)
        return entry
    private = learned('user_repository', 'private')
    public = learned('g6_public_archive', 'public-wrapper', private)
    with pytest.raises(ValueError, match='provenance.*local'):
        Library().export(public.entry_id)
