# SPDX-License-Identifier: Apache-2.0
"""Control flow and diagnostics must never manufacture a successful check."""
from __future__ import annotations

import ast
import asyncio
import builtins
import concurrent.futures
import importlib
import inspect
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.harness import behaviour_runner as runner, verification_templates as vt
from g6.harness.executable_scenario import SETUP, Scenario
from g6.core.ledger import canonical
from g6.workforce.repo_tests import RepoRefused
from tests.harness.test_template_runtime_binding import TEMPLATES, SOURCE, _compiled, _fixture, _invoke


SIGNALS = (
    KeyboardInterrupt(), SystemExit(7), GeneratorExit(), asyncio.CancelledError(),
    concurrent.futures.CancelledError(), ExecutionInterrupted("stop"), ContainmentFailure("cleanup"),
)
SIGNAL_IDS = ("interrupt", "exit", "generator", "async-cancel", "future-cancel", "stop", "cleanup")


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
def test_setup_diagnostics_do_not_call_exception_hooks(template):
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]
    called = []

    class Meta(type):
        def __getattribute__(self, name):
            called.append("type." + name)
            raise TypeError("type hook")

    class Failure(Exception, metaclass=Meta):
        def __getattribute__(self, name):
            called.append(name)
            raise TypeError("attribute hook")

        def __str__(self):
            called.append("str")
            raise TypeError("display hook")

        def __repr__(self):
            called.append("repr")
            raise TypeError("display hook")

    failure = Failure("lookup failed")

    def lookup():
        raise failure

    with pytest.raises(TypeError, match="G6_E_CHECK_SETUP: method_lookup") as caught:
        prepare("method_lookup", lookup)
    assert type(caught.value) is not TypeError
    assert caught.value.__cause__ is failure
    assert called == []


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
def test_real_repo_refusal_retains_code_without_display(template):
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]
    refusal = RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "setup refused")

    def lookup():
        raise refusal

    with pytest.raises(TypeError, match="G6_E_ISOLATION_UNAVAILABLE") as caught:
        prepare("method_lookup", lookup)
    assert caught.value.__cause__ is refusal


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
def test_typed_code_storage_does_not_call_attribute_or_string_hooks(template):
    called = []

    class Refusal(RepoRefused):
        def __getattribute__(self, name):
            called.append(name)
            raise TypeError("attribute hook")

        def __str__(self):
            called.append("str")
            raise TypeError("display hook")

    refusal = Refusal("G6_E_ISOLATION_UNAVAILABLE", "setup refused")
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]

    def lookup():
        raise refusal

    with pytest.raises(TypeError, match="G6_E_ISOLATION_UNAVAILABLE") as caught:
        prepare("method_lookup", lookup)
    assert caught.value.__cause__ is refusal
    assert called == []


@pytest.mark.parametrize("template", ("relation.v1", "access_rule.v1", "differential.v1", "state_invariant.v1"))
def test_refusal_diagnostics_never_call_exception_hooks(template):
    namespace, _ = _compiled(template)
    called = []

    class Failure(Exception):
        def __getattribute__(self, name):
            called.append(name)
            raise TypeError("attribute hook")

        def __str__(self):
            called.append("str")
            raise TypeError("display hook")

        def __repr__(self):
            called.append("repr")
            raise TypeError("display hook")

    shown = namespace["_raised"](Failure("operation refused"))
    assert "Failure" in shown and "operation refused" in shown
    assert called == []


@pytest.mark.parametrize("template", ("relation.v1", "access_rule.v1", "differential.v1", "state_invariant.v1"))
@pytest.mark.parametrize("phase", ("lookup",))
@pytest.mark.parametrize("signal", SIGNALS, ids=SIGNAL_IDS)
def test_generated_handlers_preserve_control_identity(tmp_path, monkeypatch, template, phase, signal):
    namespace, _ = _compiled(template)
    assert phase == "lookup"
    source = SOURCE.replace("    def read(self):", '''    def __getattribute__(self, name):
        if name == "act":
            raise SIGNAL
        return object.__getattribute__(self, name)
    def read(self):''')
    module = _fixture(tmp_path, monkeypatch, source)
    module.SIGNAL = signal
    with pytest.raises(type(signal)) as caught:
        _invoke(template, namespace)
    assert caught.value is signal


