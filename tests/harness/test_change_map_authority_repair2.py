# SPDX-License-Identifier: Apache-2.0
"""Repository-controlled locations and decorators cannot supply body identity."""
import json
import time
import types

import pytest

from g6.harness import change_map
from g6.workforce import repo_tests
from tests._review_cmfu2.test_authority import API, NODE, mapped, oracle
from tests._review_changemap1.test_review import repository
from tests.harness.test_change_map import change, target


def signature(source):
    return change_map.code_identity(next(code for code in compile(source, "test_api.py", "exec").co_consts
                                         if isinstance(code, types.CodeType) and code.co_name == "test_call"))


@pytest.mark.parametrize("substitute", [
    "    neighbour()\n    return 2\n",
    "    changed()\n    return 1\n",
    "    def inner():\n        pass\n    neighbour()\n    return 1\n",
])
def test_map_independently_compares_code_contents(substitute):
    source = "from api import neighbour, changed\ndef test_call():\n    neighbour()\n    return 1\n"
    other = "from api import neighbour, changed\ndef test_call():\n" + substitute
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    answer = {"status": "available", "nodes": [NODE],
              "bodies": {NODE: {"file": "test_api.py", "line": 2}}, "codes": {NODE: signature(other)}}
    record = change_map.build(change(before, after), before, after, stated_checks=("test_api.py",),
                              collection={"test_api.py": answer})
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []


@pytest.mark.parametrize("include_code", [False, True])
def test_only_matching_code_can_establish_a_positive(include_code):
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    answer = {"status": "available", "nodes": [NODE],
              "bodies": {NODE: {"file": "test_api.py", "line": 2}}}
    if include_code:
        answer["codes"] = {NODE: signature(source)}
    record = change_map.build(change(before, after), before, after, stated_checks=("test_api.py",),
                              collection={"test_api.py": answer})
    assert target(record, "neighbour")["exercised"] == (True if include_code else "uncertain")


@pytest.mark.parametrize("replacement", ["constant", "name", "nested"])
def test_compilation_compares_more_than_locations(tmp_path, replacement):
    bodies = {
        "constant": ("    neighbour()\n    return 1\n", "    neighbour()\n    return 2\n"),
        "name": ("    neighbour()\n", "    changed()\n"),
        "nested": ("    def inner():\n        neighbour()\n    inner()\n",
                   "    def inner():\n        pass\n    inner()\n"),
    }
    original, substitute = bodies[replacement]
    source = ("from api import neighbour, changed\ndef test_call():\n" + original +
              "def replacement():\n" + substitute +
              "test_call.__code__ = replacement.__code__.replace(\n"
              "    co_filename=test_call.__code__.co_filename, co_firstlineno=2,\n"
              "    co_name='test_call', co_qualname='test_call')\n")
    before = {"api.py": API, "test_api.py": source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    ran, answer = oracle(tmp_path)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    sibling = mapped(delta, answer)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == []


@pytest.mark.parametrize("depth", [1, 2])
def test_static_admission_scans_every_ancestor_module(depth):
    before = {"api.py": API,
              "base.py": "from api import neighbour\nclass Parent:\n    def test_call(self):\n        neighbour()\nParent.__test__ = False\n",
              "test_api.py": "from base import Parent\nclass TestAPI(Parent):\n    pass\n"}
    if depth == 2:
        before["middle.py"] = "from base import Parent\nclass Middle(Parent):\n    pass\n"
        before["test_api.py"] = "from middle import Middle\nclass TestAPI(Middle):\n    pass\n"
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after,
                              stated_checks=("test_api.py::TestAPI::test_call",))
    assert target(record, "neighbour")["exercised"] == "uncertain"


@pytest.mark.parametrize("decorator", ["discard", "alias"])
def test_decorated_helper_blocks_transitive_static_calls(decorator):
    source = ("from api import neighbour\ndef discard(fn):\n    return lambda: None\n"
              "alias = discard\n@" + decorator + "\ndef helper():\n    neighbour()\n"
              "def test_call():\n    helper()\n")
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,))
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []


def test_plain_helper_preserves_transitive_positive():
    source = "from api import neighbour\ndef helper():\n    neighbour()\ndef test_call():\n    helper()\n"
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,))
    assert target(record, "neighbour")["exercised"] is True


def test_positive_survives_unrelated_unavailable_check():
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after,
                              stated_checks=(NODE, "test_unknown.py"))
    sibling = target(record, "neighbour")
    assert sibling["exercised"] is True and sibling["checks"] == [NODE]


def test_collection_filter_retains_identity_rejections():
    answer = {"status": "available", "nodes": [NODE],
              "bodies": {NODE: {"file": "test_api.py", "line": 2}}, "unresolved": [NODE]}
    found = change_map.collect_checks(type("Change", (), {"repo": None, "files": ()})(),
                                      (NODE,), (), (), collector=lambda *a, **k: answer,
                                      deadline=time.monotonic() + 20)
    assert found[NODE]["unresolved"] == [NODE]


@pytest.mark.parametrize("configuration", ["conftest.py", "pytest.ini", "pyproject.toml"])
def test_mutable_report_cannot_supply_identity(tmp_path, configuration):
    (tmp_path / configuration).write_text("", encoding="utf-8")
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(tmp_path), "exit": 0, "nodes": [NODE],
                                  "bodies": {NODE: {"file": "test_api.py", "line": 2}}}),
                      encoding="utf-8")
    answer = repo_tests._read_collection(report, 0, tmp_path)
    assert answer.get("unresolved") == [NODE]
