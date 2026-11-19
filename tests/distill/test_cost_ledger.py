"""Vector costs, failed attempts and horizon sign cases."""
import importlib
import importlib.util

import pytest

from g6.core.llm import FakeLLM
from g6.distill.graph_records import CostVector


def completed_resources(meter, cost, *, execution_id=None):
    from uuid import uuid4
    from g6.core.ledger import digest
    return meter.record_resources(cost, execution_id=execution_id or digest(uuid4().hex),
        event_id=digest(uuid4().hex))


def api():
    assert importlib.util.find_spec("g6.distill.cost_ledger"), "P4 cost ledger is missing"
    return importlib.import_module("g6.distill.cost_ledger")


def test_exception_refusal_and_discard_are_measured_and_booked():
    ledger = api().CostLedger()
    with pytest.raises(RuntimeError):
        with ledger.measure("synthesis") as meter:
            meter.wrap(FakeLLM()).complete("", "")
            raise RuntimeError("failed candidate")
    for outcome in ("concept_refused", "no_candidate", "screened"):
        with ledger.measure("dev_screen") as meter:
            meter.outcome = outcome
    assert len(ledger.attempts) == 4
    assert ledger.attempts[0].outcome == "failed"
    assert all(a.cost.wall_s > 0 for a in ledger.attempts)
    assert ledger.total("acquisition").llm_calls == 1
    assert ledger.total("acquisition").wall_s > 0


@pytest.mark.parametrize("fixed,saving,kind,point", [(0, 0, "dominates_from_zero", None),
    (-1, 2, "dominates_from_zero", None), (20, 2, "break_even", 10),
    (20, 0, "never_breaks_even", None), (20, -2, "never_breaks_even", None),
    (-20, -2, "crossover", 10), (0, -2, "never_breaks_even", None),
    (-20, 0, "dominates_from_zero", None), (0, 2, "dominates_from_zero", None)])
def test_sign_cases(fixed, saving, kind, point):
    result = api().sign_case(fixed, saving)
    assert result.kind == kind and result.tasks == point


def test_crossover_is_never_reported_as_break_even():
    result = api().sign_case(-10, -2)
    assert result.kind == "crossover" and result.break_even_tasks is None
    assert result.crossover_tasks == 5


def test_validation_three_repeats_probes_and_one_fifth_split():
    a = api()
    ledger = a.CostLedger()
    estimate = ledger.estimate_validation([[CostVector(llm_calls=1)] * 3], probes=[CostVector(probe_runs=2)],
                           split_cost=a.Resources(llm_calls=1, cpu_s=10))
    assert estimate.llm_calls == 3.2
    assert estimate.probe_runs == 2
    assert estimate.cpu_s == 2
    assert ledger.total("validation") == a.Resources()
    with pytest.raises(ValueError, match="three"):
        ledger.estimate_validation([[CostVector()]], probes=[], split_cost=a.Resources())
    with pytest.raises(ValueError, match="execution ids"):
        ledger.book_validation([[CostVector()] * 3], probes=[], split_cost=a.Resources())


def test_horizon_uses_supplied_upper_bound_and_preserves_vectors():
    a = api()
    new = a.CostProfile(acquisition=a.Resources(llm_calls=20), serving=a.Resources(llm_calls=0.2),
                        maintenance_per_task=a.Resources(llm_calls=0.05))
    old = a.CostProfile(serving=a.Resources(llm_calls=1))
    assert new.total(1000).llm_calls == 270
    result = a.compare_costs(new, old, upper95=a.ResourceDelta(llm_calls=1))
    assert result.horizon == 1000 and result.economic is False
    assert result.difference.llm_calls == -730
    assert a.compare_costs(new, old, upper95=a.ResourceDelta()).economic is True
    with pytest.raises(ValueError):
        a.compare_costs(new, old, upper95=a.ResourceDelta(llm_calls=-731))


