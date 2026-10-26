# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Durable admission at the existing workforce ledger; all calls are scripted."""
from __future__ import annotations

import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from g6.safety_engine.schema import RuntimePolicy
from g6.workforce import store
from g6.workforce import (
    calls,
    capabilities,
    contracts,
    economics,
    missions,
    runs,
    workers,
)


def _contract(amount=30, currency="AUD"):
    return capabilities.ParamContract.from_dict({
        "component": "calculator", "op": "sum", "side_effect": "read", "model_free": True,
        "required": {}, "probe_params": {}, "expect_field": "total", "check": "input_model",
        **({"cost_ceiling": {"amount_minor_units": amount, "currency": currency}} if amount is not None else {})})


def _cost(amount=30):
    return economics.call_cost(economics.Money(amount, "AUD"), model_free=True, component="calculator", op="sum",
                               charged_model_calls=0, process_model_calls=0)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "venture.db"
    vstore = store.VentureStore(path)
    vid = vstore.create_venture("alice", "Alice", budget_aud_month=0, model_cap_usd_month=0,
                                task_cap_usd=0, max_turns=5)["venture_id"]
    ms = missions.MissionStore(vstore.for_owner("alice"))
    mid = ms.create(vid, objective="Total", risk_tier="R1", budget_cents=50, actor="chat")
    with vstore.conn:
        vstore.conn.execute("UPDATE missions SET status='active' WHERE mission_id=?", (mid,))
    worker = workers.WorkerSpec(template="doer", purpose="Total", rung="deterministic",
                               allowed=(("calculator", "sum"),), read_only=True,
                               policy=RuntimePolicy(max_cost_cents=50, max_shell_commands=0, max_files_modified=0,
                                                    max_lines_changed=0, allowed_tools=frozenset({"calculator.sum"})))
    ledger = runs.RunLedger(ms)
    opened = ledger.open(mid, "sum", worker)
    yield SimpleNamespace(path=path, vstore=vstore, ms=ms, mid=mid, worker=worker, ledger=ledger, run=opened.run_id)
    vstore.conn.close()


def test_pending_holds_count_once_and_settlement_does_not_rewrite_history(book):
    assert book.ledger.reserve_call(book.run, 0, _contract(), charged=True) == ""
    before = book.ms.events(book.mid, kind="cost.reserved")
    assert book.ledger.cost_total(book.mid).known_minor_units == 0
    assert book.ledger.committed_total(book.mid).known_minor_units == 30
    assert "left" in book.ledger.reserve_call(book.run, 1, _contract(), charged=True)
    book.ledger.record_cost(book.run, 0, _cost())
    assert book.ledger.pending_reservations(book.mid) == []
    assert book.ledger.committed_total(book.mid).known_minor_units == 30
    assert book.ms.events(book.mid, kind="cost.reserved") == before
    book.ledger.record_cost(book.run, 0, _cost())
    assert book.ledger.cost_total(book.mid).known_minor_units == 30
    assert book.ledger.reserve_call(book.run, 1, _contract(20), charged=True) == ""
    assert book.ledger.committed_total(book.mid).known_minor_units == 50




@pytest.mark.parametrize("settled", [True, False])
def test_replayed_call_identity_never_grants_another_invocation(book, settled):
    book.ledger.reserve_call(book.run, 0, _contract(0), charged=True)
    if settled:
        book.ledger.record_cost(book.run, 0, _cost(0))
    with pytest.raises(ValueError, match="already"):
        book.ledger.reserve_call(book.run, 0, _contract(0), charged=True)


def test_recorded_cost_without_a_reservation_cannot_be_replayed(book):
    book.ledger.record_cost(book.run, 0, _cost(0))
    with pytest.raises(ValueError, match="already"):
        book.ledger.reserve_call(book.run, 0, _contract(0), charged=True)


def test_two_connections_cannot_reserve_the_same_remaining_headroom(book):
    others = [store.VentureStore(book.path) for _ in range(2)]
    ledgers = [runs.RunLedger.reader(missions.MissionStore(s.for_owner("alice"))) for s in others]
    barrier = threading.Barrier(2, timeout=10)

    def reserve(index):
        barrier.wait()
        return ledgers[index].reserve_call(book.run, index, _contract(), charged=True)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, range(2)))
        assert sum(not result for result in outcomes) == 1
        assert len(book.ledger.pending_reservations(book.mid)) == 1
        assert book.ledger.committed_total(book.mid).known_minor_units == 30
    finally:
        for connection in others:
            connection.conn.close()


def test_mission_limit_binds_independent_units_even_if_workers_allow_more(book):
    second = book.ledger.open(book.mid, "second", book.worker)
    assert book.ledger.reserve_call(book.run, 0, _contract(), charged=True) == ""
    assert "mission" in book.ledger.reserve_call(second.run_id, 0, _contract(), charged=True)


def test_stored_worker_limit_is_checked_separately_from_mission_limit(book):
    small = dataclasses.replace(book.worker, policy=dataclasses.replace(book.worker.policy, max_cost_cents=20))
    run = book.ledger.open(book.mid, "small", small)
    assert "step" in book.ledger.reserve_call(run.run_id, 0, _contract(), charged=True)
    assert book.ledger.pending_reservations(book.mid) == []


def test_reservations_require_the_attempts_current_lease(book):
    lease = book.ms.take_lease(book.mid, holder="first")
    run = book.ledger.open(book.mid, "leased", book.worker, lease=lease)
    with pytest.raises(missions.LeaseLost):
        book.ledger.reserve_call(run.run_id, 0, _contract(), charged=True)
    book.ms.release_lease(lease)
    newer = book.ms.take_lease(book.mid, holder="second")
    for supplied in (lease, newer):
        with pytest.raises(missions.LeaseLost):
            book.ledger.reserve_call(run.run_id, 0, _contract(), charged=True, lease=supplied)
    assert book.ledger.pending_reservations(book.mid) == []


