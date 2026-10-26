# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""One runner per mission: the runner lease and its fence (workforce OS plan, V2a).

Pinned: a mission's runner holds its lease; another runner is refused until the lease expires or is
released, and taking it bumps the fence and records who it was taken from. A runner whose lease was
taken cannot land a write (open, close, abandon or a mission move), so a clock that runs ahead can
hand the lease over early but never makes two writers. A new runner abandons, at once, an attempt left
open under an older lease (its runner died, unwound or was fenced out) and retries it, with no
duplicate call; an abandoned attempt whose worker may have acted (an op not declared read-only, or a test
bench op, which runs the owner's code) is escalated, never retried. Offline, temp dbs, fake components.
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from g6.workforce import store
from g6.workforce import bench
from g6.workforce import calls as wcall
from g6.workforce import capabilities as wcap
from g6.workforce import contracts as wc
from g6.workforce import graph as wg
from g6.workforce import missions as wm
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt
from g6.workforce import workers as ww

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
T0 = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)


def _contract(side_effect: str = "read") -> dict:
    return {
        ("fake_ledger", "total"): wcap.ParamContract.from_dict({
            "component": "fake_ledger", "op": "total", "required": {"numbers": "list"},
            "probe_params": {"numbers": [1]}, "expect_field": "total", "side_effect": side_effect,
            "check": "input_model"}),
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
    """The fake components; ``calls`` holds only calls that finished, so a duplicate action shows as two."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.kill_next = False  # the next ledger call kills the process mid-call (it never finishes)
        self.fail_next = False  # the next ledger call fails (the attempt closes blocked-escalated)
        self.during: list = []  # callables run inside the next ledger call (another process acting meanwhile)

    def __call__(self, component: str, op: str, params: dict):
        if component == "fake_ledger":
            while self.during:
                self.during.pop(0)()
            if self.kill_next:
                self.kill_next = False
                raise SystemExit("the runner's process was killed mid-call")
            if self.fail_next:
                self.fail_next = False
                raise ConnectionError("the ledger export is locked")
            self.calls.append((component, op))
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
        self.calls.append((component, op))
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}


PLAN = {"units": [
    {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
     "max_attempts": 2, "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}},
    {"key": "check-sum", "kind": "verify", "verifies": "sum", "goal": "The total is 6",
     "capabilities": [["fake_solver", "check"]],
     "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6}, "bind": {"total": "sum.total"},
                "expect": {"field": "satisfiable", "equals": False}, "kind": "formal"}}], "edges": []}


@pytest.fixture
def rig(tmp_path):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner("alice"))
    world = World()
    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts=_contract(), registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in _contract():
        assert caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    world.calls.clear()
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)
    mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0, actor="chat")
    missions.transition(mid, "awaiting_plan", reason="accepted", actor="chat")
    missions.add_plan(mid, PLAN, actor="chat", ready=caps.ready)
    graph.materialize(mid, actor="code")

    def runtime(holder: str, *, now=None, caps_=None):
        return wrt.Runtime(missions, graph, ledger, caps_ or caps, invoke=world, holder=holder,
                           now=now or (lambda: T0))

    def worker(key: str = "sum"):
        return ww.form_worker("doer", ceiling=wrt.ceiling_for(missions.get(mid)), contracts=caps.contract,
                              unit=missions.plan(mid)[1].unit(key))

    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, caps=caps, world=world, mid=mid,
                           runtime=runtime, worker=worker, vstore=vstore)


# --- the lease ------------------------------------------------------------------------------------


def test_one_runner_holds_a_mission_and_another_is_refused_until_the_lease_expires(rig):
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    with pytest.raises(wm.LeaseHeld, match="runner:a"):
        rig.missions.take_lease(rig.mid, holder="runner:b", now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS - 1))
    b = rig.missions.take_lease(rig.mid, holder="runner:b", now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS + 1))
    assert b.fence == a.fence + 1
    taken = [e["detail"] for e in rig.missions.events(rig.mid, kind="lease.taken")]
    assert [(t["holder"], t["previous"]) for t in taken] == [("runner:a", ""), ("runner:b", "runner:a")]


def test_a_released_lease_is_taken_at_once_and_a_renewed_one_is_kept(rig):
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    a = rig.missions.renew_lease(a, now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS - 1))
    with pytest.raises(wm.LeaseHeld):  # renewed: still a's a little past the first expiry
        rig.missions.take_lease(rig.mid, holder="runner:b", now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS + 1))
    rig.missions.release_lease(a)
    assert rig.missions.take_lease(rig.mid, holder="runner:b", now=T0).holder == "runner:b"
    with pytest.raises(wm.LeaseLost):
        rig.missions.renew_lease(a, now=T0)


def test_a_take_that_read_the_lease_before_its_holder_renewed_it_is_refused(rig, monkeypatch):
    """Codex V2: B reads A's expired lease, A renews, then B's write must not land on the renewed lease."""
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    stale = rig.missions.get(rig.mid)  # what B read: A's lease, about to expire
    rig.missions.renew_lease(a, now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS - 1))
    real_get = rig.missions.get
    monkeypatch.setattr(rig.missions, "get", lambda mid: stale if mid == rig.mid else real_get(mid))
    with pytest.raises(wm.LeaseHeld):
        rig.missions.take_lease(rig.mid, holder="runner:b", now=T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS + 1))
    monkeypatch.undo()
    assert rig.missions.get(rig.mid)["lease_holder"] == "runner:a"


def test_a_runner_whose_lease_was_taken_cannot_land_a_write(rig):
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    run = rig.ledger.open(rig.mid, "sum", rig.worker(), lease=a)
    assert (rig.ledger.runs(rig.mid)[0]["holder"], rig.ledger.runs(rig.mid)[0]["fence"]) == ("runner:a", a.fence)
    b = rig.missions.take_lease(rig.mid, holder="runner:b", now=T0 + timedelta(hours=1))
    before = len(rig.missions.events(rig.mid))
    with pytest.raises(wm.LeaseLost):
        rig.ledger.close(run.run_id, wc.Outcome(wc.QUALIFIED), lease=a)
    with pytest.raises(wm.LeaseLost):
        rig.ledger.abandon(run.run_id, "a's own reconcile", lease=a)
    with pytest.raises(wm.LeaseLost):
        rig.missions.transition(rig.mid, "closing", reason="a thinks it is done", actor="code", lease=a)
    with pytest.raises(wm.LeaseLost):  # nor start another attempt beside the new runner's
        rig.ledger.open(rig.mid, "check-sum", rig.worker(), lease=a)
    assert len(rig.missions.events(rig.mid)) == before and rig.ledger.open_runs(rig.mid)[0]["run_id"] == run.run_id
    assert rig.missions.get(rig.mid)["status"] == "active"
    rig.ledger.abandon(run.run_id, "its runner lost the lease", lease=b)  # the holder's write lands
    assert rig.ledger.runs(rig.mid)[0]["state"] == "abandoned"


def test_a_runner_that_lost_its_lease_cannot_move_a_unit_its_successor_took(rig, monkeypatch):
    """Codex V2: A closes a failed attempt and stalls before moving the unit; B takes over, settles A's attempt
    from the ledger and claims a retry; A wakes in the middle of B's attempt and must not move B's unit."""
    a = rig.runtime("runner:a")
    paused = []
    monkeypatch.setattr(a, "_settle", lambda *args: paused.append(args))  # A stalls right before the move
    rig.world.fail_next = True
    a.step(rig.mid, lease=rig.missions.take_lease(rig.mid, holder="runner:a", now=T0))
    monkeypatch.undo()
    (mission_id, spec, row, lease), = paused
    refused = []

    def a_resumes():
        with pytest.raises(wm.LeaseLost):
            a._settle(mission_id, spec, row, lease)
        refused.append(True)

    rig.world.during.append(a_resumes)  # runs inside B's retry, while B holds the unit
    b = rig.runtime("runner:b", now=lambda: T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS + 1))
    final = b.run(rig.mid)
    assert refused == [True]
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    assert [r["state"] for r in rig.ledger.runs(rig.mid, "sum")] == ["closed", "closed"]


def test_a_unit_moves_only_for_the_attempt_that_holds_it(rig):
    """The chat's own submissions carry no lease: the unit's assigned attempt is checked instead."""
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    run = rig.ledger.open(rig.mid, "sum", rig.worker(), lease=a)
    rig.graph.claim(rig.mid, "sum", agent=f"worker:{run.instance_id}")
    with pytest.raises(ValueError, match="another attempt"):
        rig.graph.fail(rig.mid, "sum", "a stale attempt's late move", agent="worker:wi_someone_else")
    assert {u["key"]: u["status"] for u in rig.graph.units(rig.mid)}["sum"] == "in_progress"
    rig.graph.fail(rig.mid, "sum", "its own attempt failed", agent=f"worker:{run.instance_id}")


def test_a_clock_that_runs_ahead_hands_the_lease_over_early_but_never_makes_two_writers(rig):
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    ahead = rig.runtime("runner:b", now=lambda: T0 + timedelta(hours=2))  # b's clock is two hours fast
    final = ahead.run(rig.mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    with pytest.raises(wm.LeaseLost):  # a, still sure of its lease by its own clock, cannot write
        rig.missions.renew_lease(a, now=T0)
    assert [r["holder"] for r in rig.ledger.runs(rig.mid)] == ["runner:b", "runner:b"]


def test_a_clock_that_runs_behind_waits_for_a_live_lease(rig):
    rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    behind = rig.runtime("runner:b", now=lambda: T0 - timedelta(hours=2))
    with pytest.raises(wm.LeaseHeld):
        behind.run(rig.mid)
    assert rig.ledger.runs(rig.mid) == [] and rig.world.calls == []


# --- recovery ----------------------------------------------------------------------------------------


def _crash_mid_attempt(rig, monkeypatch, holder: str = "runner:a"):
    """Runner ``holder`` opens an attempt at ``sum``, claims it, and is killed inside the component call
    (its lease stays held: a dead process releases nothing)."""
    first = rig.runtime(holder)
    rig.world.kill_next = True
    monkeypatch.setattr(rig.missions, "release_lease", lambda lease: None)
    with pytest.raises(SystemExit):
        first.run(rig.mid)
    monkeypatch.undo()
    orphan, = rig.ledger.open_runs(rig.mid)
    assert {u["key"]: u["status"] for u in rig.graph.units(rig.mid)}["sum"] == "in_progress"
    return orphan


def test_a_stale_lease_is_taken_only_once_it_expires(rig, monkeypatch):
    _crash_mid_attempt(rig, monkeypatch)
    with pytest.raises(wm.LeaseHeld):  # the dead runner's lease has not run out yet
        rig.runtime("runner:b", now=lambda: T0 + timedelta(seconds=5)).run(rig.mid)
    assert len(rig.ledger.runs(rig.mid)) == 1


def test_a_new_runner_abandons_an_attempt_left_under_an_older_lease_at_once_and_retries_it(rig, monkeypatch):
    orphan = _crash_mid_attempt(rig, monkeypatch)
    # Well inside the orphan's own wall clock (900 s), just past the dead runner's lease.
    later = rig.runtime("runner:b", now=lambda: T0 + timedelta(seconds=wm.RUN_LEASE_SECONDS + 1))
    final = later.run(rig.mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    runs = rig.ledger.runs(rig.mid, "sum")
    assert (runs[0]["run_id"], runs[0]["state"]) == (orphan["run_id"], "abandoned")
    assert [r["state"] for r in runs] == ["abandoned", "closed"]
    assert "lost the mission's lease" in wr.outcome_of(runs[0]).reasons[0]
    assert rig.world.calls.count(("fake_ledger", "total")) == 1  # the killed attempt never called: no duplicate


def test_an_abandoned_attempt_that_may_have_acted_is_escalated_never_retried(rig, monkeypatch):
    _crash_mid_attempt(rig, monkeypatch)
    # The same op, as V3 will let a plan declare one: it writes. Nothing records whether the call ran.
    writer = wcap.Capabilities(rig.vstore.for_owner("alice"), contracts=_contract("local_write"),
                               registry=FakeRegistry(), ops_for=lambda n: None, authority=SCRIPTED)
    later = rig.runtime("runner:b", now=lambda: T0 + timedelta(hours=1), caps_=writer)
    final = later.run(rig.mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED)
    assert [r["state"] for r in rig.ledger.runs(rig.mid, "sum")] == ["abandoned"]  # never retried
    status = {u["key"]: u["status"] for u in rig.graph.units(rig.mid)}
    assert status["sum"] == "blocked"
    assert any("may have acted" in r for r in final["outcome"]["reasons"]), final["outcome"]
    assert rig.world.calls == []


@pytest.mark.parametrize("op", ["run_tests", bench.IN_PLACE])
def test_a_cut_off_test_bench_attempt_is_escalated_even_from_a_read_only_verifier(tmp_path, monkeypatch, op):
    """Each op of the test bench runs the owner's code in a child process (run_tests_in_place: in the owner's own
    working tree), which a killed runner cannot see stop. Its shipped contract says read and the verifier is read-only,
    so only the runtime's list of ops that run code keeps the cut-off attempt from being run again (Codex V2)."""
    contract = wcap.load_contracts()[(bench.COMPONENT, op)]
    assert contract.side_effect == "read"  # the declaration alone would call the cut-off attempt harmless
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner("alice"))
    ran, kill = [], []

    def world(component: str, op_: str, params: dict) -> dict:
        if kill:
            kill.pop()
            raise SystemExit("the runner's process was killed while the bench ran the owner's tests")
        ran.append((component, op_))
        return {"ok": True, "result": {"passed": True}}

    caps = wcap.Capabilities(vstore.for_owner("alice"), contracts={(bench.COMPONENT, op): contract},
                             registry=FakeRegistry(), ops_for=lambda n: None, authority=SCRIPTED)
    assert caps.probe(bench.COMPONENT, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    ran.clear()
    graph, ledger = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db"), wr.RunLedger(missions)
    mid = missions.create(venture, objective="Make the failing add test pass", risk_tier="R1", budget_cents=0,
                          actor="chat")
    missions.transition(mid, "awaiting_plan", reason="accepted", actor="chat")
    missions.add_plan(mid, {"units": [
        {"key": "patch", "kind": "do", "goal": "Fix add"},
        {"key": "check-patch", "kind": "verify", "verifies": "patch", "max_attempts": 2, "goal": "The named test passes",
         "capabilities": [[bench.COMPONENT, op]],
         "oracle": {"capability": [bench.COMPONENT, op], "params": {"repo": "calc"}, "bind": {"tests": "patch.tests"},
                    "expect": {"field": "passed", "equals": True}, "kind": "test"}}], "edges": []},
        actor="chat", ready=caps.ready)
    graph.materialize(mid, actor="code")

    def runtime(holder: str, at: datetime):
        return wrt.Runtime(missions, graph, ledger, caps, invoke=world, holder=holder, now=lambda: at)

    first = runtime("runner:a", T0)
    assert first.run(mid)["status"] == "waiting"  # for the chat's patch
    first.submit(mid, "patch", {"tests": ["tests/test_calc.py::test_add"]})
    kill.append(True)
    monkeypatch.setattr(missions, "release_lease", lambda lease: None)  # a dead process releases nothing
    with pytest.raises(SystemExit):
        first.run(mid)
    monkeypatch.undo()
    assert [r["unit_key"] for r in ledger.open_runs(mid)] == ["check-patch"]

    final = runtime("runner:b", T0 + timedelta(hours=1)).run(mid)
    assert [r["state"] for r in ledger.runs(mid, "check-patch")] == ["abandoned"]  # never retried
    assert ran == []  # the bench was never called again
    assert (final["status"], final["completion_state"]) == ("done", wc.BLOCKED), final["outcome"]
    assert any("may have acted" in r for r in final["outcome"]["reasons"]), final["outcome"]


def test_a_run_records_its_holder_and_fence_and_neither_ever_changes(rig):
    a = rig.missions.take_lease(rig.mid, holder="runner:a", now=T0)
    run = rig.ledger.open(rig.mid, "sum", rig.worker(), lease=a)
    # A close that also rewrites who opened it.
    with pytest.raises(Exception, match="holder and fence"), rig.vstore.conn:
        rig.vstore.conn.execute("UPDATE task_runs SET state='abandoned', holder='runner:z' WHERE run_id=?",
                                (run.run_id,))
    assert dataclasses.astuple(a)[:3] == (rig.mid, "runner:a", rig.missions.get(rig.mid)["lease_fence"])
