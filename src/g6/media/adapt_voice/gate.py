# SPDX-License-Identifier: Apache-2.0
"""Every paid request adapt_voice could send, decided by ``g6.action_gating`` first.

:func:`payment` is the action the gate decides: a payment to the speech service for one request, external and costly
to reverse once sent, with the plan G6 itself follows to check what comes back (the voice inspected from disk before it
is returned and removed when it fails; a transcript checked against the clip's measured speech). :func:`ask` asks the
gate with the engine's key name: the gate itself reads whether the person's key file holds it, and a key there is
their approval to spend it (``allow_with_notice``, ``G6_E_KEY_FILE_APPROVED``). :func:`lets_through` is whether that
decision sends the request. A gate that cannot decide holds it.
"""
from __future__ import annotations

from typing import Any


def payment(engine: Any, what: str, would: dict[str, Any], checks: str) -> dict[str, Any]:
    plan = {"expected_result": checks,
            "observation_window": "until the service replies, within the op's time limit",
            "leading_indicators": ["the reply's status", "the reply's size, under the cap"],
            "lagging_indicators": ["G6's own checks on what came back"],
            "monitor": "g6.media's sound inspector and adapt_voice's checks",
            "verification_time": "when the reply arrives, before anything is returned",
            "stop_threshold": "any failed check: nothing is returned and any file is removed",
            "rollback_procedure": "the money cannot be returned; a result that fails is removed",
            "model_update_rule": "a failed request is reported with its checks and never retried unasked"}
    return {"action_type": "payment", "target_backend": engine.name,
            "target_resource": f"{engine.service} ({what})",
            "description": f"send one {what} request to {engine.service}, a paid service, spending the person's money",
            "action_params": {"engine": engine.name, **would}, "requested_by": "adapt_voice", "actor_type": "agent",
            "external_side_effect": True, "irreversible_or_costly_to_reverse": True, "blast_radius": "external_party",
            "safety_domains": ["financial"], "verification_contract": plan}


def ask(action: dict[str, Any], key_approval: str | None) -> dict[str, Any]:
    """The gate's decision on ``action``, as evidence (its audit record is written under ``$G6_HOME``)."""
    from g6.action_gating import evaluate_action

    found: dict[str, Any] = {"kind": "action_gate", "gate": "g6.action_gating", "action_type": action["action_type"]}
    try:
        evaluation = evaluate_action(action, key_approval=key_approval)
    except Exception as exc:  # noqa: BLE001 - a gate that cannot decide never lets money be spent
        return {**found, "decision": "block", "completion_state": "blocked-escalated", "reason_codes": [],
                "required_user_message": f"the action gate could not decide: {type(exc).__name__}: {exc}"[:300],
                "required_confirmation_phrase": None, "audit_id": ""}
    decided = evaluation.gate_decision
    return {**found, "decision": decided.decision, "completion_state": evaluation.completion_state,
            "reason_codes": list(decided.reason_codes), "required_user_message": decided.required_user_message,
            "required_confirmation_phrase": decided.required_confirmation_phrase, "audit_id": decided.audit_id}


def lets_through(gate: dict[str, Any]) -> bool:
    """Its ``allow``, or its ``allow_with_notice`` because the person's key file holds the engine's key."""
    from g6.action_gating.decision_engine import E_KEY_FILE_APPROVED

    return gate["decision"] == "allow" or (gate["decision"] == "allow_with_notice"
                                           and E_KEY_FILE_APPROVED in gate["reason_codes"])


__all__ = ["ask", "lets_through", "payment"]
