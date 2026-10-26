# SPDX-License-Identifier: Apache-2.0
"""The block's door: an unknown field is refused, and a refusal carries a stable G6_E_ code (conformance kit, D4)."""
from __future__ import annotations

import json

from g6.mcp import invoke_reply


def test_an_unknown_field_is_refused_not_dropped() -> None:
    reply = invoke_reply("epistemic_integrity", "adjudicate",
                         json.dumps({"message": "The build passed.", "criticality": "irreversible"}))
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_INPUT_VALIDATION]"), reply["error"]


def test_a_message_left_out_is_a_coded_refusal() -> None:
    reply = invoke_reply("epistemic_integrity", "adjudicate", "{}")
    assert reply["ok"] is False
    assert reply["error"].startswith("[G6_E_EPISTEMIC_INTEGRITY_MESSAGE_REQUIRED]"), reply["error"]
