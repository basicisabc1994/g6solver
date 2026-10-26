# SPDX-License-Identifier: Apache-2.0

"""epistemic_integrity block — deterministic claim adjudication + evidence gating.

A thin AIBlock over the deterministic adjudication floor in ``adjudicator``
(G6-EI-001). It takes a message (or atomic claim), its declared action criticality,
and any evidence records, and returns per-claim epistemic status / evidence grade /
confidence-ceiling / disposition plus an overall most-restrictive disposition. No LLM
runs in this floor; the gate cannot be loosened by tone or user pressure (EI-094).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core import AIBlock, Result
from g6.core.block_contract import FailureMode
from g6.core.contract_decorator import block_contract
from g6.core.evidence import EvidenceRecord
from g6.core.maturity import component_maturity
from g6.epistemic_integrity.adjudicator import adjudicate

_OPS: dict[str, str] = {
    "adjudicate": "adjudicate(message, action_criticality, evidence): each claim's type, risk, evidence grade, "
                  "status, confidence and disposition, and the most restrictive disposition overall",
}


#: Refusal code: there is no message to adjudicate.
E_MESSAGE_REQUIRED = "G6_E_EPISTEMIC_INTEGRITY_MESSAGE_REQUIRED"
#: Refusal code: an evidence record has a key an EvidenceRecord does not take, or a value it refuses.
E_EVIDENCE_INVALID = "G6_E_EPISTEMIC_INTEGRITY_EVIDENCE_INVALID"
#: The keys read as ``source_uri_or_artifact_id`` when it is not given.
_SOURCE_ALIASES = frozenset({"source", "uri"})


class EpistemicIntegrityInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")  # a misspelt parameter is refused, never dropped

    op: Literal["adjudicate", "ops", "help"] = "adjudicate"
    message: str = ""
    action_criticality: str = ""
    evidence: list[dict] = Field(default_factory=list)
    has_human_approval: bool = False
    has_rollback: bool = False


def _to_records(evidence: list[dict]) -> tuple[EvidenceRecord, ...]:
    """Build EvidenceRecord tuple from caller dicts. A key EvidenceRecord does not take, or a value it refuses, raises
    ``ValueError`` (review B1-1: a misspelt ``stance`` was dropped, and the opposing record read as support)."""
    known = set(EvidenceRecord.__dataclass_fields__)
    out: list[EvidenceRecord] = []
    for index, raw in enumerate(evidence or []):
        if not isinstance(raw, dict):
            continue
        unknown = sorted(set(raw) - known - _SOURCE_ALIASES)
        if unknown:
            raise ValueError(f"evidence[{index}] has keys an evidence record does not take: {unknown}")
        kwargs = {k: v for k, v in raw.items() if k in known}
        # source_uri_or_artifact_id is the only required field; default it honestly.
        kwargs.setdefault("source_uri_or_artifact_id", str(raw.get("source") or raw.get("uri") or "unknown"))
        try:
            out.append(EvidenceRecord(**kwargs))
        except ValueError as exc:
            raise ValueError(f"evidence[{index}]: {exc}") from None
    return tuple(out)


@block_contract(
    purpose="epistemic_integrity",
    inputs_required_per_op={"adjudicate": ("message",)},
    outputs=("AdjudicationResult",),
    known_failure_modes=(
        # D4 batch 5: BLANK — `adjudicate_claim` (adjudicator.py:233-307)
        # assigns `claim.confidence = confidence_ceiling(grade, contested)`
        # (line 260,302) unconditionally — the ONLY assignment to
        # `confidence` anywhere in the file (grepped). `confidence_ceiling`
        # (line 201-206) reads a hardcoded, pure-Python `_GRADE_CEILING`
        # dict (line 56) with NO override path — no env var, no policy YAML,
        # no caller-supplied value can ever raise `confidence` above its
        # grade's ceiling. A self-consistency detector comparing
        # `confidence` against `evidence_grade`'s ceiling would never fire —
        # permanently, unconditionally impossible by construction, not a
        # genuine discriminator.
        FailureMode("grade_overstated", detector="", severity="high"),
        # D4 batch 5: WIRE (new bespoke detector, epistemic_integrity/honesty.py)
        # — under the policy in `core/risk_policy.py`, `gate_decision` never
        # returns "ALLOW" for evidence_grade=="E0" at any risk tier (R0
        # always returns ALLOW_WITH_LABEL; R1/R2/R3 all require
        # grade_meets(grade, required) before allowing ALLOW, else
        # ASK/ESCALATE/BLOCK). The matrix is code now (no docs-tree YAML is
        # read), so the detector guards against a code change that breaks
        # the invariant. The self-consistency check (does this claim's own
        # `evidence_grade`/`disposition` pair violate the floor's own
        # documented invariant) reads only 2 sibling fields already present
        # on each `claims[]` entry of the Output payload.
        FailureMode("unsupported_claim_allowed", detector="adjudication_at_tests", severity="high"),
    ),
    mitigates_failure_modes=("epistemic_indifference", "repetition_as_corroboration", "false_confidence"),
    verification_method="tier1_review_pending",
)
@component_maturity(
    "beta",
    reason="deterministic epistemic-integrity adjudication floor (G6-EI-001); HELD at qualified_draft (tier1_review_pending)",
)
class EpistemicIntegrityBlock(AIBlock[EpistemicIntegrityInput, dict, dict]):
    name: str = "epistemic_integrity"

    def __init__(self, name: str = "epistemic_integrity", state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: EpistemicIntegrityInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        if not (data.message or "").strip():
            return Result.fail(f"[{E_MESSAGE_REQUIRED}] adjudicate: message is required")
        try:
            records = _to_records(data.evidence)
        except ValueError as exc:
            return Result.fail(f"[{E_EVIDENCE_INVALID}] adjudicate: {exc}")
        result = adjudicate(
            data.message,
            action_criticality=data.action_criticality,
            evidence_records=records,
            has_human_approval=data.has_human_approval,
            has_rollback=data.has_rollback,
        )
        payload = result.to_dict()
        payload["component"] = self.name
        payload["llm_used"] = False
        return Result.ok(payload)


__all__ = ["EpistemicIntegrityBlock", "EpistemicIntegrityInput"]
