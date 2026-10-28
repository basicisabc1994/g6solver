# SPDX-License-Identifier: Apache-2.0
"""CodingAgentGateBlock: the registry / MCP door to ``preflight`` and ``verify_answer``. Deterministic, no model.

Everything that arrives through this door is caller-declared: an evidence item or verifier result may only say
``observed_by="caller"``. Unknown fields are refused rather than ignored (CAG-013: the archive accepted a ``mode``
and silently did nothing with it), and the list fields take JSON arrays of the declared type only (CAG-010).

One exception (plan Step 2, A6): a block a host builds with ``receipt_artifact_digest`` (the digest of the tree the
host itself reviews, as the patch review does) replaces an evidence item whose ``receipt`` is the id of a test
receipt G6's own store holds for that digest (``g6.workforce.receipt_store.check``) by that receipt's test run,
``observed_by="g6"``, whatever else the item says. The digest is never taken from the request (review C1: a caller
could otherwise name the digest any stored receipt binds), so the instance the registry and MCP doors build has none,
and there every receipt is ``not_honoured_at_door``. The result's ``receipts`` says, item by item, why each one was or
was not (``matched``, ``unknown``, ``artifact_mismatch``, ``store_unreadable``, ``not_honoured_at_door``,
``not_supplied``), with the matched run's digest, commit, tests asked for and counts, as ``evidence_counted`` does.
A matched run counts as a pass only when every test it collected ran and passed (``receipt_store.counts_as_pass``).
A host may also bind the diff it read itself (``scanned_diff``, as the patch review binds its working tree's diff):
``verify_answer`` scans it for shortcuts as it scans a diff item, and does not count it as evidence.
Only G6's confined test runs write receipts. The store is a file owned by the same user as G6: this defends against
what a caller sends, not against code running as that user.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from g6.coding_agent_gate.evidence import Evidence, VerifierResult
from g6.coding_agent_gate.gate import preflight, verify_answer
from g6.coding_agent_gate.protected_paths import ProtectedPathsPolicy
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.maturity import component_maturity
from g6.core.result import Result

#: Refusal code: there is no task to check the plan or the answer against.
E_TASK_REQUIRED = "G6_E_CODING_AGENT_GATE_TASK_REQUIRED"
_MAX_TEXT = 2_000_000

_OPS: dict[str, str] = {
    "preflight": "preflight(task, proposed_action, evidence=[{kind, ref, ..., receipt}], risk_tier, constraints): "
                 "block / needs_review / needs_evidence / proceed_with_caution",
    "verify_answer": "verify_answer(task, answer, task_contract, evidence=[{kind, ref, output, exit_code, reviewer, "
                     "receipt}], verifier={status, exit_code}, claims, risk_tier, author): block / needs_review / "
                     "revise / pass; a pass is qualified-draft, never verified; over this door a receipt is "
                     "not_honoured_at_door",
}


class EvidenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["file", "test", "log", "diff", "review"]
    ref: str = Field("", max_length=2_000)
    output: str = Field("", max_length=_MAX_TEXT)
    exit_code: StrictInt | None = None
    reviewer: str = Field("", max_length=200)
    observed_by: Literal["caller"] = "caller"
    #: The id of a G6 test receipt (``repo_tests.run(..., receipt=True)``): G6-observed only when the store holds it
    #: for the request's ``artifact_digest``; otherwise the item stands as the caller sent it.
    receipt: str = Field("", max_length=200)


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
    reason="deterministic checks over caller-declared evidence and G6's own test receipts; text shortcut detector is "
           "a tripwire; never verified",
)
class CodingAgentGateBlock(AIBlock[CodingAgentGateInput, dict, dict]):
    """Check a coding agent's plan or drafted answer. Advisory unless the caller obeys it."""
    __g6_block_contract__ = BlockContract(
        name="coding_agent_gate",
        purpose="Assess declared coding evidence and G6's own test receipts, without running tests.",
        inputs_required_per_op={"verify_answer": ("task", "answer"), "preflight": ("task",)},
        outputs_per_op={op: ("verdict", "findings", "completion_state") for op in ("verify_answer", "preflight")},
        verification_method="model", bad_at=("authenticating caller evidence", "executing tests"),
        guarantees=("gate pass remains a qualified draft",
                    "evidence is G6-observed only through a stored G6 test receipt for the artifact digest its host "
                    "bound it to, never one a caller sends"))


    def __init__(self, name: str = "coding_agent_gate", state: dict | None = None,
                 *, protected_paths_policy: ProtectedPathsPolicy | None = None,
                 receipt_artifact_digest: str = "", scanned_diff: str = "") -> None:
        super().__init__(name=name, state=state or {})
        self.protected_paths_policy = protected_paths_policy
        #: The digest of the tree the host reviews; "" (the door's instance): no receipt is honoured.
        self.receipt_artifact_digest = receipt_artifact_digest
        #: The diff the host read itself, which ``verify_answer`` scans and never counts; "" at the door.
        self.scanned_diff = scanned_diff

    def infer(self, data: CodingAgentGateInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if not data.task.strip():
            return Result.fail(f"[{E_TASK_REQUIRED}] {data.op}: task is required")
        evidence, receipts = _evidence(data, self.receipt_artifact_digest)
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
                                     protected_paths_policy=self.protected_paths_policy,
                                     scanned_diff=self.scanned_diff)
        out = decision.to_dict()
        runs = {r["receipt"]: r["run"] for r in receipts if "run" in r}
        out["evidence_counted"] = [{**item, "receipt_run": runs[item["ref"]]}
                                   if item.get("observed_by") == "g6" and item.get("ref") in runs else item
                                   for item in out["evidence_counted"]]
        return Result.ok({"component": self.name, **out, "receipts": receipts,
                          **decision.to_envelope(request_id=data.request_id, run_id=data.run_id)})


