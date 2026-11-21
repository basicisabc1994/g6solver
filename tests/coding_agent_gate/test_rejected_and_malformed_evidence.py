# SPDX-License-Identifier: Apache-2.0
"""Review 1 of wire-clarity: a rejected item never supplies a gate pass, an exit code is an int and never a bool,
and a path field G6 cannot resolve is refused at the door as input, not raised. Synthetic receipt store only."""
import json

import pytest

from g6.coding_agent_gate import Evidence, verify_answer
from g6.mcp import invoke_reply
from g6.workforce import receipt_store

TASK = dict(task="Fix addition", answer="Changed addition.", task_contract="Addition returns the sum")
FAILED = {"result": {"verdict": "failed", "passed": 0, "failed": 1, "not_passed": 1, "total": 1,
                     "completed": True, "degraded": False}}


def _store(monkeypatch, body=FAILED, passes=False):
    monkeypatch.setattr(receipt_store, "check", lambda *args, **kwargs: ("matched", body))
    monkeypatch.setattr(receipt_store, "counts_as_pass", lambda _body: passes)


def _receipt(exit_code, ref="r-1"):
    return Evidence("test", ref, exit_code=exit_code, receipt="r-1", artifact_digest="host-bound")


def test_a_rejected_failed_receipt_cannot_supply_a_gate_pass(monkeypatch):
    _store(monkeypatch)
    decision = verify_answer(**TASK, evidence=[Evidence("file", "src/sum.py"), _receipt(True)])
    assert decision.rejected and not any(item.kind == "test" for item in decision.counted)
    assert decision.verdict == "report_consistent"


@pytest.mark.parametrize(("exit_code", "passes"), [(True, False), (False, True), (1.0, False), (0.0, True)])
def test_an_exit_code_that_is_not_an_int_never_matches_a_receipt(monkeypatch, exit_code, passes):
    _store(monkeypatch, passes=passes)
    assert _receipt(exit_code).evidence_status == "asserted"
    assert _receipt(1 if not passes else 0).evidence_status == "observed_by_g6"


def test_a_pass_needs_a_counted_observed_test_and_no_rejected_one(monkeypatch):
    _store(monkeypatch, passes=True)
    observed = _receipt(0)
    assert verify_answer(**TASK, evidence=[Evidence("file", "src/sum.py"), observed]).verdict == "pass"
    assert verify_answer(**TASK, evidence=[Evidence("file", "src/sum.py")]).verdict == "report_consistent"
    mixed = verify_answer(**TASK, evidence=[observed, Evidence("test", "pytest -q", exit_code=True)])
    assert mixed.rejected and mixed.verdict == "report_consistent"


BAD = ["bad\x00name", "bad\ud800name"]


@pytest.mark.parametrize("bad", BAD, ids=["nul", "lone-surrogate"])
@pytest.mark.parametrize("field", ["repo_root", "file_ref", "test_ref", "touched_paths"])
def test_a_path_g6_cannot_resolve_is_an_input_refusal_at_the_door(monkeypatch, field, bad):
    from g6.core import door

    monkeypatch.setattr(door, "person_model", lambda: None)
    params = {**TASK, "repo_root": ".", "evidence": [{"kind": "file", "ref": "src/g6/__init__.py"}]}
    if field == "repo_root":
        params["repo_root"] = bad
    elif field == "file_ref":
        params["evidence"][0]["ref"] = bad + ".py"
    elif field == "test_ref":
        params["evidence"].append({"kind": "test", "ref": f"pytest -q {bad}.py", "exit_code": 0})
    else:
        params["touched_paths"] = [bad]
    reply = invoke_reply("coding_agent_gate", "verify_answer", json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"


@pytest.mark.parametrize("bad", BAD, ids=["nul", "lone-surrogate"])
def test_in_python_an_unresolvable_ref_is_missing_and_an_unresolvable_root_is_refused(tmp_path, bad):
    found = verify_answer(**TASK, evidence=[Evidence("file", bad + ".py")], repo_root=str(tmp_path))
    assert found.verdict == "revise" and "EVIDENCE_REF_MISSING" in {f.code for f in found.findings}
    with pytest.raises(ValueError, match="repo_root"):
        verify_answer(**TASK, evidence=[Evidence("file", "a.py")], repo_root=bad)
