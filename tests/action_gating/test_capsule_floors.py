# SPDX-License-Identifier: Apache-2.0
"""A capsule gets its profile's floors however it is built (audit 002, AG-008).

The floors were applied only when the action came in as a dict, so an ``ActionCapsule`` built in Python was taken as
built: a ``send_email`` capsule declared reversible and local was allowed. A capsule is now floored exactly as the
normaliser floors a dict, so the same action has the same scope hash either way. ``read_file`` has a profile: a local
read that declared facts can only raise.
"""
from __future__ import annotations

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.decision_engine import decide_gate, evaluate_action
from g6.action_gating.native_ops import _capsule_hash, issue_gate_token
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.schema import ActionCapsule
from g6.action_gating.token_store import ActionGatingStore

_RUNS = {"allow", "allow_with_notice"}
_WHEN = "2026-10-20T00:00:00+00:00"


def _typed_email(**extra) -> ActionCapsule:
    return ActionCapsule(action_id="ag-typed-email", action_type="send_email", target_backend="mail",
                         target_resource="c@example.test", description="d", user_visible_summary="d",
                         reversibility="reversible", blast_radius="local", created_at=_WHEN, **extra)


def test_a_capsule_built_in_python_gets_its_profile_floors() -> None:
    ev = evaluate_action(_typed_email(), audit_logger=ActionGateAuditLogger())
    assert ev.gate_decision.decision not in _RUNS
    cap = ev.action_capsule
    assert cap.external_side_effect and cap.irreversible_or_costly_to_reverse
    assert (cap.reversibility, cap.blast_radius) == ("irreversible", "external_party")
    assert cap.required_authority == ["owner"] and {"communications", "privacy"} <= set(cap.safety_domains)


def test_decide_gate_floors_a_capsule_it_is_handed() -> None:
    decision = decide_gate(_typed_email(), audit_id="audit-test")
    assert decision.risk.aggregate_risk == "high" and decision.decision not in _RUNS


def test_a_capsule_cannot_observe_its_own_authority() -> None:
    cap = ActionCapsule(action_id="ag-typed-pay", action_type="payment", target_resource="invoice-1",
                        description="d", user_visible_summary="d", required_authority=["owner"],
                        observed_authority=["owner"], created_at=_WHEN)
    ev = evaluate_action(cap, audit_logger=ActionGateAuditLogger())
    assert ev.action_capsule.observed_authority == []
    assert ev.action_capsule.proposed_inputs["observed_authority"] == ["owner"]  # kept as the caller's claim
    assert "G6_E_AUTHORITY_UNVERIFIED" in ev.gate_decision.reason_codes


def test_a_floored_capsule_has_the_scope_hash_of_the_same_action_given_as_a_dict() -> None:
    from_dict = normalize_action_capsule({"action_type": "send_email", "target_backend": "mail",
                                          "target_resource": "c@example.test", "reversibility": "reversible",
                                          "blast_radius": "local"})
    under_declared = from_dict.model_copy(update={"external_side_effect": False, "irreversible_or_costly_to_reverse": False,
                                                  "reversibility": "reversible", "blast_radius": "local",
                                                  "required_authority": []})
    floored = normalize_action_capsule(under_declared)
    assert _capsule_hash(floored) == _capsule_hash(from_dict)
    assert _capsule_hash(normalize_action_capsule(floored)) == _capsule_hash(floored)  # idempotent


def test_a_token_for_a_capsule_is_scoped_like_one_for_the_dict(tmp_path) -> None:
    store = ActionGatingStore(str(tmp_path / "gate.db"))
    action = {"action_type": "send_email", "target_backend": "mail", "target_resource": "c@example.test"}
    from_dict = issue_gate_token(action, store=store)
    typed = normalize_action_capsule(action).model_copy(update={"external_side_effect": False,
                                                                "reversibility": "reversible"})
    assert issue_gate_token(typed, store=store).action_capsule_hash == from_dict.action_capsule_hash


def test_read_file_has_a_profile_declarations_can_only_raise() -> None:
    gate = evaluate_action({"action_type": "read_file", "target_backend": "local", "target_resource": "notes.txt"},
                           audit_logger=ActionGateAuditLogger()).gate_decision
    assert gate.decision == "allow" and "G6_E_ACTION_UNPROFILED" not in gate.reason_codes
    raised = normalize_action_capsule({"action_type": "read_file", "target_resource": "notes.txt",
                                       "blast_radius": "public", "external_side_effect": True})
    assert raised.blast_radius == "public" and raised.external_side_effect
    lowered = normalize_action_capsule({"action_type": "read_file", "target_resource": "notes.txt",
                                        "blast_radius": "none"})
    assert lowered.blast_radius == "local"


def test_a_database_write_is_profiled_and_keeps_what_the_database_op_declared() -> None:
    # g6.integrations.database: the reversibility and reach are the op's observation and the operator's declaration;
    # an unprofiled database_write was held as unknown risk however it was declared
    declared = evaluate_action({"action_type": "database_write", "target_backend": "sqlite", "target_resource": "shop",
                                "reversibility": "reversible", "blast_radius": "local"}, ActionGateAuditLogger())
    assert "G6_E_ACTION_UNPROFILED" not in declared.gate_decision.reason_codes
    assert "data_integrity" in declared.action_capsule.safety_domains and declared.gate_decision.decision in _RUNS
    undeclared = evaluate_action({"action_type": "database_write", "target_backend": "sqlite",
                                  "target_resource": "shop"}, ActionGateAuditLogger())
    assert (undeclared.action_capsule.reversibility, undeclared.action_capsule.blast_radius) == ("unknown", "unknown")
    assert undeclared.gate_decision.decision not in _RUNS