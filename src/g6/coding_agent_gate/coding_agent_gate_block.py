# SPDX-License-Identifier: Apache-2.0
"""CodingAgentGateBlock: the registry / MCP door to ``preflight`` and ``verify_answer``. Deterministic, no model.

Everything that arrives through this door is caller-declared: an evidence item or verifier result may only say
``observed_by="caller"``. Unknown fields are refused rather than ignored (CAG-013: the archive accepted a ``mode``
and silently did nothing with it), and the list fields take JSON arrays of the declared type only (CAG-010).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from g6.coding_agent_gate.evidence import Evidence, VerifierResult
from g6.coding_agent_gate.gate import preflight, verify_answer
from g6.coding_agent_gate.protected_paths import ProtectedPathsPolicy
from g6.core.ai_block import AIBlock
from g6.core.maturity import component_maturity
from g6.core.result import Result

#: Refusal code: there is no task to check the plan or the answer against.
E_TASK_REQUIRED = "G6_E_CODING_AGENT_GATE_TASK_REQUIRED"
_MAX_TEXT = 2_000_000

_OPS: dict[str, str] = {
    "preflight": "preflight(task, proposed_action, evidence=[{kind, ref, ...}], risk_tier, constraints): "
                 "block / needs_review / needs_evidence / proceed_with_caution",
    "verify_answer": "verify_answer(task, answer, task_contract, evidence=[{kind, ref, output, exit_code, reviewer}], "
                     "verifier={status, exit_code}, claims, risk_tier, author): block / needs_review / revise / pass; "
                     "a pass is qualified-draft, never verified",
}


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "test", "log", "diff", "review"]
    ref: str = Field("", max_length=2_000)
    output: str = Field("", max_length=_MAX_TEXT)
    exit_code: StrictInt | None = None
    reviewer: str = Field("", max_length=200)
    observed_by: Literal["caller"] = "caller"


class VerifierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pass", "fail", "not_run"]
    exit_code: StrictInt | None = None
    source: str = Field("", max_length=2_000)
    observed_by: Literal["caller"] = "caller"


class CodingAgentGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["preflight", "verify_answer", "ops", "help"] = "verify_answer"
    task: str = Field("", max_length=_MAX_TEXT)
    answer: str = Field("", max_length=_MAX_TEXT)
    proposed_action: str = Field("", max_length=_MAX_TEXT)
    task_contract: str = Field("", max_length=_MAX_TEXT)
    constraints: list[str] = Field(default_factory=list, max_length=200)
    claims: list[str] = Field(default_factory=list, max_length=200)
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=200)
    verifier: VerifierIn | None = None
    risk_tier: str = Field("", max_length=40)
    author: str = Field("", max_length=200)
    touched_paths: list[str] | None = Field(None, max_length=1000)
    require_task_contract: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


@component_maturity(
    "beta",
    reason="deterministic checks over caller-declared evidence; text shortcut detector is a tripwire; never verified",
)
class CodingAgentGateBlock(AIBlock[CodingAgentGateInput, dict, dict]):
    """Check a coding agent's plan or drafted answer. Advisory unless the caller obeys it."""

    def __init__(self, name: str = "coding_agent_gate", state: dict | None = None,
                 *, protected_paths_policy: ProtectedPathsPolicy | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.protected_paths_policy = protected_paths_policy

    def infer(self, data: CodingAgentGateInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if not data.task.strip():
            return Result.fail(f"[{E_TASK_REQUIRED}] {data.op}: task is required")
        evidence = [Evidence(e.kind, e.ref, e.output, e.exit_code, e.reviewer) for e in data.evidence]
        if data.op == "preflight":
            decision = preflight(data.task, proposed_action=data.proposed_action, evidence=evidence,
                                 risk_tier=data.risk_tier, constraints=data.constraints, author=data.author,
                                 touched_paths=data.touched_paths, protected_paths_policy=self.protected_paths_policy)
        else:
            verifier = None if data.verifier is None else VerifierResult(data.verifier.status, data.verifier.exit_code,
                                                                         data.verifier.source)
            decision = verify_answer(data.task, data.answer, evidence=evidence, verifier=verifier, claims=data.claims,
                                     task_contract=data.task_contract, risk_tier=data.risk_tier, author=data.author,
                                     require_task_contract=data.require_task_contract, touched_paths=data.touched_paths,
                                     protected_paths_policy=self.protected_paths_policy)
        return Result.ok({"component": self.name, **decision.to_dict(),
                          **decision.to_envelope(request_id=data.request_id, run_id=data.run_id)})


__all__ = ["CodingAgentGateBlock", "CodingAgentGateInput", "EvidenceIn", "VerifierIn"]
