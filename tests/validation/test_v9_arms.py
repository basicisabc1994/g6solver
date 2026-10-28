# SPDX-License-Identifier: Apache-2.0
"""V9's arms 1, 3 and 4 (PROTOCOL.md, "Arms"), each mapped to accepted or not accepted.

Development fixtures only: every repository and patch is a tiny one made here; none is V9 evaluation data. Arm 1 runs
pytest on these fixtures through the study's executor. Arms 3 and 4 run G6's confined test runs only in the tests
that take ``bwrap_backend`` (Linux bubblewrap; they skip elsewhere); the others replace those runs with recorded
results, which say nothing about confinement."""
from __future__ import annotations

import inspect
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
HELPER = "def assert_sum(got, want):\n    assert got == want\n"
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
    (root / "tests").mkdir()
    (root / "tests" / "helpers.py").write_text(HELPER, encoding="utf-8")   # a helper the tests could check through
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture")
    return root


def patch(root: Path, files: dict[str, str | None]) -> list[str]:
    """Write the patch into the working tree (``None`` deletes a file); the test files it adds or changes that are on
    the patched tree, which every requirement cites."""
    for rel, text in files.items():
        if text is None:
            (root / rel).unlink()
            continue
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return contract.patch_tests([rel for rel, text in files.items() if text is not None])


def result(passed=(), failed=(), skipped=(), *, verdict=None, backend="synthetic-unit-fixture", network=False,
           source_before="", rootdir=""):
    """One recorded ``repo_tests.run`` result; ``source_before`` "" leaves the digest unreported."""
    outcomes = {**{n: {"setup": "passed", "call": "passed", "teardown": "passed"} for n in passed},
                **{n: {"setup": "passed", "call": "failed", "teardown": "passed"} for n in failed},
                **{n: {"setup": "skipped", "teardown": "passed"} for n in skipped}}
    verdict = verdict or ("failed" if failed else "passed")
    return {"verdict": verdict, "said": "", "passed": len(passed), "failed": len(failed), "not_passed": len(skipped),
            "total": len(passed) + len(failed), "deselected": 0, "test_outcomes": outcomes,
            "collected_ids": sorted(outcomes), "omitted_ids": [], "changed": [], "output": "", "seconds": 0.2,
            "python": sys.executable, "interpreter_source": "g6", "command": "pytest",
            "isolation": {"backend": backend, "why": "", "network": network}, "failed_ids": list(failed),
            "setup_errors": {}, "degraded": verdict not in ("passed", "failed"), "receipt": "",
            "source_before": source_before, "rootdir": rootdir}


#: What G6's run of the passing stated checks against the base commit's code reports when a test records none.
NOT_AT_BASE = {"verdict": "not_run", "said": "not recorded by this test"}


class Runs:
    """Recorded test runs for both arms: in place by the ids asked for (or ``default``); at the base commit by file;
    and, for G6's review only, the stated checks against the base commit's code (``at_base``)."""

    def __init__(self, in_place=None, *, default=None, on_base=None, at_base=None):
        self.in_place, self.default, self.on_base, self.calls = dict(in_place or {}), default, dict(on_base or {}), []
        self.at_base = at_base or NOT_AT_BASE

    def run(self, repo, tests, *, timeout_s, receipt=False):
        self.calls.append(("in_place", tuple(tests)))
        found = self.in_place.get(tuple(tests), self.default)
        assert found is not None, f"no recorded result for {tests}"
        return found

    def run_before(self, change, *, timeout_s):
        self.calls.append(("base", tuple(sorted(change.before))))
        return {rel: self.on_base[rel] for rel in change.before}

    def into(self, monkeypatch):
        """Stand in for G6's review's runs too (``patch_review._execute`` and ``_execute_at_base``)."""
        def execute(root, ids, *, timeout_s, change=None):
            if change is None:
                return self.run(root, ids, timeout_s=timeout_s)
            self.calls.append(("base", tuple(ids)))
            return {rel: self.on_base[rel] for rel in ids}

        def at_base(change, ids, *, timeout_s):
            self.calls.append(("at_base", tuple(ids)))
            return self.at_base
        monkeypatch.setattr(patch_review, "_execute", execute)
        monkeypatch.setattr(patch_review, "_execute_at_base", at_base)
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
    got = g6(repo, checks)
    found = got["disposition"]
    assert found["accepted"] is False and found["time_or_cap_reached"] is True and found["error_kind"] == ""
    assert found["category"] == "insufficient_evidence"                 # review I5: a run out of time is no defect
    assert "evidence:run_timed_out" in got["row"]["reason_codes"]


