"""Operation booking, child intervals and canonical arithmetic regressions."""
import asyncio
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from g6.distill import cost_ledger as costs
from g6.distill.graph_records import CostVector


def clock_ns(monkeypatch):
    clock = [0]
    monkeypatch.setattr(time, "process_time_ns", lambda: clock[0])
    monkeypatch.setattr(time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setattr(time, "perf_counter_ns", lambda: clock[0])
    return clock


def booked_operations(ledger):
    return [e for e in ledger.resource_events if getattr(e, "time_booking", None) == "operation"]


def test_review2_transition_intervals_book_once(monkeypatch):
    clock = clock_ns(monkeypatch)
    monkeypatch.setattr(time, "thread_time", lambda: clock[0] / 1_000_000_000)
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0] / 1_000_000_000)
    resume, pause = costs._ExclusiveMeter.resume, costs._ExclusiveMeter.pause
    def entering(self):
        clock[0] += 1_000_000_000
        return resume(self)
    def leaving(self):
        result = pause(self)
        clock[0] += 1_000_000_000
        return result
    monkeypatch.setattr(costs._ExclusiveMeter, "resume", entering)
    monkeypatch.setattr(costs._ExclusiveMeter, "pause", leaving)
    ledger = costs.CostLedger()
    with ledger.measure("transition"):
        clock[0] += 1_000_000_000
        with costs.consume_resources(CostVector(probe_runs=1)):
            clock[0] += 1_000_000_000
            with costs.consume_resources(CostVector(sandbox_runs=1)):
                clock[0] += 1_000_000_000
            clock[0] += 1_000_000_000
        clock[0] += 1_000_000_000
    assert ledger.total("acquisition").cpu_s == clock[0] / 1_000_000_000
    assert ledger.total("acquisition").wall_s == clock[0] / 1_000_000_000
    assert len(booked_operations(ledger)) == 1


def test_review2_async_non_lifo_is_diagnostic(monkeypatch):
    clock = clock_ns(monkeypatch)
    ledger = costs.CostLedger()
    async def schedule():
        entered, leave = asyncio.Event(), asyncio.Event()
        async def first():
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] += 2_000_000_000
                entered.set()
                await leave.wait()
                clock[0] += 3_000_000_000
        async def second():
            await entered.wait()
            with costs.consume_resources(CostVector(sandbox_runs=1)):
                clock[0] += 2_000_000_000
                leave.set()
                await asyncio.sleep(0)
                clock[0] += 3_000_000_000
        with ledger.measure("async"):
            await asyncio.gather(first(), second())
    asyncio.run(schedule())
    assert ledger.total("acquisition").cpu_s == 10
    assert ledger.total("acquisition").probe_runs == 1
    assert not getattr(costs._meters, "stack", ())
    assert len(booked_operations(ledger)) == 1


@pytest.mark.parametrize("boundary", ["pause", "resume"])
@pytest.mark.parametrize("position", range(1, 5))
def test_review2_diagnostic_sampling_exception_retains_completions(monkeypatch, boundary, position):
    clock = clock_ns(monkeypatch)
    ledger, calls = costs.CostLedger(), [0]
    method = getattr(costs._ExclusiveMeter, boundary)
    def fail_at(self):
        calls[0] += 1
        if calls[0] == position:
            raise RuntimeError("diagnostic reading")
        return method(self)
    monkeypatch.setattr(costs._ExclusiveMeter, boundary, fail_at)
    with ledger.measure("sampling"):
        for _ in range(3):
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] += 1_000_000_000
    assert ledger.total("acquisition").probe_runs == 3
    assert ledger.total("acquisition").cpu_s == 3
    assert not getattr(costs._meters, "stack", ())
    assert costs.resource_owner() is None


def test_review2_independent_nested_ledgers_share_one_booking(monkeypatch):
    clock = clock_ns(monkeypatch)
    outer, inner = costs.CostLedger(), costs.CostLedger()
    with outer.measure("outer"):
        clock[0] += 1_000_000_000
        with inner.measure("inner") as nested:
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] += 1_000_000_000
        clock[0] += 1_000_000_000
    assert outer.total("acquisition").cpu_s == 3
    assert inner.total("acquisition").cpu_s == 0
    assert inner.total("acquisition").probe_runs == 1
    assert nested.record.operation_id == booked_operations(outer)[0].event_id
    assert nested.record.time_booking == "shared_operation"


