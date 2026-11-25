# SPDX-License-Identifier: Apache-2.0
"""Review-4 repairs: source-only admission with refused/inert neighbours."""
import pytest

from tests.core.test_change_map_fu2_effects import inventory


@pytest.mark.parametrize("callback,expected", [("configure", False), ("int", True)],
                         ids=["repository-callback", "builtin-key"])
def test_external_callback_policy(tmp_path, monkeypatch, callback, expected):
    source = ("import os\ndef configure(value):\n    os.environ['PATH'] = 'local'\n    return value\n"
              "sorted(['2', '1'], key=" + callback + ")\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


@pytest.mark.parametrize("operation", [
    "reduce(configure, [1, 2])", "map(configure, [1])", "filter(configure, [1])",
    "dependency.call(configure)", "dependency.call(callback=configure)",
    "dependency.call(Settings)", "dependency.call('helper')", "dependency.call('helper.py')",
    "runpy.run_module('helper', run_name='__main__')", "runpy.run_path('helper.py')",
    "importlib.import_module('helper')", "exec('import helper')", "eval('configure(1)')",
    "pkgutil.resolve_name('helper.configure')", "metadata.entry_points()",
], ids=["reduce", "map", "filter", "callback", "keyword-callback", "class", "module-name", "path",
        "run-module", "run-path", "import-module", "exec", "eval", "pkgutil", "entry-points"])
def test_unmodelled_external_execution(tmp_path, monkeypatch, operation):
    source = ("import os\nimport dependency\nimport runpy\nimport importlib\nimport pkgutil\n"
              "from importlib import metadata\nfrom functools import reduce\n"
              "class Settings: pass\ndef configure(*values):\n    os.environ['PATH'] = 'local'\n"
              + operation + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source, "helper.py": "VALUE = 1\n"})
    assert usable is False


@pytest.mark.parametrize("source", [
    "sorted(['2', '1'], key=int)\n",
    "import logging\nlogging.getLogger(__name__)\n",
    "from contextlib import nullcontext\nwith nullcontext(): pass\n",
    "from dataclasses import dataclass\n@dataclass\nclass Settings:\n    value: int = 1\nSettings()\n",
], ids=["sorted-builtin", "module-logger", "nullcontext", "dataclass"])
def test_modelled_external_controls(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
@pytest.mark.parametrize("method", ["__get__", "__set__", "__delete__"], ids=["get", "set", "delete"])
@pytest.mark.parametrize("field", ["Descriptor()", "field(default=Descriptor())"], ids=["value", "field-default"])
def test_dataclass_descriptor_protocol(tmp_path, monkeypatch, inherited, method, field):
    source = ("import os\nfrom dataclasses import dataclass, field\nclass Base:\n    def " + method
              + "(self, *args):\n        os.environ['PATH'] = 'local'\n        return 1\n")
    source += "class Descriptor(Base): pass\n" if inherited else "Descriptor = Base\n"
    source += "@dataclass\nclass Settings:\n    value: int = " + field + "\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("default", ["1", "Inert()"], ids=["literal", "plain-object"])
def test_dataclass_plain_default(tmp_path, monkeypatch, default):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "from dataclasses import dataclass\nclass Inert: pass\n@dataclass\nclass Settings:\n"
                       "    value: int = " + default + "\n",
    })
    assert usable is True


def test_dataclass_class_default_metaclass_descriptor(tmp_path, monkeypatch):
    source = ("import os\nfrom dataclasses import dataclass\nclass Meta(type):\n"
              "    def __get__(cls, instance, owner):\n        os.environ['PATH'] = 'local'\n        return 1\n"
              "class Descriptor(metaclass=Meta): pass\n@dataclass\nclass Settings:\n    value: int = Descriptor\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("hook", ["__getattribute__", "__getattr__"], ids=["getattribute", "getattr"])
@pytest.mark.parametrize("placement", ["direct", "base", "second-base", "meta-base"])
def test_effective_metaclass_lookup(tmp_path, monkeypatch, hook, placement):
    source = ("import os\nclass MetaBase(type):\n    def " + hook
              + "(cls, name):\n        os.environ['PATH'] = 'local'\n")
    source += "class Meta(MetaBase): pass\n" if placement == "meta-base" else "Meta = MetaBase\n"
    source += "class Base(metaclass=Meta): pass\nclass Plain: pass\n"
    bases = {"direct": "metaclass=Meta", "base": "Base", "second-base": "Plain, Base", "meta-base": "Base"}
    source += "class Settings(" + bases[placement] + "):\n    def configure(): pass\nSettings.configure()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("metaclass", [False, True], ids=["absent", "plain-inherited"])
def test_plain_class_lookup(tmp_path, monkeypatch, metaclass):
    source = "class Meta(type): pass\nclass Base(metaclass=Meta): pass\n" if metaclass else "class Base: pass\n"
    source += "class Settings(Base):\n    def configure(): pass\nSettings.configure()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("operation", [
    "items.extend(value)", "items += value", "value * 2", "1 in value", "sorted(value)",
    "set(value)", "dict(value)", "str(value)", "items.extend([value])", "f'{value}'",
    "{value}", "{value: 1}", "left, right = value", "items.extend(iter(value))",
], ids=["extend", "add", "multiply", "membership", "sorted", "set", "dict", "str", "nested-extend",
        "format", "hash-set", "hash-dict", "unpack", "indirect-iterator"])
