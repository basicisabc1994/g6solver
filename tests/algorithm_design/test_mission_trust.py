# SPDX-License-Identifier: Apache-2.0
"""The design mission's trust gaps (P1, PR #3; Review Focus 2 and 3): a run needs a person's queue decision bound to
exactly this mission, isolation is read from confine's run record, the mission writes a durable ledger, and with no
engineer nothing runs."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace

import pytest

from g6.algorithm_design.autonomous import (
    ApprovalRef,
    DesignMission,
    NoEngineer,
    PatchProposal,
    RepoTestsRunner,
    TestObservation,
    run_agent,
)
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.resources import DeviceBudget
from g6.complexity.ir import FrozenIntent, Obligation, Requirement
from g6.core.ledger import ChainLedger
from g6.operator import ActionQueue, OperatorStore
from g6.workforce import repo_tests

SOURCE = "def f(x):\n    return x + 1\n"
TARGET = "app/logic.py"
TESTS = ("tests/test_logic.py",)


@pytest.fixture
def q(tmp_path):
    store = OperatorStore(tmp_path / "operator.db")
    scope = store.for_owner("dana")
    queue = ActionQueue(scope)
    queue.venture = scope.create_venture("missions", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    yield queue
    store.close()


def _mission(repo="/unused", **changes):
    intent = FrozenIntent((Requirement("r1", "Preserve public function output", "stated", "person"),),
                          (Obligation("o1", "r1", "assert original behavior", "person", True),))
    mission = DesignMission(
        repo=repo, files={"app/__init__.py": "", TARGET: SOURCE}, package="app", source_root="",
        target_path=TARGET, tests=TESTS, intent=intent, problem=DesignProblem(("sequence",), "membership"),
        environment_digest="env1", device=DeviceBudget(1_000_000, 100_000, 1_000_000), dimensions={"n": 50},
        mode="swe", max_attempts=3)
    return replace(mission, **changes)


def _approve(q, mission, *, reviewer="ana", decide=True, binding=None):
    item = q.request_run(q.venture, binding=binding or mission.fingerprint, summary="run the mission's tests",
                         requested_by="g6:algorithm_design")
    if decide:
        q.approve(q.venture, item["action_id"], reviewer=reviewer)
    return replace(mission, approval=ApprovalRef(q.venture, item["action_id"]))


class Producer:
    def __init__(self, sources):
        self.sources, self.briefs = iter(sources), []

    def propose(self, brief):
        self.briefs.append(brief)
        source = next(self.sources, None)
        return PatchProposal(TARGET, source, "swe_refactor") if source is not None else None


class Runner:
    def __init__(self, verdicts):
        self.verdicts, self.sources = iter(verdicts), []

    def _observe(self, source):
        self.sources.append(source)
        verdict = next(self.verdicts)
        return TestObservation(verdict, True, True, "fake-host", "env1", "checks",
                               hashlib.sha256(source.encode()).hexdigest(),
                               TESTS if verdict == "failed" else (), "assertion failure" if verdict == "failed" else "",
                               True)

    def baseline(self, *, tests, timeout_s):
        return self._observe(SOURCE)

    def run(self, source, *, expected_path, tests, timeout_s):
        return self._observe(source)


def test_no_engineer_refuses_before_any_test_runs(q, tmp_path):
    runner = Runner(["passed"])
    report = run_agent(_approve(q, _mission()), NoEngineer(), runner, queue=q, ledger=ChainLedger(tmp_path / "m.jsonl"))
    assert (report.reason, report.code, runner.sources) == ("no_engineer", "G6_E_ALGO_NO_ENGINEER", [])
    assert report.completion_state == "blocked-escalated"


@pytest.mark.parametrize("approval,reason", [("none", "execution_not_approved"), ("proposed", "execution_not_approved"),
                                             ("other_mission", "approval_mismatch"),
                                             ("rejected", "execution_not_approved")])
def test_an_unapproved_or_misbound_mission_runs_nothing(q, tmp_path, approval, reason):
    mission = _mission()
    if approval == "proposed":
        mission = _approve(q, mission, decide=False)
    elif approval == "other_mission":
        mission = _approve(q, mission, binding=_mission(max_attempts=2).fingerprint)
    elif approval == "rejected":
        mission = _approve(q, mission, decide=False)
        q.reject(q.venture, mission.approval.action_id, reviewer="ana", reason="not now")
    producer, runner = Producer([SOURCE]), Runner(["passed", "passed"])
    report = run_agent(mission, producer, runner, queue=q, ledger=ChainLedger(tmp_path / "m.jsonl"))
    assert (report.reason, report.code) == (reason, "G6_E_ALGO_" + reason.upper())
    assert producer.briefs == [] and runner.sources == [] and report.approved_by == ""


def test_the_fingerprint_binds_the_mission_and_never_its_repository_path():
    assert _mission().fingerprint == _mission(repo="/elsewhere").fingerprint
    assert _mission().fingerprint != _mission(max_attempts=2).fingerprint != _mission(tests=("tests/other.py",)).fingerprint


def test_an_approved_mission_records_its_approver_in_a_durable_ledger(q, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    first = run_agent(_approve(q, _mission()), Producer([SOURCE.replace("1", "2")]), Runner(["failed", "passed"]),
                      queue=q)
    second = run_agent(_approve(q, _mission()), Producer([SOURCE.replace("1", "3")]), Runner(["failed", "passed"]),
                       queue=q)
    led = ChainLedger(tmp_path / "algorithm_design" / "missions.jsonl")
    assert led.verify() == () and first.run_id != second.run_id and first.approved_by == second.approved_by == "ana"
    assert {e["id"].split(":")[0] for e in led.entries()} == {first.run_id, second.run_id}
    assert first.completion_state == "qualified-draft"
    approval = [e["body"] for e in led.entries() if e["kind"] == "approval"]
    assert [a["reviewer"] for a in approval] == ["ana", "ana"] and all(a["action_id"] for a in approval)


def test_a_second_writer_is_a_ledger_conflict_not_a_traceback(q, tmp_path):
    path = tmp_path / "m.jsonl"

    class Intruder(Producer):
        def propose(self, brief):
            other = ChainLedger(path)
            other.append("note", "intruder", {"said": "hello"})
            return super().propose(brief)

    report = run_agent(_approve(q, _mission()), Intruder([SOURCE.replace("1", "2")]), Runner(["failed", "passed"]),
                       queue=q, ledger=ChainLedger(path))
    assert (report.completion_state, report.reason, report.code) == ("blocked-escalated", "ledger_conflict",
                                                                     "G6_E_ALGO_LEDGER_CONFLICT")


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / TARGET).write_text(SOURCE, encoding="utf-8")
    (repo / TESTS[0]).write_text("from app.logic import f\n\ndef test_f():\n    assert f(1) == 2\n", encoding="utf-8")
    for args in (["-c", "core.autocrlf=false", "init", "-q"], ["add", "-A"], ["-c", "user.name=g6 test", "-c", "user.email=test@example.invalid",
                                                 "-c", "commit.gpgsign=false", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.parametrize("isolation,counted", [
    ({"backend": "bubblewrap", "network": False}, True),                    # Review Focus 3
    ({"backend": "appcontainer", "network": False}, True),
    ({"backend": None, "allowed_by": "G6_ALLOW_UNCONFINED"}, False),
    ({"backend": "bubblewrap", "network": True}, False)])
def test_isolation_is_read_from_confines_record(q, isolation, counted, git_repo, monkeypatch):
    calls = []
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)    # no longer consulted: the run asks for isolation itself
    monkeypatch.setattr(repo_tests, "run", lambda *a, **k: calls.append(k) or {
        "verdict": "passed", "completed": True, "isolation": isolation, "receipt": "", "source_before": "d",
        "python": "p", "interpreter_source": "g6"})
    runner = RepoTestsRunner(_approve(q, _mission(repo=str(git_repo))), queue=q)
    obs = runner.run(SOURCE.replace("1", "2"), expected_path=TARGET, tests=TESTS, timeout_s=10)
    base = runner.baseline(tests=TESTS, timeout_s=10)
    assert obs.isolated is counted and base.isolated is counted and calls
    assert all(k["require_isolation"] is True for k in calls)
    assert calls[0].get("mutant") == (TARGET, SOURCE.replace("1", "2")) and calls[1].get("receipt") is True
    assert "mutant" not in calls[1] or calls[1]["mutant"] is None


def test_the_runner_checks_the_approval_before_every_run(q, git_repo):
    mission = _approve(q, _mission(repo=str(git_repo)), decide=False)
    with pytest.raises(PermissionError):
        RepoTestsRunner(mission, queue=q)
