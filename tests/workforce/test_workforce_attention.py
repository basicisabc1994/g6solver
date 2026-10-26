# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Human attention (Phase E Part 2, E1b; Ledger §6, §23, §28 Phase 2, §29 ECON-009, §33).

Pinned: an intervention is a person's, of a Ledger §6 kind, names who decided, why and what came of it, and its time
is measured (both times, and exactly the seconds between them) or unknown, never inferred; attention adds up by kind,
its seconds are None while any intervention is untimed, and it is priced only with a configured hourly rate (unset is
unpriced, never 0). Every decision writes its intervention in the decision's own transaction; a person may correct a
unit G6 is waiting for the chat to write; that correction and an approval both appear in the mission's record. Offline.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import decide as wdecide
from g6.workforce import economics as we
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
from g6.workforce import service as ws


def _iv(**kw):
    base = {"kind": "approval", "actor_type": "user", "decided_by": "terminal:alice", "required_by_policy": False,
            "reason": "the drafts read well", "outcome": "approve"}
    return we.HumanIntervention(**{**base, **kw})


def _timed(seconds: int = 90, **kw):
    start = datetime(2026, 10, 4, 9, tzinfo=UTC)
    base = {"kind": "approval", "actor_type": "user", "decided_by": "web:7", "required_by_policy": True,
            "reason": "checked the drafts", "outcome": "approve"}
    return we.HumanIntervention.timed(started=start, ended=start + timedelta(seconds=seconds), **{**base, **kw})


@pytest.mark.parametrize(("field", "value"), [("kind", "nudge"), ("actor_type", "robot"),
                                              ("duration_source", "guessed")])
def test_kinds_actor_types_and_duration_sources_are_the_closed_sets(field, value):
    with pytest.raises(ValueError, match=field):
        _iv(**{field: value})


@pytest.mark.parametrize("missing", ["decided_by", "reason", "outcome"])
def test_an_intervention_names_who_decided_why_and_what_came_of_it(missing):
    with pytest.raises(ValueError, match=missing):
        _iv(**{missing: "  "})


def test_required_by_policy_is_true_or_false():
    with pytest.raises(ValueError, match="required_by_policy"):
        _iv(required_by_policy="yes")


def test_an_unmeasured_intervention_has_no_time_at_all():
    plain = _iv()
    assert (plain.duration_seconds, plain.duration_source, plain.measured) == (None, "unknown", False)
    with pytest.raises(ValueError, match="never inferred"):
        _iv(duration_seconds=120)  # e.g. a guess from how many messages were exchanged
    with pytest.raises(ValueError, match="never inferred"):
        _iv(started_at="2026-10-04T09:00:00+00:00")


def test_a_measured_duration_is_exactly_the_seconds_between_its_times():
    start = datetime(2026, 10, 4, 9, 0, 0, 500_000, tzinfo=UTC)
    timed = we.HumanIntervention.timed(started=start, ended=start + timedelta(minutes=2, seconds=14),
                                       kind="approval", actor_type="user", decided_by="web:7",
                                       required_by_policy=True, reason="checked the drafts", outcome="approve")
    assert (timed.duration_seconds, timed.duration_source, timed.measured) == (134, "measured", True)
    with pytest.raises(ValueError, match="never a guess"):
        _iv(duration_source="measured", started_at="2026-10-04T09:00:00+00:00",
            ended_at="2026-10-04T09:01:00+00:00", duration_seconds=90)
    with pytest.raises(ValueError, match="start and end"):  # no zone: a different moment on each machine
        _iv(duration_source="measured", started_at="2026-10-04T09:00:00", ended_at="2026-10-04T09:01:00",
            duration_seconds=60)


