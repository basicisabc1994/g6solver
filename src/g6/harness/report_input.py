# SPDX-License-Identifier: Apache-2.0
"""The report's input fields, including records supplied by callers and test runs.

These types describe presentation inputs; they grant no evidence authority.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Reason(TypedDict, total=False):
    stage: str
    code: str
    detail: str


class Import(TypedDict, total=False):
    file: str
    line: int
    text: str


class Reproduction(TypedDict, total=False):
    kind: str
    imports: list[Import]
    command: str
    node: str
    file: str
    at_base: str


class Check(TypedDict, total=False):
    evidence_status: str
    check: str
    code: str
    result: str
    verdict: str
    requirement: str
    requirements: list[str]
    reproduction: Reproduction
    kind: str
    checks: list[str]
    activity: str
    passed: int
    outcomes: dict[str, str]


class Skip(TypedDict, total=False):
    check: str
    reason: str
    tier: int


class Isolation(TypedDict, total=False):
    backend: str


class Environment(TypedDict, total=False):
    isolation: Isolation


class Search(TypedDict, total=False):
    template_instances: int
    proposed_checks: int
    model_calls: int


class Scope(TypedDict, total=False):
    evidence_status: str
    artifact_digest: str
    base_commit: str
    checks_run: list[Check]
    checks_skipped: list[Skip]
    environment: Environment
    search: Search
    seconds: float


class Missing(TypedDict, total=False):
    requirement: str
    text: str
    reason: str
    settle_by: str


class Defects(TypedDict, total=False):
    stated_and_repository_checks: list[Check]
    proposed_checks: list[Check]


class Question(TypedDict, total=False):
    question: str


class Completion(TypedDict, total=False):
    defects_reproduced: Defects
    not_established: list[Missing]
    demonstrated: list[Check]
    open_questions: list[Question]
    activities: list[Check]


class Statement(TypedDict, total=False):
    requirement: str
    kinds: dict[str, str]
    checked: bool
    support: bool
    refute: bool
    refuted_by: list[str]
    not_counted: str
    codes: list[str]


class RequirementEvidence(TypedDict, total=False):
    refused_by_issuer: bool
    requirements: list[Statement]


class ConfirmedQuestion(TypedDict, total=False):
    function: str
    args: list[Any]
    kwargs: dict[str, Any]
    outcomes: dict[str, dict[str, Any]]
    scope: str


class Answer(TypedDict, total=False):
    option: str
    answered_by: str


class Expectation(TypedDict, total=False):
    expectation_id: str
    question: ConfirmedQuestion
    answer: Answer
    check: Check


class ConfirmedStatement(TypedDict, total=False):
    expectation: str
    checked: bool
    refute: bool
    not_counted: str
    gap: str


class ConfirmedEvidence(TypedDict, total=False):
    expectations: list[ConfirmedStatement]


class Confirmed(TypedDict, total=False):
    expectations: list[Expectation]
    evidence: ConfirmedEvidence


class Assessment(TypedDict, total=False):
    outcome: str
    evidence_status: str
    reasons: list[Reason]
    requirements: list[dict[str, Any]]
    scope: Scope
    completion: Completion
    requirement_evidence: RequirementEvidence
    unknowns: list[str]
    not_checked: list[str]
    confirmed_expectations: Confirmed


class ProbeSet(TypedDict, total=False):
    label: str
    ran: int
    left_out: str
    hidden: bool
    late: bool
    refused: list[str]


class Review(TypedDict, total=False):
    n: int
    verdict: str
    file: str
    rule: str
    why: str
    results: dict[str, str]
    error: str
    told: str
    claimed: str
    unrun: str
    masked: int


class Critic(TypedDict, total=False):
    error: str
    verdict: str


class Loop(TypedDict, total=False):
    evidence_status: str
    reason: str
    repairs: int
    max_repairs: int
    sets: list[ProbeSet]
    failing: list[str]
    boundary: str
    late_because: str
    critic: Critic
    reviews: list[Review]
    ending: str
    left: str
    no_clean: bool


def observation_records(assessment: Assessment | Loop) -> list[dict]:
    """The schema's observation positions, shared by the producer and renderer.

    Never walk arbitrary nested payloads: an imports entry, a reproduction, or
    caller data containing an observation flag cannot attribute its parent.
    Findings and activities count independently of whether any test ran.
    """
    completion = assessment.get("completion") or {}
    defects = completion.get("defects_reproduced") or {}
    return [
        *((assessment.get("scope") or {}).get("checks_run") or []),
        *(completion.get("demonstrated") or []),
        *(completion.get("activities") or []),
        *(defects.get("stated_and_repository_checks") or []),
        *(defects.get("proposed_checks") or []),
        *(one.get("check") or {} for one in (assessment.get("confirmed_expectations") or {}).get("expectations") or []),
        *(assessment.get("reviews") or []),
        *(assessment.get("sets") or []),
    ]
