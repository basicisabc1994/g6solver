# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The owner's chat drives missions through plain functions (workforce OS plan, V1a).

Pinned: the process is bound to one venture and owner by its environment (no tool takes either), and
an unbound process or another owner's venture is refused; the chat session sees the capability
catalogue (each pair's readiness and keys) and the plan format, probes a pair through the real door,
creates a mission, accepts its contract, plans (validated, then materialized) and runs it, and reads
back its status: units, runs, verdicts and the rolled-up outcome. Every chat-made change is recorded
as the chat's, never the human's. Offline, temp dbs, fake components.
"""
from __future__ import annotations

import pytest
from g6.workforce import store
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import plans as wp
from g6.workforce import service as ws

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
PLAN = {"units": [
    {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
     "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}},
    {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is 6",
     "capabilities": [["fake_solver", "check"]],
     "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}], "edges": []}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def world(component: str, op: str, params: dict) -> dict:
    if component == "fake_ledger":
        return {"ok": True, "result": {"total": sum(params["numbers"])}}
    return {"ok": True, "result": {"satisfiable": params["total"] != params["expected"]}}


@pytest.fixture
def env(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    bob = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    return {ws.ENV_DB_DIR: str(tmp_path), ws.ENV_VENTURE: venture, ws.ENV_OWNER: "alice", "bob_venture": bob}


def _bind(env, **over):
    return ws.bind({**env, **over}, registry=FakeRegistry(), invoke=world, contracts=CONTRACTS, authority=SCRIPTED)




def test_a_mission_another_runner_holds_is_not_stepped_and_its_status_names_that_runner(env):
    """V2a: another runner (the chat in another window) holds the mission's unexpired lease; this chat's run
    steps nothing and says who is running it. (A service holder is judged by its heartbeat instead: the
    daemon tests pin that.)"""
    wf = _bind(env)
    for component, op in CONTRACTS:
        ws.probe(wf, component, op)
    mid = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid)
    ws.plan(wf, mid, PLAN)
    wf.missions.take_lease(mid, holder="runner:elsewhere")
    found = ws.run(wf, mid)
    assert (found["status"], found["runs"]) == ("active", [])
    assert (found["runner"]["holder"], found["runner"]["alive"]) == ("runner:elsewhere", None)


def test_a_waiting_mission_shows_what_it_waits_for_and_where_to_drop_the_file(env, tmp_path):
    """V2b: the status names each armed wake, and a file wake's inbox path; a drop there wakes the mission."""
    wf = _bind(env)
    for component, op in CONTRACTS:
        ws.probe(wf, component, op)
    assert {"not_before", "await_file"} <= {w for w in ("not_before", "await_file")
                                            if w in ws.capabilities(wf)["plan_format"]}
    mid = ws.create(wf, objective="Total the ledger once the export lands", risk_tier="R1",
                    budget_cents=0)["mission_id"]
    ws.accept(wf, mid)
    plan = {"units": [{**PLAN["units"][0], "await_file": "export.csv"}, PLAN["units"][1]], "edges": []}
    ws.plan(wf, mid, plan)
    found = ws.run(wf, mid)
    assert (found["status"], found["runs"]) == ("waiting", [])
    wake, = found["wakes"]
    assert (wake["kind"], wake["unit"], wake["name"]) == ("file", "sum", "export.csv")
    assert wake["path"] == str(tmp_path / "inbox" / env[ws.ENV_VENTURE] / mid / "export.csv")
    drop = tmp_path / "inbox" / env[ws.ENV_VENTURE] / mid
    drop.mkdir(parents=True)
    (drop / "export.csv").write_text("invoice,amount\n", encoding="utf-8")
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"], done["wakes"]) == ("done", wc.VERIFIED, [])


def test_an_unbound_process_and_another_owners_venture_are_refused(env):
    # the library binds $G6_HOME/workforce when no db dir is named, so that refusal names the missing store
    for missing, said in ((ws.ENV_DB_DIR, "no venture db"), (ws.ENV_VENTURE, ws.ENV_VENTURE)):
        with pytest.raises(ws.WorkforceUnbound, match=said):
            ws.bind({k: v for k, v in env.items() if k != missing})
    with pytest.raises(LookupError) as foreign:
        _bind(env, **{ws.ENV_OWNER: "bob"})
    assert str(foreign.value) == store.NOT_FOUND


def test_the_catalogue_shows_each_pair_with_its_readiness_keys_and_the_plan_format(env):
    wf = _bind(env)
    found = ws.capabilities(wf)
    pairs = {(c["component"], c["op"]): c for c in found["capabilities"]}
    assert set(pairs) == set(CONTRACTS)
    assert pairs[("fake_ledger", "total")]["level"] == "deps_installed"
    assert pairs[("fake_ledger", "total")]["required"] == {"numbers": "list"}
    assert pairs[("fake_solver", "check")]["model_free"] is True
    assert "oracle" in found["plan_format"] and "call" in found["plan_format"]
    assert ws.probe(wf, "fake_ledger", "total")["level"] == "probe_passed"
    assert {c["level"] for c in ws.capabilities(wf)["capabilities"] if c["op"] == "total"} == {"probe_passed"}


def test_the_chat_runs_a_mission_from_objective_to_a_verified_deliverable(env):
    wf = _bind(env)
    for component, op in CONTRACTS:
        ws.probe(wf, component, op)
    created = ws.create(wf, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0)
    mid = created["mission_id"]
    assert created["status"] == "eliciting" and created["units"] == []
    assert ws.accept(wf, mid)["status"] == "awaiting_plan"
    planned = ws.plan(wf, mid, PLAN)
    assert planned["status"] == "active" and planned["plan_revision"] == 1
    assert [(u["key"], u["status"]) for u in planned["units"]] == [("sum", "pending"), ("check-sum", "pending")]
    done = ws.run(wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", wc.VERIFIED)
    assert [(r["unit"], r["state"]) for r in done["runs"]] == [("sum", "closed"), ("check-sum", "closed")]
    assert done["verdicts"] == [{"unit": "check-sum", "verifies": "sum", "result": "pass", "state": wc.VERIFIED}]
    assert ws.status(wf, mid) == done
    actors = {e["detail"]["actor"] for e in wf.missions.events(mid)
              if e["kind"] in ("mission.created", "mission.status", "plan.revision")}
    assert "human" not in actors and "chat" in actors


def test_a_plan_naming_an_unready_capability_is_refused_with_every_problem(env):
    wf = _bind(env)
    mid = ws.create(wf, objective="x", risk_tier="R1", budget_cents=0)["mission_id"]
    ws.accept(wf, mid)
    with pytest.raises(wp.PlanRejected) as refused:
        ws.plan(wf, mid, PLAN)
    assert len(refused.value.problems) == 2 and all("not ready" in p for p in refused.value.problems)
    assert ws.status(wf, mid)["status"] == "awaiting_plan"


def test_another_owners_mission_is_refused_like_an_unknown_one(env):
    wf = _bind(env)
    mid = ws.create(wf, objective="x", risk_tier="R0", budget_cents=0)["mission_id"]
    theirs = _bind(env, **{ws.ENV_OWNER: "bob", ws.ENV_VENTURE: env["bob_venture"]})
    for call in (ws.status, ws.run, ws.accept):
        with pytest.raises(LookupError) as foreign:
            call(theirs, mid)
        with pytest.raises(LookupError) as unknown:
            call(theirs, "no-such-mission")
        assert str(foreign.value) == str(unknown.value) == store.NOT_FOUND
