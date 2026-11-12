# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The person's acceptance of a formalisation, and where it is kept (G6 0.3 design, section 15.3).

A formalisation is accepted only by a person's recorded answer to the rule question that showed them the exact rule:
the statement in words, its formal text and digests, what it was checked on, what it does not cover and who
drafted it (``rule_text``). ``accept`` gives a ``FormalAcceptance`` only when

- the answer is the person's, to the rule's question (not the case's), and confirms one of its outcomes;
- the rule the question shows is, byte for byte, the rule text of the assessment being accepted, so the form cannot
  have shown one rendering while the receipt binds another;
- that assessment is ready for the person: all five translation checks passed, against the standard as it is with
  this answer in it.

The acceptance is normative acceptance of the displayed rule and scope. It is not execution evidence, it says nothing
of any code, and it does not make a model-written statement the person's own: the drafts' provenance is kept in it.

``AcceptanceStore`` keeps acceptances per task in a hash-chained file under ``G6_HOME`` (the question store's chain
and kept head, reused), with the bytes of every part an acceptance binds, so each digest resolves. It refuses an
acceptance whose answer the task's question store does not hold. An acceptance bound to another standard than the
current one is left out of ``current``: a superseding answer drops it, and the next assessment is made again.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from g6.core.ledger import LedgerError
from g6.core.storage import state_path
from g6.harness import question_store as qs
from g6.harness.behaviour_question import RULE_SCOPE
from g6.harness.formalisation import receipts
from g6.harness.formalisation.checks import Assessment
from g6.harness.formalisation.render import Rendering
from g6.harness.formalisation.scope import ScopeBinding

NOT_ACCEPTED = "G6_E_FORMALISATION_NOT_ACCEPTED"
ACCEPTANCE = "formal_acceptance"
#: The labels of a rule text's fields, in their fixed order.
RULE_LABELS = ("Rule in words:", "Formal statement:", "Statement SHA-256:", "Rendering SHA-256:", "Checked on:",
               "Scope not covered:", "Drafted by:")


class AcceptanceRefused(ValueError):
    """Nothing was accepted; the message says which condition failed (``G6_E_FORMALISATION_NOT_ACCEPTED``)."""

    code = NOT_ACCEPTED

    def __init__(self, why: str) -> None:
        super().__init__(f"[{NOT_ACCEPTED}] {why}")


def _drafters(drafts: Sequence[Any]) -> list[dict]:
    return [one.provenance() for one in drafts if getattr(one, "status", "") == "drafted"]


def rule_text(rendering: Rendering, scope: ScopeBinding, drafts: Sequence[Any]) -> str:
    """The rule a person is asked to accept, as one ASCII line of labelled fields in a fixed order. It is made from
    the rendering, the scope and the drafts' provenance only; it names no standard, so the same statement, checked on
    the same examples, reads the same before and after the answer that accepts it."""
    by = ", ".join(f"{one['family']} ({one['model'] or 'model not named'})" for one in _drafters(drafts))
    fields = (" | ".join(rendering.words.split("\n")), rendering.formal_text, rendering.statement_sha256,
              rendering.sha256, scope.covers, "; ".join(scope.not_covered) or "nothing listed",
              by or "no model (not drafted by G6's loop)")
    text = " || ".join(f"{label} {value}" for label, value in zip(RULE_LABELS, fields, strict=True))
    if not text.isascii() or not text.isprintable():
        raise AcceptanceRefused("the rule cannot be shown as one line of printable ASCII")
    return text


@dataclass(frozen=True)
class FormalAcceptance:
    question_sha256: str
    answer_sha256: str
    scope: ScopeBinding
    assessment_sha256: str
    drafts: tuple[Mapping[str, Any], ...]
    accepted_at: str
    accepted_by: str

    def record(self) -> bytes:
        return receipts.acceptance_record(
            scope_sha256=self.scope.sha256, assessment_sha256=self.assessment_sha256, accepted_by=self.accepted_by,
            question_sha256=self.question_sha256, answer_sha256=self.answer_sha256, accepted_at=self.accepted_at,
            scope=self.scope.to_dict(), drafts=[dict(one) for one in self.drafts])

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.record()).hexdigest()

    @classmethod
    def from_record(cls, data: bytes) -> FormalAcceptance:
        held = json.loads(data)
        scope = dict(held["scope"])
        made = cls(question_sha256=held["question_sha256"], answer_sha256=held["answer_sha256"],
                   scope=ScopeBinding(**{**scope, "not_covered": tuple(scope["not_covered"])}),
                   assessment_sha256=held["assessment_sha256"], drafts=tuple(held["drafts"]),
                   accepted_at=held["accepted_at"], accepted_by=held["accepted_by"])
        if made.record() != data:
            raise ValueError("an acceptance record is held in its canonical encoding")
        return made


