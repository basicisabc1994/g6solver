# SPDX-License-Identifier: Apache-2.0
"""Assertion ownership is a property of the entire path, including calls back into tests."""
import ast
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt


ASSERTIONS = {
    "assert": "assert False",
    "raise": "raise AssertionError('mismatch')",
    "fail": "pytest.fail('mismatch')",
    "unittest-equal": "unittest.TestCase().assertEqual(1, 2)",
    "unittest-sequence": "unittest.TestCase().assertSequenceEqual([1], [2])",
    "unittest-raises": "with unittest.TestCase().assertRaises(ValueError):\n    pass",
    "unittest-match": "with unittest.TestCase().assertRaisesRegex(ValueError, 'expected'):\n"
                      "    raise ValueError('wrong')",
    "pytest-raises": "with pytest.raises(ValueError):\n    pass",
    "pytest-match": "with pytest.raises(ValueError, match='expected'):\n    raise ValueError('wrong')",
    "pytest-exception-match": "with pytest.raises(ValueError) as caught:\n    raise ValueError('wrong')\n"
                              "caught.match('expected')",
}
PATHS = [owners for size in range(4) for owners in itertools.product(("test", "implementation"), repeat=size)]


@pytest.fixture(scope="module")
def recorder():
    cls = next(n for n in ast.parse(rt._BOOT).body if isinstance(n, ast.ClassDef) and n.name == "_Counted")
    namespace = {}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<recorder>", "exec"), namespace)
    return namespace["_Counted"]


def _record(recorder, statement, owners=(), *, terminal="tests/support/assertions.py", module="test_helpers",
            test_file="tests/test_path_probe.py"):
    root = Path.cwd()
    namespace = {"__name__": module}
    source = "import pytest\nimport unittest\ndef fail_here():\n" + "\n".join(
        "    " + line for line in statement.splitlines()) + "\n"
    exec(compile(source, str(root / terminal), "exec"), namespace)
    called = namespace["fail_here"]
    for index, owner in reversed(list(enumerate(owners))):
        directory = "tests/support" if owner == "test" else "src"
        namespace = {"__name__": f"bridge_{index}", "next_call": called}
        exec(compile("def bridge():\n    next_call()\n", str(root / directory / f"bridge_{index}.py"), "exec"),
             namespace)
        called = namespace["bridge"]
    namespace = {"__name__": "test_path_probe", "next_call": called}
    exec(compile("def test_probe():\n    next_call()\n", str(root / test_file), "exec"), namespace)
    try:
        namespace["test_probe"]()
    except BaseException as error:
        node = SimpleNamespace(path=root / test_file, config=SimpleNamespace(rootpath=root))
        return recorder.record(node, SimpleNamespace(when="call"), error)
    raise AssertionError("the assertion scenario did not fail")


def _problems(record):
    nodes = [f"test_probe.py::test_cross[{i}]" for i in range(4)]
    nodes += [f"test_probe.py::test_random[{i}]" for i in range(loop.MIN_RANDOM)]
    raw = {
        "verdict": "failed", "said": "", "output": "", "rootdir": "",
        "test_outcomes": {node: {"setup": "passed", "call": "failed" if node == nodes[0] else "passed",
                                 "teardown": "passed"} for node in nodes},
        "collected_ids": nodes, "omitted_ids": [], "failed_ids": [nodes[0]], "setup_errors": {},
        "failure_messages": {nodes[0]: "failure"}, "failure_records": {nodes[0]: [record]},
        "passed": len(nodes) - 1, "failed": 1, "not_passed": 0, "total": len(nodes),
        "isolation": {"backend": "recorded"},
    }
    assert rt.read_report(raw).valid
    got = loop._read(SimpleNamespace(repo=Path.cwd(), sources=()), [raw])
    frozen, _ = loop._snapshot_probe(got)
    assert frozen.usable
    tree = SimpleNamespace(is_file=lambda: True, read_text=lambda **kw: json.dumps(
        {"dimensions": {"a": [0, 1], "b": [0, 1]}}))
    return loop._gate(tree, frozen)


