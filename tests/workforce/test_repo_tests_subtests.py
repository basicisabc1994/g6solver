# SPDX-License-Identifier: Apache-2.0
"""Subtest regressions replay pytest 9.1.1's capture in data/subtest_reports.json.

The fixture records pytest.main's real reports, including the passing unittest
parent after a failed subtest. Doubles add only the failure text used by G6.
"""
import ast
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.harness import patch_review
from g6.workforce import repo_tests as rt


CAPTURE = json.loads(Path(__file__).with_name("data").joinpath("subtest_reports.json").read_text(encoding="utf-8"))
CASES = CAPTURE["cases"]
# Expected node result and subtest counts, independent of the producer.
EXPECTED = {
    "fixture_pass": ("passed", (2, 0, 0)),
    "fixture_fail": ("failed", (1, 1, 0)),
    "unittest_pass": ("passed", (2, 0, 0)),
    "unittest_fail": ("failed", (1, 1, 0)),
    "fixture_skip": ("passed", (0, 0, 1)),
    "unittest_skip": ("passed", (0, 0, 1)),
    "xfail_fail": ("passed", (0, 0, 1)),
    "xfail_pass": ("passed", (1, 0, 0)),
}


def producer(path):
    tree = ast.parse(rt._BOOT)
    counted = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "_Counted")
    namespace = {"json": json, "counted": str(path)}
    exec(compile(ast.Module(body=[counted], type_ignores=[]), "subtest-reporter", "exec"), namespace)
    return namespace["_Counted"]()


def replay(path, case, *, detection="both", parent=None):
    counted = producer(path)
    node = CASES[case]["reports"][0]["nodeid"]
    counted.collected, counted.collected_ids = 1, {node}
    for shape in CASES[case]["reports"]:
        fields = {key: value for key, value in shape.items() if key not in ("class", "context")}
        is_subtest = "context" in shape
        if is_subtest and detection != "class":
            context = shape["context"]
            if detection in ("msg", "kwargs"):
                context = {detection: context[detection]}
            fields["context"] = SimpleNamespace(**context)
        if not is_subtest and fields["when"] == "call" and parent:
            fields["outcome"] = parent
        fields.update(failed=fields["outcome"] == "failed", longrepr="AssertionError: item failed")
        name = shape["class"] if detection in ("both", "class") else "CompatibleReport"
        report = type(name, (), {})()
        report.__dict__.update(fields)
        counted.pytest_runtest_logreport(report)
    counted.pytest_sessionfinish(SimpleNamespace(exitstatus=CASES[case]["exitstatus"]))
    return json.loads(path.read_text(encoding="utf-8")), node


def final_report(raw, verdict):
    return {**raw, "verdict": verdict, "said": "", "output": ""}


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("detection", ["both", "class", "msg", "kwargs"])
def test_recorded_subtests_preserve_parent_phases_and_are_readable(tmp_path, case, detection):
    raw, node = replay(tmp_path / "counted.json", case, detection=detection)
    want, counts = EXPECTED[case]
    report = rt.read_report(final_report(raw, want))
    assert report.valid, report.withheld
    assert report.body_result(node) == want
    assert report.body_result(node.partition("::")[0]) == want
    assert report.outcome == want
    assert node in report.failed if want == "failed" else node not in report.failed
    assert raw["test_outcomes"][node] == {
        row["when"]: row["outcome"] for row in CASES[case]["reports"] if row["class"] == "TestReport"}
    subtests = raw["subtests"][node]
    assert tuple(subtests[key] for key in ("passed", "failed", "skipped")) == counts
    assert ("item failed" in subtests["messages"]) is (want == "failed")
    assert rt.read_report(report.wire()).body_result(node) == want


@pytest.mark.parametrize("parent", ["passed", "failed", "skipped"])
def test_failed_subtest_overrides_every_parent_outcome_for_patch_review(tmp_path, parent):
    raw, node = replay(tmp_path / "counted.json", "unittest_fail", parent=parent)
    raw = final_report(raw, "failed")
    report = rt.read_report(raw)
    assert report.valid and report.body_result(node) == "failed"
    assert report.failed == {node}
    assert patch_review.body_of(patch_review.phases_of(raw)[node]) == "failed"
    record = patch_review._record(2, "derived", [node], raw)
    assert record["executed_outcomes"] == {node: "failed"}
    assert record["executed_tests"] == 1


def test_failed_subtest_keeps_sibling_evidence_readable(tmp_path):
    passed, good = replay(tmp_path / "passed.json", "fixture_pass")
    failed, bad = replay(tmp_path / "failed.json", "unittest_fail")
    raw = final_report({**failed, "test_outcomes": {**passed["test_outcomes"], **failed["test_outcomes"]},
                        "subtests": {**passed["subtests"], **failed["subtests"]},
                        "collected_ids": [good, bad], "collected": 2, "started": 2, "finished": 2,
                        "passed": 1, "failed": 1, "total": 2}, "failed")
    report = rt.read_report(raw)
    assert report.valid
    assert report.body_result(good) == "passed" and report.body_result(bad) == "failed"
    assert patch_review._record(2, "derived", [good, bad], raw)["executed_outcomes"] == {good: "passed", bad: "failed"}


