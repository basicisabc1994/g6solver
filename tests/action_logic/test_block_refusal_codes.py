# SPDX-License-Identifier: Apache-2.0
"""The block's door: an unknown field is refused, and every refusal carries a stable G6_E_ code that names what is
wrong, never an internal TypeError's text (conformance kit, D4)."""
from __future__ import annotations

import json

import pytest

from g6.mcp import invoke_reply

_REQUIRED = "G6_E_ACTION_LOGIC_FIELD_REQUIRED"
_INVALID = "G6_E_ACTION_LOGIC_INVALID_FIELD"
HYP = {"hypothesis_id": "h1", "statement": "caching cuts latency"}


def _reply(op: str, params: dict) -> dict:
    return invoke_reply("action_logic", op, json.dumps(params))


def test_an_unknown_field_is_refused_not_dropped() -> None:
    reply = _reply("detect_conflicts", {"proposed": {"action_id": "a"}, "actives": []})
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), reply["error"]


@pytest.mark.parametrize(("op", "field"), [
    ("update_on_outcome", "hypothesis"),
    ("apply_disconfirming_evidence", "hypothesis"),
    ("disconfirming_evidence_severity", "hypothesis"),
    ("reauthorisation_severity", "relied_hypothesis"),
])
def test_a_hypothesis_left_out_names_the_missing_fields(op: str, field: str) -> None:
    reply = _reply(op, {})
    error = reply["error"]
    assert reply["ok"] is False and error.startswith(f"[{_REQUIRED}] {op}:"), error
    assert f"{field}.hypothesis_id" in error and f"{field}.statement" in error
    assert "__init__" not in error and "positional" not in error


def test_a_proposed_action_left_out_is_a_coded_refusal() -> None:
    error = _reply("detect_conflicts", {})["error"]
    assert error.startswith(f"[{_REQUIRED}] detect_conflicts: proposed"), error


def test_a_record_field_the_record_does_not_have_is_named() -> None:
    error = _reply("detect_conflicts", {"proposed": {"action_id": "a", "colour": "red"}})["error"]
    assert error.startswith(f"[{_INVALID}] detect_conflicts:") and "proposed.colour" in error, error
    assert "__init__" not in error


@pytest.mark.parametrize(("params", "expect"), [
    ({"hypothesis": HYP}, f"[{_REQUIRED}] update_on_outcome: outcome"),
    ({"hypothesis": HYP, "outcome": "maybe"}, f"[{_INVALID}] update_on_outcome: outcome"),
    ({"hypothesis": {**HYP, "status": "certain"}, "outcome": "confirmed"}, f"[{_INVALID}] update_on_outcome:"),
])
def test_an_outcome_or_status_it_cannot_read_is_coded(params: dict, expect: str) -> None:
    error = _reply("update_on_outcome", params)["error"]
    assert error.startswith(expect), error
