# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The skeleton mission, end to end on fake components (workforce OS plan, P0 T6).

Pinned: a mission runs plan > units > a do unit on the deterministic rung > a SEPARATE read-only
verifier instance on a model-free oracle > roll-up, through the single door and the attempt ledger,
and closes ``verified`` only then; a failed attempt is retried within its unit's attempts and an
exhausted unit closes the mission blocked-escalated; a unit whose verifier has nothing to judge is
never verified; every step leaves its history. Offline, temp dbs, fake components.
"""
from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import plans as wp
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
from g6.workforce import workers as ww

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"}, "probe_params": {"numbers": [1]},
        "expect_field": "total", "side_effect": "read", "check": "input_model"}),
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True}),
}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class World:
    """The fake components: a ledger that totals and a solver that checks a total (sat = it is wrong)."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.ledger_failures = 0  # the first N ledger calls raise

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op, dict(params)))
        if component == "fake_ledger":
            if self.ledger_failures:
                self.ledger_failures -= 1
                raise ConnectionError("the ledger export is locked")
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
        if component == "fake_solver":
            return {"ok": True, "completion_state": "qualified-draft",
                    "result": {"satisfiable": params["total"] != params["expected"]}}
        raise AssertionError(f"unexpected call {component}.{op}")


def _plan(max_attempts: int = 1, oracle: bool = True) -> dict:
    check = {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is the ledger's 6",
             "capabilities": [["fake_solver", "check"]]}
    if oracle:
        check["oracle"] = {"capability": ["fake_solver", "check"], "params": {"expected": 6},
                           "bind": {"total": "sum.total"}, "expect": {"field": "satisfiable", "equals": False},
                           "kind": "formal"}
    return {"units": [
        {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
         "max_attempts": max_attempts,
         "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}},
        check], "edges": []}


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner("alice"))
    world = World()
    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts=CONTRACTS, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in CONTRACTS:
        assert caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    world.calls.clear()
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)

    def mission(plan: dict) -> str:
        mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0,
                              actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, plan, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world)
    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, caps=caps, world=world,
                           runtime=runtime, mission=mission)


def test_the_skeleton_mission_closes_verified_only_through_a_separate_verifier(rig):
    mid = rig.mission(_plan())
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED), final["outcome"]
    assert [c[:2] for c in rig.world.calls] == [("fake_ledger", "total"), ("fake_solver", "check")]
    assert rig.world.calls[1][2] == {"expected": 6, "total": 6}  # the verifier read the producer's artifact
    do_run, = rig.ledger.runs(mid, "sum")
    check_run, = rig.ledger.runs(mid, "check-sum")
    assert do_run["instance_id"] != check_run["instance_id"]
    assert rig.ledger.instance(mid, check_run["instance_id"])["read_only"] == 1
    assert rig.ledger.instance(mid, do_run["instance_id"])["read_only"] == 0
    verdict, = rig.ledger.verdicts(mid, "sum")
    assert (verdict["state"], verdict["artifact_id"], verdict["producer_instance"]) == (
        wc.VERIFIED, do_run["artifact_id"], do_run["instance_id"])
    assert do_run["completion_state"] == wc.QUALIFIED  # a produced output is a draft until judged
    kinds = [e["kind"] for e in rig.missions.events(mid)]
    for kind in ("plan.revision", "unit.materialized", "run.opened", "artifact", "verdict", "run.closed",
                 "mission.closed"):
        assert kind in kinds


def test_a_wrong_answer_fails_its_verifier_and_the_mission_is_blocked(rig):
    plan = _plan()
    plan["units"][0]["call"]["params"]["numbers"] = [1, 2, 4]
    mid = rig.mission(plan)
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED)
    verdict, = rig.ledger.verdicts(mid, "sum")
    assert verdict["result"] == "fail" and verdict["state"] == wc.BLOCKED


def test_a_failed_attempt_is_retried_within_the_units_attempts(rig):
    rig.world.ledger_failures = 1
    mid = rig.mission(_plan(max_attempts=2))
    final = rig.runtime.run(mid)
    assert final["completion_state"] == wc.VERIFIED
    first, second = rig.ledger.runs(mid, "sum")
    assert (first["completion_state"], second["completion_state"]) == (wc.BLOCKED, wc.QUALIFIED)
    assert "ConnectionError" in first["outcome_json"]


