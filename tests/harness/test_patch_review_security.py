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


@pytest.fixture
def started(monkeypatch):
    """Every test process a run would start (``repo_tests._bounded``), recorded and never started."""
    from g6.workforce import repo_tests

    calls: list[list[str]] = []

    def bounded(cmd, **_):
        calls.append(list(cmd))
        return None

    monkeypatch.setattr(repo_tests, "_bounded", bounded)
    return calls


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


# --- I1: patch review never runs a patch without the boundary, whatever the person allowed ---------------------------

TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
SUM = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": ["test_calc.py::test_add"]},
       {"id": "R2", "text": "add returns the sum of two negative integers", "checks": ["test_calc.py::test_negative"]}]


def test_with_unconfined_runs_allowed_and_no_boundary_the_review_starts_nothing(tmp_path, monkeypatch, unconfined,
                                                                                 started):
    from g6.core import confine
    from g6.core.errors import ISOLATION_UNAVAILABLE

    monkeypatch.delenv(confine.REQUIRE, raising=False)
    root = _repo(tmp_path, {"calc.py": BAD, "test_calc.py": TEST})
    _write(root, {"calc.py": GOOD})
    got = review_patch(root, task="Repair add.", requirements=SUM)
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == ISOLATION_UNAVAILABLE and "assessment" not in got
    said = got["warning_card"]["message"]
    assert "does not change this" in said and "g6 doctor" in said and "bubblewrap" in said
    assert started == []   # no test process was started, not only no receipt written


@pytest.mark.parametrize("which", ["in place", "replayed at the base commit", "against the base commit's code"])
def test_each_run_patch_review_makes_is_refused_before_it_starts_without_the_boundary(tmp_path, monkeypatch,
                                                                                      unconfined, started, which):
    from g6.core import confine
    from g6.core.errors import ISOLATION_UNAVAILABLE
    from g6.workforce import repo_tests

    monkeypatch.delenv(confine.REQUIRE, raising=False)
    root = _repo(tmp_path, {"calc.py": BAD, "test_calc.py": TEST})
    _write(root, {"calc.py": GOOD, "test_calc.py": TEST + "\n"})
    change = repo_tests.change(root)
    with pytest.raises(repo_tests.RepoRefused) as refused:
        if which == "in place":
            patch_review._execute(root, ["test_calc.py"], timeout_s=30)
        elif which == "replayed at the base commit":
            patch_review._execute(root, ["test_calc.py"], timeout_s=30, change=change)
        else:
            patch_review._execute_at_base(change, ["test_calc.py::test_add"], timeout_s=30)
    assert refused.value.code == ISOLATION_UNAVAILABLE
    assert started == []

# --- I2: a file the patch adds or changes, visible to git, that could change what the tests load ----------------------

