"""Missing resource observations cannot supply budget headroom or savings."""
import pytest
from pydantic import ValidationError

from g6.core.llm import FakeLLM
from tests.distill.test_graph import api, lower_api, run
from tests.distill.test_graph_records import graph_for, primitive, records


def complete(**changes):
    r = records()
    return r.CostVector(**(r.CostVector.zero().model_dump() | changes))


@pytest.mark.parametrize("dimension", ["llm_tokens_in", "llm_tokens_out", "probe_runs",
                                       "solver_calls", "sandbox_runs"])
def test_omitted_callback_accounting_cannot_admit_result(dimension):
    a, r = api(), records()
    p = primitive(budget=complete(wall_s=5, cpu_s=5, **{dimension: 1}))
    cache = a.ReplayCache()
    observed = r.CostVector(**complete().model_dump(exclude={dimension}))
    rec = run(graph_for([p]), [p], cache=cache, kernels={p.impl_ref: lambda *args, **kw:
              a.KernelResult(output="no_concern", cost=observed)})
    assert getattr(rec.cost, dimension) is None
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.completion_state == "blocked-escalated"
    assert rec.attempts[0].cost == rec.cost == rec.runs[0].cost
    assert len(cache) == 0


def test_complete_observed_zero_survives(monkeypatch):
    a = api()
    monkeypatch.setattr(a.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(a.time, "process_time", lambda: 10.0)
    p = primitive(budget=complete(wall_s=5, cpu_s=5))
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw:
              a.KernelResult(output="concern", cost=complete())})
    assert rec.terminal == "ok" and rec.root_output == "concern"
    assert rec.cost == complete()


@pytest.mark.parametrize("dimension", list(records().CostVector.model_fields))
def test_unknown_dimension_survives_addition_roundtrip_and_comparison(dimension):
    r = records()
    partial = r.CostVector(**complete().model_dump(exclude={dimension}))
    known = complete(**{dimension: 1})
    merged = known + partial
    assert getattr(merged, dimension) is None
    assert getattr(partial + known, dimension) is None
    assert getattr(complete() + partial, dimension) is None
    assert all(value == 0 for name, value in merged.model_dump().items() if name != dimension)
    assert getattr(known + known, dimension) == 2
    assert r.CostVector.model_validate_json(partial.model_dump_json()) == partial
    assert not partial.within(complete())
    assert not complete().within(partial)
    assert complete().within(complete())


def test_default_observation_is_distinct_from_zero():
    r = records()
    assert r.CostVector() != r.CostVector.zero()
    assert all(value is None for value in r.CostVector().model_dump().values())


def test_typed_comparison_distinguishes_unknown_from_known_excess():
    r = records()
    partial = r.CostVector(**complete(probe_runs=2).model_dump(exclude={"llm_tokens_in"}))
    assert partial.comparison(complete(probe_runs=2)) == "unknown"
    assert partial.comparison(complete(probe_runs=1)) == "exceeded"
    assert complete().comparison(complete()) == "within"


@pytest.mark.parametrize("location", ["graph", "node"])
def test_unknown_graph_or_node_budget_is_refused(location):
    r = records()
    p = primitive(budget=complete(wall_s=5, cpu_s=5))
    body = graph_for([p]).model_dump(exclude={"graph_id"})
    budget = body["budget"] if location == "graph" else body["nodes"][0]["budget"]
    del budget["probe_runs"]
    with pytest.raises(ValidationError):
        r.ProgramGraph(**body)


def test_lowering_refuses_unknown_primitive_reservation_before_callback():
    r = records()
    p = primitive(budget=complete(wall_s=5, cpu_s=5))
    body = graph_for([p]).model_dump(exclude={"graph_id"})
    incomplete = primitive(budget=r.CostVector(**p.budget.model_dump(exclude={"probe_runs"})))
    body["nodes"][0]["primitive_id"] = incomplete.primitive_id
    graph = r.ProgramGraph(**body)
    with pytest.raises(lower_api().LoweringRefused):
        lower_api().validated(graph, {incomplete.primitive_id: incomplete})
    calls = []
    rec = run(graph, [incomplete], kernels={incomplete.impl_ref: lambda *args, **kw: calls.append(1)})
    assert rec.terminal == "refused" and calls == [] and rec.attempts == []
    assert rec.cost == complete()


