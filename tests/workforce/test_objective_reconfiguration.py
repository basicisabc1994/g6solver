# SPDX-License-Identifier: Apache-2.0
"""Build 6 through the served MCP entry: G6 changes course inside the frozen standard (tests C, D and I).

Fault injection, named again in each test that uses it:
- ``unable_to_start``: ``truth.run_isolated`` is replaced in-process by a runner that cannot start (no exit code, as an
  OSError or a timeout leaves it). The in-place runner (``repo_tests.run``) is real and unaffected.
- ``priced``: two ParamContracts are rebuilt with an operator-declared cost, in this test only. Nothing shipped is
  priced.
- ``spy``: an observer of ``bench.run`` that records each op and repository it was asked for; it changes nothing.
The client only creates, reads and submits; the person confirms at the terminal.
"""
from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
from g6.workforce import truth
from g6.workforce import (
    bench,
    capabilities,
    coding,
    decide,
    reconfigure,
    service,
)

_spec = importlib.util.spec_from_file_location("objective_rig", Path(__file__).with_name("_objective_rig.py"))
_rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rig)
objective_rig, plain = _rig.objective_rig, _rig.plain

# COPY: Synthetic owner's objective, from the founder's Test A verbatim.
OBJECTIVE = "Fix this failing authentication test and verify that the underlying bug is solved."
BEFORE = ('def normalise(name):\n    return name.strip().lower()\n\n'
          'def check_login(name, given, stored):\n    return given == stored\n')
AFTER = BEFORE.replace("return given == stored", "return bool(given) and given == stored")
WRONG = BEFORE.replace("return given == stored", "return given is not None and given == stored")
STILL_WRONG = BEFORE.replace("return given == stored", "return len(given) >= 0 and given == stored")
TESTS = ('from auth import normalise, check_login\n\n'
         'def test_empty_secret_is_rejected():\n    assert not check_login("a", "", "")\n\n'
         'def test_names_are_normalised():\n    assert normalise(" A ") == "a"\n')
FAILING = "tests/test_auth.py::test_empty_secret_is_rejected"
COPY, IN_PLACE = "workforce.run_tests", "workforce.run_tests_in_place"
HUMAN = "Human action required"


def create(rig, *, repository=True):
    if repository:
        rig.repository({"auth.py": BEFORE, "tests/test_auth.py": TESTS})
    found = rig.call("mission_create", objective=plain(OBJECTIVE), risk_tier="R0", budget_cents=0,
                     repo_json=json.dumps({"repo": "auth_service", "module": "auth.py", "function": "check_login"}))
    assert found["ok"], found
    return found["mission_id"]


def status(rig, mid):
    found = rig.call("mission_status", mission_id=mid, detail="full")
    assert found["ok"], found
    return found


def submit(rig, mid, text):
    found = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                     artifact_json=json.dumps({"files": {"auth.py": text}}))
    assert found["ok"], found
    return status(rig, mid)


def unable_to_start(monkeypatch):
    """The fault: the isolated runner cannot start (no exit code), as an OSError or a timeout leaves it."""
    def runner(nodeid, repo):
        # COPY: Synthetic runner-start failure.
        truth.LAST_RUN.update(nodeid=nodeid, rc=None, stdout="", stderr="synthetic: the interpreter could not start")
        return False
    monkeypatch.setattr(truth, "run_isolated", runner)


def spy(monkeypatch):
    """An observer of the bench: (op, repository) per call, in order; every call still runs."""
    real, calls = bench.run, []

    def observed(op, params, **kwargs):
        calls.append((op, params.get("repo")))
        return real(op, params, **kwargs)
    monkeypatch.setattr(bench, "run", observed)
    return calls


