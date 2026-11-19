# SPDX-License-Identifier: Apache-2.0
"""Measured attempt costs and vector comparisons at the registered horizon."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Condition, Lock, RLock
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter

from g6.core.ledger import digest
from g6.core.model_calls import counting, note_model_call

from .graph_records import AttemptRecord, CostVector
from .records import Finite, Model, Sha256

HORIZON = 1000
SPLIT_REUSES = 5
Category = Literal["acquisition", "validation", "maintenance", "serving", "storage"]
CATEGORIES = ("acquisition", "validation", "maintenance", "serving", "storage")
Nonnegative = Annotated[Finite, Field(ge=0)]


class ResourceDelta(Model):
    """Signed differences; fractional counts describe averages, never sends."""
    llm_calls: Finite = 0
    llm_tokens_in: Finite = 0
    llm_tokens_out: Finite = 0
    cpu_s: Finite = 0
    wall_s: Finite = 0
    solver_calls: Finite = 0
    probe_runs: Finite = 0
    sandbox_runs: Finite = 0

    def __add__(self, other):
        return type(self)(**{k: getattr(self, k) + getattr(other, k) for k in ResourceDelta.model_fields})

    def scaled(self, factor):
        return type(self)(**{k: getattr(self, k) * factor for k in ResourceDelta.model_fields})

    def minus(self, other):
        return ResourceDelta(**{k: getattr(self, k) - getattr(other, k) for k in ResourceDelta.model_fields})


class Resources(ResourceDelta):
    llm_calls: Nonnegative = 0
    llm_tokens_in: Nonnegative = 0
    llm_tokens_out: Nonnegative = 0
    cpu_s: Nonnegative = 0
    wall_s: Nonnegative = 0
    solver_calls: Nonnegative = 0
    probe_runs: Nonnegative = 0
    sandbox_runs: Nonnegative = 0

    @classmethod
    def measured(cls, cost: CostVector):
        return cls(**CostVector.model_validate_json(cost.model_dump_json()).model_dump())


class CostProfile(Model):
    acquisition: Resources = Field(default_factory=Resources)
    validation: Resources = Field(default_factory=Resources)
    serving: Resources = Field(default_factory=Resources)
    maintenance_per_task: Resources = Field(default_factory=Resources)

    @property
    def fixed(self):
        return self.acquisition + self.validation

    @property
    def variable(self):
        return self.serving + self.maintenance_per_task

    def total(self, tasks=HORIZON):
        if type(tasks) is not int or tasks < 0:
            raise ValueError("task count must be a nonnegative integer")
        return self.fixed + self.variable.scaled(tasks)


class Crossing(Model):
    kind: Literal["dominates_from_zero", "break_even", "never_breaks_even", "crossover"]
    tasks: Nonnegative | None = None

    @property
    def break_even_tasks(self):
        return self.tasks if self.kind == "break_even" else None

    @property
    def crossover_tasks(self):
        return self.tasks if self.kind == "crossover" else None


def sign_case(delta_fixed: float, saving: float) -> Crossing:
    if not math.isfinite(delta_fixed) or not math.isfinite(saving):
        raise ValueError("cost differences must be finite")
    if delta_fixed <= 0 <= saving:
        return Crossing(kind="dominates_from_zero")
    if delta_fixed > 0 and saving > 0:
        return Crossing(kind="break_even", tasks=delta_fixed / saving)
    if delta_fixed >= 0 and saving <= 0:
        return Crossing(kind="never_breaks_even")
    return Crossing(kind="crossover", tasks=delta_fixed / saving)


class CostComparison(Model):
    horizon: Literal[1000] = HORIZON
    difference: ResourceDelta
    upper95: ResourceDelta
    economic: bool
    sign_cases: dict[str, Crossing]


def compare_costs(new: CostProfile, incumbent: CostProfile, *, upper95: ResourceDelta):
    """Prepare cost inputs for the gate; no admission or price conversion here.

    A vector bound must cover its point estimate in every resource dimension.
    The narrower vector rule requires every upper bound to be nonpositive.
    """
    difference = new.total().minus(incumbent.total())
    if any(getattr(upper95, k) < getattr(difference, k) for k in ResourceDelta.model_fields):
        raise ValueError("upper95 must cover the cumulative cost difference")
    fixed, saving = new.fixed.minus(incumbent.fixed), incumbent.variable.minus(new.variable)
    return CostComparison(difference=difference, upper95=upper95,
        economic=all(getattr(upper95, k) <= 0 for k in ResourceDelta.model_fields),
        sign_cases={k: sign_case(getattr(fixed, k), getattr(saving, k)) for k in ResourceDelta.model_fields})


class RegisteredPrice(Model):
    registration_sha256: Sha256
    rates: Resources

    def frontier_cost(self, resources: Resources):
        """Scalar reporting for the frontier only; never used by compare_costs."""
        return sum(getattr(self.rates, k) * getattr(resources, k) for k in ResourceDelta.model_fields)


MODEL_FIELDS = ("llm_calls", "llm_tokens_in", "llm_tokens_out")


def _id():
    return digest(uuid4().hex)


def _model_cost(cost):
    return CostVector(**{k: getattr(cost, k) for k in MODEL_FIELDS})


def _execution_cost(cost):
    return CostVector(**{k: getattr(cost, k) for k in CostVector.model_fields if k not in MODEL_FIELDS})


class SendRecord(Model):
    send_id: Sha256
    what: str
    cost: CostVector
    outcome: Literal["sent", "refused_before_send", "failed_after_send"]
    started_at: str
    ended_at: str


class ResourceEvent(Model):
    execution_id: Sha256
    event_id: Sha256
    category: Category
    cost: CostVector


class BookingReport(Model):
    attempt_id: Sha256 | None = None
    booked_send_ids: list[Sha256] = Field(default_factory=list)
    already_booked_send_ids: list[Sha256] = Field(default_factory=list)
    booked_execution_ids: list[Sha256] = Field(default_factory=list)
    already_booked_execution_ids: list[Sha256] = Field(default_factory=list)


@dataclass
class _Capture:
    ledger: object = None
    send_ids: set = field(default_factory=set)
    execution_ids: set = field(default_factory=set)
    consumed_ids: set = field(default_factory=set)
    lock: object = field(default_factory=RLock)

    def add(self, ledger, send_id):
        with self.lock:
            if self.ledger is not None and self.ledger is not ledger:
                raise ValueError("attempt spans different ledgers")
            self.ledger = ledger
            self.send_ids.add(send_id)

    def snapshot(self):
        with self.lock:
            ids, executions = sorted(self.send_ids), sorted(self.execution_ids)
            return ids, executions, self.ledger.cost_for(ids, executions) if self.ledger is not None else CostVector()

    def add_resource(self, ledger, event_id, *, consumed):
        with self.lock:
            if self.ledger is not None and self.ledger is not ledger:
                raise ValueError("attempt spans different ledgers")
            self.ledger = ledger
            self.execution_ids.add(event_id)
            if consumed:
                self.consumed_ids.add(event_id)

    def consumed_cost(self):
        with self.lock:
            return self.ledger.cost_for((), self.consumed_ids) if self.ledger is not None else CostVector()


_captures = ContextVar("distill_send_captures", default=())
_current_measurement = ContextVar("distill_measurement", default=None)


def resource_owner():
    return _current_measurement.get()


@contextmanager
def consume_resources(cost, *, execution_id=None):
    """Report a trusted probe or sandbox runner's completion before validation."""
    owner = resource_owner()
    if owner is None:
        yield
        return
    with owner.execution():
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            yield
        finally:
            measured = CostVector.model_validate_json(cost.model_dump_json()).model_dump()
            measured["wall_s"] = max(measured["wall_s"], time.perf_counter() - wall)
            measured["cpu_s"] = max(measured["cpu_s"], time.process_time() - cpu)
            owner._register_resource(execution_id or _id(), _id(), CostVector(**measured))


