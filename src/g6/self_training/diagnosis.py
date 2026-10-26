# SPDX-License-Identifier: Apache-2.0
"""``diagnose``: a deterministic first classification of why an audit fell short, with a suggested fix.

Fixed rules, in order: the tier and shape score first, then patterns in the invocation error, then a generic
fallback. ``basis`` names the rule that fired. There is no confidence number: the archive attached hard-coded
"confidences" (1.0, 0.9, 0.85, 0.8, 0.5) that were labels, not measurements, and a number shaped like a probability
can be passed on as one (audit 008, ST-012). Paraphrased errors that contain none of the patterns fall through to
``runtime_error``; this is a floor, not an accuracy claim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from g6.self_training.schema import PUBLICATION_THRESHOLD, UNREVIEWED_TIER_CAP, AuditResult

FailureClass = Literal[
    "missing_block",      # the component or its entry point was not found
    "import_error",       # the module does not import
    "type_mismatch",      # the input reached the component as the wrong type
    "validation_error",   # the input failed validation
    "external_dep",       # a service or model it needs was unavailable
    "timeout",            # it ran past its time limit
    "runtime_error",      # anything else that raised
    "rubric_gap",         # it ran, and no rubric assertion checked its output
    "rubric_unreviewed",  # its shape checks ran, but nobody reviewed the rubric, so the tier stays at T3
    "low_shape_score",    # a reviewed rubric's shape checks scored below 0.8 (shape, not quality; ST-010)
    "none",               # it passes its reviewed shape checks
]
Basis = Literal["tier_and_score", "error_pattern", "fallback"]


@dataclass(frozen=True)
class Diagnosis:
    component: str
    failure_class: FailureClass
    description: str
    suggested_fix: str
    basis: Basis          # which rule chose the class; not a probability (audit 008, ST-012)
    blocking: bool = True


_PATTERNS: tuple[tuple[str, FailureClass, str, str], ...] = (
    (r"No AIBlock subclass found", "missing_block", "the component has no block implementation",
     "implement a block with an infer() method"),
    (r"Cannot import", "import_error", "the module fails to import", "fix the missing dependency or import cycle"),
    (r"ModuleNotFoundError", "import_error", "a required package is not installed",
     "add the missing package to the dependencies"),
    (r"'dict' object has no attribute", "type_mismatch", "a raw dict reached a typed infer()",
     "build the typed input before invoking"),
    (r"VALIDATION_ERROR", "validation_error", "the input fails validation",
     "fix the rubric's test_input to match the input schema"),
    (r"missing.*required.*argument", "validation_error", "a required field is missing from the test input",
     "add the required fields to the rubric's test_input"),
    (r"TIMEOUT", "timeout", "the invocation exceeded its time limit", "add a fast path or raise the limit"),
    (r"APIConnectionError|Connection refused|ECONNREFUSED", "external_dep", "an external service was unavailable",
     "add a fallback or a local stand-in for the audit"),
    (r"model.*not found|ollama", "external_dep", "the model was not available",
     "configure an available model or an offline fallback"),
    (r"not found.*aiblock|no aiblock", "missing_block", "the component has no block (infrastructure by design?)",
     "classify it as infrastructure"),
)


def diagnose(component: str, error: str, tier: int, score: float, *, evaluable: bool = True,
             reviewed: bool = True) -> Diagnosis:
    """Classify one audit. ``evaluable`` and ``reviewed`` describe the rubric it ran against (see ``RubricScore``)."""
    def made(cls: FailureClass, description: str, fix: str, basis: Basis, blocking: bool = True) -> Diagnosis:
        return Diagnosis(component, cls, description, fix, basis, blocking)

    if tier >= PUBLICATION_THRESHOLD:
        return made("none", "passes its reviewed shape checks (shape, not quality)", "", "tier_and_score", False)
    if not error.strip() and tier >= UNREVIEWED_TIER_CAP:
        if not evaluable:
            return made("rubric_gap", "it runs, and no rubric assertion checked its output",
                        "write a rubric with assertions and a realistic test_input", "tier_and_score")
        if not reviewed:
            return made("rubric_unreviewed", f"its shape checks scored {score:.2f}, but the rubric has no reviewer",
                        "have a person review the rubric and record it in provenance.reviewed_by", "tier_and_score")
    if not error.strip() and evaluable and tier >= 2 and score < 0.8:
        return made("low_shape_score", f"its shape checks scored {score:.2f}, below 0.8",
                    "fix the component's output, or the rubric's expectations if they are wrong", "tier_and_score")
    for pattern, cls, description, fix in _PATTERNS:
        if re.search(pattern, error, re.IGNORECASE):
            return made(cls, description, fix, "error_pattern")
    return made("runtime_error", f"general failure: {error[:100]}", "inspect the component's infer() for the fault",
                "fallback")


def diagnose_audit(result: AuditResult) -> Diagnosis:
    """:func:`diagnose` for an :class:`AuditResult`."""
    return diagnose(result.component, result.error, result.tier, result.score, evaluable=result.evaluable,
                    reviewed=result.rubric_reviewed)


__all__ = ["Basis", "Diagnosis", "FailureClass", "diagnose", "diagnose_audit"]
