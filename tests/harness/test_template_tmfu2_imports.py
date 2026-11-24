"""Import availability follows its cause, including inside the observed call."""
import json
import subprocess
import sys

import pytest

from g6.harness import behaviour_runner as runner
from tests.harness.test_template_runtime_binding import _compiled


def _observe(tmp_path, template, source, extra=None, *, expected=None, cases=None):
    _, data = _compiled(template)
    for rel, text in {'runtime_fixture.py': source, **(extra or {})}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    if expected:
        data['cases'] = [{'args': [0], 'kwargs': {}, 'expect': 'raises', 'error': ['builtin', '', expected]}]
    if cases:
        data['cases'] = cases
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'channel.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=20)
    return runner.finish(ran.returncode, channel.read_bytes(), data)


SOURCE = '''class Machine:
    def act(self, value):
        import tmfu2_absent_external
        return value
def make():
    return Machine()
'''


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_lazy_external_import_never_becomes_an_observed_raise(tmp_path, template):
    found = _observe(tmp_path, template, SOURCE)
    assert found['result'] == 'error' and found['detail'].startswith('G6_E_OBSERVATION_UNAVAILABLE:')
    assert 'observations' not in found


@pytest.mark.parametrize('expected', ['ImportError', 'ModuleNotFoundError'])
def test_confirmed_import_error_remains_an_outcome(tmp_path, expected):
    assert _observe(tmp_path, 'example.v1', SOURCE, expected=expected)['result'] == 'passed'


def test_a_different_cases_expected_error_does_not_authorize_this_import_failure(tmp_path):
    cases = [{'args': [0], 'kwargs': {}, 'expect': 'returns', 'value': 0},
             {'args': [1], 'kwargs': {}, 'expect': 'raises', 'error': ['builtin', '', 'ImportError']}]
    found = _observe(tmp_path, 'example.v1', SOURCE, cases=cases)
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


@pytest.mark.parametrize('guard', [
    'try:\n    import tmfu2_absent_external\n    fast = 1\nexcept ImportError:\n    pass\n',
    'try:\n    from .speed import fast\nexcept ImportError:\n    pass\n',
])
def test_guarded_preparation_import_is_remembered(tmp_path, guard):
    source = 'from held import fast\n' + SOURCE.replace('        import tmfu2_absent_external\n', '')
    found = _observe(tmp_path, 'example.v1', source, {
        'held/__init__.py': guard, 'held/speed.py': 'import tmfu2_absent_external\nfast = 1\n'})
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


def test_a_never_defined_held_name_remains_a_call_failure(tmp_path):
    found = _observe(tmp_path, 'example.v1', 'from held import fast\n' + SOURCE,
                     {'held/__init__.py': 'kept = 1\n'})
    assert found == {'result': 'failed', 'detail': 'call failed: builtins.ImportError'}


def test_an_unrelated_guard_does_not_supply_a_never_defined_name(tmp_path):
    guard = 'try:\n    import tmfu2_absent_external\n    other = 1\nexcept ImportError:\n    pass\n'
    found = _observe(tmp_path, 'example.v1', 'from held import fast\n' + SOURCE,
                     {'held/__init__.py': guard})
    assert found == {'result': 'failed', 'detail': 'call failed: builtins.ImportError'}


def test_explicit_import_error_with_metadata_is_still_the_operations_outcome(tmp_path):
    source = SOURCE.replace('import tmfu2_absent_external',
                            'raise ModuleNotFoundError("private", name="tmfu2_absent_external")')
    assert _observe(tmp_path, 'example.v1', source)['result'] == 'failed'


def test_missing_name_from_a_non_own_module_is_unavailable(tmp_path):
    source = SOURCE.replace('import tmfu2_absent_external', 'from json import tmfu2_absent_name')
    found = _observe(tmp_path, 'example.v1', source)
    assert found['result'] == 'error' and 'G6_E_OBSERVATION_UNAVAILABLE:' in found['detail']


def test_non_import_probe_setup_does_not_claim_an_import_failed(tmp_path):
    source = SOURCE.replace('return Machine()', 'raise ValueError("private")')
    found = _observe(tmp_path, 'differential.v1', source)
    assert found['result'] == 'error' and found['detail'] == (
        'G6_E_OBSERVATION_UNAVAILABLE: no single completed observation')
