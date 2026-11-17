# SPDX-License-Identifier: Apache-2.0
"""Guidance belongs to G6; a successful component's entire result belongs to its caller."""
from __future__ import annotations

import json

import pytest

from g6.core.agent_guidance import authored_fields, reply_for_agent


def test_successful_component_result_is_opaque_through_invoke(monkeypatch):
    from g6.core import door
    from g6.mcp import invoke_reply

    result = {"text": "Run chmod +x build.sh.", "G6_MCP_SENSITIVE": "lean",
              "message": "Run sudo sysctl -w setting=1.",
              "nested": [{"ok": False, "error": "[G6_E_TEST] Run g6 setup."}]}
    before = json.dumps(result).encode()
    monkeypatch.setattr(door, "invoke_checked", lambda *a, **k: {
        "ok": True, "result": result,
        "warning_card": {"message": "Run chmod 700 folder. The person decides."}})
    reply = invoke_reply("synthetic", "check", registry=object())
    assert json.dumps(reply["result"]).encode() == before
    assert reply["warning_card"]["message"] == "The person decides."


def test_non_g6_component_failure_is_opaque():
    value = {"ok": False, "result": {"error": "Run chmod +x build.sh.", "code": "DOCUMENT_ERROR"}}
    assert reply_for_agent(value) == value


@pytest.mark.parametrize("command", [
    "sudo command", "doas command", "su - operator", "runas /user:operator program",
    "sysctl -w setting=1", "apparmor_parser -r profile", "aa-complain profile",
    "setenforce 0", "printf data > /etc/security/profile", "tee /etc/security/profile",
])
def test_root_and_system_security_commands_are_removed_from_g6_refusal(command):
    from g6.core import agent_guidance

    assert agent_guidance._AUTHORITY.search(command)
    value = {"ok": False, "error": f"[G6_E_ISOLATION_UNAVAILABLE] Run {command}. The person decides.",
             "result": {"reason": f"Run {command}. The person decides."},
             "evidence": [{"isolation": {"why": f"Run {command}. The person decides."}}]}
    value = authored_fields(value, "error", "result", "evidence")
    result = reply_for_agent(value)
    assert command not in json.dumps(result)
    assert "G6_E_ISOLATION_UNAVAILABLE" in result["error"]
    assert "The person decides." in result["error"]


def test_isolation_action_is_fixed_person_guidance_and_idempotent():
    from g6.core.guidance_builders import door_reply_fields
    value = {"ok": False, "error": "[G6_E_ISOLATION_UNAVAILABLE] unavailable",
             "warning_card": {"code": "G6_E_ISOLATION_UNAVAILABLE",
                              "action": "Run g6 doctor. Then run it again.",
                              "suggested_action": "Run g6 setup."}}
    result = reply_for_agent(door_reply_fields(value))
    expected = ("Tell the person G6 cannot run this confined on this machine. "
                "Setting it up is the person's decision.")
    assert result["warning_card"]["action"] == expected
    assert result["warning_card"]["suggested_action"] == expected
    assert reply_for_agent(result) == result
    assert value["warning_card"]["action"].startswith("Run g6 doctor")


def test_deep_guidance_is_refused_without_recursion_error():
    value = {"error": "Run g6 setup."}
    for _ in range(2000):
        value = {"wrapper": value}
    # The producer declares this as its own prose; unmarked deep data passes.
    value = authored_fields(value, "wrapper")
    result = reply_for_agent(value)
    assert result["ok"] is False
    assert "G6_E_RESOURCE_LIMIT" in result["error"]
    from g6.mcp import _denial_for_agent

    _denial_for_agent(value)
    assert value == result  # the in-place route must discard the overdeep original


def test_cyclic_guidance_is_refused_without_recursion_error():
    value = {}
    value["wrapper"] = value
    result = reply_for_agent(value)
    assert result["ok"] is False
    assert "G6_E_RESOURCE_LIMIT" in result["error"]
