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
#: What to record once this call's reply reaches the client (``g6.mcp.repair.observed``), kept by the call's middleware.
_DELIVERY: contextvars.ContextVar[list | None] = contextvars.ContextVar('g6_reply_delivery', default=None)


def public_tool(fn):
    """Host-declared program instructions or opaque consent metadata, never source content."""
    fn._g6_public = _PUBLIC
    return fn


def _in_thread(fn, *args, **kwargs):
    """A synchronous tool body, in its worker thread. It starts only if it claimed its start while its call's
    connection was open (``g6.mcp.consent_transport.claim_start``, under that connection's lock), and its call was not
    cancelled. Python cannot stop a thread, so a body already started runs on; its caller is abandoned
    (``abandon_on_cancel``): the connection does not wait for it, and what it returns is never sent, admitted or
    counted."""
    from g6.mcp.consent_transport import claim_start
    claim_start()
    return fn(*args, **kwargs)


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
            bound = inspect.signature(fn).bind(*args, **kwargs)
            arguments = {k: v for k, v in bound.arguments.items() if k != 'ctx'}
            # Only supplied arguments cross this ingress. Defaults and any prose
            # the handler constructs keep G6 ownership.
            from g6.core.external_data import caller_echo
            for name in tuple(bound.arguments):
                if name != 'ctx':
                    bound.arguments[name] = caller_echo(bound.arguments[name])
            declared = declaration.sources(arguments) if declaration is not None else ()
            with policy.request_sources(declared):
                value = fn(*bound.args, **bound.kwargs) if inspect.iscoroutinefunction(fn) else await anyio.to_thread.run_sync(
                    partial(_in_thread, fn, *bound.args, **bound.kwargs), abandon_on_cancel=True)
                if inspect.isawaitable(value):
                    value = await value
            if root:
                from g6.core.ledger import digest
                try:
                    from g6.core.external_data import detached
                    value, refs, metadata = detached(value)
                    # A refused reply gains its repair block (g6.mcp.repair: G6's own words, codes and templates)
                    # on the frozen snapshot, so the sources it carries are exactly the snapshot's.
                    repaired = _repaired(fn.__name__, arguments, value, capture)
                    from g6.core.agent_guidance import reply_for_agent
                    value = reply_for_agent(repaired)
                    # Keep all original source refs. Filtering gives no source a
                    # grant, and the ticket must bind the filtered bytes.
                    body = json.dumps(value, separators=(',', ':'), allow_nan=False)
                    content_hash = digest(value)
                except (TypeError, ValueError, RecursionError, MemoryError):
                    # Unsupported serialization cannot establish source authority.
                    refs, metadata, content_hash, body = (policy.UNKNOWN,), {}, '', ''
                if declaration is not None and refs != ():
                    producer = declaration.sources(arguments, payload=content_hash)
                    carried = (*(refs if refs is not None else ()), *capture.sources)
                    refs = policy.boundary_sources(producer, carried=carried,
                                                   inherit=bool(producer or carried))
                return_type = hints.get('return')
                if return_type is list or get_origin(return_type) is list:
                    metadata['empty_result'] = []
                capture.returned = body, content_hash, refs, public and refs is None, metadata
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


def _repaired(tool, arguments, value, capture):
    """``value`` (a frozen snapshot) with its repair block when it is a refusal; the same object otherwise, or when
    the repair cannot be made: the block never changes whether or how a call is answered. The attempt is counted
    only once the reply reaches the client, after the consent decision (the call's middleware hands the count to the
    transport); a reply withheld for consent was never seen, so it is not counted."""
    from g6.mcp.repair import observed, suppress_declined_dialogs
    try:
        session = capture.client[1] if capture.client else None
        value, record = observed(tool, arguments, value, session)
        delivery = _DELIVERY.get()
        if delivery is None:
            record()
        else:
            delivery.append(record)
        if tool != 'g6_select':
            return value
        return suppress_declined_dialogs(value, session, capture.client[0] if capture.client else '')
    except Exception:  # noqa: BLE001
        return value


def input_refusal(tool: str, fields, *, arguments=None, session=None, records=None) -> dict:
    """An undeclared argument is refused before executing or admitting any source.

    Only published field names and G6's program text are returned; no argument value is read back. With ``records``
    (the call's middleware), the refusal's ``Record`` is kept there and counted on delivery; else at once.
    """
    from g6.core.guidance_builders import blocked_reply
    from g6.mcp.repair import observe, observed

    reply = blocked_reply('[G6_E_INPUT_VALIDATION] Undeclared tool argument; use only the declared arguments.')
    if records is None:
        reply = observe(tool, arguments, reply, session)
    else:
        reply, record = observed(tool, arguments, reply, session)
        records.append(record)
    if reply.get('repair') is not None and reply['repair']['by'] == 'agent':
        reply['repair']['fill'] = sorted(fields)
    from g6.core.agent_guidance import reply_for_agent
    return reply_for_agent(reply)


