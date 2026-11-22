"""Review-one regressions. All host execution is synthetic or forbidden."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as policy, consent_callers, host_models, llm
from g6.core.consent_declarations import contract


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('G6_DISABLE_LLM', raising=False)
    monkeypatch.delenv('G6_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('G6_LLM_MODEL', raising=False)
    monkeypatch.setattr(llm.key_file, 'read', lambda: {})
    monkeypatch.setattr(host_models._HostCaller, 'locate', lambda self: 'synthetic-host')
    monkeypatch.setattr(host_models, '_run', lambda *a, **k: pytest.fail('unapproved host launched'))


@pytest.mark.parametrize('filed', [{}, {'G6_LLM_PROVIDER': 'auto'}, {'G6_LLM_PROVIDER': 'codex'}])
def test_scout_requires_its_explicit_provider(monkeypatch, filed):
    """Auto availability and another provider's approval must never arm file tools."""
    from g6.harness.completion_loop import scout_host
    monkeypatch.setattr(llm.key_file, 'read', lambda: filed)
    with policy.collect_sources() as capture:
        capture.client = ('anthropic:claude_code', object())
        assert scout_host() is None


def test_direct_tool_session_and_agent_admission_require_provider(tmp_path, accepted_cloud_dialog):
    """A person-facing agent session needs its key-file provider. The loop's scout ToolSession keeps its own enrolment
    (scout_host, test_scout_requires_its_explicit_provider) and consent gate instead (lead ruling, owner F4 pending)."""
    from g6.agents._runtime.consent import process_admission
    from g6.agents._runtime.model import AgentRefusal
    for provider in ('anthropic:claude_code', 'openai:codex'):
        for source in ('model_inputs', 'agent_environment', policy.repository_source(tmp_path),
                       policy.input_source('synthetic')):
            accepted_cloud_dialog(source, provider, text='synthetic')
    with pytest.raises(AgentRefusal, match='G6_E_NO_MODEL'):
        with process_admission('openai:codex', 'synthetic', tmp_path):
            pytest.fail('unapproved agent session admitted')


@pytest.mark.parametrize('provider', ['claude_code', 'codex'])
def test_agent_model_needs_explicit_host_even_when_completion_is_available(provider):
    from g6.agents._runtime.consent import require_model_session
    from g6.agents._runtime.model import AgentRefusal
    caller = consent_callers.provider_from_env(env={}, provider=provider)
    with pytest.raises(AgentRefusal, match='G6_E_NO_MODEL'):
        require_model_session(caller)


