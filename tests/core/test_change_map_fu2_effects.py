# SPDX-License-Identifier: Apache-2.0
"""Visible collection effects, without running repository code in the reader."""
import ast
import json

import pytest

from g6.workforce import repo_tests


def inventory(tmp_path, monkeypatch, files, *, roots=(), selections=()):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(repo_tests, "_git", lambda *a, **k: "\0".join(files).encode() + b"\0")
    return repo_tests._collection_evidence(tmp_path, roots=roots, selections=selections)


@pytest.mark.parametrize("source", [
    "import pytest\n@pytest.fixture\ndef two():\n    return 2\n",
    "class Settings:\n    def __init__(self):\n        self.debug = False\nSETTINGS = Settings()\n",
    "from typing import NamedTuple\nclass Case(NamedTuple):\n    want: int\nCASES = [Case(2)]\n",
    "def setup():\n    pass\nrun = setup\nrun()\n",
    "import os\nos.environ['APP_ENV'] = 'test'\n",
    "from os import environ as env\nenv.setdefault('app_mode', 'test')\n",
    "import os\nos.environ.update({'APP_ENV': 'test'}, APP_MODE='local')\n",
    "import os\nMODE = os.environ.get('APP_ENV', 'test')\n",
])
def test_visible_inert_import_effects_keep_identity(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source, "test_api.py": "def test_call(): pass\n"})
    assert usable is True


@pytest.mark.parametrize("source", [
    "def pytest_collection_modifyitems(items):\n    items.clear()\n",
    "import pytest\n@pytest.fixture(autouse=True)\ndef automatic():\n    pass\n",
    "import pytest\nflag = False\n@pytest.fixture(autouse=flag)\ndef automatic():\n    pass\n",
    "collect_ignore = ['test_api.py']\n",
    "collect_ignore_glob = ['test_*.py']\n",
    "pytest_plugins = ['plugin']\n",
    "from helper import hook as pytest_collection_modifyitems\n",
    "import pytest\ndef setup():\n    pytest.Function.runtest = lambda self: None\nsetup()\n",
    "import os\ndef setup():\n    os.path.answer = 2\nsetup()\n",
    "import os\nos.environ['PYTEST_ADDOPTS'] = '-k other'\n",
    "import os\nos.environ.update({'pythonpath': 'other'})\n",
    "import os\nkey = 'APP_ENV'\nos.environ[key] = 'test'\n",
    "import os\nos.environ.clear()\n",
    "import os\nos.environ = {}\n",
    "import os\nenv = getattr(os, 'environ')\nenv['APP_ENV'] = 'test'\n",
    "def recur():\n    recur()\nrecur()\n",
    "def dynamic(callback):\n    callback()\ndynamic(print)\n",
    "def broken(:\n",
])
def test_collection_changing_neighbours_lose_identity(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source, "test_api.py": "def test_call(): pass\n"})
    assert usable is False


@pytest.mark.parametrize("name,source", [
    ("pytest.ini", "[pytest]\nminversion = 6.0\nmarkers = slow: a slow check\n"),
    ("tox.ini", "[pytest]\nfilterwarnings = ignore::DeprecationWarning\n"),
    ("setup.cfg", "[tool:pytest]\nxfail_strict = true\nlog_level = INFO\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\nminversion = "6.0"\n'),
    ("pytest.toml", '[pytest]\nminversion = "6.0"\n'),
    (".pytest.toml", '[pytest]\nmarkers = ["slow: a slow check"]\n'),
])
def test_passive_configuration_keeps_identity(tmp_path, monkeypatch, name, source):
    _, usable = inventory(tmp_path, monkeypatch, {name: source, "test_api.py": "def test_call(): pass\n"})
    assert usable is True


@pytest.mark.parametrize("option", ["python_files = checks.py", "addopts = -k other", "unknown_key = yes"])
def test_unknown_or_collection_configuration_loses_identity(tmp_path, monkeypatch, option):
    _, usable = inventory(tmp_path, monkeypatch, {"pytest.ini": "[pytest]\n" + option + "\n",
                                               "test_api.py": "def test_call(): pass\n"})
    assert usable is False


@pytest.mark.parametrize("body,expected", [
    ("HANDLERS.append(function)\n    return function", True),
    ("pytest.Function.runtest = function\n    return function", False),
    ("return register(function)", False),
])
def test_imported_decorator_effects_are_followed(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "lib/registry.py": "import pytest\nHANDLERS = []\ndef register(function):\n    " + body + "\n",
        "test_api.py": "from registry import register\n@register\ndef test_call(): pass\n",
    }, roots=("lib",))
    assert usable is expected


