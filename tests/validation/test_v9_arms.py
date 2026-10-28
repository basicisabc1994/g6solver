# SPDX-License-Identifier: Apache-2.0
"""V9's arms 1, 3 and 4 (PROTOCOL.md, "Arms"), each mapped to accepted or not accepted.

Development fixtures only: every repository and patch is a tiny one made here; none is V9 evaluation data. Arm 1 runs
pytest on these fixtures through the study's executor. Arms 3 and 4 run G6's confined test runs only in the tests
that take ``bwrap_backend`` (Linux bubblewrap; they skip elsewhere); the others replace those runs with recorded
results, which say nothing about confinement."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from g6.core import component_process
from g6.harness import patch_review
from g6.workforce import repo_tests
from patch_review_utility import arms, contract, executor

BAD = "def add(a, b):\n    return a - b\n"
GOOD = "def add(a, b):\n    return a + b\n"
TEST = ("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\n"
        "def test_negative():\n    assert add(-2, -3) == -5\n")
MORE = "from calc import add\n\n\ndef test_more():\n    assert add(1, 1) == 2\n"
CONTRACT = contract.split("Addition returns the sum.", "- The sum of two positive integers.\n"
                                                       "- The sum of two negative integers.")
MORE_ID, ADD, NEGATIVE = "test_more.py::test_more", "test_calc.py::test_add", "test_calc.py::test_negative"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BAD, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture")
    return root


def patch(root: Path, files: dict[str, str]) -> list[str]:
    """Write the patch into the working tree; the test files it adds or changes, which every requirement cites."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return contract.patch_tests(list(files))


def result(passed=(), failed=(), skipped=(), *, verdict=None, backend="synthetic-unit-fixture", network=False):
    """One recorded ``repo_tests.run`` result."""
    outcomes = {**{n: {"setup": "passed", "call": "passed", "teardown": "passed"} for n in passed},
                **{n: {"setup": "passed", "call": "failed", "teardown": "passed"} for n in failed},
                **{n: {"setup": "skipped", "teardown": "passed"} for n in skipped}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": len(skipped),
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.2,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": backend, "why": "", "network": network}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": ""}


class Runs:
    """Recorded test runs for both arms: in place by the ids asked for (or ``default``); at the base commit by file."""

    def __init__(self, in_place=None, *, default=None, on_base=None):
        self.in_place, self.default, self.on_base, self.calls = dict(in_place or {}), default, dict(on_base or {}), []

    def run(self, repo, tests, *, timeout_s, receipt=False):
        self.calls.append(("in_place", tuple(tests)))
        found = self.in_place.get(tuple(tests), self.default)
        assert found is not None, f"no recorded result for {tests}"
        return found

    def run_before(self, change, *, timeout_s):
        self.calls.append(("base", tuple(sorted(change.before))))
        return {rel: self.on_base[rel] for rel in change.before}

    def into(self, monkeypatch):
        """Stand in for G6's review's runs too (``patch_review._execute``)."""
        def execute(root, ids, *, timeout_s, change=None):
            if change is None:
                return self.run(root, ids, timeout_s=timeout_s)
            self.calls.append(("base", tuple(ids)))
            return {rel: self.on_base[rel] for rel in ids}
        monkeypatch.setattr(patch_review, "_execute", execute)
        return self


def g6(root, checks, *, timeout_s=300, **kwargs):
    return arms.g6_review(root, CONTRACT, checks, timeout_s=timeout_s, **kwargs)


def direct(root, checks, runs, *, timeout_s=300):
    return arms.direct_checks(root, CONTRACT, checks, timeout_s=timeout_s, run=runs.run, run_before=runs.run_before)


# --- arm 3, G6's review ------------------------------------------------------------------------------------------------

