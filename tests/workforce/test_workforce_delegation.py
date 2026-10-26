# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Delegation within inherited limits (workforce OS plan, V4a).

A unit G6 is waiting for the owner's chat to write may be handed, in part, to children: a specialist the code
runs and extra verifiers. Pinned: the children land as the next plan revision, judged by the same validator,
and never widen what their parent had. A child names only its parent's capabilities (a verifier also its
parent's verifiers'); risk and attempts are at most the parent's; the children's budgets are carved out of the
parent's; depth and fan-out are capped; a child verifier judges only its own subtree. Only the owner's chat
delegates, and only on a unit G6 is waiting for it to write. A refused delegation changes nothing. Offline,
temp dbs, fake components.
"""
from __future__ import annotations

import dataclasses
import json
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
from g6.workforce import service as ws
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
    ("fake_crm", "lookup"): wcap.ParamContract.from_dict({
        "component": "fake_crm", "op": "lookup", "required": {"name": "str"}, "probe_params": {"name": "probe"},
        "expect_field": "found", "side_effect": "read", "check": "input_model"}),
}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver", "fake_crm"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class World:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op, dict(params)))
        if component == "fake_ledger":
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
        if component == "fake_solver":
            return {"ok": True, "completion_state": "qualified-draft",
                    "result": {"satisfiable": params["total"] != params["expected"]}}
        if component == "fake_crm":
            return {"ok": True, "completion_state": "qualified-draft", "result": {"found": False}}
        raise AssertionError(f"unexpected call {component}.{op}")


def _oracle(unit: str, expected: int = 6) -> dict:
    return {"capability": ["fake_solver", "check"], "params": {"expected": expected},
            "bind": {"total": f"{unit}.total"}, "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}


def _check(key: str, verifies: str) -> dict:
    return {"key": key, "kind": "verify", "verifies": verifies, "goal": f"{verifies}'s total is 6",
            "capabilities": [["fake_solver", "check"]], "oracle": _oracle(verifies)}


def _plan(*, other: bool = False) -> dict:
    """``report`` is the chat's to write (no call); it may use the ledger, and its verifier the solver."""
    units = [{"key": "report", "kind": "do", "goal": "Report the ledger's total", "output_schema": "total",
              "capabilities": [["fake_ledger", "total"]], "budget_cents": 60},
             _check("check-report", "report")]
    if other:
        units += [{"key": "other", "kind": "do", "goal": "Total another ledger",
                   "capabilities": [["fake_ledger", "total"]],
                   "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [6]}}},
                  _check("check-other", "other")]
    return {"units": units, "edges": []}


SPECIALIST = {"key": "sum", "kind": "do", "goal": "Total the ledger exactly", "capabilities": [["fake_ledger", "total"]],
              "budget_cents": 20, "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
SPECIALIST_CHECK = _check("check-sum", "sum")
EXTRA = _check("check-report-again", "report")


def _pull(key: str, parent: str = "") -> dict:
    unit = {"key": key, "kind": "do", "goal": f"Write {key}", "output_schema": "total"}
    return {**unit, "parent": parent} if parent else unit


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
    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world)

    def mission(plan: dict, *, risk: str = "R1", budget: int = 100) -> str:
        mid = missions.create(venture, objective="Report the ledger's total, checked", risk_tier=risk,
                              budget_cents=budget, actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, plan, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    wf = ws.Workforce(venture, missions, graph, ledger, caps, runtime, world)
    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, caps=caps, world=world, runtime=runtime,
                           mission=mission, wf=wf)


def _waiting(rig, plan: dict | None = None, **kw) -> str:
    mid = rig.mission(plan or _plan(), **kw)
    assert rig.runtime.run(mid)["status"] == "waiting"  # for the chat's report
    return mid


def _policy(rig, mid: str, unit: str) -> dict:
    run = rig.ledger.runs(mid, unit)[-1]
    return json.loads(rig.ledger.instance(mid, run["instance_id"])["spec_json"])["policy"]


# -- the acceptance: a specialist and an extra verifier, within inherited limits -------------------------------
def test_a_worker_spawns_a_specialist_and_an_extra_verifier_within_its_limits(rig):
    mid = _waiting(rig)
    rig.runtime.delegate(mid, "report", [SPECIALIST, SPECIALIST_CHECK, EXTRA])
    mission = rig.missions.get(mid)
    assert (mission["status"], mission["plan_revision"]) == ("active", 2)  # woken in the same transaction
    assert rig.missions.plan(mid)[1].unit("report").inputs == ("sum",)  # the parent consumes its specialist

    after = rig.runtime.run(mid)  # the specialist runs and is judged; then report waits for the chat again
    assert after["status"] == "waiting"
    assert [s.key for s in rig.runtime.awaiting(mid)] == ["report"]
    assert rig.ledger.last_artifact(mid, "sum")["content"] == {"total": 6}
    assert [v["result"] for v in rig.ledger.verdicts(mid, "sum")] == ["pass"]

    rig.runtime.submit(mid, "report", {"total": 6})
    final = rig.runtime.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED), final["outcome"]
    judged = sorted((v["unit_key"], v["result"]) for v in rig.ledger.verdicts(mid, "report"))
    assert judged == [("check-report", "pass"), ("check-report-again", "pass")]
    assert _policy(rig, mid, "sum")["max_cost_cents"] == 20  # the specialist's own carve
    assert _policy(rig, mid, "report")["max_cost_cents"] == 40  # what the parent kept: 60 - 20
    (delegated,) = [e for e in rig.missions.events(mid, kind="plan.revision") if e["detail"].get("delegation")]
    assert delegated["detail"]["delegation"] == {"parent": "report",
                                                 "children": ["sum", "check-sum", "check-report-again"]}
    assert delegated["detail"]["actor"] == "chat" and delegated["detail"]["revision"] == 2


