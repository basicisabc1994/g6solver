# SPDX-License-Identifier: Apache-2.0
"""Expression identity and repository-wide collection decision regressions.

Written before the repair; execution belongs to the lead's test lane.
"""
import ast

import pytest

from g6.core.git_name import GitName
from g6.workforce._collection_effects import CollectionEffects
from tests.core.test_change_map_fu2_effects import inventory


def settings(effect):
    return ("import os\nclass Settings:\n" + (
        "    def __getattribute__(self, name):\n"
        "        os.environ['PATH'] = 'collection-follow-up'\n"
        "        return object.__getattribute__(self, name)\n" if effect else "    pass\n"
    ) + "    def test_call(self): pass\n")


@pytest.mark.parametrize("route", ["list", "dict"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "effect"])
def test_nested_projection_keeps_collecting_class(tmp_path, monkeypatch, route, effect):
    projection = {
        "list": "classes = [[Settings]]\nTestSettings = classes[0][0]\n",
        "dict": "classes = {'group': [Settings]}\nTestSettings = classes['group'][0]\n",
    }[route]
    _, certain = inventory(tmp_path, monkeypatch, {"test_api.py": settings(effect) + projection})
    assert certain is (not effect)


@pytest.mark.parametrize("mutated", [False, True], ids=["inert-control", "mutated-effect"])
def test_later_module_write_precedes_collection_decision(tmp_path, monkeypatch, mutated):
    helper = settings(mutated) + ("classes = [None]\nclasses[0] = Settings\n" if mutated else
                                  "classes = [None]\n")
    _, certain = inventory(tmp_path, monkeypatch, {
        "test_api.py": "from zhelper import classes\nTestSettings = classes[0]\n",
        "zhelper.py": helper,
    })
    assert certain is (not mutated)


@pytest.mark.parametrize("effect", [False, True], ids=["inert-class", "effect"])
@pytest.mark.parametrize("mutated", [False, True], ids=["unchanged-control", "mutated"])
def test_helper_rename_does_not_change_collection_answer(tmp_path, monkeypatch, effect, mutated):
    answers = []
    for module in ("ahelper", "zhelper"):
        helper = settings(effect) + ("classes = [None]\nclasses[0] = Settings\n" if mutated else
                                     "classes = [Settings]\n" if effect else "classes = [None]\n")
        _, certain = inventory(tmp_path / module, monkeypatch, {
            module + ".py": helper,
            "test_api.py": "from " + module + " import classes\nTestSettings = classes[0]\n",
        })
        answers.append(certain)
    assert answers[0] is answers[1]
    # Mutated projections have no exact result model, including an inert RHS.
    assert answers == ([False, False] if mutated or effect else [True, True])


@pytest.mark.parametrize("source", [
    "classes[0][0]", "classes['group'][0]", "build()()", "build().method()",
    "build(build())", "build(*build(), option=build())",
    "[[value for value in (1,)] for other in (1,)]", "(lambda: (lambda: 1))",
    "((1,),)", "(value := classes[0])",
])
def test_expression_registries_retain_each_original_node(source):
    file = GitName(b"test_api.py")
    expression = ast.parse(source, mode="eval").body
    reader = CollectionEffects.from_trees({file: ast.parse("")})
    scope = reader.scopes[file]
    # Resolving children must never replace the model of an outer expression.
    resolved = [(node, scope.resolve(node)) for node in ast.walk(expression)
                if isinstance(node, (ast.Call, ast.Subscript, ast.NamedExpr, ast.Lambda,
                                     ast.List, ast.Tuple, ast.ListComp))]
    keys = [key for _, key in resolved]
    assert len(keys) == len(set(keys))
    registries = {**reader.calls, **reader.expressions, **reader.lambdas, **reader.literal_values}
    assert all(registries[key][1] is node for node, key in resolved)


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "effect"])
def test_module_read_order_does_not_change_collection_answer(reverse, effect):
    caller, helper = GitName(b"test_api.py"), GitName(b"helper.py")
    trees = [(caller, ast.parse("from helper import classes\nTestSettings = classes[0]\n")),
             (helper, ast.parse(settings(effect) + "classes = [None]\nclasses[0] = Settings\n"))]
    reader = CollectionEffects.from_trees(dict(reversed(trees) if reverse else trees))
    assert reader.passive_all() is False


