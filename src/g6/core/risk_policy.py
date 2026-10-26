# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Epistemic risk policy gates for claim disposition decisions.

The R0-R3 x E0-E3 matrix is this module's code. It is never read from a document (the spec's
``g6_epistemic_policy.yaml`` is for people), and each read is a fresh copy, so neither an edited
file nor a caller holding the policy can change a gate.
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any

from g6.core.evidence import Disposition
from g6.core.evidence_grade import EvidenceGrade, e_level_from_grade

_DEFAULT_POLICY: dict[str, Any] = {
    "g6_epistemic_integrity": {
        "risk_policy": {
            "R0": {
                "minimum_evidence_grade": "E0",
                "allowed_dispositions": ["ALLOW_WITH_LABEL"],
            },
            "R1": {
                "minimum_evidence_grade": "E1",
                "allowed_dispositions": ["ALLOW", "ALLOW_WITH_LABEL", "ASK"],
            },
            "R2": {
                "minimum_evidence_grade": "E2",
                "allowed_dispositions": ["ALLOW", "ESCALATE", "BLOCK"],
            },
            "R3": {
                "minimum_evidence_grade": "E3",
                "require_human_approval": True,
                "require_rollback_or_interlock": True,
                "allowed_dispositions": ["ALLOW", "ESCALATE", "BLOCK"],
                "default_on_error": "BLOCK",
            },
        }
    }
}
_GRADE_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}


def _risk_policy(policy: dict[str, Any]) -> dict[str, Any]:
    root = policy.get("g6_epistemic_integrity", {})
    risk_policy = root.get("risk_policy", {})
    if isinstance(risk_policy, dict):
        return risk_policy
    return {}


def load_epistemic_policy() -> dict[str, Any]:
    """The G6 epistemic policy: a fresh copy of the built-in matrix (no file is read)."""
    return deepcopy(_DEFAULT_POLICY)


def minimum_grade_for(risk_level: str) -> str:
    policy = _risk_policy(load_epistemic_policy())
    configured = policy.get(risk_level, {})
    if isinstance(configured, dict):
        grade = configured.get("minimum_evidence_grade")
        if grade in _GRADE_ORDER:
            return str(grade)
    return "E3"


def grade_meets(actual_grade: str, required_grade: str) -> bool:
    actual = _GRADE_ORDER.get(actual_grade)
    required = _GRADE_ORDER.get(required_grade)
    if actual is None or required is None:
        return False
    return actual >= required


def _grade_floor_enabled() -> bool:
    """W4 kill-switch (default **OFF**; ``G6_RISK_POLICY_GRADE_FLOOR=1`` = enforce).

    OFF → behaviour unchanged; the certificate floor is computed only to shadow-log
    the would-tighten set. ON → when a certificate grade is supplied, the effective
    E-level is the MINIMUM of the independence E-level and the certificate E-level
    (down-only — it can only ever tighten a disposition, never newly-permit).
    """
    return os.environ.get("G6_RISK_POLICY_GRADE_FLOOR", "0").strip().lower() in {"1", "true", "yes", "on"}


def _min_e_level(a: str, b: str) -> str:
    """The stricter (lower) of two E-levels by ``_GRADE_ORDER``.

    An UNKNOWN grade is treated as strictest and propagates, so an unknown
    independence grade cannot be loosened into a valid one by the certificate
    (preserving the fail-closed BLOCK on unknown ``evidence_grade``).
    """
    ra = _GRADE_ORDER.get(a)
    rb = _GRADE_ORDER.get(b)
    if ra is None:
        return a
    if rb is None:
        return b
    return a if ra <= rb else b


def _shadow_log_w4_floor(
    risk_level: str, independence_e: str, certificate_e: str, floored_e: str
) -> None:
    """W4 measurement sink — record a would-tighten (flag OFF) WITHOUT changing behaviour."""
    logging.getLogger("g6.risk_policy").info(
        "w4_grade_floor component=adjudicator risk_level=%s independence_e=%s "
        "certificate_e=%s floored_e=%s flag_state=off",
        risk_level,
        independence_e,
        certificate_e,
        floored_e,
    )


def gate_decision(
    risk_level: str,
    evidence_grade: str,
    *,
    unresolved_conflict: bool = False,
    has_human_approval: bool = False,
    has_rollback: bool = False,
    certificate_grade: EvidenceGrade | None = None,
) -> Disposition:
    """Apply the G6-EI-001 section 9.3 gate matrix to a claim.

    Unknown inputs are treated as highest risk and lowest evidence, so the gate
    fails closed.

    W4: ``certificate_grade`` (kernel/instrument/model, or ``None`` = axis not
    asserted) floors the effective E-level at the certificate axis, down-only. It
    is ``None`` whenever no verification method was declared — in that case the
    certificate axis is NOT applied (absence must never tighten). Behaviour is
    byte-identical when it is ``None`` or the ``G6_RISK_POLICY_GRADE_FLOOR`` flag
    is OFF.
    """
    # W4: floor the effective E-level at the certificate axis, down-only.
    effective_grade = evidence_grade
    if certificate_grade is not None:
        cert_e = e_level_from_grade(certificate_grade)
        floored = _min_e_level(evidence_grade, cert_e)
        if _grade_floor_enabled():
            effective_grade = floored
        elif floored != evidence_grade:
            # would-tighten under W4 — shadow-log, change nothing (flag OFF)
            _shadow_log_w4_floor(risk_level, evidence_grade, cert_e, floored)

    policy = _risk_policy(load_epistemic_policy())
    if risk_level not in policy or effective_grade not in _GRADE_ORDER:
        return "BLOCK"

    required_grade = minimum_grade_for(risk_level)
    if risk_level == "R0":
        return "ALLOW_WITH_LABEL"
    if risk_level == "R1":
        if not grade_meets(effective_grade, required_grade):
            return "ASK"
        return "ALLOW"
    if risk_level == "R2":
        if not grade_meets(effective_grade, required_grade):
            return "ESCALATE"
        if unresolved_conflict:
            return "ESCALATE"
        return "ALLOW"
    if risk_level == "R3":
        if not grade_meets(effective_grade, required_grade):
            default = policy.get("R3", {}).get("default_on_error", "BLOCK")
            return default  # type: ignore[return-value]
        if not has_human_approval or not has_rollback:
            return "BLOCK"
        if unresolved_conflict:
            return "ESCALATE"
        return "ALLOW"
    return "BLOCK"


_POLICY = load_epistemic_policy()


__all__ = ["gate_decision", "grade_meets", "load_epistemic_policy", "minimum_grade_for"]