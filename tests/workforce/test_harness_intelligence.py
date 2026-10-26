# SPDX-License-Identifier: Apache-2.0
"""New authored HIC population; task-specific construction is not ordinary test replay."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from g6.workforce import acceptance, bench, service, trace

_spec = importlib.util.spec_from_file_location(
    "hic_objective_fixture", Path(__file__).with_name("test_coding_objective.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
objective_rig = _fixture.objective_rig
_plan_spec = importlib.util.spec_from_file_location(
    "hic_plan_fixture", Path(__file__).with_name("test_coding_standard_plan.py"))
_plan_fixture = importlib.util.module_from_spec(_plan_spec)
_plan_spec.loader.exec_module(_plan_fixture)
# copied from docs/validation/autonomous-business/hic05-dev-v1
POPULATION = Path(__file__).resolve().parent / "data" / "hic05-dev-v1"


@pytest.fixture(autouse=True)
def bounded_development_checks(monkeypatch):
    from g6.workforce import truth

    # Enforce the frozen development protocol's per-check cap. Production
    # defaults are not changed, and no provider/model call is introduced.
    monkeypatch.setattr(truth, "TIMEOUT_S", 30)
    monkeypatch.setattr(truth, "SUITE_TIMEOUT_S", 30)


def _created(rig):
    task = json.loads((POPULATION / "task.json").read_text(encoding="utf-8"))
    # Keep every original rig check, now satisfied, alongside the new population.
    # No old webhook duplicate check is removed or weakened for this population.
    (rig.repo / "policy.py").write_text(_fixture.AFTER, encoding="utf-8")
    for name in ("inventory.py", "tests/test_inventory.py"):
        (rig.repo / name).write_bytes((POPULATION / name).read_bytes())
    for args in (("add", "."), ("-c", "user.name=Codex", "-c", "user.email=codex@example.invalid",
                                "commit", "-m", "new public HIC development population")):
        subprocess.run(["git", *args], cwd=rig.repo, check=True, capture_output=True)
    made = rig.call("mission_create", objective=task["objective"], risk_tier="R0", budget_cents=0,
                    repo_json=json.dumps({"repo": "sample", "module": "inventory.py", "function": "apply_event"}))
    assert made["ok"], made
    return made


def test_hic_mcp_constructs_check_for_nonadjacent_replay(objective_rig):
    rig = objective_rig
    made = _created(rig)
    mid = made["mission_id"]
    inventory = rig.call("workforce_capabilities")
    candidates = {("intent_engine", "elicit"), ("alignment", "model_intent"), ("plan_critic", "critique"),
                  ("formal_verification", "verify"), ("coding_agent_gate", "verify_answer"),
                  ("workforce", "run_tests"), ("workforce", "run_tests_in_place"),
                  ("workforce", "run_snippet_tests"), ("reliability_pipeline", "infer")}
    selected_rows = [row for row in inventory["capabilities"] if (row["component"], row["op"]) in candidates]
    wf = service.bind()
    qualified = [{"component": c, "op": o, "authority": wf.caps.authority(c, o),
                  "level": wf.caps.readiness(c, o).level, "reason": wf.caps.readiness(c, o).reason}
                 for c, o in sorted(candidates)]
    confirmation_error = ""
    try:
        rig.human(mid)  # Existing terminal-decision path, explicitly simulated by this fixture.
    except AssertionError:
        confirmation_error = "terminal confirmation failed to advance; see preserved stderr"
    run = rig.call("mission_run", mission_id=mid)
    status = rig.call("mission_status", mission_id=mid, detail="full")
    print(json.dumps({"case": "hic-missing-construction", "mission_run": run, "status": status,
                      "trace": trace.assemble(trace.gather(wf.missions, mid)),
                      "probed_mcp_rows": selected_rows, "probed_mcp_count": len(inventory["capabilities"]),
                      "capability_rows": qualified, "contract_denominator": len(wf.caps.contracts()),
                      "confirmation_error": confirmation_error,
                      "scope": "new authored/unblinded public population; no agent/model inference"}, sort_keys=True))
    constructed = status["acceptance"]["metadata"]["coding"].get("harness_intelligence", {})
    assert constructed.get("checks"), "HIC-05 prerequisite absent: no task-specific check was constructed"
    assert any(h["failure_id"] == "nonadjacent_event_replay" for h in constructed["hypotheses"])
    assert not confirmation_error
    accepted = status["acceptance"]
    standard = accepted["metadata"]["coding"]
    check = constructed["checks"][0]
    node = check["nodeid"]
    snapshot = bench._repo(standard["snapshot"], wf.venture_id)
    assert standard["test_digests"][node.split("::")[0]] == check["source_sha256"]
    assert hashlib.sha256((snapshot / node.split("::")[0]).read_bytes()).hexdigest() == check["source_sha256"]
    assert standard["original_baseline_digest"] != standard["baseline_digest"]
    assert not (rig.repo / ".g6_harness").exists()
    assert any("identical event" in line for line in acceptance.describe(wf.missions.acceptance(mid)))
    preflight = wf.missions.events(mid, kind="preflight")[0]["detail"]
    assert preflight["answers"][3]["answer"].get("task_specific_hypotheses") == constructed["hypotheses"]
    reproduction = status["coding"]["reproduction"]
    assert reproduction["reproduced"] and reproduction["tests"][node] is False
    assert all(reproduction["tests"][n] for n in standard["original_required_tests"])

    bad = (POPULATION / "inventory.py").read_text(encoding="utf-8")
    candidate = {"files": {"inventory.py": bad}}
    # Operation-level ablation under the SAME accepted standard/snapshot/artifact.
    # It is an observation, not an admissible weakened mission or assurance pass.
    # run_tests also runs its normal whole tests/ regression comparison.
    fixed = bench.run("run_tests", {"repo": standard["snapshot"], "tests": standard["original_required_tests"],
                                    **candidate}, venture=wf.venture_id)
    assert fixed["ok"] and fixed["result"]["passed"], fixed
    assert node not in fixed["result"]["tests"]
    assert not any(".g6_harness" in n for n in fixed["result"].get("suite", {}).get("tests", []))
    failed = rig.call("mission_submit", mission_id=mid, unit_key="patch", artifact_json=json.dumps(candidate))
    assert failed["ok"] and failed["status"] != "done", failed
    first = wf.ledger.last_artifact(mid, "patch")
    assert first["content"] == candidate
    assert any(v["executed"] and v["result"] == "fail" for v in wf.ledger.verdicts(mid))
    fixed_line = 'if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:'
    repair = bad.replace(fixed_line, 'if event["id"] not in result["seen_ids"]:')
    assert repair != bad
    repaired = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                        artifact_json=json.dumps({"files": {"inventory.py": repair}}))
    print(json.dumps({"case": "hic05-ablation-repair", "standard_sha": accepted["sha256"],
                      "B1_fixed_checks": fixed, "B1_omitted_predicate": check["predicate_id"],
                      "B1_scope": "native operation ablation; missing predicate is uncovered, not accepted",
                      "B2_failed": failed, "B2_repaired": repaired,
                      "trace": trace.assemble(trace.gather(wf.missions, mid))}, sort_keys=True))
    assert repaired["ok"] and repaired["status"] == "done", repaired
    assert repaired["completion_state"] == "qualified-draft"
    assert "harness_pack" not in repaired["coding"]  # Full pack is opt-in detail, not repeated in every repair brief.
    final = rig.call("mission_status", mission_id=mid, detail="full")
    assert final["acceptance"] == accepted
    last = wf.ledger.last_artifact(mid, "patch")
    assert first["sha256"] != last["sha256"]
    latest = [v for v in wf.ledger.verdicts(mid) if v["artifact_id"] == last["artifact_id"]]
    assert latest and all(v["executed"] and v["result"] == "pass" and not v["independent"] for v in latest)
    pack = final["coding"].get("harness_pack")
    assert pack, "HIC-10 prerequisite absent: no reusable same-standard Harness Pack"
    assert pack["standard"]["sha256"] == accepted["sha256"]
    assert pack["artifact"]["sha256"] == last["sha256"]
    assert pack["construction"]["checks"][0]["source_sha256"] == check["source_sha256"]
    assert pack["assurance"]["completion_state"] == "qualified-draft"
    assert pack["execution"]["scope"] == "externally_supplied_artifact"
    assert pack["reuse"]["required_tests"] == standard["required_tests"]
    assert pack["learning"]["promotion"] == "none"
    assert pack["construction_receipts"]
    assert pack["execution"]["cost"]["amount_minor_units"] is None
    assert (rig.repo / "inventory.py").read_bytes() == (POPULATION / "inventory.py").read_bytes()
    assert pack == rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    print(json.dumps({"case": "hic-harness-pack", "pack": pack}, sort_keys=True))


def test_hic_noop_cannot_pass_by_satisfying_only_the_constructed_relation(objective_rig):
    rig = objective_rig
    mid = _created(rig)["mission_id"]
    rig.human(mid)
    before = rig.call("mission_status", mission_id=mid, detail="full")
    standard = before["acceptance"]["metadata"]["coding"]
    check = standard["harness_intelligence"]["checks"][0]
    no_op = 'def apply_event(state, event):\n    return {"quantity": state["quantity"], "seen_ids": list(state["seen_ids"])}\n'
    result = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"inventory.py": no_op}}))
    assert result["ok"] and result["status"] != "done", result
    wf = service.bind()
    verdict = next(v for v in wf.ledger.verdicts(mid) if v["unit_key"] == "check-patch")
    assert verdict["executed"] and verdict["result"] == "fail" and not verdict["independent"]
    # Discriminate why: the relation passes, but the preserved positive example fails.
    isolated = bench.run("run_tests", {"repo": standard["snapshot"],
                                      "tests": [check["nodeid"]], "files": {"inventory.py": no_op}},
                         venture=wf.venture_id)
    assert isolated["result"]["tests"][check["nodeid"]] is True
    assert isolated["result"]["suite"]["status"] == "fail"
    assert "tests/test_inventory.py::test_new_event" in isolated["result"]["suite"]["tests"]
    print(json.dumps({"case": "hic-noop-nonvacuity", "mission_result": result, "relation_only": isolated}, sort_keys=True))


@pytest.mark.parametrize("attack", ["omit-generated-check", "change-generated-bytes", "extra-check-file"])
def test_hic_obligation_cannot_be_omitted_or_rewritten(objective_rig, attack):
    rig = objective_rig
    mid = _created(rig)["mission_id"]
    before = rig.call("mission_status", mission_id=mid, detail="full")
    standard = before["acceptance"]["metadata"]["coding"]
    check = standard["harness_intelligence"]["checks"][0]
    wf = service.bind()
    if attack == "omit-generated-check":
        # Interrupted confirmation before plan materialization: same production
        # recovery case used by the existing standard-binding suite.
        wf.runtime.confirm_acceptance(mid, reason="synthetic interrupted confirmation",
                                      actor="human", decided_by="fixture:human")
        plan = _plan_fixture._plan(before["acceptance"])
        plan["units"][1]["oracle"]["params"]["tests"] = deepcopy(standard["original_required_tests"])
        result = rig.call("mission_plan", mission_id=mid, plan_json=json.dumps(plan))
        assert not result["ok"] and any("frozen coding standard" in p for p in result["problems"]), result
        assert wf.ledger.runs(mid) == []
    else:
        rig.human(mid)
        if attack == "change-generated-bytes":
            target = bench._repo(standard["snapshot"], wf.venture_id) / check["nodeid"].split("::")[0]
            target.write_text("def test_nonadjacent_event_replay(): assert True\n", encoding="utf-8")
            submitted = rig.call("mission_submit", mission_id=mid, unit_key="patch", artifact_json=json.dumps({
                "files": {"inventory.py": (POPULATION / "inventory.py").read_text(encoding="utf-8")}}))
            result = rig.call("mission_run", mission_id=mid)
            print(json.dumps({"case": "hic-tampered-check-after-submission", "submitted": submitted, "continued": result},
                             sort_keys=True))
            assert result.get("status") != "done"
            assert not any(v["executed"] and v["result"] == "pass" for v in wf.ledger.verdicts(mid))
        else:
            result = rig.call("mission_submit", mission_id=mid, unit_key="patch", artifact_json=json.dumps({
                "files": {"inventory.py": (POPULATION / "inventory.py").read_text(encoding="utf-8"),
                          check["nodeid"].split("::")[0]: "def test_nonadjacent_event_replay(): assert True\n"}}))
        if attack == "extra-check-file":
            assert not result["ok"], result
            assert wf.ledger.last_artifact(mid, "patch") is None
    assert wf.missions.acceptance(mid).sha256 == before["acceptance"]["sha256"]
    print(json.dumps({"case": "hic-frozen-obligation", "attack": attack, "result": result}, sort_keys=True))


def test_hic_check_changing_frozen_bytes_during_execution_cannot_keep_a_passing_verdict(objective_rig, monkeypatch):
    rig = objective_rig
    mid = _created(rig)["mission_id"]
    rig.human(mid)
    wf = service.bind()
    standard = wf.missions.acceptance(mid).metadata["coding"]
    path = bench._repo(standard["snapshot"], wf.venture_id) / standard["harness_intelligence"]["checks"][0]["nodeid"].split("::")[0]
    original = bench.run
    mutated = []

    def invoke(op, params, *, venture=None):
        reply = original(op, params, venture=venture)
        if params.get("repo") == standard["snapshot"] and params.get("files") and "regression" not in params:
            path.write_bytes(path.read_bytes() + b"\n# injected after checker returned\n")
            mutated.append(True)
        return reply

    monkeypatch.setattr(bench, "run", invoke)
    bad = (POPULATION / "inventory.py").read_text(encoding="utf-8")
    good = bad.replace('if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:',
                       'if event["id"] not in result["seen_ids"]:')
    result = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"inventory.py": good}}))
    assert mutated and not result["ok"], result
    runs = [r for r in wf.ledger.runs(mid) if r["unit_key"] == "check-patch"]
    assert len(runs) == 1 and runs[0]["state"] == "closed"
    assert runs[0]["completion_state"] == "blocked-escalated"
    assert runs[0]["failure_code"] == "coding_snapshot_changed"
    assert not wf.ledger.verdicts(mid)
    print(json.dumps({"case": "hic-post-check-custody", "response": result, "preserved_run": runs[0]}, sort_keys=True))


@pytest.fixture(autouse=True)
def _legacy_without_certificates(monkeypatch):
    """S0 (2026-10-16): certificates are on by default now, and a verified certificate lifts a clean mission to verified (founder R6-P). These tests were written for the close without
    certificates; this pins that, and every assertion is unchanged."""
    monkeypatch.setenv("G6_WORKFORCE_CERTIFICATES", "0")
