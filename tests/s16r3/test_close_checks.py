"""The three roots, challenged once more at the points a reply or a request leaves.

A: the rendered MCP reply is serialised once; the receipt is matched against that serialisation and the ticket is cut
from it. B: a proxy the platform's own settings name is the recipient, for a remote and a loopback destination. C: a
collector entered inside others, in a job and in a worker thread, starts with what every ancestor observed.
"""
import ast
import asyncio
import io
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.response import addinfourl

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.core.consent_declarations import declares
from g6.mcp import build_server, cloud_consent as egress, consent_transport as transport
from g6.mcp.cloud_consent import request_grant
from g6.metaharness.jobs import Jobs


class Dialog:
    async def elicit_form(self, **kw):
        return SimpleNamespace(action='accept', content={'value': 'Person'})


def grant(scope, provider):
    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, scope, provider))['status'] == 'granted'


MOMENTS = ('never', 'before_serialising', 'after_match', 'after_ticket')


@pytest.mark.parametrize('moment', MOMENTS)
def test_a_rendered_reply_changed_at_any_step_of_its_handoff_is_not_sent_under_its_receipt(monkeypatch, moment):
    """The rendered reply is a live object until the transport takes it: change it between every two steps."""
    value = {'text': 'shared original'}
    source = cloud.Source.text('shared-document', 'passage', value['text'])
    reached = []

    def change(rendered):
        if 'shared original' in str(rendered.content) and not reached:
            reached.append(moment)
            for block in rendered.content:
                block.text = block.text.replace('shared original', 'PRIVATE_CHANGED_REPLY')
            if isinstance(rendered.structured_content, dict):
                rendered.structured_content['text'] = 'PRIVATE_CHANGED_REPLY'

    serialise, match, ticket = transport.call_body, egress._matches_return, transport.prepare

    def serialise_after_change(rendered):
        if moment == 'before_serialising' and rendered.structured_content is not None:
            change(rendered)
        return serialise(rendered)

    def match_then_change(returned, rendered, *more):
        same = match(returned, rendered, *more)
        if moment == 'after_match' and same:
            change(rendered)
        return same

    def ticket_then_change(session, request_id, rendered, *more, **named):
        cut = ticket(session, request_id, rendered, *more, **named)
        if moment == 'after_ticket':
            change(rendered)
        return cut

    monkeypatch.setattr(transport, 'call_body', serialise_after_change)
    monkeypatch.setattr(egress, '_matches_return', match_then_change)
    monkeypatch.setattr(transport, 'prepare', ticket_then_change)
    server = build_server()

    @server.tool()
    @declares('source:shared-document')
    def cached_document() -> dict:
        return cloud.bind_output(value, (source,))

    async def run():
        async with Client(server, client_info=Implementation(name='codex', version='close')) as client:
            return await client.call_tool('cached_document', {})

    grant('shared-document', 'openai:codex')
    received = asyncio.run(run())
    if moment == 'never':
        assert received.data == {'text': 'shared original'}
    else:
        assert reached == [moment], 'the reply was never changed at this step'
        assert 'PRIVATE_CHANGED_REPLY' not in str(received), 'a reply changed during its handoff went out under its receipt'
        assert cloud.REQUIRED in str(received)


def ticket_cuts(source):
    """Each call that cuts a transport ticket, with whether it was handed the serialisation its caller matched."""
    tree = ast.parse(source)
    names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'g6.mcp.consent_transport':
            names.update(alias.asname or alias.name for alias in node.names if alias.name == 'prepare')
        elif isinstance(node, ast.ImportFrom) and node.module == 'g6.mcp':
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == 'consent_transport')
        elif isinstance(node, ast.Import):
            modules.update(alias.asname for alias in node.names if alias.name == 'g6.mcp.consent_transport' and alias.asname)
    return [any(keyword.arg == 'body' for keyword in node.keywords) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id in names
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'prepare'
                and isinstance(node.func.value, ast.Name) and node.func.value.id in modules)]