def child_work(grandchild=False):
    burn = "import time; s=time.process_time_ns(); exec('while time.process_time_ns()-s<20000000: pass')"
    source = ("import subprocess,sys; subprocess.run([sys.executable,'-I','-c'," + repr(burn) + "],check=True)"
              if grandchild else burn)
    subprocess.run([sys.executable, "-I", "-c", source], check=True)


def test_review2_generic_subprocess_has_cpu_interval():
    ledger = costs.CostLedger()
    with ledger.measure("generic child") as scope:
        child_work()
    total = ledger.total("acquisition")
    assert scope.record.resource_gaps
    lower, upper = total.interval("cpu_ns")
    assert upper > lower
    assert upper == lower + booked_operations(ledger)[0].cost.wall_ns * (costs.os.cpu_count() or 1)


def test_review2_direct_child_reading_does_not_close_descendants():
    ledger = costs.CostLedger()
    with ledger.measure("grandchild") as scope:
        with costs.consume_resources(CostVector(probe_runs=1), child_process=True):
            child_work(grandchild=True)
            costs.report_child_resources(cpu_s=0, wall_s=0.1)
    assert scope.record.resource_gaps
    assert ledger.total("acquisition").interval("cpu_ns")[1] > ledger.total("acquisition").cpu_ns


def test_review2_public_totals_and_estimates_keep_bounds():
    ledger = costs.CostLedger()
    with ledger.measure("unmeasured", category="serving"):
        with costs.consume_resources(CostVector(probe_runs=1), child_process=True):
            pass
    total = ledger.total("serving")
    profile = costs.CostProfile(serving=total)
    incumbent = costs.CostProfile(serving=costs.Resources(cpu_s=100, wall_s=100, probe_runs=2))
    result = costs.compare_costs(profile, incumbent, upper95=costs.ResourceDelta())
    assert not result.economic
    for value in [total, total.scaled(2), costs.Resources.measured(total),
                  ledger.estimate_validation([[total] * 3], probes=[total], split_cost=total),
                  costs.CostProfile(serving=total).total(), ledger.profile(deployment_tasks=1).total()]:
        assert value.resource_gaps
        assert value.interval("cpu_ns")[1] > value.interval("cpu_ns")[0]
        copy = type(value).model_validate_json(value.model_dump_json())
        assert copy.interval("cpu_ns") == value.interval("cpu_ns")


def test_review2_sign_uncertainty_is_indeterminate():
    ledger = costs.CostLedger()
    with ledger.measure("gap", category="serving"):
        with costs.consume_resources(CostVector(), child_process=True):
            pass
    profile = costs.CostProfile(serving=ledger.total("serving"))
    result = costs.compare_costs(profile, costs.CostProfile(), upper95=profile.total())
    assert result.sign_cases["cpu_s"].kind == "indeterminate"
    assert result.sign_cases["llm_calls"].kind == "dominates_from_zero"


def test_review2_shared_reference_arithmetic_is_integer(monkeypatch):
    from .test_exclusive_resources import graph_run
    from g6.distill.graph import KernelResult
    clock_ns(monkeypatch)
    monkeypatch.setattr(time, "thread_time", lambda: 0)
    monkeypatch.setattr(time, "perf_counter", lambda: 0)
    ids = iter(f"{i:064x}" for i in range(1, 100) if i != 3)
    monkeypatch.setattr(costs, "_id", lambda: next(ids))
    ledger = costs.CostLedger()
    with ledger.measure("canonical") as scope:
        report = scope.record_resources(CostVector(cpu_s=1), execution_id="f" * 64, event_id=f"{3:064x}")
        def kernel(*a, **kw):
            return KernelResult(output="concern", cost=CostVector(cpu_s=2 ** -53),
                                execution_ids=tuple(report.booked_execution_ids))
        record = graph_run(kernel, count=2)
    assert record.cost.cpu_ns == 1_000_000_000
    assert CostVector.model_validate_json(record.cost.model_dump_json()).cpu_ns == 1_000_000_000


