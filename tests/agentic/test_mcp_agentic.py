# SPDX-License-Identifier: Apache-2.0
"""MCP overrides keep sensitive allowances and provider consent, original-first execution and confined jobs.
The owner's 2026-11-17 ruling removes the separate override dialog and typed name.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

fastmcp = pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6._agentic import kernel  # noqa: E402
from g6.invariants import check_envelope  # noqa: E402
from g6.mcp import build_server  # noqa: E402
from tests.agentic.kit import (  # noqa: E402
    FLOAT_PARAMS, INT_PARAMS, Model, cegis_in_process, configured_model, in_process_child)

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")


def data(reply) -> dict:
    return reply.data if isinstance(reply.data, dict) else reply.structured_content


async def finished(client, job: dict) -> dict:
    if "job_id" not in job:
        return job
    for _ in range(3000):
        got = data(await client.call_tool("metaharness_job", {"job_id": job["job_id"]}))
        if got.get("status") == "completed":
            return got["result"]
        if got.get("status") == "failed":
            return got
        await asyncio.sleep(0.02)
    raise AssertionError("the job did not finish")


async def named(message, response_type, params, context):
    return response_type(value="Ada")


async def declined(message, response_type, params, context):
    return ElicitResult(action="decline")


async def cancelled(message, response_type, params, context):
    return ElicitResult(action="cancel")


async def nameless(message, response_type, params, context):
    return response_type(value="   ")


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agentic.run,cegis.synthesize")
    model = Model([0.25, 0.5, 1.0])
    configured_model(monkeypatch, model)
    found = SimpleNamespace(model=model, served=in_process_child(monkeypatch), asked=[], server=build_server())

    def client(handler=named):
        async def seen(message, response_type, params, context):
            found.asked.append(message)
            return await handler(message, response_type, params, context)

        return Client(found.server, elicitation_handler=seen) if handler else Client(found.server)

    found.client = client
    return found


def call(rig, arguments: dict, handler=named) -> dict:
    async def go():
        async with rig.client(handler) as client:
            return await finished(client, data(await client.call_tool("g6_agentic_run", arguments)))
    return asyncio.run(go())


FLOAT = {"component": "cegis", "op": "synthesize", "params_json": json.dumps(FLOAT_PARAMS)}


def test_the_tool_is_listed_with_its_arguments(rig):
    async def go():
        async with rig.client() as client:
            return {t.name: t for t in await client.list_tools()}
    tools = asyncio.run(go())
    assert set(tools["g6_agentic_run"].inputSchema["properties"]) == {
        "component", "op", "params_json", "part", "mode", "reason"}


def test_without_agentic_run_allowed_nothing_runs_and_nobody_is_asked(rig, monkeypatch):
    monkeypatch.setenv("G6_MCP_SENSITIVE", "cegis.synthesize")
    reply = call(rig, FLOAT)
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and "agentic.run" in reply["error"]
    assert rig.asked == [] and rig.served == [] and rig.model.prompts == []


def test_the_ops_own_allowance_is_needed_too(rig, monkeypatch):
    monkeypatch.setenv("G6_MCP_SENSITIVE", "agentic.run")
    reply = call(rig, FLOAT)
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and "cegis.synthesize" in reply["error"]
    assert rig.asked == [] and rig.served == [] and rig.model.prompts == []


def test_with_no_trigger_the_original_answer_comes_back_and_nobody_is_asked(rig, monkeypatch):
    cegis_in_process(monkeypatch)
    reply = call(rig, {"component": "cegis", "op": "synthesize", "params_json": json.dumps(INT_PARAMS)})
    assert reply["ok"] is True and reply["result"]["program"] == "def f(x):\n    return x * 3 + 1\n"
    assert "agentic" not in reply and rig.asked == [] and rig.served == [] and rig.model.prompts == []


def test_code_mode_is_refused_before_anyone_is_asked(rig):
    reply = call(rig, {**FLOAT, "mode": "code"})
    assert reply["warning_card"]["code"] == "G6_E_AGENTIC_MODE_UNAVAILABLE" and rig.asked == []


@pytest.mark.parametrize(("handler", "status"), [(declined, "declined"), (cancelled, "cancelled"),
                                                  (None, "unavailable"), (nameless, "invalid")])
def test_a_decline_cancel_missing_dialog_or_empty_name_leaves_the_original_answer(rig, handler, status):
    reply = call(rig, {**FLOAT, "reason": "the table halves x"}, handler)
    assert reply["warning_card"]["code"] == "G6_E_AGENTIC_SUBSTITUTE" and reply["ok"] is True
    assert reply["agentic"]["outcome"] == "substituted" and reply["agentic"]["rechecked"] is True
    assert check_envelope(reply) == [] and "result" in reply
    assert reply["original"]["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"
    assert rig.served and rig.model.prompts and rig.asked == []


def test_params_json_nested_past_the_depth_is_invalid_input_and_nobody_is_asked(rig):
    reply = call(rig, {**FLOAT, "params_json": '{"sketch": ' + "[" * 3000 + "]" * 3000 + "}"})
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "nest" in reply["error"]
    assert rig.asked == [] and rig.served == [] and rig.model.prompts == []


def test_a_deeply_nested_model_reply_leaves_the_original_answer_standing(rig, monkeypatch):
    deep = Model(raw='{"value": ' + "[" * 3000 + "]" * 3000 + "}")
    configured_model(monkeypatch, deep)
    reply = call(rig, FLOAT)
    assert reply.get("status") != "failed" and reply["agentic"]["outcome"] == "degraded"
    assert reply["degradation_reason"].startswith("agentic_value_invalid") and "result" not in reply
    assert reply["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED" and check_envelope(reply) == []


def test_an_accepted_override_runs_as_a_job_and_records_the_question_digest_and_the_name(rig):
    reply = call(rig, {**FLOAT, "reason": "the table halves x"})
    assert rig.asked == []
    assert reply["ok"] is True and reply["completion_state"] == "qualified-draft" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] == "G6_E_AGENTIC_SUBSTITUTE"
    assert reply["result"]["program"] == "def f(x):\n    return x * 0.5\n"
    assert reply["original"]["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"
    record = reply["agentic"]
    assert len(record["digests"]["question"]) == 64 and record["outcome"] == "substituted"
    assert record["model"] in reply["warning_card"]["message"]
    assert record["person"] == {"answer": "provider consent", "provider": "local:loopback", "name": "",
                                "authenticated": False}
    assert record["rechecked"] is True and record["cost"]["model_calls"] == 1


@pytest.mark.parametrize("number", ["NaN", "Infinity", "1e999"])
def test_params_json_with_a_number_that_is_not_finite_is_invalid_input_and_nobody_is_asked(rig, number):
    params_json = ('{"sketch": ' + json.dumps(FLOAT_PARAMS["sketch"]) + ', "cases": [{"input": {"x": 1}, "output": '
                   + number + "}]}")
    reply = call(rig, {**FLOAT, "params_json": params_json})
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and "finite" in reply["error"]
    assert check_envelope(reply) == [] and rig.asked == [] and rig.served == [] and rig.model.prompts == []


def test_a_validator_that_fails_on_the_childs_ask_leaves_the_original_standing_in_a_completed_job(rig, monkeypatch):
    import types

    import g6.cegis.hole as hole

    def broke(value, *args, **kwargs):
        raise RuntimeError("the validator broke")

    monkeypatch.setattr(hole, "valid_float_grid", types.FunctionType(broke.__code__, vars(hole), "valid_float_grid"))
    reply = call(rig, FLOAT)
    assert reply.get("status") != "failed" and reply["agentic"]["outcome"] == "degraded"
    assert reply["degradation_reason"].startswith("agentic_value_invalid") and "result" not in reply
    assert reply["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED" and check_envelope(reply) == []


def _cloud_model(sent: list) -> object:
    """A cloud model behind G6's consent guard: an Anthropic caller with Anthropic's own SDK client for Anthropic's
    address (no proxy read), whose own connection pool records each request and answers it; nothing leaves the test.
    (Review 3, class A: an SDK-shaped client that is not Anthropic's own takes no timeout per request, so overrides
    refuse it.)"""
    import sys

    anthropic = pytest.importorskip("anthropic")
    from g6.core import consent_callers, llm

    client = anthropic.Anthropic(api_key="fixture-not-a-key", base_url="https://api.anthropic.com",
                                 http_client=anthropic.DefaultHttpxClient(trust_env=False))
    pool = client._client._transport._pool
    core = sys.modules[type(pool).__module__.split(".")[0]]
    message = {"id": "msg_fixture", "type": "message", "role": "assistant", "model": "fixture-model",
               "content": [{"type": "text", "text": json.dumps({"value": [0.5]})}], "stop_reason": "end_turn",
               "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}

    def handle(request):
        sent.append(request)
        return core.Response(200, headers=[(b"content-type", b"application/json")], content=json.dumps(message).encode())

    pool.handle_request = handle      # this client's own pool only: no other test's client is touched
    return consent_callers.guard(llm.AnthropicCaller("fixture-model", client=client, fallbacks=False))


@pytest.mark.parametrize("granted", [True, False])
def test_a_person_who_shared_with_the_model_is_not_refused_in_the_overrides_threads(rig, monkeypatch, granted):
    from g6.mcp.cloud_consent import request_grant
    from tests.conftest import accepted_cloud_name, cloud_argument_sources

    sent: list = []
    cloud = _cloud_model(sent)
    monkeypatch.setattr(kernel, "person_caller", lambda: cloud)

    class Accepting:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action="accept", content={"value": accepted_cloud_name(kwargs["message"])})

    async def grant():
        context = SimpleNamespace(session=Accepting(), request_id="fixture-dialog")
        # the prompt holds the arguments, the original's answer (component_data) and the kernel's own text
        for source in ("model_inputs", "component_data", *cloud_argument_sources(FLOAT)):
            assert (await request_grant(context, source, "anthropic:api"))["status"] == "granted"

    if granted:
        asyncio.run(grant())
    reply = call(rig, FLOAT)
    # Follow-up ruling: SDK sharing does not establish a total deadline, so both cases refuse before a question.
    assert reply["agentic"]["code"] == kernel.CALLER_UNMETERED and sent == [] and rig.asked == []


def test_a_ledger_that_cannot_be_read_leaves_the_original_standing_and_the_job_completes(rig):
    from g6._agentic import ledger

    target = ledger.path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("[" * 200_000 + "\n", encoding="utf-8")     # nested too deep for any JSON decoder
    reply = call(rig, FLOAT)
    assert reply.get("status") != "failed" and reply["agentic"]["outcome"] == "degraded"
    assert reply["degradation_reason"].startswith("agentic_ledger_failed") and "result" not in reply
    assert reply["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED" and check_envelope(reply) == []
