# SPDX-License-Identifier: Apache-2.0
"""g6_review_patch's technical assessment: four outcomes in a fixed order, each stated requirement with its own
evidence, merge authorisation kept apart, and refusals that are never counted as detections.

Development fixtures only: none of these patches is V9 evaluation data. Tests that take ``bwrap_backend`` run real
confined pytest runs (Linux bubblewrap) and skip elsewhere; the others replace G6's test runs with recorded results
(``Recorded``), which says nothing about confinement, and the refusal tests switch the boundary off."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core import component_process, confine
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.harness import patch_review
from g6.harness.patch_review import review_patch
from g6.invariants import check_envelope

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
POSITIVE_ONLY = "def add(a, b):\n    return a + b if a > 0 else a - b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
ADD, NEGATIVE = "test_calc.py::test_add", "test_calc.py::test_negative"
SUM = [{"id": "R1", "text": "add returns the sum of two positive integers", "checks": [ADD]},
       {"id": "R2", "text": "add returns the sum of two negative integers", "checks": [NEGATIVE]}]
OUTCOMES = {"supported_within_scope", "defect_detected", "test_integrity_compromised", "insufficient_evidence"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text(BAD)
    (tmp_path / "test_calc.py").write_text(TEST)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "base")
    return tmp_path


def review(repo, requirements=SUM, **kwargs):
    return review_patch(repo, task="Repair addition for positive and negative integers.", requirements=requirements,
                        **kwargs)


def result(passed=(), failed=(), skipped=(), *, verdict=None, backend="synthetic-unit-fixture", said="", output="",
           receipt=""):
    """One recorded ``repo_tests.run`` result: these node ids passed, failed or were skipped."""
    outcomes = {**{node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in passed},
                **{node: {"setup": "passed", "call": "failed", "teardown": "passed"} for node in failed},
                **{node: {"setup": "skipped", "teardown": "passed"} for node in skipped}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": said, "passed": len(passed), "failed": len(failed),
            "not_passed": len(skipped), "total": len(passed) + len(failed), "deselected": 0,
            "test_outcomes": outcomes, "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [],
            "output": output, "seconds": 0.25, "python": sys.executable, "interpreter_source": "g6",
            "command": "pytest", "isolation": {"backend": backend, "why": ""}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": receipt}


class Recorded:
    """Stands in for every test run of a review (``patch_review._execute``): in place, ``in_place[ids]`` (or
    ``default``); replayed at the base commit, ``on_base[file]``. Records what was asked."""

    def __init__(self, monkeypatch, in_place=None, *, default=None, on_base=None):
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        in_place, on_base = dict(in_place or {}), dict(on_base or {})

        def execute(root, ids, *, timeout_s, change=None):
            self.calls.append(("base" if change is not None else "in_place", tuple(ids)))
            if change is not None:
                return {rel: on_base[rel] for rel in ids}
            got = in_place.get(tuple(ids), default)
            assert got is not None, f"no recorded result for {ids}"
            return got

        monkeypatch.setattr(patch_review, "_execute", execute)


def _reasons(got) -> set[str]:
    return {reason["code"] for reason in got["assessment"]["reasons"]}


def _requirement(got, ident) -> dict:
    return next(r for r in got["assessment"]["requirements"] if r["id"] == ident)


# --- the four outcomes, with recorded runs --------------------------------------------------------------------------

def test_a_correct_patch_is_supported_within_its_stated_scope(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    runs = Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]),
                                  ("test_calc.py",): result([ADD, NEGATIVE])})
    got = review(repo)
    assessment = got["assessment"]
    assert assessment["outcome"] == "supported_within_scope", assessment
    assert got["ok"] is True and got["completion_state"] == "qualified-draft" and check_envelope(got) == []
    for ident, check in (("R1", ADD), ("R2", NEGATIVE)):
        found = _requirement(got, ident)
        assert found["status"] == "passed_on_stated_checks" and check in found["reason"]
        assert [(c["id"], c["verdict"]) for c in found["checks"]] == [(check, "passed")]
    scope = assessment["scope"]
    assert scope["artifact_digest"] == got["artifact_digest"] and len(scope["base_commit"]) == 40
    assert scope["changed_files"] == ["calc.py"]
    assert scope["environment"]["isolation"]["backend"] == "synthetic-unit-fixture"
    assert scope["environment"]["python"] == sys.executable
    assert [(run["tier"], run["verdict"]) for run in scope["checks_run"]] == [(1, "passed"), (2, "passed")]
    assert all(run["command"].startswith("pytest ") and run["seconds"] >= 0 for run in scope["checks_run"])
    assert any(skip["tier"] == 3 and "tier 3 not run" in skip["reason"] for skip in scope["checks_skipped"])
    assert isinstance(scope["seconds"], float)
    assert runs.calls == [("in_place", (ADD, NEGATIVE)), ("in_place", ("test_calc.py",))]
    # it never claims correctness, safety or completeness
    words = set((assessment["summary"] + " " + got["warning_card"]["message"]).lower().replace(".", " ")
                .replace(",", " ").replace(":", " ").split())
    assert not {"correct", "safe", "complete", "proven", "verified"} & words


def test_a_requirement_with_no_check_is_not_established_and_the_outcome_is_insufficient_evidence(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD,): result([ADD]), ("test_calc.py",): result([ADD, NEGATIVE])})
    unchecked = [SUM[0], {"id": "R2", "text": "add returns the sum of two negative integers", "checks": []}]
    got = review(repo, unchecked)
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    second = _requirement(got, "R2")
    assert second["status"] == "not_established" and second["reason"]
    assert "negative integers" in second["suggested_test"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert got["assessment"]["requirements_without_adequate_check"] == ["R2"]
    assert "requirement_without_check" in _reasons(got)
    # G6 never invents a passing test: the derived run that covered the negative case does not establish R2
    assert second["checks"] == []


def test_no_stated_requirement_is_insufficient_evidence(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    runs = Recorded(monkeypatch, {("test_calc.py",): result([ADD, NEGATIVE])})
    got = review(repo, [], claim="All tests pass.")
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    assert "no_requirement_stated" in _reasons(got)
    assert runs.calls == [("in_place", ("test_calc.py",))]   # tier 1 had nothing to run; tier 2 still ran


def test_a_stated_check_that_did_not_run_is_not_evidence(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD], skipped=[NEGATIVE]),
                           ("test_calc.py",): result([ADD], skipped=[NEGATIVE])})
    got = review(repo)
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    assert _requirement(got, "R2")["status"] == "not_established"
    assert _requirement(got, "R2")["checks"][0]["verdict"] == "skipped"
    assert "check_did_not_run" in _reasons(got)
    missing = review(repo, [{"id": "R1", "text": "add sums", "checks": ["test_gone.py::test_add"]}])
    assert missing["assessment"]["outcome"] == "insufficient_evidence"
    assert _requirement(missing, "R1")["checks"][0]["verdict"] == "missing"


def test_a_weakened_test_compromises_test_integrity(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    (repo / "test_calc.py").write_text(TEST.replace("assert add(-2, -3) == -5", "assert True"))
    runs = Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert any(r["stage"] == "integrity" and r["code"] == "test_weakened" and "test_negative" in r["detail"]
               for r in got["assessment"]["reasons"])
    assert got["completion_state"] == "qualified-draft" and got["ok"] is True
    assert runs.calls == [("in_place", (ADD, NEGATIVE))]   # tier 2 skipped
    assert any(skip["tier"] == 2 for skip in got["assessment"]["scope"]["checks_skipped"])


PARAMETRIZED = ("import pytest\n\nfrom calc import add\n\n\n@pytest.mark.parametrize('a, b, total', [(2, 3, 5), "
                "(-2, -3, -5)])\ndef test_sums(a, b, total):\n    assert add(a, b) == total\n")
HELPER = "from calc import add\n\n\ndef check(a, b, total):\n    assert add(a, b) == total\n"


def _commit(repo, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", message)


@pytest.mark.parametrize("rel, before, after, named", [
    # review C2, probe S1: a test file by pytest's other default name
    ("tests/calc_test.py", "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
     "from calc import add\n\n\ndef test_add():\n    add(2, 3)\n", "test_add"),
    # probe S2: a parametrize case cut
    ("test_sums.py", PARAMETRIZED, PARAMETRIZED.replace(", (-2, -3, -5)", ""), "(-2, -3, -5)"),
    # probe S3: the helper module the tests check through, gutted
    ("tests/helpers.py", HELPER, "def check(a, b, total):\n    pass\n", "check"),
])
def test_every_changed_test_file_and_test_helper_is_compared_for_weakening(repo, monkeypatch, rel, before, after,
                                                                          named):
    """Review C2: only test_*.py files were compared, and only their asserts, so these three weakenings of a defective
    patch's tests read supported_within_scope."""
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(before)
    _commit(repo, "more tests")
    (repo / rel).write_text(after)   # calc.py stays BAD: the weakened check is all that lets it pass
    (repo / "calc.py").write_text(BAD + "\n")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised", got["assessment"]["reasons"]
    assert any(r["code"] == "test_weakened" and rel in r["detail"] and named in r["detail"]
               for r in got["assessment"]["reasons"]), got["assessment"]["reasons"]


