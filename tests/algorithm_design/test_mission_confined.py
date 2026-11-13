# SPDX-License-Identifier: Apache-2.0
"""The design mission for real under bubblewrap (P1, PR #3; Review Focus 3): its baseline runs in place and is
receipted in G6's receipt store, its candidates run as mutants and are never receipted. Skips where bubblewrap cannot
confine a run, and fails instead where ``G6_EXPECT_BACKEND=bubblewrap`` (CI's Linux jobs, and the Docker routine)."""
from __future__ import annotations

import subprocess
from dataclasses import replace


from g6.algorithm_design.autonomous import ApprovalRef, DesignMission, PatchProposal, RepoTestsRunner, run_agent
from g6.algorithm_design.ir import DesignProblem
from g6.algorithm_design.resources import DeviceBudget
from g6.complexity.ir import FrozenIntent, Obligation, Requirement
from g6.core.ledger import ChainLedger
from g6.operator import ActionQueue, OperatorStore
from g6.workforce import receipt_store

BROKEN = "def double(x):\n    return x + x + 1\n"
FIXED = "def double(x):\n    return x + x\n"
TEST = "from app.logic import double\n\n\ndef test_double():\n    assert double(2) == 4\n"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=g6 test", "-c", "user.email=test@example.invalid", "-c",
                    "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], cwd=repo, check=True,
                   capture_output=True)


class Fixer:
    def __init__(self):
        self.briefs = []

    def propose(self, brief):
        self.briefs.append(brief)
        return PatchProposal("app/logic.py", FIXED, "swe_refactor") if len(self.briefs) == 1 else None


def test_a_confined_mission_receipts_its_baseline_and_not_its_mutants(bwrap_backend, tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_bytes(b"")
    (repo / "app" / "logic.py").write_bytes(BROKEN.encode())
    (repo / "tests" / "test_logic.py").write_bytes(TEST.encode())
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    intent = FrozenIntent((Requirement("r1", "double(x) is 2x", "stated", "person"),),
                          (Obligation("o1", "r1", "tests/test_logic.py passes", "person", True),))
    mission = DesignMission(
        repo=str(repo), files={"app/__init__.py": "", "app/logic.py": BROKEN}, package="app", source_root="",
        target_path="app/logic.py", tests=("tests/test_logic.py",), intent=intent,
        problem=DesignProblem(("sequence",), "membership"), environment_digest="env1",
        device=DeviceBudget(1_000_000, 100_000, 1_000_000), dimensions={"n": 50}, mode="swe", max_attempts=2,
        timeout_per_run_s=120)
    store = OperatorStore(tmp_path / "operator.db")
    try:
        scope = store.for_owner("dana")
        queue = ActionQueue(scope)
        venture = scope.create_venture("missions", caps={"model": 1.0, "cash": 0.0})["venture_id"]
        item = queue.request_run(venture, binding=mission.fingerprint, summary="run the mission's tests",
                                 requested_by="g6:algorithm_design")
        queue.approve(venture, item["action_id"], reviewer="ana")
        mission = replace(mission, approval=ApprovalRef(venture, item["action_id"]))
        report = run_agent(mission, Fixer(), RepoTestsRunner(mission, queue=queue), queue=queue,
                           ledger=ChainLedger(tmp_path / "missions.jsonl"))
    finally:
        store.close()
    assert report.completion_state == "qualified-draft", (report.reason, report.observations)
    assert report.observations[0].backend == "bubblewrap" and report.approved_by == "ana"
    assert report.receipts == (report.observations[0].receipt,) and report.observations[0].receipt
    assert receipt_store.check(report.receipts[0],
                               artifact_digest=report.observations[0].artifact_digest)[0] == receipt_store.MATCHED
    assert report.observations[-1].receipt == ""                 # a mutant is never the artifact under review
