# SPDX-License-Identifier: Apache-2.0
"""Pure forward simulation and post-action scoring; the runner owns persistence.

Return the Simulation as a cycle row before acting. score_after requires a
separate recording receipt and measured, action-bound post-completion outcomes.
There is no callback, action executor, model call or filesystem store here.
"""
from __future__ import annotations

import statistics
from typing import Literal

from pydantic import computed_field, field_validator

from g6.core.lessons import Calibration, Prediction, Reading, calibrate, score
from g6.reliability_pipeline.premortem import hypotheses
from g6.self_model.model import Data, Field, NamedField, SelfModel, UNKNOWN, clock, digest, unique
from g6.self_model.monitor import FailureRecord, measured, number
from g6.self_model.risk_gate import GateReadout, PlanReadout, ProtectedManifest, RiskDecision, assess


class Cost(Data):
    model_calls: Field
    wall_time_s: Field
    allowance_share: Field


class Action(Data):
    action_id: str
    kind: str
    components: tuple[str, ...]
    touched_paths: tuple[str, ...]
    measures: tuple[NamedField, ...]
    cost: Cost
    operations: tuple[str, ...] = ()
    gate: GateReadout | None = None
    plan: PlanReadout | None = None

    _unique = field_validator('measures')(unique)

    @computed_field
    @property
    def sha256(self) -> str:
        return digest(self.model_dump(exclude={'sha256', 'gate', 'plan'}))


class ReferenceAction(Data):
    action_id: str
    kind: str
    deltas: tuple[NamedField, ...]
    regressed: Field

    _unique = field_validator('deltas')(unique)