def test_an_intervention_round_trips_through_its_stored_form():
    iv = _iv(work_unit_id="approve", decision_id="dc_1", metadata={"run_id": "run_1"})
    stored = iv.as_detail()
    assert "id" not in stored and "mission_id" not in stored and stored["duration_seconds"] is None
    back = we.HumanIntervention.from_detail({**stored, "actor": "human"}, id="ev_1", mission_id="m_1",
                                            recorded_at="2026-10-04T00:00:00+00:00")
    assert back == dataclasses.replace(iv, id="ev_1", mission_id="m_1", recorded_at="2026-10-04T00:00:00+00:00")


def test_attention_adds_up_by_kind_and_its_time_is_unknown_while_any_is_untimed():
    found = we.attention_total([_timed(90), _iv(kind="correction", outcome="submitted")])
    assert (found.interventions, dict(found.by_kind), found.required_by_policy) == (
        2, {"approval": 1, "correction": 1}, 1)
    assert (found.known_seconds, found.unmeasured, found.seconds) == (90, 1, None)  # never a partial sum
    assert we.attention_total([_timed(90)]).seconds == 90
    assert we.attention_total([]).as_dict() == {
        "interventions": 0, "by_kind": {}, "required_by_policy": 0, "known_seconds": 0, "unmeasured": 0,
        "seconds": None, "cost": None, "cost_note": "unpriced: no hourly rate is set for human attention"}


def test_no_intervention_recorded_is_an_unknown_time_and_cost_never_zero():
    """Fix pass M5 (Ledger §4, §23): with a rate set and no intervention recorded, the owner's time and its cost are
    unknown, never "0 s" and 0.00: their time in the chat is never measured."""
    found = we.attention_total([], hourly_rate=we.Money(6000, "AUD"))
    assert (found.seconds, found.cost) == (None, None)
    assert found.cost_note == "not known: no intervention was recorded, and the owner's time in the chat is not measured"


def test_attention_is_priced_only_with_a_configured_rate_and_only_when_every_intervention_was_timed():
    assert we.attention_total([_timed(90)]).cost is None  # unset is unpriced, never 0
    rate = we.Money(6000, "AUD")  # 60.00 AUD an hour
    assert we.attention_total([_timed(90)], hourly_rate=rate).cost == we.Money(150, "AUD")  # 90 s is 1.50 AUD
    untimed = we.attention_total([_timed(90), _iv()], hourly_rate=rate)
    assert untimed.cost is None and untimed.cost_note == "not known: 1 of 2 interventions were not timed"
    assert we.attention_cost(1, we.Money(1800, "AUD")) == we.Money(1, "AUD")  # half a minor unit rounds up
    assert we.attention_cost(1, we.Money(1799, "AUD")) == we.Money(0, "AUD")


@pytest.mark.parametrize(("text", "rate"), [
    ("6000 AUD", we.Money(6000, "AUD")), (" 6000   AUD ", we.Money(6000, "AUD")), ("6000 aud", None),
    ("60.00 AUD", None), ("-5 AUD", None), ("6000", None), ("", None), (None, None)])
def test_an_hourly_rate_is_whole_minor_units_and_a_currency_or_no_rate_at_all(text, rate):
    assert we.parse_hourly_rate(text) == rate


# --- every decision writes its intervention with it (Task 2) ---------------------------------------------------------

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
    ("fake_mail", "send"): wcap.ParamContract.from_dict({
        "component": "fake_mail", "op": "send", "required": {"to": "str"}, "probe_params": {"to": "x"},
        "expect_field": "sent", "side_effect": "external", "check": "input_model"}),
}


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_solver", "fake_mail"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def _world(component: str, op: str, params: dict):
    if component == "fake_ledger":
        return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
    if component == "fake_solver":
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}
    raise AssertionError(f"unexpected call {component}.{op}")  # nothing here ever sends


SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}
CHECK = {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is the ledger's 6",
         "capabilities": [["fake_solver", "check"]],
         "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                    "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}
GATE = {"key": "approve", "kind": "gate", "goal": "A person approves the total", "inputs": ["sum"]}
SEND = {"key": "send", "kind": "do", "goal": "Send the total", "inputs": ["approve"],
        "capabilities": [["fake_mail", "send"]], "call": {"capability": ["fake_mail", "send"], "params": {"to": "b@x"}}}
