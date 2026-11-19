# SPDX-License-Identifier: Apache-2.0
"""Measured attempt costs and vector comparisons at the registered horizon."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager
from threading import Lock
from typing import Annotated, Literal

from pydantic import Field

from .graph import attempt_meter
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

    def book(self, category: Category, cost: Resources):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        self.entries.append((category, Resources.model_validate_json(cost.model_dump_json())))

    def book_attempt(self, record: AttemptRecord, *, category: Category = "acquisition"):
        row = AttemptRecord.model_validate_json(record.model_dump_json())
        if any(a.attempt_id == row.attempt_id for a in self.attempts):
            raise ValueError("attempt already booked")
        self.book(category, Resources.measured(row.cost))
        self.attempts.append(row)
        if self.sink is not None:
            self.sink(row, category)

    @contextmanager
    def measure(self, what: str, *, category: Category = "acquisition"):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        if not self._measurement.acquire(blocking=False):
            raise ValueError("nested or active measurement on this ledger")
        with_record = None
        try:
            with attempt_meter(what) as meter:
                with_record = meter
                wall, cpu = time.perf_counter(), time.process_time()
                try:
                    yield meter
                finally:
                    meter.cost = CostVector(**(meter.cost.model_dump() | {
                        "wall_s": max(meter.cost.wall_s, time.perf_counter() - wall),
                        "cpu_s": max(meter.cost.cpu_s, time.process_time() - cpu)}))
        finally:
            try:
                if with_record is not None and with_record.record is not None:
                    self.book_attempt(with_record.record, category=category)
            finally:
                self._measurement.release()

    def total(self, category: Category):
        if category not in CATEGORIES:
            raise ValueError("unknown cost category")
        total = Resources()
        for kind, cost in self.entries:
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
