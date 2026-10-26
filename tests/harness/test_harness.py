# SPDX-License-Identifier: Apache-2.0
"""The end-to-end spine: intake, frozen checks with controls, execution, adjudication, memory across processes."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from g6.harness import Check, Memory, Standard, run
from g6.invariants import check_envelope

GOAL = "Summarise the refund policy for customers"
WINDOW = Check("states the refund window", lambda out: "14 days" in out, known_bad=("Refunds are possible.",),
               known_good="Refunds within 14 days.")
CREDIT = Check("mentions store credit", lambda out: "store credit" in out.lower())


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "harness.db")
    yield m
    m.close()


def _run(standard, output, memory, **kw):
    reply = run(standard, lambda contract: output, memory=memory, **kw)
    assert check_envelope(reply) == [], reply
    return reply


def test_a_passing_run_is_a_qualified_draft_with_receipts(memory) -> None:
    reply = _run(Standard(GOAL, (WINDOW, CREDIT)), "Refunds within 14 days, or store credit.", memory)
    assert reply["completion_state"] == "qualified-draft" and reply["results"] == {
        "states-the-refund-window": "pass", "mentions-store-credit": "pass"}
    assert reply["certificate"]["state"] == "qualified-draft" and len(reply["evidence"]) == 2
    assert "independent evidence" in reply["next_action"]


def test_a_failed_check_blocks_names_itself_and_is_remembered(memory) -> None:
    reply = _run(Standard(GOAL, (WINDOW, CREDIT)), "Refunds are possible, or store credit.", memory)
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_HARNESS_CHECK_FAILED" and "states-the-refund-window" in reply["next_action"]
    assert [f["check"] for f in memory.failures(" ".join(GOAL.lower().split()))] == ["states-the-refund-window"]


def test_a_remembered_failure_blocks_a_standard_that_drops_its_check_until_waived(memory) -> None:
    _run(Standard(GOAL, (WINDOW, CREDIT)), "Refunds are possible, or store credit.", memory)
    later = Standard(GOAL, (CREDIT,))
    blocked = _run(later, "Store credit only.", memory)
    assert blocked["completion_state"] == "blocked-escalated" and blocked["stage"] == "plan"
    assert blocked["warning_card"]["code"] == "G6_E_HARNESS_REMEMBERED_FAILURE"
    assert blocked["plan"]["unchecked_failures"] == ["states-the-refund-window"]
    waived = _run(later, "Store credit only.", memory, decisions={"waive:states-the-refund-window"})
    assert waived["completion_state"] == "qualified-draft"


def test_memory_changes_the_next_run_after_a_process_restart(tmp_path) -> None:
    """Demo 1's claim, as a test: the second run is a new interpreter and still knows the first run's failure."""
    script = textwrap.dedent(f"""
        import sys
        from g6.harness import Check, Standard, run
        checks = (Check("mentions store credit", lambda out: "store credit" in out.lower()),)
        if sys.argv[1] == "first":
            checks += (Check("states the refund window", lambda out: "14 days" in out),)
        reply = run(Standard({GOAL!r}, checks), lambda contract: "Store credit only.")
        print(reply["completion_state"], reply["warning_card"]["code"] if reply.get("warning_card") else "-")
    """)
    env = dict(os.environ, G6_HOME=str(tmp_path))
    first = subprocess.run([sys.executable, "-c", script, "first"], capture_output=True, text=True, env=env, check=True)
    second = subprocess.run([sys.executable, "-c", script, "second"], capture_output=True, text=True, env=env, check=True)
    assert first.stdout.split() == ["blocked-escalated", "G6_E_HARNESS_CHECK_FAILED"]
    assert second.stdout.split() == ["blocked-escalated", "G6_E_HARNESS_REMEMBERED_FAILURE"]
    fresh = subprocess.run([sys.executable, "-c", script, "second"], capture_output=True, text=True, check=True,
                           env=dict(os.environ, G6_HOME=str(tmp_path / "other")))
    assert fresh.stdout.split()[0] == "qualified-draft"   # control: without the memory the same run passes


def test_a_check_that_accepts_a_known_bad_output_is_reported_broken(memory) -> None:
    lax = Check("states the refund window", lambda out: True, known_bad=("Refunds are possible.",))
    reply = _run(Standard(GOAL, (lax,)), "anything", memory)
    assert reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_HARNESS_BROKEN_CHECK" and reply["plan"]["broken_checks"]


def test_a_risky_task_waits_for_a_person(memory) -> None:
    standard = Standard("Delete production customer payment records older than 7 years",
                        (Check("lists the tables touched", lambda out: "tables:" in out),))
    blocked = _run(standard, "tables: payments", memory)
    assert blocked["stage"] == "intake" and "RISKY" in blocked["warning_card"]["code"]
    accepted = _run(standard, "tables: payments", memory, decisions={"accept_risky"})
    assert accepted["completion_state"] == "qualified-draft"


def test_no_executor_or_a_failing_one_is_blocked(memory) -> None:
    standard = Standard(GOAL, (WINDOW,))
    assert run(standard, None, memory=memory)["warning_card"]["code"] == "G6_E_HARNESS_NO_EXECUTOR"

    def boom(contract):
        raise RuntimeError("model unavailable")

    failed = run(standard, boom, memory=memory)
    assert failed["warning_card"]["code"] == "G6_E_HARNESS_EXECUTOR_FAILED" and "model unavailable" in failed[
        "warning_card"]["message"]


def test_an_undecided_gate_is_a_named_gap(memory) -> None:
    standard = Standard(GOAL, (WINDOW,), gates=("legal sign-off",))
    reply = _run(standard, "Refunds within 14 days.", memory)
    assert "gate:legal sign-off: human_decision_missing" in reply["gaps"]
    decided = _run(standard, "Refunds within 14 days.", memory, decisions={"legal sign-off"})
    assert not any(g.startswith("gate:") for g in decided["gaps"])


def test_a_callers_check_never_lifts_a_result_to_verified(memory) -> None:
    """DECISIONS #22: even declared independent, a check whose controls G6 did not generate supports nothing."""
    independent = Check("states the refund window", lambda out: "14 days" in out, author="reviewer",
                        kind="independent_executable", known_bad=("Refunds are possible.",), known_good="14 days")
    reply = _run(Standard(GOAL, (independent,)), "Refunds within 14 days.", memory, producer="agent")
    assert reply["completion_state"] == "qualified-draft"
    assert any("control_not_generated_by_g6" in g for g in reply["gaps"])


