# SPDX-License-Identifier: Apache-2.0
"""Non-LLM conflict detection across explicit, scope-bound method assertions.

This is a conservative conflict tripwire, NOT a fact checker. It builds on
`g6.epistemic_integrity`: each admitted assertion becomes an evidence record
whose lineage is its source, so one source repeated is one lineage
(`collapse_independence`), and each proposition's status (supported, contested,
refuted, undetermined) is `adjudicate_claim`'s, by that component's rules, not
a second set: the bridge maps the answer, it does not decide again. It reads the
whole answer: a BLOCK or ASK disposition, or a failure the adjudicator
triggered (a G6_E_ code), refuses whatever the status says.
An independent evaluator must admit a statement before it can be used as
evidence. No score averaging.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicator
from g6.invariants import ERROR_CODE_PATTERN

from .codes import (EVIDENCE_CONTESTED, EVIDENCE_NOT_RECONCILED, EVIDENCE_REFUTED, EVIDENCE_UNDETERMINED, refusal,
                    settled)

HEX64 = re.compile("[0-9a-f]{64}\\Z")
#: An assertion's stance as an evidence record states it (``g6.core.evidence.EvidenceStance``).
_STANCE = {"supports": "supports", "refutes": "opposes", "unknown": "context_only"}
#: ``adjudicate_claim``'s epistemic status, as the bridge names it, with the code of a status that blocks; any status
#: not listed blocks too.
_STATUS = {"ASSERTED": ("consistent_with_admitted_evidence", None), "CONTESTED": ("contested", EVIDENCE_CONTESTED),
           "REFUTED": ("refuted", EVIDENCE_REFUTED), "UNVERIFIABLE": ("undetermined", EVIDENCE_UNDETERMINED)}
#: ``adjudicate_claim``'s dispositions that let a passing status stand; BLOCK, ASK and any other refuse.
_PASSING = ("ALLOW", "ALLOW_WITH_LABEL")


@dataclass(frozen=True)
class ScopedAssertion:
    claim_id: str
    proposition_id: str
    scope_digest: str
    stance: str                  # supports | refutes | unknown
    method: str
    evidence_ref: str
    evaluator_receipt: str

    def __post_init__(self) -> None:
        if not all((self.claim_id, self.proposition_id, self.method,
                    self.evidence_ref, self.evaluator_receipt)):
            raise ValueError("assertion must include scope, method, source and receipt")
        if not HEX64.fullmatch(self.scope_digest):
            raise ValueError("claim's logical scope must be digest-bound")
        if self.stance not in ("supports", "refutes", "unknown"):
            raise ValueError("unsupported assertion stance")


class CrossMethodConflictGate:
    """Explicitly checked opposite stances on exactly the same scope block.

    Missing evaluator admission makes records 'unadmitted', not independent
    evidence; those never establish a claim. A trusted injected verifier must
    replay the receipt and bind it to the assertion contents.
    """
    def __init__(self, admit_assertion: Callable[[ScopedAssertion], bool] | None = None):
        self.admit_assertion = admit_assertion

    def reconcile(self, assertions: Sequence[ScopedAssertion]) -> dict[str, Any]:
        groups: dict[tuple[str, str, str], list[ScopedAssertion]] = {}
        refused: list[str] = []
        duplicate_sources: list[str] = []
        for row in assertions:
            admitted = False
            if self.admit_assertion is not None:
                try:
                    admitted = self.admit_assertion(row) is True
                except Exception:
                    admitted = False
            if not admitted:
                refused.append(row.evidence_ref)
                continue
            groups.setdefault((row.claim_id, row.proposition_id, row.scope_digest), []).append(row)
        findings = []
        conflicts = 0
        for (claim_id, proposition_id, scope), data in groups.items():
            # Only exactly the same proposition and scope are compared. Each assertion is an evidence record whose
            # lineage is its source: one source repeated is one lineage, not corroboration, and a source that changes
            # its stance contests itself. The status is epistemic_integrity's adjudication of these records.
            records = tuple(EvidenceRecord(row.evidence_ref, supports_or_opposes=_STANCE[row.stance],
                                           independence_group=row.evidence_ref, claim_id=claim_id) for row in data)
            lineages = adjudicator.collapse_independence(records)
            duplicate_sources += [source for source, members in lineages.items() for _ in members[1:]]
            # The adjudicator's whole result is read: its status, its disposition and any failure it triggered (a
            # G6_E_ code). A passing result is a status the bridge maps to a pass, an ALLOW disposition and no failure.
            result = adjudicator.adjudicate_claim(proposition_id, evidence_records=records)
            status, disposition = result.claim.epistemic_status, result.claim.disposition
            failures = [t for t in result.triggered if ERROR_CODE_PATTERN.match(str(t))]
            label, code = _STATUS.get(status, ("not_reconciled", EVIDENCE_NOT_RECONCILED))
            if code is None and (disposition not in _PASSING or failures):
                label, code = "not_reconciled", EVIDENCE_NOT_RECONCILED
            conflicts += label == "contested"
            findings.append(settled(
                refusal(code, f"{proposition_id}: {label} ({status}, {disposition}"
                              + "".join(f", {f}" for f in failures) + ")") if code else None,
                claim_id=claim_id, proposition_id=proposition_id, scope_digest=scope, status=label,
                epistemic_status=status, disposition=disposition, triggered=list(result.triggered),
                source_count=len(lineages), methods=sorted({r.method for r in data}),
                warning="Consistency does not establish truth or domain correctness"))
        # Every unadmitted claim is insufficient. Do not call it a successful
        # reconciliation merely because the contradictory evidence was rejected.
        unsettled = [f"{x['proposition_id']}: {x['status']}" for x in findings
                     if x["completion_state"] != "qualified-draft"]
        why = ([f"{len(refused)} assertion(s) not admitted"] if refused else []) + unsettled + (
            [] if groups else ["no admitted assertion"])
        return settled(refusal(EVIDENCE_NOT_RECONCILED, "; ".join(why)) if why else None,
                       conflicts=conflicts, unadmitted_evidence_refs=refused,
                       repeated_source_refs=duplicate_sources,
                       scoped_findings=findings,
                       limitations=["Only exact proposition-and-scope identity is compared; differing scopes are "
                                    "incomparable.",
                                    "Agreement is neither independent replication nor evidence of truth.",
                                    "Admissibility depends on an external trusted evaluator, not a field or an LLM "
                                    "judgment."])


__all__ = ["ScopedAssertion", "CrossMethodConflictGate"]
