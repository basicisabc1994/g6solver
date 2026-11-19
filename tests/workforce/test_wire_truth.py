# SPDX-License-Identifier: Apache-2.0
from xml.etree.ElementTree import fromstring

from g6.workforce import truth


def test_truth_requires_matching_junit_console_and_control_record():
    record = {"outcomes": {"test_x": "passed"}, "collect_errors": [], "control": "failed"}
    root = fromstring('<testsuite><testcase name="test_x"><properties>'
                      '<property name="g6.nodeid" value="test_x"/></properties></testcase><testcase name="g6-positive-control">'
                      '<failure/></testcase></testsuite>')
    assert truth._report_tally(root, record, "1 passed, 1 failed in 0.1s", control=True) == {"passed": 1}
    assert truth._report_tally(root, record, "2 passed in 0.1s", control=True) is None
    assert truth._report_tally(None, record, "1 passed in 0.1s", control=True) is None


def test_truth_cannot_use_forged_json_pass_against_xml_failure():
    record = {"outcomes": {"test_x": "passed"}, "collect_errors": []}
    root = fromstring('<testsuite><testcase name="test_x"><failure/></testcase></testsuite>')
    assert truth._report_tally(root, record, "1 failed in 0.1s") is None


def test_truth_rejects_swapped_outcomes_even_with_matching_counts():
    record = {"outcomes": {"test_a": "passed", "test_b": "failed"}, "collect_errors": []}
    root = fromstring('<testsuite><testcase name="test_a"><properties>'
                      '<property name="g6.nodeid" value="test_a"/></properties><failure/></testcase>'
                      '<testcase name="test_b"><properties><property name="g6.nodeid" value="test_b"/>'
                      '</properties></testcase></testsuite>')
    assert truth._report_tally(root, record, "1 passed, 1 failed in 0.1s") is None
