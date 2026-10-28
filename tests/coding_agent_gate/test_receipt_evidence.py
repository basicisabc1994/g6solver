# SPDX-License-Identifier: Apache-2.0
"""The coding-agent gate counts an evidence item as G6-observed only when it cites a receipt id that G6's store holds
for the artifact the host bound the gate to (plan Step 2, amendment A6; review finding C1). The binding is a
constructor argument, set by the host (the patch review sets it from its own snapshot); the default instance, which
the registry and MCP doors build, has none, so there every receipt stays caller-declared, and a digest sent as JSON
is refused. The receipts here are written straight to the store (``receipt_store._record``) in place of a confined
run; tests/workforce/test_receipt_store.py writes them with real runs."""
from __future__ import annotations

import json
import uuid

import pytest
from returns.result import Success

from g6.coding_agent_gate import CodingAgentGateBlock, CodingAgentGateInput
from g6.core.compose import Stage, compose
from g6.core.door import door_reply
from g6.core.registry import get_registry
from g6.mcp import invoke_reply
from g6.workforce import receipt_store

ARTIFACT = "a1" * 32
TASK = {"task": "Fix add()", "answer": "Fixed add(); all tests pass.",
        "task_contract": "add(2, 3) == 5 and the existing tests stay green"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A G6_HOME of this test's own: the store holds only the receipts this test writes."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "g6-home"))


def _receipt(verdict: str = "passed", source_before: str = ARTIFACT, *, passed: int | None = None,
             not_passed: int = 0, completed: bool = True) -> str:
    passed = (2 if verdict == "passed" else 1) if passed is None else passed
    failed = 0 if verdict == "passed" else 1
    return receipt_store._record({
        "schema": "g6.test_receipt/1", "execution_id": uuid.uuid4().hex, "started_at": "2026-10-28T10:00:00.000+00:00",
        "finished_at": "2026-10-28T10:00:01.000+00:00", "g6_version": "0.1.1", "source_before": source_before,
        "source_after": source_before, "base_commit": "0" * 40,
        "executable": {"path": "/usr/bin/python3", "version": "3.13.5", "sha256": "b" * 64},
        "tests": {"requested": ["tests/test_calc.py"], "collected": ["tests/test_calc.py::test_add",
                                                                     "tests/test_calc.py::test_sub"],
                  "deselected": 0, "omitted": []},
        "command": "pytest 1 test id(s) in place", "isolation": {"backend": "bubblewrap", "why": "", "network": False},
        "result": {"verdict": verdict, "passed": passed, "failed": failed, "not_passed": not_passed,
                   "total": passed + failed, "failed_ids": [] if verdict == "passed" else ["tests/test_calc.py::test_add"],
                   "completed": completed, "degraded": not completed}})


def _bound(evidence: list[dict], op: str = "verify_answer", *, artifact: str = ARTIFACT, **params) -> dict:
    """The gate as a host builds it: bound to the artifact it reviews."""
    block = CodingAgentGateBlock(receipt_artifact_digest=artifact)
    return block.infer(CodingAgentGateInput(op=op, **{**TASK, "evidence": evidence, **params})).unwrap()


def _item(ident: str = "", **extra) -> dict:
    return {"kind": "test", "ref": "pytest -q", "exit_code": 0, **({"receipt": ident} if ident else {}), **extra}


def test_a_stored_receipt_for_the_bound_artifact_is_g6_observed_and_echoed():
    # Removing the gate's receipt check, or its host binding, fails here.
    ident = _receipt()
    seen = _bound([_item(ident)])
    assert (seen["verdict"], seen["evidence_basis"]) == ("pass", "g6-observed"), seen
    [counted] = seen["evidence_counted"]
    assert (counted["kind"], counted["ref"], counted["exit_code"], counted["observed_by"]) == ("test", ident, 0, "g6")
    echo = counted["receipt_run"]
    assert (echo["source_before"], echo["base_commit"], echo["requested"]) == (ARTIFACT, "0" * 40,
                                                                              ["tests/test_calc.py"])
    assert (echo["passed"], echo["failed"], echo["not_passed"], echo["total"]) == (2, 0, 0, 2)
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": "matched", "run": echo}]
    declared = _bound([_item()])
    assert (declared["verdict"], declared["evidence_basis"]) == ("revise", "caller-declared")
    assert declared["receipts"] == [{"receipt": "", "receipt_status": "not_supplied"}]


def test_at_the_registry_and_mcp_doors_a_genuine_receipt_stays_caller_declared():
    """C1: the door's instance has no binding, so a caller cannot pick the digest a receipt is checked against."""
    ident = _receipt()
    seen = get_registry().invoke("coding_agent_gate", "verify_answer", {**TASK, "evidence": [_item(ident)]}).unwrap()
    assert seen["evidence_basis"] == "caller-declared" and seen["verdict"] == "revise"
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": "not_honoured_at_door"}]
    reply = invoke_reply("coding_agent_gate", "verify_answer", json.dumps({**TASK, "evidence": [_item(ident)]}))
    assert reply["ok"] and reply["result"]["evidence_basis"] == "caller-declared", reply
    assert reply["result"]["receipts"] == [{"receipt": ident, "receipt_status": "not_honoured_at_door"}]
    smuggled = invoke_reply("coding_agent_gate", "verify_answer",
                            json.dumps({**TASK, "artifact_digest": ARTIFACT, "evidence": [_item(ident)]}))
    assert not smuggled["ok"] and smuggled["completion_state"] == "blocked-escalated"   # extra="forbid"
    with pytest.raises(ValueError):
        CodingAgentGateInput(op="verify_answer", artifact_digest=ARTIFACT, **TASK)


def test_the_matched_status_survives_the_composition_and_the_door():
    """I1: the door rewrites a value that reads "verified"; the status is "matched", which it leaves alone."""
    ident = _receipt()
    flow = compose(Stage(CodingAgentGateBlock(receipt_artifact_digest=ARTIFACT), "verify_answer"),
                   caller_inputs=("task", "answer", "task_contract", "evidence"))
    composed = flow.infer({**TASK, "evidence": [_item(ident)]}).value
    assert composed["result"]["receipts"][0]["receipt_status"] == "matched"
    replied = door_reply("coding_agent_gate", "verify_answer", Success(_bound([_item(ident)])),
                         caller={**TASK, "evidence": [_item(ident)]})
    assert replied["result"]["receipts"][0]["receipt_status"] == "matched"


def test_the_receipt_decides_the_item_and_the_callers_own_fields_are_ignored():
    ident = _receipt("failed")
    seen = _bound([{"kind": "log", "ref": "all green", "output": "2 passed", "exit_code": 0, "receipt": ident}])
    [counted] = seen["evidence_counted"]
    assert (counted["kind"], counted["ref"], counted["exit_code"], counted["observed_by"]) == ("test", ident, 1, "g6")
    assert seen["verdict"] == "revise" and "test_failed" in {f["code"] for f in seen["findings"]}


@pytest.mark.parametrize("receipt", [
    {"passed": 1, "not_passed": 50},   # 1 passed, 50 skipped
    {"passed": 0},                     # nothing ran
    {"completed": False},              # stopped part-way
])
def test_a_receipt_counts_as_a_pass_only_when_every_test_ran_and_passed(receipt):
    """I3: the same rule as the patch review's: passed, total > 0, nothing skipped, completed, not degraded."""
    ident = _receipt(**receipt)
    seen = _bound([_item(ident)])
    [counted] = seen["evidence_counted"]
    assert counted["observed_by"] == "g6" and counted["exit_code"] == 1, seen
    assert seen["verdict"] != "pass"
    assert counted["receipt_run"]["not_passed"] == receipt.get("not_passed", 0)


@pytest.mark.parametrize(("cited", "artifact", "status"), [
    ("forged", ARTIFACT, "unknown"),           # an id the store does not hold
    ("real", "b2" * 32, "artifact_mismatch"),  # a real receipt, bound to another tree
])
def test_a_receipt_the_store_does_not_hold_for_the_bound_artifact_stays_caller_declared(cited, artifact, status):
    ident = _receipt() if cited == "real" else "f" * 64
    seen = _bound([_item(ident)], artifact=artifact)
    assert seen["evidence_basis"] == "caller-declared" and seen["verdict"] == "revise"
    [counted] = seen["evidence_counted"]
    assert (counted["ref"], counted["observed_by"]) == ("pytest -q", "caller")
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": status}]


def test_an_unreadable_store_says_so():
    """M1: a torn last line leaves the store unreadable, and the status names that, not "unknown"."""
    ident = _receipt()
    path = receipt_store.store_path()
    path.write_text(path.read_text(encoding="utf-8") + '{"seq": 1, "kind": "test-rec', encoding="utf-8")
    seen = _bound([_item(ident)])
    assert seen["receipts"] == [{"receipt": ident, "receipt_status": "store_unreadable"}]
    assert seen["evidence_basis"] == "caller-declared"


def test_preflight_reads_receipts_the_same_way():
    ident = _receipt()
    seen = _bound([_item(ident)], op="preflight", proposed_action="merge the fix")
    assert seen["evidence_basis"] == "g6-observed"
    assert seen["receipts"][0]["receipt_status"] == "matched"


@pytest.mark.parametrize("item", [
    {"kind": "test", "ref": "pytest", "exit_code": 0, "observed_by": "g6"},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "digest": ARTIFACT},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "source_before": ARTIFACT},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "receipt": {"schema": "g6.test_receipt/1",
                                                                  "source_before": ARTIFACT}},
    {"kind": "test", "ref": "pytest", "exit_code": 0, "receipt_status": "matched"},
])
def test_a_caller_cannot_send_g6_provenance_or_a_receipts_body(item):
    reply = invoke_reply("coding_agent_gate", "verify_answer", json.dumps({**TASK, "evidence": [item]}))
    assert not reply["ok"] and reply["completion_state"] == "blocked-escalated"


def test_a_receipts_body_sent_as_its_id_is_not_a_receipt():
    body = {"schema": "g6.test_receipt/1", "source_before": ARTIFACT, "result": {"verdict": "passed"}}
    for ident in (json.dumps(body), receipt_store.receipt_id(body)):
        seen = _bound([_item(ident)])
        assert seen["evidence_basis"] == "caller-declared"
        assert seen["receipts"] == [{"receipt": ident, "receipt_status": "unknown"}]