SRC = {"src/calc.py": GOOD, "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
CHECK = [{"id": "R1", "text": "add adds", "checks": ["tests/test_calc.py::test_add"]}]


def _extension(stem: str) -> str:
    import importlib.machinery

    return stem + importlib.machinery.EXTENSION_SUFFIXES[-1]   # .so on Linux, .pyd on Windows


@pytest.mark.parametrize(("added", "what"), [
    ({"src/x-1.0.dist-info/entry_points.txt": "[pytest11]\nx = x_plugin\n", "src/x_plugin.py": "\n"}, "plugin"),
    ({"src/x-1.0.egg-info/entry_points.txt": "[pytest11]\nx = x_plugin\n", "src/x_plugin.py": "\n"}, "plugin"),
    ({_extension("src/calc"): "not really a binary\n"}, "extension"),
    ({"src/helper.pyc": "not really bytecode\n"}, "bytecode"),
    ({"src/__pycache__/calc.cpython-312.pyc": "not really bytecode\n"}, "bytecode"),
    ({"calc.py": "def add(a, b):\n    return 5\n"}, "shadow"),   # named like src/calc.py, earlier on the path
])
def test_a_visible_file_the_patch_adds_that_could_change_the_tests_compromises_integrity(tmp_path, monkeypatch,
                                                                                         added, what):
    root = _repo(tmp_path, SRC)
    _write(root, added)
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Change add.", requirements=CHECK)
    assessment = got["assessment"]
    assert assessment["outcome"] == "test_integrity_compromised", assessment["reasons"]
    found = [r for r in assessment["reasons"] if r["code"] == "changed_file_can_change_tests"]
    assert found and found[0]["stage"] == "integrity", assessment["reasons"]
    assert any(rel in found[0]["detail"] for rel in added if not rel.endswith("x_plugin.py")), found


@pytest.mark.parametrize("added", [
    {"src/extra.py": "def extra():\n    return 1\n"},                                    # an ordinary new module
    {"src/shop/__init__.py": "", "src/shop/models.py": "X = 1\n"},                     # a new package's own modules
    {"src/x-1.0.dist-info/METADATA": "Name: x\n", "src/x-1.0.dist-info/entry_points.txt": "[console_scripts]\n"},
])
def test_an_ordinary_added_file_leaves_the_outcome_as_it_was(tmp_path, monkeypatch, added):
    root = _repo(tmp_path, {**SRC, "src/blog/__init__.py": "", "src/blog/models.py": "Y = 2\n"})
    _write(root, added)
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Add a module.", requirements=CHECK)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]

def test_a_module_the_patch_moves_shadows_nothing(tmp_path, monkeypatch):
    """Moved, the old path is gone: nothing is left for the new one to shadow (and a moved test file is the weakening
    detector's, compared with its old text)."""
    root = _repo(tmp_path, {**SRC, "src/helpers.py": "H = 1\n"})
    _git(root, "mv", "src/helpers.py", "helpers.py")
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Move a module.", requirements=CHECK)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]

# --- I4: a touched-module test that fails on the patch and is not compared with the base commit ------------------------

MORE = "from calc import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
STATED = [{"id": "R1", "text": "add adds", "checks": ["test_calc.py::test_add"]}]
ZERO = "test_more.py::test_zero"


def _tier2(monkeypatch, second, replay=lambda rel: {"verdict": "not_run", "said": "not replayed"}, spend=False):
    """The stated checks pass; the tests that touch the changed module give ``second`` (with ``spend``, after the
    whole time they were given, so the review's time budget is spent whatever the set-up took); each replay at the
    base commit and on the patch gives ``replay(file)``."""
    import time

    def execute(root, ids, *, timeout_s, change=None, **_):
        if change is not None:
            return {rel: replay(rel) for rel in ids}
        if all("::" in node for node in ids):
            return result(ids)
        if spend:
            time.sleep(timeout_s + 0.2)
        return second

    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda change, ids, *, timeout_s, **_: {
        "verdict": "not_run", "said": "no run was recorded"})


def _touched(tmp_path) -> Path:
    root = _repo(tmp_path, {"calc.py": BAD, "test_calc.py": "from calc import add\n\n\ndef test_add():\n"
                            "    assert add(2, 3) == 5\n", "test_more.py": MORE})
    _write(root, {"calc.py": GOOD})
    return root


