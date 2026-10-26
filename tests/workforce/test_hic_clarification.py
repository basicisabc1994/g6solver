# SPDX-License-Identifier: Apache-2.0
"""Task semantics must be resolved before executing customer repository code."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
from g6.workforce import truth
from g6.workforce import acceptance, bench, service
from g6.workforce.missions import MissionStore

_spec = importlib.util.spec_from_file_location("hic_clarify_fixture", Path(__file__).with_name("test_harness_intelligence.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
objective_rig = _fixture.objective_rig
bounded_development_checks = _fixture.bounded_development_checks

OBJECTIVE = "Make this Python inventory event handler idempotent."
REPOSITORY = {"repo": "sample", "module": "inventory.py", "function": "apply_event"}
ANSWER = {"replay_semantics": "identical_nonadjacent_in_memory"}


def prepare(rig):
    (rig.repo / "policy.py").write_text(_fixture._fixture.AFTER, encoding="utf-8")
    for name in ("inventory.py", "tests/test_inventory.py"):
        (rig.repo / name).write_bytes((_fixture.POPULATION / name).read_bytes())
    for args in (("add", "."), ("-c", "user.name=Codex", "-c", "user.email=codex@example.invalid",
                                "commit", "-m", "authored clarification control")):
        subprocess.run(["git", *args], cwd=rig.repo, check=True, capture_output=True)


def test_ambiguous_replay_mcp_preserves_mission_and_asks_before_repository_execution(objective_rig, monkeypatch):
    rig = objective_rig
    prepare(rig)
    called = []
    original = truth.run_suite

    def observe(*args, **kwargs):
        called.append(str(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(truth, "run_suite", observe)
    made = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    print(json.dumps({"case": "hic01-before-clarification", "reply": made, "repository_executions": called}))
    assert made["ok"], made
    assert called == [], "Material replay semantics were unresolved when repository code executed"
    mid = made["mission_id"]
    assert made["status"] == "eliciting" and made["completion_state"] == ""
    assert made["interaction_state"] == "awaiting_input"
    question = made["clarification"]
    assert question["mission_id"] == mid
    assert "identical" in question["question"].lower() and "different event" in question["question"].lower()
    assert question["next_action"]["tool"] == "mission_accept"
    assert question["next_action"]["arguments"]["mission_id"] == mid
    # The fixture uses a new basic MCP Client for each call; state cannot depend on its session.
    again = rig.call("mission_status", mission_id=mid)
    assert again["clarification"] == question and again["objective"] == OBJECTIVE
    wf = service.bind()
    assert wf.missions.get(mid)["acceptance"] is None
    assert wf.ledger.runs(mid) == []
    assert called == []


def test_pending_question_does_not_claim_old_acceptance_or_zero_owner_effort(objective_rig):
    rig = objective_rig
    mid = create(rig)
    reply = rig.call("mission_status", mission_id=mid)
    words = reply["result"]["markdown"]
    assert "older record" not in words
    assert "nothing was asked of you" not in words
    assert "intake/preparation" in words and "unmeasured" in words


def test_question_discloses_that_answering_starts_preparation_checks(objective_rig):
    rig = objective_rig
    mid = create(rig)
    reply = rig.call("mission_status", mission_id=mid)
    assert "starts repository preparation checks" in reply["clarification"]["approval"]
    assert "preparation checks" in reply["result"]["markdown"]


def test_cancelled_unaccepted_question_is_not_reported_as_historical_acceptance(objective_rig):
    rig = objective_rig
    mid = create(rig)
    service.bind().missions.cancel(mid, reason="fixture cancellation before clarification", actor="chat")
    reply = rig.call("mission_status", mission_id=mid)
    assert reply["ok"] and reply["status"] == "cancelled"
    assert "older record" not in reply["result"]["markdown"]


def create(rig):
    prepare(rig)
    made = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    assert made["ok"], made
    assert made.get("interaction_state") == "awaiting_input", made
    return made["mission_id"]


def test_answer_resumes_original_snapshot_same_mission_and_preserves_approval(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    # Original material must survive both reconnects and later owner-worktree edits.
    bad = (rig.repo / "inventory.py").read_text(encoding="utf-8")
    (rig.repo / "inventory.py").write_text("def apply_event(state, event): return state\n", encoding="utf-8")
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    print(json.dumps({"case": "hic01-resumed", "reply": reply}))
    assert reply["ok"] and reply["mission_id"] == mid and reply["objective"] == OBJECTIVE
    assert reply["status"] == "awaiting_plan" and reply["awaiting_chat"] == []
    assert [q["kind"] for q in reply["awaiting_human"]] == ["acceptance"]
    wf = service.bind()
    standard = wf.missions.acceptance(mid)
    coding = standard.metadata["coding"]
    assert coding["intake_clarification"]["answer"] == ANSWER
    assert (bench._repo(coding["snapshot"], wf.venture_id) / "inventory.py").read_text(encoding="utf-8") == bad
    assert coding["harness_intelligence"]["checks"]
    assert coding["harness_intelligence"]["task_sha256"]
    assert mid in wf.missions.earlier(wf.venture_id, template="code_change",
                                      task_sha256=coding["task_signature"]["sha256"])
    assert not wf.missions.confirmed(mid)

    def forbidden(*args, **kwargs):
        pytest.fail("Repeated acceptance must not execute repository code")

    with monkeypatch.context() as duplicate:
        duplicate.setattr(truth, "run_suite", forbidden)
        again = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
        assert again["ok"] and again["status"] == "awaiting_plan"
        assert not rig.call("mission_accept", mission_id=mid, contract_json=json.dumps({"replay_semantics": "different"}))["ok"]
    assert wf.missions.acceptance(mid).sha256 == standard.sha256
    rig.human(mid)
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"inventory.py": bad}}))
    assert failed["ok"] and failed["status"] == "waiting", failed
    assert any(v["executed"] and v["result"] == "fail" for v in wf.ledger.verdicts(mid))
    assert wf.missions.acceptance(mid).sha256 == standard.sha256
    assert "time in chat is not measured" in failed["result"]["markdown"]
    print(json.dumps({"case": "hic01-enforced-after-answer", "reply": failed}))


def test_a_chat_answer_is_recorded_as_the_chats_never_as_the_users(objective_rig):
    from g6.workforce import readiness

    rig = objective_rig
    mid = create(rig)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert reply["ok"], reply
    wf = service.bind()
    contexts = [e["detail"]["refs"]["readiness"] for e in wf.missions.events(mid, kind="message")
                if "readiness" in e["detail"].get("refs", {})]
    assert contexts and contexts[-1]["interpretation"] == {"key": ANSWER["replay_semantics"], "source": "chat_answer"}
    assert readiness.state(wf.missions, mid).interpretation["source"] == "chat_answer"


@pytest.mark.parametrize("contract", [None, {"replay_semantics": "concurrent_or_restart"},
                                      {"goal": "Just pass any test", "success_criteria": ["done"]}])
def test_unresolved_answers_and_manual_paths_cannot_bypass_question(objective_rig, monkeypatch, contract):
    rig = objective_rig
    mid = create(rig)

    def forbidden(*args, **kwargs):
        pytest.fail("Unresolved semantics must not execute repository code")

    monkeypatch.setattr(truth, "run_suite", forbidden)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(contract) if contract else "")
    wf = service.bind()
    assert wf.missions.get(mid)["status"] == "eliciting"
    assert wf.missions.get(mid)["acceptance"] is None
    assert not rig.call("mission_plan", mission_id=mid, plan_json=json.dumps({"units": [], "edges": []}))["ok"]
    rig.call("mission_run", mission_id=mid)
    assert wf.ledger.runs(mid) == []
    # Even a caller below the MCP service cannot accept a generic contract over the unresolved coding intake.
    compiled = acceptance.compile_contract(objective=OBJECTIVE, contract=wf.missions.contract(mid), risk_tier="R2", budget_cents=0,
                                            template_derived=True)
    with pytest.raises(ValueError, match="clarification"):
        wf.missions.accept(mid, compiled, actor="chat")
    print(json.dumps({"case": "hic01-unresolved", "input": contract, "reply": reply}))


def test_interrupted_preparation_does_not_repeat_repository_execution(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("injected interruption at repository dispatch")

    monkeypatch.setattr(truth, "run_suite", interrupted)
    first = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    again = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert calls == [True]
    assert service.bind().missions.get(mid)["acceptance"] is None
    assert "mission_create" in json.dumps(again)
    print(json.dumps({"case": "hic01-interrupted-preparation", "first": first, "again": again}))


def test_broader_answer_is_preserved_without_repeating_the_narrow_question(objective_rig):
    rig = objective_rig
    mid = create(rig)
    answer = {"replay_semantics": "Must also survive concurrent delivery and restart"}
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(answer))
    assert reply["ok"] and reply["interaction_state"] == "attention_required", reply
    assert reply["clarification"]["answer"] == answer
    assert reply["clarification"]["state"] == "unsupported_requirement"
    assert "not covered" in reply["clarification"]["problem"]
    again = rig.call("mission_status", mission_id=mid)
    assert again["clarification"] == reply["clarification"]
    assert service.bind().missions.get(mid)["acceptance"] is None


def test_answer_without_constructible_check_cannot_drop_the_required_predicate(objective_rig):
    rig = objective_rig
    prepare(rig)
    path = rig.repo / "tests/test_inventory.py"
    path.write_text(path.read_text(encoding="utf-8").replace('assert apply_event(', 'assert target(').replace(
        "from inventory import apply_event", "from inventory import apply_event\ntarget = apply_event"), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=rig.repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Codex", "-c", "user.email=codex@example.invalid", "commit", "-m", "unsupported example shape"],
                   cwd=rig.repo, check=True, capture_output=True)
    made = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps(REPOSITORY))
    mid = made["mission_id"]
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert service.bind().missions.get(mid)["acceptance"] is None, reply
    assert "construct" in reply["clarification"]["problem"]


def test_different_owner_cannot_read_or_answer_question(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    monkeypatch.setenv(service.ENV_OWNER, "bob")
    assert not rig.call("mission_status", mission_id=mid)["ok"]
    assert not rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))["ok"]


def test_snapshot_mutation_cannot_start_repository_code(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    wf = service.bind()
    request = wf.missions.events(mid, kind="mission.created")[0]["detail"]["materials"]["coding_intake"]
    snapshot = bench._repo(request["snapshot"], wf.venture_id)
    (snapshot / "inventory.py").write_text("def apply_event(*args): return {}\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Changed retained material must not execute")

    monkeypatch.setattr(truth, "run_suite", forbidden)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert not reply["ok"] and "snapshot changed" in json.dumps(reply)
    assert wf.missions.get(mid)["acceptance"] is None


def test_preparation_cannot_accept_bytes_mutated_by_a_repository_test(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    original = truth.run_suite

    def mutate_after_check(*args, **kwargs):
        found = original(*args, **kwargs)
        prepared = list(rig.repo.parent.glob("clarified-*"))
        assert len(prepared) == 1
        (prepared[0] / "inventory.py").write_text("def apply_event(*args): return {}\n", encoding="utf-8")
        return found

    monkeypatch.setattr(truth, "run_suite", mutate_after_check)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert service.bind().missions.get(mid)["acceptance"] is None, reply
    assert "changed" in reply["clarification"]["problem"], reply


def test_broader_answer_winning_dispatch_race_cannot_be_narrowed(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    record = MissionStore.record_event
    inserted = []

    def interleave(store, mission_id, kind, **kwargs):
        if kind == "message" and kwargs.get("detail", {}).get("refs", {}).get("answer") == ANSWER and not inserted:
            inserted.append(True)
            service.accept(service.bind(), mid, {"replay_semantics": "Restart and concurrency are mandatory"})
        return record(store, mission_id, kind, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("A losing answer must not dispatch repository code")

    monkeypatch.setattr(MissionStore, "record_event", interleave)
    monkeypatch.setattr(truth, "run_suite", forbidden)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert inserted and not reply["ok"], reply
    assert service.bind().missions.get(mid)["acceptance"] is None


def test_same_answer_losing_atomic_claim_cannot_execute_preparation_twice(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    record = MissionStore.record_event
    run = truth.run_suite
    interleaved, executions = [], []

    def interleave(store, mission_id, kind, **kwargs):
        if kind == "message" and kwargs.get("detail", {}).get("refs", {}).get("answer") == ANSWER and not interleaved:
            interleaved.append(True)
            won = service.accept(service.bind(), mid, ANSWER)
            assert won["status"] == "awaiting_plan"
        return record(store, mission_id, kind, **kwargs)

    def observe(*args, **kwargs):
        executions.append(True)
        return run(*args, **kwargs)

    monkeypatch.setattr(MissionStore, "record_event", interleave)
    monkeypatch.setattr(truth, "run_suite", observe)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert reply["ok"] and reply["status"] == "awaiting_plan", reply
    assert executions == [True]


def test_conflicting_answer_after_dispatch_is_explicitly_refused(objective_rig, monkeypatch):
    rig = objective_rig
    mid = create(rig)
    original = truth.run_suite
    conflicts = []
    duplicates = []

    def observe(*args, **kwargs):
        # A reconnected identical caller must not recursively run this suite again.
        duplicate = service.accept(service.bind(), mid, ANSWER)
        duplicates.append(duplicate["clarification"]["state"])
        try:
            service.accept(service.bind(), mid, {"replay_semantics": "Concurrency is mandatory"})
        except ValueError:
            conflicts.append("refused")
        return original(*args, **kwargs)

    monkeypatch.setattr(truth, "run_suite", observe)
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert conflicts == ["refused"], reply
    assert duplicates == ["preparation_recorded"]
    assert reply["ok"] and reply["status"] == "awaiting_plan"


def test_critical_mode_construction_receipt_binds_the_stored_strengthened_standard(objective_rig):
    rig = objective_rig
    prepare(rig)
    made = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps({**REPOSITORY, "mode": "critical"}))
    mid = made["mission_id"]
    reply = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps(ANSWER))
    assert reply["ok"] and reply["status"] == "awaiting_plan", reply
    wf = service.bind()
    accepted = wf.missions.acceptance(mid)
    assert wf.missions.mode(mid) == "critical"
    assert "person_approved" in accepted.verification_requirements
    receipt = next(m["refs"]["harness_construction"] for m in wf.missions.messages(mid)
                   if "harness_construction" in m["refs"])
    assert receipt["standard_sha256"] == accepted.sha256
