# SPDX-License-Identifier: Apache-2.0
from g6.mcp.composition import compose_reply


def test_real_grounding_composition_returns_contract_report():
    result = compose_reply({"stages": [{"component": "grounding", "op": "check_claims"}],
                            "inputs": {"text": "The limit is 42 units.",
                                       "sources": {"manual": "The limit is 42 units."}}})
    assert result["ok"] is True
    assert result["completion_state"] == "qualified-draft"
    assert result["assurance"]["status"] == "contract-checked"
    assert result["result"]["checks"][0]["status"] == "quoted"


def test_missing_input_names_the_stage_and_field():
    got = compose_reply({"stages": [{"component": "grounding", "op": "check_claims"}], "inputs": {}})
    assert got["ok"] is False
    assert got["warning_card"]["code"] == "G6_E_COMPOSITION_UNMET_INPUT"
    assert "stage 0" in got["error"] and "text" in got["error"]


def test_sensitive_operation_cannot_be_reached_through_composition():
    got = compose_reply({"stages": [{"component": "action_gating", "op": "budget_policy",
                                     "params": {"policy": {"limits": {}}, "dry_run": False}}], "inputs": {}})
    assert got["ok"] is False
    assert got["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"


def test_untrusted_composition_cannot_supply_python_or_relax_strictness():
    got = compose_reply({"stages": [], "inputs": {}, "strict": False, "python": "pass"})
    assert got["ok"] is False


def test_real_action_contract_matches_the_output():
    got = compose_reply({"stages": [{"component": "action_gating", "op": "evaluate"}],
                         "inputs": {"action": {"verb": "read", "target": "README.md"}}})
    assert got["ok"] is True
    assert "gate_decision" in got["result"]


def test_contract_survives_serialization():
    from g6.core.block_contract import contract_from_dict, contract_to_dict
    from g6.grounding.grounding_block import GroundingBlock
    contract = GroundingBlock().contract
    restored = contract_from_dict(contract_to_dict(contract))
    assert restored.outputs_per_op == contract.outputs_per_op
    assert restored.evidence_outputs_per_op == contract.evidence_outputs_per_op
