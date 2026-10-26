# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The injection matrix and crash recovery for the skeleton mission (workforce OS plan, P0 T6).

Pinned: whatever is injected at the envelope, the component's inner result, its metadata, the
attempt, the verdict, the tracker transition or the roll-up, the mission closes no better than the
worst thing injected, and ``verified`` only in the clean cell; a crash at each write-order step
(after the run opened, after the claim, after the run closed) is finished by ``reconcile`` with no
duplicate work. Offline, temp dbs, fake components.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from g6.core import model_calls as mc
from g6.workforce.tracker import TaskTrackerStore
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import rungs as wrung
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731


def _contracts(model_free: bool) -> dict:
    return {
        ("fake_ledger", "total"): wcap.ParamContract.from_dict({
            "component": "fake_ledger", "op": "total", "required": {"numbers": "list"},
            "probe_params": {"numbers": [1]}, "expect_field": "total", "side_effect": "read",
            "check": "input_model"}),
        ("fake_solver", "check"): wcap.ParamContract.from_dict({
            "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
            "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
            "check": "input_model", "model_free": model_free}),
    }


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


class World:
    """Fake components whose replies a test can rewrite after the probes have passed."""

    def __init__(self) -> None:
        self.ledger_reply = None  # callable(total) -> reply, or None for the clean reply
        self.solver_reply = None  # callable(sat) -> reply
        self.ledger_raises = False
        self.solver_model_calls = 0

    def __call__(self, component: str, op: str, params: dict):
        if component == "fake_ledger":
            if self.ledger_raises:
                raise ConnectionError("the export is locked")
            total = sum(params["numbers"])
            if self.ledger_reply:
                return self.ledger_reply(total)
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": total}}
        sat = params["total"] != params["expected"]
        for _ in range(self.solver_model_calls):
            mc.note_model_call()
        if self.solver_reply:
            return self.solver_reply(sat)
        return {"ok": True, "completion_state": "qualified-draft", "result": {"satisfiable": sat}}


PLAN = {"units": [
    {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
     "max_attempts": 2, "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}},
    {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is 6",
     "capabilities": [["fake_solver", "check"]],
     "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}], "edges": []}


def _rig(tmp_path, *, model_free: bool = True, now=None):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner("alice"))
    world = World()
    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts=_contracts(model_free), registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in _contracts(model_free):
        caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code")
    units = tmp_path / "workforce" / "units.db"
    graph = wg.WorkGraph(missions, units)
    mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0, actor="chat")
    missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
    missions.add_plan(mid, PLAN, actor="chat", ready=caps.ready)
    ids = graph.materialize(mid, actor="code")
    ledger = wr.RunLedger(missions)
    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world, now=now)
    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, caps=caps, world=world, mid=mid, ids=ids,
                           units=units, runtime=runtime)


def _set(attr, value):
    return lambda r: setattr(r.world, attr, value)


def _reply(**top):
    return lambda total: {"ok": True, "completion_state": "qualified-draft", "result": {"total": total}, **top}


def _inner(**extra):
    return lambda total: {"ok": True, "completion_state": "qualified-draft", "result": {"total": total, **extra}}


def _tamper(r):
    """A writer that skips the runtime AND the tracker's own rules (raw SQL on units.db) marks the verify
    unit complete with no run behind it: the roll-up must not believe it."""
    conn = sqlite3.connect(r.units)
    with conn:
        conn.execute("UPDATE tasks SET status='complete' WHERE id=?", (r.ids["check-sum"],))
    conn.close()
    assert TaskTrackerStore(str(r.units)).get_task(r.ids["check-sum"])["status"] == "complete"


def _accept(r):
    contract = r.missions.contract(r.mid)
    r.missions.conn.execute("UPDATE missions SET contract_json=? WHERE mission_id=?", (contract.model_copy(update={
        "checkpoints": [{"name": "the books balance", "predicate": "ledger.balanced"}]}).model_dump_json(), r.mid))
    r.missions.conn.commit()


