"""Correspondence and presentation paths audited during the resumed build."""
import ast
import json
from pathlib import Path

import pytest

from g6.core.storage import state_path
from g6.harness import behaviour_question as bq, patch_review, verification_search as vs
from g6.harness.executable_scenario import Scenario
from g6.mcp import behaviour
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, candidate, repo, result, stated


def test_form_setup_and_call_are_read_from_the_executable_record(tmp_path, monkeypatch):
    root = repo(tmp_path)
    one = bq.caller_candidates(TASK, [candidate()])[0]
    question = one.question('b' * 64)
    scenario = Scenario.resolve(root, question)
    # Independent presentation defaults must not override the frozen executable record.
    monkeypatch.setattr(bq, 'SETUP', 'a different setup')
    monkeypatch.setattr(bq, '_call', lambda question: 'a different call')
    message, _ = bq.form(question, one, scenario=scenario)
    assert 'Setup: ' + json.loads(scenario.record)['setup'] in message
    assert 'Input: calc.add(2, 3)' in message
    assert 'a different setup' not in message and 'a different call' not in message


def test_missing_scenario_cannot_silently_restore_confirmed_support(tmp_path, monkeypatch):
    root = repo(tmp_path)
    assert ask(root)['status'] == behaviour.CONFIRMED
    held = behaviour.enforced(str(root), TASK)
    state_path('scenarios', held.expectations[0].question_sha256 + '.json').unlink()
    stated(monkeypatch)
    monkeypatch.setattr(vs, '_run_check', lambda change, placed, source, node, *, at_base, **kwargs:
                        result(node + '::test_example', 'failed' if at_base else 'passed'))
    reply = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS, confirmed=held)
    expectation = reply['assessment']['confirmed_expectations']
    assert reply['assessment']['outcome'] == patch_review.INSUFFICIENT
    assert any(reason['code'] == patch_review.CONFIRMED_NOT_RUN and
               'G6_E_SCENARIO_UNAVAILABLE' in reason['detail'] for reason in reply['assessment']['reasons'])
    assert not expectation['evidence']['expectations'][0]['checked']
    assert 'G6_E_SCENARIO_UNAVAILABLE' in expectation['expectations'][0]['check']['scenario_unavailable']


@pytest.mark.parametrize('slot', ['setup', 'entrypoint', 'input', 'outcomes'])
def test_scenario_field_inventory_reaches_the_form_producer_and_compiler(tmp_path, slot):
    root = repo(tmp_path)
    one = bq.caller_candidates(TASK, [candidate()])[0]
    scenario = Scenario.resolve(root, one.question('b' * 64))
    value = json.loads(scenario.record)
    assert slot in value
    source = Path(bq.__file__).read_text(encoding='utf-8')
    form = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'form')
    assert 'scenario.presentation' in ast.unparse(form)
    assert 'SCENARIO = ' + ascii(scenario.record.decode('ascii')) in scenario.program


def test_every_presentation_field_routes_through_the_shared_encoder():
    from g6.harness import patch_report

    paths = ((bq, 'form', 'caller_text'), (patch_report, 'code', 'caller_text'),
             (behaviour, '_reply', 'bq.caller_text'))
    for module, name, encoder in paths:
        tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        assert any(isinstance(n, ast.Call) and ast.unparse(n.func) == encoder for n in ast.walk(function))
    for value in ('\n', '\r', '\r\n', '\x85', '\u2028', '\u2029', '\f', '\v'):
        assert json.loads(bq.caller_text(value)) == value
        assert len(bq.caller_text(value).splitlines()) == 1


def test_control_and_replay_receive_the_same_frozen_copy_binding(tmp_path, monkeypatch):
    from g6.harness import confirmed_checks
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    assert ask(root)['status'] == behaviour.CONFIRMED
    held = behaviour.enforced(str(root), TASK)
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    seen = []

    def execute(change, placed, source, node, *, at_base, **kwargs):
        seen.append(kwargs.get('artefact'))
        return result(node + '::test_example', 'failed' if at_base else 'passed')

    monkeypatch.setattr(vs, '_run_check', execute)
    confirmed_checks.run(root, change, held, deadline=vs._clock() + 60, artifact='b' * 64, before=before)
    assert len(seen) == 3
    assert all(binding is before for binding in seen)