CHECK_SEND = {**CHECK, "key": "check-send", "verifies": "send", "goal": "The sent total is 6",
              "oracle": {**CHECK["oracle"], "bind": {"total": "send.total"}}}


def _rig(tmp_path, owner: str = "alice"):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture(owner, f"{owner}'s", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner(owner))
    caps = wcap.Capabilities(vstore.for_owner(owner), contracts=CONTRACTS, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for pair in (("fake_ledger", "total"), ("fake_solver", "check")):
        assert caps.probe(*pair, wcall.prober(caps, invoke=_world).reply, actor="code").level == "probe_passed"
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)
    runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=_world)

    def mission(plan: dict, *, ready=caps.ready) -> str:
        mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0,
                              actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        missions.add_plan(mid, plan, actor="chat", ready=ready, effects=caps.side_effect)
        graph.materialize(mid, actor="code")
        return mid

    return SimpleNamespace(vstore=vstore, venture=venture, missions=missions, caps=caps, graph=graph, ledger=ledger,
                           runtime=runtime, mission=mission)


def test_a_gate_decision_writes_its_intervention_in_the_same_transaction(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK, GATE], "edges": []})
    assert rig.runtime.run(mid)["status"] == "waiting"
    found = rig.runtime.decide(mid, "approve", "approve", reason="the total matches the export", actor="human")
    iv, = rig.runtime.decisions.interventions(mid)
    assert (iv.kind, iv.outcome, iv.work_unit_id, iv.decision_id) == ("approval", "approve", "approve",
                                                                      found["decision_id"])
    assert (iv.actor_type, iv.decided_by, iv.required_by_policy) == ("user", "alice", False)
    assert (iv.duration_seconds, iv.duration_source, iv.reason) == (None, "unknown", "the total matches the export")
    assert rig.runtime.run(mid)["completion_state"] == wc.VERIFIED


def test_a_gate_with_an_external_step_behind_it_is_required_by_policy(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK, GATE, SEND, CHECK_SEND], "edges": []},
                      ready=lambda component, op: None)  # the send is never probed: it never runs here
    rig.runtime.run(mid)
    rig.runtime.decide(mid, "approve", "reject", reason="not this week", actor="human",
                       decided_by="terminal:alice")
    iv, = rig.runtime.decisions.interventions(mid)
    assert (iv.outcome, iv.required_by_policy, iv.decided_by) == ("reject", True, "terminal:alice")
    assert rig.runtime._required(mid, "check-sum", "adjudication") is True  # checks disagreeing go to a person


def test_the_decision_and_its_intervention_commit_together(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK, GATE], "edges": []})
    rig.runtime.run(mid)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("injected: the intervention row could not be written")

    monkeypatch.setattr(rig.runtime.decisions, "intervene_within", broken)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        rig.runtime.decide(mid, "approve", "approve", reason="ok", actor="human")
    monkeypatch.undo()
    assert rig.runtime.decisions.all(mid) == [] and rig.missions.events(mid, kind="approval.decided") == []
    assert [q["unit"] for q in rig.runtime.decisions.pending(mid)] == ["approve"]  # still waiting for the person


def _insert(rig, mid: str, detail: dict) -> None:
    """A row written straight into the store, past every code path: only the database's own rule stands."""
    venture = rig.missions.get(mid)["venture_id"]
    rig.missions.conn.execute(
        "INSERT INTO evidence (evidence_id, venture_id, kind, ref, detail_json, seq, at, idem_key)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (f"ev_{uuid.uuid4().hex[:12]}", venture, "intervention", mid, json.dumps(detail), 999999,
         "2026-10-04T00:00:00+00:00", ""))


