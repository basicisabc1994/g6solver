"""Review 1 of claude/change-map-fu-3 (1933925d), Claude standing in for a stopped Codex review.

Every case writes a small repository on a real disk, lets real pytest collect it in a child
interpreter (``--collect-only``: no test body runs), and records whether repository code wrote
PATH while pytest collected. The reader's answer for the same files is then taken from
``repo_tests._collection_evidence``. A finding is a case where repository code ran at collection
and the reader still read the collection certain. No git write, network or model call is made
(one test reads the parent commit's reader with ``git show``).
"""
import ast
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from g6.workforce import repo_tests

TREE = Path(__file__).resolve().parents[2]
NODE = 'test_api.py::test_call'
CLASS_NODE = 'test_api.py::TestAPI::test_call'
TEST = 'def test_call():\n    pass\n'
# ``install`` is the visible effect; ``noop`` is its inert neighbour.
PRELUDE = ("import os\n"
           "def install(*args, **kwargs):\n    os.environ['PATH'] = 'local'\n    return 1\n"
           "def noop(*args, **kwargs):\n    return 1\n")
CHILD = ("import os, sys, pytest\n"
         "code = pytest.main(sys.argv[1:])\n"
         "print('REVIEW-PATH-' + ('WRITTEN' if os.environ.get('PATH') == 'local' else 'KEPT'))\n"
         "sys.exit(int(code))\n")


def collected(tmp_path, monkeypatch, files, node=NODE):
    """(repository code wrote PATH during real collection, the reader read the collection certain)."""
    root = tmp_path / 'repo'
    files = {'test_api.py': TEST, **files}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')
    ran = subprocess.run(
        [sys.executable, '-B', '-c', CHILD, '--collect-only', '-q', '-p', 'no:cacheprovider',
         '--confcutdir', str(root), '--rootdir', str(root), '--basetemp', str(tmp_path / 'plain')],
        cwd=root, env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=60)
    lines = ran.stdout.splitlines()
    assert ran.returncode == 0 and node in lines, ran.stdout + ran.stderr
    monkeypatch.setattr(repo_tests, '_git', lambda *a, **k: '\0'.join(files).encode() + b'\0')
    sources, authority = repo_tests._collection_evidence(root)
    assert set(sources) == set(files), sorted(sources)
    return 'REVIEW-PATH-WRITTEN' in lines, authority


def unseen(tmp_path, monkeypatch, files, node=NODE):
    wrote, authority = collected(tmp_path, monkeypatch, files, node)
    assert wrote is True, 'precondition: repository code writes PATH while real pytest collects'
    assert authority is False, 'repository code ran at collection, yet the reader read the collection certain'


def conftest(body):
    return {'conftest.py': PRELUDE + body}


# --------------------------------------------------------------------------- must-close

STORES = {
    # control: the class that defines __init__ also defines __setattr__ (read unavailable today)
    'control': ('class Child:\n    def __init__(self):\n        self.value = 1\n'
                '    def __setattr__(self, name, value):\n        install()\nChild()\n'),
    'subclass-setattr': ('class Base:\n    def __init__(self):\n        self.value = 1\n'
                         'class Child(Base):\n    def __setattr__(self, name, value):\n        install()\nChild()\n'),
    'subclass-descriptor': ('class Stored:\n    def __set__(self, instance, value):\n        install()\n'
                            'class Base:\n    def __init__(self):\n        self.value = 1\n'
                            'class Child(Base):\n    value = Stored()\nChild()\n'),
    'mixin-setattr': ('class Mixin:\n    def __setattr__(self, name, value):\n        install()\n'
                      'class Base:\n    def __init__(self):\n        self.value = 1\n'
                      'class Child(Base, Mixin):\n    pass\nChild()\n'),
    'init-subclass-metaclass': ('class Meta(type):\n    def __setattr__(cls, name, value):\n        install()\n'
                                '        type.__setattr__(cls, name, value)\n'
                                'class Base:\n    def __init_subclass__(cls):\n        cls.registered = True\n'
                                'class Child(Base, metaclass=Meta):\n    pass\n'),
    'set-name-subclass': ('class Named:\n    def __set_name__(self, owner, name):\n        self.name = name\n'
                          'class Loud(Named):\n    def __setattr__(self, name, value):\n        install()\n'
                          'class Settings:\n    value = Loud()\n'),
}