def priced(monkeypatch, prices):
    """Operator-declared costs (AUD minor units) for the named bench ops, in this test only; each contract is rebuilt
    through ParamContract.from_dict, so its digest covers the price. The library's census reading is bound to the
    shipped contract's digest (a changed contract reads A0, fail closed), so the operator who re-prices a contract
    re-reads its census too: the same class, bound to the priced digest."""
    real = capabilities.load_contracts

    def contract(op, amount):
        data = json.loads((capabilities.PARAMS_DIR / f"workforce.{op}.json").read_text(encoding="utf-8"))
        data["cost_ceiling"] = {"amount_minor_units": amount, "currency": "AUD"}
        return capabilities.ParamContract.from_dict(data)

    def load(directory=None):
        found = real(directory)
        for op, amount in prices.items():
            found[("workforce", op)] = contract(op, amount)
        return found
    monkeypatch.setattr(capabilities, "load_contracts", load)
    for op, amount in prices.items():
        monkeypatch.setitem(service.CENSUS, ("workforce", op),
                            (service.CENSUS[("workforce", op)][0], contract(op, amount).sha256))


def decisions(mid):
    """The mission's ReconfigurationDecisions, read by an observer of the store."""
    return [e["detail"] for e in service.bind().missions.events(mid, kind="reconfiguration")]


def probe_row(probe_id):
    wf = service.bind()
    row = wf.caps._conn.execute("SELECT passed, reason, op FROM preflight_runs WHERE probe_id=?", (probe_id,)).fetchone()
    assert row is not None, probe_id
    return dict(row)


def reproduce_decision(mid):
    events = service.bind().missions.events(mid, kind="preflight")
    return events[0]["detail"]["answers"][6]["answer"]["reproduce"]


def texts(found):
    """Everything a worker or a person read about this mission: every brief, message and the result."""
    return json.dumps([found["result"], found["awaiting_chat"], found["messages"]])


def test_unavailable_runner_is_recognised_and_replaced_with_provenance(objective_rig, monkeypatch):
    """Faults: ``spy``, then ``unable_to_start`` after the mission is created (both reproduction routes probed and
    ready) and before the person confirms. The in-place runner is real."""
    rig = objective_rig
    mid = create(rig)
    frozen = status(rig, mid)["acceptance"]
    calls = spy(monkeypatch)
    unable_to_start(monkeypatch)
    rig.human(mid)
    found = status(rig, mid)
    # Recognised, and no blind retry: one reproduction call on the copy route, its re-probe, and never again.
    copy = [i for i, (op, _) in enumerate(calls) if op == "run_tests"]
    assert [calls[i][1] for i in copy] == [f"coding-{mid}", bench.PROBE], calls
    assert [op for op, _ in calls[copy[-1] + 1:]] == ["run_tests_in_place", "run_tests_in_place"], calls
    swap, *checks = decisions(mid)
    assert (swap["stage"], swap["trigger"]) == ("reproduce", "tool_unavailable")
    assert swap["changed"] == {"from": COPY, "to": IN_PLACE}
    probe = probe_row(swap["observation"]["probe_id"])
    assert probe["passed"] == 0 and probe["op"] == "run_tests" and "could not start" in probe["reason"]
    assert "could not start" in swap["inadequate_because"]
    assert swap["invariants_before"] == swap["invariants_after"]
    failed = swap["observation"]["execution_id"]
    # The reproduction G6 kept is the in-place runner's: its tallies and verdicts, no exit code it did not see.
    reproduction = found["coding"]["reproduction"]
    assert reproduction["reproduced"] and reproduction["tests"] == {FAILING: False,
                                                                     "tests/test_auth.py::test_names_are_normalised": True}
    assert all(r["executed"] and r["verdict"] in ("passed", "failed") and r["returncode"] is None
               for r in reproduction["receipts"])
    assert failed not in {r["execution_id"] for r in reproduction["receipts"]}
    # No weaker check: each check stage has one census-read route, so each gets "no eligible alternative" and a person.
    assert [(d["stage"], d["trigger"], d["changed"]) for d in checks] == [
        ("check-patch", "tool_unavailable", {"from": COPY, "to": None}),
        ("check-regression", "tool_unavailable", {"from": COPY, "to": None})]
    assert all(d["invariants_before"] == d["invariants_after"] == swap["invariants_before"] for d in checks)
    assert found["completion_state"] == "blocked-escalated" and found["awaiting_chat"] == []
    assert HUMAN in json.dumps(found["result"])
    assert found["acceptance"] == frozen


