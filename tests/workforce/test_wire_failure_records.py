# SPDX-License-Identifier: Apache-2.0
"""G6's own per-test record (``_Counted``) keeps one typed record for each failed report, read from the exception
object pytest hands it, never from printed text: the kind (an assertion, ``pytest.fail``'s ``Failed``, or the error's
own class), the module an import error names, the file a syntax error names, and the file of the innermost frame.
The completion loop's crash class is read from the kind, and where a run's failure comes from (inside the repository
or outside it) from the records.

The differential below runs real pytest sessions and checks that the typed reading gives the prose reader's
(``_blame``, over pytest's JUnit text) placement on each. The prose reader stays only for a run whose own record holds
no failure record: pinned producer fixtures build such runs without pytest's exception hook (see the repair report)."""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop
from g6.workforce import repo_tests as rt


def _counted_plugin(counted: Path):
    """The bootstrap's own ``_Counted``, as the child runs it, writing to ``counted``."""
    plugin = next(node for node in ast.parse(rt._BOOT).body if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {"os": os, "json": json, "counted": str(counted)}
    exec(compile(ast.Module(body=[plugin], type_ignores=[]), "<real-counted>", "exec"), namespace)
    return namespace["_Counted"]()


def _run(tmp_path: Path, monkeypatch, files: dict[str, str]) -> tuple[int, Path, Path]:
    """A real pytest session in this process over ``files``, with the bootstrap's record and a JUnit report."""
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    config = tmp_path / "empty.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    counted, xml = tmp_path / "counted.json", tmp_path / "report.xml"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    before = set(sys.modules)
    try:
        code = pytest.main([*sorted(rel for rel in files if Path(rel).name.startswith("test_")), "-q", "-p",
                            "no:cacheprovider", "-c", str(config), "--rootdir", str(tmp_path), "--junitxml", str(xml)],
                           plugins=[_counted_plugin(counted)])
    finally:
        for name in set(sys.modules) - before:
            if str(getattr(sys.modules[name], "__file__", "") or "").startswith(str(tmp_path)):
                del sys.modules[name]
    return code, counted, xml


SCENARIOS = {
    "a syntax error in a repository file": (
        {"calc_syn.py": "def add(:\n    pass\n", "test_syn.py": "from calc_syn import add\n\n\ndef test_add():\n    assert add\n"},
        {"collection": 1, "placed": 1, "outside": 0}),
    "a broken import of a repository module": (
        {"calc_imp.py": "X = 1\n", "test_imp.py": "from calc_imp import missing\n\n\ndef test_x():\n    assert missing\n"},
        {"collection": 1, "placed": 1, "outside": 0}),
    "a missing outside dependency": (
        {"test_dep.py": "import g6_absent_dependency_one\n\n\ndef test_x():\n    assert g6_absent_dependency_one\n"},
        {"collection": 1, "placed": 1, "outside": 1}),
    "a real failure beside one": (
        {"test_mix.py": "def test_fail():\n    assert 1 == 2\n\n\ndef test_dep():\n    import g6_absent_dependency_two\n"},
        {"collection": 0, "placed": 0, "outside": 1}),
    "a raising repository test file": (
        {"test_raise.py": "raise RuntimeError('the module raises')\n\n\ndef test_x():\n    pass\n"},
        {"collection": 1, "placed": 1, "outside": 0}),
    "a module-level skip misused (pytest prints no traceback)": (
        {"test_skip.py": "import pytest\npytest.skip('later')\n\n\ndef test_x():\n    pass\n"},
        {"collection": 1, "placed": 0, "outside": 0}),
}


def _records(counted: Path) -> dict[str, list[dict[str, str]]]:
    records = json.loads(counted.read_text(encoding="utf-8"))["failure_records"]
    assert rt.REPORT_FIELDS["failure_records"].accepts(records)
    return records


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_typed_record_places_each_failure_where_the_prose_reader_does(tmp_path, monkeypatch, name):
    files, expected = SCENARIOS[name]
    _, counted, xml = _run(tmp_path, monkeypatch, files)
    assert rt._failure_records(counted) == _records(counted)
    prose = rt._counts(xml, tmp_path, (tmp_path,))
    typed = rt._counts(xml, tmp_path, (tmp_path,), rt._failure_records(counted))
    assert prose is not None and {key: prose[key] for key in expected} == expected, (name, prose)
    assert typed is not None and typed == prose, name


def test_the_typed_reading_does_not_read_the_reports_text(tmp_path, monkeypatch):
    """With the run's own records, a text that names an outside module places nothing outside."""
    files, _ = SCENARIOS["a broken import of a repository module"]
    _, counted, xml = _run(tmp_path, monkeypatch, files)
    xml.write_text(xml.read_text(encoding="utf-8").replace(
        "calc_imp", "g6_absent_dependency_four"), encoding="utf-8")
    assert rt._counts(xml, tmp_path, (tmp_path,))["outside"] == 1                       # the prose reader is led
    assert rt._counts(xml, tmp_path, (tmp_path,), rt._failure_records(counted))["outside"] == 0


def test_a_missing_or_mistyped_record_file_holds_no_records(tmp_path):
    assert rt._failure_records(tmp_path / "absent.json") == {}
    (tmp_path / "counted.json").write_text(json.dumps({"failure_records": {"t.py::x": [{"kind": "x"}]}}),
                                           encoding="utf-8")
    assert rt._failure_records(tmp_path / "counted.json") == {}


def test_each_failed_report_keeps_its_kind(tmp_path, monkeypatch):
    files = {"test_kinds.py": "import pytest\n\n\ndef test_assert():\n    assert 1 == 2\n\n\n"
                              "def test_fail():\n    pytest.fail('said so')\n\n\n"
                              "def test_raise():\n    raise ValueError('assert-like words')\n\n\n"
                              "def test_pass():\n    assert True\n"}
    _, counted, _ = _run(tmp_path, monkeypatch, files)
    records = _records(counted)
    assert {node.rsplit("::", 1)[1]: [r["kind"] for r in got] for node, got in records.items()} == {
        "test_assert": ["AssertionError"], "test_fail": ["Failed"], "test_raise": ["ValueError"]}
    progress = rt._progress({"passed": 1, "failures": 3, "errors": 0}, counted)
    assert progress["failure_records"] == records
    report = rt.read_report({**{key: value for key, value in progress.items() if key in rt.REPORT_FIELDS},
                             "verdict": "failed", "said": "", "output": "", "rootdir": "",
                             "isolation": {"backend": "recorded-review-fixture"}, "passed": 1, "failed": 3,
                             "not_passed": 0, "total": 4})
    assert report.valid


def test_the_record_names_the_module_and_the_file(tmp_path, monkeypatch):
    files = {"calc_rec.py": "def add(:\n", "test_rec_syntax.py": "import calc_rec\n\n\ndef test_x():\n    pass\n",
             "test_rec_import.py": "import g6_absent_dependency_three\n\n\ndef test_x():\n    pass\n"}
    _, counted, _ = _run(tmp_path, monkeypatch, files)
    records = _records(counted)
    [syntax] = records["test_rec_syntax.py"]
    [imported] = records["test_rec_import.py"]
    assert syntax["when"] == imported["when"] == "collect"
    assert Path(syntax["file"]).name == "calc_rec.py" and syntax["module"] == ""
    assert imported["module"] == "g6_absent_dependency_three"


def test_the_crash_class_comes_from_the_kind_not_the_words(monkeypatch):
    seen = {}
    monkeypatch.setattr(loop, "scout_gate", lambda text, **kwargs: seen.update(kwargs) or [])
    tree = SimpleNamespace(is_file=lambda: False)
    loop._gate(tree, loop.ProbeRun(1, {"t.py::test_cross": "failed"}, {"t.py::test_cross": "custom helper text"},
                                   kinds={"t.py::test_cross": "AssertionError"}))
    assert seen["broken"] == 0
    loop._gate(tree, loop.ProbeRun(1, {"t.py::test_cross": "failed"}, {"t.py::test_cross": "assert-like words"},
                                   kinds={"t.py::test_cross": "ValueError"}))
    assert seen["broken"] == 1
    # a report with no record for the case is read by its message, as before
    loop._gate(tree, loop.ProbeRun(1, {"t.py::test_cross": "failed"}, {"t.py::test_cross": "assert 1 == 2"}))
    assert seen["broken"] == 0


def test_the_completion_loop_reads_the_kinds_by_the_ids_it_reads_results_by():
    node = "test_x.py::test_cross"
    ran = {"verdict": "failed", "said": "", "output": "", "rootdir": "tests",
           "test_outcomes": {node: {"setup": "passed", "call": "failed", "teardown": "passed"}},
           "collected_ids": [node], "omitted_ids": [], "failed_ids": [node], "setup_errors": {},
           "failure_messages": {node: "helper failed"},
           "failure_records": {node: [{"when": "call", "kind": "AssertionError", "module": "", "file": "test_x.py"}]},
           "passed": 0, "failed": 1, "not_passed": 0, "total": 1, "isolation": {"backend": "recorded"}}
    assert rt.read_report(ran).valid
    got = loop._read(SimpleNamespace(repo=Path("."), sources=()), [ran])
    assert got.kinds == {"tests/test_x.py::test_cross": "AssertionError"} and set(got.kinds) == set(got.results)