@pytest.mark.parametrize('shape', list(STORES))
def test_store_in_an_inherited_hook_reaches_the_receiving_class(tmp_path, monkeypatch, shape):
    """_instance_store asks the class that defines the method, not the class of the object stored to."""
    unseen(tmp_path, monkeypatch, conftest(STORES[shape]))


CLASSMETHODS = {
    # control: the same call through a staticmethod binds ``hook`` to ``install`` (unavailable today)
    'control': ('class Settings:\n    @staticmethod\n    def apply(hook=noop):\n        hook()\n'
                'Settings.apply(install)\n'),
    'callback': ('class Settings:\n    @classmethod\n    def apply(cls, hook=noop):\n        hook()\n'
                 'Settings.apply(install)\n'),
    'operand': ('class Value:\n    def __add__(self, other):\n        return install()\n'
                'class Settings:\n    @classmethod\n    def configure(cls, value=1):\n        return value + 1\n'
                'Settings.configure(Value())\n'),
}


@pytest.mark.parametrize('shape', list(CLASSMETHODS))
def test_classmethod_call_binds_arguments_after_cls(tmp_path, monkeypatch, shape):
    """_arguments zips the call's arguments onto ``cls``; the real parameter keeps its inert default."""
    unseen(tmp_path, monkeypatch, conftest(CLASSMETHODS[shape]))


DESCRIPTOR = 'class Descriptor:\n    def __set_name__(self, owner, name):\n        install()\nclass Plain:\n    pass\n'
NESTED = {
    # control: nothing in Outer shadows the module name (unavailable today)
    'control': 'hook = install\nclass Outer:\n    class Inner:\n        hook()\n',
    'call': 'hook = install\nclass Outer:\n    hook = noop\n    class Inner:\n        hook()\n',
    'descriptor': DESCRIPTOR + 'Field = Descriptor\nclass Outer:\n    Field = Plain\n    class Inner:\n        value = Field()\n',
    'base': ('class Base:\n    def __init_subclass__(cls):\n        install()\n'
             'class Outer:\n    Base = object\n    class Inner:\n        class Leaf(Base):\n            pass\n'),
    'decorator': ('def register(function):\n    install()\n    return function\n'
                  'class Outer:\n    def register(function):\n        return function\n'
                  '    class Inner:\n        @register\n        def method(self):\n            pass\n'),
    'factory-lambda': ('from dataclasses import dataclass, field\nhook = install\n'
                       '@dataclass\nclass Outer:\n    hook = noop\n'
                       '    value: int = field(default_factory=lambda: hook())\nOuter()\n'),
}


@pytest.mark.parametrize('shape', list(NESTED))
def test_nested_class_body_does_not_see_the_enclosing_class(tmp_path, monkeypatch, shape):
    """A class body nested in a class, and a lambda in a class body, resolve names in the module."""
    unseen(tmp_path, monkeypatch, conftest(NESTED[shape]))


LAZY = 'class Lazy:\n    def __getattr__(self, name):\n        install()\n        raise AttributeError(name)\n'
EAGER = ('class Lazy:\n    def __getattribute__(self, name):\n        install()\n'
         '        return object.__getattribute__(self, name)\n')