def test_a_moved_test_helper_is_compared_with_its_old_text_not_read_as_deleted(repo, monkeypatch):
    (repo / "tests").mkdir()
    (repo / "tests" / "helpers.py").write_text(HELPER)
    _commit(repo, "a helper")
    (repo / "calc.py").write_text(GOOD)
    _git(repo, "mv", "tests/helpers.py", "tests/checks.py")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]


@pytest.mark.parametrize("name", [".pytest.ini", "pytest.ini", "conftest.py", "pytest.toml", ".pytest.toml"])
def test_a_changed_runner_configuration_compromises_test_integrity(repo, monkeypatch, name):
    """An empty ``pytest.toml`` is pytest's configuration file whenever it exists (review C1, probe S13): it replaces a
    ``pyproject.toml`` whose ``filterwarnings = ['error']`` the patch would have failed."""
    (repo / "calc.py").write_text(GOOD)
    (repo / name).write_text("[pytest]\naddopts = -q\n" if name.endswith(".ini") else "\n")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert any(r["code"] == "runner_configuration_changed" and name in r["detail"]
               for r in got["assessment"]["reasons"])


def _pytest_config_names() -> set[str]:
    """The configuration files the installed pytest looks for by name: its ``CONFIG_LOADERS_BY_NAME`` (pytest 9.2 on),
    else the file names ``locate_config`` holds as constants (earlier versions); a skip that says so when neither
    can be read, so a pytest that moves them is noticed rather than read as "no names"."""
    import re

    from _pytest.config import findpaths

    names = getattr(findpaths, "CONFIG_LOADERS_BY_NAME", None)
    if isinstance(names, dict) and names:
        return set(names)
    found: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, str) and re.fullmatch(r"\.?[\w-]+\.(?:ini|toml|cfg)", value):
            found.add(value)
        elif isinstance(value, (tuple, frozenset)):
            for item in value:
                walk(item)

    code = getattr(getattr(findpaths, "locate_config", None), "__code__", None)
    walk(tuple(code.co_consts) if code is not None else ())
    if not found:
        pytest.skip(f"pytest {pytest.__version__} names its configuration files neither in "
                    "_pytest.config.findpaths.CONFIG_LOADERS_BY_NAME nor as constants of locate_config")
    return found


def test_every_configuration_file_pytest_reads_is_one_g6_checks():
    """Review C1: G6's lists of pytest's configuration files lacked pytest.toml and .pytest.toml. Read from the
    installed pytest, so a name a later pytest adds fails here."""
    from g6.workforce import repo_tests

    names = _pytest_config_names()
    assert {"pytest.ini", "pyproject.toml"} <= names   # the reading itself works
    assert names <= patch_review.RUNNER_FILES          # a change to one is an integrity reason
    assert names <= repo_tests.RUNNER_FILES            # a change to one is a runner file of the change
    assert names <= {name for name, _ in repo_tests._PYTEST_CONFIG}   # one in the repository is its own config
    assert names <= repo_tests._LOADABLE               # a git-ignored one withholds the receipt


