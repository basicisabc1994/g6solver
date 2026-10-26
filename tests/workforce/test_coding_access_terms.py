# SPDX-License-Identifier: Apache-2.0
"""F-2 (founder, 2026-10-07): on a code change, an objective whose only access terms are "password" or "credential"
takes the high-consequence route instead of the refusal: R3, nothing planned before the person confirms, and the
person approves the checked patch. "private key", "production access", any mixture with them and every other blocked
classification still refuse. Outside the coding path an access term is a risky tripwire signal (audit 030 FEA-002):
held for the person's explicit acceptance, never a hard block on its own."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from g6.reliability_pipeline.feasibility import deterministic_classify
from g6.intent_engine.schema import IntentContract
from g6.workforce import coding, decide, service

_spec = importlib.util.spec_from_file_location("objective_rig", Path(__file__).with_name("_objective_rig.py"))
_rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rig)
objective_rig, plain = _rig.objective_rig, _rig.plain

# COPY: Synthetic objectives, repositories and fixes for the password and credential cases.
CASES = {
    "password": ("Fix the wrong-password check and verify it.", {
        "auth.py": "def check_password(given, stored):\n    return given != stored\n",
        "tests/test_auth.py": ('from auth import check_password\n\n'
                               'def test_the_right_password_is_accepted():\n'
                               '    assert check_password("s3cret", "s3cret")\n\n'
                               'def test_a_wrong_password_is_refused():\n'
                               '    assert not check_password("guess", "s3cret")\n')},
        "def check_password(given, stored):\n    return given == stored\n"),
    # "credentials": a plural still matches, as deterministic_classify matches it
    "credential": ("Fix how expired credentials are refused and verify it.", {
        "auth.py": "def credential_valid(expires_in):\n    return True\n",
        "tests/test_auth.py": ('from auth import credential_valid\n\n'
                               'def test_an_expired_credential_is_refused():\n'
                               '    assert not credential_valid(-1)\n\n'
                               'def test_a_current_credential_is_accepted():\n'
                               '    assert credential_valid(60)\n')},
        "def credential_valid(expires_in):\n    return expires_in > 0\n"),
}


def status(rig, mid):
    found = rig.call("mission_status", mission_id=mid, detail="full")
    assert found["ok"], found
    return found


def enter(rig, case):
    objective, files, _ = CASES[case]
    rig.repository(files)
    found = rig.call("mission_create", objective=plain(objective), risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "auth_service"}))
    assert found["ok"], found
    return found["mission_id"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_password_or_credential_fix_enters_at_r3_and_plans_nothing_before_the_person(objective_rig, case):
    rig = objective_rig
    mid = enter(rig, case)
    frozen = status(rig, mid)
    derivation = frozen["acceptance"]["metadata"]["coding"]["derivation"]
    assert frozen["risk_tier"] == "R3" and derivation["effective_risk"] == "R3", derivation
    # term names only, never the objective's text (the hosted no-text rule)
    assert derivation["consequence"] == {"source": "feasibility", "signals": [case]}
    assert not frozen["units"] and not service.bind().missions.get(mid)["plan_revision"], "a plan before confirmation"
    question, = frozen["awaiting_human"]
    assert question["kind"] == "acceptance"
    assert "mentions passwords or credentials" in question["question"] and "approve the patch" in question["question"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_after_confirmation_it_proceeds_and_the_person_approves_the_checked_patch(objective_rig, monkeypatch, capsys,
                                                                                   case):
    rig = objective_rig
    mid = enter(rig, case)
    monkeypatch.setattr(decide, "_interactive", lambda: True)
    # COPY: The simulated person's recorded reason.
    confirmed = decide.main(["confirm", mid, "--reason", "synthetic human confirmation"], ask=lambda _: mid)
    refused = capsys.readouterr().err
    assert confirmed == 0 and coding.REFUSAL not in refused, f"refused at the re-intake after confirmation: {refused}"
    wf = service.bind()
    assert any(m["refs"].get("accepted_risky_intent") for m in wf.missions.messages(mid))
    brief, = status(rig, mid)["awaiting_chat"]
    waiting = rig.call("mission_submit", mission_id=mid, unit_key=brief["unit"],
                       artifact_json=json.dumps({"files": {"auth.py": CASES[case][2]}}))
    assert waiting["ok"], waiting
    assert [q["unit"] for q in waiting["awaiting_human"]] == ["approve-patch"] and waiting["status"] != "done", (
        "no approval after the checks")
    plan = wf.missions.plan(mid)[1]
    assert {u.key for u in plan.units if u.kind == "verify"} <= set(plan.upstream("approve-patch"))
    assert wf.ledger.verdicts(mid) and all(v["executed"] and v["result"] == "pass" for v in wf.ledger.verdicts(mid))
    # COPY: Simulated person's approval reason.
    assert decide.main(["approve", mid, "approve-patch", "--reason", "I reviewed the proposed change."],
                       ask=lambda _: "approve-patch") == 0
    assert status(rig, mid)["status"] == "done"


@pytest.mark.parametrize("objective", [
    # COPY: Synthetic objectives that must still be refused.
    "Fix the private key check and verify it.",
    "Fix the production access check and verify it.",
    "Fix the password check that grants production access and verify it.",
    "Fix the password check described in the attached document and verify it.",  # blocked_missing_context
    # M-1: another access term in any spelling, or another class the password word hid, still refuses
    "Fix the password check and rotate the private-key file and verify it.",
    "Fix the password check and rotate the private_key file and verify it.",
    "Fix the password check and guarantee 100% uptime and verify it.",
])
def test_private_key_production_access_and_other_blocks_are_still_refused(objective_rig, objective):
    rig = objective_rig
    rig.repository(CASES["password"][1])
    found = rig.call("mission_create", objective=plain(objective), risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "auth_service"}))
    assert not found["ok"] and found["error"] == coding.REFUSAL, found
    wf = service.bind()
    assert wf.missions.list(wf.venture_id) == []


def test_a_caller_outside_the_coding_path_still_holds_password():
    """The reliability pipeline's intake classifies through deterministic_classify, outside the coding path."""
    from g6.reliability_pipeline.intake import run_intake
    from g6.reliability_pipeline.stages import StageState

    found = run_intake(SimpleNamespace(goal=CASES["password"][0], intent_answers={
        "success_criteria": ["a wrong password is refused"]}), llm=None, clarification=True)
    assert found.state == StageState.BLOCK
    assert found.reason == "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"
    report = found.metadata["feasibility_report"]
    assert report.classification == "risky" and "access: password" in report.signals