def input_error_result(tool: str, fields, *, arguments=None, session=None, records=None):
    """G6's public validation envelope, retaining the MCP protocol's error status."""
    from fastmcp.tools.tool import ToolResult
    from mcp import types

    class InputErrorResult(ToolResult):
        def to_mcp_result(self):
            return types.CallToolResult(content=self.content, structuredContent=self.structured_content, isError=True)

    return InputErrorResult(structured_content=input_refusal(tool, fields, arguments=arguments, session=session,
                                                             records=records))


#: The MCP client names G6 knows, each with the provider its replies reach. Any other client is ``cloud:unknown``,
#: which no grant covers.
CLIENTS = {'claude-code': 'anthropic:claude_code', 'claude code': 'anthropic:claude_code',
           'codex': 'openai:codex', 'codex-mcp-client': 'openai:codex'}
#: G6's catalogue and selector (the owner's decision 1, 2026-11-16): built-in text. A reply of one of them to a client
#: G6 cannot name is marked as the reply to the client's own request (``requester``), so admission may send that
#: client back the exact bytes it supplied, unless the person revoked them, recording the disclosure; every other
#: source of the reply still needs what admission always needs, and ``cloud:unknown`` has no grant.
CATALOGUE_TOOLS = frozenset({'g6_select', 'g6_find', 'g6_describe', 'g6_components', 'g6_help'})


def client_provider(ctx: Any) -> str:
    try:
        name = ctx.session.client_params.clientInfo.name.strip().lower()
    except (AttributeError, RuntimeError, TypeError):
        return 'cloud:unknown'
    # Exact protocol names, never a caller-supplied provider argument or a substring match.
    return CLIENTS.get(name, 'cloud:unknown')


async def request_grant(ctx: Any, source: str, provider: str, *, description: str = '', label: str = '') -> dict:
    if not _PROVIDER.fullmatch(provider):
        return {'status': 'invalid', 'code': 'G6_E_CLOUD_CONSENT_INPUT'}
    capture = policy.current_capture()
    scope = policy.source_id(source)
    from g6.mcp.repair import declined
    session = getattr(ctx, 'session', None)     # no client context: no dialog can open, and no session holds a decision
    previous = declined(session, scope, provider)
    if previous:
        return {'status': previous, 'code': policy.REQUIRED}
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
        declined(session, scope, provider, consent.status)
        return {'status': consent.status, 'code': policy.REQUIRED}
    status = await anyio.to_thread.run_sync(policy._grant, scope, provider, revision, consent.answer)
    return {'status': status, 'source': scope, 'provider': provider}


def withheld_reply(result=None, *, job=None, metadata=None, provider='cloud:unknown', sources=(), session=None):
    from g6.core.guidance_builders import blocked_reply

    from g6.core.ledger import digest
    result = result if isinstance(result, dict) else {}
    control = dict(metadata or {})
    if job is not None and job.get('digest') == digest(result):
        control = dict(job.get('metadata') or control)
    code = control.pop('error_code', policy.REQUIRED)
    empty_result = control.pop('empty_result', None)
    review_provider = control.pop('review_provider', '')
    has_report, has_text = control.pop('has_report', False), control.pop('has_text', False)
    from g6.mcp.repair import consent_block, unnamed_client_step
    message = ('Results were withheld from a source not shared with this provider. Tell the person. ' +
               policy.withheld_message(provider, sources).removeprefix(policy.WITHHELD).lstrip())
    if not _PROVIDER.fullmatch(provider):           # no grant covers it: the dialog cannot help
        provider = 'cloud:unknown'
        message += ' ' + unnamed_client_step()
    refused = {**blocked_reply(f'[{code}] {message}'), **control}
    refused['cloud_consent_code'] = policy.REQUIRED
    refused['repair'] = consent_block(session, sources, provider)
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
            return {'ok': True, 'job_id': job['job_id'], 'status': 'completed', 'result': refused,
                    'repair': refused['repair']}
        return {**refused, 'job_id': job['job_id'], 'status': job['status']}
    return refused


def protect_reply(ctx: Any, result: dict, sources, *, supplied=(), job=None, record=True) -> dict:
    metadata = {}
    try:
        from g6.core.external_data import detached
        result, known, metadata = detached(result)
        body = json.dumps(result, separators=(',', ':'), allow_nan=False)
        sources = policy.boundary_sources(sources, carried=known or ())
        payload = policy.sha256(body)
        with policy.admission(client_provider(ctx), sources, payload, supplied=supplied, disclose=record):
            return result
    except (LLMError, ValueError, RecursionError, MemoryError):
        return withheld_reply(result, job=job, metadata=metadata,
                              provider=client_provider(ctx), sources=sources, session=ctx.session)


