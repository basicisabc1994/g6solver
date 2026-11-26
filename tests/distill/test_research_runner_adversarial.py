# SPDX-License-Identifier: Apache-2.0
"""Run boundaries, uncertain effects and default-off repository capabilities."""
from dataclasses import replace
from pathlib import Path
import importlib

import pytest

from g6.core.ledger import digest
from tests.distill.test_research_runner import fixture
from tests.self_training.test_research_merge_gate import fixture as merge_fixture


def test_absent_configuration_stops_before_reading_host_inputs():
    r, runner, inputs, calls, budget, repo = fixture()
    runner.config = Path(__file__).parents[2] / '.build/tmp/absent-runner-autonomy.json'
    runner.refresh = lambda now: pytest.fail('off runner must not refresh')
    assert runner.tick(now=100.).status == 'off'
    assert not calls and not budget.reservations and not repo.effects


def test_environment_kill_switch_halts_next_tick_without_logging(monkeypatch):
    r, runner, inputs, calls, budget, repo = fixture()
    monkeypatch.setenv('G6_STOP', 'owner kill switch')
    assert runner.tick(now=100.).status == 'stopped'
    assert not calls and not budget.reservations and not runner.journal.entries()


def test_underpowered_experiment_does_not_spend_or_record_a_prediction():
    r, runner, inputs, calls, budget, repo = fixture()
    runner.refresh = lambda now: replace(inputs, power={'separating': .49})
    assert runner.tick(now=100.).status == 'waiting'
    assert not budget.reservations and not runner.meta.predictions()


def test_runner_subordinates_experiments_to_the_measured_constraint():
    r, runner, inputs, calls, budget, repo = fixture()
    runner.refresh = lambda now: replace(inputs, experiment_stages={'separating': 'merge'})
    assert runner.tick(now=100.).status != 'completed'
    assert not calls and not budget.reservations


def test_repeated_constraint_requires_change_of_strategy_before_third_run():
    r, runner, inputs, calls, budget, repo = fixture()
    for at in (80., 90.):
        body = {'at': at, 'bottleneck': 'synthesis', 'strategy': 'template-first'}
        runner.journal.append('cycle', digest(body), body)
    assert runner.tick(now=100.).status == 'reframe'
    assert not calls and not budget.reservations


def test_mission_failure_is_logged_and_reservation_settled_conservatively():
    r, runner, inputs, calls, budget, repo = fixture()
    def crash(*args, **kwargs):
        raise RuntimeError('fixture execution failed')
    runner.execute = crash
    with pytest.raises(RuntimeError):
        runner.tick(now=100.)
    assert budget.settlements == [.01]
    assert runner.journal.entries()[-1]['kind'] == 'crash'


def test_uncertain_digest_publication_is_not_retried_or_double_logged():
    r, runner, inputs, calls, budget, repo = fixture(push=True, draft_pr=True, digest=True)
    runner.tick(now=100.)
    def uncertain(*args):
        raise RuntimeError('lost reply after publishing')
    repo.publish_digest = uncertain
    with pytest.raises(RuntimeError):
        runner.daily_digest(now=103.)
    assert runner.daily_digest(now=104.).pr == ''
    assert len([r for r in runner.journal.entries() if r['kind'] == 'digest_pending']) == 1


def test_repository_adapter_has_no_push_authority_by_default():
    a = importlib.import_module('g6.distill.repository_adapter')
    m, gate, host, repo, change, e = merge_fixture()
    commands = []
    adapter = a.GhRepository(root='/missions/root', command=lambda *a, **kw: commands.append(a),
        stop=lambda: False, session_trailers=('Co-Authored-By: Test <test@example.test>', 'Claude-Session: fixture'))
    with pytest.raises(ValueError, match='authority|off'):
        adapter.propose(change)
    assert commands == []


def test_draft_only_authority_never_pushes_a_result_change():
    r, runner, inputs, calls, budget, repo = fixture(draft_pr=True)
    m, gate, host, unused, change, e = merge_fixture()
    original = runner.execute
    runner.execute = lambda *a, **kw: replace(original(*a, **kw), change=change)
    assert runner.tick(now=100.).status == 'completed'
    assert not repo.effects


def test_changed_code_is_a_draft_and_protected_diffs_are_labelled():
    r, runner, inputs, calls, budget, repo = fixture(draft_pr=True, push=True)
    m, gate, host, unused, change, e = merge_fixture()
    change = change.model_copy(update={'files': (m.FileChange(path='src/g6/self_training/merge_gate.py',
        before='old\n', after='new\n'),)})
    original = runner.execute
    runner.execute = lambda *a, **kw: replace(original(*a, **kw), change=change)
    assert runner.tick(now=100.).pr == 'https://example.test/pr/1'
    assert repo.effects[0][2] == ('protected-change',)


