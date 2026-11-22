# SPDX-License-Identifier: Apache-2.0
"""Assertion evidence needs a typed record for every failed phase and a test origin."""
import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt


def _read(records, *, teardown="passed", repeat_without_records=False):
    nodes = [f"test_probe.py::test_cross[{i}]" for i in range(4)]
    nodes += [f"test_probe.py::test_random[{i}]" for i in range(loop.MIN_RANDOM)]
    raw = {
        "verdict": "failed", "said": "", "output": "", "rootdir": "",
        "test_outcomes": {node: {"setup": "passed", "call": "failed" if node == nodes[0] else "passed",
                                 "teardown": teardown if node == nodes[0] else "passed"} for node in nodes},
        "collected_ids": nodes, "omitted_ids": [], "failed_ids": [nodes[0]], "setup_errors": {},
        "failure_messages": {nodes[0]: "AssertionError: a label cannot establish an assertion"},
        "failure_records": {nodes[0]: records} if records is not None else {},
        "passed": len(nodes) - 1, "failed": 1, "not_passed": 0, "total": len(nodes),
        "isolation": {"backend": "recorded"},
    }
    runs = [raw, {**raw, "failure_records": {}}] if repeat_without_records else [raw]
    got = loop._read(SimpleNamespace(repo=Path.cwd(), sources=()), runs)
    tree = SimpleNamespace(is_file=lambda: True, read_text=lambda **kwargs: json.dumps(
        {"dimensions": {"a": [0, 1], "b": [0, 1]}}))
    return got, loop._gate(tree, got)


@pytest.mark.parametrize("records", [None, [], [
    {"when": "call", "kind": "AssertionError", "module": "", "file": ""},
]])
def test_legacy_failure_without_origin_is_not_assertion_evidence(records):
    got, problems = _read(records)
    assert got.usable
    assert any("not an assertion" in p for p in problems)


def test_a_missing_failed_phase_record_is_not_assertion_evidence():
    _, problems = _read([{"when": "call", "kind": "AssertionError", "module": "", "file": "",
                          "assertion_from_test": True}], teardown="failed")
    assert any("not an assertion" in p for p in problems)


@pytest.mark.parametrize("fail", [False, True])
def test_real_test_assertion_has_origin_evidence(fail):
    cls = next(n for n in ast.parse(rt._BOOT).body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    namespace = {}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<recorder>", "exec"), namespace)
    node = SimpleNamespace(config=SimpleNamespace(rootpath=Path.cwd()), path=Path(__file__))
    try:
        if fail:
            pytest.fail("from a test helper")
        raise AssertionError("from the test body")
    except BaseException as error:
        record = namespace["_Counted"].record(node, SimpleNamespace(when="call"), error)
    assert record.get("assertion_from_test") is True


def test_assertion_in_repository_implementation_is_not_a_test_body_assertion():
    cls = next(n for n in ast.parse(rt._BOOT).body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    namespace = {}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<recorder>", "exec"), namespace)
    node = SimpleNamespace(config=SimpleNamespace(rootpath=Path.cwd()), path=Path(__file__))
    code = compile("raise AssertionError('implementation invariant')", "src/g6/core/markdown.py", "exec")
    try:
        exec(code, {"__name__": "g6.core.markdown"})
    except AssertionError as error:
        record = namespace["_Counted"].record(node, SimpleNamespace(when="call"), error)
    assert record["assertion_from_test"] is False


def test_every_failed_phase_with_test_origin_is_assertion_evidence():
    _, problems = _read([{"when": "call", "kind": "AssertionError", "module": "", "file": "",
                          "assertion_from_test": True}])
    assert not problems


def test_later_recordless_failure_does_not_inherit_earlier_assertion_evidence():
    _, problems = _read([{"when": "call", "kind": "AssertionError", "module": "", "file": "",
                          "assertion_from_test": True}], repeat_without_records=True)
    assert any("not an assertion" in p for p in problems)


def _session(tmp_path, monkeypatch, body, helper=""):
    source = "import pytest\nimport bdb\n" + helper + "\n" + body
    (tmp_path / "test_origin_probe.py").write_text(source, encoding="utf-8")
    counted = tmp_path / "counted.json"
    cls = next(n for n in ast.parse(rt._BOOT).body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    namespace = {"os": os, "json": json, "counted": str(counted)}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<recorder>", "exec"), namespace)
    monkeypatch.chdir(tmp_path)
    before = set(sys.modules)
    try:
        pytest.main([str(tmp_path / "test_origin_probe.py"), "-q"], plugins=[namespace["_Counted"]()])
    finally:
        for name in set(sys.modules) - before:
            if str(getattr(sys.modules[name], "__file__", "") or "").startswith(str(tmp_path)):
                del sys.modules[name]
    return json.loads(counted.read_text(encoding="utf-8"))


def test_another_recordless_failure_is_not_assertion_evidence(tmp_path, monkeypatch):
    stored = _session(tmp_path, monkeypatch,
                      "def test_cross():\n    raise AssertionErrorQuit()\n",
                      "class AssertionErrorQuit(bdb.BdbQuit):\n    pass\n")
    assert stored["test_outcomes"]["test_origin_probe.py::test_cross"]["call"] == "failed"
    assert not stored["failure_records"]
    _, problems = _read(None)
    assert any("not an assertion" in p for p in problems)


@pytest.mark.parametrize("statement", ["pytest.skip('later')", "pytest.xfail('later')"])
def test_skip_and_xfail_inside_test_supply_no_assertion(tmp_path, monkeypatch, statement):
    stored = _session(tmp_path, monkeypatch, f"def test_cross():\n    {statement}\n")
    assert stored["test_outcomes"]["test_origin_probe.py::test_cross"]["call"] == "skipped"
    assert not stored["failure_records"]


@pytest.mark.parametrize(("body", "helper", "origin"), [
    ("def test_cross():\n    assert False\n", "", True),
    ("def test_cross():\n    pytest.fail('test failure')\n", "", True),
    ("def test_cross():\n    helper()\n", "def helper():\n    pytest.fail('helper failure')\n", True),
    ("async def test_cross():\n    assert False\n", "", False),
])
def test_recorder_marks_test_origin_without_using_failure_words(tmp_path, monkeypatch, body, helper, origin):
    stored = _session(tmp_path, monkeypatch, body, helper)
    record = stored["failure_records"]["test_origin_probe.py::test_cross"][0]
    assert record.get("assertion_from_test") is origin

