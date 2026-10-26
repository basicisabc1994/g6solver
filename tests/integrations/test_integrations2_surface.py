# SPDX-License-Identifier: Apache-2.0
"""Family declarations, discovery, skills and the sensitive door, without extras or services."""
import importlib
import json

import pytest

from g6.core import discovery, skills
from g6.core.failure_modes import FailureModeRegistry
from g6.core.registry import get_registry
from g6.mcp import describe_reply, invoke_reply


@pytest.mark.parametrize("name,mode", [("adapt_rq", "unseen_job_failure"), ("hat_orchestrator", "unclear_authority")])
def test_declarations_and_mcp_surface(name, mode):
    package = importlib.import_module(f"g6.integrations.{name}")
    info = describe_reply(name)
    assert info["family"] == "integrations"
    assert package.__g6_mitigates__ == (mode,)
    assert all(FailureModeRegistry.is_registered(code) for code in package.__g6_mitigates__ + package.__g6_introduces__)
    assert f"g6.integrations.{name}" not in FailureModeRegistry.planned()
    assert sorted(skills.skill_ops(info["skill"])) == sorted(info["ops"])
    assert name in {hit["component"] for hit in discovery.find(mode)}
    assert invoke_reply(name, "ops", "{}")["ok"]
    assert invoke_reply(name, "help", "{}")["ok"]
    for op in info["sensitive_ops"]:
        result = get_registry().invoke(name, op, {}, block_sensitive=True)
        assert "G6_E_SENSITIVE_COMPONENT_DENIED" in str(result.failure())
    for op, params in skills.skill_examples(info["skill"]):
        if op not in info["sensitive_ops"]:
            reply = invoke_reply(name, op, json.dumps(params))
            assert reply["ok"] and reply["completion_state"] in {"qualified-draft", "blocked-escalated"}
    # Removal of declarations, discovery metadata or sensitive-op guards makes this test fail.
