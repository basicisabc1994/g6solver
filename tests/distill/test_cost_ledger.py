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
        with ledger.measure("synthesis"):
            FakeLLM().complete("", "")
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
    ledger.book_validation([[CostVector(llm_calls=1)] * 3], probes=[CostVector(probe_runs=2)],
                           split_cost=a.Resources(llm_calls=1, cpu_s=10))
    assert ledger.total("validation").llm_calls == 3.2
    assert ledger.total("validation").probe_runs == 2
    assert ledger.total("validation").cpu_s == 2
    with pytest.raises(ValueError, match="three"):
        ledger.book_validation([[CostVector()]], probes=[], split_cost=a.Resources())


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
    ledger.book("maintenance", a.Resources(llm_calls=5, cpu_s=10))
    ledger.book("serving", a.Resources(llm_calls=20))
    profile = ledger.profile(deployment_tasks=100)
    assert profile.variable.llm_calls == 0.25
    assert profile.maintenance_per_task.cpu_s == 0.1
    from g6.core.ledger import digest
    price = a.RegisteredPrice(registration_sha256=digest("registered"), rates=a.Resources(llm_calls=2, cpu_s=1))
    assert price.frontier_cost(profile.total(100)) == 60
    with pytest.raises(ValueError):
        ledger.profile(deployment_tasks=0)


def test_nested_measurement_is_rejected_before_work_and_ledger_recovers():
    ledger = api().CostLedger()
    caller = FakeLLM()
    with ledger.measure("outer"):
        caller.complete("", "")
        with pytest.raises(ValueError, match="nested|active"):
            with ledger.measure("inner"):
                caller.complete("", "")
    assert len(caller.calls) == 1
    assert ledger.total("acquisition").llm_calls == 1
    assert len(ledger.attempts) == 1
    with pytest.raises(RuntimeError):
        with ledger.measure("failure"):
            raise RuntimeError("failed")
    with ledger.measure("later"):
        caller.complete("", "")
    assert ledger.total("acquisition").llm_calls == 2
    assert len(ledger.attempts) == 3


def test_external_inner_booking_is_refused_during_measurement():
    from g6.distill.graph import attempt_meter
    ledger = api().CostLedger()
    with ledger.measure("outer"):
        with attempt_meter("inner") as inner:
            FakeLLM().complete("input", "input")
        with pytest.raises(ValueError, match="active|measurement"):
            ledger.book_attempt(inner.record)
    assert ledger.total("acquisition").llm_calls == 1
    assert len(ledger.attempts) == 1


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
    assert ledger.sends[-1].outcome == "exception"
    assert ledger.sends[-1].cost.wall_s > 0
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
    assert ledger.sends[0].outcome == "refused"
    assert ledger.total("acquisition").llm_calls == 1
    with pytest.raises(ValueError, match="already booked"):
        ledger.book_attempt(meter.record)
    assert len(ledger.attempts) == len(ledger.entries) == 1
