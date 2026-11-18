# SPDX-License-Identifier: Apache-2.0
"""Acceptance: missing custody, stale/exact evidence and restart state cannot grant a merge."""
import hashlib
import hmac
import importlib
import json
from pathlib import Path

import pytest

from g6.core.ledger import canonical, digest


def api():
    path = Path(__file__).parents[2] / 'src/g6/self_training/merge_gate.py'
    assert path.is_file(), 'host merge gate is missing'
    return importlib.import_module('g6.self_training.merge_gate')


class Host:
    """Test-only signing service; the product never owns this key."""
    def sign(self, payload):
        return hmac.new(b'fixture-only-key', payload.encode(), hashlib.sha256).hexdigest()

    def verify(self, payload, signature):
        return hmac.compare_digest(self.sign(payload), signature)


class Repository:
    def __init__(self):
        self.effects = []
        self.stop = False

    def stopped(self):
        return self.stop

    def merge_exact(self, change):
        self.effects.append(('merge', change.merge_commit))
        return change.merge_commit

    def revert_pr(self, change, *, evidence, key):
        self.effects.append(('revert', key))
        return 'https://example.test/revert/1'


def fixture(tmp_path=None, **change_fields):
    m = api()
    host, repo = Host(), Repository()
    change = m.Change(pr=7, branch='claude/g6-auto/20261118-example', parent_commit='a' * 40,
        head_commit='b' * 40, merge_commit='c' * 40, tree_sha256=digest('tree'), claim='improvement',
        author_family='claude', author_model='fixture-claude',
        files=(m.FileChange(path='src/g6/distill/example.py', before='old\n', after='new\n'),), **change_fields)
    from g6.distill.merge_evidence_gate import FROZEN_POLICY, FROZEN_POLICY_SHA256, protected_set
    manifest = protected_set()
    evidence = dict(commit=change.merge_commit, change_sha256=change.sha256, measured_at=1000., expires_at=2000.,
        origin='host_measurement', manifest_sha256=manifest.sha256,
        ci=[dict(job=job, run_id='run-1', conclusion='success', commit=change.merge_commit) for job in m.REQUIRED_CI],
        review=dict(family='codex', model='fixture-codex', commit=change.merge_commit, clean=True,
            rounds=1, repairs=0, k_loop_receipt=digest('k'), search_receipt=digest('search'), findings=[]),
        evaluation=dict(split='held_out', policy_sha256=FROZEN_POLICY_SHA256,
            split_sha256=digest('split'), registration_sha256=digest('registration'), reuse_index=1,
            n=100, wins=20, losses=0, ties=80, p_value=2 ** -20, gate='recommend',
            gate_receipt=digest('frozen-gate'), independent=True, measures={
                name: dict(before=.5, after=.7 if name == 'success' else .5,
                    before_interval=[.4, .6], after_interval=[.6, .8] if name == 'success' else [.4, .6],
                    delta_upper95=.3 if name == 'success' else .01)
                for name in ('success', 'false_acceptance', 'false_rejection', 'cost')},
            receipts=[digest('measured-receipt')], full_suite=True),
        trust=dict(bot='g6-owner-bot', bot_is_owner_provisioned=True, workflows_write=False,
            ruleset_main=True, required_checks=[*m.REQUIRED_CI, 'g6-merge-gate'], no_bypass=True,
            pr_required=True, codeowners_complete=True, owner_review_required=True,
            status_host_only=True, isolated_mission=True))
    settings = dict(level='auto_merge', research=True, push=True, draft_pr=True,
        merge=True, container_signin=True, digest=True)
    config = m.Autonomy(**settings)
    if tmp_path is not None:
        config = tmp_path / 'autonomy.json'
        config.write_text(json.dumps(settings), encoding='utf-8')
    gate = m.MergeGate(home=tmp_path, config=config, host=host, repository=repo, manifest=manifest,
                       stop=lambda: False, policy=FROZEN_POLICY,
                       state_head='' if tmp_path is not None else None,
                       certificate_head='' if tmp_path is not None else None)
    return m, gate, host, repo, change, evidence


