# SPDX-License-Identifier: Apache-2.0
"""Collection diagnostics cannot invalidate ordinary runner evidence."""
import ast
import json
from types import SimpleNamespace

import pytest

from g6.workforce import repo_tests as rt
from tests.workforce.test_repo_tests_run import FIXED, make_repo


def producer(path, *, collect_only=False):
    tree = ast.parse(rt._COLLECTION_BOOT if collect_only else rt._BOOT)
    counted = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {"json": json, "counted": str(path)}
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "counted-amendment", "exec"), namespace)
    return namespace["_Counted"]()


@pytest.mark.parametrize("collect_only", [False, True])
def test_counted_bytes_keep_parent_schema_and_collection_has_its_own_file(tmp_path, collect_only):
    path = tmp_path / "counted.json"
    counted = producer(path, collect_only=collect_only)
    item = SimpleNamespace(nodeid="test_api.py::test_call")
    session = SimpleNamespace(items=[item], exitstatus=0,
                              config=SimpleNamespace(rootpath="", rootdir="", option=SimpleNamespace(collectonly=collect_only)))
    counted.pytest_itemcollected(item)
    counted.pytest_collection_finish(session)
    counted.pytest_sessionfinish(session)
    # Literal bytes from the parent's writer: key order, spacing and no collection-only fields.
    assert path.read_bytes() == (
        b'{"collected": 1, "deselected": 0, "started": 0, "finished": 0, "failed_ids": [], '
        b'"setup_errors": {}, "test_outcomes": {}, "collected_ids": ["test_api.py::test_call"], '
        b'"omitted_ids": [], "rootdir": "", "messages": {}, "os_errors": [], "missing_modules": [], '
        b'"not_found_ids": [], "collection_stopped": false}'
    )
    collection = tmp_path / "counted.json.collection.json"
    assert collection.exists() is collect_only
    if collect_only:
        assert json.loads(collection.read_text(encoding="utf-8")) == {
            "nodes": ["test_api.py::test_call"], "root": "", "exit": 0}


def test_normal_passing_run_still_reads_passed(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "calc.py").write_text(FIXED, encoding="utf-8")
    found = rt.run(repo, ["tests/test_calc.py"])
    assert (found["verdict"], found["passed"], found["total"]) == (rt.PASSED, 2, 2)
    assert found["completed"] is True and found["degraded"] is False


def test_ordinary_bootstrap_has_no_collection_only_report_path():
    tree = ast.parse(rt._BOOT)
    counted = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    assert not any(isinstance(node, ast.Attribute) and node.attr == "collectonly" for node in ast.walk(counted))
    assert not any(isinstance(node, ast.Constant) and node.value == ".collection.json" for node in ast.walk(counted))


@pytest.mark.parametrize("inside", [True, False])
def test_collection_root_uses_the_single_containment_rule(tmp_path, monkeypatch, inside):
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({"nodes": ["test_api.py::test_call"], "root": str(tmp_path), "exit": 0}),
                    encoding="utf-8")
    monkeypatch.setattr(rt, "_within", lambda root, where: inside)
    found = rt._read_collection(path, 0, tmp_path)
    assert found["status"] == ("available" if inside else "uncertain")
    if inside:
        assert found["nodes"] == ["test_api.py::test_call"]
    else:
        assert "nodes" not in found
