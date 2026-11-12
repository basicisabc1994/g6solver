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
import importlib.util
import inspect
import math
import os
import stat
import site
import sys
import threading
import time
import uuid
import weakref
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial, wraps
from pathlib import Path
from types import CodeType
from typing import Any
from uuid import uuid4

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import LedgerError, canonical, digest
from g6.core.llm import _WAITS, DEFAULT_MAX_TOKENS, LLMError
from g6.core.model_calls import counting
from g6.core.reading import Unreadable, asked, exact, files_under, linked, said, same, sha256_of, walk
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
from g6.metaharness.shadow import ADOPTED, ADOPTER, FROZEN_CHANGED, ROLLED_BACK, SCOPE, Held, adapt_shadow
from g6.operator.store import OperatorStore, utc_clock
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_maintenance.envelope import (
    CYCLE_LOG,
    ENDS_A_STREAK,
    ENROLMENT_CHANGED,
    FINDINGS,
    MAX_FAILED_CYCLES,
    REFUSED_FILE,
    STOP_UNREADABLE,
    STOPPED,
    TICK_BUDGET_SPENT,
    CycleLog,
    _REFUSAL_WRITE_LOCK as _LOCK,
    Stop,
    admission,
    ask_to_stop,
    beat,
    end_start,
    note_refusal,
    refusal_rows,
    stop_in_force,
    writable,
)
from g6.self_maintenance.pacing import AccountReading, Admission, BudgetMeter, ProviderWindow, utc
from g6.self_maintenance.usage import (
    HOST_UNKNOWN,
    ProviderStop,
    Request,
    Send,
    _complete_host,
    _file_identity,
    _final_path,
    _host_kind,
    _native_host_note,
    _open_input,
    launch_record,
    limit_ends,
    read_stops,
    plain_text,
    refused_by,
    request_digest,
    telling_stops,
)
from g6.workspace._coordination import Coordination
from g6.workspace._envelope import BUSY as HELD_BY_ANOTHER
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
UNNAMED_REQUEST = "G6_E_LOOP_UNNAMED_REQUEST"
#: What holds the loop for a person when a request of a tick meets it: a sender the loop may not send through, or a request
#: whose sender names nothing it could be known by once refused.
HOLDS = (*BARRED, UNNAMED_REQUEST)
#: Judging code in a file no listing pins (``_unpinned``), and an answer no exact encoding identifies (``_answer_problems``):
#: what cannot be identified exactly is refused at enrolment, never guessed.
JUDGE_UNPINNED = "G6_E_LOOP_JUDGE_UNPINNED"
ANSWER_UNIDENTIFIABLE = "G6_E_LOOP_ANSWER_UNIDENTIFIABLE"
#: A cycle's safety stops: each holds the loop for a person (``_must_stop``), whatever code its row was given.
SAFETY_STOPS = (SEALED_BREACH, UNMETERED_CALLER, CLASSIFIER_STOP, HOST_UNKNOWN, *HOLDS)
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


class _Stopped(tuple):
    """This folder's stop as a tick reads it: the code and the reason, both empty with none, and the reading they are
    from (``stop_in_force``), which every ending on a stop decides on. True when a stop is in force."""

    def __new__(cls, reading: Stop) -> _Stopped:
        found = super().__new__(cls, (reading.code, reading.reason))
        found.reading = reading
        return found

    code = property(lambda self: self[0])
    reason = property(lambda self: self[1])

    def __bool__(self) -> bool:
        return bool(self[0])

    def __getattr__(self, name: str) -> Any:
        if name == "reading":                # not set yet: nothing to ask
            raise AttributeError(name)
        return getattr(self.reading, name)   # the rest of the reading, as the reading names it


class _Halted(BaseException):
    """A stop, met at a model call, on its way out of the tick as ``_AtLimit`` is, and for the same reason: nothing from the
    run it cut off is written, so an evaluation it cut off is not one of the split's five. ``reading``: this folder's
    reading that halted it (``stop_in_force``), or ``None`` for a stop of another kind."""

    def __init__(self, code: str, said: str, reading: Stop | None = None) -> None:
        super().__init__(said)
        self.code, self.said, self.reading = code, said, reading


class _Refused(BaseException):
    """A classifier or a permission stopped a request, or a reply's outcome is unknown and its inputs are held, on its way
    out of the tick as ``_AtLimit`` is. Which of the two is in what the tick latched (``refusal``, ``hold``), never in this.
    Nothing it cut off is written, so an evaluation it cut off is not one of the split's five."""


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
_SETTLED = threading.Condition(_LOCK)
# Persistence is ordered separately; no stop reader holds this lock.
_PUBLICATIONS = threading.RLock()


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
    holds what this process alone knows of each loop's folder (``_Folder``), which admission reads through each handle of the
    folder before it takes that lock, and reads again if a refusal was kept or a folder stopped in between (``moved``)."""

    def __init__(self) -> None:
        self.out: dict[str, list[Send]] = {}         # each send let out whose outcome is not yet read, by what it sent
        self.taken: weakref.WeakKeyDictionary[Any, dict[int, int]] = weakref.WeakKeyDictionary()   # meter: tick: places
        self.books: dict[object, _Book] = {}         # each metered call open, by its owner
        self.writing: set[object] = set()           # refusal writes not yet persisted and latched; excludes every admission
        self.moved = 0                              # counts, under the lock, each refusal kept and each folder stopped here
        self.folders: dict[tuple[int, int], _Folder] = {}  # opened volume/device and file id/inode
        self.folder_handles = ExitStack()          # retain identities while their latches live, even after unlink
        self.folder_lock = threading.Lock()
        weakref.finalize(self, self.folder_handles.close)

    def charge(self, meter: Any, generation: int, places: int) -> None:
        held = self.taken.setdefault(meter, {})
        held[generation] = held.get(generation, 0) + places

    def folder(self, root: str | Path) -> _Folder:
        """One latch per opened folder. An unresolved final link is refused; spelling never keys state."""
        opened = _open_input(Path(root), follow_directory=True)
        try:
            handle = opened.__enter__()
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: loop folder cannot be opened safely") from None
        keep = False
        try:
            identity = _file_identity(handle)
            if not stat.S_ISDIR(os.fstat(handle.fileno()).st_mode):
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: loop folder is not a directory")
            with self.folder_lock:
                if identity not in self.folders:
                    self.folder_handles.callback(opened.__exit__, None, None, None)
                    keep = True
                    self.folders[identity] = _Folder()
                return self.folders[identity]
        finally:
            if not keep:
                opened.__exit__(None, None, None)


_RECORD = _Record()


def _meters(calls: Sequence[_Call]) -> tuple[tuple[Any, int | None], ...]:
    """Each meter around these calls once, outermost first, with the tick its outermost call began in."""
    found: list[tuple[Any, int | None]] = []
    for call in calls:
        if not any(meter is call.meter for meter, _ in found):
            found.append((call.meter, call.generation))
    return tuple(found)


def _unread(said: str) -> bool:
    """Whether what was said of a send is G6's own hold on a reply of unknown outcome, and no refusal a provider stated: the
    one rule for which of the two a record, a latch or an error is of."""
    return said.startswith(f"{HOST_UNKNOWN}:")


def _marked(request: str) -> str:
    """What a store's reader adds beside a request it holds only for an unknown outcome (``ShadowLoop._on_record``). It is
    no request's digest, so it bars nothing: admission reads from it which words a barred request is turned away with."""
    return f"{HOST_UNKNOWN}:{request}"


def _kinds(rows: Iterable[tuple[str, str]]) -> tuple[set[str], set[str]]:
    """The requests refused, and those held only because the outcome of a send is unknown, from each record's request and
    what was said of it. A refusal outranks: a request with one of each was refused."""
    kept = list(rows)
    refused = {request for request, said in kept if not _unread(said)}
    return refused, {request for request, _ in kept} - refused


def _stopped_by(said: str) -> str:
    """What a tick's record says ended it, from what was said of the send: a refusal, or a reply of unknown outcome."""
    if _unread(said):
        return f"the outcome of a send is unknown ({said})"
    return f"a classifier or a permission stopped a request ({said})"


def _not_sent(known: str, barred: set[str], meters: Sequence[Any]) -> LLMError:
    """Why a barred request does not go, as the failed request it leaves as: it was refused once, or the outcome of an
    earlier send of the same inputs is unknown, which is said as that and not as a refusal. Each begins with one of G6's
    codes, and neither as a provider's statement does (``refused_by`` reads the hold's code only where a colon follows
    it), so no reader takes the loop's own bar for a new one. Called under the lock."""
    if not any(known in meter.refused for meter in meters) and (
            any(known in meter.unknown for meter in meters) or _marked(known) in barred):
        return LLMError(f"{HOST_UNKNOWN} held: the outcome of an earlier send of these inputs is unknown, and nothing "
                        "sends them again")
    return LLMError(f"{CLASSIFIER_STOP}: this request was refused once, and nothing sends it again")


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
    hold: dict[str, Any] | None = None