def scoped_caller(caller):
    """Bind graph injections to the active operation, including joined workers."""
    owner = _current_measurement.get()
    return owner.wrap(caller) if owner is not None else caller


@contextmanager
def _measurement_context(meter):
    token = _current_measurement.set(meter)
    try:
        yield
    finally:
        _current_measurement.reset(token)


@contextmanager
def capture_attempt_sends():
    capture = _Capture()
    token = _captures.set((*_captures.get(), capture))
    try:
        yield capture
    finally:
        _captures.reset(token)


def _hook_rejection(error):
    # A hook raises through this boundary before the backend send starts.
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code is note_model_call.__code__:
            return True
        tb = tb.tb_next
    return False


class _MeteredCaller:
    def __init__(self, inner, owner):
        self._inner, self._owner = inner, owner

    @property
    def __class__(self):
        # Preserve the injected caller's offline classification in synthesis.
        return self._inner.__class__

    def __getattr__(self, name):
        # Only the metered text entry point is part of the injected interface.
        raise AttributeError(name)

    def complete_with_images(self, *args, **kwargs):
        raise ValueError("distillation metering accepts text sends only")

    def complete(self, system, user, *, max_tokens=2048):
        if not isinstance(system, str) or not isinstance(user, str):
            raise ValueError("text system and user messages required")
        owner, send_id = self._owner, _id()
        started = datetime.now(timezone.utc).isoformat()
        with owner._condition:
            if owner._closed:
                owner._append_send(SendRecord(send_id=send_id, what=owner.what, cost=CostVector(),
                    outcome="refused_before_send", started_at=started,
                    ended_at=datetime.now(timezone.utc).isoformat()), late=True)
                raise ValueError("caller measurement is closed")
            owner._active += 1
        reply, outcome, sent = None, "failed_after_send", True
        boundary_calls, boundary_lock = 0, Lock()

        def before_send():
            nonlocal boundary_calls
            with boundary_lock:
                if boundary_calls:
                    raise ValueError("injected caller must make at most one send per completion")
                boundary_calls += 1

        try:
            with counting(before_call=before_send) as noted:
                try:
                    reply = self._inner.complete(system, user, max_tokens=max_tokens)
                except BaseException as error:
                    if not noted() and _hook_rejection(error):
                        sent, outcome = False, "refused_before_send"
                    raise
            outcome = "sent"
            return reply
        finally:
            try:
                # Surrogate text is charged as UTF-8 bytes with surrogatepass.
                cost = CostVector(llm_calls=int(sent),
                    llm_tokens_in=(len(str.encode(system, "utf-8", "surrogatepass")) +
                                   len(str.encode(user, "utf-8", "surrogatepass"))) if sent else 0,
                    llm_tokens_out=len(str.encode(reply, "utf-8", "surrogatepass")) if sent and isinstance(reply, str) else 0)
                owner._append_send(SendRecord(send_id=send_id, what=owner.what, cost=cost,
                    outcome=outcome, started_at=started, ended_at=datetime.now(timezone.utc).isoformat()))
            finally:
                with owner._condition:
                    owner._active -= 1
                    owner._condition.notify_all()


