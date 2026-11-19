"""Assertions for the operation booking contract used by earlier regressions."""
import time


def operation_clock(monkeypatch, clock):
    monkeypatch.setattr(time, "process_time_ns", lambda: round(clock[0] * 1_000_000_000))
    monkeypatch.setattr(time, "perf_counter_ns", lambda: round(clock[0] * 1_000_000_000))


def assert_operation_booking(ledger, category="acquisition"):
    events = {row.event_id: row for row in ledger.resource_events if row.category == category}
    operations = [row for row in events.values() if row.time_booking == "operation"]
    assert operations
    total = ledger.total(category)
    assert total.cpu_ns == sum(row.cost.cpu_ns for row in operations)
    assert total.wall_ns == sum(row.cost.wall_ns for row in operations)
    assert total.interval("cpu_ns")[1] == sum(row.cost.interval("cpu_ns")[1] for row in operations)
    for name in ("probe_runs", "sandbox_runs", "solver_calls"):
        assert getattr(total, name) == sum(getattr(row.cost, name) for row in events.values())


def assert_diagnostic_cpu(ledger, minimum, category="acquisition"):
    assert max(max(row.cost.cpu_s, row.diagnostic_cost.cpu_s) for row in ledger.resource_events) >= minimum
    assert_operation_booking(ledger, category)
