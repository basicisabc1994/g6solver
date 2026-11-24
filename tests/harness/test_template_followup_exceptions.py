"""Exception diagnostics and constructor phase boundaries."""
import asyncio
import concurrent.futures
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as search
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_template_runtime_binding import TEMPLATES, SOURCE, _compiled, _fixture, _invoke
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('template', ['host', *TEMPLATES])
@pytest.mark.parametrize(('statement', 'error'), [
    ('raise SystemExit(2)', SystemExit(2)),
    ('raise GeneratorExit()', GeneratorExit()),
    ('raise asyncio.CancelledError()', asyncio.CancelledError()),
    ('raise concurrent.futures.CancelledError()', concurrent.futures.CancelledError()),
])
def test_constructor_non_control_base_exception_is_setup(tmp_path, monkeypatch, template, statement, error):
    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    scope = vars(runner) if template == 'host' else _compiled(template)[0]

    def construct():
        raise error

    with pytest.raises(type(error)) as caught:
        scope['setup_call']('constructor', construct)
    assert caught.value is error

    source = 'import asyncio, concurrent.futures\n' + SOURCE.replace('# REPLACEMENT', statement)
    if template == 'host':
        # Read the real isolated runner's outcome, after setup_call has let the signal through.
        _, data = _compiled('differential.v1')
        (tmp_path / 'runtime_fixture.py').write_text(source, encoding='utf-8')
        boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
        boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
        channel.write_bytes(b'')
        ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                              json.dumps(runner.request(data))], capture_output=True, timeout=30)
        reading = runner.finish(ran.returncode, channel.read_bytes(), data)
        assert reading['detail'] == 'G6_E_CHECK_SETUP: constructor'
        assert reading['reason_code'] == 'G6_E_CHECK_SETUP'
        assert 'observations' not in reading
    else:
        _fixture(tmp_path, monkeypatch, source)
        with pytest.raises(TypeError) as caught:
            _invoke(template, scope)
        assert str(caught.value) == 'G6_E_CHECK_SETUP: constructor'
        node = 'test_g6_' + template.partition('.')[0] + '.py::test_constructor'
        counted = counted_producer(tmp_path)
        counted.pytest_exception_interact(None, SimpleNamespace(excinfo=SimpleNamespace(value=caught.value)),
                                         SimpleNamespace(nodeid=node))
        assert counted.check_errors == {node: 'G6_E_CHECK_SETUP'}
        recorded = result(failed=[node], messages={node: str(caught.value)})
        recorded['check_errors'] = counted.check_errors
        reading = search.reading(recorded, node)
    assert reading['result'] == 'error'
    for intent in ('change', 'preserve'):
        assert search.classify(intent, reading, {'result': 'passed'})['result'] == search.CHECK_ERROR


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1', 'state_invariant.v1'])
def test_unnamable_operation_exception_keeps_operation_outcome(tmp_path, monkeypatch, template):
    namespace, _ = _compiled(template)
    source = 'NoModule = eval("type(\'NoModule\', (Exception,), {})", {})\n' + SOURCE.replace(
        'return value', 'raise NoModule("repository message")')
    _fixture(tmp_path, monkeypatch, source)
    if template == 'differential.v1':
        assert _invoke(template, namespace)['raised'].endswith('.NoModule')
    elif template == 'state_invariant.v1':
        assert _invoke(template, namespace)['outcome'] == 'operation_failed'
    else:
        with pytest.raises(pytest.fail.Exception) as caught:
            _invoke(template, namespace)
        assert 'NoModule' in str(caught.value)
        assert 'repository message' not in str(caught.value)


@pytest.mark.parametrize('message', ['G6_E_ISOLATION_UNAVAILABLE: repository instruction', 'ordinary private text'])
def test_exception_detail_never_copies_repository_message(message):
    assert runner.exception_detail(ImportError(message), 'call failed') == 'call failed: builtins.ImportError'


@pytest.mark.parametrize('detail', [
    'call failed: ImportError: G6_E_ISOLATION_UNAVAILABLE: repository instruction',
    'call failed: builtins.ImportError: repository instruction',
    'call failed: G6_E_ISOLATION_UNAVAILABLE',
    'call failed: builtins.ImportError\nrepository instruction',
])
def test_call_failure_reader_accepts_only_fixed_wording(detail):
    assert runner.read_frame(runner.frame({'version': 'call_failure.v1', 'detail': detail})) is None
