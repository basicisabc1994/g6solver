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

from dataclasses import asdict, dataclass
import json
from types import MappingProxyType
from typing import Mapping

from g6.core.ledger import ChainLedger, canonical, digest
from g6.core.storage import state_path
from g6.metaharness.evaluate import evaluate
from g6.metaharness.gate import decide
from g6.metaharness.schema import EvidencePolicy, Proposal, Suite, SuiteCase, Workflow, _source_sha
from g6.harness import Check
from g6.distill.records import FailureRecord

from .records import ReplayMeasurement, ReplaySequence, SynthesisPolicy

# Owner-host runner bridge. Keep upwards judging dependencies in this existing
# protected module; planning/runner modules receive data and never edit judges.
FROZEN_MERGE_POLICY = EvidencePolicy()
FROZEN_MERGE_POLICY_SHA256 = digest(asdict(FROZEN_MERGE_POLICY))


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
        seen = set()
        def cases(sequences):
            values = []
            for sequence in sequences:
                row = ReplaySequence.model_validate_json(sequence.model_dump_json())
                for ident in row.failure_ids:
                    failure = FailureRecord.model_validate_json(self._failures[ident])
                    if failure.case_ref in seen:
                        raise ValueError('repeated failure unit or overlap between frozen meta sequences')
                    seen.add(failure.case_ref)
                values.append(SuiteCase(row.case_id, canonical({'failure_ids': row.failure_ids,
                    'replay_sha256': self._digest}), row.utility_target))
            return tuple(values)
        suite = Suite(cases(dev), cases(held_out), {'dev': 'independent', 'held_out': 'independent'}, cases(selection))
        problems = suite.problems()
        if problems:
            raise ValueError('; '.join(why for _, why in problems))
        good = ReplayMeasurement(tp=1, fp=0, evidence_ref='a' * 64, origin='independent_field').model_dump_json()
        check = Check('independently measured library confusion counts and evidence reference', _measurement,
                      check_id='meta-library-measurement', known_good=good, known_bad=('', '{}'))
        return Workflow(name=name, task='{input}', checks=(check,), oracle=_utility_oracle, suite=suite,
                        models={'declared': model}, steps=('synthesis',))

    def factory(self, context):
        def execute(contract):
            request = json.loads(contract.task)
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
        if not method_ids or set(method_ids) & {c.case_id for c in (*workflow.suite.dev, *workflow.suite.held_out,
                                                                  *workflow.suite.selection)}:
            raise ValueError('meta cases must be separate from method cases; no overlap')
        if journal is None:
            journal = ChainLedger(state_path('distill', 'meta', 'evaluations.jsonl'))
        return cls(workflow.digest(), workflow.suite.digest('held_out'), confirmation['judging_sha256'],
                   method_ids, journal)

    def reserve(self, workflow, *, reuse_index):
        if workflow.digest() != self.workflow_digest:
            raise ValueError('frozen workflow changed')
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
    return evaluate(workflow, base, proposal.candidate, factory, split='held_out', reuse_index=reuse_index, **kwargs)


def rank_policies(workflow, base, policies, factory, *, frozen: FrozenMetaEvaluation, **kwargs):
    """Reuse metaharness's selection-split successive-halving planner; no gate look.

    Selection outcomes guide proposals only. They cannot become a held-out report.
    A caller needing more compute changes a future registration, never this one.
    """
    from g6.metaharness.select import race
    if workflow.digest() != frozen.workflow_digest:
        raise ValueError('frozen workflow changed')
    if not workflow.suite.selection:
        raise ValueError('policy planning needs a separate frozen selection split')
    if set(kwargs) - {'clock'}:
        raise ValueError('selection budgets and cases stay frozen')
    profiles = tuple(policy_proposal(base, policy, proposer='M').candidate for policy in policies)
    return race(workflow, factory, base, profiles, budget=workflow.policy.max_eval_model_calls, **kwargs)


def judge_policy(report, proposal, workflow, *, base, frozen: FrozenMetaEvaluation, judging_sha256):
    _policy_only(proposal, base)
    # In particular, a lowered threshold is handed to the original gate with the
    # ORIGINAL confirmation; STANDARD_CHANGED blocks it before any statistic.
    return decide(report, proposal, workflow, base=base, confirmed=frozen.confirmation,
                  judging_sha256=judging_sha256)
