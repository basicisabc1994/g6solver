# SPDX-License-Identifier: Apache-2.0
"""M's immutable advisory rows. Constructing a row confers no authority."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from g6.distill.detectors import SEED_TEMPLATES
from g6.distill.records import Count, Finite, Model, Positive, Record, Sha256
from g6.self_model.model import Field as MeasuredField
from g6.self_model.simulate import Simulation

PredictionKind = Literal['concept_compile', 'method_catch', 'experiment_decide', 'synthesis_policy']


class MetaPrediction(Record):
    identity_field = 'decision_id'
    decision_id: Sha256 | None = None
    kind: PredictionKind
    subject_id: str
    simulation: Simulation
    receipt: MeasuredField

    @model_validator(mode='after')
    def bound(self):
        if not self.subject_id.strip() or self.kind != self.simulation.action_kind:
            raise ValueError('prediction type must match the simulated action kind and name a subject')
        if (self.receipt.value != self.simulation.simulation_id
                or self.receipt.source != 'runner.cycle_log.append'
                or self.receipt.origin != 'measured'
                or self.receipt.observed_at != self.simulation.recorded_at):
            raise ValueError('recording receipt must bind the simulation before acting')
        return self


class ConceptOutcome(Model):
    concept_id: str
    family: str
    admitted: bool
    evidence_ref: Sha256


class WorthEstimate(Model):
    family: str
    probability: Finite
    observations: Count
    value_per_cost: Finite
    expected_delta_utility: Finite = 0.
    recurrence: Annotated[Finite, Field(ge=0)] = 0.
    cost: Positive = 1.
    met_before: Count = 0
    nearly_caught: bool = False
    observable: bool = True
    note: str = 'Beta(1,1) family estimate; uncalibrated, not gate evidence or field reward'


class MethodOutcome(Model):
    entry_id: Sha256
    case_id: str
    cohort_id: str
    resolved: bool
    cost: Annotated[Finite, Field(ge=0)]
    evaluator_ref: str
    oracle_digest: Sha256
    domain: str
    uncertainty: str
    claim_kind: str
    critical_regression: bool = False
    split: Literal['field', 'spent_published'] = 'field'


class SynthesisPolicy(Record):
    """S's knobs only. No field can change an evaluator, split, threshold or authority.

    Reuse and composition remain first. Template order may change, predicates may be
    disabled, and the existing three-call/one-repair caps may only be tightened.
    """
    template_order: tuple[str, ...] = tuple(sorted(SEED_TEMPLATES))
    try_predicate: bool = True
    repair_budget: Annotated[int, Field(strict=True, ge=0, le=1)] = 1
    max_synthesis_calls: Annotated[int, Field(strict=True, ge=0, le=3)] = 3

    @model_validator(mode='after')
    def registered(self):
        if len(set(self.template_order)) != len(self.template_order) or set(self.template_order) != set(SEED_TEMPLATES):
            raise ValueError('template order must be a permutation of the registered seed templates')
        return self


class ReplaySequence(Model):
    """Evaluator-owned frozen sequence and target; the target never reaches S."""
    case_id: str
    failure_ids: tuple[Sha256, ...]
    utility_target: Finite

    @model_validator(mode='after')
    def nonempty(self):
        if not self.case_id.strip() or not self.failure_ids or len(set(self.failure_ids)) != len(self.failure_ids):
            raise ValueError('a replay sequence needs a case ID and distinct failure IDs')
        return self


class ReplayMeasurement(Model):
    """Counts from trusted replay's independent field or spent-published labels."""
    tp: Count
    fp: Count
    evidence_ref: Sha256
    origin: Literal['independent_field', 'spent_published']


def concept_worth(family: str, outcomes: tuple[ConceptOutcome, ...], *, expected_delta_utility: float,
                  recurrence: float, cost: float, observable: bool = True, met_before: int = 0,
                  nearly_caught: bool = False) -> WorthEstimate:
    """Compilation probability is not M's reward: reward needs fresh labelled field utility."""
    from pydantic import TypeAdapter
    delta = TypeAdapter(Finite).validate_python(expected_delta_utility)
    recurrence = TypeAdapter(Annotated[Finite, Field(ge=0)]).validate_python(recurrence)
    cost = TypeAdapter(Positive).validate_python(cost)
    if len({r.concept_id for r in outcomes}) != len(outcomes):
        raise ValueError('duplicate concept outcomes')
    rows = [ConceptOutcome.model_validate_json(r.model_dump_json()) for r in outcomes if r.family == family]
    probability = (1 + sum(r.admitted for r in rows)) / (2 + len(rows))
    return WorthEstimate(family=family, probability=probability, observations=len(rows),
                         value_per_cost=probability * delta * recurrence / cost if observable else 0.,
                         expected_delta_utility=delta, recurrence=recurrence, cost=cost,
                         met_before=met_before, nearly_caught=nearly_caught, observable=observable)