def test_a_git_ignored_file_pytest_could_load_compromises_test_integrity(repo, monkeypatch):
    """I2: an ignored conftest.py is outside git's view, so outside the artifact digest, yet pytest loads it."""
    (repo / "calc.py").write_text(GOOD)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("conftest.py\n")
    (repo / "conftest.py").write_text("def pytest_runtest_setup(item):\n    pass\n")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert any(r["code"] == "ignored_file_can_change_tests" and "conftest.py" in r["detail"]
               for r in got["assessment"]["reasons"])


def _ignore(repo, rel: str, text: str) -> None:
    (repo / ".git" / "info").mkdir(exist_ok=True)
    with open(repo / ".git" / "info" / "exclude", "a", encoding="utf-8") as handle:
        handle.write(rel + "\n")
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)


def test_an_ignored_python_file_that_shadows_nothing_is_an_unknown_not_an_integrity_problem(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    _ignore(repo, "_version.py", "version = '1.0'\n")   # setuptools-scm writes one beside the package's modules
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])})
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert "an ignored file G6 did not judge: _version.py" in got["assessment"]["unknowns"]


def test_an_ignored_module_that_shadows_a_tracked_one_compromises_integrity(repo, monkeypatch):
    (repo / "src").mkdir()
    _git(repo, "mv", "calc.py", "src/calc.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "src layout")
    (repo / "src" / "calc.py").write_text(GOOD)
    _ignore(repo, "tests/calc.py", "def add(a, b):\n    return 5\n")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert any(r["code"] == "ignored_file_can_change_tests" and "tests/calc.py" in r["detail"]
               for r in got["assessment"]["reasons"])


@pytest.mark.parametrize("rel", ["sitecustomize.py", "test_extra.py"])
def test_an_ignored_start_up_or_test_file_compromises_integrity(repo, monkeypatch, rel):
    (repo / "calc.py").write_text(GOOD)
    _ignore(repo, rel, "import os\n")
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert any(r["code"] == "ignored_file_can_change_tests" and rel in r["detail"]
               for r in got["assessment"]["reasons"])


@pytest.mark.parametrize("pattern, rel, text", [
    ("*.pyc", "tests/calc.pyc", "bytecode Python imports in place of calc.py\n"),            # review C3, probe S6
    ("*.egg-info/", "zz.egg-info/entry_points.txt", "[pytest11]\nzz = zz_absent_plugin\n"),  # probe S7
])
def test_an_ignored_bytecode_file_or_plugin_declaration_compromises_integrity(repo, monkeypatch, pattern, rel, text):
    (repo / "calc.py").write_text(GOOD)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text(pattern + "\n")
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised", got["assessment"]["reasons"]
    assert any(r["code"] == "ignored_file_can_change_tests" and rel in r["detail"]
               for r in got["assessment"]["reasons"])


def test_an_ignored_file_scan_that_reaches_its_limit_is_insufficient_evidence(repo, monkeypatch):
    from g6.workforce import repo_tests

    (repo / "calc.py").write_text(GOOD)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("logs/\n")
    (repo / "logs").mkdir()
    for index in range(5):
        (repo / "logs" / f"run{index}.log").write_text("x\n")
    monkeypatch.setattr(repo_tests, "IGNORED_SCAN_LIMIT", 2)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    assert "ignored_files_not_checked" in _reasons(got)


@pytest.mark.parametrize("verdict", ["changed_tracked_files", "inconsistent_report", "incomplete"])
def test_a_run_that_cannot_be_believed_compromises_test_integrity(repo, monkeypatch, verdict):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE], verdict=verdict))
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised"
    assert "run_not_trusted" in _reasons(got)
    assert _requirement(got, "R1")["status"] != "passed_on_stated_checks"


def test_a_run_that_began_on_another_tree_compromises_integrity_whatever_the_gate_says(repo, monkeypatch):
    """Review I4 (probe S9): the tree was the defective one when the review took its digest, the fixed one while the
    tests ran, and the defective one again at the end, so the before and after digests matched and only the receipts
    (artifact_mismatch, an unknown) showed it. The run's own record of the tree it began on decides."""
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default={**result([ADD, NEGATIVE]), "source_before": "0" * 64})
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised", got["assessment"]["reasons"]
    assert any(r["code"] == "repository_changed_during_review" and "tier 1" in r["detail"]
               for r in got["assessment"]["reasons"])


def test_a_moved_test_file_is_compared_with_its_old_text_not_read_as_deleted(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    _git(repo, "mv", "test_calc.py", "test_addition.py")
    moved = [{**req, "checks": [check.replace("test_calc.py", "test_addition.py") for check in req["checks"]]}
             for req in SUM]
    nodes = [ADD.replace("test_calc", "test_addition"), NEGATIVE.replace("test_calc", "test_addition")]
    Recorded(monkeypatch, {tuple(nodes): result(nodes), ("test_addition.py",): result(nodes)})
    got = review(repo, moved)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert not any("deleted" in r["detail"] for r in got["assessment"]["reasons"])
    # survival: a moved test that was also weakened is still found
    (repo / "test_addition.py").write_text(TEST.replace("assert add(2, 3) == 5", "assert True"))
    weakened = review(repo, moved)
    assert weakened["assessment"]["outcome"] == "test_integrity_compromised"
    assert any("test_addition.py: test_add" in r["detail"] for r in weakened["assessment"]["reasons"])


def test_a_defect_inside_the_declared_coverage_is_detected_and_named(repo, monkeypatch):
    (repo / "calc.py").write_text(POSITIVE_ONLY)
    runs = Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD], [NEGATIVE])})
    got = review(repo)
    assert got["assessment"]["outcome"] == "defect_detected"
    assert any(r["stage"] == "tier_1" and r["code"] == "check_failed" and NEGATIVE in r["detail"]
               and "failed" in r["detail"] for r in got["assessment"]["reasons"])
    assert _requirement(got, "R2")["status"] == "failed" and NEGATIVE in _requirement(got, "R2")["reason"]
    assert _requirement(got, "R1")["status"] == "passed_on_stated_checks"
    assert NEGATIVE in got["warning_card"]["message"]
    assert runs.calls == [("in_place", (ADD, NEGATIVE))]   # tier 2 skipped after a defect


