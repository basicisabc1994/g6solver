# SPDX-License-Identifier: Apache-2.0
"""F2: a plausible caller report must never acquire observed-test authority."""
import json
from pathlib import Path

import pytest

from g6.coding_agent_gate import CodingAgentGateBlock, CodingAgentGateInput, Evidence, verify_answer
from g6.mcp import invoke_reply
from g6.workforce import receipt_store

TASK = {"task": "Fix the calculation", "answer": "Changed the calculation.",
        "task_contract": "The calculation returns the sum"}
PROBE = [{"kind": "file", "ref": "/nonexistent/does_not_exist.py"},
         {"kind": "test", "ref": "pytest -q tests/no_such_test.py", "exit_code": 0, "output": "3 passed"}]


def call(items=PROBE, **kwargs):
    return CodingAgentGateBlock().infer(CodingAgentGateInput(**TASK, evidence=items, **kwargs)).unwrap()


def test_exact_audit_probe_cannot_pass_at_mcp():
    reply = invoke_reply("coding_agent_gate", "verify_answer", json.dumps({**TASK, "evidence": PROBE}))
    assert reply["result"]["verdict"] == "report_consistent"
    assert reply["result"]["evidence_status"] == "asserted"
    assert "tests passed" not in json.dumps(reply).lower()
    assert all(item["evidence_status"] == "asserted" for item in reply["result"]["evidence_counted"])


def test_exact_audit_probe_with_root_requires_revision(tmp_path):
    out = call(repo_root=str(tmp_path))
    assert out["verdict"] == "revise"
    assert out["evidence_status"] == "asserted"
    assert "EVIDENCE_REF_MISSING" in {f["code"] for f in out["findings"]}


@pytest.mark.parametrize("ref", ["../outside.py", "/nonexistent/does_not_exist.py", "missing.py"])
def test_file_refs_must_resolve_inside_root(tmp_path, ref):
    out = call([{"kind": "file", "ref": ref}], repo_root=str(tmp_path))
    assert out["verdict"] == "revise"
    assert "EVIDENCE_REF_MISSING" in {f["code"] for f in out["findings"]}


def test_existing_file_is_still_asserted_and_next_call_names_the_test(tmp_path):
    (tmp_path / "test_sum.py").write_text("def test_sum():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    out = call([{"kind": "test", "ref": "pytest -q test_sum.py::test_sum", "exit_code": 0}],
               repo_root=str(tmp_path))
    assert out["verdict"] == "report_consistent"
    assert out["evidence_status"] == "asserted"
    assert out["next_actions"][0] == {"tool": "g6_review_patch", "arguments": {
        "repo": str(tmp_path), "task": TASK["task"], "requirements": [
            {"id": "R1", "text": TASK["task_contract"], "checks": ["test_sum.py::test_sum"]}],
        "tests": ["test_sum.py::test_sum"]}}


def test_no_root_explicitly_says_references_were_not_checked():
    out = call()
    assert out["references_checked"] is False
    assert "not checked" in json.dumps(out).lower()
    assert "G6 did not run it" in json.dumps(out)
    assert "the agent reports 3 passed" in json.dumps(out)


def test_a_python_observed_by_flag_alone_is_not_a_receipt():
    out = verify_answer(**TASK, evidence=[Evidence("test", "pytest -q", exit_code=0, observed_by="g6")])
    assert out.verdict == "report_consistent"
    assert out.to_dict()["evidence_status"] == "asserted"


