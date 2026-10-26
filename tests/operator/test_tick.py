# SPDX-License-Identifier: Apache-2.0
"""One tick: completion comes from the operator's own record (VEN-007), and every outward draft passes the action gate
and the draft checks before it reaches a person."""
from __future__ import annotations

from g6.harness import Check
from g6.invariants import assert_envelope
from g6.operator import ExecutionResult, SimulatedExecutor, Task, tick
from g6.operator.router import Context

CLAIM = "Analysis complete: all 12 competitors reviewed and priced."


def _tick(queue, venture, now, result, checks=(), context=None, actor="agent-1"):
    reply = tick(queue, venture, Task("t1", "[analysis] review competitors", reserve=0.25, checks=checks),
                 SimulatedExecutor({"t1": result}, actor=actor), context=context, now=now)
    assert_envelope(reply)
    assert reply["completion_state"] != "verified"
    return reply


def test_ven007_a_claimed_completion_without_a_check_is_self_reported(queue, venture, now):
    # Archive: for non-code kinds, a well-formed result.json was "completed" and the agent's summary stored as done.
    reply = _tick(queue, venture, now, ExecutionResult(CLAIM, claimed_status="completed"))
    assert (reply["completion_state"], reply["completion"]) == ("qualified-draft", "self-reported")
    assert reply["warning_card"]["code"] == "G6_E_OPERATOR_SELF_REPORTED"
    [task] = queue.scope.evidence(venture, kind="task")
    assert task["detail"]["claimed_status"] == "completed" and task["detail"]["receipts"] == {}
    assert queue.scope.evidence(venture, kind="completion")[0]["detail"]["completion"] == "self-reported"


def test_ven007_the_operators_check_overrules_the_executors_claim(queue, venture, now, plan):
    checks = (Check("names the 12 competitors", lambda out: out.count("\n") >= 12),)
    draft = {"type": "post", "body": "We reviewed 12 competitors.", "verification_contract": plan}
    reply = _tick(queue, venture, now, ExecutionResult(CLAIM, proposals=(draft,), claimed_status="completed"), checks)
    assert (reply["completion_state"], reply["completion"]) == ("blocked-escalated", "failed")
    assert reply["receipts"] == {"names-the-12-competitors": "fail"} and reply["queued"] == []
    assert queue.list(venture) == []


def test_passing_caller_checks_are_still_a_qualified_draft(queue, venture, now):
    reply = _tick(queue, venture, now, ExecutionResult(CLAIM), (Check("mentions 12", lambda out: "12" in out),))
    assert (reply["completion"], reply["warning_card"]["code"]) == ("checked", "G6_E_OPERATOR_CALLER_CHECKS")
    unknown = _tick(queue, venture, now, ExecutionResult(CLAIM), (Check("asks a model", lambda out: "unknown"),))
    assert (unknown["completion"], unknown["completion_state"]) == ("unsettled", "qualified-draft")


def test_every_outward_draft_goes_through_the_gate_then_the_checks_then_a_person(queue, venture, now, plan):
    ballistic = {"type": "email", "to": "b1", "body": "Hi Sam."}   # no verification plan: the gate blocks it
    cold = {"type": "email", "to": "stranger", "body": "Hi there.", "verification_contract": plan}
    good = {"type": "email", "to": "b1", "body": "Hi Sam, the pilot starts Monday.", "verification_contract": plan}
    reply = _tick(queue, venture, now, ExecutionResult(CLAIM, proposals=(ballistic, cold, good)),
                  context=Context(contacts=frozenset({"b1"})))
    assert [b["stage"] for b in reply["blocked"]] == ["gate", "checks"]
    assert "G6_E_BALLISTIC_ACTION_BLOCKED" in reply["blocked"][0]["reason_codes"]
    assert reply["blocked"][1]["fired"] == ["recipient"]
    [row] = queue.list(venture)
    assert row["action_id"] == reply["queued"][0] and row["status"] == "proposed" and row["drafted_by"] == "agent-1"
    assert row["review"]["gate"]["decision"] in {"require_human_review", "require_confirmation"}
    kinds = [e["kind"] for e in reply["evidence"]]
    assert kinds == ["task", "completion", "draft_blocked", "draft_blocked", "queued"]


def test_an_executor_that_fails_or_names_no_actor_is_reported(queue, venture, now):
    def broken(task):
        raise RuntimeError("model unavailable")

    reply = tick(queue, venture, Task("t", "x", reserve=0.25), SimulatedExecutor(broken), now=now)
    assert_envelope(reply)
    assert (reply["warning_card"]["code"], reply["charged"]) == ("G6_E_OPERATOR_EXECUTOR_FAILED", 0.25)
    anonymous = _tick(queue, venture, now, ExecutionResult(CLAIM), actor="")
    assert anonymous["warning_card"]["code"] == "G6_E_OPERATOR_TASK"


def test_se010_nothing_is_reserved_or_run_while_g6_is_stopped(queue, venture, now, monkeypatch):
    ran = []
    executor = SimulatedExecutor(lambda task: ran.append(task.task_id) or ExecutionResult(CLAIM))
    monkeypatch.setenv("G6_STOP", "owner pause")
    reply = tick(queue, venture, Task("t1", "[analysis] review competitors", reserve=0.25), executor, now=now)
    assert_envelope(reply)
    assert (reply["completion_state"], reply["warning_card"]["code"]) == ("blocked-escalated", "G6_E_STOPPED")
    assert ran == [] and [e["kind"] for e in reply["evidence"]] == ["stop"]
    monkeypatch.delenv("G6_STOP")   # control: released, the same tick runs
    assert tick(queue, venture, Task("t1", "[analysis] review competitors", reserve=0.25), executor,
                now=now)["completion"] == "self-reported" and ran == ["t1"]
