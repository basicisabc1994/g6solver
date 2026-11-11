"""A patch that returns a large value where the person confirmed a small one must be a failure, not "not run".

The bootstrap frames every returned value in full and refuses a payload over 1 MiB
(behaviour_runner.BOOTSTRAP: `if len(payload) > 1048576: raise RuntimeError("observation too large")`), so the child
writes no frame and `finish` reads `error`. The confirmed outcome (returns 5) plainly did not happen, yet the review
reads the case as not run instead of reproducing the defect.

Only the OS boundary is a fixture; the dialog, store, scenario, copy binding, runner, aggregator and review are the
product's.
"""
import subprocess

import pytest

from g6.core import confine
from g6.harness import patch_review
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, repo, stated


@pytest.mark.parametrize("size", [1000, 2_000_000])
def test_a_large_wrong_return_is_observed_as_a_failure(tmp_path, monkeypatch, size):
    root = repo(tmp_path)
    (root / "calc.py").write_text(f"def add(a, b):\n    return 'x' * {size}\n", encoding="utf-8")
    assert ask(root)["status"] == "confirmed"   # the person chose returns 5 for add(2, 3)
    stated(monkeypatch)
    children = []
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("trusted-review-fixture", "", spec))

    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=min(timeout_s, 30))
        log.write_bytes(child.stdout + child.stderr)
        children.append((child.returncode, child.stderr[-120:]))
        return child.returncode

    monkeypatch.setattr(rt, "_bounded", execute)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    [held] = reply["assessment"]["confirmed_expectations"]["expectations"]
    check = held["check"]
    assert check["result"] == "failed", (
        f"patch returns a {size}-character string where the person confirmed 5; G6 reports {check['result']}: "
        f"{check.get('detail')}; outcome {reply['assessment']['outcome']}; children (exit, stderr tail): {children}")
    assert reply["assessment"]["outcome"] == patch_review.DEFECT
