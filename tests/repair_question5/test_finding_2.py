"""A confirmed case on a function in a module the patch adds must not read as a changed working tree.

At the base commit the module does not exist, so the owned runner's `located` raises ValueError. Since this change the
base control passes the artefact binding (confirmed_checks.read: `**binding` for both runs), and `_run_check` maps
every OSError/ValueError of a bound run to the NOT_THE_ARTEFACT refusal, which `confirmed_checks` records as
`tree_changed` and `patch_review` turns into the integrity reason `repository_changed_during_review`.

Case A: the patch does what the person confirmed. Case B: the patch observably fails the confirmed case, and the
integrity outcome replaces defect_detected (claim 6's property, through the real `_run_check`).

Only the OS boundary is a fixture; the dialog, store, scenario, copy binding, runner and review are the product's.
"""
import subprocess

import pytest

from g6.core import confine
from g6.harness import patch_review
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, Dialog, ask, candidate, repo, stated


@pytest.mark.parametrize("choice,expected", [("A", "passed"), ("B", "failed")])
def test_new_module_case_is_not_reported_as_a_changed_working_tree(tmp_path, monkeypatch, choice, expected):
    root = repo(tmp_path)
    (root / "newcalc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")   # added by the patch
    got = ask(root, dialog=Dialog({"choice": choice, "name": "Maintainer"}),
              data=[candidate(function="newcalc.add", evidence=["newcalc.py: add"])])
    assert got["status"] == "confirmed", got   # A: returns 5; B: returns -1, for newcalc.add(2, 3)
    stated(monkeypatch)
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("trusted-review-fixture", "", spec))

    def execute(cmd, *, cwd, env, timeout_s, log, **kwargs):
        child = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=min(timeout_s, 20))
        log.write_bytes(child.stdout + child.stderr)
        return child.returncode

    monkeypatch.setattr(rt, "_bounded", execute)
    before = rt.snapshot(root)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    assert rt.snapshot(root) == before   # nothing changed the working tree
    assessment = reply["assessment"]
    [held] = assessment["confirmed_expectations"]["expectations"]
    assert held["check"]["result"] == expected, held["check"]   # the patch run itself observed the case
    integrity = [r for r in assessment.get("reasons", []) if r.get("stage") == "integrity"]
    assert not integrity and assessment["outcome"] != patch_review.INTEGRITY, (
        f"outcome {assessment['outcome']}; integrity reasons {integrity}; "
        f"check.tree_changed={held['check'].get('tree_changed')!r}")
    if expected == "failed":
        assert assessment["outcome"] == patch_review.DEFECT