class _Requests:
    """A meter: the limit a tick reserved, when the week it reserved in ends, and what the tick latched. Past either limit,
    nothing more goes out: a reset drops the ceiling to a new week's first day, and nothing is reserved under that one.
    ``stopped`` says which stopped it. ``barring`` is what a request's real sender is held to during a tick.

    A meter keeps no count: what its tick has taken is the one record's (``_Record``), which every send in the process goes
    into. Every send passes ``admit``, tick or no tick, once, at the place it leaves: as it is noted, for any caller but a
    watched host caller, and where a watched host caller's program is about to start, for that one; a ``Retry-After`` resend
    passes it too, and the start of a call passes it with no send of its own. It is let in once for every meter around it,
    which are the meters of the metered calls open where it is made, nested or not, on whatever thread: one step under the one
    lock checks the stops read outside it, latches and what each holds as refused, takes one place on each, and makes the send's
    record (``usage.Send``), which is all that is known of the send. What a provider said of it goes to each meter that record
    took a place on, and it is released by its own outcome (``release``). A call's children's totals, which pass no boundary,
    are settled once, into the record, as the call ends (``settle``).

    ``halted`` is read first, and read again after any wait, so a stop, or a refusal this process could not keep, refuses
    everything after it. After a refusal nothing more of its tick goes out, and what was refused is never sent again: what
    ``refused`` holds, every request refused through this meter, ``unknown`` holds requests whose replies could not be read
    (barred the same way, and latched and said as that: ``hold``, never ``refusal``),
    and what ``on_record`` reads at every admission, which for a
    loop's meter is the store and what this process holds for the folder (``_Folder``), so another handle of the same loop, in
    this process or another, refuses it too. ``keeping`` runs outside the admission lock; its synchronous durable refusal
    write takes that lock through fsync. Pending writes hold admission and tick finalization until persistence and the
    protected latch finish. A send waits while an identical one is out
    on another thread
    (``_out_elsewhere``), so one let in before a refusal of the same bytes was kept cannot leave after it. A tick ends in one
    step under the lock (``close``), which keeps what it latched as ``closed``: a limit or a refusal is in what the tick ends
    on, or was met once it had ended, and is never lost between."""

    #: What a tick latches on its meter: per tick, never per request, and no count.
    TICK_STATE = ("limit", "ends", "stopped", "unseen", "refusal", "hold", "limited", "met", "generation", "closed")

    def __init__(self, now: Callable[[], datetime] = lambda: utc(datetime.now(UTC)),
                 halted: Callable[[], _Halted | None] = lambda: None,
                 barring: Callable[[Any], _Halted | None] = lambda sender: None,
                 keeping: Callable[[str, str], None] = lambda request, said: None,
                 on_record: Callable[[], set[str]] = frozenset) -> None:
        self.now, self.halted, self.barring, self.keeping, self.on_record = utc_clock(now), halted, barring, keeping, on_record
        self.refused: set[str] = set()
        self.unknown: set[str] = set()
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
        """A tick's limits, from here on. The week's end is held as a UTC instant."""
        ends = utc(ends)
        with self._lock:
            self._await_writes()
            self._reset(limit, ends)

    def _await_writes(self) -> None:
        """Wait under the condition, releasing its lock, until all refusal writes have finished, across handles too."""
        while _RECORD.writing:
            self._settled.wait()

    def _reset(self, limit: float, ends: datetime | None) -> None:
        """A tick's limits, and nothing taken under it yet. ``generation`` moves with each opening and closing, so a request
        that took its look at one tick cannot be admitted under the next, or under none. Called under the lock."""
        self.limit, self.ends, self.stopped, self.unseen, self.refusal = limit, ends, "", False, None
        self.hold: dict[str, Any] | None = None     # a reply of unknown outcome met in this tick: no classifier's refusal
        self.limited: tuple[datetime, str] | None = None
        self.met: tuple[tuple[datetime, str], ...] = ()
        self.generation += 1
        _RECORD.taken[self] = {}

    def close(self) -> None:
        """End the tick in one step under the lock: what it latched is kept as ``closed``, and nothing is admitted under it
        after. So a limit or a refusal is in what the tick ends on, or was met once it had ended."""
        with self._lock:
            self._await_writes()
            self.closed = _Ended(self.sent, self.unseen, self.stopped, self.refusal, self.limited, self.met, uuid4().hex,
                                 self.hold)
            self._reset(math.inf, None)

    def latched(self) -> _Ended:
        """What the tick has latched, read in one step under the lock without ending it: what a tick whose ``close`` raised
        before it ended leaves by (``ShadowLoop._run``), so a refusal or a hold it met is kept on its way out all the
        same. It names no closing (``tick``) and decides nothing of how the tick ends."""
        with self._lock:
            return _Ended(self.sent, self.unseen, self.stopped, self.refusal, self.limited, self.met, "", self.hold)

    def ends_by(self, moment: datetime) -> None:
        """A provider has said the tick's week ends at ``moment``, which can come to light only once it is under way. What it
        reserved covers nothing after that. A later end buys it nothing, and outside a tick there is no week to end."""
        moment = utc(moment)
        with self._lock:
            if self.ends is not None and moment < utc(self.ends):
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

    def _full(self, places: int, wait: float, moment: datetime | None) -> bool:
        """Whether this tick has no room for ``places`` more, or its week ends before a send that waits ``wait`` seconds could
        leave; ``stopped`` then says which. Called under the lock."""
        if self.unseen or self.sent + places > self.limit or (places == 0 and self.sent >= self.limit):
            self.stopped = f"this tick has sent {self.sent} of the {self.limit:g} it reserved"
            return True
        if self.ends is not None and utc(moment) + timedelta(seconds=wait) >= utc(self.ends):
            self.stopped = f"this tick's week ended at {self.ends.isoformat()} and nothing is reserved in the new one"
            return True
        return False

    def admit(self, wait: float = 0.0, *, places: int = 0, generation: int | None = None,
              live: Callable[[], bool] = lambda: True, identity: str | Request | None = None, owner: object = None,
              identify: Callable[[], str | Request] | None = None, watched: bool = False, sender: Any = None,
              record: Send | None = None) -> Send | None:
        """Read callbacks without the admission lock, then decide and register under it. A refusal published during any
        reader is checked before charging or sending. Pending persistence excludes admission, and every condition wait
        restarts all external reads, as does a refusal kept or a folder stopped by this process since they began (``moved``).
        ``identify`` supplies the immutable snapshot this send carries; no callback can block a refusal from being
        published while it reads. Tick generations and latches are checked in the protected step."""
        here = threading.get_ident()
        with self._lock:
            calls = _ACTIVE.get()
            if not any(call.meter is self for call in calls):
                calls = (*calls, _Call(owner, self, generation, live))
            meters = [meter for meter, _ in _meters(calls)]
            books = tuple(_RECORD.books.get(call.owner) for call in calls)
            seen = _RECORD.moved
        while True:
            external: set[str] = set()
            stops: tuple[tuple[Any, _Halted | None], ...] = ()
            for meter in meters:
                stopped = meter.halted()
                if stopped is None and (identity is not None or identify is not None):
                    try:
                        external |= meter.refused | meter.unknown | set(meter.on_record())
                    except LedgerError as exc:
                        stopped = _Halted(RECORDS_UNREADABLE, f"what was refused here cannot be read, so nothing could "
                                                              f"say what must not be sent: {exc}"[:400])
                stops += ((meter, stopped),)
            # Snapshot after every stop/store read, including after a wait. No later read names this send.
            request = identify() if identify is not None and not any(stop for _, stop in stops) else identity
            known = request.identity if isinstance(request, Request) else request
            if identify is not None and not any(stop for _, stop in stops):
                stops += tuple((meter, meter.halted()) for meter in meters)
            living = all(call.live() for call in calls)
            moments = [meter.now() if meter.ends is not None else None for meter in meters]
            registering = None
            try:
                with self._lock:
                    if _RECORD.writing:
                        self._settled.wait()
                        seen = _RECORD.moved
                        continue
                    if _RECORD.moved != seen:  # a refusal was kept or a folder stopped here since the reads above began
                        seen = _RECORD.moved
                        continue
                    for meter, stopped in stops:
                        if stopped is not None:
                            if meter.limit != math.inf:
                                raise stopped
                            raise LLMError(f"{stopped.code}: {stopped.said}")
                    for meter in meters:
                        if meter.refusal is not None:
                            raise _Refused(meter.refusal["said"])
                        if meter.hold is not None:
                            raise _Refused(meter.hold["said"])
                        if meter.limited is not None:
                            raise _AtLimit(*meter.limited)
                    if (not living or any(call.generation is not None and call.generation != call.meter.generation
                                          for call in calls)
                            or any(book is not None and _RECORD.books.get(call.owner) is not book
                                   for call, book in zip(calls, books))):
                        raise LLMError(f"{TICK_BUDGET_SPENT}: this request came after its call returned, so nothing reserved "
                                       "covers it")
                    barred = external.copy()
                    for meter in meters:
                        barred |= meter.refused | meter.unknown
                    if record is not None:
                        if (request is not record.request or known != record.identity
                                or not any(held is record for held in _RECORD.out.get(known, ()))
                                or any(not any(charged is meter and generation == meter.generation
                                               for charged, generation in record.charged) for meter in meters)):
                            raise LLMError("G6_E_LOOP_UNNAMED_REQUEST: note does not name its admitted launch")
                        if known in barred:
                            raise _not_sent(known, barred, meters)
                        return record
                    if known is not None and self._out_elsewhere(known, owner, here):
                        self._settled.wait(1.0)
                        continue
                    if any(meter.ends is not None and moment is None for meter, moment in zip(meters, moments)):
                        continue
                    full = next((meter for meter, moment in zip(meters, moments)
                                 if meter._full(places, wait, moment)), None)
                    if full is not None:
                        break
                    for meter in meters:
                        _RECORD.charge(meter, meter.generation, places)
                    if known is not None and known in barred:
                        raise _not_sent(known, barred, meters)
                    if known is None:
                        return None
                    record = Send(known, watched, owner, self.generation, here, sender, places,
                                  tuple((meter, meter.generation) for meter in meters),
                                  request if isinstance(request, Request) else None)
                    registering = record
                    try:
                        _RECORD.out.setdefault(known, []).append(record)
                        for call in calls:
                            book = _RECORD.books.get(call.owner)
                            if book is not None:
                                book.sends.append(record)
                        _AROUND.set((*_AROUND.get(), record))
                        return record
                    except BaseException:
                        self._release_locked(record)
                        raise
            except BaseException:
                if registering is not None and registering.request is not None:
                    registering.request.close()
                raise
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
        total = counted()
        with self._lock:
            while _RECORD.writing:
                self._settled.wait()
            book = _RECORD.books.pop(calls[-1].owner, None)
            imported = max(total - book.notes - book.settled, 0) if book is not None else 0
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
            self._release_locked(done)

    def _release_locked(self, done: Send | object) -> None:
        """Release with the admission lock already held, including an interrupted record registration."""
        if not isinstance(done, Send):
            _RECORD.books.pop(done, None)
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
            self._await_writes()
            if self.limit == math.inf or generation != self.generation:
                return
            refusal, limited = self.refusal or self.hold, self.limited
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
        """Keep a provider refusal or an unread-reply hold, whether or not a tick is open, on every meter the send took a place
        on (or around the request, where it has no record), and end each tick it stopped. ``failed``: what the request raised,
        or what a watched caller read before it raised anything. ``request``: the record of the refused send, or its identity,
        never sent again; with neither known, the tick ends and nothing can be barred. ``generation``: the tick the request
        began in; a refusal that comes once that tick has closed ends nothing of the next, though what it refused is still
        never sent again. A pending write is registered under the admission lock, persistence hooks run outside it, and
        note_refusal takes it for the durable write before the outcome latches under it in a finally. Admission and tick
        finalization wait for pending writes. Publications are
        ordered by their own reentrant lock, which no admission reader holds, so a paused stop reader cannot exclude one.
        Every target meter's keeper is tried, whatever one of them raised, and the first of those leaves once they have
        been. A hold for an unknown outcome is latched as ``hold`` and a stated refusal as ``refusal`` (``_unread``)."""
        said = refused_by(str(failed))
        if not said:
            return
        identity = request.identity if isinstance(request, Send) else request
        writable = _writable(said)
        token = object()
        targets: tuple[tuple[Any, int | None], ...] = ()
        unknowns: tuple[set[str], ...] = ()

        def finish() -> bool:
            with self._lock:
                try:
                    if token not in _RECORD.writing:
                        return False
                    _RECORD.moved += 1
                    for (meter, began), unknown in zip(targets, unknowns):
                        if identity is not None:
                            if _unread(said):
                                unknown.add(identity)
                            else:
                                meter.refused.add(identity)
                        if meter.limit != math.inf and (began is None or began == meter.generation):
                            if _unread(said):
                                meter.hold = {"said": writable, "request": identity}
                            else:
                                meter.refusal = {"said": writable, "request": identity}
                    return any(meter.limit != math.inf for meter, _ in targets)
                finally:
                    _RECORD.writing.discard(token)
                    self._settled.notify_all()

        try:
            with self._lock:
                targets = self._targets(request, generation)
                unknowns = tuple(meter.unknown for meter, _ in targets)
                _RECORD.writing.add(token)
            with _PUBLICATIONS:
                failed: BaseException | None = None
                try:
                    for meter, _ in targets:
                        needed = identity is not None     # kept, unless this meter is seen to hold it already
                        try:          # every meter's keeper is tried: one that raises takes no other's record with it
                            try:
                                with self._lock:
                                    needed = needed and identity not in meter.refused | meter.unknown
                            finally:
                                if needed:
                                    meter.keeping(identity, said)
                        except BaseException as raised:
                            failed = failed or raised
                    if failed is not None:
                        raise failed
                finally:
                    ticking = finish()  # latch before the next publication can start
        finally:
            finish()                 # interruption while acquiring the publication lock also releases pending writes
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
        self.requests.from_sender(sender)          # caller policy runs before the snapshot, never after admission
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


