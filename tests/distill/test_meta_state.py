# SPDX-License-Identifier: Apache-2.0
"""Pure M state transitions, independently of the durable filesystem adapter."""
import importlib
from dataclasses import replace

import pytest
from pydantic import ValidationError

from tests.distill.test_meta import api, experiment_fixture, outcome, policy_fixture, record


def test_in_memory_prediction_state_machine_uses_self_model_and_keeps_rows_immutable():
    m = api()
    store = m.MetaLedger(memory=True)
    row = record(store)
    original = row.model_dump_json()
    with pytest.raises(ValidationError):
        row.kind = 'method_catch'
    with pytest.raises(ValueError, match='already'):
        record(store)
    with pytest.raises(ValueError, match='after|time'):
        store.score(row.decision_id, outcome(row, started=100.), now=103.)
    assert not store.scores()
    store.score(row.decision_id, outcome(row), now=103.)
    assert store.hit_rates() == {'concept_compile': 1.}
    assert store.predictions()[0].model_dump_json() == original
    with pytest.raises(ValueError, match='already'):
        store.score(row.decision_id, outcome(row, value=0.), now=104.)


def test_in_memory_separate_prediction_types_and_unknown_reference_class():
    store = api().MetaLedger(memory=True)
    a = record(store, 'concept_compile', 'a')
    b = record(store, 'concept_compile', 'b')
    c = record(store, 'method_catch', 'c')
    record(store, 'experiment_decide', 'pending')
    unknown = record(store, 'method_catch', 'unknown', count=4)
    for row, value in ((a, 14.), (b, 10.), (c, 14.), (unknown, 14.)):
        store.score(row.decision_id, outcome(row, value), now=103.)
    assert store.hit_rates() == {'concept_compile': .5, 'method_catch': 1., 'experiment_decide': None}


def test_meta_rows_store_outcomes_and_policies_without_earning_authority():
    m = api()
    from g6.core.ledger import digest
    store = m.MetaLedger(memory=True)
    row = m.ConceptOutcome(concept_id='a', family='assertions', admitted=True, evidence_ref=digest('independent'))
    ident = store.record_data(row)
    assert store.data(m.ConceptOutcome) == (row,)
    assert store.record_data(m.SynthesisPolicy()) != ident
    assert store.data(m.SynthesisPolicy) == (m.SynthesisPolicy(),)
    assert store.record_data(row) == ident


def test_experiment_decision_records_simulation_before_returning_choice():
    m = api()
    from g6.self_model.model import SelfModel
    sim = importlib.import_module('g6.self_model.simulate')
    from tests.self_model.test_simulate import setup
    state, experiments = experiment_fixture()
    forecasts = (m.ExperimentForecast(experiments[1], ('concern', 'no_concern'), ('admit', 'refuse')),)
    action, refs, manifest, graph = setup(sim)
    action = action.model_copy(update={'kind': 'experiment_decide', 'action_id': 'separating'})
    refs = tuple(ref.model_copy(update={'kind': 'experiment_decide'}) for ref in refs)
    store = m.MetaLedger(memory=True)
    controller = m.MetaController(store)
    choice, prediction = controller.experiment(state, forecasts, action=action, snapshot=SelfModel(),
        reference_actions=refs, relationships=graph, protected_manifest=manifest, now=100.)
    assert choice.experiment.id == prediction.subject_id == 'separating'
    assert store.predictions() == (prediction,)
    assert prediction.simulation.action_sha256 == action.sha256
    assert state.attempted == ()
    assert store.data(m.ExperimentChoice) == (choice,)


def test_binary_information_gain_counts_only_decision_relevant_information():
    m = api()
    state, experiments = experiment_fixture()
    # The outcome reveals WHICH candidate inside each decision class, but
    # carries no information about whether the final decision changes.
    forecast = m.ExperimentForecast(experiments[1], ('a', 'b', 'a', 'b'),
                                    ('admit', 'admit', 'refuse', 'refuse'))
    assert m.choose_experiment(state, (forecast,)) is None
    with pytest.raises(ValueError, match='binary|two'):
        m.ExperimentForecast(experiments[1], ('a', 'b', 'c'), ('admit', 'refuse', 'refuse'))


