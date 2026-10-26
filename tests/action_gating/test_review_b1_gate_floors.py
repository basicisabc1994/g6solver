# SPDX-License-Identifier: Apache-2.0
"""Review B1: a premise's evidence keys and stance are checked (B1-1), a caller's risk never lowers the gate's own
(B1-2), and a read of a secret-looking path goes to a person (B1-4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.decision_engine import decide_gate, evaluate_action
from g6.action_gating.mitigation import build_mitigation_plan
from g6.action_gating.scenario_modeler import build_action_scenarios
from g6.action_gating.schema import (
    ActionCapsule,
    ActionIntentModel,
    CoordinationContextModel,
    FactualEvidenceRef,
    FactualPrecondition,
    ImpactAssessment,
    VerificationContractModel,
    WorldStateModel,
)
from g6.mcp import invoke_reply

_WHEN = "2026-10-21T00:00:00+00:00"
_RUNS = {"allow", "allow_with_notice"}
E_SECRET_READ = "G6_E_SECRET_READ"
E_SECRET_WRITE = "G6_E_SECRET_WRITE"


def _evaluate(action: dict):
    return evaluate_action(action, audit_logger=ActionGateAuditLogger()).gate_decision


# --- B1-1: a premise's evidence keys and stance -------------------------------------------------------------------

def _premise(**stance) -> dict:
    return {"statement": "The customer asked for the refund.",
            "evidence": [{"source_type": "primary", "independence_group": g, "authority": "high", **stance}
                         for g in ("a", "b")]}


def _gate_reply(premise: dict) -> dict:
    return invoke_reply("action_gating", "evaluate", json.dumps(
        {"action": {"action_type": "write_file", "target_resource": "refunds.csv", "factual_preconditions": [premise]}}))


def test_a_misspelt_stance_key_on_a_premise_is_refused_over_mcp() -> None:
    reply = _gate_reply(_premise(stance="opposes"))
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_ACTION_GATING_ACTION_INVALID]"), reply
    assert "stance" in reply["error"]


@pytest.mark.parametrize("stance", ["refutes", "Opposes"])
def test_a_premise_stance_outside_the_vocabulary_is_refused_over_mcp(stance: str) -> None:
    reply = _gate_reply(_premise(supports_or_opposes=stance))
    assert reply["ok"] is False and reply["error"].startswith("[G6_E_ACTION_GATING_ACTION_INVALID]"), reply


def test_the_premise_spelt_right_is_refuted_and_sent_to_a_person() -> None:
    reply = _gate_reply(_premise(supports_or_opposes="opposes"))
    gate = reply["result"]["gate_decision"]
    assert gate["decision"] == "require_human_review" and "G6_E_FACTUAL_PRECONDITION_UNSUPPORTED" in gate["reason_codes"]
    item = next(e for e in gate["evidence"] if e["kind"] == "factual_precondition")
    assert (item["evidence_grade"], item["refuted"]) == ("E0", True)


def test_python_refuses_an_unknown_key_or_stance_on_a_premise() -> None:
    with pytest.raises(ValidationError):
        FactualEvidenceRef(source_type="primary", stance="opposes")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        FactualEvidenceRef(source_type="primary", supports_or_opposes="refutes")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FactualPrecondition(statement="s", evidences=[])  # type: ignore[call-arg]
    assert FactualEvidenceRef().supports_or_opposes == "supports"  # a record with no stance supports its premise


# --- B1-2: the gate derives the risk itself and keeps the stricter ------------------------------------------------

def _declared_contexts(action_id: str) -> dict:
    return {
        "verification_contract": VerificationContractModel(
            expected_result="the invoice is paid", observation_window="24h", leading_indicators=["payment sent"],
            lagging_indicators=["receipt arrives"], monitor="finance", verification_time="T+24h",
            stop_threshold="any bounce", rollback_procedure="request a refund", model_update_rule="revise on bounce"),
        "coordination_context": CoordinationContextModel(proposed=ActionIntentModel(action_id=action_id)),
        "world_state": WorldStateModel(horizons_evaluated=["next week"]),
    }


def _self_assessed(capsule: ActionCapsule):
    """The caller's own low risk, scenario model and residual-low mitigation plan, handed to ``decide_gate``."""
    low = ImpactAssessment(aggregate_risk="low")
    scenario = build_action_scenarios(capsule, low)
    plan = build_mitigation_plan(capsule, low, scenario).model_copy(update={"residual_risk": "low"})
    return decide_gate(capsule, risk=low, scenario_model=scenario, mitigation_plan=plan, audit_id="audit-test")


def test_a_payment_with_a_caller_supplied_low_risk_is_not_allowed() -> None:
    capsule = ActionCapsule(action_id="ag-pay", action_type="payment", target_resource="invoice-7",
                            description="Pay invoice 7", user_visible_summary="Pay invoice 7", created_at=_WHEN,
                            **_declared_contexts("ag-pay"))
    gate = _self_assessed(capsule)
    assert gate.decision not in _RUNS and "G6_E_GATE_OK" not in gate.reason_codes, gate.reason_codes
    assert gate.risk.aggregate_risk == "high"
    assert gate.decision == evaluate_action(capsule, audit_logger=ActionGateAuditLogger()).gate_decision.decision \
        == "require_human_review"


