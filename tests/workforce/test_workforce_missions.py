# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Missions and their event history (workforce OS plan, P0 T2).

Pinned: a mission persists the clarified objective as the intent engine's own ``IntentContract``
(acceptance = its ``checkpoints``); every read and write carries the owner, and another owner's
mission id is refused exactly like an unknown one; status moves only along the mission state
machine and a finished mission never changes (a trigger, not a convention); mission events are
venture ``evidence`` rows, written once per idempotency key, and evidence itself is append-only.
Offline, temp db.
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from types import SimpleNamespace

import pytest
from g6.intent_engine.schema import CheckpointSpec
from g6.intent_engine.schema import IntentContract
from g6.workforce import store
from g6.workforce import acceptance
from g6.workforce import contracts as wc
from g6.workforce import missions as wm
from g6.workforce.graph import WorkGraph

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}


def _contract() -> IntentContract:
    return IntentContract(
        goal="Make tests/test_totals.py::test_gst pass",
        constraints=["no test file is edited"],
        success_criteria=["the named test passes", "no other test regresses"],
        checkpoints=[CheckpointSpec(name="named test passes", predicate="coder.run_tests",
                                    params={"tests": ["tests/test_totals.py::test_gst"]})],
        deadline="2026-10-08",
        risk_tolerance="low",
    )


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    alice = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    bob = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    a = wm.MissionStore(vstore.for_owner("alice"))
    b = wm.MissionStore(vstore.for_owner("bob"))
    mid = a.create(alice, objective="Fix the failing GST test", contract=_contract(), risk_tier="R1",
                   budget_cents=0, actor="chat")
    return SimpleNamespace(vstore=vstore, alice=alice, bob=bob, a=a, b=b, mid=mid)


# --- a mission persists the clarified objective --------------------------------------------------


def test_the_contract_round_trips_as_the_intent_engines_own_model(rig):
    assert rig.a.contract(rig.mid) == _contract()
    row = rig.a.get(rig.mid)
    assert row["objective"] == "Fix the failing GST test" and row["status"] == "eliciting"
    assert row["risk_tier"] == "R1" and row["budget_cents"] == 0 and row["completion_state"] == ""
    assert row["contract"]["checkpoints"][0]["predicate"] == "coder.run_tests"


def test_code_creation_request_is_atomic_and_refuses_conflicting_replay(rig):
    args = {"objective": "Reuse accepted code harness", "risk_tier": "R2", "budget_cents": 0,
            "actor": "code", "template": "code_change", "request_key": "coding-reuse:example"}
    mid = rig.a.create(rig.alice, **args)
    before = rig.a.list(rig.alice)
    assert rig.a.create(rig.alice, **args) == mid
    with pytest.raises(ValueError, match="different|conflict"):
        rig.a.create(rig.alice, **{**args, "budget_cents": 100})
    assert rig.a.list(rig.alice) == before
    assert len(rig.a.events(mid, kind="mission.created")) == 1
    with pytest.raises(ValueError, match="code"):
        rig.a.create(rig.alice, **{**args, "actor": "chat"})
    with pytest.raises(LookupError):
        rig.b.create(rig.alice, **args)


def test_concurrent_creation_requests_commit_one_mission_without_orphans(rig, tmp_path):
    ready = Barrier(2, timeout=10)
    before = len(rig.a.list(rig.alice))

    def create():
        connection = store.VentureStore(tmp_path / "venture.db")
        missions = wm.MissionStore(connection.for_owner("alice"))
        try:
            ready.wait()
            return missions.create(rig.alice, objective="Concurrent harness reuse", risk_tier="R2", budget_cents=0,
                actor="code", template="code_change", request_key="coding-reuse:concurrent")
        finally:
            connection.conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))
    assert len(set(ids)) == 1
    assert len(rig.a.list(rig.alice)) == before + 1
    assert len(rig.a.events(ids[0], kind="mission.created")) == 1


@pytest.mark.parametrize("creator,acceptor,creation_digest,found", [
    ("code", "code", "", True), ("chat", "code", "", False),
    ("code", "chat", "", False), ("code", "code", "a" * 64, False)])
def test_historical_task_lookup_requires_code_bound_accepted_signature(rig, creator, acceptor, creation_digest, found):
    signature = "b" * 64
    mid = rig.a.create(rig.alice, objective="Check a coding result", risk_tier="R1", budget_cents=0,
                        actor=creator, template="code_change", task_sha256=creation_digest)
    standard = acceptance.compile_contract(objective="Check a coding result", contract=rig.a.contract(mid),
                                             risk_tier="R1", budget_cents=0)
    standard = replace(standard, metadata={**standard.metadata, "coding": {"task_signature": {"sha256": signature}}})
    rig.a.accept(mid, standard, actor=acceptor)
    assert (mid in rig.a.earlier(rig.alice, template="code_change", task_sha256=signature)) is found
    if creation_digest:
        assert mid in rig.a.earlier(rig.alice, template="code_change", task_sha256=creation_digest)