@pytest.mark.parametrize("seed", range(8))
def test_operation_mixed_schedule_property(monkeypatch, seed):
    clock = clock_ns(monkeypatch)
    rng, ledger, nested = random.Random(seed), costs.CostLedger(), costs.CostLedger()
    actual, count = [0], [0]
    def unit(kind, fail):
        try:
            with costs.consume_resources(CostVector(**{kind: 1})):
                amount = rng.randrange(1, 10) * 1_000_000
                actual[0] += amount
                clock[0] += amount
                count[0] += 1
                if fail:
                    raise RuntimeError("body")
        except RuntimeError:
            pass
    async def tasks():
        async def task(kind):
            with costs.consume_resources(CostVector()):
                await asyncio.sleep(0)
                unit(kind, True)
        await asyncio.gather(task("probe_runs"), task("sandbox_runs"))
    with ledger.measure("mixed"):
        for _ in range(4):
            thread = threading.Thread(target=unit, args=("probe_runs", bool(rng.randrange(2))))
            thread.start()
            thread.join()
        asyncio.run(tasks())
        with nested.measure("nested"):
            unit("solver_calls", False)
        child_work(grandchild=True)
    total = ledger.total("acquisition")
    low, high = total.interval("cpu_ns")
    assert low <= actual[0] + 20_000_000 <= high
    assert low == actual[0]
    assert len(booked_operations(ledger)) == 1
    assert not booked_operations(nested)
    assert total.probe_runs + total.sandbox_runs + nested.total("acquisition").solver_calls == count[0]
    assert not getattr(costs._meters, "stack", ())
    assert costs.resource_owner() is None


@pytest.mark.parametrize("sampler", ["process_time_ns", "perf_counter_ns"])
@pytest.mark.parametrize("position", range(1, 11))
def test_operation_sampling_boundaries_keep_bounds_and_stack(monkeypatch, sampler, position):
    clock = clock_ns(monkeypatch)
    calls = [0]
    def read():
        calls[0] += 1
        if calls[0] == position:
            raise RuntimeError("sample boundary")
        return clock[0]
    monkeypatch.setattr(time, sampler, read)
    ledger = costs.CostLedger()
    with ledger.measure("sampling boundaries"):
        for _ in range(3):
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] += 1_000_000_000
    total = ledger.total("acquisition")
    assert total.probe_runs == 3
    assert total.interval("cpu_ns")[0] <= 3_000_000_000 <= total.interval("cpu_ns")[1]
    assert total.interval("wall_ns")[0] <= 3_000_000_000 <= total.interval("wall_ns")[1]
    assert len(booked_operations(ledger)) == 1
    assert not costs._meters.stack
    assert costs.resource_owner() is None


def test_operation_lock_serializes_preexisting_threads():
    first, second = costs.CostLedger(), costs.CostLedger()
    start, waiting, entered = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def other():
        try:
            assert start.wait(5)
            waiting.set()
            with second.measure("second"):
                entered.set()
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=other)
    thread.start()
    try:
        with first.measure("first"):
            start.set()
            assert waiting.wait(5)
            assert not entered.is_set()
    finally:
        start.set()
        thread.join(5)
    assert not errors and not thread.is_alive() and entered.is_set()
    assert len(booked_operations(first)) == len(booked_operations(second)) == 1


