# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The questions G6 asked about a task and the answers it received (G6 0.3 design, section 3.2 steps 3 and 4; slice 3).

One store per task, keyed by the task's identity (``task_identity``: the repository's top folder and the task's text,
not the base commit, so a commit the agent makes keeps the budget): ``$G6_HOME/questions/<identity>.jsonl``, an
append-only, hash-chained ledger (``g6.core.ledger.ChainLedger``), one writer at a time (the receipt store's sidecar
lock). Its entries, in the order G6 wrote them:

- ``asked``: a question G6 is about to show, as slice 2's record (``Question``, its canonical JSON text), with the
  rule's record when the candidate offered a rule, the candidate it came from and when. Written before the dialog
  opens, under the lock that counts the budget, so two calls never both spend the last question.
- ``shown``: each time the form was shown, what came back (answered, declined, cancelled, skipped, unavailable,
  invalid) and the dialog's times, for the person's time.
- ``answer``: what the person chose, as slice 2's record (``Answer``). The ledger's own rule refuses an answer whose
  question is not an ``asked`` entry before it, and a second answer to one question, on every write and every read:
  the store accepts answers only for questions G6 asked.
- ``more``: the person let G6 ask one more question on this task, in a form G6 showed them.

The budget is one question per task by default (owner, 2026-11-06), persistent across retries and MCP calls: the
person sets another default where the server is registered (``G6_QUESTIONS_PER_TASK``, 0 for none; the agent cannot
set it, as the server reads it from its own environment), and asks for one more in the form. A question asked and
not answered (declined, cancelled, skipped, or never shown) stays pending and is shown again before any new one, so
it costs nothing more. The standard of confirmed expectations is derived from the answers, in order, every time it is
read: an answer that contradicts an accepted one starts a superseding standard, as slice 2 decided. A store whose
chain does not check out (a torn last line after a crash, an edit) is unreadable, never read as empty.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from g6.core.ledger import ChainLedger, LedgerError, digest
from g6.core.storage import state_path
from g6.harness.behaviour_question import Candidate
from g6.harness.confirmed_expectation import (
    NOT_CONFIRMING,
    TIME_FORMAT,
    Answer,
    ConfirmedExpectation,
    ContradictsStandard,
    ExpectationStandard,
    Question,
)
from g6.workforce.receipt_store import _locked

UNREADABLE = "G6_E_QUESTION_STORE_UNREADABLE"
REFUSED = "G6_E_QUESTION_ANSWER_REFUSED"
BUDGET_ENV = "G6_QUESTIONS_PER_TASK"
DEFAULT_BUDGET = 1
MAX_BUDGET = 10
ASKED, SHOWN, ANSWER, MORE = "asked", "shown", "answer", "more"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class StoreUnreadable(Exception):
    """A task's store that cannot be read: its chain does not check out, or the file cannot be opened."""

    def __init__(self, path: Path, why: str) -> None:
        self.path, self.why = path, why
        super().__init__(f"[{UNREADABLE}] the questions G6 holds for this task cannot be read ({why}); move "
                         f"{path.name} aside under $G6_HOME/questions: G6 starts a new store for the task, and the "
                         "answers in the old one are no longer enforced")


class AnswerRefused(ValueError):
    """An answer the store does not take: its question is not one G6 asked, or it was answered already."""


class BudgetSpent(ValueError):
    """No question is left in this task's budget."""


def task_identity(root: Path, task: str) -> str:
    """A task's identity: the digest of its repository's top folder (resolved) and its text, exactly as given."""
    return digest({"repository": str(Path(root).resolve()), "task": task})


