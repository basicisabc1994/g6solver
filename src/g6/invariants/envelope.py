# SPDX-License-Identifier: Apache-2.0
"""``check_envelope``: does a result envelope say only what its own fields support? (audit 031 §11)

The archive's invariants component had 14 verifiers, a category/tier system and an MCP facade; its ``verified`` could
be reached with an empty trace or self-certified by decorator evidence (INV-001/002). None of that is ported. What is
here is the one check every surface needs, against the fields ``g6.core.reliability_envelope`` defines:

* ``completion_state`` is one of the three reliability labels (and agrees with ``reliability_label`` when both exist);
* a result that is not ``verified`` carries a ``warning_card`` whose ``code`` is a stable ``G6_E_*`` code;
* a ``verified`` result carries non-empty ``evidence``;
* the ``request_id`` and ``run_id`` keys are present;
* ``degraded``, when present, is true exactly when the result is not ``verified``.

It checks the envelope's consistency, not the truth of its evidence: a ``verified`` with fabricated evidence passes.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, get_args

from g6.core.reliability_envelope import ReliabilityLabel

#: The reliability labels a ``completion_state`` may take.
RELIABILITY_LABELS: frozenset[str] = frozenset(get_args(ReliabilityLabel))
#: A stable error code.
ERROR_CODE_PATTERN = re.compile(r"^G6_E_[A-Z0-9_]+$")


@dataclass(frozen=True)
class Violation:
    """One rule an envelope breaks: which rule, on which field, and what was found."""

    rule: str
    field: str
    message: str


def check_envelope(payload: Mapping[str, Any]) -> list[Violation]:
    """Every rule ``payload`` breaks (empty when it breaks none)."""
    if not isinstance(payload, Mapping):
        return [Violation("not_an_envelope", "", f"expected a mapping, got {type(payload).__name__}")]
    found: list[Violation] = []
    state = payload.get("completion_state")
    if state not in RELIABILITY_LABELS:
        found.append(Violation("unknown_state", "completion_state",
                               f"{state!r} is not one of {sorted(RELIABILITY_LABELS)}"))
    label = payload.get("reliability_label")
    if label is not None and label != state:
        found.append(Violation("label_mismatch", "reliability_label",
                               f"reliability_label {label!r} disagrees with completion_state {state!r}"))
    if state in RELIABILITY_LABELS and state != "verified":
        card = payload.get("warning_card")
        code = card.get("code") if isinstance(card, Mapping) else None
        if not isinstance(card, Mapping):
            found.append(Violation("missing_warning_card", "warning_card",
                                   f"a {state} result must carry a warning card"))
        elif not (isinstance(code, str) and ERROR_CODE_PATTERN.match(code)):
            found.append(Violation("unstable_code", "warning_card.code", f"{code!r} is not a G6_E_* code"))
    if state == "verified":
        evidence = payload.get("evidence")
        if not (isinstance(evidence, list | tuple) and evidence):
            found.append(Violation("verified_without_evidence", "evidence",
                                   "a verified result must carry evidence"))
    for key in ("request_id", "run_id"):
        if key not in payload:
            found.append(Violation("missing_id", key, f"the envelope has no {key!r} key"))
    if "degraded" in payload and state in RELIABILITY_LABELS:
        if payload["degraded"] is not (state != "verified"):
            found.append(Violation("degraded_mismatch", "degraded",
                                   f"degraded={payload['degraded']!r} contradicts completion_state {state!r}"))
    return found


def assert_envelope(payload: Mapping[str, Any]) -> None:
    """Raise ``AssertionError`` listing every rule ``payload`` breaks (for any component's tests)."""
    found = check_envelope(payload)
    if found:
        raise AssertionError("envelope breaks " + "; ".join(f"{v.rule} ({v.field}): {v.message}" for v in found))


def nonvacuous_pass(results: Iterable[tuple[bool, bool]]) -> bool:
    """Whether a set of checks passed without passing vacuously: each item is ``(passed, blocking)``; True only when at
    least one blocking check ran and every blocking check passed (audit 031 INV-001: a class that checks nothing must
    never read as a pass)."""
    blocking = [passed for passed, is_blocking in results if is_blocking]
    return bool(blocking) and all(blocking)


__all__ = ["ERROR_CODE_PATTERN", "RELIABILITY_LABELS", "Violation", "assert_envelope", "check_envelope",
           "nonvacuous_pass"]
