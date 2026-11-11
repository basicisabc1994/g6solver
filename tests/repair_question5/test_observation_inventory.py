"""Inventory the validator's exception language and canonical value boundaries in real children."""
import builtins
import inspect
import subprocess
import time
from pathlib import Path

import pytest

from g6.core import confine
from g6.harness import behaviour_question as bq, behaviour_runner as runner
from g6.harness.executable_scenario import Scenario
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import TASK, candidate, repo


ERRORS = sorted({kind.__name__ for kind in vars(builtins).values()
                 if isinstance(kind, type) and issubclass(kind, BaseException)})


def observe(tmp_path, program, data, *, scenario=None):
    root = tmp_path / 'copy'
    root.mkdir()
    (root / 'calc.py').write_text(program, encoding='utf-8')
    sizes = []
    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=20)
        log.write_bytes(child.stdout + child.stderr)
        sizes.append(Path(cmd[-2]).stat().st_size)
        return child.returncode
    if scenario:
        data = {**data, '_program': scenario.program}
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(confine, 'interpreter_paths', lambda python: ())
        patched.setattr(confine, 'plan', lambda spec, python: confine.Plan('test-boundary', '', spec))
        patched.setattr(rt, '_bounded', execute)
        ran = runner.run(root, data, 'test_example.py', timeout_s=20, deadline=time.monotonic() + 30)
    return ran.get('g6_observation', {'result': 'error', 'detail': ran.get('said')}), sizes[-1]


@pytest.mark.parametrize('name', ERRORS)
def test_every_accepted_builtin_exception_is_observed_as_itself(tmp_path, name):
    # Exercise actual admission, resolution, the canonical program and the host comparison.
    root = repo(tmp_path)
    one = bq.caller_candidates(TASK, [candidate(outcomes={
        'A': {'raises': name}, 'B': {'returns': -1}})])[0]
    scenario = Scenario.resolve(root, one.question('b' * 64))
    expression = ("UnicodeDecodeError('utf8', b'x', 0, 1, 'bad')" if name == 'UnicodeDecodeError' else
                  "UnicodeEncodeError('utf8', 'x', 0, 1, 'bad')" if name == 'UnicodeEncodeError' else
                  "UnicodeTranslateError('x', 0, 1, 'bad')" if name == 'UnicodeTranslateError' else
                  f"{name}('group', [ValueError('bad')])" if name in ('ExceptionGroup', 'BaseExceptionGroup') else
                  name + '()')
    got, _ = observe(tmp_path, f'def add(a,b):\n    raise {expression}\n', scenario.comparison('A'), scenario=scenario)
    assert got['result'] == 'passed', (name, got)
    assert got['observed']['outcomes'][0]['raised_name'] == 'builtins.' + (
        'ExceptionGroup' if name == 'BaseExceptionGroup' else name)


@pytest.mark.parametrize('size', [0, 1, runner.MAX_FRAME - 4, runner.MAX_FRAME - 3,
                                 runner.MAX_FRAME - 2, runner.MAX_FRAME - 1,
                                 runner.MAX_FRAME, runner.MAX_FRAME + 1, 2_000_000])
@pytest.mark.parametrize('effect', [False, True])
def test_size_never_changes_equality_into_unknown(tmp_path, size, effect):
    value = 'x' * size
    case = {'args': [[]] if effect else [], 'expect': 'effect' if effect else 'returns', 'value': value}
    if effect:
        case.update(argument=0, value=[value])
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [case]}
    program = (f'def add(a):\n    a.append("x" * {size})\n' if effect else
               f'def add():\n    return "x" * {size}\n')
    got, framed = observe(tmp_path, program, data)
    assert got['result'] == 'passed', got
    assert framed < 4096, 'framing must carry a bounded canonical digest, size and prefix'
    case['value'] = 5
    # Compare the very same observation, not another call whose behaviour could differ.
    assert runner.finish(0, runner.frame(got['observed']), data)['result'] == 'failed'


def test_exception_policy_is_shared_by_every_admission_and_execution_layer():
    from g6.harness import confirmed_expectation, executable_scenario
    from g6.harness.observation_contract import observable_exception
    for module in (bq, confirmed_expectation, executable_scenario, runner):
        assert module.observable_exception is observable_exception
    assert inspect.getsource(observable_exception) in runner.BOOTSTRAP
    assert inspect.getsource(runner._fingerprint) in runner.BOOTSTRAP


def test_repository_defined_oserror_subtype_is_observed(tmp_path):
    root = repo(tmp_path)
    program = 'class DomainError(OSError):\n    pass\ndef add(a,b):\n    raise DomainError()\n'
    (root / 'calc.py').write_text(program)
    one = bq.caller_candidates(TASK, [candidate(outcomes={
        'A': {'raises': 'calc.DomainError'}, 'B': {'returns': 5}})])[0]
    scenario = Scenario.resolve(root, one.question('b' * 64))
    got, _ = observe(tmp_path, program, scenario.comparison('A'), scenario=scenario)
    assert got['result'] == 'passed', got


@pytest.mark.parametrize('inside', [False, True])
def test_call_exception_is_an_observation_and_setup_exception_is_unavailable(tmp_path, inside):
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['builtin', '', 'OSError']}]}
    program = 'def add():\n    raise OSError()\n' if inside else 'raise OSError()\n'
    got, _ = observe(tmp_path, program, data)
    assert got['result'] == ('passed' if inside else 'error'), got


def test_a_large_exception_diagnostic_cannot_hide_a_wrong_outcome(tmp_path):
    program = ('class DomainError(ValueError):\n    pass\ndef add():\n'
               '    DomainError.__qualname__ = "X" * 2_000_000\n    raise DomainError()\n')
    data = {'function': ['module', 'calc', 'add', 'calc.py'],
            'cases': [{'args': [], 'expect': 'returns', 'value': 5}]}
    got, size = observe(tmp_path, program, data)
    assert got['result'] == 'failed', got
    assert size < 4096


def test_a_resolved_name_must_be_an_exception_type_before_the_call(tmp_path):
    data = {'function': ['module', 'calc', 'add', 'calc.py'], 'cases': [
        {'args': [], 'expect': 'raises', 'error': ['module', 'calc', 'DomainError', 'calc.py']}]}
    got, _ = observe(tmp_path, "DomainError = 'ValueError'\ndef add():\n    raise ValueError()\n", data)
    assert got['result'] == 'error'


def test_versioned_digest_frame_inventory_and_prefix_is_diagnostic_only():
    encoded = runner._fingerprint('5')
    assert set(encoded) == {'sha256', 'size', 'prefix'}
    frame = {'version': 'observation.v2', 'errors': {},
             'outcomes': [{'returned': encoded, 'arguments': []}]}
    data = {'cases': [{'args': [], 'expect': 'returns', 'value': 5}]}
    assert runner.finish(0, runner.frame(frame), data)['result'] == 'passed'
    encoded['prefix'] = '?'  # equality is the canonical digest and size, not a display string
    assert runner.finish(0, runner.frame(frame), data)['result'] == 'passed'
    for bad in ({'sha256': 'x' * 64}, {'sha256': '0' * 63}, {'size': True}, {'size': -1},
                {'prefix': 'x' * 201}, {'prefix': 'xx'}, {'extra': 'field'}):
        frame['outcomes'][0]['returned'] = {**encoded, **bad}
        assert runner.read_frame(runner.frame(frame)) is None, bad
    frame['outcomes'][0]['returned'] = encoded
    frame['version'] = 'observation.v1'
    assert runner.read_frame(runner.frame(frame)) is None, 'existing v1 fields must keep their meaning'
