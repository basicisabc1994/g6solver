# SPDX-License-Identifier: Apache-2.0
"""Runner acceptance through real M/simulation/scoring and fake external ports."""
from dataclasses import replace
from pathlib import Path
import importlib

import pytest

from g6.core.ledger import ChainLedger
from g6.distill.meta import MetaLedger
from g6.self_model.model import Field, NamedField, SelfModel
from tests.self_model.test_simulate import setup
from tests.distill.test_meta import experiment_fixture


def api():
    path = Path(__file__).parents[2] / 'src/g6/distill/runner.py'
    assert path.is_file(), 'autonomous runner is missing'
    return importlib.import_module('g6.distill.runner')


class Budget:
    def __init__(self, allowed=True):
        self.allowed, self.reservations, self.settlements = allowed, [], []

    def reserve(self, provider, now, worst_case, *, tick):
        from types import SimpleNamespace
        self.reservations.append((provider, worst_case, tick))
        return SimpleNamespace(admitted=self.allowed, reason='fixture reported limit', amount=worst_case)

    def settle(self, admission, used):
        self.settlements.append(used)


class Repo:
    def __init__(self):
        self.effects, self.stop = [], False

    def stopped(self):
        return self.stop

    def propose(self, change, *, labels):
        self.effects.append(('draft', change.sha256, labels))
        return 'https://example.test/pr/1'

    def publish_digest(self, day, body):
        self.effects.append(('digest', day, body))
        return 'https://example.test/digest/1'


def fixture(**settings):
    r = api()
    from g6.distill.meta import ExperimentForecast
    from g6.self_maintenance.constraints import StageMeasurement
    from g6.self_training.autonomy_gate import Autonomy
    sim = importlib.import_module('g6.self_model.simulate')
    action, refs, manifest, graph = setup(sim)
    action = action.model_copy(update={'action_id': 'separating:tick-1', 'kind': 'experiment_decide'})
    # GateReadout's action id/digest must bind the actual per-tick action.
    from g6.self_model.risk_gate import GateReadout
    gate = GateReadout.from_evaluation(action.sha256, {'action_capsule': {
        'action_id': action.action_id, 'reversibility': 'reversible', 'external_side_effect': False,
        'real_world_effect': False}, 'gate_decision': {'decision': 'allow'}}, now=100., ttl_s=100.)
    action = action.model_copy(update={'gate': gate})
    state, experiments = experiment_fixture()
    snapshot = SelfModel(state=tuple(NamedField(name=name, field=Field.measured(value,
        source='fixture.host', observed_at=100., ttl_s=100.)) for name, value in
        (('stop', False), ('budget_room', True), ('isolation', 'container'))))
    inputs = r.TickInputs(snapshot=snapshot, state=state,
        forecasts=(ExperimentForecast(experiments[0], ('a', 'a'), ('admit', 'admit')),
                   ExperimentForecast(experiments[1], ('a', 'b'), ('admit', 'refuse'))),
        actions={'separating': action}, reference_actions=tuple(ref.model_copy(update={'kind': action.kind}) for ref in refs),
        relationships=graph, protected_manifest=manifest,
        stages=(StageMeasurement('synthesis', 10, 2, 5., True), StageMeasurement('acceptance', 10, 8, 5., True)),
        power={'redundant': 1., 'separating': .8}, provider='claude', worst_case=.01, strategy='template-first',
        experiment_stages={'redundant': 'synthesis', 'separating': 'synthesis'})
    meta, budget, repo = MetaLedger(memory=True), Budget(), Repo()
    journal = ChainLedger()
    calls = []
    def execute(choice, prediction, snapshot, *, stop):
        assert meta.predictions()[0].decision_id == prediction.decision_id
        calls.append(choice.experiment.id)
        measured = Field.measured(14., source='fixture.independent_post_run', observed_at=102., ttl_s=100.)
        outcome = sim.Outcome(action_sha256=prediction.simulation.action_sha256, action_started_at=101.,
            action_finished_at=102., prediction_record=prediction.receipt,
            measurements=(NamedField(name='yield', field=measured),))
        return r.RunResult(outcome=outcome, used=.002)
    config = Autonomy(level='draft_pr', research=True, **settings)
    runner = r.Runner(config=config, refresh=lambda now: inputs, execute=execute, budget=budget,
        repository=repo, meta=meta, journal=journal, clock=lambda: 103., stop=lambda: False)
    return r, runner, inputs, calls, budget, repo


def test_tick_selects_information_records_before_run_and_scores_measured_outcome():
    r, runner, inputs, calls, budget, repo = fixture()
    result = runner.tick(now=100.)
    assert result.status == 'completed' and calls == ['separating']
    assert runner.meta.hit_rates() == {'experiment_decide': 1.}
    assert budget.settlements == [.002]
    cycle = runner.journal.entries()[-1]['body']
    assert cycle['bottleneck'] == 'synthesis' and cycle['snapshot_sha256'] == inputs.snapshot.sha256(now=100.)
    assert cycle['calibration'] and cycle['heartbeat'] == 103.
    assert not repo.effects


