# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Degradation, QA, and audit tests for action_gating remediation."""
from __future__ import annotations

import os

from pathlib import Path
from uuid import uuid4

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.decision_engine import evaluate_action
from g6.action_gating.token_store import ActionGatingStore
from g6.action_gating.native_ops import execute_backend_op, issue_gate_token
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.qa import run_safety_qa


def _db_path() -> str:
    root = Path(os.environ["G6_HOME"]) / "test_dbs"  # isolated by tests/conftest.py
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{uuid4().hex}.db")


def test_degradation_notice_required_for_missing_audit_logger():
    capsule = normalize_action_capsule(
        {
            "action_type": "account_deletion",
            "target_backend": "billing",
            "target_resource": "account-123",
            "irreversible_or_costly_to_reverse": True,
        }
    )
    result = evaluate_action(capsule, audit_logger=ActionGateAuditLogger(available=False))

    assert result.gate_decision.completion_state == "blocked-escalated"
    assert any("audit_logger" in notice.missing_dependencies for notice in result.degradation)
    assert result.gate_decision.warning_card["user_visible_message"]


def test_audit_failure_blocks_irreversible_action():
    capsule = normalize_action_capsule(
        {
            "action_type": "payment",
            "target_backend": "payments",
            "target_resource": "invoice-1",
            "external_side_effect": True,
            "irreversible_or_costly_to_reverse": True,
        }
    )
    logger = ActionGateAuditLogger(available=True, fail_writes=True)
    result = evaluate_action(capsule, audit_logger=logger)

    assert result.gate_decision.decision == "block"
    assert result.gate_decision.completion_state == "blocked-escalated"
    assert "G6_E_AUDIT_WRITE_FAILED" in result.gate_decision.reason_codes


def test_qa_failure_blocks_execution():
    capsule = normalize_action_capsule(
        {
            "action_type": "account_deletion",
            "target_backend": "billing",
            "target_resource": "account-123",
            "irreversible_or_costly_to_reverse": True,
        }
    )
    qa = run_safety_qa(
        capsule,
        mitigation_plan=None,
        degradation=[],
        required_confirmation_phrase=None,
    )

    assert qa.mitigation_check_passed is False
    assert qa.required_fixes_before_execution


def test_native_op_cannot_bypass_global_safety_guards():
    capsule = normalize_action_capsule(
        {
            "action_type": "permission_escalation",
            "target_backend": "iam",
            "target_resource": "admin-role",
            "external_side_effect": True,
            "action_params": {"target_resource": "admin-role", "external_side_effect": True},
        }
    )
    token = issue_gate_token(capsule)

    result = execute_backend_op(
        backend_id="iam",
        op_name="permission_escalation",
        # Same declared action as the token (the full-scope hash refuses any difference earlier).
        args={"target_resource": "admin-role", "external_side_effect": True},
        gate_token=token.token,
        global_safety_approved=False,
    )

    assert result["completion_state"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_DURABLE_POLICY_DENIED"


def test_every_evaluated_action_has_required_envelope_fields():
    result = evaluate_action({"action_type": "write_file", "target_resource": "scratch.txt"})
    payload = result.to_response_dict()

    assert payload["action_capsule"]
    assert payload["gate_decision"]
    assert payload["completion_state"] in {"verified", "qualified-draft", "blocked-escalated"}
    assert payload["warning_card"]
    assert payload["evidence"]
    assert payload["request_id"]
    assert payload["run_id"]


#: A file the gate can read, standing for the backup a write is undone from (ACTION_GATING-01: without one a write's
#: reversibility is unknown and no token is authorised).
_WRITE = {"path": "scratch.txt", "checkpoint": str(Path(__file__).resolve())}


def test_native_execute_defaults_fail_closed_without_explicit_approval():
    # The global-policy bridge is external to this component: omitting
    # global_safety_approved/audit_available must BLOCK, never implicitly approve.
    token = issue_gate_token({
        "action_type": "write_file",
        "target_backend": "local",
        "action_params": dict(_WRITE),
        **_WRITE,
    })
    result = execute_backend_op(
        "local", "write_file", dict(_WRITE), token.token,
    )
    assert result["completion_state"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_GLOBAL_SAFETY_BLOCKED"


def test_native_execute_requires_durable_policy_and_audit_binding():
    store = ActionGatingStore(_db_path())
    token = issue_gate_token(
        {
            "action_type": "write_file",
            "target_backend": "local",
            "action_params": {"path": "scratch.txt"},
            "path": "scratch.txt",
        },
        store=store,
    )
    store._conn.execute(  # noqa: SLF001 - corrupt durable audit binding for fail-closed proof
        "DELETE FROM action_gate_audit_records WHERE gate_token=?",
        (token.token,),
    )
    store._conn.commit()  # noqa: SLF001

    result = execute_backend_op(
        "local",
        "write_file",
        {"path": "scratch.txt"},
        token.token,
        global_safety_approved=True,
        audit_available=True,
        store=store,
    )

    assert result["completion_state"] == "blocked-escalated"
    assert result["reliability_label"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_DURABLE_AUDIT_REQUIRED"
    assert result["warning_card"]
    assert result["request_id"]
    assert result["run_id"]


def test_simulated_native_execution_is_never_verified():
    store = ActionGatingStore(_db_path())
    token = issue_gate_token(
        {
            "action_type": "write_file",
            "target_backend": "local",
            "action_params": dict(_WRITE),
            **_WRITE,
        },
        store=store,
    )

    result = execute_backend_op(
        "local",
        "write_file",
        dict(_WRITE),
        token.token,
        global_safety_approved=True,
        audit_available=True,
        store=store,
    )

    assert result["authorization"]["completion_state"] == "qualified-draft"  # AG-007: policy pass, not verification
    assert result["authorization"]["status"] == "authorized"
    assert result["completion_state"] == "qualified-draft"
    assert result["reliability_label"] == "qualified-draft"
    assert result["executed"] is False
    assert result["action_result"]["status"] == "simulated"
