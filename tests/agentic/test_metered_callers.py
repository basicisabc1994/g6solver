# SPDX-License-Identifier: Apache-2.0
"""Review 3, class A: every bound the question states is enforced where the cost is spent, or the override is refused
for that caller before the question. Nothing here leaves the test process: each HTTP request is answered by a fake at
the last layer G6's code reaches (urllib's opener, the Anthropic SDK's connection pool), and a socket that would connect
fails the test instead, loopback included."""
from __future__ import annotations

import asyncio
import email.message
import io
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from g6._agentic import kernel
from g6.core import llm
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child
from tests.agentic.test_mcp_agentic import FLOAT, call, rig  # noqa: F401 - rig: the MCP fixture, shared

GRID = json.dumps({"value": [0.5]})
LOCAL = "http://127.0.0.1:9"
#: The most a reply, or a revoke, may come after the budget's seconds while a provider stalls: the kernel's resend slack
#: (kernel.RESEND_SLACK_S) and the time to build the reply and poll the job.
MARGIN_S = 2.0
MESSAGE = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": "fixture-model",
           "content": [{"type": "text", "text": GRID}], "stop_reason": "end_turn", "stop_sequence": None,
           "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No proxy is read and no socket connects; G6's state is the test's own."""
    import socket

    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "*")

    connect = socket.socket.connect

    def refuse(self, *args, **kwargs):
        caller = sys._getframe(1)
        # the one exception: the standard library's own socketpair (asyncio's in-process wake-up pipe on Windows),
        # which joins two sockets of this process and reaches no service
        if caller.f_globals.get("__name__") == "socket" and "socketpair" in caller.f_code.co_name:
            return connect(self, *args, **kwargs)
        raise AssertionError("a test tried to open a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def _urllib(monkeypatch, plan: list, opened: list) -> llm.EmptyRetry:
    """An Ollama caller as G6 builds it, whose every request reaches ``OpenerDirector.open`` and is answered by the next
    step of ``plan``: ``("429", seconds)`` a rate-limit answer with ``Retry-After``, or the model's text."""
    def open_(self, request, data=None, timeout=None):
        opened.append({"at": time.monotonic(), "timeout": timeout})
        step = plan[min(len(opened) - 1, len(plan) - 1)]
        if isinstance(step, tuple):
            headers = email.message.Message()
            headers["Retry-After"] = str(step[1])
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", headers, io.BytesIO(b""))
        return io.BytesIO(json.dumps({"message": {"content": step}}).encode())

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", open_)
    return llm.EmptyRetry(llm.OllamaCaller("fixture-model", base_url=LOCAL))


def _sdk(monkeypatch, handle, base_url: str = LOCAL):
    """Anthropic's own SDK client (its defaults: two retries of its own), whose connection pool is ``handle``."""
    anthropic = pytest.importorskip("anthropic")
    client = anthropic.Anthropic(api_key="fixture-not-a-key", base_url=base_url)
    pool = type(client._client._transport._pool)        # the SDK's own HTTP stack (httpcore or its vendored httpcore2)
    core = sys.modules[pool.__module__.split(".")[0]]
    monkeypatch.setattr(pool, "handle_request", lambda self, request: handle(core, request))
    return client


def _answer(core, status: int = 200):
    if status == 429:
        return core.Response(429, headers=[(b"content-type", b"application/json"), (b"retry-after-ms", b"5")],
                             content=b'{"type":"error","error":{"type":"rate_limit_error","message":"wait"}}')
    return core.Response(200, headers=[(b"content-type", b"application/json")], content=json.dumps(MESSAGE).encode())


def _budget(calls: int = 3, seconds: float = 30.0) -> kernel.Budget:
    return kernel.Budget(max_model_calls=calls, max_output_chars=20_000, max_wall_clock_s=seconds)


def _run(caller, budget):
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller, budget=budget)
    assert isinstance(pending, kernel.Pending), pending
    return pending, kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Ada"))


# a provider's rate-limit resend (g6.core.llm's Retry-After path) passes the same meter as the first request

def test_a_rate_limit_resend_is_counted_before_it_is_sent_and_the_one_past_the_budget_is_not_sent(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda seconds: None)
    in_process_child(monkeypatch)
    opened: list[dict] = []
    caller = _urllib(monkeypatch, [("429", 0), ("429", 0), ("429", 0), GRID], opened)
    pending, reply = _run(caller, _budget(calls=1))
    assert "at most 1 model calls in all, retries included, each counted before it is sent" in pending.question
    assert len(opened) == 1, opened
    assert reply["agentic"]["reason"] == "budget_exhausted" and reply["agentic"]["cost"]["model_calls"] == 1