@pytest.mark.parametrize('condition', ['off', 'stop', 'budget', 'power', 'stale', 'risk'])
def test_no_run_when_authority_admission_power_or_self_model_blocks(condition):
    r, runner, inputs, calls, budget, repo = fixture()
    if condition == 'off':
        from g6.self_training.autonomy_gate import Autonomy
        runner.config = Autonomy()
    elif condition == 'stop':
        repo.stop = True
    elif condition == 'budget':
        budget.allowed = False
    elif condition == 'power':
        inputs = replace(inputs, power={'redundant': 1., 'separating': .49})
    elif condition == 'stale':
        inputs = replace(inputs, snapshot=SelfModel())
    elif condition == 'risk':
        inputs = replace(inputs, reference_actions=inputs.reference_actions[:4])
    runner.refresh = lambda now: inputs
    assert runner.tick(now=100.).status != 'completed'
    assert not calls and not repo.effects
    if condition in ('off', 'stop'):
        assert not budget.reservations and not runner.journal.entries()


def test_stop_file_halts_before_refresh_or_any_side_effect(tmp_path, monkeypatch):
    r, runner, inputs, calls, budget, repo = fixture()
    monkeypatch.setenv('G6_HOME', str(tmp_path))
    (tmp_path / 'STOP').write_text('owner stop\n', encoding='utf-8')
    runner.refresh = lambda now: pytest.fail('stop must precede refresh')
    assert runner.tick(now=100.).status == 'stopped'
    assert not budget.reservations and not runner.journal.entries() and not calls


def test_failed_prediction_is_logged_and_intaken_without_becoming_merge_evidence():
    r, runner, inputs, calls, budget, repo = fixture()
    original = runner.execute
    def misses(*args, **kw):
        result = original(*args, **kw)
        result.outcome.measurements[0].field  # actual measured result changes, not prediction
        measurement = Field.measured(10., source='fixture.independent_post_run', observed_at=102., ttl_s=100.)
        return replace(result, outcome=result.outcome.model_copy(update={'measurements': (
            NamedField(name='yield', field=measurement),)}))
    received = []
    runner.execute, runner.intake = misses, received.append
    assert runner.tick(now=100.).status == 'completed'
    assert runner.meta.hit_rates() == {'experiment_decide': 0.}
    assert received[0].kind == 'prediction_failed'
    assert runner.journal.entries()[-1]['body']['failures'][0]['kind'] == 'prediction_failed'


def test_daily_digest_is_readable_and_published_once_through_adapter():
    r, runner, inputs, calls, budget, repo = fixture(push=True, draft_pr=True, digest=True)
    runner.tick(now=100.)
    a = runner.daily_digest(now=103.)
    b = runner.daily_digest(now=104.)
    assert 'separating' in a.body and 'experiment_decide' in a.body
    assert a.pr == b.pr and len(repo.effects) == 1
    assert repo.effects[0][0] == 'digest'


@pytest.mark.parametrize('family', ['claude', 'codex'])
def test_mission_mounts_only_disposable_worktree_and_clean_home_with_no_signins(family):
    api()
    m = importlib.import_module('g6.distill.missions')
    mission = m.build_mission(family=family, mission_id='run-1', worktree='/missions/run-1/worktree',
        home='/missions/run-1/home', image='g6-engineer@sha256:' + 'a' * 64, task='repair the training case')
    assert {mount.target for mount in mission.mounts} == {'/workspace', '/mission-home'}
    assert mission.signin_mounts == () and mission.autonomy_ceiling == 'draft_pr'
    assert mission.command[:2] == (('claude', '-p') if family == 'claude' else ('codex', 'exec'))
    assert mission.network == 'none' and mission.read_only_root and not mission.privileged
    with pytest.raises(ValueError):
        m.build_mission(family=family, mission_id='bad', worktree='/missions/bad', home='/missions/bad',
                       image='untrusted:latest', task='repair')


def test_git_gh_adapter_uses_only_injected_commands_and_refuses_head_only_merge():
    api()
    a = importlib.import_module('g6.distill.repository_adapter')
    from tests.self_training.test_research_merge_gate import fixture as merge_fixture
    m, gate, host, repo, change, evidence = merge_fixture()
    recorded = []
    def command(argv, *, cwd, stdin=None):
        recorded.append((argv, cwd, stdin))
        return 'https://example.test/draft/7'
    adapter = a.GhRepository(root='/missions/root', command=command, stop=lambda: False,
        session_trailers=('Co-Authored-By: Test <test@example.test>', 'Claude-Session: fixture-session'),
        config=__import__('g6.self_training.autonomy_gate', fromlist=['Autonomy']).Autonomy(
            level='draft_pr', push=True, draft_pr=True))
    assert adapter.propose(change, labels=('protected-change',)).startswith('https://')
    assert any(argv[:3] == ('git', 'worktree', 'add') for argv, _, _ in recorded)
    assert any(argv[:3] == ('git', 'push', '--') for argv, _, _ in recorded)
    pr = [argv for argv, _, _ in recorded if argv[:3] == ('gh', 'pr', 'create')][0]
    assert '--draft' in pr and '--body-file' in pr
    with pytest.raises(ValueError, match='exact|squash'):
        adapter.merge_exact(change)


def test_runner_and_adapter_have_no_ambient_process_or_network_executor():
    import ast
    root = Path(__file__).parents[2] / 'src/g6/distill'
    for name in ('runner.py', 'missions.py', 'repository_adapter.py'):
        path = root / name
        assert path.is_file(), 'runner adapter is missing'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        names += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(n.startswith(('subprocess', 'socket', 'requests', 'g6.core.llm', 'g6.mcp')) for n in names)
