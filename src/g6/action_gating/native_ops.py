# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Explicit, constrained backend-native action-gating operations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
import secrets
from uuid import uuid4

from g6.action_gating.decision_engine import evaluate_action
from g6.action_gating.token_store import ActionGatingStore
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.schema import ActionCapsule, BackendNativeOpDescriptor


@dataclass(frozen=True)
class GateToken:
    token: str
    action_capsule_hash: str
    expires_at: str


_TOKENS: dict[str, GateToken] = {}
_STORE: ActionGatingStore | None = None


# Fields that change what an authorised action does or who it acts for. A token binds all of
# them (audit 002, AG-001): the archive hashed only type/backend/resource, so one token for
# "write 'safe' to scratch.txt" also authorised writing anything else to that file.
_SCOPE_FIELDS = (
    "action_type", "target_backend", "target_resource", "normalized_inputs", "requested_by", "actor_type",
    "required_authority", "observed_authority", "reversibility", "blast_radius", "real_world_effect",
    "external_side_effect", "irreversible_or_costly_to_reverse",
)
_SCOPE_VERSION = "g6.action_scope.v2"
# Keys in caller-supplied args that would override the operation's identity (AG-002).
RESERVED_ARGS = frozenset({"action_type", "target_backend", "action_params", "op_name", "op", "args"})


def _capsule_hash(capsule: ActionCapsule) -> str:
    dumped = capsule.model_dump(mode="json")
    payload = {"v": _SCOPE_VERSION, **{k: dumped.get(k) for k in _SCOPE_FIELDS}}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(blob.encode("utf-8")).hexdigest()


def _backend_action(backend_id: str, op_name: str, args: dict) -> dict:
    """The action dict for a backend call; identity fields are set last so args cannot shadow them."""
    return {**args, "action_type": op_name, "target_backend": backend_id, "action_params": dict(args)}


def _reserved_in(args: dict) -> list[str]:
    return sorted(RESERVED_ARGS & set(args or {}))


def _get_store(store: ActionGatingStore | None = None) -> ActionGatingStore:
    global _STORE
    if store is not None:
        return store
    if _STORE is None:
        _STORE = ActionGatingStore(os.getenv("ACTION_GATING_STORE_PATH"))
    return _STORE


def _envelope(
    completion_state: str,
    *,
    error_code: str | None = None,
    message: str = "",
    evidence: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    request_id = "req-" + uuid4().hex[:12]
    run_id = "run-" + uuid4().hex[:12]
    warning_card = {
        "completion_state": completion_state,
        "degradation_level": "blocked" if completion_state == "blocked-escalated" else "warning",
        "user_visible_message": message,
        "degraded": completion_state != "verified",
        "not_verified": [error_code] if error_code else [],
    }
    payload = {
        "completion_state": completion_state,
        "reliability_label": completion_state,
        "warning_card": warning_card,
        "evidence": evidence or [],
        "request_id": request_id,
        "task_id": "",
        "run_id": run_id,
    }
    if error_code:
        payload["error"] = {"code": error_code, "message": message}
    if extra:
        payload.update(extra)
    return payload


# The only native ops this component describes, each for its own backend (all simulated).
_NATIVE_OPS = (
    BackendNativeOpDescriptor(
        backend_id="local",
        op_name="write_file",
        description="Write a local file through an external executor after gating.",
        side_effect_type="local_write",
        reversibility="fully_reversible",
        requires_confirmation=False,
        supports_dry_run=True,
        supports_rollback=True,
        required_permissions=["filesystem_write"],
        required_dependencies=["audit_logger"],
        risk_domains=["data_integrity"],
        safety_notes=["Execution is simulated by action_gating; use a separate executor for real writes."],
    ),
    BackendNativeOpDescriptor(
        backend_id="iam",
        op_name="permission_escalation",
        description="Change permissions or roles.",
        side_effect_type="external_write",
        reversibility="partially_reversible",
        requires_confirmation=True,
        supports_dry_run=True,
        supports_rollback=False,
        required_permissions=["iam_admin"],
        required_dependencies=["audit_logger", "global_policy_bridge"],
        risk_domains=["security", "identity"],
        safety_notes=["Requires global safety approval and cannot execute under degraded assurance."],
    ),
)


def discover_backend_ops(backend_id: str = "local") -> list[BackendNativeOpDescriptor]:
    """The simulated native ops described for ``backend_id``; an undescribed backend has none.

    The archive returned both illustrative ops for any backend id, so discovery advertised
    support that nothing provides (audit 002, AG-010).
    """
    return [op for op in _NATIVE_OPS if op.backend_id == backend_id]


def describe_backend_op(backend_id: str, op_name: str) -> BackendNativeOpDescriptor | None:
    for op in discover_backend_ops(backend_id):
        if op.op_name == op_name:
            return op
    return None


def dry_run_backend_op(backend_id: str, op_name: str, args: dict) -> dict:
    descriptor = describe_backend_op(backend_id, op_name)
    return {
        "backend_id": backend_id,
        "op_name": op_name,
        "args": args,
        "op": descriptor.model_dump(mode="json") if descriptor else None,
        "would_execute": False,
        "completion_state": "qualified-draft",
        "reliability_label": "qualified-draft",
        "warning_card": {
            "completion_state": "qualified-draft",
            "degradation_level": "warning",
            "user_visible_message": "Dry run is simulated; no backend action was executed.",
            "degraded": True,
            "not_verified": ["simulated_execution"],
        },
        "evidence": [{"kind": "simulation", "status": "dry_run"}],
        "request_id": "req-" + uuid4().hex[:12],
        "task_id": "",
        "run_id": "run-" + uuid4().hex[:12],
        "degradation": [],
    }


def evaluate_backend_op(backend_id: str, op_name: str, args: dict) -> dict:
    reserved = _reserved_in(args)
    if reserved:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_RESERVED_ARGUMENT",
            message=f"Arguments may not set the operation identity: {', '.join(reserved)}.",
        )
    return evaluate_action(_backend_action(backend_id, op_name, args)).to_response_dict()


