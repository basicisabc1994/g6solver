# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""One behaviour question, from candidate to the person's answer (G6 0.3 design, sections 3.2 and 7.3; slice 3).

A ``Candidate`` is one concrete choice the task may leave open. It carries the task's citation (a quote from the task,
``cited``), repository evidence (``evidence``), an executable scenario (``function`` called with ``args`` and
``kwargs``, in ``SETUP``), the competing observable outcomes (``outcomes``: ``A`` and ``B``, each an example.v1
outcome) and any evidence that the task already settles it (``settled``), with where it came from (``source``). Two
sources in this version:

- ``caller``: candidates the calling agent supplies (``caller_candidates``), taken first (the caller-first rule);
- ``differential``: base-and-patch differences that ``differential.v1`` turned into questions in a review
  (``differential_candidates``): each difference G6 observed between the base commit and the patch on a call the
  instance states, whose two outputs example.v1 can state, is one candidate (outcome A the base commit's, B the
  patch's).

Every outcome is held, compared and shown in one encoding (``encoded``, read back by ``decoded``): JSON text with
sorted keys, as the differential.v1 probe writes what it observed and verification search compares it, and as example.v1
compares a result with what it expects. It tells 1, 1.0 and true apart, -0.0 from 0.0, and 5 from "5". A probe
observation is an outcome only when the probe writes it exactly as example.v1 compares it (``observed``): an exception
raised, by its name, or a returned None, boolean, number or text. A list the probe writes may stand for a tuple, a set or a
mapping's items, and an object for any mapping, so neither is offered, at any depth.

A candidate's two outcomes compete (``competition``): outcome A's compiled check rejects what outcome B shows, and B's
rejects what A shows, by example.v1's own model (a result is compared as JSON writes it; an error matches an instance of
the type named, so of a subtype too; an effect is an argument's value after a call that returned). Identical, overlapping
and independent outcomes are refused, each with its code (``IDENTICAL``, ``OVERLAP``, ``INDEPENDENT``).

``choose`` takes one by a fixed priority (``PRIORITY``). Coverage stays unknown and no candidate marks the task as
determined: ``plan`` says so in every reply. The question G6 shows is slice 2's record (``Question``), made from the
candidate and the digest of the artefact it is asked about; a candidate that offers a stated rule gives a second
record of the same scenario whose scope is that rule in full (``Candidate.questions``), so the person sees the rule
before confirming it, and a single case never becomes a rule unless they choose it.

The behaviour form (``form``) is one dialog: the exact input and setup, outcome A and outcome B, the cited scope,
the options "neither", "the premise is wrong", "the task already says" and "skip", a free-text box, the choice of
confirming the case or the rule (when one is offered), whether G6 may ask one more question on this task, and the
person's name, recorded as given and not authenticated. ``read`` turns the dialog's result, and nothing else, into
slice 2's ``Answer``: a decline, a cancel, a skip, a dialog the client cannot show and a form without a name leave the
question pending (``PENDING``), reported, to be answered later.
"""
from __future__ import annotations

import json
import builtins
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from g6.harness import verification_search as vs
from g6.harness.confirmed_expectation import NOT_CONFIRMING, Answer, Question, Unconfirmed
from g6.harness.verification_templates import ExampleCase, TemplateInstance, instance_digest, literal_problem

SOURCES = ("caller", "differential")
#: The labels of the two competing outcomes the form shows.
LABELS = ("A", "B")
#: The fixed priority ``choose`` applies, in order (design 3.2 step 1; documented, never learned).
PRIORITY = (
    "a candidate with no evidence that the task settles it comes before one with such evidence",
    "the calling agent's candidates come before those G6 made from base-and-patch differences (caller first)",
    "within a source, the order it gave them: the caller's own order; for differences, verification search's question "
    "ranking (the review's open questions in their order), then the review's other activities in theirs",
)
#: What every candidate's scenario runs in: example.v1 calls the function once per case, with copies of the arguments.
SETUP = ("a fresh import of the function's module in a copy of the tree under review, with no other state set up: "
         "the call is made once, with copies of these arguments")
#: The scope of a question that confirms one case only.
CASE_SCOPE = "this one case: this input, this outcome"
#: How a rule's scope reads in its question's record.
RULE_SCOPE = "the rule, as shown in full: {rule}"
#: The longest citation G6 takes from a task for a candidate it makes itself.
MAX_CITED = 500
#: Coverage and determination, as every plan states them (design 3.2 step 1).
UNKNOWN = "unknown"
COVERAGE_NOTE = ("G6 proposes candidates from the sources it has; it does not know which behaviours the task leaves "
                 "open, so coverage is unknown, and no candidate shows that the task decides or does not decide one")
#: The dialog's statuses: an answer G6 recorded, or a question left pending to be answered later.
ANSWERED, PENDING = "answered", "pending"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
#: The refusal of a candidate that lacks a part, and of two outcomes that do not compete (``competition``): the same
#: outcome twice, two that one implementation can both meet (an error and its own subtype, or two errors G6 cannot tell
#: apart by kind), and two about different things one call can do at once (a result and an argument's value).
INVALID = "G6_E_INPUT_VALIDATION"
PROBE_UNAVAILABLE = "G6_E_QUESTION_PROBE_UNAVAILABLE"
IDENTICAL, OVERLAP, INDEPENDENT = ("G6_E_QUESTION_OUTCOMES_IDENTICAL", "G6_E_QUESTION_OUTCOMES_OVERLAP",
                                   "G6_E_QUESTION_OUTCOMES_INDEPENDENT")
#: The values differential.v1's probe writes exactly as example.v1 compares them: the types it passes through as they
#: are. A list it writes may be a tuple, a set or a mapping's items, and an object any mapping: neither is exact.
_EXACT = (type(None), bool, int, float, str)


class CandidateRefused(ValueError):
    """A candidate that does not carry what a candidate must, or whose outcomes do not compete, named, with its code."""

    def __init__(self, message: str, code: str = INVALID) -> None:
        super().__init__(message)
        self.code = code


def require_probe() -> None:
    """The executor currently exposes repository-owned streams, not a protected G6 observation facility.

    No new or resumed differential question may label those streams as G6's own observations. Generic review
    diagnostics retain their existing scope; they cannot cross this boundary into a behaviour dialog.
    """
    raise CandidateRefused("G6 has no protected probe channel for these observations; repository stdout cannot "
                           "supply G6's own runs for a behaviour question", PROBE_UNAVAILABLE)


# --- the one encoding of an outcome -------------------------------------------------------------------------------------


def encoded(value: Any) -> str:
    """The one encoding of an outcome's value (the module's docstring): the probe's own (``verification_search``'s,
    which compares what differential.v1 observed), and example.v1's for a result it compares. A ``TypeError`` or
    ``ValueError`` for a value JSON cannot write."""
    return vs._canonical(value)


def decoded(text: str) -> Any:
    """A value ``encoded`` wrote, read back: the one reader of the probe's text."""
    return json.loads(text)


