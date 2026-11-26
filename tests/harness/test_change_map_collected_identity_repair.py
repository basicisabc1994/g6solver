# SPDX-License-Identifier: Apache-2.0
"""Collected names need an independently recorded callable location."""
import ast
import json
import time
from types import SimpleNamespace

import pytest

from g6.harness import change_map, patch_review
from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API


SOURCE = "from api import neighbour\ndef test_call():\n    neighbour()\n"
NODE = "test_api.py::test_call"


@pytest.mark.parametrize("body", [None, {"file": "other.py", "line": 2},
                                  {"file": "test_api.py", "line": 4},
                                  {"file": "test_api.py", "line": True}])
def test_collected_name_without_matching_body_is_uncertain(body):
    before = {"api.py": API, "test_api.py": SOURCE}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    answer = {"status": "available", "nodes": [NODE]}
    if body is not None:
        answer["bodies"] = {NODE: body}
    record = change_map.build(change(before, after), before, after,
                              stated_checks=("test_api.py",), collection={"test_api.py": answer})
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == []


@pytest.mark.parametrize("source,line", [(SOURCE, 2), (
    "from api import neighbour\n@decorate\ndef test_call():\n    neighbour()\n", 2)])
def test_matching_callable_location_admits_the_body(source, line):
    answer = {"status": "available", "nodes": [NODE],
              "bodies": {NODE: {"file": "test_api.py", "line": line}}}
    assert change_map.exercised([{"binding": "api.neighbour"}], {"test_api.py": source},
                                collection={"test_api.py": answer}) == {"api.neighbour": ["test_api.py"]}


def test_inherited_callable_location_names_its_definition_file():
    source = "from base import Parent\nclass TestAPI(Parent):\n    pass\n"
    node = "test_api.py::TestAPI::test_call"
    before = {"api.py": API, "test_api.py": source,
              "base.py": "from api import neighbour\nclass Parent:\n    def test_call(self):\n        neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    answer = {"status": "available", "nodes": [node],
              "bodies": {node: {"file": "base.py", "line": 3}}}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,),
                              collection={node: answer})
    assert target(record, "neighbour")["exercised"] is True


def test_selection_filter_preserves_only_its_callable_locations():
    answer = {"status": "available", "nodes": [NODE, "test_other.py::test_other"],
              "bodies": {NODE: {"file": "test_api.py", "line": 2},
                         "test_other.py::test_other": {"file": "test_other.py", "line": 1}}}
    found = change_map.collect_checks(SimpleNamespace(repo=None, files=()),
                                       (NODE, "test_other.py::test_other"), (), (),
                                       collector=lambda *a, **k: answer, deadline=time.monotonic() + 20)
    assert found[NODE]["bodies"] == {NODE: answer["bodies"][NODE]}


def test_collection_producer_and_reader_keep_actual_callable_location(tmp_path, monkeypatch, request):
    monkeypatch.chdir(tmp_path)
    namespace = {}
    exec(compile("def test_call():\n    pass\n", str(tmp_path / "test_api.py"), "exec"), namespace)
    tree = ast.parse(repo_tests._COLLECTION_BOOT)
    counted = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    report = tmp_path / "counted.json"
    scope = {"json": json, "counted": str(report)}
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "counted-repair", "exec"), scope)
    plugin = scope["_Counted"]()
    item = pytest.Function.from_parent(request.node.parent, name="test_call", callobj=namespace["test_call"])
    item._id = NODE
    assert item.nodeid == NODE
    session = SimpleNamespace(items=[item], exitstatus=0,
                              config=SimpleNamespace(rootpath=tmp_path, rootdir=tmp_path))
    plugin.pytest_collection_finish(session)
    plugin.pytest_sessionfinish(session)
    found = repo_tests._read_collection(tmp_path / "counted.json.collection.json", 0, tmp_path)
    assert found["bodies"] == {NODE: {"file": "test_api.py", "line": 1}}


def test_custom_items_cannot_claim_callable_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scope = {}
    exec(compile("def test_call():\n    pass\n", str(tmp_path / "test_api.py"), "exec"), scope)
    tree = ast.parse(repo_tests._COLLECTION_BOOT)
    counted = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    report = tmp_path / "counted.json"
    scope.update(json=json, counted=str(report))
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "counted-repair", "exec"), scope)
    plugin = scope["_Counted"]()
    item = SimpleNamespace(nodeid=NODE, originalname="test_call", obj=scope["test_call"])
    session = SimpleNamespace(items=[item], exitstatus=0,
                              config=SimpleNamespace(rootpath=tmp_path, rootdir=tmp_path))
    plugin.pytest_collection_finish(session)
    plugin.pytest_sessionfinish(session)
    assert "bodies" not in json.loads((tmp_path / "counted.json.collection.json").read_text(encoding="utf-8"))


def test_collection_reader_rebases_node_but_not_definition_path(tmp_path):
    (tmp_path / "suite").mkdir()
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"nodes": [NODE], "root": str(tmp_path / "suite"), "exit": 0,
                                  "bodies": {NODE: {"file": "suite/test_api.py", "line": 2}}}), encoding="utf-8")
    found = repo_tests._read_collection(report, 0, tmp_path)
    assert found["bodies"] == {"suite/" + NODE: {"file": "suite/test_api.py", "line": 2}}


@pytest.mark.parametrize("file,line", [("../outside.py", 1), ("/outside.py", 1),
                                      ("test_api.py", True), ("test_api.py", 0)])
def test_collection_reader_rejects_invalid_body_locations(tmp_path, file, line):
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"nodes": [NODE], "root": str(tmp_path), "exit": 0,
                                  "bodies": {NODE: {"file": file, "line": line}}}), encoding="utf-8")
    assert repo_tests._read_collection(report, 0, tmp_path)["status"] == "uncertain"


def test_reader_uses_collected_callable_locations_for_positive_leads(tmp_path):
    before = {"api.py": API, "test_api.py": SOURCE}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    answer = {"status": "available", "nodes": [NODE],
              "bodies": {NODE: {"file": "test_api.py", "line": 2}}}
    record = patch_review._change_neighbourhood(
        delta, ("test_api.py",), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: answer)
    sibling = target(record, "neighbour")
    assert sibling["exercised"] is True and sibling["checks"] == ["test_api.py"]
