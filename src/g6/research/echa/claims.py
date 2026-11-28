# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The claim rule (ECHA spec 2.2 and 2.3): a claim's result is computed from its evidence, never written by hand.

``supported`` needs an active check that passed. A check is active only if it can be shown to fail: an executable check
with no recorded negative control is a structural protection (ruling R-1) and supports nothing. A review by a model or a
person is a lead, never support, and reviews that share a group are one voice. A partial invariant that passed supports a
claim about the cases it covered and never a general one. An independent failure refutes the claim on its scope. Evidence
about another standard, another predicate or a fixture with a recorded incident does not count. A claim about true
correctness is not identified unless every active oracle has an independent, population-matched calibration receipt,
and the bounds of separate oracles add. Supplied numbers remain declarations. Positive true correctness also needs
a sampling-adjusted evaluator lower bound from those receipts, corrected by their errors, strictly above its target.
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

from fractions import Fraction
import math
import json
import re
from typing import Any

from .custody import about, evaluator_of, fixture_contaminated
from .records import ClaimRecord, EvidenceRecord, premise_text

EXECUTABLE = ("FORMAL_CHECK", "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER")
_SUBJECT = re.compile(r"sha256:([0-9a-f]{64})\Z")


def _number_interval(value: float) -> tuple[Fraction, Fraction]:
    """Enclose both the stored number and its decimal spelling, without arithmetic rounding."""
    binary, decimal = Fraction(value), Fraction(str(value))
    return min(binary, decimal), max(binary, decimal)


def _round_outward(value: Fraction, *, upper: bool) -> float:
    """Render an exact bound as a float only toward the conservative side."""
    rounded = float(value)
    inward = Fraction(rounded) < value if upper else Fraction(rounded) > value
    if inward:
        rounded = math.nextafter(rounded, math.inf if upper else -math.inf)
    return rounded


def subject_artefact(claim: ClaimRecord) -> str | None:
    """The artefact a claim's subject names (``sha256:<hex>``), or None when it names no bytes."""
    found = _SUBJECT.match(claim.subject.strip())
    return found.group(1) if found else None


def _custody_refused(reasons: list[str], *refusals: tuple[tuple[str, str], ...]) -> bool:
    """Carry every receipt role's custody codes to the verdict, and thus its trace."""
    for refused in refusals:
        for code, _why in refused:
            _once(reasons, code)
    return any(refusals)


def _control_holds(ledger: Any, rec: EvidenceRecord, contaminated: set, reasons: list[str]) -> bool:
    """Whether the check's negative control is in the ledger, is one, failed on its injected defect, concerns the same
    standard and predicate as the check (each read through its custody binding, which it agrees with), was taken by
    the same evaluator, compared by its bytes (``evaluator_of``), and was not taken against a compromised fixture.
    ``rec`` is a cited check, whose own binding ``evaluate_claim`` has read already."""
    if not rec.negative_control_ref:
        return False
    try:
        control = EvidenceRecord.from_dict(ledger.get("evidence", rec.negative_control_ref))
    except (KeyError, ValueError, TypeError, RecursionError, MemoryError):
        return False
    (of_control, control_refused), (of_check, check_refused) = about(control), about(rec)
    refused = _custody_refused(reasons, control_refused, check_refused)
    return (not refused and control.purpose == "negative_control" and control.result == "fail"
            and bool(premise_text(of_control.scope)) and bool(premise_text(of_check.scope))
            and of_control.standard == of_check.standard
            and not fixture_contaminated(of_control.fixture, contaminated)
            and of_control.predicates == of_check.predicates and evaluator_of(control) == evaluator_of(rec)
            and control.oracle_category in EXECUTABLE)


def _once(items: list[str], item: str) -> None:
    if item not in items:
        items.append(item)


