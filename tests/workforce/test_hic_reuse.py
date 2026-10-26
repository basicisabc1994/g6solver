# SPDX-License-Identifier: Apache-2.0
"""A completed Harness Pack must check a second change, never reuse its old pass."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from g6.workforce import acceptance, bench, service
from g6.workforce.missions import MissionStore

_spec = importlib.util.spec_from_file_location("hic_reuse_fixture", Path(__file__).with_name("test_harness_intelligence.py"))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
objective_rig = _fixture.objective_rig
bounded_development_checks = _fixture.bounded_development_checks


def completed(rig, *, derived=False):
    if derived:
        # Separate coverage control in scratch only; the frozen development population is unchanged.
        (rig.repo / "policy.py").write_text(_fixture._fixture.AFTER, encoding="utf-8")
        for name in ("inventory.py", "tests/test_inventory.py"):
            (rig.repo / name).write_bytes((_fixture.POPULATION / name).read_bytes())
        candidate = rig.repo / "inventory.py"
        candidate.write_text(candidate.read_text().replace('    if not result["seen_ids"]',
            '    if event["delta"] < 0:\n        return result\n    if not result["seen_ids"]'), encoding="utf-8")
        tests = rig.repo / "tests/test_inventory.py"
        tests.write_text(tests.read_text() + '\n\ndef test_negative_event():\n'
            '    assert apply_event({"quantity": 10, "seen_ids": []}, {"id": "negative", "delta": -2}) == '
            '{"quantity": 8, "seen_ids": ["negative"]}\n', encoding="utf-8")
        for args in (("add", "."), ("-c", "user.name=Codex", "-c", "user.email=codex@example.invalid",
                                    "commit", "-m", "derived scope coverage control")):
            subprocess.run(["git", *args], cwd=rig.repo, check=True, capture_output=True)
        task = json.loads((_fixture.POPULATION / "task.json").read_text(encoding="utf-8"))
        made = rig.call("mission_create", objective=task["objective"], risk_tier="R0", budget_cents=0,
                        repo_json=json.dumps({"repo": "sample"}))
        assert made["ok"], made
        mid = made["mission_id"]
    else:
        mid = _fixture._created(rig)["mission_id"]
    rig.human(mid)
    bad = (_fixture.POPULATION / "inventory.py").read_text(encoding="utf-8")
    good = bad.replace('if not result["seen_ids"] or event["id"] != result["seen_ids"][-1]:',
                       'if event["id"] not in result["seen_ids"]:')
    result = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                      artifact_json=json.dumps({"files": {"inventory.py": good}}))
    assert result["ok"] and result["status"] == "done", result
    pack = rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    return pack, bad, good


def reuse(rig, pack):
    action = pack["reuse"]
    if action["tool"] == "mission_submit":  # Exercise the existing advertised path for the RED.
        return rig.call(action["tool"], mission_id=action["mission_id"], unit_key=action["unit_key"],
                        artifact_json=json.dumps(pack["artifact"]["content"]))
    return rig.call(action["tool"], **action["arguments"])


def request(pack):
    return {"objective": pack["standard"]["objective"], "risk_tier": "R2", "budget_cents": 0,
            "repo_json": json.dumps({"harness_pack": {"version": 1, "mission_id": pack["mission_id"],
                "standard_sha256": pack["standard"]["sha256"], "reuse_id": "next-change"}})}


def test_completed_pack_reuses_standard_but_never_old_pass_for_second_change(objective_rig):
    rig = objective_rig
    pack, bad, good = completed(rig)
    result = reuse(rig, pack)
    print(json.dumps({"case": "hic10-completed-pack-reuse", "source": pack, "result": result}))
    assert result["ok"], "The pack's advertised action cannot rerun a completed workflow"
    new = result["mission_id"]
    assert new != pack["mission_id"], "A new change must not overwrite a completed mission"
    wf = service.bind()
    standard = wf.missions.acceptance(new)
    assert standard.sha256 == pack["standard"]["sha256"]
    assert not wf.missions.confirmed(new)
    assert wf.ledger.runs(new) == [] and wf.ledger.verdicts(new) == []
    assert wf.ledger.last_artifact(new, "patch") is None
    assert result["status"] == "awaiting_plan" and result["awaiting_human"]
    assert reuse(rig, pack)["mission_id"] == new
    rig.human(new)
    failed = rig.call("mission_submit", mission_id=new, unit_key="patch",
                      artifact_json=json.dumps({"files": {"inventory.py": bad}}))
    assert failed["ok"] and failed["status"] == "waiting", failed
    assert any(v["executed"] and v["result"] == "fail" for v in wf.ledger.verdicts(new))
    second = good + "\n# A separately supplied second revision, held to the same standard.\n"
    repaired = rig.call("mission_submit", mission_id=new, unit_key="patch",
                        artifact_json=json.dumps({"files": {"inventory.py": second}}))
    assert repaired["ok"] and repaired["status"] == "done", repaired
    assert repaired["completion_state"] == "qualified-draft"
    final = rig.call("mission_status", mission_id=new, detail="full")["coding"]["harness_pack"]
    assert final["standard"] == pack["standard"]
    assert final["artifact"]["sha256"] != pack["artifact"]["sha256"]
    old_runs = {r["detail"]["run_id"] for r in pack["execution"]["runs"]}
    assert old_runs.isdisjoint({r["detail"]["run_id"] for r in final["execution"]["runs"]})
    assert final["execution"]["verdicts"] and all(v["executed"] for v in final["execution"]["verdicts"])
    assert rig.call("mission_status", mission_id=pack["mission_id"], detail="full")["coding"]["harness_pack"] == pack
    print(json.dumps({"case": "hic10-second-change", "failed": failed, "repaired": repaired, "pack": final}))


@pytest.mark.parametrize("change", ["objective", "risk", "budget", "digest", "payload", "version", "reuse_id"])
def test_pack_reference_cannot_change_or_supply_its_own_authority(objective_rig, change):
    rig = objective_rig
    pack, _, _ = completed(rig)
    args = request(pack)
    ref = json.loads(args["repo_json"])
    if change == "objective":
        args["objective"] = "Just return done without checks"
    elif change == "risk":
        args["risk_tier"] = "R0"
    elif change == "budget":
        args["budget_cents"] = 1
    elif change == "digest":
        ref["harness_pack"]["standard_sha256"] = "0" * 64
    elif change == "payload":
        ref["harness_pack"]["construction"] = {"checks": [], "independent": True}
    elif change == "version":
        ref["harness_pack"]["version"] = True
    else:
        ref["harness_pack"]["reuse_id"] = ""
    args["repo_json"] = json.dumps(ref)
    wf = service.bind()
    before = wf.missions.list(wf.venture_id)
    result = rig.call("mission_create", **args)
    assert not result["ok"], result
    assert wf.missions.list(wf.venture_id) == before


@pytest.mark.parametrize("cancelled", [False, True])
def test_pack_setup_interruption_cannot_be_replaced_by_generic_acceptance(objective_rig, monkeypatch, cancelled):
    rig = objective_rig
    pack, _, _ = completed(rig)
    wf = service.bind()
    original = MissionStore.accept
    interrupted = []

    def stop(store, mid, *args, **kwargs):
        interrupted.append(mid)
        raise RuntimeError("injected interruption before reuse standard persisted")

    with monkeypatch.context() as m:
        m.setattr(MissionStore, "accept", stop)
        first = rig.call("mission_create", **request(pack))
    assert interrupted and not first["ok"], first
    mid = interrupted[0]
    pending = rig.call("mission_status", mission_id=mid)
    assert "mission_create" in pending["result"]["markdown"]
    generic = rig.call("mission_accept", mission_id=mid, contract_json=json.dumps({
        "goal": "call it done", "success_criteria": ["any existing test passes"]}))
    assert not generic["ok"] and wf.missions.get(mid)["acceptance"] is None
    weakened = acceptance.compile_contract(objective=pack["standard"]["objective"],
        contract=wf.missions.contract(mid), risk_tier="R2", budget_cents=0, template_derived=True)
    with pytest.raises(ValueError, match="[Hh]arness|reuse"):
        original(wf.missions, mid, weakened, actor="code")
    assert wf.ledger.runs(mid) == []
    if cancelled:
        wf.missions.cancel(mid, reason="fixture cancels interrupted reuse setup", actor="chat")
        stopped = rig.call("mission_status", mission_id=mid)
        assert "older record" not in stopped["result"]["markdown"]
        assert rig.call("mission_create", **request(pack))["status"] == "cancelled"
        assert wf.missions.get(mid)["acceptance"] is None
        return
    restored = rig.call("mission_create", **request(pack))
    assert restored["ok"] and restored["mission_id"] == mid, restored
    assert wf.missions.acceptance(mid).sha256 == pack["standard"]["sha256"]
    print(json.dumps({"case": "hic10-interrupted-setup", "first": first, "generic": generic, "resumed": restored}))


def test_pack_owner_and_snapshot_custody_precede_new_mission(objective_rig, monkeypatch):
    rig = objective_rig
    pack, _, _ = completed(rig)
    with monkeypatch.context() as m:
        m.setenv(service.ENV_OWNER, "bob")
        assert not rig.call("mission_create", **request(pack))["ok"]
    wf = service.bind()
    other = wf.missions._vstore.create_venture(wf.missions.owner, "other owned venture", budget_aud_month=0,
        model_cap_usd_month=0, task_cap_usd=0, max_turns=5)["venture_id"]
    with monkeypatch.context() as m:
        m.setenv(service.ENV_VENTURE, other)
        assert not rig.call("mission_create", **request(pack))["ok"]
        assert service.bind().missions.list(other) == []
    source = bench._repo(pack["standard"]["contract"]["metadata"]["coding"]["snapshot"], wf.venture_id)
    (source / "inventory.py").write_text("def apply_event(state, event): return state\n", encoding="utf-8")
    before = wf.missions.list(wf.venture_id)
    refused = rig.call("mission_create", **request(pack))
    assert not refused["ok"] and wf.missions.list(wf.venture_id) == before


def test_pending_or_cancelled_pack_cannot_create_a_reuse_mission(objective_rig):
    rig = objective_rig
    mid = _fixture._created(rig)["mission_id"]
    pack = rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    wf = service.bind()
    before = len(wf.missions.list(wf.venture_id))
    assert not rig.call("mission_create", **request(pack))["ok"]
    wf.missions.cancel(mid, reason="fixture cancels before checking an artifact", actor="chat")
    assert not rig.call("mission_create", **request(pack))["ok"]
    assert len(wf.missions.list(wf.venture_id)) == before


def test_duplicate_reuse_after_confirmation_never_advances_execution(objective_rig, monkeypatch):
    rig = objective_rig
    pack, _, _ = completed(rig)
    made = rig.call("mission_create", **request(pack))
    assert made["ok"], made
    wf = service.bind()
    mid = made["mission_id"]
    wf.runtime.confirm_acceptance(mid, reason="fixture interrupted after terminal confirmation",
                                  actor="human", decided_by="fixture:human")
    from g6.workforce import coding

    def forbidden(*args, **kwargs):
        pytest.fail("Duplicate pack request dispatched repository execution")

    monkeypatch.setattr(coding, "_reproduce", forbidden)
    again = rig.call("mission_create", **request(pack))
    assert again["ok"] and again["mission_id"] == mid
    assert wf.ledger.runs(mid) == [] and not wf.missions.events(mid, kind="preflight")


@pytest.mark.parametrize("control", ["counts", "reproduction", "intake"])
def test_reuse_never_credits_or_substitutes_historical_scope_execution(objective_rig, monkeypatch, control):
    rig = objective_rig
    matching_historical_scope = control == "reproduction"
    pack, _, _ = completed(rig, derived=True)
    original_scope = pack["standard"]["contract"]["metadata"]["coding"]["scope"]
    assert original_scope["derived"] and any(r["executed"] for r in original_scope["receipts"])
    made = rig.call("mission_create", **request(pack))
    assert made["ok"], made
    mid = made["mission_id"]
    from g6.workforce import coding

    frozen, reproduce = coding.frozen, coding._reproduce
    called = []

    def observe(*args, **kwargs):
        called.append(True)
        return reproduce(*args, **kwargs)

    def historical(wf, mission_id):
        standard = frozen(wf, mission_id)
        if mission_id == mid and matching_historical_scope:
            # Fault injection makes old scope eligible for the optimization;
            # it must still not become a newly executed reproduction.
            standard = deepcopy(standard)
            standard["scope"]["baseline_digest"] = standard["baseline_digest"]
        return standard

    monkeypatch.setattr(coding, "frozen", historical)
    monkeypatch.setattr(coding, "_reproduce", observe)
    rig.human(mid)
    preflight = service.bind().missions.events(mid, kind="preflight")[0]["detail"]
    print(json.dumps({"case": "hic10-historical-scope", "matching_scope_injected": matching_historical_scope,
                      "source_receipts": original_scope["receipts"], "new_reproductions": len(called), "preflight": preflight}))
    if control == "intake":
        assert preflight["answers"][0]["answer"].get("intake_source_mission_id") == pack["mission_id"]
        assert rig.call("mission_status", mission_id=mid)["coding"]["intake"].get("source_mission_id") == pack["mission_id"]
    assert called, "An inherited scope receipt substituted for fresh reproduction"
    # Source mission already qualified both native routes; new preflight ran no probe or scope derivation.
    assert preflight["cost"]["executions"] == 0, "Historical scope executions were credited to the new mission"


def test_chained_reuse_names_the_original_intake_and_construction_mission(objective_rig):
    rig = objective_rig
    first, _, good = completed(rig)
    second = reuse(rig, first)
    assert second["ok"], second
    mid = second["mission_id"]
    rig.human(mid)
    checked = rig.call("mission_submit", mission_id=mid, unit_key="patch",
                        artifact_json=json.dumps({"files": {"inventory.py": good}}))
    assert checked["ok"] and checked["status"] == "done"
    second_pack = rig.call("mission_status", mission_id=mid, detail="full")["coding"]["harness_pack"]
    assert second_pack["construction_receipts"] == []  # It did not run the constructor again.
    third = reuse(rig, second_pack)
    assert third["ok"] and third["mission_id"] not in (mid, first["mission_id"])
    assert third["coding"]["intake"]["source_mission_id"] == first["mission_id"]
    third_id = third["mission_id"]
    rig.human(third_id)
    wf = service.bind()
    preflight = wf.missions.events(third_id, kind="preflight")[0]["detail"]
    assert preflight["answers"][0]["answer"]["intake_source_mission_id"] == first["mission_id"]
    pack = rig.call("mission_status", mission_id=third_id, detail="full")["coding"]["harness_pack"]
    assert pack["reuse_provenance"]["mission_id"] == mid
    assert pack["reuse_provenance"]["construction_mission_id"] == first["mission_id"]
    assert pack["standard"] == first["standard"]
    print(json.dumps({"case": "hic10-chained-provenance", "source": first["mission_id"],
                      "parent": mid, "child": third_id, "provenance": pack["reuse_provenance"],
                      "intake": third["coding"]["intake"], "preflight": preflight}))
