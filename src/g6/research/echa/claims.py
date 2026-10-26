# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The claim rule (ECHA spec 2.2 and 2.3): a claim's result is computed from its evidence, never written by hand.

``supported`` needs an active check that passed. A check is active only if it can be shown to fail: an executable check
with no recorded negative control is a structural protection (ruling R-1) and supports nothing. A review by a model or a
person is a lead, never support, and reviews that share a group are one voice. A partial invariant that passed supports a
claim about the cases it covered and never a general one. An independent failure refutes the claim on its scope. Evidence
about another standard, another predicate or a fixture with a recorded incident does not count. A claim about true
correctness is not identified unless every active oracle's error is bounded, and the bounds of separate oracles add.
Everything else is ``inconclusive``, with the reasons.
"""
from __future__ import annotations

from typing import Any

from .flag import require_research
from .records import ClaimRecord, EvidenceRecord

EXECUTABLE = ("FORMAL_CHECK", "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER")


def _control_holds(ledger: Any, rec: EvidenceRecord, contaminated: set) -> bool:
    """Whether the check's negative control is in the ledger, is one, failed on its injected defect, concerns the same
    standard, and was not taken against a compromised fixture."""
    if not rec.negative_control_ref:
        return False
    try:
        control = EvidenceRecord.from_dict(ledger.get("evidence", rec.negative_control_ref))
    except (KeyError, ValueError, TypeError):
        return False
    return (control.purpose == "negative_control" and control.result == "fail"
            and control.standard_sha256 == rec.standard_sha256 and control.fixture_version not in contaminated
            and control.predicate_id == rec.predicate_id and control.oracle_version == rec.oracle_version
            and control.oracle_category in EXECUTABLE)


def _once(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def evaluate_claim(ledger: Any, claim_id: str) -> dict:
    require_research()
    claim = ClaimRecord.from_dict(ledger.get("claim", claim_id))
    contaminated = ledger.contaminated_fixtures()
    reasons: list[str] = []
    cited: list[EvidenceRecord] = []
    for eid in claim.evidence_ids:
        try:
            rec = EvidenceRecord.from_dict(ledger.get("evidence", eid))
        except (KeyError, ValueError, TypeError):
            _once(reasons, "evidence_not_traceable")
            continue
        if rec.purpose != "check":
            _once(reasons, "cited_record_is_a_control_not_a_check")
        elif rec.standard_sha256 != claim.standard_sha256:
            _once(reasons, "evidence_for_another_standard")
        elif rec.predicate_id != claim.predicate_id:
            _once(reasons, "evidence_for_another_predicate")
        elif rec.fixture_version in contaminated:
            _once(reasons, "contaminated_fixture")
        else:
            cited.append(rec)

    active: list[EvidenceRecord] = []
    structural: list[str] = []
    leads: list[str] = []
    for rec in cited:
        if rec.oracle_category == "HEURISTIC_REVIEW":
            if rec.result == "fail":
                leads.append(rec.evidence_id)
        elif rec.oracle_category in EXECUTABLE and rec.result in ("pass", "fail"):
            if rec.oracle_category == "FORMAL_CHECK" or _control_holds(ledger, rec, contaminated):
                active.append(rec)
            else:
                structural.append(rec.evidence_id)
                if rec.result == "fail":
                    leads.append(rec.evidence_id)

    refuting = [r for r in active if r.result == "fail"]
    passing = [r for r in active if r.result == "pass"]
    groups = {r.independence_group for r in passing if r.independent}
    # Two labels over one oracle are one voice: groups count only as far as there are distinct oracles behind them.
    voices = min(len(groups), len({r.oracle_version for r in passing if r.independent}))
    unexplained = [r.evidence_id for r in cited if r.oracle_category in EXECUTABLE and r.result == "fail"
                   and r.evidence_id in structural]
    bounds: dict[tuple[str, str], float] = {}
    for rec in active:
        if rec.oracle_error_bound is not None:
            key = (rec.oracle_version, rec.independence_group)
            bounds[key] = max(bounds.get(key, 0.0), float(rec.oracle_error_bound))
    identified = bool(active) and all(r.oracle_error_bound is not None for r in active)

    if refuting:
        result = "unsupported"
    elif passing:
        result = "supported"
        if claim.breadth == "general" and not any(r.oracle_category == "FORMAL_CHECK" for r in passing):
            result = "inconclusive"
            _once(reasons, "partial_evidence_cannot_support_a_general_claim")
        if voices < claim.requires_independent_groups:
            result = "inconclusive"
            _once(reasons, "independence_not_met")
        if unexplained:  # a check that failed and was never shown able to fail is not outweighed by one that passed
            result = "inconclusive"
            _once(reasons, "unexplained_failure")
        if claim.estimand == "true_correctness" and not all(r.oracle_error_bound is not None for r in passing):
            result = "inconclusive"
            _once(reasons, "oracle_error_not_bounded")
    else:
        result = "inconclusive"
        _once(reasons, "no_active_evidence")
        if structural:
            _once(reasons, "not_an_active_check")
        if cited and all(r.oracle_category == "HEURISTIC_REVIEW" for r in cited):
            _once(reasons, "only_heuristic_evidence")

    limits: list[str] = list(claim.known_limitations)
    for rec in active:
        for item in (*rec.known_limitations, f"covers only: {rec.scope}"):
            _once(limits, item)
    if any(r.oracle_category == "FORMAL_CHECK" for r in passing):
        _once(limits, "a proof holds relative to its formalisation, which may not capture the intended requirement")
    if any(r.oracle_category == "PARTIAL_FALSIFIER" for r in passing):
        _once(limits, "partial evidence: a passing invariant does not establish general correctness")
    if structural:
        _once(limits, "a check with no recorded negative control is a structural protection, not an active check")

    roles = {**{r.evidence_id: "active" for r in active}, **{eid: "structural" for eid in structural}}
    return {"claim_id": claim.claim_id, "predicate_id": claim.predicate_id, "scope": claim.scope,
            "breadth": claim.breadth, "estimand": claim.estimand, "result": result, "reasons": reasons,
            "limits": limits, "independent_groups": voices, "active_evidence": [r.evidence_id for r in active],
            "refuting_evidence": [r.evidence_id for r in refuting], "structural": structural, "leads": leads,
            "identification": "identified" if identified else "not_identified",
            "oracle_error_bound": sum(bounds.values()) if identified else None,
            "evidence": [{"evidence_id": r.evidence_id, "category": r.oracle_category, "result": r.result,
                          "role": roles.get(r.evidence_id, "lead" if r.evidence_id in leads else "not_support")}
                         for r in cited]}
