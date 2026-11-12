# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The bounded draft and repair loop (design sections 15.3 and 15.4): the earlier repository's loop shape, ported.

Two model families each draft one candidate statement from the confirmed intent. A draft that fails checks 1 or 2
may be repaired, with the checks' own diagnostics and nothing else: never the other family's draft. The caps are
fixed before the loop runs (``Caps``): one first draft per family, at most two repairs in total across both, a
wall-clock limit, a token limit per call and a character limit on all replies together. Nothing is regenerated
until the drafts agree.

Every call goes through section 16's consent check: each caller is guarded (``g6.core.consent_callers.guard``) and
is called inside ``cloud_consent.request_sources`` with the sources of everything the prompt carries (the task, the
person's confirmed examples, retrieved statements). Without a grant the data is withheld and the draft's status is
``withheld``; a model that runs on this machine still drafts. No caller means ``unavailable``.

A reply is read as a typed structure and becomes a statement only through ``smt_ir``; anything else is
``outside_fragment``. A reply that parses is not evidence of anything: this module produces drafts and their
provenance, never a check outcome, a receipt or an acceptance.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core import cloud_consent as cloud
from g6.core.consent_callers import guard
from g6.core.llm import LLMCaller, complete_structured
from g6.harness.formalisation import prompts, smt_adapter, smt_ir
from g6.harness.formalisation.adapters import FRAGMENTS, ExampleObservation
from g6.harness.formalisation.checks import check_domain, check_examples
from g6.harness.formalisation.library import Retrieved, StatementLibrary
from g6.harness.formalisation.statement import FormalStatement, Observation
from g6.reasoning.budget import Budget, BudgetExhausted, MeteredCaller

STATUSES = ("drafted", "withheld", "unavailable", "outside_fragment", "caps_spent", "model_failed")


def _now() -> float:
    return time.monotonic()


@dataclass(frozen=True)
class Caps:
    max_repairs_total: int = 2
    max_wall_clock_s: float = 120.0
    max_output_tokens: int = 2_000
    max_output_chars: int = 48_000


#: The caps a draft loop runs under unless a caller freezes others before it starts.
DEFAULT_CAPS = Caps()


@dataclass(frozen=True)
class Intent:
    """What a draft is made from: the requirement, the task's text with its sources, the person's confirmed
    examples, the property hypotheses, and the observation and domain the statement must be about."""

    requirement_id: str
    task: str
    task_sources: tuple[cloud.Source, ...]
    cases: tuple[ExampleObservation, ...]
    observation: Observation
    domain: tuple[str, ...] = ()
    properties: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Draft:
    status: Literal["drafted", "withheld", "unavailable", "outside_fragment", "caps_spent", "model_failed"]
    statement: FormalStatement | None
    family: str
    model: str = ""
    attempts: int = 0
    repairs: int = 0
    prompt_sha256s: tuple[str, ...] = ()
    common_sources: tuple[str, ...] = ()
    retrieved: tuple[str, ...] = ()
    #: Whether this draft's prompts ever held the other family's draft. Recorded from what was sent, not assumed.
    saw_other: bool = False
    diagnostics: tuple[str, ...] = ()
    seconds: float = 0.0
    calls: int = 0
    output_chars: int = 0
    reason: str = ""

    def provenance(self) -> dict:
        return {"family": self.family, "model": self.model, "status": self.status, "attempts": self.attempts,
                "repairs": self.repairs, "prompt_sha256s": list(self.prompt_sha256s),
                "common_sources": list(self.common_sources), "retrieved": list(self.retrieved),
                "saw_other": self.saw_other, "calls": self.calls, "output_chars": self.output_chars,
                "seconds": self.seconds}


class SmtReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sorts: dict[str, str]
    relation: str
    raises_when: str = ""
    error: str = ""
    assumptions: list[str] = []


class _Capped:
    """One family's view of its model: each reply asked for at most the per-call token cap, every call counted with
    its prompt's digest, and no call made once the shared wall-clock or output cap is spent."""

    def __init__(self, metered: MeteredCaller, caps: Caps, shared: dict) -> None:
        self.metered, self.caps, self.shared = metered, caps, shared
        self.prompts: list[str] = []
        self.sent: list[str] = []
        self.calls = 0
        self.output_chars = 0
        #: Why the last call was not made or failed, and whether a cap stopped it: ``complete_structured`` keeps a
        #: caller's failure to itself, so the loop reads it here.
        self.spent = ""
        self.error = ""

    def _stop(self, why: str) -> None:
        self.spent = self.error = why
        raise BudgetExhausted(why)

    def complete(self, system: str, user: str, *, max_tokens: int = 0) -> str:
        if self.shared["output_chars"] >= self.caps.max_output_chars:
            self._stop("the output limit of the draft loop was reached")
        if _now() >= self.shared["deadline"]:
            self._stop("the time limit of the draft loop was reached")
        self.prompts.append(hashlib.sha256((system + "\n" + user).encode("utf-8")).hexdigest())
        self.sent.append(user)
        self.calls += 1
        try:
            text = self.metered.complete(system, user, max_tokens=self.caps.max_output_tokens)
        except Exception as exc:   # noqa: BLE001 - recorded for the loop, then raised as it was
            self.error = " ".join(str(exc).split())[:300] or type(exc).__name__
            if isinstance(exc, BudgetExhausted):
                self.spent = self.error
            raise
        self.output_chars += len(text or "")
        self.shared["output_chars"] += len(text or "")
        if _now() > self.shared["deadline"]:
            self._stop("the time limit of the draft loop was reached")
        return text


def _context(retrieved: Sequence[Retrieved], intent: Intent) -> str:
    lines = []
    if intent.cases:
        lines.append("Confirmed examples (the person's):")
        lines += [f"- args {list(c.args)!r} kwargs {dict(c.kwargs)!r}: confirmed {dict(c.accepted)!r}; rejected "
                  f"{[dict(r) for r in c.rejected]!r}" for c in intent.cases]
    if retrieved:
        lines.append("Earlier statements (untrusted context; they may be wrong or about something else):")
        lines += [f"- [{one.source_id}] {one.text}" for one in retrieved]
    return "\n".join(lines)


def _reply_format(intent: Intent) -> str:
    seen = intent.observation
    sorts = {**dict(seen.inputs), "result": seen.result_sort}
    return prompts.REPLY_SMT.format(sorts=json.dumps(sorts, sort_keys=True), entry_point=seen.entry_point,
                                    inputs=", ".join(f"{n} ({s})" for n, s in seen.inputs) or "none",
                                    result_sort=seen.result_sort, domain="; ".join(intent.domain) or "every value")


def _statement(reply: Mapping[str, Any], intent: Intent) -> FormalStatement:
    """The statement a typed reply states, or ``smt_ir.OutsideFragment``."""
    seen = intent.observation
    declared = {**dict(seen.inputs), "result": seen.result_sort}
    if dict(reply["sorts"]) != declared:
        raise smt_ir.OutsideFragment("sorts other than the function's own inputs and result")
    return smt_adapter.statement(entry_point=seen.entry_point, inputs=seen.inputs, result_sort=seen.result_sort,
                                 relation=reply["relation"], domain=intent.domain,
                                 assumptions=tuple(reply.get("assumptions") or ()),
                                 raises_when=reply.get("raises_when") or "", error=reply.get("error") or "",
                                 setup=seen.setup)


def sources_of(intent: Intent, retrieved: Sequence[Retrieved]) -> tuple[cloud.Source, ...]:
    """The source of everything a prompt carries: the task, the person's confirmed examples, retrieved statements."""
    found = list(intent.task_sources)
    if intent.cases:
        text = json.dumps([[list(c.args), list(c.kwargs), dict(c.accepted), [dict(r) for r in c.rejected]]
                           for c in intent.cases], sort_keys=True, default=str)
        found.append(cloud.Source.text(cloud.input_source(text), "confirmed-examples", text))
    found += [cloud.Source.text(one.source_id, one.content_sha256, one.text) for one in retrieved]
    return cloud.boundary_sources(tuple(found))


def _one(family: str, raw: LLMCaller, intent: Intent, language: str, caps: Caps, shared: dict,
         retrieved: Sequence[Retrieved], sources: tuple[cloud.Source, ...]) -> Draft:
    started = _now()
    budget = Budget(max_model_calls=1 + caps.max_repairs_total, max_output_chars=caps.max_output_chars,
                    max_wall_clock_s=max(0.001, shared["deadline"] - started))
    capped = _Capped(MeteredCaller(guard(raw), budget), caps, shared)
    model = str(getattr(raw, "model", "") or "")
    common = tuple(sorted({s.source_id for s in sources}))
    context, reply_format = _context(retrieved, intent), _reply_format(intent)
    statement, diagnostics, repairs, attempts = None, (), 0, 0

    def done(status: str, reason: str = "") -> Draft:
        return Draft(status=status, statement=statement if status == "drafted" else None, family=family, model=model,
                     attempts=attempts, repairs=repairs, prompt_sha256s=tuple(capped.prompts), common_sources=common,
                     retrieved=tuple(one.content_sha256 for one in retrieved), saw_other=False,
                     diagnostics=tuple(diagnostics), seconds=round(_now() - started, 3),
                     calls=capped.calls, output_chars=capped.output_chars, reason=reason)

    user = prompts.PROMPTS[language, "initial"].format(statement=intent.task, context_block=context,
                                                       reply_format=reply_format)
    repairing = False
    while True:
        attempts += 1
        capped.error = ""
        with cloud.request_sources(sources):
            got = complete_structured(capped, prompts.SYSTEM, user, SmtReply, retries=0)
        reply = got.get("data")
        if reply is None:   # the call failed, or the reply was not the typed structure
            text = capped.error or str((got.get("warning_card") or {}).get("message") or "no typed reply")
            if cloud.REQUIRED in text or cloud.STATE_UNAVAILABLE in text:
                return done("withheld", text)        # nothing was sent: the consent check refused the sources
            if capped.spent:
                return done("caps_spent" if statement is None else "drafted", capped.spent)
            return done("model_failed" if statement is None else "drafted", text)
        if repairing:
            repairs += 1
        try:
            statement = _statement(reply, intent)
        except (smt_ir.OutsideFragment, ValueError, KeyError, TypeError) as exc:
            statement = None
            return done("outside_fragment", f"the reply is outside the supported fragment: {exc}")
        first, second = check_examples(statement, intent.cases), check_domain(statement, intent.cases)
        failed = [why for one in (first, second) if one.status == "failed" for why in one.reasons]
        diagnostics = tuple(failed)
        if not failed or shared["repairs_left"] <= 0:
            return done("drafted")
        shared["repairs_left"] -= 1
        repairing = True
        user = prompts.PROMPTS[language, "repair"].format(
            statement=intent.task, prev_formalization=json.dumps(reply, sort_keys=True),
            error="\n".join(f"- {why}" for why in failed), context_block=context, reply_format=reply_format)


def draft_pair(intent: Intent, *, language: str, fragment: str, callers: Mapping[str, LLMCaller],
               caps: Caps = DEFAULT_CAPS, library: StatementLibrary | None = None) -> tuple[Draft, Draft]:
    """One draft from each of two families, in the callers' order, under one set of caps. A family with no caller
    gives an ``unavailable`` draft; nothing is drafted for a language or fragment G6 does not support."""
    entry = FRAGMENTS.get(fragment)
    if entry is None or entry["language"] != language or (language, "initial") not in prompts.PROMPTS \
            or fragment != smt_adapter.FRAGMENT:
        from g6.harness.formalisation import UNSUPPORTED, Unsupported

        raise Unsupported(f"[{UNSUPPORTED}] no draft loop for {language}/{fragment}")
    retrieved = tuple(library.search(intent.task, language=language, limit=3)) if library is not None else ()
    sources = sources_of(intent, retrieved)
    shared = {"deadline": _now() + caps.max_wall_clock_s, "output_chars": 0,
              "repairs_left": caps.max_repairs_total}
    drafts = [_one(family, raw, intent, language, caps, shared, retrieved, sources)
              for family, raw in list(callers.items())[:2]]
    while len(drafts) < 2:
        drafts.append(Draft(status="unavailable", statement=None, family="",
                            reason="no second model family is available on this machine"))
    return drafts[0], drafts[1]


__all__ = ["STATUSES", "Caps", "Draft", "Intent", "SmtReply", "draft_pair", "sources_of"]