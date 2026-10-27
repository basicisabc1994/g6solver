# SPDX-License-Identifier: Apache-2.0
"""Reference application: run selected Python tests and review a patch's scoped claims.

Tests execute through workforce confinement. Supplied sources remain caller assertions;
lexical support does not establish freshness or truth. This application never merges code.
"""
from __future__ import annotations

import platform
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.compose import Stage, compose
from g6.core.door import blocked_reply
from g6.core.ledger import digest
from g6.core.result import Result
from g6.workforce import repo_tests, verify


class GateAdapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    checks: list[dict]
    task: str
    claim: str
    test_run: dict


class GateAdapter(AIBlock):
    """Reviewed mapping: source support stays separate from executed test observations."""
    __g6_block_contract__ = BlockContract(
        name="patch_gate_adapter", inputs_required=("checks", "task", "claim", "test_run"),
        outputs=("task", "answer", "task_contract", "evidence", "verifier"),
        evidence_inputs_per_op={"adapt": {"checks": "source-support-assessment"}},
        verification_method="unit_tests_only",
        guarantees=("does not convert source support into test evidence",))

    def __init__(self):
        super().__init__("patch_gate_adapter")

    def infer(self, data: GateAdapterInput) -> Result[dict]:
        ran = data.test_run
        passed = ran.get("verdict") == "passed" and ran.get("passed", 0) > 0 and not ran.get("degraded")
        # The generic gate door accepts only declared evidence. The enclosing report retains
        # the host's independent execution receipt; this adapter cannot upgrade its authority.
        return Result.ok({"task": data.task, "answer": data.claim, "task_contract": data.task,
                          "evidence": [{"kind": "test", "ref": ran.get("command", "pytest"),
                                        "exit_code": 0 if passed else 1}],
                          "verifier": {"status": "pass" if passed else "fail", "exit_code": 0 if passed else 1},
                          "completion_state": "qualified-draft"})


class ActionAdapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    verdict: str


class ActionAdapter(AIBlock):
    __g6_block_contract__ = BlockContract(
        name="patch_action_adapter", inputs_required=("verdict",), outputs=("action",),
        verification_method="unit_tests_only",
        guarantees=("a coding gate pass is not permission to merge",))

    def __init__(self):
        super().__init__("patch_action_adapter")

    def infer(self, data: ActionAdapterInput) -> Result[dict]:
        return Result.ok({"action": {"action_type": "merge_patch", "description": "Accept the reviewed Python patch",
                                      "external_side_effect": True, "reversibility": "unknown",
                                      "metadata": {"coding_gate_verdict": data.verdict}},
                          "completion_state": "qualified-draft"})


def review_patch(repo: str | Path, *, task: str, claim: str, sources: dict[str, str], tests: list[str],
                 base: str = "HEAD", expected_artifact_digest: str = "", timeout_s: int = 300) -> dict:
    """Check a working-tree patch against a base commit. Never accepts claimed test results.

    Optional expected_artifact_digest pins a prior snapshot. Missing isolation, changed
    runner files, weakened tests, failed checks or a changed snapshot prevent recommendation.
    Even the positive outcome requires review and does not authorize any external action.
    """
    try:
        root = repo_tests.checked_repo(repo)
        before = repo_tests.snapshot(root)
        artifact = digest(before)
        if expected_artifact_digest and artifact != expected_artifact_digest:
            return blocked_reply("[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest")
        change = repo_tests.change(root, base)
        weakened = verify.weakened_tests(change.before, change.after)
        ran = repo_tests.run(root, tests, timeout_s=timeout_s)
        unresolved = []
        if before != repo_tests.snapshot(root):
            unresolved.append("repository changed during review")
        if ran["verdict"] != "passed" or ran["passed"] < 1 or ran["degraded"] or ran.get("not_passed"):
            unresolved.append("selected tests did not all pass")
        if not ran["isolation"].get("backend"):
            unresolved.append("unconfined test execution")
        if weakened:
            unresolved.append("changed tests may weaken the acceptance criteria")
        if change.runner_files:
            unresolved.append("test runner configuration changed")
        flow = compose(
            Stage("grounding", "check_claims", inputs={"text": "caller.claim", "sources": "caller.sources"}),
            Stage(GateAdapter(), "adapt", inputs={"checks": "0.checks", "task": "caller.task",
                                                   "claim": "caller.claim", "test_run": "caller.test_run"}),
            Stage("coding_agent_gate", "verify_answer", inputs={key: f"1.{key}" for key in
                  ("task", "answer", "task_contract", "evidence", "verifier")}),
            Stage(ActionAdapter(), "adapt", inputs={"verdict": "2.verdict"}),
            Stage("action_gating", "evaluate", inputs={"action": "3.action"}),
            caller_inputs=("task", "claim", "sources", "test_run"))
        composition = flow.infer({"task": task, "claim": claim, "sources": sources, "test_run": ran}).value
        if composition["completion_state"] == "blocked-escalated":
            unresolved.append("composition requires further evidence or correction")
        receipt = {"artifact_digest": artifact, "base_commit": change.base_commit,
                   "check": "selected pytest tests", "check_version": "1", "tests": list(tests),
                   "result": ran["verdict"], "environment": {"python": ran.get("python"),
                   "host_python_version": platform.python_version(), "platform": platform.platform(),
                   "isolation": ran["isolation"]}, "authority": "host-observed",
                   "scope": "these test outcomes on this working tree; not complete correctness"}
        receipt["digest"] = digest(receipt)
        return {"ok": not unresolved, "decision": "blocked" if unresolved else "needs_review",
                "completion_state": "blocked-escalated" if unresolved else "qualified-draft",
                "warning_card": {"code": "G6_E_PATCH_REVIEW", "message": "; ".join(unresolved) or
                                 "Selected checks passed; review unresolved scope before accepting the patch."},
                "artifact_digest": artifact, "base_commit": change.base_commit, "changed_files": list(change.files),
                "tests": ran, "test_integrity": weakened, "executed_checks": [receipt],
                "evidence": [receipt], "composition": composition, "unresolved": unresolved,
                "sources": {name: {"digest": digest(text), "authority": "caller-supplied",
                                    "freshness": "not established"} for name, text in sources.items()},
                "not_checked": ["complete patch correctness", "test oracle independence", "source freshness",
                                "application benefit relative to a baseline", "merge authorization"]}
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}")
    except (OSError, ValueError, TypeError) as exc:
        return blocked_reply(f"[G6_E_PATCH_REVIEW] {exc}")
