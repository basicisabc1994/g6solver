# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What a person's confirmed answer is, in the issuer's terms (G6 0.3 design, section 3.2, step 4). Semantics only: the
question is asked in slice 3, and nothing here asks, shows or answers one.

Two records, each held as its canonical JSON bytes (``g6.core.ledger.canonical``: sorted keys, no spaces, ASCII) and
identified by the digest of those bytes:

- A ``Question`` is a record G6 made: one concrete scenario where the task may not decide the behaviour (a call,
  ``function`` with ``args`` and ``kwargs``), the competing observable outcomes and the options that confirm nothing
  (together, the options shown), the scope it covers, the task's citation and the digest of the artefact (the source
  baseline) it is asked about (``QUESTION_FIELDS``). G6 takes every value through its rule for a binding's literal
  (``verification_templates.literal_problem``: JSON data only, no bytes, tuple or set) and writes the bytes once, so a
  flag and a number, numeric text and a number, a float and an int stay distinct, and nothing a caller holds is shared
  with the record. An outcome states only the outcome fields example.v1 defines (``OUTCOME_FIELDS``: ``returns``,
  ``raises``, ``effect``): the scenario is the question's alone.
- An ``Answer`` names only what the person chose: the question's digest, the option chosen (its label among the
  question's own options), any free text, the answerer and the time (``ANSWER_FIELDS``). It repeats nothing the
  question holds, no artefact, scenario, outcome or check, and any such field given to it is refused by name.

A ``ConfirmedExpectation`` pairs a question with a person's answer to it. What the answer means is exactly one
``example.v1`` check (``g6.harness.verification_templates``; no second template) whose one case is the question's
scenario with the chosen outcome, with provenance ``person_confirmed``; G6 derives it from the question's bytes and the
chosen option every time it is read, and never reads it from the answer. An answer whose question digest is another
question's never stands for this one. An answer that confirms nothing ("neither", "the premise is wrong", "the task
already says", "skip", or no outcome shown) leaves the expectation unconfirmed (``Unconfirmed``), and so does an answer
from anything but a person: no agent or model answers on the person's behalf.

Every comparison here is by bytes or their digest, never by Python equality on records: "for this question" compares
the question's bytes; "the same answer" and "contradicts" compare the compiled scenario and its outcome as canonical
JSON, so the same answer to a question with another citation is the same answer; a record is equal to another only
when their bytes are, and a standard only when its digest is.

In the issuer's terms the expectation is a predicate of its own standard (``ExpectationStandard``) and its check is an
``independent_executable`` whose author is the person, whose code is the example.v1 instance's digest, and which, like
any other, counts only with a negative control and a receipt on the artefact it is enforced on (``g6.evidence.mapping``,
row ``person_confirmed``). A standard is an immutable sequence of answer digests, with its revision and what it
supersedes, built once as a tuple of records it copies when it is made, so nothing a caller holds can change it. It
holds confirmed expectations only, kept apart from any tentative model of intent: a model's hypothesis is refused,
never frozen. Adding an answer makes a new standard, a revision when it agrees; the same answer under any citation is
held once. An answer that contradicts an accepted one is refused as an addition: it starts a superseding standard
(``superseding``), which names the one it replaces, so every piece of evidence issued under the old standard's hash no
longer counts under the new one.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Literal

from g6.core.ledger import canonical, digest
from g6.evidence.certificate import Check, Control, MissionFacts
from g6.evidence.mapping import id_of
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
#: that is not its call's (``args``, ``kwargs``). An outcome gives these and nothing else.
OUTCOME_FIELDS = frozenset(ExampleCase.model_fields) - frozenset(Call.model_fields)
#: The answers that confirm nothing (the design's behaviour form, 3.2 step 2), besides a decline or cancel.
NOT_CONFIRMING = ("neither", "premise_wrong", "task_already_says", "skip")
#: Every field of a question's record and the JSON type it holds; its ``options`` are G6's own (``NOT_CONFIRMING``).
QUESTION_FIELDS: Mapping[str, type] = MappingProxyType({
    "requirement_id": str, "function": str, "args": list, "kwargs": dict, "outcomes": dict, "options": list,
    "scope": str, "cited": str, "baseline_sha256": str, "intent": str})