def test_an_exhausted_unit_closes_the_mission_blocked_escalated(rig):
    rig.world.ledger_failures = 5
    mid = rig.mission(_plan(max_attempts=2))
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED)
    assert len(rig.ledger.runs(mid, "sum")) == 2 and rig.ledger.runs(mid, "check-sum") == []
    assert any("sum" in r for r in final["outcome"]["reasons"])


def test_a_verifier_without_an_oracle_never_verifies(rig):
    mid = rig.mission(_plan(oracle=False))
    final = rig.runtime.run(mid)
    assert final["completion_state"] != wc.VERIFIED
    assert all(v["state"] != wc.VERIFIED for v in rig.ledger.verdicts(mid))


def test_an_unmeasured_acceptance_check_holds_the_mission_at_qualified_draft(rig):
    mid = rig.mission(_plan())
    contract = rig.missions.contract(mid)
    assert contract.checkpoints == []
    # A mission whose contract names an acceptance check that no verifier in P0 measures:
    rig.missions.conn.execute("UPDATE missions SET contract_json=? WHERE mission_id=?", (
        contract.model_copy(update={"checkpoints": [{"name": "the books balance", "predicate": "ledger.balanced"}]}
                            ).model_dump_json(), mid))
    rig.missions.conn.commit()
    final = rig.runtime.run(mid)
    assert final["completion_state"] == wc.QUALIFIED
    assert any("the books balance" in r for r in final["outcome"]["reasons"])


def test_step_does_nothing_for_a_mission_that_is_not_active(rig):
    mid = rig.mission(_plan())
    rig.runtime.run(mid)
    assert rig.runtime.step(mid) is None
    assert rig.missions.get(mid)["status"] == "done"


# --- Codex review of T4-T6 ------------------------------------------------------------------------


def _unbound_oracle(plan: dict) -> dict:
    """Codex's shape: the oracle checks constants, never the artifact it certifies."""
    plan["units"][0]["call"]["params"]["numbers"] = [1, 2, 4]  # the producer's total is 7, not 6
    plan["units"][1]["oracle"].update(bind={}, params={"total": 6, "expected": 6})
    return plan


def test_a_plan_whose_oracle_does_not_read_the_artifact_it_judges_is_refused(rig):
    with pytest.raises(wp.PlanRejected) as refused:
        rig.mission(_unbound_oracle(_plan()))
    assert any("check-sum" in p and "must read the artifact it judges" in p for p in refused.value.problems)


def test_an_oracle_that_does_not_read_the_artifact_never_verifies_even_past_the_validator(rig, monkeypatch):
    mid = rig.mission(_plan())
    _rev, plan = rig.missions.plan(mid)
    check = plan.unit("check-sum")
    loose = dataclasses.replace(check, oracle={**check.oracle, "bind": {}, "params": {"total": 6, "expected": 6}})
    monkeypatch.setattr(rig.runtime, "_plan", lambda mission: wp.Plan(
        tuple(loose if u.key == "check-sum" else u for u in plan.units), plan.edges, plan.sha256))
    final = rig.runtime.run(mid)
    assert final["completion_state"] != wc.VERIFIED
    assert all(v["state"] != wc.VERIFIED for v in rig.ledger.verdicts(mid))


@pytest.mark.parametrize("inner", [{"completion_state": "blocked-escalated"}, {"degraded": True}])
def test_an_oracle_reply_that_is_blocked_or_degraded_inside_leaves_no_verified_verdict(rig, monkeypatch, inner):
    """Codex: satisfiable=false (the check holds) with blocked-escalated inside result.result: the run and the
    mission were blocked, yet the verdict row said verified."""
    real = rig.world.__call__

    def world(component, op, params):
        reply = real(component, op, params)
        if component == "fake_solver":
            reply["result"]["result"] = dict(inner)
        return reply

    rig.runtime._invoke = world
    mid = rig.mission(_plan())
    final = rig.runtime.run(mid)
    assert final["completion_state"] != wc.VERIFIED
    verdict, = rig.ledger.verdicts(mid, "sum")
    check_run, = rig.ledger.runs(mid, "check-sum")
    assert verdict["state"] == check_run["completion_state"] != wc.VERIFIED


