# SPDX-License-Identifier: Apache-2.0
"""Bounded source-only reading of ordinary import-time collection effects.

This is advisory analysis within the collection child's same-process caveat.
Nothing here imports, evaluates or calls repository objects.
"""
from __future__ import annotations

import ast
import fnmatch
import sys
import sysconfig
from collections import deque
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath

from g6.core import component_process


MAX_CALL_DEPTH = 24
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
# These operations build values; the binding must resolve to the builtin first.
_VALUE_BUILTINS = frozenset("bool bytes complex dict float frozenset int len list range set str tuple".split())
_LITERALS = {ast.List: "?list", ast.Dict: "?dict", ast.Tuple: "?tuple", ast.Set: "?set"}
_LITERALS.update({ast.ListComp: "?list", ast.SetComp: "?set", ast.DictComp: "?dict", ast.GeneratorExp: "?generator"})

# Exact external callees and their argument policy. No package-wide fallback:
# an unlisted dependency may call a callback, load a module or consume a protocol.
MODELLED_EXTERNAL_CALLS = {
    **dict.fromkeys(("builtins." + name for name in _VALUE_BUILTINS),
                    "plain: conversion/iteration only over modeled builtin values"),
    "builtins.sorted": "sorted: plain iterable and a modeled builtin key only",
    "builtins.type": "type: one-argument inspection runs no hook; a result with repository input needs a separately modeled identity",
    "builtins.object": "allocate: bare builtin object allocation runs no caller code",
    "builtins.object.__new__": "allocate: native allocation; audit the allocated class's later protocols",
    "builtins.object.__getattribute__": "native-lookup: forward a constructor-free receiver's lookup to fully audited class members",
    "logging.getLogger": "logger: literal/module name only; no caller-supplied code",
    "contextlib.nullcontext": "hold: stores its argument without invoking it",
    "dataclasses.dataclass": "dataclass: class creation/defaults and generated hooks followed by _class",
    "dataclasses.field": "field: stores defaults; forwards default set_name even outside dataclasses; factories followed by _class",
    "functools.wraps": "wraps: resolvable function metadata only, checked by _decoration",
    "pytest.fixture": "fixture: defer body; autouse and declarations checked by _scan/passive",
    "builtins.staticmethod": "hold: stores function without invoking it",
    "builtins.classmethod": "hold: stores function without invoking it",
    "builtins.property": "hold: stores accessors; descriptor access is unavailable",
    **dict.fromkeys(("os.path." + name for name in ("dirname", "join", "abspath", "basename", "exists")),
                    "constant: path operations on literal strings run no repository path protocol"),
    "re.compile": "constant: literal pattern/flags run no repository conversion or callback",
    "typing.TypeVar": "constant: literal name and installed type constraints run no repository code",
    "collections.namedtuple": "constant: literal names/fields/defaults contain no repository callbacks",
    "typing.NamedTuple": "constant: literal fields with installed types contain no repository annotations",
    **dict.fromkeys(("datetime." + name for name in ("datetime", "date", "timedelta")),
                    "constant: literal constructor arguments run no repository numeric hooks"),
}
_PLAIN_EXTERNAL_VALUES = frozenset(("sys.platform", "sys.version", "sys.version_info", "sys.maxsize", "os.name",
    *("re." + name for name in ("ASCII", "IGNORECASE", "LOCALE", "MULTILINE", "DOTALL", "UNICODE", "VERBOSE",
                                "A", "I", "L", "M", "S", "U", "X", "NOFLAG"))))
_INSTALLED_TYPES = frozenset("builtins." + name for name in (*_VALUE_BUILTINS, "object", "type"))
_TYPING_GENERICS = frozenset("typing." + name for name in (
    "Optional", "List", "Dict", "Union", "Tuple", "Set", "FrozenSet", "Sequence", "Mapping", "Iterable",
    "Iterator", "Callable", "Type", "ClassVar", "Final", "Literal", "Annotated"))
# These bodies have explicit dispatch below. Every other repository protocol is
# unavailable, even if a particular spelling happens to have an inert body.
MODELLED_CLASS_PROTOCOLS = frozenset((
    "__init__", "__new__", "__init_subclass__", "__set_name__", "__post_init__",
    "__setattr__", "__getattribute__", "__getattr__", "__prepare__",
))
# The existing external-base set, with explicit native/generated/source policies.
# Source policies follow installed lifecycle bodies, including receiver dispatch.
MODELLED_EXTERNAL_BASES = {
    "builtins.object": "native: allocation and subclass creation run no caller code",
    "builtins.type": "native: repository metaclasses are followed separately",
    "typing.NamedTuple": "fields: generated record/default protocols are followed by _class_fields",
    "unittest.TestCase": "source: read installed lifecycle bodies on the repository receiver",
}

