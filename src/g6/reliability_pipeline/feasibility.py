# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Feasibility triage for intake (from the archive's ``feasibility`` component, rewritten for audit 030).

What a rule verdict rests on:

- structure: no success criteria gives ``blocked_unclear_success``; a request that names context it does not carry (an
  attached document, an unavailable file) with nothing attached gives ``blocked_missing_context``. Attaching context
  resolves the second and relaxes nothing else (FEA-001).
- tripwire signals: word-boundary phrases (secrets and access, destructive actions, live systems, regulated or
  money-moving domains, absolute guarantees, sweeping or vague scope), a negation right before a phrase cancelling it.
  A signal can make a goal ``risky`` (held until the request accepts the risk), ``too_large`` or ``underspecified``
  (both downgrade). It never chooses a hard block on its own (FEA-002/003): only an injected assessor can answer
  ``infeasible`` or ``blocked_missing_access``.
- figures: a time bound, a cost constraint, and the figures stated beside a time bound become assumptions marked as the
  request's own and unchecked, and the risk band is at least ``medium`` (FEA-005).

There is no confidence number (FEA-004) and no template decomposition (FEA-012).

Limits: the tripwire is a word list. Its silence never means a task is safe. On the archive's paraphrase fixture
(high-stakes tasks reworded to avoid its words) it holds 1 of 7 risky rows, and it holds harmless tasks that use its
words (``tests/reliability_pipeline/test_feasibility_measured.py`` records both). An injected assessor may tighten the
verdict, never relax it (:func:`at_least`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.intent_engine.schema import IntentContract

FeasibilityClassification = Literal[
    "clear",
    "risky",
    "underspecified",
    "infeasible",
    "too_large",
    "blocked_missing_context",
    "blocked_missing_access",
    "blocked_unclear_success",
]


class ReportAssumption(BaseModel):
    """An assumption the verdict rests on, with who stated it (FEA-009). ``source`` is the contract's own (``explicit``,
    ``inferred``, ``fallback``) or ``request_figure``: a figure or time bound the request states that nothing here
    checked (FEA-005). ``confidence`` is the contract's, or ``None``."""

    model_config = ConfigDict(frozen=True)

    statement: str
    source: Literal["explicit", "inferred", "fallback", "request_figure"]
    confidence: float | None = None


class FeasibilityReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    classification: FeasibilityClassification
    #: ``None`` from the rules: a word match has no measured confidence (FEA-004). An injected assessor may set one.
    confidence_level: float | None = None
    assumptions: list[ReportAssumption] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    #: Kept for stored reports; the rules no longer fill it with a template (FEA-012).
    suggested_decomposition: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    recommended_next_step: str = ""
    reduction_to_practice_questions: list[str] = Field(default_factory=list)
    #: What the verdict rests on: tripwire matches (``access: password``), cancelled ones (``negated: delete``),
    #: unchecked figures, and the structural findings.
    signals: list[str] = Field(default_factory=list)
    #: ``rule`` (this module), ``assessor`` (an injected one), or ``assessor, risk held to rule``.
    source: str = "assessor"


# Explicit permissiveness order. Larger values are stricter / more blocking.
CLASSIFICATION_STRICTNESS = {
    "clear": 0,
    "underspecified": 1,
    "too_large": 2,
    "risky": 3,
    "infeasible": 4,
    "blocked_unclear_success": 4,
    "blocked_missing_context": 4,
    "blocked_missing_access": 5,
}
RISK_STRICTNESS = {"low": 0, "medium": 1, "high": 2, "critical": 3}

REDUCTION_TO_PRACTICE_QUESTIONS = [
    "What would prove this works?",
    "What is the smallest version that demonstrates value?",
    "What data or harness is required?",
    "What can be mocked without invalidating the test?",
    "What is the first irreversible decision?",
    "What is the cheapest validation?",
    "What assumptions could kill the plan?",
    "What evidence is needed before scaling?",
    "What rollback is required?",
]


@dataclass(frozen=True)
class FeasibilityClassificationDecision:
    """The rule verdict for an ``IntentContract``. Always a qualified draft: nothing can construct another state."""

    classification: str
    risk_level: str
    rationale: str = ""
    signals: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    confidence: float | None = None
    completion_state: str = field(default="qualified-draft", init=False)


def recommended_next_step(classification: str) -> str:
    return {
        "clear": "proceed_to_plan",
        "risky": "answer_reduction_to_practice_questions",
        "underspecified": "clarify_missing_information",
        "infeasible": "renegotiate_scope_or_timeline",
        "too_large": "split_into_smaller_goal",
        "blocked_missing_context": "provide_context",
        "blocked_missing_access": "provide_or_remove_access_requirement",
        "blocked_unclear_success": "define_success_criteria",
    }[classification]


#: Secret and access terms: each makes a goal ``risky``. ``g6.workforce.coding`` reads the same list (F-2: there a
#: password or credential change takes the high-consequence route and the other two refuse).
ACCESS_TERMS: tuple[str, ...] = ("password", "credential", "private key", "production access")
#: The terms that name context the task depends on: ``blocked_missing_context`` unless the caller attached it.
CONTEXT_TERMS: tuple[str, ...] = ("missing context", "unknown api", "unavailable file", "attached document")
#: Actions that destroy data or history: each one alone makes a goal ``risky``, whatever system it names.
DESTRUCTIVE_TERMS: tuple[str, ...] = ("drop table", "wipe", "destroy", "purge", "rm rf", "force push")
#: Each one alone makes a goal ``risky`` (the archive's single-term rules, now matched as words).
_HIGH_STAKES: dict[str, tuple[str, ...]] = {
    "access": ACCESS_TERMS,
    "high stakes": ("medical", "legal", "safety critical", "payment", "delete", "production"),
    "destructive": DESTRUCTIVE_TERMS,
    "absolute guarantee": ("guarantee 100%", "100% uptime", "perpetual motion"),
}
#: Two different axes together make a goal ``risky`` (the archive's compound-risk lexicon, kept as it was).
_AXES: dict[str, tuple[str, ...]] = {
    "irreversible": ("delete", "drop table", "wipe", "destroy", "irreversible", "overwrite", "purge", "truncate",
                     "rm rf", "force push", "revoke"),
    "live system": ("production", "prod", "live system", "live database", "customer data", "real users", "deployed",
                    "go live"),
    "external or sensitive": ("external", "third party", "send email", "publish", "webhook", "private key",
                              "credential", "secret", "payment", "pii", "personal data", "medical", "legal", "ssn",
                              "patient"),
}
#: Scope words: they downgrade, never block.
_SCOPE: dict[str, tuple[str, ...]] = {
    "too_large": ("entire platform", "all features", "everything"),
    "underspecified": ("maybe", "something", "somehow", "not sure", "figure out"),
}
_SEPARATORS = re.compile(r"[\s_\-]+")
_NEGATED = re.compile(r"(?:^|\s)(?:not|never|no|don['’]t|dont|without)\s$")
_UNITS = r"(?:hours?|days?|weeks?|months?|quarters?|years?)"
_COUNTS = r"(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|few|couple of)"
_MONTHS = (r"(?:january|february|march|april|may|june|july|august|september|october|november|december"
           r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)")
_DAYS = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_TIME_BOUND = re.compile(
    rf"\b(?:in|within|by|before|inside|over|under)\s+(?:the\s+)?(?:next\s+|first\s+)?(?:a\s+)?{_COUNTS}\s+{_UNITS}\b"
    rf"|\bby\s+(?:the\s+end\s+of\s+(?:the\s+)?\w+|tomorrow|tonight|today|next\s+\w+|{_MONTHS}|{_DAYS}|q[1-4]|20\d\d)\b"
    r"|\b(?:tomorrow|tonight|year one|first year)\b")
_FIGURE = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|bn|million|billion|thousand)?(?![a-z])"
                     r"|\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent\b)"
                     r"|\b\d[\d,]*\s?(?:k\b|m\b)?\s?[a-z]+")