def test_no_weaker_check_replaces_an_unavailable_one(objective_rig, monkeypatch):
    """Faults: ``spy``, then ``unable_to_start`` after the plan is stored and the worker's brief is out. The worker
    submits a correct patch."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    assert status(rig, mid)["awaiting_chat"]
    calls = spy(monkeypatch)
    unable_to_start(monkeypatch)
    found = submit(rig, mid, AFTER)
    assert calls == [("run_tests", f"coding-{mid}"), ("run_tests", bench.PROBE)], calls
    found_decisions = decisions(mid)
    assert [(d["stage"], d["trigger"], d["changed"]) for d in found_decisions] == [
        ("check-patch", "tool_unavailable", {"from": COPY, "to": None}),
        ("check-regression", "tool_unavailable", {"from": COPY, "to": None})]
    probe_ids = {d["observation"]["probe_id"] for d in found_decisions}
    assert len(probe_ids) == 1 and probe_row(probe_ids.pop())["passed"] == 0
    assert all(d["invariants_before"] == d["invariants_after"] for d in found_decisions)
    wf = service.bind()
    assert wf.ledger.runs(mid, "check-regression") == []  # never dispatched to a tool known to be unavailable
    verdict, = wf.ledger.verdicts(mid)
    assert (verdict["unit_key"], verdict["result"], verdict["executed"]) == ("check-patch", "unmeasured", 0)
    assert found["status"] == "done" and found["completion_state"] == "blocked-escalated"
    # The recorded outcome the client reads names the person's step for each check (the default result's caveats
    # budget may cut the second check's problem; see the build report).
    assert found["awaiting_chat"] == [] and json.dumps(found["outcome"]).count(HUMAN) == 2


def test_a_runner_that_never_started_is_not_reported_as_a_failed_test(objective_rig, monkeypatch):
    """Fault: ``unable_to_start`` after the brief is out. No failure that did not happen reaches the worker or the
    answer; the bench itself answers that the runner could not start."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    unable_to_start(monkeypatch)
    found = submit(rig, mid, AFTER)
    sent_back = [m for m in service.bind().missions.messages(mid, to="patch") if m["from"] != "code"]
    assert sent_back == []  # only the reproduction's instructions were ever sent to the worker
    for false_failure in ("passed is False", "verification failed", '"passed": false'):
        assert false_failure not in texts(found), false_failure
    reply = bench.run("run_tests", {"repo": bench.PROBE, "tests": [bench.PROBE_TEST]})
    assert reply == {"ok": False, "error": reply["error"]} and "could not start" in reply["error"]


