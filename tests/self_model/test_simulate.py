# SPDX-License-Identifier: Apache-2.0
"""Breaks caught: invented forecasts, unsafe routing, and scoring before action."""
import importlib
from pathlib import Path

import pytest

from g6.self_model.model import Field, NamedField, SelfModel, UNKNOWN


def measured(value, source='fixture.measure', at=100, ttl=100):
    return Field.measured(value, source=source, observed_at=at, ttl_s=ttl)


@pytest.fixture
def api():
    assert (Path(__file__).parents[2] / 'src/g6/self_model/simulate.py').is_file(), 'simulation API is missing'
    return importlib.import_module('g6.self_model.simulate')


def setup(api, *, paths=('docs/x.md',), count=30, components=('a',), plan=False):
    risk = importlib.import_module('g6.self_model.risk_gate')
    action = api.Action(action_id='next', kind='experiment', components=components, touched_paths=paths,
                        measures=(NamedField(name='yield', field=measured(10.)),),
                        operations=('component.read',) if plan else (),
                        cost=api.Cost(model_calls=measured(0), wall_time_s=measured(2.), allowance_share=measured(.001)))
    gate = risk.GateReadout.from_evaluation(action.sha256, {
        'action_capsule': {'action_id': 'next', 'reversibility': 'reversible', 'external_side_effect': False,
                           'real_world_effect': False, 'irreversible_or_costly_to_reverse': False},
        'gate_decision': {'decision': 'allow'}}, now=100, ttl_s=10)
    action = action.model_copy(update={'gate': gate})
    if plan:
        readout = risk.PlanReadout.from_report(action.sha256,
            {'valid': True, 'effect': 'read', 'issues': []}, now=100, ttl_s=10)
        action = action.model_copy(update={'plan': readout})
    history = tuple(api.ReferenceAction(action_id=f'old-{i}', kind='experiment',
                        deltas=(NamedField(name='yield', field=measured(2. if i % 2 else 4.)),),
                        regressed=measured(False)) for i in range(count))
    manifest = risk.ProtectedManifest(patterns=measured(('src/g6/core/', 'certificate/', 'held_out/', 'gate')),
                                       complete=measured(True))
    graph = api.Relationships.measured(imports=(('b', 'a'), ('c', 'b')), corpus=(('c', 'd'),),
                                      source='fixture.import_and_corpus_reader', observed_at=100, ttl_s=10)
    return action, history, manifest, graph


def simulate(api, *, now=100, **kwargs):
    action, history, manifest, graph = setup(api, **kwargs)
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=now)
    return action, result


def outcome(api, result, value=14., *, started=101, observed=102, finished=102, action_hash=None, receipt_at=100):
    return api.Outcome(action_sha256=action_hash or result.action_sha256, action_started_at=float(started),
                       action_finished_at=float(finished),
                       prediction_record=measured(result.simulation_id, source='runner.cycle_log.append', at=receipt_at),
                       measurements=() if value is None else (NamedField(name='yield', field=measured(value, at=observed)),))


def test_simulation_records_predictions_and_transitive_affected_components(api):
    action, result = simulate(api)
    assert result.affected == ('a', 'b', 'c', 'd')
    assert result.action_sha256 == action.sha256
    assert result.recorded_at == 100
    assert result.cost.wall_time_s.value == 2.
    assert result.reversibility == 'reversible'
    assert result.risk.decision == 'proceed'
    effect = result.effects[0]
    assert (effect.lower, effect.upper) == (12., 14.)
    assert effect.prediction.before == 10.
    assert effect.prediction.expected == 13.
    from g6.metaharness.lessons import Prediction
    assert type(effect.prediction) is Prediction
    assert effect.reference_count == 30
    assert all(question for question in result.could_break)
    assert any('protected' in question for question in result.could_break)
    assert api.simulate(action, SelfModel(), reference_actions=setup(api)[1], relationships=setup(api)[3],
                        protected_manifest=setup(api)[2], now=100) == result


def test_fewer_than_five_unique_reference_actions_gives_unknown(api):
    action, history, manifest, graph = setup(api, count=4)
    result = api.simulate(action, SelfModel(), reference_actions=history + history, relationships=graph,
                          protected_manifest=manifest, now=100)
    assert result.effects[0].prediction is None
    assert result.effects[0].lower == result.effects[0].upper == UNKNOWN
    assert result.risk.decision == 'more_evidence'
    assert result.effects[0].reference_count == 4


def test_scoring_is_after_recording_and_uses_measured_outcomes(api):
    _, result = simulate(api)
    scored = api.score_after(result, outcome(api, result), now=103)
    assert scored.scores[0].status == 'held'
    assert scored.failures == ()
    assert scored.calibration[0].kind == 'experiment'
    assert scored.calibration[0].agreed == 1
    failed = api.score_after(result, outcome(api, result, 11.), now=103)
    assert failed.scores[0].status == 'failed'
    assert len(failed.failures) == 1
    assert failed.failures[0].kind == 'prediction_failed'
    assert api.score_after(result, outcome(api, result, None), now=103).scores[0].status == 'open'


@pytest.mark.parametrize('started,observed,now', [(100, 102, 103), (99, 102, 103), (101, 100, 103),
                                               (104, 105, 103)])
def test_early_and_future_outcomes_do_not_score(api, started, observed, now):
    _, result = simulate(api)
    with pytest.raises(ValueError, match='after|time'):
        api.score_after(result, outcome(api, result, started=started, observed=observed), now=now)