def request(m, host, change, evidence):
    parsed = m.HostEvidence.model_validate(evidence)
    return m.MergeRequest(change=change, evidence=parsed, signature=host.sign(canonical(parsed.model_dump(mode='json'))))


def test_complete_host_evidence_merges_and_issues_scoped_generation(tmp_path):
    m, gate, host, repo, change, evidence = fixture(tmp_path)
    result = gate.merge(request(m, host, change, evidence), now=1100.)
    assert result.status == 'merged'
    rows = gate.certificates.read()
    assert len(rows) == 1 and rows[0]['body']['merge_commit'] == 'c' * 40
    assert rows[0]['body']['evaluation']['n'] == 100
    assert 'no claim beyond these cases' in rows[0]['body']['scope']
    assert rows[0]['body']['does_not_show']
    assert repo.effects == [('merge', 'c' * 40)]


@pytest.mark.parametrize('path', ['src/g6/self_training/merge_gate.py', 'src/g6/core/llm.py',
    '.github/workflows/ci.yml', 'CODEOWNERS', 'validation/distill/held_out.json',
    'src/g6/self_model/risk_gate.py', 'tests/test_existing.py', '../outside.py', 'C:/outside.py'])
def test_protected_bound_and_ambiguous_diff_stays_draft(tmp_path, path):
    m, gate, host, repo, change, evidence = fixture(tmp_path)
    change = change.model_copy(update={'files': (m.FileChange(path=path, before='old', after='new'),)})
    evidence['change_sha256'] = change.sha256
    result = gate.merge(request(m, host, change, evidence), now=1100.)
    assert result.status == 'draft_only' and 'protected-change' in result.labels
    assert repo.effects == []


def test_brand_new_test_is_allowed_but_not_conftest_or_bound_file(tmp_path):
    m, gate, host, repo, change, evidence = fixture(tmp_path)
    for path, expected in [('tests/test_new.py', 'ready'), ('tests/conftest.py', 'draft_only'),
                            ('src/g6/core/test_new.py', 'draft_only')]:
        candidate = change.model_copy(update={'files': (m.FileChange(path=path, before=None,
            after='def test_new():\n    assert 1 + 1 == 2\n'),)})
        evidence['change_sha256'] = candidate.sha256
        assert gate.assess(request(m, host, candidate, evidence), now=1100.).status == expected


@pytest.mark.parametrize('mutation', ['red', 'skipped', 'missing', 'wrong_commit', 'same_family',
    'unresolved', 'missing_ruleset', 'missing_codeowners', 'bot_workflows', 'untrusted_status',
    'stale', 'predicted', 'no_gain', 'cost_regression', 'false_accept_regression', 'wrong_policy', 'spent'])
def test_incomplete_evidence_never_merges(tmp_path, mutation):
    m, gate, host, repo, change, e = fixture(tmp_path)
    if mutation in ('red', 'skipped'):
        e['ci'][0]['conclusion'] = 'failure' if mutation == 'red' else 'skipped'
    elif mutation == 'missing':
        e['ci'].pop()
    elif mutation == 'wrong_commit':
        e['ci'][0]['commit'] = 'f' * 40
    elif mutation == 'same_family':
        e['review']['family'] = 'claude'
    elif mutation == 'unresolved':
        e['review']['findings'] = [dict(id='f1', disposition='open', reason='', reviewer_accepted=False,
            reproduction_digest='', test_digest='', test_passed=False)]
    elif mutation in ('missing_ruleset', 'missing_codeowners', 'untrusted_status'):
        e['trust'][{'missing_ruleset': 'ruleset_main', 'missing_codeowners': 'codeowners_complete',
                    'untrusted_status': 'status_host_only'}[mutation]] = False
    elif mutation == 'bot_workflows':
        e['trust']['workflows_write'] = True
    elif mutation == 'stale':
        e['expires_at'] = 1050.
    elif mutation == 'predicted':
        e['origin'] = 'self_prediction'
    elif mutation == 'no_gain':
        e['evaluation']['wins'] = 0
        e['evaluation']['ties'] = 100
    elif mutation.endswith('regression'):
        e['evaluation']['measures']['cost' if mutation == 'cost_regression' else 'false_acceptance']['delta_upper95'] = .2
    elif mutation == 'wrong_policy':
        e['evaluation']['policy_sha256'] = digest('relaxed-policy')
    elif mutation == 'spent':
        e['evaluation']['reuse_index'] = 6
    assert gate.merge(request(m, host, change, e), now=1100.).status != 'merged'
    assert not repo.effects


