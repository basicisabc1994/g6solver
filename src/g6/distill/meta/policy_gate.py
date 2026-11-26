# SPDX-License-Identifier: Apache-2.0
"""S policy proposals reuse the UNCHANGED evaluator and gate, with frozen confirmation.

Only the trusted evaluator creates this binding from an existing person-confirmed
workflow. Its oracle must measure library utility on a frozen sequence of failures,
separate from method-held-out cases. M can change the profile's policy data only.
The supplied executor factory is trusted replay code: it drives S plus acceptance
and routes synthesis calls through context callers for metaharness compute matching.
No approval, activation, evaluator change or model discovery happens here.
"""
from __future__ import annotations

from g6.core.decoder_limits import json_value as _decode_json

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Mapping

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.metaharness.evaluate import EvaluationReport, evaluate
from g6.metaharness.gate import decide
from g6.metaharness.schema import EvidencePolicy, Proposal, Suite, SuiteCase, Workflow, _source_sha
from g6.harness import Check
from g6.distill.records import FailureRecord
from g6.distill.intake import unit_cohort

from .records import ReplayMeasurement, ReplaySequence, SynthesisPolicy

# Owner-host runner bridge. Keep upwards judging dependencies in this existing
# protected module; planning/runner modules receive data and never edit judges.
FROZEN_MERGE_POLICY = EvidencePolicy()
FROZEN_MERGE_POLICY_SHA256 = digest(asdict(FROZEN_MERGE_POLICY))

_META_SPLITS = ('dev', 'held_out', 'selection')


def _unit_custody(workflow):
    """Bind metadata omitted by SuiteCase.ref without changing the fixed schema."""
    return {split: [{'case_id': c.case_id, 'unit_id': c.meta.get('unit_id'),
                     'case_ids': list(c.meta.get('case_ids', (c.case_id,)))}
                    for c in getattr(workflow.suite, split)] for split in _META_SPLITS}


def _meta_case_ids(workflow):
    ids = {c.case_id for split in _META_SPLITS for c in getattr(workflow.suite, split)}
    for rows in _unit_custody(workflow).values():
        for row in rows:
            ids.update(row['case_ids'])
    # Refused replay rows may have been omitted from the execution suite. Their
    # original identities still belong to this frozen registration.
    ids.update(_decode_json(workflow.suite.provenance.get('meta_case_ids', '[]')))
    return ids


def _unit_problems(workflow):
    custody = _unit_custody(workflow)
    problems, units, originals = [], {}, {}
    if workflow.suite.provenance.get('unit_custody_sha256') != digest(custody):
        problems.append('meta unit custody is unbound to the confirmed workflow; refused')
    for split, rows in custody.items():
        for row in rows:
            unit, ids = row['unit_id'], row['case_ids']
            if not isinstance(unit, str) or not unit.strip() or unit != unit.strip():
                problems.append(f'{split}: missing or ambiguous unit identity; refused')
                continue
            if unit in units:
                problems.append(f'{split}: repeated unit or unit overlap with {units[unit]}; refused')
            units[unit] = split
            if not ids or any(not isinstance(ident, str) or not ident.strip() for ident in ids):
                problems.append(f'{split}: missing original unit case identity; refused')
                continue
            for ident in ids:
                if ident in originals:
                    problems.append(f'{split}: original unit case overlap; refused')
                originals[ident] = split
    if workflow.suite.provenance.get('refused'):
        problems.append(workflow.suite.provenance['refused'])
    return problems


def merge_judging_patterns():
    from g6.metaharness.api import JUDGING_PATTERNS
    return JUDGING_PATTERNS


def diagnose_runner_tick(data):
    from g6.metaharness.tick_rca import TickRecord, tick_rca
    return tick_rca(TickRecord.from_dict(data))


def _measurement(output):
    try:
        ReplayMeasurement.model_validate_json(output)
        return True
    except (ValueError, TypeError):
        return False


