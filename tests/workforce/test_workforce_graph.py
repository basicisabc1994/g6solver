# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Plans and the work graph (workforce OS plan, P0 T3).

Pinned: code validates a plan before anything is stored, naming the unit and the rule for every
problem at once; a consumer depends on the VERIFY unit of what it consumes, never on the producer,
so "complete" only flows after verification; plan revisions are append-only and commit with their
event; units are task_tracker rows that always carry the owner, materialized idempotently whatever
step a crash interrupts; a unit's spec lives in the plan, so a tracker write cannot change it; a
complete unit is never claimed again; another owner sees nothing. Offline, temp dbs.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest
from g6.workforce.tracker import TaskTrackerStore
from g6.workforce import store
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import plans as wp

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
KEYS = ("reproduce", "check-reproduce", "patch", "check-patch")


def READY(component: str, op: str) -> None:
    """Every capability ready: these tests are about the graph; readiness is test_workforce_capabilities."""
    return


def _plan(**over) -> dict:
    """reproduce > patch, each with its own verify unit; patch consumes what reproduce produced."""
    plan = {"units": [
        {"key": "reproduce", "kind": "do", "goal": "Run the named test and record how it fails",
         "capabilities": [["coder", "run_tests"]], "max_attempts": 2, "budget_cents": 40},
        {"key": "check-reproduce", "kind": "verify", "verifies": "reproduce", "goal": "The failure is real"},
        {"key": "patch", "kind": "do", "goal": "Change the code, not the test", "inputs": ["reproduce"],
         "priority": 5},
        {"key": "check-patch", "kind": "verify", "verifies": "patch", "goal": "Every test passes",
         "checks": [{"name": "suite passes", "predicate": "coder.run_tests", "params": {"all": True}}]},
    ], "edges": []}
    plan.update(over)
    return plan


def _unit(plan: dict, key: str) -> dict:
    return next(u for u in plan["units"] if u["key"] == key)


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    alice = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    bob = vstore.create_venture("bob", "Bob's", **_CAPS)["venture_id"]
    a = wm.MissionStore(vstore.for_owner("alice"))
    b = wm.MissionStore(vstore.for_owner("bob"))
    mid = a.create(alice, objective="Fix the failing GST test", risk_tier="R1", budget_cents=100, actor="chat")
    a.transition(mid, "awaiting_plan", reason="contract accepted", actor="human")
    units = tmp_path / "workforce" / "units.db"
    return SimpleNamespace(vstore=vstore, alice=alice, bob=bob, a=a, b=b, mid=mid, units=units,
                           ga=wg.WorkGraph(a, units), gb=wg.WorkGraph(b, units))


def _rows(rig) -> list[dict]:
    """Every tracker row, read as the test (no owner filter): what is really in units.db."""
    return TaskTrackerStore(str(rig.units)).list_tasks(limit=10_000)


def _ids(rig) -> dict[str, str]:
    return {r["title"]: r["id"] for r in _rows(rig)}


def _upstream(rig, key: str) -> set[str]:
    ids = _ids(rig)
    by_id = {v: k for k, v in ids.items()}
    deps = TaskTrackerStore(str(rig.units)).get_dependencies(ids[key])["depends_on"]
    return {by_id[d["depends_on_id"]] for d in deps}


# --- code validates a plan ------------------------------------------------------------------------


def test_a_valid_plan_parses_with_defaults_and_a_stable_digest():
    plan = wp.parse_plan(_plan(), risk_tier="R1", budget_cents=100)
    assert tuple(u.key for u in plan.units) == KEYS
    unit = plan.unit("check-reproduce")
    assert (unit.kind, unit.verifies, unit.risk, unit.max_attempts, unit.budget_cents) == (
        "verify", "reproduce", "R0", 1, 0)
    assert plan.unit("reproduce").capabilities == (("coder", "run_tests"),)
    assert plan.unit("check-patch").checks[0].predicate == "coder.run_tests"
    reordered = json.dumps(dict(reversed(list(_plan().items()))))
    assert wp.parse_plan(reordered, risk_tier="R1", budget_cents=100).sha256 == plan.sha256
    assert wp.parse_plan(plan.to_json(), risk_tier="R1", budget_cents=100) == plan