@pytest.mark.parametrize("verdict, outcome, check", [
    ("failed", "defect_detected", "failed"),
    # review I5: corrected. This case expected a defect from a run out of time, which says nothing against the patch:
    # the check it never reached did not run.
    ("timed_out", "insufficient_evidence", "not_run"),
])
def test_a_stated_check_that_fails_to_collect_is_a_defect_and_one_the_time_never_reached_did_not_run(
        repo, monkeypatch, verdict, outcome, check):
    """``failed`` with no test run is a collection error G6 places in the repository's own code."""
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result(verdict=verdict, said="the run stopped"))
    got = review(repo)
    assert got["assessment"]["outcome"] == outcome
    assert _requirement(got, "R1")["checks"][0]["verdict"] == check


def test_a_run_out_of_time_is_insufficient_evidence_and_a_check_it_never_reached_did_not_run(repo, monkeypatch):
    """Review I5 (probe S10): a correct patch whose first stated check is slow. The run's time budget ran out during
    that check and before the second started; both read as failed, and the outcome as defect_detected."""
    (repo / "calc.py").write_text(GOOD)
    out_of_time = {**result(verdict="timed_out", said="the tests did not finish within 8 seconds"),
                   "test_outcomes": {ADD: {"setup": "passed"}}}   # started, never finished
    Recorded(monkeypatch, default=out_of_time)
    got = review(repo)
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]["reasons"]
    assert [c["verdict"] for c in _requirement(got, "R1")["checks"]] == ["timed_out"]
    assert [c["verdict"] for c in _requirement(got, "R2")["checks"]] == ["not_run"]
    assert {_requirement(got, ident)["status"] for ident in ("R1", "R2")} == {"not_established"}
    assert "check_failed" not in _reasons(got) and "run_timed_out" in _reasons(got)


def test_test_ids_are_read_against_the_root_folder_pytest_used(repo, monkeypatch):
    """Review I9(a) (probe S5): a pytest.ini in tests/ makes that folder pytest's root, and pytest names each test
    relative to it, so the stated check tests/test_calc.py::test_add read as not collected though it passed."""
    _git(repo, "rm", "-q", "test_calc.py")
    (repo / "tests").mkdir()
    (repo / "tests" / "pytest.ini").write_text("[pytest]\n")
    (repo / "tests" / "test_calc.py").write_text(TEST)
    _commit(repo, "tests in a folder with their own pytest.ini")
    (repo / "calc.py").write_text(GOOD)
    ids = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_negative"]
    ran = {**result(["test_calc.py::test_add", "test_calc.py::test_negative"]), "rootdir": "tests"}
    Recorded(monkeypatch, default=ran)
    got = review(repo, [{**req, "checks": [ident]} for req, ident in zip(SUM, ids)])
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert [c["verdict"] for req in got["assessment"]["requirements"] for c in req["checks"]] == ["passed", "passed"]


def test_a_stated_check_the_environment_cannot_collect_is_not_established(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    lacking = {"verdict": "not_run", "said": "the tests could not be collected", "baseline": "collection_error"}
    Recorded(monkeypatch, default=result(verdict="collection_error", output="ERROR test_calc.py\n"),
             on_base={"test_calc.py": lacking})   # the environment lacks the dependency at the base commit too
    got = review(repo)
    assert got["assessment"]["outcome"] == "insufficient_evidence"
    first = _requirement(got, "R1")
    assert first["status"] == "not_established" and "could not be collected in this environment" in first["reason"]
    assert "check_failed" not in _reasons(got)


def test_only_the_check_whose_file_failed_to_collect_is_named(repo, monkeypatch):
    (repo / "test_more.py").write_text("import calc\n\n\ndef test_more():\n    assert calc.add(0, 0) == 0\n")
    (repo / "calc.py").write_text(GOOD)
    more = [*SUM, {"id": "R3", "text": "add sums zeros", "checks": ["test_more.py::test_more"]}]
    Recorded(monkeypatch, default=result(verdict="failed", output="ERROR collecting test_more.py\nERROR test_more.py"))
    got = review(repo, more)
    assert got["assessment"]["outcome"] == "defect_detected"
    assert _requirement(got, "R3")["checks"][0]["verdict"] == "failed"
    assert _requirement(got, "R1")["checks"][0]["verdict"] == "not_run"


PYPROJECT = '[project]\nname = "calc"\nversion = "1.0"\ndependencies = []\n'
SETUP_CFG = "[metadata]\nname = calc\nversion = 1.0\n"
TOX = "[tox]\nenvlist = py313\n\n[testenv]\ncommands = pytest\n"


@pytest.mark.parametrize("name, before, after, section", [
    ("pyproject.toml", PYPROJECT, PYPROJECT.replace("[]", '["requests"]'), ""),
    ("pyproject.toml", PYPROJECT, PYPROJECT + '\n[tool.pytest.ini_options]\naddopts = "-q"\n', "[tool.pytest]"),
    ("pyproject.toml", None, PYPROJECT, ""),
    ("pyproject.toml", None, PYPROJECT + '\n[tool.pytest.ini_options]\naddopts = "-q"\n', "[tool.pytest]"),
    ("setup.cfg", SETUP_CFG, SETUP_CFG.replace("1.0", "1.1"), ""),
    ("setup.cfg", SETUP_CFG, SETUP_CFG + "\n[tool:pytest]\naddopts = -q\n", "[tool:pytest]"),
    ("tox.ini", TOX, TOX.replace("py313", "py312"), ""),
    ("tox.ini", TOX, TOX + "\n[pytest]\naddopts = -q\n", "[pytest]"),
    # review I7 (probe S14): an empty section is still pytest's config, and outranks the one it read before
    ("tox.ini", None, "[pytest]\n", "[pytest]"),
    ("tox.ini", TOX, TOX + "\n[pytest]\n", "[pytest]"),
    ("setup.cfg", SETUP_CFG, SETUP_CFG + "\n[tool:pytest]\n", "[tool:pytest]"),
    # ... but pytest reads no configuration from an empty [tool.pytest] table: nothing it does changes
    ("pyproject.toml", PYPROJECT, PYPROJECT + "\n[tool.pytest]\n", ""),
])
def test_a_project_file_counts_only_when_its_test_runner_settings_change(repo, monkeypatch, name, before, after,
                                                                         section):
    if before is not None:
        (repo / name).write_text(before)
        _git(repo, "add", name)
        _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-qm", "project file")
    (repo / "calc.py").write_text(GOOD)
    (repo / name).write_text(after)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    runner = [r["detail"] for r in got["assessment"]["reasons"] if r["code"] == "runner_configuration_changed"]
    if section:
        assert got["assessment"]["outcome"] == "test_integrity_compromised"
        assert runner and name in runner[0] and section in runner[0]
    else:
        assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
        assert runner == []


def test_a_derived_test_failing_on_base_and_patch_is_pre_existing_not_a_defect(repo, monkeypatch):
    (repo / "test_legacy.py").write_text("import calc\n\n\ndef test_legacy():\n    assert calc.add(1, 1) == 3\n")
    _git(repo, "add", "test_legacy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "legacy")
    (repo / "calc.py").write_text(GOOD)
    derived = result([ADD, NEGATIVE], ["test_legacy.py::test_legacy"])
    not_on_base = {"verdict": "not_run", "said": "old tests not run: the old-code baseline did not pass"}
    runs = Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]),
                                  ("test_calc.py", "test_legacy.py"): derived},
                    on_base={"test_legacy.py": not_on_base})
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]["reasons"]
    assert "derived_test_regressed" not in _reasons(got)
    assert any("test_legacy.py" in unknown for unknown in got["assessment"]["unknowns"])
    assert runs.calls[-1] == ("base", ("test_legacy.py",))
    # survival: the same failure that passed on the base commit is a defect the patch introduced
    regressed = result([], ["test_legacy.py::test_legacy"])
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py", "test_legacy.py"): derived},
             on_base={"test_legacy.py": regressed})
    found = review(repo)
    assert found["assessment"]["outcome"] == "defect_detected"
    assert any(r["stage"] == "tier_2" and r["code"] == "derived_test_regressed" and "test_legacy.py" in r["detail"]
               for r in found["assessment"]["reasons"])


