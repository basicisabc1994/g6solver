# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The supervisor's retry after a failed verdict, and messages (workforce OS plan, V4b; deferral E6).

Pinned: when an oracle EXECUTED and failed the latest output of a unit the owner's chat writes, and that unit
has attempts left, the verifier's reasons go to the unit as a message and the unit is offered to the chat
again, its brief carrying the message. Every verifier of the unit judges the new output afresh (one that
passed the old output is re-opened too), and a verifier's attempts are counted per output it judges. A unit
is never verified on an output some verifier has not judged. Code's own units are not retried (the same
inputs give the same answer), nor is a unit whose oracle never ran. A "complete" unit leaves complete only
through a one-time reopen grant the supervisor writes and the move uses up. Offline, temp dbs, fakes.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
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


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def world(component: str, op: str, params: dict):
    if component == "fake_ledger":
        return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
    return {"ok": True, "completion_state": "qualified-draft",
            "result": {"satisfiable": params["total"] != params["expected"]}}


def _check(key: str, verifies: str, field: str = "total", expected: int = 6) -> dict:
    return {"key": key, "kind": "verify", "verifies": verifies, "goal": f"{verifies}'s {field} is {expected}",
            "capabilities": [["fake_solver", "check"]],
            "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": expected},
                       "bind": {"total": f"{verifies}.{field}"}, "expect": {"field": "satisfiable", "equals": False},
                       "kind": "formal"}}


def _report(attempts: int = 2) -> dict:
    return {"key": "report", "kind": "do", "goal": "Report the ledger's total", "output_schema": "total",
            "max_attempts": attempts}


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner("alice"))
    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts=CONTRACTS, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in CONTRACTS:
        assert caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)
    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world)

    def mission(units: list[dict]) -> str:
        mid = missions.create(venture, objective="Report the ledger's total, checked", risk_tier="R1",
                              budget_cents=0, actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, {"units": units, "edges": []}, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    wf = ws.Workforce(venture, missions, graph, ledger, caps, runtime, world)
    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, runtime=runtime, mission=mission, wf=wf,
                           units=tmp_path / "workforce" / "units.db")


def _first_try(rig, units: list[dict], content: dict) -> str:
    mid = rig.mission(units)
    assert rig.runtime.run(mid)["status"] == "waiting"
    rig.runtime.submit(mid, "report", content)
    return mid


def _status(rig, mid: str, unit: str) -> str:
    return {u["key"]: u["status"] for u in rig.graph.units(mid)}[unit]