def test_g6_accepts_only_supported_within_scope_with_every_requirement_passed(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}).into(monkeypatch)
    got = g6(repo, checks)
    assert got["disposition"]["category"] == "accept" and got["disposition"]["accepted"] is True
    assert got["row"]["g6_outcome"] == "supported_within_scope" and got["a5"]["complete"] is True
    assert got["disposition"]["cost"]["model_calls"] == 0
    assert got["trace"]["isolation"] == [{"backend": "synthetic-unit-fixture", "why": "", "network": False}]
    assert got["trace"]["commands"] == ["pytest test_more.py", "pytest test_calc.py"]


@pytest.mark.parametrize("files, runs, category", [
    ({"calc.py": BAD + "\n", "test_more.py": MORE}, {("test_more.py",): result(failed=[MORE_ID])}, "defect_found"),
    ({"calc.py": GOOD, "test_calc.py": "def test_add():\n    assert True\n"}, None, "test_integrity_compromised"),
    ({"calc.py": GOOD}, None, "insufficient_evidence"),
])
def test_g6s_other_outcomes_are_not_accepted_and_tallied_apart(repo, monkeypatch, files, runs, category):
    checks = patch(repo, files)
    Runs(runs, default=result([ADD, NEGATIVE, MORE_ID])).into(monkeypatch)
    found = g6(repo, checks)["disposition"]
    assert found["category"] == category and found["accepted"] is False


def test_a_refusal_an_exception_an_incomplete_report_or_a_late_review_is_never_acceptance(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    Runs(default=result([MORE_ID, ADD, NEGATIVE], backend=None)).into(monkeypatch)
    assert g6(repo, checks)["disposition"]["error_kind"] == "refusal"        # no boundary confined the run

    def broken(*args, **kwargs):
        raise RuntimeError("the review crashed")
    crashed = g6(repo, checks, review=broken)
    assert crashed["disposition"]["error_kind"] == "tool_error" and "crashed" in crashed["disposition"]["detail"]

    Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}).into(monkeypatch)

    def no_note(*args, **kwargs):
        reply = patch_review.review_patch(*args, **kwargs)
        reply["assessment"].pop("specification_note")
        return reply
    incomplete = g6(repo, checks, review=no_note)["disposition"]
    assert incomplete["error_kind"] == "a5_report_incomplete" and "specification_note" in incomplete["detail"]

    def slow(*args, **kwargs):
        time.sleep(1.2)
        return patch_review.review_patch(*args, **kwargs)
    late = g6(repo, checks, review=slow, timeout_s=1)["disposition"]      # a reply, after the limit: an abstention
    assert (late["category"], late["time_or_cap_reached"]) == ("insufficient_evidence", True)


def test_a_review_stopped_at_its_wall_clock_limit_without_a_reply_is_a_tool_error(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})

    def hangs(*args, **kwargs):
        time.sleep(1.2)
        component_process.checkpoint()          # where G6's own runs check the enclosing allowance
        raise AssertionError("the allowance should have ended the review")
    found = g6(repo, checks, review=hangs, timeout_s=1, kill_after_s=1)["disposition"]
    assert (found["category"], found["error_kind"], found["time_or_cap_reached"]) == (
        "tool_error_or_refusal", "killed_at_time_limit", True)


def test_a_reply_that_says_g6_ran_out_of_time_is_tallied_as_time_reached(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    Runs(default=result(verdict="timed_out")).into(monkeypatch)
    found = g6(repo, checks)["disposition"]
    assert found["accepted"] is False and found["time_or_cap_reached"] is True and found["error_kind"] == ""


def test_g6s_model_calls_come_from_its_reply_when_it_reports_them(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}).into(monkeypatch)
    assert g6(repo, checks)["model_calls"] == {"count": 0, "source": arms.MODEL_CALLS_NOT_REPORTED}

    def reporting(*args, **kwargs):
        reply = patch_review.review_patch(*args, **kwargs)
        reply["assessment"]["completion"] = {"activities": [{"kind": "proposer", "model_calls": 2},
                                                            {"kind": "replay"}, {"kind": "judge", "model_calls": 1}]}
        return reply
    got = g6(repo, checks, review=reporting)
    assert got["model_calls"] == {"count": 3, "source": "assessment.completion.activities"}
    assert got["disposition"]["cost"]["model_calls"] == 3