def issue_gate_token(capsule: ActionCapsule | dict, store: ActionGatingStore | None = None) -> GateToken:
    normalized = normalize_action_capsule(capsule)
    token = "agt-" + secrets.token_hex(16)
    expires_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    evaluation = evaluate_action(normalized)  # the durable default audit trail (AG-009)
    decision = evaluation.gate_decision.model_dump(mode="json")
    audit_record = (
        evaluation.audit_record.model_dump(mode="json")
        if evaluation.audit_record
        else {
            "audit_id": evaluation.gate_decision.audit_id,
            "action_id": normalized.action_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "action_capsule": normalized.model_dump(mode="json"),
            "decision": evaluation.gate_decision.decision,
            "reason_codes": evaluation.gate_decision.reason_codes,
            "gate_token_issued": True,
            "gate_token_id": token,
            "completion_state": evaluation.gate_decision.completion_state,
        }
    )
    gate = GateToken(
        token=token,
        action_capsule_hash=_capsule_hash(normalized),
        expires_at=expires_at,
    )
    _get_store(store).persist_gate_authorization(
        gate_token=token,
        action_capsule_hash=gate.action_capsule_hash,
        action_capsule=normalized.model_dump(mode="json"),
        decision=decision,
        audit_record=audit_record,
        expires_at=expires_at,
    )
    _TOKENS[token] = gate
    return gate


def execute_backend_op(
    backend_id: str,
    op_name: str,
    args: dict,
    gate_token: str,
    global_safety_approved: bool = False,
    audit_available: bool = False,
    store: ActionGatingStore | None = None,
) -> dict:
    """Simulated execution of a gated backend op; nothing is executed.

    ``global_safety_approved`` and ``audit_available`` are the caller's own statements (audit
    002, AG-008). ``False`` refuses. ``True`` is not an authenticated approval — this component
    has no global-policy bridge — so the reply records it as caller-declared and never grants
    live-execution authority. Audit health is checked here: the token's durable audit record
    must exist and the single-use consume must commit.
    """
    reserved = _reserved_in(args)
    if reserved:
        # Refused before the token is looked up, so a shadowing attempt cannot consume it.
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_RESERVED_ARGUMENT",
            message=f"Arguments may not set the operation identity: {', '.join(reserved)}.",
        )
    if not gate_token:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_REQUIRED",
            message="A gate token is required before native execution can be considered.",
        )
    try:
        durable = _get_store(store).load_gate_authorization(gate_token)
    except Exception:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_DURABLE_POLICY_AUDIT_UNAVAILABLE",
            message="Durable policy and audit records are unavailable; action_gating fails closed.",
        )
    if durable is None:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_INVALID",
            message="Gate token is not backed by a durable policy/audit record.",
        )
    if durable.get("audit_record") is None:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_DURABLE_AUDIT_REQUIRED",
            message="Gate token has no durable audit record; execution is blocked.",
        )
    if durable.get("consumed_at"):
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_INVALID",
            message="Gate token has already been consumed.",
        )
    if datetime.fromisoformat(str(durable["expires_at"])) < datetime.now(UTC):
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_EXPIRED",
            message="Gate token has expired.",
            evidence=[{"kind": "durable_policy", "audit_id": durable.get("audit_id")}],
        )
    capsule = normalize_action_capsule(_backend_action(backend_id, op_name, args))
    if _capsule_hash(capsule) != durable["action_capsule_hash"]:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_SCOPE",
            message="Gate token is not scoped to this action capsule.",
            evidence=[{"kind": "durable_policy", "audit_id": durable.get("audit_id")}],
        )
    if not durable.get("authorized"):
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_DURABLE_POLICY_DENIED",
            message="Durable policy decision did not authorize this action.",
            evidence=[
                {
                    "kind": "durable_policy",
                    "audit_id": durable.get("audit_id"),
                    "completion_state": durable.get("completion_state"),
                    "decision": durable.get("decision"),
                }
            ],
        )
    if not global_safety_approved:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GLOBAL_SAFETY_BLOCKED",
            message="Global safety approval is required and was not present.",
            evidence=[{"kind": "durable_policy", "audit_id": durable.get("audit_id")}],
        )
    if not audit_available:
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_AUDIT_REQUIRED",
            message="Audit availability was not present at execution time.",
            evidence=[{"kind": "durable_policy", "audit_id": durable.get("audit_id")}],
        )
    if not _get_store(store).consume_gate_token(gate_token):
        return _envelope(
            "blocked-escalated",
            error_code="G6_E_GATE_TOKEN_INVALID",
            message="Gate token could not be consumed exactly once.",
        )
    _TOKENS.pop(gate_token, None)
    return _envelope(
        "qualified-draft",
        message=(
            "Native execution remains simulated and externally constrained; the global safety "
            "approval was declared by the caller and is not authenticated."
        ),
        evidence=[
            {"kind": "durable_policy", "audit_id": durable.get("audit_id"), "completion_state": durable.get("completion_state")},
            {"kind": "durable_audit", "audit_id": durable.get("audit_id")},
            {"kind": "global_safety_approval", "source": "caller", "authenticated": False},
            {"kind": "simulation", "executed": False},
        ],
        extra={
            "executed": False,
            "authorization": {
                "completion_state": durable.get("completion_state"),
                "status": "authorized",
                "global_safety_approval": "caller_declared_unauthenticated",
            },
            "action_result": {"status": "simulated", "backend_id": backend_id, "op_name": op_name},
        },
    )