def test_the_default_answer_leads_with_the_persons_step_and_the_real_cause(objective_rig, monkeypatch):
    """Fault: ``unable_to_start`` after the brief is out (as C2). Lead ruling R-B6-L2: the default MCP answer (not the
    full status) shows the person's step within its budget, and the unmeasured check says the runner did not start."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    unable_to_start(monkeypatch)
    answer = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"auth.py": AFTER}}))
    assert answer["ok"] and answer["completion_state"] == "blocked-escalated", answer
    shown = answer["result"]["markdown"]
    assert HUMAN in shown and "could not start" in shown, shown
    assert "plan the check so that it reads a field" not in shown, shown
    # M-1: said once, and the caveats the answer showed before still fit its budget.
    assert shown.count(HUMAN) == 1, shown
    assert "Caller-authored tests" in shown and "Not established" in shown, shown


def test_repeated_failure_changes_strategy_then_escalates(objective_rig):
    """No fault injection: the worker's proposals are scripted. P1 fails, P2 repeats P1 byte for byte, P3 fails
    differently."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    frozen = status(rig, mid)["acceptance"]
    wf = service.bind()
    first = submit(rig, mid, WRONG)
    second_brief, = first["awaiting_chat"]
    # The reproduction's instructions, then strategy 1: the failed check's own feedback.
    assert [m["from"] for m in second_brief["messages"]] == ["code", "check-patch"]
    rejected = wf.ledger.last_artifact(mid, "patch")
    second = submit(rig, mid, WRONG)  # P2: the same proposal again
    assert second["status"] != "done"
    assert len(wf.ledger.runs(mid, "patch")) == 2 and len(wf.ledger.runs(mid, "check-patch")) == 1  # not judged again
    changed, = decisions(mid)
    assert (changed["stage"], changed["trigger"]) == ("patch", "repeated_failure")
    assert changed["changed"] == {"from": "feedback", "to": "focused"}
    assert changed["observation"]["repeats"] == rejected["artifact_id"]
    assert "stopped asking" not in changed["inadequate_because"]  # M-3: G6 asks again, so it never says it stopped
    third_brief, = second["awaiting_chat"]
    new = third_brief["messages"][len(second_brief["messages"]):]
    assert len(new) == 1 and new[0]["refs"].get("strategy") == "focused"
    packet = new[0]["refs"]["focused"]
    assert packet["rejected"]["sha256"] == rejected["sha256"]
    assert packet["rejected"]["diff"] == [{"path": "auth.py", "added": 1, "removed": 1, "lines": [
        "-    return given == stored", "+    return given is not None and given == stored"]}]
    assert packet["failing"] == [{"check": "check-patch", "tests": [
        {"test": FAILING, "assertions": ['assert not check_login("a", "", "")']}]}]
    assert packet["rule"]
    assert new[0]["text"] != second_brief["messages"][-1]["text"]
    assert "passed is False" not in json.dumps(new) and "the oracle answered" not in json.dumps(new)
    third = submit(rig, mid, STILL_WRONG)  # P3: still failing, no better on the agreed checks
    assert third["status"] == "done" and third["completion_state"] == "blocked-escalated"
    assert third["awaiting_chat"] == []  # no fourth brief
    first_change, person = decisions(mid)
    assert first_change == changed
    assert (person["stage"], person["trigger"], person["changed"]) == (
        "patch", "no_progress", {"from": "focused", "to": "person"})
    assert (changed["invariants_before"] == changed["invariants_after"] == person["invariants_before"]
            == person["invariants_after"])
    assert third["coding"]["stop_reason"].startswith(HUMAN)
    assert len(wf.ledger.runs(mid, "patch")) == 3 and len(wf.ledger.runs(mid, "check-patch")) == 2
    assert third["acceptance"] == frozen


