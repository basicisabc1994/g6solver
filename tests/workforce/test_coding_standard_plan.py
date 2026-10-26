# SPDX-License-Identifier: Apache-2.0
"""A custom plan cannot replace a coding objective's accepted checks.

The confirmation is persisted without advance to reproduce a restart between
the human decision and default-plan materialization. All plan requests use MCP.
"""
from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
from g6.workforce import service
from g6.workforce.plans import PlanRejected, parse_plan

_spec = importlib.util.spec_from_file_location(
    "coding_objective_fixture", Path(__file__).with_name("test_coding_objective.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
objective_rig = _fixture.objective_rig


def _confirmed(rig):
    mid = _fixture._create(rig)["mission_id"]
    wf = service.bind()
    wf.runtime.confirm_acceptance(mid, reason="synthetic interrupted confirmation",
                                  actor="human", decided_by="fixture:human")
    status = _fixture._status(rig, mid)
    assert status["plan_revision"] == 0
    return mid, status["acceptance"]


def _plan(accepted):
    standard = accepted["metadata"]["coding"]
    return {"units": [
        {"key": "patch", "kind": "do", "goal": _fixture.OBJECTIVE,
         "output_schema": "files", "risk": "R2", "budget_cents": 0, "max_attempts": 3},
        {"key": "required-tests", "kind": "verify", "verifies": "patch", "goal": "Check accepted tests",
         "capabilities": [["workforce", "run_tests"]], "max_attempts": 1,
         "oracle": {"capability": ["workforce", "run_tests"], "params": {
             "repo": standard["snapshot"], "tests": list(standard["required_tests"])},
             "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True}, "kind": "test"}},
        {"key": "regression", "kind": "verify", "verifies": "patch", "goal": "Preserve behavior",
         "capabilities": [["workforce", "run_tests"]], "max_attempts": 1,
         "oracle": {"capability": ["workforce", "run_tests"], "params": {
             "repo": standard["snapshot"], "tests": list(standard["required_tests"]),
             "regression": {**standard["task_signature"], "declared": standard.get("declared", [])}},
             "bind": {"files": "patch.files"}, "expect": {"field": "passed", "equals": True},
             "kind": "deterministic"}},
    ], "edges": []}


def _weaken(plan, change):
    check = plan["units"][1]["oracle"]
    if change == "missing-test":
        check["params"]["tests"] = [_fixture.BOUNDARY]
    elif change == "wrong-snapshot":
        check["params"]["repo"] = "sample"
    elif change == "wrong-expectation":
        check["expect"]["equals"] = False
    elif change == "wrong-mechanism":
        check["kind"] = "deterministic"
    elif change == "dynamic-tests":
        del check["params"]["tests"]
        check["bind"]["tests"] = "patch.files"
    elif change == "unbound-patch":
        check["bind"]["files"] = "patch.files.policy"
    elif change == "missing-regression":
        plan["units"].pop()
    elif change == "wrong-regression":
        plan["units"][2]["oracle"]["params"]["regression"]["function"] = "accepts"
    elif change == "more-proposals":
        plan["units"][0]["max_attempts"] = 4
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("change", ["missing-test", "wrong-snapshot", "wrong-expectation", "wrong-mechanism",
                                    "dynamic-tests", "unbound-patch", "missing-regression", "wrong-regression"])
def test_mcp_refuses_plan_weakening_accepted_checks(objective_rig, change):
    rig = objective_rig
    mid, accepted = _confirmed(rig)
    plan = _plan(accepted)
    _weaken(plan, change)
    parse_plan(plan, risk_tier="R2", budget_cents=0)  # The generic graph/schema rules alone admit it.
    found = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
    if found["ok"] and change == "missing-test":
        unchanged = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                             artifact_json=json.dumps({"files": {"policy.py": _fixture.BEFORE}}))
        print(json.dumps({"case": change, "unchanged_submission": unchanged}, sort_keys=True))
    print(json.dumps({"case": change, "plan_admitted": found["ok"], "error": found.get("error")}, sort_keys=True))
    assert not found["ok"], found
    status = _fixture._status(rig, mid)
    assert status["plan_revision"] == 0
    assert status["acceptance"] == accepted
    assert status["runs"] == []


def test_mcp_equivalent_plan_catches_unfixed_objective_then_accepts_repair(objective_rig):
    rig = objective_rig
    mid, accepted = _confirmed(rig)
    plan = _plan(accepted)
    # Names and test order are choices; the obligations are not.
    plan["units"][1]["oracle"]["params"]["tests"].reverse()
    found = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
    assert found["ok"], found
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"policy.py": _fixture.BEFORE}}))
    assert failed["ok"] and failed["status"] != "done", failed
    assert any(v["unit_key"] == "required-tests" and v["result"] == "fail" and v["executed"]
               for v in service.bind().ledger.verdicts(mid))
    repaired = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                        artifact_json=json.dumps({"files": {"policy.py": _fixture.AFTER}}))
    assert repaired["ok"] and repaired["status"] == "done", repaired
    assert repaired["completion_state"] == "qualified-draft"  # Caller tests do not establish independence.
    assert _fixture._status(rig, mid)["acceptance"] == accepted
    print(json.dumps({"case": "enforced-customer-journey", "failed": failed, "repaired": repaired}, sort_keys=True))


