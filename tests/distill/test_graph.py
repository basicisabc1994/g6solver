"""Graph lowering, bounded execution, replay and structural credit."""
import importlib
import importlib.util
from itertools import product

import pytest

from g6.core.ledger import digest, canonical
from g6.core.llm import FakeLLM, EmptyRetry
from g6.distill.execution import Observation, run_candidate
from g6.distill.records import Composition, VerifierCandidate
from tests.distill.test_records import bodies
from tests.distill.test_synthesis import MemoryLibrary
from tests.distill.test_graph_records import D, cost, graph_for, primitive, records


def api():
    assert importlib.util.find_spec("g6.distill.graph"), "graph executor is missing"
    return importlib.import_module("g6.distill.graph")


def lower_api():
    assert importlib.util.find_spec("g6.distill.graph_lowering"), "graph lowering is missing"
    return importlib.import_module("g6.distill.graph_lowering")


def context():
    return dict(unit_ref=digest("case"), split="training", request_id="request", g6_commit="fixture", seed=7)


def run(g, ps, **kwargs):
    return api().execute(g, {"assertions": 1}, primitives={p.primitive_id: p for p in ps},
                         **context(), **kwargs)


class AccountedLLM(FakeLLM):
    """Trusted fixture adapter with explicit non-time resource observations."""

    def complete(self, *args, **kwargs):
        reply = super().complete(*args, **kwargs)
        return api().KernelResult(output=reply, cost=cost(llm_tokens_in=1, llm_tokens_out=1))


def timed_kernel(monkeypatch, *, wall_s=0, cpu_s=0):
    a = api()
    clock = [10.0, 10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[1])

    def kernel(*args, **kwargs):
        clock[0] += wall_s
        clock[1] += cpu_s
        return a.KernelResult(output="no_concern", cost=cost())

    return kernel


@pytest.mark.parametrize("op,k", [("any", None), ("all", None), ("k_of_n", 1), ("sequence", None)])
def test_lowering_keeps_composition_bytes_and_refuses_unmetered_execution(op, k):
    r = records()
    lib = MemoryLibrary(["assert_strength.v1", "skip_gate.v1"])
    ps = [primitive(kind="template", impl_ref=e.entry_id, entry_id=e.entry_id,
                    contract_sha256=e.contract_sha256) for e in lib.values]
    threshold = 1 if op == "any" else 2 if op == "all" else k
    g = graph_for(ps, combinator="cascade" if op == "sequence" else "vote", k=threshold,
                  lowering="composition")
    comp = lower_api().lower(g, {p.primitive_id: p for p in ps}, entries={e.entry_id: e for e in lib.values})
    expected = Composition(op=op, entries=[e.entry_id for e in lib.values], k=k)
    # A threshold of one lowers to any rather than the equivalent k_of_n spelling.
    if op == "k_of_n":
        expected = Composition(op="any", entries=expected.entries)
    assert canonical(comp.model_dump()) == canonical(expected.model_dump())
    candidate = VerifierCandidate(**(bodies()[3][1] | {"form": "composition", "body": comp}))
    baseline = VerifierCandidate(**(bodies()[3][1] | {"form": "composition", "body": expected}))
    before = {"tests/test_a.py": "def test_a():\n    assert calc() == 4\n"}
    afters = ["def test_a():\n    assert True\n", "@pytest.mark.skip\ndef test_a():\n    assert calc() == 4\n",
              "def test_a():\n    assert calc() == 4\n"]
    for after in afters:
        obs = Observation(before, {"tests/test_a.py": after}, {"assertions": 1})
        expected_verdict = run_candidate(baseline, [obs], lib.methods.__getitem__)[0]
        assert run_candidate(candidate, [obs], lib.methods.__getitem__)[0] == expected_verdict
        rec = api().execute(g, obs, primitives={p.primitive_id: p for p in ps},
                            resolve=lib.methods.__getitem__, entries={e.entry_id: e for e in lib.values}, **context())
        assert rec.root_output is None and rec.terminal == "refused"
        assert rec.cost.probe_runs is None
        assert rec.attempts
        assert r.ExecutionRecord.model_validate_json(rec.model_dump_json()) == rec