def test_only_g6s_own_codes_say_it_ran_out_of_time_never_the_words_in_its_reply(repo, monkeypatch):
    checks = patch(repo, {"calc.py": BAD + "\n", "test_more.py": MORE})
    Runs({("test_more.py",): result(failed=[MORE_ID])}).into(monkeypatch)

    def worded(*args, **kwargs):
        reply = patch_review.review_patch(*args, **kwargs)
        reply["assessment"]["unknowns"].append("something else; the time budget was spent")
        return reply
    found = g6(repo, checks, review=worded)["disposition"]
    assert found["category"] == "defect_found" and found["time_or_cap_reached"] is False


def test_g6_gets_the_repositorys_import_roots_and_a_g6_without_the_parameter_is_a_tool_error(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}).into(monkeypatch)
    asked = {}

    def taking(*args, import_roots=(), **kwargs):
        asked["import_roots"] = import_roots
        return patch_review.review_patch(*args, **kwargs)
    assert g6(repo, checks, review=taking, import_roots=["lib"])["disposition"]["accepted"] is True
    assert asked["import_roots"] == ["lib"]
    unable = g6(repo, checks, review=patch_review.review_patch, import_roots=["lib"])["disposition"]
    if "import_roots" not in inspect.signature(patch_review.review_patch).parameters:
        assert unable["error_kind"] == "tool_error" and "import_roots" in unable["detail"]
    assert g6(repo, checks)["disposition"]["accepted"] is True              # no roots: nothing is passed


def test_the_direct_checks_run_with_the_same_import_roots(repo):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    seen = []

    class Rooted(Runs):
        def run(self, repo, tests, *, timeout_s, receipt=False, roots=()):
            seen.append(tuple(roots))
            return super().run(repo, tests, timeout_s=timeout_s)
    runs = Rooted({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])})
    got = arms.direct_checks(repo, CONTRACT, checks, timeout_s=300, run=runs.run, run_before=runs.run_before,
                             import_roots=["lib"])
    assert got["disposition"]["accepted"] is True and seen == [("lib",), ("lib",)]


@pytest.mark.parametrize("found", [{"calc.py": {"module": "calc", "file": "/usr/lib/python3/dist-packages/calc.py"}},
                                   None])
def test_the_direct_checks_do_not_accept_a_run_that_loaded_the_changed_code_from_elsewhere(repo, monkeypatch, found):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    monkeypatch.setattr(repo_tests, "shadowed", lambda sources, ran: found, raising=False)
    got = direct(repo, checks, Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}))
    assert got["disposition"]["accepted"] is False and got["disposition"]["category"] == "insufficient_evidence"
    stated = next(c for c in got["checks"] if c["name"] == "stated_checks")
    assert stated["passed"] is False and arms.NOT_LOADED in stated["detail"]


def test_without_g6s_loaded_code_check_the_direct_checks_run_as_before(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    monkeypatch.delattr(repo_tests, "shadowed", raising=False)
    got = direct(repo, checks, Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}))
    assert got["disposition"]["accepted"] is True