@pytest.mark.parametrize(("term", "named"), [("password", "password"), ("passwords", "password"),
                                             ("credential", "credential"), ("credentials", "credential"),
                                             ("private key", "private key"), ("production access",
                                                                              "production access")])
def test_each_access_term_is_a_risky_signal(term, named):
    found = deterministic_classify(IntentContract(goal=f"Fix the {term} check", why="needed",
                                                  success_criteria=["observable pass"]))
    assert (found.classification, found.risk_level, found.blockers) == ("risky", "high", [])
    assert f"access: {named}" in found.signals and found.confidence is None


def test_the_classifier_control_without_an_access_term_is_clear():
    found = deterministic_classify(IntentContract(goal="Fix the label check", why="needed",
                                                  success_criteria=["observable pass"]))
    assert (found.classification, found.risk_level, found.blockers) == ("clear", "low", [])


def test_the_risky_path_is_unchanged(objective_rig):
    """R-6's risky route keeps its own consequence record, and its question carries no password reason."""
    rig = objective_rig
    rig.repository(CASES["password"][1])
    # COPY: Synthetic risky objective.
    found = rig.call("mission_create", objective=plain("Fix the payment rounding in the totals and verify it."),
                     risk_tier="R0", budget_cents=0, repo_json=json.dumps({"repo": "auth_service"}))
    assert found["ok"], found
    frozen = status(rig, found["mission_id"])
    assert frozen["risk_tier"] == "R3"
    assert frozen["acceptance"]["metadata"]["coding"]["derivation"]["consequence"] == {
        "source": "feasibility", "signals": {"classification": "risky", "risk_level": "high",
                                             "recommended_next_step": "answer_reduction_to_practice_questions"}}
    assert "passwords or credentials" not in frozen["awaiting_human"][0]["question"]