def _utility_oracle(output, expected):
    row = ReplayMeasurement.model_validate_json(output)
    # Lambda belongs to the fixed evaluator, never SynthesisPolicy.
    return row.tp - 3 * row.fp > expected


class SynthesisReplay:
    """Evaluator-owned adapter from frozen FailureRecords to a metaharness workflow.

    ``replay(policy, failures, caller)`` is trusted, fixed code which runs S plus
    unchanged acceptance and independently measures the resulting library on fresh
    labelled field data or spent-published cases. It receives no utility target.
    Every synthesis send must use the supplied metaharness-metered caller (and S's
    usual weekly pacing reservation). The adapter never discovers a model or edits
    a production library; replay owns isolated experimental library state.
    """
    def __init__(self, *, failures, replay):
        rows = tuple(FailureRecord.model_validate_json(row.model_dump_json()) for row in failures)
        if (len({row.record_id for row in rows}) != len(rows)
                or any(row.split != 'training' or row.observed_by != 'independent' for row in rows)):
            raise ValueError('replay needs distinct independent training FailureRecords')
        self._failures = MappingProxyType({row.record_id: row.model_dump_json() for row in rows})
        self._replay = replay
        self._digest = digest({'replay_code': _source_sha(replay), 'failures': dict(self._failures)})

    def workflow(self, *, name, dev, held_out, model, selection=()):
        seen, custody, refused, original_cases = set(), {}, [], set()
        def cases(sequences, split):
            groups = {}
            for sequence in sequences:
                row = ReplaySequence.model_validate_json(sequence.model_dump_json())
                original_cases.add(row.case_id)
                source_ids = {row.case_id}
                units = set()
                for ident in row.failure_ids:
                    failure = FailureRecord.model_validate_json(self._failures[ident])
                    original_cases.add(failure.case_ref)
                    source_ids.add(failure.case_ref)
                    if ident in seen:
                        raise ValueError('repeated failure unit or overlap between frozen meta sequences')
                    seen.add(ident)
                    unit, _ = unit_cohort(failure)
                    if not unit or not unit.strip():
                        refused.append(f'{row.case_id}: missing unit identity; refused')
                    units.add(unit)
                # A target spanning bugs has no registered allocation per unit.
                if len(units) != 1 or None in units or any(not u.strip() for u in units):
                    refused.append(f'{row.case_id}: replay must name exactly one bug unit; refused')
                    continue
                unit = next(iter(units))
                if custody.setdefault(unit, split) != split:
                    refused.append(f'{row.case_id}: unit overlap between frozen meta splits; refused')
                    continue
                ids, targets, sources = groups.setdefault(unit, ([], [], set()))
                ids.extend(row.failure_ids)
                targets.append(row.utility_target)
                sources.update(source_ids)
            return tuple(SuiteCase(unit, canonical({'failure_ids': sorted(ids),
                'replay_sha256': self._digest}), sum(targets), {'unit_id': unit, 'case_ids': tuple(sorted(sources))})
                for unit, (ids, targets, sources) in sorted(groups.items()))
        development = cases(dev, 'dev')
        held = cases(held_out, 'held_out')
        planning = cases(selection, 'selection')
        provenance = {'dev': 'independent', 'held_out': 'independent'}
        provenance['meta_case_ids'] = canonical(sorted(original_cases))
        if refused:
            # Retain a reportable workflow, but no held-out execution or statistic.
            # The unchanged judge blocks its empty held-out suite.
            provenance['refused'] = canonical(refused)
            held = ()
        suite = Suite(development, held, provenance, planning)
        problems = suite.problems()
        if problems and not refused:
            raise ValueError('; '.join(why for _, why in problems))
        good = ReplayMeasurement(tp=1, fp=0, evidence_ref='a' * 64, origin='independent_field').model_dump_json()
        check = Check('independently measured library confusion counts and evidence reference', _measurement,
                      check_id='meta-library-measurement', known_good=good, known_bad=('', '{}'))
        workflow = Workflow(name=name, task='{input}', checks=(check,), oracle=_utility_oracle, suite=suite,
                            models={'declared': model}, steps=('synthesis',))
        # Suite provenance is part of the already-confirmed workflow digest.
        provenance['unit_custody_sha256'] = digest(_unit_custody(workflow))
        return workflow

    def factory(self, context):
        def execute(contract):
            request = _decode_json(contract.task)
            current = digest({'replay_code': _source_sha(self._replay), 'failures': dict(self._failures)})
            if set(request) != {'failure_ids', 'replay_sha256'} or request['replay_sha256'] != current:
                raise ValueError('frozen replay code or failure snapshot changed')
            policy = SynthesisPolicy.model_validate_json(context.profile.prompt)
            failures = tuple(FailureRecord.model_validate_json(self._failures[ident]) for ident in request['failure_ids'])
            result = self._replay(policy, failures, context.caller('synthesis'))
            return ReplayMeasurement.model_validate_json(result.model_dump_json()).model_dump_json()
        return execute


