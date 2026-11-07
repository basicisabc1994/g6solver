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

import contextvars
import dataclasses
import hashlib
import importlib.machinery
import inspect
import math
import os
import sys
import threading
import time
import weakref
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import LedgerError, digest
from g6.core.llm import _WAITS, DEFAULT_MAX_TOKENS, LLMError
from g6.core.model_calls import counting
from g6.integrity import build_manifest, in_cache, manifest_sha256
from g6.metaharness.api import _blocked, _envelope, judging_sha256
from g6.metaharness.domain import (
    BARRED,
    FALLBACK_MODEL,
    Cut,
    Domain,
    Execution,
    FailureRecord,
    Judge,
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
from g6.metaharness.shadow import SCOPE, adapt_shadow
from g6.operator.store import OperatorStore
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    ENDS_A_STREAK,
    FINDINGS,
    MAX_FAILED_CYCLES,
    REFUSED_FILE,
    TICK_BUDGET_SPENT,
    CycleLog,
    ask_to_stop,
    beat,
    file_digest,
    note_refusal,
    refused_requests,
    stop_standing,
)
from g6.self_maintenance.pacing import AccountReading, Admission, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import (
    ProviderStop,
    Request,
    Send,
    limit_ends,
    read_stops,
    plain_text,
    refused_by,
    request_digest,
    telling_stops,
    watched,
)
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import WorkspaceRefused

NOT_ENROLLED = "G6_E_LOOP_NOT_ENROLLED"
POOL_TOO_SMALL = "G6_E_LOOP_POOL_TOO_SMALL"
EVALUATOR_IN_REACH = "G6_E_LOOP_EVALUATOR_IN_REACH"
STOPPED = "G6_E_LOOP_STOPPED"
PAUSED = "G6_E_LOOP_PAUSED"
RESUMED = "G6_E_LOOP_RESUMED"
FROZEN_CHANGED = "G6_E_LOOP_FROZEN_CHANGED"
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
UNNAMED_REQUEST = "G6_E_LOOP_UNNAMED_REQUEST"
#: What holds the loop for a person when a request of a tick meets it: a sender the loop may not send through, or a request
#: whose sender names nothing it could be known by once refused.
HOLDS = (*BARRED, UNNAMED_REQUEST)
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
    """What a request to a model is known by once it has been refused, where no host program's input can be seen
    (``usage.request_digest``): no text of it is kept, and no two requests share one."""
    return request_digest(system, user)


def _named(frame: Any) -> Request | str | None:
    """What a frame on the way out of a send names as the request sent: the digest of its ``system`` and ``user`` when it names
    both, as text; ``""``, which no digest is, when it names both and they are not both text, so nothing further out stands in
    for them; and ``None`` when it does not name both, or is one of this module's own frames, which name what a call was asked.
    The one reader of a request from frames, as it is noted or resent (``_sender``) and as it fails (``_raised_by``)."""
    if frame.f_globals.get("__name__") == __name__:
        return None
    names = frame.f_locals
    if "system" not in names or "user" not in names:
        return None
    system, user = names["system"], names["user"]
    return Request.text(system, user) if isinstance(system, str) and isinstance(user, str) else ""


def _sender(counted: Any) -> tuple[Any, Request | str]:
    """The caller sending the request being noted and what it sends, from ``counting``'s observer. ``g6.core.model_calls`` says
    only that one is going out, and that file is bound, so both are read from the stack above the note, up to ``counted``'s
    own call, passing over this module's own frames on the way (the observer, the resend gate). The first ``self`` there is the
    sender: what calls the model, whatever holds it and whatever that declares. What it sends is what the innermost frame names
    as a ``system`` and a ``user`` (``_named``), up to the sender's outermost frame: the functions and methods it called, its
    own frames and whatever runs between them, whichever of those sends. A frame further out is a wrapper's, which names what
    it was asked and not what went out. With no object between the note and ``counted``, a plain function noted it, or another
    thread did: the sender is what ``counted`` holds, as declared, and any frame there may name the request. ``""`` when none
    names it as text: then the request is not sent (``_Requests.unnamed``), since once refused nothing could keep it from going
    out again. Tests send through each kind of caller the loop accepts. A host caller is known by what its program is started
    with, at the step that starts it (``usage.request_identity``), and not by this. What a sender does to the text below the
    frame that names it cannot be seen."""
    frames, frame = [], sys._getframe(1)
    while frame is not None and not (frame.f_code is _Counted.complete.__code__ and frame.f_locals.get("self") is counted):
        if frame.f_globals.get("__name__") != __name__:
            frames.append((frame, frame.f_locals.get("self")))
        frame = frame.f_back
    sender = next((own for _, own in frames if own is not None), None)
    if sender is not None:                        # the sender's outermost frame: past it, a wrapper's, whatever it was asked
        del frames[max(at for at, (_, own) in enumerate(frames) if own is sender) + 1:]
    identity = next((named for named in (_named(found) for found, _ in frames) if named is not None), "")
    return (counted.inner if sender is None else sender), identity


#: The one lock every send in this process is admitted under, whichever meter it is reached through, and what a send held for
#: an identical one waits on. Each meter holds it as ``_lock``, so there is one lock however many meters are around a send.
_LOCK = threading.Lock()
_SETTLED = threading.Condition(_LOCK)


@dataclass(frozen=True, eq=False)
class _Call:
    """A metered call open in a context: whose it is (``owner``), the meter it charges, the tick that meter was in when the
    call began (``None``: whichever is open), and whether the call is still running."""

    owner: object
    meter: Any
    generation: int | None
    live: Callable[[], bool]


#: The metered calls open in this context, innermost last.
_ACTIVE: contextvars.ContextVar[tuple[_Call, ...]] = contextvars.ContextVar("g6_metered_calls", default=())
#: The sends let out in this context, and in the one it was copied from while they were out: a send made inside one of them
#: never waits for it, which would be waiting for itself.
_AROUND: contextvars.ContextVar[tuple[Send, ...]] = contextvars.ContextVar("g6_sends_around", default=())


@dataclass
class _Book:
    """What the record holds of one metered call while it is open: each send let out inside it, a nested call's too, so a
    failure is read by the send that raised it whichever call it comes out of; the notes let in inside it; and what calls
    inside it settled of their children's totals."""

    sends: list[Send] = field(default_factory=list)
    notes: int = 0
    settled: int = 0


@dataclass
class _Folder:
    """What this process alone knows of one loop's folder, whichever of its handles met it: each request refused there, which
    the folder's store may not hold (``refused``); why this process sends nothing more from it (``cannot_record``), which only
    the process's end lifts; and how many cycles in a row its log would not take (``lost_cycles``). Every handle of the folder
    reads it, one made after it too. A restart has none of it: then only what the folder's files hold stands."""

    refused: set[str] = field(default_factory=set)
    cannot_record: str = ""
    lost_cycles: int = 0


