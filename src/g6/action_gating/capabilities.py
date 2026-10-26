# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Action-gating capability discovery and degradation normalization."""
from __future__ import annotations

from g6.action_gating.schema import ActionGatingCapabilities, DegradationNotice


def build_degradation_notice(
    missing: list[str] | None = None,
    unavailable: list[str] | None = None,
    severity: str = "warning",
    message: str = "",
    blocks_execution: bool = False,
) -> DegradationNotice:
    missing = missing or []
    unavailable = unavailable or []
    if not missing and not unavailable:
        return DegradationNotice(user_visible_message=message or "Full action-gating assurance is available.")
    return DegradationNotice(
        degraded=True,
        severity="blocked" if blocks_execution else severity,  # type: ignore[arg-type]
        missing_dependencies=missing,
        unavailable_capabilities=unavailable,
        affected_features=unavailable or missing,
        impact_on_assurance=[
            "Cannot claim full assurance while required action-gating checks are unavailable."
        ],
        user_visible_message=message or "Action-gating assurance is degraded; execution may be blocked.",
        blocks_execution=blocks_execution,
    )


def degradation_level(degradation: list[DegradationNotice]) -> str:
    order = {"none": 0, "info": 1, "warning": 2, "unsafe": 3, "blocked": 4}
    level = "none"
    for notice in degradation:
        if order[notice.severity] > order[level]:
            level = notice.severity
    return level


def discover_capabilities(audit_available: bool = True) -> ActionGatingCapabilities:
    """What this component actually provides (audit 002, AG-010).

    The policy is local and deterministic: there is no global-policy bridge, and native ops
    are simulated (``live_native_execution`` is false). ``supported_backends`` lists only the
    backends ``native_ops`` describes an op for.
    """
    degradation: list[DegradationNotice] = []
    if not audit_available:
        degradation.append(build_degradation_notice(
            missing=["audit_logger"],
            unavailable=["durable_audit_record"],
            severity="blocked",
            message="This action cannot proceed because required audit logging is unavailable.",
            blocks_execution=True,
        ))
    return ActionGatingCapabilities(
        capabilities={
            "action_normalization": True,
            "reversibility_classification": True,
            "impact_classification": True,
            "scenario_modelling": True,
            "mitigation_planning": True,
            "rumsfeld_uncertainty_mapping": True,
            "user_mental_model_estimation": True,
            "benign_safety_qa": True,
            "global_policy_bridge": False,
            "audit_logging": audit_available,
            "dry_run_support": True,
            "backend_native_ops": True,
            "live_native_execution": False,
        },
        supported_action_types=[
            "write_file", "run_code", "call_api", "send_email", "payment",
            "account_deletion", "production_infrastructure_change",
            "permission_escalation", "plc_write", "gcode_execute",
        ],
        supported_backends=["iam", "local"],  # the backends of native_ops._NATIVE_OPS
        degradation=degradation,
        safety_guards={
            "global_policy_enforced": False,
            "authz_required": True,
            "audit_required_for_high_risk": True,
            "native_ops_constrained": True,
        },
    )