def test_a_costlier_route_is_replaced_at_the_next_decision_with_the_standard_unchanged(objective_rig, monkeypatch):
    """Fixture ``priced``: mission 1 prices both reproduction routes at 1; before mission 2 the copy route rises to 50.
    Mission 1 has no measured chance yet (a fresh owner), so it never ranks by cost."""
    rig = objective_rig
    priced(monkeypatch, {"run_tests": 1, "run_tests_in_place": 1})
    first = create(rig)
    before = reproduce_decision(first)
    assert before["metadata"]["chosen_by"] == "policy_class"  # no chance is made up for an unmeasured route
    assert before["selected_option"]["required_capabilities"] == [["workforce", "run_tests"]]
    assert all(o["acceptance_probability"] is None for o in before["candidate_options"])
    priced(monkeypatch, {"run_tests": 50, "run_tests_in_place": 1})
    second = create(rig, repository=False)
    after = reproduce_decision(second)
    assert after["selected_option"]["required_capabilities"] == [["workforce", "run_tests_in_place"]]
    assert after["metadata"]["chosen_by"] == "expected_cost_per_accepted_outcome"
    copy, = (o for o in after["candidate_options"] if o["id"] == "reproduce:run_tests")
    assert copy["estimated_cost"] == {"amount_minor_units": 50, "currency": "AUD"}
    assert ["reproduce:run_tests", "its expected cost per accepted outcome, 50 minor units, is not below 1"] in (
        after["metadata"]["rejected"])
    assert after["metadata"]["replanned_from"]["mission_id"] == first
    assert after["metadata"]["because"] == "cost changed"
    assert after["metadata"]["before"]["reproduce:run_tests"] == {"amount_minor_units": 1, "currency": "AUD"}
    assert after["metadata"]["after"]["reproduce:run_tests"] == {"amount_minor_units": 50, "currency": "AUD"}
    assert decisions(first) == []
    replan, = decisions(second)
    assert (replan["stage"], replan["trigger"]) == ("reproduce", "cost_changed")
    assert replan["changed"] == {"from": COPY, "to": IN_PLACE}
    assert replan["observation"]["mission_id"] == first
    assert replan["invariants_before"] == replan["invariants_after"]
    one, two = status(rig, first)["acceptance"], status(rig, second)["acceptance"]
    for key in ("requirements", "verification_requirements", "required_completion_state", "risk_tier",
                "human_approval_requirements"):
        assert one[key] == two[key], key
    standard_one, standard_two = one["metadata"]["coding"], two["metadata"]["coding"]
    assert standard_one.pop("snapshot") != standard_two.pop("snapshot")  # each mission's own frozen copy
    assert standard_one == standard_two  # the task standard is otherwise byte for byte the same
    rig.human(second)
    receipts = status(rig, second)["coding"]["reproduction"]["receipts"]
    assert receipts and all(r["verdict"] in ("passed", "failed") and r["returncode"] is None for r in receipts)


def test_unknown_prices_or_unmeasured_chances_never_trigger_a_replan(objective_rig, monkeypatch):
    """Two controls. Nothing priced: two missions on one task. Fixture ``priced`` on the copy route only (the other's
    cost is unknown), raised from 1 to 50 between missions."""
    rig = objective_rig
    first = create(rig)
    second = create(rig, repository=False)
    priced(monkeypatch, {"run_tests": 1})
    third = create(rig, repository=False)
    priced(monkeypatch, {"run_tests": 50})
    fourth = create(rig, repository=False)
    for mid in (first, second, third, fourth):
        found = reproduce_decision(mid)
        assert found["metadata"]["chosen_by"] == "policy_class", mid
        assert found["selected_option"]["required_capabilities"] == [["workforce", "run_tests"]]
        assert "replanned_from" not in found["metadata"] and decisions(mid) == []


# -- review fix pass (lead rulings I-2, I-3, I-5, M-2, M-4, M-5 and M-12) ------------------------------------------------


def crash_once(monkeypatch, unit):
    """Fault: the first move of ``unit`` to blocked crashes, as a process dying between a run's close and its move."""
    from g6.workforce.graph import WorkGraph

    real, crashed = WorkGraph.escalate, []

    def escalate(self, mission_id, key, reason, **kwargs):
        if key == unit and not crashed:
            crashed.append(reason)
            raise RuntimeError("synthetic crash before the move")
        return real(self, mission_id, key, reason, **kwargs)
    monkeypatch.setattr(WorkGraph, "escalate", escalate)
    return crashed


@pytest.mark.parametrize(("body", "cause"), [
    ("import time\n    time.sleep(120)", "the tests did not finish: timed out after 5 s"),
    ("import os\n    os._exit(1)", "the test process exited with code 1 before reporting")])
