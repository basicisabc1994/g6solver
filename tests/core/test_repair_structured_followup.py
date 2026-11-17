# SPDX-License-Identifier: Apache-2.0
"""The agent filter changes prose, never identifiers or runnable templates."""
from __future__ import annotations

import copy
import json

import pytest

from g6.core.agent_guidance import reply_for_agent, text_for_agent


CALL = {"tool": "g6_cloud_grant", "arguments": {
    "source": "G6_MCP_SENSITIVE", "provider": "openai:codex",
    "message": "Run g6 setup.", "G6_ALLOW_UNCONFINED": "unchanged",
    "params_json": '{"allow_sensitive": false, "tool": "g6_find"}',
}}
STRUCTURED = {
    "id": "tool:g6_select", "component": "g6_find", "op": "g6_describe",
    "code": "G6_E_INPUT_VALIDATION", "fix_by": "tool:g6_cloud_grant",
    "call": CALL, "arguments": CALL["arguments"],
    "fill": ["G6_MCP_SENSITIVE", "g6_invoke", "allow_sensitive"],
    "next": {"id": "tool:g6_select", "call": CALL, "fill": ["allow_sensitive"]},
    "validated": True, "retries_left": 1,
    "G6_MCP_SENSITIVE": "structured key", "unknown_id": "G6_REQUIRE_ISOLATION",
}


@pytest.mark.parametrize("family", ["selector", "repair", "invoke", "describe", "consent"])
def test_every_reply_family_preserves_all_structured_fields_byte_for_byte(family):
    # Filtering every leaf used to erase g6_* tool names and entire template fields.
    body = copy.deepcopy(STRUCTURED)
    body.update(step="Run g6 setup. The person decides.",
                reason="Run chmod 700 folder. Keep the evidence.",
                message="Set G6_MCP_SENSITIVE=lean. Tell the person.")
    expected = copy.deepcopy(body)
    expected.update(step="The person decides.", reason="Keep the evidence.", message="Tell the person.")
    wrappers = {
        "selector": lambda value: {"ok": True, "candidates": [value]},
        "repair": lambda value: {"ok": False, "repair": value},
        "invoke": lambda value: {"ok": False, "result": value},
        "describe": lambda value: {"component": "synthetic", "ops": [value]},
        "consent": lambda value: {"cloud_consent_code": "G6_E_CLOUD_CONSENT_REQUIRED", "repair": value},
    }
    original = wrappers[family](body)
    before = json.dumps(original, ensure_ascii=False).encode("utf-8")
    filtered = reply_for_agent(original)
    assert json.dumps(filtered, ensure_ascii=False).encode("utf-8") == json.dumps(
        wrappers[family](expected), ensure_ascii=False).encode("utf-8")
    assert json.dumps(original, ensure_ascii=False).encode("utf-8") == before


def test_g6_tool_names_in_prose_do_not_name_authority_commands():
    text = "Read g6_select, g6_find and g6_describe. Ask g6_cloud_grant for the person's dialog."
    assert text_for_agent(text) == text


def test_warning_text_and_help_prose_are_filtered_inside_nested_results():
    from g6.core.agent_guidance import _ISOLATION_ACTION

    value = {"warning_card": {"code": "G6_E_ISOLATION_UNAVAILABLE",
                              "action": "Run icacls folder /grant identity:F. The person decides."},
             "next_actions": ["Run g6 setup. Tell the person."],
             "result": {"help": "Set G6_REQUIRE_ISOLATION=0. Read the fields.",
                        "degradation_reason": "Run setfacl folder. Keep the evidence."}}
    assert reply_for_agent(value) == {
        # The isolation card action is G6's fixed sentence (lead ruling, wire-repair follow-up repair 1, F5).
        "warning_card": {"code": "G6_E_ISOLATION_UNAVAILABLE", "action": _ISOLATION_ACTION,
                         "suggested_action": _ISOLATION_ACTION},
        "next_actions": ["Tell the person."],
        "result": {"help": "Read the fields.", "degradation_reason": "Keep the evidence."}}


@pytest.mark.parametrize("field", [
    "suggested_action", "next_step", "why", "risks", "known_limitations", "missing_checks",
    "recommended_next_steps", "block_reason", "required_human_decision", "evidence_collected",
    "safe_next_action", "rollback_or_containment",
])
def test_envelope_warning_prose_does_not_repeat_authority_in_mirrored_fields(field):
    text = "Run g6 setup. The person decides."
    value = {"warning_card": {field: text}, field: [text]}
    assert reply_for_agent(value) == {"warning_card": {field: "The person decides."},
                                      field: ["The person decides."]}
