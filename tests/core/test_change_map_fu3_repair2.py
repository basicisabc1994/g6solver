# SPDX-License-Identifier: Apache-2.0
"""Binding and installed-lookup regressions, written before the repair.

The no-lane override defers runtime checks to the lead. No snippet is imported
by the source reader; the inventory helper exercises its shipped entry point.
"""
import ast
import hashlib
import sys
import sysconfig
from importlib.metadata import distribution
from pathlib import Path

import pytest

from g6.workforce import _collection_effects as effects
from tests.core.test_change_map_fu2_effects import inventory


PRELUDE = "import os\ndef install():\n    os.environ['PATH'] = 'repair-two'\n"


@pytest.mark.parametrize("route", ["alias", "assigned-test", "inherited-test", "imported-alias"])
def test_collected_binding_audits_instance(tmp_path, monkeypatch, route):
    source = (PRELUDE + "class Settings:\n"
              "    def __getattribute__(self, name):\n        install()\n        return 1\n"
              "    def test_call(self): pass\n")
    if route == "assigned-test":
        source = source.replace("class Settings:\n", "class Settings:\n    __test__ = True\n")
    elif route == "inherited-test":
        source = source.replace("class Settings:\n", "class Base:\n    __test__ = True\nclass Settings(Base):\n")
    elif route == "alias":
        source += "TestSettings = Settings\n"
    files = {"test_api.py": source}
    if route == "imported-alias":
        files = {"helper.py": source, "test_api.py": "from helper import Settings as TestSettings\n"}
    _, usable = inventory(tmp_path, monkeypatch, files)
    assert usable is False


def test_unresolved_test_attribute_is_unavailable(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nclass Settings:\n    __test__ = os.environ.get('APP_TEST')\n",
    })
    assert usable is False


def test_run_test_only_follows_installed_constructor(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": PRELUDE + "from unittest import TestCase\nclass Settings(TestCase):\n"
        "    def addTypeEqualityFunc(self, typeobj, function):\n        install()\n"
        "    def runTest(self): pass\n",
    })
    assert usable is False


def test_bound_helper_module_lookup_is_followed(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": PRELUDE + "def __getattr__(name):\n    install()\n    return 1\n",
        "test_api.py": "import helper as settings\ndef test_call(): pass\n",
    })
    assert usable is False


@pytest.mark.parametrize("consumer", ["pytest", "dataclass"])
def test_call_built_metaclass_descriptor_is_audited(tmp_path, monkeypatch, consumer):
    attribute = "setup_class" if consumer == "pytest" else "value"
    source = (PRELUDE + "class Meta(type):\n"
              "    def load(cls):\n        install()\n        return None\n"
              "    " + attribute + " = property(load)\n")
    if consumer == "pytest":
        source += "class TestSettings(metaclass=Meta):\n    def test_call(self): pass\n"
    else:
        source += "from dataclasses import dataclass\n@dataclass\nclass Settings(metaclass=Meta):\n    value: int\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("source", [
    "class Settings: pass\nTestSettings = Settings\n",
    "class Settings:\n    __test__ = True\n    def test_call(self): pass\n",
    "class Base:\n    __test__ = True\nclass Settings(Base):\n    def test_call(self): pass\n",
    "from unittest import TestCase\nclass Settings(TestCase):\n    def runTest(self): pass\n",
])
def test_inert_bound_classes_remain_certain(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


def test_inert_bound_module_hook_remains_certain(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": "def __getattr__(name): return 1\n",
        "test_api.py": "import helper\ndef test_call(): pass\n",
    })
    assert usable is True


@pytest.mark.parametrize("holder", ["helper", "collected"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_dir_lookup_uses_only_collection_holders(tmp_path, monkeypatch, holder, effect):
    hook = PRELUDE + "def __dir__():\n    " + ("install()\n    " if effect else "") + "return []\n"
    files = {"test_api.py": hook + "def test_call(): pass\n"}
    if holder == "helper":
        files = {"helper.py": hook, "test_api.py": "import helper\ndef test_call(): pass\n"}
    _, usable = inventory(tmp_path, monkeypatch, files)
    assert usable is (holder == "helper" or not effect)


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_bound_helper_test_probe_runs_getattr(tmp_path, monkeypatch, effect):
    hook = PRELUDE + "def __getattr__(name):\n    " + ("install()\n    " if effect else "") + "return 1\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": hook, "test_api.py": "import helper\ndef test_call(): pass\n",
    })
    assert usable is (not effect)


