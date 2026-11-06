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
from dataclasses import asdict
from typing import Any, Callable, Mapping, Sequence

from g6.core.evidence import EvidenceRecord
from g6.epistemic_integrity import adjudicator
from g6.invariants import ERROR_CODE_PATTERN

from .assurance import canonical_json, sha256
from .codes import (EVIDENCE_CONTESTED, EVIDENCE_NOT_RECONCILED, EVIDENCE_REFUTED, EVIDENCE_UNDETERMINED, MADE_FIELDS,
                    STATES, affirmed, only_fields, refusal, settled)
from .snapshot import fresh, issue, record, snapshot

HEX64 = re.compile("[0-9a-f]{64}\\Z")
#: An assertion's stance as an evidence record states it (``g6.core.evidence.EvidenceStance``).
_STANCE = {"supports": "supports", "refutes": "opposes", "unknown": "context_only"}
#: ``adjudicate_claim``'s epistemic status, as the bridge names it, with the code of a status that blocks; any status
#: not listed blocks too.
_STATUS = {"ASSERTED": ("consistent_with_admitted_evidence", None), "CONTESTED": ("contested", EVIDENCE_CONTESTED),
           "REFUTED": ("refuted", EVIDENCE_REFUTED), "UNVERIFIABLE": ("undetermined", EVIDENCE_UNDETERMINED)}
#: ``adjudicate_claim``'s dispositions that let a passing status stand; BLOCK, ASK and any other refuse.
_PASSING = ("ALLOW", "ALLOW_WITH_LABEL")
#: A reconciliation report's own fields (beside the state, code, reason and card ``codes.settled`` writes): what
#: ``CrossMethodConflictGate.reconcile`` writes, and all a report may carry. ``subject_sha256`` names the assertions it
#: reconciled (:func:`assertions_sha256`).
REPORT_FIELDS = ("subject_sha256", "conflicts", "unadmitted_evidence_refs", "repeated_source_refs", "scoped_findings",
                 "limitations")


@record
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


def assertions_sha256(assertions: Sequence[ScopedAssertion]) -> str:
    """The digest of an assertion set (each assertion's every field, in any order, repeats kept). A report names it as
    its ``subject_sha256``, and an assessment credits a report only for the assertions submitted to that assessment."""
    return sha256(canonical_json(sorted(canonical_json(asdict(a)).decode() for a in assertions)))


def checked_report(report: Any) -> Mapping[str, Any]:
    """A reconciliation report as the bridge takes it from a gate: only its format's fields (:data:`REPORT_FIELDS`), no
    claim of a state anywhere in it (``codes.only_fields``), and itself a result in the makers' format
    (``codes.result_faults``: blocked with a registered code and that code's card, or a qualified draft)."""
    return only_fields(report, allowed=REPORT_FIELDS + MADE_FIELDS, required=("completion_state",),
                       where="a conflict report")


def findings_state(report: Mapping[str, Any]) -> str:
    """The state a report's own fields give, by the rule ``reconcile`` decides by: ``blocked-escalated`` when an
    assertion was not admitted, there is no finding, or a finding is not ``qualified-draft``; else ``qualified-draft``."""
    findings = report.get("scoped_findings", ())
    unsettled = (not isinstance(findings, (list, tuple)) or not findings
                 or bool(report.get("unadmitted_evidence_refs"))
                 or any(not isinstance(f, dict) or f.get("completion_state") != STATES[0] for f in findings))
    return STATES[unsettled]  # STATES is (qualified-draft, blocked-escalated)


class CrossMethodConflictGate:
    """Explicitly checked opposite stances on exactly the same scope block.

    Missing evaluator admission makes records 'unadmitted', not independent
    evidence; those never establish a claim. A trusted injected verifier must
    replay the receipt and bind it to the assertion contents.
    """
    def __init__(self, admit_assertion: Callable[[ScopedAssertion], bool] | None = None):
        self.admit_assertion = admit_assertion

    def reconcile(self, assertions: Sequence[ScopedAssertion]) -> dict[str, Any]:
        assertions = snapshot(assertions, where="assertions")
        groups: dict[tuple[str, str, str], list[ScopedAssertion]] = {}
        refused: list[str] = []
        duplicate_sources: list[str] = []
        for row in assertions:
            admitted = False
            if self.admit_assertion is not None:
                try:
                    admitted = affirmed(self.admit_assertion(fresh(row)))  # a copy: what it admits is what is used
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
        return issue(self, settled(refusal(EVIDENCE_NOT_RECONCILED, "; ".join(why)) if why else None,
                                   subject_sha256=assertions_sha256(assertions),
                                   conflicts=conflicts, unadmitted_evidence_refs=refused,
                                   repeated_source_refs=duplicate_sources,
                                   scoped_findings=findings,
                                   limitations=["Only exact proposition-and-scope identity is compared; differing "
                                                "scopes are incomparable.",
                                                "Agreement is neither independent replication nor evidence of truth.",
                                                "Admissibility depends on an external trusted evaluator, not a field "
                                                "or an LLM judgment."]))


__all__ = ["REPORT_FIELDS", "ScopedAssertion", "CrossMethodConflictGate", "assertions_sha256", "checked_report",
           "findings_state"]
