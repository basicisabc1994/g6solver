# SPDX-License-Identifier: Apache-2.0
"""Review 1 of the follow-up: a failure's kind is read from its real type, never from an overridable ``__class__``;
a phase holds one record; and a case is an assertion only when every failed phase's record is one."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt


def _record(kind, when="call"):
    return {"when": when, "kind": kind, "module": "", "file": ""}


def _run(records, teardown="passed"):
    nodes = [f"test_probe.py::test_cross[{i}]" for i in range(4)]
    nodes += [f"test_probe.py::test_random[{i}]" for i in range(loop.MIN_RANDOM)]
    raw = {
        "verdict": "failed", "said": "", "output": "", "rootdir": "",
        "test_outcomes": {node: {"setup": "passed", "call": "failed" if node == nodes[0] else "passed",
                                 "teardown": teardown if node == nodes[0] else "passed"} for node in nodes},
        "collected_ids": nodes, "omitted_ids": [], "failed_ids": [nodes[0]],
        "setup_errors": {}, "failure_messages": {nodes[0]: "assert 1 == 2"},
        "failure_records": {nodes[0]: records},
        "passed": len(nodes) - 1, "failed": 1, "not_passed": 0, "total": len(nodes),
        "isolation": {"backend": "recorded"},
    }
    got = loop._read(SimpleNamespace(repo=Path.cwd(), sources=()), [raw])
    tree = SimpleNamespace(is_file=lambda: True,
                           read_text=lambda **kwargs: json.dumps({"dimensions": {"a": [0, 1], "b": [0, 1]}}))
    return rt.read_report(raw), got, loop._gate(tree, got)


def _counted():
    counted = next(node for node in ast.parse(rt._BOOT).body
                   if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {}
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "<real-counted>", "exec"), namespace)
    return namespace["_Counted"]


class _DisguisedCrash(RuntimeError):
    @property
    def __class__(self):
        return AssertionError


class _DisguisedImport(RuntimeError):
    name = "g6_outside_module"

    @property
    def __class__(self):
        return ModuleNotFoundError


def test_a_crash_that_claims_another_class_keeps_its_real_kind():
    error = _DisguisedCrash("runtime crash")
    assert isinstance(error, AssertionError)          # what isinstance would have said
    record = _counted().record(SimpleNamespace(), SimpleNamespace(when="call"), error)
    assert record["kind"] == "_DisguisedCrash"
    report, got, problems = _run([record])
    assert report.valid and got.usable
    assert any("not an assertion" in problem for problem in problems)


def test_a_disguised_import_error_names_no_module():
    record = _counted().record(SimpleNamespace(), SimpleNamespace(when="call"), _DisguisedImport("crash"))
    assert record["module"] == ""


@pytest.mark.parametrize("kinds", [("AssertionError", "RuntimeError"), ("RuntimeError", "AssertionError"),
                                   ("AssertionError", "AssertionError")])
def test_two_records_on_one_phase_make_the_report_invalid(kinds):
    report, got, _ = _run([_record(kind) for kind in kinds])
    assert not report.valid and not got.usable


def test_a_crash_in_another_failed_phase_is_the_cases_kind():
    report, got, problems = _run([_record("AssertionError"), _record("RuntimeError", "teardown")],
                                 teardown="failed")
    assert report.valid and got.kinds == {"test_probe.py::test_cross[0]": "RuntimeError"}
    assert any("not an assertion" in problem for problem in problems)
    # every failed phase an assertion: still an assertion
    _, got, _ = _run([_record("AssertionError"), _record("Failed", "teardown")], teardown="failed")
    assert got.kinds == {"test_probe.py::test_cross[0]": "AssertionError"}
