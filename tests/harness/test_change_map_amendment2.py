# SPDX-License-Identifier: Apache-2.0
"""Explicit bodies are static inputs; expanding pytest selections needs a collector."""
import ast
import inspect

import pytest

from g6.core.git_name import GitName, shown_name
from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API
from tests.harness.test_git_names import ALLOWED, ROOTS, _Program


@pytest.mark.parametrize("statement, expected", [("neighbour()", True), ("pass", False)])
@pytest.mark.parametrize("answer", [None, {"status": "uncertain", "reason": "no isolation"}])
def test_explicit_stated_body_keeps_static_answer(statement, expected, answer):
    node = "test_api.py::test_call[id::with.dots[inside]]"
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    " + statement + "\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,),
                              collection={} if answer is None else {node: answer})
    sibling = target(record, "neighbour")
    assert sibling["exercised"] is expected
    assert sibling["checks"] == ([node] if expected else [])
    assert sibling["collection"] == "not_required"


def test_collected_deselection_overrides_explicit_static_body():
    node = "test_api.py::test_call"
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,),
                              collection={node: {"status": "available", "nodes": []}})
    assert target(record, "neighbour")["exercised"] is False
    assert target(record, "neighbour")["collection"] == "available"


@pytest.mark.parametrize("selection", ["test_api.py", "test_api.py::TestAPI", "test_api.py::missing"])
def test_non_body_selections_still_need_collection(selection):
    before = {"api.py": API, "test_api.py": "from api import neighbour\nclass TestAPI:\n"
              "    def test_call(self):\n        neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(selection,))
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert any(s["check"] == selection and "collection" in s["reason"] for s in record["skipped"])


def test_injected_collector_expands_file_with_pytest_node_ids():
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef check_call():\n    neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}

    def collector(repo, checks, **kwargs):
        return {"status": "available", "nodes": ["test_api.py::check_call"]}

    record = change_map.build(change(before, after), before, after, stated_checks=("test_api.py",),
                              collector=collector)
    assert target(record, "neighbour")["exercised"] is True
    assert target(record, "neighbour")["checks"] == ["test_api.py"]


def test_collection_path_git_name_inventory():
    # Follow listing-derived names across callers, including joins with no Python hook.
    program = _Program(ROOTS)
    assert program.sinks(ALLOWED) == []
    assert not set(ALLOWED) - program.places()
    # The caller-list failure branch must preserve binding keys as data, too.
    tree = ast.parse(inspect.getsource(change_map.exercised))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str"
                   for node in ast.walk(tree))


def test_git_named_check_is_displayed_by_the_name_rule():
    rel = GitName(b'test_odd"name.py')
    before = {"api.py": API, rel: "def test_call():\n    pass\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(rel,))
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert any(s["check"] == shown_name(rel) for s in record["skipped"])


def test_reader_accepts_an_injected_collector(tmp_path):
    from tests._review_changemap1.test_review import repository

    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})

    def collector(repo, checks, **kwargs):
        return {"status": "available", "nodes": ["test_api.py::test_call"]}

    record = patch_review._change_neighbourhood(delta, ("test_api.py",), (), (), deadline=1e20,
                                               collector=collector)
    assert target(record, "neighbour")["exercised"] is True
