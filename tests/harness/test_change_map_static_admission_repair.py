# SPDX-License-Identifier: Apache-2.0
"""Static admission must account for external suppression and nested config."""
import time

import pytest

from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API
from tests._review_changemap1.test_review import repository


SOURCE = "from api import neighbour\nclass TestAPI:\n    def test_call(self):\n        neighbour()\n"
NODE = "suite/deep/test_api.py::TestAPI::test_call"


@pytest.mark.parametrize("write", [
    "TestAPI.__test__ = False", "TestAPI.test_call.__test__ = False",
    "setattr(TestAPI, '__test__', False)", "setattr(TestAPI, name, False)",
    "vars(TestAPI)['__test__'] = False", "TestAPI.__dict__[name] = False",
    "__test__ = True", "def suppress():\n    TestAPI.__test__ = False",
    "class Other:\n    __test__ = unknown()",
    "class Other:\n    if flag:\n        __test__ = True",
    "class Other:\n    global __test__\n    __test__ = True",
    "from builtins import setattr as put\nput(TestAPI, '__test__', False)",
    "put = setattr\nput(TestAPI, name, False)",
    "def suppress():\n    for __test__ in [False]:\n        pass",
    "vars(TestAPI.test_call).update(__test__=False)",
    "TestAPI.test_call.__dict__.update({'__test__': False})",
])
def test_external_or_dynamic_test_assignments_require_collection(write):
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE + write + "\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,))
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []


@pytest.mark.parametrize("name", [*change_map.PYTEST_CONFIGS, "conftest.py"])
@pytest.mark.parametrize("folder", ["", "suite/", "suite/deep/"])
def test_each_ancestor_configuration_blocks_static_admission(name, folder):
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE, folder + name: ""}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,))
    assert target(record, "neighbour")["exercised"] == "uncertain"


def test_sibling_configuration_does_not_block_an_explicit_static_body():
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE, "other/pytest.ini": ""}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,))
    assert target(record, "neighbour")["exercised"] is True


@pytest.mark.parametrize("name", change_map.PYTEST_CONFIGS)
def test_reader_preserves_nested_configuration_when_collection_is_unavailable(tmp_path, name):
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE, "suite/" + name: ""}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    record = patch_review._change_neighbourhood(
        delta, (NODE,), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: {"status": "uncertain", "reason": "no collection"})
    assert target(record, "neighbour")["exercised"] == "uncertain"


def test_matching_collection_can_select_a_body_despite_static_suppression():
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE + "TestAPI.__test__ = False\n"}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(NODE,), collection={
        NODE: {"status": "available", "nodes": [NODE],
               "bodies": {NODE: {"file": "suite/deep/test_api.py", "line": 3}}}})
    assert target(record, "neighbour")["exercised"] is True


def test_reader_keeps_unreadable_configuration_as_a_static_admission_barrier(tmp_path, monkeypatch):
    before = {"api.py": API, "suite/deep/test_api.py": SOURCE, "suite/pytest.ini": ""}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    read = patch_review._change_map_text

    def unreadable(root, path, limit):
        return b"\xff" if path.name == "pytest.ini" else read(root, path, limit)

    monkeypatch.setattr(patch_review, "_change_map_text", unreadable)
    record = patch_review._change_neighbourhood(
        delta, (NODE,), (), (), deadline=time.monotonic() + 30,
        collector=lambda *a, **k: {"status": "uncertain", "reason": "no collection"})
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert any("source unavailable" in entry["reason"] for entry in record["skipped"])
