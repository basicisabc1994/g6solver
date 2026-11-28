# SPDX-License-Identifier: Apache-2.0
"""Repair-1 route regressions, written before changing the source reader.

The no-lane ruling forbids running these here. Expectations describe collection
effects, without executing any of the repository snippets in this interpreter.
"""
import ast

import pytest

from g6.workforce._collection_effects import CollectionEffects
from tests.core.test_change_map_fu2_effects import inventory


PRELUDE = "import os\ndef install():\n    os.environ['PATH'] = 'local'\n"


@pytest.mark.parametrize("hook", [
    "__repr__", "__str__", "__eq__", "__hash__", "__iter__", "__len__", "__bool__",
    "__del__", "__dir__", "__get__", "__set__", "__delete__", "__enter__", "__exit__",
    "__call__", "__class_getitem__",
])
@pytest.mark.parametrize("present", [False, True], ids=["plain", "unfollowed"])
def test_unfollowed_instance_protocol(tmp_path, monkeypatch, hook, present):
    body = "    def " + hook + "(self, *args):\n        return 1\n" if present else "    pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "class Value:\n" + body + "held = Value()\n",
    })
    assert usable is (not present)


@pytest.mark.parametrize("placement", ["subclass", "mixin", "set-name", "class-store"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_hook_store_uses_receiving_class(tmp_path, monkeypatch, placement, effect):
    store = "install()" if effect else "pass"
    if placement == "set-name":
        source = ("class Base:\n    def __set_name__(self, owner, name):\n        self.name = name\n"
                  "class Child(Base):\n    def __setattr__(self, name, value):\n        " + store
                  + "\nclass Settings:\n    value = Child()\n")
    elif placement == "class-store":
        source = ("class Meta(type):\n    def __setattr__(cls, name, value):\n        " + store
                  + "\nclass Base:\n    def __init_subclass__(cls):\n        cls.ready = True\n"
                    "class Child(Base, metaclass=Meta): pass\n")
    else:
        source = "class Base:\n    def __init__(self):\n        self.value = 1\n"
        if placement == "mixin":
            source += "class Mixin:\n    def __setattr__(self, name, value):\n        " + store + "\n"
            source += "class Child(Base, Mixin): pass\n"
        else:
            source += "class Child(Base):\n    def __setattr__(self, name, value):\n        " + store + "\n"
        source += "Child()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": PRELUDE + source})
    assert usable is (not effect)


