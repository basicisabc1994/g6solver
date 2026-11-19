"""Resource attribution regressions at consuming boundaries."""
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from .operation_helpers import assert_diagnostic_cpu, assert_operation_booking, operation_clock

from g6.distill import cost_ledger as costs
from g6.distill.graph import KernelResult, execute
from g6.distill.graph_records import CostVector
from .test_graph_records import D, graph_for, primitive


def graph_run(kernel, *, count=1, probes=20, budget=None):
    ps = [primitive(impl_ref=f"code:meter-{i}",
                    budget=budget or CostVector(probe_runs=probes, cpu_s=10, wall_s=10)) for i in range(count)]
    return execute(graph_for(ps, combinator="seq", k=None), {}, primitives={p.primitive_id: p for p in ps},
                   unit_ref=D, split="dev", request_id="exclusive", g6_commit="fixture",
                   kernels={p.impl_ref: kernel for p in ps})


@pytest.mark.parametrize("resource", ["probe_runs", "sandbox_runs", "solver_calls"])
@pytest.mark.parametrize("with_references", [False, True])
def test_kernel_runner_counts_cross_check_references_without_duplicate_booking(resource, with_references):
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(**{resource: 3})) as usage:
            pass
        return KernelResult(output="concern", cost=CostVector(cpu_s=0.25, **{resource: 3}),
                            execution_ids=(usage.event_id,) if with_references else ())
    with ledger.measure("runner count"):
        if with_references:
            assert graph_run(kernel, budget=CostVector(cpu_s=10, wall_s=10, **{resource: 3})).terminal == "ok"
        else:
            with pytest.raises(ValueError, match="event ids"):
                graph_run(kernel)
    assert getattr(ledger.total("acquisition"), resource) == 3
    assert sum(getattr(e.cost, resource) for e in ledger.resource_events) == 3
    assert_diagnostic_cpu(ledger, 0.25)


@pytest.mark.parametrize("resource", ["probe_runs", "sandbox_runs", "solver_calls"])
def test_standalone_kernel_runner_counts_are_refused(resource):
    with pytest.raises(ValueError, match="event ids"):
        graph_run(lambda *a, **kw: KernelResult(output="concern", cost=CostVector(**{resource: 3})))


def _assert_referenced_runner_total_refused(resource, reported_total):
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(**{resource: 3})) as usage:
            pass
        return KernelResult(output="concern", cost=CostVector(cpu_s=0.25, **{resource: reported_total}),
                            execution_ids=(usage.event_id,))
    with ledger.measure("runner cross-check mismatch"):
        with pytest.raises(ValueError, match="event ids"):
            graph_run(kernel)
    assert getattr(ledger.total("acquisition"), resource) == 3
    assert sum(getattr(e.cost, resource) for e in ledger.resource_events) == 3
    assert_diagnostic_cpu(ledger, 0.25)


@pytest.mark.parametrize("resource", ["probe_runs", "sandbox_runs", "solver_calls"])
def test_referenced_runner_total_higher_than_events_is_refused(resource):
    _assert_referenced_runner_total_refused(resource, 4)


@pytest.mark.parametrize("resource", ["probe_runs", "sandbox_runs", "solver_calls"])
def test_referenced_runner_total_lower_than_events_is_refused(resource):
    _assert_referenced_runner_total_refused(resource, 2)


def test_referenced_runner_totals_cross_check_unique_events_across_aliases():
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=1, sandbox_runs=2), execution_id=D) as first:
            pass
        with costs.consume_resources(CostVector(probe_runs=2, solver_calls=4), execution_id=D) as second:
            pass
        return KernelResult(output="concern", cost=CostVector(cpu_s=0.25, probe_runs=3, sandbox_runs=2, solver_calls=4),
                            execution_ids=(D, first.event_id, first.event_id, second.event_id))
    with ledger.measure("unique runner cross-check"):
        assert graph_run(kernel, budget=CostVector(cpu_s=10, wall_s=10, probe_runs=3,
                                                  sandbox_runs=2, solver_calls=4)).terminal == "ok"
    total = ledger.total("acquisition")
    assert (total.probe_runs, total.sandbox_runs, total.solver_calls) == (3, 2, 4)
    assert sum(e.cost.probe_runs for e in ledger.resource_events) == 3
    assert sum(e.cost.sandbox_runs for e in ledger.resource_events) == 2
    assert sum(e.cost.solver_calls for e in ledger.resource_events) == 4
    assert_diagnostic_cpu(ledger, 0.25)


