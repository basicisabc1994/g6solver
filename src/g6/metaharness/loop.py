# SPDX-License-Identifier: Apache-2.0
"""The self-improvement loop's tick, in shadow: it works on any domain, with no person in it.

One tick is one cycle of design 6.1. It opens by reading its own records and saying what limits progress (``g6.metaharness.reflex``), which is recorded and
changes nothing it does. **Measure** the head on the tuning cases. **Identify** the constraint: the cause
a profile can change that costs the most cases, through ``self_maintenance.constraints``, and none at all when the
failure records were not independently observed. **Exploit**, by proposing within the meta-harness's editable schema:
``method.tune`` runs its usual proposers, with ``FailureTraceStrategy`` shown only the constraint's failures, and races
them on the selection split. **Gate**: the champion goes to the held-out split and the unchanged gate under
``EvidencePolicy``, which holds it to ``GATED``. **Record** the cycle in the local constraint log, and **adopt** what the
gate recommends in the loop's own workspace (``g6.metaharness.shadow``), so the next tick starts from it.

The only person in it is the one who enrols a domain: they confirm what judges the work, once, and the loop runs from
there. Someone has to say what "better" means, and it cannot be the thing being judged.

``python -P -B -X pycache_prefix=<an empty folder> -m g6.metaharness.loop run <folder>`` runs ticks on their own
(``g6.metaharness.runner``).

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been
established.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.machinery
import inspect
import math
import os
import re
import site
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial, wraps
from pathlib import Path
from types import CodeType
from typing import Any

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import LedgerError, canonical, digest
from g6.core.llm import _WAITS, DEFAULT_MAX_TOKENS, LLMError
from g6.core.model_calls import counting
from g6.integrity import build_manifest, in_cache, manifest_sha256
from g6.metaharness.api import _blocked, _envelope, judging_sha256
from g6.metaharness.domain import (
    BARRED,
    FALLBACK_MODEL,
    SHARES,
    Cut,
    Domain,
    Execution,
    FailureRecord,
    Judge,
    answer_encoding,
    answer_from,
    backends,
    caller_problems,
    cut_pool,
    domain_problems,
    judge_source,
    provider_problems,
    sealed_closed,
    sender_problems,
)
from g6.metaharness import reflex
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.method import Method, tune
from g6.metaharness.tick_rca import TickDiagnosis, TickRecord, scored, tick_rca
from g6.metaharness.monitor import MIN_PAIRS
from g6.metaharness.propose import DevRunner, ProposerView
from g6.metaharness.schema import APPROVAL_REQUIRED, EvidencePolicy, Suite, SuiteCase, Workflow, default_objectives
from g6.metaharness.select import race_calls
from g6.metaharness.shadow import FROZEN_CHANGED, ROLLED_BACK, SCOPE, Held, adapt_shadow
from g6.operator.store import OperatorStore
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    ENDS_A_STREAK,
    FINDINGS,
    MAX_FAILED_CYCLES,
    STOP_UNREADABLE,
    STOPPED,
    TICK_BUDGET_SPENT,
    CycleLog,
    Stop,
    ask_to_stop,
    beat,
    note_refusal,
    refused_requests,
    stop_in_force,
)
from g6.self_maintenance.pacing import AccountReading, Admission, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import limit_ends, read_stops, refused_by, telling_stops
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

NOT_ENROLLED = "G6_E_LOOP_NOT_ENROLLED"
POOL_TOO_SMALL = "G6_E_LOOP_POOL_TOO_SMALL"
EVALUATOR_IN_REACH = "G6_E_LOOP_EVALUATOR_IN_REACH"
#: This folder's stops: one asked for, and one whose record cannot be read (``stop_in_force``).
FOLDER_STOPS = (STOPPED, STOP_UNREADABLE)
PAUSED = "G6_E_LOOP_PAUSED"
RESUMED = "G6_E_LOOP_RESUMED"
HELD_OUT_SPENT = "G6_E_LOOP_HELD_OUT_SPENT"
SEALED_BREACH = "G6_E_LOOP_SEALED_BREACH"
PERSON_REQUIRED = "G6_E_LOOP_PERSON_REQUIRED"
NO_PATH_OUT = "G6_E_LOOP_NO_PATH_OUT"
ENROLLED = "G6_E_LOOP_ENROLLED"
STATUS = "G6_E_LOOP_STATUS"
IDLE = "G6_E_LOOP_IDLE"
CYCLE = "G6_E_LOOP_CYCLE"
CYCLE_FAILED = "G6_E_LOOP_CYCLE_FAILED"
NO_CONSTRAINT = "G6_E_LOOP_NO_CONSTRAINT"
BUDGET_WAIT = "G6_E_LOOP_BUDGET_WAIT"
NO_BUDGET = "G6_E_LOOP_NO_BUDGET"
BUSY = "G6_E_LOOP_BUSY"
RECORDS_UNREADABLE = "G6_E_LOOP_RECORDS_UNREADABLE"
CANNOT_RECORD = "G6_E_LOOP_CANNOT_RECORD"
SPLIT_REUSED = "G6_E_LOOP_SPLIT_REUSED"
UNMETERED_CALLER = "G6_E_LOOP_UNMETERED_CALLER"
CLASSIFIER_STOP = "G6_E_LOOP_CLASSIFIER_STOP"
#: Judging code in a file no listing pins (``_unpinned``), and an answer no exact encoding identifies (``_answer_problems``):
#: what cannot be identified exactly is refused at enrolment, never guessed.
JUDGE_UNPINNED = "G6_E_LOOP_JUDGE_UNPINNED"
ANSWER_UNIDENTIFIABLE = "G6_E_LOOP_ANSWER_UNIDENTIFIABLE"
#: A cycle's safety stops: each holds the loop for a person (``_must_stop``), whatever code its row was given.
SAFETY_STOPS = (SEALED_BREACH, UNMETERED_CALLER, CLASSIFIER_STOP, *BARRED)
#: The order the one rule codes a cycle by (``_coded``), first ranking highest; a status not here ranks after them all.
#: A rollback first: it has happened, the chain went back a generation, and the log counts generations by that outcome.
#: Then a frozen change, above every safety stop: nothing the cycle measured stands, and the next tick is refused until a
#: person enrols again. A safety stop it outranks loses nothing: the row keeps every status, ``_must_stop`` reads them all,
#: so the loop still pauses, and a pause is read before the frozen digests, so enrolling again does not lift it. Then the
#: tick's own reservation spent.
RANKED = (ROLLED_BACK, FROZEN_CHANGED, *SAFETY_STOPS, TICK_BUDGET_SPENT)
#: What enforces the envelope, beside ``JUDGING_PATTERNS``: all of G6. The loop's own files import much of the package and those
#: files import more, so a list of them leaves out whatever an import reaches next. It is read in a tenth of a second, and a
#: change to what runs is a person's to enrol again, as it is theirs to merge. Every kind of file Python imports from a
#: package's folder counts, as a compiled module or a ``.pyc`` with no source beside it is imported as surely as source is,
#: and an extension module before it. A ``__pycache__`` is left out: a first import writes one, and the runner reads none
#: (``g6.metaharness.runner``).
ENVELOPE_PATTERNS = tuple(sorted({f"**/*.{suffix.rsplit('.', 1)[-1]}" for suffix in importlib.machinery.all_suffixes()}))
UNKNOWN = "unknown"
#: What the gate holds this loop's candidates to (the owner's ruling, 2026-11-02: quality first, and optimisation only once
#: everything is correct). Calls, time and cost are still measured, and reported as not gated. ``default_objectives`` and the
#: gate's default margin are as they were, so a workflow a person runs still holds all three.
GATED = ("success",)
_SOURCE = Path(g6.__file__).resolve().parent
#: Code another digest pins: G6's own package (``envelope_sha256``) and the Python installation, its prefixes and every
#: folder packages are installed to, the user's own among them, so a package first imported in the middle of a run (a
#: provider's SDK at its first request) is not a change to what judges.
_INSTALLED = tuple(dict.fromkeys((_SOURCE, *(Path(place).resolve() for place in (
    sys.prefix, sys.base_prefix, sys.exec_prefix, *getattr(site, "getsitepackages", list)(), site.getusersitepackages())))))
STATUS_LINE = ("Early alpha. Individual capabilities have been tested; general application-level improvements have not "
               "yet been established.")


@dataclass(frozen=True)
class LoopSettings:
    windows: tuple[ProviderWindow, ...]
    method: Method = field(default_factory=Method)
    seed: int = 20261031             # cuts the development pool
    audit_per_tick: int = 10         # monitor cases per tick
    eval_calls: int = 2000           # the most one held-out evaluation may call; the gate's thresholds are not settings
    provider: str = ""               # the window this folder's calls go to; with one window, that window
    # THE OWNER'S RULING, NOT THE LOOP'S: may a passed gate stand in for a person outside the loop's own workspace?
    # Off until they say otherwise. No profile can reach it, and in this phase turning it on still moves nothing.
    person_free_adoption_outside_shadow: bool = False


@dataclass(frozen=True)
class Constraint:
    name: str
    basis: str
    observations: tuple[tuple[str, float], ...] = ()
    case_ids: tuple[str, ...] = ()
    reason: str = ""


def name_constraint(records: Sequence[FailureRecord], cases: int) -> Constraint:
    """The cause a profile can change that costs the most cases. Each cause is a stage of the work, and a stage's
    capacity is the share of cases it lets through, so the bottleneck is the costliest cause."""
    costs = Counter(r.cause for r in records if r.changeable)
    if not costs:
        return Constraint(UNKNOWN, "no_changeable_cause", reason="only a person can change what is failing here: "
                          + ", ".join(sorted({r.cause for r in records})))
    independent = all(r.independently_observed for r in records)
    found = identify_constraint(tuple(StageMeasurement(cause, cases, max(0, cases - cost), 1.0, independent)
                                      for cause, cost in costs.most_common(40)))
    if found.bottleneck == UNKNOWN:
        return Constraint(UNKNOWN, found.evidence, reason="no constraint is named: not every failure record was "
                                                          "independently observed")
    return Constraint(found.bottleneck, found.evidence, found.observations,
                      tuple(r.case_id for r in records if r.cause == found.bottleneck))


class _OnConstraint:
    """A proposer shown only the constraint's failures: all other work waits behind it (design 6.1, step 4)."""

    def __init__(self, inner: Any, case_ids: Sequence[str]) -> None:
        self.inner, self.name, self.case_ids = inner, inner.name, frozenset(case_ids)

    def propose(self, view: ProposerView, runner: DevRunner) -> list[Any]:
        shown = tuple(f for f in view.failures if f.case_id in self.case_ids)
        return self.inner.propose(replace(view, failures=shown), runner)


class _AtLimit(BaseException):
    """A provider's limit, met during a tick, on its way out of it. It is no ``Exception`` so that it passes every
    ``except Exception`` between a model call and the tick, where the harness would score a failed case and each layer
    carry on. So nothing from the run it cut off is written: no report, so the held-out split is not spent, no decision and
    no cycle."""

    def __init__(self, until: datetime, said: str) -> None:
        super().__init__(said)
        self.until, self.said = until, said