FAILING = result(failed=[ZERO])
CASES = {
    "no failing test G6 can name": dict(second={**FAILING, "failed_ids": [], "test_outcomes": {}}),
    "the base never ran (the copy over its cap)": dict(second=FAILING, replay=lambda rel: {
        "verdict": "not_run", "said": "the repository copy cap of 10000 files was exceeded"}),
    "the replay is not read": dict(second=FAILING, replay=lambda rel: {
        **result([ZERO]), "loaded_outside": {"calc": "/elsewhere/site-packages/calc.py"}, "loaded_complete": True}),
    "failed in place, passed in the copy": dict(second=FAILING, replay=lambda rel: result([ZERO])),
    "the runs are not comparable": dict(second=FAILING, replay=lambda rel: {
        "verdict": "not_run", "said": "its tests did not match", "baseline": "passed", "patched": "passed"}),
    "tier 2 did not run to a result": dict(second={**result(), "verdict": "no_tests_collected", "said": "none"}),
    "tier 2 ran out of time": dict(second={**result(), "verdict": "timed_out", "said": "the time ran out"}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_touched_test_that_fails_and_is_not_compared_with_the_base_is_insufficient_evidence(tmp_path, monkeypatch,
                                                                                               case):
    root = _touched(tmp_path)
    _tier2(monkeypatch, **CASES[case])
    got = review_patch(root, task="Repair add.", requirements=STATED)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", (case, assessment["reasons"])
    assert ("evidence", "derived_test_not_compared") in _codes(got), (case, assessment["reasons"])


def test_the_copy_over_its_cap_is_insufficient_evidence(tmp_path, monkeypatch):
    """The real replay (``repo_tests.run_before``), whose copy stops at its file cap before anything runs."""
    from g6.workforce import repo_tests

    real = patch_review._execute
    root = _touched(tmp_path)
    _tier2(monkeypatch, FAILING)
    recorded = patch_review._execute
    monkeypatch.setattr(patch_review, "_execute", lambda root, ids, *, timeout_s, change=None, **kw: (
        real(root, ids, timeout_s=timeout_s, change=change, **kw) if change is not None
        else recorded(root, ids, timeout_s=timeout_s)))
    monkeypatch.setattr(repo_tests, "COPY_MAX_FILES", 1)
    got = review_patch(root, task="Repair add.", requirements=STATED)
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]["reasons"]
    found = [r["detail"] for r in got["assessment"]["reasons"] if r["code"] == "derived_test_not_compared"]
    assert found and "test_more.py" in found[0], got["assessment"]["reasons"]


def test_the_time_budget_spent_before_the_replay_is_insufficient_evidence(tmp_path, monkeypatch):
    root = _touched(tmp_path)
    _tier2(monkeypatch, FAILING, spend=True)
    got = review_patch(root, task="Repair add.", requirements=STATED, timeout_s=5)
    found = [r["detail"] for r in got["assessment"]["reasons"] if r["code"] == "derived_test_not_compared"]
    assert found and "time budget was spent" in found[0], got["assessment"]["reasons"]
    assert got["assessment"]["outcome"] == "insufficient_evidence"


def test_a_touched_test_that_did_not_pass_at_the_base_either_leaves_the_outcome_as_it_was(tmp_path, monkeypatch):
    root = _touched(tmp_path)
    _tier2(monkeypatch, FAILING, replay=lambda rel: {"verdict": "not_run", "said": "it failed there too",
                                                     "baseline": "failed"})
    got = review_patch(root, task="Repair add.", requirements=STATED)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert "derived_test_not_compared" not in [code for _, code in _codes(got)]

# --- reviewer B's critical item: what a confined run reads, never a folder the repository names ----------------------
# Everything here is fake: a home folder the test makes, with a fake key in its .ssh. Never a real private folder.

FAKE_KEY = "FAKE-SSH-KEY-not-a-real-one\n"
READS_KEY = "def test_read():\n    assert open({path!r}).read().startswith('FAKE-SSH-KEY')\n"
PYTHON_IN = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")


def _person(tmp_path, monkeypatch) -> Path:
    """A home folder of the test's own (HOME names it), holding .ssh with a fake key."""
    home = tmp_path / "people" / "someone"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_probe").write_text(FAKE_KEY, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return home


def _environment(folder: Path, home: str | None, *, starts: str = "") -> Path:
    """A virtual environment at ``folder``: its pyvenv.cfg names ``home`` (none when None), and its Python is a script
    that starts ``starts`` (POSIX), or an empty file that is never started."""
    python = folder.joinpath(*PYTHON_IN)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec {starts} "$@"\n' if starts else "", encoding="utf-8")
    python.chmod(0o755)
    if home is not None:
        (folder / "pyvenv.cfg").write_text(f"home = {home}\nversion = 3.12\n", encoding="utf-8")
    return python


def _installation() -> str:
    """A pyvenv.cfg ``home`` that names a real Python installation: the folder holding the base interpreter."""
    return sys.base_prefix if sys.platform == "win32" else str(Path(sys.base_prefix) / "bin")


def _repo_in(home: Path) -> Path:
    """The repository's folder, inside the person's home folder (as the probe has it)."""
    root = home / "code" / "repo"
    root.mkdir(parents=True)
    return root


def _commit(root: Path) -> Path:
    """The base commit, with what the folder holds (its .venv too): a wrong ``add`` and a stated check that passes only
    if it can read the fake key; the patch repairs ``add``."""
    _write(root, {"calc.py": BAD, "test_leak.py": READS_KEY.format(path=str(root.parents[1] / ".ssh" / "id_probe"))})
    _git(root, "init", "-q")
    _git(root, "add", "-f", ".")
    _git(root, "commit", "-qm", "base")
    _write(root, {"calc.py": GOOD})
    return root


LEAK = [{"id": "R1", "text": "the check reads the key", "checks": ["test_leak.py::test_read"]}]


@pytest.mark.parametrize("named", ["its pyvenv.cfg names the folder", "its pyvenv.cfg names the folder's bin",
                                   "the environment is a link to the folder"])
def test_a_repository_environment_that_would_open_a_folder_of_the_persons_is_refused_before_anything_runs(
        tmp_path, monkeypatch, started, named):
    from g6.workforce import repo_tests

    monkeypatch.delenv(repo_tests.REPO_PYTHON, raising=False)
    home = _person(tmp_path, monkeypatch)
    root = _repo_in(home)
    if named == "the environment is a link to the folder":
        if sys.platform == "win32":
            pytest.skip("a link needs a privilege on Windows")
        _environment(home / ".ssh", None)   # a Python in the person's folder, and .venv a link to that folder
        (root / ".venv").symlink_to(home / ".ssh", target_is_directory=True)
    else:   # on POSIX a home that ends in bin names the folder above it
        _environment(root / ".venv", str(home / ".ssh" / "bin") if named.endswith("bin") else str(home / ".ssh"))
    _commit(root)
    got = review_patch(root, task="Repair add.", requirements=LEAK)
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated", got.get("assessment")
    said = got["warning_card"]["message"]
    assert got["warning_card"]["code"] == repo_tests.REPO_PYTHON_INVALID, said
    assert ".ssh" in said and "standard library" in said and repo_tests.REPO_PYTHON in said, said
    assert "icacls" not in said and "grant" not in said   # never a request to open the folder
    assert started == []   # no test process was started


@pytest.mark.parametrize("home", ["a real installation", "a folder inside the repository", "none"])
def test_a_repository_environment_that_reads_only_an_installation_and_itself_is_used(tmp_path, monkeypatch, home):
    from g6.workforce import repo_tests

    monkeypatch.delenv(repo_tests.REPO_PYTHON, raising=False)
    root = _repo(tmp_path / "repo", {"calc.py": GOOD})
    named = {"a real installation": _installation(), "a folder inside the repository": str(root / "py"),
             "none": None}[home]
    python = _environment(root / ".venv", named)
    assert repo_tests.interpreter(root) == str(python)


def test_the_setting_reads_its_own_environment_and_an_installation_never_a_folder_its_pyvenv_cfg_names(
        tmp_path, monkeypatch):
    """G6_REPO_PYTHON names the person's environment, whose own folder (outside the repository) a run reads; the
    base installation its pyvenv.cfg names must be one, as for the repository's own (one inside the repository is
    the repository's text)."""
    from g6.workforce import repo_tests

    home = _person(tmp_path, monkeypatch)
    root = _repo(tmp_path / "repo", {"calc.py": GOOD})
    poetry = _environment(tmp_path / "poetry-env", _installation())
    monkeypatch.setenv(repo_tests.REPO_PYTHON, str(poetry))
    assert repo_tests.interpreter(root) == str(poetry)
    inside = _environment(root / ".tox" / "py312", str(home / ".ssh"))
    monkeypatch.setenv(repo_tests.REPO_PYTHON, str(inside))
    with pytest.raises(repo_tests.RepoRefused) as refused:
        repo_tests.interpreter(root)
    assert refused.value.code == repo_tests.REPO_PYTHON_INVALID and ".ssh" in str(refused.value)


@pytest.mark.parametrize("home", ["a real installation", "a folder of the person's"])
def test_confined_a_repository_environment_never_opens_a_folder_of_the_persons(tmp_path, monkeypatch, bwrap_backend,
                                                                               home):
    """Reviewer B's probe (probe_cross_venv_home_bind.py): the repository's committed .venv, whose Python starts G6's
    own base interpreter. The control reads a real installation, and the fake key stays out of sight (the check
    fails); the planted pyvenv.cfg names the fake .ssh, and the review is refused before anything runs."""
    from g6.workforce import repo_tests

    monkeypatch.delenv(repo_tests.REPO_PYTHON, raising=False)
    person = _person(tmp_path, monkeypatch)
    root = _repo_in(person)
    _environment(root / ".venv", _installation() if home == "a real installation" else str(person / ".ssh"),
                 starts=sys.executable)
    _commit(root)
    got = review_patch(root, task="Repair add.", requirements=LEAK, timeout_s=120)
    if home == "a real installation":
        assessment = got["assessment"]
        assert assessment["scope"]["environment"]["interpreter_source"] == "repository", assessment["scope"]
        assert assessment["scope"]["environment"]["isolation"]["backend"] == "bubblewrap"
        assert assessment["outcome"] == "defect_detected", assessment["reasons"]   # the key was not there to read
        assert [(r["id"], r["status"]) for r in assessment["requirements"]] == [("R1", "failed")]
    else:
        assert got["ok"] is False and got["warning_card"]["code"] == repo_tests.REPO_PYTHON_INVALID, got
        assert "assessment" not in got

# --- I3: changed test data and snapshots, which G6 does not compare: ask, don't accuse -------------------------------

DATA = {"calc.py": BAD, "tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"}
DATA_CHECK = [{"id": "R1", "text": "add adds", "checks": ["tests/test_calc.py::test_add"]}]


@pytest.mark.parametrize("rel", ["tests/data/expected.json", "tests/__snapshots__/test_calc.ambr",
                                 "src/pkg/__snapshots__/view.ambr", "Testing/golden.txt"])
def test_changed_test_data_or_a_snapshot_is_insufficient_evidence_and_the_person_is_asked(tmp_path, monkeypatch, rel):
    root = _repo(tmp_path, {**DATA, rel: '{"sum": 5}\n'})
    _write(root, {"calc.py": GOOD, rel: '{"sum": -1}\n'})   # re-pinned
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Repair add.", requirements=DATA_CHECK)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    found = [r for r in assessment["reasons"] if r["code"] == "test_data_changed"]
    assert found and found[0]["stage"] == "evidence" and rel in found[0]["detail"], assessment["reasons"]
    asked = [action for action in assessment["next_actions"] if rel in action]
    assert asked and "diff" in asked[0], assessment["next_actions"]
    assert "no sign that the tests" not in assessment["summary"]


@pytest.mark.parametrize("changed", [{"README.md": "# calc, repaired\n"}, {"docs/tests.md": "how to test\n"}])
def test_a_changed_file_outside_the_test_folders_leaves_the_outcome_as_it_was(tmp_path, monkeypatch, changed):
    root = _repo(tmp_path, {**DATA, "README.md": "# calc\n", "docs/tests.md": "testing\n"})
    _write(root, {"calc.py": GOOD, **changed})
    _record(monkeypatch, lambda ids: result(ids))
    got = review_patch(root, task="Repair add.", requirements=DATA_CHECK)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]

