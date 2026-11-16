# SPDX-License-Identifier: Apache-2.0
"""The v1 transport ruling: refuse before asking, and recheck before spending."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from types import SimpleNamespace

import pytest

from g6._agentic import kernel
from g6.core import consent_callers, host_models, llm
from tests.agentic.kit import ALLOW, FLOAT_PARAMS, Model, in_process_child, no_child
from tests.agentic.test_mcp_agentic import FLOAT, call, rig  # noqa: F401


class Subclass(llm.OllamaCaller):
    pass


class Wrapper(consent_callers.CallerWrapper):
    pass


REFUSED = {
    "cloud-openai": lambda: llm.OpenAICompatibleCaller("m", base_url="https://models.fixture.invalid/v1", api_key=None),
    "cloud-ollama": lambda: llm.OllamaCaller("m", base_url="https://models.fixture.invalid"),
    "sdk-local": lambda: llm.AnthropicCaller("m", base_url="http://127.0.0.1:9", fallbacks=False),
    "sdk-cloud": lambda: llm.AnthropicCaller("m", fallbacks=False),
    "subclass": lambda: Subclass("m", base_url="http://127.0.0.1:9"),
    "wrapper": lambda: Wrapper(llm.OllamaCaller("m", base_url="http://127.0.0.1:9")),
}


@pytest.mark.parametrize("kind", REFUSED)
def test_uncontrolled_callers_refused_before_question(monkeypatch, kind):
    started = no_child(monkeypatch)
    reply = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=REFUSED[kind]())
    assert isinstance(reply, dict), kind
    assert reply["agentic"]["code"] == "G6_E_AGENTIC_CALLER_UNMETERED"
    assert reply["original"]["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"
    assert "model it can end at the deadline" in reply["error"]
    assert started == []


@pytest.mark.parametrize("replacement", ["cloud", "subclass", "wrapper"])
def test_override_rechecks_caller_after_prepare(monkeypatch, replacement):
    started = no_child(monkeypatch)
    caller = llm.OllamaCaller("m", base_url="http://127.0.0.1:9")
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller)
    assert isinstance(pending, kernel.Pending)
    if replacement == "cloud":
        caller.base_url = "https://models.fixture.invalid"
    else:
        pending = dataclasses.replace(pending, caller=REFUSED[replacement]())
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Ada"))
    if replacement == "cloud":
        # Repair ruling: the original object's mutation cannot change G6's prepared snapshot.
        assert reply["agentic"]["outcome"] == "degraded" and len(started) == 1
        return
    assert reply["agentic"]["code"] == kernel.DIGEST_MISMATCH  # a copied Pending has no issued seal
    assert started == []


def test_a_builtin_instance_with_custom_completion_is_refused(monkeypatch):
    started = no_child(monkeypatch)
    caller = llm.FakeLLM()
    monkeypatch.setattr(caller, "complete", lambda *args, **kwargs: pytest.fail("custom completion was called"))
    reply = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller)
    assert isinstance(reply, dict)
    assert reply["agentic"]["code"] == "G6_E_AGENTIC_CALLER_UNMETERED" and started == []


def test_configured_loopback_keeps_the_guarded_factory_and_exact_issued_identity(monkeypatch):
    no_child(monkeypatch)
    made = consent_callers.guard(llm.EmptyRetry(llm.OllamaCaller("m", base_url="http://127.0.0.1:9")))
    monkeypatch.setattr(consent_callers, "provider_from_env", lambda: made)
    caller = kernel.person_caller()
    assert caller is made
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller)
    assert isinstance(pending, kernel.Pending), pending
    # A different dynamically generated guard, even of the same backend class, has no issued identity here.
    foreign = consent_callers.guard(llm.OllamaCaller("m", base_url="http://127.0.0.1:9"))
    reply = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=foreign)
    assert reply["agentic"]["code"] == "G6_E_AGENTIC_CALLER_UNMETERED"


class BadParams(dict):
    def items(self):
        raise LookupError("private parameter text")


class BadReason:
    def __str__(self):
        raise LookupError("private reason text")


@pytest.mark.parametrize("field", ["params", "reason"])
def test_unreadable_input_is_an_envelope(monkeypatch, field):
    started = no_child(monkeypatch)
    kwargs = {"params": FLOAT_PARAMS, "reason": ""}
    kwargs[field] = BadParams(FLOAT_PARAMS) if field == "params" else BadReason()
    reply = kernel.prepare("cegis", "synthesize", allow=ALLOW, llm=Model([0.5]).llm, **kwargs)
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    assert "private" not in json.dumps(reply)
    assert started == []


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_exact_native_and_watched_host_classes_allow_only_owned_instances(monkeypatch, provider):
    from g6.self_maintenance import usage

    no_child(monkeypatch)
    native = host_models.CALLERS[provider]
    callers = [native(), usage.WatchedClaude() if provider == "claude_code" else usage.WatchedCodex()]
    for caller in callers:
        pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller)
        assert isinstance(pending, kernel.Pending), pending
        assert "at its own launch, after any consent-lock wait" in pending.question

    class Custom(native):
        pass

    refused = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=Custom())
    assert refused["agentic"]["code"] == "G6_E_AGENTIC_CALLER_UNMETERED"


@pytest.mark.parametrize("url", ["http://127.0.0.1:9", "http://[::1]:9", "http://localhost:9"])
def test_loopback_question_discloses_per_operation_timeout(monkeypatch, url):
    no_child(monkeypatch)
    caller = llm.OllamaCaller("m", base_url=url)
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller)
    if "localhost" in url:
        # Repair ruling F5: names, including localhost, establish no loopback IP literal.
        assert isinstance(pending, dict) and pending["agentic"]["code"] == kernel.CALLER_UNMETERED
        assert "127.0.0.1" in pending["error"]
        return
    assert isinstance(pending, kernel.Pending), pending
    assert "timeout is per operation, not a total network deadline" in pending.question
    assert "SDK" not in pending.question


def test_host_launcher_window_cannot_restart_the_override_deadline():
    """The bound launcher opens its own relative window after preparing streams; it must inherit the absolute cut."""
    from g6.core import component_process
    from g6.self_maintenance.usage import host_deadline

    with host_deadline(time.monotonic() + 1.0), component_process.window(600):
        assert 0 < component_process.checkpoint() <= 1.0


def test_host_deadline_expired_before_launch_refuses_and_does_not_leak():
    from g6.self_maintenance.usage import _launch_seconds, host_deadline

    with host_deadline(time.monotonic() - 1.0), pytest.raises(llm.LLMError, match="nothing was started"):
        _launch_seconds(600)
    assert _launch_seconds(600) == 600


@pytest.mark.usefixtures("declared_mcp_grants")
def test_cloud_refused_over_mcp_without_override_dialog(monkeypatch, request):
    mcp = request.getfixturevalue("rig")
    monkeypatch.setattr(kernel, "person_caller", REFUSED["cloud-openai"])
    reply = call(mcp, FLOAT)
    assert reply["agentic"]["code"] == "G6_E_AGENTIC_CALLER_UNMETERED"
    assert mcp.asked == []


@pytest.mark.parametrize("provider", ["claude_code", "codex"])
def test_host_launch_reads_deadline_after_preparation(monkeypatch, provider):
    """A slow command hook must spend the same deadline as admission and the session."""
    import g6.self_maintenance.usage as usage

    in_process_child(monkeypatch)
    launched = []
    native = host_models.CALLERS[provider]
    command = native._command

    def slow_command(self, *args):
        time.sleep(0.2)
        return command(self, *args)

    def run(argv, stdin, cwd, files, env, timeout, name):
        launched.append((time.monotonic(), timeout))
        raise llm.LLMError("G6_E_LLM_TIMEOUT: fixture")

    monkeypatch.setattr(native, "_command", slow_command)
    monkeypatch.setattr(host_models._HostCaller, "locate", lambda self: "fixture-host")
    monkeypatch.setattr(host_models, "_run", run)
    recipient = "anthropic:claude_code" if provider == "claude_code" else "openai:codex"
    _grant_host(recipient)
    caller = native()
    assert isinstance(caller, usage._Watched)
    pending = kernel.prepare("cegis", "synthesize", FLOAT_PARAMS, allow=ALLOW, llm=caller,
                             budget=kernel.Budget(max_wall_clock_s=3.0))
    assert isinstance(pending, kernel.Pending), pending
    started = time.monotonic()
    reply = kernel.override(pending, confirmation=kernel.Confirmation(pending.digest, "Ada"))
    assert reply["agentic"]["reason"] == "model_failed"
    assert launched and launched[0][1] <= 3.0 - (launched[0][0] - started) + 0.05


def _grant_host(provider):
    from g6.mcp.cloud_consent import request_grant
    from tests.conftest import accepted_cloud_name, cloud_argument_sources

    class Accepting:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action="accept", content={"value": accepted_cloud_name(kwargs["message"])})

    async def grant():
        context = SimpleNamespace(session=Accepting(), request_id="fixture-dialog")
        for source in ("model_inputs", "component_data", *cloud_argument_sources(FLOAT)):
            result = await request_grant(context, source, provider)
            assert result["status"] == "granted"

    asyncio.run(grant())