@pytest.mark.parametrize('at_base', [False, True])
@pytest.mark.parametrize('substituted', [False, True])
def test_every_copy_is_bound_before_base_projection(tmp_path, monkeypatch, at_base, substituted):
    from contextlib import contextmanager
    from g6.core.ledger import digest
    from g6.harness import behaviour_runner
    from g6.workforce import repo_tests

    root = repo(tmp_path, extra={'helper.py': 'VALUE = 1\n'})
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    source = behaviour_runner.CONFIRMED + "TEMPLATE = 'example.v1'\nBINDINGS = " + repr({
        'function': ['module', 'calc', 'add', 'calc.py'],
        'cases': [{'args': [2, 3], 'kwargs': {}, 'expect': 'returns', 'value': 5}]})
    copy_tree = repo_tests.repository_copy
    executed = []

    @contextmanager
    def copying(*args, **kwargs):
        with copy_tree(*args, **kwargs) as copy:
            if substituted:
                (copy / 'helper.py').write_text('VALUE = 2\n')
            yield copy

    def observe(copy, *args, **kwargs):
        executed.append(True)
        return {'verdict': 'passed', 'source_before': digest(repo_tests._copy_snapshot(copy)),
                'g6_observation': {'result': 'passed', 'detail': 'fixture'}, 'seconds': 0.01}

    monkeypatch.setattr(repo_tests, 'repository_copy', copying)
    monkeypatch.setattr(behaviour_runner, 'run', observe)
    got = vs._run_check(change, 'test_g6.py', source, 'test_g6.py', at_base=at_base,
                        timeout_s=60, artefact=before)
    assert bool(executed) is (not substituted)
    assert (got['verdict'] == 'passed') is (not substituted)


def test_differential_patch_observation_receives_the_review_copy_binding(tmp_path, monkeypatch):
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    one = vs.ProposedCheck(id='probe', requirement_id='R1', intent='change', file='test_probe.py',
                            source='def test_x():\n    pass\n')
    seen = []

    def execute(change, placed, source, node, *, at_base, **kwargs):
        seen.append(kwargs.get('artefact'))
        return result(node, 'passed')

    monkeypatch.setattr(vs, '_run_check', execute)
    vs.search(change, [one], {'probe': 'test_probe.py'}, deadline=vs._clock() + 60, artifact='b' * 64,
              origins={'probe': vs.Origin(template='differential.v1')}, before=before)
    assert seen and all(binding is before for binding in seen)


def test_owned_probe_can_be_saved_and_shown_through_the_product_dialog(tmp_path, monkeypatch):
    import asyncio
    import subprocess
    from g6.core import confine
    from g6.mcp import review_reply
    from g6.workforce import repo_tests
    from tests.repair_question2.helpers import Dialog

    root = repo(tmp_path)
    stated(monkeypatch)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('trusted-fixture', '', spec))

    def execute(command, *, cwd, env, timeout_s, log, **kwargs):
        # Only the synthetic fixture and G6 bootstrap execute; no real OS boundary is claimed.
        ran = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=min(timeout_s, 20))
        log.write_bytes(ran.stdout + ran.stderr)
        return ran.returncode

    monkeypatch.setattr(repo_tests, '_bounded', execute)
    instance = {'id': 'difference', 'template': 'differential.v1', 'requirement_id': 'R1', 'intent': 'change',
                'bindings': {'function': 'calc.add', 'mode': 'outputs', 'calls': [{'args': [2, 3]}]}}
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS,
                                template_instances=[instance])
    assert 'assessment' in reply, reply
    assert reply['assessment']['completion']['activities'], reply
    job = 'a' * 32
    saved = review_reply.save(reply, job)
    assert saved
    custody = json.loads(Path(saved).with_suffix('.observations.json').read_bytes())
    assert len(custody['executions']['difference']) == 3
    dialog = Dialog(action='decline')
    got = asyncio.run(behaviour.confirm(dialog, str(root), TASK, review_job=job, template_instances=[instance]))
    assert got['status'] == behaviour.PENDING, got
    assert len(dialog.messages) == 1
    assert "Evidence (G6's own runs in a review):" in dialog.messages[0]
    assert 'Outcome A: returns -1' in dialog.messages[0] and 'Outcome B: returns 5' in dialog.messages[0]


