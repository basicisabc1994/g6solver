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

A question and a confirmed answer are frozen records of what the person was shown and answered. The scenario (the
arguments, keyword arguments and outcomes) and the compiled check are kept as the JSON text G6's rule for a binding's
literal allows (``verification_templates.literal_problem``: JSON data only, no bytes, tuple or set), taken when the
record is made; each read gives a fresh copy, so nothing a caller changes afterwards, in the objects it passed or in
those it read, rewrites what was confirmed. "Contradicts" is decided by the answer itself, the compiled scenario and
its outcome: the same answer to a question with another citation is the same answer, and is held once.

A confirmed answer cannot change the question (review 2's finding 2). The scenario (the function, its arguments and
keyword arguments; example.v1 has no setup) comes from the question alone, and an answer contributes only its outcome,
through the outcome fields example.v1 defines (``OUTCOME_FIELDS``: ``returns``, ``raises``, ``effect``). An outcome
that holds any other key is refused when the question is made, and a confirmed expectation answers a question only
when its check is that question's scenario with the chosen outcome (``for_question``).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from g6.core.ledger import digest
from g6.evidence.certificate import Check, Control, MissionFacts
from g6.harness.patch_review import NORMATIVE
from g6.harness.verification_templates import (
    Call,
    ExampleBindings,
    ExampleCase,
    TemplateInstance,
    instance_digest,
    literal_problem,
)

PERSON_CONFIRMED = "person_confirmed"
#: The fields of an example.v1 case that state its outcome, read from the template's own case: every field of a case
#: that is not its call's (``args``, ``kwargs``). An answer gives these and nothing else.
OUTCOME_FIELDS = frozenset(ExampleCase.model_fields) - frozenset(Call.model_fields)
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


class _Frozen:
    """A field kept as the JSON text of its value when the record is made, and read back as a fresh copy (the module
    docstring). ``default`` makes a left-out field that value; ``dump`` and ``load`` write and read a value that is not
    plain JSON data (the compiled check), and plain JSON data is held to G6's rule for a binding's literal."""

    def __init__(self, *, default: Callable[[], Any] | None = None, dump: Callable[[Any], str] | None = None,
                 load: Callable[[str], Any] | None = None) -> None:
        self._default, self._dump, self._load = default, dump, load

    def __set_name__(self, owner: type, name: str) -> None:
        self._name, self._slot = name, f"_frozen_{name}"

    def __get__(self, obj: object, owner: type | None = None) -> Any:
        if obj is None:   # the class's own default: none for a field that has to be given
            if self._default is None:
                raise AttributeError(self._name)
            return None
        text = obj.__dict__[self._slot]
        return self._load(text) if self._load else json.loads(text)

    def __set__(self, obj: object, value: Any) -> None:
        if value is None and self._default is not None:
            value = self._default()
        if self._dump is not None:
            text = self._dump(value)
        else:
            if isinstance(value, Mapping) and not isinstance(value, dict):
                value = dict(value)
            problem = literal_problem(value)
            if problem:
                raise ValueError(f"{self._name}: {problem}")
            text = json.dumps(value, ensure_ascii=True, allow_nan=False)
        obj.__dict__[self._slot] = text


def _instance_text(value: Any) -> str:
    """The compiled check as JSON text: a template instance whose bindings are JSON data, never anything else."""
    if not isinstance(value, TemplateInstance):
        raise TypeError("a confirmed expectation's check is a template instance")
    problem = literal_problem(value.bindings)
    if problem:
        raise ValueError(f"instance: {problem}")
    return value.model_dump_json()