@pytest.mark.parametrize("change", [
    {"actor": "chat"},  # the chat model is never the person
    {"duration_seconds": 120},  # a time with no measurement behind it
    {"duration_source": "measured", "duration_seconds": 60},  # measured, with no times
    {"decided_by": ""},
    {"kind": "nudge"},
    {"required_by_policy": "yes"},
    {"actor": None},  # a field left empty is never a pass: SQL's NULL must not slip past the rule
    {"duration_source": None, "duration_seconds": None},
])
def test_the_database_refuses_an_intervention_that_is_not_a_persons_or_whose_time_is_inferred(tmp_path, change):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK, GATE], "edges": []})
    with pytest.raises(sqlite3.DatabaseError, match="never inferred"):
        _insert(rig, mid, {**_iv().as_detail(), "actor": "human", **change})


def test_the_database_admits_a_persons_intervention_with_its_time_unknown_or_measured(tmp_path):
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK, GATE], "edges": []})
    _insert(rig, mid, {**_iv().as_detail(), "actor": "human"})
    _insert(rig, mid, {**_timed(90).as_detail(), "actor": "human"})
    assert [iv.measured for iv in rig.runtime.decisions.interventions(mid)] == [False, True]


# --- a person's correction, and the Phase 2 acceptance (Task 3) ----------------------------------------------------

DRAFT = {"key": "draft", "kind": "do", "goal": "Total the ledger by hand", "output_schema": "total", "max_attempts": 3}
CHECK_DRAFT = {**CHECK, "key": "check-draft", "verifies": "draft", "goal": "The total is the ledger's 6",
               "max_attempts": 3, "oracle": {**CHECK["oracle"], "bind": {"total": "draft.total"}}}
GATE_DRAFT = {"key": "approve", "kind": "gate", "goal": "A person approves the total", "inputs": ["draft"]}
CORRECTED = {"units": [DRAFT, CHECK_DRAFT, GATE_DRAFT], "edges": []}


def _sent_back(rig) -> str:
    """The chat's draft is wrong, its check sends it back, and G6 waits for it to be written again."""
    mid = rig.mission(CORRECTED)
    rig.runtime.run(mid)
    rig.runtime.submit(mid, "draft", {"total": 7})
    rig.runtime.run(mid)
    assert [u.key for u in rig.runtime.awaiting(mid)] == ["draft"]
    return mid


def _wf(rig, *, hourly_rate=None) -> ws.Workforce:
    return ws.Workforce(rig.venture, rig.missions, rig.graph, rig.ledger, rig.caps, rig.runtime,
                        hourly_rate=hourly_rate)


def test_phase_2_an_approval_and_a_manual_correction_both_appear_in_the_missions_record(tmp_path):
    """Ledger §28 Phase 2 acceptance, and ECON-009: a mission needing a person's approval and one manual correction
    shows both interventions in its record, and never claims zero supervision."""
    rig = _rig(tmp_path)
    mid = _sent_back(rig)
    rig.runtime.correct(mid, "draft", {"total": 6}, reason="invoice 3 was counted twice", actor="human",
                        decided_by="terminal:alice")
    assert rig.runtime.run(mid)["status"] == "waiting"  # checked, and now waiting for the person's approval
    rig.runtime.decide(mid, "approve", "approve", reason="the total matches the export", actor="human")
    final = rig.runtime.run(mid)
    assert final["completion_state"] == wc.VERIFIED  # the person's total was judged by the check, never trusted
    found = [(iv.kind, iv.outcome, iv.work_unit_id, iv.required_by_policy)
             for iv in rig.runtime.decisions.interventions(mid)]
    assert found == [("correction", "submitted", "draft", False), ("approval", "approve", "approve", False)]
    record = ws.status(_wf(rig), mid)["human_attention"]
    assert (record["interventions"], record["by_kind"]) == (2, {"approval": 1, "correction": 1})
    assert (record["seconds"], record["time"], record["cost"]) == (None, "not measured", None)  # never 0
    assert record["cost_note"] == "unpriced: no hourly rate is set for human attention"
    priced = ws.status(_wf(rig, hourly_rate=we.Money(6000, "AUD")), mid)["human_attention"]
    assert priced["cost"] is None and priced["cost_note"] == "not known: 2 of 2 interventions were not timed"


