# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A mission's budget, enforced and never silently exceeded (Phase E Part 1, E1c; Ledger §20, §29 ECON-008).

Pinned: a call whose declared cost would take its step past its budget is never sent, what earlier attempts spent
counts, and the step stops with what failed, why, how much it matters and what resolves it (Design §23); the work
done before it is kept and the mission closes honestly (never verified); the soft limit is recorded once; a hosted
(charged) mission runs nothing whose cost it cannot bound, and a ceiling that cannot be read is 0. From the final
review of Part 1 (C1): a sent call's cost row is written before anything that can fail; on a charged run a cost that
is not known uses up the rest of the step's budget; the auditor never calls such a mission within its budget. Offline.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import economics as we
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
from g6.workforce import service as ws

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
_FREE = {"amount_minor_units": 0, "currency": "AUD"}


def _contracts(ledger_ceiling: int | None) -> dict:
    ledger = {"component": "fake_ledger", "op": "total", "required": {"numbers": "list"},
              "probe_params": {"numbers": [1]}, "expect_field": "total", "side_effect": "read",
              "check": "input_model"}
    if ledger_ceiling is not None:
        ledger["cost_ceiling"] = {"amount_minor_units": ledger_ceiling, "currency": "AUD"}
    return {("fake_ledger", "total"): wcap.ParamContract.from_dict(ledger),
            ("fake_solver", "check"): wcap.ParamContract.from_dict({
                "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
                "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
                "check": "input_model", "model_free": True, "cost_ceiling": _FREE})}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class ProcessDied(BaseException):
    """The process dying: nothing in the door or the runtime catches it."""


class World:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.ledger_raises: list[BaseException] = []
        self.ledger_reply: dict | None = None

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op))
        if component == "fake_ledger":
            if self.ledger_raises:
                raise self.ledger_raises.pop(0)
            return self.ledger_reply or {"ok": True, "completion_state": "qualified-draft",
                                         "result": {"total": sum(params["numbers"])}}
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}


def _rig(tmp_path, *, owner: str = "alice", ledger_ceiling: int | None = 30, charged: bool | None = None):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture(owner, "Theirs", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner(owner))
    world, contracts = World(), _contracts(ledger_ceiling)
    caps = wcap.Capabilities(vstore.for_owner(owner), contracts=contracts, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    # These tests isolate mission execution after readiness. Seed the scripted check directly: charged
    # standalone probes cannot spend to create this readiness (the real service path is tested separately).
    # (the archive's hosted-owner test, kept as this rig's own choice: the library derives nothing from an owner)
    probe_reply = world if owner.startswith("customer:") else wcall.prober(caps, invoke=world).reply
    for component, op in contracts:
        assert caps.probe(component, op, probe_reply, actor="code").level == "probe_passed"
    world.calls.clear()
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)

    def mission(plan: dict, *, budget: int) -> str:
        mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=budget,
                              actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, plan, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world, charged=charged)
    return SimpleNamespace(missions=missions, world=world, graph=graph, ledger=ledger, runtime=runtime,
                           mission=mission, venture=venture, caps=caps, vstore=vstore)


def _sum(key: str, budget: int, attempts: int = 1) -> dict:
    return {"key": key, "kind": "do", "goal": f"Total the ledger ({key})", "capabilities": [["fake_ledger", "total"]],
            "budget_cents": budget, "max_attempts": attempts,
            "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}


def _check(key: str, verifies: str) -> dict:
    return {"key": key, "kind": "verify", "verifies": verifies, "goal": f"{verifies} totals 6",
            "capabilities": [["fake_solver", "check"]],
            "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6},
                       "bind": {"total": f"{verifies}.total"}, "expect": {"field": "satisfiable", "equals": False},
                       "kind": "formal"}}