def accept(asked: qs.Asked, reading: Any, assessment: Assessment, drafts: Sequence[Any]) -> FormalAcceptance:
    """The acceptance the person's answer makes of ``assessment``'s statement, or ``AcceptanceRefused``."""
    answer = getattr(reading, "answer", None)
    if answer is None or not getattr(reading, "confirms", False):
        raise AcceptanceRefused("the person confirmed nothing")
    if asked.rule is None or answer.question_sha256 != asked.rule.sha256:
        raise AcceptanceRefused("the person confirmed the case, not the rule: the case is enforced, and the "
                                "formalisation stays unconfirmed")
    if not str(answer.answered_by).startswith("person:"):
        raise AcceptanceRefused("only a person accepts a formalisation")
    if assessment.status != "ready_for_person" or assessment.rendering is None or assessment.scope is None:
        raise AcceptanceRefused("the assessment is not ready for the person: " + "; ".join(assessment.reasons[:3]))
    shown = RULE_SCOPE.format(rule=rule_text(assessment.rendering, assessment.scope, drafts))
    if asked.rule.scope != shown or asked.candidate.rule != rule_text(assessment.rendering, assessment.scope, drafts):
        raise AcceptanceRefused("the rule the person was shown is not this assessment's rule: its rendering, scope "
                                "or drafts differ")
    return FormalAcceptance(question_sha256=asked.rule.sha256, answer_sha256=answer.sha256, scope=assessment.scope,
                            assessment_sha256=assessment.sha256, drafts=tuple(_drafters(drafts)),
                            accepted_at=answer.answered_at, accepted_by=answer.answered_by)


def _rules(kind: str, ident: str, body: dict) -> list[str]:
    if kind != ACCEPTANCE:
        return [f"a {kind} entry is not an acceptance"]
    try:
        record = body["record"].encode("ascii")
        made = FormalAcceptance.from_record(record)
        parts = body["parts"]
        if made.sha256 != ident or not made.accepted_by.startswith("person:"):
            return ["an acceptance entry is named by its record's digest and accepted by a person"]
        if any(hashlib.sha256(text.encode("ascii")).hexdigest() != sha for sha, text in parts.items()):
            return ["a part of an acceptance is kept under the digest of its own bytes"]
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError) as exc:
        return [f"an acceptance entry does not read as one ({type(exc).__name__})"]
    return []


class _Chain(qs.TaskQuestions):
    """The question store's chain, kept head and lock, over the acceptance file of the same task."""

    def __init__(self, identity: str) -> None:
        super().__init__(identity)
        self.path = state_path("formalisation", f"{identity}.jsonl")
        self.head = state_path("formalisation", f"{identity}.head")

    def _validate(self, kind: str, ident: str, body: dict, seen) -> list[str]:
        found = _rules(kind, ident, body)
        if not found and body.get("task") != self.identity:
            found = ["an acceptance bound to another task: refused"]
        return found


class AcceptanceStore:
    """One task's acceptances (the module's docstring), keyed by ``question_store.task_identity``."""

    def __init__(self, identity: str) -> None:
        self._chain = _Chain(identity)
        self.identity = identity

    def _entries(self) -> list[dict]:
        if not self._chain.exists():
            return []
        with qs._locked(self._chain.path):
            return list(self._chain._open().entries())

    def record(self, acceptance: FormalAcceptance, *, parts: Mapping[str, bytes], answers: Sequence[Any]) -> None:
        """Keep ``acceptance`` with the bytes of every part it binds. ``answers`` are the answers the task's question
        store holds (``State.answers``): an acceptance whose answer is not among them is refused, so nothing but the
        person's recorded answer makes one."""
        if not acceptance.accepted_by.startswith("person:"):
            raise AcceptanceRefused("only a person accepts a formalisation")
        if acceptance.answer_sha256 not in {one.sha256 for one in answers}:
            raise AcceptanceRefused("the acceptance names an answer the task's question store does not hold")
        if any(hashlib.sha256(data).hexdigest() != sha for sha, data in parts.items()):
            raise AcceptanceRefused("a part is kept under the digest of its own bytes")
        body = {"record": acceptance.record().decode("ascii"),
                "parts": {sha: data.decode("ascii") for sha, data in sorted(parts.items())}}
        with qs._locked(self._chain.path):
            ledger = self._chain._open()
            if any(entry["id"] == acceptance.sha256 for entry in ledger.entries()):
                return
            try:
                self._chain._append(ledger, ACCEPTANCE, acceptance.sha256, body)
            except LedgerError as exc:
                raise AcceptanceRefused(str(exc)) from None

    def all(self) -> tuple[FormalAcceptance, ...]:
        return tuple(FormalAcceptance.from_record(entry["body"]["record"].encode("ascii"))
                     for entry in self._entries())

    def current(self, standard: Any) -> tuple[FormalAcceptance, ...]:
        """The acceptances bound to exactly this standard. One bound to a standard this one supersedes, or to any
        other revision, is left out: its assessment is made again before anything rests on it."""
        return tuple(one for one in self.all() if one.scope.standard_sha256 == standard.sha256)

    def dropped(self, standard: Any) -> tuple[FormalAcceptance, ...]:
        """The acceptances no longer current under ``standard``: what a contradicting answer invalidated."""
        return tuple(one for one in self.all() if one.scope.standard_sha256 != standard.sha256)

    def resolve(self, sha256: str) -> bytes | None:
        """The bytes kept under ``sha256``: an acceptance's record or a part one binds; None when there are none."""
        for entry in self._entries():
            if entry["id"] == sha256:
                return entry["body"]["record"].encode("ascii")
            text = entry["body"]["parts"].get(sha256)
            if text is not None:
                return text.encode("ascii")
        return None


__all__ = ["ACCEPTANCE", "NOT_ACCEPTED", "RULE_LABELS", "AcceptanceRefused", "AcceptanceStore", "FormalAcceptance",
           "accept", "rule_text"]