def test_a_mission_with_no_intervention_reports_its_attention_unknown_with_a_rate_set(tmp_path):
    """Fix pass M5: the economics record never reads "0 s" or a cost of 0.00 for time nothing measured."""
    rig = _rig(tmp_path)
    mid = rig.mission({"units": [SUM, CHECK], "edges": []})
    rig.runtime.run(mid)
    record = ws.status(_wf(rig, hourly_rate=we.Money(6000, "AUD")), mid)["human_attention"]
    assert (record["interventions"], record["seconds"], record["time"], record["cost"]) == (0, None, "not measured",
                                                                                            None)




def test_a_correction_is_one_attempt_costing_no_machine_and_names_its_attempt(tmp_path):
    rig = _rig(tmp_path)
    mid = _sent_back(rig)
    done = rig.runtime.correct(mid, "draft", {"total": 6}, reason="recounted", actor="human")
    run = rig.ledger.run(mid, done["run_id"])
    assert (run["state"], run["model_calls"], run["cost_source"], run["cost_cents"]) == ("closed", 0, "exact", 0)
    iv, = rig.runtime.decisions.interventions(mid)
    assert (iv.metadata, iv.decided_by) == ({"run_id": done["run_id"]}, "alice")


def test_only_a_person_corrects_with_a_reason_and_only_a_unit_g6_is_waiting_for(tmp_path):
    rig = _rig(tmp_path)
    mid = _sent_back(rig)
    with pytest.raises(ValueError, match="only a person"):
        rig.runtime.correct(mid, "draft", {"total": 6}, reason="x", actor="chat")
    with pytest.raises(ValueError, match="gives its reason"):
        rig.runtime.correct(mid, "draft", {"total": 6}, reason="  ", actor="human")
    with pytest.raises(ValueError, match="not waiting"):
        rig.runtime.correct(mid, "check-draft", {"total": 6}, reason="x", actor="human")
    assert rig.runtime.decisions.interventions(mid) == []  # a refusal records nothing


def test_a_correction_and_its_intervention_commit_together(tmp_path, monkeypatch):
    rig = _rig(tmp_path)
    mid = _sent_back(rig)
    before = len(rig.ledger.runs(mid, "draft"))

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("injected: the intervention row could not be written")

    monkeypatch.setattr(rig.runtime.decisions, "intervene_within", broken)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        rig.runtime.correct(mid, "draft", {"total": 6}, reason="recounted", actor="human")
    attempt = rig.ledger.runs(mid, "draft")[before]
    assert attempt["state"] == "open"  # the person's output was not kept without its intervention
    assert rig.runtime.decisions.interventions(mid) == []


def test_the_command_line_records_a_correction_typed_at_a_terminal(tmp_path, monkeypatch, capsys):
    rig = _rig(tmp_path)
    mid = _sent_back(rig)
    for name, value in ((ws.ENV_DB_DIR, str(tmp_path)), (ws.ENV_VENTURE, rig.venture), (ws.ENV_OWNER, "alice")):
        monkeypatch.setenv(name, value)
    real = ws.bind
    monkeypatch.setattr(ws, "bind", lambda env=None: real(env, registry=FakeRegistry(), invoke=_world,
                                                         contracts=CONTRACTS, authority=SCRIPTED))
    monkeypatch.setattr(wdecide, "_interactive", lambda: True)
    (tmp_path / "fixed.json").write_text(json.dumps({"total": 6}), encoding="utf-8")
    code = wdecide.main(["correct", mid, "draft", "--file", str(tmp_path / "fixed.json"), "--reason", "recounted"],
                        ask=lambda prompt: "draft")
    assert code == 0, capsys.readouterr().err
    iv, = rig.runtime.decisions.interventions(mid)
    assert (iv.kind, iv.decided_by) == ("correction", "terminal:alice")