@pytest.mark.parametrize('change', ['action', 'receipt', 'measurement'])
def test_pure_state_machine_refuses_unbound_or_self_predicted_outcomes(change):
    from g6.self_model.model import Field, NamedField
    store = api().MetaLedger(memory=True)
    row = record(store)
    result = outcome(row)
    if change == 'action':
        result = result.model_copy(update={'action_sha256': '0' * 64})
    elif change == 'receipt':
        result = result.model_copy(update={'prediction_record': row.receipt.model_copy(update={'value': 'forged'})})
    else:
        result = result.model_copy(update={'measurements': (NamedField(name='yield', field=Field.predicted(
            14., source='g6.self_model.simulate', observed_at=102., ttl_s=1000.)),)})
    with pytest.raises(ValueError):
        store.score(row.decision_id, result, now=103.)
    assert store.scores() == ()


def test_policy_evaluation_reserves_a_split_even_when_replay_fails():
    m, workflow, base, frozen, clock = policy_fixture()
    proposal = m.policy_proposal(base, m.SynthesisPolicy(try_predicate=False), proposer='M')
    def failing_factory(ctx):
        raise RuntimeError('fixture replay failed')
    m.evaluate_policy(workflow, base, proposal, failing_factory, frozen=frozen, reuse_index=1, clock=clock)
    with pytest.raises(ValueError, match='spent|stale'):
        m.evaluate_policy(workflow, base, proposal, failing_factory, frozen=frozen, reuse_index=1, clock=clock)
    assert frozen.journal.entries()[0]['body']['reuse_index'] == 1


def test_policy_cannot_change_other_profile_fields_or_evaluation_split():
    m, workflow, base, frozen, clock = policy_fixture()
    proposal = m.policy_proposal(base, m.SynthesisPolicy(try_predicate=False), proposer='M')
    tampered = replace(proposal, candidate=proposal.candidate.model_copy(update={'retries': 3}))
    with pytest.raises(ValueError, match='only|frozen'):
        m.evaluate_policy(workflow, base, tampered, None, frozen=frozen, reuse_index=1)
    with pytest.raises(ValueError, match='split|frozen'):
        m.evaluate_policy(workflow, base, proposal, None, frozen=frozen, reuse_index=1, split='dev')
    assert frozen.journal.entries() == []


def test_no_library_methods_is_an_explicit_empty_plan():
    from g6.cognitive_bridge.metacognition import ProblemSignature
    from g6.core.selector import rank_library_methods
    context = ProblemSignature('tests', 'known', 'weakening', 2, 10.)
    assert rank_library_methods((), (), context) is None


def test_reuse_does_not_offer_withdrawn_or_superseded_methods():
    m = api()
    from g6.distill.records import LibraryEntry
    from tests.distill.test_synthesis import MemoryLibrary
    entries = MemoryLibrary(['assert_strength.v1', 'skip_gate.v1']).list()
    inactive = tuple(LibraryEntry(**(entry.model_dump() | {'entry_id': None, 'status': status, 'authority': 'A0'}))
                     for entry, status in zip(entries, ('withdrawn', 'superseded')))
    assert m.selector_feed(inactive, ()).variants == ()


