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
    (-20, -2, "crossover", 10), (0, -2, "crossover", 0)])
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
