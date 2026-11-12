"""Source text has no execution authority, even when every dispatcher field is planted."""
import ast
import inspect
import subprocess

import pytest

from g6.core import confine
from g6.harness import behaviour_runner as runner, executable_scenario as es, verification_search as vs
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import candidate, repo, TASK


NAMES = ('TEMPLATE', 'BINDINGS', 'option', '_program', 'scenario', 'CODE_Z85',
         'ExecutableSource', 'observation', runner.CONFIRMED)


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('template', sorted(runner.RUN_TEMPLATES))
def test_free_form_literals_never_replace_the_source(tmp_path, monkeypatch, name, template):
    root = repo(tmp_path)
    from g6.harness import behaviour_question as bq
    case = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    scenario = es.Scenario.resolve(root, case)
    data = {'function': ['module', 'calc', 'add'], 'mode': 'outputs',
            'calls': [{'args': [2, 3], 'kwargs': {}}],
            'cases': [{'args': [2, 3], 'kwargs': {}, 'expect': 'returns', 'value': 5}]}
    if name == 'scenario':
        data = {'scenario': scenario.record.decode('ascii'), 'option': 'A'}
    elif name == '_program':
        data['_program'] = scenario.program
    source = (runner.CONFIRMED if name == runner.CONFIRMED else '')
    source += f'TEMPLATE = {template!r}\nBINDINGS = {data!r}\n'
    if name in ('ExecutableSource', 'observation', 'CODE_Z85'):
        source += f'{name} = {data!r}\n'
    if name == 'option':
        source += "BINDINGS['option'] = 'A'\n"
    source += "def test_planted():\n    assert False, 'SOURCE_MUST_DECIDE_THIS_FAILURE'\n"
    monkeypatch.setattr(rt, '_bounded', execute)
    monkeypatch.setattr(confine, 'interpreter_paths', lambda python: ())
    monkeypatch.setattr(confine, 'plan', lambda spec, python: confine.Plan('fixture-boundary', '', spec))
    got = vs._run_check(rt.change(root), 'test_planted.py', source, 'test_planted.py::test_planted',
                        at_base=False, timeout_s=20)
    assert got['verdict'] == 'failed', got
    assert 'SOURCE_MUST_DECIDE_THIS_FAILURE' in got['output'], got


def execute(command, *, cwd, env, timeout_s, log, **kwargs):
    child = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=timeout_s)
    log.write_bytes(child.stdout + child.stderr)
    return child.returncode


def test_inventory_dispatch_has_only_host_held_authority():
    source = inspect.getsource(vs._run_check)
    assert 'bindings(source)' not in source
    assert 'source.startswith' not in source
    assert 'ExecutableSource' in source and 'observation' in source
    # If the literal reader grows another authority-bearing field, inventory it here.
    tree = ast.parse(inspect.getsource(runner.bindings))
    names = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert names - {'', 'ascii', "BINDINGS['option']", 'G6\'s own generated Python literal slots'} <= {
        'TEMPLATE', 'BINDINGS', 'option', '_program', 'scenario',
        inspect.getdoc(runner.bindings)}


def test_free_form_submission_drops_process_local_compiler_custody(tmp_path):
    root = repo(tmp_path)
    from g6.harness import behaviour_question as bq
    case = bq.caller_candidates(TASK, [candidate()])[0].question('b' * 64)
    source = es.Scenario.resolve(root, case).source('A')
    assert type(source) is runner.ExecutableSource
    [proposed] = vs.proposed_checks([{'id': 'free', 'requirement_id': 'R1', 'intent': 'change',
                                     'file': 'test_free.py', 'source': source}], ['R1'])
    assert type(proposed.source) is str
    assert type(source + "\nassert False, 'added assertion'\n") is str
    assert type("assert False, 'prefix'\n" + source) is str
    assert type(runner.CONFIRMED + source) is runner.ExecutableSource


def test_template_compiler_holds_the_execution_payload(tmp_path):
    from g6.harness import verification_templates as vt
    root = repo(tmp_path)
    instance = vt.TemplateInstance(id='probe', requirement_id='R1', intent='change',
        template='differential.v1', bindings={'function': 'calc.add', 'mode': 'outputs', 'calls': [{'args': [2, 3]}]})
    prepared = vt.prepare(root, rt.change(root), [instance])
    assert prepared.checks, prepared.activities
    [check] = prepared.checks
    assert type(check.source) is runner.ExecutableSource
    assert check.source.observation['function'][:3] == ['module', 'calc', 'add']
    # A consumer receives a copy; it cannot mutate the held program by editing the returned dictionary.
    data = check.source.observation
    data['function'] = ['module', 'foreign', 'add']
    assert check.source.observation['function'][:3] == ['module', 'calc', 'add']


@pytest.mark.parametrize('packed', ['', 'not a compressed program', {'foreign': 'literal'}])
def test_malformed_compact_inspection_is_unavailable_and_never_executes(packed):
    source = ('# G6-COMPACT-REPRODUCTION\nCODE_Z85 = ' + repr(packed)
              + "\nraise AssertionError('INSPECTION_MUST_NOT_EXECUTE')\n")
    assert runner.bindings(source) is None