def test_the_status_shows_the_delegation_tree_and_the_parents_brief_carries_its_childs_output(rig):
    mid = _waiting(rig)
    ws.delegate(rig.wf, mid, "report", [SPECIALIST, SPECIALIST_CHECK, EXTRA])
    seen = ws.run(rig.wf, mid)
    assert seen["tree"] == {"report": ["sum", "check-sum", "check-report-again"]}
    assert {u["key"]: u["parent"] for u in seen["units"]} == {
        "report": "", "check-report": "", "sum": "report", "check-sum": "report", "check-report-again": "report"}
    (asked,) = seen["awaiting_chat"]
    assert asked["unit"] == "report" and asked["inputs"] == {"sum": {"total": 6}}


def test_a_childs_worker_is_formed_under_its_parents_budget(rig):
    mid = _waiting(rig)
    rig.runtime.delegate(mid, "report", [SPECIALIST, SPECIALIST_CHECK])
    mission, plan = rig.missions.get(mid), rig.missions.plan(mid)[1]
    child = plan.unit("sum")
    assert wrt.ceiling_for(mission).max_cost_cents == 100
    assert wrt.ceiling_for(mission, plan, child).max_cost_cents == 60
    with pytest.raises(ww.WideningRefused, match="max_cost_cents"):  # formation refuses a widening a second time
        ww.form_worker("doer", ceiling=wrt.ceiling_for(mission, plan, child), contracts=rig.caps.contract,
                       unit=dataclasses.replace(child, budget_cents=61))


def test_only_the_puller_may_delegate():
    templates = ww.load_templates()
    assert {name: t.may_delegate for name, t in templates.items()} == {
        "doer": False, "gatekeeper": False, "prober": False, "puller": True, "verifier": False}


# -- refusals: nothing widens, nothing changes ---------------------------------------------------------------
def _snapshot(rig, mid: str) -> tuple:
    mission = rig.missions.get(mid)
    return (mission["status"], mission["plan_revision"], [u["key"] for u in rig.graph.units(mid)],
            [w["wake_id"] for w in rig.runtime.wakes.armed(mid)], len(rig.missions.events(mid)))


