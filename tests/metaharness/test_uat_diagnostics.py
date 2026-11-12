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

pytestmark = pytest.mark.usefixtures("declared_mcp_grants")


@pytest.mark.parametrize('sharing_scope', ['missions', 'improvement', 'component_data', 'jobs'])
@pytest.mark.parametrize('scope_granted', [False, True])
def test_real_retained_scope_capture(tmp_path, monkeypatch, sharing_scope, scope_granted, accepted_cloud_dialog):
    """Seed actual stores, then capture only at the MCP recipient boundary."""
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    marker = 'PLANTED_REAL_RETAINED_' + sharing_scope.upper()
    jobs = None
    if sharing_scope == 'missions':
        from g6.workforce import api, bench, service
        monkeypatch.delenv(bench.ENV_DB_DIR, raising=False)
        wf = api.bind()
        made = service.create(wf, objective=marker, risk_tier='R2', budget_cents=0)
        tool, args = 'g6_mission_status', {'mission_id': made['mission_id'], 'detail': 'full'}
        assert marker in json.dumps(api.status(wf, made['mission_id'], detail='full'))
        server = build_server()
    elif sharing_scope == 'improvement':
        from tests.core.test_mcp_improve import Rig
        rig = Rig(tmp_path, monkeypatch)
        (rig.root / 'STOP').write_text(marker, encoding='utf-8')
        tool, args = 'g6_improve_status', {'folder': str(rig.root)}
        server = rig.server
    elif sharing_scope == 'component_data':
        from g6.workspace.align_prompt_library import ops
        made = ops.put('prompt', 'retained', marker)
        assert made.completion_state != 'blocked-escalated'
        tool, args = 'g6_invoke', {'component': 'align_prompt_library', 'op': 'get',
                                 'params_json': json.dumps({'name': 'retained'})}
        server = build_server()
    else:
        import g6.metaharness.jobs as module
        from g6.core.consent_declarations import declares
        jobs = module.Jobs()
        monkeypatch.setattr(module, 'Jobs', lambda: jobs)
        path = tmp_path / 'retained.txt'
        path.write_text(marker, encoding='utf-8')
        from g6.core import cloud_consent as cloud
        source = cloud.repository_source(path)
        accepted_cloud_dialog(source, 'openai:codex', text=marker)
        server = build_server()
        @server.tool()
        @declares('source:jobs')
        def retain_for_later() -> dict:
            def read_retained():
                text = path.read_text(encoding='utf-8')
                return cloud.bind_output({'result': text}, (cloud.Source.text(source, path.name, text),))
            return jobs.start(read_retained)
        tool, args = 'metaharness_job', {}
    async def read():
        async with Client(server) as client:
            if jobs is not None:
                handle = (await client.call_tool('retain_for_later', {})).data['job_id']
                for _ in range(200):
                    received = await client.call_tool(tool, {'job_id': handle})
                    if received.data.get('status') not in ('pending', 'running'):
                        return str(received)
                    await asyncio.sleep(.005)
                pytest.fail('the retained fake job did not finish')
            return str(await client.call_tool(tool, args))
    try:
        assert (marker in asyncio.run(read())) is scope_granted
    finally:
        if jobs is not None:
            jobs.close()


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


def _confirm_with(tmp_path, monkeypatch, handler=None) -> dict:
    """metaharness_confirm on a fresh workflow, through a client whose form ``handler`` answers (none: no form)."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    surface = Surface(workflow_root=tmp_path / "workflows")
    started = surface.start({"name": "consent", "task": "Label {input}",
                             "cases": [{"input": "red", "expected": "warm"}, {"input": "blue", "expected": "cool"}],
                             "baseline_code": {"main.py": "def solve(task):\n    return 'unknown'\n"}})

    async def run():
        options = {"elicitation_handler": handler} if handler else {}
        async with Client(build_server(surface), **options) as client:
            reply = await client.call_tool("metaharness_confirm", {
                "workflow": "consent", "workflow_digest": started["workflow_digest"]})
            return json.loads(reply.content[0].text)

    result = asyncio.run(run())
    assert surface.inspect("consent")["confirmed"] is None
    return result


@pytest.mark.parametrize("action", ["decline", "cancel"])
def test_a_no_in_the_form_is_never_followed_by_another_way_to_confirm(tmp_path, monkeypatch, action):
    """The owner's pilot (2026-10-30): after a decline, the reply offered the terminal confirm as the next step. In
    Codex the form has no Decline, so a cancel is how the person says no there."""
    async def handler(message, response_type, params, context):
        return ElicitResult(action=action)

    result = _confirm_with(tmp_path, monkeypatch, handler)
    assert result["consent_status"] == {"decline": "declined", "cancel": "cancelled"}[action]
    text = json.dumps(result)
    assert "workflow confirm" not in text and "--as" not in text, text
    assert "ask the person" in text.lower(), text


def test_a_client_without_a_form_is_offered_no_terminal_and_told_what_the_person_can_do(tmp_path, monkeypatch):
    """Changed on purpose (owner's standing rule: consent through the client's dialog, no terminal fallback): this
    pinned the ``g6 workflow confirm`` fallback."""
    result = _confirm_with(tmp_path, monkeypatch)
    assert result["consent_status"] == "unavailable", result
    assert result["warning_card"]["code"] == "G6_E_CONSENT_NO_DIALOG"
    assert "workflow confirm" not in json.dumps(result) and "--as" not in json.dumps(result)
    assert "client session" in result["next_action"]


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