def test_a_failing_derived_file_g6_could_not_run_at_the_base_commit_is_said_so_not_called_pre_existing(repo,
                                                                                                        monkeypatch):
    """Review I6: when the base run never happened, the unknown said the file "did not pass at the base commit
    either"; it says why it did not run there instead."""
    (repo / "test_legacy.py").write_text("import calc\n\n\ndef test_legacy():\n    assert calc.add(1, 1) == 2\n")
    _commit(repo, "legacy")
    (repo / "calc.py").write_text(GOOD)
    never = {"verdict": "not_run", "said": "the repository copy cap of 10000 files was exceeded"}   # no baseline
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]),
                           ("test_calc.py", "test_legacy.py"): result([ADD, NEGATIVE], ["test_legacy.py::test_legacy"])},
             on_base={"test_legacy.py": never})
    unknowns = review(repo)["assessment"]["unknowns"]
    assert not any("did not pass at the base commit" in u for u in unknowns), unknowns
    assert any("test_legacy.py" in u and "could not be run at the base commit" in u and "copy cap" in u
               for u in unknowns), unknowns


@pytest.mark.parametrize("verdict, output", [("failed", "ERROR test_legacy.py\n"),
                                             ("collection_error", "____ ERROR collecting test_legacy.py ____\n")])
def test_a_derived_file_that_fails_to_collect_on_the_patch_and_passes_at_the_base_is_a_defect(repo, monkeypatch,
                                                                                               verdict, output):
    (repo / "test_legacy.py").write_text("import calc\n\n\ndef test_legacy():\n    assert calc.add(1, 1) == 2\n")
    _git(repo, "add", "test_legacy.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "legacy")
    (repo / "calc.py").write_text(GOOD)
    collected_at_base = {"verdict": "not_run", "said": "the tests could not be collected", "baseline": "passed",
                         "patched": verdict}
    runs = Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]),
                                  ("test_calc.py", "test_legacy.py"): result(verdict=verdict, output=output)},
                    on_base={"test_legacy.py": collected_at_base})
    got = review(repo)
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]
    assert any(r["code"] == "derived_test_regressed" and "test_legacy.py" in r["detail"]
               and "collect" in r["detail"] for r in got["assessment"]["reasons"])
    assert runs.calls[-1] == ("base", ("test_legacy.py",))
    # survival: when it did not pass at the base commit either, it stays an unknown
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]),
                           ("test_calc.py", "test_legacy.py"): result(verdict=verdict, output=output)},
             on_base={"test_legacy.py": {"verdict": "not_run", "said": "no", "baseline": "failed"}})
    unknown = review(repo)
    assert unknown["assessment"]["outcome"] == "supported_within_scope"
    assert any("test_legacy.py" in u for u in unknown["assessment"]["unknowns"])


def test_each_run_carries_its_receipt_to_the_scope_and_the_gate(repo, monkeypatch):
    """Recorded runs cite receipts the store does not hold: the gate reads them as unknown, so caller-declared."""
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE], receipt="a" * 64),
                           ("test_calc.py",): result([ADD, NEGATIVE], receipt="b" * 64)})
    got = review(repo)
    receipts = got["assessment"]["scope"]["receipts"]
    assert [(r["tier"], r["receipt"], r["receipt_status"]) for r in receipts] == [(1, "a" * 64, "unknown"),
                                                                                  (2, "b" * 64, "unknown")]
    assert [run["receipt"] for run in got["assessment"]["scope"]["checks_run"]] == ["a" * 64, "b" * 64]
    assert got["gate"]["evidence_basis"] == "caller-declared"
    assert [r["receipt_status"] for r in got["gate"]["receipts"]] == ["unknown", "unknown"]
    assert any("receipt" in unknown for unknown in got["assessment"]["unknowns"])


def test_an_empty_patch_is_insufficient_evidence(repo, monkeypatch):
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo)
    assert "no_change" in _reasons(got)
    assert got["assessment"]["outcome"] == "insufficient_evidence"


# --- the reply's shape ------------------------------------------------------------------------------------------------

