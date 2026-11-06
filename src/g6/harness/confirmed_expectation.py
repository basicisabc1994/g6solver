# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What a person's confirmed answer is, in the issuer's terms (G6 0.3 design, section 3.2, step 4). Semantics only: the
question is asked in slice 3, and nothing here asks, shows or answers one.

A ``Question`` is one concrete scenario where the task may not decide the behaviour: a call (``function`` with ``args``
and ``kwargs``), the competing observable outcomes, the scope it covers, the task's citation and the digest of the
artefact (the source baseline) it is asked about. Its ``sha256`` covers everything the person is shown.

``confirm`` turns a person's choice of one outcome into a ``ConfirmedExpectation``: exactly one ``example.v1`` check
(``g6.harness.verification_templates``; no second template), whose one case is the scenario with the chosen outcome,
with provenance ``person_confirmed``, bound to the question's digest and the artefact's. An answer that confirms nothing
("neither", "the premise is wrong", "the task already says", "skip", a decline) leaves it unconfirmed (``Unconfirmed``),
and so does an answer from anything but a person: no agent or model answers on the person's behalf.

In the issuer's terms the expectation is a predicate of its own standard (``ExpectationStandard``) and its check is an
``independent_executable`` whose author is the person, whose code is the example.v1 instance's digest, and which, like
any other, counts only with a negative control and a receipt on the artefact it is enforced on
(``g6.evidence.mapping``, row ``person_confirmed``). The standard holds confirmed expectations only, kept apart from any
tentative model of intent: a model's hypothesis is refused, never frozen. A later answer that contradicts an accepted
expectation is refused as an addition; it starts a superseding standard, which names the one it replaces, so every
piece of evidence issued under the old standard's hash no longer counts under the new one.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from g6.core.ledger import digest
from g6.evidence.certificate import Check, Control, MissionFacts
from g6.harness.patch_review import NORMATIVE
from g6.harness.verification_templates import ExampleBindings, TemplateInstance, instance_digest