def test_store_admission_cannot_bypass_coding_standard(objective_rig):
    mid, accepted = _confirmed(objective_rig)
    plan = _plan(accepted)
    _weaken(plan, "missing-test")
    wf = service.bind()
    with pytest.raises(PlanRejected, match="frozen coding standard"):
        wf.missions.add_plan(mid, plan, actor="chat", ready=wf.caps.ready)
    assert wf.missions.get(mid)["plan_revision"] == 0


@pytest.mark.parametrize("change", ["wrong-snapshot", "more-proposals"])
def test_amendment_cannot_weaken_coding_standard(objective_rig, change):
    rig = objective_rig
    mid, accepted = _confirmed(rig)
    plan = _plan(accepted)
    assert rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))["ok"]
    wf = service.bind()
    revision, stored = wf.missions.plan(mid)
    _weaken(plan, change)
    weakened = parse_plan(plan, risk_tier="R2", budget_cents=0)
    with pytest.raises(PlanRejected, match="frozen coding standard"):
        wf.missions.amend_plan(mid, weakened, expect_revision=revision, actor="chat", detail={})
    assert wf.missions.plan(mid)[1].sha256 == stored.sha256


@pytest.mark.parametrize("change", ["missing-test", "more-proposals"])
def test_recovery_refuses_legacy_weak_plan_without_rewriting_history(objective_rig, change):
    mid, accepted = _confirmed(objective_rig)
    plan = _plan(accepted)
    _weaken(plan, change)
    weakened = parse_plan(plan, risk_tier="R2", budget_cents=0)
    wf = service.bind()
    # Seed the validly hashed but unsafe revision the old admission code could write.
    with wf.missions._conn:
        wf.missions._conn.execute(
            "INSERT INTO mission_plans (mission_id,revision,plan_json,plan_sha,actor,created_at) VALUES (?,?,?,?,?,?)",
            (mid, 1, weakened.to_json(), weakened.sha256, "chat", "2026-10-12T00:00:00+00:00"))
    with pytest.raises(ValueError, match="mission_create"):
        wf.graph.materialize(mid, actor="code")
    assert wf.ledger.runs(mid) == []
    refusal = objective_rig.call("mission_run", mission_id=mid)
    assert not refusal["ok"] and "mission_create" in refusal["error"], refusal
    assert "mission_plan (or mission_delegate)" not in refusal["error"]
    row = wf.missions._conn.execute("SELECT plan_sha FROM mission_plans WHERE mission_id=?", (mid,)).fetchone()
    assert row["plan_sha"] == weakened.sha256
    # Cancellation through the existing store does not require reading the unsafe plan.
    # This is a store control, not a claim that MCP already exposes cancellation.
    wf.missions.cancel(mid, reason="synthetic migration control", actor="human")
    assert wf.missions.plan(mid)[1].sha256 == weakened.sha256


