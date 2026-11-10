# SPDX-License-Identifier: Apache-2.0
"""Client-dialog grants and the last MCP reply boundary for cloud coding clients."""
from __future__ import annotations

import json
from copy import deepcopy
import contextvars
import re
import inspect
from functools import partial, wraps
from typing import get_origin, get_type_hints
from typing import Any

import anyio

from g6.core import cloud_consent as policy
from g6.core.llm import LLMError
from g6.mcp.consent import request_consent

_PROVIDER = re.compile(r'(?:anthropic:(?:claude_code|api)|openai:(?:codex|api)|openrouter:api|endpoint:[0-9a-f]{64})\Z')
_PUBLIC = object()
_INSIDE: contextvars.ContextVar[bool] = contextvars.ContextVar('g6_tool_body', default=False)


def public_tool(fn):
    """Host-declared program instructions or opaque consent metadata, never source content."""
    fn._g6_public = _PUBLIC
    return fn


def _source_function(fn):
    """Capture provenance before FastMCP serialises/copies the returned object."""
    identity = object()
    public = getattr(fn, '_g6_public', None) is _PUBLIC
    from g6.core.consent_declarations import tool_declaration
    declaration = tool_declaration(fn)

    @wraps(fn)
    async def invoke(*args, **kwargs):
        capture = policy.current_capture()
        root = capture is not None and capture.expected_tool is identity and not _INSIDE.get()
        token = _INSIDE.set(True)
        try:
            arguments = inspect.signature(fn).bind(*args, **kwargs).arguments
            arguments = {k: v for k, v in arguments.items() if k != 'ctx'}
            declared = declaration.sources(arguments) if declaration is not None else ()
            with policy.request_sources(declared):
                value = fn(*args, **kwargs) if inspect.iscoroutinefunction(fn) else await anyio.to_thread.run_sync(
                    partial(fn, *args, **kwargs))
                if inspect.isawaitable(value):
                    value = await value
            if root:
                from g6.core.ledger import digest
                refs = policy.output_sources(value)
                try:
                    content_hash = digest(value)
                except ValueError:
                    content_hash = ''
                if declaration is not None and refs != ():
                    producer = declaration.sources(arguments, payload=content_hash)
                    carried = (*(refs if refs is not None else ()), *capture.sources)
                    refs = policy.boundary_sources(producer, carried=carried,
                                                   inherit=bool(producer or carried))
                metadata = policy.output_metadata(value)
                return_type = hints.get('return')
                if return_type is list or get_origin(return_type) is list:
                    metadata['empty_result'] = []
                capture.returned = value, content_hash, refs, public and refs is None, metadata
            return value
        finally:
            _INSIDE.reset(token)

    # Signature/types stay those of the original tool; *args/**kwargs are private.
    hints = get_type_hints(fn)
    signature = inspect.signature(fn)
    invoke.__signature__ = signature.replace(parameters=[p.replace(annotation=hints.get(p.name, p.annotation))
        for p in signature.parameters.values()], return_annotation=hints.get('return', signature.return_annotation))
    invoke.__annotations__ = hints
    invoke._g6_tool_identity = identity
    invoke._g6_declaration = declaration
    return invoke


def client_provider(ctx: Any) -> str:
    try:
        name = ctx.session.client_params.clientInfo.name.strip().lower()
    except (AttributeError, RuntimeError, TypeError):
        return 'cloud:unknown'
    # Exact protocol names, never a caller-supplied provider argument or a substring match.
    return {'claude-code': 'anthropic:claude_code', 'claude code': 'anthropic:claude_code',
            'codex': 'openai:codex', 'codex-mcp-client': 'openai:codex'}.get(name, 'cloud:unknown')