def test_maintenance_amortisation_and_registered_frontier_prices():
    a = api()
    ledger = a.CostLedger()
    from g6.core.ledger import digest
    with ledger.measure("cpu", category="maintenance") as meter:
        completed_resources(meter, CostVector(cpu_s=10), execution_id=digest("cpu execution"))
    ledger.book("maintenance", a.Resources(cpu_s=10), execution_id=digest("cpu execution"))
    for category, count in [("maintenance", 5), ("serving", 20)]:
        with ledger.measure(category, category=category) as meter:
            caller = meter.wrap(FakeLLM())
            for _ in range(count):
                caller.complete("", "")
    profile = ledger.profile(deployment_tasks=100)
    assert profile.variable.llm_calls == 0.25
    assert profile.maintenance_per_task.cpu_s >= 0.1
    price = a.RegisteredPrice(registration_sha256=digest("registered"), rates=a.Resources(llm_calls=2, cpu_s=1))
    assert price.frontier_cost(a.Resources(llm_calls=25, cpu_s=10)) == 60
    with pytest.raises(ValueError):
        ledger.profile(deployment_tasks=0)


def test_nested_measurement_is_rejected_before_work_and_ledger_recovers():
    ledger = api().CostLedger()
    caller = FakeLLM()
    with ledger.measure("outer") as meter:
        meter.wrap(caller).complete("", "")
        with pytest.raises(ValueError, match="nested|active"):
            with ledger.measure("inner"):
                caller.complete("", "")
    assert len(caller.calls) == 1
    assert ledger.total("acquisition").llm_calls == 1
    assert len(ledger.attempts) == 1
    with pytest.raises(RuntimeError):
        with ledger.measure("failure"):
            raise RuntimeError("failed")
    with ledger.measure("later") as meter:
        meter.wrap(caller).complete("", "")
    assert ledger.total("acquisition").llm_calls == 2
    assert len(ledger.attempts) == 3


def test_active_inner_booking_cannot_charge_again_at_outer_exit():
    from g6.distill.graph import attempt_meter
    ledger = api().CostLedger()
    with ledger.measure("outer") as meter:
        with attempt_meter("inner") as inner:
            meter.wrap(FakeLLM()).complete("input", "input")
        report = ledger.book_attempt(inner.record)
        assert report.booked_send_ids == inner.record.send_ids
    assert ledger.total("acquisition").llm_calls == 1
    assert len(ledger.attempts) == 2


def test_injected_caller_meter_is_thread_safe_and_keeps_exception_usage():
    from concurrent.futures import ThreadPoolExecutor
    ledger = api().CostLedger()
    def fail(system, user):
        raise RuntimeError("offline failure")
    with ledger.measure("workers") as meter:
        caller = meter.wrap(FakeLLM(default="reply"))
        assert meter.wrap(caller) is caller
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(lambda _: caller.complete("system", "input"), range(20))) == ["reply"] * 20
        with pytest.raises(RuntimeError, match="offline failure"):
            meter.wrap(FakeLLM(responder=fail)).complete("system", "input")
    assert ledger.total("acquisition").llm_calls == 21
    assert ledger.total("acquisition").llm_tokens_in > 0
    assert ledger.total("acquisition").llm_tokens_out > 0
    assert len(ledger.sends) == 21
    assert ledger.sends[-1].outcome == "failed_after_send"
    assert ledger.attempts[-1].cost.wall_s > 0
    with pytest.raises(ValueError, match="closed"):
        caller.complete("late", "input")


def test_meter_refuses_unaccounted_image_entry_before_send():
    class Caller:
        calls = 0
        def complete_with_images(self, *args, **kwargs):
            self.calls += 1
            return "image reply"
    ledger, raw = api().CostLedger(), Caller()
    with ledger.measure("images") as meter:
        with pytest.raises(ValueError, match="text"):
            meter.wrap(raw).complete_with_images("system", "user", [])
    assert raw.calls == 0
    assert ledger.total("acquisition").llm_calls == 0


def test_meter_counts_a_hookless_caller_on_an_existing_worker():
    from concurrent.futures import ThreadPoolExecutor
    class Caller:
        def complete(self, system, user, *, max_tokens):
            return "reply"
    ledger = api().CostLedger()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: None).result()
        with ledger.measure("hookless") as meter:
            assert pool.submit(meter.wrap(Caller()).complete, "system", "user").result() == "reply"
    assert ledger.total("acquisition").llm_calls == 1
    assert ledger.total("acquisition").llm_tokens_in == len("systemuser")
    assert ledger.total("acquisition").llm_tokens_out == len("reply")


def test_caller_boundary_refuses_second_inner_send():
    from g6.core.model_calls import note_model_call
    class Retrying:
        calls = 0
        def complete(self, system, user, *, max_tokens):
            for _ in range(2):
                note_model_call()
                self.calls += 1
            return "reply"
    ledger, raw = api().CostLedger(), Retrying()
    with ledger.measure("retry") as meter:
        with pytest.raises(ValueError, match="one send"):
            meter.wrap(raw).complete("system", "user")
    assert raw.calls == 1
    assert ledger.total("acquisition").llm_calls == 1