class _Halted(BaseException):
    """A stop, met at a model call, on its way out of the tick as ``_AtLimit`` is, and for the same reason: nothing from the
    run it cut off is written, so an evaluation it cut off is not one of the split's five."""

    def __init__(self, code: str, said: str) -> None:
        super().__init__(said)
        self.code, self.said = code, said


class _Refused(BaseException):
    """A classifier or a permission stopped a request, on its way out of the tick as ``_AtLimit`` is. Nothing it cut off is
    written, so an evaluation it cut off is not one of the split's five."""


def _request_digest(system: str, user: str) -> str:
    """What a request is known by once it has been refused: no text of it is kept."""
    return hashlib.sha256(f"{system}\0{user}".encode("utf-8", "replace")).hexdigest()


def _sender(counted: Any, asked: str) -> tuple[Any, str]:
    """The caller sending the request being noted and the digest of that request, from ``counting``'s observer.
    ``g6.core.model_calls`` says only that one is going out, and that file is bound, so both are read from the stack above the
    note: the first ``self`` there is what calls the model, whatever holds it and whatever that declares, and the nearest of its
    own frames that names a ``system`` and a ``user`` holds what it was given to send, whatever a wrapper was asked. A test holds
    both for each built-in caller. With no object between the note and ``counted``, a plain function noted it, or another thread
    did, and the stack cannot say: then it is what ``counted`` holds, as declared. Where no frame names that text it stays
    ``asked``, and what a caller of a known type does to the text below those frames cannot be seen."""
    frame = sys._getframe(2)
    while frame is not None and "self" not in frame.f_locals:
        frame = frame.f_back
    found = frame.f_locals["self"] if frame is not None else counted
    while frame is not None and frame.f_locals.get("self") is found:
        if "system" in frame.f_locals and "user" in frame.f_locals:
            asked = _request_digest(frame.f_locals["system"], frame.f_locals["user"])
            break
        frame = frame.f_back
    return (counted.inner if found is counted else found), asked


class _Requests:
    """What a tick has sent to its backends, the limit it reserved, and when the week it reserved in ends. Past either,
    nothing more goes out: a reset drops the ceiling to a new week's first day, and nothing is reserved under that one.
    ``stopped`` says which stopped it. A request takes its place in ``sent`` before it goes out, under one lock, so requests
    running at once can never pass the limit between them. During a tick each request looks at ``halted`` first, so a stop
    lands at the next model call: a request already sent comes back first, since nothing here can end one. ``barring`` is what
    a request's real sender is held to. After a refusal nothing more goes out that tick, and ``refused`` holds each request ever
    refused here, which never goes out again. It only grows, and ``keeping`` is told of each as it happens, before any record
    of the tick is written, so no failure of those records can send one twice."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 halted: Callable[[], _Halted | None] = lambda: None,
                 barring: Callable[[Any], _Halted | None] = lambda sender: None,
                 keeping: Callable[[str, str], None] = lambda request, said: None) -> None:
        self.now, self.halted, self.barring, self.keeping = now, halted, barring, keeping
        self.refused: set[str] = set()
        self._lock = threading.Lock()
        self.generation = 0
        self.close()

    def open(self, limit: float, ends: datetime | None = None) -> None:
        """A tick's limits, from here on. ``generation`` moves with each opening and closing, so a request that took its
        look at one tick cannot be admitted under the next, or under none."""
        with self._lock:
            self.sent, self.limit, self.ends, self.stopped, self.unseen, self.refusal = 0, limit, ends, "", False, None
            self.limited: tuple[datetime, str] | None = None
            self.generation += 1

    def close(self) -> None:
        self.open(math.inf)

    def ends_by(self, moment: datetime) -> None:
        """A provider has said the tick's week ends at ``moment``, which can come to light only once it is under way. What it
        reserved covers nothing after that. A later end buys it nothing, and outside a tick there is no week to end."""
        with self._lock:
            if self.ends is not None and moment < self.ends:
                self.ends = moment

    def admit(self, wait: float = 0.0, *, places: int = 0, generation: int | None = None,
              live: Callable[[], bool] = lambda: True) -> None:
        """Refuse what would pass the limit, and take ``places`` in ``sent`` in that same step. ``wait``: the seconds a
        server asked for before this one goes out. ``generation``: the tick the request's call began in, and ``live`` whether
        that call is still running, both checked again under the lock, as the stop is read outside it and the call can return,
        and its tick close, while it is read."""
        if self.refusal is not None:
            raise _Refused(self.refusal["said"])
        if self.limited is not None:
            raise _AtLimit(*self.limited)
        stopped = self.halted() if self.limit != math.inf else None
        if stopped is not None:
            raise stopped
        with self._lock:
            if self.refusal is not None:                  # one may have come while the stop was being read
                raise _Refused(self.refusal["said"])
            if self.limited is not None:                  # and a limit, met by another of the tick's requests
                raise _AtLimit(*self.limited)
            if (generation is not None and generation != self.generation) or not live():
                raise LLMError(f"{TICK_BUDGET_SPENT}: this request came after its call returned, so nothing reserved covers it")
            if self.unseen or self.sent >= self.limit:
                self.stopped = f"this tick has sent {self.sent} of the {self.limit:g} it reserved"
            elif self.ends is not None and self.now() + timedelta(seconds=wait) >= self.ends:
                self.stopped = f"this tick's week ended at {self.ends.isoformat()} and nothing is reserved in the new one"
            else:
                self.sent += places
                return
        raise LLMError(f"{TICK_BUDGET_SPENT}: {self.stopped}")

    def take(self, wait: float = 0.0, *, generation: int | None = None, live: Callable[[], bool] = lambda: True) -> None:
        """A request about to go out. It keeps its place whatever becomes of it: a failed one may have reached the model."""
        self.admit(wait, places=1, generation=generation, live=live)

    def from_sender(self, sender: Any) -> None:
        """Refuse a request its real sender may not send, before it goes out or takes a place."""
        barred = self.barring(sender) if self.limit != math.inf else None
        if barred is not None:
            raise barred

    def count(self, requests: int) -> None:
        """Requests that had no boundary to take a place at."""
        with self._lock:
            self.sent += requests

    def at_a_limit(self, failed: LLMError, generation: int | None = None) -> None:
        """End the tick when a request failed at a provider's limit (``met_a_limit``)."""
        until = limit_ends(str(failed), self.now())
        if until is not None:
            self.met_a_limit(until, str(failed)[:300], generation, failed)

    def met_a_limit(self, until: datetime, reason: str, generation: int | None = None, cause: BaseException | None = None) -> None:
        """End the tick at a provider's limit, and let nothing more of it go out: the limit is kept until the tick closes, so
        another of its requests, on another thread, meets it too. ``generation``: the tick the request began in; one whose
        tick has closed latches nothing on the next, and outside a tick there is none to end."""
        with self._lock:
            if self.limit == math.inf or (generation is not None and generation != self.generation):
                return
            self.limited = (until, reason)
        raise _AtLimit(until, reason) from cause

    def not_refused(self, request: str) -> None:
        """Refuse what was refused once, whoever asks."""
        if request in self.refused:
            raise LLMError(f"{CLASSIFIER_STOP}: this request was refused once, and nothing sends it again")

    def at_a_refusal(self, failed: Any, request: str, sent: str = "", generation: int | None = None) -> None:
        """End the tick when a classifier or a permission stopped a request, and let nothing more go out of it. ``failed``: what
        the request raised, or what ``WatchedClaude`` read before it raised anything. ``request``: the digest of what the tick
        asked for, and ``sent``: of what went out, where a wrapper made that another request. Neither is sent again.
        ``generation``: the tick the request began in; a refusal that comes once that tick has closed ends nothing of the
        next, though what it refused is still never sent again."""
        said = refused_by(str(failed))
        if said and self.limit != math.inf:
            with self._lock:
                if generation is None or generation == self.generation:
                    self.refusal = {"said": said, "request": sent or request, "asked": request}
            for known in {request, sent or request}:
                self.refused.add(known)
                self.keeping(known, said)
            raise _Refused(said)


class _Resends(list):
    """The resends of one call. ``g6.core.llm`` adds each to the open records of ``waits`` just before it waits and sends
    again, which is the only boundary a resend has, so adding to this record is the gate on it, as ``counting``'s observer
    is on a request. What ``take`` raises stops the resend, and leaves the caller as a failed request's ``LLMError``. That
    boundary comes before the wait and nothing marks its end, so a wakeup that runs late is not seen."""

    def __init__(self, take: Callable[[float], None], limited: Callable[[dict[str, Any]], None] = lambda wait: None) -> None:
        super().__init__()
        self.take, self.limited = take, limited

    def append(self, wait: dict[str, Any]) -> None:
        if wait.get("status") == 429:      # a provider's limit, however short the wait it asks: the tick ends, nothing resent
            self.limited(wait)
        self.take(wait["retry_after_s"])
        super().append(wait)


@contextmanager
def _gating_resends(take: Callable[[float], None], limited: Callable[[dict[str, Any]], None] = lambda wait: None) -> Iterator[None]:
    """As ``g6.core.llm.waits``, whose record is a plain list, with a record that is the gate above."""
    token = _WAITS.set((*_WAITS.get(), _Resends(take, limited)))
    try:
        yield
    finally:
        _WAITS.reset(token)


class _Counted:
    """A model caller the host meters. Every call of a tick passes through one, and what it counts is what reached a
    backend, not how often it was called: ``g6.core.model_calls`` notes each request as it goes out, so a retry inside a
    caller of a domain's own is charged attempt by attempt, and a ``Retry-After`` resend takes its place from
    ``g6.core.llm.waits``. Nothing a run reports about itself enters the count.

    Each noted request takes its place at that same boundary, before it goes out, so a retry inside a caller stops at what
    the tick reserved. ``counting`` asks its observers not to raise, because an observer is normally a bystander at someone
    else's request. This one is the gate on that request: it raises before anything is sent or counted, and what it raises
    is the ``LLMError`` a failed request raises, which every caller here passes on. That file is bound, so its sentence
    stays until an issuance window.

    A caller that answers with no request noted is refused, because nothing could say how often it asked. The limit of
    this: a caller that notes one request and retries unnoted looks like one request, and so do the Anthropic SDK's own
    retries. Only a usage record from the provider can show those, which is the relay of design section 3. A ``Retry-After``
    resend is admitted as a request is, at its own boundary (``_Resends``), whichever caller sent it. A wrapper that sends from
    a thread of its own is seen only when it answers, as a caller that noted nothing, because ``counting`` holds its observers
    in a context that thread does not have: the tick adopts nothing and the loop pauses, but what it sent has gone. Closing that
    takes a change to that bound file, which waits for an issuance window. One that copied the context has those observers, and
    a request it sends once the call has returned is refused: the call's place is gone, and its tick may be."""

    def __init__(self, inner: Any, requests: _Requests) -> None:
        self.inner, self.requests = inner, requests

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        generation = self.requests.generation
        self.requests.admit(generation=generation)
        asked = sent = _request_digest(system, user)
        self.requests.not_refused(asked)
        took: list[None] = []
        returned = False

        def take(wait: float = 0.0) -> None:
            self.requests.take(wait, generation=generation, live=lambda: not returned)

        def next_request() -> None:
            nonlocal sent
            sender, sent = _sender(self, asked)
            self.requests.from_sender(sender)
            read_stops(sender)
            take()                            # a place first, so a wrapper that keeps trying stops at what the tick reserved
            self.requests.not_refused(sent)
            took.append(None)

        def told(said: str, given: tuple[str, ...]) -> None:
            self.requests.at_a_refusal(said, asked, _request_digest(*given[:2]) if len(given) > 1 else sent, generation)

        def limited(wait: dict[str, Any]) -> None:
            hint = float(wait.get("retry_after_s") or 0.0)
            self.requests.met_a_limit(self.requests.now() + timedelta(seconds=max(hint, 0.0)),
                                      "HTTP 429: the provider's limit was met", generation)

        with counting(before_call=next_request) as noted, _gating_resends(take, limited), telling_stops(told):
            try:
                text = self.inner.complete(system, user, max_tokens=max_tokens)
            except LLMError as failed:
                self.requests.at_a_limit(failed, generation)
                self.requests.at_a_refusal(failed, asked, sent, generation)
                raise
            finally:
                returned = True
                # a child's totals have no boundary; one that took its place and is not yet noted gives none back
                self.requests.count(max(noted() - len(took), 0))
            if not noted():
                self.requests.unseen = True
                raise LLMError(f"{UNMETERED_CALLER}: a model caller answered with no request noted")
        return text


