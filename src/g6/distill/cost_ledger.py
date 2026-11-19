# SPDX-License-Identifier: Apache-2.0
"""Measured attempt costs and vector comparisons at the registered horizon."""
from __future__ import annotations

import math
import os
import sys
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Condition, Lock, RLock, get_ident
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, TypeAdapter, model_validator

from g6.core.ledger import digest
from g6.core.model_calls import counting, note_model_call

from .resource_math import (CANONICAL_FIELDS, RESOURCE_FIELDS, HorizonResources, ResourceDelta, Resources)
from .graph_records import AttemptRecord, CostVector
from .records import Finite, Model, Sha256

HORIZON = 1000
SPLIT_REUSES = 5
Category = Literal["acquisition", "validation", "maintenance", "serving", "storage"]
CATEGORIES = ("acquisition", "validation", "maintenance", "serving", "storage")
Nonnegative = Annotated[Finite, Field(ge=0)]




class CostProfile(Model):
    acquisition: Resources = Field(default_factory=Resources)
    validation: Resources = Field(default_factory=Resources)
    serving: Resources = Field(default_factory=Resources)
    maintenance_per_task: Resources = Field(default_factory=Resources)
    resource_gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def collect_gaps(self):
        gaps = set(self.resource_gaps)
        for name in ("acquisition", "validation", "serving", "maintenance_per_task"):
            gaps.update(getattr(self, name).resource_gaps)
        object.__setattr__(self, "resource_gaps", sorted(gaps))
        return self

    @property
    def fixed(self):
        return self.acquisition + self.validation

    @property
    def variable(self):
        return self.serving + self.maintenance_per_task

    def total(self, tasks=HORIZON):
        if type(tasks) is not int or tasks < 0:
            raise ValueError("task count must be a nonnegative integer")
        total = self.fixed + self.variable.scaled(tasks)
        return HorizonResources.model_validate(total.model_dump() | {"resource_gaps": sorted(set(total.resource_gaps + self.resource_gaps))})


class Crossing(Model):
    kind: Literal["dominates_from_zero", "break_even", "never_breaks_even", "crossover", "indeterminate"]
    tasks: Nonnegative | None = None

    @property
    def break_even_tasks(self):
        return self.tasks if self.kind == "break_even" else None

    @property
    def crossover_tasks(self):
        return self.tasks if self.kind == "crossover" else None


def sign_case(delta_fixed: float | tuple, saving: float | tuple) -> Crossing:
    if isinstance(delta_fixed, tuple) or isinstance(saving, tuple):
        fixed = delta_fixed if isinstance(delta_fixed, tuple) else (delta_fixed, delta_fixed)
        variable = saving if isinstance(saving, tuple) else (saving, saving)
        if len(fixed) != 2 or len(variable) != 2 or fixed[0] > fixed[1] or variable[0] > variable[1]:
            raise ValueError("ordered lower and upper resource bounds required")
        cases = [sign_case(a, b) for a in fixed for b in variable]
        return cases[0] if all(case == cases[0] for case in cases) else Crossing(kind="indeterminate")
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
    resource_gaps: list[str] = Field(default_factory=list)


def compare_costs(new: CostProfile, incumbent: CostProfile, *, upper95: ResourceDelta):
    """Prepare cost inputs for the gate; no admission or price conversion here.

    A vector bound must cover its point estimate in every resource dimension.
    The narrower vector rule requires every upper bound to be nonpositive.
    """
    difference = new.total().minus(incumbent.total())
    if any(upper95.canonical_upper[k] < difference.canonical_upper[k] for k in CANONICAL_FIELDS):
        raise ValueError("upper95 must cover the cumulative cost difference")
    fixed, saving = new.fixed.minus(incumbent.fixed), incumbent.variable.minus(new.variable)
    gaps = sorted(set(difference.resource_gaps + new.resource_gaps + incumbent.resource_gaps))
    signs = {}
    for key in RESOURCE_FIELDS:
        f, v = fixed.interval(key), saving.interval(key)
        cases = [sign_case(a, b) for a in f for b in v]
        uncertain = f[0] != f[1] or v[0] != v[1]
        same_kind = len({case.kind for case in cases}) == 1
        # Unknown CPU at zero cannot establish dominance or a crossing.
        signs[key] = (Crossing(kind="indeterminate") if key == "cpu_s" and gaps or uncertain and
                      (not same_kind or len({case.tasks for case in cases}) != 1) else cases[0])
    return CostComparison(difference=difference, upper95=upper95, resource_gaps=gaps,
        economic=not gaps and all(upper95.canonical_upper[k] <= 0 for k in CANONICAL_FIELDS), sign_cases=signs)