@dataclass
class _Measurement:
    ledger: object
    what: str
    _category: str = "acquisition"
    parent: object = None
    outcome: str = "screened"
    record: AttemptRecord | None = None
    _condition: Condition = field(default_factory=lambda: Condition(RLock()))
    _proxies: dict = field(default_factory=dict)
    _send_ids: set = field(default_factory=set)
    _execution_ids: set = field(default_factory=set)
    _consumed_ids: set = field(default_factory=set)
    _active: int = 0
    _closed: bool = False

    @property
    def category(self):
        return self._category

    @property
    def cost(self):
        return self.ledger.cost_for(self._send_ids, self._execution_ids)

    @cost.setter
    def cost(self, value):
        raise ValueError("cost assignment refused; report resources at consumption")

    def wrap(self, caller):
        if caller is None:
            return None
        with self._condition:
            if self._closed:
                raise ValueError("caller measurement is closed")
            if type(caller) is _MeteredCaller:
                if caller._owner is self:
                    return caller
                # A child operation gets its own lifetime over the raw injection.
                if caller._owner is not self.parent:
                    raise ValueError("caller belongs to another measurement")
                caller = caller._inner
            if id(caller) not in self._proxies:
                self._proxies[id(caller)] = _MeteredCaller(caller, self)
            return self._proxies[id(caller)]

    def _lineage(self):
        owner = self
        while owner is not None:
            yield owner
            owner = owner.parent

    def _append_send(self, row, *, late=False):
        with self.ledger._state:
            self.ledger._sends.append(row)
            self.ledger._send_rows[row.send_id] = row
            self.ledger._send_categories[row.send_id] = self.category
            if not late:
                for owner in self._lineage():
                    owner._send_ids.add(row.send_id)
        if not late:
            for capture in _captures.get():
                capture.add(self.ledger, row.send_id)

    @property
    def send_cost(self):
        with self.ledger._state:
            return self.ledger.cost_for(self._send_ids, ())

    def record_execution(self, execution):
        """Reference completed events; an aggregate cannot originate spending."""
        cost = _execution_cost(CostVector.model_validate_json(execution.cost.model_dump_json()))
        ids = {ident for attempt in execution.attempts for ident in attempt.execution_ids}
        with self.ledger._state:
            report = self.reference_resources(cost, ids)
            self.ledger._reference_execution(execution.run_id, ids)
            return report

    def reference_resources(self, cost, execution_ids):
        ids = set(execution_ids)
        with self.ledger._state:
            if _execution_cost(cost) != self.ledger.cost_for((), ids):
                raise ValueError("execution resources differ from resource event ids")
            self.ledger._check_category((), ids, self.category)
            for owner in self._lineage():
                owner._execution_ids.update(ids)
            return self.ledger._book_ids((), ids, self.category)

    def record_resources(self, cost, *, execution_id=None, event_id=None):
        """A consuming runner reports one completion, with stable ids for retries."""
        if execution_id is None or event_id is None:
            raise ValueError("resource completion requires execution and event ids")
        with self._condition:
            if self._closed:
                raise ValueError("resource measurement is closed")
            return self._register_resource(execution_id, event_id, cost)

    @contextmanager
    def execution(self):
        """Keep the owning scope open until a consuming runner completes."""
        with self._condition:
            if self._closed:
                raise ValueError("resource measurement is closed")
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def _register_resource(self, execution_id, event_id, cost):
        row = ResourceEvent(execution_id=execution_id, event_id=event_id,
            category=self.category, cost=_execution_cost(CostVector.model_validate_json(cost.model_dump_json())))
        with self.ledger._state:
            previous = self.ledger._resource_events.get(event_id)
            if previous is not None and previous != row:
                raise ValueError("resource event id has conflicting resources or ownership")
            if event_id in self.ledger._execution_refs:
                raise ValueError("resource event id conflicts with an execution id")
            if execution_id in self.ledger._executions or execution_id == event_id:
                raise ValueError("execution id conflicts with a resource event id")
            self.ledger._resource_events[event_id] = row
            self.ledger._executions[event_id] = row.cost
            self.ledger._execution_refs.setdefault(execution_id, set()).add(event_id)
            for owner in self._lineage():
                owner._execution_ids.add(event_id)
                if previous is None:
                    owner._consumed_ids.add(event_id)
            report = self.ledger._book_ids((), (event_id,), self.category)
        for capture in _captures.get():
            capture.add_resource(self.ledger, event_id, consumed=previous is None)
        return report

    def _close(self):
        with self._condition:
            self._closed = True
            while self._active:
                self._condition.wait()

    @contextmanager
    def scope(self, what):
        with self._condition:
            if self._closed:
                raise ValueError("caller measurement is closed")
            self._active += 1
        try:
            with self.ledger._scope(what, self.category, parent=self) as child:
                yield child
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()