#: Every field of an answer's record and its type: what the person chose, and the digest of the question they chose
#: it for. None of them is a question's field.
ANSWER_FIELDS: Mapping[str, type] = MappingProxyType({
    "question_sha256": str, "option": str, "text": str, "answered_by": str, "answered_at": str})
#: How an answer's time is written: UTC, to the second.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
#: The fields a caller gives a question; ``options`` is not one.
_QUESTION_GIVEN = ("requirement_id", "function", "args", "outcomes", "scope", "cited", "baseline_sha256", "kwargs",
                   "intent")
_PAIR_FIELDS: Mapping[str, type] = MappingProxyType({"question": dict, "answer": dict})
_PERSON = re.compile(r"person:\S.{0,199}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class Unconfirmed(ValueError):
    """Nothing was confirmed: the expectation stays unconfirmed, and is reported as such."""


class ContradictsStandard(ValueError):
    """The expectation contradicts one the standard holds: it starts a superseding standard, never an overwrite."""

    def __init__(self, contradicted: Iterable[str]) -> None:
        self.contradicted = tuple(contradicted)
        super().__init__("it contradicts the accepted expectation(s) " + ", ".join(self.contradicted)
                         + ": start a superseding standard (superseding), which replaces them")


def normative(provenance: str) -> bool:
    """Whether something with this provenance may be normative: a model's hypothesis never is."""
    return provenance in NORMATIVE


# --- records held as their canonical JSON bytes -------------------------------------------------------------------------


def _canonical(value: Any) -> bytes:
    """A record's bytes: G6's canonical JSON (the ledger's, which writes ASCII only)."""
    return canonical(value).encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parsed(data: object, fields: Mapping[str, type], what: str) -> dict:
    """The record ``data`` holds, read back: canonical JSON bytes of an object with exactly ``fields``; a
    ``ValueError`` for anything else (not bytes, not JSON, not canonical, or another set of fields)."""
    if type(data) is not bytes:
        raise ValueError(f"a {what} is read from its canonical JSON bytes")
    try:
        record = json.loads(data)
        again = _canonical(record)
    except (ValueError, RecursionError):   # not UTF-8, not JSON, or a value JSON cannot hold (NaN)
        raise ValueError(f"a {what} record that is not JSON is unreadable") from None
    if again != data:
        raise ValueError(f"a {what} record is held as its canonical JSON (sorted keys, no spaces, ASCII), and this "
                         "is not")
    if type(record) is not dict or set(record) != set(fields):
        raise ValueError(f"a {what} record holds exactly {', '.join(fields)}")
    return record


def _typed(record: Mapping[str, Any], fields: Mapping[str, type], what: str) -> None:
    """Each field of ``record`` is its type exactly, and JSON data G6's rule for a binding's literal allows."""
    for name, kind in fields.items():
        value = record[name]
        if type(value) is not kind:
            raise ValueError(f"{what}'s {name} is {kind.__name__}, not {type(value).__name__}")
        problem = literal_problem(value)
        if problem:
            raise ValueError(f"{what}'s {name}: {problem}")


def _carried(carried: Mapping[str, object]) -> str:
    return (f"an answer names only what the person chose ({', '.join(ANSWER_FIELDS)}); it never carries "
            f"{', '.join(sorted(carried))}: the question's record holds its scenario, outcomes, citation and artefact "
            "digest, and G6 derives the check and its provenance from the question's bytes and the chosen option")


class _Field:
    """A record's field, read from the record's bytes (or by ``read``): every read is a fresh copy, and nothing
    assigns to it after the record is made."""

    def __init__(self, read: Callable[[Any], Any] | None = None) -> None:
        self._read = read

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: object, owner: type | None = None) -> Any:
        if obj is None:   # no class default: a field the caller gives
            raise AttributeError(self._name)
        return self._read(obj) if self._read else json.loads(obj.record)[self._name]

    def __set__(self, obj: object, value: Any) -> None:
        raise FrozenInstanceError(f"cannot assign to field {self._name!r}: a record is held as its bytes")


class _Record:
    """A record held as its canonical JSON bytes (``record``): equal, hashed, copied and pickled by those bytes."""

    @property
    def record(self) -> bytes:
        return self._record

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.record == other.record

    def __hash__(self) -> int:
        return hash(self.record)

    def __reduce__(self) -> tuple:
        return (_restore, (type(self), self.record))


def _restore(kind: type, data: bytes) -> Any:
    """A record read back from its bytes, checked as when it was made (a copy, a pickle, a restart)."""
    return kind.from_record(data)