@pytest.mark.parametrize("template", ("example.v1", "differential.v1"))
@pytest.mark.parametrize("phase", ("lookup",))
@pytest.mark.parametrize("signal", (
    "KeyboardInterrupt()", "SystemExit(7)", "GeneratorExit()", "asyncio.CancelledError()",
    "concurrent.futures.CancelledError()",
), ids=SIGNAL_IDS[:5])
def test_bootstrap_control_never_publishes_a_completed_frame(tmp_path, monkeypatch, template, phase, signal):
    _, data = _compiled(template)
    assert phase == "lookup"
    source = SOURCE.replace("    def read(self):", '''    def __getattribute__(self, name):
        if name == "act":
            raise SIGNAL
        return object.__getattribute__(self, name)
    def read(self):''')
    source = "import asyncio, concurrent.futures\n" + source.replace("SIGNAL", signal)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime_fixture.py").write_text(source, encoding="utf-8")
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    data = runner.located(tmp_path, data)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    raw = channel.read_bytes()
    assert runner.read_frame(raw) is None, (raw, ran.stderr)
    reading = runner.finish(ran.returncode, raw, data)
    assert reading["result"] == "error"
    assert "observations" not in reading and "observed" not in reading


@pytest.mark.parametrize("template", ("host", *TEMPLATES))
def test_setup_origin_uses_frame_identity_and_does_not_call_exception_hooks(template):
    namespace = vars(runner) if template == "host" else _compiled(template)[0]
    called = []
    # Exercise the predicate in the same generated scope as the real operation handlers.
    scope = dict(namespace)
    exec('''def boundary(function, prepare):
    try:
        if prepare:
            return setup_call("method_lookup", function)
        return function()
    except BaseException as exc:
        return raised_during_setup(exc)
''', scope)

    class Exit(SystemExit):
        def __getattribute__(self, name):
            called.append(name)
            raise TypeError("attribute hook")

    signal = Exit(7)

    def stopped():
        raise signal

    # Functions must share the real generated namespace, just as the production handlers do.
    boundary = type(scope["boundary"])(scope["boundary"].__code__, namespace)
    assert boundary(stopped, True)
    assert called == []

    # A repository function with the same name is still an operation, not G6 preparation.
    def setup_call():
        raise signal

    # Reuse the same exception, which still holds its earlier genuine setup traceback.
    assert not boundary(setup_call, False)
    assert called == []


@pytest.mark.parametrize("template", ("example.v1", "differential.v1"))
def test_bootstrap_operation_system_exit_is_a_completed_failure(tmp_path, monkeypatch, template):
    _, data = _compiled(template)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime_fixture.py").write_text(SOURCE.replace("return value", "raise SystemExit(7)"),
                                               encoding="utf-8")
    boot, channel = tmp_path / "bootstrap.py", tmp_path / "result.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    data = runner.located(tmp_path, data)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel),
                          json.dumps(runner.request(data))], capture_output=True, timeout=10)
    frame = runner.read_frame(channel.read_bytes())
    assert frame["version"] == "observation.v2", (frame, ran.stderr)
    assert frame["outcomes"][0]["raised_name"] == "builtins.SystemExit"
    if template == "example.v1":
        assert runner.finish(ran.returncode, channel.read_bytes(), data)["result"] == "failed"


def _sources():
    yield "runner", inspect.getsource(runner)
    yield "bootstrap", runner.BOOTSTRAP
    for ident, definition in vt.REGISTRY.items():
        if definition.text:
            yield ident, definition.text.replace(vt.SLOT, "{}")
    scenario = Scenario(canonical({
        "version": "example-scenario.v1", "setup": SETUP,
        "entrypoint": ["module", "fixture", "run", "fixture.py"],
        "input": {"args": [], "kwargs": {}}, "outcomes": {"A": {"expect": "returns", "value": None}},
    }).encode("ascii"))
    yield "scenario", runner.expanded(scenario.source("A"))


def _ordinary_handler_type(node):
    spelling = ast.unparse(node)
    return {
        "CheckSetupError": runner.CheckSetupError, "zlib.error": zlib.error, "struct.error": struct.error,
        "component_process.ExecutionInterrupted": ExecutionInterrupted,
        "component_process.ContainmentFailure": ContainmentFailure,
    }.get(spelling, getattr(builtins, spelling, None))


