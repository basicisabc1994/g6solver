# SPDX-License-Identifier: Apache-2.0
"""Measured attempt costs and vector comparisons at the registered horizon."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Condition, Lock, RLock
from typing import Annotated, Literal

from pydantic import Field

from g6.core.model_calls import counting

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


class SendRecord(Model):
    what: str
    cost: CostVector
    outcome: Literal["completed", "refused", "exception"]
    started_at: str
    ended_at: str


class _MeteredCaller:
    def __init__(self, inner, owner):
        self._inner, self._owner = inner, owner

    @property
    def __class__(self):
        # Preserve the injected caller's offline classification in synthesis.
        return self._inner.__class__

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete_with_images(self, *args, **kwargs):
        raise ValueError("distillation metering accepts text sends only")

    def complete(self, system, user, *, max_tokens=2048):
        if not isinstance(system, str) or not isinstance(user, str):
            raise ValueError("text system and user messages required")
        owner = self._owner
        with owner._condition:
            if owner._closed:
                raise ValueError("caller measurement is closed")
            owner._active += 1
        wall, cpu = time.perf_counter(), time.process_time()
        started = datetime.now(timezone.utc).isoformat()
        reply, outcome = None, "exception"
        boundary_calls, boundary_lock = 0, Lock()

        def before_send():
            nonlocal boundary_calls
            with boundary_lock:
                if boundary_calls:
                    raise ValueError("injected caller must make at most one send per completion")
                boundary_calls += 1

        try:
            with counting(before_call=before_send) as noted:
                reply = self._inner.complete(system, user, max_tokens=max_tokens)
            outcome = "refused" if isinstance(reply, dict) and reply.get("ok") is False else "completed"
            return reply
        finally:
            # UTF-8 bytes are conservative token units, matching graph execution.
            # A caller without a hook still crossed this injected send boundary.
            calls = max(1, noted())
            cost = CostVector(llm_calls=calls,
                llm_tokens_in=(len(system.encode("utf-8")) + len(user.encode("utf-8"))) * calls,
                llm_tokens_out=len(reply.encode("utf-8")) if isinstance(reply, str) else 0,
                wall_s=time.perf_counter() - wall, cpu_s=time.process_time() - cpu)
            row = SendRecord(what=owner.what, cost=cost, outcome=outcome, started_at=started,
                ended_at=datetime.now(timezone.utc).isoformat())
            with owner._condition:
                owner._sent = owner._sent + cost
                with owner.ledger._state:
                    owner.ledger._sends.append(row)
                owner._active -= 1
                owner._condition.notify_all()


@dataclass
class _Measurement:
    ledger: object
    what: str
    cost: CostVector = field(default_factory=CostVector)
    outcome: str = "screened"
    record: AttemptRecord | None = None
    _condition: Condition = field(default_factory=Condition)
    _sent: CostVector = field(default_factory=CostVector)
    _proxies: dict = field(default_factory=dict)
    _active: int = 0
    _closed: bool = False

    def wrap(self, caller):
        if caller is None:
            return None
        with self._condition:
            if self._closed:
                raise ValueError("caller measurement is closed")
            if type(caller) is _MeteredCaller:
                if caller._owner is not self:
                    raise ValueError("caller belongs to another measurement")
                return caller
            if id(caller) not in self._proxies:
                self._proxies[id(caller)] = _MeteredCaller(caller, self)
            return self._proxies[id(caller)]

    @property
    def send_cost(self):
        with self._condition:
            return self._sent

    def _close(self):
        with self._condition:
            self._closed = True
            while self._active:
                self._condition.wait()


class CostLedger:
    """One outer measurement per operation; inner sends join that measurement.

    Retain this ledger for failed and discarded proposals too. Callers may persist
    the typed attempts through their own ledger sink. Never book a nested meter
    as well as its parent: that would charge the same sends twice.
    """
    def __init__(self, *, sink=None):
        self.attempts: list[AttemptRecord] = []
        self.entries: list[tuple[str, Resources]] = []
        self.sink = sink
        self._measurement = Lock()
        self._state = RLock()
        self._sends: list[SendRecord] = []

    @property
    def sends(self):
        with self._state:
            return tuple(self._sends)

    def book(self, category: Category, cost: Resources):
        if not self._measurement.acquire(blocking=False):
            raise ValueError("external booking during active measurement")
        try:
            self._book(category, cost)
        finally:
            self._measurement.release()

    def _book(self, category, cost, record=None):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        cost = Resources.model_validate_json(cost.model_dump_json())
        with self._state:
            if record is not None and any(a.attempt_id == record.attempt_id for a in self.attempts):
                raise ValueError("attempt already booked")
            self.entries.append((category, cost))
            if record is not None:
                self.attempts.append(record)
        if record is not None and self.sink is not None:
            self.sink(record, category)

    def book_attempt(self, record: AttemptRecord, *, category: Category = "acquisition"):
        if not self._measurement.acquire(blocking=False):
            raise ValueError("external attempt booking during active measurement")
        try:
            self._book_attempt(record, category=category)
        finally:
            self._measurement.release()

    def _book_attempt(self, record, *, category):
        row = AttemptRecord.model_validate_json(record.model_dump_json())
        self._book(category, Resources.measured(row.cost), row)

    @contextmanager
    def measure(self, what: str, *, category: Category = "acquisition"):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        if not self._measurement.acquire(blocking=False):
            raise ValueError("nested or active measurement on this ledger")
        meter = _Measurement(self, what)
        started = datetime.now(timezone.utc).isoformat()
        wall, cpu = time.perf_counter(), time.process_time()
        try:
            with counting() as noted:
                yield meter
        except BaseException:
            meter.outcome = "failed"
            raise
        finally:
            try:
                meter._close()
                sent = meter.send_cost
                measured = meter.cost.model_dump() | {
                    "llm_calls": max(sent.llm_calls, noted()),
                    "llm_tokens_in": max(sent.llm_tokens_in, meter.cost.llm_tokens_in),
                    "llm_tokens_out": max(sent.llm_tokens_out, meter.cost.llm_tokens_out),
                    "wall_s": max(meter.cost.wall_s, time.perf_counter() - wall),
                    "cpu_s": max(meter.cost.cpu_s, time.process_time() - cpu)}
                meter.record = AttemptRecord(what=what, cost=CostVector(**measured),
                    model_sends=measured["llm_calls"], outcome=meter.outcome, update_id=None,
                    started_at=started, ended_at=datetime.now(timezone.utc).isoformat())
                self._book_attempt(meter.record, category=category)
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

    def book_validation(self, repeats, *, probes, split_cost: Resources):
        groups = [tuple(group) for group in repeats]
        if any(len(group) != 3 for group in groups):
            raise ValueError("validation requires three repeats per case")
        total = split_cost.scaled(1 / SPLIT_REUSES)
        for cost in [*(cost for group in groups for cost in group), *probes]:
            total = total + Resources.measured(cost)
        self.book("validation", total)

    def profile(self, *, deployment_tasks: int):
        if type(deployment_tasks) is not int or deployment_tasks <= 0:
            raise ValueError("positive deployment task count required for amortisation")
        return CostProfile(acquisition=self.total("acquisition"), validation=self.total("validation"),
            serving=self.total("serving").scaled(1 / deployment_tasks),
            maintenance_per_task=self.total("maintenance").scaled(1 / deployment_tasks))
