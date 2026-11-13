# SPDX-License-Identifier: Apache-2.0
"""Budget stops that actually stop (audit 011 VEN-004) and atomic monthly reservations (VEN-005)."""
from __future__ import annotations

import math
import threading

import pytest

from g6.operator import ActionQueue, BudgetRefused, ExecutionResult, OperatorStore, SimulatedExecutor, Task, stop_reason, tick


def test_ven004_unknown_or_missing_budget_fails_closed(scope):
    # Archive: gates.stop_reason skipped any cap <= 0 ("continue"), so an unset cap meant no limit.
    for caps in ({"model": 1.0}, {"model": 1.0, "cash": None}, {"model": math.nan, "cash": 0.0},
                 {"model": -1.0, "cash": 0.0}, {}):
        assert stop_reason({"status": "active", "caps": caps}, {}) is not None, caps
        with pytest.raises(ValueError):
            scope.create_venture("x", caps=caps)
    assert stop_reason({"status": "active", "caps": {"model": 1.0, "cash": 0.0}}, {}) is None


def test_ven004_a_zero_cap_is_a_cap_not_no_limit(scope):
    vid = scope.create_venture("free tier", caps={"model": 0.0, "cash": 0.0})["venture_id"]
    with pytest.raises(BudgetRefused, match="budget_exhausted"):
        scope.reserve(vid, month="2026-10", account="model", amount=0.01)


def test_ven004_a_zero_cost_executor_still_hits_the_model_cap(queue, venture, now):
    # Archive: local and customer-client runs recorded cost 0, so 10 ticks on a 1-cent cap never stopped.
    executor = SimulatedExecutor(lambda task: ExecutionResult("done", cost=0.0, claimed_status="completed"))
    replies = [tick(queue, venture, Task(f"t{i}", "analyse", reserve=0.25), executor, now=now) for i in range(10)]
    codes = [r["warning_card"]["code"] for r in replies]
    assert codes == ["G6_E_OPERATOR_SELF_REPORTED"] * 4 + ["G6_E_OPERATOR_STOP"] * 6
    assert "budget_exhausted: committing 0.25 would take model to 1.25" in replies[4]["warning_card"]["message"]
    assert len(executor.calls) == 4
    assert queue.scope.committed(venture, "2026-10") == {"model": 1.0}


def test_ven004_a_spend_marked_sent_is_charged_to_cash_and_stops_the_venture(queue, venture, plan, now):
    # Archive: nothing outside tests wrote cash_aud_spent, so a sent A$1,000 spend on a A$500 cap never stopped it.
    spend = {"type": "spend", "amount": 1000.0, "body": "Buy the ad slot.", "verification_contract": plan}
    executor = SimulatedExecutor({"t1": ExecutionResult("drafted", proposals=(spend,))}, actor="agent-1")
    [action_id] = tick(queue, venture, Task("t1", "draft a spend", reserve=0.25), executor, now=now)["queued"]
    queue.approve(venture, action_id, reviewer="dana")
    assert queue.mark_sent(venture, action_id, reviewer="dana", channel="other") is True
    assert queue.mark_sent(venture, action_id, reviewer="dana", channel="other") is False   # charged once
    assert queue.scope.committed(venture, "2026-10")["cash"] == 1000.0
    reason = stop_reason(queue.scope.venture(venture), queue.scope.committed(venture, "2026-10"))
    assert reason.startswith("budget_exhausted: cash")
    after = tick(queue, venture, Task("t2", "anything", reserve=0.25), SimulatedExecutor(lambda t: None), now=now)
    assert (after["completion_state"], after["warning_card"]["code"]) == ("blocked-escalated", "G6_E_OPERATOR_STOP")


def test_ven004_a_run_is_charged_at_least_its_reservation(scope, venture):
    reservation = scope.reserve(venture, month="2026-10", account="model", amount=0.5)
    assert scope.settle(venture, reservation, reported=0.0) == 0.5
    again = scope.reserve(venture, month="2026-10", account="model", amount=0.25)
    assert scope.settle(venture, again, reported=0.375) == 0.375   # a higher report raises the charge
    with pytest.raises(ValueError, match="already settled"):
        scope.settle(venture, again, reported=None)
    for bad in (0, -1.0, math.inf, math.nan, None, True):
        with pytest.raises(BudgetRefused):
            scope.reserve(venture, month="2026-10", account="model", amount=bad)


