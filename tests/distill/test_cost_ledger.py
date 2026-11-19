"""Vector costs, failed attempts and horizon sign cases."""
import importlib
import importlib.util

import pytest

from g6.core.llm import FakeLLM
from g6.distill.graph_records import CostVector


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
        meter.cost = results[0].cost
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
def test_random_interleavings_booked_sends_equal_actual_inner_calls(seed):
    import random
    from concurrent.futures import ThreadPoolExecutor
    from g6.distill.graph import attempt_meter
    rng, ledger, deferred = random.Random(seed), api().CostLedger(), []
    def reply(system, user):
        if user == "fail":
            raise RuntimeError("after send")
        return "ok"
    raw = FakeLLM(responder=reply)
    jobs = [(rng.randrange(3), rng.choice(["ok", "fail"]), rng.choice([True, False])) for _ in range(20)]
    with ledger.measure("outer") as meter:
        caller = meter.wrap(raw)
        def job(spec):
            depth, text, early = spec
            def nested(n):
                with attempt_meter("nested") as inner:
                    if n:
                        nested(n - 1)
                    else:
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
        meter.cost = CostVector(llm_calls=99, llm_tokens_in=999, llm_tokens_out=999, probe_runs=3)
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
    from .test_shadow_audit import selected_record
    from g6.distill.graph_records import ExecutionRecord, NodeRun
    _, record = selected_record()
    cost = CostVector(cpu_s=7, wall_s=9, probe_runs=3, sandbox_runs=2)
    run = NodeRun(**(record.runs[0].model_dump() | {"cost": cost}))
    record = ExecutionRecord(**(record.model_dump(exclude={"run_id"}) | {"cost": cost, "runs": [run]}))
    ledger = api().CostLedger()
    for _ in range(2):
        with ledger.measure("execution", category="maintenance") as meter:
            meter.record_execution(record)
        ledger.book_attempt(meter.record, category="maintenance")
    assert ledger.total("maintenance").probe_runs == 3
    assert ledger.total("maintenance").sandbox_runs == 2
    assert record.run_id in ledger.booking_reports[-1].already_booked_execution_ids


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
