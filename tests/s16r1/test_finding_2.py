import asyncio
from types import SimpleNamespace

from g6.core import cloud_consent as cloud, consent_callers, llm
from g6.mcp.cloud_consent import request_grant


def test_explicit_unknown_source_stays_withheld(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda url, body, *args: sent.append(body) or {
        'choices': [{'message': {'content': 'fake reply'}}]})

    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, 'model_inputs', 'openai:api'))['status'] == 'granted'
    caller = consent_callers.guard(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    with cloud.request_sources((cloud.UNKNOWN,)):
        try:
            caller.complete('', 'PRIVATE_WITHOUT_KNOWN_ORIGIN')
        except llm.LLMError:
            pass
    assert sent == [], 'explicit unrecorded provenance was relabelled model_inputs and sent'
