# SPDX-License-Identifier: Apache-2.0
"""Audit 019 (poka_yoke): its claim/evidence checks moved into coding_agent_gate, with the audit's fixes."""
from __future__ import annotations

import os
import pathlib

import pytest

import g6.coding_agent_gate as cag
from g6.coding_agent_gate import SEVERITY, Evidence, preflight, stricter_of, surface_state, verify_answer
from g6.coding_agent_gate.gate import VERDICT_RANK, strictest
from g6.core.registry import get_registry
from g6.core.storage import g6_home

TC = "the report totals match the ledger"
FILE = Evidence("file", "src/report.py")
RUN = Evidence("test", "pytest -q tests/test_report.py", exit_code=0)


def test_py004_claim_needs_counted_evidence_not_a_key():
    """{"claim": "tests pass", "evidence": ""} and "trust me" passed as evidence in the archive."""
    for weak in (Evidence("log", "evidence", output=""), Evidence("file", "trust me"), Evidence("test", "")):
        d = verify_answer("Fix totals", "Totals fixed.", claims=["report.py sums the ledger"], task_contract=TC,
                          evidence=[weak])
        assert d.verdict == "revise" and {"no_evidence", "claims_without_files"} <= {f.code for f in d.findings}


def test_py002_nothing_checked_is_never_a_pass_and_nothing_is_ever_verified():
    d = verify_answer("Fix totals", "Totals fixed.", task_contract=TC)
    assert d.verdict == "revise" and d.evidence_basis == "none"
    for op, ranks in VERDICT_RANK.items():
        for verdict in [*ranks, "", "allow", "verified"]:
            assert surface_state(verdict) != "verified", (op, verdict)
    assert surface_state("verified") == "blocked-escalated"    # an unknown verdict fails closed
    out = get_registry().invoke("coding_agent_gate", "preflight", {"task": "Tidy the docs"}).unwrap()
    assert out["completion_state"] == "qualified-draft" and out["verdict"] == "needs_evidence"


def test_py006_the_severity_table_drives_the_verdict(monkeypatch):
    failing = Evidence("test", "pytest -q", exit_code=1)
    d = verify_answer("Fix totals", "Totals fixed.", task_contract=TC, evidence=[FILE, failing])
    assert d.verdict == strictest("verify_answer", d.findings) == "revise"
    assert {f.code for f in d.findings} <= set(SEVERITY)
    monkeypatch.setitem(SEVERITY, "test_failed", "block")
    assert verify_answer("Fix totals", "Totals fixed.", task_contract=TC, evidence=[FILE, failing]).verdict == "block"
    assert stricter_of("revise", "pass", vocabulary="verify_answer") == "revise"
    assert stricter_of("revise", "nonsense", vocabulary="verify_answer") == "revise"


def test_py008_mode_is_a_parameter_never_the_environment(monkeypatch):
    args = ("Fix totals", "Totals fixed.")
    kw = {"evidence": [FILE, RUN]}
    before = verify_answer(*args, **kw).to_dict()
    for name, value in (("G6_CODING_AGENT_GATE_CONTRACT", "off"), ("G6_CODING_AGENT_GATE_AGENTIC_RUNTIME", "1"),
                        ("G6_POKA_YOKE_SHADOW", "1"), ("G6_POKA_YOKE_DISABLED", "1")):
        monkeypatch.setenv(name, value)
    assert verify_answer(*args, **kw).to_dict() == before and before["verdict"] == "block"
    assert verify_answer(*args, require_task_contract=False, **kw).verdict == "revise"


def test_py010_no_audit_file_is_written(tmp_path):
    def tree() -> set[str]:
        return {str(p) for p in g6_home().rglob("*")} if g6_home().exists() else set()

    before = tree()
    for _ in range(20):
        verify_answer("Fix totals", "Totals fixed.", task_contract=TC, evidence=[FILE, RUN]).to_envelope()
        preflight("Deploy to production", proposed_action="force push")
        get_registry().invoke("coding_agent_gate", "preflight", {"task": "Tidy the docs"})
    assert tree() == before
    for path in pathlib.Path(cag.__file__).parent.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert not any(w in src for w in ("open(", "write_text", "open_db", "state_path", "os.environ")), path.name


@pytest.mark.parametrize("name", ["claude", "Claude Opus", "CLAUDE  opus"])
def test_self_validation_is_not_review(name):
    review = Evidence("review", "PR 12", reviewer=name)
    assert review.problem(author="Claude Opus") == "the reviewer is the author; a self-review does not count"
    assert Evidence("review", "PR 12", reviewer="Dana").problem(author="Claude Opus") == ""
    assert os.environ.get("G6_HOME")   # the conftest's throwaway home is in force
