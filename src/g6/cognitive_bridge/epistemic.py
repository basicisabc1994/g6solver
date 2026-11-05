# SPDX-License-Identifier: Apache-2.0
"""Non-LLM conflict detection across explicit, scope-bound method assertions.

This is a conservative conflict tripwire, NOT a fact verifier and not a
replacement for `g6.epistemic_integrity`. An independent evaluator must
admit a statement before it can be used as evidence. No score averaging.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

HEX64 = re.compile("[0-9a-f]{64}\\Z")


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
        observed: dict[tuple[str, str, str, str], str] = {}
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
            key = (row.claim_id, row.proposition_id, row.scope_digest)
            source_key = (*key, row.evidence_ref)
            if source_key in observed:
                # One source repeated is not independent corroboration.
                # If it changes its stance, it is itself inconsistent.
                duplicate_sources.append(row.evidence_ref)
                if observed[source_key] != row.stance:
                    groups.setdefault(key, []).append(row)
                continue
            observed[source_key] = row.stance
            groups.setdefault(key, []).append(row)
        findings = []
        conflicts = 0
        for (claim_id, proposition_id, scope), data in groups.items():
            stances = {row.stance for row in data}
            if "supports" in stances and "refutes" in stances:
                label, status = "contested", "blocked-escalated"
                conflicts += 1
            elif stances <= {"unknown"}:
                label, status = "undetermined", "blocked-escalated"
            else:
                label, status = "consistent_with_admitted_evidence", "qualified-draft"
            findings.append({"claim_id": claim_id, "proposition_id": proposition_id,
                             "scope_digest": scope, "status": label,
                             "completion_state": status,
                             "source_count": len(data),
                             "methods": sorted({r.method for r in data}),
                             "warning": "Consistency does not establish truth or domain correctness"})
        # Every unadmitted claim is insufficient. Do not call it a successful
        # reconciliation merely because the contradictory evidence was rejected.
        blocked = bool(conflicts or refused or not groups or
                       any(x["completion_state"] == "blocked-escalated" for x in findings))
        return {"completion_state": "blocked-escalated" if blocked else "qualified-draft",
                "conflicts": conflicts, "unadmitted_evidence_refs": refused,
                "repeated_source_refs": duplicate_sources,
                "scoped_findings": findings,
                "limitations": ["Only exact proposition-and-scope identity is compared; differing scopes are incomparable.",
                                "Agreement is neither independent replication nor proof of truth.",
                                "Admissibility depends on an external trusted evaluator, not a field or an LLM judgment."]}


__all__ = ["ScopedAssertion", "CrossMethodConflictGate"]