@pytest.mark.parametrize("body,expected", [("return 2", True), ("pytest.Function.runtest = None", False)])
def test_transitive_imported_calls_are_followed(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "pkg/__init__.py": "",
        "pkg/helper.py": "import pytest\ndef leaf():\n    " + body + "\n",
        "pkg/setup.py": "from .helper import leaf\ndef setup():\n    return leaf()\n",
        "test_api.py": "from pkg.setup import setup\nsetup()\ndef test_call(): pass\n",
    })
    assert usable is expected


@pytest.mark.parametrize("method", ["__init__", "__new__", "__init_subclass__"])
def test_class_creation_effects_lose_identity(tmp_path, monkeypatch, method):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import pytest\nclass Base:\n    def " + method + "(self):\n"
                       "        pytest.Function.runtest = None\nclass Child(Base):\n    pass\nChild()\n",
    })
    assert usable is False


def test_unimported_unparsable_script_is_named_not_read(tmp_path, monkeypatch):
    files = {"examples/legacy.py": 'print "hello"\n', "test_api.py": "def test_call(): pass\n"}
    evidence = inventory(tmp_path, monkeypatch, files)
    assert evidence[1] is True
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(tmp_path), "exit": 5, "nodes": []}), encoding="utf-8")
    answer = repo_tests._read_collection(report, 5, tmp_path, evidence=evidence)
    assert answer["not_read"] == [{"file": "examples/legacy.py", "reason": "source does not parse and is not loaded by collection"}]


@pytest.mark.parametrize("importer", ["import examples.legacy", "from examples import legacy"])
def test_imported_unparsable_script_loses_identity(tmp_path, monkeypatch, importer):
    _, usable = inventory(tmp_path, monkeypatch, {"examples/legacy.py": 'print "hello"\n',
                                               "test_api.py": importer + "\n"})
    assert usable is False


def test_unresolved_imported_call_still_refused():
    assert not repo_tests._collection_source_passive(ast.parse("from api import setup\nsetup()\n"), {"api"})


@pytest.mark.parametrize("source,expected", [
    ("class Setup:\n    @staticmethod\n    def run():\n        return 2\nSetup.run()\n", True),
    ("import pytest\nclass Setup:\n    @staticmethod\n    def run():\n        pytest.Function.runtest = None\nSetup.run()\n", False),
    ("def factory():\n    return lambda function: function\n@factory()\ndef test_call(): pass\n", True),
    ("def factory():\n    return\n@factory()\ndef test_call(): pass\n", False),
    ("import pytest\ndef factory():\n    def decorate(function):\n        pytest.Function.runtest = function\n"
     "        return function\n    return decorate\n@factory()\ndef test_call(): pass\n", False),
    ("import pytest\nclass Meta(type):\n    def __new__(cls, name, bases, attrs):\n"
     "        pytest.Function.runtest = None\nclass Settings(metaclass=Meta):\n    pass\n", False),
    ("import pytest\nclass Meta(type):\n    def __call__(cls):\n"
     "        pytest.Function.runtest = None\nclass Settings(metaclass=Meta):\n    pass\nSettings()\n", False),
])
def test_visible_class_and_decorator_callees(tmp_path, monkeypatch, source, expected):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


def test_call_depth_is_bounded(tmp_path, monkeypatch):
    source = "\n".join(f"def step{i}():\n    return step{i + 1}()" for i in range(40))
    source += "\ndef step40():\n    return 2\nstep0()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("name,source", [
    ("pytest.ini", "[pytest\nminversion = 6.0\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\nminversion = {bad = "value"}\n'),
    ("pytest.ini", "[pytest]\nxfail_strict = maybe\n"),
])
def test_unreadable_configuration_loses_identity(tmp_path, monkeypatch, name, source):
    _, usable = inventory(tmp_path, monkeypatch, {name: source, "test_api.py": "def test_call(): pass\n"})
    assert usable is False


@pytest.mark.parametrize("broken,test_file", [("test_broken.py", "test_api.py"), ("pkg/__init__.py", "pkg/test_api.py")])
def test_unparsable_collection_source_loses_identity(tmp_path, monkeypatch, broken, test_file):
    _, usable = inventory(tmp_path, monkeypatch, {broken: "def broken(:\n", test_file: "def test_call(): pass\n"})
    assert usable is False


def test_unparsable_explicit_file_loses_identity(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {"checks.py": "def broken(:\n"}, selections=("checks.py::test_call",))
    assert usable is False