def test_the_reply_keeps_the_three_a5_parts_apart_and_merge_needs_separate_approval(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD,): result([ADD]), ("test_calc.py",): result([ADD, NEGATIVE])})
    got = review(repo, [SUM[0], {"id": "R2", "text": "add sums negatives", "checks": []}])
    assessment = got["assessment"]
    assert {"requirements", "requirements_without_adequate_check", "specification_note"} <= set(assessment)
    assert [r["id"] for r in assessment["requirements"]] == ["R1", "R2"]
    assert all(r["text"] and r["status"] and "checks" in r for r in assessment["requirements"])
    assert assessment["requirements_without_adequate_check"] == ["R2"]
    assert "incomplete" in assessment["specification_note"] and "cannot rule" in assessment["specification_note"]
    assert assessment["outcome"] in OUTCOMES and got["completion_state"] == "qualified-draft"
    assert got["merge"]["status"] == "requires_separate_approval" and got["merge"]["reason"]
    assert got["warning_card"]["code"] == "G6_E_PATCH_REVIEW" and got["warning_card"]["suggested_action"]
    assert {"severity", "why", "next_step", "verified"} <= set(got["warning_card"])
    assert got["request_id"] and "run_id" in got and check_envelope(got) == []
    assert assessment["next_actions"] and isinstance(assessment["unknowns"], list)
    # the composition feeds the test run to the coding gate; action gating's merge_patch is never evaluated
    stages = [stage["component"] for stage in got["gate"]["assurance"]["stages"]]
    assert stages == ["patch_gate_adapter", "coding_agent_gate"]
    assert "decision" not in got


def test_only_integrity_class_gate_findings_change_the_outcome(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])})
    plain = review(repo, claim="Fixed addition; all tests pass and it is production ready.")
    assert plain["assessment"]["outcome"] == "supported_within_scope"
    assert plain["gate"]["findings"] and plain["merge"]["reasons"]   # an overclaim is a merge reason, not a defect
    shortcut = review(repo, claim="I skipped the failing test so the suite is green.")
    assert shortcut["assessment"]["outcome"] == "test_integrity_compromised"
    assert "gate_shortcut" in _reasons(shortcut)


def test_grounding_is_reported_and_does_not_decide_the_outcome(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])})
    got = review(repo, claim="Refunds are issued within 30 days.", sources={"spec": "Refunds take 14 days."})
    assert got["assessment"]["outcome"] == "supported_within_scope"
    grounding = got["grounding"]
    assert grounding["status"] == "run" and grounding["decides_outcome"] is False
    assert grounding["completion_state"] == "blocked-escalated"
    assert grounding["sources"]["spec"]["authority"] == "caller-supplied"


# --- refusals: no outcome, never a detection --------------------------------------------------------------------------

def _stored() -> int:
    from g6.workforce import receipt_store

    path = receipt_store.store_path()
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0


def _refused(got, code) -> None:
    assert got["ok"] is False and got["completion_state"] == "blocked-escalated", got
    assert got["warning_card"]["code"] == code and check_envelope(got) == []
    assert "assessment" not in got and "merge" not in got


def _no_boundary(got) -> None:
    """The refusal says how to get a boundary, and that allowing unconfined runs does not help a review."""
    _refused(got, ISOLATION_UNAVAILABLE)
    said = got["warning_card"]["message"]
    assert "bubblewrap" in said and "g6 doctor" in said and "does not change this" in said
    assert "choose it once with g6 setup" not in said


def test_a_stale_digest_is_a_refusal_with_no_outcome(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    runs = Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    stored = _stored()
    _refused(review(repo, expected_artifact_digest="old"), "G6_E_PATCH_STALE")
    assert runs.calls == [] and _stored() == stored


def test_unavailable_confinement_is_a_refusal_with_no_outcome(repo, monkeypatch):
    (repo / "calc.py").write_text(GOOD)
    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off by this test"))
    monkeypatch.delenv(confine.ALLOW, raising=False)
    monkeypatch.setenv(confine.REQUIRE, "1")
    stored = _stored()
    _no_boundary(review(repo))
    monkeypatch.delenv(confine.REQUIRE)
    _no_boundary(review(repo))   # refused by the run itself: nobody allowed unconfined runs
    assert _stored() == stored


def test_an_allowed_unconfined_run_is_a_refusal_too_and_writes_no_receipt(repo, monkeypatch, unconfined):
    (repo / "calc.py").write_text(GOOD)
    monkeypatch.delenv(confine.REQUIRE, raising=False)
    stored = _stored()
    _no_boundary(review(repo))
    assert _stored() == stored


@pytest.mark.parametrize("requirements", [
    "R1: add sums",
    [{"id": "R1", "text": "add sums"}, {"id": "R1", "text": "again", "checks": []}],
    [{"id": "", "text": "add sums", "checks": []}],
    [{"id": "R1", "text": "add sums", "checks": ["../outside.py"]}],
    [{"id": "R1", "text": "add sums", "checks": [], "weight": 2}],
])
def test_invalid_requirements_are_refused(repo, monkeypatch, requirements):
    runs = Recorded(monkeypatch, default=result([ADD]))
    stored = _stored()
    _refused(review(repo, requirements), "G6_E_INPUT_VALIDATION")
    assert runs.calls == [] and _stored() == stored


def test_the_composition_contracts_connect(repo, monkeypatch):
    """Unit test of the joins only; the recorded run is not evidence of confinement."""
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    got = review(repo, claim="Addition returns the sum.")
    assert got["gate"]["assurance"]["status"] == "contract-checked", got["gate"]
    assert len(got["gate"]["assurance"]["checks"]) == 2


def test_the_mcp_tool_takes_requirements_and_describes_the_four_outcomes(repo, monkeypatch):
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, {(ADD, NEGATIVE): result([ADD, NEGATIVE]), ("test_calc.py",): result([ADD, NEGATIVE])})
    server = build_server()

    def data(reply) -> dict:
        return reply.data if isinstance(reply.data, dict) else reply.structured_content

    async def flow():
        tool = (await server.get_tools())["g6_review_patch"]
        async with Client(server) as client:
            job = data(await client.call_tool("g6_review_patch", {"repo": str(repo), "task": "Repair addition.",
                                                                  "requirements": SUM}))
            for _ in range(400):
                got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
                if got.get("status") in ("completed", "failed"):
                    return tool, got
                await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    tool, got = asyncio.run(flow())
    assert "requirements" in tool.parameters["properties"]
    assert all(outcome in tool.description for outcome in OUTCOMES)
    assert got["status"] == "completed", got
    assert got["result"]["assessment"]["outcome"] == "supported_within_scope"
    assert [r["id"] for r in got["result"]["assessment"]["requirements"]] == ["R1", "R2"]