def classified_text(contract: IntentContract) -> str:
    """The text the rules match in: goal, context and constraints, lower-cased."""
    return " ".join([contract.goal, contract.context or "", *contract.constraints]).lower()


def _normalised(text: str) -> str:
    """NFKC, invisible format characters dropped, case-folded, and runs of spaces, ``_`` and ``-`` made one space, so
    ``private_key``, ``private-key`` and ``check_password`` read as words."""
    text = "".join(ch for ch in unicodedata.normalize("NFKC", text or "") if unicodedata.category(ch) != "Cf")
    return _SEPARATORS.sub(" ", text.casefold()).strip()


@lru_cache(maxsize=None)
def _term(term: str) -> re.Pattern[str]:
    """``term`` as a whole word or phrase, a plural ``s``/``es`` allowed (``passwords``; not ``reproduction``)."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?:e?s)?(?![a-z0-9])")


def _matches(text: str, terms: tuple[str, ...], negated: set[str]) -> list[str]:
    """The ``terms`` found in normalised ``text``. A term found only right after a negation ("do not delete") is not
    counted and is added to ``negated``; one plain occurrence counts ("do not hesitate to delete")."""
    found = []
    for term in terms:
        hits = list(_term(term).finditer(text))
        if not hits:
            continue
        if all(_NEGATED.search(text[:hit.start()]) for hit in hits):
            negated.add(term)
        else:
            found.append(term)
    return found


def names_context(contract: IntentContract) -> bool:
    """The request names context it depends on (an attached document, an unavailable file)."""
    return bool(_matches(_normalised(classified_text(contract)), CONTEXT_TERMS, set()))


def _unchecked(contract: IntentContract) -> list[tuple[str, str]]:
    """``(kind, text)`` for each time bound, cost constraint and figure beside a time bound the request states."""
    text = _normalised(classified_text(contract))
    bounds = list(_TIME_BOUND.finditer(text))
    found = [("time bound", hit.group(0)) for hit in bounds]
    if bounds:
        spans = [hit.span() for hit in bounds]
        found += [("figure", hit.group(0).strip()) for hit in _FIGURE.finditer(text)
                  if not any(start <= hit.start() < end for start, end in spans)]
    if contract.deadline.strip():
        found.append(("time bound", contract.deadline.strip()))
    if contract.cost_constraint.strip():
        found.append(("figure", contract.cost_constraint.strip()))
    return found


def deterministic_classify(contract: IntentContract, *, context_attached: bool = False) -> FeasibilityClassificationDecision:
    """The rule verdict. Structure decides the hard blocks; tripwire signals decide ``risky``, ``too_large`` and
    ``underspecified``; with ``context_attached`` a context phrase is resolved and classification continues."""
    text = _normalised(classified_text(contract))
    missing: list[str] = []
    blockers: list[str] = []
    signals: list[str] = []
    negated: set[str] = set()
    named_context = bool(_matches(text, CONTEXT_TERMS, negated))
    stakes = {group: _matches(text, terms, negated) for group, terms in _HIGH_STAKES.items()}
    axes = [axis for axis, terms in _AXES.items() if _matches(text, terms, negated)]
    scope = {kind: _matches(text, terms, negated) for kind, terms in _SCOPE.items()}
    signals += [f"{group}: {term}" for group, terms in stakes.items() for term in terms]
    if len(axes) >= 2:
        signals.append("compound: " + " + ".join(axes))
    signals += [f"{kind}: {term}" for kind, terms in scope.items() for term in terms]
    signals += [f"negated: {term}" for term in sorted(negated)]
    if not contract.success_criteria:
        missing.append("success_criteria")
        signals.append("missing success criteria")
    if named_context:
        signals.append("the context the request names is attached" if context_attached
                       else "the request names context it does not carry")
    if named_context and not context_attached:
        blockers.append("blocked_missing_context")
        classification = "blocked_missing_context"
    elif not contract.success_criteria:
        classification = "blocked_unclear_success"
    elif any(stakes.values()) or len(axes) >= 2:
        classification = "risky"
    elif scope["too_large"]:
        classification = "too_large"
    elif scope["underspecified"]:
        missing.append("task_scope")
        classification = "underspecified"
    else:
        classification = "clear"
    unchecked = _unchecked(contract)
    signals += [f"unchecked {kind}: {value}" for kind, value in unchecked]
    risk = {"risky": "high", "clear": "medium" if unchecked else "low"}.get(classification, "medium")
    return FeasibilityClassificationDecision(
        classification=classification,
        risk_level=risk,
        rationale=f"rules selected {classification} from: "
                  + ("; ".join(signals) or "no signal (a silent tripwire is no evidence the task is safe)"),
        signals=signals,
        missing_information=missing,
        blockers=blockers,
    )


def report_from_decision(
    contract: IntentContract,
    decision: FeasibilityClassificationDecision,
) -> FeasibilityReport:
    prompts = (
        REDUCTION_TO_PRACTICE_QUESTIONS
        if decision.classification in {"risky", "too_large"}
        else []
    )
    stated = [ReportAssumption(statement=item.statement, source=item.source, confidence=item.confidence)
              for item in contract.assumptions]
    figures = [ReportAssumption(statement=f"The {kind} '{value}' is the request's own; nothing here checked it.",
                                source="request_figure") for kind, value in _unchecked(contract)]
    return FeasibilityReport(
        classification=decision.classification,  # type: ignore[arg-type]
        confidence_level=decision.confidence,
        assumptions=[*stated, *figures],
        blockers=decision.blockers,
        missing_information=decision.missing_information,
        risk_level=decision.risk_level,  # type: ignore[arg-type]
        recommended_next_step=recommended_next_step(decision.classification),
        reduction_to_practice_questions=prompts,
        signals=decision.signals,
        source="rule",
    )


def deterministic_assess(contract: IntentContract, *, context_attached: bool = False) -> FeasibilityReport:
    """The rule report."""
    return report_from_decision(contract, deterministic_classify(contract, context_attached=context_attached))


def at_least(report: FeasibilityReport, floor: FeasibilityReport, *, context_attached: bool = False) -> FeasibilityReport:
    """``report`` held to the rule ``floor``: never less strict, and on a tie of strictness between two different
    classes the floor's wins (audit 030 FEA-001). With ``context_attached`` a ``blocked_missing_context`` answer is
    resolved, so the floor (classified with the context present) stands in for it. Whichever wins keeps the other's
    assumptions and signals, so an assessor cannot drop the unchecked figures."""
    if context_attached and report.classification == "blocked_missing_context":
        chosen, other = floor, report
    else:
        mine, theirs = CLASSIFICATION_STRICTNESS[report.classification], CLASSIFICATION_STRICTNESS[floor.classification]
        if mine < theirs or (mine == theirs and report.classification != floor.classification):
            chosen, other = floor, report
        else:
            chosen, other = report, floor
            if RISK_STRICTNESS[report.risk_level] < RISK_STRICTNESS[floor.risk_level]:
                chosen = report.model_copy(update={"risk_level": floor.risk_level,
                                                   "source": f"{report.source}, risk held to rule"})
    return chosen.model_copy(update={
        "assumptions": [*chosen.assumptions, *(a for a in other.assumptions if a not in chosen.assumptions)],
        "signals": [*chosen.signals, *(s for s in other.signals if s not in chosen.signals)]})


class FeasibilityAssessor:
    """Rules only. An injected ``planner`` (anything with ``assess(contract) -> FeasibilityReport``) may tighten the
    rule verdict, never relax it (:func:`at_least`); the library ships none."""

    def __init__(self, planner=None) -> None:
        self.planner = planner

    def assess(self, contract: IntentContract, *, context_attached: bool = False) -> FeasibilityReport:
        floor = deterministic_assess(contract, context_attached=context_attached)
        if self.planner is None:
            return floor
        return at_least(self.planner.assess(contract), floor, context_attached=context_attached)