class CostLedger:
    """Proxy sends and execution resources have distinct, idempotent ownership."""
    def __init__(self, *, sink=None):
        self.attempts: list[AttemptRecord] = []
        self.entries: list[tuple[str, Resources]] = []
        self.booking_reports: list[BookingReport] = []
        self.sink = sink
        self._measurement = Lock()
        self._state = RLock()
        self._sends: list[SendRecord] = []
        self._send_rows = {}
        self._send_categories = {}
        self._executions = {}
        self._resource_events = {}
        self._execution_refs = {}
        self._booked_sends, self._booked_executions = set(), set()

    @property
    def sends(self):
        with self._state:
            return tuple(self._sends)

    @property
    def resource_events(self):
        with self._state:
            return tuple(self._resource_events.values())

    def book(self, category: Category, cost: Resources, *, execution_id=None):
        """Reference consumption events; caller totals cannot create spending."""
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        if execution_id is None:
            raise ValueError("unidentified booking refused: send or execution ids required")
        TypeAdapter(Sha256).validate_python(execution_id)
        supplied = Resources.model_validate_json(cost.model_dump_json())
        if any(getattr(supplied, name) for name in MODEL_FIELDS):
            raise ValueError("model resources require proxy send ids")
        values = supplied.model_dump(exclude=set(MODEL_FIELDS))
        for name in ("solver_calls", "probe_runs", "sandbox_runs"):
            if values[name] != int(values[name]):
                raise ValueError("execution resource counts must be whole numbers")
            values[name] = int(values[name])
        measured = CostVector(**values)
        with self._state:
            if measured != self.cost_for((), (execution_id,)):
                raise ValueError("execution resources differ from resource event ids")
            return self._book_ids((), (execution_id,), category)

    def _event_ids(self, execution_ids):
        ids = set()
        for ident in execution_ids:
            if ident in self._executions:
                ids.add(ident)
            elif ident in self._execution_refs:
                ids.update(self._execution_refs[ident])
            else:
                raise ValueError("unknown execution or resource event id")
        return ids

    def _reference_execution(self, ident, ids):
        TypeAdapter(Sha256).validate_python(ident)
        events = self._event_ids(ids)
        if ident in self._executions or (ident in self._execution_refs and self._execution_refs[ident] != events):
            raise ValueError("execution id has conflicting resource event ids")
        self._execution_refs[ident] = events

    def _check_category(self, send_ids, execution_ids, category):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        if any(self._send_categories[ident] != category for ident in send_ids) or any(
                self._resource_events[ident].category != category for ident in self._event_ids(execution_ids)):
            raise ValueError("booking category conflicts with resource ownership")

    def cost_for(self, send_ids, execution_ids):
        with self._state:
            total = CostVector()
            for ident in set(send_ids):
                if ident not in self._send_rows:
                    raise ValueError("unknown send id")
                total = total + self._send_rows[ident].cost
            for ident in self._event_ids(execution_ids):
                total = total + self._executions[ident]
            return total

    def _book_ids(self, send_ids, execution_ids, category, attempt_id=None):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        with self._state:
            sends, executions = set(send_ids), self._event_ids(execution_ids)
            if sends - self._send_rows.keys() or executions - self._executions.keys():
                raise ValueError("unknown send or execution id")
            self._check_category(sends, executions, category)
            fresh_sends, fresh_executions = sends - self._booked_sends, executions - self._booked_executions
            cost = self.cost_for(fresh_sends, fresh_executions)
            report = BookingReport(attempt_id=attempt_id, booked_send_ids=sorted(fresh_sends),
                already_booked_send_ids=sorted(sends & self._booked_sends),
                booked_execution_ids=sorted(fresh_executions),
                already_booked_execution_ids=sorted(executions & self._booked_executions))
            self._booked_sends.update(fresh_sends)
            self._booked_executions.update(fresh_executions)
            if fresh_sends or fresh_executions:
                self.entries.append((category, Resources.measured(cost)))
            self.booking_reports.append(report)
            return report

    def book_attempt(self, record: AttemptRecord, *, category: Category = "acquisition"):
        return self._book_attempt(record, category=category)

    def _book_attempt(self, record, *, category):
        row = AttemptRecord.model_validate_json(record.model_dump_json())
        with self._state:
            if _model_cost(row.cost) != self.cost_for(row.send_ids, ()):
                raise ValueError("unmetered_send: attempt model usage differs from proxy send ids")
            if _execution_cost(row.cost) != self.cost_for((), row.execution_ids):
                raise ValueError("attempt resources differ from execution or resource event ids")
            report = self._book_ids(row.send_ids, row.execution_ids, category, row.attempt_id)
            fresh = all(a.attempt_id != row.attempt_id for a in self.attempts)
            if fresh:
                self.attempts.append(row)
        if fresh and self.sink is not None:
            self.sink(row, category)
        return report

    @contextmanager
    def _scope(self, what, category, parent=None):
        meter = _Measurement(self, what, category, parent)
        started = datetime.now(timezone.utc).isoformat()
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            # Observer propagation lets the cross-check cover joined workers too.
            with _measurement_context(meter), counting(before_call=lambda: None) as noted:
                yield meter
        except BaseException:
            meter.outcome = "failed"
            raise
        finally:
            meter._close()
            # Completed child events cover their intervals; book the remaining floor.
            covered = self.cost_for((), meter._consumed_ids)
            meter._register_resource(_id(), _id(), CostVector(
                wall_s=max(0, time.perf_counter() - wall - covered.wall_s),
                cpu_s=max(0, time.process_time() - cpu - covered.cpu_s)))
            cost = self.cost_for(meter._send_ids, meter._execution_ids)
            unmetered = noted() > cost.llm_calls
            if unmetered:
                meter.outcome = "unmetered_send"
            meter.record = AttemptRecord(what=what, cost=cost, model_sends=cost.llm_calls,
                send_ids=sorted(meter._send_ids), execution_ids=sorted(meter._execution_ids),
                outcome=meter.outcome, update_id=None, started_at=started,
                ended_at=datetime.now(timezone.utc).isoformat())
            if parent is None:
                self._book_attempt(meter.record, category=category)
            if unmetered:
                raise ValueError("unmetered_send: hooks exceed proxy sends")

    @contextmanager
    def measure(self, what: str, *, category: Category = "acquisition"):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        if not self._measurement.acquire(blocking=False):
            raise ValueError("nested or active measurement on this ledger")
        try:
            with self._scope(what, category) as meter:
                yield meter
        finally:
            self._measurement.release()

    def total(self, category: Category):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        total = Resources()
        with self._state:
            entries = tuple(self.entries)
        for kind, cost in entries:
            if kind == category:
                total = total + cost
        return total

    @staticmethod
    def estimate_validation(repeats, *, probes, split_cost: Resources):
        """Return a horizon estimate, separate from measured spending."""
        groups = [tuple(group) for group in repeats]
        if any(len(group) != 3 for group in groups):
            raise ValueError("validation requires three repeats per case")
        total = split_cost.scaled(1 / SPLIT_REUSES)
        for cost in [*(cost for group in groups for cost in group), *probes]:
            total = total + Resources.measured(cost)
        return total

    def book_validation(self, repeats, *, probes, split_cost: Resources):
        raise ValueError("validation booking requires execution ids; use estimate_validation for estimates")

    def profile(self, *, deployment_tasks: int):
        if type(deployment_tasks) is not int or deployment_tasks <= 0:
            raise ValueError("positive deployment task count required for amortisation")
        return CostProfile(acquisition=self.total("acquisition"), validation=self.total("validation"),
            serving=self.total("serving").scaled(1 / deployment_tasks),
            maintenance_per_task=self.total("maintenance").scaled(1 / deployment_tasks))
