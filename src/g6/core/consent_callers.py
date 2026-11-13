# SPDX-License-Identifier: Apache-2.0
"""Additive wrappers around the certificate-bound provider implementations."""
from __future__ import annotations

import ipaddress
import sys
import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit
import urllib.request

from g6.core import host_models, llm
from g6.core.cloud_consent import REQUIRED, _refusal, admission, admitted, current_sources, sha256
from g6.core.consent_declarations import model_sources
from g6.core.ledger import digest

_CLAUDE = host_models.CALLERS['claude_code']
_CODEX = host_models.CALLERS['codex']

# Class identities are resolved from loaded G6 modules, never from a caller's
# rendered name, declared model, or an opaque wrapper's attributes.
CALLER_KINDS = {
    'g6.core.llm.LLMCaller': 'protocol',
    'g6.core.llm.EmptyRetry': 'wrapper:_inner',
    'g6.core.llm.TimingLLMCaller': 'wrapper:_inner',
    'g6.core.llm.FallbackCaller': 'wrapper:_callers',
    'g6.core.llm.FakeLLM': 'process',
    'g6.core.llm.NoModel': 'no-send',
    'g6.core.llm.UnusableModel': 'no-send',
    'g6.core.llm._NoKey': 'no-send',
    'g6.core.llm.OllamaCaller': 'endpoint',
    'g6.core.llm.OpenAICompatibleCaller': 'endpoint',
    'g6.core.llm.AnthropicCaller': 'sdk-endpoint',
    'g6.core.host_models._HostCaller': 'abstract-host',
    'g6.core.host_models.ClaudeCodeCaller': 'cloud-host',
    'g6.core.host_models.CodexCaller': 'cloud-host',
    'g6.core.consent_callers._Consent': 'guard',
    'g6.core.consent_callers._Unknown': 'unknown',
    'g6.core.consent_callers.CallerWrapper': 'wrapper:inner',
    'g6.core.consent_callers.ConsentClaude': 'cloud-host',
    'g6.core.consent_callers.ConsentCodex': 'cloud-host',
    'g6.reasoning.budget.MeteredCaller': 'wrapper:inner',
    'g6.metaharness.measurement.TraceCaller': 'wrapper:inner',
    'g6.metaharness.loop._Counted': 'wrapper:inner',
    'g6.harness.verification_proposer._Limits': 'wrapper:metered',
    'g6.harness.verification_proposer.StudyCaller': 'wrapper:_caller',
    'g6.harness.completion_loop.ToolSession': 'cloud-tools',
    'g6.self_maintenance.usage._Watched': 'cloud-host',
    'g6.self_maintenance.usage.WatchedClaude': 'cloud-host',
    'g6.self_maintenance.usage.WatchedCodex': 'cloud-host',
    'g6.grounding.judges.nli.LocalNLICaller': 'process',
    'g6.metaharness.lora.AdapterCaller': 'process',
    'g6.core.executor_contracts.ModelProvider': 'protocol',
}


def caller_kind(caller: object) -> str:
    if isinstance(caller, CallerWrapper):
        return 'wrapper:inner'
    for qualified, kind in CALLER_KINDS.items():
        module, _, name = qualified.rpartition('.')
        owner = sys.modules.get(module)
        if owner is not None and type(caller) is getattr(owner, name, None):
            return kind
    return 'unknown'


class CallerWrapper:
    """Published contract for wrappers that transform requests before delegating.

    A wrapper exposes its actual sending caller as ``inner`` and delegates every
    request through that object. The guard replaces ``inner`` with its guarded
    form, so rewriting and retries retain the backend's send-time check. Merely
    having an ``inner`` attribute does not implement this contract: membership
    is determined by this base class's identity. This is a trusted host-code
    contract, like request_sources; it grants no source or provider permission.
    """

    def __init__(self, inner):
        self.inner = inner

    @property
    def _inner(self):
        """Compatibility with the model-budget and enrolment wrapper contract."""
        return self.inner

    @_inner.setter
    def _inner(self, value):
        self.inner = value

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        return self.inner.complete(system, user, max_tokens=max_tokens)