@dataclass(frozen=True)
class FrozenMetaEvaluation:
    workflow_digest: str
    held_out_digest: str
    judging_sha256: str
    method_case_ids: tuple[str, ...]
    journal: ChainLedger
    unit_custody_sha256: str

    @property
    def confirmation(self) -> dict:
        return {'workflow_digest': self.workflow_digest, 'held_out_digest': self.held_out_digest,
                'judging_sha256': self.judging_sha256}

    @classmethod
    def freeze(cls, workflow, *, confirmation: Mapping, method_case_ids, journal=None):
        if (confirmation.get('workflow_digest') != workflow.digest()
                or confirmation.get('held_out_digest') != workflow.suite.digest('held_out')
                or not confirmation.get('judging_sha256')):
            raise ValueError('existing confirmation must bind the frozen workflow and held-out split')
        method_ids = tuple(method_case_ids)
        if not method_ids or set(method_ids) & _meta_case_ids(workflow):
            raise ValueError('meta cases must be separate from method cases; no overlap')
        if journal is None:
            journal = ChainLedger(state_path('distill', 'meta', 'evaluations.jsonl'))
        return cls(workflow.digest(), workflow.suite.digest('held_out'), confirmation['judging_sha256'],
                   method_ids, journal, digest(_unit_custody(workflow)))

    def check(self, workflow):
        if (workflow.digest() != self.workflow_digest
                or digest(_unit_custody(workflow)) != self.unit_custody_sha256):
            raise ValueError('frozen workflow or unit custody changed')
        if set(self.method_case_ids) & _meta_case_ids(workflow):
            raise ValueError('meta cases must be separate from method cases; no overlap')

    def reserve(self, workflow, *, reuse_index):
        self.check(workflow)
        self.journal.reload()
        # Spending is by split, even if a later registration changes another workflow field.
        used = sum(row['kind'] == 'meta_evaluation' and row['body']['split_digest'] == self.held_out_digest
                   for row in self.journal.entries())
        if type(reuse_index) is not int or reuse_index != used + 1 or reuse_index > workflow.policy.max_evaluations:
            raise ValueError('frozen split spent, stale index or evaluation limit exceeded')
        body = {'workflow_digest': self.workflow_digest, 'split_digest': self.held_out_digest,
                'reuse_index': reuse_index}
        if self.journal.path is not None:
            self.journal.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal.append('meta_evaluation', digest(body), body)


def policy_proposal(base, policy: SynthesisPolicy, *, proposer: str) -> Proposal:
    policy = SynthesisPolicy.model_validate_json(policy.model_dump_json())
    candidate = base.model_copy(update={'prompt': policy.model_dump_json()})
    return Proposal(base_digest=base.digest(), candidate=candidate, proposer=proposer,
                    strategy='synthesis_policy', claim={'success': 'up'})