# --- what an option of a question means -------------------------------------------------------------------------------


def _expectation_id(question_sha256: str, option: str) -> str:
    """The id of the expectation an option of a question confirms: an id is a structure (``id_of``), never a join."""
    return id_of("confirmed", question_sha256, option, digest=True)


def _compiled(shown: Mapping[str, Any], question_sha256: str, option: str) -> TemplateInstance:
    """The one example.v1 check ``option`` of a question means: the question's scenario with that outcome, read from the
    question's record alone, as the template takes it."""
    case = {"args": shown["args"], **({"kwargs": shown["kwargs"]} if shown["kwargs"] else {}),
            **shown["outcomes"][option]}
    bindings = {"function": shown["function"], "cases": [case]}
    ExampleBindings.model_validate(bindings)
    return TemplateInstance(id=_expectation_id(question_sha256, option), template="example.v1",
                            requirement_id=shown["requirement_id"], intent=shown["intent"],
                            provenance=PERSON_CONFIRMED, bindings=bindings)


def _options_sha256(shown: Mapping[str, Any]) -> str:
    return digest({"outcomes": shown["outcomes"], "options": shown["options"]})


def _shown(record: dict, question_sha256: str) -> None:
    """A ``ValueError`` when ``record`` is not a question G6 shows: two or more outcomes, each an example.v1 case of the
    question's own scenario, a scope, a citation and the artefact's digest."""
    if not _HEX64.match(record["baseline_sha256"]):
        raise ValueError("baseline_sha256 must be the sha256 hex digest of the artefact the question is asked about")
    outcomes = record["outcomes"]
    if len(outcomes) < 2 or any(not label or label in NOT_CONFIRMING for label in outcomes):
        raise ValueError("a question shows at least two competing outcomes, each labelled, none named like a "
                         "non-answer")
    for label, outcome in outcomes.items():   # each outcome is an example.v1 case, checked before it is shown
        if type(outcome) is not dict:
            raise ValueError("each outcome states returns, raises or effect")
        other = sorted(set(outcome) - OUTCOME_FIELDS)
        if other:   # the scenario is the question's alone: an outcome never restates or replaces it
            raise ValueError(f"outcome {label!r} holds {', '.join(other)}: an outcome states only "
                             f"{', '.join(sorted(OUTCOME_FIELDS))}, and the scenario is the question's")
        _compiled(record, question_sha256, label)
    if not record["scope"].strip() or not record["cited"].strip():
        raise ValueError("a question names its scope and the task's citation")


# --- the question, the answer and the pair ----------------------------------------------------------------------------


@dataclass(frozen=True, eq=False, init=False)
class Question(_Record):
    """One behaviour question, as the person is shown it: a record G6 made, held as its canonical JSON bytes."""

    requirement_id: str = _Field()
    function: str = _Field()
    args: list = _Field()
    #: Each competing outcome, by its label: exactly one of ``returns``, ``raises`` or ``effect``, as example.v1 states it.
    outcomes: Mapping[str, Mapping[str, Any]] = _Field()
    scope: str = _Field()
    cited: str = _Field()
    baseline_sha256: str = _Field()
    kwargs: Mapping[str, Any] = _Field()
    intent: Literal["change", "preserve"] = _Field()

    def __init__(self, *, requirement_id: str, function: str, args: list, outcomes: Mapping[str, Mapping[str, Any]],
                 scope: str, cited: str, baseline_sha256: str, kwargs: Mapping[str, Any] | None = None,
                 intent: Literal["change", "preserve"] = "change") -> None:
        record = {"requirement_id": requirement_id, "function": function, "args": args,
                  "kwargs": {} if kwargs is None else kwargs, "outcomes": outcomes, "options": list(NOT_CONFIRMING),
                  "scope": scope, "cited": cited, "baseline_sha256": baseline_sha256, "intent": intent}
        _typed(record, QUESTION_FIELDS, "a question")   # the caller's values: each field's type, JSON data only
        data = _canonical(record)   # written once: from here nothing the caller holds is shared with the record
        held = json.loads(data)
        _typed(held, QUESTION_FIELDS, "a question")   # and what was written, so a change in between is checked too
        _shown(held, _sha(data))
        object.__setattr__(self, "_record", data)

    @classmethod
    def from_record(cls, data: bytes) -> Question:
        """The question ``data`` holds, read back from its bytes and checked as a question G6 makes."""
        record = _parsed(data, QUESTION_FIELDS, "question")
        made = cls(**{name: record[name] for name in _QUESTION_GIVEN})
        if made.record != data:   # the one field a caller never gives: the options are G6's own
            raise ValueError("a question record whose options are not G6's own is not a question G6 made")
        return made

    @property
    def sha256(self) -> str:
        """The question's identity: the digest of its bytes, everything the person is shown."""
        return _sha(self._record)

    @property
    def options(self) -> list[str]:
        return json.loads(self._record)["options"]

    @property
    def options_sha256(self) -> str:
        return _options_sha256(json.loads(self._record))

    def to_dict(self) -> dict:
        return json.loads(self._record)


