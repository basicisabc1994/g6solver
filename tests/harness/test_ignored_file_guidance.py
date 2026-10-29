# SPDX-License-Identifier: Apache-2.0
"""What a person does about ``ignored_file_can_change_tests`` (worked examples, W3b).

In the worked examples' copies of Black, Black's own data fixtures were untracked and git-ignored, and named like
modules the repository tracks (``a.py``), so G6 counted them as files that could replace a module during the tests and
gave test integrity compromised, with no word on what to do. The outcome and every decided field stay as they were;
the next actions now name each such file once (the file, never also its folder), say why G6 cannot rule it out, and
say what settles it, and the report says it too. Development fixtures only; the test runs are recorded, not run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_review
from g6.harness.patch_review import review_patch
from g6.workforce import repo_tests

GOOD = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
ADD = "test_calc.py::test_add"
SUM = [{"id": "R1", "text": "add returns the sum", "checks": [ADD]}]
#: What settles it (the security review's F4): never "git add -f", which only stages the file into the patch.
SETTLE = ("If they belong to the project, commit them before the change under review; otherwise remove them or move "
          "them out of the repository; then review again.")
NAMED = ("untracked and git-ignored, but named like modules in the repository, so G6 cannot rule out that one "
         "replaces a module during the tests. " + SETTLE)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text(TEST)
    fixtures = tmp_path / "tests" / "data" / "gitignore_case"
    for folder in ("dir1", "dir2"):
        (fixtures / folder).mkdir(parents=True)
        (fixtures / folder / "keep.py").write_text("print('formatted')\n")
    (fixtures / ".gitignore").write_text("calc.py\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    (tmp_path / "calc.py").write_text(GOOD)
    return tmp_path


def _recorded(monkeypatch) -> None:
    passed = {"verdict": "passed", "said": "", "passed": 1, "failed": 0, "not_passed": 0, "total": 1,
              "deselected": 0, "test_outcomes": {ADD: {"setup": "passed", "call": "passed", "teardown": "passed"}},
              "collected_ids": [ADD], "omitted_ids": [], "changed": [], "output": "", "seconds": 0.25,
              "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
              "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": [], "setup_errors": {},
              "degraded": False, "receipt": ""}
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **kw: passed)
    monkeypatch.setattr(patch_review, "_execute_at_base",
                        lambda change, ids, *, timeout_s, **kw: {"verdict": "not_run", "said": "not recorded"})


def _review(repo):
    return review_patch(repo, task="Repair addition.", requirements=SUM)


def _asked(got) -> list[str]:
    return [action for action in got["assessment"]["next_actions"] if "untracked and git-ignored" in action]


def test_ignored_fixtures_named_like_modules_are_named_once_with_what_settles_it(repo, monkeypatch):
    for folder in ("dir1", "dir2"):
        (repo / "tests" / "data" / "gitignore_case" / folder / "calc.py").write_text("print('fixture')\n")
    _recorded(monkeypatch)
    got = _review(repo)
    assessment = got["assessment"]
    assert assessment["outcome"] == "test_integrity_compromised"                      # unchanged: it errs to an alarm
    assert [(r["stage"], r["code"]) for r in assessment["reasons"]] == [("integrity", "ignored_file_can_change_tests")]
    asked, = _asked(got)
    files = ["tests/data/gitignore_case/dir1/calc.py", "tests/data/gitignore_case/dir2/calc.py"]
    assert asked == f"{', '.join(f'`{rel}`' for rel in files)}: {NAMED}"   # each in a code span (F5)
    report = assessment["report"]
    assert "What to do:" in report and "commit them before the change under review" in report
    assert "`assessment.next_actions`" in report and "git add -f" not in report and "git add -f" not in asked
    assert len(report.splitlines()) <= 40


def test_ignored_inputs_says_which_files_it_counted_for_their_module_names(repo):
    (repo / "tests" / "data" / "gitignore_case" / "dir1" / "calc.py").write_text("print('fixture')\n")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("conftest.py\n")
    (repo / "conftest.py").write_text("")
    found = repo_tests.ignored_inputs(repo)
    assert found.loadable == ("conftest.py", "tests/data/gitignore_case/dir1/calc.py")
    assert found.named_like_modules == ("tests/data/gitignore_case/dir1/calc.py",)


def test_each_file_is_named_once_and_never_also_its_folder(repo, monkeypatch):
    # git can list a folder and a file in it both (Black's ignore_subfolders_gitignore_tests): the file is shown, once
    listed = ("a/b", "a/b/c.py", "a/b/c.py", "x/calc.py")
    monkeypatch.setattr(repo_tests, "ignored_inputs", lambda root: repo_tests.Ignored(
        listed, {}, True, ("a/b/c.py", "a/b/c.py", "x/calc.py")))
    _recorded(monkeypatch)
    got = _review(repo)
    asked, = _asked(got)
    assert asked == f"`a/b/c.py`, `x/calc.py`: {NAMED}"
    reason, = got["assessment"]["reasons"]
    assert reason["detail"] == "git ignores files that could change the tests: a/b, a/b/c.py, a/b/c.py, x/calc.py"


def test_an_ignored_conftest_is_not_said_to_be_named_like_a_module(repo, monkeypatch):
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("conftest.py\n")
    (repo / "conftest.py").write_text("# an empty conftest\n")
    _recorded(monkeypatch)
    got = _review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    asked, = _asked(got)
    assert asked.startswith("`conftest.py`: untracked and git-ignored, but pytest or Python could load them")
    assert "named like modules" not in asked and asked.endswith(SETTLE) and "git add -f" not in asked


def test_many_files_name_the_first_ten_and_say_how_to_list_the_rest(repo, monkeypatch):
    listed = tuple(f"data/case{i:02}/calc.py" for i in range(12))
    monkeypatch.setattr(repo_tests, "ignored_inputs", lambda root: repo_tests.Ignored(listed, {}, True, listed))
    _recorded(monkeypatch)
    asked, = _asked(_review(repo))
    assert asked.startswith(", ".join(f"`{rel}`" for rel in listed[:10]) + " and 2 more (git status --ignored lists them all): ")