def test_a_mission_without_a_contract_starts_from_the_objective(rig):
    mid = rig.a.create(rig.alice, objective="Collect overdue invoices", risk_tier="R2", budget_cents=500,
                       actor="chat")
    assert rig.a.contract(mid).goal == "Collect overdue invoices"


@pytest.mark.parametrize("kw", [
    {"objective": "  "}, {"risk_tier": "R9"}, {"budget_cents": -1}, {"budget_cents": 1.5}, {"actor": "someone"},
])
def test_an_incomplete_or_malformed_mission_is_refused(rig, kw):
    args = {"objective": "x", "risk_tier": "R0", "budget_cents": 0, "actor": "chat", **kw}
    with pytest.raises(ValueError):
        rig.a.create(rig.alice, **args)


# --- another owner cannot, and cannot tell whether the mission exists -----------------------------


def _calls(rig) -> dict:
    return {
        "get": lambda s, m: s.get(m),
        "contract": lambda s, m: s.contract(m),
        "set_contract": lambda s, m: s.set_contract(m, _contract(), actor="chat"),
        "transition": lambda s, m: s.transition(m, "awaiting_plan", reason="x", actor="human"),
        "cancel": lambda s, m: s.cancel(m, reason="x", actor="human"),
        "close": lambda s, m: s.close(m, wc.Outcome("qualified-draft"), actor="code"),
        "record_event": lambda s, m: s.record_event(m, "message", actor="code", detail={"a": 1}),
        "events": lambda s, m: s.events(m),
    }


_NAMES = sorted(_calls(None))


@pytest.mark.parametrize("name", _NAMES)
def test_another_owner_is_refused_exactly_as_for_an_unknown_mission(rig, name):
    call = _calls(rig)[name]
    with pytest.raises(LookupError) as foreign:
        call(rig.b, rig.mid)
    with pytest.raises(LookupError) as unknown:
        call(rig.b, "no-such-mission")
    assert str(foreign.value) == str(unknown.value) == store.NOT_FOUND
    assert rig.mid not in str(foreign.value)


def test_another_owner_cannot_create_or_list_missions_in_someone_elses_venture(rig):
    with pytest.raises(LookupError) as foreign:
        rig.b.create(rig.alice, objective="x", risk_tier="R0", budget_cents=0, actor="chat")
    with pytest.raises(LookupError) as unknown:
        rig.b.create("no-such-venture", objective="x", risk_tier="R0", budget_cents=0, actor="chat")
    assert str(foreign.value) == str(unknown.value)
    with pytest.raises(LookupError):
        rig.b.list(rig.alice)
    assert [m["mission_id"] for m in rig.a.list(rig.alice)] == [rig.mid]


def _snapshot(rig) -> dict:
    conn = rig.vstore.conn
    return {"missions": conn.execute("SELECT mission_id, status, contract_json, completion_state FROM missions"
                                     " ORDER BY mission_id").fetchall(),
            "evidence": conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]}


def test_refused_calls_change_nothing(rig):
    before = _snapshot(rig)
    for call in _calls(rig).values():
        with pytest.raises(LookupError):
            call(rig.b, rig.mid)
    assert _snapshot(rig) == before


def test_a_mission_store_needs_an_owner(rig):
    with pytest.raises(ValueError):
        wm.MissionStore(rig.vstore)


# --- status moves only along the mission state machine ---------------------------------------------


def test_the_mission_lifecycle_runs_and_each_move_is_an_event(rig, tmp_path):
    a, mid = rig.a, rig.mid
    a.transition(mid, "awaiting_plan", reason="contract accepted", actor="human")
    plan = {"units": [{"key": "do", "kind": "do", "goal": "Do it"},
                      {"key": "check", "kind": "verify", "verifies": "do", "goal": "Check it"}], "edges": []}
    a.add_plan(mid, plan, actor="chat", ready=lambda component, op: None)
    WorkGraph(a, tmp_path / "units.db").materialize(mid, actor="code")  # the only way to "active"
    for to in ("waiting", "active", "closing"):
        a.transition(mid, to, reason=f"to {to}", actor="code")
    a.close(mid, wc.Outcome("qualified-draft", reasons=("one check unmeasured",)), actor="code")
    row = a.get(mid)
    assert (row["status"], row["completion_state"]) == ("done", "qualified-draft")
    assert row["outcome"]["reasons"] == ["one check unmeasured"]
    moves = [(e["detail"]["from"], e["detail"]["to"]) for e in a.events(mid, kind="mission.status")]
    assert moves == [("eliciting", "awaiting_plan"), ("awaiting_plan", "active"), ("active", "waiting"),
                     ("waiting", "active"), ("active", "closing")]
    assert next(e["kind"] for e in a.events(mid)) == "mission.created"
    assert a.events(mid)[-1]["kind"] == "mission.closed"


