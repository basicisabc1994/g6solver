# SPDX-License-Identifier: Apache-2.0
"""Repository objectives enter through the served tools; the worker only submits files."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from g6.core import confine
from g6.workforce.store import VentureStore
from g6.workforce import decide, service

_tools_spec = importlib.util.spec_from_file_location("workforce_tools_fixture", Path(__file__).with_name("_tools.py"))
_tools = importlib.util.module_from_spec(_tools_spec)
_tools_spec.loader.exec_module(_tools)

OBJECTIVE = "Fix the label beside the acceptance rule and preserve its behavior."
BEFORE = 'def accepts(value):\n    return value >= 3\n\n\ndef label():\n    return "wrong"\n'
AFTER = BEFORE.replace('"wrong"', '"ready"')
BOUNDARY = "tests/test_boundary.py::test_boundary"
OBJECTIVE_CHECK = "tests/test_objective.py::test_label"


@pytest.fixture
def objective_rig(tmp_path, monkeypatch):
    store = VentureStore(tmp_path / "venture.db")
    venture = store.create_venture("alice", "synthetic", budget_aud_month=0, model_cap_usd_month=0,
                                   task_cap_usd=0, max_turns=5)["venture_id"]
    repo = tmp_path / "repos" / venture / "sample"
    (repo / "tests").mkdir(parents=True)
    (repo / "policy.py").write_text(BEFORE, encoding="utf-8")
    (repo / "tests/test_boundary.py").write_text(
        "from policy import accepts\n\ndef test_boundary():\n    assert accepts(3) is True\n"
        "    assert accepts(2) is False\n", encoding="utf-8")
    (repo / "tests/test_objective.py").write_text(
        'from policy import label\n\ndef test_label():\n    assert label() == "ready"\n', encoding="utf-8")
    for args in (("init",), ("add", "."), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                                         "commit", "-m", "synthetic")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    baseline = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    monkeypatch.setenv(service.ENV_DB_DIR, str(tmp_path))
    monkeypatch.setenv(service.ENV_VENTURE, venture)
    monkeypatch.setenv(service.ENV_OWNER, "alice")
    def call(name, **arguments):  # the archive's served tool, as the library's local call (same JSON answer)
        if name == "mission_create":
            allow(arguments)
        return _tools.call(name, **arguments)

    def allow(arguments):  # audit 010 WF-007: the person first allows G6 to run the repository's present code
        try:
            repo = json.loads(arguments.get("repo_json") or "{}").get("repo")
        except (ValueError, AttributeError):
            return
        if isinstance(repo, str):
            with monkeypatch.context() as actor:
                actor.setattr(decide, "_interactive", lambda: True)
                decide.main(["allow", repo, "--reason", "synthetic permission to run the repository's code"],
                            ask=lambda _: repo)

    def human(mid):
        with monkeypatch.context() as actor:
            actor.setattr(decide, "_interactive", lambda: True)
            assert decide.main(["confirm", mid, "--reason", "synthetic human confirmation"],
                               ask=lambda _: mid) == 0

    yield SimpleNamespace(call=call, human=human, repo=repo, baseline=baseline, db=tmp_path / "venture.db")


def _create(rig):
    found = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0,
                     # lead ruling, E2E Brief 2: the named function is the one the objective changes
                     repo_json=json.dumps({"repo": "sample", "module": "policy.py", "function": "label"}))
    assert found["ok"], found
    return found


def _status(rig, mid):
    found = rig.call("mission_status", mission_id=mid, detail="full")
    assert found["ok"], found
    return found


def _submit(rig, mid):
    brief = _status(rig, mid)["awaiting_chat"]
    assert len(brief) == 1, brief
    return rig.call("mission_submit", mission_id=mid, unit_key=brief[0]["unit"],
                    artifact_json=json.dumps({"files": {"policy.py": AFTER}}))


def test_objective_enters_workforce_with_frozen_production_standard(objective_rig):
    rig = objective_rig
    created = _create(rig)
    assert created["risk_tier"] == "R2"
    found = _status(rig, created["mission_id"])
    frozen = found["acceptance"]["metadata"]["coding"]
    assert frozen["baseline_revision"] == rig.baseline
    assert frozen["required_tests"] == [BOUNDARY, OBJECTIVE_CHECK]
    assert frozen["test_provenance"] == "caller"
    assert frozen["permissions"] == {"files": ["policy.py"], "test_edits": False, "network": False}
    assert frozen["budget_cents"] == 0 and frozen["max_attempts"] == 3
    assert frozen["derivation"] == {"policy_version": "coding-production-v1", "declared_risk": "R0",
                                    "floor": "R2", "effective_risk": "R2"}
    signature = frozen["task_signature"]
    assert signature["task_kind"] == "code_change"
    assert signature["module"] == "policy.py" and signature["function"] == "label"
    assert len(signature["repository_identity"]) == len(signature["sha256"]) == 64
    for relative, digest in frozen["test_digests"].items():
        assert digest == hashlib.sha256((rig.repo / relative).read_bytes()).hexdigest()
    assert found["acceptance"]["metadata"]["spec_complete"] is True
    assert found["acceptance"]["requirements"] == [
        {"name": node, "predicate": "tests_passed", "params": {"unit": "patch"}, "problem": ""}
        for node in frozen["required_tests"]]
    assert found["coding"]["intake"]["model_calls"] == 0
    assert found["coding"]["intake"]["components_used"] == ["intent_engine", "feasibility"]
    assert found["units"] == [] and found["awaiting_chat"] == []


def test_production_contract_waits_for_human_then_resumes(objective_rig):
    rig = objective_rig
    created = _create(rig)
    mid = created["mission_id"]
    before = _status(rig, mid)
    assert before["awaiting_chat"] == [] and before["runs"] == []
    assert [q["kind"] for q in before["awaiting_human"]] == ["acceptance"]
    digest = before["acceptance"]["sha256"]
    rig.human(mid)
    after = _status(rig, mid)
    assert len(after["awaiting_chat"]) == 1
    assert after["acceptance"]["sha256"] == digest
    assert after["awaiting_human"] == [] and after["schedule"]
    # Store inspection is an observer, never the client or the simulated human.
    wf = service.bind()
    intervention = wf.runtime.decisions.interventions(mid)[0]
    assert intervention.decided_by == "terminal:alice"
    assert intervention.metadata["acceptance_sha256"] == digest
    assert wf.missions.contract(mid).declined_clarification is False
    # lead ruling, Build 5: R5 measures the person's time at the terminal
    assert isinstance(after["human_attention"]["seconds"], int) and after["human_attention"]["seconds"] >= 0
    assert after["human_attention"]["unmeasured"] == 0 and intervention.duration_source == "measured"


def test_objective_submission_runs_checks_without_client_orchestration(objective_rig):
    rig = objective_rig
    mid = _create(rig)["mission_id"]
    rig.human(mid)
    before = _status(rig, mid)
    reproduction = before["coding"]["reproduction"]
    assert reproduction["reproduced"] is True
    assert reproduction["tests"] == {BOUNDARY: True, OBJECTIVE_CHECK: False}
    assert all(r["executed"] and r["execution_id"] for r in reproduction["receipts"])
    assert reproduction["receipts"][1]["returncode"] == 1
    brief = before["awaiting_chat"][0]
    assert brief["attempts_left"] == 3 and brief["output_schema"] == "files"
    assert brief["coding"]["reproduction"] == reproduction
    # The caller's workspace can move; checks must still use the frozen baseline and tests.
    (rig.repo / "tests/test_objective.py").write_text("def test_label():\n    assert False\n", encoding="utf-8")
    done = _submit(rig, mid)
    assert done["ok"] and done["status"] == "done", done
    final = _status(rig, mid)
    assert final["acceptance"] == before["acceptance"]
    assert final["coding"]["reproduction"] == reproduction
    wf = service.bind()
    artifact = wf.ledger.last_artifact(mid, brief["unit"])
    verdicts = wf.ledger.verdicts(mid)
    assert verdicts and all(v["executed"] and v["result"] == "pass" for v in verdicts)
    assert all(v["artifact_id"] == artifact["artifact_id"] and v["artifact_sha"] == artifact["sha256"]
               for v in verdicts)
    receipts = final["coding"]["checks"]
    assert receipts and receipts[0]["artifact_id"] == artifact["artifact_id"]
    assert next(r for r in receipts if r["check"] == "check-patch")["measured"] == {"passed": 2, "total": 2}
    assert (rig.repo / "policy.py").read_text(encoding="utf-8") == BEFORE
    assert wf.ledger.cost_total(mid).amount_minor_units is None


def test_caller_authored_tests_cannot_earn_verified(objective_rig):
    rig = objective_rig
    mid = _create(rig)["mission_id"]
    rig.human(mid)
    done = _submit(rig, mid)
    assert done["status"] == "done", done
    wf = service.bind()
    verdicts = wf.ledger.verdicts(mid)
    assert verdicts and all(v["result"] == "pass" and v["executed"] and v["model_free"] for v in verdicts)
    # A4 when the bench's tests ran confined by the operating system; held at A3 when they ran as the owner
    confined = confine.backend(sys.executable)[0] is not None
    assert all(v["authority"] == ("A4" if confined else "A3") for v in verdicts if v["unit_key"] == "check-patch")
    assert all(v["authority"] == "A0" for v in verdicts if v["unit_key"] == "check-regression")
    assert all(v["state"] == "qualified-draft" and not v["independent"] for v in verdicts)
    assert done["completion_state"] == "qualified-draft"
    assert _status(rig, mid)["completion_state"] == "qualified-draft"


def test_legacy_mission_create_keeps_manual_behavior(objective_rig):
    rig = objective_rig
    created = rig.call("mission_create", objective=OBJECTIVE, risk_tier="R0", budget_cents=0)
    assert created["ok"] and created["risk_tier"] == "R0" and created["status"] == "eliciting"
    found = _status(rig, created["mission_id"])
    assert found["plan_revision"] == 0 and found["completion_state"] == ""
    assert found["units"] == found["runs"] == found["awaiting_chat"] == found["awaiting_human"] == []
    assert "coding" not in found and "coding" not in found["acceptance"]["metadata"]


@pytest.fixture(autouse=True)
def _legacy_without_certificates(monkeypatch):
    """S0 (2026-10-16): certificates are on by default now, and a verified certificate lifts a clean mission to verified (founder R6-P). These tests were written for the close without
    certificates; this pins that, and every assertion is unchanged."""
    monkeypatch.setenv("G6_WORKFORCE_CERTIFICATES", "0")