def test_refused_reply_is_retained_without_double_booking():
    class Caller:
        def complete(self, *args, **kwargs):
            return {"ok": False, "error": "refused"}
    ledger = api().CostLedger()
    with ledger.measure("refusal") as meter:
        assert meter.wrap(Caller()).complete("system", "user")["ok"] is False
    assert ledger.sends[0].outcome == "sent"
    assert ledger.total("acquisition").llm_calls == 1
    report = ledger.book_attempt(meter.record)
    assert report.already_booked_send_ids == meter.record.send_ids
    assert len(ledger.attempts) == 1


def test_review3_joined_graph_worker_books_one_call():
    from threading import Thread
    from g6.distill.graph import execute
    from .test_distill_operator import teacher_graph
    from .test_graph_records import D
    ledger, raw = api().CostLedger(), FakeLLM(default="concern")
    graph, primitive, config = teacher_graph()
    results = []
    with ledger.measure("joined graph") as meter:
        def run():
            results.append(execute(graph, {}, primitives={primitive.primitive_id: primitive},
                unit_ref=D, split="dev", request_id="worker", g6_commit="fixture",
                caller=raw, llm_configs={primitive.primitive_id: config}))
        worker = Thread(target=run)
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and results
        meter.record_execution(results[0])
    assert len(raw.calls) == ledger.total("acquisition").llm_calls == 1
    assert meter.record.send_ids == [ledger.sends[0].send_id]


def test_review3_lone_surrogate_completes_and_keeps_send():
    from threading import Event, Thread
    ledger, raw, done, errors = api().CostLedger(), FakeLLM(default="\ud800"), Event(), []
    def run():
        try:
            with ledger.measure("surrogate") as meter:
                meter.wrap(raw).complete("\udfff", "input")
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
    worker = Thread(target=run, daemon=True)
    worker.start()
    assert done.wait(1), "surrogate send did not release the active count"
    assert not errors
    assert len(raw.calls) == ledger.total("acquisition").llm_calls == 1
    assert ledger.total("acquisition").llm_tokens_out > 0


def test_review3_deferred_inner_booking_books_one_call():
    from g6.distill.graph import attempt_meter
    ledger, raw = api().CostLedger(), FakeLLM()
    with ledger.measure("outer") as meter:
        with attempt_meter("inner") as inner:
            meter.wrap(raw).complete("system", "user")
    ledger.book_attempt(inner.record)
    assert len(raw.calls) == ledger.total("acquisition").llm_calls == 1
    assert inner.record.send_ids == meter.record.send_ids
    assert ledger.booking_reports[-1].already_booked_send_ids == inner.record.send_ids


def test_review3_pre_send_rejection_books_zero_for_rejected_send():
    from g6.core.model_calls import counting
    ledger, raw = api().CostLedger(), FakeLLM()
    count = 0
    def allow_one():
        nonlocal count
        if count:
            raise ValueError("budget rejected before send")
        count += 1
    with ledger.measure("budget") as meter:
        with counting(before_call=allow_one):
            caller = meter.wrap(raw)
            caller.complete("s", "u")
            with pytest.raises(ValueError, match="budget"):
                caller.complete("s", "u")
    assert len(raw.calls) == ledger.total("acquisition").llm_calls == 1
    assert ledger.sends[-1].outcome == "refused_before_send"
    assert ledger.sends[-1].cost.llm_calls == 0