def test_an_unmeasured_unit_check_holds_the_mission_at_qualified_draft(rig):
    plan = _plan()
    plan["units"][1]["checks"] = [{"name": "required suite", "predicate": "coder.run_tests", "params": {"all": True}}]
    mid = rig.mission(plan)
    final = rig.runtime.run(mid)
    assert final["completion_state"] == wc.QUALIFIED
    assert any("required suite" in r for r in final["outcome"]["reasons"])


def test_an_open_attempt_is_never_raced_and_is_retried_once_its_runner_is_gone(rig, monkeypatch):
    """Codex: a crash between opening a run and claiming its unit, then an immediate restart. While a live
    runner holds the mission, no other runner steps it at all, so nothing races the open attempt. Once the
    lease is free the attempt's runner is gone (its run can never close), so the next runner abandons it at
    once and retries (V2a; P0 waited out its wall clock); the mission closes verified."""
    mid = rig.mission(_plan(max_attempts=2))
    real_claim = rig.graph.claim

    def crash(*args, **kwargs):
        raise RuntimeError("injected crash after the run opened")

    monkeypatch.setattr(rig.graph, "claim", crash)
    with pytest.raises(RuntimeError, match="injected crash"):
        rig.runtime.step(mid)
    monkeypatch.setattr(rig.graph, "claim", real_claim)
    first, = rig.ledger.open_runs(mid)
    live = rig.missions.take_lease(mid, holder="runner:live")
    with pytest.raises(wm.LeaseHeld):
        rig.runtime.run(mid)
    assert [r["run_id"] for r in rig.ledger.runs(mid, "sum")] == [first["run_id"]]
    rig.missions.release_lease(live)
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    assert [r["state"] for r in rig.ledger.runs(mid, "sum")] == ["abandoned", "closed"]


def test_a_mission_never_closes_while_an_attempt_is_open(rig):
    """The unit's only attempt is open without a runner lease (the chat's own submission, in flight) and
    within its wall clock: nothing is left to try, yet the mission waits for that attempt instead of closing
    around it, and closes only once the attempt is abandoned at its wall clock."""
    mid = rig.mission(_plan(max_attempts=1))
    worker = ww.form_worker("doer", ceiling=wrt.ceiling_for(rig.missions.get(mid)), contracts=rig.caps.contract,
                            unit=rig.missions.plan(mid)[1].unit("sum"))
    rig.ledger.open(mid, "sum", worker)
    assert rig.runtime.run(mid)["status"] == "active"
    later = wrt.Runtime(rig.missions, rig.graph, rig.ledger, rig.caps, invoke=rig.world,
                        now=lambda: datetime.now(UTC) + timedelta(hours=1))
    final = later.run(mid)  # abandoned once its wall clock ran out; its one attempt is spent
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED)
    assert rig.ledger.open_runs(mid) == []


def test_a_unit_has_one_open_attempt_and_runs_open_only_on_an_active_mission(rig):
    mid = rig.mission(_plan())
    worker = ww.form_worker("doer", ceiling=wrt.ceiling_for(rig.missions.get(mid)), contracts=rig.caps.contract,
                            unit=rig.missions.plan(mid)[1].unit("sum"))
    run = rig.ledger.open(mid, "sum", worker)
    with pytest.raises(ValueError, match="open attempt"):
        rig.ledger.open(mid, "sum", worker)
    rig.missions.cancel(mid, reason="the owner stopped it", actor="human")
    with pytest.raises(ValueError, match="not active"):
        rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), artifact={"schema": "t", "content": {"total": 6}})
    with pytest.raises(sqlite3.DatabaseError), rig.missions.conn:  # the database refuses it too
        rig.missions.conn.execute("UPDATE task_runs SET state='closed' WHERE run_id=?", (run.run_id,))
    with pytest.raises(ValueError, match="not active"):
        rig.ledger.open(mid, "check-sum", worker)
    rig.ledger.abandon(run.run_id, "the mission was cancelled")  # an abandon is always recorded


def test_a_mission_interrupted_while_closing_is_finished_on_the_next_run(rig, monkeypatch):
    mid = rig.mission(_plan())
    real_close = rig.missions.close

    def crash(*args, **kwargs):
        raise RuntimeError("injected crash while closing")

    monkeypatch.setattr(rig.missions, "close", crash)
    with pytest.raises(RuntimeError, match="while closing"):
        rig.runtime.run(mid)
    assert rig.missions.get(mid)["status"] == "closing"
    monkeypatch.setattr(rig.missions, "close", real_close)
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
