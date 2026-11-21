# SPDX-License-Identifier: Apache-2.0
import ast
import os
import types
from io import BytesIO
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest
from _pytest.junitxml import LogXML

from g6.workforce import repo_tests as rt


def test_partial_records_do_not_hide_strict_xpass(tmp_path):
    counted_ast = next(
        n for n in ast.parse(rt._BOOT).body
        if isinstance(n, ast.ClassDef) and n.name == "_Counted"
    )
    namespace = {"os": os}
    exec(compile(ast.Module(body=[counted_ast], type_ignores=[]),
                 "<real-counted>", "exec"), namespace)

    class Counted(namespace["_Counted"]):
        def write(self):
            pass  # Keep the real producer's observations in memory.

    class XML(LogXML):
        def pytest_sessionfinish(self, session):
            pass  # Keep pytest's real XML tree in memory.

    counted = Counted()
    writer = XML("unused.xml", prefix=None, family="xunit2")

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason="known defect")
    def test_xpass():
        pass

    class Session:
        @pytest.hookimpl(tryfirst=True)
        def pytest_collection(self, session):
            module = pytest.Module.from_parent(
                session, path=Path.cwd() / "tests/harness/_strict_xpass_virtual.py"   # never written
            )
            module.obj = types.ModuleType("strict_xpass")
            item = pytest.Function.from_parent(
                module, name="test_xpass", callobj=test_xpass
            )
            session.items = [item]
            session.testscollected = 1
            counted.pytest_itemcollected(item)
            session.config.hook.pytest_collection_finish(session=session)
            return True

        def pytest_runtest_teardown(self, item):
            raise ModuleNotFoundError(
                "No module named 'g6_absent_review_dependency'",
                name="g6_absent_review_dependency",
            )

    code = pytest.main(
        ["--noconftest", "--capture=sys", "-p", "no:cacheprovider", "-q",
         "--basetemp", str(tmp_path / "inner")],
        plugins=[Session(), counted, writer],
    )
    assert code == 1
    [records] = counted.records.values()
    assert [r["when"] for r in records] == ["teardown"]
    [phases] = counted.test_outcomes.values()
    assert phases == {"setup": "passed", "call": "failed", "teardown": "failed"}

    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    for reporter in writer.node_reporters_ordered:
        suite.append(reporter.to_xml())

    class MemoryXML:
        def open(self, mode):
            assert mode == "rb"
            return BytesIO(tostring(root))

    repo = Path.cwd()
    prose = rt._counts(MemoryXML(), repo, (repo,))
    assert rt._judge(code, prose, "", []) == rt.FAILED
    typed = rt._counts(MemoryXML(), repo, (repo,), counted.records)
    observed = rt._judge(code, typed, "", [])
    assert observed == rt.FAILED, (observed, typed)