def test_failure_retains_unknown_resources_and_measured_work(monkeypatch):
    a = api()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    caller = FakeLLM(default="concern")
    p = primitive(budget=complete(llm_calls=1, wall_s=5, cpu_s=5))

    def fail(*args, **kwargs):
        caller.complete("fixture", "fixture")
        clock[0] += 0.5
        raise ValueError("fixture failure")

    rec = run(graph_for([p]), [p], kernels={p.impl_ref: fail})
    assert rec.cost.probe_runs is None and rec.cost.llm_tokens_out is None
    # Lead ruling 11-23: CPU no adapter reported stays unknown (local process time misses child CPU).
    assert rec.cost.llm_calls == 1 and rec.cost.wall_s == 0.5 and rec.cost.cpu_s is None
    assert rec.terminal == "fail" and rec.root_output is None
    assert rec.attempts[0].outcome == "failed"
    assert rec.attempts[0].cost == rec.runs[0].cost == rec.cost


def test_execution_and_attempt_totals_preserve_unknown_dimensions():
    from g6.core.ledger import digest
    from tests.distill.test_graph_records import D
    r = records()
    partial = r.CostVector(**complete().model_dump(exclude={"probe_runs"}))
    costs = [complete(probe_runs=1), partial]
    runs = [r.NodeRun(node_id=str(i), input_sha256=D, output_sha256=digest(None),
                      output=None, terminal="fail", cost=cost, trace_ref=None, replayable=False)
            for i, cost in enumerate(costs)]
    attempts = [r.AttemptRecord(what=str(i), cost=cost, model_sends=0, outcome="failed", update_id=None,
                                started_at="start", ended_at="end") for i, cost in enumerate(costs)]
    body = dict(graph_id=D, unit_ref=D, split="training", request_id="fixture", g6_commit="fixture",
                model_ids={}, seed=0, runs=runs, attempts=attempts, root_output=None, terminal="fail",
                cost=partial, completion_state="blocked-escalated", started_at="start", ended_at="end")
    rec = r.ExecutionRecord(**body)
    assert rec.cost.probe_runs is None
    assert r.ExecutionRecord.model_validate_json(rec.model_dump_json()) == rec
    with pytest.raises(ValidationError):
        r.ExecutionRecord(**(body | {"cost": complete(probe_runs=1)}))


def test_measured_overrun_is_not_hidden_by_unknown_usage(monkeypatch):
    a = api()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    p = primitive(budget=complete(wall_s=5, cpu_s=5))

    def kernel(*args, **kwargs):
        clock[0] += 6
        return a.KernelResult(output="concern")

    rec = run(graph_for([p]), [p], kernels={p.impl_ref: kernel})
    assert rec.cost.probe_runs is None and rec.cost.wall_s == 6
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.attempts[0].outcome == "budget_exhausted"


def test_unknown_usage_stops_later_callback():
    a = api()
    ps = [primitive(impl_ref=name, budget=complete(wall_s=5, cpu_s=5)) for name in ("first", "second")]
    calls = []

    def kernel(p, *args, **kwargs):
        calls.append(p.impl_ref)
        return a.KernelResult(output="abstain")

    rec = run(graph_for(ps, combinator="cascade", k=None), ps,
              kernels={p.impl_ref: kernel for p in ps})
    assert rec.terminal == "refused" and calls == ["first"]
    assert rec.cost.probe_runs is None


def test_cache_cannot_store_incomplete_observation():
    a, r = api(), records()
    from g6.core.ledger import digest
    cache = a.ReplayCache()
    row = r.NodeRun(node_id="n0", input_sha256=digest({}), output_sha256=digest("concern"),
                    output="concern", terminal="ok", cost=r.CostVector(), trace_ref=None,
                    replayable=True)
    cache.put("fixture", row)
    assert cache.get("fixture") is None and len(cache) == 0


def test_unknown_runtime_limit_cannot_admit_cache_hit():
    a, r = api(), records()
    p = primitive(budget=complete(wall_s=5, cpu_s=5))
    cache = a.ReplayCache()
    calls = []

    def kernel(*args, **kwargs):
        calls.append(1)
        return a.KernelResult(output="concern", cost=complete())

    graph = graph_for([p])
    assert run(graph, [p], cache=cache, kernels={p.impl_ref: kernel}).terminal == "ok"
    limit = r.CostVector(**graph.budget.model_dump(exclude={"probe_runs"}))
    rec = run(graph, [p], cache=cache, kernels={p.impl_ref: kernel}, budget=limit)
    assert rec.terminal == "refused" and rec.root_output is None and calls == [1]


