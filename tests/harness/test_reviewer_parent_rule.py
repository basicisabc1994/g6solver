# SPDX-License-Identifier: Apache-2.0
"""Reviewer cases need body failures at both the parent and the patch to become defects."""
import ast
import inspect
from types import SimpleNamespace

import pytest

from g6.harness import completion_loop as loop, patch_report
from g6.workforce import repo_tests


@pytest.fixture
def parent_round(tmp_path, monkeypatch):
    change = SimpleNamespace(repo=tmp_path)
    monkeypatch.setattr(repo_tests, "diff_read", lambda _: SimpleNamespace(text="a patch"))
    world = SimpleNamespace(change=change, session=tmp_path / "session", reviews=[], runs=[],
                            base=loop.ProbeRun(1, {"test_critic.py::test_bug": "failed"}, {}),
                            patch=loop.ProbeRun(1, {"test_critic.py::test_bug": "failed"}, {}))

    def critic(change, task, caller, system, where, n, **kwargs):
        where.mkdir(parents=True, exist_ok=True)
        (where / loop.critic_file(n)).write_text("def test_bug():\n    assert False\n")
        return {"verdict": "defect", "rule": "the stated rule", "why": "the case fails", "test": "assert False"}

    def run(change, where, *, at_base, deadline, only):
        world.runs.append((at_base, deadline, only))
        return world.base if at_base else world.patch

    monkeypatch.setattr(loop, "run_critic", critic)
    monkeypatch.setattr(loop, "run_set", run)
    return world


@pytest.mark.parametrize("before,exit_code,is_finding", [
    ("failed", 1, True), ("passed", 0, False), ("error", 1, False), ("unknown", None, False),
    ("skipped", 0, False), (None, None, False), ("failed", None, False)])
def test_parent_body_failure_is_required_for_feedback(parent_round, before, exit_code, is_finding):
    world = parent_round
    world.base = loop.ProbeRun(exit_code, {"test_critic.py::test_bug": before} if before else {}, {}, "parent unavailable")
    failing, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    assert bool(failing) is is_finding
    assert sorted(world.runs) == [(False, 1000, "test_critic.py"), (True, 1000, "test_critic.py")]
    if is_finding:
        assert "the stated rule" in feedback
        assert patch_report.findings(world.reviews)[1]
    else:
        assert feedback == "" and patch_report.findings(world.reviews) == ([], [])
        rendered = patch_report.completion_report({"reviews": world.reviews}, "local review")
        assert "**Note:**" in rendered and "python -m pytest test_critic.py::test_bug" in rendered
        assert "parent" in rendered


def test_mixed_cases_only_send_parent_and_patch_failures(parent_round):
    world = parent_round
    world.base.results["test_critic.py::test_regression"] = "passed"
    world.patch.results["test_critic.py::test_regression"] = "failed"
    failing, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    assert failing == {"critic:test_critic.py::test_bug"}
    assert world.reviews[0]["failed"] == {"test_critic.py": ["test_critic.py::test_bug"]}
    assert "test_bug" in feedback
    rendered = patch_report.completion_report({"reviews": world.reviews}, "local review")
    assert "test_regression" in rendered and "**Note:**" in rendered


def test_patch_fixture_error_is_a_note_even_when_parent_failed(parent_round):
    world = parent_round
    world.patch = loop.ProbeRun(1, {"test_critic.py::test_bug": "error"}, {})
    failing, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    assert not failing and not feedback and patch_report.findings(world.reviews) == ([], [])
    assert "**Note:**" in patch_report.completion_report({"reviews": world.reviews}, "local review")


def test_reporting_cannot_promote_ungated_file_failure_to_finding():
    legacy = {"n": 1, "file": "test_critic.py", "results": {"test_critic.py": "failed"},
              "failed": {"test_critic.py": ["test_critic.py::test_bug"]}}
    assert patch_report.findings([legacy]) == ([], [])