class _Record:
    """Every send this process lets out to a model, nested or not, on any thread, a resend included, and every child's totals
    a call settles: the one record of what was sent. A meter keeps no count of its own: what it has taken is read from here
    (``_Requests.sent``, ``_Requests.in_flight``). It is written only under ``_LOCK``: by admission, which takes a place on each
    meter around a send and makes the send's record in one step (``_Requests.admit``), by a send's outcome
    (``_Requests.release``), and by the end of a call, which settles what its children imported (``_Requests.settle``). It also
    holds what this process alone knows of each loop's folder (``_Folder``), which admission reads, under that lock, through
    each handle of the folder."""

    def __init__(self) -> None:
        self.out: dict[str, list[Send]] = {}         # each send let out whose outcome is not yet read, by what it sent
        self.taken: weakref.WeakKeyDictionary[Any, dict[int, int]] = weakref.WeakKeyDictionary()   # meter: tick: places
        self.books: dict[object, _Book] = {}         # each metered call open, by its owner
        self.folders: dict[str, _Folder] = {}        # what this process alone knows of each loop's folder, by its path

    def charge(self, meter: Any, generation: int, places: int) -> None:
        held = self.taken.setdefault(meter, {})
        held[generation] = held.get(generation, 0) + places

    def folder(self, root: str | Path) -> _Folder:
        """What this process knows of the loop's folder at ``root``, however its path is spelled: it is known by the path the
        file system resolves it to, links and short names followed, compared as the platform compares paths."""
        return self.folders.setdefault(os.path.normcase(os.path.realpath(root)), _Folder())


_RECORD = _Record()


def _meters(calls: Sequence[_Call]) -> tuple[tuple[Any, int | None], ...]:
    """Each meter around these calls once, outermost first, with the tick its outermost call began in."""
    found: list[tuple[Any, int | None]] = []
    for call in calls:
        if not any(meter is call.meter for meter, _ in found):
            found.append((call.meter, call.generation))
    return tuple(found)


@dataclass(frozen=True)
class _Ended:
    """What a tick had latched as it closed, read in the same step that closed it: what decides how the tick ends. ``tick``
    names this closing, for the records of what it met."""

    sent: int = 0
    unseen: bool = False
    stopped: str = ""
    refusal: dict[str, Any] | None = None
    limited: tuple[datetime, str] | None = None
    met: tuple[tuple[datetime, str], ...] = ()
    tick: str = ""


