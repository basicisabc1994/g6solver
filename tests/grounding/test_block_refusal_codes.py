# SPDX-License-Identifier: Apache-2.0
"""The block's door: an unknown field is refused, every refusal carries a stable G6_E_ code, and a blocked check names
its code (conformance kit, D4 and D6)."""
from __future__ import annotations

import json

import pytest

from g6.mcp import invoke_reply

POLICY = {"refund-policy.md": "Refunds are issued within 14 days of a return. Gift cards are not refundable."}


def _reply(op: str, params: dict) -> dict:
    return invoke_reply("grounding", op, json.dumps(params))


def test_an_unknown_field_is_refused_not_dropped() -> None:
    reply = _reply("check_claim", {"claim": "Refunds are issued within 14 days", "source": POLICY})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), reply["error"]


@pytest.mark.parametrize(("op", "params", "code"), [
    ("check_claim", {}, "G6_E_GROUNDING_SOURCES_REQUIRED"),
    ("check_claims", {}, "G6_E_GROUNDING_SOURCES_REQUIRED"),
    ("check_claim", {"sources": POLICY}, "G6_E_GROUNDING_CLAIM_REQUIRED"),
    ("check_claims", {"text": "   ", "sources": POLICY}, "G6_E_GROUNDING_NO_CHECKABLE_SENTENCE"),
])
def test_each_refusal_is_coded(op: str, params: dict, code: str) -> None:
    reply = _reply(op, params)
    assert reply["ok"] is False and reply["error"].startswith(f"[{code}]"), reply["error"]


@pytest.mark.parametrize(("op", "params", "code"), [
    ("check_claim", {"claim": "Refunds are issued within 30 days", "sources": POLICY}, "G6_E_GROUNDING_CONFLICT"),
    ("check_claim", {"claim": "Shipping is free worldwide", "sources": POLICY}, "G6_E_GROUNDING_NOT_FOUND"),
    ("check_claims", {"text": "Refunds are issued within 14 days of a return. Shipping is free worldwide.",
                      "sources": POLICY}, "G6_E_GROUNDING_NOT_FOUND"),
    ("check_claims", {"text": "Shipping is free worldwide. Gift cards are refundable.", "sources": POLICY},
     "G6_E_GROUNDING_CONFLICT"),  # the worst check names the code
])
def test_a_blocked_check_names_its_code(op: str, params: dict, code: str) -> None:
    reply = _reply(op, params)
    assert reply["result"]["completion_state"] == "blocked-escalated"
    assert reply["result"]["warning_card"]["code"] == code and reply["warning_card"]["code"] == code


def test_a_supported_check_needs_no_code() -> None:
    result = _reply("check_claim", {"claim": "Refunds are issued within 14 days of a return", "sources": POLICY})["result"]
    assert result["completion_state"] == "qualified-draft" and "warning_card" not in result
