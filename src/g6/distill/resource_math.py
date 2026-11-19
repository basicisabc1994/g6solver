# SPDX-License-Identifier: Apache-2.0
"""Integer duration arithmetic and bounded resource estimates."""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from .records import Finite, Model

RESOURCE_FIELDS = ("llm_calls", "llm_tokens_in", "llm_tokens_out", "cpu_s", "wall_s",
                   "solver_calls", "probe_runs", "sandbox_runs")
CANONICAL_FIELDS = tuple({"cpu_s": "cpu_ns", "wall_s": "wall_ns"}.get(k, k) for k in RESOURCE_FIELDS)
NS = 1_000_000_000
Nonnegative = Annotated[Finite, Field(ge=0)]
NonnegativeInteger = Annotated[StrictInt, Field(ge=0)]


def nanoseconds(seconds):
    if isinstance(seconds, bool) or not math.isfinite(seconds):
        raise ValueError("finite duration required")
    return round(Fraction(str(seconds)) * NS)


class ResourceDelta(Model):
    """Signed intervals; duration arithmetic uses integer nanoseconds."""
    llm_calls: StrictInt | Finite = 0
    llm_tokens_in: StrictInt | Finite = 0
    llm_tokens_out: StrictInt | Finite = 0
    cpu_s: Finite = 0
    wall_s: Finite = 0
    solver_calls: StrictInt | Finite = 0
    probe_runs: StrictInt | Finite = 0
    sandbox_runs: StrictInt | Finite = 0
    cpu_ns: StrictInt = 0
    wall_ns: StrictInt = 0
    upper: dict[str, Finite | StrictInt] = Field(default_factory=dict)
    resource_gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def canonical_durations(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for stem in ("cpu", "wall"):
            seconds, ns = stem + "_s", stem + "_ns"
            if ns not in data:
                value = data.get(seconds, 0)
                nanoseconds(value)
                exact = Fraction(str(value)) * NS
                data[ns] = math.floor(exact)
                high = math.ceil(exact)
                if high != data[ns]:
                    upper = dict(data.get("upper", {}))
                    upper[ns] = max(upper.get(ns, high), high)
                    data["upper"] = upper
            elif seconds in data and data[seconds] != data[ns] / NS and nanoseconds(data[seconds]) != data[ns]:
                raise ValueError("duration seconds differ from integer nanoseconds")
            data[seconds] = data[ns] / NS
        return data

    @model_validator(mode="after")
    def bounded(self):
        if set(self.upper) - set(CANONICAL_FIELDS):
            raise ValueError("unknown resource interval dimension")
        lower = self.canonical_lower
        for key, value in self.upper.items():
            if key.endswith("_ns") and (isinstance(value, bool) or int(value) != value):
                raise ValueError("duration bounds require integer nanoseconds")
            if value < lower[key]:
                raise ValueError("resource upper bound is below lower bound")
        # A single representation keeps equality independent of construction order.
        object.__setattr__(self, "upper", {k: int(v) if k.endswith("_ns") else v
                                           for k, v in self.upper.items() if v != lower[k]})
        object.__setattr__(self, "resource_gaps", sorted(set(self.resource_gaps)))
        return self

    @property
    def canonical_lower(self):
        return {k: getattr(self, k) for k in CANONICAL_FIELDS}

    @property
    def canonical_upper(self):
        return self.canonical_lower | self.upper

    def interval(self, name):
        key = {"cpu_s": "cpu_ns", "wall_s": "wall_ns"}.get(name, name)
        low, high = self.canonical_lower[key], self.canonical_upper[key]
        return (low / NS, high / NS) if name in {"cpu_s", "wall_s"} else (low, high)

    @classmethod
    def from_bounds(cls, lower, upper, gaps=()):
        return cls(**lower, upper=upper, resource_gaps=sorted(set(gaps)))

    def __add__(self, other):
        return type(self).from_bounds(
            {k: self.canonical_lower[k] + other.canonical_lower[k] for k in CANONICAL_FIELDS},
            {k: self.canonical_upper[k] + other.canonical_upper[k] for k in CANONICAL_FIELDS},
            self.resource_gaps + other.resource_gaps)

    def scaled(self, factor):
        if not math.isfinite(factor):
            raise ValueError("finite scaling factor required")
        factor = Fraction(str(factor))
        lower, upper = {}, {}
        for key in CANONICAL_FIELDS:
            ends = [Fraction(str(v)) * factor for v in (self.canonical_lower[key], self.canonical_upper[key])]
            lower[key], upper[key] = min(ends), max(ends)
            if key.endswith("_ns"):
                lower[key], upper[key] = math.floor(lower[key]), math.ceil(upper[key])
            else:
                lower[key] = int(lower[key]) if lower[key].denominator == 1 else float(lower[key])
                upper[key] = int(upper[key]) if upper[key].denominator == 1 else float(upper[key])
        return type(self).from_bounds(lower, upper, self.resource_gaps)

    def divided(self, divisor):
        if type(divisor) is not int or divisor <= 0:
            raise ValueError("positive integer divisor required")
        return self.scaled(Fraction(1, divisor))

    def minus(self, other):
        return ResourceDelta.from_bounds(
            {k: self.canonical_lower[k] - other.canonical_upper[k] for k in CANONICAL_FIELDS},
            {k: self.canonical_upper[k] - other.canonical_lower[k] for k in CANONICAL_FIELDS},
            self.resource_gaps + other.resource_gaps)

    def _counts_only(self):
        lower, upper = self.canonical_lower, self.canonical_upper
        for key in ("cpu_ns", "wall_ns"):
            lower[key] = upper[key] = 0
        return type(self).from_bounds(lower, upper)

    def within(self, limit):
        return all(self.canonical_upper[k] <= limit.canonical_lower[k] for k in CANONICAL_FIELDS)


class Resources(ResourceDelta):
    llm_calls: NonnegativeInteger | Nonnegative = 0
    llm_tokens_in: NonnegativeInteger | Nonnegative = 0
    llm_tokens_out: NonnegativeInteger | Nonnegative = 0
    cpu_s: Nonnegative = 0
    wall_s: Nonnegative = 0
    cpu_ns: Annotated[StrictInt, Field(ge=0)] = 0
    wall_ns: Annotated[StrictInt, Field(ge=0)] = 0
    solver_calls: NonnegativeInteger | Nonnegative = 0
    probe_runs: NonnegativeInteger | Nonnegative = 0
    sandbox_runs: NonnegativeInteger | Nonnegative = 0

    @classmethod
    def measured(cls, cost):
        return cls.model_validate_json(cost.model_dump_json())


class HorizonResources(Resources):
    pass