# --- I6: a module the patch imports that is not installed here: ask, never blame the environment ---------------------

MISSING = "not_a_real_pkg_xyz"
CALC_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
ASK = ("The patch imports {name}, which is not installed here: check that it is the package you intend, and that it "
       "exists under that name, before installing anything.")


def _uncollectable(name: str) -> dict:
    """A recorded run pytest could not collect: the import of ``name`` failed (the environment's, by G6's reading)."""
    from g6.workforce import repo_tests

    return {**result(), "verdict": "collection_error", "said": repo_tests.SAID["collection_error"],
            "output": f"ERROR collecting test_calc.py\nE   ModuleNotFoundError: No module named '{name}'\n"
                      "ERROR test_calc.py\n", "test_outcomes": {}, "collected_ids": []}


def _no_run_text(got: dict) -> None:
    import json

    shown = json.dumps(got)
    assert "make those tests run" not in shown and "make those checks run" not in shown
    assert "make its stated checks run" not in shown


@pytest.mark.parametrize(("line", "reported"), [
    (f"import {MISSING}", MISSING),
    (f"from {MISSING}.sub import thing", MISSING),
    (f"import os, {MISSING}.sub as sub", f"{MISSING}.sub"),
])
def test_a_module_the_patch_imports_that_is_not_installed_is_asked_about_never_blamed_on_the_environment(
        tmp_path, monkeypatch, line, reported):
    root = _repo(tmp_path, {"calc.py": BAD, "test_calc.py": CALC_TEST})
    _write(root, {"calc.py": f"{line}\n\n\n{GOOD}"})
    _record(monkeypatch, lambda ids: _uncollectable(reported))
    got = review_patch(root, task="Repair add.", requirements=STATED)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    found = [r for r in assessment["reasons"] if r["code"] == "patch_imports_missing_module"]
    assert found and found[0]["stage"] == "evidence" and reported in found[0]["detail"], assessment["reasons"]
    assert assessment["next_actions"][0] == ASK.format(name=reported), assessment["next_actions"]
    _no_run_text(got)


