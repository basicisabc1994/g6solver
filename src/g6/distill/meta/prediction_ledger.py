# SPDX-License-Identifier: Apache-2.0
"""Single-writer, append-only M cycle rows. Self-model owns simulation and scoring.

The caller supplies the clock and measured inputs. This is a runner-side recording
adapter, not an action executor. Chain anchors must be retained independently to
detect replacement of an entire history. Predictions never feed an admission gate.
"""
from __future__ import annotations

from g6.core.ledger import ChainLedger, LedgerCorrupt, LedgerError, canonical
from g6.core.lessons import hit_rate
from g6.core.storage import state_path
from g6.self_model.model import Field
from g6.self_model.simulate import ScoredSimulation, score_after, simulate

from .records import ConceptOutcome, MetaPrediction, MethodOutcome, SynthesisPolicy, WorthEstimate
from .experiments import ExperimentChoice

_DATA_MODELS = {model.__name__: model for model in (ConceptOutcome, MethodOutcome, SynthesisPolicy, WorthEstimate,
                                                  ExperimentChoice)}


def _body(row):
    body = row.model_dump(mode='json')
    if isinstance(row, MetaPrediction):
        body['simulation'].pop('simulation_id', None)
    if isinstance(row, ScoredSimulation):
        for failure in body['failures']:
            failure.pop('record_id', None)
    return body


def _validate(kind, ident, body, seen):
    try:
        if kind == 'prediction':
            row = MetaPrediction.model_validate_json(canonical(body))
            if row.decision_id != ident:
                raise ValueError('prediction identity differs')
            for (prior_kind, _), prior in seen.items():
                if prior_kind == 'prediction' and prior['simulation']['action_sha256'] == row.simulation.action_sha256:
                    raise ValueError('duplicate action prediction')
        elif kind == 'score':
            prediction = MetaPrediction.model_validate_json(canonical(seen['prediction', ident]))
            scored = ScoredSimulation.model_validate_json(canonical(body['scored']))
            from g6.self_model.simulate import Outcome
            outcome = Outcome.model_validate_json(canonical(body['outcome']))
            if outcome.prediction_record != prediction.receipt:
                raise ValueError('score receipt differs from recorded receipt')
            if score_after(prediction.simulation, outcome, now=scored.scored_at) != scored:
                raise ValueError('score differs from measured outcome')
        elif kind in _DATA_MODELS:
            row = _DATA_MODELS[kind].model_validate_json(canonical(body))
            from g6.core.ledger import digest
            if digest(row.model_dump(mode='json')) != ident:
                raise ValueError('M data identity differs')
        else:
            raise ValueError('unknown M row')
    except (ValueError, KeyError, TypeError) as error:
        return [str(error)]
    return []


class MetaLedger:
    def __init__(self, path=None, *, expect_head=None, memory=False):
        self.path = None if memory else path if path is not None else state_path('distill', 'meta', 'predictions.jsonl')
        try:
            self.chain = ChainLedger(self.path, expect_head=expect_head, validate=_validate)
        except (RecursionError, MemoryError) as exc:   # a home ledger line nested past the decoder
            raise LedgerError("a ledger line exceeds the JSON decoder's limits") from exc

    def _rows(self, *, reload=True):
        try:
            if reload:
                self.chain.reload()
            return self.chain.entries()
        except (RecursionError, MemoryError) as exc:
            raise LedgerError("a ledger line exceeds the JSON decoder's limits") from exc

    def _append(self, kind, ident, body):
        if self.path is not None:
            from pathlib import Path
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self.chain.append(kind, ident, body)
        except (RecursionError, MemoryError) as exc:
            raise LedgerError("a ledger line exceeds the JSON decoder's limits") from exc

    def predictions(self) -> tuple[MetaPrediction, ...]:
        return tuple(MetaPrediction.model_validate_json(canonical(row['body']))
                     for row in self._rows() if row['kind'] == 'prediction')

    def scores(self) -> tuple[ScoredSimulation, ...]:
        return tuple(ScoredSimulation.model_validate_json(canonical(row['body']['scored']))
                     for row in self._rows() if row['kind'] == 'score')

    def predict(self, *, kind, subject_id, action, snapshot, reference_actions, relationships,
                protected_manifest, now) -> MetaPrediction:
        existing = self.predictions()
        if any(row.simulation.action_sha256 == action.sha256 for row in existing):
            raise ValueError('action already has a recorded prediction')
        simulation = simulate(action, snapshot, reference_actions=reference_actions, relationships=relationships,
                              protected_manifest=protected_manifest, now=now)
        receipt = Field.measured(simulation.simulation_id, source='runner.cycle_log.append',
                                 observed_at=now, ttl_s=86400.)
        row = MetaPrediction(kind=kind, subject_id=subject_id, simulation=simulation, receipt=receipt)
        self._append('prediction', row.decision_id, _body(row))
        return row

    def score(self, decision_id, outcome, *, now) -> ScoredSimulation:
        rows = {r.decision_id: r for r in self.predictions()}
        row = rows[decision_id]
        if any(r['kind'] == 'score' and r['id'] == decision_id for r in self._rows(reload=False)):
            raise ValueError('prediction already scored')
        if outcome.prediction_record != row.receipt:
            raise ValueError('outcome receipt differs from recorded receipt')
        scored = score_after(row.simulation, outcome, now=now)
        self._append('score', decision_id, {'scored': _body(scored),
                                           'outcome': outcome.model_dump(mode='json')})
        return scored

    def record_data(self, value) -> str:
        from g6.core.ledger import digest
        model = _DATA_MODELS.get(type(value).__name__)
        if model is not type(value):
            raise ValueError('only declared M data rows can be recorded')
        row = model.model_validate_json(value.model_dump_json())
        body = row.model_dump(mode='json')
        ident = digest(body)
        if any(row['kind'] == model.__name__ and row['id'] == ident for row in self._rows()):
            return ident
        self._append(model.__name__, ident, body)
        return ident

    def data(self, model):
        if _DATA_MODELS.get(model.__name__) is not model:
            raise ValueError('unknown M data model')
        return tuple(model.model_validate_json(canonical(row['body'])) for row in self._rows()
                     if row['kind'] == model.__name__)

    def hit_rates(self) -> dict[str, float | None]:
        predictions = self.predictions()
        kinds = {row.simulation.simulation_id: row.kind for row in predictions}
        grouped = {row.kind: [] for row in predictions}
        for row in self.scores():
            if row.simulation_id not in kinds:
                raise LedgerCorrupt('score has no recorded simulation')
            grouped[kinds[row.simulation_id]].extend(score.status for score in row.scores)
        return {kind: hit_rate(statuses) for kind, statuses in grouped.items()}
