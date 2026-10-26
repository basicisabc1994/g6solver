# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A check whose own answer was sent back to the worker cannot certify the worker's retry (task #182, Sec3 review I6;
lead rulings S-1 to S-3, S-4b, S-6, R-a to R-d).

Pinned: the worker still gets the check's answer (S-1). When that answer carried a value the check then judges in the
retry (numbers as numbers, strings case- and whitespace-folded; booleans and None, "", 0, 1 ignored), or carried a
counterexample, probe or expected output, the same check's pass on the retry earns at most A3, with the S-2 reason, so
the mission closes qualified-draft; read from the recorded message, so a restart keeps it (S-2). Another check that
disclosed nothing keeps A4 (S-3). An answer that only says "wrong" does not cap (S-4b; the V4b tests in
test_workforce_supervisor.py are the survival control, unchanged). The auditor applies the same rule (R-a). Offline,
temp dbs, fakes.
"""
from __future__ import annotations

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
def SCRIPTED(component, op):  # a scripted oracle stands in for a verifier-grade one
    return "A4"

CONTRACTS = {
    ("fake_solver", "check"): wcap.ParamContract.from_dict({
        "component": "fake_solver", "op": "check", "required": {"total": "number", "expected": "number"},
        "probe_params": {"total": 1, "expected": 1}, "expect_field": "satisfiable", "side_effect": "read",
        "check": "input_model", "model_free": True}),
}
#: Lead ruling S-2, verbatim.
REASON = ("This check passed only after its own answer was sent to the worker, so the pass does not count as "
          "independent evidence.")


class FakeRegistry:
    def list_all(self):
        return ["fake_solver"]

    def get_class(self, name):
        return type(name, (), {})

    def describe(self, name):
        return {"backend": {"installed": None}}


def _check(key: str, field: str = "total", expected: int = 6) -> dict:
    return {"key": key, "kind": "verify", "verifies": "report", "goal": f"report's {field} is {expected}",
            "capabilities": [["fake_solver", "check"]],
            "oracle": {"capability": ["fake_solver", "check"], "params": {"expected": expected},
                       "bind": {"total": f"report.{field}"}, "expect": {"field": "satisfiable", "equals": False},
                       "kind": "formal"}}


REPORT = {"key": "report", "kind": "do", "goal": "Report the ledger's total", "output_schema": "total",
          "max_attempts": 2}


@pytest.fixture
def rig(tmp_path):
    said: dict = {"extra": lambda params: {}}  # what the solver adds to a failing answer, set per test

    def world(component: str, op: str, params: dict):
        wrong = params["total"] != params["expected"]
        return {"ok": True, "completion_state": "qualified-draft",
                "result": {"satisfiable": wrong, **(said["extra"](params) if wrong else {})}}

    vstore = store.VentureStore(tmp_path / "venture.db")
    venture = vstore.create_venture("alice", "Alice's", **_CAPS)["venture_id"]

    def build() -> SimpleNamespace:
        """The workforce over the same stores (a fresh process's view, for the restart test)."""
        scoped = store.VentureStore(tmp_path / "venture.db").for_owner("alice")
        missions = wm.MissionStore(scoped)
        caps = wcap.Capabilities(scoped, contracts=CONTRACTS, registry=FakeRegistry(), ops_for=lambda n: None,
                                 authority=SCRIPTED)
        graph = wg.WorkGraph(missions, tmp_path / "workforce" / "units.db")
        ledger = wr.RunLedger(missions)
        runtime = wrt.Runtime(missions, graph, ledger, caps, invoke=world)
        return SimpleNamespace(missions=missions, ledger=ledger, caps=caps, runtime=runtime,
                               wf=ws.Workforce(venture, missions, graph, ledger, caps, runtime, world), graph=graph)

    first = build()
    for component, op in CONTRACTS:
        assert first.caps.probe(component, op, wcall.prober(first.caps, invoke=world).reply,
                                actor="code").level == "probe_passed"

    def mission(units: list[dict]) -> str:
        mid = first.missions.create(venture, objective="Report the ledger's total, checked", risk_tier="R1",
                                    budget_cents=0, actor="chat")
        first.missions.transition(mid, "awaiting_plan", reason="accepted", actor="human")
        first.missions.add_plan(mid, {"units": units, "edges": []}, actor="chat", ready=first.caps.ready)
        first.graph.materialize(mid, actor="code")
        return mid

    return SimpleNamespace(**vars(first), build=build, mission=mission, said=said)


def _sent_back(rig, units: list[dict], content: dict, extra) -> str:
    """The first output is judged, fails a check, and that check's answer goes back to the worker (S-1: kept)."""
    rig.said["extra"] = extra
    mid = rig.mission(units)
    assert rig.runtime.run(mid)["status"] == "waiting"
    rig.runtime.submit(mid, "report", content)
    back = ws.run(rig.wf, mid)
    assert back["status"] == "waiting" and [a["unit"] for a in back["awaiting_chat"]] == ["report"]
    (message,) = back["messages"]
    assert "the oracle answered: " in message["text"]  # the disclosure itself is kept (S-1)
    return mid


def _latest(rig, mid: str) -> dict[str, dict]:
    latest = rig.ledger.last_artifact(mid, "report")["artifact_id"]
    return {v["unit_key"]: v for v in rig.ledger.verdicts(mid, "report") if v["artifact_id"] == latest}


@pytest.mark.parametrize("extra", [
    pytest.param(lambda p: {"model": {"total": p["expected"]}}, id="flat"),
    pytest.param(lambda p: {"witness": {"rows": [{"ledger": {"total": p["expected"]}}]}}, id="nested-leaf"),
    pytest.param(lambda p: {"should_be": float(p["expected"])}, id="six-point-zero"),
])
def test_a_retry_that_copies_the_checks_disclosed_value_is_never_verified_by_that_check(rig, extra):
    mid = _sent_back(rig, [REPORT, _check("check-report")], {"total": 7}, extra)
    ws.submit(rig.wf, mid, "report", {"total": 6})  # exactly the value the check sent back
    done = ws.run(rig.wf, mid)
    assert (done["status"], done["completion_state"]) == ("done", wc.QUALIFIED), done["outcome"]
    assert any(REASON in r for r in done["outcome"]["reasons"]), done["outcome"]
    passed = _latest(rig, mid)["check-report"]
    assert (passed["result"], passed["authority"], passed["state"]) == ("pass", "A3", wc.QUALIFIED)


@pytest.mark.parametrize("key", ["counterexamples", "expected", "probe"])
def test_an_answer_that_carried_a_counterexample_probe_or_expected_output_caps_the_retry(rig, key):
    mid = _sent_back(rig, [REPORT, _check("check-report")], {"total": 7}, lambda p: {key: [{"total": 9}]})
    ws.submit(rig.wf, mid, "report", {"total": 6})  # not the value sent back: (ii) caps on the payload alone
    done = ws.run(rig.wf, mid)
    assert done["completion_state"] == wc.QUALIFIED, done["outcome"]
    assert _latest(rig, mid)["check-report"]["authority"] == "A3"


def test_another_check_that_disclosed_nothing_keeps_its_full_authority(rig):
    """S-3: only the check whose answer went back is capped; check-b passed both outputs and told the worker nothing."""
    units = [REPORT, _check("check-a"), _check("check-b", "count", 2)]
    mid = _sent_back(rig, units, {"total": 7, "count": 2}, lambda p: {"model": {"total": p["expected"]}})
    ws.submit(rig.wf, mid, "report", {"total": 6, "count": 2})
    done = ws.run(rig.wf, mid)
    assert done["completion_state"] == wc.QUALIFIED, done["outcome"]
    found = _latest(rig, mid)
    assert (found["check-a"]["result"], found["check-a"]["authority"]) == ("pass", "A3")
    assert (found["check-b"]["result"], found["check-b"]["authority"], found["check-b"]["state"]) == (
        "pass", "A4", wc.VERIFIED)


def test_an_answer_that_carries_no_value_the_retry_copies_does_not_cap(rig):
    """S-4b control: the answer echoes the worker's own wrong total (7); the retry's 6 is not in it, and no
    counterexample, probe or expected output was sent. The pass counts in full."""
    mid = _sent_back(rig, [REPORT, _check("check-report")], {"total": 7}, lambda p: {"got": p["total"]})
    ws.submit(rig.wf, mid, "report", {"total": 6})
    done = ws.run(rig.wf, mid)
    assert done["completion_state"] == wc.VERIFIED, done["outcome"]
    assert _latest(rig, mid)["check-report"]["authority"] == "A4"


def test_the_cap_is_read_from_the_ledger_and_survives_a_restart(rig):
    mid = _sent_back(rig, [REPORT, _check("check-report")], {"total": 7}, lambda p: {"model": {"total": 6}})
    fresh = rig.build()  # a new process: nothing of the first runtime's memory, the same stores
    ws.submit(fresh.wf, mid, "report", {"total": 6})
    done = ws.run(fresh.wf, mid)
    assert done["completion_state"] == wc.QUALIFIED, done["outcome"]
    assert any(REASON in r for r in done["outcome"]["reasons"]), done["outcome"]
    assert _latest(fresh, mid)["check-report"]["authority"] == "A3"


def test_a_first_try_that_passes_is_verified_as_before(rig):
    rig.said["extra"] = lambda p: {"model": {"total": 6}}
    mid = rig.mission([REPORT, _check("check-report")])
    assert rig.runtime.run(mid)["status"] == "waiting"
    rig.runtime.submit(mid, "report", {"total": 6})
    done = ws.run(rig.wf, mid)
    assert done["completion_state"] == wc.VERIFIED and done["messages"] == [], done["outcome"]
    assert _latest(rig, mid)["check-report"]["authority"] == "A4"