@pytest.mark.parametrize("seed", range(4))
def test_real_mixed_operation_bounds_independent_tree_cpu(monkeypatch, seed):
    rng, ledger, nested = random.Random(seed), costs.CostLedger(), costs.CostLedger()
    cpu_clock, samples = time.process_time_ns, []
    def sample():
        value = cpu_clock()
        samples.append(value)
        return value
    monkeypatch.setattr(time, "process_time_ns", sample)
    child_file = Path(".build") / f"operation-tree-{seed}.txt"
    child_file.unlink(missing_ok=True)
    burn = ("import time,pathlib; s=time.process_time_ns(); "
            "exec('while time.process_time_ns()-s<20000000: pass'); "
            f"pathlib.Path({str(child_file.resolve())!r}).write_text(str(time.process_time_ns()))")
    parent_file = Path(".build") / f"operation-parent-{seed}.txt"
    parent_file.unlink(missing_ok=True)
    source = (f"import subprocess,sys,time,pathlib; subprocess.run([sys.executable,'-I','-c',{burn!r}],check=True); "
              f"pathlib.Path({str(parent_file.resolve())!r}).write_text(str(time.process_time_ns()))")
    def unit():
        with costs.consume_resources(CostVector(probe_runs=1)):
            start = time.thread_time_ns()
            while time.thread_time_ns() - start < 5_000_000:
                pass
    async def tasks():
        async def task():
            with costs.consume_resources(CostVector(sandbox_runs=1)):
                await asyncio.sleep(0)
                unit()
        await asyncio.gather(task(), task())
    method = costs._ExclusiveMeter.pause if seed % 2 else costs._ExclusiveMeter.resume
    calls = [0]
    def sometimes(self):
        calls[0] += 1
        if calls[0] == seed + 1:
            raise RuntimeError("diagnostic boundary")
        return method(self)
    monkeypatch.setattr(costs._ExclusiveMeter, "pause" if seed % 2 else "resume", sometimes)
    with ledger.measure("real mixed"):
        threads = [threading.Thread(target=unit) for _ in range(rng.randrange(1, 4))]
        for thread in threads:
            thread.start()
        asyncio.run(tasks())
        with nested.measure("nested"):
            unit()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        subprocess.run([sys.executable, "-I", "-c", source], check=True)
    # First and last process samples bracket the booked operation exactly.
    independent = samples[-1] - samples[0] + int(child_file.read_text()) + int(parent_file.read_text())
    total = ledger.total("acquisition")
    assert total.interval("cpu_ns")[0] <= independent <= total.interval("cpu_ns")[1]
    assert len(booked_operations(ledger)) == 1 and not booked_operations(nested)
    assert total.probe_runs + nested.total("acquisition").probe_runs == len(threads) + 3
    assert total.sandbox_runs == 2
    assert not costs._meters.stack and costs.resource_owner() is None


def test_process_pool_spawn_retains_tree_interval():
    from concurrent.futures import ProcessPoolExecutor
    ledger = costs.CostLedger()
    with ledger.measure("process pool"):
        with ProcessPoolExecutor(max_workers=1) as pool:
            assert pool.submit(sum, range(100000)).result(timeout=15) == sum(range(100000))
    assert ledger.total("acquisition").resource_gaps
    assert ledger.total("acquisition").interval("cpu_ns")[1] > ledger.total("acquisition").cpu_ns


def test_estimate_counts_and_large_nanoseconds_remain_exact():
    count = 2 ** 54 + 1
    cost = costs.Resources.measured(CostVector(probe_runs=count, cpu_ns=2 ** 60 + 1))
    assert cost.probe_runs == count
    assert type(cost.probe_runs) is int
    assert (cost + costs.Resources(probe_runs=1)).probe_runs == count + 1
    assert cost.scaled(2).probe_runs == count * 2
    assert type(cost).model_validate_json(cost.model_dump_json()).cpu_ns == 2 ** 60 + 1


@pytest.mark.parametrize("fixed,variable", [(0, 0), (10, 10), (0, 10), (10, 0)])
def test_uncertain_dimension_sign_cases_are_conservative(fixed, variable):
    new = costs.CostProfile(acquisition=costs.Resources(cpu_ns=fixed, upper={"cpu_ns": fixed + 20}),
                            serving=costs.Resources(cpu_ns=variable, upper={"cpu_ns": variable + 20}))
    old = costs.CostProfile(acquisition=costs.Resources(cpu_ns=10), serving=costs.Resources(cpu_ns=10))
    result = costs.compare_costs(new, old, upper95=new.total())
    assert result.sign_cases["cpu_s"].kind == "indeterminate"
    assert result.difference.interval("cpu_ns")[1] == (fixed + 20 - 10) + 1000 * (variable + 20 - 10)


