# SPDX-License-Identifier: Apache-2.0
"""One run's verdict comes from pytest's exit code and its own JUnit tallies, never from printed text (the
wire-parsers repair, stage 1), and a large suite's report is still read: a size limit on the reader may never make a
suite that passed impossible to pass."""
from __future__ import annotations

from g6.workforce import repo_tests as rt


def test_printed_text_does_not_turn_a_recorded_failure_into_nothing():
    counts = {"passed": 0, "failures": 1, "errors": 0, "skipped": 0}
    assert rt._judge(1, counts, "9 passed in 0.1s", []) == "failed"


def test_a_report_of_150000_passing_testcases_keeps_its_counts(tmp_path):
    report = tmp_path / "report.xml"
    with report.open("w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="150000">')
        for i in range(150_000):
            out.write(f'<testcase classname="tests.test_many" name="test_{i}" time="0.001"/>')
        out.write("</testsuite></testsuites>")
    found = rt._counts(report, tmp_path, (tmp_path,))
    assert found is not None
    assert found["tests"] == found["passed"] == 150_000
    assert found["failures"] == found["errors"] == 0
