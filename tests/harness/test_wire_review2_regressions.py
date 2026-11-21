# SPDX-License-Identifier: Apache-2.0
"""Review 2 reproductions. Run in memory if tests/_review_wpf2 is unwritable."""
import ast
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement, tostring

from _pytest.junitxml import LogXML
from _pytest.reports import CollectReport

from g6.harness import completion_loop as loop
from g6.metaharness import steps
from g6.workforce import repo_tests as rt


def _failed_report():
    node = "test_probe.py::test_cross"
    return {
        "verdict": "failed", "said": "", "output": "", "rootdir": "",
        "test_outcomes": {
            node: {"setup": "passed", "call": "failed", "teardown": "passed"}
        },
        "collected_ids": [node], "omitted_ids": [], "failed_ids": [node],
        "setup_errors": {}, "failure_messages": {node: "ValueError: crashed"},
        "passed": 0, "failed": 1, "not_passed": 0, "total": 1,
        "isolation": {"backend": "recorded"},
    }


def test_malformed_failure_phase_is_unreadable_instead_of_crashing():
    raw = _failed_report()
    assert rt.read_report(raw).valid
    node = raw["failed_ids"][0]
    raw["failure_records"] = {
        node: [{"when": [], "kind": "ValueError", "module": "", "file": ""}]
    }
    assert not rt.read_report(raw).valid


def test_malformed_html_reply_does_not_crash_question_detection():
    # Ordinary malformed HTML from the agent, not executable content.
    assert loop.asks("<div><![bogus]>May I continue?</div>")


class MemoryXML:
    def __init__(self, root):
        self.data = tostring(root)

    def open(self, mode):
        assert mode == "rb"
        return BytesIO(self.data)


def test_junit_prefix_does_not_hide_a_repository_syntax_error():
    repo = Path.cwd()
    filename = str(repo / "broken.py")
    # This is pytest's actual writer, including its configured --junit-prefix.
    # No sessionfinish is called, so it writes no file.
    writer = LogXML(
        str(repo / "tests/_review_wpf2/unused.xml"),
        prefix="ci", family="xunit2",
    )
    report = CollectReport(
        "broken.py", "failed",
        f'File "{filename}", line 1\nSyntaxError: invalid syntax', [],
    )
    writer.pytest_collectreport(report)
    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    for reporter in writer.node_reporters_ordered:
        suite.append(reporter.to_xml())

    # Obtain the typed record from the real bootstrap implementation.
    counted_class = next(
        node for node in ast.parse(rt._BOOT).body
        if isinstance(node, ast.ClassDef) and node.name == "_Counted"
    )
    namespace = {}
    exec(compile(ast.Module(body=[counted_class], type_ignores=[]),
                 "<real-counted>", "exec"), namespace)
    error = SyntaxError("invalid syntax", (filename, 1, 1, "def broken(:"))
    record = namespace["_Counted"].record(SimpleNamespace(), report, error)
    records = {report.nodeid: [record]}
    assert record["when"] == "collect"
    assert rt._judge(2, rt._counts(MemoryXML(root), repo, (repo,)), "", []) == rt.FAILED

    counts = rt._counts(MemoryXML(root), repo, (repo,), records)
    verdict = rt._judge(2, counts, "", [])
    assert verdict == rt.FAILED, (verdict, counts)


def test_repair_step_runs_assertions_shown_in_a_test_function(monkeypatch):
    wrong = "```python\ndef add(a, b):\n    return a - b\n```"
    right = "```python\ndef add(a, b):\n    return a + b\n```"
    task = (
        "Write add(a, b). It must pass this test:\n"
        "```python\ndef test_add():\n    assert add(1, 2) == 3\n```"
    )
    checked = []
    calls = []

    def run_shown(code, imports, asserts, **kwargs):
        checked.append(tuple(asserts))
        scope = {}
        exec(code, scope)
        try:
            for statement in [*imports, *asserts]:
                exec(statement, scope)
        except AssertionError:
            return "AssertionError"
        return ""

    def complete(system, user):
        calls.append(user)
        return right

    monkeypatch.setattr(steps, "run_shown", run_shown)
    ctx = SimpleNamespace(
        callers={"local": None},
        caller=lambda _: SimpleNamespace(complete=complete),
    )
    step = steps.repair_with_tests()
    result = step.run(wrong, SimpleNamespace(task=task), ctx)
    assert result == right, (result, checked, calls)