def test_mcp_keeps_split_and_additive_required_checks(objective_rig):
    rig = objective_rig
    mid, accepted = _confirmed(rig)
    plan = _plan(accepted)
    check = plan["units"][1]
    extra = deepcopy(check)
    extra["key"] = "label-check"
    extra["oracle"]["params"]["tests"] = [_fixture.OBJECTIVE_CHECK]
    check["oracle"]["params"]["tests"] = [_fixture.BOUNDARY]
    plan["units"].append(extra)
    redundant = deepcopy(extra)
    redundant["key"] = "additional-label-check"
    plan["units"].append(redundant)
    found = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
    assert found["ok"], found
    done = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                    artifact_json=json.dumps({"files": {"policy.py": _fixture.AFTER}}))
    assert done["ok"] and done["status"] == "done", done
    verdicts = service.bind().ledger.verdicts(mid)
    assert {v["unit_key"] for v in verdicts if v["executed"] and v["result"] == "pass"} == {
        "required-tests", "label-check", "additional-label-check", "regression"}
    assert _fixture._status(rig, mid)["acceptance"] == accepted


@pytest.mark.parametrize("attempts", [4, 5])
def test_mcp_refuses_more_proposals_than_frozen_standard(objective_rig, attempts):
    mid, accepted = _confirmed(objective_rig)
    plan = _plan(accepted)
    assert accepted["metadata"]["coding"]["max_attempts"] == 3
    plan["units"][0]["max_attempts"] = attempts
    parse_plan(plan, risk_tier="R2", budget_cents=0)
    found = objective_rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
    print(json.dumps({"case": "proposal-ceiling", "attempts": attempts, "response": found}, sort_keys=True))
    assert not found["ok"], found
    assert any("proposals" in problem for problem in found["problems"])
    assert _fixture._status(objective_rig, mid)["runs"] == []


def _programmatic(rig, monkeypatch, content, *, name="fixture_producer", side_effect="read", model_free=True,
                  envelope=False):
    """A scripted, no-inference producer, through real admission/door/ledger/MCP.

    The injected declaration/authority qualifies only this authored test fixture.
    It does not qualify a shipped component or a real coding agent.
    """
    from g6.workforce import bench, capabilities

    original_bind = service.bind
    base = original_bind()
    contracts = base.caps.contracts()
    contracts[(name, "draft")] = capabilities.ParamContract.from_dict({
        "component": name, "op": "draft", "required": {}, "optional": {},
        "probe_params": {}, "expect_field": "files", "side_effect": side_effect, "model_free": model_free,
        "shadow_probe": side_effect != "read",  # This fixture's injected probe has no effects.
        "check": "input_model"})

    class Registry:
        def list_all(self):
            return [name]

        def get_class(self, key):
            assert key == name
            return type(name, (), {})

        def describe(self, key):
            return {"backend": {"installed": None}}

    executed = []

    def invoke(component, op, params):
        executed.append((component, op))
        if component == name:
            result = content() if callable(content) else content
            if envelope:
                return result
            return {"ok": True, "completion_state": "qualified-draft",
                    "result": result}
        assert component == "workforce"
        return bench.run(op, params, venture=base.venture_id)

    wf = original_bind(contracts=contracts, registry=Registry(), invoke=invoke,
                       authority=lambda c, o: "A2" if c == name else base.caps.authority(c, o))
    # The probe is a separate known-valid reply, never used as a task result.
    assert wf.caps.probe(name, "draft", lambda *_: {"ok": True, "result": {"files": {}}},
                         actor="code").level == "probe_passed"
    monkeypatch.setattr(service, "bind", lambda *a, **kw: wf)
    return wf, executed




