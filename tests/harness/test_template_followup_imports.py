"""Import preparation is distinct from a missing repository callable."""
import json
import subprocess
import sys

import pytest

from g6.harness import behaviour_runner as runner
from tests.harness.test_template_runtime_binding import SOURCE, _compiled, _invoke
from tests.harness.test_template_classification_repair3 import counted_producer
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1', 'relation.v1', 'access_rule.v1',
                                      'state_invariant.v1'])
@pytest.mark.parametrize(('prefix', 'qualified_name'), [
    ('import no_such_followup_external_dependency\n', 'builtins.ModuleNotFoundError'),
    ('raise AttributeError("repository text")\n', 'builtins.AttributeError'),
    ('raise ImportError("repository text")\n', 'builtins.ImportError'),
])
def test_import_preparation_has_no_function_observation(tmp_path, monkeypatch, template, prefix, qualified_name):
    namespace, _ = _compiled(template)
    (tmp_path / 'runtime_fixture.py').write_text(prefix + SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
    with pytest.raises(BaseException) as caught:
        _invoke(template, namespace)
    assert str(caught.value) == (
        'G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete: ' + qualified_name)
    assert 'repository text' not in str(caught.value)


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
@pytest.mark.parametrize('source', [
    'import pydantic\n' + SOURCE,
    'raise AttributeError("repository text")\n' + SOURCE,
    'def __getattr__(name):\n    raise AttributeError("repository text")\n',
])
def test_runner_import_or_lazy_lookup_is_unavailable(tmp_path, template, source):
    _, data = _compiled(template)
    (tmp_path / 'runtime_fixture.py').write_text(source, encoding='utf-8')
    boot, channel = tmp_path / 'bootstrap.py', tmp_path / 'result.bin'
    boot.write_text(runner.BOOTSTRAP, encoding='utf-8')
    channel.write_bytes(b'')
    ran = subprocess.run([sys.executable, '-I', '-S', '-B', str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=30)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found['result'] == 'error'
    assert found['detail'].startswith('G6_E_OBSERVATION_UNAVAILABLE:')
    assert 'repository text' not in found['detail']


@pytest.mark.parametrize('template', ['relation.v1', 'access_rule.v1', 'differential.v1', 'state_invariant.v1'])
@pytest.mark.parametrize('package_file', ['__init__.py', 'sibling.py'])
def test_held_package_missing_submodule_remains_a_call_failure(tmp_path, monkeypatch, template, package_file):
    namespace, _ = _compiled(template)
    package = tmp_path / 'held_package'
    package.mkdir()
    (package / package_file).write_text('', encoding='utf-8')
    (tmp_path / 'runtime_fixture.py').write_text('import held_package.missing\n' + SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ('runtime_fixture', 'held_package', 'held_package.missing'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with pytest.raises(BaseException) as caught:
        _invoke(template, namespace)
    assert 'ModuleNotFoundError' in str(caught.value) or isinstance(caught.value, ModuleNotFoundError)
    assert 'G6_E_' not in str(caught.value)


def test_held_import_failure_drops_its_repository_message(tmp_path, monkeypatch):
    namespace, _ = _compiled('relation.v1')
    package = tmp_path / 'held_package'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    (tmp_path / 'runtime_fixture.py').write_text(
        'raise ModuleNotFoundError("G6_E_ISOLATION_UNAVAILABLE: repository instruction", '
        'name="held_package.missing")\n' + SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
    with pytest.raises(ModuleNotFoundError) as caught:
        _invoke('relation.v1', namespace)
    assert str(caught.value) == 'call failed: builtins.ModuleNotFoundError'


def test_pytest_producer_carries_unavailable_import_as_an_error(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from g6.harness import verification_search as search

    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    namespace, _ = _compiled('access_rule.v1')
    (tmp_path / 'runtime_fixture.py').write_text('raise ImportError("repository message")\n' + SOURCE,
                                               encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
    with pytest.raises(BaseException) as caught:
        _invoke('access_rule.v1', namespace)
    node = 'test_g6_access_rule.py::test_access_rule'
    counted = counted_producer(tmp_path)
    counted.pytest_exception_interact(None, SimpleNamespace(excinfo=SimpleNamespace(value=caught.value)),
                                     SimpleNamespace(nodeid=node))
    assert counted.check_errors == {node: 'G6_E_OBSERVATION_UNAVAILABLE'}
    recorded = result(failed=[node], messages={node: str(caught.value)})
    recorded['check_errors'] = counted.check_errors
    reading = search.reading(recorded, node)
    assert reading['result'] == 'error'
    assert reading['detail'] == (
        'G6_E_OBSERVATION_UNAVAILABLE: import or lazy lookup did not complete: builtins.ImportError')
    assert 'repository message' not in reading['detail']