# --- candidates ---------------------------------------------------------------------------------------------------------


def competition(outcomes: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    """("", "") when outcome A's compiled check rejects what outcome B shows and B's rejects what A shows, by
    example.v1's model; otherwise the code and why. A result against a result competes when ``encoded`` tells them
    apart; an error against a result or an effect always competes (each check rejects the other's call); two errors
    are refused because subtype matching permits a shared multiple-inheritance witness; two effects compete on the
    same argument with values ``encoded`` tells apart. A result and an effect are independent: one call can do both."""
    a, b = outcomes["A"], outcomes["B"]
    kinds = {next(iter(a)), next(iter(b))}
    if kinds == {"returns"}:
        if encoded(a["returns"]) == encoded(b["returns"]):
            return IDENTICAL, f"both outcomes return {encoded(a['returns'])[:80]}"
        return "", ""
    if kinds == {"raises"}:
        if a["raises"] == b["raises"]:
            return IDENTICAL, f"both outcomes raise {a['raises']}"
        return OVERLAP, (f"{a['raises']} and {b['raises']} use subtype matching; G6 cannot tell whether an exception "
                         "could inherit from both, so these outcomes cannot be offered as competing")
    if kinds == {"effect"}:
        one, other = a["effect"], b["effect"]
        if one["argument"] != other["argument"]:
            return INDEPENDENT, "the outcomes are about different arguments, which one call can leave as both say"
        if encoded(one["equals"]) == encoded(other["equals"]):
            return IDENTICAL, f"both outcomes leave argument {one['argument']} as {encoded(one['equals'])[:80]}"
        return "", ""
    if "raises" in kinds:
        return "", ""
    return INDEPENDENT, "one outcome is a result and the other an argument's value, which one call can give both"


@dataclass(frozen=True)
class Candidate:
    """One candidate choice, held as its text in the one encoding (``data``): every field read from it is a fresh
    copy."""

    data: str

    @classmethod
    def make(cls, *, source: str, requirement_id: str, function: str, args: list, kwargs: Mapping[str, Any],
             outcomes: Mapping[str, Mapping[str, Any]], cited: str, evidence: Sequence[str],
             settled: Sequence[Mapping[str, str]] = (), scope: str = CASE_SCOPE, rule: str = "") -> Candidate:
        made = cls._structural(source=source, requirement_id=requirement_id, function=function, args=args,
                               kwargs=kwargs, outcomes=outcomes, cited=cited, evidence=evidence, settled=settled,
                               scope=scope, rule=rule)
        code, why = competition(made.to_dict()["outcomes"])
        if code:
            raise CandidateRefused(f"the two outcomes do not compete: {why}", code)
        return made

    @classmethod
    def from_record(cls, data: str) -> Candidate:
        """Restore a durable record under its original admission language, without changing a confirmed case.

        Repair 1 admitted unrelated built-in error types. They remain readable historical records; new admission
        and every pending dialog use today's stricter rule. The record and its compiled expectation never change.
        """
        made = cls._structural(**decoded(data))
        if made.data != data:
            raise CandidateRefused("a candidate record is held in its canonical encoding")
        outcomes = made.to_dict()["outcomes"]
        code, why = competition(outcomes)
        if code == OVERLAP and all(set(one) == {"raises"} for one in outcomes.values()):
            names = [one["raises"] for one in outcomes.values()]
            types = [getattr(builtins, name, None) if "." not in name else None for name in names]
            if (all(isinstance(one, type) and issubclass(one, BaseException) for one in types)
                    and not issubclass(types[0], types[1]) and not issubclass(types[1], types[0])):
                return made
        if code:
            raise CandidateRefused(f"the recorded outcomes do not compete: {why}", code)
        return made

    @classmethod
    def _structural(cls, *, source: str, requirement_id: str, function: str, args: list, kwargs: Mapping[str, Any],
                    outcomes: Mapping[str, Mapping[str, Any]], cited: str, evidence: Sequence[str],
                    settled: Sequence[Mapping[str, str]] = (), scope: str = CASE_SCOPE, rule: str = "") -> Candidate:
        if source not in SOURCES:
            raise CandidateRefused(f"a candidate's source is one of {', '.join(SOURCES)}")
        record = {"source": source, "requirement_id": requirement_id, "function": function, "args": args,
                  "kwargs": dict(kwargs), "outcomes": {k: dict(v) for k, v in outcomes.items()}, "cited": cited,
                  "evidence": list(evidence), "settled": [dict(one) for one in settled], "scope": scope,
                  "rule": rule}
        for name in ("requirement_id", "function", "cited", "scope", "rule"):
            if type(record[name]) is not str:
                raise CandidateRefused(f"a candidate's {name} is text")
        if not record["cited"].strip():
            raise CandidateRefused("a candidate cites the task")
        if not record["evidence"] or not all(type(one) is str and one.strip() for one in record["evidence"]):
            raise CandidateRefused("a candidate carries repository evidence: at least one line of text")
        if sorted(record["outcomes"]) != list(LABELS):
            raise CandidateRefused("a candidate states two competing outcomes, A and B")
        problem = literal_problem(record)
        if problem:
            raise CandidateRefused(f"a candidate is JSON data: {problem}")
        made = cls(encoded(record))
        made.question("0" * 64)   # every candidate is a question G6 can show: slice 2's record checks it
        return made

    def _get(self, name: str) -> Any:
        return decoded(self.data)[name]

    @property
    def source(self) -> str:
        return self._get("source")

    @property
    def settled(self) -> list[dict]:
        return self._get("settled")

    @property
    def rule(self) -> str:
        return self._get("rule")

    def to_dict(self) -> dict:
        return decoded(self.data)

    def question(self, baseline_sha256: str, *, scope: str | None = None) -> Question:
        """The question of this candidate as the person is shown it, about the artefact ``baseline_sha256`` names."""
        found = self.to_dict()
        try:
            return Question(requirement_id=found["requirement_id"], function=found["function"], args=found["args"],
                            kwargs=found["kwargs"], outcomes=found["outcomes"], scope=scope or found["scope"],
                            cited=found["cited"], baseline_sha256=baseline_sha256)
        except (TypeError, ValueError) as exc:
            raise CandidateRefused(f"the candidate is not a question G6 can show: {exc}") from exc

    def questions(self, baseline_sha256: str) -> tuple[Question, Question | None]:
        """The question that confirms the case, and, when the candidate offers a rule, the one that confirms the rule:
        the same scenario and outcomes, its scope the rule in full."""
        rule = self.rule
        return (self.question(baseline_sha256),
                self.question(baseline_sha256, scope=RULE_SCOPE.format(rule=rule)) if rule.strip() else None)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+", text))


def settled_by(task: str, outcomes: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """Evidence that the task already settles a choice: each outcome the task names literally (the exception's name,
    or the returned value in the one encoding, or a returned text itself). Evidence only: it decides nothing, and it
    reorders candidates (``PRIORITY``)."""
    found = []
    words = _words(task)
    for label in LABELS:
        outcome = outcomes.get(label) or {}
        if "raises" in outcome and str(outcome["raises"]).rsplit(".", 1)[-1] in words:
            found.append({"outcome": label, "by": f"the task names {outcome['raises'].rsplit('.', 1)[-1]}"})
        elif "returns" in outcome:
            value = outcome["returns"]
            text = encoded(value)
            if (isinstance(value, str) and len(value) >= 2 and value in task) or (
                    re.search(rf"(?<![\w.]){re.escape(text)}(?![\w.])", task)):
                found.append({"outcome": label, "by": f"the task names {text[:80]}"})
    return found


#: The fields a caller's candidate takes, and those it must give.
CALLER_FIELDS = frozenset({"requirement_id", "function", "args", "kwargs", "outcomes", "cited", "evidence", "settled",
                           "scope", "rule"})
CALLER_REQUIRED = ("requirement_id", "function", "args", "outcomes", "cited", "evidence")


def caller_candidates(task: str, items: object) -> list[Candidate]:
    """The calling agent's candidates (the caller-first rule), each checked: the fields ``CALLER_FIELDS`` names, a
    citation that is a verbatim quote of the task, repository evidence, two outcomes example.v1 can state and,
    optionally, ``settled`` (each ``{outcome, by}`` whose ``by`` quotes the task) and a stated ``rule``. G6 adds the
    outcomes the task names literally to ``settled``. A ``CandidateRefused`` names the first one that is not."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise CandidateRefused("candidates is a list of {requirement_id, function, args, outcomes, cited, evidence}")
    found = []
    for index, item in enumerate(items):
        where = f"candidates.{index}"
        if not isinstance(item, dict):
            raise CandidateRefused(f"{where} is an object")
        other = sorted(set(item) - CALLER_FIELDS)
        missing = [name for name in CALLER_REQUIRED if name not in item]
        if other or missing:
            raise CandidateRefused(f"{where}: " + "; ".join(
                ([f"not a candidate's field: {', '.join(other)}"] if other else [])
                + ([f"missing: {', '.join(missing)}"] if missing else [])))
        cited = item["cited"]
        if not isinstance(cited, str) or not cited.strip() or cited not in task:
            raise CandidateRefused(f"{where}: cited must be a verbatim quote of the task")
        settled = item.get("settled", [])
        if not isinstance(settled, list) or not all(
                isinstance(one, dict) and set(one) == {"outcome", "by"} and one["outcome"] in LABELS
                and isinstance(one["by"], str) and one["by"].strip() and one["by"] in task for one in settled):
            raise CandidateRefused(f"{where}: settled is a list of {{outcome, by}}, each by a verbatim quote of the "
                                   "task")
        outcomes = item["outcomes"]
        given = [{"outcome": one["outcome"], "by": f"the task says: {one['by']}"} for one in settled]
        try:
            found.append(Candidate.make(
                source="caller", requirement_id=item["requirement_id"], function=item["function"], args=item["args"],
                kwargs=item.get("kwargs", {}), outcomes=outcomes, cited=cited, evidence=item["evidence"],
                settled=[*given, *(settled_by(task, outcomes) if isinstance(outcomes, dict) else [])],
                scope=item.get("scope", CASE_SCOPE), rule=item.get("rule", "")))
        except (CandidateRefused, TypeError, AttributeError) as exc:
            raise CandidateRefused(f"{where}: {exc}", getattr(exc, "code", INVALID)) from exc
    return found


def observed(output: object) -> dict | None:
    """A differential observation's output as an example.v1 outcome, or None when G6 cannot state exactly what the
    probe observed: a raised exception by its name, or a returned value of a type the probe writes as it is (None, a
    boolean, a whole number, a finite float, text). A list or an object the probe writes, at any depth, may stand for
    another value (a tuple, a set or a mapping's items; any mapping), so it is never offered as an observed output."""
    if not isinstance(output, dict) or len(output) != 1:
        return None
    (kind, value), = output.items()
    if kind == "raised" and isinstance(value, str):
        candidate = {"raises": value}
    elif kind == "returned" and type(value) in _EXACT:
        candidate = {"returns": value}
    else:
        return None
    try:
        ExampleCase.model_validate({"args": [], **candidate})
    except ValueError:
        return None
    return candidate


def _citation(task: str, function: str) -> str:
    """The sentence of the task that names the function (its last dotted part), else the task itself, cut."""
    name = function.rsplit(".", 1)[-1]
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", task):
        if name in _words(sentence) and sentence.strip():
            return sentence.strip()[:MAX_CITED]
    return task.strip()[:MAX_CITED]


def differential_candidates(task: str, instances: Iterable[TemplateInstance], activities: Iterable[Mapping],
                            ranked: Sequence[str] = ()) -> list[Candidate]:
    """The candidates base-and-patch differences give: each ``behaviour_changed`` activity of a review whose check is
    a ``differential.v1`` instance with intent ``change`` (one whose intent is ``preserve`` reads a difference as a
    counterexample, never a question), held to the instance it names by its digest (``instance_sha256``, which the
    review recorded), and each of its differences whose two outputs G6 can state exactly (``observed``) and which
    compete (``competition``, in the one encoding). Ranked by ``ranked`` (the review's open questions' check ids,
    verification search's question ranking), then in the activities' own order."""
    by_id = {one.id: one for one in instances if isinstance(one, TemplateInstance)}
    chosen = [a for a in activities if isinstance(a, Mapping) and a.get("activity") == "template instance"
              and a.get("template") == "differential.v1" and a.get("result") == "behaviour_changed"]
    order = {check: index for index, check in enumerate(dict.fromkeys(ranked))}
    chosen = sorted(chosen, key=lambda a: order.get(a.get("check"), len(order)))
    found = []
    for activity in chosen:
        one = by_id.get(activity.get("check"))
        if (one is None or one.template != "differential.v1" or one.intent != "change"
                or activity.get("instance_sha256") != instance_digest(one)):
            continue
        function = one.bindings.get("function", "")
        for difference in activity.get("differences") or []:
            try:
                call = decoded(difference["input"])
                base, patch = observed(decoded(difference["base"])), observed(decoded(difference["patch"]))
            except (KeyError, TypeError, ValueError):
                continue
            if base is None or patch is None or not isinstance(call, dict):   # the same outcome twice: refused below
                continue
            evidence = [f"differential.v1 instance {one.id} (sha256 {instance_digest(one)}), run by G6 in the review: "
                        f"at the base commit it observed {difference['base']}, on the patch {difference['patch']}"]
            try:
                found.append(Candidate.make(
                    source="differential", requirement_id=one.requirement_id, function=function,
                    args=call.get("args", []), kwargs=call.get("kwargs", {}), outcomes={"A": base, "B": patch},
                    cited=_citation(task, function), evidence=evidence,
                    settled=settled_by(task, {"A": base, "B": patch})))
            except CandidateRefused:
                continue
    return found


def ranked(candidates: Iterable[Candidate]) -> list[Candidate]:
    """``candidates`` in ``PRIORITY``'s order: unsettled first, then the caller's, then in the order given."""
    return [one for _, one in sorted(enumerate(candidates), key=lambda pair: (
        bool(pair[1].settled), pair[1].source != "caller", pair[0]))]


def choose(candidates: Iterable[Candidate]) -> Candidate | None:
    """The candidate G6 asks about: the first by ``PRIORITY``."""
    found = ranked(candidates)
    return found[0] if found else None


def plan(candidates: Sequence[Candidate]) -> dict:
    """What a reply says of the candidates: each with its source and rank, the one chosen, the priority applied, and
    that coverage and whether the task is determined are unknown."""
    order = ranked(candidates)
    chosen = order[0] if order else None
    return {"candidates": [{"rank": rank, **one.to_dict()} for rank, one in enumerate(order, start=1)],
            "chosen": chosen.to_dict() if chosen else None, "priority": list(PRIORITY), "coverage": UNKNOWN,
            "task_determined": UNKNOWN, "note": COVERAGE_NOTE}


# --- the behaviour form ---------------------------------------------------------------------------------------------------

#: The options the form offers for the outcome, in order: the two outcomes, then the four that confirm nothing.
CHOICES = (*LABELS, *NOT_CONFIRMING)
FURTHER = ("none", "one_more")
#: The form's fields; the person's name is the one required besides the choice (a required string, so a client's
#: policy cannot accept an empty form on the person's behalf).
FIELDS = ("choice", "binds", "text", "further", "name")
MAX_NAME = 120


#: Whose words a candidate's evidence is, as the form labels it.
EVIDENCE_FROM = {"caller": "as the calling agent states it, not checked by G6",
                 "differential": "G6's own runs in a review"}


def _line(text: object) -> str:
    """Text a candidate holds (the calling agent's words, or a value), on one line: a line break in it can never stand
    as a line of G6's own in the form."""
    return " ".join(str(text).split())


def _value(value: Any) -> str:
    """A value as the form and the report show it: in the one encoding, or a note when JSON cannot write it."""
    try:
        return encoded(value)
    except (TypeError, ValueError, RecursionError):
        return "(a value G6 cannot write)"


def shown_call(question: Mapping[str, Any]) -> str:
    """A question's call as the form and the report show it: the function, each positional argument, then each
    keyword argument by name in sorted order, every value in the one encoding."""
    args, kwargs = question.get("args"), question.get("kwargs")
    positional = ", ".join(_value(one) for one in (args if isinstance(args, list) else []))
    named = ", ".join(f"{key}={_value(value)}" for key, value in sorted(
        (kwargs if isinstance(kwargs, Mapping) else {}).items(), key=lambda item: str(item[0])))
    return f"{question.get('function', '')}({', '.join(part for part in (positional, named) if part)})"


def shown_outcome(outcome: object) -> str:
    """An outcome as the form and the report show it, its value in the one encoding."""
    if not isinstance(outcome, Mapping) or len(outcome) != 1:
        return "an outcome G6 cannot read"
    if "raises" in outcome:   # an exception type's name, as example.v1 resolves it
        name = outcome["raises"]
        return f"raises {name}" if isinstance(name, str) else f"raises {_value(name)}"
    if "effect" in outcome:
        effect = outcome["effect"] if isinstance(outcome["effect"], Mapping) else {}
        return f"leaves argument {_value(effect.get('argument'))} as {_value(effect.get('equals'))}"
    if "returns" in outcome:
        return f"returns {_value(outcome['returns'])}"
    return "an outcome G6 cannot read"


def _call(question: Question) -> str:
    return shown_call(question.to_dict())


def _said(outcome: Mapping[str, Any]) -> str:
    return shown_outcome(outcome)


def form(question: Question, candidate: Candidate, rule: Question | None = None) -> tuple[str, dict]:
    """The behaviour form for ``question``: the message the person reads (the exact input and setup, outcome A and
    outcome B, the cited scope, the evidence, the rule in full when one is offered, and how the answer is kept) and
    the form's schema, with standard form-schema fields only."""
    if candidate.source == "differential":
        require_probe()
    code, why = competition(question.outcomes)
    if code:
        raise CandidateRefused(f"the pending outcomes do not compete: {why}", code)
    shown = question.to_dict()
    whose = EVIDENCE_FROM[candidate.source]
    lines = ["G6 asks one behaviour question about this task. Your answer is enforced exactly as you give it.",
             f"Input: {_line(_call(question))}",
             f"Setup: {SETUP}",
             f"Outcome A: {_line(_said(shown['outcomes']['A']))}",
             f"Outcome B: {_line(_said(shown['outcomes']['B']))}",
             f"Scope: {_line(shown['scope'])}",
             f"Cited from the task: {_line(shown['cited'])}",
             f"Requirement: {_line(shown['requirement_id'])}; asked about the artefact with SHA-256 "
             f"{shown['baseline_sha256']}",
             f"Evidence ({whose}): " + "; ".join(_line(one) for one in candidate.to_dict()["evidence"])]
    settled = candidate.settled
    if settled:
        lines.append("Evidence that the task may already settle it: " + "; ".join(
            f"{one['outcome']}: {_line(one['by'])}" for one in settled))
    if rule is not None:
        lines.append(f"Rule offered, in full: {_line(candidate.rule)}")
        lines.append("Choose binds=rule to confirm that rule; binds=case (the default) confirms this one case only. "
                     "Either way G6 enforces this one case; a rule is recorded as you confirmed it.")
    lines.append("Choose A or B; or neither, premise_wrong (the premise is wrong), task_already_says (the task "
                 "already says) or skip (answer later). Your name is recorded as you give it; it is not "
                 "authenticated.")
    properties: dict[str, dict] = {
        "choice": {"type": "string", "enum": list(CHOICES),
                   "description": "A or B, the outcome intended; or neither, premise_wrong, task_already_says or "
                                  "skip"}}
    if rule is not None:
        properties["binds"] = {"type": "string", "enum": ["case", "rule"],
                               "description": "case: this one case only; rule: the rule shown in full"}
    properties.update({
        "text": {"type": "string", "description": "Anything you want to add (optional)"},
        "further": {"type": "string", "enum": list(FURTHER),
                    "description": "one_more lets G6 ask one more question on this task; none (the default) does not"},
        "name": {"type": "string", "description": "Your name (recorded as supplied, not authenticated)"}})
    return "\n".join(lines), {"type": "object", "properties": properties, "required": ["choice", "name"]}


@dataclass(frozen=True)
class Reading:
    """What the dialog's result says: ``status`` (``ANSWERED`` or ``PENDING``), why, the answer when there is one, and
    whether the person let G6 ask one more question on this task."""

    status: str
    why: str
    answer: Answer | None = None
    further: bool = False
    #: Who answered, as recorded: ``person:`` and the name the form returned ("" when it returned none).
    by: str = ""

    @property
    def confirms(self) -> bool:
        return self.answer is not None and self.answer.option not in NOT_CONFIRMING


#: Why a dialog left the question pending, by the client's action or what the form held.
WHY = {"decline": "declined", "cancel": "cancelled", "unavailable": "unavailable", "invalid": "invalid",
       "skip": "skipped"}


def read(action: str, content: object, question: Question, rule: Question | None, *, answered_at: str) -> Reading:
    """The answer the dialog's result holds, and only that: ``action`` and ``content`` are what the client returned
    (``mcp.consent.elicit``). An accepted form with a choice among ``CHOICES`` and a name gives an ``Answer`` to the
    case's question, or to the rule's when the person chose ``binds`` rule; ``skip`` gives none. Anything else leaves
    the question pending, with why."""
    if action != "accept":
        return Reading(PENDING, WHY.get(action, "unavailable"))
    if not isinstance(content, dict):
        return Reading(PENDING, WHY["invalid"])
    name, choice = content.get("name"), content.get("choice")
    name = " ".join(name.split())[:MAX_NAME] if isinstance(name, str) else ""
    if not name or choice not in CHOICES:
        return Reading(PENDING, WHY["invalid"])
    further, by = content.get("further") == "one_more", f"person:{name}"
    if choice == "skip":
        return Reading(PENDING, WHY["skip"], further=further, by=by)
    binds = content.get("binds", "case")
    if binds not in ("case", "rule") or (binds == "rule" and rule is None):
        return Reading(PENDING, WHY["invalid"])
    text = content.get("text", "")
    if not isinstance(text, str):
        return Reading(PENDING, WHY["invalid"])
    shown = rule if binds == "rule" else question
    try:
        answer = Answer(question_sha256=shown.sha256, option=choice, text=text[:4000], answered_by=by,
                        answered_at=answered_at)
    except (Unconfirmed, ValueError):
        return Reading(PENDING, WHY["invalid"])
    return Reading(ANSWERED, "confirmed" if choice in LABELS else choice, answer, further, by)


__all__ = ["ANSWERED", "CALLER_FIELDS", "CASE_SCOPE", "CHOICES", "COVERAGE_NOTE", "Candidate", "CandidateRefused",
           "FIELDS", "IDENTICAL", "INDEPENDENT", "INVALID", "LABELS", "OVERLAP", "PENDING", "PRIORITY", "RULE_SCOPE",
           "Reading", "SETUP", "SOURCES", "UNKNOWN", "caller_candidates", "choose", "competition", "decoded",
           "differential_candidates", "encoded", "form", "observed", "plan", "ranked", "read", "settled_by",
           "shown_call", "shown_outcome"]
