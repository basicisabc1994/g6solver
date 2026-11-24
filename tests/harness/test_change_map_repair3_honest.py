# SPDX-License-Identifier: Apache-2.0
"""Honest-code admission, compiler failure reasons and map-local scan bounds."""
import ast
import json
from unittest.mock import Mock

import pytest

from g6.harness import change_map
from g6.workforce import repo_tests
from tests.harness.test_change_map import change, target


API = "def changed():\n    return 1\ndef neighbour():\n    return changed()\n"


@pytest.mark.parametrize("config,source,selection,expected", [
    ("", "def helper():\n    neighbour()\n", "test_api.py::helper", "uncertain"),
    ("[pytest]\npython_functions = check_*\n", "def check_call():\n    neighbour()\n",
     "test_api.py::check_call", "uncertain"),
    ("[pytest]\npython_functions = check_*\n", "def test_call():\n    neighbour()\n",
     "test_api.py::test_call", "uncertain"),
    ("[pytest]\npython_classes = Check*\npython_functions = check_*\npython_files = spec_*.py\n",
     "class CheckAPI:\n    def check_call(self):\n        neighbour()\n", "spec_api.py::CheckAPI::check_call", "uncertain"),
    ("", "def test_call():\n    neighbour()\n", "ordinary.py::test_call", "uncertain"),
])
def test_static_admission_uses_collection_names(config, source, selection, expected):
    rel = selection.partition("::")[0]
    before = {"api.py": API, rel: "from api import neighbour\n" + source}
    if config:
        before["pytest.ini"] = config
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(selection,))
    assert target(record, "neighbour")["exercised"] == expected


@pytest.mark.parametrize("kind", ["function", "module"])
def test_imported_body_reads_definition_module_collection_attribute(kind):
    suppression = "test_call.__test__ = False" if kind == "function" else "__test__ = False"
    before = {"api.py": API, "test_api.py": "from body import test_call\n",
              "body.py": "from api import neighbour\ndef test_call():\n    neighbour()\n" + suppression + "\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after,
                              stated_checks=("test_api.py::test_call",))
    assert target(record, "neighbour")["exercised"] == "uncertain"


def test_explicit_admission_scans_each_module_once(monkeypatch):
    before = {"api.py": API, "test_api.py": "from api import neighbour\n" + "".join(
        f"def test_case_{i}():\n    neighbour()\n" for i in range(40))}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    scan = Mock(wraps=change_map._static_test_writes)
    monkeypatch.setattr(change_map, "_static_test_writes", scan)
    record = change_map.build(change(before, after), before, after, stated_checks=tuple(
        f"test_api.py::test_case_{i}" for i in range(40)))
    assert target(record, "neighbour")["exercised"] is True
    assert scan.call_count == 1


@pytest.mark.parametrize("source", [
    "from api import setup\nsetup()\n",
    "import pytest\npytest.Function.runtest = lambda self: None\n",
    "class Thing:\n    pass\nThing()\n",
    "import pytest\nalias = pytest.Function\nalias.runtest = lambda self: None\n",
    "from api import setup\nrun = setup\nrun()\n",
    "def setup():\n    pass\nrun = setup\nrun()\n",
    "import pytest\nsetattr(pytest.Function, 'runtest', lambda self: None)\n",
    "import pytest\npytest.Function.__dict__['runtest'] = None\n",
])
def test_collection_read_rejects_repository_calls_and_imported_writes(source):
    assert not repo_tests._collection_source_passive(ast.parse(source), {"api", "test_api"})


def test_compilation_failure_has_a_cause(tmp_path, monkeypatch):
    node = "test_api.py::test_call"
    source = b"def test_call():\n    assert True\n"
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(tmp_path), "exit": 0, "nodes": [node],
                                 "bodies": {node: {"file": "test_api.py", "line": 1}},
                                 "codes": {node: "unavailable"}}), encoding="utf-8")
    monkeypatch.setattr(repo_tests, "_compile_collection_sources", Mock(side_effect=ValueError("pytest unavailable")))
    answer = repo_tests._read_collection(report, 0, tmp_path, evidence=({"test_api.py": source}, True))
    assert answer["status"] == "uncertain"
    assert "compilation" in answer["reason"] and "pytest unavailable" in answer["reason"]


def test_map_states_collection_same_process_limit():
    expected = ("Not checked against code written to deceive collection: repository code running inside the collection "
                "child can make a check look exercised, and the pre-launch read is not authority against it.")
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    delta = change(before, after)
    record = change_map.build(delta, before, after, stated_checks=("test_api.py::test_call",))
    for answer in (record, change_map.empty(), change_map.json_record(record),
                   change_map.build(delta, before, after, deadline=0), change_map.json_record({})):
        assert set(answer) == {"targets", "instances", "cases", "gates", "skipped"}
        if answer["targets"]:
            assert any(entry["check"] == "change map static visibility" and entry["reason"].endswith(expected)
                       for entry in answer["skipped"])
        assert expected not in json.dumps(answer.get("limits", []))
    assert target(record, "neighbour")["exercised"] is True
    assert not any("limit" in entry["reason"] or "budget" in entry["reason"] for entry in record["skipped"])


def test_collection_inventory_respects_declared_import_roots(tmp_path, monkeypatch):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "tool.py").write_text("def setup():\n    pass\n", encoding="utf-8")
    (tmp_path / "test_api.py").write_text("from tool import setup\nsetup()\n", encoding="utf-8")
    monkeypatch.setattr(repo_tests, "_git", lambda *a, **k: b"lib/tool.py\0test_api.py\0")
    _, usable = repo_tests._collection_evidence(tmp_path, roots=("lib",))
    assert usable is False
