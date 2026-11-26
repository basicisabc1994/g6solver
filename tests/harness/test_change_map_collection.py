# SPDX-License-Identifier: Apache-2.0
"""Pytest owns collection; the map owns only static named-call reachability."""
import json
import os
import time

import pytest

from tests.harness.collection_fixtures import collection_answer

from g6.harness import change_map, patch_review
from g6.workforce import repo_tests
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API
from tests._review_changemap1.test_review import repository


@pytest.fixture
def real_collection_python(monkeypatch, request):
    if os.name == "nt":
        # Use the existing owner-controlled confinement fixture, including its
        # explicit infrastructure skip if no interpreter can be confined.
        monkeypatch.setenv("G6_REPO_PYTHON", request.getfixturevalue("confined_python"))


CASES = [
    pytest.param("class TestHidden:\n    __test__ = 0\n    def test_call(self):\n        neighbour()\n",
                 {}, False, id="false-class"),
    pytest.param("__test__ = False\ndef test_call():\n    neighbour()\n", {}, False, id="disabled-module"),
    pytest.param("class TestAbstract(metaclass=abc.ABCMeta):\n"
                 "    @abc.abstractmethod\n    def test_call(self):\n        neighbour()\n",
                 {}, False, id="abstract-class"),
    pytest.param("class TestCustom:\n    def test_call(self):\n        neighbour()\n",
                 {"conftest.py": "def pytest_pycollect_makeitem(collector, name, obj):\n"
                  "    if name == 'TestCustom':\n        return []\n"}, False, id="custom-collector"),
    pytest.param("class TestNative:\n    __new__ = object.__new__\n"
                 "    def test_call(self):\n        neighbour()\n", {}, True, id="native-new"),
    pytest.param("import unittest\nclass Case(unittest.TestCase):\n"
                 "    def test_call(self):\n        neighbour()\n", {}, True, id="unittest"),
    pytest.param("class Base:\n    def test_call(self):\n        neighbour()\n"
                 "class TestInvalid(Base()):\n    pass\n", {}, "uncertain", id="instance-base"),
    *[pytest.param("def test_call():\n    neighbour()\n", {name: text}, False, id=name)
      for name, text in (
          ("pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
          (".pytest.toml", '[pytest]\npython_functions = ["check_*"]\n'),
          ("pytest.ini", "[pytest]\naddopts = -o python_functions=check_*\n"))],
    pytest.param("def TEST_call():\n    neighbour()\n",
                 {"pytest.ini": "[pytest]\npython_functions = test_*\n"}, os.name == "nt", id="glob-case"),
    pytest.param("from base import Ancestor as Parent\nclass TestLocal(Parent):\n    pass\n",
                 {"base.py": "from api import neighbour\nclass Ancestor:\n"
                  "    def test_call(self):\n        neighbour()\n"}, True, id="inherited-alias"),
    pytest.param("from base import *\nclass TestLocal(Ancestor):\n    pass\n",
                 {"base.py": "from api import neighbour\nclass Ancestor:\n"
                  "    def test_call(self):\n        neighbour()\n"}, "uncertain", id="unresolved-base"),
    pytest.param("class Parent:\n    def test_call(self):\n        neighbour()\n"
                 "flag = True\nclass TestLocal(Parent if flag else object):\n    pass\n",
                 {}, "uncertain", id="dynamic-base"),
    pytest.param("from meta import Factory\nclass TestCustom(metaclass=Factory):\n"
                 "    def test_call(self):\n        neighbour()\n",
                 {"meta.py": "Factory = type\n"}, True, id="unresolved-metaclass"),
]


def mapped_repository(tmp_path, source, extra, *, collector=None, monkeypatch=None):
    before = {"api.py": API, "test_api.py": "import abc\nfrom api import neighbour\n" + source, **extra}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    delta = repository(tmp_path, before, after)
    if collector is not None:
        monkeypatch.setattr(repo_tests, "collect", collector)
    return patch_review._change_neighbourhood(delta, ("test_api.py",), (), (), deadline=time.monotonic() + 60)


@pytest.mark.parametrize("source,extra,expected", CASES)
def test_reviewer_cases_use_real_confined_collection(tmp_path, real_collection_python, source, extra, expected):
    # No replacement of the collector, its bootstrap, config, conftest or result.
    record = mapped_repository(tmp_path, source, extra)
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == expected, record
    assert sibling["collection"] == ("uncertain" if expected == "uncertain" else "available"), record


@pytest.mark.parametrize("source,extra,expected", CASES)
@pytest.mark.parametrize("reason", ["no isolation", "refused", "timed out", "collection errors"])
def test_every_case_is_uncertain_without_collection(tmp_path, monkeypatch, source, extra, expected, reason):
    record = mapped_repository(tmp_path, source, extra, monkeypatch=monkeypatch,
                               collector=lambda *a, **k: {"status": "uncertain", "reason": reason})
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == "uncertain", record
    assert sibling["checks"] == []
    assert any(s["check"] == "test_api.py" and reason in s["reason"] for s in record["skipped"])


def test_text_only_map_has_no_collection_authority():
    before = {"api.py": API, "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=("test_api.py",))
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert any(s["check"] == "test_api.py" and "unavailable" in s["reason"] for s in record["skipped"])


def test_collected_helper_path_is_transitive_and_parameter_ids_are_opaque():
    before = {"api.py": API, "test_api.py": "from api import neighbour\n"
              "def helper():\n    neighbour()\ndef test_call():\n    helper()\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    node = "test_api.py::test_call[id::with.dots[inside]]"
    record = change_map.build(change(before, after), before, after, stated_checks=("test_api.py",),
                              collection={"test_api.py": collection_answer(before, {"status": "available", "nodes": [node]})})
    assert target(record, "neighbour")["exercised"] is True
    assert target(record, "neighbour")["checks"] == ["test_api.py"]


def test_collection_errors_and_partial_nodes_never_establish_a_negative(tmp_path):
    counted = tmp_path / "counted.json"
    counted.write_text(json.dumps({"nodes": ["test_api.py::test_call"], "exit": 2,
                                   "root": str(tmp_path)}), encoding="utf-8")
    found = repo_tests._read_collection(counted, 2, tmp_path)
    assert found["status"] == "uncertain"
    assert "nodes" not in found


@pytest.mark.parametrize("raw", [{}, {"nodes": "test_api.py::test_call"},
                                  {"nodes": ["../escape.py::test_call"]}])
def test_incomplete_or_malformed_collection_is_uncertain(tmp_path, raw):
    counted = tmp_path / "counted.json"
    counted.write_text(json.dumps(raw), encoding="utf-8")
    assert repo_tests._read_collection(counted, 0, tmp_path)["status"] == "uncertain"


def test_collect_only_never_runs_test_bodies(tmp_path, real_collection_python):
    repository(tmp_path, {"test_api.py": "def test_call():\n    raise RuntimeError('body ran')\n"}, {})
    found = repo_tests.collect(tmp_path, ["test_api.py"], timeout_s=30)
    assert found["status"] == "available", found
    assert found["nodes"] == ["test_api.py::test_call"]


def test_deselected_nodes_are_not_entry_points(tmp_path, real_collection_python):
    repository(tmp_path, {"test_api.py": "def test_call():\n    pass\ndef test_keep():\n    pass\n",
                          "pytest.ini": "[pytest]\naddopts = -k keep\n"}, {})
    found = repo_tests.collect(tmp_path, ["test_api.py"], timeout_s=30)
    assert found["status"] == "available", found
    assert found["nodes"] == ["test_api.py::test_keep"]


def test_collection_hooks_see_the_whole_selection(tmp_path, real_collection_python):
    before = {"api.py": API,
              "test_a.py": "from api import neighbour\ndef test_call():\n    neighbour()\n",
              "test_b.py": "def test_other():\n    pass\n",
              "conftest.py": "def pytest_collection_modifyitems(items):\n"
              "    if len(items) != 2:\n        items[:] = []\n"}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    record = patch_review._change_neighbourhood(delta, ("test_a.py", "test_b.py"), (), (),
                                               deadline=time.monotonic() + 60)
    assert target(record, "neighbour")["exercised"] is True, record
    assert target(record, "neighbour")["checks"] == ["test_a.py"]


def test_proposed_source_is_collected_in_a_copy(tmp_path, real_collection_python):
    repository(tmp_path, {"api.py": API}, {})
    source = "def test_call():\n    raise RuntimeError('body ran')\n"
    found = repo_tests.collect(tmp_path, ["test_extra.py"], sources={"test_extra.py": source}, timeout_s=30)
    assert found["status"] == "available", found
    assert found["nodes"] == ["test_extra.py::test_call"]
    assert not (tmp_path / "test_extra.py").exists()


def test_collect_always_requires_isolation_and_never_requests_a_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_tests, "checked_repo", lambda repo: repo)
    monkeypatch.setattr(repo_tests, "interpreter", lambda repo: "python")
    calls = []

    def run(repo, tests, **kwargs):
        calls.append(kwargs)
        raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "no isolation")

    monkeypatch.setattr(repo_tests, "_run", run)
    found = repo_tests.collect(tmp_path, ["test_api.py"])
    assert found["status"] == "uncertain"
    assert "G6_E_ISOLATION_UNAVAILABLE" in found["reason"]
    assert len(calls) == 1
    assert calls[0]["require_isolation"] is True and calls[0]["collect_only"] is True
    assert not calls[0].get("receipt")


def test_collection_refusal_never_changes_decided_fields(tmp_path, monkeypatch):
    from tests.harness.test_patch_review import BAD, GOOD, SUM, TEST, Recorded, result

    repository(tmp_path, {"calc.py": BAD, "test_calc.py": TEST}, {"calc.py": GOOD})
    Recorded(monkeypatch, default=result(["test_calc.py::test_add", "test_calc.py::test_negative"]),
             at_base=result(failed=["test_calc.py::test_add", "test_calc.py::test_negative"]))
    decided = []
    for answer in ({"status": "available", "nodes": []},
                   {"status": "uncertain", "reason": "collection refused"}):
        monkeypatch.setattr(repo_tests, "collect", lambda *a, **k: answer)
        reply = patch_review.review_patch(tmp_path, task="Repair addition", requirements=SUM)
        assert reply["ok"], reply
        assessment = reply["assessment"]
        decided.append({key: assessment[key] for key in (
            "outcome", "reasons", "requirements", "requirements_without_adequate_check", "summary", "next_actions")})
    assert decided[0] == decided[1]


@pytest.mark.parametrize("python_change", [False, True])
def test_expired_reader_keeps_the_budget_reason_without_collecting(monkeypatch, python_change):
    def unexpected(*args, **kwargs):
        pytest.fail("collection started after the deadline")

    monkeypatch.setattr(repo_tests, "collect", unexpected)
    delta = change({"api.py": API}, {"api.py": API.replace("return 1", "return 2")}) if python_change else change({}, {})
    record = patch_review._change_neighbourhood(delta, ("test_api.py",), (), (), deadline=0)
    assert record["targets"] == []
    assert any("budget" in item["reason"] for item in record["skipped"])


@pytest.mark.parametrize("selection", ["test_api.py", "test_api.py::TestB",
                                        "test_api.py::TestB::test_call[value::with.dots[brackets]]"])
def test_available_collection_keeps_inherited_calls_and_unrelated_selection(selection):
    from tests.harness.test_change_map_repair2 import CHECKS

    other = "test_api.py::TestA::test_other"
    collection = {selection: collection_answer({"test_api.py": CHECKS}, {"status": "available", "nodes": [
        "test_api.py::TestB::test_call[value::with.dots[brackets]]"]}),
        other: collection_answer({"test_api.py": CHECKS}, {"status": "available", "nodes": [other]})}
    calls = change_map.exercised([{"binding": "api.neighbour"}], {selection: CHECKS, other: CHECKS},
                                 collection=collection)
    assert calls == {"api.neighbour": [selection]}


@pytest.mark.parametrize("calls_neighbour", [False, True])
def test_stubbed_collector_keeps_definite_answers(tmp_path, monkeypatch, calls_neighbour):
    source = "def test_call():\n    " + ("neighbour()" if calls_neighbour else "pass") + "\n"
    record = mapped_repository(tmp_path, source, {}, monkeypatch=monkeypatch,
                               collector=lambda *a, **k: collection_answer({"test_api.py": "import abc\nfrom api import neighbour\n" + source}, {"status": "available", "nodes": ["test_api.py::test_call"]}))
    sibling = target(record, "neighbour")
    assert sibling["exercised"] is calls_neighbour
    assert sibling["collection"] == "available"
    assert sibling["checks"] == (["test_api.py"] if calls_neighbour else [])
