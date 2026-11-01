# SPDX-License-Identifier: Apache-2.0
"""The self-improvement loop's tick, in shadow: it works on any domain, with no person in it.

One tick is one cycle of design 6.1. **Measure** the head on the tuning cases. **Identify** the constraint: the cause
a profile can change that costs the most cases, through ``self_maintenance.constraints``, and none at all when the
failure records were not independently observed. **Exploit**, by proposing within the meta-harness's editable schema:
``method.tune`` runs its usual proposers, with ``FailureTraceStrategy`` shown only the constraint's failures, and races
them on the selection split. **Gate**: the champion goes to the held-out split and the unchanged gate under
``EvidencePolicy``, which holds it to ``GATED``. **Record** the cycle in the local constraint log, and **adopt** what the
gate recommends in the loop's own workspace (``g6.metaharness.shadow``), so the next tick starts from it.

The only person in it is the one who enrols a domain: they confirm what judges the work, once, and the loop runs from
there. Someone has to say what "better" means, and it cannot be the thing being judged.

``python -P -m g6.metaharness.loop run <folder>`` runs ticks on their own (``g6.metaharness.runner``).

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been
established.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

import g6
from g6.core import stop
from g6.core.adaptation_gate import is_automated
from g6.core.ledger import LedgerError, digest
from g6.core.llm import _WAITS, DEFAULT_MAX_TOKENS, LLMError
from g6.core.model_calls import counting
from g6.integrity import build_manifest, manifest_sha256
from g6.metaharness.api import _blocked, _envelope, judging_sha256
from g6.metaharness.domain import (
    FALLBACK_MODEL,
    Cut,
    Domain,
    FailureRecord,
    Judge,
    backends,
    cut_pool,
    domain_problems,
    judge_source,
    sealed_closed,
)
from g6.metaharness.failure_strategy import FailureTraceStrategy
from g6.metaharness.method import Method, tune
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
    CycleLog,
    ask_to_stop,
    beat,
    file_digest,
    stop_reason,
)
from g6.self_maintenance.pacing import AccountReading, Admission, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import limit_ends
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
TICK_BUDGET_SPENT = "G6_E_LOOP_TICK_BUDGET_SPENT"
UNMETERED_CALLER = "G6_E_LOOP_UNMETERED_CALLER"
#: What enforces the envelope, beside ``JUDGING_PATTERNS``: the loop's own files, and what they spend, seal, pin and propose
#: with. A test holds every module those files import to this set or that one.
ENVELOPE_PATTERNS = ("metaharness/domain.py", "metaharness/failure_strategy.py", "metaharness/loop.py",
                     "metaharness/method.py", "metaharness/pool.py", "metaharness/propose.py",
                     "metaharness/recovery.py", "metaharness/reflect.py", "metaharness/runner.py",
                     "metaharness/select.py", "metaharness/shadow.py", "self_maintenance/constraints.py",
                     "self_maintenance/digest.py", "self_maintenance/envelope.py", "self_maintenance/pacing.py",
                     "self_maintenance/usage.py", "operator/gates.py", "operator/store.py", "core/adaptation_gate.py",
                     "core/llm.py", "core/vault.py", "integrity/*.py")
UNKNOWN = "unknown"
#: What the gate holds this loop's candidates to (the owner's ruling, 2026-11-01). Wall clock is left out: differences of
#: milliseconds refused real gains on noise, each spending one of the split's evaluations. It is still measured, and reported
#: as not gated. The gate and ``default_objectives`` are as they were, so a workflow a person runs still holds wall clock.
GATED = ("success", "model_calls")
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


class _Requests:
    """What a tick has sent to its backends, the limit it reserved, and when the week it reserved in ends. Past either,
    nothing more goes out: a reset drops the ceiling to a new week's first day, and nothing is reserved under that one.
    ``stopped`` says which stopped it. A request takes its place in ``sent`` before it goes out, under one lock, so requests
    running at once can never pass the limit between them."""

    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.now = now
        self._lock = threading.Lock()
        self.close()

    def open(self, limit: float, ends: datetime | None = None) -> None:
        self.sent, self.limit, self.ends, self.stopped, self.unseen = 0, limit, ends, "", False

    def close(self) -> None:
        self.open(math.inf)

    def admit(self, wait: float = 0.0, *, places: int = 0) -> None:
        """Refuse what would pass the limit, and take ``places`` in ``sent`` in that same step. ``wait``: the seconds a
        server asked for before this one goes out."""
        with self._lock:
            if self.unseen or self.sent >= self.limit:
                self.stopped = f"this tick has sent {self.sent} of the {self.limit:g} it reserved"
            elif self.ends is not None and self.now() + timedelta(seconds=wait) >= self.ends:
                self.stopped = f"this tick's week ended at {self.ends.isoformat()} and nothing is reserved in the new one"
            else:
                self.sent += places
                return
        raise LLMError(f"{TICK_BUDGET_SPENT}: {self.stopped}")

    def take(self, wait: float = 0.0) -> None:
        """A request about to go out. It keeps its place whatever becomes of it: a failed one may have reached the model."""
        self.admit(wait, places=1)

    def count(self, requests: int) -> None:
        """Requests that had no boundary to take a place at."""
        with self._lock:
            self.sent += requests

    def at_a_limit(self, failed: LLMError) -> None:
        """End the tick when a request failed at a provider's limit. Outside a tick there is none to end."""
        until = limit_ends(str(failed), self.now())
        if until is not None and self.limit != math.inf:
            raise _AtLimit(until, str(failed)[:300]) from failed


