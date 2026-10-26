# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The shareable result card: what a G6 result can honestly say about itself.

Founder, 2026-09-23: the growth harness's share prototype (section 19) "provides
an excellent example of what a strong value proposition in G6 should look like".
The card states what was CHECKED and HOW -- the reliability state and what it
means, what is qualified, how many evidence sources, whether an independent
evaluation ran and its verdict, how many alternative approaches were rejected --
and shows a stop as plainly as a pass.

One rule governs everything here: a card is printed only from values the run
RECORDED. A field that is absent, or that holds a value the envelope filled in
by default, is "not recorded", and a card with a required field not recorded is
not printable. ``card_or_gaps`` never invents, estimates or defaults a value.

This module is the single definition of the card. The envelope carries its
fields (``CARD_FIELDS``); the simulated-user round grades every run against it
(scripts/sim_users/share_card.py); a future share link would render it.
Standard library only.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

VERIFIED = "verified"
QUALIFIED_DRAFT = "qualified-draft"
BLOCKED_ESCALATED = "blocked-escalated"
STATES = (VERIFIED, QUALIFIED_DRAFT, BLOCKED_ESCALATED)
# The job orchestrator's CompletionState values; the surfaces publish the
# canonical names above. Either spelling is the same state.
_STATE_ALIASES = {"verified_ready": VERIFIED, "qualified_draft": QUALIFIED_DRAFT,
                  "blocked_escalated": BLOCKED_ESCALATED}

HEADLINES = {
    VERIFIED: "G6 VERIFIED RESULT",
    QUALIFIED_DRAFT: "G6 QUALIFIED DRAFT",
    BLOCKED_ESCALATED: "G6 STOPPED FOR A HUMAN DECISION",
}
MEANINGS = {
    VERIFIED: "checked, and every check passed",
    QUALIFIED_DRAFT: "usable, with stated limits",
    BLOCKED_ESCALATED: "G6 would not finish this without a person deciding",
}
FOOTER = "Generated and checked with G6"
PRIVATE_TASK = "Private: the task is not shown"

# Envelope keys for the four fields the product did not record before
# 2026-09-23. ``None`` / absent always means "not recorded", never zero.
EVIDENCE_SOURCES = "evidence_sources"            # int >= 0
INDEPENDENT_EVALUATION = "independent_evaluation"  # {"verdict": ..., "by": str}
ALTERNATIVES_REJECTED = "alternatives_rejected"  # int >= 0
TASK_TITLE = "task_title"                        # str chosen by the user; optional
CARD_FIELDS = (EVIDENCE_SOURCES, INDEPENDENT_EVALUATION, ALTERNATIVES_REJECTED, TASK_TITLE)

# Verdicts an independent evaluation can record.
PASSED, PASSED_WITH_LIMITS, FAILED, NOT_RUN = "passed", "passed-with-limits", "failed", "not-run"
VERDICTS = {
    PASSED: "Passed",
    PASSED_WITH_LIMITS: "Passed with limits",
    FAILED: "Failed",
    NOT_RUN: "Not run",
}

# The fallback ReliabilityEnvelope writes into known_limitations when a producer
# supplied nothing at all (reliability_envelope._enforce_state_required_fields).
# It is a placeholder, not a recorded limit.
DEFAULT_LIMITATION = "Qualified draft: this output has not been independently verified."