def test_budget_refuses_before_caller_and_never_returns_no_concern():
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    caller = AccountedLLM(default='"no_concern"')
    rec = run(graph_for([p]), [p], caller=caller, llm_configs={p.primitive_id: config}, budget=cost())
    assert rec.terminal == "refused" and rec.root_output is None
    assert caller.calls == []
    assert all(n.terminal == "refused" for n in rec.runs)


@pytest.mark.parametrize("status", ["fail", "timeout", "abstain", "refused"])
def test_terminal_status_is_explicit_in_leaf_and_root_and_record(status):
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(),
        output="abstain" if status == "abstain" else None, terminal=status)})
    assert rec.terminal == status
    assert all(n.terminal == status for n in rec.runs)
    assert rec.root_output != "no_concern"
    assert rec.completion_state != "qualified-draft" or status == "abstain"


@pytest.mark.parametrize("output", [None, "no_concern"])
def test_abstention_status_cannot_turn_into_silence_at_parent(output):
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(),
        output=output, terminal="abstain")})
    assert rec.terminal == "abstain" and rec.root_output == "abstain"
    assert all(r.output == "abstain" for r in rec.runs)


@pytest.mark.parametrize("error,status", [(TimeoutError("late"), "timeout"), (ValueError("bad"), "fail")])
def test_kernel_exceptions_become_records(error, status):
    def bad(*args, **kwargs):
        raise error
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: bad})
    assert rec.terminal == status and rec.runs[0].terminal == status


def test_actual_overspend_refuses_and_is_not_cached(monkeypatch):
    a = api()
    p = primitive()
    cache = a.ReplayCache()
    rec = run(graph_for([p]), [p], cache=cache,
              kernels={p.impl_ref: timed_kernel(monkeypatch, cpu_s=6)})
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.cost.cpu_s >= 6 and len(cache) == 0


def test_replay_edit_reexecutes_only_changed_node_and_descendants():
    a, r = api(), records()
    ps = [primitive(impl_ref=name) for name in ("a", "b", "c")]
    g = graph_for(ps, combinator="seq", k=None, output_type="value")
    body = g.model_dump(exclude={"graph_id"})
    body["edges"] = [dict(src="n0", src_port="out", dst="n1", dst_port="in", type_sha256=D),
                     dict(src="n1", src_port="out", dst="root", dst_port="0", type_sha256=D),
                     dict(src="n2", src_port="out", dst="root", dst_port="1", type_sha256=D)]
    g = r.ProgramGraph(**body)
    calls = []
    def kernel(p, inputs, **kwargs):
        calls.append(p.impl_ref)
        return a.KernelResult(cost=cost(), output=p.impl_version)
    kernels = {p.impl_ref: kernel for p in ps}
    cache = a.ReplayCache()
    run(g, ps, kernels=kernels, cache=cache)
    assert sorted(calls) == ["a", "b", "c"]
    calls.clear()
    replay = run(g, ps, kernels=kernels, cache=cache)
    assert calls == [] and replay.cost == cost()
    changed = primitive(impl_ref="a", impl_version=digest("v2"))
    body["nodes"][0]["primitive_id"] = changed.primitive_id
    calls.clear()
    run(r.ProgramGraph(**body), [changed, *ps[1:]], kernels=kernels, cache=cache)
    assert calls == ["a", "b"]


def test_llm_recorded_output_replays_only_identical_input_model_settings_and_seed():
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    g, cache = graph_for([p]), a.ReplayCache()
    caller = AccountedLLM(default='"concern"')
    kw = dict(caller=caller, cache=cache, llm_configs={p.primitive_id: config})
    first = run(g, [p], **kw)
    second = run(g, [p], **kw)
    assert len(caller.calls) == 1 and first.cost.llm_calls == 1 and second.cost.llm_calls == 0
    api().execute(g, {"assertions": 2}, primitives={p.primitive_id: p}, **context(), **kw)
    api().execute(g, {"assertions": 1}, primitives={p.primitive_id: p},
                  **(context() | {"seed": 8}), **kw)
    assert len(caller.calls) == 3
    altered = a.LLMConfig(model_id="fixture-v2", system="judge", max_tokens=20)
    refused = run(g, [p], caller=caller, cache=cache, llm_configs={p.primitive_id: altered})
    assert refused.terminal == "refused" and len(caller.calls) == 3