def test_a_consumer_depends_on_the_verify_unit_never_on_the_producer():
    plan = wp.parse_plan(_plan(), risk_tier="R1", budget_cents=100)
    assert plan.depends_on() == {"reproduce": frozenset(), "check-reproduce": frozenset({"reproduce"}),
                                 "patch": frozenset({"check-reproduce"}), "check-patch": frozenset({"patch"})}
    assert plan.order().index("check-reproduce") < plan.order().index("patch")


def test_an_explicit_edge_is_rewritten_the_same_way_and_an_artifact_input_is_not_a_dependency():
    raw = _plan(edges=[["reproduce", "patch"]])
    _unit(raw, "patch")["inputs"] = ["artifact:ev_123"]
    assert wp.parse_plan(raw, risk_tier="R1", budget_cents=100).depends_on()["patch"] == frozenset(
        {"check-reproduce"})


def _bad(mutate):
    raw = _plan()
    mutate(raw)
    return raw


REJECTIONS = {
    "cycle": (lambda p: p.update(edges=[["patch", "reproduce"]]), "cycle"),
    "unverified do unit": (lambda p: p["units"].pop(), "unit 'patch': every do unit needs a verify unit"),
    "verifies unknown": (lambda p: _unit(p, "check-patch").update(verifies="nope"), "unit 'check-patch'"),
    "verifies a verify unit": (lambda p: _unit(p, "check-patch").update(verifies="check-reproduce"),
                               "unit 'check-patch'"),
    "do names verifies": (lambda p: _unit(p, "patch").update(verifies="reproduce"), "unit 'patch'"),
    "duplicate key": (lambda p: p["units"].append(dict(_unit(p, "patch"))), "unit 'patch': duplicate key"),
    "bad key": (lambda p: _unit(p, "patch").update(key="Patch Now"), "unit 'Patch Now'"),
    "unknown field": (lambda p: _unit(p, "patch").update(tool="shell"), "unit 'patch': unknown field"),
    "unknown kind": (lambda p: _unit(p, "patch").update(kind="think"), "unit 'patch': kind"),
    "empty goal": (lambda p: _unit(p, "patch").update(goal="  "), "unit 'patch': goal"),
    "unknown input": (lambda p: _unit(p, "patch").update(inputs=["reproduc"]), "unit 'patch': input 'reproduc'"),
    "unknown edge end": (lambda p: p.update(edges=[["reproduce", "deploy"]]), "edge"),
    "risk above the mission": (lambda p: _unit(p, "patch").update(risk="R2"), "unit 'patch': risk"),
    "unknown risk": (lambda p: _unit(p, "patch").update(risk="high"), "unit 'patch': risk"),
    "over the mission budget": (lambda p: _unit(p, "patch").update(budget_cents=61), "budget"),
    "negative budget": (lambda p: _unit(p, "patch").update(budget_cents=-1), "unit 'patch': budget_cents"),
    "bool budget": (lambda p: _unit(p, "patch").update(budget_cents=True), "unit 'patch': budget_cents"),
    "no attempts": (lambda p: _unit(p, "patch").update(max_attempts=0), "unit 'patch': max_attempts"),
    "too many attempts": (lambda p: _unit(p, "patch").update(max_attempts=9), "unit 'patch': max_attempts"),
    "half a capability": (lambda p: _unit(p, "patch").update(capabilities=[["coder"]]),
                          "unit 'patch': capabilities"),
    "malformed check": (lambda p: _unit(p, "check-patch").update(checks=[{"name": "x"}]),
                        "unit 'check-patch': checks"),
    "unknown plan field": (lambda p: p.update(notes="trust me"), "unknown field"),
    "no units": (lambda p: p.update(units=[]), "at least one unit"),
}


@pytest.mark.parametrize("name", sorted(REJECTIONS))
def test_a_bad_plan_is_refused_naming_the_unit_and_the_rule(name):
    mutate, expected = REJECTIONS[name]
    with pytest.raises(wp.PlanRejected) as refused:
        wp.parse_plan(_bad(mutate), risk_tier="R1", budget_cents=100)
    assert any(expected in problem for problem in refused.value.problems), refused.value.problems


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "null"])
def test_a_plan_that_is_not_a_json_object_is_refused(text):
    with pytest.raises(wp.PlanRejected):
        wp.parse_plan(text, risk_tier="R1", budget_cents=100)


