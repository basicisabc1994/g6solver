# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Missions wait for events at no model cost (workforce OS plan, V2b).

Pinned: a plan unit may say when it may start (``not_before``) and on what file (``await_file``, a name in
the mission's inbox). When nothing can run but units held by time, by a file or by the owner's chat, the
runner moves the mission to ``waiting`` and arms one wake per held unit, in one transaction. Waiting costs
nothing: no call, no event, however often a runner looks. A due timer or a dropped file wakes it, the
chat's submission wakes it, and whatever fires is ONE ``wake`` event and one move back to ``active``,
however many wakes fire at once or however many runners race to fire them. A crash mid-wake changes
nothing. A file wake records the file's digest; a timer fires by the runner's clock and the event says
so. Offline, temp dbs, fake components.
"""
from __future__ import annotations

import hashlib
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

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731
T0 = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
LATER = T0 + timedelta(hours=1)
CONTRACTS = {
    ("fake_ledger", "total"): wcap.ParamContract.from_dict({
        "component": "fake_ledger", "op": "total", "required": {"numbers": "list"},
        "probe_params": {"numbers": [1]}, "expect_field": "total", "side_effect": "read", "check": "input_model"}),
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
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op))
        if component == "fake_ledger":
            return {"ok": True, "completion_state": "qualified-draft", "result": {"total": sum(params["numbers"])}}
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": params["total"] != params["expected"]}}


def _pair(key: str = "sum", *, pulled: bool = False, **start) -> list[dict]:
    """A do unit (code-run, or the chat's to write) and the verify unit that checks its total is 6."""
    do = {"key": key, "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]], **start}
    if not pulled:
        do["call"] = {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}
    check = {"key": f"check-{key}", "kind": "verify", "verifies": key, "goal": "The total is 6",
             "capabilities": [["fake_solver", "check"]],
             "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6},
                        "bind": {"total": f"{key}.total"}, "expect": {"field": "satisfiable", "equals": False},
                        "kind": "formal"}}
    return [do, check]


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
    inbox = tmp_path / "inbox"

    def mission(units: list[dict]) -> str:
        mid = missions.create(venture, objective="Total the ledger, checked", risk_tier="R1", budget_cents=0,
                              actor="chat")
        missions.transition(mid, "awaiting_plan", reason="accepted", actor="chat")
        missions.add_plan(mid, {"units": units, "edges": []}, actor="chat", ready=caps.ready)
        graph.materialize(mid, actor="code")
        return mid

    def runtime(at: datetime, holder: str = "runner:a"):
        return wrt.Runtime(missions, graph, ledger, caps, invoke=world, holder=holder, now=lambda: at, inbox=inbox)

    def drop(mid: str, name: str, body: bytes) -> str:
        folder = inbox / venture / mid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(body)
        return hashlib.sha256(body).hexdigest()

    return SimpleNamespace(missions=missions, graph=graph, ledger=ledger, world=world, mission=mission,
                           runtime=runtime, drop=drop, venture=venture, inbox=inbox)


def _kinds(rig, mid: str) -> list[str]:
    return [e["kind"] for e in rig.missions.events(mid)]


# --- the plan says when and on what a unit may start --------------------------------------------------


def test_a_plan_states_when_and_on_what_file_a_unit_may_start():
    units = [*_pair("a", not_before="2026-10-02T19:00:00+10:00"), *_pair("b", await_file="export.csv")]
    plan = wp.parse_plan({"units": units, "edges": []}, risk_tier="R1", budget_cents=0)
    assert plan.unit("a").not_before == "2026-10-02T09:00:00+00:00"  # one UTC form, whatever zone was written
    assert plan.unit("b").await_file == "export.csv"
    plain = wp.parse_plan({"units": _pair("c"), "edges": []}, risk_tier="R1", budget_cents=0)
    assert "not_before" not in plain.unit("c").as_dict() and "await_file" not in plain.unit("c").as_dict()
    bad = [*_pair("d", not_before="2026-10-02T09:00:00"), *_pair("e", await_file="../secrets"),
           *_pair("f", await_file=".hidden"), *_pair("g", not_before="soon"),
           {**_pair("h")[1], "key": "check-h2", "verifies": "h", "await_file": "x.csv"}, _pair("h")[0]]
    with pytest.raises(wp.PlanRejected) as refused:
        wp.parse_plan({"units": bad, "edges": []}, risk_tier="R1", budget_cents=0)
    problems = " | ".join(refused.value.problems)
    assert "'d'" in problems and "time zone" in problems
    assert "'e'" in problems and "'f'" in problems and "'g'" in problems
    assert "check-h2" not in problems  # a verify unit may wait on a file too


# --- the acceptance test: a scheduled wake fires a waiting mission --------------------------------------


def test_a_scheduled_wake_fires_a_waiting_mission_at_no_model_cost(rig):
    mid = rig.mission(_pair(not_before=LATER.isoformat()))
    waiting = rig.runtime(T0).run(mid)
    assert waiting["status"] == "waiting" and rig.world.calls == [] and rig.ledger.runs(mid) == []
    armed, = rig.runtime(T0).wakes.armed(mid)
    assert (armed["kind"], armed["unit_key"], armed["at"]) == ("timer", "sum", LATER.isoformat())
    seen = len(rig.missions.events(mid))
    for _ in range(25):  # looking costs nothing: no call, no event
        assert rig.runtime(T0 + timedelta(minutes=30)).run(mid)["status"] == "waiting"
    assert len(rig.missions.events(mid)) == seen and rig.world.calls == []
    final = rig.runtime(LATER).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    wake, = [e["detail"] for e in rig.missions.events(mid, kind="wake")]
    assert [(w["kind"], w["unit"]) for w in wake["fired"]] == [("timer", "sum")]
    assert wake["clock"] == LATER.isoformat()  # fired by the runner's clock, and the event says which reading


def test_a_clock_that_runs_behind_never_fires_a_timer_early(rig):
    mid = rig.mission(_pair(not_before=LATER.isoformat()))
    rig.runtime(T0).run(mid)
    assert rig.runtime(LATER - timedelta(seconds=1)).run(mid)["status"] == "waiting"
    assert "wake" not in _kinds(rig, mid)


# --- a file drop --------------------------------------------------------------------------------------


def test_a_file_drop_wakes_the_mission_and_the_wake_records_its_digest(rig):
    mid = rig.mission(_pair(await_file="export.csv"))
    assert rig.runtime(T0).run(mid)["status"] == "waiting"
    armed, = rig.runtime(T0).wakes.armed(mid)
    assert (armed["kind"], armed["name"]) == ("file", "export.csv")
    assert armed["path"] == str(rig.inbox / rig.venture / mid / "export.csv")
    rig.drop(mid, "other.csv", b"not the awaited file")
    assert rig.runtime(T0).run(mid)["status"] == "waiting"
    digest = rig.drop(mid, "export.csv", b"invoice,amount\nA-1,100\n")
    final = rig.runtime(T0).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)
    wake, = [e["detail"] for e in rig.missions.events(mid, kind="wake")]
    fired, = wake["fired"]
    assert (fired["kind"], fired["name"], fired["sha256"], fired["bytes"]) == ("file", "export.csv", digest, 23)


def test_a_folder_named_like_the_awaited_file_is_not_a_drop(rig):
    mid = rig.mission(_pair(await_file="export.csv"))
    (rig.inbox / rig.venture / mid / "export.csv").mkdir(parents=True)  # there before the mission first runs
    assert rig.runtime(T0).run(mid)["status"] == "waiting" and rig.world.calls == []
    assert rig.runtime(T0).run(mid)["status"] == "waiting"


def test_a_mission_never_waits_beside_an_attempt_in_flight(rig):
    """The chat's submission is in flight (its attempt is open) while another unit waits for its time: the
    mission stays active, so the submission is judged as soon as it lands, not when the timer fires."""
    now = datetime.now(UTC)  # the ledger stamps the attempt with the real clock: stay inside its wall clock
    mid = rig.mission([*_pair("pulled", pulled=True),
                       *_pair("later", not_before=(now + timedelta(hours=1)).isoformat())])
    plan = rig.missions.plan(mid)[1]
    worker = wrt.form_worker("puller", ceiling=wrt.ceiling_for(rig.missions.get(mid)),
                             contracts=lambda c, o: CONTRACTS.get((c, o)), unit=plan.unit("pulled"), allowed=())
    rig.ledger.open(mid, "pulled", worker, actor="chat")
    assert rig.runtime(now).run(mid)["status"] == "active"
    assert rig.runtime(now).wakes.all(mid) == []


# --- the chat's submission ------------------------------------------------------------------------------


def test_the_chats_submission_wakes_a_mission_waiting_for_it(rig):
    mid = rig.mission(_pair(pulled=True))
    assert rig.runtime(T0).run(mid)["status"] == "waiting"
    armed, = rig.runtime(T0).wakes.armed(mid)
    assert (armed["kind"], armed["unit_key"]) == ("submission", "sum")
    assert [u.key for u in rig.runtime(T0).awaiting(mid)] == ["sum"]  # still listed while waiting
    rig.runtime(T0).submit(mid, "sum", {"total": 6})
    assert rig.missions.get(mid)["status"] == "active"
    wake, = [e["detail"] for e in rig.missions.events(mid, kind="wake")]
    assert [(w["kind"], w["unit"]) for w in wake["fired"]] == [("submission", "sum")]
    final = rig.runtime(T0).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_submission_landing_while_the_runner_decides_to_wait_never_strands_the_mission(rig, monkeypatch):
    """Codex V2: the runner sees the chat's unit outstanding, the chat then submits it in full, and only then
    would the runner arm a wake for it: it must not wait for a unit already delivered."""
    mid = rig.mission(_pair(pulled=True))
    service, chat = rig.runtime(T0, holder="runner:service"), rig.runtime(T0, holder="runner:chat")
    real_wait = service.wakes.wait

    def chat_submits_first(*args, **kwargs):
        chat.submit(mid, "sum", {"total": 6})
        return real_wait(*args, **kwargs)

    monkeypatch.setattr(service.wakes, "wait", chat_submits_first)
    service.step(mid)
    monkeypatch.undo()
    assert rig.missions.get(mid)["status"] == "active" and service.wakes.armed(mid) == []
    final = rig.runtime(T0).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_submission_that_finds_the_mission_gone_waiting_under_it_wakes_it_and_lands(rig, monkeypatch):
    """Codex V2: the chat checks it is awaited while the mission is active, the runner then moves it to
    waiting for that very submission, and the submission must wake it and land, not fail."""
    mid = rig.mission(_pair(pulled=True))
    service, chat = rig.runtime(T0, holder="runner:service"), rig.runtime(T0, holder="runner:chat")
    real_awaiting = chat.awaiting

    def then_the_service_waits(mission_id):
        found = real_awaiting(mission_id)
        service.run(mid)
        return found

    monkeypatch.setattr(chat, "awaiting", then_the_service_waits)
    chat.submit(mid, "sum", {"total": 6})
    monkeypatch.undo()
    assert rig.missions.get(mid)["status"] == "active"
    final = rig.runtime(T0).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_submission_raced_again_between_its_wake_and_its_attempt_still_lands(rig, monkeypatch):
    """The chat wakes the waiting mission, and before its attempt opens the runner looks again and moves it
    back to waiting for that same submission: the chat wakes it once more and lands (Codex V2)."""
    mid = rig.mission(_pair(pulled=True))
    service, chat = rig.runtime(T0, holder="runner:service"), rig.runtime(T0, holder="runner:chat")
    assert service.run(mid)["status"] == "waiting"
    real_fire, raced = chat.wakes.fire_submission, []

    def fire_then_the_service_waits_again(*args, **kwargs):
        woke = real_fire(*args, **kwargs)
        if not raced:
            raced.append(service.run(mid)["status"])
        return woke

    monkeypatch.setattr(chat.wakes, "fire_submission", fire_then_the_service_waits_again)
    chat.submit(mid, "sum", {"total": 6})
    monkeypatch.undo()
    assert raced == ["waiting"] and rig.missions.get(mid)["status"] == "active"
    final = rig.runtime(T0).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_submission_a_waiting_mission_is_not_waiting_for_is_refused_and_changes_nothing(rig):
    mid = rig.mission([*_pair("sum", not_before=LATER.isoformat()), *_pair("pulled", pulled=True)])
    rig.runtime(T0).run(mid)
    before = (rig.missions.get(mid)["status"], len(rig.missions.events(mid)), len(rig.runtime(T0).wakes.armed(mid)))
    with pytest.raises(ValueError, match="not waiting for the chat"):
        rig.runtime(T0).submit(mid, "sum", {"total": 6})  # code's unit, held by its time
    after = (rig.missions.get(mid)["status"], len(rig.missions.events(mid)), len(rig.runtime(T0).wakes.armed(mid)))
    assert before == after == ("waiting", before[1], 2)


# --- storms, races and crashes --------------------------------------------------------------------------


def test_a_wake_storm_wakes_the_mission_once(rig):
    units = [u for n in range(12) for u in _pair(f"s{n}", not_before=LATER.isoformat())]
    mid = rig.mission(units)
    rig.runtime(T0).run(mid)
    assert len(rig.runtime(T0).wakes.armed(mid)) == 12
    late = rig.runtime(LATER)
    due = late.wakes.due(mid, LATER)
    assert len(due) == 12
    assert late.wakes.fire(mid, due, now=LATER) is True
    assert late.wakes.fire(mid, due, now=LATER) is False  # a second runner racing to fire: nothing more
    assert _kinds(rig, mid).count("wake") == 1
    moves = [e["detail"] for e in rig.missions.events(mid, kind="mission.status")]
    assert [(m["from"], m["to"]) for m in moves][-2:] == [("active", "waiting"), ("waiting", "active")]
    final = late.run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_crash_mid_wake_changes_nothing_and_the_next_runner_wakes_it(rig, monkeypatch):
    mid = rig.mission(_pair(not_before=LATER.isoformat()))
    rig.runtime(T0).run(mid)
    real = rig.missions.append_event

    def crash(mission_id, kind, *args, **kwargs):
        if kind == "wake":
            raise SystemExit("the runner died while firing the wake")
        return real(mission_id, kind, *args, **kwargs)

    monkeypatch.setattr(rig.missions, "append_event", crash)
    monkeypatch.setattr(rig.missions, "release_lease", lambda lease: None)
    with pytest.raises(SystemExit):
        rig.runtime(LATER).run(mid)
    monkeypatch.undo()
    assert rig.missions.get(mid)["status"] == "waiting" and len(rig.runtime(T0).wakes.armed(mid)) == 1
    assert "wake" not in _kinds(rig, mid)
    final = rig.runtime(LATER + timedelta(minutes=5), holder="runner:b").run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_runner_that_lost_its_lease_cannot_fire_a_wake(rig):
    """Codex V2: A reads what is due, loses its lease to B, then tries to fire: nothing lands."""
    mid = rig.mission(_pair(not_before=LATER.isoformat()))
    rig.runtime(T0).run(mid)
    a = rig.missions.take_lease(mid, holder="runner:a", now=LATER)
    due = rig.runtime(LATER).wakes.due(mid, LATER)
    rig.missions.take_lease(mid, holder="runner:b", now=LATER + timedelta(hours=1))  # a's lease ran out
    with pytest.raises(wm.LeaseLost):
        rig.runtime(LATER).wakes.fire(mid, due, now=LATER, lease=a)
    assert rig.missions.get(mid)["status"] == "waiting" and len(rig.runtime(LATER).wakes.armed(mid)) == 1
    assert "wake" not in _kinds(rig, mid)


def test_a_wake_from_an_ended_episode_cannot_wake_a_later_one(rig):
    """Codex V2: a delayed fire of episode 1's wakes must not end episode 2 (its wakes stay armed)."""
    mid = rig.mission([*_pair("early", not_before=LATER.isoformat()),
                       *_pair("late", not_before=(LATER + timedelta(hours=1)).isoformat())])
    rig.runtime(T0).run(mid)
    stale = rig.runtime(T0).wakes.armed(mid)  # episode 1
    assert rig.runtime(LATER).run(mid)["status"] == "waiting"  # episode 1 fired; early done; episode 2 armed
    later = LATER + timedelta(minutes=30)
    assert rig.runtime(later).wakes.fire(mid, stale, now=later) is False
    assert rig.missions.get(mid)["status"] == "waiting"
    assert [w["unit_key"] for w in rig.runtime(later).wakes.armed(mid)] == ["late"]
    assert _kinds(rig, mid).count("wake") == 1


def test_a_wake_ends_the_whole_episode_and_the_next_wait_arms_afresh(rig):
    """Two units held by different times: the first fires and the other wake is dropped with it; once the
    first is done the mission waits again, arming only what still holds it."""
    mid = rig.mission([*_pair("early", not_before=LATER.isoformat()),
                       *_pair("late", not_before=(LATER + timedelta(hours=1)).isoformat())])
    rig.runtime(T0).run(mid)
    assert {w["unit_key"] for w in rig.runtime(T0).wakes.armed(mid)} == {"early", "late"}
    assert rig.runtime(LATER).run(mid)["status"] == "waiting"
    assert [w["unit_key"] for w in rig.runtime(LATER).wakes.armed(mid)] == ["late"]
    states = sorted((w["unit_key"], w["state"]) for w in rig.runtime(LATER).wakes.all(mid))
    assert states == [("early", "fired"), ("late", "armed"), ("late", "dropped")]
    final = rig.runtime(LATER + timedelta(hours=1)).run(mid)
    assert (final["status"], final["completion_state"]) == ("done", wc.VERIFIED)


def test_a_wake_row_ends_once_and_is_kept(rig):
    mid = rig.mission(_pair(not_before=LATER.isoformat()))
    rig.runtime(T0).run(mid)
    rig.runtime(LATER).run(mid)
    conn = rig.missions.conn
    with pytest.raises(Exception, match="wake"), conn:
        conn.execute("UPDATE mission_wakes SET state='armed' WHERE mission_id=?", (mid,))
    with pytest.raises(Exception, match="wake"), conn:
        conn.execute("DELETE FROM mission_wakes WHERE mission_id=?", (mid,))