def test_the_direct_checks_do_not_accept_a_submodule_folder_g6_could_not_hash(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    monkeypatch.setattr(repo_tests, "submodules_not_hashed", lambda found: {"vendor/lib": "it holds too many files"},
                        raising=False)
    got = direct(repo, checks, Runs({("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}))
    assert got["disposition"]["accepted"] is False and got["disposition"]["category"] == "insufficient_evidence"
    by_name = {c["name"]: c for c in got["checks"]}
    assert by_name["submodule_folders"]["passed"] is False and "vendor/lib" in by_name["submodule_folders"]["detail"]
    assert by_name["tests_touching_changed_modules"]["passed"] is True     # as in G6, evidence never stops tier 2
    assert by_name["repository_unchanged"]["passed"] is True


#: Whether the G6 under test reads which code a run loaded (the pool-compatibility change's guard).
READS_LOADED = hasattr(repo_tests, "shadowed")


@pytest.mark.skipif(not READS_LOADED, reason="this G6 does not read which code a run loaded")
@pytest.mark.parametrize("loaded, complete, accepted", [
    ({}, True, True), ({"calc": "/usr/lib/python3/dist-packages/calc.py"}, True, False), ({}, False, False)])
def test_g6_and_the_direct_checks_read_the_code_a_run_loaded_alike(repo, monkeypatch, loaded, complete, accepted):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    stated = {**result([MORE_ID]), "loaded_outside": loaded, "loaded_complete": complete}
    in_place = {("test_more.py",): stated, ("test_calc.py",): result([ADD, NEGATIVE])}
    Runs(in_place).into(monkeypatch)
    review = g6(repo, checks)
    plain = direct(repo, checks, Runs(in_place))
    assert review["disposition"]["accepted"] is accepted and plain["disposition"]["accepted"] is accepted
    assert review["disposition"]["category"] == plain["disposition"]["category"]
    assert any("changed_code_not_loaded" in code for code in review["row"]["reason_codes"]) is not accepted


#: Whether the G6 under test reads submodule folders git does not look into (the pool-compatibility change); before
#: it, G6 refuses a repository that holds one.
READS_SUBMODULES = hasattr(repo_tests, "submodules_not_hashed")


class Growing(Runs):
    """Recorded runs that also add a file to ``folder`` on the first one: a submodule folder that grows past G6's cap
    while the tests run."""

    def __init__(self, folder, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.folder = folder

    def run(self, repo, tests, *, timeout_s, receipt=False):
        (self.folder / "grown.txt").write_text("written by a test\n", encoding="utf-8")
        return super().run(repo, tests, timeout_s=timeout_s, receipt=receipt)


def linked(root: Path, case: str, monkeypatch) -> tuple[list[str], Runs]:
    """``root`` a development fixture holding a submodule folder git does not look into (a gitlink that was never set
    up, its files in place), with ``case`` applied; the stated checks, and the recorded runs."""
    root.mkdir()
    (root / "calc.py").write_text(BAD, encoding="utf-8")
    (root / "test_calc.py").write_text(TEST, encoding="utf-8")
    (root / "vendor" / "lib").mkdir(parents=True)
    (root / "vendor" / "lib" / "data.txt").write_text("vendored\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "calc.py", "test_calc.py")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/lib")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
        "commit", "-qm", "development fixture with a submodule folder")
    checks = patch(root, {"calc.py": GOOD, "test_more.py": MORE})
    in_place = {("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}
    if case == "entry_changed":
        git(root, "update-index", "--cacheinfo", f"160000,{'2' * 40},vendor/lib")
    if case == "not_hashed":
        monkeypatch.setattr(repo_tests, "COPY_MAX_FILES", 0)
    if case == "not_hashed_after_the_runs":
        monkeypatch.setattr(repo_tests, "COPY_MAX_FILES", 1)
        return checks, Growing(root / "vendor" / "lib", in_place)
    return checks, Runs(in_place)


@pytest.mark.skipif(not READS_SUBMODULES, reason="this G6 refuses a repository that holds a submodule folder")
@pytest.mark.parametrize("case, accepted, code", [
    ("unchanged", True, None), ("entry_changed", False, "change_inside_submodule"),
    ("not_hashed", False, "submodule_not_hashed"), ("not_hashed_after_the_runs", False, "submodule_not_hashed")])
def test_g6_and_the_direct_checks_read_a_submodule_folder_alike(tmp_path, monkeypatch, case, accepted, code):
    checks, runs = linked(tmp_path / "for_g6", case, monkeypatch)
    runs.into(monkeypatch)
    review = g6(tmp_path / "for_g6", checks)
    checks, runs = linked(tmp_path / "direct", case, monkeypatch)
    plain = direct(tmp_path / "direct", checks, runs)
    assert review["disposition"]["accepted"] is accepted and plain["disposition"]["accepted"] is accepted
    assert review["disposition"]["category"] == plain["disposition"]["category"]
    codes = review["row"]["reason_codes"]
    assert any(code in found for found in codes) if code else codes == []
    by_name = {c["name"]: c for c in plain["checks"]}
    assert by_name["submodule_folders"]["passed"] is accepted and by_name["repository_unchanged"]["passed"] is True


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
    spent = arms.direct_checks(repo, CONTRACT, checks, timeout_s=1,
                               run=lambda *a, **k: (time.sleep(1.1), result([MORE_ID]))[1],
                               run_before=Runs().run_before)
    tier_2 = next(c for c in spent["checks"] if c["name"] == "tests_touching_changed_modules")
    assert tier_2["passed"] is False and tier_2["out_of_time"] is True
    assert spent["disposition"]["time_or_cap_reached"] is True


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


MOVED = ["test_addition.py::test_add", "test_addition.py::test_negative"]
PASSING = {("test_more.py",): result([MORE_ID]), ("test_calc.py",): result([ADD, NEGATIVE])}


@pytest.mark.parametrize("name, files, in_place, at_base, accepts", [
    # C2: every changed test-related file through the weakening detector, a helper under tests/ included
    ("C2 helper gutted", {"calc.py": GOOD, "test_more.py": MORE, "tests/helpers.py": "def assert_sum(got, want):\n"
                          "    return None\n"}, PASSING, None, False),
    ("C2 and I6 moved and weakened", {"calc.py": GOOD, "test_calc.py": None,
                                      "test_addition.py": "from calc import add\n\n\ndef test_add():\n"
                                                          "    assert add(2, 3) == 5\n"},
     {("test_addition.py",): result(["test_addition.py::test_add"])}, None, False),
    # I6: a moved test file is compared with its old text, never read as one deleted and another added
    ("I6 moved unchanged", {"calc.py": GOOD, "test_calc.py": None, "test_addition.py": TEST},
     {("test_addition.py",): result(MOVED)}, None, True),
    # I4: a run that began on a tree other than the one under review
    ("I4 other tree", {"calc.py": GOOD, "test_more.py": MORE},
     {**PASSING, ("test_more.py",): result([MORE_ID], source_before="0" * 64)}, None, False),
    # I8: the gate's diff scan, limited to test, runner and continuous-integration files
    ("I8 shortcut in a workflow", {"calc.py": GOOD, "test_more.py": MORE,
                                   ".github/workflows/ci.yml": "jobs:\n  test:\n    continue-on-error: true\n"},
     PASSING, None, False),
    ("I8 the same words in source", {"calc.py": GOOD + "SKIP_CHECKS = True\n", "test_more.py": MORE}, PASSING, None,
     True),
    # I8: the stated checks pass against the base commit's code too, which is an unknown for G6, never an outcome
    ("I8 passes at the base too", {"calc.py": GOOD, "test_more.py": MORE}, PASSING, result([MORE_ID]), True),
    # I9: node ids are read against the root folder the run reports
    ("I9 rootdir below the top", {"calc.py": GOOD, "pkg/test_more.py": MORE},
     {("pkg/test_more.py",): result([MORE_ID], rootdir="pkg"), ("test_calc.py",): result([ADD, NEGATIVE])}, None,
     True),
    # C1 and C3 come through the shared lists and repo_tests.ignored_inputs
    ("C1 pytest.toml", {"calc.py": GOOD, "test_more.py": MORE, "pytest.toml": "[pytest]\n"}, PASSING, None, False),
    ("C3 ignored bytecode", {"calc.py": GOOD, "test_more.py": MORE, ".gitignore": "*.pyc\n", "calc.pyc": "x"},
     PASSING, None, False),
])
def test_arm_4_mirrors_every_check_the_frozen_review_added(repo, monkeypatch, name, files, in_place, at_base,
                                                          accepts):
    checks = patch(repo, files)
    runs = Runs(in_place, default=result([ADD, NEGATIVE, MORE_ID]), at_base=at_base).into(monkeypatch)
    assert g6(repo, checks)["disposition"]["accepted"] is accepts, name
    assert direct(repo, checks, runs)["disposition"]["accepted"] is accepts, name


def test_arm_4_does_not_run_the_stated_checks_at_the_base_commit(repo, monkeypatch):
    checks = patch(repo, {"calc.py": GOOD, "test_more.py": MORE})
    runs = Runs(PASSING, at_base=result([MORE_ID]))
    assert direct(repo, checks, runs)["disposition"]["accepted"] is True
    assert [call for call in runs.calls if call[0] == "at_base"] == []
    runs.into(monkeypatch)
    g6(repo, checks)
    assert ("at_base", ("test_more.py",)) in runs.calls                # G6 does, and it changed nothing


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


def replies(tree, requirements, checks, *, timeout_s, import_roots=()):
    return {"disposition": {"accepted": True, "category": "accept", "error_kind": "", "time_or_cap_reached": False,
                            "detail": "", "cost": {"seconds": 0.0, "processor_seconds": 0.0, "model_calls": 0}},
            "trace": {"commands": [], "isolation": []},
            "seen": {"requirements": requirements, "checks": checks, "timeout_s": timeout_s,
                     "research_flag": os.environ.get("G6_ECHA_RESEARCH"), "isolated": sys.flags.isolated,
                     "import_roots": list(import_roots)}}
"""


@pytest.fixture
def fixture_arms(tmp_path):
    (tmp_path / "v9_fixture_arms.py").write_text(FIXTURE_ARMS, encoding="utf-8")
    return tmp_path


def test_an_arm_in_its_own_isolated_process_replies_without_the_research_flag(fixture_arms, monkeypatch):
    monkeypatch.setenv("G6_ECHA_RESEARCH", "1")
    got = arms.in_child("v9_fixture_arms:replies", fixture_arms, CONTRACT, ["test_more.py"], timeout_s=30,
                        roots=[fixture_arms], import_roots=["lib"])
    assert got["disposition"]["accepted"] is True and got["child"]["exit_code"] == 0
    assert got["seen"] == {"requirements": CONTRACT, "checks": ["test_more.py"], "timeout_s": 30,
                           "research_flag": None, "isolated": 1, "import_roots": ["lib"]}


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