@pytest.mark.parametrize(("children", "why"), [
    ([{**SPECIALIST, "capabilities": [["fake_crm", "lookup"]],
       "call": {"capability": ["fake_crm", "lookup"], "params": {"name": "Acme"}}}, SPECIALIST_CHECK],
     "outside its parent's authority"),
    ([{**SPECIALIST, "capabilities": [["fake_solver", "check"]],  # a judge's capability is not a doer's
       "call": {"capability": ["fake_solver", "check"], "params": {"total": 1, "expected": 1}}}, SPECIALIST_CHECK],
     "outside its parent's authority"),
    ([{**EXTRA, "capabilities": [["fake_solver", "check"], ["fake_crm", "lookup"]]}],  # no self-granted authority
     "outside its parent's authority"),
    ([{**SPECIALIST, "budget_cents": 70}, SPECIALIST_CHECK], "over its parent's budget of 60"),
    ([{**SPECIALIST, "risk": "R1"}, SPECIALIST_CHECK], "exceeds its parent's R0"),
    ([{**SPECIALIST, "max_attempts": 2}, SPECIALIST_CHECK], "more attempts than its parent"),
    ([_check(f"check-report-{n}", "report") for n in range(7)], "at most 6 children"),
    ([{**SPECIALIST, "parent": "check-report"}, SPECIALIST_CHECK], "delegated by 'report'"),
    ([_check("check-report", "report")], "duplicate key"),
    ([SPECIALIST], "every do unit needs a verify unit"),
    # E8e: a child's call is judged against its op's contract, as a planned unit's is
    ([{**SPECIALIST, "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": "1, 2, 3"}}},
      SPECIALIST_CHECK], "call fake_ledger.total: 'numbers' must be list"),
])
def test_a_delegation_that_would_widen_or_break_a_rule_is_refused_and_changes_nothing(rig, children, why):
    mid = _waiting(rig)
    before = _snapshot(rig, mid)
    with pytest.raises(wp.PlanRejected, match=why):
        rig.runtime.delegate(mid, "report", children)
    assert _snapshot(rig, mid) == before


def test_a_child_verifier_judges_only_its_own_subtree(rig):
    mid = _waiting(rig, _plan(other=True))
    before = _snapshot(rig, mid)
    with pytest.raises(wp.PlanRejected, match="judges only its parent or a unit under it"):
        rig.runtime.delegate(mid, "report", [_check("check-other-again", "other")])
    assert _snapshot(rig, mid) == before


def test_delegation_is_capped_at_three_levels_deep(rig):
    mid = _waiting(rig, {"units": [_pull("draft-0"), _check("check-0", "draft-0")], "edges": []})
    for level in range(1, 4):  # each child is the chat's to write, and delegates in turn
        rig.runtime.delegate(mid, f"draft-{level - 1}", [_pull(f"draft-{level}"), _check(f"check-{level}",
                                                                                        f"draft-{level}")])
        assert rig.runtime.run(mid)["status"] == "waiting"
        assert [s.key for s in rig.runtime.awaiting(mid)] == [f"draft-{level}"]
    before = _snapshot(rig, mid)
    with pytest.raises(wp.PlanRejected, match="deeper than 3"):
        rig.runtime.delegate(mid, "draft-3", [_pull("draft-4"), _check("check-4", "draft-4")])
    assert _snapshot(rig, mid) == before


@pytest.mark.parametrize("unit", ["check-report", "other", "nope"])
def test_only_a_unit_waiting_for_the_chat_may_delegate(rig, unit):
    mid = _waiting(rig, _plan(other=True))
    before = _snapshot(rig, mid)
    with pytest.raises(ValueError, match="not waiting for the chat|no unit"):
        rig.runtime.delegate(mid, unit, [_check("check-again", "report")])
    assert _snapshot(rig, mid) == before


def test_a_finished_mission_takes_no_delegation(rig):
    mid = _waiting(rig)
    rig.runtime.submit(mid, "report", {"total": 6})
    assert rig.runtime.run(mid)["status"] == "done"
    with pytest.raises(ValueError, match="not active"):
        rig.runtime.delegate(mid, "report", [EXTRA])


@pytest.mark.parametrize("children", [[], {"key": "x"}, ["not a unit"]])
def test_a_delegation_names_a_list_of_unit_objects(rig, children):
    mid = _waiting(rig)
    with pytest.raises((ValueError, wp.PlanRejected)):
        rig.runtime.delegate(mid, "report", children)


def test_a_submission_that_lands_while_delegating_refuses_the_delegation(rig, monkeypatch):
    """Review of V4 (reproduced): the chat submits the unit between delegate's check and its amendment. The
    amendment refuses (nothing changes), so a parent never gains children after its output was written."""
    mid = _waiting(rig)
    real = rig.missions.amend_plan

    def racing(*args, **kwargs):
        rig.runtime.submit(mid, "report", {"total": 6})  # lands first
        return real(*args, **kwargs)

    monkeypatch.setattr(rig.missions, "amend_plan", racing)
    with pytest.raises(ValueError, match="changed while"):
        rig.runtime.delegate(mid, "report", [SPECIALIST, SPECIALIST_CHECK])
    assert rig.missions.get(mid)["plan_revision"] == 1
    assert "sum" not in [u["key"] for u in rig.graph.units(mid)]