def test_all_file_tool_host_entry_points_use_the_session_predicate():
    """Walk the session entry points, including the watched launch's admission terms."""
    root = Path(__file__).resolve().parents[2] / 'src/g6'
    expected = {
        'harness/completion_loop.py': {'scout_host'},
        'agents/_runtime/consent.py': {'process_admission'},
    }
    for relative, names in expected.items():
        tree = ast.parse((root / relative).read_text(encoding='utf-8'))
        for name in names:
            functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
            assert any(any(isinstance(n, ast.Attribute) and n.attr == 'session_approved'
                           for n in ast.walk(fn)) for fn in functions), (relative, name)
    # Every CLI adapter delegates to consent_run; direct CliAgentRunner uses process_admission.
    for relative in ('agents/agent_claude/adapter.py', 'agents/agent_codex/adapter.py'):
        tree = ast.parse((root / relative).read_text(encoding='utf-8'))
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'consent_run'
                   for n in ast.walk(tree)), relative
    tree = ast.parse((root / 'agents/_runtime/cli.py').read_text(encoding='utf-8'))
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'process_admission'
               for n in ast.walk(tree))
    # Walk every constructor in the host-code agent/session inventories. Each
    # class must reach the checked seam from its own run body, not merely import it.
    from g6.core.consent_declarations import AGENTS
    # Person-facing agent sessions only: the loop's cloud-tools ToolSession is enrolled by scout_host (above).
    sessions = set(AGENTS)
    checked = {'consent_run', 'process_admission', 'require_model_session', 'session_approved'}
    for qualified in sessions:
        module, _, name = qualified.rpartition('.')
        path = root.parent / (module.replace('.', '/') + '.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
        run = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        assert any(isinstance(n, ast.Call)
                   and (n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                        if isinstance(n.func, ast.Attribute) else '') in checked for n in ast.walk(run)), qualified


@pytest.mark.parametrize('kind', ['agent_environment', 'jobs', 'knowledge:synthetic', 'model_inputs'])
def test_same_client_never_exempts_unrelated_sources(kind):
    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', object())
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            with policy.admission('openai:codex', (policy.Source.text(kind, 'item', 'synthetic'),), policy.sha256('p')):
                pytest.fail('unrelated source admitted')
    assert policy.disclosures() == []


@pytest.mark.parametrize('arguments', [{}, {'text': 'CURRENT_LITERAL'}])
def test_nested_argument_snapshot_needs_no_client_identity(arguments):
    """Producer defaults cannot replace wire bytes in an unidentified request's child."""
    with policy.collect_sources() as parent:
        parent.client_arguments = arguments
        with policy.collect_sources() as child:
            refs = contract('client_request').sources({'default': 'PRIVATE_HOST_DEFAULT'})
            actual = json.dumps(arguments, sort_keys=True)
            assert {ref.source_id for ref in refs} == {policy.input_source(actual)}
            assert refs[0].content_sha256 == policy.sha256(actual)
            assert child.client is None and parent.client is None
        assert parent.supplied == {policy.input_source(actual)}


def test_current_request_and_rooted_repository_only(tmp_path):
    root = tmp_path / 'workspace'
    repo = root / 'repo'
    outside = tmp_path / 'outside'
    repo.mkdir(parents=True)
    outside.mkdir()
    with policy.collect_sources() as capture:
        capture.client = ('openai:codex', object())
        capture.client_roots = (root,)
        capture.client_arguments = {'repo': str(repo), 'task': 'synthetic'}
        refs = contract('client_request', 'client_repository').sources(capture.client_arguments)
        with policy.admission('openai:codex', refs, policy.sha256('p')):
            pass
        assert {s['basis'] for s in policy.disclosures()[-1]['sources']} == {'calling_client'}
        for arguments in ({'repo': str(outside)}, {}):
            other = contract('client_repository').sources(arguments)
            with pytest.raises(llm.LLMError, match=policy.REQUIRED):
                policy.refuse_unshared('openai:codex', other)
        policy.revoke(refs[0].source_id, 'openai:codex')
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            policy.refuse_unshared('openai:codex', refs)
    with policy.collect_sources() as fresh:
        fresh.client = ('openai:codex', object())
        with pytest.raises(llm.LLMError, match=policy.REQUIRED):
            policy.refuse_unshared('openai:codex', refs)


@pytest.mark.parametrize('filed,env', [({'G6_DISABLE_LLM': '1'}, {'G6_DISABLE_LLM': ''}),
                                     ({'G6_DISABLE_LLM': ''}, {'G6_DISABLE_LLM': '1'})])
def test_either_kill_switch_disables_resolution_and_doctor(monkeypatch, filed, env):
    from g6.cli.doctor import _model
    monkeypatch.setattr(llm.key_file, 'read', lambda: filed)
    assert isinstance(llm.provider_from_env(env=env), llm.NoModel)
    assert _model(env, filed).detail.startswith('Model calls disabled')


def test_agent_send_checks_share_the_kill_switch_reader():
    root = Path(__file__).resolve().parents[2] / 'src/g6/agents'
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get':
                assert not (node.args and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value == 'G6_DISABLE_LLM'), path.relative_to(root)


@pytest.mark.parametrize('path', ['critic', 'scout', 'selector', 'premortem', 'agent'])
def test_key_file_kill_switch_stops_every_host_path(monkeypatch, tmp_path, path, accepted_cloud_dialog):
    from g6.harness import completion_loop
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    # Make source consent otherwise sufficient, so a refusal cannot hide a missing kill check.
    for source in ('model_inputs', 'agent_environment', policy.repository_source(tmp_path)):
        accepted_cloud_dialog(source, 'openai:codex', text='synthetic')
    # Construct first, then disable: send-time checks must reread the file.
    caller = consent_callers.provider_from_env(env={}, provider='codex')
    monkeypatch.setattr(llm.key_file, 'read', lambda: {
        'G6_DISABLE_LLM': '1', 'G6_LLM_PROVIDER': 'claude_code', 'G6_LOOP_CRITIC': 'codex'})
    if path == 'scout':
        assert completion_loop.scout_host() is None
    elif path == 'agent':
        runner = SimpleNamespace(run=lambda *a, **k: pytest.fail('agent launched'))
        with pytest.raises(AgentRefusal, match='G6_E_LLM_DISABLED'):
            consent_run(runner, [], provider='openai:codex', prompt='synthetic', cwd=tmp_path)
    elif path == 'selector':
        from g6.solver.agentic_runtime import SolverPlanner
        index, decision = SolverPlanner(caller).select_answer(goal='synthetic', candidates=['first', 'second'])
        assert index == 0 and not decision.llm_used
        assert 'G6_E_LLM_DISABLED' in decision.rationale
    elif path == 'premortem':
        from g6.bundles.premortem import run
        result = run({'plan': 'synthetic plan ' * 20}, caller)
        assert result.completion_state == 'blocked-escalated'
        assert 'G6_E_LLM_DISABLED' in result.summary
    else:
        if path == 'critic':
            caller = completion_loop.critic_host()
        with pytest.raises(llm.LLMError, match='G6_E_LLM_DISABLED'):
            caller.complete('synthetic', 'synthetic')


@pytest.mark.parametrize('environment', ['ollama', 'claude_code', 'codex', 'auto', 'openai'])
def test_key_file_opt_out_wins_every_environment_provider(monkeypatch, environment):
    from g6.cli.doctor import _model
    filed = {'G6_LLM_PROVIDER': 'none'}
    monkeypatch.setattr(llm.key_file, 'read', lambda: filed)
    assert isinstance(llm.provider_from_env(env={'G6_LLM_PROVIDER': environment}), llm.NoModel)
    assert _model({'G6_LLM_PROVIDER': environment}, filed).detail == 'Key-file opt-out: plain code only'


def test_environment_only_host_explains_that_calls_fail():
    from g6.cli.doctor import _model
    check = _model({'G6_LLM_PROVIDER': 'codex'}, {})
    assert 'no default' in check.detail.lower() and 'fail' in check.detail.lower()
    caller = llm.provider_from_env(env={'G6_LLM_PROVIDER': 'codex'})
    with pytest.raises(llm.LLMError, match='no default'):
        caller.complete('synthetic', 'synthetic')


def test_invalid_environment_model_does_not_crash_scout(monkeypatch):
    from g6.harness.completion_loop import scout_host
    monkeypatch.setenv('G6_LLM_MODEL', 'invalid model name')
    assert scout_host() is None


def test_invalid_auto_model_is_an_unusable_caller_instead_of_a_constructor_crash():
    caller = llm.provider_from_env(env={'G6_LLM_MODEL': 'invalid model name'})
    assert isinstance(caller, llm.UnusableModel)
    with pytest.raises(llm.LLMError, match='G6_E_LLM_BAD_MODEL'):
        caller.complete('synthetic', 'synthetic')


def test_invalid_environment_model_keeps_prepare_plain_code_path(monkeypatch, tmp_path):
    from g6.harness import completion_loop as loop
    monkeypatch.setenv('G6_LLM_MODEL', 'invalid model name')
    monkeypatch.setattr(loop, '_before_fix', lambda: True)
    monkeypatch.setattr(loop, 'critic_host', lambda: None)
    monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(loop.repo_tests, '_git', lambda *args: b'synthetic.py\0')
    monkeypatch.setattr(loop.proposer, 'boundary', lambda root: '')
    assert loop.prepare(tmp_path, 'synthetic', 'base', tmp_path) == ''


def test_native_and_watched_current_request_sends_have_identical_evidence(monkeypatch):
    import g6.self_maintenance.usage  # noqa: F401  install the real construction hook
    from tests.host_stand_in import stand_in_hosts
    stand_in_hosts(monkeypatch, lambda system, user: 'synthetic answer')
    source = policy.Source.text(policy.input_source('synthetic'), 'current-request', 'synthetic')
    seen = []
    for native in (True, False):
        with monkeypatch.context() as patch:
            if native:
                patch.setattr(host_models._HostCaller, '__new__',
                              staticmethod(lambda kind, *a, **k: object.__new__(kind)))
            before = len(policy.disclosures())
            with policy.collect_sources() as capture:
                capture.client = ('anthropic:claude_code', object())
                capture.supplied.add(source.source_id)
                caller = consent_callers.provider_from_env(env={})
                with policy.request_sources((source,)):
                    answer = caller.complete('synthetic', 'synthetic')
                rows = policy.disclosures()[before:]
                seen.append((answer, [s['basis'] for row in rows for s in row['sources']], capture.model_calls))
    assert seen[0] == seen[1]
    assert seen[0][1] == ['calling_client']


def test_no_calling_client_keeps_solver_fallback_and_reason():
    from g6.solver.agentic_runtime import SolverPlanner
    from g6.solver.complexity_classifier import classify_task
    caller = consent_callers.provider_from_env(env={})
    assert policy.calling_capture() is None
    goal = 'synthetic task'
    level, decision = SolverPlanner(caller).assess_complexity(goal=goal)
    assert level == classify_task(goal)
    assert decision.degraded and not decision.llm_used
    assert policy.REQUIRED in decision.rationale


def test_images_record_selection_after_admission(monkeypatch):
    from g6.core.ledger import digest
    # A loopback model needs no sharing grant; HTTP itself stays stood in.
    caller = consent_callers.provider_from_env(env={}, provider='ollama')
    monkeypatch.setattr(llm, '_post_json', lambda *a, **k: {'message': {'content': 'synthetic'}})
    image = llm.Image(b'\x89PNG\r\n\x1a\nsynthetic', 'image/png')
    with policy.collect_sources() as capture:
        assert caller.complete_with_images('s', 'u', [image]) == 'synthetic'
        assert capture.model_calls == [dict(caller.model_selection, status='attempted')]
        assert digest(capture.model_calls)


def test_job_full_and_compact_results_keep_selection_evidence():
    from g6.metaharness.jobs import Jobs
    jobs = Jobs()
    selection = {'kind': 'model_selection', 'provider': 'codex', 'reason': 'calling_client',
                 'available_hosts': ['codex'], 'status': 'attempted'}
    def operation():
        policy.current_capture().model_calls.append(selection)
        return policy.bind_output({'ok': True, 'evidence': []}, ())
    try:
        pending = jobs.start(operation, compact=lambda value, ident: value)
        jobs.close()
        for detail in ('full', 'compact'):
            result = jobs.status(pending['job_id'], detail=detail)['result']
            assert result['evidence'] == [selection], json.dumps(result)
    finally:
        jobs.close()


def test_job_keeps_its_starting_workspace_scope_but_a_poll_acquires_none(tmp_path):
    from g6.metaharness.jobs import Jobs
    jobs = Jobs()
    def operation():
        with policy.admission('openai:codex', refs, policy.sha256('synthetic')):
            return policy.bind_output({'ok': True}, refs)
    try:
        with policy.collect_sources() as capture:
            capture.client = ('openai:codex', object())
            capture.client_roots = (tmp_path,)
            refs = contract('client_repository').sources({'repo': str(tmp_path)})
            pending = jobs.start(operation)
        jobs.close()
        assert jobs.status(pending['job_id'])['status'] == 'completed'
        assert policy.disclosures()[-1]['sources'][0]['basis'] == 'calling_client'
        with policy.collect_sources() as poll:
            poll.client = ('openai:codex', object())
            with pytest.raises(llm.LLMError, match=policy.REQUIRED):
                policy.refuse_unshared('openai:codex', refs)
    finally:
        jobs.close()
