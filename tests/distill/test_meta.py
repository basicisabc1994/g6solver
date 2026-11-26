# SPDX-License-Identifier: Apache-2.0
"""M learns advisory data; outcomes, authority and frozen judging stay outside it."""
import importlib
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from g6.core.ledger import LedgerCorrupt, digest
from g6.self_model.model import Field, NamedField, SelfModel
from tests.self_model.test_simulate import setup


def api():
    assert importlib.util.find_spec('g6.distill.meta') is not None, 'meta level M is not implemented'
    return importlib.import_module('g6.distill.meta')


def measured(value, at=100., source='fixture.measurement'):
    return Field.measured(value, source=source, observed_at=at, ttl_s=1000.)


def record(store, kind='concept_compile', subject='concept-1', count=30):
    sim = importlib.import_module('g6.self_model.simulate')
    action, refs, manifest, graph = setup(sim, count=count)
    action = action.model_copy(update={'kind': kind, 'action_id': subject})
    refs = tuple(ref.model_copy(update={'kind': kind}) for ref in refs)
    return store.predict(kind=kind, subject_id=subject, action=action, snapshot=SelfModel(),
                         reference_actions=refs, relationships=graph, protected_manifest=manifest, now=100.)


def outcome(row, value=14., started=101., receipt=None):
    sim = importlib.import_module('g6.self_model.simulate')
    return sim.Outcome(action_sha256=row.simulation.action_sha256, action_started_at=started,
                       action_finished_at=102., prediction_record=receipt or row.receipt,
                       measurements=() if value is None else (NamedField(name='yield', field=measured(value, 102.)),))


def test_prediction_is_durable_immutable_and_scored_only_after_outcome(tmp_path):
    m = api()
    store = m.MetaLedger(tmp_path / 'meta.jsonl')
    row = record(store)
    assert store.predictions() == (row,)
    assert row.simulation.effects[0].prediction.expected == 13.
    assert m.MetaLedger(tmp_path / 'meta.jsonl').predictions() == (row,)
    with pytest.raises(ValidationError):
        row.subject_id = 'rewritten'
    with pytest.raises(ValueError, match='already|duplicate'):
        record(store)
    with pytest.raises(ValueError, match='after|time'):
        store.score(row.decision_id, outcome(row, started=100.), now=103.)
    assert store.scores() == ()
    assert store.score(row.decision_id, outcome(row), now=103.).scores[0].status == 'held'
    with pytest.raises(ValueError, match='already|scored'):
        store.score(row.decision_id, outcome(row, value=0.), now=104.)
    assert store.predictions() == (row,)


def test_type_hit_rates_ignore_unscored_and_unknown_predictions(tmp_path):
    m = api()
    store = m.MetaLedger(tmp_path / 'meta.jsonl')
    for kind, subject, value in [('concept_compile', 'a', 14.), ('concept_compile', 'b', 10.),
                                  ('method_catch', 'c', 14.)]:
        row = record(store, kind, subject)
        store.score(row.decision_id, outcome(row, value), now=103.)
    record(store, 'experiment_decide', 'unscored')
    unknown = record(store, 'method_catch', 'unknown', count=4)
    assert unknown.simulation.effects[0].prediction is None
    assert store.hit_rates() == {'concept_compile': .5, 'method_catch': 1., 'experiment_decide': None}


@pytest.mark.parametrize('change', ['receipt', 'action', 'prediction'])
def test_scoring_rejects_unbound_or_predicted_evidence(tmp_path, change):
    store = api().MetaLedger(tmp_path / 'meta.jsonl')
    row = record(store)
    result = outcome(row)
    if change == 'receipt':
        result = result.model_copy(update={'prediction_record': measured('forged', source='runner.cycle_log.append')})
    elif change == 'action':
        result = result.model_copy(update={'action_sha256': '0' * 64})
    else:
        result = result.model_copy(update={'measurements': (NamedField(name='yield', field=Field.predicted(
            14., source='g6.self_model.simulate', observed_at=102., ttl_s=1000.)),)})
    with pytest.raises(ValueError):
        store.score(row.decision_id, result, now=103.)
    assert store.scores() == ()


