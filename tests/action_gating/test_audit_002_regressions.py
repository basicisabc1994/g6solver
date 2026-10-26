# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 002 (docs/transition/audits/002_action_gating.md).

Written before the fixes; each targets one finding. Execution stays simulated throughout.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

import g6.action_gating
from g6.action_gating.audit_store import ActionGateAuditStore
from g6.action_gating.capabilities import discover_capabilities
from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import evaluate_action
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.native_ops import (
    describe_backend_op,
    discover_backend_ops,
    execute_backend_op,
    issue_gate_token,
)
from g6.action_gating.normalizer import normalize_action_capsule
from g6.action_gating.provenance import provenance_for_decision
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.token_store import ActionGatingStore
from g6.action_gating.user_model import estimate_user_mental_model
from g6.core.storage import g6_home, state_path


@pytest.fixture
def store() -> ActionGatingStore:
    return ActionGatingStore(str(Path(tempfile.mkdtemp()) / "gate.db"))


#: A file the gate can read, standing for the backup a write is undone from (ACTION_GATING-01: without one a write's
#: reversibility is unknown and no token is authorised).
_BACKUP = {"checkpoint": str(Path(__file__).resolve())}


def _issue(store: ActionGatingStore, params: dict) -> str:
    params = {**params, **_BACKUP}
    return issue_gate_token(
        {"action_type": "write_file", "target_backend": "local", "action_params": params, **params}, store=store
    ).token


def _execute(store: ActionGatingStore, token: str, args: dict, op: str = "write_file", backend: str = "local"):
    return execute_backend_op(backend, op, {**args, **_BACKUP}, token, global_safety_approved=True,
                              audit_available=True, store=store)