class _Resends(list):
    """The resends of one call. ``g6.core.llm`` adds each to the open records of ``waits`` just before it waits and sends
    again, which is the only boundary a resend has, so adding to this record is the gate on it, as ``counting``'s observer
    is on a request. What ``take`` raises stops the resend, and leaves the caller as a failed request's ``LLMError``. That
    boundary comes before the wait and nothing marks its end, so a wakeup that runs late is not seen."""

    def __init__(self, take: Callable[[float], None]) -> None:
        super().__init__()
        self.take = take

    def append(self, wait: dict[str, Any]) -> None:
        self.take(wait["retry_after_s"])
        super().append(wait)


@contextmanager
def _gating_resends(take: Callable[[float], None]) -> Iterator[None]:
    """As ``g6.core.llm.waits``, whose record is a plain list, with a record that is the gate above."""
    token = _WAITS.set((*_WAITS.get(), _Resends(take)))
    try:
        yield
    finally:
        _WAITS.reset(token)


class _Counted:
    """A model caller the host meters. Every call of a tick passes through one, and what it counts is what reached a
    backend, not how often it was called: ``g6.core.model_calls`` notes each request as it goes out, so an ``EmptyRetry``
    or a ``FallbackCaller`` wrapped around a built-in caller is charged attempt by attempt, and a ``Retry-After`` resend
    takes its place from ``g6.core.llm.waits``. Nothing a run reports about itself enters the count.

    Each noted request takes its place at that same boundary, before it goes out, so a retry inside a caller stops at what
    the tick reserved. ``counting`` asks its observers not to raise, because an observer is normally a bystander at someone
    else's request. This one is the gate on that request: it raises before anything is sent or counted, and what it raises
    is the ``LLMError`` a failed request raises, which every caller here passes on. That file is bound, so its sentence
    stays until an issuance window.

    A caller that answers with no request noted is refused, because nothing could say how often it asked. The limit of
    this: a caller that notes one request and retries unnoted looks like one request, and so do the Anthropic SDK's own
    retries. Only a usage record from the provider can show those, which is the relay of design section 3. A ``Retry-After``
    resend is admitted as a request is, at its own boundary (``_Resends``), whichever caller sent it."""

    def __init__(self, inner: Any, requests: _Requests) -> None:
        self.inner, self.requests = inner, requests

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        self.requests.admit()
        took: list[None] = []

        def next_request() -> None:
            self.requests.take()
            took.append(None)

        with counting(before_call=next_request) as noted, _gating_resends(self.requests.take):
            try:
                text = self.inner.complete(system, user, max_tokens=max_tokens)
            except LLMError as failed:
                self.requests.at_a_limit(failed)
                raise
            finally:
                self.requests.count(noted() - len(took))     # a child's totals have no boundary
            if not noted():
                self.requests.unseen = True
                raise LLMError(f"{UNMETERED_CALLER}: a model caller answered with no request noted")
        return text


