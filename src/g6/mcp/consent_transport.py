# SPDX-License-Identifier: Apache-2.0
"""Admission at the MCP session's transport handoff, after tool rendering.

Source tickets carry provenance, never cached permission. A worker owns the ledger
lock until the session stream accepts the response; revocation and that handoff
therefore have one order. Data already handed to transport cannot be recalled.
"""
from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MethodType
from typing import Callable

import anyio
from mcp import types
from mcp.shared.message import SessionMessage

from g6.core import cloud_consent as policy
from g6.core.ledger import digest
from g6.core.llm import LLMError
from g6.core.agent_guidance import WITHHELD

_CONTROL = contextvars.ContextVar('g6_public_mcp_control', default=False)
_OUTBOUND = contextvars.ContextVar('g6_mcp_source_ticket', default=None)


@contextmanager
def consent_form():
    """The sharing dialog contains program text, scope ids and the client's own literal input."""
    token = _CONTROL.set(True)
    try:
        yield
    finally:
        _CONTROL.reset(token)


def provider(session):
    from g6.mcp.cloud_consent import client_provider
    from types import SimpleNamespace
    return client_provider(SimpleNamespace(session=session))


def call_body(result):
    return types.CallToolResult(content=result.content, structuredContent=result.structured_content,
                                _meta=result.meta).model_dump(mode='json', by_alias=True, exclude_none=True)


@dataclass(frozen=True)
class Ticket:
    sources: tuple
    provider: str
    supplied: frozenset[str]
    body_sha256: str
    job: dict | None = None
    control: dict | None = None
    #: What to record about this reply (``g6.mcp.repair.Record``): ``sent()`` once the transport handed these exact
    #: bytes to the client, never for a reply withheld in their place; called once they are seen written
    #: (``settled``), which counts the attempt the agent saw.
    delivered: Callable[[], None] | None = field(default=None, compare=False, repr=False)
    #: The reply to the client's own request, from a catalogue or selector tool to a client G6 cannot name
    #: (``g6.mcp.cloud_consent.CATALOGUE_TOOLS``). Admission reads it once it takes that mark (the issuance hunk);
    #: until then such a reply is withheld, as any reply to ``cloud:unknown`` is.
    requester: bool = False


def _written(stream) -> bool | None:
    """Whether a message handed to ``stream`` earlier was written to the client, as far as can be seen now: the
    stream's one reader takes a message and comes back for the next only once it has handled it (the stdio writer
    after it wrote and flushed the line, an in-process client after it read it). So the reader waiting again with
    nothing left in the stream: written; the reader gone (a write failed and the writer stopped), or a stream with no
    such statistics: not seen written; anything else (the reader still busy): ``None``, not decided yet."""
    statistics = getattr(stream, 'statistics', None)
    if statistics is None:
        return False
    seen = statistics()
    if seen.open_receive_streams == 0:
        return False
    if seen.current_buffer_used == 0 and seen.tasks_waiting_receive > 0:
        return True
    return None


def _handed(session, stream, ticket: Ticket) -> None:
    """The ticket's reply was handed to the client: record that (``sent``), and keep it until its writing is seen."""
    if ticket.delivered is None:
        return
    try:
        ticket.delivered.sent()
    except Exception:  # noqa: BLE001 - recording never fails a reply already sent
        pass
    unseen = getattr(session, '_g6_unseen', None)
    if unseen is None:
        unseen = session._g6_unseen = []
    unseen.append((stream, ticket))


def settled(session) -> None:
    """Before a call of this session is assessed, and before each reply it sends: count each reply it already handed
    to the client that is now seen written (``_written``), in the order they went; drop one seen not written; stop at
    the first not decided yet, kept with those after it for later. Nothing here waits, so nothing waits while
    admission holds the sharing ledger (it only takes the repair state's own lock and queues log rows); a reply the
    session sends last, with no call or reply after it, is never counted."""
    unseen = getattr(session, '_g6_unseen', None)
    for entry in list(unseen or ()):
        written = _written(entry[0])
        if written is None:
            break
        unseen.remove(entry)
        if written:
            try:
                entry[1].delivered()
            except Exception:  # noqa: BLE001 - counting an attempt never fails a call
                pass


def _request_key(request_id):
    # MCP's request-id schema accepts strings and integers, not bool/float/null.
    # Invalid/absent ids never create or consume another request's authority.
    return (type(request_id), request_id) if type(request_id) in (str, int) else None


def prepare(session, request_id, result, sources, supplied=(), job=None, *, metadata=None, body=None, delivered=None,
            requester=False):
    """Cut the ticket for this reply. ``body`` is the caller's one serialisation of ``result``, when it has matched
    the sources against one: the ticket then names those bytes, never a second reading of the live reply.
    ``delivered`` is the reply's ``g6.mcp.repair.Record`` (``Ticket.delivered``); ``requester`` marks a catalogue or
    selector reply to a client G6 cannot name (``Ticket.requester``)."""
    key = _request_key(request_id)
    if key is None:
        return
    body = call_body(result) if body is None else body
    # Refusal controls and retained-job metadata must not retain live aliases.
    try:
        controls = json.loads(json.dumps({'job': job, 'metadata': metadata}))
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None
    session._g6_tickets[key] = Ticket(tuple(sources), provider(session), frozenset(supplied),
                                    digest(body), controls['job'], controls['metadata'], delivered, bool(requester))


