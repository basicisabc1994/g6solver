import asyncio
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.mcp.cloud_consent import request_grant
from g6.metaharness.jobs import Jobs


@pytest.mark.parametrize('unknown', [False, True], ids=['revoked', 'unknown'])
def test_job_nested_capture_cannot_erase_an_observed_tool_result_origin(monkeypatch, unknown):
    class Dialog:
        async def elicit_form(self, **kw):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})

    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, 'model_inputs', 'openai:api'))['status'] == 'granted'
    cloud.revoke('private-tool-document', 'openai:api')
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {
        'choices': [{'message': {'content': 'answer'}}]})
    caller = callers.guard(llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None))
    source = cloud.UNKNOWN if unknown else cloud.Source.text(
        'private-tool-document', 'passage', 'PRIVATE_TOOL_RESULT_IN_JOB')
    jobs = Jobs()
    try:
        with cloud.collect_sources() as outer:
            result = cloud.bind_output({'text': 'PRIVATE_TOOL_RESULT_IN_JOB'}, (source,))
            assert source in outer.sources
            # Jobs._run starts a nested collector in its copied request context.
            # Formatting the observed tool output must not erase its source.
            jobs.start(lambda: caller.complete('', str(result)))
            jobs.close()
        assert not sent, 'the job collector dropped the revoked origin before its model admission'
    finally:
        jobs.close()