# Source-derived superset: internal lookups are retained so a new literal
# lookup anywhere in these sources cannot silently escape the pin test.
# '*' means every bound/member name, never permission for an unknown result.
PYTEST_COLLECTION_LOOKUPS = {
    "pytest_version": '9.2.0',
    "python_version": (3, 12, 11),
    "policies": {
        'receiver': 'Follow receiver hooks and descriptors; dynamic names audit every binding; native metadata is inert.',
        'source-pin': 'Inventory superset, including internal reads; not evidence that a receiver route executes.',
        'holder': 'dir only on the collected module/class or instance holder; classes become holders only through bound names, never container traversal.',
        'unresolved-value': 'Every value needs an explicit lookup-result model; unknown bindings, candidates and results are unavailable.',
        'external-result': 'Nonrepository module/call result with no repository argument has inert lookup; executed calls still require admission.',
        'test-flag': 'Unknown effective metaclass __test__ replacement is unavailable, even after following an inert hook.',
    },
    "sources": (
        ('pytest', '_pytest/python.py', '9553fbb38363c987f59b1de522142a9dc0e9f6191cd1f173a6bee21140d4c887'),
        ('pytest', '_pytest/compat.py', '3b3f955b969c8d4794db70ae1b45f1ef525930ba4da86144fc7409bf394a806e'),
        ('pytest', '_pytest/fixtures.py', '3847b9bbb05ba2408c3e9e270276ee86a70c1f0d1d420ad14b37b06093a9a330'),
        ('pytest', '_pytest/unittest.py', '3bc68aac6f44cb15dee7131fd4902f2d14a1e54205c89a6e707e5416711a92dd'),
        ('pytest', '_pytest/mark/structures.py', 'e8322e0b57aa8c8bb8150a563a3bc8b85983b3f30645773dcc7488b1db6e9755'),
        ('pytest', '_pytest/config/__init__.py', 'd297eaeba8f6ecfeb73acaa8eef2092203aa49343eb7c77ffedb8ef95b5717ab'),
        ('pluggy', 'pluggy/_manager.py', '95b8787e4ca8882afea9b97e1ef670febbd3eb411a94fc85ba43ad76a0272c1d'),
    ),
    "stdlib_sources": (
        ('inspect.py', '5c54ebf0bfdc25397750e5d0f63800751650ee50e65325ff3ea4069c9bfa5f75'),
        ('unittest/loader.py', '7f0af414bfa7fe8612a424b14cfcf8a3c34fcac999d976461beb738ef8b211b8'),
    ),
    "indirect": (
        ('_pytest/python.py', '_get_first_non_fixture_func', 1, (760,), ((596, ('setUpModule', 'setup_module')), (599, ('tearDownModule', 'teardown_module')), (630, ('setup_function',)), (631, ('teardown_function',)), (702, ('setUpModule', 'setup_module')), (708, ('tearDownModule', 'teardown_module')), (815, ('setup_class',)), (816, ('teardown_class',)), (847, ('setup_method',)), (849, ('teardown_method',)))),
        ('_pytest/config/__init__.py', '_get_legacy_hook_marks', 2, (472,), ((576, ('tryfirst', 'trylast', 'optionalhook', 'hookwrapper')), (587, ('firstresult', 'historic')))),
        ('inspect.py', '_signature_get_user_defined_method', 1, (2036, 2038), ((2614, ('__call__',)), (2618, ('__new__',)), (2619, ('__init__',)))),
    ),
    "transitive": (
        ('inspect.py', 'get_annotations', 176, 294),
        ('inspect.py', 'ismodule', 298, 300),
        ('inspect.py', 'isclass', 302, 304),
        ('inspect.py', 'ismethod', 306, 308),
        ('inspect.py', 'ismethoddescriptor', 310, 328),
        ('inspect.py', 'isdatadescriptor', 330, 342),
        ('inspect.py', 'isfunction', 378, 389),
        ('inspect.py', '_has_code_flag', 391, 400),
        ('inspect.py', 'isgeneratorfunction', 402, 407),
        ('inspect.py', '_has_coroutine_mark', 412, 416),
        ('inspect.py', 'iscoroutinefunction', 427, 433),
        ('inspect.py', 'isasyncgenfunction', 435, 441),
        ('inspect.py', 'isbuiltin', 524, 531),
        ('inspect.py', 'ismethodwrapper', 533, 535),
        ('inspect.py', 'isroutine', 537, 543),
        ('inspect.py', 'isabstract', 545, 567),
        ('inspect.py', 'unwrap', 754, 783),
        ('inspect.py', '_check_instance', 1797, 1803),
        ('inspect.py', '_check_class', 1806, 1810),
        ('inspect.py', '_shadowed_dict_from_weakref_mro_tuple', 1814, 1829),
        ('inspect.py', '_shadowed_dict', 1832, 1842),
        ('inspect.py', 'getattr_static', 1845, 1892),
        ('inspect.py', '_signature_get_user_defined_method', 2030, 2045),
        ('inspect.py', '_signature_get_partial', 2048, 2121),
        ('inspect.py', '_signature_bound_method', 2124, 2147),
        ('inspect.py', '_signature_is_builtin', 2150, 2161),
        ('inspect.py', '_signature_is_functionlike', 2164, 2186),
        ('inspect.py', '_signature_strip_non_python_syntax', 2189, 2236),
        ('inspect.py', '_signature_fromstr', 2239, 2378),
        ('inspect.py', '_signature_from_builtin', 2381, 2394),
        ('inspect.py', '_signature_from_function', 2397, 2489),
        ('inspect.py', '_descriptor_get', 2492, 2498),
        ('inspect.py', '_signature_from_callable', 2501, 2676),
        ('inspect.py', 'signature', 3346, 3349),
        ('inspect.py', '__init__', 1041, 1043),
        ('inspect.py', '__init__', 1187, 1194),
        ('inspect.py', '__new__', 1666, 1669),
        ('inspect.py', '__new__', 1738, 1741),
        ('inspect.py', 'parse_name', 2271, 2275),
        ('inspect.py', 'wrap_value', 2277, 2288),
        ('inspect.py', 'p', 2323, 2331),
        ('inspect.py', '__new__', 2694, 2699),
        ('inspect.py', '__init__', 2743, 2784),
        ('inspect.py', 'name', 2797, 2798),
        ('inspect.py', 'default', 2801, 2802),
        ('inspect.py', 'annotation', 2805, 2806),
        ('inspect.py', 'kind', 2809, 2810),
        ('inspect.py', 'replace', 2812, 2828),
        ('inspect.py', '__init__', 2888, 2890),
        ('inspect.py', 'signature', 2893, 2894),
        ('inspect.py', 'args', 2897, 2917),
        ('inspect.py', 'kwargs', 2920, 2947),
        ('inspect.py', '__init__', 3029, 3079),
        ('inspect.py', 'from_callable', 3082, 3087),
        ('inspect.py', 'parameters', 3090, 3091),
        ('inspect.py', 'return_annotation', 3094, 3095),
        ('inspect.py', 'replace', 3097, 3110),
        ('inspect.py', '_bind', 3133, 3273),
        ('inspect.py', 'bind_partial', 3282, 3287),
        ('unittest/loader.py', 'getTestCaseNames', 468, 475),
        ('unittest/loader.py', 'getTestCaseNames', 210, 227),
    ),
    "reads": (
        ('_pytest/python.py', 188, 'module value class instance function',
         ('__aiter__', '__await__'), 'receiver'),
        ('_pytest/python.py', 250, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('_pytest/python.py', 264, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/python.py', 308, 'module value class instance function',
         ('_obj',), 'receiver'),
        ('_pytest/python.py', 329, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/python.py', 361, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('_pytest/python.py', 365, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('_pytest/python.py', 367, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('_pytest/python.py', 384, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/python.py', 393, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('_pytest/python.py', 401, 'module value class instance function',
         ('__abstractmethods__', '__isabstractmethod__'), 'receiver'),
        ('_pytest/python.py', 421, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/python.py', 425, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/python.py', 427, 'module value class instance function',
         ('__mro__',), 'receiver'),
        ('_pytest/python.py', 428, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/python.py', 450, 'module value class instance function',
         ('__module__', '__name__'), 'receiver'),
        ('_pytest/python.py', 492, 'module value class instance function',
         ('pytest_generate_tests',), 'receiver'),
        ('_pytest/python.py', 494, 'module value class instance function',
         ('pytest_generate_tests',), 'receiver'),
        ('_pytest/python.py', 616, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 652, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 688, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/python.py', 746, 'module value class instance function',
         ('__code__',), 'receiver'),
        ('_pytest/python.py', 760, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/python.py', 778, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/python.py', 784, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 793, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 832, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/python.py', 857, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/python.py', 861, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/python.py', 866, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/python.py', 875, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/python.py', 877, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/python.py', 882, 'module value class instance function',
         ('__new__',), 'receiver'),
        ('_pytest/python.py', 884, 'module value class instance function',
         ('__new__',), 'receiver'),
        ('_pytest/python.py', 1116, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 1118, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 1244, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/python.py', 1413, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/python.py', 1432, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/python.py', 1709, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/python.py', 1713, 'module value class instance function',
         ('__self__',), 'receiver'),
        ('_pytest/python.py', 1737, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/python.py', 1789, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/python.py', 1804, 'module value class instance function',
         ('_obj',), 'receiver'),
        ('_pytest/compat.py', 59, 'module value class instance function',
         ('_is_coroutine',), 'receiver'),
        ('_pytest/compat.py', 71, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__signature__'), 'receiver'),
        ('_pytest/compat.py', 72, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__signature__'), 'receiver'),
        ('_pytest/compat.py', 78, 'module value class instance function',
         ('__code__',), 'receiver'),
        ('_pytest/compat.py', 91, 'module value class instance function',
         ('patchings',), 'receiver'),
        ('_pytest/compat.py', 95, 'module value class instance function',
         ('DEFAULT',), 'receiver'),
        ('_pytest/compat.py', 96, 'module value class instance function',
         ('DEFAULT',), 'receiver'),
        ('_pytest/compat.py', 137, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__signature__'), 'receiver'),
        ('_pytest/compat.py', 156, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/compat.py', 167, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/compat.py', 172, 'module value class instance function',
         ('__wrapped__',), 'receiver'),
        ('_pytest/compat.py', 183, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__signature__'), 'receiver'),
        ('_pytest/compat.py', 223, 'module value class instance function',
         ('__wrapped__',), 'receiver'),
        ('_pytest/compat.py', 232, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('_pytest/compat.py', 249, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/compat.py', 288, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 380, 'module value class instance function',
         ('pypy_version_info',), 'receiver'),
        ('_pytest/fixtures.py', 610, 'module value class instance function',
         ('instance',), 'receiver'),
        ('_pytest/fixtures.py', 807, 'module value class instance function',
         ('nofuncargs',), 'receiver'),
        ('_pytest/fixtures.py', 812, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 846, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/fixtures.py', 898, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/fixtures.py', 963, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__signature__'), 'receiver'),
        ('_pytest/fixtures.py', 964, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1183, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1311, 'module value class instance function',
         ('param',), 'receiver'),
        ('_pytest/fixtures.py', 1324, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/fixtures.py', 1357, 'module value class instance function',
         ('__self__',), 'receiver'),
        ('_pytest/fixtures.py', 1358, 'module value class instance function',
         ('__self__',), 'receiver'),
        ('_pytest/fixtures.py', 1373, 'module value class instance function',
         ('__self__',), 'receiver'),
        ('_pytest/fixtures.py', 1375, 'module value class instance function',
         ('__class__', '__self__'), 'receiver'),
        ('_pytest/fixtures.py', 1380, 'module value class instance function',
         ('__get__',), 'receiver'),
        ('_pytest/fixtures.py', 1414, 'module value class instance function',
         ('__traceback__',), 'receiver'),
        ('_pytest/fixtures.py', 1440, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1443, 'module value class instance function',
         ('pytestmark',), 'receiver'),
        ('_pytest/fixtures.py', 1453, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1475, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1482, 'module value class instance function',
         ('__get__',), 'receiver'),
        ('_pytest/fixtures.py', 1728, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/fixtures.py', 1850, 'module value class instance function',
         ('nofuncargs',), 'receiver'),
        ('_pytest/fixtures.py', 2148, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/fixtures.py', 2149, 'module value class instance function',
         ('__mro__',), 'receiver'),
        ('_pytest/fixtures.py', 2151, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/fixtures.py', 2170, 'module value class instance function',
         ('_mock_name',), 'receiver'),
        ('_pytest/fixtures.py', 2178, 'module value class instance function',
         ('__wrapped__',), 'receiver'),
        ('_pytest/fixtures.py', 2212, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('_pytest/fixtures.py', 2308, 'module class instance-type',
         ('*', '__dir__'), 'holder'),
        ('_pytest/fixtures.py', 2317, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/fixtures.py', 2334, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/fixtures.py', 2418, 'module value class instance function',
         ('__file__',), 'receiver'),
        ('_pytest/fixtures.py', 2444, 'module value class instance function',
         ('_fixtureinfo',), 'receiver'),
        ('_pytest/fixtures.py', 2529, 'module value class instance function',
         ('__module__',), 'receiver'),
        ('_pytest/unittest.py', 73, 'module value class instance function',
         ('__abstractmethods__', '__isabstractmethod__'), 'receiver'),
        ('_pytest/unittest.py', 96, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/unittest.py', 114, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/unittest.py', 115, 'module value class instance function',
         ('__test__',), 'receiver'),
        ('_pytest/unittest.py', 121, 'module value class instance function',
         ('runTest',), 'receiver'),
        ('_pytest/unittest.py', 130, 'module value class instance function',
         ('setUpClass',), 'receiver'),
        ('_pytest/unittest.py', 131, 'module value class instance function',
         ('tearDownClass',), 'receiver'),
        ('_pytest/unittest.py', 132, 'module value class instance function',
         ('doClassCleanups',), 'receiver'),
        ('_pytest/unittest.py', 137, 'module value class instance function',
         ('tearDown_exceptions',), 'receiver'),
        ('_pytest/unittest.py', 153, 'module value class instance function',
         ('__unittest_skip_why__',), 'receiver'),
        ('_pytest/unittest.py', 174, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/unittest.py', 186, 'module value class instance function',
         ('__unittest_skip_why__',), 'receiver'),
        ('_pytest/unittest.py', 190, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/unittest.py', 200, 'module value class instance function',
         ('setup_method',), 'receiver'),
        ('_pytest/unittest.py', 201, 'module value class instance function',
         ('teardown_method',), 'receiver'),
        ('_pytest/unittest.py', 210, 'module value class instance function',
         ('__unittest_skip_why__',), 'receiver'),
        ('_pytest/unittest.py', 220, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/unittest.py', 295, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/unittest.py', 527, 'module value class instance function',
         ('__unittest_skip__',), 'receiver'),
        ('_pytest/unittest.py', 585, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/unittest.py', 631, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/unittest.py', 632, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/unittest.py', 642, 'module value class instance function',
         ('__traceback__',), 'receiver'),
        ('_pytest/mark/structures.py', 57, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/mark/structures.py', 77, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/mark/structures.py', 215, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/mark/structures.py', 236, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/mark/structures.py', 305, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/mark/structures.py', 306, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/mark/structures.py', 307, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/mark/structures.py', 308, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/mark/structures.py', 309, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/mark/structures.py', 440, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('_pytest/mark/structures.py', 460, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/mark/structures.py', 463, 'module value class instance function',
         ('__dict__', '__mro__'), 'receiver'),
        ('_pytest/mark/structures.py', 472, 'module value class instance function',
         ('pytestmark',), 'receiver'),
        ('_pytest/mark/structures.py', 502, 'module value class instance function',
         ('mark',), 'receiver'),
        ('_pytest/config/__init__.py', 145, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 191, 'module value class instance function',
         ('__cause__',), 'receiver'),
        ('_pytest/config/__init__.py', 192, 'module value class instance function',
         ('__cause__',), 'receiver'),
        ('_pytest/config/__init__.py', 468, 'module value class instance function',
         ('pytestmark',), 'receiver'),
        ('_pytest/config/__init__.py', 472, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 485, 'module value class instance function',
         ('__qualname__',), 'receiver'),
        ('_pytest/config/__init__.py', 506, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('_pytest/config/__init__.py', 536, 'module value class instance function',
         ('encoding',), 'receiver'),
        ('_pytest/config/__init__.py', 571, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 585, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 758, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 795, 'module value class instance function',
         ('__traceback__',), 'receiver'),
        ('_pytest/config/__init__.py', 808, 'module value class instance function',
         ('__file__',), 'receiver'),
        ('_pytest/config/__init__.py', 822, 'module value class instance function',
         ('pytest_plugins',), 'receiver'),
        ('_pytest/config/__init__.py', 908, 'module value class instance function',
         ('pytest_plugins',), 'receiver'),
        ('_pytest/config/__init__.py', 1039, 'module value class instance function',
         ('read_text',), 'receiver'),
        ('_pytest/config/__init__.py', 1195, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/config/__init__.py', 1196, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/config/__init__.py', 1197, 'module value class instance function',
         ('__setattr__',), 'receiver'),
        ('_pytest/config/__init__.py', 1402, 'module value class instance function',
         ('version',), 'receiver'),
        ('_pytest/config/__init__.py', 1409, 'module value class instance function',
         ('help',), 'receiver'),
        ('_pytest/config/__init__.py', 1425, 'module value class instance function',
         ('fulltrace',), 'receiver'),
        ('_pytest/config/__init__.py', 1430, 'module value class instance function',
         ('showlocals',), 'receiver'),
        ('_pytest/config/__init__.py', 1450, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('_pytest/config/__init__.py', 1457, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 1494, 'module value class instance function',
         ('assertmode',), 'receiver'),
        ('_pytest/config/__init__.py', 1496, 'module value class instance function',
         ('disable_plugin_autoload',), 'receiver'),
        ('_pytest/config/__init__.py', 1629, 'module value class instance function',
         ('__version__',), 'receiver'),
        ('_pytest/config/__init__.py', 1631, 'module value class instance function',
         ('__version__',), 'receiver'),
        ('_pytest/config/__init__.py', 1811, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 1969, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 1990, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 2088, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 2098, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 2118, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('_pytest/config/__init__.py', 2166, 'module value class instance function',
         ('__file__',), 'receiver'),
        ('_pytest/config/__init__.py', 2167, 'module value class instance function',
         ('__file__',), 'receiver'),
        ('_pytest/config/__init__.py', 2190, 'module value class instance function',
         ('*',), 'receiver'),
        ('_pytest/config/__init__.py', 2473, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 47, 'module value class instance function',
         ('__code__',), 'receiver'),
        ('pluggy/_manager.py', 48, 'module value class instance function',
         ('__code__',), 'receiver'),
        ('pluggy/_manager.py', 58, 'module value class instance function',
         ('__getattribute__',), 'receiver'),
        ('pluggy/_manager.py', 86, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 90, 'module value class instance function',
         ('__get__',), 'receiver'),
        ('pluggy/_manager.py', 95, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('pluggy/_manager.py', 100, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 102, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('pluggy/_manager.py', 105, 'module value class instance function',
         ('__get__',), 'receiver'),
        ('pluggy/_manager.py', 107, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('pluggy/_manager.py', 111, 'module value class instance function',
         ('__get__',), 'receiver'),
        ('pluggy/_manager.py', 117, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 120, 'module value class instance function',
         ('__func__',), 'receiver'),
        ('pluggy/_manager.py', 122, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 136, 'module value class instance function',
         ('__init__',), 'receiver'),
        ('pluggy/_manager.py', 223, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('pluggy/_manager.py', 236, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 245, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 351, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('pluggy/_manager.py', 354, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 412, 'module value class instance function',
         ('__name__',), 'receiver'),
        ('pluggy/_manager.py', 485, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('pluggy/_manager.py', 488, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 565, 'module value class instance function',
         ('__dict__',), 'receiver'),
        ('pluggy/_manager.py', 637, 'module value class instance function',
         ('*',), 'receiver'),
        ('pluggy/_manager.py', 652, 'module value class instance function',
         ('__annotations__', '__defaults__', '__kwdefaults__', '__name__', '__signature__'), 'receiver'),
        ('_pytest/python.py', 98, 'patterns',
         ('python_files', 'test_*.py', '*_test.py'), 'default file globs; explicit files bypass them'),
        ('_pytest/python.py', 105, 'patterns',
         ('python_classes', 'Test'), 'default prefix/glob; use each bound name'),
        ('_pytest/python.py', 111, 'patterns',
         ('python_functions', 'test'), 'default prefix/glob; use each bound name'),
        ('inspect.py', 223, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 224, 'inventory',
         ('get',), 'source-pin'),
        ('inspect.py', 232, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 236, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 241, 'inventory',
         ('__annotations__',), 'source-pin'),
        ('inspect.py', 242, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 249, 'inventory',
         ('__annotations__',), 'source-pin'),
        ('inspect.py', 250, 'inventory',
         ('__globals__',), 'source-pin'),
        ('inspect.py', 270, 'inventory',
         ('__wrapped__',), 'source-pin'),
        ('inspect.py', 271, 'inventory',
         ('__wrapped__',), 'source-pin'),
        ('inspect.py', 277, 'inventory',
         ('__globals__',), 'source-pin'),
        ('inspect.py', 278, 'inventory',
         ('__globals__',), 'source-pin'),
        ('inspect.py', 288, 'inventory',
         ('__type_params__',), 'source-pin'),
        ('inspect.py', 289, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 328, 'inventory',
         ('__get__', '__set__'), 'source-pin'),
        ('inspect.py', 342, 'inventory',
         ('__delete__', '__set__'), 'source-pin'),
        ('inspect.py', 344, 'inventory',
         ('MemberDescriptorType',), 'source-pin'),
        ('inspect.py', 361, 'inventory',
         ('GetSetDescriptorType',), 'source-pin'),
        ('inspect.py', 396, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 400, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 414, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 416, 'inventory',
         ('_is_coroutine_marker',), 'source-pin'),
        ('inspect.py', 422, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 423, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 472, 'inventory',
         ('gi_code',), 'source-pin'),
        ('inspect.py', 549, 'inventory',
         ('__flags__',), 'source-pin'),
        ('inspect.py', 553, 'inventory',
         ('__abstractmethods__',), 'source-pin'),
        ('inspect.py', 559, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 560, 'inventory',
         ('__isabstractmethod__',), 'source-pin'),
        ('inspect.py', 562, 'inventory',
         ('__bases__',), 'source-pin'),
        ('inspect.py', 563, 'inventory',
         ('__abstractmethods__',), 'source-pin'),
        ('inspect.py', 564, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 565, 'inventory',
         ('__isabstractmethod__',), 'source-pin'),
        ('inspect.py', 572, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('inspect.py', 579, 'inventory',
         ('__bases__',), 'source-pin'),
        ('inspect.py', 580, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 598, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 599, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 664, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('inspect.py', 669, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 692, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 696, 'inventory',
         ('__objclass__',), 'source-pin'),
        ('inspect.py', 704, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 710, 'inventory',
         ('__getattr__',), 'source-pin'),
        ('inspect.py', 718, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 719, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 750, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 775, 'inventory',
         ('__wrapped__',), 'source-pin'),
        ('inspect.py', 778, 'inventory',
         ('__wrapped__',), 'source-pin'),
        ('inspect.py', 792, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 795, 'inventory',
         ('__qualname__',), 'source-pin'),
        ('inspect.py', 796, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 803, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 806, 'inventory',
         ('__doc__',), 'source-pin'),
        ('inspect.py', 814, 'inventory',
         ('__func__', '__name__'), 'source-pin'),
        ('inspect.py', 815, 'inventory',
         ('__self__',), 'source-pin'),
        ('inspect.py', 817, 'inventory',
         ('*', '__func__'), 'source-pin'),
        ('inspect.py', 821, 'inventory',
         ('__class__',), 'source-pin'),
        ('inspect.py', 823, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 825, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 828, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 829, 'inventory',
         ('__self__',), 'source-pin'),
        ('inspect.py', 831, 'inventory',
         ('__qualname__',), 'source-pin'),
        ('inspect.py', 835, 'inventory',
         ('__class__',), 'source-pin'),
        ('inspect.py', 838, 'inventory',
         ('fget',), 'source-pin'),
        ('inspect.py', 839, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 841, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 844, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 845, 'inventory',
         ('__objclass__',), 'source-pin'),
        ('inspect.py', 846, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 849, 'inventory',
         ('__slots__',), 'source-pin'),
        ('inspect.py', 854, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 856, 'inventory',
         ('*', '__doc__'), 'source-pin'),
        ('inspect.py', 870, 'inventory',
         ('__doc__',), 'source-pin'),
        ('inspect.py', 914, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 915, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 918, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 919, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 920, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 921, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 922, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 926, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 928, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 930, 'inventory',
         ('tb_frame',), 'source-pin'),
        ('inspect.py', 932, 'inventory',
         ('f_code',), 'source-pin'),
        ('inspect.py', 934, 'inventory',
         ('co_filename',), 'source-pin'),
        ('inspect.py', 937, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 971, 'inventory',
         ('__loader__',), 'source-pin'),
        ('inspect.py', 973, 'inventory',
         ('__spec__', 'loader'), 'source-pin'),
        ('inspect.py', 992, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 993, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 1007, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 1008, 'inventory',
         ('__file__',), 'source-pin'),
        ('inspect.py', 1016, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 1021, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 1023, 'inventory',
         ('*', '__name__'), 'source-pin'),
        ('inspect.py', 1024, 'inventory',
         ('*', '__name__'), 'source-pin'),
        ('inspect.py', 1029, 'inventory',
         ('*', '__name__'), 'source-pin'),
        ('inspect.py', 1030, 'inventory',
         ('*', '__name__'), 'source-pin'),
        ('inspect.py', 1092, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1102, 'inventory',
         ('__qualname__',), 'source-pin'),
        ('inspect.py', 1115, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 1117, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 1119, 'inventory',
         ('tb_frame',), 'source-pin'),
        ('inspect.py', 1121, 'inventory',
         ('f_code',), 'source-pin'),
        ('inspect.py', 1123, 'inventory',
         ('co_firstlineno',), 'source-pin'),
        ('inspect.py', 1125, 'inventory',
         ('co_firstlineno',), 'source-pin'),
        ('inspect.py', 1243, 'inventory',
         ('__next__',), 'source-pin'),
        ('inspect.py', 1270, 'inventory',
         ('tb_frame',), 'source-pin'),
        ('inspect.py', 1274, 'inventory',
         ('f_code',), 'source-pin'),
        ('inspect.py', 1294, 'inventory',
         ('__bases__',), 'source-pin'),
        ('inspect.py', 1311, 'inventory',
         ('__bases__',), 'source-pin'),
        ('inspect.py', 1312, 'inventory',
         ('__bases__',), 'source-pin'),
        ('inspect.py', 1464, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 1472, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 1473, 'inventory',
         ('__qualname__',), 'source-pin'),
        ('inspect.py', 1474, 'inventory',
         ('__module__', '__qualname__'), 'source-pin'),
        ('inspect.py', 1478, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 1552, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 1556, 'inventory',
         ('__self__',), 'source-pin'),
        ('inspect.py', 1558, 'inventory',
         ('__self__',), 'source-pin'),
        ('inspect.py', 1616, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 1621, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 1624, 'inventory',
         ('__closure__',), 'source-pin'),
        ('inspect.py', 1629, 'inventory',
         ('__closure__',), 'source-pin'),
        ('inspect.py', 1634, 'inventory',
         ('__globals__',), 'source-pin'),
        ('inspect.py', 1635, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1637, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1667, 'inventory',
         ('__new__',), 'source-pin'),
        ('inspect.py', 1739, 'inventory',
         ('__new__',), 'source-pin'),
        ('inspect.py', 1777, 'inventory',
         ('_getframe',), 'source-pin'),
        ('inspect.py', 1786, 'inventory',
         ('__traceback__',), 'source-pin'),
        ('inspect.py', 1793, 'inventory',
         ('__dict__', '__get__'), 'source-pin'),
        ('inspect.py', 1794, 'inventory',
         ('__dict__', '__get__'), 'source-pin'),
        ('inspect.py', 1800, 'inventory',
         ('__getattribute__',), 'source-pin'),
        ('inspect.py', 1808, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1809, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1826, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 1827, 'inventory',
         ('__objclass__',), 'source-pin'),
        ('inspect.py', 1887, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1889, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 1930, 'inventory',
         ('gi_frame',), 'source-pin'),
        ('inspect.py', 1968, 'inventory',
         ('cr_frame',), 'source-pin'),
        ('inspect.py', 2012, 'inventory',
         ('ag_frame',), 'source-pin'),
        ('inspect.py', 2036, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 2038, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 2176, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 2177, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 2178, 'inventory',
         ('__defaults__',), 'source-pin'),
        ('inspect.py', 2179, 'inventory',
         ('__kwdefaults__',), 'source-pin'),
        ('inspect.py', 2180, 'inventory',
         ('__annotations__',), 'source-pin'),
        ('inspect.py', 2206, 'inventory',
         ('__next__',), 'source-pin'),
        ('inspect.py', 2243, 'inventory',
         ('_parameter_cls',), 'source-pin'),
        ('inspect.py', 2264, 'inventory',
         ('__module__',), 'source-pin'),
        ('inspect.py', 2268, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 2334, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2335, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2336, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2339, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2343, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2347, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2349, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2353, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2357, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2359, 'inventory',
         ('args',), 'source-pin'),
        ('inspect.py', 2368, 'inventory',
         ('__self__',), 'source-pin'),
        ('inspect.py', 2378, 'inventory',
         ('empty',), 'source-pin'),
        ('inspect.py', 2390, 'inventory',
         ('__text_signature__',), 'source-pin'),
        ('inspect.py', 2410, 'inventory',
         ('__text_signature__',), 'source-pin'),
        ('inspect.py', 2414, 'inventory',
         ('_parameter_cls',), 'source-pin'),
        ('inspect.py', 2417, 'inventory',
         ('__code__',), 'source-pin'),
        ('inspect.py', 2425, 'inventory',
         ('__defaults__',), 'source-pin'),
        ('inspect.py', 2426, 'inventory',
         ('__kwdefaults__',), 'source-pin'),
        ('inspect.py', 2495, 'inventory',
         ('__get__',), 'source-pin'),
        ('inspect.py', 2527, 'inventory',
         ('__func__',), 'source-pin'),
        ('inspect.py', 2538, 'inventory',
         ('__signature__',), 'source-pin'),
        ('inspect.py', 2547, 'inventory',
         ('__signature__',), 'source-pin'),
        ('inspect.py', 2567, 'inventory',
         ('_partialmethod',), 'source-pin'),
        ('inspect.py', 2606, 'inventory',
         ('func',), 'source-pin'),
        ('inspect.py', 2623, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 2625, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 2631, 'inventory',
         ('__dict__',), 'source-pin'),
        ('inspect.py', 2637, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 2646, 'inventory',
         ('__text_signature__',), 'source-pin'),
        ('inspect.py', 2658, 'inventory',
         ('__mro__',), 'source-pin'),
        ('inspect.py', 2661, 'inventory',
         ('__init__',), 'source-pin'),
        ('inspect.py', 2662, 'inventory',
         ('__new__',), 'source-pin'),
        ('inspect.py', 2671, 'inventory',
         ('__call__',), 'source-pin'),
        ('inspect.py', 2695, 'inventory',
         ('__members__',), 'source-pin'),
        ('inspect.py', 2696, 'inventory',
         ('__new__',), 'source-pin'),
        ('inspect.py', 2760, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 2853, 'inventory',
         ('__class__', '__name__'), 'source-pin'),
        ('inspect.py', 2996, 'inventory',
         ('__class__', '__name__'), 'source-pin'),
        ('inspect.py', 3298, 'inventory',
         ('__class__', '__name__'), 'source-pin'),
        ('inspect.py', 3396, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 3405, 'inventory',
         ('*',), 'source-pin'),
        ('inspect.py', 3407, 'inventory',
         ('__name__',), 'source-pin'),
        ('inspect.py', 3414, 'inventory',
         ('__cached__',), 'source-pin'),
        ('inspect.py', 3416, 'inventory',
         ('__loader__',), 'source-pin'),
        ('inspect.py', 3417, 'inventory',
         ('__path__',), 'source-pin'),
        ('inspect.py', 3418, 'inventory',
         ('__path__',), 'source-pin'),
        ('unittest/loader.py', 27, 'inventory',
         ('__init__',), 'source-pin'),
        ('unittest/loader.py', 31, 'inventory',
         ('__getattr__',), 'source-pin'),
        ('unittest/loader.py', 75, 'inventory',
         ('__init__',), 'source-pin'),
        ('unittest/loader.py', 92, 'inventory',
         ('runTest',), 'source-pin'),
        ('unittest/loader.py', 100, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('unittest/loader.py', 101, 'inventory',
         ('*',), 'source-pin'),
        ('unittest/loader.py', 109, 'inventory',
         ('load_tests',), 'source-pin'),
        ('unittest/loader.py', 116, 'inventory',
         ('__name__',), 'source-pin'),
        ('unittest/loader.py', 152, 'inventory',
         ('*',), 'source-pin'),
        ('unittest/loader.py', 155, 'inventory',
         ('__path__',), 'source-pin'),
        ('unittest/loader.py', 187, 'inventory',
         ('*',), 'source-pin'),
        ('unittest/loader.py', 216, 'inventory',
         ('*',), 'source-pin'),
        ('unittest/loader.py', 220, 'inventory',
         ('__module__', '__qualname__'), 'source-pin'),
        ('unittest/loader.py', 224, 'inventory',
         ('*', '__dir__'), 'source-pin'),
        ('unittest/loader.py', 292, 'inventory',
         ('__file__',), 'source-pin'),
        ('unittest/loader.py', 294, 'inventory',
         ('__name__',), 'source-pin'),
        ('unittest/loader.py', 316, 'inventory',
         ('__file__',), 'source-pin'),
        ('unittest/loader.py', 406, 'inventory',
         ('__file__',), 'source-pin'),
        ('unittest/loader.py', 438, 'inventory',
         ('load_tests',), 'source-pin'),
        ('_pytest/python.py', 596, 'module',
         ('setUpModule', 'setup_module'), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 599, 'module',
         ('tearDownModule', 'teardown_module'), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 630, 'module',
         ('setup_function',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 631, 'module',
         ('teardown_function',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 702, 'module',
         ('setUpModule', 'setup_module'), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 708, 'module',
         ('tearDownModule', 'teardown_module'), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 815, 'class',
         ('setup_class',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 816, 'class',
         ('teardown_class',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 847, 'class',
         ('setup_method',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/python.py', 849, 'class',
         ('teardown_method',), 'receiver: indirect names passed to _get_first_non_fixture_func; lookup lines (760,)'),
        ('_pytest/config/__init__.py', 576, 'function',
         ('tryfirst', 'trylast', 'optionalhook', 'hookwrapper'), 'receiver: indirect names passed to _get_legacy_hook_marks; lookup lines (472,)'),
        ('_pytest/config/__init__.py', 587, 'function',
         ('firstresult', 'historic'), 'receiver: indirect names passed to _get_legacy_hook_marks; lookup lines (472,)'),
        ('inspect.py', 2614, 'class',
         ('__call__',), 'receiver: indirect names passed to _signature_get_user_defined_method; lookup lines (2036, 2038)'),
        ('inspect.py', 2618, 'class',
         ('__new__',), 'receiver: indirect names passed to _signature_get_user_defined_method; lookup lines (2036, 2038)'),
        ('inspect.py', 2619, 'class',
         ('__init__',), 'receiver: indirect names passed to _signature_get_user_defined_method; lookup lines (2036, 2038)'),
        ('pluggy/_manager.py', 278, 'function',
         ('pytest_impl',), 'project: pytest from _pytest/config/__init__.py:506; _get_marker_opts reads at 117 and 122'),
        ('pluggy/_manager.py', 393, 'function',
         ('pytest_spec',), 'project: pytest from _pytest/config/__init__.py:506; _get_marker_opts reads at 117 and 122'),
        ('inspect.py', 176, 'class value function',
         ('__annotations__', '__dict__', '__globals__', '__module__', '__name__', '__type_params__', '__wrapped__', 'get'), 'receiver: transitive get_annotations; exact sites pinned below'),
        ('inspect.py', 310, 'class value function',
         ('__get__', '__set__'), 'receiver: transitive ismethoddescriptor; exact sites pinned below'),
        ('inspect.py', 330, 'class value function',
         ('__delete__', '__set__'), 'receiver: transitive isdatadescriptor; exact sites pinned below'),
        ('inspect.py', 391, 'class value function',
         ('__code__', '__func__'), 'receiver: transitive _has_code_flag; exact sites pinned below'),
        ('inspect.py', 412, 'class value function',
         ('__func__', '_is_coroutine_marker'), 'receiver: transitive _has_coroutine_mark; exact sites pinned below'),
        ('inspect.py', 545, 'class',
         ('*', '__abstractmethods__', '__bases__', '__dict__', '__flags__', '__isabstractmethod__'), 'receiver: transitive isabstract; exact sites pinned below'),
        ('inspect.py', 754, 'class value function',
         ('__wrapped__',), 'receiver: transitive unwrap; exact sites pinned below'),
        ('inspect.py', 1797, 'class value function',
         ('__getattribute__',), 'receiver: transitive _check_instance; exact sites pinned below'),
        ('inspect.py', 1806, 'class value function',
         ('__dict__',), 'receiver: transitive _check_class; exact sites pinned below'),
        ('inspect.py', 1814, 'class value function',
         ('__name__', '__objclass__'), 'receiver: transitive _shadowed_dict_from_weakref_mro_tuple; exact sites pinned below'),
        ('inspect.py', 1845, 'class value function',
         ('__dict__',), 'receiver: transitive getattr_static; exact sites pinned below'),
        ('inspect.py', 2030, 'class value function',
         ('*',), 'receiver: transitive _signature_get_user_defined_method; exact sites pinned below'),
        ('inspect.py', 2164, 'class value function',
         ('__annotations__', '__code__', '__defaults__', '__kwdefaults__', '__name__'), 'receiver: transitive _signature_is_functionlike; exact sites pinned below'),
        ('inspect.py', 2189, 'class value function',
         ('__next__',), 'receiver: transitive _signature_strip_non_python_syntax; exact sites pinned below'),
        ('inspect.py', 2239, 'class value function',
         ('__dict__', '__module__', '__self__', '_parameter_cls', 'args', 'empty'), 'receiver: transitive _signature_fromstr; exact sites pinned below'),
        ('inspect.py', 2381, 'class value function',
         ('__text_signature__',), 'receiver: transitive _signature_from_builtin; exact sites pinned below'),
        ('inspect.py', 2397, 'class value function',
         ('__code__', '__defaults__', '__kwdefaults__', '__text_signature__', '_parameter_cls'), 'receiver: transitive _signature_from_function; exact sites pinned below'),
        ('inspect.py', 2492, 'class value function',
         ('__get__',), 'receiver: transitive _descriptor_get; exact sites pinned below'),
        ('inspect.py', 2501, 'class value function',
         ('__call__', '__dict__', '__func__', '__init__', '__mro__', '__new__', '__signature__', '__text_signature__', '_partialmethod', 'func'), 'receiver: transitive _signature_from_callable; exact sites pinned below'),
        ('inspect.py', 1666, 'class value function',
         ('__new__',), 'receiver: transitive __new__; exact sites pinned below'),
        ('inspect.py', 1738, 'class value function',
         ('__new__',), 'receiver: transitive __new__; exact sites pinned below'),
        ('inspect.py', 2694, 'class value function',
         ('__members__', '__new__'), 'receiver: transitive __new__; exact sites pinned below'),
        ('inspect.py', 2743, 'class value function',
         ('__name__',), 'receiver: transitive __init__; exact sites pinned below'),
        ('unittest/loader.py', 210, 'class',
         ('*', '__dir__', '__module__', '__qualname__'), 'receiver: transitive getTestCaseNames; exact sites pinned below'),
    ),
}

# The installed-source lookup inventory is below. A declaration name alone is
# never collection evidence: all module bindings use the same lookup audit.


# Executable admission table: absence means unavailable, including future AST
# kinds. Conditional handlers below constrain the operations, not just the kind.
MODELLED_OPERATIONS = {
    **dict.fromkeys((ast.Expr, ast.Assign, ast.AnnAssign, ast.NamedExpr, ast.Return),
                    "Follow evaluated values/targets; postponed annotations are names/strings, not executed."),
    **dict.fromkeys((ast.Pass, ast.Break, ast.Continue, ast.Load, ast.Store, ast.Del),
                    "Syntax or control transfer; no repository call."),
    ast.Constant: "Literal value; no repository call.",
    ast.Name: "Resolve all binding candidates; reads alone do not call them.",
    ast.NamedExpr: "Bind and retain the resolved value; an unknown result stays unavailable.",
    **dict.fromkeys((ast.List, ast.Tuple, ast.Set, ast.Dict),
                    "Build values; elements are followed and hash operands must be plain."),
    **dict.fromkeys((ast.FunctionDef, ast.AsyncFunctionDef),
                    "Follow defaults and decorator results; follow annotations only without the future import; bind modeled defaults on calls."),
    ast.ClassDef: "Follow bases/metaclasses and hooks on real receivers; audit implicit protocols, native new identity and inherited builder fields. Admitted installed bases use native/generated policies or source-read lifecycle routes with repository dispatch followed.",
    **dict.fromkeys((ast.arguments, ast.arg, ast.keyword, ast.alias),
                    "Syntax wrapper; evaluated children are followed."),
    ast.Lambda: "Defer body; follow defaults and called/factory/decorator bodies in lexical scopes that skip classes.",
    ast.Call: "Follow repository callees with arguments/defaults; external calls only by MODELLED_EXTERNAL_CALLS, environment/list policies and owner mark rules.",
    ast.Attribute: "Follow module missing-name hooks and class/instance method lookup on checked receivers/metaclasses; unknown descriptors/results unavailable.",
    ast.Subscript: "Resolve literal list/tuple/dict indexing with constant keys on unchanged containers; plain builtin/typing operations and classified environment writes retain their guards; unknown results unavailable.",
    ast.Import: "Repository modules are read separately; namespace portions must be unique and inside roots.",
    ast.ImportFrom: "Resolve relative levels; refuse stars, lazy module lookup and ambiguous namespace portions.",
    **dict.fromkeys((ast.Global, ast.Nonlocal),
                    "Redirect assignments to every candidate in the named lexical scope."),
    ast.If: "Follow both branches and a plain predicate; omit the guarded main body.",
    **dict.fromkeys((ast.For, ast.While), "Plain iteration/predicate; re-read until bindings and mutated-container sets stop changing."),
    **dict.fromkeys((ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension),
                    "Plain iterables and operands; comprehension names skip enclosing classes; re-read mutations."),
    ast.With: "Installed contextlib.nullcontext; bind its actual enter_result, follow body/arguments and require plain values for tuple/list targets.",
    ast.withitem: "Follow the modeled context expression and binding target.",
    **dict.fromkeys((ast.Try, ast.ExceptHandler), "Follow every body and exception expression."),
    ast.Match: "Plain subject and literal/name patterns only; follow every case.",
    **dict.fromkeys((ast.match_case, ast.MatchValue, ast.MatchSingleton, ast.MatchAs, ast.MatchStar, ast.MatchOr),
                    "Literal or binding pattern; evaluated children are followed."),
    **dict.fromkeys((ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.AugAssign),
                    "Only plain operands; repository operator and truth hooks unavailable."),
    **dict.fromkeys((ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
                     ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd, ast.Invert,
                     ast.Not, ast.UAdd, ast.USub, ast.And, ast.Or, ast.Eq, ast.NotEq,
                     ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot, ast.In, ast.NotIn),
                    "Operator token; enclosing operation checks operands."),
}


def module_names(file, roots=(), *, full=False):
    from g6.workforce.repo_tests import _import_names

    return _import_names(file, (".",) if full else (*roots, ".", "src"))


def _candidates(binding):
    return binding if isinstance(binding, frozenset) else (binding,)


def _merge(bindings):
    held = set()
    for binding in bindings:
        component_process.checkpoint()
        held.update(_candidates(binding))
    candidates = frozenset(held)
    return next(iter(candidates)) if len(candidates) == 1 else candidates or "?unresolved"


def _attributes(binding, attributes):
    """Append identifier parts, keeping a local file identity opaque."""
    if isinstance(binding, frozenset):
        return _merge(_attributes(candidate, attributes) for candidate in binding)
    if isinstance(binding, tuple):
        kind, file, line, held = binding
        return kind, file, line, (*held, *attributes)
    return ".".join((binding, *attributes))


def _internal(binding):
    if isinstance(binding, frozenset):
        return all(_internal(candidate) for candidate in binding)
    return isinstance(binding, tuple) or binding.startswith("?")


def _external(binding):
    if isinstance(binding, frozenset):
        return any(_external(candidate) for candidate in binding)
    return (isinstance(binding, str) and binding.startswith("?external") or
            isinstance(binding, tuple) and binding[0] == "?external-call")


def _owned_container(binding, file, kinds=("?list", "?dict")):
    if isinstance(binding, frozenset):
        return all(_owned_container(candidate, file, kinds) for candidate in binding)
    return isinstance(binding, tuple) and binding[0] in kinds and binding[1] == file


def _nodes(body):
    pending = deque(body)
    while pending:
        component_process.checkpoint()
        node = pending.popleft()
        yield node
        if not isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(node))


class _Scope:
    def __init__(self, reader, file, body, parent=None, arguments=(), instance=None, *, class_body=False, values=()):
        self.reader, self.file, self.parent = reader, file, parent
        self.class_body = class_body
        self.bindings = {name: "?argument" for name in arguments}
        self.bindings.update(zip(arguments, values))
        if instance:
            self.bindings[instance] = "?instance"
        self.nodes = list(_nodes(body))
        self.globals = {name for node in self.nodes if isinstance(node, ast.Global) for name in node.names}
        self.nonlocals = {name for node in self.nodes if isinstance(node, ast.Nonlocal) for name in node.names}
        slots = {name: [value] for name, value in self.bindings.items()}
        self.slots, self.writes, self.write_values = slots, {}, {}
        self.assignments, self.context_assignments = [], []

        def bind(name, value):
            held = slots.setdefault(name, [])
            held.append(value)
            self.bindings[name] = _merge(held)
            return len(held) - 1

        assignments = self.assignments

        def assignment(target, value):
            if isinstance(target, ast.Name):
                assignments.append((target.id, bind(target.id, "?unresolved"), value))
            elif isinstance(target, (ast.Tuple, ast.List)):
                if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
                    for item, item_value in zip(target.elts, value.elts):
                        assignment(item, item_value)
                else:
                    for part in ast.walk(target):
                        if isinstance(part, ast.Name):
                            bind(part.id, "?unresolved")

        for node in self.nodes:
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                key = self.key("@", node)
                bind(node.name, key)
                reader.definitions[key] = (self, node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    bind(alias.asname or alias.name.split(".")[0], alias.name if alias.asname else alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                module = reader.import_module(file, node)
                for alias in node.names:
                    binding = ".".join(part for part in (module, alias.name) if part)
                    if node.level and not module:
                        root_files = reader.paths.get("", ())
                        binding = ("?module", root_files[0], 0, (alias.name,)) if len(root_files) == 1 else "?unresolved"
                    bind(alias.asname or alias.name, binding)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if node.value is not None:
                    for target in targets:
                        assignment(target, node.value)
            elif isinstance(node, ast.NamedExpr):
                assignment(node.target, node.value)
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                bind(node.target.id, "?unresolved")
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                def context_target(target, path=()):
                    if isinstance(target, ast.Name):
                        self.context_assignments.append((target.id, bind(target.id, "?unresolved"),
                                                         node.context_expr, path))
                    elif isinstance(target, (ast.Tuple, ast.List)):
                        for index, part in enumerate(target.elts):
                            context_target(part, (*path, (index, len(target.elts))))
                context_target(node.optional_vars)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                target = node.target
                for part in ast.walk(target) if target is not None else ():
                    if isinstance(part, ast.Name):
                        bind(part.id, self.resolve(node) if isinstance(node, ast.For) else "?unresolved")
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bind(node.name, "?unresolved")
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                bind(node.name, "?unresolved")
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bind(node.rest, "?unresolved")
        # Resolve aliases using bindings, not the spelling of a callable. A
        # bounded fixed point leaves cycles and unavailable assignments unknown.
        for _ in range(MAX_CALL_DEPTH):
            if not self.refresh():
                break

    def refresh(self):
        """Recompute construction-time resolutions without retaining stale unknowns."""
        changed = False
        assignments = list(self.assignments)
        for name, slot, expression, path in self.context_assignments:
            value = self.context_value(expression)
            for index, size in path:
                if not isinstance(value, (ast.Tuple, ast.List)) or len(value.elts) != size:
                    value = None
                    break
                value = value.elts[index]
            assignments.append((name, slot, value))
        for name, slot, value in assignments:
            component_process.checkpoint()
            resolved = self.resolve(value) if value is not None else "?unresolved"
            if self.slots[name][slot] != resolved:
                self.slots[name][slot], changed = resolved, True
                self.rebind(name)
        for name in self.globals | self.nonlocals:
            target = self.redirect(name)
            if target is not None and name in self.bindings:
                target.write(name, self.bindings[name], (self, name))
        return changed

    def rebind(self, name):
        binding = _merge((*self.slots.get(name, ()), *self.writes.get(name, {}).values()))
        if self.bindings.get(name) != binding:
            self.bindings[name] = binding
            self.reader.revision += 1

    def write(self, name, binding, source, value=None):
        self.writes.setdefault(name, {})[source] = binding
        if value is not None:
            sources = self.write_values.setdefault(name, {})
            if source not in sources:
                sources[source] = value
                # Constants share the plain-value model, but differing truth
                # assignments must still invalidate collection decisions.
                self.reader.revision += 1
        self.rebind(name)

    def redirect(self, name):
        if not self.parent:
            return None
        if name in self.globals:
            return self.reader.scopes.get(self.file)
        if name in self.nonlocals:
            parent = self.parent
            while parent.parent:
                if not parent.class_body and name in parent.bindings:
                    return parent
                parent = parent.parent
        return None

    def assign(self, target, value):
        """Refresh aliases at an executed assignment after a followed installer."""
        if value is None:
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(value.elts):
                for item, held in zip(target.elts, value.elts):
                    self.assign(item, held)
            return
        if isinstance(target, ast.Attribute):
            for candidate in _candidates(self.reader.follow(self.resolve(target.value))):
                owner = None
                if candidate == "?instance" and getattr(self, "class_receiver", False):
                    owner = getattr(self, "method_owner", None)
                elif candidate in self.reader.definitions:
                    parent, node = self.reader.definitions[candidate]
                    if isinstance(node, ast.ClassDef):
                        owner = self.reader._function_scope(parent, node)
                if owner is not None:
                    owner.write(target.attr, self.resolve(value), (self, target), (self, value))
            return
        if not isinstance(target, ast.Name):
            return
        owner = self.redirect(target.id) if self.parent and target.id in self.globals | self.nonlocals else self
        if owner is None:
            return
        owner.write(target.id, self.resolve(value), (self, target), (self, value))

    def key(self, kind, node):
        # Nested expressions can start at the same source position. Retain
        # the actual node and its evaluation scope in every registered model.
        return kind, self.file, (node, self), ()

    def literal(self, node):
        key = self.key(_LITERALS[type(node)], node)
        self.reader.literal_values[key] = (self, node)
        return key

    def context_value(self, expression):
        """The exact value nullcontext.__enter__ returns; no context fallback."""
        if (not isinstance(expression, ast.Call) or
                self.reader.follow(self.resolve(expression.func)) != "contextlib.nullcontext" or
                len(expression.args) > 1 or any(isinstance(arg, ast.Starred) for arg in expression.args) or
                any(keyword.arg != "enter_result" for keyword in expression.keywords) or
                len(expression.args) + len(expression.keywords) > 1):
            return None
        return (expression.args[0] if expression.args else expression.keywords[0].value if expression.keywords
                else ast.Constant(value=None))

    def resolve(self, node):
        if isinstance(node, ast.Name):
            if node.id in self.globals | self.nonlocals and self.parent:
                target = self.redirect(node.id)
                return target.resolve(node) if target else "?unresolved"
            if node.id in self.bindings:
                return self.bindings[node.id]
            if self.parent:
                return self.parent.resolve(node)
            if node.id in ("__file__", "__name__"):
                return "?value"  # Native module strings, unless explicitly rebound above.
            return f"builtins.{node.id}" if node.id in _VALUE_BUILTINS | {
                "object", "type", "sorted", "staticmethod", "classmethod", "setattr", "delattr", "property"} else "?unresolved"
        if isinstance(node, ast.Lambda):
            parent = self
            while parent.class_body:
                parent = parent.parent
            key = self.key("?lambda", node)
            self.reader.lambdas[key] = (parent, node)
            return key
        if isinstance(node, ast.Attribute):
            return _attributes(self.resolve(node.value), (node.attr,))
        if isinstance(node, ast.Call):
            external = False
            for part in ast.walk(node):
                component_process.checkpoint()
                if isinstance(part, ast.Name):
                    name = self.resolve(part)
                    if _external(name) or not all(_internal(candidate) or candidate.startswith("builtins.")
                                                  for candidate in _candidates(name)):
                        external = True
            key = self.key("?external-call" if external else "?call", node)
            self.reader.calls[key] = (self, node)
            return key
        if isinstance(node, (ast.Subscript, ast.NamedExpr, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.For)):
            key = self.key("?expression", node)
            self.reader.expressions[key] = (self, node)
            return key
        if type(node) in _LITERALS:
            return self.literal(node)
        if isinstance(node, ast.Constant):
            return "?value"
        return "?unresolved"


class CollectionEffects:
    @classmethod
    def from_trees(cls, trees, *, roots=(), modules=(), files=(), collection_files=()):
        """Read a mapping of opaque file identities to parsed source trees."""
        return cls(trees, roots=roots, modules=modules, files=files, collection_files=collection_files)

    def __init__(self, trees, *, roots=(), modules=(), files=(), collection_files=()):
        from g6.workforce.repo_tests import _import_names

        self.trees, self.modules = trees, set(modules)
        self.collection_files = set(collection_files) | {file for file in trees if
            PurePosixPath(file).name == "conftest.py" or any(fnmatch.fnmatch(PurePosixPath(file).name, pattern)
                                                         for pattern in ("test_*.py", "*_test.py"))}
        self.paths, self.definitions, self.scopes = {}, {}, {}
        self.revision = 0
        self.roots = (*roots, ".", "src")
        self.namespace_portions = {}
        self.external_roots = any(PurePosixPath(root).is_absolute() or ".." in PurePosixPath(root).parts or
                                  ":" in root or "\\" in root for root in roots)
        files = set(trees) | set(files)
        self.names = {file: module_names(file, roots) for file in files}
        self.packages = {PurePosixPath(file).parent for file in files if PurePosixPath(file).name == "__init__.py"}
        self.calls = {}
        self.expressions, self.resolving_values, self.comprehension_scopes = {}, set(), {}
        self.container_additions, self.mutated_containers = {}, set()
        self.literal_values, self.dirty_containers = {}, set()
        self.decorating, self.returning, self.lambdas = set(), set(), {}
        self.function_scopes, self.active, self.protocol_active = {}, set(), set()
        self.lambda_scopes = {}
        self.module_nodes, self.mark_aliases = {}, {}
        self.installed_definitions = {}
        self.postponed_annotations = {file for file, tree in trees.items() if any(
            isinstance(node, ast.ImportFrom) and node.module == "__future__" and
            any(alias.name == "annotations" for alias in node.names) for node in tree.body)}
        for file in files:
            component_process.checkpoint()
            path = PurePosixPath(file)
            for root in self.roots:
                component_process.checkpoint()
                for module in _import_names(file, (root,)):
                    parts = module.split(".")
                    if path.name != "__init__.py":
                        parts = parts[:-1]
                    portion = path.parent
                    for end in range(len(parts), 0, -1):
                        module = ".".join(parts[:end])
                        self.namespace_portions.setdefault(module, set()).add(portion)
                        portion = portion.parent
            for name in self.names[file]:
                self.paths.setdefault(name, []).append(file)
                self.modules.add(name.split(".")[0])
            if PurePosixPath(file) == PurePosixPath("__init__.py"):
                self.paths.setdefault("", []).append(file)
        for file, tree in trees.items():
            self.scopes[file] = _Scope(self, file, tree.body)

    def import_module(self, file, node):
        if not node.level:
            return node.module or ""
        path, depth = PurePosixPath(file), 0
        directory = path.parent
        while directory in self.packages:
            component_process.checkpoint()
            depth += 1
            if directory == PurePosixPath("."):
                break
            directory = directory.parent
        if not depth:
            packages = {name.rpartition(".")[0] for name in self.names[file] if "." in name}
            if not packages or any(not self._namespace(package) for package in packages):
                return "?unresolved"
            resolved = set()
            for package in packages:
                try:
                    name = resolve_name("." * node.level + (node.module or ""), package)
                except (ImportError, ValueError):
                    return "?unresolved"
                if not self._import_path(name):
                    return "?unresolved"
                resolved.add(name)
            return self._same_import(resolved)
        root_package = directory == PurePosixPath(".") and directory in self.packages
        names = module_names(file, full=True) if root_package else self.names[file]
        packages = set()
        for module in names or ({""} if root_package and path.name == "__init__.py" else set()):
            parts = module.split(".") if module else []
            if path.name != "__init__.py":
                parts = parts[:-1]
            if root_package:
                # A private anchor represents the root's package level; it is
                # removed before lookup, never exposed as a repository name.
                parts.insert(0, "__g6_root__")
            if len(parts) >= depth:
                packages.add(".".join(parts))
        resolved = set()
        for package in packages:
            try:
                name = resolve_name("." * node.level + (node.module or ""), package)
            except (ImportError, ValueError):
                return "?unresolved"
            if root_package:
                name = "" if name == "__g6_root__" else name.removeprefix("__g6_root__.")
            resolved.add(name)
        if not resolved:
            return "?unresolved"
        if any(not self._import_path(name) for name in resolved):
            return "?unresolved"
        return self._same_import(resolved)

    def _namespace(self, name):
        portions = self.namespace_portions.get(name, ())
        return not self.external_roots and len(portions) == 1

    def _import_path(self, name):
        parts = name.split(".") if name else []
        for end in range(1, len(parts) + 1):
            prefix = ".".join(parts[:end])
            if prefix in self.paths:
                if len(self.paths[prefix]) != 1:
                    return False
            elif not self._namespace(prefix):
                return False
        return len(self.paths.get(name, ())) == 1 or self._namespace(name)

    def _same_import(self, resolved):
        if not resolved:
            return "?unresolved"
        if len(resolved) > 1:
            identities = [self.paths.get(name, ()) or tuple(self.namespace_portions.get(name, ())) for name in resolved]
            if any(len(files) != 1 for files in identities) or any(files != identities[0] for files in identities):
                return "?unresolved"
        return sorted(resolved)[0]

    def follow(self, name, seen=()):
        component_process.checkpoint()
        if isinstance(name, frozenset):
            return _merge(self.follow(candidate, seen) for candidate in name)
        if name in seen or len(seen) >= MAX_CALL_DEPTH:
            return "?unresolved"
        if name in self.expressions:
            if name in self.resolving_values:
                return "?unresolved"
            self.resolving_values.add(name)
            try:
                owner, expression = self.expressions[name]
                return self.follow(self._expression_value(owner, expression, (*seen, name)), (*seen, name))
            finally:
                self.resolving_values.remove(name)
        if name in self.definitions:
            if name[0] == "?original":
                return name
            return self._decorated(name, len(seen))
        if isinstance(name, tuple):
            kind, file, line, attributes = name
            if attributes and (kind, file, line, ()) in self.calls:
                call_scope, expression = self.calls[(kind, file, line, ())]
                members = []
                for constructor in _candidates(self.follow(call_scope.resolve(expression.func), (*seen, name))):
                    if constructor not in self.definitions or not isinstance(self.definitions[constructor][1], ast.ClassDef):
                        return "?unresolved"
                    owner, definition = self.definitions[constructor]
                    members.append(self._members(self._function_scope(owner, definition), attributes[0]))
                return self.follow(_attributes(_merge(members), attributes[1:]), (*seen, name))
            if kind == "?module":
                scope = self.scopes.get(file)
                if scope is None or not attributes:
                    return "?unresolved"
                reference = (kind, file, line, (attributes[0],))
                bindings = [candidate for candidate in _candidates(scope.bindings[attributes[0]])
                            if candidate != reference] if attributes[0] in scope.bindings else []
                if attributes[0] in self.paths:
                    bindings.append(attributes[0])
                return self.follow(_attributes(_merge(bindings), attributes[1:]), (*seen, name))
            if kind not in ("@", "?original"):
                return name
            definition = self.definitions.get((kind, file, line, ()))
            if not definition or not attributes or not isinstance(definition[1], ast.ClassDef):
                return "?unresolved"
            parent, node = definition
            scope = self._function_scope(parent, node)
            resolved = self._members(scope, attributes[0])
            return self.follow(_attributes(resolved, attributes[1:]), (*seen, name))
        if name.startswith("?"):
            return name
        parts = name.split(".")
        for end in range(len(parts), 0, -1):
            files = self.paths.get(".".join(parts[:end]), ())
            if not files:
                continue
            if len(files) != 1:
                return "?unresolved"
            if end == len(parts):
                return name
            scope = self.scopes.get(files[0])
            if scope is None:
                return "?unresolved"
            resolved = scope.bindings.get(parts[end], "?unresolved")
            if parts[end] not in scope.bindings and "__getattr__" in scope.bindings:
                resolved = self._returned(self.follow(scope.bindings["__getattr__"], (*seen, name)),
                                          ("?value",), len(seen) + 1)
            return self.follow(_attributes(resolved, parts[end + 1:]), (*seen, name))
        return name

    def _expression_value(self, scope, node, seen):
        """Resolve supported expressions, never turn a missing value into data."""
        if isinstance(node, ast.NamedExpr):
            return scope.resolve(node.value)
        if isinstance(node, ast.For):
            return "?value" if self._plain(node.iter, scope) else "?unresolved"
        if isinstance(node, ast.Subscript):
            if self._typing_subscript(node, scope):
                return "?value"  # An installed typing object with installed arguments.
            results = []
            for candidate in _candidates(self.follow(scope.resolve(node.value), seen)):
                if candidate not in self.literal_values or candidate in self.dirty_containers or candidate in self.mutated_containers:
                    return "?unresolved"
                owner, literal = self.literal_values[candidate]
                key = node.slice
                if isinstance(literal, (ast.List, ast.Tuple)) and isinstance(key, ast.Constant) and type(key.value) is int:
                    if not -len(literal.elts) <= key.value < len(literal.elts):
                        return "?unresolved"
                    results.append(owner.resolve(literal.elts[key.value]))
                elif isinstance(literal, ast.Dict) and isinstance(key, ast.Constant):
                    if not all(isinstance(held, ast.Constant) for held in literal.keys):
                        return "?unresolved"
                    matches = [value for held, value in zip(literal.keys, literal.values) if held.value == key.value]
                    if not matches:
                        return "?unresolved"
                    results.extend(owner.resolve(value) for value in matches)
                else:
                    return "?unresolved"
            return _merge(results)
        # These operations have a modeled builtin result only after every
        # operand has been resolved as plain. This is no unknown-value fallback.
        return "?value" if self._plain(node, scope) else "?unresolved"

    def _environment(self, node, scope):
        """None means another object; bool is the result for an environ write."""
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
            owners = _candidates(self.follow(scope.resolve(node.value)))
            if "os.environ" in owners:
                return (all(owner == "os.environ" for owner in owners) and
                        isinstance(node.ctx, ast.Store) and self._environment_key(node.slice))
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return None
        owners = _candidates(self.follow(scope.resolve(node.func.value)))
        if "os.environ" not in owners:
            return None
        if any(owner != "os.environ" for owner in owners):
            return False
        if node.func.attr in ("get", "copy", "keys", "values", "items", "__getitem__", "__contains__", "__iter__", "__len__"):
            return True
        if node.func.attr == "setdefault":
            return bool(node.args and len(node.args) <= 2 and not node.keywords and self._environment_key(node.args[0]))
        if node.func.attr != "update" or len(node.args) > 1:
            return False
        if any(keyword.arg is None or not self._environment_key(ast.Constant(keyword.arg)) for keyword in node.keywords):
            return False
        return not node.args or (isinstance(node.args[0], ast.Dict) and all(
            self._environment_key(key) for key in node.args[0].keys))

    @staticmethod
    def _environment_key(node):
        from g6.workforce.repo_tests import _runner_name

        return isinstance(node, ast.Constant) and isinstance(node.value, str) and not _runner_name(node.value)

    def _call(self, function, scope, depth, strict, *, call=None):
        if not isinstance(function, (ast.Name, ast.Attribute)):
            return False
        if not self._callable_lookup(function, scope, depth):
            return False
        name = self.follow(scope.resolve(function))
        return all(self._call_binding(candidate, function, scope, depth, strict, call=call)
                   for candidate in _candidates(name))

    def _call_binding(self, name, function, scope, depth, strict, *, call=None):
        # Preserve the owner's same-process exceptions; do not tighten the gate
        # against repository code deliberately shadowing these bindings.
        if (isinstance(name, str) and name.startswith("pytest.mark.") or self._legacy_mark(function, scope.file) or
                isinstance(function, ast.Name) and function.id in ("staticmethod", "classmethod") and
                name not in ("builtins.staticmethod", "builtins.classmethod")):
            return True
        if name in self.definitions:
            receiver = self._bound_receiver(function, scope, name)
            return self._callee(name, depth + 1, call=call, caller=scope, receiver=receiver,
                                method=receiver is not None)
        if name in self.lambdas:
            parent, expression = self.lambdas[name]
            if call is not None and (call.args or call.keywords):
                return False
            inner = self._lambda_scope(parent, expression, site=(scope, call))
            return self._scan([expression.body], inner, depth + 1, strict=True)
        if _internal(name):
            if isinstance(function, ast.Attribute):
                owner = self.follow(scope.resolve(function.value))
                if _owned_container(owner, scope.file, ("?list",)) and function.attr in ("append", "extend", "insert"):
                    if call is None or call.keywords:
                        return False
                    if function.attr == "extend":
                        return len(call.args) == 1 and self._plain(call.args[0], scope)
                    if function.attr == "insert":
                        return len(call.args) == 2 and self._plain(call.args[0], scope)
                    return len(call.args) == 1
            return False
        if name.split(".")[0] in self.modules:
            return False
        policy = MODELLED_EXTERNAL_CALLS.get(name, "").partition(":")[0]
        if not policy:
            return False
        if call is None:
            return policy in ("plain", "type", "allocate", "hold", "dataclass", "fixture", "wraps")
        values = [*call.args, *(keyword.value for keyword in call.keywords)]
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords):
            return False
        if policy == "plain":
            return all(self._plain(value, scope) for value in values)
        if policy == "constant":
            return all(self._installed_argument(value, scope) for value in values)
        if policy == "native-lookup":
            return self._native_lookup(call, scope, depth + 1)
        if policy == "sorted":
            if len(call.args) != 1 or not self._plain(call.args[0], scope):
                return False
            for keyword in call.keywords:
                if keyword.arg == "key":
                    if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None):
                        keys = _candidates(self.follow(scope.resolve(keyword.value)))
                        if not all(key in {"builtins." + item for item in _VALUE_BUILTINS} for key in keys):
                            return False
                elif keyword.arg != "reverse" or not self._plain(keyword.value, scope):
                    return False
            return True
        if policy == "type":
            return len(call.args) == 1 and not call.keywords
        if policy == "allocate":
            if len(call.args) != (0 if name == "builtins.object" else 1) or call.keywords:
                return False
            if name == "builtins.object.__new__":
                for allocated in _candidates(self.follow(scope.resolve(call.args[0]))):
                    if allocated == "?instance":
                        owner = getattr(scope, "method_owner", None)
                        if owner is None or not self._protocols(owner, depth + 1):
                            return False
                    elif allocated in self.definitions and isinstance(self.definitions[allocated][1], ast.ClassDef):
                        owner, definition = self.definitions[allocated]
                        if not self._protocols(self._function_scope(owner, definition), depth + 1):
                            return False
                    elif allocated != "builtins.object":
                        return False
            return True
        if policy == "logger":
            return len(call.args) <= 1 and not call.keywords and all(
                isinstance(value, ast.Name) and value.id == "__name__" or self._plain(value, scope)
                for value in values)
        if policy == "hold":
            if name == "contextlib.nullcontext":
                return len(call.args) <= 1 and all(k.arg == "enter_result" for k in call.keywords)
            if name == "builtins.property":
                if len(call.args) > 4 or any(k.arg not in ("fget", "fset", "fdel", "doc") for k in call.keywords):
                    return False
                accessors = [*call.args[:3], *(k.value for k in call.keywords if k.arg != "doc")]
                docs = [*call.args[3:], *(k.value for k in call.keywords if k.arg == "doc")]
                return all(self._function_value(value, scope, allow_none=True) for value in accessors) and all(
                    self._plain(value, scope) for value in docs)
            return len(call.args) == 1 and not call.keywords and self._function_value(call.args[0], scope)
        if policy == "field":
            return not call.args and all(keyword.arg in {
                "default", "default_factory", "init", "repr", "hash", "compare", "metadata", "kw_only", "doc"}
                and (keyword.arg in ("default", "default_factory") or self._plain(keyword.value, scope))
                for keyword in call.keywords)
        # Decorator factories are handled only at their application site, where
        # both the input and the returned function/class are known.
        return False

    def _all_nodes(self, file):
        if file not in self.module_nodes:
            nodes, pending = [], deque([self.trees[file]])
            while pending:
                component_process.checkpoint()
                node = pending.popleft()
                nodes.append(node)
                children = ast.iter_child_nodes(node)
                if file in self.postponed_annotations and isinstance(node, (ast.arg, ast.AnnAssign)):
                    children = (child for child in children if child is not node.annotation)
                elif file in self.postponed_annotations and isinstance(node, _FUNCTIONS):
                    children = (child for child in children if child is not node.returns)
                pending.extend(children)
            self.module_nodes[file] = nodes
        return self.module_nodes[file]

    def _legacy_mark(self, function, file):
        # Keep the existing whole-file import-alias treatment of pytest marks,
        # including nested aliases. The owner explicitly leaves deception via
        # those aliases within the same-process caveat.
        if file not in self.mark_aliases:
            aliases = {}
            for node in self._all_nodes(file):
                if isinstance(node, ast.Import):
                    aliases.update({alias.asname or alias.name.split(".")[0]: alias.name for alias in node.names})
                elif isinstance(node, ast.ImportFrom):
                    aliases.update({alias.asname or alias.name: f"{node.module or ''}.{alias.name}" for alias in node.names})
            self.mark_aliases[file] = aliases
        name = ast.unparse(function)
        head = name.split(".")[0]
        return (self.mark_aliases[file].get(head, head) + name[len(head):]).startswith("pytest.mark.")

    def _callee(self, name, depth, *, method=False, call=None, caller=None, receiver=None, values=None):
        if depth > MAX_CALL_DEPTH or name in self.active:
            return False
        self.active.add(name)
        try:
            parent, node = self.definitions[name]
            if isinstance(node, ast.AsyncFunctionDef):
                result = False
            elif isinstance(node, ast.ClassDef):
                result = self._class(parent, node, depth, instantiate=True, call=call, caller=caller)
            else:
                if values is None:
                    values = self._arguments(parent, node, call, caller, bound=method) if call is not None else ()
                if values is None:
                    return False
                scope = self._function_scope(parent, node, method=method, values=values, receiver=receiver)
                result = self._scan(node.body, scope, depth, strict=True)
            return result
        finally:
            self.active.remove(name)

    def _arguments(self, parent, node, call, caller, *, bound=False):
        """Bind explicit arguments; unsupported unpacking stays unavailable."""
        if any(isinstance(arg, ast.Starred) for arg in call.args) or any(k.arg is None for k in call.keywords):
            return None
        positional = [*node.args.posonlyargs, *node.args.args][1 if bound else 0:]
        arguments = [*positional, *node.args.kwonlyargs]
        values = dict(zip((arg.arg for arg in positional), (self._value_binding(caller, arg) for arg in call.args)))
        if len(call.args) > len(positional) and node.args.vararg is None:
            return None
        for keyword in call.keywords:
            if keyword.arg in values or keyword.arg in {arg.arg for arg in node.args.posonlyargs}:
                return None
            if keyword.arg not in {arg.arg for arg in arguments}:
                if node.args.kwarg is None:
                    return None
                continue
            values[keyword.arg] = self._value_binding(caller, keyword.value)
        defaults = self._defaults(parent, node)
        return (("?instance",) if bound else ()) + tuple(
            values.get(arg.arg, defaults.get(arg.arg, "?argument")) for arg in arguments)

    def _value_binding(self, scope, value):
        return scope.resolve(value)

    def _defaults(self, parent, node):
        positional = [*node.args.posonlyargs, *node.args.args]
        pairs = [*zip(positional[len(positional) - len(node.args.defaults):], node.args.defaults),
                 *zip(node.args.kwonlyargs, node.args.kw_defaults)]
        return {arg.arg: self._value_binding(parent, value) for arg, value in pairs if value is not None}

    def _function_scope(self, parent, node, *, method=False, values=(), receiver=None):
        declaration = parent
        owner = parent if parent.class_body else None
        while parent.class_body:
            parent = parent.parent
        key = (declaration, node, method, values, receiver)
        if key not in self.function_scopes:
            args = []
            if isinstance(node, _FUNCTIONS):
                args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
                args.extend(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
                # Implicit class hooks receive protocol/constructor arguments
                # whose bindings are not modeled here. Do not mistake those
                # unknown supplied arguments for an omitted default.
                defaults = {} if method else self._defaults(owner or parent, node)
                values = (*values, *(defaults.get(arg, "?argument") for arg in args[len(values):]))
            self.function_scopes[key] = _Scope(self, parent.file, node.body, parent, args,
                                              args[0] if method and args else None,
                                              class_body=isinstance(node, ast.ClassDef), values=values)
            self.function_scopes[key].method_owner = (receiver or owner) if method else None
            self.function_scopes[key].class_receiver = method and isinstance(node, _FUNCTIONS) and (
                node.name == "__init_subclass__" or any("builtins.classmethod" in _candidates(self.follow(
                    (owner or parent).resolve(decorator))) for decorator in node.decorator_list))
            self.function_scopes[key].class_node = node if isinstance(node, ast.ClassDef) else None
            self.function_scopes[key].declaration_scope = declaration
        return self.function_scopes[key]

    def _returned(self, name, values, depth, *, site=None):
        """Follow a visible decorator call and determine its callable result."""
        if isinstance(name, frozenset):
            return _merge(self._returned(candidate, values, depth, site=site) for candidate in name)
        if depth > MAX_CALL_DEPTH or name in self.returning:
            return "?unresolved"
        if name in self.lambdas:
            parent, node = self.lambdas[name]
            scope = self._lambda_scope(parent, node, values=values, site=site)
            return self._return_value(node.body, scope, depth + 1) if self._scan(
                [node.body], scope, depth + 1, strict=True) else "?unresolved"
        if name not in self.definitions:
            return "?unresolved"
        parent, node = self.definitions[name]
        if not isinstance(node, ast.FunctionDef):
            return "?unresolved"
        self.returning.add(name)
        try:
            scope = self._function_scope(parent, node, values=values)
            if not self._scan(node.body, scope, depth + 1, strict=True):
                return "?unresolved"
            returns = [part for part in scope.nodes if isinstance(part, ast.Return)]
            if not returns:
                return "?value"
            # A fall-through path returns None. Only a final return (or two
            # complete returning branches) establishes a determined result.
            if not self._always_returns(node.body):
                return "?unresolved"
            return _merge(self._return_value(part.value, scope, depth + 1) for part in returns)
        finally:
            self.returning.remove(name)

    def _lambda_scope(self, parent, node, *, values=(), site=None):
        # The callee and caller AST nodes, plus their enclosing evaluations,
        # distinguish call sites without allocating fresh identities per pass.
        key = (parent, node, values, site)
        if key not in self.lambda_scopes:
            args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            args.extend(arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg)
            self.lambda_scopes[key] = _Scope(self, parent.file, [node.body], parent, args, values=values)
        return self.lambda_scopes[key]

    @staticmethod
    def _always_returns(body):
        if not body:
            return False
        last = body[-1]
        return isinstance(last, ast.Return) or isinstance(last, ast.If) and (
            CollectionEffects._always_returns(last.body) and CollectionEffects._always_returns(last.orelse))

    def _return_value(self, value, scope, depth):
        if depth > MAX_CALL_DEPTH:
            return "?unresolved"
        if value is None or isinstance(value, ast.Constant):
            return "?value"
        if type(value) in _LITERALS:
            return scope.literal(value)
        if isinstance(value, ast.Lambda):
            key = scope.key("?lambda", value)
            self.lambdas[key] = (scope, value)
            return key
        if isinstance(value, ast.Call):
            if self._nonrepository_result(scope, value, depth + 1):
                return scope.resolve(value)
            if value.keywords or any(isinstance(arg, ast.Starred) for arg in value.args):
                return "?unresolved"
            return self._returned(self.follow(scope.resolve(value.func)),
                                  tuple(scope.resolve(arg) for arg in value.args), depth + 1, site=(scope, value))
        if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.NamedExpr,
                              ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
            binding = scope.resolve(value)
            # A decorator returning its input must retain the undecorated
            # identity; following that same definition would recurse here.
            return _merge(candidate if candidate in self.decorating else self.follow(candidate)
                          for candidate in _candidates(binding))
        return "?unresolved"

    def _decoration(self, decorator, scope, value, depth):
        function = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not self._callable_lookup(function, scope, depth):
            return "?unresolved"
        name = self.follow(scope.resolve(function))
        results = []
        is_function = value in self.definitions and isinstance(self.definitions[value][1], _FUNCTIONS)
        for candidate in _candidates(name):
            if candidate == "functools.wraps":
                originals = _candidates(self.follow(scope.resolve(decorator.args[0]))) if (
                    isinstance(decorator, ast.Call) and len(decorator.args) == 1 and not decorator.keywords) else ()
                if (originals and all(original in self.definitions and isinstance(
                        self.definitions[original][1], _FUNCTIONS) for original in originals) and
                        value in self.definitions and isinstance(self.definitions[value][1], _FUNCTIONS)):
                    results.append(value)
                else:
                    results.append("?unresolved")
            elif candidate in self.definitions or candidate in self.lambdas:
                if isinstance(decorator, ast.Call):
                    if decorator.keywords or any(isinstance(arg, ast.Starred) for arg in decorator.args):
                        results.append("?unresolved")
                        continue
                    callee = self._returned(candidate, tuple(scope.resolve(arg) for arg in decorator.args), depth + 1,
                                            site=(scope, decorator))
                else:
                    callee = candidate
                results.append(self._returned(callee, (value,), depth + 1, site=(scope, decorator, value)))
            elif (candidate in ("dataclasses.dataclass", "pytest.fixture", "builtins.staticmethod",
                                "builtins.classmethod", "builtins.property") or
                  isinstance(candidate, str) and candidate.startswith("pytest.mark.") or
                  self._legacy_mark(function, scope.file) or
                  isinstance(function, ast.Name) and function.id in ("staticmethod", "classmethod")):
                # Only known decorator applications carry these deferred
                # callbacks. Unknown keyword protocols do not get a free pass.
                if isinstance(decorator, ast.Call) and candidate == "dataclasses.dataclass" and (
                        decorator.args or any(keyword.arg not in {
                            "init", "repr", "eq", "order", "unsafe_hash", "frozen", "match_args", "kw_only", "slots",
                            "weakref_slot"} or not self._plain(keyword.value, scope) for keyword in decorator.keywords)):
                    results.append("?unresolved")
                elif isinstance(decorator, ast.Call) and candidate == "pytest.fixture" and (
                        decorator.args or any(keyword.arg not in ("scope", "params", "autouse", "ids", "name") or
                                              not self._plain(keyword.value, scope) for keyword in decorator.keywords)):
                    results.append("?unresolved")
                elif candidate in ("builtins.property", "pytest.fixture") and not is_function:
                    results.append("?unresolved")
                elif candidate in ("builtins.staticmethod", "builtins.classmethod") and not is_function:
                    results.append("?unresolved")
                elif candidate == "dataclasses.dataclass" and not (
                        value in self.definitions and isinstance(self.definitions[value][1], ast.ClassDef)):
                    results.append("?unresolved")
                else:
                    results.append(value)
            else:
                results.append("?unresolved")
        return _merge(candidate if candidate in self.definitions or candidate in self.lambdas else "?unresolved"
                      for result in results for candidate in _candidates(result))

    def _function_value(self, value, scope, *, allow_none=False):
        if allow_none and isinstance(value, ast.Constant) and value.value is None:
            return True
        return all(candidate in self.definitions and isinstance(self.definitions[candidate][1], _FUNCTIONS)
                   for candidate in _candidates(self.follow(scope.resolve(value))))

    def _decorated(self, name, depth):
        if depth > MAX_CALL_DEPTH or name in self.decorating:
            return "?unresolved"
        parent, node = self.definitions[name]
        self.decorating.add(name)
        try:
            # Keep the undecorated input distinct in closures. A wrapper that
            # calls its parameter calls the original body, not itself again.
            value = ("?original", *name[1:])
            self.definitions[value] = (parent, node)
            for decorator in reversed(node.decorator_list):
                value = self._decoration(decorator, parent, value, depth + 1)
            return value
        finally:
            self.decorating.remove(name)

    def _members(self, scope, name, seen=()):
        """Keep all possible inherited members; cycles/unknown bases fail closed."""
        if scope in seen or len(seen) >= MAX_CALL_DEPTH:
            return "?unresolved"
        if name in scope.bindings:
            return scope.bindings[name]
        members = []
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return "?unresolved"
                parent, node = self.definitions[candidate]
                inherited = self._members(self._function_scope(parent, node), name, (*seen, scope))
                if inherited != "?absent":
                    members.append(inherited)
        return _merge(members) if members else "?absent"

    def _bound_receiver(self, function, caller, binding):
        parent, node = self.definitions[binding]
        if not parent.class_body or not isinstance(node, _FUNCTIONS):
            return None
        decorators = {name for decorator in node.decorator_list for name in _candidates(self.follow(parent.resolve(
            decorator.func if isinstance(decorator, ast.Call) else decorator)))}
        if "builtins.staticmethod" in decorators:
            return None
        origin = caller.resolve(function.value) if isinstance(function, ast.Attribute) else caller.resolve(function)
        for candidate in _candidates(origin):
            if candidate in self.calls or isinstance(candidate, tuple) and candidate[0] in ("?call", "?external-call"):
                base = (*candidate[:3], ())
                if base not in self.calls:
                    continue
                owner, expression = self.calls[base]
                classes = _candidates(self.follow(owner.resolve(expression.func)))
                if len(classes) == 1 and classes[0] in self.definitions:
                    held, definition = self.definitions[classes[0]]
                    if isinstance(definition, ast.ClassDef):
                        return self._function_scope(held, definition)
            for resolved in _candidates(self.follow(candidate)):
                if "builtins.classmethod" in decorators and resolved in self.definitions:
                    held, definition = self.definitions[resolved]
                    if isinstance(definition, ast.ClassDef):
                        return self._function_scope(held, definition)
        return None

    def _hook(self, scope, name, depth, *, receiver=None, call=None, caller=None):
        """All local bindings of an implicitly invoked method must be read."""
        if name not in scope.bindings:
            return True
        for candidate in _candidates(self.follow(scope.bindings[name])):
            if candidate in self.definitions:
                method = scope.class_body
                values = ("?value",) if not method and name == "__getattr__" else None
                if not self._callee(candidate, depth + 1, method=method, receiver=receiver or scope,
                                    call=call, caller=caller, values=values):
                    return False
                if name in ("__getattribute__", "__getattr__", "__dir__", "__call__"):
                    parent, node = self.definitions[candidate]
                    held = self._function_scope(parent, node, method=method, receiver=receiver or scope, values=values or ())
                    if not all(part.value is None or self._plain(part.value, held) or
                               name == "__getattribute__" and self._native_lookup(part.value, held, depth + 1)
                               for part in held.nodes
                               if isinstance(part, ast.Return)):
                        # Lookup results may themselves carry protocols or be
                        # called by pytest. An undetermined result is not the
                        # statically declared member of the same name.
                        return False
            elif not self._call_binding(candidate, ast.Name(id=name), scope, depth, True):
                return False
        return True

    def _native_lookup(self, expression, scope, depth):
        """Exact native delegation preserves the receiver's audited member graph.

        Do not classify the result as plain: it may be a bound method. The
        collection holder's member audit still applies. Constructors, instance
        fields, descriptors and metaclass receivers need a richer result model.
        """
        owner = getattr(scope, "method_owner", None)
        if (depth > MAX_CALL_DEPTH or owner is None or not isinstance(expression, ast.Call) or
                self.follow(scope.resolve(expression.func)) != "builtins.object.__getattribute__" or
                len(expression.args) != 2 or expression.keywords or
                scope.resolve(expression.args[0]) != "?instance" or
                scope.resolve(expression.args[1]) != "?argument" or self._is_metaclass(owner) is not False):
            return False
        if any(isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and
               "?instance" in _candidates(self.follow(scope.resolve(node.value))) for node in scope.nodes):
            return False
        return self._native_lookup_members(owner, depth + 1)

    def _native_lookup_members(self, owner, depth, seen=()):
        if depth > MAX_CALL_DEPTH or owner in seen:
            return False
        if any(name in owner.bindings for name in ("__init__", "__new__", "__slots__")):
            return False
        for binding in list(owner.bindings.values()):
            if not self._descriptor(binding, depth + 1, dataclass=True):
                return False
            for candidate in _candidates(self.follow(binding)):
                if candidate in self.definitions:
                    parent, node = self.definitions[candidate]
                    if isinstance(node, _FUNCTIONS) and any("builtins.property" in _candidates(self.follow(
                            parent.resolve(decorator.func if isinstance(decorator, ast.Call) else decorator)))
                            for decorator in node.decorator_list):
                        return False
        for base in owner.class_node.bases:
            for candidate in _candidates(self.follow(owner.declaration_scope.resolve(base))):
                if candidate == "builtins.object":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._native_lookup_members(self._function_scope(parent, node), depth + 1, (*seen, owner)):
                    return False
        return True

    def _callable_lookup(self, function, scope, depth):
        """Do not call the declared member after a lookup replaced its value."""
        if depth > MAX_CALL_DEPTH:
            return False
        for raw in _candidates(scope.resolve(function)):
            if isinstance(raw, tuple) and raw[3]:
                owner = self.follow((*raw[:3], raw[3][:-1]))
            elif isinstance(raw, str) and "." in raw:
                owner = self.follow(raw.rpartition(".")[0])
            else:
                continue
            for candidate in _candidates(owner):
                if candidate in self.calls:
                    caller, expression = self.calls[candidate]
                    for constructor in _candidates(self.follow(caller.resolve(expression.func))):
                        if constructor in self.definitions and isinstance(self.definitions[constructor][1], ast.ClassDef):
                            parent, node = self.definitions[constructor]
                            if self._members(self._function_scope(parent, node), "__getattribute__") != "?absent":
                                return False
                elif candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._meta_intercepts(self._function_scope(parent, node), depth + 1):
                        return False
        return True

    def _meta_intercepts(self, scope, depth, seen=()):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return True
        for keyword in scope.class_node.keywords:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return True
                parent, node = self.definitions[candidate]
                if self._members(self._function_scope(parent, node), "__getattribute__") != "?absent":
                    return True
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if self._meta_intercepts(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                        return True
        return False

    def _protocols(self, scope, depth, *, receiver=None, metaclass=False, seen=()):
        """Admission shared by constructors and every implicit value consumer.

        A body in the allowed set is read on the actual receiver. Unknown
        protocols are never inferred inert from their name or an empty body.
        """
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        receiver = receiver or scope
        key = (scope, receiver, metaclass)
        if key in self.protocol_active:
            return False
        self.protocol_active.add(key)
        try:
            for name, binding in list(scope.bindings.items()):
                if not (name.startswith("__") and name.endswith("__")):
                    # Fixture discovery reads raw members, unwraps them, and
                    # looks them up on the class. Calls can produce descriptors
                    # just as decorators do; audit their results on this route.
                    if not self._descriptor(binding, depth + 1, dataclass=True):
                        return False
                    for candidate in _candidates(self.follow(binding)):
                        if candidate in self.definitions:
                            parent, node = self.definitions[candidate]
                            if isinstance(node, _FUNCTIONS) and any("builtins.property" in _candidates(
                                    self.follow(parent.resolve(decorator))) for decorator in node.decorator_list):
                                return False
                    continue
                for candidate in _candidates(self.follow(binding)):
                    if candidate == "?value" or candidate in self.literal_values:
                        continue
                    if name == "__new__" and candidate == "builtins.object.__new__":
                        continue
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], _FUNCTIONS):
                        return False
                    if name not in MODELLED_CLASS_PROTOCOLS and not (metaclass and name == "__call__"):
                        return False
                    if name == "__new__" and not self._native_new(*self.definitions[candidate]):
                        return False
                if name in ("__getattr__", "__getattribute__", "__setattr__") or metaclass and name == "__call__":
                    if not self._hook(scope, name, depth, receiver=receiver):
                        return False
            for base in scope.class_node.bases:
                for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                    if candidate in MODELLED_EXTERNAL_BASES:
                        continue
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                        return False
                    parent, node = self.definitions[candidate]
                    if not self._protocols(self._function_scope(parent, node), depth + 1,
                                           receiver=receiver, metaclass=metaclass, seen=(*seen, scope)):
                        return False
            return True
        finally:
            self.protocol_active.remove(key)

    def _native_new(self, parent, node):
        """Only allocation of the supplied class has a determined identity."""
        positional = [*node.args.posonlyargs, *node.args.args]
        body = [part for part in node.body if not (isinstance(part, ast.Expr) and
                isinstance(part.value, ast.Constant) and isinstance(part.value.value, str))]
        if not positional or len(body) != 1 or not isinstance(body[0], ast.Return):
            return False
        value = body[0].value
        return (isinstance(value, ast.Call) and self.follow(parent.resolve(value.func)) == "builtins.object.__new__"
                and not value.keywords and len(value.args) == 1 and isinstance(value.args[0], ast.Name)
                and value.args[0].id == positional[0].arg)

    def _dataclass(self, parent, node, depth):
        if depth > MAX_CALL_DEPTH:
            return False
        for decorator in node.decorator_list:
            function = decorator.func if isinstance(decorator, ast.Call) else decorator
            if "dataclasses.dataclass" in _candidates(self.follow(parent.resolve(function))):
                return True
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in self.definitions:
                    owner, definition = self.definitions[candidate]
                    if isinstance(definition, ast.ClassDef) and self._dataclass(owner, definition, depth + 1):
                        return True
        return False

    def _factory(self, value, scope, depth):
        if depth >= MAX_CALL_DEPTH:
            return False
        if isinstance(value, ast.Lambda):
            while scope.class_body:
                scope = scope.parent
            inner = self._lambda_scope(scope, value, site=(scope, value))
            return self._scan([value.body], inner, depth + 1, strict=True)
        return self._call(value, scope, depth, True)

    def _field_factories(self, scope, depth):
        for binding in list(scope.bindings.values()):
            component_process.checkpoint()
            for candidate in _candidates(self.follow(binding)):
                call = self.calls.get(candidate)
                if call is None:
                    continue
                owner, value = call
                functions = _candidates(self.follow(owner.resolve(value.func)))
                if "dataclasses.field" not in functions:
                    continue
                if any(function != "dataclasses.field" for function in functions):
                    return False
                for keyword in value.keywords:
                    if (keyword.arg is None or keyword.arg == "default_factory" and
                            not self._factory(keyword.value, owner, depth)):
                        return False
        return True

    def _descriptor(self, value, depth, *, dataclass=False):
        if depth > MAX_CALL_DEPTH:
            return False
        for candidate in _candidates(self.follow(value)):
            call = self.calls.get(candidate)
            if call is None:
                if not self._known_value(candidate):
                    return False
                if candidate in self.definitions:
                    owner, definition = self.definitions[candidate]
                    if isinstance(definition, ast.ClassDef) and not self._class_value_descriptor(
                            owner, definition, depth + 1, dataclass=dataclass):
                        return False
                continue
            scope, expression = call
            if self._nonrepository_result(scope, expression, depth + 1):
                continue
            for constructor in _candidates(self.follow(scope.resolve(expression.func))):
                if constructor == "builtins.type":
                    return False
                if constructor == "builtins.property":
                    # Construction holds the accessors; a later class/default
                    # lookup can execute them. That descriptor is not modeled.
                    if dataclass:
                        return False
                    continue
                if constructor in ("builtins.classmethod", "builtins.staticmethod"):
                    # These wrappers are inert only around a resolved function;
                    # classmethod can otherwise chain a caller's descriptor.
                    if (len(expression.args) != 1 or expression.keywords or
                            not self._function_value(expression.args[0], scope)):
                        return False
                    continue
                if constructor == "dataclasses.field":
                    if any(keyword.arg == "default" and not self._descriptor(
                            scope.resolve(keyword.value), depth + 1, dataclass=dataclass) for keyword in expression.keywords):
                        return False
                    continue
                if constructor == "contextlib.nullcontext":
                    if scope.context_value(expression) is None:
                        return False
                    continue
                if constructor == "builtins.object.__new__":
                    if len(expression.args) != 1 or expression.keywords:
                        return False
                    for allocated in _candidates(self.follow(scope.resolve(expression.args[0]))):
                        if allocated not in self.definitions or not isinstance(self.definitions[allocated][1], ast.ClassDef):
                            return False
                        owner, definition = self.definitions[allocated]
                        if not self._class(owner, definition, depth + 1, set_name=True):
                            return False
                        if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                            return False
                    continue
                if constructor in self.definitions:
                    owner, definition = self.definitions[constructor]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, set_name=True):
                        return False
                    if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                        return False
                else:
                    return False
        return True

    def _class_value_descriptor(self, parent, node, depth, *, dataclass):
        """A class used as a default is an instance of its effective metaclass."""
        if depth > MAX_CALL_DEPTH:
            return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(parent.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._class(owner, definition, depth + 1, set_name=True):
                    return False
                if dataclass and not self._plain_descriptor_class(owner, definition, depth + 1):
                    return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._class_value_descriptor(
                        owner, definition, depth + 1, dataclass=dataclass):
                    return False
        return True

    def _plain_descriptor_class(self, parent, node, depth):
        """Dataclass default extraction can read descriptors before __init__."""
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if not self._protocols(scope, depth):
            return False
        if any(method in scope.bindings for method in ("__get__", "__set__", "__delete__", "__getattribute__", "__getattr__")):
            return False
        if not self._metaclass_lookup(parent, node, "__hash__"):
            return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate == "builtins.object":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._plain_descriptor_class(owner, definition, depth + 1):
                    return False
        return True

    def _installed_definition(self, name, depth):
        """Read installed stdlib source, never import a repository-selected module.

        This is only reached for an already admitted external base. Reexports
        are resolved in source; native/generated bases retain their own policy.
        Missing source, ambiguous bindings and nontrivial inheritance refuse.
        """
        if depth > MAX_CALL_DEPTH or not isinstance(name, str):
            return None
        if name in self.installed_definitions:
            return self.installed_definitions[name]
        self.installed_definitions[name] = None
        module, _, attribute = name.rpartition(".")
        if module.split(".")[0] not in sys.stdlib_module_names:
            return None
        root = Path(sysconfig.get_path("stdlib"))
        path = root.joinpath(*module.split("."))
        try:
            package = path.is_dir()
            path = path / "__init__.py" if package else path.with_suffix(".py")
            # Resolving installed paths cannot grant a repository path authority.
            if not path.resolve().is_relative_to(root.resolve()):
                return None
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError, ValueError):
            return None
        found = []
        for part in tree.body:
            component_process.checkpoint()
            if isinstance(part, (ast.ClassDef, *_FUNCTIONS)) and part.name == attribute:
                found.append(part)
            elif isinstance(part, ast.ImportFrom):
                for alias in part.names:
                    if (alias.asname or alias.name) == attribute:
                        origin = part.module or ""
                        if part.level:
                            try:
                                origin = resolve_name("." * part.level + origin,
                                                      module if package else module.rpartition(".")[0])
                            except (ImportError, ValueError):
                                return None
                        found.append(self._installed_definition(origin + "." + alias.name, depth + 1))
            elif isinstance(part, (ast.Assign, ast.AnnAssign)):
                targets = part.targets if isinstance(part, ast.Assign) else [part.target]
                if any(isinstance(target, ast.Name) and target.id == attribute for target in targets):
                    found.append(None)
        result = found[0] if len(found) == 1 else None
        self.installed_definitions[name] = result
        return result

    def _installed_class_routes(self, name, receiver, depth, *, instantiate):
        """Follow receiver dispatch in an unmodified installed lifecycle body.

        The installed implementation owns its local operations, as with exact
        installed-call entries. That trust never covers repository overrides:
        every receiver member it touches is resolved on the repository MRO and
        its exact body is read. Receiver escapes, indirect calls, unsupported
        inheritance and uncertain callback results remain unavailable.
        """
        node = self._installed_definition(name, depth)
        if (not isinstance(node, ast.ClassDef) or node.decorator_list or node.keywords or
                any(not isinstance(base, ast.Name) or base.id != "object" for base in node.bases)):
            return False
        methods = {part.name: part for part in node.body if isinstance(part, _FUNCTIONS)}
        if len(methods) != sum(isinstance(part, _FUNCTIONS) for part in node.body):
            return False

        def follow_method(method, seen):
            component_process.checkpoint()
            if method not in methods:
                return True  # Native object lifecycle, or installed instance data.
            if method in seen or depth + len(seen) > MAX_CALL_DEPTH:
                return False
            body = methods[method]
            args = [*body.args.posonlyargs, *body.args.args]
            if not args or isinstance(body, ast.AsyncFunctionDef):
                return False
            owner = args[0].arg
            nodes = list(_nodes(body.body))
            parents = {child: part for part in nodes for child in ast.iter_child_nodes(part)}
            # Only calls whose source callee is determined may be trusted.
            # A parameter/local could hold a callback, even without a receiver
            # alias. Collect all stores first so source order cannot hide one.
            indirect = {argument.arg for argument in args}
            indirect.update(part.id for part in nodes if isinstance(part, ast.Name) and
                            isinstance(part.ctx, ast.Store))
            for part in nodes:
                component_process.checkpoint()
                if isinstance(part, (*_FUNCTIONS, ast.ClassDef, ast.Lambda)):
                    return False
                if isinstance(part, ast.Call):
                    head = part.func
                    while isinstance(head, ast.Attribute):
                        head = head.value
                    if isinstance(head, ast.Name) and head.id in indirect and head.id != owner:
                        return False
                    if isinstance(part.func, ast.Name) and part.func.id == owner:
                        return False
                    if isinstance(head, ast.Call) and not (isinstance(head.func, ast.Name) and
                            head.func.id == "super" and isinstance(part.func, ast.Attribute) and
                            part.func.attr == "__init_subclass__"):
                        return False
                if isinstance(part, ast.Name) and part.id == owner:
                    parent = parents.get(part)
                    if isinstance(parent, ast.Attribute) and parent.value is part:
                        continue
                    if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and
                            parent.func.id in ("getattr", "hasattr", "type") and parent.args and
                            parent.args[0] is part):
                        continue  # Attribute read/type inspection, never a callback call.
                    return False
                if not (isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name) and
                        part.value.id == owner):
                    continue
                member = self._members(receiver, part.attr)
                if member == "?absent":
                    if not follow_method(part.attr, (*seen, method)):
                        return False
                    continue
                for candidate in _candidates(self.follow(member)):
                    if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], _FUNCTIONS):
                        return False
                    if not self._callee(candidate, depth + len(seen) + 1, method=True, receiver=receiver):
                        return False
                    parent, definition = self.definitions[candidate]
                    held = self._function_scope(parent, definition, method=True, receiver=receiver)
                    if any(part.value is not None and not self._plain(part.value, held)
                           for part in held.nodes if isinstance(part, ast.Return)):
                        return False
            return True

        routes = ("__init_subclass__", "__new__", "__init__") if instantiate else ("__init_subclass__",)
        return all(follow_method(method, ()) for method in routes)

    def _class(self, parent, node, depth, *, instantiate=False, subclass=False, metaclass=False,
               prepare=False, set_name=False, post_init=False, receiver=None, call=None, caller=None):
        if depth > MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        receiver = receiver or scope
        if not self._class_metaprotocols(scope, depth):
            return False
        if instantiate:
            is_metaclass = self._is_metaclass(scope)
            if is_metaclass is None or not self._protocols(scope, depth, receiver=receiver, metaclass=is_metaclass):
                return False
        dataclass = self._dataclass(parent, node, depth)
        if dataclass and not all(self._metaclass_lookup(parent, node, attribute) for attribute in
                                 ("__dict__", "__annotations__", "__mro__", "__hash__")):
            return False
        if instantiate:
            post_init = post_init or self._dataclass(parent, node, depth)
        if not self._scan(node.body, scope, depth, strict=False):
            return False
        if dataclass and not self._class_fields(scope, depth):
            return False
        for binding in list(scope.bindings.values()):
            component_process.checkpoint()
            if not self._descriptor(binding, depth, dataclass=dataclass):
                return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass" or not self._call(keyword.value, parent, depth, True):
                return False
            for meta in _candidates(self.follow(parent.resolve(keyword.value))):
                if meta in self.definitions:
                    owner, definition = self.definitions[meta]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, instantiate=True, metaclass=instantiate, prepare=True):
                        return False
        for base in node.bases:
            for name in _candidates(self.follow(parent.resolve(base))):
                if name in self.definitions:
                    owner, definition = self.definitions[name]
                    if not isinstance(definition, ast.ClassDef) or not self._class(
                            owner, definition, depth + 1, instantiate=instantiate, subclass=True,
                            metaclass=metaclass, prepare=prepare, set_name=set_name, post_init=post_init,
                            receiver=receiver, call=call, caller=caller):
                        return False
                elif name not in MODELLED_EXTERNAL_BASES:
                    return False
                else:
                    # Use the table's canonical installed identifier, never a
                    # repository file identity, to locate installed source.
                    installed = next(base for base in MODELLED_EXTERNAL_BASES if base == name)
                    if MODELLED_EXTERNAL_BASES[installed].startswith("source:") and not (
                            self._installed_class_routes(installed, receiver, depth + 1, instantiate=instantiate)):
                        return False
        hooks = set()
        if subclass:
            hooks.add("__init_subclass__")
        if instantiate:
            hooks.update(("__init__", "__new__"))
        if metaclass:
            hooks.add("__call__")
        if prepare:
            hooks.add("__prepare__")
        if set_name:
            hooks.add("__set_name__")
        if post_init:
            hooks.update(("__post_init__", "__setattr__", "__getattribute__", "__getattr__"))
            if not self._field_factories(scope, depth):
                return False
        return all(self._hook(scope, hook, depth, receiver=receiver,
                              call=call if hook in ("__init__", "__new__") else None,
                              caller=caller) for hook in hooks)

    def _class_fields(self, scope, depth, seen=()):
        """Class builders read field defaults and annotations through the MRO."""
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        for part in scope.nodes:
            if isinstance(part, ast.AnnAssign) and isinstance(part.target, ast.Name):
                default = self._members(scope, part.target.id)
                if default != "?absent" and not self._descriptor(default, depth + 1, dataclass=True):
                    return False
                if scope.file not in self.postponed_annotations and not self._pytest_value(scope.resolve(part.annotation), depth + 1):
                    return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    if not self._class_fields(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                        return False
                elif candidate not in MODELLED_EXTERNAL_BASES:
                    return False
        return True

    def _is_metaclass(self, scope, seen=()):
        if scope in seen or len(seen) >= MAX_CALL_DEPTH:
            return None
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate == "builtins.type":
                    return True
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    inherited = self._is_metaclass(self._function_scope(parent, node), (*seen, scope))
                    if inherited is None:
                        return None
                    if inherited:
                        return True
                elif candidate not in MODELLED_EXTERNAL_BASES:
                    return None
        return False

    def _class_metaprotocols(self, scope, depth, seen=()):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return False
        for keyword in scope.class_node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._protocols(self._function_scope(parent, node), depth + 1, metaclass=True):
                    return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._class_metaprotocols(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                    return False
        return True

    def passive_all(self):
        """A certain collection decision requires stable repository-wide inputs.

        Each pass follows every module and all reached calls/holder protocols.
        Later writes invalidate every earlier decision, including writes made
        during holder auditing itself. A changing or bounded read never grants
        certainty. Parsing and cached node walks remain shared across passes.
        """
        for _ in range(MAX_CALL_DEPTH):
            component_process.checkpoint()
            revision, dirty = self.revision, set(self.dirty_containers)
            passive = True
            for file in self.trees:
                component_process.checkpoint()
                if not self.passive(file):
                    passive = False
            if self.revision == revision and self.dirty_containers == dirty:
                return passive
        return False

    def passive(self, file):
        # Hooks and declarations are refused even when nested, matching the
        # existing read. Function bodies otherwise matter only when called.
        for node in self._all_nodes(file):
            component_process.checkpoint()
            if (isinstance(node, _FUNCTIONS) and node.name.startswith("pytest_") or
                    isinstance(node, ast.Name) and (node.id in ("pytest_plugins", "collect_ignore", "collect_ignore_glob") or
                        isinstance(node.ctx, ast.Store) and node.id.startswith("pytest_")) or
                    isinstance(node, ast.ImportFrom) and any(
                        (alias.asname or alias.name).startswith("pytest_") for alias in node.names)):
                return False
        scope = self.scopes[file]
        if not self._scan(self.trees[file].body, scope, 0, strict=False):
            return False
        if file in self.collection_files:
            if not self._pytest_module(scope, 0, collected=True):
                return False
            return all(self._pytest_value(binding, 0, binding_name=name)
                       for name, binding in list(scope.bindings.items()))
        return True

    @staticmethod
    def _pytest_names(target):
        return {name for _, _, targets, attributes, _ in PYTEST_COLLECTION_LOOKUPS["reads"]
                if target in targets.split() for name in attributes}

    def _pytest_module(self, scope, depth, *, collected=False):
        # Every binding receives the __test__ probe (python.py:384). Only a
        # collection holder receives dir() (fixtures.py:2308); a helper module
        # bound in that holder is never itself passed to fixture discovery.
        hooks = ("__getattr__", "__dir__") if collected else ("__getattr__",)
        return all(self._hook(scope, name, depth) for name in hooks)

    def _nonrepository_result(self, scope, expression, depth):
        """Owner boundary: installed calls without repository arguments.

        This classifies later lookup/descriptor behavior only. The executed
        call still needs admission by _scan/_call; this never permits executing
        an unlisted external call. Importing a repository object by a string
        without receiving one remains the owner's same-process caveat.
        """
        if depth > MAX_CALL_DEPTH or not isinstance(expression, ast.Call):
            return False
        callees = _candidates(self.follow(scope.resolve(expression.func)))
        if not all(isinstance(name, str) and not _internal(name) and
                   name.split(".")[0] not in self.modules for name in callees):
            return False
        if any(isinstance(arg, ast.Starred) for arg in expression.args) or any(
                keyword.arg is None for keyword in expression.keywords):
            return False
        return all(self._nonrepository_argument(value, scope, depth + 1) for value in
                   [*expression.args, *(keyword.value for keyword in expression.keywords)])

    def _nonrepository_argument(self, value, scope, depth):
        if depth > MAX_CALL_DEPTH:
            return False
        if self._plain(value, scope):
            return True
        for candidate in _candidates(self.follow(scope.resolve(value))):
            if candidate in self.calls:
                owner, expression = self.calls[candidate]
                if not self._nonrepository_result(owner, expression, depth + 1):
                    return False
            elif candidate in self.literal_values:
                owner, literal = self.literal_values[candidate]
                if not isinstance(literal, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                    return False
                children = [*literal.keys, *literal.values] if isinstance(literal, ast.Dict) else literal.elts
                if not all(child is not None and self._nonrepository_argument(child, owner, depth + 1)
                           for child in children):
                    return False
            elif not (isinstance(candidate, str) and not _internal(candidate) and
                      candidate.split(".")[0] not in self.modules):
                return False
        return True

    def _known_value(self, candidate):
        """Identities with a model, rather than an arbitrary unresolved token."""
        return (candidate == "?value" or candidate in self.definitions or candidate in self.lambdas or
                candidate in self.calls or candidate in self.literal_values or
                isinstance(candidate, str) and not _internal(candidate) and (
                    candidate in self.paths or candidate.split(".")[0] not in self.modules))

    def _container_mutation(self, binding, value=None):
        for candidate in _candidates(binding):
            if candidate not in self.literal_values:
                continue  # The operation's receiver guard refuses unknowns.
            if candidate not in self.mutated_containers:
                self.mutated_containers.add(candidate)
                self.revision += 1
            if value is not None:
                additions = self.container_additions.setdefault(candidate, set())
                if value not in additions:
                    additions.add(value)
                    self.revision += 1

    def _pytest_value(self, binding, depth, seen=(), *, binding_name=None):
        if depth > MAX_CALL_DEPTH:
            return False
        candidates = _candidates(self.follow(binding))
        if not candidates:
            return False
        for candidate in candidates:
            if candidate in seen or not self._known_value(candidate):
                return False
            if candidate in self.definitions:
                parent, node = self.definitions[candidate]
                if isinstance(node, ast.ClassDef):
                    scope = self._function_scope(parent, node)
                    if not self._class_metaprotocols(scope, depth):
                        return False
                    # Only objects bound by name in PyCollector's __dict__
                    # iteration are collection candidates. Container contents
                    # retain protocol checks, never become collection holders.
                    collected = self._test_class(scope, depth, binding_name=binding_name) if binding_name is not None else False
                    if collected is None:
                        return False
                    if collected:
                        if not self._class(parent, node, depth + 1, instantiate=True):
                            return False
                        # PyCollector and fixture discovery also visit nested
                        # and inherited members, under each member's bound name.
                        if not self._pytest_members(scope, depth + 1, (*seen, candidate)):
                            return False
            elif candidate in self.calls:
                owner, expression = self.calls[candidate]
                if self._nonrepository_result(owner, expression, depth + 1):
                    continue
                for constructor in _candidates(self.follow(owner.resolve(expression.func))):
                    if constructor in self.definitions:
                        parent, node = self.definitions[constructor]
                        if isinstance(node, ast.ClassDef):
                            if not self._protocols(self._function_scope(parent, node), depth + 1):
                                return False
                        elif isinstance(node, ast.FunctionDef):
                            values = self._arguments(parent, node, expression, owner)
                            if values is None:
                                return False
                            returned = self._returned(constructor, values, depth + 1)
                            if returned == "?unresolved" or not self._pytest_value(
                                    returned, depth + 1, (*seen, candidate), binding_name=binding_name):
                                return False
                        else:
                            return False
                    elif constructor == "dataclasses.field":
                        if any(keyword.arg == "default" and not self._pytest_value(
                                owner.resolve(keyword.value), depth + 1, (*seen, candidate)) for keyword in expression.keywords):
                            return False
                    elif constructor in ("builtins.staticmethod", "builtins.classmethod"):
                        if (len(expression.args) != 1 or expression.keywords or
                                not self._function_value(expression.args[0], owner)):
                            return False
                    elif constructor == "contextlib.nullcontext":
                        if owner.context_value(expression) is None:
                            return False
                    elif constructor == "builtins.object.__new__":
                        if len(expression.args) != 1 or expression.keywords:
                            return False
                        for allocated in _candidates(self.follow(owner.resolve(expression.args[0]))):
                            if allocated not in self.definitions or not isinstance(self.definitions[allocated][1], ast.ClassDef):
                                return False
                            parent, node = self.definitions[allocated]
                            if not self._protocols(self._function_scope(parent, node), depth + 1):
                                return False
                    else:
                        # Executing a listed call and understanding the later
                        # lookup behavior of its result are different models.
                        return False
            elif candidate in self.literal_values:
                owner, value = self.literal_values[candidate]
                if isinstance(value, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
                    children = [value.elt]
                    owner = self.comprehension_scopes.get((owner, value))
                elif isinstance(value, ast.DictComp):
                    children = [value.key, value.value]
                    owner = self.comprehension_scopes.get((owner, value))
                else:
                    children = value.elts if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else [*value.keys, *value.values]
                if owner is None or not all(child is not None and self._pytest_value(owner.resolve(child), depth + 1, (*seen, candidate))
                           for child in children):
                    return False
                if not all(self._pytest_value(added, depth + 1, (*seen, candidate))
                           for added in self.container_additions.get(candidate, ())):
                    return False
            elif isinstance(candidate, str) and candidate in self.paths:
                files = self.paths[candidate]
                if len(files) != 1 or files[0] not in self.scopes or not self._pytest_module(self.scopes[files[0]], depth + 1):
                    return False
        return True

    def _pytest_members(self, scope, depth, seen, bases=()):
        if depth > MAX_CALL_DEPTH or scope in bases:
            return False
        names = self._pytest_names("class") | self._pytest_names("instance")
        for name, binding in list(scope.bindings.items()):
            component_process.checkpoint()
            if ("*" in names or name in names) and not self._pytest_value(
                    binding, depth, seen, binding_name=name):
                return False
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, node = self.definitions[candidate]
                if not self._pytest_members(self._function_scope(parent, node), depth + 1, seen, (*bases, scope)):
                    return False
        return True

    def _test_flag(self, scope, depth, seen=()):
        """Literal truth on every visible __test__ assignment, including bases.

        'absent' differs from an unresolved value (None). Unknown truth must not
        let a non-Test declaration escape an instance collection route.
        """
        if depth > MAX_CALL_DEPTH or scope in seen:
            return None
        if self._dynamic_test_flag(scope, depth):
            return None
        if "__test__" in scope.bindings:
            return self._assigned_truth(scope, "__test__", depth + 1)
        flags = []
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return None
                parent, node = self.definitions[candidate]
                flags.append(self._test_flag(self._function_scope(parent, node), depth + 1, (*seen, scope)))
        if None in flags:
            return None
        present = [flag for flag in flags if flag != "absent"]
        return any(present) if present else "absent"

    def _dynamic_test_flag(self, scope, depth, seen=()):
        # A hook's inert body does not establish the truth of the value it
        # substitutes for __test__. Until that result is modeled, refuse even
        # when the declaration name would otherwise avoid collection. A hook
        # can also replace an explicitly declared flag through __getattribute__.
        if depth > MAX_CALL_DEPTH or scope in seen:
            return True
        for keyword in scope.class_node.keywords:
            if keyword.arg != "metaclass":
                return True
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return True
                parent, node = self.definitions[candidate]
                meta = self._function_scope(parent, node)
                if any(self._members(meta, name) != "?absent" for name in
                       ("__getattribute__", "__getattr__", "__test__")):
                    return True
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate in MODELLED_EXTERNAL_BASES:
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return True
                parent, node = self.definitions[candidate]
                if self._dynamic_test_flag(self._function_scope(parent, node), depth + 1, (*seen, scope)):
                    return True
        return False

    def _assigned_truth(self, scope, name, depth, seen=()):
        key = (scope, name)
        if depth > MAX_CALL_DEPTH or key in seen:
            return None
        if name not in scope.bindings:
            return self._assigned_truth(scope.parent, name, depth + 1, seen) if scope.parent else None
        values, assignments = [], list(scope.write_values.get(name, {}).values())
        for part in scope.nodes:
            component_process.checkpoint()
            if not isinstance(part, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            targets = part.targets if isinstance(part, ast.Assign) else [part.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                assignments.append((scope, part.value))
        for owner, value in assignments:
            if isinstance(value, ast.Constant):
                values.append(bool(value.value))
            elif isinstance(value, ast.Name):
                values.append(self._assigned_truth(owner, value.id, depth + 1, (*seen, key)))
            else:
                return None
        return any(values) if values and None not in values else None

    def _test_class(self, scope, depth, seen=(), *, binding_name=None):
        if depth > MAX_CALL_DEPTH or scope in seen:
            return None
        flag = self._test_flag(scope, depth)
        if flag is None or flag is False:
            return flag
        if flag is True:
            return True
        # Naming configuration outside this default table remains unavailable
        # at _collection_config_passive; it is never guessed here.
        patterns = next(attributes[1:] for _, _, targets, attributes, _ in PYTEST_COLLECTION_LOOKUPS["reads"]
                        if targets == "patterns" and attributes[0] == "python_classes")
        if binding_name is not None and any(binding_name.startswith(pattern) or fnmatch.fnmatch(binding_name, pattern)
                                            for pattern in patterns):
            return True
        for base in scope.class_node.bases:
            for candidate in _candidates(self.follow(scope.declaration_scope.resolve(base))):
                if candidate == "unittest.TestCase":
                    return True
                if candidate in self.definitions and isinstance(self.definitions[candidate][1], ast.ClassDef):
                    parent, node = self.definitions[candidate]
                    inherited = self._test_class(self._function_scope(parent, node), depth + 1, (*seen, scope))
                    if inherited is None:
                        return None
                    if inherited:
                        return True
        return False

    def _scan(self, body, scope, depth, *, strict):
        if depth > MAX_CALL_DEPTH:
            return False
        scope.refresh()
        revision = self.revision
        dirty = set(self.dirty_containers)
        pending = list(reversed(body))
        while pending:
            component_process.checkpoint()
            node = pending.pop()
            if type(node) not in MODELLED_OPERATIONS:
                return False
            if isinstance(node, ast.If) and ast.unparse(node.test) in ("__name__ == '__main__'", "'__main__' == __name__"):
                pending.extend(reversed(node.orelse))
                continue
            if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                binding = scope.key("@", node)
                if "?unresolved" in _candidates(self._decorated(binding, depth)):
                    return False
                for decorator in node.decorator_list:
                    function = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(function, ast.Attribute) and not self._attribute_allowed(function, scope):
                        return False
                    # _decorated already followed repository decorators with
                    # the actual original binding. Re-entering without that
                    # binding would discard a nested wraps(function) input.
                    names = _candidates(self.follow(scope.resolve(function)))
                    if not all(name in self.definitions or name in self.lambdas or
                               self._call_binding(name, function, scope, depth, strict) for name in names):
                        return False
                    if isinstance(decorator, ast.Call):
                        callee = self.follow(scope.resolve(function))
                        if "pytest.fixture" in _candidates(callee) and any(
                                keyword.arg is None or keyword.arg == "autouse" and not (
                                    isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
                                for keyword in decorator.keywords):
                            return False
                        pending.extend([*decorator.args, *(keyword.value for keyword in decorator.keywords)])
                if isinstance(node, ast.ClassDef):
                    if not self._class(scope, node, depth):
                        return False
                    pending.extend([*node.bases, *(keyword.value for keyword in node.keywords)])
                else:
                    pending.append(node.args)
                    if node.returns and scope.file not in self.postponed_annotations:
                        pending.append(node.returns)
                continue
            if isinstance(node, ast.arg):
                if node.annotation and scope.file not in self.postponed_annotations:
                    pending.append(node.annotation)
                continue
            if isinstance(node, ast.Lambda):
                pending.append(node.args)
                continue
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                if not self._comprehension(node, scope, depth):
                    return False
                continue
            if isinstance(node, ast.For):
                if not self._plain(node.iter, scope):
                    return False
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    scope.assign(target, node.value)
                    if isinstance(target, ast.Subscript) and node.value is not None and self._environment(target, scope) is not True:
                        self._container_mutation(self.follow(scope.resolve(target.value)), scope.resolve(node.value))
                    if self._environment(target, scope) is True and (node.value is None or not self._plain(node.value, scope)):
                        return False
            if isinstance(node, (ast.Import, ast.ImportFrom)) and not self._import_allowed(node, scope):
                return False
            environment = self._environment(node, scope)
            if environment is False:
                return False
            if isinstance(node, ast.Attribute) and not self._attribute_allowed(node, scope):
                return False
            if isinstance(node, ast.Subscript) and environment is not True:
                owner = self.follow(scope.resolve(node.value))
                typing = self._typing_subscript(node, scope)
                if not typing and (not (_owned_container(owner, scope.file) or self._plain(node.value, scope)) or
                                   not self._plain(node.slice, scope)):
                    return False
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    self._container_mutation(owner)
                    self.dirty_containers.update(_candidates(owner))
            if not self._operands_allowed(node, scope):
                return False
            if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                root = node.value
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                origin = scope.resolve(root)
                if environment is not True:
                    owned_container = _owned_container(origin, scope.file)
                    if (not _internal(origin) or _external(origin) or
                            strict and not (origin == "?instance" or owned_container)):
                        return False
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    owner = self.follow(scope.resolve(node.func.value))
                    if (_owned_container(owner, scope.file, ("?list",)) and
                            node.func.attr in ("append", "extend", "insert") and node.args):
                        self._container_mutation(owner, scope.resolve(node.args[-1]))
                    if _owned_container(owner, scope.file) and not all(self._plain(value, scope) for value in
                            [*node.args, *(keyword.value for keyword in node.keywords)]):
                        self.dirty_containers.update(_candidates(owner))
                names = _candidates(self.follow(scope.resolve(node.func)))
                if any(isinstance(name, str) and name in {"builtins." + item for item in _VALUE_BUILTINS}
                       for name in names) and not all(self._plain(value, scope) for value in
                                                     [*node.args, *(keyword.value for keyword in node.keywords)]):
                    return False
                if "builtins.type" in names and len(node.args) != 1:
                    return False
                if any(name in ("builtins.setattr", "builtins.delattr") for name in names) and node.args:
                    root = node.args[0]
                    while isinstance(root, (ast.Attribute, ast.Subscript)):
                        root = root.value
                    origin = self.follow(scope.resolve(root))
                    if (not _internal(origin) or strict or any(candidate in self.definitions or candidate in self.calls
                                                              for candidate in _candidates(origin))):
                        return False
                if environment is True and not all(self._plain(value, scope) for value in
                                                   [*node.args, *(keyword.value for keyword in node.keywords)]):
                    return False
                if environment is not True and not self._call(node.func, scope, depth, strict, call=node):
                    return False
            if strict and isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.With, ast.AsyncWith)):
                return False
            children = list(ast.iter_child_nodes(node))
            if isinstance(node, ast.AnnAssign) and scope.file in self.postponed_annotations:
                # Dataclasses inspect field names/strings, without executing
                # calls embedded in postponed annotations. Defaults still run.
                children = [child for child in children if child is not node.annotation]
            pending.extend(reversed(children))
        # A followed call may add global/nonlocal candidates after an earlier
        # use in this body. Re-read against that enlarged candidate set.
        return (self.revision == revision and self.dirty_containers == dirty or
                self._scan(body, scope, depth + 1, strict=strict))

    def _import_allowed(self, node, scope):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            if any(alias.name == "*" for alias in node.names):
                return False
            module = self.import_module(scope.file, node)
            if module == "?unresolved":
                return False
            names = [module]
            for file in self.paths.get(module, ()):
                imported = self.scopes.get(file)
                if imported and "__getattr__" in imported.bindings:
                    return False
        for name in names:
            parts = name.split(".")
            for end in range(1, len(parts) + 1):
                prefix = ".".join(parts[:end])
                if prefix not in self.paths and prefix in self.namespace_portions and not self._namespace(prefix):
                    return False
        return True

    def _plain(self, node, scope, seen=()):
        """Values with no repository operator/iteration/subscription hooks."""
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # Its body was followed, but its produced element graph is not a
            # literal graph. Consumers requiring plain values stay unavailable.
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self._plain(item, scope, seen) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(key is not None and self._plain(key, scope, seen) and self._plain(value, scope, seen)
                       for key, value in zip(node.keys, node.values))
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
            return all(self._plain(child, scope, seen) for child in ast.iter_child_nodes(node)
                       if isinstance(child, ast.expr))
        if isinstance(node, ast.Subscript):
            return self._plain(node.value, scope, seen) and self._plain(node.slice, scope, seen)
        if isinstance(node, ast.Call):
            names = _candidates(self.follow(scope.resolve(node.func)))
            return all(isinstance(name, str) and name in {"builtins." + item for item in _VALUE_BUILTINS}
                       for name in names) and all(self._plain(arg, scope, seen) for arg in
                                                 [*node.args, *(keyword.value for keyword in node.keywords)])
        binding = self.follow(scope.resolve(node))
        for candidate in _candidates(binding):
            if candidate in self.literal_values:
                if candidate in seen or candidate in self.dirty_containers or len(seen) >= MAX_CALL_DEPTH:
                    return False
                owner, literal = self.literal_values[candidate]
                if not self._plain(literal, owner, (*seen, candidate)):
                    return False
            elif candidate in self.calls:
                if candidate in seen or len(seen) >= MAX_CALL_DEPTH:
                    return False
                owner, expression = self.calls[candidate]
                if not self._plain(expression, owner, (*seen, candidate)):
                    return False
            elif not (candidate == "?value" or candidate in _PLAIN_EXTERNAL_VALUES or
                      candidate in {"builtins." + item for item in _VALUE_BUILTINS}):
                return False
        return True

    def _installed_argument(self, node, scope, seen=()):
        """Literal graphs and named installed constants/types, with no hooks."""
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return all(self._installed_argument(item, scope, seen) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(key is not None and self._installed_argument(key, scope, seen) and
                       self._installed_argument(value, scope, seen) for key, value in zip(node.keys, node.values))
        if isinstance(node, (ast.BinOp, ast.UnaryOp)):
            return all(self._installed_argument(child, scope, seen) for child in ast.iter_child_nodes(node)
                       if isinstance(child, ast.expr))
        if isinstance(node, ast.Subscript):
            return self._typing_subscript(node, scope)
        if isinstance(node, ast.Call):
            names = _candidates(self.follow(scope.resolve(node.func)))
            return (all(MODELLED_EXTERNAL_CALLS.get(name, "").startswith("constant:") for name in names) and
                    not any(isinstance(arg, ast.Starred) for arg in node.args) and
                    not any(keyword.arg is None for keyword in node.keywords) and
                    all(self._installed_argument(value, scope, seen) for value in
                        [*node.args, *(keyword.value for keyword in node.keywords)]))
        binding = self.follow(scope.resolve(node))
        constants = _PLAIN_EXTERNAL_VALUES | _INSTALLED_TYPES | {
            "typing.Any", "typing.NoReturn", "typing.Never", "typing.LiteralString",
            "re.ASCII", "re.IGNORECASE", "re.LOCALE", "re.MULTILINE", "re.DOTALL", "re.UNICODE", "re.VERBOSE",
            "re.A", "re.I", "re.L", "re.M", "re.S", "re.U", "re.X", "re.NOFLAG",
        }
        for candidate in _candidates(binding):
            if candidate in seen or len(seen) >= MAX_CALL_DEPTH or candidate in self.dirty_containers:
                return False
            if candidate in self.literal_values:
                owner, value = self.literal_values[candidate]
                if not self._installed_argument(value, owner, (*seen, candidate)):
                    return False
            elif candidate in self.calls:
                owner, value = self.calls[candidate]
                if not self._installed_argument(value, owner, (*seen, candidate)):
                    return False
            elif candidate != "?value" and candidate not in constants:
                return False
        return True

    def _typing_subscript(self, node, scope):
        names = _candidates(self.follow(scope.resolve(node.value)))
        return isinstance(node.ctx, ast.Load) and all(name in _TYPING_GENERICS for name in names) and self._installed_argument(
            node.slice, scope)

    def _comprehension(self, node, scope, depth):
        if depth >= MAX_CALL_DEPTH or any(generator.is_async for generator in node.generators):
            return False
        parent = scope
        while parent.class_body:
            parent = parent.parent
        targets = [part.id for generator in node.generators for part in ast.walk(generator.target)
                   if isinstance(part, ast.Name)]
        key = (scope, node)
        inner = self.comprehension_scopes.get(key)
        if inner is None:
            inner = _Scope(self, scope.file, [], parent, targets, values=("?value",) * len(targets))
            self.comprehension_scopes[key] = inner
        expressions = []
        for index, generator in enumerate(node.generators):
            owner = scope if index == 0 else inner
            if not self._plain(generator.iter, owner) or not self._scan([generator.iter], owner, depth + 1, strict=True):
                return False
            expressions.extend(generator.ifs)
        expressions.extend([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
        return self._scan(expressions, inner, depth + 1, strict=True) and (
            not isinstance(node, (ast.SetComp, ast.DictComp)) or
            self._plain(node.key if isinstance(node, ast.DictComp) else node.elt, inner))

    def _attribute_allowed(self, node, scope):
        owners = _candidates(self.follow(scope.resolve(node.value)))
        for owner in owners:
            if owner == "?instance":
                if not isinstance(node.ctx, ast.Store) or not self._instance_store(scope, node.attr):
                    return False
            elif owner in self.calls:
                if not isinstance(node.ctx, ast.Load):
                    return False
                caller, expression = self.calls[owner]
                for constructor in _candidates(self.follow(caller.resolve(expression.func))):
                    if constructor not in self.definitions or not isinstance(self.definitions[constructor][1], ast.ClassDef):
                        return False
                    parent, definition = self.definitions[constructor]
                    held = self._function_scope(parent, definition)
                    if not self._protocols(held, 0):
                        return False
                    for value in _candidates(self.follow(self._members(held, node.attr))):
                        if value not in self.definitions or not isinstance(self.definitions[value][1], _FUNCTIONS):
                            return False
            elif owner in self.definitions:
                parent, definition = self.definitions[owner]
                if (not isinstance(definition, ast.ClassDef) or not isinstance(node.ctx, ast.Load) or
                        not self._metaclass_lookup(parent, definition, node.attr)):
                    return False
                held = self._function_scope(parent, definition)
                values = _candidates(self.follow(held.bindings.get(node.attr, "?unresolved")))
                for value in values:
                    if value not in self.definitions or not isinstance(self.definitions[value][1], _FUNCTIONS):
                        return False
                    function_parent, function = self.definitions[value]
                    if any("builtins.property" in _candidates(self.follow(function_parent.resolve(
                            decorator.func if isinstance(decorator, ast.Call) else decorator)))
                           for decorator in function.decorator_list):
                        return False
            elif _owned_container(owner, scope.file):
                if not isinstance(node.ctx, ast.Load):
                    return False
            elif _internal(owner):
                return False
            elif not isinstance(node.ctx, ast.Load):
                return False
            else:
                for file in self.paths.get(owner, ()):
                    imported = self.scopes.get(file)
                    if imported and node.attr not in imported.bindings and "__getattr__" in imported.bindings:
                        if not self._hook(imported, "__getattr__", 0):
                            return False
        return True

    def _metaclass_lookup(self, parent, node, attribute, seen=()):
        """Check metaclasses contributed by every base before class lookup."""
        if node in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        for keyword in node.keywords:
            if keyword.arg != "metaclass":
                return False
            for candidate in _candidates(self.follow(parent.resolve(keyword.value))):
                if candidate == "builtins.type":
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_members(owner, definition, attribute):
                    return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in ("builtins.object", "builtins.type"):
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_lookup(
                        owner, definition, attribute, (*seen, node)):
                    return False
        return True

    def _metaclass_members(self, parent, node, attribute, seen=()):
        if node in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        scope = self._function_scope(parent, node)
        if not all(self._hook(scope, name, len(seen), receiver=scope) for name in ("__getattribute__", "__getattr__")):
            return False
        if attribute in scope.bindings:
            # A member of the same name may itself be a descriptor. Until its
            # lookup protocol is followed, the class lookup stays unavailable.
            return False
        for base in node.bases:
            for candidate in _candidates(self.follow(parent.resolve(base))):
                if candidate in ("builtins.type", "builtins.object"):
                    continue
                if candidate not in self.definitions:
                    return False
                owner, definition = self.definitions[candidate]
                if not isinstance(definition, ast.ClassDef) or not self._metaclass_members(
                        owner, definition, attribute, (*seen, node)):
                    return False
        return True

    def _instance_store(self, scope, attribute, seen=()):
        owner = getattr(scope, "method_owner", None)
        if getattr(scope, "class_receiver", False):
            return owner is not None and self._class_metaprotocols(owner, len(seen))
        return self._instance_store_owner(owner, attribute, seen)

    def _instance_store_owner(self, owner, attribute, seen):
        if owner is None or owner in seen or len(seen) >= MAX_CALL_DEPTH:
            return False
        if not all(self._hook(owner, name, len(seen), receiver=owner) for name in ("__setattr__",)):
            return False
        if attribute in owner.bindings and not all(candidate == "?value" for candidate in
                                                   _candidates(self.follow(owner.bindings[attribute]))):
            return False
        node = owner.class_node
        for base in node.bases:
            for candidate in _candidates(self.follow(owner.declaration_scope.resolve(base))):
                if candidate == "builtins.object":
                    continue
                if candidate not in self.definitions or not isinstance(self.definitions[candidate][1], ast.ClassDef):
                    return False
                parent, definition = self.definitions[candidate]
                base_scope = self._function_scope(parent, definition)
                # Reuse the same store rule with the inherited owner.
                if not self._instance_store_owner(base_scope, attribute, (*seen, owner)):
                    return False
        return True

    def _operands_allowed(self, node, scope):
        operands = []
        if isinstance(node, ast.BinOp):
            operands = [node.left, node.right]
        elif isinstance(node, ast.UnaryOp):
            operands = [node.operand]
        elif isinstance(node, ast.BoolOp):
            operands = node.values
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
        elif isinstance(node, ast.AugAssign):
            operands = [node.target, node.value]
        elif isinstance(node, (ast.If, ast.While, ast.IfExp)):
            operands = [node.test]
        elif isinstance(node, ast.For):
            operands = [node.iter]
        elif isinstance(node, ast.Match):
            operands = [node.subject]
        elif isinstance(node, ast.Set):
            operands = node.elts
        elif isinstance(node, ast.Dict):
            operands = node.keys
        elif isinstance(node, ast.With):
            for item in node.items:
                expression = item.context_expr
                if not isinstance(expression, ast.Call) or self.follow(scope.resolve(expression.func)) != "contextlib.nullcontext":
                    return False
                values = [*expression.args, *(keyword.value for keyword in expression.keywords)]
                if isinstance(item.optional_vars, (ast.Tuple, ast.List)) and (len(values) != 1 or not self._plain(values[0], scope)):
                    return False
            return True
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, (ast.Tuple, ast.List)) for target in targets):
                operands = [node.value]
        return all(operand is not None and self._plain(operand, scope) for operand in operands)