def test_econ_008_a_small_hard_budget_is_never_silently_exceeded(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [_sum("sum-a", 30), _check("check-a", "sum-a"), _sum("sum-b", 20),
                                 _check("check-b", "sum-b")], "edges": []}, budget=50)
    final = rig.runtime.run(mid)
    # never silently exceeded: the spend is known and within the budget; sum-b's call was never sent
    spent = rig.ledger.cost_total(mid, binding_only=True)
    assert (spent.known_minor_units, spent.unknown_entries) == (30, 0)
    assert rig.world.calls.count(("fake_ledger", "total")) == 1
    run_b, = rig.ledger.runs(mid, "sum-b")
    assert (run_b["failure_code"], run_b["cost_cents"]) == ("refused:budget", 0)
    # the partial work is kept: sum-a's verified total stands
    assert rig.ledger.last_artifact(mid, "sum-a")["content"] == {"total": 6}
    assert [v["state"] for v in rig.ledger.verdicts(mid, "sum-a")] == [wc.VERIFIED]
    # an honest close that says what stopped, why, how much it matters and what resolves it
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED)
    reasons = " ".join(final["outcome"]["reasons"])
    assert ("one call to fake_ledger.total may cost up to 0.30 AUD, and this step has 0.20 AUD left of its "
            "0.20 AUD budget") in reasons
    assert "Nothing more was spent" in reasons and "To resolve: start the mission again with a larger budget" in reasons
    assert "(code G6_E_COST_GATE_OVER_BUDGET)" in reasons and "%" not in reasons
    assert [e["detail"]["code"] for e in rig.missions.events(mid, kind="budget")] == ["BUDGET_EXHAUSTED"]


def test_attempts_add_up_and_the_soft_limit_is_recorded_once(tmp_path):
    rig = _rig(tmp_path, ledger_ceiling=25)
    rig.world.ledger_raises = [ConnectionError("the export is locked")] * 5
    mid = rig.mission({"units": [_sum("sum", 50, attempts=3), _check("check-sum", "sum")], "edges": []}, budget=50)
    final = rig.runtime.run(mid)
    assert [r["failure_code"] for r in rig.ledger.runs(mid, "sum")] == [
        "call_unreachable", "call_unreachable", "refused:budget"]
    assert rig.ledger.cost_total(mid, binding_only=True).known_minor_units == 50  # the third would pass 0.50 AUD
    events = [e["detail"] for e in rig.missions.events(mid, kind="budget")]
    assert [d["code"] for d in events] == ["SOFT_BUDGET", "BUDGET_EXHAUSTED"]
    assert (events[0]["spent_minor_units"], events[0]["soft_limit"], events[0]["hard_limit"],
            events[0]["currency"]) == (50, 45, 50, "AUD")
    assert final["completion_state"] == wc.BLOCKED


def test_a_hosted_mission_refuses_a_call_whose_cost_it_cannot_bound(tmp_path):
    # the library derives nothing from an owner: the strict budget is what the caller asks for
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=None, charged=True)
    assert rig.runtime.charged is True
    mid = rig.mission({"units": [_sum("sum", 50), _check("check-sum", "sum")], "edges": []}, budget=50)
    final = rig.runtime.run(mid)
    assert rig.world.calls == [] and final["completion_state"] == wc.BLOCKED
    reasons = " ".join(final["outcome"]["reasons"])
    assert "fake_ledger.total has no declared cost" in reasons and "on your own machine" in reasons
    assert "(code G6_E_COST_GATE_GATED)" in reasons


def test_a_hosted_mission_with_no_budget_runs_nothing_that_may_cost(tmp_path):
    """Review Focus 1."""
    rig = _rig(tmp_path, owner="customer:7")
    mid = rig.mission({"units": [_sum("sum", 0), _check("check-sum", "sum")], "edges": []}, budget=0)
    final = rig.runtime.run(mid)
    assert rig.world.calls == [] and final["completion_state"] == wc.BLOCKED
    assert "0.00 AUD left of its 0.00 AUD budget" in " ".join(final["outcome"]["reasons"])


@pytest.mark.parametrize("budget", [None, "lots", True, 1.5, -5])
def test_a_ceiling_that_cannot_be_read_is_zero(budget):
    """Review Focus 1: fail closed."""
    mission = {"mission_id": "m1", "budget_cents": budget}
    assert wrt.ceiling_for(mission).max_cost_cents == 0
    policy = we.budget_policy(mission, charged=True)
    assert (policy.hard_machine_cost_limit, policy.ceiling_readable) == (0, False)


