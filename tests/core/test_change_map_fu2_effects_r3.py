# SPDX-License-Identifier: Apache-2.0
"""Repair 3 admission controls, added before the reader change."""
import ast

import pytest

from g6.core.git_name import GitName
from g6.workforce._collection_effects import CollectionEffects
from tests.core.test_change_map_fu2_effects import inventory


@pytest.mark.parametrize("source,expected", [
    ("class Settings: pass\nVALUE = Settings\n", True),
    ("class Settings: pass\nVALUE = Settings.value\n", False),
    ("class Settings: pass\nSettings().value = 1\n", False),
    ("VALUE = [1][0]\n", True),
    ("class Settings: pass\nVALUE = Settings[int]\n", False),
    ("class Settings: pass\nVALUE = Settings()[0]\n", False),
    ("class Settings(object): pass\nSettings()\n", True),
    ("from enum import Enum\nclass Settings(Enum):\n    VALUE = 1\n", False),
    ("from external import Base\nclass Settings(Base): pass\n", False),
    ("class Settings:\n    def __init__(self):\n        self.value = 1\nSettings()\n", True),
    ("class Settings:\n    def __setattr__(self, name, value): pass\n"
     "    def __init__(self):\n        self.value = 1\nSettings()\n", False),
    ("VALUE = 1 + 2\n", True),
    ("class Settings: pass\nVALUE = Settings() + 2\n", False),
    ("class Settings: pass\nVALUE = str(Settings())\n", False),
    ("class Settings: pass\nitems = [Settings()]\nvalue = items[0]\nVALUE = value + 2\n", False),
    ("class Settings: pass\nitems = (Settings(),)\nVALUE = items == (1,)\n", False),
    ("with unknown_context(): pass\n", False),
], ids=["class-value", "class-attribute", "instance-store", "literal-subscript", "class-subscript",
        "instance-subscript", "object-base", "enum-base", "external-base", "plain-init",
        "custom-setattr", "literal-operation", "instance-operation", "builtin-conversion", "indexed-operator", "tuple-operator",
        "unknown-context"])
def test_operation_admission(tmp_path, monkeypatch, source, expected):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("importer,expected", [
    ("import helper\n", True),
    ("from helper import VALUE\n", False),
    ("from helper import *\n", False),
], ids=["module", "lazy-name", "star"])
def test_import_lookup_admission(tmp_path, monkeypatch, importer, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": "def __getattr__(name):\n    return 1\n",
        "test_api.py": importer,
    })
    assert usable is expected


@pytest.mark.parametrize("redirect", ["global", "nonlocal"])
@pytest.mark.parametrize("body,expected", [("pass", True), ("os.environ['PATH'] = 'local'", False)],
                         ids=["inert", "runner-write"])
@pytest.mark.parametrize("calls", ["install()\nconfigure()\n", "configure()\ninstall()\nconfigure()\n", "configure()\ninstall()\n"],
                         ids=["install-first", "cached-first", "candidate-added-later"])
def test_redirected_candidates(tmp_path, monkeypatch, redirect, body, expected, calls):
    source = ("def active():\n    " + body + "\ndef configure(): pass\ndef install():\n    "
              + redirect + " configure\n    configure = active\n" + calls)
    if redirect == "nonlocal":
        source = "def outer():\n" + "\n".join("    " + line for line in source.splitlines()) + "\nouter()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": "import os\n" + source})
    assert usable is expected


@pytest.mark.parametrize("body,expected", [("pass", True), ("os.environ['PATH'] = 'local'", False)],
                         ids=["inert", "runner-write"])
@pytest.mark.parametrize("returned", ["function", "class"], ids=["wrapper", "replacement-class"])
def test_decorator_returned_binding(tmp_path, monkeypatch, body, expected, returned):
    definition = ("    def replacement():\n        " + body + "\n" if returned == "function" else
                  "    class replacement:\n        def __init__(self):\n            " + body + "\n")
    source = ("import os\ndef decorate(function):\n" + definition + "    return replacement\n"
              "@decorate\ndef setup(): pass\nsetup()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("returned,expected", [("cls", True), ("dict", False)], ids=["identity", "unknown-result"])
def test_class_decorator_result(tmp_path, monkeypatch, returned, expected):
    source = ("def decorate(cls):\n    return " + returned + "\n@decorate\nclass Settings: pass\nSettings()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("ambiguous", [False, True], ids=["one-portion", "two-portions"])
def test_namespace_portions(tmp_path, monkeypatch, ambiguous):
    files = {"one/ns/helper.py": "VALUE = 1\n", "one/ns/setup.py": "from .helper import VALUE\n",
             "test_api.py": "from ns import setup\n"}
    if ambiguous:
        files["two/ns/other.py"] = "VALUE = 2\n"
    _, usable = inventory(tmp_path, monkeypatch, files, roots=("one", "two"))
    assert usable is (not ambiguous)


def test_namespace_external_root_stays_unavailable(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "ns/helper.py": "VALUE = 1\n", "ns/setup.py": "from .helper import VALUE\n",
    }, roots=("../outside",))
    assert usable is False


def test_unknown_ast_kind_is_not_inert():
    class FutureStatement(ast.stmt):
        _fields = ()

    file = GitName(b"test_api.py")
    reader = CollectionEffects.from_trees({file: ast.Module(body=[FutureStatement()], type_ignores=[])})
    assert reader.passive(file) is False


@pytest.mark.parametrize("redirect", ["global", "nonlocal"])
def test_alias_after_redirect_keeps_new_candidate(tmp_path, monkeypatch, redirect):
    source = ("def active():\n    os.environ['PATH'] = 'local'\ndef configure(): pass\n"
              "def install():\n    " + redirect + " configure\n    configure = active\n"
              "install()\nrun = configure\nrun()\n")
    if redirect == "nonlocal":
        source = "def outer():\n" + "\n".join("    " + line for line in source.splitlines()) + "\nouter()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": "import os\n" + source})
    assert usable is False


def test_identity_wrapper_calls_original_once(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "def decorate(function):\n    def wrapper():\n        return function()\n"
                       "    return wrapper\n@decorate\ndef setup(): pass\nsetup()\n",
    })
    assert usable is True


@pytest.mark.parametrize("body,expected", [("pass", True), ("os.environ['PATH'] = 'local'", False)],
                         ids=["inert", "runner-write"])
def test_dataclass_generated_attribute_hook(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nfrom dataclasses import dataclass\n@dataclass\nclass Settings:\n"
                       "    value: int = 1\n    def __setattr__(self, name, value):\n        " + body + "\nSettings()\n",
    })
    assert usable is expected


@pytest.mark.parametrize("instantiate", [False, True], ids=["creation", "instantiation"])
def test_testcase_external_constructor_is_not_assumed_inert(tmp_path, monkeypatch, instantiate):
    source = "from unittest import TestCase\nclass Settings(TestCase): pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source + ("Settings()\n" if instantiate else "")})
    assert usable is (not instantiate)


def test_builtin_setter_on_repository_instance_is_unavailable(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "class Settings: pass\nsettings = Settings()\nsetattr(settings, 'value', 1)\n",
    })
    assert usable is False