@pytest.mark.parametrize("case", ["outside-scope", "test-edit", "malformed", "root-path", "non-json", "valid"])
def test_mcp_programmatic_patch_obeys_frozen_file_scope(objective_rig, monkeypatch, case):
    rig = objective_rig
    mid, accepted = _confirmed(rig)
    content = {"files": {"policy.py": _fixture.AFTER}}
    if case == "outside-scope":
        content["files"]["unapproved.py"] = "unauthorized = True\n"
    elif case == "test-edit":
        content["files"]["tests/test_objective.py"] = "def test_label():\n    assert True\n"
    elif case == "malformed":
        content = {"files": ["policy.py"]}
    elif case == "root-path":
        content = {"files": {"./": "not a file"}}
    elif case == "non-json":
        content = {"files": {"policy.py": Decimal("1.25")}}
    wf, executed = _programmatic(rig, monkeypatch, content)
    plan = _plan(accepted)
    plan["units"][0].update(capabilities=[["fixture_producer", "draft"]],
                             call={"capability": ["fixture_producer", "draft"], "params": {}})
    if case == "valid":
        plan["units"][0]["max_attempts"] = 2  # A tighter limit is permitted.
    admitted = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
    assert admitted["ok"], admitted
    executed.clear()
    found = rig.call("mission_run", mission_id=mid)
    assert found["ok"], found
    runs = wf.ledger.runs(mid, "patch")
    print(json.dumps({"case": "producer-" + case, "response": found,
                      "runs": runs, "executed": executed,
                      "rejected": wf.missions.events(mid, kind="coding.output_refused"),
                      "closed": wf.missions.events(mid, kind="run.closed")}, sort_keys=True))
    assert len(runs) == 1, runs
    assert ("fixture_producer", "draft") in executed
    if case == "valid":
        assert found["status"] == "done", found
        assert found["completion_state"] == "qualified-draft"
        assert any(v["executed"] and v["result"] == "pass" for v in wf.ledger.verdicts(mid))
        assert wf.ledger.last_artifact(mid, "patch")["content"] == content
    else:
        from g6.workforce import economics

        assert found["completion_state"] == "blocked-escalated", found
        assert runs[0]["failure_code"] == "coding_scope"
        assert wf.ledger.last_artifact(mid, "patch") is None
        assert wf.ledger.verdicts(mid) == []
        calls = json.loads(runs[0]["calls_json"])
        assert len(calls) == 1 and calls[0]["refused"] is False  # The producer really executed.
        refused = wf.missions.events(mid, kind="coding.output_refused")
        assert len(refused) == 1
        assert refused[0]["detail"]["run_id"] == runs[0]["run_id"]
        detail = refused[0]["detail"]
        assert detail["content"] == json.loads(json.dumps(content, default=str))
        assert detail["provenance"]["call"]["contract_sha"] == calls[0]["contract_sha"]
        assert detail["provenance"]["inputs"] == {}
        closed = [e["detail"] for e in wf.missions.events(mid, kind="run.closed")
                  if e["detail"]["run_id"] == runs[0]["run_id"]]
        assert closed[0]["context"]["output_bytes"] == economics.size_of(content)
        assert len(wf.ledger.cost_entries(mid, run_id=runs[0]["run_id"])) == 1
        assert runs[0]["cost_source"] == "unknown"  # Unmeasured producer cost never becomes measured zero.
        assert executed == [("fixture_producer", "draft")]  # No tests execute the disallowed files.
        rig.call("mission_run", mission_id=mid)  # No automatic repeat of a permissions failure.
        assert len(wf.ledger.runs(mid, "patch")) == 1
    assert _fixture._status(rig, mid)["acceptance"] == accepted


@pytest.mark.parametrize("writer", ["submit", "correct"])
def test_runtime_direct_writer_cannot_bypass_frozen_scope(objective_rig, writer):
    rig = objective_rig
    mid = _fixture._create(rig)["mission_id"]
    rig.human(mid)
    wf = service.bind()
    content = {"files": {"policy.py": _fixture.AFTER, "unapproved.py": "extra = True\n"}}
    args = {"reason": "synthetic direct correction", "actor": "human"} if writer == "correct" else {}
    with pytest.raises(ValueError, match="frozen|scope"):
        getattr(wf.runtime, writer)(mid, "patch", content, **args)
    assert wf.ledger.runs(mid) == []