def test_a_candidate_that_hangs_or_exits_reaches_the_worker_as_a_failure(objective_rig, monkeypatch, body, cause):
    """Fixture timeout: ``truth.TIMEOUT_S`` lowered after confirmation (no wall-clock assertion). Lead ruling I-2: the
    runner started, so this is the candidate's executed failure, sent back; never "could not start"."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    monkeypatch.setattr(truth, "TIMEOUT_S", 5)
    found = submit(rig, mid, BEFORE.replace("return given == stored", body))
    feedback = [m for m in found["awaiting_chat"][0]["messages"] if m["from"] == "check-patch"]
    assert feedback and cause in feedback[0]["text"], found["awaiting_chat"]
    assert "could not start" not in texts(found) and decisions(mid) == []


def test_a_change_of_course_that_would_change_the_standard_is_refused_without_an_exception(objective_rig, monkeypatch):
    """Faults: ``unable_to_start`` before the person confirms, and the stored acceptance re-read with a deadline it did
    not have when it froze (``MissionStore.acceptance`` wrapped), as if the standard had changed."""
    from g6.workforce.missions import MissionStore

    rig = objective_rig
    mid = create(rig)
    real = MissionStore.acceptance
    monkeypatch.setattr(MissionStore, "acceptance",
                        lambda self, m: replace(real(self, m), deadline="2099-01-01T00:00:00+00:00"))
    unable_to_start(monkeypatch)
    rig.human(mid)  # no exception reaches the person or the client
    found = status(rig, mid)
    assert found["completion_state"] == "blocked-escalated"
    refusals = [d for d in decisions(mid) if d.get("refused")]
    assert len(refusals) == 1 and refusals[0]["stage"] == "reproduce", decisions(mid)
    assert "may not change what counts as done" in found["result"]["markdown"], found["outcome"]
    assert refusals[0]["invariants_before"] != refusals[0]["invariants_after"]


def test_a_test_process_that_starts_only_without_the_proposal_is_the_proposals_failure(objective_rig, monkeypatch):
    """Fault: ``truth.run_isolated`` cannot start only in a copy holding the proposal; the runner's own probe starts.
    Lead ruling I-2: the re-probe passes, so the proposal's failure is sent to the worker, and no route changes."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    real = truth.run_isolated

    def runner(nodeid, repo):
        source = repo / "auth.py"
        if source.is_file() and "bool(given)" in source.read_text(encoding="utf-8"):
            truth.LAST_RUN.update(nodeid=nodeid, rc=None, started=False, stdout="", stderr="synthetic: spawn failed")
            return False
        return real(nodeid, repo)
    monkeypatch.setattr(truth, "run_isolated", runner)
    found = submit(rig, mid, AFTER)
    told = [m for brief in found["awaiting_chat"] for m in brief["messages"] if m["from"] == "check-patch"]
    assert told and told[0]["text"] == reconfigure.CANDIDATE_STOPPED
    assert decisions(mid) == []


def test_a_refused_strategy_change_stops_for_a_person_and_a_re_settle_reuses_it(objective_rig, monkeypatch):
    """Faults: after P1 the attempt cap in force drops to 2 (``scheduler.settings`` wrapped), so the ladder's change no
    longer keeps the frozen limit; and the first move of the patch after P2 crashes (``crash_once``)."""
    from g6.workforce import scheduler

    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    submit(rig, mid, WRONG)
    real = scheduler.settings
    monkeypatch.setattr(scheduler, "settings", lambda mode: replace(real(mode), max_attempts_per_work_unit=2))
    crashed = crash_once(monkeypatch, "patch")
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"auth.py": WRONG}}))
    assert not failed["ok"]  # the synthetic crash (the server says only that the service failed)
    assert crashed and "may not change what counts as done" in crashed[0]
    resumed = rig.call("mission_run", mission_id=mid)
    assert resumed["ok"] and resumed["completion_state"] == "blocked-escalated", resumed
    assert len([d for d in decisions(mid) if d.get("refused")]) == 1  # the re-settle reused it