def test_consumed_repository_protocol(tmp_path, monkeypatch, operation):
    source = ("import os\nclass Values:\n    def __iter__(self):\n        os.environ['PATH'] = 'local'\n"
              "        return iter(())\nvalue = Values()\nitems = []\n" + operation + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("operation", ["items.extend([1, 2])", "items.append(value)", "items.insert(0, value)",
                                      "[1, 2] * 2", "1 in [1, 2]", "str([1, 2])"],
                         ids=["extend-list", "append-object", "insert-object", "repeat", "contains", "convert"])
def test_consumed_builtin_controls(tmp_path, monkeypatch, operation):
    source = "class Values: pass\nvalue = Values()\nitems = []\n" + operation + "\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("definition,call,expected", [
    ("def configure(value=1):\n    return value + 1\n", "configure()", True),
    ("DEFAULT = 1\ndef configure(value=DEFAULT):\n    return value + 1\n", "configure()", True),
    ("def configure(*, value=1):\n    return value + 1\n", "configure()", True),
    ("def configure(value=1, /):\n    return value + 1\n", "configure()", True),
    ("def configure(value=unknown):\n    return value + 1\n", "configure()", False),
    ("def configure(value=1):\n    return value + 1\n", "configure(Values())", False),
    ("def configure(value=1):\n    return value + 1\n", "configure(value=Values())", False),
    ("def configure(value):\n    return value + 1\n", "configure(1)", True),
    ("def configure(value):\n    return value + 1\n", "configure(value=1)", True),
    ("def configure(value=1 + 2):\n    return value + 1\n", "configure()", True),
    ("def configure(value=int()):\n    return value + 1\n", "configure()", True),
    ("DEFAULT = int()\ndef configure(value=DEFAULT):\n    return value + 1\n", "configure()", True),
], ids=["literal", "constant", "keyword-only", "positional-only", "unknown", "explicit-object",
        "keyword-object", "explicit-literal", "keyword-literal", "constant-expression", "builtin-default", "builtin-constant"])
def test_followed_default_binding(tmp_path, monkeypatch, definition, call, expected):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "class Values: pass\n" + definition + call + "\n",
    })
    assert usable is expected