def test_referenced_runner_cross_check_refuses_omitted_nonzero_field():
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=3, sandbox_runs=2)) as usage:
            pass
        return KernelResult(output="concern", cost=CostVector(cpu_s=0.25, probe_runs=3),
                            execution_ids=(usage.event_id,))
    with ledger.measure("incomplete runner cross-check"):
        with pytest.raises(ValueError, match="event ids"):
            graph_run(kernel)
    total = ledger.total("acquisition")
    assert (total.probe_runs, total.sandbox_runs) == (3, 2)
    assert_diagnostic_cpu(ledger, 0.25)


@pytest.mark.parametrize("invalid", ["output", "trace", "terminal"])
def test_review1_invalid_kernel_output_retains_three_probes(invalid):
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=3)):
            pass
        return KernelResult(**{invalid: object()})
    with ledger.measure("invalid"):
        rec = graph_run(kernel)
    assert ledger.total("acquisition").probe_runs == rec.cost.probe_runs == 3
    assert rec.terminal == "fail"


@pytest.mark.parametrize("refuse", [False, True])
def test_review1_kernel_own_consumption_separate_from_child_references(refuse):
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=3)):
            pass
        with costs.consume_resources(CostVector(probe_runs=2)):
            pass
        return KernelResult(output="concern", terminal="refused" if refuse else "ok",
                            cost=CostVector(cpu_s=2))
    with ledger.measure("own"):
        rec = graph_run(kernel)
    assert ledger.total("acquisition").probe_runs == rec.cost.probe_runs == 5
    assert rec.cost.interval("cpu_s")[1] >= 2
    assert_diagnostic_cpu(ledger, 2)
    assert rec.terminal == ("refused" if refuse else "ok")


def test_review1_nested_probe_in_sandbox_charges_one_interval(monkeypatch):
    clock = [0.0]
    operation_clock(monkeypatch, clock)
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(time, "thread_time", lambda: clock[0])
    monkeypatch.setattr(time, "process_time", lambda: clock[0])
    ledger = costs.CostLedger()
    with ledger.measure("nested"):
        with costs.consume_resources(CostVector(sandbox_runs=1)):
            with costs.consume_resources(CostVector(probe_runs=1)):
                clock[0] += 0.12
    total = ledger.total("acquisition")
    assert (total.probe_runs, total.sandbox_runs) == (1, 1)
    assert total.cpu_s == total.wall_s == pytest.approx(0.12)
    parent = next(e for e in ledger.resource_events if e.cost.sandbox_runs)
    child = next(e for e in ledger.resource_events if e.cost.probe_runs)
    assert parent.cost.cpu_s == parent.cost.wall_s == 0
    assert parent.child_event_ids == [child.event_id]
    assert ledger.cost_for((), (parent.execution_id,)).interval("cpu_s")[1] >= 0.12
    assert_operation_booking(ledger)


def test_review1_concurrent_workers_do_not_share_process_cpu():
    ledger, barrier, done = costs.CostLedger(), threading.Barrier(2), threading.Event()
    own = {}
    def worker(burn):
        with costs.consume_resources(CostVector(probe_runs=int(burn), sandbox_runs=int(not burn))):
            barrier.wait()
            start = time.thread_time()
            if burn:
                while time.thread_time() - start < 0.12:
                    pass
                done.set()
            else:
                assert done.wait(5)
            own[burn] = time.thread_time() - start
    start = time.process_time()
    with ledger.measure("workers"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, (True, False)))
    independent = time.process_time() - start
    events = ledger.resource_events
    burner = next(e for e in events if e.cost.probe_runs)
    waiter = next(e for e in events if e.cost.sandbox_runs)
    assert burner.diagnostic_cost.cpu_s == pytest.approx(own[True], abs=0.02)
    assert waiter.diagnostic_cost.cpu_s < 0.02
    assert burner.cost.cpu_ns == waiter.cost.cpu_ns == 0
    assert_operation_booking(ledger)
    assert ledger.total("acquisition").cpu_s <= independent + 0.02