class _Requests:
    """A meter: the limit a tick reserved, when the week it reserved in ends, and what the tick latched. Past either limit,
    nothing more goes out: a reset drops the ceiling to a new week's first day, and nothing is reserved under that one.
    ``stopped`` says which stopped it. ``barring`` is what a request's real sender is held to during a tick.

    A meter keeps no count: what its tick has taken is the one record's (``_Record``), which every send in the process goes
    into. Every send passes ``admit``, tick or no tick, once, at the place it leaves: as it is noted, for any caller but a
    watched host caller, and where a watched host caller's program is about to start, for that one; a ``Retry-After`` resend
    passes it too, and the start of a call passes it with no send of its own. It is let in once for every meter around it,
    which are the meters of the metered calls open where it is made, nested or not, on whatever thread: one step under the one
    lock reads each of their stops and latches and what each holds as refused, takes one place on each, and makes the send's
    record (``usage.Send``), which is all that is known of the send. What a provider said of it goes to each meter that record
    took a place on, and it is released by its own outcome (``release``). A call's children's totals, which pass no boundary,
    are settled once, into the record, as the call ends (``settle``).

    ``halted`` is read first, and read again after any wait, so a stop, or a refusal this process could not keep, refuses
    everything after it. After a refusal nothing more of its tick goes out, and what was refused is never sent again: what
    ``refused`` holds, every request refused through this meter, and what ``on_record`` reads at every admission, which for a
    loop's meter is the store and what this process holds for the folder (``_Folder``), so another handle of the same loop, in
    this process or another, refuses it too. ``keeping`` is told of each refusal under
    the lock and before any other thread can see it. A send waits while an identical one is out on another thread
    (``_out_elsewhere``), so one let in before a refusal of the same bytes was kept cannot leave after it. A tick ends in one
    step under the lock (``close``), which keeps what it latched as ``closed``: a limit or a refusal is in what the tick ends
    on, or was met once it had ended, and is never lost between."""

    #: What a tick latches on its meter: per tick, never per request, and no count.
    TICK_STATE = ("limit", "ends", "stopped", "unseen", "refusal", "limited", "met", "generation", "closed")

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC),
                 halted: Callable[[], _Halted | None] = lambda: None,
                 barring: Callable[[Any], _Halted | None] = lambda sender: None,
                 keeping: Callable[[str, str], None] = lambda request, said: None,
                 on_record: Callable[[], set[str]] = frozenset) -> None:
        self.now, self.halted, self.barring, self.keeping, self.on_record = now, halted, barring, keeping, on_record
        self.refused: set[str] = set()
        self._lock, self._settled = _LOCK, _SETTLED
        self.generation = 0
        self._reset(math.inf, None)
        self.closed = _Ended()

    @property
    def sent(self) -> int:
        """What this meter's tick has taken, read from the one record: each send let out under it, each refused one it charged,
        and what its calls' children imported."""
        return _RECORD.taken.get(self, {}).get(self.generation, 0)

    @property
    def in_flight(self) -> dict[str, list[Send]]:
        """This meter's sends that are out, read from the one record."""
        return {identity: mine for identity, held in list(_RECORD.out.items())
                if (mine := [send for send in held if any(meter is self for meter, _ in send.charged)])}

    def open(self, limit: float, ends: datetime | None = None) -> None:
        """A tick's limits, from here on."""
        with self._lock:
            self._reset(limit, ends)

    def _reset(self, limit: float, ends: datetime | None) -> None:
        """A tick's limits, and nothing taken under it yet. ``generation`` moves with each opening and closing, so a request
        that took its look at one tick cannot be admitted under the next, or under none. Called under the lock."""
        self.limit, self.ends, self.stopped, self.unseen, self.refusal = limit, ends, "", False, None
        self.limited: tuple[datetime, str] | None = None
        self.met: tuple[tuple[datetime, str], ...] = ()
        self.generation += 1
        _RECORD.taken[self] = {}

    def close(self) -> None:
        """End the tick in one step under the lock: what it latched is kept as ``closed``, and nothing is admitted under it
        after. So a limit or a refusal is in what the tick ends on, or was met once it had ended."""
        with self._lock:
            self.closed = _Ended(self.sent, self.unseen, self.stopped, self.refusal, self.limited, self.met, uuid4().hex)
            self._reset(math.inf, None)

    def ends_by(self, moment: datetime) -> None:
        """A provider has said the tick's week ends at ``moment``, which can come to light only once it is under way. What it
        reserved covers nothing after that. A later end buys it nothing, and outside a tick there is no week to end."""
        with self._lock:
            if self.ends is not None and moment < self.ends:
                self.ends = moment

    def _around(self, generation: int | None) -> tuple[tuple[Any, int | None], ...]:
        """The meters around a request made here: those of the metered calls open in this context, and this one, with the
        tick each call began in (``generation`` for this one where no call here is on it)."""
        around = _meters(_ACTIVE.get())
        return around if any(meter is self for meter, _ in around) else (*around, (self, generation))

    def _out_elsewhere(self, identity: str, owner: object, thread: int) -> bool:
        """Whether a send of ``identity`` is out on another thread, in the one record, whose outcome this one waits for: a
        watched one, which its own outcome releases, or one of another call. Never one this send is made inside of, nor an
        unwatched one of this same call: that is released only as the call ends, so waiting for it would wait for itself."""
        around = _AROUND.get()
        return any(held.thread != thread and not any(held is mine for mine in around)
                   and (held.watched or held.owner is not owner) for held in _RECORD.out.get(identity, ()))

    def _full(self, places: int, wait: float) -> bool:
        """Whether this tick has no room for ``places`` more, or its week ends before a send that waits ``wait`` seconds could
        leave; ``stopped`` then says which. Called under the lock."""
        if self.unseen or self.sent + places > self.limit or (places == 0 and self.sent >= self.limit):
            self.stopped = f"this tick has sent {self.sent} of the {self.limit:g} it reserved"
            return True
        if self.ends is not None and self.now() + timedelta(seconds=wait) >= self.ends:
            self.stopped = f"this tick's week ended at {self.ends.isoformat()} and nothing is reserved in the new one"
            return True
        return False

    def admit(self, wait: float = 0.0, *, places: int = 0, generation: int | None = None,
              live: Callable[[], bool] = lambda: True, identity: str | Request | None = None, owner: object = None,
              identify: Callable[[], str | Request] | None = None, watched: bool = False, sender: Any = None) -> Send | None:
        """The one admission of every send in the process, nested or not, on any thread, a resend included, as one step under
        the one lock. The calls around the send are the metered calls open in this context, and one on this meter from
        ``owner``, ``generation`` and ``live`` where none of those is. Everything it decides on is read inside the lock, for
        every meter around the send: the bytes it will carry, each meter's stop, latches and refused requests (its own and its
        store's), and the tick each call began in and whether it is still running. Then, in that same step, it takes ``places``
        on each of those meters once and makes the send's record, before the lock is let go: nothing read before it decides
        anything. ``wait``: the seconds a server asked for before this one goes out. ``identity``, or ``identify`` to read it
        from the bytes as they are now: what is about to be sent, never sent again once refused, and held while an identical
        send is out elsewhere; after each wait all of it is read again. With neither, nothing is sent here, and no record is
        made."""
        here = threading.get_ident()
        with self._lock:
            calls = _ACTIVE.get()
            if not any(call.meter is self for call in calls):
                calls = (*calls, _Call(owner, self, generation, live))
            meters = [meter for meter, _ in _meters(calls)]
            while True:
                barred: set[str] = set()
                for meter in meters:
                    stopped = meter.halted()
                    if stopped is None and (identity is not None or identify is not None):
                        try:
                            barred |= meter.refused | set(meter.on_record())
                        except LedgerError as exc:
                            stopped = _Halted(RECORDS_UNREADABLE, f"what was refused here cannot be read, so nothing could "
                                                                  f"say what must not be sent: {exc}"[:400])
                    if stopped is not None:
                        if meter.limit != math.inf:
                            raise stopped
                        raise LLMError(f"{stopped.code}: {stopped.said}")
                # Snapshot after every stop/store read, including after a wait. No later read names this send.
                request = identify() if identify is not None else identity
                known = request.identity if isinstance(request, Request) else request
                if identify is not None:
                    for meter in meters:
                        stopped = meter.halted()
                        if stopped is not None:
                            if meter.limit != math.inf:
                                raise stopped
                            raise LLMError(f"{stopped.code}: {stopped.said}")
                for meter in meters:
                    if meter.refusal is not None:
                        raise _Refused(meter.refusal["said"])
                    if meter.limited is not None:
                        raise _AtLimit(*meter.limited)
                if any((call.generation is not None and call.generation != call.meter.generation) or not call.live()
                       for call in calls):
                    raise LLMError(f"{TICK_BUDGET_SPENT}: this request came after its call returned, so nothing reserved "
                                   "covers it")
                if known is not None and self._out_elsewhere(known, owner, here):
                    self._settled.wait(1.0)               # an identical send is out: what becomes of it decides
                    continue
                full = next((meter for meter in meters if meter._full(places, wait)), None)
                if full is not None:
                    break
                for meter in meters:                      # a place first, so a wrapper that keeps trying stops at the limit
                    _RECORD.charge(meter, meter.generation, places)
                if known is not None and known in barred:
                    raise LLMError(f"{CLASSIFIER_STOP}: this request was refused once, and nothing sends it again")
                if known is None:
                    return None
                record = Send(known, watched, owner, self.generation, here, sender, places,
                              tuple((meter, meter.generation) for meter in meters),
                              request if isinstance(request, Request) else None)
                _RECORD.out.setdefault(known, []).append(record)
                for call in calls:
                    book = _RECORD.books.get(call.owner)
                    if book is not None:
                        book.sends.append(record)
                _AROUND.set((*_AROUND.get(), record))
                return record
        raise LLMError(f"{TICK_BUDGET_SPENT}: {full.stopped}")

    def take(self, wait: float = 0.0, *, generation: int | None = None, live: Callable[[], bool] = lambda: True,
             identity: str | Request | None = None, owner: object = None, identify: Callable[[], str | Request] | None = None,
             watched: bool = False, sender: Any = None) -> Send | None:
        """A send about to go out, which takes one place on each meter around it: it keeps them whatever becomes of it, as a
        failed one may have reached the model."""
        return self.admit(wait, places=1, generation=generation, live=live, identity=identity, owner=owner,
                          identify=identify, watched=watched, sender=sender)

    def begin(self, call: _Call) -> None:
        """A metered call begins: what stops or latches a meter around it, or has ended a call around it, refuses it here, with
        no send of its own; then the record opens its book."""
        self.admit(generation=call.generation, live=call.live, owner=call.owner)
        with self._lock:
            _RECORD.books[call.owner] = _Book()

    def noted(self, calls: Sequence[_Call]) -> None:
        """A note let in at its boundary, which ``model_calls`` counts in the scope of each of ``calls`` once it is let in."""
        with self._lock:
            for call in calls:
                book = _RECORD.books.get(call.owner)
                if book is not None:
                    book.notes += 1

    def sends_within(self, owner: object) -> list[Send]:
        """Every send let out inside the call ``owner`` names, a nested call's too, in the order they were let out."""
        with self._lock:
            book = _RECORD.books.get(owner)
            return list(book.sends) if book is not None else []

    def settle(self, calls: Sequence[_Call], counted: Callable[[], int]) -> None:
        """The end of the innermost of ``calls``, the calls around it, itself last: what its children imported, which passes
        no boundary, is settled once, into the record. That is what its scope counted (``counted``) beyond the notes let in
        inside it and what the calls inside it settled already. It takes its places on each meter around the call once, under
        the tick each call began in, and every call around this one counts it as settled, so none of them charges it again. A
        note refused at its boundary was never counted, so it offsets nothing."""
        with self._lock:
            book = _RECORD.books.pop(calls[-1].owner, None)
            imported = max(counted() - book.notes - book.settled, 0) if book is not None else 0
            if imported:
                for meter, began in _meters(calls):
                    _RECORD.charge(meter, meter.generation if began is None else began, imported)
                for call in calls[:-1]:
                    around = _RECORD.books.get(call.owner)
                    if around is not None:
                        around.settled += imported

    def release(self, done: Send | object) -> None:
        """A send's outcome is in (its record), or a call is over (its owner): what it had out is out no longer, and an
        identical send held for it may go on. Only that send's own entry goes."""
        with self._lock:
            for identity, held in list(_RECORD.out.items()):
                kept = [record for record in held if record is not done and record.owner is not done]
                if kept:
                    _RECORD.out[identity] = kept
                else:
                    del _RECORD.out[identity]
            self._settled.notify_all()

    def recheck(self, generation: int) -> None:
        """What the tick latched decides, not what a request raised or a wrapper caught: a call of a tick that met a refusal
        or a limit leaves as that, whichever way the call came back."""
        with self._lock:
            if self.limit == math.inf or generation != self.generation:
                return
            refusal, limited = self.refusal, self.limited
        if refusal is not None:
            raise _Refused(refusal["said"])
        if limited is not None:
            raise _AtLimit(*limited)

    def from_sender(self, sender: Any) -> None:
        """Refuse a request its real sender may not send, on any meter around it whose tick is open, before it goes out or
        takes a place."""
        for meter, _ in self._around(None):
            barred = meter.barring(sender) if meter.limit != math.inf else None
            if barred is not None:
                raise barred

    def unnamed(self) -> None:
        """Refuse a request whose sender names nothing it could be known by (``_sender``), before it goes out or takes a place:
        once refused, nothing could keep it from going out again. In a tick it leaves the tick and holds the loop for a person
        (``HOLDS``), as a sender the loop may not send through does; outside one it is a failed request."""
        said = ("no frame of the caller sending this request, or of a function or method it called, names its system text and "
                "request as a system and a user, so nothing could keep it from being sent again once refused")
        if any(meter.limit != math.inf for meter, _ in self._around(None)):
            raise _Halted(UNNAMED_REQUEST, said)
        raise LLMError(f"{UNNAMED_REQUEST}: {said}")

    def _targets(self, request: Send | str | None, generation: int | None) -> tuple[tuple[Any, int | None], ...]:
        """The meters what a provider said of a send is about: each one the send's record took a place on, with the tick it
        took it under; or, where there is no record, each one around the request here."""
        if isinstance(request, Send) and request.charged:
            return request.charged
        return self._around(generation)

    def at_a_limit(self, failed: Any, generation: int | None = None, request: Send | str | None = None) -> None:
        """End the tick when a request failed at a provider's limit or its model at capacity (``met_a_limit``). ``failed``:
        what the request raised, or what a watched caller read before it raised anything."""
        until = limit_ends(str(failed), self.now())
        if until is not None:
            self.met_a_limit(until, str(failed)[:300], generation, failed if isinstance(failed, BaseException) else None,
                             request=request)

    def met_a_limit(self, until: datetime, reason: str, generation: int | None = None, cause: BaseException | None = None,
                    *, request: Send | str | None = None) -> None:
        """End each tick a provider's limit stopped, and let nothing more of it go out: on every meter the send took a place on
        (``request``'s record), or around the request where it has none, whose tick is the one the request began in
        (``generation``). The first limit is kept until the tick closes, so another of its requests, on another thread, meets it
        too, and every limit met is listed, in the order met (``met``), which the tick writes to its records before its charges
        settle. Outside a tick there is none to end, and one whose tick has closed latches nothing on the next."""
        latched = None
        with self._lock:
            for meter, began in self._targets(request, generation):
                if meter.limit == math.inf or (began is not None and began != meter.generation):
                    continue
                meter.met = (*meter.met, (until, reason))
                if meter.limited is None:
                    meter.limited = (until, reason)
                latched = latched or meter.limited
        if latched is not None:
            raise _AtLimit(*latched) from cause

    def provider_said(self, failed: Any, request: Send | str | None, generation: int | None = None) -> None:
        """What a provider said stopped a send, read only from G6's own code at the start of ``failed``: a refusal
        (``at_a_refusal``), or a limit or its model at capacity (``at_a_limit``). ``failed``: what the request raised, or what a
        watched host caller read before it raised anything. ``request``: the record of the send it was of, or its identity.
        Anything else is no statement of the provider's, and the request's own failure stands."""
        self.at_a_refusal(failed, request, generation)
        self.at_a_limit(failed, generation, request)

    def at_a_refusal(self, failed: Any, request: Send | str | None, generation: int | None = None) -> None:
        """Keep what a classifier or a permission refused, whether or not a tick is open, on every meter the send took a place
        on (or around the request, where it has no record), and end each tick it stopped. ``failed``: what the request raised,
        or what a watched caller read before it raised anything. ``request``: the record of the refused send, or its identity,
        never sent again; with neither known, the tick ends and nothing can be barred. ``generation``: the tick the request
        began in; a refusal that comes once that tick has closed ends nothing of the next, though what it refused is still
        never sent again. Under the lock, in order: it is kept (``keeping``), and then, whatever keeping it did, it is in
        ``refused`` and ends its tick, so no other thread sees it before it is on the disk, and no request goes out between;
        then it leaves for the tick, so nothing is scored and no record of the tick is written before it is kept."""
        said = refused_by(str(failed))
        if not said:
            return
        identity = request.identity if isinstance(request, Send) else request
        ticking = False
        with self._lock:
            targets = self._targets(request, generation)
            try:
                for meter, _ in targets:
                    if identity is not None and identity not in meter.refused:
                        meter.keeping(identity, said)
            finally:
                for meter, began in targets:
                    if identity is not None:
                        meter.refused.add(identity)
                    if meter.limit != math.inf and (began is None or began == meter.generation):
                        meter.refusal = {"said": _writable(said), "request": identity}
                    ticking = ticking or meter.limit != math.inf
                self._settled.notify_all()
        if ticking:
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


