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


@pytest.mark.parametrize("key,expected", [
    ("PATH", False), ("LD_PRELOAD", False), ("APP_ENV", True), ("PYTESTAPP", True),
])
@pytest.mark.parametrize("write", [
    "os.environ[{key!r}] = 'local'",
    "os.environ.setdefault({key!r}, 'local')",
    "os.environ.update({{{key!r}: 'local'}})",
], ids=["store", "setdefault", "update"])
def test_environment_writes_use_existing_runner_key_rule(tmp_path, monkeypatch, key, expected, write):
    _, usable = inventory(tmp_path, monkeypatch, {
        "conftest.py": "import os\n" + write.format(key=key) + "\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is expected


@pytest.mark.parametrize("source", ["from . import helper\n", "from .helper import VALUE\n"],
                         ids=["module", "value"])
def test_root_package_relative_constant_import_keeps_identity(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {
        "__init__.py": source, "helper.py": "VALUE = 2\n", "test_api.py": "def test_call(): pass\n",
    })
    assert usable is True


@pytest.mark.parametrize("body,expected", [("return 2", True), ("pytest.Function.runtest = None", False)],
                         ids=["inert", "imported-write"])
@pytest.mark.parametrize("source", [
    "from . import helper\nhelper.setup()\n", "from .helper import setup\nsetup()\n",
], ids=["module", "callee"])
def test_root_package_relative_calls_follow_helper_effects(tmp_path, monkeypatch, body, expected, source):
    _, usable = inventory(tmp_path, monkeypatch, {
        "__init__.py": source,
        "helper.py": "import pytest\ndef setup():\n    " + body + "\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is expected


@pytest.mark.parametrize("file,source", [
    ("__init__.py", "from .. import helper\n"),
    ("module.py", "from . import helper\n"),
    ("odd-name/__init__.py", "from . import helper\n"),
], ids=["above-root", "no-package", "no-module-name"])
def test_unresolvable_relative_import_is_local_to_its_effect(file, source):
    from g6.core.git_name import GitName
    from g6.workforce._collection_effects import CollectionEffects

    bad, healthy = GitName(file.encode()), GitName(b"healthy.py")
    reader = CollectionEffects.from_trees({
        bad: ast.parse(source), healthy: ast.parse("def setup():\n    return 2\nsetup()\n"),
    })
    assert reader.passive(bad) is False
    assert reader.passive(healthy) is True


@pytest.mark.parametrize("source", ["from . import helper\n", "from .helper import VALUE\n"],
                         ids=["module", "value"])
def test_root_package_relative_import_keeps_invalid_helper_loaded(tmp_path, monkeypatch, source):
    evidence = inventory(tmp_path, monkeypatch, {
        "__init__.py": source, "helper.py": 'print "old"\n', "test_api.py": "def test_call(): pass\n",
    })
    assert evidence[1] is False
    assert evidence[0].not_read == []


def test_root_package_helper_relative_import_keeps_identity(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "__init__.py": "from . import helper\n",
        "helper.py": "from . import values\nVALUE = 2\n",
        "values.py": "VALUE = 2\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is True


@pytest.mark.parametrize("level,expected", [(2, False), (3, True)], ids=["two-levels", "three-levels"])
def test_src_package_relative_import_uses_full_package(tmp_path, monkeypatch, level, expected):
    helper = "from .. import configure\nconfigure()\n" if level == 2 else "from ... import values\nVALUE = values.VALUE\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "__init__.py": "from .src.pkg import helper\n",
        "src/__init__.py": "import os\ndef configure():\n    os.environ['PATH'] = 'local'\n",
        "src/pkg/__init__.py": "",
        "src/pkg/helper.py": helper,
        "values.py": "VALUE = 2\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is expected


@pytest.mark.parametrize("source,expected", [
    ("from .helper import configure\nconfigure()\n", True),
    ("from ..helper import configure\nconfigure()\n", False),
], ids=["same-module-aliases", "different-package-depths"])
def test_relative_import_alternatives_must_agree(tmp_path, monkeypatch, source, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "src/pkg/__init__.py": "",
        "src/pkg/helper.py": "def configure(): pass\n",
        "src/pkg/setup.py": source,
        "test_api.py": "from pkg import setup\n",
    })
    assert usable is expected


@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "both-inert"])
@pytest.mark.parametrize("branches", [
    "if sys.platform == 'win32':\n{first}\nelse:\n{second}\n",
    "try:\n{first}\nexcept Exception:\n{second}\n",
    "while False:\n{first}\nelse:\n{second}\n",
    "for item in ():\n{first}\nelse:\n{second}\n",
    "with nullcontext():\n{first}\n{plain_second}\n",
    "match sys.platform:\n    case 'win32':\n{nested_first}\n    case _:\n{nested_second}\n",
    "{plain_first}\n{plain_second}\n",
], ids=["if", "try", "while", "for", "with", "match", "sequential"])
def test_every_definition_candidate_is_followed(tmp_path, monkeypatch, body, expected, branches):
    first = "def configure():\n    " + body
    second = "def configure():\n    pass"
    def indent(text, spaces):
        return "\n".join(" " * spaces + line for line in text.splitlines())

    source = "import os\nimport sys\nfrom contextlib import nullcontext\n" + branches.format(
        first=indent(first, 4), second=indent(second, 4), plain_first=first, plain_second=second,
        nested_first=indent(first, 8), nested_second=indent(second, 8)) + "configure()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source, "test_api.py": "def test_call(): pass\n"})
    assert usable is expected


@pytest.mark.parametrize("binding", [
    "run = active\nrun = inert\n",
    "from helper import active as run\nfrom helper import inert as run\n",
    "class run:\n    def __init__(self):\n        active()\nclass run:\n    pass\n",
], ids=["assignments", "imports", "classes"])
@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "both-inert"])
def test_repeated_binding_kinds_keep_all_candidates(tmp_path, monkeypatch, binding, body, expected):
    helpers = "import os\ndef active():\n    " + body + "\ndef inert():\n    pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": helpers,
        "test_api.py": "from helper import active, inert\n" + binding + "run()\n",
    })
    assert usable is expected