def test_a_missing_module_the_patch_does_not_import_leaves_the_review_as_it_was(tmp_path, monkeypatch):
    """The base commit imports it already: the environment lacks a dependency, as before."""
    root = _repo(tmp_path, {"calc.py": f"import {MISSING}\n\n\n{BAD}", "test_calc.py": CALC_TEST})
    _write(root, {"calc.py": f"import {MISSING}\n\n\n{GOOD}"})
    _record(monkeypatch, lambda ids: _uncollectable(MISSING))
    got = review_patch(root, task="Repair add.", requirements=STATED)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert "patch_imports_missing_module" not in [code for _, code in _codes(got)]
    assert assessment["next_actions"][0] == patch_review._NEXT["insufficient_evidence"]


def test_confined_a_module_the_patch_imports_that_is_not_installed_is_asked_about(tmp_path, monkeypatch,
                                                                                bwrap_backend):
    from g6.workforce import repo_tests

    monkeypatch.delenv(repo_tests.REPO_PYTHON, raising=False)
    root = _repo(tmp_path, {"calc.py": BAD, "test_calc.py": CALC_TEST})
    _write(root, {"calc.py": f"import {MISSING}\n\n\n{GOOD}"})
    got = review_patch(root, task="Repair add.", requirements=STATED, timeout_s=120)
    assessment = got["assessment"]
    assert assessment["outcome"] == "insufficient_evidence", assessment["reasons"]
    assert ("evidence", "patch_imports_missing_module") in _codes(got), assessment["reasons"]
    assert assessment["next_actions"][0] == ASK.format(name=MISSING)
    _no_run_text(got)

