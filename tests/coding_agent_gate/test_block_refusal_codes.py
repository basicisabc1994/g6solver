# SPDX-License-Identifier: Apache-2.0
"""The block's door: a refusal carries a stable G6_E_ code, not the component's own prefix (conformance kit, D4)."""
from __future__ import annotations

import pytest

from g6.mcp import invoke_reply


@pytest.mark.parametrize("op", ["preflight", "verify_answer"])
def test_a_task_left_out_is_a_coded_refusal(op: str) -> None:
    reply = invoke_reply("coding_agent_gate", op, "{}")
    assert reply["ok"] is False
    assert reply["error"].startswith("[G6_E_CODING_AGENT_GATE_TASK_REQUIRED]"), reply["error"]
    assert reply["warning_card"]["code"] == "G6_E_CODING_AGENT_GATE_TASK_REQUIRED"
