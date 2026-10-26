# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""ECHA Checkpoint 1: the evaluator-only access boundary (ECHA-10; founder decision A2) and the end-to-end evidence
trace ("a user or reviewer can trace any local claim to its exact evidence and standard").

The boundary is a software check on the research harness's read path. It is not an operating-system sandbox, so a result
that depends on it is recorded as ``unblinded``: the test says so, and it is the honest label until a real separation
exists. What must hold, each with a case that can fail:
- a builder, learner or reviewer who reads a sealed fixture, hidden test, withheld fix or evaluator transcript is refused,
  an incident is recorded without the path's text, and every piece of evidence taken against that fixture is invalid;
- the evaluator can read what it holds, and a path outside the sealed roots is not blocked (the control: the check
  depends on what was sealed, and a look-alike sibling directory is not caught by a prefix);
- a fixture changed after it was sealed is found and counts as compromised;
- the trace of a claim lists its standard, its evidence, each negative control and the ledger's integrity, and a tampered
  ledger shows in it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from g6.research.echa import (
    FLAG,
    AccessDenied,
    ClaimRecord,
    Ledger,
    StandardRef,
    Vault,
    blinding_status,
    evaluate_claim,
    guarded_read,
    trace_claim,
)
from g6.research.echa import example as kernel_example
from g6.research.echa.records import EvidenceRecord

STD = StandardRef.from_text("std-keep", "A change must not alter a function it was not asked to alter.", "owner doc",
                            ("p-keep",))