class SDKClient:
    """Trusted host contract for an identifiable SDK-shaped provider client.

    An implementation names its actual configured ``base_url`` and sends directly
    there, without proxying or request rewriting. Tests use this identity for
    synthetic provider endpoints. A foreign object with a similarly named
    attribute does not implement this contract.
    """

    base_url: str


class SDKClientWrapper:
    """SDK wrappers expose the actual client, rather than inventing its recipient."""

    def __init__(self, inner):
        self.sdk_inner = inner


def sdk_endpoint(client, seen=frozenset()):
    if id(client) in seen:
        return None
    if isinstance(client, SDKClientWrapper):
        return sdk_endpoint(client.sdk_inner, seen | {id(client)})
    module = sys.modules.get('anthropic')
    native = getattr(module, 'Anthropic', None) if module is not None else None
    if isinstance(client, SDKClient) or isinstance(native, type) and type(client) is native:
        return getattr(client, 'base_url', None)
    return None


def sdk_destination(client, seen=frozenset()):
    """Ask the SDK's HTTP client to resolve mounts and environment proxies.

    Only the native direct pool establishes locality. Unknown clients, custom
    routing and custom transports cannot establish a first hop. This deliberately
    depends on the installed client's routing API, not a second proxy parser.
    """
    unknown = Destination('cloud:unknown')
    if id(client) in seen:
        return unknown
    if isinstance(client, SDKClientWrapper):
        return sdk_destination(client.sdk_inner, seen | {id(client)})
    url = sdk_endpoint(client)
    if not url:
        return unknown
    url = str(url).rstrip('/')
    if isinstance(client, SDKClient):
        # The published host fake contract explicitly sends directly to base_url.
        return _sdk_direct_destination(url)
    http = getattr(client, '_client', None)
    for name, core_name in (('httpx2', 'httpcore2'), ('httpx', 'httpcore')):
        module, core = sys.modules.get(name), sys.modules.get(core_name)
        if module is None or core is None or not isinstance(http, module.Client):
            continue
        try:
            sdk_base = sys.modules.get('anthropic._base_client')
            supported = (module.Client, getattr(sdk_base, 'DefaultHttpxClient', None),
                         getattr(sdk_base, 'SyncHttpxClientWrapper', None))
            if type(http) not in supported or http._auth is not None or any(http._event_hooks.values()):
                return unknown
            for method in ('_transport_for_url', 'send', 'request', 'build_request', '_send_single_request',
                           '_send_handling_auth', '_send_handling_redirects'):
                if method in vars(http) or getattr(type(http), method) is not getattr(module.Client, method):
                    return unknown
            transport = http._transport_for_url(module.URL(url))
            if type(transport) is not module.HTTPTransport:
                return unknown
            pool = transport._pool
            if type(pool) is core.ConnectionPool:
                if getattr(pool, '_uds', True) is not None:
                    return unknown
                return _sdk_direct_destination(url)
            if type(pool) not in (core.HTTPProxy, core.SOCKSProxy):
                return unknown
            proxy = pool._proxy_url
            hop = str(module.URL(scheme=proxy.scheme.decode('ascii'), host=proxy.host.decode('ascii'),
                                 port=proxy.port)).rstrip('/')
            # Even a loopback proxy can forward off-machine. It needs consent.
            return Destination('endpoint:' + sha256(hop))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return unknown
    return unknown


def _sdk_direct_destination(url):
    if loopback(url):
        return Destination('local:loopback', True)
    return Destination('anthropic:api' if url == 'https://api.anthropic.com' else 'endpoint:' + sha256(url))


@dataclass
class _SDKCall:
    client: object
    provider: str
    thread: int
    sources: tuple
    active: bool = True


_SDK_CALL = contextvars.ContextVar('g6_admitted_sdk_call', default=None)


