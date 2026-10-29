# SPDX-License-Identifier: Apache-2.0
"""pdm-backend's editable build output is not an input the tests load (worked example run 9, fastapi #15077: F8).

pdm-backend's editable install writes ``.pdm-build/`` (a ``<package>.pth`` and a ``.gitignore`` of ``*``) and
git-ignores it. G6 gave test integrity compromised with ``ignored_file_can_change_tests`` for ``.pdm-build/fastapi.pth``,
on the maintainers' own fix too. It is build output, as ``build/`` and ``dist/`` are, which the ignored-file scan does
not enter, and Python reads ``.pth`` files only from its site folders. The general ``.pth`` rule is unchanged: an
ignored ``.pth`` elsewhere still puts integrity in doubt. Benign files only; the test runs are recorded, not run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_review
from g6.harness.patch_review import review_patch
from g6.workforce import repo_tests

ADD = "test_calc.py::test_add"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


def _pdm_build(repo: Path) -> None:
    """What pdm-backend's editable install leaves: the folder, its own .gitignore of everything, and the .pth."""
    (repo / ".pdm-build").mkdir()
    (repo / ".pdm-build" / ".gitignore").write_text("*\n")
    (repo / ".pdm-build" / "calc.pth").write_text("/nonexistent/editable/source\n")


def _review(repo, monkeypatch):
    passed = {"verdict": "passed", "said": "", "passed": 1, "failed": 0, "not_passed": 0, "total": 1,
              "deselected": 0, "test_outcomes": {ADD: {"setup": "passed", "call": "passed", "teardown": "passed"}},
              "collected_ids": [ADD], "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
              "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
              "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": [], "setup_errors": {},
              "degraded": False, "receipt": ""}
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **kw: passed)
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **kw: {"verdict": "not_run", "said": "not recorded"})
    return review_patch(repo, task="Repair addition.",
                        requirements=[{"id": "R1", "text": "add returns the sum", "checks": [ADD]}])


def test_pdm_backends_editable_build_output_raises_nothing(repo, monkeypatch):
    _pdm_build(repo)
    assert repo_tests.ignored_inputs(repo).loadable == ()
    got = _review(repo, monkeypatch)
    assert got["assessment"]["reasons"] == [] and got["assessment"]["outcome"] == "supported_within_scope"


@pytest.mark.parametrize("rel", ["zz.pth", "conftest.py"])
def test_an_ignored_pth_or_conftest_elsewhere_still_puts_integrity_in_doubt(repo, monkeypatch, rel):
    _pdm_build(repo)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(rel + "\n")
    (repo / rel).write_text("# benign\n")
    got = _review(repo, monkeypatch)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert [r["code"] for r in got["assessment"]["reasons"]] == ["ignored_file_can_change_tests"]
    assert rel in got["assessment"]["reasons"][0]["detail"]