def test_evidence_signature_binds_every_measured_field(tmp_path):
    m, gate, host, repo, change, e = fixture(tmp_path)
    req = request(m, host, change, e)
    altered = req.evidence.model_dump(mode='json')
    altered['evaluation']['n'] = 10000
    forged = req.model_copy(update={'evidence': m.HostEvidence.model_validate(altered)})
    assert gate.merge(forged, now=1100.).status == 'blocked'
    assert not repo.effects


@pytest.mark.parametrize('level', [None, 'off', 'draft_pr'])
def test_missing_or_disabled_authority_refuses_merge(tmp_path, level):
    m, gate, host, repo, change, e = fixture(tmp_path)
    if level is None:
        gate.config.unlink()
    else:
        gate.config.write_text(json.dumps({'level': level}), encoding='utf-8')
    assert gate.merge(request(m, host, change, e), now=1100.).status != 'merged'
    assert repo.effects == []


def test_rate_limit_survives_restart_and_clock_rollback(tmp_path):
    m, gate, host, repo, change, e = fixture(tmp_path)
    assert gate.merge(request(m, host, change, e), now=1100.).status == 'merged'
    restarted = m.MergeGate(home=tmp_path, config=gate.config, host=host, repository=repo,
                           manifest=gate.manifest, stop=lambda: False, policy=gate.policy,
                           state_head=gate.state.chain.head(), certificate_head=gate.certificates.chain.head())
    for now in (1099., 1101., 87499.):
        e['measured_at'], e['expires_at'] = now - 1, now + 100
        assert restarted.merge(request(m, host, change, e), now=now).status != 'merged'
    assert len(repo.effects) == 1


def test_edited_and_forged_certificate_chain_is_rejected(tmp_path):
    m, gate, host, repo, change, e = fixture(tmp_path)
    gate.merge(request(m, host, change, e), now=1100.)
    path = gate.certificates.path
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['body']['body']['scope'] = 'forged universal proof'
    path.write_text(canonical(raw) + '\n', encoding='utf-8')
    with pytest.raises(ValueError):
        gate.certificates.read()


def test_regression_opens_one_revert_and_cooldown_survives_restart(tmp_path):
    m, gate, host, repo, change, e = fixture(tmp_path)
    gate.merge(request(m, host, change, e), now=1100.)
    signal = dict(generation=1, commit=change.merge_commit, measured_at=1200., expires_at=1300.,
        metric='false_acceptance', delta_lower95=.2, baseline=.1, receipt=digest('regression'), origin='host_measurement')
    signature = host.sign(canonical(signal))
    first = gate.regression(signal, signature=signature, now=1201.)
    second = gate.regression(signal, signature=signature, now=1202.)
    assert first.status == second.status == 'revert_proposed'
    assert len([row for row in repo.effects if row[0] == 'revert']) == 1
    assert not gate.state.room(now=1202.)


def test_stop_before_merge_side_effect(tmp_path):
    m, gate, host, repo, change, e = fixture(tmp_path)
    repo.stop = True
    assert gate.merge(request(m, host, change, e), now=1100.).status == 'stopped'
    assert repo.effects == []


def test_merge_gate_does_not_import_predictions():
    import ast
    path = Path(__file__).parents[2] / 'src/g6/self_training/merge_gate.py'
    assert path.is_file(), 'host merge gate is missing'
    modules = [node.module or '' for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
               if isinstance(node, ast.ImportFrom)]
    assert not any(name.startswith(('g6.self_model', 'g6.distill.meta')) for name in modules)