@dataclass(frozen=True, eq=False, init=False)
class Answer(_Record):
    """What the person chose for one question, and nothing the question holds: a record held as its canonical JSON
    bytes. A choice that confirms nothing is a record too; it confirms no expectation (``ConfirmedExpectation``)."""

    question_sha256: str = _Field()
    option: str = _Field()
    answered_by: str = _Field()
    answered_at: str = _Field()
    text: str = _Field()

    def __init__(self, *, question_sha256: str, option: str, answered_by: str, answered_at: str, text: str = "",
                 **carried: object) -> None:
        if carried:
            raise ValueError(_carried(carried))
        if type(answered_by) is not str or not _PERSON.match(answered_by):
            raise Unconfirmed("only a person confirms an expectation; no agent or model answers on their behalf")
        record = {"question_sha256": question_sha256, "option": option, "text": text, "answered_by": answered_by,
                  "answered_at": answered_at}
        _typed(record, ANSWER_FIELDS, "an answer")
        if not _HEX64.match(question_sha256):
            raise ValueError("question_sha256 must be the digest of the question the person was shown")
        try:
            kept = datetime.strptime(answered_at, TIME_FORMAT).strftime(TIME_FORMAT)
        except ValueError:
            kept = ""
        if kept != answered_at:
            raise ValueError(f"answered_at is the time the person answered, in UTC, written {TIME_FORMAT}")
        object.__setattr__(self, "_record", _canonical(record))

    @classmethod
    def from_record(cls, data: bytes) -> Answer:
        """The answer ``data`` holds, read back from its bytes and checked as when it was made."""
        return cls(**_parsed(data, ANSWER_FIELDS, "answer"))

    @property
    def sha256(self) -> str:
        return _sha(self._record)


def _question_of(one: ConfirmedExpectation) -> Question:
    return Question.from_record(one._question)


def _answer_of(one: ConfirmedExpectation) -> Answer:
    return Answer.from_record(one._answer)


