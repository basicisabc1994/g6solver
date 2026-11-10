# SPDX-License-Identifier: Apache-2.0
"""Cloud coding-process adapters are callers too, not just core.llm implementations."""
import importlib
from types import SimpleNamespace

import pytest


def test_registered_program_identity_cannot_be_borrowed_by_an_equal_program():
    from dataclasses import replace
    from g6.agents._runtime.consent import program_provider, register_program
    from g6.agents.agent_claude.adapter import CLAUDE
    from g6.agents.agent_codex.adapter import CODEX
    for program, provider in ((CLAUDE, 'anthropic:claude_code'), (CODEX, 'openai:codex')):
        assert program_provider(program) == provider
        assert program_provider(replace(program)) == 'cloud:unknown'
        register_program(program, provider)
        with pytest.raises(ValueError, match='another provider'):
            register_program(program, 'openai:codex' if provider == 'anthropic:claude_code' else 'anthropic:claude_code')


@pytest.mark.parametrize('provider', ['anthropic:claude_code', 'openai:codex'])
def test_cloud_agent_process_never_starts_with_ungranted_workspace(tmp_path, monkeypatch, provider):
    assert importlib.util.find_spec('g6.agents._runtime.consent'), 'host agent subprocesses have no consent check'
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    runner = SimpleNamespace(run=lambda *a, **kw: sent.append(kw.get('prompt')))
    with pytest.raises(AgentRefusal, match='G6_E_CLOUD_CONSENT_REQUIRED'):
        consent_run(runner, ['fake'], provider=provider, prompt='PLANTED_WORKSPACE', cwd=tmp_path,
                    scratch=tmp_path, seconds=1)
    assert not sent


def test_a_program_version_probe_sends_no_personal_data(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from g6.agents._runtime.cli import CliAgentRunner
    from g6.core import component_process, host_models
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    calls = []
    @contextmanager
    def window(*args):
        yield SimpleNamespace(execution={'cleanup_confirmed': True})
    def run(argv, **kwargs):
        calls.append(argv)
        kwargs['stdout'].write(b'version 1')
        return 0
    monkeypatch.setattr(component_process, 'window', window)
    monkeypatch.setattr(component_process, 'run_process', run)
    result = CliAgentRunner(host_models.CLAUDE).run(['stand-in', '--version'], prompt='', cwd=tmp_path,
                                                 seconds=1, scratch=tmp_path)
    assert result.exit_code == 0 and result.output_tail == 'version 1'
    assert calls == [['stand-in', '--version']]


@pytest.mark.parametrize('provider', ['anthropic:claude_code', 'openai:codex'])
@pytest.mark.parametrize('granted', [False, True])
@pytest.mark.parametrize('actual_boundary', [False, True])
def test_cli_external_environment_is_not_the_named_repository(tmp_path, monkeypatch, accepted_cloud_dialog,
                                                             provider, granted, actual_boundary):
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    from g6.core import cloud_consent as cloud
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    repo = tmp_path / 'repo'
    repo.mkdir()
    accepted_cloud_dialog(cloud.repository_source(repo), provider, text='synthetic workspace')
    external = tmp_path / 'external.txt'
    marker = 'PLANTED_AGENT_EXTERNAL_ENVIRONMENT'
    external.write_text(marker, encoding='utf-8')
    sent = []
    runner = SimpleNamespace(run=lambda *a, **kw: sent.append(external.read_text(encoding='utf-8')))
    if actual_boundary:
        from contextlib import contextmanager
        from g6.agents._runtime.cli import CliAgentRunner
        from g6.agents.agent_claude.adapter import CLAUDE
        from g6.agents.agent_codex.adapter import CODEX
        from g6.core import component_process
        @contextmanager
        def window(*args):
            yield SimpleNamespace(execution={'cleanup_confirmed': True})
        def run(argv, **kwargs):
            sent.append(external.read_text(encoding='utf-8'))
            kwargs['stdout'].write(b'finished')
            return 0
        monkeypatch.setattr(component_process, 'window', window)
        monkeypatch.setattr(component_process, 'run_process', run)
        runner = CliAgentRunner(CLAUDE if provider == 'anthropic:claude_code' else CODEX)
    if granted:
        accepted_cloud_dialog('agent_environment', provider, text=marker)
    with cloud.collect_sources() as capture:
        capture.client = provider, object()
        capture.supplied.add(cloud.repository_source(repo))
        if granted:
            consent_run(runner, ['fake'], provider=provider, prompt='client request', cwd=repo,
                        scratch=tmp_path, seconds=1)
        else:
            with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
                consent_run(runner, ['fake'], provider=provider, prompt='client request', cwd=repo,
                            scratch=tmp_path, seconds=1)
    assert (marker in str(sent)) is granted
    assert cloud.is_granted(cloud.repository_source(repo), provider)