# AG-T01 / AG-001 — a token issued for one payload must not authorise a different payload.
def test_token_does_not_authorise_different_content(store: ActionGatingStore) -> None:
    token = _issue(store, {"path": "scratch.txt", "content": "safe"})
    result = _execute(store, token, {"path": "scratch.txt", "content": "different"})
    assert result["completion_state"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_GATE_TOKEN_SCOPE"
    # The token was not consumed by the refused attempt: the original payload still works.
    assert _execute(store, token, {"path": "scratch.txt", "content": "safe"})["completion_state"] == "qualified-draft"


# AG-T03 / AG-001 — same target, changed amount.
def test_token_does_not_authorise_changed_amount(store: ActionGatingStore) -> None:
    token = _issue(store, {"path": "ledger.csv", "amount": 10})
    result = _execute(store, token, {"path": "ledger.csv", "amount": 10_000})
    assert result["error"]["code"] == "G6_E_GATE_TOKEN_SCOPE"


# AG-T02 / AG-002 — args may not shadow the operation identity.
def test_args_cannot_shadow_operation_identity(store: ActionGatingStore) -> None:
    token = _issue(store, {"path": "scratch.txt"})
    shadow = {"path": "scratch.txt", "action_type": "write_file", "target_backend": "local"}
    result = _execute(store, token, shadow, op="delete_file", backend="prod")
    assert result["completion_state"] == "blocked-escalated"
    assert result["error"]["code"] == "G6_E_RESERVED_ARGUMENT"
    assert _execute(store, token, {"path": "scratch.txt"})["completion_state"] == "qualified-draft"


# AG-T14 / AG-007 — a rule pass is a policy decision, not verification of anything.
def test_low_risk_allow_is_not_labelled_verified() -> None:
    ev = evaluate_action({"action_type": "write_file", "target_backend": "local", "target_resource": "a.txt"})
    assert ev.gate_decision.decision in {"allow", "allow_with_notice", "require_additional_information"}
    assert ev.completion_state != "verified"


# AG-T07 / AG-006 — a request for confirmation is not evidence that a human approved.
def test_confirmation_required_is_not_human_approval() -> None:
    ev = evaluate_action({"action_type": "account_deletion", "action_params": {"target_resource": "acct-1"}})
    assert ev.gate_decision.required_confirmation_phrase is not None
    assert provenance_for_decision(ev.action_capsule, ev.gate_decision).value != "human_approval"


def test_user_authored_action_is_not_human_approval() -> None:
    ev = evaluate_action({"action_type": "write_file", "actor_type": "user", "target_resource": "a.txt"})
    assert provenance_for_decision(ev.action_capsule, ev.gate_decision).value != "human_approval"


# Found while fixing AG-001: normalisation must be idempotent or a capsule's scope hash drifts.
def test_normalisation_is_idempotent() -> None:
    from g6.action_gating.native_ops import _capsule_hash
    from g6.action_gating.normalizer import normalize_action_capsule

    once = normalize_action_capsule({"action_type": "write_file", "target_backend": "local",
                                     "action_params": {"path": "a.txt", "content": "x"}})
    twice = normalize_action_capsule(once)
    assert _capsule_hash(once) == _capsule_hash(twice)


# AG-T05 / AG-T06 / AG-003 — omitted context is reported as "not evaluated", never silently passed.
def test_material_action_without_context_reports_unevaluated_controls() -> None:
    ev = evaluate_action({"action_type": "payment", "target_backend": "stripe", "target_resource": "invoice-1",
                          "external_side_effect": True})
    items = [e for e in ev.gate_decision.evidence if e.get("kind") == "control_not_evaluated"]
    assert {i["control"] for i in items} == {"verification_contract", "coordination", "weak_signals"}
    assert "G6_E_CONTROL_NOT_EVALUATED" in ev.gate_decision.reason_codes
    assert ev.completion_state != "verified"


def test_non_material_action_without_context_is_not_flagged() -> None:
    ev = evaluate_action({"action_type": "read_file", "target_backend": "local", "target_resource": "a.txt"})
    assert "G6_E_CONTROL_NOT_EVALUATED" not in ev.gate_decision.reason_codes


# AL-004 through the gate: the coordination model carries the idempotency key.
def test_gate_coordination_model_accepts_idempotency_key() -> None:
    from g6.action_gating.decision_engine import _to_action_intent
    from g6.action_gating.schema import ActionIntentModel

    intent = _to_action_intent(ActionIntentModel(action_id="a", idempotency_key="k1"))
    assert intent.idempotency_key == "k1"


# AG-008 — declared facts may make a known action riskier than its profile, never safer.
def test_declared_facts_cannot_make_a_profiled_action_safer() -> None:
    ev = evaluate_action({"action_type": "send_email", "target_resource": "all-customers",
                          "external_side_effect": False, "irreversible_or_costly_to_reverse": False,
                          "reversibility": "reversible", "blast_radius": "local"})
    cap = ev.action_capsule
    assert cap.external_side_effect and cap.irreversible_or_costly_to_reverse
    assert (cap.reversibility, cap.blast_radius) == ("irreversible", "external_party")
    assert ev.gate_decision.decision not in {"allow", "allow_with_notice"}


def test_declared_facts_can_still_make_an_action_riskier() -> None:
    cap = normalize_action_capsule({"action_type": "write_file", "target_resource": "a.txt",
                                    "external_side_effect": True, "blast_radius": "public"})
    assert cap.external_side_effect and cap.blast_radius == "public"


# AG-008 — authority named in a request is a claim, not an observation.
def test_caller_declared_authority_is_not_observed_authority() -> None:
    ev = evaluate_action({"action_type": "payment", "target_resource": "invoice-1", "observed_authority": ["owner"]})
    assert ev.action_capsule.observed_authority == []
    assert ev.action_capsule.proposed_inputs["observed_authority"] == ["owner"]  # kept as the caller's claim
    assert "G6_E_AUTHORITY_UNVERIFIED" in ev.gate_decision.reason_codes


# AG-T13 / AG-008 — the caller's own approval flags never become live-execution authority.
def test_self_declared_approvals_never_grant_live_authority(store: ActionGatingStore) -> None:
    token = _issue(store, {"path": "scratch.txt"})
    result = _execute(store, token, {"path": "scratch.txt"})
    assert result["executed"] is False and result["completion_state"] == "qualified-draft"
    assert result["authorization"]["global_safety_approval"] == "caller_declared_unauthenticated"
    assert {"kind": "global_safety_approval", "source": "caller", "authenticated": False} in result["evidence"]


def _in_durable_trail(audit_id: str) -> bool:
    # A second connection to the default trail, opened by path so this never creates a store elsewhere.
    trail = ActionGateAuditStore(str(state_path("action_gating_audit.db")))
    try:
        return trail.get_by_audit_id(audit_id) is not None
    finally:
        trail.close()


# AG-009 — the default evaluation commits its audit record to the durable trail and says so.
def test_default_evaluation_commits_a_durable_audit_record() -> None:
    ev = evaluate_action({"action_type": "payment", "target_resource": "invoice-2"})
    assert ev.audit_record is not None
    assert _in_durable_trail(ev.audit_record.audit_id)
    assert {"kind": "audit_record", "audit_id": ev.audit_record.audit_id, "persisted": True} in ev.evidence


# AG-T08 / AG-009 — a trail that cannot be opened blocks audit-required actions; no in-memory fallback.
def test_an_unopenable_audit_trail_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(cls: type) -> ActionGateAuditStore:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(ActionGateAuditStore, "default", classmethod(refuse))
    ev = evaluate_action({"action_type": "mystery_op", "target_resource": "x"})  # unknown risk: audit required
    assert ev.gate_decision.decision == "block"
    assert "G6_E_DEGRADED_ASSURANCE_BLOCKS_EXECUTION" in ev.gate_decision.reason_codes


def test_the_audit_trail_defaults_under_g6_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ActionGateAuditStore, "_ensure_schema", lambda self: None)  # open nothing
    assert Path(ActionGateAuditStore().db_path).parent == g6_home().resolve()