def test_the_budget_policy_holds_the_ledgers_fields(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [_sum("sum", 50), _check("check-sum", "sum")], "edges": []}, budget=50)
    policy = rig.runtime.budget(mid)
    assert (policy.hard_machine_cost_limit, policy.soft_machine_cost_limit, policy.currency, policy.charged) == (
        50, 45, "AUD", False)
    assert (policy.max_concurrency, policy.allow_budget_escalation_request) == (1, False)


def test_an_explicit_choice_overrides_the_owner(tmp_path):
    assert _rig(tmp_path / "a", owner="alice", charged=True).runtime.charged is True
    assert _rig(tmp_path / "b", owner="customer:9", charged=False).runtime.charged is False


# --- the final review of Part 1, finding C1: a charged run never spends past its hard budget unrecorded ----------

#: An answer larger than one step's result may be (``runs.MAX_ARTIFACT_BYTES``).
_OVERSIZED = {"ok": True, "completion_state": "qualified-draft", "result": {"total": 6, "rows": ["x" * 100] * 1000}}


def _charged_unit(rig) -> str:
    """The reviewer's scenario: a 0.60 AUD unit (and mission) whose one call may cost 0.50 AUD, three attempts."""
    return rig.mission({"units": [_sum("sum", 60, attempts=3), _check("check-sum", "sum")], "edges": []}, budget=60)




def _die_at_the_close(rig, mid: str, monkeypatch) -> dict:
    """Run the mission until the process dies after the attempt's call returned, before it closed: the open run."""

    def died(*args, **kwargs):
        raise ProcessDied("the process died before the attempt closed")

    monkeypatch.setattr(rig.ledger, "close", died)
    with pytest.raises(ProcessDied):
        rig.runtime.run(mid)
    monkeypatch.undo()
    first, = rig.ledger.runs(mid, "sum")
    assert first["state"] == "open"
    return first




def test_a_sent_calls_cost_is_recorded_even_when_its_close_never_lands(tmp_path, monkeypatch):
    """Nothing that can fail stands between a sent call and its cost row: here the process dies at the close."""
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)
    first = _die_at_the_close(rig, mid, monkeypatch)
    assert [e.source for e in rig.ledger.cost_entries(mid, run_id=first["run_id"])] == ["declared_ceiling"]
    final = rig.runtime.run(mid)  # the next runner abandons it: its recorded call and an unknown remainder
    first = rig.ledger.runs(mid, "sum")[0]
    assert (first["state"], first["failure_code"], first["cost_cents"], first["cost_source"]) == (
        "abandoned", "abandoned:lease", 50, "unknown")
    assert [e.source for e in rig.ledger.cost_entries(mid, run_id=first["run_id"])] == [
        "declared_ceiling", "abandoned"]
    assert rig.world.calls.count(("fake_ledger", "total")) == 1 and final["completion_state"] == wc.BLOCKED


@pytest.mark.parametrize("charged", [True, False])
def test_an_attempt_abandoned_mid_call_uses_up_a_charged_steps_budget(tmp_path, charged):
    """An uncertain call retains its known hold on every account; a charged run also treats unknown cost as
    consuming the remaining budget. Neither may send a second 50-cent call against the 60-cent limit."""
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50, charged=charged)
    rig.world.ledger_raises = [ProcessDied("the process died mid-call")]
    mid = _charged_unit(rig)
    with pytest.raises(ProcessDied):
        rig.runtime.run(mid)
    final = rig.runtime.run(mid)
    codes = [r["failure_code"] for r in rig.ledger.runs(mid, "sum")]
    if not charged:
        assert codes == ["abandoned:lease", "refused:budget"] and final["completion_state"] == wc.BLOCKED
        assert rig.world.calls.count(("fake_ledger", "total")) == 1
        assert rig.ledger.committed_total(mid).known_minor_units == 50
        assert "0.10 AUD left" in " ".join(final["outcome"]["reasons"])
        return
    assert codes == ["abandoned:lease", "refused:budget"]
    assert rig.world.calls.count(("fake_ledger", "total")) == 1
    assert final["completion_state"] == wc.BLOCKED
    reasons = " ".join(final["outcome"]["reasons"])
    assert ("this step has 0.00 AUD left of its 0.60 AUD budget: an earlier attempt stopped before G6 could see what "
            "it spent") in reasons
    assert "Nothing more was spent" in reasons and "(code G6_E_COST_GATE_OVER_BUDGET)" in reasons
    assert [e["detail"]["code"] for e in rig.missions.events(mid, kind="budget")] == ["BUDGET_EXHAUSTED"]






