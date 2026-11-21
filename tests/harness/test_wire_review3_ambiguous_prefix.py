# SPDX-License-Identifier: Apache-2.0
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

from _pytest.junitxml import LogXML
from _pytest.reports import TestReport as PytestReport

from g6.workforce import repo_tests as rt


def test_ambiguous_prefix_never_rebinds_records_to_another_test():
    repo = Path.cwd()
    writer = LogXML("unused.xml", prefix="ci", family="xunit2")
    records = {}
    cases = [
        ("test_a.py::test_same", "AssertionError", ""),
        ("ci/test_a.py::test_same", "ModuleNotFoundError",
         "g6_absent_review_dependency"),
    ]
    for node, kind, module in cases:
        message = (f"ModuleNotFoundError: No module named '{module}'"
                   if module else "AssertionError: wrong result")
        location = (node.split("::")[0], 0, "test_same")
        writer.pytest_runtest_logreport(PytestReport(
            node, location, {}, "failed", message, "call"
        ))
        writer.pytest_runtest_logreport(PytestReport(
            node, location, {}, "passed", None, "teardown"
        ))
        records[node] = [
            {"when": "call", "kind": kind, "module": module, "file": ""}
        ]

    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    for reporter in writer.node_reporters_ordered:
        suite.append(reporter.to_xml())

    class MemoryXML:
        def open(self, mode):
            assert mode == "rb"
            return BytesIO(tostring(root))

    prose = rt._counts(MemoryXML(), repo, (repo,))
    assert rt._judge(1, prose, "", []) == rt.FAILED
    typed = rt._counts(MemoryXML(), repo, (repo,), records)
    observed = rt._judge(1, typed, "", [])
    assert observed == rt.FAILED, (observed, typed)