@pytest.mark.parametrize("raised, code", [
    (component_process.ContainmentFailure("cleanup unconfirmed"), "G6_E_PATCH_REVIEW_INTERRUPTED"),
    (component_process.ExecutionInterrupted("allowance ended"), "G6_E_PATCH_REVIEW_INTERRUPTED"),
    (component_process.ExecutionInterrupted("stop", stopped=True), "G6_E_STOPPED"),
])
def test_an_interrupted_review_over_mcp_ends_with_a_code_not_a_traceback(repo, monkeypatch, raised, code):
    """review_patch re-raises these so an enclosing door sees them; the MCP job, which has none, names them."""
    pytest.importorskip("fastmcp")
    import asyncio

    from fastmcp import Client

    from g6.mcp import build_server

    monkeypatch.setenv("G6_MCP_SENSITIVE", "patch_review.review")

    def interrupted(*args, **kwargs):
        raise raised

    monkeypatch.setattr(patch_review, "review_patch", interrupted)
    server = build_server()

    async def flow():
        async with Client(server) as client:
            reply = await client.call_tool("g6_review_patch", {"repo": str(repo), "task": "Repair addition.",
                                                               "requirements": SUM})
            job = reply.data if isinstance(reply.data, dict) else reply.structured_content
            for _ in range(200):
                reply = await client.call_tool("metaharness_job", {"job_id": job["job_id"]})
                got = reply.data if isinstance(reply.data, dict) else reply.structured_content
                if got.get("status") in ("completed", "failed"):
                    return got
                await asyncio.sleep(0.05)
        raise AssertionError("the review job did not finish")

    got = asyncio.run(flow())
    assert got["status"] == "completed", got
    assert got["result"]["warning_card"]["code"] == code
    assert got["result"]["completion_state"] == "blocked-escalated" and "Traceback" not in str(got)


# --- the engineering trial's counts -----------------------------------------------------------------------------------

def _trial():
    path = Path(__file__).resolve().parents[2] / "validation" / "patch_review" / "run.py"
    spec = importlib.util.spec_from_file_location("patch_review_trial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_trial_counts_acceptances_from_the_outcome(repo, monkeypatch):
    trial = _trial()
    (repo / "calc.py").write_text(GOOD)
    Recorded(monkeypatch, default=result([ADD, NEGATIVE]))
    supported = review(repo)
    refused = review(repo, expected_artifact_digest="old")
    rows = [trial.row("correct", True, supported, 0.1), trial.row("wrong", False, supported, 0.1),
            trial.row("stale", False, refused, 0.1)]
    assert rows[0]["g6_outcome"] == "supported_within_scope" and rows[2]["g6_outcome"] is None
    assert rows[0]["checks_run"] and rows[0]["cost"]["checks"] == 2 and rows[0]["reason_codes"] == []
    counts = trial.summarise(rows)
    assert (counts["g6_acceptances"], counts["g6_correct_acceptances"], counts["g6_false_acceptances"]) == (2, 1, 1)
    assert (counts["g6_refusals"], counts["g6_tool_errors"], counts["g6_detections"]) == (1, 0, 0)
    fewer = trial.summarise(rows[2:] + [trial.row("crash", True, None, 0.1, error="boom")])
    assert (fewer["g6_acceptances"], fewer["g6_tool_errors"]) == (0, 1)


# --- real confined runs (Linux bubblewrap; run in Docker on Windows) ---------------------------------------------------

def test_confined_a_correct_patch_is_supported_and_a_defect_is_named(repo, bwrap_backend):
    (repo / "calc.py").write_text(GOOD)
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    assert got["assessment"]["scope"]["environment"]["isolation"]["backend"] == confine.BUBBLEWRAP
    (repo / "calc.py").write_text(POSITIVE_ONLY)
    found = review(repo)
    assert found["assessment"]["outcome"] == "defect_detected"
    assert _requirement(found, "R2")["status"] == "failed" and _requirement(found, "R1")["status"] != "failed"


def test_confined_receipts_make_the_gate_g6_observed_and_a_forged_one_does_not(repo, bwrap_backend, monkeypatch):
    from g6.workforce import receipt_store

    (repo / "calc.py").write_text(GOOD)
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    receipts = got["assessment"]["scope"]["receipts"]
    assert [r["tier"] for r in receipts] == [1, 2] and all(r["receipt_status"] == "matched" for r in receipts)
    assert all(r["receipt_status"] == "matched" for r in got["gate"]["receipts"])   # it survived the composition
    assert got["gate"]["evidence_basis"] == "g6-observed"
    for found in receipts:
        assert receipt_store.check(found["receipt"], artifact_digest=got["artifact_digest"])[0] == "matched"
    real = patch_review._execute

    def forged(*args, **kwargs):
        ran = real(*args, **kwargs)
        return {**ran, "receipt": "0" * 64} if "receipt" in ran else ran

    monkeypatch.setattr(patch_review, "_execute", forged)
    faked = review(repo)
    assert [r["receipt_status"] for r in faked["gate"]["receipts"]] == ["unknown", "unknown"]
    assert faked["gate"]["evidence_basis"] == "caller-declared"


HOOK = ("import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\ndef pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n    rep = outcome.get_result()\n    if rep.failed:\n"
        "        rep.outcome = 'passed'\n        rep.longrepr = None\n")


def test_confined_a_conftest_hidden_from_git_compromises_integrity_and_gets_no_receipt(repo, bwrap_backend):
    """I2, the reviewer's probe: the buggy tree with a conftest.py that turns failures into passes, hidden from git's
    view through .git/info/exclude, so the artifact digest is the same as without it."""
    from g6.core.ledger import digest
    from g6.workforce import repo_tests

    (repo / "calc.py").write_text(BAD + "\n")   # a change, still the bug
    clean = digest(repo_tests.snapshot(repo))
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "exclude").write_text("conftest.py\n")
    (repo / "conftest.py").write_text(HOOK)
    assert digest(repo_tests.snapshot(repo)) == clean   # the hidden file is outside the digest
    got = review(repo)
    assert got["assessment"]["outcome"] == "test_integrity_compromised", got["assessment"]
    assert any(r["code"] == "ignored_file_can_change_tests" and "conftest.py" in r["detail"]
               for r in got["assessment"]["reasons"])
    assert all(r["receipt"] == "" for r in got["assessment"]["scope"]["receipts"])
    assert got["gate"]["evidence_basis"] != "g6-observed"