def test_confirmed_reproduction_command_also_excludes_repository_pytest_hooks(tmp_path, monkeypatch):
    import shlex
    import subprocess
    import sys
    from g6.harness import confirmed_checks
    from g6.workforce import repo_tests

    root = repo(tmp_path, extra={'conftest.py': 'def pytest_pyfunc_call(pyfuncitem):\n    return True\n'})
    (root / 'calc.py').write_text('def add(a,b):\n    return a-b\n')
    assert ask(root)['status'] == behaviour.CONFIRMED
    held = behaviour.enforced(str(root), TASK)
    monkeypatch.setattr(vs, '_run_check', lambda change, placed, source, node, **kwargs:
                        result(node + '::test_example', 'failed'))
    [reading] = confirmed_checks.run(root, repo_tests.change(root), held,
                                     deadline=vs._clock() + 60, artifact='b' * 64)
    reproduction = reading['reproduction']
    path = root / reproduction['file']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reproduction['text'], encoding='utf-8')
    command = shlex.split(reproduction['command'])
    assert command[0] == 'python' and '-I' in command and '-S' in command
    command[0] = sys.executable
    # Match the suite's existing ordinary-directory fixture in this isolated
    # synthetic process. Product owner-only folders are not altered.
    directory_fixture = '''import tempfile, pathlib, os, uuid
def fixture_directory(suffix=None, prefix=None, dir=None):
    path = pathlib.Path(dir or os.environ['TMP']) / ((prefix or 'tmp') + uuid.uuid4().hex + (suffix or ''))
    path.mkdir(parents=True)
    return str(path)
tempfile.mkdtemp = fixture_directory
'''
    command[-1] = directory_fixture + command[-1]
    ran = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
    assert ran.returncode != 0
    assert 'the confirmed outcome did not match' in ran.stderr


def test_ordinary_unconfirmed_executor_keeps_its_sparse_snapshot_contract(tmp_path, monkeypatch):
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    before = repo_tests.snapshot(root)
    executed = []

    def execute(copy, ids, **kwargs):
        executed.append(True)
        return result(ids[0])

    monkeypatch.setattr(repo_tests, '_run', execute)
    got = vs._run_check(repo_tests.change(root), 'test_ordinary.py', 'def test_x():\n    pass\n',
                        'test_ordinary.py::test_x', at_base=False, timeout_s=60, artefact=before)
    assert got['verdict'] == 'passed' and executed == [True]
    assert 'execution_artifact' not in got  # no full-byte assertion is invented for the ordinary run


def test_bad_g6_generated_check_never_falls_back_to_repository_pytest(tmp_path, monkeypatch):
    from g6.core.ledger import digest
    from g6.harness import behaviour_runner
    from g6.workforce import repo_tests

    root = repo(tmp_path)
    change = repo_tests.change(root)
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    executed = []

    def execute(copy, ids, **kwargs):
        executed.append(True)
        return {**result(ids[0]), 'source_before': digest(repo_tests._copy_snapshot(copy))}

    monkeypatch.setattr(repo_tests, '_run', execute)
    source = behaviour_runner.CONFIRMED + 'def test_x():\n    pass\n'
    got = vs._run_check(change, 'test_bad.py', source, 'test_bad.py::test_x', at_base=False,
                        timeout_s=60, artefact=before)
    assert got['verdict'] == 'not_run' and executed == []


def test_a_literal_cannot_supply_the_g6_bootstrap():
    from g6.harness import behaviour_runner

    source = "TEMPLATE = 'differential.v1'\nBINDINGS = " + repr({
        'function': ['module', 'calc', 'add'], 'calls': [{'args': [2, 3]}],
        '_program': "raise RuntimeError('not the G6 bootstrap')"})
    assert behaviour_runner.bindings(source) is None


@pytest.mark.parametrize('kind', ['raises', 'effect'])
def test_canonical_runner_accepts_the_reference_and_rejects_the_other_witness(tmp_path, kind):
    root = repo(tmp_path)
    if kind == 'raises':
        outcomes = {'A': {'raises': 'ValueError'}, 'B': {'returns': 0}}
        accepted = 'class Specific(ValueError):\n    pass\ndef add(value):\n    raise Specific()\n'
        rejected = 'def add(value):\n    return 0\n'
    else:
        outcomes = {'A': {'effect': {'argument': 0, 'equals': [0, 1]}}, 'B': {'raises': 'ValueError'}}
        accepted = 'def add(value):\n    value.append(1)\n'
        rejected = 'def add(value):\n    return None\n'
    (root / 'calc.py').write_text(accepted)
    one = bq.caller_candidates(TASK, [candidate(args=[[0]], outcomes=outcomes)])[0]
    scenario = Scenario.resolve(root, one.question('b' * 64))
    namespace = {}
    exec(compile(scenario.source('A'), '<canonical witness>', 'exec'), namespace)
    # The suite's ordinary-directory fixture only supplies this synthetic child's temp folder.
    import os
    previous = Path.cwd()
    try:
        os.chdir(root)
        namespace['_check_example']()
        (root / 'calc.py').write_text(rejected)
        with pytest.raises(AssertionError, match='the confirmed outcome did not match'):
            namespace['_check_example']()
    finally:
        os.chdir(previous)
