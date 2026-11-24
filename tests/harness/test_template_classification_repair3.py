"""Classification classes for the third template-method repair; no host/model calls."""
import inspect
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as search
from g6.harness.executable_scenario import SETUP, Scenario
from g6.core.ledger import canonical
from tests.harness.test_template_runtime_binding import SETUP_FAILURES, SOURCE, TEMPLATES, _compiled, _fixture, _invoke
from tests.harness.test_verification_templates import result


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("missing", ["owner", "method", "module"])
@pytest.mark.parametrize("intent", ["preserve", "change"])
def test_missing_callable_is_behaviour(tmp_path, monkeypatch, template, missing, intent):
    namespace, _ = _compiled(template)
    source = SOURCE
    if missing == "owner":
        source = source.replace("class Machine:", "class Other:")
        if template == 'state_invariant.v1':
            source = source.replace('def make(', 'def other_make(')
    elif missing == "method":
        source = source.replace("def act(", "def other(")
    else:
        source = "import no_such_repair3_dependency\n" + source
    (tmp_path / "runtime_fixture.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "runtime_fixture", raising=False)
    with pytest.raises(BaseException) as caught:
        _invoke(template, namespace)
    assert type(caught.value).__name__ != "CheckSetupError"
    assert "AttributeError" in str(caught.value) or "ModuleNotFoundError" in str(caught.value) or isinstance(
        caught.value, (AttributeError, ImportError))
    node = "test_generated.py::test_runtime"
    missing_run = search.reading(result(failed=[node], messages={node: str(caught.value)}), node)
    passing = search.reading(result(passed=[node]), node)
    patch, base = (missing_run, passing) if intent == "preserve" else (passing, missing_run)
    assert search.classify(intent, patch, base)["result"] == (
        search.COUNTEREXAMPLE if intent == "preserve" else search.NOT_REFUTED)


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
@pytest.mark.parametrize("forgery", ["module", "code", "message", "missing_module_metadata"])
def test_only_owned_setup_class_supplies_setup_reason(template, forgery):
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]
    kind = type("RepositoryError", (ValueError,), {})
    if forgery == "module":
        kind.__module__ = "g6.workforce.repo_tests"
        kind.code = "G6_E_CLOUD_CONSENT_REQUIRED"
    elif forgery == "code":
        kind.code = "G6_E_CLOUD_CONSENT_REQUIRED"
    elif forgery == "missing_module_metadata":
        kind = eval("type('RepositoryError', (Exception,), {})", {})
    message = "G6_E_ISOLATION_UNAVAILABLE: repository instructions " + "x" * 100000

    def fail():
        raise kind(message)

    with pytest.raises(TypeError) as caught:
        prepare("method_lookup", fail)
    assert str(caught.value) == "G6_E_CHECK_SETUP: method_lookup"


def test_setup_frame_detail_is_bounded_and_uses_owned_code():
    for detail in ("G6_E_CHECK_SETUP: " + "x" * 1000, "G6_E_ISOLATION_UNAVAILABLE: forged"):
        assert runner.read_frame(runner.frame({"version": "check_setup.v1", "detail": detail})) is None


def test_setup_finish_carries_structured_reason():
    raw = runner.frame({'version': 'check_setup.v1', 'detail': 'G6_E_CHECK_SETUP: method_lookup'})
    assert runner.finish(0, raw, {})['reason_code'] == 'G6_E_CHECK_SETUP'
    assert 'reason_code' not in runner.finish(3, b'', {})


def test_setup_classification_uses_structured_reason_only():
    node = "test_generated.py::test_runtime"
    forged = result(failed=[node], messages={node: "CheckSetupError: G6_E_CHECK_SETUP: method_lookup"})
    assert search.reading(forged, node)["result"] == "failed"
    recorded = {**forged, "check_errors": {node: "G6_E_CHECK_SETUP"}}
    assert search.reading(recorded, node)["result"] == "error"


def counted_producer(tmp_path):
    from g6.workforce import repo_tests

    source = repo_tests._BOOT.split("class _Counted:", 1)[1].split("class _Capture:", 1)[0]
    namespace = {"json": json, "sys": sys, "counted": str(tmp_path / "counted.json")}
    exec("class _Counted:" + source, namespace)
    return namespace["_Counted"]()


