"""Owner UAT regressions: interoperable consent and understandable state reports."""
import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from fastmcp.client.elicitation import ElicitResult  # noqa: E402

from g6.mcp import build_server, describe_reply  # noqa: E402
from g6.metaharness.api import _envelope  # noqa: E402
from g6.metaharness.surface import Surface  # noqa: E402
from g6.core.reliability_envelope import envelope_fields  # noqa: E402


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_consent_uses_portable_schema_and_reports_client_outcome(tmp_path, monkeypatch, action):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    spec = {"name": "consent", "task": "Label {input}",
            "cases": [{"input": "red", "expected": "warm"}, {"input": "blue", "expected": "cool"}],
            "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"}}
    started = surface.start(spec)
    seen = []

    async def handler(message, response_type, params, context):
        schema = params.requestedSchema
        # Codex 0.164 rejects Pydantic's extra root title before displaying a prompt.
        assert set(schema) <= {"$schema", "type", "properties", "required"}
        assert started["workflow_digest"] in message
        seen.append(message)
        return response_type(value="Test person") if action == "accept" else ElicitResult(action=action)

    async def run():
        async with Client(build_server(surface), elicitation_handler=handler) as client:
            reply = await client.call_tool("metaharness_confirm", {
                "workflow": "consent", "workflow_digest": started["workflow_digest"]})
            return json.loads(reply.content[0].text)

    result = asyncio.run(run())
    assert len(seen) == 1
    if action == "accept":
        assert result["code"] == "G6_E_META_CONFIRMED"
    else:
        assert result["consent_status"] == {"decline": "declined", "cancel": "cancelled"}[action]
        assert result["completion_state"] == "blocked-escalated"
        assert surface.inspect("consent")["confirmed"] is None


def test_unverified_envelope_always_explains_degradation_without_promoting():
    for state in ("qualified-draft", "blocked-escalated"):
        result = envelope_fields(reliability_label=state)
        assert result["degradation_reason"]
        assert result["completion_state"] == state
    assert envelope_fields(degradation_reason="specific limitation")["degradation_reason"] == "specific limitation"


def test_successful_history_is_not_an_unexplained_warning():
    result = _envelope("qualified-draft", "G6_E_META_INSPECT", "Workflow history; no operation executed")
    assert "not independently verified application behavior" in result["warning_card"]["message"]
    assert "recorded consent" in result["warning_card"]["suggested_action"]
    assert result["degradation_reason"]
    assert result["completion_state"] == "qualified-draft"


def test_grounding_contract_identifies_lexical_method():
    from g6.grounding.grounding_block import GroundingBlock
    assert GroundingBlock.__g6_block_contract__.verification_method == "lexical_sentence_overlap"
    assert describe_reply("grounding")["available"]


@pytest.mark.parametrize("content", [None, {}, {"value": " "}, {"value": 123}])
def test_accepted_but_invalid_identity_never_authorizes(content):
    from g6.mcp.consent import request_consent
    async def elicit_form(**kwargs):
        return SimpleNamespace(action="accept", content=content)
    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="test")
    result = asyncio.run(request_consent(ctx, "Confirm digest x?"))
    assert result.status == "invalid" and result.answer is None


def test_elicitation_failure_is_distinct_without_exposing_exception_text():
    from g6.mcp.consent import request_consent, refusal
    async def elicit_form(**kwargs):
        raise RuntimeError("PLANTED_PRIVATE_DETAILS")
    ctx = SimpleNamespace(session=SimpleNamespace(elicit_form=elicit_form), request_id="test")
    result = refusal(asyncio.run(request_consent(ctx, "Confirm digest x?")))
    assert result["consent_status"] == "unavailable"
    assert result["consent_error_type"] == "RuntimeError"
    assert "PLANTED_PRIVATE_DETAILS" not in json.dumps(result)
    assert result["completion_state"] == "blocked-escalated"


def test_changed_digest_refuses_before_any_consent_request(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    surface.start({"name": "changed", "task": "Label {input}",
                   "cases": [{"input": "red", "expected": "warm"}, {"input": "blue", "expected": "cool"}],
                   "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"}})
    prompts = []
    async def handler(*args):
        prompts.append(args)
        return ElicitResult(action="cancel")
    async def run():
        async with Client(build_server(surface), elicitation_handler=handler) as client:
            reply = await client.call_tool("metaharness_confirm", {"workflow": "changed", "workflow_digest": "0" * 64})
            return json.loads(reply.content[0].text)
    result = asyncio.run(run())
    assert result["consent_status"] == "workflow_changed"
    assert prompts == [] and surface.inspect("changed")["confirmed"] is None
