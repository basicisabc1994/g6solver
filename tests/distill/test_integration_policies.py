"""Owner decisions enforced at the consuming boundaries."""
from pathlib import Path
from unittest.mock import patch

import pytest


def test_signin_mount_requires_separate_grant_and_is_read_only():
    from g6.distill.missions import Mount, build_mission, ContainerMissions
    from g6.self_training.autonomy_gate import Autonomy
    kwargs = dict(family='codex', mission_id='fixture', worktree='/work/task', home='/work/clean',
                  image='g6@sha256:' + 'a' * 64, task='rehearse')
    signin = Mount('/owner/configured-session', '/signin/codex', read_only=True)
    with pytest.raises(ValueError, match='authority'):
        build_mission(**kwargs, signin_mounts=(signin,))
    config = Autonomy(level='draft_pr', signin_mounts=True, signin_mount_paths={signin.target: signin.source})
    with patch.object(Path, 'open', side_effect=AssertionError('must not open sign-in contents')):
        mission = build_mission(**kwargs, signin_mounts=(signin,), config=config)
        assert mission.signin_mounts == (signin,)
        assert mission.autonomy_ceiling == 'draft_pr'
        assert ContainerMissions(lambda m, **kw: m, stop=lambda: False, config=config).run(mission) == mission
    with pytest.raises(ValueError, match='read.only'):
        build_mission(**kwargs, signin_mounts=(Mount(signin.source, signin.target),), config=config)
    with pytest.raises(ValueError):
        build_mission(**kwargs, signin_mounts=(Mount(signin.source, '/workspace', True),), config=config)


def test_study_registration_uses_owner_42_of_84_budget(monkeypatch):
    from validation.distill.held_out import registration_gate as gate
    assert gate.study_metadata({})['sealed_bug_budget'] == {'allowed': 42, 'total': 84}
    with pytest.raises(ValueError, match='budget'):
        gate.study_metadata({'sealed_bug_budget': {'allowed': 43, 'total': 84}})


def test_acceptance_git_uses_hardened_backend_and_keeps_refusal(monkeypatch):
    from g6.distill import acceptance_gate as gate
    from g6.workforce import repo_tests
    seen = []
    def read(repo, *args, **kwargs):
        assert kwargs['isolated_config'] is True
        seen.append((repo, args))
        return b'exact git bytes\n'
    monkeypatch.setattr(repo_tests, '_git', read)
    assert gate._git('show', 'a' * 40 + ':registration.json') == b'exact git bytes\n'
    assert seen == [(gate.ROOT, ('show', 'a' * 40 + ':registration.json'))]
    def refuse(*a, **kw):
        raise repo_tests.RepoRefused('G6_E_GIT_COMMAND_FAILED', 'safe refusal')
    monkeypatch.setattr(repo_tests, '_git', refuse)
    with pytest.raises(repo_tests.RepoRefused):
        gate._git('merge-base', '--is-ancestor', 'a' * 40, 'b' * 40)


def test_signin_cannot_mount_a_host_root_or_ancestor_of_owner_state():
    from g6.distill.missions import Mount, build_mission
    from g6.self_training.autonomy_gate import Autonomy
    for source in ('/', '/owner', 'C:/', 'C:/Users', '//host/share'):
        with pytest.raises(ValueError):
            build_mission(family='codex', mission_id='fixture', worktree='/task/work', home='/task/home',
                image='g6@sha256:' + 'a' * 64, task='rehearse',
                signin_mounts=(Mount(source, '/signin/codex', True),),
                config=Autonomy(level='draft_pr', signin_mounts=True))


def test_digest_read_does_not_publish_with_full_digest_grants():
    from tests.distill.test_research_runner import fixture
    from g6.mcp.distill_backend import Service
    _, runner, _, _, _, repo = fixture(push=True, draft_pr=True, digest=True)
    service = Service(runners={'fixture': runner}, now=lambda: 103.)
    assert service.research_digest('fixture')['ticks'] == 0
    assert repo.effects == []


def test_issuance_rehearsal_is_container_only_read_only_and_has_no_signer():
    from g6.distill.missions import build_mission, ContainerMissions
    mission = build_mission(family='codex', mission_id='rehearsal', worktree='/task/work', home='/task/home',
        image='g6@sha256:' + 'a' * 64, task='rehearse')
    launched = []
    host = ContainerMissions(lambda m, **kw: launched.append(m) or 'receipt', stop=lambda: False)
    assert host.rehearse_issuance(mission) == ('receipt', 'receipt')
    assert [m.command for m in launched] == [('python', 'tools/issue_certificate.py', '--precheck'),
                                            ('python', 'tools/issue_certificate.py', '--check')]
    assert all(m.mounts[0].read_only and not m.signin_mounts and not m.mcp_servers for m in launched)
    assert all(m.network == 'none' for m in launched)


def test_signin_grant_never_authorizes_a_caller_selected_source():
    from g6.distill.missions import Mount, build_mission
    from g6.self_training.autonomy_gate import Autonomy
    config = Autonomy(level='draft_pr', signin_mounts=True)
    with pytest.raises(ValueError, match='owner.configured'):
        build_mission(family='codex', mission_id='fixture', worktree='/task/work', home='/task/home',
            image='g6@sha256:' + 'a' * 64, task='rehearse',
            signin_mounts=(Mount('/caller/secret.json', '/signin/codex', True),), config=config)