def test_a_resend_whose_wait_ends_after_the_override_stopped_waiting_is_never_sent(monkeypatch):
    in_process_child(monkeypatch)
    opened: list[dict] = []
    caller = _urllib(monkeypatch, [("429", 1.0), GRID], opened)
    _, reply = _run(caller, _budget(seconds=0.6))
    returned = time.monotonic()
    assert reply["agentic"]["reason"] == "budget_exhausted" and "stopped waiting" in reply["agentic"]["detail"]
    time.sleep(1.0)          # the abandoned call's wait ends
    assert [o for o in opened if o["at"] > returned] == [] and len(opened) == 1


def test_a_resend_that_would_carry_its_first_requests_timeout_past_the_deadline_is_not_sent(monkeypatch):
    in_process_child(monkeypatch)
    opened: list[dict] = []
    caller = _urllib(monkeypatch, [("429", kernel.RESEND_SLACK_S + 0.3), GRID], opened)
    _, reply = _run(caller, _budget(seconds=30.0))
    assert len(opened) == 1, opened
    assert reply["agentic"]["reason"] == "budget_exhausted" and "resend" in reply["agentic"]["detail"]
    assert all(0 < o["timeout"] <= 30.0 for o in opened)


# Anthropic's SDK: its own retries off, the time left as each request's timeout

def test_the_anthropic_sdk_sends_once_with_its_own_retries_off(monkeypatch):
    in_process_child(monkeypatch)
    sent: list = []

    def handle(core, request):
        sent.append(request.extensions.get("timeout"))
        return _answer(core, 429 if len(sent) < 3 else 200)

    client = _sdk(monkeypatch, handle)
    caller = llm.EmptyRetry(llm.AnthropicCaller("fixture-model", client=client, fallbacks=False))
    _, reply = _run(caller, _budget(calls=1))
    assert len(sent) == 1, sent
    assert reply["agentic"]["outcome"] == "degraded" and reply["agentic"]["reason"] == "model_failed"
    assert client.max_retries == 2               # the person's own client is not changed


def test_the_anthropic_sdk_is_given_the_time_left_as_each_requests_timeout(monkeypatch):
    in_process_child(monkeypatch)
    sent: list = []

    def handle(core, request):
        sent.append(request.extensions.get("timeout"))
        return _answer(core)

    caller = llm.EmptyRetry(llm.AnthropicCaller("fixture-model", client=_sdk(monkeypatch, handle), fallbacks=False))
    pending, reply = _run(caller, _budget(seconds=30.0))
    assert reply["agentic"]["outcome"] == "substituted", reply["agentic"].get("detail")
    assert len(sent) == 1 and all(0 < sent[0][phase] <= 30.0 for phase in ("connect", "read", "write", "pool"))
    assert "sent with the time left as its timeout and the SDK's own retries off" in pending.question


# callers G6 cannot bound per request are refused before the question

class _Custom:
    def complete(self, system, user, *, max_tokens=1000):
        raise AssertionError("never called")


def _sdk_shaped():
    from g6.core import consent_callers

    class SDK(consent_callers.SDKClient):
        base_url = "https://api.anthropic.com"
        messages = SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(AssertionError("never sent")))

    return consent_callers.guard(llm.AnthropicCaller(client=SDK(), fallbacks=False))


UNMETERED = {
    "a custom caller": lambda: _Custom(),
    "an SDK-shaped client that is not Anthropic's own": _sdk_shaped,
    "a fallback across two models": lambda: llm.FallbackCaller([llm.OllamaCaller("a", base_url=LOCAL),
                                                                llm.OllamaCaller("b", base_url=LOCAL)]),
}


@pytest.mark.parametrize("kind", sorted(UNMETERED))
def test_a_caller_g6_cannot_bound_per_request_is_refused_before_the_question(monkeypatch, kind):
    started = no_child(monkeypatch)
    reply = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=UNMETERED[kind](), budget=_budget())
    assert not isinstance(reply, kernel.Pending)
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and reply["warning_card"]["code"] == kernel.CALLER_UNMETERED
    assert reply["original"]["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED" and started == []


def test_a_host_client_is_allowed_and_the_question_says_one_call_is_one_session(monkeypatch):
    from g6.core import host_models

    no_child(monkeypatch)
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                             llm=llm.EmptyRetry(host_models.ClaudeCodeCaller()), budget=_budget())
    assert isinstance(pending, kernel.Pending), pending
    assert "one model call is one session of Claude Code, which may make several requests to its model" in pending.question
    assert "ended, with every process it started, at the deadline" in pending.question
    in_process_child(monkeypatch)       # the program is missing here (G6_CLAUDE names none), so nothing starts
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Ada"))
    assert reply["agentic"]["reason"] == "model_failed", reply["agentic"].get("detail")


def test_the_question_states_each_kinds_own_bound(monkeypatch):
    no_child(monkeypatch)
    local = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=llm.FakeLLM()).question
    endpoint = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=_urllib(monkeypatch, [], [])).question
    for question in (local, endpoint):
        assert "retries included, each counted before it is sent" in question
        assert "a model reply that arrives after the deadline is not used" in question
    assert "each request to the model is sent with the time left as its timeout" in endpoint
    assert f"within {kernel.RESEND_SLACK_S:g} second of its call's first request" in endpoint
    assert "runs on this machine and is sent nothing over the network" in local
    assert "time left as its timeout" not in local


