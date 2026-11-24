# SPDX-License-Identifier: Apache-2.0
"""Static selections cannot enter a class pytest would suppress."""
import pytest

from g6.harness import change_map
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API


@pytest.mark.parametrize("owner", ["outer", "inner", "inherited"])
@pytest.mark.parametrize("suppression", ["__test__ = False", "__test__ = 0",
                                        "def __init__(self):\n    pass", "def __new__(cls):\n    pass"])
@pytest.mark.parametrize("selection", ["TestOuter::TestInner::test_call",
                                       "TestOuter.TestInner.test_call[param::with.dots]"])
def test_explicit_body_checks_every_containing_class(owner, suppression, selection):
    body = "\n".join("    " + line for line in suppression.splitlines()) + "\n"
    source = "from api import neighbour\n"
    if owner == "inherited":
        source += "class Parent:\n" + body
    source += "class TestOuter" + ("(Parent)" if owner == "inherited" else "") + ":\n"
    if owner == "outer":
        source += body
    source += "    class TestInner:\n"
    if owner == "inner":
        source += "\n".join("    " + line for line in body.splitlines()) + "\n"
    source += "        def test_call(self):\n            neighbour()\n"
    node = "test_api.py::" + selection
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {"api.neighbour": []}
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,))
    assert target(record, "neighbour")["checks"] == []
    assert target(record, "neighbour")["exercised"] is not True


def test_collectible_parent_chain_still_supplies_a_static_body():
    source = ("from api import neighbour\nclass Base:\n    __test__ = True\n"
              "class TestOuter(Base):\n    class TestInner:\n"
              "        def test_call(self):\n            neighbour()\n")
    node = "test_api.py::TestOuter::TestInner::test_call"
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {"api.neighbour": [node]}


def test_real_collection_remains_authoritative_for_custom_collectors():
    node = "test_api.py::SpecOuter::SpecInner::check_call"
    source = ("from api import neighbour\nclass SpecOuter:\n    class SpecInner:\n"
              "        def check_call(self):\n            neighbour()\n")
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}, collection={
        node: {"status": "available", "nodes": [node]}}) == {"api.neighbour": [node]}


@pytest.mark.parametrize("header", ["@decorate\nclass TestOuter:", "class TestOuter(metaclass=Factory):"])
def test_unknown_class_creation_requires_collection(header):
    node = "test_api.py::TestOuter::TestInner::test_call"
    source = ("from api import neighbour\n" + header + "\n    class TestInner:\n"
              "        def test_call(self):\n            neighbour()\n")
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {"api.neighbour": []}


@pytest.mark.parametrize("config, text", [
    ("pytest.ini", "[pytest]\npython_classes = Spec*\n"),
    ("conftest.py", "def pytest_pycollect_makeitem(collector, name, obj):\n    return []\n"),
])
def test_explicit_class_body_needs_collection_when_configuration_can_suppress_it(config, text):
    node = "test_api.py::TestOuter::TestInner::test_call"
    source = ("from api import neighbour\nclass TestOuter:\n    class TestInner:\n"
              "        def test_call(self):\n            neighbour()\n")
    before = {"api.py": API, "test_api.py": source, config: text}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,))
    assert target(record, "neighbour")["checks"] == []
    assert target(record, "neighbour")["exercised"] == "uncertain"
