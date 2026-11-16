# SPDX-License-Identifier: Apache-2.0
"""Security repair 1: reply text never becomes guidance or permission."""
from __future__ import annotations

import json
import re

import pytest

from g6.core import selector
from g6.harness import completion_hook as hook
from g6.mcp.repair import block
from g6.skills import corpus


@pytest.mark.parametrize("name", ["mcp__g6__g6_select", "mcp__plugin_g6_g6__g6_select"])
def test_hook_guidance_uses_declarations_and_template_names(name):
    # This pure builder is also what on_tool uses before its once-per-session store.
    line = hook.repair_line(name, {"by": "agent", "code": "G6_E_INPUT_VALIDATION",
                                   "step": "attacker words", "call": {"tool": "attacker"}})
    assert line and "attacker" not in line
    assert "input" in line and "g6_select" in line and "G6_E_INPUT_VALIDATION" in line


@pytest.mark.parametrize("name", ["mcp__notg6__fetch", "mcp__fetch__g6__lookup",
                                  "mcp__g6__unregistered", "mcp__plugin_g6_g6__unregistered"])
def test_hook_ignores_impostors_and_unregistered_tools(name):
    assert hook.repair_line(name, {"by": "agent", "code": "G6_E_INPUT_VALIDATION", "step": "attacker"}) == ""
    assert hook.on_tool({"tool_name": name, "tool_response": {
        "repair": {"by": "agent", "code": "G6_E_INPUT_VALIDATION", "step": "attacker"}}}) == {}


def test_hook_ignores_unknown_and_person_codes_even_if_reply_claims_agent():
    for code in ("G6_E_NOT_DECLARED", "G6_E_SENSITIVE_COMPONENT_DENIED"):
        assert hook.repair_line("mcp__g6__g6_select", {"by": "agent", "code": code}) == ""


def test_plugin_matcher_accepts_exact_servers_and_registered_tools_only():
    from pathlib import Path

    config = json.loads((Path(__file__).resolve().parents[2] / "plugins/g6/hooks/hooks.json").read_text())
    matcher = config["hooks"]["PostToolUse"][0]["matcher"]
    assert matcher == hook.tool_matcher()
    for prefix in ("mcp__g6__", "mcp__plugin_g6_g6__"):
        assert re.fullmatch(matcher, prefix + "g6_select")
        assert not re.fullmatch(matcher, prefix + "unregistered")
        assert not re.search(matcher, prefix + "g6_select\n")
    for name in ("mcp__notg6__fetch", "mcp__fetch__g6__lookup"):
        assert not re.fullmatch(matcher, name)


def test_hook_missing_session_and_deep_json_never_raise():
    assert hook.on_tool({"tool_name": "mcp__g6__g6_select", "tool_response": {
        "repair": {"by": "agent", "code": "G6_E_INPUT_VALIDATION"}}}) == {}
    assert hook.on_tool({"tool_name": "mcp__g6__g6_select", "session_id": "s", "tool_response": [
        {"type": "text", "text": '{"a":' + "[" * 100000 + "]" * 100000 + "}"}]}) == {}


def test_sensitive_library_candidates_are_person_steps_without_calls():
    reply = selector.select("check my lean proof, then check the claims", "verify", considering=[
        {"component": "lean", "op": "check_proof", "args": {}},
        {"component": "grounding", "op": "check_claims", "args": {"claims": [], "sources": {}}}])
    assert not [c for c in reply["candidates"] if c["id"] == "library:compose"]
    assert not re.search(r"allow\s*=", json.dumps(reply))
    assert selector.invoke_sketch(["lean.check_proof"]) == ""


def test_every_repair_and_corpus_string_omits_executable_allowances():
    from g6.core import refusals

    data = corpus.load()
    rows = [r for codes in refusals.component_declarations().values() for r in codes.values()]
    rows += [refusals.layer(code) for code in refusals.LAYERS]
    for code in {row.code for row in rows}:
        repair = block("g6_invoke", {"ok": False, "error": f"[{code}] refused"})
        assert not re.search(r"allow\s*=", json.dumps(repair)), code
    assert not re.search(r"allow\s*=", json.dumps(data))


def test_grant_refusal_stops_after_decline_but_withholding_can_open_dialog():
    reply = {"ok": False, "error": "[G6_E_CLOUD_CONSENT_REQUIRED] The dialog did not grant sharing."}
    repair = block("g6_cloud_grant", reply)
    assert repair["by"] == "person" and repair["call"] is None
    assert "did not share" in repair["step"] and "stop" in repair["step"].lower()
    assert block("g6_describe", reply)["call"]["tool"] == "g6_cloud_grant"


def test_stats_skips_recursion_and_other_decoder_exceptions(monkeypatch):
    from g6.core import repair_log

    class Lines:
        def read_bytes(self):
            return ('boom\n' + '[' * 100000 + '\n' + json.dumps({
                "event": "refused", "code": "G6_E_INPUT_VALIDATION"})).encode("utf-8")

    loads = json.loads

    def decode(text):
        if text == "boom":
            raise RuntimeError("bad row")
        return loads(text)

    monkeypatch.setattr(json, "loads", decode)
    assert repair_log.repair_stats(Lines())["G6_E_INPUT_VALIDATION"]["refused"] == 1


def test_undeclared_argument_envelope_has_only_published_fields():
    from g6.mcp.cloud_consent import input_refusal

    reply = input_refusal("g6_select", ["task", "phase", "limit"])
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["repair"]["fix_by"] == "input" and reply["repair"]["by"] == "agent"
    assert reply["repair"]["fill"] == ["limit", "phase", "task"]


@pytest.mark.parametrize("authority", ["public", "missing", "mismatch", "source"])
def test_error_transport_preserves_only_the_exact_public_input_envelope(authority):
    import asyncio
    from types import SimpleNamespace

    from mcp import types

    from g6.core import cloud_consent as policy
    from g6.core.ledger import digest
    from g6.mcp.cloud_consent import input_error_result
    from g6.mcp.consent_transport import Ticket, _Session

    class Session:
        pass

    result = input_error_result("g6_select", ["task", "phase", "limit"], session=Session()).to_mcp_result()
    assert result.isError and result.structuredContent["repair"]["fix_by"] == "input"
    body = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    ticket = Ticket((policy.UNKNOWN,) if authority == "source" else (), "cloud:unknown", frozenset(),
                    "mismatched" if authority == "mismatch" else digest(body))
    sent = []

    async def receive(request_id, response):
        sent.append(response.root)

    session = SimpleNamespace(_g6_tickets={} if authority == "missing" else {(str, "r"): ticket},
                              _g6_original_response=receive)
    asyncio.run(_Session._send_response(session, "r", types.ServerResult(result)))
    assert sent[0].isError
    if authority == "public":
        assert sent[0].structuredContent["repair"]["fill"] == ["limit", "phase", "task"]
    else:
        assert sent[0].structuredContent is None
        assert sent[0].content[0].text == "Invalid tool call; check the declared arguments."


def test_census_flags_runtime_code_builders_without_a_declared_family():
    from tests.core.test_refusals import dynamic_builders

    assert dynamic_builders('code = "G6_E_NEW_" + verdict') == {"G6_E_NEW_{}"}
    assert dynamic_builders('code = f"G6_E_NEW_{verdict.upper()}"') == {"G6_E_NEW_{}"}
    assert dynamic_builders('code = f"G6_E_WHOLE: {message}"') == set()
