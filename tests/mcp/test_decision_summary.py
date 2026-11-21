# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from g6.mcp import invoke_reply


def test_asserted_gate_reply_has_one_summary_without_changing_ok():
    reply = invoke_reply("coding_agent_gate", "verify_answer", json.dumps({
        "task": "Fix sum", "answer": "Changed sum.", "task_contract": "sum adds inputs",
        "evidence": [{"kind": "test", "ref": "pytest -q", "exit_code": 0}]}))
    assert reply["ok"] is True
    assert reply["decision"] == {"may_proceed": None, "declared": True, "evidence_status": "asserted",
                                 "action_authorization": "not_requested"}


def test_an_undeclared_help_reply_is_explicitly_unknown():
    reply = invoke_reply("coding_agent_gate", "help")
    assert reply["decision"]["may_proceed"] is None
    assert reply["decision"]["declared"] is False


@pytest.mark.parametrize("params", ["{", "[]", "null", '{"bogus": true}'])
def test_validation_replies_also_carry_an_unknown_decision(params):
    reply = invoke_reply("coding_agent_gate", "verify_answer", params)
    assert reply["ok"] is False
    assert reply["decision"]["may_proceed"] is None
    assert reply["decision"]["action_authorization"] == "not_requested"


def test_sensitive_refusal_and_unknown_component_have_no_authorization():
    for component, op in [("action_gating", "budget_check"), ("not_a_component", "help")]:
        reply = invoke_reply(component, op, allow_sensitive=frozenset())
        assert reply["ok"] is False
        assert reply["decision"]["may_proceed"] is None
        assert reply["decision"]["action_authorization"] == "not_requested"


def test_a_sensitive_refusal_keeps_its_public_declaration():
    # The summary is attached before the refusal is declared public instructions: a change after that declaration
    # reads as unknown content, and the transport would withhold the denial as G6_E_CLOUD_CONSENT_REQUIRED.
    from g6.core import cloud_consent as cloud
    with cloud.collect_sources():
        reply = invoke_reply("align_prompt_library", "put", "{}", allow_sensitive=frozenset())
        assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"
        assert cloud.output_sources(reply) == ()


def test_real_action_block_is_exposed_by_mcp_summary():
    reply = invoke_reply("action_gating", "evaluate", json.dumps({"action": {
        "action_type": "send_email", "target_resource": "customer@example.com"}}))
    assert reply["result"]["gate_decision"]["decision"] == "block"
    assert reply["decision"] == {"may_proceed": False, "declared": True, "evidence_status": "asserted",
                                 "action_authorization": "refused"}


def test_outer_mcp_argument_validation_has_an_unknown_summary():
    from g6.mcp.cloud_consent import input_refusal
    reply = input_refusal("g6_invoke", ["component", "op", "params_json"], arguments={"unexpected": True})
    assert reply["ok"] is False
    assert reply["decision"] == {"may_proceed": None, "declared": False, "evidence_status": "asserted",
                                 "action_authorization": "not_requested"}


def test_transport_withholding_never_reuses_a_components_allowed_summary():
    from g6.mcp.cloud_consent import withheld_reply
    reply = withheld_reply({"ok": True, "decision": {"may_proceed": True, "action_authorization": "allowed"}})
    assert reply["ok"] is False
    assert reply["decision"] == {"may_proceed": None, "declared": False, "evidence_status": "asserted",
                                 "action_authorization": "not_requested"}
