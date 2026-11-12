"""Every collector producer carries observations through nesting and copied threads."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, consent_callers as callers, llm
from g6.harness import completion_loop as loop
from g6.mcp import build_server
from g6.mcp.cloud_consent import request_grant
from g6.metaharness.jobs import Jobs


def grant(scope):
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Person'})
    assert asyncio.run(request_grant(SimpleNamespace(session=Dialog(), request_id='grant'), scope, 'openai:api'))['status'] == 'granted'


@pytest.mark.parametrize('origin', ['revoked', 'unknown', 'granted'])
@pytest.mark.parametrize('entry', ['collector', 'thread', 'job', 'prepare', 'advance', 'mcp'])
def test_every_nested_entry_preserves_outer_origins(origin, entry, tmp_path, monkeypatch):
    grant('model_inputs')
    grant('private')
    if origin == 'revoked':
        cloud.revoke('private', 'openai:api')
    refs = (cloud.UNKNOWN,) if origin == 'unknown' else (cloud.Source.text('private', 'passage', 'PRIVATE_OUTER'),)
    if entry in ('advance', 'prepare'):
        for scope in (cloud.repository_source(tmp_path), cloud.input_source('task')):
            grant(scope)
    sent = []
    monkeypatch.setattr(llm, '_post_json', lambda *a: sent.append(a) or {'choices': [{'message': {'content': 'answer'}}]})
    raw = llm.OpenAICompatibleCaller('fake', base_url='https://api.openai.com/v1', api_key=None)
    caller = callers.guard(raw)
    def send():
        return caller.complete('', 'formatted PRIVATE_OUTER')
    def nested():
        with cloud.collect_sources():
            return send()
    with cloud.collect_sources() as outer:
        cloud.bind_output({'text': 'PRIVATE_OUTER'}, refs)
        if entry == 'collector':
            invoke = nested
        elif entry == 'thread':
            context = copy_context()
            def invoke():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(context.run, nested).result()
        elif entry == 'job':
            def invoke():
                jobs = Jobs()
                try:
                    handle = jobs.start(send)
                    jobs.close()
                    return jobs.status(handle['job_id'])
                finally:
                    jobs.close()
        elif entry == 'advance':
            monkeypatch.setattr(loop, '_load', lambda *a: {})
            monkeypatch.setattr(loop, '_advance_inner', lambda *a: send())
            def invoke():
                return loop._advance(tmp_path, 'task', 'HEAD', tmp_path, None, None, '', False, '')
        elif entry == 'prepare':
            for scope in (cloud.repository_source(tmp_path), cloud.input_source('task')):
                grant(scope)
            monkeypatch.setattr(loop, '_before_fix', lambda: True)
            monkeypatch.setattr(loop, 'critic_host', lambda: None)
            monkeypatch.setattr(loop.repo_tests, 'checked_repo', lambda *a: tmp_path)
            monkeypatch.setattr(loop.repo_tests, '_git', lambda *a: b'mod.py\0')
            monkeypatch.setattr(loop.repo_tests, 'change', lambda *a: SimpleNamespace(repo=tmp_path))
            monkeypatch.setattr(loop.proposer, 'boundary', lambda *a: '')
            monkeypatch.setattr(loop, 'harness', lambda: SimpleNamespace(sources=()))
            monkeypatch.setattr(loop, '_load', lambda *a: {})
            monkeypatch.setattr(loop, '_save', lambda *a: None)
            monkeypatch.setattr(loop, '_scouts', lambda *a: send())
            monkeypatch.setattr(loop, '_told', lambda *a: '')
            def invoke():
                return loop.prepare(tmp_path, 'task', 'HEAD', tmp_path, scout=lambda *a: '')
        else:
            server = build_server()
            @server.tool()
            def nested_model() -> dict:
                return {'text': send()}
            async def run():
                async with Client(server, client_info=Implementation(name='codex', version='test')) as client:
                    return await client.call_tool('nested_model', {})
            def invoke():
                return asyncio.run(run())
        try:
            invoke()
        except Exception as exc:
            assert origin != 'granted', str(exc)
            assert cloud.REQUIRED in str(exc) or entry == 'mcp' and 'Invalid tool call' in str(exc), str(exc)
        assert bool(sent) == (origin == 'granted')
        assert set(refs) <= set(outer.sources)


def test_parent_observations_added_after_child_entry_are_seen_by_prompt_sources():
    with cloud.collect_sources() as outer:
        with cloud.collect_sources():
            outer.sources.append(cloud.UNKNOWN)
            assert cloud.UNKNOWN in cloud.prompt_sources()


def test_child_observations_union_with_every_ancestor_without_copying_authority():
    with cloud.collect_sources() as outer:
        outer.client = ('openai:codex', object())
        outer.supplied.add('literal')
        outer.client_arguments = {'task': 'literal'}
        outer.sources.append(cloud.UNKNOWN)
        with cloud.collect_sources() as middle:
            with cloud.collect_sources() as inner:
                assert cloud.UNKNOWN in inner.sources
                assert inner.client is None and inner.supplied == set() and inner.client_arguments is None
                source = cloud.Source.text('private', 'child', 'private')
                cloud.record_sources((source,))
            assert source in middle.sources
        assert source in outer.sources


def collector_sites(source):
    found = set()
    def walk(node, owners=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owners = (*owners, node.name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'collect_sources':
            found.add('.'.join(owners))
        for child in ast.iter_child_nodes(node):
            walk(child, owners)
    walk(ast.parse(source))
    return found


def test_code_derived_collector_entry_inventory():
    root = Path(cloud.__file__).resolve().parents[1]
    found = {path.relative_to(root).as_posix(): sites for path in root.rglob('*.py')
             if (sites := collector_sites(path.read_text(encoding='utf-8')))}
    assert found == {'harness/completion_loop.py': {'prepare', '_advance'},
                     'mcp/cloud_consent.py': {'install.ConsentEgress.on_call_tool'},
                     'metaharness/jobs.py': {'Jobs._run'}}
    assert collector_sites('def missed():\n    with alias.collect_sources():\n        pass\n') == {'missed'}