@pytest.mark.parametrize("claimed", ["g6", " G6 "])
def test_evidence_01_a_callers_check_that_claims_to_be_g6s_never_lifts_a_run_to_verified(memory, claimed) -> None:
    """A check the caller supplies is the caller's, whatever author it names: claiming "g6" (and an independent kind)
    makes neither the check nor the controls the caller's known-bad outputs gave G6's."""
    # Taking check.author for _caller_author(check) in _run's `made, author = [], _caller_author(check)` makes this fail.
    claimed_check = Check("states the refund window", lambda out: "14 days" in out, author=claimed,
                          kind="independent_executable", known_bad=("Refunds are possible.",), known_good="14 days")
    reply = _run(Standard(GOAL, (claimed_check,)), "Refunds within 14 days.", memory, producer="agent")
    assert reply["completion_state"] == "qualified-draft" and reply["certificate"]["state"] == "qualified-draft"
    recorded = reply["certificate"]["checks"]
    assert [c["author"] for c in recorded] == ["caller"]
    assert all(k["generated_by"] == "caller" for c in recorded for k in c["controls"])
    assert any("control_not_generated_by_g6" in g for g in reply["gaps"])


def test_an_issuers_verified_is_held_at_the_harness_door(memory, monkeypatch) -> None:
    """Plan A1: the issuer's certificate keeps its persisted state, but its verified is held where it enters the
    envelope, because the harness door does not re-run the checks itself."""
    import g6.evidence as evidence

    class _Verified:
        state, gaps = "verified", ()

        def as_dict(self) -> dict:
            return {"state": "verified", "gaps": []}

    monkeypatch.setattr(evidence, "issue", lambda *args, **kwargs: _Verified())
    reply = _run(Standard(GOAL, (WINDOW,)), "Refunds within 14 days.", memory)
    assert reply["completion_state"] == "qualified-draft" and reply["claimed_completion_state"] == "verified"
    assert reply["warning_card"]["code"] == "G6_E_PROMOTION_LAW" and reply["certificate"]["state"] == "verified"
    assert reply["next_action"] != "None."


def test_a_plan_that_needs_revision_stops_the_run_before_execution(memory) -> None:
    """Audit 018 PC-003: the critique verdict is obeyed, not just reported."""
    from g6.reliability_pipeline.plan import Plan

    ran = []
    sloppy = Standard(GOAL, (WINDOW,), plan=Plan(goal=GOAL, tests=["."], rollback="TBD",
                                                 acceptance_criteria=["states the refund window"]))
    reply = run(sloppy, lambda contract: ran.append(1) or "Refunds within 14 days.", memory=memory)
    assert reply["stage"] == "plan" and reply["completion_state"] == "blocked-escalated" and not ran
    assert reply["warning_card"]["code"] == "G6_E_PLAN_REVISE" and "undone" in reply["next_action"]
    tidy = Standard(GOAL, (WINDOW,), plan=Plan(goal=GOAL, tests=["the refund-window check"], rollback="discard the reply",
                                               acceptance_criteria=["states the refund window"]))
    assert _run(tidy, "Refunds within 14 days.", memory)["completion_state"] == "qualified-draft"


@pytest.mark.parametrize("checks", [(), (WINDOW, WINDOW)])
def test_a_standard_without_checks_or_with_duplicate_ids_is_refused(memory, checks) -> None:
    reply = run(Standard(GOAL, checks), lambda contract: "x", memory=memory)
    assert reply["warning_card"]["code"] == "G6_E_HARNESS_STANDARD"