@dataclass(frozen=True, eq=False, init=False)
class ConfirmedExpectation(_Record):
    """A person's confirmed answer: the question's bytes and the answer's, and what the answer means derived from them
    every time (one example.v1 check, bound to the question it answers and so to the artefact the question names)."""

    question: Question = _Field(_question_of)
    answer: Answer = _Field(_answer_of)

    def __init__(self, question: Question, answer: Answer, **carried: object) -> None:
        if carried:
            raise ValueError(_carried(carried))
        if type(question) is not Question:
            raise TypeError("a confirmed expectation answers a question G6 made")
        if type(answer) is not Answer:
            raise TypeError("a confirmed expectation holds the record of what the person chose (an Answer)")
        shown, chosen = Question.from_record(question.record), Answer.from_record(answer.record)   # copies, checked
        if chosen.question_sha256 != shown.sha256:
            raise ValueError("an answer to one question never stands for another question: its question digest is "
                             "another question's")
        if chosen.option in NOT_CONFIRMING or chosen.option not in shown.outcomes:
            raise Unconfirmed(f"{chosen.option!r} confirms none of the outcomes shown, so the expectation stays "
                              "unconfirmed")
        object.__setattr__(self, "_question", shown.record)
        object.__setattr__(self, "_answer", chosen.record)

    @classmethod
    def from_record(cls, data: bytes) -> ConfirmedExpectation:
        """The confirmed expectation ``data`` holds (``record``), read back and checked as when it was made."""
        pair = _parsed(data, _PAIR_FIELDS, "confirmed expectation")
        return cls(Question.from_record(_canonical(pair["question"])), Answer.from_record(_canonical(pair["answer"])))

    @property
    def record(self) -> bytes:
        """The two records, as one canonical JSON object: ``question`` and ``answer``."""
        return _canonical({"question": json.loads(self._question), "answer": json.loads(self._answer)})

    @property
    def question_sha256(self) -> str:
        return _sha(self._question)

    @property
    def answer_sha256(self) -> str:
        return _sha(self._answer)

    @property
    def option(self) -> str:
        """The option the person chose: the label of one of the question's outcomes."""
        return json.loads(self._answer)["option"]

    @property
    def answered_by(self) -> str:
        return json.loads(self._answer)["answered_by"]

    @property
    def options_sha256(self) -> str:
        return _options_sha256(json.loads(self._question))

    @property
    def artefact_sha256(self) -> str:
        """The digest of the artefact the question is asked about, read from the question's bytes."""
        return json.loads(self._question)["baseline_sha256"]

    @property
    def requirement_id(self) -> str:
        return json.loads(self._question)["requirement_id"]

    @property
    def scope(self) -> str:
        return json.loads(self._question)["scope"]

    @property
    def provenance(self) -> str:
        return PERSON_CONFIRMED

    @property
    def expectation_id(self) -> str:
        return _expectation_id(self.question_sha256, self.option)

    @property
    def instance(self) -> TemplateInstance:
        """What the answer means: the example.v1 check compiled from the question's bytes and the chosen option."""
        return _compiled(json.loads(self._question), self.question_sha256, self.option)

    @property
    def check_sha256(self) -> str:
        """The check's code: the example.v1 instance's digest (template, its fixed text, bindings and provenance)."""
        return instance_digest(self.instance)

    def to_dict(self) -> dict:
        """The two records and what G6 derives from them, as JSON data."""
        return {"question": json.loads(self._question), "answer": json.loads(self._answer),
                "question_sha256": self.question_sha256, "answer_sha256": self.answer_sha256,
                "expectation_id": self.expectation_id, "provenance": PERSON_CONFIRMED,
                "instance": self.instance.model_dump(), "check_sha256": self.check_sha256}

    def for_question(self, question: Question) -> ConfirmedExpectation:
        """This expectation, when ``question`` is the one it answers, byte for byte; a ``ValueError`` for any other."""
        if type(question) is not Question:
            raise TypeError("a confirmed expectation answers a question G6 made")
        if question.record != self._question:
            raise ValueError("an answer to one question never stands for another question")
        return self

    def check(self, *, frozen_seq: int, controls: Iterable[Control] = (), positive_seq: int | None = None) -> Check:
        """The issuer's check for this expectation, frozen at ``frozen_seq`` (when it was confirmed)."""
        return Check(self.expectation_id, (self.expectation_id,), self.answered_by, "independent_executable",
                     self.check_sha256, frozen_seq, tuple(controls), "", positive_seq)


def confirm(question: Question, answer: str, *, answered_by: str, text: str = "",
            answered_at: str | None = None) -> ConfirmedExpectation:
    """The expectation the person's choice of the option ``answer`` confirms, answered now unless ``answered_at`` says
    when; ``Unconfirmed`` for a non-answer, an outcome the question did not show, or an answer that is not a person's."""
    if type(question) is not Question:
        raise TypeError("a person answers a question G6 made")
    chosen = Answer(question_sha256=question.sha256, option=answer, answered_by=answered_by, text=text,
                    answered_at=datetime.now(UTC).strftime(TIME_FORMAT) if answered_at is None else answered_at)
    return ConfirmedExpectation(question, chosen)


# --- the standard -------------------------------------------------------------------------------------------------------


def _scenario(one: ConfirmedExpectation) -> bytes:
    """What a case asks, without its answer, as canonical JSON: the function and its arguments."""
    shown = json.loads(one._question)
    return _canonical({"function": shown["function"], "args": shown["args"], "kwargs": shown["kwargs"]})


def _meaning(one: ConfirmedExpectation) -> bytes:
    """What an answer means, as canonical JSON: the compiled scenario and its outcome (the check's bindings), whatever
    citation the question that asked it carried."""
    return _canonical(one.instance.bindings)


def _items(value: object, name: str) -> tuple:
    """``value``'s items, taken once into a tuple the standard owns."""
    if isinstance(value, (str, bytes, bytearray, Mapping)) or not isinstance(value, Iterable):
        raise TypeError(f"a standard's {name} is a sequence")
    return tuple(value)


