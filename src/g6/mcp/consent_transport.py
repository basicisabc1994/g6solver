# SPDX-License-Identifier: Apache-2.0
"""Admission at the MCP session's transport handoff, after tool rendering.

Source tickets carry provenance, never cached permission. A worker owns the ledger
lock until the session stream accepts the response; revocation and that handoff
therefore have one order. Data already handed to transport cannot be recalled.
"""
from __future__ import annotations

import contextlib
from g6.core.decoder_limits import json_value as _decode_json

import contextvars
import functools
import json
import threading
import weakref
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
from g6.mcp.repair import INFERRED, WRITTEN

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
    #: What to record about this reply (``g6.mcp.repair.Record``), called once these exact bytes were written on a
    #: writer G6 owns (``Witness``), or at the handoff elsewhere (``_handed``); never for a reply withheld in their place.
    delivered: Callable[[], None] | None = field(default=None, compare=False, repr=False)
    #: The reply to the client's own request, from a catalogue or selector tool to a client G6 cannot name
    #: (``g6.mcp.cloud_consent.CATALOGUE_TOOLS``). Admission reads it once it takes that mark (the issuance hunk);
    #: until then such a reply is withheld, as any reply to ``cloud:unknown`` is.
    requester: bool = False


class Witness:
    """The writer of one connection, which G6 owns, seeing each message written. ``expect`` files a send's own callback
    under that message object; ``take(message)`` (the writer took it from the stream) leaves the decision to that
    message's own write; ``written(message)`` (G6's stdio writer once the whole line was written and flushed, or the
    in-process client once it took it) fires the callback, once; ``discard`` removes one without firing.
    ``untaken_discard`` (a send cancelled or failed on the sender's side) discards only a message the stream never
    took. ``close`` (the connection ended) discards every callback still held, firing none. ``claim`` (a synchronous
    tool body about to start) succeeds only while the connection is open."""

    def __init__(self) -> None:
        self._expected: dict[int, tuple[SessionMessage, Callable[[], None]]] = {}
        self._taken: set[int] = set()
        self._gate = threading.Lock()
        self._open = True

    def claim(self) -> None:
        """A synchronous tool body on this connection is about to start, in its worker thread. The claim and ``close``
        take the connection's lock, so a body claims its start either before the connection ends (it runs; once the
        connection ends it is abandoned, never waited on, sent, admitted or counted) or not at all: none starts after.
        A call already cancelled does not start either."""
        with self._gate:
            if not self._open:
                raise RuntimeError("the connection ended before this tool started")
            anyio.from_thread.check_cancelled()

    def expect(self, message: SessionMessage, callback: Callable[[], None]) -> int:
        self._expected[id(message)] = (message, callback)      # the message held, so its identity is not reused
        return id(message)

    def take(self, message: SessionMessage) -> None:
        if id(message) in self._expected:
            self._taken.add(id(message))

    def untaken_discard(self, key: int | None) -> None:
        if key not in self._taken:
            self.discard(key)

    def discard(self, key: int | None) -> None:
        self._taken.discard(key)
        self._expected.pop(key, None)

    def written(self, message: SessionMessage) -> None:
        self._taken.discard(id(message))
        held = self._expected.pop(id(message), None)
        if held is not None and held[0] is message:
            try:
                held[1]()
            except Exception:  # noqa: BLE001 - recording never fails a write already made
                pass

    def close(self) -> None:
        with self._gate:
            self._open = False
        self._expected.clear()
        self._taken.clear()


#: The connections whose writer G6 owns (``g6.mcp.stdio``, ``G6MemoryTransport``), named when the connection starts,
#: each with the send stream it was named by (both weakly). A send on any stream of one of them is never counted on
#: inferred evidence: with its witness gone (the connection is closing or closed) the send is dropped.
_OWNED: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
#: Each owned stream's live witness, removed when its writer or connection ends (``unwitness``).
_WITNESSES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _connection(stream):
    """The connection ``stream`` sends on: an anyio memory stream's state, which its clones share and wrappers that
    delegate attributes (``_Stream``) reach; the stream itself otherwise."""
    return getattr(stream, '_state', stream)


def _owned(stream) -> tuple[bool, Witness | None]:
    """Whether ``stream`` sends on a connection G6 owns, and that connection's live witness (None once it ended)."""
    try:
        named = _OWNED.get(_connection(stream))
    except TypeError:                       # a stream that cannot be named weakly was never named
        return False, None
    if named is None:
        return False, None
    origin = named()
    return True, (_WITNESSES.get(origin) if origin is not None else None)


def witness(stream) -> Witness:
    """The witness of ``stream``, a send stream whose writer G6 owns, from the connection's start."""
    _OWNED[_connection(stream)] = weakref.ref(stream)
    _WITNESSES[stream] = seen = Witness()
    return seen