@contextmanager
def _sdk_call(client, provider):
    if client is None:
        yield
        return
    call = _SDKCall(client, provider, threading.get_ident(), model_sources())
    token = _SDK_CALL.set(call)
    try:
        yield
    finally:
        call.active = False
        _SDK_CALL.reset(token)


@contextmanager
def sdk_send(client, body):
    """Guard an SDK resource used directly; an enclosing admitted call checks once.

    The live token belongs to this SDK client, recipient and synchronous thread.
    A copied context cannot retain permission after the enclosing call ends.
    """
    to = destination(llm.AnthropicCaller(client=client))  # recipient inspection; no model is called
    call = _SDK_CALL.get()
    counted = call is not None and call.active and call.thread == threading.get_ident()
    if counted and call.client is client and call.provider == to.provider:
        if not to.local and not set(model_sources()) <= set(call.sources):
            raise _refusal(llm.LLMError(f'{REQUIRED}: SDK request sources changed after admission; nothing was sent'))
        yield
        return
    try:
        payload = digest(body)
    except (TypeError, ValueError):
        payload = sha256(repr(body))
    with admission(to.provider, model_sources(), payload, local=to.local):
        if not counted:
            llm._start_model_call()
        yield


@dataclass(frozen=True)
class Destination:
    provider: str
    local: bool = False