def _premise(ledger: Any, rec: EvidenceRecord, claim: ClaimRecord, ref: str, purpose: str,
             contaminated: set, reasons: list[str]) -> EvidenceRecord | None:
    """Resolve independent executable support for an exact record and population, never from explanatory text.

    This uses the research ledger's existing trusted-input/independence boundary. It issues no product status.
    Both evaluator identities must name bytes; different declared labels cannot establish separate assessors.
    """
    try:
        premise = EvidenceRecord.from_dict(ledger.get("evidence", ref))
        parts, refused = about(premise)
        original, original_refused = about(rec)
        custody_refused = _custody_refused(reasons, refused, original_refused)
        controlled = _control_holds(ledger, premise, contaminated, reasons)
        if (custody_refused or premise.evidence_id != ref or premise.purpose != purpose
                or premise.result != "pass" or premise.oracle_category not in EXECUTABLE
                or not premise.independent or premise.independence_group == rec.independence_group
                or not original.check_sha or not parts.check_sha or parts.check_sha == original.check_sha
                or parts.artefact != original.artefact or parts.standard != original.standard
                or parts.predicates != original.predicates or premise.applies_to_sha256 != rec.sha256
                or not premise_text(premise.population) or not premise_text(premise.sample_unit)
                or premise_text(premise.population) != premise_text(claim.population)
                or premise_text(premise.sample_unit) != premise_text(claim.sample_unit)
                or premise_text(rec.population) != premise_text(claim.population)
                or premise_text(rec.sample_unit) != premise_text(claim.sample_unit)
                or fixture_contaminated(parts.fixture, contaminated)
                or not controlled):
            return None
        if purpose == "scope_mapping" and (
                not premise_text(rec.checked_statement)
                or not premise_text(parts.scope) or not premise_text(claim.scope)
                or premise_text(premise.checked_statement) != premise_text(rec.checked_statement)
                or premise_text(parts.scope) != premise_text(claim.scope)):
            return None
        return premise
    except (KeyError, ValueError, TypeError, RecursionError, MemoryError):
        return None