def test_stored_receipt_status_and_weakest_item(tmp_path, monkeypatch):
    """Store plumbing only; this fixture does not establish real OS confinement."""
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    digest = "a1" * 32
    ident = receipt_store._record({"schema": "g6.test_receipt/1", "source_before": digest,
        "source_after": digest, "tests": {"requested": ["tests/test_sum.py"]},
        "result": {"verdict": "passed", "passed": 1, "failed": 0, "not_passed": 0, "total": 1,
                   "completed": True, "degraded": False}, "isolation": {"backend": "fixture"}})
    block = CodingAgentGateBlock(receipt_artifact_digest=digest)
    evidence = [{"kind": "test", "ref": "pytest -q", "exit_code": 0, "receipt": ident}]
    out = block.infer(CodingAgentGateInput(**TASK, evidence=evidence)).unwrap()
    assert (out["verdict"], out["evidence_status"]) == ("pass", "observed_by_g6")
    assert out["evidence_counted"][0]["evidence_status"] == "observed_by_g6"
    mixed = block.infer(CodingAgentGateInput(**TASK, evidence=[*evidence, PROBE[0]])).unwrap()
    assert mixed["evidence_status"] == "asserted"
    assert mixed["verdict"] == "pass"  # every test observed; the file assertion remains separately labelled
    for receipt, bound in [("0" * 64, digest), (ident, "b2" * 32)]:
        forged = CodingAgentGateBlock(receipt_artifact_digest=bound).infer(
            CodingAgentGateInput(**TASK, evidence=[{**evidence[0], "receipt": receipt}])).unwrap()
        assert (forged["verdict"], forged["evidence_status"]) == ("report_consistent", "asserted")


def test_no_evidence_and_declared_failure_keep_their_floor():
    assert call([])["verdict"] == "revise"
    assert call([{**PROBE[1], "exit_code": 1}])["verdict"] == "revise"


def test_audit_probe_against_the_existing_worktree():
    out = call(repo_root=str(Path(__file__).resolve().parents[2]))
    assert out["verdict"] == "revise"
    assert "EVIDENCE_REF_MISSING" in {f["code"] for f in out["findings"]}


def test_mixed_observed_and_asserted_test_items_cannot_pass(monkeypatch):
    """Synthetic authority seam only, not a confined-run acceptance test."""
    monkeypatch.setattr(receipt_store, "check", lambda *args, **kwargs: ("matched", {"fixture": True}))
    monkeypatch.setattr(receipt_store, "counts_as_pass", lambda body: True)
    observed = Evidence("test", "receipt-id", exit_code=0, receipt="receipt-id", artifact_digest="bound")
    for items, verdict, status in [([observed], "pass", "observed_by_g6"),
                                    ([observed, Evidence("test", "pytest -q", exit_code=0)],
                                     "report_consistent", "asserted")]:
        out = verify_answer(**TASK, evidence=items).to_dict()
        assert (out["verdict"], out["evidence_status"]) == (verdict, status)


@pytest.mark.parametrize("ref,paths,missing", [
    ("src/sum.py", ["src/sum.py"], False), ("src/sum.py", [], True),
    ("../src/sum.py", ["../src/sum.py"], True), ("C:/outside.py", ["C:/outside.py"], True),
    ("pytest -q tests/missing.py::test_sum", ["src/sum.py"], True),
    ('pytest -q "tests/test sum.py::test_sum"', ["tests/test sum.py"], False),
])
def test_path_lists_check_membership_without_observing_a_run(ref, paths, missing):
    item = {"kind": "test", "ref": ref, "exit_code": 0} if ref.startswith("pytest") else {"kind": "file", "ref": ref}
    out = call([item], touched_paths=paths)
    assert out["references_checked"] is True
    assert out["evidence_status"] == "asserted"
    assert ("EVIDENCE_REF_MISSING" in {f["code"] for f in out["findings"]}) is missing


@pytest.mark.parametrize("command", ["pytest tests/no_such.py; echo done", 'pytest "tests/no_such.py'])
def test_missing_or_unparseable_test_references_fail_closed_with_scope(command):
    out = call([{"kind": "test", "ref": command, "exit_code": 0}],
               repo_root=str(Path(__file__).resolve().parents[2]))
    assert "EVIDENCE_REF_MISSING" in {f["code"] for f in out["findings"]}


def test_a_failed_asserted_run_is_never_reported_as_a_passing_count():
    out = call([{"kind": "test", "ref": "pytest -q", "exit_code": 1, "output": "1 failed"}])
    assert "passed" not in out["evidence_counted"][0]["report"]
    assert "exit 1" in out["evidence_counted"][0]["report"]