# --- M1: run_inconsistent only when G6's own per-test record agrees that every test passed ---------------------------

PASSED_TALLY = {"tests": 2, "passed": 2, "failures": 0, "errors": 0, "skipped": 0, "collection": 0, "placed": 0,
                "outside": 0, "expected": 2, "finished": 2}


@pytest.mark.parametrize(("record", "verdict"), [
    ({"failed_ids": [], "setup_errors": {}}, "run_inconsistent"),
    ({"failed_ids": ["test_calc.py::test_add"], "setup_errors": {}}, "inconsistent_report"),
    ({"failed_ids": [], "setup_errors": {"test_calc.py::test_add": "fixture 'db' not found"}}, "inconsistent_report"),
])
def test_an_exit_error_after_passes_is_run_inconsistent_only_when_no_test_is_recorded_as_failed(record, verdict):
    from g6.workforce import repo_tests

    assert repo_tests._judge(1, {**PASSED_TALLY, **record}, "", []) == verdict


#: A runner file of the base commit that takes every failure out of pytest's JUnit report after it is written.
REWRITES_REPORT = ("import re\n\n\ndef pytest_unconfigure(config):\n"
                   "    path = getattr(config.option, 'xmlpath', None)\n"
                   "    if path:\n"
                   "        with open(path, encoding='utf-8') as handle:\n"
                   "            text = handle.read()\n"
                   "        text = re.sub(r'<failure[^>]*>.*?</failure>|<failure[^>]*/>', '', text, flags=re.S)\n"
                   "        with open(path, 'w', encoding='utf-8') as handle:\n"
                   "            handle.write(text)\n")


def test_confined_a_report_that_hides_a_failure_g6_recorded_compromises_integrity(tmp_path, monkeypatch,
                                                                                 bwrap_backend):
    from g6.workforce import repo_tests

    monkeypatch.delenv(repo_tests.REPO_PYTHON, raising=False)
    root = _repo(tmp_path, {"calc.py": GOOD, "test_calc.py": CALC_TEST, "conftest.py": REWRITES_REPORT})
    _write(root, {"calc.py": BAD})   # the stated check fails; the report says it passed
    got = review_patch(root, task="Change add.", requirements=STATED, timeout_s=120)
    assessment = got["assessment"]
    assert assessment["outcome"] == "test_integrity_compromised", assessment["reasons"]
    assert ("integrity", "run_not_trusted") in _codes(got), assessment["reasons"]