async def request_grant(ctx: Any, source: str, provider: str, *, description: str = '', label: str = '') -> dict:
    if not _PROVIDER.fullmatch(provider):
        return {'status': 'invalid', 'code': 'G6_E_CLOUD_CONSENT_INPUT'}
    capture = policy.current_capture()
    scope = policy.source_id(source)
    allowed, revision = await anyio.to_thread.run_sync(policy.preview, scope, provider)
    if allowed:
        return {'status': 'granted', 'source': scope, 'provider': provider}
    supplied = capture.supplied if capture is not None else ()
    caption = label if label and policy.input_source(label) in supplied else (
        source if policy.input_source(source) in supplied else scope)
    description = ('Exact task input: ' + description if description and policy.input_source(description) == scope
                   and scope in supplied else '')
    shown_scope = scope
    if capture is not None and capture.client is not None and capture.client[0] == 'cloud:unknown':
        caption = shown_scope = 'source:' + policy.sha256(scope)
        description = ''
    from g6.mcp.consent_transport import consent_form
    with consent_form():
        consent = await request_consent(ctx, f'Share data from source {caption} ({shown_scope}) with {provider}? {description} '
            'This covers files, passages and anything derived from that source. '
            'What is sent cannot be recalled. You can revoke future sharing at any time with g6_cloud_revoke. '
            'Accept only if you want this sharing; your supplied name is an attribution, not authenticated identity.')
    if consent.status != 'accepted':
        return {'status': consent.status, 'code': policy.REQUIRED}
    status = await anyio.to_thread.run_sync(policy._grant, scope, provider, revision, consent.answer)
    return {'status': status, 'source': scope, 'provider': provider}


def withheld_reply(result=None, *, job=None, metadata=None, provider='cloud:unknown', sources=()):
    from g6.core.door import blocked_reply
    from g6.core.ledger import digest
    result = result if isinstance(result, dict) else {}
    control = dict(metadata or {})
    if job is not None and job.get('digest') == digest(result):
        control = dict(job.get('metadata') or control)
    code = control.pop('error_code', policy.REQUIRED)
    empty_result = control.pop('empty_result', None)
    review_provider = control.pop('review_provider', '')
    has_report, has_text = control.pop('has_report', False), control.pop('has_text', False)
    message = policy.withheld_message(provider, sources)
    refused = {**blocked_reply(f'[{code}] {message}'), **control}
    refused['cloud_consent_code'] = policy.REQUIRED
    if empty_result is not None:
        refused['result'] = empty_result
    if has_report:
        refused['report'] = message
    if has_text:
        refused['text'] = message
    if job is not None and job.get('digest') == digest(result):
        if job.get('review_unsent'):
            recipient = f' to {review_provider}' if _PROVIDER.fullmatch(review_provider) else ''
            refused.update(action='stop', ending='untested', report=f'The review was not sent{recipient}. ' + message)
        if job['status'] == 'completed':
            return {'ok': True, 'job_id': job['job_id'], 'status': 'completed', 'result': refused}
        return {**refused, 'job_id': job['job_id'], 'status': job['status']}
    return refused


def protect_reply(ctx: Any, result: dict, sources, *, supplied=(), job=None, record=True) -> dict:

    try:
        payload = policy.sha256(json.dumps(result, sort_keys=True, default=str))
        with policy.admission(client_provider(ctx), sources, payload, supplied=supplied, disclose=record):
            return result
    except LLMError:
        return withheld_reply(result, job=job, metadata=policy.output_metadata(result),
                              provider=client_provider(ctx), sources=sources)


def _matches_return(returned, result):
    from fastmcp.tools.tool import ToolResult
    from g6.core.ledger import digest
    if returned is None or not returned[1] or result.meta is not None:
        return False
    value = returned[0]
    try:
        if digest(value) != returned[1] or ToolResult(content=value).content != result.content:
            return False
        expected = value if isinstance(value, dict) else {'result': value}
        return result.structured_content is None or digest(expected) == digest(result.structured_content)
    except (TypeError, ValueError):
        return False


