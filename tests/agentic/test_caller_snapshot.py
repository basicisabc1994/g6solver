# SPDX-License-Identifier: Apache-2.0
"""The kernel sends through its own caller; supplied objects are input, never the send path."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from g6._agentic import kernel
from g6.core import consent_callers, host_models, llm
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, in_process_child, no_child


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect = socket.socket.connect

    def refuse(self, address, *args, **kwargs):
        frame = sys._getframe(1)
        if frame.f_globals.get("__name__") == "socket" and "socketpair" in frame.f_code.co_name:
            return connect(self, address, *args, **kwargs)
        raise AssertionError("no socket connect is allowed")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def prepare(caller, **kwargs):
    request_id = "snapshot-fixture"
    return kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller, request_id=request_id, **kwargs)


def finish(pending):
    return kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Fixture person"))


class TwoFaced(str):
    def __str__(self):
        raise AssertionError("custom str called")

    def __format__(self, spec):
        return "http://203.0.113.7:9"


def test_endpoint_uses_plain_snapshot_and_never_reads_original_again(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    sent = []

    def post(url, body, headers, timeout):
        sent.append((url, body["model"], timeout, headers))
        return {"message": {"content": '{"value":[0.5]}'}}

    monkeypatch.setattr(llm, "_post_json", post)
    caller = llm.OllamaCaller("fixture-model", base_url="http://127.0.0.1:9", timeout=30)
    caller.model = TwoFaced("fixture-model")
    caller.base_url = TwoFaced("http://127.0.0.1:9")
    pending = prepare(caller)
    assert isinstance(pending, kernel.Pending), pending
    caller.model, caller.base_url, caller.timeout = "changed", "http://203.0.113.7:9", 0
    caller.complete = lambda *a, **k: pytest.fail("supplied caller was sent through")
    reply = finish(pending)
    assert reply["agentic"]["outcome"] == "substituted", reply["agentic"]
    assert len(sent) == 1 and sent[0][:2] == ("http://127.0.0.1:9/api/chat", "fixture-model")
    assert type(sent[0][1]) is str and 0 < sent[0][2] <= 120


def test_own_function_is_named_honestly_and_original_caller_is_not_called(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    answers = []

    def answer(system, user):
        answers.append(user)
        return '{"value":[0.5]}'

    caller = llm.FakeLLM(answer)
    pending = prepare(caller)
    assert isinstance(pending, kernel.Pending)
    assert "own Python function" in pending.question and "cannot say where" in pending.question
    assert "sent nothing over the network" not in pending.question
    caller._responder = lambda *args: pytest.fail("changed function used")
    reply = finish(pending)
    assert reply["agentic"]["outcome"] == "substituted" and len(answers) == 1
    assert caller.calls == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_configuration_cannot_supply_own_function(monkeypatch, wrapped):
    no_child(monkeypatch)
    caller = llm.FakeLLM(lambda *args: pytest.fail("function ran from configuration"))
    configured = llm.EmptyRetry(caller) if wrapped else caller
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: configured)
    reply = prepare(None)
    assert isinstance(reply, dict) and reply["agentic"]["code"] == kernel.CALLER_UNMETERED


def test_mcp_cannot_ask_about_a_configured_own_function(monkeypatch):
    from g6.mcp.agentic import register

    no_child(monkeypatch)
    asked = []
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agentic.run,cegis.synthesize")
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: llm.FakeLLM(lambda *a: "unused"))

    class Server:
        def tool(self):
            def save(tool):
                self.call_tool = tool
            return save

    class Context:
        async def elicit(self, *args, **kwargs):
            asked.append(args)
            pytest.fail("MCP asked about own_function")

    server = Server()
    register(server, Context, SimpleNamespace(start=lambda fn: pytest.fail("job started")))
    reply = asyncio.run(server.call_tool("cegis", "synthesize", Context(), params_json=json.dumps(FLOAT_PARAMS)))
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and asked == []


@pytest.mark.parametrize("field", ["model", "base_url", "timeout"])
def test_unreadable_snapshot_is_input_envelope_without_private_text(monkeypatch, field):
    no_child(monkeypatch)

    class Unreadable:
        def __float__(self):
            raise RuntimeError("private timeout")

    caller = llm.OllamaCaller("m", base_url="http://127.0.0.1:9")
    setattr(caller, field, Unreadable())
    reply = prepare(caller)
    assert isinstance(reply, dict) and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "private" not in json.dumps(reply)


def test_question_build_failure_is_input_envelope(monkeypatch):
    import g6.mcp.consent as consent

    no_child(monkeypatch)
    monkeypatch.setattr(consent, "question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private question")))
    reply = prepare(llm.FakeLLM())
    assert isinstance(reply, dict) and reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "private" not in json.dumps(reply)


def test_envelope_build_failure_is_input_envelope(monkeypatch):
    no_child(monkeypatch)
    pending = prepare(llm.FakeLLM())
    assert isinstance(pending, kernel.Pending)
    monkeypatch.setattr(kernel, "_Envelope", lambda *a: (_ for _ in ()).throw(ValueError("private build")))
    reply = finish(pending)
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "private" not in json.dumps(reply)
    assert reply["original"]["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"


@pytest.mark.parametrize("provider,program", [("claude_code", "Claude Code"), ("codex", "Codex")])
def test_host_program_comes_from_exact_class_and_timeout_is_frozen(monkeypatch, provider, program):
    from g6.self_maintenance import usage

    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    seen = []

    def complete(self, system, user, **kwargs):
        seen.append((self, self.program.name, self.model, self.timeout))
        llm._start_model_call()
        return '{"value":[0.5]}'

    monkeypatch.setattr(usage, "_complete_host", complete)
    caller = host_models.CALLERS[provider]("fixture-model", timeout=30)
    caller.program = None
    pending = prepare(caller)
    assert isinstance(pending, kernel.Pending), pending
    assert f"one session of {program}" in pending.question
    caller.timeout, caller.model = 0, "changed"
    reply = finish(pending)
    assert reply["agentic"]["outcome"] == "substituted", reply["agentic"]
    assert len(seen) == 1 and seen[0][0] is not caller and type(seen[0][0]) is type(caller)
    assert seen[0][1:3] == (program, "fixture-model") and seen[0][3] > 0


def test_wrapper_hooks_and_instance_usage_clock_are_dropped(monkeypatch):
    in_process_child(monkeypatch)
    monkeypatch.setattr(kernel.ledger, "append", lambda entry: "fixture-ledger")
    caller = llm.FakeLLM(lambda s, u: '{"value":[0.5]}')
    caller.usage = caller.clock = lambda *a: pytest.fail("instance hook ran")
    wrapper = llm.TimingLLMCaller(caller, lambda *a: pytest.fail("latency hook ran"))
    pending = prepare(wrapper)
    assert isinstance(pending, kernel.Pending)
    assert finish(pending)["agentic"]["outcome"] == "substituted"
    assert caller.calls == []


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost.", "fixture.invalid"])
def test_endpoint_hostnames_are_refused_before_question(monkeypatch, host):
    started = no_child(monkeypatch)
    reply = prepare(llm.OllamaCaller("m", base_url=f"http://{host}:9"))
    assert isinstance(reply, dict) and reply["agentic"]["code"] == kernel.CALLER_UNMETERED
    assert "127.0.0.1" in reply["error"] and started == []


@pytest.mark.parametrize("wrapped", [False, True])
def test_no_key_is_no_model_before_question(monkeypatch, wrapped):
    no_child(monkeypatch)
    caller = llm._NoKey("FIXTURE_KEY", "fixture-provider", "fixture-model")
    reply = prepare(llm.EmptyRetry(caller) if wrapped else caller)
    assert isinstance(reply, dict) and reply["agentic"]["code"] == kernel.NO_MODEL


def test_override_rechecks_proxy_for_snapshot_route(monkeypatch):
    started = no_child(monkeypatch)
    monkeypatch.setenv("no_proxy", "*")
    pending = prepare(llm.OllamaCaller("m", base_url="http://127.0.0.1:9"))
    assert isinstance(pending, kernel.Pending)
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.7:9")
    monkeypatch.delenv("no_proxy")
    reply = finish(pending)
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and started == []


def test_foreign_caller_cannot_replace_the_snapshot(monkeypatch):
    no_child(monkeypatch)
    pending = prepare(llm.FakeLLM())
    pending = dataclasses.replace(pending, caller=object())
    assert finish(pending)["agentic"]["code"] == kernel.CALLER_UNMETERED


@pytest.mark.parametrize("native", [host_models.ClaudeCodeCaller, host_models.CodexCaller])
def test_legacy_native_without_owned_launch_path_is_refused(monkeypatch, native):
    """Simulate an object constructed before usage installed its owned construction wrapper."""
    no_child(monkeypatch)
    caller = object.__new__(native)
    native.__init__(caller)
    reply = prepare(caller)
    assert isinstance(reply, dict) and reply["agentic"]["code"] == kernel.CALLER_UNMETERED
    assert "construct a new caller" in reply["error"]