def _workflow(domain: Domain, cut: Cut, settings: LoopSettings, requests: _Requests) -> Workflow:
    run, judge = domain.execution(), domain.judge()
    models = {name: dataclasses.replace(model, caller=_Counted(model.caller, requests)) for name, model in run.models.items()}
    suite = Suite(dev=cut.tune, selection=cut.selection, held_out=cut.held_out,
                  provenance={"dev": "caller", "held_out": judge.answers_by})
    return Workflow(name=domain.name, task=run.task, checks=run.checks, oracle=judge.verdict, suite=suite,
                    models=models, objectives=tuple(o for o in default_objectives() if o.name in GATED),
                    policy=EvidencePolicy(max_eval_model_calls=settings.eval_calls, cost_margin=None),
                    optional_checks=run.optional_checks, pipeline_steps=run.pipeline_steps, feedback=run.feedback)


def _pool_problems(cut: Cut, policy: EvidencePolicy) -> list[tuple[str, str]]:
    needs = {"tuning": (cut.tune, 1), "selection": (cut.selection, 1), "held-out": (cut.held_out, policy.min_cases),
             "monitor": (cut.monitor, MIN_PAIRS)}
    return [(POOL_TOO_SMALL, f"the {name} split has {len(found)} cases and needs {least}")
            for name, (found, least) in needs.items() if len(found) < least]


def _answer_problems(cut: Cut) -> list[tuple[str, str]]:
    """Cases whose answer the loop cannot give its judge exactly as identified: one outside the admitted set
    (``answer_encoding``), or one the split's reference cannot render, which the evaluation count is kept by
    (``SuiteCase.ref``). Either, and anything at all that raises while an answer is read, is refused at enrolment, by case
    id only: an answer is never shown."""
    found = []
    for name, _ in SHARES:
        for case in getattr(cut, name):
            try:
                canonical(answer_from(answer_encoding(case.expected)))
            except Exception as exc:  # noqa: BLE001 - whatever cannot be read whole is refused, by case id
                found.append(f"{case.case_id} ({type(exc).__name__})")
    return [(ANSWER_UNIDENTIFIABLE, f"{len(found)} cases have an answer the loop cannot give its judge exactly, for example "
             f"{', '.join(found[:3])}: an answer is None, a bool, an int, a finite float, a str, a list or a dict with str "
             "keys, each of exactly that type, as a tree no deeper than 64")] if found else []


def _as_identified(cut: Cut) -> Cut:
    """``cut`` with each answer replaced by the one decoded from its encoding: a fresh tree that shares nothing with what the
    domain holds, which is what the judge is given, so what it reads is exactly what was identified."""
    return Cut(**{name: tuple(dataclasses.replace(case, expected=answer_from(answer_encoding(case.expected)))
                              for case in getattr(cut, name)) for name, _ in SHARES})


def _groups(cases: Sequence[SuiteCase]) -> list[str]:
    """The groups these cases name: a case's group is exposed when any of its members is, whatever its id or text."""
    return sorted({f"group:{c.meta['group']}" for c in cases if c.meta.get("group")})


def _overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _in_reach(root: Path, domain: Domain) -> list[str]:
    """What judges that lies in or around the loop's folder, which is the only place it writes: by path, and by the file
    system's own identity of each folder on the way, so another spelling of a folder is that folder, and whatever a link
    under a judge's root points at (a link there is refused all the same, ``_unpinned``)."""
    sealed, roots = domain.sealed(), _roots(domain)
    try:
        links = _links_under(roots)
    except FrozenUnreadable:
        links = []                               # a root that cannot be listed refuses at enrolment (``_unpinned``)
    guarded = {"G6's own source": str(_SOURCE),
               **{f"the judge's root {root}": str(root) for root in roots},
               **{f"a link under the judge's root, to {os.path.realpath(link)}": os.path.realpath(link) for link in links},
               **{f"sealed root {i}": r for i, r in enumerate(sealed.vault.roots if sealed is not None else ())}}
    return [name for name, path in guarded.items() if _same_place(root, Path(path))]


def _same_place(a: Path, b: Path) -> bool:
    """Whether ``a`` and ``b`` overlap, one being the other or lying in it: by path, or by the identity the file system
    gives a folder, compared along every folder above each."""
    return _overlap(os.path.normcase(str(a)), os.path.normcase(str(b))) or _within(a, b) or _within(b, a)


def _within(inner: Path, outer: Path) -> bool:
    try:
        held = outer.stat()
    except OSError:
        return False
    for place in (inner, *inner.parents):
        try:
            if os.path.samestat(place.stat(), held):
                return True
        except OSError:
            continue
    return False


def _is_link(path: Path) -> bool:
    """Whether ``path`` is a link or a junction; one that cannot be asked is taken for one."""
    try:
        return path.is_symlink() or path.is_junction()
    except OSError:
        return True


def _links_under(roots: Sequence[Path]) -> list[Path]:
    """Every link or junction that is a root or lies under one: what it points at is no part of the root's listing. A folder
    under a root that cannot be listed raises ``FrozenUnreadable``: what it holds is not known to hold no link."""
    found = []
    for root in roots:
        if _is_link(root):
            found.append(root)
            continue
        for here, dirs, files in os.walk(root, followlinks=False, onerror=_unlisted):
            found += [Path(here) / name for name in (*dirs, *files) if _is_link(Path(here) / name)]
            dirs[:] = [name for name in dirs if not _is_link(Path(here) / name)]
    return found


class FrozenUnreadable(OSError):
    """What judges could not be read whole: a file under a judge's root, a loaded module's source outside them, the domain's
    declaration, or a folder under a root that could not be listed. No reading stands on it: enrolment refuses it
    (``JUDGE_UNPINNED``), and every cycle and adoption refuses it as a frozen change while it stands."""


def _frozen_bytes(path: Any) -> str:
    """The one reader the freeze takes a file's bytes with: their SHA-256, or ``FrozenUnreadable`` on any failure at all,
    never a value that stands in for bytes it could not read."""
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - every failure to read is unreadable, never a digest
        raise FrozenUnreadable(f"{path} cannot be read ({type(exc).__name__})") from exc


def _unlisted(error: OSError) -> None:
    """``os.walk``'s handler: a folder it cannot list is unreadable, never an empty folder."""
    raise FrozenUnreadable(f"{getattr(error, 'filename', None) or 'a folder'} cannot be listed "
                           f"({type(error).__name__})") from error


#: The one kind of file under ``__pycache__`` the freeze leaves out: a bytecode cache named for an interpreter,
#: ``<name>.<tag>.pyc`` (``select.cpython-312.pyc``, ``select.cpython-312.opt-1.pyc``), which an import writes.
_INTERPRETER_CACHE = re.compile(r"[^.]+\.[A-Za-z0-9_-]+(\.opt-\d+)?\.pyc", re.IGNORECASE)


def envelope_sha256() -> str:
    """The enforcing code and where it is installed: no candidate changes the envelope (design 6.5), and a change a person
    made is theirs to accept by enrolling again. The place counts, so a run enrolled on a pinned install cannot be swapped
    for a working tree that holds the same bytes today. It is a tripwire, not custody: the code that compares this digest is
    among what it covers, so an edit to that code can skip the comparison. Holding it needs the read-only checkout of design
    6.5."""
    return digest({"code": manifest_sha256({**build_manifest(_SOURCE, patterns=ENVELOPE_PATTERNS), **_importable_caches(_SOURCE)}),
                   "install": os.path.normcase(str(_SOURCE))})


def _importable_caches(root: Path) -> dict[str, str]:
    """Each file under a ``__pycache__`` folder that Python could import as a module, as ``build_manifest`` gives files. A cache
    folder is a namespace package, so ``g6.x.__pycache__.name`` imports ``name.py``, ``name.pyc`` or an extension there. A cache
    a first import writes is named for its interpreter (``select.cpython-312.pyc``), which no import statement can name, so it is
    left out and a legitimate import changes nothing."""
    found = {}
    for path in root.rglob("*"):
        if in_cache(path.relative_to(root)) and path.is_file() and _importable(path.name):
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _importable(name: str) -> bool:
    """Whether an import statement could name a file called ``name``: an importable suffix, and no dot before it, which a
    cache named for its interpreter (``select.cpython-312.pyc``) has."""
    name = os.path.normcase(name)                   # Windows imports ``planted.PY`` as it does ``planted.py``
    suffix = next((s for s in sorted(importlib.machinery.all_suffixes(), key=len, reverse=True)
                   if name.endswith(os.path.normcase(s))), None)
    return suffix is not None and bool(name[:-len(suffix)]) and "." not in name[:-len(suffix)]   # importlib checks no names


def _profile_space(name: str, run: Execution) -> str:
    """The digest of what a profile may express in a domain: the values each field may take, as a set, and the checks and
    steps it may switch on, each by what it does (a check's code, a step's op and the source of what it runs), so the same
    edits in another order are the same domain and an action replaced under its own name is a new one."""
    def does(fn: Callable[..., Any]) -> str:
        fn = inspect.unwrap(fn)
        try:
            text = inspect.getsource(fn)
        except (OSError, TypeError):      # no source to read (a built-in, a callable object): its name stands for it
            text = ""
        return f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', type(fn).__qualname__)}\n{text}"

    return digest({"domain": name, "models": sorted(run.models),
                   "space": {k: sorted({repr(v) for v in values}) for k, values in sorted(run.space.space.items())},
                   "optional_checks": {k: check.code_sha() for k, check in sorted(run.optional_checks.items())},
                   "pipeline_steps": {k: [step.component, step.op, step.authority, step.effect, step.mandatory,
                                          hashlib.sha256(does(step.run).encode("utf-8", "backslashreplace")).hexdigest()]
                                      for k, step in sorted(run.pipeline_steps.items())}})


