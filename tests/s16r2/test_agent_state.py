"""One model-source join covers reads and raw state from every framework bridge."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.mcp.cloud_consent import request_grant


def grant(scope, provider='openai:api'):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'),
                                    scope, provider))['status'] == 'granted'


@pytest.mark.parametrize('part', ['tool', 'retrieved', 'prior_turn'])
def test_common_check_joins_all_observed_origins_even_after_rendering(part, monkeypatch):
    grant('model_inputs')
    cloud.revoke('private', 'openai:api')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = callers.guard(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    with cloud.collect_sources():
        value = cloud.bind_output({'text': 'PRIVATE_' + part},
                                  (cloud.Source.text('private', part, 'PRIVATE_' + part),))
        with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
            caller.complete('', str(value))
    assert not sent


@pytest.mark.parametrize('state_kind', ['known', 'opaque', 'no_receipt', 'mutated', 'unknown'])
def test_state_must_be_readable_and_keep_its_exact_receipts(state_kind, monkeypatch):
    state_boundary = getattr(cloud, 'prompt_state', None)
    assert state_boundary is not None, 'the common consent check cannot see adapter state'
    grant('model_inputs')
    grant('shared')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = callers.guard(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    with cloud.collect_sources():
        state = {'prior_turn': 'PRIVATE_STATE'}
        if state_kind == 'opaque':
            state = object()
        elif state_kind != 'no_receipt':
            cloud.bind_output(state, (cloud.UNKNOWN,) if state_kind == 'unknown' else (
                cloud.Source.text('shared', 'state', 'PRIVATE_STATE'),))
            if state_kind == 'mutated':
                state['prior_turn'] = 'PRIVATE_MUTATION'
        with state_boundary(state):
            if state_kind == 'known':
                caller.complete('', str(state))
            else:
                with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
                    caller.complete('', str(state))
    assert bool(sent) is (state_kind == 'known')


@pytest.mark.parametrize('framework', ['langgraph', 'autogen', 'smolagents'])
def test_registered_framework_tool_result_is_never_sent_after_revocation(framework, monkeypatch):
    from g6.agents._runtime import GatedCalls
    grant('model_inputs')
    cloud.revoke('private', 'openai:api')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None)
    tools = GatedCalls(SimpleNamespace(decide=lambda *a, **k: SimpleNamespace(runs=True)), {
        'read': lambda: cloud.bind_output({'text': 'PRIVATE_TOOL_RESULT'}, (
            cloud.Source.text('private', 'tool', 'PRIVATE_TOOL_RESULT'),))})
    with cloud.collect_sources():
        result = tools.call('read')
        if framework == 'langgraph':
            from g6.agents.agent_langgraph.adapter import _node
            from g6.agents.agent_langgraph.spec import GraphNode
            def send():
                return _node(GraphNode(name='answer', op='llm', key='answer', prompt='{tool.result}'),
                                              caller)({'tool': result})
        elif framework == 'autogen':
            pytest.importorskip('autogen_core')
            from autogen_core.models import UserMessage
            from g6.agents.agent_autogen.client import G6ChatClient
            def send():
                return asyncio.run(G6ChatClient(caller).create([
                            UserMessage(content=str(result), source='user')]))
        else:
            pytest.importorskip('smolagents')
            from g6.agents.agent_smolagents.models import G6Model
            def send():
                return G6Model(caller).generate([{'role': 'user', 'content': str(result)}])
        with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
            send()
    assert not sent


def agent_sends(extra=()):
    root = Path(cloud.__file__).resolve().parents[1] / 'agents'
    rows = set()
    for path in [*root.rglob('*.py'), *extra]:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in (
                    'complete', 'complete_with_images'):
                row = path.relative_to(root).as_posix() if root in path.parents else 'new.py'
                rows.add(row)
                contexts = [parent for parent in ast.walk(tree) if isinstance(parent, ast.With)
                            and n in list(ast.walk(parent))]
                assert any(isinstance(i.context_expr, ast.Call) and
                           ast.unparse(i.context_expr.func).endswith('prompt_state')
                           and i.optional_vars is not None
                           for parent in contexts for i in parent.items), row + ' hides its raw state'
    return rows


def test_code_derived_adapter_sends_expose_state_to_the_common_check(tmp_path):
    from tests.core.test_producer_declarations import test_code_derived_agent_inventory
    test_code_derived_agent_inventory()
    assert agent_sends() == {'agent_langgraph/adapter.py', 'agent_autogen/client.py', 'agent_smolagents/models.py'}
    path = tmp_path / 'new.py'
    path.write_text('def opaque(llm):\n    return llm.complete("", "hidden state")\n')
    with pytest.raises(AssertionError, match='new.py hides its raw state'):
        agent_sends((path,))


def test_nested_collectors_and_source_scopes_do_not_drop_state():
    state_boundary = getattr(cloud, 'prompt_state', None)
    assert state_boundary is not None
    source = cloud.Source.text('private', 'turn', 'PRIVATE_PRIOR_TURN')
    with cloud.collect_sources():
        value = cloud.bind_output({'text': 'PRIVATE_PRIOR_TURN'}, (source,))
        with state_boundary(value), cloud.collect_sources():
            from g6.core.consent_declarations import model_sources
            assert source in model_sources()
        from g6.core.consent_declarations import model_sources
        assert source in model_sources()


@pytest.mark.parametrize('provider', ['openai:codex', 'anthropic:claude_code'])
def test_registered_coding_processes_also_join_tool_origins_at_admission(provider, tmp_path):
    from g6.agents._runtime.consent import consent_run
    from g6.agents._runtime.model import AgentRefusal
    prompt = 'PRIVATE_PRIOR_TOOL_RESULT'
    for scope in ('agent_environment', cloud.repository_source(tmp_path), cloud.input_source(prompt)):
        grant(scope, provider)
    cloud.revoke('private', provider)
    sent = []
    runner = SimpleNamespace(run=lambda *a, **kw: sent.append(kw['prompt']))
    with cloud.collect_sources():
        cloud.bind_output({'text': prompt}, (cloud.Source.text('private', 'tool', prompt),))
        with pytest.raises(AgentRefusal, match=cloud.REQUIRED):
            consent_run(runner, ['fake'], provider=provider, prompt=prompt, cwd=tmp_path)
    assert not sent


@pytest.mark.parametrize('framework', ['langgraph', 'autogen', 'smolagents'])
def test_framework_with_known_state_can_send_then_stops_after_revocation(framework, monkeypatch):
    grant('model_inputs')
    grant('private')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None)
    with cloud.collect_sources():
        source = cloud.Source.text('private', 'prior', 'PRIVATE_KNOWN_STATE')
        if framework == 'langgraph':
            from g6.agents.agent_langgraph.adapter import _node
            from g6.agents.agent_langgraph.spec import GraphNode
            state = cloud.bind_output({'document': 'PRIVATE_KNOWN_STATE'}, (source,))
            def send():
                return _node(GraphNode(name='answer', op='llm', key='answer', prompt='{document}'), caller)(state)
        elif framework == 'autogen':
            pytest.importorskip('autogen_core')
            from autogen_core.models import UserMessage
            from g6.agents.agent_autogen.client import G6ChatClient
            state = cloud.bind_output([UserMessage(content='PRIVATE_KNOWN_STATE', source='user')], (source,))
            def send():
                return asyncio.run(G6ChatClient(caller).create(state))
        else:
            pytest.importorskip('smolagents')
            from g6.agents.agent_smolagents.models import G6Model
            state = cloud.bind_output([{'role': 'user', 'content': 'PRIVATE_KNOWN_STATE'}], (source,))
            def send():
                return G6Model(caller).generate(state)
        send()
        assert len(sent) == 1 and 'PRIVATE_KNOWN_STATE' in str(sent)
        cloud.revoke('private', 'openai:api')
        with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
            send()
        assert len(sent) == 1


def test_state_is_copied_before_rendering_and_never_borrows_a_later_value(monkeypatch):
    boundary = getattr(cloud, 'prompt_state', None)
    assert boundary is not None
    grant('model_inputs')
    grant('shared')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = callers.guard(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    with cloud.collect_sources():
        state = cloud.bind_output({'text': 'shared'}, (cloud.Source.text('shared', 'turn', 'shared'),))
        with boundary(state) as (snapshot,):
            state['text'] = 'PRIVATE_AFTER_SNAPSHOT'
            caller.complete('', str(snapshot))
    assert len(sent) == 1 and 'PRIVATE_AFTER_SNAPSHOT' not in str(sent)
