# SPDX-License-Identifier: Apache-2.0
"""self_maintenance's block over MCP (P1, PR #7): a caller's stage measurements name a constraint but stay the
caller's word, an op with no parameters is refused with its own code, and nothing calls itself "experimental"."""
from __future__ import annotations

import json

import pytest

import g6.self_maintenance
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.self_maintenance import SelfMaintenanceBlock

STAGES = [{"name": "review", "attempted": 20, "succeeded": 10, "capacity_per_day": 5, "independently_observed": True},
          {"name": "coding", "attempted": 20, "succeeded": 19, "capacity_per_day": 20, "independently_observed": True}]
TICKET = {"id": "issue-1", "title": "Fix a user-visible error", "evidence": "failing regression fixture",
          "files": ["src/g6/code/swe_diagnostics/read.py"], "tests": ["tests/code/test_swe_diagnostics.py"],
          "estimated_tokens": 6000}


def test_a_callers_measurements_name_a_constraint_but_stay_the_callers_word():          # Review Focus 4
    r = invoke_reply("self_maintenance", "identify_constraint", json.dumps({"stages": STAGES}))
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_SELF_MAINTENANCE_CONSTRAINT"
    assert r["result"]["constraint"] == "review" and r["result"]["evidence"][0]["declared_by"] == "caller"
    assert check_envelope(r) == []


def test_a_plan_is_a_simulation_that_reserves_nothing():
    params = {"stages": STAGES, "tickets": [TICKET], "ticket_stages": {"issue-1": "review"}, "day": "2026-10-29"}
    r = invoke_reply("self_maintenance", "plan", json.dumps(params))
    assert r["warning_card"]["code"] == "G6_E_SELF_MAINTENANCE_PLAN" and r["result"]["selected"] == "issue-1"
    assert r["completion_state"] in ("qualified-draft", "blocked-escalated") and check_envelope(r) == []


@pytest.mark.parametrize("op", ["identify_constraint", "plan"])
def test_no_parameters_is_refused_with_its_own_code(op):
    reply = invoke_reply("self_maintenance", op, "{}")
    assert reply["warning_card"]["code"] == "G6_E_SELF_MAINTENANCE_INPUT"
    assert reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []


def test_it_is_a_beta_block_that_never_says_experimental():
    info = discovery.describe("self_maintenance")
    assert (info["block"], info["maturity"], info["sensitive_ops"]) == ("SelfMaintenanceBlock", "beta", [])
    assert sorted(info["ops"]) == ["identify_constraint", "plan"]
    assert "experimental" not in (g6.self_maintenance.__doc__ + maturity_reason(SelfMaintenanceBlock)).lower()
    assert not hasattr(g6.self_maintenance, "__g6_maturity__")