def test_repeated_non_subtest_phase_still_invalidates_the_whole_report(tmp_path):
    raw, node = replay(tmp_path / "counted.json", "fixture_pass")
    counted = producer(tmp_path / "repeated.json")
    counted.collected, counted.collected_ids = 1, {node}
    counted.test_outcomes = raw["test_outcomes"]
    counted.started, counted.finished = {node}, {node}
    # An unrelated context must not disguise an ordinary repeated report.
    counted.pytest_runtest_logreport(SimpleNamespace(
        nodeid=node, when="call", outcome="passed", failed=False, context=SimpleNamespace(other=True)))
    repeated = json.loads((tmp_path / "repeated.json").read_text())
    report = rt.read_report(final_report(repeated, "passed"))
    assert not report.valid and report.body_result(node) == "unknown"
    assert not rt.read_phases(repeated["test_outcomes"][node]).valid


def test_subtest_failure_messages_accumulate_within_the_existing_limit(tmp_path):
    path = tmp_path / "counted.json"
    raw, node = replay(path, "fixture_pass")
    counted = producer(path)
    counted.collected, counted.collected_ids = 1, {node}
    counted.test_outcomes = raw["test_outcomes"]
    counted.started, counted.finished = {node}, {node}
    for message in ("first failure", "second failure", "x" * 3000):
        counted.pytest_runtest_logreport(SimpleNamespace(
            nodeid=node, when="call", outcome="failed", failed=True,
            context=SimpleNamespace(msg="item"), longrepr=message))
        if message == "second failure":
            text = json.loads(path.read_text())["subtests"][node]["messages"]
            assert "first failure" in text and "second failure" in text
    raw = json.loads(path.read_text())
    assert raw["subtests"][node]["failed"] == 3
    assert len(raw["subtests"][node]["messages"]) <= 2000
    assert len(raw["messages"][node]) <= 2000
    assert "first failure" in raw["messages"][node]
    report = rt.read_report(final_report(raw, "failed"))
    assert report.valid and report.body_result(node) == "failed"


@pytest.mark.parametrize("damage", ["negative", "boolean", "missing", "orphan", "unlisted", "pass-verdict"])
def test_malformed_or_contradictory_subtests_stay_unknown(tmp_path, damage):
    raw, node = replay(tmp_path / "counted.json", "unittest_fail")
    raw = deepcopy(final_report(raw, "failed"))
    # Supplying the new field explicitly also tests old readers' fail-closed path.
    raw["subtests"] = {node: {"passed": 1, "failed": 1, "skipped": 0, "messages": "failure"}}
    if damage == "negative":
        raw["subtests"][node]["failed"] = -1
    elif damage == "boolean":
        raw["subtests"][node]["failed"] = True
    elif damage == "missing":
        del raw["subtests"][node]["failed"]
    elif damage == "orphan":
        raw["subtests"]["other.py::test_other"] = raw["subtests"].pop(node)
    elif damage == "unlisted":
        raw["failed_ids"] = []
    else:
        raw["verdict"] = "passed"
    report = rt.read_report(raw)
    assert not report.valid and report.body_result(node) == "unknown"


@pytest.mark.parametrize("case", CASES)
def test_progress_and_result_keep_subtests_and_parent_tallies(tmp_path, case):
    path = tmp_path / "counted.json"
    raw, node = replay(path, case)
    want, (passed, failed, skipped) = EXPECTED[case]
    # pytest's JUnit reports a skipped subtest as a skipped testcase even when
    # the parent call passed (the same capture as data/subtest_reports.json).
    counts = {"passed": int(not failed and not skipped), "failures": int(bool(failed)),
              "errors": 0, "skipped": int(bool(skipped))}
    progress = rt._progress(counts, path)
    assert progress["subtests"] == raw["subtests"]
    assert progress["passed"] == int(want == "passed")
    assert progress["skipped"] == 0
    verdict = rt._judge(CASES[case]["exitstatus"], progress, "", [])
    assert verdict == want
    final = final_report({**raw, "passed": progress["passed"], "failed": int(bool(failed)),
                          "not_passed": progress["skipped"], "total": 1}, verdict)
    assert rt.read_report(final).body_result(node) == want


@pytest.mark.parametrize("case", CASES)
def test_real_pytest_reports_flow_through_g6_progress(tmp_path, case):
    shape = CASES[case]
    node = shape["reports"][0]["nodeid"]
    filename = node.partition("::")[0]
    (tmp_path / filename).write_text(shape["source"], encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    path, junit = tmp_path / "counted.json", tmp_path / "junit.xml"
    counted = producer(path)
    status = pytest.main([str(tmp_path / filename), "-q", "--capture=sys", "--import-mode=importlib",
                          "-p", "no:cacheprovider", "-c", str(config), "--junitxml=" + str(junit)], plugins=[counted])
    assert int(status) == shape["exitstatus"]
    counts = rt._counts(junit, tmp_path, (tmp_path,))
    progress = rt._progress(counts, path)
    want, expected_counts = EXPECTED[case]
    verdict = rt._judge(int(status), progress, "", [])
    assert verdict == want
    assert tuple(progress["subtests"][node][key] for key in ("passed", "failed", "skipped")) == expected_counts
    report = rt.read_report(final_report(json.loads(path.read_text()), verdict))
    assert report.valid and report.body_result(report.full(node)) == want


@pytest.mark.parametrize("passes", [0, 1])
def test_subtest_skips_only_adjust_skips_junit_actually_counted(tmp_path, passes):
    path = tmp_path / "counted.json"
    replay(path, "fixture_skip")
    counts = {"passed": passes, "skipped": 0, "failures": 0, "errors": 0}
    progress = rt._progress(counts, path)
    assert progress["passed"] == passes
    assert rt._judge(0, progress, "", []) == ("passed" if passes else "inconsistent_report")