def _writable(value: Any) -> Any:
    """``value`` with every string, keys too, made writable as UTF-8: what cannot be (a lone surrogate) is spelled out with
    a backslash, so a row that repeats a domain's labels or an error's words can always be written."""
    if isinstance(value, str):
        return value.encode("utf-8", "backslashreplace").decode("utf-8")
    if isinstance(value, Mapping):
        return {_writable(k): _writable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_writable(v) for v in value]
    return value


def _judge_digest(judge: Judge | None, roots: Sequence[Path] | None = None) -> str:
    """What decides a verdict, pinned by files listed whole and never by following imports: the verdict function the domain
    selected, by its file and its qualified name, so another function in the same file is another judge, and every file
    under the roots that hold the domain's judging code, each by its bytes, as the envelope pins G6 (every importable
    suffix, namespace packages and importable caches included), so a helper it calls, or imports only once it runs, judges
    too. ``roots``: the folders the enrolment pins (``_roots``), or the verdict's own folder when none are given. G6's own
    package is the envelope's, and the installation's code its own."""
    if judge is None:
        return ""
    return digest({"verdict": _named(judge.verdict),
                   "roots": _pinned(roots if roots is not None else _roots_of([_code_file(judge.verdict)]))})


def _named(fn: Any) -> list[str]:
    """A callable as the enrolment names it: its file, its qualified name, and the line its code starts on, so two lambdas
    of one function are two names."""
    unwrapped = _unwrapped(fn)
    named = getattr(unwrapped, "__qualname__", None)
    where = _code_file(fn)
    code = getattr(unwrapped, "__code__", None)
    return [str(where) if where is not None else "", named if isinstance(named, str) else type(unwrapped).__qualname__,
            str(code.co_firstlineno) if isinstance(code, CodeType) else ""]


def _unwrapped(fn: Any) -> Any:
    while isinstance(fn, partial):
        fn = fn.func
    return inspect.unwrap(fn)


def _code_file(fn: Any) -> Path | None:
    """The file a callable's code is in: its source file; for a class or a callable object, its class's; for a built-in,
    its module's file, or the interpreter's when it is built into it. ``None`` when none is known, as for Python code that
    is in no file."""
    unwrapped = _unwrapped(fn)
    source = judge_source(unwrapped)
    if source is not None:
        return source.resolve()
    if inspect.isfunction(unwrapped) or inspect.ismethod(unwrapped):
        return None
    owner = getattr(unwrapped, "__objclass__", None) or (unwrapped if isinstance(unwrapped, type) else None)
    if owner is None and not inspect.isbuiltin(unwrapped):
        owner = type(unwrapped)                  # a callable object: its class's code is what runs
    source = judge_source(owner) if owner is not None else None
    if source is not None:
        return source.resolve()
    name = getattr(owner if owner is not None else unwrapped, "__module__", None)
    if not isinstance(name, str):
        return None
    if name in sys.builtin_module_names:
        return Path(sys.base_prefix).resolve()
    where = getattr(sys.modules.get(name), "__file__", None)
    return Path(where).resolve() if isinstance(where, str) and where else None


def _judging_code(domain: Domain) -> list[tuple[str, Any]]:
    """What decides a verdict, each by the name a person reads: the verdict and every check of the workflow."""
    judge, run = domain.judge(), domain.execution()
    return ([("the verdict", judge.verdict)] if judge is not None else []) + [
        (f"check {check.key}", check.run) for check in (*run.checks, *run.optional_checks.values())]


def _roots(domain: Domain) -> tuple[Path, ...]:
    """The folders that hold a domain's judging code, each pinned whole: those it declares as ``roots``, or else the folders
    its verdict and checks are in. G6's package and the installation are pinned by their own digests."""
    declared = getattr(domain, "roots", None)
    if declared is not None:
        return tuple(sorted({Path(root).resolve() for root in declared}))
    return _roots_of([_code_file(fn) for _, fn in _judging_code(domain)])


def _roots_of(files: Sequence[Path | None]) -> tuple[Path, ...]:
    """The folders that hold these files, leaving out G6's package and the installation."""
    return tuple(sorted({path.parent for path in files if path is not None and not _installed(path)}))


def _held_by(path: Path | None, roots: Sequence[Path]) -> bool:
    return path is not None and (_installed(path) or any(path.is_relative_to(root) for root in roots))


def _unpinned(domain: Domain) -> list[tuple[str, str]]:
    """Judging code whose file no listing pins: refused at enrolment, as what cannot be identified exactly is refused."""
    roots = _roots(domain)
    found = [f"{name} lives in {where or 'no file'}" for name, fn in _judging_code(domain)
             if not _held_by(where := _code_file(fn), roots)]
    problems = [(JUDGE_UNPINNED, "; ".join(found) + ", outside G6, the Python installation and every root this domain "
                 "declares, so no listing pins what it does")] if found else []
    given = [Path(root) for root in getattr(domain, "roots", None) or ()]       # as declared, before anything resolves it
    try:
        links = [root for root in given if _is_link(root)] + _links_under(roots)
    except FrozenUnreadable as exc:
        return problems + [(JUDGE_UNPINNED, f"what judges cannot be listed whole ({exc}), so no listing pins it")]
    return problems + ([(JUDGE_UNPINNED, f"a link or junction at or under the judge's roots, at {links[0]}, points at what "
                         "no listing of them pins: a root holds its files itself")] if links else [])


def _pinned(roots: Sequence[Path]) -> dict[str, str]:
    """Each root pinned whole: every regular file under it, of any suffix, by its bytes (data files, archives and any file
    under ``__pycache__`` as much as source), and any link or junction under it by that name alone, which refuses
    (``_unpinned``). Only a bytecode cache named for an interpreter is left out (``_INTERPRETER_CACHE``): an import writes
    one, and the runner reads none (the freeze's threat model is honest drift). A file that cannot be read, or a folder
    that cannot be listed, raises ``FrozenUnreadable``."""
    return {os.path.normcase(str(root)): ("a link" if _is_link(root) else manifest_sha256(_listing(root)) if root.is_dir()
                                          else "") for root in roots}


def _listing(root: Path) -> dict[str, str]:
    found = {}
    for here, dirs, files in os.walk(root, followlinks=False, onerror=_unlisted):
        for name in (*dirs, *files):
            path = Path(here) / name
            if _is_link(path):
                found[path.relative_to(root).as_posix()] = "a link"
            elif name in files and not (in_cache(path.relative_to(root)) and _INTERPRETER_CACHE.fullmatch(name)):
                found[path.relative_to(root).as_posix()] = _frozen_bytes(path)
        dirs[:] = [name for name in dirs if not _is_link(Path(here) / name)]
    return found


def _loaded_outside(roots: Sequence[Path]) -> list[list[str]]:
    """Every module loaded in this process whose file is neither G6's, the installation's, nor under a pinned root, each by
    its file's bytes. Code loaded from anywhere else is code no listing holds, so a module loaded later (a verdict's own
    import, made as it runs), or a change to any of them, is a change to what judges. A source that cannot be read raises
    ``FrozenUnreadable``."""
    held = [os.path.normcase(str(root)) + os.sep for root in (*roots, *_INSTALLED)]
    found: dict[str, str] = {}
    for module in list(sys.modules.values()):
        where = getattr(module, "__file__", None)
        if not isinstance(where, str) or not where:
            continue
        path = os.path.normcase(os.path.abspath(where))
        if not any(path.startswith(prefix) for prefix in held):
            found[path] = _frozen_bytes(path)
    return sorted([path, sealed] for path, sealed in found.items())


def _installed(path: Path) -> bool:
    """Whether ``path`` is G6's own package or lies in the Python installation: code another digest pins."""
    return any(path.is_relative_to(root) for root in _INSTALLED)


def _imported_files(verdict: Any) -> set[Path]:
    """The person's own files a verdict's module reaches through the modules loaded in this process, for whoever reads what
    a judge reaches. It pins nothing: ``_judge_digest`` pins files listed whole, and ``_loaded_outside`` every other module
    loaded."""
    todo, seen, found = [getattr(verdict, "__module__", None)], set(), set()
    while todo:
        name = todo.pop()
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        where = getattr(sys.modules.get(name), "__file__", None)
        if not where:
            continue
        path = Path(where).resolve()
        if _installed(path):
            continue
        found.add(path)
        for value in list(vars(sys.modules[name]).values()):
            if inspect.ismodule(value):
                todo.append(value.__name__)
                continue
            for owner in (value, type(value)):
                try:
                    todo.append(owner.__module__)
                except Exception:  # noqa: BLE001 - an object that will not say where it came from adds nothing to follow
                    pass
    return found