@pytest.fixture
def led(monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    ledger = Ledger()
    ledger.add_standard(STD)
    return ledger


@pytest.fixture
def world(tmp_path):
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "test_hidden.py").write_bytes(b"assert helper(3) == 4\n")
    (hidden / "fix.diff").write_bytes(b"the withheld fix\n")
    transcripts = tmp_path / "evaluator_transcripts"
    transcripts.mkdir()
    (transcripts / "review.txt").write_bytes(b"the reviewer's reasoning\n")
    sibling = tmp_path / "hidden2"
    sibling.mkdir()
    (sibling / "notes.txt").write_bytes(b"not sealed\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "module.py").write_bytes(b"def helper(x):\n    return x\n")
    return tmp_path


def _seal(led, world):
    return Vault.seal([world / "hidden", world / "evaluator_transcripts"], ledger=led, vault_id="vault-1")


# --- ECHA-10 -----------------------------------------------------------------------------------------------------------


def test_sealing_records_a_manifest_digest_that_changes_with_any_file(led, world) -> None:
    first = _seal(led, world)
    assert len(first.digest) == 64
    assert led.get("vault", "vault-1")["digest"] == first.digest
    (world / "hidden" / "fix.diff").write_text("a different fix\n", encoding="utf-8")
    again = Vault.seal([world / "hidden", world / "evaluator_transcripts"], ledger=led, vault_id="vault-2")
    assert again.digest != first.digest


@pytest.mark.parametrize("role", ["builder", "learner", "reviewer"])
@pytest.mark.parametrize("target", [("hidden", "test_hidden.py"), ("hidden", "fix.diff"),
                                    ("evaluator_transcripts", "review.txt")])
def test_echa_10_a_builder_learner_or_reviewer_cannot_read_a_sealed_fixture(led, world, role, target) -> None:
    vault = _seal(led, world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, role, world.joinpath(*target), ledger=led)
    incidents = [e for e in led.entries() if e["kind"] == "incident"]
    assert len(incidents) == 1
    body = incidents[0]["body"]
    assert body["type"] == "access_denied" and body["role"] == role and body["fixture_digest"] == vault.digest
    assert target[1] not in json.dumps(incidents[0])  # the incident holds a hash of the path, never the path


def test_the_evaluator_can_read_what_it_holds_and_nothing_is_recorded(led, world) -> None:
    vault = _seal(led, world)
    assert guarded_read(vault, "evaluator", world / "hidden" / "test_hidden.py", ledger=led) == b"assert helper(3) == 4\n"
    assert [e for e in led.entries() if e["kind"] == "incident"] == []


def test_a_path_that_was_not_sealed_is_not_blocked(led, world) -> None:
    # the control: the check depends on what was sealed, so a look-alike sibling directory is read freely
    vault = _seal(led, world)
    assert guarded_read(vault, "builder", world / "work" / "module.py", ledger=led).startswith(b"def helper")
    assert guarded_read(vault, "builder", world / "hidden2" / "notes.txt", ledger=led) == b"not sealed\n"


def test_a_vault_that_sealed_nothing_blocks_nothing(led, world) -> None:
    # survival control for the refusals above: a guard with the wrong roots lets the same read through
    vault = Vault.seal([world / "work"], ledger=led, vault_id="wrong-root")
    assert guarded_read(vault, "builder", world / "hidden" / "test_hidden.py", ledger=led)


def test_a_dotdot_path_and_a_relative_path_reach_the_same_refusal(led, world, monkeypatch) -> None:
    vault = _seal(led, world)
    sneaky = world / "work" / ".." / "hidden" / "test_hidden.py"
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", sneaky, ledger=led)
    monkeypatch.chdir(world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", Path("hidden") / "fix.diff", ledger=led)


def test_a_case_changed_path_is_refused_where_the_file_system_ignores_case(led, world) -> None:
    if not os.path.exists(str(world / "HIDDEN" / "test_hidden.py")):
        pytest.skip("a case-sensitive file system: the upper-case path is a different path")
    vault = _seal(led, world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", world / "HIDDEN" / "test_hidden.py", ledger=led)


def test_a_link_into_a_sealed_directory_is_refused(led, world) -> None:
    link = world / "work" / "innocent.diff"
    try:
        link.symlink_to(world / "hidden" / "fix.diff")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    vault = _seal(led, world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", link, ledger=led)


def test_a_directory_junction_into_a_sealed_directory_is_refused(led, world) -> None:
    if os.name != "nt":
        pytest.skip("junctions are a Windows feature")
    junction = world / "work" / "alias"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(world / "hidden")], capture_output=True,
                          check=False)
    if made.returncode != 0 or not junction.exists():
        pytest.skip("a junction could not be made here")
    vault = _seal(led, world)
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", junction / "fix.diff", ledger=led)


def test_a_role_outside_the_four_is_an_error_not_a_free_pass(led, world) -> None:
    vault = _seal(led, world)
    with pytest.raises(ValueError, match="role"):
        guarded_read(vault, "root", world / "hidden" / "fix.diff", ledger=led)


def test_a_refusal_invalidates_every_check_taken_against_that_fixture(led, world) -> None:
    vault = _seal(led, world)
    led.add_evidence(_evidence("ctl", vault.digest, purpose="negative_control", result="fail", control=""))
    led.add_evidence(_evidence("e1", vault.digest))
    led.add_claim(_claim(("e1",)))
    assert evaluate_claim(led, "c1")["result"] == "supported"
    with pytest.raises(AccessDenied):
        guarded_read(vault, "builder", world / "hidden" / "test_hidden.py", ledger=led)
    got = evaluate_claim(led, "c1")
    assert got["result"] == "inconclusive"
    assert "contaminated_fixture" in got["reasons"] and got["active_evidence"] == []
    assert vault.digest in led.contaminated_fixtures()


def test_a_fixture_changed_after_sealing_is_found_and_counts_as_compromised(led, world) -> None:
    vault = _seal(led, world)
    assert vault.verify_unchanged(ledger=led) == ()
    (world / "hidden" / "test_hidden.py").write_text("assert True\n", encoding="utf-8")
    problems = vault.verify_unchanged(ledger=led)
    assert problems and "test_hidden.py" in problems[0]
    assert vault.digest in led.contaminated_fixtures()


def test_blinding_is_recorded_as_unblinded_because_the_check_is_in_process(led, world) -> None:
    vault = _seal(led, world)
    status = blinding_status(vault)
    assert status["blinding"] == "unblinded"
    assert "in-process" in status["reason"] and status["enforcement"] == "software_check_in_process"


# --- the trace and the example -----------------------------------------------------------------------------------------


def _evidence(evidence_id: str, fixture: str, *, purpose: str = "check", result: str = "pass",
              control: str = "ctl") -> EvidenceRecord:
    return EvidenceRecord(evidence_id=evidence_id, predicate_id="p-keep", oracle_category="INDEPENDENT_EXECUTABLE",
                          oracle_version="probe-v1", fixture_version=fixture, independent=True,
                          independence_group="oracle-a", independence_rationale="sealed outside the adapting process",
                          result=result, scope="12 held-out inputs", known_limitations=("12 inputs only",),
                          source_ref=f"log:{evidence_id}", standard_sha256=STD.standard_sha256,
                          negative_control_ref=control, purpose=purpose, oracle_error_bound=None)


def _claim(evidence_ids) -> ClaimRecord:
    return ClaimRecord(claim_id="c1", subject="patch p1", predicate_id="p-keep", scope="the 12 held-out inputs",
                       breadth="covered_cases", estimand="oracle_scoped", population="pilot task t1",
                       sample_unit="one task", provenance="sealed fixture", assumptions=("the oracle is correct",),
                       known_limitations=("12 inputs only",), versions={"standard": "std-keep"},
                       evidence_ids=tuple(evidence_ids), standard_sha256=STD.standard_sha256,
                       requires_independent_groups=1)


def test_the_example_builds_an_end_to_end_trace_with_one_supported_and_one_inconclusive_claim(led) -> None:
    claims = kernel_example.build(led)
    assert claims == {"supported": "claim-covered-cases", "inconclusive": "claim-reviewer-consensus"}
    assert evaluate_claim(led, claims["supported"])["result"] == "supported"
    assert evaluate_claim(led, claims["inconclusive"])["result"] == "inconclusive"
    assert led.verify() == ()


def test_the_trace_of_a_claim_reaches_its_standard_its_evidence_and_its_controls(led) -> None:
    claims = kernel_example.build(led)
    got = trace_claim(led, claims["supported"])
    assert got["ledger_ok"] is True and got["problems"] == []
    assert got["standard"]["standard_sha256"] == got["claim"]["standard_sha256"] and got["standard"]["hash_ok"] is True
    assert got["evidence"] and all(item["hash_ok"] for item in got["evidence"])
    active = [item for item in got["evidence"] if item["role"] == "active"]
    assert active and all(item["negative_control"]["result"] == "fail" for item in active)
    structural = [item for item in got["evidence"] if item["role"] == "structural"]
    assert [item["evidence_id"] for item in structural] == ["chk-bench-bytes"]
    assert structural[0]["negative_control"] is None  # the byte comparison is recorded and supports nothing
    assert got["verdict"]["result"] == "supported" and got["episodes"]
    assert got["limits"]


def test_the_trace_of_a_claim_whose_ledger_was_tampered_says_so(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(FLAG, "1")
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    claims = kernel_example.build(ledger)
    assert trace_claim(Ledger(path), claims["supported"])["ledger_ok"] is True
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["kind"] == "evidence" and row["body"]["purpose"] == "check":
            row["body"]["result"] = "pass" if row["body"]["result"] != "pass" else "fail"
            break
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
    problems = Ledger.audit(path)
    assert problems  # a reader who opens the file is told; the ledger will not open as a clean chain


def test_the_trace_of_a_live_ledger_whose_stored_entry_was_changed_says_so(led) -> None:
    claims = kernel_example.build(led)
    assert trace_claim(led, claims["supported"])["ledger_ok"] is True
    entry = next(e for e in led._chain._entries if e["id"] == "chk-probe")  # the chain is g6.core.ledger now
    entry["body"]["result"] = "fail"  # white-box: a stored body changed without its hashes
    got = trace_claim(led, claims["supported"])
    assert got["ledger_ok"] is False and got["problems"]
    assert any(not item["hash_ok"] for item in got["evidence"])


def test_a_claim_that_is_not_in_the_ledger_has_no_trace(led) -> None:
    with pytest.raises(KeyError):
        trace_claim(led, "nope")


def test_the_example_writes_a_ledger_file_only_when_the_flag_is_on(monkeypatch, tmp_path) -> None:
    target = tmp_path / "example.jsonl"
    monkeypatch.delenv(FLAG, raising=False)
    from g6.research.echa import ResearchDisabled

    with pytest.raises(ResearchDisabled):
        kernel_example.main([str(target)])
    assert not target.exists()
    monkeypatch.setenv(FLAG, "1")
    assert kernel_example.main([str(target)]) == 0
    assert Ledger.audit(target) == ()
