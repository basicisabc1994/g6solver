# SPDX-License-Identifier: Apache-2.0
"""g6-mcp ``g6_invoke`` replies carry the library envelope and never raise a state (MCP-ENV). Offline."""
from __future__ import annotations

import json

import pytest
from returns.result import Failure, Success

from g6.invariants import check_envelope
from g6.mcp import invoke_reply


class _Registry:
    def __init__(self, result) -> None:
        self.result = result

    def invoke(self, component, op, params, *, block_sensitive=False):  # noqa: ANN001, ANN201
        assert block_sensitive is True  # the MCP surface never runs a sensitive op
        return self.result


def _reply(result, params: str = "{}") -> dict:
    found = invoke_reply("demo", "op", params, registry=_Registry(result))
    assert check_envelope(found) == [], found
    return found


def test_a_failure_is_blocked_with_its_own_code() -> None:
    found = _reply(Failure("[G6_E_UNKNOWN_COMPONENT] demo"))
    assert found["ok"] is False and found["completion_state"] == "blocked-escalated"
    assert found["warning_card"]["code"] == "G6_E_UNKNOWN_COMPONENT" and found["error"].endswith("demo")
    assert _reply(Failure("something broke"))["warning_card"]["code"] == "G6_E_INVOKE_FAILED"


@pytest.mark.parametrize("params", ["not json", "[1, 2]"])
def test_bad_parameters_are_blocked(params: str) -> None:
    found = _reply(Success({}), params)
    assert found["completion_state"] == "blocked-escalated" and found["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


def test_a_result_with_no_state_is_a_draft() -> None:
    found = _reply(Success({"answer": 42}))
    assert found["ok"] is True and found["result"] == {"answer": 42}
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == "G6_E_QUALIFIED_DRAFT"


def test_a_draft_carries_no_made_up_confidence() -> None:
    """The envelope used to fill confidence 0.5 into every qualified-draft: a constant that reads as a measurement."""
    assert _reply(Success({"answer": 42}))["confidence"] is None


def test_the_components_own_run_id_is_carried() -> None:
    assert _reply(Success({"completion_state": "qualified-draft", "run_id": "run-7"}))["run_id"] == "run-7"
    assert _reply(Success({"run_id": 7}))["run_id"] == ""   # only a string is an id


def test_verified_without_evidence_is_held_at_draft() -> None:
    found = _reply(Success({"completion_state": "verified"}))
    assert found["completion_state"] == "qualified-draft" and found["result"]["completion_state"] == "qualified-draft"
    assert found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"   # G6_E_VERIFIED_WITHOUT_EVIDENCE is retired


def test_verified_with_evidence_is_held_without_g6s_recheck() -> None:
    """One outcome (plan A1): evidence a component attaches is its own; with no re-check by G6 its verified is held."""
    found = _reply(Success({"completion_state": "verified", "evidence": [{"kind": "test", "ref": "t1"}]}))
    assert found["completion_state"] == "qualified-draft" and found["evidence"]
    assert found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"


def test_a_blocked_result_keeps_its_own_card() -> None:
    card = {"code": "G6_E_POLICY_BLOCKED", "message": "refused", "suggested_action": "ask a person"}
    found = _reply(Success({"completion_state": "blocked-escalated", "warning_card": card}))
    assert found["completion_state"] == "blocked-escalated" and found["warning_card"]["code"] == "G6_E_POLICY_BLOCKED"


@pytest.mark.parametrize("value", [
    {"completion_state": "blocked-escalated", "answer": 42},
    {"reliability_label": "blocked", "answer": 42},
    {"completion_state": "qualified-draft", "reliability_label": "failed", "answer": 42},
])
def test_ok_is_false_when_the_result_is_blocked(value: dict) -> None:
    """``ok`` says whether the reply may be used: a component that answered with a block has not been (plan Step
    1). Its answer is still there to read; ``error`` stays for a reply with no result."""
    found = _reply(Success(value))
    assert found["ok"] is False and found["completion_state"] == "blocked-escalated"
    assert found["result"]["answer"] == 42 and "error" not in found


@pytest.mark.parametrize("value", [{"answer": 42}, {"completion_state": "qualified-draft"},
                                   {"completion_state": "verified", "evidence": [{"kind": "test"}]}])
def test_ok_stays_true_for_a_result_that_is_not_blocked(value: dict) -> None:
    assert _reply(Success(value))["ok"] is True


def test_the_promotion_law_demotion_carries_its_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("G6_KERNEL_PROMOTION_LAW", "off")   # no switch turns the law off
    found = _reply(Success({"completion_state": "verified", "evidence": [{"kind": "test"}]}))
    assert found["completion_state"] == "qualified-draft" and found["warning_card"]["code"] == "G6_E_PROMOTION_LAW"


def test_a_real_component_reply_is_a_whole_envelope() -> None:
    found = invoke_reply("action_gating", "evaluate", json.dumps({"action": {"action_type": "read_file"}}))
    assert found["ok"] is True and check_envelope(found) == [], found
    assert found["completion_state"] != "verified" or found["evidence"]
