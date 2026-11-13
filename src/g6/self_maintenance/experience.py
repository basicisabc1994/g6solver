# SPDX-License-Identifier: Apache-2.0
"""Consent-bound product learning; feedback guides proposals, never authorizes action.

No transcript, identity or free-text feedback is persisted by this module.
Measure time-to-value and task completion alongside user-provided ratings.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from typing import Sequence


@dataclass(frozen=True)
class UserSignal:
    cohort: str
    task_success: bool
    time_to_value_seconds: float
    satisfaction: int  # optional 1..5 survey; 0 means unanswered
    friction_count: int
    accessibility_issue: bool
    consented: bool
    independently_observed: bool

    def __post_init__(self):
        if not self.cohort or len(self.cohort) > 64:
            raise ValueError("short, nonidentifying aggregate cohort required")
        if not isfinite(self.time_to_value_seconds) or self.time_to_value_seconds < 0:
            raise ValueError("invalid time-to-value")
        if type(self.satisfaction) is not int or not 0 <= self.satisfaction <= 5:
            raise ValueError("satisfaction must be 0 (not answered) or 1..5")
        if type(self.friction_count) is not int or not 0 <= self.friction_count <= 100:
            raise ValueError("invalid friction count")


@dataclass(frozen=True)
class ExperienceSummary:
    state: str
    consenting_sessions: int
    task_completion_rate: float | None
    median_time_to_value_s: float | None
    satisfaction_mean: float | None
    friction_per_session: float | None
    accessibility_issues: int
    covered_cohorts: tuple[str, ...]
    limitations: tuple[str, ...]


def summarize_experience(signals: Sequence[UserSignal], *, minimum_sessions: int = 10) -> ExperienceSummary:
    if not 1 <= minimum_sessions <= 10_000 or len(signals) > 10_000:
        raise ValueError("feedback size budget")
    rows = [s for s in signals if s.consented and s.independently_observed]
    if len(rows) < minimum_sessions:
        return ExperienceSummary("insufficient_evidence", len(rows), None, None, None, None, 0, (),
                                 ("consent and independent observation required", "sample too small"))
    times = sorted(s.time_to_value_seconds for s in rows if s.task_success)
    median = ((times[(len(times) - 1) // 2] + times[len(times) // 2]) / 2) if times else None
    ratings = [s.satisfaction for s in rows if s.satisfaction]
    return ExperienceSummary(
        "qualified-draft", len(rows), sum(s.task_success for s in rows) / len(rows),
        median, sum(ratings) / len(ratings) if ratings else None,
        sum(s.friction_count for s in rows) / len(rows),
        sum(s.accessibility_issue for s in rows),
        tuple(sorted({s.cohort for s in rows})),
        ("observational user feedback: selection and confounding may bias results",
         "missing responses cannot be treated as positive or negative",
         "results must be paired with held-out task outcomes and safety measures",
         "aggregate metrics alone do not establish fairness or accessibility"),
    )


def experience_obligations() -> tuple[str, ...]:
    return (
        "collect only consented and minimal user signals; do not store identities or raw transcripts",
        "measure first-use success, task completion, time-to-value, effort/friction, and opt-in satisfaction",
        "disaggregate voluntary accessibility feedback only when privacy-safe and sufficiently sampled",
        "record failures and abandoned flows; never optimize only survey respondents",
        "test user-facing alternatives with independent baseline and an explicit no-harm policy",
        "prioritize the user journey bottleneck, not cosmetic improvements on nonconstraining stages",
        "never optimize for compulsive engagement, manipulation, dependency, dark patterns or hidden monetization",
        "retain transparency, accessible defaults, user control, and easy opt-out and rollback",
    )