def _policy_only(proposal, base):
    SynthesisPolicy.model_validate_json(base.prompt)
    SynthesisPolicy.model_validate_json(proposal.candidate.prompt)
    if (proposal.strategy != 'synthesis_policy' or proposal.base_digest != base.digest()
            or proposal.candidate.model_dump(exclude={'prompt'}) != base.model_dump(exclude={'prompt'})):
        raise ValueError('M may propose only SynthesisPolicy data; other profile fields remain frozen')


def evaluate_policy(workflow, base, proposal, factory, *, frozen: FrozenMetaEvaluation, reuse_index: int, **kwargs):
    _policy_only(proposal, base)
    if set(kwargs) - {'clock'}:
        raise ValueError('only an explicit clock may be supplied; evaluation split and budgets stay frozen')
    frozen.reserve(workflow, reuse_index=reuse_index)
    problems = _unit_problems(workflow)
    if problems:
        # No replay, oracle or statistic is run for unbound or repeated units.
        # The original judge already refuses a partial, zero-observation report.
        return EvaluationReport(workflow_digest=workflow.digest(), split='held_out',
            split_digest=workflow.suite.digest('held_out'), base_digest=base.digest(),
            candidate_digest=proposal.candidate.digest(), n=0, cases=[], estimates={}, success={},
            false_accepts={'base': 0, 'candidate': 0}, negative_control={}, control=None, power={},
            partial=True, stopped='meta unit custody refused', model_calls_used=0,
            budget=workflow.policy.max_eval_model_calls, reuse_index=reuse_index,
            provenance=workflow.suite.provenance.get('held_out', 'caller'),
            method='no unit evidence: refused before evaluation', notes=problems)
    report = evaluate(workflow, base, proposal.candidate, factory, split='held_out', reuse_index=reuse_index, **kwargs)
    report.method = 'paired by bug unit; cases pooled within each unit before the unchanged evaluator'
    if workflow.suite.provenance.get('refused'):
        report.notes.append(workflow.suite.provenance['refused'])
    return report


def rank_policies(workflow, base, policies, factory, *, frozen: FrozenMetaEvaluation, **kwargs):
    """Reuse metaharness's selection-split successive-halving planner; no gate look.

    Selection outcomes guide proposals only. They cannot become a held-out report.
    A caller needing more compute changes a future registration, never this one.
    """
    from g6.metaharness.select import race
    frozen.check(workflow)
    if not workflow.suite.selection:
        raise ValueError('policy planning needs a separate frozen selection split')
    if set(kwargs) - {'clock'}:
        raise ValueError('selection budgets and cases stay frozen')
    profiles = tuple(policy_proposal(base, policy, proposer='M').candidate for policy in policies)
    return race(workflow, factory, base, profiles, budget=workflow.policy.max_eval_model_calls, **kwargs)


def judge_policy(report, proposal, workflow, *, base, frozen: FrozenMetaEvaluation, judging_sha256):
    _policy_only(proposal, base)
    # Preserve the original standard-change decision for changed workflows. Unit
    # metadata alone is absent from that digest and must be checked here too.
    if workflow.digest() == frozen.workflow_digest:
        frozen.check(workflow)
        problems = _unit_problems(workflow)
        if problems and not (report.partial and report.n == 0 and not report.cases):
            from dataclasses import replace
            report = replace(report, n=0, cases=[], estimates={}, success={}, negative_control={},
                             control=None, power={}, partial=True, stopped='meta unit custody refused',
                             notes=[*report.notes, *problems])
    # In particular, a lowered threshold is handed to the original gate with the
    # ORIGINAL confirmation; STANDARD_CHANGED blocks it before any statistic.
    return decide(report, proposal, workflow, base=base, confirmed=frozen.confirmation,
                  judging_sha256=judging_sha256)
