# SPDX-License-Identifier: Apache-2.0
"""Bounded autonomous tick, OFF unless the owner supplies every capability.

No ambient engineer, repository executor, budget, signer or network client. A
trusted host refreshes measured inputs, owns this single-writer cycle ledger,
and executes work through mission/evaluator ports. Predictions go to MetaLedger;
only a separate authenticated host MergeRequest can reach the merge gate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping

from g6.core import stop as global_stop
from g6.core.ledger import ChainLedger, digest
from g6.distill.meta import MetaController, MetaLedger, choose_experiment
from g6.distill.meta.policy_gate import diagnose_runner_tick
from g6.self_maintenance.constraints import StageMeasurement, identify_constraint
from g6.self_model.model import SelfModel
from g6.self_model.monitor import compare
from g6.self_model.simulate import Action, Outcome, ReferenceAction, Relationships
from g6.self_model.risk_gate import ProtectedManifest
from g6.self_training.autonomy_gate import authority, clock


@dataclass(frozen=True)
class TickInputs:
    snapshot: SelfModel
    state: Any
    forecasts: tuple
    actions: Mapping[str, Action]
    reference_actions: tuple[ReferenceAction, ...]
    relationships: Relationships
    protected_manifest: ProtectedManifest
    stages: tuple[StageMeasurement, ...]
    power: Mapping[str, float]
    provider: str
    worst_case: float
    strategy: str
    experiment_stages: Mapping[str, str] = field(default_factory=dict)
    observations: tuple = ()  # (Observation, Expectation), measured host rows


@dataclass(frozen=True)
class RunResult:
    outcome: Outcome
    used: float
    change: Any = None
    merge_request: Any = None
    rca: Mapping = field(default_factory=dict)
    library_changes: tuple[str, ...] = ()
    regressions: tuple = ()  # (host-measured signal, host signature), never predictions
    rollback_trigger: tuple | None = None


@dataclass(frozen=True)
class TickResult:
    status: str
    reason: str = ''
    pr: str = ''
    next_at: float = 0.


@dataclass(frozen=True)
class Digest:
    body: str
    pr: str = ''


class Runner:
    def __init__(self, *, config, refresh, execute, budget, repository, clock, home=None,
                 meta=None, journal=None, stop=None, intake=None, merge_gate=None):
        self.config, self.refresh, self.execute, self.budget = config, refresh, execute, budget
        self.repository, self.clock, self.stop = repository, clock, stop or (lambda: False)
        self.meta = meta if meta is not None else MetaLedger()
        self.controller = MetaController(self.meta)
        self.journal = journal if journal is not None else ChainLedger(
            Path(home) / 'distill/runner_ledger.jsonl' if home is not None else None)
        self.intake, self.merge_gate = intake, merge_gate

    def stopped(self):
        return bool(global_stop.engaged() or self.stop() or self.repository.stopped())

    def _append(self, kind, body):
        if self.stopped():
            return
        self.journal.reload()
        if self.journal.path is not None:
            self.journal.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal.append(kind, digest(body), body)

    def _wait(self, status, reason, *, now):
        waits = [r['body'] for r in self.journal.entries() if r['kind'] == 'wait']
        delay = min(86400., 900. * 2 ** min(7, len(waits)))
        next_at = now + delay
        self._append('wait', dict(status=status, reason=reason, at=now, next_at=next_at))
        return TickResult(status, reason, next_at=next_at)

    def tick(self, *, now=None):
        now = clock(self.clock() if now is None else now)
        if self.stopped():
            return TickResult('stopped', 'owner stop or kill switch')
        if not authority(self.config).permits('research'):
            return TickResult('off', 'research authority is off')
        self.journal.reload()
        prior = self.journal.entries()
        waits = [r['body'] for r in prior if r['kind'] == 'wait']
        if waits and now < waits[-1]['next_at']:
            return TickResult('waiting', waits[-1]['reason'], next_at=waits[-1]['next_at'])
        data = self.refresh(now)
        state = data.snapshot.read(now=now)['state']
        if (state.get('stop') is not False or state.get('budget_room') is not True
                or state.get('isolation') not in ('container', 'appcontainer', 'bubblewrap')):
            return self._wait('blocked', 'self-model admission state unknown or blocked', now=now)
        if (data.provider not in ('claude', 'codex') or type(data.worst_case) not in (int, float)
                or not math.isfinite(data.worst_case) or data.worst_case <= 0):
            return self._wait('blocked', 'known provider and positive worst-case allowance required', now=now)
        constraint = identify_constraint(data.stages)
        cycles = [r['body'] for r in prior if r['kind'] == 'cycle']
        if constraint.bottleneck == 'unknown':
            return self._wait('blocked', 'measure loop stages independently first', now=now)
        if len(cycles) >= 2 and all(row['bottleneck'] == constraint.bottleneck
            and row['strategy'] == data.strategy for row in cycles[-2:]):
            return self._wait('reframe', 'same constraint for three ticks; change method, level or frame', now=now)
        forecasts = tuple(f for f in data.forecasts
            if data.experiment_stages.get(f.experiment.id) == constraint.bottleneck
            and type(data.power.get(f.experiment.id)) in (int, float)
            and math.isfinite(data.power[f.experiment.id]) and .5 <= data.power[f.experiment.id] <= 1.)
        choice = choose_experiment(data.state, forecasts)
        if choice is None:
            return self._wait('waiting', 'no powered decision-relevant experiment', now=now)
        if self.stopped():
            return TickResult('stopped')
        admission = self.budget.reserve(data.provider, datetime.fromtimestamp(now, timezone.utc), data.worst_case,
                                         tick=digest({'at': now, 'choice': choice.experiment.id}))
        if not admission.admitted:
            return self._wait('budget', admission.reason, now=now)
        used = data.worst_case  # crash/uncertainty remains charged at the reservation
        try:
            if self.stopped() or not authority(self.config).permits('research'):
                return TickResult('stopped')
            seen = tuple(r['body']['observation_id'] for r in prior if r['kind'] == 'monitor')
            monitor_discrepancies = 0
            for observation, expectation in data.observations:
                for row in compare(observation, expectation, now=now, seen=seen):
                    self._append('monitor', {'observation_id': observation.observation_id,
                        'row': row.model_dump(mode='json')})
                    if getattr(row, 'kind', '') == 'self_model_discrepancy' and self.intake:
                        self.intake(row)
                    if getattr(row, 'kind', '') == 'self_model_discrepancy':
                        monitor_discrepancies += 1
                        if self.merge_gate is not None and observation.signal.startswith('merge_gate.'):
                            self.merge_gate.report_anomaly('measured discrepancy in gate inputs', now=now)
            chosen = self.controller.experiment(data.state, forecasts, action=data.actions[choice.experiment.id],
                snapshot=data.snapshot, reference_actions=data.reference_actions, relationships=data.relationships,
                protected_manifest=data.protected_manifest, now=now)
            choice, prediction = chosen
            if prediction.simulation.risk.decision != 'proceed':
                used = 0.
                return self._wait('risk', '; '.join(prediction.simulation.risk.reasons), now=now)
            if self.stopped() or not authority(self.config).permits('research'):
                return TickResult('stopped')
            result = self.execute(choice, prediction, data.snapshot, stop=self.stopped)
            finished = clock(self.clock())
            if type(result.used) not in (int, float) or not math.isfinite(result.used) or not 0 <= result.used <= data.worst_case:
                raise ValueError('measured spend must fit the reserved worst case')
            used = result.used
            scores = self.meta.score(prediction.decision_id, result.outcome, now=finished)
            for failure in scores.failures:
                if self.intake and not self.stopped():
                    self.intake(failure)
            rollbacks = []
            if self.merge_gate is not None:
                for signal, signature in result.regressions:
                    if self.stopped():
                        break
                    rollbacks.append(self.merge_gate.regression(signal, signature=signature,
                        now=finished).model_dump(mode='json'))
            pr, merge = '', None
            if result.change is not None and not self.stopped():
                grants = authority(self.config)
                if grants.permits('push') and grants.permits('draft_pr'):
                    from g6.self_training.merge_gate import ProtectedSet
                    labels = ('protected-change',) if ProtectedSet.load().hits(result.change.files) else ()
                    pr = self.repository.propose(result.change, labels=labels)
                    if result.merge_request is not None and self.merge_gate is not None:
                        if result.merge_request.change.sha256 != result.change.sha256:
                            raise ValueError('merge request differs from the proposed change')
                        if result.merge_request.change.claim == 'rollback':
                            if result.rollback_trigger is None:
                                raise ValueError('rollback merge needs its measured signed trigger')
                            signal, signature = result.rollback_trigger
                            merge = self.merge_gate.merge_rollback(result.merge_request, signal=signal,
                                signature=signature, now=finished).model_dump(mode='json')
                        else:
                            merge = self.merge_gate.merge(result.merge_request, now=finished).model_dump(mode='json')
            diagnosis = diagnose_runner_tick(dict(result.rca) | {'outcome': 'completed'})
            self._append('cycle', dict(at=now, heartbeat=finished, snapshot_sha256=data.snapshot.sha256(now=now),
                bottleneck=constraint.bottleneck, strategy=data.strategy, experiment=choice.experiment.id,
                prediction=prediction.decision_id, scores=[s.model_dump(mode='json') for s in scores.scores],
                calibration=[asdict(c) for c in scores.calibration], failures=[f.model_dump(mode='json') for f in scores.failures],
                rca=asdict(diagnosis), library_changes=list(result.library_changes),
                monitor_discrepancies=monitor_discrepancies,
                budget={'provider': data.provider, 'reserved': data.worst_case, 'used': used,
                    'target': getattr(admission, 'target', 'unknown'), 'ceiling': getattr(admission, 'ceiling', 'unknown')},
                pr=pr, merge=merge, rollbacks=rollbacks))
            return TickResult('completed', pr=pr)
        except Exception as exc:
            self._append('crash', dict(at=now, heartbeat=clock(self.clock()), exception=type(exc).__name__,
                experiment=choice.experiment.id, budget_reserved=data.worst_case))
            raise
        finally:
            self.budget.settle(admission, used)

    step = tick

    def daily_digest(self, *, now):
        now = clock(now)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        self.journal.reload()
        rows = self.journal.entries()
        cycles = [r['body'] for r in rows if r['kind'] == 'cycle'
                  and datetime.fromtimestamp(r['body']['at'], timezone.utc).date().isoformat() == day]
        text = [f'# G6 research digest {day}', '', f'Ticks: {len(cycles)}',
                f'Prediction hit rates: {self.meta.hit_rates()}', '']
        for row in cycles:
            text += [f'- Experiment {row["experiment"]}; constraint {row["bottleneck"]}; '
                     f'budget {row["budget"]}; library changes {row["library_changes"]}; '
                     f'discrepancies {row["monitor_discrepancies"]}; failed predictions {len(row["failures"])}; '
                     f'draft {row["pr"]}; merge/certificate {row["merge"]}; '
                     f'rollback proposals {row["rollbacks"]}']
        text += ['', 'Owner waits: protected changes, sign-in mount, bot/ruleset/CODEOWNERS, issuance and releases.', '']
        body = '\n'.join(text)
        published = [r['body'] for r in rows if r['kind'] == 'digest' and r['body']['day'] == day]
        if published:
            return Digest(published[-1]['body'], published[-1]['pr'])
        grants = authority(self.config)
        if self.stopped() or not all(grants.permits(a) for a in ('digest', 'push', 'draft_pr')):
            return Digest(body)
        if any(r['kind'] == 'digest_pending' and r['body']['day'] == day for r in rows):
            return Digest(body)  # ambiguous previous publication requires host reconciliation
        self._append('digest_pending', {'day': day, 'at': now})
        pr = self.repository.publish_digest(day, body)
        self._append('digest', {'day': day, 'at': now, 'body': body, 'pr': pr})
        return Digest(body, pr)