def test_container_port_receives_only_checked_mission_plan():
    m = importlib.import_module('g6.distill.missions')
    mission = m.build_mission(family='codex', mission_id='run-1', worktree='/missions/run-1/worktree',
        home='/missions/run-1/home', image='g6-engineer@sha256:' + 'a' * 64, task='repair training case')
    seen = []
    executor = m.ContainerMissions(lambda plan, **kw: seen.append(plan) or {'result': 'fixture'},
                                   stop=lambda: False)
    assert executor.run(mission) == {'result': 'fixture'}
    assert seen[0].mcp_servers == (('g6', ('g6-mcp',)),)
    forged = replace(mission, privileged=True)
    with pytest.raises(ValueError):
        executor.run(forged)
    assert len(seen) == 1


def test_mission_supports_actual_windows_worktree_location_without_reading_signins():
    m = importlib.import_module('g6.distill.missions')
    plan = m.build_mission(family='claude', mission_id='run-2',
        worktree='C:/missions/repo/.claude/worktrees/auto-run2', home='C:/missions/clean/run2/home',
        image='g6-engineer@sha256:' + 'a' * 64, task='repair')
    assert plan.mounts[0].source.endswith('/.claude/worktrees/auto-run2')
    with pytest.raises(ValueError):
        m.build_mission(family='claude', mission_id='run-2', worktree='C:/Users/owner/.claude',
            home='C:/missions/clean/run2/home', image=plan.image, task='repair')


def test_runner_forwards_measured_monitor_regression_to_the_real_revert_gate():
    from tests.self_training.test_research_merge_gate import request
    from g6.core.ledger import canonical
    r, runner, inputs, calls, budget, repo = fixture(draft_pr=True, push=True)
    m, gate, host, repository, change, e = merge_fixture()
    e['measured_at'], e['expires_at'] = 0., 2000.
    assert gate.merge(request(m, host, change, e), now=10.).status == 'merged'
    signal = dict(generation=1, commit=change.merge_commit, measured_at=102., expires_at=200.,
        metric='false_acceptance', delta_lower95=.2, baseline=.1, receipt=digest('monitor-run'), origin='host_measurement')
    original = runner.execute
    runner.execute = lambda *a, **kw: replace(original(*a, **kw),
        regressions=((signal, host.sign(canonical(signal))),))
    runner.merge_gate = gate
    assert runner.tick(now=100.).status == 'completed'
    assert repository.effects[-1][0] == 'revert'
    assert runner.journal.entries()[-1]['body']['rollbacks'][0]['status'] == 'revert_proposed'


def test_measured_discrepancy_in_gate_inputs_only_lowers_autonomy():
    from g6.self_model.model import Field
    from g6.self_model.monitor import Observation, Expectation
    r, runner, inputs, calls, budget, repo = fixture()
    m, gate, host, repository, change, e = merge_fixture()
    def measured(value):
        return Field.measured(value, source='fixture.host.gate_monitor', observed_at=100., ttl_s=100.)
    observation = Observation(observation_id='ci-discrepancy-1', signal='merge_gate.ci', category='check_progress',
                              measurement=measured(.5))
    expectation = Expectation(signal='merge_gate.ci', lower=measured(0.), upper=measured(.1))
    runner.refresh = lambda now: replace(inputs, observations=((observation, expectation),))
    runner.merge_gate = gate
    assert runner.tick(now=100.).status == 'completed'
    assert gate.state.demoted()
    assert runner.journal.entries()[-1]['body']['monitor_discrepancies'] == 1


def test_daily_digest_appends_to_the_existing_remote_stream_on_later_days():
    from g6.self_training.autonomy_gate import Autonomy
    a = importlib.import_module('g6.distill.repository_adapter')
    commands = []
    def command(argv, **kwargs):
        commands.append(argv)
        return 'a' * 40 if argv[:2] == ('git', 'rev-parse') else 'https://example.test/pr/7'
    adapter = a.GhRepository(root='/missions/root', command=command, stop=lambda: False,
        session_trailers=('Co-Authored-By: Test <test@example.test>', 'Claude-Session: fixture'), pinned_issue=1,
        config=Autonomy(level='draft_pr', draft_pr=True, push=True, digest=True))
    adapter.publish_digest('2026-11-18', '# Day one\n')
    adapter.publish_digest('2026-11-19', '# Day two\n')
    assert len([c for c in commands if c[:2] == ('git', 'fetch')]) == 2
    assert not any(c[:3] == ('git', 'switch', '-c') for c in commands)