def default_budget() -> tuple[int, str]:
    """The questions per task the person allows by default (``G6_QUESTIONS_PER_TASK``, 0 to ``MAX_BUDGET``; one when
    unset), and why a value that is set could not be read. A value G6 cannot read allows none: G6 never asks more
    than the person said."""
    raw = os.environ.get(BUDGET_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_BUDGET, ""
    text = raw.strip()
    if text.isdigit() and int(text) <= MAX_BUDGET:
        return int(text), ""
    return 0, f"{BUDGET_ENV}={raw!r} is not a whole number from 0 to {MAX_BUDGET}, so G6 asks no question"


def _question(text: object) -> Question:
    if not isinstance(text, str):
        raise ValueError("a question is held as its canonical JSON text")
    return Question.from_record(text.encode("ascii"))


def _answer(text: object) -> Answer:
    if not isinstance(text, str):
        raise ValueError("an answer is held as its canonical JSON text")
    return Answer.from_record(text.encode("ascii"))


def _rules(kind: str, ident: str, body: dict, seen) -> list[str]:
    """The store's own rules for an entry, given the entries before it (on every write and every read)."""
    try:
        if kind == ASKED:
            asked = _question(body.get("question"))
            if asked.sha256 != ident:
                return ["an asked entry's id is its question's digest"]
            if body.get("rule"):
                rule = _question(body["rule"])
                same = {k: v for k, v in asked.to_dict().items() if k != "scope"}
                if {k: v for k, v in rule.to_dict().items() if k != "scope"} != same:
                    return ["a rule's question asks the case's question, with another scope only"]
            data = body.get("candidate")
            if not isinstance(data, str) or Candidate.make(**json.loads(data)).data != data:
                return ["an asked entry holds the candidate it came from, as G6 made it"]
            return []
        asked = {one: entry for (k, one), entry in seen.items() if k == ASKED}
        shas = {sha: one for one, entry in asked.items() for sha in (one, *(
            [_question(entry["rule"]).sha256] if entry.get("rule") else []))}
        if kind == SHOWN:
            return [] if body.get("question_sha256") in asked else ["a showing names a question G6 asked"]
        if kind == ANSWER:
            answer = _answer(body.get("answer"))
            if answer.sha256 != ident:
                return ["an answer entry's id is its answer's digest"]
            if answer.question_sha256 not in shas:
                return ["an answer to a question G6 did not ask"]
            done = {shas[_answer(entry["answer"]).question_sha256] for (k, _), entry in seen.items() if k == ANSWER}
            if shas[answer.question_sha256] in done:
                return ["a question G6 asked is answered once"]
            return []
        if kind == MORE:
            return [] if body.get("question_sha256") in asked else ["one more question is allowed in a form G6 showed"]
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        return [f"a {kind} entry does not read as one ({exc})"]
    return [f"the store holds no {kind} entries"]


@dataclass(frozen=True)
class Asked:
    """A question G6 asked: the case's record, the rule's (None when no rule was offered), the candidate, when, and
    its number on this task."""

    question: Question
    rule: Question | None
    candidate: Candidate
    asked_at: str
    number: int

    @property
    def shas(self) -> tuple[str, ...]:
        return (self.question.sha256, *((self.rule.sha256,) if self.rule is not None else ()))

    def for_answer(self, answer: Answer) -> Question:
        return self.rule if self.rule is not None and answer.question_sha256 == self.rule.sha256 else self.question


@dataclass(frozen=True)
class State:
    """What a task's store holds, read once: the questions asked, the answers, each showing, the questions the person
    allowed beyond the default, and the budget."""

    asked: tuple[Asked, ...] = ()
    answers: tuple[Answer, ...] = ()
    shown: tuple[dict, ...] = ()
    more: int = 0
    default: int = DEFAULT_BUDGET
    budget_note: str = ""
    superseded: tuple[dict, ...] = field(default=())

    @property
    def allowance(self) -> int:
        return self.default + self.more

    @property
    def left(self) -> int:
        return max(0, self.allowance - len(self.asked))

    def answer_to(self, asked: Asked) -> Answer | None:
        return next((one for one in self.answers if one.question_sha256 in asked.shas), None)

    @property
    def pending(self) -> Asked | None:
        """The first question asked and not answered: it is shown again before any new one."""
        return next((one for one in self.asked if self.answer_to(one) is None), None)

    def asked_for(self, answer: Answer) -> Asked:
        return next(one for one in self.asked if answer.question_sha256 in one.shas)

    def standard(self) -> ExpectationStandard:
        return _standard(self)[0]


def _standard(state: State) -> tuple[ExpectationStandard, tuple[dict, ...]]:
    """The standard the answers make, in the order they were given, and each supersession: an answer that confirms
    an outcome is added, and one that contradicts an accepted answer starts a superseding standard."""
    standard, superseded = ExpectationStandard(), []
    for answer in state.answers:
        if answer.option in NOT_CONFIRMING:
            continue
        one = ConfirmedExpectation(state.asked_for(answer).for_answer(answer), answer)
        try:
            standard = standard.with_expectation(one)
        except ContradictsStandard as exc:
            before = standard
            standard = standard.superseding(one)
            superseded.append({"standard": standard.sha256, "supersedes": before.sha256,
                               "replaced": list(exc.contradicted), "by": one.expectation_id})
    return standard, tuple(superseded)


def now() -> str:
    return datetime.now(UTC).strftime(TIME_FORMAT)


class TaskQuestions:
    """One task's store (the module's docstring)."""

    def __init__(self, identity: str) -> None:
        if not isinstance(identity, str) or not _HEX64.match(identity):
            raise ValueError("a task's identity is the SHA-256 task_identity gives")
        self.identity = identity
        self.path = state_path("questions", f"{identity}.jsonl")

    def exists(self) -> bool:
        return self.path.is_file()

    def _open(self) -> ChainLedger:
        try:
            return ChainLedger(self.path, validate=_rules)
        except (LedgerError, OSError, UnicodeError) as exc:
            raise StoreUnreadable(self.path, str(exc)[:300]) from None

    def state(self) -> State:
        """What the store holds now; ``StoreUnreadable`` when it cannot be read."""
        default, note = default_budget()
        if not self.exists():
            return State(default=default, budget_note=note)
        with _locked(self.path):   # never a line another writer has half written
            return self._read(self._open(), default, note)

    @staticmethod
    def _read(ledger: ChainLedger, default: int, note: str) -> State:
        asked, answers, shown, more = [], [], [], 0
        for entry in ledger.entries():
            body = entry["body"]
            if entry["kind"] == ASKED:
                asked.append(Asked(_question(body["question"]), _question(body["rule"]) if body.get("rule") else None,
                                   Candidate(body["candidate"]), body.get("asked_at", ""), len(asked) + 1))
            elif entry["kind"] == ANSWER:
                answers.append(_answer(body["answer"]))
            elif entry["kind"] == SHOWN:
                shown.append(dict(body))
            elif entry["kind"] == MORE:
                more += 1
        state = State(tuple(asked), tuple(answers), tuple(shown), more, default, note)
        return State(state.asked, state.answers, state.shown, more, default, note, _standard(state)[1])

    def ask(self, question: Question, rule: Question | None, candidate: Candidate, *, at: str | None = None) -> Asked:
        """Record ``question`` as asked, before it is shown, when the budget has room (``BudgetSpent`` when not); a
        question already asked is returned as it is, at no cost."""
        default, note = default_budget()
        with _locked(self.path):
            ledger = self._open()
            state = self._read(ledger, default, note)
            for one in state.asked:
                if one.question.sha256 == question.sha256:
                    return one
            if state.left < 1:
                raise BudgetSpent(note or f"the budget of {state.allowance} question(s) for this task is spent")
            ledger.append(ASKED, question.sha256, {"question": question.record.decode("ascii"),
                                                   "rule": rule.record.decode("ascii") if rule is not None else "",
                                                   "candidate": candidate.data, "asked_at": at or now()})
            return self._read(ledger, default, note).asked[-1]

    def show(self, asked: Asked, result: str, *, shown_at: str, back_at: str, seconds: float) -> None:
        """Record one showing of ``asked``'s form and what came back."""
        with _locked(self.path):
            ledger = self._open()
            ident = digest(["shown", asked.question.sha256, len(ledger.entries())])
            ledger.append(SHOWN, ident, {"question_sha256": asked.question.sha256, "result": result,
                                         "shown_at": shown_at, "back_at": back_at, "seconds": round(seconds, 3)})

    def answer(self, answer: Answer) -> State:
        """Record the person's answer; ``AnswerRefused`` when its question is not one G6 asked, or was answered."""
        if type(answer) is not Answer:
            raise AnswerRefused("the store takes the record of what the person chose (an Answer)")
        default, note = default_budget()
        with _locked(self.path):
            ledger = self._open()
            try:
                ledger.append(ANSWER, answer.sha256, {"answer": answer.record.decode("ascii")})
            except LedgerError as exc:
                raise AnswerRefused(f"[{REFUSED}] {exc}") from None
            return self._read(ledger, default, note)

    def allow_more(self, asked: Asked, *, by: str, at: str | None = None) -> None:
        """Record that the person, in ``asked``'s form, let G6 ask one more question on this task."""
        with _locked(self.path):
            ledger = self._open()
            ident = digest(["more", asked.question.sha256, len(ledger.entries())])
            ledger.append(MORE, ident, {"question_sha256": asked.question.sha256, "by": by, "at": at or now()})


__all__ = ["ANSWER", "ASKED", "AnswerRefused", "Asked", "BUDGET_ENV", "BudgetSpent", "DEFAULT_BUDGET", "MAX_BUDGET",
           "MORE", "REFUSED", "SHOWN", "State", "StoreUnreadable", "TaskQuestions", "UNREADABLE", "default_budget",
           "now", "task_identity"]