def unwitness(stream) -> None:
    """The connection of ``stream`` ended: its witness discards every callback still held, firing none. Called from a
    ``finally`` on every exit path; it neither waits nor raises."""
    try:
        seen = _WITNESSES.pop(stream, None)
        if seen is not None:
            seen.close()
    except Exception:  # noqa: BLE001 - connection cleanup never replaces the error that ended the connection
        pass


#: The witness of the connection G6 owns that a tool call runs on, copied into each request's task and worker thread.
_CONNECTION: contextvars.ContextVar[Witness | None] = contextvars.ContextVar('g6_mcp_connection', default=None)


@contextmanager
def calls_on(seen: Witness):
    """Tool calls started inside run on the connection ``seen`` witnesses: a synchronous body claims its start there."""
    token = _CONNECTION.set(seen)
    try:
        yield
    finally:
        _CONNECTION.reset(token)


def claim_start() -> None:
    """A synchronous tool body is about to start, in its worker thread: on a connection G6 owns it claims its start
    (``Witness.claim``); elsewhere a call already cancelled does not start."""
    seen = _CONNECTION.get()
    if seen is None:
        anyio.from_thread.check_cancelled()
    else:
        seen.claim()


class _Taking:
    """The in-process client's read stream: each message it takes is ``written`` to the server's witness."""

    def __init__(self, inner, seen: Witness) -> None:
        self.inner, self.seen = inner, seen

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def receive(self):
        message = await self.inner.receive()
        if isinstance(message, SessionMessage):
            self.seen.written(message)
        return message

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.inner.__aexit__(*args)


@functools.lru_cache(maxsize=1)
def _memory_transport():
    from fastmcp.client.transports import FastMCPTransport

    class G6MemoryTransport(FastMCPTransport):
        """FastMCP's in-memory transport (``FastMCPTransport.connect_session``, fastmcp 2.14), whose client read
        stream tells G6 each reply it takes, so a reply is counted as "written" when the in-process client has it."""

        @contextlib.asynccontextmanager
        async def connect_session(self, **session_kwargs):
            from fastmcp.client.transports import _enter_server_lifespan
            from mcp import ClientSession
            from mcp.shared.memory import create_client_server_memory_streams

            async with create_client_server_memory_streams() as ((client_read, client_write),
                                                                  (server_read, server_write)):
                seen, raised = witness(server_write), None
                try:
                    async with anyio.create_task_group() as tg, _enter_server_lifespan(server=self.server):
                        with calls_on(seen):           # the server's task, and so each of its calls, runs on it
                            tg.start_soon(lambda: self.server._mcp_server.run(
                                server_read, server_write, self.server._mcp_server.create_initialization_options(),
                                raise_exceptions=self.raise_exceptions))
                        try:
                            async with ClientSession(read_stream=_Taking(client_read, seen),
                                                     write_stream=client_write, **session_kwargs) as client_session:
                                yield client_session
                        except BaseException as exc:  # noqa: BLE001 - raised again once the group closed, as FastMCP
                            raised = exc
                        finally:
                            unwitness(server_write)    # ended before it is cancelled: no body starts after this
                            tg.cancel_scope.cancel()
                finally:
                    # the connection ended, on whatever path (an error, a cancellation): a reply it never read is
                    # never counted; unwitness neither waits nor raises
                    unwitness(server_write)
                if raised is not None:
                    raise raised
    return G6MemoryTransport


def __getattr__(name):
    if name == 'G6MemoryTransport':      # built on first use, so serving never imports FastMCP's client
        return _memory_transport()
    raise AttributeError(name)


def _handed(ticket: Ticket) -> None:
    """The ticket's reply was handed to a client on a transport whose writer G6 cannot see (the stock in-process
    client, HTTP, SSE): every delivery effect runs now, at the handoff, on evidence ``inferred``, so retries stay
    bounded there and no row claims more than the handoff. It takes the repair state's own lock and queues log rows;
    it never waits, so it may run while admission holds the sharing ledger."""
    if ticket.delivered is None:
        return
    try:
        ticket.delivered(INFERRED)
    except Exception:  # noqa: BLE001 - recording never fails a reply already sent
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
        controls = _decode_json(json.dumps({'job': job, 'metadata': metadata}))
    except (RecursionError, MemoryError):
        raise json.JSONDecodeError("a JSON record exceeds decoder limits", "", 0) from None
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
        """Send ``packet``. On a writer G6 owns, its delivery effects wait for its own callback (``Witness``);
        otherwise they run in the same step as the send (``_handed``), so the client cannot read it and call again
        first. Nothing is awaited after the send, so nothing waits on the client while admission holds the ledger."""
        owned, seen = _owned(self.inner)
        if owned:
            if seen is None:
                return                    # the connection is closing: dropped, never counted, never inferred
            key = seen.expect(packet, functools.partial(ticket.delivered, WRITTEN)) if ticket.delivered else None
            try:
                await self.inner.send(packet)
            except BaseException:
                # cancelled or failed on this side: a message the writer already took is decided by its own write;
                # one the stream never took is never written, so its callback goes
                seen.untaken_discard(key)
                raise
            return
        await self.inner.send(packet)
        _handed(ticket)


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