class Relationships(Data):
    """Measured graph input: (importer, dependency), plus bidirectional corpus links."""
    imports: tuple[tuple[str, str], ...] = ()
    corpus: tuple[tuple[str, str], ...] = ()
    provenance: Field

    @classmethod
    def measured(cls, *, imports: tuple[tuple[str, str], ...], corpus: tuple[tuple[str, str], ...],
                 source: str, observed_at: float, ttl_s: float):
        body = digest({'imports': imports, 'corpus': corpus})
        return cls(imports=imports, corpus=corpus,
                   provenance=Field.measured(body, source=source, observed_at=observed_at, ttl_s=ttl_s))

    def affected(self, components: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        if measured(self.provenance, now=now) != digest({'imports': self.imports, 'corpus': self.corpus}):
            raise ValueError('relationship graph does not match its measured digest')
        reached, todo = set(), list(components)
        edges = (*((dependency, importer) for importer, dependency in self.imports),
                 *self.corpus, *((right, left) for left, right in self.corpus))
        while todo:
            component = todo.pop()
            if component in reached:
                continue
            reached.add(component)
            todo.extend(right for left, right in edges if left == component and right not in reached)
        return tuple(sorted(reached))


class Effect(Data):
    measure: str
    prediction: Prediction | None
    lower: float | str = UNKNOWN
    upper: float | str = UNKNOWN
    reference_count: int
    origin: Literal['self_prediction'] = 'self_prediction'
    interval_kind: str = 'observed reference range; no coverage guarantee'


class Simulation(Data):
    action_sha256: str
    action_kind: str
    snapshot_sha256: str
    recorded_at: float
    affected: tuple[str, ...]
    effects: tuple[Effect, ...]
    could_break: tuple[str, ...]
    cost: Cost
    reversibility: str
    risk: RiskDecision
    protected_touched: tuple[str, ...]
    origin: Literal['self_prediction'] = 'self_prediction'

    @computed_field
    @property
    def simulation_id(self) -> str:
        return digest(self.model_dump(exclude={'simulation_id'}))


def _references(actions: tuple[ReferenceAction, ...], kind: str, now: float) -> tuple[ReferenceAction, ...]:
    found: dict[str, ReferenceAction] = {}
    for action in actions:
        if action.kind != kind:
            continue
        if action.action_id in found and found[action.action_id] != action:
            raise ValueError('conflicting reference action IDs')
        if type(measured(action.regressed, now=now)) is not bool:
            raise ValueError('reference regressions must be measured booleans')
        for item in action.deltas:
            number(item.field, now=now)
        found[action.action_id] = action
    return tuple(found[key] for key in sorted(found))


def simulate(action: Action, snapshot: SelfModel, *, reference_actions: tuple[ReferenceAction, ...],
             relationships: Relationships, protected_manifest: ProtectedManifest | None, now: float) -> Simulation:
    clock(now)
    references = _references(reference_actions, action.kind, now)
    effects = []
    for measure in action.measures:
        values = [number(item.field, now=now) for ref in references for item in ref.deltas if item.name == measure.name]
        try:
            before = number(measure.field, now=now)
        except ValueError:
            # Stale baselines yield unknown forecasts; predicted baselines are
            # rejected rather than laundered through a reference class.
            if measure.field.origin != 'measured':
                raise
            before = None
        prediction, low, high = None, UNKNOWN, UNKNOWN
        if len(values) >= 5 and before is not None:
            low, high = before + min(values), before + max(values)
            expected = before + statistics.median(values)
            if expected != before:
                prediction = Prediction(digest((action.sha256, measure.name)), measure.name, before, expected)
        effects.append(Effect(measure=measure.name, prediction=prediction, lower=low, upper=high,
                              reference_count=len(values)))
    affected = relationships.affected(action.components, now=now)
    risk = assess(action_id=action.action_id, action_sha256=action.sha256, touched_paths=action.touched_paths,
                  affected=affected, manifest=protected_manifest, gate=action.gate, plan=action.plan,
                  has_plan=bool(action.operations), regression_history=tuple(ref.regressed for ref in references), now=now)
    if risk.decision == 'proceed' and (not effects or any(effect.prediction is None for effect in effects)):
        risk = RiskDecision(decision='more_evidence', score=risk.score, reasons=('effect predictions are unknown',))
    reversibility = action.gate.reversibility.read(now=now, evidence=True) if action.gate else UNKNOWN
    return Simulation(action_sha256=action.sha256, action_kind=action.kind, snapshot_sha256=snapshot.sha256(now=now),
                      recorded_at=float(now), affected=affected, effects=tuple(effects), cost=action.cost,
                      reversibility=str(reversibility), could_break=tuple(row['question'] for row in hypotheses()),
                      risk=risk, protected_touched=risk.protected_touched)


class Outcome(Data):
    action_sha256: str
    action_started_at: float
    action_finished_at: float
    prediction_record: Field
    measurements: tuple[NamedField, ...]

    _unique = field_validator('measurements')(unique)


class PredictionScore(Data):
    prediction_id: str
    status: Literal['held', 'failed', 'open']


class ScoredSimulation(Data):
    simulation_id: str
    scored_at: float
    scores: tuple[PredictionScore, ...]
    failures: tuple[FailureRecord, ...]
    calibration: tuple[Calibration, ...]


def score_after(simulation: Simulation, outcome: Outcome, *, now: float) -> ScoredSimulation:
    clock(now)
    started, finished = clock(outcome.action_started_at), clock(outcome.action_finished_at)
    if not simulation.recorded_at < started <= finished <= now:
        raise ValueError('action time must be after recording and complete before scoring time')
    if outcome.action_sha256 != simulation.action_sha256:
        raise ValueError('outcome is bound to a different action')
    receipt = outcome.prediction_record
    if (receipt.source != 'runner.cycle_log.append' or measured(receipt, now=now) != simulation.simulation_id
            or not simulation.recorded_at <= receipt.observed_at < started):
        raise ValueError('prediction row must be recorded before the action')
    outcomes = {item.name: item.field for item in outcome.measurements}
    for field in outcomes.values():
        measured(field, now=now)
        if not finished <= field.observed_at <= now:
            raise ValueError('outcome measurement time must be after action completion')
    scores, failures, readings = [], [], []
    for effect in simulation.effects:
        prediction = effect.prediction
        if prediction is None:
            continue
        observed = outcomes.get(effect.measure)
        status = score(prediction, None if observed is None else number(observed, now=now))
        scores.append(PredictionScore(prediction_id=prediction.prediction_id, status=status))
        if status != 'open':
            readings.append(Reading(effect.measure, simulation.action_kind, simulation.action_sha256, status == 'held'))
        if status == 'failed':
            failures.append(FailureRecord(case_id=prediction.prediction_id, case_ref=simulation.action_sha256,
                                kind='prediction_failed', cause='prediction_failed', category='predict_outcome',
                                note=f'{effect.measure} did not reach the recorded prediction', observed_at=float(now),
                                evidence=(observed,), source='experiment', split='field'))
    return ScoredSimulation(simulation_id=simulation.simulation_id, scored_at=float(now), scores=tuple(scores),
                            failures=tuple(failures), calibration=calibrate(readings))
