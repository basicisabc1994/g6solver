# SPDX-License-Identifier: Apache-2.0
"""Inventory all meta splits and public evidence boundaries for unit custody."""
from dataclasses import replace
import re

import pytest

from g6.core.ledger import ChainLedger, digest
from g6.core.llm import FakeLLM
from g6.distill.meta import policy_gate as gate
from g6.distill.meta.records import ReplaySequence, SynthesisPolicy
from g6.distill.records import FailureRecord
from g6.metaharness.schema import Model, Profile, SuiteCase
from tests.distill.test_records import bodies
from tests.metaharness._rig import Clock, build

SPLITS = ('dev', 'held_out', 'selection')


def binding(workflow, method_ids=('original-method',)):
    return gate.FrozenMetaEvaluation.freeze(workflow, confirmation={
        'workflow_digest': workflow.digest(), 'held_out_digest': workflow.suite.digest('held_out'),
        'judging_sha256': digest('judge')}, method_case_ids=method_ids, journal=ChainLedger())


def declared_workflow():
    workflow, _ = build(n_dev=1, n_held=30)
    suite = replace(workflow.suite, selection=(SuiteCase('s0', 'planning', '1'),))
    suite = replace(suite, **{split: tuple(replace(c, meta={
        'unit_id': f'{split}-{i}', 'case_ids': (c.case_id,)}) for i, c in enumerate(getattr(suite, split)))
        for split in SPLITS})
    workflow = replace(workflow, suite=suite)
    return seal(workflow)


def seal(workflow):
    custody = {split: [{'case_id': c.case_id, 'unit_id': c.meta.get('unit_id'),
                        'case_ids': list(c.meta.get('case_ids', (c.case_id,)))}
                       for c in getattr(workflow.suite, split)] for split in SPLITS}
    return replace(workflow, suite=replace(workflow.suite, provenance={
        **workflow.suite.provenance, 'unit_custody_sha256': digest(custody)}))


@pytest.mark.parametrize('split', SPLITS)
@pytest.mark.parametrize('bad', ['missing', 'empty', 'duplicate', 'overlap', 'unbound'])
def test_every_split_refuses_invalid_units_before_factory(split, bad):
    workflow = declared_workflow()
    cases = list(getattr(workflow.suite, split))
    if bad == 'unbound':
        workflow = replace(workflow, suite=replace(workflow.suite, provenance={
            k: v for k, v in workflow.suite.provenance.items() if k != 'unit_custody_sha256'}))
    else:
        meta = dict(cases[0].meta)
        if bad == 'missing':
            meta.pop('unit_id')
        elif bad == 'empty':
            meta['unit_id'] = '   '
        elif bad == 'duplicate':
            cases.append(replace(cases[0], case_id='duplicate', input='other input'))
        else:
            other = next(s for s in SPLITS if s != split)
            meta['unit_id'] = getattr(workflow.suite, other)[0].meta['unit_id']
        cases[0] = replace(cases[0], meta=meta)
        workflow = seal(replace(workflow, suite=replace(workflow.suite, **{split: tuple(cases)})))
    frozen = binding(workflow)
    base = Profile(routing={'answer': 'local'}, prompt=SynthesisPolicy().model_dump_json())
    proposal = gate.policy_proposal(base, SynthesisPolicy(try_predicate=False), proposer='M')
    report = gate.evaluate_policy(workflow, base, proposal,
        lambda ctx: pytest.fail('invalid units reached replay'), frozen=frozen, reuse_index=1, clock=Clock())
    assert report.n == 0
    assert 'unit' in ' '.join(report.notes)
    assert gate.judge_policy(report, proposal, workflow, base=base, frozen=frozen,
                             judging_sha256=digest('judge')).verdict != 'recommend'


@pytest.mark.parametrize('split', SPLITS)
@pytest.mark.parametrize('original', ['sequence', 'failure'])
def test_all_original_case_ids_survive_pooling(split, original):
    failures = {s: FailureRecord(**(bodies()[0][1] | {'case_ref': digest(s), 'provenance': {
        'unit_id': f'bug-{s}', 'cohort': 'development'}})) for s in SPLITS}
    sequences = {s: ReplaySequence(case_id=f'source-{s}', failure_ids=(f.record_id,), utility_target=0.)
                 for s, f in failures.items()}
    replay = gate.SynthesisReplay(failures=failures.values(), replay=lambda *args: None)
    kwargs = {s: (seq,) for s, seq in sequences.items()}
    workflow = replay.workflow(name='source-separation', model=Model(FakeLLM(default='unused')), **kwargs)
    method = sequences[split].case_id if original == 'sequence' else failures[split].case_ref
    with pytest.raises(ValueError, match='separate|overlap'):
        binding(workflow, (method,))


@pytest.mark.parametrize('split', SPLITS)
def test_unit_metadata_cannot_change_after_freeze(split):
    workflow = declared_workflow()
    frozen = binding(workflow)
    cases = list(getattr(workflow.suite, split))
    cases[0] = replace(cases[0], meta={**cases[0].meta, 'unit_id': 'renamed-after-freeze'})
    changed = replace(workflow, suite=replace(workflow.suite, **{split: tuple(cases)}))
    with pytest.raises(ValueError, match='frozen|changed|custody'):
        frozen.reserve(changed, reuse_index=1)
    assert not frozen.journal.entries()


def test_valid_declared_units_are_counted_once_by_real_evaluator():
    workflow = declared_workflow()
    frozen = binding(workflow)
    base = Profile(routing={'answer': 'local'}, prompt=SynthesisPolicy().model_dump_json())
    proposal = gate.policy_proposal(base, SynthesisPolicy(try_predicate=False), proposer='M')
    report = gate.evaluate_policy(workflow, base, proposal, lambda ctx: lambda contract: '1',
                                  frozen=frozen, reuse_index=1, clock=Clock())
    assert report.n == 30
    assert len({c['case_id'] for c in report.cases}) == 30
    assert 'paired by bug unit' in report.method


@pytest.mark.parametrize('bad', ['missing', 'repeated', 'unbound'])
def test_public_judgment_cannot_use_a_raw_case_report(bad):
    workflow = declared_workflow()
    if bad == 'unbound':
        workflow = replace(workflow, suite=replace(workflow.suite, provenance={
            k: v for k, v in workflow.suite.provenance.items() if k != 'unit_custody_sha256'}))
    else:
        rows = tuple(replace(c, meta={} if bad == 'missing' else {'unit_id': 'one-bug'})
                     for c in workflow.suite.held_out)
        workflow = seal(replace(workflow, suite=replace(workflow.suite, held_out=rows)))
    frozen = binding(workflow)
    base = Profile(routing={'answer': 'local'}, prompt=SynthesisPolicy().model_dump_json())
    proposal = gate.policy_proposal(base, SynthesisPolicy(try_predicate=False), proposer='M')
    def factory(context):
        def execute(contract):
            a, b = map(int, re.search(r'(\d+)\+(\d+)', contract.task).groups())
            wrong = SynthesisPolicy.model_validate_json(context.profile.prompt).try_predicate and a % 2 == 0
            return str(a + b + int(wrong))
        return execute
    report = gate.evaluate(workflow, base, proposal.candidate, factory, clock=Clock())
    # Establish that the unmodified evaluator/judge see a case-based win here.
    assert gate.decide(report, proposal, workflow, base=base, confirmed=frozen.confirmation,
                       judging_sha256=digest('judge')).verdict == 'recommend'
    assert gate.judge_policy(report, proposal, workflow, base=base, frozen=frozen,
                             judging_sha256=digest('judge')).verdict != 'recommend'
