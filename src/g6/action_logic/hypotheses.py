# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Hypothesis reflection controls (G6-INV-04/09, §11; AT-08, AT-12).

Deterministic, LLM-free. Dörner's operators "guard their competence": they ignore evidence
that contradicts the current plan (AT-08) and repeat strategies that already failed (AT-12).
These controls check for that in the records a caller passes in:

- ``update_on_outcome`` applies the reflection rules of §11. Crucially, a *successful* outcome
  never validates the model (§11): confirmation lands in SUPPORTED with a capped confidence,
  never a "proven" absorbing state. Disconfirmation FALSIFIES, and FALSIFIED is sticky — a
  later success cannot silently resurrect a model already shown wrong.
- ``disconfirming_evidence_severity`` (AT-08): an active hypothesis carrying disconfirming
  evidence pauses its dependent actions for re-evaluation (review), and its confidence must
  have been reduced.
- ``reauthorisation_severity`` (AT-12): an action relying on a FALSIFIED hypothesis is blocked
  unless a NEW, well-formed, evidence-backed hypothesis is supplied.

Limits (audit 003, AL-006 / AL-012). This is not learning and nothing is remembered: each
function returns a new record and the caller must store it; there is no registry here
(``reauthorisation_severity_from_registry`` takes a caller's mapping). Confidence moves by
fixed heuristic steps (+0.2 / +0.1 capped, x0.7, x0.5), not by calibration. Outcome labels and
evidence are the caller's free text: a replacement hypothesis is checked for a new statement and
new non-blank evidence, but nothing resolves that evidence to an observation.
"""
from __future__ import annotations

from typing import Any, Mapping

from g6.action_logic.schema import Hypothesis, HypothesisStatus, Outcome

# Reason codes (registered in core/error_codes.yaml).
E_DISCONFIRMING_PENDING = "G6_E_DISCONFIRMING_EVIDENCE_PENDING"
E_FALSIFIED_REUSED = "G6_E_FALSIFIED_HYPOTHESIS_REUSED"

# Confidence ceiling for a corroborated (but unproven) hypothesis — success != validation (§11).
_SUPPORTED_CEILING = 0.9
_PARTIAL_CEILING = 0.75
_FALSIFIED_CONFIDENCE = 0.1


def _clamp(x: float, lo: float = 0.05, hi: float = 0.95) -> float:
    return max(lo, min(hi, x))


def update_on_outcome(
    hyp: Hypothesis, outcome: Outcome, *, evidence: str = ""
) -> Hypothesis:
    """Apply the §11 reflection rules to a hypothesis after an observation window.

    FALSIFIED is sticky: once a model is shown wrong, a later confirmation cannot un-falsify
    it (you must supply a new hypothesis — see :func:`reauthorisation_severity`). A confirmed
    hypothesis becomes SUPPORTED (corroborated, still falsifiable), never "proven". The
    confidence steps are fixed heuristics, not calibrated estimates; ``outcome`` is taken as
    the caller states it.
    """
    if hyp.status is HypothesisStatus.FALSIFIED:
        # Sticky: record the new evidence but do not resurrect a disproven model.
        if outcome in (Outcome.DISCONFIRMED, Outcome.HARMFUL) and evidence:
            return _with(
                hyp,
                confidence=_clamp(hyp.confidence),
                disconfirming=hyp.disconfirming_evidence + (evidence,),
            )
        return _with(hyp, confidence=_clamp(hyp.confidence))

    if outcome in (Outcome.DISCONFIRMED, Outcome.HARMFUL):
        return _with(
            hyp,
            status=HypothesisStatus.FALSIFIED,
            confidence=min(hyp.confidence, _FALSIFIED_CONFIDENCE),
            disconfirming=hyp.disconfirming_evidence + ((evidence,) if evidence else ()),
        )
    if outcome is Outcome.CONFIRMED:
        return _with(
            hyp,
            status=HypothesisStatus.SUPPORTED,
            confidence=_clamp(min(_SUPPORTED_CEILING, hyp.confidence + 0.2)),
            supporting=hyp.supporting_evidence + ((evidence,) if evidence else ()),
        )
    if outcome is Outcome.PARTIALLY_CONFIRMED:
        return _with(
            hyp,
            status=HypothesisStatus.SUPPORTED,
            confidence=_clamp(min(_PARTIAL_CEILING, hyp.confidence + 0.1)),
            supporting=hyp.supporting_evidence + ((evidence,) if evidence else ()),
        )
    if outcome is Outcome.CONFOUNDED:
        # The result cannot credit the model — another intervention may explain it (§11).
        return _with(
            hyp, status=HypothesisStatus.WEAKENED, confidence=_clamp(hyp.confidence * 0.7)
        )
    # INCONCLUSIVE: no credit, no status change — but an operational hypothesis is never
    # certain (§11: the model may always be wrong), so confidence is still clamped < 1.0.
    return _with(hyp, confidence=_clamp(hyp.confidence))


def apply_disconfirming_evidence(hyp: Hypothesis, note: str) -> Hypothesis:
    """Record disconfirming evidence and reduce confidence (AT-08), without yet falsifying.

    Distinguishes "evidence against" (weakens, pauses dependent actions) from a confirmed
    disconfirming *outcome* (falsifies — use :func:`update_on_outcome`)."""
    if hyp.status is HypothesisStatus.FALSIFIED:
        return _with(hyp, disconfirming=hyp.disconfirming_evidence + (note,))
    return _with(
        hyp,
        status=HypothesisStatus.WEAKENED,
        confidence=_clamp(hyp.confidence * 0.5),
        disconfirming=hyp.disconfirming_evidence + (note,),
    )


def disconfirming_evidence_severity(
    hyp: Hypothesis,
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """AT-08: an in-use hypothesis carrying disconfirming evidence pauses dependent actions.

    Returns ``review`` (pause / re-evaluate) when disconfirming evidence is present and the
    hypothesis has not yet been formally falsified; ``None`` otherwise. (A falsified
    hypothesis is handled at reauthorisation — :func:`reauthorisation_severity`.)
    """
    if hyp.disconfirming_evidence and hyp.status is not HypothesisStatus.FALSIFIED:
        return "review", [E_DISCONFIRMING_PENDING], {
            "kind": "hypothesis",
            "hypothesis_id": hyp.hypothesis_id,
            "status": hyp.status.value,
            "confidence": hyp.confidence,
            "disconfirming_evidence": list(hyp.disconfirming_evidence),
        }
    return None, [], None


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _is_valid_replacement(relied: Hypothesis, new: Hypothesis | None) -> bool:
    """A replacement is a different hypothesis with evidence of its own (audit 003, AL-006).

    The archive accepted any non-empty evidence tuple, so the falsified hypothesis recast under
    a new id, a blank string, or the falsified hypothesis's own evidence all passed. The
    evidence is still declared text: nothing here resolves it to an observation.
    """
    if new is None or new.hypothesis_id == relied.hypothesis_id:
        return False
    if new.status is HypothesisStatus.FALSIFIED or not new.is_well_formed():
        return False
    if _same_text(new.statement, relied.statement):
        return False
    return any(
        e.strip() and not any(_same_text(e, old) for old in relied.supporting_evidence)
        for e in new.supporting_evidence
    )


def reauthorisation_severity(
    *, relied_hypothesis: Hypothesis, new_hypothesis: Hypothesis | None = None
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """AT-12: block an action relying on a FALSIFIED hypothesis unless a NEW evidence-backed
    hypothesis is supplied (§8 "Repeating a failed strategy -> Hard block on reauthorisation").
    """
    if relied_hypothesis.status is not HypothesisStatus.FALSIFIED:
        return None, [], None
    item = {
        "kind": "reauthorisation",
        "relied_hypothesis_id": relied_hypothesis.hypothesis_id,
        "relied_status": relied_hypothesis.status.value,
        "new_hypothesis_supplied": new_hypothesis is not None,
    }
    if _is_valid_replacement(relied_hypothesis, new_hypothesis):
        item["replacement_accepted"] = True
        return None, [], item
    item["replacement_accepted"] = False
    return "block", [E_FALSIFIED_REUSED], item


def reauthorisation_severity_from_registry(
    relied_on_id: str,
    registry: Mapping[str, Hypothesis],
    *,
    new_hypothesis: Hypothesis | None = None,
) -> tuple[str | None, list[str], dict[str, Any] | None]:
    """Registry convenience: look the relied-on hypothesis up by id, then apply AT-12.

    An unknown id has no recorded falsification, so it does not block here (the well-formedness
    of the supplied hypothesis is enforced elsewhere)."""
    relied = registry.get(relied_on_id)
    if relied is None:
        return None, [], None
    return reauthorisation_severity(relied_hypothesis=relied, new_hypothesis=new_hypothesis)


def _with(
    hyp: Hypothesis,
    *,
    status: HypothesisStatus | None = None,
    confidence: float | None = None,
    supporting: tuple[str, ...] | None = None,
    disconfirming: tuple[str, ...] | None = None,
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hyp.hypothesis_id,
        statement=hyp.statement,
        confidence=hyp.confidence if confidence is None else confidence,
        supporting_evidence=hyp.supporting_evidence if supporting is None else supporting,
        disconfirming_evidence=hyp.disconfirming_evidence if disconfirming is None else disconfirming,
        falsification_condition=hyp.falsification_condition,
        status=hyp.status if status is None else status,
    )


__all__ = [
    "E_DISCONFIRMING_PENDING",
    "E_FALSIFIED_REUSED",
    "update_on_outcome",
    "apply_disconfirming_evidence",
    "disconfirming_evidence_severity",
    "reauthorisation_severity",
    "reauthorisation_severity_from_registry",
]