class RegisteredPrice(Model):
    registration_sha256: Sha256
    rates: Resources

    def frontier_cost(self, resources: Resources):
        """Scalar reporting for the frontier only; never used by compare_costs."""
        return sum(getattr(self.rates, k) * resources.interval(k)[1] for k in RESOURCE_FIELDS)


MODEL_FIELDS = ("llm_calls", "llm_tokens_in", "llm_tokens_out")


def _id():
    return digest(uuid4().hex)


def _select_cost(cost, names):
    names = set(names)
    lower = {k: v for k, v in cost.canonical_lower.items()
             if k in names or k.replace("_ns", "_s") in names}
    upper = {k: v for k, v in cost.canonical_upper.items() if k in lower}
    return CostVector.from_bounds(lower, upper, cost.resource_gaps if "cpu_s" in names else ())


def _model_cost(cost):
    return _select_cost(cost, MODEL_FIELDS)


def _execution_cost(cost):
    return _select_cost(cost, set(RESOURCE_FIELDS) - set(MODEL_FIELDS))


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
    operation_id: Sha256 | None = None
    time_booking: Literal["operation", "diagnostic"] = "diagnostic"
    diagnostic_cost: CostVector = Field(default_factory=CostVector)
    diagnostic_errors: list[str] = Field(default_factory=list)
    child_event_ids: list[Sha256] = Field(default_factory=list)
    child_cpu_status: Literal["not_applicable", "measured", "unmeasured"] = "not_applicable"
    child_cpu_s: Nonnegative | None = None
    child_wall_s: Nonnegative | None = None

    @model_validator(mode="after")
    def child_reading(self):
        if self.child_cpu_status == "not_applicable":
            if self.child_cpu_s is not None or self.child_wall_s is not None:
                raise ValueError("non-child event cannot carry child usage")
        elif self.child_wall_s is None or (self.child_cpu_s is not None) != (self.child_cpu_status == "measured"):
            raise ValueError("child usage must state measured CPU or an explicit gap")
        if len(self.child_event_ids) != len(set(self.child_event_ids)) or self.event_id in self.child_event_ids:
            raise ValueError("child event ids must be distinct from their parent")
        return self


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
                return
            self.ledger = ledger
            self.send_ids.add(send_id)

    def snapshot(self):
        with self.lock:
            ids, executions = sorted(self.send_ids), sorted(self.execution_ids)
            return ids, executions, self.ledger.cost_for(ids, executions) if self.ledger is not None else CostVector()

    def add_resource(self, ledger, event_id, *, consumed):
        with self.lock:
            if self.ledger is not None and self.ledger is not ledger:
                return
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


class _DiagnosticStacks:
    _stack = ContextVar("distill_diagnostic_stack", default=())

    @property
    def stack(self):
        return tuple(m for m in self._stack.get() if m.active)

    @stack.setter
    def stack(self, value):
        self._stack.set(tuple(value))


_meters = _DiagnosticStacks()
_operation_lock = Lock()
_process_operation = None
_spawn_observer_installed = False


@dataclass
class _Operation:
    owner: object
    event_id: str = field(default_factory=_id)
    thread_id: int = field(default_factory=get_ident)
    users: int = 0
    child_seen: bool = False
    gaps: list = field(default_factory=list)
    closed: bool = False
    cost: CostVector | None = None
    state: object = field(default_factory=RLock)

    def sample_cpu(self):
        try:
            return time.process_time_ns()
        except BaseException:
            self.gaps.append("process CPU sample unavailable")
            return None

    def sample_wall(self):
        readings = []
        for clock in (time.perf_counter_ns, time.monotonic_ns):
            try:
                readings.append(clock())
            except BaseException:
                self.gaps.append("monotonic sample unavailable")
                readings.append(None)
        return tuple(readings)