@dataclass(frozen=True)
class Graded:
    state: str | None
    printable: bool
    missing: tuple[str, ...]
    card: str | None = None
    values: dict[str, Any] = field(default_factory=dict)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _evaluation(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    verdict = value.get("verdict")
    if verdict not in VERDICTS:
        return None
    by = str(value.get("by") or "").strip()
    if verdict != NOT_RUN and not by:
        return None  # a verdict with no named evaluator is not independent evidence
    return {"verdict": verdict, "by": by, "reason": str(value.get("reason") or "").strip()}


def _clean(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip() and str(x).strip() != DEFAULT_LIMITATION]


def _limits(envelope: Mapping[str, Any]) -> list[str]:
    """What is qualified: the core envelope's ``known_limitations``, else the job
    orchestrator's named failed checks (``verification_report.checks_failed``).
    Both are specific to the run; the core envelope's generic fallback is not."""
    limits = _clean(envelope.get("known_limitations"))
    if not limits:
        report = envelope.get("verification_report")
        if isinstance(report, Mapping):
            limits = _clean(report.get("checks_failed"))
    return limits


def card_or_gaps(envelope: Mapping[str, Any]) -> Graded:
    """Grade one envelope against the card. Print it only if every required
    field was recorded; otherwise name the fields that were not."""
    state = envelope.get("completion_state")
    state = _STATE_ALIASES.get(state, state) if isinstance(state, str) else state
    if state not in STATES:
        return Graded(None, False, ("completion_state",))
    missing: list[str] = []
    sources = _count(envelope.get(EVIDENCE_SOURCES))
    evaluation = _evaluation(envelope.get(INDEPENDENT_EVALUATION))
    rejected = _count(envelope.get(ALTERNATIVES_REJECTED))
    limits = _limits(envelope)
    if sources is None:
        missing.append(EVIDENCE_SOURCES)
    if evaluation is None:
        missing.append(INDEPENDENT_EVALUATION)
    if rejected is None:
        missing.append(ALTERNATIVES_REJECTED)
    if state == QUALIFIED_DRAFT and not limits:
        missing.append("known_limitations")
    # A verified result must have been independently checked and passed.
    if state == VERIFIED and evaluation is not None and evaluation["verdict"] != PASSED:
        missing.append(f"{INDEPENDENT_EVALUATION} (verified needs a passed evaluation, "
                       f"recorded {evaluation['verdict']})")
    title = envelope.get(TASK_TITLE)
    title = title.strip() if isinstance(title, str) and title.strip() else None
    values = {"sources": sources, "evaluation": evaluation, "rejected": rejected,
              "limits": limits, "title": title}
    if missing:
        return Graded(state, False, tuple(missing), None, values)
    return Graded(state, True, (), render(state, values), values)


def render(state: str, values: Mapping[str, Any]) -> str:
    evaluation = values["evaluation"]
    verdict = VERDICTS[evaluation["verdict"]]
    if evaluation["verdict"] == NOT_RUN and evaluation.get("reason"):
        verdict = f"{verdict}: {evaluation['reason']}"
    elif evaluation.get("by"):
        verdict = f"{verdict} ({evaluation['by']})"
    lines = [HEADLINES[state], "", "Task:", values["title"] or PRIVATE_TASK, "",
             "Status:", f"{state} ({MEANINGS[state]})"]
    if state == QUALIFIED_DRAFT:
        lines += ["", "What is qualified:", *values["limits"]]
    n = values["sources"]
    lines += ["", "Evidence:", f"{n} source{'' if n == 1 else 's'}",
              "", "Independent evaluation:", verdict,
              "", "Alternative approaches rejected:", str(values["rejected"]),
              "", FOOTER]
    return "\n".join(lines)


# --- the customer's title ------------------------------------------------------------

TITLE_MAX_CHARS = 120


def clean_task_title(title: object) -> str:
    """The customer's title, made safe to print: control characters removed,
    whitespace collapsed, capped at TITLE_MAX_CHARS. "" means no title."""
    if not isinstance(title, str):
        return ""
    printable = "".join(ch if ch.isprintable() else " " for ch in title)
    collapsed = " ".join(printable.split())
    if len(collapsed) > TITLE_MAX_CHARS:
        collapsed = collapsed[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return collapsed


def stamp_task_title(result: Any, title: object) -> Any:
    """Put the CUSTOMER's title on a result, flat and nested; with no title,
    make sure none is present (the task stays private).

    Called only at the MCP entry points, AFTER the reliability envelope is
    attached (which strips any title a component wrote), so the title on a card
    is always one the customer typed. Mutates and returns ``result``.
    """
    if not isinstance(result, dict):
        return result
    clean = clean_task_title(title)
    nested = result.get("reliability_envelope")
    targets = [result] + ([nested] if isinstance(nested, dict) else [])
    for target in targets:
        if clean:
            target[TASK_TITLE] = clean
        else:
            target.pop(TASK_TITLE, None)
    return result
