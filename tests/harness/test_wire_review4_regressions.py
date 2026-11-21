# SPDX-License-Identifier: Apache-2.0
"""Review 4 reproductions for the per-test report: a collection error's record needs a collection failure the report
lists (finding 1), and a failure's kind is an assertion by its real type, never by its class's name (finding 2)."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt


def _report(nodes, failed=()):
    failed = set(failed)
    return {
        "verdict": "failed" if failed else "passed",
        "said": "", "output": "", "rootdir": "",
        "test_outcomes": {
            node: {"setup": "passed", "call": "failed" if node in failed else "passed", "teardown": "passed"}
            for node in nodes
        },
        "collected_ids": nodes, "omitted_ids": [],
        "failed_ids": sorted(failed), "setup_errors": {},
        "failure_messages": {node: "custom runtime crash" for node in failed},
        "passed": len(nodes) - len(failed), "failed": len(failed),
        "not_passed": 0, "total": len(nodes),
        "isolation": {"backend": "recorded"},
    }


def _collect_record():
    return {"when": "collect", "kind": "SyntaxError", "module": "", "file": "broken.py"}


def test_a_collect_record_without_its_collection_failure_makes_the_report_unknown():
    raw = _report(["test_probe.py::test_cross"])
    assert rt.established(raw, sources=()).of() == rt.PASSED
    raw["failure_records"] = {"broken.py": [_collect_record()]}
    report = rt.read_report(raw)
    assert not report.valid
    assert rt.established(raw, sources=()).of() == "unknown"


def test_a_collect_record_on_a_listed_collection_failure_is_read():
    raw = _report(["test_probe.py::test_cross"])
    raw.update(verdict="failed", failure_messages={"broken.py": "SyntaxError: invalid syntax"},
               failure_records={"broken.py": [_collect_record()]}, failed=1, total=2)
    report = rt.read_report(raw)
    assert report.valid and report.collection_errors == frozenset({"broken.py"})
    # a record on a node that ran a phase is still no collection error, message or not
    raw["failure_records"] = {"test_probe.py::test_cross": [_collect_record()]}
    assert not rt.read_report(raw).valid


def _real_counted():
    counted = next(node for node in ast.parse(rt._BOOT).body
                   if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {}
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "<real-counted>", "exec"), namespace)
    return namespace["_Counted"]


def _record_of(error):
    return _real_counted().record(SimpleNamespace(), SimpleNamespace(when="call"), error)


@pytest.mark.parametrize("name", ["AssertionError", "Failed"])
def test_a_crash_named_like_an_assertion_is_not_one(name):
    crash_type = type(name, (RuntimeError,), {})
    try:
        raise crash_type("custom runtime crash")
    except RuntimeError as error:
        message = pytest.ExceptionInfo.from_exception(error).getrepr().reprcrash.message
        record = _record_of(error)
    assert record["kind"] not in ("AssertionError", "Failed") and record["kind"].endswith("." + name)

    nodes = [f"test_probe.py::test_cross[{i}]" for i in range(4)]
    nodes += [f"test_probe.py::test_random[{i}]" for i in range(loop.MIN_RANDOM)]
    raw = _report(nodes, [nodes[0]])
    raw["failure_records"] = {nodes[0]: [record]}
    raw["failure_messages"] = {nodes[0]: message}
    assert rt.read_report(raw).valid
    got = loop._read(SimpleNamespace(repo=Path.cwd(), sources=()), [raw])
    assert got.usable
    tree = SimpleNamespace(is_file=lambda: True,
                           read_text=lambda **kwargs: json.dumps({"dimensions": {"a": [0, 1], "b": [0, 1]}}))
    legacy = loop.ProbeRun(got.exit, got.results, got.texts)
    assert any("not an assertion" in problem for problem in loop._gate(tree, legacy))
    assert any("not an assertion" in problem for problem in loop._gate(tree, got))


class _OwnAssertion(AssertionError):
    pass


def _raise(error):
    raise error


@pytest.mark.parametrize("raising, kind", [
    (lambda: pytest.fail("said so"), "Failed"),
    (lambda: _raise(_OwnAssertion("a subclass is an assertion")), "AssertionError"),
    (lambda: _raise(ValueError("assert-like words")), "ValueError"),
])
def test_the_real_types_keep_their_kinds(raising, kind):
    with pytest.raises(BaseException) as caught:  # pytest's Failed is not an Exception
        raising()
    assert _record_of(caught.value)["kind"] == kind