def install(server) -> None:
    from fastmcp.server.middleware import Middleware
    from fastmcp.tools.tool import ToolResult
    original_tool = server.tool

    def source_tool(*args, **kwargs):
        if args and callable(args[0]):
            return original_tool(_source_function(args[0]), *args[1:], **kwargs)
        decorator = original_tool(*args, **kwargs)
        return lambda fn: decorator(_source_function(fn))

    server.tool = source_tool

    class ConsentEgress(Middleware):
        async def on_call_tool(self, context, call_next):
            from g6.mcp.consent_transport import install as install_transport, prepare
            ctx = context.fastmcp_context
            try:
                session = ctx.session
                supported = install_transport(session)
            except (AttributeError, RuntimeError):
                supported = False
            if not supported:
                return ToolResult(structured_content=withheld_reply())
            args = context.message.arguments or {}
            with policy.collect_sources() as capture:
                capture.client_arguments = deepcopy(args)
                tool = await server.get_tool(context.message.name)
                # The source wrapper has *args/**kwargs. FastMCP/Pydantic can
                # discard extra keys on that wrapper before signature.bind sees
                # them, including an agent's purported answer to a dialog.
                # Enforce the published argument names before that conversion.
                if set(args) - tool.parameters.get('properties', {}).keys():
                    from fastmcp.exceptions import ToolError
                    raise ToolError('Undeclared tool argument; use only the declared arguments.')
                capture.expected_tool = getattr(getattr(tool, 'fn', None), '_g6_tool_identity', None)
                capture.client = client_provider(ctx), session
                def inputs(value):
                    if isinstance(value, str):
                        capture.supplied.add(policy.input_source(value))
                    elif isinstance(value, dict):
                        for child in value.values():
                            inputs(child)
                    elif isinstance(value, list):
                        for child in value:
                            inputs(child)
                inputs(args)
                result = await call_next(context)
                returned = capture.returned
                matches = _matches_return(returned, result)
                public = matches and returned[3]
                refs = (() if public else returned[2]) if matches else None
                if refs == ():
                    prepare(session, ctx.request_id, result, ())
                    return result
                sources = refs if refs is not None else (policy.UNKNOWN,)
                prepare(session, ctx.request_id, result, sources, capture.supplied, capture.job,
                        metadata=returned[4] if matches else None)
                return result
    server.add_middleware(ConsentEgress())


def register(server, context_type):
    from g6.core.door import blocked_reply
    from g6.core.reliability_envelope import envelope_fields, warning_card
    from g6.mcp.consent_controls import public_instructions

    def reply(operation):
        try:
            result = operation()
        except (LLMError, ValueError):
            return public_instructions(blocked_reply(
                f'[{policy.STATE_UNAVAILABLE}] Sharing state is unavailable; no sharing was authorized.'))
        capture = policy.current_capture()
        if isinstance(result, dict) and capture is not None and capture.client is not None \
                and capture.client[0] == 'cloud:unknown':
            result = dict(result)
            if isinstance(result.get('source'), str):
                result['source'] = 'source:' + policy.sha256(result['source'])
            if 'provider' in result and not _PROVIDER.fullmatch(result['provider']):
                result['provider'] = 'cloud:unknown'
        return {'ok': True, 'result': result, **envelope_fields(reliability_label='qualified-draft',
            warning_card=warning_card('G6_E_CLOUD_CONSENT', 'Sharing settings and attempted disclosures.',
                                      'Read the source/provider scope. Revocation stops later sends.'), evidence=[])}

    async def g6_cloud_grant(source: str, provider: str, ctx) -> dict:
        """Ask in the client dialog before sharing a source/repository with a cloud provider. Sent data cannot be recalled."""
        try:
            result = await request_grant(ctx, source, provider)
        except (LLMError, ValueError):
            return blocked_reply(f'[{policy.STATE_UNAVAILABLE}] Sharing state is unavailable; no sharing was authorized.')
        if result['status'] != 'granted':
            return blocked_reply(f'[{policy.REQUIRED}] The dialog did not grant sharing; no data was sent.')
        return reply(lambda: result)
    g6_cloud_grant.__annotations__['ctx'] = context_type
    server.tool()(public_tool(g6_cloud_grant))

    @server.tool()
    @public_tool
    def g6_cloud_revoke(source: str, provider: str) -> dict:
        """Revoke a source/provider grant immediately, without a dialog. Data already sent cannot be recalled."""
        return reply(lambda: policy.revoke(source, provider))

    @server.tool()
    @public_tool
    def g6_cloud_grants() -> dict:
        """List active source/provider grants, their times and attribution hashes."""
        return reply(policy.grants)

    @server.tool()
    @public_tool
    def g6_disclosures() -> dict:
        """Read attempted cloud disclosures by source/file/passage ids, hashes, provider and time; never text."""
        return reply(policy.disclosures)
