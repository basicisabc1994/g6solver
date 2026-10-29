# SPDX-License-Identifier: Apache-2.0
"""The launch's security fix pass, part A: patch review's execution and integrity layer (the fresh reviewer's findings
on b5c5705, with the owner's rulings). Development fixtures only. Tests that take ``bwrap_backend`` run real confined
runs (Linux bubblewrap; in Docker on Windows); the rest record the runs."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from g6.harness import patch_review
from g6.harness.patch_review import review_patch

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


def _repo(root: Path, files: dict[str, str]) -> Path:
    """A repository whose base commit holds ``files``."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def result(passed=(), failed=(), *, verdict=None, said="", output=""):
    """One recorded ``repo_tests.run`` result: these node ids passed or failed."""
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": said, "passed": len(passed), "failed": len(failed), "not_passed": 0,
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": output, "seconds": 0.25,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": "synthetic-unit-fixture", "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": ""}


def _record(monkeypatch, run, *, at_base=None):
    """Every in-place run gives ``run(ids)``; the base-commit runs give ``at_base`` (or a run that did not happen)."""
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **_: run(list(ids))
                        if change is None else {rel: result() for rel in ids})
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda change, ids, *, timeout_s, **_: at_base or {
        "verdict": "not_run", "said": "no run was recorded"})


def _codes(got) -> list[tuple[str, str]]:
    return [(r["stage"], r["code"]) for r in got["assessment"]["reasons"]]


# --- C1: a weakened test outside pytest's default names --------------------------------------------------------------

def _pinned() -> tuple[str, str]:
    """A test file's text at the base commit, and the same file with its check pinned to the wrong answer."""
    honest = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    return honest, honest.replace("== 5", "== -1")


CONFIGS = {   # the repository's own python_files, in each file pytest reads it from
    "pytest.ini": ("pytest.ini", "[pytest]\npython_files = check_*.py\n"),
    ".pytest.ini": (".pytest.ini", "[pytest]\npython_files = test_*.py check_*.py\n"),
    "pytest.toml": ("pytest.toml", "[pytest]\npython_files = [\"check_*.py\"]\n"),
    ".pytest.toml": (".pytest.toml", "[pytest]\npython_files = [\"check_*.py\"]\n"),
    "pyproject ini_options": ("pyproject.toml", "[tool.pytest.ini_options]\npython_files = [\"check_*.py\"]\n"),
    "pyproject ini_options text": ("pyproject.toml", "[tool.pytest.ini_options]\npython_files = \"check_*.py\"\n"),
    "pyproject tool.pytest": ("pyproject.toml", "[tool.pytest]\npython_files = [\"check_*.py\"]\n"),
    "tox.ini": ("tox.ini", "[pytest]\npython_files = check_*.py\n"),
    "setup.cfg": ("setup.cfg", "[tool:pytest]\npython_files = check_*.py\n"),
}


@pytest.mark.parametrize(("test_file", "config"), [
    ("app/tests.py", None),            # Django's default name
    ("testcalc.py", None),             # unittest's default pattern, test*.py
    ("app/calc_tests.py", None),       # no default name: a stated check names it
    *[("check_calc.py", name) for name in CONFIGS],   # the repository's own python_files
])
def test_a_weakened_test_outside_pytests_default_names_compromises_integrity(tmp_path, monkeypatch, test_file,
                                                                             config):
    honest, pinned = _pinned()
    base = {"calc.py": GOOD, "test_main.py": MAIN, test_file: honest, **(dict([CONFIGS[config]]) if config else {})}
    root = _repo(tmp_path, base)
    _write(root, {"calc.py": BAD, test_file: pinned})
    # only the oddly named file is found by the stated check; the others by their names or the repository's settings
    node = f"{test_file}::test_add" if test_file == "app/calc_tests.py" else "test_main.py::test_ok"
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Change add.", requirements=[{"id": "R1", "text": "add adds", "checks": [node]}])
    assessment = got["assessment"]
    assert assessment["outcome"] == "test_integrity_compromised", assessment["reasons"]
    assert ("integrity", "test_weakened") in _codes(got)
    assert any(test_file in r["detail"] for r in assessment["reasons"] if r["code"] == "test_weakened")


#: A stated check in a file by pytest's default name, which the patches below leave unchanged.
MAIN = "def test_ok():\n    assert True\n"


@pytest.mark.parametrize(("test_file", "changed"), [
    ("app/tests.py", False),          # unchanged: nothing to compare
    ("check_calc.py", True),          # changed, but nothing makes it a test: no python_files, no check names it
])
def test_the_correct_cases_stay_as_they_were(tmp_path, monkeypatch, test_file, changed):
    honest, pinned = _pinned()
    root = _repo(tmp_path, {"calc.py": BAD, "test_main.py": MAIN, test_file: honest})
    _write(root, {"calc.py": GOOD, **({test_file: pinned} if changed else {})})
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Repair add.", requirements=[
        {"id": "R1", "text": "add adds", "checks": ["test_main.py::test_ok"]}])
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]