def test_a_lease_lost_during_the_re_probe_moves_nothing(objective_rig, monkeypatch):
    """Faults: ``unable_to_start`` after the brief; during the re-probe another runner takes the mission's lease
    (``Capabilities.probe`` wrapped; the lease is taken at a later clock, no wall clock is read by an assertion)."""
    from datetime import UTC, datetime, timedelta

    from g6.workforce.capabilities import Capabilities

    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    unable_to_start(monkeypatch)
    real = Capabilities.probe

    def probe(self, *args, **kwargs):
        found = real(self, *args, **kwargs)
        service.bind().missions.take_lease(mid, holder="runner:other", now=datetime.now(UTC) + timedelta(hours=1))
        return found
    monkeypatch.setattr(Capabilities, "probe", probe)
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"auth.py": AFTER}}))
    assert not failed["ok"]
    units = {u["key"]: u["status"] for u in service.bind().graph.units(mid)}
    assert (units["check-patch"], units["check-regression"]) == ("in_progress", "pending")
    assert decisions(mid) == []


def test_a_vetoed_send_back_records_no_change_of_strategy(objective_rig, monkeypatch):
    """Fault: once the repair loop decides (``coding.repair_turn`` wrapped), the runtime's attempt count for the patch
    reads as used up (``Runtime._tries`` wrapped), so the send-back is vetoed."""
    from g6.workforce.runtime import Runtime

    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    submit(rig, mid, WRONG)
    real_turn, real_tries, decided = coding.repair_turn, Runtime._tries, []

    def turn(*args, **kwargs):
        decided.append(True)
        return real_turn(*args, **kwargs)
    monkeypatch.setattr(coding, "repair_turn", turn)
    monkeypatch.setattr(Runtime, "_tries", lambda self, m, spec: spec.max_attempts if decided and spec.key == "patch"
                        else real_tries(self, m, spec))
    found = submit(rig, mid, STILL_WRONG)
    assert decided and decisions(mid) == []
    assert not [m for m in found["messages"] if m["refs"].get("strategy")]


def test_a_route_found_unavailable_is_not_run_again_on_a_later_advance(objective_rig, monkeypatch):
    """Faults: ``spy``, ``unable_to_start`` before the person confirms, and the first in-place reproduction is
    interrupted (``coding._reproduce`` raises once)."""
    rig = objective_rig
    mid = create(rig)
    calls = spy(monkeypatch)
    unable_to_start(monkeypatch)
    real, interrupted = coding._reproduce, []

    def reproduce(source, tests, venture, **kwargs):
        if kwargs.get("op") == bench.IN_PLACE and not interrupted:
            interrupted.append(True)
            raise ValueError("synthetic interruption")
        return real(source, tests, venture, **kwargs)
    monkeypatch.setattr(coding, "_reproduce", reproduce)
    with monkeypatch.context() as person:
        person.setattr(decide, "_interactive", lambda: True)
        assert decide.main(["confirm", mid, "--reason", "synthetic confirmation"], ask=lambda _: mid) == 2
    coding.advance(service.bind(), mid)
    assert calls.count(("run_tests", f"coding-{mid}")) == 1, calls
    assert status(rig, mid)["coding"]["reproduction"]["reproduced"]


def test_an_alternative_route_with_no_fresh_probe_is_probed_before_it_is_excluded(objective_rig, monkeypatch):
    """Faults: ``unable_to_start`` before the person confirms, and the in-place route's latest probe made to fail once
    (``bench._in_place`` replaced for that probe only)."""
    rig = objective_rig
    mid = create(rig)
    unable_to_start(monkeypatch)
    with monkeypatch.context() as broken:  # the in-place route's latest probe fails once (not passed)
        broken.setattr(bench, "_in_place", lambda params, venture: {"ok": False, "error": "synthetic probe failure"})
        assert service.probe(service.bind(), "workforce", "run_tests_in_place")["level"] != "probe_passed"
    rig.human(mid)
    assert decisions(mid)[0]["changed"] == {"from": COPY, "to": IN_PLACE}