@pytest.mark.parametrize("original,body,expected", [
    ("original", "pass", True), ("original", "os.environ['PATH'] = 'local'", False),
    ("unknown", "pass", False), ("1", "pass", False), ("Settings", "pass", False),
], ids=["inert", "effectful-body", "unknown-original", "literal-original", "class-original"])
def test_wraps_function_identity(tmp_path, monkeypatch, original, body, expected):
    source = ("import os\nfrom functools import wraps\nclass Settings: pass\ndef original(): pass\n@wraps("
              + original + ")\ndef configure():\n    " + body + "\nconfigure()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is expected


def test_nested_wraps_retains_original_body(tmp_path, monkeypatch):
    source = ("from functools import wraps\ndef decorate(function):\n    @wraps(function)\n"
              "    def wrapper():\n        return function()\n    return wrapper\n"
              "@decorate\ndef configure(): pass\nconfigure()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


def test_wraps_async_original_metadata_is_inert(tmp_path, monkeypatch):
    source = ("from functools import wraps\nasync def original(): pass\n"
              "@wraps(original)\ndef configure(): pass\nconfigure()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("postponed", [False, True], ids=["evaluated", "postponed"])
@pytest.mark.parametrize("declaration", [
    "def configure(value: annotation()) -> annotation(): pass\n",
    "class Settings:\n    value: annotation() = 1\n",
    "from dataclasses import dataclass\n@dataclass\nclass Settings:\n    value: annotation() = 1\n",
], ids=["function", "class", "dataclass"])
def test_annotation_evaluation(tmp_path, monkeypatch, postponed, declaration):
    source = "from __future__ import annotations\n" if postponed else ""
    source += "import os\ndef annotation():\n    os.environ['PATH'] = 'local'\n    return int\n" + declaration
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is postponed


@pytest.mark.parametrize("declaration", [
    "def configure(value=annotation()): pass\n", "class Settings:\n    value: int = annotation()\n",
    "from typing import get_type_hints\ndef configure(value: annotation()): pass\nget_type_hints(configure)\n",
], ids=["function-default", "class-default", "type-hints"])
def test_postponed_annotations_keep_evaluated_effects(tmp_path, monkeypatch, declaration):
    source = ("from __future__ import annotations\nimport os\ndef annotation():\n"
              "    os.environ['PATH'] = 'local'\n    return int\n" + declaration)
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("operation", [
    "items.insert(value, 1)", "sorted([1], reverse=value)", "property(value)",
    "logging.getLogger(value)", "os.environ.get(value)", "os.environ['APP_ENV'] = value",
    "field(init=value)", "dataclass(init=value)(Settings)",
    "@dataclass(init=value)\nclass Record:\n    number: int = 1",
    "staticmethod(value)", "classmethod(value)",
], ids=["insert-index", "sort-reverse", "property-accessor", "logger-name", "environment-key", "environment-value",
        "field-flag", "dataclass-flag", "dataclass-decorator-flag", "staticmethod-value", "classmethod-value"])
def test_external_argument_protocol_audit(tmp_path, monkeypatch, operation):
    source = ("import os\nimport logging\nfrom dataclasses import dataclass, field\nclass Settings:\n"
              "    def __bool__(self):\n        os.environ['PATH'] = 'local'\n        return True\n"
              "value = Settings()\nitems = []\n" + operation + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("keyword", ["ids", "scope", "params"])
def test_fixture_callback_policy(tmp_path, monkeypatch, keyword):
    source = ("import os\nimport pytest\ndef configure(*args, **kwargs):\n"
              "    os.environ['PATH'] = 'local'\n    return 'function'\n@pytest.fixture("
              + keyword + "=configure)\ndef value(): return 1\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("placement", ["class", "default"], ids=["class-metaclass", "default-object"])
def test_dataclass_introspection_protocol(tmp_path, monkeypatch, placement):
    source = "import os\nfrom dataclasses import dataclass\n"
    if placement == "class":
        source += ("class Meta(type):\n    def __getattribute__(cls, name):\n"
                   "        os.environ['PATH'] = 'local'\n        return type.__getattribute__(cls, name)\n"
                   "@dataclass\nclass Settings(metaclass=Meta):\n    value: int = 1\n")
    else:
        source += ("class Default:\n    def __getattribute__(self, name):\n"
                   "        os.environ['PATH'] = 'local'\n        return object.__getattribute__(self, name)\n"
                   "@dataclass\nclass Settings:\n    value: int = Default()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("decorator", ["staticmethod", "classmethod", "property"])
@pytest.mark.parametrize("alias", [False, True], ids=["direct", "alias"])
def test_builtin_decorator_metadata_requires_function(tmp_path, monkeypatch, decorator, alias):
    name = "decorate" if alias else decorator
    source = ("import os\nfrom builtins import " + decorator + " as " + name + "\nclass Meta(type):\n"
              "    def __getattribute__(cls, name):\n        os.environ['PATH'] = 'local'\n"
              "        return type.__getattribute__(cls, name)\n@" + name + "\nclass Settings(metaclass=Meta): pass\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False


@pytest.mark.parametrize("declaration", [
    "def configure(value: collect_ignore): pass\n",
    "def configure() -> collect_ignore: pass\n",
    "class Settings:\n    value: collect_ignore = 1\n",
], ids=["argument", "return", "class-field"])
def test_postponed_names_are_not_collection_declarations(tmp_path, monkeypatch, declaration):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "from __future__ import annotations\n" + declaration,
    })
    assert usable is True


@pytest.mark.parametrize("call", ["Settings(Values())", "Settings(value=Values())"], ids=["positional", "keyword"])
def test_implicit_constructor_arguments_do_not_assume_defaults(tmp_path, monkeypatch, call):
    source = ("import os\nclass Values:\n    def __add__(self, other):\n"
              "        os.environ['PATH'] = 'local'\n        return 2\nclass Settings:\n"
              "    def __init__(self, value=1):\n        value + 1\n" + call + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False