@pytest.mark.parametrize("measured", [False, True])
@pytest.mark.parametrize("exit_kind", ["success", "failure", "timeout", "exception"])
def test_review1_child_cpu_is_reported_or_explicitly_unmeasured(measured, exit_kind):
    ledger = costs.CostLedger()
    source = "import time,json; s=time.process_time(); exec('while time.process_time()-s<0.03: pass'); print(json.dumps(time.process_time()-s))"
    child_cpu = 0
    with ledger.measure("child") as scope:
        try:
            with costs.consume_resources(CostVector(probe_runs=1), child_process=True):
                started = time.monotonic()
                child_cpu = json.loads(subprocess.check_output([sys.executable, "-I", "-c", source], text=True))
                if measured:
                    costs.report_child_resources(cpu_s=child_cpu, wall_s=time.monotonic() - started)
                if exit_kind != "success":
                    raise TimeoutError(exit_kind) if exit_kind == "timeout" else RuntimeError(exit_kind)
        except (RuntimeError, TimeoutError):
            pass
    event = next(e for e in ledger.resource_events if e.cost.probe_runs)
    if measured:
        assert event.child_cpu_s == child_cpu
        assert event.diagnostic_cost.cpu_s >= child_cpu > 0
        assert event.child_cpu_status == "measured"
        assert scope.record.resource_gaps
        assert ledger.total("acquisition").interval("cpu_ns")[1] > ledger.total("acquisition").cpu_ns
    else:
        assert event.child_cpu_s is None
        assert event.child_cpu_status == "unmeasured"
        assert scope.record.resource_gaps
        profile = ledger.profile(deployment_tasks=1000)
        assert profile.resource_gaps
        assert profile.total().resource_gaps == profile.resource_gaps
        comparison = costs.compare_costs(profile, costs.CostProfile(), upper95=profile.total())
        assert comparison.resource_gaps and not comparison.economic
    assert event.child_wall_s > 0


def test_review1_shared_event_references_count_once_in_execution():
    ledger = costs.CostLedger()
    with ledger.measure("shared") as scope:
        report = scope.record_resources(CostVector(probe_runs=3), execution_id=costs._id(), event_id=costs._id())
        refs = tuple(report.booked_execution_ids)
        rec = graph_run(lambda *a, **kw: KernelResult(output="no_concern", execution_ids=refs), count=2)
        scope.record_execution(rec)
    assert rec.cost.probe_runs == ledger.total("acquisition").probe_runs == 3
    assert sum(node.cost.probe_runs for node in rec.runs) == 0
    assert rec.referenced_cost.probe_runs == 3
    assert rec.referenced_execution_ids == list(refs)
    ids = [ident for a in rec.attempts for ident in a.execution_ids]
    assert len(ids) == len(set(ids))


def test_bad_reference_does_not_discard_kernel_own_consumption():
    ledger = costs.CostLedger()
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=2)):
            pass
        return KernelResult(output="concern", cost=CostVector(cpu_s=0.25), execution_ids=(D,))
    with ledger.measure("bad reference"):
        with pytest.raises(ValueError, match="unknown|event"):
            graph_run(kernel)
    assert ledger.total("acquisition").probe_runs == 2
    assert_diagnostic_cpu(ledger, 0.25)


def test_bare_workers_inherit_scope_and_book_their_thread_cpu():
    ledger = costs.CostLedger()
    def burn():
        start = time.thread_time()
        while time.thread_time() - start < 0.06:
            pass
    start = time.process_time()
    with ledger.measure("bare workers"):
        thread = threading.Thread(target=burn)
        thread.start()
        thread.join()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(burn).result()
    total = ledger.total("acquisition").cpu_s
    assert 0.10 <= total <= time.process_time() - start + 0.02
    assert sum(e.diagnostic_cost.cpu_s >= 0.05 for e in ledger.resource_events if e.time_booking == "diagnostic") >= 2
    assert_operation_booking(ledger)


