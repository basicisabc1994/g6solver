"""The bundle must not count an original-test replay of outside-loaded code."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.bundles import agent_change
from g6.workforce import repo_tests as runner
from tests.harness.efu2_cases import FAILED, PASSED, report


@pytest.mark.parametrize("phases", [PASSED, FAILED], ids=["pass", "failure"])
def test_bundle_does_not_count_an_outside_loaded_replay(monkeypatch, phases):
    change = SimpleNamespace(files=("test_calc.py", "calc.py"),
                             before={"test_calc.py": "def test_add(): pass\n"},
                             after={"test_calc.py": "def test_add(): pass\n"},
                             renamed={}, runner_files=())
    first = report(PASSED, passed=1, failed=0, not_passed=0, total=1,
                   loaded_complete=True, loaded_outside={})
    replay = report(phases, loaded_complete=True,
                    loaded_outside={"calc": "elsewhere/calc.py"}, python_sources=["calc.py"])
    assert runner.loaded_from(Path.cwd(), replay)
    monkeypatch.setattr(runner, "checked_repo", lambda *a: Path.cwd())
    monkeypatch.setattr(runner, "change", lambda *a: change)
    monkeypatch.setattr(runner, "diff_text", lambda *a: "")
    monkeypatch.setattr(runner, "run", lambda *a, **kw: first)
    monkeypatch.setattr(runner, "run_before", lambda *a, **kw: {"test_calc.py": replay})
    monkeypatch.setattr(agent_change, "_mutations", lambda *a: (
        {"check": "mutations", "result": "not run", "detail": "", "label": "qualified-draft"}, []))
    out = agent_change.run({"repo": "recorded", "task": "retain add", "answer": "changed implementation",
                            "tests": "test_calc.py"})
    row = next(row for row in out.rows if row["check"] == "Original tests on the new code")
    assert row["result"] == "not run", row