def test_llm_retry_cannot_send_past_call_reservation():
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    caller = FakeLLM(default="")
    rec = run(graph_for([p]), [p], caller=EmptyRetry(caller), llm_configs={p.primitive_id: config})
    assert len(caller.calls) == 1 and rec.cost.llm_calls == 1 and rec.terminal == "refused"


@pytest.mark.parametrize("reply", ["concern", '{"verdict":"concern","reason":"fixture"}'])
def test_llm_verdict_protocol_retains_reason_without_losing_verdict(reply):
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=200)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=200, wall_s=5, cpu_s=5))
    rec = run(graph_for([p]), [p], caller=AccountedLLM(default=reply), llm_configs={p.primitive_id: config})
    assert rec.terminal == "ok" and rec.root_output == "concern"


def test_cascade_and_guard_skip_unused_calls():
    a, r = api(), records()
    ps = [primitive(impl_ref="first"), primitive(impl_ref="unused")]
    calls = []
    def kernel(p, *args, **kwargs):
        calls.append(p.impl_ref)
        return a.KernelResult(cost=cost(), output="concern")
    g = graph_for(ps, combinator="cascade", k=None)
    assert run(g, ps, kernels={p.impl_ref: kernel for p in ps}).root_output == "concern"
    assert calls == ["first"]
    body = graph_for([ps[0]], combinator="guard", k=None).model_dump(exclude={"graph_id"})
    body["nodes"][-1]["guard"] = [dict(feature="assertions", op="gt", value=10)]
    assert run(r.ProgramGraph(**body), [ps[0]]).terminal == "abstain"


def test_typed_edges_and_effects_are_checked_before_any_kernel():
    p = primitive(output_schema=digest("other"))
    rec = run(graph_for([p]), [p])
    assert rec.terminal == "refused"
    p = primitive(side_effect="external")
    assert run(graph_for([p]), [p]).terminal == "refused"


def test_primitive_budget_remains_binding_inside_larger_node_reservation(monkeypatch):
    r = records()
    p = primitive()
    body = graph_for([p]).model_dump(exclude={"graph_id"})
    body["nodes"][0]["budget"]["cpu_s"] = 10
    body["budget"]["cpu_s"] = 10
    rec = run(r.ProgramGraph(**body), [p], kernels={p.impl_ref: timed_kernel(monkeypatch, cpu_s=6)})
    assert rec.terminal == "refused" and rec.root_output is None


def test_combinator_reservation_is_checked_at_runtime():
    a, r = api(), records()
    p = primitive()
    body = graph_for([p]).model_dump(exclude={"graph_id"})
    body["nodes"][-1]["budget"]["solver_calls"] = 1
    body["budget"]["solver_calls"] = 1
    rec = run(r.ProgramGraph(**body), [p], budget=p.budget,
              kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output="no_concern")})
    assert rec.terminal == "refused" and rec.root_output is None


@pytest.mark.parametrize("change", [{"terminal": "unknown"}, {"output": {1, 2}}, {"cost": {"cpu_s": 1}}])
def test_malformed_kernel_result_is_recorded_as_failure(change):
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw:
              a.KernelResult(**({"cost": cost()} | change))})
    assert rec.terminal == "fail" and rec.root_output is None


def test_wall_budget_overrun_is_refused_even_if_stop_timeout_also_elapsed(monkeypatch):

    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: timed_kernel(monkeypatch, wall_s=6)})
    assert rec.terminal == "refused" and rec.root_output is None


def test_stop_timeout_below_budget_is_explicit_timeout(monkeypatch):
    r = records()
    p = primitive(stop=r.StopRule(timeout_s=1))
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: timed_kernel(monkeypatch, wall_s=2)})
    assert rec.terminal == "timeout" and rec.root_output is None


def test_composition_lowering_refuses_extra_primitive_scope():
    lib = MemoryLibrary(["assert_strength.v1"])
    e = lib.values[0]
    p = primitive(kind="template", impl_ref=e.entry_id, entry_id=e.entry_id,
                  contract_sha256=e.contract_sha256,
                  scope=[dict(feature="assertions", op="gt", value=20)])
    with pytest.raises(ValueError):
        lower_api().lower(graph_for([p], lowering="composition"), {p.primitive_id: p}, entries={e.entry_id: e})