def _observe_spawn(event, args):
    operation = _process_operation
    if operation is not None and event in {"subprocess.Popen", "os.system", "os.fork", "os.forkpty",
                                          "os.posix_spawn", "_winapi.CreateProcess"}:
        operation.child_seen = True


def _install_spawn_observer():
    global _spawn_observer_installed
    if not _spawn_observer_installed:
        sys.addaudithook(_observe_spawn)
        from multiprocessing.process import BaseProcess
        started = BaseProcess.start

        def start(process):
            if _process_operation is not None:
                _process_operation.child_seen = True
            return started(process)

        BaseProcess.start = start
        _spawn_observer_installed = True


@contextmanager
def _operation_scope(owner):
    global _process_operation
    operation = getattr(owner.parent or resource_owner(), "operation", None)
    if operation is not None and operation.closed:
        raise ValueError("originating resource operation is closed")
    # Sibling asyncio scopes on the owning thread share the process operation.
    if operation is None and _process_operation is not None and _process_operation.thread_id == get_ident():
        operation = _process_operation
    outer = operation is None
    if outer:
        operation = _Operation(owner)
        _operation_lock.acquire()
        _process_operation = operation
        try:
            operation.wall_started = operation.sample_wall()
            operation.cpu_started = operation.sample_cpu()
        except BaseException:
            _process_operation = None
            _operation_lock.release()
            raise
    owner.operation = operation
    if operation.owner.ledger is not owner.ledger:
        owner.ledger._shared_operations[operation.event_id] = (owner.category, operation)
    with operation.state:
        if operation.closed:
            raise ValueError("originating resource operation is closed")
        operation.users += 1
    try:
        if outer:
            _install_spawn_observer()
            from multiprocessing import active_children
            if active_children():
                operation.child_seen = True
        yield operation
    finally:
        with operation.state:
            operation.users -= 1
            closing = operation.users == 0 and not operation.closed
            if closing:
                operation.closed = True
        if closing:
            try:
                wall_ended = operation.sample_wall()
                cpu_ended = operation.sample_cpu()
                pairs = [(a, b) for a, b in zip(operation.wall_started, wall_ended)
                         if a is not None and b is not None]
                if not pairs:
                    operation.owner.ledger._incomplete_operations[operation.event_id] = operation.owner.category
                    raise ValueError("operation has no common monotonic clock readings")
                wall = max(0, pairs[0][1] - pairs[0][0])
                cpu = (max(0, cpu_ended - operation.cpu_started)
                       if cpu_ended is not None and operation.cpu_started is not None else 0)
                upper = cpu
                if operation.child_seen or operation.gaps:
                    upper += wall * (os.cpu_count() or 1)
                gaps = list(operation.gaps)
                if operation.child_seen:
                    gaps.append(f"child tree CPU unmeasured: operation {operation.event_id}")
                cost = CostVector(cpu_ns=cpu, wall_ns=wall, upper={"cpu_ns": upper}, resource_gaps=gaps)
                operation.cost = cost
            finally:
                operation.closed = True
                _process_operation = None
                _operation_lock.release()
            root = operation.owner
            root._register_resource(_id(), operation.event_id, cost,
                operation_id=operation.event_id, time_booking="operation")
            if root.record is not None:
                root.ledger.book_attempt(AttemptRecord(what="operation_completion:" + root.what,
                    operation_id=operation.event_id, time_booking="operation", cost=cost,
                    model_sends=0, execution_ids=[operation.event_id], resource_gaps=cost.resource_gaps,
                    outcome="screened", update_id=None, started_at="operation_completion",
                    ended_at=datetime.now(timezone.utc).isoformat()), category=root.category)