@contextmanager
def _call_scope(call: _Call, ended: threading.Event) -> Iterator[tuple[_Call, ...]]:
    """Own a call's book, contexts and sends even if setup, settlement or a context exit is interrupted."""
    try:
        call.meter.begin(call)
        around = (*_ACTIVE.get(), call)
        active = _ACTIVE.set(around)
        try:
            lineage = _AROUND.set(_AROUND.get())
            try:
                yield around
            finally:
                _AROUND.reset(lineage)
        finally:
            _ACTIVE.reset(active)
    finally:
        ended.set()
        call.meter.release(call.owner)


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
        def innermost() -> tuple[_Call, ...] | None:
            """The metered calls open here, when this one is the innermost of them, which lets each send in for them all."""
            calls = _ACTIVE.get()
            return calls if calls and calls[-1] is call else None

        def next_request() -> None:
            calls = innermost()
            if calls is None:
                return                            # a call nested in this one lets this send in, for this meter too
            record = launch_record()
            if record is not None:
                self.requests.admit(identity=record.request, owner=call.owner, places=1, record=record)
                self.requests.noted(calls)
                return
            if _native_host_note(_Counted.complete.__code__):
                raise LLMError("G6_E_LOOP_UNWATCHED_HOST: native host note has no owned launch record")
            sender, identity = _sender(self)
            self.requests.from_sender(sender)
            if _host_kind(sender):
                raise LLMError("G6_E_LOOP_UNWATCHED_HOST: host note has no owned launch record")
            read_stops(sender)
            if not identity:
                self.requests.unnamed()           # nothing names what it sends, so it does not go
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
            try:
                ended.set()
                self.requests.settle(around, noted)   # what its children imported, once, into the record
            finally:
                self.requests.release(call.owner)
            if latched:                           # what the tick latched decides, unless the process is going down
                self.requests.recheck(call.generation)

        with (_call_scope(call, ended) as around, counting(before_call=next_request) as noted,
              _gating_resends(resend, limited),
              telling_stops(_Gate(self.requests, call))):
            try:
                text = (_complete_host(self.inner, system, user, max_tokens=max_tokens) if _host_kind(self.inner)
                        else self.inner.complete(system, user, max_tokens=max_tokens))
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


def _folder_place(path: str | Path) -> tuple[tuple[int, int], tuple[str, ...], tuple[tuple[int, int], ...]]:
    """An opened folder and its opened ancestors, or a future slot beneath that folder.

    Names describe only uncreated entries. Existing folders are compared solely by handle identity, even when their
    handles expose different drive/share spellings. Parent-name equality terminates a walk; it identifies no object.
    """
    where, missing = Path(path), []
    while True:
        try:
            with _open_input(where, follow_directory=True) as stream:
                identity, final = _file_identity(stream), Path(_final_path(stream))
                if not stat.S_ISDIR(os.fstat(stream.fileno()).st_mode):
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: folder locator names a non-directory")
            break
        except FileNotFoundError:
            missing.append(where.name)
            parent = where.parent
            if parent == where:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: folder has no opened ancestor") from None
            where = parent
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: folder ancestry cannot be opened safely") from None
    ancestors = [identity]
    while final.parent != final:
        final = final.parent
        try:
            with _open_input(final, follow_directory=True) as stream:
                ancestors.append(_file_identity(stream))
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: folder ancestry cannot be opened safely") from None
    # Future slots have no object yet. Preserve the platform's conservative name comparison for those labels only.
    return identity, tuple(os.path.normcase(part) for part in reversed(missing)), tuple(ancestors)


def _overlap(a: str, b: str) -> bool:
    left, left_missing, left_parents = _folder_place(a)
    right, right_missing, right_parents = _folder_place(b)
    if left == right:
        return (left_missing[:len(right_missing)] == right_missing
                or right_missing[:len(left_missing)] == left_missing)
    return (not left_missing and left in right_parents) or (not right_missing and right in left_parents)