def test_protected_meta_bridge_is_the_only_m_importer_of_metaharness():
    import ast
    from pathlib import Path
    root = Path(__file__).parents[2] / 'src/g6/distill/meta'
    for path in root.glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            names = [node.module or ''] if isinstance(node, ast.ImportFrom) else (
                [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                assert not name.startswith(('g6.mcp', 'g6.core.llm', 'subprocess', 'socket', 'requests'))
                if name.startswith('g6.metaharness'):
                    assert path.name == 'policy_gate.py'


def test_policy_planning_reuses_selection_race_without_spending_held_out():
    m, workflow, base, frozen, clock = policy_fixture()
    with pytest.raises(ValueError, match='selection'):
        m.rank_policies(workflow, base, (m.SynthesisPolicy(try_predicate=False),), None, frozen=frozen)
    from g6.core.ledger import ChainLedger
    from g6.metaharness.schema import SuiteCase
    workflow = replace(workflow, suite=replace(workflow.suite, selection=(SuiteCase('s-meta', 'selection-input', '1'),)))
    confirmation = {**frozen.confirmation, 'workflow_digest': workflow.digest()}
    frozen = m.FrozenMetaEvaluation.freeze(workflow, confirmation=confirmation,
        method_case_ids=('separate-method-case',), journal=ChainLedger())
    policies = (m.SynthesisPolicy(try_predicate=False), m.SynthesisPolicy(repair_budget=0))
    result = m.rank_policies(workflow, base, policies, lambda ctx: lambda contract: '1',
                             frozen=frozen, clock=clock)
    assert result.cases_used == 1
    assert frozen.journal.entries() == []


@pytest.mark.parametrize('settings,expected', [({'try_predicate': False}, 1),
    ({'max_synthesis_calls': 1}, 1), ({'max_synthesis_calls': 2}, 2), ({}, 3)])
def test_synthesis_policy_meters_actual_fake_calls(settings, expected):
    m = api()
    from g6.core.llm import FakeLLM
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import fixture
    concept, case = fixture('def test_a():\n    assert calc() == 4\n')
    caller = FakeLLM(['{}'] * 3)
    result = Synthesizer(caller=caller, policy=m.SynthesisPolicy(**settings)).synthesise(concept, [case])
    assert result.synthesis_calls == len(caller.calls) == expected
    if not settings.get('try_predicate', True):
        assert not any('Propose predicate' in call[1] for call in caller.calls)


def test_template_order_is_data_and_reuse_still_precedes_it():
    m = api()
    from g6.distill.detectors import SEED_TEMPLATES
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import MemoryLibrary, fixture
    concept, case = fixture('import pytest\n@pytest.mark.skip\ndef test_a():\n    assert True\n')
    order = ('skip_gate.v1', *(name for name in sorted(SEED_TEMPLATES) if name != 'skip_gate.v1'))
    policy = m.SynthesisPolicy(template_order=order)
    result = Synthesizer(policy=policy).synthesise(concept, [case])
    assert result.candidates[0].body.template == 'skip_gate.v1'
    reuse = Synthesizer(policy=policy).synthesise(concept, [case], library=MemoryLibrary(['assert_strength.v1']))
    assert reuse.candidates == []
    assert len(reuse.reuses) == 1 and reuse.reuses[0].concept_id == concept.concept_id


def test_concept_reuse_and_policy_choices_record_per_action_instance():
    m = api()
    from g6.cognitive_bridge.metacognition import ProblemSignature
    from g6.self_model.model import SelfModel
    from tests.distill.test_synthesis import MemoryLibrary
    from tests.self_model.test_simulate import setup
    sim = importlib.import_module('g6.self_model.simulate')
    def inputs(kind, subject):
        action, refs, manifest, graph = setup(sim)
        return dict(action=action.model_copy(update={'kind': kind, 'action_id': subject + ':tick-1'}),
                    reference_actions=tuple(ref.model_copy(update={'kind': kind}) for ref in refs),
                    snapshot=SelfModel(), relationships=graph, protected_manifest=manifest, now=100.)
    store = m.MetaLedger(memory=True)
    controller = m.MetaController(store)
    estimate = m.concept_worth('assertions', (), expected_delta_utility=2., recurrence=3., cost=2.)
    chosen, _, concept = controller.concept({'concept': estimate}, **inputs('concept_compile', 'concept'))
    assert chosen == concept.subject_id == 'concept'
    entries = MemoryLibrary(['assert_strength.v1']).list()
    ident = entries[0].entry_id
    data = inputs('method_catch', ident)
    actions = {ident: data.pop('action')}
    ranking, predictions = controller.reuse(entries, (), ProblemSignature('tests', 'known', 'weakening', 2, 10.),
                                             actions=actions, **data)
    assert ranking.admitted_trials == 0 and predictions[0].subject_id == ident
    _, _, base, _, _ = policy_fixture()
    policy = m.SynthesisPolicy(try_predicate=False)
    proposal = m.policy_proposal(base, policy, proposer='M')
    result, prediction = controller.policy(base, policy, proposer='M',
                                            **inputs('synthesis_policy', proposal.candidate_digest))
    assert result == proposal and prediction.subject_id == proposal.candidate_digest
    assert {row.kind for row in store.predictions()} == {'concept_compile', 'method_catch', 'synthesis_policy'}


@pytest.mark.parametrize('cap', [1, 2])
def test_tighter_synthesis_budget_counts_every_inner_retry_send(cap):
    m = api()
    from g6.core.llm import EmptyRetry, FakeLLM
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import MemoryLibrary, quiet_case
    concept, case = quiet_case()
    caller = FakeLLM(default='')
    result = Synthesizer(caller=EmptyRetry(caller), policy=m.SynthesisPolicy(max_synthesis_calls=cap)).synthesise(
        concept, [case], library=MemoryLibrary([]))
    assert result.synthesis_calls == len(caller.calls) == cap


def test_frozen_failure_replay_workflow_measures_utility_under_unchanged_gate():
    m = api()
    from g6.core.ledger import ChainLedger, digest
    from g6.core.llm import FakeLLM
    from g6.distill.records import FailureRecord
    from g6.metaharness.schema import Model, Profile
    from tests.distill.test_records import bodies
    from tests.metaharness._rig import Clock
    failures, sequences = [], []
    for i in range(31):
        failure = FailureRecord(**(bodies()[0][1] | {'record_id': None, 'case_ref': digest(i),
                                                   'provenance': {'unit_id': f'bug-{i}', 'cohort': 'development'},
                                                   'family': 'baseline-ok' if i % 2 else 'gain'}))
        failures.append(failure)
        sequences.append(m.ReplaySequence(case_id=f'meta-{i}', failure_ids=(failure.record_id,), utility_target=0.))
    seen = []
    def replay(policy, failures, caller):
        seen.append((policy, failures))
        return m.ReplayMeasurement(tp=2 if not policy.try_predicate else 1,
            fp=1 if policy.try_predicate and failures[0].family == 'gain' else 0,
            evidence_ref=digest('fixture-independent-field'), origin='independent_field')
    runner = m.SynthesisReplay(failures=failures, replay=replay)
    workflow = runner.workflow(name='M-replay-fixture', dev=tuple(sequences[:1]), held_out=tuple(sequences[1:]),
                               model=Model(FakeLLM(default='unused')))
    base = Profile(routing={'synthesis': 'declared'}, prompt=m.SynthesisPolicy().model_dump_json())
    proposal = m.policy_proposal(base, m.SynthesisPolicy(try_predicate=False), proposer='M')
    confirmation = {'workflow_digest': workflow.digest(), 'held_out_digest': workflow.suite.digest('held_out'),
                    'judging_sha256': digest('frozen-judging')}
    frozen = m.FrozenMetaEvaluation.freeze(workflow, confirmation=confirmation,
        method_case_ids=('method-case-only',), journal=ChainLedger())
    report = m.evaluate_policy(workflow, base, proposal, runner.factory, frozen=frozen, reuse_index=1, clock=Clock())
    decision = m.judge_policy(report, proposal, workflow, base=base, frozen=frozen,
                               judging_sha256=digest('frozen-judging'))
    assert report.n == 30 and report.success['wins'] == 15
    assert decision.verdict == 'recommend' and decision.requires_person
    assert all(tuple(failures) and not hasattr(policy, 'utility_target') for policy, failures in seen)
    with pytest.raises(ValueError, match='repeat|overlap'):
        runner.workflow(name='bad-split', dev=tuple(sequences[:1]), held_out=tuple(sequences),
                        model=Model(FakeLLM(default='unused')))


def test_worth_records_feature_inputs_without_inventing_earned_credit():
    m = api()
    estimate = m.concept_worth('assertions', (), expected_delta_utility=2., recurrence=3., cost=2.,
                               met_before=4, nearly_caught=True)
    assert estimate.met_before == 4 and estimate.nearly_caught
    assert estimate.probability == .5 and estimate.observations == 0
    assert estimate.expected_delta_utility == 2. and estimate.recurrence == 3. and estimate.cost == 2.