def _workflow(domain: Domain, cut: Cut, settings: LoopSettings, requests: _Requests) -> Workflow:
    run, judge = domain.execution(), domain.judge()
    models = {name: dataclasses.replace(model, caller=_Counted(model.caller, requests))
              for name, model in run.models.items()}
    suite = Suite(dev=cut.tune, selection=cut.selection, held_out=cut.held_out,
                  provenance={"dev": "caller", "held_out": judge.answers_by})
    return Workflow(name=domain.name, task=run.task, checks=run.checks, oracle=judge.verdict, suite=suite,
                    models=models, objectives=tuple(o for o in default_objectives() if o.name in GATED),
                    policy=EvidencePolicy(max_eval_model_calls=settings.eval_calls),
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
    """The enforcing code: no candidate changes the envelope (design 6.5), and a change a person made is theirs to
    accept by enrolling again. It is a tripwire, not custody: the code that compares this digest is among what it
    covers, so an edit to that code can skip the comparison. Holding it needs the read-only checkout of design 6.5."""
    return manifest_sha256(build_manifest(_SOURCE, patterns=ENVELOPE_PATTERNS))


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
                 domain_file: str | Path | None = None) -> None:
        self.domain, self.settings, self._now, self.domain_file = domain, settings, now, domain_file
        self.beat_fields: dict[str, Any] = {}     # what a runner adds to every heartbeat
        self.lost_cycles = 0                      # cycles in a row the log would not take: it cannot hold this count
        self.cannot_record = ""                   # why this process spends no more, set at MAX_FAILED_CYCLES of those
        self.requests = _Requests(lambda: self._now())
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
        self.problems = domain_problems(domain)
        if backends(proposer) > 1:
            self.problems.append((FALLBACK_MODEL, "the proposer can answer from more than one backend, so a limit on one would "
                                  "pass unseen and its calls could go where no window covers"))
        if not settings.windows:
            self.problems.append((NO_BUDGET, "no provider window: an unknown budget stops the loop"))
        named = tuple(self.meter.windows)
        self.provider = settings.provider or (named[0] if len(named) == 1 else "")
        if named and self.provider not in named:     # the callers are the domain's: nothing here can see where they go
            self.problems.append((NO_BUDGET, f"the windows are for {', '.join(named)} and this folder's calls are stated to "
                                  f"go to {settings.provider or 'none of them'}: an allowance pays only for its own "
                                  "provider's requests, so LOOP must name the one they go to"))
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
                                                          workspace=self.root / "workspace", clock=self._clock)

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
                       per_run * race_calls(len(cut.selection), entered, method.race_block, method.race_keep))

        one_case = per_run * (per_run + 2)                   # base, candidate and a control matched to the candidate's calls
        return (2 * per_run * min(self.settings.audit_per_tick, len(cut.monitor)) + on_dev(1)
                + method.cycles * proposing + (method.cycles - 1) * racing(racers)
                + racing(racers + len(self.mh.unfinished()))
                + min(self.settings.eval_calls + one_case, len(cut.held_out) * one_case))

    def _frozen(self) -> dict[str, str]:
        """What must not change, read fresh from the domain: the code that judges, every frozen case, and the sealed
        split, and the file that declares it all when a runner opened one. The judge's whole file is digested, since a
        helper it calls judges too. No profile is in it."""
        run, sealed, judge = self.domain.execution(), self.domain.sealed(), self.domain.judge()
        return {"envelope": envelope_sha256(), "domain_file": file_digest(self.domain_file),
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
        halted = self.cannot_record or stop_reason(self.root)
        if halted:
            return _blocked(STOPPED, halted, "Remove the STOP file to resume.")
        engaged = stop.engaged()
        if engaged is not None:
            return _blocked(stop.STOPPED, f"G6 is stopped: {engaged}", "Whoever engaged the stop releases it.")
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

    def _lacking(self) -> dict[str, Any] | None:
        """The domain checked as it is now: a handle's first check says nothing of what has changed since."""
        problems = domain_problems(self.domain)
        if not problems:
            return None
        return _blocked(problems[0][0], "; ".join(why for _, why in problems),
                        "Give this domain what it lacks; the loop will not run without it.")

    def _must_stop(self) -> str:
        """Why the durable record of cycles says stop, whether or not a pause was written: the cycles themselves are the
        fact, and the pause is only its note."""
        cycles = self.log.since_resumed()
        for cycle in reversed(cycles):
            if cycle["outcome"] == "sealed_breach" or cycle.get("code") == UNMETERED_CALLER:
                return cycle["reason"]
        streak = self.log.failed_streak()
        if streak < MAX_FAILED_CYCLES:
            return ""
        return "three failed cycles in a row: " + "; ".join(
            f"tick {c['tick']} {c['outcome']} ({str(c.get('reason', ''))[:160]})" for c in cycles[-streak:])

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
        """This tick's worst case on the provider its calls go to, or the wait: room on another pays for none of them."""
        spend = self.meter.reserve(self.provider, self._now(), self.worst_case())
        if spend.admitted:
            return spend, None
        return None, _blocked(BUDGET_WAIT, f"{spend.provider}: {spend.reason}",
                              "The loop waits; it runs again when a ceiling or a reset allows.",
                              wait_until=spend.wait_until.isoformat() if spend.wait_until else "")

    def _run(self) -> dict[str, Any]:
        spend, waiting = self._reserve()
        if waiting is not None:
            return waiting
        self._beat("running", CYCLE)
        recommended: list[dict[str, Any]] = []
        limit: _AtLimit | None = None
        self.requests.open(spend.amount, spend.ends)
        with sealed_closed(self.domain.sealed().vault) as breaches:
            try:                      # a sealed file already held open: no cycle starts over it
                row = self._row() if breaches else self._cycle(recommended)
            except _AtLimit as reached:
                row, limit = self._row(), reached
            except Exception as exc:  # noqa: BLE001 - an unattended loop records the failure and lives to its next tick
                row = {"at": self._now().isoformat(), "domain": self.domain.name, "outcome": "error",
                       "code": CYCLE_FAILED, "reason": f"{type(exc).__name__}: {exc}"[:500]}
            finally:             # use nobody could count stays charged at its worst case
                charged = self.meter.settle(spend, spend.amount if self.requests.unseen else self.requests.sent)
        unseen, stopped = self.requests.unseen, self.requests.stopped
        self.requests.close()
        if breaches:                  # read only now: a strategy that swallowed the refusal adopts nothing either
            row |= {"outcome": "sealed_breach", "code": SEALED_BREACH,
                    "reason": f"{len(breaches)} sign(s) that the sealed split was within reach while developing. Nothing "
                              "is adopted. A read through a handle already open leaves no trace, so a person decides "
                              "whether it still counts as sealed"}
        elif unseen:
            row |= {"outcome": "error", "code": UNMETERED_CALLER,
                    "reason": "a model caller answered with no request noted, so the loop cannot count what it sends: "
                              "nothing is adopted, and a person gives it a caller that notes each request"}
        elif limit is not None:       # a wait and no cycle, like any other: what ran is charged, and none of it counts
            self.meter.limit_reached(spend.provider, limit.until)
            try:
                self.log.limit(spend.provider, limit.until.isoformat())
            except (OSError, LedgerError):
                pass                  # this process still waits; after a restart the first request finds the limit again
            return _blocked(BUDGET_WAIT, f"{spend.provider} met a limit after {charged:g} requests, so this tick scored nothing "
                            f"and waits until {limit.until.isoformat()}: {limit.said}",
                            "The loop waits; it runs again when the limit ends.", wait_until=limit.until.isoformat(),
                            charged=charged)
        elif stopped:                 # runs cut off mid-tick compared nothing: no gate result stands on them
            row |= {"outcome": "blocked", "code": TICK_BUDGET_SPENT, "reason": f"{stopped}, so nothing is adopted"}
        elif recommended:
            row |= self._adopt(recommended[-1]["decision"], row)
        seen = self.meter.reading(spend.provider, self._now())
        row["budget"] = {"provider": seen.provider, "day": seen.day, "charged": charged, "used": seen.used,
                         "target": seen.target, "ceiling": seen.ceiling, "estimated": seen.estimated}
        return self._record(row)

    def _record(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            reply = self._reply(self.log.cycle(row))
        except (OSError, LedgerError) as exc:
            return self.lost_cycle(exc)
        self.lost_cycles = 0
        self._pause_if_failing()
        return reply

    def lost_cycle(self, exc: BaseException) -> dict[str, Any]:
        """A cycle the log would not take. The streak that pauses the loop is read from the log, so these leave it nothing
        to read and are counted here, for as long as this process lives. At ``MAX_FAILED_CYCLES`` in a row the loop stops
        for good: ``STOP`` and ``stops.log`` are other files, so a restart stays closed until a person starts it again, and
        should those fail too, this process still spends no more. A cycle that is written starts the count again."""
        self.lost_cycles += 1
        why = (f"{self.lost_cycles} of the {MAX_FAILED_CYCLES} cycles in a row that stop the loop could not be written to "
               f"its log ({type(exc).__name__}: {exc})")[:400]
        if self.lost_cycles >= MAX_FAILED_CYCLES:
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
        constraint = name_constraint(records, len(view.dev))
        row |= {"constraint": constraint.name, "basis": constraint.basis,
                "observations": [list(o) for o in constraint.observations],
                "failure_records": dict(Counter(r.cause for r in records))}
        if constraint.name == UNKNOWN:
            return {**row, "outcome": "no_constraint", "code": NO_CONSTRAINT, "reason": constraint.reason}
        return {**row, **self._exploit(constraint, recommended)}

    def _exploit(self, constraint: Constraint, recommended: list[dict[str, Any]]) -> dict[str, Any]:
        """Propose and gate. A recommendation is only held here: it is adopted once the sealed split is known untouched."""

        def nobody(decided: dict[str, Any]) -> None:
            recommended.append(decided)              # no name is given: none stands in for a person's

        # earlier findings only: which candidate the gate took or refused is the held-out split's, and stays out of it
        strategy = FailureTraceStrategy(self._proposer, earlier=tuple(self.log.findings()))
        run = tune(self.mh, self.settings.method, approve=nobody, reflector=self._proposer,
                   space=self.domain.execution().space, extra=(_OnConstraint(strategy, constraint.case_ids),),
                   carried=self.mh.unfinished())
        reasons = (run.decision or {}).get("reasons") or run.notes or ["no candidate beat the head"]
        return {"confirmation": run.confirmation, "decision": run.decision, "champion": run.champion,
                "outcome": run.outcome, "code": (run.decision or {}).get("code", CYCLE), "reason": "; ".join(reasons),
                "recovery": run.recovery, **strategy.findings}

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
