# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The one implicit-signal risk classifier: R0 to R3 from the words of a task (TS §31; production-path S11).

It was ``mvp.design.catalogue.classify_risk``. It lives here, standard library only, so the workforce can derive a
standard from it without loading the design block (whose package imports the model router), and the catalogue
delegates to it: one list of signals, extended in place (E10b, 2026-10-05) with TS §31's words it lacked. Matching is
on the lowercased text, as the catalogue always did, so it errs toward the higher tier.
"""
from __future__ import annotations

from typing import Any

#: Each tier's signal words, checked from the highest tier down (the first tier with a match wins), with the reason the
#: design assessment shows. E10b adds TS §31's: "send" (an external action), "delete" (destructive; with production or
#: data words, high consequence), "payment" and "deploy" (consequential). "deploy" now also catches "deployment",
#: which was ordinary (TS §6.2: production deployments are consequential).
TIER_TERMS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("R3", ("irreversible", "clinical decision", "legal filing", "financial transaction", "destructive", "credential",
            "production access", "delete production", "delete data", "delete customer", "drop table",
            "drop database"),
     "critical or irreversible action terms detected"),
    ("R2", ("legal", "medical", "clinical", "financial", "security", "compliance", "production", "public claim",
            "hiring", "tax", "investment", "send", "delete", "payment", "deploy"),
     "high-stakes domain or public/regulated decision terms detected"),
    ("R1", ("internal", "prototype", "project plan", "analysis", "deployment", "code", "engineering"),
     "moderate-stakes internal, code, deployment, or analysis task"),
    ("R0", ("brainstorm", "style", "summary", "draft"), "low-stakes draft, summary, or brainstorming task"),
)
#: What the classifier answers when no word matches. A default is not a signal: a derived standard never cites it.
DEFAULT_TIER = "R1"
DEFAULT_REASON = "defaulted to moderate stakes because task impact is not explicitly low"


def classify(text: Any) -> tuple[str, list[str]]:
    """The tier the words of ``text`` signal, and why (the default when none does)."""
    lowered = str(text).lower()
    for tier, terms, reason in TIER_TERMS:
        if any(term in lowered for term in terms):
            return tier, [reason]
    return DEFAULT_TIER, [DEFAULT_REASON]


def signals(text: Any) -> tuple[tuple[str, str], ...]:
    """Every (tier, word) ``text`` matches, highest tier first: what a derived standard cites (E10b). Empty when no
    word matches."""
    lowered = str(text).lower()
    return tuple((tier, term) for tier, terms, _ in TIER_TERMS for term in terms if term in lowered)


__all__ = ["DEFAULT_REASON", "DEFAULT_TIER", "TIER_TERMS", "classify", "signals"]
