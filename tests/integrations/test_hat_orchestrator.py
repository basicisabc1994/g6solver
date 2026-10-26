# SPDX-License-Identifier: Apache-2.0
"""Orchestration through the block: declarations stay claims and authority stays operator-owned."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from g6.integrations.hat_orchestrator import HATOrchestratorBlock, HATOrchestratorInput


def allow(action):
    return SimpleNamespace(gate_decision=SimpleNamespace(decision="allow", reason_codes=[], required_user_message=""))


def call(block, op, **values):
    return block.infer(HATOrchestratorInput(op=op, **values)).unwrap()


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    block = HATOrchestratorBlock(roster={"editor": 2, "reviewer": 7}, informational_actions={"review"}, gate=allow)
    for agent, capabilities in (("editor", ["write"]), ("reviewer", ["review"])):
        call(block, "register_agent", agent_id=agent, capabilities=capabilities, available=True, dry_run=False)
    return block


def test_capability_allocation_and_sync_through_block(team):
    result = call(team, "allocate_task", task_id="draft-42", required_capabilities=["review"], dry_run=False)
    assert result["task"]["assigned_agent_id"] == "reviewer"
    assert result["task"]["basis"] == "declared_capabilities_and_availability"
    call(team, "create_sync_point", sync_point_id="review-barrier", required_agents=["editor", "reviewer"], dry_run=False)
    call(team, "signal_ready", sync_point_id="review-barrier", agent_id="editor", dry_run=False)
    assert call(team, "check_sync", sync_point_id="review-barrier")["sync"]["all_reported_ready"] is False
    call(team, "signal_ready", sync_point_id="review-barrier", agent_id="reviewer", dry_run=False)
    assert call(team, "check_sync", sync_point_id="review-barrier")["sync"]["all_reported_ready"] is True
    assert call(HATOrchestratorBlock(), "check_sync", sync_point_id="review-barrier")["sync"]["all_reported_ready"] is True
    # Removal of capability matching, participant readiness, or persistence makes this test fail.


@pytest.mark.parametrize("op,status", [("complete_task", "completed_claim"), ("fail_task", "failed_claim")])
def test_terminal_reports_remain_agent_claims(team, op, status):
    call(team, "allocate_task", task_id="report-6", required_capabilities=["write"], dry_run=False)
    result = call(team, op, task_id="report-6", agent_id="editor", claim={"tests_passed": True}, dry_run=False)
    assert result["completion_state"] == "qualified-draft"
    assert result["task"]["status"] == status
    assert result["task"]["reporter_claim"] == "editor"
    assert result["task"]["result_claim"] == {"tests_passed": True}
    # Removal of status's _claim suffix or the qualified-draft envelope fails this test (HAT_ORCHESTRATOR-01).


def test_d1_authority_cannot_be_self_assigned(team):
    for field in ("authority_level", "rank", "approved", "agent_type", "real_world"):
        with pytest.raises(ValidationError):
            HATOrchestratorInput(op="register_agent", agent_id="editor", **{field: 99})
    result = call(team, "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action="review")
    assert result["winner"] == "reviewer" and result["basis"] == "operator_declared_rank"
    assert result["completion_state"] == "qualified-draft"
    # Removal of extra="forbid" makes this D1 test fail (HAT_ORCHESTRATOR-02).


def test_real_world_and_tied_authority_escalate_even_when_gate_allows(team):
    actions = []
    def observe(action):
        actions.append(action)
        return allow(action)
    team.gate = observe
    real = call(team, "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action="publish", dry_run=False)
    assert real["completion_state"] == "blocked-escalated" and "winner" not in real
    tied = HATOrchestratorBlock(roster={"editor": 2, "reviewer": 2}, informational_actions={"review"}, gate=observe)
    result = call(tied, "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action="review", dry_run=False)
    assert result["code"] == "G6_E_HAT_AUTHORITY_ESCALATED" and len(actions) == 2
    assert actions[0]["real_world_effect"] is True
    # Removal of tie/unknown-action escalation makes this test fail (HAT_ORCHESTRATOR-02).


def test_missing_roster_does_not_create_authority(team):
    result = call(HATOrchestratorBlock(gate=allow), "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action="review")
    assert result["completion_state"] == "blocked-escalated" and "winner" not in result
    # Removal of the roster membership check makes this test fail.


def test_default_dry_run_and_failed_gate_leave_state_unchanged(team):
    dry = call(team, "register_agent", agent_id="intruder", capabilities=["write"])
    assert dry["dry_run"] is True
    def broken(action):
        raise OSError("policy unavailable")
    team.gate = broken
    held = call(team, "register_agent", agent_id="intruder", capabilities=["write"], dry_run=False)
    assert held["code"] == "G6_E_HAT_GATE_HELD"
    assert "intruder" not in team.store.read().agents
    # Removal of dry_run or the gate decision check makes this test fail.


def test_empty_sync_and_mismatched_reporter_are_refused(team):
    with pytest.raises(ValidationError):
        HATOrchestratorInput(op="create_sync_point", sync_point_id="empty", required_agents=[])
    call(team, "allocate_task", task_id="write-it", required_capabilities=["write"], dry_run=False)
    result = call(team, "complete_task", task_id="write-it", agent_id="reviewer", dry_run=False)
    assert result["completion_state"] == "blocked-escalated"
    # Removal of participant validation or assigned reporter matching makes this test fail.


def test_corrupt_or_oversize_state_fails_closed(team):
    team.store.path.write_text('{"agents": "broken"}', encoding="utf-8")
    result = call(team, "check_sync", sync_point_id="x")
    assert result["completion_state"] == "blocked-escalated"
    team.store.path.write_bytes(b" " * (team.store.max_bytes + 1))
    result = call(team, "check_sync", sync_point_id="x")
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    # Removal of Store.read's model validation or byte cap makes this test fail.


def test_capability_mismatch_cannot_allocate(team):
    result = call(team, "allocate_task", task_id="unknown", required_capabilities=["translate"], dry_run=False)
    assert result["code"] == "G6_E_HAT_NO_MATCH"
    assert "unknown" not in team.store.read().tasks
    # Removal of required_capabilities.issubset makes this test fail.


def test_record_count_limit_and_write_lock_fail_closed(team):
    from g6.integrations.hat_orchestrator.state import Agent
    with team.store.transaction() as snapshot:
        for index in range(254):
            name = f"agent-{index}"
            snapshot.agents[name] = Agent(agent_id=name, capabilities=[], available=False)
    result = call(team, "register_agent", agent_id="overflow", dry_run=False)
    assert result["code"] == "G6_E_RESOURCE_LIMIT"
    lock = team.store.path.parent / "write.lock"
    lock.write_text("operator must inspect interrupted writer", encoding="utf-8")
    result = call(team, "register_agent", agent_id="editor", dry_run=False)
    assert result["code"] == "G6_E_HAT_STATE_BUSY"
    assert lock.exists()
    # Removal of the record cap or exclusive writer lock makes this test fail.


def test_operator_roster_file_is_used_and_registration_cannot_rewrite_it(team):
    import json
    policy = team.store.path.parent / "operator-roster.json"
    policy.write_text(json.dumps({"roster": {"editor": 91, "reviewer": 3},
                                  "informational_actions": ["review"]}), encoding="utf-8")
    block = HATOrchestratorBlock(gate=allow)
    call(block, "register_agent", agent_id="reviewer", capabilities=["write", "review"], dry_run=False)
    result = call(block, "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action="review")
    assert result["winner"] == "editor"
    assert json.loads(policy.read_text(encoding="utf-8"))["roster"]["reviewer"] == 3
    # Removal of operator-roster loading or replacing ranks with capability scores makes this test fail.


@pytest.mark.parametrize("ranks,action", [({"editor": 2, "reviewer": 2}, "review"),
                                         ({"editor": 2, "reviewer": 7}, "publish")])
def test_authority_dry_run_proposes_escalation_without_gate_or_writes(team, tmp_path, monkeypatch, ranks, action):
    from g6 import action_gating

    # Seeded by team; use the real default evaluator, with a spy that would forward to it.
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    evaluator = action_gating.evaluate_action
    calls = []
    def observe(*args, **kwargs):
        calls.append(args)
        return evaluator(*args, **kwargs)
    monkeypatch.setattr(action_gating, "evaluate_action", observe)
    block = HATOrchestratorBlock(roster=ranks, informational_actions={"review"})
    result = call(block, "resolve_authority", agent_a_id="editor", agent_b_id="reviewer", action=action)
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert calls == [] and after == before
    assert result["dry_run"] is True and result["action_executed"] is False
    assert result["proposed_escalation"]["action_type"] == "run_code"
    assert result["completion_state"] == "blocked-escalated" and "gate" not in result
    # Removal of `_authority`'s dry-run return before `consult` makes this test fail.