def test_handler_ast_inventory_excludes_control_signals():
    # Setup controls escape; operation outcomes and the child diagnostic recorder retain the base contract.
    inventory = []
    for label, source in _sources():
        tree = ast.parse(source)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for statement in ast.walk(tree):
            if not isinstance(statement, (ast.Try, ast.TryStar)):
                continue
            for handler in statement.handlers:
                inventory.append((label, handler.lineno, ast.unparse(handler.type) if handler.type else "bare"))
                assert handler.type is not None, inventory[-1]
                if isinstance(handler.type, ast.Name) and handler.type.id == "LETS_THROUGH":
                    assert len(handler.body) == 1 and isinstance(handler.body[0], ast.Raise), inventory[-1]
                    assert handler.body[0].exc is None, inventory[-1]
                    continue
                kinds = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                for kind in kinds:
                    actual = _ordinary_handler_type(kind)
                    assert isinstance(actual, type) and issubclass(actual, BaseException), inventory[-1]
                    if actual is not BaseException:
                        # Resolve each member separately: an ordinary tuple, including struct.error, is valid.
                        assert issubclass(actual, Exception), inventory[-1]
                        continue
                    ancestors, parent = [], handler
                    while parent in parents:
                        parent = parents[parent]
                        ancestors.append(parent)
                    recorder = any(isinstance(parent, ast.FunctionDef) and parent.name == "_exception_observation"
                                   for parent in ancestors)
                    if recorder:
                        continue
                    # These existing operation handlers must let genuine preparation failures through first.
                    guard = handler.body[0]
                    assert isinstance(guard, ast.If), inventory[-1]
                    assert any(isinstance(one, ast.Call) and isinstance(one.func, ast.Name)
                               and one.func.id == "raised_during_setup" for one in ast.walk(guard.test)), inventory[-1]
                    assert len(guard.body) == 1 and isinstance(guard.body[0], ast.Raise), inventory[-1]
                    assert guard.body[0].exc is None, inventory[-1]
    assert inventory
    assert set(vt.REGISTRY) >= {ident for ident in TEMPLATES}


def test_handler_inventory_accepts_ordinary_exception_tuples():
    handler = ast.parse("try: pass\nexcept (ValueError, TypeError, RecursionError, OverflowError, "
                        "struct.error, UnicodeError): pass").body[0].handlers[0]
    assert all(issubclass(_ordinary_handler_type(kind), Exception) for kind in handler.type.elts)


def _coded_exception_types():
    """Refusal carriers: ordinary Exception ancestry, raw code storage and a stable G6_E_ namespace.

    Read only tracked G6 Python sources. RepoRefused explicitly declares its code contract; other modules must
    declare G6_E_ literals (in code or their refusal documentation). Include same-module inherited carriers.
    BaseException-only loop halts/limits are control flow, regardless of whether they also store a code.
    """
    root = Path(__file__).parents[2]
    paths = subprocess.run(["git", "ls-files", "src/g6/*.py", "src/g6/**/*.py"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.splitlines()
    classes = {}
    for relative in paths:
        module = relative[:-3].replace("/", ".").removeprefix("src.")
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        stable_codes = any(isinstance(part, ast.Constant) and isinstance(part.value, str) and "G6_E_" in part.value
                           for part in ast.walk(tree))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [ast.unparse(base) for base in node.bases]
            code = any(isinstance(part, ast.Attribute) and isinstance(part.ctx, ast.Store)
                       and isinstance(part.value, ast.Name) and part.value.id == "self" and part.attr == "code"
                       for part in ast.walk(node))
            code = code or any(isinstance(part, (ast.Assign, ast.AnnAssign)) and any(
                isinstance(target, ast.Name) and target.id == "code"
                for target in (part.targets if isinstance(part, ast.Assign) else [part.target]))
                for part in node.body)
            classes[module, node.name] = (bases, code and stable_codes)
    def exception(module, name, seen=()):
        if name == "Exception":
            return True
        if name == "BaseException":
            return False
        if name not in {entry[1] for entry in classes if entry[0] == module}:
            kind = getattr(builtins, name, None)
            return isinstance(kind, type) and issubclass(kind, Exception)
        if name in seen:
            return False
        return any(exception(module, base, (*seen, name)) for base in classes[module, name][0])

    def coded(module, name):
        bases, code = classes[module, name]
        return code or any((module, base) in classes and coded(module, base) for base in bases)

    return sorted((module, name) for module, name in classes if exception(module, name) and coded(module, name))


@pytest.mark.parametrize("module,name", _coded_exception_types())
@pytest.mark.parametrize("template", ("host", *TEMPLATES))
def test_every_g6_code_carrier_keeps_its_typed_code(module, name, template):
    kind = getattr(importlib.import_module(module), name)
    # Exercise the actual type with its raw storage contract, independent of constructor-specific extra inputs.
    refusal = kind.__new__(kind)
    BaseException.__init__(refusal, "setup refused")
    refusal.code = "G6_E_ISOLATION_UNAVAILABLE"
    prepare = runner.setup_call if template == "host" else _compiled(template)[0]["setup_call"]

    def lookup():
        raise refusal

    with pytest.raises(TypeError, match="G6_E_ISOLATION_UNAVAILABLE") as caught:
        prepare("method_lookup", lookup)
    assert caught.value.__cause__ is refusal


def test_code_carrier_inventory_includes_repo_refused():
    assert ("g6.workforce.repo_tests", "RepoRefused") in _coded_exception_types()


def test_code_carrier_inventory_excludes_loop_internal_controls():
    carriers = _coded_exception_types()
    assert ("g6.metaharness.loop", "_Halted") not in carriers
    for module, name in carriers:
        assert issubclass(getattr(importlib.import_module(module), name), Exception)