@pytest.mark.parametrize("source", [
    "import os\n",
    "import os\nMODE = os.environ.get('APP_ENV', 'test')\n",
    "import os\nclass Settings:\n    mode = os.environ.get('APP_ENV', 'test')\n",
    "import os\ndef make(): return os.environ.get('APP_ENV', 'test')\nMODE = make()\n",
], ids=["stdlib-module", "stdlib-result", "stdlib-member", "returned-stdlib-result"])
def test_nonrepository_bindings_have_inert_lookup(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("repository", [False, True], ids=["installed-control", "repository-argument"])
def test_external_result_with_repository_argument_requires_identity(tmp_path, monkeypatch, repository):
    source = "class Value: pass\nKIND = type(Value)\n" if repository else "KIND = type(1)\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not repository)


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_factory_returned_class_keeps_collecting_binding(tmp_path, monkeypatch, effect):
    source = PRELUDE + "def make():\n    class Settings:\n"
    if effect:
        source += "        def __getattribute__(self, name):\n            install()\n            return 1\n"
    source += "        def test_call(self): pass\n    return Settings\nTestSettings = make()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
@pytest.mark.parametrize("hook", [False, True], ids=["plain-control", "lookup-hook"])
def test_metaclass_supplied_test_flag_is_unavailable(tmp_path, monkeypatch, inherited, hook):
    source = "class Meta(type):\n"
    source += "    def __getattr__(cls, name): return True\n" if hook else "    pass\n"
    if inherited:
        source += "class Base(metaclass=Meta): pass\nclass Settings(Base):\n"
    else:
        source += "class Settings(metaclass=Meta):\n"
    source += "    def test_call(self): pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not hook)


def test_lookup_table_covers_installed_pytest_source():
    # This is an upstream-boundary guard: an added lookup requires reviewing
    # the reader's model, even when the upstream version number is unchanged.
    table = effects.PYTEST_COLLECTION_LOOKUPS
    assert distribution("pytest").version == table["pytest_version"]
    for package, relative, digest in table["sources"]:
        source = distribution(package).locate_file(relative).read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest, relative
        rows = [row for row in table["reads"] if row[0] == relative]
        names = {name for _, _, _, attributes, _ in rows for name in attributes}
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if function not in {"getattr", "hasattr", "safe_getattr", "getattr_static"} or len(node.args) < 2:
                continue
            name = node.args[1]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                assert name.value in names, (relative, node.lineno, name.value)
            else:
                assert any(line == node.lineno and "*" in attrs for _, line, _, attrs, _ in rows), (
                    relative, node.lineno, "dynamic lookup")


def test_lookup_table_pins_transitive_stdlib_sources():
    table = effects.PYTEST_COLLECTION_LOOKUPS
    assert tuple(sys.version_info[:3]) == table["python_version"]
    assert {relative for relative, _ in table["stdlib_sources"]} == {"inspect.py", "unittest/loader.py"}
    for relative, digest in table["stdlib_sources"]:
        source = (Path(sysconfig.get_path("stdlib")) / relative).read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest, relative
        rows = [row for row in table["reads"] if row[0] == relative]
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"getattr", "hasattr", "getattr_static"}:
                    name = node.args[1]
                    wanted = name.value if isinstance(name, ast.Constant) else "*"
                elif node.func.id == "dir":
                    wanted = "__dir__"
                else:
                    continue
                assert any(line == node.lineno and wanted in attrs for _, line, _, attrs, _ in rows), (
                    relative, node.lineno, wanted)
            elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and isinstance(node.ctx, ast.Load):
                assert any(line == node.lineno and node.attr in attrs for _, line, _, attrs, _ in rows), (
                    relative, node.lineno, node.attr)


def test_lookup_table_binds_indirect_names_to_call_sites():
    table = effects.PYTEST_COLLECTION_LOOKUPS
    for relative, helper, argument, lookup_lines, anchors in table["indirect"]:
        source = (Path(sysconfig.get_path("stdlib")) / relative if relative == "inspect.py" else
                  distribution("pytest").locate_file(relative))
        tree = ast.parse(source.read_bytes())
        constants = {node.targets[0].id: node.value.value for node in ast.walk(tree)
                     if isinstance(node, ast.Assign) and len(node.targets) == 1 and
                     isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant)}
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                 isinstance(node.func, ast.Name) and node.func.id == helper]
        assert {node.lineno for node in calls} == {line for line, _ in anchors}
        for call in calls:
            value = call.args[argument]
            names = (value.value,) if isinstance(value, ast.Constant) else tuple(
                item.value if isinstance(item, ast.Constant) else constants[item.id] for item in value.elts)
            assert (call.lineno, names) in anchors
            for lookup_line in lookup_lines:
                assert any(source == relative and line == lookup_line and "*" in attrs
                           for source, line, _, attrs, _ in table["reads"])
            assert any(source == relative and line == call.lineno and set(names) <= set(attrs)
                       for source, line, _, attrs, _ in table["reads"])
    assert {helper for _, helper, _, _, _ in table["indirect"]} == {
        "_get_first_non_fixture_func", "_get_legacy_hook_marks", "_signature_get_user_defined_method"}


def test_transitive_lookup_routes_are_explicit():
    table = effects.PYTEST_COLLECTION_LOOKUPS
    roots = {(source, name) for source, name, _, _ in table["transitive"]}
    assert {("inspect.py", name) for name in (
        "unwrap", "isabstract", "signature", "from_callable", "_signature_from_callable",
        "_signature_from_function", "_signature_fromstr", "_signature_is_functionlike",
        "get_annotations", "getattr_static", "_descriptor_get", "_has_code_flag",
        "_has_coroutine_mark")} <= roots
    assert ("unittest/loader.py", "getTestCaseNames") in roots
    for _, _, targets, names, _ in table["reads"]:
        if "__dir__" in names:
            assert not {"value", "function"} & set(targets.split())