def test_every_problem_is_reported_at_once():
    raw = _plan()
    _unit(raw, "patch").update(kind="think")
    _unit(raw, "reproduce").update(budget_cents=-5)
    with pytest.raises(wp.PlanRejected) as refused:
        wp.parse_plan(raw, risk_tier="R1", budget_cents=100)
    text = " | ".join(refused.value.problems)
    assert "unit 'patch': kind" in text and "unit 'reproduce': budget_cents" in text


# --- plan revisions -------------------------------------------------------------------------------


def test_a_plan_revision_is_stored_with_its_event(rig):
    assert rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY) == 1
    revision, plan = rig.a.plan(rig.mid)
    assert revision == 1 and plan == wp.parse_plan(_plan(), risk_tier="R1", budget_cents=100)
    (event,) = rig.a.events(rig.mid, kind="plan.revision")
    assert event["detail"] == {"revision": 1, "plan_sha": plan.sha256, "units": 4, "actor": "chat", "origin": ""}
    assert rig.a.add_plan(rig.mid, _plan(edges=[["reproduce", "patch"]]), actor="chat", ready=READY) == 2
    assert rig.a.plan(rig.mid)[0] == 2 and rig.a.plan(rig.mid, revision=1)[1] == plan


def test_a_rejected_plan_stores_nothing(rig):
    with pytest.raises(wp.PlanRejected):
        rig.a.add_plan(rig.mid, _plan(edges=[["patch", "reproduce"]]), actor="chat", ready=READY)
    assert rig.vstore.conn.execute("SELECT COUNT(*) FROM mission_plans").fetchone()[0] == 0
    assert rig.a.events(rig.mid, kind="plan.revision") == []
    with pytest.raises(LookupError):
        rig.a.plan(rig.mid)


def test_a_plan_is_judged_against_this_missions_risk_and_budget(rig):
    tight = rig.a.create(rig.alice, objective="Cheap and safe", risk_tier="R0", budget_cents=10, actor="chat")
    rig.a.transition(tight, "awaiting_plan", reason="accepted", actor="human")
    with pytest.raises(wp.PlanRejected) as refused:
        rig.a.add_plan(tight, _plan(), actor="chat", ready=READY)
    assert any("budget" in p for p in refused.value.problems)


@pytest.mark.parametrize("status", ["eliciting", "active"])
def test_a_plan_is_added_only_while_the_mission_awaits_one(rig, status):
    if status == "eliciting":
        mid = rig.a.create(rig.alice, objective="Still talking", risk_tier="R1", budget_cents=100, actor="chat")
    else:
        mid = rig.mid
        rig.a.add_plan(mid, _plan(), actor="chat", ready=READY)
        rig.ga.materialize(mid, actor="code")
    with pytest.raises(ValueError, match="awaiting_plan"):
        rig.a.add_plan(mid, _plan(), actor="chat", ready=READY)


def test_a_plan_needs_a_known_actor_before_anything_is_read(rig):
    with pytest.raises(ValueError):
        rig.a.add_plan(rig.mid, _plan(), actor="someone", ready=READY)
    assert rig.a.events(rig.mid, kind="plan.revision") == []