class _Gate:
    """One call's gate in front of a watched host caller (``usage.telling_stops``): its program starts only past ``admit``,
    which takes a place on every meter around the send and makes its record from what it is started with as it leaves; what
    the provider said of that send goes to every meter its record took a place on; and the send is released by its own
    outcome. Whichever call's gate is the innermost, the admission is the same one."""

    def __init__(self, requests: _Requests, call: _Call) -> None:
        self.requests, self.call = requests, call

    def sending(self, identify: Callable[[], Request], sender: Any) -> Send:
        return self.requests.admit(places=1, owner=self.call.owner, identify=identify, watched=True, sender=sender)

    def told(self, said: str, record: Send | str) -> None:
        self.requests.provider_said(said, record, self.call.generation)

    def done(self, record: Send) -> None:
        self.requests.release(record)


def _raised_by(failed: BaseException, sends: Sequence[Send]) -> Send | None:
    """Compatibility fallback for multi-send callers outside G6's wrappers. This cannot bind an error after its sender
    changes traceback locals. Removing it without losing existing external callers needs the bound note API to carry
    requests and errors. Owned host errors and single-send metered errors carry their record instead.

    The send whose failure ``failed`` is, read from its own frames, innermost first, on its way out or on the way out of
    what it was raised from: a frame of a watched caller this call let a send out for, or a frame, a function's or a method's,
    that names what an unwatched send let out in this call sent, as ``_sender`` reads it (``_named``). A send that came back
    has no frame there. With none found, it is this call's one send, or not known."""
    seen: set[int] = set()
    error: BaseException | None = failed
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        frames, tb = [], error.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame)
            tb = tb.tb_next
        for frame in reversed(frames):
            caller, named = frame.f_locals.get("self"), _named(frame)
            for record in reversed(sends):
                if (caller is not None and record.sender is caller) if record.watched else (
                        isinstance(named, Request) and named.identity == record.identity):
                    return record
        error = error.__cause__ or error.__context__
    return sends[0] if len(sends) == 1 else None


