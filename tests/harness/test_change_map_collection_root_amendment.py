# SPDX-License-Identifier: Apache-2.0
"""Collection identity belongs to the reviewed repository, even under another root."""
import ast
import json
from types import SimpleNamespace

import pytest

from g6.core.git_name import GitName, shown_name
from g6.harness import change_map
from g6.workforce import repo_tests
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API


@pytest.mark.parametrize("calls", [False, True])
def test_collection_identity_uses_reviewed_root_despite_cwd_and_pytest_root(tmp_path, monkeypatch, request, calls):
    repo = tmp_path / "reviewed"
    repo.mkdir()
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    source = "from api import neighbour\ndef test_call():\n    " + ("neighbour()" if calls else "pass") + "\n"
    scope = {}
    exec(compile(source.replace("from api import neighbour\n", "\n"), str(repo / "test_api.py"), "exec"), scope)
    item = pytest.Function.from_parent(request.node.parent, name="test_call", callobj=scope["test_call"])
    item._id = "reviewed/test_api.py::test_call[id::with.dots[inside]]"
    assert item.nodeid == "reviewed/test_api.py::test_call[id::with.dots[inside]]"
    report = tmp_path / "counted.json"
    namespace = {"json": json, "counted": str(report), "_g6_collection_repo": str(repo)}
    tree = ast.parse(repo_tests._COLLECTION_BOOT)
    counted = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "counted-root-amendment", "exec"), namespace)
    plugin = namespace["_Counted"]()
    session = SimpleNamespace(items=[item], exitstatus=0,
                              config=SimpleNamespace(rootpath=tmp_path, rootdir=tmp_path))
    plugin.pytest_collection_finish(session)
    plugin.pytest_sessionfinish(session)
    found = repo_tests._read_collection(tmp_path / "counted.json.collection.json", 0, repo)
    node = "test_api.py::test_call[id::with.dots[inside]]"
    assert found == {"status": "available", "nodes": [node],
                     "bodies": {node: {"file": "test_api.py", "line": 2}}}
    assert change_map.exercised([{"binding": "api.neighbour"}], {"test_api.py": source},
                                collection={"test_api.py": found}) == {"api.neighbour": ["test_api.py"] if calls else []}


def test_reader_normalizes_absolute_callable_location_without_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "reviewed"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    node = "test_api.py::test_call"
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(repo), "exit": 0, "nodes": [node],
                                  "bodies": {node: {"file": str(repo / "test_api.py"), "line": 1}}}),
                      encoding="utf-8")
    found = repo_tests._read_collection(report, 0, repo)
    assert found["bodies"] == {node: {"file": "test_api.py", "line": 1}}


@pytest.mark.parametrize("display_path", [False, True])
def test_display_quoted_path_is_not_a_different_raw_callable_path(display_path):
    rel = GitName(b'test_"api.py')
    node = 'test_"api.py::test_call'
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    answer = {"status": "available", "nodes": [node],
              "bodies": {node: {"file": shown_name(rel) if display_path else 'test_"api.py', "line": 2}}}
    before = {"api.py": API, rel: source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,),
                              collection={node: answer})
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == ("uncertain" if display_path else True)
    assert sibling["checks"] == ([] if display_path else [node])