def test_a_child_reads_only_the_artifacts_its_parent_reads():
    plan = _plan()
    plan["units"] += [{**SPECIALIST, "parent": "report", "inputs": ["artifact:ev_elsewhere"]},
                      {**SPECIALIST_CHECK, "parent": "report"}]
    with pytest.raises(wp.PlanRejected, match="reads only its parent's inputs"):
        _parse(plan)


# -- the write: compare-and-set, and an interrupted unit creation is finished by the next step -----------------
def test_a_stale_amendment_is_refused(rig):
    mid = _waiting(rig)
    _, plan = rig.missions.plan(mid)
    rig.missions.amend_plan(mid, plan, expect_revision=1, actor="chat", detail={})
    with pytest.raises(ValueError, match="changed underneath"):
        rig.missions.amend_plan(mid, plan, expect_revision=1, actor="chat", detail={})
    assert rig.missions.get(mid)["plan_revision"] == 2


def test_units_a_crash_left_uncreated_are_created_by_the_next_step(rig, monkeypatch):
    mid = _waiting(rig)
    real = rig.graph.extend
    monkeypatch.setattr(rig.graph, "extend", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError, match="killed"):
        rig.runtime.delegate(mid, "report", [SPECIALIST, SPECIALIST_CHECK])
    assert rig.missions.get(mid)["plan_revision"] == 2  # the revision landed; its units did not
    assert "sum" not in [u["key"] for u in rig.graph.units(mid)]
    monkeypatch.setattr(rig.graph, "extend", real)
    rig.runtime.run(mid)
    assert [v["result"] for v in rig.ledger.verdicts(mid, "sum")] == ["pass"]
    rig.runtime.submit(mid, "report", {"total": 6})
    assert rig.runtime.run(mid)["completion_state"] == wc.VERIFIED


# -- the plan validator judges a planned tree the same way ----------------------------------------------------
def _parse(plan: dict, budget: int = 100) -> wp.Plan:
    return wp.parse_plan(plan, risk_tier="R1", budget_cents=budget)


def test_the_mission_budget_counts_top_level_units_only():
    plan = _plan()
    plan["units"] += [{**SPECIALIST, "parent": "report", "budget_cents": 60}, {**SPECIALIST_CHECK, "parent": "report"}]
    plan["units"][0] = {**plan["units"][0], "inputs": ["sum"]}
    parsed = _parse(plan)  # 60 at the top, 60 carved out of it: the mission spends at most 60
    assert parsed.unit("sum").parent == "report"
    plan["units"][0] = {**plan["units"][0], "budget_cents": 101}
    with pytest.raises(wp.PlanRejected, match="over the mission budget"):
        _parse(plan)


@pytest.mark.parametrize(("change", "why"), [
    ({"parent": "check-report"}, "parent must be a do unit"),
    ({"parent": "ghost"}, "parent must be a do unit"),
    ({"parent": "Not A Key"}, "parent must be a unit key"),
    ({"inputs": ["elsewhere"]}, "reads only its parent's inputs"),
])
def test_a_planned_child_is_judged_by_the_same_rules(change, why):
    plan = _plan()
    plan["units"] += [{"key": "elsewhere", "kind": "do", "goal": "x", "capabilities": [["fake_ledger", "total"]],
                       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1]}}},
                      _check("check-elsewhere", "elsewhere"),
                      {**SPECIALIST, "parent": "report", **change}, {**SPECIALIST_CHECK, "parent": "report"}]
    with pytest.raises(wp.PlanRejected, match=why):
        _parse(plan)


def test_a_parent_cycle_is_refused_not_followed_forever():
    plan = {"units": [{**_pull("a"), "parent": "b"}, _check("check-a", "a"), {**_pull("b"), "parent": "a"},
                      _check("check-b", "b")], "edges": []}
    with pytest.raises(wp.PlanRejected, match="parent cycle"):
        _parse(plan)


def test_a_plan_without_parents_keeps_its_digest():
    """Adding the parent field must not change any stored plan's digest (``as_dict`` omits it when empty)."""
    parsed = _parse(_plan())
    assert '"parent"' not in parsed.to_json()