def test_the_hosts_own_count_is_the_charge_and_a_runs_report_is_not_read(scope, venture):
    reservation = scope.reserve(venture, month="2026-10", account="model", amount=0.5)
    assert scope.settle(venture, reservation, reported=0.75, metered=0.125) == 0.125
    assert scope.committed(venture, "2026-10")["model"] == 0.125
    zero = scope.reserve(venture, month="2026-10", account="model", amount=0.5)
    assert scope.settle(venture, zero, metered=0) == 0.0
    open_one = scope.reserve(venture, month="2026-10", account="model", amount=0.5)
    for bad in (-1, math.inf, math.nan, True, "1"):
        with pytest.raises(ValueError, match="metered"):
            scope.settle(venture, open_one, metered=bad)


def test_ven005_concurrent_tickers_never_exceed_the_cap(tmp_path):
    # Archive: the month row was checked before a tick and charged after it, so two tickers at 89% both passed.
    path = tmp_path / "operator.db"
    seed = OperatorStore(path).for_owner("dana")
    vid = seed.create_venture("v", caps={"model": 1.0, "cash": 0.0})["venture_id"]
    seed.settle(vid, seed.reserve(vid, month="2026-10", account="model", amount=0.5), reported=None)
    stores = [OperatorStore(path) for _ in range(8)]
    barrier, won, lost = threading.Barrier(len(stores)), [], []

    def ticker(store):
        barrier.wait()
        try:
            won.append(store.for_owner("dana").reserve(vid, month="2026-10", account="model", amount=0.125))
        except BudgetRefused:
            lost.append(store)

    threads = [threading.Thread(target=ticker, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (len(won), len(lost)) == (4, 4)
    assert seed.committed(vid, "2026-10") == {"model": 1.0}
    for s in [*stores, seed.store]:
        s.close()


def test_ven005_the_ledger_is_monthly_not_lifetime(scope, venture):
    # Archive: ledger-<venture>.jsonl never rolled over; after one month's spend every reservation was refused.
    scope.settle(venture, scope.reserve(venture, month="2026-10", account="model", amount=1.0), reported=None)
    with pytest.raises(BudgetRefused):
        scope.reserve(venture, month="2026-10", account="model", amount=0.125)
    assert scope.reserve(venture, month="2026-11", account="model", amount=0.125)


def test_ledger_and_evidence_are_append_only(scope, venture):
    scope.settle(venture, scope.reserve(venture, month="2026-10", account="model", amount=0.5), reported=None)
    for sql in ("UPDATE ledger SET amount = 0", "DELETE FROM ledger", "UPDATE evidence SET kind = 'x'",
                "DELETE FROM evidence", "INSERT OR REPLACE INTO ledger (seq, venture_id, month, account, kind, amount,"
                " ref, at) VALUES (1, 'v', '2026-10', 'model', 'charge', 0, 'r', 'now')"):
        with pytest.raises(Exception, match="append-only"):
            scope.conn.execute(sql)
        scope.conn.rollback()


def test_a_paused_venture_does_not_start_work(queue, venture, now):
    queue.scope.set_status(venture, "paused", by="dana")
    executor = SimulatedExecutor(lambda t: ExecutionResult("x"))
    reply = tick(queue, venture, Task("t", "x", reserve=0.25), executor, now=now)
    assert reply["warning_card"]["code"] == "G6_E_OPERATOR_STOP" and "paused" in reply["warning_card"]["message"]
    assert executor.calls == []


def test_another_owner_cannot_reach_the_venture(store, venture):
    other = store.for_owner("mallory")
    calls = (lambda: other.venture(venture), lambda: ActionQueue(other).list(venture),
             lambda: other.reserve(venture, month="2026-10", account="model", amount=0.1))
    for call in calls:
        with pytest.raises(LookupError, match="no such venture for this owner"):
            call()