def test_unavailable_binding_candidate_is_not_discarded(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "def configure(): pass\nconfigure = unknown\nconfigure()\n",
    })
    assert usable is False


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "inert"])
def test_metaclass_prepare_effect_is_followed(tmp_path, monkeypatch, inherited, body, expected):
    source = ("import os\nclass Meta(type):\n    @classmethod\n"
              "    def __prepare__(mcls, name, bases):\n        " + body + "\n        return {}\n")
    source += "class Base(metaclass=Meta): pass\nclass Settings(Base): pass\n" if inherited else (
        "class Settings(metaclass=Meta): pass\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "inert"])
def test_descriptor_set_name_effect_is_followed(tmp_path, monkeypatch, inherited, body, expected):
    source = ("import os\nclass Descriptor:\n    def __set_name__(self, owner, name):\n        " + body + "\n")
    source += "class ChildDescriptor(Descriptor): pass\n" if inherited else ""
    source += "class Settings:\n    value = " + ("ChildDescriptor" if inherited else "Descriptor") + "()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("decorator", ["dataclass", "dataclass()"], ids=["bare", "called"])
@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("self.ready = True", True)],
                         ids=["runner-write", "inert"])
def test_dataclass_post_init_effect_is_followed(tmp_path, monkeypatch, decorator, body, expected):
    source = ("import os\nfrom dataclasses import dataclass\n@" + decorator + "\nclass Settings:\n"
              "    def __post_init__(self):\n        " + body + "\nSettings()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("return 2", True)],
                         ids=["runner-write", "inert"])
def test_dataclass_default_factory_effect_is_followed(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nfrom dataclasses import dataclass, field\ndef make():\n    " + body + "\n"
                       "@dataclass\nclass Settings:\n    value: int = field(default_factory=make)\nSettings()\n",
    })
    assert usable is expected


