"""Amendment boundaries: preparation identity and the later outcome reading."""
import asyncio
import concurrent.futures
import inspect
import json
import subprocess
import sys

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.core.ledger import canonical
from g6.harness import behaviour_runner as runner, verification_formal as formal
from g6.harness import verification_search as search
from g6.harness.executable_scenario import SETUP, Scenario
from tests.harness.test_template_runtime_binding import SOURCE, TEMPLATES, _compiled, _fixture, _invoke
from tests.harness.test_template_followup_formal import replay
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('template', TEMPLATES)
@pytest.mark.parametrize(('statement', 'signal'), [
    ('raise SystemExit(2)', SystemExit(2)), ('raise GeneratorExit()', GeneratorExit()),
    ('raise asyncio.CancelledError()', asyncio.CancelledError()),
    ('raise concurrent.futures.CancelledError()', concurrent.futures.CancelledError()),
])
def test_constructor_signal_identity_and_later_setup_reading(tmp_path, monkeypatch, template, statement, signal):
    namespace, _ = _compiled(template)

    def stop():
        raise signal

    with pytest.raises(type(signal)) as caught:
        namespace['setup_call']('constructor', stop)
    assert caught.value is signal
    source = 'import asyncio, concurrent.futures\n' + SOURCE.replace('# REPLACEMENT', statement)
    _fixture(tmp_path, monkeypatch, source)
    with pytest.raises(TypeError) as caught:
        _invoke(template, namespace)
    assert str(caught.value) == 'G6_E_CHECK_SETUP: constructor'


@pytest.mark.parametrize('template', [one for one in TEMPLATES if one != 'example.v1'])
@pytest.mark.parametrize('signal', [KeyboardInterrupt(), ExecutionInterrupted('stop', stopped=True),
                                   ContainmentFailure('cleanup')])
def test_outcome_reader_still_propagates_g6_controls(tmp_path, monkeypatch, template, signal):
    namespace, _ = _compiled(template)
    module = _fixture(tmp_path, monkeypatch, SOURCE)

    def stop():
        raise signal

    module.make = stop
    with pytest.raises(type(signal)) as caught:
        _invoke(template, namespace)
    assert caught.value is signal


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1', 'state_invariant.v1'])
@pytest.mark.parametrize('kind', ['AttributeError', 'ImportError', 'ModuleNotFoundError'])
def test_unavailable_import_names_type_without_message(tmp_path, monkeypatch, template, kind):
    namespace, _ = _compiled(template)
    (tmp_path / 'runtime_fixture.py').write_text(
        'raise ' + kind + '("repository private instruction")\n' + SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
    with pytest.raises(BaseException) as caught:
        _invoke(template, namespace)
    assert str(caught.value).startswith('G6_E_OBSERVATION_UNAVAILABLE:')
    assert 'builtins.' + kind in str(caught.value)
    assert 'repository private instruction' not in str(caught.value)
    assert type(caught.value).__name__ != 'CheckSetupError'


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
def test_bootstrap_records_constructor_signal_as_setup(tmp_path, template):
    _, data = _compiled(template)
    (tmp_path / 'runtime_fixture.py').write_text(
        SOURCE.replace('# REPLACEMENT', 'raise SystemExit(2)'), encoding='utf-8')
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=30)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found['result'] == 'error'
    assert found['detail'] == 'G6_E_CHECK_SETUP: constructor'
    assert 'observations' not in found


def test_setup_registration_works_with_only_original_setup_class(monkeypatch):
    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    scope = {'sys': sys, '__name__': 'generated_check'}
    exec(inspect.getsource(runner.CheckSetupError) + '\n' + inspect.getsource(runner.register_setup_error), scope)
    scope['register_setup_error']()
    assert sys._g6_check_setup_types == [(scope['CheckSetupError'], 'G6_E_CHECK_SETUP')]


def test_confirmed_scenario_keeps_readable_template_and_bindings():
    scenario = Scenario(canonical({
        'version': 'example-scenario.v1', 'setup': SETUP,
        'entrypoint': ['module', 'fixture', 'run', 'fixture.py'],
        'input': {'args': [0], 'kwargs': {}}, 'outcomes': {'A': {'expect': 'returns', 'value': 0}},
    }).encode('ascii'))
    source = runner.CONFIRMED + scenario.source('A')
    assert "TEMPLATE = 'example.v1'" in source
    assert 'BINDINGS = ' in source
    assert len(source.encode('utf-8')) <= 65536


def test_failed_base_read_uses_not_established_wording():
    found = formal._judge(1, 'preserve', replay('broken'),
                          replay('operation_failed', 'reading balance raised NotImplementedError'))
    assert found['result'] == 'check_error'
    assert 'not established at the base commit' in found['detail']
    assert 'steps_refused_at_base' not in found['correspondence']


@pytest.mark.parametrize('name', ['builtins.ModuleNotFoundError', 'builtins.AttributeError'])
def test_owned_unavailable_reading_retains_bounded_type(name):
    node = 'test_g6_access_rule.py::test_access_rule'
    detail = runner.ObservationUnavailable.DETAIL + ': ' + name
    recorded = result(failed=[node], messages={node: 'ObservationUnavailable: ' + detail})
    recorded['check_errors'] = {node: 'G6_E_OBSERVATION_UNAVAILABLE'}
    assert search.reading(recorded, node)['detail'] == detail
