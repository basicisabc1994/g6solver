# SPDX-License-Identifier: Apache-2.0
"""pytest's JUnit report is read by a reader that refuses a document type (so no entity is ever expanded) and holds
the report to JUnit-sized limits: far above a large suite's report, so a size limit never makes a suite that passed
impossible to pass, and still a bound, past which the tallies are unread (never a pass)."""
from __future__ import annotations

from g6.workforce import repo_tests as rt

HEAD = '<?xml version="1.0" encoding="utf-8"?>'


def _write(tmp_path, body: str, *, head: str = HEAD):
    report = tmp_path / "report.xml"
    report.write_text(head + body, encoding="utf-8")
    return report


def test_a_report_with_a_document_type_is_unread(tmp_path):
    report = _write(tmp_path, '<!DOCTYPE testsuites [<!ENTITY x "x">]><testsuites><testsuite name="pytest">'
                              '<testcase classname="t" name="test_a"/></testsuite></testsuites>')
    assert rt._counts(report, tmp_path, (tmp_path,)) is None


def test_a_failure_with_nine_mebibytes_of_text_is_read(tmp_path):
    text = "x" * (9 * 1024 * 1024)
    report = _write(tmp_path, f'<testsuites><testsuite name="pytest"><testcase classname="t" name="test_a">'
                              f'<failure message="assert 0">{text}</failure></testcase></testsuite></testsuites>')
    found = rt._counts(report, tmp_path, (tmp_path,))
    assert found is not None and found["failures"] == 1 and found["tests"] == 1


def test_a_report_past_the_byte_limit_is_unread(tmp_path, monkeypatch):
    report = _write(tmp_path, '<testsuites><testsuite name="pytest"><testcase classname="t" name="test_a"/>'
                              '</testsuite></testsuites>')
    monkeypatch.setattr(rt, "JUNIT_BYTES", 40)
    assert rt._counts(report, tmp_path, (tmp_path,)) is None


def test_a_report_past_the_element_limit_is_unread(tmp_path, monkeypatch):
    cases = "".join(f'<testcase classname="t" name="test_{i}"/>' for i in range(10))
    report = _write(tmp_path, f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>')
    monkeypatch.setattr(rt, "JUNIT_ELEMENTS", 5)
    assert rt._counts(report, tmp_path, (tmp_path,)) is None


def test_the_limits_are_junit_sized():
    assert rt.JUNIT_BYTES >= 256 * 1024 * 1024
    assert rt.JUNIT_ELEMENTS >= 2_000_000
