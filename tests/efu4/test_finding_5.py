"""Literal pytest parameter names must not borrow another body's failure."""
import ast
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from g6.harness import patch_review as review, requirement_evidence as evidence
from g6.workforce import repo_tests as runner


def test_literal_pytest_identity_cannot_borrow_a_negative_control(tmp_path, monkeypatch):
    stem = "test_" + tmp_path.name
    test = tmp_path / (stem + ".py")
    test.write_text('import pytest\n@pytest.mark.parametrize("value", [1, 2], ids=["a", "a][b"])\n'
                    'def test_add(value): assert value == 1\n', encoding="utf-8")
    first, second = (test.name + "::test_add[" + ident + "]" for ident in ("a", "a][b"))
    selections = [first, second]
    config = tmp_path / "empty.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    counted, xml = tmp_path / "counted.json", tmp_path / "report.xml"
    plugin_class = next(node for node in ast.parse(runner._BOOT).body
                        if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {"os": os, "json": json, "counted": str(counted)}
    exec(compile(ast.Module(body=[plugin_class], type_ignores=[]), "<real-counted>", "exec"), namespace)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    code = pytest.main([*selections, "-q", "-p", "no:cacheprovider", "-c", str(config),
                        "--rootdir", str(tmp_path), "--junitxml", str(xml)], plugins=[namespace["_Counted"]()])
    counts = runner._progress(runner._counts(xml, tmp_path, (tmp_path,)), counted)
    baseline = {**{key: value for key, value in counts.items() if key in runner.REPORT_FIELDS},
                "verdict": runner._judge(code, counts, "", []), "rootdir": "", "said": "", "output": "",
                "isolation": {"backend": "recorded-review-fixture"}}
    baseline.update(passed=counts["passed"], failed=counts["failures"] + counts["errors"],
                    not_passed=counts["skipped"], total=counts["passed"] + counts["failures"] + counts["errors"])
    read = runner.read_report(baseline)
    assert read.valid, baseline
    assert sorted(phase.body() for phase in read.phases.values()) == ["failed", "passed"]
    patch = {**baseline, "verdict": "passed", "passed": 2, "failed": 0, "failed_ids": [],
             "failure_messages": {}, "test_outcomes": {node: {"setup": "passed", "call": "passed", "teardown": "passed"}
                                                        for node in baseline["test_outcomes"]}}
    monkeypatch.setattr(review, "_execute_at_base", lambda *a, **kw: baseline)
    monkeypatch.setattr(review, "_execute", lambda *a, **kw: patch)
    requests = [review.Requirement(id="R1", text="first identity works", checks=[first]),
                review.Requirement(id="R2", text="second identity works", checks=[second])]
    change = SimpleNamespace(files=("calc.py",), sources=("calc.py",), base_commit="0" * 40)
    norm = {first: first, second: second}
    verdicts = review._verdicts(selections, patch)
    runs, deadline = [], time.monotonic() + 30
    review._at_base(change, requests, norm, verdicts, deadline, [], runs, [], [])
    again = review._run_again(Path.cwd(), change, requests, norm, verdicts, runs, deadline, "a" * 64)
    assessed = evidence.assess([review._requirement(req, verdicts, norm) for req in requests], runs,
                               artifact="a" * 64, base_commit=change.base_commit,
                               check_sha={first: "c" * 64, second: "d" * 64}, written=lambda _: False,
                               environment={}, again=again)
    assert assessed["requirements"][1]["checked"] is True
    assert assessed["requirements"][0]["checked"] is False