# -- the retry ----------------------------------------------------------------------------------------------------
def test_a_failed_verdict_goes_back_to_the_chat_with_the_reasons_and_the_second_try_is_verified(rig):
    mid = _first_try(rig, [_report(), _check("check-report", "report")], {"total": 7})
    back = ws.run(rig.wf, mid)
    assert back["status"] == "waiting"  # for the chat's second try, not closed
    (asked,) = back["awaiting_chat"]
    assert asked["unit"] == "report" and asked["attempts_left"] == 1
    (message,) = asked["messages"]
    assert message["from"] == "check-report" and "satisfiable" in message["text"]
    assert back["messages"] == [message | {"to": "report"}]
    ws.submit(rig.wf, mid, "report", {"total": 6})
    done = ws.run(rig.wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", wc.VERIFIED), done["outcome"]
    assert [v["result"] for v in rig.ledger.verdicts(mid, "report")] == ["fail", "pass"]
    assert [r["attempt"] for r in rig.ledger.runs(mid, "check-report")] == [1, 2]  # one attempt per output judged
    latest = rig.ledger.last_artifact(mid, "report")
    assert rig.ledger.verdicts(mid, "report")[-1]["artifact_id"] == latest["artifact_id"]


def test_a_verifier_that_passed_the_old_output_judges_the_new_one_too(rig):
    """Output 1 passes A and fails B; output 2 passes B and fails A. Were A's old pass kept, the unit would read
    verified on an output A never judged."""
    units = [_report(), _check("check-a", "report", "total", 6), _check("check-b", "report", "count", 2)]
    mid = _first_try(rig, units, {"total": 6, "count": 3})
    assert rig.runtime.run(mid)["status"] == "waiting"
    assert _status(rig, mid, "check-a") == "failed"  # re-opened: it must judge the next output
    rig.runtime.submit(mid, "report", {"total": 5, "count": 2})
    seen = rig.runtime.run(mid)
    latest = rig.ledger.last_artifact(mid, "report")["artifact_id"]
    on_latest = {v["unit_key"]: v["result"] for v in rig.ledger.verdicts(mid, "report") if v["artifact_id"] == latest}
    assert on_latest == {"check-a": "fail", "check-b": "pass"}
    # They now disagree on one output with no retry left: a person adjudicates (V4c), never verified meanwhile.
    assert seen["status"] == "waiting" and seen["completion_state"] == ""
    assert [(q["unit"], q["kind"]) for q in rig.runtime.decisions.pending(mid)] == [("check-a", "adjudication")]


def test_a_verifier_marked_complete_behind_the_ledgers_back_is_never_healed_by_a_retry(rig):
    """Only a verifier that really passed the old output is re-opened: one a raw write marked complete stays a
    disagreement the roll-up refuses (the injection matrix's ``transition tampered`` cell, on the retry path)."""
    units = [_report(), _check("check-a", "report"), _check("check-b", "report", "count", 2)]
    mid = rig.mission(units)
    assert rig.runtime.run(mid)["status"] == "waiting"
    task_id = {u["key"]: u["task_id"] for u in rig.graph.units(mid)}["check-b"]
    conn = sqlite3.connect(rig.units)
    with conn:
        conn.execute("UPDATE tasks SET status='complete' WHERE id=?", (task_id,))
    conn.close()
    rig.runtime.submit(mid, "report", {"total": 7, "count": 2})  # check-a fails it: a retry is due
    assert rig.runtime.run(mid)["status"] == "waiting"
    assert _status(rig, mid, "check-b") == "complete"  # not re-opened: it never passed anything
    rig.runtime.submit(mid, "report", {"total": 6, "count": 2})
    done = rig.runtime.run(mid)
    assert (done["status"], done["completion_state"]) == ("done", wc.BLOCKED)
    assert any("check-b" in r and "no passing run" in r for r in done["outcome"]["reasons"]), done["outcome"]


def _old_pass_then_new_output(rig) -> str:
    """check-a passed output 1; the unit was re-opened on its own (a path the send-back did not cover) and output 2
    is in. ``other`` keeps the mission open (the chat has not written it)."""
    mid = rig.mission([_report(), _check("check-a", "report"), _report() | {"key": "other"},
                       _check("check-other", "other")])
    rig.runtime.run(mid)
    rig.runtime.submit(mid, "report", {"total": 6})  # wakes the mission
    assert rig.runtime.step(mid)["unit"] == "check-a"  # one attempt: check-a passes output 1
    assert [v["result"] for v in rig.ledger.verdicts(mid, "report")] == ["pass"]
    rig.graph.reopen(mid, "report", "test: the unit alone")
    rig.runtime.submit(mid, "report", {"total": 5})
    return mid


def test_a_pass_on_an_older_output_is_judged_again_before_anything_waits_on_it(rig):
    mid = _old_pass_then_new_output(rig)
    rig.runtime.run(mid)  # reconcile re-opens check-a first; it then judges output 2
    assert [v["result"] for v in rig.ledger.verdicts(mid, "report")] == ["pass", "fail"]


def test_the_roll_up_refuses_a_verifier_whose_pass_judged_an_older_output(rig):
    mid = _old_pass_then_new_output(rig)
    reasons = " ".join(rig.runtime.roll_up(mid).reasons)
    assert "verifier check-a has not passed unit report's latest output" in reasons


def test_the_roll_up_never_counts_a_pass_on_an_older_output():
    """The roll-up's own rule, apart from the re-opening: a verifier whose last pass judged an older output
    leaves its unit blocked."""
    verdicts = [{"unit_key": "check-a", "artifact_id": "art-1", "result": "pass"}]
    assert wrt.judged_latest(verdicts, "check-a", "art-2") is False
    assert wrt.judged_latest(verdicts, "check-a", "art-1") is True


def test_with_no_attempts_left_the_failed_verdict_stands(rig):
    mid = _first_try(rig, [_report(attempts=1), _check("check-report", "report")], {"total": 7})
    done = ws.run(rig.wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", wc.BLOCKED)
    assert done["messages"] == []


def test_code_is_not_retried_after_a_failed_verdict(rig):
    """E2d (Ledger §13 step 8; plan ruling P3.7) changed what follows a failed check of code: the code is still never
    run again on the same inputs, and a code step with an attempt left is now written by the owner's chat, with the
    check's reasons."""
    wrong = {"key": "report", "kind": "do", "goal": "Total", "capabilities": [["fake_ledger", "total"]],
             "max_attempts": 2, "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 4]}}}
    mid = rig.mission([wrong, _check("check-report", "report")])
    back = ws.run(rig.wf, mid)
    assert back["status"] == "waiting" and [a["unit"] for a in back["awaiting_chat"]] == ["report"]
    assert len(rig.ledger.runs(mid, "report")) == 1  # the code was not run again
    assert back["messages"][0]["to"] == "report"
    assert [e["detail"]["selected_option"]["strategy_type"] for e in rig.missions.events(mid, kind="escalation")] == [
        "worker"]


def test_an_oracle_that_never_ran_sends_nothing_back(rig):
    unbound = _check("check-report", "report", "missing_field")
    mid = _first_try(rig, [_report(), unbound], {"total": 6})
    done = ws.run(rig.wf, mid)
    assert done["completion_state"] == wc.BLOCKED and done["messages"] == []
    assert len(rig.ledger.runs(mid, "report")) == 1


def test_a_supervisor_interrupted_mid_reopen_is_finished_by_the_next_step_once(rig, monkeypatch):
    mid = _first_try(rig, [_report(), _check("check-report", "report")], {"total": 7})
    real, calls = rig.graph.fail, []

    def crash_once(mission_id, key, reason, **kw):
        if key == "check-report" and not calls:
            calls.append(key)
            raise RuntimeError("killed after the reopen, before the verifier's own move")
        return real(mission_id, key, reason, **kw)

    monkeypatch.setattr(rig.graph, "fail", crash_once)
    with pytest.raises(RuntimeError, match="killed"):
        rig.runtime.run(mid)
    back = ws.run(rig.wf, mid)  # reconcile settles the verifier from the ledger; the supervisor's work is idempotent
    assert back["status"] == "waiting" and [a["unit"] for a in back["awaiting_chat"]] == ["report"]
    assert len(back["messages"]) == 1
    ws.submit(rig.wf, mid, "report", {"total": 6})
    assert ws.run(rig.wf, mid)["completion_state"] == wc.VERIFIED


@pytest.mark.parametrize("which", ["the unit", "a passing sibling checker"])
def test_a_crash_inside_a_reopen_is_finished_by_the_next_step(rig, monkeypatch, which):
    """Review of V4 (Codex's lead, reproduced): a re-open is two tracker moves (claim by the supervisor, then fail). A
    process that dies between them leaves the unit held by the supervisor; recovery finishes the re-open."""
    units = [_report(), _check("check-a", "report")]
    if which != "the unit":
        units.append(_check("check-b", "report", "count", 2))  # check-a passes, check-b fails: check-a is re-opened
    content = {"total": 7} if which == "the unit" else {"total": 6, "count": 3}
    mid = _first_try(rig, units, content)
    tracker, real, crashed = rig.graph._tracker, rig.graph._tracker.fail_task, []

    def crash_once(task_id, error_message="", owner=""):
        if error_message.startswith("reopened:") and not crashed:
            crashed.append(task_id)
            raise RuntimeError("killed between the supervisor's claim and its fail")
        return real(task_id, error_message=error_message, owner=owner)

    monkeypatch.setattr(tracker, "fail_task", crash_once)
    with pytest.raises(RuntimeError, match="killed"):
        rig.runtime.run(mid)
    assert crashed
    back = ws.run(rig.wf, mid)  # never "taken by another attempt": the supervisor's re-open is finished
    assert back["status"] == "waiting" and [a["unit"] for a in back["awaiting_chat"]] == ["report"]
    ws.submit(rig.wf, mid, "report", {"total": 6, "count": 2})
    assert ws.run(rig.wf, mid)["completion_state"] == wc.VERIFIED


def test_a_used_reopen_grant_cannot_be_rearmed(rig):
    mid = _first_try(rig, [_report(), _check("check-report", "report")], {"total": 6})
    rig.graph.reopen(mid, "report", "test: one grant")
    conn = sqlite3.connect(rig.units)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="used once"), conn:
            conn.execute("UPDATE workforce_reopens SET used = 0")
    finally:
        conn.close()


# -- the reopen grant ----------------------------------------------------------------------------------------------
def test_a_complete_unit_leaves_complete_only_through_one_unused_grant(rig):
    mid = _first_try(rig, [_report(), _check("check-report", "report")], {"total": 6})
    assert _status(rig, mid, "report") == "complete"
    task_id = {u["key"]: u["task_id"] for u in rig.graph.units(mid)}["report"]
    conn = sqlite3.connect(rig.units)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="final"), conn:
            conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (task_id,))
    finally:
        conn.close()
    rig.graph.reopen(mid, "report", "test: one grant")
    assert _status(rig, mid, "report") == "failed"
    conn = sqlite3.connect(rig.units)
    try:
        used = conn.execute("SELECT used FROM workforce_reopens WHERE task_id=?", (task_id,)).fetchall()
    finally:
        conn.close()
    assert used == [(1,)]
    rig.graph.reopen(mid, "report", "a failed unit is already open: nothing to do")
    assert _status(rig, mid, "report") == "failed"
