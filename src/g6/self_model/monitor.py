# SPDX-License-Identifier: Apache-2.0
"""Compare measured signals with historical expectations; return at most one row."""
from __future__ import annotations

from typing import Literal

from pydantic import computed_field, model_validator

from g6.core.binomial import lower_bound, upper_bound
from g6.self_model.model import Data, Field, UNKNOWN, clock, digest

Category = Literal['identify_task', 'check_progress', 'evaluate', 'predict_outcome']


def measured(field: Field, *, now: float):
    if field.origin != 'measured' or field.source.startswith('g6.self_model'):
        raise ValueError('only measured observations are evidence')
    result = field.read(now=now, evidence=True)
    if result == UNKNOWN:
        raise ValueError('a fresh measured field is required')
    return result


def number(field: Field, *, now: float) -> float:
    value = measured(field, now=now)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError('a measured finite number is required')
    return float(value)


class Observation(Data):
    observation_id: str
    signal: str
    category: Category
    measurement: Field


class Expectation(Data):
    signal: str
    lower: Field
    upper: Field


class Explanation(Data):
    signal: str
    previous: Field
    current: Field
    reason: str


class FailureRecord(Data):
    """Wave-one row, with the existing domain's five fields and distill intake metadata.

    Independence is declared provenance, not authenticated by this data package.
    The runner persists rows and adapts domain_fields to domain.FailureRecord.
    """
    case_id: str
    cause: Literal['self_model_discrepancy', 'prediction_failed']
    kind: Literal['self_model_discrepancy', 'prediction_failed']
    category: Category
    note: str
    observed_at: float
    evidence: tuple[Field, ...]
    case_ref: str
    changeable: bool = True
    independently_observed: bool = True
    observed_by: Literal['independent', 'self'] = 'independent'
    family: str = 'self_model'
    source: Literal['runner', 'experiment'] = 'runner'
    split: Literal['monitor', 'field'] = 'monitor'
    finding_class: str = UNKNOWN
    met_before: tuple[str, ...] = ()
    g6_commit: str = UNKNOWN
    provenance: str = 'declared measured observation; not authenticated'

    @model_validator(mode='after')
    def consistent(self):
        clock(self.observed_at)
        if self.kind != self.cause or self.independently_observed != (self.observed_by == 'independent'):
            raise ValueError('failure provenance or kind is inconsistent')
        if not self.evidence:
            raise ValueError('measured evidence is required for a failure')
        for field in self.evidence:
            measured(field, now=self.observed_at)
        return self

    @computed_field
    @property
    def record_id(self) -> str:
        return digest(self.model_dump(exclude={'record_id'}))

    def domain_fields(self) -> dict:
        return {name: getattr(self, name) for name in
                ('case_id', 'cause', 'changeable', 'note', 'independently_observed')}


class SelfModelUpdate(Data):
    kind: Literal['self_model_update'] = 'self_model_update'
    observation_id: str
    signal: str
    category: Category
    previous: Field
    current: Field
    reason: str
    observed_at: float

    @computed_field
    @property
    def record_id(self) -> str:
        return digest(self.model_dump(exclude={'record_id'}))


def expectation_from_history(signal: str, history: tuple[Field, ...], *, now: float,
                             ttl_s: float) -> Expectation | str:
    """Exact two-sided 95% binomial interval, as lessons.calibrate's binomial seam.

    A history item is a measured boolean outcome. Continuous signals use an
    independently measured lower/upper Expectation supplied by their reader.
    """
    clock(now)
    if not history:
        return UNKNOWN
    values = tuple(measured(field, now=now) for field in history)
    if not all(type(value) is bool for value in values):
        raise ValueError('binomial history requires measured booleans')
    k, n = sum(values), len(values)
    # Derived bounds retain the oldest observation time, never refresh old data.
    at = min(field.observed_at for field in history)
    ttl = min(ttl_s, *(field.observed_at + field.ttl_s - at for field in history))
    metadata = dict(source='g6.core.binomial:historical_interval', observed_at=at, ttl_s=ttl)
    return Expectation(signal=signal, lower=Field.measured(lower_bound(k, n, .025), **metadata),
                       upper=Field.measured(upper_bound(k, n, .025), **metadata))


def compare(observation: Observation, expectation: Expectation, *, now: float,
            explanation: Explanation | None = None, seen: tuple[str, ...] = ()) -> tuple[FailureRecord | SelfModelUpdate, ...]:
    """One observation yields zero rows or exactly one discrepancy/update row.

    ``seen`` is supplied from the cycle log. There is no hidden mutable cache.
    """
    clock(now)
    if observation.signal != expectation.signal:
        raise ValueError('observation and expectation signals differ')
    value = number(observation.measurement, now=now)
    low, high = number(expectation.lower, now=now), number(expectation.upper, now=now)
    if low > high:
        raise ValueError('expectation interval is reversed')
    if observation.observation_id in seen or low <= value <= high:
        return ()
    if explanation is not None and explanation.signal == observation.signal and explanation.reason.strip():
        current = explanation.current
        older = max(expectation.lower.observed_at, expectation.upper.observed_at, explanation.previous.observed_at)
        try:
            explanation.previous.valid()
            current_value = measured(current, now=now)
            explains = (explanation.previous.origin == 'measured'
                        and not explanation.previous.source.startswith('g6.self_model')
                        and explanation.previous.source == current.source
                        and older < current.observed_at <= observation.measurement.observed_at
                        and current_value != explanation.previous.value)
        except ValueError:
            explains = False
        if explains:
            return (SelfModelUpdate(observation_id=observation.observation_id, signal=observation.signal,
                                    category=observation.category, previous=explanation.previous, current=current,
                                    reason=explanation.reason, observed_at=float(now)),)
    return (FailureRecord(case_id=observation.observation_id, case_ref=digest(observation.model_dump()),
                          cause='self_model_discrepancy', kind='self_model_discrepancy',
                          category=observation.category, note=f'{observation.signal} is outside its expected interval',
                          observed_at=float(now), evidence=(observation.measurement, expectation.lower, expectation.upper)),)