def test_non_lifo_operation_leases_retain_final_attempt(monkeypatch):
    clock = clock_ns(monkeypatch)
    first, second = costs.CostLedger(), costs.CostLedger()
    async def schedule():
        ready, leave = asyncio.Event(), asyncio.Event()
        async def a():
            with first.measure("first"):
                ready.set()
                await leave.wait()
                clock[0] += 1_000_000_000
        async def b():
            await ready.wait()
            with second.measure("second"):
                leave.set()
                await asyncio.sleep(0)
                clock[0] += 2_000_000_000
        await asyncio.gather(a(), b())
    asyncio.run(schedule())
    assert first.total("acquisition").cpu_ns == 3_000_000_000
    assert sum(row.cost.cpu_ns for row in first.attempts if row.time_booking == "operation") == 3_000_000_000
    assert not booked_operations(second)
    assert second.total("acquisition").resource_gaps


def test_public_sign_case_accepts_resource_intervals():
    assert costs.sign_case((-1, 1), (0, 2)).kind == "indeterminate"
    assert costs.sign_case((20, 20), (2, 2)).tasks == 10


def test_amortisation_uses_exact_integer_divisor():
    resource = costs.Resources(cpu_ns=10 ** 18)
    lower, upper = resource.divided(3).interval("cpu_ns")
    assert lower == 333_333_333_333_333_333
    assert upper == 333_333_333_333_333_334


def test_legacy_seconds_round_outward_to_nanosecond_interval():
    cost = CostVector(cpu_s=2 ** -53)
    assert cost.interval("cpu_ns") == (0, 1)
    assert (cost + cost + CostVector(cpu_s=1)).interval("cpu_ns") == (1_000_000_000, 1_000_000_002)


def test_operation_initialization_exception_books_known_interval(monkeypatch):
    clock = clock_ns(monkeypatch)
    def fail():
        clock[0] += 1_000_000_000
        raise RuntimeError("observer setup")
    monkeypatch.setattr(costs, "_install_spawn_observer", fail)
    ledger = costs.CostLedger()
    with pytest.raises(RuntimeError, match="observer setup"):
        with ledger.measure("setup"):
            raise AssertionError("body must not start")
    assert ledger.total("acquisition").cpu_ns == 1_000_000_000
    assert ledger.total("acquisition").wall_ns == 1_000_000_000
    assert not costs._meters.stack and costs.resource_owner() is None
    monkeypatch.setattr(costs, "_install_spawn_observer", lambda: None)
    with ledger.measure("recovery"):
        clock[0] += 1_000_000_000
    assert ledger.total("acquisition").cpu_ns == 2_000_000_000


def test_missing_both_wall_clocks_refuses_public_estimates(monkeypatch):
    clock = clock_ns(monkeypatch)
    def wall():
        if clock[0]:
            raise RuntimeError("clock unavailable")
        return 0
    monkeypatch.setattr(time, "perf_counter_ns", wall)
    monkeypatch.setattr(time, "monotonic_ns", wall)
    ledger = costs.CostLedger()
    with pytest.raises(ValueError, match="monotonic clock"):
        with ledger.measure("missing wall"):
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] = 1_000_000_000
    assert sum(e.cost.probe_runs for e in ledger.resource_events) == 1
    assert ledger.attempts[-1].resource_gaps
    with pytest.raises(ValueError, match="incomplete operation"):
        ledger.total("acquisition")
    assert not costs._meters.stack and costs.resource_owner() is None


def test_standalone_consuming_scope_books_one_operation(monkeypatch):
    clock = clock_ns(monkeypatch)
    with costs.consume_resources(CostVector(probe_runs=1)) as usage:
        with costs.consume_resources(CostVector(sandbox_runs=1)):
            clock[0] += 1_000_000_000
    assert usage.cost.cpu_ns == usage.cost.wall_ns == 1_000_000_000
    assert (usage.cost + usage.descendant_cost).probe_runs == 1
    assert (usage.cost + usage.descendant_cost).sandbox_runs == 1
    assert not costs._meters.stack and costs.resource_owner() is None