# a provider that stalls holds neither the person's reply nor their revoke past the deadline

def _stalling(monkeypatch, entered: list, release: threading.Event):
    """Anthropic's own SDK client for Anthropic's address, whose pool behaves as a stalled provider does: no byte
    arrives, and the request ends when its read timeout has passed (``ReadTimeout``), not before."""
    def handle(core, request):
        entered.append(time.monotonic())
        release.wait(request.extensions["timeout"]["read"])
        raise core.ReadTimeout("fixture: no byte arrived within the read timeout")

    from g6.core import consent_callers

    client = _sdk(monkeypatch, handle, base_url="https://api.anthropic.com")
    return consent_callers.guard(llm.AnthropicCaller("fixture-model", client=client, fallbacks=False))


def _grant(arguments: dict) -> None:
    from g6.mcp.cloud_consent import request_grant
    from tests.conftest import accepted_cloud_name, cloud_argument_sources

    class Accepting:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action="accept", content={"value": accepted_cloud_name(kwargs["message"])})

    async def grant():
        context = SimpleNamespace(session=Accepting(), request_id="fixture-dialog")
        for source in ("model_inputs", "component_data", *cloud_argument_sources(arguments)):
            assert (await request_grant(context, source, "anthropic:api"))["status"] == "granted"

    asyncio.run(grant())


def _revoke_during(entered: list, revoked: list) -> threading.Thread:
    """A revoke of another source, made while the stalled request is out; when it returned."""
    from g6.core import cloud_consent

    def revoke():
        for _ in range(200):
            if entered:
                break
            time.sleep(0.01)
        time.sleep(0.2)
        cloud_consent.revoke("fixture-unrelated", "anthropic:api")
        revoked.append(time.monotonic())

    thread = threading.Thread(target=revoke, daemon=True)
    thread.start()
    return thread


BUDGET_S = 1.5


def test_a_stalled_provider_holds_neither_the_reply_nor_a_revoke_past_the_deadline_from_python(monkeypatch):
    in_process_child(monkeypatch)
    entered, release, revoked = [], threading.Event(), []
    _grant({"component": "cegis", "op": "synthesize", "params_json": json.dumps(FLOAT_PARAMS)})
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW,
                             llm=_stalling(monkeypatch, entered, release), budget=_budget(seconds=BUDGET_S))
    started = time.monotonic()
    watcher = _revoke_during(entered, revoked)
    try:
        reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Ada"))
        took = time.monotonic() - started
        watcher.join(BUDGET_S + MARGIN_S)
    finally:
        release.set()
    assert entered, reply["agentic"].get("detail")            # the request was out: the provider stalled on it
    assert reply["agentic"]["outcome"] == "degraded" and reply["agentic"]["reason"] in ("budget_exhausted",
                                                                                         "model_failed")
    assert took < BUDGET_S + MARGIN_S, took
    assert revoked and revoked[0] - started < BUDGET_S + MARGIN_S, revoked


@pytest.mark.usefixtures("declared_mcp_grants")
def test_a_stalled_provider_holds_neither_the_reply_nor_a_revoke_past_the_deadline_over_mcp(monkeypatch, request):
    mcp = request.getfixturevalue("rig")
    entered, release, revoked = [], threading.Event(), []
    cloud = _stalling(monkeypatch, entered, release)
    monkeypatch.setattr(kernel, "person_caller", lambda: cloud)
    monkeypatch.setattr(kernel, "DEFAULT_BUDGET", _budget(seconds=BUDGET_S))
    _grant(FLOAT)
    started = time.monotonic()
    watcher = _revoke_during(entered, revoked)
    try:
        reply = call(mcp, FLOAT)
        took = time.monotonic() - started
        watcher.join(BUDGET_S + MARGIN_S)
    finally:
        release.set()
    assert entered, reply
    assert reply["agentic"]["outcome"] == "degraded" and reply["agentic"]["reason"] in ("budget_exhausted",
                                                                                         "model_failed")
    assert took < BUDGET_S + MARGIN_S, took
    assert revoked and revoked[0] - started < BUDGET_S + MARGIN_S, revoked


@pytest.mark.usefixtures("declared_mcp_grants")
def test_over_mcp_a_caller_g6_cannot_bound_is_refused_and_nobody_is_asked(monkeypatch, request):
    mcp = request.getfixturevalue("rig")
    cloud = _sdk_shaped()
    monkeypatch.setattr(kernel, "person_caller", lambda: cloud)
    reply = call(mcp, FLOAT)
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and mcp.asked == []
    assert "not Anthropic's own SDK client" in reply["error"]

