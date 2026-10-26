# SPDX-License-Identifier: Apache-2.0
"""Optional checks tighten the existing action gate; no alternative verdicts."""
import pytest

from g6.action_gating import decide_gate, evaluate_action, normalize_action_capsule
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.schema import ImpactAssessment
from g6.core.llm import FakeLLM, NoModel
from g6.csf import BoundedAgent, ResourceTriple

READ = {"action_type": "read_file", "target_resource": "notes.txt"}


def evaluate(action=READ, **kwargs):
    kwargs.setdefault("audit_logger", ActionGateAuditLogger())
    return evaluate_action(action, **kwargs)


def test_csf_gate_01_requested_bound_without_model_refuses():
    # Removal oracle: evaluate_optional_checks rejects missing csf_model when epsilon is supplied.
    result = evaluate(csf_epsilon=0.1)
    assert result.gate_decision.decision == "block"
    assert "G6_E_CSF_MODEL_REQUIRED" in result.gate_decision.reason_codes


def test_csf_gate_02_model_pass_never_verifies():
    # Removal oracle: tighten_decision caps completion_state at qualified-draft.
    result = evaluate(harm_judge=FakeLLM(default='{"decision":"allow"}'))
    assert result.gate_decision.decision == "allow" and result.completion_state == "qualified-draft"
    assert any(e["kind"] == "harm_judge" for e in result.evidence)


@pytest.mark.parametrize("judge", [NoModel(), FakeLLM(default="not json"), FakeLLM(default='{"decision":"approved"}')])
def test_csf_gate_03_d1_judge_failure_tightens(judge):
    # Removal oracle: evaluate_optional_checks's judge exception sets require_human_review.
    result = evaluate(harm_judge=judge)
    assert result.gate_decision.decision == "require_human_review"
    assert result.completion_state == "blocked-escalated"
    assert "G6_E_HARM_JUDGE_FAILED" in result.gate_decision.reason_codes


def test_csf_gate_03_judge_cannot_release_a_block():
    # Removal oracle: tighten_decision selects max(current, candidate) using _DECISION_RANK.
    result = evaluate({"action_type": "send_email"}, harm_judge=FakeLLM(default='{"decision":"allow"}'))
    assert result.gate_decision.decision == "block"


def test_csf_gate_04_bound_uses_actual_model_and_probability():
    # Removal oracle: evaluate_optional_checks blocks when not report.meets_bound.
    model = BoundedAgent(states=("start", "loss"), initial_state="start", unsafe_states=frozenset({"loss"}),
                         resources=ResourceTriple(N=1), transitions=[
                             {"from": "start", "to": "loss", "probability": 1.0},
                             {"from": "loss", "to": "loss", "probability": 1.0}])
    result = evaluate(csf_model=model, csf_epsilon=0.05)
    assert result.gate_decision.decision == "block"
    item = next(e for e in result.evidence if e["kind"] == "csf_reachability_bound")
    assert item["p_unsafe"] == item["vub"] == 1.0 and item["measured"] is False


def test_bound_failure_blocks(monkeypatch):
    # Removal oracle: evaluate_optional_checks's bound exception sets block.
    import g6.csf
    def broken(*args, **kwargs):
        raise ArithmeticError("failed")
    monkeypatch.setattr(g6.csf, "reachability_bound", broken)
    result = evaluate(csf_model=object(), csf_epsilon=0.05)
    assert result.gate_decision.decision == "block"
    assert "G6_E_CSF_CHECK_FAILED" in result.gate_decision.reason_codes


def test_csf_gate_05_review_receipt_is_not_an_approval():
    # Removal oracle: evaluate_action records review_created without changing the held decision.
    result = evaluate({"action_type": "read_file", "target_resource": "~/.ssh/id_rsa"},
                      review_creator=lambda capsule, decision: "review-17")
    assert result.gate_decision.decision == "require_human_review"
    assert any(e.get("review_id") == "review-17" for e in result.evidence)
    claims = evaluate({**READ, "approved": True, "reviewer": "person"}, csf_epsilon=0.1)
    assert claims.gate_decision.decision == "block"


@pytest.mark.parametrize("receipt", [None, "", False])
def test_missing_review_receipt_blocks(receipt):
    # Removal oracle: evaluate_action rejects a non-empty-string review receipt violation.
    result = evaluate({"action_type": "read_file", "target_resource": "~/.ssh/id_rsa"},
                      review_creator=lambda capsule, decision: receipt)
    assert result.gate_decision.decision == "block"
    assert "G6_E_REVIEW_CREATION_FAILED" in result.gate_decision.reason_codes


def test_review_creation_exception_blocks():
    # Removal oracle: evaluate_action's review_creator exception tightens to block.
    def broken(*args):
        raise OSError("review unavailable")
    result = evaluate({"action_type": "read_file", "target_resource": "~/.ssh/id_rsa"}, review_creator=broken)
    assert result.gate_decision.decision == "block"
    assert "G6_E_REVIEW_CREATION_FAILED" in result.gate_decision.reason_codes


def test_any_audit_write_exception_blocks():
    # Removal oracle: evaluate_action catches Exception, not only RuntimeError, on audit write.
    class Broken(ActionGateAuditLogger):
        def write(self, capsule, decision):
            raise OSError("disk unavailable")
    result = evaluate({"action_type": "send_email"}, audit_logger=Broken())
    assert result.gate_decision.decision == "block"
    assert "G6_E_AUDIT_WRITE_FAILED" in result.gate_decision.reason_codes


def test_existing_tier_floor_cannot_be_lowered():
    # Removal oracle: decide_gate's risk = _stricter_risk(...) assignment (existing protection).
    capsule = normalize_action_capsule({"action_type": "payment"})
    decision = decide_gate(capsule, risk=ImpactAssessment(aggregate_risk="low"))
    assert decision.risk.aggregate_risk == "high" and decision.decision != "allow"


def test_optional_check_evidence_survives_durable_audit(tmp_path):
    # Removal oracle: ActionGateAuditLogger.write copies decision.evidence to its audit record.
    from g6.action_gating.audit_store import ActionGateAuditStore
    store = ActionGateAuditStore(str(tmp_path / "audit.db"))
    result = evaluate(harm_judge=FakeLLM(default='{"decision":"block"}'),
                      audit_logger=ActionGateAuditLogger(store=store))
    restored = store.get_by_audit_id(result.gate_decision.audit_id)
    assert any(e.get("kind") == "harm_judge" and e.get("judgement") == "block" for e in restored.evidence)
    store.close()


def test_malformed_audit_receipt_blocks():
    # Removal oracle: evaluate_action validates the logger returned ActionGateAuditRecord.
    class Missing(ActionGateAuditLogger):
        def write(self, capsule, decision):
            return None
    result = evaluate(harm_judge=FakeLLM(default='{"decision":"allow"}'), audit_logger=Missing())
    assert result.gate_decision.decision == "block"
    assert result.audit_record is None
    assert "G6_E_GATE_OK" not in result.gate_decision.reason_codes
    assert "audit" in result.gate_decision.required_user_message


def test_optional_pass_keeps_warning_card_state_aligned():
    # Removal oracle: tighten_decision copies its final completion state into the warning card.
    result = evaluate({"action_type": "read_file", "target_resource": "~/.ssh/id_rsa"},
                      harm_judge=FakeLLM(default='{"decision":"allow"}'))
    assert result.gate_decision.decision == "require_human_review"
    assert result.warning_card["completion_state"] == result.completion_state == "blocked-escalated"
    assert result.warning_card["code"].startswith("G6_E_")
