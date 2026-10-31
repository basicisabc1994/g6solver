# SPDX-License-Identifier: Apache-2.0
"""Discovery, CLI and MCP surface: honest ranking, fail-closed invoke, no label upgrades."""
from __future__ import annotations

import asyncio
import json

import pytest

from g6.cli import main as cli
from g6.core import discovery


def test_find_ranks_by_self_description_and_returns_scores() -> None:
    hits = discovery.find("gate an agent action before execution")
    assert hits and hits[0]["component"] == "action_gating"
    assert all(h["score"] > 0 and h["matched"] for h in hits)


def test_improvement_intents_point_to_the_workflow_loop():
    assert discovery.find("make my agent better at classification")[0]["component"] == "metaharness"
    assert "metaharness_start" in discovery.HELP and "g6_compose" in discovery.HELP


def test_what_a_component_did_not_port_is_not_ranked() -> None:
    """A ported component's "Not ported from the archive" list names what it does NOT offer. The solver's mention of
    "customer scoping" there once outranked action_gating for "should my agent send this email to a customer"."""
    for name in discovery.get_registry().names():
        assert discovery._NOT_OFFERED not in discovery._profile(name), name
    assert "customer" not in discovery._tokens(discovery._profile("solver"))
    assert discovery.find("should my agent send this email to a customer")[0]["component"] == "action_gating"


def test_find_returns_nothing_for_unrelated_words() -> None:
    assert discovery.find("zzzz qqqq") == []
    assert discovery.find("") == []


def test_describe_lists_ops_and_dependencies() -> None:
    info = discovery.describe("action_gating")
    assert info["available"] is True
    assert info["ops"] == ["evaluate", "explain", "budget_check", "budget_record", "budget_report", "budget_policy"]
    assert set(info["requires"]) == {"action_logic", "epistemic_integrity", "csf", "evidence"}
    assert discovery.describe("no_such_thing") == {"name": "no_such_thing", "available": False}


@pytest.mark.parametrize(("query", "component"), [
    ("a fix made for a failure nobody reproduced", "workforce"),
    ("taking one model's first answer unchallenged", "debate"),
    ("a design that fails only in a rare order of events nobody tried", "model_checking"),
    ("a plan that fails halfway through", "navigator"),
    ("made-up facts and quotes", "grounding"),
    ("a proof that only looks finished", "lean"),
])
def test_find_reaches_a_component_by_the_failure_mode_it_declares(query: str, component: str) -> None:
    # D6: an agent finds a guard by the failure it is worried about. Before the profile carried the titles of the
    # declared modes, each of these ranked another component first (grounding was not even in the first three for "made-up facts").
    assert discovery.find(query)[0]["component"] == component


def test_describe_returns_the_failure_modes_a_component_guards_against_and_can_cause() -> None:
    info = discovery.describe("workforce")
    assert {"code": "moved_goalposts", "title": "moving the goalposts after the work is done"} in info["mitigates"]
    assert [m["code"] for m in info["introduces"]] == ["runner_in_process", "live_working_tree"]


def test_cli_invoke_and_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(["invoke", "action_gating", "evaluate", json.dumps({"action": {"action_type": "read_file"}})]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["completion_state"] != "verified"
    assert cli(["invoke", "action_gating", "no_such_op"]) == 1
    assert "[ACTION_GATING_ERROR]" in capsys.readouterr().err


def test_mcp_server_exposes_only_the_discovery_surface() -> None:
    pytest.importorskip("fastmcp")
    from g6.mcp import build_server

    server = build_server()
    tools = asyncio.run(server.get_tools())
    # the discovery surface, plus the meta-harness tools added in its Phase 6 (META_HARNESS_PLAN.md; owner ruling Q10)
    # and its recovery tool (DECISIONS #27), and the coding mission (``g6.mcp.mission``)
    assert set(tools) == {"g6_help", "g6_components", "g6_find", "g6_describe", "g6_invoke", "g6_compose", "g6_review_patch",
                          "g6_premortem", "g6_mission_create", "g6_mission_clarify", "g6_mission_confirm",
                          "g6_mission_submit", "g6_mission_status", "g6_mission_certificate",
                          "metaharness_start", "metaharness_confirm", "metaharness_propose_code",
                          "metaharness_evaluate_start", "metaharness_job", "metaharness_run",
                          "metaharness_inspect", "metaharness_propose", "metaharness_evaluate", "metaharness_decide",
                          "metaharness_diagnose", "metaharness_approve", "metaharness_rollback"}