def test_composition_rejects_resolver_returning_a_different_candidate():
    lib = MemoryLibrary(["assert_strength.v1", "skip_gate.v1"])
    e = lib.values[0]
    p = primitive(kind="template", impl_ref=e.entry_id, entry_id=e.entry_id,
                  contract_sha256=e.contract_sha256)
    obs = Observation({"test_a.py": "def test_a():\n    assert False\n"},
                      {"test_a.py": "def test_a():\n    assert True\n"}, {})
    rec = api().execute(graph_for([p], lowering="composition"), obs, primitives={p.primitive_id: p},
                        entries={e.entry_id: e}, resolve=lambda _: lib.methods[lib.values[1].entry_id], **context())
    assert rec.terminal == "refused"


@pytest.mark.parametrize("combinator,k", [("vote", 1), ("vote", 2), ("cascade", None)])
def test_structural_credit_matches_all_single_leaf_substitutions(combinator, k):
    a = api()
    g = graph_for([primitive(impl_ref="a"), primitive(impl_ref="b")], combinator=combinator, k=k)
    verdicts = ("concern", "abstain", "no_concern")
    def combine(xs):
        if combinator == "cascade":
            return next((v for v in xs if v != "abstain"), "abstain")
        c, ab = xs.count("concern"), xs.count("abstain")
        return "concern" if c >= k else "abstain" if c+ab >= k else "no_concern"
    for xs in product(verdicts, repeat=2):
        expected = [f"n{i}" for i in range(2) if any(
            combine(list(xs[:i]) + [v] + list(xs[i+1:])) != combine(list(xs)) for v in verdicts)]
        assert a.structural_blame(g, dict(zip(("n0", "n1"), xs))) == expected


def test_structural_credit_evaluates_combinators_once_for_all_interventions(monkeypatch):
    a = api()
    g = graph_for([primitive(impl_ref=str(i)) for i in range(20)], k=10)
    calls = []
    original = a._combine
    def counted(node, values):
        calls.append(node.node_id)
        return original(node, values)
    monkeypatch.setattr(a, "_combine", counted)
    if hasattr(a, "_combine_masks"):
        masks = a._combine_masks
        def counted_masks(node, values, all_bits):
            calls.append(node.node_id)
            return masks(node, values, all_bits)
        monkeypatch.setattr(a, "_combine_masks", counted_masks)
    result = a.structural_blame(g, {f"n{i}": "concern" if i < 10 else "no_concern" for i in range(20)})
    assert result == [f"n{i}" for i in range(10)]
    assert len(calls) <= len(g.nodes)


def test_execution_store_roundtrip_and_held_out_refusal(tmp_path):
    a, r = api(), records()
    from g6.distill.graph_store import ExecutionStore
    p = primitive()
    store = ExecutionStore(tmp_path / "exec")
    rec = run(graph_for([p]), [p], store=store, kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(),
        output="concern")})
    assert store.get(rec.run_id) == rec
    assert ExecutionStore(tmp_path / "exec").list() == [rec]
    with pytest.raises(ValueError, match="split"):
        a.execute(graph_for([p]), {}, primitives={p.primitive_id: p}, **(context() | {"split": "held_out"}))
    with pytest.raises(ValueError):
        r.ExecutionRecord.model_validate(rec.model_dump(exclude={"run_id"}) | {"split": "held_out"})


def test_trace_reference_names_a_retained_trace():
    a = api()
    class Store:
        def __init__(self):
            self.traces = {}
        def put_trace(self, trace):
            self.traces[digest(trace)] = trace
            return digest(trace)
        def put(self, record):
            self.record = record
    p, store = primitive(), Store()
    rec = run(graph_for([p]), [p], store=store, kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(),
        output="concern", trace={"reason": "fixture evidence"})})
    assert store.traces[rec.runs[0].trace_ref] == {"reason": "fixture evidence"}
    assert store.record == rec


def test_unretained_trace_does_not_create_a_dangling_reference():
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(),
        output="concern", trace={"reason": "fixture evidence"})})
    assert rec.runs[0].trace_ref is None


def test_procedure_target_is_explicit_stub():
    p = primitive()
    g = records().ProgramGraph(**(graph_for([p], combinator=None).model_dump(exclude={"graph_id"}) |
                                  {"lowering": "procedure"}))
    with pytest.raises(NotImplementedError, match="ProcedureSpec"):
        lower_api().lower(g, {p.primitive_id: p})