class _Counted:
    """A model caller the host meters. Every call of a tick passes through one, and what it counts is what reached a
    backend, not how often it was called: ``g6.core.model_calls`` notes each request as it goes out, so a retry inside a
    caller of a domain's own is charged attempt by attempt, and a ``Retry-After`` resend takes its place from
    ``g6.core.llm.waits``. Nothing a run reports about itself enters the count.

    Each send is let in once, before it goes out, by the innermost metered call open where it is made, which admits it for
    every meter around it (``_Requests.admit``): as it is noted, or for a watched host caller where its program starts, and a
    resend where it is resent. The calls around it see the same note and the same resend, and let the innermost decide, so a
    nested call charges nothing twice and passes no meter around it. ``counting`` asks its observers not to raise, because an
    observer is normally a bystander at someone else's request. This one is the gate on that request: it raises before
    anything is sent or counted, and what it raises is the ``LLMError`` a failed request raises, which every caller here
    passes on. That file is bound, so its sentence stays until an issuance window. What a call's children imported, which
    passes no boundary, is settled once as the call ends (``_Requests.settle``).

    Each send's record (``usage.Send``) is all that is known of it: no flag or variable here holds a send's state. What was
    refused is known by what was sent: for a host caller, an immutable snapshot handed to its launcher (``_Gate``);
    for any other, the system text and request the innermost frame below its sender, or the sender's own, names, a function's
    or a method's, read as it is noted (``_sender``), and a request no such frame names is not sent. What a call raised is
    carried by ``ProviderStop`` with its send's record for owned host errors and single-send metered errors. Only external
    multi-send errors use the traceback fallback (``_raised_by``), which cannot bind a mutated traceback: the bound note API
    must hand over the request and error to close that gap. A watched host
    caller's own reading of its structured output is all that decides for it, and the text reader is for any other. Whichever
    way the call comes back, what its tick latched decides last (``recheck``), so a wrapper that catches the limit or the
    refusal cannot turn it into an answer.

    A caller that answers with no request noted is refused, because nothing could say how often it asked. The limit of
    this: a caller that notes one request and retries unnoted looks like one request, and so do the Anthropic SDK's own
    retries. Only a usage record from the provider can show those, which is the relay of design section 3. A wrapper that
    sends from a thread of its own is seen only when it answers, as a caller that noted nothing, because ``counting`` holds its
    observers in a context that thread does not have: the tick adopts nothing and the loop pauses, but what it sent has gone.
    Closing that takes a change to that bound file, which waits for an issuance window. One that copied the context has those
    observers, and a request it sends once the call has returned is refused: the call's place is gone, and its tick may be."""

    def __init__(self, inner: Any, requests: _Requests) -> None:
        self.inner, self.requests = inner, requests

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        system, user = plain_text(system), plain_text(user)
        ended = threading.Event()
        call = _Call(object(), self.requests, self.requests.generation, lambda: not ended.is_set())
        self.requests.begin(call)                 # the call begins: no send of its own here
        around = (*_ACTIVE.get(), call)

        def innermost() -> tuple[_Call, ...] | None:
            """The metered calls open here, when this one is the innermost of them, which lets each send in for them all."""
            calls = _ACTIVE.get()
            return calls if calls and calls[-1] is call else None

        def next_request() -> None:
            calls = innermost()
            if calls is None:
                return                            # a call nested in this one lets this send in, for this meter too
            sender, identity = _sender(self)
            self.requests.from_sender(sender)
            read_stops(sender)
            if not watched(sender):               # a watched host caller is let in where its program starts
                if not identity:
                    self.requests.unnamed()       # nothing names what it sends, so it does not go
                self.requests.take(identity=identity, owner=call.owner, sender=sender)
            self.requests.noted(calls)

        def resend(wait: float = 0.0) -> None:
            if innermost() is not None:           # a resend is a send: what it sends again is checked again
                sender, identity = _sender(self)
                if not identity:
                    self.requests.unnamed()
                self.requests.take(wait, identity=identity, owner=call.owner, sender=sender)

        def limited(wait: dict[str, Any]) -> None:
            if innermost() is not None:
                hint = float(wait.get("retry_after_s") or 0.0)
                self.requests.met_a_limit(self.requests.now() + timedelta(seconds=max(hint, 0.0)),
                                          "HTTP 429: the provider's limit was met")

        def finish(latched: bool) -> None:
            ended.set()
            _ACTIVE.reset(active)
            _AROUND.reset(lineage)
            self.requests.settle(around, noted)   # what its children imported, once, into the record
            self.requests.release(call.owner)
            if latched:                           # what the tick latched decides, unless the process is going down
                self.requests.recheck(call.generation)

        active, lineage = _ACTIVE.set(around), _AROUND.set(_AROUND.get())
        with (counting(before_call=next_request) as noted, _gating_resends(resend, limited),
              telling_stops(_Gate(self.requests, call))):
            try:
                text = self.inner.complete(system, user, max_tokens=max_tokens)
            except LLMError as failed:
                try:
                    if isinstance(failed, ProviderStop):  # told already, unless the reply was read where the gate is not
                        self.requests.provider_said(failed.said, failed.record, call.generation)
                    else:                                 # read as the send that raised it is: text only if not watched
                        sends = self.requests.sends_within(call.owner)
                        # A wrapper with one send owns that association; a nested wrapper carries it out explicitly.
                        # Multi-send callers outside these wrappers still need the bound note API to hand over errors.
                        raised = sends[0] if len(sends) == 1 else _raised_by(failed, sends)
                        if (raised is None and not any(s.watched for s in sends)) or (raised is not None
                                                                                       and not raised.watched):
                            self.requests.provider_said(failed, raised, call.generation)
                except BaseException as reading:          # a death while it is kept stays a death, never the refusal
                    finish(isinstance(reading, Exception | _AtLimit | _Refused | _Halted))
                    raise
                finish(True)
                if not isinstance(failed, ProviderStop) and len(sends) == 1 and raised is not None and not raised.watched:
                    if refused_by(str(failed)) or limit_ends(str(failed), self.requests.now()) is not None:
                        raise ProviderStop(str(failed), raised) from failed
                raise
            except (Exception, _AtLimit, _Refused, _Halted):
                finish(True)
                raise
            except BaseException:                         # a death stays a death, and is never read as a refusal
                finish(False)
                raise
            finish(True)
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