def _refused(message, ticket, recipient='cloud:unknown', session=None):
    from g6.mcp.cloud_consent import withheld_reply
    root = message.message.root
    if isinstance(root, types.JSONRPCResponse):
        original = root.result.get('structuredContent', {})
        refused = withheld_reply(original, job=ticket.job if ticket else None,
                                 metadata=ticket.control if ticket else None,
                                 provider=recipient, sources=ticket.sources if ticket else (), session=session)
        result = types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(refused))],
                                      structuredContent=refused)
        root = types.JSONRPCResponse(jsonrpc='2.0', id=root.id,
            result=result.model_dump(mode='json', by_alias=True, exclude_none=True))
        return SessionMessage(types.JSONRPCMessage(root), metadata=message.metadata)
    raise LLMError(f'{policy.REQUIRED}: {WITHHELD}')


class _Stream:
    def __init__(self, inner, session):
        self.inner, self.session = inner, session

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def clone(self):
        return _Stream(self.inner.clone(), self.session)

    def send_nowait(self, message):
        raise LLMError(f'{policy.REQUIRED}: nonblocking sends have no source-aware handoff; use send')

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.inner.__aexit__(*args)

    async def send(self, message):
        # Freeze the actual packet. Neither a later mutation nor a serializer's
        # additional text can inherit an earlier result's source ticket.
        packet = SessionMessage(types.JSONRPCMessage.model_validate_json(
            message.message.model_dump_json(by_alias=True, exclude_none=True)), metadata=message.metadata)
        root, ticket = packet.message.root, _OUTBOUND.get()
        if isinstance(root, types.JSONRPCResponse | types.JSONRPCError):
            body = root.result if isinstance(root, types.JSONRPCResponse) else root.error.model_dump(exclude_none=True)
            if ticket is None or ticket.body_sha256 != digest(body):
                ticket = Ticket((policy.UNKNOWN,), provider(self.session), frozenset(), digest(body))
        else:
            control = _CONTROL.get() or (root.method in ('ping', 'roots/list', 'notifications/tools/list_changed',
                'notifications/prompts/list_changed', 'notifications/resources/list_changed') and not root.params)
            capture = policy.current_capture()
            actual = provider(self.session)
            supplied = frozenset(capture.supplied) if capture is not None and capture.client is not None \
                and capture.client[0] == actual and capture.client[1] is self.session else frozenset()
            ticket = Ticket(() if control else policy.current_sources() or (policy.UNKNOWN,),
                            actual, supplied, digest(root.model_dump(mode='json', exclude_none=True)))
        if not ticket.sources:
            await self._hand(packet, ticket)
            return

        def handoff():
            actual = provider(self.session)
            supplied = ticket.supplied if actual == ticket.provider else ()
            try:
                with policy.admission(actual, ticket.sources, ticket.body_sha256, supplied=supplied):
                    anyio.from_thread.run(self._hand, packet, ticket)
            except (LLMError, RecursionError, MemoryError):
                anyio.from_thread.run(self.inner.send, _refused(packet, ticket, actual, self.session))
        await anyio.to_thread.run_sync(handoff)

    async def _hand(self, packet, ticket: Ticket) -> None:
        """Send ``packet`` and record it handed in the same step: the client cannot read it, and call again, before the
        session knows it went (``settled``). The replies handed before it are settled first, while the stream still
        shows whether they were written. Nothing is awaited after the send, so nothing waits on the client while
        admission holds the sharing ledger."""
        settled(self.session)
        await self.inner.send(packet)
        _handed(self.session, self.inner, ticket)


class _Session:
    async def _send_response(self, request_id, response):
        ticket = self._g6_tickets.pop(_request_key(request_id), None)
        if isinstance(response, types.ErrorData):
            response = types.ErrorData(code=response.code, message=WITHHELD)
            ticket = Ticket((), provider(self), frozenset(), digest(response.model_dump(exclude_none=True)))
        elif isinstance(response.root, types.CallToolResult) and response.root.isError:
            # Framework input errors precede the producer and have no source
            # ticket. Keep their protocol error status, but never echo arbitrary
            # exception text (which can contain an unshared value or host path).
            # G6's own public input envelope may keep its repair only under its
            # exact, already prepared empty-source ticket. No source binding is
            # dropped or inferred from an error's words/status.
            if ticket is None or ticket.sources or ticket.body_sha256 != digest(response.root.model_dump(
                    mode='json', by_alias=True, exclude_none=True)):
                response = types.ServerResult(types.CallToolResult(isError=True, content=[
                    types.TextContent(type='text', text='Invalid tool call; check the declared arguments.')]))
                ticket = Ticket((), provider(self), frozenset(), digest(response.model_dump(
                    mode='json', by_alias=True, exclude_none=True)))
        elif ticket is None and isinstance(response.root, (types.InitializeResult, types.ListToolsResult, types.EmptyResult)):
            ticket = Ticket((), provider(self), frozenset(), digest(response.model_dump(
                mode='json', by_alias=True, exclude_none=True)))
        token = _OUTBOUND.set(ticket)
        try:
            await self._g6_original_response(request_id, response)
        finally:
            _OUTBOUND.reset(token)


def install(session) -> bool:
    """Only this instance changes; an unsupported session cannot send source content."""
    if getattr(getattr(session, '_send_response', None), '__func__', None) is _Session._send_response:
        return isinstance(session._write_stream, _Stream)
    try:
        original = session._send_response
        stream = session._write_stream
        session._g6_original_response = original
        session._g6_tickets = {}
        session._write_stream = _Stream(stream, session)
        session._send_response = MethodType(_Session._send_response, session)
        return True
    except (AttributeError, TypeError):
        return False
