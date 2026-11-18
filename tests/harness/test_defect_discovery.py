# SPDX-License-Identifier: Apache-2.0
"""Development fixtures; synthetic runs do not demonstrate operating-system isolation."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from g6.core import semantic
from g6.core.llm import FakeLLM
from g6.harness import defect_discovery as dd
from g6.harness import verification_search as vs, verification_templates as vt
from g6.harness.patch_review import Requirement
from g6.workforce import repo_tests


@pytest.fixture
def world(monkeypatch):
    tmp_path = Path.cwd() / '.build' / 'discovery-fixture'
    before = 'def scale(x: int):\n    return x * 2\n'
    after = 'def scale(x: int):\n    return abs(x) * 2\n'
    change = repo_tests.Change(tmp_path, 'HEAD', 'base', 'head', ('calc.py',),
                               {'calc.py': before}, {'calc.py': after}, ('calc.py',), ())
    tree = vt.Tree(tmp_path, change, frozenset({'calc.py', 'test_calc.py'}),
                   frozenset({'calc.py', 'test_calc.py'}), _texts={
                       ('calc.py', vt.BASE): before, ('calc.py', vt.PATCH): after,
                       ('test_calc.py', vt.BASE): 'from calc import scale as s\ndef test_positive():\n    assert s(7) == 14\n',
                       ('test_calc.py', vt.PATCH): ''})
    monkeypatch.setattr(vt.Tree, 'read', lambda *a, **k: tree)
    monkeypatch.setattr(dd, 'boundary', lambda root: '')
    monkeypatch.setattr(semantic, 'resolve_caller', lambda: None)
    return change, tree


def generate(world, requirements=(), **kw):
    return dd.discover(world[0], list(requirements), room=kw.pop('room', 20),
                       deadline=kw.pop('deadline', time.monotonic() + 30), llm=kw.pop('llm', None), **kw)


def test_boundary_without_caller_checks_and_literal_calls_are_deterministic(world):
    first, second = generate(world), generate(world)
    assert first.checks and [c.source for c in first.checks] == [c.source for c in second.checks]
    calls = [call for c in first.checks for call in c.source.observation['calls']]
    assert {'args': [-1], 'kwargs': {}} in calls
    assert {'args': [7], 'kwargs': {}} in calls
    assert all(len(c.source.observation['calls']) <= 32 for c in first.checks)
    assert all(o.about['hypothesis'] for o in first.origins.values())


def test_only_preservation_covering_the_function_can_make_a_counterexample(world):
    kept = generate(world, [Requirement(id='P', text='Keep scale behaviour', kind='preserve')])
    changed = generate(world, [Requirement(id='R', text='scale now accepts negative inputs')])
    unrelated = generate(world, [Requirement(id='P', text='Keep rescale behaviour', kind='preserve')])
    assert all(o.refuted_by_difference for o in kept.origins.values())
    assert not any(o.refuted_by_difference for o in changed.origins.values())
    assert not any(o.refuted_by_difference for o in unrelated.origins.values())


@pytest.mark.parametrize('room,deadline,why', [(0, None, '20'), (20, 0, 'time')])
def test_limits_list_skipped_probes(world, room, deadline, why):
    found = generate(world, room=room, **({'deadline': deadline} if deadline is not None else {}))
    assert not found.checks
    assert any(a['result'] == 'skipped' and why in a['reason'] for a in found.activities)


def test_non_python_and_no_isolation_never_probe(world, monkeypatch):
    change, tree = world
    found = generate((replace(change, sources=(), files=('README.md',)), tree))
    assert not found.checks and any('Python' in a['reason'] for a in found.activities)
    monkeypatch.setattr(dd, 'boundary', lambda root: 'no isolation')
    found = generate(world)
    assert not found.checks and any('isolation' in a['reason'] for a in found.activities)


def test_model_code_is_refused_and_nested_data_keys_stay_literal(world):
    code = FakeLLM(lambda *_: json.dumps({'value': [{'target': 'calc.scale', 'source': 'raise RuntimeError()'}]}))
    with semantic.request(llm=code):
        found = generate(world, llm=code)
    assert found.checks
    assert any('invalid model reply' in str(a) for a in found.activities)
    payload = {'scenario': 'raise RuntimeError()', 'source': 'print(1)', 'function': 'os.system'}
    model = FakeLLM(lambda *_: json.dumps({'value': [{'target': 'calc.scale', 'args': [payload], 'kwargs': {}}]}))
    with semantic.request(llm=model):
        found = generate(world, llm=model)
    assert any(call['args'] == [payload] for check in found.checks for call in check.source.observation['calls'])
    assert all(check.source.observation['function'][-1] == 'scale' for check in found.checks)


def test_model_cannot_choose_unoffered_function(world):
    model = FakeLLM(lambda *_: '{"value":[{"target":"os.system","args":["bad"],"kwargs":{}}]}')
    with semantic.request(llm=model):
        found = generate(world, llm=model)
    assert any('invalid model reply' in str(a) for a in found.activities)


def test_discovery_off_has_legacy_config_and_strict_validation():
    assert vs.shown_config(vs.search_config({'discovery': False})) == {'templates': True, 'test_adequacy': False}
    with pytest.raises(ValueError):
        vs.search_config({'discovery': 'false'})


def test_src_layout_uses_the_importable_module(world):
    change, tree = world
    tree.known = frozenset({'src/calc.py'})
    tree.at_base = tree.known
    tree._texts = {('src/calc.py', side): tree._texts[('calc.py', side)] for side in (vt.BASE, vt.PATCH)}
    change = replace(change, sources=('src/calc.py',), files=('src/calc.py',))
    found = generate((change, tree))
    assert found.checks[0].source.observation['function'] == ['module', 'calc', 'scale']


def test_shadowed_src_module_cannot_probe_another_file(world):
    change, tree = world
    tree.known = tree.at_base = frozenset({'src/calc.py', 'calc.py'})
    tree._texts = {('src/calc.py', side): tree._texts[('calc.py', side)] for side in (vt.BASE, vt.PATCH)}
    tree._texts.update({('calc.py', side): 'def scale(x: int): return 100\n' for side in (vt.BASE, vt.PATCH)})
    change = replace(change, sources=('src/calc.py',), files=('src/calc.py',))
    found = generate((change, tree))
    assert found.checks[0].source.observation['function'] == ['module', 'src.calc', 'scale']


def test_optional_boundaries_and_code_strings_remain_data():
    import ast
    node = ast.parse('def f(x: int | None, *, value: str): pass').body[0]
    calls = dd._boundaries(node, [])
    assert {'args': [None], 'kwargs': {'value': ''}} in calls
    assert {'args': [-1], 'kwargs': {'value': ''}} in calls


def test_model_suggestions_cannot_erase_deterministic_inputs(world):
    model = FakeLLM(lambda *_: json.dumps({'value': [
        {'target': 'calc.scale', 'args': [i], 'kwargs': {}} for i in range(32)]}))
    with semantic.request(llm=model):
        found = generate(world, llm=model)
    calls = found.checks[0].source.observation['calls']
    assert len(calls) == 32
    assert {'args': [-1], 'kwargs': {}} in calls


def test_review_discovers_by_default_and_off_never_plans(world, monkeypatch):
    from g6.harness import patch_review as pr, outcome_history
    change, tree = world
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda _: change.repo)
    monkeypatch.setattr(repo_tests, 'snapshot', lambda _: {})
    monkeypatch.setattr(repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(outcome_history, 'retained_checks', lambda *a: ([], {}, []))
    monkeypatch.setattr(vs, 'bind_execution', lambda change, before, **kw: before)
    got = []
    def assess(*a, **kw):
        got.append(kw.get('discovery_enabled', False))
        return {'decided': ['same']}
    monkeypatch.setattr(pr, '_assess', assess)
    baseline = pr.review_patch(change.repo, task='Keep scale', requirements=[])
    disabled = pr.review_patch(change.repo, task='Keep scale', requirements=[], search={'discovery': False})
    assert baseline == disabled
    assert got == [True, False]


@pytest.fixture
def review_world(world, monkeypatch):
    """Real review and classifier, with in-memory trees and synthetic runner evidence."""
    from g6.harness import patch_review as pr, outcome_history
    change, tree = world
    monkeypatch.setattr(repo_tests, 'checked_repo', lambda _: change.repo)
    monkeypatch.setattr(repo_tests, 'snapshot', lambda _: {})
    monkeypatch.setattr(repo_tests, 'change', lambda *a: change)
    monkeypatch.setattr(repo_tests, 'test_related', lambda *a: ())
    monkeypatch.setattr(repo_tests, 'related_texts', lambda *a: ({}, {}))
    monkeypatch.setattr(repo_tests, 'loadable_changes', lambda *a: {})
    monkeypatch.setattr(repo_tests, 'ignored_inputs', lambda *a: repo_tests.Ignored((), {}, True))
    monkeypatch.setattr(repo_tests, 'diff_text', lambda *a: '')
    monkeypatch.setattr(repo_tests, 'derive_tests', lambda *a: ([], 0))
    monkeypatch.setattr(outcome_history, 'retained_checks', lambda *a: ([], {}, []))
    monkeypatch.setattr(outcome_history, 'check_sources', lambda *a: {})
    monkeypatch.setattr(outcome_history, 'read', lambda *a: outcome_history.History())
    monkeypatch.setattr(outcome_history, 'disclose', lambda reply, root: reply)
    monkeypatch.setattr(pr, '_gate', lambda *a: {'receipts': [], 'findings': [], 'verdict': 'allow'})
    monkeypatch.setattr(pr, '_closing', lambda *a, **kw: ({}, [], {}))
    monkeypatch.setattr(vs, 'bind_execution', lambda change, before, **kw: before)
    history, runs = [], []
    monkeypatch.setattr(outcome_history, 'record_check', lambda *a, **kw: history.append((a, kw)))
    def observe(change, placed, source, node, *, at_base, timeout_s, **kw):
        runs.append((at_base, timeout_s))
        namespace = {}
        # Test-owned fixture code only; production never executes code in its own process.
        exec(change.before['calc.py'] if at_base else change.after['calc.py'], namespace)
        observations = [{'input': c, 'output': namespace['scale'](*c['args'], **c['kwargs'])}
                        for c in source.observation['calls']]
        return {'verdict': 'passed', 'said': '', 'source_before': 'b' * 64, 'seconds': 0.01,
                'g6_observation': {'result': 'passed', 'detail': 'synthetic fixture', 'observations': observations}}
    monkeypatch.setattr(vs, '_run_check', observe)
    def review(*, preserve=True, **kw):
        return pr.review_patch(change.repo, task='Keep scale behaviour' if preserve else 'Use absolute scale',
            requirements=[] if preserve else [{'id': 'R', 'text': 'Absolute scale'}],
            intent={'goal': 'Keep scale behaviour', 'non_goals': ['Keep scale behaviour']} if preserve else None, **kw)
    return review, history, runs


def test_public_review_reproduces_negative_regression_and_records_history(review_world):
    review, history, runs = review_world
    reply = review()
    assert reply['ok'], reply
    assessment = reply['assessment']
    assert assessment['outcome'] == 'insufficient_evidence'
    [found] = assessment['completion']['defects_reproduced']['proposed_checks']
    assert found['result'] == 'counterexample'
    assert any('-1' in d['input'] for d in found['reproduction']['differences'])
    assert any('reproduce' in a.lower() and 'discover-' in a for a in assessment['next_actions'])
    assert history[0][0][5] == 'counterexample'
    assert [base for base, _ in runs] == [False, True, True]
    assert all(seconds <= vs.RUN_S for _, seconds in runs)
    assert assessment['requirements'][0]['status'] == 'not_established'


def test_intentional_difference_is_a_question_with_both_outputs(review_world):
    review, history, runs = review_world
    assessment = review(preserve=False)['assessment']
    assert not assessment['completion']['defects_reproduced']['proposed_checks']
    [question] = assessment['completion']['open_questions']
    assert question['result'] == 'behaviour_changed'
    assert '-2' in question['question'] and '2' in question['question']
    assert history[0][0][5] == 'behaviour_changed'


def test_off_preserves_every_decided_field_and_never_runs_discovery(review_world, monkeypatch):
    review, history, runs = review_world
    from tests.harness.test_patch_review_off_path import decided
    disabled = review(search={'discovery': False})
    assert not history and not runs
    # Simulate the base implementation's path (no discovery stage); compare serialized decided fields.
    monkeypatch.setattr(dd, 'discover', lambda *a, **kw: dd.Discovery())
    baseline = review()
    assert json.dumps(decided(disabled), sort_keys=True) == json.dumps(decided(baseline), sort_keys=True)


def test_report_labels_generated_counterexample_and_gives_one_line(review_world):
    review, _, _ = review_world
    assessment = review()['assessment']
    [finding] = assessment['completion']['defects_reproduced']['proposed_checks']
    assert type(finding['reproduction']['text']) is str
    assert 'calc.scale' in finding['reproduction']['one_line']
    assert "caller's bindings" not in finding['reproduction']['caution']
    assert 'G6 generated' in assessment['report']
    assert 'code someone other than G6 wrote' not in assessment['report']


def test_keyword_model_code_is_not_a_callable_input_and_does_not_drop_boundaries(world):
    model = FakeLLM(lambda *_: json.dumps({'value': [{'target': 'calc.scale', 'args': [],
                                                    'kwargs': {'source': 'print(1)'}}]}))
    with semantic.request(llm=model):
        found = generate(world, llm=model)
    assert found.checks, 'one invalid suggestion must not erase valid deterministic inputs'
    assert any('refused input' in a['reason'] for a in found.activities)


def test_probe_names_all_targeted_premortem_hypotheses(world):
    found = generate(world, [Requirement(id='P', text='Keep scale behaviour', kind='preserve')])
    assert {h['kind'] for o in found.origins.values() for h in o.about['hypotheses']} == {
        'boundary', 'kept behaviour', 'misread intent'}


def test_unsupported_methods_are_explicitly_skipped(world):
    change, tree = world
    tree._texts[('calc.py', vt.BASE)] += '\nclass C:\n    def run(self): return 1\n'
    tree._texts[('calc.py', vt.PATCH)] += '\nclass C:\n    def run(self): return 2\n'
    found = generate(world)
    assert any('C.run' in a['target'] and a['result'] == 'skipped' for a in found.activities)


def test_shared_limit_skips_discovery_with_existing_checks(review_world):
    review, _, runs = review_world
    checks = [{'id': f'existing-{i}', 'requirement_id': 'R', 'intent': 'change',
               'file': f'test_existing_{i}.py', 'source': 'def test_existing(): assert True\n'} for i in range(20)]
    # Tier-1 requirements are intentionally unchecked; existing checks still own all the slots.
    reply = review(preserve=False, proposed_checks=checks)
    activities = reply['assessment']['completion']['activities']
    assert not any(a['activity'] == 'automatic discovery probe' for a in activities)
    assert any(a['activity'] == 'automatic discovery' and '20' in a['reason'] for a in activities)


def test_retained_checks_share_the_limit_with_caller_checks(review_world, monkeypatch):
    review, _, _ = review_world
    from g6.harness import outcome_history
    existing = vs.ProposedCheck(id='retained-fixture', requirement_id='R', intent='preserve',
                               file='test_retained.py', source='def test_retained(): assert True\n')
    monkeypatch.setattr(outcome_history, 'retained_checks', lambda *a: (
        [existing], {existing.id: vs.Origin(activity='retained check')}, []))
    checks = [{'id': f'caller-{i}', 'requirement_id': 'R', 'intent': 'change',
               'file': 'test_caller.py', 'source': 'def test_caller(): assert True\n'} for i in range(20)]
    activities = review(preserve=False, proposed_checks=checks)['assessment']['completion']['activities']
    assert len([a for a in activities if a.get('runs')]) <= 20
    assert any(a.get('check') == existing.id and a['result'] == 'skipped' and '20' in a['reason'] for a in activities)


def test_nonfinite_model_value_cannot_erase_boundaries(world):
    model = FakeLLM(lambda *_: '{"value":[{"target":"calc.scale","args":[NaN],"kwargs":{}}]}')
    with semantic.request(llm=model):
        found = generate(world, llm=model)
    assert found.checks
    assert any('refused input' in a['reason'] or 'invalid model reply' in a['reason'] for a in found.activities)


def test_model_activity_timeout_uses_remaining_budget_and_restores_request(world, monkeypatch):
    recorded = []
    real = semantic.read
    def reader(q):
        recorded.append(semantic._REQUEST.get().timeout)
        return real(q)
    monkeypatch.setattr(semantic, 'read', reader)
    with semantic.request(llm=None, timeout=15) as scope:
        found = generate(world, deadline=time.monotonic() + 12)
        assert scope.timeout == 15
    assert found.checks and 0 < recorded[0] <= 12


def test_exhausted_shared_model_budget_keeps_deterministic_inputs(world):
    model = FakeLLM(lambda *_: pytest.fail('model budget was already spent'))
    with semantic.request(llm=model):
        found = generate(world, llm=model, model_budget=0)
    assert found.checks
    assert any('model' in a['reason'] and 'budget' in a['reason'] for a in found.activities)


def test_discovery_id_cannot_collide_with_caller(world):
    [check] = generate(world).checks
    found = generate(world, reserved_ids={check.id})
    assert not found.checks
    assert any('collision' in a['reason'] for a in found.activities)


def test_qualified_preservation_for_another_module_is_only_a_question(world):
    found = generate(world, [Requirement(id='P', text='Keep other.scale behaviour', kind='preserve')])
    assert not any(o.refuted_by_difference for o in found.origins.values())


def test_confirmed_and_candidate_preservation_cover_the_function(world):
    from types import SimpleNamespace
    confirmed = generate(world, confirmed=[SimpleNamespace(intent='preserve', function='calc.scale', requirement_id='C')])
    candidate = generate(world, [Requirement(id='candidate', text='Keep calc.scale', kind='preserve',
                                             provenance='model_hypothesis')])
    assert all(o.refuted_by_difference for o in [*confirmed.origins.values(), *candidate.origins.values()])
    assert confirmed.checks[0].requirement_id == 'C'


def test_discovery_off_decisions_match_actual_head_implementation(review_world, monkeypatch):
    """Read-only git show of the base review; no git fixture mutation or source rewrite."""
    import sys
    import types
    from g6.harness import patch_review as pr
    from tests.harness.test_patch_review_off_path import decided
    module = types.ModuleType('g6.harness._discovery_baseline')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    source = repo_tests._git(Path.cwd(), 'show', 'HEAD:src/g6/harness/patch_review.py').decode('utf-8')
    exec(compile(source, '<HEAD patch_review>', 'exec'), module.__dict__)
    module._gate, module._closing = pr._gate, pr._closing
    review, _, _ = review_world
    current = review(search={'discovery': False})
    baseline = module.review_patch(Path.cwd() / '.build/discovery-fixture', task='Keep scale behaviour', requirements=[],
        intent={'goal': 'Keep scale behaviour', 'non_goals': ['Keep scale behaviour']})
    assert baseline['ok'] and current['ok']
    assert json.dumps(decided(current), sort_keys=True) == json.dumps(decided(baseline), sort_keys=True)


def test_a_refusal_inside_discovery_is_a_skipped_activity_not_an_aborted_review(review_world, monkeypatch):
    review, history, runs = review_world
    off = review(search={'discovery': False})['assessment']

    def refuse(*a, **k):
        raise repo_tests.RepoRefused('G6_E_PATCH_REVIEW', 'the repository copy cap of 1 files was exceeded')
    monkeypatch.setattr(vt.Tree, 'read', refuse)
    reply = review()
    assert 'assessment' in reply, reply
    assessment = reply['assessment']
    skipped = [a for a in assessment['completion']['activities']
               if a.get('activity') == 'automatic discovery' and a.get('result') == 'skipped']
    assert skipped and 'copy cap' in skipped[0]['reason'], assessment['completion']['activities']
    assert assessment['outcome'] == off['outcome']


@pytest.mark.parametrize('error', [
    repo_tests.CopyNotRun('the repository copy cap of 1 files was exceeded'),
    OSError('G6_E_EXECUTION_ARTIFACT_UNAVAILABLE: links need a materialised-byte adapter')])
def test_a_refusal_binding_the_snapshot_is_discoverys_skip_not_the_reviews(review_world, monkeypatch, error):
    review, history, runs = review_world
    off = review(search={'discovery': False})['assessment']

    def refuse(*a, **k):
        raise error
    monkeypatch.setattr(vs, 'bind_execution', refuse)
    reply = review()
    assert 'assessment' in reply, reply
    assessment = reply['assessment']
    skipped = [a for a in assessment['completion']['activities']
               if a.get('activity') == 'automatic discovery' and a.get('result') == 'skipped']
    assert skipped and str(error) in skipped[0]['reason'], assessment['completion']['activities']
    assert assessment['outcome'] == off['outcome']