def test_every_ticket_is_cut_from_the_serialisation_its_sources_were_matched_against():
    root = Path(egress.__file__).resolve().parents[1]
    cuts = {path.relative_to(root).as_posix(): found for path in root.rglob('*.py')
            if (found := ticket_cuts(path.read_text(encoding='utf-8')))}
    assert cuts == {'mcp/cloud_consent.py': [True, True]}
    # The one place that cuts them serialises the rendered reply once and hands that to the match as well.
    handoff = next(node for node in ast.walk(ast.parse((root / 'mcp/cloud_consent.py').read_text(encoding='utf-8')))
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == 'on_call_tool')
    calls = [node for node in ast.walk(handoff) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    assert [ast.unparse(node) for node in calls if node.func.id == 'call_body'] == ['call_body(result)']
    assert [ast.unparse(node) for node in calls if node.func.id == '_matches_return'] == [
        '_matches_return(returned, result, wire)']
    assert all(ast.unparse(keyword.value) == 'wire' for node in calls if node.func.id == 'prepare'
               for keyword in node.keywords if keyword.arg == 'body')
    assert ticket_cuts('from g6.mcp.consent_transport import prepare as cut\n\n'
                       'def late(session, ident, result, refs):\n    cut(session, ident, result, refs)\n') == [False]
    assert ticket_cuts('from g6.mcp import consent_transport as wire\n\n'
                       'def late(session, ident, result, refs):\n    wire.prepare(session, ident, result, refs)\n') == [False]


@pytest.mark.parametrize('kind', ['openai_compatible', 'ollama'])
@pytest.mark.parametrize('local', [False, True], ids=['remote', 'loopback'])
def test_a_proxy_from_the_platforms_settings_is_the_recipient_for_both_destination_classes(monkeypatch, local, kind):
    # No environment variable names this proxy: it is what urllib reads from the platform (the registry on Windows).
    base = 'http://127.0.0.1:11434' if local else 'http://model.invalid:11434'
    proxy = 'http://platform-proxy.invalid:3128'
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'http': proxy})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host, *more: False)
    sent = []

    def fake_socket(handler, connection, request, **kwargs):
        sent.append((request.host, request.data))
        response = addinfourl(io.BytesIO(b'{"message":{"content":"answer"},"choices":[{"message":{"content":"answer"}}]}'),
                             Message(), request.full_url, 200)
        response.msg = 'OK'
        return response

    monkeypatch.setattr(urllib.request.AbstractHTTPHandler, 'do_open', fake_socket)
    raw = llm.OllamaCaller('fake', base_url=base) if kind == 'ollama' else llm.OpenAICompatibleCaller(
        'fake', base_url=base, api_key=None)
    raw.complete('', 'routing oracle')      # unguarded and synthetic: where the transport itself goes
    assert [hop for hop, _ in sent] == ['platform-proxy.invalid:3128']
    sent.clear()
    endpoint, hop = 'endpoint:' + cloud.sha256(base), 'endpoint:' + cloud.sha256(proxy)
    to = callers.destination(raw)
    assert (to.provider, to.local) == (hop, False)
    caller = callers.guard(raw)
    grant('model_inputs', endpoint)
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        caller.complete('', 'PRIVATE_TO_PLATFORM_PROXY')
    assert not sent
    grant('model_inputs', hop)
    assert caller.complete('', 'shared') == 'answer'
    assert [hop for hop, _ in sent] == ['platform-proxy.invalid:3128']


@pytest.mark.parametrize('entry', ['job', 'thread'])
def test_a_collector_inside_others_starts_with_what_every_ancestor_observed(entry):
    private = cloud.Source.text('private-document', 'passage', 'PRIVATE_OUTER')
    seen = {}

    def note(inner):
        seen.update(capture=inner, at_entry=tuple(inner.sources), prompt=cloud.prompt_sources())

    def in_job():
        note(cloud.current_capture())       # the collector Jobs._run entered for this job
        return {'ok': True}

    def in_thread():
        with cloud.collect_sources() as inner:
            note(inner)

    with cloud.collect_sources():
        cloud.record_sources((cloud.UNKNOWN,))
        with cloud.collect_sources() as outer:
            cloud.bind_output({'text': 'PRIVATE_OUTER'}, (private,))
            if entry == 'job':
                jobs = Jobs()
                try:
                    jobs.start(in_job)
                finally:
                    jobs.close()
            else:
                context = copy_context()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(context.run, in_thread).result()
    assert seen['capture'] is not outer and seen['capture'].parent is outer
    assert {private, cloud.UNKNOWN} <= set(seen['at_entry'])
    assert {private, cloud.UNKNOWN} <= set(seen['prompt'])