def _evidence(data: CodingAgentGateInput, bound: str) -> tuple[list[Evidence], list[dict]]:
    """The evidence items, and each one's receipt status in the order sent. With a host binding (``bound``), an item
    citing a receipt the store holds for that digest becomes the receipt's own test run, G6-observed, exit code 0 only
    when ``receipt_store.counts_as_pass``; every other item is caller-declared as sent."""
    items: list[Evidence] = []
    receipts: list[dict] = []
    for e in data.evidence:
        status, body = "not_supplied", None
        if e.receipt and not bound:
            status = "not_honoured_at_door"
        elif e.receipt:
            from g6.workforce import receipt_store   # here, not above: a call that cites no receipt never loads it

            status, body = receipt_store.check(e.receipt, artifact_digest=bound)
        if body is None:
            receipts.append({"receipt": e.receipt, "receipt_status": status})
            items.append(Evidence(e.kind, e.ref, e.output, e.exit_code, e.reviewer))
            continue
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        tests = body.get("tests") if isinstance(body.get("tests"), dict) else {}
        isolation = body.get("isolation") if isinstance(body.get("isolation"), dict) else {}
        run = {"source_before": body.get("source_before"), "base_commit": body.get("base_commit"),
               "requested": tests.get("requested"), "verdict": result.get("verdict"),
               **{key: result.get(key) for key in ("passed", "failed", "not_passed", "total", "completed")}}
        receipts.append({"receipt": e.receipt, "receipt_status": status, "run": run})
        summary = (f"G6 test receipt {e.receipt}: a run confined by {isolation.get('backend')} ended "
                   f"{result.get('verdict')!r} ({result.get('passed')} passed, {result.get('failed')} failed, "
                   f"{result.get('not_passed')} not passed of {result.get('total')})")
        items.append(Evidence("test", e.receipt, summary, 0 if receipt_store.counts_as_pass(body) else 1,
                              observed_by="g6"))
    return items, receipts


__all__ = ["CodingAgentGateBlock", "CodingAgentGateInput", "EvidenceIn", "VerifierIn"]
