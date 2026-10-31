# SPDX-License-Identifier: Apache-2.0
"""goal_engine's block (P1, PR #5): a goal tree proposed by the model the caller configured, through the one prompt
catalogue, bounded at 100 model calls, and a qualified draft at best; a reply of the wrong shape is an error, never a
subgoal or a state."""
from __future__ import annotations

import json

import pytest

from g6.core import discovery
from g6.core.llm import FakeLLM
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply

REPLY = json.dumps({"subgoals": [{"goal": "write tests", "success_criteria": [], "required_evidence": []},
                                 {"goal": "implement", "success_criteria": [], "required_evidence": []}],
                    "dependencies": [], "validation_gaps": []})


def test_a_model_proposes_and_the_tree_stays_a_qualified_draft():
    llm = FakeLLM(lambda s, u: REPLY)
    got = get_registry().invoke("goal_engine", "propose_goal_tree",
                                {"objective": "ship", "max_depth": 1, "max_model_calls": 1}, llm=llm).unwrap()
    assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_GOAL_PROPOSED"
    assert [n["node_id"] for n in got["nodes"]] == ["root", "root.0", "root.1"] and got["model_calls"] == len(llm.calls) == 1
    assert "ship" in llm.calls[0][1] and "{{" not in llm.calls[0][1]
    assert check_envelope(got) == []


def test_with_no_model_it_refuses_and_proposes_nothing(monkeypatch):
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    reply = invoke_reply("goal_engine", "propose_goal_tree", json.dumps({"objective": "ship"}))
    assert reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == "G6_E_GOAL_NO_MODEL"
    assert check_envelope(reply) == []


def test_the_call_bound_is_100():                                       # Review Focus 5
    reply = invoke_reply("goal_engine", "propose_goal_tree", json.dumps({"objective": "g", "max_model_calls": 101}))
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    llm = FakeLLM(lambda s, u: json.dumps({"subgoals": [{"goal": f"g{len(llm.calls)}.{i}"} for i in range(12)]}))
    got = get_registry().invoke("goal_engine", "propose_goal_tree", {
        "objective": "g", "branching_factor": 12, "max_depth": 8, "max_nodes": 1000, "max_model_calls": 100},
        llm=llm).unwrap()
    assert got["model_calls"] == len(llm.calls) <= 100


@pytest.mark.parametrize("answer", ["not JSON", "x" * 40_001, json.dumps({"subgoals": "ship"}),
                                    json.dumps({"completion_state": "verified", "subgoals": []})])
def test_a_reply_of_the_wrong_shape_is_an_error_never_a_subgoal_or_a_state(answer):
    got = get_registry().invoke("goal_engine", "propose_goal_tree", {"objective": "ship"},
                                llm=FakeLLM(default=answer)).unwrap()
    assert got["completion_state"] == "blocked-escalated" and [n["node_id"] for n in got["nodes"]] == ["root"]


def test_it_is_a_beta_block_with_one_op_and_nothing_sensitive():
    info = discovery.describe("goal_engine")
    assert (info["block"], info["maturity"], info["ops"], info["sensitive_ops"]) == \
        ("GoalEngineBlock", "beta", ["propose_goal_tree"], [])
