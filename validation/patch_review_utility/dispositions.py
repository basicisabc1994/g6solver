# SPDX-License-Identifier: Apache-2.0
"""Each arm's result as accepted or not accepted (PROTOCOL.md, "Arms": everything that is not acceptance is "not
accepted"), with the kind of not accepted kept apart.

A disposition is ``{"accepted", "category", "error_kind", "detail"}``. ``category`` is ``accept`` or one of the five
tallies the protocol names; ``error_kind`` says, for ``tool_error_or_refusal``, which it was (``tool_error``,
``refusal``, ``a5_report_incomplete``, ...). Tool errors and refusals are not accepted, and are counted, never dropped.
"""
from __future__ import annotations

ARMS = ("tests", "self_review", "g6", "direct_checks")
ACCEPT = "accept"
DEFECT = "defect_found"
ABSTAIN = "insufficient_evidence"
ESCALATE = "escalation"
INTEGRITY = "test_integrity_compromised"
ERROR = "tool_error_or_refusal"
NOT_ACCEPTED = (DEFECT, ABSTAIN, ESCALATE, INTEGRITY, ERROR)
CATEGORIES = (ACCEPT, *NOT_ACCEPTED)
#: G6's four technical outcomes and the tally each falls in when it is not acceptance.
G6_OUTCOMES = {"defect_detected": DEFECT, "insufficient_evidence": ABSTAIN, "test_integrity_compromised": INTEGRITY,
               "supported_within_scope": ABSTAIN}


def make(category: str, detail: str = "", *, error_kind: str = "") -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    if (category == ERROR) != bool(error_kind):
        raise ValueError("an error kind is given exactly when the category is a tool error or refusal")
    return {"accepted": category == ACCEPT, "category": category, "error_kind": error_kind, "detail": detail}


def tally(found: list[dict | None]) -> dict:
    """Counts per category, the error kinds, and how many dispositions are missing (``None``)."""
    counts = {category: 0 for category in CATEGORIES}
    kinds: dict[str, int] = {}
    missing = 0
    for item in found:
        if item is None:
            missing += 1
            continue
        counts[item["category"]] += 1
        if item.get("error_kind"):
            kinds[item["error_kind"]] = kinds.get(item["error_kind"], 0) + 1
    return {**counts, "error_kinds": kinds, "missing": missing}