def test_unleased_attempt_cannot_reserve_while_a_runner_holds_the_mission(book):
    book.ms.take_lease(book.mid, holder="runner")
    with pytest.raises(missions.LeaseLost):
        book.ledger.reserve_call(book.run, 0, _contract(), charged=True)


def test_closed_attempt_and_foreign_owner_cannot_reserve_or_read_holds(book):
    foreign = runs.RunLedger.reader(missions.MissionStore(book.vstore.for_owner("bob")))
    with pytest.raises(LookupError, match=store.NOT_FOUND):
        foreign.reserve_call(book.run, 0, _contract(), charged=True)
    with pytest.raises(LookupError, match=store.NOT_FOUND):
        foreign.pending_reservations(book.mid)
    assert foreign.pending_reservations() == []
    book.ledger.close(book.run, contracts.Outcome(contracts.QUALIFIED))
    with pytest.raises(ValueError, match="not open"):
        book.ledger.reserve_call(book.run, 0, _contract(), charged=True)


@pytest.mark.parametrize(("amount", "currency"), [(None, "AUD"), (30, "USD")])
def test_charged_unknown_or_foreign_reservation_is_refused(book, amount, currency):
    with pytest.raises(ValueError, match="cost"):
        book.ledger.reserve_call(book.run, 0, _contract(amount, currency), charged=True)
    assert book.ledger.pending_reservations(book.mid) == []


@pytest.mark.parametrize(("amount", "currency"), [(None, "AUD"), (30, "USD")])
def test_local_unknown_or_foreign_reservations_remain_visible_as_unknown(book, amount, currency):
    assert book.ledger.reserve_call(book.run, 0, _contract(amount, currency), charged=False) == ""
    assert book.ledger.committed_total(book.mid).amount_minor_units is None
    assert len(book.ledger.pending_reservations(book.mid)) == 1


def test_unresolved_holds_survive_abandon_and_block_local_priced_retry(book):
    book.ledger.reserve_call(book.run, 0, _contract(), charged=False)
    book.ledger.abandon(book.run, "uncertain")
    run = book.ledger.open(book.mid, "sum", book.worker)
    assert "left" in book.ledger.reserve_call(run.run_id, 0, _contract(), charged=False)
    assert book.ledger.committed_total(book.mid).known_minor_units == 30
    assert book.ledger.committed_total(book.mid).amount_minor_units is None
    assert book.ledger.reserve_call(run.run_id, 1, _contract(0), charged=True) == ""


def test_a_cost_above_its_reservation_is_kept_and_prevents_more_spend(book):
    book.ledger.reserve_call(book.run, 0, _contract(), charged=True)
    book.ledger.record_cost(book.run, 0, _cost(60))
    assert book.ledger.committed_total(book.mid).known_minor_units == 60
    assert "left" in book.ledger.reserve_call(book.run, 1, _contract(1), charged=True)


def test_atomic_reservation_refusal_stops_a_door_with_an_older_budget_snapshot(book):
    invoked = []
    second = book.ledger.open(book.mid, "second", book.worker)
    door = calls.Door(book.worker, run_id=second.run_id, contracts=lambda *args: _contract(), charged=True,
                      invoke=lambda *args: invoked.append(args), reserve=lambda index, contract: book.ledger.reserve_call(
                          second.run_id, index, contract, charged=True))
    book.ledger.reserve_call(book.run, 0, _contract(), charged=True)
    result = door.call("calculator", "sum", {})
    assert (result.refusal_code, result.cost, invoked) == ("refused:budget", None, [])
    assert "mission" in result.refused
    assert book.ledger.cost_entries(book.mid) == []


@pytest.mark.parametrize("index", [-1, True, 1.5, "0"])
def test_invalid_call_index_cannot_create_a_hold(book, index):
    with pytest.raises(ValueError, match="index"):
        book.ledger.reserve_call(book.run, index, _contract(0), charged=True)
    assert book.ledger.pending_reservations(book.mid) == []


def test_admission_never_runs_in_an_outer_uncommitted_transaction(book):
    with book.vstore.conn:
        book.vstore.conn.execute("UPDATE missions SET lease_fence=lease_fence WHERE mission_id=?", (book.mid,))
        with pytest.raises(ValueError, match="own transaction"):
            book.ledger.reserve_call(book.run, 0, _contract(), charged=True)
    assert book.ledger.pending_reservations(book.mid) == []


def test_another_attempts_cost_cannot_settle_this_attempts_reservation(book):
    book.ledger.reserve_call(book.run, 0, _contract(), charged=True)
    second = book.ledger.open(book.mid, "second", book.worker)
    # An internal caller can supply an invocation id, but the ledger stamps the actual owning attempt.
    book.ledger.record_cost(second.run_id, 0, dataclasses.replace(
        _cost(0), capability_invocation_id=f"{book.run}#0"))
    assert len(book.ledger.pending_reservations(book.mid, run_id=book.run)) == 1
    assert len(book.ledger.pending_reservations(book.mid, unit_key="sum")) == 1
    assert len(book.ledger.pending_reservations(book.mid)) == 1
    assert len(book.ledger.pending_reservations()) == 1
    assert book.ledger.committed_total(book.mid).known_minor_units == 30
    assert "mission" in book.ledger.reserve_call(second.run_id, 1, _contract(), charged=True)
