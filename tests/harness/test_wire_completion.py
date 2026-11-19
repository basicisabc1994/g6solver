# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from types import SimpleNamespace

from g6.harness.completion_loop import ProbeRun, _gate, asks


def test_completion_crash_class_comes_from_exception_record(monkeypatch):
    # An assertion helper's wording does not make an assertion a crash.
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    from g6.harness import completion_loop as loop
    seen = {}
    monkeypatch.setattr(loop, "scout_gate", lambda text, **kwargs: seen.update(kwargs) or [])
    got = ProbeRun(1, {"test_cross": "failed"}, {"test_cross": "custom helper text"},
                   failure_types={"test_cross": "AssertionError"})
    _gate(Path("unused"), got)
    assert seen["broken"] == 0


def test_question_mark_in_tilde_code_is_not_a_question():
    assert not asks('~~~python\nprint("choose?")\n~~~\n')


def test_completion_types_use_the_same_identity_as_results():
    from g6.harness import completion_loop as loop
    from g6.workforce import repo_tests as rt

    node = "test_x.py::test_cross"
    ran = {"verdict": "failed", "said": "", "output": "", "rootdir": "tests",
           "test_outcomes": {node: {"setup": "passed", "call": "failed", "teardown": "passed"}},
           "collected_ids": [node], "omitted_ids": [], "failed_ids": [node], "setup_errors": {},
           "failure_messages": {node: "helper failed"}, "failure_types": {node: "AssertionError"},
           "passed": 0, "failed": 1, "not_passed": 0, "total": 1,
           "isolation": {"backend": "recorded"}}
    assert rt.read_report(ran).valid
    got = loop._read(SimpleNamespace(repo=Path("."), sources=()), [ran])
    assert set(got.failure_types) == set(got.results) == {"tests/test_x.py::test_cross"}
