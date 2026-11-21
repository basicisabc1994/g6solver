# SPDX-License-Identifier: Apache-2.0
import pytest

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


def _past_plain_text_limit(kind):
    from g6.core.external_data import MAX_PLAIN_VALUES
    if kind == "deep":
        value = "The limit is 42 units."
        for _ in range(65):
            value = [value]
        return value, "nested deeper than"
    if kind == "cyclic":
        value = ["The limit is 42 units."]
        value.append(value)
        return value, "holds itself"
    return ["The limit is 42 units."] * (MAX_PLAIN_VALUES + 1), f"holds more than {MAX_PLAIN_VALUES} values"


@pytest.mark.parametrize("kind", ["deep", "cyclic", "wide"])
def test_stage_input_past_the_plain_text_limits_is_an_input_validation_refusal(kind):
    value, said = _past_plain_text_limit(kind)
    got = compose_reply({"stages": [{"component": "grounding", "op": "check_claims"}],
                         "inputs": {"text": value, "sources": {"manual": "The limit is 42 units."}}})
    assert got["ok"] is False
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION", got["error"]
    assert "stage 0: text must be JSON data" in got["error"] and said in got["error"]


@pytest.mark.parametrize("kind", ["deep", "cyclic", "wide"])
def test_stage_constant_past_the_plain_text_limits_is_an_input_validation_refusal(kind):
    value, said = _past_plain_text_limit(kind)
    got = compose_reply({"stages": [{"component": "grounding", "op": "check_claims",
                                     "params": {"text": "The limit is 42 units.", "sources": value}}], "inputs": {}})
    assert got["ok"] is False
    assert got["completion_state"] == "blocked-escalated"
    assert got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION", got["error"]
    assert "stage 0: sources must be JSON data" in got["error"] and said in got["error"]


def test_a_very_deep_json_constant_through_g6_compose_is_the_refusal_envelope():
    import asyncio

    from g6.mcp import build_server
    handler = asyncio.run(build_server().get_tools())["g6_compose"].fn
    deep = "[" * 600 + '"x"' + "]" * 600
    got = asyncio.run(handler(composition_json='{"stages": [{"component": "grounding", "op": "check_claims", '
                                               '"params": {"text": "The limit is 42 units.", "sources": ' + deep +
                                               '}}], "inputs": {}}'))
    assert got["ok"] is False
    assert got["warning_card"]["code"] == "G6_E_INPUT_VALIDATION", got["error"]
    assert "nested deeper than" in got["error"]


@pytest.mark.parametrize("where", ["input", "constant"])
@pytest.mark.parametrize("kind", ["deep", "wide"])
def test_stage_data_at_the_plain_text_limits_is_not_refused_by_them(where, kind):
    from g6.core.external_data import MAX_PLAIN_VALUES
    value = "The limit is 42 units."
    if kind == "deep":
        for _ in range(64):
            value = [value]
    else:
        value = [value] * (MAX_PLAIN_VALUES - 1)   # the list and its items: exactly the values plain_text reads
    stage = {"component": "grounding", "op": "check_claims"}
    if where == "constant":
        stage["params"] = {"sources": value}
    got = compose_reply({"stages": [stage], "inputs": {"text": "The limit is 42 units.",
                                                         **({"sources": value} if where == "input" else {})}})
    assert "must be JSON data" not in str(got.get("error"))


def test_contract_survives_serialization():
    from g6.core.block_contract import contract_from_dict, contract_to_dict
    from g6.grounding.grounding_block import GroundingBlock
    contract = GroundingBlock().contract
    restored = contract_from_dict(contract_to_dict(contract))
    assert restored.outputs_per_op == contract.outputs_per_op
    assert restored.evidence_outputs_per_op == contract.evidence_outputs_per_op