LOOKUPS = {
    # control: the same hook called where the reader can see it (unavailable today)
    'control': (conftest(LAZY + 'client = Lazy()\ntry:\n    Lazy.__getattr__(client, "ready")\n'
                                'except AttributeError:\n    pass\n'), NODE),
    'conftest-proxy-getattr': (conftest(LAZY + 'client = Lazy()\n'), NODE),
    'conftest-proxy-getattribute': (conftest(EAGER + 'client = Lazy()\n'), NODE),
    'test-module-proxy-getattr': ({'test_api.py': PRELUDE + LAZY + 'client = Lazy()\n' + TEST}, NODE),
    'imported-proxy-getattr': ({'helper.py': PRELUDE + LAZY + 'client = Lazy()\n',
                                'conftest.py': 'from helper import client\n'}, NODE),
    'conftest-metaclass-getattr': (conftest(
        'class Meta(type):\n    def __getattr__(cls, name):\n        install()\n        raise AttributeError(name)\n'
        'class Settings(metaclass=Meta):\n    pass\n'), NODE),
    'conftest-module-getattr': (conftest(
        'def __getattr__(name):\n    install()\n    raise AttributeError(name)\n'), NODE),
    'conftest-module-dir': (conftest('def __dir__():\n    install()\n    return ["install"]\n'), NODE),
    'test-class-descriptor-get': ({'test_api.py': PRELUDE + (
        'class Shared:\n    def __get__(self, instance, owner):\n        return install()\n'
        'class TestAPI:\n    client = Shared()\n    def test_call(self):\n        pass\n')}, CLASS_NODE),
    'test-class-metaclass-call': ({'test_api.py': PRELUDE + (
        'class Meta(type):\n    def __call__(cls, *args, **kwargs):\n        install()\n'
        '        return type.__call__(cls, *args, **kwargs)\n'
        'class TestAPI(metaclass=Meta):\n    def test_call(self):\n        pass\n')}, CLASS_NODE),
    'test-class-dir': ({'test_api.py': PRELUDE + (
        'class TestAPI:\n    def __dir__(self):\n        install()\n        return object.__dir__(self)\n'
        '    def test_call(self):\n        pass\n')}, CLASS_NODE),
}


@pytest.mark.parametrize('shape', list(LOOKUPS))
def test_pytest_own_lookups_on_bound_objects(tmp_path, monkeypatch, shape):
    """pytest asks every object a conftest or test module binds for attributes while it collects."""
    files, node = LOOKUPS[shape]
    unseen(tmp_path, monkeypatch, files, node)


# --------------------------------------------------------------------------- important

META = ('class Meta(type):\n    def __getattribute__(cls, name):\n        install()\n'
        '        return type.__getattribute__(cls, name)\n')
REGISTRY = 'class Registry(metaclass=Meta):\n    def register(function):\n        return function\n'
DECORATORS = {
    # control: the same lookup in a call, which build 1 checks (unavailable today)
    'control': META + REGISTRY + 'def setup():\n    pass\nRegistry.register(setup)\n',
    'function': META + REGISTRY + '@Registry.register\ndef setup():\n    pass\n',
    'class': META + REGISTRY + '@Registry.register\nclass Setup:\n    pass\n',
    'inherited-metaclass': (META + 'class Base(metaclass=Meta):\n    pass\n'
                            'class Registry(Base):\n    def register(function):\n        return function\n'
                            '@Registry.register\ndef setup():\n    pass\n'),
    'called': (META + 'class Registry(metaclass=Meta):\n    def register():\n'
               '        def inner(function):\n            return function\n        return inner\n'
               '@Registry.register()\ndef setup():\n    pass\n'),
}


@pytest.mark.parametrize('shape', list(DECORATORS))
def test_decorator_lookup_checks_the_metaclass(tmp_path, monkeypatch, shape):
    """The decorator expression's own attribute lookup never reaches _attribute_allowed."""
    unseen(tmp_path, monkeypatch, conftest(DECORATORS[shape]))


GETTER = 'from dataclasses import dataclass\nclass Getter:\n    def __get__(self, instance, owner):\n        return install()\n'
INHERITED_DEFAULTS = {
    # control: the descriptor in the dataclass's own body, which build 1 checks (unavailable today)
    'control': GETTER + '@dataclass\nclass Settings:\n    value: int = Getter()\n',
    'base-descriptor': GETTER + 'class Base:\n    value = Getter()\n@dataclass\nclass Settings(Base):\n    value: int\n',
    'base-alias': (GETTER + 'shared = Getter()\nclass Base:\n    value = shared\n'
                   '@dataclass\nclass Settings(Base):\n    value: int\n    other: int = 1\n'),
}


@pytest.mark.parametrize('shape', list(INHERITED_DEFAULTS))
def test_dataclass_reads_a_default_inherited_from_a_base(tmp_path, monkeypatch, shape):
    """dataclass takes a field's default with getattr(cls, name): a base's descriptor answers it."""
    unseen(tmp_path, monkeypatch, conftest(INHERITED_DEFAULTS[shape]))