def test_an_unprofiled_action_with_a_caller_supplied_low_risk_is_not_allowed() -> None:
    capsule = ActionCapsule(action_id="ag-wire", action_type="wire_transfer", target_resource="acct-9",
                            description="Send the deposit", user_visible_summary="Send the deposit",
                            reversibility="reversible", blast_radius="local", created_at=_WHEN)
    gate = _self_assessed(capsule)
    assert gate.decision not in _RUNS and gate.risk.aggregate_risk == "unknown", gate.reason_codes
    assert "G6_E_ACTION_UNPROFILED" in gate.reason_codes and "G6_E_GATE_OK" not in gate.reason_codes


def test_a_caller_may_still_raise_the_risk() -> None:
    # reversible only with a backup the gate can read (ACTION_GATING-01); this file stands for one
    capsule = ActionCapsule(action_id="ag-note", action_type="write_file", target_resource="notes.txt",
                            description="d", user_visible_summary="d", reversibility="reversible",
                            blast_radius="local", created_at=_WHEN,
                            proposed_inputs={"checkpoint": str(Path(__file__).resolve())})
    assert decide_gate(capsule, audit_id="audit-test").decision == "allow"   # survival: derived low, allowed
    raised = decide_gate(capsule, risk=ImpactAssessment(aggregate_risk="critical"), audit_id="audit-test")
    assert raised.risk.aggregate_risk == "critical" and raised.decision not in _RUNS


# --- B1-4: a read of a secret-looking path goes to a person -------------------------------------------------------

_SECRETS = ["~/.ssh/id_rsa", r"D:\home\ada\.ssh\id_ed25519", ".env", "config/.env.production", "deploy/prod.env",
            "/home/alice/.aws/credentials", "~/.netrc", "certs/server.pem", "deploy/server.key", "secrets.yaml",
            "config/api_token.txt", "client_secret.json", "~/Library/Keychains/login.keychain-db", "/etc/shadow",
            r"D:\Profiles\ada\AppData\Roaming\Microsoft\Credentials\ABC123", "~/.git-credentials", "~/.gnupg/secring.gpg"]
_PLAIN = ["src/app/main.py", "notes.txt", "README.md", "docs/tokenizer.md", "tests/test_keys_order.py"]


@pytest.mark.parametrize("path", _SECRETS)
def test_reading_a_secret_looking_path_goes_to_a_person(path: str) -> None:
    gate = _evaluate({"action_type": "read_file", "target_backend": "local", "target_resource": path})
    assert gate.decision == "require_human_review", (path, gate.decision, gate.reason_codes)
    assert E_SECRET_READ in gate.reason_codes and "G6_E_GATE_OK" not in gate.reason_codes


@pytest.mark.parametrize("path", _PLAIN)
def test_reading_a_plain_file_is_still_allowed(path: str) -> None:
    gate = _evaluate({"action_type": "read_file", "target_backend": "local", "target_resource": path})
    assert gate.decision == "allow" and gate.reason_codes == ["G6_E_GATE_OK"], (path, gate.reason_codes)


def test_a_secret_path_in_the_parameters_is_read_too() -> None:
    gate = _evaluate({"action_type": "read_file", "target_backend": "local", "target_resource": "notes.txt",
                      "action_params": {"path": "~/.ssh/id_rsa"}})
    assert gate.decision == "require_human_review" and E_SECRET_READ in gate.reason_codes


def test_a_secret_read_built_in_python_and_over_mcp_goes_to_a_person() -> None:
    capsule = ActionCapsule(action_id="ag-read", action_type="read_file", target_backend="local",
                            target_resource="/srv/app/.env", description="d", user_visible_summary="d",
                            reversibility="reversible", blast_radius="local", created_at=_WHEN)
    assert decide_gate(capsule, audit_id="audit-test").decision == "require_human_review"
    plain = capsule.model_copy(update={"target_resource": "/srv/app/main.py"})
    assert decide_gate(plain, audit_id="audit-test").decision == "allow"   # survival: the same read of a plain file
    reply = invoke_reply("action_gating", "evaluate", json.dumps(
        {"action": {"action_type": "read_file", "target_backend": "local", "target_resource": "~/.ssh/id_rsa"}}))
    gate = reply["result"]["gate_decision"]
    assert gate["decision"] == "require_human_review" and E_SECRET_READ in gate["reason_codes"]


@pytest.mark.parametrize("path", ["~/.ssh/authorized_keys", ".env", "~/.aws/credentials", "deploy/server.key"])
def test_writing_a_secret_looking_path_goes_to_a_person(path: str) -> None:
    # the same floor for a write: planting a key in authorized_keys or rewriting .env is a person's call
    gate = _evaluate({"action_type": "write_file", "target_backend": "local", "target_resource": path})
    assert gate.decision == "require_human_review", (path, gate.decision, gate.reason_codes)
    assert E_SECRET_WRITE in gate.reason_codes and "G6_E_GATE_OK" not in gate.reason_codes
    plain = _evaluate({"action_type": "write_file", "target_backend": "local", "target_resource": "notes.txt"})
    assert E_SECRET_WRITE not in plain.reason_codes                      # survival: a plain file is not held for it
