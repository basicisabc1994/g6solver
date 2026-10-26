# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Honesty detectors for the action_gating component (ActionGatingBlock).

Two bespoke detectors, both reading real, live-populated fields on
``MotorControlOutput`` (the block's declared Output type — ``ActionGatingBlock``
is a thin metadata-injecting subclass of ``MotorControlBlock``):

``metadata_review_pending_flag`` (FailureMode ``alias_inherits_unproven_parent``)
— ``ActionGatingBlock.infer`` (``action_gating_block.py:119``) unconditionally
``merged.setdefault("review_pending", True)`` on every output's ``metadata``
dict (the parent ``MotorControlBlock`` never sets this key itself — grepped,
no hit), so the alias's own metadata always discloses that it injected an
unproven-parent-inheritance flag rather than an independently-verified one.
Reachable whenever the legacy escape hatch
(``ACTION_GATING_USE_LEGACY_MOTOR_CONTROL=1``) is NOT set — under that env
var, ``infer`` returns ``super().infer(data)`` directly and no metadata
injection (hence no alias disclosure) occurs, so the detector is correctly
silent there too (a real, computed condition, not a dead field).

``simulated_execution_honesty`` (FailureMode ``simulated-presented-as-real``)
— ``action_gating_block.py:111-133`` merges ``evaluate_action(data)``'s own
independent risk assessment into ``metadata["completion_state"]``, which can
reach ``"verified"`` (``decision_engine.py:447``, the ``decide_gate`` default
for a low-risk action with no other flags) — but this assessment is
COMPLETELY DECOUPLED from whether ``MotorControlBlock``'s own underlying
execution (``motor_control_block.py:337-358``) actually ran; that block is
permanently ``@component_maturity("simulation_only")`` and its approved
branch stamps ``action_executed=True`` + ``action_result={"status":
"simulated", ...}`` — never anything else (grepped every ``_motor_output``
call site: no branch ever sets ``action_executed=True`` without also
stamping ``status="simulated"``). So whenever ``metadata["completion_state"]
== "verified"`` AND ``action_executed is True`` in the SAME output, the
alias's own metadata is claiming "verified" about something the SAME output
discloses, elsewhere, as merely simulated — a genuine, reachable,
self-contained honesty gap (e.g. ``op="execute_action"``,
``action_type="write_file"``, default low-risk profile: no
``external_side_effect``/``irreversible_or_costly_to_reverse``, so
``decide_gate`` never raises risk off "low" and ``completion_state`` stays
"verified").

Two rows dispositioned BLANK (not wired here; see
``action_gating_block.py``'s inline comments for the evidence):
``unauthorized-high-impact-action``/``durable-policy-audit-store-unavailable``
— no field anywhere in ``MotorControlOutput`` (top-level or ``metadata``)
distinguishes a durable audit write from an ephemeral one: ``evaluate_action``
is always called from this block with a fresh, store-less
``ActionGateAuditLogger()`` (``decision_engine.py:606``), so
``self.store is not None`` (``audit.py:74``) never triggers, no durable-write
exception path (``audit.py:78-81``) is ever reachable, and the constructed
``ActionGateAuditRecord`` itself is the ONE field of ``ActionGatingEvaluation``
that ``action_gating_block.py`` never copies into ``metadata`` (grepped every
``merged.setdefault(...)`` call: ``audit_record`` is absent) — no observable
marker exists for "was this durable" at all. ``expired-token-reuse`` — no
gate-token concept is produced OR consumed anywhere in
``ActionGatingBlock``'s reachable code (``decide_gate`` never sets
``GateDecision.gate_token``; token issuance/consumption/expiry-checking is
``mvp.action_gating.native_ops``'s separate ``issue_gate_token`` /
``execute_backend_op`` mechanism, never called from
``ActionGatingBlock.infer``).
"""
from __future__ import annotations

from typing import Any

from g6.core.block_contract import register_detector


def _field(output: Any, key: str, default: Any = None) -> Any:
    if isinstance(output, dict):
        return output.get(key, default)
    return getattr(output, key, default)


@register_detector(name="metadata_review_pending_flag")
def detect_alias_inherits_unproven_parent(output: Any) -> bool:
    metadata = _field(output, "metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("review_pending") is True


@register_detector(name="simulated_execution_honesty")
def detect_simulated_presented_as_real(output: Any) -> bool:
    if _field(output, "action_executed") is not True:
        return False
    metadata = _field(output, "metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("completion_state") == "verified"


__all__ = [
    "detect_alias_inherits_unproven_parent",
    "detect_simulated_presented_as_real",
]