# --- arm 4, the same checks without G6 ------------------------------------------------------------------------------------

def test_the_direct_checks_accept_when_every_check_passes(repo):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    runs = Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])})
    got = direct(repo, checks, runs)
    assert got["disposition"]["accepted"] is True and runs.calls == [("in_place", ("test_more.py",)),
                                                                     ("in_place", ("test_calc.py",))]
    assert [c["name"] for c in got["checks"]] == list(arms.DIRECT_CHECKS)
    assert all(c["passed"] for c in got["checks"])


@pytest.mark.parametrize("files, runs, category, failing", [
    ({"calc.py": BAD + "\n", "test_more.py": MORE}, {("test_more.py",): result(failed=[MORE_ID])}, "defect_found",
     "stated_checks"),
    ({"calc.py": GOOD, "test_calc.py": "def test_add():\n    assert True\n"}, None, "test_integrity_compromised",
     "test_weakening"),
    ({"calc.py": GOOD, "pytest.ini": "[pytest]\naddopts = -q\n"}, None, "test_integrity_compromised",
     "runner_configuration"),
    ({"calc.py": GOOD, "test_more.py": MORE}, {("test_more.py",): result([MORE_ID], verdict="changed_tracked_files")},
     "test_integrity_compromised", "stated_checks"),
    ({"calc.py": GOOD, "test_more.py": MORE}, {("test_more.py",): result(skipped=[MORE_ID])},
     "insufficient_evidence", "stated_checks"),
    ({"calc.py": GOOD, "test_more.py": MORE}, {("test_more.py",): result([MORE_ID]),
                                              ("test_calc.py",): result([ADD], [NEGATIVE])},
     "defect_found", "tests_touching_changed_modules"),
])
def test_a_check_that_does_not_pass_means_not_accepted(repo, files, runs, category, failing):
    checks = patch(repo, files)
    got = direct(repo, checks, Runs(runs, default=result([ADD, NEGATIVE, MORE_ID]),
                                    on_base={"test_calc.py": result([ADD, NEGATIVE])}))
    assert got["disposition"]["category"] == category and got["disposition"]["accepted"] is False
    assert failing in [c["name"] for c in got["checks"] if not c["passed"]]


def test_a_git_ignored_file_pytest_could_load_fails_the_direct_checks(repo):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE, ".gitignore": "local/\n",
                          "local/conftest.py": "import pytest\n"})
    got = direct(repo, checks, Runs(default=result([MORE_ID, ADD, NEGATIVE])))
    assert [c["name"] for c in got["checks"] if c["passed"] is False] == ["ignored_inputs"]


def test_an_unconfined_or_refused_run_is_a_refusal_never_a_result(repo):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    assert direct(repo, checks, Runs(default=result([MORE_ID], backend=None)))["disposition"]["error_kind"] == "refusal"

    class Refusing(Runs):
        def run(self, repo, tests, *, timeout_s, receipt=False):
            raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "no boundary")
    assert direct(repo, checks, Refusing())["disposition"]["error_kind"] == "refusal"


def test_direct_checks_stopped_at_their_limit_are_a_tool_error_and_a_timed_out_run_is_time_reached(repo):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})

    class Hanging(Runs):
        def run(self, repo, tests, *, timeout_s, receipt=False):
            time.sleep(1.2)
            component_process.checkpoint()
            raise AssertionError("the allowance should have ended the run")
    hung = Hanging()
    found = arms.direct_checks(repo, CONTRACT, checks, timeout_s=1, kill_after_s=1, run=hung.run,
                               run_before=hung.run_before)["disposition"]
    assert (found["error_kind"], found["time_or_cap_reached"]) == ("killed_at_time_limit", True)
    slow = direct(repo, checks, Runs(default=result(verdict="timed_out")))["disposition"]
    assert slow["accepted"] is False and slow["time_or_cap_reached"] is True and slow["error_kind"] == ""


