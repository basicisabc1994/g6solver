# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The one implicit-signal risk classifier: R0 to R3 from the words of a task (TS §31; production-path S11).

The catalogue and workforce share the vocabulary in risk_terms. The original
matching logic remains a reproducible floor; the guarded semantic reader may
only raise its tier. No model or an unusable reading leaves that floor in place.
"""
from __future__ import annotations
from g6.core.semantic import scoped

from typing import Any

#: Each tier's signal words, checked from the highest tier down (the first tier with a match wins), with the reason the
#: design assessment shows. E10b adds TS §31's: "send" (an external action), "delete" (destructive; with production or
#: data words, high consequence), "payment" and "deploy" (consequential). "deploy" now also catches "deployment",
#: which was ordinary (TS §6.2: production deployments are consequential).
from g6.core.risk_terms import TIER_TERMS, floor_signals
from g6.core import semantic
#: What the classifier answers when no word matches. A default is not a signal: a derived standard never cites it.
DEFAULT_TIER = "R1"
DEFAULT_REASON = "defaulted to moderate stakes because task impact is not explicitly low"


def _floor_classify(text: Any) -> tuple[str, list[str]]:
    """The tier the words of ``text`` signal, and why (the default when none does)."""
    found = floor_signals(str(text))
    if found:
        tier = found[0][0]
        return tier, [next(reason for t, _, reason in TIER_TERMS if t == tier)]
    return DEFAULT_TIER, [DEFAULT_REASON]


def _floor_signals(text: Any) -> tuple[tuple[str, str], ...]:
    """Every (tier, word) ``text`` matches, highest tier first: what a derived standard cites (E10b). Empty when no
    word matches."""
    return floor_signals(str(text))


__all__ = ["DEFAULT_REASON", "DEFAULT_TIER", "TIER_TERMS", "classify", "read_signals", "signals"]


@scoped
def classify(text: Any) -> tuple[str, list[str]]:
    floor, reasons = _floor_classify(text)
    reading = semantic.decide("risk", str(text), floor, ("R0", "R1", "R2", "R3"))
    if reading.value != floor:
        reasons = [*reasons, "semantic reading raised risk from " + floor + " to " + reading.value]
    return reading.value, reasons


@scoped
def read_signals(text: Any) -> tuple[semantic.Reading, tuple[tuple[str, str], ...]]:
    """A typed model/floor distinction beside only the literal deterministic signals."""
    found = _floor_signals(text)
    floor = max((tier for tier, _ in found), default="R1")
    reading = semantic.decide("risk", str(text), floor, ("R0", "R1", "R2", "R3"))
    return reading, found


@scoped
def signals(text: Any) -> tuple[tuple[str, str], ...]:
    """Compatibility view: literal signals plus an explicitly model-labelled addition."""
    reading, found = read_signals(text)
    literal_tier = max((tier for tier, _ in found), default="R0")
    if reading.by != "floor" and reading.model_value > literal_tier:
        return ((reading.model_value, "semantic risk signal (model reading)"), *found)
    return found
