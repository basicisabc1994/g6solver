# SPDX-License-Identifier: Apache-2.0
"""Protected risk policy. Data in, routing out; no authority grant or action executor.

Gate readouts are the existing APIs' serialised results. Readers outside this
package call those APIs and assemble the merge gate's union manifest. Provenance
here is declared, not authenticated. Self-predictions can never clear this gate.
"""
from __future__ import annotations

from collections.abc import Mapping
from fnmatch import fnmatchcase
from typing import Literal

from g6.core.binomial import upper_bound
from g6.self_model.model import Data, Field, UNKNOWN, clock
from g6.self_model.monitor import measured

MAX_REGRESSION = .2
MAX_AFFECTED = 6
ACTION_SOURCE = 'g6.action_gating.evaluate_action'
PLAN_SOURCE = 'g6.navigator.check_plan'


def _reject_predictions(value):
    if getattr(value, 'origin', None) == 'self_prediction':
        raise ValueError('a self prediction is not a measured gate readout')
    if isinstance(value, Mapping):
        if value.get('origin') == 'self_prediction':
            raise ValueError('a self prediction is not a measured gate readout')
        for item in value.values():
            _reject_predictions(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _reject_predictions(item)


class GateReadout(Data):
    action_sha256: str
    action_id: str
    decision: Field
    reversibility: Field
    external_effect: Field

    @classmethod
    def from_evaluation(cls, action_sha256: str, evaluation: Mapping, *, now: float, ttl_s: float):
        """Adapt evaluate_action.to_response_dict(), never a simulation."""
        _reject_predictions(evaluation)
        capsule, decision = evaluation['action_capsule'], evaluation['gate_decision']
        metadata = dict(source=ACTION_SOURCE, observed_at=now, ttl_s=ttl_s)
        reverse = capsule.get('reversibility', UNKNOWN)
        if capsule.get('irreversible_or_costly_to_reverse') is True:
            reverse = 'irreversible'
        flags = (capsule.get('external_side_effect'), capsule.get('real_world_effect'))
        external = True if True in flags else (False if all(item is False for item in flags) else UNKNOWN)
        return cls(action_sha256=action_sha256, action_id=capsule['action_id'],
                   decision=Field.measured(decision['decision'], **metadata),
                   reversibility=Field.measured(reverse, **metadata), external_effect=Field.measured(external, **metadata))


class PlanReadout(Data):
    action_sha256: str
    valid: Field
    effect: Field

    @classmethod
    def from_report(cls, action_sha256: str, report: Mapping, *, now: float, ttl_s: float):
        """Adapt check_plan(..., max_effect=...).to_dict(); never run_plan."""
        _reject_predictions(report)
        metadata = dict(source=PLAN_SOURCE, observed_at=now, ttl_s=ttl_s)
        valid = report['valid'] is True and report['issues'] == []
        return cls(action_sha256=action_sha256, valid=Field.measured(valid, **metadata),
                   effect=Field.measured(report['effect'], **metadata))


class ProtectedManifest(Data):
    """Reader's union of path fragments/globs and certificate-bound members.

    Module names must be converted to repository paths by the union reader.
    ``complete`` means all union sources were read, including certificates.
    """
    patterns: Field
    complete: Field


class RiskDecision(Data):
    decision: Literal['proceed', 'more_evidence', 'owner', 'block']
    score: float | str = UNKNOWN
    reasons: tuple[str, ...]
    protected_touched: tuple[str, ...] = ()


def _normal(path: str) -> str:
    low = path.replace('\\', '/').casefold()
    if not low or low.startswith('/') or ':' in low or any(part == '..' for part in low.split('/')):
        raise ValueError('path is absolute or ambiguous')
    normal = '/'.join(part for part in low.split('/') if part and part != '.')
    if not normal:
        raise ValueError('repository-wide scope is protected')
    return normal


def protected_touched(paths: tuple[str, ...], manifest: ProtectedManifest | None, *, now: float) -> tuple[str, ...]:
    """Missing union information protects every requested path (including none)."""
    try:
        if manifest is None or measured(manifest.complete, now=now) is not True:
            raise ValueError('complete union manifest required')
        patterns = measured(manifest.patterns, now=now)
        if not isinstance(patterns, tuple) or not patterns or not all(isinstance(p, str) and p.strip() for p in patterns):
            raise ValueError('union manifest patterns required')
        # Fixed additions from design section 8.3; no exception for new tests in
        # this slice, since the self-model cannot authenticate a diff as new.
        patterns += ('.github/', 'codeowners', 'validation/distill/', 'self_model/risk_gate.py',
                     'self_training/autonomy.json', 'self_training/merge_gate.py')
        found = []
        for path in paths:
            low = _normal(path)
            aliases = {low, low.removeprefix('src/'), low.removeprefix('src/g6/'), low.removeprefix('g6/')}
            if low in ('src', 'src/g6', 'g6'):
                found.append(path)
                continue
            for pattern in patterns:
                pattern = pattern.replace('\\', '/').casefold()
                if (fnmatchcase(low, pattern) or fnmatchcase(low, '*/' + pattern)
                        or (not any(char in pattern for char in '*?[') and pattern in low)
                        or any(pattern.rstrip('/') == alias or pattern.startswith(alias + '/') for alias in aliases)):
                    found.append(path)
                    break
        return tuple(found)
    except ValueError:
        return paths or ('unknown protected manifest',)


def _read(field: Field, source: str, now: float):
    if field.source != source:
        raise ValueError('readout must come from its existing gate API')
    return measured(field, now=now)


def assess(*, action_id: str, action_sha256: str, touched_paths: tuple[str, ...], affected: tuple[str, ...],
           manifest: ProtectedManifest | None, gate: GateReadout | None, plan: PlanReadout | None,
           has_plan: bool, regression_history: tuple[Field, ...], now: float) -> RiskDecision:
    """Risk uses measured reference outcomes, never a Simulation or Prediction.

    A small score grants nothing on its own: every existing gate readout must
    independently permit the proposed action. Unknowns cannot lower a hold.
    """
    clock(now)
    touched = protected_touched(touched_paths, manifest, now=now)
    if touched:
        return RiskDecision(decision='owner', reasons=('protected or unknown union manifest',), protected_touched=touched)
    try:
        if gate is None or gate.action_sha256 != action_sha256 or gate.action_id != action_id:
            raise ValueError('action-bound gate readout required')
        decision = _read(gate.decision, ACTION_SOURCE, now)
        if decision == 'block':
            return RiskDecision(decision='block', reasons=('existing action gate blocks',))
        try:
            reverse = _read(gate.reversibility, ACTION_SOURCE, now)
            external = _read(gate.external_effect, ACTION_SOURCE, now)
        except ValueError:
            return RiskDecision(decision='owner', reasons=('reversibility or external effect is unknown',))
        if external is not False or reverse != 'reversible' or decision != 'allow':
            return RiskDecision(decision='owner', reasons=('external, uncertain, irreversible or owner-gated action',))
        if has_plan:
            if plan is None or plan.action_sha256 != action_sha256:
                raise ValueError('action-bound navigator readout required')
            if _read(plan.valid, PLAN_SOURCE, now) is not True:
                return RiskDecision(decision='block', reasons=('navigator plan did not pass',))
            effect = _read(plan.effect, PLAN_SOURCE, now)
            if effect in ('external', 'irreversible'):
                return RiskDecision(decision='owner', reasons=('navigator reports external or irreversible effect',))
            if effect not in ('read', 'local_write'):
                raise ValueError('navigator side effect unknown')
        if not affected or len(set(affected)) > MAX_AFFECTED:
            return RiskDecision(decision='more_evidence', reasons=('affected set missing or above six; climb evidence ladder',))
        values = tuple(measured(item, now=now) for item in regression_history)
        if not all(type(value) is bool for value in values) or len(values) < 5:
            raise ValueError('at least five measured reference actions required')
        risk = upper_bound(sum(values), len(values), .05)
        if risk > MAX_REGRESSION:
            return RiskDecision(decision='owner', score=risk, reasons=('regression upper bound exceeds .2; more evidence first',))
        return RiskDecision(decision='proceed', score=risk, reasons=('measured gate readouts clear; risk within fixed bounds',))
    except ValueError as exc:
        return RiskDecision(decision='more_evidence', reasons=(str(exc),))