def test_counted_producer_records_owned_setup_class(tmp_path, monkeypatch):
    """Exercise the real exception hook, without starting pytest or loading repository configuration."""
    counted = counted_producer(tmp_path)
    owned = {"__name__": "generated_check", 'sys': sys}
    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    exec(inspect.getsource(runner.CheckSetupError) + '\n' + inspect.getsource(runner.register_setup_error), owned)
    owned['register_setup_error']()
    error = owned["CheckSetupError"]("method_lookup")
    call = SimpleNamespace(excinfo=SimpleNamespace(value=error))
    report = SimpleNamespace(nodeid="test_generated.py::test_runtime")
    counted.pytest_exception_interact(None, call, report)
    assert counted.check_errors == {report.nodeid: "G6_E_CHECK_SETUP"}
    forged = type("CheckSetupError", (TypeError,), {"__module__": "generated_check"})(str(error))
    counted.check_errors.clear()
    call.excinfo.value = forged
    counted.pytest_exception_interact(None, call, report)
    assert counted.check_errors == {}


@pytest.mark.parametrize('template', TEMPLATES)
@pytest.mark.parametrize('failure', SETUP_FAILURES)
def test_setup_failure_producer_record_is_check_error(tmp_path, monkeypatch, template, failure):
    monkeypatch.setattr(sys, '_g6_check_setup_types', [], raising=False)
    namespace, _ = _compiled(template)
    source = SETUP_FAILURES[failure]
    if failure == 'import':
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'runtime_fixture.py').write_text(source, encoding='utf-8')
        monkeypatch.delitem(sys.modules, 'runtime_fixture', raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))
    else:
        _fixture(tmp_path, monkeypatch, source)
    with pytest.raises(TypeError, match='G6_E_CHECK_SETUP:') as caught:
        _invoke(template, namespace)
    node = 'test_generated.py::test_runtime'
    counted = counted_producer(tmp_path)
    counted.pytest_exception_interact(None, SimpleNamespace(excinfo=SimpleNamespace(value=caught.value)),
                                     SimpleNamespace(nodeid=node))
    produced = result(failed=[node], messages={node: str(caught.value)})
    produced['check_errors'] = counted.check_errors
    patch = search.reading(produced, node)
    base = search.reading(result(passed=[node]), node)
    assert patch['result'] == 'error'
    for intent in ('preserve', 'change'):
        assert search.classify(intent, patch, base)['result'] == search.CHECK_ERROR


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("missing", ["owner", "method", "module"])
def test_bootstrap_missing_callable_is_failed(tmp_path, template, missing):
    _, data = _compiled(template)
    source = SOURCE.replace("class Machine:", "class Other:") if missing == "owner" else (
        SOURCE.replace("def act(", "def other(") if missing == "method" else
        "import no_such_repair3_dependency\n" + SOURCE)
    (tmp_path / "runtime_fixture.py").write_text(source, encoding="utf-8")
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found["result"] == "failed", (found, ran.stderr)
    assert ("ModuleNotFoundError" if missing == "module" else "AttributeError") in found["detail"]


def test_differential_constructs_once_per_execution(tmp_path, monkeypatch, capsys):
    namespace, _ = _compiled('differential.v1')
    _fixture(tmp_path, monkeypatch, SOURCE.replace('return value', 'self.n += 1\n        return self.n'))
    call = namespace['BINDINGS']['calls'][0]
    assert [namespace['_outcome'](call) for _ in range(2)] == [{'returned': 1}, {'returned': 2}]
    namespace['test_differential'](capsys)
    assert namespace['_outcome'](call) == {'returned': 2}


@pytest.mark.parametrize('wrapper', ['example', 'scenario'])
@pytest.mark.parametrize('failure', ['exit', 'unresolved_error'])
def test_example_wrappers_do_not_label_completed_calls_as_setup(tmp_path, monkeypatch, wrapper, failure):
    outcome = {'expect': 'returns', 'value': 1} if failure == 'exit' else {
        'expect': 'raises', 'error': ['module', 'runtime_fixture', 'Denied', 'runtime_fixture.py']}
    source = ('import os\ndef run(value):\n    os._exit(3)\n' if failure == 'exit' else
              'def run(value):\n    raise ValueError("operation ran")\n')
    (tmp_path / 'runtime_fixture.py').write_text(source, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    if wrapper == 'scenario':
        scenario = Scenario(canonical({
            'version': 'example-scenario.v1', 'setup': SETUP,
            'entrypoint': ['module', 'runtime_fixture', 'run', 'runtime_fixture.py'],
            'input': {'args': [0], 'kwargs': {}}, 'outcomes': {'A': outcome},
        }).encode('ascii'))
        text = scenario.source('A')
    else:
        data = {'function': ['module', 'runtime_fixture', 'run', 'runtime_fixture.py'],
                'cases': [{'args': [0], 'kwargs': {}, **outcome}]}
        text = runner.example_text(ascii(data))
    namespace = {}
    exec(text, namespace)
    with pytest.raises(AssertionError) as caught:
        namespace['_check_example']()
    assert type(caught.value).__name__ != 'CheckSetupError'