@pytest.mark.parametrize("target", ["method_plan", "compose", "procedure"])
def test_broad_lowering_is_deferred_in_v31(target):
    p = primitive()
    g = records().ProgramGraph(**(graph_for([p], combinator=None).model_dump(exclude={"graph_id"}) |
                                  {"lowering": target}))
    with pytest.raises(NotImplementedError, match="deferred"):
        lower_api().lower(g, {p.primitive_id: p})


def test_native_execution_is_not_a_lowering_target():
    p = primitive()
    with pytest.raises(lower_api().LoweringRefused, match="Composition"):
        lower_api().lower(graph_for([p]), {p.primitive_id: p})


def test_held_out_split_refused_before_input_access():
    class Unreadable(dict):
        def items(self):
            raise AssertionError("held-out input read")
    p = primitive()
    with pytest.raises(api().ExecutionRefused, match="split.*held_out") as exc:
        api().execute(graph_for([p]), Unreadable(), primitives={p.primitive_id: p},
                      **(context() | {"split": "held_out"}))
    assert exc.value.code == "G6_E_GRAPH_SPLIT"


def test_structural_blame_requires_complete_recorded_leaves():
    g = graph_for([primitive(), primitive(impl_ref="b")])
    with pytest.raises(ValueError, match="complete"):
        api().structural_blame(g, {"n0": "concern"})


@pytest.mark.parametrize("verdict,sends", [("concern", 0), ("no_concern", 0), ("abstain", 1)])
def test_scoped_cascade_sends_only_on_abstention(verdict, sends):
    from g6.core.model_calls import counting
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    check = primitive(scope=[dict(feature="assertions", op="gt", value=0)])
    llm = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                    determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                    llm_tokens_out=20, wall_s=5, cpu_s=5))
    caller = AccountedLLM(default="concern")
    with counting() as counter:
        rec = run(graph_for([check, llm], combinator="cascade", k=None), [check, llm], caller=caller,
                  llm_configs={llm.primitive_id: config},
                  kernels={check.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output=verdict)})
    assert rec.root_output == ("concern" if verdict == "abstain" else verdict)
    assert rec.cost.llm_calls == counter() == len(caller.calls) == sends


@pytest.mark.parametrize("verdict,k", [("concern", 1), ("no_concern", 2)])
def test_vote_skips_model_once_verdict_is_fixed(verdict, k):
    from g6.core.model_calls import counting
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    check = primitive()
    llm = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                    determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                    llm_tokens_out=20, wall_s=5, cpu_s=5))
    caller = AccountedLLM(default="abstain")
    with counting() as counter:
        rec = run(graph_for([check, llm], k=k), [check, llm], caller=caller,
                  llm_configs={llm.primitive_id: config},
                  kernels={check.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output=verdict)})
    assert rec.root_output == verdict
    assert rec.cost.llm_calls == counter() == len(caller.calls) == 0
    assert [row.node_id for row in rec.runs] == ["n0", "root"]


def test_vote_skips_model_once_abstention_is_fixed():
    from g6.core.model_calls import counting
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    checks = [primitive(impl_ref=f"check{i}") for i in range(2)]
    llm = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                    determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                    llm_tokens_out=20, wall_s=5, cpu_s=5))
    caller = AccountedLLM(default="concern")
    with counting() as counter:
        rec = run(graph_for([*checks, llm], k=2), [*checks, llm], caller=caller,
                  llm_configs={llm.primitive_id: config},
                  kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output="abstain") for p in checks})
    assert rec.terminal == rec.root_output == "abstain"
    assert rec.cost.llm_calls == counter() == len(caller.calls) == 0
    assert [row.node_id for row in rec.runs] == ["n0", "n1", "root"]


@pytest.mark.parametrize("in_scope,sends", [(False, 0), (True, 1)])
def test_guard_checks_scope_before_model_send(in_scope, sends):
    from g6.core.model_calls import counting
    a, r = api(), records()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    body = graph_for([p], combinator="guard", k=None).model_dump(exclude={"graph_id"})
    body["nodes"][-1]["guard"] = [dict(feature="assertions", op="gt", value=0 if in_scope else 10)]
    caller = AccountedLLM(default="concern")
    with counting() as counter:
        rec = run(r.ProgramGraph(**body), [p], caller=caller, llm_configs={p.primitive_id: config})
    assert rec.root_output == ("concern" if in_scope else "abstain")
    assert rec.cost.llm_calls == counter() == len(caller.calls) == sends