@pytest.mark.parametrize("seed", range(12))
def test_random_interleavings_booked_sends_equal_actual_inner_calls(seed, monkeypatch):
    import random
    import json
    import subprocess
    import sys
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from g6.distill.graph import attempt_meter
    from g6.distill.cost_ledger import consume_resources, report_child_resources
    rng, ledger, deferred = random.Random(seed), api().CostLedger(), []
    clock, independent, lock = threading.local(), [], threading.Lock()
    monkeypatch.setattr(api().time, "thread_time", lambda: getattr(clock, "cpu", 0.0))
    monkeypatch.setattr(api().time, "perf_counter", lambda: getattr(clock, "wall", 0.0))
    def tick(cpu, wall):
        clock.cpu = getattr(clock, "cpu", 0.0) + cpu
        clock.wall = getattr(clock, "wall", 0.0) + wall
        with lock:
            independent.append((cpu, wall))
    def reply(system, user):
        if user == "fail":
            raise RuntimeError("after send")
        return "ok"
    raw = FakeLLM(responder=reply)
    jobs = [(rng.randrange(3), rng.choice(["ok", "fail"]), rng.choice([True, False]),
             rng.randrange(1, 5), rng.randrange(1, 3)) for _ in range(20)]
    with ledger.measure("outer") as meter:
        caller = meter.wrap(raw)
        def job(spec):
            depth, text, early, probes, sandboxes = spec
            def nested(n):
                with attempt_meter("nested") as inner:
                    if n:
                        nested(n - 1)
                    else:
                        first, second = (CostVector(probe_runs=probes), CostVector(sandbox_runs=sandboxes))
                        if probes % 2:
                            first, second = second, first
                        with consume_resources(first):
                            tick(0.01, 0.02)
                            with consume_resources(second):
                                tick(0.03, 0.04)
                                completed_resources(meter, CostVector(solver_calls=1))
                                if early:
                                    with consume_resources(CostVector(), child_process=True):
                                        source = "import time; s=time.process_time(); exec('while time.process_time()-s<0.01: pass'); print(time.process_time()-s)"
                                        child_cpu = json.loads(subprocess.check_output(
                                            [sys.executable, "-I", "-c", source], text=True))
                                        report_child_resources(cpu_s=child_cpu, wall_s=0.01)
                                        with lock:
                                            independent.append((child_cpu, 0.0))
                                        tick(0.0, 0.01)
                        try:
                            caller.complete("system", text)
                        except RuntimeError:
                            pass
                if early:
                    ledger.book_attempt(inner.record)
                deferred.append(inner.record)
            nested(depth)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(job, jobs))
    rng.shuffle(deferred)
    for record in deferred * 2:
        ledger.book_attempt(record)
    assert len(raw.calls) == ledger.total("acquisition").llm_calls == len(jobs)
    assert len({s.send_id for s in ledger.sends}) == len(jobs)
    total = ledger.total("acquisition")
    assert total.probe_runs == sum(job[3] for job in jobs)
    assert total.sandbox_runs == sum(job[4] for job in jobs)
    assert total.solver_calls == 20
    assert total.cpu_s == pytest.approx(sum(cpu for cpu, _ in independent))
    assert total.wall_s == pytest.approx(sum(wall for _, wall in independent))
    for name in ("cpu_s", "wall_s", "solver_calls", "probe_runs", "sandbox_runs"):
        assert getattr(total, name) == pytest.approx(sum(getattr(e.cost, name) for e in ledger.resource_events))



def _colliding_completion_ids():
    # The first three small-set probe slots are 0, 1, 6 for these hashes.
    # Select against this process's hash seed, without relying on thread timing.
    ids = []
    for number in range(1_000_000):
        ident = f"{number:064x}"
        if hash(ident) & 0x7FFF == 0:
            ids.append(ident)
            if len(ids) == 3:
                return ids
    raise AssertionError("could not construct colliding completion ids")


@pytest.mark.parametrize("resource_field", ["cpu_s", "wall_s"])
def test_concurrent_completion_snapshot_books_in_canonical_id_order(resource_field, monkeypatch):
    from threading import Event, Thread
    from g6.distill.graph_records import AttemptRecord

    module, ledger = api(), api().CostLedger()
    monkeypatch.setattr(module.time, "thread_time", lambda: 0.0)
    monkeypatch.setattr(module.time, "perf_counter", lambda: 0.0)
    small_first, small_second, large = _colliding_completion_ids()
    first_done, second_done, errors = Event(), Event(), []
    costs = {small_first: 2 ** -53, small_second: 2 ** -53, large: 1.0}

    with ledger.measure("interleaved completions") as meter:
        # Use stable event ids as well as one execution alias for the completions.
        def complete(ident):
            meter.record_resources(CostVector(**{resource_field: costs[ident], "probe_runs": 1}),
                                   execution_id="f" * 64, event_id=ident)

        def worker():
            try:
                complete(large)
                first_done.set()
                assert second_done.wait(5), "second completion did not arrive"
                complete(small_first)
            except BaseException as error:
                errors.append(error)
                first_done.set()

        thread = Thread(target=worker)
        thread.start()
        try:
            assert first_done.wait(5), "first completion did not arrive"
            complete(small_second)
        finally:
            second_done.set()
            thread.join(5)
        assert not thread.is_alive() and not errors
        completion_ids = {large, small_second, small_first}
        cost = ledger.cost_for((), completion_ids)
        record = AttemptRecord(what="snapshot", cost=cost, model_sends=0,
            execution_ids=sorted(completion_ids), outcome="screened", update_id=None,
            started_at="snapshot", ended_at="snapshot")
        ledger.book_attempt(record)
        ledger.book_attempt(record)
        assert getattr(record.cost, resource_field) == 1.0000000000000002
        assert ledger.cost_for((), ["f" * 64, *reversed(sorted(completion_ids))]) == cost
    total = ledger.total("acquisition")
    assert getattr(total, resource_field) == 1.0000000000000002
    assert total.probe_runs == 3
    assert len([event for event in ledger.resource_events if event.cost.probe_runs]) == 3