# AG-010 — capabilities say what is provided: local policy only, simulated execution.
def test_capabilities_do_not_claim_a_global_policy_guard() -> None:
    caps = discover_capabilities()
    assert caps.capabilities["global_policy_bridge"] is False
    assert caps.capabilities["live_native_execution"] is False
    assert caps.safety_guards["global_policy_enforced"] is False


# AG-T11 / AG-010 — an op is advertised only for the backend that describes it.
def test_native_op_discovery_is_backend_specific() -> None:
    assert discover_backend_ops("payments") == []
    assert describe_backend_op("unrelated", "write_file") is None
    assert describe_backend_op("local", "write_file") is not None
    assert all(discover_backend_ops(b) for b in discover_capabilities().supported_backends)


# AG-011 — template outputs carry no made-up numbers and claim no risk reduction.
def _send_email():
    cap = normalize_action_capsule({"action_type": "send_email", "target_resource": "c@example.test"})
    return cap, classify_impact(cap, classify_reversibility(cap))


def test_the_user_model_template_reports_no_confidence() -> None:
    cap, _risk = _send_email()
    assert estimate_user_mental_model(cap).confidence is None


def test_scenario_templates_do_not_claim_a_likelihood() -> None:
    cap, risk = _send_email()
    model = build_action_scenarios(cap, risk)
    cases = [model.best_case, model.expected_case, model.failure_case, model.partial_failure_case,
             model.incorrect_assumption_case, model.abuse_or_misuse_case, model.rollback_case]
    assert {case.likelihood for case in cases} == {"unknown"}


def test_suggested_mitigations_do_not_lower_the_assessed_risk() -> None:
    cap, risk = _send_email()
    assert risk.aggregate_risk == "high"
    assert build_mitigation_plan(cap, risk).residual_risk == "high"


# AG-013 — the component metadata states the licence its file headers carry.
def test_component_metadata_licence_matches_the_file_headers() -> None:
    root = Path(g6.action_gating.__file__).parent
    spdx = {re.search(r"SPDX-License-Identifier:\s*(\S+)", f.read_text(encoding="utf-8")).group(1)
            for f in root.glob("*.py")}
    meta = (root / "component_metadata.yaml").read_text(encoding="utf-8")
    assert spdx == {"Apache-2.0"}
    assert re.search(r"^license_status:\s*(\S+)", meta, re.M).group(1) == "Apache-2.0"