# --- what arm 4 isolates: the parity of arms 3 and 4, and their two designed differences -----------------------------------

@pytest.mark.parametrize("files, in_place, on_base, g6_accepts, direct_accepts", [
    ({"calc.py": GOOD, "test_more.py": MORE}, {("test_more.py",): result([MORE_ID]),
                                              ("test_calc.py",): result([ADD, NEGATIVE])}, None, True, True),
    ({"calc.py": BAD + "\n", "test_more.py": MORE}, {("test_more.py",): result(failed=[MORE_ID])}, None, False, False),
    ({"calc.py": GOOD, "test_calc.py": "def test_add():\n    assert True\n"}, None, None, False, False),
    ({"calc.py": GOOD, "pytest.ini": "[pytest]\n"}, None, None, False, False),
    # G6 asks for a stated check; the direct checks have nothing to run for the requirements, so all of them pass
    ({"calc.py": GOOD}, {("test_calc.py",): result([ADD, NEGATIVE])}, None, False, True),
    # a test that touches the changed module fails on the patch and at the base: G6 leaves it as an unknown
    ({"calc.py": GOOD, "test_more.py": MORE}, {("test_more.py",): result([MORE_ID]),
                                              ("test_calc.py",): result([ADD], [NEGATIVE])},
     {"test_calc.py": {"verdict": "not_run", "said": "it failed at the base too", "baseline": "failed"}}, True, False),
])
def test_arm_4_runs_g6s_checks_and_differs_only_where_g6s_assessment_logic_decides(repo, monkeypatch, files, in_place,
                                                                                  on_base, g6_accepts, direct_accepts):
    checks = patch(repo, files)
    runs = Runs(in_place, default=result([ADD, NEGATIVE, MORE_ID]), on_base=on_base).into(monkeypatch)
    assert g6(repo, checks)["disposition"]["accepted"] is g6_accepts
    assert direct(repo, checks, runs)["disposition"]["accepted"] is direct_accepts


# --- arm 1, tests and continuous integration -----------------------------------------------------------------------------

class Scripted:
    """Arm 1's executor with set outcomes: ``base`` and ``patched`` lists, one mapping per run (or "broken")."""

    def __init__(self, base, patched):
        self.script = {"base": list(base), "patched": list(patched)}
        self.calls = []

    def run(self, tree, tests, *, timeout_s, overlay=None):
        self.calls.append((Path(tree).name, tuple(tests), timeout_s))
        step = self.script[Path(tree).name].pop(0)
        if step == "timeout":
            return {"outcomes": {}, "collection_errors": [], "exit": None, "seconds": timeout_s, "timed_out": True,
                    "error": ""}
        broken = step == "broken"
        return {"outcomes": {} if broken else dict(step), "collection_errors": ["x.py"] if broken else [],
                "exit": 2 if broken else 0, "seconds": 1.0, "timed_out": False, "error": ""}


OLD, NEW, FLAKY = "tests/test_old.py::test_old", "tests/test_new.py::test_new", "tests/test_old.py::test_flaky"


def run_ci(base, patched, patch_tests=("tests/test_new.py",)):
    run = Scripted(base, patched)
    return arms.tests_and_ci(Path("base"), Path("patched"), suite=["tests"], patch_tests=list(patch_tests),
                             executor=run, timeout_s=3600), run


def test_ci_accepts_with_no_regression_and_every_added_test_passing_and_leaves_out_flaky_base_tests():
    base = [{OLD: "passed", FLAKY: "passed"}, {OLD: "passed", FLAKY: "failed"}, {OLD: "passed", FLAKY: "passed"}]
    patched = [{OLD: "passed", NEW: "passed", FLAKY: "failed"}] * 3
    got, run = run_ci(base, patched)
    assert got["disposition"]["accepted"] is True and got["base_flaky"] == [FLAKY]
    assert run.calls[0] == ("base", ("tests",), 3600) and run.calls[3] == ("patched", ("tests", "tests/test_new.py"),
                                                                          3600)


