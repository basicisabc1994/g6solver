# SPDX-License-Identifier: Apache-2.0
"""Runtime method lookup and the boundary between setup and operation failures."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.core.ledger import canonical
from g6.harness import behaviour_runner as runner, verification_formal as formal
from g6.harness import verification_search as search, verification_templates as vt
from g6.harness.executable_scenario import SETUP, Scenario
from tests.harness.test_verification_templates import result


TEMPLATES = ("example.v1", "relation.v1", "access_rule.v1", "differential.v1", "state_invariant.v1")


@pytest.mark.parametrize("template", tuple(vt.REGISTRY))
def test_every_registered_template_has_setup_separation_coverage(template):
    definition = vt.REGISTRY[template]
    if definition.applies is None:
        assert not definition.text, "a static template must not compile an unchecked runtime path"
    else:
        assert template in TEMPLATES, "add this executable template to the runtime setup-failure matrix"


SOURCE = '''class Machine:
    def __init__(self):
        self.n = 0
        # REPLACEMENT
    def act(self, value):
        return value
    def read(self):
        return self.n

def make():
    return Machine()
'''
REPLACEMENTS = (
    'self.act = lambda value: setattr(self, "n", -1) or value + 1',
    'type(self).act = lambda self, value: setattr(self, "n", -1) or value + 1',
)


class Tree:
    roots = ()

    def folders(self):
        return ("", "src/")

    def has(self, rel, side):
        return rel == "runtime_fixture.py"

    def text(self, rel, side):
        return SOURCE


def _compiled(template):
    constructor = {"call": "runtime_fixture.make"}
    bindings = {"constructor": constructor, "function": "runtime_fixture.Machine.act"}
    if template == "example.v1":
        bindings["cases"] = [{"args": [0], "returns": 0}]
    elif template == "relation.v1":
        bindings.update(relation="idempotence", inputs=[0])
    elif template == "access_rule.v1":
        bindings.pop("function")
        bindings.update(operation="runtime_fixture.Machine.act", refusal="falsy_return", cases=[
            {"who": "owner", "resource": "record", "args": [1], "expected": "allowed"},
            {"who": "guest", "resource": "record", "args": [0], "expected": "refused"}])
    elif template == "differential.v1":
        bindings.update(mode="outputs", calls=[{"args": [0]}])
    else:
        bindings.pop("function")
        bindings.update(
            model={"variables": {"n": "Int", "act": "Int"}, "init": "(and (= n 0) (= act 0))",
                   "transition": "(and (= next_act 1) (= next_n (- 1)))",
                   "invariants": {"nonnegative": "(>= n 0)"}}, action="act",
            actions={"1": {"call": "runtime_fixture.Machine.act", "args": [0]}},
            observe={"n": {"call": "runtime_fixture.Machine.read"}})
    [item] = vt.template_instances([{"id": "runtime", "template": template, "requirement_id": "R1",
                                    "intent": "preserve", "bindings": bindings}], {"R1"})
    definition = vt.REGISTRY[template]
    data = definition.applies(Tree(), definition.bindings.model_validate(item.bindings), vt.BASE).data
    if template == "state_invariant.v1":
        data["traces"] = [{"actions": [1], "ghost": {}}]
    namespace = {}
    exec(vt.compile_text(definition, data), namespace)
    return namespace, data


def _fixture(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runtime_fixture.py"
    path.write_text(source, encoding="utf-8")
    module = ModuleType("runtime_fixture")
    module.__file__ = str(path)
    exec(source, module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def _invoke(template, namespace):
    if template == "differential.v1":
        return namespace["_outcome"](namespace["BINDINGS"]["calls"][0])
    if template == "state_invariant.v1":
        return namespace["_replay"](namespace["BINDINGS"]["traces"][0])
    return namespace["test_" + template.split(".")[0]]()


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("replacement", REPLACEMENTS, ids=["instance-shadow", "class-rebind"])
def test_each_template_calls_the_runtime_method(tmp_path, monkeypatch, template, replacement):
    namespace, _ = _compiled(template)
    _fixture(tmp_path, monkeypatch, SOURCE.replace("# REPLACEMENT", replacement))
    if template == "differential.v1":
        assert _invoke(template, namespace) == {"returned": 1}
    elif template == "state_invariant.v1":
        assert _invoke(template, namespace)["outcome"] == "broken"
    else:
        with pytest.raises(pytest.fail.Exception):
            _invoke(template, namespace)


SETUP_FAILURES = {
    "import": 'raise ValueError("import failed")\n' + SOURCE,
    "constructor": SOURCE.replace("# REPLACEMENT", 'raise ValueError("setup failed")'),
    "constructor_type": SOURCE.replace("return Machine()", "return {}"),
    "method_lookup": SOURCE.replace("# REPLACEMENT", "self.act = None"),
    "attribute_lookup": SOURCE.replace("    def read(self):", '''    def __getattribute__(self, name):
        if name == "act":
            raise ValueError("lookup failed")
        return object.__getattribute__(self, name)
    def read(self):'''),
    "owner": SOURCE + "\nMachine = 0\n",
}


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("failure", SETUP_FAILURES)
def test_setup_failure_is_check_error(tmp_path, monkeypatch, template, failure):
    namespace, _ = _compiled(template)
    # Import failures must occur when the generated check loads the fixture, rather than in this test's setup.
    source = SETUP_FAILURES[failure]
    if failure == "import":
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runtime_fixture.py").write_text(source, encoding="utf-8")
        monkeypatch.delitem(sys.modules, "runtime_fixture", raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))
    else:
        _fixture(tmp_path, monkeypatch, source)
    with pytest.raises(BaseException, match="G6_E_CHECK_SETUP:") as caught:
        _invoke(template, namespace)
    node = "test_generated.py::test_runtime"
    patch = search.reading(result(failed=[node], messages={node: f"{type(caught.value).__name__}: {caught.value}"}), node)
    base = search.reading(result(passed=[node]), node)
    assert patch["result"] == "error"
    for intent in ("change", "preserve"):
        assert search.classify(intent, patch, base)["result"] == search.CHECK_ERROR


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("exception", ["ValueError", "TypeError", "SystemExit"])
def test_operation_exception_remains_a_failure(tmp_path, monkeypatch, template, exception):
    namespace, _ = _compiled(template)
    source = SOURCE.replace("return value", f'raise {exception}("operation failed")')
    _fixture(tmp_path, monkeypatch, source)
    if template == "differential.v1":
        assert _invoke(template, namespace) == {"raised": exception}
    elif template == "state_invariant.v1":
        outcome = _invoke(template, namespace)
        assert outcome["outcome"] == "operation_failed"
        patch = {"result": "failed", "detail": "operation failed",
                 "observations": [{"input": 0, "output": outcome}]}
        base = {"result": "passed", "detail": "holds",
                "observations": [{"input": 0, "output": {"outcome": "holds"}}]}
        assert formal._judge(1, "preserve", patch, base)["result"] == search.COUNTEREXAMPLE
    else:
        with pytest.raises(pytest.fail.Exception) as caught:
            _invoke(template, namespace)
        assert "G6_E_CHECK_SETUP:" not in str(caught.value)


@pytest.mark.parametrize("kind", ["method", "state_method"])
def test_binding_looks_up_again_on_every_call(kind):
    module = ModuleType("runtime_fixture")
    source = SOURCE.replace("return value", '''self.act = lambda value: value + 10
        return value''')
    exec(source, module.__dict__)
    state = module.Machine()
    spec = [kind, "runtime_fixture", "Machine.act", "runtime_fixture.py",
            {"call": ["module", "runtime_fixture", "make"], "args": [], "kwargs": {}}]
    operation = runner.bind_method(module, spec, lambda _: module.make)
    args = (state, 1) if kind == "state_method" else (1,)
    assert operation(*args) == 1
    assert operation(*args) == 11


@pytest.mark.parametrize("template", ["example.v1", "differential.v1"])
@pytest.mark.parametrize("replacement", REPLACEMENTS, ids=["instance-shadow", "class-rebind"])
def test_isolated_bootstrap_uses_runtime_lookup(tmp_path, monkeypatch, template, replacement):
    _, data = _compiled(template)
    _fixture(tmp_path, monkeypatch, SOURCE.replace("# REPLACEMENT", replacement))
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    data = runner.located(Path.cwd(), data)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    if template == "example.v1":
        assert found["result"] == "failed", (found, ran.stderr)
    else:
        assert found["result"] == "passed", (found, ran.stderr)
        assert found["observations"][0]["output"] == {"returned": 1}


def test_operation_message_cannot_masquerade_as_setup_error():
    node = "test_generated.py::test_runtime"
    found = search.reading(result(failed=[node], messages={
        node: "ValueError: G6_E_CHECK_SETUP: this text came from the operation"}), node)
    assert found["result"] == "failed"


@pytest.mark.parametrize("replacement", REPLACEMENTS, ids=["instance-shadow", "class-rebind"])
def test_multi_step_access_uses_runtime_observer(tmp_path, monkeypatch, replacement):
    source = SOURCE.replace("# REPLACEMENT", replacement)
    _fixture(tmp_path, monkeypatch, source)
    bindings = vt.AccessStepsBindings.model_validate({
        "constructor": {"call": "runtime_fixture.make"}, "operation": "runtime_fixture.Machine.act",
        "args": [0], "who": "guest", "resource": "record", "refusal": "falsy_return", "initially": "refused",
        "steps": {"touch": {"call": "runtime_fixture.Machine.read", "effect": "none"}}})
    definition = vt.FORMAL_TEMPLATES["state_invariant.v1"]
    bound = definition.bindings.model_validate(vt.access_model(bindings))
    data = definition.applies(Tree(), bound, vt.BASE).data
    data["traces"] = [{"actions": [1], "ghost": {"may": False}}]
    namespace = {}
    exec(vt.compile_text(definition, data), namespace)
    assert namespace["_replay"](data["traces"][0])["outcome"] == "broken"


def test_binding_error_is_not_an_expected_access_refusal(tmp_path, monkeypatch):
    namespace, _ = _compiled("access_rule.v1")
    _fixture(tmp_path, monkeypatch, SOURCE.replace("# REPLACEMENT", "self.act = None"))
    namespace["BINDINGS"].update(refusal="exception", error=["builtin", "", "TypeError"])
    with pytest.raises(TypeError, match="G6_E_CHECK_SETUP: method_lookup"):
        namespace["test_access_rule"]()


@pytest.mark.parametrize("failure", SETUP_FAILURES)
def test_differential_bootstrap_setup_is_not_an_observation(tmp_path, monkeypatch, failure):
    _, data = _compiled("differential.v1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime_fixture.py").write_text(SETUP_FAILURES[failure], encoding="utf-8")
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    data = runner.located(tmp_path, data)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    found = runner.finish(ran.returncode, channel.read_bytes(), data)
    assert found["result"] == "error", (found, ran.stderr)
    assert found["detail"].startswith("G6_E_OBSERVATION_UNAVAILABLE:")
    assert "observations" not in found


@pytest.mark.parametrize("failure", SETUP_FAILURES)
def test_example_bootstrap_setup_is_not_an_observation(tmp_path, monkeypatch, failure):
    _, data = _compiled("example.v1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime_fixture.py").write_text(SETUP_FAILURES[failure], encoding="utf-8")
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    data = runner.located(tmp_path, data)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    raw = channel.read_bytes()
    found = runner.finish(ran.returncode, raw, data)
    assert found["result"] == "error", (found, ran.stderr)
    assert found["detail"].startswith("G6_E_CHECK_SETUP:"), (found, ran.stderr)
    assert "observed" not in found and "observations" not in found
    assert "outcomes" not in runner.read_frame(raw)


def test_scenario_wrapper_import_failure_is_check_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime_fixture.py").write_text(SETUP_FAILURES["import"], encoding="utf-8")
    scenario = Scenario(canonical({
        "version": "example-scenario.v1", "setup": SETUP,
        "entrypoint": ["module", "runtime_fixture", "make", "runtime_fixture.py"],
        "input": {"args": [], "kwargs": {}}, "outcomes": {"A": {"expect": "returns", "value": None}},
    }).encode("ascii"))
    namespace = {}
    exec(scenario.source("A"), namespace)
    with pytest.raises(TypeError, match="G6_E_CHECK_SETUP: import_or_binding"):
        namespace["_check_example"]()


@pytest.mark.parametrize("detail", ["", "operation failed", None, 1])
def test_setup_frame_rejects_unmarked_or_nontext_details(detail):
    raw = runner.frame({"version": "check_setup.v1", "detail": detail})
    assert runner.read_frame(raw) is None


def test_example_path_binding_keeps_the_loader_refusal(tmp_path, monkeypatch):
    namespace, _ = _compiled("example.v1")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="G6_E_OBSERVATION_UNAVAILABLE: the copied entry point does not match"):
        namespace["_check_example"]()
    with pytest.raises(pytest.fail.Exception, match="G6_E_OBSERVATION_UNAVAILABLE: the copied entry point does not match"):
        namespace["test_example"]()


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
@pytest.mark.parametrize("signal", (
    KeyboardInterrupt(), SystemExit(2), GeneratorExit(), asyncio.CancelledError(),
    concurrent.futures.CancelledError(), ExecutionInterrupted("operator stop", stopped=True),
    ContainmentFailure("cleanup not established"),
), ids=("interrupt", "exit", "generator", "async-cancel", "future-cancel", "operator-stop", "cleanup"))
def test_setup_never_converts_an_execution_control_signal(template, signal):
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]

    def stopped():
        raise signal

    with pytest.raises(type(signal)) as caught:
        prepare("constructor", stopped)
    assert caught.value is signal


SETUP_REFUSALS = (
    "G6_E_OBSERVATION_UNAVAILABLE", "G6_E_SCENARIO_UNAVAILABLE", "G6_E_CHECK_SETUP",
    "G6_E_EXECUTION_ARTIFACT_UNAVAILABLE", "G6_E_ISOLATION_UNAVAILABLE", "G6_E_INPUT_VALIDATION",
)


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
@pytest.mark.parametrize("code", SETUP_REFUSALS)
def test_setup_refusals_keep_their_original_code(template, code):
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]
    refusal = ValueError(code + ": setup refused")

    def refused():
        raise refusal

    with pytest.raises(TypeError) as caught:
        prepare("constructor", refused)
    assert str(caught.value) == code + ": setup refused"
    node = "test_generated.py::test_runtime"
    found = search.reading(result(failed=[node], messages={
        node: f"{type(caught.value).__name__}: {caught.value}"}), node)
    assert found["result"] == "error" and code in found["detail"]


@pytest.mark.parametrize("template", ("relation.v1", "access_rule.v1", "differential.v1", "state_invariant.v1"))
@pytest.mark.parametrize("phase", ("import", "constructor", "attribute_lookup"))
def test_repository_setup_refusal_is_an_error_with_its_original_code(tmp_path, monkeypatch, template, phase):
    namespace, _ = _compiled(template)
    source = SETUP_FAILURES[phase].replace("import failed", "G6_E_OBSERVATION_UNAVAILABLE: setup refused")
    source = source.replace("setup failed", "G6_E_OBSERVATION_UNAVAILABLE: setup refused")
    source = source.replace("lookup failed", "G6_E_OBSERVATION_UNAVAILABLE: setup refused")
    if phase == "import":
        monkeypatch.chdir(tmp_path)
        (tmp_path / "runtime_fixture.py").write_text(source, encoding="utf-8")
        monkeypatch.delitem(sys.modules, "runtime_fixture", raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))
    else:
        _fixture(tmp_path, monkeypatch, source)
    with pytest.raises(TypeError) as caught:
        _invoke(template, namespace)
    assert str(caught.value) == "G6_E_OBSERVATION_UNAVAILABLE: setup refused"
    node = "test_generated.py::test_runtime"
    found = search.reading(result(failed=[node], messages={
        node: f"{type(caught.value).__name__}: {caught.value}"}), node)
    assert found["result"] == "error"
    assert "G6_E_OBSERVATION_UNAVAILABLE" in found["detail"]


def test_nested_setup_keeps_the_original_error():
    refusal = runner.CheckSetupError("G6_E_SCENARIO_UNAVAILABLE: no selected option")

    def refused():
        raise refusal

    with pytest.raises(runner.CheckSetupError) as caught:
        runner.setup_call("constructor", refused)
    assert caught.value is refusal
    assert str(caught.value) == "G6_E_SCENARIO_UNAVAILABLE: no selected option"
