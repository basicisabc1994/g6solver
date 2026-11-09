"""A confirmed `raises <OSError subclass>` outcome must be observable, or never offered.

The form offers "raises FileNotFoundError" and says the answer is enforced exactly as given. The bootstrap
re-raises every OSError (behaviour_runner.BOOTSTRAP: `if is_instance(exc, (..., OSError)): raise`), so the child
writes no frame and the runner reads `error`: the confirmed outcome is never observed even when the patch does it.

Only the OS boundary is a fixture (confine.plan and repo_tests._bounded run the real isolated child with G6's own
program); the dialog, store, scenario, copy binding, runner, aggregator and issuer are the product's.
"""
import subprocess

import pytest

from g6.core import confine
from g6.harness import patch_review
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, candidate, repo, stated


@pytest.mark.parametrize("error", ["FileNotFoundError", "TimeoutError"])
def test_confirmed_oserror_outcome_is_observed_when_the_patch_does_it(tmp_path, monkeypatch, error):
    root = repo(tmp_path)   # base: add returns a - b
    (root / "calc.py").write_text(f"def add(a, b):\n    raise {error}('the patch raises it')\n", encoding="utf-8")
    got = ask(root, data=[candidate(outcomes={"A": {"raises": error}, "B": {"returns": -1}})])
    if got.get("status") != "confirmed":
        # A fix that refuses to offer an outcome the runner cannot observe also satisfies the rule.
        assert got.get("ok") is False, got
        return
    stated(monkeypatch)
    children = []
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("trusted-review-fixture", "", spec))

    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=min(timeout_s, 20))
        log.write_bytes(child.stdout + child.stderr)
        children.append((child.returncode, child.stderr[-200:]))
        return child.returncode

    monkeypatch.setattr(rt, "_bounded", execute)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert "assessment" in reply, reply
    [held] = reply["assessment"]["confirmed_expectations"]["expectations"]
    check = held["check"]
    assert check["killed"] is True, check   # positive control: the base (returns -1) was observed and failed
    assert check["result"] == "passed", (
        f"the person confirmed 'raises {error}' and the patch raises it, but G6 reports "
        f"{check['result']}: {check.get('detail')}; children (exit, stderr tail): {children}")
