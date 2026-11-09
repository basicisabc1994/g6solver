# SPDX-License-Identifier: Apache-2.0
"""Additive wrappers around the certificate-bound provider implementations."""
from __future__ import annotations

import ipaddress
import os
import sys
import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

from g6.core import host_models, llm
from g6.core.cloud_consent import admission, current_sources, sha256
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
    'g6.self_maintenance.usage.WatchedClaude': 'cloud-host',
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

    An implementation names its actual configured ``base_url`` and sends only
    there. Tests use this identity for synthetic provider endpoints. A foreign
    object with a similarly named attribute does not implement this contract.
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
    if isinstance(client, SDKClient) or isinstance(native, type) and isinstance(client, native):
        return getattr(client, 'base_url', None)
    return None


@dataclass
class _SDKCall:
    client: object
    provider: str
    thread: int
    active: bool = True


_SDK_CALL = contextvars.ContextVar('g6_admitted_sdk_call', default=None)


@contextmanager
def _sdk_call(client, provider):
    if client is None:
        yield
        return
    call = _SDKCall(client, provider, threading.get_ident())
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


def wrapper_slot(caller: object) -> str | None:
    """G6 wrapper identities, never attributes supplied by an opaque wrapper.

    Higher-layer classes are looked up only in already loaded G6 modules. This
    avoids importing an application from core, and compares the class objects.
    """
    kind = caller_kind(caller)
    return kind.partition(':')[2] if kind.startswith('wrapper:') else None


def backend_supported(caller: object) -> bool:
    if isinstance(caller, CallerWrapper):
        return True
    methods = ('complete', 'complete_with_images', '_chat', '_create', '_command', '_get_client')
    if any(name in getattr(caller, '__dict__', {}) for name in methods):
        return False
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
        url = caller.base_url
        parsed = urlsplit(url)
        proxies = getproxies()
        proxied = parsed.scheme in proxies and not proxy_bypass(parsed.hostname or '')
        if loopback(url):
            if proxied or getattr(caller, 'proxy', None):
                return Destination('cloud:unknown')
            return Destination('local:loopback', True)
        canonical = url.rstrip('/')
        own = {'https://api.openai.com/v1': 'openai:api', 'https://openrouter.ai/api/v1': 'openrouter:api'}
        return Destination(own.get(canonical, 'endpoint:' + sha256(canonical)))
    if isinstance(caller, llm.AnthropicCaller):
        client = caller._client
        url = sdk_endpoint(client) if client is not None else (
            caller._base_url or os.environ.get('ANTHROPIC_BASE_URL') or 'https://api.anthropic.com')
        if not url:
            return Destination('cloud:unknown')
        url = str(url).rstrip('/')
        if loopback(url):
            parsed = urlsplit(url)
            if parsed.scheme in getproxies() and not proxy_bypass(parsed.hostname or ''):
                return Destination('cloud:unknown')
            return Destination('local:loopback', True)
        return Destination('anthropic:api' if url == 'https://api.anthropic.com' else 'endpoint:' + sha256(url))
    return Destination('cloud:unknown')


class _Consent:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if {'complete', 'complete_with_images'} & cls.__dict__.keys():
            raise llm.LLMError('G6_E_CLOUD_CALLER_UNSUPPORTED: guarded completion methods cannot be overridden')

    def complete(self, system: str, user: str, *, max_tokens: int = llm.DEFAULT_MAX_TOKENS) -> str:
        to = destination(self)
        with (admission(to.provider, model_sources(), digest({'system': system, 'user': user}), local=to.local),
              _sdk_call(getattr(self, '_client', None), to.provider)):
            return super().complete(system, user, max_tokens=max_tokens)

    def complete_with_images(self, system, user, images, *, max_tokens=llm.DEFAULT_MAX_TOKENS):
        to = destination(self)
        hashes = [sha256(image.data) for image in images]
        with (admission(to.provider, model_sources(), digest({'system': system, 'user': user, 'images': hashes}), local=to.local),
              _sdk_call(getattr(self, '_client', None), to.provider)):
            return super().complete_with_images(system, user, images, max_tokens=max_tokens)


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

    As with WatchedClaude, an instance gets an additive subclass. Bound code and
    global classes are never patched. Unknown implementations fail closed.
    """
    if id(caller) in _seen:
        return _Unknown(caller)
    if type(caller) is _Unknown:
        return caller
    if not backend_supported(caller):
        return _Unknown(caller)
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
        caller.__class__ = type('Consent' + kind.__name__, (_Consent, kind), {})
    else:
        return _Unknown(caller)
    return caller


def provider_from_env(*args, **kwargs):
    return guard(llm.provider_from_env(*args, **kwargs))
