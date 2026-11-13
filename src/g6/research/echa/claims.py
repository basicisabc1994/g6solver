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

G6 0.3 (slice 2) closes the two places where this rule differed from the issuer's, ``g6.evidence.certificate``:

- a formal check is active only with a recorded negative control, as every executable check is;
- a claim is bound to its subject's bytes. A record a custody binding ties to an artefact (``custody.binding_of``)
  counts only for a claim whose subject names that artefact (``sha256:<hex>``), so it cannot be retargeted to a patch
  that never ran; a claim that names an artefact counts only records bound to it. A claim and records that name no
  bytes are read as declared (``subject_bound`` false).

Every part a record is about (its artefact, standard and predicate) is read through ``custody.about``: from its
custody binding when it has one. A record whose own standard or predicate is not its binding's counts for nothing,
and the reason names the part (``G6_E_EVIDENCE_BINDING_STANDARD``, ``G6_E_EVIDENCE_BINDING_PREDICATE``), so a bound
receipt cannot be retargeted to another standard or predicate by its fields; its control likewise. A record whose
binding does not read as one counts for nothing (``G6_E_EVIDENCE_BINDING_UNREADABLE``), never as a record with none.

An evaluator is compared by its bytes (``custody.evaluator_of``), never by its label: a control activates a check only
when both name one evaluator digest, and distinct evaluators, for independence and for error bounds, are distinct
digests. Records with no binding name only declared labels, compared as declared.

This rule is research analysis, never a requirement's status: the issuer decides that, and ECHA shows it as it is
(``view.certificate_view``).
"""
from __future__ import annotations

import re
from typing import Any

from .custody import about, evaluator_of
from .records import ClaimRecord, EvidenceRecord

EXECUTABLE = ("FORMAL_CHECK", "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER")
_SUBJECT = re.compile(r"sha256:([0-9a-f]{64})\Z")


def subject_artefact(claim: ClaimRecord) -> str | None:
    """The artefact a claim's subject names (``sha256:<hex>``), or None when it names no bytes."""
    found = _SUBJECT.match(claim.subject.strip())
    return found.group(1) if found else None


def _control_holds(ledger: Any, rec: EvidenceRecord, contaminated: set) -> bool:
    """Whether the check's negative control is in the ledger, is one, failed on its injected defect, concerns the same
    standard and predicate as the check (each read through its custody binding, which it agrees with), was taken by
    the same evaluator, compared by its bytes (``evaluator_of``), and was not taken against a compromised fixture.
    ``rec`` is a cited check, whose own binding ``evaluate_claim`` has read already."""
    if not rec.negative_control_ref:
        return False
    try:
        control = EvidenceRecord.from_dict(ledger.get("evidence", rec.negative_control_ref))
    except (KeyError, ValueError, TypeError):
        return False
    (of_control, control_refused), (of_check, _) = about(control), about(rec)
    return (not control_refused and control.purpose == "negative_control" and control.result == "fail"
            and of_control.standard == of_check.standard and control.fixture_version not in contaminated
            and of_control.predicates == of_check.predicates and evaluator_of(control) == evaluator_of(rec)
            and control.oracle_category in EXECUTABLE)


def _once(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def evaluate_claim(ledger: Any, claim_id: str) -> dict:
    claim = ClaimRecord.from_dict(ledger.get("claim", claim_id))
    contaminated = ledger.contaminated_fixtures()
    subject = subject_artefact(claim)
    reasons: list[str] = []
    cited: list[EvidenceRecord] = []
    for eid in claim.evidence_ids:
        try:
            rec = EvidenceRecord.from_dict(ledger.get("evidence", eid))
        except (KeyError, ValueError, TypeError):
            _once(reasons, "evidence_not_traceable")
            continue
        parts, disagree = about(rec)
        bound = parts.artefact or None
        if disagree:   # a field that is not its binding's: the record is refused, with the code naming the part
            for code, _why in disagree:
                _once(reasons, code)
        elif bound is not None and bound != subject:   # evidence on other bytes is never this subject's
            _once(reasons, "evidence_for_another_subject")
        elif subject is not None and bound is None:   # a claim about bytes counts only evidence taken on them
            _once(reasons, "evidence_not_bound_to_subject")
        elif rec.purpose != "check":
            _once(reasons, "cited_record_is_a_control_not_a_check")
        elif parts.standard != claim.standard_sha256:
            _once(reasons, "evidence_for_another_standard")
        elif parts.predicates != (claim.predicate_id,):
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
            if _control_holds(ledger, rec, contaminated):   # a formal check too, as the issuer requires
                active.append(rec)
            else:
                structural.append(rec.evidence_id)
                if rec.result == "fail":
                    leads.append(rec.evidence_id)

    refuting = [r for r in active if r.result == "fail"]
    passing = [r for r in active if r.result == "pass"]
    groups = {r.independence_group for r in passing if r.independent}
    # Two groups over one evaluator are one voice: groups count only as far as there are distinct evaluators behind
    # them, each named by its bytes when it has them (evaluator_of), never by a label
    voices = min(len(groups), len({evaluator_of(r) for r in passing if r.independent}))
    unexplained = [r.evidence_id for r in cited if r.oracle_category in EXECUTABLE and r.result == "fail"
                   and r.evidence_id in structural]
    bounds: dict[tuple[tuple[str, str], str], float] = {}
    for rec in active:
        if rec.oracle_error_bound is not None:
            key = (evaluator_of(rec), rec.independence_group)
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
        for item in (*rec.known_limitations, f"covers only: {about(rec)[0].scope}"):
            _once(limits, item)
    if any(r.oracle_category == "FORMAL_CHECK" for r in passing):
        _once(limits, "a proof holds relative to its formalisation, which may not capture the intended requirement")
    if any(r.oracle_category == "PARTIAL_FALSIFIER" for r in passing):
        _once(limits, "partial evidence: a passing invariant does not establish general correctness")
    if structural:
        _once(limits, "a check with no recorded negative control is a structural protection, not an active check")

    roles = {**{r.evidence_id: "active" for r in active}, **{eid: "structural" for eid in structural}}
    return {"claim_id": claim.claim_id, "predicate_id": claim.predicate_id, "scope": claim.scope,
            "subject_bound": subject is not None, "breadth": claim.breadth, "estimand": claim.estimand,
            "result": result, "reasons": reasons,
            "limits": limits, "independent_groups": voices, "active_evidence": [r.evidence_id for r in active],
            "refuting_evidence": [r.evidence_id for r in refuting], "structural": structural, "leads": leads,
            "identification": "identified" if identified else "not_identified",
            "oracle_error_bound": sum(bounds.values()) if identified else None,
            "evidence": [{"evidence_id": r.evidence_id, "category": r.oracle_category, "result": r.result,
                          "role": roles.get(r.evidence_id, "lead" if r.evidence_id in leads else "not_support")}
                         for r in cited]}