CONSTRUCTORS = {
    'control': ('class Factory:\n    def __new__(cls):\n        return object.__new__(Settings)\n'
                'class Settings(Factory):\n    def __init__(self):\n        install()\nSettings.__init__(None)\n'),
    'returned-subclass-init': ('class Factory:\n    def __new__(cls):\n        return object.__new__(Settings)\n'
                               'class Settings(Factory):\n    def __init__(self):\n        install()\nFactory()\n'),
    'returned-descriptor': (DESCRIPTOR + 'class Factory:\n    def __new__(cls):\n        return Descriptor()\n'
                            'class Settings:\n    value = Factory()\n'),
}


@pytest.mark.parametrize('shape', list(CONSTRUCTORS))
def test_constructor_that_returns_another_object(tmp_path, monkeypatch, shape):
    """__new__ may return an instance of a subclass (its __init__ runs) or of another class."""
    unseen(tmp_path, monkeypatch, conftest(CONSTRUCTORS[shape]))


VALUE = ('class Value:\n    def __eq__(self, other):\n        return bool(install())\n'
         '    def __hash__(self):\n        return 1\n'
         '    def __repr__(self):\n        install()\n        return "Value()"\n'
         'items = []\n')
LOOPS = {
    # control: the same two statements in the order they run once (unavailable today)
    'control': VALUE + 'items.append(Value())\nstr(items)\n',
    'for-str': VALUE + 'for number in (1, 2):\n    str(items)\n    items.append(Value())\n',
    'for-equality': VALUE + 'for number in (1, 2):\n    same = items == [1]\n    items.append(Value())\n',
    'for-functions': (VALUE + 'def show():\n    return str(items)\ndef add():\n    items.append(Value())\n'
                      'for number in (1, 2):\n    show()\n    add()\n'),
    'for-subscript-store': VALUE + 'box = [1]\nfor number in (1, 2):\n    str(box)\n    box[0] = Value()\n',
}


@pytest.mark.parametrize('shape', list(LOOPS))
def test_loop_body_reads_a_container_filled_later_in_the_body(tmp_path, monkeypatch, shape):
    """A loop body is read once, in source order: the second pass's container is never read."""
    unseen(tmp_path, monkeypatch, conftest(LOOPS[shape]))


RESOURCE = 'class Resource:\n    def __del__(self):\n        install()\n'
FINALIZERS = {
    'control': RESOURCE + 'held = Resource()\nResource.__del__(held)\n',
    'discarded': RESOURCE + 'Resource()\n',
    'rebound': RESOURCE + 'held = Resource()\nheld = None\n',
    'function-local': RESOURCE + 'def load():\n    held = Resource()\nload()\n',
    'inherited': RESOURCE + 'class Child(Resource):\n    pass\nChild()\n',
}


@pytest.mark.parametrize('shape', list(FINALIZERS))
def test_finalizer_of_an_instance_dropped_at_import(tmp_path, monkeypatch, shape):
    """Instantiation follows __new__ and __init__ only; __del__ runs when the instance is dropped."""
    unseen(tmp_path, monkeypatch, conftest(FINALIZERS[shape]))


# --------------------------------------------------------------------------- minor

VALUES = ('from contextlib import nullcontext\nclass Values:\n    def __iter__(self):\n        install()\n'
          '        return iter((1,))\n')
WITH_TARGETS = {
    'control': VALUES + 'value, = Values()\n',
    'tuple-target': VALUES + 'with nullcontext(Values()) as (value,):\n    pass\n',
    'list-target': VALUES + 'with nullcontext(enter_result=Values()) as [value]:\n    pass\n',
}


@pytest.mark.parametrize('shape', list(WITH_TARGETS))
def test_with_target_unpacking(tmp_path, monkeypatch, shape):
    unseen(tmp_path, monkeypatch, conftest(WITH_TARGETS[shape]))


DEFAULT = ('from dataclasses import dataclass, field\nclass Default:\n    def __repr__(self):\n        install()\n'
           '        return "Default()"\n')
REPRESENTATIONS = {
    'control': DEFAULT + 'held = Default()\nDefault.__repr__(held)\n',
    'default': DEFAULT + '@dataclass\nclass Settings:\n    value: object = Default()\n',
    'field-default': DEFAULT + '@dataclass\nclass Settings:\n    value: object = field(default=Default())\n',
    'repr-off': DEFAULT + '@dataclass(repr=False)\nclass Settings:\n    value: object = Default()\n',
}


