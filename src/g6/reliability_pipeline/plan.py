# SPDX-License-Identifier: Apache-2.0
"""Plan critique: is a plan complete enough to run, and what in it needs a second look? (audit 018 §11)

Ported small from the archive's ``plan_critic``. Two deterministic parts and one merge:

* :func:`critique_structure`: the plan has a concrete goal, acceptance criteria, tests and a rollback, and none of
  them is a placeholder (".", "TBD", one word). Presence alone is not completeness (PC-002).
* :func:`lexical_tripwire`: a word-boundary scan for risky or hand-waving wording. It is a tripwire: it asks for a
  revision and never blocks on its own, and it misses risky plans worded without its terms (PC-001).
* :func:`merge_reports`: the floor plus any other report (a reviewer's, a model's); findings are united by category and
  the verdict is the strictest of the two verdicts and of the merged findings' severities (PC-005, PC-010).

A report's ``completion_state`` is what a caller obeys (PC-003): only ``pass`` may proceed, and even then as a
``qualified-draft``, because a structurally complete plan is not evidence that it works. No model judge is shipped
(PC-006); one may be merged in through ``merge_reports``, where it can only tighten.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["pass", "revise", "block"]
_RANK = {"pass": 0, "revise": 1, "block": 2}
_WORD = re.compile(r"[a-z0-9]+")
_PLACEHOLDERS = frozenset({"tbd", "todo", "n a", "na", "none", "later", "see above", "same", "etc", "x"})

#: The tripwire's terms per category, matched as whole words or phrases on lower-cased text.
DEFAULT_TERMS: dict[str, tuple[str, ...]] = {
    "unsafe_action": ("delete", "drop table", "truncate", "wipe", "purge", "production", "prod", "credentials",
                      "password", "payment", "charge the card", "force push", "rm -rf"),
    "technical_unsoundness": ("guess", "hope", "magic", "without inspecting", "somehow"),
    "unrealistic_timeline": ("soc2 in 3 days", "all features", "entire platform", "overnight"),
    "premature_scaling": ("scale to millions", "global launch", "premature scaling"),
    "missing_context": ("missing context", "unknown api", "attached file"),
    "dependency_risk": ("external service", "third party", "third-party", "vendor", "api dependency"),
    "user_misalignment": ("user probably wants", "assume user", "they must mean"),
    "weak_reduction_to_practice": ("big bang", "rewrite everything", "no prototype"),
}


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    goal: str
    steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    rollback: str = ""


class CritiqueFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: str
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    what: str
    better_plan: str = ""
    source: str = "structure"          # structure | tripwire | the merged report's own label
    notes: tuple[str, ...] = ()        # what other reports said about the same category


class CritiqueReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    findings: list[CritiqueFinding] = Field(default_factory=list)

    @property
    def completion_state(self) -> str:
        """What the caller obeys: ``pass`` proceeds as a ``qualified-draft``; ``revise`` and ``block`` stop."""
        return "qualified-draft" if self.verdict == "pass" else "blocked-escalated"

    def to_envelope(self, *, request_id: str = "", run_id: str = "") -> dict:
        from g6.core.reliability_envelope import envelope_fields, warning_card

        envelope = envelope_fields(reliability_label=self.completion_state, request_id=request_id, run_id=run_id,
                                   evidence=[{"kind": "plan_critique", "category": f.category, "severity": f.severity,
                                              "source": f.source} for f in self.findings])
        code = {"pass": "G6_E_PLAN_STRUCTURE_ONLY", "revise": "G6_E_PLAN_REVISE", "block": "G6_E_PLAN_BLOCKED"}
        message = "; ".join(f"{f.category}: {f.what}" for f in self.findings) or "structurally complete; not evidence"
        envelope["warning_card"] = warning_card(code[self.verdict], message)
        return {**envelope, "verdict": self.verdict, "findings": [f.model_dump() for f in self.findings]}


def stricter_verdict(*verdicts: str) -> str:
    """The strictest of ``verdicts`` on pass < revise < block; an unknown verdict counts as ``block``."""
    return max((v if v in _RANK else "block" for v in verdicts), key=_RANK.__getitem__, default="pass")


def severity_verdict(findings: list[CritiqueFinding]) -> str:
    """The verdict the findings themselves call for: any high or critical one blocks, any other asks for a revision."""
    if any(f.severity in ("high", "critical") for f in findings):
        return "block"
    return "revise" if findings else "pass"


def _placeholder(text: str) -> bool:
    words = _WORD.findall(text.lower())
    return len(words) < 2 or " ".join(words) in _PLACEHOLDERS


def _real(items: list[str]) -> list[str]:
    return [i for i in items if not _placeholder(i)]


def critique_structure(plan: Plan) -> CritiqueReport:
    """The deterministic completeness checks; each placeholder entry counts as missing."""
    findings = []
    if len(_WORD.findall(plan.goal.lower())) < 4:
        findings.append(CritiqueFinding(category="vague_goal", what="The goal is too short to verify.",
                                        better_plan="State a concrete, observable outcome."))
    for category, items, what, better in (
            ("missing_success_criteria", plan.acceptance_criteria, "No real acceptance criterion.",
             "Add pass/fail criteria before execution."),
            ("missing_evaluation", plan.tests, "No real test or evidence path.",
             "Name the tests or checks that will show it worked."),
            ("missing_rollback", [plan.rollback], "No real rollback.",
             "State how the change is undone (git, data, config).")):
        if not _real(items):
            findings.append(CritiqueFinding(category=category, what=what, better_plan=better))
    return CritiqueReport(verdict=severity_verdict(findings), findings=findings)


def lexical_tripwire(plan: Plan, *, terms: dict[str, tuple[str, ...]] = DEFAULT_TERMS) -> list[CritiqueFinding]:
    """Categories whose terms appear as whole words in the goal, steps or risks; ``medium`` (revise) at most."""
    text = " ".join([plan.goal, *plan.steps, *plan.risks]).lower()
    found = []
    for category, words in terms.items():
        hits = [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text)]
        if hits:
            found.append(CritiqueFinding(category=category, what=f"The plan mentions {', '.join(hits)}.",
                                         better_plan="Check this part before running; this is a word match.",
                                         source="tripwire"))
    return found


def merge_reports(floor: CritiqueReport, other: CritiqueReport, *, other_source: str = "other") -> CritiqueReport:
    """``other`` may only tighten ``floor``: one finding per category (the floor's text, with the other's as a note);
    the verdict is the strictest of both verdicts and of the merged findings' severities."""
    merged = {f.category: f for f in floor.findings}
    for f in other.findings:
        if f.category in merged:
            kept = merged[f.category]
            severity = max((kept.severity, f.severity), key=("low", "medium", "high", "critical").index)
            merged[f.category] = kept.model_copy(update={"severity": severity, "notes": (*kept.notes, f.what)})
        else:
            merged[f.category] = f.model_copy(update={"source": f.source if f.source != "structure" else other_source})
    findings = list(merged.values())
    return CritiqueReport(verdict=stricter_verdict(floor.verdict, other.verdict, severity_verdict(findings)),
                          findings=findings)


def critique(plan: Plan) -> CritiqueReport:
    """The deterministic floor: the structure checks with the tripwire merged in."""
    floor = critique_structure(plan)
    trip = lexical_tripwire(plan)
    return merge_reports(floor, CritiqueReport(verdict=severity_verdict(trip), findings=trip), other_source="tripwire")


__all__ = ["DEFAULT_TERMS", "CritiqueFinding", "CritiqueReport", "Plan", "critique", "critique_structure",
           "lexical_tripwire", "merge_reports", "severity_verdict", "stricter_verdict"]