@dataclass
class _ExclusiveMeter:
    """A diagnostic scope; its timing never creates a booked duration."""
    cost: CostVector
    child_process: bool = False
    cpu_s: float = 0
    wall_s: float = 0
    cpu_ns: int = 0
    wall_ns: int = 0
    child_cpu_s: float | None = None
    child_wall_s: float | None = None
    child_cpu_ns: int | None = None
    child_event_ids: list = field(default_factory=list)
    event_id: str | None = None
    descendant_cost: CostVector = field(default_factory=CostVector)
    resource_gaps: list[str] = field(default_factory=list)
    diagnostic_errors: list[str] = field(default_factory=list)
    active: bool = True
    operation_id: str | None = None
    time_booking: str = "diagnostic"

    def resume(self):
        self._wall, self._cpu = time.perf_counter_ns(), time.thread_time_ns()
        self._process_cpu = time.process_time_ns()

    def pause(self):
        wall, cpu = time.perf_counter_ns(), time.thread_time_ns()
        self.wall_ns = max(0, wall - self._wall)
        self.cpu_ns = max(0, cpu - self._cpu)
        self.wall_s = self.wall_ns / 1_000_000_000
        self.cpu_s = self.cpu_ns / 1_000_000_000
        self.process_cpu_ns = max(0, time.process_time_ns() - self._process_cpu)

    def report_child(self, *, cpu_s, wall_s):
        if not self.child_process:
            raise ValueError("child resources require a child-process boundary")
        reading = Resources(cpu_s=cpu_s, wall_s=wall_s)
        if self.child_cpu_s is not None:
            raise ValueError("child resources already reported")
        self.child_cpu_s, self.child_wall_s = reading.cpu_s, reading.wall_s
        self.child_cpu_ns = reading.cpu_ns
        # A direct reading cannot describe the CPU of descendants.


def report_child_resources(*, cpu_s, wall_s):
    """Record a direct-child diagnostic, leaving whole-tree coverage open."""
    stack = _meters.stack
    if not stack:
        raise ValueError("child resources require an active consuming boundary")
    stack[-1].report_child(cpu_s=cpu_s, wall_s=wall_s)


@contextmanager
def _exclusive_resources(owner, cost, *, execution_id=None, child_process=False, worker=False):
    stack = _meters.stack
    parent = stack[-1] if stack else None
    meter = _ExclusiveMeter(cost, child_process)
    meter.ledger = owner.ledger if owner is not None else None
    operation = getattr(owner, "operation", None)
    meter.operation_id = operation.event_id if operation is not None else None
    if child_process and operation is not None:
        operation.child_seen = True
    _meters.stack = (*stack, meter)
    try:
        try:
            meter.resume()
        except Exception as error:
            meter.diagnostic_errors.append(type(error).__name__)
        yield meter
    finally:
        try:
            try:
                meter.pause()
            except Exception as error:
                meter.diagnostic_errors.append(type(error).__name__)
        finally:
            meter.active = False
            _meters.stack = tuple(m for m in _meters.stack if m is not meter)
            diagnostic = _execution_cost(CostVector.model_validate_json(meter.cost.model_dump_json()))
            lower, upper = diagnostic.canonical_lower, diagnostic.canonical_upper
            lower["cpu_ns"] = max(lower["cpu_ns"], meter.cpu_ns) + (meter.child_cpu_ns or 0)
            upper["cpu_ns"] = max(upper["cpu_ns"], meter.cpu_ns) + (meter.child_cpu_ns or 0)
            lower["wall_ns"] = max(lower["wall_ns"], meter.wall_ns)
            upper["wall_ns"] = max(upper["wall_ns"], meter.wall_ns)
            diagnostic = CostVector.from_bounds(lower, upper, diagnostic.resource_gaps)
            # Diagnostic slices have zero booked time and conservative local upper bounds.
            lower = diagnostic._counts_only().canonical_lower
            upper = diagnostic._counts_only().canonical_upper
            upper["wall_ns"] = diagnostic.canonical_upper["wall_ns"]
            upper["cpu_ns"] = max(diagnostic.canonical_upper["cpu_ns"], getattr(meter, "process_cpu_ns", 0))
            if child_process or operation is not None and operation.child_seen:
                upper["cpu_ns"] += diagnostic.wall_ns * (os.cpu_count() or 1)
            meter.cost = CostVector.from_bounds(lower, upper)
            meter.event_id = _id()
            if child_process or operation is not None and operation.child_seen:
                meter.resource_gaps.append(f"child CPU unmeasured: event {meter.event_id}")
                meter.cost = meter.cost.model_copy(update={"resource_gaps": list(meter.resource_gaps)})
            if parent is not None:
                parent.descendant_cost = parent.descendant_cost + meter.cost + meter.descendant_cost
                parent.resource_gaps.extend(meter.resource_gaps)
            if owner is not None:
                owner._register_resource(execution_id or _id(), meter.event_id, meter.cost,
                    operation_id=operation.event_id if operation is not None else None,
                    diagnostic_cost=diagnostic, diagnostic_errors=meter.diagnostic_errors,
                    child_event_ids=meter.child_event_ids,
                    child_cpu_status=("measured" if meter.child_cpu_s is not None else "unmeasured")
                        if child_process else "not_applicable",
                    child_cpu_s=meter.child_cpu_s,
                    child_wall_s=(meter.child_wall_s if meter.child_wall_s is not None else meter.wall_s)
                        if child_process else None)
                if parent is not None and meter.event_id in owner.ledger._resource_events:
                    # Another ledger's ids never enter this ledger's event tree.
                    if getattr(parent, "ledger", None) is owner.ledger:
                        parent.child_event_ids.append(meter.event_id)
                if worker and owner._closed:
                    owner.ledger.book_attempt(AttemptRecord(what=f"worker:{owner.what}",
                        operation_id=operation.event_id if operation is not None else None,
                        cost=meter.cost, model_sends=0, execution_ids=[meter.event_id],
                        resource_gaps=owner.ledger.gaps_for([meter.event_id]), outcome="screened",
                        update_id=None, started_at="worker", ended_at=datetime.now(timezone.utc).isoformat()),
                        category=owner.category)
            meter.ledger = owner.ledger if owner is not None else None