def test_another_owners_plan_is_refused_exactly_as_an_unknown_one(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    for call in (lambda m: rig.b.add_plan(m, _plan(), actor="chat", ready=READY), lambda m: rig.b.plan(m)):
        with pytest.raises(LookupError) as foreign:
            call(rig.mid)
        with pytest.raises(LookupError) as unknown:
            call("no-such-mission")
        assert str(foreign.value) == str(unknown.value) == store.NOT_FOUND


def test_plan_revisions_are_append_only(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    conn = rig.vstore.conn
    for sql in ("UPDATE mission_plans SET plan_json='{}'", "DELETE FROM mission_plans",
                ("INSERT OR REPLACE INTO mission_plans (mission_id, revision, plan_json, plan_sha, actor, created_at)"
                 " SELECT mission_id, revision, '{}', '', 'code', created_at FROM mission_plans")):
        with pytest.raises(sqlite3.DatabaseError), conn:
            conn.execute(sql)
    assert rig.a.plan(rig.mid)[1].sha256 == wp.parse_plan(_plan(), risk_tier="R1", budget_cents=100).sha256


def test_a_failed_revision_event_stores_no_revision(rig):
    rig.vstore.conn.execute("CREATE TEMP TRIGGER inject_plan BEFORE INSERT ON main.evidence"
                            " WHEN NEW.kind='plan.revision' BEGIN SELECT RAISE(ABORT, 'injected'); END;")
    with pytest.raises(sqlite3.DatabaseError):
        rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    assert rig.vstore.conn.execute("SELECT COUNT(*) FROM mission_plans").fetchone()[0] == 0
    rig.vstore.conn.execute("DROP TRIGGER temp.inject_plan")
    assert rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY) == 1


# --- the work graph -------------------------------------------------------------------------------


def test_a_cycle_is_refused_with_zero_units_created(rig):
    with pytest.raises(wp.PlanRejected):
        rig.a.add_plan(rig.mid, _plan(edges=[["patch", "reproduce"]]), actor="chat", ready=READY)
    with pytest.raises(LookupError):
        rig.ga.materialize(rig.mid, actor="code")
    assert _rows(rig) == [] and rig.a.get(rig.mid)["status"] == "awaiting_plan"


def test_materialize_makes_one_owned_unit_per_key_with_the_rewritten_edges(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    rows = _rows(rig)
    assert sorted(ids) == sorted(KEYS) and {r["id"] for r in rows} == set(ids.values())
    assert {(r["owner"], r["project_id"], r["status"]) for r in rows} == {("alice", rig.mid, "pending")}
    assert _upstream(rig, "patch") == {"check-reproduce"} and _upstream(rig, "check-reproduce") == {"reproduce"}
    assert _upstream(rig, "check-patch") == {"patch"} and _upstream(rig, "reproduce") == set()
    assert rig.a.get(rig.mid)["status"] == "active"
    (event,) = rig.a.events(rig.mid, kind="unit.materialized")
    assert event["detail"]["revision"] == 1 and event["detail"]["units"] == ids


def test_materializing_twice_changes_nothing(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    first = rig.ga.materialize(rig.mid, actor="code")
    assert rig.ga.materialize(rig.mid, actor="code") == first
    assert len(_rows(rig)) == 4 and len(rig.a.events(rig.mid, kind="unit.materialized")) == 1


def test_a_consumer_cannot_be_claimed_until_its_verifier_completes(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    assert rig.ga.claimable(rig.mid) == ["reproduce"]
    rig.ga.claim(rig.mid, "reproduce", agent="worker:w1")
    rig.ga.complete(rig.mid, "reproduce")
    assert rig.ga.claimable(rig.mid) == ["check-reproduce"]
    with pytest.raises(ValueError, match="check-reproduce"):
        rig.ga.claim(rig.mid, "patch", agent="worker:w2")
    # The edge is in the tracker too, so a caller that skips the graph is refused by the store itself.
    assert "error" in TaskTrackerStore(str(rig.units)).claim_task(ids["patch"], owner="alice")
    rig.ga.claim(rig.mid, "check-reproduce", agent="worker:v1")
    rig.ga.complete(rig.mid, "check-reproduce")
    assert rig.ga.claimable(rig.mid) == ["patch"]
    assert rig.ga.claim(rig.mid, "patch", agent="worker:w2")["status"] == "in_progress"


class _Crash(TaskTrackerStore):
    """A tracker that dies once, at the k-th call of one method, before or after the write lands."""

    def __init__(self, path, method: str, k: int, when: str) -> None:
        super().__init__(str(path))
        self.method, self.k, self.when, self.n = method, k, when, 0

    def _around(self, method: str, write):
        if method != self.method:
            return write()
        self.n += 1
        if self.n == self.k and self.when == "before":
            raise RuntimeError("injected crash")
        result = write()
        if self.n == self.k and self.when == "after":
            raise RuntimeError("injected crash")
        return result

    def create_task(self, *args, **kwargs):
        return self._around("create_task", lambda: super(_Crash, self).create_task(*args, **kwargs))

    def add_dependency(self, *args, **kwargs):
        return self._around("add_dependency", lambda: super(_Crash, self).add_dependency(*args, **kwargs))


CRASHES = ([("create_task", k, w) for k in range(1, 5) for w in ("before", "after")]
           + [("add_dependency", k, w) for k in range(1, 4) for w in ("before", "after")])


@pytest.mark.parametrize(("method", "k", "when"), CRASHES)
def test_materialization_survives_a_crash_at_any_tracker_write(rig, method, k, when):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    crashing = wg.WorkGraph(rig.a, rig.units, tracker=_Crash(rig.units, method, k, when))
    with pytest.raises(RuntimeError, match="injected crash"):
        crashing.materialize(rig.mid, actor="code")
    assert rig.a.get(rig.mid)["status"] == "awaiting_plan"
    ids = wg.WorkGraph(rig.a, rig.units).materialize(rig.mid, actor="code")
    assert sorted(r["title"] for r in _rows(rig)) == sorted(KEYS) and set(_ids(rig).values()) == set(ids.values())
    assert _upstream(rig, "patch") == {"check-reproduce"} and _upstream(rig, "check-patch") == {"patch"}
    assert rig.a.get(rig.mid)["status"] == "active"
    assert len(rig.a.events(rig.mid, kind="unit.materialized")) == 1


def test_materialization_survives_a_crash_before_the_mission_turns_active(rig, monkeypatch):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)

    def boom(*args, **kwargs):
        raise RuntimeError("injected crash")

    monkeypatch.setattr(rig.a, "activate", boom)
    with pytest.raises(RuntimeError):
        rig.ga.materialize(rig.mid, actor="code")
    monkeypatch.undo()
    rig.ga.materialize(rig.mid, actor="code")
    assert len(_rows(rig)) == 4 and rig.a.get(rig.mid)["status"] == "active"
    assert len(rig.a.events(rig.mid, kind="unit.materialized")) == 1


def test_a_tracker_that_disagrees_with_the_plan_blocks_materialization(rig):
    """A unit the plan does not name is never silently adopted (one pending row for a key the plan names
    is what a crash leaves behind, and is adopted: see the crash test above)."""
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    TaskTrackerStore(str(rig.units)).create_task(title="deploy", project_id=rig.mid, component="workforce",
                                                 owner="alice")
    with pytest.raises(ValueError, match="deploy"):
        rig.ga.materialize(rig.mid, actor="code")
    assert len(_rows(rig)) == 1 and rig.a.get(rig.mid)["status"] == "awaiting_plan"


def test_the_units_db_itself_refuses_a_second_unit_for_a_key(rig):
    """Two rows for one key cannot exist at all: a unique index on (owner, mission, key), not a check."""
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    rig.ga.materialize(rig.mid, actor="code")
    with pytest.raises(sqlite3.IntegrityError):
        TaskTrackerStore(str(rig.units)).create_task(title="patch", project_id=rig.mid, component="workforce",
                                                     owner="alice")
    assert len(_rows(rig)) == 4


# --- Codex review of T3: crash windows, stale claims, racing writers, a swapped plan -----------------


class _Interleave(TaskTrackerStore):
    """A tracker that lets another writer take a turn once, just before its first call of ``method``."""

    def __init__(self, path, method: str, their_turn) -> None:
        super().__init__(str(path))
        self.method, self.their_turn, self.fired = method, their_turn, False

    def _turn(self, method: str) -> None:
        if method == self.method and not self.fired:
            self.fired = True
            self.their_turn()

    def create_task(self, *args, **kwargs):
        self._turn("create_task")
        return super().create_task(*args, **kwargs)

    def claim_task(self, *args, **kwargs):
        self._turn("claim_task")
        return super().claim_task(*args, **kwargs)


@pytest.mark.parametrize(("method", "k"), [("create_task", 2), ("add_dependency", 1), ("add_dependency", 3)])
def test_nothing_is_claimed_before_the_mission_is_active_and_a_unit_that_moved_is_refused(rig, method, k):
    """Inside a crash window a caller that skips the graph can still claim a row; recovery refuses it."""
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    with pytest.raises(RuntimeError):
        wg.WorkGraph(rig.a, rig.units, tracker=_Crash(rig.units, method, k, "before")).materialize(
            rig.mid, actor="code")
    with pytest.raises(ValueError, match="active"):
        rig.ga.claim(rig.mid, "reproduce", agent="worker:w1")
    TaskTrackerStore(str(rig.units)).claim_task(_ids(rig)["reproduce"], owner="alice")
    with pytest.raises(ValueError, match="reproduce"):
        rig.ga.materialize(rig.mid, actor="code")
    assert rig.a.get(rig.mid)["status"] == "awaiting_plan"


def test_a_stale_claim_cannot_reopen_a_unit_another_writer_completed(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    other = wg.WorkGraph(rig.a, rig.units)

    def their_turn():
        other.claim(rig.mid, "reproduce", agent="worker:w2")
        other.complete(rig.mid, "reproduce")

    late = wg.WorkGraph(rig.a, rig.units, tracker=_Interleave(rig.units, "claim_task", their_turn))
    with pytest.raises(ValueError, match="reproduce"):
        late.claim(rig.mid, "reproduce", agent="worker:w1")
    assert TaskTrackerStore(str(rig.units)).get_task(ids["reproduce"])["status"] == "complete"


def test_a_complete_unit_is_final_even_for_a_caller_that_skips_the_graph(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    rig.ga.claim(rig.mid, "reproduce", agent="worker:w1")
    rig.ga.complete(rig.mid, "reproduce")
    with pytest.raises(sqlite3.DatabaseError):
        TaskTrackerStore(str(rig.units)).claim_task(ids["reproduce"], owner="alice")
    assert TaskTrackerStore(str(rig.units)).get_task(ids["reproduce"])["status"] == "complete"


def test_a_second_materialization_waits_its_turn_and_one_unit_per_key_results(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    refused = []

    def their_turn():
        try:
            wg.WorkGraph(rig.a, rig.units).materialize(rig.mid, actor="code")
        except ValueError as exc:
            refused.append(str(exc))

    ids = wg.WorkGraph(rig.a, rig.units, tracker=_Interleave(rig.units, "create_task", their_turn)).materialize(
        rig.mid, actor="code")
    assert refused and "materializ" in refused[0]
    assert sorted(r["title"] for r in _rows(rig)) == sorted(KEYS) and set(_ids(rig).values()) == set(ids.values())
    assert rig.a.get(rig.mid)["status"] == "active" and rig.ga.claimable(rig.mid) == ["reproduce"]


def test_an_expired_materialization_lease_is_taken_over(rig, monkeypatch):
    """A writer that died holding the lease does not strand the mission: after the lease ends, anyone resumes."""
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    monkeypatch.setattr(wg.WorkGraph, "_release", lambda self, mission_id: None)  # the process died: no release
    with pytest.raises(RuntimeError):
        wg.WorkGraph(rig.a, rig.units, tracker=_Crash(rig.units, "create_task", 2, "before")).materialize(
            rig.mid, actor="code")
    monkeypatch.undo()
    with pytest.raises(ValueError, match="materializ"):
        rig.ga.materialize(rig.mid, actor="code")
    with rig.vstore.conn:
        rig.vstore.conn.execute("UPDATE missions SET lease_until='2000-01-01T00:00:00+00:00'")
    rig.ga.materialize(rig.mid, actor="code")
    assert rig.a.get(rig.mid)["status"] == "active" and len(_rows(rig)) == 4


def test_a_plan_cannot_be_swapped_while_it_is_being_materialized(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    renamed = _plan()
    _unit(renamed, "patch")["key"] = "replacement"
    _unit(renamed, "check-patch")["verifies"] = "replacement"
    refused = []

    def their_turn():
        try:
            rig.a.add_plan(rig.mid, renamed, actor="chat", ready=READY)
        except ValueError as exc:
            refused.append(str(exc))

    wg.WorkGraph(rig.a, rig.units, tracker=_Interleave(rig.units, "create_task", their_turn)).materialize(
        rig.mid, actor="code")
    assert refused and "revision 1" in refused[0]
    assert rig.a.get(rig.mid)["plan_revision"] == 1
    assert [u["key"] for u in rig.ga.units(rig.mid)] == list(KEYS)


def test_a_mission_becomes_active_only_by_materializing_its_plan(rig):
    with pytest.raises(ValueError):
        rig.a.transition(rig.mid, "active", reason="skip the plan", actor="code")
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    with pytest.raises(ValueError):
        rig.a.transition(rig.mid, "active", reason="skip materializing", actor="code")
    assert rig.a.begin_materialize(rig.mid, holder="h1", actor="code") == 1
    with pytest.raises(ValueError, match="materialized"):  # the lease alone is not a materialization
        rig.a.activate(rig.mid, holder="h1", actor="code")
    with pytest.raises(ValueError, match="reserved"):
        rig.a.transition(rig.mid, "eliciting", reason="rethink", actor="human")
    assert rig.a.get(rig.mid)["status"] == "awaiting_plan"


@pytest.mark.parametrize("text", ["fix \ud800 now", json.loads('"\\udfff"')])
def test_text_sqlite_cannot_store_is_refused_before_anything_is_stored(text):
    raw = _plan()
    _unit(raw, "patch")["goal"] = text
    with pytest.raises(wp.PlanRejected, match="Unicode"):
        wp.parse_plan(raw, risk_tier="R1", budget_cents=100)


def test_the_spec_survives_fail_task(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    rig.ga.claim(rig.mid, "reproduce", agent="worker:w1")
    rig.ga.fail(rig.mid, "reproduce", "the runner timed out")
    row = TaskTrackerStore(str(rig.units)).get_task(ids["reproduce"])
    assert row["status"] == "failed" and json.loads(row["metadata_json"]) == {"error": "the runner timed out"}
    expected = wp.parse_plan(_plan(), risk_tier="R1", budget_cents=100).unit("reproduce")
    assert rig.ga.spec(rig.mid, "reproduce") == expected and expected.max_attempts == 2
    assert rig.ga.claimable(rig.mid) == ["reproduce"]


@pytest.mark.parametrize("finish", ["complete", "running"])
def test_a_finished_or_running_unit_is_never_claimed_again(rig, finish):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    rig.ga.claim(rig.mid, "reproduce", agent="worker:w1")
    if finish == "complete":
        rig.ga.complete(rig.mid, "reproduce")
    before = TaskTrackerStore(str(rig.units)).get_task(ids["reproduce"])["status"]
    with pytest.raises(ValueError, match=before):
        rig.ga.claim(rig.mid, "reproduce", agent="worker:w9")
    assert TaskTrackerStore(str(rig.units)).get_task(ids["reproduce"])["status"] == before
    assert "reproduce" not in rig.ga.claimable(rig.mid)


def test_a_unit_the_plan_does_not_name_is_refused(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    rig.ga.materialize(rig.mid, actor="code")
    with pytest.raises(KeyError):
        rig.ga.claim(rig.mid, "deploy", agent="worker:w1")


def test_another_owner_sees_no_units_and_moves_none(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    rig.ga.materialize(rig.mid, actor="code")
    before = [(r["id"], r["status"]) for r in _rows(rig)]
    calls = {
        "units": lambda m: rig.gb.units(m), "claimable": lambda m: rig.gb.claimable(m),
        "materialize": lambda m: rig.gb.materialize(m, actor="code"),
        "claim": lambda m: rig.gb.claim(m, "reproduce", agent="worker:x"),
        "complete": lambda m: rig.gb.complete(m, "reproduce"), "fail": lambda m: rig.gb.fail(m, "reproduce", "x"),
        "spec": lambda m: rig.gb.spec(m, "reproduce"),
    }
    for name, call in calls.items():
        with pytest.raises(LookupError) as foreign:
            call(rig.mid)
        with pytest.raises(LookupError) as unknown:
            call("no-such-mission")
        assert str(foreign.value) == str(unknown.value) == store.NOT_FOUND, name
    assert TaskTrackerStore(str(rig.units)).list_tasks(owner="bob") == []
    assert [(r["id"], r["status"]) for r in _rows(rig)] == before
    assert {r["owner"] for r in _rows(rig)} == {"alice"}


def test_units_report_their_key_kind_and_status(rig):
    rig.a.add_plan(rig.mid, _plan(), actor="chat", ready=READY)
    ids = rig.ga.materialize(rig.mid, actor="code")
    units = {u["key"]: u for u in rig.ga.units(rig.mid)}
    assert {k: (u["kind"], u["status"], u["task_id"]) for k, u in units.items()} == {
        k: ({"reproduce": "do", "patch": "do"}.get(k, "verify"), "pending", ids[k]) for k in KEYS}