@pytest.mark.parametrize('shape', list(REPRESENTATIONS))
def test_dataclass_writes_its_defaults_into_the_class_docstring(tmp_path, monkeypatch, shape):
    """A dataclass without a docstring gets one from str(inspect.signature(cls)): repr() of each default."""
    unseen(tmp_path, monkeypatch, conftest(REPRESENTATIONS[shape]))


ANNOTATIONS = {
    'control': 'class Marker:\n    def __eq__(self, other):\n        return bool(install())\nMarker.__eq__(None, int)\n',
    'namedtuple-instance': ('from typing import NamedTuple\nclass Marker:\n    def __eq__(self, other):\n'
                            '        return bool(install())\n    def __hash__(self):\n        return 1\n'
                            'class Settings(NamedTuple):\n    value: Marker()\n'),
    'namedtuple-metaclass': ('from typing import NamedTuple\nclass Meta(type):\n    def __eq__(cls, other):\n'
                             '        return bool(install())\n    def __hash__(cls):\n        return 1\n'
                             'class Marker(metaclass=Meta):\n    pass\n'
                             'class Settings(NamedTuple):\n    value: Marker\n'),
    'dataclass-instance': ('from dataclasses import dataclass\nclass Marker:\n    def __getattribute__(self, name):\n'
                           '        install()\n        return object.__getattribute__(self, name)\n'
                           '@dataclass\nclass Settings:\n    value: Marker() = 1\n'),
}


@pytest.mark.parametrize('shape', list(ANNOTATIONS))
def test_class_builder_consumes_an_annotation_value(tmp_path, monkeypatch, shape):
    """typing's NamedTuple compares each annotation; dataclass asks isinstance of each annotation."""
    unseen(tmp_path, monkeypatch, conftest(ANNOTATIONS[shape]))


SET_NAMES = {
    'control': DESCRIPTOR + 'class Settings:\n    value = Descriptor()\n',
    'field-default-without-dataclass': (DESCRIPTOR + 'from dataclasses import field\n'
                                        'class Settings:\n    value = field(default=Descriptor())\n'),
    'object-new': DESCRIPTOR + 'class Settings:\n    value = object.__new__(Descriptor)\n',
    'hook-argument': (DESCRIPTOR + 'class Factory:\n    def __init__(self, held):\n'
                      '        class Settings:\n            value = held\nFactory(Descriptor())\n'),
}


@pytest.mark.parametrize('shape', list(SET_NAMES))
def test_set_name_of_a_class_value_the_reader_did_not_construct(tmp_path, monkeypatch, shape):
    unseen(tmp_path, monkeypatch, conftest(SET_NAMES[shape]))


META_SETATTR = ('from dataclasses import dataclass\nclass Meta(type):\n    def __setattr__(cls, name, value):\n'
                '        install()\n        type.__setattr__(cls, name, value)\n')
META_STORES = {
    'control': META_SETATTR + 'class Settings(metaclass=Meta):\n    pass\nMeta.__setattr__(Settings, "value", 1)\n',
    'dataclass': META_SETATTR + '@dataclass\nclass Settings(metaclass=Meta):\n    value: int = 1\n',
}


@pytest.mark.parametrize('shape', list(META_STORES))
def test_dataclass_stores_generated_methods_through_the_metaclass(tmp_path, monkeypatch, shape):
    unseen(tmp_path, monkeypatch, conftest(META_STORES[shape]))


# --------------------------------------------------------------------------- limit

PARAMETERS = {
    'control': {'test_api.py': PRELUDE + (
        'class Cases:\n    def __iter__(self):\n        install()\n        return iter((1,))\n'
        'cases = Cases()\nCases.__iter__(cases)\n') + TEST},
    'argvalues-iter': {'test_api.py': PRELUDE + (
        'import pytest\nclass Cases:\n    def __iter__(self):\n        install()\n        return iter((1,))\n'
        '@pytest.mark.parametrize("value", Cases())\ndef test_other(value):\n    pass\n') + TEST},
    'value-getattr': {'test_api.py': PRELUDE + (
        'import pytest\nclass Case:\n    def __getattr__(self, name):\n        install()\n'
        '        raise AttributeError(name)\n'
        '@pytest.mark.parametrize("value", [Case()])\ndef test_other(value):\n    pass\n') + TEST},
}


