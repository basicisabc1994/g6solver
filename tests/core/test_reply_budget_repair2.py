"""Fallback controls are bounded data; only bounded G6 codes are issued codes."""
import copy
import json

import pytest
from mcp import types
from mcp.shared.message import SessionMessage
from returns.result import Success

from g6.core.door import door_reply
from g6.core.reply_budget import MAX_REPLY_BYTES as CAP, SELECTOR, oversize_refusal
from g6.mcp.reply_egress import budget_packet


def packet(value):
    return SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(
        jsonrpc="2.0", id=1, result={"content": [{"type": "text", "text": json.dumps(value)}],
                                     "structuredContent": value})))


def code_position(position, code):
    if position == "warning_card":
        return {"warning_card": {"code": code, "message": "component warning"}}
    if position == "error_text":
        return {"error": f"[{code}] component failure"}
    if position in ("error", "repair", "remedy"):
        return {position: {"code": code}}
    if position == "diagnostic":
        return {"mechanisms": [{"stage": "facts", "facts": {"last_outcome": code}}]}
    return {position: code}


POSITIONS = ["warning_card", "code", "cloud_consent_code", "error_text", "error", "repair", "remedy", "diagnostic"]


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("code", ["G6_E_" + "A" * 65, "G6_E_", "G6_E_lower", "G6_E_OK\n", "application"])
def test_non_g6_code_is_withheld_whole_in_every_declared_code_position(position, code):
    original = {**code_position(position, code), "payload": "x" * CAP}
    before = copy.deepcopy(original)
    shown = oversize_refusal(original, schema=SELECTOR)
    encoded = json.dumps(shown)
    assert shown["warning_card"]["code"] == "G6_E_REPLY_WITHHELD"
    assert json.dumps(code) not in encoded
    assert "not a G6 code" in encoded
    assert len(encoded.encode()) <= CAP
    assert original == before


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("suffix", ["A", "A" * 64])
def test_real_issued_codes_survive_fallback_verbatim(position, suffix):
    code = "G6_E_" + suffix
    shown = oversize_refusal({**code_position(position, code), "payload": "x" * CAP}, schema=SELECTOR)
    assert code in json.dumps(shown)
    assert "G6_E_INPUT_VALIDATION" not in json.dumps(shown)


@pytest.mark.parametrize("field,maximum", [("request_id", 256), ("run_id", 256), ("job_id", 256),
                                           ("status", 64), ("completion_state", 64)])
def test_fallback_identity_is_kept_at_maximum_and_withheld_whole_above_it(field, maximum):
    for length, expected in [(maximum, "\U0001f642" * maximum), (maximum + 1, "[withheld for size]")]:
        shown = oversize_refusal({field: "\U0001f642" * length})
        assert shown[field] == expected


@pytest.mark.parametrize("field", ["message", "suggested_action"])
def test_oversized_card_fields_are_withheld_without_losing_the_issued_code(field):
    code = "G6_E_CLOUD_CONSENT_REQUIRED"
    card = {"code": code, "message": "warning", "suggested_action": "inspect"}
    card[field] = "x" * 2049
    shown = oversize_refusal({"warning_card": card})
    assert shown["warning_card"]["code"] == code
    assert card[field] not in json.dumps(shown)
    assert "[withheld for size]" in json.dumps(shown)


@pytest.mark.parametrize("code", ["G6_E_" + "A" * 87381, ["G6_E_OK"], {"code": "G6_E_OK"}])
def test_real_component_warning_data_cannot_expand_the_transport_fallback(code):
    original = door_reply("synthetic", "read", Success({
        "completion_state": "qualified-draft", "warning_card": {"code": "G6_E_" + "A" * 87381,
                                                                  "message": "component warning"}}))
    # Exercise non-string shapes too, at the declared envelope card position.
    original["warning_card"]["code"] = code
    original["padding"] = "x" * CAP
    result = budget_packet(packet(original)).message.root.result
    shown = result["structuredContent"]
    assert len(json.dumps(result).encode()) <= CAP
    assert json.loads(result["content"][0]["text"]) == shown
    assert shown["warning_card"]["code"] == "G6_E_REPLY_WITHHELD"
    assert "not a G6 code" in json.dumps(shown)


def test_egress_checks_the_last_fallback_even_if_the_compactor_returns_oversize(monkeypatch):
    import g6.mcp.reply_egress as egress

    original = {"code": "G6_E_CLOUD_CONSENT_REQUIRED", "payload": "x" * CAP}
    monkeypatch.setattr(egress, "fit_reply", lambda value, **kwargs: value)
    result = egress.budget_packet(packet(original)).message.root.result
    assert len(json.dumps(result).encode()) <= CAP
    assert result["structuredContent"]["warning_card"]["code"] == "G6_E_CLOUD_CONSENT_REQUIRED"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_largest_fixed_template_fits_with_all_kept_values_at_their_maxima():
    from g6.core.reply_budget import IDENTITY_LIMITS, fixed_refusal

    # Astral characters cost twelve bytes in JSON and fourteen in the nested MCP
    # text; ASCII G6 codes cannot expand. Exercise the actual fixed constructor.
    controls = {key: "\U0001f642" * IDENTITY_LIMITS[key] for key in ("request_id", "run_id", "job_id", "status")}
    value = fixed_refusal("G6_E_" + "A" * 64, controls=controls)
    wire = packet(value).message.root.result
    wire["isError"] = False
    assert len(json.dumps(wire).encode()) < CAP
    for key, expected in controls.items():
        assert value[key] == expected
    assert value["warning_card"]["code"] == "G6_E_" + "A" * 64