@pytest.mark.parametrize("route", ["classmethod-flag", "subclass-flag", "classmethod-protocol"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "effect"])
def test_class_receiver_writes_revisit_collection(tmp_path, monkeypatch, route, effect):
    source = ("import os\ndef lookup(self, name):\n" +
              ("    os.environ['PATH'] = 'collection-follow-up'\n" if effect else "") +
              "    return object.__getattribute__(self, name)\n")
    source += {
        "classmethod-flag": (
            "class Settings:\n    __test__ = False\n    __getattribute__ = lookup\n"
            "    @classmethod\n    def configure(cls): cls.__test__ = True\nSettings.configure()\n"),
        "subclass-flag": (
            "class Base:\n    def __init_subclass__(cls): cls.__test__ = True\n"
            "class Settings(Base):\n    __test__ = False\n    __getattribute__ = lookup\n"),
        "classmethod-protocol": (
            "class TestSettings:\n    @classmethod\n"
            "    def configure(cls): cls.__getattribute__ = lookup\nTestSettings.configure()\n"),
    }[route]
    _, certain = inventory(tmp_path, monkeypatch, {"test_api.py": source + "def test_call(): pass\n"})
    assert certain is (not effect)


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_context_targets_refresh_after_all_modules_exist(reverse):
    answers = []
    for module in ("ahelper", "zhelper"):
        caller, helper = GitName(b"test_api.py"), GitName((module + ".py").encode())
        trees = [(caller, ast.parse("from " + module + " import context\n"
                                   "with context(None) as settings: pass\n")),
                 (helper, ast.parse("from contextlib import nullcontext as context\n"))]
        reader = CollectionEffects.from_trees(dict(reversed(trees) if reverse else trees))
        answers.append(reader.passive_all())
    assert answers == [True, True]


@pytest.mark.parametrize("form", ["def", "lambda"], ids=["def-control", "lambda"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "effect"])
def test_lambda_call_values_stabilise(form, effect):
    body = "os.environ.update({'PATH': 'collection-follow-up'})" if effect else "[0].append(1)"
    source = "import os\n" + ("def initialise(): " + body if form == "def" else
                               "initialise = lambda: " + body) + "\ninitialise()\n"
    reader = CollectionEffects.from_trees({GitName(b"test_api.py"): ast.parse(source)})
    assert reader.passive_all() is (not effect)


def test_distinct_lambda_call_sites_keep_distinct_local_containers():
    reader = CollectionEffects.from_trees({GitName(b"test_api.py"): ast.parse(
        "initialise = lambda: [0].append(1)\ninitialise()\ninitialise()\n")})
    assert reader.passive_all() is True
    assert len(reader.mutated_containers) == 2
    revision = reader.revision
    assert reader.passive_all() is True
    assert reader.revision == revision


@pytest.mark.parametrize("descriptor", [False, True], ids=["plain-control", "unmodelled-descriptor"])
def test_native_lookup_delegation_requires_modelled_members(descriptor):
    source = ("def lookup(self, name): return object.__getattribute__(self, name)\n"
              "class TestSettings:\n    __getattribute__ = lookup\n" +
              ("    @property\n    def value(self): return 1\n" if descriptor else "    value = 1\n"))
    reader = CollectionEffects.from_trees({GitName(b"test_api.py"): ast.parse(source)})
    assert reader.passive_all() is (not descriptor)


def test_native_lookup_delegation_cannot_hide_instance_fields():
    source = ("def lookup(self, name):\n    self.extra = 1\n"
              "    return object.__getattribute__(self, name)\n"
              "class TestSettings:\n    __getattribute__ = lookup\n")
    reader = CollectionEffects.from_trees({GitName(b"test_api.py"): ast.parse(source)})
    assert reader.passive_all() is False
