# SPDX-License-Identifier: Apache-2.0
"""Runner seam checks do not execute candidate code in the test process."""
from pathlib import Path

import pytest

from g6.recursion.tasks import CheckSpec, TaskError, TaskSpec, licence_for, run_checks


def task(kind):
    checks = (CheckSpec("function", tests=("assert bump(3) == 5",)) if kind == "function" else
              CheckSpec("stdin_stdout", inputs=("3\n",), outputs=("5\n",)))
    return TaskSpec("apps:runner", "toy", "apps", licence_for("apps"), "Add two.", checks,
                    "", "introductory", "", "train", source_split="train", train_ok=True)


def seams(monkeypatch, outside=False):
    from g6.workforce import repo_tests
    from g6.workspace._files import Workspace

    repo = Path(__file__).resolve().parents[2]
    seen = {"writes": {}, "calls": []}

    class Staging:
        root = repo.parent / "outside" if outside else repo / ".build/home/workspaces/toy"

        def write(self, path, source, **kwargs):
            seen["writes"][path] = source
            assert kwargs["dry_run"] is False
            return {"written": True}

    def for_task(name):
        seen["workspace"] = name
        return Staging()

    def run(root, tests, **kwargs):
        seen["calls"].append((root, tests, kwargs))
        return {"verdict": "passed", "isolation": {"backend": "recorded"}}

    monkeypatch.setattr(Workspace, "for_task", for_task)
    monkeypatch.setattr(repo_tests, "run", run)
    return repo, seen


@pytest.mark.parametrize("kind", ["function", "stdin_stdout"])
def test_checks_use_workspace_and_confined_runner(monkeypatch, kind):
    repo, seen = seams(monkeypatch)
    result = run_checks(task(kind), "def bump(x): return x + 2", repo=repo)
    assert result["verdict"] == "passed"
    assert seen["workspace"].startswith("rsi-")
    assert seen["writes"]["solution.py"] == "def bump(x): return x + 2"
    assert "test_task.py" in seen["writes"]
    compile(seen["writes"]["test_task.py"], "<staged-checks>", "exec")
    root, tests, kwargs = seen["calls"][0]
    assert root == repo
    assert tests == [".build/home/workspaces/toy/test_task.py"]
    assert kwargs["require_isolation"] is True and kwargs["network"] is False
    assert kwargs["timeout_s"] == 30


def test_runner_refusal_propagates(monkeypatch):
    from g6.workforce import repo_tests

    repo, seen = seams(monkeypatch)

    def refuse(*args, **kwargs):
        assert kwargs["require_isolation"] is True
        raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "synthetic unavailable backend")

    monkeypatch.setattr(repo_tests, "run", refuse)
    with pytest.raises(repo_tests.RepoRefused, match="synthetic unavailable backend"):
        run_checks(task("function"), "def bump(x): return x + 2", repo=repo)
    assert seen["writes"]


def test_workspace_outside_repo_refused_before_write(monkeypatch):
    repo, seen = seams(monkeypatch, outside=True)
    with pytest.raises(TaskError, match="^G6_E_RSI_WORKSPACE_REFUSED$"):
        run_checks(task("function"), "def bump(x): return x + 2", repo=repo)
    assert not seen["writes"] and not seen["calls"]
