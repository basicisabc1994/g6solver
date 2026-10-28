# SPDX-License-Identifier: Apache-2.0
"""V10's reruns of every finding's reproduction (PROTOCOL.md on the verification search branch, "Reruns").

Development fixtures only: tiny trees made here, recorded runs, and one end-to-end run of V9's executor on a fixture.
Nothing here calls a model or imports G6's code into the rerun script."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from patch_review_search import reruns
from patch_review_utility import executor

FILE = "tests/g6_check_0123456789ab/test_sum.py"
REPRO = {"file": FILE, "text": "from calc import add\n\n\ndef test_sum():\n    assert add(2, 3) == 5\n",
         "node": FILE + "::test_sum"}


class Recorded:
    """One recorded result per run: ``"passed"``, ``"failed"`` (in the body), ``"setup"`` or ``"timeout"``."""

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def run(self, tree, tests, *, timeout_s, overlay=None):
        self.calls.append((Path(tree).name, tuple(tests), timeout_s, dict(overlay["files"])))
        found = self.results.pop(0)
        if found == "timeout":
            return {"outcomes": {}, "phases": {}, "collection_errors": [], "exit": None, "timed_out": True,
                    "error": "", "seconds": 1.0}
        phases = ({"setup": "failed", "teardown": "passed"} if found == "setup" else
                  {"setup": "passed", "call": found, "teardown": "passed"})
        return {"outcomes": {}, "phases": {REPRO["node"]: phases}, "collection_errors": [], "exit": 0,
                "timed_out": False, "error": "", "seconds": 1.0}


@pytest.mark.parametrize("bad", [{**REPRO, "file": "/abs/test_sum.py"}, {**REPRO, "file": "../test_sum.py"},
                                 {**REPRO, "node": "elsewhere.py::test_sum"}, {k: v for k, v in REPRO.items()
                                                                              if k != "text"},
                                 {**REPRO, "file": "tests\\test_sum.py"}])
def test_a_finding_that_cannot_be_run_that_way_is_not_reproducible_and_is_not_run(bad, tmp_path):
    runner = Recorded()
    found = reruns.before_labels(runner, tmp_path / "patched", tmp_path / "base", bad, timeout_s=60)
    assert found["runnable"] is False and found["reproducible"] is False and found["why"] and runner.calls == []


def test_a_finding_is_reproducible_when_it_fails_in_two_of_three_runs_on_the_patched_tree(tmp_path):
    runner = Recorded("failed", "passed", "failed", "passed", "passed", "setup")
    found = reruns.before_labels(runner, tmp_path / "patched", tmp_path / "base", REPRO, timeout_s=60)
    assert found["patched"] == ["fail", "pass", "fail"] and found["base"] == ["pass", "pass", "error"]
    assert found["reproducible"] is True and found["runnable"] is True
    assert [call[0] for call in runner.calls] == ["patched"] * 3 + ["base"] * 3
    assert all(call[2] == 60 and call[3] == {FILE: REPRO["text"]} for call in runner.calls)
    once = Recorded("failed", "timeout", "setup", "failed", "failed", "failed")
    assert reruns.before_labels(once, tmp_path / "p", tmp_path / "b", REPRO, timeout_s=60)["reproducible"] is False


@pytest.mark.parametrize("runs, compatible, comparable", [
    (["passed", "passed", "failed"], True, True), (["failed", "failed", "passed"], False, True),
    (["setup", "timeout", "passed"], False, False)])
def test_on_the_reference_fix_a_finding_is_compatible_or_not_comparable(tmp_path, runs, compatible, comparable):
    found = reruns.on_reference_fix(Recorded(*runs), tmp_path / "reference", REPRO, timeout_s=60)
    assert found["compatible"] is compatible and found["not_comparable"] is (not comparable)


def test_end_to_end_with_v9s_executor_on_a_fixture(tmp_path):
    for side, code in (("patched", "def add(a, b):\n    return a + b\n"), ("base", "def add(a, b):\n    return a - b\n")):
        (tmp_path / side).mkdir()
        (tmp_path / side / "calc.py").write_text(code, encoding="utf-8", newline="\n")
    wrong = {**REPRO, "text": REPRO["text"].replace("== 5", "== 4")}
    found = reruns.before_labels(executor.Executor(), tmp_path / "patched", tmp_path / "base", wrong, timeout_s=60)
    assert found["patched"] == ["fail"] * 3 and found["reproducible"] is True and found["base"] == ["fail"] * 3
    assert not (tmp_path / "patched" / "tests").exists()


def test_the_rerun_script_does_not_import_g6():
    validation = Path(reruns.__file__).resolve().parents[1]
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import patch_review_search.reruns; "
            "print(any(name == 'g6' or name.startswith('g6.') for name in sys.modules))")
    done = subprocess.run([sys.executable, "-I", "-c", code, str(validation)], capture_output=True, text=True,
                          check=True)
    assert done.stdout.strip() == "False"
