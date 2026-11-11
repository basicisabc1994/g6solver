import asyncio
from types import SimpleNamespace

from g6.agents._runtime import GatedCalls
from g6.agents.agent_langgraph import adapter
from g6.agents.agent_langgraph.spec import GraphNode
from g6.core import cloud_consent as cloud, llm
from g6.mcp.cloud_consent import request_grant


def test_langgraph_tool_result_origin_survives_rendering_into_the_next_model_call(monkeypatch):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, 'model_inputs', 'openai:api'))['status'] == 'granted'
    cloud.revoke('private-document', 'openai:api')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *args: sent.append(body) or {
        'choices': [{'message': {'content': 'fake answer'}}]})
    caller = llm.OpenAICompatibleCaller('fake-model', base_url='https://api.openai.com/v1', api_key=None)
    gate = SimpleNamespace(decide=lambda *args, **kwargs: SimpleNamespace(runs=True))

    def read_document():
        return cloud.bind_output({'text': 'PRIVATE_LANGGRAPH_TOOL_RESULT'}, (
            cloud.Source.text('private-document', 'passage', 'PRIVATE_LANGGRAPH_TOOL_RESULT'),))

    calls = GatedCalls(gate, {'read_document': read_document})
    monkeypatch.setattr(adapter, '_tools', lambda: calls)
    read = adapter._node(GraphNode(name='read', op='tool', key='document', tool='read_document'), caller)
    answer = adapter._node(GraphNode(name='answer', op='llm', key='answer', prompt='Summarize: {document.result}'), caller)
    with cloud.collect_sources():
        state = read({})
        try:
            answer(state)
        except cloud.LLMError:
            pass
    if sent:
        assert 'PRIVATE_LANGGRAPH_TOOL_RESULT' in str(sent)
    assert not sent, 'the next LangGraph model call sent a tool result from a revoked source'