def _in_reach(root: Path, domain: Domain) -> list[str]:
    """What judges that lies in or around the loop's folder, which is the only place it writes: by path, and by the file
    system's own identity of each folder on the way, so another spelling of a folder is that folder, and whatever a link
    under a judge's root points at (a link there is refused all the same, ``_unpinned``). A folder under a judge's root
    that cannot be listed is in reach too: what it holds is not known to hold no link to the loop's folder."""
    sealed, roots = domain.sealed(), _roots(domain)
    links, unlisted = _links_under(roots)
    guarded = {"G6's own source": str(_SOURCE),
               **{f"the judge's root {root}": str(root) for root in roots},
               **{f"a link under the judge's root, to {os.path.realpath(exact(link))}": os.path.realpath(exact(link))
                  for link in links},
               **{f"sealed root {i}": r for i, r in enumerate(sealed.vault.roots if sealed is not None else ())}}
    return [name for name, path in guarded.items() if _same_place(root, Path(path))] + [
        f"a folder under the judge's root that cannot be listed ({name}), which may hold a link to it" for name in unlisted]


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


def _is_link(path: Path, *, absent: bool = False, was: os.stat_result | None = None) -> bool:
    """Whether ``path`` is a link or a junction, by the one reader's stat that raises (``asked``): one that cannot be
    asked raises ``Unreadable``, never a "no", and so does one that is gone, unless ``absent`` allows it (a root, which is
    compared by its absence), when it is none. ``was``: what the lister's own stat found of this entry (``Listed``); an
    entry has one answer, so one that is another entry by this ask was changed while its folder was listed, and is
    ``Unreadable`` too."""
    info = asked(path, absent=absent)
    if was is not None and info is not None and not same(was, info):
        raise Unreadable(f"{said(path)} cannot be read (it was changed while its folder was listed)")
    return info is not None and linked(info)


def _links_under(roots: Sequence[Path], given: Sequence[Path] = ()) -> tuple[list[Path], list[str]]:
    """Every link or junction that is a root, as ``given`` before anything resolved it or as resolved, or lies under one,
    since what it points at is no part of the root's listing, and every root, folder or entry under one that could not
    be listed or asked, named. The walk goes on past what it cannot list or ask, so no link found elsewhere is lost to it,
    and the caller refuses either (``_in_reach``, ``_unpinned``)."""
    found: list[Path] = []
    unlisted: list[str] = []

    def link(path: Path, absent: bool = False, was: os.stat_result | None = None) -> bool | None:
        """``True`` for a link, ``False`` for none, ``None`` for what could not be asked, which is named."""
        try:
            return _is_link(path, absent=absent, was=was)
        except Exception:  # noqa: BLE001 - what cannot be asked is not known to be no link: named, and refused
            unlisted.append(said(path))
            return None

    found += [root for root in given if link(root, absent=True)]
    for root in roots:
        at = link(root, absent=True)
        if at:
            found.append(root)
        if at is not False:
            continue
        for here, dirs, files in walk(root, skipped=unlisted):
            kept = []
            for name in (*dirs, *files):
                held = link(Path(here) / name, was=getattr(name, "info", None))
                if held:
                    found.append(Path(here) / name)
                elif held is False and name in dirs:
                    kept.append(name)
            dirs[:] = kept
    return found, unlisted


#: What judges could not be read whole: a file under a judge's root, a loaded module's source outside them, the domain's
#: declaration, G6's own code, the sealed split, or a folder under any of them that could not be listed. It is the one
#: reader's (``g6.core.reading``), which the sealed split and the integrity manifest read through too, and no reading
#: stands on it: enrolment refuses it (``JUDGE_UNPINNED``), and every cycle and adoption refuses it as a frozen change
#: while it stands.
FrozenUnreadable = Unreadable


def _frozen_bytes(path: Any) -> str:
    """The SHA-256 of a file the freeze pins, by the one reader (``sha256_of``): ``FrozenUnreadable`` on any failure at
    all, never a value that stands in for bytes it could not read."""
    return sha256_of(str(path))


def _whole(read: Callable[[], Any]) -> Any:
    """The freeze's one boundary: ``read`` made whole, or ``FrozenUnreadable`` whatever kept it from being made, a domain's
    own code raising while it is read included, so no reading of what judges raises anything else past it."""
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - whatever keeps the reading from being made, it is unreadable
        if isinstance(exc, Unreadable):
            raise
        raise FrozenUnreadable(f"what judges could not be read whole ({type(exc).__name__}: {said(exc)})"[:400]) from exc


def _unpinned_reading(exc: BaseException) -> dict[str, Any]:
    """Enrolment refused on a reading of what judges that could not be made whole: nothing is enrolled."""
    return _blocked(JUDGE_UNPINNED, f"what judges could not be read whole ({said(exc)}), so no listing pins it and nothing "
                    "was enrolled"[:500], "A person makes it readable, then enrols.")


def envelope_sha256() -> str:
    """The enforcing code and where it is installed: no candidate changes the envelope (design 6.5), and a change a person
    made is theirs to accept by enrolling again. The place counts, so a run enrolled on a pinned install cannot be swapped
    for a working tree that holds the same bytes today. It is a tripwire, not custody: the code that compares this digest is
    among what it covers, so an edit to that code can skip the comparison. Holding it needs the read-only checkout of design
    6.5. G6's package is listed once, and both its files and its importable caches are chosen from that one listing."""
    listed = files_under(_SOURCE)
    with _open_input(_SOURCE, follow_directory=True) as stream:
        install = _file_identity(stream)
    return digest({"code": manifest_sha256({**build_manifest(_SOURCE, patterns=ENVELOPE_PATTERNS, listed=listed),
                                            **_importable_caches(_SOURCE, listed)}),
                   "install": install})


def _importable_caches(root: Path, listed: Sequence[Path] | None = None) -> dict[str, str]:
    """Each file under a ``__pycache__`` folder that Python could import as a module, as ``build_manifest`` gives files. A cache
    folder is a namespace package, so ``g6.x.__pycache__.name`` imports ``name.py``, ``name.pyc`` or an extension there. A cache
    a first import writes is named for its interpreter (``select.cpython-312.pyc``), which no import statement can name, so it is
    left out and a legitimate import changes nothing. Listed and read by the one reader, or chosen from ``listed``, its listing
    of ``root``: what cannot be listed or read raises ``FrozenUnreadable``."""
    return {path.relative_to(root).as_posix(): _frozen_bytes(path) for path in (files_under(root) if listed is None else listed)
            if in_cache(path.relative_to(root)) and _importable(path.name)}


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


#: ``value`` with every string, keys too, made writable as UTF-8: the envelope's one rule for text a row repeats
#: (``g6.self_maintenance.envelope.writable``), so a row that repeats a domain's labels or an error's words can always be
#: written.
_writable = writable


def _judge_digest(judge: Judge | None, roots: Sequence[Path] | None = None) -> str:
    """What decides a verdict, pinned by files listed whole and never by following imports: the verdict function the domain
    selected, by its file and its qualified name, so another function in the same file is another judge, and every file
    under the roots that hold the domain's judging code, each by its bytes, as the envelope pins G6 (every importable
    suffix, namespace packages and importable caches included), so a helper it calls, or imports only once it runs, judges
    too. ``roots``: the folders the enrolment pins (``_roots``), or the verdict's own folder when none are given. G6's own
    package is the envelope's, and the installation's code its own."""
    if judge is None:
        return ""
    return digest({"verdict": _callable_named(judge.verdict),
                   "roots": _pinned(roots if roots is not None else _roots_of([_code_file(judge.verdict)]))})


def _callable_named(fn: Any) -> list[str]:
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
    links, unlisted = _links_under(roots, given)
    if unlisted:
        problems.append((JUDGE_UNPINNED, f"what judges cannot be listed whole ({unlisted[0]} cannot be listed), so no "
                         "listing pins it"))
    return problems + ([(JUDGE_UNPINNED, f"a link or junction at or under the judge's roots, at {said(links[0])}, points at "
                         "what no listing of them pins: a root holds its files itself")] if links else [])


def _pinned(roots: Sequence[Path]) -> dict[str, str]:
    """Each root pinned whole: every regular file under it, of any suffix, by its bytes (data files, archives and any file
    under ``__pycache__`` as much as source), and any link or junction under it by that name alone, which refuses
    (``_unpinned``). Only the bytecode cache the interpreter writes for a source file under the root is left out, by the
    interpreter's own rule (``_interpreter_caches``): an import writes one, and the runner reads none (the freeze's threat
    model is honest drift). A root that does not exist pins nothing, so its absence is compared. A file that cannot be read,
    a folder that cannot be listed, an entry that cannot be asked, and a root that is no folder raise ``FrozenUnreadable``."""
    return {os.path.normcase(str(root)): ("" if (info := asked(root, absent=True)) is None else "a link" if linked(info)
                                          else manifest_sha256(_listing(root))) for root in roots}


def _listing(root: Path) -> dict[str, str]:
    """Every entry under ``root``, from one listing and each asked once by a stat that raises: a link by that name alone, a
    file by its bytes, and no interpreter cache."""
    links, files = [], []
    for here, dirs, names in walk(root):
        held = {name for name in (*dirs, *names) if _is_link(Path(here) / name, was=getattr(name, "info", None))}
        links += [Path(here) / name for name in (*dirs, *names) if name in held]
        files += [Path(here) / name for name in names if name not in held]
        dirs[:] = [name for name in dirs if name not in held]
    caches = _interpreter_caches(files)
    return {**{path.relative_to(root).as_posix(): "a link" for path in links},
            **{path.relative_to(root).as_posix(): _frozen_bytes(path) for path in files
               if os.path.normcase(str(path)) not in caches}}


