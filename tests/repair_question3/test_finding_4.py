"""Repository pytest hooks must not invent observations of the person's case.

The executor boundary is a fixture: only this test's authored code runs, in short-lived
child interpreters. G6's bootstrap, pytest producer, readers, aggregator and issuer
are real. No confinement setting is changed and no model or network is used.
"""
import subprocess
import sys

from g6.core import confine
from g6.harness import patch_review
from g6.mcp import behaviour
from g6.workforce import repo_tests as rt
from tests.repair_question2.helpers import REQUIREMENTS, TASK, ask, repo, stated


HOOK = '''from pathlib import Path

def pytest_pyfunc_call(pyfuncitem):
    if pyfuncitem.name == "test_example" and "PATCH_MARKER" in Path(__file__).with_name("calc.py").read_text():
        # firstresult hook: reporting True prevents pytest from calling test_example.
        return True
'''


def test_repository_hook_cannot_supply_confirmed_support_without_running_the_case(tmp_path, monkeypatch):
    root = repo(tmp_path, extra={"conftest.py": HOOK})
    # Both trees compute subtraction; the patch merely adds a marker the unchanged hook reads.
    (root / "calc.py").write_text("# PATCH_MARKER\ndef add(a, b):\n    return a - b\n", encoding="utf-8")
    assert ask(root)["status"] == "confirmed"  # the person selected returns 5 for add(2, 3)
    actual = {}
    exec((root / "calc.py").read_text(encoding="utf-8"), actual)
    assert actual["add"](2, 3) == -1
    stated(monkeypatch)
    runs = []

    # Fake only the OS boundary and interpreter discovery. The real _run_check,
    # copy binding, command construction, pytest bootstrap and all readers run.
    monkeypatch.setattr(rt, "interpreter", lambda root: sys.executable)
    monkeypatch.setattr(rt, "_environment_of", lambda python: ())
    monkeypatch.setattr(confine, "interpreter_paths", lambda python: ())
    monkeypatch.setattr(confine, "plan", lambda spec, python: confine.Plan("trusted-review-fixture", "", spec))

    def execute_fixture(cmd, *, cwd, env, timeout_s, log, **kwargs):
        # Execute only the fixture tree authored above, with the product's command.
        at_base = "PATCH_MARKER" not in (cwd / "calc.py").read_text(encoding="utf-8")
        child = subprocess.run(cmd, cwd=cwd, env={**env, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                               capture_output=True, timeout=min(timeout_s, 20))
        log.write_bytes(child.stdout + child.stderr)
        runs.append((at_base, child.returncode))
        return child.returncode

    monkeypatch.setattr(rt, "_bounded", execute_fixture)
    reply = behaviour.reviewed(patch_review.review_patch, str(root), TASK, requirements=REQUIREMENTS)
    if "assessment" not in reply:
        assert reply.get("ok") is False
        return
    held = reply["assessment"]["confirmed_expectations"]
    assert not held["evidence"]["expectations"][0]["checked"], (
        "the issuer supported returns 5 although the repository hook prevented the confirmed check from running; "
        f"child runs (at_base, exit_code): {runs}")
