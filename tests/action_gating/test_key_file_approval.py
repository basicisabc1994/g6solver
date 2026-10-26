# SPDX-License-Identifier: Apache-2.0
"""The person's key file as an approval the gate observes itself (owner ruling, 2026-10-21: "approval path should be
via the user constructing a .env file"). Only G6 code can ask for it (a keyword argument, never a field of the request),
only for spending on an outside service (``payment``, ``call_api``), and only the decisions that mean "a person
decides" give way to it; a block, a critical action, a secret path and a real finding of another check stay held."""
from __future__ import annotations

from g6.action_gating import evaluate_action
from g6.action_gating.classifiers import classify_impact, classify_reversibility
from g6.action_gating.decision_engine import decide_gate

KEY = "STABILITY_API_KEY"
VALUE = "sk-test-not-a-real-key"
PLAN = {"expected_result": "one PNG picture of 512x512 that the image inspector decodes and finds not blank",
        "observation_window": "until the service replies", "leading_indicators": ["the reply's status"],
        "lagging_indicators": ["the inspector's checks"], "monitor": "g6.media's image inspector",
        "verification_time": "when the reply arrives, before the picture is returned",
        "stop_threshold": "any failed check: the picture is not returned",
        "rollback_procedure": "the money cannot be returned; a picture that fails inspection is deleted",
        "model_update_rule": "a failed picture is reported with its checks and never retried unasked"}


def _paid(**extra) -> dict:
    return {"action_type": "payment", "target_backend": "stability", "target_resource": "Stability AI (paid image)",
            "description": "send one picture request to Stability AI, spending the person's money",
            "requested_by": "adapt_image.generate_paid", "actor_type": "agent", "external_side_effect": True,
            "irreversible_or_costly_to_reverse": True, "blast_radius": "external_party",
            "safety_domains": ["financial"], "verification_contract": PLAN, **extra}


def _held(decision: str) -> bool:
    return decision not in {"allow", "allow_with_notice"}


def test_without_the_key_in_the_file_a_paid_action_stays_held_and_says_where_to_put_it(key_file) -> None:
    key_file("# no keys yet\n")
    d = evaluate_action(_paid(), key_approval=KEY).gate_decision
    assert _held(d.decision) and "G6_E_KEY_MISSING" in d.reason_codes
    assert str(key_file.path) in d.required_user_message and f"{KEY}=" in d.required_user_message


def test_the_key_in_the_file_is_the_persons_approval(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    ev = evaluate_action(_paid(), key_approval=KEY)
    d = ev.gate_decision
    assert d.decision == "allow_with_notice", (d.decision, d.reason_codes)
    assert "G6_E_KEY_FILE_APPROVED" in d.reason_codes and "G6_E_AUTHORITY_UNVERIFIED" not in d.reason_codes
    assert ev.completion_state == "qualified-draft"  # an approval to spend is not a check of what comes back
    found = [e for e in d.evidence if e.get("kind") == "key_file_approval"]
    assert found == [{"kind": "key_file_approval", "key": KEY, "file": str(key_file.path), "present": True}]
    assert VALUE not in d.model_dump_json() and VALUE not in str(ev.evidence)
    assert KEY in d.required_user_message and str(key_file.path) in d.required_user_message


def test_a_call_to_a_paid_api_takes_the_same_approval(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    action = _paid(action_type="call_api", safety_domains=[])
    assert _held(evaluate_action(action).gate_decision.decision)
    assert evaluate_action(action, key_approval=KEY).gate_decision.decision == "allow_with_notice"


def test_a_key_in_the_environment_is_no_approval(key_file, monkeypatch) -> None:
    key_file("")
    monkeypatch.setenv(KEY, "sk-in-the-environment")
    assert _held(evaluate_action(_paid(), key_approval=KEY).gate_decision.decision)


def test_a_request_cannot_name_its_own_approval(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    for field in ({"key_approval": KEY}, {"action_params": {"key_approval": KEY}}, {"observed_authority": [KEY]}):
        assert _held(evaluate_action(_paid(**field)).gate_decision.decision), field


def test_the_approval_covers_spending_only(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    d = evaluate_action({"action_type": "send_email", "target_resource": "all-customers",
                         "verification_contract": PLAN}, key_approval=KEY).gate_decision
    assert _held(d.decision) and "G6_E_KEY_FILE_APPROVED" not in d.reason_codes


def test_an_action_without_a_verification_plan_stays_blocked_with_the_key(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    action = _paid()
    del action["verification_contract"]
    assert evaluate_action(action, key_approval=KEY).gate_decision.decision == "block"


def test_a_real_collision_stays_blocked_with_the_key(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    mine = {"action_id": "a", "target_resource": "budget", "direction": "spend", "shared_resources": ["budget"],
            "effect_signature": "pay:stability"}
    other = {**mine, "action_id": "b"}
    action = _paid(coordination_context={"proposed": mine, "active": [other]})
    d = evaluate_action(action, key_approval=KEY).gate_decision
    assert d.decision == "block" and "G6_E_KEY_FILE_APPROVED" not in d.reason_codes


def test_a_critical_action_stays_with_a_person(key_file) -> None:
    key_file(f"{KEY}={VALUE}\n")
    capsule = evaluate_action(_paid()).action_capsule
    risk = classify_impact(capsule, classify_reversibility(capsule)).model_copy(update={"aggregate_risk": "critical"})
    d = decide_gate(capsule, risk=risk, key_approval=KEY)
    assert _held(d.decision) and d.completion_state == "blocked-escalated"