@pytest.mark.parametrize("to", ["active", "closing", "done", "nonsense"])
def test_an_illegal_move_is_refused_and_changes_nothing(rig, to):
    before = _snapshot(rig)
    with pytest.raises(ValueError):
        rig.a.transition(rig.mid, to, reason="skip ahead", actor="code")
    assert _snapshot(rig) == before


def test_close_only_from_closing_and_cancel_is_blocked_escalated(rig):
    with pytest.raises(ValueError):
        rig.a.close(rig.mid, wc.Outcome("verified"), actor="code")
    rig.a.cancel(rig.mid, reason="the owner withdrew it", actor="human")
    row = rig.a.get(rig.mid)
    assert (row["status"], row["completion_state"]) == ("cancelled", "blocked-escalated")


def test_a_finished_mission_never_changes_even_by_sql(rig):
    rig.a.cancel(rig.mid, reason="done with it", actor="human")
    with pytest.raises(ValueError):
        rig.a.transition(rig.mid, "awaiting_plan", reason="revive", actor="code")
    with pytest.raises(sqlite3.DatabaseError), rig.vstore.conn:
        rig.vstore.conn.execute("UPDATE missions SET status='active' WHERE mission_id=?", (rig.mid,))
    with pytest.raises(sqlite3.DatabaseError), rig.vstore.conn:
        rig.vstore.conn.execute("DELETE FROM missions WHERE mission_id=?", (rig.mid,))


def test_the_contract_is_set_only_while_eliciting(rig):
    rig.a.set_contract(rig.mid, _contract().model_copy(update={"deadline": "2026-10-09"}), actor="chat")
    assert rig.a.contract(rig.mid).deadline == "2026-10-09"
    rig.a.transition(rig.mid, "awaiting_plan", reason="owner accepted", actor="human")
    with pytest.raises(ValueError):
        rig.a.set_contract(rig.mid, _contract(), actor="chat")


# --- events are evidence rows, written once per key -----------------------------------------------


def test_an_event_with_the_same_key_is_stored_once(rig):
    first = rig.a.record_event(rig.mid, "message", actor="worker:w1", detail={"to": "w2"}, idem_key="m-1")
    again = rig.a.record_event(rig.mid, "message", actor="worker:w1", detail={"to": "w2"}, idem_key="m-1")
    assert first == again
    assert len(rig.a.events(rig.mid, kind="message")) == 1
    assert rig.a.events(rig.mid, kind="message")[0]["detail"]["actor"] == "worker:w1"


@pytest.mark.parametrize("kind, actor", [("not_a_kind", "code"), ("message", "someone"), ("message", "worker:")])
def test_unknown_event_kinds_and_actors_are_refused(rig, kind, actor):
    with pytest.raises(ValueError):
        rig.a.record_event(rig.mid, kind, actor=actor)


def test_evidence_is_append_only(rig):
    conn = rig.vstore.conn
    with pytest.raises(sqlite3.DatabaseError), conn:
        conn.execute("UPDATE evidence SET kind='forged'")
    with pytest.raises(sqlite3.DatabaseError), conn:
        conn.execute("DELETE FROM evidence")


def test_idempotency_keys_are_per_venture_and_an_empty_key_never_collides(rig):
    v = rig.vstore
    assert v.record_evidence(rig.alice, "note", idem_key="k") == v.record_evidence(rig.alice, "note", idem_key="k")
    assert v.record_evidence(rig.bob, "note", idem_key="k") != v.record_evidence(rig.alice, "note", idem_key="k")
    assert v.record_evidence(rig.alice, "note") != v.record_evidence(rig.alice, "note")


# --- Codex review of T2 (2026-10-01): a mutation and its event commit together, or not at all -------


def _fail_event(rig, kind: str) -> None:
    rig.vstore.conn.execute(f"CREATE TEMP TRIGGER inject_{kind.replace('.', '_')} BEFORE INSERT ON main.evidence"
                            f" WHEN NEW.kind='{kind}' BEGIN SELECT RAISE(ABORT, 'injected'); END;")


def _unfail_event(rig, kind: str) -> None:
    rig.vstore.conn.execute(f"DROP TRIGGER temp.inject_{kind.replace('.', '_')}")


