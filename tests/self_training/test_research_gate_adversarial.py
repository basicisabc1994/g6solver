# SPDX-License-Identifier: Apache-2.0
"""Counterexamples: rollback generations, interrupted attempts and external custody."""
from pathlib import Path
import ast

import pytest

from g6.core.ledger import ChainLedger, canonical, digest
from tests.self_training.test_research_merge_gate import fixture, request


def rebind(m, change, e, *, parent=None, suffix='d', now=100000.):
    change = change.model_copy(update={'parent_commit': parent or change.merge_commit,
        'head_commit': suffix * 40, 'merge_commit': suffix * 40})
    e = dict(e) | {'commit': change.merge_commit, 'change_sha256': change.sha256,
                  'measured_at': now - 1., 'expires_at': now + 100.}
    e['ci'] = [dict(j) | {'commit': change.merge_commit} for j in e['ci']]
    e['review'] = dict(e['review']) | {'commit': change.merge_commit}
    return change, e


def test_policy_and_judging_patterns_are_injected_from_the_host_bridge():
    root = Path(__file__).parents[2] / 'src/g6/self_training'
    # An upwards dependency through distill violates the existing component layer.
    for name in ('merge_gate.py', 'generation_gate.py', 'autonomy_gate.py'):
        tree = ast.parse((root / name).read_text(encoding='utf-8'))
        modules = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(n.startswith(('g6.distill', 'g6.metaharness', 'g6.self_model')) for n in modules)


def test_four_merges_per_rolling_week_and_daily_limit():
    m, gate, host, repo, change, e = fixture()
    for i, at in enumerate((1100., 87500., 173900., 260300.)):
        if i:
            change, e = rebind(m, change, e, suffix='def'[i - 1], now=at)
        assert gate.merge(request(m, host, change, e), now=at).status == 'merged'
    change, e = rebind(m, change, e, suffix='1', now=346700.)
    assert gate.merge(request(m, host, change, e), now=346700.).status == 'blocked'
    assert len(repo.effects) == 4


def test_interrupted_merge_retains_reservation_and_never_retries():
    m, gate, host, repo, change, e = fixture()
    def interrupted(change):
        raise RuntimeError('response lost after server accepted merge')
    repo.merge_exact = interrupted
    with pytest.raises(RuntimeError):
        gate.merge(request(m, host, change, e), now=1100.)
    e['measured_at'], e['expires_at'] = 199999., 200100.
    assert gate.merge(request(m, host, change, e), now=200000.).status == 'blocked'


def test_rehashed_forged_certificate_is_still_refused_by_host_signature():
    m, gate, host, repo, change, e = fixture()
    gate.merge(request(m, host, change, e), now=1100.)
    record = gate.certificates.read()[0]
    record['body']['scope'] = 'forged unlimited claim'
    altered = ChainLedger()
    altered.append('generation', digest(record), record)
    gate.certificates.chain = ChainLedger(validate=gate.certificates._validate)
    with pytest.raises(ValueError, match='signature'):
        gate.certificates.chain.append('generation', digest(record), record)


def test_rollback_uses_fast_gate_and_is_a_signed_generation_with_trigger():
    m, gate, host, repo, change, e = fixture()
    gate.merge(request(m, host, change, e), now=1100.)
    signal = dict(generation=1, commit=change.merge_commit, measured_at=1200., expires_at=2200.,
        metric='false_acceptance', delta_lower95=.2, baseline=.1, receipt=digest('regression'), origin='host_measurement')
    assert gate.regression(signal, signature=host.sign(canonical(signal)), now=1201.).status == 'revert_proposed'
    revert, re = rebind(m, change, e, suffix='d', now=1300.)
    revert = revert.model_copy(update={'claim': 'rollback', 'files': tuple(
        f.model_copy(update={'before': f.after, 'after': f.before}) for f in change.files)})
    re['change_sha256'] = revert.sha256
    re['review']['family'], re['review']['clean'] = 'claude', False
    re['evaluation']['wins'], re['evaluation']['ties'] = 0, 100
    result = gate.merge_rollback(request(m, host, revert, re), signal=signal,
        signature=host.sign(canonical(signal)), now=1300.)
    assert result.status == 'merged'
    generations = gate.certificates.read()
    assert len(generations) == 2 and generations[-1]['body']['kind'] == 'rollback'
    assert generations[-1]['body']['trigger']['receipt'] == digest('regression')
    assert not gate.state.room(now=1301.)


def test_attempt_burst_demotes_until_owner_reconciles():
    m, gate, host, repo, change, e = fixture()
    for i in range(5):
        assert gate.note_attempt(now=1100. + i) is (i < 4)
    assert gate.state.demoted()
    change, e = rebind(m, change, e, suffix='d', now=1100000.)
    assert gate.merge(request(m, host, change, e), now=1100000.).status != 'merged'