@pytest.mark.parametrize('shape', list(PARAMETERS))
def test_parametrize_consumes_repository_values(tmp_path, monkeypatch, shape):
    """Beside the owner-ruled ids= callable: pytest iterates argvalues and asks each value for __name__."""
    unseen(tmp_path, monkeypatch, PARAMETERS[shape])


# --------------------------------------------------------------------------- answers lost by build 1

ORDINARY = {
    # control: an installed call the policy table lists
    'control': 'import logging\nlog = logging.getLogger(__name__)\n',
    'os-path-dirname': 'import os\nHERE = os.path.dirname(os.path.abspath(__file__))\n',
    'os-path-join': 'import os\nDATA = os.path.join("tests", "data")\n',
    're-compile': 'import re\nPATTERN = re.compile(r"\\d+")\n',
    'optional-annotation': 'from typing import Optional\ndef helper(value: Optional[int] = None):\n    return value\n',
    'list-annotation': 'from typing import List\ndef helper(values: List[int]):\n    return values\n',
    'typevar': 'from typing import TypeVar\nT = TypeVar("T")\n',
    'namedtuple': 'import collections\nPoint = collections.namedtuple("Point", "x y")\n',
    'datetime': 'import datetime\nSTART = datetime.datetime(2020, 1, 1)\n',
}


def parent_reader():
    """The reader as committed at e53617ee, read from git (read-only); None when unavailable."""
    shown = subprocess.run(['git', '-C', str(TREE), 'show', 'e53617ee:src/g6/workforce/_collection_effects.py'],
                           capture_output=True, timeout=30)
    if shown.returncode != 0:
        return None
    module = types.ModuleType('review_parent_effects')
    exec(compile(shown.stdout, 'e53617ee:_collection_effects.py', 'exec'), module.__dict__)
    return module.CollectionEffects


@pytest.mark.parametrize('shape', list(ORDINARY))
def test_ordinary_installed_call_keeps_its_answer(tmp_path, monkeypatch, shape):
    """Installed calls that take no repository code: certain at e53617ee, unavailable at 1933925d."""
    source = ORDINARY[shape]
    parent = parent_reader()
    if parent is not None:
        before = parent.from_trees({'helper.py': ast.parse(source)}, files={'helper.py'}).passive('helper.py')
        assert before is True, 'precondition: the parent commit read this module certain'
    wrote, authority = collected(tmp_path, monkeypatch, {'helper.py': source, 'conftest.py': 'import helper\n'})
    assert wrote is False, 'precondition: nothing writes PATH while real pytest collects'
    assert authority is True, 'an inert ordinary module lost its collection answer'


# --------------------------------------------------------------------------- neighbours that stay certain

INERT = {
    'inherited-store': 'class Base:\n    def __init__(self):\n        self.value = 1\nclass Child(Base):\n    pass\nChild()\n',
    'classmethod-inert-callback': ('class Settings:\n    @classmethod\n    def apply(cls, hook=noop):\n        hook()\n'
                                   'Settings.apply()\n'),
    'nested-class': 'class Outer:\n    hook = noop\n    class Inner:\n        value = 1\n',
    'plain-module-object': 'class Plain:\n    pass\nclient = Plain()\n',
    'decorator-through-plain-class': ('class Registry:\n    def register(function):\n        return function\n'
                                      '@Registry.register\ndef setup():\n    pass\n'),
    'dataclass-plain-base': ('from dataclasses import dataclass\nclass Base:\n    other = 1\n'
                             '@dataclass\nclass Settings(Base):\n    value: int = 1\n'),
    'plain-loop': 'items = []\nfor number in (1, 2):\n    str(items)\n    items.append(number)\n',
    'with-plain-target': 'from contextlib import nullcontext\nwith nullcontext((1,)) as value:\n    pass\n',
}


@pytest.mark.parametrize('shape', list(INERT))
def test_inert_neighbours_read_certain(tmp_path, monkeypatch, shape):
    wrote, authority = collected(tmp_path, monkeypatch, conftest(INERT[shape]))
    assert wrote is False and authority is True