@pytest.mark.parametrize("kind, call", [
    ("mission.closed", lambda r: r.a.cancel(r.mid, reason="x", actor="human")),
    ("mission.status", lambda r: r.a.transition(r.mid, "awaiting_plan", reason="x", actor="human")),
    ("mission.contract", lambda r: r.a.set_contract(r.mid, _contract().model_copy(update={"deadline": "2027-01-01"}),
                                                    actor="chat")),
])
def test_a_failed_event_write_leaves_the_mission_unchanged_and_retryable(rig, kind, call):
    before = _snapshot(rig)
    _fail_event(rig, kind)
    with pytest.raises(sqlite3.DatabaseError):
        call(rig)
    assert _snapshot(rig) == before, "the mutation committed without its history"
    _unfail_event(rig, kind)
    call(rig)
    assert rig.a.events(rig.mid, kind=kind), "the retry recorded its event"


def test_a_failed_created_event_leaves_no_mission(rig):
    _fail_event(rig, "mission.created")
    with pytest.raises(sqlite3.DatabaseError):
        rig.a.create(rig.alice, objective="x", risk_tier="R0", budget_cents=0, actor="chat")
    assert [m["mission_id"] for m in rig.a.list(rig.alice)] == [rig.mid]


def test_set_contract_refuses_a_bad_actor_before_writing(rig):
    before = _snapshot(rig)
    with pytest.raises(ValueError):
        rig.a.set_contract(rig.mid, _contract().model_copy(update={"deadline": "2027-01-01"}), actor="invalid")
    assert _snapshot(rig) == before


def test_replace_cannot_rewrite_evidence_or_a_mission(rig):
    conn = rig.vstore.conn
    ev = rig.a.events(rig.mid)[0]
    keyed = rig.vstore.record_evidence(rig.alice, "note", idem_key="kept")
    with pytest.raises(sqlite3.DatabaseError), conn:
        conn.execute("INSERT OR REPLACE INTO evidence (evidence_id, venture_id, kind, ref, detail_json, seq, at)"
                     " VALUES (?, ?, 'forged', '', '{}', 1, 'x')", (ev["evidence_id"], rig.alice))
    with pytest.raises(sqlite3.DatabaseError), conn:
        conn.execute("INSERT OR REPLACE INTO evidence (evidence_id, venture_id, kind, ref, detail_json, seq, at,"
                     " idem_key) VALUES ('fresh', ?, 'forged', '', '{}', 1, 'x', 'kept')", (rig.alice,))
    assert [e["evidence_id"] for e in rig.vstore.evidence(rig.alice, kind="note")] == [keyed]
    rig.a.cancel(rig.mid, reason="finished", actor="human")
    with pytest.raises(sqlite3.DatabaseError), conn:
        conn.execute("INSERT OR REPLACE INTO missions (mission_id, venture_id, objective, risk_tier, status,"
                     " created_at, updated_at) VALUES (?, ?, 'x', 'R0', 'active', 'x', 'x')", (rig.mid, rig.alice))
    assert rig.a.get(rig.mid)["status"] == "cancelled"


def test_an_existing_venture_db_gains_the_idempotency_column_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old_venture.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE evidence (evidence_id TEXT PRIMARY KEY, venture_id TEXT NOT NULL, kind TEXT NOT NULL,"
        " ref TEXT NOT NULL DEFAULT '', detail_json TEXT NOT NULL DEFAULT '{}', seq INTEGER NOT NULL, at TEXT NOT NULL);"
        " INSERT INTO evidence VALUES ('e1', 'v1', 'payment', 'b1', '{}', 1, '2026-09-01T00:00:00+00:00');")
    old.commit()
    old.close()
    vstore = store.VentureStore(path)
    cols = {r[1] for r in vstore.conn.execute("PRAGMA table_info(evidence)")}
    assert "idem_key" in cols
    assert [e["evidence_id"] for e in vstore.evidence("v1")] == ["e1"]


# --- Codex review of T4-T6: the public event writer is owner-scoped -------------------------------


def test_another_owner_cannot_append_an_event_to_a_mission_it_does_not_hold(rig):
    """Codex: Bob's store wrote an artifact row into Alice's mission history by naming her ids."""
    before = rig.a.events(rig.mid)
    for mission_id in (rig.mid, "no-such-mission"):
        with pytest.raises(LookupError) as refused, rig.vstore.conn:
            rig.b.append_event(mission_id, "artifact", "code", {"content": "injected"})
        assert str(refused.value) == store.NOT_FOUND
    assert rig.a.events(rig.mid) == before
    with rig.vstore.conn:  # the survival control: the owner's own append is written, to her venture
        rig.a.append_event(rig.mid, "artifact", "code", {"content": "hers"})
    mine, = rig.a.events(rig.mid, kind="artifact")
    assert (mine["venture_id"], mine["detail"]["content"]) == (rig.alice, "hers")