@pytest.mark.parametrize("patched, category, field", [
    ([{OLD: "failed", NEW: "passed"}, {OLD: "failed", NEW: "passed"}, {OLD: "passed", NEW: "passed"}],
     "defect_found", "regressions"),
    ([{OLD: "passed", NEW: "skipped"}] * 3, "defect_found", "added_not_passing"),
    (["broken", "broken", {OLD: "passed", NEW: "passed"}], "defect_found", "unrunnable"),
])
def test_ci_rejects_a_regression_an_added_test_that_does_not_pass_or_a_suite_that_cannot_run(patched, category,
                                                                                               field):
    got, _ = run_ci([{OLD: "passed"}] * 3, patched)
    assert got["disposition"]["category"] == category and got[field]


def test_a_base_suite_that_cannot_run_is_a_tool_error():
    got, _ = run_ci([{OLD: "passed"}, "broken", {OLD: "passed"}], [{OLD: "passed", NEW: "passed"}] * 3)
    assert got["disposition"]["error_kind"] == "tool_error"
    assert got["disposition"]["time_or_cap_reached"] is False


def test_a_base_suite_run_killed_at_its_limit_is_a_tool_error_tallied_as_time_reached():
    got, _ = run_ci([{OLD: "passed"}, "timeout", {OLD: "passed"}], [{OLD: "passed", NEW: "passed"}] * 3)
    found = got["disposition"]
    assert (found["category"], found["error_kind"], found["time_or_cap_reached"]) == (
        "tool_error_or_refusal", "killed_at_time_limit", True)


def test_a_patched_suite_killed_at_its_limit_after_the_base_completed_is_a_defect_the_patch_caused():
    got, _ = run_ci([{OLD: "passed"}] * 3, ["timeout", "timeout", {OLD: "passed", NEW: "passed"}])
    found = got["disposition"]
    assert (found["category"], found["error_kind"], found["time_or_cap_reached"]) == ("defect_found", "", True)
    one, _ = run_ci([{OLD: "passed"}] * 3, ["timeout", {OLD: "passed", NEW: "passed"}, {OLD: "passed", NEW: "passed"}])
    assert one["disposition"]["accepted"] is True                       # one killed run of three decides nothing


# --- arms 3 and 4 in their own process --------------------------------------------------------------------------------

FIXTURE_ARMS = """\
import os, pathlib, subprocess, sys, time


def hangs(tree, requirements, checks, *, timeout_s):
    beat = pathlib.Path(tree, "heartbeat")
    subprocess.Popen([sys.executable, "-c", "import pathlib, time\\nwhile True:\\n"
                      f"    pathlib.Path({str(beat)!r}).write_text(str(time.time()))\\n    time.sleep(0.2)\\n"])
    while True:
        time.sleep(1)


def crashes(tree, requirements, checks, *, timeout_s):
    os._exit(3)


def replies(tree, requirements, checks, *, timeout_s):
    return {"disposition": {"accepted": True, "category": "accept", "error_kind": "", "time_or_cap_reached": False,
                            "detail": "", "cost": {"seconds": 0.0, "processor_seconds": 0.0, "model_calls": 0}},
            "trace": {"commands": [], "isolation": []},
            "seen": {"requirements": requirements, "checks": checks, "timeout_s": timeout_s,
                     "research_flag": os.environ.get("G6_ECHA_RESEARCH"), "isolated": sys.flags.isolated}}
"""


@pytest.fixture
def fixture_arms(tmp_path):
    (tmp_path / "v9_fixture_arms.py").write_text(FIXTURE_ARMS, encoding="utf-8")
    return tmp_path