def test_runtime_commits_a_reservation_before_the_tool_can_start(tmp_path):
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)
    observed = []

    def inspect(component, op, params):
        # A different connection must see the committed hold before any tool effect.
        other = store.VentureStore(tmp_path / "venture.db")
        try:
            missions = wm.MissionStore(other.for_owner("customer:7"))
            reservations = missions.events(mid, kind="cost.reserved")
            run = wr.RunLedger.reader(missions).open_runs(mid)[0]
            current = [e["detail"] for e in reservations if e["detail"]["attempt_id"] == run["run_id"]]
            assert len(current) == 1
            assert current[0]["capability_invocation_id"] == f"{run['run_id']}#0"
            assert current[0]["contract_sha"] == rig.caps.contract(component, op).sha256
            assert current[0]["amount_minor_units"] == (50 if component == "fake_ledger" else 0)
            assert wr.RunLedger.reader(missions).cost_entries(mid, run_id=run["run_id"]) == []
            observed.append(component)
        finally:
            other.conn.close()
        return rig.world(component, op, params)

    rig.runtime._invoke = inspect
    result = rig.runtime.run(mid)
    assert result["completion_state"] == wc.VERIFIED
    assert observed == ["fake_ledger", "fake_solver"]
    assert rig.ledger.pending_reservations(mid) == []
    assert rig.ledger.committed_total(mid).known_minor_units == 50
    assert rig.ledger.cost_total(mid).known_minor_units == 50


def test_a_crash_retains_the_hold_without_claiming_an_observed_charge(tmp_path):
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)
    rig.world.ledger_raises = [ProcessDied("lost while the call was in flight")]
    with pytest.raises(ProcessDied):
        rig.runtime.run(mid)
    other = store.VentureStore(tmp_path / "venture.db")
    try:
        ledger = wr.RunLedger.reader(wm.MissionStore(other.for_owner("customer:7")))
        reservation, = ledger.pending_reservations(mid)
        assert reservation.amount_minor_units == 50
        assert ledger.cost_entries(mid) == []
        assert ledger.committed_total(mid).known_minor_units == 50
    finally:
        other.conn.close()
    wf = ws.Workforce(rig.venture, rig.missions, rig.graph, rig.ledger, rig.caps, rig.runtime)
    shown = ws.status(wf, mid)
    assert shown["cost_reservations"][0]["amount_minor_units"] == 50
    assert "0.50 AUD reserved" in shown["result"]["markdown"]
    assert "not a recorded charge" in shown["result"]["markdown"]


def test_a_failed_reservation_write_never_sends_the_call(tmp_path, monkeypatch):
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)

    def broken(*args, **kwargs):
        raise OSError("reservation store unavailable")

    monkeypatch.setattr(rig.ledger, "reserve_call", broken)
    with pytest.raises(OSError, match="reservation store unavailable"):
        rig.runtime.run(mid)
    assert rig.world.calls == []
    assert rig.ledger.cost_entries(mid) == []


def test_a_failed_cost_write_keeps_the_previously_committed_hold(tmp_path, monkeypatch):
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)

    def broken(*args, **kwargs):
        raise OSError("cost store unavailable")

    monkeypatch.setattr(rig.ledger, "record_cost", broken)
    with pytest.raises(OSError, match="cost store unavailable"):
        rig.runtime.run(mid)
    assert rig.world.calls == [("fake_ledger", "total")]
    assert rig.ledger.cost_entries(mid) == []
    assert rig.ledger.committed_total(mid).known_minor_units == 50


def test_untrusted_tool_cost_fields_cannot_release_a_reservation(tmp_path):
    rig = _rig(tmp_path, owner="customer:7", ledger_ceiling=50)
    mid = _charged_unit(rig)
    rig.world.ledger_reply = {"ok": True, "completion_state": "qualified-draft", "result": {"total": 6},
                              "cost": 0, "actual_cost_cents": 0, "refund": 50}
    rig.runtime.run(mid)
    assert rig.ledger.pending_reservations(mid) == []
    assert rig.ledger.cost_total(mid).known_minor_units == 50
    assert rig.ledger.cost_total(mid).summary == "estimated"
