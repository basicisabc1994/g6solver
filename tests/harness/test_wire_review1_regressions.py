# SPDX-License-Identifier: Apache-2.0
"""Reproductions for review of b2251ee6; no disk or provider access."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from xml.etree.ElementTree import Element, SubElement, tostring

from _pytest.junitxml import mangle_test_address

from g6.harness import completion_loop as loop
from g6.media._types import NO_OUTPUT
from g6.media.adapt_diagrams import block as diagrams
from g6.workforce import repo_tests as rt


class MemoryXML:
    def __init__(self, root):
        self.data = tostring(root)

    def open(self, mode):
        assert mode == "rb"
        return BytesIO(self.data)


def test_junit_collision_does_not_hide_an_own_assertion():
    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    records = {}
    cases = [
        ("a/b.py::test_dep", "ModuleNotFoundError", "wpf1_absent_dep"),
        ("a.b.py::test_dep", "ModuleNotFoundError", "wpf1_absent_dep"),
        ("test_own.py::test_bad", "AssertionError", ""),
    ]
    for node, kind, module in cases:
        parts = mangle_test_address(node)
        case = SubElement(suite, "testcase",
                          classname=".".join(parts[:-1]), name=parts[-1])
        message = (f"{kind}: No module named '{module}'"
                   if module else "assert 0")
        SubElement(case, "failure", message=message)
        records[node] = [{"when": "call", "kind": kind,
                          "module": module, "file": "test.py"}]
    repo = Path.cwd()
    prose = rt._counts(MemoryXML(root), repo, (repo,))
    typed = rt._counts(MemoryXML(root), repo, (repo,), records)
    assert rt._judge(1, prose, "", []) == rt.FAILED
    verdict = rt._judge(1, typed, "", [])
    assert verdict == rt.FAILED, (verdict, typed)


def test_a_record_for_a_passing_phase_is_not_authoritative(monkeypatch):
    node = "test_x.py::test_cross"
    ran = {
        "verdict": "failed", "said": "", "output": "", "rootdir": "",
        "test_outcomes": {
            node: {"setup": "passed", "call": "failed", "teardown": "passed"}
        },
        "collected_ids": [node], "omitted_ids": [], "failed_ids": [node],
        "setup_errors": {}, "failure_messages": {node: "ValueError: crashed"},
        "failure_records": {
            node: [{"when": "setup", "kind": "AssertionError",
                    "module": "", "file": "test_x.py"}]
        },
        "passed": 0, "failed": 1, "not_passed": 0, "total": 1,
        "isolation": {"backend": "recorded"},
    }
    seen = {}
    monkeypatch.setattr(
        loop, "scout_gate",
        lambda text, **kwargs: seen.update(kwargs) or [],
    )
    got = loop._read(SimpleNamespace(repo=Path.cwd(), sources=()), [ran])
    loop._gate(SimpleNamespace(is_file=lambda: False), got)
    assert not rt.read_report(ran).valid, (
        "record for passed setup accepted; failing call classified with "
        f"broken={seen['broken']}"
    )


def test_visible_html_question_keeps_repair_waiting():
    rounds = [{"failing": ["check"], "text": "fix this", "tree": "same"}]
    reply = "<p>May I install the missing dependency?</p>"
    repair = loop.Repairs(
        rounds, lambda: ({"check"}, "fix this"), lambda: "same",
        answered=True, asked=loop.asks(reply),
    )
    repair.search(None, None)
    repair.search(None, None)
    assert repair.waiting, repair.why


def test_malformed_dot_output_preserves_no_output_code(monkeypatch):
    path = Path("unused.svg")
    monkeypatch.setattr(diagrams, "media_file", lambda *args: path)
    monkeypatch.setattr(
        diagrams.dot, "run_dot",
        # a whole run record, as run_dot returns one (the review's stub had only outputs)
        lambda *args, **kwargs: SimpleNamespace(outputs={"svg": b"<svg"}, version="", argv=[], warnings=[],
                                                execution={}),
    )
    monkeypatch.setattr(
        diagrams, "stage_media", lambda *args, **kwargs: (path, "written"),
    )
    monkeypatch.setattr(diagrams, "discard", lambda *args: True)
    result = diagrams.AdaptDiagramsBlock().infer(
        diagrams.DiagramInput(op="render_dot", source="digraph { a }")
    ).value
    assert result["warning_card"]["code"] == NO_OUTPUT, result