@contextmanager
def consume_resources(cost, *, execution_id=None, child_process=False):
    """Report a trusted probe or sandbox runner's completion before validation."""
    owner = resource_owner()
    if owner is None:
        ledger, meter = CostLedger(), None
        ledger._track_model_sends = False
        try:
            with ledger.measure("standalone_runner", category="serving") as standalone:
                with consume_resources(cost, execution_id=execution_id, child_process=child_process) as meter:
                    yield meter
        finally:
            if meter is not None:
                meter.resource_gaps = ledger.gaps_for(ledger._booked_executions)
                if standalone.operation.cost is not None:
                    meter.cost = meter.cost._counts_only() + standalone.operation.cost
                    meter.descendant_cost = meter.descendant_cost._counts_only()
                    meter.time_booking = "operation"
        return
    with owner.execution():
        with _exclusive_resources(owner, cost, execution_id=execution_id, child_process=child_process) as meter:
            from g6.core import component_process
            observer = getattr(component_process, "observe_child_usage", None)
            with (observer(meter.report_child) if child_process and observer is not None else nullcontext()):
                yield meter


_worker_hooks = Lock()
_workers_carried = False


def _run_resource_worker(owner, fn, *args, **kwargs):
    with _measurement_context(owner), _exclusive_resources(owner, CostVector(), worker=True):
        return fn(*args, **kwargs)


def _carry_resource_workers():
    """Meter worker units whose contexts the model-send boundary carries."""
    global _workers_carried
    import threading
    from concurrent.futures import ThreadPoolExecutor

    with _worker_hooks:
        if _workers_carried:
            return
        started, submitted = threading.Thread.start, ThreadPoolExecutor.submit

        def submit(pool, fn, /, *args, **kwargs):
            owner = resource_owner()
            if owner is None:
                return submitted(pool, fn, *args, **kwargs)
            return submitted(pool, _run_resource_worker, owner, fn, *args, **kwargs)

        def start(thread):
            owner = resource_owner()
            if owner is None:
                return started(thread)
            run, own = thread.run, "run" in vars(thread)

            def run_metered():
                try:
                    _run_resource_worker(owner, run)
                finally:
                    if own:
                        thread.run = run
                    else:
                        vars(thread).pop("run", None)

            thread.run = run_metered
            try:
                return started(thread)
            except BaseException:
                if own:
                    thread.run = run
                else:
                    vars(thread).pop("run", None)
                raise

        threading.Thread.start, ThreadPoolExecutor.submit = start, submit
        _workers_carried = True