def test_prediction_chain_detects_edits(tmp_path):
    m = api()
    path = tmp_path / 'meta.jsonl'
    store = m.MetaLedger(path)
    record(store)
    body = json.loads(path.read_text(encoding='utf-8'))
    body['body']['subject_id'] = 'after-the-fact'
    path.write_text(json.dumps(body) + '\n', encoding='utf-8')
    with pytest.raises(LedgerCorrupt):
        m.MetaLedger(path).predictions()


def test_family_worth_uses_outcomes_and_observable_features():
    m = api()
    rows = (m.ConceptOutcome(concept_id='a', family='assertions', admitted=True, evidence_ref=digest('a')),
            m.ConceptOutcome(concept_id='b', family='assertions', admitted=False, evidence_ref=digest('b')),
            m.ConceptOutcome(concept_id='c', family='other', admitted=True, evidence_ref=digest('c')))
    worth = m.concept_worth('assertions', rows, expected_delta_utility=2., recurrence=3., cost=2., observable=True)
    assert worth.probability == .5
    assert worth.value_per_cost == 1.5
    assert m.concept_worth('unseen', rows, expected_delta_utility=2., recurrence=3., cost=2.,
                           observable=False).value_per_cost == 0.
    with pytest.raises(ValueError, match='duplicate|conflict'):
        m.concept_worth('assertions', (*rows, rows[0]), expected_delta_utility=1., recurrence=1., cost=1.)


def test_selector_feed_is_read_only_and_never_confers_authority():
    m = api()
    from g6.cognitive_bridge.metacognition import ProblemSignature
    from g6.core.selector import rank_library_methods
    from tests.distill.test_synthesis import MemoryLibrary
    library = MemoryLibrary(['assert_strength.v1', 'skip_gate.v1'])
    before = [e.model_dump(mode='json') for e in library.list()]
    entries = library.list()
    rows = tuple(m.MethodOutcome(entry_id=entry.entry_id, case_id=f'case-{i}', cohort_id='field-1',
        resolved=resolved, cost=1., evaluator_ref='independent.field_reader', oracle_digest=digest('rubric'),
        domain='tests', uncertainty='known', claim_kind='weakening')
        for entry, resolved in zip(entries, (True, False)) for i in range(6))
    feed = m.selector_feed(entries, rows)
    assert all(v.kind == 'library_method' for v in feed.variants)
    context = ProblemSignature(domain='tests', uncertainty='known', claim_kind='weakening', risk=2, budget=10.)
    ranked = rank_library_methods(feed.variants, feed.trials, context, admit_trial=lambda t: t.evaluator_ref ==
                                 'independent.field_reader')
    assert ranked.ordered_variants[0]['variant_id'] == entries[0].entry_id
    assert ranked.admitted_trials == 12
    cold = rank_library_methods(feed.variants, feed.trials, context)
    assert cold.admitted_trials == 0
    assert not any('authority' in row for row in ranked.ordered_variants)
    assert [e.model_dump(mode='json') for e in library.list()] == before


def experiment_fixture():
    from g6.complexity.ir import Hypothesis
    from g6.harness.verification_controller import Experiment, ExperimentState
    state = ExperimentState('tree', 'environment', 'checker',
        (Hypothesis('h', 'candidate quality', 'high'),), budget_units=5)
    return state, (Experiment('redundant', 'probe', ('h',), 1, allowed=True),
                   Experiment('separating', 'probe', ('h',), 2, allowed=True))


def test_information_gain_prefers_a_decision_relevant_separator():
    m = api()
    state, experiments = experiment_fixture()
    probes = (m.ExperimentForecast(experiment=experiments[0], outcomes=('concern', 'concern'),
                                  decisions=('admit', 'refuse')),
              m.ExperimentForecast(experiment=experiments[1], outcomes=('concern', 'no_concern'),
                                  decisions=('admit', 'refuse')))
    chosen = m.choose_experiment(state, probes)
    assert chosen.experiment.id == 'separating'
    assert chosen.information_gain == 1.
    assert chosen.gain_per_cost == .5
    assert chosen.regret == 0.
    assert state.attempted == ()
    assert m.choose_experiment(replace(state, attempted=('separating',)), probes) is None
    irrelevant = tuple(replace(p, decisions=('refuse', 'refuse')) for p in probes)
    assert m.choose_experiment(state, irrelevant) is None
    assert m.choose_experiment(replace(state, budget_units=1), probes) is None