@pytest.mark.parametrize("resource_field", ["cpu_s", "wall_s"])
def test_attempt_rejects_one_ulp_resource_difference(resource_field, monkeypatch):
    import math
    from g6.distill.graph_records import AttemptRecord

    module, ledger = api(), api().CostLedger()
    monkeypatch.setattr(module.time, "thread_time", lambda: 0.0)
    monkeypatch.setattr(module.time, "perf_counter", lambda: 0.0)
    with ledger.measure("exact resource check") as meter:
        completed_resources(meter, CostVector(**{resource_field: 1.0}))
    body = meter.record.model_dump(exclude={"attempt_id"})
    body["cost"][resource_field] = math.nextafter(1.0, 2.0)
    altered = AttemptRecord(**body)
    with pytest.raises(ValueError, match="attempt resources differ"):
        ledger.book_attempt(altered)
    assert getattr(ledger.total("acquisition"), resource_field) == 1.0


def test_worker_finishing_after_attempt_creates_separate_late_event(monkeypatch):
    from threading import Event, Thread, local

    module, ledger, clock = api(), api().CostLedger(), local()
    monkeypatch.setattr(module.time, "thread_time", lambda: getattr(clock, "cpu", 0.0))
    monkeypatch.setattr(module.time, "perf_counter", lambda: getattr(clock, "wall", 0.0))
    ready, finish, errors = Event(), Event(), []

    def worker():
        try:
            ready.set()
            assert finish.wait(5), "worker was not released"
            clock.cpu, clock.wall = 0.25, 0.5
        except BaseException as error:
            errors.append(error)

    try:
        with ledger.measure("late completion") as meter:
            thread = Thread(target=worker)
            thread.start()
            assert ready.wait(5), "worker did not start"
        snapshot = meter.record.model_dump_json()
        prior_events = {event.event_id: event.model_dump_json() for event in ledger.resource_events}
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    assert meter.record.model_dump_json() == snapshot
    events = {event.event_id: event for event in ledger.resource_events}
    assert {ident: events[ident].model_dump_json() for ident in prior_events} == prior_events
    late_ids = events.keys() - prior_events.keys()
    assert len(late_ids) == 1
    late = next(attempt for attempt in ledger.attempts if attempt.what == "worker:late completion")
    assert set(late.execution_ids) == late_ids
    assert late.cost.cpu_s == 0.25 and late.cost.wall_s == 0.5
    for record in (meter.record, late):
        ledger.book_attempt(record)
    assert ledger.total("acquisition").cpu_s == 0.25
    assert ledger.total("acquisition").wall_s == 0.5


def test_hook_counts_are_only_a_cross_check_and_unmetered_send_fails_closed():
    ledger = api().CostLedger()
    with pytest.raises(ValueError, match="unmetered_send"):
        with ledger.measure("unwrapped"):
            FakeLLM().complete("s", "u")
    assert ledger.attempts[-1].outcome == "unmetered_send"
    assert ledger.total("acquisition").llm_calls == 0
    assert not ledger.sends


def test_reported_model_usage_cannot_add_to_or_replace_proxy_usage():
    ledger = api().CostLedger()
    with ledger.measure("reported") as meter:
        meter.wrap(FakeLLM(default="reply")).complete("s", "u")
        completed_resources(meter, CostVector(llm_calls=99, llm_tokens_in=999, llm_tokens_out=999, probe_runs=3))
    total = ledger.total("acquisition")
    assert (total.llm_calls, total.llm_tokens_in, total.llm_tokens_out) == (1, 2, 5)
    assert total.probe_runs == 3


