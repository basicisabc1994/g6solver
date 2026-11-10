"""Owned checks and probes retain the executor seam used by recorded test runs."""
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner, behaviour_question as bq, executable_scenario, verification_search as vs
from g6.harness import verification_templates as vt
from g6.workforce import repo_tests
from tests.repair_question2.helpers import TASK, candidate, repo


@pytest.mark.parametrize('at_base', [False, True])
@pytest.mark.parametrize('probe', [False, True])
def test_recorded_executor_controls_every_owned_run(tmp_path, monkeypatch, at_base, probe):
    root = repo(tmp_path)
    change = repo_tests.change(root)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    scenario = executable_scenario.Scenario.resolve(root, question)
    if probe:
        instance = vt.TemplateInstance(id='owned', requirement_id='R1', intent='change', template='differential.v1',
            bindings={'function': 'calc.add', 'mode': 'outputs', 'calls': [{'args': [2, 3]}]})
        tree = vt.Tree.read(root, change)
        definition = vt.TEMPLATES[instance.template]
        applied = definition.applies(tree, definition.bindings.model_validate(instance.bindings), vt.BASE)
        data = {**applied.data, 'function': vt._located(vt.resolve(tree, 'calc.add', vt.BASE))}
        compiled, _, _ = vt.compiled_check(root, instance, data, applied.notes)
        source = compiled.source
    else:
        source = behaviour_runner.CONFIRMED + scenario.source('A')
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    seen = []

    def recorded(copy, ids, **kwargs):
        seen.append((copy, ids, kwargs))
        return {'verdict': 'not_run', 'said': 'recorded executor owns this run',
                'source_before': vs.digest(repo_tests._copy_snapshot(copy))}

    monkeypatch.setattr(repo_tests, '_run', recorded)
    monkeypatch.setattr(behaviour_runner, 'run', lambda *a, **k: pytest.fail('escaped the recorded executor'))
    found = vs._run_check(change, 'test_owned.py', source, 'test_owned.py', at_base=at_base,
                          timeout_s=60, artefact=before)
    assert found['said'] == 'recorded executor owns this run'
    assert len(seen) == 1
    assert seen[0][2]['observation']['function'] == ['module', 'calc', 'add', 'calc.py']
    assert seen[0][2]['require_isolation'] is True and seen[0][2]['network'] is False


def test_real_executor_dispatches_owned_runs_without_pytest_startup(tmp_path, monkeypatch):
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'calls': [{'args': [2, 3]}]}
    seen = []

    def owned(copy, observation, node, **kwargs):
        seen.append((copy, observation, node, kwargs))
        return {'verdict': 'not_run', 'said': 'owned runner'}

    monkeypatch.setattr(behaviour_runner, 'run', owned)
    found = repo_tests._run(tmp_path, ['test_owned.py'], roots=(), timeout_s=60, network=False,
                            copied=True, require_isolation=True, deadline=123, observation=data)
    assert found == {'verdict': 'not_run', 'said': 'owned runner'}
    assert seen == [(tmp_path, data, 'test_owned.py', {'timeout_s': 60, 'deadline': 123})]


@pytest.mark.parametrize('changes', [
    {'copied': False}, {'network': True}, {'require_isolation': False}, {'tests': []},
    {'tests': ['one.py', 'two.py']},
])
def test_owned_dispatch_requires_one_copied_isolated_offline_run(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(behaviour_runner, 'run', lambda *a, **k: pytest.fail('invalid owned run was started'))
    arguments = {'tests': ['test_owned.py'], 'roots': (), 'timeout_s': 60, 'network': False,
                 'copied': True, 'require_isolation': True, 'observation': {'calls': []}, **changes}
    with pytest.raises(repo_tests.RepoRefused) as refused:
        repo_tests._run(tmp_path, **arguments)
    assert refused.value.code == 'G6_E_INPUT_VALIDATION'


@pytest.mark.parametrize('option', ['A', 'B'])
def test_reproduction_and_executor_share_the_canonical_host_comparison(tmp_path, option):
    root = repo(tmp_path)
    question = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    scenario = executable_scenario.Scenario.resolve(root, question)
    source = scenario.source(option)
    namespace, seen = {}, []
    exec(compile(source, '<portable canonical comparison>', 'exec'), namespace)
    namespace['subprocess'] = SimpleNamespace(run=lambda *a, **k: SimpleNamespace(returncode=0))
    def finish(code, raw, data):
        seen.append(data)
        return {'result': 'passed', 'detail': ''}
    namespace['finish'] = finish
    namespace['_check_example']()
    [comparison] = seen
    _, execution = behaviour_runner.bindings(source)
    assert comparison == scenario.comparison(option) == {key: value for key, value in execution.items()
                                                         if key != '_program'}
    assert execution['_program'] == scenario.program