def _copied(one: object) -> ConfirmedExpectation:
    """A copy of a confirmed expectation, read back from its bytes; a ``TypeError`` for anything else."""
    if type(one) is not ConfirmedExpectation:
        raise TypeError("a standard holds confirmed expectations only; a tentative model of intent stays proposed")
    return ConfirmedExpectation.from_record(one.record)


@dataclass(frozen=True, eq=False)
class ExpectationStandard:
    """A standard of confirmed expectations, each a predicate: an immutable sequence of answers with its revision and
    what it supersedes, built once from copies of the records it is given; kept apart from any tentative model of
    intent (the module docstring)."""

    expectations: tuple[ConfirmedExpectation, ...] = ()
    revision: int = 1
    #: The hash of the revision this one follows ("" for the first).
    previous: str = ""
    #: The expectations a superseding standard replaced (empty when it only added a consistent one).
    replaced: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        held = tuple(_copied(one) for one in _items(self.expectations, "expectations"))
        replaced = _items(self.replaced, "replaced")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("a standard's revision is a whole number from 1")
        if type(self.previous) is not str or (self.previous and not _HEX64.match(self.previous)):
            raise ValueError("previous is the hash of the revision this one follows, or empty for the first")
        if any(type(one) is not str or not _HEX64.match(one) for one in replaced):
            raise ValueError("replaced names the expectations a superseding standard replaced, by their ids")
        if replaced and not self.previous:
            raise ValueError("a superseding standard names the standard it supersedes")
        seen: dict[bytes, tuple[str, bytes]] = {}
        for one in held:   # joint consistency: one answer per scenario
            scenario, meaning = _scenario(one), _meaning(one)
            if scenario in seen:
                first, before = seen[scenario]
                if before != meaning:
                    raise ContradictsStandard((first, one.expectation_id))
                raise ValueError("the same answer is held once")
            seen[scenario] = (one.expectation_id, meaning)
        object.__setattr__(self, "expectations", held)
        object.__setattr__(self, "replaced", replaced)

    @property
    def sha256(self) -> str:
        """The standard's identity: its answers' digests in order, its revision and what it supersedes."""
        return digest({"answers": [one.answer_sha256 for one in self.expectations], "revision": self.revision,
                       "previous": self.previous, "replaced": list(self.replaced)})

    def __eq__(self, other: object) -> bool:
        if type(other) is not ExpectationStandard:
            return NotImplemented
        return self.sha256 == other.sha256

    def __hash__(self) -> int:
        return hash(self.sha256)

    def __reduce__(self) -> tuple:
        return (ExpectationStandard, (self.expectations, self.revision, self.previous, self.replaced))

    @property
    def supersedes(self) -> str:
        """The hash of the standard this one supersedes: the previous revision, when it replaced an expectation."""
        return self.previous if self.replaced else ""

    @property
    def predicates(self) -> tuple[str, ...]:
        return tuple(one.expectation_id for one in self.expectations)

    def contradicted_by(self, new: ConfirmedExpectation) -> tuple[str, ...]:
        """The accepted expectations ``new`` contradicts: the same scenario, another outcome, by their canonical JSON.
        The same answer under another citation contradicts nothing."""
        if type(new) is not ConfirmedExpectation:
            raise TypeError("only a confirmed expectation contradicts one")
        scenario, meaning = _scenario(new), _meaning(new)
        return tuple(one.expectation_id for one in self.expectations
                     if _scenario(one) == scenario and _meaning(one) != meaning)

    def with_expectation(self, new: ConfirmedExpectation) -> ExpectationStandard:
        """The next revision with ``new`` added; ``ContradictsStandard`` when it contradicts one already accepted; this
        standard when it already holds the same answer (under any citation)."""
        contradicted = self.contradicted_by(new)
        if contradicted:
            raise ContradictsStandard(contradicted)
        meaning = _meaning(new)
        if any(_meaning(one) == meaning for one in self.expectations):
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


__all__ = ["ANSWER_FIELDS", "NORMATIVE", "NOT_CONFIRMING", "OUTCOME_FIELDS", "PERSON_CONFIRMED", "QUESTION_FIELDS",
           "TIME_FORMAT", "Answer", "ConfirmedExpectation", "ContradictsStandard", "ExpectationStandard", "Question",
           "Unconfirmed", "confirm", "normative"]