def _groups(cases: Sequence[SuiteCase]) -> list[str]:
    """The groups these cases name: a case's group is exposed when any of its members is, whatever its id or text."""
    return sorted({f"group:{c.meta['group']}" for c in cases if c.meta.get("group")})


def _overlap(a: str, b: str) -> bool:
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _in_reach(root: Path, domain: Domain) -> list[str]:
    """What judges that lies in or around the loop's folder, which is the only place it writes."""
    judge, sealed = domain.judge(), domain.sealed()
    source = judge_source(judge.verdict) if judge is not None else None
    guarded = {"G6's own source": str(_SOURCE),
               **({"the judge's folder": str(source.resolve().parent)} if source is not None else {}),
               **{f"sealed root {i}": r for i, r in enumerate(sealed.vault.roots if sealed is not None else ())}}
    where = os.path.normcase(str(root))
    return [name for name, path in guarded.items() if _overlap(where, os.path.normcase(path))]


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
    suffixes = sorted(importlib.machinery.all_suffixes(), key=len, reverse=True)
    found = {}
    for path in root.rglob("*"):
        if not in_cache(path.relative_to(root)) or not path.is_file():
            continue
        name = os.path.normcase(path.name)          # Windows imports ``planted.PY`` as it does ``planted.py``
        suffix = next((s for s in suffixes if name.endswith(os.path.normcase(s))), None)
        if suffix is not None and name[:-len(suffix)] and "." not in name[:-len(suffix)]:   # importlib checks no names
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


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


def _judge_digest(judge: Judge | None) -> str:
    source = judge_source(judge.verdict) if judge is not None else None
    return hashlib.sha256(source.read_bytes()).hexdigest() if source is not None else ""


