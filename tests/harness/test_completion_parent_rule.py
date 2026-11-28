# SPDX-License-Identifier: Apache-2.0
"""The parent rule (the owner's ruling R4, 2026-11-23; stop-exact-review 38628f4c): a reviewer's case is a defect sent back
to the agent only when it fails at the parent (the base commit's code) and still fails on the patch. A case that fails only
on the patch, or whose parent run gave no usable result, is a note in the report, never defect feedback."""
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop, patch_report
from g6.workforce import repo_tests

CASE = "test_critic.py::test_bug"


@pytest.fixture
def parent_round(tmp_path, monkeypatch):
    """One review round with the reviewer and its runs stood in for: ``base`` and ``patch`` are what the reviewer's test
    gives at the parent and on the patch."""
    monkeypatch.setattr(repo_tests, "diff_read", lambda _: SimpleNamespace(text="a patch"))
    world = SimpleNamespace(change=SimpleNamespace(repo=tmp_path), session=tmp_path / "session", reviews=[], runs=[],
                            base=loop.ProbeRun(1, {CASE: "failed"}, {}), patch=loop.ProbeRun(1, {CASE: "failed"}, {}))

    def critic(change, task, caller, system, where, n, **kwargs):
        where.mkdir(parents=True, exist_ok=True)
        (where / loop.critic_file(n)).write_text("def test_bug():\n    assert False\n")
        return {"verdict": "defect", "rule": "the stated rule", "why": "the case fails", "test": "assert False"}

    def run(change, where, *, at_base, deadline, only):
        world.runs.append((at_base, only))
        return world.base if at_base else world.patch

    monkeypatch.setattr(loop, "run_critic", critic)
    monkeypatch.setattr(loop, "run_set", run)
    world.round = lambda: loop.review_round(world.change, "task", object(), loop.harness(), world.session,
                                            world.reviews, 1000)
    return world


def report(world) -> str:
    return patch_report.completion_report({"reviews": world.reviews}, "the model-free review")


def test_a_case_that_fails_at_the_parent_and_on_the_patch_is_a_defect(parent_round):
    failing, feedback = parent_round.round()
    assert failing == {f"critic:{CASE}"} and "the stated rule" in feedback
    assert sorted(parent_round.runs) == [(False, "test_critic.py"), (True, "test_critic.py")]
    assert patch_report.findings(parent_round.reviews)[1]


def test_a_case_that_fails_only_on_the_patch_is_a_note(parent_round):
    parent_round.base = loop.ProbeRun(0, {CASE: "passed"}, {})
    failing, feedback = parent_round.round()
    assert failing == set() and feedback == ""
    assert patch_report.findings(parent_round.reviews) == ([], [])
    said = report(parent_round)
    assert "**Note:**" in said and CASE in said and "parent case: passed" in said
    assert "Parent rule" in said


@pytest.mark.parametrize("before,exit_code", [("error", 1), ("unknown", None), ("skipped", 0), (None, None),
                                              ("failed", None)])
def test_a_parent_run_with_no_usable_failure_is_a_note_never_a_defect(parent_round, before, exit_code):
    parent_round.base = loop.ProbeRun(exit_code, {CASE: before} if before else {}, {}, "parent unavailable")
    failing, feedback = parent_round.round()
    assert failing == set() and feedback == ""
    assert "**Note:**" in report(parent_round)


def test_a_parent_run_the_boundary_refused_is_a_note(parent_round, monkeypatch):
    def run(change, where, *, at_base, deadline, only):
        if at_base:
            raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "parent boundary unavailable")
        return parent_round.patch

    monkeypatch.setattr(loop, "run_set", run)
    failing, feedback = parent_round.round()
    assert failing == set() and feedback == ""
    said = report(parent_round)
    assert "**Note:**" in said and "parent boundary unavailable" in said


def test_only_the_cases_that_meet_the_rule_go_back(parent_round):
    parent_round.base.results["test_critic.py::test_regression"] = "passed"
    parent_round.patch.results["test_critic.py::test_regression"] = "failed"
    failing, feedback = parent_round.round()
    assert failing == {f"critic:{CASE}"} and "test_regression" not in feedback
    assert parent_round.reviews[0]["failed"] == {"test_critic.py": [CASE]}
    assert "test_regression" in report(parent_round)


@pytest.mark.parametrize("after", ["passed", "skipped", "error", "unknown", None])
def test_a_parent_failure_alone_admits_nothing(after):
    base = loop.ProbeRun(1, {CASE: "failed"}, {})
    patch = loop.ProbeRun(0, {CASE: after} if after else {}, {})
    assert loop.reviewer_cases(patch, base)[0] == set()


def test_a_failure_saved_before_the_rule_is_a_note_and_never_sent_back(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    session = tmp_path / "session"
    saved = {"n": 1, "verdict": "defect", "rule": "saved rule", "why": "saved reason", "file": "test_critic.py",
             "results": {"test_critic.py": "failed"}, "failed": {"test_critic.py": [CASE]}}
    loop._save(session, {"reviews": [saved], "rounds": [{"tree": None, "failing": [f"critic:{CASE}"],
                                                         "text": "stale defect feedback"}]})

    def forbidden(*args, **kwargs):
        raise AssertionError("a saved patch-only failure ends the loop before any new work")

    monkeypatch.setattr(loop, "review_patch", forbidden)
    monkeypatch.setattr(repo_tests, "checked_repo", forbidden)
    step = loop.advance(tmp_path, "task", "HEAD", session)
    assert step.action == loop.STOP and step.ending == "untested"
    assert "**Note:**" in step.text and "parent observations" in step.text and "stale defect feedback" not in step.text