@pytest.mark.parametrize("instantiate,expected", [(False, True), (True, True)], ids=["creation", "instantiation"])
def test_unmodelled_class_decorator_instantiation_is_unavailable(tmp_path, monkeypatch, instantiate, expected):
    source = "def decorate(cls):\n    return cls\n@decorate\nclass Settings:\n    pass\n"
    source += "Settings()\n" if instantiate else ""
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "both-inert"])
def test_function_scope_keeps_definition_candidates(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\ndef setup():\n    if True:\n        def run():\n            " + body + "\n"
                       "    else:\n        def run(): pass\n    run()\nsetup()\n",
    })
    assert usable is expected


@pytest.mark.parametrize("source,expected", [
    ("@dataclass\nclass Base:\n    def __post_init__(self): pass\nclass Settings(Base):\n"
     "    def __post_init__(self):\n        os.environ['PATH'] = 'local'\nSettings()\n", False),
    ("@dataclass\nclass Settings:\n    value: list = field(default_factory=lambda: [])\nSettings()\n", True),
    ("@dataclass\nclass Settings:\n    value: int = field(default_factory=unknown)\nSettings()\n", False),
], ids=["inherited-post-init", "inert-lambda-factory", "unavailable-factory"])
def test_dataclass_generated_calls_keep_inheritance_and_unknowns(tmp_path, monkeypatch, source, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nfrom dataclasses import dataclass, field\n" + source,
    })
    assert usable is expected


def test_aliased_descriptor_set_name_effect_is_followed(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nclass Descriptor:\n    def __set_name__(self, owner, name):\n"
                       "        os.environ['PATH'] = 'local'\nheld = Descriptor()\n"
                       "class Settings:\n    value = held\n",
    })
    assert usable is False


def test_same_line_descriptor_calls_keep_distinct_values(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nclass Active:\n    def __set_name__(self, owner, name):\n"
                       "        os.environ['PATH'] = 'local'\nclass Inert: pass\n"
                       "active = Active(); inert = Inert()\nclass Settings:\n    value = active\n",
    })
    assert usable is False


def test_imported_descriptor_set_name_effect_is_followed(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": "import os\nclass Descriptor:\n    def __set_name__(self, owner, name):\n"
                     "        os.environ['PATH'] = 'local'\n",
        "test_api.py": "from helper import Descriptor\nclass Settings:\n    value = Descriptor()\n",
    })
    assert usable is False


def test_aliased_dataclass_field_factory_effect_is_followed(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nfrom dataclasses import dataclass, field\ndef make():\n"
                       "    os.environ['PATH'] = 'local'\nheld = field(default_factory=make)\n"
                       "@dataclass\nclass Settings:\n    value: int = held\nSettings()\n",
    })
    assert usable is False


@pytest.mark.parametrize("body,expected", [("os.environ['PATH'] = 'local'", False), ("pass", True)],
                         ids=["runner-write", "inert"])
def test_relative_import_of_root_attribute_is_followed(tmp_path, monkeypatch, body, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "__init__.py": "import os\ndef configure():\n    " + body + "\nfrom . import helper\n",
        "helper.py": "from . import configure\nconfigure()\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is expected


def test_missing_relative_module_is_an_unavailable_effect(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "pkg/__init__.py": "",
        "pkg/helper.py": "from .missing import configure\n",
        "test_api.py": "def test_call(): pass\n",
    })
    assert usable is False


def test_implicit_method_uses_module_scope_for_bare_names(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\ndef configure():\n    os.environ['PATH'] = 'local'\n"
                       "class Settings:\n    @staticmethod\n    def configure(): pass\n"
                       "    def __init__(self):\n        configure()\nSettings()\n",
    })
    assert usable is False


def test_builtin_setter_candidate_is_checked(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nif True:\n    configure = setattr\nelse:\n"
                       "    def configure(*args): pass\nconfigure(os, 'ready', True)\n",
    })
    assert usable is False


def test_destructured_class_value_keeps_descriptor_effect(tmp_path, monkeypatch):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "import os\nclass Descriptor:\n    def __set_name__(self, owner, name):\n"
                       "        os.environ['PATH'] = 'local'\nclass Settings:\n"
                       "    value, count = Descriptor(), 2\n",
    })
    assert usable is False