def test_unknown_runtime_limit_cannot_admit_skipped_guard():
    r = records()
    p = primitive(budget=complete(wall_s=5, cpu_s=5))
    body = graph_for([p], combinator="guard", k=None).model_dump(exclude={"graph_id"})
    body["nodes"][-1]["guard"] = [dict(feature="assertions", op="gt", value=10)]
    rec = run(r.ProgramGraph(**body), [p], budget=r.CostVector())
    assert rec.terminal == "refused" and rec.root_output is None and rec.attempts == []
    assert rec.cost == complete()


def test_plain_llm_reply_cannot_turn_byte_estimate_into_observed_tokens():
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  budget=complete(llm_calls=1, llm_tokens_in=1000, llm_tokens_out=20, wall_s=5, cpu_s=5))
    rec = run(graph_for([p]), [p], caller=FakeLLM(default="concern"),
              llm_configs={p.primitive_id: config})
    assert rec.cost.llm_tokens_in is None and rec.cost.llm_tokens_out is None
    assert rec.terminal == "refused" and rec.root_output is None and rec.cost.llm_calls == 1


@pytest.mark.parametrize("reply,terminal", [("concern", "ok"), ("invalid json", "fail")])
def test_trusted_llm_adapter_accounting_survives_parsing(reply, terminal):
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  budget=complete(llm_calls=1, llm_tokens_in=1000, llm_tokens_out=20, wall_s=5, cpu_s=5))

    class Adapter(FakeLLM):
        def complete(self, *args, **kwargs):
            text = super().complete(*args, **kwargs)
            return a.KernelResult(output=text, cost=complete(llm_tokens_in=3, llm_tokens_out=2))

    rec = run(graph_for([p]), [p], caller=Adapter(default=reply), llm_configs={p.primitive_id: config})
    assert rec.terminal == terminal
    assert rec.cost.llm_tokens_in == 3 and rec.cost.llm_tokens_out == 2
    assert rec.cost.llm_calls == 1 and rec.attempts[0].cost == rec.cost


def test_error_message_cannot_supply_timeout_type():
    from g6.core.llm import LLMError
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  budget=complete(llm_calls=1, llm_tokens_in=1000, llm_tokens_out=20, wall_s=5, cpu_s=5))

    def fail(*args, **kwargs):
        raise LLMError("TIMEOUT is only message text")

    rec = run(graph_for([p]), [p], caller=FakeLLM(responder=fail), llm_configs={p.primitive_id: config})
    assert rec.terminal == "fail" and rec.cost.llm_calls == 1


def test_measured_time_and_calls_override_callback_declarations(monkeypatch):
    a = api()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    p = primitive(budget=complete(wall_s=5, cpu_s=5))

    def kernel(*args, **kwargs):
        clock[0] += 0.25
        return a.KernelResult(output="concern", cost=complete(llm_calls=99, wall_s=99, cpu_s=99))

    rec = run(graph_for([p]), [p], kernels={p.impl_ref: kernel})
    # Lead ruling 11-23 (graph-unk repair 1 class rule): model sends are counted, never taken from the callback;
    # a reported CPU overrun (child work the parent cannot see) is never replaced by a lower local reading, so the
    # attempt is refused. Wall time is at least the measured elapsed time.
    assert rec.cost.llm_calls == 0 and rec.cost.cpu_s == 99 and rec.cost.wall_s >= 0.25
    assert rec.terminal != "ok" and rec.completion_state == "blocked-escalated"


@pytest.mark.parametrize("resource", ["delta_cost", "validation", "maintenance_per_task"])
def test_savings_cannot_claim_missing_resource_observations(resource):
    r = records()
    body = dict(coverage=r.Interval(point=0.5, lo95=0, hi95=1, method="wilson"), calls_saved_per_task=1,
                delta_cost=complete(), validation=complete(), maintenance_per_task=complete(),
                break_even_tasks=1)
    assert r.SavingsEstimate(**body).calls_saved_per_task == 1
    body[resource] = r.CostVector()
    with pytest.raises(ValidationError):
        r.SavingsEstimate(**body)