def _exclusive(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """One operation at a time per folder, on what is written and not on what this handle last read: a restart can
    overlap a tick that is still running, and a person's process is not the loop's. The lock is the operating system's,
    so it dies with its process, and a refusal is a reply, never a raised error."""

    @wraps(method)
    def invoke(self: ShadowLoop, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            with Coordination(self.root / ".tick").mutex():
                self.log.reload()
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
        self.evaluating = False                   # this tick has begun its held-out evaluation
        self.waited = 0                           # ticks a budget wait turned away since the last cycle: a wait leaves no row
        self.tuned: list[dict[str, Any]] = []     # this tick's tuning cycles as they happen, for a row that a raise cut short
        # ``lost_cycles`` and ``cannot_record`` are the process's for the folder (``_Folder``), so this handle starts with
        # whatever another of the folder's handles met; what was refused here is read with the store at every admission
        self.requests = _Requests(lambda: self._now(), self._halted, self._barred, self._keep_refusal,
                                  lambda: refused_requests(self.root) | self._here.refused)
        self._proposer = _Counted(proposer, self.requests)
        self.root = Path(root).resolve()
        reach = _in_reach(self.root, domain)
        if reach:                                # before anything is written: this folder is all the loop ever writes
            raise ValueError(f"[{EVALUATOR_IN_REACH}] the loop's folder overlaps {', '.join(reach)}: it never writes "
                             "what judges it")
        self.log = CycleLog(self.root / CYCLE_LOG)
        self.unreadable = ""                      # why what was refused here could not be read, which refuses every request
        try:                                      # before the first request: a restart sends nothing that was refused
            self.requests.refused |= self._refusals_on_record()
        except LedgerError as exc:
            self.unreadable = f"what was refused here cannot be read, so nothing could say what must not be sent: {exc}"[:400]
        self.meter = BudgetMeter(settings.windows, store=OperatorStore(self.root / "operator.db"),
                                 read_account=read_account)
        for provider, until in self.log.limits().items():
            self.meter.limit_reached(provider, until)
        self.problems = domain_problems(domain)
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
        self.cut = cut_pool(self.domain.development(), self.settings.seed)
        self.workflow = _workflow(self.domain, self.cut, self.settings, self.requests)
        self.problems = _pool_problems(self.cut, self.workflow.policy) + self.workflow.problems()
        self.mh = None if self.problems else adapt_shadow(self.workflow, self.domain.execution().factory,
                                                          workspace=self.root / "workspace", clock=self._clock,
                                                          before_evaluation=self._before_evaluation)

    def _before_evaluation(self) -> None:
        """Read the account once more before a held-out evaluation: a limit reads as a failed case, and an evaluation of
        failed cases spends one of the split's five on a reason untrue of the candidate. So a run already under a limit ends
        here, as one met in a request does, with nothing spent, and is latched as one is, so it is on the record before the
        tick's charges settle. The same read can move the end of the week, and the tick's requests stop there, as at the end it
        reserved in. A limit that arrives during the evaluation is read from the provider's own output as the request fails
        (``g6.self_maintenance.usage``), and spends nothing either."""
        if self._read_now is not None:
            now = self._now()
            self._read_now(now)
            seen = self.meter.reading(self.provider, now)
            if seen.wait_until is not None:
                self.requests.met_a_limit(seen.wait_until, seen.reason)
                raise _AtLimit(seen.wait_until, seen.reason)     # with no tick open there is nothing to latch: still none
            self.requests.ends_by(seen.ends)
        self.evaluating = True

    def _keep_refusal(self, request: str, said: str) -> None:
        """Keep a refused request's digest before anything else happens, under the admission lock: this process holds it for
        the folder first, whichever handle met it (``_Folder``), and then the disk (``note_refusal``). One no file would take
        is held by this process alone, and a restart could send it again: so this process sends nothing more from the folder,
        on any of its handles, and asks for a stop a person must lift."""
        self._here.refused.add(request)
        try:
            if note_refusal(self.root, request, said):
                return
        except Exception:  # noqa: BLE001 - any failure to keep it is a refusal only this process holds
            pass
        why = _writable(f"a request was refused ({' '.join(said.split())[:160]}) and {REFUSED_FILE} would not keep it, so a "
                        "restart could send it again")[:400]
        self.cannot_record = why
        try:
            ask_to_stop(self.root, why)
        except OSError:
            pass                      # every handle of the folder in this process still sends nothing more

    @property
    def _here(self) -> _Folder:
        """What this process alone knows of this handle's folder, which every handle of the folder shares."""
        return _RECORD.folder(self.root)

    @property
    def cannot_record(self) -> str:
        """Why this process spends no more on this folder, or ``""``: a refusal no file would keep, or cycles its log would not
        take. It is the process's and not the handle's (``_Folder``), so every handle of the folder stops on it at every
        admission, one made after it too, and only the process's end lifts it."""
        return self._here.cannot_record

    @cannot_record.setter
    def cannot_record(self, why: str) -> None:
        if why:                       # set and never lifted: an empty value changes nothing
            self._here.cannot_record = why

    @property
    def lost_cycles(self) -> int:
        """Cycles in a row this folder's log would not take, counted for the process whichever handle lost them."""
        return self._here.lost_cycles

    @lost_cycles.setter
    def lost_cycles(self, count: int) -> None:
        self._here.lost_cycles = count

    def stopped_by(self) -> str:
        """Why this folder is stopped, which a tick and the runner read alike: if the runner read less, a tick refused for a
        stop would leave it going, and asking again."""
        return self.cannot_record or stop_standing(self.root, self.log.started())

    def _refusals_on_record(self) -> set[str]:
        """Every request refused here, from ``refused.log`` and from the cycles that recorded a refusal: what a cycle of an
        earlier version kept as ``asked`` still counts."""
        stops = [c["classifier_stop"] for c in self.log.cycles() if c.get("classifier_stop")]
        return refused_requests(self.root) | {kept[name] for kept in stops for name in ("request", "asked") if kept.get(name)}

    def _halted(self) -> _Halted | None:
        """What stops any request, in a tick or not: what was refused could not be read, this folder's stop, or G6's own."""
        if self.unreadable:
            try:
                self.requests.refused |= self._refusals_on_record()
                self.unreadable = ""
            except LedgerError:
                return _Halted(RECORDS_UNREADABLE, self.unreadable)
        why = self.stopped_by()
        if why:
            return _Halted(STOPPED, why)
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
        split, and the file that declares it all when a runner opened one. The judge's whole file is digested, since a
        helper it calls judges too. No profile is in it. ``profile_space`` is what a profile may express, which a new version
        of the domain changes and a change of method in the same file does not (``tick_rca``'s domain part)."""
        run, sealed, judge = self.domain.execution(), self.domain.sealed(), self.domain.judge()
        return {"envelope": envelope_sha256(), "domain_file": file_digest(self.domain_file),
                "profile_space": _profile_space(str(getattr(self.domain, "name", "")), run),
                "evaluator": digest({"judging": judging_sha256(), "judge": _judge_digest(judge),
                                     "independence": [judge.answers_by, judge.is_model] if judge is not None else [],
                                     "task": run.task, "checks": [c.code_sha() for c in (*run.checks,
                                                                                         *run.optional_checks.values())]}),
                "cases": cut_pool(self.domain.development(), self.settings.seed).digest(),
                "sealed": digest({"vault": sealed.vault.digest if sealed is not None else "",
                                  "refs": [dict(r) for r in sealed.refs] if sealed is not None else []})}

    def _changed(self) -> list[str]:
        """Which of the frozen things differ from what a person enrolled. The vault's digest is of the files as sealed,
        so the sealed bytes are compared as well."""
        recorded, now = self.log.enrolled()["frozen"], self._frozen()
        changed = {name for name, value in now.items() if value != recorded.get(name)}
        sealed = self.domain.sealed()
        if sealed is not None and sealed.vault.verify_unchanged():
            changed.add("sealed")
        return sorted(changed)

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
                            "A person inspects the folder. Nothing runs on records that do not check out."
                            if halted.code == RECORDS_UNREADABLE else "Whoever engaged the stop releases it.")
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
        changed = self._changed()
        if changed:
            return _blocked(FROZEN_CHANGED, f"changed since a person enrolled this domain: {', '.join(changed)}. Nothing "
                            "measured against it would mean what it did", "A person checks the change and enrols again.")
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
        problems = domain_problems(self.domain) + provider_problems(self._callers(), self.provider)
        if not problems:
            return None
        return _blocked(problems[0][0], "; ".join(why for _, why in problems),
                        "Give this domain what it lacks; the loop will not run without it.")

    def _must_stop(self) -> str:
        """Why the durable record of cycles says stop, whether or not a pause was written: the cycles themselves are the
        fact, and the pause is only its note."""
        cycles = self.log.since_resumed()
        for cycle in reversed(cycles):
            if cycle["outcome"] == "sealed_breach" or cycle.get("code") in (UNMETERED_CALLER, CLASSIFIER_STOP, *HOLDS):
                return cycle["reason"]
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
        confirmed = self.mh.confirm(who, initial=self.domain.execution().initial)
        if confirmed["completion_state"] == "blocked-escalated":
            return confirmed
        frozen = self._frozen()
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
        reply = self._refused() or self._run()
        self._beat(reply["completion_state"], reply["warning_card"]["code"], reason=reply["message"])
        return reply

    @_exclusive
    def fault(self, exc: BaseException) -> dict[str, Any]:
        """A tick that raised past its own guard is a failed cycle like any other, so three in a row still pause."""
        if self.log.paused():
            return _blocked(PAUSED, f"the loop is paused: {self.log.paused()}", "A person reads why, then lifts it.")
        return self._record({"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                             "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]})

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
        """This tick's worst case on the provider its calls go to, or the wait: room on another pays for none of them. The
        limits on the record are read first, whichever handle or process met them, so a limit is never one handle's alone."""
        for provider, until in self.log.limits().items():
            self.meter.limit_reached(provider, until)
        spend = self.meter.reserve(self.provider, self._now(), self.worst_case())
        if spend.admitted:
            return spend, None
        return None, _blocked(BUDGET_WAIT, f"{spend.provider}: {spend.reason}",
                              "The loop waits; it runs again when a ceiling or a reset allows.",
                              wait_until=spend.wait_until.isoformat() if spend.wait_until else "")

    def _run(self) -> dict[str, Any]:
        self.requests.refused |= self._refusals_on_record()     # and any other process has kept since
        spend, waiting = self._reserve()
        if waiting is not None:
            self.waited += 1
            return waiting
        self._beat("running", CYCLE)
        opened = reflex.read(self.log.cycles())     # what limits progress, read before this cycle does anything
        recommended: list[dict[str, Any]] = []
        halted: _Halted | None = None
        self.evaluating = False
        self.tuned = []
        self._rca: TickRecord | str | None = None     # what this tick's analysis reads once it has tuned, or why it could not
        self.requests.open(spend.amount, spend.ends)
        with sealed_closed(self.domain.sealed().vault) as breaches:
            try:                      # a sealed file already held open: no cycle starts over it
                row = self._row() if breaches else self._cycle(recommended)
            except (_AtLimit, _Refused):  # what the tick latched decides how it ends, read as it closes
                row = self._row()
            except _Halted as reached:
                row, halted = self._row(), reached
            except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
                row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                       "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]}
            finally:             # whatever ended it, the tick closes in one step with what it latched, which decides from here
                self.requests.close()
                ended = self.requests.closed
                for until, _ in ended.met:          # every limit it met, in the order met, kept before its charges settle
                    self._keep_limit(spend.provider, until)
                # use nobody could count stays charged at its worst case
                charged = self.meter.settle(spend, spend.amount if ended.unseen else ended.sent)
        unseen, stopped, refusal = ended.unseen, ended.stopped, ended.refusal
        limit = _AtLimit(*ended.limited) if ended.limited is not None else None
        if self.tuned and "tuning" not in row:     # a tick cut short keeps what it had made
            row["tuning"] = reflex.funnel(self.tuned)
        if breaches:                  # read only now: a strategy that swallowed the refusal adopts nothing either
            row |= {"outcome": "sealed_breach", "code": SEALED_BREACH,
                    "reason": f"{len(breaches)} sign(s) that the sealed split was within reach while developing. Nothing "
                              "is adopted. A read through a handle already open leaves no trace, so a person decides "
                              "whether it still counts as sealed"}
        elif unseen:
            row |= {"outcome": "error", "code": UNMETERED_CALLER,
                    "reason": "a model caller answered with no request noted, so the loop cannot count what it sends: "
                              "nothing is adopted, and a person gives it a caller that notes each request"}
        elif refusal is not None:     # a cycle, so it is on the record, and a pause, so nothing rephrases it on the next tick
            row |= {"outcome": "blocked", "code": CLASSIFIER_STOP, "classifier_stop": refusal,
                    "reason": f"a classifier or a permission stopped a request ({refusal['said']}): nothing is adopted, it is "
                              "not sent again, and a person reads this before the loop goes on"}
        elif limit is not None:       # a wait and no cycle, like any other: what ran is charged, and none of it counts
            self.waited += 1
            return _blocked(BUDGET_WAIT, f"{spend.provider} met a limit after {charged:g} requests, so this tick scored nothing "
                            f"and waits until {limit.until.isoformat()}: {limit.said}",
                            "The loop waits; it runs again when the limit ends.", wait_until=limit.until.isoformat(),
                            charged=charged)
        elif halted is not None and halted.code in HOLDS:    # a fault that is on the record, and holds the loop for a person
            row |= {"outcome": "error", "code": halted.code, "reason": f"{halted.said}: nothing was sent, and nothing is adopted"}
        elif halted is not None:      # also no cycle: what ran is charged, and none of it counts
            return self._cut_off(halted, charged)
        elif stopped:                 # runs cut off mid-tick compared nothing: no gate result stands on them
            row |= {"outcome": "blocked", "code": TICK_BUDGET_SPENT, "reason": f"{stopped}, so nothing is adopted"}
        elif recommended:
            row |= self._adopt(recommended[-1]["decision"], row)
        if isinstance(self._rca, str):           # the read for it failed: the row says so
            row["rca"] = _writable({"unreadable": self._rca})
        elif self._rca is not None:               # read once the outcome is known: an adoption is part of what it reads
            row |= self._analysis(dataclasses.replace(self._rca, outcome=str(row.get("outcome", ""))))
        seen = self.meter.reading(spend.provider, self._now())
        row["budget"] = {"provider": seen.provider, "day": seen.day, "charged": charged, "used": seen.used,
                         "target": seen.target, "ceiling": seen.ceiling, "estimated": seen.estimated, "waited": self.waited}
        row["reflex"] = opened
        return self._record(row)

    def _keep_limit(self, provider: str, until: datetime) -> None:
        """A limit or capacity a provider reported, met in the tick that just closed: this process holds it, and the log keeps
        it for a restart, under that tick, so each of the tick's limits stands until the furthest of them ends. A log that
        would not take it leaves this process waiting, and after a restart the first request meets the limit again, which
        reads as one again and scores nothing."""
        self.meter.limit_reached(provider, until)
        try:
            self.log.limit(provider, until.isoformat(), self.requests.closed.tick)
        except (OSError, LedgerError):
            pass

    def _cut_off(self, halted: _Halted, charged: float) -> dict[str, Any]:
        """A stop met at a model call: say what it cut off, and note this folder's stop in the records, which this tick
        holds, so no one else can. The file is what stops the loop, so a note that cannot be written changes nothing."""
        dropped = (", and the held-out evaluation under way was dropped, so none of the split's five was spent"
                   if self.evaluating else "")
        if halted.code == STOPPED:
            try:
                self.log.stop(halted.said, at=self._now().isoformat(),
                              cut_off={"charged": charged, "evaluation": self.evaluating})
            except (OSError, LedgerError):
                pass
        return _blocked(halted.code, f"a stop ended this tick at a model call after {charged:g} requests{dropped}: {halted.said}",
                        "A new g6_improve_start lets it run again." if halted.code == STOPPED else
                        "Whoever engaged the stop releases it.", charged=charged)

    def _record(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = self._reply(self.log.cycle(row))
        except (OSError, LedgerError) as exc:
            return self.lost_cycle(exc, refusal=row.get("classifier_stop"))
        self.lost_cycles = self.waited = 0
        self._pause_if_failing()
        return reply

    def lost_cycle(self, exc: BaseException, *, refusal: dict[str, str] | None = None) -> dict[str, Any]:
        """A cycle the log would not take. The streak that pauses the loop is read from the log, so these leave it nothing
        to read and are counted in this process, for the folder, whichever of its handles lost them, for as long as the
        process lives. At ``MAX_FAILED_CYCLES`` in a row the loop stops
        for good: ``STOP`` and ``stops.log`` are other files, so a restart stays closed until a person starts it again, and
        should those fail too, this process still spends no more. A cycle that is written starts the count again. A cycle
        that holds a ``refusal`` stops the loop at the first, since the pause a person must read would have stood on it."""
        with _LOCK:                   # the process's count for the folder, whichever of its handles lost the cycle
            self.lost_cycles += 1
            lost = self.lost_cycles
        why = (f"{lost} of the {MAX_FAILED_CYCLES} cycles in a row that stop the loop could not be written to "
               f"its log ({type(exc).__name__}: {exc})")[:400]
        if refusal is not None:
            why = _writable(f"a classifier or a permission stopped a request ({refusal['said']}) and the log would not take "
                            f"that cycle ({type(exc).__name__}: {exc})")[:400]
        if refusal is not None or lost >= MAX_FAILED_CYCLES:
            with _LOCK:               # what every admission of the folder reads, under the same lock
                self.cannot_record = why
            try:
                ask_to_stop(self.root, why)
            except OSError:
                pass                  # the run still ends on what is held here
        return _blocked(CANNOT_RECORD, why, "A person frees the log, then starts the loop again with g6_improve_start.")

    def _adopt(self, decision: Any, row: dict[str, Any]) -> dict[str, Any]:
        def still_frozen() -> dict[str, Any] | None:      # the tick's first check came before all it measured
            changed = self._changed()
            return _blocked(FROZEN_CHANGED, f"changed during this cycle: {', '.join(changed)}. What it measured is not what "
                            "a person enrolled, so nothing is adopted") if changed else None

        adopted = self.mh.adopt(decision, findings={name: row[name] for name in FINDINGS if name in row},
                                last_check=still_frozen)
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