#: The optimisation levels the interpreter writes a cache for: none, ``-O`` and ``-OO``.
_OPTIMISATIONS = ("", 1, 2)


def _interpreter_caches(files: Sequence[Path]) -> set[str]:
    """The bytecode caches this interpreter writes for the source files among ``files``, by its own rule: each source's
    cache is named as ``importlib.util.cache_from_source`` names it, at each optimisation level, in the ``__pycache__``
    folder beside it, where the interpreter writes it with no cache prefix (a runner sets one, and reads none there), so
    the same files are left out whatever prefix this process has. Normcased paths; no cache at all for an interpreter that
    writes none. Any other file under ``__pycache__``, a cache with no source or another interpreter's, is pinned."""
    if sys.implementation.cache_tag is None:
        return set()
    sources = tuple(os.path.normcase(suffix) for suffix in importlib.machinery.SOURCE_SUFFIXES)
    return {os.path.normcase(str(path.parent / "__pycache__" / Path(importlib.util.cache_from_source(str(path),
                                                                                                  optimization=level)).name))
            for path in files if os.path.normcase(path.name).endswith(sources) for level in _OPTIMISATIONS}


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
    from what this handle held, ``LedgerError``, which ``_exclusive`` answers as records that cannot be trusted. That stop,
    the tick's guard, ends the start durably (``end_start``), as every ending on a stop does."""
    try:
        loop.log.reload()
    except Exception as exc:  # noqa: BLE001 - any failure of the reload is the reading's to say, never an escape
        halted = stop_in_force(loop.root)
        if halted.code != STOP_UNREADABLE:
            raise LedgerError(f"{type(exc).__name__}: {exc}") from exc
        end_start(loop.root, halted.code, halted.reason, halted)
        reply = _blocked(STOP_UNREADABLE, halted.reason[:400], "A person makes the loop's records readable; a new "
                         "g6_improve_start lets it run again.")
        loop._beat(reply["completion_state"], STOP_UNREADABLE, reason=reply["message"])
        return reply
    return None
def must_stop(log: CycleLog) -> str:
    """Why the durable record of cycles says stop, whether or not a pause was written: the cycles themselves are the fact,
    and the pause is only its note. A tick reads it, and so does a person's dialog that lifts it."""
    for cycle in reversed(log.since_resumed()):          # every status a cycle kept counts, whatever outranked it
        for held in (cycle, *cycle.get("statuses", ())):
            if held.get("outcome") == "sealed_breach" or held.get("code") in SAFETY_STOPS:
                return held["reason"]
    streak = log.failed_run()
    if len(streak) < MAX_FAILED_CYCLES:
        return ""
    return "three failed cycles in a row: " + "; ".join(
        f"tick {c['tick']} {c['outcome']} ({str(c.get('reason', ''))[:160]})" for c in streak)


_holding = threading.local()


@contextmanager
def folder_lock(root: str | Path) -> Iterator[None]:
    """The folder's lock (its ``.tick`` mutex), taken once by a thread however often it asks: a person's act holds it from
    its last check of what the person read, through the domain's code it runs, to its write, and the loop's own
    operations inside that take it too. ``WorkspaceRefused`` while another holds it."""
    key = str(Path(root).resolve())
    held = _holding.__dict__.setdefault("roots", set())
    if key in held:
        yield
        return
    with Coordination(Path(key) / ".tick").mutex():
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)


