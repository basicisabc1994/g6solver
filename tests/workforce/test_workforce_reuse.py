# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Reuse of verified results (Phase E Part 1, E2b; Ledger §13 step 2, §29 ECON-002/-003; Doctrine §10).

Pinned: a unit may read an earlier mission's verified result instead of having it made again (ECON-002: reused,
provenance kept, the tool that made it never called again); a result that is stale, unverified, failed, replaced,
made by a tool or checked by a check that changed since, made from inputs that changed since, another account's,
or checked for lower-stakes work is refused at planning with what would resolve it, and fresh work is then planned
and done (ECON-003); a result that lapses after the plan was accepted stops the step that would read it; a pull
step is told why an input it names cannot be read. Offline, scripted components.
"""
from __future__ import annotations

import json
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
from g6.workforce import reuse as wre
from g6.workforce import runs as wr
from g6.workforce import runtime as wrt

_CAPS = {"budget_aud_month": 0, "model_cap_usd_month": 0, "task_cap_usd": 0, "max_turns": 5}
SCRIPTED = lambda component, op: "A4"  # a scripted oracle stands in for a verifier-grade one; the cap itself is pinned in test_workforce_authority.py  # noqa: E731


def _contract(component: str, op: str, required: dict, expect: str, **extra) -> wcap.ParamContract:
    probe = {key: [1] if kind == "list" else 1 for key, kind in required.items()}
    return wcap.ParamContract.from_dict({"component": component, "op": op, "required": required,
                                         "probe_params": probe, "expect_field": expect, "side_effect": "read",
                                         "check": "input_model", **extra})


CONTRACTS = {
    ("fake_ledger", "total"): _contract("fake_ledger", "total", {"numbers": "list"}, "total"),
    ("fake_report", "render"): _contract("fake_report", "render", {"total": "number"}, "total"),
    ("fake_solver", "check"): _contract("fake_solver", "check", {"total": "number", "expected": "number"},
                                        "satisfiable", model_free=True),
}


def _ok(answer: dict) -> dict:
    return {"ok": True, "completion_state": "qualified-draft", "result": answer}


class World:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, component: str, op: str, params: dict):
        self.calls.append((component, op))
        if component == "fake_ledger":
            return _ok({"total": sum(params["numbers"])})
        if component == "fake_report":
            return _ok({"total": params["total"], "line": f"The ledger totals {params['total']}"})
        return _ok({"satisfiable": params["total"] != params["expected"]})


class FakeRegistry:
    def list_all(self):
        return ["fake_ledger", "fake_report", "fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def _later(hours: int = 25):
    return lambda: datetime.now(UTC) + timedelta(hours=hours)


def _rig(tmp_path, *, owner: str = "alice", contracts=CONTRACTS):
    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture(owner, f"{owner}'s", **_CAPS)["venture_id"]
    missions = wm.MissionStore(vstore.for_owner(owner))
    world = World()
    caps = wcap.Capabilities(vstore.for_owner(owner), contracts=contracts, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    for component, op in contracts:
        assert caps.probe(component, op, wcall.prober(caps, invoke=world).reply, actor="code").level == "probe_passed"
    world.calls.clear()
    graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
    ledger = wr.RunLedger(missions)
    rig = SimpleNamespace(vstore=vstore, venture=venture, missions=missions, world=world, caps=caps, graph=graph,
                          ledger=ledger)
    rig.runtime = rig_runtime(rig)
    return rig


def rig_runtime(rig, *, caps=None, now=None) -> wrt.Runtime:
    return wrt.Runtime(rig.missions, rig.graph, rig.ledger, caps or rig.caps, invoke=rig.world, now=now)


def _plan_in(rig, plan: dict, *, risk: str = "R1", caps=None, runtime=None) -> str:
    caps, runtime = caps or rig.caps, runtime or rig.runtime
    mid = rig.missions.create(rig.venture, objective="Report the ledger total", risk_tier=risk, budget_cents=0,
                              actor="chat")
    rig.missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
    rig.missions.add_plan(mid, plan, actor="chat", ready=caps.ready, reusable=runtime.reusable)
    rig.graph.materialize(mid, actor="code")
    return mid


SUM = {"key": "sum", "kind": "do", "goal": "Total the ledger", "capabilities": [["fake_ledger", "total"]],
       "call": {"capability": ["fake_ledger", "total"], "params": {"numbers": [1, 2, 3]}}}


def _check(key: str, verifies: str) -> dict:
    return {"key": key, "kind": "verify", "verifies": verifies, "goal": f"{verifies} totals 6",
            "capabilities": [["fake_solver", "check"]],
            "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": 6},
                       "bind": {"total": f"{verifies}.total"}, "expect": {"field": "satisfiable", "equals": False},
                       "kind": "formal"}}


def _report(source: str) -> list[dict]:
    """A report built from ``source`` (a unit key, or a saved result's ``artifact:<id>``), checked independently."""
    return [{"key": "report", "kind": "do", "goal": "Report the total", "inputs": [source],
             "capabilities": [["fake_report", "render"]],
             "call": {"capability": ["fake_report", "render"], "bind": {"total": f"{source}.total"}}},
            _check("check-report", "report")]


def _saved(rig) -> tuple[str, str]:
    """An earlier mission totals the ledger, verified: (its saved total's id, that mission's id)."""
    mid = _plan_in(rig, {"units": [SUM, _check("check-sum", "sum")], "edges": []})
    assert rig.runtime.run(mid)["completion_state"] == wc.VERIFIED
    rig.world.calls.clear()
    return rig.ledger.last_artifact(mid, "sum")["artifact_id"], mid


def _refused(rig, plan: dict, **kw) -> str:
    with pytest.raises(wp.PlanRejected) as refused:
        _plan_in(rig, plan, **kw)
    return str(refused.value)


def test_econ_002_a_valid_verified_result_is_reused_with_its_provenance_and_no_redundant_work(tmp_path):
    rig = _rig(tmp_path)
    saved, made_in = _saved(rig)
    mid = _plan_in(rig, {"units": _report(f"artifact:{saved}"), "edges": []})
    final = rig.runtime.run(mid)
    assert final["completion_state"] == wc.VERIFIED, final["outcome"]
    assert rig.world.calls == [("fake_report", "render"), ("fake_solver", "check")]  # the total was not made again
    report = rig.ledger.last_artifact(mid, "report")
    used = report["provenance"]["inputs"][f"artifact:{saved}"]
    assert (used["artifact_id"], used["sha256"]) == (saved, rig.ledger.artifact(made_in, saved)["sha256"])
    assert used["reused"]["mission_id"] == made_in and used["reused"]["verdicts"]
    assert report["provenance"]["call"]["component"] == "fake_report"
    closed = [e["detail"] for e in rig.missions.events(mid, kind="run.closed") if e["detail"]["unit"] == "report"]
    assert closed[-1]["context"]["context_reuse"] == 1


def test_econ_003_a_stale_result_is_refused_and_fresh_work_is_done(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    later = rig_runtime(rig, now=_later())
    why = _refused(rig, {"units": _report(f"artifact:{saved}"), "edges": []}, runtime=later)
    assert f"The saved result artifact:{saved} cannot be reused: it is older than its validity period of 24 hours" in why
    assert "To resolve: plan this step again so G6 redoes the work" in why
    assert "(code G6_E_DEPENDENCY_UNAVAILABLE)" in why
    mid = _plan_in(rig, {"units": [SUM, _check("check-sum", "sum"), *_report("sum")], "edges": []}, runtime=later)
    assert later.run(mid)["completion_state"] == wc.VERIFIED
    assert ("fake_ledger", "total") in rig.world.calls  # fresh work performed


def test_econ_003_a_result_made_by_a_tool_that_changed_is_refused(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    changed = {**CONTRACTS, ("fake_ledger", "total"): _contract("fake_ledger", "total", {"numbers": "list"}, "total",
                                                               optional={"currency": "str"})}
    caps = wcap.Capabilities(rig.vstore.for_owner("alice"), contracts=changed, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    why = _refused(rig, {"units": _report(f"artifact:{saved}"), "edges": []}, caps=caps,
                   runtime=rig_runtime(rig, caps=caps))
    assert "the tool that made it (fake_ledger.total) has changed since" in why


def test_econ_003_a_result_checked_by_a_check_that_changed_is_refused(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    changed = {**CONTRACTS, ("fake_solver", "check"): _contract(
        "fake_solver", "check", {"total": "number", "expected": "number"}, "satisfiable", model_free=True,
        optional={"tolerance": "number"})}
    caps = wcap.Capabilities(rig.vstore.for_owner("alice"), contracts=changed, registry=FakeRegistry(),
                             ops_for=lambda n: None, authority=SCRIPTED)
    assert caps.probe("fake_solver", "check", wcall.prober(caps, invoke=rig.world).reply,
                      actor="code").level == "probe_passed"
    why = _refused(rig, {"units": _report(f"artifact:{saved}"), "edges": []}, caps=caps,
                   runtime=rig_runtime(rig, caps=caps))
    assert "the check that verified it has changed since" in why


def test_another_accounts_result_reads_exactly_like_one_that_does_not_exist(tmp_path):
    """Review Focus 2."""
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    bob = _rig(tmp_path, owner="bob")
    foreign, unknown = bob.runtime.reusable(saved, "R1"), bob.runtime.reusable("nosuchresult", "R1")
    assert "there is no such result for this account" in foreign
    assert foreign == unknown.replace("nosuchresult", saved)


def test_another_account_with_the_same_contracts_and_inputs_never_reuses_the_result(tmp_path):
    """Doctrine §10: a result verified for one account is never reused for another. Bob's mission names Alice's
    verified total with the same contracts and the same inputs: it is refused at planning as an unknown result; a plan
    stored without that check is still never fed it (the step stops before any call, with nothing of Alice's in its
    record); Bob's own identical work is done afresh, never served from Alice's; and Alice may still reuse hers."""
    alice = _rig(tmp_path)
    saved, made_in = _saved(alice)
    bob = _rig(tmp_path, owner="bob")
    plan = {"units": _report(f"artifact:{saved}"), "edges": []}
    why = _refused(bob, plan)
    assert f"The saved result artifact:{saved} cannot be reused: there is no such result for this account" in why
    mid = bob.missions.create(bob.venture, objective="Report the ledger total", risk_tier="R1", budget_cents=0,
                              actor="chat")
    bob.missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
    bob.missions.add_plan(mid, plan, actor="chat", ready=bob.caps.ready)  # no planning check: the run-time one holds
    bob.graph.materialize(mid, actor="code")
    final = bob.runtime.run(mid)
    assert final["completion_state"] == wc.BLOCKED and bob.world.calls == []
    run, = bob.ledger.runs(mid, "report")
    assert (run["failure_code"], run["artifact_id"]) == ("stale_reuse", "")
    assert "there is no such result for this account" in " ".join(final["outcome"]["reasons"])
    assert made_in not in json.dumps([e["detail"] for e in bob.missions.events(mid)])  # nothing of Alice's leaked
    fresh = _plan_in(bob, {"units": [SUM, _check("check-sum", "sum"), *_report("sum")], "edges": []})
    assert bob.runtime.run(fresh)["completion_state"] == wc.VERIFIED
    assert ("fake_ledger", "total") in bob.world.calls  # Bob's identical work was done again, for Bob
    assert bob.ledger.last_artifact(fresh, "report")["provenance"]["inputs"]["sum"]["artifact_id"] != saved
    assert alice.runtime.reusable(saved, "R1") is None  # the owner may still reuse it


def test_a_result_checked_for_lower_stakes_work_is_not_reused_by_a_more_consequential_mission(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)  # an R1 mission's
    why = _refused(rig, {"units": _report(f"artifact:{saved}"), "edges": []}, risk="R2")
    assert "it was checked for lower-stakes work (R1) than this mission (R2)" in why


def test_a_result_that_lapses_after_the_plan_was_accepted_stops_the_step_that_reads_it(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    mid = _plan_in(rig, {"units": _report(f"artifact:{saved}"), "edges": []})  # valid when planned
    final = rig_runtime(rig, now=_later()).run(mid)
    assert final["completion_state"] == wc.BLOCKED and rig.world.calls == []
    run, = rig.ledger.runs(mid, "report")
    assert run["failure_code"] == "stale_reuse"
    reasons = " ".join(final["outcome"]["reasons"])
    assert "cannot be reused: it is older than its validity period" in reasons
    assert "(code G6_E_DEPENDENCY_UNAVAILABLE)" in reasons


def test_a_pull_step_whose_saved_input_lapsed_is_told_why_and_its_submission_is_refused(tmp_path):
    rig = _rig(tmp_path)
    saved, _ = _saved(rig)
    ref = f"artifact:{saved}"
    mid = _plan_in(rig, {"units": [{"key": "summary", "kind": "do", "goal": "Write the total", "inputs": [ref]},
                                   _check("check-summary", "summary")], "edges": []})
    spec = rig.missions.plan(mid)[1].unit("summary")
    later = rig_runtime(rig, now=_later())
    shown, problem = later.brief_inputs(mid, spec)
    assert shown == {ref: None} and "cannot be reused" in problem and "(code G6_E_DEPENDENCY_UNAVAILABLE)" in problem
    with pytest.raises(ValueError, match="cannot be reused"):
        later.submit(mid, "summary", {"total": 6})
    assert rig.ledger.runs(mid, "summary") == []  # a refusal spends nothing
    assert rig.runtime.brief_inputs(mid, spec) == ({ref: {"total": 6}}, "")


# --- each rule on rows written by hand ----------------------------------------------------------------------------

TOOL = CONTRACTS[("fake_ledger", "total")]
CHECK = CONTRACTS[("fake_solver", "check")]
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
SHA = "d" * 64


class StubLedger:
    """The rows ``reuse.check`` reads, written by hand: one mission 'm1' at risk R1."""

    def __init__(self) -> None:
        self.artifacts: dict[str, dict] = {}
        self.latest: dict[str, str] = {}
        self.verdict_rows: list[dict] = []
        self.run_rows: dict[str, dict] = {}

    def find_artifact(self, artifact_id):
        if artifact_id not in self.artifacts:
            raise LookupError("no such venture for this owner")
        return "m1", self.artifacts[artifact_id]

    def last_artifact(self, mission_id, unit):
        found = self.latest.get(unit)
        return None if found is None else self.artifacts[found]

    def verdicts(self, mission_id, verifies_key=None):
        return [v for v in self.verdict_rows if v["verifies_key"] == verifies_key]

    def run(self, mission_id, run_id):
        return self.run_rows[run_id]

    def mission(self, mission_id):
        return {"mission_id": "m1", "risk_tier": "R1"}


def _by_hand(ledger: StubLedger, art_id: str = "a1", unit: str = "sum", *, inputs=None, hours_old: int = 1,
             provenance: bool = True, verdict: str = "pass") -> dict:
    art = {"artifact_id": art_id, "unit": unit, "sha256": SHA, "content": {"total": 6},
           "at": (NOW - timedelta(hours=hours_old)).isoformat()}
    if provenance:
        art["provenance"] = {"call": {"component": "fake_ledger", "op": "total", "contract_sha": TOOL.sha256},
                             "inputs": inputs or {}}
    ledger.artifacts[art_id], ledger.latest[unit] = art, art_id
    ledger.run_rows[f"vr-{art_id}"] = {"calls_json": json.dumps(
        [{"component": "fake_solver", "op": "check", "refused": False, "contract_sha": CHECK.sha256}])}
    ledger.verdict_rows.append({"verdict_id": f"vd-{art_id}", "run_id": f"vr-{art_id}", "verifies_key": unit,
                                "artifact_id": art_id, "artifact_sha": SHA, "result": verdict, "executed": 1,
                                "state": wc.VERIFIED if verdict == "pass" else wc.BLOCKED,
                                "authority": SCRIPTED("fake_solver", "check")})
    return art


def _check_by_hand(ledger: StubLedger, art_id: str = "a1", contracts=None):
    contracts = contracts or {("fake_ledger", "total"): TOOL, ("fake_solver", "check"): CHECK}
    return wre.check(ledger, art_id, contract=lambda c, o: contracts.get((c, o)), now=NOW, risk_tier="R1")


def test_a_valid_result_is_reused_until_its_validity_period_ends():
    ledger = StubLedger()
    _by_hand(ledger)
    found = _check_by_hand(ledger)
    assert found.verdict_ids == ("vd-a1",) and found.valid_until == (NOW + timedelta(hours=23)).isoformat()


def test_a_replaced_result_is_not_reused():
    ledger = StubLedger()
    _by_hand(ledger, "a1")
    _by_hand(ledger, "a2")
    with pytest.raises(wre.ReuseRefused, match="a newer result has replaced it"):
        _check_by_hand(ledger, "a1")


def test_a_result_whose_inputs_changed_since_is_not_reused():
    ledger = StubLedger()
    _by_hand(ledger, "p1", unit="parse")
    _by_hand(ledger, "a1", inputs={"parse": {"artifact_id": "p1", "sha256": SHA}})
    _by_hand(ledger, "p2", unit="parse")  # parse produced again after the total was made
    with pytest.raises(wre.ReuseRefused, match=r"the result it was made from \(parse\) has changed since"):
        _check_by_hand(ledger, "a1")


def test_a_result_saved_before_provenance_was_recorded_is_not_reused():
    ledger = StubLedger()
    _by_hand(ledger, provenance=False)
    with pytest.raises(wre.ReuseRefused, match="saved before G6 recorded"):
        _check_by_hand(ledger)


def test_a_result_an_independent_check_failed_is_not_reused():
    ledger = StubLedger()
    _by_hand(ledger)
    ledger.verdict_rows.append({**ledger.verdict_rows[0], "verdict_id": "vd-x", "result": "fail",
                                "state": wc.BLOCKED})
    with pytest.raises(wre.ReuseRefused, match="an independent check failed it"):
        _check_by_hand(ledger)


def test_a_result_no_independent_check_verified_is_not_reused():
    ledger = StubLedger()
    _by_hand(ledger)
    ledger.verdict_rows[0]["state"] = wc.QUALIFIED
    with pytest.raises(wre.ReuseRefused, match="no independent check verified it"):
        _check_by_hand(ledger)


def test_a_chain_of_saved_results_is_followed_only_so_deep():
    ledger = StubLedger()
    _by_hand(ledger, "a5", unit="u5")
    for depth in (4, 3, 2, 1):
        _by_hand(ledger, f"a{depth}", unit=f"u{depth}",
                 inputs={f"artifact:a{depth + 1}": {"artifact_id": f"a{depth + 1}", "sha256": SHA}})
    with pytest.raises(wre.ReuseRefused, match="too deep to check"):
        _check_by_hand(ledger, "a1")


def test_a_shorter_declared_validity_wins():
    ledger = StubLedger()
    _by_hand(ledger, hours_old=1)
    short = _contract("fake_ledger", "total", {"numbers": "list"}, "total", valid_for_seconds=1800)
    ledger.artifacts["a1"]["provenance"]["call"]["contract_sha"] = short.sha256
    with pytest.raises(wre.ReuseRefused, match="validity period of 1800 seconds"):
        _check_by_hand(ledger, contracts={("fake_ledger", "total"): short, ("fake_solver", "check"): CHECK})