def evaluate_claim(ledger: Any, claim_id: str) -> dict:
    try:
        claim = ClaimRecord.from_dict(ledger.get("claim", claim_id))
    except (RecursionError, MemoryError):
        raise json.JSONDecodeError("a JSON record exceeds decoder limits", "", 0) from None
    contaminated = ledger.contaminated_fixtures()
    subject = subject_artefact(claim)
    reasons: list[str] = []
    cited: list[EvidenceRecord] = []
    unplaced_failures: list[str] = []
    for eid in claim.evidence_ids:
        try:
            rec = EvidenceRecord.from_dict(ledger.get("evidence", eid))
        except (KeyError, ValueError, TypeError, RecursionError, MemoryError):
            _once(reasons, "evidence_not_traceable")
            continue
        parts, disagree = about(rec)
        bound = parts.artefact or None
        if _custody_refused(reasons, disagree):
            if rec.purpose == "check" and rec.result == "fail" and rec.oracle_category in EXECUTABLE:
                unplaced_failures.append(rec.evidence_id)
        elif bound is not None and bound != subject:   # evidence on other bytes is never this subject's
            _once(reasons, "evidence_for_another_subject")
        elif subject is not None and bound is None:   # a claim about bytes counts only evidence taken on them
            _once(reasons, "evidence_not_bound_to_subject")
            if rec.purpose == "check" and rec.result == "fail" and rec.oracle_category in EXECUTABLE:
                unplaced_failures.append(rec.evidence_id)
        elif rec.purpose != "check":
            _once(reasons, "cited_record_is_a_control_not_a_check")
        elif parts.standard != claim.standard_sha256:
            _once(reasons, "evidence_for_another_standard")
        elif parts.predicates != (claim.predicate_id,):
            _once(reasons, "evidence_for_another_predicate")
        elif fixture_contaminated(parts.fixture, contaminated):
            _once(reasons, "contaminated_fixture")
        else:
            cited.append(rec)

    active: list[EvidenceRecord] = []
    structural: list[str] = []
    leads: list[str] = list(unplaced_failures)
    for rec in cited:
        if rec.oracle_category == "HEURISTIC_REVIEW":
            if rec.result == "fail":
                leads.append(rec.evidence_id)
        elif rec.oracle_category in EXECUTABLE and rec.result in ("pass", "fail"):
            if _control_holds(ledger, rec, contaminated, reasons):   # a formal check too, as the issuer requires
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
    unexplained = unplaced_failures + [r.evidence_id for r in cited if r.oracle_category in EXECUTABLE and r.result == "fail"
                                      and r.evidence_id in structural]
    bounds: dict[tuple[tuple[str, str], str], float] = {}
    calibrations: dict[str, EvidenceRecord] = {}
    for rec in active:
        cal = _premise(ledger, rec, claim, rec.calibration_ref, "calibration", contaminated, reasons)
        if cal is not None and rec.oracle_error_bound is not None and cal.oracle_error_bound == rec.oracle_error_bound:
            calibrations[rec.evidence_id] = cal
            key = (evaluator_of(rec), rec.independence_group)
            bounds[key] = max(bounds.get(key, 0.0), float(rec.oracle_error_bound))
    identified = bool(active) and all(r.evidence_id in calibrations for r in active)
    # Upper errors and lower evaluator outcomes enclose both decimal and binary readings of every declaration.
    # Keep the sum and subtraction exact; float display must never shrink errors or enlarge corrected outcomes.
    pooled_exact = sum((_number_interval(v)[1] for v in bounds.values()), Fraction()) if identified else None
    pooled_bound = _round_outward(pooled_exact, upper=True) if pooled_exact is not None else None
    lowers = [calibrations[r.evidence_id].evaluator_lower_bound for r in passing if r.evidence_id in calibrations]
    corrected_lower = (_round_outward(max(Fraction(), min(_number_interval(v)[0] for v in lowers) - pooled_exact),
                                     upper=False)
                       if identified and passing and all(v is not None for v in lowers) else None)
    general = False
    for rec in passing:
        mapping = _premise(ledger, rec, claim, rec.scope_mapping_ref, "scope_mapping", contaminated, reasons)
        if rec.oracle_category == "FORMAL_CHECK" and mapping is not None:
            general = True

    if refuting:
        result = "unsupported"
    elif passing:
        result = "supported"
        if claim.breadth == "general" and not general:
            result = "inconclusive"
            _once(reasons, "checked_statement_or_scope_mapping_missing")
            if not any(r.oracle_category == "FORMAL_CHECK" for r in passing):
                _once(reasons, "partial_evidence_cannot_support_a_general_claim")
        if voices < claim.requires_independent_groups:
            result = "inconclusive"
            _once(reasons, "independence_not_met")
        if unexplained:  # a check that failed and was never shown able to fail is not outweighed by one that passed
            result = "inconclusive"
            _once(reasons, "unexplained_failure")
        if premise_text(claim.estimand) == "true_correctness":
            if not identified:
                result = "inconclusive"
                _once(reasons, "oracle_error_not_calibrated")
                if any(r.oracle_error_bound is None for r in active):
                    _once(reasons, "oracle_error_not_bounded")
            if claim.target is None or corrected_lower is None or corrected_lower <= claim.target:
                result = "inconclusive"
                _once(reasons, "corrected_lower_bound_does_not_exceed_target")
    else:
        result = "inconclusive"
        _once(reasons, "no_active_evidence")
        if structural:
            _once(reasons, "not_an_active_check")
        if cited and all(r.oracle_category == "HEURISTIC_REVIEW" for r in cited):
            _once(reasons, "only_heuristic_evidence")

    if result == "supported" and (not premise_text(claim.scope) or not premise_text(claim.population)
                                   or not premise_text(claim.sample_unit)):
        result = "inconclusive"
        _once(reasons, "claim_text_premise_missing")

    limits: list[str] = list(claim.known_limitations)
    for rec in active:
        for item in (*rec.known_limitations, f"covers only: {about(rec)[0].scope}"):
            _once(limits, item)
    if any(r.oracle_category == "FORMAL_CHECK" for r in passing):
        _once(limits, "a checked statement holds relative to its formalisation, which may not capture the intended requirement")
    if any(r.oracle_category == "PARTIAL_FALSIFIER" for r in passing):
        _once(limits, "partial evidence: a passing invariant does not establish general correctness")
    if structural:
        _once(limits, "a check with no recorded negative control is a structural protection, not an active check")

    roles = {**{r.evidence_id: "active" for r in active}, **{eid: "structural" for eid in structural}}
    return {"claim_id": claim.claim_id, "predicate_id": claim.predicate_id, "scope": claim.scope,
            "subject_bound": subject is not None, "breadth": claim.breadth, "estimand": claim.estimand,
            "effective_breadth": "general" if claim.breadth == "general" and general else "covered_cases",
            "result": result, "reasons": reasons,
            "limits": limits, "independent_groups": voices, "active_evidence": [r.evidence_id for r in active],
            "refuting_evidence": [r.evidence_id for r in refuting], "structural": structural, "leads": leads,
            "identification": "identified" if identified else "not_identified",
            "oracle_error_bound": pooled_bound,
            "declared_oracle_error_bounds": {r.evidence_id: r.oracle_error_bound for r in active},
            "target": claim.target, "corrected_lower_bound": corrected_lower,
            "evidence": [{"evidence_id": r.evidence_id, "category": r.oracle_category, "result": r.result,
                          "role": roles.get(r.evidence_id, "lead" if r.evidence_id in leads else "not_support")}
                         for r in cited]}