def _coded(row: dict[str, Any], statuses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The one rule that codes a cycle: of every status that held, the cycle's own end among them, the first in ``RANKED``,
    or else the last to hold, gives the row its outcome, code and reason. The row keeps every status, so a frozen change or
    a safety stop is never lost whatever ranks first."""
    held = [{name: status[name] for name in ("outcome", "code", "reason")} for status in statuses]
    if held:
        row |= min(reversed(held), key=lambda status: RANKED.index(status["code"]) if status["code"] in RANKED else len(RANKED))
    row["statuses"] = held
    return row


def _safety_in(row: Mapping[str, Any]) -> str:
    """The reason of the safety stop a cycle's row holds, ranked first among its statuses, or ``""``."""
    held = [status for status in (row, *row.get("statuses", ()))
            if status.get("code") in SAFETY_STOPS or status.get("outcome") == "sealed_breach"]
    return str(min(held, key=lambda status: SAFETY_STOPS.index(status["code"]) if status.get("code") in SAFETY_STOPS
                   else 0)["reason"]) if held else ""


def _differs(now: Mapping[str, str], breached: bool, recorded: Mapping[str, str]) -> list[str]:
    """The frozen things whose digest ``now`` is not the one ``recorded``, and ``sealed`` when its bytes were breached."""
    return sorted({name for name, value in now.items() if value != recorded.get(name)} | ({"sealed"} if breached else set()))


def _reloaded(loop: ShadowLoop) -> dict[str, Any] | None:
    """The tick's writer read again, or the reply when it cannot be: whatever the reload fails with, the stop reading says
    what it is, a stop in force (``STOP_UNREADABLE``) when the records do not read, and records that read but do not follow
    from what this handle held, ``LedgerError``, which ``_exclusive`` answers as records that cannot be trusted."""
    try:
        loop.log.reload()
    except Exception as exc:  # noqa: BLE001 - any failure of the reload is the reading's to say, never an escape
        halted = stop_in_force(loop.root)
        if halted.code != STOP_UNREADABLE:
            raise LedgerError(f"{type(exc).__name__}: {exc}") from exc
        reply = _blocked(STOP_UNREADABLE, halted.reason[:400], "A person makes the loop's records readable; a new "
                         "g6_improve_start lets it run again.")
        loop._beat(reply["completion_state"], STOP_UNREADABLE, reason=reply["message"])
        return reply
    return None


def _exclusive(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """One operation at a time per folder, on what is written and not on what this handle last read: a restart can
    overlap a tick that is still running, and a person's process is not the loop's. The lock is the operating system's,
    so it dies with its process, and a refusal is a reply, never a raised error. Records the stop decision stands on that
    do not read are that decision's to say: a stop in force, ``STOP_UNREADABLE``, as every reader says it."""

    @wraps(method)
    def invoke(self: ShadowLoop, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            with Coordination(self.root / ".tick").mutex():
                refused = _reloaded(self)
                if refused is not None:
                    return refused
                if self.mh is not None:
                    self.mh.refresh()
                return method(self, *args, **kwargs)
        except WorkspaceRefused:
            return _blocked(BUSY, "another operation is running on this folder; this one did nothing")
        except LedgerError as exc:
            reply = _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                             "A person inspects the folder. Nothing runs on records that do not check out.")
            self._beat(reply["completion_state"], RECORDS_UNREADABLE, reason=reply["message"])
            return reply

    return invoke


class ShadowLoop:
    def __init__(self, domain: Domain, root: str | Path, settings: LoopSettings, *, proposer: Any,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 clock: Callable[[], float] = time.monotonic,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None,
                 read_now: Callable[[datetime], None] | None = None,
                 domain_file: str | Path | None = None) -> None:
        self.domain, self.settings, self._now, self.domain_file, self._read_now = domain, settings, now, domain_file, read_now
        self.beat_fields: dict[str, Any] = {}     # what a runner adds to every heartbeat
        self.lost_cycles = 0                      # cycles in a row the log would not take: it cannot hold this count
        self.cannot_record = ""                   # why this process spends no more, set at MAX_FAILED_CYCLES of those
        self._on_record: tuple[str, dict | None, str] | None = None   # the records' identity, their start, and its revocation
        self.evaluating = False                   # this tick has begun its held-out evaluation
        self.waited = 0                           # ticks a budget wait turned away since the last cycle: a wait leaves no row
        self.tuned: list[dict[str, Any]] = []     # this tick's tuning cycles as they happen, for a row that a raise cut short
        self._frozen_seen: dict[str, str] | None = None   # the frozen digests this tick read before it developed anything
        self.requests = _Requests(lambda: self._now(), self._halted, self._barred,
                                  lambda request, said: note_refusal(self.root, request, said))
        self._proposer = _Counted(proposer, self.requests)
        self.root = Path(root).resolve()
        reach = _in_reach(self.root, domain)
        if reach:                                # before anything is written: this folder is all the loop ever writes
            raise ValueError(f"[{EVALUATOR_IN_REACH}] the loop's folder overlaps {', '.join(reach)}: it never writes "
                             "what judges it")
        self.log = CycleLog(self.root / CYCLE_LOG)
        self.meter = BudgetMeter(settings.windows, store=OperatorStore(self.root / "operator.db"),
                                 read_account=read_account)
        for provider, until in self.log.limits().items():
            self.meter.limit_reached(provider, until)
        self.problems = domain_problems(domain) + _unpinned(domain)
        if backends(proposer) > 1:
            self.problems.append((FALLBACK_MODEL, "the proposer can answer from more than one backend, so a limit on one would "
                                  "pass unseen and its calls could go where no window covers"))
        self.problems += caller_problems({"the proposer": proposer})
        if not settings.windows:
            self.problems.append((NO_BUDGET, "no provider window: an unknown budget stops the loop"))
        named = tuple(self.meter.windows)
        self.provider = settings.provider or (named[0] if len(named) == 1 else "")
        if named and self.provider not in named:     # the callers are the domain's: nothing here can see where they go
            self.problems.append((NO_BUDGET, f"the windows are for {', '.join(named)} and this folder's calls are stated to "
                                  f"go to {settings.provider or 'none of them'}: an allowance pays only for its own "
                                  "provider's requests, so LOOP must name the one they go to"))
        elif named:
            self.problems += provider_problems(self._callers(), self.provider)
        self._clock = clock
        self.cut, self.workflow, self.mh = None, None, None
        if not self.problems:
            self._bind()

    def _bind(self) -> None:
        """What this handle runs: the domain's cases as they stand, cut, and a harness on them with nothing kept from
        before. An enrolment does this again, so what a person froze is what runs, on this handle as on a new one."""
        cut = cut_pool(self.domain.development(), self.settings.seed)
        answers = _answer_problems(cut)
        self.cut = cut if answers else _as_identified(cut)     # the judge is given each answer as it was identified
        self.workflow = _workflow(self.domain, self.cut, self.settings, self.requests)
        # a workflow is digested by its answers' rendering, so answers that cannot be held are refused before it is read
        self.problems = _pool_problems(self.cut, self.workflow.policy) + (answers or self.workflow.problems())
        # the harness holds its own last check from here: what judges, read and compared by the one function the loop
        # calls too (``_still_frozen``), never a check a caller of ``adopt`` hands it
        self.mh = None if self.problems else adapt_shadow(self.workflow, self.domain.execution().factory,
                                                          workspace=self.root / "workspace", clock=self._clock,
                                                          before_evaluation=self._before_evaluation,
                                                          hold=lambda: self._still_frozen())

    def _before_evaluation(self) -> None:
        """Read the account once more before a held-out evaluation: a limit reads as a failed case, and an evaluation of
        failed cases spends one of the split's five on a reason untrue of the candidate. So a run already under a limit ends
        here, as one met in a request does, with nothing spent. The same read can move the end of the week, and the tick's
        requests stop there, as at the end it reserved in. The limit of this: only a read can show a Codex limit, so one that
        arrives during the evaluation still spends it, until an issuance window gives those callers a code."""
        if self._read_now is not None:
            now = self._now()
            self._read_now(now)
            seen = self.meter.reading(self.provider, now)
            if seen.wait_until is not None:
                raise _AtLimit(seen.wait_until, seen.reason)
            self.requests.ends_by(seen.ends)
        self.evaluating = True

    def _stop(self) -> Stop:
        """This folder's stop, which a tick and the runner read alike: if the runner read less, a tick refused for a stop
        would leave it going, and asking again. It is ``stop_in_force``'s reading of what is on disk, taken before each
        request. The start and any stop after it are kept against the digest of the very records' bytes they were parsed
        from, in that same reading, so a later reading parses again only when its bytes differ; records that cannot be read
        stop it."""
        if self.cannot_record:
            return Stop(CANNOT_RECORD, self.cannot_record)  # this process's own stop: the log took no cycle
        found = stop_in_force(self.root, known=self._on_record)
        self._on_record = None if found.code == STOP_UNREADABLE else (found.records_sha256, found.start, found.revoked)
        return found

    def stopped_by(self) -> str:
        """Why this folder is stopped, or ``""``."""
        return self._stop().reason

    def _halted(self) -> _Halted | None:
        """What stops a tick between two requests: this folder's stop, or G6's own."""
        found = self._stop()
        if found:
            return _Halted(found.code, found.reason)
        engaged = stop.engaged()
        return _Halted(stop.STOPPED, f"G6 is stopped: {engaged}") if engaged is not None else None

    def _barred(self, sender: Any) -> _Halted | None:
        """Why the caller really sending a request may not send it. Enrolment can only read what a domain declares, so a
        wrapper can declare one caller and send through another: this is the same rule, held to what goes out."""
        problems = sender_problems(sender, self.provider)
        return _Halted(*problems[0]) if problems else None

    def _calls_per_run(self) -> int:
        """The most one run can call: what the models declare, or, on G6's own executor with no step of the domain's, one
        call and a retry of an empty answer for each a profile can hold (``RecoveryStrategy`` adds one)."""
        run, method = self.domain.execution(), self.settings.method
        declared = max(m.max_calls_per_run for m in self.workflow.models.values())
        if run.factory is not None or run.pipeline_steps:
            return declared
        return min(declared, 1 + max(method.use_recovery, *run.space.space.get("retries", ()),
                                     (run.initial or {}).get("retries", 0)))

    def worst_case(self) -> int:
        """The most one tick can call: what its limits allow, and no more than the pool gives them to spend on. That is the
        audits, a measurement, each proposer and race of each cycle, and one evaluation. Each limit is checked before a
        run, so each can be passed by one run. A retry inside a caller is not foreseen here, and a tick stops at what it
        reserved all the same."""
        method, per_run, cut = self.settings.method, self._calls_per_run(), self.cut
        most, tries = method.candidates_per_cycle, method.reflect_calls
        remedies = 2 + len(self.workflow.pipeline_steps)     # a step or an example, and a retry: what recovery can offer
        one_profile = len(cut.tune) * per_run                # on every tuning case
        spent = method.propose_budget + per_run              # where one proposer's dev budget stops it

        def on_dev(profiles: int) -> int:
            return min(spent, profiles * one_profile)

        # a proposer reads the view the tick measured, so the head runs once, and failure traces ask one call of the proposer
        proposing = (1 + method.use_recovery * on_dev(remedies + (remedies > most) * most)
                     + method.use_reflection * (on_dev(tries) + tries) + method.use_edits * on_dev(tries))
        racers = 2 + method.use_recovery * min(most, remedies) + (method.use_reflection + method.use_edits) * min(most, tries)

        def racing(entered: int) -> int:                     # failure traces offer one, and a champion races again
            return min(method.select_budget + per_run,
                       per_run * race_calls(len(cut.selection), entered))

        one_case = per_run * (per_run + 2)                   # base, candidate and a control matched to the candidate's calls
        return (2 * per_run * min(self.settings.audit_per_tick, len(cut.monitor)) + on_dev(1)
                + method.cycles * proposing + (method.cycles - 1) * racing(racers)
                + racing(racers + len(self.mh.unfinished()))
                + min(self.settings.eval_calls + one_case, len(cut.held_out) * one_case))

    def _frozen(self) -> dict[str, str]:
        """What must not change, read fresh from the domain: the code that judges, every frozen case, and the sealed
        split, and the file that declares it all when a runner opened one. What judges is pinned by files listed whole: the
        roots that hold the verdict and the checks (``_roots``), each check by its file and name rather than its own
        ``code_sha``, and every module loaded from outside those, G6 and the installation (``_loaded_outside``). No profile
        is in it. ``profile_space`` is what a profile may express, which a new version of the domain changes and a change of
        method in the same file does not (``tick_rca``'s domain part)."""
        run, sealed, judge = self.domain.execution(), self.domain.sealed(), self.domain.judge()
        roots = _roots(self.domain)
        return {"envelope": envelope_sha256(), "domain_file": _frozen_bytes(self.domain_file) if self.domain_file else "",
                "profile_space": _profile_space(str(getattr(self.domain, "name", "")), run),
                "evaluator": digest({"judging": judging_sha256(), "judge": _judge_digest(judge, roots),
                                     "independence": [judge.answers_by, judge.is_model] if judge is not None else [],
                                     "task": run.task, "checks": [[c.predicate, c.key, c.kind, *_named(c.run)]
                                                                  for c in (*run.checks, *run.optional_checks.values())],
                                     "loaded": _loaded_outside(roots)}),
                "cases": cut_pool(self.domain.development(), self.settings.seed).digest(),
                "sealed": digest({"vault": sealed.vault.digest if sealed is not None else "",
                                  "refs": [dict(r) for r in sealed.refs] if sealed is not None else []})}

    def _frozen_now(self) -> tuple[dict[str, str], bool]:
        """The frozen digests as they stand, and whether the sealed bytes changed since they were sealed, in one reading
        with the slow part first: the sealed bytes are compared before anything is digested, so an edit made while they
        are read is in the digests that follow, and nothing comes after the digests but what acts on them."""
        sealed = self.domain.sealed()
        breached = sealed is not None and bool(sealed.vault.verify_unchanged())
        return self._frozen(), breached

    def _changed(self, against: Mapping[str, str] | None = None) -> list[str]:
        """Which of the frozen things differ from ``against``, the digests a tick read before it developed anything, or
        from what a person enrolled when none are given. The vault's digest is of the files as sealed, so the sealed bytes
        are compared as well. A reading that cannot be made whole (``FrozenUnreadable``) differs from every reading: it is
        named as what changed, so nothing stands on it."""
        recorded = self.log.enrolled()["frozen"] if against is None else against
        try:
            return _differs(*self._frozen_now(), recorded)
        except FrozenUnreadable as exc:
            return [f"what judges, which could not be read ({exc})"]

    def _invalid(self) -> dict[str, Any] | None:
        if self.problems:
            return _blocked(self.problems[0][0], "; ".join(why for _, why in self.problems),
                            "Give this domain what it lacks; the loop will not run without it.")
        if self.log.enrolled() is None:
            return _blocked(NOT_ENROLLED, "no person has enrolled this domain", "loop.enrol('<your name>')")
        return None

    def _refused(self) -> dict[str, Any] | None:
        """Why this tick is not a cycle. Each is a wait or a refusal: none spends, and none is a failed cycle."""
        halted = self._halted()
        if halted is not None:
            return _blocked(halted.code, halted.said, "Remove the STOP file to resume." if halted.code == STOPPED else
                            "A person makes the stop's files readable; a new g6_improve_start lets it run again."
                            if halted.code == STOP_UNREADABLE else "A person frees the log, then starts the loop again with "
                            "g6_improve_start." if halted.code == CANNOT_RECORD else "Whoever engaged the stop releases it.")
        invalid = self._invalid()
        if invalid is not None:
            return invalid
        lost = self._lost_generations()
        if lost:
            return _blocked(RECORDS_UNREADABLE, lost, "A person inspects the folder. Nothing runs on records that do not "
                            "check out.")
        paused = self.log.paused() or self._must_stop()
        if paused:
            return _blocked(PAUSED, f"the loop is paused: {paused}", "A person reads why, then loop.resume('<name>').")
        try:
            now, breached = self._frozen_now()
            changed = _differs(now, breached, self.log.enrolled()["frozen"])
        except FrozenUnreadable as exc:          # no reading of what judges stands, so none is compared or kept
            return _blocked(FROZEN_CHANGED, f"what judges could not be read ({exc}), so nothing measured against it would "
                            "mean what it did", "A person makes it readable; the next tick reads it again.")
        if changed:
            return _blocked(FROZEN_CHANGED, f"changed since a person enrolled this domain: {', '.join(changed)}. Nothing "
                            "measured against it would mean what it did", "A person checks the change and enrols again.")
        self._frozen_seen = now                  # what this tick's adoption must still find on disk as it is written
        lacking = self._lacking()
        if lacking is not None:
            return lacking
        if self.mh.held_out_left() <= 0:
            return _blocked(HELD_OUT_SPENT, "this held-out split has given all the evaluations it may: the gate needs a "
                            "fresh split, and only a person can confirm one", "A person freezes a fresh split and enrols.")
        return None

    def _lost_generations(self) -> str:
        """Why the chain of adoptions cannot be the one the log saw, or ``""``. A chain emptied or cut short still opens,
        and numbers from its length, so the log is what shows it: a generation it saw must be there. A chain ahead of the
        log is the crash between the two writes. Both cut together leave nothing to compare."""
        seen, held = self.log.generation(), len(self.mh.adoption_rows())
        return "" if seen <= held else (f"the log saw generation {seen} and the chain of adoptions holds {held}: a "
                                        "generation's number must never be used twice")

    def _callers(self) -> dict[str, Any]:
        """Every caller a tick can send through, as the domain declares them now."""
        models = self.domain.execution().models
        return {"the proposer": self._proposer.inner, **{name: model.caller for name, model in models.items()}}

    def _lacking(self) -> dict[str, Any] | None:
        """The domain checked as it is now: a handle's first check says nothing of what has changed since."""
        problems = domain_problems(self.domain) + _unpinned(self.domain) + provider_problems(self._callers(), self.provider)
        if not problems:
            return None
        return _blocked(problems[0][0], "; ".join(why for _, why in problems),
                        "Give this domain what it lacks; the loop will not run without it.")

    def _must_stop(self) -> str:
        """Why the durable record of cycles says stop, whether or not a pause was written: the cycles themselves are the
        fact, and the pause is only its note. Every status a cycle kept counts, so a safety stop that another status
        outranked (``_coded``) still stops the loop."""
        cycles = self.log.since_resumed()
        for cycle in reversed(cycles):
            for held in (cycle, *cycle.get("statuses", ())):
                if held.get("outcome") == "sealed_breach" or held.get("code") in SAFETY_STOPS:
                    return held["reason"]
        streak = self.log.failed_run()
        if len(streak) < MAX_FAILED_CYCLES:
            return ""
        return "three failed cycles in a row: " + "; ".join(
            f"tick {c['tick']} {c['outcome']} ({str(c.get('reason', ''))[:160]})" for c in streak)

    @_exclusive
    def enrol(self, confirmed_by: str) -> dict[str, Any]:
        """A person confirms what judges this domain, as it stands now. It is the one step the loop cannot do."""
        if self.problems:
            return self._invalid()
        who = confirmed_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person enrols a domain (no empty or auto: name)")
        lacking = self._lacking()
        if lacking is not None:
            return lacking
        self._bind()
        if self.problems:
            return self._invalid()
        reused = self._reused()
        if reused:
            return _blocked(SPLIT_REUSED, f"{len(reused)} held-out cases were already seen by proposers or spent by an "
                            f"earlier split, for example {reused[:3]}: a gain measured on them proves nothing",
                            "Give the gate cases the loop has not used: a split changed by a few cases is not fresh.")
        try:                                     # read before anything is confirmed: an unreadable freeze enrols nothing
            frozen = self._frozen()
        except FrozenUnreadable as exc:
            return _blocked(JUDGE_UNPINNED, f"what judges could not be read whole ({exc}), so no listing pins it and nothing "
                            "was enrolled", "A person makes it readable, then enrols.")
        confirmed = self.mh.confirm(who, initial=self.domain.execution().initial)
        if confirmed["completion_state"] == "blocked-escalated":
            return confirmed
        exposed = (*self.cut.tune, *self.cut.selection)
        self.log.enrol(who, frozen, domain=self.domain.name, at=self._now().isoformat(),
                       seen=sorted(c.case_id for c in exposed),
                       seen_inputs=sorted(c.ref()["input_sha256"] for c in exposed),
                       seen_groups=_groups(exposed),
                       held_out=sorted(c.case_id for c in self.cut.held_out),
                       held_out_inputs=sorted(c.ref()["input_sha256"] for c in self.cut.held_out),
                       held_out_groups=_groups(self.cut.held_out),
                       held_out_digest=self.workflow.suite.digest("held_out"))
        return _envelope("qualified-draft", ENROLLED, f"{self.domain.name} is enrolled: the loop may run on it", **frozen,
                         next_action="loop.tick()")

    def _reused(self) -> list[str]:
        """Held-out cases that cannot be fresh evidence. The evaluation limit counts per split digest, so a split
        changed by one case would start again from nothing, and a re-cut could hand the gate what proposers tuned on.
        Against each earlier enrolment: nothing it let proposers see, and nothing from a split it spent. A case is known
        by its id and by a hash of its input, so a rename does not make it new. An input reworded is a new hash: only a
        person sourcing cases can tell that apart."""
        current, seen = self.workflow.suite.digest("held_out"), set()
        evaluated = {r.get("split_digest") for r in self.mh.ledger.rows("evaluation")}
        for row in self.log.enrolments():
            seen |= {*row["seen"], *row["seen_inputs"], *row.get("seen_groups", ())}
            if row["held_out_digest"] != current and row["held_out_digest"] in evaluated:
                seen |= {*row["held_out"], *row["held_out_inputs"], *row.get("held_out_groups", ())}
        return sorted(c.case_id for c in self.cut.held_out if {c.case_id, c.ref()["input_sha256"], *_groups((c,))} & seen)

    @_exclusive
    def tick(self) -> dict[str, Any]:
        """One cycle. A heartbeat follows every tick, so someone watching can tell a loop that is waiting from one that
        has died."""
        self._frozen_seen = None                  # no tick adopts on a reading an earlier tick made
        reply = self._refused() or self._run()
        self._beat(reply["completion_state"], reply["warning_card"]["code"], reason=reply["message"])
        return reply

    @_exclusive
    def fault(self, exc: BaseException) -> dict[str, Any]:
        """A tick that raised past its own guard is a failed cycle like any other, so three in a row still pause. It ends on
        the same frozen check and the same rule as every cycle that adopts nothing (``_still_frozen``, ``_coded``), held to
        what the tick read before it developed anything, or to what a person enrolled when it read nothing, so a frozen
        change in a failed cycle is kept and ranked."""
        if self.log.paused():
            return _blocked(PAUSED, f"the loop is paused: {self.log.paused()}", "A person reads why, then lifts it.")
        row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error", "code": CYCLE_FAILED,
               "reason": f"{type(exc).__name__}: {exc}"[:500]}
        statuses: list[Mapping[str, Any]] = [row]
        try:
            held = self._still_frozen() if self._frozen_seen is not None else self._as_enrolled()
        except Exception as second:  # noqa: BLE001 - a failed cycle is recorded whatever else fails
            held = None
            row["reason"] = f"{row['reason']}; what judges could not be read ({type(second).__name__})"[:500]
        if held is not None and type(held) is not Held:
            statuses.append({"outcome": "blocked", "code": held["warning_card"]["code"], "reason": held["message"]})
        return self._record(_coded(row, statuses))

    def _as_enrolled(self) -> Held | dict[str, Any] | None:
        """What judges held to what a person enrolled, for a failed cycle that read nothing of its own; ``None`` unenrolled."""
        enrolled = self.log.enrolled()
        if enrolled is None:
            return None
        changed = self._changed()
        return _blocked(FROZEN_CHANGED, f"changed since a person enrolled this domain: {', '.join(changed)}. Nothing "
                        "measured against it would mean what it did") if changed else Held(dict(enrolled["frozen"]))

    @_exclusive
    def quiet(self) -> dict[str, Any]:
        """How many cycles in a row found nothing to try, and when the last began, from the records as they stand now, so
        a person's enrolment or resumption elsewhere counts. It runs nothing."""
        cycles = self.log.cycles()
        return _envelope("qualified-draft", STATUS, f"{self.log.quiet_streak()} ticks in a row found nothing to try",
                         quiet=self.log.quiet_streak(), last_at=cycles[-1].get("at", "") if cycles else "")

    def _beat(self, state: str, code: str, **said: Any) -> None:
        beat(self.root, **{**self.beat_fields, **said}, domain=self.domain.name, at=self._now().isoformat(),
             tick=len(self.log.cycles()), state=state, code=code)

    def _reserve(self) -> tuple[Admission | None, dict[str, Any] | None]:
        """This tick's worst case on the provider its calls go to, or the wait: room on another pays for none of them."""
        spend = self.meter.reserve(self.provider, self._now(), self.worst_case())
        if spend.admitted:
            return spend, None
        return None, _blocked(BUDGET_WAIT, f"{spend.provider}: {spend.reason}",
                              "The loop waits; it runs again when a ceiling or a reset allows.",
                              wait_until=spend.wait_until.isoformat() if spend.wait_until else "")

    def _run(self) -> dict[str, Any]:
        stops = [c["classifier_stop"] for c in self.log.cycles() if c.get("classifier_stop")]
        self.requests.refused |= refused_requests(self.root) | {kept[name] for kept in stops for name in ("request", "asked")
                                                                if name in kept}
        spend, waiting = self._reserve()
        if waiting is not None:
            self.waited += 1
            return waiting
        self._beat("running", CYCLE)
        opened = reflex.read(self.log.cycles())     # what limits progress, read before this cycle does anything
        recommended: list[dict[str, Any]] = []
        limit: _AtLimit | None = None
        halted: _Halted | None = None
        self.evaluating = False
        self.tuned = []
        self._rca: TickRecord | str | None = None     # what this tick's analysis reads once it has tuned, or why it could not
        self.requests.open(spend.amount, spend.ends)
        with sealed_closed(self.domain.sealed().vault) as breaches:
            try:                      # a sealed file already held open: no cycle starts over it
                row = self._row() if breaches else self._cycle(recommended)
            except _AtLimit as reached:
                row, limit = self._row(), reached
            except _Halted as reached:
                row, halted = self._row(), reached
            except _Refused:
                row = self._row()
            except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
                row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                       "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]}
            finally:             # use nobody could count stays charged at its worst case
                charged = self.meter.settle(spend, spend.amount if self.requests.unseen else self.requests.sent)
        unseen, stopped, refusal = self.requests.unseen, self.requests.stopped, self.requests.refusal
        self.requests.close()
        if self.tuned and "tuning" not in row:     # a tick cut short keeps what it had made
            row["tuning"] = reflex.funnel(self.tuned)
        held: list[dict[str, Any]] = []           # what held over the cycle, read only now: a strategy that swallowed a
        if breaches:                              # refusal adopts nothing either
            held.append({"outcome": "sealed_breach", "code": SEALED_BREACH,
                         "reason": f"{len(breaches)} sign(s) that the sealed split was within reach while developing. "
                                   "Nothing is adopted. A read through a handle already open leaves no trace, so a person "
                                   "decides whether it still counts as sealed"})
        if unseen:
            held.append({"outcome": "error", "code": UNMETERED_CALLER,
                         "reason": "a model caller answered with no request noted, so the loop cannot count what it sends: "
                                   "nothing is adopted, and a person gives it a caller that notes each request"})
        if refusal is not None:       # a cycle, so it is on the record, and a pause, so nothing rephrases it on the next tick
            row["classifier_stop"] = refusal
            held.append({"outcome": "blocked", "code": CLASSIFIER_STOP,
                         "reason": f"a classifier or a permission stopped a request ({refusal['said']}): nothing is "
                                   "adopted, it is not sent again, and a person reads this before the loop goes on"})
        if not held and limit is not None:   # a wait and no cycle, like any other: what ran is charged, none of it counts,
            self.meter.limit_reached(spend.provider, limit.until)     # nothing is adopted, and a frozen change stays on
            try:                                                      # disk for the next tick's reading to refuse
                self.log.limit(spend.provider, limit.until.isoformat())
            except (OSError, LedgerError):
                pass                  # this process still waits; after a restart the first request finds the limit again
            self.waited += 1
            return _blocked(BUDGET_WAIT, f"{spend.provider} met a limit after {charged:g} requests, so this tick scored nothing "
                            f"and waits until {limit.until.isoformat()}: {limit.said}",
                            "The loop waits; it runs again when the limit ends.", wait_until=limit.until.isoformat(),
                            charged=charged)
        if halted is not None and halted.code in BARRED:   # a fault that is on the record, and holds the loop for a person
            held.append({"outcome": "error", "code": halted.code,
                         "reason": f"{halted.said}: nothing was sent, and nothing is adopted"})
        elif not held and halted is not None:   # also no cycle, and as a limit: what ran is charged, none of it counts
            return self._cut_off(halted, charged)
        if stopped:                   # runs cut off mid-tick compared nothing: no gate result stands on them
            held.append({"outcome": "blocked", "code": TICK_BUDGET_SPENT, "reason": f"{stopped}, so nothing is adopted"})
        statuses = [row] if "outcome" in row else []   # the cycle's own end is a status like the rest
        adopted = False
        if recommended and not held:  # the harness's adoption runs its own frozen check last, under the lock it writes with
            statuses.append(self._adopt(recommended[-1]["decision"], row))
            adopted = statuses[-1]["outcome"] == "adopted"
            if adopted:
                row["head_version"] = statuses[-1]["head_version"]
        statuses += held
        if not adopted and all(status["code"] != FROZEN_CHANGED for status in statuses):
            frozen = self._still_frozen()        # every other cycle ends on the same check, whatever else held
            if type(frozen) is not Held:
                statuses.append({"outcome": "blocked", "code": frozen["warning_card"]["code"], "reason": frozen["message"]})
        _coded(row, statuses)
        if isinstance(self._rca, str):           # the read for it failed: the row says so
            row["rca"] = _writable({"unreadable": self._rca})
        elif self._rca is not None:               # read once the outcome is known: an adoption is part of what it reads
            row |= self._analysis(dataclasses.replace(self._rca, outcome=str(row.get("outcome", ""))))
        seen = self.meter.reading(spend.provider, self._now())
        row["budget"] = {"provider": seen.provider, "day": seen.day, "charged": charged, "used": seen.used,
                         "target": seen.target, "ceiling": seen.ceiling, "estimated": seen.estimated, "waited": self.waited}
        row["reflex"] = opened
        return self._record(row)

    def _cut_off(self, halted: _Halted, charged: float) -> dict[str, Any]:
        """A stop met at a model call: say what it cut off, and note this folder's stop in the records, which this tick
        holds, so no one else can. The file is what stops the loop, so a note that cannot be written changes nothing."""
        dropped = (", and the held-out evaluation under way was dropped, so none of the split's five was spent"
                   if self.evaluating else "")
        if halted.code in FOLDER_STOPS:
            try:
                self.log.stop(halted.said, at=self._now().isoformat(),
                              cut_off={"charged": charged, "evaluation": self.evaluating})
            except Exception:  # noqa: BLE001 - a note that cannot be written, for any reason, changes nothing
                pass
        return _blocked(halted.code, f"a stop ended this tick at a model call after {charged:g} requests{dropped}: {halted.said}",
                        "A new g6_improve_start lets it run again." if halted.code in FOLDER_STOPS else
                        "Whoever engaged the stop releases it.", charged=charged)

    def _record(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = self._reply(self.log.cycle(row))
        except (OSError, LedgerError) as exc:
            return self.lost_cycle(exc, refusal=row.get("classifier_stop"), safety=_safety_in(row))
        self.lost_cycles = self.waited = 0
        self._pause_if_failing()
        return reply

    def lost_cycle(self, exc: BaseException, *, refusal: dict[str, str] | None = None, safety: str = "") -> dict[str, Any]:
        """A cycle the log would not take. The streak that pauses the loop is read from the log, so these leave it nothing
        to read and are counted here, for as long as this process lives. At ``MAX_FAILED_CYCLES`` in a row the loop stops
        for good: ``STOP`` and ``stops.log`` are other files, so a restart stays closed until a person starts it again, and
        should those fail too, this process still spends no more. A cycle that is written starts the count again. A cycle
        that holds a safety stop (``safety``, its reason, or a classifier's ``refusal``) stops the loop at the first, since
        the pause a person must read would have stood on it, so a restarted handle reads it all the same."""
        self.lost_cycles += 1
        why = (f"{self.lost_cycles} of the {MAX_FAILED_CYCLES} cycles in a row that stop the loop could not be written to "
               f"its log ({type(exc).__name__}: {exc})")[:400]
        if safety:
            why = f"{safety[:200]}, and the log would not take that cycle ({type(exc).__name__}: {exc})"[:400]
        if refusal is not None:
            why = (f"a classifier or a permission stopped a request ({refusal['said']}) and the log would not take that cycle "
                   f"({type(exc).__name__}: {exc})")[:400]
        if refusal is not None or safety or self.lost_cycles >= MAX_FAILED_CYCLES:
            self.cannot_record = why
            try:
                ask_to_stop(self.root, why)
            except Exception:  # noqa: BLE001 - the run still ends on what is held here, whatever kept the file
                pass
        return _blocked(CANNOT_RECORD, why, "A person frees the log, then starts the loop again with g6_improve_start.")

    def _still_frozen(self) -> Held | dict[str, Any]:
        """The one reading and comparison of what judges at a cycle's end, which the loop and its harness both call: the
        frozen digests this tick read before it developed anything must be the ones on disk now, and then it returns a
        ``Held`` of them. Otherwise the cycle is ``FROZEN_CHANGED``, whether or not a candidate won, and a tick with no such
        reading is refused the same way."""
        seen = self._frozen_seen
        if seen is None:                         # nothing to hold the files to, so nothing to stand on
            return _blocked(FROZEN_CHANGED, "this cycle read no frozen digests before it developed anything, so nothing it "
                            "measured stands")
        changed = self._changed(against=seen)
        return _blocked(FROZEN_CHANGED, f"changed during this cycle: {', '.join(changed)}. What it measured is not what a "
                        "person enrolled, so nothing is adopted, and the next tick is refused while it stands"
                        ) if changed else Held(dict(seen))

    def _adopt(self, decision: Any, row: dict[str, Any]) -> dict[str, Any]:
        """Adopt ``decision`` through the harness, whose own last check is ``_still_frozen``: it runs that check last, with
        the row already made and under the lock it writes with, and the row carries the digests the check held. A change at
        any moment between is a refusal of its own, written in this cycle's row, and the next tick is refused while it
        stands."""
        adopted = self.mh.adopt(decision, findings={name: row[name] for name in FINDINGS if name in row})
        said = {"code": adopted["warning_card"]["code"], "reason": adopted["message"]}
        if adopted["completion_state"] == "blocked-escalated":
            return {**said, "outcome": "blocked"}
        return {**said, "outcome": "adopted", "head_version": adopted["head_version"]}

    def _pause_if_failing(self) -> None:
        reason = self._must_stop()
        if reason:
            try:
                self.log.pause(reason)
            except (OSError, LedgerError):
                pass                  # the cycles are already written, so the next tick is refused all the same

    @_exclusive
    def resume(self, accepted_by: str) -> dict[str, Any]:
        """A person lifts a pause, having read why. The loop cannot lift its own."""
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person lifts a pause (no empty or auto: name)")
        if self.log.paused() or self._must_stop():
            self.log.resume(who)
        return _envelope("qualified-draft", RESUMED, "the loop is not paused", next_action="loop.tick()")

    @_exclusive
    def demote(self, generation: int, asked_by: str) -> dict[str, Any]:
        """A person takes back the generation at the head, having read the digest. The loop goes on from what stood before."""
        who = asked_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person takes a generation back (no empty or auto: name)")
        invalid = self._invalid()
        if invalid is not None:
            return invalid
        lost = self._lost_generations()
        if lost:
            return _blocked(RECORDS_UNREADABLE, lost, "A person inspects the folder. Nothing runs on records that do not check out.")
        taken = self.mh.demote(generation, who)
        if taken["completion_state"] != "blocked-escalated":
            self.log.demote(who, at=self._now().isoformat(), undoes=taken["undoes"], head_version=taken["head_version"])
        return taken

    def _row(self) -> dict[str, Any]:
        version, head = self.mh._head()
        return {"at": self._now().isoformat(), "domain": self.domain.name, "head_version": version,
                "head_profile": head.digest(), "code": CYCLE, "reason": ""}

    def _cycle(self, recommended: list[dict[str, Any]]) -> dict[str, Any]:
        row = self._row()
        audited = self.mh.audit(self.cut.monitor, count=self.settings.audit_per_tick)
        row["audit"] = {k: audited[k] for k in ("audited", "pairs", "mean_difference", "threshold")}
        if audited["rolled_back"]:
            return {**row, "outcome": "rolled_back", "code": audited["warning_card"]["code"],
                    "reason": audited["message"], "undoes": audited["undoes"], "head_version": audited["head_version"]}
        measured = self.mh.measure(budget=self.settings.method.propose_budget)
        if "view" not in measured:
            return {**row, "outcome": "blocked", "code": measured["warning_card"]["code"], "reason": measured["message"]}
        view = measured["view"]
        row |= {"dev_success": view.dev_success, "dev_cases": len(view.dev)}
        if not view.failures:
            return {**row, "outcome": "idle", "code": IDLE, "reason": measured["message"]}
        records = self.domain.failures(view)
        self._tuning_failed = tuple(record.case_id for record in records)
        constraint = name_constraint(records, len(view.dev))
        row |= {"constraint": constraint.name, "basis": constraint.basis,
                "observations": [list(o) for o in constraint.observations],
                "failure_records": dict(Counter(r.cause for r in records)), "lessons": self._lessons(records)}
        if constraint.name == UNKNOWN:
            return {**row, "outcome": "no_constraint", "code": NO_CONSTRAINT, "reason": constraint.reason}
        return {**row, **self._exploit(constraint, recommended)}

    def _lessons(self, records: Sequence[FailureRecord]) -> list[dict[str, Any]]:
        """What this cycle's failures taught, as its row keeps them. This only records, so a domain's fault here costs the cycle nothing."""
        try:
            return [dataclasses.asdict(lesson) for lesson in reflex.lessons_from(
                records, getattr(self.domain, "root_cause", None), generation=self.log.generation(), tick=len(self.log.cycles()) + 1,
                scope=self.domain.name)]
        except Exception:  # noqa: BLE001 - see above: a failure that leaves no lesson row is allowed, and one that stops the work is not
            return []

    def _read_for_analysis(self, run: Any, marks: Mapping[str, int]) -> TickRecord:
        """What ``tick_rca`` reads of this tick: the rows tuning added to the ledger since ``marks``, the cases' labels where the
        domain gives them (``label_of``), and the digest of what a person decides. It reads the held-out report, so what it
        gives goes into the cycle's row and to no proposer, as the gate's verdicts do not."""
        new = {kind: self.mh.ledger.rows(kind)[count:] for kind, count in marks.items()}
        raced = {r.get("candidate_digest") for s in new["selection"] for r in s.get("ranking") or ()}
        made = {p.get("candidate_digest") for p in new["proposal"]}
        new["proposal"] += [p for p in self.mh.ledger.rows("proposal")[:marks["proposal"]]      # carried in from a tick before
                            if p.get("candidate_digest") in raced - made]
        report = next((r for r in reversed(new["report"]) if r.get("split") == "held_out"), None) if run.confirmation else None
        label_of = getattr(self.domain, "label_of", None)
        cases = (*self.cut.tune, *self.cut.selection, *self.cut.held_out)
        enrolled = self.log.enrolled() or {}
        return TickRecord(
            outcome="", labels=({c.case_id: str(label) for c in cases if (label := label_of(c)) is not None}   # no label: none
                                if callable(label_of) else {}),
            tuning_failed=getattr(self, "_tuning_failed", ()),
            held_out=tuple((c["case_id"], bool(c["base"]["success"]), bool(c["candidate"]["success"]))
                           for c in (report or {}).get("cases") or ()),
            races=tuple({"winner": s.get("winner"),
                         "ranking": [{"candidate": r.get("candidate_digest"), "wins": r.get("wins", 0), "losses": r.get("losses", 0),
                                      "n": r.get("n"), "dropped_at_block": r.get("dropped_at_block"),
                                      "net_by_block": list(r.get("net_by_block") or ())} for r in s.get("ranking") or ()]}
                        for s in new["selection"]),
            candidates=tuple({"candidate": p.get("candidate_digest"), "fields": sorted(p.get("diff") or {})} for p in new["proposal"]),
            power=dict((report or {}).get("power") or {}),
            enrolment={"method": self.settings.method.digest(),
                       "cut": digest({"held_out": enrolled.get("held_out_digest"),
                                      **{k: (enrolled.get("frozen") or {}).get(k) for k in ("cases", "sealed")}}),
                       "domain": str((enrolled.get("frozen") or {}).get("profile_space", ""))},
            champion=str(run.champion or ""))

    def _analysis(self, record: TickRecord) -> dict[str, Any]:
        """The tick's own root cause analysis, and the last one's scored on what this tick saw (``g6.metaharness.tick_rca``).
        This only records, so a fault in it costs the cycle nothing: the row says the analysis could not be read. What it
        records is written as UTF-8, so text that cannot be (a label's or an error's) is spelled out (``_writable``)."""
        try:
            found: dict[str, Any] = {"rca": tick_rca(record).to_dict()}
        except Exception as exc:  # noqa: BLE001 - see above
            return _writable({"rca": {"unreadable": f"{type(exc).__name__}: {exc}"[:200]}})
        try:
            before = next((c["rca"] for c in reversed(self.log.cycles()) if isinstance(c.get("rca"), dict) and "causes" in c["rca"]), None)
            if before is not None:
                found["rca_scored"] = [list(pair) for pair in scored(TickDiagnosis.from_dict(before), record)]
        except Exception:  # noqa: BLE001 - an unscored analysis is recorded as such
            found["rca_scored"] = None
        return _writable(found)

    def _exploit(self, constraint: Constraint, recommended: list[dict[str, Any]]) -> dict[str, Any]:
        """Propose and gate. A recommendation is only held here: it is adopted once the sealed split is known untouched."""

        def nobody(decided: dict[str, Any]) -> None:
            recommended.append(decided)              # no name is given: none stands in for a person's

        # earlier findings only: which candidate the gate took or refused is the held-out split's, and stays out of it
        strategy = FailureTraceStrategy(self._proposer, earlier=tuple(self.log.findings()))
        marks = {kind: len(self.mh.ledger.rows(kind)) for kind in ("proposal", "selection", "report")}
        run = tune(self.mh, self.settings.method, approve=nobody, reflector=self._proposer,
                   space=self.domain.execution().space, extra=(_OnConstraint(strategy, constraint.case_ids),),
                   carried=self.mh.unfinished(), cycles=self.tuned)
        try:                                      # only records: a fault reading for it costs the cycle nothing
            self._rca = self._read_for_analysis(run, marks)
        except Exception as exc:  # noqa: BLE001 - see above
            self._rca = f"{type(exc).__name__}: {exc}"[:200]
        reasons = (run.decision or {}).get("reasons") or run.notes or ["no candidate beat the head"]
        if (run.confirmation or {}).get("partial"):   # first, so the digest's one line for a tick keeps it
            reasons = ["the evaluation stopped part way and still spent one of the split's five", *reasons]
        return {"confirmation": run.confirmation, "decision": run.decision, "champion": run.champion,
                "outcome": run.outcome, "code": (run.decision or {}).get("code", CYCLE), "reason": "; ".join(reasons),
                "recovery": run.recovery, "tuning": reflex.funnel(run.cycles), **strategy.findings}

    @_exclusive
    def leave_shadow(self) -> dict[str, Any]:
        """Take the head out of the loop's own workspace. Nothing in this phase can. Outside shadow a person accepts a
        change, and whether a passed gate may ever stand in for them is the owner's ruling, held in one setting."""
        refused = self._invalid()
        if refused is not None:
            return refused
        version, head = self.mh._head()
        if not self.settings.person_free_adoption_outside_shadow:
            return _blocked(PERSON_REQUIRED, f"version {version} holds only in the loop's own workspace: outside it a "
                            "person accepts a change", "Take this profile to an ordinary meta-harness as a candidate: "
                            "mh.candidate(profile, proposer=...), for its gate and a person.",
                            candidate=head.model_dump(mode="json"))
        return _blocked(NO_PATH_OUT, "the owner's setting allows it, but this phase has no way out of shadow: no "
                        "adoption reaches anything outside this workspace", "A later phase builds the path.",
                        candidate=head.model_dump(mode="json"))

    def _reply(self, row: dict[str, Any]) -> dict[str, Any]:
        state = "qualified-draft" if row["outcome"] in ENDS_A_STREAK else "blocked-escalated"
        fields = {k: v for k, v in row.items() if k not in ("code", "reason")}
        return _envelope(state, row["code"], row["reason"], **fields)

    @_exclusive
    def status(self) -> dict[str, Any]:
        """Where the loop stands; it runs nothing."""
        refused = self._invalid()
        if refused is not None:
            return refused
        version, head = self.mh._head()
        return _envelope("qualified-draft", STATUS, f"head version {version} of {self.domain.name}", head_version=version,
                         generation=version, head_profile=head.digest(),
                         store_version=self.mh.store.current_approved(), scope=SCOPE,
                         paused=self.log.paused(), failed_streak=self.log.failed_streak(),
                         cycles=len(self.log.cycles()), held_out_left=self.mh.held_out_left(), g6_status=STATUS_LINE)


__all__ = ["Constraint", "LoopSettings", "ShadowLoop", "name_constraint"]

if __name__ == "__main__":
    from g6.metaharness.runner import main

    raise SystemExit(main())