@pytest.mark.parametrize("hook", ["__getattr__", "__getattribute__"])
@pytest.mark.parametrize("route", ["bound", "imported", "test-class", "parametrize", "decorator"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_pytest_lookup_routes(tmp_path, monkeypatch, hook, route, effect):
    body = "install()\n        return 1" if effect else "return 1"
    if route == "decorator":
        meta_body = "    def " + hook + "(cls, name):\n        " + body + "\n" if effect else "    pass\n"
        source = (PRELUDE + "class Meta(type):\n" + meta_body
                  + "class Registry(metaclass=Meta):\n    def register(function):\n        return function\n"
                    "@Registry.register\ndef setup(): pass\n")
        files = {"conftest.py": source}
    else:
        source = PRELUDE + "class Value:\n    def " + hook + "(self, name):\n        " + body + "\n"
        if route == "bound":
            files = {"conftest.py": source + "held = Value()\n"}
        elif route == "imported":
            files = {"helper.py": source + "held = Value()\n", "test_api.py": "from helper import held\n"}
        elif route == "test-class":
            files = {"test_api.py": source + "class TestAPI(Value):\n    def test_call(self): pass\n"}
        else:
            files = {"test_api.py": source + "import pytest\n@pytest.mark.parametrize('value', [Value()])\n"
                     "def test_call(value): pass\n"}
    _, usable = inventory(tmp_path, monkeypatch, files)
    assert usable is (not effect)


@pytest.mark.parametrize("alias", [False, True], ids=["direct", "aliased"])
def test_lookup_cannot_substitute_an_unfollowed_callable(tmp_path, monkeypatch, alias):
    source = (PRELUDE + "class Meta(type):\n    def __getattribute__(cls, name):\n        return install\n"
              "class Registry(metaclass=Meta):\n    def configure(): pass\n")
    source += "run = Registry.configure\nrun()\n" if alias else "Registry.configure()\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is False


@pytest.mark.parametrize("hook,arguments,result", [
    ("__getattr__", "name", "1"), ("__dir__", "", "[]"),
])
@pytest.mark.parametrize("file", ["conftest.py", "test_api.py", "helper.py"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
def test_module_lookup_only_on_executed_routes(tmp_path, monkeypatch, hook, arguments, result, file, effect):
    body = "    install()\n" if effect else ""
    files = {file: PRELUDE + "def " + hook + "(" + arguments + "):\n" + body + "    return " + result + "\n"}
    if file == "helper.py":
        files["test_api.py"] = "import helper\n"
    _, usable = inventory(tmp_path, monkeypatch, files)
    assert usable is (not effect or file == "helper.py" and hook == "__dir__")


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
@pytest.mark.parametrize("missing", [False, True], ids=["declared", "missing"])
def test_helper_module_attribute_dispatch(tmp_path, monkeypatch, effect, missing):
    source = PRELUDE + "VALUE = 1\ndef __getattr__(name):\n"
    source += "    install()\n" if effect else ""
    source += "    return 1\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": source,
        "test_api.py": "import helper\nstr(helper." + ("missing" if missing else "VALUE") + ")\n",
    })
    assert usable is (not effect)


@pytest.mark.parametrize("route", ["dataclass-base", "annotation", "with-target", "new-result", "field-set-name"])
@pytest.mark.parametrize("effect", [False, True], ids=["plain", "protocol"])
def test_class_value_consumers(tmp_path, monkeypatch, route, effect):
    source = PRELUDE
    if route == "new-result":
        source += ("class Base:\n    def __new__(cls):\n        return object.__new__(Child)\n"
                   "class Child(Base):\n    def __init__(self):\n        install()\nBase()\n" if effect else
                   "class Base:\n    def __new__(cls):\n        return object.__new__(cls)\nBase()\n")
    else:
        hook = {"dataclass-base": "__get__", "annotation": "__eq__", "with-target": "__iter__",
                "field-set-name": "__set_name__"}[route]
        source += "class Value:\n" + ("    def " + hook + "(self, *args):\n        install()\n" if effect else "    pass\n")
        source += {
            "dataclass-base": "from dataclasses import dataclass\nclass Base:\n    value = Value()\n@dataclass\nclass Child(Base):\n    value: int\n",
            "annotation": "from typing import NamedTuple\nclass Record(NamedTuple):\n    value: Value()\n",
            "with-target": "from contextlib import nullcontext\nwith nullcontext(Value()) as (value,): pass\n" if effect else
                           "from contextlib import nullcontext\nwith nullcontext((1,)) as (value,): pass\n",
            "field-set-name": "from dataclasses import field\nclass Settings:\n    value = field(default=Value())\n",
        }[route]
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("allocated", ["Descriptor()", "object.__new__(Descriptor)"])
def test_inherited_dataclass_default_read(tmp_path, monkeypatch, allocated):
    source = (PRELUDE + "from dataclasses import dataclass\nclass Descriptor:\n"
              "    def __get__(self, instance, owner):\n        install()\n        return 1\n"
              "class Base:\n    value = " + allocated + "\n@dataclass\nclass Child(Base):\n    value: int\n")
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is False


def test_native_allocation_still_has_finalizer_protocol(tmp_path, monkeypatch):
    source = PRELUDE + "class Value:\n    def __del__(self):\n        install()\nobject.__new__(Value)\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is False


def test_metaclass_call_cannot_hide_descriptor_identity(tmp_path, monkeypatch):
    source = (PRELUDE + "class Descriptor:\n    def __set_name__(self, owner, name):\n        install()\n"
              "class Meta(type):\n    def __call__(cls):\n        return Descriptor()\n"
              "class Factory(metaclass=Meta): pass\nclass Settings:\n    value = Factory()\n")
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is False


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
@pytest.mark.parametrize("call", ["Settings.apply(install)", "Settings.apply(hook=install)", "Settings.apply()"])
def test_classmethod_argument_offset(tmp_path, monkeypatch, effect, call):
    source = ("import os\ndef install():\n    " + ("os.environ['PATH'] = 'local'" if effect else "pass")
              + "\ndef noop(): pass\nclass Settings:\n    @classmethod\n    def apply(cls, hook=noop):\n        hook()\n"
              + call + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is (not effect or call == "Settings.apply()")


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
@pytest.mark.parametrize("aliased", [False, True], ids=["direct", "aliased"])
def test_bound_method_argument_offset(tmp_path, monkeypatch, effect, aliased):
    source = ("import os\ndef install():\n    " + ("os.environ['PATH'] = 'local'" if effect else "pass")
              + "\ndef noop(): pass\nclass Settings:\n    def apply(self, hook=noop):\n        hook()\n"
                "settings = Settings()\n")
    source += "run = settings.apply\nrun(install)\n" if aliased else "settings.apply(install)\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("effect", [False, True], ids=["plain", "descriptor"])
def test_test_instance_attribute_read(tmp_path, monkeypatch, effect):
    source = PRELUDE + "class TestAPI:\n"
    source += "    @property\n    def client(self):\n        install()\n" if effect else "    client = 1\n"
    source += "    def test_call(self): pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("collected", [False, True], ids=["creation", "collected-method"])
def test_unittest_instantiation_route(tmp_path, monkeypatch, collected):
    source = "from unittest import TestCase\nclass Settings(TestCase):\n"
    source += "    def test_call(self): pass\n" if collected else "    pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("effect", [False, True], ids=["inert", "effect"])
@pytest.mark.parametrize("shape", ["class", "lambda", "comprehension"])
def test_class_lexical_scope(tmp_path, monkeypatch, effect, shape):
    outer = "install" if effect else "noop"
    source = PRELUDE + "def noop(): return 1\nhook = " + outer + "\nclass Outer:\n    hook = noop\n"
    source += {
        "class": "    class Inner:\n        hook()\n",
        "lambda": "    run = lambda: hook()\n    run()\n",
        "comprehension": "    values = [hook() for item in (1,)]\n",
    }[shape]
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("effect", [False, True], ids=["plain", "protocol"])
@pytest.mark.parametrize("loop", ["for number in (1, 2)", "while False"])
def test_loop_rechecks_mutated_container(tmp_path, monkeypatch, effect, loop):
    source = PRELUDE + ("class Value:\n    def __repr__(self):\n        install()\n" if effect else "")
    source += "items = []\n" + loop + ":\n    str(items)\n    items.append(" + ("Value()" if effect else "1") + ")\n"
    _, usable = inventory(tmp_path, monkeypatch, {"conftest.py": source})
    assert usable is (not effect)


SAFE_CALLS = [
    "os.path.dirname('a/b')", "os.path.join('a', 'b')", "os.path.abspath(__file__)",
    "os.path.basename('a/b')", "os.path.exists('a')", "re.compile('a')", "typing.TypeVar('T')",
    "collections.namedtuple('Point', 'x y')", "typing.NamedTuple('Point', [('x', int)])",
    "datetime.datetime(2020, 1, 1)", "datetime.date(2020, 1, 1)", "datetime.timedelta(days=1)",
]


@pytest.mark.parametrize("expression", SAFE_CALLS)
@pytest.mark.parametrize("repository_argument", [False, True], ids=["literal", "repository-callable"])
def test_installed_call_argument_policy(tmp_path, monkeypatch, expression, repository_argument):
    if repository_argument:
        expression = expression.replace("(", "(callback, ", 1)
    source = ("import os\nimport re\nimport typing\nimport collections\nimport datetime\n"
              "def callback(): pass\nVALUE = " + expression + "\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not repository_argument)


@pytest.mark.parametrize("annotation", ["Optional[int]", "List[int]", "Dict[str, int]", "Union[int, str]"])
@pytest.mark.parametrize("repository_type", [False, True], ids=["installed", "repository"])
def test_installed_typing_subscript(annotation, repository_type):
    if repository_type:
        annotation = annotation.replace("int", "Value")
    source = ("from typing import Optional, List, Dict, Union\nclass Value: pass\n"
              "def helper(value: " + annotation + "): pass\n")
    reader = CollectionEffects.from_trees({"helper.py": ast.parse(source)})
    assert reader.passive("helper.py") is (not repository_type)


@pytest.mark.parametrize("base", ["unittest.TestCase", "Check"])
def test_honest_installed_base_sibling_is_exercised(tmp_path, base):
    """An installed constructor alone must not discard a collected sibling."""
    from tests._review_cmfu3.helpers import HONEST_API, scenario

    source = ("import unittest\nfrom unittest import TestCase as Check\nfrom api import neighbour\n"
              "class TestAPI(" + base + "):\n    def test_call(self):\n        assert neighbour() == 2\n")
    found = scenario({"root": str(tmp_path), "api": HONEST_API, "files": {"test_api.py": source}})
    assert found["exit"] == 0 and "1 passed" in found["tail"][0], found
    assert found["authority"] is True, found
    assert found["sibling"]["collection"] == "available", found
    assert found["sibling"]["exercised"] is True, found


@pytest.mark.parametrize("inherited", [False, True], ids=["direct", "inherited"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert", "unfollowed"])
def test_installed_base_constructor_dispatch(tmp_path, monkeypatch, inherited, effect):
    """Installed bodies still dispatch to the repository's receiving class."""
    body = "eval('1')" if effect else "pass"
    owner = "Base" if inherited else "TestAPI"
    source = ("from unittest import TestCase as Check\nclass " + owner + "(Check):\n"
              "    def addTypeEqualityFunc(self, typeobj, function):\n        " + body + "\n")
    if inherited:
        source += "class TestAPI(Base):\n"
    source += "    def test_call(self): pass\n"
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not effect)


def test_installed_base_subclass_hook_stays_unavailable(tmp_path, monkeypatch):
    """A repository hook is read from its own body, even behind a library base."""
    source = ("from unittest import TestCase as Check\nclass Base(Check):\n"
              "    def __init_subclass__(cls):\n        eval('1')\n"
              "class TestAPI(Base):\n    def test_call(self): pass\n")
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is False