PERSON_CONFIRMED = "person_confirmed"
#: The answers that confirm nothing (the design's behaviour form, 3.2 step 2), besides a decline or cancel.
NOT_CONFIRMING = ("neither", "premise_wrong", "task_already_says", "skip")
_PERSON = re.compile(r"person:\S.{0,199}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class Unconfirmed(ValueError):
    """Nothing was confirmed: the expectation stays unconfirmed, and is reported as such."""


class ContradictsStandard(ValueError):
    """The expectation contradicts one the standard holds: it starts a superseding standard, never an overwrite."""

    def __init__(self, contradicted: Sequence[str]) -> None:
        self.contradicted = tuple(contradicted)
        super().__init__("it contradicts the accepted expectation(s) " + ", ".join(self.contradicted)
                         + ": start a superseding standard (superseding), which replaces them")


def normative(provenance: str) -> bool:
    """Whether something with this provenance may be normative: a model's hypothesis never is."""
    return provenance in NORMATIVE


@dataclass(frozen=True)
class Question:
    """One behaviour question, as the person is shown it."""

    requirement_id: str
    function: str
    args: list
    #: Each competing outcome, by its label: exactly one of ``returns``, ``raises`` or ``effect``, as example.v1 states it.
    outcomes: Mapping[str, Mapping[str, Any]]
    scope: str
    cited: str
    baseline_sha256: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    intent: Literal["change", "preserve"] = "change"

    def __post_init__(self) -> None:
        if not _HEX64.match(str(self.baseline_sha256)):
            raise ValueError("baseline_sha256 must be the sha256 hex digest of the artefact the question is asked about")
        if len(self.outcomes) < 2 or any(label in NOT_CONFIRMING for label in self.outcomes):
            raise ValueError("a question shows at least two competing outcomes, none named like a non-answer")
        for label, outcome in self.outcomes.items():   # each outcome is an example.v1 case, checked before it is shown
            ExampleBindings.model_validate({"function": self.function, "cases": [self._case(label, outcome)]})
        if not str(self.scope).strip() or not str(self.cited).strip():
            raise ValueError("a question names its scope and the task's citation")

    def _case(self, label: str, outcome: Mapping[str, Any] | None = None) -> dict:
        return {"args": list(self.args), **({"kwargs": dict(self.kwargs)} if self.kwargs else {}),
                **dict(self.outcomes[label] if outcome is None else outcome)}

    def to_dict(self) -> dict:
        return {"requirement_id": self.requirement_id, "function": self.function, "args": list(self.args),
                "kwargs": dict(self.kwargs), "outcomes": {k: dict(v) for k, v in self.outcomes.items()},
                "options": list(NOT_CONFIRMING), "scope": self.scope, "cited": self.cited,
                "baseline_sha256": self.baseline_sha256, "intent": self.intent}

    @property
    def sha256(self) -> str:
        """The digest of everything the person is shown."""
        return digest(self.to_dict())

    @property
    def options_sha256(self) -> str:
        return digest({"outcomes": {k: dict(v) for k, v in self.outcomes.items()}, "options": list(NOT_CONFIRMING)})


@dataclass(frozen=True)
class ConfirmedExpectation:
    """A person's confirmed answer: one example.v1 check, bound to the question it answers and the artefact."""

    expectation_id: str
    requirement_id: str
    question_sha256: str
    options_sha256: str
    answer: str
    artefact_sha256: str
    scope: str
    instance: TemplateInstance
    answered_by: str
    provenance: str = PERSON_CONFIRMED

    def __post_init__(self) -> None:
        if self.provenance != PERSON_CONFIRMED or self.instance.provenance != PERSON_CONFIRMED:
            raise ValueError("a confirmed expectation is the person's: a model's or another source's check never is one")
        if self.instance.template != "example.v1":
            raise ValueError("a confirmed expectation compiles into one example.v1 check")
        if not _PERSON.match(self.answered_by):
            raise Unconfirmed("only a person confirms an expectation; no agent or model answers on their behalf")

    @property
    def check_sha256(self) -> str:
        """The check's code: the example.v1 instance's digest (template, its fixed text, bindings and provenance)."""
        return instance_digest(self.instance)

    def to_dict(self) -> dict:
        return {"expectation_id": self.expectation_id, "requirement_id": self.requirement_id,
                "question_sha256": self.question_sha256, "options_sha256": self.options_sha256, "answer": self.answer,
                "artefact_sha256": self.artefact_sha256, "scope": self.scope, "answered_by": self.answered_by,
                "provenance": self.provenance, "instance": self.instance.model_dump(),
                "check_sha256": self.check_sha256}

    def for_question(self, question: Question) -> ConfirmedExpectation:
        """This expectation, when ``question`` is the one it answers; a ``ValueError`` for any other question."""
        if question.sha256 != self.question_sha256:
            raise ValueError("an answer to one question never stands for another question")
        return self

    def check(self, *, frozen_seq: int, controls: Sequence[Control] = (), positive_seq: int | None = None) -> Check:
        """The issuer's check for this expectation, frozen at ``frozen_seq`` (when it was confirmed)."""
        return Check(self.expectation_id, (self.expectation_id,), self.answered_by, "independent_executable",
                     self.check_sha256, frozen_seq, tuple(controls), "", positive_seq)


def confirm(question: Question, answer: str, *, answered_by: str) -> ConfirmedExpectation:
    """The expectation ``answer`` confirms, or ``Unconfirmed``: for a non-answer, an outcome the question did not show,
    or an answer that is not a person's."""
    if not isinstance(answered_by, str) or not _PERSON.match(answered_by):
        raise Unconfirmed("only a person confirms an expectation; no agent or model answers on their behalf")
    if answer in NOT_CONFIRMING or answer not in question.outcomes:
        raise Unconfirmed(f"{answer!r} confirms none of the outcomes shown, so the expectation stays unconfirmed")
    ident = "confirmed-" + digest({"question": question.sha256, "answer": answer})[:16]
    instance = TemplateInstance(id=ident, template="example.v1", requirement_id=question.requirement_id,
                                intent=question.intent, provenance=PERSON_CONFIRMED,
                                bindings={"function": question.function, "cases": [question._case(answer)]})
    return ConfirmedExpectation(ident, question.requirement_id, question.sha256, question.options_sha256, answer,
                                question.baseline_sha256, question.scope, instance, answered_by)


def _scenario(one: ConfirmedExpectation) -> str:
    """What a case asks, without its answer: the function and its arguments."""
    case = dict(one.instance.bindings["cases"][0])
    return digest({"function": one.instance.bindings["function"], "args": case.get("args", []),
                   "kwargs": case.get("kwargs", {})})


@dataclass(frozen=True)
class ExpectationStandard:
    """A frozen standard of confirmed expectations, each a predicate; kept apart from any tentative model of intent."""

    expectations: tuple[ConfirmedExpectation, ...] = ()
    revision: int = 1
    #: The hash of the revision this one follows ("" for the first).
    previous: str = ""
    #: The expectations a superseding standard replaced (empty when it only added a consistent one).
    replaced: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(isinstance(one, ConfirmedExpectation) for one in self.expectations):
            raise TypeError("a standard holds confirmed expectations only; a tentative model of intent stays proposed")
        ids = [one.expectation_id for one in self.expectations]
        if len(set(ids)) != len(ids):
            raise ValueError("an expectation is held once")
        scenarios = [_scenario(one) for one in self.expectations]
        if len(set(scenarios)) != len(scenarios):
            raise ContradictsStandard(ids)

    @property
    def sha256(self) -> str:
        return digest({"expectations": [one.to_dict() for one in self.expectations], "revision": self.revision,
                       "previous": self.previous, "replaced": list(self.replaced)})

    @property
    def supersedes(self) -> str:
        """The hash of the standard this one supersedes: the previous revision, when it replaced an expectation."""
        return self.previous if self.replaced else ""

    @property
    def predicates(self) -> tuple[str, ...]:
        return tuple(one.expectation_id for one in self.expectations)

    def contradicted_by(self, new: ConfirmedExpectation) -> tuple[str, ...]:
        """The accepted expectations ``new`` contradicts: the same scenario, another answer."""
        return tuple(one.expectation_id for one in self.expectations
                     if _scenario(one) == _scenario(new) and one.check_sha256 != new.check_sha256)

    def with_expectation(self, new: ConfirmedExpectation) -> ExpectationStandard:
        """The next revision with ``new`` added; ``ContradictsStandard`` when it contradicts one already accepted."""
        contradicted = self.contradicted_by(new)
        if contradicted:
            raise ContradictsStandard(contradicted)
        if new.expectation_id in self.predicates:
            return self
        return ExpectationStandard((*self.expectations, new), self.revision + 1, self.sha256, ())

    def superseding(self, new: ConfirmedExpectation) -> ExpectationStandard:
        """A new standard that replaces the expectations ``new`` contradicts with ``new`` and names this one as the
        one it supersedes; this standard is left as it was."""
        contradicted = self.contradicted_by(new)
        if not contradicted:
            raise ValueError("nothing is contradicted, so nothing is superseded: add it instead")
        kept = tuple(one for one in self.expectations if one.expectation_id not in contradicted)
        return ExpectationStandard((*kept, new), self.revision + 1, self.sha256, contradicted)

    def facts(self, mission_sha: str) -> MissionFacts:
        """The issuer's facts for a mission held to exactly this standard."""
        return MissionFacts(mission_sha=mission_sha, mission_standard_sha=self.sha256, standard_sha=self.sha256,
                            predicates=self.predicates, standard_predicates=self.predicates)


__all__ = ["NORMATIVE", "NOT_CONFIRMING", "PERSON_CONFIRMED", "ConfirmedExpectation", "ContradictsStandard",
           "ExpectationStandard", "Question", "Unconfirmed", "confirm", "normative"]