def test_booking_during_measurement_is_atomic_and_idempotent():
    from g6.distill.graph import attempt_meter
    ledger, raw = api().CostLedger(), FakeLLM()
    with ledger.measure("outer") as meter:
        with attempt_meter("inner") as inner:
            meter.wrap(raw).complete("s", "u")
        first = ledger.book_attempt(inner.record)
        again = ledger.book_attempt(inner.record)
        assert first.booked_send_ids == inner.record.send_ids
        assert again.already_booked_send_ids == inner.record.send_ids
    assert ledger.total("acquisition").llm_calls == len(raw.calls) == 1
    assert ledger.booking_reports[-1].already_booked_send_ids == inner.record.send_ids


def test_attempt_model_usage_must_match_its_send_ids():
    from g6.distill.graph_records import AttemptRecord
    ledger = api().CostLedger()
    with ledger.measure("source") as meter:
        meter.wrap(FakeLLM(default="reply")).complete("s", "u")
    bad = AttemptRecord(**(meter.record.model_dump(exclude={"attempt_id"}) | {
        "cost": CostVector(llm_calls=1), "model_sends": 1}))
    with pytest.raises(ValueError, match="proxy|send ids"):
        ledger.book_attempt(bad)
    assert ledger.total("acquisition").llm_tokens_out == 5