def test_unnoted_caller_output_is_refused_without_inventing_send():
    from g6.core.model_calls import counting
    a = api()
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    class Unnoted:
        def complete(self, *args, **kwargs):
            return "concern"
    with counting() as counter:
        rec = run(graph_for([p]), [p], caller=Unnoted(), llm_configs={p.primitive_id: config})
    assert rec.terminal == "refused" and rec.root_output is None
    assert rec.cost.llm_calls == counter() == 0


@pytest.mark.parametrize("sent", [False, True])
def test_exception_books_measured_time_and_actual_sends(monkeypatch, sent):
    from g6.core.model_calls import counting
    a = api()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    config = a.LLMConfig(model_id="fixture", system="judge", max_tokens=20)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=cost(llm_calls=1, llm_tokens_in=1000,
                  llm_tokens_out=20, wall_s=5, cpu_s=5))
    def fail(*args, **kwargs):
        clock[0] += 0.25
        raise ValueError("fixture failure")
    caller = FakeLLM(responder=fail) if sent else type("BeforeSendFailure", (), {"complete": staticmethod(fail)})()
    with counting() as counter:
        rec = run(graph_for([p]), [p], caller=caller, llm_configs={p.primitive_id: config})
    assert rec.terminal == "fail" and rec.root_output is None
    assert rec.cost.wall_s == rec.cost.cpu_s == 0.25
    assert rec.cost.llm_calls == counter() == int(sent)
    assert rec.attempts[0].cost == rec.cost
    assert rec.attempts[0].outcome == "failed"


def test_kernel_failure_keeps_send_count_and_time(monkeypatch):
    from g6.core.model_calls import counting
    a = api()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    p = primitive(budget=cost(llm_calls=1, wall_s=5, cpu_s=5))
    caller = AccountedLLM(default="concern")
    def kernel(*args, **kw):
        caller.complete("fixture", "fixture")
        clock[0] += 0.5
        raise ValueError("fixture failure after send")
    with counting() as counter:
        rec = run(graph_for([p]), [p], kernels={p.impl_ref: kernel})
    assert rec.terminal == "fail"
    assert rec.cost.llm_calls == counter() == 1
    assert rec.cost.wall_s == rec.cost.cpu_s == 0.5


def test_attempt_meter_retains_exception_record(monkeypatch):
    from g6.core.model_calls import counting
    a, r = api(), records()
    clock = [10.0]
    monkeypatch.setattr(a.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(a.time, "process_time", lambda: clock[0])
    caller = AccountedLLM(default="concern")
    with counting() as sends:
        with pytest.raises(ValueError, match="fixture failure"):
            with a.attempt_meter("fixture") as meter:
                caller.complete("fixture", "fixture")
                clock[0] += 0.25
                raise ValueError("fixture failure")
    row = meter.record
    assert row.what == "fixture" and row.outcome == "failed"
    assert row.model_sends == row.cost.llm_calls == sends() == 1
    assert row.cost.wall_s == row.cost.cpu_s == 0.25
    assert r.AttemptRecord.model_validate_json(row.model_dump_json()) == row


def test_execution_retains_metered_attempts():
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output="concern")})
    assert len(rec.attempts) == 1
    assert rec.attempts[0].what == "n0"
    assert rec.attempts[0].cost == rec.cost == rec.runs[0].cost
    assert rec.attempts[0].outcome == "screened"


def test_invalid_verdict_marks_attempt_failed():
    a = api()
    p = primitive()
    rec = run(graph_for([p]), [p], kernels={p.impl_ref: lambda *args, **kw: a.KernelResult(cost=cost(), output="unknown")})
    assert rec.terminal == "fail" and rec.root_output is None
    assert rec.attempts[0].outcome == "failed"


@pytest.mark.skip(reason="merge: ProcedureSpec target is absent on this branch")
def test_procedure_lowering_after_merge():
    raise AssertionError("bind ProcedureSpec adapter to the merged recursion/ports.py type")