def test_every_reviewer_feedback_entry_uses_the_one_parent_rule():
    # Inventory both the fresh/retained review loop and the optional scouts' critic.
    inventory = {"review_round": "run", "_advance_inner": "check"}
    tree = ast.parse(inspect.getsource(loop))
    actual = set()
    for owner in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        for entry in (node for node in ast.walk(owner) if isinstance(node, ast.FunctionDef)):
            direct = [node for statement in entry.body for node in ast.walk(statement)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                      and node.func.id == "reviewer_cases"]
            if direct and entry is not owner:
                actual.add((owner.name, entry.name))
    assert actual == set(inventory.items())
    for outer, inner in inventory.items():
        owner = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == outer)
        entry = next(node for node in ast.walk(owner) if isinstance(node, ast.FunctionDef) and node.name == inner)
        calls = [node for node in ast.walk(entry) if isinstance(node, ast.Call)]
        assert any(isinstance(call.func, ast.Name) and call.func.id == "reviewer_cases" for call in calls), outer
        assert not any(isinstance(call.func, ast.Name) and call.func.id == "failures_of" and
                       any(isinstance(node, ast.Constant) and node.value == "critic" for node in ast.walk(call))
                       for call in calls), outer
    # Rendering reuses the same rule on retained observations, including old session data.
    assert "reviewer_cases(" in inspect.getsource(patch_report.admitted_cases)
    assert "admitted_cases(" in inspect.getsource(patch_report.findings)


def test_parent_refusal_is_a_note_without_suppressing_patch_observation(parent_round, monkeypatch):
    world = parent_round

    def run(change, where, *, at_base, deadline, only):
        if at_base:
            raise repo_tests.RepoRefused("G6_E_ISOLATION_UNAVAILABLE", "parent boundary unavailable")
        return world.patch

    monkeypatch.setattr(loop, "run_set", run)
    failing, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    assert not failing and not feedback
    rendered = patch_report.completion_report({"reviews": world.reviews}, "local review")
    assert "**Note:**" in rendered and "parent boundary unavailable" in rendered


def test_retained_case_cannot_bypass_parent_rule_in_later_round(parent_round):
    world = parent_round
    loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    world.base.results["test_critic.py::test_bug"] = "passed"
    failing, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    assert not failing and not feedback
    # The earlier admitted failure is still open until its own case passes on the patch.
    assert patch_report.findings(world.reviews)[1]
    assert world.reviews[-1]["notes"]["test_critic.py"]


@pytest.mark.parametrize("after", ["passed", "skipped", "error", "unknown", None])
def test_parent_failure_alone_never_admits_a_case(after):
    base = loop.ProbeRun(1, {"test_critic.py::test_bug": "failed"}, {})
    patch = loop.ProbeRun(0, {"test_critic.py::test_bug": after} if after else {}, {})
    assert loop.reviewer_cases(patch, base)[0] == set()


@pytest.mark.parametrize("done", [False, True])
def test_saved_patch_only_failure_is_a_note_and_never_replayed_as_a_defect(tmp_path, monkeypatch, done):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    session = tmp_path / "session"
    legacy = {"n": 1, "verdict": "defect", "rule": "legacy rule", "why": "legacy reason", "file": "test_critic.py",
              "results": {"test_critic.py": "failed"}, "failed": {"test_critic.py": ["test_critic.py::test_bug"]}}
    loop._save(session, {"done": done, "reviews": [legacy], "rounds": [
        {"tree": None, "failing": ["critic:test_critic.py::test_bug"], "text": "stale defect feedback"}]})

    def forbidden(*args, **kwargs):
        raise AssertionError("saved patch-only failures must end before any new work or cached feedback")

    monkeypatch.setattr(loop, "review_patch", forbidden)
    monkeypatch.setattr(repo_tests, "checked_repo", forbidden)
    result = loop.advance(tmp_path, "task", "HEAD", session)
    assert result.action == loop.STOP and result.ending == "untested"
    assert "**Note:**" in result.text and "parent observations" in result.text
    assert "python -m pytest test_critic.py::test_bug" in result.text
    assert "stale defect feedback" not in result.text


def test_continue_reply_lists_notes_separately_with_reason_and_command(parent_round, monkeypatch):
    from g6.harness import completion_hook
    from g6.mcp import review_loop

    world = parent_round
    world.base.results["test_critic.py::test_regression"] = "passed"
    world.patch.results["test_critic.py::test_regression"] = "failed"
    _, feedback = loop.review_round(world.change, "task", object(), loop.harness(), world.session, world.reviews, 1000)
    loop._save(world.session, {"reviews": world.reviews})
    monkeypatch.setattr(completion_hook, "folder", lambda _: world.session)
    monkeypatch.setattr(review_loop, "_asked", lambda *a: {"repo": str(world.change.repo), "task": "task", "base": "HEAD"})
    monkeypatch.setattr(loop, "advance", lambda *a, **k: loop.Step(loop.CONTINUE, feedback))
    reply = review_loop._step(str(world.change.repo), "task", "session")
    [note] = reply["reviewer_notes"]
    assert note["case"] == "test_critic.py::test_regression" and "parent case: passed" in note["reason"]
    assert note["command"] == "python -m pytest test_critic.py::test_regression"
    assert note["case"] not in reply["text"]