def _exclusive(method: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """One operation at a time per folder, on what is written and not on what this handle last read: a restart can
    overlap a tick that is still running, and a person's process is not the loop's. The lock is the operating system's,
    so it dies with its process, and its refusal for another holder is a reply, never a raised error; a lock that cannot be
    taken for any other reason raises what kept it, never another operation. Records the stop decision stands on that
    do not read are that decision's to say: a stop in force, ``STOP_UNREADABLE``, as every reader says it."""

    @wraps(method)
    def invoke(self: ShadowLoop, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            with folder_lock(self.root):
                refused = _reloaded(self)
                if refused is not None:
                    return refused
                if self.mh is not None:
                    self.mh.refresh()
                return method(self, *args, **kwargs)
        except WorkspaceRefused as exc:
            if exc.code != HELD_BY_ANOTHER:
                raise              # a lock that could not be taken for what it is, or a refusal of another kind: no holder
            return _blocked(BUSY, "another operation is running on this folder; this one did nothing")
        except LedgerError as exc:
            reply = _blocked(RECORDS_UNREADABLE, f"the loop's records cannot be trusted: {exc}"[:400],
                             "A person inspects the folder. Nothing runs on records that do not check out.")
            self._beat(reply["completion_state"], RECORDS_UNREADABLE, reason=reply["message"])
            return reply

    return invoke


class ShadowLoop:
    def __init__(self, domain: Domain, root: str | Path, settings: LoopSettings, *, proposer: Any,
                 now: Callable[[], datetime] = lambda: utc(datetime.now(UTC)),
                 clock: Callable[[], float] = time.monotonic,
                 read_account: Callable[[str, datetime], AccountReading | None] | None = None,
                 read_now: Callable[[datetime], None] | None = None,
                 domain_file: str | Path | None = None) -> None:
        self.domain, self.settings, self.domain_file, self._read_now = domain, settings, domain_file, read_now
        self._now = utc_clock(now)                # every instant the loop reads is UTC, where its clock is taken in
        self.beat_fields: dict[str, Any] = {}     # what a runner adds to every heartbeat
        self.beats = True                         # a person's command sets it false: only the runner lock's holder beats
        self._records_known: tuple[str, dict | None, str] | None = None   # the records' identity, their start, its revocation
        self.evaluating = False                   # this tick has begun its held-out evaluation
        self.waited = 0                           # ticks a budget wait turned away since the last cycle: a wait leaves no row
        self.tuned: list[dict[str, Any]] = []     # this tick's tuning cycles as they happen, for a row that a raise cut short
        self._frozen_seen: dict[str, str] | None = None   # the frozen digests this tick read before it developed anything
        # what its cycle met: breaches, a refusal, a request nobody noted, a stop, and a hold for an unknown outcome
        self._met: tuple[list[str], dict[str, str] | None, bool, _Halted | None, dict[str, Any] | None] | None = None
        self._began: int | None = None            # the generations the chain held as this tick's cycle began
        self._written = False                     # the log holds this tick's row
        # ``lost_cycles`` and ``cannot_record`` are the process's for the folder (``_Folder``), so this handle starts with
        # whatever another of the folder's handles met; what was refused here is read with the store at every admission
        self.requests = _Requests(lambda: self._now(), self._halted, self._barred, self._keep_refusal, self._on_record)
        self._proposer = _Counted(proposer, self.requests)
        self.root = Path(root).resolve()
        reach = _in_reach(self.root, domain)
        if reach:                                # before anything is written: this folder is all the loop ever writes
            raise ValueError(f"[{EVALUATOR_IN_REACH}] the loop's folder overlaps {', '.join(reach)}: it never writes "
                             "what judges it")
        self.log = CycleLog(self.root / CYCLE_LOG)
        self.unreadable = ""                      # why what was refused here could not be read, which refuses every request
        try:                                      # before the first request: a restart sends nothing that was refused
            self._hold_what_is_on_record()
        except LedgerError as exc:
            self.unreadable = f"what was refused here cannot be read, so nothing could say what must not be sent: {exc}"[:400]
        self.meter = BudgetMeter(settings.windows, store=OperatorStore(self.root / "operator.db", clock=self._now),
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
        """Keep a refused request's digest, or one held for an unknown outcome, while the pending-write barrier excludes
        admission. Nothing it must do waits on a step that can fail before it. The disk is tried first, and needs no record
        of the folder (``note_refusal``); then, whatever became of the write, this process holds the request for the folder,
        whichever handle met it (``_Folder``), so no handle reads it there before it is durable. The folder's record is
        looked for only then, and once more if the folder could not be opened (``_found``). Unless the write and the record
        both held, what is on no file, or in no record of this process, could be sent again: so the folder is stopped on the
        record that was found, and a stop a person must lift is asked for whether or not one was (``ask_to_stop`` needs
        none). Each of these is tried whatever ended the one before it. An interrupt that takes the write is no failed
        write: nothing says what became of the row, a death runs none of this, and the request is held here as far as the
        folder's record is found, with no stop asked for, before the interrupt goes on its way."""
        kept: bool | None = None      # ``None``: an interrupt took the write, and nothing here says what became of it
        here = None
        try:
            kept = note_refusal(self.root, request, said)
        except Exception:  # noqa: BLE001 - any failure to keep it is a refusal only this process holds
            kept = False
        finally:
            try:
                here = self._found() or self._found()
                if here is not None:
                    here.refused.add(request)
            finally:
                if kept is False or (kept and here is None):
                    self._stop_folder(here, request, said, kept)

    def _found(self) -> _Folder | None:
        """This folder's record, or ``None`` when the folder cannot be opened for this lookup: what keeps a refusal never
        waits on it."""
        try:
            return self._here
        except Exception:  # noqa: BLE001 - a lookup that failed is a write that failed: the folder stops without it
            return None

    def _stop_folder(self, here: _Folder | None, request: str, said: str, kept: bool) -> None:
        """A refusal ``refused.log`` would not keep, or one this process could find no record to hold in: every handle of the
        folder in this process sends nothing more, by the record already found, and the folder is asked to stop."""
        lost = f"{REFUSED_FILE} would not keep it" if not kept else "this process found no record of the folder to hold it in"
        why = _writable(f"{_stopped_by(' '.join(said.split())[:160])} and {lost}, so a restart or another handle could "
                        "send it again")[:400]
        try:
            if here is not None:
                with _LOCK:           # an admission that read the folder's stop before this reads it again
                    here.cannot_record = why
                    _RECORD.moved += 1
        finally:
            try:
                ask_to_stop(self.root, why)
            except OSError:
                pass                  # every handle of the folder in this process still sends nothing more

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
            here = self._here
            with _LOCK:               # an admission that read the folder's stop before this reads it again
                here.cannot_record = why
                _RECORD.moved += 1

    @property
    def lost_cycles(self) -> int:
        """Cycles in a row this folder's log would not take, counted for the process whichever handle lost them."""
        return self._here.lost_cycles

    @lost_cycles.setter
    def lost_cycles(self, count: int) -> None:
        self._here.lost_cycles = count
    def _stop(self) -> _Stopped:
        """This folder's stop, as a code and a reason (``("", "")`` with none), which a tick and the runner read alike: if
        the runner read less, a tick refused for a stop would leave it going, and asking again. It is ``stop_in_force``'s
        reading of what is on disk, decided as every admission is (``admission``: the stop lock is asked for beside it, so
        a stop another process could not write is met here too), before each request, and that reading comes with it
        (``_Stopped.reading``). The
        start, any stop after it and the enrolment on record are kept against the digest of the very records' bytes they
        were parsed from, in that same reading, so a later reading parses again only when its bytes differ; records that
        cannot be read stop it. An enrolment on record that the start did not show is a stop with a code of its own
        (``ENROLMENT_CHANGED``), so no request goes out on an enrolment its start did not agree to."""
        if self.cannot_record:
            return _Stopped(Stop(CANNOT_RECORD, self.cannot_record))  # this process's own stop: the log took no cycle
        found = admission(self.root, stop_in_force(self.root, known=getattr(self, "_records_known", None)))
        self._records_known = (None if found.code == STOP_UNREADABLE else
                               (found.records_sha256, found.start, found.revoked, found.seen.enrolled))
        return _Stopped(found)

    def stopped_by(self) -> str:
        """Why this folder is stopped, or ``""``."""
        return self._stop().reason

    def _refusals_on_record(self) -> tuple[set[str], set[str]]:
        """Every request refused here, and every one held only for an unknown outcome, from ``refused.log`` and from the
        cycles that recorded either: what a cycle of an earlier version kept as ``asked`` still counts."""
        stops = [kept for c in self.log.cycles() for kept in (c.get("classifier_stop"), c.get("unknown_hold")) if kept]
        return _kinds([*refusal_rows(self.root), *((kept[name], str(kept.get("said", ""))) for kept in stops
                                                   for name in ("request", "asked") if kept.get(name))])

    def _hold_what_is_on_record(self) -> None:
        """This handle's meter holds what the folder's records bar, each as what it is, so a restart sends none of it."""
        refused, held = self._refusals_on_record()
        self.requests.refused |= refused
        self.requests.unknown |= held

    def _on_record(self) -> set[str]:
        """What admission reads of this folder at every send: each request ``refused.log`` holds and each this process holds
        for the folder (``_Folder``). One the store holds only for an unknown outcome is marked so (``_marked``)."""
        refused, held = _kinds(refusal_rows(self.root))
        return refused | held | self._here.refused | {_marked(request) for request in held}

    def _halted(self) -> _Halted | None:
        """What stops any request, in a tick or not: what was refused could not be read, this folder's stop, or G6's own."""
        if self.unreadable:
            try:
                self._hold_what_is_on_record()
                self.unreadable = ""
            except LedgerError:
                return _Halted(RECORDS_UNREADABLE, self.unreadable)
        found = self._stop()
        if found:
            return _Halted(found.code, found.reason, found.reading)
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
        method in the same file does not (``tick_rca``'s domain part). Read whole or not at all (``_whole``): anything that
        keeps it from being read raises ``FrozenUnreadable``."""
        return _whole(self._frozen_reading)

    def _frozen_reading(self) -> dict[str, str]:
        run, sealed, judge = self.domain.execution(), self.domain.sealed(), self.domain.judge()
        roots = _roots(self.domain)
        return {"envelope": envelope_sha256(), "domain_file": _frozen_bytes(self.domain_file) if self.domain_file else "",
                "profile_space": _profile_space(str(getattr(self.domain, "name", "")), run),
                "evaluator": digest({"judging": judging_sha256(), "judge": _judge_digest(judge, roots),
                                     "independence": [judge.answers_by, judge.is_model] if judge is not None else [],
                                     "task": run.task, "checks": [[c.predicate, c.key, c.kind, *_callable_named(c.run)]
                                                                  for c in (*run.checks, *run.optional_checks.values())],
                                     "loaded": _loaded_outside(roots)}),
                "cases": cut_pool(self.domain.development(), self.settings.seed).digest(),
                "sealed": digest({"vault": sealed.vault.digest if sealed is not None else "",
                                  "refs": [dict(r) for r in sealed.refs] if sealed is not None else []})}

    def _frozen_now(self) -> tuple[dict[str, str], bool]:
        """The frozen digests as they stand, and whether the sealed bytes changed since they were sealed, in one reading
        with the slow part first: the sealed bytes are compared before anything is digested, so an edit made while they
        are read is in the digests that follow, and nothing comes after the digests but what acts on them. The sealed bytes
        are read by the one reader the rest is (the vault's listing), and the whole reading is made whole or not at all
        (``_whole``): anything that keeps it from being made raises ``FrozenUnreadable``."""
        def reading() -> tuple[dict[str, str], bool]:
            sealed = self.domain.sealed()
            breached = sealed is not None and bool(sealed.vault.verify_unchanged())
            return self._frozen(), breached

        return _whole(reading)

    def _changed(self, against: Mapping[str, str] | None = None) -> list[str]:
        """Which of the frozen things differ from ``against``, the digests a tick read before it developed anything, or
        from what a person enrolled when none are given. The vault's digest is of the files as sealed, so the sealed bytes
        are compared as well. A reading that cannot be made whole, however that fails, differs from every reading: it is
        named as what changed, so nothing stands on it and nothing raises past it."""
        recorded = self.log.enrolled()["frozen"] if against is None else against
        try:
            return _differs(*self._frozen_now(), recorded)
        except Exception as exc:  # noqa: BLE001 - a reading that cannot be made is a change, never a raise
            return [f"what judges, which could not be read ({said(exc)})"[:400]]

    def _invalid(self) -> dict[str, Any] | None:
        if self.problems:
            return _blocked(self.problems[0][0], "; ".join(why for _, why in self.problems),
                            "Give this domain what it lacks; the loop will not run without it.")
        if self.log.enrolled() is None:
            return _blocked(NOT_ENROLLED, "no person has enrolled this domain",
                            "A person enrols it with g6_improve_enrol, in the client's dialog.")
        return None

    def _refused(self) -> dict[str, Any] | None:
        """Why this tick is not a cycle. Each is a wait or a refusal: none spends, and none is a failed cycle. This folder's
        stop, the tick's guard, ends the start it was run on durably (``end_start``), as its cut-off and the runner's ending
        do."""
        halted = self._halted()
        if halted is not None:
            if halted.code in FOLDER_STOPS:
                end_start(self.root, halted.code, halted.said, halted.reading)
            return _blocked(halted.code, halted.said, "Remove the STOP file to resume." if halted.code == STOPPED else
                            "A person inspects the folder. Nothing runs on records that do not check out."
                            if halted.code == RECORDS_UNREADABLE else
                            "A person makes the stop's files readable; a new g6_improve_start lets it run again."
                            if halted.code == STOP_UNREADABLE else "A person frees the log, then starts the loop again with "
                            "g6_improve_start." if halted.code == CANNOT_RECORD else
                            "A person starts it again with g6_improve_start, which shows them the enrolment that stands."
                            if halted.code == ENROLMENT_CHANGED else "Whoever engaged the stop releases it.")
        invalid = self._invalid()
        if invalid is not None:
            return invalid
        lost = self._lost_generations()
        if lost:
            return _blocked(RECORDS_UNREADABLE, lost, "A person inspects the folder. Nothing runs on records that do not "
                            "check out.")
        paused = self.log.paused() or self._must_stop()
        if paused:
            return _blocked(PAUSED, f"the loop is paused: {paused}",
                            "A person reads why, then lifts it with g6_improve_resume, in the client's dialog.")
        try:                                     # the reading's one boundary (``_whole``) raises nothing else
            now, breached = self._frozen_now()
        except Exception as exc:  # noqa: BLE001 - no reading of what judges stands, so none is compared or kept
            return _blocked(FROZEN_CHANGED, f"what judges could not be read ({said(exc)}), so nothing measured against it "
                            "would mean what it did", "A person makes it readable; the next tick reads it again.")
        changed = _differs(now, breached, self.log.enrolled()["frozen"])
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
        return must_stop(self.log)

    @_exclusive
    def enrol(self, confirmed_by: str, *, last_check: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
              record_id: str | None = None
              ) -> dict[str, Any]:
        """A person confirms what judges this domain, as it stands now. It is the one step the loop cannot do.
        ``last_check``: given what would be frozen, under the folder's lock and before anything is written, the refusal
        when that is no longer what the person read (``g6.mcp.improve``), or ``None``."""
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
            return _unpinned_reading(exc)
        refused = last_check(frozen) if last_check is not None else None
        if refused is not None:
            return refused
        try:                                     # the harness pins its judging code as it confirms, by the same reader
            confirmed = self.mh.confirm(who, initial=self.domain.execution().initial)
        except Unreadable as exc:
            return _unpinned_reading(exc)
        if confirmed["completion_state"] == "blocked-escalated":
            return confirmed
        exposed = (*self.cut.tune, *self.cut.selection)
        self.log.enrol(who, frozen, record_id=record_id, domain=self.domain.name, at=self._now().isoformat(),
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

    def tick(self) -> dict[str, Any]:
        """One cycle. A heartbeat follows every tick, so someone watching can tell a loop that is waiting from one that
        has died. What the handle holds of an earlier tick is dropped first, before the lock is asked for: a pass that
        fails before its tick's body runs (the lock, the reload, the workspace's refresh) reaches ``fault`` holding
        nothing of a cycle that is not its own."""
        self._frozen_seen = None                  # no tick adopts on a reading an earlier tick made
        self._met = None                          # nor carries what an earlier tick's cycle met,
        self._began, self._written = None, False  # made or wrote
        return self._tick()

    @_exclusive
    def _tick(self) -> dict[str, Any]:
        try:
            reply = self._refused() or self._run()
        except BaseException:
            self._keep()                          # no tick leaves with what its cycle met held nowhere but here
            raise
        self._beat(reply["completion_state"], reply["warning_card"]["code"], reason=reply["message"])
        return reply

    @_exclusive
    def fault(self, exc: BaseException) -> dict[str, Any]:
        """A tick that raised past its own guard is a failed cycle like any other, so three in a row still pause. It ends on
        the same frozen check and the same rule as every cycle that adopts nothing (``_still_frozen``, ``_coded``), held to
        what the tick read before it developed anything, or to what a person enrolled when it read nothing, so a frozen
        change in a failed cycle is kept and ranked, and it carries every safety status the tick's cycle met before it
        failed that no row carries yet (``_owed``), so a restart pauses on them and no status is said by two rows, and
        every generation its harness made (``_made``), first,
        as it came first, so a rollback ranks as it does on any row. A loop already paused takes no failed cycle, unless
        the pause is the one this tick wrote on its way out for what its own cycle met (``_keep``) and its row is still
        owed (``_owed``)."""
        if self.log.paused() and not self._owed():
            return _blocked(PAUSED, f"the loop is paused: {self.log.paused()}", "A person reads why, then lifts it.")
        row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error", "code": CYCLE_FAILED,
               "reason": f"{type(exc).__name__}: {exc}"[:500]}
        statuses: list[Mapping[str, Any]] = [*self._made(row), row, *self._owed(row)]
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
        if not self.beats:
            return
        beat(self.root, **{**self.beat_fields, **said}, domain=self.domain.name, at=self._now().isoformat(),
             tick=len(self.log.cycles()), state=state, code=code)

    def _reserve(self) -> tuple[Admission | None, dict[str, Any] | None]:
        """This tick's worst case on the provider its calls go to, or the wait: room on another pays for none of them. The
        limits on the record are read first, whichever handle or process met them, so a limit is never one handle's alone. A
        tick runs under its folder's tick lock, which one tick holds at a time, so a reservation a tick made that is still
        open here is one a tick that is gone left: it is settled in full first, or it would count in every week to come.
        One made outside a tick is left to its maker (``BudgetMeter.recover``). This tick's own is recorded as a tick's."""
        for provider, until in self.log.limits().items():
            self.meter.limit_reached(provider, until)
        self.meter.recover(self.provider)
        spend = self.meter.reserve(self.provider, self._now(), self.worst_case(), tick=uuid.uuid4().hex)
        if spend.admitted:
            return spend, None
        return None, _blocked(BUDGET_WAIT, f"{spend.provider}: {spend.reason}",
                              "The loop waits; it runs again when a ceiling or a reset allows.",
                              wait_until=spend.wait_until.isoformat() if spend.wait_until else "")

    def _run(self) -> dict[str, Any]:
        self._hold_what_is_on_record()            # and any other process has kept since
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
        self._began = len(self.mh.adoption_rows())    # every generation after these is this cycle's (``_made``)
        self.requests.open(spend.amount, spend.ends)
        opened = self.requests.generation         # a close that ends this tick moves it on (``_Requests._reset``)
        with sealed_closed(self.domain.sealed().vault) as breaches:
            try:                      # a sealed file already held open: no cycle starts over it
                row = self._row() if breaches else self._cycle(recommended)
            except (_AtLimit, _Refused):  # what the tick latched decides how it ends, read as it closes
                row = self._row()
            except _Halted as reached:
                halted = reached
                row = self._row()
            except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
                row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                       "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]}
            finally:             # whatever ended it, the tick closes in one step with what it latched, which decides from here
                try:
                    self.requests.close()
                finally:         # a close that raised before it ended the tick: what it latched, read in one step
                    ended = self.requests.closed if self.requests.generation != opened else self.requests.latched()
                    # what the cycle met, kept before anything else that can fail (``_carried``), and its limits on disk
                    # with it; a tick that leaves by a raise from here on writes what it met on its way out (``tick``,
                    # ``_keep``)
                    self._met = (breaches, ended.refusal, ended.unseen, halted, ended.hold)
                    for until, _ in ended.met:      # every limit it met, in the order met, kept before its charges settle
                        self._keep_limit(spend.provider, until)
                # use nobody could count stays charged at its worst case
                charged = self.meter.settle(spend, spend.amount if ended.unseen else ended.sent)
        stopped = ended.stopped
        limit = _AtLimit(*ended.limited) if ended.limited is not None else None
        if self.tuned and "tuning" not in row:     # a tick cut short keeps what it had made
            row["tuning"] = reflex.funnel(self.tuned)
        held = self._carried(row)                 # what held over the cycle: a strategy that swallowed a refusal adopts
        made = self._made(row)                    # nothing either; and a generation made that a raise kept off the row
        if not held and not made and limit is not None:
            # a wait and no cycle, like any other: what ran is charged, none of it counts, nothing is adopted, and a frozen
            # change stays on disk for the next tick's reading to refuse
            self.waited += 1
            return _blocked(BUDGET_WAIT, f"{spend.provider} met a limit after {charged:g} requests, so this tick scored nothing "
                            f"and waits until {limit.until.isoformat()}: {limit.said}",
                            "The loop waits; it runs again when the limit ends.", wait_until=limit.until.isoformat(),
                            charged=charged)
        if not held and not made and halted is not None:   # also no cycle, and as a limit: what ran is charged, none
            return self._cut_off(halted, charged)          # of it counts; a cycle that made a generation is a row
        if stopped:                   # runs cut off mid-tick compared nothing: no gate result stands on them
            held.append({"outcome": "blocked", "code": TICK_BUDGET_SPENT, "reason": f"{stopped}, so nothing is adopted"})
        statuses = [*made, *([row] if "outcome" in row else [])]   # the cycle's own end is a status like the rest
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

    def _carried(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """The safety statuses this tick's cycle met, as it kept them the moment its work ended and before settlement
        (``_met``), so every row the tick writes carries them, a fault's row among them, and a tick that leaves with no
        row writes them on its way out (``_keep``): a restart pauses on them whatever failed after they were met. A
        classifier's stop puts the request it refused on the row as well, where ``lost_cycle`` and the next tick read
        it. The sealed split's breaches are read as they stand, since its guard counts a file still held open on its
        way out too."""
        if self._met is None:
            return []
        breaches, refusal, unseen, halted, hold = self._met
        held: list[dict[str, Any]] = []
        if breaches:
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
            if hold is not None:
                row["unknown_hold"] = hold
            held.append({"outcome": "blocked", "code": CLASSIFIER_STOP,
                         "reason": f"{_stopped_by(refusal['said'])}: nothing is adopted, it is not sent again, and a "
                                   "person reads this before the loop goes on"})
        elif hold is not None:        # no classifier said anything: its own code and field, and the same pause for a person
            row["unknown_hold"] = hold
            held.append({"outcome": "blocked", "code": HOST_UNKNOWN,
                         "reason": f"{_stopped_by(hold['said'])}: nothing is adopted, its inputs are held and not sent "
                                   "again, and a person reads this before the loop goes on"})
        if halted is not None and halted.code in HOLDS:    # a fault that is on the record, and holds the loop for a person
            held.append({"outcome": "error", "code": halted.code,
                         "reason": f"{halted.said}: nothing was sent, and nothing is adopted"})
        return held

    def _made(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """The generations this tick's cycle made that ``row`` does not say, read from the chain of adoptions, where the
        harness keeps them, and never from what the tick held when something after them raised: each row the loop itself
        added since the cycle began (``_began``) that the log has not seen. Each is a status, and gives ``row`` its head,
        a rollback what it undid as well, so the one rule (``_coded``) ranks a rollback first on a failed cycle's row as
        on any other, and the log counts its generation. A person's own row there, or another handle's cycle's, is not
        this cycle's."""
        if self._began is None:
            return []
        seen, made = self.log.generation(), []
        for kept in self.mh.adoption_rows()[self._began:]:
            outcome = "rolled_back" if kept["kind"] == "rollback" else "adopted"
            if kept["by"] != ADOPTER or kept["version"] <= seen or (
                    row.get("outcome") == outcome and row.get("head_version") == kept["version"]):
                continue
            made.append({"outcome": outcome, "code": ROLLED_BACK if outcome == "rolled_back" else ADOPTED,
                         "reason": f"generation {kept['version']} was made before this cycle ended: {kept['change']}"})
            row |= {"head_version": kept["version"], **({"undoes": kept["undoes"]} if outcome == "rolled_back" else {})}
        return made

    def _owed(self, row: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """The safety statuses this tick's cycle met that no row in the log carries yet (``_carried``, into ``row``)."""
        return [] if self._written else self._carried({} if row is None else row)

    def _keep(self) -> None:
        """Every safety status this tick's cycle met and no row carries, on disk before the tick leaves by a raise: the
        pause a restart reads (``CycleLog.paused``), for the reason its row would have given. A tick has two ways out:
        it returns, having written its row or stopped the folder for the row the log would not take (``_record``,
        ``lost_cycle``), or it raises, through here. So whatever raised, and whoever answers or raises after it (the
        fault's own reading of the records, the fault, the runner's hand-off, or nobody at all), the loop stays paused
        for a person, and the fault's row, when one is written, says why as well. A pause the log will not take, for
        any reason, stops the folder at once (``_closed``), as a row it will not take does: an error of the log's is
        left there, and an interrupt or an exit that landed inside the pause's write goes on up once the stop is
        written."""
        held = self._owed()
        if not held:
            return
        why = _safety_in({"statuses": held})
        try:
            self.log.pause(why)
        except BaseException as exc:  # noqa: BLE001 - whatever keeps the note from the log, an interrupt or an exit that
            # landed inside its write among them, the stop is written in its place; what is no error then goes on up
            self._closed(f"{why[:200]}, and the log would not take its pause ({type(exc).__name__}: {exc})"[:400])
            if not isinstance(exc, Exception):
                raise

    def _closed(self, why: str, here: _Folder | None = None) -> None:
        """This process spends no more, and the folder is stopped: ``STOP`` and ``stops.log`` are other files than the
        log, so a restart stays closed until a person starts it again, and should those fail too, this process still
        ends on what it holds here. The stop is held on the folder's record a caller already found (``here``), or on the
        one found now, and never waits on a second lookup; a lookup that fails takes nothing from the ``STOP`` file. An
        interrupt or an exit that lands inside the stop's own write has it asked for once more, since nothing else is
        left to hold it, and then goes on up."""
        try:
            here = here if here is not None else self._found()
            if here is not None:
                with _LOCK:           # every admission of the folder that read its stop before this reads it again
                    here.cannot_record = why
                    _RECORD.moved += 1
        finally:
            try:
                ask_to_stop(self.root, why)
            except Exception:  # noqa: BLE001 - the run still ends on what is held here, whatever kept the file
                pass
            except BaseException:  # an interrupt or an exit landed inside the stop's own write: nothing else is left to
                try:               # hold it, so it is asked for once more before that goes on up
                    ask_to_stop(self.root, why)
                except Exception:  # noqa: BLE001 - as above
                    pass
                raise

    def _keep_limit(self, provider: str, until: datetime) -> None:
        """A limit or capacity a provider reported, met in the tick that just closed: this process holds it, and the log keeps
        it for a restart, under that tick, so each of the tick's limits stands until the furthest of them ends. A log that
        would not take it leaves this process waiting, and after a restart the first request meets the limit again, which
        reads as one again and scores nothing."""
        self.meter.limit_reached(provider, until)
        try:
            self.log.limit(provider, until.isoformat(), self.requests.closed.tick)
        except Exception:  # noqa: BLE001 - the note is left, whatever kept it: this process knows the limit already
            pass

    def _cut_off(self, halted: _Halted, charged: float) -> dict[str, Any]:
        """A stop met at a model call: say what it cut off, and note this folder's stop in the records, which this tick
        holds, so no one else can, and through the stop's own writer beside it (``end_start``), so the start it cut off
        stays ended whatever became of that note or of a file that could not be read."""
        dropped = (", and the held-out evaluation under way was dropped, so none of the split's five was spent"
                   if self.evaluating else "")
        if halted.code in FOLDER_STOPS:
            try:
                self.log.stop(halted.said, at=self._now().isoformat(),
                              cut_off={"charged": charged, "evaluation": self.evaluating})
            except Exception:  # noqa: BLE001 - a note that cannot be written, for any reason: end_start covers what it must
                pass
            end_start(self.root, halted.code, halted.said, halted.reading)
        return _blocked(halted.code, f"a stop ended this tick at a model call after {charged:g} requests{dropped}: {halted.said}",
                        "A new g6_improve_start lets it run again." if halted.code in FOLDER_STOPS else
                        "Whoever engaged the stop releases it.", charged=charged)

    def _record(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = self._reply(self.log.cycle(row))
        except (OSError, LedgerError) as exc:
            return self.lost_cycle(exc, refusal=row.get("classifier_stop") or row.get("unknown_hold"),
                                   safety=_safety_in(row))
        self._written, self._began = True, None   # the row holds all the tick's cycle met and made: no later row says it again
        self.lost_cycles = self.waited = 0
        self._pause_if_failing()
        return reply

    def lost_cycle(self, exc: BaseException, *, refusal: dict[str, str] | None = None, safety: str = "") -> dict[str, Any]:
        """A cycle the log would not take. The streak that pauses the loop is read from the log, so these leave it nothing
        to read and are counted in this process, for the folder, whichever of its handles lost them, for as long as the
        process lives. At ``MAX_FAILED_CYCLES`` in a row the loop stops
        for good: ``STOP`` and ``stops.log`` are other files, so a restart stays closed until a person starts it again, and
        should those fail too, this process still spends no more. A cycle that is written starts the count again. A cycle
        that holds a safety stop (``safety``, its reason, or a classifier's ``refusal``) stops the loop at the first, since
        the pause a person must read would have stood on it, so a restarted handle reads it all the same. Whoever hands
        the cycle over, a fault that never reached its row among them (``Runner._fault``), what the tick's cycle met and
        no row carries yet is read here (``_owed``), never left to the caller to pass."""
        here = self._here
        with _LOCK:                   # the process's count for the captured folder, with no custody lookup under admission
            here.lost_cycles += 1
            lost = here.lost_cycles
        if refusal is None and not safety:
            owed: dict[str, Any] = {}
            safety = _safety_in({"statuses": self._owed(owed)})
            refusal = owed.get("classifier_stop") or owed.get("unknown_hold")
        why = (f"{lost} of the {MAX_FAILED_CYCLES} cycles in a row that stop the loop could not be written to "
               f"its log ({type(exc).__name__}: {exc})")[:400]
        if safety:
            why = f"{safety[:200]}, and the log would not take that cycle ({type(exc).__name__}: {exc})"[:400]
        if refusal is not None:
            why = _writable(f"{_stopped_by(refusal['said'])} and the log would not take that cycle "
                            f"({type(exc).__name__}: {exc})")[:400]
        if refusal is not None or safety or lost >= MAX_FAILED_CYCLES:
            self._closed(why, here)
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
    def resume(self, accepted_by: str, *, last_check: Callable[[], dict[str, Any] | None] | None = None) -> dict[str, Any]:
        """A person lifts a pause, having read why. The loop cannot lift its own. ``last_check`` as for ``enrol``."""
        who = accepted_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person lifts a pause (no empty or auto: name)")
        refused = last_check() if last_check is not None else None
        if refused is not None:
            return refused
        if self.log.paused() or self._must_stop():
            self.log.resume(who)
        return _envelope("qualified-draft", RESUMED, "the loop is not paused", next_action="loop.tick()")

    @_exclusive
    def demote(self, generation: int, asked_by: str, *,
               last_check: Callable[[Mapping[str, Any]], dict[str, Any] | None] | None = None) -> dict[str, Any]:
        """A person takes back the generation at the head, having read the digest. The loop goes on from what stood before.
        ``last_check`` as for ``enrol``, given what the demotion would restore (``ShadowHarness.demote``)."""
        who = asked_by.strip()
        if not who or is_automated(who):
            return _blocked(APPROVAL_REQUIRED, "a person takes a generation back (no empty or auto: name)")
        invalid = self._invalid()
        if invalid is not None:
            return invalid
        lost = self._lost_generations()
        if lost:
            return _blocked(RECORDS_UNREADABLE, lost, "A person inspects the folder. Nothing runs on records that do not check out.")
        taken = self.mh.demote(generation, who, last_check=last_check or (lambda found: None))
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