def test_a_re_settle_after_a_crash_reuses_the_recorded_decision(objective_rig, monkeypatch):
    """Faults: ``unable_to_start`` after the brief; the first move of check-patch crashes (``crash_once``); ``spy``
    then counts any probe on the re-settle."""
    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    unable_to_start(monkeypatch)
    crash_once(monkeypatch, "check-patch")
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"auth.py": AFTER}}))
    assert not failed["ok"]
    calls = spy(monkeypatch)
    resumed = rig.call("mission_run", mission_id=mid)
    assert resumed["ok"] and resumed["completion_state"] == "blocked-escalated", resumed
    assert calls == []  # no second probe
    assert [d["stage"] for d in decisions(mid)] == ["check-patch", "check-regression"]


# -- deferred review minors (#171: M-6 and M-10) ---------------------------------------------------------------------------


def test_a_search_whose_runner_never_started_stops_for_a_person_whichever_check_settles_first(objective_rig,
                                                                                              monkeypatch):
    """Faults: ``unable_to_start`` after the brief; the graph offers its claimable units in reverse order
    (``WorkGraph.claimable`` wrapped), so check-regression settles before check-patch. The proposal also changes
    ``normalise``, so the search for hidden failures has inputs to run. Review M-6: that search answered "not run"
    because its runner never started, so it is re-probed and stops for a person like check-patch, never with the generic
    advice."""
    from g6.workforce.graph import WorkGraph

    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    real = WorkGraph.claimable
    monkeypatch.setattr(WorkGraph, "claimable", lambda self, m: list(reversed(real(self, m))))
    unable_to_start(monkeypatch)
    found = submit(rig, mid, AFTER.replace("def normalise(name):\n",
                                           "def normalise(name):\n    if name == 0:\n        return name\n"))
    assert [d["stage"] for d in decisions(mid)] == ["check-regression", "check-patch"], decisions(mid)
    assert all((d["trigger"], d["changed"]) == ("tool_unavailable", {"from": COPY, "to": None}) for d in decisions(mid))
    assert found["completion_state"] == "blocked-escalated" and json.dumps(found["outcome"]).count(HUMAN) == 2
    assert "plan the check so that it reads a field" not in json.dumps(found["outcome"]), found["outcome"]


def test_an_attempt_that_left_no_proposal_still_counts_toward_the_proposal_limit(objective_rig, monkeypatch):
    """Fault: the second submission's claim is refused once (``WorkGraph.claim`` wrapped), so that attempt is abandoned
    and leaves no proposal. Review M-10: the repair ladder counts the ledger's attempts, so after P2 (the third and last
    attempt) it stops for a person, never sending a focused brief no attempt is left to answer."""
    from g6.workforce.graph import WorkGraph

    rig = objective_rig
    mid = create(rig)
    rig.human(mid)
    submit(rig, mid, WRONG)
    real, refused = WorkGraph.claim, []

    def claim(self, mission_id, key, **kwargs):
        if key == "patch" and not refused:
            refused.append(key)
            raise ValueError("synthetic: the claim was refused")
        return real(self, mission_id, key, **kwargs)
    monkeypatch.setattr(WorkGraph, "claim", claim)
    lost = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                    artifact_json=json.dumps({"files": {"auth.py": WRONG}}))
    assert refused and not lost["ok"]
    wf = service.bind()
    assert len(wf.ledger.runs(mid, "patch")) == 2 and len(wf.missions.events(mid, kind="artifact")) == 1
    found = submit(rig, mid, STILL_WRONG)
    assert found["coding"]["stop_reason"].startswith(HUMAN), found["coding"]["stop_reason"]
    person, = decisions(mid)
    assert (person["stage"], person["trigger"], person["changed"]) == (
        "patch", "no_progress", {"from": "feedback", "to": "person"})
    assert found["completion_state"] == "blocked-escalated" and found["awaiting_chat"] == []