@pytest.mark.parametrize('change', ['action', 'receipt', 'late_receipt', 'unfinished'])
def test_unbound_or_unrecorded_action_cannot_be_scored(api, change):
    _, result = simulate(api)
    data = outcome(api, result)
    if change == 'action':
        data = data.model_copy(update={'action_sha256': '0' * 64})
    elif change == 'receipt':
        data = data.model_copy(update={'prediction_record': measured('unrelated', source='runner.cycle_log.append')})
    elif change == 'late_receipt':
        data = outcome(api, result, receipt_at=102)
    elif change == 'unfinished':
        data = data.model_copy(update={'action_finished_at': 104.})
    with pytest.raises(ValueError):
        api.score_after(result, data, now=103)


def test_self_prediction_is_rejected_by_scoring_and_reference_history(api):
    action, history, manifest, graph = setup(api)
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=100)
    invented = Field.predicted(14., source='g6.self_model.simulate', observed_at=102, ttl_s=10)
    with pytest.raises(ValueError, match='measured'):
        data = outcome(api, result).model_copy(update={'measurements': (NamedField(name='yield', field=invented),)})
        api.score_after(result, data, now=103)
    bad_history = history[0].model_copy(update={'regressed': invented})
    with pytest.raises(ValueError, match='measured'):
        api.simulate(action, SelfModel(), reference_actions=(bad_history,), relationships=graph,
                     protected_manifest=manifest, now=100)


@pytest.mark.parametrize('paths', [('src/g6/self_model/risk_gate.py',), ('src/g6/core/stop.py',),
                                 ('src/g6/Foo/../core/stop.py',), ('C:\\outside\\a.py',),
                                 ('.github/workflows/ci.yml',), ('src/g6/CERTIFICATE/x.json',),
                                 ('.',), ('src/g6',), ('src/g6/core',)])
def test_protected_or_ambiguous_paths_route_to_owner(api, paths):
    _, result = simulate(api, paths=paths)
    assert result.risk.decision == 'owner'
    assert result.protected_touched


@pytest.mark.parametrize('change', ['external', 'irreversible', 'unknown_reversibility', 'review', 'block',
                                  'mismatched_gate', 'predicted_gate', 'no_manifest', 'stale_manifest', 'no_gate',
                                  'regression', 'many_components', 'missing_plan'])
def test_gate_routes_fail_closed(api, change):
    action, history, manifest, graph = setup(api, plan=change == 'missing_plan')
    want = 'owner'
    if change == 'external':
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'external_effect': measured(True,
                                                source='g6.action_gating.evaluate_action')})})
    elif change in {'irreversible', 'unknown_reversibility'}:
        value = 'irreversible' if change == 'irreversible' else UNKNOWN
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'reversibility': measured(value,
                                                source='g6.action_gating.evaluate_action')})})
    elif change in {'review', 'block'}:
        value = 'block' if change == 'block' else 'require_human_review'
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'decision': measured(value,
                                                source='g6.action_gating.evaluate_action')})})
        want = 'block' if change == 'block' else 'owner'
    elif change == 'mismatched_gate':
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'action_sha256': '0' * 64})})
        want = 'more_evidence'
    elif change == 'predicted_gate':
        invented = Field.predicted('allow', source='g6.self_model.simulate', observed_at=100, ttl_s=10)
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'decision': invented})})
        want = 'more_evidence'
    elif change == 'no_manifest':
        manifest = None
    elif change == 'stale_manifest':
        manifest = manifest.model_copy(update={'patterns': measured(('gate',), at=80, ttl=1)})
    elif change == 'no_gate':
        action = action.model_copy(update={'gate': None})
        want = 'more_evidence'
    elif change == 'regression':
        history = tuple(item.model_copy(update={'regressed': measured(True)}) for item in history)
    elif change == 'many_components':
        action = action.model_copy(update={'components': tuple('abcdefgh')})
        action = action.model_copy(update={'gate': action.gate.model_copy(update={'action_sha256': action.sha256})})
        want = 'more_evidence'
    elif change == 'missing_plan':
        action = action.model_copy(update={'plan': None})
        want = 'more_evidence'
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=100)
    assert result.risk.decision == want


def test_plan_report_is_used_and_unknown_class_does_not_borrow_history(api):
    action, history, manifest, graph = setup(api, plan=True)
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=100)
    assert result.risk.decision == 'proceed'
    action = action.model_copy(update={'kind': 'merge'})
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=100)
    assert result.effects[0].reference_count == 0
    assert result.effects[0].prediction is None


def test_stale_or_unbound_relationship_graph_cannot_understate_affected_set(api):
    action, history, manifest, graph = setup(api)
    with pytest.raises(ValueError, match='fresh'):
        api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                     protected_manifest=manifest, now=111)
    changed = graph.model_copy(update={'imports': ()})
    with pytest.raises(ValueError, match='graph'):
        api.simulate(action, SelfModel(), reference_actions=history, relationships=changed,
                     protected_manifest=manifest, now=100)


def test_relationship_cycles_terminate_and_are_counted_once(api):
    action, history, manifest, _ = setup(api)
    graph = api.Relationships.measured(imports=(('a', 'b'), ('b', 'a'), ('c', 'b')), corpus=(('c', 'a'),),
                                      source='fixture.reader', observed_at=100, ttl_s=10)
    result = api.simulate(action, SelfModel(), reference_actions=history, relationships=graph,
                          protected_manifest=manifest, now=100)
    assert result.affected == ('a', 'b', 'c')


def test_predicted_recording_receipt_cannot_score(api):
    _, result = simulate(api)
    data = outcome(api, result).model_copy(update={'prediction_record': Field.predicted(result.simulation_id,
                                source='runner.cycle_log.append', observed_at=100, ttl_s=10)})
    with pytest.raises(ValueError, match='measured'):
        api.score_after(result, data, now=103)


def test_recorded_field_forgery_is_rejected_on_read():
    field = measured(True).model_copy(update={'value': False})
    with pytest.raises(ValueError, match='digest'):
        field.read(now=100, evidence=True)