@pytest.mark.parametrize("statement", ASSERTIONS.values(), ids=ASSERTIONS)
def test_each_standard_assertion_family_survives_test_owned_helpers(recorder, statement):
    record = _record(recorder, statement, ("test", "test"))
    assert record["assertion_from_test"] is True
    assert _problems(record) == []


@pytest.mark.parametrize("statement", ASSERTIONS.values(), ids=ASSERTIONS)
@pytest.mark.parametrize("owners", PATHS, ids=lambda owners: "-".join(owners) or "direct")
def test_every_implementation_position_vetoes_test_origin(recorder, statement, owners):
    record = _record(recorder, statement, owners)
    if "implementation" in owners:
        assert record["assertion_from_test"] is False
        assert any("not an assertion" in problem for problem in _problems(record))
    else:
        assert record["assertion_from_test"] is True
        assert _problems(record) == []


@pytest.mark.parametrize("terminal", ["src/implementation.py", "src/test_implementation.py", "vendor/helpers.py"])
@pytest.mark.parametrize("statement", ASSERTIONS.values(), ids=ASSERTIONS)
def test_assertion_helper_names_never_grant_implementation_ownership(recorder, terminal, statement):
    record = _record(recorder, statement, terminal=terminal, module="_pytest.outcomes")
    assert record["assertion_from_test"] is False
    assert any("not an assertion" in problem for problem in _problems(record))


@pytest.mark.parametrize("statement", ["pytest.fail('mismatch')", "unittest.TestCase().assertEqual(1, 2)",
                                       "with pytest.raises(ValueError):\n    pass"])
def test_a_root_level_collected_file_can_use_standard_assertion_helpers(recorder, statement):
    record = _record(recorder, statement, terminal="test_path_probe.py", test_file="test_path_probe.py")
    assert record["assertion_from_test"] is True


def test_a_foreign_frame_cannot_borrow_a_standard_helpers_filename(recorder):
    # Compile metadata only: do not read or write the installed helper's file.
    record = _record(recorder, "raise AssertionError('mismatch')", terminal=pytest.raises.__code__.co_filename,
                     module=pytest.raises.__module__)
    assert record["assertion_from_test"] is False


def test_no_test_entry_supplies_no_assertion_evidence(recorder):
    try:
        raise AssertionError("a recorder caller is not necessarily a collected test")
    except AssertionError as error:
        node = SimpleNamespace(path=Path.cwd() / "test_unentered.py", config=SimpleNamespace(rootpath=Path.cwd()))
        record = recorder.record(node, SimpleNamespace(when="call"), error)
    assert record["assertion_from_test"] is False


@pytest.mark.parametrize("statement", ASSERTIONS.values(), ids=ASSERTIONS)
def test_an_ancestor_conftest_is_test_owned(recorder, statement):
    record = _record(recorder, statement, terminal="conftest.py", test_file="test_path_probe.py")
    assert record["assertion_from_test"] is True


def test_a_standard_assertion_cannot_launder_an_implementation_comparison(recorder):
    namespace = {"__name__": "implementation"}
    exec(compile("class Value:\n    def __eq__(self, other):\n        raise AssertionError('invariant')\n",
                 str(Path.cwd() / "src/comparison.py"), "exec"), namespace)
    test_file = Path.cwd() / "tests/test_comparison.py"
    namespace = {"__name__": "test_comparison", "Value": namespace["Value"]}
    exec(compile("import unittest\ndef test_probe():\n    unittest.TestCase().assertEqual(Value(), 2)\n",
                 str(test_file), "exec"), namespace)
    try:
        namespace["test_probe"]()
    except AssertionError as error:
        node = SimpleNamespace(path=test_file, config=SimpleNamespace(rootpath=Path.cwd()))
        record = recorder.record(node, SimpleNamespace(when="call"), error)
    assert record["assertion_from_test"] is False
    assert any("not an assertion" in problem for problem in _problems(record))


def test_a_non_assertion_from_test_owned_code_stays_a_crash(recorder):
    record = _record(recorder, "raise RuntimeError('assert-like words')")
    assert record["assertion_from_test"] is False
    assert any("not an assertion" in problem for problem in _problems(record))