V, Q, B = wc.VERIFIED, wc.QUALIFIED, wc.BLOCKED
CELLS = {
    "clean": (None, V, {}),
    "envelope says blocked": (_set("ledger_reply", _reply(completion_state="blocked-escalated")), B, {}),
    "envelope says degraded": (_set("ledger_reply", _reply(degraded=True)), Q, {}),
    "envelope claims verified": (_set("ledger_reply", _reply(completion_state="verified")), V, {}),
    "inner result degraded": (_set("ledger_reply", _inner(degraded=True)), Q, {}),
    "inner result blocked": (_set("ledger_reply", _inner(completion_state="blocked-escalated")), B, {}),
    "inner result error": (_set("ledger_reply", _inner(error="the export is malformed")), B, {}),
    "metadata answered on defaults": (_set("ledger_reply", _inner(metadata={"answered_on_defaults": True})), B, {}),
    "metadata unread parameter": (_set("ledger_reply", _inner(metadata={"unrecognised_parameters": ["x"]})), Q, {}),
    "attempt raises": (_set("ledger_raises", True), B, {}),
    "verdict asked a model": (_set("solver_model_calls", 1), Q, {}),
    "verdict oracle not model-free": (None, Q, {"model_free": False}),
    "verdict unknown": (_set("solver_reply", lambda sat: {"ok": True, "result": {"satisfiable": None}}), B, {}),
    "verdict degraded": (_set("solver_reply", lambda sat: {"ok": True, "result": {"satisfiable": sat,
                                                                                    "degraded": True}}), Q, {}),
    "verdict wrong": (_set("ledger_reply", lambda total: {"ok": True, "result": {"total": total + 1}}), B, {}),
    "verdict answer lacks the field": (_set("solver_reply", lambda sat: {"ok": True, "result": {"model": {}}}), B, {}),
    "transition tampered": (_tamper, B, {}),
    "roll-up acceptance unmeasured": (_accept, Q, {}),
}


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_the_mission_is_never_better_than_the_worst_thing_injected(tmp_path, cell):
    inject, expected, rig_kw = CELLS[cell]
    rig = _rig(tmp_path, **rig_kw)
    if inject:
        inject(rig)
    final = rig.runtime.run(rig.mid)
    if cell == "verdict wrong":  # E2d (P3.7): the code step whose check failed, an attempt left, goes to the chat
        assert final["status"] == "waiting" and [s.key for s in rig.runtime.awaiting(rig.mid)] == ["sum"]
        rig.runtime.submit(rig.mid, "sum", {"total": 7})  # the chat's attempt is wrong too
        final = rig.runtime.run(rig.mid)
    assert final["status"] == "done"
    assert final["completion_state"] == expected, (cell, final["outcome"])
    if expected != V:
        assert final["outcome"]["reasons"], cell  # a mission below verified always says why


def test_only_the_clean_cell_verifies():
    assert [name for name, (_, state, _) in CELLS.items() if state == wc.VERIFIED] == [
        "clean", "envelope claims verified"]


# --- crash recovery --------------------------------------------------------------------------------


def _later():
    return datetime.now(UTC) + timedelta(hours=1)


def test_a_crash_after_the_run_opened_is_abandoned_and_retried(tmp_path, monkeypatch):
    rig = _rig(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the process died after opening the run")

    monkeypatch.setattr(rig.graph, "claim", boom)
    with pytest.raises(RuntimeError):
        rig.runtime.step(rig.mid)
    monkeypatch.undo()
    # The crashed step's lease is gone, so nothing will ever close its run: the next runner abandons it at
    # once, well inside its wall clock (V2a; P0 waited the wall clock out).
    recovered = wrt.Runtime(rig.missions, rig.graph, rig.ledger, rig.caps, invoke=rig.world)
    assert recovered.reconcile(rig.mid) and rig.ledger.runs(rig.mid, "sum")[0]["state"] == "abandoned"
    final = recovered.run(rig.mid)
    assert final["completion_state"] == wc.VERIFIED
    assert [r["state"] for r in rig.ledger.runs(rig.mid, "sum")] == ["abandoned", "closed"]


def test_a_crash_after_the_claim_fails_the_unit_and_a_retry_claims_it(tmp_path, monkeypatch):
    rig = _rig(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the process died mid-attempt")

    monkeypatch.setattr(wrung, "run_do", boom)
    with pytest.raises(RuntimeError):
        rig.runtime.step(rig.mid)
    monkeypatch.undo()
    assert {u["key"]: u["status"] for u in rig.graph.units(rig.mid)}["sum"] == "in_progress"
    recovered = wrt.Runtime(rig.missions, rig.graph, rig.ledger, rig.caps, invoke=rig.world, now=_later)
    final = recovered.run(rig.mid)
    assert final["completion_state"] == wc.VERIFIED
    assert [r["state"] for r in rig.ledger.runs(rig.mid, "sum")] == ["abandoned", "closed"]


def test_a_crash_after_the_run_closed_is_settled_from_the_ledger_with_no_second_attempt(tmp_path, monkeypatch):
    rig = _rig(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the process died before moving the unit")

    monkeypatch.setattr(rig.runtime, "_settle", boom)
    with pytest.raises(RuntimeError):
        rig.runtime.step(rig.mid)
    monkeypatch.undo()
    assert rig.ledger.runs(rig.mid, "sum")[0]["state"] == "closed"
    final = rig.runtime.run(rig.mid)  # no clock change needed: the ledger already says what happened
    assert final["completion_state"] == wc.VERIFIED
    assert len(rig.ledger.runs(rig.mid, "sum")) == 1
