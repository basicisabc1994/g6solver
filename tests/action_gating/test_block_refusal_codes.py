# SPDX-License-Identifier: Apache-2.0
"""The block's door: an unknown field is refused, every refusal carries a stable G6_E_ code, and a blocked decision
names its code in its warning card (conformance kit, D4 and D6)."""
from __future__ import annotations

import json

import pytest

from g6.mcp import invoke_reply


def _reply(op: str, params: dict) -> dict:
    return invoke_reply("action_gating", op, json.dumps(params))


def test_an_unknown_field_is_refused_not_dropped() -> None:
    reply = _reply("evaluate", {"action": {"action_type": "write_file"}, "actoin": {}})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), reply["error"]


@pytest.mark.parametrize("op", ["evaluate", "explain"])
def test_an_action_left_out_is_a_coded_refusal(op: str) -> None:
    reply = _reply(op, {})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_ACTION_GATING_ACTION_REQUIRED]"), reply["error"]
    assert reply["warning_card"]["code"] == "G6_E_ACTION_GATING_ACTION_REQUIRED"


def test_an_action_the_capsule_cannot_take_is_a_coded_refusal() -> None:
    reply = _reply("evaluate", {"action": {"action_type": "write_file", "reversibility": "sometimes"}})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_ACTION_GATING_ACTION_INVALID]"), reply["error"]


def test_a_blocked_decision_names_its_code() -> None:
    reply = _reply("evaluate", {"action": {"action_type": "send_email", "target_resource": "customer@example.com"}})
    result = reply["result"]
    assert result["completion_state"] == "blocked-escalated"
    assert result["warning_card"]["code"] == "G6_E_ACTION_GATING_BLOCKED"
    assert reply["warning_card"]["code"] == "G6_E_ACTION_GATING_BLOCKED"


def test_a_decision_short_of_blocked_needs_no_card_code() -> None:
    result = _reply("evaluate", {"action": {"action_type": "write_file", "target_resource": "notes.txt"}})["result"]
    assert result["completion_state"] == "qualified-draft" and "code" not in result["warning_card"]