def scoped_caller(caller):
    """Bind graph injections to the active operation, including joined workers."""
    owner = _current_measurement.get()
    return owner.wrap(caller) if owner is not None and owner.ledger._track_model_sends else caller


@contextmanager
def _measurement_context(meter):
    token = _current_measurement.set(meter)
    try:
        yield
    finally:
        _current_measurement.reset(token)


@contextmanager
def capture_attempt_sends():
    owner = resource_owner()
    capture = _Capture(ledger=owner.ledger if owner is not None else None)
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
    operation: object = None
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
        ids.update(execution.referenced_execution_ids)
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

    def record_resources(self, cost, *, execution_id=None, event_id=None, **metadata):
        """A consuming runner reports one completion, with stable ids for retries."""
        if execution_id is None or event_id is None:
            raise ValueError("resource completion requires execution and event ids")
        with self._condition:
            if self._closed:
                raise ValueError("resource measurement is closed")
            previous = self.ledger._resource_events.get(event_id)
            if previous is not None and not metadata:
                metadata = previous.model_dump(exclude={"execution_id", "event_id", "category", "cost"})
            return self._register_resource(execution_id, event_id, cost, **metadata)

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

    def _register_resource(self, execution_id, event_id, cost, **metadata):
        if self.operation is not None:
            metadata.setdefault("operation_id", self.operation.event_id)
        row = ResourceEvent(execution_id=execution_id, event_id=event_id,
            category=self.category, cost=_execution_cost(CostVector.model_validate_json(cost.model_dump_json())),
            **metadata)
        with self.ledger._state:
            previous = self.ledger._resource_events.get(event_id)
            if previous is not None and previous != row:
                raise ValueError("resource event id has conflicting resources or ownership")
            if event_id in self.ledger._execution_refs:
                raise ValueError("resource event id conflicts with an execution id")
            if execution_id in self.ledger._executions or execution_id == event_id:
                raise ValueError("execution id conflicts with a resource event id")
            if set(row.child_event_ids) - self.ledger._resource_events.keys():
                raise ValueError("unknown child resource event id")
            self.ledger._check_category((), row.child_event_ids, self.category)
            self.ledger._resource_events[event_id] = row
            self.ledger._executions[event_id] = row.cost
            children, pending = set(), list(row.child_event_ids)
            while pending:
                ident = pending.pop()
                if ident not in children:
                    children.add(ident)
                    pending.extend(self.ledger._resource_events[ident].child_event_ids)
            self.ledger._execution_refs.setdefault(execution_id, set()).update({event_id, *children})
            for owner in self._lineage():
                if owner.record is None:
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
        self._track_model_sends = True
        self._shared_operations = {}
        self._incomplete_operations = {}
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
            for ident in sorted(set(send_ids)):
                if ident not in self._send_rows:
                    raise ValueError("unknown send id")
                total = total + self._send_rows[ident].cost
            events = self._event_ids(execution_ids)
            for ident in sorted(events):
                total = total + self._executions[ident]
            return total.model_copy(update={"resource_gaps": sorted(set(total.resource_gaps + self.gaps_for(events)))})

    def booked_cost_for(self, send_ids, execution_ids):
        with self._state:
            total = self.cost_for(send_ids, ())
            events = self._event_ids(execution_ids)
            for ident in sorted(events):
                row = self._resource_events[ident]
                total = total + (row.cost if row.time_booking == "operation" else row.cost._counts_only())
            return total.model_copy(update={"resource_gaps": sorted(set(total.resource_gaps + self.gaps_for(events)))})

    def gaps_for(self, execution_ids):
        with self._state:
            events = self._event_ids(execution_ids)
            gaps = {gap for ident in events for gap in self._resource_events[ident].cost.resource_gaps}
            gaps.update(f"incomplete operation {self._resource_events[ident].operation_id}" for ident in events
                        if self._resource_events[ident].operation_id in self._incomplete_operations)
            return sorted(gaps)


    def _book_ids(self, send_ids, execution_ids, category, attempt_id=None):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        with self._state:
            sends, executions = set(send_ids), self._event_ids(execution_ids)
            if sends - self._send_rows.keys() or executions - self._executions.keys():
                raise ValueError("unknown send or execution id")
            self._check_category(sends, executions, category)
            fresh_sends, fresh_executions = sends - self._booked_sends, executions - self._booked_executions
            cost = self.booked_cost_for(fresh_sends, fresh_executions)
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
            expected = (self.booked_cost_for((), row.execution_ids) if row.time_booking == "operation"
                        else self.cost_for((), row.execution_ids))
            if _execution_cost(row.cost) != expected:
                raise ValueError("attempt resources differ from execution or resource event ids")
            if row.resource_gaps != self.gaps_for((*row.execution_ids, *row.referenced_execution_ids)):
                raise ValueError("attempt resource gaps differ from resource event ids")
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
        def noted():
            return 0
        started = datetime.now(timezone.utc).isoformat()
        try:
            # Observer propagation lets the cross-check cover joined workers too.
            with _operation_scope(meter), _measurement_context(meter), counting(before_call=lambda: None) as noted:
                _carry_resource_workers()
                with _exclusive_resources(meter, CostVector()):
                    try:
                        yield meter
                    finally:
                        meter._close()
        except BaseException:
            meter.outcome = "failed"
            raise
        finally:
            with self._state:
                cost = (self.booked_cost_for(meter._send_ids, meter._execution_ids)
                        if meter.operation is not None and meter.operation.owner is meter
                        else self.cost_for(meter._send_ids, meter._execution_ids))
                unmetered = self._track_model_sends and noted() > cost.llm_calls
                if unmetered:
                    meter.outcome = "unmetered_send"
                meter.record = AttemptRecord(what=what, cost=cost, model_sends=cost.llm_calls,
                    operation_id=meter.operation.event_id if meter.operation is not None else None,
                    time_booking="operation" if meter.operation is not None and meter.operation.owner is meter
                                 else "shared_operation",
                    send_ids=sorted(meter._send_ids), execution_ids=sorted(meter._execution_ids),
                    resource_gaps=self.gaps_for(meter._execution_ids),
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
        with self._state:
            if category in self._incomplete_operations.values():
                raise ValueError("incomplete operation has no wall interval; estimates refused")
            sends = [ident for ident in self._booked_sends if self._send_categories[ident] == category]
            events = [ident for ident in self._booked_executions
                      if self._resource_events[ident].category == category]
            total = Resources.measured(self.booked_cost_for(sends, events))
            for ident, (owned_category, operation) in self._shared_operations.items():
                if owned_category != category:
                    continue
                if operation.cost is None:
                    if operation.closed:
                        raise ValueError("incomplete operation has no wall interval; estimates refused")
                    start = operation.wall_started[1]
                    wall = max(0, time.monotonic_ns() - start) if start is not None else 0
                    upper = {"cpu_ns": wall * (os.cpu_count() or 1), "wall_ns": wall}
                else:
                    upper = {k: operation.cost.canonical_upper[k] for k in ("cpu_ns", "wall_ns")}
                total = total + Resources(upper=upper,
                    resource_gaps=[f"time booked in shared operation {ident}"])
            return total

    @staticmethod
    def estimate_validation(repeats, *, probes, split_cost: Resources):
        """Return a horizon estimate, separate from measured spending."""
        groups = [tuple(group) for group in repeats]
        if any(len(group) != 3 for group in groups):
            raise ValueError("validation requires three repeats per case")
        total = split_cost.divided(SPLIT_REUSES)
        for cost in [*(cost for group in groups for cost in group), *probes]:
            total = total + Resources.measured(cost)
        return total

    def book_validation(self, repeats, *, probes, split_cost: Resources):
        raise ValueError("validation booking requires execution ids; use estimate_validation for estimates")

    def profile(self, *, deployment_tasks: int):
        if type(deployment_tasks) is not int or deployment_tasks <= 0:
            raise ValueError("positive deployment task count required for amortisation")
        return CostProfile(acquisition=self.total("acquisition"), validation=self.total("validation"),
            serving=self.total("serving").divided(deployment_tasks),
            maintenance_per_task=self.total("maintenance").divided(deployment_tasks),
            resource_gaps=self.gaps_for(self._booked_executions))
