# SPDX-License-Identifier: Apache-2.0
"""The action gate in front of every outward act a sources member takes: a push, a fetch, a paid call.

``_``-prefixed, so it never registers as a component. :func:`ask` asks ``g6.action_gating`` about one declared action
and returns its decision as evidence (the gate writes its own audit record under ``$G6_HOME``). ``key_approval`` names
the key a paid call would spend: the gate itself reads whether the person's key file holds it, and a key there is the
person's approval to spend it (``allow_with_notice`` with ``G6_E_KEY_FILE_APPROVED``). A gate that cannot decide holds
the act: its decision is ``block``.

:func:`lets_through` is whether a decision lets the act go ahead: ``allow``; or ``allow_with_notice``, which for a paid
call counts only when the notice is the key file's approval. Every other decision holds it, and the member returns
what it would have done instead (a dry run). Nothing a caller says reaches the gate as an approval: the action is
built by the member from what it observed and what the person configured.
"""
from __future__ import annotations

from typing import Any

#: The decisions that are not a hold.
GOES = frozenset({"allow", "allow_with_notice"})


def ask(action: dict[str, Any], key_approval: str | None = None) -> dict[str, Any]:
    """``g6.action_gating``'s decision on ``action``, as an evidence entry."""
    from g6.action_gating import evaluate_action

    found: dict[str, Any] = {"kind": "action_gate", "gate": "g6.action_gating", "action_type": action["action_type"],
                             "target": action.get("target_resource", "")}
    try:
        evaluation = evaluate_action(action, key_approval=key_approval)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot decide holds the act
        return {**found, "decision": "block", "reason_codes": [],
                "message": f"the action gate could not decide: {type(exc).__name__}: {exc}"[:300], "audit_id": ""}
    decided = evaluation.gate_decision
    return {**found, "decision": str(decided.decision), "reason_codes": [str(c) for c in decided.reason_codes],
            "message": str(decided.required_user_message or ""), "audit_id": str(decided.audit_id or "")}


def lets_through(gate: dict[str, Any], *, paid: bool = False) -> bool:
    """Whether the gate's decision lets the act go ahead (see the module docstring)."""
    from g6.action_gating.decision_engine import E_KEY_FILE_APPROVED

    decision = gate.get("decision")
    if decision == "allow":
        return True
    if decision == "allow_with_notice":
        return not paid or E_KEY_FILE_APPROVED in gate.get("reason_codes", ())
    return False


def plan(expected: str, monitor: str, verification_time: str, stop: str, rollback: str) -> dict[str, Any]:
    """A verification contract: what G6 itself checks after the act, which the gate needs before it lets an outward act
    through."""
    return {"expected_result": expected, "observation_window": "until the act returns, within the op's time limit",
            "leading_indicators": ["the outside program's or service's exit status and reply"],
            "lagging_indicators": ["G6's own read-back of the result"], "monitor": monitor,
            "verification_time": verification_time, "stop_threshold": stop, "rollback_procedure": rollback,
            "model_update_rule": "a failed act is reported with its code and never retried unasked"}


__all__ = ["GOES", "ask", "lets_through", "plan"]