def test_confined_an_ignored_version_file_is_bound_into_the_receipt_and_the_patch_stays_supported(repo,
                                                                                                   bwrap_backend):
    import hashlib

    from g6.workforce import receipt_store

    (repo / "calc.py").write_text(GOOD)
    _ignore(repo, "_version.py", "version = '1.0'\n")
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    assert "an ignored file G6 did not judge: _version.py" in got["assessment"]["unknowns"]
    [first, *_] = got["assessment"]["scope"]["receipts"]
    assert first["receipt_status"] == "matched"
    body = receipt_store.lookup(first["receipt"], artifact_digest=got["artifact_digest"])
    assert body["ignored_inputs"] == {"_version.py": hashlib.sha256((repo / "_version.py").read_bytes()).hexdigest()}


def test_confined_an_environment_collection_failure_is_not_a_defect(repo, bwrap_backend):
    (repo / "test_env.py").write_text("import g6_dependency_absent_for_this_test\n\n\ndef test_env():\n    assert True\n")
    _git(repo, "add", "test_env.py")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "a test that needs a dependency this environment lacks")
    (repo / "calc.py").write_text(GOOD)
    got = review(repo, [*SUM, {"id": "R3", "text": "it runs where the dependency is", "checks": ["test_env.py"]}])
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]
    third = _requirement(got, "R3")
    assert third["status"] == "not_established" and "could not be collected in this environment" in third["reason"]


def test_confined_a_derived_file_the_patch_stops_collecting_is_a_defect(repo, bwrap_backend):
    (repo / "helpers.py").write_text("def double(x):\n    return 2 * x\n")
    (repo / "test_helpers.py").write_text("from helpers import double\n\n\ndef test_double():\n"
                                          "    assert double(2) == 4\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "helpers")
    (repo / "calc.py").write_text(GOOD)
    (repo / "helpers.py").write_text("def double(x)\n    return 2 * x\n")   # a syntax error
    got = review(repo)
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]
    assert any(r["code"] == "derived_test_regressed" and "test_helpers.py" in r["detail"]
               for r in got["assessment"]["reasons"])


def test_confined_a_failing_test_moved_out_of_the_stated_folder_is_a_regression(repo, bwrap_backend):
    """Review I6 (probe S4): the patch breaks negative sums and moves the test that shows it under a dot folder, which
    pytest's walk of tests/ skips; the stated check names the folder."""
    _git(repo, "rm", "-q", "test_calc.py")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pos.py").write_text("from calc import add\n\n\ndef test_pos():\n    assert add(2, 3) == 5\n")
    (repo / "tests" / "test_neg.py").write_text("from calc import add\n\n\ndef test_neg():\n"
                                                "    assert add(-2, -3) == -5\n")
    (repo / "calc.py").write_text(GOOD)
    _commit(repo, "tests in a folder; add sums")
    (repo / "tests" / ".off").mkdir()
    _git(repo, "mv", "tests/test_neg.py", "tests/.off/test_neg.py")
    (repo / "calc.py").write_text("def add(a, b):\n    return abs(a + b)\n")
    got = review(repo, [{"id": "R1", "text": "add sums positive integers", "checks": ["tests"]}])
    assert got["assessment"]["outcome"] == "defect_detected", got["assessment"]
    assert any(r["code"] == "derived_test_regressed" and "tests/.off/test_neg.py" in r["detail"]
               for r in got["assessment"]["reasons"])


def test_confined_a_test_folder_with_its_own_pytest_config_is_read_against_that_root(repo, bwrap_backend):
    _git(repo, "rm", "-q", "test_calc.py")
    (repo / "tests").mkdir()
    (repo / "tests" / "pytest.ini").write_text("[pytest]\n")
    (repo / "tests" / "test_calc.py").write_text(TEST)
    _commit(repo, "tests in a folder with their own pytest.ini")
    (repo / "calc.py").write_text(GOOD)
    ids = ["tests/test_calc.py::test_add", "tests/test_calc.py::test_negative"]
    got = review(repo, [{**req, "checks": [ident]} for req, ident in zip(SUM, ids)])
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    (repo / "calc.py").write_text(POSITIVE_ONLY)   # survival: a failure is still named under that root
    found = review(repo, [{**req, "checks": [ident]} for req, ident in zip(SUM, ids)])
    assert found["assessment"]["outcome"] == "defect_detected", found["assessment"]
    assert _requirement(found, "R2")["status"] == "failed"


def test_confined_a_check_pytest_cannot_find_is_not_collected(repo, bwrap_backend):
    (repo / "calc.py").write_text(GOOD)
    got = review(repo, [*SUM, {"id": "R3", "text": "add handles zero", "checks": ["test_calc.py::test_zero"]}])
    assert got["assessment"]["outcome"] == "insufficient_evidence", got["assessment"]
    assert _requirement(got, "R3")["checks"][0]["verdict"] == "not_collected"
    assert "defect_detected" != got["assessment"]["outcome"]


def test_confined_a_derived_regression_is_a_defect_and_a_pre_existing_failure_is_not(repo, bwrap_backend):
    (repo / "test_legacy.py").write_text("import calc\n\n\ndef test_legacy():\n    assert calc.add(1, 1) == 3\n")
    (repo / "test_zero.py").write_text("import calc\n\n\ndef test_zero():\n    assert calc.add(0, 0) == 0\n")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "commit", "-qm", "more tests")
    (repo / "calc.py").write_text(GOOD)
    got = review(repo)
    assert got["assessment"]["outcome"] == "supported_within_scope", got["assessment"]
    assert any("test_legacy.py" in unknown for unknown in got["assessment"]["unknowns"])
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + (1 if a == b == 0 else 0)\n")
    found = review(repo)
    assert found["assessment"]["outcome"] == "defect_detected", found["assessment"]
    assert any(r["code"] == "derived_test_regressed" and "test_zero.py" in r["detail"]
               for r in found["assessment"]["reasons"])
