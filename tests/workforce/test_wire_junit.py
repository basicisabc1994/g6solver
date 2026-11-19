# SPDX-License-Identifier: Apache-2.0
import pytest
from pathlib import Path
from xml.etree.ElementTree import fromstring

from g6.workforce import repo_tests as rt


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_junit_doctype_is_refused(tmp_path, encoding):
    report = tmp_path / "report.xml"
    report.write_bytes(('<?xml version="1.0" encoding="' + encoding + '"?>'
        '<!DOCTYPE testsuite [<!ENTITY x "forged">]>'
        '<testsuite><testcase classname="c" name="&x;"/></testsuite>').encode(encoding))
    assert rt._counts(report, tmp_path, ()) is None


def test_junit_console_disagreement_is_insufficient_evidence():
    counts = dict(tests=1, passed=1, failures=0, errors=0, skipped=0)
    assert rt._judge(0, counts, "2 passed in 0.01s", []) == "insufficient_evidence"


def test_missing_junit_is_insufficient_evidence():
    assert rt._judge(0, None, "1 passed in 0.01s", []) == "insufficient_evidence"


def test_junit_swapped_identities_are_insufficient_evidence():
    counts = dict(tests=2, passed=1, failures=1, errors=0, skipped=0,
                  junit_outcomes={"test_a": "failed", "test_b": "passed"},
                  test_outcomes={
                      "test_a": {"setup": "passed", "call": "passed", "teardown": "passed"},
                      "test_b": {"setup": "passed", "call": "failed", "teardown": "passed"}})
    assert rt._judge(1, counts, "1 passed, 1 failed in 0.1s", []) == "insufficient_evidence"


def test_external_missing_import_is_placed_from_structured_properties():
    problem = fromstring('<failure message="arbitrary translated diagnostic"/>')
    assert rt._blame(problem, Path("."), (), collection=False, properties={
        "g6.exception": "ModuleNotFoundError", "g6.missing_module": "absent_package"}) == "outside"


def test_ambiguous_exit_three_is_not_classified_from_console_prefix():
    counts = dict(tests=1, passed=1, failures=0, errors=0, skipped=0)
    assert rt._judge(3, counts, "INTERNALERROR> claimed\n1 passed in 0.1s", []) == "insufficient_evidence"
