# SPDX-License-Identifier: Apache-2.0
"""The compact reply never cuts an integrity reason (the security review of W1-W5, F6).

W2's compact reply shortened every list under its hardest cut, the reasons included, so a review with many reasons
could lose its integrity reasons, the ones that come before every other finding. They are now kept whole and first;
only the others are shortened. Offline: the reply is built from a fixture, nothing runs.
"""
from __future__ import annotations

import json
from pathlib import Path

from g6.mcp.review_reply import compact

FIXTURE = Path(__file__).with_name("data") / "worked_example_review_reply.json"


def _huge() -> dict:
    reply = json.loads(FIXTURE.read_text(encoding="utf-8"))
    integrity = [{"stage": "integrity", "code": code, "detail": f"{code}: " + "i" * 1_500}
                 for code in ("test_weakened", "runner_configuration_changed", "ignored_file_can_change_tests")]
    evidence = [{"stage": "evidence", "code": f"code_{i}", "detail": "e" * 5_000} for i in range(100)]
    reply["assessment"]["reasons"] = [*evidence[:50], *integrity, *evidence[50:]]
    reply["assessment"]["next_actions"] = [f"action {i}: " + "y" * 5_000 for i in range(100)]
    reply["assessment"]["unknowns"] = [f"unknown {i}: " + "z" * 5_000 for i in range(100)]
    return reply


def test_every_integrity_reason_stays_whole_and_first_under_the_hardest_cut():
    reply = _huge()
    shown = compact(reply, "job", None)
    reasons = shown["assessment"]["reasons"]
    integrity = [r for r in reply["assessment"]["reasons"] if r["stage"] == "integrity"]
    assert reasons[:3] == integrity                                     # whole, in their order, before the rest
    assert all(r["stage"] != "integrity" for r in reasons[3:])
    assert all(len(r["detail"]) < 5_000 for r in reasons[3:])            # the others are shortened
    assert "assessment.reasons" in shown["full_assessment"]["shortened"]
    assert len(json.dumps(shown)) < 30_000