def policy_fixture():
    m = api()
    from tests.metaharness._rig import build, Clock
    from g6.metaharness.schema import Profile
    workflow, _ = build(n_held=30)
    base = Profile(routing={'answer': 'local'}, prompt=m.SynthesisPolicy().model_dump_json())
    from g6.core.ledger import ChainLedger
    confirmation = {'workflow_digest': workflow.digest(), 'judging_sha256': digest('judging'),
                    'held_out_digest': workflow.suite.digest('held_out')}
    frozen = m.FrozenMetaEvaluation.freeze(workflow, confirmation=confirmation,
                                           method_case_ids=('separate-method-case',), journal=ChainLedger())
    return m, workflow, base, frozen, Clock()


def test_policy_proposal_is_judged_by_the_unchanged_metaharness_gate():
    m, workflow, base, frozen, clock = policy_fixture()
    policy = m.SynthesisPolicy(try_predicate=False, repair_budget=0)
    proposal = m.policy_proposal(base, policy, proposer='M')
    assert proposal.strategy == 'synthesis_policy'
    def factory(ctx):
        return lambda contract: '1'  # no claimed improvement: the real evaluator/gate refuses
    report = m.evaluate_policy(workflow, base, proposal, factory, frozen=frozen, reuse_index=1, clock=clock)
    decision = m.judge_policy(report, proposal, workflow, base=base, frozen=frozen,
                               judging_sha256=digest('judging'))
    from g6.metaharness.gate import decide
    direct = decide(report, proposal, workflow, base=base, confirmed=frozen.confirmation,
                    judging_sha256=digest('judging'))
    assert decision == direct
    assert decision.verdict != 'recommend'
    weakened = replace(workflow, policy=replace(workflow.policy, min_cases=1))
    denied = m.judge_policy(report, proposal, weakened, base=base, frozen=frozen,
                             judging_sha256=digest('judging'))
    assert denied.verdict == 'block'
    assert denied.code == 'G6_E_META_STANDARD_CHANGED'
    with pytest.raises(ValueError, match='frozen|changed'):
        m.evaluate_policy(weakened, base, proposal, factory, frozen=frozen, reuse_index=1, clock=clock)


def test_meta_split_is_separate_and_spending_is_not_reset():
    m, workflow, base, frozen, clock = policy_fixture()
    with pytest.raises(ValueError, match='separate|overlap'):
        m.FrozenMetaEvaluation.freeze(workflow, confirmation=frozen.confirmation,
                                      method_case_ids=(workflow.suite.held_out[0].case_id,))
    proposal = m.policy_proposal(base, m.SynthesisPolicy(try_predicate=False), proposer='M')
    with pytest.raises(ValueError, match='spent|limit'):
        m.evaluate_policy(workflow, base, proposal, lambda ctx: lambda contract: '1', frozen=frozen,
                          reuse_index=workflow.policy.max_evaluations + 1, clock=clock)
    with pytest.raises(ValidationError):
        m.SynthesisPolicy(min_cases=1)


def test_synthesis_policy_controls_calls_without_raising_the_cap():
    m = api()
    from g6.core.llm import FakeLLM
    from g6.distill.synthesis import Synthesizer
    from tests.distill.test_synthesis import fixture
    concept, case = fixture('def test_a():\n    assert calc() == 4\n')
    caller = FakeLLM(['{"source":"def solve(facts): return {}"}'] * 3)
    result = Synthesizer(caller=caller, policy=m.SynthesisPolicy(max_synthesis_calls=0)).synthesise(concept, [case])
    assert result.synthesis_calls == 0
    assert len(caller.calls) == 0
    with pytest.raises(ValidationError):
        m.SynthesisPolicy(max_synthesis_calls=4)