def test_usage_encoding_does_not_dispatch_to_text_subclass():
    class Text(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("custom encoder")
    ledger = api().CostLedger()
    with ledger.measure("text subclass") as meter:
        assert meter.wrap(FakeLLM(default=Text("\ud800"))).complete(Text("\udfff"), "u") == "\ud800"
    assert ledger.total("acquisition").llm_tokens_out == 3


def test_execution_ids_are_booked_once_even_when_an_attempt_is_rebooked():
    from g6.distill.graph import KernelResult, execute
    from g6.distill.cost_ledger import consume_resources
    from .test_graph_records import D, graph_for, primitive
    cost = CostVector(cpu_s=7, wall_s=9, probe_runs=3, sandbox_runs=2)
    p = primitive(budget=CostVector(cpu_s=10, wall_s=10, probe_runs=3, sandbox_runs=2))
    ledger = api().CostLedger()
    def kernel(*args, **kwargs):
        with consume_resources(cost):
            pass
        return KernelResult(output="concern")
    with ledger.measure("source", category="maintenance"):
        record = execute(graph_for([p]), {}, primitives={p.primitive_id: p}, unit_ref=D, split="dev",
            request_id="events", g6_commit="fixture", kernels={p.impl_ref: kernel})
    for _ in range(2):
        with ledger.measure("execution", category="maintenance") as meter:
            meter.record_execution(record)
        ledger.book_attempt(meter.record, category="maintenance")
    assert ledger.total("maintenance").probe_runs == 3
    assert ledger.total("maintenance").sandbox_runs == 2
    assert set(record.attempts[0].execution_ids) <= set(ledger.booking_reports[-1].already_booked_execution_ids)


def test_attempt_without_resource_references_keeps_its_existing_identity():
    from g6.core.ledger import digest
    from g6.distill.graph_records import AttemptRecord
    body = dict(what="legacy graph node", cost=CostVector(llm_calls=1, cpu_s=0.0, wall_s=0.0).model_dump(), model_sends=1,
        outcome="screened", update_id=None, started_at="start", ended_at="end")
    ident = digest(body)
    record = AttemptRecord.model_validate(body | {"attempt_id": ident})
    assert record.attempt_id == ident
    assert record.model_dump() == body | {"attempt_id": ident}
    with pytest.raises(ValueError, match="proxy send ids"):
        api().CostLedger().book_attempt(record)


def test_unidentified_booking_cannot_create_model_spend():
    ledger = api().CostLedger()
    with pytest.raises(ValueError, match="ids|unidentified"):
        ledger.book("acquisition", api().Resources(llm_calls=4, llm_tokens_in=99))
    assert ledger.total("acquisition").llm_calls == 0


def test_review4_probe_report_cannot_erase_measured_time(monkeypatch):
    a = api()
    clock = [0.0]
    monkeypatch.setattr(a.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(a.time, "thread_time", lambda: clock[0])
    ledger = a.CostLedger()
    with ledger.measure("probe") as meter:
        with meter.execution():
            clock[0] += 0.12
            completed_resources(meter, CostVector(probe_runs=2))
    total = ledger.total("acquisition")
    assert total.wall_s >= 0.12 and total.cpu_s >= 0.12
    assert total.probe_runs == 2


def test_review4_idless_non_model_attempt_is_refused():
    from g6.distill.graph_records import AttemptRecord
    ledger = api().CostLedger()
    record = AttemptRecord(what="unidentified", cost=CostVector(cpu_s=7, probe_runs=3, sandbox_runs=2),
        model_sends=0, outcome="failed", update_id=None, started_at="start", ended_at="end")
    with pytest.raises(ValueError, match="resource|execution|unidentified"):
        ledger.book_attempt(record)
    assert not ledger.attempts and not ledger.entries


def test_review4_child_and_parent_aggregate_book_three_probes():
    from g6.distill.graph import KernelResult, execute
    from g6.distill.cost_ledger import consume_resources
    from .test_graph_records import D, graph_for, primitive
    p = primitive(budget=CostVector(probe_runs=3, cpu_s=5, wall_s=5))
    ledger = api().CostLedger()
    def kernel(*args, **kwargs):
        with consume_resources(CostVector(probe_runs=3)):
            pass
        return KernelResult(output="concern")
    with ledger.measure("parent") as parent:
        with parent.scope("child") as child:
            rec = execute(graph_for([p]), {}, primitives={p.primitive_id: p},
                unit_ref=D, split="dev", request_id="resources", g6_commit="fixture",
                kernels={p.impl_ref: kernel})
            assert ledger.total("acquisition").probe_runs == 3
            child.record_execution(rec)
        parent.record_execution(rec)
    assert ledger.total("acquisition").probe_runs == 3


def test_review4_legacy_assignment_cannot_create_or_hide_spend():
    ledger = api().CostLedger()
    with ledger.measure("legacy") as meter:
        with pytest.raises((AttributeError, ValueError), match="cost|resource|assignment"):
            meter.cost = CostVector(probe_runs=3)
    assert ledger.total("acquisition").probe_runs == 0
    assert ledger.total("acquisition").wall_s > 0


def test_review4_bare_resource_reports_cannot_originate_spending():
    ledger = api().CostLedger()
    with ledger.measure("aggregate") as meter:
        with pytest.raises(ValueError, match="execution|event|ids"):
            meter.record_resources(CostVector(probe_runs=3))
    assert ledger.total("acquisition").probe_runs == 0


@pytest.mark.parametrize("with_references", [False, True])
def test_review4_kernel_aggregate_requires_child_event_references(with_references):
    from g6.distill.graph import KernelResult, execute
    from g6.distill.cost_ledger import resource_owner
    from .test_graph_records import D, graph_for, primitive
    p = primitive(budget=CostVector(probe_runs=6, cpu_s=5, wall_s=5))
    ledger = api().CostLedger()
    def kernel(*a, **kw):
        cost = CostVector(probe_runs=3)
        report = completed_resources(resource_owner(), cost)
        refs = {"execution_ids": tuple(report.booked_execution_ids)} if with_references else {}
        return KernelResult(output="concern", cost=cost, **refs)
    def run():
        return execute(graph_for([p]), {}, primitives={p.primitive_id: p}, unit_ref=D, split="dev",
            request_id="kernel-aggregate", g6_commit="fixture", kernels={p.impl_ref: kernel})
    with ledger.measure("aggregate"):
        if with_references:
            assert run().terminal == "ok"
        else:
            with pytest.raises(ValueError, match="aggregate|event ids"):
                run()
    assert ledger.total("acquisition").probe_runs == 3


def test_review4_probe_runner_books_completion_before_isolation_refusal(monkeypatch):
    from pathlib import Path
    from g6.distill import probes
    from g6.workforce.repo_tests import RepoRefused
    from .test_probes import invoke
    monkeypatch.setattr(probes, "_stage_run", lambda *a, **kw: {"isolation": {}})
    ledger = api().CostLedger()
    with ledger.measure("probe", category="maintenance"):
        with pytest.raises(RepoRefused, match="isolation"):
            invoke(Path(".build"))
        assert ledger.total("maintenance").probe_runs == 1


def test_review4_sandbox_completion_survives_bad_output(monkeypatch):
    from g6.distill import execution
    from g6.distill.records import VerifierCandidate
    from .test_records import bodies
    monkeypatch.setenv("G6_REQUIRE_ISOLATION", "1")
    monkeypatch.setattr(execution.sandbox, "run_batch", lambda *a, **kw: [])
    ledger = api().CostLedger()
    with ledger.measure("sandbox", category="maintenance"):
        with pytest.raises(execution.ExecutionRefused, match="count"):
            execution.run_candidate(VerifierCandidate(**bodies()[3][1]),
                [execution.Observation({}, {}, {})], lambda _: None)
        assert ledger.total("maintenance").sandbox_runs == 1


def test_review4_resource_event_retries_and_category_conflicts():
    from g6.core.ledger import digest
    ledger = api().CostLedger()
    execution_id, event_id = digest("runner"), digest("completion")
    with ledger.measure("owner", category="maintenance") as meter:
        cost = CostVector(probe_runs=3, sandbox_runs=2)
        first = meter.record_resources(cost, execution_id=execution_id, event_id=event_id)
        again = meter.record_resources(cost, execution_id=execution_id, event_id=event_id)
        assert first.booked_execution_ids == again.already_booked_execution_ids == [event_id]
        with pytest.raises(ValueError, match="conflicting"):
            meter.record_resources(CostVector(probe_runs=4), execution_id=execution_id, event_id=event_id)
        with pytest.raises((AttributeError, ValueError)):
            meter.category = "acquisition"
    with pytest.raises(ValueError, match="category|ownership"):
        ledger.book("acquisition", api().Resources(probe_runs=3, sandbox_runs=2), execution_id=execution_id)
    with pytest.raises(ValueError, match="category|ownership"):
        ledger.book_attempt(meter.record)
    ledger.book_attempt(meter.record, category="maintenance")
    assert ledger.total("maintenance").probe_runs == 3
    assert ledger.total("maintenance").sandbox_runs == 2
    assert ledger.total("acquisition").probe_runs == 0


def test_review4_aggregate_cannot_create_resources_or_change_event_cost():
    from g6.distill.graph_records import AttemptRecord
    from .test_shadow_audit import selected_record
    ledger = api().CostLedger()
    _, rec = selected_record()
    from g6.distill.graph_records import CostVector, ExecutionRecord, NodeRun
    cost = CostVector(probe_runs=3)
    row = NodeRun(**(rec.runs[0].model_dump() | {"cost": cost}))
    rec = ExecutionRecord(**(rec.model_dump(exclude={"run_id"}) | {"runs": [row], "cost": cost}))
    with ledger.measure("owner") as meter:
        with pytest.raises(ValueError, match="resource event ids"):
            meter.record_execution(rec)
        completed_resources(meter, cost)
    bad = AttemptRecord(**(meter.record.model_dump(exclude={"attempt_id"}) | {
        "cost": meter.record.cost + CostVector(sandbox_runs=2)}))
    with pytest.raises(ValueError, match="resource event ids"):
        ledger.book_attempt(bad)
    assert ledger.total("acquisition").probe_runs == 3
    assert ledger.total("acquisition").sandbox_runs == 0


@pytest.mark.parametrize("reference", ["aggregate", "event_retry"])
def test_review4_historical_references_cannot_cover_a_new_measured_interval(monkeypatch, reference):
    from g6.distill.graph import KernelResult, execute
    from .test_graph_records import D, graph_for, primitive
    a = api()
    ledger = a.CostLedger()
    p = primitive(budget=CostVector(cpu_s=10, wall_s=10))
    with ledger.measure("original"):
        rec = execute(graph_for([p]), {}, primitives={p.primitive_id: p}, unit_ref=D, split="dev",
            request_id="historical", g6_commit="fixture", kernels={p.impl_ref: lambda *a, **kw:
                KernelResult(output="concern", cost=CostVector(cpu_s=7, wall_s=9))})
    before = ledger.total("acquisition")
    clock = [0.0]
    monkeypatch.setattr(a.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(a.time, "thread_time", lambda: clock[0])
    with ledger.measure("later") as meter:
        if reference == "aggregate":
            meter.record_execution(rec)
        else:
            event = next(e for e in ledger.resource_events if e.event_id in rec.attempts[0].execution_ids)
            meter.record_resources(event.cost, execution_id=event.execution_id, event_id=event.event_id)
        clock[0] += 0.12
    total = ledger.total("acquisition")
    assert total.cpu_s >= before.cpu_s + 0.12
    assert total.wall_s >= before.wall_s + 0.12
