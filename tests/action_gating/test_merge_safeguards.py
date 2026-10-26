# SPDX-License-Identifier: Apache-2.0
"""Kernel merge: T-02, T-03, then T-01. Owner runs RED/GREEN."""
from dataclasses import FrozenInstanceError
from itertools import permutations
from pathlib import Path

import pytest

from g6.action_gating import evaluate_action
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.safeguards import InteractionContext, Interceptor, Outcome, Safeguard, fold
from g6.mcp import invoke_reply

ACTION = {"action_type": "read_file", "target_resource": "notes.txt"}


def gate(checks):
    return evaluate_action(ACTION, audit_logger=ActionGateAuditLogger(), safeguards=checks)


def test_t02_py002_empty_and_allow_never_verify():
    # Removal oracle: Interceptor.intercept's `if not outcomes` inactive refusal.
    empty = gate(Interceptor([]))
    assert empty.completion_state == "blocked-escalated"
    assert "G6_E_SAFEGUARDS_INACTIVE" in empty.gate_decision.reason_codes
    allowed = gate(Interceptor([Safeguard("present", lambda ctx: Outcome.ALLOW)]))
    assert allowed.gate_decision.decision == "allow"
    assert allowed.completion_state == "qualified-draft"


def test_t03_under_evidenced_answers_are_inconclusive():
    # Removal oracle: Safeguard.check's malformed-answer return Outcome.INCONCLUSIVE.
    result = Interceptor([Safeguard("shape", lambda ctx: None)]).intercept(InteractionContext("read_file"))
    assert result.outcome == Outcome.INCONCLUSIVE and result.evaluated_count == 1
    decision = gate(Interceptor([Safeguard("shape", lambda ctx: {})])).gate_decision
    assert decision.decision == "require_human_review" and decision.completion_state == "blocked-escalated"


def test_t01_py001_mcp_inactivity_is_explicit():
    # Removal oracle: evaluate_action's inactive safeguards evidence item.
    reply = invoke_reply("action_gating", "evaluate", '{"action":{"action_type":"read_file","target_resource":"notes.txt"}}')
    item = next(e for e in reply["result"]["evidence"] if e.get("kind") == "safeguards")
    assert item["evaluated_count"] == 0 and item["code"] == "G6_E_SAFEGUARDS_INACTIVE"


def test_d1_exception_blocks_and_block_dominates_review():
    # Removal oracle: Safeguard.check's exception return Outcome.BLOCK.
    def broken(ctx):
        raise LookupError("predicate unavailable")
    result = gate(Interceptor([Safeguard("broken", broken), Safeguard("review", lambda ctx: Outcome.HITL_REQUIRED)]))
    assert result.gate_decision.decision == "block"
    for values in permutations([Outcome.ALLOW, Outcome.WARN, Outcome.HITL_REQUIRED, Outcome.BLOCK]):
        assert fold(values) == Outcome.BLOCK
    with pytest.raises(ValueError, match="G6_E_SAFEGUARDS_INACTIVE"):
        fold([])


def test_shadow_is_explicit_and_never_loosens_gate(monkeypatch):
    # Removal oracle: InterceptionResult.apply's shadow candidate allow_with_notice.
    monkeypatch.setenv("G6_POKA_YOKE_SHADOW", "1")
    checks = [Safeguard("deny", lambda ctx: Outcome.BLOCK)]
    assert gate(Interceptor(checks)).gate_decision.decision == "block"
    shadow = gate(Interceptor(checks, shadow=True))
    assert shadow.gate_decision.decision == "allow_with_notice"
    held = evaluate_action({"action_type": "send_email"}, audit_logger=ActionGateAuditLogger(),
                           safeguards=Interceptor(checks, shadow=True))
    assert held.gate_decision.decision == "block"


def test_injected_sink_failure_blocks_and_records_no_parameters():
    # Removal oracle: Interceptor.intercept's audit-sink exception block.
    rows = []
    interceptor = Interceptor([Safeguard("ok", lambda ctx: Outcome.ALLOW)], sink=rows.append)
    interceptor.intercept(InteractionContext("read_file", params={"private": "do not retain"}))
    assert "do not retain" not in str(rows)
    def broken(row):
        raise OSError("disk full")
    result = gate(Interceptor(interceptor.safeguards, sink=broken))
    assert result.gate_decision.decision == "block"
    assert "G6_E_SAFEGUARDS_AUDIT_FAILED" in result.gate_decision.reason_codes


def test_context_is_frozen_and_nested_inputs_cannot_change():
    # Removal oracle: InteractionContext.__post_init__ freezes copied input containers.
    params = {"rows": [{"count": 1}]}
    context = InteractionContext("compute", params=params)
    params["rows"][0]["count"] = 2
    assert context.params["rows"][0]["count"] == 1
    with pytest.raises(TypeError):
        context.params["rows"][0]["count"] = 3
    with pytest.raises(FrozenInstanceError):
        context.operation = "other"


def test_py013_kernel_has_only_the_library_licence():
    # Removal oracle: safeguards.py's first-line Apache-2.0 SPDX declaration.
    import g6.action_gating.safeguards as kernel
    source = Path(kernel.__file__).read_text(encoding="utf-8")
    assert source.startswith("# SPDX-License-Identifier: Apache-2.0\n")
    assert "All rights reserved" not in source and "Proprietary" not in source


def test_interceptor_dependency_failure_blocks(monkeypatch):
    # Removal oracle: evaluate_action catches interceptor failures and emits G6_E_SAFEGUARDS_FAILED.
    interceptor = Interceptor([])
    def broken(ctx):
        raise OSError("interceptor unavailable")
    monkeypatch.setattr(interceptor, "intercept", broken)
    result = gate(interceptor)
    assert result.gate_decision.decision == "block"
    assert "G6_E_SAFEGUARDS_FAILED" in result.gate_decision.reason_codes


def test_d1_supplied_predicate_catches_repeated_bad_target():
    # Removal oracle: evaluate_action applies the interceptor result to its GateDecision.
    known_bad_targets = {"retired/old-settings.txt"}
    checks = Interceptor([Safeguard("stale-source", lambda ctx:
        Outcome.BLOCK if ctx.params["target_resource"] in known_bad_targets else Outcome.ALLOW)])
    for _ in range(2):
        result = evaluate_action({**ACTION, "target_resource": "retired/old-settings.txt"},
                                 safeguards=checks, audit_logger=ActionGateAuditLogger())
        assert result.gate_decision.decision == "block"
    assert gate(checks).gate_decision.decision == "allow"