def test_merge_entry_point_records_burst_without_optional_caller_help():
    m, gate, host, repo, change, e = fixture()
    e['ci'][0]['conclusion'] = 'failure'
    for i in range(5):
        assert gate.merge(request(m, host, change, e), now=1100. + i).status != 'merged'
    assert gate.state.demoted()


def test_two_measured_rollbacks_demote_even_after_cooldown():
    m, gate, host, repo, change, evidence = fixture()
    for iteration, at in enumerate((1100., 270000.)):
        if iteration:
            change, evidence = rebind(m, change, evidence, suffix='e', now=at)
            evidence['review']['clean'], evidence['review']['family'] = True, 'codex'
            evidence['evaluation']['wins'], evidence['evaluation']['ties'] = 20, 80
        assert gate.merge(request(m, host, change, evidence), now=at).status == 'merged'
        signal = dict(generation=1 + iteration * 2, commit=change.merge_commit, measured_at=at + 1,
            expires_at=at + 1000, metric='false_acceptance', delta_lower95=.2, baseline=.1,
            receipt=digest(['regression', iteration]), origin='host_measurement')
        assert gate.regression(signal, signature=host.sign(canonical(signal)), now=at + 2).status == 'revert_proposed'
        revert, evidence = rebind(m, change, evidence, suffix='d' if not iteration else 'f', now=at + 3)
        revert = revert.model_copy(update={'claim': 'rollback', 'files': tuple(
            f.model_copy(update={'before': f.after, 'after': f.before}) for f in change.files)})
        evidence['change_sha256'] = revert.sha256
        assert gate.merge_rollback(request(m, host, revert, evidence), signal=signal,
            signature=host.sign(canonical(signal)), now=at + 3).status == 'merged'
        change = revert.model_copy(update={'claim': 'improvement'})
    assert gate.state.demoted() and not gate.state.room(now=2000000.)


def test_invalid_certificate_chain_latches_draft_authority():
    m, gate, host, repo, change, e = fixture()
    from g6.core.ledger import LedgerCorrupt
    def corrupt():
        raise LedgerCorrupt('generation signature failed')
    gate.certificates.read = corrupt
    assert gate.merge(request(m, host, change, e), now=1100.).status != 'merged'
    assert gate.state.demoted()


def test_signing_uses_the_same_detached_snapshot_as_the_merge_decision():
    m, gate, host, repo, change, e = fixture()
    req = request(m, host, change, e)
    verify = host.verify
    def concurrent_edit(payload, signature):
        valid = verify(payload, signature)
        req.evidence.evaluation.measures['cost'] = m.Measure(before=.5, after=.99,
            before_interval=(.4, .6), after_interval=(.9, 1.), delta_upper95=.5)
        return valid
    host.verify = concurrent_edit
    assert gate.merge(req, now=1100.).status == 'merged'
    assert gate.certificates.read()[0]['body']['evaluation']['measures']['cost']['after'] == .5


def test_fixed_review_finding_requires_its_test_in_the_exact_diff():
    m, gate, host, repo, change, e = fixture()
    e['review']['findings'] = [dict(id='r1', disposition='fixed', reason='reproduced and repaired',
        reviewer_accepted=True, reproduction_digest=digest('failed-at-base'), test_digest=digest('missing-test'),
        test_passed=True)]
    assert gate.assess(request(m, host, change, e), now=1100.).status == 'blocked'


def test_no_behaviour_change_uses_paired_monitor_noninferiority():
    m, gate, host, repo, change, e = fixture()
    change = change.model_copy(update={'claim': 'no_behaviour_change'})
    e['change_sha256'] = change.sha256
    e['evaluation'].update(split='monitor', gate='non_inferior', wins=0, losses=0, ties=100, p_value=1.)
    e['evaluation']['measures']['success'] = dict(before=.5, after=.5, before_interval=[.4, .6],
        after_interval=[.4, .6], delta_lower95=0., delta_upper95=0.)
    assert gate.assess(request(m, host, change, e), now=1100.).status == 'ready'
    e['evaluation']['full_suite'] = False
    assert gate.assess(request(m, host, change, e), now=1100.).status == 'blocked'


def test_missing_union_does_not_get_new_test_exception():
    m, gate, host, repo, change, e = fixture()
    gate.manifest = m.ProtectedSet((), (), complete=False)
    change = change.model_copy(update={'files': (m.FileChange(path='tests/test_new.py', before=None,
        after='def test_it():\n    assert True\n'),)})
    e['change_sha256'] = change.sha256
    assert gate.assess(request(m, host, change, e), now=1100.).status == 'draft_only'


@pytest.mark.parametrize('path', ['.git/config', 'tests/../src/g6/core/llm.py', 'src/g6/CORE./llm.py'])
def test_repository_metadata_and_platform_aliases_are_protected(path):
    m, gate, host, repo, change, e = fixture()
    change = change.model_copy(update={'files': (m.FileChange(path=path, before=None, after='bad\n'),)})
    e['change_sha256'] = change.sha256
    assert gate.assess(request(m, host, change, e), now=1100.).status == 'draft_only'