def loopback(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
            return False
        if host == 'localhost':
            return True
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def urllib_destination(url):
    """Resolve the first hop with the same native handlers as _post_json.

    A body-free Request observes ProxyHandler's own set_proxy decision, including
    platform settings and NO_PROXY's host/port rules. No opener is run and no
    connection is made. A handler that needs an opaque re-dispatch stays unknown.
    """
    class Route(urllib.request.Request):
        hop = None

        def set_proxy(self, host, scheme):
            self.hop = f'{scheme}://{host}'.rstrip('/')
            super().set_proxy(host, scheme)

    try:
        request = Route(url)
        opener = urllib.request.build_opener(llm._NoRedirects)
        proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
        if len(proxies) > 1 or any(type(h) is not urllib.request.ProxyHandler for h in proxies):
            return Destination('cloud:unknown')
        if proxies:
            handler = getattr(proxies[0], request.type + '_open', None)
            # proxy_open may re-dispatch for a different proxy protocol. That
            # route is unsupported; do not let its parent open a connection.
            if handler is not None:
                class NoOpen:
                    def open(self, *args, **kwargs):
                        raise ValueError('opaque proxy re-dispatch')
                proxies[0].parent = NoOpen()
                request.timeout = 0
                handler(request)
        if request.hop is not None:
            parsed = urlsplit(request.hop)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                return Destination('cloud:unknown')
            return Destination('endpoint:' + sha256(request.hop))
        if loopback(url):
            return Destination('local:loopback', True)
        canonical = url.rstrip('/')
        own = {'https://api.openai.com/v1': 'openai:api', 'https://openrouter.ai/api/v1': 'openrouter:api'}
        return Destination(own.get(canonical, 'endpoint:' + sha256(canonical)))
    except (AttributeError, OSError, TypeError, ValueError):
        return Destination('cloud:unknown')


def wrapper_slot(caller: object) -> str | None:
    """G6 wrapper identities, never attributes supplied by an opaque wrapper.

    Higher-layer classes are looked up only in already loaded G6 modules. This
    avoids importing an application from core, and compares the class objects.
    """
    kind = caller_kind(caller)
    return kind.partition(':')[2] if kind.startswith('wrapper:') else None


_WATCHED_HOSTS = (('WatchedClaude', 'anthropic:claude_code'), ('WatchedCodex', 'openai:codex'))


def watched_host(caller: object) -> str:
    """The provider of a host the loop watches, or ``''``: ``usage.WatchedClaude`` or ``WatchedCodex``, or an instance the
    construction hook of ``g6.self_maintenance.usage`` made of a native host, whose class is a subclass of one of them.

    Its send methods are final there (``usage._check_seam``), and each launch of its program takes the sharing check
    itself: ``usage._complete_host`` (``host_admission``) or a session's launch step (``session_admission``). The classes
    are looked up in loaded modules only, as ``CALLER_KINDS`` is read.
    """
    usage = sys.modules.get('g6.self_maintenance.usage')
    watched = getattr(usage, '_Watched', None) if usage is not None else None
    if not isinstance(watched, type) or getattr(type(caller), 'complete', None) is not watched.complete:
        return ''                 # a completion replaced after the class was made is not the owned path
    for name, provider in _WATCHED_HOSTS:
        kind = getattr(usage, name, None)
        if isinstance(kind, type) and isinstance(caller, kind):
            return provider
    return ''


def host_admission(caller: object, system: str, user: str) -> dict:
    """The terms of the sharing check of one owned host launch (``usage._complete_host``), for
    ``cloud_consent.admission``: its recipient (a host no rule here recognises is ``cloud:unknown``, which no grant
    covers), the model inputs, and the request's digest."""
    to = destination(caller)
    return {'provider': to.provider, 'sources': model_sources(),
            'payload_sha256': digest({'system': system, 'user': user}), 'local': to.local}


def session_admission(caller: object, stdin: str) -> dict:
    """The terms of the sharing check of a host session's launch step (``ToolSession._launch``, or the owned
    ``usage.WatchedClaude._launch`` that replaces it): the sources are only those its session's admitted ``run`` declared
    (``request_sources``), and it sends only inside that run's admitted send: anywhere else its recipient is
    ``cloud:unknown``, which no grant covers."""
    to = destination(caller)
    provider = to.provider if to.local or admitted(to.provider) else 'cloud:unknown'
    return {'provider': provider, 'sources': (), 'payload_sha256': sha256(stdin), 'local': to.local}


def backend_supported(caller: object) -> bool:
    if isinstance(caller, CallerWrapper):
        return True
    methods = ('complete', 'complete_with_images', '_chat', '_create', '_command', '_get_client')
    if any(name in getattr(caller, '__dict__', {}) for name in methods):
        return False
    if watched_host(caller):
        return True
    for native in (llm.OllamaCaller, llm.OpenAICompatibleCaller, llm.AnthropicCaller, _CLAUDE, _CODEX):
        if isinstance(caller, native):
            for name in methods:
                method = getattr(type(caller), name, None)
                if isinstance(caller, _Consent) and method is getattr(_Consent, name, object()):
                    continue
                if method is not getattr(native, name, None):
                    return False
    return True


def destination(caller: object, _seen: frozenset[int] = frozenset()) -> Destination:
    if id(caller) in _seen:
        return Destination('cloud:unknown')
    if not backend_supported(caller):
        return Destination('cloud:unknown')
    if watched_host(caller):
        return Destination(watched_host(caller))
    slot = wrapper_slot(caller)
    if slot:
        inner = getattr(caller, slot)
        children = inner if slot == '_callers' else (inner,)
        destinations = [destination(c, _seen | {id(caller)}) for c in children]
        if destinations and all(d.local for d in destinations):
            return Destination('local:process', True)
        return destinations[0] if destinations and all(d == destinations[0] for d in destinations) else Destination('cloud:unknown')
    if isinstance(caller, _CLAUDE):
        return Destination('anthropic:claude_code')
    if isinstance(caller, _CODEX):
        return Destination('openai:codex')
    if caller_kind(caller) in ('process', 'no-send'):
        return Destination('local:process', True)
    if isinstance(caller, llm.OllamaCaller | llm.OpenAICompatibleCaller):
        return urllib_destination(caller.base_url)
    if isinstance(caller, llm.AnthropicCaller):
        client = caller._client
        if client is None:
            # Resolve the real lazily-built SDK before admission. Without an
            # explicit key, inspection must not trigger an ambient sign-in.
            if not caller._api_key:
                return Destination('cloud:unknown')
            client = caller._get_client()
        return sdk_destination(client)
    return Destination('cloud:unknown')


class _Consent:
    #: The caller a guarded form made by ``guard`` checks and sends through: the one ``guard`` was given, of the class
    #: it was constructed with. None where the object is its own backend (``ConsentClaude``, ``ConsentCodex``, a
    #: watched host). The recipient checked is always that of the object that sends.
    _guards = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if {'complete', 'complete_with_images'} & cls.__dict__.keys():
            raise llm.LLMError('G6_E_CLOUD_CALLER_UNSUPPORTED: guarded completion methods cannot be overridden')

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        backend = self if self._guards is None else self._guards
        to = destination(backend)
        with (admission(to.provider, model_sources(), digest({'system': system, 'user': user}), local=to.local),
              _sdk_call(getattr(backend, '_client', None), to.provider)):
            return (super() if backend is self else backend).complete(system, user, max_tokens=max_tokens)

    def complete_with_images(self, system, user, images, *, max_tokens=llm.DEFAULT_MAX_TOKENS):
        backend = self if self._guards is None else self._guards
        to = destination(backend)
        hashes = [sha256(image.data) for image in images]
        with (admission(to.provider, model_sources(), digest({'system': system, 'user': user, 'images': hashes}), local=to.local),
              _sdk_call(getattr(backend, '_client', None), to.provider)):
            return (super() if backend is self else backend).complete_with_images(system, user, images,
                                                                                  max_tokens=max_tokens)


class ConsentClaude(_Consent, _CLAUDE):
    """Claude Code is always a cloud recipient, including when its process is local."""


class ConsentCodex(_Consent, _CODEX):
    """Codex is always a cloud recipient."""


class _Unknown:
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def complete(self, system, user, *, max_tokens=llm.DEFAULT_MAX_TOKENS):
        with admission('cloud:unknown', current_sources(), digest({'system': system, 'user': user})):
            return self._inner.complete(system, user, max_tokens=max_tokens)

    def complete_with_images(self, system, user, images, *, max_tokens=llm.DEFAULT_MAX_TOKENS):
        with admission('cloud:unknown', current_sources(), digest({'system': system, 'user': user})):
            return self._inner.complete_with_images(system, user, images, max_tokens=max_tokens)


def guard(caller, _seen: frozenset[int] = frozenset()):
    """Preserve backend identity and retry/fallback behavior; guard each actual backend.

    The check belongs to the caller this returns: use that one. A native backend's
    guarded form is a caller of its own, made here with an additive subclass chosen
    as it is made (as WatchedClaude's is at construction). It holds the backend's
    own state, takes the check on the backend as it is at each send (its endpoint,
    its client), and then sends through it, so the caller that sends is the one
    given, with the class and the send methods it was constructed with. Bound code
    and global classes are never patched. Unknown implementations fail closed.
    """
    if id(caller) in _seen:
        return _Unknown(caller)
    if type(caller) is _Unknown:
        return caller
    if not backend_supported(caller):
        return _Unknown(caller)
    if watched_host(caller):
        return caller          # each launch takes the check itself; its class and send methods are final
    if isinstance(caller, _Consent):
        return caller if type(caller).complete is _Consent.complete else _Unknown(caller)
    slot = wrapper_slot(caller)
    if slot:
        inner = getattr(caller, slot)
        seen = _seen | {id(caller)}
        setattr(caller, slot, tuple(guard(c, seen) for c in inner) if slot == '_callers' else guard(inner, seen))
    elif caller_kind(caller) in ('process', 'no-send'):
        return caller
    elif isinstance(caller, llm.OllamaCaller | llm.OpenAICompatibleCaller | llm.AnthropicCaller
                    | _CLAUDE | _CODEX):
        if not backend_supported(caller):
            return _Unknown(caller)
        kind = type(caller)
        guarded = object.__new__(type('Consent' + kind.__name__, (_Consent, kind), {'_guards': caller}))
        guarded.__dict__ = caller.__dict__      # one state: what the backend is told later is what its guarded form reads
        return guarded
    else:
        return _Unknown(caller)
    return caller


def provider_from_env(*args, **kwargs):
    return guard(llm.provider_from_env(*args, **kwargs))