def test_an_arm_in_its_own_isolated_process_replies_without_the_research_flag(fixture_arms, monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    got = arms.in_child("v9_fixture_arms:replies", fixture_arms, CONTRACT, ["test_more.py"], timeout_s=30,
                        roots=[fixture_arms])
    assert got["disposition"]["accepted"] is True and got["child"]["exit_code"] == 0
    assert got["seen"] == {"requirements": CONTRACT, "checks": ["test_more.py"], "timeout_s": 30,
                           "research_flag": None, "isolated": 1}


def test_an_arm_process_that_ends_with_no_reply_is_a_tool_error(fixture_arms):
    got = arms.in_child("v9_fixture_arms:crashes", fixture_arms, CONTRACT, [], timeout_s=30, roots=[fixture_arms])
    found = got["disposition"]
    assert (found["error_kind"], found["time_or_cap_reached"]) == ("tool_error", False) and "exit code 3" in \
        found["detail"]


def test_an_arm_that_never_returns_is_killed_with_its_whole_process_tree_at_its_limit(fixture_arms):
    started = time.monotonic()
    got = arms.in_child("v9_fixture_arms:hangs", fixture_arms, CONTRACT, [], timeout_s=1, kill_after_s=8,
                        roots=[fixture_arms])
    assert time.monotonic() - started < 30
    found = got["disposition"]
    assert (found["category"], found["error_kind"], found["time_or_cap_reached"]) == (
        "tool_error_or_refusal", "killed_at_time_limit", True)
    beat = fixture_arms / "heartbeat"
    assert beat.is_file()                                               # the grandchild did start
    before = beat.read_text(encoding="utf-8")
    time.sleep(1.5)
    assert beat.read_text(encoding="utf-8") == before                   # and died with the tree


def test_the_study_runs_arms_3_and_4_only_through_their_own_process():
    assert set(arms.CHILD_ARMS) == {"g6", "direct_checks"}
    assert arms.CHILD_ARMS["g6"] is arms.g6_review and arms.CHILD_ARMS["direct_checks"] is arms.direct_checks


def test_ci_end_to_end_with_the_real_executor(repo, tmp_path):
    subprocess.run(["git", "clone", "-q", str(repo), str(tmp_path / "base")], check=True)
    subprocess.run(["git", "clone", "-q", str(repo), str(tmp_path / "patched")], check=True)
    checks = patch(tmp_path / "patched", {"calc.py": GOOD, "test_more.py": MORE})
    got = arms.tests_and_ci(tmp_path / "base", tmp_path / "patched", suite=["test_calc.py"], patch_tests=checks,
                            executor=executor.Executor(), timeout_s=300)
    assert got["disposition"]["accepted"] is True and got["base_passing"] == 0      # the base fails both
    broken = tmp_path / "broken"
    subprocess.run(["git", "clone", "-q", str(repo), str(broken)], check=True)
    patch(broken, {"calc.py": "def add(a, b):\n    return a +\n"})
    got = arms.tests_and_ci(tmp_path / "base", broken, suite=["test_calc.py"], patch_tests=[],
                            executor=executor.Executor(), timeout_s=300)
    assert got["disposition"]["category"] == "defect_found" and got["unrunnable"]


# --- confined runs: arms 3 and 4 with G6's real test runs (Linux bubblewrap) ---------------------------------------------

def test_confined_g6_and_direct_checks_accept_the_correct_patch_and_reject_the_unfixed_one(bwrap_backend, repo,
                                                                                          tmp_path):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    assert g6(repo, checks)["disposition"]["accepted"] is True
    assert arms.direct_checks(repo, CONTRACT, checks, timeout_s=300)["disposition"]["accepted"] is True
    (repo / "calc.py").write_text("def add(a, b):\n    return b - a\n", encoding="utf-8")
    assert g6(repo, checks)["disposition"]["category"] == "defect_found"
    assert arms.direct_checks(repo, CONTRACT, checks, timeout_s=300)["disposition"]["category"] == "defect_found"