def _matches_return(returned, result, wire=None):
    """Whether the rendered reply is the frozen return, read through one serialisation of that reply.

    ``wire`` is the serialisation the caller also cuts the ticket from. Matching the live ``result`` and then
    serialising it again for the ticket would let a change between the two go out under the matched sources.
    """
    from fastmcp.tools.tool import ToolResult
    from g6.core.ledger import digest
    from g6.mcp.consent_transport import call_body
    if returned is None or not returned[1]:
        return False
    try:
        wire = call_body(result) if wire is None else wire
        value = json.loads(returned[0])
        if '_meta' in wire or digest(value) != returned[1]:
            return False
        if call_body(ToolResult(content=value))['content'] != wire['content']:
            return False
        expected = value if isinstance(value, dict) else {'result': value}
        return 'structuredContent' not in wire or digest(expected) == digest(wire['structuredContent'])
    except (KeyError, TypeError, ValueError, RecursionError, MemoryError):
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
            from g6.mcp.consent_transport import call_body, install as install_transport, prepare
            from g6.mcp.repair import Record
            ctx = context.fastmcp_context
            session = None
            try:
                session = ctx.session
                supported = install_transport(session)
            except (AttributeError, RuntimeError):
                supported = False
            if not supported:
                return ToolResult(structured_content=withheld_reply(session=session, provider=client_provider(ctx)))
            args = context.message.arguments or {}
            with policy.collect_sources() as capture:
                capture.client_arguments = deepcopy(args)
                tool = await server.get_tool(context.message.name)
                # The source wrapper has *args/**kwargs. FastMCP/Pydantic can
                # discard extra keys on that wrapper before signature.bind sees
                # them, including an agent's purported answer to a dialog.
                # Enforce the published argument names before that conversion.
                if set(args) - tool.parameters.get('properties', {}).keys():
                    refusals: list = []
                    result = input_error_result(context.message.name, tool.parameters.get('properties', {}),
                                                arguments=args, session=session, records=refusals)
                    wire = result.to_mcp_result().model_dump(mode='json', by_alias=True, exclude_none=True)
                    prepare(session, ctx.request_context.request_id, result, (), body=wire,
                            delivered=refusals[0] if refusals else None)
                    return result
                capture.expected_tool = getattr(getattr(tool, 'fn', None), '_g6_tool_identity', None)
                capture.client = client_provider(ctx), session
                def inputs(value):
                    if isinstance(value, str):
                        try:
                            capture.supplied.add(policy.input_source(value))
                        except UnicodeEncodeError:
                            # A lone surrogate has no UTF-8 bytes, so no exact input can be named: it is not counted
                            # as the client's own, and the tool gives its own stable refusal or answer.
                            pass
                    elif isinstance(value, dict):
                        for child in value.values():
                            inputs(child)
                    elif isinstance(value, list):
                        for child in value:
                            inputs(child)
                inputs(args)
                records: list = []
                token = _DELIVERY.set(records)
                try:
                    result = await call_next(context)
                finally:
                    _DELIVERY.reset(token)
                delivered = Record(lambda evidence: [record.sent(evidence) for record in records],
                                   lambda evidence: [record(evidence) for record in records])
                returned = capture.returned
                # The rendered reply is serialised once, here. The frozen return is
                # matched against that serialisation and the ticket is cut from it,
                # so a reply changed at any later moment no longer has its ticket's
                # digest when the transport takes it, and goes as an unknown source.
                wire = call_body(result)
                matches = _matches_return(returned, result, wire)
                public = matches and returned[3]
                refs = (() if public else returned[2]) if matches else None
                if refs == ():
                    prepare(session, ctx.request_context.request_id, result, (), body=wire, delivered=delivered)
                    return result
                sources = refs if refs is not None else (policy.UNKNOWN,)
                requester = capture.client[0] == 'cloud:unknown' and context.message.name in CATALOGUE_TOOLS
                prepare(session, ctx.request_context.request_id, result, sources, capture.supplied, capture.job,
                        metadata=returned[4] if matches else None, body=wire, delivered=delivered,
                        requester=requester)
                return result
    server.add_middleware(ConsentEgress())


def register(server, context_type):
    from g6.core.guidance_builders import blocked_reply
    from g6.core.guidance_builders import envelope_fields, warning_card
    from g6.mcp.consent_controls import public_instructions

    def reply(operation):
        try:
            result = operation()
        except (LLMError, ValueError, RecursionError, MemoryError):
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
        except (LLMError, ValueError, RecursionError, MemoryError):
            return blocked_reply(f'[{policy.STATE_UNAVAILABLE}] Sharing state is unavailable; no sharing was authorized.')
        if result['status'] != 'granted':
            previous = result['status'] if result['status'] in ('declined', 'cancelled') else 'did not grant'
            return blocked_reply(f'[{policy.REQUIRED}] The person {previous} sharing; no data was sent.')
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
