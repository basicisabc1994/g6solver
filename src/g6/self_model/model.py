# SPDX-License-Identifier: Apache-2.0
"""Data only. Readers supply measured facts and an explicit clock, never ambient state."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, FiniteFloat, field_validator, model_validator

UNKNOWN = 'unknown'
Scalar = str | bool | int | float | None
Value = Scalar | tuple[Scalar, ...]


def digest(value: object) -> str:
    """core.ledger's canonical digest without importing its filesystem writer."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def clock(now: float) -> float:
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('clock must be finite')
    return now


class Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True, allow_inf_nan=False)


class Field(Data):
    value: Value
    source: str
    observed_at: FiniteFloat
    ttl_s: FiniteFloat
    sha256: str
    origin: Literal['measured', 'self_prediction'] = 'measured'

    @model_validator(mode='after')
    def valid(self) -> Self:
        if not self.source.strip() or self.ttl_s < 0:
            raise ValueError('source and nonnegative ttl_s are required')
        if self.sha256 != digest(self.model_dump(exclude={'sha256'})):
            raise ValueError('field digest differs from its body')
        return self

    @classmethod
    def measured(cls, value: Value, *, source: str, observed_at: float, ttl_s: float) -> Self:
        return cls._make(value, source, observed_at, ttl_s, 'measured')

    @classmethod
    def predicted(cls, value: Value, *, source: str, observed_at: float, ttl_s: float) -> Self:
        return cls._make(value, source, observed_at, ttl_s, 'self_prediction')

    @classmethod
    def _make(cls, value: Value, source: str, observed_at: float, ttl_s: float, origin: str) -> Self:
        body = dict(value=value, source=source, observed_at=float(clock(observed_at)),
                    ttl_s=float(clock(ttl_s)), origin=origin)
        return cls(**body, sha256=digest(body))

    def read(self, *, now: float, evidence: bool = False) -> Value:
        self.valid()  # Recheck data arriving through model_copy/model_construct too.
        age = clock(now) - self.observed_at
        if age < 0 or age > self.ttl_s:
            return UNKNOWN
        if evidence and (self.origin != 'measured' or self.source.startswith('g6.self_model')):
            return UNKNOWN
        return self.value


class NamedField(Data):
    name: str
    field: Field

    @field_validator('name')
    @classmethod
    def named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('name is required')
        return value


def unique(fields: tuple[NamedField, ...]) -> tuple[NamedField, ...]:
    if len({item.name for item in fields}) != len(fields):
        raise ValueError('duplicate field name')
    return fields


class Component(Data):
    name: str
    capabilities: tuple[NamedField, ...] = ()
    limits: tuple[NamedField, ...] = ()
    metadata: tuple[NamedField, ...] = ()

    _unique = field_validator('capabilities', 'limits', 'metadata')(unique)


class SelfModel(Data):
    components: tuple[Component, ...] = ()
    state: tuple[NamedField, ...] = ()
    goals: tuple[NamedField, ...] = ()
    commitments: tuple[NamedField, ...] = ()
    environment: tuple[NamedField, ...] = ()
    authority: tuple[NamedField, ...] = ()

    _unique = field_validator('state', 'goals', 'commitments', 'environment', 'authority')(unique)

    @field_validator('components')
    @classmethod
    def unique_components(cls, value: tuple[Component, ...]) -> tuple[Component, ...]:
        if len({item.name for item in value}) != len(value):
            raise ValueError('duplicate component name')
        return value

    def read(self, *, now: float) -> dict:
        clock(now)

        def section(fields):
            return {item.name: item.field.read(now=now, evidence=True) for item in fields}

        return {'components': {item.name: {part: section(getattr(item, part))
                                           for part in ('capabilities', 'limits', 'metadata')}
                               for item in self.components},
                **{part: section(getattr(self, part))
                   for part in ('state', 'goals', 'commitments', 'environment', 'authority')}}

    def sha256(self, *, now: float) -> str:
        # Include provenance as well as the freshness-sensitive public reading.
        return digest({'fields': self.model_dump(), 'reading': self.read(now=now)})