def test_different_terms_at_the_re_intake_are_refused(objective_rig, monkeypatch, capsys):
    """M-2: the re-intake accepts only the terms frozen at entry; here the term list changes between the freeze and
    the person's confirmation, so the re-intake matches fewer terms and refuses."""
    rig = objective_rig
    rig.repository(CASES["password"][1])
    # COPY: Synthetic objective with both accepted terms.
    found = rig.call("mission_create", objective=plain("Fix the password and credential checks and verify it."),
                     risk_tier="R0", budget_cents=0, repo_json=json.dumps({"repo": "auth_service"}))
    assert found["ok"], found
    mid = found["mission_id"]
    signals = status(rig, mid)["acceptance"]["metadata"]["coding"]["derivation"]["consequence"]["signals"]
    assert signals == ["password", "credential"]
    monkeypatch.setattr(coding, "ACCESS_TERMS", ("password", "private key", "production access"))
    monkeypatch.setattr(decide, "_interactive", lambda: True)
    # COPY: The simulated person's recorded reason.
    confirmed = decide.main(["confirm", mid, "--reason", "synthetic human confirmation"], ask=lambda _: mid)
    assert confirmed == 2 and coding.REFUSAL in capsys.readouterr().err, "different terms were accepted at re-intake"


def test_no_recorded_confirmation_at_the_re_intake_is_refused(objective_rig, monkeypatch):
    """M-2: the re-intake checks the confirmation itself, not only advance's early return; here the confirmation is
    seen by that early return and gone by the re-intake."""
    from g6.workforce.missions import MissionStore

    rig = objective_rig
    mid = enter(rig, "password")
    wf = service.bind()
    # COPY: The simulated person's recorded reason.
    wf.runtime.confirm_acceptance(mid, reason="synthetic human confirmation", actor="human", decided_by="terminal:alice")
    answers = iter([True])
    monkeypatch.setattr(MissionStore, "confirmed", lambda self, mission_id: next(answers, False))
    with pytest.raises(ValueError, match=coding.REFUSAL):
        coding.advance(wf, mid)


@pytest.mark.parametrize("shape", ["dropped", "before_the_checks", "kept"])
def test_a_plan_that_drops_or_bypasses_the_approval_is_refused(objective_rig, shape):
    """The lead's check: after the person confirms an R3 password fix and before G6 stores its own plan, the chat's
    plan without the approval gate after every check is refused, so nothing runs the patch past the person. Control
    ("kept"): the same plan with the gate after the check is stored, so a refusal is about the approval."""
    rig = objective_rig
    mid = enter(rig, "password")
    wf = service.bind()
    # COPY: The simulated person's recorded reason.
    wf.runtime.confirm_acceptance(mid, reason="synthetic human confirmation", actor="human", decided_by="terminal:alice")
    standard = coding.frozen(wf, mid)
    check = {"key": "check-patch", "kind": "verify", "verifies": "patch", "goal": "The frozen tests pass",
             "max_attempts": 1, "capabilities": [["workforce", "run_tests"]],
             "oracle": {"capability": ["workforce", "run_tests"],
                        "params": {"repo": standard["snapshot"], "tests": standard["required_tests"]},
                        "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True},
                        "kind": "test"}}
    regression = {**check, "key": "check-regression", "oracle": {**check["oracle"], "kind": "deterministic",
        "params": {**check["oracle"]["params"], "regression": {
            **standard["task_signature"], "declared": standard.get("declared", [])}}}}
    units = [{"key": "patch", "kind": "do", "goal": "Fix it", "output_schema": "files", "risk": "R3",
              "budget_cents": 0, "max_attempts": 3}, check, regression]
    if shape != "dropped":  # "before_the_checks": a gate the checks do not lead to
        units.append({"key": "approve-patch", "kind": "gate", "inputs": ["patch"], "goal": "Approve the patch."})
    edges = [[key, "approve-patch"] for key in ("check-patch", "check-regression")] if shape == "kept" else []
    found = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps({"units": units, "edges": edges}))
    if shape == "kept":
        assert found["ok"] and wf.missions.get(mid)["plan_revision"], found
        return
    assert not found["ok"], f"a plan without the approval after the checks was stored: {found}"
    assert found["problems"] == [coding.APPROVAL_KEPT], found  # The gate alone caused the refusal.
    assert not wf.missions.get(mid)["plan_revision"]