@pytest.mark.parametrize("kind", ["probe", "sandbox"])
@pytest.mark.parametrize("measured", [False, True])
@pytest.mark.parametrize("exit_kind", ["success", "failure", "timeout", "exception"])
def test_child_backend_completion_survives_runner_validation(monkeypatch, kind, measured, exit_kind):
    from pathlib import Path
    from types import SimpleNamespace
    from g6.distill import execution, probes
    from g6.distill.records import VerifierCandidate
    from .test_probes import invoke
    from .test_records import bodies

    child_cpu = []
    def backend(*args, **kwargs):
        started = time.perf_counter()
        source = "import time; s=time.process_time(); exec('while time.process_time()-s<0.03: pass'); print(time.process_time()-s)"
        cpu = float(subprocess.check_output([sys.executable, "-I", "-c", source], text=True))
        child_cpu.append(cpu)
        if measured:
            costs.report_child_resources(cpu_s=cpu, wall_s=time.perf_counter() - started)
        if exit_kind == "timeout":
            raise TimeoutError("child timeout")
        if exit_kind == "exception":
            raise RuntimeError("child exception")
        if kind == "sandbox":
            return [] if exit_kind == "failure" else [SimpleNamespace(
                ok=True, value={"verdict": "concern", "reason": "offline child"})]
        return {"isolation": {} if exit_kind == "failure" else {"backend": "offline-fake", "network": False}}

    monkeypatch.setattr(execution.sandbox, "run_batch", backend)
    monkeypatch.setattr(probes, "_stage_run", backend)
    monkeypatch.setattr(probes.repo_tests, "established", lambda *a, **kw: SimpleNamespace(of=lambda: "failed"))
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    ledger = costs.CostLedger()
    with ledger.measure("child backend") as scope:
        try:
            if kind == "probe":
                invoke(Path(".build"))
            else:
                execution.run_candidate(VerifierCandidate(**bodies()[3][1]),
                    [execution.Observation({}, {}, {})], lambda _: None)
        except (RuntimeError, TimeoutError, execution.ExecutionRefused, probes.repo_tests.RepoRefused):
            if exit_kind == "success":
                raise
    event = next(e for e in ledger.resource_events if e.cost.probe_runs or e.cost.sandbox_runs)
    assert (event.cost.probe_runs, event.cost.sandbox_runs) == ((1, 0) if kind == "probe" else (0, 1))
    assert event.child_cpu_status == ("measured" if measured else "unmeasured")
    assert event.child_cpu_s == (child_cpu[0] if measured else None)
    assert scope.record.resource_gaps
    assert_operation_booking(ledger)
    assert event.child_wall_s > 0


def test_child_completion_retry_preserves_metadata_and_gap():
    ledger = costs.CostLedger()
    with ledger.measure("child retry") as scope:
        with costs.consume_resources(CostVector(probe_runs=1), child_process=True):
            pass
        event = next(e for e in ledger.resource_events if e.cost.probe_runs)
        metadata = event.model_dump(exclude={"execution_id", "event_id", "category", "cost"})
        report = scope.record_resources(event.cost, execution_id=event.execution_id, event_id=event.event_id,
                                        **metadata)
        assert report.already_booked_execution_ids == [event.event_id]
    assert ledger.total("acquisition").probe_runs == 1
    assert scope.record.resource_gaps == ledger.profile(deployment_tasks=1000).resource_gaps


def test_attempt_cannot_omit_a_child_cpu_gap():
    from g6.distill.graph_records import AttemptRecord
    ledger = costs.CostLedger()
    with ledger.measure("child gap") as scope:
        with costs.consume_resources(CostVector(probe_runs=1), child_process=True):
            pass
    forged = AttemptRecord(**(scope.record.model_dump(exclude={"attempt_id"}) | {"resource_gaps": []}))
    with pytest.raises(ValueError, match="gap"):
        ledger.book_attempt(forged)


@pytest.mark.parametrize("terminal", ["ok", "refused", "bad_output"])
def test_kernel_own_work_and_shared_reference_remain_separate(terminal):
    ledger = costs.CostLedger()
    with ledger.measure("own plus reference") as scope:
        report = scope.record_resources(CostVector(probe_runs=3), execution_id=costs._id(), event_id=costs._id())
        def kernel(*args, **kwargs):
            with costs.consume_resources(CostVector(probe_runs=2)):
                pass
            return KernelResult(output=object() if terminal == "bad_output" else "concern",
                                terminal="refused" if terminal == "refused" else "ok",
                                cost=CostVector(cpu_s=2), execution_ids=tuple(report.booked_execution_ids))
        rec = graph_run(kernel)
    assert rec.cost.probe_runs == ledger.total("acquisition").probe_runs == 5
    assert rec.referenced_cost.probe_runs == 3
    assert sum(n.cost.probe_runs for n in rec.runs) == 2
    assert rec.cost.interval("cpu_s")[1] >= 2
    assert_diagnostic_cpu(ledger, 2)
    assert rec.terminal == ("fail" if terminal == "bad_output" else terminal)


def test_standalone_graph_retains_nested_completion_time(monkeypatch):
    clock = [0.0]
    operation_clock(monkeypatch, clock)
    monkeypatch.setattr(time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(time, "thread_time", lambda: clock[0])
    def kernel(*args, **kwargs):
        with costs.consume_resources(CostVector(probe_runs=1)):
            clock[0] += 0.12
        with costs.consume_resources(CostVector(probe_runs=2)):
            pass
        return KernelResult(output="concern")
    rec = graph_run(kernel)
    assert rec.cost.cpu_s == rec.cost.wall_s == pytest.approx(0.12)
    assert rec.cost.probe_runs == 3
