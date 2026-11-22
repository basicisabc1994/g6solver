"""Adapter observations cannot lose resource use at the attempt boundary."""
import pytest

from g6.core.llm import FakeLLM
from g6.distill.graph import KernelResult, LLMConfig, ReplayCache
from g6.distill.graph_records import CostVector, ExecutionRecord
from tests.distill.test_graph import api, run
from tests.distill.test_graph_records import cost, graph_for, primitive


# Every currently dispatched trusted adapter route uses the same accounting rule.
ADAPTERS = ("code", "solver", "g6_component", "llm")
REPORTED = tuple(name for name in CostVector.model_fields if name != "llm_calls")
REQUIRED = tuple(name for name in REPORTED if name != "wall_s")


def execute_observation(monkeypatch, kind, observed, *, local=0, terminal="ok"):
    """Use real dispatch and recording with controlled local clock observations."""
    clock = [10.0]
    monkeypatch.setattr(api().time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(api().time, "process_time", lambda: clock[0])
    config = LLMConfig(model_id="fixture", system="accounting", max_tokens=1)
    bindings = {}
    if kind == "llm":
        bindings = dict(impl_ref="model:fixture", impl_version=config.content_id)
    p = primitive(kind=kind, budget=cost(llm_calls=1, llm_tokens_in=10000,
                  llm_tokens_out=1, cpu_s=5, wall_s=5, solver_calls=1,
                  probe_runs=1, sandbox_runs=1), **bindings)

    def kernel(*args, **kwargs):
        clock[0] += local
        return KernelResult(output="no_concern", cost=observed, terminal=terminal)

    class Adapter(FakeLLM):
        def complete(self, *args, **kwargs):
            super().complete(*args, **kwargs)
            return kernel()

    cache = ReplayCache()
    kwargs = (dict(caller=Adapter(default="no_concern"), llm_configs={p.primitive_id: config})
              if kind == "llm" else dict(kernels={p.impl_ref: kernel}))
    rec = run(graph_for([p]), [p], cache=cache, **kwargs)
    assert len(rec.attempts) == 1
    assert rec.cost == rec.attempts[0].cost == rec.runs[0].cost
    assert ExecutionRecord.model_validate_json(rec.model_dump_json()) == rec
    return rec, cache


@pytest.mark.parametrize("kind", ADAPTERS)
@pytest.mark.parametrize("dimension", REPORTED)
def test_every_reported_resource_overrun_refuses_without_caching(monkeypatch, kind, dimension):
    # The limit for this dimension is at most 10000; local clocks report zero.
    rec, cache = execute_observation(monkeypatch, kind, cost(**{dimension: 10001}))
    assert getattr(rec.cost, dimension) == 10001
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.completion_state == "blocked-escalated"
    assert rec.attempts[0].outcome == "budget_exhausted"
    assert len(cache) == 0


@pytest.mark.parametrize("kind", ADAPTERS)
@pytest.mark.parametrize("dimension", REQUIRED)
def test_every_unmeasured_adapter_resource_stays_unknown(monkeypatch, kind, dimension):
    observed = CostVector(**cost().model_dump(exclude={dimension}))
    rec, cache = execute_observation(monkeypatch, kind, observed, local=0.25)
    assert getattr(rec.cost, dimension) is None
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.attempts[0].outcome == "failed"
    assert len(cache) == 0


@pytest.mark.parametrize("kind", ADAPTERS)
@pytest.mark.parametrize("dimension", ("cpu_s", "wall_s"))
def test_local_time_overrun_cannot_be_hidden_by_adapter_zero(monkeypatch, kind, dimension):
    rec, cache = execute_observation(monkeypatch, kind, cost(), local=6)
    assert getattr(rec.cost, dimension) == 6
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.attempts[0].outcome == "budget_exhausted"
    assert len(cache) == 0


@pytest.mark.parametrize("kind", ADAPTERS)
def test_overlapping_time_observations_are_not_added(monkeypatch, kind):
    rec, cache = execute_observation(monkeypatch, kind, cost(cpu_s=4, wall_s=4), local=3)
    assert rec.cost.cpu_s == rec.cost.wall_s == 4
    assert rec.terminal == "ok" and rec.root_output == "no_concern"
    assert len(cache) == 1


@pytest.mark.parametrize("kind", ADAPTERS)
def test_missing_adapter_wall_time_uses_full_elapsed_measurement(monkeypatch, kind):
    observed = CostVector(**cost().model_dump(exclude={"wall_s"}))
    rec, cache = execute_observation(monkeypatch, kind, observed, local=0.25)
    assert rec.cost.wall_s == rec.cost.cpu_s == 0.25
    assert rec.terminal == "ok" and len(cache) == 1


@pytest.mark.parametrize("kind", ADAPTERS)
@pytest.mark.parametrize("terminal", ("fail", "timeout", "refused", "abstain"))
def test_reported_overrun_survives_every_non_success_terminal(monkeypatch, kind, terminal):
    rec, cache = execute_observation(monkeypatch, kind, cost(cpu_s=6), terminal=terminal)
    assert rec.cost.cpu_s == 6
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.attempts[0].outcome == "budget_exhausted"
    assert len(cache) == 0


@pytest.mark.parametrize("kind", ADAPTERS)
def test_explicit_complete_zero_remains_admissible(monkeypatch, kind):
    rec, cache = execute_observation(monkeypatch, kind, cost())
    assert rec.terminal == "ok" and rec.root_output == "no_concern"
    assert rec.cost.cpu_s == rec.cost.wall_s == 0
    assert rec.cost.llm_calls == int(kind == "llm")
    assert len(cache) == 1