@dataclass(frozen=True)
class Question:
    """One behaviour question, as the person is shown it: a frozen record (the module docstring)."""

    requirement_id: str
    function: str
    args: list = _Frozen()
    #: Each competing outcome, by its label: exactly one of ``returns``, ``raises`` or ``effect``, as example.v1 states it.
    outcomes: Mapping[str, Mapping[str, Any]] = _Frozen()
    scope: str
    cited: str
    baseline_sha256: str
    kwargs: Mapping[str, Any] = _Frozen(default=dict)
    intent: Literal["change", "preserve"] = "change"

    def __post_init__(self) -> None:
        if not _HEX64.match(str(self.baseline_sha256)):
            raise ValueError("baseline_sha256 must be the sha256 hex digest of the artefact the question is asked about")
        outcomes = self.outcomes
        if (not isinstance(outcomes, dict) or len(outcomes) < 2
                or any(label in NOT_CONFIRMING for label in outcomes)):
            raise ValueError("a question shows at least two competing outcomes, none named like a non-answer")
        for label, outcome in outcomes.items():   # each outcome is an example.v1 case, checked before it is shown
            if not isinstance(outcome, dict):
                raise ValueError("each outcome states returns, raises or effect")
            other = sorted(set(outcome) - OUTCOME_FIELDS)
            if other:   # the scenario is the question's alone: an outcome never restates or replaces it
                raise ValueError(f"outcome {label!r} holds {', '.join(other)}: an outcome states only "
                                 f"{', '.join(sorted(OUTCOME_FIELDS))}, and the scenario is the question's")
            ExampleBindings.model_validate({"function": self.function, "cases": [self._case(label, outcome)]})
        if not isinstance(self.kwargs, dict):
            raise ValueError("kwargs maps each parameter's name to its value")
        if not str(self.scope).strip() or not str(self.cited).strip():
            raise ValueError("a question names its scope and the task's citation")

    def _case(self, label: str, outcome: Mapping[str, Any] | None = None) -> dict:
        """The example.v1 case for ``label``: the question's own scenario, and the outcome's fields (``OUTCOME_FIELDS``
        only), so nothing in an outcome can replace the scenario."""
        chosen = self.outcomes[label] if outcome is None else dict(outcome)
        kwargs = self.kwargs
        return {"args": self.args, **({"kwargs": kwargs} if kwargs else {}),
                **{name: value for name, value in chosen.items() if name in OUTCOME_FIELDS}}

    def to_dict(self) -> dict:
        return {"requirement_id": self.requirement_id, "function": self.function, "args": self.args,
                "kwargs": self.kwargs, "outcomes": self.outcomes, "options": list(NOT_CONFIRMING),
                "scope": self.scope, "cited": self.cited, "baseline_sha256": self.baseline_sha256,
                "intent": self.intent}

    @property
    def sha256(self) -> str:
        """The digest of everything the person is shown."""
        return digest(self.to_dict())

    @property
    def options_sha256(self) -> str:
        return digest({"outcomes": self.outcomes, "options": list(NOT_CONFIRMING)})


@dataclass(frozen=True)
class ConfirmedExpectation:
    """A person's confirmed answer: one example.v1 check, bound to the question it answers and the artefact. A frozen
    record: its check is kept as it was compiled when the person answered (the module docstring)."""

    expectation_id: str
    requirement_id: str
    question_sha256: str
    options_sha256: str
    answer: str
    artefact_sha256: str
    scope: str
    instance: TemplateInstance = _Frozen(dump=_instance_text, load=TemplateInstance.model_validate_json)
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
        """This expectation, when ``question`` is the one it answers and its check is that question's scenario with the
        chosen outcome; a ``ValueError`` for any other question, or for a check that runs another scenario."""
        if question.sha256 != self.question_sha256:
            raise ValueError("an answer to one question never stands for another question")
        if self.answer not in question.outcomes or self.instance.bindings != {
                "function": question.function, "cases": [question._case(self.answer)]}:
            raise ValueError("its check is not the question's scenario with the chosen outcome")
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
    bindings = one.instance.bindings
    case = bindings["cases"][0]
    return digest({"function": bindings["function"], "args": case.get("args", []), "kwargs": case.get("kwargs", {})})


def _answer(one: ConfirmedExpectation) -> str:
    """The answer itself: the compiled scenario and its outcome (the check's bindings), whatever citation the question
    that asked it carried."""
    return digest(one.instance.bindings)


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
        """The accepted expectations ``new`` contradicts: the same scenario, another outcome (``_answer``). The same
        answer under another citation contradicts nothing."""
        return tuple(one.expectation_id for one in self.expectations
                     if _scenario(one) == _scenario(new) and _answer(one) != _answer(new))

    def with_expectation(self, new: ConfirmedExpectation) -> ExpectationStandard:
        """The next revision with ``new`` added; ``ContradictsStandard`` when it contradicts one already accepted; this
        standard when it already holds the same answer (under any citation)."""
        contradicted = self.contradicted_by(new)
        if contradicted:
            raise ContradictsStandard(contradicted)
        if any(_answer(one) == _answer(new) for one in self.expectations):
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


__all__ = ["NORMATIVE", "NOT_CONFIRMING", "OUTCOME_FIELDS", "PERSON_CONFIRMED", "ConfirmedExpectation",
           "ContradictsStandard", "ExpectationStandard", "Question", "Unconfirmed", "confirm", "normative"]
